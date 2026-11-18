"""Unit tests for the decision logic that needs no models, audio devices or network.

Run: app/asr-venv/bin/python -m unittest discover -s app/tests -p 'test_*.py'
"""
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path

import numpy as np
from PIL import Image

APP = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(APP / "lib"))
sys.path.insert(0, str(APP / "tools"))
import asr  # noqa: E402
import i18n  # noqa: E402
import minutes_record  # noqa: E402
import realtime  # noqa: E402
import screen  # noqa: E402
import setup_minutes  # noqa: E402
import speakers  # noqa: E402


class FallbackUnavailable(unittest.TestCase):
    """The fallback model is not installed: the live transcriber must not download it mid-meeting."""

    class Model:
        def __init__(self, text):
            self.text = text

        def generate(self, audio, **kw):
            return type("R", (), {"text": self.text})()

    def transcriber(self, primary_text):
        tr = object.__new__(asr.Transcriber)
        tr.family, tr.primary = "en", self.Model(primary_text)
        tr._fallback, tr.fallback_error, tr._fallback_id = None, None, "someone/not-installed"
        return tr

    def test_junk_is_dropped_and_a_short_result_kept(self):
        saved = asr.load_model
        asr.load_model = lambda name: (_ for _ in ()).throw(OSError("offline: not in the cache"))
        try:
            audio = np.zeros(asr.SAMPLE_RATE * 6, dtype=np.float32)
            seg = self.transcriber("哈哈哈哈哈哈哈哈哈哈").transcribe_segment(audio, 0.0, 6.0)
            self.assertEqual((seg.engine, seg.text), ("dropped", ""))
            self.assertIn("fallback unavailable", seg.note)
            tr = self.transcriber("ok then")                      # six seconds, two words: looks truncated
            seg = tr.transcribe_segment(audio, 0.0, 6.0)
            self.assertEqual((seg.engine, seg.text), ("primary", "ok then"))
            self.assertIn("OSError", tr.fallback_error)
            tr.transcribe_segment(audio, 0.0, 6.0)                   # not retried for every segment
            tr = self.transcriber("我們的 Pie Torch 模型跟 py3 環境")
            tr.corrections = (("pie torch", "PyTorch"), ("py3", "Python 3"))
            self.assertEqual(tr.transcribe_segment(audio, 0.0, 3.0).text, "我們的 PyTorch 模型跟 Python 3 環境")
        finally:
            asr.load_model = saved

    def test_corrections(self):
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / "corrections.txt"
            p.write_text("# heard => meant\npie torch => PyTorch\n平果 => 蘋果  # homophone\nno arrow\n => x\n")
            pairs = asr.load_corrections(p)
            self.assertEqual(pairs, (("pie torch", "PyTorch"), ("平果", "蘋果")))
            self.assertEqual(asr.load_corrections(Path(d) / "missing.txt"), ())
        self.assertEqual(asr.correct("a pie torches 平果", pairs), "a pie torches 蘋果")   # ASCII stands on its own
        self.assertEqual(asr.correct("PIE TORCH", pairs), "PyTorch")


class SimplifiedSpeech(unittest.TestCase):
    """Simplified Chinese: the transcript is written in Simplified script, digits as digits, words as they were said."""

    def test_both_models_write_simplified(self):
        import opencc
        audio = np.zeros(asr.SAMPLE_RATE * 2, dtype=np.float32)
        tr = object.__new__(asr.Transcriber)
        tr.family, tr._t2s = "zh-hans", opencc.OpenCC("t2s")
        tr.primary = FallbackUnavailable.Model("那個軟體版本是三點一點四，二零二六年上線")   # a Traditional model set by hand
        tr._fallback, tr.fallback_error, tr._fallback_id = None, None, "x"
        seg = tr.transcribe_segment(audio, 0.0, 2.0)
        self.assertEqual(seg.text, "那个软体版本是三点一点四，2026年上线")   # 软体 stays: the script, not the words
        tr._fallback = FallbackUnavailable.Model("我们下周二上线")
        tr.primary = FallbackUnavailable.Model("哈哈哈哈哈哈哈哈哈哈")
        self.assertEqual(tr.transcribe_segment(audio, 0.0, 2.0).text, "我们下周二上线")


