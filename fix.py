#!/usr/bin/env python
"""
JP_TOOLS/fix.py
Auto-fix what can be fixed automatically, report what can't.

Usage:
    python fix.py <path> [--lang python|js|auto] [--dry-run] [--pretty]
"""

import argparse
import json
import os
import shutil
import subprocess
from pathlib import Path

# One definition of where ruff's config comes from. check.py is import-safe
# (its argparse sits behind a main guard), so this defers to it rather than
# keeping a second copy that can drift out of step.
from check import _ruff_config_args

_EXTRA_PATHS = [
    Path(os.environ.get("APPDATA", "")) / "npm",
    Path("C:/Program Files/nodejs"),
    Path("C:/Users") / os.environ.get("USERNAME", "") / "AppData/Local/Programs/PHP/8.3.30/nts/x64",
]
os.environ["PATH"] = os.pathsep.join(
    [str(p) for p in _EXTRA_PATHS if p.exists()] + [os.environ.get("PATH", "")]
)



def _ruff_count(target: str) -> int:
    """How many diagnostics ruff reports right now."""
    out = subprocess.run(
        ["ruff", "check", *_ruff_config_args(target), "--output-format", "json", target],
        capture_output=True, text=True, check=False,
    ).stdout
    try:
        return len(json.loads(out)) if out.strip() else 0
    except json.JSONDecodeError:
        return 0


def fix_ruff(target: str, dry_run: bool, unsafe: bool = False, **_: object) -> dict:
    """Apply ruff's fixes.

    `ruff check --fix` applies *safe* fixes only. Most of what accumulates in
    practice is classed unsafe (a rewrite that could change behaviour, such as
    percent-format to f-string) or displayonly (never auto-applied). Without
    --unsafe this reports 0 fixed on files check.py describes as fixable, which
    is the tool disagreeing with its own report rather than a no-op.
    """
    if not shutil.which("ruff"):
        return {"tool": "ruff", "status": "unavailable", "fixed": 0, "remaining": []}
    before = _ruff_count(target)
    args = ["ruff", "check", *_ruff_config_args(target), "--fix"]
    if unsafe:
        args.append("--unsafe-fixes")
    if dry_run:
        args.append("--diff")
    subprocess.run([*args, target], capture_output=True, text=True, check=False)
    # After fix, re-check to find what remains
    recheck = subprocess.run(
        ["ruff", "check", *_ruff_config_args(target),
         "--output-format", "json", target],
        capture_output=True, text=True, check=False,
    )
    try:
        remaining = json.loads(recheck.stdout) if recheck.stdout.strip() else []
    except json.JSONDecodeError:
        remaining = []
    return {
        "tool":      "ruff",
        "status":    "dry-run" if dry_run else "fixed",
        # A real number. This used to say "auto", which was not checkable.
        "fixed":     0 if dry_run else max(0, before - len(remaining)),
        "unsafe":    unsafe,
        "remaining": [
            {"file": i.get("filename"), "line": i.get("location", {}).get("row"),
             "rule": i.get("code"), "message": i.get("message")}
            for i in remaining
        ],
    }


def fix_prettier(target: str, dry_run: bool, **_: object) -> dict:
    local_bin = Path(__file__).parent / "node_modules" / ".bin"
    cmd = (
        str(local_bin / "prettier.cmd") if (local_bin / "prettier.cmd").exists() else
        str(local_bin / "prettier")     if (local_bin / "prettier").exists() else
        shutil.which("prettier") or shutil.which("prettier.cmd")
    )
    if not cmd:
        return {"tool": "prettier", "status": "unavailable", "fixed": 0, "remaining": []}
    if dry_run:
        result = subprocess.run([cmd, "--check", target], capture_output=True, text=True, check=False)
        unformatted = [
            line.strip()[len("[warn]"):].strip()
            for line in (result.stdout + result.stderr).splitlines()
            if line.strip().startswith("[warn]")
        ]
        return {"tool": "prettier", "status": "dry-run", "would_fix": unformatted}
    result = subprocess.run([cmd, "--write", target], capture_output=True, text=True, check=False)
    return {
        "tool":   "prettier",
        "status": "fixed" if result.returncode == 0 else "error",
        "output": result.stdout.strip(),
    }


def _php_bin(name: str) -> str | None:
    tools_dir = Path(__file__).parent
    for ext in ("", ".bat", ".cmd"):
        local = tools_dir / "vendor" / "bin" / f"{name}{ext}"
        if local.exists():
            return str(local)
    return shutil.which(name) or shutil.which(f"{name}.bat")


def _php_cmd() -> str | None:
    return shutil.which("php") or shutil.which("php.exe")


