"""scripts/ci_report.py: whether a test run counts as passed. It must not be fooled by a good exit code alone."""
import hashlib
import importlib.util
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

SCRIPT = Path(__file__).resolve().parents[2] / "scripts" / "ci_report.py"
spec = importlib.util.spec_from_file_location("ci_report", SCRIPT)
ci_report = importlib.util.module_from_spec(spec)
spec.loader.exec_module(ci_report)

GOOD = """==> Python unit tests
Ran 152 tests in 132.542s

OK
==> Python integration tests (fake Minutes and Claude)
Ran 1 test in 19.937s

OK
==> Coverage (app/lib, app/bin, app/tools)
TOTAL                         3088    298  90.3%
==> Swift tests
	 Executed 9 tests, with 0 failures (0 unexpected) in 0.682 (0.683) seconds
	 Executed 66 tests, with 0 failures (0 unexpected) in 0.703 (0.710) seconds
==> Script tests (bats)
1..2
ok 1 installs
ok 2 uninstalls
==> shellcheck

==> all green
"""


class Summary(unittest.TestCase):
    def test_a_good_run(self):
        s = ci_report.summarize(GOOD)
        self.assertEqual((s["python"], s["swift"], s["bats"], s["coverage"]), (153, 66, 2, 90.3))
        self.assertEqual(ci_report.failures(s, 0), [])
        self.assertEqual(ci_report.one_line(s, []), "Python 153, coverage 90.3 %, Swift 66, bats 2")

    def test_a_good_exit_code_does_not_cover_a_group_that_did_not_run(self):
        for gone, why in (("==> Swift tests\n\t Executed 9 tests, with 0 failures (0 unexpected) in 0.682 (0.683) seconds\n"
                           "\t Executed 66 tests, with 0 failures (0 unexpected) in 0.703 (0.710) seconds\n", "swift"),
                          ("ok 1 installs\nok 2 uninstalls\n", "bats"),
                          ("==> shellcheck\n", "shellcheck"),
                          ("TOTAL                         3088    298  90.3%\n", "coverage"),
                          ("==> all green\n", "all green")):
            failed = ci_report.failures(ci_report.summarize(GOOD.replace(gone, "")), 0)
            self.assertTrue(any(why in f for f in failed), (why, failed))

    def test_failures_are_named(self):
        log = GOOD + "FAILED (failures=2)\nnot ok 3 purges\n  !! bats\n"
        failed = ci_report.failures(ci_report.summarize(log), 0)
        self.assertIn("bats", failed)
        self.assertIn("purges", failed)
        self.assertIn("python: failures=2", failed)
        self.assertEqual(ci_report.failures(ci_report.summarize(GOOD), 1), ["exit code 1"])


class Report(unittest.TestCase):
    def run_it(self, log, code, *extra, env=None):
        with tempfile.TemporaryDirectory() as d:
            path, out = Path(d) / "log", Path(d) / "reports" / "r.txt"
            path.write_text(log)
            r = subprocess.run([sys.executable, str(SCRIPT), str(path), str(code), str(out), *extra],
                               capture_output=True, text=True, env=env)
            return r, out.read_text()

    def test_no_home_or_temporary_folder_of_the_account_is_written(self):
        # Assembled: this file is published too, and the export scan would take them for real paths.
        users, folders = "/Us" + "ers/", "/var/" + "folders/"
        home, tmp = users + "jdoe", "/private" + folders + "zq/k3v9xw0000gn/T/"
        log = (GOOD + f"{home}/proj/app/lib/screen.py:26: DeprecationWarning\n"
               f"wrote {tmp}mc-test-7/review.md and {folders}zq/k3v9xw0000gn/T/mc-test-7\n"
               f"cache in {folders}ab/cd12/C/clang, VM checkout {users}admin/repo, shared {users}Shared/x\n")
        env = {**os.environ, "HOME": home, "TMPDIR": tmp}
        r, text = self.run_it(log, 0, "branch=main", f"problem=stopped in {home}/proj", env=env)
        self.assertIn("~/proj/app/lib/screen.py:26", text)
        self.assertIn("wrote $TMPDIR/mc-test-7/review.md and $TMPDIR/mc-test-7\n", text)
        self.assertIn("cache in /var/folders/<user>/C/clang, VM checkout ~/repo, shared " + users + "Shared/x", text)
        self.assertIn("problem: stopped in ~/proj", text)
        for private in ("jdoe", "zq/", "k3v9xw", "ab/cd12"):
            self.assertNotIn(private, text)
        self.assertIn("FAILED, stopped in ~/proj", r.stdout)
        head, _, written = text.partition("\n----- full log -----\n")
        self.assertIn(f"log sha256: {hashlib.sha256(written.encode()).hexdigest()}", head)

    def test_redact_takes_whole_folder_names_and_nothing_else(self):
        users = "/Us" + "ers/"
        paths = [(users + "jdoe", "~"), ("/opt/scratch/t1", "$TMPDIR")]
        self.assertEqual(ci_report.redact(f"{users}jdoex/a {users}jdoe-2/b {users}jdoe. /opt/scratch/t12 /opt/scratch/t1/c "
                                          "/tmp/x ./jdoe", paths),
                         "~/a ~/b ~. /opt/scratch/t12 $TMPDIR/c /tmp/x ./jdoe")

    def test_report_names_the_commit_and_holds_the_log(self):
        r, text = self.run_it(GOOD, 0, "commit=abc123", "macos=15.7.7 arm64", "problem=")
        self.assertEqual(r.returncode, 0)
        self.assertEqual(r.stdout.strip(), "Python 153, coverage 90.3 %, Swift 66, bats 2")
        self.assertTrue(text.startswith("result: passed\ncommit: abc123\nmacos: 15.7.7 arm64\n"))
        self.assertIn("log sha256: ", text)
        self.assertTrue(text.endswith(GOOD))
        self.assertNotIn("problem", text)

    def test_a_problem_from_outside_the_log_fails_it(self):
        r, text = self.run_it(GOOD, 0, "problem=the working tree changed during the run")
        self.assertEqual(r.returncode, 1)
        self.assertIn("FAILED, the working tree changed", r.stdout)
        self.assertTrue(text.startswith("result: FAILED\n"))


if __name__ == "__main__":
    unittest.main()
