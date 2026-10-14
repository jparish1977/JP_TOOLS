#!/bin/bash
# tools/lab/rename-pr-mutmut-entry.sh -- rename one entry in the runner volume
# that keeps each pull request's own mutmut state (ci-image/pr-mutmut-state.sh).
#
#   bash tools/lab/rename-pr-mutmut-entry.sh VOLUME OLD NEW
#
# ANSWERS: carrying a kept entry across a change in how entries are named, so
# the branch's next run restores it rather than judging its whole diff again.
# Entries were named BRANCH@STATE_VERSION; they are now BRANCH.
#
# Runs on the box with the fleet runner: the volume lives in its
# docker-in-docker daemon, reached with sudo, and the move is made as the
# fleet-ci user that owns the entries. Lists the volume before and after.
# Refuses when OLD is missing or NEW exists, so it never overwrites an entry.
#
# TESTED AGAINST: jp-tools-mutmut-pr on the fleet runner, batch2's entry from
# before the naming change (the listing before and after is the evidence).
# NOT TESTED AGAINST: a runner whose daemon container is not named
# forgejo-runner-docker-in-docker-1, or a volume being written by a running job
# (the move is one rename, but a job mid-save could recreate OLD).
printf '%s' jpt-pr-rename > /proc/$$/comm 2>/dev/null
set -euo pipefail
volume=${1:?usage: rename-pr-mutmut-entry.sh VOLUME OLD NEW}
old=${2:?usage: rename-pr-mutmut-entry.sh VOLUME OLD NEW}
new=${3:?usage: rename-pr-mutmut-entry.sh VOLUME OLD NEW}
daemon=forgejo-runner-docker-in-docker-1
in_volume() {
  sudo -n docker exec "$daemon" docker run --rm --user fleet-ci -v "$volume:/p" fleet-ci:current sh -c "$1"
}
echo "== before"
in_volume 'ls -la /p'
in_volume "test -d '/p/$old' && test ! -e '/p/$new' && mv -T '/p/$old' '/p/$new' && chmod go-w '/p/$new'" \
  || { echo "rename-pr-mutmut-entry: $old is not there, or $new already is: nothing moved" >&2; exit 1; }
echo "== after"
in_volume 'ls -la /p'
