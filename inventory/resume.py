"""Resuming a TSV inventory that is written one top-level entry at a time.

Each finished top-level entry gets a `#done<TAB>name` line. On resume, entries
with one are skipped, and the rows of an entry without one (a run cut off in
the middle of it) are dropped so it is recorded again, whole and once. An
inventory with no `#done` lines at all predates the markers: every entry in it
counts as finished, since there is no way to tell which were cut off.

Rows are `path<TAB>...`; the top-level entry is the path's first component,
with or without a leading "/".
"""

from __future__ import annotations

import os
from typing import TextIO

DONE = "#done\t"


def top(row: str) -> str:
    return row.split("\t", 1)[0].lstrip("/").split("/", 1)[0]


def mark_done(out: TextIO, name: str) -> None:
    out.write(f"{DONE}{name}\n")
    out.flush()


def _drop_unfinished(path: str, lines: list[str], done: set[str]) -> None:
    """Rewrite PATH without the rows of entries not in DONE, when it has any."""
    kept = [line for line in lines if line.startswith("#") or top(line) in done]
    if len(kept) == len(lines):
        return
    dropped = sorted({top(line) for line in lines if not line.startswith("#")} - done)
    print(f"re-recording unfinished: {', '.join(dropped)}")
    tmp = f"{path}.tmp"
    with open(tmp, "w", encoding="utf-8", errors="surrogateescape") as out:
        out.writelines(kept)
    os.replace(tmp, path)


def resume_state(path: str) -> set[str]:
    """Finished top-level entries in the inventory at PATH; drops unfinished ones' rows."""
    if not os.path.exists(path):
        return set()
    with open(path, encoding="utf-8", errors="surrogateescape") as handle:
        lines = [line for line in handle if line.strip()]
    done = {line[len(DONE):].rstrip("\n") for line in lines if line.startswith(DONE)}
    if not done:
        return {top(line) for line in lines if not line.startswith("#")}
    _drop_unfinished(path, lines, done)
    return done
