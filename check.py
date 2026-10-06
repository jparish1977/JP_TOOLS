#!/usr/bin/env python
"""
JP_TOOLS/check.py
Run code quality tools against arbitrary files or directories.
Outputs structured JSON suitable for AI agent consumption.

Usage:
    python check.py <path> [--lang python|js|auto] [--tools ruff,mypy] [--pretty]

Exit codes (named in jpexit):
    0  OK        - every check ran, and none found an error (warnings OK)
    1  FINDINGS  - one or more errors found
    2  NOT_RUN   - usage or path error, OR a check that could not run: a tool that is
         unavailable, a tool that exited abnormally, or no runner for a
         requested tool. Each one is named on stderr. A check that did not
         run is not a pass.

The implementation is in jpcheck/, one module per responsibility.
"""

import argparse
import json
import sys
from pathlib import Path
from typing import Any, NoReturn

# The packages beside this file import when another program loads it by path,
# not only when it is run: a script's own directory is on sys.path only then.
sys.path.insert(0, str(Path(__file__).resolve().parent))

import jpexit
from jpcheck.baseline import compare_baseline, record_baseline
from jpcheck.common import _repo_root
from jpcheck.coverage import is_project_file
from jpcheck.files import _IGNORE_FILE, Ignore, _detect_lang, _IgnoreError, ignored_by, load_ignores
from jpcheck.lab import lab_claim
from jpcheck.registry import _exit_code
from jpcheck.runs import NoSourceError, NoToolsError, check_directory, check_target, tool_names
from jpcheck.smells import _SMELLS, _smell_measure

# Other repositories load this file as a module and read these names off it.
# tests/test_load_by_path.py pins them.
__all__ = ["_smell_measure", "is_project_file", "lab_claim"]


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Run code quality tools and output structured JSON.",
    )
    parser.add_argument("target", help="File or directory to check")
    parser.add_argument("--lang",  choices=["python", "js", "css", "html", "php", "cpp",
                                            "shell", "powershell", "auto"],
                        default="auto",
                        help="Language override (default: auto-detect)")
    parser.add_argument("--tools", metavar="TOOLS",
                        help="Comma-separated tools to run, e.g. ruff,mypy")
    parser.add_argument("--pretty", action="store_true",
                        help="Pretty-print JSON output")
    parser.add_argument("--audit", action="store_true",
                        help="Also run security audit tools (pip-audit, npm audit, composer audit)")
    parser.add_argument("--skip-unsupported", action="store_true",
                        help="Exit 0 on a file whose language cannot be detected, "
                             "instead of 2. For callers handed an arbitrary file "
                             "list (the pre-commit hook), where a README is not "
                             "a failure.")
    parser.add_argument("--baseline", metavar="FILE",
                        help="Compare against a recorded baseline and fail only "
                             "where a file got WORSE, instead of failing on any "
                             "finding at all. Refuses if the baseline was "
                             "recorded in a different mode.")
    parser.add_argument("--record-baseline", metavar="FILE",
                        help="Measure per-file and write a baseline to FILE. "
                             "Per-file because that is what the gate does; see "
                             "METHODOLOGY, 'Adopting this in a codebase you "
                             "inherited', step 2.")
    parser.add_argument("--force-baseline", action="store_true",
                        help="Allow --record-baseline to write a baseline that "
                             "drops files the existing one recorded. Needed "
                             "only when that loss is intended, such as after "
                             "deleting files.")
    parser.add_argument("--smells-base", metavar="REF", default="HEAD",
                        help="What a smell is NEW against: over its threshold "
                             "now and not at REF fails, and so does one over at REF "
                             "that grew; one already over that did not grow warns. "
                             "Default HEAD, the last commit, which is what the "
                             "pre-commit hook wants. CI on a pull request passes "
                             "the PR's base.")
    return parser


def _stop(doc: dict[str, Any], code: int) -> NoReturn:
    print(json.dumps(doc))
    sys.exit(code)


def _single(args: argparse.Namespace, target: str, root: Path,
            ignores: list[Ignore]) -> tuple[dict[str, Any], str]:
    """A single file, or a directory named with --lang or --tools: (output, baseline mode)."""
    is_dir = Path(target).is_dir()
    # A file the repo does not own is skipped, and says which entry did it,
    # so the hook prints the exclusion in every commit it applies to.
    hit = None if is_dir else ignored_by(target, root, ignores)
    if hit:
        _stop({"target": target,
               "skipped": f"ignored by {_IGNORE_FILE}: {hit[0]} ({hit[1]})"}, jpexit.OK)
    lang = args.lang if args.lang != "auto" else _detect_lang(target)
    try:
        names = tool_names(lang, args.tools, args.audit)
    except NoToolsError:
        if args.skip_unsupported:
            _stop({"target": target, "skipped": "no language detected"}, jpexit.OK)
        _stop({"error": f"Cannot detect language for: {target}"}, jpexit.NOT_RUN)
    # A single file IS the per-file measurement, so a per-file baseline is
    # comparable here.
    return check_target(target, lang, names, root, ignores), "per-file"


def main() -> None:
    args = _parser().parse_args()
    _SMELLS["base"] = args.smells_base

    target = str(Path(args.target).resolve())
    if not Path(target).exists():
        _stop({"error": f"Path not found: {target}"}, jpexit.NOT_RUN)

    # Read before any measuring, so a bad entry refuses every mode alike.
    root = _repo_root(target)
    try:
        ignores = load_ignores(root)
    except _IgnoreError as e:
        _stop({"error": str(e)}, jpexit.NOT_RUN)

    # Recording MEASURES DIFFERENTLY: it runs every file on its own even when
    # handed a directory, which is exactly what `check.py .` does not do.
    if args.record_baseline:
        sys.exit(record_baseline(target, args.record_baseline, args.audit,
                                 args.force_baseline))

    if not Path(target).is_dir() or args.lang != "auto" or args.tools:
        output, mode = _single(args, target, root, ignores)
    else:
        try:
            output = check_directory(target, args.audit, root, ignores)
        except NoSourceError:
            _stop({"error": f"No recognized source files in: {target}"}, jpexit.NOT_RUN)
        # A directory run lets the dir-capable tools resolve across files, so
        # this is the WHOLE-REPO measurement. Declaring it as such is what
        # makes compare_baseline refuse rather than report scope as regression.
        mode = "whole-repo"

    print(json.dumps(output, indent=2 if args.pretty else None))
    # With a baseline, the verdict replaces the compare-against-zero one.
    if args.baseline:
        sys.exit(compare_baseline(output["checks"], target, args.baseline, mode))
    sys.exit(_exit_code(output["summary"], output["checks"]))


if __name__ == "__main__":
    main()
