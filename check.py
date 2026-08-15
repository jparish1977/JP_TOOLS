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

import argparse
import json
import os
import re
import shutil
import subprocess
import sys
import time
from pathlib import Path
from typing import Any

# Inject known tool locations that may not be on PATH yet (e.g. before reboot)
_EXTRA_PATHS = [
    Path(os.environ.get("APPDATA", "")) / "npm",           # Node global bins
    Path("C:/Program Files/nodejs"),
    Path("C:/Users") / os.environ.get("USERNAME", "") / "AppData/Local/Programs/PHP/8.3.30/nts/x64",
]
os.environ["PATH"] = os.pathsep.join(
    [str(p) for p in _EXTRA_PATHS if p.exists()] + [os.environ.get("PATH", "")]
)

# Force UTF-8 for subprocess output on Windows (avoids cp1252 decode errors)
os.environ["PYTHONUTF8"] = "1"


# ── tool runners ──────────────────────────────────────────────────────────────

def _ruff_config_args(target: str) -> list[str]:
    """Point ruff at the JP_TOOLS defaults, unless the project has its own.

    ruff already implements precedence when it discovers a config itself, so
    passing --config on top of a project's own file would override the project
    rather than defer to it. Supply the default only when there is nothing to
    discover. Note a bare pyproject.toml counts: --config would fail on one
    with no [tool.ruff] section, and ruff handles that case correctly alone.
    """
    if _find_project_config(target, ["ruff.toml", ".ruff.toml", "pyproject.toml"]):
        return []
    shared = Path(__file__).parent / "configs" / "ruff.toml"
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
            # is `ruff check --fix`: safe fixes only. Counting every finding
            # that carries a fix object overstated it badly -- on recover.py,
            # 14 of 18 carried one while only 2 were safe, the rest being 3
            # unsafe and 9 displayonly, which are never auto-applied at all.
            "fixable":  (i.get("fix") or {}).get("applicability") == "safe",
            "fixable_unsafe":
                (i.get("fix") or {}).get("applicability") == "unsafe",
        }
        for i in raw
    ]
    return {"tool": "ruff", "status": _status(issues), "issues": issues}


# mypy prints "file:line: severity: message [code]", optionally with a column,
# and on Windows the path carries a drive letter. The previous parser split on
# ":" with maxsplit=3, which put the severity in one field and then searched a
# different field for it. It therefore classified EVERY error as a note and
# dropped it: check.py reported "mypy pass" on a file mypy was failing.
# Verified 2026-08-12 against a deliberate type error.
_MYPY_LINE = re.compile(
    r"^(?P<file>(?:[A-Za-z]:)?[^:]+):(?P<line>\d+):(?:(?P<col>\d+):)?\s*"
    r"(?P<sev>error|warning|note):\s*(?P<msg>.*)$"
)


def _mypy_config_args(target: str) -> list[str]:
    """Point mypy at the JP_TOOLS defaults, unless the project has its own.

    Nothing passed this before, so configs/mypy.ini was dead file and the gate
    ran on mypy's defaults while README described the file and METHODOLOGY
    described something stricter than either. Project-local config wins, same
    precedence as _ruff_config_args.
    """
    if _find_project_config(target, ["mypy.ini", ".mypy.ini", "setup.cfg",
                                     "pyproject.toml"]):
        return []
    shared = Path(__file__).parent / "configs" / "mypy.ini"
    return ["--config-file", str(shared)] if shared.exists() else []


def run_mypy(target: str) -> dict[str, Any]:
    if not shutil.which("mypy"):
        return _tool_missing("mypy")
    result = subprocess.run(
        ["mypy", "--show-error-codes", "--no-error-summary",
         "--ignore-missing-imports", *_mypy_config_args(target), target],
        capture_output=True, text=True, check=False,
    )
    issues = []
    for line in result.stdout.splitlines():
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


