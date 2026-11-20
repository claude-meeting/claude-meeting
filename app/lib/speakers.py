"""Live speaker grouping for the call audio: which other participant said each line.

The call stem mixes every other participant. Each utterance gets a CAM++ voiceprint (the model the
user's own voiceprint uses) and joins the closest speaker so far, or starts a new one, as the
meeting runs. Names come later: from voiceprints saved for people in earlier meetings, or from the
screen (the meeting app shows who is speaking).
"""
import json
import os
import tempfile
import threading
from pathlib import Path

import numpy as np


def atomic_write(path, text):
    """Write a file other processes read: they see the old or the new content, never half of it."""
    path = Path(path)
    fd, tmp = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)   # unique: two writers never collide
    try:
        with os.fdopen(fd, "w") as f:
            f.write(text)
        try:
            os.chmod(tmp, os.stat(path).st_mode & 0o777)   # keep the file's permissions
        except OSError:
            os.chmod(tmp, 0o644)
        os.replace(tmp, path)
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise

MIN_SECS = 1.0          # shorter utterances are too short for a reliable voiceprint: no label


GROUP_THRESHOLD = 0.5    # cosine to join a speaker. 0.35 (tuned on AliMeeting) merged different people into a few
                         # mixed groups on recorded calls; 0.5 gives more, smaller and much purer groups. A person
                         # split in two is named twice by the screen; mixed is wrong.


# A turn (one voice's lines with no gap over TURN_GAP_SECS) is checked as a whole against its group's voice from before
# it: one line is too short to tell people apart, a turn is not. Replayed on recorded calls (turns of 3 s or more
# against the group's earlier turns), the turns of the group's own person mostly scored 0.7 or more, and wrongly
# grouped turns scored less. A turn under TURN_THRESHOLD becomes a voice of its own: at worst a person's turn goes
# unnamed, never under someone else's name.
TURN_THRESHOLD = 0.7
TURN_MIN_SECS = 3.0
TURN_GAP_SECS = 2.0
TURN_MAX_SECS = 30.0     # audio kept for a turn's voiceprint


