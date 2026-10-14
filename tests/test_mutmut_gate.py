#!/usr/bin/env python3
"""
JP_TOOLS/tests/test_mutmut_gate.py
mutmut-gate.py holds each function to its baseline, and gets mutants/ ready.

    - The rule: a baseline
      function fails when its survivors or its "no tests" rise; a function
      the baseline lacks fails on any of either. Kills turning into "no
      tests" (the test reaching it gone) fail; "timeout" counts as detected.
    - A status that is no verdict ("suspicious", "not checked") in a number
      the baseline does not hold is NOT_RUN (2), naming it; a finding wins.
    - Results are counted per function, the name before `__mutmut_`.
    - prepare makes mutants/ a worktree of HEAD whose index lists the tracked
      files while none is checked out, links node_modules/ and vendor/, follows
      a new HEAD, and rebuilds the link when the state came from elsewhere.
    - A run that cannot be read is NOT_RUN (2), never a pass.

    python3 tests/test_mutmut_gate.py
"""

import contextlib
import io
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path
from unittest import mock

from _harness import check, check_true, run
from jpcheck import mutmut_log, mutmut_own
from mutmut_gate_support import ENV as _ENV
from mutmut_gate_support import MUTMUT as _MUTMUT
from mutmut_gate_support import codes as _codes
from mutmut_gate_support import commit as _commit
from mutmut_gate_support import gate
from mutmut_gate_support import head as _head
from mutmut_gate_support import ls as _ls
from mutmut_gate_support import plant as _plant
from mutmut_gate_support import repo as _repo
from mutmut_gate_support import run_mutmut as _run_mutmut
from mutmut_gate_support import stats as _stats

RESULTS = """\
    pkg.mod.x_alpha__mutmut_1: killed
    pkg.mod.x_alpha__mutmut_2: survived
    pkg.mod.x_alpha__mutmut_3: survived
    pkg.mod.x_beta__mutmut_1: no tests
    script.x_main__mutmut_1: killed
"""


def test_counts_are_per_function() -> None:
    check("each function's statuses counted, the name before __mutmut_",
          gate.by_function(RESULTS),
          {"pkg.mod.x_alpha": {"killed": 1, "survived": 2},
           "pkg.mod.x_beta": {"no tests": 1},
           "script.x_main": {"killed": 1}})


def test_results_with_no_mutants_are_not_a_pass() -> None:
    try:
        gate.by_function("mutmut said nothing useful\n")
        raised = False
    except gate.GateError:
        raised = True
    check_true("results naming no mutant are an error, not an empty pass", raised)


def test_the_strict_rule() -> None:
    base = {"f.same": {"survived": 2}, "f.fewer": {"survived": 3}, "f.more": {"survived": 1},
            "f.gone": {"survived": 5}, "f.untested": {"no tests": 4}}
    now = {"f.same": {"survived": 2, "killed": 9}, "f.fewer": {"survived": 1},
           "f.more": {"survived": 2}, "f.new_clean": {"killed": 4},
           "f.new_weak": {"killed": 3, "survived": 1}, "f.untested": {"no tests": 6},
           "f.new_untested": {"no tests": 2}}
    check("a rise in either, and a new function's survivor or untested mutant, break the rule, "
          "each said whole", gate.violations(base, now), [
              "f.more: survived 1 -> 2",
              "f.new_untested: NEW, 2 mutant(s) no test reaches; a new function's tests reach all of its",
              "f.new_weak: NEW, 1 mutant(s) survived; a new function kills all of its",
              "f.untested: no tests 4 -> 6"])
    check("a baseline function with no survivor recorded holds 0, and says so",
          gate.violations({"f.clean": {"killed": 3}}, {"f.clean": {"killed": 2, "survived": 1}}),
          ["f.clean: survived 0 -> 1"])


# The baseline: x_f kills all three; x_g kills two, one survives.
BASE = {"m.x_f": {"killed": 3}, "m.x_g": {"killed": 2, "survived": 1}}


def test_reach_lost_or_never_given_fails() -> None:
    check("CONTROL: the baseline itself passes", gate.violations(BASE, BASE), [])
    lost = {**BASE, "m.x_f": {"no tests": 3}}
    check("f's kills becoming no tests (its test gone) fails",
          gate.violations(BASE, lost), ["m.x_f: no tests 0 -> 3"])
    fewer = {**BASE, "m.x_g": {"killed": 1, "no tests": 1, "survived": 1}}
    check("  a single kill becoming no tests fails too",
          gate.violations(BASE, fewer), ["m.x_g: no tests 0 -> 1"])
    untested = {**BASE, "m.x_h": {"no tests": 2}}
    check_true("a new function no test reaches fails",
               [f.split(":")[0] for f in gate.violations(BASE, untested)] == ["m.x_h"])
    timeout = {**BASE, "m.x_f": {"timeout": 3}}
    check("kills becoming timeouts pass: a looping mutant was caught",
          gate.violations(BASE, timeout) + gate.no_verdicts(BASE, timeout), [])


def test_a_status_that_is_no_verdict_is_not_run() -> None:
    for status in ("suspicious", "not checked", "segfault", "skipped"):
        now = {**BASE, "m.x_f": {"killed": 2, status: 1}}
        check(f"{status!r}: no finding", gate.violations(BASE, now), [])
        check("  but no verdict, named", gate.no_verdicts(BASE, now),
              [f"m.x_f: 1 mutant(s) {status!r}, which is no verdict (the baseline has 0)"])
    held = {"m.x_f": {"killed": 2, "suspicious": 1}}
    check("the baseline's own count of one is carried, not judged again",
          gate.no_verdicts(held, held), [])
    check("  in a new function it is judged",
          len(gate.no_verdicts({}, held)), 1)
    more = {"m.x_f": {"killed": 1, "suspicious": 2}}
    check("  a different count of it is judged, naming what the baseline holds",
          gate.no_verdicts(held, more),
          ["m.x_f: 2 mutant(s) 'suspicious', which is no verdict (the baseline has 1)"])


def _printed(baseline: dict[str, dict[str, int]], current: dict[str, dict[str, int]]) -> tuple[int, list[str]]:
    out = io.StringIO()
    with contextlib.redirect_stdout(out):
        rc = gate._gate(baseline, current)
    return rc, out.getvalue().splitlines()


