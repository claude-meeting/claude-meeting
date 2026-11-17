"""Unit tests for the parts of app/bin/autopilot.py that the integration tests only touch in passing:
transcript reading, process helpers, the live-ASR and screen-capture controllers, the notes writer and
the periodic analysis. The module is imported inside a Sandbox, so every external command is a fake."""
import json
import os
import struct
import sys
import time
import unittest
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from harness import FIXTURES, FakeClock, FakeOpenAI, Sandbox  # noqa: E402


class Base(unittest.TestCase):
    def setUp(self):
        self.sb = Sandbox()
        self.sb.tick_result()
        self.clock = FakeClock()
        self.ap = self.sb.import_autopilot(self.clock)

    def tearDown(self):
        self.sb.cleanup()

    def stems(self):
        self.sb.stems(True)


class ReadRows(Base):
    def test_incremental_and_torn_lines(self):
        p = self.sb.state / "t.jsonl"
        p.write_text('{"a": 1}\n{"a": 2}\n{"a": 3')
        self.assertEqual([r["a"] for r in self.ap.read_rows(p)], [1, 2])      # the torn line waits
        with open(p, "a") as f:
            f.write('}\nnot json\n{"a": 4}\n')
        self.assertEqual([r["a"] for r in self.ap.read_rows(p)], [1, 2, 3, 4])
        p.write_text('{"a": 9}\n')                                               # a new, shorter file
        self.assertEqual([r["a"] for r in self.ap.read_rows(p)], [9])
        self.assertEqual(self.ap.read_rows(self.sb.state / "missing.jsonl"), [])
        self.ap.forget_rows(Path("/other"))
        self.assertEqual(self.ap._ROWS_CACHE, {})

    def test_rows_are_copies(self):
        p = self.sb.state / "t.jsonl"
        p.write_text('{"a": 1}\n')
        rows = self.ap.read_rows(p)
        rows.append("x")
        self.assertEqual(len(self.ap.read_rows(p)), 1)


class ProcessHelpers(Base):
    def test_app_label(self):
        ap = self.ap
        self.assertEqual(ap.app_label(["us.zoom.xos"]), "Zoom")
        self.assertEqual(ap.app_label(["com.google.Chrome.helper"]), "Chrome")
        self.assertEqual(ap.app_label(["com.example.someapp"]), "someapp")
        self.assertEqual(ap.app_label([]), ap.LANG.t("call"))

    def test_mic_users_filters_system_and_own_processes(self):
        self.sb.mic_users("us.zoom.xos", "com.apple.Sound-Settings", "/Applications/Minutes.app/Contents/MacOS/minutes",
                          "corespeechd", "com.meeting-copilot.app")
        self.assertEqual(self.ap.mic_users(), ["us.zoom.xos"])
        self.sb.flag("mic-users.txt", False)
        self.assertEqual(self.ap.mic_users(), [])
        self.sb.flag("mic-users-fail")
        self.assertIsNone(self.ap.mic_users())                  # no answer is not "nobody"

    def test_status_and_run_failures(self):
        self.assertFalse(self.ap.status()["recording"])
        self.sb.minutes(fail=["status"])
        self.assertEqual(self.ap.status(), {})
        self.assertIsNone(self.ap.run(["/nonexistent/binary"]))
        self.assertIn("run failed", self.sb.log())

    def test_notify_respects_types_and_gap(self):
        ap = self.ap
        ap.NOTIFY_TYPES = {"decision"}
        ap.NOTIFY_MIN_GAP = 0
        ap.notify("t", "x", kind="risk")                                     # not enabled
        ap.notify("t", "y", kind="decision")
        ap.notify("sys", "z")                                                # system messages always go out
        ap.NOTIFY_MIN_GAP = 1000
        ap.notify("t", "too soon", kind="decision")
        self.assertEqual([n[1] for n in self.sb.notifications()], ["y", "z"])

    def test_recording_started_at(self):
        before = time.time()
        self.assertGreaterEqual(self.ap.recording_started_at(), before - 1)    # no stem: now
        self.stems()
        self.assertLessEqual(abs(self.ap.recording_started_at() - time.time()), 5)

    def test_screen_image_block_and_ask_model(self):
        from PIL import Image
        img = self.sb.root / "f.png"
        Image.new("RGB", (2000, 1000), "white").save(img)
        block = self.ap.screen_image_block(img)
        self.assertEqual(block["source"]["media_type"], "image/jpeg")
        self.assertGreater(len(block["source"]["data"]), 100)
        out = self.ap.ask_model("hello", [img, self.sb.root / "missing.png"])
        self.assertIn("items", json.loads(out))
        self.assertEqual(self.sb.claude_calls()[-1]["images"], 1)
        self.sb.flag("claude-fail")
        self.assertIsNone(self.ap.ask_model("hello"))
        self.assertIn("claude failed", self.sb.log())

    def test_a_speaking_name_is_read_again_at_full_size(self):
        from PIL import Image
        ap = self.ap
        img = self.sb.root / "frame.png"
        Image.new("RGB", (2560, 1440), "black").save(img)
        box = [0.36, 0.19, 0.40, 0.20]
        crop = ap.name_crop(img, box)                    # a ~102 x 14 px label, widened and enlarged
        self.assertEqual((crop.width, crop.height), (205 * 4, 43 * 4))     # enlarged 4 times at most
        self.assertIsNone(ap.name_crop(img, None))
        self.assertIsNone(ap.name_crop(img, [0.5, 0.2, 0.4, 0.3]))
        replies = {"Pip 石錨貓": '{"name": "Pip 石貓貓"}', "諸葛蘑菇": '{"name": "南瓜湯..."}'}
        asked = []
        real = ap.ask_model
        try:
            ap.ask_model = lambda prompt, images, **kw: (asked.append(prompt), next(
                (r for n, r in replies.items() if f'"{n}"' in prompt), None))[1]
            ap.SPELLED.clear()
            self.assertEqual(ap.spelled_name(img, "Pip 石錨貓", box), "Pip 石貓貓")
            self.assertEqual(ap.spelled_name(img, "Pip 石錨貓", box), "Pip 石貓貓")
            self.assertEqual(len(asked), 1)                                  # each reading is checked once
            self.assertEqual(ap.spelled_name(img, "諸葛蘑菇", box), "諸葛蘑菇")                # a neighbour's
            self.assertEqual(ap.spelled_name(img, "番茄蛋", None), "番茄蛋")                 # no box: as read
            self.assertEqual(ap.spelled_name(img, "Bob", box), "Bob")        # no answer: tried again later
            self.assertNotIn("bob", ap.SPELLED)
        finally:
            ap.ask_model = real
            ap.SPELLED.clear()
        self.assertIn("reads Pip 石貓貓 at full size", self.sb.log())
        self.assertIn("another label, not taken", self.sb.log())

    def test_notifications_go_through_the_app_while_it_runs(self):
        ap = self.ap
        notes = ap.LIVE_DIR / "2025-01-15-0930-Demo.md"
        ap.LIVE_DIR.mkdir(parents=True, exist_ok=True)
        notes.write_text("# notes\n")
        (ap.LIVE_DIR / "current.md").symlink_to(notes.name)
        ap.control.HEARTBEAT.touch()
        ap.notify("紀錄完成", "x" * 300, sound="Ping")
        files = list(ap.control.NOTIFICATIONS.iterdir())
        self.assertEqual(len(files), 1)
        self.assertEqual(json.loads(files[0].read_text()), {"title": "紀錄完成", "text": "x" * 240, "sound": "Ping",
                                                            "open": str(notes.resolve())})
        self.assertEqual(self.sb.notifications(), [])                        # not sent a second time

    def test_notifications_fall_back_to_script_without_the_app(self):
        ap = self.ap
        ap.notify("a", "no app")
        ap.control.HEARTBEAT.touch()
        ap.control.NOTIFICATIONS.write_text("not a folder")                  # the hand-over fails
        ap.notify("b", "cannot write")
        self.assertEqual([n[0] for n in self.sb.notifications()], ["a", "b"])

    def test_open_viewer_is_skipped_while_the_app_runs(self):
        self.ap.control.HEARTBEAT.touch()
        self.ap.open_viewer()
        self.assertEqual(self.sb.notifications(), [])
        os.utime(self.ap.control.HEARTBEAT, (0, 0))
        self.ap.open_viewer()
        self.assertTrue(any("tail -n 200" in x[0] for x in self.sb.notifications()), self.sb.notifications())


