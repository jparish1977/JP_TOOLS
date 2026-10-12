#!/usr/bin/env python3
"""
JP_TOOLS/tests/test_run_suites.py
run-suites.sh, against throwaway trees of tiny suites. The invariants it exists
to keep, not its happy path:

    - A failing suite fails the run (exit 1) and is named, with its output.
    - A suite that exits 0 with a last line starting SKIP is counted as
      skipped, never as passed, and named; with no failure the run exits 3.
    - Only the last line decides: a SKIP printed earlier is not a skip.
    - The count line is exact: "P/N passed, S skipped, F failed".
    - Exit 0 only when every suite passed. A root that cannot be entered: 2,
      could not look, which is not 3, a skip.
    - The run's last line names its exit code and what it means.
    - The old path, tools/lab/run-suites.sh, forwards to it.

    python3 tests/test_run_suites.py
"""

import atexit
import os
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

from _harness import ROOT, check, check_true, run

_MADE: list[Path] = []
atexit.register(lambda: [shutil.rmtree(p, ignore_errors=True) for p in _MADE])

SUITES = {
    "test_pass.py": 'print("all good")\n',
    "test_fail.py": 'import sys\nprint("first")\nprint("FAIL something")\nsys.exit(1)\n',
    "test_skip.py": 'print("SKIP: the tool is not installed")\n',
    "test_early_skip.py": 'print("SKIP: one case")\nprint("3 passed")\n',
    "test_silent.py": "",
    "test_shell.sh": 'echo "shell ok"\n',
}


def _tree(names: list[str]) -> Path:
    root = Path(tempfile.mkdtemp())
    _MADE.append(root)
    (root / "tests").mkdir()
    for name in names:
        (root / "tests" / name).write_text(SUITES[name], encoding="utf-8")
    return root


def _run(root: Path, script: Path = ROOT / "run-suites.sh") -> tuple[int, str]:
    p = subprocess.run(["bash", str(script), str(root)], capture_output=True, text=True,
                       env=dict(os.environ, JOBS="2", FAIL_LINES="5"), check=False)
    return p.returncode, p.stdout


def _count(out: str) -> str:
    m = re.search(r"^suites: (\d+/\d+ passed, \d+ skipped, \d+ failed) in \d+s$", out, re.M)
    return m.group(1) if m else f"no count line in: {out!r}"


def _last(out: str) -> str:
    return out.rstrip("\n").rsplit("\n", 1)[-1]


def test_every_suite_passing_is_the_only_exit_0() -> None:
    rc, out = _run(_tree(["test_pass.py", "test_early_skip.py", "test_silent.py",
                          "test_shell.sh"]))
    check("every suite passed, so exit 0", rc, 0)
    check("  the last line says so", _last(out), "run-suites: exit 0, every suite passed")
    check("  the count", _count(out), "4/4 passed, 0 skipped, 0 failed")
    check_true("  a SKIP that is not the last line does not make a skip",
               "SKIPPED" not in out)
    check_true("  each suite has its line, rc and last line",
               "rc=0 tests/test_early_skip.py :: 3 passed" in out
               and "rc=0 tests/test_shell.sh :: shell ok" in out)


def test_a_skip_is_not_a_pass() -> None:
    rc, out = _run(_tree(["test_pass.py", "test_skip.py"]))
    check("a skip and no failure: exit 3", rc, 3)
    check("  counted as skipped, not passed", _count(out), "1/2 passed, 1 skipped, 0 failed")
    check_true("  and named", "SKIPPED tests/test_skip.py" in out)
    check("  the last line says so", _last(out), "run-suites: exit 3, none failed, 1 skipped")


def test_a_failure_fails_the_run_and_is_named() -> None:
    rc, out = _run(_tree(["test_pass.py", "test_fail.py", "test_skip.py"]))
    check("a failure: exit 1, whatever else", rc, 1)
    check("  the count", _count(out), "1/3 passed, 1 skipped, 1 failed")
    check_true("  named with its rc", "---- FAILED rc=1 tests/test_fail.py" in out)
    check_true("  and its own output shown", "FAIL something" in out.split("---- FAILED", 1)[-1])
    check("  the last line says so", _last(out), "run-suites: exit 1, 1 failed")


def test_a_root_that_cannot_be_entered() -> None:
    missing = Path(tempfile.gettempdir()) / "no-such-tree-for-run-suites"
    rc, out = _run(missing)
    check("could not look: exit 2, not a skip's 3", rc, 2)
    check("  the last line says so", _last(out), f"run-suites: exit 2, could not look: cannot enter {missing}")
    check_true("  and no suite ran", "suites:" not in out)


def test_the_old_lab_path_forwards() -> None:
    root = _tree(["test_pass.py", "test_skip.py"])
    check("tools/lab/run-suites.sh gives the same verdict",
          _run(root, ROOT / "tools" / "lab" / "run-suites.sh")[0], 3)


if __name__ == "__main__":
    sys.exit(run(globals(), "run-suites.sh"))
