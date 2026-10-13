#!/usr/bin/env python3
"""
JP_TOOLS/tests/test_common_runner_paths.py
jpcheck/common.py's node-runner verdict and path helpers, in this process.

WHAT IS CHECKED
    _node_runner_failed: exit 0 is None; any other exit is an error result,
    compared whole, whose note names the exit code and the first stderr line
    holding "Error" (case as written, colour stripped), else the first
    non-blank line, else "exit N"; the line is cut at 300 characters.

    _rel: a path under the root is relative to it, POSIX form; one outside it
    is returned as given.

    _repo_root: git's answer is used only when git succeeded AND printed a
    directory. A failing git that printed one anyway is not trusted, and the
    start directory is the root. git is replaced in-process for this case.

    _tool_missing: the whole report, with no install hint (the note says to
    put it on PATH) and with one (the note carries the hint).

    python3 tests/test_common_runner_paths.py
"""

import subprocess
import sys
import tempfile
from collections.abc import Callable
from pathlib import Path
from unittest import mock

from _harness import check, run
from jpcheck import common


def outcome(fn: Callable[[], object]) -> object:
    """FN's result, or the name of what it raised: a crash is a wrong answer."""
    try:
        return fn()
    except Exception as e:  # noqa: BLE001 -- reason: a crash is compared as a result, so its check names it
        return f"raised {type(e).__name__}"


def failed(code: int, stderr: str) -> object:
    proc = subprocess.CompletedProcess(args=["node"], returncode=code, stdout="", stderr=stderr)
    return outcome(lambda: common._node_runner_failed("eslint", proc))


def error(note: str) -> dict[str, object]:
    return {"tool": "eslint", "status": "error", "issues": [], "note": note}


def test_node_runner_failed() -> None:
    check("runner: exit 0 linted, so no result", failed(0, "Error: printed anyway\n"), None)
    check("runner: exit 1 with no output names the exit",
          failed(1, ""), error("runner exited 1: exit 1"))
    check("runner: blank stderr lines are not a line",
          failed(2, "  \n\n"), error("runner exited 2: exit 2"))
    check("runner: the first line holding 'Error', colour stripped",
          failed(1, "\n  node:internal/modules\n an error, lower case\n"
                    "\x1b[31mError [ERR_MODULE_NOT_FOUND]: Cannot find package 'eslint'\x1b[0m\n"
                    "TypeError: a later one\n"),
          error("runner exited 1: Error [ERR_MODULE_NOT_FOUND]: Cannot find package 'eslint'"))
    check("runner: no line holding 'Error' gives the first non-blank line",
          failed(2, "\n  refused: no files named  \n second line\n"),
          error("runner exited 2: refused: no files named"))
    check("runner: the line is cut at 300 characters",
          failed(1, "Error " + "x" * 400 + "\n"), error("runner exited 1: Error " + "x" * 294))


def test_rel() -> None:
    root = Path(tempfile.gettempdir()).resolve() / "jp-tools-rel"
    check("rel: a path under the root is relative to it",
          outcome(lambda: common._rel(str(root / "src" / "a.py"), root)), "src/a.py")
    check("rel: a path outside the root is returned as given",
          outcome(lambda: common._rel("/elsewhere/b.py", root)), "/elsewhere/b.py")


def root_when_git_says(start: Path, code: int, out: str) -> object:
    """_repo_root(START) with git answering exit CODE and stdout OUT."""
    def fake(args: list[str], cwd: Path) -> subprocess.CompletedProcess[str]:
        return subprocess.CompletedProcess(["git", *args], code, out, "")
    with mock.patch.object(common, "_git", fake):
        return outcome(lambda: common._repo_root(str(start)))


def test_repo_root_distrusts_a_failing_git() -> None:
    with tempfile.TemporaryDirectory() as td:
        start = Path(td).resolve()
        check("repo root: a failing git's printed directory is not used",
              root_when_git_says(start, 128, "/somewhere/else\n"), start)
        check("repo root: a git that printed nothing is not used",
              root_when_git_says(start, 0, "\n"), start)
        check("repo root: CONTROL a succeeding git's directory is used",
              root_when_git_says(start, 0, "/top/of/repo\n"), Path("/top/of/repo"))


def test_tool_missing() -> None:
    check("missing: with no install hint the note says to put it on PATH",
          outcome(lambda: common._tool_missing("phpstan")),
          {"tool": "phpstan", "status": "unavailable", "issues": [],
           "note": "'phpstan' not found on PATH: install it to enable this check"})
    check("missing: an install hint is the note",
          outcome(lambda: common._tool_missing("phpstan", "composer install")),
          {"tool": "phpstan", "status": "unavailable", "issues": [],
           "note": "'phpstan' not found: composer install"})


if __name__ == "__main__":
    sys.exit(run(globals(), "common-runner-paths"))