class AsrGuards(unittest.TestCase):
    def test_repetition(self):
        self.assertTrue(asr.is_repetitive("然後然後然後然後然後然後"))
        self.assertFalse(asr.is_repetitive("然後我們週末再去一次海邊"))

    def test_minutes_only_fillers(self):
        import text
        self.assertTrue(text.is_hallucination("好", text.MINUTES_EXACT))
        self.assertFalse(text.is_hallucination("好"))                            # a real one-word answer live
        self.assertTrue(text.is_hallucination("Thank you."))

    def test_hallucination(self):
        self.assertTrue(asr.is_hallucination("Thanks for watching!"))
        self.assertFalse(asr.is_hallucination("you"))
        self.assertTrue(asr.is_hallucination("謝謝觀看"))
        self.assertTrue(asr.is_hallucination("（音樂）"))
        self.assertTrue(asr.is_hallucination("嗯。"))
        self.assertFalse(asr.is_hallucination("這個 API 下週上線"))

    def test_truncated(self):
        self.assertTrue(asr.looks_truncated("那個盆栽呢就是那個", 10.0))
        self.assertFalse(asr.looks_truncated("那個盆栽呢就是那個", 3.0))           # short segments are fine
        self.assertFalse(asr.looks_truncated("把食譜存成 PDF 之後應該就能直接分享出去我記得", 8.0))

    def test_numbers(self):
        self.assertEqual(asr.normalize_numbers("二零二六年"), "2026年")
        self.assertEqual(asr.normalize_numbers("一個一個看"), "一個一個看")   # short runs are words

    def test_echo(self):
        # Measured on recorded calls: own speech >= 6.5x the call level, echo 0.12-0.30x.
        self.assertTrue(asr.is_echo(0.006, 0.03))
        self.assertFalse(asr.is_echo(0.05, 0.0))        # call silent: the user talking
        self.assertFalse(asr.is_echo(0.2, 0.03))        # user talking over the call
        self.assertFalse(asr.is_echo(0.001, 0.001))     # call not active


class LevelGate(unittest.TestCase):
    def test_default_until_enough_samples(self):
        g = asr.SelfLevelGate()
        self.assertEqual(g.threshold(), asr.SelfLevelGate.DEFAULT)

    def test_two_groups_split_between(self):
        g = asr.SelfLevelGate()
        g.levels = [0.05, 0.06, 0.055, 0.07, 0.045] * 3 + [0.02, 0.025, 0.022, 0.03] * 3
        t = g.threshold()
        self.assertTrue(0.03 < t < 0.045, t)

    def test_fixed(self):
        self.assertEqual(asr.SelfLevelGate(fixed=0.01).threshold(), 0.01)


class Realtime(unittest.TestCase):
    def test_mentions(self):
        self.assertTrue(realtime.mentions("Alex 你那邊呢", ("alex", "艾力克斯")))
        self.assertTrue(realtime.mentions("Alex你那邊呢", ("alex",)))          # no space before Chinese
        self.assertTrue(realtime.mentions("請問alex覺得呢", ("alex",)))
        self.assertFalse(realtime.mentions("大家那邊呢", ("alex", "艾力克斯")))
        self.assertFalse(realtime.mentions("I also think so", ("al",)))        # "Al" is not "also"

    def test_question(self):
        self.assertTrue(realtime.looks_like_question("Python 跟 Go 差在哪？"))
        self.assertTrue(realtime.looks_like_question("這個why要這樣做"))
        self.assertFalse(realtime.looks_like_question("我下午會把蛋糕送過去"))
        self.assertFalse(realtime.looks_like_question("somewhat later"))         # "what" inside a word

    def test_worth_asking(self):
        self.assertTrue(realtime.worth_asking("這個你們那邊會不會拖到交貨日"))   # no question word, still sent
        self.assertTrue(realtime.worth_asking("誰？"))                       # short but a question
        self.assertFalse(realtime.worth_asking("嗯嗯。"))

    def test_similar(self):
        self.assertTrue(realtime.similar("幫毛毛蟲買兩張週六的電影票", "幫毛毛蟲買兩張週六電影票"))
        self.assertFalse(realtime.similar("幫毛毛蟲買電影票", "把投影機的線收回抽屜"))
        self.assertFalse(realtime.similar("a", "b"))

    def test_stamp(self):
        self.assertEqual(realtime.stamp("2026-01-01T09:30:05"), "09:30:05")


