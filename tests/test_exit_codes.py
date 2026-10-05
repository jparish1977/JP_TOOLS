#!/usr/bin/env python3
"""
JP_TOOLS/tests/test_exit_codes.py
jpexit's values are a contract with whatever runs these scripts (hooks, CI,
other repos' gates, README's tables): pinned here, so renumbering one is a
failing test rather than a quiet change on one side of the boundary.

    python3 tests/test_exit_codes.py
"""

import sys

from _harness import check, run
from jpexit import FINDINGS, NOT_RUN, OK


def test_the_values_are_the_documented_ones() -> None:
    check("OK", OK, 0)
    check("FINDINGS", FINDINGS, 1)
    check("NOT_RUN", NOT_RUN, 2)


if __name__ == "__main__":
    sys.exit(run(globals(), "exit codes"))