def test_what_the_gate_prints() -> None:
    check("a pass: exit 0, and says PASS", _printed(BASE, BASE), (0, ["mutmut-gate: PASS"]))
    worse = {**BASE, "m.x_g": {"killed": 1, "survived": 2}}
    check("a finding: exit 1, the line, then the count",
          _printed(BASE, worse), (1, ["FAIL m.x_g: survived 1 -> 2", "mutmut-gate: 1 rule(s) broken"]))
    odd = {**BASE, "m.x_f": {"killed": 2, "suspicious": 1}}
    check("no verdict: exit 2, the line, then what could not be judged",
          _printed(BASE, odd), (2, [
              "NOT RUN m.x_f: 1 mutant(s) 'suspicious', which is no verdict (the baseline has 0)",
              "mutmut-gate: could not judge 1 function(s)"]))


def test_prepare() -> None:
    if not shutil.which("git"):
        print("SKIP: git not found")
        return
    with tempfile.TemporaryDirectory() as td:
        root = _repo(Path(td))
        mutants = gate.prepare(root)
        check("git in mutants/ lists the tracked files", _ls(mutants), "a.py\n")
        check_true("  with none checked out (mutmut writes them)", not (mutants / "a.py").exists())
        check_true("  node_modules/ and vendor/ linked, not copied",
                   all((mutants / n).is_symlink() for n in ("node_modules", "vendor")))
        check("  at the checkout's HEAD", _head(mutants), _head(root))

        first = _head(root)
        (root / "a.py").write_text("x = 2\n", encoding="utf-8")
        subprocess.run(["git", "-C", str(root), "commit", "-q", "--no-verify", "-am", "two"],
                       check=True, env=_ENV)
        check_true("CONTROL: the checkout's HEAD moved", _head(root) != first)
        (mutants / "a.py").write_text("x = 'mutated'\n", encoding="utf-8")
        gate.prepare(root)
        check("a new HEAD is followed", _head(mutants), _head(root))
        check("  and a file mutmut wrote is left as it is",
              (mutants / "a.py").read_text(encoding="utf-8"), "x = 'mutated'\n")

        # A state restored from another checkout: its .git link points nowhere.
        (mutants / ".git").write_text("gitdir: /nonexistent/worktrees/mutants\n", encoding="utf-8")
        gate.prepare(root)
        check("a restored state's broken link is rebuilt", _ls(mutants), "a.py\n")
        check("  and mutmut's files survive the rebuild",
              (mutants / "a.py").read_text(encoding="utf-8"), "x = 'mutated'\n")
        check("  leaving no scratch worktree behind",
              sorted(p.name for p in root.iterdir() if p.name.startswith(".mutants-wt-")), [])


def test_a_failed_git_is_named_with_its_error() -> None:
    if not shutil.which("git"):
        print("SKIP: git not found")
        return
    nowhere = "/nonexistent/" + "d" * 400  # git's error quotes the path: longer than 300
    args = ("-C", nowhere, "status")
    said = subprocess.run(["git", *args], capture_output=True, text=True, check=False).stderr.strip()
    check_true("CONTROL: git's own error is longer than the 300 characters kept", len(said) > 300)
    try:
        gate._git(*args)
        raised = ""
    except gate.GateError as e:
        raised = str(e)
    check("a GateError naming the command and the first 300 characters of git's error",
          raised, f"git -C {nowhere} status: {said[:300]}")


def test_is_worktree_of() -> None:
    """Asked directly: prepare() rebuilds whenever this says no, so a wrong
    "no" never shows through prepare alone."""
    if not shutil.which("git"):
        print("SKIP: git not found")
        return
    with tempfile.TemporaryDirectory() as td:
        root = _repo(Path(td))
        mutants = gate.prepare(root)
        check("a worktree of ROOT's repository: yes", gate._is_worktree_of(mutants, root), True)
        check("  asked from either end's path as given, relative to another cwd",
              gate._is_worktree_of(Path(os.path.relpath(mutants)), Path(os.path.relpath(root))), True)
        check("ROOT itself (a .git directory, not a link): no", gate._is_worktree_of(root, root), False)
        plain = Path(td) / "plain"
        plain.mkdir()
        check("a directory with no .git: no", gate._is_worktree_of(plain, root), False)
        (Path(td) / "elsewhere").mkdir()
        other = _repo(Path(td) / "elsewhere")
        check("a worktree of ROOT asked about another repository: no",
              gate._is_worktree_of(mutants, other), False)
        (mutants / ".git").write_text("gitdir: /nonexistent/worktrees/mutants\n", encoding="utf-8")
        check("a .git link git cannot follow: no, not an error", gate._is_worktree_of(mutants, root), False)


def test_run_traces_reach_afresh_and_keeps_results() -> None:
    """Kept stats naming no traced commit cannot say what moved: run drops
    them whole, so all reach is traced, and keeps the mutant results."""
    if not shutil.which("git"):
        print("SKIP: git not found")
        return
    with tempfile.TemporaryDirectory() as td:
        root = _repo(Path(td))
        mutants = gate.prepare(root)
        (mutants / gate.STATS).write_text("{}", encoding="utf-8")
        (mutants / "a.py.meta").write_text("{}", encoding="utf-8")
        asked = _run_mutmut(root, full=False)
        check("mutmut was run as a module of this python, with the children asked for",
              [c["cmd"] for c in asked],
              [_MUTMUT])
        check("  from the checkout", [c["cwd"] for c in asked], [root])
        check_true("  after the kept test reach was dropped", not (mutants / gate.STATS).exists())
        check_true("  and with the kept mutant results left in place", (mutants / "a.py.meta").exists())
        check("a run with no stats kept runs just the same", len(_run_mutmut(root, full=False)), 1)


