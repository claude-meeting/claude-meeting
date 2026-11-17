#!/usr/bin/env bats
# install.sh: option handling and the --only-prerequisites path. The uv / model steps are never
# reached: every run here either stops at --only-prerequisites or dies before them.

load common

setup() {
  common_setup
  WORK="$BATS_TEST_TMPDIR/checkout"
  mkdir -p "$WORK"
  cp "$REPO/install.sh" "$WORK/install.sh"
  INSTALL="$WORK/install.sh"
  CONFIG="$HOME/.config/meeting-copilot/config.toml"      # the default config in the fake HOME
  mkdir -p "$(dirname "$CONFIG")"
  MINUTES="$BATS_TEST_TMPDIR/Minutes.app"
  make_minutes_app "$MINUTES"
  MISSING="$BATS_TEST_TMPDIR/NoSuchMinutes.app"             # Minutes absent: not the real /Applications one
}

# write_config <file> <minutes_app value as written in TOML>
write_config() { printf '[paths]\nminutes_app = %s\n' "$2" > "$1"; }

assert_prereqs_ok() {
  [ "$status" -eq 0 ]
  [[ "$output" == *"==> Checking the system"* ]]
  [ "$(last_line)" = "  ok" ]
  [[ "$output" != *"  ! "* ]]                 # no warning lines
  [[ "$output" != *"✗"* ]]                    # no die lines
  [[ "$output" != *"Python environment"* ]]   # never reached uv
}

@test "install: -h prints the usage comment, not the script body" {
  run bash "$INSTALL" -h
  [ "$status" -eq 0 ]
  [[ "$output" == *"./install.sh --only-prerequisites"* ]]
  [[ "$output" != *"set -euo pipefail"* ]]
}

@test "install: --help behaves like -h" {
  run bash "$INSTALL" --help
  [ "$status" -eq 0 ]
  [[ "$output" == *"install or update"* ]]
}

@test "install: unknown option exits 2" {
  run bash "$INSTALL" --bogus
  [ "$status" -eq 2 ]
  [[ "$output" == *"unknown option: --bogus"* ]]
}

@test "install: --only-prerequisites, claude present, Minutes present" {
  use_fake claude
  write_config "$CONFIG" "\"$MINUTES\""
  run bash "$INSTALL" --only-prerequisites
  assert_prereqs_ok
  not_logged "curl"
}

@test "install: keeps going when whoever reads its output goes away (the app quit)" {
  use_fake claude
  write_config "$CONFIG" "\"$MINUTES\""
  run bash -c 'bash "$1" --only-prerequisites | true; echo "exit=${PIPESTATUS[0]}"' _ "$INSTALL"
  [ "$status" -eq 0 ]
  [ "$output" = "exit=0" ]
}

@test "install: programs it starts keep the default SIGPIPE (only the output reader ignores it)" {
  use_fake claude
  write_config "$CONFIG" "\"$MINUTES\""
  # sw_vers runs after the output reader is set up. The fake writes into a pipe whose reader stops at once:
  # with the default SIGPIPE the writer just ends, with an inherited "ignore" it gets write errors.
  printf '#!/bin/bash\nfor i in $(seq 1 20000); do echo x; done 2>>"%s/child-epipe" | head -1 >/dev/null\nexec /usr/bin/sw_vers "$@"\n' \
    "$BATS_TEST_TMPDIR" > "$FAKE_BIN/sw_vers"
  chmod +x "$FAKE_BIN/sw_vers"
  # bats itself runs with SIGPIPE ignored: start install.sh with the default, as the app does.
  run "$REPO/app/asr-venv/bin/python" -c 'import os, signal, sys
signal.signal(signal.SIGPIPE, signal.SIG_DFL)
os.execvp("bash", ["bash", sys.argv[1], "--only-prerequisites"])' "$INSTALL"
  assert_prereqs_ok
  [ -e "$BATS_TEST_TMPDIR/child-epipe" ]
  ! grep -q "Broken pipe" "$BATS_TEST_TMPDIR/child-epipe"
}

@test "install: a stalled Minutes download is stopped after the time limit, with a warning" {
  local fake_minutes="$BATS_TEST_TMPDIR/Stall.app/Contents/MacOS/minutes"
  mkdir -p "$(dirname "$fake_minutes")"
  printf '#!/bin/bash\nsleep 30\n' > "$fake_minutes"
  chmod +x "$fake_minutes"
  # The helper as install.sh defines it, run on its own (a full install would reach uv and the models).
  sed -n '/^minutes_setup() {/,/^}/p' "$INSTALL" > "$BATS_TEST_TMPDIR/helper.sh"
  [ -s "$BATS_TEST_TMPDIR/helper.sh" ]
  run bash -c 'warn() { printf "  ! %s\n" "$*"; }
               PY="$1" MINUTES_APP="$2" MINUTES_SETUP_SECS=1
               source "$3"
               start=$SECONDS; minutes_setup "speech model"; echo "status=$? took=$((SECONDS - start))"' \
      _ "$REPO/app/asr-venv/bin/python" "$BATS_TEST_TMPDIR/Stall.app" "$BATS_TEST_TMPDIR/helper.sh"
  [[ "$output" == *"stopped after 1 s without finishing"* ]]
  [[ "$output" == *"could not download Minutes' speech model"* ]]
  [[ "$output" == *"status=0"* ]]              # a warning, not the end of the install
  [[ "$output" =~ took=[0-3]$ ]]
}