def _count_eslint_suppressions(target: str) -> list[dict[str, Any]]:
    """Scan source files for eslint-disable comments — these are acknowledged but not invisible."""
    import re
    suppressions = []
    p = Path(target)
    files = [p] if p.is_file() else [
        Path(root) / f
        for root, dirs, filenames in os.walk(str(p))
        for f in filenames
        if f.endswith((".js", ".mjs", ".html"))
        and "node_modules" not in root and "vendor" not in root
    ]
    pattern = re.compile(r"eslint-disable(?:-next-line|-line)?\s+([^\s*]+)")
    for fp in files:
        try:
            for i, line in enumerate(fp.read_text(encoding="utf-8", errors="replace").splitlines(), 1):
                m = pattern.search(line)
                if m:
                    reason = ""
                    if "--" in line:
                        reason = line.split("--", 1)[1].strip().rstrip("*/").strip()
                    suppressions.append({
                        "file":     str(fp),
                        "line":     i,
                        "col":      0,
                        "severity": "warning",
                        "rule":     f"suppressed:{m.group(1)}",
                        "message":  f"Acknowledged suppression: {m.group(1)}" + (f" ({reason})" if reason else ""),
                        "fixable":  False,
                    })
        except OSError:
            pass
    return suppressions


def run_eslint(target: str) -> dict[str, Any]:
    tools_dir = Path(__file__).parent
    runner    = tools_dir / "jp_eslint.mjs"
    node      = shutil.which("node") or shutil.which("node.exe")
    if not node:
        return _tool_missing("node (required for eslint)")
    if not runner.exists():
        return _tool_missing("jp_eslint.mjs")
    result = subprocess.run([node, str(runner), target], capture_output=True, text=True, check=False,
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
    # Add acknowledged suppressions as warnings so they stay visible in reports
    suppressions = _count_eslint_suppressions(target)
    issues.extend(suppressions)
    return {"tool": "eslint", "status": _status(issues), "issues": issues}


def run_stylelint(target: str) -> dict[str, Any]:
    tools_dir = Path(__file__).parent
    runner    = tools_dir / "jp_stylelint.mjs"
    node      = shutil.which("node") or shutil.which("node.exe")
    if not node:
        return _tool_missing("node (required for stylelint)")
    if not runner.exists():
        return _tool_missing("jp_stylelint.mjs")
    result = subprocess.run([node, str(runner), target], capture_output=True, text=True, check=False,
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


def _find_project_config(target: str, filenames: list[str]) -> Path | None:
    """Walk up from target looking for a project-local config file."""
    p = Path(target).resolve()
    start = p if p.is_dir() else p.parent
    for directory in (start, *start.parents):
        for name in filenames:
            candidate = directory / name
            if candidate.exists():
                return candidate
    return None


def run_phpstan(target: str) -> dict[str, Any]:
    php  = _php_cmd()
    bin_ = _php_bin("phpstan")
    if not php:
        return _tool_missing("php")
    if not bin_:
        return _tool_missing("phpstan (run: composer install in JP_TOOLS)")
    cfg = _find_project_config(target, ["phpstan.neon", "phpstan.neon.dist"])
    if cfg is None:
        cfg = Path(__file__).parent / "configs" / "phpstan.neon"
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
        cfg = Path(__file__).parent / "configs" / "phpcs.xml"
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
    """Rector in dry-run mode — reports what would change without writing."""
    php  = _php_cmd()
    bin_ = _php_bin("rector")
    if not php:
        return _tool_missing("php")
    if not bin_:
        return _tool_missing("rector (run: composer install in JP_TOOLS)")
    cfg = _find_project_config(target, ["rector.php", "rector.php.dist"])
    if cfg is None:
        cfg = Path(__file__).parent / "configs" / "rector.php"
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


def run_prettier(target: str) -> dict[str, Any]:
    cmd = shutil.which("prettier") or shutil.which("prettier.cmd")
    if not cmd:
        return _tool_missing("prettier")
    result = subprocess.run([cmd, "--check", target], capture_output=True, text=True, check=False)
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

def run_pip_audit(target: str) -> dict[str, Any]:
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
    result = subprocess.run(args, capture_output=True, text=True, check=False)
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
                "severity": adv.get("severity", "error"),
                "rule":     f"npm-audit:{name}",
                "message":  f"{name}: {adv.get('title', adv.get('severity', 'vulnerability'))} (via {', '.join(adv.get('via', []) if isinstance(adv.get('via', [None])[0], str) else [v.get('title','?') for v in adv.get('via',[])])})",
                "fixable":  adv.get("fixAvailable", False) is not False,
            })
    except (json.JSONDecodeError, TypeError, IndexError):
        pass
    return {"tool": "npm-audit", "status": _status(issues), "issues": issues}


