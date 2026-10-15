#!/usr/bin/env python3
"""
JP_TOOLS/tests/test_jpt_mutmut_stats.py
mutmut-plugin/jpt_mutmut_stats.py: mutmut's stats pass recorded in a
pytest-xdist worker.

WHAT IS CHECKED
    Imported in a stats pass's worker (STATS_DIR in its environment), it
    marks the process as mutmut's stats pass in mutmut's own way, before any
    conftest loads; imported anywhere else it marks nothing. Each test's
    start sets its time to 0, its call adds its
    duration (setup and teardown do not), and its teardown files every
    function the test reached under the test's id (mutants/ taken off the
    front) and clears them, as mutmut's own recorder does. At the end a
    worker writes what it recorded to STATS_DIR/WORKERID.json; the process
    controlling the workers writes nothing.

    python3 tests/test_jpt_mutmut_stats.py
"""

import importlib.util
import json
import os
import sys
import tempfile
from pathlib import Path
from types import ModuleType, SimpleNamespace
from unittest import mock

from mutmut.state import MutmutState

from _harness import ROOT, check, run


def _load() -> ModuleType:
    spec = importlib.util.spec_from_file_location("jpt_mutmut_stats", ROOT / "mutmut-plugin" / "jpt_mutmut_stats.py")
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


plugin = _load()


def test_a_stats_worker_is_marked_and_nothing_else_is() -> None:
    marked: list[str] = []
    with mock.patch.object(plugin, "set_mutant_under_test", marked.append):
        got = plugin.mark({plugin.STATS_DIR: "/somewhere"})
        check("a stats pass's worker is marked as mutmut's stats pass, in mutmut's way",
              (got, marked, plugin.STATS), (True, ["stats"], "stats"))
        marked.clear()
        check("anywhere else nothing is marked",
              [(plugin.mark(env), list(marked)) for env in ({}, {plugin.STATS_DIR: ""})], [(False, []), (False, [])])
    marked.clear()
    with mock.patch("mutmut.mutation.trampoline.set_mutant_under_test", marked.append), \
            mock.patch.dict(os.environ, {plugin.STATS_DIR: "/somewhere"}):
        _load()
    check("  and it is marked as the plugin is imported, before any conftest loads", marked, ["stats"])


def test_a_test_s_reach_and_time_are_recorded() -> None:
    s = MutmutState()
    item = SimpleNamespace(_nodeid="mutants/tests/test_a.py::t", nodeid="tests/test_a.py::t")
    with mock.patch.object(plugin, "state", lambda: s):
        s.duration_by_test["tests/test_a.py::t"] = 9.0
        plugin.pytest_runtest_logstart("tests/test_a.py::t", None)
        check("its start sets its time to 0", s.duration_by_test["tests/test_a.py::t"], 0)
        for when, duration in (("setup", 5.0), ("call", 1.25), ("teardown", 7.0), ("call", 0.5)):
            plugin.pytest_runtest_makereport(item, SimpleNamespace(when=when, duration=duration))
        check("only its calls' durations are added", s.duration_by_test["tests/test_a.py::t"], 1.75)
        s._stats.update({"a.x_f", "b.x_g"})
        s.tests_by_mangled_function_name["a.x_f"].add("tests/test_old.py::o")
        plugin.pytest_runtest_teardown(item, None)
        check("its teardown files every function it reached under its id, mutants/ off the front",
              {k: sorted(v) for k, v in s.tests_by_mangled_function_name.items()},
              {"a.x_f": ["tests/test_a.py::t", "tests/test_old.py::o"], "b.x_g": ["tests/test_a.py::t"]})
        check("  and starts the next test with nothing reached", s._stats, set())


def test_a_worker_writes_what_it_recorded() -> None:
    s = MutmutState()
    s.tests_by_mangled_function_name["a.x_f"].update({"tests/b.py::t", "tests/a.py::t"})
    s.duration_by_test["tests/a.py::t"] = 0.5
    s.function_dependencies["a.x_f"].update({"b.x_h", "a.x_g"})
    with tempfile.TemporaryDirectory() as td, mock.patch.dict(os.environ, {plugin.STATS_DIR: td}), \
            mock.patch.object(plugin, "state", lambda: s):
        plugin.pytest_sessionfinish(SimpleNamespace(config=SimpleNamespace(workerinput={"workerid": "gw3"})))
        written = sorted(p.name for p in Path(td).iterdir())
        check("a worker: one file, named for it", written, ["gw3.json"])
        named = Path(td) / "gw3.json"
        record = json.loads(named.read_text(encoding="utf-8")) if named.is_file() else None
        check("  its reach, times and callers, the sets as sorted lists", record,
              {"tests_by_mangled_function_name": {"a.x_f": ["tests/a.py::t", "tests/b.py::t"]},
               "duration_by_test": {"tests/a.py::t": 0.5},
               "function_dependencies": {"a.x_f": ["a.x_g", "b.x_h"]}})
        for f in Path(td).iterdir():
            f.unlink()
        plugin.pytest_sessionfinish(SimpleNamespace(config=SimpleNamespace()))
        check("the controlling process writes nothing", list(Path(td).iterdir()), [])
    check("the directory's name in the environment", plugin.STATS_DIR, "JPT_MUTMUT_STATS_DIR")


if __name__ == "__main__":
    sys.exit(run(globals(), "jpt_mutmut_stats"))
