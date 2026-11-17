"""Unit tests for app/bin/live_transcriber.py: following growing stem files, resuming, and the whole
program on synthetic speech with fake recognizers (tests/fakes/pymods)."""
import json
import os
import queue
import struct
import subprocess
import sys
import time
import unittest
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent))
from harness import APP, FAKES_PY, FakeClock, Sandbox  # noqa: E402

SR = 16000


def header():
    return (b"RIFF" + struct.pack("<I", 0) + b"WAVEfmt " + struct.pack("<IHHIIHH", 16, 1, 1, SR, SR * 2, 2, 16)
            + b"data" + struct.pack("<I", 0))


def pcm(seconds, level=0.3, seed=0):
    rng = np.random.default_rng(seed)
    return (rng.standard_normal(int(seconds * SR)) * level * 32767 / 4).clip(-32767, 32767).astype("<i2").tobytes()


class FakeVAD:
    """A VADIterator stand-in: speech starts at chunk `start` and ends at chunk `end` (indexes of the
    512-sample chunks fed to it), repeating every `period` chunks."""

    def __init__(self, start=2, end=12, period=10**9):
        self.start, self.end, self.period = start, end, period
        self.n = -1

    def __call__(self, chunk):
        self.n += 1
        k = self.n % self.period
        if k == self.start:
            return {"start": self.n * 512}
        if k == self.end:
            return {"end": self.n * 512}
        return None