def test_only_the_reach_a_change_can_move_is_traced_again() -> None:
    """The tests in a changed file, and the tests reaching a function in a
    changed source file, are taken out of the kept stats; the rest stay."""
    if not shutil.which("git"):
        print("SKIP: git not found")
        return
    with tempfile.TemporaryDirectory() as td:
        root = _repo(Path(td))
        traced = _commit(root, {"pkg/mod.py": "y = 1\n", "b.py": "z = 1\n",
                                "tests/test_c.py": "", "tests/test_d.py": ""}, "traced")
        stats = Path(td) / "stats.json"
        # t6 is reached but has no duration kept; t5 is a class's test; t7's
        # module (c) does not change and stays.
        reach = {"a.x_f": ["tests/test_d.py::t1"],
                 "pkg.mod.x_g": ["tests/test_d.py::t2", "tests/test_e.py::t6"],
                 "b.x_h": ["tests/test_d.py::t3"], "c.x_k": ["tests/test_d.py::t7"]}
        durations = dict.fromkeys(("tests/test_d.py::t1", "tests/test_d.py::t2", "tests/test_d.py::t3",
                                   "tests/test_c.py::t4", "tests/test_c.py::K::t5",
                                   "tests/test_d.py::t7"), 1.0)
        stats.write_text(json.dumps({"git_commit": traced, "tests_by_mangled_function_name": reach,
                                     "duration_by_test": durations}), encoding="utf-8")
        _commit(root, {"a.py": "x = 3\n", "pkg/mod.py": "y = 2\n", "tests/test_c.py": "# changed\n"},
                "the change")
        (root / "b.py").write_text("z = 2\n", encoding="utf-8")  # changed, not committed
        said = gate.forget_moved_reach(root, stats)
        doc = json.loads(stats.read_text(encoding="utf-8"))
        check("the tests reaching a changed module, and in a changed test file, go",
              sorted(doc["duration_by_test"]), ["tests/test_d.py::t7"])
        check("  and leave the reach they had", doc["tests_by_mangled_function_name"],
              {"a.x_f": [], "pkg.mod.x_g": [], "b.x_h": [], "c.x_k": ["tests/test_d.py::t7"]})
        check("  and it says how many: an uncommitted change counts too", said,
              "4 file(s) changed since the traced commit: 6 test(s) traced again")


def test_a_file_tests_share_has_all_reach_traced() -> None:
    check("which files the tests share",
          {p: gate._shared_by_tests(p) for p in (
              "tests/conftest.py", "conftest.py", "tests/_harness.py", "tests/fixtures/a.json",
              "tests/_lab_python_cases.py", "tests/test_x.py", "tests/mutants/spec.json",
              "jpcheck/lab_use.py", "README.md", "pyproject.toml", "pytest.ini", "setup.cfg",
              "tox.ini", "sub/pyproject.toml")},
          {"tests/conftest.py": True, "conftest.py": True, "tests/_harness.py": True,
           "tests/fixtures/a.json": True, "tests/_lab_python_cases.py": True,
           "tests/test_x.py": False, "tests/mutants/spec.json": False,
           "jpcheck/lab_use.py": False, "README.md": False, "pyproject.toml": True,
           "pytest.ini": True, "setup.cfg": True, "tox.ini": True, "sub/pyproject.toml": False})
    if not shutil.which("git"):
        print("SKIP: git not found")
        return
    with tempfile.TemporaryDirectory() as td:
        root = _repo(Path(td))
        stats = Path(td) / "stats.json"
        stats.write_text(json.dumps({"git_commit": _head(root), "duration_by_test": {"tests/t.py::t": 1.0}}),
                         encoding="utf-8")
        _commit(root, {"tests/_harness.py": "# changed\n"}, "a helper")
        check("a helper changed: all reach traced, naming it", gate.forget_moved_reach(root, stats),
              "tests/_harness.py changed, which tests share: all test reach traced")
        check_true("  and the kept reach is gone", not stats.exists())


def test_a_fixture_only_suites_name_moves_only_their_tests() -> None:
    """A data file that suites name by its file name moves only their tests;
    named by a helper, or by nobody, it moves them all; a Python helper, a
    conftest.py and the configuration always do."""
    if not shutil.which("git"):
        print("SKIP: git not found")
        return
    with tempfile.TemporaryDirectory() as td:
        root = _repo(Path(td))
        # Each suite names its fixture by the file name alone, never by its
        # directory, and test_d names every file whose change moves all reach
        # (the configuration, a conftest.py, a Python fixture), so only the
        # rule, not "nobody names it", can send those to a full trace.
        traced = _commit(root, {"tests/test_a.py": 'F = FIXTURES / "help.txt"\n',
                                "tests/test_b.py": 'G = FIXTURES / "other.txt"\n',
                                "tests/test_c.py": "# names nothing\n",
                                "tests/test_d.py": "# pyproject.toml pytest.ini setup.cfg tox.ini conftest.py"
                                                   " sample.py\n",
                                "tests/_helper.py": 'H = FIXTURES / "shared.txt"\n',
                                "tools/elsewhere.py": 'X = "help.txt"  # not a test file: never counted\n',
                                "tests/fixtures/help.txt": "1\n", "tests/fixtures/other.txt": "1\n",
                                "tests/fixtures/shared.txt": "1\n", "tests/fixtures/orphan.txt": "1\n",
                                "tests/fixtures/sample.py": "x = 1\n"},
                        "traced")
        check("help.txt: named by test_a.py alone (a file outside tests/ is not a reader)",
              gate._suites_naming(root, "tests/fixtures/help.txt"), {"tests/test_a.py"})
        check("shared.txt: named by a helper, so any test", gate._suites_naming(root, "tests/fixtures/shared.txt"),
              None)
        check("orphan.txt: named by no suite, so any test", gate._suites_naming(root, "tests/fixtures/orphan.txt"),
              None)
        check("a Python file, a conftest.py and the configuration: any test, even when a suite names them",
              [gate._suites_naming(root, p) for p in ("tests/fixtures/sample.py", "tests/_helper.py",
                                                       "tests/conftest.py", "pyproject.toml", "pytest.ini",
                                                       "setup.cfg", "tox.ini")],
              [None] * 7)
        check("a suite is test_*.py under tests/, at any depth",
              [gate._is_suite(p) for p in ("tests/test_x.py", "tests/sub/test_x.py", "tests/x_test.py",
                                           "lib/test_x.py", "tests/test_x.txt", "tests/sub/x.py")],
              [True, True, False, False, False, False])
        stats = Path(td) / "stats.json"
        durations = dict.fromkeys(("tests/test_a.py::t1", "tests/test_b.py::t2", "tests/test_c.py::t3"), 1.0)
        reach = {"a.x_f": ["tests/test_a.py::t1", "tests/test_b.py::t2", "tests/test_c.py::t3"]}

        def kept() -> None:
            stats.write_text(json.dumps({"git_commit": traced, "tests_by_mangled_function_name": dict(reach),
                                         "duration_by_test": dict(durations)}), encoding="utf-8")

        kept()
        _commit(root, {"tests/fixtures/help.txt": "2\n"}, "the help fixture")
        said = gate.forget_moved_reach(root, stats)
        doc = json.loads(stats.read_text(encoding="utf-8"))
        check("help.txt changed: only test_a's tests are traced again", sorted(doc["duration_by_test"]),
              ["tests/test_b.py::t2", "tests/test_c.py::t3"])
        check("  and its reach goes, the others' stays", doc["tests_by_mangled_function_name"],
              {"a.x_f": ["tests/test_b.py::t2", "tests/test_c.py::t3"]})
        check("  and it says so, naming the reader", said,
              "1 file(s) changed since the traced commit: 1 test(s) traced again,"
              " the shared files' readers among them (tests/test_a.py)")
        kept()
        _commit(root, {"tests/fixtures/help.txt": "3\n", "tests/fixtures/other.txt": "2\n"}, "two fixtures")
        said = gate.forget_moved_reach(root, stats)
        doc = json.loads(stats.read_text(encoding="utf-8"))
        check("two fixtures changed, each named by its own suite: both suites' tests go",
              sorted(doc["duration_by_test"]), ["tests/test_c.py::t3"])
        check("  and it names both readers", said,
              "2 file(s) changed since the traced commit: 2 test(s) traced again,"
              " the shared files' readers among them (tests/test_a.py, tests/test_b.py)")
        kept()
        _commit(root, {"tests/fixtures/shared.txt": "2\n"}, "the helper's fixture")
        check("shared.txt changed: all reach traced, naming it", gate.forget_moved_reach(root, stats),
              "tests/fixtures/shared.txt changed, which tests share: all test reach traced")
        check_true("  and the kept reach is gone", not stats.exists())


