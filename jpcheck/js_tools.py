"""Runners for the JS and CSS tools: eslint, stylelint, prettier, npm audit."""

import json
import os
import re
import shutil
import subprocess
from pathlib import Path
from typing import Any

from jpcheck.common import (
    TOOLS_ROOT,
    _node_bin,
    _node_runner_failed,
    _plain_env,
    _status,
    _tool_missing,
    _tool_result,
    node_module_roots,
    strip_ansi,
)

_ESLINT_DISABLE = re.compile(r"eslint-disable(?:-next-line|-line)?\s+([^\s*]+)")

# What a missing JS tool tells the reader to run: package.json declares them.
NPM_INSTALL = "run: npm install in JP_TOOLS"

# Codec names fold case, so "utf-8" and "UTF-8" name the same codec.
_UTF8 = "utf-8"


def _eslint_source_files(target: str) -> list[Path]:
    """The file itself, or every .js/.mjs/.html under a directory outside vendored code."""
    p = Path(target)
    if p.is_file():
        return [p]
    return [
        Path(root) / f
        for root, _dirs, filenames in os.walk(str(p))
        for f in filenames
        if f.endswith((".js", ".mjs", ".html"))
        and "node_modules" not in root and "vendor" not in root
    ]


def _suppressions_in(fp: Path) -> list[dict[str, Any]]:
    """Each eslint-disable comment in one file, as a warning naming its rule and reason."""
    try:
        text = fp.read_text(encoding=_UTF8, errors="replace")
    except OSError:
        # reason: a source file that cannot be read carries no acknowledged
        # suppressions to list; the linter that reads it reports the read
        return []
    found = []
    for i, line in enumerate(text.splitlines(), 1):
        m = _ESLINT_DISABLE.search(line)
        if not m:
            continue
        reason = line.split("--", 1)[1].strip().rstrip("*/").strip() if "--" in line else ""
        found.append({
            "file":     str(fp),
            "line":     i,
            "col":      0,
            "severity": "warning",
            "rule":     f"suppressed:{m.group(1)}",
            "message":  f"Acknowledged suppression: {m.group(1)}" + (f" ({reason})" if reason else ""),
            "fixable":  False,
        })
    return found


def _count_eslint_suppressions(target: str) -> list[dict[str, Any]]:
    """eslint-disable comments are acknowledged, not invisible: list each one."""
    return [s for fp in _eslint_source_files(target) for s in _suppressions_in(fp)]


def _run_node_runner(tool: str, script: str,
                     target: str) -> subprocess.CompletedProcess[str] | dict[str, Any]:
    """Run one of JP_TOOLS' node runners on TARGET.

    Returns the process when the runner linted, or the finished report when it
    could not: node missing, the runner missing, or the runner failing to lint.
    """
    runner = TOOLS_ROOT / script
    node = shutil.which("node") or shutil.which("node.exe")
    if not node:
        return _tool_missing(tool, "needs node, which is not on PATH")
    if not runner.exists():
        return _tool_missing(tool, f"{script} is missing from this checkout")
    # The runner imports the package of the same name. With it in no tool
    # root's node_modules, node would fail with a raw module error; say what
    # is missing and how to get it, as the PHP tools do.
    if not any((modules / tool).is_dir() for modules in node_module_roots()):
        return _tool_missing(tool, NPM_INSTALL)
    result = subprocess.run([node, *_resolve_args(), str(runner), target], capture_output=True,
                            text=True, check=False, cwd=str(TOOLS_ROOT), env=_runner_env())
    return _node_runner_failed(tool, result) or result


def _resolve_args() -> list[str]:
    """`--import jp_node_resolve.mjs` when this checkout has no node_modules of its own.

    The runners' bare imports resolve from the runner's own directory, so in a
    worktree they fail although the checkout it belongs to has the packages.
    The hook retries them from each tool root's node_modules (jpcheck.common).
    """
    if (TOOLS_ROOT / "node_modules").is_dir() or not node_module_roots():
        return []
    return ["--import", (TOOLS_ROOT / "jp_node_resolve.mjs").as_uri()]


def _runner_env() -> dict[str, str]:
    return {**os.environ, "JP_TOOLS_NODE_ROOTS": os.pathsep.join(str(p) for p in node_module_roots())}


def _eslint_issues(stdout: str) -> list[dict[str, Any]]:
    return [
        {
            "file":     file_result.get("filePath", ""),
            "line":     msg.get("line", 0),
            "col":      msg.get("column", 0),
            "severity": "error" if msg.get("severity") == 2 else "warning",
            "rule":     msg.get("ruleId", ""),
            "message":  msg.get("message", ""),
            "fixable":  msg.get("fix") is not None,
        }
        for file_result in json.loads(stdout or "[]")
        for msg in file_result.get("messages", [])
    ]


