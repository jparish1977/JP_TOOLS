#!/usr/bin/env python3
"""
JP_TOOLS/tests/test_python_tools_runners.py
What jpcheck.python_tools' ruff and mypy runners call and what they report.

In-process, with shutil.which and subprocess.run replaced: which tool is
found, the exact command and keyword arguments each runner passes, the
environment it runs in, and the whole result it builds from a given output.
The report for a missing tool, ruff's JSON (every field, every default),
mypy's text lines (notes skipped, unmatched lines skipped, the trailing
error code split off) and output that does not parse are each pinned whole.

    python tests/test_python_tools_runners.py
"""

import contextlib
import os
import shutil
import subprocess
import sys
import tempfile
from collections.abc import Callable, Iterator
from pathlib import Path
from typing import Any
from unittest import mock

from _harness import check, run
from jpcheck import python_tools
from jpcheck.common import TOOLS_ROOT, _plain_env


@contextlib.contextmanager
def fake(found: tuple[str, ...], stdout: str = "", returncode: int = 0,
         stderr: str = "") -> Iterator[list[tuple[list[str], dict[str, Any]]]]:
    """The calls subprocess.run gets, while only FOUND are on PATH and every
    run finishes with STDOUT, RETURNCODE and STDERR."""
    calls: list[tuple[list[str], dict[str, Any]]] = []

    def fake_run(cmd: list[str], **kwargs: Any) -> "subprocess.CompletedProcess[str]":
        calls.append((cmd, kwargs))
        return subprocess.CompletedProcess(cmd, returncode, stdout, stderr)

    def fake_which(name: str) -> str | None:
        return f"/fake/{name}" if name in found else None

    with mock.patch.object(shutil, "which", fake_which), \
            mock.patch.object(subprocess, "run", fake_run):
        yield calls


def outcome(fn: Callable[[str], Any], target: str) -> Any:
    """FN(TARGET), or what it raised: a crash is then a wrong value in the
    check aimed at it rather than a failure under some other name."""
    try:
        return fn(target)
    except Exception as e:  # noqa: BLE001 -- reason: the exception is the value compared
        return f"raised {type(e).__name__}"


def target_file(td: str) -> str:
    t = Path(td) / "mod.py"
    t.write_text("x = 1\n", encoding="utf-8")
    return str(t)


# ── ruff ─────────────────────────────────────────────────────────────────────

def test_ruff_env_names_this_checkout() -> None:
    check("ruff_env: the environment, JP_TOOLS_ROOT naming this checkout",
          python_tools.ruff_env(),
          {**os.environ, "JP_TOOLS_ROOT": str(TOOLS_ROOT.resolve())})


def test_ruff_missing() -> None:
    with fake(found=()) as calls:
        got = outcome(python_tools.run_ruff, "x.py")
    check("ruff missing: the report", got,
          {"tool": "ruff", "status": "unavailable", "issues": [],
           "note": "'ruff' not found: pip install -r requirements-dev.txt"})
    check("ruff missing: nothing is run", calls, [])


RUFF_JSON = """[
 {"filename": "/p/a.py", "location": {"row": 3, "column": 5}, "code": "F401",
  "message": "unused import", "fix": {"applicability": "safe"}},
 {"filename": "/p/b.py", "location": {"row": 7, "column": 2}, "code": "UP006",
  "message": "use list", "fix": {"applicability": "unsafe"}},
 {"filename": "/p/c.py", "location": {"row": 9, "column": 1}, "code": "E501",
  "message": "too long", "fix": {"applicability": "display-only"}},
 {"filename": "/p/d.py", "location": {}, "code": "E1", "message": "m", "fix": null},
 {}
]"""


def ruff_issue(file: str, line: int, col: int, rule: str, message: str,
               fixable: bool, unsafe: bool) -> dict[str, Any]:
    return {"file": file, "line": line, "col": col, "severity": "error", "rule": rule,
            "message": message, "fixable": fixable, "fixable_unsafe": unsafe}


def test_ruff_call() -> None:
    with tempfile.TemporaryDirectory() as td:
        t = target_file(td)
        with fake(found=("ruff",)) as calls:
            outcome(python_tools.run_ruff, t)
        check("ruff: the call", calls,
              [(["ruff", "check", *python_tools._ruff_config_args(t),
                 "--output-format", "json", t],
                {"capture_output": True, "text": True, "check": False,
                 "env": python_tools.ruff_env()})])


