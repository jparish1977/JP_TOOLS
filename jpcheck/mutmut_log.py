"""mutmut's output as mutmut-gate.py tells it, in a log a person can read.

mutmut 3.8 draws its phases and its progress as frames of one terminal line,
each a spinner character, a space and the text, redrawn after a carriage
return (mutmut/ui/terminal.py) many times a second; a CI log keeps every
frame as a line. The progress text is print_stats' in mutmut/stats.py.
Mutation testing is announced by a plain line instead, and after a named run
mutmut lists each named mutant's result, one line each, under a heading.

Relay reads that output and says instead:

- each phase once, by what it works on and why, as the run's Plan words it;
- mutation testing's progress each tenth of the way and at least each
  minute, counted from where testing began, with the functions still open;
- while mutmut is quiet, a line each minute naming what it is still doing,
  so a quiet log means a dead run, never a slow one;
- every other line mutmut writes (a warning, an error, a traceback) whole.

The named run's list of results is counted and left out: `mutmut results`
has it. Every line of the gate's own is stamped with the UTC time and
flushed when it is said.
"""

import codecs
import io
import queue
import re
import signal
import subprocess
import threading
import time
from collections import Counter
from collections.abc import Callable
from pathlib import Path
from typing import Protocol, cast

SPINNER = "⠋⠙⠹⠸⠼⠴⠦⠧⠇⠏"
COUNTER = re.compile(r"(\d+)/(\d+)  🎉 (\d+) 🫥 (\d+)  ⏰ (\d+)  🤔 (\d+)  🙁 (\d+)  🔇 (\d+)  🧙 (\d+)")
TESTING = "Running mutation testing"
LISTED = re.compile(r"\S+ \S+__mutmut_\d+")
LISTING = ("Mutant results", "--------------")
# The counter's fields after judged/total, in order, as the progress line names them.
TALLIES = ("killed", "no tests", "timeout", "suspicious", "survived", "skipped", "caught by type check")
# The progress line says these even at 0; the others only when they happen.
ALWAYS = ("killed", "survived", "no tests", "timeout")
# Progress lines across the mutants a run tests: one each tenth of the way.
STEPS = 10
# Seconds between the progress lines said when nothing else was.
EVERY = 60.0
# Seconds to wait for mutmut's output before checking whether a line is due.
TICK = 1.0
# Seconds mutmut has to stop its workers after an interrupt before it is killed.
STOP_WAIT = 30.0


def say(message: str) -> None:
    """One line of the gate's own, stamped with the UTC time and flushed at
    once: a CI log shows it when it happens, not when the run ends."""
    print(f"mutmut-gate {time.strftime('%H:%M:%SZ', time.gmtime())}: {message}", flush=True)


def sources_line(kept: int, changed: list[str] | None) -> str:
    """What mutmut will rebuild mutants for: the KEPT source files' count,
    and those CHANGED since the state was judged, the first five named; None:
    there is no commit to say which (mutmut then decides by file times)."""
    if changed is None:
        return f"{kept} source files kept, no state commit to say which changed"
    named = f" ({', '.join(changed[:5])}{', ...' if len(changed) > 5 else ''})" if changed else ""
    return f"{kept} source files kept, {len(changed)} changed since the state{named}"


def code_reasons(before: dict[str, str], now: dict[str, str]) -> dict[str, str]:
    """Why each function's mutants are tested in the first run, by mutmut's
    code hashes BEFORE it and NOW: its code is new, or changed; a function
    tested then for neither had no verdict kept."""
    return {fn: "new" if fn not in before else "code changed" for fn, h in now.items() if before.get(fn) != h}


