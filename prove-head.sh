#!/usr/bin/env bash
# prove-head.sh -- the whole proof a merge needs, in one run: every suite,
# then every mutant spec, in the tree it is run from.
#
#   [JOBS=N] ./prove-head.sh        (from a worktree at the head to prove)
#
# Steps; 2 and 3 both run whatever the other found, so one report shows all:
#   1. composer install, so the suites that need the PHP libraries run rather
#      than skip; if it fails, stop: exit 1, since what follows would skip.
#   2. this tree's run-suites.sh: every suite, a skip is not a pass (its rc:
#      1 a suite failed, 2 could not look, 3 a suite skipped).
#   3. mutate-spec.py over tests/mutants/*.json: every spec's mutants killed on
#      target. A spec whose last line does not say so has its output printed.
# Exit 0 only when every suite passed (none failed, none skipped) and every
# spec was killed on target; otherwise 1. The last line says which:
#   suites rc=N specs rc=N
#
# It is long (minutes on 8 cores) and binds the ports the suites use, so run
# it as a batch job on a box with the room, not beside another full run.
#
# tests/test_prove_head.py holds these rules against a throwaway tree with
# stand-ins for composer, the suites and mutate-spec.py.
printf '%s' jp-prove-head > /proc/$$/comm 2>/dev/null || true
set -u
jobs=${JOBS:-8}
out=$(mktemp -d)
trap 'rm -rf "$out"' EXIT

echo "== head $(git rev-parse --short HEAD)"
if ! composer install --no-interaction --no-progress --quiet; then
  echo "prove-head: composer install failed; the PHP suites would skip" >&2
  exit 1
fi

JOBS=$jobs ./run-suites.sh .
suites=$?

MUTATE_OUT_DIR="$out" python3 mutate-spec.py --jobs "$jobs" . tests/mutants/*.json
specs=$?
for f in "$out"/*.mutate.out; do
  [ -e "$f" ] || continue
  case "$(tail -n 1 "$f")" in
    *"every one killed on target"*) ;;
    *) echo "--- $(basename "$f") ---"; cat "$f" ;;
  esac
done

echo "suites rc=$suites specs rc=$specs"
[ "$suites" = 0 ] && [ "$specs" = 0 ]