class Language(unittest.TestCase):
    def test_family(self):
        self.assertEqual(i18n.family("zh-Hant-TW"), "zh")
        self.assertEqual(i18n.family("en-US"), "en")
        self.assertEqual(i18n.family("ja"), "en")          # no translation yet: English
        for code in ("zh-CN", "zh-Hans", "zh-Hans-TW", "zh_SG", "zh-MY"):
            self.assertEqual(i18n.family(code), "zh-hans", code)
        for code in ("zh-TW", "zh-HK", "zh", "zh-Hant-CN"):
            self.assertEqual(i18n.family(code), "zh", code)
        self.assertTrue(i18n.chinese("zh-hans") and i18n.chinese("zh") and not i18n.chinese("en"))

    # Taiwan usage that must not be left in Simplified texts (mainland word in the comment of each line).
    TW_USAGE = ("軟體", "软体", "螢幕", "萤幕", "設定", "设定", "網路", "网路", "程式", "资料夹", "视窗", "登入",
                "帐号", "品质", "支援", "回馈", "预设", "滑鼠", "列印", "搜寻", "伺服器", "影片", "专案", "透过",
                "使用者", "介面", "储存", "讯息", "音讯", "视讯", "资讯", "撷取", "金钥", "选单", "辨识", "终端机",
                "物件", "字串", "阵列", "图示", "台湾", "繁体", "简体字", "「", "」", "接口", "缺省",
                "设置档", "背景程序", "截屏", "讲者", "垃圾桶", "登录项目", "精灵", "前后文", "脉络", "卷动", "侦测",
                "本机", "暱", "拷贝", "安装档", "会议纪录", "视频镜头", "屏幕与系统录音")

    def test_simplified_texts_are_complete_and_mainland(self):
        import string
        tables = i18n.HANS_TABLES

        def texts(value):
            if isinstance(value, str):
                return [value]
            return [t for v in value.values() for t in texts(v)]

        def fields(text):
            return sorted(f for _, f, _, _ in string.Formatter().parse(text) if f)

        for name in tables:
            table = getattr(i18n, name)
            hans, zh = table[i18n.HANS], table["zh"]
            self.assertEqual(type(hans), type(zh), name)
            if isinstance(zh, dict):
                self.assertEqual(set(hans), set(zh), name)
            for h, z in zip(texts(hans), texts(zh)):
                self.assertEqual(fields(h), fields(z), f"{name}: placeholders must survive: {h[:40]}")
                found = [w for w in self.TW_USAGE if w in h]
                self.assertEqual(found, [], f"{name}: {h[:60]}")
        lang = i18n.Lang({"user": {"language": "zh-CN"}})
        self.assertEqual((lang.family, lang.t("answer_to_me"), lang.detail("medium"), lang.speaker("其他人", "A"),
                          lang.speaker("我")), ("zh-hans", "❓ 问我", "两三句", "其他人 A", "我"))
        self.assertIn("简体中文", i18n.URGENT_PROMPT[i18n.HANS])            # the model is told which script to write

    def test_a_name_is_heard_in_either_script(self):
        # A name typed in Traditional, heard in a Simplified transcript (and the other way round).
        import realtime
        self.assertTrue(realtime.mentions("陈伟你那边进度呢", ("陳偉",)))
        self.assertTrue(realtime.mentions("陳偉你那邊進度呢", ("陈伟",)))
        self.assertTrue(realtime.mentions("对象：陈伟，在提问", ("Alex", "陳偉")))
        self.assertFalse(realtime.mentions("陈伟你那边进度呢", ("陳偉明",)))
        self.assertTrue(realtime.mentions("alex 你看呢", ("Alex",)))
        self.assertFalse(realtime.mentions("also", ("Al",)))
        self.assertEqual(i18n.ADDRESSEE[:2], ("對象", "对象"))           # the Simplified word is made, not typed

    def test_the_app_texts_are_written_as_swift_reads_them(self):
        import hans_strings as hs
        for value in ("一般", 'say "hi"', "a\\b", "line\nnext", "tab\there", "bell\x07", "…"):
            literal = hs.swift_literal(value)
            self.assertEqual(hs.swift_unescape(literal[1:-1]), value, literal)
        self.assertEqual(hs.swift_literal("bell\x07"), '"bell\\u{7}"')
        self.assertEqual(hs.swift_unescape("\\u{2026} \\\\ \\\""), "… \\ \"")
        with self.assertRaises(SystemExit):
            hs.swift_unescape("hello \\(name)", "greeting")

    def test_answer_headers_in_simplified(self):
        self.assertEqual(i18n.Lang.kind_of("问我"), "to_me")
        self.assertEqual(i18n.Lang.kind_of("第二行：略过"), "skip")
        self.assertEqual(i18n.Lang.header(["对象：Alex，在提问", "问题", "什么是 gRPC", "一种远程调用协议"]),
                         ("question", "什么是 gRPC", 3, "对象：Alex，在提问"))

    def test_the_apps_simplified_texts_are_up_to_date(self):
        import subprocess
        r = subprocess.run([sys.executable, str(APP / "tools" / "hans_strings.py"), "--check"], capture_output=True,
                           text=True, timeout=60)
        self.assertEqual(r.returncode, 0, r.stderr)

    def test_answer_kinds_in_both_languages(self):
        self.assertEqual(i18n.Lang.kind_of("問我"), "to_me")
        self.assertEqual(i18n.Lang.kind_of("略過"), "skip")
        self.assertEqual(i18n.Lang.kind_of("QUESTION"), "question")
        self.assertEqual(i18n.Lang.kind_of("skip"), "skip")
        self.assertIsNone(i18n.Lang.kind_of("嗯"))
        self.assertEqual(i18n.Lang.kind_of("第一行：略過"), "skip")
        self.assertEqual(i18n.Lang.kind_of("1. 問題"), "question")

    def test_detail_and_speakers(self):
        zh, en = i18n.Lang({"user": {"language": "zh-TW"}}), i18n.Lang({"user": {"language": "en"}})
        self.assertEqual(zh.detail("medium"), "兩三句")
        self.assertEqual(zh.detail("三點條列"), "三點條列")    # the user's own wording passes through
        self.assertEqual(en.detail("short"), "one sentence")
        self.assertEqual(en.speaker("其他人"), "Others")
        self.assertEqual(set(i18n.STRINGS["zh"]), set(i18n.STRINGS["en"]))   # no missing translation