class Plan:
    """What one mutmut run works on and why, so each phase line can say it.
    CODES reads every mutant's exit code as mutmut keeps it now (None: no
    verdict); NAMES are the mutants a named run tests (none: an unnamed run,
    every mutant with no verdict); TRACED says which tests' reach is traced
    again; SOURCES which source files are rebuilt; WHY each function's reason
    to be tested, read when testing begins (a function it does not name had
    no verdict kept)."""

    def __init__(self, codes: Callable[[], dict[str, int | None]], names: list[str], traced: str,
                 sources: str, why: Callable[[], dict[str, str]]) -> None:
        self.codes = codes
        self.names = names
        self.traced = traced
        self.sources = sources
        self.why = why
        self.tested: dict[str, list[str]] = {}

    def subject(self, title: str) -> str:
        """The line said for mutmut's phase TITLE."""
        if title == TESTING:
            return self._testing()
        clean = (f"the {len(self.names)} named mutants' tests" if self.names
                 else "the whole suite in one process")
        lines = {
            "Generating mutants": f"generating mutants: {self.sources}",
            "Listing all tests": "listing tests: every test pytest collects, to find the ones not traced yet",
            "Running stats": f"tracing reach: {self.traced}",
            "Running clean tests": f"clean run: {clean}, which must pass so a mutant's failure is its own",
            "Running forced fail test": "forced-fail check: a test made to fail must fail, "
                                        "or no verdict can be trusted",
        }
        return lines.get(title, f"mutmut: {title}")

    def _testing(self) -> str:
        """Read which mutants this run tests, by function, and say so with
        each one's reason, biggest first."""
        wanted = set(self.names)
        self.tested = {}
        for k, code in self.codes().items():
            if k in wanted if wanted else code is None:
                self.tested.setdefault(k.partition("__mutmut_")[0], []).append(k)
        known = self.why()
        why = {fn: known.get(fn, "no verdict kept") for fn in self.tested}
        biggest = sorted(self.tested, key=lambda fn: (-len(self.tested[fn]), fn))
        each = ", ".join(f"{len(self.tested[fn]):,} in {fn} ({why[fn]})" for fn in biggest[:10])
        more = f", and {len(biggest) - 10} more" if len(biggest) > 10 else ""
        counted = ", ".join(f"{r}: {n}" for r, n in sorted(Counter(why.values()).items()))
        total = sum(map(len, self.tested.values()))
        return f"testing {total:,} mutants in {len(self.tested)} functions ({counted}): {each}{more}"

    def open_functions(self) -> str:
        """How many tested functions are done, and the biggest still open."""
        codes = self.codes()
        left = {fn: n for fn, keys in self.tested.items() if (n := sum(codes.get(k) is None for k in keys))}
        named = "".join(f", {fn} ({left[fn]} left)" for fn in sorted(left, key=lambda fn: (-left[fn], fn))[:3])
        return f"{len(self.tested) - len(left)} of {len(self.tested)} functions done{named}"


class _Subjects(Protocol):
    def subject(self, title: str) -> str: ...

    def open_functions(self) -> str: ...


