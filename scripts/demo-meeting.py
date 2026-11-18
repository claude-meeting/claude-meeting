#!/usr/bin/env python3
"""Play a made-up meeting into the Mac app, for README screenshots and the demo video.

Nothing here comes from a real meeting: the people, the show and every line are invented. The app is
pointed at a stand-in engine folder (MEETING_COPILOT_ENGINE) that only looks installed; this script writes
the status, live notes and notifications the real engine would, at the pace of a meeting.

  scripts/demo-meeting.py ~/demo --lang zh-TW        # or zh-CN, en; --speed 3 plays three times faster
  MEETING_COPILOT_ENGINE=~/demo/app /Applications/claude-meeting.app/Contents/MacOS/MeetingCopilot --show-notes

Quit claude-meeting first: a second copy of the same version hands over to the running one and quits, which
would show the real engine instead. The labels below are the engine's own (app/lib/i18n.py); a test keeps them
the same.
"""
import argparse
import json
import os
import re
import uuid
import time
from datetime import datetime, timedelta
from pathlib import Path

# The engine's texts this demo writes, copied from app/lib/i18n.py (test_demo_meeting checks they still match).
STR = {
    "zh": {"notes_title": "# {title}（即時筆記）\n\n開始：{start}\n\n", "item_decision": "📌 決策",
           "item_deadline": "⏰ 期限", "item_my_todo": "✅ 我的待辦", "item_other_todo": "✅ 待辦", "item_risk": "⚠️ 風險",
           "answer_heading": "💡", "answer_to_me": "❓ 問我", "speaker_named": "👤 {who} 是 {name}",
           "colon": "：", "title_sep": "｜"},
    "zh-hans": {"notes_title": "# {title}（实时笔记）\n\n开始：{start}\n\n", "item_decision": "📌 决策",
                "item_deadline": "⏰ 期限", "item_my_todo": "✅ 我的待办", "item_other_todo": "✅ 待办", "item_risk": "⚠️ 风险",
                "answer_heading": "💡", "answer_to_me": "❓ 问我", "speaker_named": "👤 {who} 是 {name}",
                "colon": "：", "title_sep": "｜"},
    "en": {"notes_title": "# {title} (live notes)\n\nStarted: {start}\n\n", "item_decision": "📌 Decision",
           "item_deadline": "⏰ Deadline", "item_my_todo": "✅ My to-do", "item_other_todo": "✅ To-do", "item_risk": "⚠️ Risk",
           "answer_heading": "💡", "answer_to_me": "❓ Asked me", "speaker_named": "👤 {who} is {name}",
           "colon": ": ", "title_sep": " · "},
}
VERIFY = {"zh": "🔎 查證補充", "zh-hans": "🔎 查证补充", "en": "🔎 Checked"}

