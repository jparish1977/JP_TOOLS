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
import queue
import subprocess
import sys
import tempfile
import time
from collections.abc import Callable
from pathlib import Path
from unittest import mock

from _harness import ROOT, check, check_true, run
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
          (ml.STEPS, ml.EVERY, ml.TICK, ml.STOP_WAIT), (10, 60.0, 1.0, 30.0))


class _HalfWritten(_StubPlan):
    """A plan whose reads meet a .meta mutmut is rewriting."""

    def subject(self, title: str) -> str:
        raise ValueError("Expecting value: line 1 column 1 (char 0)")

    def open_functions(self) -> str:
        raise ValueError("Expecting value: line 1 column 1 (char 0)")


def test_what_the_log_cannot_read_it_says_and_goes_on() -> None:
    clock = [0.0]
    relay = ml.Relay(_HalfWritten(), clock=lambda: clock[0])
    out = io.StringIO()
    with mock.patch.object(time, "gmtime", return_value=_EPOCH), contextlib.redirect_stdout(out):
        relay.feed("Running mutation testing\n")
        relay.feed(_frame(0, 10, 0, 0, 0, 0, 0, 0, 0))
        relay.feed(_frame(5, 10, 5, 0, 0, 0, 0, 0, 0))
    check("a phase and a progress line it could not read: said so, the run not stopped",
          out.getvalue().splitlines(),
          [("mutmut-gate 00:00:00Z: mutmut: Running mutation testing (what it works on could not be read:"
            " Expecting value: line 1 column 1 (char 0))"),
           ("mutmut-gate 00:00:00Z: 5 / 10 judged: 5 killed, 0 no tests, 0 timeout, 0 survived;"
            " functions open could not be read: Expecting value: line 1 column 1 (char 0)")])
    out = io.StringIO()
    with mock.patch.object(time, "gmtime", return_value=_EPOCH), contextlib.redirect_stdout(out):
        relay.plan = _OSErrorPlan()
        relay.feed(_frame(10, 10, 10, 0, 0, 0, 0, 0, 0))
        relay.feed("\r⠋ Running clean tests\n")
    check("  and an OSError the same", out.getvalue().splitlines(),
          [("mutmut-gate 00:00:00Z: 10 / 10 judged: 10 killed, 0 no tests, 0 timeout, 0 survived;"
            " functions open could not be read: gone"),
           "mutmut-gate 00:00:00Z: mutmut: Running clean tests (what it works on could not be read: gone)"])


class _OSErrorPlan(_StubPlan):
    def subject(self, title: str) -> str:
        raise OSError("gone")

    def open_functions(self) -> str:
        raise OSError("gone")


