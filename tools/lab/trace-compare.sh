#!/usr/bin/env bash
# tools/lab/trace-compare.sh -- mutmut's stats pass run serially and across
# workers on one tree, then compared.
#
#   tools/lab/trace-compare.sh WORKERS OUTDIR      (from a checkout to test)
#
# ANSWERS: does mutmut-clean.py's stats pass across WORKERS lose any of the
#   reach mutmut's own serial pass records (which tests reach which function,
#   and each function's callers), and how long does each take? Exit 0 only
#   when both passes exited 0 and no test or caller the serial pass recorded
#   is missing from the parallel one; the last line says so.
#
#   Extra reach in the parallel pass is listed, not failed. A function behind
#   process-wide state (functools.cache, a module-level instance made at
#   import) records only the first test that reaches it in a process: one
#   test in mutmut's single serial process, the first in each worker in
#   parallel. Measured at c8fc44b: 2 of 591 functions (jpcheck.common._git,
#   under the cached tool_roots, and Directive.__init__), every serial test
#   kept. Extra reach runs more tests against those functions' mutants,
#   never fewer.
#
# HOW: for each mode, mutmut-gate.py's prepare() makes mutants/ fresh, then
#   mutmut-clean.py runs mutmut with a mutant pattern that names nothing, so
#   mutmut generates mutants and runs the stats pass, and tests no mutant.
#   Serial is MUTMUT_CLEAN_WORKERS unset: mutmut's own pass. Each pass's
#   mutants/mutmut-stats.json and log are kept in OUTDIR.
#
# TESTED AGAINST: nothing yet beyond its own runs; its answer is the
#   comparison it prints, and a run that loses reach must exit 1.
#
# NOT TESTED AGAINST: a checkout with no mutants/ worktree support (mutmut-
#   gate.py's prepare needs git); test durations are compared by total only,
#   since they differ run to run.
printf '%s' jpt-trace-cmp > /proc/$$/comm 2>/dev/null || true
set -u
workers=${1:?WORKERS}
out=${2:?OUTDIR}
mkdir -p "$out"

prepare() {
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
}

for mode in serial parallel; do
  prepare
  start=$(date +%s)
  if [ "$mode" = serial ]; then
    env -u MUTMUT_CLEAN_WORKERS python mutmut-clean.py run 'nomatch_zz.*' > "$out/$mode.log" 2>&1
  else
    MUTMUT_CLEAN_WORKERS=$workers python mutmut-clean.py run 'nomatch_zz.*' > "$out/$mode.log" 2>&1
  fi
  rc=$?
  end=$(date +%s)
  # A pattern naming no mutant ends mutmut with this assertion once the stats
  # pass is done and saved: for this run, that is the pass succeeding.
  if [ "$rc" = 1 ] && tail -n 3 "$out/$mode.log" | grep -q "^Filter: ('nomatch_zz.\*',)$"; then
    rc=0
  fi
  echo "$mode: rc=$rc, $((end - start)) s (mutant generation included)"
  echo "$rc $((end - start))" > "$out/$mode.rc"
  cp mutants/mutmut-stats.json "$out/$mode-stats.json" 2>/dev/null || echo "$mode: no mutmut-stats.json"
done

python - "$out" <<'PY'
import json
import sys
from pathlib import Path

out = Path(sys.argv[1])
rcs = {m: (out / f"{m}.rc").read_text().split()[0] for m in ("serial", "parallel")}
s, p = (json.loads((out / f"{m}-stats.json").read_text()) for m in ("serial", "parallel"))
lost_any, extra_any = False, False
for key in ("tests_by_mangled_function_name", "function_dependencies"):
    a = {k: set(v) for k, v in s[key].items() if v}
    b = {k: set(v) for k, v in p[key].items() if v}
    lost = {k: sorted(a[k] - b.get(k, set())) for k in a if a[k] - b.get(k, set())}
    extra = {k: sorted(b[k] - a.get(k, set())) for k in b if b[k] - a.get(k, set())}
    print(f"{key}: serial {len(a)}, parallel {len(b)}; LOST in parallel for {len(lost)},"
          f" extra in parallel for {len(extra)}")
    for k, v in sorted(lost.items())[:10]:
        print(f"   LOST {k}: {v}")
    for k, v in sorted(extra.items())[:10]:
        print(f"   extra {k}: {v}")
    lost_any, extra_any = lost_any or bool(lost), extra_any or bool(extra)
ts, tp = (sum(d["duration_by_test"].values()) for d in (s, p))
print(f"tests timed: serial {len(s['duration_by_test'])}, parallel {len(p['duration_by_test'])};"
      f" summed test time serial {ts:.0f} s, parallel {tp:.0f} s")
ok = not lost_any and rcs == {"serial": "0", "parallel": "0"}
verdict = "REACH LOST" if lost_any else ("NOTHING LOST, EXTRA LISTED" if extra_any else "SAME")
print(f"trace-compare: {verdict}, rc serial {rcs['serial']} parallel {rcs['parallel']}: {'PASS' if ok else 'FAIL'}")
sys.exit(0 if ok else 1)
PY
