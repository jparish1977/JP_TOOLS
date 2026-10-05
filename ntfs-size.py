#!/usr/bin/env python3
"""Size a directory tree on an NTFS volume that will not mount.

Decide whether something is worth recovering *before* imaging it. When a
volume's $MFT is damaged the kernel driver refuses to mount, but ntfsls parses
the MFT directly (the same way testdisk does), so directories can still be
listed and measured. A listing costs a fraction of what a transfer costs, which
matters when the drive is failing.

Measured on a 2TB drive whose $MFT was damaged 3.2 GB in: the volume would not
mount at all, yet this reported 356 GB across 1183 files in 205 directories
without reading a byte of file data.

Requires ntfsls (ntfs-3g package). Reading a block device needs privilege,
so ntfsls is invoked via sudo when not already root; a sudoers rule covering
just that binary is enough. Everything it does is read-only.

Usage:
    python ntfs-size.py <device> [path] [--depth N] [--top N]

Examples:
    python ntfs-size.py /dev/sdb3
    python ntfs-size.py /dev/sdb3 /Movies
    python ntfs-size.py /dev/sdb3 /Movies --depth 4
    python ntfs-size.py /dev/sdb3 / --top 20

Exit codes (jpexit): 0 OK, every directory under the path was read; 2
NOT_RUN, ntfsls is missing, nothing could be listed, or a directory was not
read (a failed or timed-out listing, or one below --depth), each named after
the totals, which leave it out.
"""

import argparse
import shutil
import sys
from pathlib import Path

# The packages beside this file import when another program loads it by path.
sys.path.insert(0, str(Path(__file__).resolve().parent))

import jpexit
from ntfstools.listing import Entry, Missed, join, listdir, walk


def entry_size(device: str, path: str, entry: Entry, maxdepth: int, timeout: int,
               missed: Missed | None = None) -> tuple[int, int]:
    """(bytes, files) for one entry listed under PATH: itself, or its whole
    subtree, with any directory it could not read added to MISSED."""
    if not entry.is_dir:
        return entry.size, 1
    sizes = [size for _, size in walk(device, join(path, entry.name), maxdepth, timeout,
                                      depth=1, missed=missed)]
    return sum(sizes), len(sizes)


def report(rows: list[tuple[int, int, str]], top: int) -> list[str]:
    rows = sorted(rows, reverse=True)[: top or None]
    lines = [f"  {name[:40]:<40} {total / 1e9:10.2f} GB  {files:7d} files" for total, files, name in rows]
    grand, count = sum(r[0] for r in rows), sum(r[1] for r in rows)
    return [*lines, f"  {'TOTAL':<40} {grand / 1e9:10.2f} GB  {count:7d} files"]


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Size an NTFS directory tree without mounting the volume.")
    parser.add_argument("device", help="NTFS partition, e.g. /dev/sdb3, or an image file")
    parser.add_argument("path", nargs="?", default="/", help="path within the volume")
    parser.add_argument("--depth", type=int, default=4,
                        help="how deep to recurse below each entry (default 4)")
    parser.add_argument("--top", type=int, default=0, help="show only the N largest entries")
    parser.add_argument("--timeout", type=int, default=60,
                        help="seconds to allow per directory listing")
    args = parser.parse_args(argv)
    if shutil.which("ntfsls") is None:
        print("ntfsls not found -- install the ntfs-3g package", file=sys.stderr)
        return jpexit.NOT_RUN
    missed: Missed = []
    entries = listdir(args.device, args.path, args.timeout, missed)
    if not entries:
        print(f"nothing listed at {args.path} on {args.device}", file=sys.stderr)
        print("(is it an NTFS partition, and are you root?)", file=sys.stderr)
        return jpexit.NOT_RUN
    rows = [(*entry_size(args.device, args.path, e, args.depth, args.timeout, missed), e.name)
            for e in entries]
    print("\n".join(report(rows, args.top)))
    if missed:
        print(f"\nTOTAL leaves out {len(missed)} directories not read:")
        print("\n".join(f"  not read: {path} ({why})" for path, why in missed))
        return jpexit.NOT_RUN
    return jpexit.OK


if __name__ == "__main__":
    sys.exit(main())
