"""Minutes' meeting record, rebuilt from our live transcript.

Minutes transcribes every recording again after it stops, with Whisper, and there is no way to turn that off. For the
recordings we start it runs with a config of its own (RECORDING_OVERRIDES: a small Whisper model, no summary), which
took the pass from about 4 GB down; then the meeting file's transcript is replaced by ours (TEA, with speakers and
names) and `minutes resummarize` writes the summary, action items and people from it.

A meeting file is matched to our transcript by its start: the frontmatter `date` and the live transcript's
rec-<epoch> name are the same recording's start, a second or two apart.
"""
import json
import os
import re
import tomllib
from datetime import datetime
from pathlib import Path

import speakers

RECORDING_OVERRIDES = {"transcription.model": "small", "summarization.engine": "none"}


def model_file(minutes_text, model):
    """The file of Whisper model `model` in Minutes' model folder ([transcription] model_path of its config text)."""
    folder = Path.home() / ".minutes" / "models"
    try:
        folder = Path(tomllib.loads(minutes_text).get("transcription", {}).get("model_path") or folder).expanduser()
    except tomllib.TOMLDecodeError:
        pass
    return folder / f"ggml-{model}.bin"


def recording_model_missing(minutes_config=None):
    """The Whisper model our recordings ask Minutes for (RECORDING_OVERRIDES) when its file is not installed, else
    None. Minutes' own setup and health check only look at the model in the user's config, so a user on another
    model never gets this one, and every post-meeting job then fails ("Transcription model not found")."""
    model = RECORDING_OVERRIDES["transcription.model"]
    return None if model_installed(recording_model_path(minutes_config)) else model


def recording_model_path(minutes_config=None):
    """Where the Whisper model our recordings ask for goes (Minutes' model folder from its config)."""
    try:
        text = Path(minutes_config or Path.home() / ".config" / "minutes" / "config.toml").read_text()
    except OSError:
        text = ""
    return model_file(text, RECORDING_OVERRIDES["transcription.model"])


MODEL_MIN_BYTES = 50_000_000                  # the smallest Whisper model is ~75 MB: less is a download cut short


def model_installed(path):
    """A model file, and not one an interrupted download left behind."""
    try:
        return path.is_file() and path.stat().st_size >= MODEL_MIN_BYTES
    except OSError:
        return False
MATCH_SECS = 30
DATE = re.compile(r"^date:\s*(\S+)\s*$", re.M)


def meeting_start(text):
    """Epoch seconds of a meeting file's `date`, or None."""
    head = text.split("\n---", 2)[0] if text.startswith("---") else ""
    m = DATE.search(head)
    if not m:
        return None
    try:
        return datetime.fromisoformat(m.group(1).strip("'\"")).timestamp()
    except ValueError:
        return None


def live_transcript(live_dir, started):
    """Our transcript of the recording that started at `started` (epoch s): rec-<epoch>.jsonl, or None."""
    best = None
    for path in Path(live_dir).glob("rec-*.jsonl"):
        try:
            epoch = int(path.stem.split("-", 1)[1])
        except (IndexError, ValueError):
            continue
        if abs(epoch - started) <= MATCH_SECS and (best is None or abs(epoch - started) < abs(best[0] - started)):
            best = (epoch, path)
    return best[1] if best else None


def stamp(secs):
    secs = max(0, int(secs))
    return f"{secs // 60}:{secs % 60:02d}"


def transcript_lines(rows, label):
    """Minutes' transcript format, `[Name m:ss] text`, for rows of a live transcript; label(row) names the speaker."""
    return [f"[{label(r)} {stamp(r.get('start', 0))}] {r['text'].strip()}" for r in rows if r.get("text", "").strip()]