class OnlineSpeakers:
    def __init__(self, voiceprint, threshold=GROUP_THRESHOLD, merge_threshold=0.85, same_person=None):
        self.vp = voiceprint
        self.same_person = same_person          # (letter, voiceprint) -> True when it is the saved voice of the
                                                # person that letter is named after: such a turn is never split off
        self.threshold = threshold              # cosine needed to join an existing speaker
        self.merge_threshold = merge_threshold  # two speakers this similar are one person
        self.centroids = []                     # normalised running means
        self.counts = []
        self.alias = {}                         # merged speaker -> surviving speaker
        self.carried = {}                       # letter merged away in an earlier part -> letter it went to
        self.labels = []                        # letter given when a speaker first appears; never reused
        self.next_label = 0                     # index of the next letter (A=0), also after a restore
        self.turn = None                        # the voice talking now: see check_turn()
        self.last_vector = None                 # the last utterance's voiceprint (for saving voices later)

    def _resolve(self, k):
        while k in self.alias:
            k = self.alias[k]
        return k

    def assign(self, audio, sample_rate=16000, start=None, end=None, vector=None):
        """Speaker index for one utterance, or None when it is too short to tell. `start`/`end` (seconds into the
        track) let check_turn() follow the turn this utterance belongs to. `vector`: its embedding, when the caller
        already has it (room mode scored it against the user's voiceprint)."""
        self.last_vector = None
        if len(audio) < MIN_SECS * sample_rate:
            return None
        v = self.vp.embed(audio) if vector is None else vector
        self.last_vector = v
        live = [k for k in range(len(self.centroids)) if k not in self.alias]
        before = None
        k = None
        if live:
            scores = [float(np.dot(v, self.centroids[k])) for k in live]
            best = int(np.argmax(scores))
            if scores[best] >= self.threshold:
                k = live[best]
                before = (self.centroids[k].copy(), self.counts[k])
                self._update(k, v)
                merged = self._merge(k)
                k = self._resolve(k)
                if merged:
                    before = None                # the group changed under the turn: nothing to compare with
        if k is None:
            k = self._new(v, 1)
        self._follow(k, before, audio, sample_rate, v, start, end)
        return k

    def _new(self, v, count):
        self.centroids.append(v / (np.linalg.norm(v) + 1e-9))
        self.counts.append(count)
        n = self.next_label
        self.next_label += 1
        self.labels.append(chr(ord("A") + n) if n < 26 else f"S{n + 1}")
        return len(self.centroids) - 1

    def _follow(self, k, before, audio, sample_rate, v, start, end):
        t = self.turn
        if start is None or end is None:
            self.turn = None
            return
        if t and t["k"] == k and start - t["end"] <= TURN_GAP_SECS:
            t["end"] = end
            t["starts"].append(start)
            t["vectors"].append(v)
            if t["secs"] < TURN_MAX_SECS:
                t["audio"].append(audio)
                t["secs"] += len(audio) / sample_rate
            return
        self.turn = {"k": k, "before": before, "end": end, "starts": [start], "vectors": [v], "audio": [audio],
                     "secs": len(audio) / sample_rate, "rate": sample_rate, "checked": False}

    def check_turn(self):
        """After assign(): when the turn so far (TURN_MIN_SECS of speech or more) sounds clearly unlike the voice its
        group had before it, the turn becomes a voice of its own. Returns (old letter, new letter, start times of the
        turn's lines) then, else None. The caller labels the current line with the new letter (label(k) of the
        last assign now gives it) and relabels the earlier ones."""
        t = self.turn
        if not t or t["checked"] or t["before"] is None or t["secs"] < TURN_MIN_SECS:
            return None
        t["checked"] = True                       # one look per turn: the first with enough speech
        k = t["k"]
        if k in self.alias:
            return None
        v = self.vp.embed(np.concatenate(t["audio"]))
        old_centroid, old_count = t["before"]
        old = self.labels[k]
        if float(np.dot(v, old_centroid)) >= TURN_THRESHOLD:
            return None
        if self.same_person and self.same_person(old, v):
            # On recorded calls, some split turns of named voices still clearly matched their own person's saved voice:
            # a voice can sound unlike its own earlier lines and still be that person.
            return None
        self.centroids[k], self.counts[k] = old_centroid, old_count
        mean = np.mean(t["vectors"], axis=0)
        new = self._new(mean, len(t["vectors"]))
        t["k"], t["before"] = new, None
        return old, self.labels[new], list(t["starts"])

    def current(self):
        """Index of the voice of the current turn (after a split, the new one)."""
        return self.turn["k"] if self.turn else None

    def restore(self, snapshot, merged=None):
        """Continue from an earlier snapshot() of the same meeting: the same voices keep their letters, and the
        letters merged away (merged() then) still go to theirs."""
        self.carried.update(merged or {})
        for label, entry in (snapshot or {}).items():
            vec = np.asarray(entry.get("vector") or [], dtype=np.float32)
            if not vec.size:
                continue
            self.centroids.append(vec / (np.linalg.norm(vec) + 1e-9))
            self.counts.append(max(1, int(entry.get("count") or 1)))
            self.labels.append(label)
            index = ord(label) - ord("A") if len(label) == 1 else int(label[1:]) - 1 if label[1:].isdigit() else 0
            self.next_label = max(self.next_label, index + 1)

    def _update(self, k, v):
        n = self.counts[k]
        m = self.centroids[k] * n + v
        self.centroids[k] = m / (np.linalg.norm(m) + 1e-9)
        self.counts[k] = n + 1

    def _merge(self, k):
        merged = False
        for j in range(len(self.centroids)):
            if j == k or j in self.alias or k in self.alias:
                continue
            if float(np.dot(self.centroids[j], self.centroids[k])) >= self.merge_threshold:
                keep, drop = (k, j) if self.counts[k] >= self.counts[j] else (j, k)
                n = self.counts[keep] + self.counts[drop]
                m = self.centroids[keep] * self.counts[keep] + self.centroids[drop] * self.counts[drop]
                self.centroids[keep] = m / (np.linalg.norm(m) + 1e-9)
                self.counts[keep] = n
                self.alias[drop] = keep
                merged = True
        return merged

    def label(self, k):
        """'A', 'B', ... for a speaker index: stable for the whole meeting (a merged speaker takes the
        surviving one's letter)."""
        return None if k is None else self.labels[self._resolve(k)]

    def merged(self):
        """{letter: letter it was merged into} for the voices found to be one person. Their earlier lines keep the
        old letter in the transcript (it is only appended to): resolve_names() names them after the voice they
        went to."""
        index = {label: k for k, label in enumerate(self.labels)}
        out = {old: self.label(index[new]) if new in index else new for old, new in self.carried.items()}
        out.update({self.labels[k]: self.label(k) for k in self.alias})
        return out

    def snapshot(self):
        """{label: {"vector": [...], "count": n}} for the speakers so far (for naming and learning)."""
        out = {}
        for k in range(len(self.centroids)):
            if k not in self.alias:
                out[self.label(k)] = {"vector": [round(float(x), 5) for x in self.centroids[k]], "count": self.counts[k]}
        return out