def test_kept_reach_missing_its_tables_is_read_as_empty() -> None:
    if not shutil.which("git"):
        print("SKIP: git not found")
        return
    with tempfile.TemporaryDirectory() as td:
        root = _repo(Path(td))
        stats = Path(td) / "stats.json"
        stats.write_text(json.dumps({"git_commit": _head(root)}), encoding="utf-8")
        check("no reach or duration tables: nothing to take out",
              gate.forget_moved_reach(root, stats),
              "0 file(s) changed since the traced commit: 0 test(s) traced again")
        check("  and the file keeps them, empty", json.loads(stats.read_text(encoding="utf-8")),
              {"git_commit": _head(root), "tests_by_mangled_function_name": {}, "duration_by_test": {}})


def test_run_keeps_the_reach_nothing_moved() -> None:
    if not shutil.which("git"):
        print("SKIP: git not found")
        return
    with tempfile.TemporaryDirectory() as td:
        root = _repo(Path(td))
        mutants = gate.prepare(root)
        kept = {"git_commit": _head(root), "tests_by_mangled_function_name": {"a.x_f": ["tests/t.py::t"]},
                "duration_by_test": {"tests/t.py::t": 1.0}}
        (mutants / gate.STATS).write_text(json.dumps(kept), encoding="utf-8")
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            _run_mutmut(root, full=False)
        check("run keeps reach traced at its own commit, whole",
              json.loads((mutants / gate.STATS).read_text(encoding="utf-8")), kept)
        check_true("  and says so", "0 test(s) traced again" in out.getvalue())


def test_kept_reach_that_cannot_be_read_or_placed_is_traced_whole() -> None:
    if not shutil.which("git"):
        print("SKIP: git not found")
        return
    with tempfile.TemporaryDirectory() as td:
        root = _repo(Path(td))
        stats = Path(td) / "stats.json"
        check("none kept", gate.forget_moved_reach(root, stats), "no test reach kept: all of it traced")
        stats.write_text("{not json", encoding="utf-8")
        check("unreadable: dropped", gate.forget_moved_reach(root, stats),
              "kept test reach unreadable: all of it traced")
        check_true("  and gone", not stats.exists())
        stats.write_text(json.dumps({"git_commit": "0" * 40}), encoding="utf-8")
        said = gate.forget_moved_reach(root, stats)
        check_true("a commit this clone does not have: all traced, saying which",
                   said.startswith("cannot diff against the traced commit " + "0" * 40))
        check_true("  and gone", not stats.exists())


def test_a_module_name_is_its_path() -> None:
    check("module names", [gate._module(p) for p in
                           ("jpcheck/lab_use.py", "jpcheck/__init__.py", "mutmut-gate.py", "README.md")],
          ["jpcheck.lab_use", "jpcheck", "mutmut-gate", None])


def test_verdicts_of_moved_reach_are_cleared() -> None:
    with tempfile.TemporaryDirectory() as td:
        mutants = Path(td)
        hashes = {"x_new": "1", "x_lost": "2", "x_same": "3", "x_edited": "4"}
        meta = _plant(mutants, "pkg/m.py", hashes, {
            "pkg.m.x_new__mutmut_1": 33, "pkg.m.x_new__mutmut_2": 33, "pkg.m.x_lost__mutmut_1": 1,
            "pkg.m.x_same__mutmut_1": 0, "pkg.m.x_edited__mutmut_1": 0, "pkg.m.x_lost__mutmut_2": None})
        before = {"pkg.m.x_lost": frozenset({"t1"}), "pkg.m.x_same": frozenset({"t1"}),
                  "pkg.m.x_edited": frozenset({"t1"})}
        _stats(mutants, {"pkg.m.x_new": ["t1"], "pkg.m.x_same": ["t1"], "pkg.m.x_edited": ["t2"]})
        why: dict[str, str] = {}
        said = gate.reset_moved_verdicts(mutants, before, {**{f"pkg.m.{k}": v for k, v in hashes.items()},
                                                           "pkg.m.x_edited": "old"}, frozenset(), why)
        check("newly reached (no tests) and no longer reached (killed): cleared, 3 verdicts of 2 functions",
              said, (3, 2))
        check("  and why, for each", why, {"pkg.m.x_new": "test reach moved", "pkg.m.x_lost": "test reach moved"})
        check("  each one", _codes(meta), {
            "pkg.m.x_new__mutmut_1": None, "pkg.m.x_new__mutmut_2": None, "pkg.m.x_lost__mutmut_1": None,
            "pkg.m.x_same__mutmut_1": 0, "pkg.m.x_edited__mutmut_1": 0, "pkg.m.x_lost__mutmut_2": None})
        doc = json.loads(meta.read_text(encoding="utf-8"))
        check("  the file stays exactly as mutmut writes it: its keys, indent 4",
              meta.read_text(encoding="utf-8"), json.dumps(doc, indent=4))
        check("  all five keys kept", sorted(doc), ["durations_by_key", "estimated_durations_by_key",
                                                  "exit_code_by_key", "hash_by_function_name",
                                                  "type_check_error_by_key"])
        check("nothing moved: nothing cleared, the file untouched",
              gate.reset_moved_verdicts(mutants, {"pkg.m.x_new": frozenset({"t1"}),
                                                  "pkg.m.x_same": frozenset({"t1"}),
                                                  "pkg.m.x_edited": frozenset({"t2"})},
                                        {f"pkg.m.{k}": v for k, v in hashes.items()}, frozenset(), {}), (0, 0))