def run_composer_audit(target: str) -> dict[str, Any]:
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
    elif composer:
        cmd = [composer, "audit", "--format=json"]
    else:
        # Neither a usable composer nor php. The old final branch put `composer`
        # in the list regardless, and it is None on exactly this path, so
        # subprocess.run raised TypeError instead of reporting a missing tool.
        return _tool_missing("composer (and no php to run it with)")
    result = subprocess.run(cmd, capture_output=True, text=True, check=False, cwd=work_dir)
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

def _status(issues: list[dict[str, Any]], returncode: int | None = None) -> str:
    if returncode is not None and returncode not in (0, 1):
        return "error"
    return "fail" if issues else "pass"


def _tool_missing(name: str) -> dict[str, Any]:
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
    # C/C++ and Arduino. .ino is a C++ sketch; the extension is the only thing
    # that differs, and it was the gap that made this toolbox look inapplicable
    # to an embedded project.
    ".c":    "cpp", ".h":   "cpp", ".cpp": "cpp",
    ".hpp":  "cpp", ".cc":  "cpp", ".ino": "cpp",
}

# Directories to skip when scanning
_SKIP_DIRS = {"node_modules", "vendor", "__pycache__", ".git", ".venv", "venv",
              "dist", "build", ".mypy_cache", ".ruff_cache", ".pytest_cache"}


def _detect_lang(target: str) -> str:
    p = Path(target)
    if p.is_file():
        return _EXT_TO_LANG.get(p.suffix.lower(), "unknown")
    return "unknown"


# Extensions that are not source and are uninteresting to report as skipped.
_UNREMARKABLE = {
    ".md", ".txt", ".json", ".yml", ".yaml", ".toml", ".ini", ".cfg", ".lock",
    ".png", ".jpg", ".jpeg", ".gif", ".svg", ".ico", ".pdf", ".zip", ".gz",
    ".csv", ".tsv", ".log", ".xml", ".sql", ".neon", ".db", "",
}


def _collect_files(directory: str) -> tuple[dict[str, list[str]], dict[str, int]]:
    """Scan a directory, grouping files by language.

    Also returns a count of source-looking extensions that no tool covers.
    Silently omitting them is how this toolbox came to look inapplicable to an
    embedded project: pointed at a repo of one .py, one .ino and one .h, it
    reported on the .py and said nothing whatsoever about the other two, which
    reads as "the tool does nothing" rather than "the tool does not cover this".
    """
    groups: dict[str, list[str]] = {}
    skipped: dict[str, int] = {}
    for root, dirs, files in os.walk(directory):
        dirs[:] = [d for d in dirs if d not in _SKIP_DIRS]
        for f in files:
            ext = Path(f).suffix.lower()
            lang = _EXT_TO_LANG.get(ext)
            if lang:
                groups.setdefault(lang, []).append(str(Path(root) / f))
            elif ext not in _UNREMARKABLE:
                skipped[ext] = skipped.get(ext, 0) + 1
    return groups, skipped


# Arduino sketches use ArduinoJson's `variant | fallback` operator, which reads
# as a bitwise-or on an integer unless the analyser can see the overload. It
# cannot: cppcheck's preprocessor fails on ArduinoJson's version-namespace
# macros with "Invalid ## usage", and a failed parse yields an EMPTY report that
# looks exactly like a clean one. Measured 2026-08-12 on cam2135/CYD-Deck:
# without the header, cppcheck flagged 8 false badBitmaskCheck but did catch an
# injected out-of-bounds write and buffer overflow; with the header it caught
# nothing at all, injected bugs included.
#
# So the headers are deliberately NOT supplied, and this one rule is suppressed
# for sketches instead. Do not "improve" this by adding -I paths: that silently
# disables the whole analysis. g++ parses the same header fine, so this is a
# cppcheck limitation, not a property of the code.
_INO_SUPPRESS = ["badBitmaskCheck"]

_CPPCHECK_SUPPRESS = [
    "missingInclude",        # Arduino core headers are not present by design
    "missingIncludeSystem",
    "toomanyconfigs",        # informational, not a finding
    "unusedFunction",        # setup()/loop() and ISRs are called by the core
]


