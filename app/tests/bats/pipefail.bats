#!/usr/bin/env bats
# Under `set -o pipefail`, `cmd | grep -q` fails when grep stops reading at its first match and cmd then dies of
# SIGPIPE: a check that should pass fails, or one that should fail passes; under `set -e` even an untested pipeline
# stops the script. Scripts with pipefail read the whole output first (grep -q ... <<< "$(cmd)", sed -n 1p instead of
# head -1). Commands sent to the test VM as strings run in its own shell and are left out.

load common

# A pipe (not ||) into a reader that stops early: grep/egrep -q, --quiet, --silent, -m, --max-count (also after
# variable assignments such as LC_ALL=C), head, or awk with exit. Limits, accepted: a pipe at the end of a line with
# the reader on the next, readers behind env or xargs, sed q, and files sourced into a pipefail script that do not
# say pipefail themselves.
EARLY='(^|[^|])\|[[:space:]]*([A-Za-z_][A-Za-z0-9_]*=[^[:space:]]*[[:space:]]+)*((e?grep)[[:space:]]+([^|]*[[:space:]])?(-[A-Za-z]*[qm]|--quiet|--silent|--max-count)|head([[:space:]]|$)|awk[[:space:]][^|]*exit)'

early_readers() { grep -n -E "$EARLY" "$1" | grep -v -E '^[0-9]+:[[:space:]]*#' | grep -v -E '^[0-9]+:check vm ' || true; }

@test "pipefail guard: the pattern finds what it should and nothing else" {
  f="$BATS_TEST_TMPDIR/sample.sh"
  printf '%s\n' \
    'codesign -dvv x 2>&1 | grep -q "Authority=me"' \
    'printf "%s" "$t" | LC_ALL=C grep -q "^a$"' \
    'launchctl print x | egrep --silent running' \
    'ls -t | head -1' \
    'security find-identity | awk "{print \$2; exit}"' \
    'x | grep -m1 y' > "$f"
  [ "$(early_readers "$f" | wc -l | tr -d ' ')" = 6 ]
  printf '%s\n' \
    '[ -f a ] || grep -q x b' \
    'grep -q x <<< "$(cmd)"' \
    'cmd | grep -c x' \
    'cmd | sed -n 1p' \
    '# cmd | grep -q x' > "$f"
  [ -z "$(early_readers "$f")" ]
}

@test "pipefail scripts: no 'cmd | grep -q' (or another reader that stops early)" {
  cd "$REPO"
  bad=""
  while IFS= read -r f; do
    grep -q 'pipefail' "$f" || continue
    hits="$(early_readers "$f")"
    [ -z "$hits" ] || bad="$bad$f: $hits"$'\n'
  done < <(git ls-files '*.sh' 'app/bin/copilot-*' 'app/bin/minutes')
  echo "$bad"
  [ -z "$bad" ]
}