class Relay:
    """mutmut's output, fed in pieces as it comes, said as the module
    docstring describes; CLOCK is seconds, for the minute between lines."""

    def __init__(self, plan: _Subjects, clock: Callable[[], float] = time.monotonic) -> None:
        self.plan = plan
        self.clock = clock
        self.partial = ""
        self.phase = ""
        self.doing = "starting mutmut"
        self.began = self.said = clock()
        self.start: list[int] = []
        self.now: list[int] = []
        self.step = 0
        self.listed = 0

    def feed(self, text: str) -> None:
        *lines, self.partial = re.split(r"[\r\n]", self.partial + text)
        for line in lines:
            self._line(line)
        # A frame ends only when the next one starts, so the latest counter is
        # read now, while it is the unended rest (mutmut writes a frame whole,
        # in one write a pipe passes whole); counting it again when it ends
        # changes nothing. A phase name waits for its end.
        if self.partial[:1] in SPINNER and COUNTER.fullmatch(self.partial[2:].strip()):
            self._frame(self.partial[2:].strip())

    def close(self) -> None:
        """The end of mutmut's output: the unended rest, and what was left out."""
        self._line(self.partial)
        self.partial = ""
        if self.listed:
            self._say(f"mutmut listed the {self.listed:,} named mutants' results one per line: "
                      "left out here, `mutmut results` has them")
            self.listed = 0

    def tick(self) -> None:
        """Say how far it has got when nothing was said for EVERY seconds."""
        if self.clock() - self.said < EVERY:
            return
        if self.now:
            self._progress()
        else:
            self._say(f"still {self.doing}, {int(self.clock() - self.began)} s in")

    def _say(self, message: str) -> None:
        say(message)
        self.said = self.clock()

    def _line(self, line: str) -> None:
        text = line.strip()
        if line[:1] and line[0] in SPINNER and line[1:2] == " ":
            self._frame(text[2:])
        elif text == TESTING:
            self._phase(TESTING)
        elif LISTED.fullmatch(text):
            self.listed += 1
        elif text and text not in LISTING:
            print(line, flush=True)

    def _frame(self, text: str) -> None:
        counter = COUNTER.fullmatch(text)
        if not counter:
            if text != self.phase:
                self._phase(text)
            return
        self.now = [int(n) for n in counter.groups()]
        self.start = self.start or self.now
        done, todo = self.now[0] - self.start[0], self.start[1] - self.start[0]
        if done and done * STEPS >= (self.step + 1) * todo:
            self.step = done * STEPS // todo
            self._progress()

    # The plan reads mutmut's .meta files while mutmut rewrites them, so a read
    # can meet one half written. What the log could not read it says, and the
    # run goes on: the log never stops the run it describes.

    def _phase(self, title: str) -> None:
        self.phase = title
        try:
            self.doing = self.plan.subject(title)
        except (OSError, ValueError) as e:  # reason: said in the line; the next read is whole
            self.doing = f"mutmut: {title} (what it works on could not be read: {e})"
        self.began = self.clock()
        self._say(self.doing)

    def _progress(self) -> None:
        start, now = self.start, self.now
        done = {t: now[i] - start[i] for i, t in enumerate(TALLIES, 2)}
        tallies = ", ".join(f"{done[t]:,} {t}" for t in TALLIES if t in ALWAYS or done[t])
        try:
            functions = self.plan.open_functions()
        except (OSError, ValueError) as e:  # reason: said in the line; the next read is whole
            functions = f"functions open could not be read: {e}"
        self._say(f"{now[0] - start[0]:,} / {start[1] - start[0]:,} judged: {tallies}; {functions}")


class _Fed(Protocol):
    def feed(self, text: str) -> None: ...

    def tick(self) -> None: ...

    def close(self) -> None: ...


def _drain(pipe: io.BufferedReader, chunks: "queue.Queue[bytes]") -> None:
    """Each piece of PIPE as it arrives, then b"" at its end."""
    while chunk := pipe.read1():
        chunks.put(chunk)
    chunks.put(b"")


def run_relayed(cmd: list[str], cwd: Path, env: dict[str, str], relay: _Fed) -> int:
    """Run CMD with its output, stdout and stderr as one, through RELAY; its
    exit code. The output is read on a thread so RELAY's tick runs each TICK
    seconds while CMD is silent, and after each piece while it is not."""
    proc = subprocess.Popen(cmd, cwd=cwd, env=env, stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
    chunks: queue.Queue[bytes] = queue.Queue()
    threading.Thread(target=_drain, args=(cast(io.BufferedReader, proc.stdout), chunks)).start()
    try:
        _relay_until_done(chunks, relay)
    except BaseException:
        _stop(proc)
        raise
    return proc.wait()


def _relay_until_done(chunks: "queue.Queue[bytes]", relay: _Fed) -> None:
    decoder = codecs.getincrementaldecoder("utf-8")("replace")
    while True:
        try:
            chunk = chunks.get(timeout=TICK)
        except queue.Empty:
            relay.tick()
            continue
        if not chunk:
            break
        relay.feed(decoder.decode(chunk))
        relay.tick()
    relay.feed(decoder.decode(b"", final=True))
    relay.close()


def _stop(proc: "subprocess.Popen[bytes]") -> None:
    """Stop PROC when its output can no longer be read: SIGINT first, which
    mutmut takes as an interrupt and stops its workers on, then SIGKILL after
    STOP_WAIT seconds. Left running, it would go on unwatched with its reader
    holding the gate open until it ended."""
    proc.send_signal(signal.SIGINT)
    try:
        proc.wait(timeout=STOP_WAIT)
    except subprocess.TimeoutExpired:
        proc.kill()
        proc.wait()