def run_cppcheck(target: str) -> dict[str, Any]:
    """Static analysis for C/C++ and Arduino sketches.

    This is the linter slot, not the strongest check available. For firmware the
    real gate is a compile against the actual toolchain (`arduino-cli compile`),
    which resolves library overloads properly. cppcheck is what runs anywhere
    without hundreds of megabytes of board support installed.
    """
    if not shutil.which("cppcheck"):
        return _tool_missing("cppcheck (apt install cppcheck)")
    suppress = list(_CPPCHECK_SUPPRESS)
    lang_args = []
    if Path(target).suffix.lower() == ".ino":
        suppress += _INO_SUPPRESS
        # Required, not cosmetic: cppcheck does not know the .ino extension, and
        # without this it reports a syntaxError at the first sketch construct and
        # analyses nothing. Measured. Not forced for .c/.cpp/.h, where cppcheck
        # infers correctly and forcing C++ on a C file would be wrong.
        lang_args = ["--language=c++"]
    args = [
        "cppcheck", *lang_args, "--enable=warning,style,performance,portability",
        "--inline-suppr", "--quiet",
        "--template={file}|{line}|{column}|{severity}|{id}|{message}",
        *[f"--suppress={s}" for s in suppress],
        target,
    ]
    result = subprocess.run(args, capture_output=True, text=True, check=False)
    issues = []
    # cppcheck writes findings to stderr, not stdout.
    for line in result.stderr.splitlines():
        parts = line.split("|", 5)
        if len(parts) < 6:
            continue
        fname, lineno, col, severity, rule, message = parts
        issues.append({
            "file":     fname,
            "line":     int(lineno) if lineno.isdigit() else 0,
            "col":      int(col) if col.isdigit() else 0,
            "severity": "error" if severity in ("error", "warning") else "warning",
            "rule":     rule,
            "message":  message,
            "fixable":  False,
            "fixable_unsafe": False,
        })
    return {"tool": "cppcheck", "status": _status(issues), "issues": issues}


TOOL_RUNNERS = {
    "cppcheck":       run_cppcheck,
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
    "cpp":    ["cppcheck"],
}

AUDIT_TOOLS = {
    "python": ["pip-audit"],
    "js":     ["npm-audit"],
    "php":    ["composer-audit"],
}

# Tools that accept directories natively (pass the dir, not individual files)
_DIR_CAPABLE = {"ruff", "mypy", "phpstan", "phpcs", "rector",
                "pip-audit", "npm-audit", "composer-audit"}
# cppcheck is deliberately not in _DIR_CAPABLE: the .ino suppression is decided
# per file, and passing a directory would apply sketch rules to every .cpp.


def _run_tools(tool_names: list[str], target: str) -> list[dict[str, Any]]:
    checks = []
    for name in tool_names:
        runner = TOOL_RUNNERS.get(name)
        if runner:
            checks.append(runner(target))
        else:
            checks.append({"tool": name, "status": "unknown",
                           "issues": [], "note": f"No runner for '{name}'"})
    return checks


def _summarize(checks: list[dict[str, Any]]) -> dict[str, Any]:
    all_issues = [i for c in checks for i in c.get("issues", [])]
    errors   = sum(1 for i in all_issues if i["severity"] == "error")
    warnings = sum(1 for i in all_issues if i["severity"] == "warning")
    return {
        "total":    len(all_issues),
        "errors":   errors,
        "warnings": warnings,
        # Split deliberately: "fixable" is what `fix.py` applies now, and
        # "fixable_unsafe" is what `fix.py --unsafe` would additionally attempt.
        # Reporting one combined number described work no tool would do.
        "fixable":  sum(1 for i in all_issues if i.get("fixable")),
        "fixable_unsafe": sum(1 for i in all_issues if i.get("fixable_unsafe")),
    }


