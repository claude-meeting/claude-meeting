"""The call's speech the live transcript missed, found in the saved call stem (lib/call_gaps.py, G-10)."""
import json
import sys
import tempfile
import unittest
import wave
from pathlib import Path

import numpy as np

APP = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(APP / "lib"))

import call_gaps  # noqa: E402
import i18n  # noqa: E402


class FakeASR:
    """Speech where the stem is loud; the text says when it was."""

    def speech_spans(self, audio):
        loud = np.abs(audio) > 0.1
        spans, start = [], None
        for i in range(0, len(audio), 1600):                     # 0.1 s steps
            on = bool(loud[i:i + 1600].any())
            if on and start is None:
                start = i / 16000
            if not on and start is not None:
                spans.append((start, i / 16000))
                start = None
        if start is not None:
            spans.append((start, len(audio) / 16000))
        return spans

    def transcribe_segment(self, audio, start, end):
        return type("Seg", (), {"engine": "primary", "text": f"說到 {start:.0f} 秒"})()


class FakeVoice:
    enrolled = None

    def embed(self, audio):
        return np.array([1.0, 0.0, 0.0, 0.0], dtype=np.float32)


class CallGaps(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.TemporaryDirectory()
        self.root = Path(self.dir.name)

    def tearDown(self):
        self.dir.cleanup()

    def stem(self, loud_spans, secs=60):
        audio = np.zeros(secs * 16000, dtype=np.float32)
        for a, b in loud_spans:
            audio[int(a * 16000):int(b * 16000)] = 0.5
        path = self.root / "x.system.wav"
        with wave.open(str(path), "wb") as w:
            w.setnchannels(1)
            w.setsampwidth(2)
            w.setframerate(16000)
            w.writeframes((audio * 32767).astype("<i2").tobytes())
        return path

    def transcript(self, rows):
        path = self.root / "rec-1000.jsonl"
        path.write_text("".join(json.dumps(r, ensure_ascii=False) + "\n" for r in rows))
        return path

    def test_a_gap_is_speech_no_call_row_covers(self):
        rows = [{"start": 10.0, "end": 14.0}]
        self.assertEqual(call_gaps.gaps([(10.5, 13.5), (20.0, 25.0), (30.0, 30.3)], rows), [(20.0, 25.0)])
        self.assertEqual(call_gaps.gaps([(12.0, 18.0)], rows), [(12.0, 18.0)])        # covered under half

    def test_missing_call_speech_is_recovered_and_inserted_in_time(self):
        # 2026-11-24: the live call stem was silent while the call went on.
        part = self.transcript([
            {"ts": "2026-11-24T10:00:05", "start": 5.0, "end": 8.0, "speaker": i18n.OTHERS, "track": "system",
             "who": "A", "text": "前面有的"},
            {"ts": "2026-11-24T10:00:40", "start": 40.0, "end": 42.0, "speaker": i18n.SELF, "track": "voice",
             "text": "我說的"}])
        stem = self.stem([(5.0, 8.0), (20.0, 24.0)])
        rows = call_gaps.recover(part, stem, FakeASR(), None, 1_000_000, log=lambda m: None)   # no voiceprint needed
        self.assertEqual([(r["start"], r["text"], r["track"], r["recovered"], r.get("who")) for r in rows],
                         [(20.0, "說到 20 秒", "system", True, None)])                 # no letter: never a wrong one
        import os
        os.utime(part, (1_000, 1_000))
        call_gaps.insert(part, rows)
        starts = [json.loads(l)["start"] for l in part.read_text().splitlines()]
        self.assertEqual(starts, [5.0, 20.0, 40.0])                                  # in time order
        self.assertEqual(part.stat().st_mtime, 1_000)        # its age still says when the recording ended

    def test_the_merged_recording_leaves_the_users_voice_out(self):
        class Mine(FakeVoice):
            enrolled = np.array([1.0, 0.0, 0.0, 0.0])

            def score(self, audio):
                return 0.9 if audio.mean() > 0.4 else 0.1

        part = self.transcript([])
        stem = self.stem([(10.0, 12.0)])
        self.assertEqual(call_gaps.recover(part, stem, FakeASR(), Mine(), 0, log=lambda m: None, mixed=True), [])
        self.assertEqual(len(call_gaps.recover(part, stem, FakeASR(), Mine(), 0, log=lambda m: None, mixed=True,
                                               threshold=0.95)), 1)            # [voice] threshold, not a fixed one


if __name__ == "__main__":
    unittest.main()
