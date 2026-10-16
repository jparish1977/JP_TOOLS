#!/usr/bin/env python3
"""
JP_TOOLS/tests/test_check_tool_names.py
Each check runs once, whatever combination of flags selects it.

No pytest, no dependencies, same as the rest of tests/.

WHAT THIS PINS DOWN
    --audit adds the language's audit tool to the tool list. When --tools
    already named it, it ran twice and every finding it reported was counted
    twice. The same holds for a tool named twice in --tools. The list keeps
    first-named order.

    python3 tests/test_check_tool_names.py
"""

import json
import subprocess
import sys
from pathlib import Path

from _harness import ROOT, check, run
from fake_tools import fake_tools
from jpcheck.registry import serves
from jpcheck.runs import tool_names


def test_tool_names() -> None:
    check("--tools naming the audit tool, plus --audit: once",
          tool_names("php", "composer-audit", True), ["composer-audit"])
    check("a tool named twice in --tools: once, first-named order",
          tool_names("python", "ruff,mypy,ruff", False), ["ruff", "mypy"])
    check("control: --audit still adds an audit tool not named",
          tool_names("php", "phpstan", True), ["phpstan", "composer-audit"])


def test_a_named_tool_runs_only_where_it_applies() -> None:
    """--tools on a file of a known language keeps the named tools that serve
    it: a shell linter run on a Python file reports nonsense as findings."""
    check("python: the shell tools named are dropped, the python ones kept in order",
          tool_names("python", "shellcheck,ruff,directive-reason,smells", False), ["ruff", "smells"])
    check("  an audit tool serves its language", tool_names("python", "pip-audit", False), ["pip-audit"])
    check("  none that serves it: nothing runs", tool_names("shell", "mypy", False), [])
    check("a language with no tools of its own: the names as given",
          tool_names("unknown", "shellcheck,ruff", False), ["shellcheck", "ruff"])
    check("serves: a tool, an audit, another language's tool",
          [serves("smells", "shell"), serves("npm-audit", "js"), serves("ruff", "shell")], [True, True, False])


def test_check_py_lists_composer_audit_once() -> None:
    # The command from the report, against a fake composer with nothing to say.
    body = 'sys.stdout.write(\'{"advisories": [], "abandoned": []}\')\n'
    with fake_tools(composer=body) as (work, _log):
        (Path(work) / "composer.json").write_text("{}", encoding="utf-8")
        r = subprocess.run([sys.executable, str(ROOT / "check.py"), str(work), "--lang", "php",
                            "--audit", "--tools", "composer-audit"],
                           capture_output=True, text=True, check=False)
    try:
        tools = [c["tool"] for c in json.loads(r.stdout)["checks"]]
    except ValueError:
        # reason: no report means the check below fails, naming what was printed
        tools = [r.stdout[-200:], r.stderr[-200:]]
    check("check.py --lang php --audit --tools composer-audit", tools, ["composer-audit"])


if __name__ == "__main__":
    sys.exit(run(globals(), "tool names"))