class ModelCheck(Base):
    def test_one_warning_per_meeting_and_recheck(self):
        ap = self.ap
        auth = ap.ModelCheck()
        self.sb.flag("claude-signed-out")
        auth.check("m1")
        self.assertIsNotNone(auth.problem)
        self.assertEqual(len(self.sb.notifications()), 1)
        auth.checked_at = 0
        auth.check("m1")                                                     # re-checked, not re-notified
        self.assertEqual(len(self.sb.notifications()), 1)
        auth.check("m2")
        self.assertEqual(len(self.sb.notifications()), 2)
        auth.recheck()                                                       # too soon: nothing
        self.sb.flag("claude-signed-out", False)
        auth.checked_at = 0
        auth.recheck()
        self.assertIsNone(auth.problem)
        self.assertIn("ok again", self.sb.log())

    def test_missing_cli(self):
        self.ap.LLM.path = "/nonexistent/claude"
        self.assertEqual(self.ap.model_problem(), self.ap.LANG.t("n_claude_missing"))


class AnotherService(unittest.TestCase):
    """[llm] provider = "openai": the notes, the checks and the answers go to the service the user names."""

    def setUp(self):
        self.fake = FakeOpenAI(reply='{"summary": "", "items": [], "answers": []}')
        self.sandboxes = []

    def tearDown(self):
        self.fake.close()
        for sb in self.sandboxes:
            sb.cleanup()

    def autopilot(self, llm):
        sb = Sandbox(extra=f"[llm]\nprovider = \"openai\"\n{llm}")
        self.sandboxes.append(sb)
        (sb.home / ".config" / "meeting-copilot" / "api_key").write_text("sk-test\n")
        return sb, sb.import_autopilot()

    def test_notes_checks_and_answers_go_to_it(self):
        from PIL import Image
        sb, ap = self.autopilot(f'base_url = "{self.fake.url}"\nmodel = "big"\nfast_model = "small"\n')
        self.assertEqual((ap.LLM.name, ap.MODELS["notes"], ap.MODELS["check"]), ("openai", "big", "small"))
        self.assertIsNone(ap.model_problem())
        img = sb.root / "frame.png"
        Image.new("RGB", (64, 48), "black").save(img)
        self.assertIn("items", ap.ask_model("整理一下", [img]))
        sent = self.fake.requests[-1]
        self.assertEqual((sent["auth"], sent["body"]["model"]), ("Bearer sk-test", "big"))
        self.assertEqual(sent["body"]["messages"][1]["content"], "整理一下")      # no image: the model was not said to read them
        self.assertFalse(ap.NAME_FROM_SCREEN)                                    # nor are frames read for who speaks
        cp = ap.Copilot()
        self.assertEqual((ap.FEED._window(cp).model, ap.FEED.explainer.model, ap.FEED.explainer.llm), ("small", "big", ap.LLM))
        self.fake.status = 401
        self.assertIsNone(ap.ask_model("整理一下"))
        self.assertIn("openai failed: (401)", sb.log())
        self.assertEqual(ap.model_problem(), ap.LANG.t("n_llm_rejected"))

    def test_a_model_that_reads_images_gets_the_frames(self):
        from PIL import Image
        sb, ap = self.autopilot(f'base_url = "{self.fake.url}"\nmodel = "big"\nvision = true\n')
        img = sb.root / "frame.png"
        Image.new("RGB", (64, 48), "black").save(img)
        ap.ask_model("整理一下", [img])
        kinds = [part["type"] for part in self.fake.requests[-1]["body"]["messages"][1]["content"]]
        self.assertEqual(kinds, ["image_url", "text"])
        self.assertTrue(ap.NAME_FROM_SCREEN)

    def test_what_is_missing_is_said(self):
        _, ap = self.autopilot("")
        self.assertEqual(ap.model_problem(), ap.LANG.t("n_llm_no_url"))
        _, ap = self.autopilot(f'base_url = "{self.fake.url}"\n')
        self.assertEqual(ap.model_problem(), ap.LANG.t("n_llm_no_model"))

    def test_calls_that_keep_failing_are_said_with_the_services_words(self):
        sb, ap = self.autopilot(f'base_url = "{self.fake.url}"\nmodel = "no-such-model"\n')
        self.fake.status = 404
        self.assertIsNone(ap.ask_model("整理一下"))
        self.assertIsNone(ap.model_problem())                                    # once may be a hiccup
        self.assertIsNone(ap.ask_model("整理一下"))
        self.assertTrue(ap.model_problem().startswith(ap.LANG.t("n_llm_failing") + " (404)"), ap.model_problem())

    def test_a_provider_that_is_neither_is_a_problem_not_claude(self):
        sb = Sandbox(extra='[llm]\nprovider = "ollama"\n')
        self.sandboxes.append(sb)
        ap = sb.import_autopilot()
        self.assertEqual(ap.model_problem(), ap.LANG.t("n_llm_unknown"))
        self.assertIsNone(ap.ask_model("整理一下"))
        self.assertEqual(sb.claude_calls(), [])


