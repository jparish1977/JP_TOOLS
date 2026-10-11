#!/usr/bin/env python3
"""
JP_TOOLS/tests/test_mutmut_config.py
pyproject.toml's [tool.mutmut] lists stay true to the checkout.

    - source_paths is every package (a top-level directory with __init__.py)
      and every top-level script, so a new one is mutated without anyone
      remembering to add it. tools/ is lab code and tests/ is the tests.
    - also_copy names every other tracked top-level file and directory, so a
      suite that reads one finds it in mutmut's copy (mutants/) too.
    - Nothing is listed that does not exist, apart from vendor/, which is
      installed rather than tracked. node_modules/ is linked, not copied (see
      pyproject.toml).

The tracked files come from `git ls-files`, checked first against names this
file knows are there, so the comparison cannot pass by listing nothing.

    python3 tests/test_mutmut_config.py
"""

import subprocess
import sys
import tomllib

from _harness import ROOT, check, check_true, run

NOT_MUTATED = {"tests", "tools"}
INSTALLED = {"vendor/"}
# mutmut copies these itself, whatever also_copy says.
COPIED_BY_MUTMUT = {"pyproject.toml", "setup.cfg"}


def mutmut_table() -> dict[str, list[str]]:
    with (ROOT / "pyproject.toml").open("rb") as fh:
        table = tomllib.load(fh).get("tool", {}).get("mutmut", {})
    return {k: list(v) for k, v in table.items() if isinstance(v, list)}


def tracked() -> list[str]:
    out = subprocess.run(["git", "-C", str(ROOT), "ls-files"], capture_output=True,
                         text=True, check=True).stdout
    return out.splitlines()


def test_the_lists_are_the_checkout() -> None:
    files = tracked()
    check_true("CONTROL: git ls-files sees check.py and jpcheck/__init__.py",
               "check.py" in files and "jpcheck/__init__.py" in files)
    packages = {f.split("/")[0] + "/" for f in files
                if f.count("/") == 1 and f.endswith("/__init__.py")}
    scripts = {f for f in files if "/" not in f and f.endswith(".py")}
    top = {f.split("/")[0] + ("/" if "/" in f else "") for f in files}
    table = mutmut_table()
    source = set(table.get("source_paths", []))
    check("source_paths is every package and top-level script",
          sorted(source), sorted(packages | scripts))
    others = {t for t in top if t not in source and t.rstrip("/") not in NOT_MUTATED
              and t not in COPIED_BY_MUTMUT}
    copied = set(table.get("also_copy", []))
    check("also_copy names every other tracked top-level entry",
          sorted(others - copied), [])
    check("also_copy names nothing that is not there",
          sorted(copied - top - INSTALLED), [])


if __name__ == "__main__":
    sys.exit(run(globals(), "mutmut config"))
