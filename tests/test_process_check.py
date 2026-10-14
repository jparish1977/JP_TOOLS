#!/usr/bin/env python3
"""
JP_TOOLS/tests/test_process_check.py
process_check.check_exit: a failed check on an exit code carries what the
process printed.

    - The exit wanted, and every text asked for: nothing recorded.
    - A wrong exit: one failure, naming both exits and the output's last lines.
    - Text the output lacks: named, with the output, though the exit was right;
      and text it must not hold, the same way.
    - NONZERO wants any exit but 0; 0 under it fails.
    - Only the last TAIL lines are carried, stdout's before stderr's.

    python3 tests/test_process_check.py
"""

import subprocess
import sys

import _harness
from _harness import check, run
from process_check import NONZERO, TAIL, check_exit


def _proc(code: int, out: str = "", err: str = "") -> subprocess.CompletedProcess[str]:
    return subprocess.CompletedProcess(["x"], code, out, err)


def _recorded(proc: subprocess.CompletedProcess[str], want: int, says: tuple[str, ...] = ()) -> list[str]:
    """What check_exit records for PROC, leaving _harness.FAILURES as it was."""
    before = len(_harness.FAILURES)
    check_exit("the label", proc, want, says)
    got = _harness.FAILURES[before:]
    del _harness.FAILURES[before:]
    return got


def test_a_met_check_records_nothing() -> None:
    check("exit 0 wanted and given, the text there: nothing", _recorded(_proc(0, "all good\n"), 0, ("good",)), [])
    check("NONZERO and exit 2: nothing", _recorded(_proc(2), NONZERO), [])


def test_a_wrong_exit_says_what_the_process_printed() -> None:
    check("exit 1 for 0: both exits and every line, stdout then stderr",
          _recorded(_proc(1, "out one\n", "refused: the reason\n"), 0),
          [("the label: exit 1, wanted 0; the last 2 line(s) it printed:\n"
            "    out one\n    refused: the reason")])
    check("NONZERO and exit 0: 'not 0' wanted",
          _recorded(_proc(0, "went through\n"), NONZERO),
          ["the label: exit 0, wanted not 0; the last 1 line(s) it printed:\n    went through"])


def test_text_the_output_lacks_is_named() -> None:
    check("the right exit, a text missing: named, with the output",
          _recorded(_proc(1, "BROKEN: x\n"), NONZERO, ("BROKEN", "exited 3")),
          [("the label: exit 1, wanted not 0; its output lacks ['exited 3']; the last 1 line(s) it printed:\n"
            "    BROKEN: x")])


def test_text_the_output_must_not_hold_is_named() -> None:
    check("text that must not be there, absent: nothing",
          _recorded_lacks(_proc(1, "refused\n"), NONZERO, ("own hook ran",)), [])
    check("text that must not be there, there: named, with the output",
          _recorded_lacks(_proc(1, "own hook ran\n"), NONZERO, ("own hook ran",)),
          [("the label: exit 1, wanted not 0; its output holds ['own hook ran']; the last 1 line(s) it printed:\n"
            "    own hook ran")])


def _recorded_lacks(proc: subprocess.CompletedProcess[str], want: int, lacks: tuple[str, ...]) -> list[str]:
    before = len(_harness.FAILURES)
    check_exit("the label", proc, want, lacks=lacks)
    got = _harness.FAILURES[before:]
    del _harness.FAILURES[before:]
    return got


def test_only_the_tail_is_carried() -> None:
    lines = [f"line {n}" for n in range(TAIL + 5)]
    got = _recorded(_proc(3, "\n".join(lines) + "\n"), 0)
    check("past TAIL lines: only the last TAIL, the count said",
          got, [f"the label: exit 3, wanted 0; the last {TAIL} line(s) it printed:\n"
                + "\n".join(f"    {line}" for line in lines[-TAIL:])])
    check("no output at all: said as none", _recorded(_proc(4), 0),
          ["the label: exit 4, wanted 0; the last 0 line(s) it printed:\n"])


if __name__ == "__main__":
    sys.exit(run(globals(), "process check"))
