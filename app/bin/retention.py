#!/usr/bin/env python3
"""Data lifecycle for meeting recordings.

Applies [retention] from the config. Expired files are moved to the Trash
(~/.Trash/meeting-copilot-retention/<date>/), never deleted outright, so a wrong
rule can be undone. Transcripts are archived instead of trashed.

  transcripts_days  ~/meetings/*.md                        -> ~/meetings/archive/<year>/
  audio_days        ~/meetings/*.wav, .voice.wav, .system.wav
  voiceprints_days  ~/meetings/.*.embeddings
  screenshots_days  app/state/screens/*, ~/.minutes/screens/*
  video_days        app/state/video/*
  live_notes_days   ~/meetings/live/*.md, app/state/live/*.jsonl, app/state/session-*.json

Usage: retention.py [--dry-run]
"""
import shutil
import sys
import time
from datetime import datetime
from pathlib import Path

APP = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(APP / "lib"))
import config  # noqa: E402

DAY = 86400


def age_days(p):
    return (time.time() - p.stat().st_mtime) / DAY


def main(dry_run=False):
    cfg = config.load()
    r = cfg.get("retention", {})
    meetings = config.meetings_dir(cfg)
    state = config.state_dir()
    trash = Path.home() / ".Trash" / "meeting-copilot-retention" / datetime.now().strftime("%Y-%m-%d")
    actions = []

    def expire(paths, days, label):
        if not days:
            return
        for p in paths:
            if p.exists() and not p.is_symlink() and age_days(p) > days:
                actions.append(("trash", label, p))

    if meetings.exists():
        expire([p for p in meetings.glob("*.wav")], r.get("audio_days"), "audio")
        expire(meetings.glob(".*.embeddings"), r.get("voiceprints_days"), "voiceprint")
        expire((meetings / "live").glob("*.md"), r.get("live_notes_days"), "live notes")
        if r.get("transcripts_days"):
            for p in meetings.glob("*.md"):
                if age_days(p) > r["transcripts_days"]:
                    actions.append(("archive", "transcript", p))
    expire((state / "screens").glob("*"), r.get("screenshots_days"), "screenshots")
    expire((Path.home() / ".minutes" / "screens").glob("*"), r.get("screenshots_days"), "screenshots")
    expire((state / "video").glob("*"), r.get("video_days"), "video")
    expire((state / "live").glob("*.jsonl"), r.get("live_notes_days"), "live transcript")
    expire((state / "live").glob("*.json"), r.get("live_notes_days"), "live speakers")   # voiceprints of the meeting
    expire((state / "live").glob("*.mode"), r.get("live_notes_days"), "live mode")
    expire(state.glob("session-*.json"), r.get("live_notes_days"), "session state")

    for action, label, p in actions:
        if action == "archive":
            dest = meetings / "archive" / datetime.fromtimestamp(p.stat().st_mtime).strftime("%Y") / p.name
        else:
            rel = p.relative_to(Path.home()) if p.is_relative_to(Path.home()) else p.relative_to(p.anchor)
            dest = trash / rel
        print(f"{'would ' if dry_run else ''}{action} {label}: {p} -> {dest}")
        if not dry_run:
            try:
                dest.parent.mkdir(parents=True, exist_ok=True)
                shutil.move(str(p), str(dest))
            except OSError as e:
                print(f"  could not move {p}: {e}")     # the rest still gets processed
    print(f"{len(actions)} item(s) {'to process' if dry_run else 'processed'}")
    return len(actions)


if __name__ == "__main__":
    main(dry_run="--dry-run" in sys.argv)
