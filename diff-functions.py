#!/usr/bin/env python3
"""Extract and side-by-side diff two named functions from JS files.

Usage:
    python diff-functions.py file.js funcA funcB
    python diff-functions.py fileA.js:funcA fileB.js:funcB
    python diff-functions.py file.js funcA funcB --unified
"""

import argparse
import re
import sys
from pathlib import Path


def extract_function(filepath: str, func_name: str) -> tuple[list[str], int]:
    """Extract a function/method body from a JS file by name.

    Returns (lines, start_line_number).
    Handles: "funcName"(...) {, funcName(...) {, "funcName": function(
    """
    path = Path(filepath)
    if not path.exists():
        print(f"Error: {filepath} not found", file=sys.stderr)
        sys.exit(1)

    lines = path.read_text(encoding="utf-8").splitlines()

    # Patterns to match function definitions
    patterns = [
        rf'^\s*"?{re.escape(func_name)}"?\s*\(',           # "funcName"( or funcName(
        rf'^\s*"?{re.escape(func_name)}"?\s*:\s*function',  # "funcName": function
        rf'^\s*function\s+{re.escape(func_name)}\s*\(',     # function funcName(
        rf'^\s*const\s+{re.escape(func_name)}\s*=',         # const funcName =
        rf'^\s*let\s+{re.escape(func_name)}\s*=',           # let funcName =
        rf'^\s*var\s+{re.escape(func_name)}\s*=',           # var funcName =
    ]

    start = None
    for i, line in enumerate(lines):
        for pat in patterns:
            if re.search(pat, line):
                start = i
                break
        if start is not None:
            break

    if start is None:
        print(f"Error: function '{func_name}' not found in {filepath}", file=sys.stderr)
        sys.exit(1)

    # Find the end by tracking brace depth
    depth = 0
    found_open = False
    end = start

    for i in range(start, len(lines)):
        for ch in lines[i]:
            if ch == "{":
                depth += 1
                found_open = True
            elif ch == "}":
                depth -= 1
        if found_open and depth <= 0:
            end = i
            break

    return lines[start : end + 1], start + 1


def side_by_side(left: list[str], right: list[str], left_label: str, right_label: str, width: int = 80):
    """Print two function bodies side by side."""
    half = width // 2 - 2
    print(f"{'-' * half}+{'-' * half}")
    print(f"{left_label:<{half}}|{right_label:<{half}}")
    print(f"{'-' * half}+{'-' * half}")

    max_len = max(len(left), len(right))
    for i in range(max_len):
        l = left[i].rstrip() if i < len(left) else ""
        r = right[i].rstrip() if i < len(right) else ""

        # Highlight differences
        marker = " "
        if i < len(left) and i < len(right):
            if l.strip() != r.strip():
                marker = "!"
            elif l.strip() == r.strip() and l.strip():
                marker = " "
        elif i >= len(left) or i >= len(right):
            marker = "+"

        l_display = l[:half - 1]
        r_display = r[:half - 1]
        print(f"{l_display:<{half}}{marker}{r_display}")

    print(f"{'-' * half}+{'-' * half}")


def unified_diff(left: list[str], right: list[str], left_label: str, right_label: str):
    """Print unified diff."""
    import difflib
    diff = difflib.unified_diff(
        left, right,
        fromfile=left_label, tofile=right_label,
        lineterm=""
    )
    for line in diff:
        print(line)


def main():
    parser = argparse.ArgumentParser(description="Extract and diff two JS functions")
    parser.add_argument("source1", help="file.js:funcName or just file.js")
    parser.add_argument("source2", help="funcName (if file shared) or file.js:funcName")
    parser.add_argument("func2", nargs="?", help="Second function name (if both in same file)")
    parser.add_argument("--unified", "-u", action="store_true", help="Unified diff instead of side-by-side")
    parser.add_argument("--width", "-w", type=int, default=160, help="Terminal width for side-by-side")
    parser.add_argument("--stats", action="store_true", help="Show similarity statistics")
    args = parser.parse_args()

    # Parse source arguments
    if args.func2:
        # diff-functions.py file.js funcA funcB
        file1 = file2 = args.source1
        func1 = args.source2
        func2 = args.func2
    elif ":" in args.source1:
        # diff-functions.py fileA.js:funcA fileB.js:funcB
        file1, func1 = args.source1.rsplit(":", 1)
        file2, func2 = args.source2.rsplit(":", 1)
    else:
        parser.error("Provide file.js funcA funcB, or fileA.js:funcA fileB.js:funcB")
        return 1

    left, left_start = extract_function(file1, func1)
    right, right_start = extract_function(file2, func2)

    left_label = f"{Path(file1).name}:{func1} (line {left_start})"
    right_label = f"{Path(file2).name}:{func2} (line {right_start})"

    if args.stats:
        import difflib
        ratio = difflib.SequenceMatcher(
            None,
            [l.strip() for l in left],
            [r.strip() for r in right]
        ).ratio()
        print(f"\n{left_label}: {len(left)} lines")
        print(f"{right_label}: {len(right)} lines")
        print(f"Similarity: {ratio:.1%}")
        print()

    if args.unified:
        unified_diff(left, right, left_label, right_label)
    else:
        side_by_side(left, right, left_label, right_label, args.width)

    return 0


if __name__ == "__main__":
    sys.exit(main())
