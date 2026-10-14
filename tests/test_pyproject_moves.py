#!/usr/bin/env python3
"""
JP_TOOLS/tests/test_pyproject_moves.py
jpcheck/pyproject_moves.py: whether a pyproject.toml change can move what
mutmut's tests judge.

WHAT IS CHECKED
    pytest's tables, mutmut's table (but source_paths) and [build-system]
    move the tests; a version, a description, dependencies and source_paths
    do not. No pyproject.toml before, or one that does not parse, moves them.

    python3 tests/test_pyproject_moves.py
"""

import sys

from _harness import check, run
from jpcheck import pyproject_moves as pm

BASE = """\
[project]
name = "x"
version = "1"
dependencies = ["a"]

[build-system]
requires = ["setuptools"]

[tool.pytest.ini_options]
testpaths = ["tests"]

[tool.mutmut]
source_paths = ["a/"]
timeout_multiplier = 5.0

[tool.ruff]
line-length = 100
"""


def test_what_moves_the_tests() -> None:
    check("the key mutmut's table keeps that moves nothing", pm.UNMOVING, "source_paths")
    check("the parts the tests run under", pm.test_config(BASE),
          {"pytest": {"pytest": {"ini_options": {"testpaths": ["tests"]}}},
           "mutmut": {"timeout_multiplier": 5.0}, "build-system": {"requires": ["setuptools"]}})
    check("none of them: empty, and no build-system", pm.test_config('[project]\nname = "x"\n'),
          {"pytest": {}, "mutmut": {}, "build-system": None})
    unmoving = {
        "a version": BASE.replace('version = "1"', 'version = "2"'),
        "dependencies": BASE.replace('["a"]', '["a", "b"]'),
        "a source path": BASE.replace('["a/"]', '["a/", "b.py"]'),
        "another tool's table": BASE.replace("100", "120"),
    }
    check("these move no test", {what: pm.moves_tests(BASE, new) for what, new in unmoving.items()},
          dict.fromkeys(unmoving, False))
    moving = {
        "pytest's table": BASE.replace('["tests"]', '["tests", "more"]'),
        "a pytest table of another name": BASE + "\n[tool.pytest-other]\nx = 1\n",
        "mutmut's timeout": BASE.replace("5.0", "6.0"),
        "a new mutmut key": BASE.replace("timeout_multiplier", 'also_copy = ["d/"]\ntimeout_multiplier'),
        "the build system": BASE.replace('["setuptools"]', '["hatchling"]'),
        "none before": None,
        "one that does not parse": "[tool.mutmut\n",
    }
    check("these move the tests", {what: pm.moves_tests(BASE if what != "none before" else None,
                                                        new if new is not None else BASE)
                                   for what, new in moving.items()},
          dict.fromkeys(moving, True))
    check("  an old one that does not parse moves them too", pm.moves_tests("[tool.mutmut\n", BASE), True)


if __name__ == "__main__":
    sys.exit(run(globals(), "pyproject moves"))
