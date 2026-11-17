# Shared setup for the shell-script tests (bats-core).
#
# Every test runs with a throwaway HOME and a PATH whose first entry holds fake launchctl,
# security, codesign, hdiutil, curl and xattr: the scripts under test act on launchd services
# and the keychain, and the real ones must stay untouched. The fakes only append their
# arguments to the file named by $FAKE_LOG.
#
# Keep real home paths and e-mail addresses out of the tests.

REPO="$(cd "$BATS_TEST_DIRNAME/../../.." && pwd)"
FAKES="$BATS_TEST_DIRNAME/fakes"
export REPO FAKES

common_setup() {
  # shellcheck disable=SC2034  # read by helpers.bats to find ffprobe outside the fake PATH
  ORIG_PATH="$PATH"
  export TEST_HOME="$BATS_TEST_TMPDIR/home"
  mkdir -p "$TEST_HOME"
  export HOME="$TEST_HOME"
  export TMPDIR="$BATS_TEST_TMPDIR/tmp"
  mkdir -p "$TMPDIR"
  export FAKE_LOG="$BATS_TEST_TMPDIR/fake.log"
  : > "$FAKE_LOG"
  export FAKE_BIN="$BATS_TEST_TMPDIR/bin"
  mkdir -p "$FAKE_BIN"
  use_fake launchctl security codesign hdiutil curl xattr
  export PATH="$FAKE_BIN:/usr/bin:/bin:/usr/sbin:/sbin"
  unset MEETING_COPILOT_CONFIG HF_HOME FAKE_KEYCHAINS FAKE_IDENTITY
}

# use_fake <name>...: put the named fake command(s) first on PATH for this test.
use_fake() {
  local n
  for n in "$@"; do
    cp "$FAKES/$n" "$FAKE_BIN/$n"
    chmod +x "$FAKE_BIN/$n"
  done
}

# logged <text>: true when a fake command recorded a line containing <text>.
logged() { grep -qF -- "$1" "$FAKE_LOG"; }

# not_logged <text>: true when no fake command recorded such a line. (A plain `! logged` would
# not fail a bats test: bash's ERR trap does not fire for negated commands.)
not_logged() { ! grep -qF -- "$1" "$FAKE_LOG"; }

# last_line: the last line of $output from the previous `run`.
# shellcheck disable=SC2154  # lines is set by bats' run
last_line() { printf '%s\n' "${lines[${#lines[@]}-1]}"; }

# logged_count <text>: how many recorded lines contain <text>.
logged_count() { grep -cF -- "$1" "$FAKE_LOG" || true; }

# make_minutes_app <path.app>: a fake Minutes.app with an executable Contents/MacOS/minutes.
make_minutes_app() {
  mkdir -p "$1/Contents/MacOS"
  printf '#!/bin/sh\nexit 0\n' > "$1/Contents/MacOS/minutes"
  chmod +x "$1/Contents/MacOS/minutes"
}
