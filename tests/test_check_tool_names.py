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
from jpcheck.runs import tool_names


def test_tool_names() -> None:
    check("--tools naming the audit tool, plus --audit: once",
          tool_names("php", "composer-audit", True), ["composer-audit"])
    check("a tool named twice in --tools: once, first-named order",
          tool_names("python", "ruff,mypy,ruff", False), ["ruff", "mypy"])
    check("control: --audit still adds an audit tool not named",
          tool_names("php", "phpstan", True), ["phpstan", "composer-audit"])


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
