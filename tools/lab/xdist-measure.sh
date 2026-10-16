#!/usr/bin/env bash
# tools/lab/xdist-measure.sh -- the whole suite's wall time under pytest,
# serially and across pytest-xdist workers.
#
# ANSWERS: how long the suite takes as mutmut's clean run runs it (-x -q, no
#   random order, no cache, the one deselected test), serially and with
#   WORKERS xdist workers distributed by file, and whether both exit 0. The
#   last line of each mode is pytest's own summary.
#
# HOW: a throwaway venv in a mktemp directory, given requirements-dev.txt,
#   so the shared venv is untouched; each mode's output goes to that
#   directory, which is removed on exit. Measurement only: it changes nothing
#   shared.
#
# TESTED AGAINST: nothing yet in this form beyond its own run; its earlier
#   copy measured the suite serially and at 8 workers on a 32-core box.
#
# NOT TESTED AGAINST: a box with no network for pip, which exits 2. Windows.
#
# usage, from anywhere: tools/lab/xdist-measure.sh TREE [WORKERS]
printf '%s' jpt-xdist-meas > /proc/$$/comm 2>/dev/null || true
set -u
tree=${1:?usage: xdist-measure.sh TREE [WORKERS]}
workers=${2:-8}
work=$(mktemp -d)
trap 'rm -rf "$work"' EXIT
python3 -m venv "$work/venv" && "$work/venv/bin/pip" install -q -r "$tree/requirements-dev.txt" || exit 2
cd "$tree" || exit 2
args=(-x -q -p no:randomly -p no:random-order -p no:cacheprovider
      --deselect tests/test_pyproject.py::test_every_import_is_declared tests)
for mode in serial "xdist$workers"; do
  extra=()
  [ "$mode" = serial ] || extra=(-n "$workers" --dist loadfile)
  start=$(date +%s.%N)
  PATH="$work/venv/bin:$PATH" "$work/venv/bin/python" -m pytest "${args[@]}" "${extra[@]}" > "$work/$mode.out" 2>&1
  rc=$?
  end=$(date +%s.%N)
  printf '%s: rc %s, %.1f s, %s\n' "$mode" "$rc" "$(echo "$end - $start" | bc)" "$(tail -1 "$work/$mode.out")"
done
