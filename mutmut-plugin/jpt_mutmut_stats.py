"""mutmut's stats pass in a pytest-xdist worker.

mutmut-clean.py runs mutmut's stats pass (which tests reach which function,
and how long each test takes) across workers. mutmut records it with a pytest
plugin in its own process; this is that plugin's work done in each worker, the
same hooks recording the same things, then written to STATS_DIR/WORKERID.json
for mutmut-clean.py to merge back into mutmut's state.

It lives outside every mutmut source path, so it is never mutated, and is
loaded by name (pytest -p) from this directory on the workers' PYTHONPATH.
"""

import json
import os
from pathlib import Path
from typing import Any

from mutmut.mutation.trampoline import set_mutant_under_test
from mutmut.state import state
from mutmut.utils.format_utils import strip_prefix

STATS_DIR = "JPT_MUTMUT_STATS_DIR"
STATS = "stats"


def pytest_configure(config: Any) -> None:
    """mutmut marks its stats pass in its own process as well as in the
    environment, so a test that clears os.environ still records; a worker
    is a process of its own and is marked the same way."""
    set_mutant_under_test(STATS)


def pytest_runtest_logstart(nodeid: str, location: Any) -> None:
    state().duration_by_test[nodeid] = 0


def pytest_runtest_teardown(item: Any, nextitem: Any) -> None:
    for function in state()._stats:
        state().tests_by_mangled_function_name[function].add(strip_prefix(item._nodeid, prefix="mutants/"))
    state()._stats.clear()


def pytest_runtest_makereport(item: Any, call: Any) -> None:
    if call.when == "call":
        state().duration_by_test[item.nodeid] += call.duration


def pytest_sessionfinish(session: Any) -> None:
    """A worker writes what it recorded; the controlling process runs no
    tests, so it writes nothing."""
    worker = getattr(session.config, "workerinput", None)
    if worker is None:
        return
    s = state()
    record = {
        "tests_by_mangled_function_name": {k: sorted(v) for k, v in s.tests_by_mangled_function_name.items()},
        "duration_by_test": dict(s.duration_by_test),
        "function_dependencies": {k: sorted(v) for k, v in s.function_dependencies.items()},
    }
    Path(os.environ[STATS_DIR], f"{worker['workerid']}.json").write_text(json.dumps(record), encoding="utf-8")
