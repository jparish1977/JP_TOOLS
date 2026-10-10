#!/usr/bin/env python3
"""
JP_TOOLS/tests/test_check_mypy_scope.py
check.py's mypy reports the file it was given, not the modules that file imports.

mypy followed imports normally, so a file's check carried every error in every
module it imported: a typed module importing an untyped one failed on the
other file's debt, and a per-file baseline counted an imported module's errors
once more for each file importing it (lib.py, 2 errors of its own and 2
importers, was recorded as 6). With --follow-imports=silent the imports are
still analysed, so a wrong call INTO them is still caught in the importer.

SKIP without mypy.

    python tests/test_check_mypy_scope.py
"""

import json
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

from _harness import ROOT, check, run

CHECK = [sys.executable, str(ROOT / "check.py")]


def _repo(td: str) -> Path:
    """lib.py: two untyped functions and one typed. a.py and b.py import it."""
    r = Path(td)
    subprocess.run(["git", "init", "-q", str(r)], check=True)
    (r / "mypy.ini").write_text("[mypy]\ndisallow_untyped_defs = True\n", encoding="utf-8")
    (r / "lib.py").write_text("def f(x):\n    return x\n\n\ndef g(y):\n    return y\n\n\n"
                              "def h(n: int) -> int:\n    return n\n", encoding="utf-8")
    for name in ("a", "b"):
        (r / f"{name}.py").write_text(f"import lib\n\n\ndef {name}() -> int:\n    return lib.h(1)\n",
                                      encoding="utf-8")
    return r


def _mypy_files(r: Path, target: str) -> dict[str, int]:
    """{file name: mypy findings} in check.py's report for TARGET."""
    out = subprocess.run([*CHECK, target, "--tools", "mypy"], cwd=r,
                         capture_output=True, text=True, check=False).stdout
    found: dict[str, int] = {}
    for c in json.loads(out)["checks"]:
        for i in c["issues"]:
            name = Path(i["file"]).name
            found[name] = found.get(name, 0) + 1
    return found


def test_a_file_is_checked_for_its_own_errors_only() -> None:
    with tempfile.TemporaryDirectory() as td:
        r = _repo(td)
        check("CONTROL: lib.py's own check finds its 2 untyped defs", _mypy_files(r, "lib.py"),
              {"lib.py": 2})
        check("a.py's check carries none of lib.py's", _mypy_files(r, "a.py"), {})


def test_imports_are_still_analysed() -> None:
    """Silent, not skipped: a wrong call into lib is the importer's error."""
    with tempfile.TemporaryDirectory() as td:
        r = _repo(td)
        (r / "a.py").write_text('import lib\n\n\ndef a() -> int:\n    return lib.h("one")\n',
                                encoding="utf-8")
        check("a wrong argument to lib.h is reported in a.py", _mypy_files(r, "a.py"), {"a.py": 1})


def test_a_baseline_counts_a_module_once() -> None:
    with tempfile.TemporaryDirectory() as td:
        r = _repo(td)
        subprocess.run([*CHECK, ".", "--record-baseline", "base.json"], cwd=r,
                       capture_output=True, check=False)
        files = json.loads((r / "base.json").read_text(encoding="utf-8"))["files"]
        check("lib.py is recorded at its own count, not once per importer",
              files.get("lib.py", {}).get("mypy", {}).get("errors"), 2)


if __name__ == "__main__":
    if shutil.which("mypy") is None or shutil.which("git") is None:
        print("SKIP: mypy or git not found")
        sys.exit(0)
    sys.exit(run(globals(), "mypy scope"))
