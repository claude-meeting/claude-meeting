#!/bin/bash
# Release check on a truly fresh Mac: a throwaway macOS 15 virtual machine (Tart) with nothing installed —
# no Homebrew, no Xcode tools, no Minutes, no Claude Code, English listed before Chinese as on many
# bilingual Macs. The engine bundled in the built .dmg is installed the way the setup guide does it, then checked.
# This is what the fresh-HOME test cannot do: that one still sees this Mac's macOS, tools and settings.
#
#   scripts/test-vm.sh [dmg]          headless install checks, then the VM is deleted
#   scripts/test-vm.sh --gui [dmg]    also opens the VM's screen for the setup guide by hand
#   scripts/test-vm.sh --keep [dmg]   keep the VM afterwards (tart run mc-fresh-test)
#
# dmg: the disk image to install, by default the newest app/macos/build/claude-meeting-*.dmg.
# Needs: tart and sshpass (brew install cirruslabs/cli/tart hudochenkov/sshpass/sshpass), ~40 GB free disk,
# 8 GB of RAM for the VM. The first run downloads the macOS image (~25 GB) once.
set -uo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
IMAGE="${MC_VM_IMAGE:-ghcr.io/cirruslabs/macos-sequoia-vanilla:latest}"   # macOS 15, nothing preinstalled
VM=mc-fresh-test
GUI=0; KEEP=0; DMG=""
for arg in "$@"; do
  case "$arg" in
    --gui) GUI=1 ;;
    --keep) KEEP=1 ;;
    -h|--help) sed -n '2,14p' "$0"; exit 0 ;;
    *) DMG="$arg" ;;
  esac
done
DMG="${DMG:-$(ls -t "$ROOT"/app/macos/build/claude-meeting-*.dmg 2>/dev/null | sed -n 1p)}"
[ -f "$DMG" ] || { echo "no .dmg: build one with app/macos/build.sh --dmg or pass its path" >&2; exit 1; }
command -v tart >/dev/null || { echo "tart is not installed: brew install cirruslabs/cli/tart" >&2; exit 1; }
command -v sshpass >/dev/null || { echo "sshpass is not installed: brew install hudochenkov/sshpass/sshpass" >&2; exit 1; }

failed=0
step() { printf '\n===== %s =====\n' "$*"; }
check() { if "$@"; then echo "  ok"; else echo "  !! FAILED: $*"; failed=1; fi; }
# Password only: with keys loaded in an agent, ssh offers them first and the server closes the connection
# ("Too many authentication failures") before the password is tried.
# One shared connection for every command: the guest's sshd sometimes refuses a correct password when
# many logins arrive in a row ("Permission denied, please try again"), which failed checks at random.
SSH_OPTS=(-o StrictHostKeyChecking=no -o UserKnownHostsFile=/dev/null -o LogLevel=ERROR
          -o PubkeyAuthentication=no -o PreferredAuthentications=password
          -o ControlMaster=auto -o "ControlPath=/tmp/mc-vm-%C" -o ControlPersist=900)   # short: socket paths max 104 bytes
vm() { sshpass -p admin ssh "${SSH_OPTS[@]}" "admin@$IP" "$@"; }

cleanup() {
  [ -n "${IP:-}" ] && ssh "${SSH_OPTS[@]}" -O exit "admin@$IP" >/dev/null 2>&1
  tart stop "$VM" >/dev/null 2>&1
  [ "$KEEP" = 1 ] || tart delete "$VM" >/dev/null 2>&1
}
trap cleanup EXIT

step "virtual machine ($IMAGE)"
tart delete "$VM" >/dev/null 2>&1
tart clone "$IMAGE" "$VM" || { echo "could not get $IMAGE" >&2; exit 1; }
tart set "$VM" --cpu 4 --memory 8192 --disk-size 90
SHARE="$(cd "$(dirname "$DMG")" && pwd)"
if [ "$GUI" = 1 ]; then
  tart run "$VM" --dir="dist:$SHARE:ro" &
