#!/usr/bin/env python3
"""Copy a directory tree off an NTFS volume that will not mount.

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
logged and stepped over rather than aborting the run. Each file is written to
a .part file and renamed only when complete, so an interrupted one is never
mistaken for a finished one. On a failing drive the goal is to take everything
readable in one pass and know exactly what was lost.

Everything is read-only with respect to the source. Never run a repair tool on
a disk you have not copied yet: repair writes, and writing to a disk with
pending sectors is how a recoverable situation becomes an unrecoverable one.

Requires ntfsls and ntfscat (ntfs-3g package). Reading a block device needs
privilege, so those two are invoked via sudo when not already root; a
sudoers rule covering just them is enough. An image file needs no sudo.

Usage:
    python ntfs-extract.py <device> <path> <destination> [--dry-run]
                                [--depth N] [--min-size BYTES]

Examples:
    python ntfs-extract.py /dev/sdb3 /Movies/tv ~/rescue/tv
    python ntfs-extract.py /dev/sdb3 /Movies ~/rescue/movies --dry-run
    python ntfs-extract.py /dev/sdb3 /Photos ~/rescue --depth 6

Exit status: 0 everything copied or already present, 1 anything failed, a
missing tool, or nothing found.
"""

import argparse
import contextlib
import os
import shutil
import subprocess
import sys
import time
from dataclasses import dataclass

from ntfstools.listing import as_root, walk

LISTING_TIMEOUT = 60


@dataclass
class Tally:
    copied: int = 0
    skipped: int = 0
    failed: int = 0
    done_bytes: int = 0


def collect(device: str, path: str, maxdepth: int, timeout: int, minsize: int) -> list[tuple[str, int]]:
    """Every file at or under PATH of at least MINSIZE bytes, as (full path, size)."""
    return [(p, s) for p, s in walk(device, path, maxdepth, timeout) if s >= minsize]


def within(root: str, candidate: str) -> bool:
    """True if CANDIDATE resolves inside ROOT.

    Names come from a filesystem damaged enough that the kernel refuses to
    mount it, and this may run as root. A ".." surviving into the relative
    path would otherwise write outside the destination.
    """
    root_abs = os.path.abspath(root)
    cand_abs = os.path.abspath(candidate)
    return cand_abs == root_abs or cand_abs.startswith(root_abs + os.sep)


def _discard(part: str, why: str) -> bool:
    print(f"  ! {why}", file=sys.stderr)
    with contextlib.suppress(FileNotFoundError):  # reason: a .part never created is already gone
        os.unlink(part)
    return False


def extract(device: str, src: str, dest: str, size: int, timeout: int) -> bool:
    """Pull one file out, through DEST.part, renamed to DEST only when whole."""
    part = dest + ".part"
    os.makedirs(os.path.dirname(dest), exist_ok=True)
    try:
        with open(part, "wb") as handle:
            result = subprocess.run(as_root(["ntfscat", device, src], device), stdout=handle,
                                    stderr=subprocess.PIPE, timeout=timeout, check=False)
    except subprocess.TimeoutExpired:
        return _discard(part, f"timeout: {src}")
    if result.returncode != 0:
        detail = result.stderr.decode("utf-8", "replace").strip().splitlines()
        return _discard(part, f"failed: {src} -- {detail[-1] if detail else 'unknown'}")
    got = os.path.getsize(part)
    if got != size:
        return _discard(part, f"short read: {src} ({got} of {size} bytes)")
    os.replace(part, dest)
    return True


def _one(args: argparse.Namespace, base: str, src: str, size: int, tally: Tally) -> None:
    dest = os.path.join(args.destination, src[len(base):].lstrip("/"))
    if not within(args.destination, dest):
        print(f"  ! refusing path outside destination: {src}", file=sys.stderr)
        tally.failed += 1
    elif os.path.exists(dest) and os.path.getsize(dest) == size:
        tally.skipped += 1
        tally.done_bytes += size
    elif extract(args.device, src, dest, size, args.timeout):
        tally.copied += 1
        tally.done_bytes += size
    else:
        tally.failed += 1


def extract_all(args: argparse.Namespace, files: list[tuple[str, int]]) -> Tally:
    tally = Tally()
    started = time.time()
    base = args.path.rstrip("/")
    for src, size in files:
        before = tally.copied + tally.failed
        _one(args, base, src, size, tally)
        attempted = tally.copied + tally.failed
        elapsed = time.time() - started
        if attempted != before and attempted % 10 == 0 and elapsed > 0:
            print(f"  {attempted + tally.skipped}/{len(files)}  {tally.done_bytes / 1e9:.1f} GB  "
                  f"{tally.done_bytes / 1e6 / elapsed:.1f} MB/s")
    return tally


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Copy a tree off an NTFS volume that will not mount.")
    parser.add_argument("device", help="NTFS partition, e.g. /dev/sdb3, or an image file")
    parser.add_argument("path", help="path within the volume, e.g. /Movies/tv")
    parser.add_argument("destination", help="directory to write into")
    parser.add_argument("--dry-run", action="store_true", help="list what would be copied and stop")
    parser.add_argument("--depth", type=int, default=6, help="how deep to recurse (default 6)")
    parser.add_argument("--min-size", type=int, default=0,
                        help="skip files smaller than this many bytes")
    parser.add_argument("--timeout", type=int, default=1800,
                        help="seconds to allow per file (default 1800)")
    return parser


def _preview(files: list[tuple[str, int]]) -> None:
    for src, size in files[:20]:
        print(f"  {size / 1e6:10.1f} MB  {src}")
    if len(files) > 20:
        print(f"  ... and {len(files) - 20} more")
    print("dry run -- nothing written")


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    missing = [t for t in ("ntfsls", "ntfscat") if shutil.which(t) is None]
    if missing:
        print(f"{missing[0]} not found -- install the ntfs-3g package", file=sys.stderr)
        return 1
    print(f"enumerating {args.path} ...")
    files = collect(args.device, args.path, args.depth, LISTING_TIMEOUT, args.min_size)
    if not files:
        print(f"nothing found at {args.path}", file=sys.stderr)
        return 1
    print(f"  {len(files)} files, {sum(s for _, s in files) / 1e9:.1f} GB")
    if args.dry_run:
        _preview(files)
        return 0
    tally = extract_all(args, files)
    print(f"copied {tally.copied}, skipped {tally.skipped} already present, failed {tally.failed}")
    return 1 if tally.failed else 0


if __name__ == "__main__":
    sys.exit(main())
