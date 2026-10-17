#!/usr/bin/env python3
"""
JP_TOOLS/tests/test_mutmut_clean.py
mutmut-clean.py: mutmut run with its stats pass and clean run across workers.

WHAT IS CHECKED
    With MUTMUT_CLEAN_WORKERS set, the clean run is mutmut's own pytest
    arguments plus -n WORKERS --dist loadfile, run in mutants/; when it
    passes, the same tests (or mutmut's test selection, for a run that names
    none) are collected in this process without running; when it fails,
    nothing more runs and its code is the answer. Unset, it is mutmut's own
    serial clean run.

    The stats pass, with it set: the same arguments plus -p jpt_mutmut_stats,
    the plugin's directory first on the workers' PYTHONPATH and on this
    process's import path, each worker's record then merged into mutmut's
    state (reach and callers added to, times set), and the environment and
    import path left as they were; a failing pass still merges and gives its
    code. Unset, mutmut's own. main puts both in place of mutmut's and runs
    mutmut's command line, exiting with its code.

    python3 tests/test_mutmut_clean.py
"""

import json
import os
import sys
import tempfile
from pathlib import Path
from typing import ClassVar
from unittest import mock

from mutmut.state import MutmutState

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

    def serial(self: object, tests: list[str]) -> int:
        # Under mutmut, ForkRunner's own clean run may already be this script's,
        # so a run sent to it would look the same: it must not be sent at all.
        raise AssertionError("the serial clean run was used")

    with tempfile.TemporaryDirectory() as td, mock.patch.dict(os.environ, env, clear=True), \
            mock.patch.object(clean, "_serial", serial):
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


class _Stats(_Pytest):
    """mutmut's PytestRunner in the stats pass. It records each pytest run
    with the environment it ran in, and writes each worker's record as a
    worker would."""

    def __init__(self, rc: int, records: dict[str, dict[str, object]]) -> None:
        super().__init__(rc)
        self.records = records
        self.seen_env: list[tuple[str | None, str | None, bool]] = []

    def execute_pytest(self, params: list[str]) -> int:
        out = os.environ.get(clean.STATS_DIR)
        self.seen_env.append((os.environ.get("PYTHONPATH"), out, str(clean.PLUGIN_DIR) in sys.path))
        for worker, record in self.records.items():
            Path(str(out), f"{worker}.json").write_text(json.dumps(record), encoding="utf-8")
        return super().execute_pytest(params)


def _stats(rc: int, tests: list[str], workers: str, records: dict[str, dict[str, object]],
           env: dict[str, str] | None = None) -> tuple[int, _Stats, dict[str, object]]:
    runner = _Stats(rc, records)
    s = MutmutState()
    s.tests_by_mangled_function_name["a.x_kept"].add("tests/test_old.py::t")

    def serial(self: object, *, tests: list[str]) -> int:
        raise AssertionError("the serial stats pass was used")

    with tempfile.TemporaryDirectory() as td, \
            mock.patch.dict(os.environ, {**(env or {}), clean.WORKERS: workers}, clear=True), \
            mock.patch.object(clean, "_serial_stats", serial), mock.patch.object(clean, "state", lambda: s):
        (Path(td) / "mutants").mkdir()
        here = os.getcwd()
        os.chdir(td)
        try:
            got = clean.run_stats(runner, tests=tests)
        finally:
            os.chdir(here)
        after: dict[str, object] = {
            "PYTHONPATH": os.environ.get("PYTHONPATH"), "out": os.environ.get(clean.STATS_DIR),
            "plugin on sys.path": str(clean.PLUGIN_DIR) in sys.path,
            "tests_by": {k: sorted(v) for k, v in s.tests_by_mangled_function_name.items()},
            "durations": dict(s.duration_by_test),
            "deps": {k: sorted(v) for k, v in s.function_dependencies.items()}}
    return got, runner, after


W0 = {"tests_by_mangled_function_name": {"a.x_f": ["tests/test_a.py::t1"], "a.x_kept": ["tests/test_a.py::t1"]},
      "duration_by_test": {"tests/test_a.py::t1": 0.5},
      "function_dependencies": {"a.x_f": ["a.x_g"]}}
W1 = {"tests_by_mangled_function_name": {"a.x_f": ["tests/test_b.py::t2"], "b.x_h": ["tests/test_b.py::t2"]},
      "duration_by_test": {"tests/test_b.py::t2": 1.5},
      "function_dependencies": {"a.x_f": ["b.x_h"]}}
PLUGIN_ARGS = ["-n", "4", "--dist", "loadfile", "-p", "jpt_mutmut_stats"]