def fix_phpcs(target: str, dry_run: bool, **_: object) -> dict:
    php  = _php_cmd()
    bin_ = _php_bin("phpcbf")
    if not php:
        return {"tool": "phpcbf", "status": "unavailable", "remaining": []}
    if not bin_:
        return {"tool": "phpcbf", "status": "unavailable",
                "note": "run: composer install in JP_TOOLS"}
    cfg  = Path(__file__).parent / "configs" / "phpcs.xml"
    args = [php, bin_]
    if cfg.exists():
        args += [f"--standard={cfg}"]
    if dry_run:
        # phpcs dry-run: check mode and report fixable
        check_bin = _php_bin("phpcs")
        if check_bin:
            r = subprocess.run([php, check_bin, "--report=json"] + ([f"--standard={cfg}"] if cfg.exists() else []) + [target],
                               capture_output=True, text=True, check=False)
            try:
                data = json.loads(r.stdout)
                fixable = [m for fd in data.get("files", {}).values()
                           for m in fd.get("messages", []) if m.get("fixable")]
                return {"tool": "phpcbf", "status": "dry-run",
                        "would_fix": len(fixable), "items": fixable}
            except (json.JSONDecodeError, TypeError):
                pass
        return {"tool": "phpcbf", "status": "dry-run", "would_fix": "?"}
    result = subprocess.run([*args, target], capture_output=True, text=True, check=False)
    return {"tool": "phpcbf", "status": "fixed" if result.returncode in (0, 1) else "error",
            "output": result.stdout.strip()}


def fix_rector(target: str, dry_run: bool, **_: object) -> dict:
    php  = _php_cmd()
    bin_ = _php_bin("rector")
    if not php:
        return {"tool": "rector", "status": "unavailable", "remaining": []}
    if not bin_:
        return {"tool": "rector", "status": "unavailable",
                "note": "run: composer install in JP_TOOLS"}
    cfg  = Path(__file__).parent / "configs" / "rector.php"
    args = [php, bin_, "process", "--output-format=json", "--no-progress-bar"]
    if cfg.exists():
        args += [f"--config={cfg}"]
    if dry_run:
        args.append("--dry-run")
    result = subprocess.run([*args, target], capture_output=True, text=True, check=False)
    try:
        data = json.loads(result.stdout)
        changed = data.get("changed_files", [])
        return {"tool": "rector", "status": "dry-run" if dry_run else "fixed",
                "changed": changed}
    except (json.JSONDecodeError, TypeError):
        return {"tool": "rector", "status": "error",
                "note": (result.stderr or result.stdout).strip()[:300]}


FIXERS = {
    "python": [("ruff",    fix_ruff)],
    "js":     [("prettier",fix_prettier)],
    "css":    [("prettier",fix_prettier)],
    "php":    [("phpcbf",  fix_phpcs), ("rector", fix_rector)],
}


def _detect_lang(target: str) -> str:
    p = Path(target)
    if p.is_file():
        ext = p.suffix.lower()
        if ext == ".py":
            return "python"
        if ext == ".php":
            return "php"
        if ext in {".js", ".ts", ".jsx", ".tsx", ".html"}:
            return "js"
        if ext in {".css", ".scss", ".less"}:
            return "css"
    elif p.is_dir():
        py  = len(list(p.rglob("*.py")))
        php = len(list(p.rglob("*.php")))
        js  = sum(len(list(p.rglob(f"*{e}"))) for e in (".js", ".ts", ".jsx", ".tsx"))
        counts = {"python": py, "php": php, "js": js}
        best = max(counts, key=counts.get)
        return best if counts[best] > 0 else "unknown"
    return "unknown"


def main():
    parser = argparse.ArgumentParser(description="Auto-fix code quality issues.")
    parser.add_argument("target", help="File or directory to fix")
    parser.add_argument("--lang",    choices=["python", "js", "css", "php", "auto"], default="auto")
    parser.add_argument("--dry-run", action="store_true",
                        help="Show what would change without writing files")
    parser.add_argument("--unsafe",  action="store_true",
                        help="also apply ruff's unsafe fixes; these can change "
                             "behaviour, so review the diff")
    parser.add_argument("--pretty",  action="store_true")
    args = parser.parse_args()

    target = str(Path(args.target).resolve())
    lang   = args.lang if args.lang != "auto" else _detect_lang(target)
    fixers = FIXERS.get(lang, [])

    results = [fn(target, args.dry_run, unsafe=args.unsafe) for _, fn in fixers]
    print(json.dumps({"target": target, "language": lang, "results": results},
                     indent=2 if args.pretty else None))


if __name__ == "__main__":
    main()
