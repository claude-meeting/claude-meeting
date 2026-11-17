"""Build a speech-recognition validation set from a real meeting.

Three different recognizers transcribe every speech segment of a stretch of a meeting:
TEA-ASR (primary), Qwen3-ASR (fallback) and a Whisper model through whisper.cpp. Where all three
agree, that text is taken as the reference; only the segments where they disagree go to a person,
who corrects one line per segment in review.md. That turns 20-30 minutes of meeting into a
reference set in 30-40 minutes of checking instead of hours of typing.

  validation_set.py build <meeting base> [--start mm:ss] [--minutes 25] [--whisper-model ggml.bin]
      (needs whisper.cpp's `whisper-server` on PATH, e.g. `brew install whisper-cpp`; the two MLX
       recognizers are the configured primary and fallback models, in the configured language)
  validation_set.py rejudge <out dir>      recompute agreement and review.md (no transcription)
  validation_set.py finalize <out dir>     review.md -> refs.jsonl (reference transcripts)

Output (default app/work/validation/<meeting>/): clips/*.wav, segments.jsonl, review.md, refs.jsonl.
"""
import argparse
import json
import re
import subprocess
import sys
import time
import unicodedata
import urllib.request
import uuid
from pathlib import Path

import opencc
import soundfile as sf

APP = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(APP / "lib"))
import asr  # noqa: E402
import i18n  # noqa: E402
import config  # noqa: E402
import voiceprint  # noqa: E402

TRACKS = (("voice", i18n.SELF), ("system", i18n.OTHERS))
AGREE_MER = 0.10            # every pair of hypotheses within 10 % MER counts as agreement
PORT = 8179
LANG_CODE = "zh" if config.language_family(config.load()).startswith("zh") else "en"
T2S = opencc.OpenCC("t2s")
S2TWP = opencc.OpenCC("s2twp")
TOKEN = re.compile(r"[㐀-䶿一-鿿豈-﫿]|[a-z0-9]+(?:'[a-z]+)?")
REVIEW_LINE = re.compile(r"^- \[(?P<id>[^\]]+)\] 正確：(?P<text>.*)$")


def tokens(text):
    """Chinese per character, English per word, script and punctuation ignored."""
    return TOKEN.findall(unicodedata.normalize("NFKC", T2S.convert(text or "")).lower())


def mer(ref, hyp):
    """Token edit distance over the longer side: symmetric, and a truncated hypothesis scores as far
    from the full one as an over-long one does (a one-sided MER let truncations look closest)."""
    r, h = tokens(ref), tokens(hyp)
    if not r and not h:
        return 0.0
    prev = list(range(len(h) + 1))
    for i, a in enumerate(r, 1):
        cur = [i] + [0] * len(h)
        for j, b in enumerate(h, 1):
            cur[j] = min(prev[j] + 1, cur[j - 1] + 1, prev[j - 1] + (a != b))
        prev = cur
    return prev[-1] / max(len(r), len(h))


class Whisper:
    """whisper.cpp server with the model loaded once."""

    def __init__(self, model):
        self.proc = subprocess.Popen(["whisper-server", "-m", str(model), "-l", LANG_CODE, "--port", str(PORT),
                                      "--host", "127.0.0.1"], stdout=subprocess.DEVNULL,
                                     stderr=subprocess.DEVNULL)
        while True:
            try:
                urllib.request.urlopen(f"http://127.0.0.1:{PORT}/", timeout=2)
                return
            except Exception:  # noqa: BLE001
                if self.proc.poll() is not None:
                    raise SystemExit("whisper-server exited during startup")
                time.sleep(1)

    def transcribe(self, wav):
        boundary = uuid.uuid4().hex
        parts = [f'--{boundary}\r\nContent-Disposition: form-data; name="{k}"\r\n\r\n{v}\r\n'.encode()
                 for k, v in (("response_format", "json"), ("language", LANG_CODE), ("temperature", "0.0"))]
        parts.append(f'--{boundary}\r\nContent-Disposition: form-data; name="file"; filename="a.wav"\r\n'
                     "Content-Type: audio/wav\r\n\r\n".encode() + Path(wav).read_bytes() + b"\r\n")
        parts.append(f"--{boundary}--\r\n".encode())
        req = urllib.request.Request(f"http://127.0.0.1:{PORT}/inference", data=b"".join(parts),
                                     headers={"Content-Type": f"multipart/form-data; boundary={boundary}"})
        text = " ".join(json.load(urllib.request.urlopen(req, timeout=600)).get("text", "").split())
        return asr.normalize_numbers(S2TWP.convert(text)).strip()

    def close(self):
        self.proc.terminate()


def mmss(value):
    m, s = value.split(":")
    return int(m) * 60 + float(s)


