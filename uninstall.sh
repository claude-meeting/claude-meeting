#!/bin/bash
# meeting-copilot uninstaller.
#
#   ./uninstall.sh            stop the background services and remove them
#   ./uninstall.sh --purge    also move the Python environment, models, built tools,
#                             runtime state and your config to the Trash
#
# Meetings (~/meetings) are never touched; delete them yourself if you want to.
# Nothing is deleted outright: removed files go to the Trash so they can be restored.
set -uo pipefail

ROOT="$(cd "$(dirname "$0")" && pwd)"
APP="$ROOT/app"
PURGE=0
case "${1:-}" in
  "") ;;
  --purge) PURGE=1 ;;
  *) echo "unknown option: $1 (only --purge)" >&2; exit 2 ;;
esac
SUPPORT="$HOME/Library/Application Support/Meeting Copilot"
TRASH="$HOME/.Trash/meeting-copilot-uninstall-$(date +%Y%m%d-%H%M%S)"

trash() {
  for p in "$@"; do
    [ -e "$p" ] || [ -L "$p" ] || continue
    # Keep the path relative to HOME inside the Trash folder, so two items with the same name
    # (~/.config/meeting-copilot and ~/Library/Caches/meeting-copilot) do not collide.
    case "$p" in "$HOME"/*) rel="${p#"$HOME"/}" ;; *) rel="${p#/}" ;; esac
    mkdir -p "$TRASH/$(dirname "$rel")"
    mv "$p" "$TRASH/$rel" && echo "  moved to Trash: $p"
  done
}

echo "==> Stopping services"
PLIST="$HOME/Library/LaunchAgents/com.meeting-copilot.autopilot.plist"
if [ -f "$PLIST" ] && grep -qF "$APP/bin/autopilot.py" "$PLIST"; then
  # Only the service that runs from this copy: another install (or another HOME) keeps its own.
  for label in com.meeting-copilot.autopilot com.meeting-copilot.capture com.meeting-copilot.screen; do
    launchctl bootout "gui/$(id -u)/$label" 2>/dev/null && echo "  stopped $label"
  done
  trash "$PLIST"
else
  echo "  no background service registered for this copy ($APP); none stopped"
fi

# A `minutes` command on PATH that points at our wrapper would break once we are gone.
for link in /opt/homebrew/bin/minutes /usr/local/bin/minutes; do
  if [ -L "$link" ] && [ "$(readlink -f "$link")" = "$APP/bin/minutes" ]; then
    rm "$link" && echo "  removed link $link (the Minutes app itself stays installed)"
  fi
done

if [ "$PURGE" = 1 ]; then
  echo "==> Purging installed files"
  # Minutes' settings first, while our Python environment is still here to do it properly.
  MC="$HOME/.config/minutes/config.toml"
  if [ -x "$APP/asr-venv/bin/python" ] && [ -f "$APP/tools/setup_minutes.py" ]; then
    "$APP/asr-venv/bin/python" "$APP/tools/setup_minutes.py" --undo || echo "  ! could not restore the Minutes settings"
  elif [ -f "$MC" ]; then
    sed -i '' 's/^keep_after_summary = true/keep_after_summary = false/' "$MC"
  fi
  trash "$APP/asr-venv" "$APP/models" "$APP/state" "$APP/work" \
        "$APP/bin/mic-users" "$APP/bin/screen-ocr" "$APP/bin/frames-to-video" "$APP/bin/MeetingCopilotScreen.app" \
        "$HOME/.config/meeting-copilot" "$HOME/Library/Caches/meeting-copilot" \
        "$SUPPORT/enroll.wav"
  # The speech models the installer downloaded into the Hugging Face cache (several GB). Other
  # cached models stay: they may belong to something else.
  HF="${HF_HOME:-$HOME/.cache/huggingface}/hub"
  for d in "$HF"/models--*TEA-ASR* "$HF"/models--*Qwen3-ASR* "$HF"/models--*parakeet* "$HF"/models--*Parakeet*; do
    [ -e "$d" ] && trash "$d"
  done
  # The engine the Mac app installed (this script may be running from inside it: trash it last).
  case "$ROOT" in "$SUPPORT"/*) ;; *) trash "$SUPPORT/engine" ;; esac
  # The signing keychain lived inside that folder: drop it from the keychain search list (the file
  # itself is in the Trash with the rest).
  keep=(); removed=0
  while IFS= read -r line; do
    line="${line#"${line%%[![:space:]]*}"}"; line="${line%\"}"; line="${line#\"}"
    [ -z "$line" ] && continue
    if [ "${line##*/}" = "signing.keychain-db" ]; then removed=1; else keep+=("$line"); fi
  done < <(security list-keychains -d user)
  # Rewrite the list only when ours was on it and something stays: never leave it empty.
  if [ "$removed" = 1 ] && [ "${#keep[@]}" -gt 0 ]; then security list-keychains -d user -s "${keep[@]}"; fi
fi

echo "==> Done"
echo "  Your meetings in ~/meetings are untouched."
[ "$PURGE" = 1 ] || echo "  Config, models and the Python environment are kept; run with --purge to remove them."
echo "  Still to do by hand, if you used the Mac app:"
echo "   - move claude-meeting.app out of Applications (or ~/Applications)"
echo "   - System Settings > General > Login Items & Extensions: remove claude-meeting"
case "$ROOT" in "$SUPPORT"/*) [ "$PURGE" = 1 ] && echo "   - this folder itself: \"$SUPPORT\"" ;; esac
echo "  Kept: uv's own Python (~/.local/share/uv, ~/.cache/uv) and Minutes with its models (~/.minutes);"
echo "  remove them yourself if you no longer want them."
echo "  Permissions you granted (Screen Recording for \"claude-meeting Screen\") can be removed in System Settings."
