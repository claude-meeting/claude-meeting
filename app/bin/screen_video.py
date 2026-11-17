#!/usr/bin/env python3
"""Turn a meeting's captured screen frames into a video for review afterwards.

bin/screen-capture saves a frame only when the screen changed (frame-<epoch ms>.jpg, ~1 fps), so each
frame is shown until the next one: the video has the meeting's real timing at a fraction of the size
of a continuous recording. The video is not sent to Claude. [retention] video_days applies.

Usage: screen_video.py <frames dir> <out.mp4> [live notes .md to link the video from]
A frames dir with display-<n>/ subfolders gives <out>-display-<n>.mp4 per display.
"""
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "lib"))
import config  # noqa: E402

FRAME = re.compile(r"frame-(\d+)\.(jpe?g|png)$")
HELPER = Path(config.tool("frames-to-video", Path(__file__).resolve().parent / "frames-to-video"))  # AVFoundation
MAX_HOLD_SECS = 600   # a gap longer than this (capture paused) is shortened


def quote(path):
    """A path inside ffmpeg's concat list: single quotes are written as '\\'' ."""
    return str(path).replace("'", "'\\''")


def frames(folder):
    found = [(int(m.group(1)), p) for p in Path(folder).iterdir() if (m := FRAME.match(p.name))]
    return sorted(found)


def make_video(folder, out):
    items = frames(folder)
    if len(items) < 2:
        print(f"{folder}: {len(items)} frame(s), no video")
        return False
    Path(out).parent.mkdir(parents=True, exist_ok=True)
    if HELPER.exists():
        try:
            r = subprocess.run([str(HELPER), str(folder), str(out)], capture_output=True, text=True, timeout=1800)
        except subprocess.TimeoutExpired:
            print("frames-to-video timed out", file=sys.stderr)
            return False
        print((r.stdout or r.stderr).strip())
        return r.returncode == 0
    if not shutil.which("ffmpeg"):
        print("no frames-to-video helper and no ffmpeg: no video", file=sys.stderr)
        return False
    with tempfile.NamedTemporaryFile("w", suffix=".txt", delete=False) as f:
        f.write("ffconcat version 1.0\n")
        for (t, p), (t_next, _) in zip(items, items[1:] + [(items[-1][0] + 1000, None)]):
            f.write(f"file '{quote(p)}'\nduration {min((t_next - t) / 1000, MAX_HOLD_SECS):.3f}\n")
        f.write(f"file '{quote(items[-1][1])}'\n")   # concat needs the last file repeated to honour its duration
        listing = f.name
    out = Path(out)
    out.parent.mkdir(parents=True, exist_ok=True)
    cmd = ["ffmpeg", "-y", "-loglevel", "error", "-f", "concat", "-safe", "0", "-i", listing,
           "-vf", "scale=trunc(iw/2)*2:trunc(ih/2)*2,format=yuv420p", "-fps_mode", "vfr",
           "-c:v", "libx264", "-preset", "veryfast", "-tune", "stillimage", "-crf", "28",
           "-movflags", "+faststart", str(out)]
    r = subprocess.run(cmd, capture_output=True, text=True)
    Path(listing).unlink(missing_ok=True)
    if r.returncode != 0:
        print(f"ffmpeg failed: {r.stderr[-500:]}", file=sys.stderr)
        return False
    minutes = (items[-1][0] - items[0][0]) / 60000
    print(f"{out}: {len(items)} frames, {minutes:.1f} min, {out.stat().st_size / 1e6:.1f} MB")
    return True


if __name__ == "__main__":
    if len(sys.argv) not in (3, 4):
        sys.exit(__doc__)
    folder, out = Path(sys.argv[1]), Path(sys.argv[2])
    # One video per display folder (display-1 = main display); a flat folder gives one video.
    subdirs = sorted(p for p in folder.iterdir() if p.is_dir()) if folder.is_dir() else []
    jobs = [(d, out.with_name(f"{out.stem}-{d.name}{out.suffix}")) for d in subdirs] or [(folder, out)]
    made = [o for d, o in jobs if make_video(d, o)]
    if made and len(sys.argv) == 4:
        sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "lib"))
        import config
        import i18n
        lang = i18n.Lang(config.load())
        with open(sys.argv[3], "a") as notes:
            notes.write("\n" + "".join(lang.t("video_link", path=o) + "\n" for o in made))
    sys.exit(0 if made else 1)
