"""Simulate an in-progress Minutes recording and measure live transcription latency.

Copies a finished meeting's .voice.wav / .system.wav into growing files in real time
(or faster with --speed), runs bin/live_transcriber.py on them, and reports for each
transcribed utterance how long after the end of its audio the text appeared.

Usage: simulate_live.py <meeting base> <start mm:ss> <seconds> [--speed 1.0]
                        [--assert-median-latency SECS] [--assert-speakers 我,其他人]
The --assert options turn the replay into a check (exit 1 when the latency median is above the bound or a
listed speaker never appears); scripts/test.sh --live uses them.
"""
import argparse
import json
import subprocess
import sys
import tempfile
import time
from pathlib import Path

import numpy as np
import soundfile as sf

APP = Path(__file__).resolve().parents[1]
PY = sys.executable


def header():
    # 44-byte PCM16 mono 16 kHz header with zero sizes, like Minutes while recording.
    import struct
    return (b"RIFF" + struct.pack("<I", 0) + b"WAVEfmt " + struct.pack("<IHHIIHH", 16, 1, 1, 16000, 32000, 2, 16)
            + b"data" + struct.pack("<I", 0))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("base")
    ap.add_argument("start")
    ap.add_argument("seconds", type=float)
    ap.add_argument("--speed", type=float, default=1.0)
    ap.add_argument("--assert-median-latency", type=float)
    ap.add_argument("--assert-speakers", help="comma-separated speaker tags that must appear")
    args = ap.parse_args()
    m, s = args.start.split(":")
    start = int(m) * 60 + float(s)

    tracks = {}
    for stem in ("voice", "system"):
        audio, sr = sf.read(f"{Path(args.base).expanduser()}.{stem}.wav", dtype="int16")
        tracks[stem] = audio[int(start * sr): int((start + args.seconds) * sr)]

    tmp = Path(tempfile.mkdtemp(prefix="sim-live-"))
    wav, out = tmp / "sim.wav", tmp / "live.jsonl"
    files = {stem: open(tmp / f"sim.{stem}.wav", "wb") for stem in tracks}
    for f in files.values():
        f.write(header())
        f.flush()
    wav.write_bytes(header())

    proc = subprocess.Popen([PY, str(APP / "bin" / "live_transcriber.py"), str(wav), str(out)],
                            stderr=open(tmp / "transcriber.log", "w"))
    time.sleep(12)   # let models load before audio starts flowing

    t0, step = time.time(), 0.25
    n = len(tracks["voice"])
    written, seen, latencies = 0, 0, []
    while written < n or proc.poll() is None:
        target = min(n, int((time.time() - t0) * args.speed * 16000))
        if target > written:
            for stem, f in files.items():
                f.write(tracks[stem][written:target].astype("<i2").tobytes())
                f.flush()
            written = target
        if out.exists():
            lines = out.read_text().splitlines()
            for line in lines[seen:]:
                r = json.loads(line)
                audio_end_at = t0 + r["end"] / args.speed
                latencies.append(time.time() - audio_end_at)
                print(f"+{latencies[-1]:4.1f}s [{r['speaker']}] {r['text'][:60]}", flush=True)
            seen = len(lines)
        if written >= n and proc.poll() is not None:
            break
        time.sleep(step)

    if latencies:
        print(f"\nutterances: {len(latencies)}  latency median {np.median(latencies):.1f}s  "
              f"p90 {np.percentile(latencies, 90):.1f}s  max {max(latencies):.1f}s")
    print(f"log: {tmp / 'transcriber.log'}")
    ok = True
    if args.assert_median_latency is not None:
        median = float(np.median(latencies)) if latencies else float("inf")
        ok &= median <= args.assert_median_latency
        print(f"check: median latency {median:.1f}s <= {args.assert_median_latency}s: {'ok' if ok else 'FAIL'}")
    if args.assert_speakers:
        seen_speakers = {json.loads(l)["speaker"] for l in out.read_text().splitlines()} if out.exists() else set()
        missing = [s for s in args.assert_speakers.split(",") if s and s not in seen_speakers]
        print(f"check: speakers {sorted(seen_speakers)}; missing {missing or 'none'}")
        ok &= not missing
    if args.assert_median_latency is not None or args.assert_speakers:
        sys.exit(0 if ok else 1)


if __name__ == "__main__":
    main()
