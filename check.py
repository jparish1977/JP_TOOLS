#!/usr/bin/env python
"""
JP_TOOLS/check.py
Run code quality tools against arbitrary files or directories.
Outputs structured JSON suitable for AI agent consumption.

Usage:
    python check.py <path> [--lang python|js|auto] [--tools ruff,mypy] [--pretty]

Exit codes:
    0  — no errors (warnings OK)
    1  — one or more errors found
    2  — usage / tool-not-found error
"""

import sys
import os
import json
import shutil
import subprocess
import argparse
from pathlib import Path

# Inject known tool locations that may not be on PATH yet (e.g. before reboot)
_EXTRA_PATHS = [
    Path(os.environ.get("APPDATA", "")) / "npm",           # Node global bins
    Path("C:/Program Files/nodejs"),
    Path("C:/Users") / os.environ.get("USERNAME", "") / "AppData/Local/Programs/PHP/8.3.30/nts/x64",
]
os.environ["PATH"] = os.pathsep.join(
    [str(p) for p in _EXTRA_PATHS if p.exists()] + [os.environ.get("PATH", "")]
)


# ── tool runners ──────────────────────────────────────────────────────────────

def run_ruff(target: str) -> dict:
    if not shutil.which("ruff"):
        return _tool_missing("ruff")
    result = subprocess.run(
        ["ruff", "check", "--output-format", "json", target],
        capture_output=True, text=True,
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
            "fixable":  i.get("fix") is not None,
        }
        for i in raw
    ]
    return {"tool": "ruff", "status": _status(issues), "issues": issues}


def run_mypy(target: str) -> dict:
    if not shutil.which("mypy"):
        return _tool_missing("mypy")
    result = subprocess.run(
        ["mypy", "--show-error-codes", "--no-error-summary",
         "--ignore-missing-imports", target],
        capture_output=True, text=True,
    )
    issues = []
    for line in result.stdout.splitlines():
        parts = line.split(":", 3)
        if len(parts) < 3:
            continue
        try:
            line_num = int(parts[1].strip())
        except ValueError:
            continue
        rest = (parts[3] if len(parts) > 3 else parts[2]).strip()
        severity = "note"
        for sev in ("error", "warning", "note"):
            if f"{sev}:" in rest:
                severity = sev
                break
        rule, msg = "", rest
        if rest.endswith("]") and "[" in rest:
            b = rest.rfind("[")
            rule = rest[b + 1:-1]
            msg  = rest[:b].strip()
        for prefix in ("error: ", "warning: ", "note: "):
            if msg.startswith(prefix):
                msg = msg[len(prefix):]
                break
        if severity == "note":
            continue  # skip context notes, not actionable
        issues.append({
            "file":     parts[0].strip(),
            "line":     line_num,
            "col":      0,
            "severity": severity,
            "rule":     f"mypy:{rule}" if rule else "mypy",
            "message":  msg,
            "fixable":  False,
        })
    return {"tool": "mypy", "status": _status(issues, result.returncode), "issues": issues}


def run_eslint(target: str) -> dict:
    tools_dir = Path(__file__).parent
    runner    = tools_dir / "jp_eslint.mjs"
    node      = shutil.which("node") or shutil.which("node.exe")
    if not node:
        return _tool_missing("node (required for eslint)")
    if not runner.exists():
        return _tool_missing("jp_eslint.mjs")
    result = subprocess.run([node, str(runner), target], capture_output=True, text=True,
                            cwd=str(tools_dir))
    issues = []
    try:
        for file_result in json.loads(result.stdout or "[]"):
            for msg in file_result.get("messages", []):
                issues.append({
                    "file":     file_result.get("filePath", ""),
                    "line":     msg.get("line", 0),
                    "col":      msg.get("column", 0),
                    "severity": "error" if msg.get("severity") == 2 else "warning",
                    "rule":     msg.get("ruleId", ""),
                    "message":  msg.get("message", ""),
                    "fixable":  msg.get("fix") is not None,
                })
    except (json.JSONDecodeError, TypeError):
        if result.stderr:
            return {"tool": "eslint", "status": "error", "issues": [],
                    "note": result.stderr.strip()}
    return {"tool": "eslint", "status": _status(issues), "issues": issues}


