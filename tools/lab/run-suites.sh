#!/usr/bin/env bash
# tools/lab/run-suites.sh -- run every JP_TOOLS suite, one line per suite.
#
# ANSWERS: did any suite change outcome between two trees? One line each,
#   "rc=N path :: last line of output", so a before-and-after pair reads side
#   by side or through diff.
#
# TESTED AGAINST: origin/master at 78354db (11 suites) and
#   fix/check-exit-on-unrun-tools (13 suites), 2026-09-14, all rc=0, with
#   test_check_phpstan.py printing its SKIP line because vendor/ was absent.
#
# NOT TESTED AGAINST: a deliberately broken suite. A failing suite should
#   print rc=1 and its own last line, and this exits 1, but that path was not
#   driven on purpose. Also not: Windows; suites that need root (ntfs skips its
#   directory cases itself); a suite that prints nothing, whose line ends
#   "::". The last line is a summary only when the suite prints one last.
#
# usage: tools/lab/run-suites.sh [REPO_ROOT]
#   default: the repo you are standing in, not the one this file lives in,
#   so it runs a fix branch's suites from any worktree.
printf '%s' jp-run-suites > /proc/$$/comm 2>/dev/null || true
set -u
root=${1:-$(git rev-parse --show-toplevel)}
cd "$root" || exit 2
worst=0
for t in tests/test_*.py tests/test_*.sh; do
  [ -e "$t" ] || continue
  case "$t" in
    *.py) out=$(timeout 300 nice python3 "$t" 2>&1); rc=$? ;;
    *.sh) out=$(PYTHON=python3 timeout 300 nice bash "$t" 2>&1); rc=$? ;;
  esac
  [ "$rc" -ne 0 ] && worst=1
  printf 'rc=%s %s :: %s\n' "$rc" "$t" "$(printf '%s' "$out" | tail -n 1 | cut -c1-110)"
done
exit "$worst"
