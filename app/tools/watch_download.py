#!/usr/bin/env python3
"""Run a model download (or conversion) and stop it when it makes no progress.

  watch_download.py [--stall SECS] [--every SECS] [--log FILE] [--hf-cache] [--watch DIR]... -- CMD [ARGS...]

Progress is either of two things: the command (or any process it started) receiving data from the network,
counted by macOS's nettop, or the files under the watched folders growing (--hf-cache adds Hugging Face's
cache folders). File sizes alone are not enough: Hugging Face's Xet transfers fetch large blocks and write
each one only when it is complete, so a healthy download can leave the files unchanged for minutes.

A slow download is fine; one with neither for --stall seconds (default 300, or MODEL_STALL_SECS) is stopped
with its whole process group, and the exit code is 124. Hugging Face keeps the partial files, so running the
same command again resumes. While it runs, a line every --every seconds (default 60) says how much has been
received, so the setup guide shows that something is happening.

The command's own output goes to --log (default: discarded); this tool prints only its own short lines.
"""
import argparse
import os
import signal
import subprocess
import sys
import time

NETTOP = os.environ.get("MC_NETTOP", "/usr/bin/nettop")
PGREP = "/usr/bin/pgrep"


def hf_cache_dirs():
    try:
        from huggingface_hub import constants
    except ImportError:
        return []
    dirs = [constants.HF_HUB_CACHE, getattr(constants, "HF_HOME", "")]
    xet = getattr(constants, "HF_XET_CACHE", "")
    return [d for d in (*dirs, xet) if d]


def files_state(dirs):
    """(bytes, allocated bytes) of the files under dirs: a block written into a file that was created at its
    full size changes the second number only."""
    size = alloc = 0
    for top in dirs:
        for root, _subdirs, files in os.walk(top):
            for name in files:
                try:
                    st = os.lstat(os.path.join(root, name))
                except OSError:        # removed or renamed while we looked
                    continue
                size += st.st_size
                alloc += st.st_blocks * 512
    return size, alloc


class NetCounter:
    """Bytes received so far by a process group, from nettop's per-connection lines. nettop counts only the
    connections open right now (its per-process number drops when one closes, and stayed below an earlier
    peak for minutes on a real download), so every connection seen is remembered: one that closes keeps its
    bytes, and one whose count falls has been replaced by a new connection on the same port."""

    def __init__(self, pgid):
        self.pgid = pgid
        self.flows = {}        # connection -> [bytes of earlier connections on it, bytes now]

    def pids(self):
        try:
            out = subprocess.run([PGREP, "-g", str(self.pgid)], capture_output=True, text=True, timeout=10).stdout
        except (OSError, subprocess.SubprocessError):
            return [self.pgid]
        return [int(p) for p in out.split() if p.isdigit()] or [self.pgid]

    def total(self):
        args = [NETTOP, "-L", "1", "-x", "-J", "bytes_in"]
        for pid in self.pids():
            args += ["-p", str(pid)]
        try:
            out = subprocess.run(args, capture_output=True, text=True, timeout=20).stdout
        except (OSError, subprocess.SubprocessError):
            out = ""
        for line in out.splitlines():       # "tcp4 192.0.2.10:50000<->198.51.100.7:443,1000000,"
            name, _, rest = line.partition(",")
            count = rest.split(",")[0]
            if "<->" not in name or not count.isdigit():
                continue                    # the header, or a process's own (open connections only) line
            flow = self.flows.setdefault(name, [0, 0])
            if int(count) < flow[1]:
                flow[0] += flow[1]
            flow[1] = int(count)
        return sum(earlier + now for earlier, now in self.flows.values())


def human(n):
    return f"{n / 1e9:.1f} GB" if n >= 1e9 else f"{n / 1e6:.0f} MB"


def stop(proc, grace=10):
    for sig in (signal.SIGTERM, signal.SIGKILL):
        try:
            os.killpg(proc.pid, sig)
        except ProcessLookupError:
            return
        try:
            proc.wait(timeout=grace)
            return
        except subprocess.TimeoutExpired:
            pass


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--stall", type=float, default=float(os.environ.get("MODEL_STALL_SECS", 300)))
    ap.add_argument("--every", type=float, default=60)
    ap.add_argument("--poll", type=float, default=5, help=argparse.SUPPRESS)
    ap.add_argument("--log")
    ap.add_argument("--hf-cache", action="store_true")
    ap.add_argument("--watch", action="append", default=[])
    ap.add_argument("cmd", nargs=argparse.REMAINDER)
    a = ap.parse_args(argv)
    cmd = a.cmd[1:] if a.cmd[:1] == ["--"] else a.cmd
    if not cmd:
        ap.error("no command")
    dirs = [*a.watch, *(hf_cache_dirs() if a.hf_cache else [])]

    out = open(a.log, "ab") if a.log else subprocess.DEVNULL   # noqa: SIM115 - closed below
    try:
        proc = subprocess.Popen(cmd, stdin=subprocess.DEVNULL, stdout=out, stderr=subprocess.STDOUT,
                                start_new_session=True)       # its own group: stop() reaches its children

        def interrupted(signum, _frame):                      # stopping this tool stops the download too
            stop(proc)
            sys.exit(128 + signum)
        signal.signal(signal.SIGTERM, interrupted)
        signal.signal(signal.SIGINT, interrupted)
        net = NetCounter(proc.pid)
        start_files = last_files = files_state(dirs)
        last_net = 0
        last_change = last_report = time.monotonic()
        while True:
            try:
                return proc.wait(timeout=a.poll)
            except subprocess.TimeoutExpired:
                pass
            now, files, received = time.monotonic(), files_state(dirs), net.total()
            if files != last_files or received > last_net:
                last_files, last_net, last_change = files, received, now
            if now - last_change >= a.stall:
                stop(proc)
                print(f"  ! no download progress for {a.stall:g} s: stopped. Press Install again to resume "
                      f"(the downloaded part is kept).", flush=True)
                return 124
            if now - last_report >= a.every:
                last_report = now
                done = received or max(0, files[0] - start_files[0])
                print(f"  ... {human(done)} downloaded", flush=True)
    finally:
        if a.log:
            out.close()


if __name__ == "__main__":
    sys.exit(main())
