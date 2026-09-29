#!/usr/bin/env python3
"""
JP_TOOLS/tests/test_missing_tool_messages.py
A tool that is not installed is reported as unavailable, naming how to install it.

WHY
    With no node_modules anywhere, eslint and stylelint were started anyway and
    reported a raw node error (status "error"), where phpstan says "phpstan
    (run: composer install in JP_TOOLS)". prettier, ruff and mypy were reported
    by bare name. All of them exit 2 either way, so nothing passed; what was
    missing is the one line that tells a reader what to run.

WHAT IS CHECKED
    check.py runs from a standalone copy of this working tree (no .git, no
    vendor/, no node_modules/, so nothing is borrowed), with a PATH holding
    only node, or nothing at all:
    - eslint, stylelint and prettier: unavailable, naming `npm install`.
    - ruff and mypy: unavailable, naming requirements-dev.txt.
    - each is reported under its own tool name, with the install line in its
      note and on stderr, so a reader looking a check up by name still finds it.
    - every one of them exits 2.

    python3 tests/test_missing_tool_messages.py
"""

import json
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

from _harness import CHECKOUT_COPY_IGNORE, ROOT, check, check_true, run

NPM = "run: npm install in JP_TOOLS"
PIP = "pip install -r requirements-dev.txt"


def gate(clone: Path, path: Path, bin_dir: Path) -> tuple[int, dict[str, tuple[str, str]], str]:
    """check.py's exit code on PATH, each check's tool -> (status, note), and its stderr."""
    r = subprocess.run([sys.executable, str(clone / "check.py"), str(path)], capture_output=True,
                       text=True, check=False, env={"PATH": str(bin_dir), "HOME": str(bin_dir)})
    try:
        doc = json.loads(r.stdout)
    except ValueError:
        # reason: no report means the checks below find nothing, and fail
        return r.returncode, {}, r.stderr
    checks = {str(c["tool"]): (str(c["status"]), str(c.get("note", "")))
              for c in doc.get("checks", [])}
    return r.returncode, checks, r.stderr


def missing(checks: dict[str, tuple[str, str]], err: str, tool: str, install: str) -> bool:
    """TOOL is reported by its own name as unavailable, and the install line reaches stderr."""
    status, note = checks.get(tool, ("absent", ""))
    return status == "unavailable" and install in note and f"did not run {tool} " in err \
        and install in err


def test_missing_tools_say_how_to_install() -> None:
    node = shutil.which("node")
    with tempfile.TemporaryDirectory() as td:
        base = Path(td)
        # A copy of the working tree (so it tests this code, not the last
        # commit) without .git, vendor/ or node_modules/: a standalone tree
        # with nothing installed and no main checkout to borrow from.
        clone = base / "clone"
        shutil.copytree(ROOT, clone, symlinks=True, ignore=CHECKOUT_COPY_IGNORE)
        empty = base / "empty"
        empty.mkdir()
        (base / "t.js").write_text('eval("1");\n', encoding="utf-8")
        (base / "t.css").write_text("a {\n  color: #fff;\n}\n", encoding="utf-8")
        (base / "t.py").write_text("x = 1\n", encoding="utf-8")
        if node:
            only_node = base / "bin"
            only_node.mkdir()
            (only_node / "node").symlink_to(node)
            for f, tools in (("t.js", ("eslint", "prettier")), ("t.css", ("stylelint", "prettier"))):
                rc, checks, err = gate(clone, base / f, only_node)
                check(f"{f}: exit 2, a check that did not run", rc, 2)
                for tool in tools:
                    check_true(f"{f}: {tool} is unavailable by its own name, naming npm install",
                               missing(checks, err, tool, NPM))
        else:
            print("SKIP: node not installed; the JS cases need it to reach the package check")
        rc, checks, err = gate(clone, base / "t.py", empty)
        check("t.py: exit 2, a check that did not run", rc, 2)
        for tool in ("ruff", "mypy"):
            check_true(f"t.py: {tool} is unavailable by its own name, naming requirements-dev.txt",
                       missing(checks, err, tool, PIP))
        # Smells needs ruff too, and says so under its own name: reported as
        # `ruff`, it would replace ruff's own report in a lookup by name.
        status, note = checks.get("smells", ("absent", ""))
        check("t.py: smells is unavailable under its own name, naming ruff and how to install it",
              (status, "ruff" in note and PIP in note), ("unavailable", True))
        check_true("control: the clone borrowed nothing (no vendor/, no node_modules/)",
                   not (clone / "vendor").exists() and not (clone / "node_modules").exists())


if __name__ == "__main__":
    sys.exit(run(globals(), "missing tool messages"))
