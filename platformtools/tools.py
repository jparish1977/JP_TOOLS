"""Finding and running external programs: on PATH, in a Windows folder, or in WSL."""

from __future__ import annotations

import os
import re
import shutil
import subprocess
import sys
import threading
import time
from collections.abc import Sequence
from pathlib import Path
from typing import IO


class ToolMissingError(Exception):
    """A program the command needs is not installed. `hint` says how to get it."""

    def __init__(self, name: str, hint: str) -> None:
        super().__init__(name)
        self.name = name
        self.hint = hint


def find_tool(name: str, windows_paths: Sequence[Path] = (), *,
              windows_first: bool = True) -> list[str] | None:
    """The argv prefix that runs NAME, or None when it is not installed.

    Looks on PATH and, on Windows, in WINDOWS_PATHS (the folders a download
    that puts nothing on PATH usually lands in) and inside WSL. WINDOWS_PATHS
    are tried before PATH when WINDOWS_FIRST, after WSL otherwise.
    """
    windows = sys.platform == "win32"
    native = next((str(p) for p in windows_paths if p.exists()), None) if windows else None
    if native and windows_first:
        return [native]
    path = shutil.which(name)
    if path:
        return [path]
    in_wsl = _in_wsl(name) if windows else None
    if in_wsl:
        return in_wsl
    return [native] if native else None


def _in_wsl(name: str) -> list[str] | None:
    try:
        result = subprocess.run(
            ["wsl", "which", name], capture_output=True, text=True, check=False
        )
    except FileNotFoundError:
        # reason: no WSL on this box, so the tool is not inside it either
        return None
    found = result.stdout.strip()
    return ["wsl", found] if result.returncode == 0 and found else None


def require(name: str, hint: str, windows_paths: Sequence[Path] = ()) -> list[str]:
    """The argv prefix that runs NAME. Raises ToolMissingError when it is not installed."""
    prefix = find_tool(name, windows_paths)
    if prefix is None:
        raise ToolMissingError(name, hint)
    return prefix


def run(prefix: list[str], args: list[str], *, capture: bool = True) -> subprocess.CompletedProcess[str]:
    """Run a tool. The caller reads the exit status; a failure is not an exception."""
    return subprocess.run([*prefix, *args], capture_output=capture, text=True, check=False)


class StalledError(Exception):
    """A tool killed for doing nothing: the file it writes stopped growing,
    or it ran past its limit. The message says which."""


def _sizes(path: Path) -> tuple[tuple[str, int], ...]:
    """PATH and the files a tool writes beside it under its name, by name
    and size, sorted; empty while there are none. chdman extracting a CD
    writes the cue (or toc, gdi) whole, then its data, named as chdman.cpp
    names it with no --outputbin: STEM.bin; a gdi's STEMNN.bin or .raw; a
    split cue's "STEM (Track N).bin". Not any name that only begins with
    STEM: "Sonic 2.cue" is another disc beside "Sonic.cue"."""
    data = re.compile(re.escape(path.stem) + r"(\.[^.]+|\d{2}\.(bin|raw)| \(Track \d{1,2}\)\.bin)")
    try:
        entries = list(os.scandir(path.parent))
    except FileNotFoundError:  # reason: no directory yet: nothing written, an empty state of its own
        return ()
    out = []
    for e in entries:
        if e.name == path.name or data.fullmatch(e.name):
            try:
                out.append((e.name, e.stat().st_size))
            except FileNotFoundError:  # reason: gone since it was listed: not written, as a later look will find
                continue
    return tuple(sorted(out))


def _drain(stream: IO[str], into: list[str]) -> None:
    with stream:
        into.extend(stream)


def _watch(ended: threading.Event, watch: Path | None, stall: float, limit: float, poll: float) -> str:
    """Wait for ENDED, looking every POLL seconds: why the tool must be
    killed (neither WATCH nor a file beside it under its name (_sizes)
    changed for STALL seconds; it ran past LIMIT), or "" once it has ended
    by itself. Nothing written yet is where a tool starts."""
    start = grew = time.monotonic()
    size: tuple[tuple[str, int], ...] = ()
    while not ended.wait(poll):
        now = time.monotonic()
        if watch is not None and (current := _sizes(watch)) != size:
            size, grew = current, now
        elif watch is not None and now - grew >= stall:
            return f"{watch} did not grow for {stall:g} s"
        if now - start >= limit:
            return f"it ran past {limit:g} s"
    return ""


def run_watched(prefix: list[str], args: list[str], *, watch: Path | None, stall: float, limit: float,
                poll: float = 1.0) -> subprocess.CompletedProcess[str]:
    """Run a tool as run() does, captured, but never wait on it forever: one
    whose WATCH file, and the files beside it under its name, do not change
    (or appear) for STALL seconds, or that
    runs past LIMIT seconds, is killed and StalledError raised, saying which
    (_watch). Without WATCH only LIMIT applies. Checked every POLL seconds,
    and a tool that ends returns at once, not at the next look.

    Both pipes are drained while it runs: a tool that filled one would
    block writing while this waited on it, a hang of this function's own.
    Every thread here ends with the tool, so none is a daemon."""
    cmd = [*prefix, *args]
    proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    out: list[str] = []
    err: list[str] = []
    pumps = [threading.Thread(target=_drain, args=(proc.stdout, out)),
             threading.Thread(target=_drain, args=(proc.stderr, err))]
    for t in pumps:
        t.start()
    ended = threading.Event()

    def wait() -> None:
        proc.wait()
        ended.set()

    threading.Thread(target=wait).start()
    why = _watch(ended, watch, stall, limit, poll)
    if why:
        proc.kill()
    proc.wait()
    for t in pumps:
        t.join()
    if why:
        raise StalledError(f"{' '.join([Path(cmd[0]).name, *args[:1]])} killed: {why}")
    return subprocess.CompletedProcess(cmd, proc.returncode, "".join(out), "".join(err))