class LiveASRController(Base):
    def test_stems_pending_and_ensure(self):
        ap = self.ap
        asr = ap.LiveASR()
        self.assertTrue(asr.stems_pending())                                 # never looked yet
        asr.ensure()
        self.assertIsNotNone(asr.waiting_since)
        self.assertTrue(asr.stems_pending())
        asr.waiting_since -= ap.STEMS_GRACE_SECS + 1
        self.assertFalse(asr.stems_pending())
        self.sb.transcript_fixture()
        self.sb.minutes(recording=True)
        self.stems()
        asr.ensure()
        self.assertRegex(asr.session, r"^rec-\d+$")
        self.assertIsNone(asr.waiting_since)
        pid = asr.proc.pid
        asr.ensure()                                                         # same session: nothing happens
        self.assertEqual(asr.proc.pid, pid)
        self.assertTrue(self.sb.wait_for(lambda: asr.path.exists() and len(ap.read_rows(asr.path)) >= 9, 10))
        self.assertTrue(asr.healthy() and asr.running())
        finals = asr.finals(60)
        self.assertEqual(finals[0]["line"], 1)
        self.assertTrue(finals[0]["text"].startswith("[其他人 A]"))
        self.assertEqual(asr.finals(-1), [])                                 # nothing newer than the future
        self.sb.minutes(recording=False)
        self.assertTrue(self.sb.wait_for(lambda: not asr.running(), 10))
        self.assertTrue(asr.healthy())                                       # exit 0 is fine

    def test_failed_transcriber_is_reported_once(self):
        ap = self.ap
        os.environ["MC_FAKE_TRANSCRIBER_EXIT"] = "3"
        try:
            asr = ap.LiveASR()
            self.sb.minutes(recording=False)
            self.stems()
            asr.ensure()
            self.assertTrue(self.sb.wait_for(lambda: asr.proc.poll() is not None, 10))
            self.assertFalse(asr.healthy())
            self.assertFalse(asr.healthy())
            self.assertTrue(asr.failed_notified)
            self.assertEqual(len([n for n in self.sb.notifications() if ap.LANG.t("n_live_asr_stopped") in n[0]]), 1)
        finally:
            os.environ.pop("MC_FAKE_TRANSCRIBER_EXIT")

    def test_missing_model_is_named_in_the_notification(self):
        ap = self.ap
        os.environ["MC_FAKE_TRANSCRIBER_EXIT"] = "5"
        try:
            asr = ap.LiveASR()
            self.sb.minutes(recording=False)
            self.stems()
            asr.ensure()
            self.assertTrue(self.sb.wait_for(lambda: asr.proc.poll() is not None, 10))
            self.assertFalse(asr.healthy())                                   # Minutes' transcript takes over
            stopped = [n for n in self.sb.notifications() if ap.LANG.t("n_live_asr_stopped") in n[0]]
            self.assertEqual(len(stopped), 1)
            self.assertNotEqual(stopped[0][1], ap.LANG.t("n_live_asr_stopped_body"))   # says the model is missing
        finally:
            os.environ.pop("MC_FAKE_TRANSCRIBER_EXIT")

    def test_deferred_to_an_older_transcriber(self):
        ap = self.ap

        class Proc:
            pid = 1

            def poll(self):
                return 4

        asr = ap.LiveASR()
        asr.proc = Proc()
        asr.path = self.sb.state / "live" / "rec-1.jsonl"
        asr.path.parent.mkdir(parents=True)
        asr.path.with_suffix(".pid").write_text(str(os.getpid()))          # alive
        self.assertTrue(asr.running() and asr.healthy())
        asr.path.with_suffix(".pid").write_text("999999")                   # gone
        self.assertFalse(asr.running())
        self.assertFalse(asr.healthy())

    def test_ensure_replaces_a_transcriber_from_the_previous_recording(self):
        ap = self.ap
        self.sb.transcript_fixture()
        os.environ["MC_FAKE_TRANSCRIBER_MAX_SECS"] = "30"
        try:
            asr = ap.LiveASR()
            self.sb.minutes(recording=True)
            self.stems()
            asr.ensure()
            old = asr.proc
            time.sleep(1.1)
            self.sb.stems(False)
            self.stems()                                                     # a new recording: new stem files
            asr.ensure()
            self.assertIsNot(asr.proc, old)
            self.assertIsNotNone(old.poll())
            self.assertIn("still running; stopping it", self.sb.log())
        finally:
            os.environ.pop("MC_FAKE_TRANSCRIBER_MAX_SECS")

    def test_names_and_name_speakers(self):
        ap = self.ap
        asr = ap.LiveASR()
        self.assertEqual(asr.names(), {})
        asr.path = self.sb.state / "live" / "rec-2.jsonl"
        asr.path.parent.mkdir(parents=True)
        asr.path.write_text("")
        ap.speakers.speakers_path(asr.path).write_text(json.dumps(
            {"A": {"vector": [0.9, 0.1, 0, 0], "count": 6}, "B": {"vector": [0, 0, 1, 0], "count": 1}}))
        written = []
        asr.name_speakers({"A": "毛毛蟲", "": "x", "Z": "nobody"}, written.append)
        self.assertEqual(asr.names().get("A"), "毛毛蟲")
        self.assertTrue(written and "毛毛蟲" in written[0])
        self.assertFalse(ap.PEOPLE.path.exists())                            # naming is not saving: learn_voices