def test_the_stats_pass_goes_across_the_workers() -> None:
    check("the plugin, a directory beside this script that mutmut does not mutate",
          (clean.PLUGIN_DIR.name, clean.PLUGIN, (clean.PLUGIN_DIR / f"{clean.PLUGIN}.py").is_file()),
          ("mutmut-plugin", "jpt_mutmut_stats", True))
    plugin_text = (clean.PLUGIN_DIR / f"{clean.PLUGIN}.py").read_text(encoding="utf-8")
    check("  the workers write where this reads: one variable, named the same on both sides",
          (clean.STATS_DIR, f'STATS_DIR = "{clean.STATS_DIR}"' in plugin_text),
          ("JPT_MUTMUT_STATS_DIR", True))
    got, runner, after = _stats(0, ["tests/test_a.py::t1"], "4", {"gw0": W0, "gw1": W1}, env={"PYTHONPATH": "/kept"})
    check("named tests: across the workers by file, the plugin loaded in each, in mutants/",
          (got, runner.ran), (0, [([*REGULAR, "tests/test_a.py::t1", *PLUGIN_ARGS], "mutants")]))
    [(path, out, on_path)] = runner.seen_env
    check("  the plugin's directory first on the workers' PYTHONPATH, then what was there",
          path, os.pathsep.join([str(clean.PLUGIN_DIR), "/kept"]))
    check("  and on this process's import path, with somewhere for the workers to write, named as ours",
          (on_path, Path(str(out)).name.startswith("mutmut-clean-stats-")), (True, True))
    check("  after it, the environment and import path as they were",
          (after["PYTHONPATH"], after["out"], after["plugin on sys.path"]), ("/kept", None, False))
    check("every worker's reach added to what mutmut already had", after["tests_by"],
          {"a.x_f": ["tests/test_a.py::t1", "tests/test_b.py::t2"],
           "a.x_kept": ["tests/test_a.py::t1", "tests/test_old.py::t"], "b.x_h": ["tests/test_b.py::t2"]})
    check("  every test's time", after["durations"], {"tests/test_a.py::t1": 0.5, "tests/test_b.py::t2": 1.5})
    check("  every function's callers, from every worker", after["deps"], {"a.x_f": ["a.x_g", "b.x_h"]})
    _, runner, after = _stats(0, [], "8", {})
    check("no tests named: mutmut's selection; no PYTHONPATH before, none after",
          (runner.ran[0][0], runner.seen_env[0][0], after["PYTHONPATH"]),
          ([*REGULAR, "tests", "-n", "8", "--dist", "loadfile", "-p", "jpt_mutmut_stats"], str(clean.PLUGIN_DIR),
           None))
    got, _, after = _stats(1, ["t"], "2", {"gw0": W0})
    check("failing: its code, and what the workers recorded still merged", (got, after["tests_by"]),
          (1, {"a.x_f": ["tests/test_a.py::t1"], "a.x_kept": ["tests/test_a.py::t1", "tests/test_old.py::t"]}))


def test_with_no_workers_the_stats_pass_is_mutmut_s() -> None:
    asked: list[tuple[object, list[str]]] = []

    def serial(self: object, *, tests: list[str]) -> int:
        asked.append((self, tests))
        return 7

    runner = _Stats(0, {})
    for env in ({}, {clean.WORKERS: ""}):
        asked.clear()
        with mock.patch.object(clean, "_serial_stats", serial), mock.patch.dict(os.environ, env, clear=True):
            got = clean.run_stats(runner, tests=["t"])
        check(f"{env or 'unset'}: mutmut's own, its code", (got, asked, runner.ran), (7, [(runner, ["t"])], []))


def test_main_runs_mutmut_with_the_clean_run_replaced() -> None:
    original = clean.ForkRunner.run_clean_tests
    original_stats = clean.PytestRunner.run_stats
    seen: list[object] = []

    def cli() -> int:
        seen.extend([clean.ForkRunner.run_clean_tests, clean.PytestRunner.run_stats])
        return 3

    limits: list[tuple[int, tuple[int, int]]] = []
    try:
        with mock.patch.object(clean.mutmut_main, "cli", cli), \
                mock.patch.object(clean.resource, "setrlimit", lambda r, v: limits.append((r, v))):
            try:
                clean.main()
                code: object = None
            except SystemExit as e:
                code = e.code
    finally:
        clean.ForkRunner.run_clean_tests = original
        clean.PytestRunner.run_stats = original_stats
    check("mutmut's command line run with the clean run and the stats pass replaced, and its code the exit",
          (seen, code), ([clean.run_clean_tests, clean.run_stats], 3))
    check("  under a 1-byte core limit, set before it", (limits, clean.CORE_LIMIT),
          ([(clean.resource.RLIMIT_CORE, (1, 1))], 1))


if __name__ == "__main__":
    sys.exit(run(globals(), "mutmut-clean"))
