#!/usr/bin/env python3
"""Find all call sites of a function across a codebase.

Usage:
    python count-callers.py processEscapePath /path/to/project
    python count-callers.py processEscapePath /path/to/project --type js
    python count-callers.py processEscapePath /path/to/project --exclude vendor,node_modules
"""

import argparse
import re
import sys
from pathlib import Path

# Default directories to exclude
DEFAULT_EXCLUDE = {"node_modules", "vendor", ".git", "__pycache__", "dist", "build"}

# File extensions by type
EXT_MAP = {
    "js": {".js", ".mjs", ".cjs", ".jsx", ".ts", ".tsx"},
    "py": {".py"},
    "php": {".php"},
    "css": {".css", ".scss", ".less"},
    "html": {".html", ".htm"},
}


def find_callers(root: Path, func_name: str, extensions: set[str], exclude: set[str]):
    """Find all references to func_name in files under root."""
    results = []
    pattern = re.compile(r'\b' + re.escape(func_name) + r'\b')

    for path in sorted(root.rglob("*")):
        if not path.is_file():
            continue
        if path.suffix not in extensions:
            continue
        if any(ex in path.parts for ex in exclude):
            continue

        try:
            lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
        except (OSError, UnicodeDecodeError):
            continue

        for i, line in enumerate(lines):
            if pattern.search(line):
                stripped = line.strip()
                # Classify: definition vs call vs reference
                kind = classify(stripped, func_name)
                results.append({
                    "file": str(path.relative_to(root)),
                    "line": i + 1,
                    "kind": kind,
                    "text": stripped[:120],
                })

    return results


def classify(line: str, func_name: str) -> str:
    """Classify a line as definition, call, or reference."""
    # Definition patterns
    def_patterns = [
        rf'^\s*"?{re.escape(func_name)}"?\s*\(.*\)\s*\{{',  # funcName(...) {
        rf'^\s*"?{re.escape(func_name)}"?\s*:\s*function',    # "funcName": function
        rf'function\s+{re.escape(func_name)}\s*\(',           # function funcName(
        rf'(const|let|var)\s+{re.escape(func_name)}\s*=',     # const funcName =
        rf'def\s+{re.escape(func_name)}\s*\(',                # def funcName( (Python)
    ]
    for pat in def_patterns:
        if re.search(pat, line):
            return "DEF"

    # Comment
    if line.lstrip().startswith("//") or line.lstrip().startswith("#") or line.lstrip().startswith("/*"):
        return "COMMENT"

    # Call pattern: funcName( or .funcName(
    if re.search(rf'\.?{re.escape(func_name)}\s*\(', line):
        return "CALL"

    return "REF"


def main():
    parser = argparse.ArgumentParser(description="Find all call sites of a function")
    parser.add_argument("function", help="Function name to search for")
    parser.add_argument("path", help="Root directory to search")
    parser.add_argument("--type", help="File type filter: js, py, php, css, html")
    parser.add_argument("--exclude", help="Comma-separated dirs to exclude (added to defaults)")
    parser.add_argument("--json", action="store_true", help="JSON output")
    args = parser.parse_args()

    root = Path(args.path).resolve()
    if not root.is_dir():
        print(f"Error: {args.path} is not a directory", file=sys.stderr)
        return 1

    # Determine extensions
    if args.type:
        extensions = EXT_MAP.get(args.type, set())
        if not extensions:
            extensions = {f".{args.type}"}
    else:
        extensions = set()
        for exts in EXT_MAP.values():
            extensions.update(exts)

    exclude = set(DEFAULT_EXCLUDE)
    if args.exclude:
        exclude.update(args.exclude.split(","))

    results = find_callers(root, args.function, extensions, exclude)

    if args.json:
        import json
        print(json.dumps(results, indent=2))
        return 0

    # Summary
    defs = [r for r in results if r["kind"] == "DEF"]
    calls = [r for r in results if r["kind"] == "CALL"]
    refs = [r for r in results if r["kind"] == "REF"]
    comments = [r for r in results if r["kind"] == "COMMENT"]

    print(f"Function: {args.function}")
    print(f"Found: {len(defs)} definitions, {len(calls)} calls, {len(refs)} references, {len(comments)} comments")
    print()

    if defs:
        print("DEFINITIONS:")
        for r in defs:
            print(f"  {r['file']}:{r['line']}  {r['text']}")
        print()

    if calls:
        print("CALLS:")
        for r in calls:
            print(f"  {r['file']}:{r['line']}  {r['text']}")
        print()

    if refs:
        print("REFERENCES:")
        for r in refs:
            print(f"  {r['file']}:{r['line']}  {r['text']}")

    return 0


if __name__ == "__main__":
    sys.exit(main())