class SpeakerNaming(Base):
    def live(self, rows, snap):
        ap = self.ap
        ap.LIVE_ASR.session = "rec-1000000"
        ap.LIVE_ASR.path = self.sb.state / "live" / "rec-1000000.jsonl"
        ap.LIVE_ASR.path.parent.mkdir(parents=True, exist_ok=True)
        ap.LIVE_ASR.path.write_text("".join(json.dumps(r, ensure_ascii=False) + "\n" for r in rows))
        ap.speakers.speakers_path(ap.LIVE_ASR.path).write_text(json.dumps(snap))
        return ap.LIVE_ASR

    def test_never_the_users_name(self):
        asr = self.live([], {"B": {"vector": [0, 1, 0, 0], "count": 6}, "C": {"vector": [0, 0, 1, 0], "count": 6}})
        asr.name_speakers({"B": "alex", "C": "Dan"}, [].append)
        self.assertEqual(asr.names(), {"C": "Dan"})
        asr.name_speakers({"B": "Alex Chen"}, [].append)                     # the user's tile, full name
        self.assertNotIn("B", asr.names())
        self.assertIn("the user's own name", self.sb.log())

    def test_the_voice_vetoes_a_screen_vote_for_a_saved_person(self):
        ap = self.ap
        ap.PEOPLE.learn("Alice", [1, 0, 0, 0], count=6)
        asr = self.live([], {"A": {"vector": [1, 0, 0, 0], "count": 6}, "C": {"vector": [0, 0, 1, 0], "count": 2}})
        check = ap.voice_not_them(asr.path)
        self.assertFalse(check("A", "Alice"))
        self.assertTrue(check("C", "Alice"))
        self.assertFalse(check("C", "Bella"))                               # nobody saved by that name
        self.assertFalse(check("Z", "Alice"))                               # a group not heard yet
        ap.speakers.speakers_path(asr.path).write_text("{")
        self.assertFalse(check("C", "Alice"))                               # being rewritten
        ap.STORE_VOICEPRINTS = False
        try:
            self.assertIsNone(ap.voice_not_them(asr.path))
        finally:
            ap.STORE_VOICEPRINTS = True

    def test_no_note_when_the_voice_already_gave_the_name(self):
        # A note "👤 其他人 A 是 Alice" is redundant when A's lines already show Alice by voice.
        ap = self.ap
        ap.PEOPLE.learn("Alice", [1, 0, 0, 0], count=6)
        asr = self.live([], {"A": {"vector": [1, 0, 0, 0], "count": 6}, "B": {"vector": [0, 1, 0, 0], "count": 6}})
        notes = []
        asr.name_speakers({"A": "Alice", "B": "Bella"}, notes.append)
        self.assertEqual(asr.names(), {"A": "Alice", "B": "Bella"})
        self.assertEqual(len(notes), 1)
        self.assertIn("Bella", notes[0])
        self.assertIn("speaker A named Alice, as the voice already said", self.sb.log())

    def test_line_voices_for_the_screen(self):
        ap = self.ap
        asr = self.live([], {})
        get = ap.voice_of_line(asr.path)
        self.assertIsNone(get(1.0))                                          # no vectors yet
        vectors = ap.speakers.vectors_path(asr.path)
        vectors.write_text(json.dumps({"start": 1.0, "v": [1, 0]}) + "\n")
        self.assertEqual(list(get(1.0)), [1, 0])
        with vectors.open("a") as f:
            f.write(json.dumps({"start": 2.5, "v": [0, 1]}) + "\n")
        self.assertEqual(list(get(2.5)), [0, 1])                             # re-read when the file grew
        ap.STORE_VOICEPRINTS = False
        try:
            self.assertIsNone(ap.voice_of_line(asr.path))
        finally:
            ap.STORE_VOICEPRINTS = True

    def test_the_screen_outranks_a_saved_voice(self):
        ap = self.ap
        ap.PEOPLE.learn("Alice", [1, 0, 0, 0], count=6)
        asr = self.live([], {"A": {"vector": [1, 0, 0, 0], "count": 6}})
        self.assertEqual(asr.names(), {"A": "Alice"})                         # the saved voice, for now
        asr.name_speakers({"A": "Carl"}, [].append)
        self.assertEqual(asr.names(), {"A": "Carl"})

    def test_a_name_the_screen_takes_back_leaves_the_saved_voice(self):
        # Taking back a wrong screen name must not hide the voice's clear match as well.
        ap = self.ap
        ap.PEOPLE.learn("Alice", [1, 0, 0, 0], count=6)
        asr = self.live([], {"A": {"vector": [1, 0, 0, 0], "count": 6}})
        notes = []
        asr.name_speakers({"A": "Bella"}, notes.append)
        self.assertEqual(asr.names(), {"A": "Bella"})                         # the screen outranks the voice
        asr.name_speakers({"A": ""}, notes.append)
        self.assertEqual(asr.names(), {"A": "Alice"})                         # taken back: the voice again
        self.assertIn("不一定是 Bella", notes[-1])
        asr.name_speakers({"A": "Carl"}, notes.append)                        # the screen agrees again
        self.assertEqual(asr.names(), {"A": "Carl"})

    def test_a_saved_voice_does_not_give_back_the_name_taken_back(self):
        ap = self.ap
        ap.PEOPLE.learn("Alice", [1, 0, 0, 0], count=6)
        asr = self.live([], {"A": {"vector": [1, 0, 0, 0], "count": 6}})
        asr.name_speakers({"A": "Alice"}, [].append)
        asr.name_speakers({"A": ""}, [].append)
        self.assertEqual(asr.names(), {})

    def teacher(self, votes):
        t = self.ap.screen_speakers.ScreenSpeakers(None, ())
        for who, (name, counts) in votes.items():
            t.decided[who] = name
            t.votes[who] = self.ap.screen_speakers.Counter(counts)
        return t

    def test_voices_are_saved_only_on_agreement_through_the_meeting(self):
        ap = self.ap
        asr = self.live([], {"A": {"vector": [1, 0, 0, 0], "count": 9}, "B": {"vector": [0, 1, 0, 0], "count": 9},
                             "C": {"vector": [0, 0, 1, 0], "count": 9}})
        ap.learn_voices(self.teacher({"A": ("Alice", {"Alice": 6}), "B": ("Bella", {"Bella": 5, "Dan": 1}),
                                      "C": ("Carl", {"Carl": 2})}), asr.path)
        self.assertEqual(list(json.loads(self.sb.people.read_text())), ["Alice"])
        self.assertIn("speaker B (Bella): not learnt", self.sb.log())

    def test_a_voice_on_file_as_someone_else_is_not_saved_under_a_new_name(self):
        ap = self.ap
        ap.PEOPLE.learn("Alice", [1, 0, 0, 0], count=6)
        asr = self.live([], {"A": {"vector": [1, 0, 0, 0], "count": 9}})
        ap.learn_voices(self.teacher({"A": ("Carl", {"Carl": 6})}), asr.path)
        self.assertNotIn("Carl", json.loads(self.sb.people.read_text()))
        self.assertIn("the voice is Alice's", self.sb.log())

    def test_a_voice_is_saved_from_the_lines_the_screen_confirmed(self):
        ap = self.ap
        rows = [{"start": float(s), "end": s + 2.0, "speaker": "其他人", "who": "A", "text": "好", "ts": "x"}
                for s in range(0, 14, 2)]
        asr = self.live(rows, {"A": {"vector": [0.6, 0.8, 0, 0], "count": 7}})
        vectors = [[1, 0, 0, 0]] * 5 + [[0, 1, 0, 0]] * 2               # the last two lines: someone else
        ap.speakers.vectors_path(asr.path).write_text("".join(
            json.dumps({"start": r["start"], "v": v}) + "\n" for r, v in zip(rows, vectors)))
        t = self.teacher({"A": ("Alice", {"Alice": 5})})
        t.seen_as = {r["start"]: "Alice" for r in rows[:5]}
        ap.learn_voices(t, asr.path)
        saved = json.loads(self.sb.people.read_text())["Alice"]
        self.assertEqual((saved["vector"], saved["count"]), ([1.0, 0.0, 0.0, 0.0], 5))
        self.assertIn("voice saved as Alice from 5 lines", self.sb.log())
        t.seen_as = {r["start"]: "Alice" for r in rows[:4]}
        t.decided, t.votes = {"A": "Bella"}, {"A": ap.screen_speakers.Counter({"Bella": 5})}
        ap.learn_voices(t, asr.path)                                        # no line showed Bella
        self.assertIn("speaker A (Bella): not learnt, 0 lines", self.sb.log())

    def test_moved_turns_and_unsure_lines_in_the_notes(self):
        ap = self.ap
        cp = ap.Copilot()
        rows = [{"ts": f"2025-01-15T09:30:0{i}", "start": float(i), "end": i + 0.9, "speaker": "其他人", "who": "A",
                 "text": f"第{i}句"} for i in range(3)]
        asr = self.live(rows, {"A": {"vector": [1, 0, 0, 0], "count": 3}})
        ap.LIVE_ASR.names = lambda: {"A": "Alice"}
        ap.FEED.poll(cp, "m")
        self.assertIn("Alice｜第2句", cp.notes.read_text())
        with open(asr.path, "a") as f:
            f.write(json.dumps(ap.speakers.relabel_row("A", "B", [1.0, 2.0])) + "\n")
            f.write(json.dumps(dict(rows[2], start=3.0, who="B", text="第3句"), ensure_ascii=False) + "\n")
        ap.FEED.poll(cp, "m")
        notes = cp.notes.read_text()
        self.assertIn("👤 09:30:01 起那段的聲音和 Alice 不同，改標為 其他人 B", notes)
        self.assertIn("其他人 B｜第3句", notes)
        self.assertEqual([r["who"] for r in ap.read_rows(asr.path)], ["A", "B", "B", "B"])
        self.assertEqual(cp.relabels_seen, 1)
        written = []
        ap.mark_unsure(asr.path, ap.read_rows(asr.path), [(0.0, "Carl", "Alice")], written.append)
        self.assertIn("09:30:00 那句畫面上是 Carl 在說，不標成 Alice", written[0])
        self.assertTrue(ap.read_rows(asr.path)[0]["unsure"])
        self.assertIn("[其他人] 第0句", ap.LIVE_ASR.finals(10 ** 7)[0]["text"])
        self.assertEqual(ap.speakers.unsure_starts(asr.path), {0.0})
        ap.forget_rows(None)                                                # an autopilot restart re-reads the file
        self.assertTrue(ap.read_rows(asr.path)[0].get("unsure"))

    def test_voices_are_learnt_when_the_recording_ends(self):
        ap = self.ap
        asr = self.live([], {"A": {"vector": [1, 0, 0, 0], "count": 9}})
        ap.SCREEN_NAMER.teacher, ap.SCREEN_NAMER.path = self.teacher({"A": ("Alice", {"Alice": 6})}), asr.path
        ap.SCREEN_NAMER.finish()
        ap.SCREEN_NAMER.learning.join(10)
        self.assertIn("Alice", json.loads(self.sb.people.read_text()))
        self.assertIsNone(ap.SCREEN_NAMER.teacher)

    def test_a_voice_matched_group_is_still_read_from_the_screen(self):
        from PIL import Image
        ap = self.ap
        ap.PEOPLE.learn("Alice", [0, 0, 0, 1], count=6)
        rows = [{"start": s, "end": s + 3, "speaker": "其他人", "who": "A", "text": "好"} for s in (1, 5)]
        asr = self.live(rows, {"A": {"vector": [0, 0, 0, 1], "count": 6}})
        self.assertEqual(asr.names(), {"A": "Alice"})
        ap.SCREEN_CAP.session = asr.session
        ap.SCREEN_CAP.out_dir = self.sb.state / "screens" / asr.session
        (ap.SCREEN_CAP.out_dir / "display-1").mkdir(parents=True)
        Image.new("RGB", (64, 36)).save(ap.SCREEN_CAP.out_dir / "display-1" / "frame-1000000000.jpg")
        (self.sb.fake / "speaking.json").write_text(json.dumps(
            {"layout": "grid", "speaking": "Carl Stone", "confidence": "high"}))
        copilot = ap.Copilot()
        copilot.notes = self.sb.state / "notes.md"
        ap.SCREEN_NAMER.poll(copilot)
        ap.SCREEN_NAMER.thread.join(20)
        self.assertEqual(asr.names(), {"A": "Carl Stone"})

    def test_names_from_the_screen(self):
        from PIL import Image
        ap = self.ap
        rows = [{"start": s, "end": s + 3, "speaker": "其他人", "who": "A", "text": "好"} for s in (1, 5)]
        asr = self.live(rows, {"A": {"vector": [0, 0, 0, 1], "count": 6}})
        ap.SCREEN_CAP.session = asr.session
        ap.SCREEN_CAP.out_dir = self.sb.state / "screens" / asr.session
        (ap.SCREEN_CAP.out_dir / "display-1").mkdir(parents=True)
        Image.new("RGB", (64, 36)).save(ap.SCREEN_CAP.out_dir / "display-1" / "frame-1000000000.jpg")
        (self.sb.fake / "speaking.json").write_text(json.dumps(
            {"layout": "grid", "speaking": "Alice Wong", "confidence": "high"}))
        copilot = ap.Copilot()
        copilot.notes = self.sb.state / "notes.md"
        ap.SCREEN_NAMER.poll(copilot)
        ap.SCREEN_NAMER.thread.join(20)
        self.assertEqual(asr.names(), {"A": "Alice Wong"})
        self.assertIn("Alice Wong", copilot.notes.read_text())
        calls = [json.loads(l) for l in (self.sb.fake / "claude.log").read_text().splitlines()]
        self.assertEqual(len(calls), 1)                                      # one frame, read once
        self.assertEqual(calls[0]["images"], 1)
        self.assertIn("claude-sonnet-5-5", calls[0]["args"])
        self.assertNotIn("WebSearch", calls[0]["args"])
        self.assertFalse(self.sb.people.exists())                            # two lines name it, not enough to save it

    def test_nothing_without_this_recordings_frames(self):
        ap = self.ap
        self.live([], {})
        ap.SCREEN_CAP.session = "rec-other"
        ap.SCREEN_CAP.out_dir = self.sb.state / "screens" / "rec-other"
        ap.SCREEN_NAMER.poll(ap.Copilot())
        self.assertIsNone(ap.SCREEN_NAMER.thread)


