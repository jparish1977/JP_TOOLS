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

# Ensure Node global bin dirs are on PATH (Windows npm installs land here)
_NODE_PATHS = [
    Path(os.environ.get("APPDATA", "")) / "npm",
    Path("C:/Program Files/nodejs"),
]
os.environ["PATH"] = os.pathsep.join(
    [str(p) for p in _NODE_PATHS if p.exists()] + [os.environ.get("PATH", "")]
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


def _detect_lang(target: str) -> str:
    p = Path(target)
    if p.is_file():
        ext = p.suffix.lower()
        if ext == ".py":
            return "python"
        if ext in {".js", ".ts", ".jsx", ".tsx", ".mjs", ".cjs"}:
            return "js"
        if ext in {".css", ".scss", ".less"}:
            return "css"
        if ext in {".html", ".htm"}:
            return "html"
    elif p.is_dir():
        py  = len(list(p.rglob("*.py")))
        js  = sum(len(list(p.rglob(f"*{e}"))) for e in (".js", ".ts", ".jsx", ".tsx"))
        css = sum(len(list(p.rglob(f"*{e}"))) for e in (".css", ".scss", ".less"))
        counts = {"python": py, "js": js, "css": css}
        best = max(counts, key=counts.get)
        return best if counts[best] > 0 else "unknown"
    return "unknown"


TOOL_RUNNERS = {
    "ruff":       run_ruff,
    "mypy":       run_mypy,
    "eslint":     run_eslint,
    "stylelint":  run_stylelint,
    "prettier":   run_prettier,
}

DEFAULT_TOOLS = {
    "python": ["ruff", "mypy"],
    "js":     ["eslint", "prettier"],
    "css":    ["stylelint", "prettier"],
    "html":   ["eslint", "stylelint", "prettier"],
}


# ── main ──────────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(
        description="Run code quality tools and output structured JSON.",
    )
    parser.add_argument("target", help="File or directory to check")
    parser.add_argument("--lang",  choices=["python", "js", "auto"], default="auto",
                        help="Language override (default: auto-detect)")
    parser.add_argument("--tools", metavar="TOOLS",
                        help="Comma-separated tools to run, e.g. ruff,mypy")
    parser.add_argument("--pretty", action="store_true",
                        help="Pretty-print JSON output")
    args = parser.parse_args()

    target = str(Path(args.target).resolve())
    if not Path(target).exists():
        print(json.dumps({"error": f"Path not found: {target}"}))
        sys.exit(2)

    lang = args.lang if args.lang != "auto" else _detect_lang(target)

    if args.tools:
        tool_names = [t.strip() for t in args.tools.split(",")]
    elif lang in DEFAULT_TOOLS:
        tool_names = DEFAULT_TOOLS[lang]
    else:
        print(json.dumps({"error": f"Cannot detect language for: {target}"}))
        sys.exit(2)

    checks = []
    for name in tool_names:
        runner = TOOL_RUNNERS.get(name)
        if runner:
            checks.append(runner(target))
        else:
            checks.append({"tool": name, "status": "unknown",
                           "issues": [], "note": f"No runner for '{name}'"})

    all_issues = [i for c in checks for i in c.get("issues", [])]
    errors   = sum(1 for i in all_issues if i["severity"] == "error")
    warnings = sum(1 for i in all_issues if i["severity"] == "warning")

    output = {
        "target":   target,
        "language": lang,
        "checks":   checks,
        "summary":  {
            "total":    len(all_issues),
            "errors":   errors,
            "warnings": warnings,
            "fixable":  sum(1 for i in all_issues if i.get("fixable")),
        },
    }

    print(json.dumps(output, indent=2 if args.pretty else None))
    sys.exit(1 if errors > 0 else 0)


if __name__ == "__main__":
    main()
