"""Point the Minutes app's settings at what meeting-copilot needs.

Sets in ~/.config/minutes/config.toml, keeping every other line as it is:
  [recording] capture_backend = "core-audio-tap"   per-process system audio, own stems
  [recording.sources] call = "auto", voice = <[audio] mic_device>
  [screen_context] enabled = true, keep_after_summary = true
  [transcription] language = <zh for zh-*>
  [voice] store_meeting_embeddings, retain_non_self_embeddings = <[privacy] store_voiceprints>
  [diarization] engine = "pyannote-rs", model = "community-1"   when its models are installed
      (noticeably more accurate than the legacy engine)
  [live_transcript] recording_sidecar = false   while [asr] live is on: our own live transcription replaces
      Minutes' during a recording

The file as it was before the first change is kept next to it as config.toml.pre-meeting-copilot,
and the keys written here are recorded in ~/.config/meeting-copilot/minutes-managed.json, so a key
is only ever removed or put back while it still holds the value this tool wrote.

Usage: setup_minutes.py [--dry-run]     apply the settings
       setup_minutes.py --undo          put them back (uninstall)
       setup_minutes.py --recording-config <file>
                                        write Minutes' config for a recording we start (lib/minutes_record.py):
                                        exit 1, writing nothing, when Minutes' own transcript is the record
"""
import json
import re
import shutil
import sys
import tomllib
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "lib"))
import config  # noqa: E402
import minutes_record  # noqa: E402
import tomledit  # noqa: E402

MINUTES_CONFIG = Path.home() / ".config" / "minutes" / "config.toml"
BACKUP = MINUTES_CONFIG.with_name("config.toml.pre-meeting-copilot")   # before our first change
MANAGED = config.USER_CONFIG.parent / "minutes-managed.json"           # "section.key" -> value we wrote


def set_key(text, section, key, value):
    """Set key = value inside [section]; an entry that already has this value is left untouched."""
    if tomledit.get_key(text, section, key) == value:
        return text
    return tomledit.set_key(text, section, key, value)


del_key = tomledit.del_key


def _managed():
    try:
        data = json.loads(MANAGED.read_text())
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def _read():
    """Minutes' config text, or None when it cannot be parsed (then it is left alone)."""
    text = MINUTES_CONFIG.read_text() if MINUTES_CONFIG.exists() else ""
    try:
        tomllib.loads(text)
    except tomllib.TOMLDecodeError as e:
        print(f"warning: {MINUTES_CONFIG} cannot be parsed ({e}); Minutes settings left alone", file=sys.stderr)
        return None
    return text


def _write(text, new, dry_run, backup=True):
    if new == text:
        print(f"{MINUTES_CONFIG}: already set")
        return False
    tomllib.loads(new)                      # never leave Minutes with a config it can't read
    if dry_run:
        print(new)
        return False
    MINUTES_CONFIG.parent.mkdir(parents=True, exist_ok=True)
    if backup and text and not BACKUP.exists():
        shutil.copy2(MINUTES_CONFIG, BACKUP)           # same permissions as the original
    MINUTES_CONFIG.write_text(new)                     # in place: the file keeps its mode
    print(f"{MINUTES_CONFIG}: updated")
    return True


def _original():
    """The pre-install Minutes config, parsed, or {}."""
    try:
        return tomllib.loads(BACKUP.read_text()) if BACKUP.exists() else {}
    except (OSError, tomllib.TOMLDecodeError):
        return {}


def _revert(text, dotted, value, original):
    """Put one key we wrote back to its pre-install value, or remove it; only while it still holds ours."""
    section, key = dotted.rsplit(".", 1)
    if tomledit.get_key(text, section, key) != value:
        return text
    before = original
    for part in section.split("."):
        before = before.get(part) if isinstance(before, dict) else None
    if isinstance(before, dict) and key in before:
        return tomledit.set_key(text, section, key, before[key])
    return del_key(text, section, key)


