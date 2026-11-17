"""Names for the voices of the call audio, taught by the screen.

Meeting apps mark who is talking: a coloured border round their tile, a ring round their avatar. When a voice
group without a name speaks, the frames shown during that line are read (one Claude call per frame, cached),
and the line counts as a vote for the name on the marked tile. A group is named once enough of its lines agree.
Nothing here depends on one meeting app: only on the app marking the speaker, and on the tiles being on screen.

A name is cheap to correct; a saved voice is not, and a group can hold two people (at the grouping threshold some
lines land in the wrong group). So a named group goes on being read, and only a group the screen agrees on through the
meeting is saved for later meetings: learnable().

On one recorded call with several remote speakers, the screen gave a clear speaker for about half of the call-side
lines, and voiceprints learnt from those lines put most of them under the right name and few under a wrong one.

The user is never a candidate: their own tile is marked while they talk, but their voice is on the microphone,
not in the call audio. Their name and aliases are always theirs; the name on their own tile is learnt from a
few frames shown while only they were talking.
"""
import json
import re
from collections import Counter
from pathlib import Path

import numpy as np

PROMPT = """This is a screenshot of a Mac during a video meeting (Google Meet, Zoom, Teams or similar).
Look only at the meeting window. Meeting apps mark the participant who is talking right now: a coloured
border or outline around their tile, a glowing ring around their avatar, or an animated audio indicator.

Answer with one JSON object and nothing else:
{"layout": "grid" | "speaker_view" | "screen_share" | "no_meeting_visible",
 "speaking": "<the name shown on the highlighted tile>" or null,
 "speaking_is_you": true if that tile is marked as the viewer's own ("You", "(me)", "你"), else false,
 "name_box": [left, top, right, bottom] of the name label on that tile, as fractions (0 to 1) of the image's
             width and height, or null,
 "confidence": "high" | "low"}

Use null when no tile is clearly marked as speaking. Copy names exactly as written on the tiles."""

NAME_PROMPT = """This is an enlarged part of a video meeting window, around the name label of the participant who is
talking. A first look at the whole, smaller screenshot read that label as "{reading}", which may be wrong in a
character or two. Read the label nearest the centre again from these pixels and copy it exactly, character by
character. Answer with one JSON object and nothing else: {{"name": "<the name>"}}, or {{"name": null}} when no
name label is legible."""

SAMPLES = (0.25, 0.5, 0.75)   # points of a line whose frames are read; two of three must show the same person
MIN_LINE_SECS = 1.5           # shorter lines: too little time on screen to tell
OVERLAP_SECS = 0.3            # a call line the user talked over is skipped: the marked tile may be theirs
NAME_AFTER = 2                # lines that must agree before a group is named
NAME_TAKEN_AFTER = 4          # ...when another group already has that name. A new voice's first lines often still
                              # show the last speaker's tile (some apps move the border late), so such a naming is
                              # more often wrong than right; one voice split in two groups is the rarer case
NAME_SHARE = 0.75             # ...and the share of the group's screen-read lines they must be
KEEP_SHARE = 0.5              # a name is taken back below this share: one stray line must not flip it
KEEP_READING = 10             # a group whose lines agree is read until this many of them showed someone
MIXED_READING = 20            # ...one whose lines disagree, up to this many: its name follows them, or is taken back
LEARN_LINES = 5               # lines that must show someone before a group's voice is saved
LEARN_SHARE = 0.9             # ...and the share of them that must show the name it was given
BACKOFF_AFTER = 8             # after this many lines that showed nobody, a group is looked at one line in 4
MAX_FRAMES = 150              # frames read per recording at most (each is one Claude call)
SELF_LINES = 4                # lines of the user alone read to learn the name on their own tile
LAG_LINES = 2                 # a turn's first lines may still show the last speaker marked (some apps move the
                              # border late, so the first lines of a new voice can still show the one before)
OTHER_VOICE_MARGIN = 0.1      # a line this much closer to its own group's voice than to the group holding the name
                              # the screen shows is not counted for that name: a stale frame can still light another
                              # tile while someone else talks, and when that person has no saved voice the saved-voice
                              # veto cannot stop it
