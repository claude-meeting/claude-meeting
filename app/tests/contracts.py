"""Contract tests: what the code and its fakes assume about the real tools, checked against the real tools.

The fakes in tests/fakes/bin were written from what Minutes and launchd were expected to do, and the code was written
from the same expectations: when one is wrong, the code and its tests are wrong together and every test still passes
(claude-meeting/claude-meeting#1 and #4). These run the real Minutes CLI and the real launchd of this Mac, read only
(Minutes) or on a throwaway job of their own (launchd), and fail when the real answer no longer has the shape the
fakes give.

    cd app && asr-venv/bin/python -m unittest tests.contracts -v       (make test-contracts; also make test-live)
Needs Minutes installed (/Applications/Minutes.app or $MINUTES_APP). Nothing is recorded or changed in Minutes.
"""
import json
import os
import plistlib
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
APP = HERE.parent
REAL = Path(os.environ.get("MINUTES_APP", "/Applications/Minutes.app")) / "Contents" / "MacOS" / "minutes"
FAKE = HERE / "fakes" / "bin" / "minutes"


def engine_done_states():
    """MinutesJobs.DONE as written in bin/autopilot.py (importing it would start the engine's setup)."""
    import ast
    tree = ast.parse((APP / "bin" / "autopilot.py").read_text())
    for node in ast.walk(tree):
        if isinstance(node, ast.ClassDef) and node.name == "MinutesJobs":
            for stmt in node.body:
                if isinstance(stmt, ast.Assign) and any(getattr(t, "id", "") == "DONE" for t in stmt.targets):
                    return ast.literal_eval(stmt.value)
    raise AssertionError("MinutesJobs.DONE not found")


def real(*args, timeout=60):
    return subprocess.run([str(REAL), *args], capture_output=True, text=True, timeout=timeout)


def fake(*args, state=None):
    with tempfile.TemporaryDirectory() as d:
        if state is not None:
            Path(d, "minutes.json").write_text(json.dumps(state))
        return subprocess.run([sys.executable, str(FAKE), *args], capture_output=True, text=True,
                              env={**os.environ, "MC_FAKE_DIR": d, "HOME": d})


@unittest.skipUnless(REAL.exists(), f"Minutes is not installed ({REAL})")
class MinutesCLI(unittest.TestCase):
    def test_status_has_the_keys_the_engine_reads(self):
        r = real("status")
        self.assertEqual(r.returncode, 0, r.stderr)
        got = json.loads(r.stdout)
        for key in ("recording", "processing"):                     # autopilot's st.get(...)
            self.assertIn(key, got)
            self.assertIsInstance(got[key], bool)
        self.assertLessEqual({"recording", "processing"}, set(json.loads(fake("status").stdout)))

    def test_jobs_are_a_list_with_the_fields_and_states_minutesjobs_reads(self):
        r = real("jobs", "--all", "--json", "--limit", "20")
        self.assertEqual(r.returncode, 0, r.stderr)
        jobs = json.loads(r.stdout)
        self.assertIsInstance(jobs, list)
        self.assertTrue(all(isinstance(j, dict) for j in jobs))

    def test_setup_takes_the_flags_install_sh_and_the_engine_pass(self):
        r = real("setup", "--help")
        text = r.stdout + r.stderr
        for flag in ("--model", "--download-only", "--vad", "--diarization"):
            self.assertIn(flag, text)

    def test_health_lists_the_items_install_sh_checks(self):
        r = real("health", "--json")
        self.assertEqual(r.returncode, 0, r.stderr)
        items = json.loads(r.stdout)["data"]["items"]
        labels = {i.get("label") for i in items}
        for label in ("Speech model", "VAD model"):                  # install.sh's wanted = {...}
            self.assertIn(label, labels)
        self.assertTrue(all("state" in i for i in items))


@unittest.skipUnless(REAL.exists(), f"Minutes is not installed ({REAL})")
class MinutesJobStates(unittest.TestCase):
    """MinutesJobs waits while a job is under way and reports it once 'failed': every state Minutes ends a job in must
    be in MinutesJobs.DONE, or such a job is waited on for ever (and no meeting record is ever reported missing)."""

    UNDER_WAY = {"queued", "pending", "running", "processing"}

    def test_every_state_seen_is_under_way_or_one_the_engine_ends_on(self):
        jobs = json.loads(real("jobs", "--all", "--json", "--limit", "50").stdout)
        if not jobs:
            self.skipTest("Minutes has no jobs yet")
        done = set(engine_done_states())
        self.assertLessEqual({"complete", "failed"}, done)
        for job in jobs:
            self.assertIn(job.get("state"), self.UNDER_WAY | done, job.get("state"))
            for key in ("id", "mode", "state", "created_at"):
                self.assertIn(key, job)
            if job.get("state") == "failed":
                self.assertIsInstance(job.get("error"), str)


class Launchd(unittest.TestCase):
    """What app/tools/launchd-start.sh and ScreenCap assume about launchd, on a throwaway job of their own."""

    LABEL = "com.meeting-copilot.contract-test"

    def setUp(self):
        self.domain = f"gui/{os.getuid()}"
        self.dir = tempfile.TemporaryDirectory()
        self.plist = Path(self.dir.name) / f"{self.LABEL}.plist"
        with open(self.plist, "wb") as f:
            plistlib.dump({"Label": self.LABEL, "ProgramArguments": ["/bin/sleep", "120"], "RunAtLoad": True}, f)

    def tearDown(self):
        subprocess.run(["launchctl", "bootout", f"{self.domain}/{self.LABEL}"], capture_output=True)
        self.dir.cleanup()

    def launchctl(self, *args):
        return subprocess.run(["launchctl", *args], capture_output=True, text=True)

    def state(self):
        """The job's `state = ...` line only: `launchctl print` also lists the user's launchd environment, which
        can hold API keys, and must not end up in a test log."""
        r = self.launchctl("print", f"{self.domain}/{self.LABEL}")
        return next((l.strip() for l in r.stdout.splitlines() if l.strip().startswith("state = ")), None) \
            if r.returncode == 0 else None

    def wait_running(self, secs=10):
        end = time.time() + secs
        while time.time() < end:
            if self.state() == "state = running":
                return True
            time.sleep(0.2)
        return False

    def test_print_fails_for_a_job_that_is_not_loaded(self):
        self.launchctl("bootout", f"{self.domain}/{self.LABEL}")
        time.sleep(1)
        self.assertIsNone(self.state())

    def test_a_second_bootstrap_of_a_loaded_job_fails_while_it_runs(self):
        # The case of claude-meeting/claude-meeting#4: an error from bootstrap does not mean the job is not running.
        self.assertEqual(self.launchctl("bootstrap", self.domain, str(self.plist)).returncode, 0)
        # Just loaded, it is not "running" yet: launchd-start.sh counts "loaded", not "running".
        self.assertIn(self.state(), ("state = xpcproxy", "state = spawn scheduled", "state = running"))
        again = self.launchctl("bootstrap", self.domain, str(self.plist))
        self.assertNotEqual(again.returncode, 0)
        self.assertTrue(self.wait_running(), self.state())

    def test_launchd_start_restarts_a_running_job_twice_in_a_row(self):
        start = APP / "tools" / "launchd-start.sh"
        for _ in range(2):
            r = subprocess.run(["bash", str(start), self.LABEL, str(self.plist)], capture_output=True, text=True,
                               timeout=120)
            self.assertEqual(r.returncode, 0, r.stderr)
            self.assertTrue(self.wait_running(), self.state())


if __name__ == "__main__":
    unittest.main()