class ScreenDiff(unittest.TestCase):
    """New page vs scroll update vs unchanged, with OCR stubbed."""

    def setUp(self):
        self.dir = tempfile.TemporaryDirectory()
        self.w = screen.ScreenWatcher("unused")
        self.pages = {}
        self.w.ocr = lambda p: self.pages[Path(p).name]
        self.n = 0

    def tearDown(self):
        self.dir.cleanup()

    def frame(self, seed, lines):
        self.n += 1
        path = Path(self.dir.name) / f"frame-{self.n}.png"
        rng = np.random.default_rng(seed)
        Image.fromarray((rng.random((64, 64)) * 255).astype("uint8")).save(path)
        self.pages[path.name] = lines
        return self.w.process(path)

    def test_flow(self):
        a = ["第一行標題", "第二行內容很重要", "第三行結論"]
        self.assertEqual(self.frame(1, a)["kind"], "new_page")
        self.assertIsNone(self.frame(1, a))                                      # same image
        scrolled = a[1:] + ["第四行新的捲動內容出現"]
        ev = self.frame(2, scrolled)
        self.assertEqual(ev["kind"], "update")
        self.assertEqual(ev["text"], "第四行新的捲動內容出現")
        self.assertEqual(self.frame(3, ["完全不同的投影片", "新主題"])["kind"], "new_page")


