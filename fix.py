#!/usr/bin/env python3
"""Auto-fix what can be fixed automatically, report what can't.

Usage:
    python fix.py <path> [--lang python|js|css|php|auto] [--dry-run] [--unsafe] [--pretty]

Prints one JSON object: the target, the language, and one result per fixer
(ruff for Python; prettier for JS and CSS; phpcbf then rector for PHP). A
fixer that is not installed reports "unavailable" rather than being skipped.
ruff's result counts what it fixed from its findings before and after; a
count that could not be taken is null, never 0, and a ruff that could not run
at all reports "error" with its reason, as check.py does.

Exit codes: 0 every fixer ran (or none applies to the target), 2 a fixer did
not run, named on stderr: a fixer that did not run is not a pass.
"""

import argparse
import json
import os
import shutil
import subprocess
import sys
from collections.abc import Callable
from pathlib import Path
from typing import Any

# The packages beside this file import when another program loads it by path.
sys.path.insert(0, str(Path(__file__).resolve().parent))

import jpexit
from jpcheck.common import _node_bin, _php_bin, _php_cmd, _tool_result
from jpcheck.python_tools import _ruff_config_args, ruff_env

TOOLS_ROOT = Path(__file__).resolve().parent

# Where Windows installs of node and PHP land without being on PATH.
_EXTRA_PATHS = [
    Path(os.environ.get("APPDATA", "")) / "npm",
    Path("C:/Program Files/nodejs"),
    Path("C:/Users") / os.environ.get("USERNAME", "") / "AppData/Local/Programs/PHP/8.3.30/nts/x64",
]

Result = dict[str, Any]


def _extend_path() -> None:
    os.environ["PATH"] = os.pathsep.join(
        [str(p) for p in _EXTRA_PATHS if p.exists()] + [os.environ.get("PATH", "")])


class _RuffError(Exception):
    """ruff stopped short of a verdict; the message says why, as check.py does."""


def _ruff(target: str, *args: str) -> "subprocess.CompletedProcess[str]":
    """`ruff check ARGS TARGET` under the project's config. Exit 0 or 1 is a
    verdict; anything else (2: a config it cannot load, say) raises
    _RuffError, since its stdout is then empty and would read as clean."""
    proc = subprocess.run(["ruff", "check", *_ruff_config_args(target), *args, target],
                          capture_output=True, text=True, check=False, env=ruff_env())
    result = _tool_result("ruff", [], proc)
    if result["status"] == "error":
        raise _RuffError(result["note"])
    return proc


def _ruff_findings(target: str) -> list[dict[str, Any]] | None:
    """ruff's findings on TARGET now, or None when its output is not JSON."""
    out = _ruff(target, "--output-format", "json").stdout
    try:
        findings: list[dict[str, Any]] = json.loads(out) if out.strip() else []
    except json.JSONDecodeError as e:
        print(f"fix: ruff's output was not JSON ({e}); its count is unknown", file=sys.stderr)
        return None
    return findings


def fix_ruff(target: str, dry_run: bool, unsafe: bool = False, **_: object) -> Result:
    """Apply ruff's fixes.

    `ruff check --fix` applies *safe* fixes only. Most of what accumulates in
    practice is classed unsafe (a rewrite that could change behaviour, such as
    percent-format to f-string) or displayonly (never auto-applied). Without
    --unsafe this reports 0 fixed on files check.py describes as fixable, which
    is the tool disagreeing with its own report rather than a no-op.

    A ruff that cannot run reports "error" with its reason and no fixed count.
    """
    if not shutil.which("ruff"):
        return {"tool": "ruff", "status": "unavailable", "fixed": 0, "remaining": []}
    args = ["--fix"]
    if unsafe:
        args.append("--unsafe-fixes")
    if dry_run:
        args.append("--diff")
    try:
        before = _ruff_findings(target)
        _ruff(target, *args)
        after = _ruff_findings(target)
    except _RuffError as e:
        return {"tool": "ruff", "status": "error", "unsafe": unsafe, "note": str(e)}
    fixed = None if before is None or after is None else max(0, len(before) - len(after))
    return {
        "tool":      "ruff",
        "status":    "dry-run" if dry_run else "fixed",
        "fixed":     0 if dry_run else fixed,
        "unsafe":    unsafe,
        "remaining": [
            {"file": i.get("filename"), "line": i.get("location", {}).get("row"),
             "rule": i.get("code"), "message": i.get("message")}
            for i in after or []
        ],
    }


def fix_prettier(target: str, dry_run: bool, **_: object) -> Result:
    cmd = _node_bin("prettier")
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


def _unavailable(tool: str, php: str | None) -> Result:
    if not php:
        return {"tool": tool, "status": "unavailable", "remaining": []}
    return {"tool": tool, "status": "unavailable", "note": "run: composer install in JP_TOOLS"}


