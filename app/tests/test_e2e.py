"""End-to-end: the real `autopilot.py` process (not an import) against the fake world, with the real
clock. Slower than the integration tests, so only the two main stories run here; coverage of the child
process is collected through COVERAGE_PROCESS_START when scripts/test.sh runs it."""
import json
import os
import sys
import threading
import time
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from harness import Sandbox  # noqa: E402


class RealProcess(unittest.TestCase):
    def setUp(self):
        self.sb = Sandbox(start_after=1, stop_after=1, tick_secs=2)
        self.sb.transcript_fixture()
        self.sb.tick_result()
        os.environ["MC_FAKE_TRANSCRIBER_INTERVAL"] = "0.01"

    def tearDown(self):
        self.sb.cleanup()

    def test_call_recorded_analysed_and_ended(self):
        self.sb.mic_users("us.zoom.xos")
        # The call ends 4 s in; the process runs 140 loops of 0.05 s (7 s) and must wrap everything up.
        threading.Timer(4.0, self.sb.mic_users).start()
        r = self.sb.run_autopilot(loops=140, poll_secs=0.05, timeout=120)
        self.assertEqual(r.returncode, 0, r.stderr[-2000:])
        minutes = [a[0] for n, a in self.sb.calls("minutes") if a[0] in ("record", "stop")]
        self.assertEqual(minutes, ["record", "stop"], self.sb.log())
        notes = self.sb.notes()
        self.assertIn("其他人 A｜大家好", notes)
        self.assertIn("排程功能下週二上線", notes)                            # the periodic analysis ran
        self.assertIn("### 💡", notes)                                       # an instant answer streamed
        st = self.sb.status()
        self.assertFalse(st["recording"])
        self.assertEqual(st["problems"], [])
        self.assertEqual(len(self.sb.calls("live_transcriber")), 1)
        self.assertTrue(any(n[0].startswith("🎙️") for n in self.sb.notifications()), self.sb.notifications())
        self.assertIn("screen capture stopped", self.sb.log())
        self.assertFalse((self.sb.state / "auto-recording").exists())

    def test_app_commands_and_restart(self):
        self.sb.command("start")

        def later():
            time.sleep(1.5)
            self.sb.command("restart_when_idle")
            time.sleep(0.5)
            self.sb.minutes(process_after_stop=False)
            self.sb.command("stop")
            time.sleep(1.0)
            (self.sb.meetings / "2026-01-15-會議.md").write_text("# 會議\n")   # Minutes wrote its summary
        threading.Thread(target=later).start()
        r = self.sb.run_autopilot(loops=400, poll_secs=0.05, timeout=120)
        self.assertEqual(r.returncode, 0, r.stderr[-2000:])
        log = self.sb.log()
        self.assertIn("app command: start", log)
        self.assertIn("restarting on the app's request", log)                # exited by itself once idle
        self.assertLess(log.index("app command: stop"), log.index("restarting on the app's request"))
        self.assertFalse(self.sb.status()["recording"])
        self.assertTrue(self.sb.status()["manual"] is False)

    def test_broken_config_is_reported_in_status(self):
        self.sb.config_path.write_text('[user\nname = "x"\n')
        r = self.sb.run_autopilot(loops=2, poll_secs=0.01, timeout=60)
        self.assertEqual(r.returncode, 0, r.stderr[-2000:])
        self.assertTrue(any("config.toml" in p for p in self.sb.status()["problems"]), self.sb.status())

    def test_deleted_model_folder_is_reported_not_fatal(self):
        self.sb.write_config(extra='[asr]\nprimary_model = "~/gone/models/gone-8bit"\n')
        r = self.sb.run_autopilot(loops=2, poll_secs=0.01, timeout=60)
        self.assertEqual(r.returncode, 0, r.stderr[-2000:])                  # the service keeps running
        st = self.sb.status()
        self.assertEqual(st["asr_missing"], "~/gone/models/gone-8bit")
        self.assertTrue(any("gone-8bit" in p for p in st["problems"]), st)

    def test_speech_model_missing_after_a_language_change_is_reported(self):
        self.sb.uncache_models()                       # e.g. Chinese chosen after an English install
        r = self.sb.run_autopilot(loops=2, poll_secs=0.01, timeout=60)
        self.assertEqual(r.returncode, 0, r.stderr[-2000:])
        st = self.sb.status()
        self.assertEqual(st["asr_missing"], "JacobLinCool/TEA-ASR-1.1")
        self.assertTrue(any("JacobLinCool/TEA-ASR-1.1" in p for p in st["problems"]), st)


if __name__ == "__main__":
    unittest.main()