def test_verdicts_reached_by_an_edited_test_are_cleared() -> None:
    """The same tests reach a function before and after, but one of them was
    edited: its verdicts are tested again, since the edited test may now kill
    a survivor or have stopped killing. Every test edited (None): all of them."""
    with tempfile.TemporaryDirectory() as td:
        mutants = Path(td)
        hashes = {"x_by_edited": "1", "x_by_other": "2", "x_by_both": "3"}
        same = {"pkg.m.x_by_edited": frozenset({"tests/test_e.py::t"}),
                "pkg.m.x_by_other": frozenset({"tests/test_o.py::t"}),
                "pkg.m.x_by_both": frozenset({"tests/test_o.py::t", "tests/test_e.py::K::t"})}
        verdicts: dict[str, int | None] = {"pkg.m.x_by_edited__mutmut_1": 0, "pkg.m.x_by_other__mutmut_1": 0,
                                           "pkg.m.x_by_both__mutmut_1": 1}
        meta = _plant(mutants, "pkg/m.py", hashes, verdicts)
        _stats(mutants, {k: sorted(v) for k, v in same.items()})
        full = {f"pkg.m.{k}": v for k, v in hashes.items()}
        why: dict[str, str] = {}
        check("tests/test_e.py edited: the two functions it reaches are cleared, the other kept, saying why",
              (gate.reset_moved_verdicts(mutants, same, full, frozenset({"tests/test_e.py"}), why), _codes(meta),
               why),
              ((2, 2), {"pkg.m.x_by_edited__mutmut_1": None, "pkg.m.x_by_other__mutmut_1": 0,
                        "pkg.m.x_by_both__mutmut_1": None},
               {"pkg.m.x_by_edited": "tests edited", "pkg.m.x_by_both": "tests edited"}))
        meta = _plant(mutants, "pkg/m.py", hashes, verdicts)
        check("nothing edited: nothing cleared",
              gate.reset_moved_verdicts(mutants, same, full, frozenset(), {}), (0, 0))
        why = {}
        check("every test edited (None): all cleared, saying so",
              (gate.reset_moved_verdicts(mutants, same, full, None, why), why),
              ((3, 3), dict.fromkeys(full, "every test counts as edited")))


def test_edited_tests_names_the_changed_suites_and_fixture_readers() -> None:
    if not shutil.which("git"):
        print("SKIP: git not found")
        return
    with tempfile.TemporaryDirectory() as td:
        root = _repo(Path(td))
        traced = _commit(root, {"tests/test_a.py": "a = 1\n", "tests/test_b.py": 'F = FIX / "help.txt"\n',
                                "tests/test_c.py": "c = 1\n", "tests/fixtures/help.txt": "1\n",
                                "tests/conftest.py": "x = 1\n", "lib.py": "y = 1\n"}, "traced")
        stats = Path(td) / "stats.json"
        stats.write_text(json.dumps({"git_commit": traced}), encoding="utf-8")
        _commit(root, {"tests/test_a.py": "a = 2\n", "tests/fixtures/help.txt": "2\n", "lib.py": "y = 2\n"},
                "a suite, a fixture and a source file")
        check("the changed suite and the fixture's reader, not the source file",
              gate.edited_tests(root, stats), frozenset({"tests/test_a.py", "tests/test_b.py"}))
        _commit(root, {"tests/conftest.py": "x = 2\n"}, "conftest")
        check("a conftest.py changed: every test (None)", gate.edited_tests(root, stats), None)
        stats.write_text(json.dumps({"git_commit": "0" * 40}), encoding="utf-8")
        check("a commit this clone does not have: every test", gate.edited_tests(root, stats), None)
        stats.write_text("{ not json", encoding="utf-8")
        check("an unreadable trace: every test", gate.edited_tests(root, stats), None)
        stats.unlink()
        check("no trace kept: every test", gate.edited_tests(root, stats), None)
        stats.write_text(json.dumps({"git_commit": _head(root)}), encoding="utf-8")
        check("nothing changed since: no test", gate.edited_tests(root, stats), frozenset())


def test_the_second_pass_names_the_cleared_mutants() -> None:
    """The mutants with no verdict, sorted, across every .meta; too many names
    for one command line: none, so the run goes unnamed."""
    with tempfile.TemporaryDirectory() as td:
        mutants = Path(td)
        _plant(mutants, "b.py", {"x_g": "1"}, {"b.x_g__mutmut_2": None, "b.x_g__mutmut_1": 1})
        _plant(mutants, "a.py", {"x_f": "1"}, {"a.x_f__mutmut_1": None, "a.x_f__mutmut_2": 0})
        check("the cleared ones, in order", gate._cleared(mutants), ["a.x_f__mutmut_1", "b.x_g__mutmut_2"])
        check("none cleared: none", gate._cleared(Path(td) / "nowhere"), [])
        (mutants / "c.py.meta").write_text(json.dumps({"hash_by_function_name": {}}), encoding="utf-8")
        check("a .meta with no verdicts at all adds none", gate._cleared(mutants),
              ["a.x_f__mutmut_1", "b.x_g__mutmut_2"])
    fits = ["m" * 1023] * 256                    # 256 x (1023 + 1) bytes: exactly the limit
    check("names that fit are kept", gate._named(fits), fits)
    check("one byte over: an unnamed run", gate._named([*fits[:-1], "m" * 1024]), [])
    check("no names: none", gate._named([]), [])


