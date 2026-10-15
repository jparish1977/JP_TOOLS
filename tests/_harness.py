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
(tests/test_harness.py holds that), so no suite runs git with them. And it
points GIT_CONFIG_GLOBAL at the null device, so the box's global git config
(a global core.hooksPath above all) is never read, as in CI.
"""

import difflib
import importlib.util
import os
import shutil
import subprocess
import sys
import traceback
from collections.abc import Mapping
from pathlib import Path
from types import ModuleType
from typing import Any

ROOT = Path(__file__).resolve().parent.parent

# The checkout whose files a whole-repo self-check reads as DATA ("this repo
# runs no lab file", "check.py passes its own noqa rule"). It is ROOT, except
# under mutmut: there ROOT is its copy, CHECKOUT/mutants, whose sources hold
# every mutant of every function beside the original, about 18 times the
# text. Code still runs from ROOT, so the mutant under test is the one judged;
# only the files it reads are the checkout's own, the files the check is about.
def checkout_of(root: Path, environ: Mapping[str, str]) -> Path:
    """ROOT's parent when ROOT is mutmut's mutants/ copy and mutmut is running
    (it sets MUTANT_UNDER_TEST in every pass, empty or not), else ROOT."""
    if "MUTANT_UNDER_TEST" in environ and root.name == "mutants":
        return root.parent
    return root


CHECKOUT = checkout_of(ROOT, os.environ)


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

# The box's own global git config is not read: a suite runs as it does in CI,
# which has none. Above all, a global core.hooksPath (install-global-hooks.py)
# would put the box's installed hooks in front of every commit a suite makes,
# so a suite testing this checkout's hooks would be answered by the installed
# ones. A suite that tests the global config gives its git its own HOME.
os.environ["GIT_CONFIG_GLOBAL"] = os.devnull

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


# Past this many characters of repr, a mismatch is shown as what differs, not
# as both values whole: two copies of a long output on one line hide the line
# that changed.
LONG = 200


def _text_lines(value: object) -> list[str] | None:
    """VALUE's lines, each as its repr so a trailing newline or a \\r shows,
    when it is text or a list of text; else None."""
    if isinstance(value, str):
        return [repr(line) for line in value.splitlines(keepends=True)]
    if isinstance(value, list) and all(isinstance(v, str) for v in value):
        return [repr(v) for v in value]
    return None


def mismatch(expected: object, actual: object) -> str:
    """What a failed check says after its label. Short values: both, whole.
    Long text or lists of text: a unified diff, - expected + got, one line of
    context. Long tuples of the same length: each differing item, by index,
    in the same way."""
    if len(repr(expected)) + len(repr(actual)) <= LONG:
        return f"expected {expected!r}, got {actual!r}"
    want, got = _text_lines(expected), _text_lines(actual)
    if want is not None and got is not None:
        diff = list(difflib.unified_diff(want, got, "expected", "got", lineterm="", n=1))[2:]
        if diff:  # none when only the type differs (a str against a list of its lines)
            return "differs (- expected, + got):\n" + "\n".join(diff)
    if isinstance(expected, tuple) and isinstance(actual, tuple) and len(expected) == len(actual):
        return "\n".join(f"[{i}] {mismatch(e, a)}" for i, (e, a) in enumerate(zip(expected, actual, strict=True))
                         if e != a)
    return f"expected {expected!r}, got {actual!r}"


def check(label: str, actual: object, expected: object) -> None:
    """Record a failure when ACTUAL is not EXPECTED, saying what differs."""
    if actual != expected:
        FAILURES.append(f"{label}: {mismatch(expected, actual)}")


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