def _phpcs_would_fix(php: str, standard: list[str], target: str) -> Result:
    """What phpcbf would fix: phpcs's fixable messages, or '?' when phpcs cannot say."""
    check_bin = _php_bin("phpcs")
    if check_bin:
        r = subprocess.run([php, check_bin, "--report=json", *standard, target],
                           capture_output=True, text=True, check=False)
        try:
            fixable = [m for fd in json.loads(r.stdout).get("files", {}).values()
                       for m in fd.get("messages", []) if m.get("fixable")]
        except (json.JSONDecodeError, TypeError, AttributeError):
            # reason: not the JSON expected: would_fix reads '?', which is visible
            return {"tool": "phpcbf", "status": "dry-run", "would_fix": "?"}
        return {"tool": "phpcbf", "status": "dry-run", "would_fix": len(fixable), "items": fixable}
    return {"tool": "phpcbf", "status": "dry-run", "would_fix": "?"}


def fix_phpcs(target: str, dry_run: bool, **_: object) -> Result:
    php, bin_ = _php_cmd(), _php_bin("phpcbf")
    if not php or not bin_:
        return _unavailable("phpcbf", php)
    cfg = TOOLS_ROOT / "configs" / "phpcs.xml"
    standard = [f"--standard={cfg}"] if cfg.exists() else []
    if dry_run:
        return _phpcs_would_fix(php, standard, target)
    result = subprocess.run([php, bin_, *standard, target], capture_output=True, text=True, check=False)
    return {"tool": "phpcbf", "status": "fixed" if result.returncode in (0, 1) else "error",
            "output": result.stdout.strip()}


def fix_rector(target: str, dry_run: bool, **_: object) -> Result:
    php, bin_ = _php_cmd(), _php_bin("rector")
    if not php or not bin_:
        return _unavailable("rector", php)
    cfg = TOOLS_ROOT / "configs" / "rector.php"
    args = [php, bin_, "process", "--output-format=json", "--no-progress-bar"]
    if cfg.exists():
        args += [f"--config={cfg}"]
    if dry_run:
        args.append("--dry-run")
    result = subprocess.run([*args, target], capture_output=True, text=True, check=False)
    try:
        changed = json.loads(result.stdout).get("changed_files", [])
    except (json.JSONDecodeError, TypeError, AttributeError):
        # reason: the tool's own output is the error, reported as such below
        return {"tool": "rector", "status": "error",
                "note": (result.stderr or result.stdout).strip()[:300]}
    return {"tool": "rector", "status": "dry-run" if dry_run else "fixed", "changed": changed}


Fixer = Callable[..., Result]

FIXERS: dict[str, list[tuple[str, Fixer]]] = {
    "python": [("ruff",    fix_ruff)],
    "js":     [("prettier", fix_prettier)],
    "css":    [("prettier", fix_prettier)],
    "php":    [("phpcbf",  fix_phpcs), ("rector", fix_rector)],
}

_BY_SUFFIX = {".py": "python", ".php": "php", ".js": "js", ".ts": "js", ".jsx": "js",
              ".tsx": "js", ".html": "js", ".css": "css", ".scss": "css", ".less": "css"}


def _detect_lang(target: str) -> str:
    p = Path(target)
    if p.is_file():
        return _BY_SUFFIX.get(p.suffix.lower(), "unknown")
    if not p.is_dir():
        return "unknown"
    counts = {
        "python": len(list(p.rglob("*.py"))),
        "php": len(list(p.rglob("*.php"))),
        "js": sum(len(list(p.rglob(f"*{e}"))) for e in (".js", ".ts", ".jsx", ".tsx")),
    }
    best = max(counts, key=lambda name: counts[name])
    return best if counts[best] > 0 else "unknown"


_RAN = ("fixed", "dry-run")


def _exit_code(results: list[Result]) -> int:
    """jpexit.OK, or jpexit.NOT_RUN when a fixer did not run (not installed,
    or stopped short of a verdict), each named on stderr, as check.py names a
    check that did not run. What a fixer leaves unfixed does not change it:
    findings are check.py's to count."""
    not_run = [r for r in results if r.get("status") not in _RAN]
    for r in not_run:
        note = r.get("note", "")
        print(f"fix.py: did not run {r.get('tool', '?')} "
              f"({r.get('status', '?')}){': ' + note if note else ''}", file=sys.stderr)
    if not_run:
        print(f"fix.py: exit {jpexit.NOT_RUN} -- a fixer that did not run is not a pass.",
              file=sys.stderr)
        return jpexit.NOT_RUN
    return jpexit.OK


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Auto-fix code quality issues.")
    parser.add_argument("target", help="File or directory to fix")
    parser.add_argument("--lang",    choices=["python", "js", "css", "php", "auto"], default="auto")
    parser.add_argument("--dry-run", action="store_true",
                        help="Show what would change without writing files")
    parser.add_argument("--unsafe",  action="store_true",
                        help="also apply ruff's unsafe fixes; these can change "
                             "behaviour, so review the diff")
    parser.add_argument("--pretty",  action="store_true")
    args = parser.parse_args(argv)
    _extend_path()
    target = str(Path(args.target).resolve())
    lang = args.lang if args.lang != "auto" else _detect_lang(target)
    results = [fn(target, args.dry_run, unsafe=args.unsafe) for _, fn in FIXERS.get(lang, [])]
    print(json.dumps({"target": target, "language": lang, "results": results},
                     indent=2 if args.pretty else None))
    return _exit_code(results)


if __name__ == "__main__":
    sys.exit(main())
