#!/usr/bin/env python3
"""
JP_TOOLS/tests/test_run_watched.py
platformtools.tools.run_watched runs a tool and never waits on it forever,
and chdtool's chdman() runs chdman under it.

    - A tool that ends: its output and exit status, as run() gives them,
      without waiting out a poll.
    - One whose output stops growing (or never appears) for STALL seconds,
      or that runs past LIMIT: killed, and StalledError says which.
    - One that keeps writing is not stalled; one that talks enough to fill
      a pipe is not blocked by it.
    - chdman(): watches the file after --output; a stall is run once more
      with its partial output removed, said on stderr; a second raises,
      and chd.py says so and exits 1.

Real child processes (Python), with stalls of a fraction of a second.

    python3 tests/test_run_watched.py
"""

import io
import os
import subprocess
import sys
import tempfile
import threading
import time
from pathlib import Path
from unittest import mock

from _harness import check, check_true, load_script, run
from chdtool import chdman as chdman_mod
from platformtools.tools import StalledError, _sizes, _watch, run_watched
from platformtools.tools import run as run_tool


def _watched(code: str, watch: Path | None, stall: float = 0.6, limit: float = 30.0) -> tuple[object, float]:
    """(the CompletedProcess, or the StalledError's message), and the seconds it took."""
    t = time.monotonic()
    try:
        result: object = run_watched([sys.executable], ["-c", code], watch=watch, stall=stall, limit=limit,
                                     poll=0.05)
    except StalledError as e:
        result = str(e)
    return result, time.monotonic() - t


def test_run() -> None:
    """run(), which chdman() no longer goes through: the command, text, both
    pipes captured, and a failure returned rather than raised."""
    code = "import sys; print('o'); print('e', file=sys.stderr); sys.exit(4)"
    r = run_tool([sys.executable, "-c"], [code])
    check("the prefix then the arguments; exit status, stdout and stderr as text, nothing raised",
          (r.args, r.returncode, r.stdout, r.stderr), ([sys.executable, "-c", code], 4, "o\n", "e\n"))
    r = run_tool([sys.executable, "-c"], ["pass"], capture=False)
    check("  capture=False: nothing captured", (r.returncode, r.stdout, r.stderr), (0, None, None))


def test_a_tool_that_ends() -> None:
    code = "import sys; print('o1'); print('o2'); print('e1', file=sys.stderr); print('e2', file=sys.stderr); sys.exit(3)"
    r, took = _watched(code, None)
    check("its command, exit status, stdout and stderr, every line, as run() gives them",
          (r.args, r.returncode, r.stdout, r.stderr) if isinstance(r, subprocess.CompletedProcess) else r,
          ([sys.executable, "-c", code], 3, "o1\no2\n", "e1\ne2\n"))
    t = time.monotonic()
    quick = run_watched([sys.executable], ["-c", "pass"], watch=None, stall=5, limit=5)
    took = time.monotonic() - t
    check_true(f"  without waiting out its poll of 1 s (took {took:.2f} s)", quick.returncode == 0 and took < 0.9)


def test_a_tool_that_stalls() -> None:
    with tempfile.TemporaryDirectory() as td:
        out = Path(td) / "o"
        code = f"import time; open({str(out)!r}, 'w').write('h'); time.sleep(3)"
        r, took = _watched(code, out)
        check("its output stops growing: killed after STALL, the program, its first argument and why said",
              (r, 0.5 < took < 2.5),
              (f"{Path(sys.executable).name} -c killed: {out} did not grow for 0.6 s", True))
        r, took = _watched("import time; time.sleep(3)", Path(td) / "never")
        check_true(f"  its output never appears: killed the same way (took {took:.2f} s)",
                   isinstance(r, str) and r.endswith(f"{Path(td) / 'never'} did not grow for 0.6 s") and took < 2.5)
        t = time.monotonic()
        try:
            run_watched([sys.executable, "-c", "import time; time.sleep(3)"], [], watch=None, stall=1, limit=0.2,
                        poll=0.05)
            said = ""
        except StalledError as e:
            said = str(e)
        check("  a command given whole, no arguments after it: the program alone named",
              said, f"{Path(sys.executable).name} killed: it ran past 0.2 s")
        t = time.monotonic()
        try:
            run_watched([sys.executable], ["-c", "import time; time.sleep(3)"], watch=Path(td) / "never", stall=0.1,
                        limit=30)
            said = ""
        except StalledError as e:
            said = str(e)
        took = time.monotonic() - t
        check_true(f"  looked at every second by default: a 0.1 s stall seen at the first look (took {took:.2f} s)",
                   said.endswith("did not grow for 0.1 s") and 0.9 < took < 1.8)
        grow = (f"import time\nf = open({str(out)!r}, 'w')\n"
                "for _ in range(12):\n    f.write('x'); f.flush(); time.sleep(0.15)\n")
        r, took = _watched(grow, out)
        check("one that keeps writing, slower than a poll but faster than STALL: runs to its end",
              (r.returncode if isinstance(r, subprocess.CompletedProcess) else r, took > 1.5), (0, True))
        cue, track = Path(td) / "game.cue", Path(td) / "game.bin"
        cd = (f"import time\nopen({str(cue)!r}, 'w').write('FILE')\nf = open({str(track)!r}, 'w')\n"
              "for _ in range(12):\n    f.write('x'); f.flush(); time.sleep(0.15)\n")
        r, took = _watched(cd, cue)
        check("a CD extract: the cue written whole, then its bin beside it growing for longer than STALL: runs "
              "to its end", (r.returncode if isinstance(r, subprocess.CompletedProcess) else r, took > 1.5), (0, True))
        r, took = _watched(grow, out, stall=30, limit=0.5)
        check_true(f"  past LIMIT, though it writes: killed, saying so (took {took:.2f} s)",
                   isinstance(r, str) and r.endswith("killed: it ran past 0.5 s") and took < 1.5)
        r, took = _watched("import time; time.sleep(3)", None, stall=0.1, limit=0.5)
        check_true(f"  with nothing to watch, only LIMIT: killed at it, not at STALL (took {took:.2f} s)",
                   isinstance(r, str) and r.endswith("killed: it ran past 0.5 s") and 0.5 <= took < 2.5)


