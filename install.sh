#!/bin/bash
# meeting-copilot installer. Safe to re-run: every step skips what is already done.
#
#   ./install.sh                 install or update
#   ./install.sh --name Alex     set your name in a new config without asking
#   ./install.sh --no-service    don't install or restart the background service
#   ./install.sh --get-minutes   download and install the Minutes app (needed when it is missing)
#   ./install.sh --get-claude    install Claude Code with Anthropic's installer (then sign in yourself)
#   ./install.sh --only-prerequisites   stop after the --get-* steps (the app's setup guide uses this)
#
# Needs: Apple Silicon Mac, macOS 15+, the Minutes app, Claude Code signed in with
# your own Claude account, Xcode Command Line Tools (swiftc), and uv.
set -euo pipefail

ROOT="$(cd "$(dirname "$0")" && pwd)"
xattr -dr com.apple.quarantine "$ROOT" 2>/dev/null || true   # files copied out of a downloaded app
APP="$ROOT/app"
VENV="$APP/asr-venv"
PY="$VENV/bin/python"
CONFIG="${MEETING_COPILOT_CONFIG:-$HOME/.config/meeting-copilot/config.toml}"   # the file the engine reads
CONFIG_DIR="$(dirname "$CONFIG")"
LABEL=com.meeting-copilot.autopilot
PLIST="$HOME/Library/LaunchAgents/$LABEL.plist"
SPEAKER_URL="https://github.com/k2-fsa/sherpa-onnx/releases/download/speaker-recongition-models/3dspeaker_speech_campplus_sv_zh_en_16k-common_advanced.onnx"
NAME=""
SERVICE=1
GET_MINUTES=0
GET_CLAUDE=0
ONLY_PREREQS=0

while [ $# -gt 0 ]; do
  case "$1" in
    --name) NAME="${2:?--name needs a value}"; shift 2 ;;
    --no-service) SERVICE=0; shift ;;
    --get-minutes) GET_MINUTES=1; shift ;;
    --get-claude) GET_CLAUDE=1; shift ;;
    --only-prerequisites) ONLY_PREREQS=1; shift ;;
    -h|--help) sed -n '2,12p' "$0"; exit 0 ;;
    *) echo "unknown option: $1" >&2; exit 2 ;;
  esac
done

step() { printf '\n==> %s\n' "$*"; }
warn() { printf '  ! %s\n' "$*"; }
die() { printf '  ✗ %s\n' "$*" >&2; exit 1; }

# Refused before anything is written: a log inside the signed app would break its signature.
case "$ROOT" in
  *.app/Contents/*) [ "$ONLY_PREREQS" = 1 ] || die "this is the copy inside the app; the setup guide installs its own copy" ;;
esac

# The Mac app's setup guide reads this output through a pipe. If the app quits mid-install the pipe closes,
# and the next echo would end the install right after the long downloads: keep going without the reader
# instead, and keep a log of the full install either way. Only that reader ignores SIGPIPE: an ignored signal
# is inherited by every program started from here, and pipelines like `... | grep -q` rely on the default.
LOG=/dev/null
if [ "$ONLY_PREREQS" = 0 ]; then
  mkdir -p "$APP/state"
  LOG="$APP/state/install.log"
  : > "$LOG"
fi
exec > >(trap '' PIPE; while IFS= read -r line || [ -n "$line" ]; do
  printf '%s\n' "$line" >> "$LOG"
  printf '%s\n' "$line" 2>/dev/null || true
done) 2>&1

step "Checking the system"
[ "$(uname -m)" = arm64 ] || die "Apple Silicon is required (speech recognition runs on MLX)."
major="$(sw_vers -productVersion | cut -d. -f1)"
[ "$major" -ge 15 ] || die "macOS 15 or later is required."
[ "$major" -ge 26 ] || [ "$ONLY_PREREQS" = 1 ] \
  || warn "macOS 26 is needed for screen text recognition; screens are captured but not read."
MINUTES_APP="$(grep -E '^[[:space:]]*minutes_app[[:space:]]*=' "$CONFIG" 2>/dev/null \
  | sed -E "s/^[^\"']*[\"']([^\"']*)[\"'].*/\\1/" || true)"     # "..." or '...' 