def run_eslint(target: str) -> dict[str, Any]:
    result = _run_node_runner("eslint", "jp_eslint.mjs", target)
    if isinstance(result, dict):
        return result
    try:
        issues = _eslint_issues(result.stdout)
    except (json.JSONDecodeError, TypeError):
        if result.stderr:
            return {"tool": "eslint", "status": "error", "issues": [],
                    "note": result.stderr.strip()}
        issues = []
    # Acknowledged suppressions are listed as warnings so they stay visible.
    issues.extend(_count_eslint_suppressions(target))
    return {"tool": "eslint", "status": _status(issues), "issues": issues}


def _stylelint_issues(stdout: str, target: str) -> list[dict[str, Any]]:
    return [
        {
            "file":     file_result.get("source", target),
            "line":     w.get("line", 0),
            "col":      w.get("column", 0),
            "severity": w.get("severity", "warning"),
            "rule":     w.get("rule", ""),
            "message":  w.get("text", ""),
            "fixable":  False,
        }
        for file_result in json.loads(stdout or "[]")
        for w in file_result.get("warnings", [])
    ]


def run_stylelint(target: str) -> dict[str, Any]:
    result = _run_node_runner("stylelint", "jp_stylelint.mjs", target)
    if isinstance(result, dict):
        return result
    try:
        issues = _stylelint_issues(result.stdout, target)
    except (json.JSONDecodeError, TypeError):
        if result.stderr:
            return {"tool": "stylelint", "status": "error", "issues": [],
                    "note": result.stderr.strip()}
        issues = []
    return {"tool": "stylelint", "status": _status(issues), "issues": issues}


def run_prettier(target: str) -> dict[str, Any]:
    cmd = _node_bin("prettier")
    if not cmd:
        return _tool_missing("prettier", NPM_INSTALL)
    result = subprocess.run([cmd, "--check", target], capture_output=True, text=True, check=False, env=_plain_env())
    issues = []
    for line in (strip_ansi(result.stdout + result.stderr)).splitlines():
        line = line.strip()
        if line.startswith("[warn]"):
            fp = line[len("[warn]"):].strip()
            # prettier ends --check with a summary on its own [warn] line:
            # "Code style issues found in the above file. Run Prettier with
            # --write to fix." (prettier 2: "... Forgot to run Prettier?").
            # Read as a filename, it would count every unformatted file twice.
            if fp.startswith("Code style issues"):
                continue
            issues.append({
                "file":     fp,
                "line":     0,
                "col":      0,
                "severity": "warning",
                "rule":     "prettier/formatting",
                "message":  "File is not formatted correctly",
                "fixable":  True,
            })
    return _tool_result("prettier", issues, result)


def run_npm_audit(target: str) -> dict[str, Any]:
    cmd = shutil.which("npm") or shutil.which("npm.cmd")
    if not cmd:
        return _tool_missing("npm")
    p = Path(target)
    work_dir = str(p) if p.is_dir() else str(p.parent)
    pkg_json = Path(work_dir) / "package.json"
    if not pkg_json.exists():
        return {"tool": "npm-audit", "status": "skip", "issues": [],
                "note": "No package.json found"}
    result = subprocess.run([cmd, "audit", "--json"], capture_output=True, text=True, check=False,
                            cwd=work_dir)
    issues = []
    try:
        data = json.loads(result.stdout)
        for name, adv in data.get("vulnerabilities", {}).items():
            issues.append({
                "file":     "package.json",
                "line":     0,
                "col":      0,
                # An error, as pip-audit's and composer's are; npm's own level
                # (low to critical) goes in the message, since the summary
                # counts only "error" toward the exit code.
                "severity": "error",
                "rule":     f"npm-audit:{name}",
                "message":  f"{name} [{adv.get('severity', 'unrated')}]: {adv.get('title', adv.get('severity', 'vulnerability'))} (via {', '.join(adv.get('via', []) if isinstance(adv.get('via', [None])[0], str) else [v.get('title','?') for v in adv.get('via',[])])})",
                "fixable":  adv.get("fixAvailable", False) is not False,
            })
    except (json.JSONDecodeError, TypeError, IndexError) as e:
        # As pip-audit: not parsed is not run, and not run is not a pass.
        return {"tool": "npm-audit", "status": "error", "issues": [],
                "note": f"npm audit's output was not the JSON expected: {e}"}
    return {"tool": "npm-audit", "status": _status(issues), "issues": issues}
