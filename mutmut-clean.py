#!/usr/bin/env python3
"""mutmut run, with its stats pass and clean run across workers.

    python mutmut-clean.py run [mutmut run's arguments]     (from the checkout)

mutmut 3.8 runs the stats pass (every test, recording which functions each
reaches and how long it takes) and the clean run (every test the run's mutants
reach, unmutated, which must pass so a mutant's failure is its own) in its own
process, one test after another, and has no setting for either. mutmut-gate.py
runs mutmut through this script, which runs both with the same pytest
arguments under pytest-xdist: WORKERS processes, each test file whole in one
of them (--dist loadfile, as the suites run apart).

The stats pass replaces PytestRunner's: each worker records with
mutmut-plugin/jpt_mutmut_stats.py, mutmut's own recording hooks, and writes
what it saw; this merges it into mutmut's state, as mutmut merges a child's.
The clean run replaces ForkRunner's (process_isolation = "fork", mutmut's
default and JP_TOOLS'), then collects the same tests in mutmut's process
without running them, which is what the serial run also did for the mutants:
each mutant's tests run in a fork of mutmut's process and find the test
modules already imported there.

WORKERS is MUTMUT_CLEAN_WORKERS in the environment (mutmut-gate.py sets it to
its --max-children); unset, both are mutmut's own, and so are they under
process_isolation = "forkserver", whose runs are forked from a server this
does not reach.

A script, not a module of a package here: mutmut puts mutants/ first on the
import path so the tests import the mutated code, and a package of this
checkout already imported in mutmut's process would be the one they got.
"""

import json
import os
import resource
import sys
import tempfile
from collections.abc import Iterable
from pathlib import Path
from typing import Any

from mutmut import __main__ as mutmut_main
from mutmut.runners.harness import PytestRunner
from mutmut.state import state
from mutmut.utils.file_utils import change_cwd
from mutmut.workers.isolation import ForkRunner

WORKERS = "MUTMUT_CLEAN_WORKERS"
# The core file size limit for mutmut and its workers, in bytes (see main).
CORE_LIMIT = 1
# The stats pass's worker plugin: its directory, its module, and where each
# worker writes what it recorded.
PLUGIN_DIR = Path(__file__).resolve().parent / "mutmut-plugin"
PLUGIN = "jpt_mutmut_stats"
STATS_DIR = "JPT_MUTMUT_STATS_DIR"
_serial = ForkRunner.run_clean_tests
_serial_stats = PytestRunner.run_stats


def merge_stats(out: Path) -> None:
    """Each worker's record in OUT, into mutmut's state: the tests that reach
    each function and each function's callers, added to; each test's time."""
    s = state()
    for record_file in sorted(out.glob("*.json")):
        record = json.loads(record_file.read_text(encoding="utf-8"))
        for function, tests in record["tests_by_mangled_function_name"].items():
            s.tests_by_mangled_function_name[function].update(tests)
        s.duration_by_test.update(record["duration_by_test"])
        for function, callers in record["function_dependencies"].items():
            s.function_dependencies[function].update(callers)


def run_stats(self: PytestRunner, *, tests: Iterable[str]) -> int:
    """The stats pass across WORKERS processes, merged into mutmut's state;
    mutmut's own when WORKERS is not set."""
    workers = os.environ.get(WORKERS) or ""
    if not workers:
        return int(_serial_stats(self, tests=tests))
    # PytestRunner's pytest helpers are not declared on the type mutmut's stubs give it.
    runner: Any = self
    before = {k: os.environ.get(k) for k in ("PYTHONPATH", STATS_DIR)}
    with tempfile.TemporaryDirectory(prefix="mutmut-clean-stats-") as out:
        os.environ["PYTHONPATH"] = os.pathsep.join(p for p in (str(PLUGIN_DIR), before["PYTHONPATH"]) if p)
        os.environ[STATS_DIR] = out
        sys.path.insert(0, str(PLUGIN_DIR))
        try:
            with change_cwd("mutants"):
                rc = int(runner.execute_pytest([*runner._pytest_args_regular_run(list(tests)), "-n", workers,
                                                "--dist", "loadfile", "-p", PLUGIN]))
        finally:
            sys.path.remove(str(PLUGIN_DIR))
            for k, v in before.items():
                if v is None:
                    os.environ.pop(k, None)
                else:
                    os.environ[k] = v
        merge_stats(Path(out))
    return rc


def run_clean_tests(self: ForkRunner, tests: Iterable[str]) -> int:
    """The clean run across WORKERS processes, then the same tests collected
    in this one; mutmut's own when WORKERS is not set."""
    workers = os.environ.get(WORKERS) or ""
    if not workers:
        return int(_serial(self, tests))
    # A fork run's runner is mutmut's PytestRunner, whose pytest helpers its
    # TestRunner type does not declare.
    runner: Any = self.test_runner
    tests = list(tests)
    chosen = tests or runner._pytest_add_cli_args_test_selection
    with change_cwd("mutants"):
        rc = int(runner.execute_pytest([*runner._pytest_args_regular_run(tests), "-n", workers, "--dist", "loadfile"]))
        if rc == 0:
            runner.execute_pytest(["--collect-only", "-qqq", *chosen])
    return rc


def main() -> None:
    # mutmut stops a mutant that runs too long with SIGXCPU, which dumps core,
    # and a core_pattern that pipes to a handler (apport, a WSL crash catcher)
    # ignores a limit of 0: one such run wrote 31 GB of cores in 13 minutes.
    # A 1-byte limit is the one such a handler takes as "no dump"; the mutant
    # still exits as a timeout. Every worker mutmut forks inherits it.
    resource.setrlimit(resource.RLIMIT_CORE, (CORE_LIMIT, CORE_LIMIT))
    # mypy sees a method assigned when mutmut's types are installed, and nothing when they are not.
    ForkRunner.run_clean_tests = run_clean_tests  # type: ignore[method-assign, unused-ignore]  # reason: a phase replaced
    PytestRunner.run_stats = run_stats  # type: ignore[method-assign, unused-ignore]  # reason: a phase replaced
    sys.exit(mutmut_main.cli())


if __name__ == "__main__":
    main()
