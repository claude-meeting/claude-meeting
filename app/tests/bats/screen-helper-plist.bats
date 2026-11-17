#!/usr/bin/env bats
# app/tools/screen-helper-plist.sh: writes the screen helper's Info.plist, and brings an older install's up to
# date, signing it again only when the grant can survive it.

load common

setup() {
  common_setup
  use_fake codesign launchctl
  TOOLS="$BATS_TEST_TMPDIR/tools"
  mkdir -p "$TOOLS"
  cp "$REPO/app/tools/screen-helper-plist.sh" "$TOOLS/"
  # A stand-in for codesign.sh: --has-identity answers $FAKE_HAS_IDENTITY (0 = yes); otherwise records what it signs.
  printf '#!/bin/bash\n[ "$1" = --has-identity ] && exit "${FAKE_HAS_IDENTITY:-0}"\necho "$@" >> "%s/signed.log"\n' \
    "$BATS_TEST_TMPDIR" > "$TOOLS/codesign.sh"
  chmod +x "$TOOLS/codesign.sh"
  # Never the real LaunchServices database: the bundle here has the real helper's identifier.
  printf '#!/bin/bash\necho "$@" >> "%s/lsregister.log"\n' "$BATS_TEST_TMPDIR" > "$FAKE_BIN/lsregister"
  chmod +x "$FAKE_BIN/lsregister"
  export LSREGISTER="$FAKE_BIN/lsregister"
  HELPER="$BATS_TEST_TMPDIR/MeetingCopilotScreen.app"
  mkdir -p "$HELPER/Contents/MacOS"
  printf '#!/bin/sh\n' > "$HELPER/Contents/MacOS/screen-capture"; chmod +x "$HELPER/Contents/MacOS/screen-capture"
}

key() { /usr/libexec/PlistBuddy -c "Print $1" "$HELPER/Contents/Info.plist"; }
old_name() {
  "$TOOLS/screen-helper-plist.sh" "$HELPER" write
  /usr/libexec/PlistBuddy -c "Set CFBundleName Meeting Copilot Screen" "$HELPER/Contents/Info.plist"
}

@test "write: the plist System Settings reads, with the helper's identifier" {
  run "$TOOLS/screen-helper-plist.sh" "$HELPER" write
  [ "$status" -eq 0 ]
  [ "$(key CFBundleName)" = "claude-meeting Screen" ]
  [ "$(key CFBundleIdentifier)" = "com.meeting-copilot.screen-capture" ]   # the grant is tied to it
  [ "$(key LSMinimumSystemVersion)" = "15.0" ]
  [ ! -f "$BATS_TEST_TMPDIR/lsregister.log" ]                 # registered after install.sh signs it
  run "$TOOLS/screen-helper-plist.sh" "$HELPER" register
  grep -q -- "-f $HELPER" "$BATS_TEST_TMPDIR/lsregister.log"
}

@test "refresh: an old name is replaced, the helper signed again and registered" {
  old_name
  printf '#!/bin/bash\necho "designated => identifier x and certificate leaf = H\\"1\\""\n' > "$FAKE_BIN/codesign"   # same before and after
  rm -f "$BATS_TEST_TMPDIR/lsregister.log"
  run "$TOOLS/screen-helper-plist.sh" "$HELPER" refresh
  [ "$status" -eq 0 ]
  [[ "$output" == *"signed again with the same identity"* ]]
  [ "$(key CFBundleName)" = "claude-meeting Screen" ]
  [ "$(cat "$BATS_TEST_TMPDIR/signed.log")" = "$HELPER" ]
  [ -f "$BATS_TEST_TMPDIR/lsregister.log" ]
}

@test "refresh: any other key that differs counts too, not only the name" {
  "$TOOLS/screen-helper-plist.sh" "$HELPER" write
  /usr/libexec/PlistBuddy -c "Set LSMinimumSystemVersion 14.0" "$HELPER/Contents/Info.plist"
  run "$TOOLS/screen-helper-plist.sh" "$HELPER" refresh
  [ "$(key LSMinimumSystemVersion)" = "15.0" ]
  [ -f "$BATS_TEST_TMPDIR/signed.log" ]
}