def test_sizes() -> None:
    with tempfile.TemporaryDirectory() as td:
        d = Path(td)
        check("no directory yet: nothing written", _sizes(d / "none" / "out.cue"), ())
        check("  none of its files yet: the same", _sizes(d / "out.cue"), ())
        for name, data in (("out.cue", "c"), ("out01.bin", "tt"), ("out.bin", "bbb"), ("other.bin", "o")):
            (d / name).write_text(data, encoding="utf-8")
        check("the output and every file beside it under its name, sorted, by size; another name not",
              _sizes(d / "out.cue"), (("out.bin", 3), ("out.cue", 1), ("out01.bin", 2)))
        # A listing taken while out01.bin was there, sized after it went;
        # listed first, so the files after it must still be sized.
        entries = sorted(os.scandir(d), key=lambda e: e.name != "out01.bin")
        (d / "out01.bin").unlink()
        with mock.patch("platformtools.tools.os.scandir", return_value=entries):
            check("  a file gone between the listing and its size: left out, the rest kept",
                  _sizes(d / "out.cue"), (("out.bin", 3), ("out.cue", 1)))
    with tempfile.TemporaryDirectory() as td:
        d = Path(td)
        names = ("Sonic.cue", "Sonic.bin", "Sonic01.bin", "Sonic03.raw", "Sonic (Track 1).bin",
                 "Sonic (Track 12).bin", "Sonic 2.cue", "Sonic 2.bin", "Sonic & Knuckles.cue", "Sonic2.bin",
                 "Sonic123.bin", "Sonic01.iso", "Sonic (Track 1).iso", "Sonic.bin.part")
        for name in names:
            (d / name).write_text("x", encoding="utf-8")
        check("chdman's names for a disc's data, and no other disc's that only begins the same way: "
              "STEM.ext, a gdi's STEMNN.bin or .raw, a split cue's 'STEM (Track N).bin'",
              [n for n, _ in _sizes(d / "Sonic.cue")],
              ["Sonic (Track 1).bin", "Sonic (Track 12).bin", "Sonic.bin", "Sonic.cue", "Sonic01.bin", "Sonic03.raw"])


class _Clock:
    """time.monotonic for _watch: 0, 1, 2, ... one tick a read."""

    def __init__(self) -> None:
        self.now = -1

    def __call__(self) -> float:
        self.now += 1
        return float(self.now)


def test_the_boundaries() -> None:
    """_watch on a clock of whole seconds: a stall or a limit reached exactly
    is reached."""
    # Set in 2 s: a _watch that waited without its poll would return "" then,
    # not hang the run.
    never = threading.Event()
    threading.Timer(2, never.set).start()
    with tempfile.TemporaryDirectory() as td:
        for stall, limit, want in ((2, 100, f"{Path(td) / 'f'} did not grow for 2 s"), (100, 2, "it ran past 2 s")):
            clock = _Clock()
            with mock.patch("platformtools.tools.time.monotonic", clock):
                why = _watch(never, Path(td) / "f", stall, limit, 0)
            check(f"stall {stall}, limit {limit}: said when the clock reads exactly 2", (why, clock.now), (want, 2))
    done = threading.Event()
    done.set()
    check("a tool already ended: nothing to say", _watch(done, None, 1, 1, 0), "")