class ScreenCapture(Base):
    def test_ensure_running_stop_and_video(self):
        ap = self.ap
        cap = ap.ScreenCap()
        cap.ensure("rec-7")
        plist = ap.STATE / f"{cap.LABEL}.plist"
        self.assertTrue(plist.exists())
        import plistlib
        args = plistlib.loads(plist.read_bytes())["ProgramArguments"]
        self.assertEqual(args[0], ap.SCREEN_CAPTURE)
        self.assertIn("--displays", args)
        self.assertTrue(cap.running())
        self.assertEqual([a[0] for n, a in self.sb.calls("launchctl") if a[0] != "print"][-2:], ["bootout", "bootstrap"])
        cap.ensure("rec-7")                                                  # same session: no new bootstrap
        self.assertEqual(len([a for n, a in self.sb.calls("launchctl") if a[0] == "bootstrap"]), 1)
        d = cap.out_dir / "display-1"
        d.mkdir()
        for ms in (1000, 2000, 3000):
            (d / f"frame-{ms}.jpg").write_bytes(b"x")
        cap.stop()
        self.assertTrue((cap.out_dir / "STOP").exists())
        notes = self.sb.meetings / "n.md"
        notes.write_text("# n\n")
        cap.make_video(notes)
        self.assertTrue(self.sb.wait_for(lambda: not cap.video_running(), 20))
        self.assertTrue(any(n == "frames-to-video" for n, a in self.sb.calls()), self.sb.calls())
        self.assertIn("display-1", notes.read_text())                        # the link was appended

    def test_a_new_session_waits_for_the_old_job_and_retries(self):
        # A recording restarted within a call lost its screen capture: launchd refused the new job while it
        # still held the old one, and the bootstrap was never tried again.
        cap = self.ap.ScreenCap()
        cap.ensure("rec-1")
        (self.sb.fake / "bootstrap-fails").write_text("2")
        cap.ensure("rec-2")
        self.assertEqual(len([a for n, a in self.sb.calls("launchctl") if a[0] == "bootstrap"]), 4)
        self.assertIn("screen capture started for rec-2: ok (attempt 3)", self.sb.log())
        self.assertTrue(cap.running())
        (self.sb.fake / "bootstrap-fails").write_text("9")
        cap.ensure("rec-3")
        self.assertIn("screen capture started for rec-3: failed", self.sb.log())

    def test_target_app_and_permission_warning(self):
        ap = self.ap
        ap.SCREEN["target"] = "app:us.zoom.xos"
        cap = ap.ScreenCap()
        cap.ensure("rec-8")
        import plistlib
        args = plistlib.loads((ap.STATE / f"{cap.LABEL}.plist").read_bytes())["ProgramArguments"]
        self.assertEqual(args[args.index("--app") + 1], "us.zoom.xos")
        (cap.out_dir / "capture.err").write_text("error: TCC denied screen capture\n")
        cap.check_permission()
        cap.check_permission()
        self.assertTrue(cap.warned)
        self.assertEqual(len([n for n in self.sb.notifications() if ap.LANG.t("n_screen_denied") in n[0]]), 1)
        ap.SCREEN["enabled"] = False
        cap2 = ap.ScreenCap()
        cap2.ensure("rec-9")
        self.assertIsNone(cap2.session)

    def test_capture_watch_reports_device_changes(self):
        ap = self.ap
        err = ap.STATE / "capture.err"
        err.write_text("starting\n")
        watch = ap.CaptureWatch()
        watch.check()
        with open(err, "a") as f:
            f.write("capture reconnected: USB Mic -> Built-in\n")
        watch.check()
        self.assertTrue(any("USB Mic" in n[1] for n in self.sb.notifications()))
        err.write_text("new\n")                                              # truncated by a new recording
        watch.check()
        self.assertEqual(watch.offset, 4)