@test "install: --only-prerequisites, claude present, Minutes absent" {
  use_fake claude
  write_config "$CONFIG" "\"$MISSING\""
  run bash "$INSTALL" --only-prerequisites
  assert_prereqs_ok
  not_logged "curl"
}

@test "install: --only-prerequisites, claude absent, Minutes present" {
  write_config "$CONFIG" "\"$MINUTES\""
  run bash "$INSTALL" --only-prerequisites
  assert_prereqs_ok
  not_logged "curl"
}

@test "install: --only-prerequisites, claude absent, Minutes absent" {
  write_config "$CONFIG" "\"$MISSING\""
  run bash "$INSTALL" --only-prerequisites
  assert_prereqs_ok
  not_logged "curl"
}

@test "install: --only-prerequisites touches nothing real (quarantine flag via fake xattr only)" {
  write_config "$CONFIG" "\"$MINUTES\""
  run bash "$INSTALL" --only-prerequisites
  assert_prereqs_ok
  logged "xattr -dr com.apple.quarantine $WORK"
  not_logged "launchctl"
  not_logged "security"
  not_logged "codesign"
}

@test "install: --get-claude --only-prerequisites with no Minutes installs claude into HOME" {
  write_config "$CONFIG" "\"$MISSING\""
  run bash "$INSTALL" --get-claude --only-prerequisites
  assert_prereqs_ok
  if [ -x /opt/homebrew/bin/claude ] || [ -x /usr/local/bin/claude ]; then
    skip "a system-wide claude exists; install.sh uses it instead of downloading"
  fi
  [[ "$output" == *"installing Claude Code"* ]]
  logged "curl -fsSL https://claude.ai/install.sh"
  [ -x "$HOME/.local/bin/claude" ]
}

@test "install: a copy inside an .app bundle is refused without --only-prerequisites" {
  BUNDLED="$BATS_TEST_TMPDIR/Fake.app/Contents/Resources"
  mkdir -p "$BUNDLED"
  cp "$REPO/install.sh" "$BUNDLED/install.sh"
  write_config "$CONFIG" "\"$MINUTES\""
  run bash "$BUNDLED/install.sh"
  [ "$status" -eq 1 ]
  [[ "$output" == *"this is the copy inside the app"* ]]
  [[ "$output" != *"Python environment"* ]]
  [ ! -e "$BUNDLED/app" ]                      # nothing written inside the signed bundle (not even a log)
  # The setup guide runs that copy with --only-prerequisites, which is allowed.
  run bash "$BUNDLED/install.sh" --only-prerequisites
  assert_prereqs_ok
}

@test "install: minutes_app in double quotes is honoured by --get-minutes" {
  write_config "$CONFIG" "\"$MINUTES\""
  run bash "$INSTALL" --get-minutes --only-prerequisites
  assert_prereqs_ok
  [[ "$output" != *"downloading Minutes"* ]]
  not_logged "curl"
}

@test "install: minutes_app in single quotes is honoured by --get-minutes" {
  write_config "$CONFIG" "'$MINUTES'"
  run bash "$INSTALL" --get-minutes --only-prerequisites
  assert_prereqs_ok
  [[ "$output" != *"downloading Minutes"* ]]
  not_logged "curl"
}

@test "install: --get-minutes downloads when minutes_app points nowhere (fake curl/hdiutil)" {
  write_config "$CONFIG" "'$MISSING'"
  run bash "$INSTALL" --get-minutes --only-prerequisites
  [ "$status" -eq 1 ]
  [[ "$output" == *"downloading Minutes"* ]]
  [[ "$output" == *"could not open the Minutes disk image"* ]]
  logged "curl -fsSL -H User-Agent: meeting-copilot https://api.github.com/repos/silverstein/minutes/releases/latest"
  logged "Minutes_9.9.9_aarch64.dmg"
  logged "hdiutil attach -nobrowse -readonly"
  not_logged "codesign"                                 # nothing was mounted, so nothing was verified
}

@test "install: MEETING_COPILOT_CONFIG picks the config file" {
  write_config "$CONFIG" "\"$MISSING\""                       # default config: Minutes missing
  ALT="$BATS_TEST_TMPDIR/alt-config.toml"
  write_config "$ALT" "\"$MINUTES\""                          # alternate config: Minutes present
  export MEETING_COPILOT_CONFIG="$ALT"
  run bash "$INSTALL" --get-minutes --only-prerequisites
  assert_prereqs_ok
  not_logged "curl"
  # The other way round: the alternate config says missing, so a download is attempted.
  write_config "$ALT" "\"$MISSING\""
  : > "$FAKE_LOG"
  run bash "$INSTALL" --get-minutes --only-prerequisites
  [ "$status" -eq 1 ]
  logged "api.github.com"
}

@test "install: a tilde in minutes_app expands to HOME" {
  mv "$MINUTES" "$HOME/Minutes.app"
  write_config "$CONFIG" "\"~/Minutes.app\""
  run bash "$INSTALL" --get-minutes --only-prerequisites
  assert_prereqs_ok
  not_logged "curl"
}