def run_stylelint(target: str) -> dict:
    tools_dir = Path(__file__).parent
    runner    = tools_dir / "jp_stylelint.mjs"
    node      = shutil.which("node") or shutil.which("node.exe")
    if not node:
        return _tool_missing("node (required for stylelint)")
    if not runner.exists():
        return _tool_missing("jp_stylelint.mjs")
    result = subprocess.run([node, str(runner), target], capture_output=True, text=True,
                            cwd=str(tools_dir))
    issues = []
    try:
        for file_result in json.loads(result.stdout or "[]"):
            for w in file_result.get("warnings", []):
                issues.append({
                    "file":     file_result.get("source", target),
                    "line":     w.get("line", 0),
                    "col":      w.get("column", 0),
                    "severity": w.get("severity", "warning"),
                    "rule":     w.get("rule", ""),
                    "message":  w.get("text", ""),
                    "fixable":  False,
                })
    except (json.JSONDecodeError, TypeError):
        if result.stderr:
            return {"tool": "stylelint", "status": "error", "issues": [],
                    "note": result.stderr.strip()}
    return {"tool": "stylelint", "status": _status(issues), "issues": issues}


def _php_bin(name: str) -> str | None:
    """Resolve a Composer bin, falling back to global PATH."""
    tools_dir = Path(__file__).parent
    for ext in ("", ".bat", ".cmd"):
        local = tools_dir / "vendor" / "bin" / f"{name}{ext}"
        if local.exists():
            return str(local)
    return shutil.which(name) or shutil.which(f"{name}.bat")


def _php_cmd() -> str | None:
    return shutil.which("php") or shutil.which("php.exe")


