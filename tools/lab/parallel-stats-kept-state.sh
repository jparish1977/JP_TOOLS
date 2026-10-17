#!/usr/bin/env bash
# tools/lab/parallel-stats-kept-state.sh -- mutmut-clean's stats pass across
# workers when mutmut already has stats, as a pull request's kept state has.
#
#   tools/lab/parallel-stats-kept-state.sh WORKERS PATTERN TESTFILE    (from a checkout to test)
#
# ANSWERS: does the parallel stats pass still load its plugin in the workers
#   when mutmut lists the tests first? With stats present, mutmut 3.8 runs an
#   in-process `pytest --collect-only` (list_all_tests) and only then the stats
#   pass for the tests it calls new. xdist gives a local worker a copy of
#   sys.path frozen when xdist was first imported, which is that listing, so
#   the plugin's directory must already be on sys.path then. CI's PR job with
#   kept state is this case; a run from fresh state never lists first.
#
# HOW: mutmut-gate.py's prepare() makes mutants/ fresh; run 1 is
#   `MUTMUT_CLEAN_WORKERS=WORKERS mutmut-clean.py run PATTERN` (fresh stats);
#   then TESTFILE's tests are taken out of mutants/mutmut-stats.json, as
#   mutmut-gate.py does for an edited test file, so mutmut calls them new;
#   run 2 is the same command. Prints each run's exit code, whether run 2
#   said it reran stats for new tests, and any "Error importing plugin" line.
#   Exit 0 only when both runs exit 0 and run 2 did rerun stats.
#
# TESTED AGAINST: 8 workers, "mutmut-clean.x_merge_stats*",
#   tests/test_mutmut_clean.py (5 tests taken out), on a 32-thread box. At
#   eb821b5 (run_stats put the directory on sys.path): run 1 rc=0, run 2
#   rc=1 with CI run 457's ImportError. At c1f4ec9 (main() puts it there
#   first): both rc=0, run 2 reran stats for the new tests.
#
# NOT TESTED AGAINST: a kept state restored from CI's volume (this makes its
#   own), or WORKERS above the box's cores.
printf '%s' jpt-ps-kept > /proc/$$/comm 2>/dev/null || true
set -u
workers=${1:?WORKERS}
pattern=${2:?PATTERN}
testfile=${3:?TESTFILE, as the stats name it (tests/test_x.py)}
log=$(mktemp)
trap 'rm -f "$log"' EXIT

rm -rf mutants
git worktree prune
python - <<'PY'
import importlib.util
import pathlib
import sys

spec = importlib.util.spec_from_file_location("gate", "mutmut-gate.py")
gate = importlib.util.module_from_spec(spec)
sys.modules["gate"] = gate
spec.loader.exec_module(gate)
gate.prepare(pathlib.Path(".").resolve())
PY

MUTMUT_CLEAN_WORKERS=$workers python mutmut-clean.py run "$pattern" > "$log" 2>&1
rc1=$?
echo "run 1 (fresh stats): rc=$rc1"

python - "$testfile" <<'PY'
import json
import sys

path, prefix = "mutants/mutmut-stats.json", sys.argv[1] + "::"
s = json.load(open(path))
for tests in s["tests_by_mangled_function_name"].values():
    tests[:] = [t for t in tests if not t.startswith(prefix)]
gone = [t for t in s["duration_by_test"] if t.startswith(prefix)]
for t in gone:
    del s["duration_by_test"][t]
json.dump(s, open(path, "w"))
print(f"took {len(gone)} test(s) of {sys.argv[1]} out of the stats")
PY

MUTMUT_CLEAN_WORKERS=$workers python mutmut-clean.py run "$pattern" > "$log" 2>&1
rc2=$?
reran=0
case "$(cat "$log")" in *"new tests, rerunning stats collection"*) reran=1 ;; esac
echo "run 2 (kept stats, new tests): rc=$rc2, reran stats for new tests: $reran"
while IFS= read -r line; do
  case "$line" in *"Error importing plugin"*) echo "   $line"; break ;; esac
done < "$log"
[ "$rc1" = 0 ] && [ "$rc2" = 0 ] && [ "$reran" = 1 ]
