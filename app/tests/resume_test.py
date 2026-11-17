"""Live check (real models): the transcriber resumes a meeting correctly after a restart.

Runs bin/live_transcriber.py on the first part of a real recording's two stems, then grows the stems
(the call track shorter than the mic track, as Minutes pads them) and runs it again with the same
transcript. Passes when every track resumed from its own point, no row overlaps an earlier one, both
speakers appear in the second half and the speaker letters were restored.

Usage: resume_test.py [<meeting base>] [--start mm:ss] [--half 35]
The meeting base is a two-stem recording of your own (<base>.voice.wav + <base>.system.wav), given as the
argument or in $MC_LIVE_MEETING. --start (default 00:00) should be a point where both speakers talk.
"""
import argparse
import json
import os
import struct
import subprocess
import sys
import tempfile
import time
from pathlib import Path

import soundfile as sf

APP = Path(__file__).resolve().parents[1]
PY = sys.executable
SR = 16000


def header():
    return (b"RIFF" + struct.pack("<I", 0) + b"WAVEfmt " + struct.pack("<IHHIIHH", 16, 1, 1, SR, SR * 2, 2, 16)
            + b"data" + struct.pack("<I", 0))


def mmss(value):
    m, s = value.split(":")
    return int(m) * 60 + float(s)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("base", nargs="?", default=os.environ.get("MC_LIVE_MEETING"))
    ap.add_argument("--start", type=mmss, default=0.0)
    ap.add_argument("--half", type=float, default=35.0)
    args = ap.parse_args()
    if not args.base:
        sys.exit("set MC_LIVE_MEETING (or pass a meeting base) to a two-stem recording: "
                 "<base>.voice.wav + <base>.system.wav")
    base = Path(args.base).expanduser()
    if not Path(f"{base}.voice.wav").exists():
        sys.exit(f"no recording at {base}.voice.wav; pass a meeting base path or set MC_LIVE_MEETING")

    out_dir = Path(tempfile.mkdtemp(prefix="mc-resume-"))
    wav, transcript, log = out_dir / "sim.wav", out_dir / "live.jsonl", out_dir / "transcriber.log"
    tracks = {}
    for stem in ("voice", "system"):
        audio, sr = sf.read(f"{base}.{stem}.wav", dtype="int16")
        assert sr == SR, f"{stem}: {sr} Hz"
        tracks[stem] = audio[int(args.start * SR): int((args.start + 2 * args.half) * SR)]

    def write(stem, seconds):
        p = out_dir / f"sim.{stem}.wav"
        data = tracks[stem][: int(seconds * SR)].astype("<i2").tobytes()
        if not p.exists():
            p.write_bytes(header() + data)
        else:
            with open(p, "r+b") as f:        # same inode, as Minutes does
                f.seek(0, 2)
                f.write(data[f.tell() - 44:])

    def run(label):
        t0 = time.time()
        with open(log, "a") as lf:
            lf.write(f"===== {label}\n")
            r = subprocess.run([PY, str(APP / "bin" / "live_transcriber.py"), str(wav), str(transcript)],
                               stdout=lf, stderr=lf)
        rows = [json.loads(l) for l in transcript.read_text().splitlines() if l.strip()]
        print(f"{label}: exit {r.returncode} in {time.time() - t0:.0f}s, {len(rows)} rows")
        return rows

    write("voice", args.half)
    write("system", args.half - 8)                                   # the call track lags behind
    run("first half")
    snap_path = out_dir / "live.speakers.json"
    before = json.loads(snap_path.read_text()) if snap_path.exists() else {}
    write("voice", 2 * args.half)
    write("system", 2 * args.half)
    rows = run("second half (resumed)")

    ok = True
    by = {}
    for r in rows:
        by.setdefault(r["speaker"], []).append(r)
    for speaker, items in by.items():
        overlaps = sum(1 for a, b in zip(items, items[1:]) if b["start"] < a["end"] - 0.5)
        second = sum(1 for r in items if r["start"] >= args.half - 8)
        print(f"{speaker}: {len(items)} rows, {second} in the second half, overlapping: {overlaps}")
        ok &= overlaps == 0 and second > 0
    ok &= {"我", "其他人"} <= set(by)
    after = json.loads(snap_path.read_text()) if snap_path.exists() else {}
    ok &= set(before) <= set(after)
    text = log.read_text()
    resumed = [l for l in text.splitlines() if "resuming after" in l]
    print("resume:", resumed or "none")
    ok &= len(resumed) >= 1 and ("speakers restored" in text or not before)
    print("RESULT", "OK" if ok else "FAIL", f"(log: {log})")
    sys.exit(0 if ok else 1)


if __name__ == "__main__":
    main()
