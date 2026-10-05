#!/usr/bin/env python3
"""Inventory an unmountable NTFS volume via ntfsls: metadata only.

The lightest operation available on a failing drive: directory entries only,
no file data. Written as "path<TAB>size" one top-level entry at a time and
resumable per entry (inventory/resume.py): an entry cut off by a disconnect,
or one with a directory that could not be listed (a failed or timed-out
listing, or one below the depth limit), is not marked done, so the next run
re-reads it whole.

Exit codes (jpexit): 0 OK, every entry recorded whole; 2 NOT_RUN, a usage
error, or a directory not read, each named.

usage: ntfs-inventory.py DEVICE OUTPUT.tsv [DIR ...]

  DEVICE   the NTFS partition, e.g. /dev/sdb3, or an image file; it does NOT
           need to mount
  OUTPUT   TSV written as "path<TAB>size"; appended to and resumable
  DIR      optional top-level entries to limit the pass to

Needs ntfsls (ntfs-3g), and root or passwordless sudo for a raw device. Run
unprivileged against a device, this is one `sudo ntfsls` PER DIRECTORY, each
logged: a full walk measured 285,471 of them and grew auth.log to 145 MB. So
run the script itself under sudo, after which no listing needs its own sudo;
the TSV is handed back to the invoking user.
"""

from __future__ import annotations

import contextlib
import os
import shlex
import sys
from pathlib import Path
from typing import TextIO

# The packages beside this file import when another program loads it by path.
sys.path.insert(0, str(Path(__file__).resolve().parent))

import jpexit
from inventory.resume import mark_done, resume_state
from ntfstools.listing import Entry, Missed, listdir, walk
from platformtools.windows import is_system_area

HEADER = "#path\tsize\n"
MAXDEPTH = 8
TIMEOUT = 90


def restore_owner(path: str) -> None:
    """Hand the output back to the invoking user when run under sudo."""
    uid, gid = os.environ.get("SUDO_UID"), os.environ.get("SUDO_GID")
    if uid and gid:
        with contextlib.suppress(OSError):  # reason: a root-owned TSV is still a correct inventory; ownership is a convenience
            os.chown(path, int(uid), int(gid))


def _sudo_warning(device: str, argv: list[str]) -> str | None:
    """The re-run-under-sudo advice, or None when it does not apply."""
    if os.geteuid() == 0 or os.path.isfile(device):
        return None
    # sys.executable and an absolute script path, so the command works however
    # this was started: a bare argv[0] is looked up on PATH by sudo and not found.
    rerun = shlex.join(["sudo", sys.executable, os.path.abspath(sys.argv[0]), *argv])
    return ("warning: unprivileged -- this runs one 'sudo ntfsls' PER DIRECTORY.\n"
            "         A full walk did 285,471 of them and grew auth.log to 145 MB.\n"
            f"         Re-run as: {rerun}")


def record(device: str, entry: Entry, out: TextIO, missed: Missed) -> tuple[int, int]:
    """Write one top-level entry's rows, and its #done only when every
    directory under it was read: one it could not read (added to MISSED)
    leaves the entry unfinished, so the next run re-reads it whole. Returns
    (files, bytes)."""
    path = "/" + entry.name
    before = len(missed)
    rows = ([(path, entry.size)] if not entry.is_dir
            else walk(device, path, MAXDEPTH, TIMEOUT, missed=missed))
    files = size = 0
    for child, child_size in rows:
        out.write(f"{child}\t{child_size}\n")
        files += 1
        size += child_size
    if len(missed) == before:
        mark_done(out, entry.name)
    return files, size


def _pending(device: str, only: set[str], done: set[str], missed: Missed) -> list[Entry]:
    """The top-level entries still to record: not system areas, not done, in ONLY if given."""
    return [e for e in listdir(device, "/", TIMEOUT, missed)
            if not is_system_area(e.name) and (not only or e.name in only) and e.name not in done]


def main(argv: list[str] | None = None) -> int:
    args = sys.argv[1:] if argv is None else argv
    wants_help = bool(args) and args[0] in ("-h", "--help")
    if len(args) < 2 or wants_help:
        print(__doc__)
        return jpexit.OK if wants_help else jpexit.NOT_RUN
    device, output = args[0], args[1]
    warning = _sudo_warning(device, args)
    if warning:
        print(warning, file=sys.stderr)
    fresh = not os.path.exists(output)
    done = resume_state(output)
    if done:
        print("resuming -- done: {}".format(", ".join(sorted(done))))
    missed: Missed = []
    tops = _pending(device, set(args[2:]), done, missed)
    with open(output, "a", encoding="utf-8", errors="surrogateescape") as out:
        if fresh:
            out.write(HEADER)
        restore_owner(output)  # now, so an interrupted run still leaves it usable
        for entry in tops:
            files, size = record(device, entry, out, missed)
            print(f"  {entry.name[:24]:<24} {files:7d} files {size / 1e9:8.1f} GB")
    print(f"wrote {output}")
    if missed:
        print(f"incomplete: {len(missed)} directories not read; their entries are not marked "
              "done, so the next run re-reads them:")
        print("\n".join(f"  not read: {path} ({why})" for path, why in missed))
        return jpexit.NOT_RUN
    return jpexit.OK


if __name__ == "__main__":
    sys.exit(main())