else
  tart run "$VM" --no-graphics --dir="dist:$SHARE:ro" &
fi
IP=""; last=""
for _ in $(seq 1 90); do
  IP="$(tart ip "$VM" 2>/dev/null)"
  if [ -n "$IP" ]; then
    last="$(vm true 2>&1)" && break
  fi
  IP=""; sleep 2
done
if [ -z "$IP" ]; then
  echo "the VM did not answer over SSH: ${last:-no address}" >&2
  if [[ "$last" == *"No route to host"* ]]; then
    # macOS blocks connections to the VM's local network for apps without the Local Network permission
    # (the terminal or the app this script runs in), and reports it as "No route to host".
    echo "allow the app running this script under System Settings > Privacy & Security > Local Network" >&2
  fi
  exit 1
fi
echo "  VM at $IP: $(vm sw_vers -productVersion)"

step "a fresh Mac: English first, Chinese second, no developer tools"
vm "defaults write -g AppleLanguages -array en-US zh-Hant-TW"
# The "vanilla" image still ships the Command Line Tools; a new Mac has none (git and python3 are stubs
# that offer to install them). Remove them so nothing of ours can lean on them unnoticed.
vm "echo admin | sudo -S rm -rf /Library/Developer/CommandLineTools 2>/dev/null; echo admin | sudo -S xcode-select --reset 2>/dev/null"
check vm '! test -d /Library/Developer/CommandLineTools && ! ls -d /Applications/Xcode*.app >/dev/null 2>&1'
check vm '! command -v brew >/dev/null 2>&1'

step "install the app from the disk image"
DMG_NAME="$(basename "$DMG")"
vm "set -e; hdiutil attach -nobrowse -quiet \"/Volumes/My Shared Files/dist/$DMG_NAME\" -mountpoint /tmp/mc-dmg
    cp -R '/tmp/mc-dmg/claude-meeting.app' /Applications/; hdiutil detach -quiet /tmp/mc-dmg"
check vm 'test -x "/Applications/claude-meeting.app/Contents/MacOS/MeetingCopilot"'

step "Gatekeeper on, the app downloaded by a browser: blocked until Open Anyway (the README's step)"
# The vanilla image ships with Gatekeeper off, which hid this step from every earlier run. A browser
# download marks the .dmg, and Finder copies that mark onto every file it drags out of it.
vm "echo admin | sudo -S spctl --global-enable 2>/dev/null"
check vm 'spctl --status | grep -q "assessments enabled"'
# Every file gets the mark, as Finder gives it to what it copies out of a downloaded disk image (a few
# read-only files, such as the bundled uv, refuse it here).
vm "xattr -rw com.apple.quarantine \"0083;\$(printf %x \$(date +%s));Safari;\$(uuidgen)\" /Applications/claude-meeting.app 2>/dev/null; true"
check vm "xattr -p com.apple.quarantine /Applications/claude-meeting.app >/dev/null && xattr -p com.apple.quarantine /Applications/claude-meeting.app/Contents/Resources/engine/install.sh >/dev/null"
# Not notarized, so Gatekeeper rejects it whatever the mark: what makes the README's Open Anyway step needed.
check vm '! spctl -a /Applications/claude-meeting.app 2>/dev/null'
# A person now clicks Open Anyway, which nothing here can do (that approval keeps the files' marks; checking the
# setup guide after it is the --gui step below). Without it macOS holds the app's scripts (sync-engine.sh can wait
# for hours), so they lose the mark here; the engine's own files keep it, so the next steps
# still check that the copy in Application Support comes out without it.
vm "xattr -dr com.apple.quarantine /Applications/claude-meeting.app 2>/dev/null; true"
vm "xattr -rw com.apple.quarantine \"0083;\$(printf %x \$(date +%s));Safari;\$(uuidgen)\" /Applications/claude-meeting.app/Contents/Resources/engine/app 2>/dev/null; true"
check vm '! xattr -p com.apple.quarantine /Applications/claude-meeting.app/Contents/Resources/engine/sync-engine.sh 2>/dev/null && xattr -p com.apple.quarantine /Applications/claude-meeting.app/Contents/Resources/engine/app/lib/config.py >/dev/null'
BUNDLED="/Applications/claude-meeting.app/Contents/Resources/engine"
ENGINE='$HOME/Library/Application Support/Meeting Copilot/engine'
VERSION="$(vm "/usr/libexec/PlistBuddy -c 'Print MCEngineVersion' '/Applications/claude-meeting.app/Contents/Info.plist'")"

