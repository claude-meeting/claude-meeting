#!/usr/bin/env bats
# uninstall.sh against a throwaway copy of the engine, a fake HOME and fake launchctl/security.

load common

LABEL=com.meeting-copilot.autopilot

setup() {
  common_setup
  ROOT="$BATS_TEST_TMPDIR/engine"                 # this copy of the engine
  APP="$ROOT/app"
  mkdir -p "$APP/bin" "$APP/tools" "$APP/asr-venv/bin" "$APP/models" "$APP/state" "$APP/work"
  cp "$REPO/uninstall.sh" "$ROOT/uninstall.sh"
  : > "$APP/bin/autopilot.py"
  : > "$APP/tools/setup_minutes.py"
  : > "$APP/models/model.bin"
  # Fake venv python: logs its arguments and whether the venv folder still exists at call time.
  cat > "$APP/asr-venv/bin/python" <<'PY'
#!/bin/bash
app="$(cd "$(dirname "$0")/../.." 2>/dev/null && pwd)"
present=no; [ -n "$app" ] && [ -d "$app/asr-venv" ] && present=yes
printf 'python %s (asr-venv present: %s)\n' "$*" "$present" >> "${FAKE_LOG:?}"
exit 0
PY
  chmod +x "$APP/asr-venv/bin/python"
  AGENTS="$HOME/Library/LaunchAgents"
  mkdir -p "$AGENTS"
  PLIST="$AGENTS/$LABEL.plist"
  TRASH_ROOT="$HOME/.Trash"
}

# write_plist <autopilot.py path>: a LaunchAgent plist whose ProgramArguments run that file.
write_plist() {
  cat > "$PLIST" <<EOF
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0"><dict>
  <key>Label</key><string>$LABEL</string>
  <key>ProgramArguments</key><array><string>/some/python</string><string>$1</string></array>
</dict></plist>
EOF
}

# trashed <path relative to HOME>: the item was moved into the Trash folder keeping its path.
trashed() { [ "$(find "$TRASH_ROOT" -path "*/meeting-copilot-uninstall-*/$1" | wc -l | tr -d ' ')" -eq 1 ]; }

@test "uninstall: no plist registered -> nothing booted out" {
  run bash "$ROOT/uninstall.sh"
  [ "$status" -eq 0 ]
  [[ "$output" == *"no background service registered"* ]]
  not_logged "bootout"
  not_logged "launchctl"
}

@test "uninstall: plist of another copy -> nothing booted out, plist kept" {
  write_plist "/somewhere/else/app/bin/autopilot.py"
  run bash "$ROOT/uninstall.sh"
  [ "$status" -eq 0 ]
  [[ "$output" == *"no background service registered"* ]]
  not_logged "bootout"
  [ -f "$PLIST" ]
}

@test "uninstall: plist of this copy -> three labels booted out, plist trashed" {
  write_plist "$APP/bin/autopilot.py"
  run bash "$ROOT/uninstall.sh"
  [ "$status" -eq 0 ]
  [[ "$output" == *"stopped com.meeting-copilot.autopilot"* ]]
  [ "$(logged_count "launchctl bootout gui/$(id -u)/")" -eq 3 ]
  logged "launchctl bootout gui/$(id -u)/com.meeting-copilot.autopilot"
  logged "launchctl bootout gui/$(id -u)/com.meeting-copilot.capture"
  logged "launchctl bootout gui/$(id -u)/com.meeting-copilot.screen"
  not_logged "launchctl bootstrap"
  [ ! -e "$PLIST" ]
  trashed "Library/LaunchAgents/$LABEL.plist"
  # Without --purge the environment stays.
  [ -d "$APP/asr-venv" ]
  [ -d "$APP/models" ]
  [[ "$output" == *"run with --purge to remove them"* ]]
}

@test "uninstall: trashed items keep their path relative to HOME (two meeting-copilot folders)" {
  mkdir -p "$HOME/.config/meeting-copilot" "$HOME/Library/Caches/meeting-copilot"
  echo cfg > "$HOME/.config/meeting-copilot/config.toml"
  echo cache > "$HOME/Library/Caches/meeting-copilot/cache.bin"
  run bash "$ROOT/uninstall.sh" --purge
  [ "$status" -eq 0 ]
  [ ! -e "$HOME/.config/meeting-copilot" ]
  [ ! -e "$HOME/Library/Caches/meeting-copilot" ]
  trashed ".config/meeting-copilot/config.toml"
  trashed "Library/Caches/meeting-copilot/cache.bin"
  # Items outside HOME keep their absolute path minus the leading slash.
  trashed "${APP#/}/models/model.bin"
}

