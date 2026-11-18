#!/bin/bash
# The test suite with evidence: a local pass counts as CI when a report says what was tested, where, and how it
# went. The report comes from the run itself: the commit, the environment and the full log.
#
#   scripts/ci.sh local             scripts/test.sh here (`make test-pr`: every pull request)
#   scripts/ci.sh vm                the same in a throwaway macOS 15 virtual machine, set up from nothing
#                                   (`make test-ci`: before a release, and for install or dependency changes)
#   MC_CI_POST=1 scripts/ci.sh ...  also publish the report on GitHub; without it the report stays here
#
# Only a committed tree is tested, so the report names exactly what ran. The report goes to app/.ci-reports/, with
# the home folder written as ~ and the temporary folder as $TMPDIR. With MC_CI_POST=1 the commit must be pushed,
# and the report also goes to the ci-evidence branch, the commit gets the status local/<kind> (a check on its pull
# request) and the pull request a comment, all as the account gh is signed in to.
# A recording stops the run: tests would compete with it for memory.
#
# The vm kind needs tart, sshpass and Xcode (mounted read-only into the VM: the Command Line Tools have no XCTest).
set -uo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
KIND="${1:-}"
POST=0
[ "${MC_CI_POST:-}" = 1 ] && POST=1
[ "${2:-}" = "--no-post" ] && POST=0     # the default; kept for older habits
case "$KIND" in
  local|vm) ;;
  *) sed -n '5,8p' "$0" >&2; exit 2 ;;
esac
cd "$ROOT" || exit 1

die() { echo "ci: $*" >&2; exit 1; }
recording() {
  local f
  for f in "${MEETING_COPILOT_STATE:-$ROOT/app/state}/status.json" \
           "$HOME/Library/Application Support/Meeting Copilot/engine/app/state/status.json"; do
    grep -q '"recording": true' "$f" 2>/dev/null && return 0
  done
  return 1
}

[ -z "$(git status --porcelain)" ] || die "the working tree has changes: commit them, so the report names what ran"
SHA="$(git rev-parse HEAD)"
SHORT="${SHA:0:8}"
BRANCH="$(git rev-parse --abbrev-ref HEAD)"
recording && die "a recording is running: not starting"
if [ "$POST" = 1 ]; then
  gh api "repos/{owner}/{repo}/commits/$SHA" --silent 2>/dev/null || die "$SHORT is not on GitHub: push it first (or leave MC_CI_POST unset)"
fi

WORK="$(mktemp -d /tmp/mc-ci.XXXXXX)"
LOG="$WORK/log"
REPORT="$ROOT/app/.ci-reports/$SHORT-$KIND.txt"
ENV_FILE="$WORK/env"
VM=mc-ci
SSH_OPTS=(-o StrictHostKeyChecking=no -o UserKnownHostsFile=/dev/null -o LogLevel=ERROR
          -o PubkeyAuthentication=no -o PreferredAuthentications=password
          -o ControlMaster=auto -o "ControlPath=/tmp/mc-ci-%C" -o ControlPersist=3600 -o ServerAliveInterval=30)
vm() { sshpass -p admin ssh "${SSH_OPTS[@]}" "admin@$(cat "$WORK/ip")" "$@"; }

cleanup() {
  [ -n "${GUARD:-}" ] && kill "$GUARD" 2>/dev/null
  [ -n "${TAIL:-}" ] && kill "$TAIL" 2>/dev/null
  if [ "$KIND" = vm ]; then
    [ -s "$WORK/ip" ] && ssh "${SSH_OPTS[@]}" -O exit "admin@$(cat "$WORK/ip")" >/dev/null 2>&1
    tart stop "$VM" >/dev/null 2>&1
    tart delete "$VM" >/dev/null 2>&1
  fi
  rm -rf "$WORK"
}
trap cleanup EXIT

run_local() {
  echo "macos=$(sw_vers -productVersion) $(uname -m)" > "$ENV_FILE"
  echo "swift=$(swift --version 2>/dev/null | sed -n 1p)" >> "$ENV_FILE"
  scripts/test.sh --contracts          # this Mac has the real Minutes and launchd: check the fakes against them
}

