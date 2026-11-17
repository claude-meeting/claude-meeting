"""Text filters shared by the live transcriber and the autopilot (which must not import MLX).

Speech recognizers produce stock phrases on silence or noise ("thanks for watching", "字幕由...提供");
such a line is not meeting content and is dropped before it reaches the notes or Claude.
"""
# Short lines containing one of these are hallucinations (subtitle credits, channel sign-offs).
HALLUCINATION_MARKERS = ("字幕", "訂閱", "订阅", "謝謝觀看", "谢谢观看", "感謝收看", "感谢收看", "請不吝", "请不吝",
                         "點贊", "点赞", "amara", "生日快樂", "生日快乐",
                         "thanks for watching", "thank you for watching", "please subscribe", "subtitles by")
# Whole lines that are nothing but a filler or a sign-off (compared after trailing punctuation is removed).
HALLUCINATION_EXACT = {"謝謝", "谢谢", "嗯", "謝謝大家", "谢谢大家", "thank you"}
# Minutes' Whisper transcript (the fallback source) also invents single words on silence.
MINUTES_EXACT = HALLUCINATION_EXACT | {"好", "你", "對", "对"}


def is_hallucination(text, exact=HALLUCINATION_EXACT):
    t = text.strip().strip("。.,，!！?？ ").lower()
    if not t or t in exact:
        return True
    if (t.startswith("(") and t.endswith(")")) or (t.startswith("（") and t.endswith("）")):
        return True   # e.g. "(字幕製作:貝爾)", "(記者: 請留意)"
    return len(t) < 30 and any(m in t for m in HALLUCINATION_MARKERS)
