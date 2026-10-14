#!/usr/bin/env python3
"""
JP_TOOLS/tests/test_mutmut_log.py
jpcheck/mutmut_log.py: mutmut's output as mutmut-gate.py tells it.

WHAT IS CHECKED
    The relay, fed mutmut 3.8's own output shapes in pieces: each phase said
    once by the plan's subject, spinner frames and blank lines dropped,
    mutmut's other lines passed through whole, every line flushed. Progress
    each tenth of the mutants to test and after a minute's quiet, counted
    from where testing began, including the frame still unended; nothing to
    test, nothing said. While mutmut is quiet, a line each minute naming what
    it is still doing. A named run's result list counted and left out.

    The plan's line for each phase: what the clean run covers, the mutants
    tested by function with their reasons (biggest first, ten at most and a
    count of the rest), and the functions still open (three at most).

    run_relayed against a real child: stdout and stderr in one stream, a
    character split across two writes decoded whole, a bad byte replaced,
    the relay ticked while the child is quiet and after each piece, the
    child's exit code.

    python3 tests/test_mutmut_log.py
"""

import contextlib
import io
import os
import sys
import time
from pathlib import Path
from unittest import mock

from _harness import check, check_true, run
from jpcheck import mutmut_log as ml

_EPOCH = time.gmtime(0)


class _Flushes(io.StringIO):
    """stdout that counts its flushes."""

    def __init__(self) -> None:
        super().__init__()
        self.flushes = 0

    def flush(self) -> None:
        self.flushes += 1


class _StubPlan:
    def __init__(self) -> None:
        self.asked: list[str] = []

    def subject(self, title: str) -> str:
        self.asked.append(title)
        return f"<{title}>"

    def open_functions(self) -> str:
        return "OPEN"


def _relay(clock: list[float]) -> tuple[ml.Relay, _StubPlan]:
    plan = _StubPlan()
    return ml.Relay(plan, clock=lambda: clock[0]), plan


def _frame(judged: int, total: int, killed: int, no_tests: int, timeout: int, suspicious: int,
           survived: int, skipped: int, typed: int) -> str:
    """One of mutmut 3.8's progress frames, as print_stats draws it."""
    return (f"\r⠋ {judged}/{total}  🎉 {killed} 🫥 {no_tests}  ⏰ {timeout}  🤔 {suspicious}  "
            f"🙁 {survived}  🔇 {skipped}  🧙 {typed}")


def test_say_stamps_and_flushes() -> None:
    out = _Flushes()
    with mock.patch.object(time, "gmtime", return_value=time.gmtime(3723)), contextlib.redirect_stdout(out):
        ml.say("hello")
    check("the gate's line, the UTC time first, flushed once", (out.getvalue(), out.flushes),
          ("mutmut-gate 01:02:03Z: hello\n", 1))
    check("progress each tenth and each minute, mutmut's output waited for a second at a time",
          (ml.STEPS, ml.EVERY, ml.TICK), (10, 60.0, 1.0))


def test_the_relay_says_each_phase_once_and_passes_the_rest() -> None:
    clock = [0.0]
    relay, plan = _relay(clock)
    out = _Flushes()
    feed = ("\r⠋ Generating mutants\r⠙ Generating mutants\r⠹ Generating mutants\n"
            "    done in 141ms (0 files mutated, 0 ignored, 78 unmodified)\n"
            "\r⠸ Listing all tests\r⠼ Listing all tests\n"
            "Found 2 new tests, rerunning stats collection\n"
            "\r⠴ Running stats\n    done\n\n"
            "⠦nospace\n ⠧ indented\n🎉 not a mutant\n"
            "\r⠇ Running clean tests\n    done\n"
            "Running mutation testing\n")
    with mock.patch.object(time, "gmtime", return_value=_EPOCH), contextlib.redirect_stdout(out):
        for i in range(0, len(feed), 7):  # in pieces, a line split across them
            relay.feed(feed[i:i + 7])
        relay.close()
    check("each phase said once, by its subject; mutmut's other lines whole; frames and blank lines dropped",
          out.getvalue().splitlines(),
          ["mutmut-gate 00:00:00Z: <Generating mutants>",
           "    done in 141ms (0 files mutated, 0 ignored, 78 unmodified)",
           "mutmut-gate 00:00:00Z: <Listing all tests>",
           "Found 2 new tests, rerunning stats collection",
           "mutmut-gate 00:00:00Z: <Running stats>",
           "    done",
           "⠦nospace", " ⠧ indented", "🎉 not a mutant",
           "mutmut-gate 00:00:00Z: <Running clean tests>",
           "    done",
           "mutmut-gate 00:00:00Z: <Running mutation testing>"])
    check("  every line flushed as it was written", out.flushes, 12)
    check("  the plan asked once per phase", plan.asked,
          ["Generating mutants", "Listing all tests", "Running stats", "Running clean tests",
           "Running mutation testing"])
    check_true("  a relay's clock is the monotonic one by default", ml.Relay(plan).clock is time.monotonic)


