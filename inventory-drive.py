#!/usr/bin/env python
"""
JP_TOOLS/inventory-drive.py
Catalogue a mounted volume so it can be compared later, offline.

Answers "do I already have this?" about a drive that is sitting in a drawer.
Records path, size and mtime only -- no file contents are read -- so it costs
directory seeks rather than a full transfer, which matters when the drive is
failing and every read is borrowed.

Written incrementally and flushed per directory, and re-running skips top-level
directories already recorded. A disconnect therefore costs the directory in
progress rather than the whole run, and resuming is just running it again.

Compare two inventories with compare-inventory (or diff the TSVs directly);
sizes alone are a strong identity signal for media files without hashing.

Usage:
    python inventory-drive.py <mountpoint> <output.tsv> [subdir ...] [--rescan]

Source can be:
    /media/joe/DRIVE    -- any mounted filesystem (Linux)
    F:                  -- Windows drive letter (auto-mapped via WSL)

Examples:
    python inventory-drive.py /media/joe/RED red.tsv
    python inventory-drive.py /media/joe/RED red.tsv Roms saved
    python inventory-drive.py F: d-drive.tsv
    python inventory-drive.py /mnt/backup backup.tsv --rescan
"""

import argparse
import os
import re
import sys
import time
from typing import TextIO

WIN_DRIVE = re.compile(r"^([A-Za-z]):[\\/]?$")


def win_to_wsl_path(win_path: str) -> str:
    """Convert C:\\foo\\bar or C: to /mnt/c/foo/bar."""
    m = WIN_DRIVE.match(win_path)
    if m:
        return "/mnt/" + m.group(1).lower()
    if len(win_path) > 2 and win_path[1] == ":":
        rest = win_path[2:].replace("\\", "/").lstrip("/")
        return "/mnt/" + win_path[0].lower() + "/" + rest
    return win_path


def already_recorded(path: str) -> set[str]:
    """Top-level directories present in an existing inventory."""
    tops: set[str] = set()
    if not os.path.exists(path):
        return tops
    with open(path, encoding="utf-8", errors="replace") as handle:
        for line in handle:
            if line.startswith("#"):
                continue
            first = line.split("\t", 1)[0]
            if first:
                tops.add(first.split("/", 1)[0])
    return tops


def record_tree(root: str, top: str, out: TextIO) -> "tuple[int, int]":
    """Walk one top-level entry, writing a row per file. Returns (files, bytes)."""
    base = os.path.join(root, top)
    count = 0
    total = 0
    if os.path.isfile(base):
        stat = os.stat(base)
        out.write("%s\t%d\t%d\n" % (top, stat.st_size, int(stat.st_mtime)))
        return 1, stat.st_size
    for dirpath, dirnames, filenames in os.walk(base):
        dirnames[:] = [d for d in dirnames if not d.startswith("$")]
        for name in filenames:
            full = os.path.join(dirpath, name)
            try:
                stat = os.stat(full)
            except OSError:
                continue
            rel = os.path.relpath(full, root)
            out.write("%s\t%d\t%d\n" % (rel, stat.st_size, int(stat.st_mtime)))
            count += 1
            total += stat.st_size
        # Flush per directory: a disconnect costs one directory, not the run.
        out.flush()
    return count, total


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Catalogue a mounted volume (paths and sizes, no contents)."
    )
    parser.add_argument("mountpoint", help="mounted filesystem, or a Windows drive letter")
    parser.add_argument("output", help="TSV to write (appended to, for resume)")
    parser.add_argument("subdirs", nargs="*", help="limit to these top-level entries")
    parser.add_argument("--rescan", action="store_true",
                        help="ignore an existing inventory and start again")
    args = parser.parse_args()

    root = win_to_wsl_path(args.mountpoint).rstrip("/")
    if not os.path.isdir(root):
        print(f"not a directory: {root}", file=sys.stderr)
        return 1

    if args.rescan and os.path.exists(args.output):
        os.unlink(args.output)
    done = already_recorded(args.output)
    if done:
        print("resuming -- already recorded: {}".format(", ".join(sorted(done))))

    try:
        entries = sorted(
            e for e in os.listdir(root)
            if not e.startswith("$") and e != "System Volume Information"
        )
    except OSError as exc:
        print(f"cannot read {root}: {exc}", file=sys.stderr)
        return 1
    if args.subdirs:
        entries = [e for e in entries if e in args.subdirs]

    grand_files = 0
    grand_bytes = 0
    with open(args.output, "a", encoding="utf-8") as out:
        if not done:
            out.write("#path\tsize\tmtime\n")
        for top in entries:
            if top in done:
                continue
            started = time.time()
            try:
                count, total = record_tree(root, top, out)
            except OSError as exc:
                print("  ! %-18s aborted: %s" % (top, exc))
                out.flush()
                continue
            grand_files += count
            grand_bytes += total
            print("  %-18s %7d files  %8.1f GB  %5.0fs"
                  % (top, count, total / 1e9, time.time() - started))
            out.flush()

    print("wrote %s -- %d files, %.1f GB this run"
          % (args.output, grand_files, grand_bytes / 1e9))
    return 0


if __name__ == "__main__":
    sys.exit(main())
