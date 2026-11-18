#!/usr/bin/env python3
"""Meeting autopilot.

Runs forever under launchd:
  * Auto-records any call: when a process other than Minutes captures the
    microphone for a few seconds, start `minutes record --call`; stop when no
    other process has used the microphone for a while.
  * Live copilot: while any recording is running, every tick send new
    live-transcript lines to Claude, then post macOS notifications for items about
    the user and explanations, and append everything to a live notes file.
  * After an auto-recorded meeting is processed, notify where the notes are.

Settings come from app/config.example.toml overridden by ~/.config/meeting-copilot/config.toml.
"""
import json
from array import array
import os
import re
import shlex
import shutil
import subprocess
import sys
import threading
import time
from datetime import datetime, timedelta
from pathlib import Path

import numpy as np

BASE = Path(__file__).resolve().parents[1]          # the app/ directory, wherever it is installed
sys.path.insert(0, str(BASE / "lib"))
import config  # noqa: E402
import control  # noqa: E402
import i18n  # noqa: E402
import llm  # noqa: E402
import minutes_record  # noqa: E402
import realtime  # noqa: E402
import screen_speakers  # noqa: E402
import speakers  # noqa: E402
import screen  # noqa: E402

CFG = config.load()
LANG = i18n.Lang(CFG)                           # language of notes, notifications and prompts
# The language can change after install.sh ran: its speech model is then not installed yet (the setup guide
# offers Install again). Checked once: a language change restarts this program.
ASR_MISSING = config.asr_model_missing(CFG) if CFG["asr"]["live"] else ""
HOME = Path.home()
STATE = config.state_dir()                           # app/state, or MEETING_COPILOT_STATE
MEETINGS_DIR = config.meetings_dir(CFG)
LIVE_DIR = MEETINGS_DIR / "live"
# tail -F follows the name, so it keeps working when current.md is re-pointed to a new meeting.
VIEWER_CMD = f"tail -n 200 -F {shlex.quote(str(LIVE_DIR / 'current.md'))}"
LOG = STATE / "autopilot.log"
AUTO_FLAG = STATE / "auto-recording"   # title of the recording autopilot started
# Helpers and system commands: MEETING_COPILOT_BIN can replace any of them (tests run against fakes).
MINUTES = config.tool("minutes", BASE / "bin" / "minutes")
LIVE_TRANSCRIBER = config.tool("live_transcriber.py", BASE / "bin" / "live_transcriber.py")
SCREEN_VIDEO = config.tool("screen_video.py", BASE / "bin" / "screen_video.py")
RETENTION = config.tool("retention.py", BASE / "bin" / "retention.py")
FRAMES_TO_VIDEO = config.tool("frames-to-video", BASE / "bin" / "frames-to-video")
OSASCRIPT = config.tool("osascript", "/usr/bin/osascript")
PGREP = config.tool("pgrep", "/usr/bin/pgrep")
LAUNCHCTL = config.tool("launchctl", "/bin/launchctl")
PMSET = config.tool("pmset", "/usr/bin/pmset")
os.environ["MINUTES_APP"] = str(config.minutes_app(CFG))   # the config wins over the value baked into the plist
MIC_USERS = config.tool("mic-users", BASE / "bin" / "mic-users")
SCREEN_OCR = config.tool("screen-ocr", BASE / "bin" / "screen-ocr")
SCREENS_DIR = HOME / ".minutes" / "screens"   # Minutes' own screenshots: the fallback when our capture can't run
SCREEN_MAX_CHARS = 4000
SCREEN = CFG.get("screen", {})
SCREEN_CAPTURE = config.tool("screen-capture", BASE / "bin" / "MeetingCopilotScreen.app" / "Contents" / "MacOS" / "screen-capture")
CLAUDE = config.claude_cli(CFG)                  # the user's own Claude Code, signed in with their account
LLM, MODELS = llm.from_config(CFG, CLAUDE)       # who writes the notes and answers, and with which model per job
USER_NAME = CFG["user"]["name"] or LANG.t("user_fallback")
USER_ALIASES = tuple(CFG["user"]["aliases"]) or (USER_NAME,)
MENTION_NAMES = tuple(n for n in (CFG["user"]["name"], *CFG["user"]["aliases"]) if n)   # empty: no name trigger
AUTO_RECORD = CFG["autopilot"]["auto_record"]
COPILOT_ENABLED = CFG["copilot"]["enabled"]
OPEN_VIEWER = CFG["copilot"]["open_viewer"]
CONSENT_REMINDER = CFG["privacy"]["consent_reminder"]
SHOW_TRANSCRIPT = CFG["copilot"].get("show_transcript", True)
INSTANT_TRIGGERS = set(CFG["copilot"].get("instant_triggers", ["name", "question", "every_line"]))
EXPLAIN = CFG.get("explain", {})
NOTIFY_TYPES = set(CFG.get("notify", {}).get("types", []))
NOTIFY_MIN_GAP = CFG.get("notify", {}).get("min_gap_secs", 0)
NOTIFY_SOUNDS = CFG.get("notify", {}).get("sounds", {})
STORE_VOICEPRINTS = CFG.get("privacy", {}).get("store_voiceprints", True)
# Reading who speaks from a frame needs a model that reads images.
NAME_FROM_SCREEN = SCREEN.get("enabled", True) and SCREEN.get("name_speakers", True) and LLM.vision
SELF_NAMES = (USER_NAME, *USER_ALIASES)   # never another participant's name
PEOPLE = speakers.People(CFG.get("voice", {}).get("people_file") or "~/.config/meeting-copilot/people.json")
KNOWN_TERMS_FILE = config.known_terms_file(CFG)
LIVE_ASR_ENABLED = CFG["asr"].get("live", True)
CURRENT_WAV = HOME / ".minutes" / "current.wav"          # Minutes' in-progress recording (+ .voice/.system stems)
LIVE_TRANSCRIPTS = STATE / "live"
RETENTION_STAMP = STATE / "retention.last"

# Items about the user get the "Ping" notification sound; everything is still notified and written.
TIER1 = ("mention", "my_todo", "question_to_me")
TIER2 = ("decision", "deadline", "risk")
ITEM_LABELS = {k: LANG.t(f"item_{k}") for k in
               ("mention", "my_todo", "question_to_me", "decision", "deadline", "risk", "other_todo")}

POLL_SECS = float(os.environ.get("MEETING_COPILOT_POLL_SECS") or 2)
MAX_LOOPS = int(os.environ["MEETING_COPILOT_MAX_LOOPS"]) if os.environ.get("MEETING_COPILOT_MAX_LOOPS") else None  # tests
STEMS_GRACE_SECS = 30     # a recording without stem files after this long has none: use Minutes' transcript
CALL_AUDIO_LOST_SECS = CFG["autopilot"].get("call_audio_lost_secs", 120)   # 0 turns the check off
CALL_AUDIO_RESTARTS = CFG["autopilot"].get("call_audio_restarts", 1)
START_AFTER_SECS = CFG["autopilot"]["start_after_secs"]   # other app must hold the mic this long
STOP_AFTER_SECS = CFG["autopilot"]["stop_after_secs"]     # mic free this long before we stop
COOLDOWN_SECS = CFG["autopilot"]["cooldown_secs"]         # after stopping, ignore mic use briefly
TICK_SECS = CFG["copilot"]["tick_secs"]                   # copilot cadence while recording
CONTEXT_MINUTES = CFG["copilot"]["context_minutes"]       # transcript window sent as context
START_GRACE_SECS = 15     # ignore "not recording" this long after we start one

# Known Whisper hallucinations on silence/noise.
from text import MINUTES_EXACT, is_hallucination  # noqa: E402

# Mic users that are not calls.
IGNORE_PREFIXES = (
    "com.useminutes",            # Minutes itself
    "com.meeting-copilot",       # the Mac app recording the voiceprint, and our own helpers
    "com.anthropic",             # Claude dictation / voice
    "com.apple.siri", "com.apple.assistant", "com.apple.SpeechRecognitionCore",
    "com.apple.dictation", "com.apple.corespeech", "com.apple.accessibility",
    "com.apple.Sound-Settings", "com.apple.systempreferences", "com.apple.settings",  # input level meter
) + tuple(CFG["autopilot"]["ignore_apps"])
IGNORE_SUBSTRINGS = ("/Minutes.app/", "corespeechd", "/claude", "assistantd")

APP_NAMES = {
    "us.zoom.xos": "Zoom", "com.microsoft.teams2": "Teams", "com.microsoft.teams": "Teams",
    "com.google.Chrome": "Chrome", "com.google.Chrome.helper": "Chrome",
    "com.apple.Safari": "Safari", "company.thebrowser.Browser": "Arc",
    "com.microsoft.edgemac": "Edge", "org.mozilla.firefox": "Firefox",
    "com.tinyspeck.slackmacgap": "Slack", "jp.naver.line.mac": "LINE", "com.apple.FaceTime": "FaceTime",
    "com.hnc.Discord": "Discord", "com.cisco.webexmeetingsapp": "Webex", "Cisco-Systems.Spark": "Webex",
    "com.tencent.meeting": "騰訊會議", "com.electron.lark": "Lark", "net.whatsapp.WhatsApp": "WhatsApp",
}

PROMPT = i18n.ANALYSIS_PROMPT[LANG.family]


def safely(label, fn, *args):
    """Run one component of the main loop; a failure is logged and never stops the others."""
    try:
        return fn(*args)
    except Exception as e:  # noqa: BLE001
        log(f"{label} error: {e!r}")
        return None


_ROWS_CACHE = {}     # path -> (bytes parsed, rows, relabels): the transcript is read incrementally
_ROWS_LOCK = threading.Lock()    # the main loop (feed) and the tick thread both read


def read_rows(path):
    """Rows of a live transcript .jsonl, read incrementally; a torn last line waits for the next read."""
    with _ROWS_LOCK:
        return _read_rows(Path(path))


def forget_rows(keep):
    """Drop cached transcripts other than `keep` (a meeting ended)."""
    with _ROWS_LOCK:
        for stale in [p for p in _ROWS_CACHE if p != keep]:
            del _ROWS_CACHE[stale]


def read_relabels(path):
    """Turns the transcriber moved to another voice so far: [{"from", "to", "starts", "ts"}] (see read_rows)."""
    with _ROWS_LOCK:
        _read_rows(Path(path))
        return list(_ROWS_CACHE.get(Path(path), (0, [], []))[2])


def _read_rows(path):
    offset, rows, relabels = _ROWS_CACHE.get(path, (0, [], []))
    try:
        size = path.stat().st_size
        if size < offset:                       # a new file under the same name
            offset, rows, relabels = 0, [], []
        if size == offset:
            return list(rows)
        with open(path, "rb") as f:
            f.seek(offset)
            chunk = f.read(size - offset)
    except OSError:
        return list(rows)
    good = chunk.rfind(b"\n") + 1               # only complete lines
    for line in chunk[:good].splitlines():
        if not line.strip():
            continue
        try:
            moved = speakers.add_row(rows, json.loads(line))
        except (ValueError, TypeError, AttributeError):
            continue
        if moved:
            relabels.append(moved)
    if not offset:
        speakers.mark_unsure(rows, speakers.unsure_starts(path))   # restarted mid-meeting
    _ROWS_CACHE[path] = (offset + good, rows, relabels)
    return list(rows)


