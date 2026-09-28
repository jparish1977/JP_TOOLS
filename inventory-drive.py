#!/usr/bin/env python3
"""Catalogue a mounted volume so it can be compared later, offline.

Answers "do I already have this?" about a drive that is sitting in a drawer.
Records path, size and mtime only (no file contents are read), so it costs
directory seeks rather than a full transfer, which matters when the drive is
failing and every read is borrowed.

Written incrementally and flushed per directory, and resumable per top-level
entry (inventory/resume.py): an entry cut off by a disconnect is re-read
whole on the next run, never left half-recorded.

Entries whose names start with `$`, and "System Volume Information", are
Windows system areas and are skipped at every depth.

Compare two inventories by diffing the TSVs; sizes alone are a strong
identity signal for media files without hashing.

Usage:
    python inventory-drive.py <mountpoint> <output.tsv> [subdir ...] [--rescan]

Source can be:
    /media/me/DRIVE     any mounted filesystem
    F:                  a Windows drive letter, read from WSL at /mnt/f

Examples:
    python inventory-drive.py /media/me/RED red.tsv
    python inventory-drive.py /media/me/RED red.tsv Roms saved
    python inventory-drive.py F: d-drive.tsv
    python inventory-drive.py /mnt/backup backup.tsv --rescan
"""

import argparse
import os
import re
import sys
import time
from typing import TextIO

from inventory.resume import mark_done, resume_state
from platformtools.windows import is_system_area, wsl_path

# A Windows path, given to the WSL side: F:, F:\Roms, F:/Roms.
WIN_PATH = re.compile(r"^[A-Za-z]:")
HEADER = "#path\tsize\tmtime\n"


def mount_root(mountpoint: str) -> str:
    """Where to read MOUNTPOINT from: a Windows drive path as WSL sees it, anything else as given."""
    return (wsl_path(mountpoint) if WIN_PATH.match(mountpoint) else mountpoint).rstrip("/")


def _row(rel: str, st: os.stat_result) -> str:
    return f"{rel}\t{st.st_size}\t{int(st.st_mtime)}\n"


def record_tree(root: str, top: str, out: TextIO) -> tuple[int, int]:
    """Walk one top-level entry, writing a row per file. Returns (files, bytes)."""
    base = os.path.join(root, top)
    if os.path.isfile(base):
        st = os.stat(base)
        out.write(_row(top, st))
        return 1, st.st_size
    count = total = 0
    for dirpath, dirnames, filenames in os.walk(base):
        dirnames[:] = [d for d in dirnames if not is_system_area(d)]
        for name in filenames:
            full = os.path.join(dirpath, name)
            try:
                st = os.stat(full)
            except OSError as e:
                # reason: named rather than skipped silently; an inventory
                # that quietly leaves a file out is short and nobody knows it
                print(f"inventory-drive: {full} cannot be stat'd ({e}); NOT inventoried",
                      file=sys.stderr)
                continue
            out.write(_row(os.path.relpath(full, root), st))
            count += 1
            total += st.st_size
        # Flush per directory: a disconnect costs one entry, not the run.
        out.flush()
    return count, total


def _entries(root: str, only: list[str]) -> list[str]:
    entries = sorted(e for e in os.listdir(root) if not is_system_area(e))
    return [e for e in entries if e in only] if only else entries


def _record(root: str, entries: list[str], done: set[str], out: TextIO) -> tuple[int, int]:
    """Record every unfinished entry. Returns (files, bytes) recorded this run."""
    files = size = 0
    for top in (e for e in entries if e not in done):
        started = time.time()
        try:
            count, total = record_tree(root, top, out)
        except OSError as exc:
            print(f"  ! {top:<18} aborted: {exc}")
            out.flush()
            continue
        mark_done(out, top)
        files += count
        size += total
        print(f"  {top:<18} {count:7d} files  {total / 1e9:8.1f} GB  {time.time() - started:5.0f}s")
    return files, size


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Catalogue a mounted volume (paths and sizes, no contents)."
    )
    parser.add_argument("mountpoint", help="mounted filesystem, or a Windows drive letter")
    parser.add_argument("output", help="TSV to write (appended to, for resume)")
    parser.add_argument("subdirs", nargs="*", help="limit to these top-level entries")
    parser.add_argument("--rescan", action="store_true",
                        help="ignore an existing inventory and start again")
    args = parser.parse_args(argv)
    root = mount_root(args.mountpoint)
    if not os.path.isdir(root):
        print(f"not a directory: {root}", file=sys.stderr)
        return 1
    if args.rescan and os.path.exists(args.output):
        os.unlink(args.output)
    fresh = not os.path.exists(args.output)
    done = resume_state(args.output)
    if done:
        print("resuming -- already recorded: {}".format(", ".join(sorted(done))))
    try:
        entries = _entries(root, args.subdirs)
    except OSError as exc:
        print(f"cannot read {root}: {exc}", file=sys.stderr)
        return 1
    with open(args.output, "a", encoding="utf-8", errors="surrogateescape") as out:
        if fresh:
            out.write(HEADER)
        files, size = _record(root, entries, done, out)
    print(f"wrote {args.output} -- {files} files, {size / 1e9:.1f} GB this run")
    return 0


if __name__ == "__main__":
    sys.exit(main())