@test "refresh: the same content as Python's plistlib wrote it (older installs) is left alone, unsigned" {
  # plistlib.dump's XML: tabs, its own key order.
  cat > "$HELPER/Contents/Info.plist" <<'EOF'
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
	<key>CFBundleDisplayName</key>
	<string>claude-meeting Screen</string>
	<key>CFBundleExecutable</key>
	<string>screen-capture</string>
	<key>CFBundleIdentifier</key>
	<string>com.meeting-copilot.screen-capture</string>
	<key>CFBundleName</key>
	<string>claude-meeting Screen</string>
	<key>CFBundlePackageType</key>
	<string>APPL</string>
	<key>CFBundleShortVersionString</key>
	<string>1.0</string>
	<key>CFBundleVersion</key>
	<string>1</string>
	<key>LSMinimumSystemVersion</key>
	<string>15.0</string>
	<key>LSUIElement</key>
	<true/>
</dict>
</plist>
EOF
  run "$TOOLS/screen-helper-plist.sh" "$HELPER" refresh
  [ "$status" -eq 0 ]
  [ -z "$output" ]
  [ ! -f "$BATS_TEST_TMPDIR/signed.log" ]
  run "$TOOLS/screen-helper-plist.sh" "$HELPER" check
  [ "$status" -eq 0 ]
}

@test "refresh: while the capture job runs, nothing changes" {
  old_name
  # Running only under the capture job's own label, as the engine names it.
  printf '#!/bin/bash\n[ "$1 $2" = "print gui/%s/com.meeting-copilot.screen" ] && echo "	state = running"\nexit 0\n' \
    "$(id -u)" > "$FAKE_BIN/launchctl"
  run "$TOOLS/screen-helper-plist.sh" "$HELPER" refresh
  [ "$status" -eq 0 ]
  [[ "$output" == *"capturing a meeting"* ]]
  [ "$(key CFBundleName)" = "Meeting Copilot Screen" ]
  [ ! -f "$BATS_TEST_TMPDIR/signed.log" ]
  run "$TOOLS/screen-helper-plist.sh" "$HELPER" running
  [ "$status" -eq 0 ]
}

@test "refresh: without the signing identity the old plist stays, so the grant is not lost" {
  old_name
  export FAKE_HAS_IDENTITY=1
  run "$TOOLS/screen-helper-plist.sh" "$HELPER" refresh
  [ "$status" -eq 0 ]
  [[ "$output" == *"left as is"* ]]
  [ "$(key CFBundleName)" = "Meeting Copilot Screen" ]
  [ ! -f "$BATS_TEST_TMPDIR/signed.log" ]
}

@test "the capture job's label is the engine's" {
  engine="$(sed -n 's/^ *LABEL = "\(com\.meeting-copilot\.screen\)".*/\1/p' "$REPO/app/bin/autopilot.py")"
  [ -n "$engine" ]
  grep -q "^LABEL=\"$engine\"" "$REPO/app/tools/screen-helper-plist.sh"
}

@test "refresh: a signature that is gone after signing is reported, not taken for the same identity" {
  old_name
  # codesign -dr - names a requirement before signing, nothing after.
  printf '#!/bin/bash\n[ -f "%s/dr" ] && exit 1; touch "%s/dr"; echo "designated => identifier x"\n' \
    "$BATS_TEST_TMPDIR" "$BATS_TEST_TMPDIR" > "$FAKE_BIN/codesign"
  run "$TOOLS/screen-helper-plist.sh" "$HELPER" refresh
  [[ "$output" == *"signing it failed"* ]]
}

@test "refresh: if the signature's identity changed anyway, it says to grant the permission again" {
  old_name
  # codesign -dr - reports a different requirement after signing.
  printf '#!/bin/bash\nn=$(cat "%s/dr" 2>/dev/null || echo 0); echo $((n+1)) > "%s/dr"\necho "designated => identifier x and certificate leaf = H\\"$n\\""\n' \
    "$BATS_TEST_TMPDIR" "$BATS_TEST_TMPDIR" > "$FAKE_BIN/codesign"
  run "$TOOLS/screen-helper-plist.sh" "$HELPER" refresh
  [[ "$output" == *"switch on \"claude-meeting Screen\" again"* ]]
}

@test "check: a differing plist fails, so build.sh refuses to package it" {
  old_name
  run "$TOOLS/screen-helper-plist.sh" "$HELPER" check
  [ "$status" -eq 1 ]
}

@test "refresh: no helper built yet is not an error" {
  rm -rf "$HELPER"
  run "$TOOLS/screen-helper-plist.sh" "$HELPER" refresh
  [ "$status" -eq 0 ]
  [ -z "$output" ]
}