def test_reach_compared_as_sets_an_empty_one_included() -> None:
    """A function with no reach recorded and one recorded as reached by no
    test are the same; neither has moved. A meta key holding __mutmut_ twice
    is read the way mutmut reads it, up to the first, so it counts as the
    same function as a plain a.x key."""
    with tempfile.TemporaryDirectory() as td:
        mutants = Path(td)
        meta = _plant(mutants, "a.py", {"x_e": "1", "x_d": "2", "x__mutmut_odd": "3"},
                      {"a.x_e__mutmut_1": 33, "a.x_d__mutmut_1": 33, "a.x__mutmut_1": 1,
                       "a.x__mutmut_odd__mutmut_1": 1})
        _stats(mutants, {"a.x_e": [], "a.x": ["t2"]})
        said = gate.reset_moved_verdicts(mutants, {"a.x_d": frozenset(), "a.x": frozenset({"t1"})},
                                         {"a.x_e": "1", "a.x_d": "2", "a.x__mutmut_odd": "3"}, frozenset(), {})
        check("empty reach on either side is no reach: not moved; the odd key follows mutmut's split",
              (said, _codes(meta)),
              ((2, 1), {"a.x_e__mutmut_1": 33, "a.x_d__mutmut_1": 33, "a.x__mutmut_1": None,
                        "a.x__mutmut_odd__mutmut_1": None}))


def test_verdicts_cleared_in_every_meta_are_counted() -> None:
    with tempfile.TemporaryDirectory() as td:
        mutants = Path(td)
        _plant(mutants, "a.py", {"x_f": "1"}, {"a.x_f__mutmut_1": 1, "a.x_f__mutmut_2": 1})
        _plant(mutants, "b.py", {"x_g": "1"}, {"b.x_g__mutmut_1": 0})
        _stats(mutants, {"a.x_f": ["t"], "b.x_g": ["t"]})
        check("two files: their cleared verdicts and functions summed",
              gate.reset_moved_verdicts(mutants, {}, {"a.x_f": "1", "b.x_g": "1"}, frozenset(), {}), (3, 2))


def test_no_reach_before_clears_all_but_what_was_just_tested() -> None:
    with tempfile.TemporaryDirectory() as td:
        mutants = Path(td)
        meta = _plant(mutants, "a.py", {"x_f": "1", "x_g": "2", "x_h": "3"},
                      {"a.x_f__mutmut_1": 1, "a.x_g__mutmut_1": 0, "a.x_h__mutmut_1": 33})
        _stats(mutants, {"a.x_f": ["t"]})
        check("no reach recorded before: every verdict whose code did not change is cleared, "
              "a function no test reaches included",
              gate.reset_moved_verdicts(mutants, None, {"a.x_f": "1", "a.x_g": "old", "a.x_h": "3"}, frozenset(),
                                        {}),
              (2, 2))
        check("  the edited function's fresh verdict stays", _codes(meta),
              {"a.x_f__mutmut_1": None, "a.x_g__mutmut_1": 0, "a.x_h__mutmut_1": None})
        meta = _plant(mutants, "a.py", {"x_f": "1"}, {"a.x_f__mutmut_1": 1})
        check("a run from nothing (--full): every verdict is fresh, none cleared",
              gate.reset_moved_verdicts(mutants, None, {}, frozenset(), {}), (0, 0))
        (mutants / gate.STATS).unlink()
        check("no reach after either: read as none",
              gate.reset_moved_verdicts(mutants, {"a.x_f": frozenset({"t"})}, {"a.x_f": "1"}, frozenset(), {}),
              (1, 1))
        (mutants / "hook.sh.meta").write_text("not json", encoding="utf-8")
        check_true("  a non-Python .meta is not read", gate._metas(mutants) == [(meta, "a")])


def test_run_tests_moved_reach_again() -> None:
    if not shutil.which("git"):
        print("SKIP: git not found")
        return
    with tempfile.TemporaryDirectory() as td:
        root = _repo(Path(td))
        mutants = gate.prepare(root)
        kept = {"git_commit": _head(root), "tests_by_mangled_function_name": {},
                "duration_by_test": {"tests/t.py::t": 1.0}}
        (mutants / gate.STATS).write_text(json.dumps(kept), encoding="utf-8")
        meta = _plant(mutants, "a.py", {"x_f": "1"}, {"a.x_f__mutmut_1": 33})
        calls: list[dict[str, int | None]] = []
        asked: list[tuple[object, object]] = []
        plans: list[tuple[list[str], str, str, dict[str, str]]] = []

        def fake(cmd: list[str], cwd: Path, env: dict[str, str], relay: mutmut_log.Relay) -> int:
            calls.append(_codes(meta))
            asked.append((cmd, cwd))
            p = relay.plan
            assert isinstance(p, mutmut_log.Plan)
            plans.append((p.names, p.traced, p.sources, p.why()))
            check("  each plan reads the mutants' codes as they are", p.codes(), _codes(meta))
            if len(calls) == 1:  # mutmut's trace: tests/t.py::t now reaches a.x_f
                (mutants / gate.STATS).write_text(json.dumps(
                    {**kept, "tests_by_mangled_function_name": {"a.x_f": ["tests/t.py::t"]}}),
                    encoding="utf-8")
            else:  # mutmut tests the cleared mutant: killed
                _plant(mutants, "a.py", {"x_f": "1"}, {"a.x_f__mutmut_1": 1})
            return 0

        out = io.StringIO()
        with mock.patch.object(mutmut_log, "run_relayed", side_effect=fake), \
                mock.patch.object(time, "gmtime", return_value=time.gmtime(0)), \
                contextlib.redirect_stdout(out):
            gate.run(root, False, 2)
        check("a test newly reached a function: mutmut ran again, with its verdict cleared",
              calls, [{"a.x_f__mutmut_1": 33}, {"a.x_f__mutmut_1": None}])
        first = _MUTMUT
        check("  the first run names nothing, the second exactly the cleared mutant, from the same place",
              asked, [(first, root), ([*first, "a.x_f__mutmut_1"], root)])
        check("  each run's plan: what it names, traces and rebuilds, and why each function is tested",
              plans, [([], "0 file(s) changed since the traced commit: 0 test(s) traced again",
                       "1 source files kept, 0 changed since the state", {}),
                      (["a.x_f__mutmut_1"], "the first run traced it", "none changed since the first run",
                       {"a.x_f": "test reach moved"})])
        check("  and says so, each line stamped",
              out.getvalue().splitlines(),
              [("mutmut-gate 00:00:00Z: tests edited since the state was judged: 0 function(s)' verdicts cleared,"
                " tested again in this run"),
               "mutmut-gate 00:00:00Z: 0 file(s) changed since the traced commit: 0 test(s) traced again",
               ("mutmut-gate 00:00:00Z: test reach moved for 1 function(s): their 1 verdict(s)"
                " tested again (by name, 1 mutant(s))")])
        calls.clear()
        with mock.patch.object(mutmut_log, "run_relayed", side_effect=fake), \
                contextlib.redirect_stdout(io.StringIO()):
            gate.run(root, False, 2)
        check("reach unchanged since: mutmut runs once, the new verdict kept", calls, [{"a.x_f__mutmut_1": 1}])


