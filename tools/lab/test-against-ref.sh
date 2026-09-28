#!/usr/bin/env bash
# tools/lab/test-against-ref.sh -- run one test file against the repo as it
# stood at another ref, to prove the test fails on the code before a fix.
#
# ANSWERS: does a new test actually catch the bug, or would it have passed on
#   the old code too? A test that passes on both trees is not evidence. Exits
#   with the test's own exit code, so NON-ZERO IS THE ANSWER YOU WANT here.
#
# HOW: the whole tree at REF is archived into a scratch directory, then the
#   CURRENT test infrastructure (every file in tests/ not named test_*: the
#   harness, support modules, fixtures) is laid over it, then the test. The
#   test's own helpers therefore always import, and a failure can only come
#   from the code under test. Copying only the code under test made a test
#   whose helpers did not exist at REF fail on import, which reads exactly
#   like the test catching the bug.
#
#   It uses the repo you are standing in, not its own location: this tool and
#   the test can live on different branches, and the test's branch is the
#   one that knows the test.
#
# TESTED AGAINST: tests/test_count_callers.py against the commit that fixed
#   count-callers.py's exclude bug -> passes, and against the commit before
#   it -> fails, though on the old main()'s signature rather than on the bug:
#   the case the NOT TESTED paragraph describes. An earlier form,
#   which archived only check.py and configs/, gave 10, 3 and 4 failures for
#   three check.py tests against the refs before their fixes; those tests
#   passed on the fixed trees. That form also exited 1 at the FIXED commit
#   for any test importing tests/_harness.py, which is why it changed.
#
# NOT TESTED AGAINST: the old tree has no node_modules or vendor/, so a test
#   needing real eslint or phpstan cannot run there. A test whose support
#   files changed their interface since REF runs the new support against old
#   code, which is the point, but can fail for that reason too; read the
#   failure before counting it.
#
# usage, from the tree that holds the test:
#   tools/lab/test-against-ref.sh REF tests/test_x.py [SCRATCH_DIR]
printf '%s' jp-test-at-ref > /proc/$$/comm 2>/dev/null || true
set -eu
ref=$1
test=$2
root=$(git rev-parse --show-toplevel)
out=${3:-$(mktemp -d)}
mkdir -p "$out"
git -C "$root" archive "$ref" | tar -x -C "$out"
mkdir -p "$out/tests"
rm -f "$out"/tests/test_*
for f in "$root"/tests/*; do
  case "$(basename "$f")" in
    test_*|__pycache__) ;;
    *) cp -R "$f" "$out/tests/" ;;
  esac
done
cp "$root/$test" "$out/tests/"
echo "== $test against $ref, in $out"
cd "$out"
python3 "tests/$(basename "$test")"
