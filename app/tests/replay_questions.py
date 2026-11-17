"""Replay a stretch of a transcript through the live question path and show what gets answered.

Feeds lines the way the copilot does (name -> answered at once, keyword -> answered at once, other
lines -> QuestionWindow batch check) at --pace seconds per line, with the real fast model, and prints
every answer header written to the notes. An optional answer key of your own, app/work/question-gold.json
(git-ignored; {transcript name: {line text: label}}), marks the known questions and reports which of them
were answered.

Usage: replay_questions.py <transcript.jsonl> [--from N] [--to M] [--pace 0.5]
"""
import argparse
import json
import sys
import threading
import time
from pathlib import Path

APP = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(APP / "lib"))
import config  # noqa: E402
import i18n  # noqa: E402
import llm  # noqa: E402
import realtime  # noqa: E402


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("transcript")
    ap.add_argument("--from", dest="start", type=int, default=0)
    ap.add_argument("--to", dest="end", type=int, default=None)
    ap.add_argument("--pace", type=float, default=0.5)
    args = ap.parse_args()
    cfg = config.load()
    lang = i18n.Lang(cfg)
    claude = config.claude_cli(cfg)
    user = cfg["user"]["name"] or lang.t("user_fallback")
    aliases = tuple(cfg["user"]["aliases"]) or (user,)
    explain = cfg.get("explain", {})
    rows = sorted((json.loads(l) for l in open(args.transcript) if l.strip()), key=lambda r: r.get("start", 0))
    rows = rows[args.start:args.end]
    gold_path = APP / "work" / "question-gold.json"
    gold = json.loads(gold_path.read_text()).get(Path(args.transcript).stem, {}) if gold_path.exists() else {}

    notes, logs = [], []
    t0 = time.time()

    def write(text):
        notes.append(text)
        if text.startswith("\n### "):
            print(f"  +{time.time() - t0:5.1f}s ANSWER {text.strip()[4:]}", flush=True)

    def log(msg):
        logs.append(msg)
        print(f"  +{time.time() - t0:5.1f}s {msg}", flush=True)

    explainer = realtime.UrgentExplainer(
        llm=llm.ClaudeCLI(claude), model=explain.get("answer_model", "claude-sonnet-5-5"), verify_model=explain.get("verify_model"),
        user=user, aliases=aliases, detail=explain.get("detail", "medium"), known_terms=[], web_verify=False,
        max_per_minute=explain.get("max_per_minute", 4), write=write, notify=lambda *a, **k: None, log=log,
        remember=lambda x: None, stream_lock=threading.Lock(), lang=lang)
    window = realtime.QuestionWindow(llm=llm.ClaudeCLI(claude), model=explain.get("model", "claude-haiku-5-5"), user=user,
                                     aliases=aliases, lang=lang, explainer=explainer, log=log)
    sent_at = {}
    for i, r in enumerate(rows):
        text, speaker = r["text"], r["speaker"]
        line = f"[{lang.speaker(speaker)}] {text}"
        context = "\n".join(f"[{lang.speaker(x['speaker'])}] {x['text']}" for x in rows[max(0, i - 12):i])
        mark = "*" if text in gold else " "
        print(f"{mark}{time.time() - t0:5.1f}s {line[:90]}", flush=True)
        sent_at[text] = time.time() - t0
        if speaker == "我":
            window.seen(line)
        elif realtime.mentions(text, aliases + (user,)):
            explainer.submit(0, line, context, "")
            window.seen(line)
        elif realtime.looks_like_question(text):
            explainer.submit(1, line, context, "")
            window.add(line, "")
        elif realtime.worth_asking(text):
            window.add(line, "")
        else:
            window.seen(line)
        time.sleep(args.pace)
    time.sleep(40)   # let the last checks and answers finish
    answered = [n for n in notes if n.startswith("\n### ")]
    print(f"\n{len(rows)} lines, {len(answered)} answers written")
    if gold:
        in_range = [g for g in gold if any(r["text"] == g for r in rows)]
        print(f"known questions in range: {len(in_range)} (marked * above); compare with the ANSWER lines")


if __name__ == "__main__":
    main()
