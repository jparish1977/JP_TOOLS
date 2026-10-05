#!/usr/bin/env python3
"""Fail under a line-coverage threshold, read from a Clover report.

METHODOLOGY 2.5 and 7.6 ask for a coverage gate. Python has one built in
(`coverage report --fail-under`); PHPUnit does not, so a PHP project writes a
Clover report and this reads it:

    vendor/bin/phpunit --coverage-clover clover.xml
    python3 JP_TOOLS/coverage-gate.py clover.xml [--fail-under 95]

The figure is the PROJECT's line coverage: Clover's project-level
coveredstatements over statements, not any one file's.

Exit codes (jpexit): 0 OK, at or over the threshold; 1 FINDINGS, under it;
2 NOT_RUN, the report is missing, is not Clover, counts no statements at
all, or counts something that is not a coverage (more covered than there
are, or a negative count), so nothing sensible was measured. A report that cannot be read, or one with
nothing in it, is never a pass.

Why not an established Composer tool (METHODOLOGY 2.15): the two that exist,
rregeer/phpunit-coverage-check (the most used, last released 2019) and its
maintained fork esi/phpunit-coverage-check, both exit 1 for a missing report
and for one with zero statements, the same as for coverage under the
threshold, so "could not look" reads as "looked and found too little".
Measured 2026-10-05; see docs/coverage.md.
"""

import argparse
import sys
import xml.etree.ElementTree as ET
from pathlib import Path
from typing import NoReturn

# The packages beside this file import when another program loads it by path.
sys.path.insert(0, str(Path(__file__).resolve().parent))

import jpexit

DEFAULT_THRESHOLD = 95.0  # METHODOLOGY 2.5
MAX_PERCENT = 100.0


def name_process() -> None:
    try:
        import ctypes
        ctypes.CDLL(None).prctl(15, b"coverage-gate", 0, 0, 0)
    except (OSError, AttributeError):
        # reason: no libc or no prctl (named types): the process runs unnamed, which is never fatal
        pass


class UnreadableError(Exception):
    """The report measured nothing; the message says why."""


def line_coverage(report: Path) -> tuple[int, int]:
    """(covered, total) statements from REPORT's project-level metrics."""
    try:
        root = ET.parse(report).getroot()
    except (OSError, ET.ParseError) as e:
        raise UnreadableError(f"cannot read a Clover report at {report}: {e}") from e
    metrics = root.find("project/metrics")
    if metrics is None:
        raise UnreadableError(f"{report} has no project metrics; is it a Clover report?")
    try:
        total = int(metrics.get("statements", ""))
        covered = int(metrics.get("coveredstatements", ""))
    except ValueError as e:
        raise UnreadableError(f"{report}'s project metrics are not counts: {e}") from e
    if total == 0:
        raise UnreadableError(f"{report} has no statements counted; refusing to call that covered")
    if not 0 <= covered <= total:
        raise UnreadableError(f"{report} counts {covered} covered of {total}, which is not a coverage")
    return covered, total


def _threshold(text: str) -> float:
    try:
        value = float(text)
    except ValueError:
        raise argparse.ArgumentTypeError(f"not a percentage: {text!r}") from None
    if not 0 <= value <= MAX_PERCENT:
        raise argparse.ArgumentTypeError(f"not between 0 and 100: {text}")
    return value


class _Parser(argparse.ArgumentParser):
    """argparse, but a usage error is jpexit.NOT_RUN rather than argparse's own 2."""

    def error(self, message: str) -> NoReturn:
        self.print_usage(sys.stderr)
        print(f"coverage-gate: {message}", file=sys.stderr)
        raise SystemExit(jpexit.NOT_RUN)


def main(argv: list[str] | None = None) -> int:
    name_process()
    parser = _Parser(description="Fail under a line-coverage threshold, read from a Clover report.")
    parser.add_argument("report", help="the Clover report, e.g. from phpunit --coverage-clover")
    parser.add_argument("--fail-under", type=_threshold, default=DEFAULT_THRESHOLD, metavar="PERCENT",
                        help=f"the threshold (default {DEFAULT_THRESHOLD:g})")
    try:
        args = parser.parse_args(argv)
    except SystemExit as e:
        return int(e.code or 0)
    try:
        covered, total = line_coverage(Path(args.report))
    except UnreadableError as e:
        print(f"coverage-gate: {e}", file=sys.stderr)
        return jpexit.NOT_RUN
    percent = 100 * covered / total
    passed = percent >= args.fail_under
    print(f"coverage-gate: {'PASS' if passed else 'FAIL'}, {percent:.2f}% of lines "
          f"({covered} of {total}), threshold {args.fail_under:g}%")
    return jpexit.OK if passed else jpexit.FINDINGS


if __name__ == "__main__":
    sys.exit(main())
