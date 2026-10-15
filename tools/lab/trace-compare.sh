#!/usr/bin/env bash
# tools/lab/trace-compare.sh -- mutmut's stats pass run serially and across
# workers on one tree, then compared.
#
#   tools/lab/trace-compare.sh WORKERS OUTDIR      (from a checkout to test)
#
# ANSWERS: does mutmut-clean.py's stats pass across WORKERS record exactly
#   what mutmut's own serial pass records (which tests reach which function,
#   and each function's callers), and how long does each take? Exit 0 only
#   when both passes exited 0 and both maps are equal; the last line says so.
#
# HOW: for each mode, mutmut-gate.py's prepare() makes mutants/ fresh, then
#   mutmut-clean.py runs mutmut with a mutant pattern that names nothing, so
#   mutmut generates mutants and runs the stats pass, and tests no mutant.
#   Serial is MUTMUT_CLEAN_WORKERS unset: mutmut's own pass. Each pass's
#   mutants/mutmut-stats.json and log are kept in OUTDIR.
#
# TESTED AGAINST: nothing yet beyond its own runs; its answer is the
#   comparison it prints, and a run where the maps differ must exit 1.
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
same = True
for key in ("tests_by_mangled_function_name", "function_dependencies"):
    a = {k: sorted(v) for k, v in s[key].items() if v}
    b = {k: sorted(v) for k, v in p[key].items() if v}
    only_s, only_p = sorted(set(a) - set(b)), sorted(set(b) - set(a))
    differ = sorted(k for k in set(a) & set(b) if a[k] != b[k])
    print(f"{key}: serial {len(a)}, parallel {len(b)}; only serial {len(only_s)}, only parallel {len(only_p)},"
          f" different {len(differ)}")
    for k in (only_s + only_p + differ)[:10]:
        print(f"   {k}: serial {a.get(k)} parallel {b.get(k)}")
    same = same and not (only_s or only_p or differ)
ts, tp = (sum(d["duration_by_test"].values()) for d in (s, p))
print(f"tests timed: serial {len(s['duration_by_test'])}, parallel {len(p['duration_by_test'])};"
      f" summed test time serial {ts:.0f} s, parallel {tp:.0f} s")
ok = same and rcs == {"serial": "0", "parallel": "0"}
print(f"trace-compare: {'SAME' if same else 'DIFFERENT'}, rc serial {rcs['serial']} parallel {rcs['parallel']}:"
      f" {'PASS' if ok else 'FAIL'}")
sys.exit(0 if ok else 1)
PY
