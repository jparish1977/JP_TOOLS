"""What the no-pytest test files share: the checkout on sys.path, and the check helpers.

Tests run as `python tests/test_x.py`, which puts tests/ on sys.path and not
the checkout root. Importing this first makes the checkout's packages
importable:

    from _harness import ROOT, check, run
    from jpcheck.suppressions import run_noqa

The ruff config forces it to the top of its import section. Bytecode is off,
so a mutation run that rewrites a module in place is never answered by a
cached copy of the original.
"""

import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent.parent

sys.dont_write_bytecode = True
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

FAILURES: list[str] = []


def check(label: str, actual: object, expected: object) -> None:
    """Record a failure when ACTUAL is not EXPECTED."""
    if actual != expected:
        FAILURES.append(f"{label}: expected {expected!r}, got {actual!r}")


def check_true(label: str, value: bool) -> None:
    if not value:
        FAILURES.append(f"{label}: expected True")


def run(tests: dict[str, Any], label: str) -> int:
    """Run every test_* in TESTS (a module's globals), then report FAILURES."""
    for name, fn in sorted(tests.items()):
        if name.startswith("test_") and callable(fn):
            fn()
    if FAILURES:
        print(f"FAILED ({len(FAILURES)})")
        for f in FAILURES:
            # "FAIL <label>: ...": a mutation runner counts a mutant as caught
            # only on a line starting FAIL that carries the check's label.
            print(f"FAIL {f}")
        return 1
    print(f"all {label} tests passed")
    return 0
