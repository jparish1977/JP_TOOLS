#!/bin/bash
# ci-image/mutmut-nightly.sh -- the full mutmut run over JP_TOOLS master, and
# its state written into the runner volume mutmut-state for CI to start from.
#
#   ssh iteration8 'bash ~/projects/JP_TOOLS/ci-image/mutmut-nightly.sh'
#
# Joe's schedule (2026-10-10): PRs mutate only what they changed; this full run
# is nightly. CI's mutation job copies /mutmut-state/current/mutants and
# /mutmut-state/current/baseline.json out of the volume, read-only, and gates a
# PR against them (mutmut-gate.py). Without them CI cannot judge, and says so.
#
# What it does, stopping at the first failure:
#   1. a clean worktree of origin/master in WORK (default ~/.cache/jpt-mutmut-nightly),
#      its PHP and JS installs, and a venv at requirements-dev.txt's pins;
#   2. mutmut-gate.py run --full, then record the per-function baseline;
#   3. the state into the volume: mutants/ and baseline.json are streamed into
#      dind, unpacked as the fleet-ci user under /mutmut-state/next, and swapped
#      in for /mutmut-state/current. A reader in the gap of the swap finds no
#      state and fails as could-not-look; never a half-written one.
#
# It runs about 40 minutes on 8 cores. iteration8-server's unit runs it through
# i8jobs after fleet-ci-reap (reap 02:00, this 02:30) and registers it with
# claude-job, so the brake stops it. Environment: WORK, JOBS (default 8), REF
# (default origin/master; the first fill ran origin/mutmut, PR #215, before
# mutmut-gate.py was on master).
#
# TESTED AGAINST: not yet; its first run (filling the empty volume) is running.
# NOT TESTED AGAINST: a second run swapping over an existing state; a volume
#   that is missing (the docker step fails, and so does this).
printf '%s' jpt-mm-nightly > /proc/$$/comm 2>/dev/null
set -euo pipefail
here=$(cd "$(dirname "$0")/.." && pwd)
work=${WORK:-$HOME/.cache/jpt-mutmut-nightly}
jobs=${JOBS:-8}
ref=${REF:-origin/master}
dind=forgejo-runner-docker-in-docker-1

echo "mutmut-nightly: worktree of $ref in $work"
git -C "$here" fetch -q origin
if [ -d "$work/tree" ]; then
    git -C "$here" worktree remove --force "$work/tree"
fi
git -C "$here" worktree prune
mkdir -p "$work"
git -C "$here" worktree add -q --detach "$work/tree" "$ref"
cd "$work/tree"
echo "mutmut-nightly: at $(git log --oneline -1)"

python3 -m venv "$work/venv"
"$work/venv/bin/pip" install -q -r requirements-dev.txt
composer install --no-interaction --no-progress --quiet
npm ci --silent
export PATH="$work/venv/bin:$PATH"

python mutmut-gate.py run --full --max-children "$jobs"
python -m mutmut results --all true > "$work/results.txt"
python mutmut-gate.py record baseline.json --results "$work/results.txt"

echo "mutmut-nightly: writing the state into the mutmut-state volume"
tar -C "$work/tree" -cf - mutants baseline.json \
    | sudo -n docker exec -i "$dind" docker run --rm -i -v mutmut-state:/s fleet-ci:current \
        sh -c 'set -e; rm -rf /s/next; mkdir /s/next; tar -C /s/next -xf -
               rm -rf /s/old; [ -d /s/current ] && mv /s/current /s/old; mv /s/next /s/current
               rm -rf /s/old; du -sh /s/current; ls /s/current'
echo "mutmut-nightly: done"
