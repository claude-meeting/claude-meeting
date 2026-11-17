"""Unit tests for lib/screen_speakers.py: naming the call's voice groups from who the meeting app marks as
speaking. Frames are empty files; a fake reader stands in for the Claude call that reads them."""
import sys
import tempfile
import unittest
from pathlib import Path

APP = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(APP / "lib"))
import screen_speakers as ss  # noqa: E402

T0 = 1_000_000                     # the recording's start, epoch seconds
GRID = {"layout": "grid", "confidence": "high"}


def line(start, end, who="A", speaker="其他人"):
    row = {"start": start, "end": end, "speaker": speaker, "text": "..."}
    if who and speaker != "我":
        row["who"] = who
    return row


class Base(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.labels = {}           # frame name -> what the reader says
        self.read = []
        self.logged = []
        self.teacher = ss.ScreenSpeakers(self.reader, ("Sam", "sam", "山姆"), log=self.logged.append)
        self.rows = []

    def tearDown(self):
        self.tmp.cleanup()

    def reader(self, path):
        key = f"{path.parent.name}/{path.name}"
        self.read.append(key)
        return self.labels.get(key)

    def frame(self, secs, label, display=1):
        d = self.root / f"display-{display}"
        d.mkdir(exist_ok=True)
        name = f"frame-{(T0 + secs) * 1000}.jpg"
        (d / name).write_bytes(b"")
        self.labels[f"{d.name}/{name}"] = label
        return name

    def observe(self, *rows, named=(), shown=None):
        self.rows += rows
        return self.teacher.observe(self.rows, T0, ss.Frames(self.root), dict(named), shown=shown)


class Naming(Base):
    def test_two_agreeing_lines_name_a_group_and_frames_are_read_once(self):
        self.frame(0, {**GRID, "speaking": "Alice Wong"})
        self.assertEqual(self.observe(line(1, 4)), {})                  # one vote is not enough
        self.assertEqual(self.observe(line(5, 8)), {"A": "Alice Wong"})
        self.assertEqual(self.read, [self.read[0]])                     # the same frame: one Claude call
        self.assertEqual(self.observe(line(9, 12)), {})                 # named once

    def test_most_of_the_line_must_show_the_same_person(self):
        self.frame(0, {**GRID, "speaking": "Alice Wong"})
        self.frame(2, {**GRID, "speaking": "Bella"})
        self.observe(line(1, 4), line(4, 6, who="B"))                   # samples: Alice, Bella, Bella
        self.assertEqual(dict(self.teacher.votes["A"]), {"Bella": 1})
        self.frame(10, {**GRID, "speaking": "Dan", "confidence": "low"})
        self.observe(line(10, 13, who="C"))
        self.assertNotIn("C", self.teacher.votes)                       # a low-confidence frame is no vote

    def test_a_group_needs_a_clear_majority(self):
        for i, name in enumerate(("Alice", "Bella", "Alice", "Alice")):
            self.frame(10 * i, {**GRID, "speaking": name})
        self.assertEqual(self.observe(line(1, 4), line(11, 14)), {})
        self.assertEqual(self.observe(line(21, 24)), {})                # 2 of 3: not enough
        self.assertEqual(self.observe(line(31, 34)), {"A": "Alice"})     # 3 of 4

    def test_skipped_lines(self):
        self.frame(0, {**GRID, "speaking": "Alice"})
        self.observe(line(1, 2),                                        # too short
                     line(3, 7, who=None),                              # no voice group
                     line(8, 12, who="B"),                              # named already (by voice)
                     line(13, 16), line(14, 16, speaker="我"), named={"B": "Bella"})
        self.assertEqual(self.read, [])                                 # the user talked over the last one

    def test_old_lines_are_passed_over_when_catching_up(self):
        self.frame(0, {**GRID, "speaking": "Alice"})
        self.observe(line(1, 4), line(1000, 1004))
        self.assertEqual(sum(self.teacher.looks.values()), 1)

    def test_backoff_and_budget(self):
        self.frame(0, {**GRID, "speaking": None})
        self.observe(*(line(i * 5, i * 5 + 3) for i in range(ss.BACKOFF_AFTER + 8)))
        self.assertEqual(self.teacher.looks["A"], ss.BACKOFF_AFTER + 8)
        self.assertEqual(self.teacher.misses["A"], ss.BACKOFF_AFTER + 3)     # past the limit, one line in four
        self.assertEqual(len(self.teacher.cache), 1)
        budget, ss.MAX_FRAMES = ss.MAX_FRAMES, 1
        try:
            teacher = ss.ScreenSpeakers(self.reader, ())
            for i in range(5):
                self.frame(100 + 10 * i, {**GRID, "speaking": "Alice"})
            teacher.observe([line(100 + 10 * i + 1, 100 + 10 * i + 4) for i in range(5)], T0,
                            ss.Frames(self.root), {})
            self.assertEqual(teacher.frames_read, 1)
            self.assertEqual(teacher.decided, {})                       # one vote, then no more frames
        finally:
            ss.MAX_FRAMES = budget

    def test_the_display_showing_the_meeting_is_read_first(self):
        self.frame(0, {"layout": "no_meeting_visible", "speaking": None}, display=1)
        self.frame(0, {**GRID, "speaking": "Alice"}, display=2)
        self.observe(line(1, 4))
        self.assertEqual(self.teacher.preferred.name, "display-2")
        self.read.clear()
        self.frame(10, {"layout": "no_meeting_visible"}, display=1)
        self.frame(10, {**GRID, "speaking": "Alice"}, display=2)
        self.assertEqual(self.observe(line(11, 14)), {"A": "Alice"})
        self.assertEqual(len(self.read), 1)                             # display 1 not read again


class Learning(Base):
    def lines(self, n, who="A", first=0):
        return [line(first + i * 5 + 1, first + i * 5 + 4, who=who) for i in range(n)]

    def test_a_group_the_screen_agrees_on_is_learnable(self):
        self.frame(0, {**GRID, "speaking": "Alice"})
        self.observe(*self.lines(4))
        self.assertEqual(self.teacher.decided, {"A": "Alice"})           # named after two lines...
        self.assertEqual(self.teacher.learnable(), {})                  # ...but four are not enough to save the voice
        self.observe(*self.lines(1, first=20))
        self.assertEqual(self.teacher.learnable(), {"A": "Alice"})

    def test_a_group_that_turns_out_mixed_keeps_its_name_but_is_not_saved(self):
        # A group with five lines of one person and one line of another keeps its name but is not saved.
        self.frame(0, {**GRID, "speaking": "Alice"})
        self.observe(*self.lines(5))
        self.frame(30, {**GRID, "speaking": "Bella"})
        self.observe(*self.lines(1, first=30))
        self.assertEqual(self.teacher.decided, {"A": "Alice"})
        self.assertEqual(self.teacher.learnable(), {})

    def test_a_named_group_is_read_only_so_far(self):
        for i in range(ss.KEEP_READING * 4):
            self.frame(i * 5, {**GRID, "speaking": "Alice"})
        self.observe(*self.lines(ss.KEEP_READING * 4))
        self.assertEqual(sum(self.teacher.votes["A"].values()), ss.KEEP_READING)
        self.assertEqual(len(self.read), ss.KEEP_READING)

    def test_a_group_that_never_agrees_stops_being_read(self):
        lines = ss.MIXED_READING + 10
        for i in range(lines):
            self.frame(i * 5, {**GRID, "speaking": "Bella" if i % 2 else "Alice"})
        self.observe(*self.lines(lines))
        self.assertEqual(self.teacher.decided, {})
        self.assertEqual(sum(self.teacher.votes["A"].values()), ss.MIXED_READING)

    def test_a_group_the_user_named_is_not_read(self):
        self.frame(0, {**GRID, "speaking": "Alice"})
        self.observe(*self.lines(6), named={"A": "Carl"})
        self.assertEqual(self.read, [])
        self.assertEqual(self.teacher.learnable(), {})


class Renaming(Base):
    lines = Learning.lines

    def test_a_name_the_lines_turn_against_is_taken_back(self):
        # A group named after two lines of one speaker loses that name once most of its lines show someone else.
        self.frame(0, {**GRID, "speaking": "Alice"})
        self.assertEqual(self.observe(*self.lines(2)), {"A": "Alice"})
        self.frame(10, {**GRID, "speaking": "Bella"})
        self.assertEqual(self.observe(*self.lines(2, first=10), named={"A": "Alice"}), {})          # 2 of 4: kept
        self.assertEqual(self.observe(*self.lines(1, first=20), named={"A": "Alice"}), {"A": ""})   # 2 of 5
        self.assertEqual(self.teacher.decided, {})
        self.assertIn("A is no longer Alice", "\n".join(self.logged))

    def test_one_stray_line_does_not_take_a_name_back(self):
        # Named at 3 of 4, then a single line marked someone else must not take the name away (3 of 5 still holds).
        for i, name in enumerate(("Bella", "Alice", "Alice", "Alice", "Carl")):
            self.frame(10 * i, {**GRID, "speaking": name})
        self.assertEqual(self.observe(*(line(10 * i + 1, 10 * i + 4) for i in range(4))), {"A": "Alice"})
        self.assertEqual(self.observe(line(41, 44), named={"A": "Alice"}), {})                       # 3 of 5
        self.assertEqual(self.teacher.decided, {"A": "Alice"})

    def test_the_name_follows_the_lines_when_they_agree_on_someone_else(self):
        self.frame(0, {**GRID, "speaking": "Alice"})
        self.observe(*self.lines(2))
        self.frame(10, {**GRID, "speaking": "Bella"})
        self.assertEqual(self.observe(*self.lines(6, first=10), named={"A": "Alice"}), {"A": "Bella"})   # 6 of 8
        self.assertEqual(self.teacher.decided, {"A": "Bella"})
        self.assertEqual(self.teacher.learnable(), {})                  # mixed: not saved

    def test_a_name_another_group_has_needs_more_lines(self):
        # A new voice's first lines may still show the previous speaker's tile: a name another group already holds
        # needs more lines before it is given.
        self.frame(0, {**GRID, "speaking": "Alice"})
        self.assertEqual(self.observe(*self.lines(2)), {"A": "Alice"})
        self.frame(10, {**GRID, "speaking": "Alice"})
        self.assertEqual(self.observe(*self.lines(2, who="B", first=10), named={"A": "Alice"}), {})
        self.frame(20, {**GRID, "speaking": "Bella"})
        self.assertEqual(self.observe(*self.lines(6, who="B", first=20), named={"A": "Alice"}), {"B": "Bella"})

    def test_one_voice_split_in_two_groups_still_gets_its_name(self):
        self.frame(0, {**GRID, "speaking": "Alice"})
        self.observe(*self.lines(2))
        # B's first two lines show who talked before B: not counted (LAG_LINES), then four more name B.
        self.assertEqual(self.observe(*self.lines(5, who="B", first=10), named={"A": "Alice"}), {})
        self.assertIn("B 11-14s marked Alice, who talked before: not counted", "\n".join(self.logged))
        self.assertEqual(self.observe(*self.lines(1, who="B", first=35), named={"A": "Alice"}), {"B": "Alice"})

    def test_a_name_the_voice_rules_out_gets_no_vote(self):
        self.teacher.not_them = lambda who, name: who == "B" and name == "Alice"
        self.frame(0, {**GRID, "speaking": "Alice"})
        self.assertEqual(self.observe(*self.lines(4, who="B")), {})
        self.assertNotIn("B", self.teacher.votes)                       # not even counted against a later name
        self.assertIn("B 1-4s marked Alice, but the voice is not theirs", "\n".join(self.logged))
        self.frame(20, {**GRID, "speaking": "Bella"})
        self.assertEqual(self.observe(*self.lines(5, who="B", first=20)), {"B": "Bella"})
        self.assertEqual(self.teacher.learnable(), {"B": "Bella"})      # the early lines did not spoil the voice

    def test_a_name_another_voice_of_this_meeting_holds_needs_a_voice_like_it(self):
        # When no new frame is saved for a while, one voice's lines can be read on a stale frame still lighting
        # another voice's tile: a name another voice of this meeting holds gets no vote unless the line sounds like it.
        import numpy as np
        voices = {}
        self.teacher.line_voice = lambda start: voices.get(start)
        self.frame(0, {**GRID, "speaking": "Alice"})
        a = self.lines(2)
        voices.update({r["start"]: np.array([1, 0.1, 0, 0]) for r in a})
        self.observe(*a)
        self.frame(20, {**GRID, "speaking": "Bella"})
        b = self.lines(2, who="B", first=20)
        voices.update({r["start"]: np.array([0, 1, 0.1, 0]) for r in b})
        self.assertEqual(self.observe(*b), {"B": "Bella"})
        stale = self.lines(4, first=40)                                  # the frame at 20 s still stands
        voices.update({r["start"]: np.array([1, 0, 0.1, 0]) for r in stale})
        self.observe(*stale, shown={"A": "Alice", "B": "Bella"})
        self.assertNotIn("Bella", self.teacher.votes["A"])
        self.assertIn("A 51-54s marked Bella, but it sounds like A, not B", "\n".join(self.logged))
        odd = line(61, 64)                                               # a line of A that sounds like B: counted
        voices[61] = np.array([0.05, 1, 0, 0])
        self.observe(odd, shown={"A": "Alice", "B": "Bella"})
        self.assertEqual(self.teacher.votes["A"]["Bella"], 1)

    def test_a_name_cut_short_on_a_small_tile_counts_for_the_whole_name(self):
        # A label cut short on a small tile ("Alice Wo...") and the whole label ("Alice Wong") are one person.
        self.frame(0, {**GRID, "speaking": "Alice Wo..."})
        self.assertEqual(self.observe(*self.lines(2)), {"A": "Alice Wo..."})     # the whole name not seen yet
        self.frame(20, {**GRID, "speaking": "Alice Wong"})
        self.assertEqual(self.observe(*self.lines(1, first=20)), {"A": "Alice Wong"})
        self.assertEqual(dict(self.teacher.votes["A"]), {"Alice Wong": 3})
        self.frame(40, {**GRID, "speaking": "Alice W…"})
        self.observe(*self.lines(1, first=40))
        self.assertEqual(dict(self.teacher.votes["A"]), {"Alice Wong": 4})
        self.assertEqual(self.teacher.confirmed(self.rows, "A", "Alice Wong"), [1, 6, 21, 41])
        self.assertIsNone(ss.cut_short("Alice Wong"))
        self.assertIsNone(ss.cut_short("A..."))                                   # too little to go on

    def test_a_name_misread_by_one_character_counts_as_the_name_read_more(self):
        # A label read with one wrong character counts as the reading seen more, not as a second person.
        self.frame(0, {**GRID, "speaking": "Pip 石貓貓"})
        self.assertEqual(self.observe(*self.lines(2)), {"A": "Pip 石貓貓"})
        self.frame(20, {**GRID, "speaking": "Pip 石錨貓"})
        self.observe(*self.lines(1, first=20))
        self.assertEqual(dict(self.teacher.votes["A"]), {"Pip 石貓貓": 3})
        self.assertTrue(ss.misread("Pip 石貓貓", "PIP石錨貓"))
        self.assertFalse(ss.misread("番茄一號", "番茄二號"))                        # no Latin letters: two people
        self.assertFalse(ss.misread("Pip 石貓貓", "Pip 右錨貓"))                 # two characters apart
        self.assertFalse(ss.misread("Alice", "Alicf"))

    def test_the_reading_seen_most_takes_over_from_a_misreading_seen_first(self):
        # On downscaled screenshots a label can be misread about as often as it is read right: the reading seen
        # most takes over from one seen first.
        self.frame(0, {**GRID, "speaking": "Pip 石錨貓"})
        self.assertEqual(self.observe(*self.lines(2)), {"A": "Pip 石錨貓"})
        self.frame(20, {**GRID, "speaking": "Pip 石貓貓"})
        self.assertEqual(self.observe(*self.lines(2, first=20)), {})            # 2 to 2: the first reading stands
        self.assertEqual(dict(self.teacher.votes["A"]), {"Pip 石錨貓": 4})
        self.assertEqual(self.observe(*self.lines(1, first=40)), {"A": "Pip 石貓貓"})
        self.assertEqual(dict(self.teacher.votes["A"]), {"Pip 石貓貓": 5})
        self.assertEqual(len(self.teacher.confirmed(self.rows, "A", "Pip 石貓貓")), 5)

    def test_two_readings_of_one_label(self):
        self.assertTrue(ss.same_label("Pip 石錨貓", "Pip 石貓貓"))
        self.assertTrue(ss.same_label("諸葛蘑...", "諸葛蘑菇"))
        self.assertFalse(ss.same_label("諸葛蘑菇", "南瓜湯..."))               # the neighbour's tile
        self.assertFalse(ss.same_label("蘑菇派", "南瓜湯"))
        self.assertFalse(ss.same_label("Alice Wong", "Alice Wang Lee"))

    def test_a_line_the_screen_shows_someone_else_saying_is_unsure(self):
        # One line of a named voice shows someone else talking: it is set aside as unsure, not counted for the
        # voice's name.
        self.frame(0, {**GRID, "speaking": "Alice"})
        self.observe(*self.lines(3))
        self.frame(20, {**GRID, "speaking": "Bella"})
        self.observe(*self.lines(1, first=20), named={"A": "Alice"}, shown={"A": "Alice"})
        self.assertEqual(self.teacher.take_unsure(), [(21, "Bella", "Alice")])
        self.assertEqual(self.teacher.take_unsure(), [])
        self.assertEqual(self.teacher.confirmed(self.rows, "A", "Alice"), [1, 6, 11])

    def test_a_voice_misnamed_by_its_saved_voice_is_not_unsure(self):
        self.frame(0, {**GRID, "speaking": "Carl"})
        self.assertEqual(self.observe(*self.lines(2), shown={"A": "Alice"}), {"A": "Carl"})
        self.assertEqual(self.teacher.take_unsure(), [])                # the screen's name leads: A was misnamed

    def test_a_name_the_user_gave_after_the_screen_is_kept(self):
        self.frame(0, {**GRID, "speaking": "Alice"})
        self.observe(*self.lines(2))
        self.read.clear()
        self.frame(10, {**GRID, "speaking": "Bella"})
        self.assertEqual(self.observe(*self.lines(6, first=10), named={"A": "Carl"}), {})
        self.assertEqual(self.read, [])


class TheUser(Base):
    def test_the_users_name_is_never_given(self):
        self.frame(0, {**GRID, "speaking": "山姆"})
        self.frame(10, {**GRID, "speaking": "Sam Reed"})                # their tile, before it is known
        self.frame(20, {**GRID, "speaking": "Alice", "speaking_is_you": True})
        self.observe(line(1, 4), line(5, 8), line(11, 14), line(15, 18), line(21, 24), line(25, 28))
        self.assertEqual(self.teacher.votes, {})

    def test_the_users_own_tile_is_learnt_then_namesakes_count(self):
        self.frame(0, {**GRID, "speaking": "Sam Reed"})
        self.observe(line(1, 4, speaker="我"), line(5, 8, speaker="我"))
        self.assertEqual(self.teacher.self_tile, "sam reed")
        self.frame(10, {**GRID, "speaking": "Sam Chen"})
        self.observe(line(11, 14), line(15, 18))
        self.assertEqual(self.teacher.decided, {"A": "Sam Chen"})

    def test_a_tile_the_call_was_marked_with_is_not_learnt_as_the_users(self):
        # When the user talks while muted, the tile of whoever spoke last stays marked: it is not learnt as theirs.
        self.frame(0, {**GRID, "speaking": "Bella Chou"})
        self.observe(line(1, 4), line(5, 8))
        self.observe(line(11, 14, speaker="我"), line(15, 18, speaker="我"))
        self.assertIsNone(self.teacher.self_tile)

    def test_a_tile_wrongly_learnt_as_the_users_is_given_up(self):
        self.frame(0, {**GRID, "speaking": "Bella Chou"})
        self.observe(line(1, 4, speaker="我"), line(5, 8, speaker="我"))
        self.assertEqual(self.teacher.self_tile, "bella chou")
        self.observe(line(11, 14), line(15, 18))                         # the user's voice is not in the call
        self.assertIsNone(self.teacher.self_tile)
        self.assertIn("screen: Bella Chou is not the user's own tile after all", self.logged)
        self.assertEqual(self.observe(line(21, 24)), {"A": "Bella Chou"})
        self.observe(line(31, 34, speaker="我"), line(35, 38, speaker="我"))
        self.assertIsNone(self.teacher.self_tile)                       # not learnt again

    def test_a_tile_under_the_users_name_is_never_given_up(self):
        # The call's sound can light the user's own tile during call lines: a tile under the user's name is never
        # given up as theirs and counted as someone else.
        self.frame(0, {**GRID, "speaking": "Sam Park"})
        self.observe(line(1, 4, speaker="我"), line(5, 8, speaker="我"))
        self.assertEqual(self.teacher.self_tile, "sam park")
        self.assertEqual(self.observe(line(11, 14), line(15, 18), line(21, 24)), {})
        self.assertEqual(self.teacher.self_tile, "sam park")
        self.assertEqual(self.teacher.votes, {})

    def test_a_user_line_with_others_talking_teaches_nothing(self):
        self.frame(0, {**GRID, "speaking": "Alice"})
        self.observe(line(1, 4, speaker="我"), line(3, 6, who="B"))
        self.assertEqual(self.teacher.self_looks, 0)


class Helpers(unittest.TestCase):
    def test_norm(self):
        self.assertEqual(ss.norm("  Sam   Reed (Presenting) "), "sam reed")
        self.assertEqual(ss.norm("毛毛蟲（你）"), "毛毛蟲")
        self.assertEqual(ss.norm(None), "")

    def test_parse_reply(self):
        self.assertEqual(ss.parse_reply('Sure: {"speaking": "Alice"} done'), {"speaking": "Alice"})
        self.assertIsNone(ss.parse_reply("no json"))
        self.assertIsNone(ss.parse_reply("[1, 2]"))
        self.assertIsNone(ss.parse_reply(None))

    def test_frames(self):
        with tempfile.TemporaryDirectory() as d:
            disp = Path(d) / "display-1"
            disp.mkdir()
            (Path(d) / "STOP").write_text("")
            for name in ("frame-1000.jpg", "frame-3000.jpg", "frame-x.jpg"):
                (disp / name).write_bytes(b"")
            frames = ss.Frames(d)
            self.assertEqual(frames.displays(), [disp])
            self.assertIsNone(frames.at(disp, 0.5))
            self.assertEqual(frames.at(disp, 2).name, "frame-1000.jpg")
            self.assertEqual(frames.at(disp, 9).name, "frame-3000.jpg")
            self.assertIsNone(frames.at(Path(d) / "display-9", 9))
            self.assertEqual(ss.Frames(Path(d) / "missing").displays(), [])


if __name__ == "__main__":
    unittest.main()
