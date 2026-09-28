"""Runners for the Python tools: ruff, mypy, pip-audit."""

import json
import re
import shutil
import subprocess
from pathlib import Path
from typing import Any

from jpcheck.common import (
    TOOLS_ROOT,
    _find_project_config,
    _plain_env,
    _status,
    _tool_missing,
    strip_ansi,
)


def _ruff_config_args(target: str) -> list[str]:
    """Point ruff at the JP_TOOLS defaults, unless the project has its own.

    ruff already implements precedence when it discovers a config itself, so
    passing --config on top of a project's own file would override the project
    rather than defer to it. Supply the default only when there is nothing to
    discover. A pyproject.toml is the project's ruff config only when it has a
    [tool.ruff] table; ruff skips one without, and so does this.
    """
    if _find_project_config(target, ["ruff.toml", ".ruff.toml", "pyproject.toml"],
                            {"pyproject.toml": "tool.ruff"}):
        return []
    shared = TOOLS_ROOT / "configs" / "ruff.toml"
    return ["--config", str(shared)] if shared.exists() else []


def run_ruff(target: str) -> dict[str, Any]:
    if not shutil.which("ruff"):
        return _tool_missing("ruff")
    result = subprocess.run(
        ["ruff", "check", *_ruff_config_args(target),
         "--output-format", "json", target],
        capture_output=True, text=True, check=False,
    )
    try:
        raw = json.loads(result.stdout) if result.stdout.strip() else []
    except json.JSONDecodeError:
        raw = []
    issues = [
        {
            "file":     i.get("filename", ""),
            "line":     i.get("location", {}).get("row", 0),
            "col":      i.get("location", {}).get("column", 0),
            "severity": "error",
            "rule":     i.get("code", ""),
            "message":  i.get("message", ""),
            # "fixable" means fix.py can apply it as invoked by default, which
            # is `ruff check --fix`: safe fixes only. A finding can carry a fix
            # object that is unsafe or display-only, which is never applied.
            "fixable":  (i.get("fix") or {}).get("applicability") == "safe",
            "fixable_unsafe":
                (i.get("fix") or {}).get("applicability") == "unsafe",
        }
        for i in raw
    ]
    # The return code is read, not discarded. ruff exits 0 clean, 1 for
    # findings, and 2 when it could not run -- a config that fails to load
    # writes to stderr and leaves stdout empty, so without the code this would
    # publish "pass" with 0 issues.
    return {"tool": "ruff", "status": _status(issues, result.returncode),
            "issues": issues}


# mypy prints "file:line: severity: message [code]", optionally with a column,
# and on Windows the path carries a drive letter, so splitting on ":" puts
# the severity in the wrong field and every error reads as a note.
_MYPY_LINE = re.compile(
    r"^(?P<file>(?:[A-Za-z]:)?[^:]+):(?P<line>\d+):(?:(?P<col>\d+):)?\s*"
    r"(?P<sev>error|warning|note):\s*(?P<msg>.*)$"
)


def _mypy_config_args(target: str) -> list[str]:
    """Point mypy at the JP_TOOLS defaults, unless the project has its own.

    Project-local config wins, same precedence as _ruff_config_args, and
    setup.cfg and pyproject.toml count only when they carry mypy's section,
    as mypy itself reads them.
    """
    if _find_project_config(target, ["mypy.ini", ".mypy.ini", "setup.cfg",
                                     "pyproject.toml"],
                            {"setup.cfg": "mypy", "pyproject.toml": "tool.mypy"},
                            stop_at_repo_root=True):
        return []
    shared = TOOLS_ROOT / "configs" / "mypy.ini"
    return ["--config-file", str(shared)] if shared.exists() else []


def run_mypy(target: str) -> dict[str, Any]:
    if not shutil.which("mypy"):
        return _tool_missing("mypy")
    result = subprocess.run(
        ["mypy", "--show-error-codes", "--no-error-summary",
         "--ignore-missing-imports", *_mypy_config_args(target), target],
        capture_output=True, text=True, check=False, env=_plain_env(),
    )
    issues = []
    for line in strip_ansi(result.stdout).splitlines():
        m = _MYPY_LINE.match(line)
        if not m:
            continue
        severity = m.group("sev")
        if severity == "note":
            continue  # context lines, not actionable on their own
        msg = m.group("msg").strip()
        rule = ""
        if msg.endswith("]") and "[" in msg:
            b = msg.rfind("[")
            rule = msg[b + 1:-1]
            msg  = msg[:b].strip()
        issues.append({
            "file":     m.group("file"),
            "line":     int(m.group("line")),
            "col":      int(m.group("col") or 0),
            "severity": severity,
            "rule":     f"mypy:{rule}" if rule else "mypy",
            "message":  msg,
            "fixable":  False,
            "fixable_unsafe": False,
        })
    return {"tool": "mypy", "status": _status(issues, result.returncode), "issues": issues}


# ── security audit runners ────────────────────────────────────────────────────

def _requirements_file(target: str) -> str | None:
    """The requirements file to audit: TARGET itself if it is one, else the first found in it."""
    p = Path(target)
    if p.is_dir():
        for name in ("requirements.txt", "requirements-dev.txt", "requirements.lock"):
            if (p / name).exists():
                return str(p / name)
        return None
    return target if p.suffix == ".txt" else None


def _pip_audit_issues(stdout: str, req_file: str | None) -> list[dict[str, Any]]:
    return [
        {
            "file":     req_file or "(installed packages)",
            "line":     0,
            "col":      0,
            "severity": "error",
            "rule":     v.get("id", "CVE"),
            "message":  f"{vuln.get('name')}=={vuln.get('version')}: {v.get('description', v.get('id', ''))}",
            "fixable":  bool(v.get("fix_versions")),
        }
        for vuln in json.loads(stdout).get("dependencies", [])
        for v in vuln.get("vulns", [])
    ]


def run_pip_audit(target: str) -> dict[str, Any]:
    cmd = shutil.which("pip-audit") or shutil.which("pip-audit.exe")
    if not cmd:
        return _tool_missing("pip-audit (pip install pip-audit)")
    req_file = _requirements_file(target)
    args = [cmd, "--format", "json", *(["-r", req_file] if req_file else [])]
    result = subprocess.run(args, capture_output=True, text=True, check=False)
    try:
        issues = _pip_audit_issues(result.stdout, req_file)
    except (json.JSONDecodeError, TypeError) as e:
        # An audit whose output did not parse did not run, and a check that
        # did not run is not a pass.
        return {"tool": "pip-audit", "status": "error", "issues": [],
                "note": f"pip-audit's output was not the JSON expected: {e}"}
    return {"tool": "pip-audit", "status": _status(issues), "issues": issues}
