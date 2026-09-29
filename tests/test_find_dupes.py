#!/usr/bin/env python3
"""
JP_TOOLS/tests/test_find_dupes.py
find-dupes.php, run as a user runs it, in each of its modes.

WHAT IS CHECKED
    A fixture of two directories: A holds a1 and a2 (the same bytes) and a3;
    B holds b1 (the same bytes as a1) and b2. Then, each by exit code and what
    it prints:
      - one directory, --json: one duplicate group, a1 and a2
      - two directories, --json: a compare, one match, one unique each side
      - --stream with --db: the same group, found through the database
      - --stream with --workers over both: one group of three; the workers'
        own console lines come before the JSON
      - no --json: console lines
      - --help; a flag given no value; no directory; --stream without --db

    A characterisation (METHODOLOGY 9, item 12): it pins what the script does,
    so a restructuring of it can be shown to change nothing.

    Needs php and the private iteration8/utilities library in vendor/ (CI's
    Python job installs it); without them it prints SKIP, which is not a pass.

    python3 tests/test_find_dupes.py
"""

import json
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any

from _harness import ROOT, check, run

SCRIPT = ROOT / "find-dupes.php"


def library_installed() -> bool:
    if not shutil.which("php") or not (ROOT / "vendor" / "autoload.php").is_file():
        return False
    probe = subprocess.run(
        ["php", "-r", 'require "vendor/autoload.php"; '
         'exit(class_exists("Iteration8\\\\Utilities\\\\FileScanner\\\\Scanner") ? 0 : 1);'],
        cwd=ROOT, capture_output=True, check=False)
    return probe.returncode == 0


def fixture(base: Path) -> tuple[Path, Path]:
    a, b = base / "A", base / "B"
    a.mkdir()
    b.mkdir()
    for path, text in ((a / "a1", "X"), (a / "a2", "X"), (a / "a3", "Y"),
                       (b / "b1", "X"), (b / "b2", "Z")):
        path.write_text(text + "\n", encoding="utf-8")
    return a, b


def find_dupes(*args: str) -> tuple[int, str]:
    r = subprocess.run(["php", str(SCRIPT), *args], capture_output=True, text=True, check=False)
    return r.returncode, r.stdout + r.stderr


def last_json(out: str) -> dict[str, Any]:
    """The JSON document that ends OUT: from the last line that opens one."""
    starts = [i for i, line in enumerate(out.splitlines()) if line == "{"]
    if not starts:
        return {}
    doc: dict[str, Any] = json.loads("\n".join(out.splitlines()[starts[-1]:]))
    return doc


def names(group: dict[str, Any]) -> list[str]:
    return sorted(Path(f["path"]).name for f in group["files"])


def json_modes(base: Path, a: Path, b: Path) -> None:
    rc, out = find_dupes(str(a), "--json")
    doc = last_json(out)
    check("one directory: exit 0", rc, 0)
    check("one directory: a duplicates run, one group of a1 and a2",
          (doc.get("mode"), doc.get("duplicate_groups"), [names(g) for g in doc.get("groups", [])]),
          ("duplicates", 1, [["a1", "a2"]]))
    rc, out = find_dupes(str(a), str(b), "--json")
    doc = last_json(out)
    check("two directories: a compare, one match, one unique each side",
          (rc, doc.get("mode"), doc.get("match_count"), doc.get("unique_to_source"),
           doc.get("unique_to_target")), (0, "compare", 1, 1, 1))
    rc, out = find_dupes(str(a), "--db", str(base / "h.sqlite"), "--stream", "--json")
    doc = last_json(out)
    check("--stream --db: the same group, through the database",
          (rc, doc.get("mode"), doc.get("db_total"), [names(g) for g in doc.get("groups", [])]),
          (0, "stream", 3, [["a1", "a2"]]))


def workers_mode(base: Path, a: Path, b: Path) -> None:
    rc, out = find_dupes(str(a), str(b), "--db", str(base / "w.sqlite"), "--stream",
                         "--workers", "2", "--json")
    doc = last_json(out)
    check("--workers over both: one group of three",
          (rc, doc.get("mode"), [names(g) for g in doc.get("groups", [])]),
          (0, "stream", [["a1", "a2", "b1"]]))
    check("--workers: the workers' console lines come before the JSON",
          "[INFO] Scanning (streaming):" in out.split("\n{\n", 1)[0], True)


def text_and_refusals(a: Path) -> None:
    rc, out = find_dupes(str(a))
    check("no --json: console lines, exit 0", (rc, "Finding duplicates in:" in out), (0, True))
    rc, out = find_dupes("--help")
    check("--help: usage, exit 0", (rc, out.splitlines()[0] if out else ""), (0, "Usage:"))
    for args, said in (((str(a), "--algo"), "Error: --algo needs a value."),
                       ((), "Error: at least one directory is required."),
                       ((str(a), "--stream"), "Error: --stream requires --db <path>")):
        rc, out = find_dupes(*args)
        check(f"{' '.join(args[1:]) or 'no directory'}: refused, exit 1", (rc, said in out),
              (1, True))


def test_find_dupes() -> None:
    with tempfile.TemporaryDirectory() as td:
        base = Path(td)
        a, b = fixture(base)
        json_modes(base, a, b)
        workers_mode(base, a, b)
        text_and_refusals(a)


if __name__ == "__main__":
    if not library_installed():
        print("SKIP: php or iteration8/utilities not installed in vendor/; nothing here can run")
        sys.exit(0)
    sys.exit(run(globals(), "find-dupes"))