def with_transcript(text, lines):
    """The meeting file with its `## Transcript` section replaced by these lines; None when it has none."""
    m = re.search(r"^## Transcript[ \t]*\n", text, re.M)
    if not m or not lines:
        return None
    rest = text[m.end():]
    nxt = re.search(r"^## ", rest, re.M)
    tail = rest[nxt.start():] if nxt else ""
    return text[:m.end()] + "\n" + "\n".join(lines) + "\n" + ("\n" + tail if tail else "")


def read_rows(path):
    """Rows of our live transcript, with its later corrections (a turn moved to another voice, a line the screen
    showed someone else saying) applied."""
    return speakers.read_transcript(path)


# A recording restarted mid-call (the call's audio was lost) is one meeting in several recordings: each later part
# says which recording came first (rec-<epoch>.part.json), so the live notes and the meeting record stay one.
def part_path(transcript):
    return Path(str(transcript).replace(".jsonl", ".part.json"))


def epoch_of(transcript):
    try:
        return int(Path(transcript).stem.split("-", 1)[1])
    except (IndexError, ValueError):
        return None


def mark_part(transcript, previous):
    """`transcript` continues the meeting `previous` was recording."""
    first = first_part(previous)
    speakers.atomic_write(part_path(transcript), json.dumps({"first": first.stem, "previous": Path(previous).stem}))


def _part(transcript):
    try:
        return json.loads(part_path(transcript).read_text())
    except (OSError, ValueError):
        return {}


def first_part(transcript):
    """The first recording of the meeting `transcript` belongs to (itself when it is not a later part)."""
    first = _part(transcript).get("first")
    return Path(transcript).with_name(f"{first}.jsonl") if first else Path(transcript)


def previous_part(transcript):
    prev = _part(transcript).get("previous")
    return Path(transcript).with_name(f"{prev}.jsonl") if prev else None


def parts(transcript):
    """Every recording of the meeting `transcript` belongs to, first to last."""
    first = first_part(transcript)
    out = [first] if first.exists() else []
    for path in Path(first).parent.glob("rec-*.part.json"):
        t = path.with_name(path.name.replace(".part.json", ".jsonl"))
        if t != first and first_part(t) == first and t.exists():
            out.append(t)
    return sorted(out, key=lambda p: epoch_of(p) or 0)


def saved_stem(meetings, epoch, suffix=".system.wav"):
    """A stem Minutes saved for the recording that began at `epoch` (a stem keeps its birth time when moved; the call
    stem can be made a second after the microphone's, which named the recording), or None (not saved yet, or
    deleted)."""
    best = None
    for p in list(Path(meetings).glob("*" + suffix)) + list(Path(meetings).glob(".parts/*" + suffix)):
        try:
            off = abs(os.stat(p).st_birthtime - epoch)
        except (OSError, AttributeError):
            continue
        if off < 3 and (best is None or off < best[0]):
            best = (off, p)
    return best[1] if best else None


def merged_lines(transcripts, label, gap):
    """Transcript lines of a meeting recorded in parts: times run on from the first part's start, and gap(secs) gives
    the line between two parts (the call's audio lost, then recorded again)."""
    lines, first, end = [], epoch_of(transcripts[0]) or 0, None
    for t in transcripts:
        offset = (epoch_of(t) or first) - first
        rows = read_rows(t)
        if end is not None:
            lines.append(gap(max(0, int(offset - end))))
        lines += [f"[{label(r)} {stamp(r.get('start', 0) + offset)}] {r['text'].strip()}"
                  for r in rows if r.get("text", "").strip()]
        ends = [r.get("end", 0) for r in rows if "end" in r]
        end = offset + max(ends) if ends else offset
    return lines


def meeting_file(meetings_dir, started):
    """Minutes' meeting file of the recording that started at `started` (epoch s), or None."""
    for md in Path(meetings_dir).glob("*.md"):
        try:
            head = md.read_text(encoding="utf-8", errors="replace")[:2000]
        except OSError:
            continue
        at = meeting_start(head)
        if at is not None and abs(at - started) <= MATCH_SECS:
            return md
    return None