class Speakers(unittest.TestCase):
    """Live grouping of other participants and remembered people, with fake voiceprints."""

    class FakeVoice:
        def embed(self, audio):
            v = np.asarray(audio[:4], dtype=np.float32)
            return v / np.linalg.norm(v)

    def test_grouping_and_stable_letters(self):
        g = speakers.OnlineSpeakers(self.FakeVoice())
        one, two = np.r_[1, 0, 0, 0, np.zeros(16000)], np.r_[0, 1, 0, 0, np.zeros(16000)]
        labels = [g.label(g.assign(a)) for a in (one, two, one, two)]
        self.assertEqual(labels, ["A", "B", "A", "B"])
        self.assertIsNone(g.assign(np.ones(100)))            # too short to tell
        self.assertEqual(set(g.snapshot()), {"A", "B"})

    def test_people_match_and_learn(self):
        with tempfile.TemporaryDirectory() as d:
            people = speakers.People(Path(d) / "people.json")
            self.assertFalse(people.learn("毛毛蟲", [1, 0, 0, 0], count=2))   # too little speech to learn
            self.assertTrue(people.learn("毛毛蟲", [1, 0, 0, 0], count=6))
            self.assertEqual(speakers.People(Path(d) / "people.json").match([0.95, 0.31, 0, 0])[0], "毛毛蟲")
            self.assertIsNone(people.match([0, 1, 0, 0])[0])

    def test_a_voice_close_to_two_people_is_nobody(self):
        # One speaker label can hold two voices (0.76 to one saved person, 0.67 to another); naming it after
        # the closer one puts every line of the other under the wrong name.
        with tempfile.TemporaryDirectory() as d:
            people = speakers.People(Path(d) / "people.json")
            people.learn("Bella", [1, 0, 0, 0], count=6)
            people.learn("Alice", [0, 1, 0, 0], count=6)
            mixed = np.array([0.76, 0.65, 0, 0]) / np.linalg.norm([0.76, 0.65])
            self.assertIsNone(people.match(mixed)[0])
            clear = np.array([0.9, 0.35, 0, 0]) / np.linalg.norm([0.9, 0.35])
            self.assertEqual(people.match(clear)[0], "Bella")


    def test_a_similar_voice_is_not_the_saved_person(self):
        # A different voice can score up to 0.70 against a saved person, while their own voice scores about 0.84.
        with tempfile.TemporaryDirectory() as d:
            people = speakers.People(Path(d) / "people.json")
            people.learn("Alice", [1, 0, 0, 0], count=6)
            people.learn("Bella", [0, 1, 0, 0], count=6)
            self.assertIsNone(people.match([0.70, 0, 0.714, 0])[0])
            self.assertEqual(people.match([0.84, 0, 0.543, 0])[0], "Alice")

    def test_a_turn_unlike_its_voice_becomes_a_voice_of_its_own(self):
        # A turn can join another voice line by line (each line close enough), though the turn as a whole scores
        # 0.68 against that voice.
        g = speakers.OnlineSpeakers(self.FakeVoice())
        alice, other = np.r_[1, 0, 0, 0, np.zeros(24000)], np.r_[0.6, 0.8, 0, 0, np.zeros(24000)]
        for t in (0, 2):
            self.assertEqual(g.label(g.assign(alice, start=t, end=t + 1.5)), "A")
        self.assertIsNone(g.check_turn())                                   # A's own first turn: nothing before it
        self.assertEqual(g.label(g.assign(other, start=10, end=11.5)), "A")   # 0.6: joins A, line by line
        self.assertIsNone(g.check_turn())                                   # 1.5 s: too little to tell
        self.assertEqual(g.label(g.assign(other, start=12, end=13.5)), "A")
        self.assertEqual(g.check_turn(), ("A", "B", [10, 12]))
        self.assertEqual(g.label(g.current()), "B")
        self.assertEqual(g.snapshot()["A"]["count"], 2)                       # A's voice as before the turn
        self.assertEqual(g.snapshot()["B"]["count"], 2)
        self.assertEqual(g.label(g.assign(other, start=14, end=15.5)), "B")   # the rest of the turn follows
        self.assertIsNone(g.check_turn())
        for t in (30, 32):                                                  # A again: the same voice, kept
            self.assertEqual(g.label(g.assign(alice, start=t, end=t + 1.5)), "A")
        self.assertIsNone(g.check_turn())
        self.assertIsNone(g.check_turn())                                   # one look per turn
        g.assign(alice)                                                     # no times: no turn to follow
        self.assertIsNone(g.current())

    def test_a_turn_that_is_the_saved_voice_of_its_name_is_kept(self):
        with tempfile.TemporaryDirectory() as d:
            t = Path(d) / "rec-1.jsonl"
            people = speakers.People(Path(d) / "people.json")
            people.learn("Alice", [0.6, 0.8, 0, 0], count=6)
            speakers.speakers_path(t).write_text(json.dumps({"A": {"vector": [1, 0, 0, 0], "count": 2}}))
            speakers.names_path(t).write_text(json.dumps({"A": "Alice"}))
            g = speakers.OnlineSpeakers(self.FakeVoice(), same_person=speakers.same_saved_person(t, people))
            alice, again = np.r_[1, 0, 0, 0, np.zeros(24000)], np.r_[0.6, 0.8, 0, 0, np.zeros(24000)]
            for start in (0, 2, 10, 12):
                g.assign(alice if start < 10 else again, start=start, end=start + 1.5)
            self.assertIsNone(g.check_turn())                               # unlike A's lines, but Alice's voice
            speakers.names_path(t).write_text(json.dumps({"A": "Bella"}))
            self.assertFalse(speakers.same_saved_person(t, people)("A", [0.6, 0.8, 0, 0]))

    def test_transcript_corrections(self):
        with tempfile.TemporaryDirectory() as d:
            t = Path(d) / "rec-1.jsonl"
            rows = [{"start": 1.0, "end": 2, "who": "A", "ts": "2026-01-15T09:00:01"},
                    {"start": 3.0, "end": 4, "who": "A", "ts": "2026-01-15T09:00:03"},
                    speakers.relabel_row("A", "B", [3.0]), {"start": 5.0, "end": 6, "who": "B"}]
            t.write_text("\n".join(json.dumps(r) for r in rows) + "\nnot json\n")
            speakers.unsure_path(t).write_text(json.dumps({"starts": [5.0]}))
            got = speakers.read_transcript(t)
            self.assertEqual([r["who"] for r in got], ["A", "B", "B"])
            self.assertEqual([speakers.shown_who(r) for r in got], ["A", "B", None])
            moved = speakers.add_row(got, speakers.relabel_row("B", "C", [3.0]))
            self.assertEqual((moved["to"], moved["ts"]), ("C", "2026-01-15T09:00:03"))
            self.assertEqual(speakers.unsure_starts(Path(d) / "rec-2.jsonl"), set())
            speakers.vectors_path(t).write_text('{"start": 1.0, "v": [1, 0]}\n{"start": 2}\n')
            self.assertEqual(list(speakers.line_vectors(t)), [1.0])
            self.assertEqual(speakers.line_vectors(Path(d) / "rec-2.jsonl"), {})

    def test_a_merged_voice_keeps_its_lines_named(self):
        # A speaker's first lines (A) can be split from their later ones (C). When C is named and A later merges into C,
        # A leaves the voices, and its lines must not be left as 其他人 A in the meeting record.
        g = speakers.OnlineSpeakers(self.FakeVoice())
        first, later = np.r_[1, 0, 0, 0, np.zeros(16000)], np.r_[0.9, 0.436, 0, 0, np.zeros(16000)]
        self.assertEqual(g.label(g.assign(first)), "A")
        self.assertEqual(g.label(g.assign(np.r_[0, 0, 1, 0, np.zeros(16000)])), "B")
        g._new(later[:4], 5)                                              # C: as check_turn() splits a turn off
        self.assertEqual(g.merged(), {})
        self.assertEqual(g.label(g.assign(np.r_[0.95, 0.31, 0, 0, np.zeros(16000)])), "C")   # A and C meet
        self.assertEqual(g.merged(), {"A": "C"})
        self.assertEqual(set(g.snapshot()), {"B", "C"})
        with tempfile.TemporaryDirectory() as d:
            t = Path(d) / "rec-1.jsonl"
            speakers.speakers_path(t).write_text(json.dumps(g.snapshot()))
            speakers.merged_path(t).write_text(json.dumps(g.merged()))
            speakers.names_path(t).write_text(json.dumps({"C": "Alice"}))
            self.assertEqual(speakers.resolve_names(t, None), {"C": "Alice", "A": "Alice"})
            speakers.names_path(t).write_text(json.dumps({"A": "Alice"}))       # named before the merge only
            self.assertEqual(speakers.resolve_names(t, None), {"C": "Alice", "A": "Alice"})
            speakers.withdrawn_path(t).write_text(json.dumps({"C": ["Alice"]}))  # ...but not a name C lost: they
            self.assertEqual(speakers.resolve_names(t, None), {})                  # are one voice
            speakers.merged_path(t).write_text("[]")
            self.assertEqual(speakers.merged_labels(t), {})
            self.assertEqual(speakers.merged_labels(Path(d) / "rec-2.jsonl"), {})
        # The recording restarted mid-call: the next part goes on from the voices and what was merged.
        h = speakers.OnlineSpeakers(self.FakeVoice())
        h.restore(g.snapshot(), g.merged())
        self.assertEqual(h.merged(), {"A": "C"})
        h.restore({}, {"Q": "Z"})                                          # a letter this part does not know
        self.assertEqual(h.merged(), {"A": "C", "Q": "Z"})

    def test_strict_grouping_by_default(self):
        self.assertEqual(speakers.OnlineSpeakers(self.FakeVoice()).threshold, 0.5)

    def test_contradicts_and_one_person_under_two_letters(self):
        with tempfile.TemporaryDirectory() as d:
            people = speakers.People(Path(d) / "people.json")
            people.learn("Alice", [1, 0, 0, 0], count=6)
            self.assertEqual(people.contradicts("Bella", [1, 0, 0, 0]), "Alice")
            self.assertIsNone(people.contradicts("Alice", [1, 0, 0, 0]))
            self.assertIsNone(people.contradicts("Bella", [0, 1, 0, 0]))    # an unknown voice: the screen decides
            t = Path(d) / "rec-1.jsonl"
            speakers.speakers_path(t).write_text(json.dumps({
                "A": {"vector": [1, 0, 0, 0], "count": 9}, "C": {"vector": [0.95, 0.31, 0, 0], "count": 3}}))
            self.assertEqual(speakers.resolve_names(t, people), {"A": "Alice", "C": "Alice"})


    def test_a_voice_far_from_a_saved_person_is_not_them(self):
        # The screen can name a new voice after the last speaker even when it scores only 0.29 against that person's
        # saved voice.
        with tempfile.TemporaryDirectory() as d:
            people = speakers.People(Path(d) / "people.json")
            people.learn("Alice", [1, 0, 0, 0], count=6)
            people.learn("Bella", [0, 1, 0, 0], count=6)
            self.assertFalse(people.not_them("Alice", [0.84, 0, 0.543, 0]))    # Alice's own voice
            self.assertTrue(people.not_them("Alice", [0.29, 0, 0.957, 0]))      # far from Alice's
            self.assertTrue(people.not_them("Alice", [0.55, 0.835, 0, 0]))         # ...or clearly Bella's
            self.assertFalse(people.not_them("Carl", [0, 0, 1, 0]))             # nobody saved: cannot tell
            self.assertFalse(people.not_them("Alice", [1, 0]))                  # another model's vector