class CopilotNotes(Base):
    def copilot(self):
        return self.ap.Copilot()

    def test_reset_creates_notes_and_current_symlink(self):
        cp = self.copilot()
        cp.reset("rec-1", "Zoom 通話 / 測試: 1")
        self.assertTrue(cp.notes.exists())
        self.assertNotIn("/", cp.notes.name[16:])
        current = self.sb.meetings / "live" / "current.md"
        self.assertEqual(current.resolve(), cp.notes.resolve())
        self.assertIn("Zoom 通話", cp.notes.read_text())
        first = cp.notes
        cp.reset("rec-1", "ignored")                                         # same session: no-op
        self.assertIs(cp.notes, first)
        cp.pending_lines.append("12:00:00 我｜留在舊筆記\n")
        cp.reset("rec-2", "second")
        self.assertIn("留在舊筆記", first.read_text())                        # flushed into the old meeting
        self.assertEqual(cp.pending_lines, [])
        self.assertNotEqual(cp.notes, first)

    def test_session_is_restored_after_a_restart(self):
        cp = self.copilot()
        cp.reset("rec-3", "m")
        cp.last_line, cp.source, cp.feed_seen = 7, "live", 5
        cp.already.append("decision: x")
        cp._save_session()
        cp2 = self.copilot()
        cp2.reset("rec-3", "m")
        self.assertEqual((cp2.last_line, cp2.source, cp2.feed_seen, cp2.already), (7, "live", 5, ["decision: x"]))
        self.assertEqual(cp2.notes, cp.notes)

    def test_deliver_items_answers_and_dedup(self):
        ap = self.ap
        cp = self.copilot()
        cp.reset("rec-4", "m")
        result = json.loads((FIXTURES / "tick-result.json").read_text())
        cp.already.append("answered: 下週二上線來得及嗎")                       # the instant answer covered it
        cp._deliver(result, "m")
        notes = cp.notes.read_text()
        self.assertIn("排程功能下週二上線", notes)
        self.assertIn(f"B{ap.LANG.t('colon')}確認測試環境", notes)             # owner prefix
        self.assertNotIn("來得及嗎", notes.split("##")[-1].split("💡")[0])    # question_to_me deduped
        self.assertIn("- 💡 **什麼是 WebSocket**\n  WebSocket", notes)
        self.assertIn("\n  - 適合即時推送", notes)
        self.assertIn(ap.LANG.t("summary", text="").strip("：: "), notes)
        before = len(cp.already)
        cp._deliver(result, "m")                                             # every item is a duplicate now
        self.assertEqual(len(cp.already), before)
        decision = f"{ap.ITEM_LABELS['decision']}{ap.LANG.t('colon')}排程功能下週二上線"
        self.assertEqual(cp.notes.read_text().count(decision), 1)
        self.assertEqual(cp.notes.read_text().count("什麼是 WebSocket"), 1)
        cp._deliver({"items": ["junk", {"type": "decision"}, {"type": "weird", "text": "x"}],
                     "answers": [1, {"question": "q"}]}, "m")
        self.assertIn("x", cp.notes.read_text())                             # unknown types use the default label

    def test_tick_live_source(self):
        ap = self.ap
        cp = self.copilot()
        ap.LIVE_ASR.session = "rec-5"
        ap.LIVE_ASR.path = ap.STATE / "live" / "rec-5.jsonl"
        ap.LIVE_ASR.path.parent.mkdir(parents=True)
        rows = [json.loads(l) for l in (FIXTURES / "live-transcript-zh.jsonl").read_text().splitlines()]
        from datetime import datetime
        now = datetime.now().isoformat(timespec="seconds")
        ap.LIVE_ASR.path.write_text("".join(json.dumps({**r, "ts": now}, ensure_ascii=False) + "\n" for r in rows))

        class Proc:
            def poll(self):
                return None
        ap.LIVE_ASR.proc = Proc()
        cp._tick("m")
        self.assertEqual(cp.session, "rec-5")
        self.assertEqual(cp.source, "live")
        self.assertEqual(cp.last_line, 9)
        self.assertIn("## ", cp.notes.read_text())
        prompt = self.sb.claude_calls()[-1]["prompt"]
        self.assertIn("[其他人 A] 大家好", prompt)
        self.assertIn("alex", prompt)
        cp._tick("m")                                                        # nothing new: no second call
        self.assertEqual(len([c for c in self.sb.claude_calls() if "--input-format" in c["args"]]), 1)

    def test_tick_waits_for_stems_then_falls_back_to_minutes(self):
        ap = self.ap
        cp = self.copilot()
        ap.LIVE_ASR.waiting_since = time.time()
        cp._tick("m")
        self.assertIsNone(cp.session)
        self.assertEqual(self.sb.claude_calls(), [])
        ap.LIVE_ASR.waiting_since -= ap.STEMS_GRACE_SECS + 1
        finals = json.loads((FIXTURES / "minutes-transcript.json").read_text())["finals"]
        self.sb.minutes(finals=finals, session_id="0123456789abcdef0123456789abcdef")
        cp._tick("m")
        self.assertEqual(cp.session, "0123456789abcdef0123456789abcdef")
        self.assertEqual(cp.source, "minutes")
        prompt = self.sb.claude_calls()[-1]["prompt"]
        self.assertIn("排程 API 已經完成", prompt)
        self.assertNotIn("] 好\n", prompt)                                   # Minutes' one-word filler dropped

    def test_tick_handles_claude_failures(self):
        ap = self.ap
        cp = self.copilot()
        ap.LIVE_ASR.waiting_since = time.time() - ap.STEMS_GRACE_SECS - 1
        self.sb.minutes(finals=[{"line": 1, "ts": "2025-01-15T09:30:00", "text": "hello"}], session_id="s1")
        self.sb.flag("claude-fail")
        cp._tick("m")
        self.assertIn("claude failed", self.sb.log())
        self.sb.flag("claude-fail", False)
        self.sb.tick_result({"nonsense": True})
        cp._tick("m")
        self.assertNotIn("tick error", self.sb.log())                         # an object without items is fine
        self.sb.flag("tick-result.json", text="[1, 2]")
        cp.last_line = 0
        cp._tick("m")
        self.assertIn("tick error", self.sb.log())

    def test_screen_text_and_images(self):
        ap = self.ap
        cp = self.copilot()
        cap = ap.SCREEN_CAP
        cap.ensure("rec-6")
        from PIL import Image, ImageDraw
        d = cap.out_dir / "display-1"
        d.mkdir()
        for i, box in enumerate(((0, 0, 20, 64), (40, 0, 64, 64), (40, 0, 64, 64))):   # the last two look alike
            img = Image.new("RGB", (64, 64), "white")
            ImageDraw.Draw(img).rectangle(box, fill="black")
            img.save(d / f"frame-{1000 + i}.jpg")
        os.utime(d / "frame-1000.jpg", (time.time() - 1, time.time() - 1))
        os.utime(d / "frame-1001.jpg", (time.time() - 1, time.time() - 1))
        os.utime(d / "frame-1002.jpg", (time.time() - 1, time.time() - 1))
        text, images = cp._new_screen_text()
        self.assertIn("OCR frame-1000.jpg", text)
        self.assertEqual(len(images), 2)                                      # the third frame equals the second
        text, images = cp._new_screen_text()
        self.assertEqual((text, images), ("", []))                           # nothing changed since
        ap.SCREEN["enabled"] = False
        self.assertEqual(cp._new_screen_text(), ("", []))


