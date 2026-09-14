#!/usr/bin/env bash
# AS RUN: this is tar.sh exactly as it stood in session 6fbf59c4 (jp-tools) scratch,
# 2026-09-14 03:42 local: the first draft of tools/lab/test-against-ref.sh, which
# differs from it. Kept because the draft is what produced the numbers below.
# tools/lab/test-against-ref.sh -- run one test file against check.py as it
# stood at another ref, to prove the test fails on the code before a fix.
#
# ANSWERS: does a new test actually catch the bug, or would it have passed on
#   the old code too? A test that passes on both trees is not evidence. Exits
#   with the test's own exit code, so NON-ZERO IS THE ANSWER YOU WANT here.
#
# TESTED AGAINST, 2026-09-14, by hand before this file existed:
#   tests/test_check_exit_codes.py at origin/master  -> 10 failures
#   tests/test_check_prettier.py   at 6e9dbf1        ->  3 failures
#   tests/test_check_exit_codes.py at 2b9d6d0        ->  4 failures
#   Each test passed on the fixed branch, which is the positive control.
#
# NOT TESTED AGAINST: tests that need anything beyond check.py, configs/ and
#   the two node runners (the spool-audit suites would find nothing to run);
#   a test that imports another repo file. The old tree has no node_modules
#   or vendor/, so a test needing real eslint or phpstan cannot run there.
#   This script itself was not run after being written down.
#
# usage: tools/lab/test-against-ref.sh REF tests/test_x.py [SCRATCH_DIR]
printf '%s' jp-test-at-ref > /proc/$$/comm 2>/dev/null || true
set -eu
ref=$1
test=$2
root=$(git -C "$(dirname "$0")" rev-parse --show-toplevel)
out=${3:-$(mktemp -d)}
mkdir -p "$out/tests"
paths=(check.py configs)
for p in jp_eslint.mjs jp_stylelint.mjs; do
  if git -C "$root" cat-file -e "$ref:$p" 2>/dev/null; then
    paths+=("$p")
  fi
done
git -C "$root" archive "$ref" "${paths[@]}" | tar -x -C "$out"
cp "$root/$test" "$out/tests/"
echo "== $test against $ref, in $out"
cd "$out"
python3 "tests/$(basename "$test")"
