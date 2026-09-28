"""Listing and walking an NTFS volume with ntfsls, which reads the $MFT directly.

A volume whose metadata is damaged enough that the kernel refuses to mount
it can usually still be listed this way. Everything here is read-only.
"""

from __future__ import annotations

import os
import re
import subprocess
import sys
from collections.abc import Iterator
from typing import NamedTuple

# `ntfsls -l` prints: size, month, day, time, year, name.
LISTING = re.compile(r"^\s*(\d+)\s+\w+\s+\d+\s+[\d:]+\s+\d{4}\s+(.*)$")


class Entry(NamedTuple):
    size: int
    name: str
    is_dir: bool


def as_root(cmd: list[str], device: str) -> list[str]:
    """CMD, prefixed with `sudo -n` when reading DEVICE needs privilege.

    A block device needs root; an image file does not, and neither does a
    process that is already root. Elevating only ntfsls and ntfscat means a
    sudoers rule covering those two binaries is enough, rather than granting
    the interpreter.
    """
    if os.geteuid() == 0 or os.path.isfile(device):
        return cmd
    return ["sudo", "-n", *cmd]


def join(path: str, name: str) -> str:
    return path.rstrip("/") + "/" + name


def parse_listing(text: str) -> list[Entry]:
    """`ntfsls -l -F` output as entries, "." and ".." dropped.

    `-F` (classify) appends "/" to directory names. Without it a directory and
    a zero-byte file are indistinguishable, because ntfsls reports size 0 for
    both, and branching on size makes every empty file a directory that is
    never copied. NTFS forbids "/" in a name, so the marker is unambiguous.
    """
    entries = []
    for line in text.splitlines():
        match = LISTING.match(line)
        if not match:
            continue
        name = match.group(2).strip()
        is_dir = name.endswith("/")
        name = name[:-1] if is_dir else name
        if name not in (".", ".."):
            entries.append(Entry(int(match.group(1)), name, is_dir))
    return entries


def listdir(device: str, path: str, timeout: int) -> list[Entry]:
    """One directory of DEVICE. A timed-out listing is said on stderr and is empty."""
    try:
        result = subprocess.run(as_root(["ntfsls", "-l", "-F", "-p", path, device], device),
                                capture_output=True, text=True, timeout=timeout, check=False)
    except subprocess.TimeoutExpired:
        print(f"  ! timed out listing {path}", file=sys.stderr)
        return []
    return parse_listing(result.stdout)


def walk(device: str, path: str, maxdepth: int, timeout: int,
         depth: int = 0) -> Iterator[tuple[str, int]]:
    """(full path, size) for every file at or under PATH.

    A directory at MAXDEPTH is not entered, and that is said on stderr: a
    silently truncated tree looks exactly like a complete one in the totals,
    which in a recovery means believing you have everything when you do not.
    """
    for entry in listdir(device, path, timeout):
        child = join(path, entry.name)
        if not entry.is_dir:
            yield child, entry.size
        elif depth < maxdepth:
            yield from walk(device, child, maxdepth, timeout, depth + 1)
        else:
            print(f"  ! depth limit {maxdepth} reached, not descending into {child}",
                  file=sys.stderr)
