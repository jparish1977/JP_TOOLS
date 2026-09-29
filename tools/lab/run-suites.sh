#!/usr/bin/env bash
# tools/lab/run-suites.sh -- run every suite in the repo, one line per suite.
#
# ANSWERS: did any suite change outcome between two trees? One line each,
#   "rc=N path :: last line of output", so a before-and-after pair reads side
#   by side or through diff. Then "suites: P/N passed in Ss".
#
# PARALLEL: JOBS suites at once (default: every core), each into its own
#   output file, printed afterwards in suite order, so the lines read the same
#   as a serial run's. The suites share nothing but the tree they read: each
#   makes its own temporary directories.
#
# TESTED AGAINST: two trees of this repo (11 and 13 suites), all rc=0, with
#   test_check_phpstan.py printing its SKIP line because vendor/ was absent.
#   A copy of the tree with a deliberately failing suite and a silent one
#   added: the failing one printed rc=1 and its own last line, the silent
#   one's line ended "::", and the run exited 1. Those were serial runs.
#   Parallel, on a 32-core box: 55/55 in 24s at every core; the same failing
#   and silent pair added, at JOBS=8, printed the same two lines in order and
#   exited 1 (56/57 in 26s).
#
# NOT TESTED AGAINST: Windows; suites that need root (ntfs skips its
#   directory cases itself). The last line is a summary only when the suite
#   prints one last.
#
# usage: [JOBS=N] tools/lab/run-suites.sh [REPO_ROOT]
#   default: the repo you are standing in, not the one this file lives in,
#   so it runs a fix branch's suites from any worktree.
printf '%s' jp-run-suites > /proc/$$/comm 2>/dev/null || true
set -u
root=${1:-$(git rev-parse --show-toplevel)}
cd "$root" || exit 2
jobs=${JOBS:-$(nproc)}
out=$(mktemp -d)
trap 'rm -rf "$out"' EXIT

run_one() {
  local n=$1 t=$2
  case "$t" in
    *.py) timeout 300 nice python3 "$t" > "$out/$n.out" 2>&1 ;;
    *.sh) PYTHON=python3 timeout 300 nice bash "$t" > "$out/$n.out" 2>&1 ;;
  esac
  echo "$?" > "$out/$n.rc"
}

start=$(date +%s)
suites=()
for t in tests/test_*.py tests/test_*.sh; do
  [ -e "$t" ] || continue
  suites+=("$t")
  run_one "${#suites[@]}" "$t" &
  while [ "$(jobs -rp | wc -l)" -ge "$jobs" ]; do wait -n; done
done
wait

worst=0
passed=0
for i in "${!suites[@]}"; do
  n=$((i + 1))
  rc=$(cat "$out/$n.rc")
  if [ "$rc" -eq 0 ]; then passed=$((passed + 1)); else worst=1; fi
  printf 'rc=%s %s :: %s\n' "$rc" "${suites[$i]}" "$(tail -n 1 "$out/$n.out" | cut -c1-110)"
done
echo "suites: $passed/${#suites[@]} passed in $(($(date +%s) - start))s"
exit "$worst"
