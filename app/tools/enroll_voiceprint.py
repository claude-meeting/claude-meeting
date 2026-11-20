"""Enroll the user's voiceprint for speaker verification.

Sources, any mix:
  a meeting base path (Minutes recording, without extension): its microphone track is used,
      minus call echo and minus quiet segments (people near the mic), i.e. the loud group
      that SelfLevelGate learns for the meeting;
  a .wav file of only the user talking (e.g. 30-60 s of normal speech): all speech is used.

Usage: enroll_voiceprint.py <source> [<source> ...] [--out file] [--max-segments 80]
"""
import argparse
import sys
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "lib"))
import asr  # noqa: E402
import config  # noqa: E402
import voiceprint  # noqa: E402

MIN_SEGMENTS = 5
CHUNK_SECS = 3   # VAD only splits at pauses of 0.4 s or more: someone talking on without them gives one long span


def pieces(a, b):
    """[a, b) cut into near-equal pieces of about CHUNK_SECS (a short span stays whole)."""
    n = max(1, (b - a) // (CHUNK_SECS * asr.SAMPLE_RATE))
    step = (b - a) // n
    return [(a + i * step, b if i == n - 1 else a + (i + 1) * step) for i in range(n)]


def speech(audio, call=None):
    """Speech segments of 1 to ~2 x CHUNK_SECS s; with a call track, drop echo and the quiet group."""
    segs = []
    for s, e in asr.speech_spans(audio):
        a, b = int(s * asr.SAMPLE_RATE), int(e * asr.SAMPLE_RATE)
        if b - a < asr.SAMPLE_RATE:
            continue
        for a, b in pieces(a, b):
            if call is not None and asr.is_echo(asr.rms(audio[a:b]), asr.rms(call[a:b])):
                continue
            segs.append(audio[a:b])
    if call is None or not segs:
        return segs
    gate = asr.SelfLevelGate()
    gate.levels = [asr.rms(x) for x in segs]
    t = gate.threshold()
    return [x for x in segs if asr.rms(x) >= t]


def main():
    cfg = config.load()
    default_out = str(config.voiceprint_path(cfg))
    ap = argparse.ArgumentParser()
    ap.add_argument("sources", nargs="+")
    ap.add_argument("--out", default=default_out)
    ap.add_argument("--max-segments", type=int, default=80)
    args = ap.parse_args()

    segs = []
    for src in args.sources:
        p = Path(src).expanduser()
        if p.suffix == ".md":                     # a Minutes note: use its recording
            p = p.with_suffix("")
        if p.suffix == ".wav":
            found = speech(asr.load_track(str(p)))
        else:
            found = speech(asr.load_track(f"{p}.voice.wav"), asr.load_track(f"{p}.system.wav"))
        print(f"{p.name}: {len(found)} segment(s) of the user's speech")
        segs += found
    if len(segs) < MIN_SEGMENTS:
        secs = sum(map(len, segs)) / asr.SAMPLE_RATE
        sys.exit(f"only {secs:.0f} s of speech found, need about {MIN_SEGMENTS * CHUNK_SECS} s: "
                 "keep talking for most of the recording and retry")

    # Longest segments first: they give the most stable embeddings.
    segs = sorted(segs, key=len, reverse=True)[:args.max_segments]
    vp = voiceprint.Voiceprint()
    n = vp.enroll(segs)
    scores = np.array([vp.score(x) for x in segs])
    out = Path(args.out).expanduser()
    vp.save(out)
    print(f"enrolled {n} segment(s) ({sum(map(len, segs)) / asr.SAMPLE_RATE:.0f} s); "
          f"self-similarity median {np.median(scores):.2f}, min {scores.min():.2f}")
    print(f"saved {out}")


if __name__ == "__main__":
    main()
