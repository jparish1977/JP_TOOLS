#!/usr/bin/env python3
"""
JP_TOOLS/tests/test_mutmut_paths.py
jpcheck/mutmut_paths.py: a checkout's test layout, and which suites read a
changed shared file.

WHAT IS CHECKED
    The layout is read from pytest's configuration as pytest reads it:
    pytest.ini first, then .pytest.ini, pyproject.toml, tox.ini, setup.cfg, a
    file without pytest's section passed over; python_files split into words
    (pytest's default without one); as test directories each testpaths entry
    (a file's own directory) and every directory holding a tracked suite.
    Under dt's layout a helper beside its suites is shared and a suite is
    not, and JP_TOOLS' tests/ means nothing; a suite at the root makes every
    file shared but a suite.

    A Python helper is read by the files that import it, in any form (from
    it, it whole, beside others, from a package, through importlib, in
    parentheses across lines), and by one that imports anything and names it
    anywhere (an over-count, a re-trace); not by one that names it and imports
    nothing, or imports a longer name; a data file by those that name it. A
    conftest.py, a configuration file, a file nothing reads, or one a helper
    reads: any test (None).

    python3 tests/test_mutmut_paths.py
"""

import sys
import tempfile
from pathlib import Path

from _harness import ROOT, check, run
from jpcheck import mutmut_paths as mp

DEFAULT = ("test_*.py", "*_test.py")
JPT = mp.SuiteLayout(DEFAULT, frozenset({"tests"}))


def _layout(files: dict[str, str]) -> mp.SuiteLayout:
    """suite_layout of a directory holding FILES, every one of them tracked."""
    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        for name, body in files.items():
            (root / name).parent.mkdir(parents=True, exist_ok=True)
            (root / name).write_text(body, encoding="utf-8")
        return mp.suite_layout(root, sorted(files))


def test_the_suite_layout_is_read_from_pytests_configuration() -> None:
    check("JP_TOOLS' own: testpaths tests, pytest's default file names",
          mp.suite_layout(ROOT, ["tests/test_a.py", "check.py"]), JPT)
    dt = {"pyproject.toml": "[tool.pytest.ini_options]\ntestpaths = [\"dtchecks/a_test.py\"]\n"
                            "python_files = [\"*_test.py\"]\n",
          "dtchecks/a_test.py": "", "dtchecks/helper.py": "", "lab/b_test.py": ""}
    check("a repo's own: python_files, a testpaths FILE's directory, and every suite's",
          _layout(dt), mp.SuiteLayout(("*_test.py",), frozenset({"dtchecks", "lab"})))
    check("no configuration: pytest's default names, the suites' directories",
          _layout({"x/test_a.py": "", "y/b.py": ""}), mp.SuiteLayout(DEFAULT, frozenset({"x"})))
    check("a pyproject.toml with no pytest table is passed over, as pytest does",
          _layout({"pyproject.toml": "[project]\nname = 'x'\n", "setup.cfg": "[tool:pytest]\npython_files = c_*.py\n"}),
          mp.SuiteLayout(("c_*.py",), frozenset()))
    for name, section in (("pytest.ini", "pytest"), (".pytest.ini", "pytest"), ("tox.ini", "pytest"),
                          ("setup.cfg", "tool:pytest")):
        check(f"{name}: [{section}] is read, words split",
              _layout({name: f"[{section}]\npython_files = a_*.py b_*.py\ntestpaths = t\n", "t/x.py": ""}),
              mp.SuiteLayout(("a_*.py", "b_*.py"), frozenset({"t"})))
        check(f"{name} with no [{section}]: passed over",
              _layout({name: "[other]\nx = 1\n"}), mp.SuiteLayout(DEFAULT, frozenset()))
    check("pytest.ini first, before pyproject.toml",
          _layout({"pytest.ini": "[pytest]\npython_files = p_*.py\n",
                   "pyproject.toml": "[tool.pytest.ini_options]\npython_files = [\"q_*.py\"]\n"}).python_files,
          ("p_*.py",))
    check("a pyproject.toml's string value is split into words, as an ini's",
          _layout({"pyproject.toml": "[tool.pytest.ini_options]\npython_files = \"r_*.py s_*.py\"\n"}).python_files,
          ("r_*.py", "s_*.py"))
    check("every file pytest reads its configuration from counts as configuration", mp.CONFIG_FILES,
          ("pytest.ini", ".pytest.ini", "pyproject.toml", "tox.ini", "setup.cfg"))