# A speaker's running voiceprint vs a saved person. On recorded calls, a person scored above this against their own saved
# voice, while other voices reached about 0.7 against a voice that was not theirs: at 0.5 that would be a match.
PERSON_THRESHOLD = 0.78
PERSON_MARGIN = 0.2      # ...and it must be clearly closer to that person than to anyone else saved
# A voice this far from a saved person is not them, whatever the screen shows: in testing, people scored above 0.8
# against their own saved voice, and names the screen got wrong scored under 0.4.
NOT_THEM_BELOW = 0.5
LEARN_MIN_LINES = 5      # only learn a person's voiceprint from a speaker heard this many times


class People:
    """Voiceprints of people named in earlier meetings: ~/.config/meeting-copilot/people.json.

    The file is shared with copilot-name and the Mac app, so it is re-read whenever it changed on
    disk and written atomically; the autopilot's threads share one instance under a lock."""

    def __init__(self, path):
        self.path = Path(path).expanduser()
        self.data = {}
        self.mtime = None
        self.lock = threading.Lock()
        self._reload()

    def _reload(self):
        try:
            mtime = self.path.stat().st_mtime
        except OSError:
            self.data, self.mtime = {}, None
            return
        if mtime == self.mtime:
            return
        try:
            data = json.loads(self.path.read_text())
            self.data = data if isinstance(data, dict) else {}
        except (OSError, ValueError):
            return                    # being rewritten: keep what we have
        self.mtime = mtime

    def match(self, vector):
        """(name, similarity) of the closest saved person, or (None, best similarity). A voice about as close to
        two people is nobody: when one speaker label mixes two people, naming it after the closer one puts every
        line under the wrong name, while a real match is usually far ahead of the runner-up."""
        v = np.asarray(vector, dtype=np.float32)
        with self.lock:
            self._reload()
            entries = list(self.data.items())
        best, second, name = 0.0, 0.0, None
        for person, entry in entries:
            stored = np.asarray(entry.get("vector", ()), dtype=np.float32)
            if stored.shape != v.shape:
                continue
            s = float(np.dot(v, stored))
            if s > best:
                best, second, name = s, best, person
            elif s > second:
                second = s
        return (name, best) if best >= PERSON_THRESHOLD and best - second >= PERSON_MARGIN else (None, best)

    def contradicts(self, name, vector):
        """The saved person this voice clearly belongs to, when that is not `name`; else None. The screen still
        decides the name, but such a voice must not be learnt as `name`."""
        person, _ = self.match(vector)
        return person if person and person != name else None

    def not_them(self, name, vector):
        """True when `name` is a saved person and this voice is clearly not theirs: far from their voiceprint, or
        clearly someone else's. False when nobody of that name is saved: the voice cannot tell then."""
        v = np.asarray(vector, dtype=np.float32)
        with self.lock:
            self._reload()
            entry = self.data.get(name)
        stored = np.asarray((entry or {}).get("vector", ()), dtype=np.float32)
        if stored.shape != v.shape:
            return False
        n = float(np.linalg.norm(v)) or 1.0
        return float(np.dot(v, stored)) / n < NOT_THEM_BELOW or self.contradicts(name, vector) is not None

    def learn(self, name, vector, count):
        """Add or refine a person's voiceprint (weighted by how much speech it came from)."""
        if count < LEARN_MIN_LINES:
            return False
        v = np.asarray(vector, dtype=np.float32)
        with self.lock:
            self._reload()
            old = self.data.get(name)
            if old and len(old.get("vector", ())) == len(v):
                n = old.get("count", 1)
                v = np.asarray(old["vector"], dtype=np.float32) * n + v * count
                count += n
            v = v / (np.linalg.norm(v) + 1e-9)
            self.data[name] = {"vector": [round(float(x), 5) for x in v], "count": count}
            self.path.parent.mkdir(parents=True, exist_ok=True)
            atomic_write(self.path, json.dumps(self.data, ensure_ascii=False))
            self.mtime = self.path.stat().st_mtime
        return True


