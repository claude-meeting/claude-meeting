"""Offline: transcribe a finished Minutes recording's two stems and merge them by speaker.

<base>.voice.wav  = the user's microphone   -> speaker "我"
<base>.system.wav = everyone else (call audio) -> speaker "其他人"

Usage: transcribe_tracks.py <meeting base path without extension> [--out file.jsonl]
"""
import argparse
import json
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "lib"))
import asr  # noqa: E402
import i18n  # noqa: E402
import voiceprint  # noqa: E402

TRACKS = (("voice", i18n.SELF), ("system", i18n.OTHERS))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("base")
    ap.add_argument("--out")
    args = ap.parse_args()
    base = Path(args.base).expanduser()
    out = Path(args.out) if args.out else Path(f"{base}.speakers.jsonl")

    t0 = time.time()
    tr = asr.Transcriber()
    print(f"models loaded in {time.time() - t0:.1f}s", flush=True)

    voice, threshold = voiceprint.from_config(tr.cfg)
    rows = []
    call = asr.load_track(f"{base}.system.wav")
    for stem, speaker in TRACKS:
        audio = call if stem == "system" else asr.load_track(f"{base}.{stem}.wav")
        t = time.time()
        segs = []
        echoes = others = 0
        for s, e in tr.speech_spans(audio):
            a, b = int(s * asr.SAMPLE_RATE), int(e * asr.SAMPLE_RATE)
            if b - a < asr.SAMPLE_RATE * 0.3:
                continue
            if stem == "voice":
                score = voice.score(audio[a:b]) if voice else None
                if score is not None:             # voiceprint first, as in the live transcriber
                    if score < threshold:
                        others += 1
                        continue
                elif asr.is_echo(asr.rms(audio[a:b]), asr.rms(call[a:b])):
                    echoes += 1
                    continue
            segs.append(tr.transcribe_segment(audio[a:b], s, e))
        took = time.time() - t
        if stem == "voice":
            print(f"  dropped {echoes} microphone segments as call echo, {others} as not the user's voice (or echo)",
                  flush=True)
        spoken = sum(s.end - s.start for s in segs)
        counts = {k: sum(s.engine == k for s in segs) for k in ("primary", "fallback", "dropped")}
        print(f"{speaker} ({stem}): {len(audio) / asr.SAMPLE_RATE / 60:.1f} min track, {len(segs)} segments, "
              f"{spoken / 60:.1f} min speech, {counts}, {took:.0f}s", flush=True)
        rows += [{"start": round(s.start, 2), "end": round(s.end, 2), "speaker": speaker, "text": s.text,
                  "engine": s.engine, "note": s.note} for s in segs]

    rows.sort(key=lambda r: r["start"])
    with open(out, "w") as f:
        for r in rows:
            f.write(json.dumps(r, ensure_ascii=False) + "\n")
    print(f"wrote {len(rows)} rows to {out}")


if __name__ == "__main__":
    main()
