#!/usr/bin/env python3
"""
JP_TOOLS/tests/test_mutmut_config.py
pyproject.toml's [tool.mutmut] lists stay true to the checkout.

    - source_paths is every package (a top-level directory with __init__.py)
      and every top-level script, so a new one is mutated without anyone
      remembering to add it. tools/ is lab code and tests/ is the tests.
    - also_copy names every other tracked top-level file and directory, so a
      suite that reads one finds it in mutmut's copy (mutants/) too.
    - Nothing is listed that is not tracked. The installed node_modules/ and
      vendor/ are linked into mutants/ by the run, not copied (see
      pyproject.toml).
    - Under pytest each test starts in an empty directory of its own
      (conftest.py), so a mutant writing to a relative path cannot litter
      mutmut's copy. The control runs the same test without conftest.

The tracked files come from `git ls-files`, checked first against names this
file knows are there, so the comparison cannot pass by listing nothing.

    python3 tests/test_mutmut_config.py
"""

import os
import subprocess
import sys
import tempfile
import tomllib
from pathlib import Path

from _harness import ROOT, check, check_true, run

NOT_MUTATED = {"tests", "tools"}
# Scripts their suites must run as processes (git hooks, child processes):
# mutmut cannot see such a test, so these stay on tests/mutants specs.
ON_SPECS = {"install-hooks.py", "fix-dashes.py", "scratch-refs.py", "mutate-on-target.py", "mutate-spec.py"}
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
    check("source_paths is every package and top-level script but the ones on specs",
          sorted(source), sorted((packages | scripts) - ON_SPECS))
    specs = " ".join(p.read_text(encoding="utf-8") for p in (ROOT / "tests" / "mutants").glob("*.json"))
    check("each script left to specs has a spec aimed at it",
          sorted(s for s in ON_SPECS if f"TREE {s} " not in specs), [])
    others = {t for t in top if t not in source and t.rstrip("/") not in NOT_MUTATED
              and t not in COPIED_BY_MUTMUT}
    copied = set(table.get("also_copy", []))
    check("also_copy names every other tracked top-level entry",
          sorted(others - copied), [])
    check("also_copy names nothing that is not there",
          sorted(copied - top), [])


_STRAY_WRITER = """\
from pathlib import Path


def test_writes_relative() -> None:
    Path("stray.txt").write_text("a mutant's output path", encoding="utf-8")
"""


def _pytest_stray(launch: Path, with_conftest: bool) -> tuple[int, bool]:
    """Run pytest from LAUNCH on a test that writes stray.txt to its working
    directory; (pytest's exit code, whether stray.txt landed in LAUNCH)."""
    (launch / "test_stray.py").write_text(_STRAY_WRITER, encoding="utf-8")
    plugin = ["-p", "conftest"] if with_conftest else []
    env = dict(os.environ, PYTHONPATH=str(ROOT / "tests"))
    p = subprocess.run([sys.executable, "-m", "pytest", "-q", "-p", "no:cacheprovider",
                        *plugin, "test_stray.py"],
                       cwd=launch, env=env, capture_output=True, text=True, check=False)
    return p.returncode, (launch / "stray.txt").exists()


def test_each_test_runs_in_its_own_directory() -> None:
    with tempfile.TemporaryDirectory() as bare, tempfile.TemporaryDirectory() as ours:
        check("CONTROL: without conftest, a relative write lands where pytest started",
              _pytest_stray(Path(bare), with_conftest=False), (0, True))
        check("with conftest it lands in the test's own directory, not there",
              _pytest_stray(Path(ours), with_conftest=True), (0, False))


if __name__ == "__main__":
    sys.exit(run(globals(), "mutmut config"))
