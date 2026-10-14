#!/usr/bin/env python3
"""
JP_TOOLS/tests/test_mutmut_clean.py
mutmut-clean.py: mutmut run with its clean run across workers.

WHAT IS CHECKED
    With MUTMUT_CLEAN_WORKERS set, the clean run is mutmut's own pytest
    arguments plus -n WORKERS --dist loadfile, run in mutants/; when it
    passes, the same tests (or mutmut's test selection, for a run that names
    none) are collected in this process without running; when it fails,
    nothing more runs and its code is the answer. Unset, it is mutmut's own
    serial clean run. main puts it in place of ForkRunner's and runs mutmut's
    command line, exiting with its code.

    python3 tests/test_mutmut_clean.py
"""

import os
import sys
import tempfile
from pathlib import Path
from typing import ClassVar
from unittest import mock

from _harness import check, load_script, run

clean = load_script("mutmut-clean.py")

REGULAR = ["-x", "-q", "-p", "no:randomly", "-p", "no:random-order"]


class _Pytest:
    """mutmut's PytestRunner, recording each pytest run and where it ran."""

    _pytest_add_cli_args_test_selection: ClassVar[list[str]] = ["tests"]

    def __init__(self, rc: int) -> None:
        self.rc = rc
        self.ran: list[tuple[list[str], str]] = []

    def _pytest_args_regular_run(self, tests: list[str]) -> list[str]:
        return REGULAR + (tests or self._pytest_add_cli_args_test_selection)

    def execute_pytest(self, params: list[str]) -> int:
        self.ran.append((params, Path.cwd().name))
        return self.rc


class _Fork:
    def __init__(self, rc: int) -> None:
        self.test_runner = _Pytest(rc)


def _clean(rc: int, tests: list[str], workers: str | None) -> tuple[int, list[tuple[list[str], str]]]:
    fork = _Fork(rc)
    env = {} if workers is None else {clean.WORKERS: workers}
    with tempfile.TemporaryDirectory() as td, mock.patch.dict(os.environ, env, clear=True):
        (Path(td) / "mutants").mkdir()
        here = os.getcwd()
        os.chdir(td)
        try:
            got = clean.run_clean_tests(fork, tests)
        finally:
            os.chdir(here)
    return got, fork.test_runner.ran


def test_the_clean_run_goes_across_the_workers() -> None:
    check("the workers' name in the environment", clean.WORKERS, "MUTMUT_CLEAN_WORKERS")
    check("named tests, passing: across the workers by file, then the same tests collected here, in mutants/",
          _clean(0, ["tests/test_a.py::t"], "4"),
          (0, [([*REGULAR, "tests/test_a.py::t", "-n", "4", "--dist", "loadfile"], "mutants"),
               (["--collect-only", "-qqq", "tests/test_a.py::t"], "mutants")]))
    check("no tests named: mutmut's selection, run and collected",
          _clean(0, [], "8"),
          (0, [([*REGULAR, "tests", "-n", "8", "--dist", "loadfile"], "mutants"),
               (["--collect-only", "-qqq", "tests"], "mutants")]))
    check("failing: its code, and nothing collected", _clean(1, ["t"], "2"),
          (1, [([*REGULAR, "t", "-n", "2", "--dist", "loadfile"], "mutants")]))


def test_with_no_workers_the_clean_run_is_mutmut_s() -> None:
    asked: list[tuple[object, list[str]]] = []

    def serial(self: object, tests: list[str]) -> int:
        asked.append((self, tests))
        return 7

    fork = _Fork(0)
    for env in ({}, {clean.WORKERS: ""}):
        asked.clear()
        with mock.patch.object(clean, "_serial", serial), mock.patch.dict(os.environ, env, clear=True):
            got = clean.run_clean_tests(fork, ["t"])
        check(f"{env or 'unset'}: mutmut's own, its code", (got, asked, fork.test_runner.ran), (7, [(fork, ["t"])], []))


def test_main_runs_mutmut_with_the_clean_run_replaced() -> None:
    original = clean.ForkRunner.run_clean_tests
    seen: list[object] = []

    def cli() -> int:
        seen.append(clean.ForkRunner.run_clean_tests)
        return 3

    try:
        with mock.patch.object(clean.mutmut_main, "cli", cli):
            try:
                clean.main()
                code: object = None
            except SystemExit as e:
                code = e.code
    finally:
        clean.ForkRunner.run_clean_tests = original
    check("mutmut's command line run with ForkRunner's clean run replaced, and its code the exit",
          (seen, code), ([clean.run_clean_tests], 3))


if __name__ == "__main__":
    sys.exit(run(globals(), "mutmut-clean"))
