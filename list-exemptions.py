#!/usr/bin/env python3
"""Inventory every `# pragma: no cover` in a file or tree, and what it claims.

    python list-exemptions.py spool-audit.py
    python list-exemptions.py .            # whole tree
    python list-exemptions.py . --json     # for scripting

WHY THIS IS SEPARATE FROM check.py's `no-cover` CHECK

The check answers "is anything breaking the rule?" and on 2026-08-13 it
answered "no" about a file whose exemptions included `reason: a read`, three
words that satisfy the regex and justify nothing. The rule was passed, not met.

No static check can judge whether a justification is any good. What it can do
is put every claim in one place where a person reads them together, which takes
about ten seconds and is what actually found the two bad ones. So this prints
the whole inventory, including the exemptions that pass, and prints the share
of the file nobody is measuring.

It reads check.py's coverage_exemptions() rather than parsing separately. Two
implementations of "what counts as an exemption" would eventually disagree, and
a rule enforced differently by the gate and the report is worse than either.

Exit status is 0 unless --strict is given, in which case an unjustified branchy
exemption exits 1. Reading the inventory is the point; failing on it is
check.py's job.
"""

from __future__ import annotations

import argparse
import importlib.util
import json
import sys
from pathlib import Path

CHECK_PY = Path(__file__).resolve().parent / "check.py"


def _load_check() -> object:
    """Import the sibling check.py by path.

    Not `import check`: that depends on the caller's working directory and on
    this repo being on sys.path, and the tool is run from anywhere.
    """
    spec = importlib.util.spec_from_file_location("_check_for_exemptions", CHECK_PY)
    if spec is None or spec.loader is None:
        raise SystemExit(f"cannot load {CHECK_PY}")
    module = importlib.util.module_from_spec(spec)
    sys.modules["_check_for_exemptions"] = module
    spec.loader.exec_module(module)
    return module


def gather(target: Path, check_mod: object) -> tuple[list[dict], int]:
    """Exemptions across `target`, and the total line count scanned."""
    if target.is_dir():
        files = sorted(
            p for p in target.rglob("*.py")
            if ".git" not in p.parts and "__pycache__" not in p.parts
        )
    else:
        files = [target]
    rows: list[dict] = []
    total_lines = 0
    for f in files:
        try:
            total_lines += len(f.read_text(encoding="utf-8", errors="replace").splitlines())
        except OSError:
            continue
        rows.extend(check_mod.coverage_exemptions(f))  # type: ignore[attr-defined]
    return rows, total_lines


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

    rows, total_lines = gather(target, _load_check())
    exempt_lines = sum(r["lines"] for r in rows)
    unjustified = [r for r in rows if r["branches"] and not r["reason"]]

    if args.json:
        print(json.dumps({
            "exemptions":   rows,
            "exempt_lines": exempt_lines,
            "total_lines":  total_lines,
            "unjustified":  len(unjustified),
        }, indent=2))
        return 1 if (args.strict and unjustified) else 0

    if not rows:
        print(f"No coverage exemptions under {target} ({total_lines} lines scanned).")
        return 0

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
    return 1 if (args.strict and unjustified) else 0


if __name__ == "__main__":
    sys.exit(main())
