"""What the no-pytest test files share: the checkout on sys.path, and the check helpers.

Tests run as `python tests/test_x.py`, which puts tests/ on sys.path and not
the checkout root. Importing this first makes the checkout's packages
importable:

    from _harness import ROOT, check, run
    from jpcheck.suppressions import run_noqa

A script whose file name is not a module name is loaded with load_script().
The ruff config forces it to the top of its import section. Bytecode is off,
so a mutation run that rewrites a module in place is never answered by a
cached copy of the original.

Importing it also takes git's repository variables out of the environment
(GIT_DIR, GIT_WORK_TREE, GIT_INDEX_FILE and the rest: the list is git's own,
`git rev-parse --local-env-vars`). Under `git bisect run` or a git hook they
name the REAL repository, and a suite's `git init` or `git commit` in a
scratch directory would act on it instead. Every suite imports this module
(tests/test_harness.py holds that), so no suite runs git with them.
"""

import importlib.util
import os
import shutil
import subprocess
import sys
import traceback
from pathlib import Path
from types import ModuleType
from typing import Any

ROOT = Path(__file__).resolve().parent.parent


def clear_git_repo_env() -> list[str]:
    """Remove git's repository variables from os.environ; return those that
    were set. Without git there is nothing a suite could run them with."""
    git = shutil.which("git")
    if git is None:
        return []
    names = subprocess.run([git, "rev-parse", "--local-env-vars"], capture_output=True,
                           text=True, check=False).stdout.split()
    return [n for n in names if os.environ.pop(n, None) is not None]


CLEARED_GIT_ENV = clear_git_repo_env()

# What a copy of the whole checkout leaves out: git data, installed packages,
# and what tools write into the checkout while other suites run beside the
# copy. Suites run in parallel under coverage, and a data file another suite
# writes and then combines away vanishes mid-copy, failing copytree.
CHECKOUT_COPY_IGNORE = shutil.ignore_patterns(
    ".git", "vendor", "node_modules", "__pycache__",
    ".coverage", ".coverage.*", ".mypy_cache", ".ruff_cache")

sys.dont_write_bytecode = True
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

FAILURES: list[str] = []


def load_script(filename: str) -> ModuleType:
    """Import a checkout script whose name is not a module name (chd-hunkmap.py).

    Registered in sys.modules before it runs, which a dataclass defined in it
    needs in order to resolve its own annotations. Under the file's own stem,
    hyphen and all: mutmut names a script's mutants after its file, and a
    test that loads it under another name runs none of them.
    """
    name = filename.removesuffix(".py")
    spec = importlib.util.spec_from_file_location(name, ROOT / filename)
    if spec is None or spec.loader is None:
        raise ImportError(f"cannot load {filename}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


def check(label: str, actual: object, expected: object) -> None:
    """Record a failure when ACTUAL is not EXPECTED."""
    if actual != expected:
        FAILURES.append(f"{label}: expected {expected!r}, got {actual!r}")


def check_true(label: str, value: bool) -> None:
    if not value:
        FAILURES.append(f"{label}: expected True")


def run(tests: dict[str, Any], label: str) -> int:
    """Run every test_* in TESTS (a module's globals), then report FAILURES.

    A test that raises is one failure under its own name, with the traceback on
    stderr, and the tests after it still run: otherwise one crash hides every
    check sorted after it, and a mutant aimed at one of those reads as caught
    by the crash instead."""
    for name, fn in sorted(tests.items()):
        if name.startswith("test_") and callable(fn):
            try:
                fn()
            except Exception as e:  # noqa: BLE001 -- reason: a crash is that test's failure, the run goes on
                traceback.print_exc()
                FAILURES.append(f"{name} raised {type(e).__name__}: {e}")
    if FAILURES:
        print(f"FAILED ({len(FAILURES)})")
        for f in FAILURES:
            # "FAIL <label>: ...": a mutation runner counts a mutant as caught
            # only on a line starting FAIL that carries the check's label.
            print(f"FAIL {f}")
        return 1
    print(f"all {label} tests passed")
    return 0
