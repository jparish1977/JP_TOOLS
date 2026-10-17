#!/usr/bin/env bash
# tools/lab/verdict-compare.sh -- one function's mutant verdicts with mutmut's
# serial stats pass and with the stats pass across workers, compared.
#
#   tools/lab/verdict-compare.sh WORKERS PATTERN OUTDIR    (from a checkout to test)
#
# ANSWERS: when trace-compare.sh says the stats pass across WORKERS lost a test
#   for a function, does that change any of that function's verdicts? PATTERN
#   is a mutmut mutant pattern for the function, ending in `*` (for a method,
#   `module.xǁClassǁname*`). Exit 0 only when both runs exited 0, both judged
#   at least one mutant, and every mutant's verdict is the same; the last line
#   says so. The lost test then cost nothing for this function; a different
#   verdict means the parallel trace is wrong for it.
#
# HOW: for each mode, mutmut-gate.py's prepare() makes mutants/ fresh, then
#   mutmut-clean.py runs PATTERN's mutants: serial with MUTMUT_CLEAN_WORKERS
#   unset (mutmut's own stats pass), parallel with it set to WORKERS. Each
#   mode's `mutmut results --all true` lines for PATTERN are kept in OUTDIR
#   with its log, then the two are compared mutant by mutant.
#
# TESTED AGAINST: nothing yet beyond its own runs.
#
# NOT TESTED AGAINST: a function whose tests are slow enough to time out in
#   one mode and not the other: a timeout counts as a different verdict, so
#   read the log before believing such a difference.
printf '%s' jpt-verdict-cmp > /proc/$$/comm 2>/dev/null || true
set -u
workers=${1:?WORKERS}
pattern=${2:?PATTERN}
out=${3:?OUTDIR}
mkdir -p "$out"
prefix=${pattern%\*}

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
  if [ "$mode" = serial ]; then
    env -u MUTMUT_CLEAN_WORKERS python mutmut-clean.py run "$pattern" > "$out/$mode.log" 2>&1
  else
    MUTMUT_CLEAN_WORKERS=$workers python mutmut-clean.py run "$pattern" > "$out/$mode.log" 2>&1
  fi
  echo "$?" > "$out/$mode.rc"
  python -m mutmut results --all true > "$out/$mode-results.txt" 2>&1
done

python - "$out" "$prefix" <<'PY'
import sys
from pathlib import Path

out, prefix = Path(sys.argv[1]), sys.argv[2]
verdicts = {}
for mode in ("serial", "parallel"):
    lines = (out / f"{mode}-results.txt").read_text().splitlines()
    pairs = (line.strip().rsplit(": ", 1) for line in lines if line.strip().startswith(prefix))
    verdicts[mode] = {name: verdict for name, verdict in pairs}
rcs = {m: (out / f"{m}.rc").read_text().strip() for m in ("serial", "parallel")}
s, p = verdicts["serial"], verdicts["parallel"]
for mode, v in verdicts.items():
    counts = {}
    for verdict in v.values():
        counts[verdict] = counts.get(verdict, 0) + 1
    print(f"{mode}: rc={rcs[mode]}, {len(v)} mutants: {dict(sorted(counts.items()))}")
differ = sorted(n for n in s.keys() | p.keys() if s.get(n) != p.get(n))
for name in differ:
    print(f"   DIFFERS {name}: serial {s.get(name)}, parallel {p.get(name)}")
ok = rcs == {"serial": "0", "parallel": "0"} and bool(s) and bool(p) and not differ
print(f"verdict-compare: {len(differ)} differ of {len(s.keys() | p.keys())}: {'PASS' if ok else 'FAIL'}")
sys.exit(0 if ok else 1)
PY