# (seconds after the previous event, what, text). "line": transcript "who｜words"; "items": note items;
# "ask": a streamed instant answer (question, answer); "check": the web check under it; "name": a speaker named.
ZH = {
    "title": "Podcast 新一集製作會議",
    "me": "我", "others": "其他人",
    "events": [
        (1, "line", "其他人 A｜好，我們開始吧，今天來排下一集 podcast 的製作進度"),
        (3, "line", "我｜在，耳機收音正常"),
        (4, "line", "其他人 A｜上週錄的那段訪談，音檔整理得怎麼樣？"),
        (4, "line", "其他人 B｜雜音都清掉了，只剩來賓那邊有一點回音"),
        (3, "name", "其他人 B｜Rowan"),
        (4, "line", "其他人 A｜那粗剪可以在週二給來賓聽嗎？"),
        (4, "line", "Rowan｜可以，不過片頭音樂的授權還沒回信"),
        (4, "line", "其他人 A｜這集音量我想統一壓在 -16 LUFS 左右"),
        (2, "ask", ("什麼是 LUFS",
                    "響度單位：依人耳感受量測整段音訊的平均音量，數字越接近 0 越大聲。"
                    "各平台會把節目拉到相近的響度，換下一集時音量才不會忽大忽小。")),
        (3, "check", "Apple Podcasts 建議節目響度約 -16 LUFS（±1 dB）；Spotify 播放時預設把音量正規化到約 -14 LUFS。"),
        (3, "line", "其他人 A｜混音你那邊週五前來得及交嗎？下週一早上要上架"),
        (2, "ask_me", ("混音週五前來得及交嗎",
                       "可以回覆：來得及。週四先把對白和配樂混好，週五中午前交完成檔；"
                       "若片頭音樂授權還沒下來，先用免版稅的備用曲。")),
        (5, "line", "我｜來得及，我週五中午前交，片頭先放備用曲"),
        (3, "line", "其他人 A｜好，那就下週一早上八點上架，週二先給來賓聽粗剪"),
        (3, "items", [("item_decision", "下週一早上八點上架，片頭授權沒下來就先用備用曲"),
                      ("item_deadline", "週二前把粗剪給來賓聽"),
                      ("item_my_todo", "週五中午前交混音完成檔，響度壓在 -16 LUFS"),
                      ("item_other_todo", "Rowan：追片頭音樂的授權回信"),
                      ("item_risk", "來賓若要求刪改段落，上架可能延到週三")]),
        (4, "line", "Rowan｜授權那邊我下午再寄信催一次"),
        (3, "line", "其他人 A｜那今天先這樣，辛苦了"),
    ],
}
ZH_HANS = {  # ZH in Simplified Chinese: from i18n.to_hans, then edited by hand for mainland usage (音频, 标准化...)
    "title": "Podcast 新一集制作会议",
    "me": "我", "others": "其他人",
    "events": [
        (1, "line", "其他人 A｜好，我们开始吧，今天来排下一集 podcast 的制作进度"),
        (3, "line", "我｜在，耳机麦克风正常"),
        (4, "line", "其他人 A｜上周录的那段访谈，音频整理得怎么样？"),
        (4, "line", "其他人 B｜杂音都清掉了，只剩嘉宾那边有一点回音"),
        (3, "name", "其他人 B｜Rowan"),
        (4, "line", "其他人 A｜那粗剪可以在周二给嘉宾听吗？"),
        (4, "line", "Rowan｜可以，不过片头音乐的授权还没回信"),
        (4, "line", "其他人 A｜这集音量我想统一压在 -16 LUFS 左右"),
        (2, "ask", ("什么是 LUFS",
                    "响度单位：按人耳感受测量整段音频的平均音量，数字越接近 0 越大声。各平台会把节目拉到相近的响度，换下一集时音量才不会忽大忽小。")),
        (3, "check", "Apple Podcasts 建议节目响度约 -16 LUFS（±1 dB）；Spotify 播放时默认把音量标准化到约 -14 LUFS。"),
        (3, "line", "其他人 A｜混音你那边周五前来得及交吗？下周一早上要上架"),
        (2, "ask_me", ("混音周五前来得及交吗",
                    "可以回复：来得及。周四先把对白和配乐混好，周五中午前交成品文件；若片头音乐授权还没下来，先用免版税的备用曲。")),
        (5, "line", "我｜来得及，我周五中午前交，片头先放备用曲"),
        (3, "line", "其他人 A｜好，那就下周一早上八点上架，周二先给嘉宾听粗剪"),
        (3, "items", [
            ("item_decision", "下周一早上八点上架，片头授权没下来就先用备用曲"),
            ("item_deadline", "周二前把粗剪给嘉宾听"),
            ("item_my_todo", "周五中午前交混音成品文件，响度压在 -16 LUFS"),
            ("item_other_todo", "Rowan：追片头音乐的授权回信"),
            ("item_risk", "嘉宾若要求删改段落，上架可能延到周三"),
        ]),
        (4, "line", "Rowan｜授权那边我下午再发邮件催一次"),
        (3, "line", "其他人 A｜那今天先这样，辛苦了"),
    ],
}
EN = {
    "title": "Podcast episode planning",
    "me": "Me", "others": "Others",
    "events": [
        (1, "line", "Others A｜OK, let's start. Today we're scheduling the next podcast episode"),
        (3, "line", "Me｜Sure, my headset is on"),
        (4, "line", "Others A｜How's the audio from last week's interview coming along?"),
        (4, "line", "Others B｜The hum is gone; there's just a bit of echo on the guest's side"),
        (3, "name", "Others B｜Rowan"),
        (4, "line", "Others A｜Can the guest hear a rough cut on Tuesday?"),
        (4, "line", "Rowan｜Yes, but the intro music licence still hasn't come back"),
        (4, "line", "Others A｜I'd like this episode to sit around -16 LUFS overall"),
        (2, "ask", ("What is LUFS",
                    "A loudness unit: it measures the average level of a whole track the way ears hear it, and the "
                    "closer to 0, the louder. Platforms bring shows to a similar loudness, so the volume doesn't "
                    "jump from one episode to the next.")),
        (3, "check", "Apple Podcasts recommends about -16 LUFS (±1 dB); Spotify normalises playback to about -14 LUFS by default."),
        (3, "line", "Others A｜Can you get the mix done by Friday? It goes live Monday morning"),
        (2, "ask_me", ("Can the mix be delivered by Friday",
                       "You could say: yes. Mix the dialogue and music on Thursday and deliver the final file by "
                       "Friday noon; if the intro licence hasn't arrived, use the royalty-free backup track.")),
        (5, "line", "Me｜Yes, I'll deliver it by Friday noon, with the backup intro for now"),
        (3, "line", "Others A｜Good: it goes live 8 a.m. Monday, and the guest hears the rough cut Tuesday"),
        (3, "items", [("item_decision", "publish at 8 a.m. next Monday; use the backup intro if the licence isn't in"),
                      ("item_deadline", "rough cut to the guest by Tuesday"),
                      ("item_my_todo", "deliver the final mix by Friday noon, at about -16 LUFS"),
                      ("item_other_todo", "Rowan: chase the intro music licence"),
                      ("item_risk", "if the guest asks for cuts, the episode may slip to Wednesday")]),
        (4, "line", "Rowan｜I'll email them about the licence again this afternoon"),
        (3, "line", "Others A｜That's all for today, thanks everyone"),
    ],
}


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("home", type=Path, help="folder for the stand-in engine and the demo meetings")
    ap.add_argument("--lang", choices=["zh-TW", "zh-CN", "en"], default="zh-TW")
    ap.add_argument("--speed", type=float, default=1.0)
    ap.add_argument("--hold", type=float, default=600, help="seconds to keep the meeting 'recording' at the end")
    a = ap.parse_args()
    family = {"zh-TW": "zh", "zh-CN": "zh-hans", "en": "en"}[a.lang]
    demo = {"zh": ZH, "zh-hans": ZH_HANS, "en": EN}[family]
    zh = family != "en"
    t = STR[family]

    app = a.home.expanduser().resolve() / "app"
    state, live = app / "state", a.home.expanduser().resolve() / "meetings" / "live"
    for d in (app / "asr-venv/bin", app / "models/speaker", state / "notifications", live):
        d.mkdir(parents=True, exist_ok=True)
    py = app / "asr-venv/bin/python"          # what makes the app treat the folder as an installed engine
    py.write_text("#!/bin/sh\nexit 0\n")
    py.chmod(0o755)
    (app / "models/speaker/demo.onnx").write_text("stand-in")

    start = datetime.now().replace(second=0, microsecond=0)
    safe = re.sub(r"[^\w.-]+", "-", demo["title"]).strip(".-") or "meeting"   # the engine's file name for a title
    notes = live / f"{start:%Y-%m-%d-%H%M}-{safe}.md"
    clock = start
    notes.write_text(t["notes_title"].format(title=demo["title"], start=f"{start:%Y-%m-%d %H:%M}") + f"## {start:%H:%M}\n")
    current = live / "current.md"
    if current.is_symlink() or current.exists():
        current.unlink()
    current.symlink_to(notes.name)
    started = time.time()

    def status(recording=True):
        s = {"recording": recording, "title": demo["title"], "started_at": started, "auto": True,
             "notes": str(notes) if recording else "", "meetings_dir": str(live.parent),
             "language": family, "open_viewer": True, "problems": [], "updated": time.time()}
        tmp = state / "status.json.tmp"
        tmp.write_text(json.dumps(s, ensure_ascii=False))
        os.replace(tmp, state / "status.json")

    def notify(title, text, sound="Glass"):  # as control.notify_app: the app never sees a half-written file
        name = f"{int(time.time() * 1000)}-{uuid.uuid4().hex[:8]}.json"
        tmp = state / "notifications" / f".{name}"
        tmp.write_text(json.dumps({"title": title, "text": text, "sound": sound, "open": ""}, ensure_ascii=False))
        os.replace(tmp, state / "notifications" / name)

    def write(text):
        with notes.open("a") as f:
            f.write(text)

    def wait(seconds):
        end = time.time() + seconds / a.speed
        while time.time() < end:
            status()
            time.sleep(min(1, max(0.05, end - time.time())))

    status()
    for delay, kind, payload in demo["events"]:
        wait(delay)
        clock += timedelta(seconds=round(delay * 2.5))
        if kind == "line":
            write(f"{clock:%H:%M:%S} {payload}\n")
        elif kind == "name":
            who, name = payload.split("｜")
            write(t["speaker_named"].format(who=who, name=name) + "\n")
        elif kind in ("ask", "ask_me"):
            question, answer = payload
            label = t["answer_to_me"] if kind == "ask_me" else t["answer_heading"]
            write(f"\n### {label} {question}\n")
            chunks = [answer[i:i + 6] for i in range(0, len(answer), 6)] if zh else [w + " " for w in answer.split(" ")]
            for chunk in chunks:                     # streamed, a few characters at a time as Claude writes it
                write(chunk)
                wait(0.12)
            write("\n\n")
            notify(f"{label} {question}", answer)
        elif kind == "check":
            label = VERIFY[family]
            write(f"  {label}（{question[:20]}）：{payload}\n" if zh else f"  {label} ({question[:20]}): {payload}\n")
            notify(f"{label}：{question[:30]}" if zh else f"{label}: {question[:30]}", payload)   # as realtime._verify
        elif kind == "items":
            write(f"\n## {clock:%H:%M}\n\n" + "\n".join(f"- {t[k]}{t['colon']}{text}" for k, text in payload) + "\n\n")
            for k, text in payload:                  # as the engine: every item, Ping for the ones about you
                notify(f"{t[k]}{t['title_sep']}{demo['title']}", text, "Ping" if k == "item_my_todo" else "Glass")
    wait(a.hold)
    status(recording=False)


if __name__ == "__main__":
    main()
