"""Tests for the command-line tools in app/tools and app/bin (run as child processes with the fake
recognizers and the fake Minutes): voiceprint enrollment, offline transcription, the validation-set
helpers, screen video, retention, config_cli, copilot-known and copilot-name."""
import json
import os
import subprocess
import sys
import time
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from harness import APP, FAKES_PY, FIXTURES, Sandbox  # noqa: E402
from test_live_transcriber import header, synth_speech  # noqa: E402

PY = sys.executable


class ToolBase(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.sb = Sandbox(extra='[asr]\nprimary_model = "fake/primary"\nfallback_model = "fake/fallback"\n')
        cls.sb.cache_models("fake/primary", "fake/fallback")
        cls.env = {**cls.sb.env, "PYTHONPATH": str(FAKES_PY)}
        # Normal, continuous speech: no deliberate pauses between sentences. (An earlier version inserted
        # 0.7 s silences, which only hid a real problem: enrollment failed on continuous speech without them.)
        cls.speech = synth_speech(cls.sb.root / "speech.wav", " ".join(
            f"This is sentence number {n} of the synthetic test recording for the voiceprint." for n in range(1, 8)))

    @classmethod
    def tearDownClass(cls):
        cls.sb.cleanup()

    def run_tool(self, *args, timeout=180, env=None):
        return subprocess.run([PY, *map(str, args)], env=env or self.env, capture_output=True, text=True, timeout=timeout)

    def stems(self, base):
        """A finished Minutes recording: proper WAV headers (the live transcriber's zero-size ones are for
        files still being written)."""
        import numpy as np
        import soundfile as sf
        audio, sr = sf.read(self.speech, dtype="int16")
        sf.write(f"{base}.voice.wav", audio, sr, subtype="PCM_16")
        sf.write(f"{base}.system.wav", (audio.astype(np.float32) * 0.5).astype("int16"), sr, subtype="PCM_16")


class Enroll(ToolBase):
    def test_enroll_from_wav_and_from_a_meeting_note(self):
        if not self.speech:
            self.skipTest("macOS `say` is not available")
        out = self.sb.root / "vp.json"
        r = self.run_tool(APP / "tools" / "enroll_voiceprint.py", self.speech, "--out", out)
        self.assertEqual(r.returncode, 0, r.stderr[-1500:])
        self.assertIn("enrolled", r.stdout)
        data = json.loads(out.read_text())
        self.assertEqual(len(data["vector"]), 4)                              # the fake extractor's size
        base = self.sb.root / "meeting-1"
        self.stems(base)
        Path(f"{base}.md").write_text("# meeting\n")
        r = self.run_tool(APP / "tools" / "enroll_voiceprint.py", f"{base}.md", "--out", out, "--max-segments", "3")
        self.assertEqual(r.returncode, 0, r.stderr[-1500:])
        self.assertIn("segment(s) of the user's speech", r.stdout)

    def test_too_little_speech_is_refused(self):
        silent = self.sb.root / "silence.wav"
        silent.write_bytes(header() + b"\0" * 32000)
        r = self.run_tool(APP / "tools" / "enroll_voiceprint.py", silent, "--out", self.sb.root / "x.json")
        self.assertNotEqual(r.returncode, 0)
        self.assertIn("s of speech found", r.stderr)

    def test_talking_without_pauses_is_enough(self):
        # About 20 s of talking without a break: enrollment must work on continuous speech.
        run_on = synth_speech(self.sb.root / "run-on.wav", " ".join(
            "and then we talked about the plan for the next release" for _ in range(6)))
        if not run_on:
            self.skipTest("macOS `say` is not available")
        out = self.sb.root / "vp-run-on.json"
        r = self.run_tool(APP / "tools" / "enroll_voiceprint.py", run_on, "--out", out)
        self.assertEqual(r.returncode, 0, r.stderr[-1500:])
        self.assertTrue(out.exists())


class TranscribeTracks(ToolBase):
    def test_merges_both_stems_by_speaker(self):
        if not self.speech:
            self.skipTest("macOS `say` is not available")
        base = self.sb.root / "meeting-2"
        self.stems(base)
        r = self.run_tool(APP / "tools" / "transcribe_tracks.py", base)
        self.assertEqual(r.returncode, 0, r.stderr[-1500:])
        out = Path(f"{base}.speakers.jsonl")
        rows = [json.loads(l) for l in out.read_text().splitlines()]
        self.assertTrue(rows)
        self.assertEqual(rows, sorted(rows, key=lambda x: x["start"]))
        self.assertTrue({"我", "其他人"} & {x["speaker"] for x in rows})
        self.assertIn("wrote", r.stdout)


class ValidationSet(ToolBase):
    def test_tokens_mer_judge_and_finalize(self):
        sys.path.insert(0, str(FAKES_PY))
        for m in ("asr", "mlx", "mlx.core", "mlx_audio", "mlx_audio.stt", "mlx_audio.stt.utils", "sherpa_onnx",
                  "voiceprint"):
            sys.modules.pop(m, None)
        try:
            vs = self.sb.import_module("validation_set", APP / "tools" / "validation_set.py")
        finally:
            sys.path.remove(str(FAKES_PY))
        self.assertEqual(vs.tokens("Hello 世界 ABC"), ["hello", "世", "界", "abc"])
        self.assertEqual(vs.mer("a b c", "a b c"), 0.0)
        self.assertAlmostEqual(vs.mer("a b c d", "a b"), 0.5)
        self.assertEqual(vs.mer("", ""), 0.0)
        self.assertEqual(vs.mmss("12:30"), 750.0)
        out = self.sb.root / "vs"
        (out / "clips").mkdir(parents=True)
        segs = [{"id": "s1", "start": 0.0, "end": 2.0, "speaker": "我", "clip": "clips/s1.wav",
                 "hyp": {"tea": "今天天氣很好", "qwen3": "今天天氣很好", "whisper": "今天天氣很好"}},
                {"id": "s2", "start": 2.0, "end": 5.5, "speaker": "其他人", "clip": "clips/s2.wav",
                 "hyp": {"tea": "排程功能上線", "qwen3": "排程功能下線", "whisper": "完全不同的話"}}]
        vs.judge(out, segs)
        written = [json.loads(l) for l in (out / "segments.jsonl").read_text().splitlines()]
        self.assertTrue(written[0]["agree"] and not written[1]["agree"])
        self.assertEqual(written[1]["best"], "tea")                          # closest to the other two
        review = (out / "review.md").read_text()
        self.assertIn("## s2", review)
        self.assertNotIn("## s1", review)
        (out / "review.md").write_text(review.replace("正確：排程功能上線", "正確：排程功能上線了"))

        class Args:
            dir = str(out)
        vs.finalize(Args())
        refs = [json.loads(l) for l in (out / "refs.jsonl").read_text().splitlines()]
        self.assertEqual([(r["id"], r["text"], r["source"]) for r in refs],
                         [("s1", "今天天氣很好", "consensus"), ("s2", "排程功能上線了", "human")])
        vs.rejudge(Args())                                                   # idempotent
        (out / "review.md").write_text(review.replace("正確：排程功能上線", "正確："))
        vs.finalize(Args())
        self.assertEqual(len((out / "refs.jsonl").read_text().splitlines()), 1)   # blank review line excluded


class ScreenVideo(ToolBase):
    def frames(self, folder, n=3):
        from PIL import Image
        folder.mkdir(parents=True, exist_ok=True)
        for i in range(n):
            Image.new("RGB", (32, 32), (i * 40, 0, 0)).save(folder / f"frame-{1000 + i * 1000}.jpg")

    def test_one_video_per_display_and_notes_link(self):
        root = self.sb.root / "frames"
        self.frames(root / "display-1")
        self.frames(root / "display-2")
        out = self.sb.root / "video" / "s.mp4"
        notes = self.sb.root / "notes.md"
        notes.write_text("# n\n")
        before = len(self.sb.calls("frames-to-video"))
        r = self.run_tool(APP / "bin" / "screen_video.py", root, out, notes)
        self.assertEqual(r.returncode, 0, r.stderr)
        made = sorted(p.name for p in out.parent.glob("*.mp4"))
        self.assertEqual(made, ["s-display-1.mp4", "s-display-2.mp4"])
        self.assertEqual(notes.read_text().count("🎞️"), 2)
        self.assertEqual(len(self.sb.calls("frames-to-video")) - before, 2)

    def test_flat_folder_and_too_few_frames(self):
        flat = self.sb.root / "flat"
        self.frames(flat, n=2)
        out = self.sb.root / "video2" / "f.mp4"
        r = self.run_tool(APP / "bin" / "screen_video.py", flat, out)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertTrue(out.exists())
        one = self.sb.root / "one"
        self.frames(one, n=1)
        r = self.run_tool(APP / "bin" / "screen_video.py", one, self.sb.root / "video3" / "o.mp4")
        self.assertFalse((self.sb.root / "video3" / "o.mp4").exists())


    def test_real_encoder(self):
        """Without the fake helper: the built frames-to-video, or ffmpeg where the helper is missing."""
        import shutil
        if not (APP / "bin" / "frames-to-video").exists() and not shutil.which("ffmpeg"):
            self.skipTest("neither frames-to-video nor ffmpeg is available")
        folder = self.sb.root / "real"
        self.frames(folder, n=3)
        out = self.sb.root / "video4" / "f.mp4"
        env = {k: v for k, v in self.env.items() if k != "MEETING_COPILOT_BIN"}
        r = self.run_tool(APP / "bin" / "screen_video.py", folder, out, env=env)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertTrue(out.exists() and out.stat().st_size > 0)             # a real video file
        self.assertIn("frames", r.stdout)

    def test_helper_failure_is_reported(self):
        folder = self.sb.root / "ff"
        self.frames(folder, n=3)
        self.sb.flag("video-fail")
        try:
            r = self.run_tool(APP / "bin" / "screen_video.py", folder, self.sb.root / "video5" / "f.mp4")
        finally:
            self.sb.flag("video-fail", False)
        self.assertEqual(r.returncode, 1)
        self.assertIn("fake encoder failure", r.stdout + r.stderr)


class Retention(ToolBase):
    def test_expired_files_go_to_the_trash_folder(self):
        meetings = self.sb.meetings
        old, new = meetings / "old.wav", meetings / "new.wav"
        old.write_bytes(b"x")
        new.write_bytes(b"x")
        os.utime(old, (0, 0))
        (meetings / "live").mkdir(exist_ok=True)
        stale_notes = meetings / "live" / "2020-01-01-x.md"
        stale_notes.write_text("old notes")
        os.utime(stale_notes, (0, 0))
        self.sb.write_config(extra='[retention]\naudio_days = 30\nlive_notes_days = 10\ntranscripts_days = 0\n'
                                   'voiceprints_days = 0\nscreenshots_days = 0\nvideo_days = 0\n')
        r = self.run_tool(APP / "bin" / "retention.py", "--dry-run")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("would trash", r.stdout)
        self.assertTrue(old.exists())
        r = self.run_tool(APP / "bin" / "retention.py")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertFalse(old.exists())
        self.assertFalse(stale_notes.exists())
        self.assertTrue(new.exists())
        trashed = list((self.sb.home / ".Trash").rglob("old.wav"))
        self.assertEqual(len(trashed), 1)


class ConfigCliAndSmallTools(ToolBase):
    def test_get_lists_microphones_without_the_rate_suffix(self):
        r = self.run_tool(APP / "tools" / "config_cli.py", "get")
        self.assertEqual(r.returncode, 0, r.stderr)
        data = json.loads(r.stdout.splitlines()[-1])
        self.assertEqual(data["microphones"], ["Built-in Microphone", "USB Mic [Pro]"])
        self.assertEqual(data["config"]["user"]["name"], "Alex")

    def test_set_runs_the_minutes_sync_for_audio_keys(self):
        minutes_cfg = self.sb.home / ".config" / "minutes" / "config.toml"
        minutes_cfg.parent.mkdir(parents=True, exist_ok=True)
        minutes_cfg.write_text("[recording]\n")
        r = self.run_tool(APP / "tools" / "config_cli.py", "set", json.dumps({"audio.mic_device": "USB Mic [Pro]"}))
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn('voice = "USB Mic [Pro]"', minutes_cfg.read_text())
        self.assertIn('mic_device = "USB Mic [Pro]"', self.sb.config_path.read_text())
        r = self.run_tool(APP / "tools" / "config_cli.py")
        self.assertNotEqual(r.returncode, 0)                                  # usage

    def test_the_models_key_and_a_test_call(self):
        from harness import FakeOpenAI
        cli = [PY, str(APP / "tools" / "config_cli.py")]

        def run(*args, stdin=None):
            r = subprocess.run([*cli, *args], input=stdin, env=self.env, capture_output=True, text=True, timeout=120)
            self.assertEqual(r.returncode, 0, r.stderr)
            return json.loads(r.stdout.splitlines()[-1])

        key_file = self.sb.home / ".config" / "meeting-copilot" / "api_key"
        self.assertFalse(run("get")["api_key_set"])
        run("set-key", stdin="sk-saved\n")
        self.assertEqual(key_file.read_text(), "sk-saved\n")
        self.assertEqual(key_file.stat().st_mode & 0o777, 0o600)              # only the user reads it
        got = run("get")
        self.assertTrue(got["api_key_set"])
        self.assertNotIn("sk-saved", json.dumps(got))                         # the key itself is never handed back
        fake = FakeOpenAI(reply="OK")
        try:
            typed = json.dumps({"provider": "openai", "base_url": fake.url, "model": "big"})   # not saved yet
            self.assertEqual(run("llm-test", typed), {"ok": True, "reply": "OK", "model": "big"})
            self.assertEqual(fake.requests[-1]["auth"], "Bearer sk-saved")
            run("llm-test", typed, "--key-stdin", stdin="sk-typed\n")
            self.assertEqual(fake.requests[-1]["auth"], "Bearer sk-typed")
            both = json.dumps({"provider": "openai", "base_url": fake.url, "model": "big", "fast_model": "small"})
            self.assertTrue(run("llm-test", both)["ok"])                      # the faster model is tried too
            self.assertEqual([r["body"]["model"] for r in fake.requests[-2:]], ["big", "small"])
            fake.status = 401                                                 # the service is asked first: no call spent
            self.assertEqual(run("llm-test", typed), {"ok": False, "error": "rejected"})
            fake.status = 404                                                 # no such model there
            failed = run("llm-test", typed)
            self.assertIn("(404)", failed["error"])
            self.assertEqual((failed["ok"], failed["model"]), (False, "big"))  # which model it failed on
        finally:
            fake.close()
        self.assertEqual(run("llm-test", json.dumps({"provider": "openai", "base_url": ""})), {"ok": False, "error": "no_url"})
        self.assertTrue(run("llm-test")["ok"])                                # the saved settings: Claude Code (a fake here)
        run("set-key", stdin="")
        self.assertFalse(key_file.exists())

    def test_copilot_known_and_name(self):
        known = self.sb.home / "known.txt"
        r = subprocess.run([str(APP / "bin" / "copilot-known"), "gRPC", "Kubernetes"], env=self.env,
                           capture_output=True, text=True)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(known.read_text().splitlines(), ["gRPC", "Kubernetes"])
        r = subprocess.run([str(APP / "bin" / "copilot-known"), "gRPC"], env=self.env, capture_output=True, text=True)
        self.assertEqual(known.read_text().splitlines(), ["gRPC", "Kubernetes"])          # no duplicates
        r = subprocess.run([str(APP / "bin" / "copilot-known")], env=self.env, capture_output=True, text=True)
        self.assertEqual(r.stdout.split(), ["gRPC", "Kubernetes"])
        live = self.sb.state / "live"
        live.mkdir(exist_ok=True)
        transcript = live / "rec-1.jsonl"
        transcript.write_text("")
        (live / "rec-1.speakers.json").write_text(json.dumps({"A": {"vector": [0.9, 0.1, 0, 0], "count": 6}}))
        r = subprocess.run([str(APP / "bin" / "copilot-name"), "--json"], env=self.env, capture_output=True, text=True)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(json.loads(r.stdout)["speakers"][0]["who"], "A")
        r = subprocess.run([str(APP / "bin" / "copilot-name"), "A", "小 明"], env=self.env, capture_output=True, text=True)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("A = 小 明", r.stdout)
        self.assertEqual(json.loads((live / "rec-1.names.json").read_text()), {"A": "小 明"})
        self.assertIn("小 明", json.loads(self.sb.people.read_text()))
        r = subprocess.run([str(APP / "bin" / "copilot-name"), "Z", "x"], env=self.env, capture_output=True, text=True)
        self.assertNotEqual(r.returncode, 0)
        self.assertIn("speakers so far: A", r.stderr)


class WatchDownload(unittest.TestCase):
    """app/tools/watch_download.py: install.sh's model downloads stop when they make no progress (a stalled
    download used to hold the setup guide with no way out but killing the process)."""
    TOOL = APP / "tools" / "watch_download.py"

    def setUp(self):
        self.sb = Sandbox()
        self.dir = self.sb.root / "dl"
        self.dir.mkdir()

    def tearDown(self):
        self.sb.cleanup()

    def watch(self, *args, script, timeout=30):
        return subprocess.run([PY, str(self.TOOL), "--poll", "0.1", *args, "--", "/bin/sh", "-c", script],
                              env=self.sb.env, capture_output=True, text=True, timeout=timeout)

    def test_the_exit_code_and_output_of_the_command_are_kept(self):
        log = self.sb.root / "download.log"
        r = self.watch("--watch", str(self.dir), "--log", str(log), script="echo fetched; echo oops >&2; exit 3")
        self.assertEqual(r.returncode, 3)
        self.assertEqual(r.stdout, "")                                      # the command's output goes to the log
        self.assertEqual(log.read_text().split(), ["fetched", "oops"])
        self.assertEqual(self.watch(script="true").returncode, 0)

    def test_a_slow_download_that_keeps_growing_is_not_stopped(self):
        # 2 s of downloading with a 1 s stall limit: new data every 0.2 s keeps it alive; progress is reported.
        script = f'for i in 1 2 3 4 5 6 7 8 9 10; do head -c 300000 /dev/zero >> "{self.dir}/blob.incomplete"; ' \
                 'sleep 0.2; done'
        r = self.watch("--stall", "1", "--every", "0.5", "--watch", str(self.dir), script=script)
        self.assertEqual(r.returncode, 0, r.stdout)
        self.assertIn("MB downloaded", r.stdout)
        self.assertNotIn("no download progress", r.stdout)

    def fake_nettop(self, growing):
        """A stand-in for macOS's nettop: the command's received bytes grow on every call, or stay put."""
        counter = self.sb.root / "nettop.count"
        script = self.sb.root / "nettop"
        step = 1000000 if growing else 0
        script.write_text(f'#!/bin/sh\nn=$(cat "{counter}" 2>/dev/null || echo 0); n=$((n + {step}))\n'
                          f'echo "$n" > "{counter}"\necho ",bytes_in,"\necho "python3.1.4242,$n,"\n'
                          f'echo "tcp4 192.0.2.10:50000<->198.51.100.7:443,$n,"\n')
        script.chmod(0o755)
        return {**self.sb.env, "MC_NETTOP": str(script)}

    def test_data_from_the_network_counts_as_progress_while_the_files_stay_the_same(self):
        # Xet transfers write a block only when it is complete: minutes can pass with no file change while
        # data keeps arriving. Seen on a real download: 200 s without a file change, then +1.3 GB.
        for growing, code in ((True, 0), (False, 124)):
            r = subprocess.run([PY, str(self.TOOL), "--poll", "0.1", "--stall", "1", "--every", "0.5", "--watch",
                                str(self.dir), "--", "/bin/sh", "-c", "sleep 3"],
                               env=self.fake_nettop(growing), capture_output=True, text=True, timeout=30)
            self.assertEqual(r.returncode, code, r.stdout)
            if growing:
                self.assertIn("MB downloaded", r.stdout)

    def test_received_bytes_survive_closed_and_reused_connections(self):
        # nettop lists only open connections; on a real download its per-process number sat below an earlier
        # peak for 6 minutes while data kept arriving, and the download was stopped by mistake.
        import importlib.util
        spec = importlib.util.spec_from_file_location("watch_download_t", self.TOOL)
        w = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(w)
        listing = self.sb.root / "nettop.txt"
        script = self.sb.root / "nettop"
        script.write_text(f'#!/bin/sh\ncat "{listing}"\n')
        script.chmod(0o755)
        w.NETTOP = str(script)
        counter = w.NetCounter(os.getpid())
        a, b = "tcp4 192.0.2.10:50001<->198.51.100.7:443", "tcp4 192.0.2.10:50002<->198.51.100.7:443"
        for rows, total in (([(a, 100), (b, 50)], 150),      # two connections
                            ([(a, 300)], 350),                # b closed: its 50 still count
                            ([(a, 20)], 370),                 # a new connection on a's port
                            ([], 370)):                       # all closed
            listing.write_text(",bytes_in,\npython3.1.42,999,\n" + "".join(f"{n},{c},\n" for n, c in rows))
            self.assertEqual(counter.total(), total, rows)

    def test_a_block_written_into_a_file_created_at_full_size_counts_as_progress(self):
        f = self.dir / "blob.incomplete"
        script = (f'{PY} -c "open(\'{f}\', \'wb\').truncate(20_000_000)"; '
                  f'for i in 1 2 3 4 5 6 7 8 9 10; do dd if=/dev/urandom of="{f}" bs=300000 count=1 seek=$i '
                  'conv=notrunc 2>/dev/null; sleep 0.25; done')
        r = self.watch("--stall", "1", "--watch", str(self.dir), script=script)
        self.assertEqual(r.returncode, 0, r.stdout)
        self.assertEqual(f.stat().st_size, 20_000_000)                     # the size never changed

    def test_a_stalled_download_is_stopped_with_its_children(self):
        pidfile = self.sb.root / "child.pid"
        start = time.monotonic()
        r = self.watch("--stall", "0.5", "--watch", str(self.dir),
                       script=f'head -c 1000 /dev/zero > "{self.dir}/part"; sleep 60 & echo $! > "{pidfile}"; wait')
        self.assertEqual(r.returncode, 124)
        self.assertLess(time.monotonic() - start, 15)
        self.assertIn("no download progress for 0.5 s: stopped. Press Install again to resume", r.stdout)
        self.assert_gone(int(pidfile.read_text()))
        self.assertEqual((self.dir / "part").stat().st_size, 1000)          # the partial download is kept

    def test_stopping_the_tool_stops_the_download(self):
        import signal
        pidfile = self.sb.root / "child.pid"
        proc = subprocess.Popen([PY, str(self.TOOL), "--poll", "0.1", "--", "/bin/sh", "-c",
                                 f'sleep 60 & echo $! > "{pidfile}"; wait'], env=self.sb.env)
        self.assertTrue(self.sb.wait_for(lambda: pidfile.exists() and pidfile.read_text().strip()))
        proc.send_signal(signal.SIGTERM)
        self.assertEqual(proc.wait(timeout=20), 128 + signal.SIGTERM)
        self.assert_gone(int(pidfile.read_text()))

    def test_the_hugging_face_cache_is_watched(self):
        r = subprocess.run([PY, "-c", "import sys; sys.path.insert(0, sys.argv[1]); import watch_download as w; "
                                      "print('\\n'.join(w.hf_cache_dirs()))", str(APP / "tools")],
                           env=self.sb.env, capture_output=True, text=True)
        self.assertIn(self.sb.env["HF_HUB_CACHE"], r.stdout.splitlines(), r.stderr)
        r = subprocess.run([PY, str(self.TOOL)], env=self.sb.env, capture_output=True, text=True)
        self.assertEqual(r.returncode, 2)                                   # no command: usage
        self.assertIn("no command", r.stderr)

    def assert_gone(self, pid):
        def gone():
            try:
                os.kill(pid, 0)
            except ProcessLookupError:
                return True
            return False
        self.assertTrue(self.sb.wait_for(gone, timeout=10), f"process {pid} still runs")


if __name__ == "__main__":
    unittest.main()
