#!/usr/bin/env python
"""
JP_TOOLS/tests/test_suite_flags.py
Every flag a suite passes to spool-audit.py must be a flag spool-audit.py has.

WHY
    On 2026-08-14 --purge and --fix were deleted from the tool. Both suites
    went on invoking them, argparse rejected the unknown flag and exited 2
    without running, and the acceptance suite printed "13 passed, 0 failed"
    because its assertions were negatives that a program which never runs
    satisfies for free. Every individual check was reasonable; the gap was
    that nothing compared the two lists.

    Suggested by thinkpad-session over the fleet mailbox, 2026-08-14, as the
    cheap check that catches this exact cause across every suite at once.

BOTH DIRECTIONS
    A suite naming a flag the tool lacks is the bug above. A tool offering a
    flag no suite exercises is the same gap facing the other way -- that is
    how spool-audit.py reached a PR with a green badge and no job touching it.

PROSE IS NOT AN INVOCATION
    This has to read code, not text. The tool's own docstring explains why
    --purge and --fix were removed, and the suites carry comments about the
    bugs those flags caused, so a plain `grep -o -- '--[a-z-]*'` finds both
    names on both sides and reports perfect agreement about two flags that no
    longer exist. Python strings come from the AST with docstrings dropped;
    shell lines have their comments stripped first.

    python tests/test_suite_flags.py
"""

import ast
import pathlib
import re
import subprocess
import sys

REPO = pathlib.Path(__file__).resolve().parent.parent
TOOL = REPO / "spool-audit.py"
SUITES = [
    REPO / "tests" / "test_spool_audit.py",
    REPO / "tests" / "test_spool_audit_invariant.py",
    REPO / "tests" / "test_spool_audit_acceptance.sh",
]

FLAG = re.compile(r"--[a-z][a-z-]+")
FAILURES: list[str] = []

# Not a flag of the tool, and not expected to be. --help is argparse's own.
IGNORED = {"--help"}


def tool_flags() -> set[str]:
    """The flags argparse actually accepts, read from its own options block.

    Not the whole --help text: argparse prints the module docstring as the
    description, and that prose names flags this tool used to have.
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


def flags_in_python(path: pathlib.Path) -> set[str]:
    """Flags in string literals, docstrings excluded."""
    tree = ast.parse(path.read_text(encoding="utf-8"))
    docstrings = set()
    for node in ast.walk(tree):
        if isinstance(node, (ast.Module, ast.FunctionDef, ast.AsyncFunctionDef,
                             ast.ClassDef)):
            body = getattr(node, "body", [])
            if (body and isinstance(body[0], ast.Expr)
                    and isinstance(body[0].value, ast.Constant)
                    and isinstance(body[0].value.value, str)):
                docstrings.add(id(body[0].value))
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

    used: dict[str, set[str]] = {}
    for suite in SUITES:
        if not suite.exists():
            FAILURES.append(f"{suite.name}: listed here but not on disk")
            continue
        got = (flags_in_shell(suite) if suite.suffix == ".sh"
               else flags_in_python(suite))
        used[suite.name] = got
        for flag in sorted(got - offered):
            FAILURES.append(
                f"{suite.name} passes {flag}, which spool-audit.py does not "
                "accept. argparse exits 2 without running, and an assertion "
                "about what the tool did not do then passes for free."
            )

    exercised = set().union(*used.values()) if used else set()
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