def test_a_tool_that_talks() -> None:
    r, _ = _watched("import sys; sys.stdout.write('x' * 1_000_000); sys.stderr.write('y' * 1_000_000)", None)
    check("a megabyte on each pipe: both drained while it runs, none lost",
          (len(r.stdout), len(r.stderr)) if isinstance(r, subprocess.CompletedProcess) else r, (1_000_000, 1_000_000))


def test_chdman_watches_its_output_and_retries_once() -> None:
    with tempfile.TemporaryDirectory() as td:
        out = Path(td) / "b.chd"
        calls: list[tuple[list[str], list[str], Path | None, float, float]] = []
        stalls: list[str] = []
        ok: subprocess.CompletedProcess[str] = subprocess.CompletedProcess(["chdman"], 0, "", "")

        def fake(prefix: list[str], args: list[str], *, watch: Path | None, stall: float,
                 limit: float) -> subprocess.CompletedProcess[str]:
            """Writes a partial output; stalls with each of STALLS in turn, then succeeds."""
            calls.append((prefix, args, watch, stall, limit))
            out.write_bytes(b"partial")
            if stalls:
                raise StalledError(stalls.pop(0))
            return ok

        args = ["createraw", "--input", "b.img", "--output", str(out)]
        call = (["/x/chdman"], args, out, 600.0, 21600.0)
        err = io.StringIO()
        with mock.patch.object(chdman_mod, "require", return_value=["/x/chdman"]) as require, \
                mock.patch.object(chdman_mod, "run_watched", fake), mock.patch("sys.stderr", err):
            check("a call that ends: its result; chdman as require finds it, Windows' usual folders "
                  "included; the file after --output watched, at 600 s and 6 h",
                  (chdman_mod.chdman(args), calls, require.call_args.args),
                  (ok, [call], ("chdman", chdman_mod.CHDMAN_HINT, chdman_mod.WINDOWS_CHDMAN)))
            calls.clear()
            check("  info, which writes no file: nothing watched", (chdman_mod.chdman(["info"]), calls[0][2]),
                  (ok, None))
            calls.clear()
            removed: list[bool] = []
            real_discard = chdman_mod._discard

            def discard(p: Path | None) -> None:
                removed.append(p is not None and p.exists())
                real_discard(p)

            with mock.patch.object(chdman_mod, "_discard", discard):
                stalls[:] = ["chdman createraw killed: stuck"]
                r = chdman_mod.chdman(args)
            check("a stall: its partial output removed, said, and run once more, the same call",
                  (r, calls, removed, err.getvalue()),
                  (ok, [call, call], [True], ("chd: chdman createraw killed: stuck; its partial output removed, "
                                              "running it once more\n")))
            calls.clear()
            stalls[:] = ["one", "two"]
            try:
                chdman_mod.chdman(args)
                raised = ""
            except StalledError as e:
                raised = str(e)
            check("  two stalls: the second raised, and no partial output left", (raised, out.exists()),
                  ("two", False))
        writes: list[bool] = []

        def silent(prefix: list[str], args: list[str], *, watch: Path | None, stall: float,
                   limit: float) -> subprocess.CompletedProcess[str]:
            """Stalls before writing anything, then succeeds."""
            writes.append(out.exists())
            if len(writes) == 1:
                raise StalledError("chdman createraw killed: stuck before writing")
            return ok

        with mock.patch.object(chdman_mod, "require", return_value=["chdman"]), \
                mock.patch.object(chdman_mod, "run_watched", silent), mock.patch("sys.stderr", io.StringIO()):
            check("a stall before any output was written: nothing to remove, run once more",
                  (chdman_mod.chdman(args), writes), (ok, [False, False]))


def test_chd_says_a_stall_and_exits_1() -> None:
    chd = load_script("chd.py")
    out = io.StringIO()
    with mock.patch.object(chd.commands, "verify", side_effect=StalledError("chdman verify killed: x")), \
            mock.patch("sys.stdout", out):
        rc = chd.main(["verify", "a.chd"])
    check("chd.py: the stall said, exit 1", (rc, out.getvalue()),
          (1, "Error: chdman verify killed: x, twice; nothing was written.\n"))
    out = io.StringIO()
    with mock.patch("sys.stdout", out):
        rc = chd.main([])
    check("  and with no command: its help, exit 0", (rc, out.getvalue().startswith("usage:")), (0, True))


if __name__ == "__main__":
    sys.exit(run(globals(), "run watched"))