class MinutesConfig(unittest.TestCase):
    def test_set_key(self):
        t = setup_minutes.set_key("", "recording", "capture_backend", "core-audio-tap")
        t = setup_minutes.set_key(t, "recording.sources", "call", "auto")
        t = setup_minutes.set_key(t, "recording", "enabled", True)
        self.assertEqual(t, '[recording]\ncapture_backend = "core-audio-tap"\nenabled = true\n\n'
                            '[recording.sources]\ncall = "auto"\n')

    def test_keeps_equal_line_and_comment(self):
        t = '[recording.sources]\nvoice = "外接麥克風"   # pinned\n'
        self.assertEqual(setup_minutes.set_key(t, "recording.sources", "voice", "外接麥克風"), t)
        self.assertIn('voice = "USB"', setup_minutes.set_key(t, "recording.sources", "voice", "USB"))

    def test_apply_then_undo_restores_the_file(self):
        """Keys we wrote go back to their old value or away; the user's own settings stay."""
        config = setup_minutes.config          # the module object setup_minutes itself uses
        original = '[screen_context]\nenabled = false\nkeep_after_summary = false   # user\n\n[transcription]\nlanguage = "ja"\n'
        with tempfile.TemporaryDirectory() as d:
            d = Path(d)
            saved = (setup_minutes.MINUTES_CONFIG, setup_minutes.BACKUP, setup_minutes.MANAGED, config.load)
            setup_minutes.MINUTES_CONFIG = d / "config.toml"
            setup_minutes.BACKUP = d / "config.toml.pre-meeting-copilot"
            setup_minutes.MANAGED = d / "minutes-managed.json"
            config.load = lambda: {"audio": {"mic_device": "USB Mic (48000Hz, 1 ch)"}, "privacy": {"store_voiceprints": True},
                                   "user": {"language": "zh-TW"}}
            try:
                setup_minutes.MINUTES_CONFIG.write_text(original)
                setup_minutes.main()
                text = setup_minutes.MINUTES_CONFIG.read_text()
                self.assertIn('voice = "USB Mic"', text)                       # suffix stripped
                self.assertIn('language = "zh"', text)
                self.assertIn("keep_after_summary = true   # user", text)
                self.assertIn("recording_sidecar = false", text)              # our live transcription instead
                self.assertEqual(setup_minutes.BACKUP.read_text(), original)
                config.load = lambda: {"user": {"language": "en"}, "asr": {"live": False}}
                setup_minutes.main()                                          # mic and language back to default
                text = setup_minutes.MINUTES_CONFIG.read_text()
                self.assertNotIn("voice =", text)
                self.assertNotIn("recording_sidecar", text)                   # no live transcription of ours
                self.assertIn('language = "ja"', text)                        # the user's value, not deleted
                self.assertEqual(setup_minutes.BACKUP.read_text(), original)  # still the pre-install file
                setup_minutes.undo()
                self.assertEqual(setup_minutes.MINUTES_CONFIG.read_text(), original)
                self.assertFalse(setup_minutes.MANAGED.exists())
            finally:
                setup_minutes.MINUTES_CONFIG, setup_minutes.BACKUP, setup_minutes.MANAGED, config.load = saved

    def test_a_recording_of_ours_gets_a_lighter_pass(self):
        config = setup_minutes.config
        with tempfile.TemporaryDirectory() as d:
            d = Path(d)
            saved = (setup_minutes.MINUTES_CONFIG, config.load)
            setup_minutes.MINUTES_CONFIG = d / "config.toml"
            try:
                models = d / "models"
                models.mkdir()
                setup_minutes.MINUTES_CONFIG.write_text(
                    f'[transcription]\nmodel = "large-v3"\nlanguage = "zh"\nmodel_path = "{models}"\n')
                config.load = lambda: {"user": {"language": "zh-TW"}}
                out = d / "state" / "minutes-recording.toml"
                # Issue #1: without ggml-small.bin every job failed; the user's own (installed) model is kept then.
                self.assertEqual(minutes_record.recording_model_missing(setup_minutes.MINUTES_CONFIG), "small")
                self.assertTrue(setup_minutes.recording_config(out))
                self.assertIn('model = "large-v3"', out.read_text())
                (models / "ggml-small.bin").write_bytes(b"x")                 # a download cut short
                self.assertEqual(minutes_record.recording_model_missing(setup_minutes.MINUTES_CONFIG), "small")
                with open(models / "ggml-small.bin", "wb") as f:
                    f.truncate(80_000_000)
                self.assertIsNone(minutes_record.recording_model_missing(setup_minutes.MINUTES_CONFIG))
                self.assertTrue(setup_minutes.recording_config(out))
                text = out.read_text()
                self.assertIn('model = "small"', text)
                self.assertIn('language = "zh"', text)                        # the rest as the user has it
                self.assertIn('[summarization]\nengine = "none"', text)
                self.assertIn('model = "large-v3"', setup_minutes.MINUTES_CONFIG.read_text())   # theirs untouched
                config.load = lambda: {"asr": {"live": False}}              # Minutes' transcript is the record
                out.unlink()
                self.assertFalse(setup_minutes.recording_config(out))
                self.assertFalse(out.exists())
            finally:
                setup_minutes.MINUTES_CONFIG, config.load = saved


