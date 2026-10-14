#!/usr/bin/env python3
"""
JP_TOOLS/tests/test_mutmut_gate_state.py
mutmut-gate.py judges only what changed since its last run, in one pass.

WHAT IS CHECKED
    - The commit to diff against is the one the gate's last run recorded
      (mutants/mutmut-gate.json) when this clone has it, else mutmut's own;
      the reach to trace again, the edited tests and the sources line all
      diff from it.
    - Before the run, the functions a test in an edited file reaches (by the
      kept reach; every function when every test counts as edited, or no
      reach is kept) have their verdicts cleared, each with its reason; a
      class's test is in its file. After it, what the run tested already is
      not cleared again: a run whose only change edits a test runs once, its
      moved reach included, and records the commit it judged.
    - The codes are every .meta's; the sources line names what changed.

    python3 tests/test_mutmut_gate_state.py
"""

import contextlib
import io
import json
import shutil
import sys
import tempfile
import time
from pathlib import Path
from unittest import mock

from _harness import check, run
from jpcheck import mutmut_log
from mutmut_gate_support import codes as _codes
from mutmut_gate_support import commit as _commit
from mutmut_gate_support import gate
from mutmut_gate_support import head as _head
from mutmut_gate_support import plant as _plant
from mutmut_gate_support import repo as _repo
from mutmut_gate_support import stats as _stats


def test_the_judged_commit_is_the_one_to_diff_against() -> None:
    """The commit the gate's last run recorded, when this clone has it; else
    mutmut's own in the stats."""
    if not shutil.which("git"):
        print("SKIP: git not found")
        return
    with tempfile.TemporaryDirectory() as td:
        root = _repo(Path(td))
        old = _head(root)
        new = _commit(root, {"b.py": "y = 1\n"}, "later")
        mutants = Path(td) / "m"
        mutants.mkdir()
        doc: dict[str, object] = {"git_commit": old}
        check("the record's name, as publish-mutmut-state.sh keeps it", gate.JUDGED, "mutmut-gate.json")
        check("no record: mutmut's", gate._since(root, mutants, doc), old)
        (mutants / gate.JUDGED).write_text(json.dumps({"commit": new}), encoding="utf-8")
        check("a record this clone has: it", gate._since(root, mutants, doc), new)
        for bad in (json.dumps({"commit": "0" * 40}), json.dumps({}), json.dumps(["x"]), "{ not json"):
            (mutants / gate.JUDGED).write_text(bad, encoding="utf-8")
            check(f"a record that names no commit this clone has ({bad[:16]}): mutmut's",
                  gate._since(root, mutants, doc), old)
        (mutants / gate.JUDGED).write_text(json.dumps({"commit": new}), encoding="utf-8")
        stats = mutants / gate.STATS
        stats.write_text(json.dumps({"git_commit": old, "duration_by_test": {"tests/test_b.py::t": 1.0}}),
                         encoding="utf-8")
        _commit(root, {"tests/test_b.py": "t = 1\n"}, "a suite, after the judged commit")
        check("reach to trace again, edited tests and sources: since the judged commit",
              (gate.forget_moved_reach(root, stats), gate.edited_tests(root, stats),
               gate._sources(root, mutants)),
              ("1 file(s) changed since the traced commit: 1 test(s) traced again", frozenset({"tests/test_b.py"}),
               "0 source files kept, 0 changed since the state"))


def test_a_pyproject_change_that_moves_no_test_counts_as_none() -> None:
    """A pyproject.toml change outside what the tests run under is left out of
    what changed; one inside it, or a pyproject.toml new since, is kept."""
    if not shutil.which("git"):
        print("SKIP: git not found")
        return
    with tempfile.TemporaryDirectory() as td:
        root = _repo(Path(td))
        before_any = _head(root)  # no pyproject.toml yet
        old = _commit(root, {"pyproject.toml": '[project]\nversion = "1"\n\n[tool.mutmut]\nsource_paths = ["a.py"]\n',
                             "b.py": ""}, "a pyproject")
        _commit(root, {"pyproject.toml": '[project]\nversion = "2"\n\n[tool.mutmut]\nsource_paths = ["a.py", "b.py"]\n',
                       "b.py": "x = 1\n"}, "a version and a source path")
        check("a version and a source path: not a change the tests see", gate._changed(root, old), {"b.py"})
        check("no pyproject.toml at the commit: a new one counts", gate._changed(root, before_any),
              {"b.py", "pyproject.toml"})
        (root / "pyproject.toml").write_text('[tool.mutmut]\nsource_paths = ["a.py"]\ntimeout_multiplier = 9.0\n',
                                             encoding="utf-8")
        check("mutmut's timeout, in the working tree: one they see", gate._changed(root, old),
              {"b.py", "pyproject.toml"})


