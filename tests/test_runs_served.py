#!/usr/bin/env python3
"""
JP_TOOLS/tests/test_runs_served.py
Which files a tool is handed on a directory run: jpcheck.runs._served, and
the script _dir_capable_result names to a tool that discovers files by
suffix. In-process, with a fake runner, so every path a tool is handed is
seen.

WHAT IS CHECKED
    - _served with a language named: that language's files only, even for
      a tool that serves others too; none when the directory has none.
    - _served with no language named (--tools alone): the files of every
      language whose default or audit tools include the tool, and a group no
      table knows is passed over rather than failing.
    - _dir_capable_result: the tool runs once over the directory, then once
      on a script whose suffix does not name its language (a #! file with no
      suffix), and that run's findings are folded into the one result.

    python3 tests/test_runs_served.py
"""

import sys
import tempfile
from collections.abc import Callable
from pathlib import Path
from typing import Any

from _harness import check, run
from jpcheck import runs

GROUPS = {"python": ["a.py"], "shell": ["b.sh"]}


def outcome(fn: Callable[[], Any]) -> object:
    """What FN returned, or the name of what it raised."""
    try:
        return fn()
    except Exception as e:  # noqa: BLE001 -- reason: a crash is an outcome the check compares, named
        return ("raised", type(e).__name__)


def test_a_named_language() -> None:
    check("smells with --lang python: the python files, not the shell ones",
          outcome(lambda: runs._served("smells", "python", GROUPS)), ["a.py"])
    check("--lang css on a directory with no css: no files",
          outcome(lambda: runs._served("shellcheck", "css", GROUPS)), [])


def test_no_language_named() -> None:
    check("pip-audit, no language: the files of the language it audits",
          outcome(lambda: runs._served("pip-audit", "unknown", GROUPS)), ["a.py"])
    check("no language, and a group no table knows: passed over",
          outcome(lambda: runs._served("pip-audit", "unknown",
                                       {"python": ["a.py"], "text": ["n.txt"]})), ["a.py"])


def test_a_script_without_a_suffix_is_named_to_a_dir_capable_tool() -> None:
    seen: list[object] = []

    def ruff(path: str) -> dict[str, Any]:
        seen.append(path)
        return {"tool": "ruff", "status": "pass", "issues": [{"file": path}]}

    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp).resolve()
        (root / "a.py").write_text("x = 1\n", encoding="utf-8")
        tool = root / "tool"
        tool.write_text("#!/usr/bin/env python3\nx = 1\n", encoding="utf-8")
        files = [str(root / "a.py"), str(tool)]
        result = outcome(lambda: runs._dir_capable_result(ruff, str(root), files, root, []))
    check("a suffixless script: the directory, then the script, and both runs' findings",
          (seen, result),
          ([str(root), str(tool)],
           {"tool": "ruff", "status": "pass", "issues": [{"file": str(root)}, {"file": str(tool)}]}))


if __name__ == "__main__":
    sys.exit(run(globals(), "runs served"))
