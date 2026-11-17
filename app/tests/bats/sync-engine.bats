#!/usr/bin/env bats
# app/macos/sync-engine.sh: copies the bundled engine over an installed one, keeping the user's
# Python environment, models and state.

load common

setup() {
  common_setup
  SRC="$BATS_TEST_TMPDIR/bundled"                 # the engine as the Mac app bundles it
  mkdir -p "$SRC/app/bin" "$SRC/app/lib" "$SRC/app/src" "$SRC/app/tools" "$SRC/bin"
  echo 'print("new")' > "$SRC/app/lib/new.py"
  echo 'print("tool")' > "$SRC/app/tools/tool.py"
  : > "$SRC/app/bin/autopilot.py"
  : > "$SRC/app/src/helper.swift"
  echo '[user]' > "$SRC/app/config.example.toml"
  echo 'mlx' > "$SRC/app/requirements.txt"
  printf '#!/bin/bash\n' > "$SRC/install.sh"
  printf '#!/bin/bash\n' > "$SRC/uninstall.sh"
  printf '#!/bin/bash\n' > "$SRC/bin/uv"
  chmod +x "$SRC/install.sh" "$SRC/uninstall.sh" "$SRC/bin/uv"
  cp "$REPO/app/macos/sync-engine.sh" "$SRC/sync-engine.sh"

  DEST="$BATS_TEST_TMPDIR/installed"              # an older install with user data
  mkdir -p "$DEST/app/asr-venv/bin" "$DEST/app/models/m" "$DEST/app/state" "$DEST/app/lib"
  echo venv > "$DEST/app/asr-venv/bin/python"
  echo model > "$DEST/app/models/m/weights"
  echo state > "$DEST/app/state/autopilot.log"
  echo 'print("old")' > "$DEST/app/lib/old.py"
  echo 0.1.0 > "$DEST/.version"
}

@test "sync-engine: user data survives, old code goes, .version is dropped" {
  run bash "$SRC/sync-engine.sh" "$DEST" 1.2.3
  [ "$status" -eq 0 ]
  [ "$output" = "engine 1.2.3 in $DEST" ]
  # kept
  [ "$(cat "$DEST/app/asr-venv/bin/python")" = venv ]
  [ "$(cat "$DEST/app/models/m/weights")" = model ]
  [ "$(cat "$DEST/app/state/autopilot.log")" = state ]
  # replaced whole
  [ ! -e "$DEST/app/lib/old.py" ]
  [ -f "$DEST/app/lib/new.py" ]
  [ -f "$DEST/app/tools/tool.py" ]
  [ -f "$DEST/app/bin/autopilot.py" ]
  [ -f "$DEST/app/src/helper.swift" ]
  [ -f "$DEST/app/config.example.toml" ]
  [ -f "$DEST/app/requirements.txt" ]
  [ -x "$DEST/install.sh" ]
  [ -x "$DEST/uninstall.sh" ]
  [ -f "$DEST/sync-engine.sh" ]
  [ -x "$DEST/bin/uv" ]
  # a half-done update must count as outdated
  [ ! -e "$DEST/.version" ]
  logged "xattr -dr com.apple.quarantine $DEST"
}

@test "sync-engine: works into an empty destination" {
  NEW="$BATS_TEST_TMPDIR/fresh"
  run bash "$SRC/sync-engine.sh" "$NEW" 2.0
  [ "$status" -eq 0 ]
  [ -f "$NEW/app/lib/new.py" ]
  [ -x "$NEW/bin/uv" ]
  [ ! -e "$NEW/.version" ]
}

@test "sync-engine: a second sync replaces a read-only uv" {
  chmod 0555 "$SRC/bin/uv"                        # as build.sh bundles it from Homebrew
  run bash "$SRC/sync-engine.sh" "$DEST" 1.2.3
  [ "$status" -eq 0 ]
  chmod u+w "$SRC/bin/uv"
  printf '#!/bin/bash\necho new\n' > "$SRC/bin/uv"
  chmod 0555 "$SRC/bin/uv"
  run bash "$SRC/sync-engine.sh" "$DEST" 1.2.4
  [ "$status" -eq 0 ]
  [ "$output" = "engine 1.2.4 in $DEST" ]
  [ -x "$DEST/bin/uv" ]
  [ "$(bash "$DEST/bin/uv")" = new ]
}

@test "sync-engine: missing version exits non-zero and copies nothing" {
  run bash "$SRC/sync-engine.sh" "$DEST"
  [ "$status" -ne 0 ]
  [[ "$output" == *"version"* ]]
  [ -f "$DEST/app/lib/old.py" ]
  [ -f "$DEST/.version" ]
}

@test "sync-engine: no arguments exits non-zero" {
  run bash "$SRC/sync-engine.sh"
  [ "$status" -ne 0 ]
  [[ "$output" == *"destination"* ]]
}
