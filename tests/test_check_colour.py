#!/usr/bin/env python
"""
JP_TOOLS/tests/test_check_colour.py
check.py must not be blinded by ambient colour forcing.

WHAT THIS PINS DOWN
    Tools honour FORCE_COLOR/CLICOLOR_FORCE even when their output is captured.
    With FORCE_COLOR set, mypy emits "\x1b[1m\x1b[31merror:", a line regex
    cannot match it, and a failing file reads as a pass: the gate defeated by
    an environment variable.

No pytest, no dependencies.

    python tests/test_check_colour.py
"""

import os
import sys

import jpcheck_loader

common = jpcheck_loader.load("common")
python_tools = jpcheck_loader.load("python_tools")

FAILURES: list[str] = []


def check_true(label: str, value: bool) -> None:
    if not value:
        FAILURES.append(f"{label}: expected True")


def test_strip_ansi() -> None:
    coloured = 'spool-audit.py:659: \x1b[1m\x1b[31merror:\x1b(B\x1b[m Item "None"'
    plain = common.strip_ansi(coloured)
    check_true("escapes removed", "\x1b" not in plain)
    check_true("text preserved", plain.startswith("spool-audit.py:659: error:"))
    check_true("the parser can now match it", python_tools._MYPY_LINE.match(plain) is not None)
    check_true("and could not before", python_tools._MYPY_LINE.match(coloured) is None)


def test_plain_env() -> None:
    os.environ["FORCE_COLOR"] = "3"
    try:
        env = common._plain_env()
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
