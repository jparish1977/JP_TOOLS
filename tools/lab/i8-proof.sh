#!/usr/bin/env bash
# tools/lab/i8-proof.sh -- the whole proof of a head, in one job: every suite,
# then every mutant spec, in the tree it is run from.
#
# ANSWERS: does this head pass everything a merge needs, on this box? Every
#   suite (tools/lab/run-suites.sh) and every spec in tests/mutants
#   (mutate-spec.py, all killed on target). The tree's vendor/ is installed
#   first (composer install), so suites that need the PHP libraries run rather
#   than skip. A suite whose last line is a SKIP is listed after, by name: a
#   skip is not a pass. Exit 0 only when suites and specs both pass and no
#   suite ended on a SKIP. run-suites.sh keeps only each suite's last line, so
#   a SKIP printed earlier in a suite is not seen here; a mutant aimed at the
#   skipped checks survives instead, which the specs report.
#
# usage, on iteration8, in a worktree at the head:
#   i8jobs submit --title T --cwd WORKTREE --cores 8 --mem 8G \
#       --env PATH=/home/joe/.local/pwsh:/home/joe/.local/share/jp-tools-venv/bin:/usr/local/bin:/usr/bin:/bin \
#       -- bash tools/lab/i8-proof.sh
#
# TESTED AGAINST: iteration8, i8jobs, 2026-10-09: head 929fa2e passed, 73
#   suites rc=0, 68 specs killed on target, 0 skips, exit 0, 4m19s
#   (20261009T204445.617Z-887865). Head 4140f74, with one spec off target
#   (jpt-pins-on-install-pwsh), printed that spec's output and exited 1
#   (20261009T202852.371Z-794bc7).
#
# NOT TESTED AGAINST: any box but iteration8; a tree without composer's global
#   repositories config (iteration8/core and utilities), where composer install
#   fails and this exits 1 saying so.
printf '%s' jpt-i8-proof > /proc/$$/comm 2>/dev/null
set -u
jobs=${JOBS:-8}
out=$(mktemp -d)
trap 'rm -rf "$out"' EXIT

echo "== head $(git rev-parse --short HEAD)"
if ! composer install --no-interaction --no-progress --quiet; then
    echo "i8-proof: composer install failed; the PHP suites would skip" >&2
    exit 1
fi

JOBS=$jobs tools/lab/run-suites.sh . | tee "$out/suites"
suites=${PIPESTATUS[0]}

MUTATE_OUT_DIR="$out" python3 mutate-spec.py --jobs "$jobs" . tests/mutants/*.json
specs=$?
for f in "$out"/*.mutate.out; do
    case "$(tail -n 1 "$f")" in
        *"every one killed on target"*) ;;
        *) echo "--- $(basename "$f") ---"; cat "$f" ;;
    esac
done

skips=0
while IFS= read -r line; do
    case "$line" in
        *SKIP*) echo "SKIPPED: $line"; skips=$((skips + 1)) ;;
    esac
done < "$out/suites"

echo "suites rc=$suites specs rc=$specs skips=$skips"
[ "$suites" = 0 ] && [ "$specs" = 0 ] && [ "$skips" = 0 ]
