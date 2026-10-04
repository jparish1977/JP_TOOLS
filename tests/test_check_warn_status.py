#!/usr/bin/env python3
"""
JP_TOOLS/tests/test_check_warn_status.py
A check whose findings are all warnings reports "warn", not "fail".

No pytest, no dependencies, same as the rest of tests/.

WHAT THIS PINS DOWN
    A check's status is its verdict on what it found: "fail" on any error,
    "warn" when every finding is a warning, "pass" on none. Exempt findings
    are listed and not counted, as in the summary. Only errors decide the exit
    code, so "warn" exits 0. Merging two results keeps the worse verdict.

    npm audit's findings are errors. They carried npm's own level (low to
    critical) as their severity, which the summary does not count as an
    error, so a vulnerable package.json reported "fail" and exited 0.

    Fake composer and npm on PATH stand in for the real ones.

    python3 tests/test_check_warn_status.py
"""

import json
import subprocess
import sys
from pathlib import Path
from typing import Any

from _harness import ROOT, check, run
from fake_tools import fake_tools
from jpcheck.common import _verdict, _worse
from jpcheck.lab import _LAB_EXEMPT_REASON
from jpcheck.runs import _fold, _per_file_result

ERROR = {"severity": "error"}
WARNING = {"severity": "warning"}
ABANDONED = json.dumps({"advisories": [], "abandoned": {"old/pkg": None}})
ADVISORY = json.dumps({"advisories": {"a/b": [{"advisoryId": "PKSA-1", "title": "XSS"}]},
                       "abandoned": []})
NPM_HIGH = json.dumps({"vulnerabilities": {"left-pad": {"severity": "high", "via": ["CVE-1"]}}})


def gate(tool: str, stdout: str, manifest: str) -> tuple[int, str]:
    """check.py's exit code and TOOL's status, on a project whose TOOL prints STDOUT."""
    fake = f"sys.stdout.write({stdout!r})\n"
    with fake_tools(named={tool: fake}) as (work, _log):
        (Path(work) / manifest).write_text("{}", encoding="utf-8")
        r = subprocess.run([sys.executable, str(ROOT / "check.py"), str(work), "--tools",
                            f"{tool}-audit" if tool == "npm" else "composer-audit"],
                           capture_output=True, text=True, check=False)
    try:
        status = str(json.loads(r.stdout)["checks"][0]["status"])
    except (ValueError, KeyError, IndexError):
        # reason: no report means the checks below fail, naming what was printed
        status = (r.stdout + r.stderr)[-200:]
    return r.returncode, status


def test_verdict() -> None:
    check("no findings: pass", _verdict([]), "pass")
    check("warnings only: warn", _verdict([WARNING, WARNING]), "warn")
    check("any error: fail", _verdict([WARNING, ERROR]), "fail")
    check("an exempt error is not counted: warn",
          _verdict([{**ERROR, "exempt": "why"}, WARNING]), "warn")
    check("only exempt findings: pass", _verdict([{**ERROR, "exempt": "why"}]), "pass")


def test_worse() -> None:
    check("warn over pass", (_worse("pass", "warn"), _worse("warn", "pass")), ("warn", "warn"))
    check("fail over warn", (_worse("warn", "fail"), _worse("fail", "warn")), ("fail", "fail"))


def test_merges_keep_the_worse_verdict() -> None:
    def result(status: str) -> dict[str, Any]:
        return {"tool": "t", "status": status, "issues": []}

    folded = result("pass")
    _fold(folded, result("warn"))
    check("folding a warn into a pass: warn", folded["status"], "warn")
    _fold(folded, result("fail"))
    check("then a fail: fail", folded["status"], "fail")
    _fold(folded, result("warn"))
    check("a later warn does not soften a fail", folded["status"], "fail")
    by_file = {"a": "pass", "b": "warn"}
    merged = _per_file_result("t", lambda f: result(by_file[f]), ["a", "b"])
    check("per-file pass and warn: warn", merged["status"], "warn")


def test_lab_exemption_leaves_the_warnings() -> None:
    lab = ROOT / "tools" / "lab"
    header = next((f for f in sorted(lab.glob("*.py"))
                   if "NOT TESTED AGAINST" in f.read_text(encoding="utf-8")[:4000]), None)
    if header is None:
        print("SKIP: no tools/lab file with a 2.9 header in this checkout")
        return
    from jpcheck.lab import _mark_lab_exempt
    checks: list[dict[str, Any]] = [{"tool": "t", "status": "fail", "issues": [
        {**ERROR, "file": str(header)}, {**WARNING, "file": str(ROOT / "check.py")}]}]
    _mark_lab_exempt(checks)
    check("the lab file's error is exempt",
          checks[0]["issues"][0].get("exempt"), _LAB_EXEMPT_REASON)
    check("an exempt error and a counted warning: warn", checks[0]["status"], "warn")


def test_check_py_end_to_end() -> None:
    check("an abandoned package alone: exit 0, warn",
          gate("composer", ABANDONED, "composer.json"), (0, "warn"))
    check("control, an advisory: exit 1, fail",
          gate("composer", ADVISORY, "composer.json"), (1, "fail"))
    check("an npm vulnerability rated high: exit 1, fail",
          gate("npm", NPM_HIGH, "package.json"), (1, "fail"))


if __name__ == "__main__":
    sys.exit(run(globals(), "warn status"))
