#!/usr/bin/env bash
# tools/lab/coverage-like-ci.sh -- spool-audit coverage, measured as CI does.
#
# ANSWERS: what CI's gated coverage number (spool-audit.py + spoolaudit/, gate
#   90) will be, before pushing. The suites run under coverage with subprocess
#   tracing, as the workflow does, so the acceptance and invariant suites,
#   which drive the tool as a subprocess, count.
#
# HOW: subprocess tracing needs a .pth file in site-packages, which this will
#   not write into a working Python. It makes a throwaway venv with the
#   coverage version requirements-dev.txt pins, writes the .pth there, and
#   removes the venv and every .coverage file on exit, interrupt or
#   termination.
#
# TESTED AGAINST: this repo's by-path-load branch: TOTAL 97%, classify.py,
#   render.py, config.py and model.py 100%, purge.py and read.py 93%,
#   spool-audit.py 97%, exit 0, and no venv, .coverage file or /tmp entry
#   left afterwards. The same numbers CI's Coverage step printed for master.
#
# NOT TESTED AGAINST: the .pth's effect, by a run without it (the workflow's
#   own comment says spool-audit.py measured 71% untraced, 82% traced, on an
#   older tree). A box with no network for pip, which exits 2. Windows.
#
# usage, from the checkout: tools/lab/coverage-like-ci.sh

# The coverage pin from requirements-dev.txt, or exit 2 when there is none.
coverage_pin() {
  local pin
  pin=$(python3 -c 'import re, sys
m = re.search(r"^coverage==\S+", open("requirements-dev.txt").read(), re.M)
print(m.group() if m else "")')
  [ -n "$pin" ] || { echo "requirements-dev.txt pins no coverage" >&2; exit 2; }
  printf '%s' "$pin"
}

# A venv in $venv with PIN installed and subprocess tracing switched on.
traced_venv() {
  python3 -m venv "$venv" && "$venv/bin/pip" install -q "$1" || exit 2
  local site
  site=$("$venv/bin/python" -c "import sysconfig; print(sysconfig.get_paths()['purelib'])")
  echo "import coverage; coverage.process_startup()" > "$site/zz-subprocess-coverage.pth"
}

# Every spool-audit suite under coverage, then the gated report; 1 on any failure.
measure() {
  local rc=0 t
  for t in tests/test_spool_audit_*.py; do
    "$venv/bin/python" -m coverage run "$t" > /dev/null || { echo "FAILED $t"; rc=1; }
  done
  PYTHON="$venv/bin/python" bash tests/test_spool_audit_acceptance.sh > /dev/null \
    || { echo "FAILED acceptance"; rc=1; }
  "$venv/bin/python" -m coverage combine -q
  "$venv/bin/python" -m coverage report --include='spool-audit.py,spoolaudit/*' --fail-under=90 \
    || rc=1
  return $rc
}

printf '%s' jp-coverage-ci > /proc/$$/comm 2>/dev/null || true
set -u
root=$(git rev-parse --show-toplevel) || exit 2
cd "$root" || exit 2
pin=$(coverage_pin) || exit 2
venv=$(mktemp -d)
trap 'rm -rf "$venv" .coverage .coverage.*' EXIT
trap 'exit 130' INT
trap 'exit 143' TERM
traced_venv "$pin"
export COVERAGE_PROCESS_START="$root/.coveragerc"
rm -f .coverage .coverage.*
measure
