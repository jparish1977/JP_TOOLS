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

from _harness import check_true, run
from jpcheck.common import _plain_env, strip_ansi
from jpcheck.python_tools import _MYPY_LINE


def test_strip_ansi() -> None:
    coloured = 'spool-audit.py:659: \x1b[1m\x1b[31merror:\x1b(B\x1b[m Item "None"'
    plain = strip_ansi(coloured)
    check_true("escapes removed", "\x1b" not in plain)
    check_true("text preserved", plain.startswith("spool-audit.py:659: error:"))
    check_true("the parser can now match it", _MYPY_LINE.match(plain) is not None)
    check_true("and could not before", _MYPY_LINE.match(coloured) is None)


def test_plain_env() -> None:
    os.environ["FORCE_COLOR"] = "3"
    try:
        env = _plain_env()
        check_true("FORCE_COLOR dropped", "FORCE_COLOR" not in env)
        check_true("NO_COLOR set", env.get("NO_COLOR") == "1")
        check_true("PATH preserved", "PATH" in env)
    finally:
        os.environ.pop("FORCE_COLOR", None)


if __name__ == "__main__":
    sys.exit(run(globals(), "check.py colour"))
