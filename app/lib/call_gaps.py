"""Speech on the call's track that the live transcript does not have (G-10).

During a meeting the live transcriber reads Minutes' growing call stem. When that stem misses speech the call stem
Minutes saves at the end does hold, the meeting record would miss it too. Before the record is written, the saved
stem's speech spans that no row of the transcript covers are transcribed and added as the others' lines, with no
letter (see recover). (2026-11-24's lost minutes were not in the saved stem either: Minutes' capture had broken; one
line of the call was recovered.)
"""
import json
import os
from pathlib import Path

import numpy as np

import asr
import i18n
import minutes_record
import speakers

COVERED = 0.5          # a span this much inside rows the transcript already has is not a gap
MIN_SECS = 0.6         # shorter speech is not worth a line


def read_stem(path):
    """A stem as float32 mono at asr.SAMPLE_RATE (Minutes writes 16 kHz mono 16-bit)."""
    import wave
    with wave.open(str(path)) as w:
        if w.getframerate() != asr.SAMPLE_RATE or w.getnchannels() != 1:
            raise ValueError(f"{path}: {w.getframerate()} Hz, {w.getnchannels()} channels")
        return np.frombuffer(w.readframes(w.getnframes()), dtype="<i2").astype(np.float32) / 32768


def gaps(spans, rows):
    """The speech spans (start, end) that the call track's rows do not cover."""
    have = [(r["start"], r["end"]) for r in rows]
    out = []
    for a, b in spans:
        if b - a < MIN_SECS:
            continue
        inside = sum(max(0.0, min(b, e) - max(a, s)) for s, e in have)
        if inside < COVERED * (b - a):
            out.append((a, b))
    return out


saved_stem = minutes_record.saved_stem


def recover(transcript, system_stem, tr, voiceprint, started_at, log=print, mixed=False, threshold=0.4):
    """Rows for the call track's speech that `transcript` misses, from the saved `system_stem`, transcribed with
    `tr` (asr.Transcriber). `started_at`: the epoch the recording began (for the rows' wall-clock times). `mixed`:
    the stem is the merged recording (both tracks, the call stem gone): a gap is speech no row of either track
    covers, and a line in the user's voice (`voiceprint` enrolled, scoring `threshold`: [voice] threshold) is left
    out. `voiceprint` is used only then (None otherwise).
    The lines are the others' with no letter: giving them letters after the meeting (grouping them with the voices it
    kept, across its parts, with merges found meanwhile) put other people's words under a wrong name in every way
    tried, and a missing line is worse than an unnamed one."""
    audio = read_stem(system_stem)
    rows = speakers.read_transcript(transcript)
    # The call's lines: on its track, or others the microphone kept (room mode, room mode for now): what the saved
    # call stem holds of them is not missing.
    have = rows if mixed else [r for r in rows if speakers.track_of(r) == "system" or r.get("speaker") == i18n.OTHERS]
    out = []
    for a, b in gaps(tr.speech_spans(audio), have):
        piece = audio[int(a * asr.SAMPLE_RATE):int(b * asr.SAMPLE_RATE)]
        seg = tr.transcribe_segment(piece, a, b)
        if seg.engine == "dropped" or not seg.text:
            continue
        if mixed and getattr(voiceprint, "enrolled", None) is not None and (voiceprint.score(piece) or 0) >= threshold:
            continue                                      # the user's own voice: not the call's
        out.append({"ts": _iso(started_at + a), "start": round(a, 2), "end": round(b, 2), "speaker": i18n.OTHERS,
                    "text": seg.text, "engine": seg.engine, "track": "system", "recovered": True})
        log(f"recovered {a:.1f}-{b:.1f}s: {seg.text[:40]}")
    return out


def insert(transcript, rows):
    """Add `rows` to the transcript file in time order among its lines (corrections stay where they were)."""
    if not rows:
        return
    lines = Path(transcript).read_text(encoding="utf-8").splitlines()
    out, pending = [], sorted(rows, key=lambda r: r["start"])
    for line in lines:
        try:
            start = json.loads(line).get("start")
        except ValueError:
            start = None
        while pending and start is not None and pending[0]["start"] < start:
            out.append(json.dumps(pending.pop(0), ensure_ascii=False))
        out.append(line)
    out += [json.dumps(r, ensure_ascii=False) for r in pending]
    st = Path(transcript).stat()
    speakers.atomic_write(transcript, "\n".join(out) + "\n")
    os.utime(transcript, (st.st_atime, st.st_mtime))   # its age says when the recording ended (part waits)


def _iso(epoch):
    from datetime import datetime
    return datetime.fromtimestamp(epoch).isoformat(timespec="seconds")
