#!/usr/bin/env python3
"""Where does a mutmut run's time go: which functions, and which tests?

    python3 tools/lab/mutmut-cost.py MUTANTS_DIR [--top N]

ANSWERS: from a kept mutmut state (MUTANTS_DIR holding mutmut-stats.json and
  the .meta files), per function: its mutants, how many tests reach it, and
  the summed duration of those tests, so the seconds one of its mutants costs
  when every reaching test runs, and that times its mutants; per test: its
  duration, how many functions it reaches, and the mutant-runs it is charged
  across them (its duration times every mutant of every function it reaches).
  The top N of each, largest first, and the totals. It ranks what to make
  cheaper before the next whole-reach run (the gate's slow tail).

TESTED AGAINST: a hand-made state of two functions and three tests whose
  costs were worked out on paper first, every printed figure matching
  (positive control: the function reached by the slowest test ranks first).
  Then the nightly state of 2026-10-14 (fleet-ci volume mutmut-state).

NOT TESTED AGAINST: mutmut other than 3.8.0, whose stats keys
  (tests_by_mangled_function_name, duration_by_test) and .meta key
  (exit_code_by_key) it reads. The seconds are an UPPER BOUND: mutmut stops a
  mutant at its first failing test, so a killed mutant usually costs less
  than every reaching test; only a survivor costs all of them. A test's
  recorded duration is from mutmut's trace, under its tracing, not a plain
  run. Class methods (mangled names with the class) are counted as mutmut
  names them, each on its own.
"""
import argparse
import collections
import json
import sys
from pathlib import Path

# The repo root, so platformtools imports from here.
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from platformtools.procname import name_process


def mutants_by_function(mutants: Path) -> collections.Counter[str]:
    """Every mutant in MUTANTS' .meta files, counted by the function it is of."""
    counts: collections.Counter[str] = collections.Counter()
    for meta in mutants.rglob("*.meta"):
        for key in json.loads(meta.read_bytes()).get("exit_code_by_key", {}):
            counts[key.partition("__mutmut_")[0]] += 1
    return counts


def main() -> int:
    name_process("mutmut-cost")
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    p.add_argument("mutants", type=Path)
    p.add_argument("--top", type=int, default=20)
    args = p.parse_args()
    stats = json.loads((args.mutants / "mutmut-stats.json").read_bytes())
    reach: dict[str, list[str]] = stats.get("tests_by_mangled_function_name", {})
    took: dict[str, float] = stats.get("duration_by_test", {})
    counts = mutants_by_function(args.mutants)

    rows = []
    charged: collections.Counter[str] = collections.Counter()
    functions_of: collections.Counter[str] = collections.Counter()
    for fn, n in counts.items():
        tests = reach.get(fn, [])
        per = sum(took.get(t, 0.0) for t in tests)
        rows.append((per * n, fn, n, len(tests), per))
        for t in tests:
            charged[t] += took.get(t, 0.0) * n
            functions_of[t] += 1
    rows.sort(reverse=True)
    total = sum(r[0] for r in rows)
    print(f"{len(counts):,} functions, {sum(counts.values()):,} mutants, {len(took):,} timed tests; "
          f"every mutant running every reaching test: {total:,.0f} s, {total / 3600:,.1f} h (an upper bound)")
    print(f"\nfunctions by mutants x reaching seconds (top {args.top}):")
    print(f"  {'est h':>7} {'share':>6} {'mutants':>7} {'tests':>5} {'s/mutant':>8}  function")
    for cost, fn, n, k, per in rows[:args.top]:
        print(f"  {cost / 3600:7.2f} {cost / total:6.1%} {n:7,} {k:5} {per:8.2f}  {fn}")
    print(f"\ntests by mutant-runs charged to them (top {args.top}):")
    print(f"  {'est h':>7} {'share':>6} {'secs':>7} {'fns':>5}  test")
    for t, cost in charged.most_common(args.top):
        print(f"  {cost / 3600:7.2f} {cost / total:6.1%} {took.get(t, 0.0):7.2f} {functions_of[t]:5}  {t}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