# ── baseline ──────────────────────────────────────────────────────────────────
#
# A baseline is a stored per-file, per-tool count the gate compares against
# instead of comparing against zero. Three things about it are load-bearing, and
# all three were measured on batocera-watch on 2026-08-15 rather than reasoned:
#
# MODE. A baseline must be measured the way the gate measures. That repo's was
# recorded with a whole-repo run while the hook checks staged files one at a
# time, and the two disagreed by +398 errors across seven files nobody had
# touched since a week before. Only the tools that resolve ACROSS files moved --
# mypy and phpstan -- while ruff, phpcs and rector were byte-identical on every
# file. That partition is the signature of scope, not of drift, and it is why
# the mode is recorded here and a mismatch is REFUSED rather than compared.
# METHODOLOGY step 2 mandated the mismatch until this landed: it said to commit
# "the output of a full run", which reads as whole-repo to most people.
#
# TOOLS THAT DID NOT RUN. An unavailable tool reports zero issues. That reads as
# an improvement, lets a real regression through, and then bakes a false zero
# into the next re-record, after which the true count reads as a regression. A
# tool that is unavailable now, or whose version differs from the recording, is
# reported as "cannot compare" and excluded from the verdict. Never counted as
# zero, never silently passed. This is METHODOLOGY step 3 applied to the
# baseline itself: a check that did not run looks exactly like one that passed.
#
# VERSIONS DEGRADE, THEY DO NOT REFUSE. Compare only when BOTH sides are known.
# An unknown version is not evidence of a match, but refusing on one would make
# the feature unusable wherever a tool has no version we can parse -- and a
# guard that fires on "cannot tell" gets switched off, which is the reasoning
# coord.py already uses for session ownership.
#
# The verdict is on ERRORS, because that is what the gate already exits on.
# Warnings are recorded per file so a later change can use them, and are printed
# in the report, but they do not fail a commit that errors would not fail.

BASELINE_VERSION = 1

_VERSION_CMD = {
    "ruff":      ["ruff", "--version"],
    "mypy":      ["mypy", "--version"],
    "eslint":    ["eslint", "--version"],
    "stylelint": ["stylelint", "--version"],
    "prettier":  ["prettier", "--version"],
    "cppcheck":  ["cppcheck", "--version"],
}
_VERSION_RE = re.compile(r"\d+\.\d+(?:\.\d+)?")


def _tool_version(name: str) -> str:
    """Best-effort version string, or "" meaning "cannot tell"."""
    cmd = _VERSION_CMD.get(name)
    if not cmd or not shutil.which(cmd[0]):
        return ""
    try:
        r = subprocess.run(cmd, capture_output=True, text=True,
                           check=False, timeout=30)
    except (OSError, subprocess.SubprocessError):
        return ""
    m = _VERSION_RE.search(f"{r.stdout} {r.stderr}")
    return m.group(0) if m else ""


def _repo_root(target: str) -> Path:
    """What baseline paths are relative to.

    Git toplevel where there is one, so a file is the same key no matter which
    directory the gate ran from. Keyed on absolute paths a baseline is useless
    on any other machine; keyed on cwd-relative ones it silently changes meaning
    when the hook runs from a subdirectory.
    """
    p = Path(target).resolve()
    start = p if p.is_dir() else p.parent
    try:
        r = subprocess.run(
            ["git", "-C", str(start), "rev-parse", "--show-toplevel"],
            capture_output=True, text=True, check=False, timeout=30)
        if r.returncode == 0 and r.stdout.strip():
            return Path(r.stdout.strip())
    except (OSError, subprocess.SubprocessError):
        pass
    return start


def _rel(path_str: str, root: Path) -> str:
    try:
        return Path(path_str).resolve().relative_to(root).as_posix()
    except (ValueError, OSError):
        return Path(path_str).as_posix()


def _counts_by_file(checks: list[dict[str, Any]], root: Path) -> dict[str, Any]:
    """{path: {tool: {"errors": n, "warnings": n}}} for one run."""
    out: dict[str, Any] = {}
    for c in checks:
        tool = c.get("tool", "?")
        for i in c.get("issues", []):
            slot = out.setdefault(_rel(i.get("file", ""), root), {})
            counts = slot.setdefault(tool, {"errors": 0, "warnings": 0})
            counts["errors" if i.get("severity") == "error" else "warnings"] += 1
    return out


def _statuses(checks: list[dict[str, Any]]) -> dict[str, str]:
    return {c.get("tool", "?"): c.get("status", "unknown") for c in checks}


