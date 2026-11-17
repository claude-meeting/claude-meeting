#!/bin/bash
# Run the test suite.
#
#   scripts/test.sh                 unit + integration + end-to-end (fake Minutes/Claude), coverage >= 85 %,
#                                   Swift tests, bats script tests, shellcheck
#   scripts/test.sh --only unit     one group: unit | integration | e2e | python | swift | bats | shellcheck | helpers
#   scripts/test.sh --live          also the real-model / real-Claude checks (minutes); needs MC_LIVE_MEETING
#   scripts/test.sh --no-coverage   skip the coverage threshold (quicker while iterating)
#
# Needs app/asr-venv (install.sh creates it; or `make venv`), and for the script groups: bats, shellcheck.
# --live replays a recording of your own with two speakers: MC_LIVE_MEETING is its base path (<base>.voice.wav and
# <base>.system.wav), MC_LIVE_START (mm:ss, default 00:00) a point where both talk.
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
APP="$ROOT/app"
PY="$APP/asr-venv/bin/python"
MIN_COVERAGE="${MIN_COVERAGE:-85}"
ONLY=""; LIVE=0; COVERAGE=1
while [ $# -gt 0 ]; do
  case "$1" in
    --only) ONLY="$2"; shift 2 ;;
    --live) LIVE=1; shift ;;
    --no-coverage) COVERAGE=0; shift ;;
    -h|--help) sed -n '2,12p' "$0"; exit 0 ;;
    *) echo "unknown option: $1" >&2; exit 2 ;;
  esac
done
if [ "$LIVE" = 1 ] && [ -z "${MC_LIVE_MEETING:-}" ]; then
  echo "--live needs MC_LIVE_MEETING: the base path of a recording of your own with two speakers" \
    "(<base>.voice.wav and <base>.system.wav), e.g. MC_LIVE_MEETING=~/meetings/<name>" >&2
  exit 2
fi

want() { [ -z "$ONLY" ] || [ "$ONLY" = "$1" ] || { [ "$ONLY" = python ] && [ "$1" != swift ] && [ "$1" != bats ] && [ "$1" != shellcheck ] && [ "$1" != helpers ]; }; }
step() { printf '\n==> %s\n' "$*"; }
failed=0
fail() { echo "  !! $*" >&2; failed=1; }

[ -x "$PY" ] || { echo "no $PY: run install.sh --no-service (or make venv) first" >&2; exit 1; }
"$PY" -c "import coverage" 2>/dev/null || "$APP/../bin/uv" pip install --python "$PY" -q -r "$APP/requirements-dev.txt" 2>/dev/null \
  || uv pip install --python "$PY" -q -r "$APP/requirements-dev.txt"

cd "$APP"
export COVERAGE_FILE="$APP/.coverage"
export COVERAGE_PROCESS_START="$APP/.coveragerc"     # child processes (autopilot.py, transcriber) report too
rm -f "$COVERAGE_FILE" "$COVERAGE_FILE".*
run_py() {   # run_py <label> <unittest pattern>
  step "$1"
  if [ "$COVERAGE" = 1 ]; then
    "$PY" -m coverage run --parallel-mode -m unittest discover -s tests -p "$2" || fail "$1"
  else
    "$PY" -m unittest discover -s tests -p "$2" || fail "$1"
  fi
}
want unit && run_py "Python unit tests" 'test_[!ie]*.py'
want integration && run_py "Python integration tests (fake Minutes and Claude)" 'test_integration.py'
want e2e && run_py "Python end-to-end tests (real processes, fakes)" 'test_e2e.py'
if [ "$COVERAGE" = 1 ] && { want unit || want integration || want e2e; }; then
  step "Coverage (app/lib, app/bin, app/tools)"
  "$PY" -m coverage combine --quiet 2>/dev/null || true
  "$PY" -m coverage report --skip-empty || true
  "$PY" -m coverage html -d "$APP/.coverage-html" --quiet 2>/dev/null || true
  "$PY" -m coverage xml -o "$APP/coverage.xml" --quiet 2>/dev/null || true
  if [ -z "$ONLY" ]; then
    "$PY" -m coverage report --fail-under="$MIN_COVERAGE" >/dev/null || fail "coverage below $MIN_COVERAGE %"
  fi
fi

if want swift; then
  step "Swift tests"
  (cd "$APP/macos" && swift test 2>&1 | tail -5) || fail "swift test"
fi
if want bats; then
  step "Script tests (bats)"
  if command -v bats >/dev/null; then bats "$APP/tests/bats" || fail "bats"; else fail "bats is not installed (brew install bats-core)"; fi
fi
if want helpers; then
  step "Helper tool tests"
  if [ -f "$APP/tests/bats/helpers.bats" ]; then bats "$APP/tests/bats/helpers.bats" || fail "helpers"; fi
fi
if want shellcheck; then
  step "shellcheck"
  if command -v shellcheck >/dev/null; then
    shellcheck -S warning "$ROOT/install.sh" "$ROOT/uninstall.sh" "$ROOT"/scripts/*.sh "$APP"/tools/*.sh "$APP"/macos/*.sh \
      "$APP/bin/minutes" "$APP/bin/copilot-name" "$APP/bin/copilot-known" || fail "shellcheck"
  else fail "shellcheck is not installed (brew install shellcheck)"; fi
fi

if [ "$LIVE" = 1 ]; then
  step "Live checks (real models, real Claude)"
  "$PY" "$APP/tests/resume_test.py" "$MC_LIVE_MEETING" --start "${MC_LIVE_START:-00:00}" || fail "resume test"
  "$PY" "$APP/tests/simulate_live.py" --assert-median-latency 3 --assert-speakers "我,其他人" \
    "$MC_LIVE_MEETING" "${MC_LIVE_START:-00:00}" 60 --speed 3 || fail "live replay"
  "$ROOT/scripts/test-fresh-home.sh" || fail "fresh-home install"
fi

[ "$failed" = 0 ] && { step "all green"; exit 0; } || { step "FAILED"; exit 1; }
