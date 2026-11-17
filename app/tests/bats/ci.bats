#!/usr/bin/env bats
# scripts/ci.sh: what it refuses, that a report always names the commit that ran and no account's folders, and that
# nothing goes to GitHub unless MC_CI_POST=1. A small git repository holds the script and a stand-in scripts/test.sh;
# gh is a fake that records its calls. The virtual machine is not started here.

load common

setup() {
  common_setup
  unset MC_CI_POST
  SANDBOX="$BATS_TEST_TMPDIR/repo"
  mkdir -p "$SANDBOX/scripts"
  cp "$REPO/scripts/ci.sh" "$REPO/scripts/ci_report.py" "$SANDBOX/scripts/"
  printf 'app/.ci-reports/\napp/state/\n' > "$SANDBOX/.gitignore"
  write_tests good
  git -C "$SANDBOX" init -q
  git -C "$SANDBOX" -c user.name=t -c user.email=t add -A
  git -C "$SANDBOX" -c user.name=t -c user.email=t commit -qm first
  SHA="$(git -C "$SANDBOX" rev-parse HEAD)"
  export REPORT="$SANDBOX/app/.ci-reports/${SHA:0:8}-local.txt"
  cat > "$FAKE_BIN/gh" <<'SH'
#!/bin/bash
echo "gh $*" >> "$FAKE_LOG"
case "$*" in
  *commits/*) [ ! -e "$FAKE_LOG.unpushed" ] ;;
  *branches/ci-evidence*) [ ! -e "$FAKE_LOG.nobranch" ] ;;
  *"-q .sha"*) exit 1 ;;
  *"-X PUT"*) echo "https://example.com/report" ;;
esac
SH
  chmod +x "$FAKE_BIN/gh"
}

write_tests() {
  cat > "$SANDBOX/scripts/test.sh" <<'SH'
#!/bin/bash
echo "Ran 3 tests in 0.1s"
echo "TOTAL     10    1  90.0%"
echo "	 Executed 4 tests, with 0 failures (0 unexpected) in 0.1 (0.1) seconds"
echo "ok 1 a"
echo "==> shellcheck"
SH
  if [ "$1" = paths ]; then             # a pass whose log names the test's home and temporary folders
    # shellcheck disable=SC2016  # expanded when the stand-in runs, under the test's HOME and TMPDIR
    printf '%s\n' 'echo "warning at $HOME/proj/app/lib/x.py:3"' 'echo "wrote $TMPDIR/mc-test-1/review.md"' \
      'echo "==> all green"' >> "$SANDBOX/scripts/test.sh"
  elif [ "$1" = good ]; then
    echo 'echo "==> all green"' >> "$SANDBOX/scripts/test.sh"
  else
    printf 'echo "  !! bats"\necho "==> FAILED"\nexit 1\n' >> "$SANDBOX/scripts/test.sh"
  fi
  chmod +x "$SANDBOX/scripts/test.sh"
}

commit_tests() {
  git -C "$SANDBOX" -c user.name=t -c user.email=t commit -qam change
  SHA="$(git -C "$SANDBOX" rev-parse HEAD)"
  REPORT="$SANDBOX/app/.ci-reports/${SHA:0:8}-local.txt"
}

@test "a pass: the report names the commit and holds the log; nothing goes to GitHub by default" {
  run "$SANDBOX/scripts/ci.sh" local
  [ "$status" -eq 0 ]
  head -1 "$REPORT" | grep -q "^result: passed$"
  grep -q "^commit: $SHA$" "$REPORT"
  grep -q "^kind: local$" "$REPORT"
  grep -q "^macos: " "$REPORT"
  grep -q "^Ran 3 tests" "$REPORT"
  [ ! -s "$FAKE_LOG" ]                                   # gh was not even asked
}

@test "with MC_CI_POST=1 the report goes to GitHub and the commit gets the status" {
  MC_CI_POST=1 run "$SANDBOX/scripts/ci.sh" local
  [ "$status" -eq 0 ]
  grep -q "statuses/$SHA .*state=success .*context=local/local" "$FAKE_LOG"
  grep -q "target_url=https://example.com/report" "$FAKE_LOG"
  [[ "$output" == *"report on GitHub: https://example.com/report"* ]]
}

@test "a failure: reported as one, and the status says so" {
  write_tests bad
  commit_tests
  MC_CI_POST=1 run "$SANDBOX/scripts/ci.sh" local
  [ "$status" -eq 1 ]
  head -1 "$REPORT" | grep -q "^result: FAILED$"
  grep -q "statuses/$SHA .*state=failure" "$FAKE_LOG"
}

@test "the report writes the home folder as ~ and the temporary folder as \$TMPDIR" {
  write_tests paths
  commit_tests
  run "$SANDBOX/scripts/ci.sh" local
  [ "$status" -eq 0 ]
  grep -qF "warning at ~/proj/app/lib/x.py:3" "$REPORT"
  grep -qF 'wrote $TMPDIR/mc-test-1/review.md' "$REPORT"
  ! grep -qF "$HOME" "$REPORT" || false
  ! grep -qF "$TMPDIR" "$REPORT" || false
  ! grep -qF "$BATS_TEST_TMPDIR" "$REPORT" || false
}

@test "the ci-evidence branch starts with a neutral, unsigned commit in UTC, whatever git is set to here" {
  touch "$FAKE_LOG.nobranch"
  git init -q --bare "$BATS_TEST_TMPDIR/origin.git"
  git -C "$SANDBOX" remote add origin "$BATS_TEST_TMPDIR/origin.git"
  # The identity and signing settings git uses for other commits here: none of them may reach this one.
  git -C "$SANDBOX" config user.name "Someone Else"
  git -C "$SANDBOX" config gpg.format ssh
  git -C "$SANDBOX" config user.signingkey "$BATS_TEST_TMPDIR/no-such-key.pub"
  git -C "$SANDBOX" config commit.gpgsign true
  MC_CI_POST=1 run "$SANDBOX/scripts/ci.sh" local
  [ "$status" -eq 0 ]
  run git -C "$BATS_TEST_TMPDIR/origin.git" log -1 --format='%an <%ae>|%cn <%ce>|%ai|%G?' ci-evidence
  [[ "$output" == "ci-evidence <ci-evidence>|ci-evidence <ci-evidence>|"*" +0000|N" ]]
}

@test "changes that are not committed are refused" {
  echo "x" > "$SANDBOX/new-file"
  run "$SANDBOX/scripts/ci.sh" local
  [ "$status" -eq 1 ]
  [[ "$output" == *"the working tree has changes"* ]]
  [ ! -e "$REPORT" ]
}

@test "a commit that is not on GitHub is refused when posting, and fine when not" {
  touch "$FAKE_LOG.unpushed"
  MC_CI_POST=1 run "$SANDBOX/scripts/ci.sh" local
  [ "$status" -eq 1 ]
  [[ "$output" == *"push it first"* ]]
  [ ! -e "$REPORT" ]
  run "$SANDBOX/scripts/ci.sh" local
  [ "$status" -eq 0 ]
  [ -s "$REPORT" ]
  MC_CI_POST=1 run "$SANDBOX/scripts/ci.sh" local --no-post
  [ "$status" -eq 0 ]
  ! grep -q "statuses/" "$FAKE_LOG" || false
}

@test "a run during a recording is refused" {
  mkdir -p "$SANDBOX/app/state"
  echo '{"recording": true}' > "$SANDBOX/app/state/status.json"
  run "$SANDBOX/scripts/ci.sh" local
  [ "$status" -eq 1 ]
  [[ "$output" == *"a recording is running"* ]]
}

@test "a recording that starts during the run stops it, and nothing is reported" {
  printf '#!/bin/bash\nmkdir -p app/state\necho %s > app/state/status.json\nsleep 30\n' "'{\"recording\": true}'" > "$SANDBOX/scripts/test.sh"
  commit_tests
  run "$SANDBOX/scripts/ci.sh" local
  [ "$status" -eq 3 ]
  [[ "$output" == *"stopped for a recording"* ]]
  [ ! -e "$REPORT" ]
  ! grep -q "statuses/" "$FAKE_LOG" || false
}

@test "a tree that changes during the run does not count" {
  printf 'echo "==> all green"\ntouch stray-file\n' >> "$SANDBOX/scripts/test.sh"
  commit_tests
  MC_CI_POST=1 run "$SANDBOX/scripts/ci.sh" local
  [ "$status" -eq 1 ]
  grep -q "^problem: the working tree changed during the run$" "$REPORT"
  grep -q "state=failure" "$FAKE_LOG"
}