def log(msg):
    STATE.mkdir(parents=True, exist_ok=True)
    with open(LOG, "a") as f:
        f.write(f"{datetime.now().isoformat(timespec='seconds')} {msg}\n")


def run(cmd, timeout=30):
    try:
        return subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
    except Exception as e:  # noqa: BLE001
        log(f"run failed {cmd[0]}: {e}")
        return None


def screen_image_block(path, max_side=1568):
    """A changed frame as an inline image (downscaled: Claude reads ~1.5 k px per side anyway). `path` may also be
    an image already open (PIL)."""
    import base64
    import io
    from PIL import Image
    im = path if isinstance(path, Image.Image) else Image.open(path)
    with im:
        im = im.convert("RGB")
        im.thumbnail((max_side, max_side))
        buf = io.BytesIO()
        im.save(buf, "JPEG", quality=85)
    return {"type": "image", "source": {"type": "base64", "media_type": "image/jpeg",
                                        "data": base64.b64encode(buf.getvalue()).decode()}}


def ask_model(prompt, images=(), timeout=180, model=None, web=True):
    """Periodic analysis call: text plus screen images (for a model that reads them); web search only where the
    provider has it. Returns the reply or None."""
    blocks = []
    for path in images if LLM.vision else ():
        try:
            blocks.append(screen_image_block(path))
        except OSError as e:
            log(f"screen image skipped: {e}")
    try:
        return LLM.complete(prompt, model or MODELS["notes"], images=blocks, web=web and LLM.web, timeout=timeout,
                            as_message=True)
    except llm.LLMError as e:
        log(f"{LLM.name} failed: {e}")
        return None


def recording_started_at():
    """When the current recording began: the mic stem's creation time, else now."""
    try:
        return float(os.stat(CURRENT_WAV.with_suffix(".voice.wav")).st_birthtime)
    except (OSError, AttributeError):
        return time.time()


def model_problem():
    """None when the model can be reached; otherwise why notes and explanations can't run."""
    problem = LLM.problem()
    if not problem:
        return None
    text = LANG.t({"missing": "n_claude_missing", "signed_out": "n_claude_signed_out", "no_url": "n_llm_no_url",
                   "no_model": "n_llm_no_model", "rejected": "n_llm_rejected", "failing": "n_llm_failing",
                   "unknown_provider": "n_llm_unknown"}[problem])
    return text + (" " + LLM.last_error[:160] if problem == "failing" else "")


class ModelCheck:
    """Warn once per meeting when the model cannot be reached (the user's Claude sign-in is missing, or their
    service has no address or refused the key); transcription keeps running."""

    RECHECK_SECS = 600

    def __init__(self):
        self.warned_for = None
        self.checked_at = 0.0
        self.problem = None

    def check(self, meeting):
        if self.warned_for == meeting and time.time() - self.checked_at < self.RECHECK_SECS:
            return
        self.checked_at = time.time()
        problem = self.problem = model_problem()
        if problem:
            log(f"model: {problem}")
            if self.warned_for != meeting:
                notify(LANG.t("n_notes_paused"), LANG.t("n_notes_paused_body", problem=problem), sound="Basso")
            self.warned_for = meeting

    def recheck(self):
        """While a problem is reported, look again every few minutes so status.json clears once the user
        signed in, without waiting for the next meeting."""
        if self.problem and time.time() - self.checked_at >= self.RECHECK_SECS:
            self.checked_at = time.time()
            self.problem = model_problem()
            if not self.problem:
                log("model: ok again")


MODEL_CHECK = ModelCheck()


def open_viewer():
    """Show the live-notes window: bring an existing one to the front, or open a new one.
    With the Mac app running, its own notes window opens instead (it follows status.json)."""
    if control.app_running():
        return
    found = run([PGREP, "-f", r"tail -n 200 -F .*live/current\.md"], timeout=5)
    if found and found.returncode == 0:
        script = ('tell application "Terminal"\n activate\n repeat with w in windows\n'
                  '  if name of w contains "current.md" then\n   set index of w to 1\n   exit repeat\n  end if\n'
                  ' end repeat\nend tell')
        r = run([OSASCRIPT, "-e", script], timeout=15)
        log(f"viewer brought to front: {'ok' if r and r.returncode == 0 else (r.stderr[-200:] if r else 'error')}")
        return
    script = 'on run argv\n tell application "Terminal"\n  do script (item 1 of argv)\n  activate\n end tell\nend run'
    r = run([OSASCRIPT, "-e", script, VIEWER_CMD], timeout=15)
    log(f"viewer opened: {'ok' if r and r.returncode == 0 else (r.stderr[-200:] if r else 'error')}")


_last_notify = [0.0]


def notify(title, text, sound="Glass", kind=None):
    """kind=None is a system message (always shown); otherwise [notify] types / min_gap_secs apply."""
    if kind is not None:
        if kind not in NOTIFY_TYPES:
            return
        if time.time() - _last_notify[0] < NOTIFY_MIN_GAP:
            return
        _last_notify[0] = time.time()
        sound = NOTIFY_SOUNDS.get(kind, sound)
    current = LIVE_DIR / "current.md"
    if control.app_running() and control.notify_app(title, text[:240], str(sound),
                                                    str(current.resolve()) if current.exists() else ""):
        return
    script = ('on run argv\n display notification (item 2 of argv) with title (item 1 of argv)'
              ' sound name (item 3 of argv)\nend run')
    run([OSASCRIPT, "-e", script, title, text[:240], str(sound)], timeout=10)


def mic_users():
    """Apps using the microphone, or None when the helper could not tell (on a Mac too busy to answer within
    5 s). None is not "nobody": a call must not look ended because the Mac is slow."""
    r = run([MIC_USERS], timeout=5)
    users = []
    if not r or r.returncode != 0:
        return None
    for line in r.stdout.splitlines():
        _, _, name = line.partition("\t")
        if name.startswith(IGNORE_PREFIXES) or any(s in name for s in IGNORE_SUBSTRINGS):
            continue
        users.append(name)
    return users


BROWSER_CALL = "WebRTC has active PeerConnections"   # Chrome's power assertion while a web call is connected


def browser_call():
    """Whether a browser holds a call (Meet, Teams on the web): Chrome keeps a power assertion while connected,
    with the microphone off too. In a web call joined with the microphone off no app holds the microphone, and
    without this check lost call audio would be taken for the silence after a call."""
    r = run([PMSET, "-g", "assertions"], timeout=5)
    return bool(r and r.returncode == 0 and BROWSER_CALL in r.stdout)


def status():
    r = run([MINUTES, "status"], timeout=10)
    try:
        return json.loads(r.stdout) if r else {}
    except json.JSONDecodeError:
        return {}


def app_label(names):
    for n in names:
        base = n[: -len(".helper")] if n.endswith(".helper") else n   # browser audio runs in a helper process
        if n in APP_NAMES or base in APP_NAMES:
            return APP_NAMES.get(n) or APP_NAMES[base]
    n = names[0] if names else LANG.t("call")
    return os.path.basename(n).split(".")[-1] or LANG.t("call")


class LiveASR:
    """Runs bin/live_transcriber.py for the recording in progress (per-speaker live transcript)."""

    def __init__(self):
        self.proc = None
        self.session = None
        self.path = None
        self.failed_notified = False
        self.waiting_since = None      # when we first looked for the stems of this recording
        self.carry_from = None         # transcript of the recording this one continues (restarted mid-call)

    def ensure(self):
        """Start the transcriber for the current recording once Minutes has created its stems."""
        if not LIVE_ASR_ENABLED:
            return
        voice = CURRENT_WAV.with_suffix(".voice.wav")
        if not voice.exists():
            self.waiting_since = self.waiting_since or time.time()
            return
        try:
            session = f"rec-{int(os.stat(voice).st_birthtime)}"
        except (OSError, AttributeError):
            return
        if session == self.session:
            return          # already running, ran, or failed for this recording; don't restart in a loop
        self.waiting_since = None
        if self.proc and self.proc.poll() is None:
            # Still draining the previous recording while a new one started: it would read the new
            # stems from its old offsets. Stop it; its last seconds are lost, the new meeting is not.
            log(f"live transcriber for {self.session} still running; stopping it for {session}")
            self.proc.terminate()
            try:
                self.proc.wait(timeout=10)
            except subprocess.TimeoutExpired:
                self.proc.kill()
        self.session, self.failed_notified = session, False
        LIVE_TRANSCRIPTS.mkdir(parents=True, exist_ok=True)
        self.path = LIVE_TRANSCRIPTS / f"{session}.jsonl"
        forget_rows(self.path)
        if self.carry_from:
            # The same call, recorded again: keep its speakers (same voices, same letters) and the names given,
            # instead of telling everyone apart and naming them from scratch.
            for of in (speakers.speakers_path, speakers.names_path, speakers.withdrawn_path, speakers.merged_path):
                try:
                    shutil.copyfile(of(self.carry_from), of(self.path))
                except OSError:
                    pass
            minutes_record.mark_part(self.path, self.carry_from)      # one meeting: same notes, one record
            log(f"live transcriber for {session} continues the speakers of {self.carry_from.stem}")
            self.carry_from = None
        with open(STATE / "live_transcriber.log", "a") as logf:      # the child keeps its own copy
            self.proc = subprocess.Popen([sys.executable, LIVE_TRANSCRIBER,
                                          str(CURRENT_WAV), str(self.path)], stdout=logf, stderr=logf)
        log(f"live transcriber started for {session} (pid {self.proc.pid})")

    def stems_pending(self):
        """True until ensure() has looked for the stem files and found none for STEMS_GRACE_SECS: Minutes may
        still be creating them. After that the recording has no stems (mic-only, no call mode) and the notes
        use Minutes' own transcript."""
        if self.waiting_since is None:
            return True                   # not looked yet (the recording started this very loop)
        return time.time() - self.waiting_since < STEMS_GRACE_SECS

    def _pidfile_alive(self):
        """A transcriber started by an earlier autopilot may still be writing this transcript."""
        try:
            pid = int(self.path.with_suffix(".pid").read_text())
            os.kill(pid, 0)
            return True
        except (OSError, ValueError, AttributeError):
            return False

    def running(self):
        """The transcript is still being written: by our process, or by the one ours deferred to."""
        if self.proc is None:
            return False
        code = self.proc.poll()
        return code is None or (code == 4 and self._pidfile_alive())

    def healthy(self):
        """False if the transcriber died with an error during this recording."""
        if self.proc is None:
            return False
        code = self.proc.poll()
        if code == 4 and self._pidfile_alive():
            return True
        if code not in (None, 0):
            if not self.failed_notified:
                self.failed_notified = True
                log(f"live transcriber exited with {code}; falling back to Minutes transcript")
                # 5: the speech model is not installed (the transcriber does not download during a meeting).
                body = (LANG.t("n_asr_missing").format(model=config.asr_model_missing(CFG) or "?") if code == 5
                        else LANG.t("n_live_asr_stopped_body"))
                notify(LANG.t("n_live_asr_stopped"), body, sound="Basso")
            return False
        return True

    def names(self):
        """{speaker letter: name} for this meeting: given during it, or matched to saved people by voice."""
        if not self.path:
            return {}
        return speakers.resolve_names(self.path, PEOPLE if STORE_VOICEPRINTS else None)

    def name_speakers(self, found, write):
        """Record names the screen taught, never the user's own: their voice is on the microphone."""
        if not self.path or not found:
            return
        path = speakers.names_path(self.path)
        given = speakers.given_names(self.path)
        withdrawn = speakers.withdrawn_names(self.path)
        try:
            snap = json.loads(speakers.speakers_path(self.path).read_text())
        except (OSError, ValueError):
            snap = {}
        voiced = speakers.resolve_names(self.path, PEOPLE if STORE_VOICEPRINTS else None)
        for who, name in found.items():
            parts, name = str(who).split(), str(name).strip()
            if not parts:
                continue
            who = parts[-1]
            if not name and given.get(who):
                # The screen took the name back; a saved voice may name them, but not with that name again.
                old = given.pop(who)
                withdrawn[who] = sorted({*withdrawn.get(who, ()), old})
                write(LANG.t("speaker_unnamed", who=LANG.speaker(i18n.OTHERS, who), name=old) + "\n")
                log(f"speaker {who}: no longer named {old}")
                continue
            if not name or who not in snap or given.get(who) == name:
                continue
            own = {screen_speakers.norm(n) for n in SELF_NAMES if n}
            if screen_speakers.norm(name) in own or screen_speakers.norm(name).split(" ")[0] in own:
                log(f"speaker {who}: not naming them {name}, the user's own name")
                continue
            given[who] = name
            if voiced.get(who) == name:
                # a note "👤 其他人 B 是 Bella" would only repeat what B's lines already show by voice
                log(f"speaker {who} named {name}, as the voice already said")
                continue
            write(LANG.t("speaker_named", who=LANG.speaker(i18n.OTHERS, who), name=name) + "\n")
            log(f"speaker {who} named {name}")
        speakers.atomic_write(path, json.dumps(given, ensure_ascii=False))
        if withdrawn:
            speakers.atomic_write(speakers.withdrawn_path(self.path), json.dumps(withdrawn, ensure_ascii=False))

    def finals(self, minutes):
        """Transcript lines of the last `minutes` minutes, tagged with the speaker ([我] / [其他人] in Chinese)."""
        if not self.path or not self.path.exists():
            return []
        rows = read_rows(self.path)
        names = self.names()
        out = [{"line": i + 1, "ts": r["ts"],
                "text": f"[{LANG.speaker(r['speaker'], speakers.shown_who(r), names)}] {r['text']}"}
               for i, r in enumerate(rows)]
        cutoff = (datetime.now() - timedelta(minutes=minutes)).isoformat(timespec="seconds")
        return [f for f in out if f["ts"] >= cutoff]