run_vm() {
  local image="${MC_VM_IMAGE:-ghcr.io/cirruslabs/macos-sequoia-vanilla:latest}"
  local paths='export PATH="$HOME/.local/bin:/opt/homebrew/bin:/opt/homebrew/sbin:$PATH"'
  local ip=""
  step() { printf '\n===== %s =====\n' "$1"; vm "$2" || { echo "ci: step failed: $1"; exit 1; }; }

  git clone -q --no-hardlinks "$ROOT" "$WORK/src/repo" || exit 1
  tart delete "$VM" >/dev/null 2>&1
  tart clone "$image" "$VM" || exit 1
  tart set "$VM" --cpu 4 --memory 6144 --disk-size 90
  tart run "$VM" --no-graphics --dir="src:$WORK/src:ro" --dir="xcode:/Applications/Xcode.app:ro" >/dev/null 2>&1 &
  for _ in $(seq 1 90); do
    ip="$(tart ip "$VM" 2>/dev/null)"
    if [ -n "$ip" ]; then
      echo "$ip" > "$WORK/ip"
      vm true 2>/dev/null && break
    fi
    ip=""; sleep 2
  done
  [ -n "$ip" ] || { echo "ci: the VM did not answer over SSH"; exit 1; }

  # The mount's path has spaces, which DEVELOPER_DIR does not take: Xcode goes where it is expected instead.
  step "Xcode from this Mac" "echo admin | sudo -S ln -s '/Volumes/My Shared Files/xcode' /Applications/Xcode.app 2>/dev/null
    echo admin | sudo -S xcode-select -s /Applications/Xcode.app/Contents/Developer 2>/dev/null
    echo admin | sudo -S xcodebuild -license accept 2>/dev/null
    echo admin | sudo -S xcodebuild -runFirstLaunch 2>/dev/null | tail -1"
  step "checkout" "git -c safe.directory='*' clone -q '/Volumes/My Shared Files/src/repo' ~/repo && git -C ~/repo checkout -q $SHA"
  [ "$(vm 'git -C ~/repo rev-parse HEAD')" = "$SHA" ] || { echo "ci: the VM is not on $SHA"; exit 1; }
  step "Homebrew" "NONINTERACTIVE=1 CI=1 /bin/bash -c \"\$(curl -fsSL https://raw.githubusercontent.com/Homebrew/install/HEAD/install.sh)\" 2>&1 | tail -2"
  step "script and media tools" "$paths; HOMEBREW_NO_AUTO_UPDATE=1 HOMEBREW_NO_INSTALL_CLEANUP=1 brew install -q bats-core shellcheck ffmpeg 2>&1 | tail -2"
  step "uv" "curl -LsSf https://astral.sh/uv/install.sh | sh 2>&1 | tail -1"
  step "Python environment" "$paths; set -o pipefail; cd ~/repo && uv python install 3.14 2>&1 | tail -1 && uv venv -q --python 3.14 app/asr-venv \
    && uv pip install -q --python app/asr-venv/bin/python -r app/requirements.txt -r app/requirements-dev.txt"
  step "helpers" "$paths; cd ~/repo && mkdir -p app/bin/MeetingCopilotScreen.app/Contents/MacOS \
    && swiftc -O -target arm64-apple-macos15.0 app/src/mic-users.swift -o app/bin/mic-users \
    && swiftc -O -target arm64-apple-macos15.0 app/src/frames-to-video.swift -o app/bin/frames-to-video \
    && swiftc -O -target arm64-apple-macos15.0 app/src/screen-ocr.swift -o app/bin/screen-ocr \
    && swiftc -O -target arm64-apple-macos15.0 app/src/screen-capture.swift -o app/bin/MeetingCopilotScreen.app/Contents/MacOS/screen-capture"
  {
    echo "macos=$(vm sw_vers -productVersion) $(vm uname -m) (virtual machine, set up from nothing)"
    echo "swift=$(vm 'swift --version 2>/dev/null | sed -n 1p') (this Mac's Xcode)"
    echo "image=$image"
  } > "$ENV_FILE"
  printf '\n===== tests =====\n'
  vm "$paths; cd ~/repo && scripts/test.sh 2>&1"
}

