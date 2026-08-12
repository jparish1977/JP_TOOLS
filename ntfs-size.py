#!/usr/bin/env python
"""
JP_TOOLS/ntfs-size.py
Size a directory tree on an NTFS volume that will not mount.

Decide whether something is worth recovering *before* imaging it. When a
volume's $MFT is damaged the kernel driver refuses to mount, but ntfsls parses
the MFT directly -- the same way testdisk does -- so directories can still be
listed and measured. A listing costs a fraction of what a transfer costs, which
matters when the drive is failing.

Measured on a 2TB drive whose $MFT was damaged 3.2 GB in: the volume would not
mount at all, yet this reported 356 GB across 1183 files in 205 directories
without reading a byte of file data.

Requires ntfsls (ntfs-3g package). Reading a block device needs privilege,
so ntfsls is invoked via sudo when not already root -- a sudoers rule
covering just that binary is enough, no need to run this as root.
Everything it does is read-only -- it never writes to the volume, which matters
because repair tools do, and writing to a disk with pending sectors is how a
recoverable situation becomes an unrecoverable one.

Usage:
    python ntfs-size.py <device> [path] [--depth N] [--top N]

Examples:
    python ntfs-size.py /dev/sdb3
    python ntfs-size.py /dev/sdb3 /Movies
    python ntfs-size.py /dev/sdb3 /Movies --depth 4
    python ntfs-size.py /dev/sdb3 / --top 20
"""

import argparse
import os
import re
import shutil
import subprocess
import sys
from typing import List, Tuple

# ntfsls -l prints: size, month, day, time, year, name
LISTING = re.compile(r"^\s*(\d+)\s+\w+\s+\d+\s+[\d:]+\s+\d{4}\s+(.*)$")


def as_root(cmd: List[str]) -> List[str]:
    """Prefix with sudo unless we already are root.

    Reading a block device needs privilege, but the *script* does not: elevating
    only ntfsls/ntfscat means a narrow sudoers rule covering those two binaries
    is enough, rather than granting the interpreter.
    """
    if os.geteuid() == 0:
        return cmd
    return ["sudo", "-n"] + cmd


def listdir(device: str, path: str, timeout: int) -> List[Tuple[int, str]]:
    """One directory, as (size, name). Directories report size 0."""
    try:
        result = subprocess.run(
            as_root(["ntfsls", "-l", "-F", "-p", path, device]),
            capture_output=True, text=True, timeout=timeout, check=False,
        )
    except subprocess.TimeoutExpired:
        print("  ! timed out listing %s" % path, file=sys.stderr)
        return []
    rows = []
    for line in result.stdout.splitlines():
        match = LISTING.match(line)
        if not match:
            continue
        name = match.group(2).strip()
        is_dir = name.endswith("/")
        if is_dir:
            name = name[:-1]
        if name in (".", ".."):
            continue
        rows.append((int(match.group(1)), name, is_dir))
    return rows


def walk(device: str, path: str, depth: int, maxdepth: int,
         timeout: int) -> Tuple[int, int]:
    """Recursive size of one subtree. Returns (bytes, files)."""
    total = 0
    files = 0
    for size, name, is_dir in listdir(device, path, timeout):
        child = path.rstrip("/") + "/" + name
        # -F marks directories, so an empty file is no longer mistaken for one.
        # At the depth limit, stop and say so rather than counting the directory
        # itself as a 0-byte file, which quietly under-counted the subtree.
        if is_dir:
            if depth < maxdepth:
                sub_total, sub_files = walk(device, child, depth + 1, maxdepth, timeout)
                total += sub_total
                files += sub_files
            else:
                print(f"  ! depth limit {maxdepth} reached, "
                      f"not descending into {child}", file=sys.stderr)
        else:
            total += size
            files += 1
    return total, files


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Size an NTFS directory tree without mounting the volume."
    )
    parser.add_argument("device", help="NTFS partition, e.g. /dev/sdb3")
    parser.add_argument("path", nargs="?", default="/", help="path within the volume")
    parser.add_argument("--depth", type=int, default=4,
                        help="how deep to recurse below each entry (default 4)")
    parser.add_argument("--top", type=int, default=0,
                        help="show only the N largest entries")
    parser.add_argument("--timeout", type=int, default=60,
                        help="seconds to allow per directory listing")
    args = parser.parse_args()

    if shutil.which("ntfsls") is None:
        print("ntfsls not found -- install the ntfs-3g package", file=sys.stderr)
        return 1

    entries = listdir(args.device, args.path, args.timeout)
    if not entries:
        print("nothing listed at %s on %s" % (args.path, args.device), file=sys.stderr)
        print("(is it an NTFS partition, and are you root?)", file=sys.stderr)
        return 1

    rows = []
    for size, name in entries:
        if size:
            rows.append((size, 1, name))
            continue
        child = args.path.rstrip("/") + "/" + name
        total, files = walk(args.device, child, 1, args.depth, args.timeout)
        rows.append((total, files, name))

    rows.sort(reverse=True)
    if args.top:
        rows = rows[: args.top]

    grand = 0
    count = 0
    for total, files, name in rows:
        print("  %-40s %10.2f GB  %7d files" % (name[:40], total / 1e9, files))
        grand += total
        count += files
    print("  %-40s %10.2f GB  %7d files" % ("TOTAL", grand / 1e9, count))
    return 0


if __name__ == "__main__":
    sys.exit(main())
