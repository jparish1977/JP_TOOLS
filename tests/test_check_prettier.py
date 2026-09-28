#!/usr/bin/env python
"""
JP_TOOLS/tests/test_check_prettier.py
run_prettier must count unformatted FILES, not prettier's summary line.

WHAT THIS PINS DOWN
    `prettier --check` ends with a summary on its own `[warn]` line:

        [warn] jp_eslint.mjs
        [warn] Code style issues found in the above file. Run Prettier with --write to fix.

    Read as a filename, that line would turn each unformatted file into two
    warnings, one of them "file": "Code style issues found...".

A fake prettier stands in for the real one, so this needs neither node nor
prettier installed, and it pins the output shapes prettier 2 and 3 print.

No pytest, no dependencies.

    python tests/test_check_prettier.py
"""

import os
import pathlib
import sys
import tempfile
from typing import Any
from unittest import mock

from _harness import check, run
from jpcheck import js_tools

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
         1, ["a.js"], "fail"),
        ("prettier 3, two files",
         ("Checking formatting...\n[warn] a.js\n[warn] b.js\n"
          "[warn] Code style issues found in 2 files. Run Prettier with --write to fix.\n"),
         1, ["a.js", "b.js"], "fail"),
        ("prettier 2 wording",
         ("Checking formatting...\n[warn] a.js\n"
          "[warn] Code style issues found in the above file(s). Forgot to run Prettier?\n"),
         1, ["a.js"], "fail"),
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


if __name__ == "__main__":
    sys.exit(run(globals(), "check.py prettier"))
