#!/usr/bin/env python3
"""Catalogue a mounted volume so it can be compared later, offline.

Answers "do I already have this?" about a drive that is sitting in a drawer.
Records path, size and mtime only (no file contents are read), so it costs
directory seeks rather than a full transfer, which matters when the drive is
failing and every read is borrowed.

Written incrementally and flushed per directory. Each top-level entry that
finishes gets a `#done<TAB>name` line; a re-run skips those, and drops the
rows of any entry that started but has no `#done` (a disconnect mid-way)
before recording it again, so an interrupted entry is re-read whole, never
left half-recorded. An inventory with no `#done` lines at all predates the
markers: every entry in it counts as finished, as it always did.

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

WIN_DRIVE = re.compile(r"^([A-Za-z]):[\\/]?$")
HEADER = "#path\tsize\tmtime\n"
DONE = "#done\t"


def win_to_wsl_path(win_path: str) -> str:
    """Convert C:\\foo\\bar or C: to /mnt/c/foo/bar."""
    m = WIN_DRIVE.match(win_path)
    if m:
        return "/mnt/" + m.group(1).lower()
    if len(win_path) > 2 and win_path[1] == ":":
        rest = win_path[2:].replace("\\", "/").lstrip("/")
        return "/mnt/" + win_path[0].lower() + "/" + rest
    return win_path


def _skipped(name: str) -> bool:
    return name.startswith("$") or name == "System Volume Information"


def _top(row: str) -> str:
    return row.split("\t", 1)[0].split("/", 1)[0]


def _drop_unfinished(path: str, lines: list[str], done: set[str]) -> None:
    """Rewrite PATH without the rows of entries not in DONE, when it has any."""
    kept = [line for line in lines if line.startswith("#") or _top(line) in done]
    if len(kept) == len(lines):
        return
    dropped = sorted({_top(line) for line in lines if not line.startswith("#")} - done)
    print(f"re-recording unfinished: {', '.join(dropped)}")
    tmp = f"{path}.tmp"
    with open(tmp, "w", encoding="utf-8", errors="surrogateescape") as out:
        out.writelines(kept)
    os.replace(tmp, path)


def resume_state(path: str) -> set[str]:
    """Finished top-level entries in an existing inventory; drops unfinished ones' rows."""
    if not os.path.exists(path):
        return set()
    with open(path, encoding="utf-8", errors="surrogateescape") as handle:
        lines = [line for line in handle if line.strip()]
    done = {line[len(DONE):].rstrip("\n") for line in lines if line.startswith(DONE)}
    if not done:
        return {_top(line) for line in lines if not line.startswith("#")}
    _drop_unfinished(path, lines, done)
    return done


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
        dirnames[:] = [d for d in dirnames if not _skipped(d)]
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
    entries = sorted(e for e in os.listdir(root) if not _skipped(e))
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
        out.write(f"{DONE}{top}\n")
        out.flush()
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
    root = win_to_wsl_path(args.mountpoint).rstrip("/")
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