@test "uninstall: --purge runs setup_minutes.py --undo before the venv is moved" {
  run bash "$ROOT/uninstall.sh" --purge
  [ "$status" -eq 0 ]
  logged "python $APP/tools/setup_minutes.py --undo (asr-venv present: yes)"
  [ ! -e "$APP/asr-venv" ]
  [ ! -e "$APP/models" ]
  [ ! -e "$APP/state" ]
  [ ! -e "$APP/work" ]
  trashed "${APP#/}/asr-venv/bin/python"
  [[ "$output" != *"could not restore the Minutes settings"* ]]
}

@test "uninstall: --purge only trashes our Hugging Face models" {
  export HF_HOME="$BATS_TEST_TMPDIR/hf"
  HUB="$HF_HOME/hub"
  for d in models--mlx-community--TEA-ASR-1.1-8bit models--mlx-community--Qwen3-ASR-0.6B-8bit \
           models--nvidia--parakeet-tdt-0.6b-v2 models--Parakeet-Other models--other; do
    mkdir -p "$HUB/$d"; : > "$HUB/$d/blob"
  done
  run bash "$ROOT/uninstall.sh" --purge
  [ "$status" -eq 0 ]
  [ ! -e "$HUB/models--mlx-community--TEA-ASR-1.1-8bit" ]
  [ ! -e "$HUB/models--mlx-community--Qwen3-ASR-0.6B-8bit" ]
  [ ! -e "$HUB/models--nvidia--parakeet-tdt-0.6b-v2" ]
  [ ! -e "$HUB/models--Parakeet-Other" ]
  [ -d "$HUB/models--other" ]
  trashed "${HUB#/}/models--mlx-community--TEA-ASR-1.1-8bit/blob"
}

@test "uninstall: --purge without HF_HOME looks in ~/.cache/huggingface" {
  HUB="$HOME/.cache/huggingface/hub"
  mkdir -p "$HUB/models--x--parakeet-y" "$HUB/models--other"
  run bash "$ROOT/uninstall.sh" --purge
  [ "$status" -eq 0 ]
  [ ! -e "$HUB/models--x--parakeet-y" ]
  [ -d "$HUB/models--other" ]
}

@test "uninstall: keychain list with our signing keychain -> rewritten without it" {
  export FAKE_KEYCHAINS="$BATS_TEST_TMPDIR/keychains.txt"
  LOGIN="$HOME/Library/Keychains/login.keychain-db"
  SIGNING="$HOME/.config/meeting-copilot/signing/signing.keychain-db"
  printf '    "%s"\n    "%s"\n    "/Library/Keychains/System.keychain"\n' "$LOGIN" "$SIGNING" > "$FAKE_KEYCHAINS"
  run bash "$ROOT/uninstall.sh" --purge
  [ "$status" -eq 0 ]
  logged "security list-keychains -d user"
  logged "security list-keychains -d user -s $LOGIN /Library/Keychains/System.keychain"
  not_logged "-s $LOGIN $SIGNING"
  [ "$(logged_count "security list-keychains -d user -s")" -eq 1 ]
}

@test "uninstall: keychain list without our keychain -> search list untouched" {
  export FAKE_KEYCHAINS="$BATS_TEST_TMPDIR/keychains.txt"
  printf '    "%s/Library/Keychains/login.keychain-db"\n' "$HOME" > "$FAKE_KEYCHAINS"
  run bash "$ROOT/uninstall.sh" --purge
  [ "$status" -eq 0 ]
  logged "security list-keychains -d user"
  not_logged "list-keychains -d user -s"
}

@test "uninstall: empty keychain list -> search list untouched" {
  run bash "$ROOT/uninstall.sh" --purge
  [ "$status" -eq 0 ]
  logged "security list-keychains -d user"
  not_logged "list-keychains -d user -s"
}

@test "uninstall: only our signing keychain on the list -> never leave it empty" {
  export FAKE_KEYCHAINS="$BATS_TEST_TMPDIR/keychains.txt"
  printf '    "%s/.config/meeting-copilot/signing/signing.keychain-db"\n' "$HOME" > "$FAKE_KEYCHAINS"
  run bash "$ROOT/uninstall.sh" --purge
  [ "$status" -eq 0 ]
  not_logged "list-keychains -d user -s"
}

@test "uninstall: without --purge the keychain is not consulted" {
  run bash "$ROOT/uninstall.sh"
  [ "$status" -eq 0 ]
  not_logged "security"
}

@test "uninstall: unknown option exits 2" {
  run bash "$ROOT/uninstall.sh" --bogus
  [ "$status" -eq 2 ]
  [[ "$output" == *"unknown option: --bogus"* ]]
  not_logged "launchctl"
}
