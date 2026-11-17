"""Screen updates for the live copilot.

Frames come from bin/screen-capture (or, as a fallback, Minutes' own screenshots). For each new one:
1. Perceptual hash (dHash) against the last kept frame: if the screen barely changed,
   the frame is skipped.
2. Otherwise OCR it (Apple Vision, zh-Hant + en) and diff its lines against the last
   kept frame:
   - mostly the same lines -> scrolling or a small update: only the new lines are passed on
   - mostly different      -> a new slide / page: the whole text is passed on
   Lines that never change (meeting-app buttons, names) cancel out in the diff.
"""
import subprocess
import time
from pathlib import Path

from PIL import Image

HASH_DISTANCE_SAME = 4       # of 64 bits; at or below this the frame counts as unchanged
SCROLL_OVERLAP = 0.5         # share of current lines already seen -> scroll/update, not a new page
MIN_NEW_CHARS = 8            # ignore updates that add almost nothing (clock ticks, cursor)


def dhash(path, size=8):
    with Image.open(path) as im:
        g = im.convert("L").resize((size + 1, size), Image.Resampling.LANCZOS)
        px = list(g.getdata())
    bits = 0
    for row in range(size):
        for col in range(size):
            bits = (bits << 1) | (px[row * (size + 1) + col] > px[row * (size + 1) + col + 1])
    return bits


def hamming(a, b):
    return bin(a ^ b).count("1")


class ScreenWatcher:
    def __init__(self, ocr_bin):
        self.ocr_bin = ocr_bin
        self.seen = set()            # paths already processed
        self.last_hash = None
        self.last_lines = []

    def ocr(self, path):
        try:
            r = subprocess.run([self.ocr_bin, str(path)], capture_output=True, text=True, timeout=30)
        except (OSError, subprocess.TimeoutExpired):
            return []                     # missing helper or a slow frame: this frame has no text
        return [l.strip() for l in r.stdout.splitlines() if l.strip()] if r.returncode == 0 else []

    def process(self, path):
        """Returns None or {"kind": "new_page"|"update", "text": str, "path": str}."""
        path = Path(path)
        if path in self.seen:
            return None
        try:
            h = dhash(path)
        except OSError:
            return None                       # still being written; caller retries later
        self.seen.add(path)
        if self.last_hash is not None and hamming(h, self.last_hash) <= HASH_DISTANCE_SAME:
            return None
        self.last_hash = h
        lines = self.ocr(path)
        prev = set(self.last_lines)
        overlap = sum(l in prev for l in lines) / len(lines) if lines else 1.0
        self.last_lines = lines
        if overlap >= SCROLL_OVERLAP:
            new = [l for l in lines if l not in prev]
            if sum(len(l) for l in new) < MIN_NEW_CHARS:
                return None
            return {"kind": "update", "text": "\n".join(new), "path": str(path)}
        return {"kind": "new_page", "text": "\n".join(lines), "path": str(path)}

    def poll(self, root, newer_than=0.0):
        """Process unseen screenshots under root (oldest first). Returns the events."""
        files = []
        for p in Path(root).rglob("*"):
            if p.suffix.lower() not in (".png", ".jpg", ".jpeg") or p in self.seen:
                continue
            try:
                mtime = p.stat().st_mtime
            except OSError:
                continue                  # deleted while we were listing
            if mtime >= newer_than and time.time() - mtime > 0.5:
                files.append((mtime, p))
        return [e for e in (self.process(p) for _, p in sorted(files)) if e]