def same_saved_person(transcript, people):
    """For OnlineSpeakers(same_person=...): the letter's name in this meeting (given, or matched by voice) is a saved
    person, and this voiceprint is clearly theirs."""
    def check(letter, vector):
        name = resolve_names(transcript, people).get(letter)
        return bool(name) and people.match(vector)[0] == name
    return check


def relabel_row(old, new, starts):
    """The transcript line that moves the lines at `starts` from voice `old` to voice `new` (OnlineSpeakers.check_turn):
    the transcript is only ever appended to, as other processes read it while it grows."""
    return {"relabel": {"from": old, "to": new, "starts": [round(x, 2) for x in starts]}}


def add_row(rows, row):
    """Append one transcript line to `rows`, or apply it when it moves earlier lines to another voice. Returns the
    relabel ({"from", "to", "starts", "ts"} with the first moved line's ts) when it was one, else None."""
    move = row.get("relabel") if isinstance(row, dict) else None
    if not move:
        rows.append(row)
        return None
    starts = {round(float(x), 2) for x in move.get("starts", ())}
    moved = [r for r in rows if r.get("who") == move.get("from") and round(float(r.get("start", -1)), 2) in starts]
    for r in moved:
        r["who"] = move.get("to")
    return {**move, "ts": moved[0].get("ts") if moved else None}


def unsure_path(transcript):
    """Start times of lines the screen showed someone else saying than their voice's name ({"starts": [...]})."""
    return Path(str(transcript).replace(".jsonl", ".unsure.json"))


def unsure_starts(transcript):
    try:
        return {round(float(x), 2) for x in json.loads(unsure_path(transcript).read_text()).get("starts", ())}
    except (OSError, ValueError, AttributeError, TypeError):
        return set()


def mark_unsure(rows, starts):
    """Lines shown without their voice's name: the screen showed someone else saying them."""
    starts = {round(float(x), 2) for x in starts}
    for r in rows:
        if round(float(r.get("start", -1)), 2) in starts:
            r["unsure"] = True


def shown_who(row):
    """The voice letter a line is shown under, or None: a line the screen showed someone else saying is shown
    without its voice's name (it may be another person grouped with that voice)."""
    return None if row.get("unsure") else row.get("who")