def _per_file_checks(target: str, audit: bool = False) -> list[dict[str, Any]]:
    """Run the measurement the GATE performs: every file on its own.

    Deliberately not `check.py .`. The entire point of the mode field is that
    the two produce different numbers, so a recorder taking the cheaper
    whole-repo route would record exactly the quantity the gate cannot
    reproduce -- which is the bug this feature exists to remove.
    """
    p = Path(target)
    if p.is_file():
        files = [str(p)]
    else:
        groups, _ = _collect_files(str(p))
        files = sorted(f for fs in groups.values() for f in fs)
    checks: list[dict[str, Any]] = []
    for f in files:
        lang = _detect_lang(f)
        names = list(DEFAULT_TOOLS.get(lang, []))
        if audit:
            names.extend(AUDIT_TOOLS.get(lang, []))
        if names:
            checks.extend(_run_tools(names, f))
    return checks


def record_baseline(target: str, dest: str, audit: bool = False) -> int:
    root   = _repo_root(target)
    checks = _per_file_checks(target, audit)
    files  = _counts_by_file(checks, root)
    tools  = sorted({c.get("tool", "?") for c in checks})
    status = _statuses(checks)
    doc = {
        "baseline_version": BASELINE_VERSION,
        "mode":     "per-file",
        "recorded": time.strftime("%Y-%m-%d"),
        "root":     root.name,
        # Recorded so a later run can refuse to compare numbers that two
        # different tools produced. "" means the version could not be
        # established, which downstream treats as "cannot tell", not "same".
        "tools":    {t: _tool_version(t) for t in tools},
        # A tool that was unavailable WHEN RECORDED contributed zero, and that
        # zero is not a measurement. Keeping the status makes it visible rather
        # than letting it read as a clean file.
        "status":   status,
        "totals":   _summarize(checks),
        "files":    files,
    }
    Path(dest).write_text(json.dumps(doc, indent=1, sort_keys=True) + "\n",
                          encoding="utf-8")
    missing = [t for t, s in sorted(status.items()) if s != "pass" and s != "fail"]
    print(f"recorded {len(files)} file(s) from {len(tools)} tool(s), "
          f"mode=per-file, to {dest}")
    if missing:
        print("  WARNING: recorded while these tools were not running: "
              + ", ".join(f"{t} ({status[t]})" for t in missing))
        print("  Their zeros are not measurements. Re-record where they run, "
              "or a later real count will read as a regression.")
    return 0