def test_ruff_findings() -> None:
    with tempfile.TemporaryDirectory() as td:
        t = target_file(td)
        with fake(found=("ruff",), stdout=RUFF_JSON, returncode=1):
            got = outcome(python_tools.run_ruff, t)
    check("ruff: the result from its JSON", got, {
        "tool": "ruff", "status": "fail", "issues": [
            ruff_issue("/p/a.py", 3, 5, "F401", "unused import", True, False),
            ruff_issue("/p/b.py", 7, 2, "UP006", "use list", False, True),
            ruff_issue("/p/c.py", 9, 1, "E501", "too long", False, False),
            ruff_issue("/p/d.py", 0, 0, "E1", "m", False, False),
            ruff_issue("", 0, 0, "", "", False, False),
        ]})


def test_ruff_output_that_is_not_json() -> None:
    with tempfile.TemporaryDirectory() as td:
        t = target_file(td)
        for label, stdout in (("empty", ""), ("blank", "  \n"), ("not JSON", "ruff crashed")):
            with fake(found=("ruff",), stdout=stdout, returncode=0):
                got = outcome(python_tools.run_ruff, t)
            check(f"ruff: {label} output reads as no findings", got,
                  {"tool": "ruff", "status": "pass", "issues": []})


# ── mypy ─────────────────────────────────────────────────────────────────────

def test_mypy_missing() -> None:
    with fake(found=()) as calls:
        got = outcome(python_tools.run_mypy, "x.py")
    check("mypy missing: the report", got,
          {"tool": "mypy", "status": "unavailable", "issues": [],
           "note": "'mypy' not found: pip install -r requirements-dev.txt"})
    check("mypy missing: nothing is run", calls, [])


def test_mypy_call() -> None:
    with tempfile.TemporaryDirectory() as td:
        t = target_file(td)
        with fake(found=("mypy",)) as calls:
            outcome(python_tools.run_mypy, t)
        check("mypy: the call", calls,
              [(["mypy", "--show-error-codes", "--no-error-summary", "--show-traceback",
                 "--ignore-missing-imports", "--follow-imports=silent",
                 *python_tools._mypy_config_args(t), t],
                {"capture_output": True, "text": True, "check": False,
                 "env": _plain_env()})])


MYPY_OUT = "\n".join([
    "Success is not a finding",
    "a.py:1: note: context before the first error",
    "a.py:5:7: error: Incompatible types  [assignment]",
    "a.py:6: error: Name [x] is not defined  [name-defined]",
    "a.py:9: error: Some [x] thing",
    "a.py:10: warning: ends with a bracket]",
    "a.py:11: error: an empty code []",
    "\x1b[31ma.py:12:3: error: coloured  \x1b[0m",
    "not a mypy line",
    "a.py:13: note: a note between errors",
    "a.py:14: error: after them all [misc]",
])


def mypy_issue(line: int, col: int, severity: str, rule: str, message: str) -> dict[str, Any]:
    return {"file": "a.py", "line": line, "col": col, "severity": severity, "rule": rule,
            "message": message, "fixable": False, "fixable_unsafe": False}


def test_mypy_findings() -> None:
    with tempfile.TemporaryDirectory() as td:
        t = target_file(td)
        with fake(found=("mypy",), stdout=MYPY_OUT, returncode=1):
            got = outcome(python_tools.run_mypy, t)
    check("mypy: the result from its lines", got, {
        "tool": "mypy", "status": "fail", "issues": [
            mypy_issue(5, 7, "error", "mypy:assignment", "Incompatible types"),
            mypy_issue(6, 0, "error", "mypy:name-defined", "Name [x] is not defined"),
            mypy_issue(9, 0, "error", "mypy", "Some [x] thing"),
            mypy_issue(10, 0, "warning", "mypy", "ends with a bracket]"),
            mypy_issue(11, 0, "error", "mypy", "an empty code"),
            mypy_issue(12, 3, "error", "mypy", "coloured"),
            mypy_issue(14, 0, "error", "mypy:misc", "after them all"),
        ]})


if __name__ == "__main__":
    sys.exit(run(globals(), "python_tools runners"))