def test_run_goes_unnamed_when_the_names_do_not_fit() -> None:
    """The cleared mutants' names past NAMES_MAX_BYTES: the second run names
    none, and says why."""
    if not shutil.which("git"):
        print("SKIP: git not found")
        return
    with tempfile.TemporaryDirectory() as td:
        root = _repo(Path(td))
        mutants = gate.prepare(root)
        kept = {"git_commit": _head(root), "tests_by_mangled_function_name": {},
                "duration_by_test": {"tests/t.py::t": 1.0}}
        (mutants / gate.STATS).write_text(json.dumps(kept), encoding="utf-8")
        _plant(mutants, "a.py", {"x_f": "1"}, {"a.x_f__mutmut_1": 33})
        asked: list[list[str]] = []

        def fake(cmd: list[str], cwd: Path, env: dict[str, str], relay: object) -> int:
            asked.append(cmd)
            if len(asked) == 1:  # mutmut's trace: tests/t.py::t now reaches a.x_f
                (mutants / gate.STATS).write_text(json.dumps(
                    {**kept, "tests_by_mangled_function_name": {"a.x_f": ["tests/t.py::t"]}}),
                    encoding="utf-8")
            return 0

        out = io.StringIO()
        with mock.patch.object(mutmut_log, "run_relayed", side_effect=fake), \
                mock.patch.object(gate, "NAMES_MAX_BYTES", len("a.x_f__mutmut_1")), \
                mock.patch.object(time, "gmtime", return_value=time.gmtime(0)), \
                contextlib.redirect_stdout(out):
            gate.run(root, False, 2)
        check("one byte too many for the name: both runs name nothing", asked, [_MUTMUT, _MUTMUT])
        check("  and the second says why, the whole line",
              out.getvalue().splitlines()[-1],
              "mutmut-gate 00:00:00Z: test reach moved for 1 function(s): their 1 verdict(s)"
              " tested again (unnamed: too many names for one command line)")


def test_run_puts_its_python_first_on_path() -> None:
    if not shutil.which("git"):
        print("SKIP: git not found")
        return
    here = str(Path(sys.executable).parent)
    with tempfile.TemporaryDirectory() as td:
        root = _repo(Path(td))
        git = str(Path(shutil.which("git") or "git").parent)
        env = {"PATH": git, "HOME": td, "KEEP": "1"}
        [call] = _run_mutmut(root, full=False, env=env)
        seen = call["env"]
        assert isinstance(seen, dict)
        check("PATH is mutmut's python's directory, then the PATH it had",
              seen["PATH"], os.pathsep.join([here, git]))
        check("  and the rest of the environment is kept, Python told not to buffer its output, the clean run"
              " given the children", {k: v for k, v in seen.items() if k != "PATH"},
              {"HOME": td, "KEEP": "1", "PYTHONUNBUFFERED": "1", "MUTMUT_CLEAN_WORKERS": "2"})
        [bare] = _run_mutmut(root, full=False, env={"HOME": td})
        seen = bare["env"]
        assert isinstance(seen, dict)
        check("no PATH at all: its python's directory and an empty rest",
              seen["PATH"], here + os.pathsep)


def test_run_full_starts_from_nothing() -> None:
    if not shutil.which("git"):
        print("SKIP: git not found")
        return
    with tempfile.TemporaryDirectory() as td:
        root = _repo(Path(td))
        mutants = gate.prepare(root)
        (mutants / "a.py.meta").write_text("{}", encoding="utf-8")
        stale = Path(td) / "stale-wt"
        subprocess.run(["git", "-C", str(root), "worktree", "add", "-q", "--detach", str(stale)],
                       check=True, env=_ENV)
        shutil.rmtree(stale)
        check_true("CONTROL: git lists a worktree whose directory is gone",
                   str(stale) in _worktrees(root))
        _run_mutmut(root, full=True)
        check_true("--full drops every kept result", not (mutants / "a.py.meta").exists())
        check_true("  and prunes the worktree that was gone", str(stale) not in _worktrees(root))
        check("  and mutants/ is ready again", _ls(mutants), "a.py\n")


def test_run_says_how_mutmut_failed() -> None:
    if not shutil.which("git"):
        print("SKIP: git not found")
        return
    with tempfile.TemporaryDirectory() as td:
        root = _repo(Path(td))
        try:
            _run_mutmut(root, full=False, rc=3)
            said = ""
        except gate.GateError as e:
            said = str(e)
    check("mutmut exiting 3 is a GateError naming the code", said, "mutmut run exited 3")


def _worktrees(root: Path) -> str:
    return subprocess.run(["git", "-C", str(root), "worktree", "list", "--porcelain"],
                          capture_output=True, text=True, check=True).stdout


def test_the_command_line() -> None:
    with tempfile.TemporaryDirectory() as td:
        tmp = Path(td)
        res = tmp / "results.txt"
        res.write_text(RESULTS, encoding="utf-8")
        base = tmp / "base.json"
        check("record exits OK", gate.main(["record", str(base), "--results", str(res)]), 0)
        check("  and writes the per-function counts",
              json.loads(base.read_text(encoding="utf-8")), gate.by_function(RESULTS))
        check("gate against its own record: OK",
              gate.main(["gate", str(base), "--results", str(res)]), 0)
        worse = tmp / "worse.txt"
        worse.write_text(RESULTS + "    pkg.mod.x_alpha__mutmut_4: survived\n", encoding="utf-8")
        check("one more survivor: FINDINGS (1)",
              gate.main(["gate", str(base), "--results", str(worse)]), 1)
        odd = tmp / "odd.txt"
        odd.write_text(RESULTS + "    pkg.mod.x_alpha__mutmut_4: suspicious\n", encoding="utf-8")
        check("a mutant with no verdict: NOT_RUN (2)",
              gate.main(["gate", str(base), "--results", str(odd)]), 2)
        both = tmp / "both.txt"
        both.write_text(RESULTS + "    pkg.mod.x_alpha__mutmut_4: suspicious\n"
                        "    pkg.mod.x_alpha__mutmut_5: survived\n", encoding="utf-8")
        check("  and with a finding too, the finding wins: FINDINGS (1)",
              gate.main(["gate", str(base), "--results", str(both)]), 1)
        check("a missing baseline: NOT_RUN (2)",
              gate.main(["gate", str(tmp / "nope.json"), "--results", str(res)]), 2)
        check("missing results: NOT_RUN (2)",
              gate.main(["gate", str(base), "--results", str(tmp / "nope.txt")]), 2)
        check("a usage error: NOT_RUN (2)", gate.main(["gate"]), 2)
        (tmp / "mutants").mkdir()
        with mock.patch("pathlib.Path.cwd", return_value=tmp):
            check("--pr with no record of what the PR judged: NOT_RUN (2)",
                  gate.main(["gate", str(base), "--results", str(worse), "--pr"]), 2)
            (tmp / "mutants" / gate.JUDGED).write_text(json.dumps({"commit": "c", "own": ["script.x_main"]}),
                                                       encoding="utf-8")
            check("--pr, the worse function not the PR's: OK",
                  gate.main(["gate", str(base), "--results", str(worse), "--pr"]), 0)
            (tmp / "mutants" / gate.JUDGED).write_text(json.dumps({"commit": "c", "own": ["pkg.mod.x_alpha"]}),
                                                       encoding="utf-8")
            check("--pr, the worse function the PR's: FINDINGS (1)",
                  gate.main(["gate", str(base), "--results", str(worse), "--pr"]), 1)