def test_edited_tests_reach_is_cleared_before_the_run() -> None:
    with tempfile.TemporaryDirectory() as td:
        mutants = Path(td)
        meta = _plant(mutants, "a.py", {"x_f": "1", "x_g": "2", "x_h": "3"},
                      {"a.x_f__mutmut_1": 1, "a.x_g__mutmut_1": 0, "a.x_h__mutmut_1": 33,
                       "a.x_g__mutmut_2": None})
        before = {"a.x_f": frozenset({"tests/test_e.py::t"}), "a.x_g": frozenset({"tests/test_o.py::K::t",
                                                                                "tests/test_e.py::t2"}),
                  "a.x_h": frozenset({"tests/test_o.py::t"})}
        check("the functions an edited test reaches, by the kept reach: cleared, each with its reason",
              (gate.clear_edited_reach(mutants, before, frozenset({"tests/test_e.py"})), _codes(meta)),
              ({"a.x_f": "tests edited", "a.x_g": "tests edited"},
               {"a.x_f__mutmut_1": None, "a.x_g__mutmut_1": None, "a.x_h__mutmut_1": 33, "a.x_g__mutmut_2": None}))
        everything = {"a.x_f": "every test counts as edited", "a.x_g": "every test counts as edited",
                      "a.x_h": "every test counts as edited"}
        _plant(mutants, "a.py", {"x_f": "1", "x_g": "2", "x_h": "3"}, {"a.x_h__mutmut_1": 33})
        check("every test edited (None): every function", gate.clear_edited_reach(mutants, before, None),
              everything)
        check("  and its verdicts", _codes(meta), {"a.x_h__mutmut_1": None})
        check("no reach kept: every function", gate.clear_edited_reach(mutants, None, frozenset()), everything)
        _plant(mutants, "a.py", {"x_f": "1"}, {"a.x_f__mutmut_1": 1})
        check("nothing edited: nothing cleared", (gate.clear_edited_reach(mutants, before, frozenset()),
                                                  _codes(meta)), ({}, {"a.x_f__mutmut_1": 1}))
        check("a class's test is in its file, the part before the first ::",
              gate.clear_edited_reach(mutants, {"a.x_f": frozenset({"tests/test_e.py::K::t"})},
                                      frozenset({"tests/test_e.py"})), {"a.x_f": "tests edited"})


def test_what_the_run_tested_already_is_not_cleared_after_it() -> None:
    with tempfile.TemporaryDirectory() as td:
        mutants = Path(td)
        meta = _plant(mutants, "a.py", {"x_f": "1", "x_g": "2"}, {"a.x_f__mutmut_1": 1, "a.x_g__mutmut_1": 1})
        _stats(mutants, {"a.x_f": ["t2"], "a.x_g": ["t2"]})
        why: dict[str, str] = {}
        check("reach moved for both, one RETESTED: only the other cleared",
              (gate.reset_moved_verdicts(mutants, {"a.x_f": frozenset({"t1"}), "a.x_g": frozenset({"t1"})},
                                         {"a.x_f": "1", "a.x_g": "2"}, frozenset(), why, ["a.x_f"]),
               _codes(meta), why),
              ((1, 1), {"a.x_f__mutmut_1": 1, "a.x_g__mutmut_1": None}, {"a.x_g": "test reach moved"}))
        _plant(mutants, "a.py", {"x_f": "1", "x_g": "2"}, {"a.x_f__mutmut_1": 1, "a.x_g__mutmut_1": 1})
        check("no reach kept before, all RETESTED: nothing cleared",
              gate.reset_moved_verdicts(mutants, None, {"a.x_f": "1", "a.x_g": "2"}, frozenset(), {},
                                        ["a.x_f", "a.x_g"]), (0, 0))


