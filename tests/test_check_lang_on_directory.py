#!/usr/bin/env python3
"""
JP_TOOLS/tests/test_check_lang_on_directory.py
A directory named with --lang or --tools runs each tool the way a plain
directory run does.

WHY
    `check.py DIR --lang php` handed DIR whole to every tool. The tools that
    read one file cannot read a directory: directive-reason stopped with
    "could not read DIR: IsADirectoryError", and shellcheck did not run, so the
    CI template's js, php and shell jobs could never pass. A plain directory
    run already ran those tools once per file.

WHAT IS CHECKED
    With --tools directive-reason on a directory: a reasonless directive in
    each file is found, and a file in .jp-tools-ignore is not read. With
    --lang, only that language's files are read. A tool with no runner is
    still reported, not dropped. Needs no linter.

    python3 tests/test_check_lang_on_directory.py
"""

import json
import subprocess
import sys
import tempfile
from pathlib import Path

from _harness import ROOT, check, run

CHECK_PY = ROOT / "check.py"

BARE_SH = "#!/bin/sh\n# shellcheck disable=SC2086\necho $1\n"
WHY_SH = "#!/bin/sh\n# shellcheck disable=SC2086  # split on purpose\necho $1\n"
BARE_PHP = "<?php\n// phpcs:ignore Generic.Files.LineLength\necho 1;\n"


def gate(d: Path, *args: str) -> tuple[int, list[tuple[str, str]], list[str]]:
    """check.py's exit code, (tool, status) per check, and each issue's file name."""
    r = subprocess.run([sys.executable, str(CHECK_PY), str(d), *args],
                       capture_output=True, text=True, check=False, cwd=d)
    try:
        doc = json.loads(r.stdout)
    except ValueError:
        # reason: not a check.py report: nothing reported, which fails every check below
        return r.returncode, [], []
    return (r.returncode, [(c["tool"], c["status"]) for c in doc["checks"]],
            sorted(Path(i["file"]).name for c in doc["checks"] for i in c["issues"]))


def repo(td: str) -> Path:
    d = Path(td)
    subprocess.run(["git", "init", "-q", str(d)], check=True)
    (d / "bare.sh").write_text(BARE_SH, encoding="utf-8")
    (d / "why.sh").write_text(WHY_SH, encoding="utf-8")
    (d / "page.php").write_text(BARE_PHP, encoding="utf-8")
    # Not vendor/, which the walk skips before .jp-tools-ignore is consulted.
    (d / "oob").mkdir()
    (d / "oob" / "copy.sh").write_text(BARE_SH, encoding="utf-8")
    (d / ".jp-tools-ignore").write_text("oob/*  # a vendor copy\n", encoding="utf-8")
    return d


def test_tools_on_a_directory_read_each_file() -> None:
    with tempfile.TemporaryDirectory() as td:
        d = repo(td)
        check("--tools directive-reason on a directory: each served file, none ignored",
              gate(d, "--tools", "directive-reason"),
              (1, [("directive-reason", "fail")], ["bare.sh", "page.php"]))


def test_lang_on_a_directory_reads_that_language_only() -> None:
    with tempfile.TemporaryDirectory() as td:
        d = repo(td)
        check("--lang shell on a directory: its shell files only",
              gate(d, "--lang", "shell", "--tools", "directive-reason"),
              (1, [("directive-reason", "fail")], ["bare.sh"]))
        check("--lang php on a directory: its PHP files only",
              gate(d, "--lang", "php", "--tools", "directive-reason"),
              (1, [("directive-reason", "fail")], ["page.php"]))
        (d / "bare.sh").unlink()
        (d / "page.php").unlink()
        check("control: with the reasonless files gone the directory passes",
              gate(d, "--lang", "shell", "--tools", "directive-reason"),
              (0, [("directive-reason", "pass")], []))


def test_a_tool_with_no_runner_is_reported_on_a_directory() -> None:
    with tempfile.TemporaryDirectory() as td:
        d = repo(td)
        check("a tool with no runner is named, not dropped",
              gate(d, "--tools", "nosuchtool"), (2, [("nosuchtool", "unknown")], []))


if __name__ == "__main__":
    sys.exit(run(globals(), "--lang on a directory"))
