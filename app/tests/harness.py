"""Test sandbox for the meeting-copilot engine.

A Sandbox is a throwaway HOME, state folder and config, plus the fake executables in tests/fakes/bin
(`minutes`, `claude`, `mic-users`, `launchctl`, `osascript`...). The engine finds them through
MEETING_COPILOT_BIN and PATH, keeps its state under MEETING_COPILOT_STATE and reads MEETING_COPILOT_CONFIG,
so the real `autopilot.py` can run in-process (import_autopilot) or as a child process (run_autopilot)
without touching Minutes, Claude, launchd or the user's files.
"""
import importlib.util
import json
import os
import shutil
import subprocess
import sys
import tempfile
import threading
import time
from pathlib import Path

TESTS = Path(__file__).resolve().parent
APP = TESTS.parent
FAKES_BIN = TESTS / "fakes" / "bin"
FAKES_PY = TESTS / "fakes" / "pymods"
FIXTURES = TESTS / "fixtures"
ENGINE_MODULES = ("config", "control", "i18n", "llm", "realtime", "speakers", "screen", "screen_speakers", "text", "asr", "voiceprint",
                  "tomledit", "autopilot", "live_transcriber", "retention", "screen_video", "setup_minutes",
                  "minutes_record")


def _auto_models():
    spec = importlib.util.spec_from_file_location("_config_for_harness", APP / "lib" / "config.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return sorted({*mod.AUTO_PRIMARY.values(), *mod.AUTO_FALLBACK.values()})


AUTO_MODELS = _auto_models()        # the default speech models, made to look installed in every sandbox

DEFAULT_CONFIG = """[user]
name = "Alex"
aliases = ["alex"]
language = "{language}"

[paths]
meetings_dir = "{meetings}"
minutes_app = "{minutes_app}"
claude_cli = "{claude}"

[autopilot]
start_after_secs = {start_after}
stop_after_secs = {stop_after}
cooldown_secs = 1

[copilot]
tick_secs = {tick_secs}
open_viewer = false

[explain]
model = "fake-haiku"
web_verify = {web_verify}

[screen]
enabled = {screen}
send_images = true
video = true

[voice]
people_file = "{people}"

[privacy]
consent_reminder = {consent}
store_voiceprints = true
{extra}
"""


class FakeClock:
    """Replaces the `time` module inside autopilot: sleep() advances the clock instead of waiting, so a
    main loop that waits START_AFTER_SECS for the microphone runs in milliseconds."""

    def __init__(self, start=None):
        self.t = start or time.time()
        self.real_sleep = 0.002        # let fake subprocesses and threads make progress
        self.events = []               # (when, action): changes in the middle of one run of main()

    def at(self, secs, action):
        """Run `action` once the clock is `secs` past now: main()'s own state lasts only one run."""
        self.events.append((self.t + secs, action))

    def time(self):
        return self.t

    def sleep(self, secs):
        self.t += secs
        for event in [e for e in self.events if e[0] <= self.t]:
            self.events.remove(event)
            event[1]()
        time.sleep(self.real_sleep)

    def monotonic(self):
        return self.t

    def __getattr__(self, name):          # strftime etc.
        return getattr(time, name)


class Sandbox:
    def __init__(self, language="zh", start_after=4, stop_after=6, tick_secs=5, screen=True, consent=False,
                 web_verify=False, extra=""):
        self.root = Path(tempfile.mkdtemp(prefix="mc-test-"))
        self.home = self.root / "home"
        self.state = self.root / "state"
        self.fake = self.root / "fake"
        self.meetings = self.home / "meetings"
        for d in (self.home / ".minutes", self.meetings, self.home / ".config" / "meeting-copilot", self.state,
                  self.fake):
            d.mkdir(parents=True)
        self.config_path = self.home / ".config" / "meeting-copilot" / "config.toml"
        self.minutes_app = self.root / "Minutes.app"
        (self.minutes_app / "Contents" / "MacOS").mkdir(parents=True)
        (self.minutes_app / "Contents" / "MacOS" / "minutes").symlink_to(FAKES_BIN / "minutes")
        self.people = self.home / ".config" / "meeting-copilot" / "people.json"
        self.write_config(language=language, start_after=start_after, stop_after=stop_after, tick_secs=tick_secs,
                          screen=screen, consent=consent, web_verify=web_verify, extra=extra)
        self.env = {
            **{k: v for k, v in os.environ.items() if not k.startswith("MEETING_COPILOT")},
            "HOME": str(self.home),
            "MEETING_COPILOT_CONFIG": str(self.config_path),
            "MEETING_COPILOT_STATE": str(self.state),
            "MEETING_COPILOT_BIN": str(FAKES_BIN),
            "MEETING_COPILOT_KNOWN_TERMS": str(self.home / "known.txt"),
            "MC_FAKE_DIR": str(self.fake),
            "PATH": f"{FAKES_BIN}:/usr/bin:/bin:/usr/sbin:/sbin",
            "HF_HUB_CACHE": str(self.home / ".cache" / "huggingface" / "hub"),
            "MEETING_COPILOT_MODELS": str(self.root / "models"),   # not the developer's own app/models
        }
        self.cache_models(*AUTO_MODELS)
        self.minutes()

    # ---------------------------------------------------------------- configuration
    def write_config(self, **kw):
        values = dict(language="zh", start_after=4, stop_after=6, tick_secs=5, screen=True, consent=False,
                      web_verify=False, extra="")
        values.update(kw)
        text = DEFAULT_CONFIG.format(
            meetings=self.meetings, minutes_app=self.minutes_app, claude=FAKES_BIN / "claude", people=self.people,
            screen=str(values["screen"]).lower(), consent=str(values["consent"]).lower(),
            web_verify=str(values["web_verify"]).lower(), **{k: values[k] for k in
                                                              ("language", "start_after", "stop_after", "tick_secs",
                                                               "extra")})
        self.config_path.write_text(text)

    def cache_models(self, *ids):
        """Make Hugging Face models look downloaded (config.asr_model_missing checks the local cache)."""
        rev = "0" * 40
        for model in ids:
            d = Path(self.env["HF_HUB_CACHE"]) / f"models--{model.replace('/', '--')}"
            (d / "snapshots" / rev).mkdir(parents=True, exist_ok=True)
            (d / "snapshots" / rev / "config.json").write_text("{}")
            (d / "refs").mkdir(exist_ok=True)
            (d / "refs" / "main").write_text(rev)

    def uncache_models(self):
        shutil.rmtree(self.env["HF_HUB_CACHE"], ignore_errors=True)

    # ---------------------------------------------------------------- the fake world
    def minutes(self, **state):
        """Set the fake Minutes state (recording, processing, finals, session_id, fail=[...])."""
        current = self.minutes_state()
        current.update(state)
        (self.fake / "minutes.json").write_text(json.dumps(current, ensure_ascii=False))

    def minutes_state(self):
        try:
            return json.loads((self.fake / "minutes.json").read_text())
        except (OSError, ValueError):
            return {}

    def mic_users(self, *bundle_ids):
        (self.fake / "mic-users.txt").write_text("".join(f"{1000 + i}\t{b}\n" for i, b in enumerate(bundle_ids)))

    def transcript_fixture(self, name="live-transcript-zh.jsonl"):
        shutil.copy(FIXTURES / name, self.fake / "transcript.jsonl")

    def tick_result(self, data=None):
        src = json.loads((FIXTURES / "tick-result.json").read_text()) if data is None else data
        (self.fake / "tick-result.json").write_text(json.dumps(src, ensure_ascii=False))

    def flag(self, name, value=True, text=""):
        p = self.fake / name
        if value:
            p.write_text(text)
        else:
            p.unlink(missing_ok=True)

    def stems(self, exist=True):
        """Create (or remove) Minutes' stem files for the recording in progress."""
        import struct
        header = (b"RIFF" + struct.pack("<I", 0) + b"WAVEfmt " + struct.pack("<IHHIIHH", 16, 1, 1, 16000, 32000, 2, 16)
                  + b"data" + struct.pack("<I", 0))
        for stem in ("voice", "system"):
            p = self.home / ".minutes" / f"current.{stem}.wav"
            if exist:
                p.write_bytes(header + b"\0" * 3200)
            else:
                p.unlink(missing_ok=True)

    def command(self, cmd):
        """Drop a command file the way the Mac app does."""
        d = self.state / "commands"
        d.mkdir(exist_ok=True)
        (d / f"{int(time.time() * 1000)}-{cmd}.json").write_text(json.dumps({"cmd": cmd}))

    # ---------------------------------------------------------------- observations
    def status(self):
        try:
            return json.loads((self.state / "status.json").read_text())
        except (OSError, ValueError):
            return {}

    def calls(self, tool=None):
        out = []
        try:
            for line in (self.fake / "calls.log").read_text().splitlines():
                name, _, args = line.partition("\t")
                if tool is None or name == tool:
                    out.append((name, json.loads(args)))
        except OSError:
            pass
        return out

    def claude_calls(self):
        try:
            return [json.loads(l) for l in (self.fake / "claude.log").read_text().splitlines() if l.strip()]
        except OSError:
            return []

    def notifications(self):
        try:
            return [json.loads(l) for l in (self.fake / "notifications.log").read_text().splitlines() if l.strip()]
        except OSError:
            return []

    def log(self):
        try:
            return (self.state / "autopilot.log").read_text()
        except OSError:
            return ""

    def notes(self):
        """Text of the live notes the engine points current.md at, or ''."""
        current = self.meetings / "live" / "current.md"
        try:
            return current.resolve().read_text()
        except OSError:
            return ""

    def wait_for(self, predicate, timeout=10, every=0.05):
        deadline = time.time() + timeout
        while time.time() < deadline:
            if predicate():
                return True
            time.sleep(every)
        return False

    # ---------------------------------------------------------------- running the engine
    def apply_env(self):
        os.environ.update(self.env)
        hub = sys.modules.get("huggingface_hub.constants")
        if hub is not None:                    # imported earlier: it read the cache folder at import time
            hub.HF_HUB_CACHE = self.env["HF_HUB_CACHE"]

    def import_module(self, name, path=None):
        """Load an engine module fresh under this sandbox's environment (earlier copies are dropped so
        their module-level constants are recomputed)."""
        self.apply_env()
        for m in ENGINE_MODULES:
            sys.modules.pop(m, None)
        sys.path.insert(0, str(APP / "lib"))
        path = path or (APP / "bin" / f"{name}.py" if (APP / "bin" / f"{name}.py").exists() else APP / "lib" / f"{name}.py")
        spec = importlib.util.spec_from_file_location(name, path)
        module = importlib.util.module_from_spec(spec)
        sys.modules[name] = module
        spec.loader.exec_module(module)
        return module

    def import_autopilot(self, clock=None):
        module = self.import_module("autopilot", APP / "bin" / "autopilot.py")
        if clock is not None:
            module.time = clock
        return module

    def run_autopilot(self, loops, timeout=60, poll_secs=0.05, extra_env=None):
        env = {**self.env, "MEETING_COPILOT_MAX_LOOPS": str(loops), "MEETING_COPILOT_POLL_SECS": str(poll_secs)}
        env.update(extra_env or {})
        return subprocess.run([sys.executable, str(APP / "bin" / "autopilot.py")], env=env, capture_output=True,
                              text=True, timeout=timeout)

    def run_main(self, module, loops, clock):
        """Run autopilot.main() in this process for `loops` iterations with the fake clock."""
        module.MAX_LOOPS = loops
        module.time = clock
        try:
            module.main()
        except SystemExit as e:
            return e.code
        return None

    def wait_threads(self, timeout=10):
        """Wait for the engine's tick/feed threads to finish (daemon threads keep running after main())."""
        deadline = time.time() + timeout
        while time.time() < deadline:
            busy = [t for t in threading.enumerate() if t is not threading.main_thread() and t.is_alive()
                    and not t.daemon]
            if not busy:
                return True
            time.sleep(0.05)
        return False

    def cleanup(self):
        shutil.rmtree(self.root, ignore_errors=True)


class FakeOpenAI:
    """A stand-in on this Mac for a service that speaks the OpenAI chat format (DeepSeek, Qwen, a local model...):
    keeps each request, and replies with `reply`, in one body or streamed line by line. `status` other than 200
    is an error reply; `body` replaces the reply's whole body; `delay` is the wait between streamed pieces."""

    def __init__(self, reply="好的。", status=200, body=None, delay=0.0):
        import http.server
        fake = self
        self.reply, self.status, self.body, self.delay = reply, status, body, delay
        self.requests = []          # {"path", "auth", "agent", "body"} per chat request
        self.probes = []            # paths asked with GET
        self.has_models = True      # some services have no list of models (404)
        self.stall = False          # stream one piece, then say nothing more

        class Handler(http.server.BaseHTTPRequestHandler):
            def log_message(self, *args):
                pass

            def do_POST(self):
                body = json.loads(self.rfile.read(int(self.headers.get("Content-Length", 0))) or b"{}")
                fake.requests.append({"path": self.path, "auth": self.headers.get("Authorization"),
                                      "agent": self.headers.get("User-Agent"), "body": body})
                if fake.status != 200:
                    return self._send(fake.status, "application/json", b'{"error": {"message": "no"}}')
                if fake.body is not None:
                    return self._send(200, "application/json", fake.body)
                if not body.get("stream"):
                    reply = {"choices": [{"message": {"role": "assistant", "content": fake.reply}}]}
                    return self._send(200, "application/json", json.dumps(reply).encode())
                self.send_response(200)
                self.send_header("Content-Type", "text/event-stream")
                self.end_headers()
                # As real services do: reasoning sent apart from the reply, a comment line, a chunk with no choices.
                chunks = [{"choices": [{"delta": {"reasoning_content": "想一下"}}]}]
                chunks += [{"choices": [{"delta": {"content": piece}}]} for piece in fake.reply.splitlines(keepends=True)]
                chunks.append({"choices": [], "usage": {"total_tokens": 1}})
                try:
                    self.wfile.write(b": keep-alive\n\n")
                    for chunk in chunks:
                        self.wfile.write(b"data: " + json.dumps(chunk).encode() + b"\n\n")
                        self.wfile.flush()
                        said = chunk["choices"] and "content" in chunk["choices"][0]["delta"]
                        time.sleep(30 if fake.stall and said else fake.delay)
                    self.wfile.write(b"data: [DONE]\n\n")
                except OSError:
                    pass                                      # the reader hung up

            def do_GET(self):                                 # "which models are there?": does it take the key
                fake.probes.append(self.path)
                ok = fake.status == 200 and self.path == "/v1/models" and fake.has_models
                self._send(fake.status if fake.status != 200 else (200 if ok else 404), "application/json", b'{"data": []}')

            def _send(self, status, kind, data):
                self.send_response(status)
                self.send_header("Content-Type", kind)
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)

        self.server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.server.daemon_threads = True
        self.url = f"http://127.0.0.1:{self.server.server_address[1]}/v1"
        threading.Thread(target=self.server.serve_forever, daemon=True).start()

    def close(self):
        self.server.shutdown()
        self.server.server_close()
