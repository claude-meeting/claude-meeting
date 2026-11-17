#!/usr/bin/env python3
"""Evidence of a test run: what scripts/test.sh printed, turned into a report tied to one commit (scripts/ci.sh).

  ci_report.py <log> <exit code> <report to write> [key=value ...]     the key=values head the report

Prints the one-line summary and exits 0 only if the run passed. A pass is decided from the log and the exit code
together: a group that did not run is a failure, whatever the exit code says.

The report may be published, so it names no account: the home folder is written as ~ and the per-user temporary
folder as $TMPDIR, in the log and in the head alike. Its log sha256 is that of the log as written."""
import hashlib
import os
import re
import sys
import tempfile
from pathlib import Path

# macOS gives each account temporary folders under /var/folders/<xx>/<id>/ (T: $TMPDIR, C: caches).
USER_TMP = re.compile(r"(?:/private)?/var/folders/[\w-]+/[\w-]+(/T(?![\w-]))?")
HOMES = re.compile(r"/Users/(?!Shared(?![\w-]))[\w.-]+")


def private_paths():
    """(path, stand-in) for this account's own folders, longest first: an exact match goes before the patterns."""
    pairs = []
    for tmp in {os.environ.get("TMPDIR", ""), tempfile.gettempdir()}:
        tmp = tmp.rstrip("/")
        if tmp and tmp not in ("/tmp", "/private/tmp", "/var/tmp", "/private/var/tmp"):
            pairs.append((tmp, "$TMPDIR"))
    home = os.path.expanduser("~").rstrip("/")
    if home:
        pairs.append((home, "~"))
    out = set()
    for path, stand_in in pairs:
        for p in (path, os.path.realpath(path)):
            out.add((p, stand_in))
            if p.startswith("/private/"):
                out.add((p[len("/private"):], stand_in))
    return sorted(out, key=lambda pair: -len(pair[0]))


def redact(text, paths=None):
    """The text without this account's home or temporary folders, nor anyone's under /Users or /var/folders."""
    for path, stand_in in private_paths() if paths is None else paths:
        text = re.sub(re.escape(path) + r"(?![\w-])", lambda _m, s=stand_in: s, text)
    text = USER_TMP.sub(lambda m: "$TMPDIR" if m.group(1) else "/var/folders/<user>", text)
    return HOMES.sub("~", text)


def summarize(log):
    """What the log shows: tests per group, coverage, and the problems scripts/test.sh reported."""
    ran = [int(n) for n in re.findall(r"^Ran (\d+) tests? in", log, re.M)]
    executed = [int(n) for n in re.findall(r"Executed (\d+) tests?, with", log)]
    coverage = re.findall(r"^TOTAL\s.*?(\d+(?:\.\d+)?)%\s*$", log, re.M)
    return {
        "python": sum(ran),
        "swift": max(executed, default=0),
        "bats": len(re.findall(r"^ok \d+", log, re.M)),
        "coverage": float(coverage[-1]) if coverage else None,
        "shellcheck": "==> shellcheck" in log,
        "problems": re.findall(r"^  !! (.+)$", log, re.M) + re.findall(r"^not ok \d+ (.+)$", log, re.M)
        + [f"python: {f}" for f in re.findall(r"^FAILED \((.+)\)$", log, re.M)],
        "green": bool(re.search(r"^==> all green\s*$", log, re.M)),
    }


def failures(s, code):
    """Why this run does not count: nothing means it passed."""
    out = list(s["problems"])
    if code != 0:
        out.append(f"exit code {code}")
    if not s["green"]:
        out.append("scripts/test.sh did not finish with all green")
    out += [f"{group} tests did not run" for group in ("python", "swift", "bats") if not s[group]]
    if s["coverage"] is None:
        out.append("no coverage figure")
    if not s["shellcheck"]:
        out.append("shellcheck did not run")
    return out


def one_line(s, failed):
    head = f"Python {s['python']}, coverage {s['coverage']} %, Swift {s['swift']}, bats {s['bats']}"
    return f"{head}: FAILED, {failed[0]}" if failed else head


def main():
    log_path, code, out = sys.argv[1], int(sys.argv[2]), Path(sys.argv[3])
    paths = private_paths()
    log = redact(Path(log_path).read_bytes().decode("utf-8", "replace"), paths)
    summary = summarize(log)
    failed = failures(summary, code)
    extra = [redact(item, paths) for item in sys.argv[4:]]
    for item in extra:
        key, _, value = item.partition("=")
        if key == "problem" and value:
            failed.insert(0, value)
    head = [item.partition("=") for item in extra if not item.startswith("problem=")]
    lines = ["result: " + ("FAILED" if failed else "passed")]
    lines += [f"{k}: {v}" for k, _, v in head]
    lines += [f"summary: {one_line(summary, failed)}", f"exit code: {code}",
              f"log sha256: {hashlib.sha256(log.encode()).hexdigest()}"]
    lines += [f"problem: {p}" for p in failed]
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text("\n".join(lines) + "\n\n----- full log -----\n" + log, encoding="utf-8")
    print(one_line(summary, failed))
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