def build(args):
    base = Path(args.base).expanduser()
    out = Path(args.out or APP / "work" / "validation" / base.name).resolve()
    (out / "clips").mkdir(parents=True, exist_ok=True)
    a, b = int(args.start * asr.SAMPLE_RATE), int((args.start + args.minutes * 60) * asr.SAMPLE_RATE)
    call_full = asr.load_track(f"{base}.system.wav")
    voice, threshold = voiceprint.from_config(config.load())

    segments = []
    for stem, speaker in TRACKS:
        track = (call_full if stem == "system" else asr.load_track(f"{base}.{stem}.wav"))[a:b]
        call = call_full[a:b]
        for s, e in asr.speech_spans(track):
            i, j = int(s * asr.SAMPLE_RATE), int(e * asr.SAMPLE_RATE)
            clip = track[i:j]
            if j - i < asr.SAMPLE_RATE * 0.5:
                continue
            if stem == "voice":
                if asr.is_echo(asr.rms(clip), asr.rms(call[i:j])):
                    continue
                score = voice.score(clip) if voice else None
                if score is not None and score < threshold:
                    continue
            sid = f"{speaker}-{args.start + s:07.1f}"
            sf.write(out / "clips" / f"{sid}.wav", clip, asr.SAMPLE_RATE, subtype="PCM_16")
            segments.append({"id": sid, "speaker": speaker, "start": round(args.start + s, 2),
                             "end": round(args.start + e, 2), "clip": f"clips/{sid}.wav"})
    segments.sort(key=lambda r: r["start"])
    print(f"{len(segments)} segments in {args.minutes} min from {args.start / 60:.1f} min")

    tr = asr.Transcriber()
    for seg in segments:
        audio = asr.load_track(str(out / seg["clip"]))
        secs = len(audio) / asr.SAMPLE_RATE
        tea, _ = tr._run(tr.primary, audio, secs)
        qwen, _ = tr._run(tr.fallback, audio, secs)
        if tr.family == "zh-hans":     # both written as the transcriber writes them: Simplified, digits as digits
            tea, qwen = (asr.normalize_numbers(tr._t2s.convert(asr.PUA.sub("", t))) for t in (tea, qwen))
        else:
            tea, qwen = asr.PUA.sub("", tea), asr.normalize_numbers(tr._s2twp.convert(qwen))
        seg["hyp"] = {"tea": tea.strip(), "qwen3": qwen.strip()}
    del tr
    whisper = Whisper(Path(args.whisper_model).expanduser())
    try:
        for seg in segments:
            seg["hyp"]["whisper"] = whisper.transcribe(out / seg["clip"])
    finally:
        whisper.close()

    judge(out, segments)


def judge(out, segments):
    """Agreement and the prefilled best guess for every segment, then segments.jsonl and review.md."""
    agreed = 0
    for seg in segments:
        h = seg["hyp"]
        pairs = [mer(h["tea"], h["qwen3"]), mer(h["tea"], h["whisper"]), mer(h["qwen3"], h["whisper"])]
        seg["max_pair_mer"] = round(max(pairs), 3)
        seg["agree"] = seg["max_pair_mer"] <= AGREE_MER
        # Best guess: the hypothesis closest to the other two (what review.md is prefilled with).
        names = list(h)
        seg["best"] = min(names, key=lambda n: sum(mer(h[o], h[n]) for o in names if o != n))
        agreed += seg["agree"]
    with open(out / "segments.jsonl", "w") as f:
        for seg in segments:
            f.write(json.dumps(seg, ensure_ascii=False) + "\n")
    write_review(out, segments)
    print(f"{agreed}/{len(segments)} agree; review {len(segments) - agreed} in {out / 'review.md'}")


def rejudge(args):
    out = Path(args.dir).resolve()
    with open(out / "segments.jsonl") as f:
        judge(out, [json.loads(l) for l in f])


def write_review(out, segments):
    todo = sorted((s for s in segments if not s["agree"]), key=lambda s: s["start"])
    lines = ["# 驗證集人工檢查", "",
             f"三個模型不一致的 {len(todo)} 段（一致的 {len(segments) - len(todo)} 段已自動採用）。",
             "播放每段音檔，把「正確：」後面改成實際講的話；聽不清楚就整行留空，該段會被排除。",
             "改完執行：validation_set.py finalize <這個資料夾>", ""]
    for s in todo:
        h = s["hyp"]
        lines += [f"## {s['id']}（{s['end'] - s['start']:.1f} 秒）", f"音檔：{out / s['clip']}", "",
                  f"- TEA：{h['tea']}", f"- Qwen3：{h['qwen3']}", f"- Whisper：{h['whisper']}",
                  f"- [{s['id']}] 正確：{h[s['best']]}", ""]
    (out / "review.md").write_text("\n".join(lines))


def finalize(args):
    out = Path(args.dir)
    with open(out / "segments.jsonl") as f:
        segments = [json.loads(l) for l in f]
    reviewed = {}
    for line in (out / "review.md").read_text().splitlines():
        m = REVIEW_LINE.match(line)
        if m:
            reviewed[m["id"]] = m["text"].strip()
    kept = skipped = 0
    with open(out / "refs.jsonl", "w") as f:
        for s in segments:
            text = s["hyp"][s["best"]] if s["agree"] else reviewed.get(s["id"], "")
            if not text:
                skipped += 1
                continue
            f.write(json.dumps({"id": s["id"], "file": str((out / s["clip"]).resolve()), "text": text,
                                "duration": round(s["end"] - s["start"], 2), "speaker": s["speaker"],
                                "source": "consensus" if s["agree"] else "human"}, ensure_ascii=False) + "\n")
            kept += 1
    print(f"{out / 'refs.jsonl'}: {kept} segments ({skipped} excluded).")


def main():
    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(dest="cmd", required=True)
    b = sub.add_parser("build")
    b.add_argument("base")
    b.add_argument("--start", type=mmss, default=0.0)
    b.add_argument("--minutes", type=float, default=25)
    b.add_argument("--whisper-model", default="~/.minutes/models/ggml-large-v3.bin")
    b.add_argument("--out")
    f = sub.add_parser("finalize")
    f.add_argument("dir")
    r = sub.add_parser("rejudge")
    r.add_argument("dir")
    args = ap.parse_args()
    {"build": build, "finalize": finalize, "rejudge": rejudge}[args.cmd](args)


if __name__ == "__main__":
    main()