def test_the_relay_says_progress_each_tenth_and_each_minute() -> None:
    clock = [100.0]
    relay, _ = _relay(clock)
    out = io.StringIO()
    with mock.patch.object(time, "gmtime", return_value=_EPOCH), contextlib.redirect_stdout(out):
        relay.feed("Running mutation testing\n")
        relay.feed(_frame(79, 100, 70, 3, 1, 0, 6, 0, 0))     # the start: 21 to test
        relay.feed(_frame(81, 100, 72, 3, 1, 0, 6, 0, 0))     # 2 of 21: short of a tenth
        relay.feed(_frame(82, 100, 73, 3, 1, 0, 6, 0, 0))     # 3 of 21: the first tenth
        relay.feed(_frame(83, 100, 73, 3, 1, 0, 7, 0, 0))     # 4 of 21: short of the second
        clock[0] = 159.9
        relay.tick()                                          # under a minute since the last line
        clock[0] = 160.0
        relay.tick()                                          # a minute: progress again
        relay.feed(_frame(84, 100, 74, 3, 1, 0, 7, 0, 0) + "   ")  # 5 of 21: the second tenth, padded
        clock[0] = 220.0
        relay.tick()                                          # the unended frame read already
        relay.feed(_frame(85, 100, 75, 3, 1, 0, 7, 0, 0))     # 6 of 21: short of the third
        relay.feed("\r= " + _frame(99, 100, 75, 3, 1, 0, 7, 0, 0)[3:])  # no spinner: not a frame
        relay.feed("\n")
        relay.feed(_frame(100, 100, 83, 4, 2, 1, 8, 1, 2))    # all 21
        relay.feed("\r⠋ 100/100  🎉 83 🫥 4\n")               # not a whole counter: a phase name
    check("progress on each new tenth and after a minute's quiet, counted from the start",
          out.getvalue().splitlines(),
          ["mutmut-gate 00:00:00Z: <Running mutation testing>",
           "mutmut-gate 00:00:00Z: 3 / 21 judged: 3 killed, 0 no tests, 0 timeout, 0 survived; OPEN",
           "mutmut-gate 00:00:00Z: 4 / 21 judged: 3 killed, 0 no tests, 0 timeout, 1 survived; OPEN",
           "mutmut-gate 00:00:00Z: 5 / 21 judged: 4 killed, 0 no tests, 0 timeout, 1 survived; OPEN",
           "mutmut-gate 00:00:00Z: 5 / 21 judged: 4 killed, 0 no tests, 0 timeout, 1 survived; OPEN",
           "= 99/100  🎉 75 🫥 3  ⏰ 1  🤔 0  🙁 7  🔇 0  🧙 0",
           ("mutmut-gate 00:00:00Z: 21 / 21 judged: 13 killed, 1 no tests, 1 timeout, 1 suspicious,"
            " 2 survived, 1 skipped, 2 caught by type check; OPEN"),
           "mutmut-gate 00:00:00Z: <100/100  🎉 83 🫥 4>"])
    clock = [0.0]
    relay, _ = _relay(clock)
    out = io.StringIO()
    with mock.patch.object(time, "gmtime", return_value=_EPOCH), contextlib.redirect_stdout(out):
        relay.feed(_frame(5, 5, 5, 0, 0, 0, 0, 0, 0))
        relay.feed(_frame(5, 5, 5, 0, 0, 0, 0, 0, 0))
    check("nothing left to test: no progress to say", out.getvalue(), "")
    relay, _ = _relay(clock)
    out = io.StringIO()
    with mock.patch.object(time, "gmtime", return_value=_EPOCH), contextlib.redirect_stdout(out):
        relay.feed(_frame(0, 2000, 0, 0, 0, 0, 0, 0, 0))
        relay.feed(_frame(2000, 2000, 1500, 0, 0, 1000, 0, 0, 0))
    check("counts past a thousand grouped", out.getvalue().splitlines(),
          [("mutmut-gate 00:00:00Z: 2,000 / 2,000 judged: 1,500 killed, 0 no tests, 0 timeout, 1,000 suspicious,"
            " 0 survived; OPEN")])


