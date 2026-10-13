#!/usr/bin/env python3
"""
JP_TOOLS/tests/test_registry_summary.py
jpcheck.registry's _run_tools and _summarize, whole documents compared.

WHAT IS CHECKED
    - A tool name with no runner is a check of its own: tool, status
      unknown, no issues, and a note naming it.
    - The summary counts: every issue in total; errors and warnings from
      the issues not exempt; exempt as the difference; fixable and
      fixable_unsafe from every issue, exempt ones included. A check with no
      issues key counts as none.

    python3 tests/test_registry_summary.py
"""

import sys
from typing import Any

from _harness import check, run
from jpcheck.registry import _run_tools, _summarize


def test_a_name_with_no_runner() -> None:
    check("no runner: the check that says so", _run_tools(["no-such"], "x.py"),
          [{"tool": "no-such", "status": "unknown", "issues": [],
            "note": "No runner for 'no-such'"}])


def test_the_summary() -> None:
    checks: list[dict[str, Any]] = [
        {"tool": "a", "status": "fail", "issues": [
            {"severity": "error"},
            {"severity": "warning", "fixable": True},
            {"severity": "warning", "fixable_unsafe": True},
        ]},
        {"tool": "b", "status": "pass"},
        {"tool": "c", "status": "fail", "issues": [
            {"severity": "error", "exempt": "a lab file", "fixable_unsafe": True},
        ]},
    ]
    try:
        summary: object = _summarize(checks)
    except Exception as e:  # noqa: BLE001 -- reason: a crash is an outcome the check compares, named
        summary = ("raised", type(e).__name__)
    check("the summary of four issues, one exempt", summary,
          {"total": 4, "errors": 1, "warnings": 2, "exempt": 1,
           "fixable": 1, "fixable_unsafe": 2})


if __name__ == "__main__":
    sys.exit(run(globals(), "registry summary"))