step "the setup guide's steps, in its order"
# 1. copy the engine out of the app; 2. Minutes and Claude Code (the guide's two buttons);
# 3. speech recognition (Install); then the checks the guide shows.
check vm "\"$BUNDLED/sync-engine.sh\" \"$ENGINE\" '$VERSION'"
check vm "\"$ENGINE/install.sh\" --get-minutes --get-claude --only-prerequisites"
check vm 'test -x /Applications/Minutes.app/Contents/MacOS/minutes && test -x "$HOME/.local/bin/claude"'
check vm "\"$ENGINE/install.sh\" --no-service --name 'VM Test' 2>&1 | tail -20"
check vm "test -s \"$ENGINE/app/state/install.log\""
check vm "test -s \"$ENGINE/app/models/speaker/\"*.onnx"
check vm "! xattr -lr \"$ENGINE\" 2>/dev/null | grep -q com.apple.quarantine"   # copied out of a marked app

step "Minutes has its speech model, VAD and diarization"
for label in "Speech model" "VAD model" "Speaker diarization"; do
  check vm "/Applications/Minutes.app/Contents/MacOS/minutes health --json | \"$ENGINE/app/asr-venv/bin/python\" -c '
import json, sys
items = {i[\"label\"]: i[\"state\"] for i in json.load(sys.stdin)[\"data\"][\"items\"]}
sys.exit(0 if items.get(sys.argv[1]) == \"ready\" else 1)' '$label'"
done

step "an app update syncs the engine a second time (read-only bundled uv)"
check vm "\"$BUNDLED/sync-engine.sh\" \"$ENGINE\" '$VERSION' && \"$ENGINE/install.sh\" --no-service > /tmp/rerun.log 2>&1"

step "voiceprint from normal, continuous speech"
check vm "say -o /tmp/me.aiff \"\$(for i in 1 2 3 4 5 6; do printf 'and then we talked about the plan for the next release '; done)\" && afconvert -f WAVE -d LEI16@16000 -c 1 /tmp/me.aiff /tmp/me.wav && \"$ENGINE/app/asr-venv/bin/python\" \"$ENGINE/app/tools/enroll_voiceprint.py\" /tmp/me.wav --out /tmp/vp.json"

step "uninstall --purge leaves nothing of ours"
check vm "\"$ENGINE/uninstall.sh\" --purge > /tmp/uninstall.log 2>&1"
check vm 'test ! -d "$HOME/.config/meeting-copilot"'

if [ "$GUI" = 1 ]; then
  cat <<'EOF'

===== by hand, in the VM's window (user admin, password admin) =====
Reinstall from scratch first:  ssh in and run  rm -rf "$HOME/Library/Application Support/Meeting Copilot"
1. Open claude-meeting from Applications; System Settings > Privacy & Security > Open Anyway.
2. In the setup guide, before installing Minutes: the speech-recognition Install button is disabled, its
   tooltip says to install Minutes first.
3. Install Minutes, Claude Code (sign in), then speech recognition. Quit the app halfway through the model
   download and reopen it: the install finishes anyway (app/state/install.log ends with the next steps).
4. Record the voiceprint talking normally for 30 s: it succeeds.
5. During the recording, no "auto start" appears in ~/Library/Application Support/Meeting Copilot/engine/app/state/autopilot.log.
6. Settings: change the language. The guide shows the speech model as missing, with Install.
Press Enter here when done.
EOF
  read -r _
fi

[ "$failed" = 0 ] && { step "fresh VM OK"; exit 0; } || { step "fresh VM FAILED"; exit 1; }
