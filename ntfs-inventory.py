#!/usr/bin/env python3
"""Inventory an unmountable NTFS volume via ntfsls -- metadata only.

The lightest operation available on a failing drive: directory entries only, no
file data. Written incrementally per top-level directory and resumable, so a
disconnect costs the directory in progress rather than the run.
"""
from __future__ import annotations

import contextlib
import os
import re
import shlex
import subprocess
import sys
from typing import TextIO

LISTING = re.compile(r"^\s*(\d+)\s+\w+\s+\d+\s+[\d:]+\s+\d{4}\s+(.*)$")

USAGE = """usage: ntfs-inventory.py DEVICE OUTPUT.tsv [DIR ...]

  DEVICE   the NTFS partition, e.g. /dev/sdb3 -- it does NOT need to mount
  OUTPUT   TSV written as "path<TAB>size"; appended to and resumable
  DIR      optional top-level directories to limit the pass to

Needs ntfsls (ntfs-3g), and root or passwordless sudo for the raw device.
Re-running the same command resumes: any top-level directory already present
in OUTPUT is skipped."""

if len(sys.argv) < 3 or sys.argv[1] in ("-h", "--help"):
    print(USAGE)
    sys.exit(0 if len(sys.argv) > 1 and sys.argv[1] in ("-h", "--help") else 2)

DEV, OUT = sys.argv[1], sys.argv[2]
ONLY = sys.argv[3:] or None


def as_root(cmd: list[str]) -> list[str]:
    """One sudo for the whole run, not one per directory.

    walk() calls listdir() once per directory, so unprivileged this becomes a
    sudo invocation per directory: hundreds of thousands on a full drive, each
    logging three auth.log lines. sudo's credential cache does not help --
    every invocation is logged whether or not it re-authenticates.

    Running the script itself under sudo makes this a no-op:

        sudo ./ntfs-inventory.py /dev/sdb1 out.tsv

    `ntfsls -R` would also collapse the calls, but it is the wrong trade: the
    per-directory walk is what makes this resumable on a drive that disconnects.
    """
    return cmd if os.geteuid() == 0 else ["sudo", "-n", *cmd]


def restore_owner(path: str) -> None:
    """Hand the output back to the invoking user when run under sudo."""
    uid, gid = os.environ.get("SUDO_UID"), os.environ.get("SUDO_GID")
    if uid and gid:
        with contextlib.suppress(OSError):  # reason: a root-owned TSV is still a correct inventory; ownership is a convenience
            os.chown(path, int(uid), int(gid))


def listdir(path: str) -> list[tuple[int, str, bool]]:
    try:
        r = subprocess.run(as_root(["ntfsls", "-l", "-F", "-p", path, DEV]),
                           capture_output=True, text=True, timeout=90, check=False)
    except subprocess.TimeoutExpired:
        print(f"  ! timeout: {path}", file=sys.stderr)
        return []
    rows = []
    for line in r.stdout.splitlines():
        m = LISTING.match(line)
        if not m:
            continue
        name = m.group(2).strip()
        is_dir = name.endswith("/")
        if is_dir:
            name = name[:-1]
        if name in (".", ".."):
            continue
        rows.append((int(m.group(1)), name, is_dir))
    return rows


def walk(path: str, out: TextIO, depth: int = 0, maxdepth: int = 8) -> tuple[int, int]:
    n = b = 0
    for size, name, is_dir in listdir(path):
        child = path.rstrip("/") + "/" + name
        # -F marks directories. Branching on size alone recorded every empty
        # file as a directory, and at the depth limit recorded every directory
        # as a 0-byte file.
        if is_dir:
            if depth < maxdepth:
                sn, sb = walk(child, out, depth + 1, maxdepth)
                n += sn
                b += sb
            else:
                print(f"  ! depth limit {maxdepth} reached, "
                      f"not descending into {child}", file=sys.stderr)
        else:
            out.write(f"{child}\t{size}\n")
            n += 1
            b += size
    out.flush()
    return n, b


if os.geteuid() != 0:
    # sys.executable and an absolute script path, so the command works however
    # this was started: `python3 ntfs-inventory.py` gives a bare argv[0] that
    # sudo would look up on PATH and not find.
    rerun = shlex.join(["sudo", sys.executable, os.path.abspath(sys.argv[0]),
                        *sys.argv[1:]])
    print("warning: unprivileged -- this runs one 'sudo ntfsls' PER DIRECTORY.\n"
          "         A full walk did 285,471 of them and grew auth.log to 145 MB.\n"
          f"         Re-run as: {rerun}",
          file=sys.stderr)

done: set[str] = set()
if os.path.exists(OUT):
    with open(OUT, encoding="utf-8", errors="replace") as prior:
        for line in prior:
            if not line.startswith("#"):
                done.add(line.split("\t")[0].lstrip("/").split("/", 1)[0])
    if done:
        print("resuming -- done: {}".format(", ".join(sorted(done))))

tops = [(s, n, d) for s, n, d in listdir("/")
        if not n.startswith("$") and n != "System Volume Information"]
if ONLY:
    tops = [(s, n, d) for s, n, d in tops if n in ONLY]

with open(OUT, "a", encoding="utf-8") as out:
    if not done:
        out.write("#path\tsize\n")
    restore_owner(OUT)   # do it now, so an interrupted run still leaves it usable
    for size, name, is_dir in tops:
        if name in done:
            continue
        if not is_dir:
            out.write(f"/{name}\t{size}\n")
            print(f"  {name:<24} {1:7d} files {size / 1e9:8.1f} GB")
            continue
        n, b = walk("/" + name, out)
        print(f"  {name[:24]:<24} {n:7d} files {b / 1e9:8.1f} GB")
print(f"wrote {OUT}")
