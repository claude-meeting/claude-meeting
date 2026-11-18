#!/bin/bash
# The Info.plist of the screen-capture helper (MeetingCopilotScreen.app), the one place it is defined.
#
#   screen-helper-plist.sh <helper.app> write     write it (install.sh, after building; it signs, then registers)
#   screen-helper-plist.sh <helper.app> refresh   an installed helper whose plist differs (an older install: the
#                                                 old name, other keys) gets this one and is signed again
#   screen-helper-plist.sh <helper.app> check     exit 1 if its plist differs (build.sh, for the prebuilt helper)
#   screen-helper-plist.sh <helper.app> running   exit 0 while the capture job runs (a meeting is being captured)
#   screen-helper-plist.sh <helper.app> register  tell LaunchServices its current name (after signing)
#
# A change to the plist changes the bundle's signature. Screen Recording stays granted only when it is signed
# again with the same local identity (codesign.sh --has-identity), so refresh leaves the helper alone while it is
# capturing and unless that identity is there; and it says so if the signature's identity changed anyway.
set -uo pipefail
HELPER="${1:?usage: screen-helper-plist.sh <helper.app> write|refresh|check|running|register}"
MODE="${2:-refresh}"
TOOLS="$(cd "$(dirname "$0")" && pwd)"
NAME="claude-meeting Screen"            # its name (Screen & System Audio Recording may list the folder's,
                                        # MeetingCopilotScreen, instead)
PLIST="$HELPER/Contents/Info.plist"
LSREGISTER="${LSREGISTER:-/System/Library/Frameworks/CoreServices.framework/Frameworks/LaunchServices.framework/Support/lsregister}"

render() {
  cat <<EOF
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>CFBundleIdentifier</key><string>com.meeting-copilot.screen-capture</string>
  <key>CFBundleName</key><string>$NAME</string>
  <key>CFBundleDisplayName</key><string>$NAME</string>
  <key>CFBundleExecutable</key><string>screen-capture</string>
  <key>CFBundlePackageType</key><string>APPL</string>
  <key>CFBundleVersion</key><string>1</string>
  <key>CFBundleShortVersionString</key><string>1.0</string>
  <key>LSUIElement</key><true/>
  <key>LSMinimumSystemVersion</key><string>15.0</string>
</dict>
</plist>
EOF
}

# Same content, whatever the formatting (older installs wrote it with Python's plistlib).
current() { [ -f "$PLIST" ] && [ "$(plutil -convert json -o - "$PLIST" 2>/dev/null)" = "$(render | plutil -convert json -o - - 2>/dev/null)" ]; }

# The capture job, as the engine checks it (ScreenCap.LABEL, ScreenCap.running): by its launchd label, not by a
# process path. A bats test keeps this label equal to the engine's.
LABEL="com.meeting-copilot.screen"
# Whole output first: under pipefail, `launchctl print | grep -q` fails when grep stops early and launchctl gets SIGPIPE.
running() { grep -q "state = running" <<< "$(launchctl print "gui/$(id -u)/$LABEL" 2>/dev/null)"; }
designated() { codesign -dr - "$HELPER" 2>&1 | grep designated; }

register() { "$LSREGISTER" -f "$HELPER" >/dev/null 2>&1 || true; }   # System Settings shows the name it has

case "$MODE" in
  write)
    mkdir -p "$HELPER/Contents" && render > "$PLIST"
    ;;
  check)
    current
    ;;
  running)
    running
    ;;
  register)
    register
    ;;
  refresh)
    [ -x "$HELPER/Contents/MacOS/screen-capture" ] || exit 0
    current && exit 0
    if running; then
      echo "  screen-capture is capturing a meeting: its Info.plist is updated on the next install"
      exit 0
    fi
    if ! "$TOOLS/codesign.sh" --has-identity; then
      echo "  ! screen-capture's Info.plist is out of date but left as is: without the local signing identity," \
           "signing it again would reset its Screen Recording permission"
      exit 0
    fi
    before="$(designated)"
    render > "$PLIST"
    "$TOOLS/codesign.sh" "$HELPER"
    register
    after="$(designated)"
    if [ -z "$after" ]; then
      echo "  ! screen-capture's Info.plist is updated but signing it failed: run install.sh again, then switch on" \
           "\"$NAME\" under Screen & System Audio Recording"
    elif [ "$after" = "$before" ]; then
      echo "  updated: screen-capture's Info.plist (listed as \"$NAME\"), signed again with the same identity"
    else
      echo "  updated: screen-capture's Info.plist, but its signing identity changed: switch on \"$NAME\"" \
           "again under Screen & System Audio Recording"
    fi
    ;;
  *)
    echo "usage: screen-helper-plist.sh <helper.app> write|refresh|check|running|register" >&2; exit 2
    ;;
esac