def read_transcript(path):
    """All rows of a finished live transcript, with lines moved between voices and lines shown without a name applied."""
    rows = []
    for line in Path(path).read_text(encoding="utf-8", errors="replace").splitlines():
        try:
            add_row(rows, json.loads(line))
        except (ValueError, TypeError, AttributeError):
            continue
    mark_unsure(rows, unsure_starts(path))
    return rows


def vectors_path(transcript):
    """Each call line's voiceprint ({"start", "v"} per line), for saving a voice from the lines the screen confirmed."""
    return Path(str(transcript).replace(".jsonl", ".vectors.jsonl"))


def line_vectors(transcript):
    out = {}
    try:
        text = vectors_path(transcript).read_text()
    except OSError:
        return out
    for line in text.splitlines():
        try:
            row = json.loads(line)
            out[round(float(row["start"]), 2)] = np.asarray(row["v"], dtype=np.float32)
        except (ValueError, KeyError, TypeError):
            continue
    return out


def names_path(transcript):
    """Names given to this meeting's speakers ({"A": "毛毛蟲"}), next to the live transcript."""
    return Path(str(transcript).replace(".jsonl", ".names.json"))


def mode_path(transcript):
    """The recording's mode next to its transcript: "call" or "room" on the first line, "user" on the second when
    the user chose it (bin/autopilot.py RecordingMode, read by bin/live_transcriber.py)."""
    return Path(transcript).with_suffix(".mode")


def open_mode(transcript):
    """The mode file, opened for reading as text (undecodable bytes replaced, so they read as an unknown mode)."""
    return open(mode_path(transcript), encoding="utf-8", errors="replace")


def speakers_path(transcript):
    return Path(str(transcript).replace(".jsonl", ".speakers.json"))


def merged_path(transcript):
    """Voices found to be one person ({"A": "E"}: A's lines are E's), next to the live transcript."""
    return Path(str(transcript).replace(".jsonl", ".merged.json"))


def merged_labels(transcript):
    try:
        data = json.loads(merged_path(transcript).read_text())
        return {str(k): str(v) for k, v in data.items()} if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def given_names(transcript):
    """{label: name} given during this meeting, by the screen or by the user."""
    try:
        return json.loads(names_path(transcript).read_text())
    except (OSError, ValueError):
        return {}


def withdrawn_path(transcript):
    """Names the screen gave this meeting's speakers and took back ({"A": ["毛毛蟲"]}), next to the live transcript."""
    return Path(str(transcript).replace(".jsonl", ".withdrawn.json"))


def withdrawn_names(transcript):
    try:
        return json.loads(withdrawn_path(transcript).read_text())
    except (OSError, ValueError):
        return {}


def resolve_names(transcript, people):
    """{label: name}: names given in the meeting first, then saved people matched by voice, except a name the screen
    took back from that label. Two labels may get the same name: a stricter grouping often splits one person in
    two."""
    given = given_names(transcript)
    withdrawn = withdrawn_names(transcript)
    try:
        snap = json.loads(speakers_path(transcript).read_text())
    except (OSError, ValueError):
        snap = {}
    names = {}
    for label, entry in snap.items():
        if given.get(label):
            names[label] = given[label]
        elif people is not None:
            person, _ = people.match(entry["vector"])
            # A name the screen took back rules out only that name: the voice's own clear match still counts.
            if person and person not in withdrawn.get(label, ()):
                names[label] = person
    # A voice merged into another is the same person: its earlier lines get that voice's name, and a name the screen
    # gave the old letter goes to the new one when it has none (otherwise a speaker's first lines, say B, merged later
    # into D, would stay unnamed in the meeting record while D is named).
    for old, new in merged_labels(transcript).items():
        if new not in names and given.get(old) and given[old] not in withdrawn.get(new, ()):
            names[new] = given[old]
        if new in names:
            names[old] = names[new]
    return names
