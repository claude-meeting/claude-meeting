"""Integration tests: the real autopilot main loop, in this process, against the fake Minutes, Claude,
mic-users, launchctl and osascript in tests/fakes/bin. Time is a fake clock, so waiting periods pass
instantly; the fake transcriber and Claude are real child processes.

    cd app && asr-venv/bin/python -m unittest tests.test_integration -v
"""
import json
import os
import sys
import time
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from harness import FakeClock, Sandbox  # noqa: E402


class AutoRecordingFlow(unittest.TestCase):
    """A call starts, is recorded, transcribed live, analysed, and ends."""

    @classmethod
    def setUpClass(cls):
        cls.sb = Sandbox(start_after=4, stop_after=6, tick_secs=5)
        cls.sb.transcript_fixture("live-transcript-zh.jsonl")
        cls.sb.tick_result()
        os.environ["MC_FAKE_TRANSCRIBER_INTERVAL"] = "0.005"
        cls.clock = FakeClock()
        cls.ap = cls.sb.import_autopilot(cls.clock)
        cls.ap.POLL_SECS = 1
        # A call app takes the microphone; the loop should start a recording after start_after_secs.
        cls.sb.mic_users("us.zoom.xos")
        cls.sb.run_main(cls.ap, loops=40, clock=cls.clock)
        cls.sb.wait_for(lambda: "其他人" in cls.sb.notes() and "Alex 你覺得" in cls.sb.notes(), timeout=15)
        cls.sb.wait_for(lambda: "## " in cls.sb.notes() and "📌" in cls.sb.notes() or "決定" in cls.sb.notes(), timeout=15)

    @classmethod
    def tearDownClass(cls):
        cls.sb.cleanup()

    def test_recording_was_started_through_minutes(self):
        records = [a for n, a in self.sb.calls("minutes") if a and a[0] == "record"]
        self.assertEqual(len(records), 1, self.sb.calls("minutes"))
        self.assertIn("--call", records[0])
        self.assertTrue(self.sb.status()["recording"])
        self.assertTrue(self.sb.status()["auto"])
        self.assertEqual(self.sb.status()["title"].split()[0], "Zoom")          # app label from the bundle id
        self.assertTrue((self.sb.state / "auto-recording").exists())

    def test_live_transcript_streams_into_the_notes(self):
        notes = self.sb.notes()
        self.assertIn("我｜好，我這邊先報告後端的部分。", notes)
        self.assertIn("其他人 A｜大家好", notes)                        # speakers are lettered until named
        self.assertRegex(notes, r"^# .*Zoom", msg=notes[:200])
        self.assertEqual(Path(self.sb.status()["notes"]).resolve(), (self.sb.meetings / "live" / "current.md").resolve())

    def test_live_transcriber_was_started_once_for_the_session(self):
        starts = [a for n, a in self.sb.calls("live_transcriber")]
        self.assertEqual(len(starts), 1, starts)
        self.assertTrue(starts[0][1].endswith(".jsonl"))
        self.assertRegex(Path(starts[0][1]).name, r"^rec-\d+\.jsonl$")

    def test_periodic_analysis_wrote_items_and_answers(self):
        notes = self.sb.notes()
        self.assertIn("排程功能下週二上線", notes)                      # a decision from the fake tick result
        self.assertIn("- 💡 **什麼是 WebSocket**", notes)
        self.assertIn("\n  - 由 HTTP 升級而來", notes)                   # multi-line answers stay indented
        ticks = [c for c in self.sb.claude_calls() if "--input-format" in c["args"]]
        self.assertGreaterEqual(len(ticks), 1)
        self.assertIn("Alex", ticks[0]["prompt"])                        # the user's name goes into the prompt

    def test_notifications_went_out(self):
        kinds = [n[0] for n in self.sb.notifications()]
        self.assertTrue(any("開始錄音" in k or "錄音" in k for k in kinds), kinds)

    def test_stop_when_the_call_ends(self):
        self.sb.mic_users()                                              # everyone hung up
        self.sb.run_main(self.ap, loops=30, clock=self.clock)
        stops = [a for n, a in self.sb.calls("minutes") if a and a[0] == "stop"]
        self.assertEqual(len(stops), 1, self.sb.calls("minutes"))
        self.assertFalse(self.sb.status()["recording"])
        self.assertFalse((self.sb.state / "auto-recording").exists())
        self.assertTrue(self.sb.wait_for(lambda: self.ap.LIVE_ASR.session is None, timeout=10), self.sb.log())
        self.assertIn("screen capture stopped", self.sb.log()) if self.ap.SCREEN.get("enabled") else None


