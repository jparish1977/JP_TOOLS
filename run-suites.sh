#!/usr/bin/env bash
# run-suites.sh -- run every suite in a JP_TOOLS tree, one line per suite.
#
#   [JOBS=N] [FAIL_LINES=N] [SUITE_TIMEOUT=SECONDS] ./run-suites.sh [REPO_ROOT]
#
# One line each, "rc=N path :: last line of output", in suite order, so a
# before-and-after pair reads side by side or through diff. Then the count:
#   suites: P/N passed, S skipped, F failed in Ss
# REPO_ROOT defaults to the repo you are standing in, not the one this file
# lives in, so it runs a branch's suites from any worktree.
#
# A SKIP IS NOT A PASS. A suite that exits 0 with a last line starting "SKIP"
# ran none of its cases (its tool was missing, say). It is counted as skipped,
# never as passed, and named after the count.
#
# Exit, and the last line says which, as "run-suites: exit N, MEANING":
#   0 every suite passed;
#   1 a suite failed (nonzero exit), whatever else happened;
#   2 could not look: REPO_ROOT could not be entered, so nothing ran;
#   3 none failed but at least one skipped.
# So a caller that wants "everything ran and passed" tests for 0, and one
# that tells a skip from a run that never happened tests for 2 and 3.
#
# Parallel: JOBS suites at once (default every core), each into its own file,
# printed afterwards in suite order. The suites share nothing but the tree:
# each makes its own temporary directories.
#
# A failing suite shows its output: after the count, each failed suite prints
# the last FAIL_LINES lines it wrote (default 40). A suite killed at
# SUITE_TIMEOUT (default 300 s, rc 124) shows only what it had flushed, which
# for Python's buffered stdout can be nothing; its name and rc are still there.
#
# tests/test_run_suites.py holds these rules against throwaway trees.
printf '%s' jp-run-suites > /proc/$$/comm 2>/dev/null || true
set -u
root=${1:-$(git rev-parse --show-toplevel)}
if ! cd "$root"; then
  echo "run-suites: exit 2, could not look: cannot enter $root"
  exit 2
fi
jobs=${JOBS:-$(nproc)}
fail_lines=${FAIL_LINES:-40}
suite_timeout=${SUITE_TIMEOUT:-300}
out=$(mktemp -d)
trap 'rm -rf "$out"' EXIT

run_one() {
  local n=$1 t=$2
  case "$t" in
    *.py) timeout "$suite_timeout" nice python3 "$t" > "$out/$n.out" 2>&1 ;;
    *.sh) PYTHON=python3 timeout "$suite_timeout" nice bash "$t" > "$out/$n.out" 2>&1 ;;
  esac
  echo "$?" > "$out/$n.rc"
}

# Every suite, JOBS at a time, into suites[].
run_all() {
  local t
  for t in tests/test_*.py tests/test_*.sh; do
    [ -e "$t" ] || continue
    suites+=("$t")
    run_one "${#suites[@]}" "$t" &
    while [ "$(jobs -rp | wc -l)" -ge "$jobs" ]; do wait -n; done
  done
  wait
}

# One line per suite, in order, and each counted once: failed, skipped or passed.
tally() {
  local i n rc last
  for i in "${!suites[@]}"; do
    n=$((i + 1))
    rc=$(cat "$out/$n.rc")
    last=$(tail -n 1 "$out/$n.out")
    if [ "$rc" -ne 0 ]; then
      failed+=("$n")
    elif [ "${last#SKIP}" != "$last" ]; then
      skipped+=("$n")
    else
      passed=$((passed + 1))
    fi
    printf 'rc=%s %s :: %s\n' "$rc" "${suites[$i]}" "$(printf '%s' "$last" | cut -c1-110)"
  done
}

# The count, then the skipped suites by name, then each failure's own output.
# ${arr[@]+...}: an empty array under set -u is an error before bash 4.4.
report() {
  local n
  echo "suites: $passed/${#suites[@]} passed, ${#skipped[@]} skipped, ${#failed[@]} failed in $(($(date +%s) - start))s"
  for n in ${skipped[@]+"${skipped[@]}"}; do
    echo "SKIPPED ${suites[$((n - 1))]}"
  done
  for n in ${failed[@]+"${failed[@]}"}; do
    printf '\n---- FAILED rc=%s %s, its last %s lines:\n' "$(cat "$out/$n.rc")" "${suites[$((n - 1))]}" "$fail_lines"
    tail -n "$fail_lines" "$out/$n.out"
  done
}

start=$(date +%s)
suites=()
passed=0
failed=()
skipped=()
run_all
tally
report
if [ "${#failed[@]}" -gt 0 ]; then
  echo "run-suites: exit 1, ${#failed[@]} failed"
  exit 1
fi
if [ "${#skipped[@]}" -gt 0 ]; then
  echo "run-suites: exit 3, none failed, ${#skipped[@]} skipped"
  exit 3
fi
echo "run-suites: exit 0, every suite passed"
exit 0
