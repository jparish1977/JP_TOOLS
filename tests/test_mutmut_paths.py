#!/usr/bin/env python3
"""
JP_TOOLS/tests/test_mutmut_paths.py
jpcheck/mutmut_paths.py: which suites read a changed shared file.

WHAT IS CHECKED
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

from _harness import check, run
from jpcheck import mutmut_paths as mp

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
    check("every form of import counts", {p: mp.readers(p, TESTS) for p in (
        "tests/listed.py", "tests/packaged.py", "tests/loaded.py", "tests/wrapped.py")},
          {"tests/listed.py": {"tests/test_f.py"}, "tests/packaged.py": {"tests/test_g.py"},
           "tests/loaded.py": {"tests/test_h.py"}, "tests/wrapped.py": {"tests/test_i.py"}})
    check("named in a file that imports anything: counted, a re-trace too many at most",
          mp.readers("tests/commented.py", TESTS), {"tests/test_j.py"})
    check("named in a file that imports nothing: not counted",
          mp.readers("tests/unread.py", TESTS), None)
    check("each file's readers", {p: mp.readers(p, TESTS) for p in (
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
    check("a name read as a pattern only literally", mp.readers("tests/a.c.py", {"tests/test_x.py": b"import abc\n"}),
          None)
    check("a conftest.py moves every test, even one a suite imports",
          mp.readers("tests/conftest.py", {"tests/test_x.py": b"from conftest import FIX\n"}), None)


if __name__ == "__main__":
    sys.exit(run(globals(), "mutmut paths"))
