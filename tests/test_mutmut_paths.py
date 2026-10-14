#!/usr/bin/env python3
"""
JP_TOOLS/tests/test_mutmut_paths.py
jpcheck/mutmut_paths.py: which suites read a changed shared file.

WHAT IS CHECKED
    A Python helper is read by the files that import it (from it, or it
    whole), not by one that only mentions it or imports a longer name; a data
    file by those that name it. A conftest.py, a configuration file, a file
    nothing reads, or one a helper reads: any test (None).

    python3 tests/test_mutmut_paths.py
"""

import sys

from _harness import check, run
from jpcheck import mutmut_paths as mp

TESTS = {
    "tests/test_a.py": b"from support import repo\nF = FIX / 'help.txt'\n",
    "tests/test_b.py": b"import support\n",
    "tests/test_c.py": b"# support is mentioned, not imported\nimport support_more\nfrom support_more import x\n",
    "tests/sub/test_d.py": b"    from other import y\n",
    "tests/_helper.py": b"import chained\nG = FIX / 'shared.txt'\n",
    "tests/test_e.py": b"from _helper import G\n\xff not UTF-8\n",
}


def test_which_suites_read_a_changed_file() -> None:
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