def test_the_relay_says_each_phase_once_and_passes_the_rest() -> None:
    clock = [0.0]
    relay, plan = _relay(clock)
    out = _Flushes()
    feed = ("\r⠏ \r⠋ Generating mutants\r⠙ Generating mutants\r⠹ Generating mutants\n"
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
    check("each phase said once, by its subject (a frame with none is none); mutmut's other lines whole;"
          " frames and blank lines dropped",
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
        relay.feed(_frame(0, 20, 0, 0, 0, 0, 0, 0, 0))
        relay.feed(_frame(1, 20, 1, 0, 0, 0, 0, 0, 0))
        relay.feed(_frame(2, 20, 2, 0, 0, 0, 0, 0, 0))     # 2 of 20: a tenth exactly
    check("a tenth reached exactly is a tenth", out.getvalue().splitlines(),
          ["mutmut-gate 00:00:00Z: 2 / 20 judged: 2 killed, 0 no tests, 0 timeout, 0 survived; OPEN"])
    relay, _ = _relay(clock)
    out = io.StringIO()
    with mock.patch.object(time, "gmtime", return_value=_EPOCH), contextlib.redirect_stdout(out):
        relay.feed(_frame(0, 2000, 0, 0, 0, 0, 0, 0, 0))
        relay.feed(_frame(2000, 2000, 1500, 0, 0, 1000, 0, 0, 0))
    relay, _ = _relay(clock)
    glued = _Flushes()
    with mock.patch.object(time, "gmtime", return_value=_EPOCH), contextlib.redirect_stdout(glued):
        relay.feed(_frame(0, 10, 0, 0, 0, 0, 0, 0, 0))
        relay.feed(_frame(1, 10, 1, 0, 0, 0, 0, 0, 0) + "Traceback (most recent call last):\n")
    check("a child's line on the end of a frame: the frame counted, the line passed through, each flushed",
          (glued.getvalue().splitlines(), glued.flushes),
          (["Traceback (most recent call last):",
            "mutmut-gate 00:00:00Z: 1 / 10 judged: 1 killed, 0 no tests, 0 timeout, 0 survived; OPEN"], 2))
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
    with mock.patch.object(time, "gmtime", return_value=_EPOCH), contextlib.redirect_stdout(out):
        relay.feed("🎉 a.x_g__mutmut_3\n")
        relay.close()
    check("  and counted afresh after it", out.getvalue().splitlines()[-1],
          ("mutmut-gate 00:00:00Z: mutmut listed the 1 named mutants' results one per line: "
           "left out here, `mutmut results` has them"))
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
         "clean run: the whole suite, which must pass so a mutant's failure is its own",
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
    odd = _plan({"a.x__mutmut_odd__mutmut_1": None, "a.x__mutmut_2": None}, [])
    check("a name holding __mutmut_ twice is its function up to the first, as mutmut reads it",
          odd.subject(ml.TESTING), "testing 2 mutants in 1 functions (no verdict kept: 1): "
                                   "2 in a.x (no verdict kept)")


def test_a_half_written_meta_is_read_again() -> None:
    reads: list[int] = []
    pauses: list[float] = []

    def flaky(fails: int) -> Callable[[], dict[str, int | None]]:
        def codes() -> dict[str, int | None]:
            reads.append(1)
            if len(reads) <= fails:
                raise ValueError("Expecting value: line 1 column 1 (char 0)")
            return {"a.x_f__mutmut_1": None}
        return codes

    check("read again, READS times, REREAD seconds apart", (ml.READS, ml.REREAD), (3, 0.2))
    with mock.patch.object(time, "sleep", pauses.append):
        plan = ml.Plan(flaky(2), [], "T", "S", dict)
        line = plan.subject(ml.TESTING)
    check("two half-written reads, then a whole one: the line, after two pauses", (line, len(reads), pauses),
          ("testing 1 mutants in 1 functions (no verdict kept: 1): 1 in a.x_f (no verdict kept)", 3, [0.2, 0.2]))
    reads.clear()
    pauses.clear()
    with mock.patch.object(time, "sleep", pauses.append):
        plan = ml.Plan(flaky(9), [], "T", "S", dict)
        try:
            plan.open_functions()
            raised = ""
        except ValueError as e:
            raised = str(e)
    check("never whole: the last failure raised, after READS reads", (raised, len(reads), pauses),
          ("Expecting value: line 1 column 1 (char 0)", 3, [0.2, 0.2]))


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
    eleven = [f"c.x_{n:02d}__mutmut_1" for n in range(11)]
    check("eleven: ten, and one more", _plan(many, eleven).subject(ml.TESTING),
          "testing 11 mutants in 11 functions (no verdict kept: 11): "
          + ", ".join(f"1 in c.x_{n:02d} (no verdict kept)" for n in range(10)) + ", and 1 more")
    big = {**many, **{f"d.x_k__mutmut_{i}": None for i in range(1, 1235)}}
    check("counts past a thousand grouped", _plan(big, []).subject(ml.TESTING),
          "testing 1,247 mutants in 13 functions (no verdict kept: 13): 1,234 in d.x_k (no verdict kept), "
          "2 in c.x_11 (no verdict kept), "
          + ", ".join(f"1 in c.x_{n:02d} (no verdict kept)" for n in range(8)) + ", and 3 more")


def test_the_first_run_says_why_its_functions_are_tested() -> None:
    check("by mutmut's hashes, before and now: new, changed; the same, none",
          ml.code_reasons({"a.x_f": "1", "a.x_g": "2", "a.x_gone": "5"}, {"a.x_f": "1", "a.x_g": "3", "a.x_h": "4"}),
          {"a.x_g": "code changed", "a.x_h": "new"})


def test_the_sources_line() -> None:
    check("no commit, none changed, two, five, six, seven",
          [ml.sources_line(3, None), ml.sources_line(3, []), ml.sources_line(3, ["a.py", "b.py"]),
           *(ml.sources_line(3, [f"m{n}.py" for n in range(count)]) for count in (5, 6, 7))],
          ["3 source files kept, no state commit to say which changed",
           "3 source files kept, 0 changed since the state",
           "3 source files kept, 2 changed since the state (a.py, b.py)",
           "3 source files kept, 5 changed since the state (m0.py, m1.py, m2.py, m3.py, m4.py)",
           "3 source files kept, 6 changed since the state (m0.py, m1.py, m2.py, m3.py, m4.py, ...)",
           "3 source files kept, 7 changed since the state (m0.py, m1.py, m2.py, m3.py, m4.py, ...)"])


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


def test_the_relay_ends_one_tick_after_the_run() -> None:
    """After the run has ended, pieces still coming are relayed until a TICK
    has passed since the end, and not one past it: a clock the test sets, the
    end seen at 10.0, then 10.5 and 11.0."""
    chunks: queue.Queue[bytes] = queue.Queue()
    for piece in (b"a", b"b", b"c", b"d"):
        chunks.put(piece)
    rec = _Rec()
    clock = iter([10.0, 10.0, 10.5, 11.0, 11.5, 12.0, 12.5])
    # mutmut_log reads time.monotonic off the time module at each call;
    # queue and threading bound their own copy at import, so their waits keep
    # the real clock.
    with mock.patch.object(ml, "TICK", 1.0), mock.patch.object(time, "monotonic", lambda: next(clock)):
        ml._relay_until_done(chunks, rec, lambda: True)
    check("relayed until exactly a TICK after the end: a, b, c, not d",
          "".join(text for what, text in rec.events if what == "feed"), "abc")
    check("  and d left unread", chunks.get_nowait(), b"d")


def test_drain_passes_each_piece_then_the_end() -> None:
    chunks: queue.Queue[bytes] = queue.Queue()
    read, write = os.pipe()
    os.write(write, b"abc")
    os.close(write)
    with open(read, "rb") as pipe:
        ml._drain(pipe, chunks)
    got = []
    while not chunks.empty():
        got.append(chunks.get())
    check("the pipe's bytes, then b'' and nothing else", got, [b"abc", b""])


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
    ml.run_relayed([sys.executable, "-c", "import os; print(os.environ['JPT_SEEN'])"], Path.cwd(),
                   {**os.environ, "JPT_SEEN": "the environment given"}, rec)
    check("the child runs in the environment it is given",
          "".join(text for what, text in rec.events if what == "feed"), "the environment given\n")
    rec = _Rec()
    with mock.patch.object(ml, "TICK", 30.0):
        ml.run_relayed([sys.executable, "-c", ("import sys, time\nfor c in 'xyz':\n"
                                               "    sys.stdout.write(c); sys.stdout.flush(); time.sleep(0.1)\n")],
                       Path.cwd(), dict(os.environ), rec)
    check("a child that never goes quiet: ticked after each piece all the same",
          [e for e in rec.events if e != ("feed", "")],
          [("feed", "x"), ("tick", ""), ("feed", "y"), ("tick", ""), ("feed", "z"), ("tick", ""), ("close", "")])


# A child that leaves a process of its own holding the pipe, for 5 s, and ends.
_ORPHANING = ("import subprocess, sys\n"
              "subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(5)'])\n"
              "print('the child is done', flush=True)\n")


def test_a_child_that_leaves_the_pipe_held_does_not_hold_the_run() -> None:
    rec = _Rec()
    began = time.monotonic()
    with mock.patch.object(ml, "TICK", 0.05):
        rc = ml.run_relayed([sys.executable, "-c", _ORPHANING], Path.cwd(), dict(os.environ), rec)
    check("the child's exit and output, long before what it left behind ends",
          (rc, "".join(text for what, text in rec.events if what == "feed"), time.monotonic() - began < 3),
          (0, "the child is done\n", True))
    check("  and closed, last", rec.events[-1], ("close", ""))
    # In a process of its own: the reader still blocked on the held pipe must
    # not keep that process from exiting.
    script = (f"import sys; sys.path.insert(0, {str(ROOT)!r})\n"
              "from jpcheck import mutmut_log as ml\n"
              "ml.TICK = 0.05\n"
              "class R:\n    def feed(self, t): pass\n    def tick(self): pass\n    def close(self): pass\n"
              f"sys.exit(ml.run_relayed([sys.executable, '-c', {_ORPHANING!r}], __import__('pathlib').Path.cwd(),"
              " dict(__import__('os').environ), R()))\n")
    began = time.monotonic()
    done = subprocess.run([sys.executable, "-c", script], capture_output=True, text=True, check=False, timeout=30)
    check("  a process running it exits then too", (done.returncode, done.stderr, time.monotonic() - began < 3),
          (0, "", True))


# A child that leaves a process of its own writing to the pipe every 0.05 s
# for 5 s, and ends.
_CHATTY = ("import subprocess, sys\n"
           "subprocess.Popen([sys.executable, '-c', 'import sys, time\\n"
           "for _ in range(100):\\n    print(\"left\", flush=True); time.sleep(0.05)'])\n"
           "print('the child is done', flush=True)\n")


def test_a_child_that_leaves_a_writer_behind_does_not_hold_the_run() -> None:
    """What it left behind is never quiet for a TICK, so the run ends a TICK
    after the child does, whatever still comes."""
    rec = _Rec()
    began = time.monotonic()
    with mock.patch.object(ml, "TICK", 0.2):
        rc = ml.run_relayed([sys.executable, "-c", _CHATTY], Path.cwd(), dict(os.environ), rec)
    fed = "".join(text for what, text in rec.events if what == "feed")
    check("the child's exit and output, long before what it left behind stops",
          (rc, "the child is done\n" in fed,
           time.monotonic() - began < 3), (0, True, True))
    check("  and closed, last", rec.events[-1], ("close", ""))


class _Broken(_Rec):
    """A relay that fails on the first output it is given."""

    def feed(self, text: str) -> None:
        if text:
            raise RuntimeError("the relay broke")


def _stopped(script: str, td: str) -> tuple[str, float, int]:
    """Run SCRIPT under run_relayed with a relay that breaks: what was raised,
    how long it took, and the child's pid (the child writes it to pid)."""
    began = time.monotonic()
    try:
        ml.run_relayed([sys.executable, "-c", script], Path(td), dict(os.environ), _Broken())
        raised = ""
    except RuntimeError as e:
        raised = str(e)
    return raised, time.monotonic() - began, int((Path(td) / "pid").read_text(encoding="utf-8"))


def _gone(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except ProcessLookupError:  # reason: no such process is the answer asked for
        return True
    return False


def test_a_broken_relay_stops_the_child() -> None:
    """The relay failing while the child runs: the child is interrupted (and
    killed if it ignores that), the failure raised, nothing left running."""
    head = ("import os, signal, sys, time\n"
            "open('pid', 'w').write(str(os.getpid()))\n")
    tail = "sys.stdout.write('out\\n'); sys.stdout.flush(); time.sleep(30)\n"
    with tempfile.TemporaryDirectory() as td:
        raised, took, pid = _stopped(head + "def stop(*a):\n    open('interrupted', 'w').write('yes')\n"
                                     "    sys.exit(0)\nsignal.signal(signal.SIGINT, stop)\n" + tail, td)
        check("an interrupt it heeds: the relay's failure raised, the child interrupted, at once",
              (raised, (Path(td) / "interrupted").exists(), took < 10, _gone(pid)),
              ("the relay broke", True, True, True))
    with tempfile.TemporaryDirectory() as td, mock.patch.object(ml, "STOP_WAIT", 0.5):
        raised, took, pid = _stopped(head + "signal.signal(signal.SIGINT, signal.SIG_IGN)\n" + tail, td)
        check("an interrupt it ignores: killed after STOP_WAIT", (raised, took < 10, _gone(pid)),
              ("the relay broke", True, True))


if __name__ == "__main__":
    sys.exit(run(globals(), "mutmut log"))
