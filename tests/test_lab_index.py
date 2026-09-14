#!/usr/bin/env python3
"""
JP_TOOLS/tests/test_lab_index.py
tools/lab/README.md indexes every tool in tools/lab, and nothing that is gone.

WHY IT IS A TEST, NOT A HABIT
    Joe, 2026-09-14: "if the methodology covers the labs it should say an idex
    is required and must be maintained", "if we have a lab it should have an
    index", "those indexes are what i hope will keep yall from duplicating
    effort", and "the idea is once i start working out proper prompting i
    suspect you will all stop rebuilding the same tools over and over".

    An index kept by hand goes stale the first time someone is in a hurry:
    claude-config's read 72 of 123 tools unlisted on 2026-09-14. A seat
    checking for prior art then reads a list that is wrong and builds the tool
    again. So this fails when a tool lands without a row, at the moment its
    author is still there to write one.

WHAT IT DOES NOT CHECK: whether a row's description is any good. A row that is
merely present still tells the next seat the tool exists, which is the job.

Controls, in a throwaway copy, so the check cannot pass by reading nothing: a
planted unlisted file must be reported, a row whose file is gone must be
reported, and the real index must yield at least one row.

    python3 tests/test_lab_index.py
"""

import re
import shutil
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
LAB = ROOT / "tools" / "lab"
ROW = re.compile(r"^\|\s*`([^`]+)`", re.MULTILINE)

fails = 0
checks = 0


def check(what: str, ok: bool, detail: str = "") -> None:
    global fails, checks
    checks += 1
    if not ok:
        fails += 1
    print(f"  [{' ok ' if ok else 'FAIL'}] {what}")
    if not ok and detail:
        print(f"         {detail}")


def rows(lab: Path) -> set[str]:
    return set(ROW.findall((lab / "README.md").read_text(encoding="utf-8")))


def gaps(lab: Path) -> tuple[list[str], list[str]]:
    """(tools with no row, rows with no tool)."""
    files = sorted(p.name for p in lab.iterdir() if p.is_file() and p.name != "README.md")
    listed = rows(lab)
    return [f for f in files if f not in listed], sorted(listed - set(files))


def main() -> int:
    print("The real index:")
    listed = rows(LAB)
    check("the index yields rows at all (a broken parse would read nothing)",
          len(listed) > 0, "no rows parsed from tools/lab/README.md")
    unlisted, stale = gaps(LAB)
    check("every tool in tools/lab has a row", not unlisted,
          f"unlisted: {unlisted}; add a row to tools/lab/README.md")
    check("every row names a tool that exists", not stale,
          f"rows with no file: {stale}")

    print("\nControls, in a throwaway copy:")
    with tempfile.TemporaryDirectory() as td:
        lab = Path(td) / "lab"
        shutil.copytree(LAB, lab, ignore=shutil.ignore_patterns("__pycache__"))
        (lab / "planted-probe.py").write_text("# planted\n", encoding="utf-8")
        unlisted, _ = gaps(lab)
        check("a planted tool with no row is reported", unlisted == ["planted-probe.py"],
              f"got {unlisted}")
        gone = sorted(rows(lab))[0]
        (lab / gone).unlink()
        _, stale = gaps(lab)
        check("a row whose tool is gone is reported", stale == [gone], f"got {stale}")

    print(f"\n{checks - fails}/{checks} passed")
    return 1 if fails else 0


if __name__ == "__main__":
    sys.exit(main())
