#!/bin/bash
# ci-image/fetch-mutmut-state.sh -- copy the runner volume's current mutmut
# state into this checkout: ./mutants and ./mutmut-baseline.json.
#
#   bash ci-image/fetch-mutmut-state.sh STATE_DIR       (from the checkout)
#
# The copy holds STATE_DIR/.lock shared (flock -s; a read-only mount can take
# it), so it waits for a publish in progress, and a publish, which holds it
# exclusively, waits for every copy in progress: no copy is cut short or
# mixes two states. STATE_DIR/current is resolved once, under the lock, and
# that version's directory is copied. With no .lock (nothing published in
# versions yet) there is no publisher to wait for. A current that is still a
# plain directory (the layout before versions) is read as it is.
#
# Exit: 0 copied; 2 no state there, so nothing to judge against (could not
# look). Used by both of check.yml's mutation jobs.
# tests/test_publish_mutmut_state.py holds these rules.
printf '%s' jpt-mut-fetch > /proc/$$/comm 2>/dev/null
set -euo pipefail
state=${1:?usage: fetch-mutmut-state.sh STATE_DIR}
if [ -e "$state/.lock" ]; then
  exec 9<"$state/.lock"
  flock -s 9
fi
src=$(readlink -f "$state/current" || true)
if [ -z "$src" ] || [ ! -d "$src/mutants" ] || [ ! -f "$src/baseline.json" ]; then
  echo "NO STATE in $state: the nightly full run has not left one, so there is" >&2
  echo "no baseline to judge against. Not judged (could not look)." >&2
  exit 2
fi
cp -a "$src/mutants" ./mutants
cp "$src/baseline.json" ./mutmut-baseline.json
echo "fetch-mutmut-state: state $(basename "$src") from $state"
