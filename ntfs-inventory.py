#!/usr/bin/env python3
"""Inventory an unmountable NTFS volume via ntfsls -- metadata only.

The lightest operation available on a failing drive: directory entries only, no
file data. Written incrementally per top-level directory and resumable, so a
disconnect costs the directory in progress rather than the run.
"""
import os
import re
import subprocess
import sys

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


def as_root(cmd):
    return cmd if os.geteuid() == 0 else ["sudo", "-n"] + cmd


def listdir(path):
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


def walk(path, out, depth=0, maxdepth=8):
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
            out.write("%s\t%d\n" % (child, size))
            n += 1
            b += size
    out.flush()
    return n, b


done = set()
if os.path.exists(OUT):
    for line in open(OUT, encoding="utf-8", errors="replace"):
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
    for size, name, is_dir in tops:
        if name in done:
            continue
        if not is_dir:
            out.write("/%s\t%d\n" % (name, size))
            print("  %-24s %7d files %8.1f GB" % (name, 1, size / 1e9))
            continue
        n, b = walk("/" + name, out)
        print("  %-24s %7d files %8.1f GB" % (name[:24], n, b / 1e9))
print(f"wrote {OUT}")