class Feed(Base):
    def test_poll_streams_lines_and_triggers(self):
        ap = self.ap
        cp = ap.Copilot()
        ap.LIVE_ASR.session = "rec-9"
        ap.LIVE_ASR.path = ap.STATE / "live" / "rec-9.jsonl"
        ap.LIVE_ASR.path.parent.mkdir(parents=True)
        rows = [json.loads(l) for l in (FIXTURES / "live-transcript-zh.jsonl").read_text().splitlines()]
        ap.LIVE_ASR.path.write_text("".join(json.dumps(r, ensure_ascii=False) + "\n" for r in rows[:6]))
        ap.FEED.poll(cp, "m")
        notes = cp.notes.read_text()
        self.assertIn("其他人 A｜大家好", notes)
        self.assertIn("我｜好，我這邊先報告", notes)
        self.assertEqual(cp.feed_seen, 6)
        self.assertTrue(self.sb.wait_for(lambda: "### " in cp.notes.read_text(), 10), self.sb.log())  # instant answer
        self.assertTrue(any("有人提到你" in n[0] or "Alex" in n[1] for n in self.sb.notifications()))
        with open(ap.LIVE_ASR.path, "a") as f:
            f.write("".join(json.dumps(r, ensure_ascii=False) + "\n" for r in rows[6:] + [dict(rows[0], text="對對對。")]))
        ap.FEED.poll(cp, "m")
        self.assertEqual(cp.feed_seen, 10)
        self.assertNotIn("對對對", cp.notes.read_text() + "".join(cp.pending_lines))   # filler stays out of the notes
        ap.FEED.new_meeting()
        self.assertEqual(ap.FEED.explainer.answered, set())

    def test_the_answer_has_its_own_model(self):
        # The fast model can take a question put to whoever spoke last as one asked of the user; the stronger
        # model tells them apart. Checking the lines stays with the fast model.
        ap = self.ap
        cp = ap.Copilot()
        self.assertEqual((ap.FEED._window(cp).model, ap.FEED.explainer.model), ("fake-haiku", "claude-sonnet-5-5"))
        ap.MODELS["answer"] = "fake-big"
        self.assertEqual(ap.TranscriptFeed()._explainer(cp).model, "fake-big")