class AppCommands(unittest.TestCase):
    def setUp(self):
        self.sb = Sandbox(start_after=4, stop_after=6, tick_secs=60)
        self.sb.transcript_fixture()
        os.environ["MC_FAKE_TRANSCRIBER_INTERVAL"] = "0.005"
        self.clock = FakeClock()
        self.ap = self.sb.import_autopilot(self.clock)
        self.ap.POLL_SECS = 1

    def tearDown(self):
        self.sb.cleanup()

    def test_start_and_stop_from_the_app(self):
        self.sb.command("start")
        self.sb.run_main(self.ap, loops=3, clock=self.clock)
        st = self.sb.status()
        self.assertTrue(st["recording"] and st["manual"] and not st["auto"])
        self.sb.command("stop")
        self.sb.run_main(self.ap, loops=3, clock=self.clock)
        self.assertFalse(self.sb.status()["recording"])
        self.assertEqual([a[0] for n, a in self.sb.calls("minutes") if a[0] in ("record", "stop")], ["record", "stop"])

    def test_a_recording_started_from_the_app_ends_with_its_call(self):
        # The user stopped and started again mid-call; when the call ends, the recording must end too.
        self.sb.mic_users("com.google.Chrome.helper")
        self.sb.command("start")
        self.clock.at(3, self.sb.mic_users)                              # the call ends
        self.sb.run_main(self.ap, loops=13, clock=self.clock)
        self.assertFalse(self.sb.status()["recording"])
        self.assertIn("the call of a recording started from the app ended", self.sb.log())

    def test_a_recording_started_from_the_app_without_a_call_goes_on(self):
        self.sb.command("start")                                         # a meeting in the room
        self.sb.run_main(self.ap, loops=12, clock=self.clock)
        self.assertTrue(self.sb.status()["recording"])
        self.assertEqual([a for n, a in self.sb.calls("minutes") if a[0] == "stop"], [])

    def test_user_stop_suppresses_auto_start_until_the_call_ends(self):
        self.sb.mic_users("us.zoom.xos")
        self.sb.run_main(self.ap, loops=10, clock=self.clock)
        self.assertTrue(self.sb.status()["recording"])
        self.sb.command("stop")
        self.sb.run_main(self.ap, loops=20, clock=self.clock)          # the call is still on: no restart
        self.assertEqual(len([a for n, a in self.sb.calls("minutes") if a[0] == "record"]), 1)
        self.assertFalse(self.sb.status()["recording"])
        self.sb.mic_users()
        self.sb.run_main(self.ap, loops=3, clock=self.clock)
        self.sb.mic_users("us.zoom.xos")                                 # a new call: recorded again
        self.sb.run_main(self.ap, loops=10, clock=self.clock)
        self.assertEqual(len([a for n, a in self.sb.calls("minutes") if a[0] == "record"]), 2)

    def test_pause_and_resume(self):
        self.sb.command("pause_auto")
        self.sb.mic_users("us.zoom.xos")
        self.sb.run_main(self.ap, loops=10, clock=self.clock)
        self.assertTrue(self.sb.status()["paused"])
        self.assertEqual([a for n, a in self.sb.calls("minutes") if a[0] == "record"], [])
        self.sb.command("resume_auto")
        self.sb.run_main(self.ap, loops=10, clock=self.clock)
        self.assertFalse(self.sb.status()["paused"])
        self.assertEqual(len([a for n, a in self.sb.calls("minutes") if a[0] == "record"]), 1)

    def test_restart_when_idle_exits_only_when_idle(self):
        import threading
        self.sb.command("start")
        self.sb.run_main(self.ap, loops=2, clock=self.clock)
        self.sb.command("restart_when_idle")
        self.sb.minutes(process_after_stop=False)
        # The request is remembered inside main(): the stop arrives while that same run is going.
        threading.Timer(0.3, lambda: self.sb.command("stop")).start()
        code = self.sb.run_main(self.ap, loops=400, clock=self.clock)
        log = self.sb.log()
        self.assertEqual(code, 0, log)                                   # exited once idle, for launchd to restart
        self.assertLess(log.index("app command: stop"), log.index("restarting on the app's request"))
        self.assertGreater(log.count("app command"), 1)                  # it kept looping while recording

    def test_minutes_unresponsive_keeps_start_stop_for_later(self):
        self.sb.minutes(fail=["status"])
        self.sb.command("start")
        self.sb.command("pause_auto")
        self.sb.run_main(self.ap, loops=2, clock=self.clock)
        self.assertTrue(self.sb.status()["paused"])                      # pause needs no Minutes
        self.assertTrue(any("Minutes" in p for p in self.sb.status()["problems"]), self.sb.status())
        self.assertTrue(list((self.sb.state / "commands").glob("*start.json")))   # start waits
        self.sb.minutes(fail=[])
        self.sb.run_main(self.ap, loops=3, clock=self.clock)
        self.assertTrue(self.sb.status()["recording"])
        self.assertEqual(self.sb.status()["problems"], [])


