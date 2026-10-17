#!/usr/bin/env python
"""
JP_TOOLS/tests/test_check_prettier.py
run_prettier must count unformatted FILES, not prettier's summary line.

WHAT THIS PINS DOWN
    `prettier --check` ends with a summary on its own `[warn]` line:

        [warn] jp_eslint.mjs
        [warn] Code style issues found in the above file. Run Prettier with --write to fix.

    Read as a filename, that line would turn each unformatted file into two
    warnings, one of them "file": "Code style issues found...". An
    unformatted file is a warning, so the status is "warn", not "fail".

    The report is compared whole (tool, status, every field of every
    issue), as is the one for prettier not installed. The call is pinned:
    the bin asked for by name, `prettier --check TARGET` with every keyword
    argument, colour off. baseline's version command for prettier likewise.

A fake prettier stands in for the real one, so this needs neither node nor
prettier installed, and it pins the output shapes prettier 2 and 3 print.

No pytest, no dependencies.

    python tests/test_check_prettier.py
"""

import os
import pathlib
import subprocess
import sys
import tempfile
from typing import Any
from unittest import mock

from _harness import check, run
from jpcheck import baseline, js_tools
from jpcheck.common import _plain_env

FAKE = """#!{python}
import os, sys
sys.stdout.write(open(os.environ["FAKE_PRETTIER_OUT"]).read())
sys.exit(int(os.environ["FAKE_PRETTIER_RC"]))
"""


def run_with(tmp: pathlib.Path, output: str, rc: int) -> dict[str, Any]:
    fake = tmp / "prettier"
    fake.write_text(FAKE.format(python=sys.executable))
    fake.chmod(0o755)
    out = tmp / "out.txt"
    out.write_text(output)
    os.environ["FAKE_PRETTIER_OUT"] = str(out)
    os.environ["FAKE_PRETTIER_RC"] = str(rc)
    # run_prettier looks _node_bin up in its own module, so that is where the
    # fake is installed.
    with mock.patch.object(js_tools, "_node_bin", lambda name: str(fake)):
        return js_tools.run_prettier(str(tmp))


def files(result: dict[str, Any]) -> list[str]:
    return [str(i["file"]) for i in result["issues"]]


def test_the_summary_line_is_not_a_file() -> None:
    cases = [
        ("prettier 3, one file",
         ("Checking formatting...\n[warn] a.js\n"
          "[warn] Code style issues found in the above file. Run Prettier with --write to fix.\n"),
         1, ["a.js"], "warn"),
        ("prettier 3, two files",
         ("Checking formatting...\n[warn] a.js\n[warn] b.js\n"
          "[warn] Code style issues found in 2 files. Run Prettier with --write to fix.\n"),
         1, ["a.js", "b.js"], "warn"),
        ("prettier 2 wording",
         ("Checking formatting...\n[warn] a.js\n"
          "[warn] Code style issues found in the above file(s). Forgot to run Prettier?\n"),
         1, ["a.js"], "warn"),
        ("clean",
         "Checking formatting...\nAll matched files use Prettier code style!\n",
         0, [], "pass"),
        ("prettier could not run",
         "[error] No parser could be inferred for file: a.xyz\n",
         2, [], "error"),
    ]
    for label, output, rc, want_files, want_status in cases:
        with tempfile.TemporaryDirectory() as d:
            result = run_with(pathlib.Path(d), output, rc)
        check(f"{label}: files", files(result), want_files)
        check(f"{label}: status", result.get("status"), want_status)


def issue(fp: str) -> dict[str, Any]:
    return {"file": fp, "line": 0, "col": 0, "severity": "warning", "rule": "prettier/formatting",
            "message": "File is not formatted correctly", "fixable": True}


def test_the_whole_report() -> None:
    """Compared whole: the tool's name, the status, every field of every issue,
    from output as prettier 3 writes it (the [warn] lines on stderr, the
    summary last)."""
    output = "[warn] a.js\n[warn] b.js\n[warn] Code style issues found in 2 files. Run Prettier with --write to fix.\n"
    with tempfile.TemporaryDirectory() as d:
        result = run_with(pathlib.Path(d), output, 1)
    check("two unformatted files: the report, whole",
          result, {"tool": "prettier", "status": "warn", "issues": [issue("a.js"), issue("b.js")]})


def test_the_call_and_the_tool_asked_for() -> None:
    """prettier is looked up by its own name and run as `prettier --check
    TARGET`, capturing text, never raising on its exit code, in the
    environment with colour turned off."""
    asked: list[str] = []
    calls: list[tuple[list[str], dict[str, Any]]] = []

    def node_bin(name: str) -> str:
        asked.append(name)
        return "/x/prettier"

    def fake_run(cmd: list[str], **kwargs: Any) -> subprocess.CompletedProcess[str]:
        calls.append((cmd, kwargs))
        return subprocess.CompletedProcess(cmd, 0, "", "")

    with mock.patch.object(js_tools, "_node_bin", node_bin), \
         mock.patch.object(subprocess, "run", fake_run):
        result = js_tools.run_prettier("T")
        plain = _plain_env()
    check("the bin asked for is prettier", asked, ["prettier"])
    check("run once: prettier --check T, every keyword exactly",
          calls, [(["/x/prettier", "--check", "T"],
                   {"capture_output": True, "text": True, "check": False, "env": plain})])
    check("  nothing to report: pass", result, {"tool": "prettier", "status": "pass", "issues": []})


def test_not_installed() -> None:
    with mock.patch.object(js_tools, "_node_bin", lambda name: None):
        result = js_tools.run_prettier("T")
    check("no prettier: unavailable, named, with how to install it, whole",
          result, {"tool": "prettier", "status": "unavailable", "issues": [],
                   "note": "'prettier' not found: run: npm install in JP_TOOLS"})


def test_the_version_command() -> None:
    asked: list[str] = []

    def node_bin(name: str) -> str | None:
        asked.append(name)
        return "/x/prettier" if name == "prettier" else None

    with mock.patch.object(baseline, "_node_bin", node_bin):
        check("prettier's version command", baseline._prettier(), ["/x/prettier", "--version"])
    check("  looked up by its own name", asked, ["prettier"])
    with mock.patch.object(baseline, "_node_bin", lambda name: None):
        check("  no prettier: no command", baseline._prettier(), None)


if __name__ == "__main__":
    sys.exit(run(globals(), "check.py prettier"))