OTHER_VOICE_LINES = 2         # lines with a voice each group needs before the comparison is made
RECENT_SECS = 180             # lines older than this behind the newest are passed over (catching up)


def norm(name):
    """A tile name to compare: case and spacing ignored, a trailing "(Presenting)" / "（你）" dropped."""
    name = re.sub(r"\s*[(（][^()（）]*[)）]\s*$", "", str(name or ""))
    return " ".join(name.split()).casefold()


def misread(a, b):
    """Two readings of one tile name that differ in a single Chinese character beside the same Latin letters
    ("Pip 石貓貓", "Pip 石錨貓"): one tile can be read both ways, and a second voice group must not be named with
    the misreading. Names without Latin letters ("番茄一號", "番茄二號") are left apart."""
    a, b = norm(a).replace(" ", ""), norm(b).replace(" ", "")
    if a == b or len(a) != len(b) or len(re.sub(r"[^a-z0-9]", "", a)) < 3:
        return False
    diff = [i for i in range(len(a)) if a[i] != b[i]]
    return len(diff) == 1 and all("一" <= c <= "鿿" for c in (a[diff[0]], b[diff[0]]))


def same_label(a, b):
    """Whether two readings can be one tile label: the same letters and digits (a Latin part read twice), and
    at most two other characters apart, or one cut short where the other goes on. A rough label box can make a
    crop show the neighbouring tile ("南瓜湯..." for "諸葛蘑菇")."""
    a, b = norm(a).replace(" ", ""), norm(b).replace(" ", "")
    cut_a, cut_b = cut_short(a), cut_short(b)
    if cut_a is not None or cut_b is not None:
        return (cut_a or a).startswith(cut_b or b) or (cut_b or b).startswith(cut_a or a)
    if re.sub(r"[^a-z0-9]", "", a) != re.sub(r"[^a-z0-9]", "", b) or len(a) != len(b):
        return False
    return sum(x != y for x, y in zip(a, b)) <= 2


def cut_short(name):
    """The start of a tile name the meeting app cut short to fit ("Alice Wo..."), compared as norm() does; None
    for a whole name."""
    m = re.search(r"\s*(\.\.\.|…)\s*$", str(name or ""))
    start = norm(name[:m.start()]) if m else ""
    return start if len(start) >= 2 else None


class Frames:
    """Captured frames of one recording: <root>/display-N/frame-<epoch ms>.jpg, written when the screen changes,
    so each frame stands until the next one."""

    def __init__(self, root):
        self.root = Path(root)
        self._lists = {}                       # display dir -> (mtime, [(ms, path)])

    def displays(self):
        try:
            return sorted(p for p in self.root.iterdir() if p.is_dir() and p.name.startswith("display-"))
        except OSError:
            return []

    def at(self, display, t):
        """The frame shown on `display` at epoch time t, or None."""
        try:
            mtime = display.stat().st_mtime
        except OSError:
            return None
        cached = self._lists.get(display)
        if not cached or cached[0] != mtime:
            frames = []
            for p in display.glob("frame-*.jpg"):
                try:
                    frames.append((int(p.stem.split("-", 1)[1]), p))
                except ValueError:
                    continue
            cached = self._lists[display] = (mtime, sorted(frames))
        best = None
        for ms, p in cached[1]:
            if ms > t * 1000:
                break
            best = p
        return best


