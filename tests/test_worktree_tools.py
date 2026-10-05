#!/usr/bin/env python3
"""
JP_TOOLS/tests/test_worktree_tools.py
A git worktree of JP_TOOLS finds the tools its main checkout has installed.

WHY
    vendor/ and node_modules/ are not tracked, so a worktree has neither. check.py
    looked only in its own tree: from a worktree it reported phpstan, phpcs and
    rector as not installed (exit 2) and could not load eslint or stylelint, on
    a machine that had all of them. Two reviews recorded "not installed" for
    tools the box had.

WHAT IS CHECKED, on a scratch repository and a worktree of it
    - tool_roots() is the worktree, then the main checkout; in a checkout that
      is not a worktree, only itself.
    - _php_bin and _node_bin find a bin the main checkout has, from the worktree.
    - A node runner in the worktree resolves a bare import the main checkout's
      node_modules holds, through jp_node_resolve.mjs. Control: the same
      import with plain node fails, so the pass is the hook's.

    python3 tests/test_worktree_tools.py
"""

import json
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

from _harness import ROOT, check, check_true, run

# Run inside a checkout: what jpcheck there says about tools.
PROBE = """
import json, sys
sys.path.insert(0, sys.argv[1])
from jpcheck.common import _node_bin, _php_bin, tool_roots
from jpcheck.js_tools import _resolve_args, _runner_env
print(json.dumps({"roots": [str(r) for r in tool_roots()],
                  "php": _php_bin("jp-fake-php"), "node": _node_bin("jp-fake-node"),
                  "args": _resolve_args(), "env": _runner_env().get("JP_TOOLS_NODE_ROOTS", "")}))
"""


def git(cwd: Path, *args: str) -> None:
    subprocess.run(["git", "-c", "user.email=t@example.com", "-c", "user.name=t", *args],
                   cwd=cwd, check=True, capture_output=True)


def _main_checkout(base: Path) -> Path:
    """A repository holding jpcheck and the node hook, with untracked installed tools."""
    main = base / "main"
    main.mkdir()
    for package in ("jpcheck", "jpexit"):
        shutil.copytree(ROOT / package, main / package,
                        ignore=shutil.ignore_patterns("__pycache__"))
    for name in ("jp_node_resolve.mjs", "jp_node_hooks.mjs"):
        shutil.copy(ROOT / name, main / name)
    (main / "probe.mjs").write_text('import { answer } from "jp-probe-pkg";\nconsole.log(answer);\n',
                                    encoding="utf-8")
    git(main, "init", "-q")
    git(main, "add", "jpcheck", "jpexit", "jp_node_resolve.mjs", "jp_node_hooks.mjs", "probe.mjs")
    git(main, "commit", "-qm", "t")
    for rel in ("vendor/bin/jp-fake-php", "node_modules/.bin/jp-fake-node"):
        (main / rel).parent.mkdir(parents=True)
        (main / rel).write_text("#!/bin/sh\n", encoding="utf-8")
        (main / rel).chmod(0o755)
    pkg = main / "node_modules" / "jp-probe-pkg"
    pkg.mkdir()
    (pkg / "package.json").write_text('{"name": "jp-probe-pkg", "type": "module", '
                                      '"exports": "./index.mjs"}', encoding="utf-8")
    (pkg / "index.mjs").write_text("export const answer = 42;\n", encoding="utf-8")
    return main


def probe(checkout: Path) -> dict[str, object]:
    r = subprocess.run([sys.executable, "-c", PROBE, str(checkout)], capture_output=True,
                       text=True, check=False, cwd=checkout)
    try:
        return dict(json.loads(r.stdout))
    except ValueError:
        # reason: no report means every check below fails, and says what came back
        return {"error": (r.stdout + r.stderr)[-300:]}


def node(wt: Path, report: dict[str, object], hooked: bool) -> str:
    given = report.get("args", [])
    args: list[str] = [str(a) for a in given] if hooked and isinstance(given, list) else []
    env = {**os.environ, "JP_TOOLS_NODE_ROOTS": str(report.get("env", ""))}
    r = subprocess.run(["node", *args, str(wt / "probe.mjs")], capture_output=True, text=True,
                       check=False, cwd=wt, env=env)
    return r.stdout.strip() if r.returncode == 0 else f"exit {r.returncode}"


def test_a_worktree_finds_the_main_checkouts_tools() -> None:
    with tempfile.TemporaryDirectory() as td:
        base = Path(td).resolve()
        main = _main_checkout(base)
        git(main, "worktree", "add", "-q", str(base / "wt"))
        wt = base / "wt"
        report = probe(wt)
        check("tool roots: the worktree, then the main checkout",
              report.get("roots"), [str(wt), str(main)])
        check("a PHP bin only the main checkout has is found",
              report.get("php"), str(main / "vendor" / "bin" / "jp-fake-php"))
        check("a node bin only the main checkout has is found",
              report.get("node"), str(main / "node_modules" / ".bin" / "jp-fake-node"))
        args = report.get("args", [])
        check_true("the node runner gets the resolve hook",
                   isinstance(args, list) and "--import" in args)
        if shutil.which("node"):
            check("a bare import the main checkout holds resolves in the worktree",
                  node(wt, report, hooked=True), "42")
            check_true("control: without the hook the same import fails",
                       node(wt, report, hooked=False).startswith("exit"))
        control = probe(main)
        check("control: the main checkout's roots are itself only", control.get("roots"), [str(main)])
        check("control: the main checkout's node runner gets no hook", control.get("args"), [])


if __name__ == "__main__":
    sys.exit(run(globals(), "worktree tools"))