class CallAudioLost(unittest.TestCase):
    """Minutes records, but the call's audio stem gets only zeros: the recording is restarted once, then the
    user is told; a recording started in Minutes itself is never restarted by us."""
    RATE = 16000

    def setUp(self):
        self.sb = Sandbox(start_after=4, stop_after=6)
        self.clock = FakeClock()
        self.ap = self.sb.import_autopilot(self.clock)
        self.ap.POLL_SECS = 5
        self.ap.COPILOT_ENABLED = False
        self.minutes_dir = self.sb.home / ".minutes"

    def tearDown(self):
        self.sb.cleanup()

    def add(self, secs, sound):
        n = int(secs * self.RATE)
        with open(self.minutes_dir / "current.system.wav", "ab") as f:
            f.write(os.urandom(n * 2) if sound else b"\0" * n * 2)
        with open(self.minutes_dir / "current.voice.wav", "ab") as f:
            f.write(bytes(b | 1 for b in os.urandom(n * 2)))             # the microphone's noise floor

    def minutes(self, cmd):
        return [a for n, a in self.sb.calls("minutes") if a and a[0] == cmd]

    def lost_notices(self):
        return [n for n in self.sb.notifications() if "錄不到通話聲音" in n[0]]

    def test_restarted_once_then_the_user_is_told(self):
        self.sb.mic_users("com.google.Chrome.helper")
        self.sb.run_main(self.ap, loops=3, clock=self.clock)              # the call is recorded
        self.assertEqual(len(self.minutes("record")), 1)
        title = self.sb.status()["title"]
        self.add(30, sound=True)
        self.sb.run_main(self.ap, loops=4, clock=self.clock)
        self.assertEqual(self.minutes("stop"), [])                         # the call is heard
        first = self.ap.LIVE_ASR.path
        sp = self.ap.speakers
        sp.speakers_path(first).write_text(json.dumps({"A": {"vector": [0.1] * 4, "count": 9}}))
        sp.names_path(first).write_text(json.dumps({"A": "Alice Wong"}))
        time.sleep(1.1)                         # recordings are named by the second their stems were made
        self.add(130, sound=False)                                         # its audio stops reaching Minutes
        self.sb.run_main(self.ap, loops=4, clock=self.clock)
        self.assertEqual(len(self.minutes("stop")), 1)
        second = self.ap.LIVE_ASR.path
        self.assertNotEqual(second, first)
        # The same call goes on: its speakers and the names given so far carry over to the new recording.
        self.assertEqual(json.loads(sp.names_path(second).read_text()), {"A": "Alice Wong"})
        self.assertEqual(json.loads(sp.speakers_path(second).read_text())["A"]["count"], 9)
        records = self.minutes("record")
        self.assertEqual(len(records), 2)
        self.assertEqual(records[1][records[1].index("--title") + 1], title)   # the same meeting
        self.assertIn("已重新開始錄音", self.lost_notices()[-1][1])
        self.assertTrue(self.sb.status()["recording"])
        self.assertEqual(self.sb.status()["problems"], [])

        self.add(130, sound=False)                                         # the new recording did not help
        self.sb.run_main(self.ap, loops=6, clock=self.clock)
        self.assertEqual(len(self.minutes("stop")), 1)                     # no second restart
        self.assertIn("停止錄音", self.lost_notices()[-1][1])
        self.assertEqual(len(self.lost_notices()), 2)
        self.assertIn("⚠️ 錄不到通話聲音", self.sb.status()["problems"])
        self.add(130, sound=False)
        self.sb.run_main(self.ap, loops=6, clock=self.clock)
        self.assertEqual(len(self.lost_notices()), 2)                      # told once per call

        self.sb.mic_users()                                                # the call ends
        self.sb.run_main(self.ap, loops=4, clock=self.clock)
        self.assertFalse(self.sb.status()["recording"])
        self.assertEqual(self.sb.status()["problems"], [])

    def test_silence_after_the_call_ends_restarts_nothing(self):
        # Once the call has ended, its silence must not be taken for lost audio (no restart).
        self.sb.mic_users("com.google.Chrome.helper")
        self.sb.command("start")
        self.clock.at(10, lambda: self.add(30, sound=True))
        self.clock.at(25, lambda: (self.sb.mic_users(), self.add(130, sound=False)))   # the call ends
        self.sb.run_main(self.ap, loops=12, clock=self.clock)
        self.assertEqual(len(self.minutes("record")), 1)
        self.assertEqual(len(self.minutes("stop")), 1)                     # ended with the call
        self.assertEqual(self.lost_notices(), [])
        self.assertNotIn("call audio silent", self.sb.log())

    def test_a_recording_started_in_minutes_is_only_reported(self):
        self.sb.mic_users("com.google.Chrome.helper")
        self.sb.minutes(recording=True)
        self.sb.stems(True)
        self.sb.run_main(self.ap, loops=2, clock=self.clock)
        self.add(30, sound=True)
        self.sb.run_main(self.ap, loops=3, clock=self.clock)
        self.add(130, sound=False)
        self.sb.run_main(self.ap, loops=4, clock=self.clock)
        self.assertEqual(self.minutes("stop"), [])
        self.assertEqual(self.minutes("record"), [])
        self.assertEqual(len(self.lost_notices()), 1)
        self.assertIn("停止錄音", self.lost_notices()[0][1])

    def test_a_slow_mic_helper_does_not_end_the_call(self):
        # mic-users can time out on a Mac too busy to answer; that must not read as "nobody on the call", or
        # an automatic recording would be stopped after stop_after_secs.
        self.sb.mic_users("com.google.Chrome.helper")
        self.sb.run_main(self.ap, loops=3, clock=self.clock)
        self.assertTrue(self.sb.status()["recording"])
        self.sb.flag("mic-users-fail")
        self.sb.run_main(self.ap, loops=10, clock=self.clock)              # 50 s without an answer
        self.assertEqual(self.minutes("stop"), [])
        self.assertTrue(self.sb.status()["recording"])
        self.sb.flag("mic-users-fail", False)
        self.sb.mic_users()                                                # it answers again: the call ended
        self.sb.run_main(self.ap, loops=10, clock=self.clock)
        self.assertEqual(len(self.minutes("stop")), 1)

    def test_a_failed_restart_is_reported(self):
        self.sb.mic_users("com.google.Chrome.helper")
        self.sb.run_main(self.ap, loops=3, clock=self.clock)
        self.add(30, sound=True)
        self.sb.run_main(self.ap, loops=3, clock=self.clock)
        self.sb.minutes(fail=["record"])
        self.add(130, sound=False)
        self.sb.run_main(self.ap, loops=4, clock=self.clock)
        self.assertEqual(len(self.minutes("stop")), 1)
        self.assertIn("停止錄音", self.lost_notices()[0][1])
        self.assertIn("call audio restart failed", self.sb.log())


if __name__ == "__main__":
    unittest.main()
