#!/usr/bin/env python3
"""Live per-speaker transcription of an in-progress Minutes recording.

Minutes writes <base>.voice.wav (the user's microphone) and <base>.system.wav (call
audio) while it records: 16 kHz mono 16-bit PCM with a 44-byte header whose sizes
are only filled in at the end. This follows both files as they grow, cuts speech with
streaming Silero VAD, transcribes each finished utterance with app/lib/asr.py, and
appends one JSON line per utterance to the output file:

  {"ts": "2026-01-15T09:31:35", "start": 95.2, "end": 99.8,
   "speaker": "我"|"其他人", "text": "...", "engine": "primary"|"fallback",
    "who": "A"}   (other participants only: which voice, see lib/speakers.py)

Exits after the recording stops (files stop growing and `minutes status` says
not recording) and the remaining speech has been transcribed.

Usage: live_transcriber.py <recording.wav> <out.jsonl>
"""
import bisect
import json
import os
import queue
import sys
import threading
import time
from array import array
from datetime import datetime
from pathlib import Path

import numpy as np
from silero_vad import VADIterator, load_silero_vad

APP = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(APP / "lib"))
# Never download a model during a meeting: a missing one used to be fetched while the meeting went on
# without a transcript, and a download can take minutes. Set before huggingface_hub is imported (via asr).
os.environ["HF_HUB_OFFLINE"] = "1"
import asr  # noqa: E402
import i18n  # noqa: E402
import speakers  # noqa: E402
import voiceprint  # noqa: E402

HEADER_BYTES = 44
POLL_SECS = 0.5
VAD_CHUNK = 512                     # Silero streaming window at 16 kHz
MAX_IDLE_SECS = 120      # no growth at all for this long: the recording is over, whatever `status` says
IDLE_EXIT_SECS = 20                 # no growth this long after recording stopped -> finish
MEMORY_LOG_SECS = 300               # how often the model's memory goes to the log
TRACKS = (("voice", i18n.SELF), ("system", i18n.OTHERS))


def log(msg):
    print(f"{datetime.now():%H:%M:%S} {msg}", file=sys.stderr, flush=True)


