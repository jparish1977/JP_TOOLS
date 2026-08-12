#!/usr/bin/env python
"""
JP_TOOLS/ntfs-extract.py
Copy a directory tree off an NTFS volume that will not mount.

When a volume's metadata is partly unreadable the kernel driver refuses to
mount it, but the files themselves are usually fine. ntfsls and ntfscat read
the $MFT directly, so a tree can be enumerated and extracted without mounting
anything.

Measured case: a 2TB drive that failed to mount with

    Failed to read NTFS $Bitmap: Input/output error

$Bitmap is the cluster allocation map, which ntfs-3g reads at mount time and
which nothing needs in order to *read* a file. ntfsls and ntfscat never touch
it, so 356 GB across 1183 files came off a volume the kernel would not mount.

Resumable and fault-tolerant by design: a file already present at the
destination with the expected size is skipped, and a file that fails to read is
logged and stepped over rather than aborting the run. On a failing drive the
goal is to take everything readable in one pass and know exactly what was lost.

Everything is read-only with respect to the source. Never run a repair tool on
a disk you have not copied yet -- repair writes, and writing to a disk with
pending sectors is how a recoverable situation becomes an unrecoverable one.

Requires ntfsls and ntfscat (ntfs-3g package). Reading a block device needs
privilege, so those two are invoked via sudo when not already root -- a
sudoers rule covering just them is enough, no need to run this as root.

Usage:
    python ntfs-extract.py <device> <path> <destination> [--dry-run]
                                [--depth N] [--min-size BYTES]

Examples:
    python ntfs-extract.py /dev/sdb3 /Movies/tv ~/rescue/tv
    python ntfs-extract.py /dev/sdb3 /Movies ~/rescue/movies --dry-run
    python ntfs-extract.py /dev/sdb3 /Photos ~/rescue --depth 6
"""

import argparse
import os
import re
import shutil
import subprocess
import sys
import time
from typing import List, Tuple

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


def listdir(device: str, path: str, timeout: int) -> List[Tuple[int, str, bool]]:
    """One directory as (size, name, is_dir).

    `-F` (classify) appends "/" to directory names. Without it a directory and a
    zero-byte file are indistinguishable, because ntfsls reports size 0 for
    both -- verified against a scratch NTFS image, where the `$Extend` directory
    and the empty `$Secure` file both list as 0. Branching on size alone treated
    every empty file as a directory, so empty files were silently never copied.
    NTFS forbids "/" in a name, so the marker is unambiguous.
    """
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


def collect(device: str, path: str, depth: int, maxdepth: int,
            timeout: int, minsize: int) -> List[Tuple[str, int]]:
    """Every file at or under path, as (full path, size)."""
    found: List[Tuple[str, int]] = []
    for size, name, is_dir in listdir(device, path, timeout):
        child = path.rstrip("/") + "/" + name
        if is_dir:
            if depth < maxdepth:
                found.extend(collect(device, child, depth + 1, maxdepth,
                                     timeout, minsize))
            else:
                # Say so. A silently truncated tree looks exactly like a
                # complete one in the totals, which in a recovery means
                # believing you have everything when you do not.
                print(f"  ! depth limit {maxdepth} reached, "
                      f"not descending into {child}", file=sys.stderr)
        elif size >= minsize:
            found.append((child, size))
    return found


def within(root: str, candidate: str) -> bool:
    """True if candidate resolves inside root.

    Names come from a filesystem damaged enough that the kernel refuses to mount
    it, and this may run as root. A ".." surviving into the relative path would
    otherwise write outside the destination.
    """
    root_abs = os.path.abspath(root)
    cand_abs = os.path.abspath(candidate)
    return cand_abs == root_abs or cand_abs.startswith(root_abs + os.sep)


def extract(device: str, src: str, dest: str, size: int, timeout: int) -> bool:
    """Pull one file out. Writes to a .part file and renames on success, so an
    interrupted extraction is never mistaken for a complete one."""
    part = dest + ".part"
    os.makedirs(os.path.dirname(dest), exist_ok=True)
    try:
        with open(part, "wb") as handle:
            result = subprocess.run(
                as_root(["ntfscat", device, src]),
                stdout=handle, stderr=subprocess.PIPE, timeout=timeout, check=False,
            )
    except subprocess.TimeoutExpired:
        print("  ! timeout: %s" % src, file=sys.stderr)
        _unlink(part)
        return False
    if result.returncode != 0:
        detail = result.stderr.decode("utf-8", "replace").strip().splitlines()
        print("  ! failed: %s -- %s" % (src, detail[-1] if detail else "unknown"),
              file=sys.stderr)
        _unlink(part)
        return False
    if os.path.getsize(part) != size:
        print("  ! short read: %s (%d of %d bytes)"
              % (src, os.path.getsize(part), size), file=sys.stderr)
        _unlink(part)
        return False
    os.replace(part, dest)
    return True


def _unlink(path: str) -> None:
    try:
        os.unlink(path)
    except OSError:
        pass


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Copy a tree off an NTFS volume that will not mount."
    )
    parser.add_argument("device", help="NTFS partition, e.g. /dev/sdb3")
    parser.add_argument("path", help="path within the volume, e.g. /Movies/tv")
    parser.add_argument("destination", help="directory to write into")
    parser.add_argument("--dry-run", action="store_true",
                        help="list what would be copied and stop")
    parser.add_argument("--depth", type=int, default=6,
                        help="how deep to recurse (default 6)")
    parser.add_argument("--min-size", type=int, default=0,
                        help="skip files smaller than this many bytes")
    parser.add_argument("--timeout", type=int, default=1800,
                        help="seconds to allow per file (default 1800)")
    args = parser.parse_args()

    for tool in ("ntfsls", "ntfscat"):
        if shutil.which(tool) is None:
            print("%s not found -- install the ntfs-3g package" % tool, file=sys.stderr)
            return 1

    print("enumerating %s ..." % args.path)
    files = collect(args.device, args.path, 0, args.depth, 60, args.min_size)
    if not files:
        print("nothing found at %s" % args.path, file=sys.stderr)
        return 1
    total = sum(size for _, size in files)
    print("  %d files, %.1f GB" % (len(files), total / 1e9))

    base = args.path.rstrip("/")
    if args.dry_run:
        for src, size in files[:20]:
            print("  %10.1f MB  %s" % (size / 1e6, src))
        if len(files) > 20:
            print("  ... and %d more" % (len(files) - 20))
        print("dry run -- nothing written")
        return 0

    copied = skipped = failed = 0
    done_bytes = 0
    started = time.time()
    for src, size in files:
        rel = src[len(base):].lstrip("/")
        dest = os.path.join(args.destination, rel)
        if not within(args.destination, dest):
            print(f"  ! refusing path outside destination: {src}", file=sys.stderr)
            failed += 1
            continue
        if os.path.exists(dest) and os.path.getsize(dest) == size:
            skipped += 1
            done_bytes += size
            continue
        if extract(args.device, src, dest, size, args.timeout):
            copied += 1
            done_bytes += size
        else:
            failed += 1
        elapsed = time.time() - started
        if (copied + failed) % 10 == 0 and elapsed > 0:
            print("  %d/%d  %.1f GB  %.1f MB/s"
                  % (copied + skipped + failed, len(files),
                     done_bytes / 1e9, done_bytes / 1e6 / elapsed))

    print("copied %d, skipped %d already present, failed %d"
          % (copied, skipped, failed))
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
