#!/usr/bin/env python3
"""Extract two named functions from JS files and diff them, side by side or unified.

Usage:
    python diff-functions.py file.js funcA funcB
    python diff-functions.py fileA.js:funcA fileB.js:funcB
    python diff-functions.py file.js funcA funcB --unified
    python diff-functions.py file.js funcA funcB --stats

A function is found by its first line (`name(`, `"name"(`, `name: function`,
`function name(`, or `const|let|var name =`) and ends where its braces
balance, or, with no braces, at the `;` that ends its statement. Braces
inside strings and comments are counted too, so a body holding an
unbalanced one in a string ends early or late.

In the side-by-side view the middle column is `!` where the two lines
differ (ignoring indentation) and `+` where only one side has a line.

Exit status: 0 shown, 1 a file or function not found.
"""

from __future__ import annotations

import argparse
import difflib
import re
import sys
from pathlib import Path

_DEFINITIONS = (
    r'^\s*"?{name}"?\s*\(',  # "name"( or name(
    r'^\s*"?{name}"?\s*:\s*function',  # "name": function
    r"^\s*function\s+{name}\s*\(",  # function name(
    r"^\s*(?:const|let|var)\s+{name}\s*=",  # const name =
)


class NotFoundError(Exception):
    """A file or a function in it could not be found."""


def _start(lines: list[str], name: str) -> int | None:
    patterns = [re.compile(p.format(name=re.escape(name))) for p in _DEFINITIONS]
    return next((i for i, line in enumerate(lines) if any(p.search(line) for p in patterns)), None)


def _end(lines: list[str], start: int) -> int:
    """The line where the braces opened at or after START balance.

    A definition with no braces (`const f = (x) => x;`) ends at the first
    line ending in `;` before any brace opens, or at START.
    """
    depth = 0
    opened = False
    for i in range(start, len(lines)):
        depth += lines[i].count("{") - lines[i].count("}")
        opened = opened or "{" in lines[i]
        if (opened and depth <= 0) or (not opened and lines[i].rstrip().endswith(";")):
            return i
    return start


def extract_function(filepath: str, name: str) -> tuple[list[str], int]:
    """(the function's lines, its 1-based first line). Raises NotFoundError."""
    path = Path(filepath)
    if not path.is_file():
        raise NotFoundError(f"{filepath} not found")
    lines = path.read_text(encoding="utf-8").splitlines()
    start = _start(lines, name)
    if start is None:
        raise NotFoundError(f"function '{name}' not found in {filepath}")
    return lines[start : _end(lines, start) + 1], start + 1


def _marker(left: list[str], right: list[str], i: int) -> str:
    if i >= len(left) or i >= len(right):
        return "+"
    return "!" if left[i].strip() != right[i].strip() else " "


def side_by_side(left: list[str], right: list[str], left_label: str, right_label: str,
                 width: int = 80) -> list[str]:
    """The two bodies in columns, with a difference marker between them."""
    half = width // 2 - 2
    rule = f"{'-' * half}+{'-' * half}"
    out = [rule, f"{left_label:<{half}}|{right_label:<{half}}", rule]
    for i in range(max(len(left), len(right))):
        lhs = left[i].rstrip() if i < len(left) else ""
        rhs = right[i].rstrip() if i < len(right) else ""
        out.append(f"{lhs[:half - 1]:<{half}}{_marker(left, right, i)}{rhs[:half - 1]}")
    return [*out, rule]


def unified(left: list[str], right: list[str], left_label: str, right_label: str) -> list[str]:
    return list(difflib.unified_diff(left, right, fromfile=left_label, tofile=right_label,
                                     lineterm=""))


def stats(left: list[str], right: list[str], left_label: str, right_label: str) -> list[str]:
    """Line counts and how similar the bodies are, ignoring indentation."""
    ratio = difflib.SequenceMatcher(
        None, [s.strip() for s in left], [s.strip() for s in right]).ratio()
    return ["", f"{left_label}: {len(left)} lines", f"{right_label}: {len(right)} lines",
            f"Similarity: {ratio:.1%}", ""]


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("source1", help="file.js:funcName, or file.js")
    parser.add_argument("source2", help="file.js:funcName, or funcName when both share a file")
    parser.add_argument("func2", nargs="?", help="Second function name (both in one file)")
    parser.add_argument("--unified", "-u", action="store_true", help="Unified diff instead of side-by-side")
    parser.add_argument("--width", "-w", type=int, default=160, help="Terminal width for side-by-side")
    parser.add_argument("--stats", action="store_true", help="Show similarity statistics")
    return parser


def _targets(args: argparse.Namespace) -> tuple[str, str, str, str] | None:
    """(file1, func1, file2, func2) from the arguments, or None when they do not name both."""
    if args.func2:
        return args.source1, args.source2, args.source1, args.func2
    if ":" in args.source1 and ":" in args.source2:
        file1, func1 = args.source1.rsplit(":", 1)
        file2, func2 = args.source2.rsplit(":", 1)
        return file1, func1, file2, func2
    return None


def main(argv: list[str] | None = None) -> int:
    parser = _parser()
    args = parser.parse_args(argv)
    targets = _targets(args)
    if targets is None:
        parser.error("give file.js funcA funcB, or fileA.js:funcA fileB.js:funcB")
    file1, func1, file2, func2 = targets
    try:
        left, left_start = extract_function(file1, func1)
        right, right_start = extract_function(file2, func2)
    except NotFoundError as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1
    labels = (f"{Path(file1).name}:{func1} (line {left_start})",
              f"{Path(file2).name}:{func2} (line {right_start})")
    out = stats(left, right, *labels) if args.stats else []
    out += unified(left, right, *labels) if args.unified else side_by_side(left, right, *labels, args.width)
    print("\n".join(out))
    return 0


if __name__ == "__main__":
    sys.exit(main())