def test_the_relay_says_it_is_alive_while_mutmut_is_quiet() -> None:
    clock = [10.0]
    relay, _ = _relay(clock)
    out = io.StringIO()
    with mock.patch.object(time, "gmtime", return_value=_EPOCH), contextlib.redirect_stdout(out):
        clock[0] = 69.9
        relay.tick()
        clock[0] = 70.7
        relay.tick()
        clock[0] = 100.0
        relay.feed("\r⠋ Running clean tests\r")
        clock[0] = 159.0
        relay.tick()
        clock[0] = 160.5
        relay.tick()
        relay.tick()
    check("a line each minute nothing else was said, naming what it is still doing and for how long",
          out.getvalue().splitlines(),
          ["mutmut-gate 00:00:00Z: still starting mutmut, 60 s in",
           "mutmut-gate 00:00:00Z: <Running clean tests>",
           "mutmut-gate 00:00:00Z: still <Running clean tests>, 60 s in"])


def test_the_relay_counts_the_listed_results_instead_of_printing_them() -> None:
    clock = [0.0]
    relay, _ = _relay(clock)
    out = io.StringIO()
    with mock.patch.object(time, "gmtime", return_value=_EPOCH), contextlib.redirect_stdout(out):
        relay.feed("0.50 mutations/second\n\nMutant results\n--------------\n"
                   "🎉 a.x_f__mutmut_1\n🙁 mutmut-gate.xǁ_Parserǁerror__mutmut_12\n\n"
                   "the last line, unended")
        relay.close()
        relay.close()
    check("the list left out and counted, once; a line still unended at the close passed through",
          out.getvalue().splitlines(),
          ["0.50 mutations/second", "the last line, unended",
           ("mutmut-gate 00:00:00Z: mutmut listed the 2 named mutants' results one per line: "
            "left out here, `mutmut results` has them")])
    relay, _ = _relay(clock)
    out = io.StringIO()
    with contextlib.redirect_stdout(out):
        relay.close()
    check("nothing listed, nothing unended: the close says nothing", out.getvalue(), "")


def _plan(codes: dict[str, int | None], names: list[str], why: dict[str, str] | None = None) -> ml.Plan:
    return ml.Plan(lambda: codes, names, "TRACED", "SOURCES", lambda: why or {})


def test_the_plan_names_what_each_phase_works_on() -> None:
    codes: dict[str, int | None] = {"a.x_f__mutmut_1": None, "a.x_f__mutmut_2": None,
                                    "a.x_f__mutmut_3": None, "a.x_g__mutmut_1": None, "a.x_h__mutmut_1": 1}
    plan = _plan(codes, [], {"a.x_f": "code changed", "a.x_z": "new"})
    check("each phase's line, an unnamed run", [plan.subject(t) for t in (
        "Generating mutants", "Listing all tests", "Running stats", "Running clean tests",
        "Running forced fail test", "Filtering mutations with type checker")],
        ["generating mutants: SOURCES",
         "listing tests: every test pytest collects, to find the ones not traced yet",
         "tracing reach: TRACED",
         "clean run: the whole suite in one process, which must pass so a mutant's failure is its own",
         "forced-fail check: a test made to fail must fail, or no verdict can be trusted",
         "mutmut: Filtering mutations with type checker"])
    check("  no phase but testing reads the mutants", plan.tested, {})
    check("testing, unnamed: every mutant with no verdict, by function, biggest first, each with its reason",
          plan.subject(ml.TESTING),
          "testing 4 mutants in 2 functions (code changed: 1, no verdict kept: 1): "
          "3 in a.x_f (code changed), 1 in a.x_g (no verdict kept)")
    check("  and how far: no function done yet", plan.open_functions(),
          "0 of 2 functions done, a.x_f (3 left), a.x_g (1 left)")
    codes.update({"a.x_f__mutmut_2": 0, "a.x_g__mutmut_1": 1})
    check("  one function done, the other's left counted", plan.open_functions(),
          "1 of 2 functions done, a.x_f (2 left)")
    named = _plan({"b.x_k__mutmut_1": None, "b.x_k__mutmut_2": 1}, ["b.x_k__mutmut_2"])
    check("a named run's clean run is the named mutants' tests",
          named.subject("Running clean tests"),
          "clean run: the 1 named mutants' tests, which must pass so a mutant's failure is its own")
    check("  and it tests just the named, a verdict or not",
          named.subject(ml.TESTING), "testing 1 mutants in 1 functions (no verdict kept: 1): "
                                     "1 in b.x_k (no verdict kept)")