class ScreenSpeakers:
    """Votes from the screen for the call's voice groups, for one recording."""

    def __init__(self, read_frame, self_names, self_label="我", log=lambda _m: None, not_them=None,
                 line_voice=None):
        self.read_frame = read_frame           # frame path -> {"speaking": ..., ...} or None
        self.not_them = not_them               # (group letter, name) -> True when the group's voice is not theirs
        self.line_voice = line_voice           # line start -> that line's voiceprint, or None
        self.self_names = {norm(n) for n in self_names if n and str(n).strip()}
        self.self_label = self_label
        self.log = log
        self.self_tiles = Counter()            # names marked while only the user talked
        self.self_tile = None
        self.self_wrong = 0                    # call lines marked with the learnt tile (_not_self)
        self.not_self = set()                  # tiles learnt as the user's and then shown to be someone else's
        self.self_looks = 0
        self.cache = {}
        self.frames_read = 0
        self.votes = {}                        # group letter -> Counter of names
        self.looks = Counter()
        self.misses = Counter()                # lines read that showed nobody
        self.decided = {}
        self.seen_as = {}                      # line start -> the name the screen showed (lines counted as votes)
        self.whole = []                        # whole names the screen has shown, for names it cut short
        self.readings = Counter()              # norm(whole name) -> times read, to choose between misreadings
        self.spelled = {}                      # norm(whole name) -> as first read
        self.unsure = []                       # (start, name shown, voice's name) of lines the screen showed someone
                                               # else saying than the name of their voice; see take_unsure()
        self.next_row = 0
        self.preferred = None                  # the display last seen showing the meeting

    def users_name(self, name):
        """A name that starts with the user's name or alias ("Sam Reed" for "Sam")."""
        return norm(name).split(" ")[0] in self.self_names

    def is_self(self, name, marked_as_you=False):
        n = norm(name)
        if marked_as_you or n in self.self_names or n == self.self_tile:
            return True
        # Until the name on the user's tile is known, one that starts with their name may be it ("Sam Reed").
        return self.self_tile is None and self.users_name(n)

    def _read(self, path):
        if path not in self.cache:
            if self.frames_read >= MAX_FRAMES:
                return None
            self.frames_read += 1
            self.cache[path] = self.read_frame(path) or {}
        return self.cache[path]

    def marked(self, frames, a, b):
        """(name, marked as the user's own tile) shown as speaking for most of [a, b], or (None, False)."""
        displays = frames.displays()
        if self.preferred in displays:
            displays.remove(self.preferred)
            displays.insert(0, self.preferred)
        for display in displays:
            seen, meeting = [], False
            for share in SAMPLES:
                path = frames.at(display, a + (b - a) * share)
                data = self._read(path) if path else None
                if data is None:
                    continue
                if data.get("layout") not in (None, "no_meeting_visible"):
                    meeting = True
                if data.get("speaking") and data.get("confidence") == "high":
                    seen.append((data["speaking"], bool(data.get("speaking_is_you"))))
            if meeting:
                self.preferred = display
                top = Counter(norm(n) for n, _ in seen).most_common(1)
                if top and top[0][1] >= 2:
                    name, you = next((n, y) for n, y in seen if norm(n) == top[0][0])
                    return name, you
                return None, False
        return None, False

    def take_unsure(self):
        """Lines found since the last call whose voice has a name but the screen showed someone else talking:
        [(start, name on screen, the voice's name)]. Such a line is shown without its voice's name."""
        out, self.unsure = self.unsure, []
        return out

    def _turns(self, rows):
        """{row index: (lines of the same voice before it in this turn, the voice talking before the turn)}."""
        out, prev, run, last = {}, None, 0, None
        for i, r in enumerate(rows):
            who = r.get("who")
            if r.get("speaker") == self.self_label or not who:
                continue
            if who == last:
                run += 1
            else:
                prev, run, last = last, 0, who
            out[i] = (run, prev)
        return out

    def observe(self, rows, t0, frames, named, shown=None):
        """Read the screen for the new lines of `rows` (a live transcript; t0 = its recording's start, epoch s).
        Returns {group letter: name} for groups that have just earned a name or changed it, and "" for a group whose
        name is taken back: its lines no longer agree on one person. `named` is {group letter: name} given in this
        meeting; a group the user named, or renamed after the screen did, is skipped. `shown` is {group letter: name}
        the lines are shown under now (also names from saved voices); lines of those groups the screen shows someone
        else saying are collected for take_unsure()."""
        named = dict(named)
        shown = dict(shown or {})
        first = self.next_row
        new_rows, self.next_row = rows[self.next_row:], len(rows)
        turns = self._turns(rows)
        if not new_rows:
            return {}
        newest = max(r.get("end", 0) for r in new_rows)
        mine = [(r["start"], r["end"]) for r in rows if r.get("speaker") == self.self_label]
        calls = [(r["start"], r["end"]) for r in rows if r.get("speaker") != self.self_label]
        found = {}
        for i, r in enumerate(new_rows, first):
            start, end = r.get("start", 0), r.get("end", 0)
            if end - start < MIN_LINE_SECS or newest - end > RECENT_SECS:
                continue
            if r.get("speaker") == self.self_label:
                self._learn_self(frames, t0, start, end, calls)
                continue
            who = r.get("who")
            if not who or (who in named and norm(named[who]) != norm(self.decided.get(who, ""))):
                continue
            votes = self.votes.get(who, Counter())
            total = sum(votes.values())
            agreed = not total or votes.most_common(1)[0][1] >= NAME_SHARE * total
            if total >= (KEEP_READING if agreed else MIXED_READING):
                continue
            if any(min(end, b) - max(start, a) > OVERLAP_SECS for a, b in mine):
                continue
            self.looks[who] += 1
            if self.misses[who] > BACKOFF_AFTER and self.looks[who] % 4:
                continue
            name, you = self.marked(frames, t0 + start, t0 + end)
            if not name:
                self.misses[who] += 1
                continue
            if self.is_self(name, you) and not self._not_self(name, you):
                if you:
                    self._saw_self(name)
                continue
            name = self._whole_name(name)
            run, before = turns.get(i, (0, None))
            if run < LAG_LINES and before and norm(name) == norm(shown.get(before) or self.decided.get(before) or ""):
                self.log(f"screen: {who} {start:.0f}-{end:.0f}s marked {name}, who talked before: not counted")
                continue
            if self.not_them and self.not_them(who, name):
                # A new voice's first lines can still show the last speaker, whose saved voice it is far from.
                self.log(f"screen: {who} {start:.0f}-{end:.0f}s marked {name}, but the voice is not theirs")
                continue
            other = self._other_voice(rows, who, start, name, shown)
            if other:
                self.log(f"screen: {who} {start:.0f}-{end:.0f}s marked {name}, but it sounds like {who}, not {other}")
                continue
            self.seen_as[round(float(start), 2)] = name
            votes = self.votes.setdefault(who, Counter())
            votes[name] += 1
            top, n = votes.most_common(1)[0]
            if shown.get(who) and norm(shown[who]) != norm(name) and norm(top) != norm(name):
                # Someone else on screen while most of this voice's lines show its name: this line may be another
                # person grouped with the voice. (When the screen's name leads instead, the voice was misnamed.)
                self.unsure.append((start, name, shown[who]))
            self.log(f"screen: {who} {start:.0f}-{end:.0f}s marked {name} ({dict(votes)})")
            had = self.decided.get(who)
            taken = any(norm(v) == norm(top) for g, v in named.items() if g != who and v)
            if n >= (NAME_TAKEN_AFTER if taken else NAME_AFTER) and n >= NAME_SHARE * sum(votes.values()):
                if had != top:                 # named, or renamed: the screen now agrees on someone else
                    self.decided[who] = named[who] = found[who] = top
            elif had and votes[had] < KEEP_SHARE * sum(votes.values()):   # most lines now show someone else
                del self.decided[who]
                named[who] = found[who] = ""
                self.log(f"screen: {who} is no longer {had} ({dict(votes)})")
        return found

    def _voice(self, starts):
        """Normalised mean voiceprint of the lines starting at `starts`, or None with too few of them."""
        found = [v for v in (self.line_voice(s) for s in starts) if v is not None and np.linalg.norm(v) > 0]
        if len(found) < OTHER_VOICE_LINES:
            return None
        mean = np.mean([v / np.linalg.norm(v) for v in found], axis=0)
        return mean / (np.linalg.norm(mean) or 1.0)

    def _other_voice(self, rows, who, start, name, shown):
        """Another group of this meeting that holds `name` when the line at `start` sounds clearly more like its own
        group `who` than like that group, else None: the frame showed the wrong tile (late or stale), so the vote
        must not move the line's voice to that person."""
        if not self.line_voice:
            return None
        holders = sorted(g for g, n in {**shown, **self.decided}.items() if g != who and n and norm(n) == norm(name))
        if not holders:
            return None
        line = self.line_voice(start)
        if line is None or not np.linalg.norm(line):
            return None
        line = line / np.linalg.norm(line)
        own = self._voice([r.get("start") for r in rows if r.get("who") == who and r.get("start") != start])
        if own is None:
            return None
        for g in holders:
            theirs = self._voice([r.get("start") for r in rows if r.get("who") == g])
            if theirs is not None and float(line @ own) - float(line @ theirs) >= OTHER_VOICE_MARGIN:
                return g
        return None

    def _whole_name(self, name):
        """A name cut short on a small tile as the whole name it starts with, and a whole name taking over the votes
        and lines counted under its cut-short form: "Alice Wo..." and "Alice Wong" would otherwise count as two
        people and split one voice's votes. Readings one character apart (misread()) count as the one read most often
        so far (the first on a tie), which takes over the others' votes when it pulls ahead: on downscaled screenshots
        a label can be misread about as often as it is read right."""
        start = cut_short(name)
        if start is not None:
            return next((n for n in self.whole if norm(n).startswith(start)), name)
        key = norm(name)
        self.spelled.setdefault(key, name)
        self.readings[key] += 1
        same = [k for k in self.readings if k == key or misread(k, key)]
        best = max(same, key=lambda k: self.readings[k])
        name = self.spelled[best]
        self.whole = [name] + [n for n in self.whole if norm(n) not in same]

        def taken_over(n):
            start = cut_short(n)
            return (norm(n) in same and norm(n) != best) or (start is not None and best.startswith(start))

        for votes in self.votes.values():          # a group named by another form is renamed by the next vote
            for n in [n for n in votes if taken_over(n)]:
                votes[name] += votes.pop(n)
        for at, n in self.seen_as.items():
            if taken_over(n):
                self.seen_as[at] = name
        return name

    def confirmed(self, rows, who, name):
        """Start times of `who`'s lines (as `rows` group them now) the screen showed `name` saying."""
        return [round(float(r["start"]), 2) for r in rows if r.get("who") == who
                and norm(self.seen_as.get(round(float(r.get("start", -1)), 2), "")) == norm(name)]

    def learnable(self):
        """{group letter: name} of the groups whose voice is worth saving: at the end of the recording, when all the
        lines read so far count. A group named early can turn out to hold two people."""
        out = {}
        for who, name in self.decided.items():
            total = sum(self.votes[who].values())
            if total >= LEARN_LINES and self.votes[who][name] >= LEARN_SHARE * total:
                out[who] = name
        return out

    def _learn_self(self, frames, t0, start, end, calls):
        """A line of the user alone: the tile marked then is theirs."""
        if self.self_tile or self.self_looks >= SELF_LINES or any(min(end, b) - max(start, a) > 0 for a, b in calls):
            return
        self.self_looks += 1
        name, _ = self.marked(frames, t0 + start, t0 + end)
        # A name the call's lines were marked with is someone else's tile: when the user talks while muted, their
        # tile stays dark and the tile of whoever spoke last can stay marked.
        if name and not any(norm(name) in map(norm, v) for v in self.votes.values()):
            self._saw_self(name)

    def _not_self(self, name, you):
        """A call line (the user not talking over it) marked with the tile learnt as the user's: the user's voice is
        not in the call audio, so after a second one the tile was someone else's. That line counts as a vote."""
        n = norm(name)
        if you or n != self.self_tile or n in self.self_names or self.users_name(n):
            # The call's sound reaching the user's microphone lights their tile too, so a tile under the user's
            # own name (e.g. "Sam Park") can light up during call lines. It is theirs whatever lights it.
            return False
        self.self_wrong += 1
        if self.self_wrong < 2:
            return False
        self.log(f"screen: {name} is not the user's own tile after all")
        self.self_tile, self.self_wrong = None, 0
        self.not_self.add(n)
        return True

    def _saw_self(self, name):
        if norm(name) in self.not_self:
            return
        self.self_tiles[norm(name)] += 1
        if self.self_tile is None and self.self_tiles[norm(name)] >= 2:
            self.self_tile = norm(name)
            self.log(f"screen: the user's own tile is {name}")


def parse_reply(text):
    """The labeller's JSON object out of a reply, or None."""
    try:
        start, end = text.index("{"), text.rindex("}") + 1
        data = json.loads(text[start:end])
    except (AttributeError, ValueError):
        return None
    return data if isinstance(data, dict) else None
