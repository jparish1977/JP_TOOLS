#!/usr/bin/env python3
"""Inventory every suppression in a file or tree, every language: what it turns off, and why.

    python list-suppressions.py fix.py
    python list-suppressions.py .            # whole tree
    python list-suppressions.py . --json     # for scripting

What it lists, in each language check.py detects:

    Python   # noqa: CODES -- why          # type: ignore[code]
    shell    # shellcheck disable=SC2086  # why
    C/C++    // cppcheck-suppress id ; why
    PHP      // @phpstan-ignore id (why)   // @phpstan-ignore-next-line -- why
             // phpcs:ignore Sniff.Name -- why
    JS       // eslint-disable-next-line rule -- why
    CSS      /* stylelint-disable rule -- why */
    HTML     eslint and stylelint both

WHY THIS IS SEPARATE FROM check.py's RULES

check.py refuses a suppression that states no reason (noqa-no-reason,
directive-no-reason), a blanket noqa, and any reason on a blind catch. It
cannot judge whether a reason is any good, and it says nothing about the
suppressions that pass. So one with a reason is invisible to every report, and
a weak reason is the easy way through.

This prints every one, the ones that pass included, counted by kind, so a
person reads the claims together: the same split as list-exemptions.py for
coverage. It reads the markers the rules read, jpcheck.suppressions'
noqa_markers() and jpcheck.directives' directives(), so the gate and the report
cannot disagree about what a suppression is. A reason is shown only when it is
in the form its tool honours; one in another form is shown as none, since the
tool drops it. A `# type: ignore` is listed too; check.py rules on none of them.

Not listed: Python's file-level forms (`# ruff: noqa`, `# flake8: noqa`),
which noqa_markers() does not read.

Exit status (jpexit): 0, unless --strict is given and a suppression is one
check.py refuses whatever the line (no reason; a noqa naming no codes), which
is 1; 2 for a path that does not exist. Reading the inventory is the point;
failing on it is check.py's job.
"""

from __future__ import annotations

import argparse
import json
import sys
from collections import Counter
from pathlib import Path
from typing import Any

# The packages beside this file import when another program loads it by path.
sys.path.insert(0, str(Path(__file__).resolve().parent))

import jpexit
from jpcheck.coverage import is_project_file
from jpcheck.directives import _DIRECTIVES, directives
from jpcheck.files import _detect_lang
from jpcheck.suppressions import noqa_markers
from platformtools.procname import name_process


def _markers(f: Path, lang: str) -> list[dict[str, Any]]:
    """One file's suppressions as rows: kind, line, what it names, reason."""
    if lang == "python":
        return [{"kind": m["kind"], "line": m["line"], "codes": m["codes"], "reason": m["reason"]}
                for m in noqa_markers(f)]
    try:
        text = f.read_text(encoding="utf-8", errors="replace")
    except OSError as e:
        # Said, not skipped: a file left out makes the inventory shorter than the truth.
        print(f"list-suppressions: {f} unreadable ({e}); NOT inventoried", file=sys.stderr)
        return []
    return [{"kind": d["name"], "line": d["line"], "codes": [d["directive"]], "reason": d["reason"]}
            for d in directives(text, lang)]


def gather(target: Path) -> tuple[list[dict[str, Any]], int]:
    """Every suppression under TARGET, each with its file, and how many files were scanned."""
    if target.is_dir():
        # is_project_file, not a second skip list, as list-exemptions.py does.
        candidates = sorted(p for p in target.rglob("*") if p.is_file() and is_project_file(p, target))
    else:
        candidates = [target]
    rows: list[dict[str, Any]] = []
    scanned = 0
    for f in candidates:
        lang = _detect_lang(str(f))
        if lang != "python" and lang not in _DIRECTIVES:
            continue
        scanned += 1
        name = f.relative_to(target).as_posix() if target.is_dir() else str(f)
        rows.extend({"file": name, **m} for m in _markers(f, lang))
    return rows, scanned


def refused(row: dict[str, Any]) -> bool:
    """One check.py refuses whatever the line: no reason, or a noqa naming no codes."""
    if row["kind"] == "type-ignore":
        return False
    return not row["reason"] or (row["kind"] == "noqa" and not row["codes"])


def print_report(rows: list[dict[str, Any]], scanned: int) -> None:
    """The human-readable inventory: counts by kind, then every suppression."""
    files = len({r["file"] for r in rows})
    print(f"SUPPRESSIONS: {len(rows)} in {files} of {scanned} file(s) scanned")
    kinds = Counter(r["kind"] for r in rows)
    print("  by kind: " + ", ".join(f"{k} {n}" for k, n in kinds.most_common()))
    codes = Counter(c for r in rows if r["kind"] == "noqa" for c in r["codes"] or ["(no codes)"])
    if codes:
        print("  noqa by code: " + ", ".join(f"{c} {n}" for c, n in codes.most_common()))
    print()
    width = max(len(f"{r['file']}:{r['line']}") for r in rows)
    what = max(len(",".join(r["codes"]) or "(none)") for r in rows)
    for r in sorted(rows, key=lambda x: (x["file"], x["line"])):
        where = f"{r['file']}:{r['line']}"
        names = ",".join(r["codes"]) or "(none)"
        print(f"  {where:{width}}  {names:{what}}  {r['reason'] or '*** NO REASON GIVEN ***'}")
    bad = [r for r in rows if refused(r)]
    print()
    if bad:
        print(f"  {len(bad)} that check.py refuses (no reason, or a noqa naming no codes).")
    print("  A reason is not a justification. Read them; nothing else can.")


def main(argv: list[str] | None = None) -> int:
    name_process("list-suppress")
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("target", nargs="?", default=".", help="file or directory")
    ap.add_argument("--json", action="store_true", help="machine-readable output")
    ap.add_argument("--strict", action="store_true",
                    help="exit 1 if any suppression is one check.py refuses whatever the line")
    args = ap.parse_args(argv)

    target = Path(args.target)
    if not target.exists():
        print(f"no such path: {target}", file=sys.stderr)
        return jpexit.NOT_RUN

    rows, scanned = gather(target)
    bad = [r for r in rows if refused(r)]
    if args.json:
        print(json.dumps({"suppressions": rows, "files_scanned": scanned, "refused": len(bad)},
                         indent=2))
    elif rows:
        print_report(rows, scanned)
    else:
        print(f"No suppressions under {target} ({scanned} file(s) scanned).")
    return jpexit.FINDINGS if (args.strict and bad) else jpexit.OK


if __name__ == "__main__":
    sys.exit(main())
