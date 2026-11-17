"""lib/minutes_record.py: Minutes' meeting file, given our transcript."""
import json
import sys
import tempfile
import unittest
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "lib"))
import minutes_record as mr  # noqa: E402

MEETING = """---
title: Weekly sync
type: meeting
date: 2026-01-15T10:00:00.123456-05:00
speaker_map:
- speaker_label: SPEAKER_1
---

## Summary

- 摘要

## Transcript

[SPEAKER_1 0:00] 舊的逐字稿
[SPEAKER_2 0:10] 第二句
"""


class Record(unittest.TestCase):
    def test_the_start_comes_from_the_frontmatter(self):
        self.assertEqual(mr.meeting_start(MEETING), datetime.fromisoformat("2026-01-15T10:00:00.123456-05:00").timestamp())
        self.assertIsNone(mr.meeting_start("## Transcript\ndate: 2026-01-15T10:00:00-05:00\n"))   # not frontmatter
        self.assertIsNone(mr.meeting_start("---\ndate: soon\n---\n"))

    def test_the_live_transcript_of_the_same_start(self):
        with tempfile.TemporaryDirectory() as d:
            for name in ("rec-1000.jsonl", "rec-1020.jsonl", "rec-2000.jsonl", "rec-x.jsonl"):
                (Path(d) / name).write_text("")
            self.assertEqual(mr.live_transcript(d, 1018.4).name, "rec-1020.jsonl")     # the closest
            self.assertIsNone(mr.live_transcript(d, 1500))

    def test_the_transcript_is_replaced_and_the_rest_kept(self):
        rows = [{"start": 0.3, "speaker": "其他人", "who": "A", "text": "新的第一句"},
                {"start": 75.9, "speaker": "我", "text": " 第二句 "},
                {"start": 80, "speaker": "其他人", "text": "  "}]
        lines = mr.transcript_lines(rows, lambda r: "Sam" if r["speaker"] == "我" else "Alice")
        self.assertEqual(lines, ["[Alice 0:00] 新的第一句", "[Sam 1:15] 第二句"])
        new = mr.with_transcript(MEETING, lines)
        self.assertIn("## Summary\n\n- 摘要\n", new)
        self.assertTrue(new.endswith("## Transcript\n\n[Alice 0:00] 新的第一句\n[Sam 1:15] 第二句\n"))
        self.assertNotIn("舊的逐字稿", new)
        later = mr.with_transcript(MEETING + "\n## Notes\n\nmine\n", lines)
        self.assertTrue(later.endswith("[Sam 1:15] 第二句\n\n## Notes\n\nmine\n"))        # a section after it stays
        self.assertIsNone(mr.with_transcript("---\n---\n## Summary\n", lines))
        self.assertIsNone(mr.with_transcript(MEETING, []))

    def test_rows_are_read_past_a_broken_line(self):
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / "rec-1.jsonl"
            p.write_text(json.dumps({"text": "a"}) + "\n{broken\n" + json.dumps({"text": "b"}) + "\n")
            self.assertEqual([r["text"] for r in mr.read_rows(p)], ["a", "b"])


class Parts(unittest.TestCase):
    def test_parts_of_a_meeting(self):
        with tempfile.TemporaryDirectory() as d:
            d = Path(d)
            a, b, c, other = (d / f"rec-{e}.jsonl" for e in (100, 400, 900, 2000))
            for t in (a, b, c, other):
                t.write_text(json.dumps({"start": 1.0, "end": 5.0, "speaker": "我", "text": "x"}) + "\n")
            mr.mark_part(b, a)
            mr.mark_part(c, b)
            self.assertEqual(mr.first_part(c), a)
            self.assertEqual(mr.previous_part(c), b)
            self.assertIsNone(mr.previous_part(a))
            self.assertEqual(mr.parts(b), [a, b, c])
            self.assertEqual(mr.parts(other), [other])
            self.assertEqual(mr.merged_lines([a, b], lambda r: "Me", lambda secs: f"gap {secs}"),
                             ["[Me 0:01] x", "gap 295", "[Me 5:01] x"])
            self.assertIsNone(mr.epoch_of(d / "notes.jsonl"))
            md = d / "m.md"
            md.write_text("---\ndate: " + datetime.fromtimestamp(400).astimezone().isoformat() + "\n---\n")
            (d / "bad.md").write_text("no frontmatter")
            self.assertEqual(mr.meeting_file(d, 401), md)
            self.assertIsNone(mr.meeting_file(d, 5000))


if __name__ == "__main__":
    unittest.main()
