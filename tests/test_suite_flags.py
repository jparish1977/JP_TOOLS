#!/usr/bin/env python
"""
JP_TOOLS/tests/test_suite_flags.py
Every flag a suite passes to spool-audit.py must be a flag spool-audit.py has.

WHY
    When a flag is removed from the tool and a suite goes on passing it,
    argparse rejects it and exits 2 without running, and an assertion that
    the tool did NOT do something then passes for free. Every individual
    check can be reasonable while nothing compares the two lists; this does.

BOTH DIRECTIONS
    A suite naming a flag the tool lacks is the case above. A tool offering a
    flag no suite exercises is the same gap facing the other way.

PROSE IS NOT AN INVOCATION
    This has to read code, not text. Docstrings and comments can name flags
    that no longer exist, and a plain `grep -o -- '--[a-z-]*'` would find
    them on both sides and report agreement. Python strings come from the AST
    with docstrings dropped; shell lines have their comments stripped first.

    python tests/test_suite_flags.py
"""

import ast
import pathlib
import re
import subprocess
import sys

from _harness import ROOT as REPO

TOOL = REPO / "spool-audit.py"
# Every spool-audit suite, found rather than listed, so a renamed or added
# suite cannot drop out of the comparison.
SUITES = sorted((REPO / "tests").glob("test_spool_audit*.py")) + \
    sorted((REPO / "tests").glob("test_spool_audit*.sh"))

FLAG = re.compile(r"--[a-z][a-z-]+")
FAILURES: list[str] = []

# Not a flag of the tool, and not expected to be. --help is argparse's own.
IGNORED = {"--help"}


def tool_flags() -> set[str]:
    """The flags argparse actually accepts, read from its own options block.

    Not the whole --help text: argparse prints the module docstring as the
    description, and prose can name flags the tool does not have.
    """
    out = subprocess.run(
        [sys.executable, str(TOOL), "--help"],
        capture_output=True, text=True, check=False,
    ).stdout
    lines = out.splitlines()
    try:
        start = next(i for i, ln in enumerate(lines) if ln.rstrip() == "options:")
    except StopIteration:
        FAILURES.append("could not find the options: block in --help output")
        return set()
    found = set()
    for ln in lines[start + 1:]:
        if ln and not ln.startswith((" ", "\t")):
            break  # next section
        if ln.lstrip().startswith("-"):
            found.update(FLAG.findall(ln))
    return found - IGNORED


def _docstring_ids(tree: ast.AST) -> set[int]:
    """id() of every docstring constant in TREE."""
    ids = set()
    for node in ast.walk(tree):
        if isinstance(node, (ast.Module, ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            first = node.body[0] if node.body else None
            if (isinstance(first, ast.Expr) and isinstance(first.value, ast.Constant)
                    and isinstance(first.value.value, str)):
                ids.add(id(first.value))
    return ids


def flags_in_python(path: pathlib.Path) -> set[str]:
    """Flags in string literals, docstrings excluded."""
    tree = ast.parse(path.read_text(encoding="utf-8"))
    docstrings = _docstring_ids(tree)
    found = set()
    for node in ast.walk(tree):
        if (isinstance(node, ast.Constant) and isinstance(node.value, str)
                and id(node) not in docstrings):
            found.update(FLAG.findall(node.value))
    return found - IGNORED


def flags_in_shell(path: pathlib.Path) -> set[str]:
    """Flags in shell code, comments stripped.

    Crude on purpose: a full shell parser is not worth it here, and the
    failure mode of being crude is naming a flag that was only mentioned,
    which is a false alarm this suite makes loudly rather than a silence.
    """
    found = set()
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if line.startswith("#"):
            continue
        line = re.sub(r"\s#.*$", "", line)
        found.update(FLAG.findall(line))
    # Flags belonging to other programs the fixtures run.
    return found - IGNORED - {"--format", "--line-buffered"}


def flags_used(offered: set[str]) -> set[str]:
    """Every flag the suites pass, recording each one the tool does not accept."""
    used: set[str] = set()
    for suite in SUITES:
        got = flags_in_shell(suite) if suite.suffix == ".sh" else flags_in_python(suite)
        used |= got
        for flag in sorted(got - offered):
            FAILURES.append(
                f"{suite.name} passes {flag}, which spool-audit.py does not "
                "accept. argparse exits 2 without running, and an assertion "
                "about what the tool did not do then passes for free."
            )
    return used


def main() -> int:
    if not TOOL.exists():
        print(f"SKIP: {TOOL} not found")
        return 0

    offered = tool_flags()
    if not offered:
        FAILURES.append(
            "the tool appears to offer no flags at all; this suite would then "
            "pass by having nothing to compare, which is the failure it exists "
            "to catch"
        )
    if len(SUITES) < 2:
        FAILURES.append(f"found {len(SUITES)} spool-audit suite(s); the glob matched nothing useful")

    exercised = flags_used(offered)
    for flag in sorted(offered - exercised):
        FAILURES.append(
            f"spool-audit.py accepts {flag}, which no suite ever passes. "
            "Nothing here would notice it breaking."
        )

    if FAILURES:
        print(f"FLAG INVENTORY MISMATCH ({len(FAILURES)})")
        for f in FAILURES:
            print(f"  {f}")
        return 1
    print(f"flag inventory agrees: {', '.join(sorted(offered))}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