def _held_said(baseline: dict[str, dict[str, int]], current: dict[str, dict[str, int]],
               own: set[str] | None) -> tuple[dict[str, dict[str, int]], list[str]]:
    out = io.StringIO()
    with contextlib.redirect_stdout(out):
        kept = mutmut_own.hold(baseline, current, own)
    return kept, out.getvalue().splitlines()


def test_a_pull_request_is_held_for_what_it_judged() -> None:
    """A newer state's baseline describes the state's code: a function the
    PR's runs never judged carries older verdicts and is not compared."""
    later = {"m.x_f": {"killed": 3}, "m.x_g": {"killed": 3}}  # master killed m.x_g's survivor
    kept = {**BASE, "m.x_new": {"survived": 1}}
    check("held: what the PR judged, and what the baseline lacks",
          mutmut_own.held(later, kept, {"m.x_f"}), {"m.x_f": {"killed": 3}, "m.x_new": {"survived": 1}})
    out = io.StringIO()
    with contextlib.redirect_stdout(out):
        rc = gate._gate(later, BASE, {"m.x_f"})
    said = ("mutmut-gate: 1 function(s) this pull request judged, held to the baseline; "
            "1 carry its state's verdicts for code it did not change, not compared")
    check("a survivor the PR never judged, killed on master since: not compared, and said",
          (rc, out.getvalue().splitlines()), (0, [said, "mutmut-gate: PASS"]))
    check("CONTROL: held for every function, it fails", _printed(later, BASE)[0], 1)
    check("  and so does the PR when it judged it", gate._gate(later, BASE, {"m.x_g"}), 1)
    with tempfile.TemporaryDirectory() as td:
        mutants = Path(td)
        record = mutants / gate.JUDGED
        check("no record: none, nothing so far", (mutmut_own.read(record), mutmut_own.so_far(record)), (None, set()))
        for doc, want in (({"commit": "c"}, None), ({"commit": "c", "own": []}, set()),
                          (["not", "an", "object"], None),
                          ({"commit": "c", "own": ["a.x_f"]}, {"a.x_f"})):
            record.write_text(json.dumps(doc), encoding="utf-8")
            check(f"a record {doc}: {want}", mutmut_own.read(record), want)
        check("  and so far, the last one's", mutmut_own.so_far(record), {"a.x_f"})
        for bad in ("{ not json", json.dumps({"own": "a.x_f"}), json.dumps({"own": [1]})):
            record.write_text(bad, encoding="utf-8")
            try:
                mutmut_own.read(record)
                said = "nothing raised"
            except mutmut_own.RecordError as e:
                said = str(e).split(":")[0]
            check(f"an unreadable record ({bad[:16]}): a RecordError naming it", said,
                  f"cannot read {gate.JUDGED}" if bad.startswith("{ ")
                  else f"{gate.JUDGED}'s \"own\" is not a list of function names")
        record.write_text(json.dumps({"commit": "c"}), encoding="utf-8")
        try:
            mutmut_own.required(record)
            said = "nothing raised"
        except mutmut_own.RecordError as e:
            said = str(e)
        check("required, a record with no \"own\": a RecordError saying what writes it", said,
              f"--pr: {gate.JUDGED} holds no record of what this pull request judged "
              "(run --pr writes it on a tree with nothing uncommitted)")
    check("hold, not a PR's run: everything, nothing said", _held_said(later, BASE, None), (BASE, []))
    check("a mutant's function is the name before its LAST __mutmut_, as by_function splits it",
          (mutmut_own.functions(["m.x_a__mutmut_b__mutmut_3", "m.x_c__mutmut_1"]),
           set(gate.by_function("    m.x_a__mutmut_b__mutmut_3: killed\n"))),
          ({"m.x_a__mutmut_b", "m.x_c"}, {"m.x_a__mutmut_b"}))


def _help(*argv: str) -> str:
    """mutmut-gate's --help for ARGV, its whitespace folded as argparse wraps it."""
    out = io.StringIO()
    with contextlib.redirect_stdout(out):
        rc = gate.main([*argv, "--help"])
    return f"rc={rc} " + " ".join(out.getvalue().split())


def test_run_and_gate_take_pr() -> None:
    """main hands run its --pr, and each subcommand's help says what --pr does."""
    for argv, pr in ((["run", "--pr", "--max-children", "3"], True), (["run", "--max-children", "3"], False)):
        with mock.patch.object(gate, "run") as ran, mock.patch("pathlib.Path.cwd", return_value=Path("/r")):
            rc = gate.main(argv)
        check(f"{' '.join(argv)}: run gets the checkout, --full, the children and --pr={pr}",
              (rc, ran.call_args_list), (0, [mock.call(Path("/r"), False, 3, pr)]))
    run_help, gate_help = _help("run"), _help("gate")
    check_true("run --help says what --pr does",
               run_help.startswith("rc=0 ")
               and "--pr a pull request's run: add what it judges to the record gate --pr holds" in run_help)
    check_true("gate --help says what --pr does",
               gate_help.startswith("rc=0 ")
               and "--pr hold only the functions the pull request's runs judged (run --pr)" in gate_help)


if __name__ == "__main__":
    sys.exit(run(globals(), "mutmut gate"))
