#!/usr/bin/env python3
"""
JP_TOOLS/tests/test_directives_run.py
run_directive_reason's whole report, in this process.

WHAT IS CHECKED
    The report for a shell file holding reasonless and reasoned directives,
    compared whole: tool, status, and each issue's every field, the message
    word for word. A file with only reasoned directives passes with no
    issues. A file that cannot be read is an error naming the exception. The
    file is read as UTF-8 whatever the locale (a NEL, U+0085, is a line break
    only when decoded as UTF-8, so the issue's line number shows the codec),
    and a byte that is not UTF-8 is replaced rather than raised.

    The locale case sets LC_CTYPE to C for the read and restores it. Under
    Python's UTF-8 mode (PYTHONUTF8=1, -X utf8) the locale names no codec, so
    there the case holds trivially.

    python3 tests/test_directives_run.py
"""

import locale
import sys
import tempfile
from collections.abc import Callable
from pathlib import Path

from _harness import check, run
from jpcheck.directives import run_directive_reason


def outcome(fn: Callable[[], object]) -> object:
    """FN's result, or the name of what it raised: a crash is a wrong answer."""
    try:
        return fn()
    except Exception as e:  # noqa: BLE001 -- reason: a crash is compared as a result, so its check names it
        return f"raised {type(e).__name__}"


SHELL_MESSAGE = ("'shellcheck disable' turns a finding off and states no reason in a form its "
                 "tool honours. Say why as a second comment: '# shellcheck disable=SC2086  # why', "
                 "or remove it")


def issue(path: str, line: int) -> dict[str, object]:
    return {"file": path, "line": line, "col": 0, "severity": "error",
            "rule": "directive-no-reason", "message": SHELL_MESSAGE,
            "fixable": False, "fixable_unsafe": False}


def test_the_whole_report() -> None:
    with tempfile.TemporaryDirectory() as td:
        bare = Path(td) / "bare.sh"
        bare.write_text("#!/bin/sh\n# shellcheck disable=SC2086\n"
                        "# shellcheck disable=SC2034  # set for a sourcing script\n"
                        "echo $1\n# shellcheck disable=SC2154\n", encoding="utf-8")
        check("report: each reasonless directive is one issue, every field exact",
              outcome(lambda: run_directive_reason(str(bare))),
              {"tool": "directive-reason", "status": "fail",
               "issues": [issue(str(bare), 2), issue(str(bare), 5)]})
        why = Path(td) / "why.sh"
        why.write_text("#!/bin/sh\n# shellcheck disable=SC2086  # split on purpose\necho $1\n",
                       encoding="utf-8")
        check("report: only reasoned directives pass with no issues",
              outcome(lambda: run_directive_reason(str(why))),
              {"tool": "directive-reason", "status": "pass", "issues": []})


def test_an_unreadable_file() -> None:
    with tempfile.TemporaryDirectory() as td:
        gone = str(Path(td) / "gone.sh")
        check("report: a file that cannot be read is an error naming the exception",
              outcome(lambda: run_directive_reason(gone)),
              {"tool": "directive-reason", "status": "error", "issues": [],
               "note": f"could not read {gone}: FileNotFoundError"})


def test_read_as_utf8_in_any_locale() -> None:
    with tempfile.TemporaryDirectory() as td:
        nel = Path(td) / "nel.sh"
        nel.write_bytes(b"#!/bin/sh\n# x\xc2\x85\n# shellcheck disable=SC2086\n")
        saved = locale.setlocale(locale.LC_CTYPE)
        try:
            locale.setlocale(locale.LC_CTYPE, "C")
            got = outcome(lambda: run_directive_reason(str(nel)))
        finally:
            locale.setlocale(locale.LC_CTYPE, saved)
        check("encoding: read as UTF-8 under the C locale, so a NEL breaks the line",
              got, {"tool": "directive-reason", "status": "fail", "issues": [issue(str(nel), 4)]})


def test_a_byte_that_is_not_utf8_is_replaced() -> None:
    with tempfile.TemporaryDirectory() as td:
        bad = Path(td) / "bad.sh"
        bad.write_bytes(b"#!/bin/sh\n# \xff\n# shellcheck disable=SC2086\n")
        check("errors: a byte that is not UTF-8 is replaced, and the file is still checked",
              outcome(lambda: run_directive_reason(str(bad))),
              {"tool": "directive-reason", "status": "fail", "issues": [issue(str(bad), 3)]})


if __name__ == "__main__":
    sys.exit(run(globals(), "directives-run"))