MINUTES_APP="${MINUTES_APP:-/Applications/Minutes.app}"
MINUTES_APP="${MINUTES_APP/#\~/$HOME}"
if [ ! -x "$MINUTES_APP/Contents/MacOS/minutes" ] && [ "$GET_MINUTES" = 1 ]; then
  # Latest release .dmg from GitHub; the app inside must carry Minutes' notarized Developer ID
  # signature (team 63TMLKT8HN) before it is installed.
  echo "  downloading Minutes ..."
  tmp="$(mktemp -d)"
  trap 'rm -rf "$tmp"' EXIT                  # whatever fails below, the download does not stay behind
  rel="$(curl -fsSL -H 'User-Agent: meeting-copilot' https://api.github.com/repos/silverstein/minutes/releases/latest)"
  url="$(printf '%s' "$rel" | grep -oE 'https://[^"]+_aarch64\.dmg' | sed -n 1p || true)"   # pipefail: grep may find nothing
  [ -n "$url" ] || die "could not find the Minutes download; get it from https://github.com/silverstein/minutes/releases"
  curl -fsSL -o "$tmp/minutes.dmg" "$url"
  mnt="$(hdiutil attach -nobrowse -readonly "$tmp/minutes.dmg" 2>/dev/null | awk -F'\t' '/\/Volumes\//{print $NF}' | tail -1)"
  [ -n "$mnt" ] || die "could not open the Minutes disk image ($tmp/minutes.dmg)"
  [ -d "$mnt/Minutes.app" ] || { hdiutil detach "$mnt" -quiet; die "unexpected Minutes disk image"; }
  signer="$(codesign -dv "$mnt/Minutes.app" 2>&1 || true)"
  if ! codesign --verify --deep --strict "$mnt/Minutes.app" 2>/dev/null \
     || [ "${signer#*TeamIdentifier=63TMLKT8HN}" = "$signer" ]; then
    hdiutil detach "$mnt" -quiet; die "the Minutes download is not signed by its developer; not installing it"
  fi
  rm -rf /Applications/Minutes.app
  cp -R "$mnt/Minutes.app" /Applications/ || { hdiutil detach "$mnt" -quiet; rm -rf "$tmp"; die "could not copy Minutes to /Applications"; }
  hdiutil detach "$mnt" -quiet
  rm -rf "$tmp"
  MINUTES_APP=/Applications/Minutes.app
  xattr -dr com.apple.quarantine /Applications/Minutes.app 2>/dev/null || true
  echo "  installed Minutes $(printf '%s' "$rel" | grep -oE '"tag_name": *"[^"]+"' | cut -d'"' -f4 || true)"
fi
if [ "$ONLY_PREREQS" = 0 ] || [ "$GET_MINUTES" = 1 ]; then
  [ -x "$MINUTES_APP/Contents/MacOS/minutes" ] \
    || die "Minutes is not installed at $MINUTES_APP: get it from https://github.com/silverstein/minutes (or re-run with --get-minutes)"
fi
claude_bin="$(command -v claude || true)"
for c in "$HOME/.local/bin/claude" /opt/homebrew/bin/claude /usr/local/bin/claude; do
  [ -n "$claude_bin" ] || [ ! -x "$c" ] || claude_bin="$c"
done
claude_bin="${claude_bin:-$HOME/.local/bin/claude}"
if [ ! -x "$claude_bin" ] && [ "$GET_CLAUDE" = 1 ]; then
  echo "  installing Claude Code with Anthropic's installer ..."
  curl -fsSL https://claude.ai/install.sh | bash >"${TMPDIR:-/tmp}/meeting-copilot-claude-install.log" 2>&1 \
    || warn "Claude Code's installer failed (see ${TMPDIR:-/tmp}/meeting-copilot-claude-install.log)"
  claude_bin="$HOME/.local/bin/claude"
fi
if [ "$ONLY_PREREQS" = 1 ]; then
  [ "$GET_MINUTES" = 0 ] || [ -x "$MINUTES_APP/Contents/MacOS/minutes" ] || die "Minutes was not installed"
  [ "$GET_CLAUDE" = 0 ] || [ -x "$claude_bin" ] || die "Claude Code was not installed"
  echo "  ok"
  exit 0
fi
# The Mac app bundles uv and prebuilt helpers; from a git checkout, uv and swiftc come from the system.
UV="$ROOT/bin/uv"
[ -x "$UV" ] || UV="$(command -v uv || true)"
[ -n "$UV" ] || die "uv is missing: run  brew install uv  (or see https://docs.astral.sh/uv/)"
echo "  ok: $(sw_vers -productVersion), Minutes, uv"

step "Python environment ($VENV)"
"$UV" python install 3.14 --no-bin >/dev/null 2>&1 || die "uv could not install Python 3.14 (uv $("$UV" --version 2>/dev/null)); update uv"
[ -x "$PY" ] || "$UV" venv --python 3.14 --clear "$VENV" >/dev/null || die "uv could not create $VENV"
"$UV" pip install --python "$PY" -q -r "$APP/requirements.txt"
echo "  ok: $("$PY" --version)"

step "Helper tools (Swift)"
mkdir -p "$APP/state"
SCREEN_APP="$APP/bin/MeetingCopilotScreen.app"
for tool in mic-users screen-ocr frames-to-video screen-capture; do
  src="$APP/src/$tool.swift" bin="$APP/bin/$tool"
  # Screen capture lives in an app bundle so macOS can list it under Screen Recording.
  [ "$tool" = screen-capture ] && bin="$SCREEN_APP/Contents/MacOS/screen-capture"
  # The Mac app ships signed, prebuilt helpers (bin/.prebuilt): use them as they are.
  if [ -f "$APP/bin/.prebuilt" ] && [ -x "$bin" ]; then echo "  bundled: $tool"; continue; fi
  # Rebuild only when the source changed: a new binary loses its Screen Recording grant.
  if [ -x "$bin" ] && ! [ "$src" -nt "$bin" ]; then echo "  up to date: $tool"; continue; fi
  # Never replace the helper while it captures a meeting: the next install rebuilds it.
  if [ "$tool" = screen-capture ] && [ -x "$bin" ] && "$APP/tools/screen-helper-plist.sh" "$SCREEN_APP" running; then
    echo "  screen-capture is capturing a meeting: rebuilt on the next install"; continue
  fi
  if ! command -v swiftc >/dev/null; then
    [ -x "$bin" ] && { echo "  using the bundled $tool"; continue; }
    die "Xcode Command Line Tools are missing (needed to build $tool): run  xcode-select --install"
  fi
  mkdir -p "$(dirname "$bin")"
  if swiftc -O -target arm64-apple-macos15.0 "$src" -o "$bin" 2>"$APP/state/build-$tool.log"; then
    if [ "$tool" = screen-capture ]; then
      "$APP/tools/screen-helper-plist.sh" "$SCREEN_APP" write
      "$APP/tools/codesign.sh" "$SCREEN_APP"
      "$APP/tools/screen-helper-plist.sh" "$SCREEN_APP" register
    else
      "$APP/tools/codesign.sh" "$bin"
    fi
    rm -f "$APP/state/build-$tool.log"; echo "  built: $tool"
  else
    warn "could not build $tool (see $APP/state/build-$tool.log)"
  fi
done
# Nothing below touches the helper while it captures a meeting: the next install does it.
capturing=0
[ -d "$SCREEN_APP" ] && "$APP/tools/screen-helper-plist.sh" "$SCREEN_APP" running && capturing=1
if [ $capturing = 0 ] && [ -d "$SCREEN_APP" ] && [[ "$(codesign -dr - "$SCREEN_APP" 2>&1)" == *cdhash* ]]; then
  echo "  re-signing screen-capture with a stable identity (grant Screen Recording once more)"
  "$APP/tools/codesign.sh" "$SCREEN_APP"
fi
# An older install's helper (the name before the rename, other keys) gets the current Info.plist, signed again with
# the same identity: after the step above, so a helper that just got the stable identity also gets the new name.
# The app's prebuilt helpers come with theirs (build.sh checks it).
[ -f "$APP/bin/.prebuilt" ] || "$APP/tools/screen-helper-plist.sh" "$SCREEN_APP" refresh
if [ $capturing = 0 ] && [ -x "$SCREEN_APP/Contents/MacOS/screen-capture" ]; then
  echo "  screen recording permission: $("$SCREEN_APP/Contents/MacOS/screen-capture" --request-access)"
fi

step "Models"
mkdir -p "$APP/models/speaker"
# Model downloads go through watch_download.py: a slow download may take as long as it needs, but one with
# no new data for MODEL_STALL_SECS (default 5 minutes) is stopped instead of holding the setup guide forever.
# Hugging Face keeps the partial files, so pressing Install again resumes.
watched() { "$PY" "$APP/tools/watch_download.py" "$@"; }
primary="$("$PY" -c "import sys; sys.path.insert(0, sys.argv[1]); import config; c = config.load(); v = c['asr']['primary_model']; print(config.AUTO_PRIMARY[config.language_family(c)] if v == 'auto' else v)" "$APP/lib")"
resolved="$("$PY" -c "import sys; sys.path.insert(0, sys.argv[1]); import config; print(config.asr_model(config.load(), 'primary_model'))" "$APP/lib")"
fallback="$("$PY" -c "import sys; sys.path.insert(0, sys.argv[1]); import config; print(config.asr_model(config.load(), 'fallback_model'))" "$APP/lib")"
if [ -d "$resolved" ]; then
  echo "  ok: speech recognition $resolved"
elif [ "${primary#mlx-community/}" != "$primary" ]; then
  # Already an MLX model on the Hub: download it as it is (converting an 8-bit model again wastes 2 GB).
  echo "  downloading $primary ..."
  watched --hf-cache --log "$APP/models/download.log" -- \
    "$PY" -c "import sys; from huggingface_hub import snapshot_download; snapshot_download(sys.argv[1])" "$primary" \
    || die "could not download $primary, see $APP/models/download.log"
  echo "  ok: speech recognition $primary"
else
  out="$APP/models/${primary##*/}-8bit"
  rm -rf "$out.part"
  echo "  converting $primary to MLX 8-bit (downloads ~4 GB once) ..."
  watched --hf-cache --watch "$out.part" --log "$APP/models/convert.log" -- \
    "$PY" -m mlx_audio.convert --hf-path "$primary" --mlx-path "$out.part" -q --q-bits 8 \
    || { rm -rf "$out.part"; die "model conversion failed, see $APP/models/convert.log"; }
  mv "$out.part" "$out"
  echo "  ok: $out"
fi
# The fallback model is fetched now, not in the middle of the first meeting that needs it.
# (A Hub id is fetched every time: when it is complete that is a quick check, when not it resumes.)
if [ -d "$fallback" ]; then
  echo "  ok: fallback model $fallback"
elif watched --hf-cache --log "$APP/models/download-fallback.log" -- \
    "$PY" -c "import sys; from huggingface_hub import snapshot_download; snapshot_download(sys.argv[1])" "$fallback"; then
  echo "  ok: fallback model $fallback"
else
  warn "could not download the fallback model $fallback, see $APP/models/download-fallback.log"
fi
speaker="$APP/models/speaker/${SPEAKER_URL##*/}"
if [ -s "$speaker" ]; then echo "  ok: speaker model"
else
  # Give up on a stalled transfer (under 10 KB/s for 2 minutes) instead of waiting forever.
  curl -fsSL --connect-timeout 30 --speed-limit 10240 --speed-time 120 --retry 2 -o "$speaker.part" "$SPEAKER_URL" \
    && mv "$speaker.part" "$speaker" \
    || { rm -f "$speaker.part"; die "could not download the speaker model ($SPEAKER_URL)"; }
  echo "  downloaded: speaker model"
fi

step "Configuration ($CONFIG)"
mkdir -p "$CONFIG_DIR"
if [ -f "$CONFIG" ]; then
  echo "  keeping your existing config"
else
  if [ -z "$NAME" ] && [ -t 0 ]; then read -r -p "  Your name as others say it in meetings: " NAME || true; fi
  "$PY" - "$CONFIG" "$NAME" <<'PY'
import json, sys
path, name = sys.argv[1:]
open(path, "w").write(
    "# meeting-copilot settings. Every key not set here keeps the default from the engine's\n"
    "# config.example.toml, which documents all of them. The Mac app's Settings window edits this file.\n\n"
    f"[user]\nname = {json.dumps(name, ensure_ascii=False)}\n")
PY
  echo "  created; the app's Settings window (or config.example.toml) explains every key"
fi
# Speaker diarization model for Minutes' post-meeting transcript (pyannote community-1, the most accurate model
# Minutes supports).
# Minutes' downloads cannot resume and have been seen to stall for many minutes without data: give each one
# a time limit (macOS has no `timeout`), so a stall costs a warning instead of the whole install.
MINUTES_SETUP_SECS="${MINUTES_SETUP_SECS:-1800}"
minutes_setup() {   # minutes_setup <what> <setup args...>
  local what="$1"; shift
  "$PY" - "$MINUTES_SETUP_SECS" "$MINUTES_APP/Contents/MacOS/minutes" setup "$@" <<'PYEOF' \
    || warn "could not download Minutes' $what: run  \"$MINUTES_APP/Contents/MacOS/minutes\" setup $*"
import subprocess, sys
secs, cmd = float(sys.argv[1]), sys.argv[2:]
try:
    sys.exit(subprocess.run(cmd, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                            timeout=secs).returncode)
except subprocess.TimeoutExpired:
    print(f"  ! stopped after {secs:.0f} s without finishing", flush=True)
    sys.exit(124)
PYEOF
}
[ -f "$HOME/.minutes/models/diarization/segmentation-community-1.onnx" ] \
  || minutes_setup "community-1 diarization model" --diarization --model community-1
# Minutes' own speech model (its post-meeting transcript, and the fallback when live transcription stops) and
# its VAD (without it non-English audio can loop): fetched when Minutes' health check says they are missing.
minutes_missing="$("$MINUTES_APP/Contents/MacOS/minutes" health --json 2>/dev/null | "$PY" -c '
import json, sys
try:
    items = json.load(sys.stdin)["data"]["items"]
except (ValueError, KeyError, TypeError):
    items = []
wanted = {"Speech model": "model", "VAD model": "vad"}
print(" ".join(wanted[i["label"]] for i in items if i.get("label") in wanted and i.get("state") != "ready"))
' 2>/dev/null || true)"
if [[ " $minutes_missing " == *" model "* ]]; then
  echo "  downloading Minutes' speech model ..."
  minutes_setup "speech model"
fi
if [[ " $minutes_missing " == *" vad "* ]]; then
  minutes_setup "VAD model" --vad
fi
# The smaller model the recordings we start ask for (lib/minutes_record.py): the health check above only looks at
# the model in the user's own Minutes config, so with another one there this is never fetched.
recording_model="$("$PY" "$APP/tools/setup_minutes.py" --recording-model-missing 2>/dev/null || true)"
if [ -n "$recording_model" ]; then
  echo "  downloading Minutes' $recording_model model (the transcript after each recording) ..."
  minutes_setup "$recording_model model" --model "$recording_model" --download-only
fi
"$PY" "$APP/tools/setup_minutes.py" | sed 's/^/  /'

if [ "$SERVICE" = 1 ]; then
step "Background service ($LABEL)"
mkdir -p "$APP/state" "$HOME/Library/LaunchAgents"
"$PY" - "$PLIST" "$LABEL" "$PY" "$APP" "$MINUTES_APP" "$(dirname "$claude_bin")" <<'PY'
import os, plistlib, sys
plist, label, py, app, minutes_app, claude_dir = sys.argv[1:]
plistlib.dump({
    "Label": label,
    "ProgramArguments": [py, os.path.join(app, "bin", "autopilot.py")],
    "RunAtLoad": True,
    "KeepAlive": True,
    "ProcessType": "Interactive",
    "StandardOutPath": os.path.join(app, "state", "autopilot.out"),
    "StandardErrorPath": os.path.join(app, "state", "autopilot.err"),
    "EnvironmentVariables": {
        "HOME": os.environ["HOME"], "USER": os.environ.get("USER", ""),
        "LANG": os.environ.get("LANG") or "en_US.UTF-8",
        "MINUTES_APP": minutes_app,
        "PATH": f"{claude_dir}:{os.environ['HOME']}/.local/bin:/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin:/usr/sbin:/sbin",
    },
}, open(plist, "wb"))
PY
"$APP/tools/launchd-start.sh" "$LABEL" "$PLIST" || die "could not start $LABEL (launchctl bootstrap failed)"
echo "  running; log: $APP/state/autopilot.log"
else
  step "Background service: skipped (--no-service)"
fi

step "The model that writes notes and answers"
if grep -Eq '^provider *= *"openai"' "$CONFIG" 2>/dev/null; then
  echo "  ok: another model service is chosen ([llm] in the config); Claude Code is not needed"
elif [ ! -x "$claude_bin" ]; then
  warn "Claude Code is not installed: https://claude.com/claude-code — then run  claude auth login"
elif auth="$("$claude_bin" auth status 2>/dev/null || true)" && grep -Eq '"loggedIn": ?true' <<< "$auth"; then
  echo "  ok: signed in (notes and explanations use your own account)"
else
  warn "not signed in: run  claude auth login  with your own Claude account"
fi

step "Remaining manual steps"
cat <<EOF
  1. Permissions (System Settings → Privacy & Security), allow once:
     - Microphone and Screen & System Audio Recording: Minutes
     - Screen & System Audio Recording: switch on "claude-meeting Screen" (listed as "MeetingCopilotScreen" on
       some Macs; added switched off)
       (the installer asked macOS to add it; if it is missing, use + and pick $SCREEN_APP)
     open "x-apple.systempreferences:com.apple.preference.security?Privacy_ScreenCapture"
  2. Voiceprint, so the assistant knows which microphone speech is you:
     after your first recorded meeting, run
       "$PY" "$APP/tools/enroll_voiceprint.py" ~/meetings/<that meeting without extension>
     or record 30-60 s of yourself talking into a .wav and pass that file instead.
  3. Join any call: recording starts by itself and the live notes window opens.
EOF