class TrackFollowing(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.sb = Sandbox()
        cls.lt = cls.sb.import_module("live_transcriber", APP / "bin" / "live_transcriber.py")
        cls.lt.VADIterator = lambda model, **kw: model          # the "model" we pass is the FakeVAD itself

    @classmethod
    def tearDownClass(cls):
        cls.sb.cleanup()

    def follower(self, path, vad=None):
        q = queue.Queue()
        return self.lt.TrackFollower(str(path), "我", vad or FakeVAD(), q), q

    def test_growing_file_yields_segments_with_track_times(self):
        p = self.sb.root / "a.voice.wav"
        p.write_bytes(header())
        f, q = self.follower(p)
        self.assertFalse(f.poll())                                           # header only: nothing yet
        with open(p, "ab") as fh:
            fh.write(pcm(1.0))
        self.assertTrue(f.poll())
        self.assertFalse(f.poll())                                           # nothing new
        speaker, start, end, audio = q.get_nowait()
        self.assertEqual(speaker, "我")
        self.assertAlmostEqual(start, 2 * 512 / SR, places=3)
        self.assertAlmostEqual(end, 13 * 512 / SR, places=3)                 # the "end" chunk is included
        self.assertEqual(len(audio), 11 * 512)
        self.assertGreater(f.rms_between(start, end), 0)
        self.assertEqual(f.rms_between(5.0, 6.0), 0.0)                      # beyond what was read
        self.assertLess(abs((f.wall_time(0.1) - __import__("datetime").datetime.now()).total_seconds()), 5)

    def test_short_speech_is_dropped_and_long_speech_is_cut(self):
        p = self.sb.root / "b.voice.wav"
        p.write_bytes(header() + pcm(0.5))
        f, q = self.follower(p, FakeVAD(start=0, end=3))                    # 0.1 s of speech: too short
        f.poll()
        self.assertTrue(q.empty())
        self.lt.asr.MAX_SEGMENT_SECS = 1.0
        try:
            p2 = self.sb.root / "c.voice.wav"
            p2.write_bytes(header() + pcm(2.6))
            f2, q2 = self.follower(p2, FakeVAD(start=0, end=10**8))           # speech never ends
            f2.poll()
            f2.flush()
            segs = []
            while not q2.empty():
                segs.append(q2.get_nowait())
            self.assertGreaterEqual(len(segs), 2)                            # forced cuts
            self.assertTrue(all(len(s[3]) <= SR * 1.0 + 512 for s in segs))
        finally:
            self.lt.asr.MAX_SEGMENT_SECS = 20

    def test_long_speech_is_cut_at_a_pause(self):
        class Pausing(FakeVAD):
            """Speech that never ends, with a dip below the threshold from chunk 40 (1.28 s) on."""
            current_sample, temp_end = 0, 0

            def __call__(self, chunk):
                event = super().__call__(chunk)
                self.current_sample = (self.n + 1) * 512
                self.temp_end = 40 * 512 if self.n >= 40 else 0
                return event

        saved = self.lt.asr.SOFT_SEGMENT_SECS, self.lt.asr.MAX_SEGMENT_SECS
        self.lt.asr.SOFT_SEGMENT_SECS, self.lt.asr.MAX_SEGMENT_SECS = 1.0, 30
        try:
            p = self.sb.root / "p.voice.wav"
            p.write_bytes(header() + pcm(1.6))
            f, q = self.follower(p, Pausing(start=0, end=10**8))
            f.poll()
            spk, start, end, audio = q.get_nowait()                          # cut once the pause is 0.1 s long
            self.assertEqual((start, round(end, 3)), (0.0, round(44 * 512 / SR, 3)))
            self.assertTrue(q.empty())
        finally:
            self.lt.asr.SOFT_SEGMENT_SECS, self.lt.asr.MAX_SEGMENT_SECS = saved

    def test_new_recording_ends_the_track_but_a_short_file_waits(self):
        p = self.sb.root / "d.voice.wav"
        p.write_bytes(header() + pcm(0.5))
        f, q = self.follower(p)
        f.poll()
        f.offset += 100000                                                    # resumed beyond the file's end
        self.assertFalse(f.poll())
        self.assertFalse(f.finished)                                          # waits for Minutes to pad it
        f.offset -= 100000
        p.unlink()
        p.write_bytes(header() + pcm(0.5))                                    # a new file (new inode)
        self.assertFalse(f.poll())
        self.assertTrue(f.finished)
        self.assertFalse(f.poll())                                            # stays finished

    def test_resume_offsets_keep_rms_indexing_consistent(self):
        p = self.sb.root / "e.voice.wav"
        p.write_bytes(header() + pcm(2.0, seed=1))
        f, q = self.follower(p, FakeVAD(start=1, end=12))
        f.samples_read = 512 * 20
        f.offset = 44 + f.samples_read * 2
        f.base_chunk = 20
        f.poll()
        speaker, start, end, audio = q.get_nowait()
        self.assertAlmostEqual(start, 21 * 512 / SR, places=3)               # absolute track time
        self.assertGreater(f.rms_between(start, end), 0)                     # indexes shifted by base_chunk
        self.assertEqual(f.rms_between(0.0, 0.3), 0.0)                       # before the resume point: unknown

    def test_recording_active_asks_minutes(self):
        self.sb.minutes(recording=True)
        self.assertTrue(self.lt.recording_active(str(Path(os.environ["MEETING_COPILOT_BIN"]) / "minutes")))
        self.sb.minutes(recording=False)
        self.assertFalse(self.lt.recording_active(str(Path(os.environ["MEETING_COPILOT_BIN"]) / "minutes")))
        self.assertTrue(self.lt.recording_active("/nonexistent/minutes"))    # unknown: keep going


def synth_speech(path, text="Testing one two three. Four five six.", voice=None):
    """A 16 kHz mono WAV of spoken text via macOS `say`; None when unavailable."""
    aiff = path.with_suffix(".aiff")
    cmd = ["say", "-o", str(aiff)] + (["-v", voice] if voice else []) + [text]
    try:
        subprocess.run(cmd, check=True, capture_output=True, timeout=60)
        subprocess.run(["afconvert", "-f", "WAVE", "-d", "LEI16@16000", "-c", "1", str(aiff), str(path)],
                       check=True, capture_output=True, timeout=60)
    except (OSError, subprocess.SubprocessError):
        return None
    return path


class WholeProgram(unittest.TestCase):
    """live_transcriber.py as a child process on synthetic speech, with the fake MLX recognizers."""

    @classmethod
    def setUpClass(cls):
        cls.sb = Sandbox(extra='[asr]\nprimary_model = "fake/primary"\nfallback_model = "fake/fallback"\n')
        cls.sb.cache_models("fake/primary", "fake/fallback")      # "installed": the transcriber never downloads
        cls.speech = synth_speech(cls.sb.root / "speech.wav")
        cls.env = {**cls.sb.env, "PYTHONPATH": str(FAKES_PY), "MC_FAKE_ASR_LOG": str(cls.sb.fake / "asr.log")}

    @classmethod
    def tearDownClass(cls):
        cls.sb.cleanup()

    def stems(self, base, voice_level=1.0, system_level=1.0):
        import soundfile as sf
        audio, sr = sf.read(self.speech, dtype="int16")
        for stem, level in (("voice", voice_level), ("system", system_level)):
            data = (audio.astype(np.float32) * level).astype("<i2").tobytes()
            (Path(f"{base}.{stem}.wav")).write_bytes(header() + data)

    def run_transcriber(self, base, out, timeout=120):
        return subprocess.run([sys.executable, str(APP / "bin" / "live_transcriber.py"), f"{base}.wav", str(out)],
                              env=self.env, capture_output=True, text=True, timeout=timeout)

    def test_transcribes_both_stems_then_exits_when_the_recording_is_over(self):
        if not self.speech:
            self.skipTest("macOS `say` is not available")
        base = self.sb.root / "m1"
        self.stems(base, voice_level=1.0, system_level=0.6)
        self.sb.minutes(recording=False)                                     # Minutes already stopped
        out = self.sb.root / "m1.jsonl"
        r = self.run_transcriber(base, out)
        self.assertEqual(r.returncode, 0, r.stderr[-2000:])
        rows = [json.loads(l) for l in out.read_text().splitlines()]
        self.assertTrue(rows, r.stderr[-2000:])
        self.assertEqual({r["speaker"] for r in rows} >= {"其他人"}, True)
        self.assertTrue(all(set(r) >= {"ts", "start", "end", "speaker", "text", "engine"} for r in rows))
        self.assertTrue(all(r["engine"] in ("primary", "fallback") for r in rows))
        self.assertIn("primary 第", "".join(r["text"] for r in rows))        # the fake model's output
        self.assertTrue(any(r.get("who") for r in rows if r["speaker"] == "其他人"))   # grouped by voice
        self.assertTrue(out.with_name("m1.speakers.json").exists())
        self.assertFalse(out.with_suffix(".pid").exists())
        self.assertIn("recording finished", r.stderr)

    def test_mic_echo_of_the_call_is_dropped(self):
        if not self.speech:
            self.skipTest("macOS `say` is not available")
        base = self.sb.root / "m2"
        self.stems(base, voice_level=0.2, system_level=1.0)                   # the mic only hears the speaker
        self.sb.minutes(recording=False)
        out = self.sb.root / "m2.jsonl"
        r = self.run_transcriber(base, out)
        self.assertEqual(r.returncode, 0, r.stderr[-2000:])
        self.assertIn("call echo", r.stderr)
        rows = [json.loads(l) for l in out.read_text().splitlines()]
        self.assertEqual([x for x in rows if x["speaker"] == "我"], [])

    def test_resume_skips_what_was_transcribed(self):
        if not self.speech:
            self.skipTest("macOS `say` is not available")
        base = self.sb.root / "m3"
        self.stems(base)
        self.sb.minutes(recording=False)
        out = self.sb.root / "m3.jsonl"
        r = self.run_transcriber(base, out)
        rows = [json.loads(l) for l in out.read_text().splitlines()]
        self.assertTrue(rows, r.stderr)
        n = len(rows)
        r = self.run_transcriber(base, out)                                  # again, nothing new in the stems
        self.assertEqual(r.returncode, 0, r.stderr[-2000:])
        self.assertIn("resuming after", r.stderr)
        self.assertEqual(len(out.read_text().splitlines()), n)               # no duplicates
        if out.with_name("m3.speakers.json").exists():
            self.assertIn("speakers restored", r.stderr)

    def test_missing_model_exits_at_once_without_downloading(self):
        """A missing model must not be downloaded mid-meeting (that can take minutes with nothing transcribed)."""
        base = self.sb.root / "m6"
        self.stems(base) if self.speech else None
        out = self.sb.root / "m6.jsonl"
        self.sb.write_config(extra='[asr]\nprimary_model = "someone/not-installed"\nfallback_model = "fake/fallback"\n')
        try:
            t0 = time.time()
            r = self.run_transcriber(base, out, timeout=60)
            self.assertEqual(r.returncode, 5, r.stderr[-2000:])
            self.assertLess(time.time() - t0, 30)
            self.assertIn("not downloading it during a meeting", r.stderr)
            self.assertFalse((self.sb.fake / "asr.log").exists() and "not-installed" in (self.sb.fake / "asr.log").read_text())
        finally:
            self.sb.write_config(extra='[asr]\nprimary_model = "fake/primary"\nfallback_model = "fake/fallback"\n')

    def test_another_transcriber_owns_the_transcript(self):
        base = self.sb.root / "m4"
        self.stems(base) if self.speech else None
        out = self.sb.root / "m4.jsonl"
        out.with_suffix(".pid").write_text(str(os.getpid()))
        r = self.run_transcriber(base, out)
        self.assertEqual(r.returncode, 4)
        self.assertIn("already working", r.stderr)

    def test_idle_while_minutes_still_records_exits_3(self):
        if not self.speech:
            self.skipTest("macOS `say` is not available")
        base = self.sb.root / "m5"
        self.stems(base)
        self.sb.minutes(recording=True)
        out = self.sb.root / "m5.jsonl"
        env = {**self.env, "MC_FAKE_MAX_IDLE": "1"}
        r = subprocess.run([sys.executable, "-c",
                            "import sys; src = sys.argv[3]; sys.argv = ['live_transcriber.py', sys.argv[1], sys.argv[2]]; "
                            "import importlib.util; spec = importlib.util.spec_from_file_location('lt', src); "
                            "m = importlib.util.module_from_spec(spec); spec.loader.exec_module(m); "
                            "m.MAX_IDLE_SECS = 1.5; m.main()",
                            f"{base}.wav", str(out), str(APP / "bin" / "live_transcriber.py")],
                           env=env, capture_output=True, text=True, timeout=120)
        self.assertEqual(r.returncode, 3, r.stderr[-2000:])
        self.assertIn("idle too long", r.stderr)


if __name__ == "__main__":
    unittest.main()
