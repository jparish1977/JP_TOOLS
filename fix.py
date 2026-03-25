#!/usr/bin/env python
"""
JP_TOOLS/fix.py
Auto-fix what can be fixed automatically, report what can't.

Usage:
    python fix.py <path> [--lang python|js|auto] [--dry-run] [--pretty]
"""

import sys
import os
import json
import shutil
import subprocess
import argparse
from pathlib import Path

_NODE_PATHS = [
    Path(os.environ.get("APPDATA", "")) / "npm",
    Path("C:/Program Files/nodejs"),
]
os.environ["PATH"] = os.pathsep.join(
    [str(p) for p in _NODE_PATHS if p.exists()] + [os.environ.get("PATH", "")]
)


def fix_ruff(target: str, dry_run: bool) -> dict:
    if not shutil.which("ruff"):
        return {"tool": "ruff", "status": "unavailable", "fixed": 0, "remaining": []}
    args = ["ruff", "check", "--fix"]
    if dry_run:
        args.append("--diff")
    result = subprocess.run(args + [target], capture_output=True, text=True)
    # After fix, re-check to find what remains
    recheck = subprocess.run(
        ["ruff", "check", "--output-format", "json", target],
        capture_output=True, text=True,
    )
    try:
        remaining = json.loads(recheck.stdout) if recheck.stdout.strip() else []
    except json.JSONDecodeError:
        remaining = []
    return {
        "tool":      "ruff",
        "status":    "dry-run" if dry_run else "fixed",
        "fixed":     "?" if dry_run else "auto",
        "remaining": [
            {"file": i.get("filename"), "line": i.get("location", {}).get("row"),
             "rule": i.get("code"), "message": i.get("message")}
            for i in remaining
        ],
    }


def fix_prettier(target: str, dry_run: bool) -> dict:
    cmd = shutil.which("prettier") or shutil.which("prettier.cmd")
    if not cmd:
        return {"tool": "prettier", "status": "unavailable", "fixed": 0, "remaining": []}
    if dry_run:
        result = subprocess.run([cmd, "--check", target], capture_output=True, text=True)
        unformatted = [
            l.strip()[len("[warn]"):].strip()
            for l in (result.stdout + result.stderr).splitlines()
            if l.strip().startswith("[warn]")
        ]
        return {"tool": "prettier", "status": "dry-run", "would_fix": unformatted}
    result = subprocess.run([cmd, "--write", target], capture_output=True, text=True)
    return {
        "tool":   "prettier",
        "status": "fixed" if result.returncode == 0 else "error",
        "output": result.stdout.strip(),
    }


FIXERS = {
    "python": [("ruff", fix_ruff)],
    "js":     [("prettier", fix_prettier)],
}


def _detect_lang(target: str) -> str:
    p = Path(target)
    if p.is_file():
        ext = p.suffix.lower()
        if ext == ".py":          return "python"
        if ext in {".js", ".ts", ".jsx", ".tsx", ".html"}: return "js"
    elif p.is_dir():
        py = len(list(p.rglob("*.py")))
        js = sum(len(list(p.rglob(f"*{e}"))) for e in (".js", ".ts", ".jsx", ".tsx"))
        return "python" if py >= js else "js"
    return "unknown"


def main():
    parser = argparse.ArgumentParser(description="Auto-fix code quality issues.")
    parser.add_argument("target", help="File or directory to fix")
    parser.add_argument("--lang",    choices=["python", "js", "auto"], default="auto")
    parser.add_argument("--dry-run", action="store_true",
                        help="Show what would change without writing files")
    parser.add_argument("--pretty",  action="store_true")
    args = parser.parse_args()

    target = str(Path(args.target).resolve())
    lang   = args.lang if args.lang != "auto" else _detect_lang(target)
    fixers = FIXERS.get(lang, [])

    results = [fn(target, args.dry_run) for _, fn in fixers]
    print(json.dumps({"target": target, "language": lang, "results": results},
                     indent=2 if args.pretty else None))


if __name__ == "__main__":
    main()
