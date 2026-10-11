#!/usr/bin/env python3
"""Which modules hold mutmut's "no tests" mutants, and how many?

    python3 -m mutmut results --all true > results.txt
    python3 tools/lab/mutmut-by-module.py results.txt

ANSWERS: after a mutmut run, per module (the mutant name before `.x_`): how
  many mutants have no test reaching them, out of how many, with killed,
  survived and not-yet-checked beside. The 25 with the most "no tests" first.
  It split the 6,748 "no tests" of the first full JP_TOOLS run (2026-10-10)
  into the three kinds behind #216 and #217.

TESTED AGAINST: that run's results (job 20261011T011109.840Z-84f8e3, 19,161
  mutants): its per-module counts summed to the run's totals (7,538 killed,
  4,863 survived, 6,748 no tests, 12 timeout) for every status.

NOT TESTED AGAINST: results from a mutmut other than 3.8.0, whose line format
  (`    name: status`) it assumes; a class method's mutant names (`xǁClassǁ...`),
  which it groups under the module as named before `.x_`, so methods of one
  class are not split out.
"""
import collections
import sys
from pathlib import Path

# The repo root, so platformtools imports from here.
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from platformtools.procname import name_process


def main() -> int:
    name_process("mutmut-by-mod")
    by: dict[str, collections.Counter[str]] = collections.defaultdict(collections.Counter)
    for line in open(sys.argv[1], encoding="utf-8"):
        name, sep, status = line.rpartition(":")
        if not sep:
            continue
        by[name.strip().split(".x_")[0]][status.strip()] += 1
    total: collections.Counter[str] = collections.Counter()
    for c in by.values():
        total.update(c)
    print("ALL", dict(total))
    for mod, c in sorted(by.items(), key=lambda kv: -kv[1].get("no tests", 0))[:25]:
        print(f"{mod:34} no-tests {c.get('no tests', 0):5}/{sum(c.values()):<5} "
              f"killed {c.get('killed', 0):4} survived {c.get('survived', 0):4} "
              f"pending {c.get('not checked', 0):4}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
