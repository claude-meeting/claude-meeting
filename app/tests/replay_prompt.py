"""Replay a stretch of a finished meeting through the live copilot prompt.

Feeds transcript lines in windows (like the live 30 s ticks) to Claude with the same
PROMPT the autopilot uses, then prints what would be delivered. Speaker tags are
stripped by default to mimic today's live transcript (no speakers).

Usage:
  replay_prompt.py <meeting.md> <start mm:ss> <end mm:ss> [--window 60] [--keep-speakers]
"""
import argparse
import importlib.util
import json
import re
from pathlib import Path

APP = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("autopilot", APP / "bin" / "autopilot.py")
ap = importlib.util.module_from_spec(spec)
spec.loader.exec_module(ap)

LINE = re.compile(r"^\[(?P<spk>[A-Z_0-9]+) (?P<ts>\d+:\d\d(?::\d\d)?)\] (?P<text>.*)$")


def secs(ts):
    parts = [int(p) for p in ts.split(":")]
    return sum(p * 60 ** i for i, p in enumerate(reversed(parts)))


def main():
    p = argparse.ArgumentParser()
    p.add_argument("meeting")
    p.add_argument("start")
    p.add_argument("end")
    p.add_argument("--window", type=int, default=60)
    p.add_argument("--keep-speakers", action="store_true")
    args = p.parse_args()

    body = Path(args.meeting).read_text().split("## Transcript", 1)[1]
    lines = []
    for raw in body.splitlines():
        m = LINE.match(raw.strip())
        if m and secs(args.start) <= secs(m["ts"]) < secs(args.end):
            text = f"[{m['spk']}] {m['text']}" if args.keep_speakers else m["text"]
            lines.append((secs(m["ts"]), m["ts"], text))

    already, old = [], []
    t = secs(args.start)
    while t < secs(args.end):
        new = [l for l in lines if t <= l[0] < t + args.window]
        t += args.window
        if not new:
            continue
        none = ap.LANG.t("none")
        fmt = lambda ls: "\n".join(f"[{ts}] {x}" for _, ts, x in ls) or none
        prompt = ap.PROMPT.format(name=ap.USER_NAME, aliases="、".join(ap.USER_ALIASES),
                                  already="\n".join(f"- {a}" for a in already[-40:]) or none,
                                  context=fmt(old[-30:]), new=fmt(new), screen=ap.LANG.t("screen_unchanged"),
                                  image_note="", known=none)
        out = (ap.ask_model(prompt, timeout=180) or "").strip()
        try:
            result = json.loads(out[out.find("{"): out.rfind("}") + 1])
        except ValueError:
            print(f"--- {new[0][1]}: unparsable output: {out[:200]}")
            continue
        print(f"\n--- {new[0][1]}–{new[-1][1]} ({len(new)} lines)")
        for item in result.get("items") or []:
            tier = "T1" if item.get("type") in ap.TIER1 else "T2" if item.get("type") in ap.TIER2 else "T3"
            print(f"  {tier} {item.get('type')}: {item.get('text')}  [owner: {item.get('owner', '')}]")
            already.append(f"{item.get('type')}: {item.get('text')}")
        for q in result.get("answers") or []:
            print(f"  T1 💡 {q.get('question')}: {q.get('answer')}")
            already.append(f"answered: {q.get('question')}")
        old += new


if __name__ == "__main__":
    main()
