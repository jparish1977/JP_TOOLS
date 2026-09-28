"""Runners for the PHP tools: phpstan, phpcs, rector, composer audit."""

import json
import shutil
import subprocess
from pathlib import Path
from typing import Any

from jpcheck.common import (
    TOOLS_ROOT,
    _find_project_config,
    _php_bin,
    _php_cmd,
    _status,
    _tool_missing,
)


def run_phpstan(target: str) -> dict[str, Any]:
    php  = _php_cmd()
    bin_ = _php_bin("phpstan")
    if not php:
        return _tool_missing("php")
    if not bin_:
        return _tool_missing("phpstan (run: composer install in JP_TOOLS)")
    cfg = _find_project_config(target, ["phpstan.neon", "phpstan.neon.dist"])
    if cfg is None:
        cfg = TOOLS_ROOT / "configs" / "phpstan.neon"
    args = [php, bin_, "analyse", "--error-format=json", "--no-progress"]
    if cfg.exists():
        args += ["-c", str(cfg)]
    result = subprocess.run([*args, target], capture_output=True, text=True, check=False)
    issues = []
    try:
        data = json.loads(result.stdout)
        # .items(), not .values(): phpstan keys this dict BY FILENAME and its
        # message objects carry no "file" field, so iterating values discarded
        # the only copy of the path and every finding fell back to `target`.
        # A whole run then reported "<repo dir>:214" and named nothing.
        for path, fe in data.get("files", {}).items():
            for msg in fe.get("messages", []):
                issues.append({
                    "file":     msg.get("file", path),
                    "line":     msg.get("line", 0),
                    "col":      0,
                    "severity": "error",
                    "rule":     "phpstan",
                    "message":  msg.get("message", ""),
                    "fixable":  False,
                })
    except (json.JSONDecodeError, TypeError):
        if result.stderr:
            return {"tool": "phpstan", "status": "error", "issues": [],
                    "note": result.stderr.strip()}
    return {"tool": "phpstan", "status": _status(issues), "issues": issues}


def run_phpcs(target: str) -> dict[str, Any]:
    php  = _php_cmd()
    bin_ = _php_bin("phpcs")
    if not php:
        return _tool_missing("php")
    if not bin_:
        return _tool_missing("phpcs (run: composer install in JP_TOOLS)")
    cfg = _find_project_config(target, ["phpcs.xml", "phpcs.xml.dist", ".phpcs.xml", ".phpcs.xml.dist"])
    if cfg is None:
        cfg = TOOLS_ROOT / "configs" / "phpcs.xml"
    args = [php, bin_, "--report=json"]
    if cfg.exists():
        args += [f"--standard={cfg}"]
    result = subprocess.run([*args, target], capture_output=True, text=True, check=False)
    issues = []
    try:
        data = json.loads(result.stdout)
        for fp, fdata in data.get("files", {}).items():
            for msg in fdata.get("messages", []):
                issues.append({
                    "file":     fp,
                    "line":     msg.get("line", 0),
                    "col":      msg.get("column", 0),
                    "severity": msg.get("type", "ERROR").lower(),
                    "rule":     msg.get("source", "phpcs"),
                    "message":  msg.get("message", ""),
                    "fixable":  msg.get("fixable", False),
                })
    except (json.JSONDecodeError, TypeError):
        if result.stderr:
            return {"tool": "phpcs", "status": "error", "issues": [],
                    "note": result.stderr.strip()}
    return {"tool": "phpcs", "status": _status(issues), "issues": issues}


def run_rector(target: str) -> dict[str, Any]:
    """Rector in dry-run mode: reports what would change without writing."""
    php  = _php_cmd()
    bin_ = _php_bin("rector")
    if not php:
        return _tool_missing("php")
    if not bin_:
        return _tool_missing("rector (run: composer install in JP_TOOLS)")
    cfg = _find_project_config(target, ["rector.php", "rector.php.dist"])
    if cfg is None:
        cfg = TOOLS_ROOT / "configs" / "rector.php"
    args = [php, bin_, "process", "--dry-run", "--output-format=json", "--no-progress-bar"]
    if cfg.exists():
        args += [f"--config={cfg}"]
    result = subprocess.run([*args, target], capture_output=True, text=True, check=False)
    issues = []
    try:
        data = json.loads(result.stdout)
        for fd in data.get("file_diffs", []):
            rectors = fd.get("applied_rectors", [])
            issues.append({
                "file":     fd.get("file", target),
                "line":     0,
                "col":      0,
                "severity": "warning",
                "rule":     ", ".join(r.rsplit("\\", 1)[-1] for r in rectors) or "rector",
                "message":  f"Rector would apply {len(rectors)} rule(s)",
                "fixable":  True,
            })
    except (json.JSONDecodeError, TypeError):
        if result.stderr:
            return {"tool": "rector", "status": "error", "issues": [],
                    "note": result.stderr.strip()}
    return {"tool": "rector", "status": _status(issues), "issues": issues}


def _composer_cmd(php: str | None, composer: str | None) -> list[str] | None:
    """How to invoke `composer audit`: directly, or through php for a .bat shim; None if neither can."""
    if composer and not composer.endswith(".bat"):
        return [composer, "audit", "--format=json"]
    if php:
        return [php, composer or "composer", "audit", "--format=json"]
    if composer:
        return [composer, "audit", "--format=json"]
    return None


def _composer_issues(stdout: str) -> list[dict[str, Any]]:
    return [
        {
            "file":     "composer.json",
            "line":     0,
            "col":      0,
            "severity": "error",
            "rule":     adv.get("cve", adv.get("advisoryId", "advisory")),
            "message":  f"{pkg}: {adv.get('title', 'security advisory')}",
            "fixable":  False,
        }
        for pkg, advisories in json.loads(stdout).get("advisories", {}).items()
        for adv in advisories
    ]


def run_composer_audit(target: str) -> dict[str, Any]:
    php = _php_cmd()
    composer = shutil.which("composer") or shutil.which("composer.bat")
    if not php and not composer:
        return _tool_missing("composer")
    p = Path(target)
    work_dir = str(p) if p.is_dir() else str(p.parent)
    if not (Path(work_dir) / "composer.json").exists():
        return {"tool": "composer-audit", "status": "skip", "issues": [],
                "note": "No composer.json found"}
    cmd = _composer_cmd(php, composer)
    if cmd is None:
        return _tool_missing("composer (and no php to run it with)")
    result = subprocess.run(cmd, capture_output=True, text=True, check=False, cwd=work_dir)
    try:
        issues = _composer_issues(result.stdout)
    except (json.JSONDecodeError, TypeError) as e:
        # As pip-audit: not parsed is not run, and not run is not a pass.
        return {"tool": "composer-audit", "status": "error", "issues": [],
                "note": f"composer audit's output was not the JSON expected: {e}"}
    return {"tool": "composer-audit", "status": _status(issues), "issues": issues}
