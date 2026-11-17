#!/usr/bin/env python3
"""Fake live transcriber: plays $MC_FAKE_DIR/transcript.jsonl into <out.jsonl> one row at a time
(every $MC_FAKE_TRANSCRIBER_INTERVAL seconds, default 0.05), writes <out>.speakers.json and the pidfile
like the real one, then waits until the fake Minutes says the recording stopped. Exit code from
$MC_FAKE_TRANSCRIBER_EXIT (default 0). Usage: live_transcriber.py <recording.wav> <out.jsonl>"""
import json
import os
import sys
import time
from datetime import datetime
from pathlib import Path

FAKE = Path(os.environ["MC_FAKE_DIR"])
wav, out = Path(sys.argv[1]), Path(sys.argv[2])
with open(FAKE / "calls.log", "a") as f:
    f.write("live_transcriber\t" + json.dumps(sys.argv[1:]) + "\n")
pidfile = out.with_suffix(".pid")
try:
    os.kill(int(pidfile.read_text()), 0)
    sys.exit(4)
except (OSError, ValueError):
    pass
pidfile.write_text(str(os.getpid()))
interval = float(os.environ.get("MC_FAKE_TRANSCRIBER_INTERVAL", "0.05"))
rows = [json.loads(l) for l in (FAKE / "transcript.jsonl").read_text().splitlines() if l.strip()] \
    if (FAKE / "transcript.jsonl").exists() else []
done = len(out.read_text().splitlines()) if out.exists() else 0      # resume: skip rows already written
speakers = {}
with open(out, "a") as f:
    for row in rows[done:]:
        time.sleep(interval)
        row["ts"] = datetime.now().isoformat(timespec="seconds")      # wall clock, as the real transcriber
        f.write(json.dumps(row, ensure_ascii=False) + "\n")
        f.flush()
        if row.get("who"):
            speakers.setdefault(row["who"], {"vector": [0.1] * 4, "count": 0})["count"] += 1
            out.with_name(out.stem + ".speakers.json").write_text(json.dumps(speakers))


def recording():
    try:
        return bool(json.loads((FAKE / "minutes.json").read_text()).get("recording"))
    except (OSError, ValueError):
        return False


deadline = time.time() + float(os.environ.get("MC_FAKE_TRANSCRIBER_MAX_SECS", "20"))
while recording() and time.time() < deadline and not (FAKE / "transcriber-stop").exists():
    time.sleep(0.05)
pidfile.unlink(missing_ok=True)
sys.exit(int(os.environ.get("MC_FAKE_TRANSCRIBER_EXIT", "0")))
