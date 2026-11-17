#!/usr/bin/env bats
# The prebuilt Swift helpers in app/bin: frames-to-video, screen-ocr, mic-users and the
# screen-capture bundle's permission query. Tests skip when a helper has not been built.
# Never run screen-capture without arguments or with --request-access: it may show a dialog.

bats_require_minimum_version 1.5.0
load common

setup() {
  common_setup
  FFPROBE="$(PATH="$ORIG_PATH" command -v ffprobe || true)"
  VENV_PY="$REPO/app/asr-venv/bin/python"
  F2V="$REPO/app/bin/frames-to-video"
  OCR="$REPO/app/bin/screen-ocr"
  MIC="$REPO/app/bin/mic-users"
  CAPTURE="$REPO/app/bin/MeetingCopilotScreen.app/Contents/MacOS/screen-capture"
  FRAMES="$BATS_TEST_TMPDIR/frames"
  mkdir -p "$FRAMES"
}

need_pil() {
  [ -x "$VENV_PY" ] || skip "no app/asr-venv/bin/python"
  "$VENV_PY" -c "import PIL" 2>/dev/null || skip "PIL is not installed in the venv"
}

# make_frames <dir> <ms>... : one JPEG per timestamp; sizes cycle through odd and other aspects.
make_frames() {
  "$VENV_PY" - "$@" <<'PY'
import sys
from PIL import Image, ImageDraw
d, stamps = sys.argv[1], sys.argv[2:]
sizes = [(641, 481), (800, 400), (640, 480)]        # odd dimensions, another aspect ratio, plain
for i, ms in enumerate(stamps):
    w, h = sizes[i % len(sizes)]
    im = Image.new("RGB", (w, h), (20 * i, 80, 160))
    ImageDraw.Draw(im).rectangle([10, 10, w // 2, h // 2], fill=(255, 255, 255))
    im.save(f"{d}/frame-{ms}.jpg", quality=85)
PY
}

@test "frames-to-video: three frames -> even-sized h264 video of (last - first) + 1 s" {
  [ -x "$F2V" ] || skip "frames-to-video is not built"
  [ -n "$FFPROBE" ] || skip "ffprobe not found"
  need_pil
  make_frames "$FRAMES" 1000 2500 4000
  [ "$(ls "$FRAMES" | wc -l | tr -d ' ')" -eq 3 ]
  OUT="$BATS_TEST_TMPDIR/out.mp4"
  run "$F2V" "$FRAMES" "$OUT"
  echo "$output"
  [ "$status" -eq 0 ]
  [ -s "$OUT" ]
  probe="$("$FFPROBE" -v error -show_entries stream=codec_name,width,height -show_entries format=duration -of default=nw=1 "$OUT")"
  echo "$probe"
  [[ "$probe" == *"codec_name=h264"* ]]
  width="$(printf '%s\n' "$probe" | sed -n 's/^width=//p' | head -1)"
  height="$(printf '%s\n' "$probe" | sed -n 's/^height=//p' | head -1)"
  duration="$(printf '%s\n' "$probe" | sed -n 's/^duration=//p' | head -1)"
  [ -n "$width" ] && [ -n "$height" ] && [ -n "$duration" ]
  [ $((width % 2)) -eq 0 ]
  [ $((height % 2)) -eq 0 ]
  # Each frame is shown until the next one and the last one for 1 s: (4000 - 1000) / 1000 + 1 = 4 s.
  # The helper then repeats the last frame once more at the end (frames-to-video.swift), and the
  # writer gives that repeated frame its own second, so the file measures 5 s. Accept both.
  awk -v d="$duration" 'BEGIN { exit !(d >= 3.5 && d <= 5.5) }'
}

@test "frames-to-video: a folder with one frame exits 1" {
  [ -x "$F2V" ] || skip "frames-to-video is not built"
  need_pil
  make_frames "$FRAMES" 1000
  run "$F2V" "$FRAMES" "$BATS_TEST_TMPDIR/out.mp4"
  [ "$status" -eq 1 ]
  [[ "$output" == *"1 frame(s), no video"* ]]
  [ ! -s "$BATS_TEST_TMPDIR/out.mp4" ]
}

@test "frames-to-video: no arguments exits 1 with the usage" {
  [ -x "$F2V" ] || skip "frames-to-video is not built"
  run "$F2V"
  [ "$status" -eq 1 ]
  [[ "$output" == *"usage: frames-to-video <frames-dir> <out.mp4>"* ]]
}

@test "screen-ocr: no arguments exits 2 with the usage" {
  [ -x "$OCR" ] || skip "screen-ocr is not built"
  run "$OCR"
  [ "$status" -eq 2 ]
  [[ "$output" == *"usage: screen-ocr <image>"* ]]
}

@test "screen-ocr: reads large text from an image (macOS 26), or exits 3 on older macOS" {
  [ -x "$OCR" ] || skip "screen-ocr is not built"
  need_pil
  IMG="$BATS_TEST_TMPDIR/text.png"
  "$VENV_PY" - "$IMG" <<'PY'
import sys
from PIL import Image, ImageDraw, ImageFont
im = Image.new("RGB", (900, 300), "white")
ImageDraw.Draw(im).text((60, 80), "ABC 123", fill="black", font=ImageFont.load_default(size=120))
im.save(sys.argv[1])
PY
  major="$(sw_vers -productVersion | cut -d. -f1)"
  run "$OCR" "$IMG"
  echo "$output"
  if [ "$major" -ge 26 ]; then
    [ "$status" -eq 0 ]
    [[ "$output" == *"ABC"* ]]
  else
    [ "$status" -eq 3 ]
    [[ "$output" == *"needs macOS 26"* ]]
  fi
}

@test "mic-users: exits 0 and prints '<pid>TAB<name>' lines (or nothing)" {
  [ -x "$MIC" ] || skip "mic-users is not built"
  run "$MIC"
  [ "$status" -eq 0 ]
  re=$'^[0-9]+\t.+$'
  for line in "${lines[@]}"; do
    [[ "$line" =~ $re ]]
  done
}

@test "screen-capture --access-status writes granted or denied and exits 0" {
  [ -x "$CAPTURE" ] || skip "screen-capture is not built"
  STATUS="$BATS_TEST_TMPDIR/access.txt"
  run "$CAPTURE" --access-status "$STATUS"
  [ "$status" -eq 0 ]
  [ -f "$STATUS" ]
  content="$(cat "$STATUS")"
  [ "$content" = granted ] || [ "$content" = denied ]
}

# make_meet <file> <lit tile> [clock] : a 1920x1080 Meet-like grid of 6 tiles, one with the speaker's border.
make_meet() {
  "$VENV_PY" - "$@" <<'PY'
import sys
from PIL import Image, ImageDraw
path, lit = sys.argv[1], int(sys.argv[2])
clock = sys.argv[3] if len(sys.argv) > 3 else "08:15"
im = Image.new("RGB", (1920, 1080), (32, 33, 36))
d = ImageDraw.Draw(im)
for i in range(6):
    x, y = 150 + (i % 3) * 560, 110 + (i // 3) * 450
    d.rounded_rectangle([x, y, x + 500, y + 410], radius=16, fill=(60, 64, 90))
    d.ellipse([x + 200, y + 155, x + 300, y + 255], fill=(150, 150, 170))
    if i == lit:
        d.rounded_rectangle([x, y, x + 500, y + 410], radius=16, outline=(168, 199, 250), width=5)
d.text((45, 45), clock, fill=(230, 230, 230))
im.save(path, quality=85)
PY
}

@test "screen-capture --compare: the speaker's border moving to another tile is a change, the clock is not" {
  [ -x "$CAPTURE" ] || skip "screen-capture is not built"
  need_pil
  make_meet "$FRAMES/a.jpg" 4
  make_meet "$FRAMES/b.jpg" 0
  make_meet "$FRAMES/c.jpg" 4 08:16
  # stdout only: in a virtual machine a system framework logs a driver warning to stderr
  # ("IOServiceMatchingfailed for: AppleM2ScalerParavirtDriver").
  run --separate-stderr "$CAPTURE" --compare "$FRAMES/a.jpg" "$FRAMES/a.jpg"
  [ "$output" = same ]
  run --separate-stderr "$CAPTURE" --compare "$FRAMES/a.jpg" "$FRAMES/b.jpg"
  [ "$output" = "small change" ]      # a border move changes no dHash bit
  run --separate-stderr "$CAPTURE" --compare "$FRAMES/a.jpg" "$FRAMES/c.jpg"
  [ "$output" = same ]
}