def test_a_repos_own_helpers_are_shared() -> None:
    """Under dt's layout a helper beside its suites is shared and a suite is
    not; JP_TOOLS' tests/ means nothing there."""
    layout = mp.SuiteLayout(("*_test.py",), frozenset({"dtchecks"}))
    check("dt's layout",
          {p: mp.shared_by_tests(p, layout) for p in (
              "dtchecks/gate_verdict.py", "dtchecks/data/case.json", "dtchecks/a_test.py",
              "dtchecks/conftest.py", "tests/_harness.py", "dt/x.py", "dtchecksx/y.py")},
          {"dtchecks/gate_verdict.py": True, "dtchecks/data/case.json": True, "dtchecks/a_test.py": False,
           "dtchecks/conftest.py": True, "tests/_harness.py": False, "dt/x.py": False,
           "dtchecksx/y.py": False})
    check("  its test files: the Python files in its test directories",
          mp.files_in_test_dirs(["dtchecks/a_test.py", "dtchecks/helper.py", "dtchecks/data/case.json", "dt/x.py",
                         "dtchecksx/y.py"], layout),
          ["dtchecks/a_test.py", "dtchecks/helper.py"])
    check("  and a suite there is one, a helper not",
          (mp.is_suite("dtchecks/a_test.py", layout), mp.is_suite("dtchecks/helper.py", layout),
           mp.is_suite("tests/test_x.py", layout)), (True, False, False))
    root_suite = mp.SuiteLayout(("test_*.py",), frozenset({""}))
    check("a suite at the root: every file shared but a suite",
          (mp.shared_by_tests("a/b.py", root_suite), mp.shared_by_tests("test_a.py", root_suite)), (True, False))

TESTS = {
    "tests/test_a.py": b"from support import repo\nF = FIX / 'help.txt'\n",
    "tests/test_b.py": b"import support\n",
    "tests/test_c.py": b"import support_more\nfrom support_more import x\n",
    "tests/sub/test_d.py": b"    from other import y\n",
    "tests/test_f.py": b"import os, listed\n",
    "tests/test_g.py": b"from tests import packaged\n",
    "tests/test_h.py": b"m = importlib.import_module('loaded')\n",
    "tests/test_i.py": b"from tests import (\n    wrapped,\n)\n",
    "tests/test_j.py": b"# commented is named here\nimport os\n",
    "tests/test_k.py": b"# unread is named here, alone\n",
    "tests/_helper.py": b"import chained\nG = FIX / 'shared.txt'\n",
    "tests/test_e.py": b"from _helper import G\n\xff not UTF-8\n",
}


def test_which_suites_read_a_changed_file() -> None:
    check("every form of import counts", {p: mp.readers(p, TESTS, JPT) for p in (
        "tests/listed.py", "tests/packaged.py", "tests/loaded.py", "tests/wrapped.py")},
          {"tests/listed.py": {"tests/test_f.py"}, "tests/packaged.py": {"tests/test_g.py"},
           "tests/loaded.py": {"tests/test_h.py"}, "tests/wrapped.py": {"tests/test_i.py"}})
    check("named in a file that imports anything: counted, a re-trace too many at most",
          mp.readers("tests/commented.py", TESTS, JPT), {"tests/test_j.py"})
    check("named in a file that imports nothing: not counted",
          mp.readers("tests/unread.py", TESTS, JPT), None)
    check("each file's readers", {p: mp.readers(p, TESTS, JPT) for p in (
        "tests/support.py", "tests/other.py", "tests/fixtures/help.txt", "tests/chained.py",
        "tests/fixtures/shared.txt", "tests/fixtures/orphan.txt", "tests/nobody.py", "tests/conftest.py",
        "tests/sub/conftest.py", "pyproject.toml", "pytest.ini", "setup.cfg", "tox.ini", "tests/_helper.py")},
          {"tests/support.py": {"tests/test_a.py", "tests/test_b.py"},
           "tests/other.py": {"tests/sub/test_d.py"},
           "tests/fixtures/help.txt": {"tests/test_a.py"},
           "tests/chained.py": None,
           "tests/fixtures/shared.txt": None,
           "tests/fixtures/orphan.txt": None,
           "tests/nobody.py": None,
           "tests/conftest.py": None,
           "tests/sub/conftest.py": None,
           "pyproject.toml": None, "pytest.ini": None, "setup.cfg": None, "tox.ini": None,
           "tests/_helper.py": {"tests/test_e.py"}})
    check("a name read as a pattern only literally",
          mp.readers("tests/a.c.py", {"tests/test_x.py": b"import abc\n"}, JPT), None)
    check("a conftest.py moves every test, even one a suite imports",
          mp.readers("tests/conftest.py", {"tests/test_x.py": b"from conftest import FIX\n"}, JPT), None)


if __name__ == "__main__":
    sys.exit(run(globals(), "mutmut paths"))
