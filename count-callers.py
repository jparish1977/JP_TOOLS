#!/usr/bin/env python3
"""Find every line that names a function across a codebase, sorted into kinds.

Usage:
    python count-callers.py processEscapePath /path/to/project
    python count-callers.py processEscapePath /path/to/project --type js
    python count-callers.py processEscapePath /path/to/project --exclude vendor,node_modules

Each line holding the name as a whole word is one of: DEF (a definition in
JS or Python form), COMMENT (the line starts a comment), CALL (the name
followed by `(`), or REF (anything else). Line-based: a call split across
lines, or one inside a string, is classified by its own line only.

Directories named in the exclude list are skipped wherever they sit below
the root; the root's own path is never matched against it. A file that
cannot be read is named on stderr and not counted.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
from collections.abc import Iterator
from dataclasses import asdict, dataclass
from pathlib import Path

DEFAULT_EXCLUDE = frozenset({"node_modules", "vendor", ".git", "__pycache__", "dist", "build"})

EXT_MAP = {
    "js": {".js", ".mjs", ".cjs", ".jsx", ".ts", ".tsx"},
    "py": {".py"},
    "php": {".php"},
    "css": {".css", ".scss", ".less"},
    "html": {".html", ".htm"},
}

_DEFINITIONS = (
    r'^\s*"?{name}"?\s*\(.*\)\s*\{{',  # name(...) {
    r'^\s*"?{name}"?\s*:\s*function',  # "name": function
    r"function\s+{name}\s*\(",  # function name(
    r"(const|let|var)\s+{name}\s*=",  # const name =
    r"def\s+{name}\s*\(",  # def name(  (Python)
)
_COMMENT_STARTS = ("//", "#", "/*")
KINDS = ("DEF", "CALL", "REF", "COMMENT")


@dataclass(frozen=True)
class Hit:
    file: str
    line: int
    kind: str
    text: str


def classify(line: str, name: str) -> str:
    """DEF, COMMENT, CALL or REF, for a line known to hold NAME."""
    escaped = re.escape(name)
    if any(re.search(p.format(name=escaped), line) for p in _DEFINITIONS):
        return "DEF"
    if line.lstrip().startswith(_COMMENT_STARTS):
        return "COMMENT"
    if re.search(rf"{escaped}\s*\(", line):
        return "CALL"
    return "REF"


def source_files(root: Path, extensions: set[str], exclude: set[str]) -> Iterator[Path]:
    """Files under ROOT with EXTENSIONS, in sorted order, never entering EXCLUDE dirs."""
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = sorted(d for d in dirnames if d not in exclude)
        for name in sorted(filenames):
            if Path(name).suffix in extensions:
                yield Path(dirpath) / name


def find_callers(root: Path, name: str, extensions: set[str], exclude: set[str]) -> list[Hit]:
    """Every line under ROOT naming NAME as a whole word."""
    pattern = re.compile(rf"\b{re.escape(name)}\b")
    hits = []
    for path in source_files(root, extensions, exclude):
        try:
            lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
        except OSError as exc:
            # reason: named rather than skipped silently, because a file left
            # out makes the count read lower than the truth
            print(f"count-callers: {path} unreadable ({exc}); NOT counted", file=sys.stderr)
            continue
        hits += [Hit(str(path.relative_to(root)), i, classify(line.strip(), name), line.strip()[:120])
                 for i, line in enumerate(lines, 1) if pattern.search(line)]
    return hits


def report(name: str, hits: list[Hit]) -> list[str]:
    by_kind = {k: [h for h in hits if h.kind == k] for k in KINDS}
    counts = ", ".join(f"{len(by_kind[k])} {label}" for k, label in
                       zip(KINDS, ("definitions", "calls", "references", "comments"), strict=True))
    lines = [f"Function: {name}", f"Found: {counts}", ""]
    for kind, title in (("DEF", "DEFINITIONS"), ("CALL", "CALLS"), ("REF", "REFERENCES")):
        if by_kind[kind]:
            lines += [f"{title}:", *(f"  {h.file}:{h.line}  {h.text}" for h in by_kind[kind]), ""]
    return lines


def _extensions(kind: str | None) -> set[str]:
    if kind is None:
        return set().union(*EXT_MAP.values())
    return EXT_MAP.get(kind) or {f".{kind}"}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("function", help="Function name to search for")
    parser.add_argument("path", help="Root directory to search")
    parser.add_argument("--type", help="File type: js, py, php, css, html, or any extension")
    parser.add_argument("--exclude", help="Comma-separated dirs to exclude (added to defaults)")
    parser.add_argument("--json", action="store_true", help="JSON output")
    args = parser.parse_args(argv)
    root = Path(args.path).resolve()
    if not root.is_dir():
        print(f"Error: {args.path} is not a directory", file=sys.stderr)
        return 1
    exclude = set(DEFAULT_EXCLUDE) | set(filter(None, (args.exclude or "").split(",")))
    hits = find_callers(root, args.function, _extensions(args.type), exclude)
    if args.json:
        print(json.dumps([asdict(h) for h in hits], indent=2))
    else:
        print("\n".join(report(args.function, hits)))
    return 0


if __name__ == "__main__":
    sys.exit(main())
