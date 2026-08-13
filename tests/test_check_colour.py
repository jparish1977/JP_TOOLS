#!/usr/bin/env python
"""
JP_TOOLS/tests/test_check_colour.py
check.py must not be blinded by ambient colour forcing.

THE BUG THIS PINS DOWN
    Tools honour FORCE_COLOR/CLICOLOR_FORCE even when their output is captured.
    Measured 2026-08-12 with FORCE_COLOR=3 in the environment: mypy emitted
    "\x1b[1m\x1b[31merror:", check.py's line regex could not match it, and
    check.py reported 0 issues on a file mypy was failing with 2. CI has no
    FORCE_COLOR, so the build went red while the local gate stayed green -- the
    gate was defeated by an environment variable.

    Same class as the parser bug check.py's own header documents: a failing
    file reported as a pass.

No pytest, no dependencies.

    python tests/test_check_colour.py
"""

import importlib.util
import os
import pathlib
import sys

MODULE_PATH = pathlib.Path(__file__).resolve().parent.parent / "check.py"
sys.dont_write_bytecode = True
_cache = pathlib.Path(importlib.util.cache_from_source(str(MODULE_PATH)))
if _cache.exists():
    _cache.unlink()
_spec = importlib.util.spec_from_file_location("check_mod", MODULE_PATH)
assert _spec is not None and _spec.loader is not None
check_mod = importlib.util.module_from_spec(_spec)
sys.modules["check_mod"] = check_mod
_spec.loader.exec_module(check_mod)

FAILURES: list[str] = []


def check_true(label: str, value: bool) -> None:
    if not value:
        FAILURES.append(f"{label}: expected True")


def test_strip_ansi() -> None:
    coloured = 'spool-audit.py:659: \x1b[1m\x1b[31merror:\x1b(B\x1b[m Item "None"'
    plain = check_mod.strip_ansi(coloured)
    check_true("escapes removed", "\x1b" not in plain)
    check_true("text preserved", plain.startswith("spool-audit.py:659: error:"))
    check_true("the parser can now match it", check_mod._MYPY_LINE.match(plain) is not None)
    check_true("and could not before", check_mod._MYPY_LINE.match(coloured) is None)


def test_plain_env() -> None:
    os.environ["FORCE_COLOR"] = "3"
    try:
        env = check_mod._plain_env()
        check_true("FORCE_COLOR dropped", "FORCE_COLOR" not in env)
        check_true("NO_COLOR set", env.get("NO_COLOR") == "1")
        check_true("PATH preserved", "PATH" in env)
    finally:
        os.environ.pop("FORCE_COLOR", None)


def main() -> int:
    for name, fn in sorted(globals().items()):
        if name.startswith("test_") and callable(fn):
            fn()
    if FAILURES:
        print(f"FAILED ({len(FAILURES)})")
        for f in FAILURES:
            print(f"  {f}")
        return 1
    print("all check.py colour tests passed")
    return 0


if __name__ == "__main__":
    sys.exit(main())