class Control(unittest.TestCase):
    def test_status_and_selective_commands(self):
        import control
        with tempfile.TemporaryDirectory() as d:
            d = Path(d)
            saved = (control.STATE, control.STATUS, control.COMMANDS)
            control.STATE, control.STATUS, control.COMMANDS = d, d / "status.json", d / "commands"
            try:
                control.write_status({"recording": False})
                self.assertFalse(json.loads(control.STATUS.read_text())["recording"])
                control.COMMANDS.mkdir()
                (control.COMMANDS / "1-stop.json").write_text('{"cmd": "stop"}')
                (control.COMMANDS / "2-pause_auto.json").write_text('{"cmd": "pause_auto"}')
                (control.COMMANDS / "3-bad.json").write_text('not json')
                only = control.take_commands(only=("pause_auto", "resume_auto"))
                self.assertEqual([c["cmd"] for c in only], ["pause_auto"])
                self.assertTrue((control.COMMANDS / "1-stop.json").exists())      # left for later
                self.assertFalse((control.COMMANDS / "3-bad.json").exists())      # garbage is dropped
                self.assertEqual([c["cmd"] for c in control.take_commands()], ["stop"])
            finally:
                control.STATE, control.STATUS, control.COMMANDS = saved


class ConfigCli(unittest.TestCase):
    def test_set_creates_and_refuses_invalid(self):
        import subprocess
        with tempfile.TemporaryDirectory() as d:
            cfg = Path(d) / "config.toml"
            env = {**os.environ, "MEETING_COPILOT_CONFIG": str(cfg)}
            cli = [sys.executable, str(APP / "tools" / "config_cli.py"), "set"]
            r = subprocess.run([*cli, '{"user.name": "Alex"}'], capture_output=True, text=True, env=env)
            self.assertEqual(r.returncode, 0, r.stderr)
            self.assertIn('name = "Alex"', cfg.read_text())
            cfg.write_text('[user\nname = "x"\n')                               # broken by hand
            r = subprocess.run([*cli, '{"user.name": "Bob"}'], capture_output=True, text=True, env=env)
            self.assertNotEqual(r.returncode, 0)
            self.assertEqual(cfg.read_text(), '[user\nname = "x"\n')            # nothing written