def run_phpstan(target: str) -> dict:
    php  = _php_cmd()
    bin_ = _php_bin("phpstan")
    if not php:
        return _tool_missing("php")
    if not bin_:
        return _tool_missing("phpstan (run: composer install in JP_TOOLS)")
    cfg  = Path(__file__).parent / "configs" / "phpstan.neon"
    args = [php, bin_, "analyse", "--error-format=json", "--no-progress"]
    if cfg.exists():
        args += ["-c", str(cfg)]
    result = subprocess.run(args + [target], capture_output=True, text=True)
    issues = []
    try:
        data = json.loads(result.stdout)
        for fe in data.get("files", {}).values():
            for msg in fe.get("messages", []):
                issues.append({
                    "file":     msg.get("file", target),
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


def run_phpcs(target: str) -> dict:
    php  = _php_cmd()
    bin_ = _php_bin("phpcs")
    if not php:
        return _tool_missing("php")
    if not bin_:
        return _tool_missing("phpcs (run: composer install in JP_TOOLS)")
    cfg  = Path(__file__).parent / "configs" / "phpcs.xml"
    args = [php, bin_, "--report=json"]
    if cfg.exists():
        args += [f"--standard={cfg}"]
    result = subprocess.run(args + [target], capture_output=True, text=True)
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


def run_rector(target: str) -> dict:
    """Rector in dry-run mode — reports what would change without writing."""
    php  = _php_cmd()
    bin_ = _php_bin("rector")
    if not php:
        return _tool_missing("php")
    if not bin_:
        return _tool_missing("rector (run: composer install in JP_TOOLS)")
    cfg  = Path(__file__).parent / "configs" / "rector.php"
    args = [php, bin_, "process", "--dry-run", "--output-format=json", "--no-progress-bar"]
    if cfg.exists():
        args += [f"--config={cfg}"]
    result = subprocess.run(args + [target], capture_output=True, text=True)
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


def run_prettier(target: str) -> dict:
    cmd = shutil.which("prettier") or shutil.which("prettier.cmd")
    if not cmd:
        return _tool_missing("prettier")
    result = subprocess.run([cmd, "--check", target], capture_output=True, text=True)
    issues = []
    for line in (result.stdout + result.stderr).splitlines():
        line = line.strip()
        if line.startswith("[warn]"):
            fp = line[len("[warn]"):].strip()
            issues.append({
                "file":     fp,
                "line":     0,
                "col":      0,
                "severity": "warning",
                "rule":     "prettier/formatting",
                "message":  "File is not formatted correctly",
                "fixable":  True,
            })
    return {"tool": "prettier", "status": _status(issues, result.returncode), "issues": issues}


# ── security audit runners ────────────────────────────────────────────────────

def run_pip_audit(target: str) -> dict:
    cmd = shutil.which("pip-audit") or shutil.which("pip-audit.exe")
    if not cmd:
        return _tool_missing("pip-audit (pip install pip-audit)")
    # target could be a dir with requirements.txt or a single file
    p = Path(target)
    req_file = None
    if p.is_dir():
        for name in ("requirements.txt", "requirements-dev.txt", "requirements.lock"):
            candidate = p / name
            if candidate.exists():
                req_file = str(candidate)
                break
    elif p.suffix == ".txt":
        req_file = target
    args = [cmd, "--format", "json"]
    if req_file:
        args += ["-r", req_file]
    result = subprocess.run(args, capture_output=True, text=True)
    issues = []
    try:
        data = json.loads(result.stdout)
        for vuln in data.get("dependencies", []):
            for v in vuln.get("vulns", []):
                issues.append({
                    "file":     req_file or "(installed packages)",
                    "line":     0,
                    "col":      0,
                    "severity": "error",
                    "rule":     v.get("id", "CVE"),
                    "message":  f"{vuln.get('name')}=={vuln.get('version')}: {v.get('description', v.get('id', ''))}",
                    "fixable":  bool(v.get("fix_versions")),
                })
    except (json.JSONDecodeError, TypeError):
        pass
    return {"tool": "pip-audit", "status": _status(issues), "issues": issues}


def run_npm_audit(target: str) -> dict:
    cmd = shutil.which("npm") or shutil.which("npm.cmd")
    if not cmd:
        return _tool_missing("npm")
    p = Path(target)
    work_dir = str(p) if p.is_dir() else str(p.parent)
    pkg_json = Path(work_dir) / "package.json"
    if not pkg_json.exists():
        return {"tool": "npm-audit", "status": "skip", "issues": [],
                "note": "No package.json found"}
    result = subprocess.run([cmd, "audit", "--json"], capture_output=True, text=True,
                            cwd=work_dir)
    issues = []
    try:
        data = json.loads(result.stdout)
        for name, adv in data.get("vulnerabilities", {}).items():
            issues.append({
                "file":     "package.json",
                "line":     0,
                "col":      0,
                "severity": adv.get("severity", "error"),
                "rule":     f"npm-audit:{name}",
                "message":  f"{name}: {adv.get('title', adv.get('severity', 'vulnerability'))} (via {', '.join(adv.get('via', []) if isinstance(adv.get('via', [None])[0], str) else [v.get('title','?') for v in adv.get('via',[])])})",
                "fixable":  adv.get("fixAvailable", False) is not False,
            })
    except (json.JSONDecodeError, TypeError, IndexError):
        pass
    return {"tool": "npm-audit", "status": _status(issues), "issues": issues}


def run_composer_audit(target: str) -> dict:
    php = _php_cmd()
    composer = shutil.which("composer") or shutil.which("composer.bat")
    if not php and not composer:
        return _tool_missing("composer")
    p = Path(target)
    work_dir = str(p) if p.is_dir() else str(p.parent)
    composer_json = Path(work_dir) / "composer.json"
    if not composer_json.exists():
        return {"tool": "composer-audit", "status": "skip", "issues": [],
                "note": "No composer.json found"}
    if composer and not composer.endswith(".bat"):
        cmd = [composer, "audit", "--format=json"]
    elif php:
        cmd = [php, composer or "composer", "audit", "--format=json"]
    else:
        cmd = [composer, "audit", "--format=json"]
    result = subprocess.run(cmd, capture_output=True, text=True, cwd=work_dir)
    issues = []
    try:
        data = json.loads(result.stdout)
        for pkg, advisories in data.get("advisories", {}).items():
            for adv in advisories:
                issues.append({
                    "file":     "composer.json",
                    "line":     0,
                    "col":      0,
                    "severity": "error",
                    "rule":     adv.get("cve", adv.get("advisoryId", "advisory")),
                    "message":  f"{pkg}: {adv.get('title', 'security advisory')}",
                    "fixable":  False,
                })
    except (json.JSONDecodeError, TypeError):
        pass
    return {"tool": "composer-audit", "status": _status(issues), "issues": issues}


# ── helpers ───────────────────────────────────────────────────────────────────

def _status(issues: list, returncode: int = None) -> str:
    if returncode is not None and returncode not in (0, 1):
        return "error"
    return "fail" if issues else "pass"


def _tool_missing(name: str) -> dict:
    return {
        "tool":   name,
        "status": "unavailable",
        "issues": [],
        "note":   f"'{name}' not found on PATH — install it to enable this check",
    }


_EXT_TO_LANG = {
    ".py":   "python",
    ".js":   "js",  ".ts":  "js",  ".jsx": "js",  ".tsx": "js",
    ".mjs":  "js",  ".cjs": "js",
    ".css":  "css", ".scss": "css", ".less": "css",
    ".html": "html", ".htm": "html",
    ".php":  "php",
}

# Directories to skip when scanning
_SKIP_DIRS = {"node_modules", "vendor", "__pycache__", ".git", ".venv", "venv", "dist", "build"}


def _detect_lang(target: str) -> str:
    p = Path(target)
    if p.is_file():
        return _EXT_TO_LANG.get(p.suffix.lower(), "unknown")
    return "unknown"


def _collect_files(directory: str) -> dict[str, list[str]]:
    """Scan a directory and group files by language. Returns {lang: [filepaths]}."""
    groups: dict[str, list[str]] = {}
    for root, dirs, files in os.walk(directory):
        dirs[:] = [d for d in dirs if d not in _SKIP_DIRS]
        for f in files:
            lang = _EXT_TO_LANG.get(Path(f).suffix.lower())
            if lang:
                groups.setdefault(lang, []).append(str(Path(root) / f))
    return groups


TOOL_RUNNERS = {
    "ruff":           run_ruff,
    "mypy":           run_mypy,
    "eslint":         run_eslint,
    "stylelint":      run_stylelint,
    "prettier":       run_prettier,
    "phpstan":        run_phpstan,
    "phpcs":          run_phpcs,
    "rector":         run_rector,
    "pip-audit":      run_pip_audit,
    "npm-audit":      run_npm_audit,
    "composer-audit": run_composer_audit,
}

DEFAULT_TOOLS = {
    "python": ["ruff", "mypy"],
    "js":     ["eslint", "prettier"],
    "css":    ["stylelint", "prettier"],
    "html":   ["eslint", "stylelint", "prettier"],
    "php":    ["phpstan", "phpcs", "rector"],
}

AUDIT_TOOLS = {
    "python": ["pip-audit"],
    "js":     ["npm-audit"],
    "php":    ["composer-audit"],
}

# Tools that accept directories natively (pass the dir, not individual files)
_DIR_CAPABLE = {"ruff", "mypy", "phpstan", "phpcs", "rector",
                "pip-audit", "npm-audit", "composer-audit"}


def _run_tools(tool_names: list[str], target: str) -> list[dict]:
    checks = []
    for name in tool_names:
        runner = TOOL_RUNNERS.get(name)
        if runner:
            checks.append(runner(target))
        else:
            checks.append({"tool": name, "status": "unknown",
                           "issues": [], "note": f"No runner for '{name}'"})
    return checks


def _summarize(checks: list[dict]) -> dict:
    all_issues = [i for c in checks for i in c.get("issues", [])]
    errors   = sum(1 for i in all_issues if i["severity"] == "error")
    warnings = sum(1 for i in all_issues if i["severity"] == "warning")
    return {
        "total":    len(all_issues),
        "errors":   errors,
        "warnings": warnings,
        "fixable":  sum(1 for i in all_issues if i.get("fixable")),
    }


# ── main ──────────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(
        description="Run code quality tools and output structured JSON.",
    )
    parser.add_argument("target", help="File or directory to check")
    parser.add_argument("--lang",  choices=["python", "js", "css", "html", "php", "auto"],
                        default="auto",
                        help="Language override (default: auto-detect)")
    parser.add_argument("--tools", metavar="TOOLS",
                        help="Comma-separated tools to run, e.g. ruff,mypy")
    parser.add_argument("--pretty", action="store_true",
                        help="Pretty-print JSON output")
    parser.add_argument("--audit", action="store_true",
                        help="Also run security audit tools (pip-audit, npm audit, composer audit)")
    args = parser.parse_args()

    target = str(Path(args.target).resolve())
    if not Path(target).exists():
        print(json.dumps({"error": f"Path not found: {target}"}))
        sys.exit(2)

    is_dir = Path(target).is_dir()

    # ── Single file or explicit --lang / --tools ──────────────────────────
    if not is_dir or args.lang != "auto" or args.tools:
        lang = args.lang if args.lang != "auto" else _detect_lang(target)
        if args.tools:
            tool_names = [t.strip() for t in args.tools.split(",")]
        elif lang in DEFAULT_TOOLS:
            tool_names = list(DEFAULT_TOOLS[lang])
        else:
            print(json.dumps({"error": f"Cannot detect language for: {target}"}))
            sys.exit(2)
        if args.audit:
            tool_names.extend(AUDIT_TOOLS.get(lang, []))

        checks = _run_tools(tool_names, target)
        output = {
            "target":   target,
            "language": lang,
            "checks":   checks,
            "summary":  _summarize(checks),
        }
        print(json.dumps(output, indent=2 if args.pretty else None))
        sys.exit(1 if output["summary"]["errors"] > 0 else 0)

    # ── Directory: scan, group by language, run appropriate tools ─────────
    groups = _collect_files(target)
    if not groups:
        print(json.dumps({"error": f"No recognized source files in: {target}"}))
        sys.exit(2)

    all_checks = []
    lang_sections = []

    for lang, files in sorted(groups.items()):
        tool_names = list(DEFAULT_TOOLS.get(lang, []))
        if args.audit:
            tool_names.extend(AUDIT_TOOLS.get(lang, []))
        if not tool_names:
            continue

        lang_checks = []
        for name in tool_names:
            runner = TOOL_RUNNERS.get(name)
            if not runner:
                continue
            if name in _DIR_CAPABLE:
                # Run once against the whole dir — tool handles file discovery
                lang_checks.append(runner(target))
            else:
                # Run per-file, merge issues into one result per tool
                merged_issues = []
                any_fail = False
                for fp in files:
                    result = runner(fp)
                    merged_issues.extend(result.get("issues", []))
                    if result["status"] == "fail":
                        any_fail = True
                lang_checks.append({
                    "tool":   name,
                    "status": "fail" if any_fail else "pass",
                    "issues": merged_issues,
                })

        all_checks.extend(lang_checks)
        lang_sections.append({
            "language":   lang,
            "file_count": len(files),
            "tools":      [c["tool"] for c in lang_checks],
        })

    output = {
        "target":     target,
        "mode":       "multi-language",
        "languages":  lang_sections,
        "checks":     all_checks,
        "summary":    _summarize(all_checks),
    }

    print(json.dumps(output, indent=2 if args.pretty else None))
    sys.exit(1 if output["summary"]["errors"] > 0 else 0)


if __name__ == "__main__":
    main()
