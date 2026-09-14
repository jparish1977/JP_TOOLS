#!/usr/bin/env python
"""
JP_TOOLS/tests/test_check_prettier.py
run_prettier must count unformatted FILES, not prettier's summary line.

THE BUG THIS PINS DOWN
    `prettier --check` ends with a summary on its own `[warn]` line:

        [warn] jp_eslint.mjs
        [warn] Code style issues found in the above file. Run Prettier with --write to fix.

    run_prettier read every `[warn]` line as a filename, so each unformatted
    file became two warnings, one of them "file": "Code style issues found...".
    Seen in CI on PR #59, the first run in which prettier ran at all: before
    it, prettier resolved through PATH only and was "unavailable" after
    `npm install`.

A fake prettier stands in for the real one, so this needs neither node nor
prettier installed, and it pins the output shapes prettier 2 and 3 print.

No pytest, no dependencies.

    python tests/test_check_prettier.py
"""

import importlib.util
import os
import pathlib
import sys
import tempfile
from typing import Any

MODULE_PATH = pathlib.Path(__file__).resolve().parent.parent / "check.py"
sys.dont_write_bytecode = True
_spec = importlib.util.spec_from_file_location("check_mod", MODULE_PATH)
assert _spec is not None and _spec.loader is not None
check_mod = importlib.util.module_from_spec(_spec)
sys.modules["check_mod"] = check_mod
_spec.loader.exec_module(check_mod)

FAILURES: list[str] = []

FAKE = """#!{python}
import os, sys
sys.stdout.write(open(os.environ["FAKE_PRETTIER_OUT"]).read())
sys.exit(int(os.environ["FAKE_PRETTIER_RC"]))
"""


def expect(label: str, got: object, want: object) -> None:
    if got != want:
        FAILURES.append(f"{label}: got {got!r}, want {want!r}")


def run_with(tmp: pathlib.Path, output: str, rc: int) -> dict[str, object]:
    fake = tmp / "prettier"
    fake.write_text(FAKE.format(python=sys.executable))
    fake.chmod(0o755)
    out = tmp / "out.txt"
    out.write_text(output)
    os.environ["FAKE_PRETTIER_OUT"] = str(out)
    os.environ["FAKE_PRETTIER_RC"] = str(rc)
    # Through an Any alias: mypy types a module loaded from a spec as
    # ModuleType, which it lets you read attributes from but not assign.
    mod: Any = check_mod
    real = mod._node_bin
    mod._node_bin = lambda name: str(fake)
    try:
        result: dict[str, object] = mod.run_prettier(str(tmp))
        return result
    finally:
        mod._node_bin = real


def files(result: dict[str, object]) -> list[str]:
    issues = result.get("issues")
    assert isinstance(issues, list)
    return [str(i["file"]) for i in issues]


def main() -> int:
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
        expect(f"{label}: files", files(result), want_files)
        expect(f"{label}: status", result.get("status"), want_status)
    if FAILURES:
        print(f"FAILED ({len(FAILURES)})")
        for f in FAILURES:
            print(f"  {f}")
        return 1
    print(f"all check.py prettier tests passed ({len(cases)} cases)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
