"""Runners for the Python tools: ruff, mypy, pip-audit."""

import json
import os
import re
import shutil
import subprocess
from pathlib import Path
from typing import Any

from jpcheck.common import (
    TOOLS_ROOT,
    _declares_section,
    _find_project_config,
    _plain_env,
    _status,
    _tool_missing,
    _tool_result,
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


# What a project's ruff.toml extends JP_TOOLS' config through:
#
#     extend = "${JP_TOOLS_ROOT:-../JP_TOOLS}/configs/ruff.toml"
#
# ruff expands variables in `extend`. Every ruff run here sets this one to the
# JP_TOOLS doing the checking, so the path resolves from a git worktree of the
# project, where nothing sits at ../JP_TOOLS. With it unset, as in a plain
# `ruff` run, the default keeps the checkout-beside layout working.
JP_TOOLS_ROOT_VAR = "JP_TOOLS_ROOT"


def ruff_env() -> dict[str, str]:
    """The environment ruff runs in: this one, with JP_TOOLS_ROOT naming this checkout."""
    return {**os.environ, JP_TOOLS_ROOT_VAR: str(TOOLS_ROOT.resolve())}


# What a missing Python runner tells the reader to run: the pins live there.
PIP_INSTALL = "pip install -r requirements-dev.txt"


def run_ruff(target: str) -> dict[str, Any]:
    if not shutil.which("ruff"):
        return _tool_missing("ruff", PIP_INSTALL)
    result = subprocess.run(
        ["ruff", "check", *_ruff_config_args(target),
         "--output-format", "json", target],
        capture_output=True, text=True, check=False, env=ruff_env(),
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
    return _tool_result("ruff", issues, result)


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

    The project's config is passed by path rather than left to mypy to find.
    mypy looks for it from its working directory, not from the file it
    checks, so a config in a subdirectory would be found here and missed by
    a mypy run from the repository root.
    """
    project = _find_project_config(target, ["mypy.ini", ".mypy.ini", "setup.cfg",
                                            "pyproject.toml"],
                                   {"setup.cfg": "mypy", "pyproject.toml": "tool.mypy"},
                                   stop_at_repo_root=True)
    if project:
        return ["--config-file", str(project)]
    shared = TOOLS_ROOT / "configs" / "mypy.ini"
    return ["--config-file", str(shared)] if shared.exists() else []


def run_mypy(target: str) -> dict[str, Any]:
    if not shutil.which("mypy"):
        return _tool_missing("mypy", PIP_INSTALL)
    result = subprocess.run(
        # --show-traceback: a crash then ends on its exception, which the
        # note carries, rather than on a hint to rerun with this flag.
        # --follow-imports=silent: imported modules are still analysed, so
        # their types resolve, but only TARGET's own errors are reported.
        # Followed normally, a file's check carried every error in every
        # module it imports, and a per-file baseline counted an imported
        # module's errors once more for each file importing it.
        ["mypy", "--show-error-codes", "--no-error-summary", "--show-traceback",
         "--ignore-missing-imports", "--follow-imports=silent",
         *_mypy_config_args(target), target],
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
    return _tool_result("mypy", issues, result)


# ── security audit runners ────────────────────────────────────────────────────

_REQUIREMENTS = ("requirements.txt", "requirements-dev.txt", "requirements.lock")


def _audit_sources(target: str) -> list[tuple[list[str], str]] | str:
    """What pip-audit audits for TARGET, one run each as (its arguments, the
    file its findings name), or why nothing is: every requirements file there,
    else a pyproject.toml with a [project] table (`pip-audit DIR` resolves its
    dependencies). One run per file, so each finding names the file whose pin
    it is.

    Never pip-audit with neither. It then audits the environment it runs in,
    which is the gate's own, and reports those packages as the project's.
    """
    p = Path(target)
    if not p.is_dir():
        if p.suffix == ".txt":
            return [(["-r", target], target)]
        return "not a requirements file, so no Python dependencies to audit"
    requirements = [str(p / name) for name in _REQUIREMENTS if (p / name).exists()]
    if requirements:
        return [(["-r", f], f) for f in requirements]
    pyproject = p / "pyproject.toml"
    if pyproject.exists() and _declares_section(pyproject, "project"):
        return [([str(p)], str(pyproject))]
    if (p / "setup.py").exists():
        return ("setup.py declares the dependencies, and pip-audit cannot read one; "
                "a requirements file would let it")
    return "No Python requirements found (requirements*.txt, or pyproject.toml with [project])"


def _pip_audit_issues(stdout: str, source: str) -> list[dict[str, Any]]:
    return [
        {
            "file":     source,
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
        return _tool_missing("pip-audit", PIP_INSTALL)
    sources = _audit_sources(target)
    if isinstance(sources, str):
        return {"tool": "pip-audit", "status": "skip", "issues": [], "note": sources}
    issues: list[dict[str, Any]] = []
    for audit_args, named in sources:
        result = subprocess.run([cmd, "--format", "json", *audit_args],
                                capture_output=True, text=True, check=False)
        try:
            issues += _pip_audit_issues(result.stdout, named)
        except (json.JSONDecodeError, TypeError) as e:
            # An audit whose output did not parse did not run, and a check
            # that did not run is not a pass. pip-audit says why on stderr.
            said = result.stderr.strip().splitlines()[-1:] or [str(e)]
            return {"tool": "pip-audit", "status": "error", "issues": [],
                    "note": f"pip-audit on {named}: output was not the JSON expected: {said[0][:300]}"}
    return {"tool": "pip-audit", "status": _status(issues), "issues": issues}
