#!/usr/bin/env python3
"""
JP_TOOLS/tests/test_files_ignores.py
.jp-tools-ignore read, matched and applied, in this process.

WHAT IS CHECKED
    load_ignores: the (glob, reason) list exact, comments and blank lines
    skipped wherever they fall, a line split at its FIRST '#', and a line
    with no reason refused with the whole message, its line number counted
    from 1. The file is read as UTF-8 under the C locale too (a
    non-ASCII reason would not decode as ASCII). With no file, [].

    ignored_by: the first entry whose glob matches the path relative to the
    root; None for a path no entry matches.

    _drop_ignored: each language's list keeps exactly the files no entry
    matches, and each dropped file is returned with its relative path, glob
    and reason.

    The locale case sets LC_CTYPE to C for the read and restores it. Under
    Python's UTF-8 mode (PYTHONUTF8=1, -X utf8) the locale names no codec, so
    there the case holds trivially.

    python3 tests/test_files_ignores.py
"""

import locale
import sys
import tempfile
from collections.abc import Callable
from pathlib import Path

from _harness import check, run
from jpcheck.files import _drop_ignored, ignored_by, load_ignores


def outcome(fn: Callable[[], object]) -> object:
    """FN's result, or what it raised with its message: a crash is a wrong answer."""
    try:
        return fn()
    except Exception as e:  # noqa: BLE001 -- reason: a crash is compared as a result, so its check names it
        return f"raised {type(e).__name__}: {e}"


def loaded(text: str) -> object:
    """load_ignores' answer for a .jp-tools-ignore holding TEXT."""
    with tempfile.TemporaryDirectory() as td:
        (Path(td) / ".jp-tools-ignore").write_text(text, encoding="utf-8")
        return outcome(lambda: load_ignores(Path(td)))


def refusal(text: str, n: int, line: str) -> str:
    """What load_ignores raises for TEXT, whose line N reads LINE (stripped)."""
    with tempfile.TemporaryDirectory() as td:
        path = Path(td) / ".jp-tools-ignore"
        path.write_text(text, encoding="utf-8")
        got = outcome(lambda: load_ignores(Path(td)))
        want = (f"raised _IgnoreError: {path}:{n}: '{line}' needs a glob and a reason after '#', "
                "so a vendor copy and a file nobody wanted to fix cannot look alike")
        return "as expected" if got == want else f"{got!r} != {want!r}"


def test_entries_are_read() -> None:
    check("load: comments and blank lines are skipped wherever they fall, every entry kept",
          loaded("# vendor content\n\noob/*  # vendor pages\n  # an indented comment\n"
                 "\n   \nlib/*.js # minified upstream\n"),
          [("oob/*", "vendor pages"), ("lib/*.js", "minified upstream")])
    check("load: a line splits at its FIRST '#', the rest is the reason",
          loaded("a/* # first # second\n"), [("a/*", "first # second")])
    check("load: no .jp-tools-ignore is no entries",
          outcome(lambda: load_ignores(Path(tempfile.gettempdir()) / "no-such-dir-jp-tools")), [])


def test_a_bad_line_is_refused() -> None:
    check("refuse: a glob with no reason, the line counted from 1",
          refusal("# comment\n\noob/*  # ok\nsrc/*\n", 4, "src/*"), "as expected")
    check("refuse: the first line is line 1",
          refusal("src/*\n", 1, "src/*"), "as expected")
    check("refuse: a '#' with nothing after it, past comments that are only '#'",
          refusal("ok/* # fine\n  # \n#only\n   #  \n x# \n", 5, "x#"), "as expected")


def test_read_as_utf8_in_any_locale() -> None:
    with tempfile.TemporaryDirectory() as td:
        (Path(td) / ".jp-tools-ignore").write_bytes("oob/* # café menus\n".encode())
        saved = locale.setlocale(locale.LC_CTYPE)
        try:
            locale.setlocale(locale.LC_CTYPE, "C")
            got = outcome(lambda: load_ignores(Path(td)))
        finally:
            locale.setlocale(locale.LC_CTYPE, saved)
        check("load: read as UTF-8 under the C locale", got, [("oob/*", "café menus")])


IGNORES = [("src/*.py", "generated"), ("oob/*", "vendor pages"), ("oob/a.py", "never reached")]


def test_ignored_by() -> None:
    root = Path(tempfile.gettempdir()).resolve() / "jp-tools-ignored-by"
    check("match: the first entry whose glob matches the path relative to the root",
          outcome(lambda: ignored_by(str(root / "oob" / "a.py"), root, IGNORES)),
          ("oob/*", "vendor pages"))
    check("match: '*' crosses directories",
          outcome(lambda: ignored_by(str(root / "oob" / "deep" / "b.js"), root, IGNORES)),
          ("oob/*", "vendor pages"))
    check("match: a path no entry matches is None",
          outcome(lambda: ignored_by(str(root / "lib" / "c.py"), root, IGNORES)), None)
    check("match: no entries is None",
          outcome(lambda: ignored_by(str(root / "oob" / "a.py"), root, [])), None)


def test_drop_ignored() -> None:
    root = Path(tempfile.gettempdir()).resolve() / "jp-tools-drop-ignored"
    groups = {"python": [str(root / "oob" / "a.py"), str(root / "src" / "own.py"),
                         str(root / "lib" / "b.py")],
              "shell": [str(root / "oob" / "deep" / "c.sh")],
              "js": [str(root / "web" / "d.js")]}
    gone = outcome(lambda: _drop_ignored(groups, root, IGNORES))
    check("drop: each ignored file is returned with its relative path, glob and reason",
          gone, [{"file": "oob/a.py", "pattern": "oob/*", "reason": "vendor pages"},
                 {"file": "src/own.py", "pattern": "src/*.py", "reason": "generated"},
                 {"file": "oob/deep/c.sh", "pattern": "oob/*", "reason": "vendor pages"}])
    check("drop: each language keeps exactly the files no entry matches",
          groups, {"python": [str(root / "lib" / "b.py")], "shell": [], "js": [str(root / "web" / "d.js")]})


if __name__ == "__main__":
    sys.exit(run(globals(), "files-ignores"))