LIVE_ASR = LiveASR()


def learn_voices(teacher, transcript):
    """A recording ended: remember the voices the screen agreed on. Not a voice that clearly is another saved person's,
    whatever the screen said: one mixed group would spoil that person's voice for every later meeting."""
    if not STORE_VOICEPRINTS:
        return
    try:
        snap = json.loads(speakers.speakers_path(transcript).read_text())
    except (OSError, ValueError):
        return
    learnable = teacher.learnable()
    lines = speakers.line_vectors(transcript)
    rows = speakers.read_transcript(transcript) if lines else []
    for who, name in teacher.decided.items():
        if who not in snap:
            continue
        vector, count = snap[who]["vector"], snap[who]["count"]
        if lines:
            # Only the lines the screen showed them saying: a group can hold a second person for a turn or two
            # (a few lines of another speaker in one group), and the whole group's voice would carry them.
            mine = [lines[s] for s in teacher.confirmed(rows, who, name) if s in lines]
            if mine:
                mean = np.mean(mine, axis=0)
                vector, count = mean / (np.linalg.norm(mean) + 1e-9), len(mine)
            else:
                count = 0
        other = PEOPLE.contradicts(name, vector)
        if who not in learnable:
            log(f"speaker {who} ({name}): not learnt, screen votes {dict(teacher.votes[who])}")
        elif other:
            log(f"speaker {who} ({name}): not learnt, the voice is {other}'s")
        elif PEOPLE.learn(name, vector, count):
            log(f"speaker {who}: voice saved as {name} from {count} lines, screen votes {dict(teacher.votes[who])}")
        else:
            log(f"speaker {who} ({name}): not learnt, {count} lines the screen confirmed have a voiceprint")


class ScreenNamer:
    """Names the call's voices after who the meeting app marks as speaking (lib/screen_speakers.py), in a thread of
    its own: reading a frame is a Claude call of several seconds, and nothing else waits for it."""

    def __init__(self):
        self.session = None
        self.path = None
        self.teacher = None
        self.thread = None
        self.learning = None

    def finish(self):
        """The recording is over. In a thread: the last frame being read must not hold up the main loop."""
        teacher, path, running = self.teacher, self.path, self.thread
        self.session = self.path = self.teacher = None
        if teacher:
            self.learning = threading.Thread(target=self._learn, args=(teacher, path, running), daemon=True)
            self.learning.start()

    @staticmethod
    def _learn(teacher, path, running):
        try:
            if running:
                running.join(120)
            learn_voices(teacher, path)
        except Exception as e:  # noqa: BLE001
            log(f"learning voices: {e!r}")

    def poll(self, copilot):
        if not (NAME_FROM_SCREEN and LIVE_ASR.session and LIVE_ASR.path and SCREEN_CAP.out_dir
                and SCREEN_CAP.session == LIVE_ASR.session):
            return
        if self.thread and self.thread.is_alive():
            return
        if self.session != LIVE_ASR.session:
            self.finish()
            self.session, self.path = LIVE_ASR.session, LIVE_ASR.path
            self.teacher = screen_speakers.ScreenSpeakers(read_speaking_frame, SELF_NAMES, i18n.SELF, log,
                                                          not_them=voice_not_them(self.path),
                                                          line_voice=voice_of_line(self.path))
        self.thread = threading.Thread(target=self._run, args=(copilot, self.teacher, LIVE_ASR.session,
                                                                LIVE_ASR.path, SCREEN_CAP.out_dir), daemon=True)
        self.thread.start()

    @staticmethod
    def _run(copilot, teacher, session, path, frames_dir):
        try:
            t0 = int(session.split("-", 1)[1])            # rec-<the stems' creation time>: where line times start
            # Only names given in this meeting end the screen's work: a saved voice is a guess the screen can overrule
            # (several people can match one saved voice, and the screen would never be asked).
            rows = read_rows(path)
            found = teacher.observe(rows, t0, screen_speakers.Frames(frames_dir), speakers.given_names(path),
                                    shown=LIVE_ASR.names() if LIVE_ASR.path == path else {})
            unsure = teacher.take_unsure()
            if (found or unsure) and LIVE_ASR.session == session:
                held = copilot.stream_lock.acquire(timeout=30)
                try:
                    if unsure:
                        mark_unsure(path, rows, unsure, copilot.write)
                    if found:
                        LIVE_ASR.name_speakers(found, copilot.write)
                finally:
                    if held:
                        copilot.stream_lock.release()
        except Exception as e:  # noqa: BLE001
            log(f"screen naming error: {e!r}")


def mark_unsure(transcript, rows, unsure, write):
    """Lines the screen showed someone else saying than their voice's name: shown without it from now on (in the
    notes from here, the meeting record, Claude's view of the transcript), and told in the notes."""
    starts = {round(float(s), 2) for s, _, _ in unsure}
    with _ROWS_LOCK:
        cached = _ROWS_CACHE.get(Path(transcript))
        speakers.mark_unsure(cached[1] if cached else rows, starts)
    speakers.atomic_write(speakers.unsure_path(transcript), json.dumps(
        {"starts": sorted(speakers.unsure_starts(transcript) | starts)}))
    at = {round(float(r.get("start", -1)), 2): r for r in rows}
    for start, name, old in unsure:
        row = at.get(round(float(start), 2))
        ts = (row or {}).get("ts")
        write(LANG.t("speaker_line_unsure", time=realtime.stamp(ts) if ts else "", name=name, old=old) + "\n")
        log(f"screen: line at {start:.0f}s showed {name}, not {old}: shown without a name")


def voice_not_them(transcript):
    """(group letter, name) -> True when that group's voice so far is clearly not the saved person `name`."""
    if not STORE_VOICEPRINTS:
        return None

    def check(who, name):
        try:
            entry = json.loads(speakers.speakers_path(transcript).read_text()).get(who)
        except (OSError, ValueError, AttributeError):
            return False
        return bool(entry and entry.get("vector")) and PEOPLE.not_them(name, entry["vector"])
    return check


def voice_of_line(transcript):
    """line start -> that line's voiceprint (rec-*.vectors.jsonl, re-read when it grows), or None."""
    if not STORE_VOICEPRINTS:
        return None
    cache = {}

    def get(start):
        try:
            size = speakers.vectors_path(transcript).stat().st_size
        except OSError:
            return None
        if cache.get("size") != size:
            cache["size"], cache["vectors"] = size, speakers.line_vectors(transcript)
        return cache["vectors"].get(round(float(start), 2))
    return get


def read_speaking_frame(path):
    """Who the meeting app marks as speaking in one frame (screen_speakers.PROMPT), or None. The name is read again
    from the frame's own pixels (spelled_name) the first time it is read so."""
    reply = ask_model(screen_speakers.PROMPT, [path], timeout=90, web=False, model=MODELS["screen"])
    data = screen_speakers.parse_reply(reply) if reply else None
    if data and data.get("speaking"):
        data["speaking"] = spelled_name(path, data["speaking"], data.get("name_box"))
    return data


SPELLED = {}             # a name as read on a whole screenshot -> as read on its label at full size
NAME_CROP_HEIGHT = 240   # a name label's crop is enlarged to about this many pixels high


def name_crop(path, box):
    """The part of a frame around a name label at full resolution, enlarged, or None. `box` is the label's
    [left, top, right, bottom] in fractions of the frame, as read on the downscaled screenshot: it is widened by
    half the label's width each side and a label height above and below, as such boxes are rough."""
    from PIL import Image
    try:
        left, top, right, bottom = (float(v) for v in box)
    except (TypeError, ValueError):
        return None
    if not (0 <= left < right <= 1 and 0 <= top < bottom <= 1):
        return None
    with Image.open(path) as im:
        w, h = im.size
        dx, dy = (right - left) * w / 2, max((bottom - top) * h, 0.01 * h)
        crop = im.convert("RGB").crop((int(max(0, left * w - dx)), int(max(0, top * h - dy)),
                                       int(min(w, right * w + dx)), int(min(h, bottom * h + dy))))
    scale = min(4.0, NAME_CROP_HEIGHT / max(1, crop.height))
    if scale > 1:
        crop = crop.resize((int(crop.width * scale), int(crop.height * scale)), Image.Resampling.LANCZOS)
    return crop