class TrackFollower:
    """Reads new PCM from a growing WAV and emits finished speech segments."""

    def __init__(self, path, speaker, vad_model, out_q):
        self.path, self.speaker, self.out_q = path, speaker, out_q
        self.offset = HEADER_BYTES
        self.inode = None                 # the stem file's identity: a new recording means a new file
        self.finished = False
        self.samples_read = 0
        self.base_chunk = 0               # VAD chunks before chunk_rms[0] when resuming mid-file
        self.pending = np.zeros(0, dtype=np.float32)
        self.vad = VADIterator(vad_model, sampling_rate=asr.SAMPLE_RATE,
                               min_silence_duration_ms=400, speech_pad_ms=200)
        self.speech = []                  # chunks of the current utterance
        self.speech_start = None          # seconds from track start
        self.last_growth = time.time()
        self.chunk_rms = array("f")       # RMS per VAD_CHUNK, for echo checks against the other track
        # (samples in file, wall time when we saw them). Minutes pauses or pads the stems when
        # the call is silent or the mic switches, so file offsets drift from the clock; these
        # anchors map a sample back to when it was written.
        self.anchor_samples = array("q")
        self.anchor_times = array("d")

    def wall_time(self, seconds):
        sample = seconds * asr.SAMPLE_RATE
        i = bisect.bisect_left(self.anchor_samples, sample)
        if i < len(self.anchor_samples):
            idx, at = self.anchor_samples[i], self.anchor_times[i]
            return datetime.fromtimestamp(at - (idx - sample) / asr.SAMPLE_RATE)
        return datetime.now()

    def rms_between(self, start, end):
        a = max(0, int(start * asr.SAMPLE_RATE) // VAD_CHUNK - self.base_chunk)
        b = max(0, int(end * asr.SAMPLE_RATE) // VAD_CHUNK + 1 - self.base_chunk)
        window = self.chunk_rms[a:b]
        return float(np.sqrt(np.mean(np.square(window)))) if window else 0.0

    def _pausing(self):
        """The speaker has stopped for at least PAUSE_SECS (too short for the VAD to end the speech: 400 ms)."""
        since = getattr(self.vad, "temp_end", 0)          # VADIterator: where the current dip below threshold began
        return bool(since) and (getattr(self.vad, "current_sample", 0) - since) / asr.SAMPLE_RATE >= asr.PAUSE_SECS

    def _emit(self, end_sample):
        if self.speech_start is None:
            return
        audio = np.concatenate(self.speech) if self.speech else np.zeros(0, dtype=np.float32)
        start = self.speech_start
        self.speech, self.speech_start = [], None
        if len(audio) >= asr.SAMPLE_RATE * 0.3:
            self.out_q.put((self.speaker, start, end_sample / asr.SAMPLE_RATE, audio))

    def poll(self):
        """Read whatever was appended since the last call. Returns True if the file grew."""
        if self.finished or not os.path.exists(self.path):
            return False
        st = os.stat(self.path)
        if self.inode is None:
            self.inode = st.st_ino
        elif st.st_ino != self.inode:
            self.finished = True           # Minutes started a new recording: this track is over
            return False
        size = st.st_size
        if size < self.offset:
            return False                   # resumed past this track's end (Minutes pads the stems): wait
        usable = (size - self.offset) // 2 * 2
        if usable <= 0:
            return False
        with open(self.path, "rb") as f:
            f.seek(self.offset)
            raw = f.read(usable)
        self.offset += len(raw)
        self.last_growth = time.time()
        self.anchor_samples.append((self.offset - HEADER_BYTES) // 2)
        self.anchor_times.append(self.last_growth)
        pcm = np.frombuffer(raw, dtype="<i2").astype(np.float32) / 32768.0
        self.pending = np.concatenate([self.pending, pcm])
        while len(self.pending) >= VAD_CHUNK:
            chunk, self.pending = self.pending[:VAD_CHUNK], self.pending[VAD_CHUNK:]
            chunk_start = self.samples_read
            self.samples_read += VAD_CHUNK
            self.chunk_rms.append(asr.rms(chunk))
            event = self.vad(chunk)
            if event and "start" in event:
                self.speech_start = chunk_start / asr.SAMPLE_RATE
                self.speech = []
            if self.speech_start is not None:
                self.speech.append(chunk)
                secs = self.samples_read / asr.SAMPLE_RATE - self.speech_start
                if secs >= asr.MAX_SEGMENT_SECS or (secs >= asr.SOFT_SEGMENT_SECS and self._pausing()):
                    self._emit(self.samples_read)          # cut long speech, at a pause when there is one
                    self.speech_start = self.samples_read / asr.SAMPLE_RATE
            if event and "end" in event:
                self._emit(self.samples_read)
        return True

    def flush(self):
        self._emit(self.samples_read)


def recording_active(minutes_cli):
    import subprocess
    try:
        out = subprocess.run([minutes_cli, "status"], capture_output=True, text=True, timeout=10).stdout
        return json.loads(out).get("recording", False)
    except Exception:
        return True   # unknown: keep going


def main():
    wav, out_path = Path(sys.argv[1]), Path(sys.argv[2])
    base = wav.with_suffix("")

    import config
    missing = config.asr_model_missing(config.load())
    if missing:
        # Exit at once: the autopilot then uses Minutes' own live transcript for this meeting and says
        # the model is missing (the setup guide installs it).
        log(f"speech model not installed: {missing}; not downloading it during a meeting")
        sys.exit(5)
    t0 = time.time()
    tr = asr.Transcriber()
    log(f"models loaded in {time.time() - t0:.1f}s; following {base}.{{voice,system}}.wav")

    pidfile = out_path.with_suffix(".pid")
    try:
        other = int(pidfile.read_text())
        os.kill(other, 0)
        log(f"another transcriber (pid {other}) is already working on {out_path.name}; exiting")
        sys.exit(4)                       # the autopilot keeps that one as the live transcriber
    except (OSError, ValueError):
        pass
    pidfile.write_text(str(os.getpid()))

    work = queue.Queue()
    followers = [TrackFollower(f"{base}.{stem}.wav", speaker, load_silero_vad(onnx=True), work)
                 for stem, speaker in TRACKS]
    # Restarted mid-meeting (the transcript already has rows): continue each track after its last
    # transcribed second instead of transcribing the whole meeting again into the notes. The tracks
    # differ in length (Minutes pads them independently), so each gets its own resume point.
    resume_at = {}
    for line in (out_path.read_text().splitlines() if out_path.exists() else []):
        try:
            row = json.loads(line)
            resume_at[row["speaker"]] = max(resume_at.get(row["speaker"], 0.0), float(row["end"]))
        except (ValueError, KeyError, TypeError):
            pass

    for f in followers:
        at = resume_at.get(f.speaker, 0.0)
        if at:
            f.samples_read = int(at * asr.SAMPLE_RATE) // VAD_CHUNK * VAD_CHUNK
            f.offset = HEADER_BYTES + f.samples_read * 2
            f.base_chunk = f.samples_read // VAD_CHUNK
            log(f"{f.speaker}: resuming after {at:.1f}s of already transcribed audio")
    call = followers[1]                     # system track: reference for echo checks
    self_gate = asr.SelfLevelGate(fixed=tr.cfg["asr"].get("self_min_rms") or None)
    voice, voice_threshold = voiceprint.from_config(tr.cfg)
    if voice:
        log(f"voiceprint verification on (threshold {voice_threshold})")
    # Who among the other participants said each line: A, B, C... grouped by voice as we go.
    snapshot_path = speakers.speakers_path(out_path)
    vectors_path = speakers.vectors_path(out_path)
    merged_path = speakers.merged_path(out_path)
    written_merges = speakers.merged_labels(out_path)
    people = None                           # saved voices; None when [privacy] store_voiceprints is off
    try:
        voice_cfg = tr.cfg.get("voice", {})
        people = (speakers.People(voice_cfg.get("people_file") or "~/.config/meeting-copilot/people.json")
                  if tr.cfg.get("privacy", {}).get("store_voiceprints", True) else None)
        others = speakers.OnlineSpeakers(voice or voiceprint.Voiceprint(),
                                         float(voice_cfg.get("group_threshold", speakers.GROUP_THRESHOLD)),
                                         same_person=speakers.same_saved_person(out_path, people) if people else None)
        if snapshot_path.exists():
            # Resuming this transcript, or a recording restarted mid-call (the autopilot copied the speakers
            # over): keep A/B/C the same voices as before.
            others.restore(json.loads(snapshot_path.read_text()), speakers.merged_labels(out_path))
            log(f"speakers restored: {', '.join(others.snapshot()) or 'none'}")
    except Exception as e:  # noqa: BLE001 - e.g. the speaker model is missing
        log(f"speaker grouping off: {e!r}")
        others = None
    done = threading.Event()

    def transcribe_worker():
        with open(out_path, "a") as out:
            while not (done.is_set() and work.empty()):
                try:
                    speaker, start, end, audio = work.get(timeout=0.5)
                except queue.Empty:
                    continue
                try:
                    if speaker == i18n.SELF:
                        # Wait until the call track has caught up to this span, then drop call echo.
                        deadline = time.time() + 2
                        while call.samples_read / asr.SAMPLE_RATE < end and time.time() < deadline:
                            time.sleep(0.1)
                        level = asr.rms(audio)
                        score = voice.score(audio) if voice else None
                        if score is not None:
                            # Voiceprint first: echo only carries other people's voices, so a match is
                            # the user even while they talk over the call, where the level rule would drop
                            # them (in offline replays of call echo over the user's speech it kept far more of them).
                            self_gate.levels.append(level)
                            if score < voice_threshold:
                                log(f"我 {start:.1f}-{end:.1f}s dropped (voiceprint {score:.2f} < {voice_threshold}: "
                                    "not the user, or call echo)")
                                continue
                        elif asr.is_echo(level, call.rms_between(start, end)):
                            log(f"我 {start:.1f}-{end:.1f}s dropped (call echo)")
                            continue
                        elif not self_gate.accept(level):
                            log(f"我 {start:.1f}-{end:.1f}s dropped (rms {level:.3f} < {self_gate.threshold():.3f}: "
                                "likely someone near the mic)")
                            continue
                    t = time.time()
                    seg = tr.transcribe_segment(audio, start, end)
                    if seg.engine == "dropped" or not seg.text:
                        log(f"{speaker} {start:.1f}-{end:.1f}s dropped ({seg.note})")
                        continue
                    follower = followers[0] if speaker == i18n.SELF else call
                    ts = follower.wall_time(start).isoformat(timespec="seconds")
                    row = {"ts": ts, "start": round(start, 2), "end": round(end, 2), "speaker": speaker,
                           "text": seg.text, "engine": seg.engine}
                    if speaker != i18n.SELF and others is not None:
                        k = others.assign(audio, start=row["start"], end=row["end"])
                        split = others.check_turn() if k is not None else None
                        if split:
                            old, new, starts = split
                            earlier = [x for x in starts if x != row["start"]]
                            if earlier:
                                out.write(json.dumps(speakers.relabel_row(old, new, earlier)) + "\n")
                            log(f"{old} {starts[0]:.1f}-{end:.1f}s: a turn unlike {old}'s voice, now {new}")
                            k = others.current()
                        who = others.label(k)
                        merged = others.merged()
                        if merged != written_merges:
                            speakers.atomic_write(merged_path, json.dumps(merged))
                            log(f"voices found to be one person: {merged}")
                            written_merges.clear()
                            written_merges.update(merged)
                        if who:
                            row["who"] = who
                            speakers.atomic_write(snapshot_path, json.dumps(others.snapshot()))
                            if others.last_vector is not None and people is not None:   # only for saving voices
                                with open(vectors_path, "a") as vf:
                                    vf.write(json.dumps({"start": row["start"], "v": [
                                        round(float(x), 4) for x in others.last_vector]}) + "\n")
                    out.write(json.dumps(row, ensure_ascii=False) + "\n")
                    out.flush()
                    log(f"{speaker} {end - start:4.1f}s audio -> {time.time() - t:.2f}s ({seg.engine})")
                except Exception as e:  # noqa: BLE001 - one bad segment must not end the meeting's transcript
                    log(f"{speaker} {start:.1f}-{end:.1f}s error: {e!r}")

    worker = threading.Thread(target=transcribe_worker, daemon=True)
    worker.start()

    import config
    minutes_cli = config.tool("minutes", APP / "bin" / "minutes")
    last_status_check, active = 0.0, True
    last_memory_log = time.time()
    exit_code = 0
    while True:
        grew = any([f.poll() for f in followers])
        now = time.time()
        if now - last_memory_log > MEMORY_LOG_SECS:
            log(asr.memory_report())          # a Mac can run short of memory during a long call
            last_memory_log = now
        if now - last_status_check > 5:
            active, last_status_check = recording_active(minutes_cli), now
        idle = min(now - f.last_growth for f in followers)
        if not active and not grew and idle > IDLE_EXIT_SECS:
            break
        if all(f.finished for f in followers):
            log("stems replaced by a new recording; finishing")
            break
        if idle > MAX_IDLE_SECS:
            # Nothing written for two minutes: the recording is over whatever `status` says, or the
            # stems stopped while Minutes still records (device change). Exit non-zero in the latter
            # case so the autopilot falls back to Minutes' own transcript instead of waiting.
            log("idle too long; finishing")
            exit_code = 3 if active else 0
            break
        if not worker.is_alive():
            log("transcribe worker died; exiting so the autopilot falls back")
            sys.exit(2)
        time.sleep(POLL_SECS)

    for f in followers:
        f.flush()
    done.set()
    worker.join()
    pidfile.unlink(missing_ok=True)
    log(f"recording finished; live transcription complete ({asr.memory_report()})")
    sys.exit(exit_code)


if __name__ == "__main__":
    main()
