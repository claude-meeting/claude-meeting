#!/bin/bash
# Load a launchd job afresh from its plist:  launchd-start.sh <label> <plist>   (exit 0 once it is loaded)
#
# `launchctl bootout` returns before launchd has let go of the old job, and a bootstrap meanwhile fails. A bootstrap
# can also fail with "5: Input/output error" although the job did load; it then fails on every retry. So: wait for the
# old job to go, bootstrap, and count the job as started once it is loaded again (just loaded, launchd shows it as
# "state = xpcproxy", not yet "running": checked against the real launchd in tests/contracts.py).
set -uo pipefail

LABEL="${1:?usage: launchd-start.sh <label> <plist>}"
PLIST="${2:?usage: launchd-start.sh <label> <plist>}"
DOMAIN="gui/$(id -u)"
PAUSE="${LAUNCHD_START_PAUSE:-0.5}"        # seconds between checks (tests set 0)

loaded() { launchctl print "$DOMAIN/$LABEL" >/dev/null 2>&1; }

launchctl bootout "$DOMAIN/$LABEL" 2>/dev/null || true
# Up to 60 checks: a job that is still loaded then is the old one, and counting it as started would leave it running.
gone=0
for _ in $(seq 1 60); do loaded || { gone=1; break; }; sleep "$PAUSE"; done
[ "$gone" = 1 ] || { echo "$LABEL did not unload after bootout" >&2; exit 1; }
for attempt in 1 2 3 4 5; do
  launchctl bootstrap "$DOMAIN" "$PLIST" 2>/dev/null && exit 0
  loaded && exit 0                     # loaded only after the old one was gone: ours
  [ "$attempt" = 5 ] || sleep "$PAUSE"
done
exit 1
