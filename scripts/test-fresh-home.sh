#!/bin/bash
# Live check: install the engine bundled in the built app into an empty HOME with a system-only PATH,
# the way another user's Mac sees it; run the installer again (idempotent); purge; verify nothing of the
# real installation was touched. Needs app/macos/build/claude-meeting.app (build.sh) and network for
# anything not in the shared caches.
#
#   scripts/test-fresh-home.sh [--keep]     --keep leaves the scratch HOME for inspection
set -uo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
ENGINE_SRC="$ROOT/app/macos/build/claude-meeting.app/Contents/Resources/engine"
[ -d "$ENGINE_SRC" ] || { echo "no built app at $ENGINE_SRC: run app/macos/build.sh first" >&2; exit 1; }
KEEP=0; [ "${1:-}" = "--keep" ] && KEEP=1

FH="$(mktemp -d "${TMPDIR:-/tmp}/mc-fresh-home.XXXXXX")"
REAL_HOME="$HOME"
LABEL=com.meeting-copilot.autopilot
service_before="$(launchctl print "gui/$(id -u)/$LABEL" 2>/dev/null | grep -c 'state = running')"

# A scratch Hugging Face cache: the purge below trashes the speech models it finds there, and the real
# cache must stay. The models are large (tens of GB): clone them (APFS copy-on-write, no extra space),
# falling back to a plain copy on other file systems.
export HOME="$FH"
export PATH=/usr/bin:/bin:/usr/sbin:/sbin
export UV_CACHE_DIR="$REAL_HOME/.cache/uv" UV_PYTHON_INSTALL_DIR="$REAL_HOME/.local/share/uv/python"
export HF_HOME="$FH/hf"
mkdir -p "$HF_HOME/hub"
if [ -d "$REAL_HOME/.cache/huggingface/hub" ]; then
  cp -cR "$REAL_HOME/.cache/huggingface/hub/." "$HF_HOME/hub/" 2>/dev/null \
    || cp -R "$REAL_HOME/.cache/huggingface/hub/." "$HF_HOME/hub/"
fi
DEST="$FH/Library/Application Support/Meeting Copilot/engine"
failed=0
step() { printf '\n===== %s =====\n' "$*"; }
check() { if "$@"; then echo "  ok"; else echo "  !! FAILED: $*"; failed=1; fi; }

step "sync-engine"
"$ENGINE_SRC/sync-engine.sh" "$DEST" fresh-home-test || { echo "sync failed"; exit 1; }

step "prerequisites without Minutes (--get-claude --only-prerequisites)"
mkdir -p "$FH/.config/meeting-copilot"
printf '[paths]\nminutes_app = "/nonexistent/Minutes.app"\n' > "$FH/.config/meeting-copilot/config.toml"
"$DEST/install.sh" --get-claude --only-prerequisites; check test $? -eq 0
check test -x "$FH/.local/bin/claude"

step "full install (no service)"
rm -f "$FH/.config/meeting-copilot/config.toml"
"$DEST/install.sh" --no-service --name "Test User"; check test $? -eq 0
check test -x "$DEST/app/asr-venv/bin/python"
check grep -q 'name = "Test User"' "$FH/.config/meeting-copilot/config.toml"
check test -s "$DEST/app/models/speaker/"*.onnx

check test -s "$DEST/app/state/install.log"
check test ! -e "$ENGINE_SRC/app/state"                      # nothing written into the app bundle

step "Minutes is ready: speech model, VAD, diarization"
MINUTES_BIN="/Applications/Minutes.app/Contents/MacOS/minutes"
health="$("$MINUTES_BIN" health --json 2>/dev/null)"
for label in "Speech model" "VAD model" "Speaker diarization"; do
  check "$DEST/app/asr-venv/bin/python" -c 'import json, sys
items = {i["label"]: i["state"] for i in json.loads(sys.argv[1])["data"]["items"]}
sys.exit(0 if items.get(sys.argv[2]) == "ready" else 1)' "$health" "$label"
done

step "second sync, as an app update does, with the bundled (read-only) uv"
check test ! -w "$ENGINE_SRC/bin/uv"                         # the bundle's uv is read-only, as shipped
"$ENGINE_SRC/sync-engine.sh" "$DEST" fresh-home-test-2; check test $? -eq 0
check test -x "$DEST/bin/uv"

step "re-run (idempotent)"
"$DEST/install.sh" --no-service > "$FH/rerun.log" 2>&1; check test $? -eq 0

step "uninstall --purge"
"$DEST/uninstall.sh" --purge; check test $? -eq 0
check test ! -d "$DEST/app/asr-venv"
check test ! -d "$FH/.config/meeting-copilot"

step "the real installation is untouched"
service_after="$(launchctl print "gui/$(id -u)/$LABEL" 2>/dev/null | grep -c 'state = running')"
check test "$service_before" = "$service_after"
check test -d "$REAL_HOME/.cache/huggingface/hub"
for m in "$REAL_HOME/.cache/huggingface/hub"/models--*; do [ -e "$m" ] || continue; check test -d "$m"; done

if [ "$KEEP" = 1 ]; then echo "kept $FH"; else rm -rf "$FH"; fi
[ "$failed" = 0 ] && { step "fresh-home install OK"; exit 0; } || { step "fresh-home install FAILED"; exit 1; }
