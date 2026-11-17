"""File-based bridge between the background program and the Mac app.

The autopilot writes state/status.json on every loop; the app reads it. The app asks for things by
dropping JSON files into state/commands/ ({"cmd": "start" | "stop" | "pause_auto" | "resume_auto" |
"restart_when_idle"}); the autopilot runs and deletes them. restart_when_idle (after the settings
changed) makes it exit once no meeting is recorded or being wrapped up; launchd starts it again. The app touches state/app.heartbeat while it runs, so the
autopilot knows to leave the live notes to the app's window instead of opening Terminal.

While the app runs it also shows the notifications (state/notifications/, {"title", "text", "sound", "open"}), as
its own: macOS credits one sent with osascript to Script Editor, and clicking it opens Script Editor.
"""
import json
import os
import time
import uuid

import config

STATE = config.state_dir()
STATUS = STATE / "status.json"
COMMANDS = STATE / "commands"
PAUSED = STATE / "auto-paused"
NOTIFICATIONS = STATE / "notifications"
HEARTBEAT = STATE / "app.heartbeat"
HEARTBEAT_SECS = 10


def write_status(status):
    STATE.mkdir(parents=True, exist_ok=True)
    tmp = STATUS.with_suffix(".tmp")
    tmp.write_text(json.dumps({**status, "updated": time.time()}, ensure_ascii=False))
    os.replace(tmp, STATUS)        # readers never see a half-written file


def take_commands(only=None):
    """Commands from the app, oldest first; each is removed once read. With `only`, commands of other
    kinds stay in place for a later call (used while Minutes is not responding)."""
    if not COMMANDS.is_dir():
        return []
    out = []
    for p in sorted(COMMANDS.glob("*.json")):
        try:
            command = json.loads(p.read_text())
        except (OSError, ValueError):
            p.unlink(missing_ok=True)
            continue
        if only is not None and (not isinstance(command, dict) or command.get("cmd") not in only):
            continue
        out.append(command)
        p.unlink(missing_ok=True)
    return out


def paused():
    return PAUSED.exists()


def set_paused(value):
    if value:
        STATE.mkdir(parents=True, exist_ok=True)
        PAUSED.touch()
    else:
        PAUSED.unlink(missing_ok=True)


def app_running():
    try:
        return time.time() - HEARTBEAT.stat().st_mtime < HEARTBEAT_SECS
    except OSError:
        return False


def notify_app(title, text, sound, open_path=""):
    """Hand a notification to the app. `open_path`: the notes a click on it opens. False if it could not be written."""
    try:
        NOTIFICATIONS.mkdir(parents=True, exist_ok=True)
        name = f"{int(time.time() * 1000)}-{uuid.uuid4().hex[:8]}.json"
        tmp = NOTIFICATIONS / f".{name}"
        tmp.write_text(json.dumps({"title": title, "text": text, "sound": sound, "open": open_path}, ensure_ascii=False))
        os.replace(tmp, NOTIFICATIONS / name)      # the app never sees a half-written file
        return True
    except OSError:
        return False
