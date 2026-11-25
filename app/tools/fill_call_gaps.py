#!/usr/bin/env python3
"""Fill a meeting's record with the call's speech its live transcript missed (lib/call_gaps.py), then write the
meeting file's transcript again and resummarize it, as the engine does after a meeting.

  fill_call_gaps.py <meeting .md> [--apply]
  fill_call_gaps.py <meeting .md> --apply --no-record --stems-only     (the engine, before writing the record)

Without --apply it only lists what it would add, and changes nothing. With --apply, each changed live transcript is
first copied beside it (state/live/<name>.<time>.bak, expired with it by retention) and the meeting file to
state/backups/. Recovered lines are the others' with no letter (call_gaps.recover). When a
recording's call stem is gone, the merged recording (<name>.wav) is used instead, unless --stems-only: speech no row
covers, not in the user's voice. --no-record: only the live transcripts (the engine writes the record itself).
"""
import argparse
import os
import shutil
import subprocess
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "lib"))
import asr  # noqa: E402
import call_gaps  # noqa: E402
import config  # noqa: E402
import i18n  # noqa: E402
import minutes_record  # noqa: E402
import speakers  # noqa: E402
import voiceprint  # noqa: E402


def mixed_of(meetings, epoch):
    """The merged recording of the recording that began at `epoch`, when its stems are gone."""
    for p in Path(meetings).glob("*.wav"):
        if p.name.endswith((".voice.wav", ".system.wav")):
            continue
        try:
            if abs(os.stat(p).st_birthtime - epoch) < 5:
                return p
        except (OSError, AttributeError):
            pass
    return None


def backup(state, path):
    """A copy beside the file, as <name>.<time>.bak: a live transcript's copy stays in state/live, where
    [retention] live_notes_days expires it with the transcript."""
    path = Path(path)
    folder = path.parent if path.suffix == ".jsonl" else state / "backups"
    folder.mkdir(exist_ok=True)
    shutil.copy2(path, folder / f"{path.name}.{time.strftime('%Y%m%d-%H%M%S')}.bak")


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("meeting", type=Path)
    ap.add_argument("--apply", action="store_true")
    ap.add_argument("--no-record", action="store_true", help="fill the live transcripts only")
    ap.add_argument("--stems-only", action="store_true", help="no merged-recording fallback")
    a = ap.parse_args()
    cfg = config.load()
    lang = i18n.Lang(cfg)
    state, meetings = config.state_dir(), config.meetings_dir(cfg)
    text = a.meeting.read_text(encoding="utf-8")
    started = minutes_record.meeting_start(text)
    live = minutes_record.live_transcript(state / "live", started) if started else None
    if not live:
        raise SystemExit(f"{a.meeting.name}: no live transcript of ours for it")
    parts = minutes_record.parts(live) or [live]
    first = minutes_record.meeting_file(meetings, minutes_record.epoch_of(parts[0]))
    if len(parts) > 1 and first and first.resolve() != a.meeting.resolve():
        raise SystemExit(f"{a.meeting.name} is a later part of a meeting: run this on {first.name}")
    tr = asr.Transcriber()
    mine = threshold = None                  # the user's voiceprint: only for a merged recording (loaded then)
    found = {}
    for part in parts:
        epoch = minutes_record.epoch_of(part)
        stem, mixed = call_gaps.saved_stem(meetings, epoch), False
        if not stem and a.stems_only:
            print(f"{part.name}: no saved call stem")
            continue
        if not stem:
            stem, mixed = mixed_of(meetings, epoch), True
            if stem and mine is None:
                mine, threshold = voiceprint.from_config(cfg)
            if not stem or mine is None:
                print(f"{part.name}: no saved call stem" + ("" if stem else " or merged recording"))
                continue
            print(f"{part.name}: no call stem; using the merged recording {stem.name}")
        try:
            rows = call_gaps.recover(part, stem, tr, mine if mixed else None, epoch, mixed=mixed,
                                     threshold=threshold if mixed else 0.4)
        except Exception as e:  # noqa: BLE001 - one unreadable stem must not cost the other parts their lines
            print(f"{part.name}: skipped ({e!r})")
            continue
        found[part] = rows
        print(f"{part.name}: {len(rows)} lines, {sum(r['end'] - r['start'] for r in rows):.0f} s of the call to add")
    if not a.apply or not any(found.values()):
        return
    for part, rows in found.items():
        if rows:
            backup(state, part)
            call_gaps.insert(part, rows)
    if a.no_record:
        return
    backup(state, a.meeting)

    v = cfg.get("voice", {})
    people = (speakers.People(v.get("people_file") or "~/.config/meeting-copilot/people.json")
              if cfg.get("privacy", {}).get("store_voiceprints", True) else None)
    names = speakers.resolve_names(parts[-1], people)
    user = cfg["user"]["name"] or lang.t("user_fallback")

    def label(row):
        return user if row.get("speaker") == i18n.SELF else lang.speaker(i18n.OTHERS, speakers.shown_who(row), names)

    if len(parts) > 1:
        lines = minutes_record.merged_lines(parts, label, lambda secs: lang.t("record_gap", secs=secs))
    else:
        lines = minutes_record.transcript_lines(minutes_record.read_rows(parts[0]), label)
    new = minutes_record.with_transcript(text, lines)
    if not new:
        raise SystemExit(f"{a.meeting.name}: no transcript section to replace")
    a.meeting.write_text(new, encoding="utf-8")
    print(f"{a.meeting.name}: transcript written again; resummarizing")
    r = subprocess.run([str(config.minutes_cli(cfg)), "resummarize", str(a.meeting), "--apply"],
                       stdin=subprocess.DEVNULL, capture_output=True, text=True, timeout=900)
    print(f"resummarize exit {r.returncode}")


if __name__ == "__main__":
    main()