def compare_baseline(checks: list[dict[str, Any]], target: str,
                     source: str, mode: str) -> int:
    """0 = no worse, 1 = worse, 2 = cannot compare."""
    try:
        doc = json.loads(Path(source).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        print(f"baseline: cannot read {source}: {exc}", file=sys.stderr)
        return 2

    recorded_mode = doc.get("mode", "unknown")
    if recorded_mode != mode:
        print(f"baseline: REFUSING to compare. It was recorded as "
              f"mode={recorded_mode!r} and this run is {mode!r}. Those are "
              f"different quantities in the same units -- for any tool that "
              f"resolves across files they disagree by hundreds of findings on "
              f"unchanged code. Re-record with --record-baseline.",
              file=sys.stderr)
        return 2

    root   = _repo_root(target)
    now    = _counts_by_file(checks, root)
    was    = doc.get("files", {})
    old_v  = doc.get("tools", {})
    status = _statuses(checks)

    skipped: list[tuple[str, str]] = []
    for tool, st in sorted(status.items()):
        if st not in ("pass", "fail"):
            skipped.append((tool, f"did not run ({st})"))
            continue
        was_v, now_v = old_v.get(tool, ""), _tool_version(tool)
        if was_v and now_v and was_v != now_v:
            skipped.append((tool, f"version {was_v} -> {now_v}"))
    skip = {t for t, _ in skipped}

    worse, deltas = [], []
    for path in sorted(set(now) | set(was)):
        tools = set(now.get(path, {})) | set(was.get(path, {}))
        for tool in sorted(tools - skip):
            before = int(was.get(path, {}).get(tool, {}).get("errors", 0))
            after  = int(now.get(path, {}).get(tool, {}).get("errors", 0))
            if after != before:
                deltas.append(f"  {path}  {tool}  {before} -> {after} "
                              f"({after - before:+d})")
            if after > before:
                worse.append((path, tool, before, after))

    for tool, why in skipped:
        print(f"baseline: cannot compare {tool} -- {why}. Excluded from the "
              f"verdict rather than counted as zero.", file=sys.stderr)
    for line in deltas:
        print(line)
    if not deltas:
        # Say it. A ratchet that only speaks when it fails is indistinguishable
        # from one that never ran, which is the whole complaint in step 3.
        print(f"baseline: no change against {Path(source).name}")

    if worse:
        print("", file=sys.stderr)
        print("baseline: WORSE than recorded --", file=sys.stderr)
        for path, tool, before, after in worse:
            print(f"  {path}  {tool}  {before} -> {after}", file=sys.stderr)
        return 1
    return 0


# ── main ──────────────────────────────────────────────────────────────────────

def main() -> None:
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
    parser.add_argument("--skip-unsupported", action="store_true",
                        help="Exit 0 on a file whose language cannot be detected, "
                             "instead of 2. For callers handed an arbitrary file "
                             "list (the pre-commit hook), where a README is not "
                             "a failure.")
    parser.add_argument("--baseline", metavar="FILE",
                        help="Compare against a recorded baseline and fail only "
                             "where a file got WORSE, instead of failing on any "
                             "finding at all. Refuses if the baseline was "
                             "recorded in a different mode.")
    parser.add_argument("--record-baseline", metavar="FILE",
                        help="Measure per-file and write a baseline to FILE. "
                             "Per-file because that is what the gate does; see "
                             "METHODOLOGY, 'Adopting this in a codebase you "
                             "inherited', step 2.")
    args = parser.parse_args()

    target = str(Path(args.target).resolve())
    if not Path(target).exists():
        print(json.dumps({"error": f"Path not found: {target}"}))
        sys.exit(2)

    is_dir = Path(target).is_dir()

    # ── Record a baseline and stop ────────────────────────────────────────
    # Its own path, before the normal flow, because recording MEASURES
    # DIFFERENTLY: it runs every file on its own even when handed a directory,
    # which is exactly what `check.py .` does not do.
    if args.record_baseline:
        sys.exit(record_baseline(target, args.record_baseline, args.audit))

    # ── Single file or explicit --lang / --tools ──────────────────────────
    if not is_dir or args.lang != "auto" or args.tools:
        lang = args.lang if args.lang != "auto" else _detect_lang(target)
        if args.tools:
            tool_names = [t.strip() for t in args.tools.split(",")]
        elif lang in DEFAULT_TOOLS:
            tool_names = list(DEFAULT_TOOLS[lang])
        else:
            if args.skip_unsupported:
                print(json.dumps({"target": target, "skipped":
                                  "no language detected"}))
                sys.exit(0)
            print(json.dumps({"error": f"Cannot detect language for: {target}"}))
            sys.exit(2)
        if args.audit:
            tool_names.extend(AUDIT_TOOLS.get(lang, []))

        checks = _run_tools(tool_names, target)
        # Held in its own name rather than read back out of `output`: the dict
        # is heterogeneous, so indexing it twice asks the type checker to
        # believe a str and a list are also subscriptable by "errors".
        summary = _summarize(checks)
        output = {
            "target":   target,
            "language": lang,
            "checks":   checks,
            "summary":  summary,
        }
        print(json.dumps(output, indent=2 if args.pretty else None))
        # A single file IS the per-file measurement, so a per-file baseline is
        # comparable here and the verdict replaces the compare-against-zero one.
        if args.baseline:
            sys.exit(compare_baseline(checks, target, args.baseline, "per-file"))
        sys.exit(1 if summary["errors"] > 0 else 0)

    # ── Directory: scan, group by language, run appropriate tools ─────────
    groups, skipped = _collect_files(target)
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

    summary = _summarize(all_checks)
    output = {
        "target":     target,
        "mode":       "multi-language",
        "languages":  lang_sections,
        # Reported even when empty, so "nothing was skipped" is a stated result
        # rather than an absence the reader has to infer.
        "skipped":    [{"extension": e, "file_count": n}
                       for e, n in sorted(skipped.items(), key=lambda kv: -kv[1])],
        "checks":     all_checks,
        "summary":    summary,
    }

    print(json.dumps(output, indent=2 if args.pretty else None))
    # A directory run lets the dir-capable tools resolve across files, so this
    # is the WHOLE-REPO measurement. Declaring it as such is what makes
    # compare_baseline refuse rather than report scope as regression.
    if args.baseline:
        sys.exit(compare_baseline(all_checks, target, args.baseline, "whole-repo"))
    sys.exit(1 if summary["errors"] > 0 else 0)


if __name__ == "__main__":
    main()