def test_the_plan_lists_ten_functions_at_most() -> None:
    many: dict[str, int | None] = {f"c.x_{n:02d}__mutmut_{i}": None
                                   for n in range(12) for i in range(1, 2 + (n == 11))}
    wide = _plan(many, list(many))
    check("past ten functions: the ten biggest, then how many more, ties by name",
          wide.subject(ml.TESTING),
          "testing 13 mutants in 12 functions (no verdict kept: 12): 2 in c.x_11 (no verdict kept), "
          + ", ".join(f"1 in c.x_{n:02d} (no verdict kept)" for n in range(9)) + ", and 2 more")
    check("  and three open functions at most", wide.open_functions(),
          "0 of 12 functions done, c.x_11 (2 left), c.x_00 (1 left), c.x_01 (1 left)")
    ten = [f"c.x_{n:02d}__mutmut_1" for n in range(10)]
    check("exactly ten functions: all of them, nothing more to count",
          _plan(many, ten).subject(ml.TESTING),
          "testing 10 mutants in 10 functions (no verdict kept: 10): "
          + ", ".join(f"1 in c.x_{n:02d} (no verdict kept)" for n in range(10)))
    big = {**many, **{f"d.x_k__mutmut_{i}": None for i in range(1, 1235)}}
    check("counts past a thousand grouped", _plan(big, []).subject(ml.TESTING),
          "testing 1,247 mutants in 13 functions (no verdict kept: 13): 1,234 in d.x_k (no verdict kept), "
          "2 in c.x_11 (no verdict kept), "
          + ", ".join(f"1 in c.x_{n:02d} (no verdict kept)" for n in range(8)) + ", and 3 more")


class _Rec:
    """A relay that records what it is given."""

    def __init__(self) -> None:
        self.events: list[tuple[str, str]] = []

    def feed(self, text: str) -> None:
        self.events.append(("feed", text))

    def tick(self) -> None:
        self.events.append(("tick", ""))

    def close(self) -> None:
        self.events.append(("close", ""))


def test_relayed_output_reaches_the_relay_as_it_comes() -> None:
    """A real child: stdout and stderr in one stream, a character split across
    two writes decoded whole, an undecodable byte replaced, the relay ticked
    while the child is quiet and after each piece, and the child's exit code."""
    script = ("import sys, time\n"
              "time.sleep(0.5)\n"
              "o = sys.stdout.buffer\n"
              "o.write('a ⠋'.encode()[:3]); o.flush(); time.sleep(0.2)\n"
              "o.write('a ⠋'.encode()[3:] + b' b\\n'); o.flush(); time.sleep(0.2)\n"
              "sys.stderr.write('err\\n'); sys.stderr.flush(); time.sleep(0.2)\n"
              "o.write(b'end \\xe2'); o.flush()\n"
              "sys.exit(3)\n")
    rec = _Rec()
    with mock.patch.object(ml, "TICK", 0.05):
        rc = ml.run_relayed([sys.executable, "-c", script], Path.cwd(), dict(os.environ), rec)
    check("the child's exit code", rc, 3)
    check("  everything it wrote, both streams, decoded across the split, the bad byte replaced",
          "".join(text for what, text in rec.events if what == "feed"), "a ⠋ b\nerr\nend �")
    check("  ticked while it was quiet, before its first output", [what for what, _ in rec.events][:3],
          ["tick"] * 3)
    check("  closed once, last", ([what for what, _ in rec.events].count("close"), rec.events[-1]),
          (1, ("close", "")))
    rec = _Rec()
    with mock.patch.object(ml, "TICK", 30.0):
        ml.run_relayed([sys.executable, "-c", ("import sys, time\nfor c in 'xyz':\n"
                                               "    sys.stdout.write(c); sys.stdout.flush(); time.sleep(0.1)\n")],
                       Path.cwd(), dict(os.environ), rec)
    check("a child that never goes quiet: ticked after each piece all the same",
          [e for e in rec.events if e != ("feed", "")],
          [("feed", "x"), ("tick", ""), ("feed", "y"), ("tick", ""), ("feed", "z"), ("tick", ""), ("close", "")])


if __name__ == "__main__":
    sys.exit(run(globals(), "mutmut log"))