# Watches the run and stops it for a recording.
guard() {
  while kill -0 "$1" 2>/dev/null; do
    if recording; then
      echo "ci: a recording started: stopping the tests"
      touch "$WORK/stopped"
      if [ "$KIND" = vm ]; then tart stop "$VM" >/dev/null 2>&1; else kill -TERM -- "-$1" 2>/dev/null; fi
      return
    fi
    sleep 5
  done
}

post() {
  local path="reports/$SHORT-$KIND.txt" state="$1" summary="$2" old url n
  if ! gh api "repos/{owner}/{repo}/branches/ci-evidence" --silent 2>/dev/null; then
    # A neutral author, no signature and UTC: not the identity, key and time zone git uses for other commits here.
    n="$(TZ=UTC GIT_AUTHOR_NAME=ci-evidence GIT_AUTHOR_EMAIL=ci-evidence GIT_COMMITTER_NAME=ci-evidence \
      GIT_COMMITTER_EMAIL=ci-evidence git -c commit.gpgsign=false commit-tree \
      -m "CI evidence: reports of test runs (scripts/ci.sh)" "$(git mktree </dev/null)")" || die "could not create the ci-evidence branch"
    git push -q origin "$n:refs/heads/ci-evidence" || die "could not create the ci-evidence branch"
  fi
  old="$(gh api "repos/{owner}/{repo}/contents/$path?ref=ci-evidence" -q .sha 2>/dev/null)"
  python3 - "$REPORT" "$WORK/put.json" "$SHORT" "$KIND" "$old" <<'PY' || die "could not prepare the report"
import base64, json, sys
report, out, short, kind, old = sys.argv[1:6]
body = {"message": f"{short} {kind}", "branch": "ci-evidence",
        "content": base64.b64encode(open(report, "rb").read()).decode()}
if old:
    body["sha"] = old          # a second run of this commit replaces the report; the branch keeps the first
json.dump(body, open(out, "w"))
PY
  url="$(gh api -X PUT "repos/{owner}/{repo}/contents/$path" --input "$WORK/put.json" -q .content.html_url)" \
    || die "could not upload the report; it is in $REPORT"
  gh api -X POST "repos/{owner}/{repo}/statuses/$SHA" -f state="$state" -f context="local/$KIND" \
    -f description="${summary:0:140}" -f target_url="$url" --silent || die "could not set the status of $SHORT"
  n="$(gh pr list --head "$BRANCH" --json number -q '.[0].number' 2>/dev/null)"
  if [ -n "$n" ]; then
    gh pr comment "$n" --body "**local/$KIND on \`$SHORT\`: $state**

$summary

[Full report]($url)" >/dev/null || echo "ci: could not comment on pull request $n" >&2
  fi
  echo "report on GitHub: $url"
}

: > "$LOG"
mkdir -p "$WORK/src"
tail -f "$LOG" &
TAIL=$!
set -m                 # the run gets its own process group, so the guard can stop all of it
"run_$KIND" > "$LOG" 2>&1 &
JOB=$!
set +m
guard "$JOB" &
GUARD=$!
wait "$JOB"
CODE=$?
kill "$GUARD" 2>/dev/null
GUARD=""
sleep 1
kill "$TAIL" 2>/dev/null
TAIL=""

if [ -e "$WORK/stopped" ]; then
  echo "ci: stopped for a recording; nothing counts from this run" >&2
  exit 3
fi
problem=""
[ "$(git rev-parse HEAD)" = "$SHA" ] && [ -z "$(git status --porcelain)" ] \
  || problem="the working tree changed during the run"
args=()
while IFS= read -r line; do args+=("$line"); done < <(cat "$ENV_FILE" 2>/dev/null)
SUMMARY="$(python3 scripts/ci_report.py "$LOG" "$CODE" "$REPORT" "kind=$KIND" "commit=$SHA" "branch=$BRANCH" \
  "date=$(date -u +%Y-%m-%d)" ${args[@]+"${args[@]}"} "problem=$problem")"
RESULT=$?
echo
echo "$SUMMARY"
echo "report: $REPORT"
[ "$POST" = 0 ] || post "$([ "$RESULT" = 0 ] && echo success || echo failure)" "$SUMMARY"
exit "$RESULT"