def wanted_settings(cfg):
    keep = bool(cfg.get("privacy", {}).get("store_voiceprints"))
    wanted = {"recording.capture_backend": "core-audio-tap", "recording.sources.call": "auto",
              "screen_context.enabled": True, "screen_context.keep_after_summary": True,
              "voice.store_meeting_embeddings": keep, "voice.retain_non_self_embeddings": keep}
    # `minutes devices` prints "Name (48000Hz, 1 ch)"; Minutes wants just the name.
    mic = re.sub(r"\s*\(\d+Hz, \d+ ch\)$", "", cfg.get("audio", {}).get("mic_device", "") or "").strip()
    if mic:
        wanted["recording.sources.voice"] = mic
    if (Path.home() / ".minutes" / "models" / "diarization" / "segmentation-community-1.onnx").exists():
        wanted["diarization.engine"] = "pyannote-rs"
        wanted["diarization.model"] = "community-1"
    if config.language_family(cfg).startswith("zh"):
        wanted["transcription.language"] = "zh"
    if cfg.get("asr", {}).get("live", True):
        # Minutes' live Whisper beside our own transcriber can hold several GB for a whole call; on Macs
        # with less memory that pushes the system into swap.
        wanted["live_transcript.recording_sidecar"] = False
    return wanted


def main(dry_run=False):
    cfg = config.load()
    text = _read()
    if text is None:
        return
    wanted, managed = wanted_settings(cfg), _managed()
    new = text
    for dotted, value in wanted.items():
        section, key = dotted.rsplit(".", 1)
        new = set_key(new, section, key, value)
    # Keys we set earlier and no longer want (microphone back to the default, language back to
    # English) go back to their pre-install value, or away, while they still hold our value.
    original = _original()
    dropped = {dotted: value for dotted, value in managed.items() if dotted not in wanted}
    for dotted, value in dropped.items():
        new = _revert(new, dotted, value, original)
    new = _drop_added_sections(new, dropped, original)
    _write(text, new, dry_run)
    if not dry_run:
        MANAGED.parent.mkdir(parents=True, exist_ok=True)
        MANAGED.write_text(json.dumps(wanted, ensure_ascii=False, indent=1))


def undo():
    """Uninstall: every key we wrote goes back to its pre-install value, or away, while it still holds
    our value. Minutes settings the user changed since are kept."""
    text = _read()
    if not text:
        return
    managed, original = _managed(), _original()
    new = text
    for dotted, value in managed.items():
        new = _revert(new, dotted, value, original)
    if not managed and tomledit.get_key(new, "screen_context", "keep_after_summary") is True:
        # An install older than this record: at least stop Minutes keeping every screenshot.
        new = tomledit.set_key(new, "screen_context", "keep_after_summary", False)
    new = _drop_added_sections(new, managed, original)
    _write(text, new, dry_run=False, backup=False)
    MANAGED.unlink(missing_ok=True)


def _drop_added_sections(text, keys, original):
    """Sections of these "section.key"s that the pre-install config did not have go once they are empty."""
    for section in sorted({d.rsplit(".", 1)[0] for d in keys}, key=len, reverse=True):
        before = original
        for part in section.split("."):
            before = before.get(part) if isinstance(before, dict) else None
        if not isinstance(before, dict):
            text = tomledit.del_empty_section(text, section)
    return text


def recording_config(out):
    """Minutes' config for a recording we start: the user's, with a lighter pass after it stops, since our own
    transcript replaces Minutes' (minutes_record.RECORDING_OVERRIDES). False, writing nothing, without a live
    transcript of ours to take its place, or with a config that cannot be read."""
    if not config.load().get("asr", {}).get("live", True):
        return False
    text = _read()
    if text is None:
        return False
    for dotted, value in minutes_record.RECORDING_OVERRIDES.items():
        section, key = dotted.rsplit(".", 1)
        text = set_key(text, section, key, value)
    tomllib.loads(text)
    out = Path(out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(text)
    return True


if __name__ == "__main__":
    if "--recording-config" in sys.argv:
        sys.exit(0 if recording_config(sys.argv[sys.argv.index("--recording-config") + 1]) else 1)
    if "--undo" in sys.argv:
        undo()
    else:
        main(dry_run="--dry-run" in sys.argv)
