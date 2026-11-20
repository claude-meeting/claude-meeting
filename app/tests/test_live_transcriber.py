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



class RoomSpeaker(unittest.TestCase):
    """Who said a microphone line in room mode (live_transcriber.room_speaker)."""

    @classmethod
    def setUpClass(cls):
        cls.sb = Sandbox()
        sys.path.insert(0, str(FAKES_PY))
        sys.path.insert(0, str(APP / "bin"))
        import live_transcriber
        cls.lt = live_transcriber

    @classmethod
    def tearDownClass(cls):
        cls.sb.cleanup()

    def voice(self, enrolled):
        class Voice:
            def __init__(self, v):
                self.enrolled = np.array(v, dtype=np.float32)

            def embed(self, audio):
                return np.array([1.0, 0.0], dtype=np.float32) if audio.mean() > 0 else np.array([0.0, 1.0], dtype=np.float32)
        return Voice(enrolled)

    def test_the_voiceprint_decides_and_others_get_their_vector(self):
        lt, me = self.lt, self.voice([1.0, 0.0])
        loud, other = np.full(16000, 0.1, dtype=np.float32), np.full(16000, -0.1, dtype=np.float32)
        self.assertEqual(lt.room_speaker(loud, me, 0.4, None)[:2][0], "我")
        speaker, vector, keep, by_voice = lt.room_speaker(other, me, 0.4, None)
        self.assertEqual((speaker, keep, by_voice), ("其他人", None, True))
        self.assertIsNotNone(vector)                                            # embedded once, grouped with it

    def test_a_short_line_goes_with_the_previous_one_on_the_microphone(self):
        lt, me = self.lt, self.voice([1.0, 0.0])
        short = np.full(4000, -0.1, dtype=np.float32)                           # 0.25 s: no score possible
        # Nothing before it (or a pause of more than KEEP_GAP_SECS): no sign it is the user.
        self.assertEqual(lt.room_speaker(short, me, 0.4, None), ("其他人", None, None, False))
        self.assertEqual(lt.room_speaker(short, me, 0.4, "我"), ("我", None, None, False))
        self.assertEqual(lt.room_speaker(short, me, 0.4, "B"), ("其他人", None, "B", False))
        # After someone else's line that got no letter (too short to group, or grouping off): not the user.
        self.assertEqual(lt.room_speaker(short, me, 0.4, "其他人"), ("其他人", None, None, False))

    def test_someone_else_s_line_too_short_to_group_goes_with_the_previous_speaker(self):
        # 0.5 to 1 s: the voiceprint can tell it is not the user, but it is too short to group by voice.
        lt, me = self.lt, self.voice([1.0, 0.0])
        line = np.full(12000, -0.1, dtype=np.float32)                           # 0.75 s
        self.assertEqual(lt.room_speaker(line, me, 0.4, "B"), ("其他人", None, "B", True))
        self.assertEqual(lt.room_speaker(line, me, 0.4, "我")[2], None)            # grouped (no letter) as usual
        self.assertEqual(lt.room_speaker(np.full(12000, 0.1, dtype=np.float32), me, 0.4, "B")[:1], ("我",))

    def test_short_lines_move_with_their_turn(self):
        mic = self.lt.MicHistory()
        for start, who in ((5.0, "B"), (12.0, "B"), (13.0, "C"), (20.0, "B")):
            mic.line(start, start + 0.3, who, kept=True)
        mic.line(14.0, 16.0, "B")                                               # has its own letter
        self.assertEqual(mic.split("B", "E", [10.0, 14.0, 16.0], 16.0), [10.0, 12.0, 14.0])
        self.assertEqual(mic.kept, [(5.0, "B"), (12.0, "E"), (13.0, "C"), (20.0, "B")])
        self.assertEqual(mic.previous(16.5), "E")                                # the last line moved too
        for i in range(60):
            mic.line(100.0 + i, 100.5 + i, "B", kept=True)
        self.assertEqual(len(mic.kept), mic.KEPT_MAX)

    def test_a_wall_clock_time_maps_to_the_track_second_read_then(self):
        from array import array
        f = self.lt.TrackFollower.__new__(self.lt.TrackFollower)
        f.anchor_samples, f.anchor_times, f.samples_read = array("q", [16000, 32000]), array("d", [100.0, 101.0]), 32000
        self.assertAlmostEqual(f.seconds_at(100.5), 1.5)
        self.assertEqual(f.seconds_at(50.0), 0.0)
        self.assertEqual(f.seconds_at(200.0), 2.0)                               # after the last read: what was read
        f.anchor_samples, f.anchor_times = array("q", [48000, 64000]), array("d", [99.0, 103.0])   # a stall
        self.assertEqual(f.seconds_at(100.0), 3.0)                               # not before what was read at 99

    def test_the_previous_line_counts_only_if_it_was_just_said(self):
        mic = self.lt.MicHistory()
        self.assertIsNone(mic.previous(1.0))
        mic.line(1.0, 3.0, "B")
        self.assertEqual(mic.previous(4.0), "B")
        self.assertIsNone(mic.previous(3.0 + self.lt.KEEP_GAP_SECS + 1))

    def test_a_restart_rebuilds_the_microphone_lines_from_the_transcript(self):
        mic = self.lt.MicHistory()
        for row in [{"start": 1.0, "end": 3.0, "speaker": "我", "track": "voice"},
                    {"start": 3.5, "end": 3.8, "speaker": "其他人", "who": "B", "track": "voice", "kept": True},
                    {"start": 4.0, "end": 6.0, "speaker": "其他人", "who": "B", "track": "voice"},
                    {"start": 6.0, "end": 9.0, "speaker": "其他人", "who": "C", "track": "system"},
                    {"relabel": {"from": "B", "to": "E", "starts": [3.5, 4.0]}}]:
            mic.add(row)
        self.assertEqual((mic.start, mic.who, mic.kept), (4.0, "E", [(3.5, "E")]))
        mic.add({"start": 7.0, "end": 8.0, "speaker": "其他人", "track": "voice"})
        self.assertEqual(mic.previous(8.5), "其他人")

    def test_a_room_waits_for_the_call_track_only_in_a_recording_with_a_call(self):
        lt = self.lt

        from array import array
        per_sec = 16000 / lt.VAD_CHUNK

        class Call:
            def __init__(self, loud_secs, written_ago, quiet_after=0):
                self.chunk_rms = array("f", [0.1] * int(loud_secs * per_sec) + [0.0] * int(quiet_after * per_sec))
                self.last_growth = 100.0 - written_ago

        self.assertFalse(lt.room_waits_for_call(Call(0, 0, 600), 100.0))       # in person, a phone on speaker
        self.assertFalse(lt.room_waits_for_call(Call(2, 0), 100.0))            # a few notification sounds
        self.assertTrue(lt.room_waits_for_call(Call(30, 0), 100.0))            # a call
        self.assertTrue(lt.room_waits_for_call(Call(30, 5, 60), 100.0))        # a pause in it
        self.assertFalse(lt.room_waits_for_call(Call(30, lt.CALL_GONE_SECS + 1), 100.0))  # its track stopped
        self.assertFalse(lt.room_waits_for_call(Call(30, 0, lt.CALL_WINDOW_SECS), 100.0))  # long ago: sounds add up
        self.assertTrue(lt.room_waits_for_call(Call(30, 0, lt.CALL_WINDOW_SECS - 6), 100.0))  # 6 s still in the window

    def test_every_switch_applies_to_what_was_said_after_it(self):
        import os
        lt = self.lt

        class Mic:
            def seconds_at(self, wall):
                return wall - 1000.0                                             # the track began at 1000

        out = self.sb.root / "switch.jsonl"
        mode_file = out.with_suffix(".mode")

        def write(mode, at):
            mode_file.write_text(mode + "\n")
            os.utime(mode_file, (at, at))

        write("call", 1000.0)
        watch = lt.ModeWatch(out, Mic())
        watch.check()
        write("room", 1010.0)
        watch.check()                                                            # the main loop sees it
        write("call", 1015.0)                                                    # and back, before any line
        self.assertEqual(watch.at(5.0), "call")
        self.assertEqual(watch.at(12.0), "room")                                 # said during the room
        self.assertEqual(watch.at(16.0), "call")
        self.assertEqual(lt.ModeWatch(self.sb.root / "none.jsonl", Mic()).at(1.0), "call")
        write("room", 1018.0)
        self.assertEqual(watch.at(19.0), "room")
        mode_file.write_bytes(b"\xff\xfe")                                      # not text: an unknown mode, a call
        os.utime(mode_file, (1020.0, 1020.0))
        self.assertEqual(watch.at(21.0), "call")
        self.assertEqual(lt.read_mode(out), "call")                             # at startup too

    def test_a_resumed_call_track_knows_its_levels_before_the_resume_point(self):
        lt = self.lt
        sys.path.insert(0, str(APP / "lib"))
        import asr
        pcm = np.concatenate([np.full(16000 * 6, 3000, dtype="<i2"), np.zeros(16000 * 2, dtype="<i2")])
        path = self.sb.root / "scan.system.wav"
        path.write_bytes(lt.HEADER_BYTES * b"\0" + pcm.tobytes() + b"\0")            # an odd byte mid-write

        def scanned(resume_secs):
            f = lt.TrackFollower.__new__(lt.TrackFollower)
            f.path, f.SCAN_BLOCK = str(path), lt.VAD_CHUNK * 7                  # several blocks
            f.samples_read = int(resume_secs * 16000) // lt.VAD_CHUNK * lt.VAD_CHUNK
            f.scan()
            return f

        f = scanned(8)
        self.assertEqual((f.base_chunk, len(f.chunk_rms)), (0, f.samples_read // lt.VAD_CHUNK))
        self.assertAlmostEqual(f.chunk_rms[3], asr.rms(pcm[3 * 512:4 * 512].astype(np.float32) / 32768), places=5)
        self.assertGreater(f.rms_between(1.0, 3.0), 0.05)                       # the call, before the resume point
        self.assertEqual(f.rms_between(6.5, 7.5), 0.0)
        self.assertAlmostEqual(f.last_growth, path.stat().st_mtime, places=3)
        f = scanned(18)                                                          # a stem shorter than the resume point
        self.assertEqual(len(f.chunk_rms), f.samples_read // lt.VAD_CHUNK)
        self.assertGreater(f.rms_between(4.0, 5.0), 0.05)                       # levels stay where they were said
        self.assertEqual(f.rms_between(12.0, 14.0), 0.0)

    def test_without_a_voiceprint_everyone_is_someone_else(self):
        self.assertEqual(self.lt.room_speaker(np.zeros(16000, dtype=np.float32), None, 0.4, "我"),
                         ("其他人", None, None, False))


class RoomMode(unittest.TestCase):
    """claude-meeting/claude-meeting#6: an in-person meeting or a phone on speaker. Everyone is on the microphone;
    in room mode the voices other than the user's are kept and grouped, where call mode drops them."""

    @classmethod
    def setUpClass(cls):
        cls.sb = Sandbox(extra='[asr]\nprimary_model = "fake/primary"\nfallback_model = "fake/fallback"\n')
        cls.sb.cache_models("fake/primary", "fake/fallback")
        cls.speech = synth_speech(cls.sb.root / "speech.wav")
        cls.env = {**cls.sb.env, "PYTHONPATH": str(FAKES_PY), "MC_FAKE_ASR_LOG": str(cls.sb.fake / "asr.log")}

    @classmethod
    def tearDownClass(cls):
        cls.sb.cleanup()

    def setUp(self):
        if not self.speech:
            self.skipTest("macOS `say` is not available")
        self.sb.minutes(recording=False)

    def run_room(self, name, mode, voiceprint=None, voice_level=0.2, system_level=0.0):
        """The speech on the microphone (quiet, as someone across the table); the call's audio silent, or with
        the same speech at `system_level`."""
        import soundfile as sf
        audio, _ = sf.read(self.speech, dtype="int16")
        base = self.sb.root / name
        Path(f"{base}.voice.wav").write_bytes(header() + (audio.astype(np.float32) * voice_level).astype("<i2").tobytes())
        Path(f"{base}.system.wav").write_bytes(header() + (audio.astype(np.float32) * system_level).astype("<i2").tobytes())
        out = self.sb.root / f"{name}.jsonl"
        if mode:
            out.with_suffix(".mode").write_text(mode)
        vp = self.sb.home / ".config" / "meeting-copilot" / "voiceprint.json"      # where the engine looks
        vp.unlink(missing_ok=True)
        if voiceprint is not None:
            vp.write_text(json.dumps({"model": "fake", "vector": list(voiceprint)}))
        r = subprocess.run([sys.executable, str(APP / "bin" / "live_transcriber.py"), f"{base}.wav", str(out)],
                           env=self.env, capture_output=True, text=True, timeout=120)
        self.assertEqual(r.returncode, 0, r.stderr[-2000:])
        rows = [json.loads(l) for l in out.read_text().splitlines()] if out.exists() else []
        return [x for x in rows if "speaker" in x], r.stderr

    def speech_vector(self, level=0.2):
        """The fake speaker model's vector of this speech at this level: a voiceprint that matches it."""
        import soundfile as sf
        sys.path.insert(0, str(FAKES_PY))
        import sherpa_onnx
        audio, _ = sf.read(self.speech, dtype="float32")
        stream = sherpa_onnx.SpeakerEmbeddingExtractor(None).create_stream()
        stream.accept_waveform(16000, audio * level)
        v = np.array(sherpa_onnx.SpeakerEmbeddingExtractor(None).compute(stream))
        return v / np.linalg.norm(v)

    def test_call_mode_drops_a_voice_that_is_not_the_user(self):
        rows, log = self.run_room("c1", None, voiceprint=[0.0, 0.0, 1.0, 0.0])
        self.assertEqual(rows, [])
        self.assertIn("mode: call", log)
        self.assertIn("dropped (voiceprint", log)

    def test_room_mode_keeps_and_groups_a_voice_that_is_not_the_user(self):
        rows, log = self.run_room("r1", "room", voiceprint=[0.0, 0.0, 1.0, 0.0])
        self.assertIn("mode: room", log)
        self.assertTrue(rows, log[-2000:])
        self.assertTrue(all(x["speaker"] == "其他人" and x["track"] == "voice" for x in rows), rows)
        self.assertTrue(all(x.get("who") for x in rows), rows)                # grouped: A, B, ...
        self.assertTrue((self.sb.root / "r1.speakers.json").exists())

    def test_room_mode_still_knows_the_user_by_voice(self):
        rows, log = self.run_room("r2", "room", voiceprint=self.speech_vector())
        self.assertTrue(rows, log[-2000:])
        self.assertTrue(all(x["speaker"] == "我" and not x.get("who") for x in rows), rows)

    def test_in_a_room_the_user_s_voice_is_kept_over_a_loud_call(self):
        # A room with others on a call: the user talking while the call is loud is not call echo (voiceprint first,
        # as in call mode); someone else that quiet under the call is.
        rows, log = self.run_room("r5", "room", voiceprint=self.speech_vector(), system_level=1.0)
        mic = [x for x in rows if x["track"] == "voice"]
        self.assertTrue(mic and all(x["speaker"] == "我" for x in mic), log[-2000:])
        self.assertNotIn("call echo, room", log)
        rows, log = self.run_room("r6", "room", voiceprint=[0.0, 0.0, 1.0, 0.0], system_level=1.0)
        self.assertIn("call echo, room", log)

    def test_room_mode_without_a_voiceprint_counts_everyone_as_others(self):
        rows, log = self.run_room("r3", "room", voiceprint=None)
        self.assertIn("room mode without the user's voiceprint", log)
        self.assertTrue(rows and all(x["speaker"] == "其他人" for x in rows), rows)

    def test_a_resumed_room_transcript_continues_each_track(self):
        rows, _ = self.run_room("r4", "room", voiceprint=[0.0, 0.0, 1.0, 0.0])
        n = len(rows)
        out = self.sb.root / "r4.jsonl"
        r = subprocess.run([sys.executable, str(APP / "bin" / "live_transcriber.py"), f"{self.sb.root / 'r4'}.wav",
                            str(out)], env=self.env, capture_output=True, text=True, timeout=120)
        self.assertEqual(r.returncode, 0, r.stderr[-2000:])
        self.assertEqual(len([l for l in out.read_text().splitlines() if '"speaker"' in l]), n)   # no duplicates


if __name__ == "__main__":
    unittest.main()