class TomlEdit(unittest.TestCase):
    """Settings edits on the real example config: headers with comments, multi-line arrays."""

    EXAMPLE = (APP / "config.example.toml").read_text()

    def test_every_settings_key_keeps_the_file_valid(self):
        import tomllib
        import tomledit
        text = self.EXAMPLE
        changes = {"user.name": "Alex", "user.aliases": ["alex", "艾力克斯"], "user.language": "en",
                   "autopilot.auto_record": False, "audio.mic_device": "USB Mic", "explain.enabled": True,
                   "explain.detail": "short", "explain.web_verify": False, "explain.answer_model": "claude-haiku-5-5",
                   "copilot.open_viewer": False,
                   "screen.enabled": True, "screen.target": "main", "screen.send_images": False,
                   "notify.types": ["heard_name", "my_todo"], "retention.audio_days": 7, "retention.voiceprints_days": 0}
        for dotted, value in changes.items():
            section, key = dotted.rsplit(".", 1)
            text = tomledit.set_key(text, section, key, value)
        parsed = tomllib.loads(text)
        for dotted, value in changes.items():
            section, key = dotted.rsplit(".", 1)
            self.assertEqual(parsed[section][key], value, dotted)
        self.assertEqual(text.count("[explain]"), 1)               # no duplicated sections
        self.assertNotIn('"decision", "deadline"', text)             # the old multi-line array is gone
        self.assertIn("# days; 0 = keep forever", text)              # comments on headers survive

    def test_del_key(self):
        import tomledit
        text = '[a]\nx = 1   # keep\ny = [\n  "p",\n  "q",\n]\n\n[b]\nz = 2\n'
        self.assertEqual(tomledit.del_key(text, "a", "y"), '[a]\nx = 1   # keep\n\n[b]\nz = 2\n')
        self.assertEqual(tomledit.del_key(text, "a", "missing"), text)
        self.assertEqual(tomledit.get_key(text, "a", "y"), ["p", "q"])

    def test_strings_with_hash_and_brackets(self):
        import tomllib
        import tomledit
        text = '[user]\naliases = ["C#", "alex"]\nlanguage = "en"\nname = "Sam [PM"\n'
        out = tomledit.set_key(text, "user", "aliases", ["x"])
        self.assertEqual(tomllib.loads(out)["user"], {"aliases": ["x"], "language": "en", "name": "Sam [PM"})
        out = tomledit.set_key(text, "user", "name", "Bob")
        self.assertEqual(tomllib.loads(out)["user"]["aliases"], ["C#", "alex"])
        self.assertEqual(tomledit.set_key('x = 1  # "hi" [b]\n', "", "x", 1), 'x = 1  # "hi" [b]\n')

    def test_dotted_sections_headers_and_values(self):
        import tomledit
        text = '[ recording . sources ]   # pinned\ncall = "auto"   # "auto" follows the app\n'
        self.assertEqual(tomledit.get_key(text, "recording.sources", "call"), "auto")
        same = tomledit.set_key(text, "recording.sources", "call", "auto")
        self.assertEqual(same, text)                                        # header found, comment kept
        self.assertEqual(tomledit.set_key("a = 1\r\n[b]\r\nc = 2\r\n", "b", "c", 3), "a = 1\r\n[b]\r\nc = 3\r\n")
        self.assertIn('t = { a = 1, b = "x" }', tomledit.set_key("", "s", "t", {"a": 1, "b": "x"}))
        text = '[a]\nx = 1\n\n[b]\n# nothing left\n\n[c]\ny = 2\n'
        self.assertEqual(tomledit.del_empty_section(text, "b"), '[a]\nx = 1\n\n[c]\ny = 2\n')
        self.assertEqual(tomledit.del_empty_section(text, "a"), text)
        with self.assertRaises(ValueError):
            tomledit.toml_value(None)


class Retention(unittest.TestCase):
    def test_dry_run_selects_only_expired(self):
        import importlib.util
        with tempfile.TemporaryDirectory() as d:
            d = Path(d)
            meetings = d / "meetings"
            (meetings / "live").mkdir(parents=True)
            old, new = meetings / "old.wav", meetings / "new.wav"
            old.write_bytes(b"x")
            new.write_bytes(b"x")
            os.utime(old, (0, 0))
            cfg = d / "config.toml"
            cfg.write_text(f'[paths]\nmeetings_dir = "{meetings}"\n[retention]\naudio_days = 30\n'
                           'transcripts_days = 0\nvoiceprints_days = 0\nscreenshots_days = 0\n'
                           'video_days = 0\nlive_notes_days = 0\n')
            os.environ["MEETING_COPILOT_CONFIG"] = str(cfg)
            import config
            previous = config.USER_CONFIG
            try:
                config.USER_CONFIG = cfg
                spec = importlib.util.spec_from_file_location("retention", APP / "bin" / "retention.py")
                retention = importlib.util.module_from_spec(spec)
                spec.loader.exec_module(retention)
                self.assertEqual(retention.main(dry_run=True), 1)
                self.assertTrue(old.exists())
            finally:
                os.environ.pop("MEETING_COPILOT_CONFIG")
                config.USER_CONFIG = previous


if __name__ == "__main__":
    unittest.main()