class CallAudio(Base):
    """CallAudioWatch: Minutes records, but the call's audio stem gets only digital zeros."""
    RATE = 16000

    def setUp(self):
        super().setUp()
        self.sb.stems(True)
        self.system = self.sb.home / ".minutes" / "current.system.wav"
        self.voice = self.sb.home / ".minutes" / "current.voice.wav"
        self.watch = self.ap.CallAudioWatch()
        self.now = 1_000_000.0

    def add(self, secs, kind="zero", mic="noise"):
        """Append to the call stem (sound, zeros, or shreds: 11 ms of sound in every 55) and to the microphone
        stem (its noise floor, or zeros when the recording is paused)."""
        n = int(secs * self.RATE)
        with open(self.system, "ab") as f:
            if kind == "shreds":
                burst = int(0.011 * self.RATE)
                pattern = bytes(b | 1 for b in os.urandom(burst * 2)) + b"\0" * burst * 8
                f.write((pattern * (n // (burst * 5) + 1))[:n * 2])
            else:
                f.write(os.urandom(n * 2) if kind == "sound" else b"\0" * n * 2)
        with open(self.voice, "ab") as f:
            f.write(bytes(b | 1 for b in os.urandom(n * 2)) if mic == "noise" else b"\0" * n * 2)

    def check(self, transcript=None, step=10):
        self.now += step
        return self.watch.silent_secs(self.now, transcript)

    def test_lost_after_the_call_was_heard(self):
        self.add(30, "sound")
        self.assertEqual(self.check(), 0)
        self.add(60)
        self.assertEqual(self.check(), 0)                      # 60 s of zeros: not yet
        self.add(65)
        self.assertGreaterEqual(self.check(), 120)

    def test_shreds_of_the_call_are_lost_too(self):
        # Shreds: a fifth of the samples at normal level, in runs of 10-12 ms: nothing to transcribe.
        self.add(30, "sound")
        self.assertEqual(self.check(), 0)
        self.add(125, "shreds")
        self.assertGreaterEqual(self.check(), 120)

    def test_never_heard_is_not_lost(self):
        # A call whose audio never reached the stem (or a call nobody has spoken in yet) is left alone.
        self.add(300)
        self.assertEqual(self.check(), 0)

    def test_a_stray_system_sound_does_not_hide_the_loss(self):
        self.add(30, "sound")
        self.check()
        for _ in range(13):
            self.add(9.95)
            self.add(0.05, "sound")                            # 0.5 % of the window: a notification sound
        self.assertGreaterEqual(self.check(), 120)

    def test_quiet_stretches_of_a_working_call_are_not_lost(self):
        # A working call: up to ~15 s of zeros at a time while nobody speaks, never minutes.
        self.add(30, "sound")
        self.check()
        for _ in range(10):
            self.add(15)
            self.add(3, "sound")
        self.assertEqual(self.check(), 0)

    def test_not_lost_while_someone_is_transcribed(self):
        # The other side is silent but the user presents: the user's lines show it is a quiet call.
        transcript = self.sb.state / "rec.jsonl"
        self.add(30, "sound")
        self.check(transcript)
        self.add(130)
        ts = __import__("datetime").datetime.fromtimestamp(self.now - 30).isoformat(timespec="seconds")
        transcript.write_text(json.dumps({"ts": ts, "speaker": "我", "text": "接下來看第二頁"}) + "\n")
        self.assertEqual(self.check(transcript), 0)
        self.assertGreaterEqual(self.check(transcript, step=200), 120)   # nothing new for 120 s: lost

    def test_isolated_spikes_are_not_sound(self):
        # A broken capture can deliver ~16 isolated 2 ms spikes a second (6-10 % non-zero samples), which kept
        # the old check quiet for two extra minutes.
        self.add(30, "sound")
        self.check()
        spike = os.urandom(64)                                 # 32 samples: 2 ms
        second = (spike + b"\0" * (1000 * 2 - 64)) * 16      # 16 spikes in 16000 samples
        with open(self.system, "ab") as f:
            f.write(second * 125)
        with open(self.voice, "ab") as f:
            f.write(bytes(b | 1 for b in os.urandom(125 * 16000 * 2)))
        self.assertGreaterEqual(self.check(), 120)

    def test_a_paused_recording_is_not_lost(self):
        # A paused recording: both stems all zeros for minutes at a time, then they come back by themselves.
        self.add(30, "sound")
        self.check()
        self.add(130, mic="zero")
        self.assertEqual(self.check(), 0)

    def test_reads_at_most_every_ten_seconds(self):
        self.add(30, "sound")
        self.check()
        self.add(130)
        self.assertEqual(self.check(step=5), 0)                # too soon: not even read
        self.assertGreaterEqual(self.check(step=5), 120)

    def test_a_restarted_recording_keeps_what_the_call_showed(self):
        # The restart did not help: the new (all-zero) recording is still noticed. A new call starts over.
        self.add(30, "sound")
        self.check()
        self.add(130)
        self.assertGreaterEqual(self.check(), 120)
        self.sb.stems(True)                                    # Minutes started a new recording
        self.add(60)
        self.assertEqual(self.check(), 0)
        self.add(70)
        self.assertGreaterEqual(self.check(), 120)
        self.watch.end_call()
        self.sb.stems(True)
        self.add(200)
        self.assertEqual(self.check(), 0)

    def test_off_and_missing_or_odd_files(self):
        self.add(30, "sound")
        self.add(130)
        self.ap.CALL_AUDIO_LOST_SECS = 0
        self.assertEqual(self.check(), 0)
        self.ap.CALL_AUDIO_LOST_SECS = 120
        self.system.unlink()
        self.assertEqual(self.check(), 0)
        self.system.write_bytes(b"not a wav file at all, but long enough to have a header....")
        self.assertEqual(self.check(), 0)
        self.voice.write_bytes(b"not a wav file at all, but long enough to have a header....")
        self.assertEqual(self.check(), 0)


class RestartedRecording(Base):
    def test_the_notes_go_on_in_the_same_file(self):
        ap = self.ap
        cp = ap.Copilot()
        first_epoch, later_epoch = 1_650_001_200, 1_650_002_400               # made-up recordings, 20 min apart
        last_line = datetime.fromtimestamp(first_epoch + 1000)               # the last line before the gap
        first = ap.LIVE_TRANSCRIPTS / f"rec-{first_epoch}.jsonl"
        first.parent.mkdir(parents=True, exist_ok=True)
        first.write_text(json.dumps({"ts": last_line.isoformat(timespec="seconds"), "start": 1.0, "end": 2,
                                     "speaker": "我", "text": "好"}) + "\n")
        cp.reset(f"rec-{first_epoch}", "Chrome 通話")
        cp.already = ["decision: x"]
        cp._save_session()
        notes = cp.notes
        later = ap.LIVE_TRANSCRIPTS / f"rec-{later_epoch}.jsonl"
        ap.minutes_record.mark_part(later, first)
        cp.reset(f"rec-{later_epoch}", "Chrome 通話")
        self.assertEqual(cp.notes, notes)
        self.assertEqual((cp.already, cp.feed_seen, cp.last_line), (["decision: x"], 0, 0))
        gap = datetime.fromtimestamp(later_epoch).strftime("%H:%M")
        self.assertIn(f"⚠️ {last_line:%H:%M}–{gap} 通話聲音中斷，已重新錄音；這段沒有錄到", notes.read_text())
        self.assertIn(f"continues the notes of rec-{first_epoch}", self.sb.log())
        cp.reset("rec-1650004000", "next")                                    # a recording of its own
        self.assertNotEqual(cp.notes, notes)


class MeetingRecordFromOurTranscript(Base):
    START = 1_650_001_200

    def meeting(self, name, start):
        md = self.sb.meetings / name
        md.write_text(f"---\ntitle: t\ntype: meeting\ndate: {datetime.fromtimestamp(start).astimezone().isoformat()}\n"
                      "---\n\n## Summary\n\n- s\n\n## Transcript\n\n[SPEAKER_1 0:00] whisper\n")
        old = time.time() - 60
        os.utime(md, (old, old))
        return md

    def test_the_transcript_is_ours_and_the_summary_is_redone(self):
        ap = self.ap
        live = ap.LIVE_TRANSCRIPTS / f"rec-{self.START}.jsonl"
        live.parent.mkdir(parents=True, exist_ok=True)
        live.write_text("".join(json.dumps(r, ensure_ascii=False) + "\n" for r in (
            {"start": 1.2, "speaker": "其他人", "who": "A", "text": "大家好"}, {"start": 64, "speaker": "我", "text": "好"})))
        ap.speakers.speakers_path(live).write_text(json.dumps({"A": {"vector": [1, 0, 0, 0], "count": 3}}))
        ap.speakers.names_path(live).write_text(json.dumps({"A": "Alice"}))
        record = ap.MeetingRecord()
        record.since = time.time() - 120
        ours = self.meeting("2025-01-15-call.md", self.START + 1)
        other = self.meeting("2025-01-15-memo.md", self.START + 900)            # no transcript of ours
        record.poll(processing=True)
        self.assertIn("whisper", ours.read_text())                              # Minutes is still at work
        record.poll(processing=False)
        text = ours.read_text()
        self.assertIn("## Summary\n\n- s", text)
        self.assertTrue(text.endswith("## Transcript\n\n[Alice 0:01] 大家好\n[Alex 1:04] 好\n"), text)
        record.proc.wait(10)
        record.poll(processing=False)
        self.assertIsNone(record.proc)
        self.assertIn(["resummarize", str(ours), "--apply"], [a for _, a in self.sb.calls("minutes")])
        self.assertIn("whisper", other.read_text())
        self.assertEqual(ap.MeetingRecord().done, {ours.name, other.name})    # remembered across restarts

    def test_a_restarted_recording_is_one_meeting_record(self):
        # The call's audio was lost and the recording restarted: one meeting must not get two records.
        ap = self.ap
        first, second = self.START, self.START + 1200
        for epoch, rows in ((first, [{"start": 1.0, "end": 2, "speaker": "其他人", "who": "A", "text": "前半"}]),
                            (second, [{"start": 3.0, "end": 4, "speaker": "其他人", "who": "A", "text": "後半"}])):
            t = ap.LIVE_TRANSCRIPTS / f"rec-{epoch}.jsonl"
            t.parent.mkdir(parents=True, exist_ok=True)
            t.write_text("".join(json.dumps(r, ensure_ascii=False) + "\n" for r in rows))
            ap.speakers.speakers_path(t).write_text(json.dumps({"A": {"vector": [1, 0, 0, 0], "count": 3}}))
        later = ap.LIVE_TRANSCRIPTS / f"rec-{second}.jsonl"
        ap.speakers.names_path(later).write_text(json.dumps({"A": "Alice"}))
        ap.minutes_record.mark_part(later, ap.LIVE_TRANSCRIPTS / f"rec-{first}.jsonl")
        record = ap.MeetingRecord()
        record.since = time.time() - 120
        one = self.meeting("2025-01-20-call.md", first + 1)
        ap.LIVE_ASR.path = later                                              # the call goes on
        record.poll(processing=False)
        self.assertIn("whisper", one.read_text())
        self.assertNotIn(one.name, record.done)
        ap.LIVE_ASR.path = None
        record.poll(processing=False)                                         # the second file is not written yet
        self.assertIn("whisper", one.read_text())
        two = self.meeting("2025-01-20-call-2.md", second + 1)
        record.poll(processing=False)
        text = one.read_text()
        self.assertTrue(text.endswith("[Alice 0:01] 前半\n（通話聲音中斷，約 1198 秒沒有錄到）\n[Alice 20:03] 後半\n"), text)
        self.assertFalse(two.exists())
        self.assertTrue((self.sb.meetings / ".parts" / two.name).exists())
        self.assertEqual(ap.MeetingRecord.meeting_of(self.sb.meetings / ".parts" / two.name), one)
        self.assertEqual(ap.MeetingRecord.meeting_of(one), one)
        record.proc.wait(10)
        self.assertIn(["resummarize", str(one), "--apply"], [a for _, a in self.sb.calls("minutes")])
        self.assertTrue({one.name, two.name} <= ap.MeetingRecord().done)

    def test_a_part_whose_file_never_comes_is_given_up_on(self):
        ap = self.ap
        first, second = self.START, self.START + 600
        for epoch in (first, second):
            t = ap.LIVE_TRANSCRIPTS / f"rec-{epoch}.jsonl"
            t.parent.mkdir(parents=True, exist_ok=True)
            t.write_text(json.dumps({"start": 1.0, "end": 2, "speaker": "我", "text": "好"}) + "\n")
            os.utime(t, (time.time() - 4000, time.time() - 4000))
        ap.minutes_record.mark_part(ap.LIVE_TRANSCRIPTS / f"rec-{second}.jsonl",
                                    ap.LIVE_TRANSCRIPTS / f"rec-{first}.jsonl")
        record = ap.MeetingRecord()
        record.since = time.time() - 120
        one = self.meeting("2025-01-20-call.md", first)
        record.poll(processing=False)
        self.assertIn("[Alex 0:01] 好\n（通話聲音中斷", one.read_text())

    def test_meeting_files_from_before_are_left_alone(self):
        live = self.ap.LIVE_TRANSCRIPTS / f"rec-{self.START}.jsonl"
        live.parent.mkdir(parents=True, exist_ok=True)
        live.write_text(json.dumps({"start": 1, "speaker": "我", "text": "好"}) + "\n")
        md = self.meeting("2025-01-15-call.md", self.START)
        record = self.ap.MeetingRecord()                                        # started after the file was written
        record.poll(processing=False)
        self.assertIn("whisper", md.read_text())


if __name__ == "__main__":
    unittest.main()
