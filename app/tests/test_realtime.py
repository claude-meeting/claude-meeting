"""Unit tests for app/lib/realtime.py: question detection, the batch question window and the instant
explainer, run against the fake `claude` in tests/fakes/bin."""
import os
import sys
import threading
import time
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from harness import FAKES_BIN, FakeOpenAI, Sandbox  # noqa: E402


class Collectors:
    """write/notify/log/remember targets for the explainer."""

    def __init__(self):
        self.written, self.notified, self.logged, self.remembered = [], [], [], []
        self.lock = threading.Lock()

    def write(self, text):
        self.written.append(text)

    def notify(self, title, text, **kw):
        self.notified.append((title, text, kw))

    def log(self, msg):
        self.logged.append(msg)

    def remember(self, entry):
        self.remembered.append(entry)

    @property
    def notes(self):
        return "".join(self.written)


class Helpers(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.sb = Sandbox()
        cls.rt = cls.sb.import_module("realtime")

    @classmethod
    def tearDownClass(cls):
        cls.sb.cleanup()

    def test_question_detection(self):
        rt = self.rt
        for text in ("這要怎麼做", "為什麼會這樣", "what's the difference", "A vs B", "有沒有人知道", "可以嗎？"):
            self.assertTrue(rt.looks_like_question(text), text)
        for text in ("我下午寄出去", "somewhat later", "whoever did it", "好的沒問題"):
            self.assertFalse(rt.looks_like_question(text), text)

    def test_worth_asking_and_similar(self):
        rt = self.rt
        self.assertTrue(rt.worth_asking("這個你們那邊會不會拖到交貨日"))
        self.assertFalse(rt.worth_asking("嗯"))
        self.assertTrue(rt.similar("排程功能下週二上線", "排程功能下週二要上線"))
        self.assertFalse(rt.similar("排程功能下週二上線", "請假單系統改版"))
        self.assertTrue(rt.similar("a", "a"))
        self.assertFalse(rt.similar("a", "b"))

    def test_filler_only(self):
        rt = self.rt
        for text in ("對", "好。", "呃", "對對對", "喔對啊", "對然後", "那個", "是的。", "ok ok", "OK.", "Yeah, um", "哦好好"):
            self.assertTrue(rt.filler_only(text), text)
        for text in ("", "。", "就這樣", "聽得到嗎", "以上", "在樓上", "we did", "哦，下雨", "好天氣", "ok然後吃飯", "okra"):
            self.assertFalse(rt.filler_only(text), text)

    def test_answer_header(self):
        header = self.rt.i18n.Lang.header
        everyone, alex = "對象：大家，在說明進度", "對象：Alex，在提問"
        self.assertIsNone(header([everyone]))                                      # the kind comes next
        self.assertEqual(header([everyone, "略過"]), ("skip", "", 2, everyone))
        self.assertIsNone(header([alex, "問我"]))                                  # the question comes next
        self.assertEqual(header([alex, "問我", "問進度"]), ("to_me", "問進度", 3, alex))
        self.assertEqual(header(["Addressee: everyone", "QUESTION", "why"]), ("question", "why", 3, "Addressee: everyone"))
        self.assertEqual(header(["問題", "什麼是 gRPC"]), ("question", "什麼是 gRPC", 2, ""))   # kind first still reads
        self.assertEqual(header(["略過"]), ("skip", "", 1, ""))
        self.assertEqual(header(["什麼是 gRPC", "答案"]), ("skip", "", 1, ""))                # no kind: off the format
        english = 'Quinn wonders aloud "晚餐要煮什麼" and names nobody, not even Alex. Verdict: 略過'
        self.assertEqual(header([english, "Quinn 在想晚餐，沒人問 Alex，略過。"]), ("skip", "", 1, ""))
        self.assertEqual(header([alex, "2. 問我", "問進度"]), ("to_me", "問進度", 3, alex))

    def test_stamp_and_known_terms(self):
        rt = self.rt
        self.assertEqual(rt.stamp("2001-02-03T04:05:06"), "04:05:06")
        self.assertRegex(rt.stamp("bad"), r"^\d\d:\d\d:\d\d$")
        p = self.sb.root / "known.txt"
        p.write_text("# comment\nKubernetes\n\n  gRPC  \n")
        self.assertEqual(rt.load_known_terms(p), ["Kubernetes", "gRPC"])
        self.assertEqual(rt.load_known_terms(self.sb.root / "missing.txt"), [])

    def test_sandbox_dir_is_created_under_home(self):
        path = Path(self.rt.sandbox())
        self.assertTrue(path.is_dir())
        self.assertTrue(str(path).startswith(str(self.sb.home)))


class FakeExplainer:
    def __init__(self):
        self.submitted = []

    def submit(self, priority, line, context, screen, judged=False):
        self.submitted.append((priority, line, context, screen, judged))


class Window(unittest.TestCase):
    def setUp(self):
        self.sb = Sandbox()
        self.rt = self.sb.import_module("realtime")
        import i18n
        self.lang = i18n.Lang({"user": {"language": "zh-TW"}})
        self.explainer = FakeExplainer()
        self.logs = []
        self.window = self.rt.QuestionWindow(llm=self.rt.llm.ClaudeCLI(str(FAKES_BIN / "claude")), model="fake",
                                             user="Alex", aliases=("alex",), lang=self.lang,
                                             explainer=self.explainer, log=self.logs.append)

    def tearDown(self):
        self.sb.cleanup()

    def test_a_check_by_another_service(self):
        fake = FakeOpenAI(reply="1 Q\n")
        try:
            self.window.llm, self.window.model = self.rt.llm.OpenAICompatible(fake.url, model="small"), "small"
            self.window._check([("[其他人 A] 這個要怎麼接", "[我] 前文", "")])
            self.assertEqual([s[:2] for s in self.explainer.submitted], [(1, "[其他人 A] 這個要怎麼接")])
            self.assertEqual(fake.requests[0]["body"]["model"], "small")
            fake.status = 500
            self.window._check([("[其他人 A] 那這個呢", "", "")])
            self.assertTrue(any("the model failed (500)" in m for m in self.logs), self.logs)
        finally:
            fake.close()

    def test_check_submits_marked_lines_only(self):
        self.sb.flag("window-reply", text="2 M\n1 q\nnonsense\n9 Q\n0 M\n")
        batch = [("[其他人 A] 第一句", "ctx", ""), ("[其他人 B] Alex 第二句", "ctx", "screen")]
        self.window._check(batch)
        self.assertEqual([(p, l, j) for p, l, _, _, j in self.explainer.submitted],
                         [(0, "[其他人 B] Alex 第二句", True), (1, "[其他人 A] 第一句", True)])
        self.assertTrue(any("2 marked" in m for m in self.logs), self.logs)
        prompt = self.sb.claude_calls()[-1]["prompt"]
        self.assertIn("1 [其他人 A] 第一句", prompt)
        self.assertIn("alex", prompt)

    def test_check_logs_a_failed_claude(self):
        self.sb.flag("claude-fail")
        self.window._check([("x", "", "")])
        self.assertEqual(self.explainer.submitted, [])
        self.assertTrue(any("the model failed" in m for m in self.logs), self.logs)

    def test_batching_loop_flushes_after_the_window(self):
        self.rt.WINDOW_SECS = 0.3
        self.sb.flag("window-reply", text="1 Q\n")
        self.window.seen("[我] 前文")
        self.window.add("[其他人 A] 這是什麼", "")
        self.assertTrue(self.sb.wait_for(lambda: self.explainer.submitted, timeout=5))
        self.assertEqual(self.explainer.submitted[0][1], "[其他人 A] 這是什麼")
        self.assertIn("[我] 前文", self.explainer.submitted[0][2])       # earlier lines travel as context
        self.window.reset()
        self.assertEqual((self.window.pending, list(self.window.recent)), ([], []))


class Explainer(unittest.TestCase):
    def setUp(self):
        self.sb = Sandbox()
        self.rt = self.sb.import_module("realtime")
        import i18n
        self.lang = i18n.Lang({"user": {"language": "zh-TW"}})
        self.c = Collectors()
        self.lock = threading.Lock()

    def tearDown(self):
        self.sb.cleanup()

    def explainer(self, **kw):
        args = dict(llm=self.rt.llm.ClaudeCLI(str(FAKES_BIN / "claude")), model="fake", verify_model="fake-big",
                    user="Alex", aliases=("alex",), detail="medium", known_terms=["gRPC"], web_verify=False, max_per_minute=2,
                    write=self.c.write, notify=self.c.notify, log=self.c.log, remember=self.c.remember,
                    stream_lock=self.lock, lang=self.lang)
        args.update(kw)
        return self.rt.UrgentExplainer(**args)

    def test_submit_dedupes_and_orders_by_priority(self):
        ex = self.explainer()
        ex.q = type("Q", (), {"items": [], "put": lambda s, x: s.items.append(x)})()   # no worker drains it
        ex.submit(1, "line a", "", "")
        ex.submit(0, "line b", "", "")
        ex.answered.add("line a")
        ex.submit(1, "line a", "", "")                                     # already answered: ignored
        self.assertEqual([(i[0], i[2]) for i in ex.q.items], [(1, "line a"), (0, "line b")])
        self.assertEqual(ex.q.items[0][-1], ex.generation)

    def test_claim_and_rate_limit(self):
        ex = self.explainer()
        self.assertTrue(ex._claim("q1"))
        self.assertFalse(ex._claim("q1"))
        self.assertTrue(ex._count_question())
        self.assertTrue(ex._count_question())
        self.assertFalse(ex._count_question())                            # max_per_minute = 2
        ex.reset()
        self.assertTrue(ex._claim("q1"))                                    # a new meeting may ask again
        self.assertTrue(ex._count_question())

    def test_answer_streams_into_the_notes(self):
        ex = self.explainer()
        ex._answer(1, "[其他人 B] 什麼是 WebSocket", "ctx", "", time.time(), judged=False, gen=ex.generation)
        notes = self.c.notes
        self.assertIn("### 💡", notes)
        self.assertIn("這是假的回答第一句", notes)
        self.assertNotIn("對象", notes)                                     # whom it was said to stays out
        self.assertTrue(notes.endswith("\n"))
        self.assertEqual(len(self.c.notified), 2, self.c.notified)         # "generating" then the answer
        self.assertTrue(self.c.remembered[0].startswith("answered: "))
        self.assertTrue(any("explain: question" in m for m in self.c.logged), self.c.logged)
        prompt = self.sb.claude_calls()[-1]["prompt"]
        self.assertIn("gRPC", prompt)                                       # known terms reach the prompt
        self.assertIn("什麼是 WebSocket", prompt)

    def test_to_me_label_and_skip(self):
        ex = self.explainer()
        self.sb.flag("urgent-kind", text="問我")
        ex._answer(0, "[其他人 A] Alex 你覺得呢", "", "", time.time(), gen=ex.generation)
        self.assertIn(self.lang.t("answer_to_me"), self.c.notes)
        self.c.written.clear()
        self.sb.flag("urgent-addressee", text="Bella")         # "問我" for a line said to someone else
        ex._answer(0, "[其他人 A] Bella 你覺得呢", "", "", time.time(), gen=ex.generation)
        self.assertNotIn(self.lang.t("answer_to_me"), self.c.notes)
        self.assertIn("### 💡", self.c.notes)
        self.sb.flag("urgent-addressee", False)
        self.c.written.clear()
        self.sb.flag("urgent-kind", text="略過")
        ex._answer(1, "[其他人 A] 好的", "", "", time.time(), gen=ex.generation)
        self.assertEqual(self.c.notes, "")
        self.assertTrue(any("skipped" in m for m in self.c.logged))

    def test_the_one_who_spoke_last_is_named_in_the_prompt(self):
        # "你" in a question right after someone's update is mostly said to that person, not to the user: the prompt
        # names who spoke last.
        rt = self.rt
        context = "[Bella] 我把花圃的土翻好了\n[Bella] 明天開始播種\n[Carl] 好"
        self.assertEqual(rt.previous_speaker(context, "[Carl] 那你打算種哪幾種"), "Bella")
        self.assertEqual(rt.previous_speaker("[我] 我做完了", "[Carl] 你那邊呢"), "我")
        self.assertIsNone(rt.previous_speaker("[Carl] 第一句\nno tag", "[Carl] 第二句"))
        self.assertIsNone(rt.previous_speaker("", "[Carl] 你呢"))
        # A short line whose voice was not told ("其他人") may be anyone, the asker included: it is passed over.
        self.assertEqual(rt.previous_speaker("[我] 我做完了\n[其他人] 好", "[Carl] 那你下一步呢", unknown="其他人"), "我")
        self.assertEqual(rt.previous_speaker("[Bella] 我在翻土\n[其他人] 嗯", "[其他人] 那你打算種哪幾種", unknown="其他人"),
                         "Bella")
        self.assertIsNone(rt.previous_speaker("[其他人] 好\n[其他人] 對", "[Carl] 你呢", unknown="其他人"))
        # Only the lines just before count: after a long turn of the asker's own, nobody "just spoke".
        long_turn = "[我] 我先說\n" + "[Carl] 接著我報告\n" * rt.PREVIOUS_LINES
        self.assertIsNone(rt.previous_speaker(long_turn, "[Carl] 那你們這邊呢"))
        self.assertEqual(rt.previous_speaker(long_turn.split("\n", 2)[0] + "\n[Carl] 好", "[Carl] 那你呢"), "我")
        self.assertIsNone(rt.tag_of("no tag"))
        ex = self.explainer()
        self.sb.flag("urgent-kind", text="問我")
        ex._answer(1, "[Carl] 那你打算種哪幾種", context, "", time.time(), gen=ex.generation)
        prompt = self.sb.claude_calls()[-1]["prompt"]
        self.assertIn("最後一位發言的是 [Bella]，不是 Alex", prompt)
        self.assertTrue(any("explain: to_me" in m and "(對象：Alex" in m for m in self.c.logged), self.c.logged)
        ex._answer(1, "[Carl] 你那邊呢", "[我] 我做完了", "", time.time(), gen=ex.generation)
        self.assertIn("最後一位發言的是 [我]，也就是 Alex 本人", self.sb.claude_calls()[-1]["prompt"])
        ex._answer(1, "[Carl] 那你下一步呢", "[我] 我做完了\n[其他人] 好", "", time.time(), gen=ex.generation)
        self.assertIn("最後一位發言的是 [我]，也就是 Alex 本人", self.sb.claude_calls()[-1]["prompt"])
        ex._answer(1, "[Carl] 大家有問題嗎", "", "", time.time(), gen=ex.generation)
        self.assertNotIn("最後一位發言的是", self.sb.claude_calls()[-1]["prompt"])

    def test_an_answer_from_another_service(self):
        # [llm] provider = "openai": the same prompt, streamed back in the OpenAI chat format; no check online.
        fake = FakeOpenAI(reply="對象：大家，在提問\n問題\n什麼是 WebSocket\n一種雙向連線的協定。\n")
        try:
            service = self.rt.llm.OpenAICompatible(fake.url, key="k", model="some-model")
            ex = self.explainer(llm=service, model="some-model", web_verify=True)
            ex._answer(1, "[其他人 B] 什麼是 WebSocket", "[我] 前文", "", time.time(), gen=ex.generation)
            self.assertIn("什麼是 WebSocket\n一種雙向連線的協定。", self.c.notes)
            sent, = fake.requests                                            # one call: the answer, not checked online
            self.assertEqual((sent["body"]["model"], sent["body"]["stream"]), ("some-model", True))
            self.assertIn("觸發句：\n[其他人 B] 什麼是 WebSocket", sent["body"]["messages"][1]["content"])
            self.assertTrue(any("explain: question" in m for m in self.c.logged), self.c.logged)
            fake.status = 401
            ex._answer(1, "[其他人 B] 什麼是 gRPC 串流", "", "", time.time(), gen=ex.generation)
            self.assertTrue(any("explain: the model failed (401)" in m for m in self.c.logged), self.c.logged)
            self.assertNotIn("gRPC 串流", self.c.notes)
        finally:
            fake.close()

    def test_questions_just_answered_go_into_the_next_prompt(self):
        ex = self.explainer()
        ex._answer(1, "[其他人 B] 週五的聚餐可以改到週六嗎", "", "", time.time(), gen=ex.generation)
        (_, question), = ex.answers
        ex._answer(1, "[其他人 B] 我是說聚餐延一天", "", "", time.time(), gen=ex.generation)
        self.assertIn(f"剛回答過（最近幾分鐘）：{question}\n", self.sb.claude_calls()[-1]["prompt"])
        ex.answers.clear()
        ex.answers.append((time.time() - self.rt.JUST_ANSWERED_SECS - 1, question))  # long ago: left out
        self.assertEqual(ex.just_answered(), "")
        ex.answers.append((time.time(), question))
        ex.reset()                                                          # a new meeting
        self.assertEqual(ex.just_answered(), "")

    def test_old_generation_never_writes_into_the_new_meeting(self):
        ex = self.explainer()
        old = ex.generation
        ex.reset()
        ex._answer(1, "[其他人 B] 什麼是 WebSocket", "", "", time.time(), gen=old)
        self.assertEqual(self.c.notes, "")

    def test_duplicate_claim_and_rate_limit_stop_the_answer(self):
        ex = self.explainer(max_per_minute=1)
        ex.answered.add("[其他人 B] 什麼是 WebSocket")
        ex._answer(1, "[其他人 B] 什麼是 WebSocket", "", "", time.time(), gen=ex.generation)
        self.assertEqual(self.c.notes, "")
        ex.answered.clear()
        ex.recent.extend([time.time()])                                     # the cap for this minute is used up
        ex._answer(1, "[其他人 B] 什麼是 gRPC", "", "", time.time(), gen=ex.generation)
        self.assertEqual(self.c.notes, "")
        self.assertTrue(any("rate limit" in m for m in self.c.logged), self.c.logged)

    def test_answer_waits_for_a_streaming_answer_then_writes_whole(self):
        ex = self.explainer()
        seen_while_held = []
        self.lock.acquire()                                                 # another answer is streaming

        def release_later():
            time.sleep(0.8)
            seen_while_held.append(self.c.notes)
            self.lock.release()
        threading.Thread(target=release_later).start()
        ex._answer(1, "[其他人 B] 什麼是 WebSocket", "", "", time.time(), gen=ex.generation)
        self.assertEqual(seen_while_held, [""])                             # nothing while the lock was held
        self.assertIn("### 💡", self.c.notes)                                 # then the whole answer at once
        self.assertEqual(self.c.notes.count("### 💡"), 1)

    def test_watchdog_kills_a_stuck_claude(self):
        self.rt.ANSWER_TIMEOUT_SECS = 1
        self.sb.flag("claude-delay", text="5")
        ex = self.explainer()
        t0 = time.time()
        ex._answer(1, "[其他人 B] 什麼是 WebSocket", "", "", time.time(), gen=ex.generation)
        self.assertLess(time.time() - t0, 4)
        self.assertEqual(self.c.notes, "")

    def test_verify_writes_only_corrections(self):
        ex = self.explainer(web_verify=True)
        ex._verify("什麼是 WebSocket", "一個答案")
        self.assertEqual(self.c.notes, "")                                  # "OK": nothing to add
        self.sb.flag("verify-reply", text="WebSocket 不是 HTTP/2 的一部分")
        ex._verify("什麼是 WebSocket", "一個答案")
        self.assertIn("WebSocket 不是 HTTP/2", self.c.notes)
        self.assertIn(self.lang.t("verify_label") if "verify_label" in self.lang.s else "", self.c.notes)
        self.sb.flag("claude-fail")
        self.c.written.clear()
        ex._verify("q", "a")
        self.assertEqual(self.c.notes, "")
        self.assertTrue(any("verify failed" in m for m in self.c.logged))

    def test_workers_process_submitted_lines(self):
        ex = self.explainer()
        ex.submit(1, "[其他人 B] 什麼是 WebSocket", "ctx", "")
        self.assertTrue(self.sb.wait_for(lambda: "### 💡" in self.c.notes, timeout=10), self.c.logged)
        self.rt.STALE_SECS = 0
        ex.submit(1, "[其他人 B] 什麼是 gRPC", "", "")                      # queued "long ago": dropped
        self.assertTrue(self.sb.wait_for(lambda: any("stale" in m for m in self.c.logged), timeout=5))


if __name__ == "__main__":
    unittest.main()
