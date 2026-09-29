#!/usr/bin/env bash
# tools/lab/run-mutant-specs.sh -- run every tests/mutants spec against HEAD.
#
# ANSWERS: is every mutant in every spec still killed on target at this
#   commit? One line per spec, the runner's own last line. Each spec's target
#   and test come from the run line in its "about" field, so a spec whose run
#   line is missing is reported, not skipped.
#
# HOW: HEAD is cloned into a scratch directory, removed on exit, interrupt or
#   termination; the runner mutates that clone in place, never this tree. A
#   clone and not an archive, because tests/test_pyproject.py lists tracked
#   files and needs .git.
#
# TESTED AGAINST: this repo's by-path-load branch (731edd4): nine specs, 35
#   mutants, all killed on target, exit 0. Positive control: a clone with a
#   commit making tests/test_pyproject.py's import check compare {} to {}:
#   the pyproject spec reported "6 mutant(s), 1 wrong", rc=1, and the run
#   exited 1.
#
# NOT TESTED AGAINST: uncommitted work, which the clone does not carry --
#   commit first. The runner lives in another repo
#   (tools/lab/mutate-on-target.py); without that checkout this exits 2.
#
# usage, from the checkout: tools/lab/run-mutant-specs.sh
printf '%s' jp-mutant-specs > /proc/$$/comm 2>/dev/null || true
set -u
runner=${MUTATE_ON_TARGET:-$HOME/projects/another repo/tools/lab/mutate-on-target.py}
[ -f "$runner" ] || { echo "no runner at $runner (set MUTATE_ON_TARGET)" >&2; exit 2; }
root=$(git rev-parse --show-toplevel) || exit 2
tree=$(mktemp -d)
trap 'rm -rf "$tree"' EXIT
trap 'exit 130' INT
trap 'exit 143' TERM
git clone -q "$root" "$tree/repo" || exit 2
cd "$tree/repo" || exit 2
worst=0
for spec in tests/mutants/*.json; do
  line=$(python3 -c 'import json, re, sys
m = re.search(r"mutate-on-target\.py\s+\S+\s+(\S+)\s+(\S+)", json.load(open(sys.argv[1])).get("about", ""))
print(" ".join(m.groups()) if m else "")' "$spec")
  if [ -z "$line" ]; then echo "rc=2 $spec :: no run line in its about"; worst=2; continue; fi
  read -r target test <<<"$line"
  out=$(nice python3 "$runner" "$tree/repo" "$target" "$test" "$spec" 2>&1); rc=$?
  echo "rc=$rc $spec :: $(printf '%s\n' "$out" | tail -n 1)"
  [ "$rc" -eq 0 ] || worst=1
done
exit $worst
