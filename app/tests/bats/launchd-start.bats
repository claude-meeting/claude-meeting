#!/usr/bin/env bats
# app/tools/launchd-start.sh: the service is loaded afresh, and a bootstrap that reports an error while the job is in
# fact running (launchd's "5: Input/output error") counts as started. launchctl is a scripted fake.

load common

setup() {
  common_setup
  export LAUNCHD_START_PAUSE=0
  STATE="$BATS_TEST_TMPDIR/launchd"; mkdir -p "$STATE"; export STATE
  # The fake: `print` succeeds while $STATE/loaded exists (and says running if $STATE/running exists); `bootout`
  # unloads unless $STATE/stuck exists; `bootstrap` loads, or fails with 5 when $STATE/bootstrap_fails exists.
  cat > "$FAKE_BIN/launchctl" <<'SH'
#!/bin/bash
printf 'launchctl %s\n' "$*" >> "$FAKE_LOG"
case "$1" in
  print) [ -f "$STATE/loaded" ] || exit 113; echo "state = $([ -f "$STATE/running" ] && echo running || echo waiting)" ;;
  bootout) [ -f "$STATE/stuck" ] || rm -f "$STATE/loaded" "$STATE/running" ;;
  bootstrap)
    if [ -f "$STATE/bootstrap_fails" ]; then
      [ -f "$STATE/loads_anyway" ] && touch "$STATE/loaded"            # loaded, not yet running ("xpcproxy")
      echo "Bootstrap failed: 5: Input/output error" >&2; exit 5
    fi
    touch "$STATE/loaded" "$STATE/running" ;;
esac
exit 0
SH
  chmod +x "$FAKE_BIN/launchctl"
}

start() { run bash "$REPO/app/tools/launchd-start.sh" com.example.job "$BATS_TEST_TMPDIR/job.plist"; }

@test "launchd-start: the old job is booted out and the new one bootstrapped" {
  touch "$STATE/loaded" "$STATE/running"
  start
  [ "$status" -eq 0 ]
  logged "launchctl bootout gui/$(id -u)/com.example.job"
  logged "launchctl bootstrap gui/$(id -u) $BATS_TEST_TMPDIR/job.plist"
}

@test "launchd-start: a bootstrap error while the job is loaded counts as started (issue #4)" {
  touch "$STATE/bootstrap_fails" "$STATE/loads_anyway"
  start
  [ "$status" -eq 0 ]
  [ "$(grep -c 'launchctl bootstrap' "$FAKE_LOG")" = 1 ]
}

@test "launchd-start: a job that never loads fails after five tries" {
  touch "$STATE/bootstrap_fails"
  start
  [ "$status" -eq 1 ]
  [ "$(grep -c 'launchctl bootstrap' "$FAKE_LOG")" = 5 ]
}

@test "launchd-start: an old job that does not unload is not taken for the new one" {
  touch "$STATE/loaded" "$STATE/running" "$STATE/stuck"
  start
  [ "$status" -eq 1 ]
  [[ "$output" == *"did not unload"* ]]
  not_logged "launchctl bootstrap"
}