def spelled_name(path, name, box):
    """`name` as spelled on its label read at full size. On a typical screen a meeting app's tile labels can be ~13 px
    high, and ~8 px once the screenshot is downscaled for reading, so one character is easily misread: Pip 石貓貓
    can come out as Pip 石錨貓. Each reading is checked once (SPELLED); a crop that cannot be read leaves the name
    as read."""
    key = screen_speakers.norm(name)
    if key in SPELLED:
        return SPELLED[key]
    crop = name_crop(path, box)
    if crop is None:
        return name
    reply = ask_model(screen_speakers.NAME_PROMPT.format(reading=name), [crop], timeout=60, web=False,
                      model=MODELS["screen"])
    if not reply:
        return name                       # no answer this time: tried again on the next frame
    spelled = (screen_speakers.parse_reply(reply) or {}).get("name")
    spelled = spelled.strip() if isinstance(spelled, str) and spelled.strip() else None
    if spelled and spelled != name and not screen_speakers.same_label(name, spelled):
        log(f"screen: {name} reads {spelled} at full size: another label, not taken")
        spelled = None                    # the box was off and the crop showed a neighbour's tile
    elif spelled and spelled != name:
        log(f"screen: {name} reads {spelled} at full size")
    SPELLED[key] = spelled or name
    return SPELLED[key]


class ScreenCap:
    """Runs bin/screen-capture under launchd (its own Screen Recording grant) during a recording."""

    LABEL = "com.meeting-copilot.screen"

    def __init__(self):
        self.session = None
        self.video_proc = None
        self.out_dir = None
        self.warned = False

    def running(self):
        """Running, or being started: launchd shows a job it just loaded as "xpcproxy" for a moment."""
        r = run([LAUNCHCTL, "print", f"gui/{os.getuid()}/{self.LABEL}"], timeout=5)
        return bool(r and r.returncode == 0 and ("state = running" in r.stdout or "state = xpcproxy" in r.stdout))

    def ensure(self, session):
        if not SCREEN.get("enabled", True) or not session or session == self.session:
            return
        self.stop()
        self.session = session
        self.warned = False                   # judged per recording: permission may have been granted since
        self.out_dir = STATE / "screens" / session
        self.out_dir.mkdir(parents=True, exist_ok=True)
        (self.out_dir / "STOP").unlink(missing_ok=True)
        args = [SCREEN_CAPTURE, str(self.out_dir), "--interval", str(SCREEN.get("interval_secs", 1)),
                "--stop-file", str(self.out_dir / "STOP")]
        target = SCREEN.get("target", "all")
        if target.startswith("app:"):
            args += ["--app", target[4:]]
        else:
            args += ["--displays", "main" if target in ("main", "display") else "all"]
        import plistlib
        plist = STATE / f"{self.LABEL}.plist"
        with open(plist, "wb") as plist_file:
            plistlib.dump({"Label": self.LABEL, "ProgramArguments": args, "RunAtLoad": True, "KeepAlive": False,
                       "StandardOutPath": str(self.out_dir / "capture.out"),
                       "StandardErrorPath": str(self.out_dir / "capture.err")}, plist_file)
        # bootout returns before launchd has let go of the old job; bootstrapping the new one meanwhile fails
        # (a recording restarted within a call lost its screen capture this way).
        run([LAUNCHCTL, "bootout", f"gui/{os.getuid()}/{self.LABEL}"], timeout=10)
        ok = False
        for attempt in range(4):
            gone = False
            for _ in range(20):
                if not self.loaded():
                    gone = True
                    break
                time.sleep(0.5)
            r = run([LAUNCHCTL, "bootstrap", f"gui/{os.getuid()}", str(plist)], timeout=10)
            # launchd can answer "5: Input/output error" although it loaded the job (app/tools/launchd-start.sh):
            # loaded counts, when the old job had gone first.
            ok = bool(r and r.returncode == 0) or (gone and self.loaded())
            if ok:
                break
            time.sleep(1)
        log(f"screen capture started for {session}: {'ok' if ok else 'failed'}"
            + (f" (attempt {attempt + 1})" if ok and attempt else ""))

    def loaded(self):
        r = run([LAUNCHCTL, "print", f"gui/{os.getuid()}/{self.LABEL}"], timeout=5)
        return bool(r and r.returncode == 0)

    def check_permission(self):
        """Warn once per recording if the tool is being refused by macOS (then Minutes' screenshots are used)."""
        err = self.out_dir / "capture.err" if self.out_dir else None
        if self.warned or not err or not err.exists():
            return
        if "TCC" in err.read_text(errors="replace"):
            self.warned = True
            log("screen capture: no Screen Recording permission; using Minutes screenshots")
            notify(LANG.t("n_screen_denied"), LANG.t("n_screen_denied_body"),
                   sound="Basso")

    def make_video(self, notes):
        """Review video of the captured frames, in the background; linked from the live notes."""
        helper = Path(FRAMES_TO_VIDEO)
        if not SCREEN.get("video", True) or not self.out_dir or not (helper.exists() or shutil.which("ffmpeg")):
            return
        out = STATE / "video" / f"{self.session}.mp4"
        cmd = [sys.executable, SCREEN_VIDEO, str(self.out_dir), str(out)]
        if notes:
            cmd.append(str(notes))
        with open(STATE / "video.log", "a") as logf:
            self.video_proc = subprocess.Popen(cmd, stdout=logf, stderr=subprocess.STDOUT)

    def video_running(self):
        return self.video_proc is not None and self.video_proc.poll() is None

    def stop(self):
        if self.out_dir:
            (self.out_dir / "STOP").touch()
        run([LAUNCHCTL, "bootout", f"gui/{os.getuid()}/{self.LABEL}"], timeout=10)


SCREEN_CAP = ScreenCap()
SCREEN_NAMER = ScreenNamer()


