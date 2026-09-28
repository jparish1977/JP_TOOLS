#!/usr/bin/env python3
"""Inventory every `# pragma: no cover` in a file or tree, and what it claims.

    python list-exemptions.py spool-audit.py
    python list-exemptions.py .            # whole tree
    python list-exemptions.py . --json     # for scripting

WHY THIS IS SEPARATE FROM check.py's `no-cover` CHECK

The check answers "is anything breaking the rule?", and a reason like
`reason: a read` satisfies it while justifying nothing. The rule is passed,
not met.

No static check can judge whether a justification is any good. What it can do
is put every claim in one place where a person reads them together. So this
prints the whole inventory, including the exemptions that pass, and the share
of the file nobody is measuring.

It reads jpcheck.coverage's coverage_exemptions() rather than parsing
separately, so the gate and the report cannot disagree about what counts as an
exemption.

Exit status is 0 unless --strict is given, in which case an unjustified branchy
exemption exits 1. Reading the inventory is the point; failing on it is
check.py's job.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

from jpcheck.coverage import coverage_exemptions, is_project_file


def gather(target: Path) -> tuple[list[dict[str, Any]], int]:
    """Exemptions across `target`, and the total line count scanned."""
    if target.is_dir():
        # is_project_file, not a second skip list here, so the gate and the
        # report cannot disagree about what was scanned.
        files = sorted(p for p in target.rglob("*.py") if is_project_file(p, target))
    else:
        files = [target]
    rows: list[dict[str, Any]] = []
    total_lines = 0
    for f in files:
        try:
            total_lines += len(f.read_text(encoding="utf-8", errors="replace").splitlines())
        except OSError as e:
            # Said, not skipped: the exemption share is rows over total lines,
            # and a file left out of both makes the share read as if it had looked.
            print(f"list-exemptions: {f} unreadable ({e}); NOT in the total", file=sys.stderr)
            continue
        rows.extend(coverage_exemptions(f))
    return rows, total_lines


def print_report(rows: list[dict[str, Any]], total_lines: int, exempt_lines: int,
                 unjustified: list[dict[str, Any]]) -> None:
    """The human-readable inventory: every exemption, most branches first."""
    share = (exempt_lines / total_lines * 100) if total_lines else 0.0
    print(f"EXEMPT FROM COVERAGE: {len(rows)} function(s), "
          f"{exempt_lines} of {total_lines} lines ({share:.1f}%)")
    print()
    width = max(len(r["function"]) for r in rows)
    print(f"  {'function':{width}}  {'lines':>5} {'br':>3}  reason")
    for r in sorted(rows, key=lambda x: (-x["branches"], -x["lines"])):
        # A branchless exemption needs no reason: it is self-evidently a
        # wrapper. Saying so beats an empty column that reads as an omission.
        reason = r["reason"] or ("(wrapper, no branches)" if not r["branches"]
                                 else "*** NO REASON GIVEN ***")
        print(f"  {r['function']:{width}}  {r['lines']:>5} {r['branches']:>3}  {reason}")

    pending = [r for r in rows if "PENDING" in r["reason"]]
    print()
    if pending:
        # PENDING means the seams are owed, not that the code cannot be tested.
        # Worth separating, because it is the half of the list that should
        # shrink rather than the half that is legitimately permanent.
        print(f"  {len(pending)} of {len(rows)} say PENDING: seams owed, not untestable.")
    if unjustified:
        print(f"  {len(unjustified)} branchy exemption(s) state no reason.")
    print("  A reason is not a justification. Read them; nothing else can.")


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("target", nargs="?", default=".", help="file or directory")
    ap.add_argument("--json", action="store_true", help="machine-readable output")
    ap.add_argument("--strict", action="store_true",
                    help="exit 1 if any branchy exemption states no reason")
    args = ap.parse_args(argv)

    target = Path(args.target)
    if not target.exists():
        print(f"no such path: {target}", file=sys.stderr)
        return 2

    rows, total_lines = gather(target)
    exempt_lines = sum(r["lines"] for r in rows)
    unjustified = [r for r in rows if r["branches"] and not r["reason"]]

    if args.json:
        print(json.dumps({
            "exemptions":   rows,
            "exempt_lines": exempt_lines,
            "total_lines":  total_lines,
            "unjustified":  len(unjustified),
        }, indent=2))
    elif rows:
        print_report(rows, total_lines, exempt_lines, unjustified)
    else:
        print(f"No coverage exemptions under {target} ({total_lines} lines scanned).")
    return 1 if (args.strict and unjustified) else 0


if __name__ == "__main__":
    sys.exit(main())