def test_a_run_whose_only_change_edits_a_test_runs_once() -> None:
    """The verdicts the edited test reaches are cleared before mutmut runs,
    so it tests them in its one run; the run records the commit it judged."""
    if not shutil.which("git"):
        print("SKIP: git not found")
        return
    with tempfile.TemporaryDirectory() as td:
        root = _repo(Path(td))
        traced = _commit(root, {"tests/test_t.py": "t = 1\n"}, "the suite")
        _commit(root, {"tests/test_t.py": "t = 2\n"}, "the suite, edited")
        mutants = gate.prepare(root)
        kept = {"git_commit": traced, "tests_by_mangled_function_name": {"a.x_f": ["tests/test_t.py::t"]},
                "duration_by_test": {"tests/test_t.py::t": 1.0}}
        (mutants / gate.STATS).write_text(json.dumps(kept), encoding="utf-8")
        meta = _plant(mutants, "a.py", {"x_f": "1"}, {"a.x_f__mutmut_1": 0})
        calls: list[tuple[dict[str, int | None], dict[str, str]]] = []

        def fake(cmd: list[str], cwd: Path, env: dict[str, str], relay: mutmut_log.Relay) -> int:
            assert isinstance(relay.plan, mutmut_log.Plan)
            calls.append((_codes(meta), relay.plan.why()))
            # mutmut traces the edited suite again, which now reaches x_f from a second test too,
            # and tests the cleared mutant with it: the moved reach needs no second run
            grown = {**kept, "tests_by_mangled_function_name": {"a.x_f": ["tests/test_t.py::t", "tests/test_t.py::t2"]},
                     "duration_by_test": {"tests/test_t.py::t": 1.0, "tests/test_t.py::t2": 1.0}}
            (mutants / gate.STATS).write_text(json.dumps(grown), encoding="utf-8")
            _plant(mutants, "a.py", {"x_f": "1"}, {"a.x_f__mutmut_1": 1})
            return 0

        out = io.StringIO()
        with mock.patch.object(mutmut_log, "run_relayed", side_effect=fake), \
                mock.patch.object(time, "gmtime", return_value=time.gmtime(0)), contextlib.redirect_stdout(out):
            gate.run(root, False, 2)
        check("one run, the edited test's verdict cleared before it, its reason given",
              calls, [({"a.x_f__mutmut_1": None}, {"a.x_f": "tests edited"})])
        check("  and it says so first", out.getvalue().splitlines()[0],
              "mutmut-gate 00:00:00Z: tests edited since the state was judged: 1 function(s)' verdicts cleared,"
              " tested again in this run")
        check("  and records the commit it judged", json.loads((mutants / gate.JUDGED).read_text(encoding="utf-8")),
              {"commit": _head(root)})
        calls.clear()
        with mock.patch.object(mutmut_log, "run_relayed", side_effect=fake), contextlib.redirect_stdout(io.StringIO()):
            gate.run(root, False, 2)
        check("run again with nothing changed since it judged: the verdict kept, nothing cleared",
              calls, [({"a.x_f__mutmut_1": 1}, {})])
        (mutants / gate.JUDGED).unlink()
        (root / "a.py").write_text("x = 2\n", encoding="utf-8")  # a change not committed
        out = io.StringIO()
        with mock.patch.object(mutmut_log, "run_relayed", side_effect=fake), \
                mock.patch.object(time, "gmtime", return_value=time.gmtime(0)), contextlib.redirect_stdout(out):
            gate.run(root, False, 2)
        check("a tree with changes not committed: no judged commit recorded, and it says so",
              ((mutants / gate.JUDGED).exists(), out.getvalue().splitlines()[-1]),
              (False, "mutmut-gate 00:00:00Z: the tree holds changes not committed: no judged commit recorded"))


def test_the_codes_are_every_meta_s() -> None:
    with tempfile.TemporaryDirectory() as td:
        mutants = Path(td)
        _plant(mutants, "a.py", {}, {"a.x_f__mutmut_1": None, "a.x_f__mutmut_2": 1})
        _plant(mutants, "p/b.py", {}, {"p.b.x_g__mutmut_1": 0})
        (mutants / "c.py.meta").write_text(json.dumps({"hash_by_function_name": {}}), encoding="utf-8")
        check("every mutant's code, across the .meta files, one with none adding nothing", gate._codes(mutants),
              {"a.x_f__mutmut_1": None, "a.x_f__mutmut_2": 1, "p.b.x_g__mutmut_1": 0})


def test_the_sources_line_names_what_changed() -> None:
    if not shutil.which("git"):
        print("SKIP: git not found")
        return
    with tempfile.TemporaryDirectory() as td:
        root = _repo(Path(td))
        mutants = gate.prepare(root)
        check("no state: the kept files, and that nothing says which changed", gate._sources(root, mutants),
              "0 source files kept, no state commit to say which changed")
        (mutants / gate.STATS).write_text("{ not json", encoding="utf-8")
        check("  an unreadable one the same", gate._sources(root, mutants),
              "0 source files kept, no state commit to say which changed")
        (mutants / gate.STATS).write_text(json.dumps({"git_commit": "0" * 40}), encoding="utf-8")
        check("  and a commit this clone does not have", gate._sources(root, mutants),
              "0 source files kept, no state commit to say which changed")
        (mutants / gate.STATS).write_text(json.dumps({"git_commit": _head(root)}), encoding="utf-8")
        _plant(mutants, "a.py", {}, {})
        check("nothing changed since", gate._sources(root, mutants), "1 source files kept, 0 changed since the state")
        _commit(root, {"p/b.py": "", "c.py": "", "README.md": "", "tests/test_x.py": ""}, "two sources")
        check("the changed Python sources named, not the tests or the rest", gate._sources(root, mutants),
              "1 source files kept, 2 changed since the state (c.py, p/b.py)")
        _commit(root, {f"m{n}.py": "" for n in range(3)}, "three more")
        check("  five: all five", gate._sources(root, mutants),
              "1 source files kept, 5 changed since the state (c.py, m0.py, m1.py, m2.py, p/b.py)")
        _commit(root, {f"m{n}.py": "" for n in range(3, 5)}, "two more")
        check("  past five, the first five and an ellipsis", gate._sources(root, mutants),
              "1 source files kept, 7 changed since the state (c.py, m0.py, m1.py, m2.py, m3.py, ...)")


if __name__ == "__main__":
    sys.exit(run(globals(), "mutmut gate state"))