class MeetingRecord:
    """Minutes' meeting files get our transcript (lib/minutes_record.py): once Minutes has written one for a recording
    we transcribed, its transcript is replaced by ours and `minutes resummarize` writes the summary from it. Only
    files written since this started doing it are touched, one at a time."""

    PATH = STATE / "meeting-records.json"     # {"since": epoch, "done": [file names]}
    SETTLE_SECS = 15                          # a meeting file this fresh may still be being written

    def __init__(self):
        try:
            data = json.loads(self.PATH.read_text())
        except (OSError, ValueError):
            data = {}
        self.since = data.get("since") or time.time()
        self.done = set(data.get("done", []))
        self.proc = None
        self.target = None
        if not data:
            self._save()                      # older meeting files are never touched, across restarts too

    def _save(self):
        speakers.atomic_write(self.PATH, json.dumps({"since": self.since, "done": sorted(self.done)[-200:]}))

    def poll(self, processing):
        if self.proc:
            if self.proc.poll() is None:
                return
            log(f"meeting record {self.target.name}: summary rewritten (exit {self.proc.returncode})")
            self.proc = self.target = None
        if processing or not LIVE_TRANSCRIPTS.is_dir():
            return
        now = time.time()
        for md in sorted(MEETINGS_DIR.glob("*.md"), key=lambda p: p.stat().st_mtime):
            mtime = md.stat().st_mtime
            if md.name in self.done or mtime < self.since or now - mtime < self.SETTLE_SECS:
                continue
            self.done.add(md.name)
            self._save()
            done = self._rebuild(md)
            if done is None:                  # a meeting recorded in parts, not all of them written yet
                self.done.discard(md.name)
                self._save()
                continue
            if done:
                return

    @staticmethod
    def meeting_of(md):
        """The meeting file `md` ends up in: the first part's, when it is a later part of a restarted recording."""
        try:
            started = minutes_record.meeting_start(md.read_text(encoding="utf-8", errors="replace"))
        except OSError:
            return md
        live = minutes_record.live_transcript(LIVE_TRANSCRIPTS, started) if started else None
        first = minutes_record.first_part(live) if live else None
        if not first or first == live:
            return md
        return minutes_record.meeting_file(MEETINGS_DIR, minutes_record.epoch_of(first)) or md

    PART_WAIT_SECS = 1800                     # a later part's meeting file this long overdue is given up on

    def _rebuild(self, md):
        """True when `md` (or the first meeting file of its meeting) got our transcript and is being resummarized;
        None to look again later (a meeting recorded in parts, still going or not all its files written)."""
        text = md.read_text(encoding="utf-8", errors="replace")
        started = minutes_record.meeting_start(text)
        live = minutes_record.live_transcript(LIVE_TRANSCRIPTS, started) if started else None
        if not live or live == LIVE_ASR.path:
            return False
        parts = minutes_record.parts(live)
        if len(parts) > 1:
            return self._rebuild_parts(md, parts)
        names = speakers.resolve_names(live, PEOPLE if STORE_VOICEPRINTS else None)

        def label(row):
            return USER_NAME if row.get("speaker") == i18n.SELF else LANG.speaker(i18n.OTHERS, speakers.shown_who(row),
                                                                                    names)

        new = minutes_record.with_transcript(text, minutes_record.transcript_lines(minutes_record.read_rows(live), label))
        if not new:
            return False
        md.write_text(new, encoding="utf-8")
        log(f"meeting record {md.name}: transcript from {live.name}")
        return self._resummarize(md)

    def _rebuild_parts(self, md, parts):
        """One meeting in several recordings (restarted after the call's audio was lost): the first part's meeting
        file gets the transcript of all of them, and the later parts' files move to .parts/ beside it."""
        if LIVE_ASR.path in parts:
            return None                                   # the meeting goes on
        files = [minutes_record.meeting_file(MEETINGS_DIR, minutes_record.epoch_of(p)) for p in parts]
        missing = [p for p, f in zip(parts, files) if f is None]
        if missing and time.time() - max(p.stat().st_mtime for p in missing) < self.PART_WAIT_SECS:
            return None                                   # Minutes is still writing a part's file
        target = files[0] or md
        names = speakers.resolve_names(parts[-1], PEOPLE if STORE_VOICEPRINTS else None)   # carried part to part

        def label(row):
            return USER_NAME if row.get("speaker") == i18n.SELF else LANG.speaker(i18n.OTHERS, speakers.shown_who(row),
                                                                                    names)

        lines = minutes_record.merged_lines(parts, label, lambda secs: LANG.t("record_gap", secs=secs))
        new = minutes_record.with_transcript(target.read_text(encoding="utf-8", errors="replace"), lines)
        if not new:
            return False
        target.write_text(new, encoding="utf-8")
        moved = []
        for f in files:
            if f and f != target:
                dest = MEETINGS_DIR / ".parts" / f.name
                dest.parent.mkdir(exist_ok=True)
                shutil.move(str(f), str(dest))
                self.done.add(f.name)
                moved.append(f.name)
        self.done.add(target.name)
        self._save()
        log(f"meeting record {target.name}: transcript from {', '.join(p.name for p in parts)}"
            + (f"; {', '.join(moved)} moved to .parts/" if moved else ""))
        return self._resummarize(target)

    def _resummarize(self, md):
        self.target = md
        self.proc = subprocess.Popen([str(MINUTES), "resummarize", str(md), "--apply"], stdin=subprocess.DEVNULL,
                                     stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        return True


MEETING_RECORD = MeetingRecord()


class MinutesJobs:
    """Minutes' job after a recording (its transcript, which MeetingRecord then replaces) can fail, and Minutes tells
    nobody: the meeting then has no file in ~/meetings at all. Once a recording or Minutes' processing ends its
    jobs are read, again every CHECK_SECS while the latest is still under way; a failed latest job is logged,
    notified once and shown as a problem until a later job succeeds.
    Also fetches, while idle, the Whisper model our recordings ask for when it is missing (minutes_record)."""

    PATH = STATE / "minutes-jobs.json"        # {"reported": [failed jobs already notified, oldest first]}
    MODEL_RETRY_SECS = 3600
    CHECK_SECS = 30
    DONE = ("complete", "failed", "cancelled")   # every state Minutes ends a job in (tests/contracts.py checks)

    def __init__(self):
        try:
            self.reported = [str(k) for k in json.loads(self.PATH.read_text()).get("reported", [])]
        except (OSError, ValueError, AttributeError, TypeError):
            self.reported = []
        self.pending = True                   # read the jobs once at start too
        self.busy = False                     # Minutes' latest job is queued or running (as last read)
        self.checked_at = 0.0
        self.problem = None
        self.download = None
        self.download_at = 0.0
        self.partial = None                   # the model file our download writes: removed if it is cut short

    def _drop_partial(self):
        """A download cut short can leave a model file that looks installed (and then every job fails with it)."""
        if self.partial:
            self.partial.unlink(missing_ok=True)
            self.partial = None

    def poll(self, recording, processing):
        if recording and self.download and self.download.poll() is None:
            self.download.terminate()         # no download while a meeting is recorded; tried again later
            self.download = None
            self.download_at = 0.0
            self._drop_partial()
            log("minutes model download stopped: a recording started")
        if recording or processing:
            self.pending = True               # a recording's job comes once it stops, even a quick one
            return
        if self.pending and time.time() - self.checked_at >= self.CHECK_SECS:
            self.checked_at = time.time()
            self.pending = not self._check_jobs()
        self._fetch_model()

    def _check_jobs(self):
        """Read Minutes' jobs: True once the latest is finished (or there is none to wait for)."""
        r = run([MINUTES, "jobs", "--all", "--json", "--limit", "10"], timeout=30)
        try:
            jobs = json.loads(r.stdout) if r and r.returncode == 0 else None
        except ValueError:
            jobs = None
        if not isinstance(jobs, list):
            self.busy = False                 # unknown: nothing waits on it
            return False                      # no answer: asked again later
        jobs = sorted((j for j in jobs if isinstance(j, dict) and j.get("mode", "meeting") == "meeting"),
                      key=lambda j: str(j.get("created_at", "")))
        if not jobs:
            self.busy = False
            return True
        last = jobs[-1]
        self.busy = last.get("state") not in self.DONE
        if self.busy:
            return False                      # queued or still running: it may yet fail
        if last.get("state") != "failed":
            if self.problem:
                log("minutes job: ok again")
            self.problem = None
            return True
        error = (str(last.get("error") or "?").strip().splitlines() or ["?"])[0]
        self.problem = LANG.t("n_minutes_job_failed_body", title=last.get("title") or "?", error=error)
        key = str(last.get("id") or last.get("created_at") or error)
        if key not in self.reported:
            log(f"minutes job {key} failed: {error}")
            notify(LANG.t("n_minutes_job_failed"), self.problem, sound="Basso")
            self.reported = (self.reported + [key])[-50:]
            speakers.atomic_write(self.PATH, json.dumps({"reported": self.reported}))
        return True

    def _fetch_model(self):
        if self.download and self.download.poll() is None:
            return
        if self.download:
            log(f"minutes model download ended (exit {self.download.returncode})")
            if self.download.returncode != 0:
                self._drop_partial()
            self.download = None
        if time.time() - self.download_at < self.MODEL_RETRY_SECS:
            return
        self.download_at = time.time()
        model = minutes_record.recording_model_missing()
        if model:
            log(f"minutes: the {model} model our recordings use is missing; downloading it")
            self.partial = minutes_record.recording_model_path()
            self.download = subprocess.Popen([str(MINUTES), "setup", "--model", model, "--download-only"],
                                             stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                                             stderr=subprocess.DEVNULL)


MINUTES_JOBS = MinutesJobs()


class RecordOutcomes:
    """The outcome a user expects after a meeting, checked at the end: its file in ~/meetings, whatever the cause
    when it is missing (a failed Minutes job has its own, more precise notice). Each meeting is matched to its own
    file by its start, as MeetingRecord does, and the list is kept on disk so a restart does not drop it.
    A meeting is given up on WAIT_SECS after it stopped, and only while Minutes is not busy: a job still queued
    behind another is waited for. A missing one is notified once and shown (outside recordings) until its file
    turns up, or for a day."""

    PATH = STATE / "record-outcomes.json"     # {"waiting": [...], "missing": [...]}: {"started", "stopped", "title"}
    WAIT_SECS = 1800
    MIN_SECS = 60                             # shorter recordings are not followed (started by mistake)
    KEEP_SECS = 86400
    CHECK_SECS = 30

    def __init__(self):
        try:
            data = json.loads(self.PATH.read_text())
            self.waiting, self.missing = list(data.get("waiting", [])), list(data.get("missing", []))
        except (OSError, ValueError, AttributeError):
            self.waiting, self.missing = [], []
        self.checked_at = 0.0

    def _save(self):
        speakers.atomic_write(self.PATH, json.dumps({"waiting": self.waiting, "missing": self.missing},
                                                    ensure_ascii=False))

    def add(self, started, stopped, title):
        """A recording stopped. `started` is the meeting's first start (a restart after lost call audio continues
        the same meeting, whose later parts' files join the first one's)."""
        if started and stopped - started >= self.MIN_SECS:
            self.waiting.append({"started": started, "stopped": stopped, "title": title or "?"})
            self._save()

    def poll(self, recording, processing):
        now = time.time()
        if now - self.checked_at < self.CHECK_SECS or not (self.waiting or self.missing):
            return
        self.checked_at = now
        before = (len(self.waiting), len(self.missing))
        found = lambda m: minutes_record.meeting_file(MEETINGS_DIR, m["started"]) is not None
        self.waiting = [m for m in self.waiting if not found(m)]
        self.missing = [m for m in self.missing if not found(m) and now - m["stopped"] < self.KEEP_SECS]
        busy = recording or processing or MINUTES_JOBS.busy
        for m in [m for m in self.waiting if not busy and now - m["stopped"] >= self.WAIT_SECS]:
            self.waiting.remove(m)
            self.missing.append(m)
            log(f"meeting {m['title']!r}: no meeting file {int(now - m['stopped'])} s after it stopped")
            if not (MINUTES_JOBS.problem and m["title"] in MINUTES_JOBS.problem):    # not told twice
                notify(LANG.t("n_meeting_missing"), self.text(m), sound="Basso")
        if before != (len(self.waiting), len(self.missing)):
            self._save()

    def text(self, m):
        return LANG.t("n_meeting_missing_body", title=m["title"], mins=max(1, round(self.WAIT_SECS / 60)))

    def problem(self):
        return self.text(self.missing[-1]) if self.missing else None


RECORD_OUTCOMES = RecordOutcomes()


class CaptureWatch:
    """Tell the user when Minutes' audio devices drop or switch mid-recording."""

    def __init__(self):
        self.path = STATE / "capture.err"
        self.offset = self.path.stat().st_size if self.path.exists() else 0

    def check(self):
        if not self.path.exists():
            return
        size = self.path.stat().st_size
        if size < self.offset:          # a new recording truncated the log
            self.offset = 0
        if size == self.offset:
            return
        with open(self.path, errors="replace") as f:
            f.seek(self.offset)
            new = f.read()
        self.offset = size
        for line in new.splitlines():
            if "capture reconnected:" in line:
                devices = line.split("reconnected:", 1)[1].strip()
                log(f"capture devices changed: {devices}")
                notify(LANG.t("n_device_switched"), devices, sound="Basso")


CAPTURE_WATCH = CaptureWatch()


def sound_samples(samples, min_run):
    """Samples that belong to a run of at least min_run consecutive non-zero samples. A broken capture is not
    always pure zeros: it can deliver ~16 isolated 2 ms spikes a second, enough to look like 6-10 % sound for a
    minute and delay the alarm by two, or shreds of the call, about a fifth of the samples at normal level in runs
    of 10-12 ms, which no speech detector could read. Real sound (even a quiet line) comes in longer runs."""
    if not samples:
        return 0
    try:
        import numpy as np
    except ImportError:            # without numpy: every non-zero sample counts
        return len(samples) - samples.count(0)
    nz = np.frombuffer(samples, dtype=np.int16) != 0
    edges = np.flatnonzero(np.diff(np.concatenate(([False], nz, [False])).astype(np.int8)))
    runs = edges[1::2] - edges[0::2]
    return int(runs[runs >= min_run].sum())


class StemLevels:
    """The share of sound in the last few minutes of one of Minutes' stems, read incrementally (only what was
    added since the last read) in 10-second blocks. Sound is non-zero samples in runs of at least 20 ms."""

    BLOCK_SECS = 10
    RUN_SECS = 0.02            # debris of a broken capture runs shorter (2 ms spikes, 10-12 ms shreds), speech longer

    def __init__(self, suffix):
        self.suffix = suffix
        self.reset(None)

    def reset(self, key):
        self.key, self.offset, self.rate, self.blocks = key, 0, 0, []

    def read(self, window_secs):
        """(seconds covered, non-zero share) of the last window_secs, and whether any block just read was
        mostly sound (the capture works). None if the stem is missing or not a WAV file."""
        path = CURRENT_WAV.with_suffix(self.suffix)
        try:
            st = os.stat(path)
        except OSError:
            return None
        key = (st.st_ino, getattr(st, "st_birthtime", 0))
        if key != self.key or st.st_size < self.offset:
            self.reset(key)                        # a new recording
        with open(path, "rb") as f:
            if not self.rate:
                header = f.read(44)
                if len(header) < 44 or header[:4] != b"RIFF":
                    return None
                channels, rate = int.from_bytes(header[22:24], "little"), int.from_bytes(header[24:28], "little")
                self.rate, self.offset = max(1, rate * channels), 44
            f.seek(self.offset)
            data = f.read(max(0, st.st_size - self.offset) // 2 * 2)
        self.offset += len(data)
        samples = array("h")
        samples.frombytes(data)
        block, sound, min_run = self.rate * self.BLOCK_SECS, False, max(1, int(self.rate * self.RUN_SECS))
        for i in range(0, len(samples), block):
            part = samples[i:i + block]
            nonzero = sound_samples(part, min_run)
            sound = sound or (len(part) >= block // 2 and nonzero >= CallAudioWatch.HEARD_FRACTION * len(part))
            self.blocks.append((len(part), nonzero))
        window = window_secs * self.rate
        while self.blocks and sum(n for n, _ in self.blocks[1:]) >= window:
            self.blocks.pop(0)                     # keep just enough blocks to cover the window
        total = sum(n for n, _ in self.blocks)
        share = sum(z for _, z in self.blocks) / total if total else 0.0
        return total / self.rate, share, sound


class CallAudioWatch:
    """Notice when Minutes keeps recording but the call's audio no longer reaches it.

    After "audio input overload" warnings Minutes can go on recording while its system (call) stem holds only
    digital zeros for the rest of the call, though the user still hears everyone; or the stem holds shreds,
    which count as no sound (see sound_samples). The call counts as lost when, over the last
    CALL_AUDIO_LOST_SECS of audio:
    - under 2 % of the call stem's samples are non-zero (a stray system sound still gets through; a working
      call's quiet stretches were zeros for up to ~15 s at a time, never minutes),
    - the microphone stem is not silent too (both all zeros is a paused or muted recording, which can last
      minutes before both come back by themselves),
    - the call stem carried real audio earlier in this call,
    - and nobody was transcribed meanwhile (when only the other side is quiet, the user's lines show up).
    A call whose audio stops is caught about a minute later (the quiet minute before counts too). A working call
    can trigger in its last minutes, once the call has ended but the user is still in it: the same signature (no
    audio from the call, nobody talking). A restart there costs one short, empty recording, so one restart per
    call is the default."""

    CHECK_SECS = 10
    LOST_FRACTION = 0.02       # non-zero samples in the call stem's window
    MIC_ALIVE_FRACTION = 0.5   # a working microphone's noise floor is non-zero nearly all the time
    HEARD_FRACTION = 0.5       # a block this full of sound shows that the capture worked

    def __init__(self):
        self.end_call()

    def end_call(self):
        """Nothing is recording: the next recording is a new call. Within one call, a restarted recording
        keeps what the first one showed (the call was heard), so a restart that did not help is noticed."""
        self.restarts, self.heard, self.gave_up, self.last_check = 0, False, False, 0.0
        self.call, self.mic = StemLevels(".system.wav"), StemLevels(".voice.wav")

    def silent_secs(self, now, transcript=None):
        """Seconds of lost call audio (0 while the call is heard or the check is off). Reads only what Minutes
        added since the last call, at most every CHECK_SECS."""
        if CALL_AUDIO_LOST_SECS <= 0 or now - self.last_check < self.CHECK_SECS:
            return 0
        self.last_check = now
        call, mic = self.call.read(CALL_AUDIO_LOST_SECS), self.mic.read(CALL_AUDIO_LOST_SECS)
        if call is None or mic is None:
            return 0
        (covered, share, sound), (mic_covered, mic_share, _) = call, mic
        self.heard = self.heard or sound
        if (not self.heard or covered < CALL_AUDIO_LOST_SECS or share >= self.LOST_FRACTION
                or mic_covered < CALL_AUDIO_LOST_SECS or mic_share < self.MIC_ALIVE_FRACTION):
            return 0
        if transcript is not None:
            rows = read_rows(transcript)
            try:
                last = datetime.fromisoformat(rows[-1]["ts"]).timestamp() if rows else 0
            except (KeyError, TypeError, ValueError):
                last = 0
            if now - last < CALL_AUDIO_LOST_SECS:
                return 0
        return int(covered)


CALL_AUDIO = CallAudioWatch()


class TranscriptFeed:
    """Every ~2 s: stream new live-transcript lines into the notes and fire instant triggers."""

    def __init__(self):
        self.explainer = None
        self.window = None

    def new_meeting(self):
        """Forget the previous meeting's questions and pending lines."""
        if self.explainer:
            self.explainer.reset()
        if self.window:
            self.window.reset()

    def _window(self, copilot):
        if self.window is None:
            self.window = realtime.QuestionWindow(
                llm=LLM, model=MODELS["check"], user=USER_NAME, aliases=USER_ALIASES,
                lang=LANG, explainer=self._explainer(copilot), log=log)
        return self.window

    def _explainer(self, copilot):
        if self.explainer is None:
            self.explainer = realtime.UrgentExplainer(
                llm=LLM, model=MODELS["answer"], verify_model=MODELS["verify"], user=USER_NAME, aliases=USER_ALIASES,
                detail=EXPLAIN.get("detail", "medium"), known_terms=realtime.load_known_terms(KNOWN_TERMS_FILE),
                web_verify=EXPLAIN.get("web_verify", True), max_per_minute=EXPLAIN.get("max_per_minute", 4),
                write=copilot.write, notify=notify, log=log, remember=copilot.remember,
                stream_lock=copilot.stream_lock, lang=LANG)
        return self.explainer

    @staticmethod
    def _flush(copilot):
        """Write buffered transcript lines unless an instant answer is streaming right now."""
        if copilot.pending_lines and copilot.stream_lock.acquire(blocking=False):
            try:
                copilot.write("".join(copilot.pending_lines))
                copilot.pending_lines.clear()
            finally:
                copilot.stream_lock.release()

    def poll(self, copilot, title):
        if not (LIVE_ASR.session and LIVE_ASR.path and LIVE_ASR.path.exists()):
            return
        if copilot.session != LIVE_ASR.session:
            copilot.reset(LIVE_ASR.session, title)
        self._flush(copilot)
        rows = read_rows(LIVE_ASR.path)
        moved = read_relabels(LIVE_ASR.path)[copilot.relabels_seen:]
        if moved:
            copilot.relabels_seen += len(moved)
            names = LIVE_ASR.names()
            for m in moved:
                copilot.pending_lines.append(LANG.t(
                    "speaker_moved", time=realtime.stamp(m["ts"]) if m.get("ts") else "",
                    old=LANG.speaker(i18n.OTHERS, m["from"], names), new=LANG.speaker(i18n.OTHERS, m["to"], names)) + "\n")
            copilot._save_session()
            self._flush(copilot)
        new = rows[copilot.feed_seen:]
        if not new:
            return
        start = copilot.feed_seen
        copilot.feed_seen = len(rows)
        copilot._save_session()
        names = MENTION_NAMES
        speaker_names = LIVE_ASR.names()

        def tag(x):
            return LANG.speaker(x["speaker"], speakers.shown_who(x), speaker_names)

        for i, r in enumerate(new):
            text, speaker = r["text"], r["speaker"]
            if SHOW_TRANSCRIPT and not realtime.filler_only(text):   # still in the transcript and the record
                copilot.pending_lines.append(f"{realtime.stamp(r['ts'])} {tag(r)}｜{text}\n")
                self._flush(copilot)
            if not EXPLAIN.get("enabled", True):
                continue
            if speaker == i18n.SELF:      # the user's own lines are never answered, but give the batch check context
                if "every_line" in INSTANT_TRIGGERS:
                    self._window(copilot).seen(f"[{LANG.speaker(speaker)}] {text}")
                continue
            context = "\n".join(f"[{tag(x)}] {x['text']}" for x in rows[max(0, start + i - 12): start + i])
            line = f"[{tag(r)}] {text}"
            window = self._window(copilot) if "every_line" in INSTANT_TRIGGERS else None
            if "name" in INSTANT_TRIGGERS and names and realtime.mentions(text, names):
                notify(LANG.t("n_heard_name"), text, sound="Ping", kind="heard_name")
                self._explainer(copilot).submit(0, line, context, copilot.last_screen)
            elif "question" in INSTANT_TRIGGERS and realtime.looks_like_question(text):
                self._explainer(copilot).submit(1, line, context, copilot.last_screen)
                if window:                 # second chance if the fast path skips a real question
                    window.add(line, copilot.last_screen)
                    continue
            elif window and realtime.worth_asking(text):
                window.add(line, copilot.last_screen)
                continue
            if window:
                window.seen(line)


FEED = TranscriptFeed()


class Copilot:
    def __init__(self):
        self.lock = threading.Lock()
        self.busy = False
        self.session = None
        self.source = None
        self.last_line = 0
        self.already = []
        self.notes = None
        self.last_shot = None
        self.last_screen = ""
        self.screen_watchers = None
        self.write_lock = threading.Lock()
        self.reset_lock = threading.Lock()
        self.stream_lock = threading.Lock()    # held while an instant answer streams into the notes
        self.pending_lines = []                # transcript lines waiting for that answer to finish
        self.feed_seen = 0                 # live-transcript rows already streamed into the notes
        self.relabels_seen = 0             # turns moved to another voice already told in the notes

    def write(self, text):
        """Append to the live notes; the tick, the explainer and the transcript feed all write here."""
        if not self.notes:
            return
        with self.write_lock, open(self.notes, "a") as f:
            f.write(text)

    def remember(self, entry):
        self.already.append(entry)

    def reset(self, session, title):
        with self.reset_lock:
            if session == self.session:
                return
            self._reset(session, title)

    def _reset(self, session, title):
        if self.pending_lines:                # the old meeting's last lines belong to its notes
            if self.notes:
                self.write("".join(self.pending_lines))
            self.pending_lines.clear()
        self.session = session
        self.last_shot = None
        self.last_screen = ""
        self.screen_watchers = None
        LIVE_DIR.mkdir(parents=True, exist_ok=True)
        saved = self._load_session()
        if not saved:
            saved = self._continued(session)
        if saved and Path(saved["notes"]).exists():
            # Same recording after an autopilot restart: keep writing to the same notes file.
            self.notes = Path(saved["notes"])
            self.last_line = saved.get("last_line", 0)
            self.source = saved.get("source")
            self.already = saved.get("already", [])
            self.feed_seen = saved.get("feed_seen", 0)
            self.relabels_seen = saved.get("relabels_seen", 0)
        else:
            self.last_line = 0
            self.source = None
            self.already = []
            self.feed_seen = 0
            self.relabels_seen = 0
            stamp = datetime.now().strftime("%Y-%m-%d-%H%M")
            safe = re.sub(r"[^\w.-]+", "-", title).strip(".-") or "meeting"   # a path that is easy to type
            self.notes = LIVE_DIR / f"{stamp}-{safe}.md"
            self.notes.write_text(LANG.t("notes_title", title=title, start=f"{datetime.now():%Y-%m-%d %H:%M}"))
        self._save_session()
        # Stable entry point: ~/meetings/live/current.md always points at the active meeting.
        current = LIVE_DIR / "current.md"
        tmp = LIVE_DIR / ".current.md.tmp"
        tmp.unlink(missing_ok=True)
        tmp.symlink_to(self.notes.name)
        os.replace(tmp, current)          # readers always find a current.md
        FEED.new_meeting()
        if OPEN_VIEWER:
            open_viewer()
        log(f"copilot session {session} -> {self.notes}")

    def _continued(self, session):
        """The notes of the meeting this recording continues (restarted after the call's audio was lost), with a line
        saying what is missing; None for a recording of its own."""
        transcript = LIVE_TRANSCRIPTS / f"{session}.jsonl"
        previous = minutes_record.previous_part(transcript)
        if not previous:
            return None
        try:
            saved = json.loads((STATE / f"session-{previous.stem}.json").read_text())
        except (OSError, ValueError):
            return None
        if not Path(saved.get("notes", "")).exists():
            return None
        rows = read_rows(previous) if previous.exists() else []
        try:
            start = datetime.fromisoformat(rows[-1]["ts"]).strftime("%H:%M") if rows else "?"
        except (KeyError, TypeError, ValueError):
            start = "?"
        epoch = minutes_record.epoch_of(transcript)
        end = datetime.fromtimestamp(epoch).strftime("%H:%M") if epoch else "?"
        with open(saved["notes"], "a") as f:
            f.write("\n" + LANG.t("call_gap", start=start, end=end) + "\n\n")
        log(f"copilot session {session} continues the notes of {previous.stem}")
        # This recording's own transcript starts from its first line.
        return {"notes": saved["notes"], "already": saved.get("already", [])}

    def _session_file(self):
        return STATE / f"session-{self.session}.json"

    def _load_session(self):
        try:
            return json.loads(self._session_file().read_text())
        except (OSError, ValueError):
            return None

    def _save_session(self):
        speakers.atomic_write(self._session_file(), json.dumps(
            {"notes": str(self.notes), "last_line": self.last_line, "source": self.source,
             "already": self.already[-80:], "feed_seen": self.feed_seen,
             "relabels_seen": self.relabels_seen},
            ensure_ascii=False))

    def _new_screen_text(self):
        """Screen changes since the last tick: new pages in full, scrolls/updates as new lines only.

        Frames come from our full-resolution capture tool, one folder per display; if it is not
        running (e.g. no Screen Recording permission yet), from Minutes' own 1280-px screenshots
        of the last 2 minutes. Returns (text, paths of every changed frame, oldest first).
        """
        if not SCREEN.get("enabled", True):
            return "", []
        watchers = self.screen_watchers
        if watchers is None:
            watchers = self.screen_watchers = {}
        if SCREEN_CAP.out_dir and SCREEN_CAP.out_dir.is_dir() and SCREEN_CAP.running():
            sources = sorted(p for p in SCREEN_CAP.out_dir.iterdir() if p.is_dir())
        else:
            sources = [SCREENS_DIR] if SCREENS_DIR.is_dir() else []
        events = []
        for src in sources:
            first = src not in watchers
            watcher = watchers.setdefault(src, screen.ScreenWatcher(SCREEN_OCR))
            label = (LANG.t("main_display") if src.name == "display-1"
                     else LANG.t("display_n", n=src.name.replace("display-", "")) if src.name.startswith("display-")
                     else src.name)
            # Minutes' own screenshots are only a fallback: just the last two minutes of them. The same
            # window applies to a watcher's first look, so a restart does not re-read the whole meeting.
            for e in watcher.poll(src, newer_than=time.time() - 120 if first or src == SCREENS_DIR else 0.0):
                e["label"] = label if len(sources) > 1 else ""
                events.append(e)
        if not events:
            return "", []
        events.sort(key=lambda e: Path(e["path"]).stat().st_mtime if Path(e["path"]).exists() else 0)
        for e in events:
            if e["kind"] == "new_page":
                self.last_screen = e["text"][:SCREEN_MAX_CHARS]
        parts = []
        for e in events:
            where = (f"（{e['label']}）" if i18n.chinese(LANG.family) else f" ({e['label']})") if e["label"] else ""
            head = LANG.t("screen_new_page" if e["kind"] == "new_page" else "screen_update", where=where)
            parts.append(f"{head}\n{e['text']}")
        # Every changed frame also goes to Claude as an image (newest kept when over the cap):
        # shared screens, camera tiles, charts and diagrams carry what OCR text cannot.
        cap = SCREEN.get("max_images_per_tick", 20) if SCREEN.get("send_images", True) and LLM.vision else 0
        images = [e["path"] for e in events][-cap:] if cap else []
        return "\n\n".join(parts)[-SCREEN_MAX_CHARS:], images

    def tick(self, title):
        with self.lock:
            if self.busy:
                return
            self.busy = True
        threading.Thread(target=self._tick, args=(title,), daemon=True).start()

    def _minutes_finals(self):
        """Fallback source: Minutes' own live Whisper transcript (no speakers)."""
        # --include-current returns one JSON object (finals + session status);
        # without it the CLI emits bare JSONL lines.
        r = run([MINUTES, "transcript", "--since", f"{CONTEXT_MINUTES}m", "--include-current"], timeout=20)
        if not r or r.returncode != 0:
            return None, []
        data = json.loads(r.stdout)
        session = (data.get("transcript_status") or {}).get("session_id")
        return session, [f for f in data.get("finals") or [] if not is_hallucination(f.get("text", ""), MINUTES_EXACT)]

    def _tick(self, title):
        try:
            if LIVE_ASR_ENABLED and not LIVE_ASR.session and LIVE_ASR.stems_pending():
                return                    # the recording's stems are not there yet
            if LIVE_ASR.session:
                session = LIVE_ASR.session
                if LIVE_ASR.healthy():
                    source, finals = "live", LIVE_ASR.finals(CONTEXT_MINUTES)
                else:
                    source, finals = "minutes", self._minutes_finals()[1]
            else:
                source = "minutes"
                session, finals = self._minutes_finals()
            if session and session != self.session:
                self.reset(session, title)
            if source != self.source:
                if self.source:
                    log(f"transcript source {self.source} -> {source}")
                self.source, self.last_line = source, 0
            new = [f for f in finals if f.get("line", 0) > self.last_line]
            screen, images = self._new_screen_text()
            if not new and not screen:
                return
            old = [f for f in finals if f.get("line", 0) <= self.last_line]

            def fmt(fs):
                return "\n".join(f"[{f.get('ts', '')[11:19]}] {f.get('text', '')}" for f in fs) or LANG.t("none")

            prompt = PROMPT.format(name=USER_NAME, aliases=LANG.t("list_sep").join(USER_ALIASES),
                                   already="\n".join(f"- {a}" for a in self.already[-40:]) or LANG.t("none"),
                                   context=fmt(old), new=fmt(new),
                                   screen=screen or LANG.t("screen_unchanged"),
                                   image_note=LANG.t("image_note", n=len(images)) if images else "",
                                   known=LANG.t("list_sep").join(realtime.load_known_terms(KNOWN_TERMS_FILE))
                                   or LANG.t("none"))
            out = ask_model(prompt, images)
            if out is None:
                MODEL_CHECK.check(title)
                return
            MODEL_CHECK.problem = None
            out = out.strip()
            out = out[out.find("{"): out.rfind("}") + 1]
            result = json.loads(out)
            if not isinstance(result, dict):
                raise ValueError("claude did not return an object")
            self._deliver(result, title)
            self.last_line = max((f.get("line", 0) for f in new), default=self.last_line)
            self._save_session()
        except Exception as e:  # noqa: BLE001
            log(f"tick error: {e!r}")
        finally:
            with self.lock:
                self.busy = False

    def _deliver(self, result, title):
        """Every item and explanation gets its own line in the notes and its own notification."""
        now = datetime.now().strftime("%H:%M")
        parts = []
        if isinstance(result.get("summary"), str) and result["summary"]:
            parts.append(LANG.t("summary", text=result["summary"]))
        answered = [a.split(":", 1)[1] for a in self.already if a.startswith("answered:")]
        for item in result.get("items") or []:
            if not isinstance(item, dict):
                continue
            kind, text = str(item.get("type") or ""), str(item.get("text") or "").strip()
            if not text:
                continue
            seen = [a.split(":", 1)[1] for a in self.already if a.split(":", 1)[0] == kind]
            if kind in ("question_to_me", "mention"):
                seen += answered          # the instant answer already covered it
            if any(realtime.similar(text, prev) for prev in seen):
                log(f"tick: skipped duplicate {kind}: {text[:40]}")
                continue
            owner = (item.get("owner") or "").strip()
            label = ITEM_LABELS.get(kind, ITEM_LABELS["other_todo"])
            if kind == "other_todo" and owner:
                text = f"{owner}{LANG.t('colon')}{text}"
            parts.append(f"- {label}{LANG.t('colon')}{text}")
            self.already.append(f"{kind}: {text}")
            notify(f"{label}{LANG.t('title_sep')}{title}", text, sound="Ping" if kind in TIER1 else "Glass",
                   kind=kind or "other_todo")
        for q in result.get("answers") or []:
            if not isinstance(q, dict):
                continue
            question, answer = str(q.get("question") or "").strip(), str(q.get("answer") or "").strip()
            if not question or not answer:
                continue
            if any(realtime.similar(question, prev) for prev in answered):
                log(f"tick: skipped duplicate explanation: {question[:40]}")
                continue
            parts.append(f"- 💡 **{question}**\n  " + answer.replace("\n", "\n  "))
            self.already.append(f"answered: {question}")
            notify(f"💡 {question}", answer, kind="explain")
        if parts and self.notes:
            # Not in the middle of a streaming instant answer; but never wait forever for one.
            held = self.stream_lock.acquire(timeout=30)
            try:
                self.write(f"\n## {now}\n\n" + "\n".join(parts) + "\n\n")
            finally:
                if held:
                    self.stream_lock.release()


def main():
    log("autopilot started")
    MODEL_CHECK.check("startup")
    copilot = Copilot()
    others_since = None
    free_since = None
    # Remember recordings we started across restarts.
    auto_title = AUTO_FLAG.read_text().strip() if AUTO_FLAG.exists() else ""
    auto_recording = bool(auto_title)
    title = auto_title or LANG.t("meeting")
    started_at = recording_started_at() if auto_recording else 0
    meeting_start = started_at        # the meeting's first start: a restart after lost call audio keeps it
    foreign = False       # a recording started outside the autopilot (in Minutes itself)
    retention_proc = None
    cooldown_until = 0
    last_tick = 0
    try:
        retention_last = float(RETENTION_STAMP.read_text())
    except (OSError, ValueError):
        retention_last = 0.0
    pending_done = None   # (stopped_at, title) while waiting for post-processing
    manual = False        # a recording started from the Mac app: stopped by the user, or with its call
    manual_call = False   # ...a call was on during it: when the call ends, so does the recording
    suppress_auto = False # the user stopped a recording while the call went on: no auto-start until it ends
    restart_requested = False   # the app changed the settings: exit when idle, launchd starts us again
    stop_retry_at = 0     # a failed `minutes stop` is tried again after a short wait
    last_users = []       # mic_users() of the last loop that got an answer

    recording = False

    def publish(recording, processing, extra=()):
        """status.json for the Mac app; written every loop and right before slow Minutes calls."""
        control.write_status({
            "recording": recording, "title": title if recording else "",
            "started_at": started_at if recording else 0, "auto": auto_recording, "manual": manual,
            "paused": control.paused(), "processing": processing,
            "notes": str(copilot.notes) if recording and copilot.notes else "",
            "meetings_dir": str(MEETINGS_DIR), "language": LANG.family, "open_viewer": OPEN_VIEWER,
            "asr_missing": ASR_MISSING,
            "problems": [p for p in (
                config.LOAD_ERROR,
                MODEL_CHECK.problem,
                LANG.t("n_asr_missing").format(model=ASR_MISSING) if ASR_MISSING else None,
                MINUTES_JOBS.problem,
                RECORD_OUTCOMES.problem() if not recording else None,
                *extra,
                LANG.t("n_screen_denied") if recording and SCREEN_CAP.warned else None,
                LANG.t("n_call_audio_lost") if recording and CALL_AUDIO.gave_up else None,
                LANG.t("n_live_asr_stopped") if recording and LIVE_ASR.failed_notified else None) if p],
        })

    loops = 0
    while MAX_LOOPS is None or loops < MAX_LOOPS:
        loops += 1
        try:
            now = time.time()
            users = mic_users()
            users_known = users is not None
            if not users_known:
                users = last_users          # no answer this time: the call timers below stay as they are
            last_users = users
            st = status()
            if "recording" not in st:
                # Status unknown (CLI busy/timeout): don't let a failed poll look like "stopped", but
                # keep the app informed and honour pause/resume, which need no Minutes.
                for command in control.take_commands(only=("pause_auto", "resume_auto")):
                    control.set_paused(command["cmd"] == "pause_auto")   # start/stop wait for Minutes
                publish(recording, False, (LANG.t("n_minutes_unresponsive"),))
                time.sleep(POLL_SECS)
                continue
            recording = bool(st.get("recording"))
            # Status lags right after we start a recording; treat it as running for a grace period.
            if (auto_recording or manual) and now - started_at < START_GRACE_SECS:
                recording = True

            for command in control.take_commands():       # from the Mac app
                cmd = command.get("cmd")
                log(f"app command: {cmd}")
                if cmd in ("pause_auto", "resume_auto"):
                    control.set_paused(cmd == "pause_auto")
                elif cmd == "restart_when_idle":
                    restart_requested = True
                elif cmd == "start" and not recording:
                    title = LANG.t("meeting") + f" {datetime.now():%H%M}"
                    publish(recording, False)
                    r = run([MINUTES, "record", "--call", "--skip-audio-probe", "started from the app",
                             "--title", title, "--consent", "na"], timeout=30)
                    if r and r.returncode == 0:
                        manual, manual_call, started_at, recording = True, False, now, True
                        meeting_start = now
                        MODEL_CHECK.check(title)
                        notify(LANG.t("n_recording_started"), title)
                    else:
                        notify(LANG.t("n_recording_failed"), (r.stderr if r else "")[-160:] or title, sound="Basso")
                elif cmd == "stop" and recording:
                    publish(recording, False)
                    r = run([MINUTES, "stop"], timeout=60)
                    if r and r.returncode == 0:
                        auto_recording = manual = False
                        suppress_auto = True
                        AUTO_FLAG.unlink(missing_ok=True)
                        cooldown_until = now + COOLDOWN_SECS
                        pending_done = (now, title)
                        RECORD_OUTCOMES.add(meeting_start, now, title)
                        recording = False
                        notify(LANG.t("n_recording_stopped"), LANG.t("n_recording_stopped_body", title=title))
                    else:
                        log(f"app stop failed: {(r.stderr or r.stdout)[-200:] if r else 'error'}")
                        notify(LANG.t("n_stop_failed"), title, sound="Basso")
            MODEL_CHECK.recheck()
            draining = LIVE_ASR.running()        # still transcribing the tail
            if recording:
                safely("live transcriber", LIVE_ASR.ensure)
                safely("capture watch", CAPTURE_WATCH.check)
                lost = safely("call audio", CALL_AUDIO.silent_secs, now, LIVE_ASR.path)
                if lost and not users and not browser_call():
                    lost = 0    # no call going on: its silence is no fault
                if lost and (auto_recording or manual) and CALL_AUDIO.restarts < CALL_AUDIO_RESTARTS:
                    # Minutes records, but the call's audio no longer reaches it: a new recording sets up the
                    # capture again. The first part is kept and processed as usual.
                    CALL_AUDIO.restarts += 1
                    log(f"call audio silent for {lost} s: restarting the recording ({CALL_AUDIO.restarts})")
                    LIVE_ASR.carry_from = LIVE_ASR.path
                    publish(recording, False)
                    r = run([MINUTES, "stop"], timeout=60)
                    stopped = bool(r and r.returncode == 0)
                    r = run([MINUTES, "record", "--call", "--skip-audio-probe", "restart: call audio lost",
                             "--title", title, "--consent", "na"], timeout=30) if stopped else r
                    if stopped and r and r.returncode == 0:
                        started_at = now
                        notify(LANG.t("n_call_audio_lost"),
                               LANG.t("n_call_audio_restarted_body", secs=lost, n=CALL_AUDIO.restarts), sound="Basso")
                    else:
                        log(f"call audio restart failed: {((r.stderr or r.stdout)[-160:] if r else 'error')}")
                        CALL_AUDIO.gave_up = True
                        notify(LANG.t("n_call_audio_lost"), LANG.t("n_call_audio_lost_body", secs=lost), sound="Basso")
                        if stopped:      # stopped but not started again: the auto start takes the call back
                            auto_recording = manual = recording = False
                            AUTO_FLAG.unlink(missing_ok=True)
                            pending_done = (now, title)
                            RECORD_OUTCOMES.add(meeting_start, now, title)
                elif lost and not CALL_AUDIO.gave_up:
                    # Out of restarts, or a recording started in Minutes itself (not ours to restart).
                    CALL_AUDIO.gave_up = True
                    log(f"call audio silent for {lost} s: not restarting")
                    notify(LANG.t("n_call_audio_lost"), LANG.t("n_call_audio_lost_body", secs=lost), sound="Basso")
                safely("screen capture", SCREEN_CAP.ensure, LIVE_ASR.session)
                safely("screen permission", SCREEN_CAP.check_permission)
            if (recording or draining) and COPILOT_ENABLED:
                safely("transcript feed", FEED.poll, copilot, title if (auto_recording or manual) else LANG.t("meeting"))
                safely("screen naming", SCREEN_NAMER.poll, copilot)

            if users_known and users:
                others_since = others_since or now
                free_since = None
                manual_call = manual_call or manual
            elif users_known:
                others_since = None
                free_since = free_since or now
                suppress_auto = False      # the call ended; the next one is recorded again

            # Auto start
            if (AUTO_RECORD and not control.paused() and not suppress_auto and not recording and others_since
                    and now - others_since >= START_AFTER_SECS and now >= cooldown_until):
                title = LANG.t("call_title", app=app_label(users), hhmm=f"{datetime.now():%H%M}")
                publish(recording, False)
                r = run([MINUTES, "record", "--call", "--skip-audio-probe", "autopilot: call may start silent",
                         "--title", title, "--consent", "na"], timeout=30)
                ok = bool(r and r.returncode == 0)
                log(f"auto start ({', '.join(users)}): {'ok' if ok else (r.stderr[-200:] if r else 'error')}")
                if ok:
                    auto_recording = True
                    started_at = meeting_start = now
                    recording = True   # status polled above predates the start; don't treat it as "ended"
                    AUTO_FLAG.write_text(title)
                    MODEL_CHECK.check(title)
                    notify(LANG.t("n_recording_started"), LANG.t("n_recording_started_body", title=title, apps=", ".join(users)))
                    if CONSENT_REMINDER:
                        notify(LANG.t("n_consent"), LANG.t("n_consent_body"), sound="Ping")
                else:
                    cooldown_until = now + 60
                    lines = ((r.stderr or r.stdout or "").strip().splitlines() if r else []) or ["unknown"]
                    reason = lines[-1]
                    notify(LANG.t("n_recording_failed"), LANG.t("n_recording_failed_body", title=title, reason=reason[:160]), sound="Basso")

            # Auto stop: recordings we started, and those started from the app whose call has ended
            if (recording and (auto_recording or (manual and manual_call)) and free_since
                    and now - free_since >= STOP_AFTER_SECS and now >= stop_retry_at):
                publish(recording, False)
                r = run([MINUTES, "stop"], timeout=60)
                if r and r.returncode == 0:
                    log("auto stop" + ("" if auto_recording else ": the call of a recording started from the app ended"))
                    auto_recording = manual = manual_call = False
                    AUTO_FLAG.unlink(missing_ok=True)
                    cooldown_until = now + COOLDOWN_SECS
                    pending_done = (now, title)
                    RECORD_OUTCOMES.add(meeting_start, now, title)
                    notify(LANG.t("n_recording_stopped"), LANG.t("n_recording_stopped_body", title=title))
                else:
                    # Still ours: try again shortly rather than leaving Minutes recording for good.
                    log(f"auto stop failed: {((r.stderr or r.stdout)[-160:] if r else 'error')}")
                    if not stop_retry_at:
                        notify(LANG.t("n_stop_failed"), title, sound="Basso")
                    stop_retry_at = now + 30
            if not recording:
                stop_retry_at = 0

            if recording and not auto_recording and not manual and not foreign:
                foreign, title, started_at = True, LANG.t("meeting"), recording_started_at()
                meeting_start = started_at
            if not recording and foreign:
                foreign = False
            if not recording and manual and now - started_at >= START_GRACE_SECS:
                manual = False           # a recording started from the app ended elsewhere
                suppress_auto = bool(users)
            if not recording and auto_recording:
                auto_recording = False   # stopped elsewhere (e.g. in Minutes): don't start it again mid-call
                suppress_auto = bool(users)
                AUTO_FLAG.unlink(missing_ok=True)
                log("auto recording ended elsewhere")

            # Daily data lifecycle, only while idle.
            if (not recording and not st.get("processing") and retention_proc is None
                    and now - retention_last >= 86400):
                retention_last = now
                RETENTION_STAMP.write_text(str(now))
                with open(STATE / "retention.log", "a") as logf:
                    retention_proc = subprocess.Popen([sys.executable, RETENTION],
                                                      stdout=logf, stderr=subprocess.STDOUT)
            if retention_proc is not None and retention_proc.poll() is not None:
                log(f"retention finished (exit {retention_proc.returncode}); see state/retention.log")
                retention_proc = None

            if not recording and SCREEN_CAP.session:
                SCREEN_CAP.stop()
                log(f"screen capture stopped for {SCREEN_CAP.session}")
                SCREEN_CAP.make_video(copilot.notes if copilot.session else None)
                SCREEN_CAP.session = None
            if not recording and not draining and LIVE_ASR.session:
                # The meeting is over and its transcript complete: the next tick must not use it.
                safely("learning voices", SCREEN_NAMER.finish)
                if not STORE_VOICEPRINTS and LIVE_ASR.path:
                    speakers.speakers_path(LIVE_ASR.path).unlink(missing_ok=True)   # other people's voiceprints
                LIVE_ASR.session = LIVE_ASR.path = None
                LIVE_ASR.failed_notified = False
                LIVE_ASR.waiting_since = None
                copilot.notes = copilot.session = None
            if not recording:
                LIVE_ASR.waiting_since = None
                CALL_AUDIO.end_call()

            # Live copilot for any recording
            if COPILOT_ENABLED and (recording or draining) and now - last_tick >= TICK_SECS:
                last_tick = now
                copilot.tick(title if (auto_recording or manual) else LANG.t("meeting"))

            # Post-meeting notice
            if pending_done and not recording and not st.get("processing") and now - pending_done[0] > 10:
                mds = sorted(MEETINGS_DIR.glob("*.md"), key=lambda p: p.stat().st_mtime, reverse=True)
                if mds and mds[0].stat().st_mtime >= pending_done[0]:
                    md = MEETING_RECORD.meeting_of(mds[0])        # a later part's file joins the first's
                    notify(LANG.t("n_minutes_done"), md.name)
                    log(f"meeting done: {md}")
                    pending_done = None
                elif now - pending_done[0] > 1800:
                    pending_done = None
            safely("meeting record", MEETING_RECORD.poll, bool(st.get("processing")))
            safely("minutes jobs", MINUTES_JOBS.poll, recording, bool(st.get("processing")))
            safely("record outcomes", RECORD_OUTCOMES.poll, recording, bool(st.get("processing")))

            publish(recording, bool(st.get("processing")))

            # Settings changed in the app: restart once nothing is in flight (launchd starts us again).
            if (restart_requested and not recording and not draining and not st.get("processing")
                    and retention_proc is None and not SCREEN_CAP.video_running() and not MEETING_RECORD.proc
                    and (pending_done is None or now - pending_done[0] > 120)):
                log("restarting on the app's request (idle)")
                sys.exit(0)
        except Exception as e:  # noqa: BLE001
            log(f"loop error: {e!r}")
        time.sleep(POLL_SECS)


if __name__ == "__main__":
    main()
