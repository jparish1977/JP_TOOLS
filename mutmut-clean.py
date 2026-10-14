#!/usr/bin/env python3
"""mutmut run, with its clean run across workers.

    python mutmut-clean.py run [mutmut run's arguments]     (from the checkout)

mutmut 3.8 runs the clean run (every test the run's mutants reach, unmutated,
which must pass so a mutant's failure is its own) in its own process, one test
after another, and has no setting for it. mutmut-gate.py runs mutmut through
this script, which replaces ForkRunner's clean run (process_isolation =
"fork", mutmut's default and JP_TOOLS') with the same pytest arguments under
pytest-xdist: WORKERS processes, each test file whole in one of them (--dist
loadfile, as the suites run apart). It then collects the same tests in
mutmut's process without running them, which is what the serial run also did
for the mutants: each mutant's tests run in a fork of mutmut's process and
find the test modules already imported there.

WORKERS is MUTMUT_CLEAN_WORKERS in the environment (mutmut-gate.py sets it to
its --max-children); unset, or under process_isolation = "forkserver" (whose
clean run is forked from a server this does not reach), the clean run is
mutmut's own.

A script, not a module of a package here: mutmut puts mutants/ first on the
import path so the tests import the mutated code, and a package of this
checkout already imported in mutmut's process would be the one they got.
"""

import os
import sys
from collections.abc import Iterable
from typing import Any

from mutmut import __main__ as mutmut_main
from mutmut.utils.file_utils import change_cwd
from mutmut.workers.isolation import ForkRunner

WORKERS = "MUTMUT_CLEAN_WORKERS"
_serial = ForkRunner.run_clean_tests


def run_clean_tests(self: ForkRunner, tests: Iterable[str]) -> int:
    """The clean run across WORKERS processes, then the same tests collected
    in this one; mutmut's own when WORKERS is not set."""
    workers = os.environ.get(WORKERS, "")
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
    # mypy sees a method assigned when mutmut's types are installed, and nothing when they are not.
    ForkRunner.run_clean_tests = run_clean_tests  # type: ignore[method-assign, unused-ignore]  # reason: the one phase replaced
    sys.exit(mutmut_main.cli())


if __name__ == "__main__":
    main()
