#!/usr/bin/env python3
"""Rebuild the CHD fixtures in this directory with chdman.

    python tests/fixtures/chd/make_fixtures.py

Deterministic input, so a rebuild with the same chdman gives the same map.
The expected hunk counts in tests/test_chd_hunkmap.py are chdman's own, from
`chdman info --verbose` on these files:

    selfref.chd   16 hunks: 4 compressed, 12 copies of earlier hunks, written
                  as SELF, runs of SELF_0 and SELF_1
    child.chd     16 hunks against parent.chd: 15 parent references, 1 new
    plain.chd     4 hunks, uncompressed, so the map is a plain offset table
    members.chd   members.tar with 512-byte hunks: a.bin and c.bin share a
                  block, a.bin and b.bin share another
"""

import subprocess
import sys
import tarfile
import tempfile
from pathlib import Path

HERE = Path(__file__).resolve().parent


def block(k: int, size: int = 4096) -> bytes:
    """A compressible block that differs for every K."""
    return bytes((i * (k + 3) + k) % 256 for i in range(size))


def chdman(*args: str) -> None:
    subprocess.run(["chdman", *args, "--force"], check=True, capture_output=True)


def raw(src: Path, dst: Path, hunk: int, *extra: str) -> None:
    chdman("createraw", "--input", str(src), "--output", str(dst),
           "--hunksize", str(hunk), "--unitsize", "512", *extra)


def _members_tar(tar: Path) -> None:
    x, y, z, w = (block(k, 512) for k in (20, 21, 22, 23))
    with tarfile.open(tar, "w", format=tarfile.USTAR_FORMAT) as t:
        for name, data in (("a.bin", x + y + z), ("b.bin", y + w), ("c.bin", x)):
            info = tarfile.TarInfo(name)
            info.size = len(data)
            info.mtime = 0
            with tempfile.TemporaryFile() as fh:
                fh.write(data)
                fh.seek(0)
                t.addfile(info, fh)


def main() -> int:
    with tempfile.TemporaryDirectory() as tmp:
        work = Path(tmp)
        img = work / "selfref.img"
        img.write_bytes(b"".join(block(k) for k in (0, 1, 2, 3, 0, 0, 0, 0, 0, 1, 2, 3, 1, 1, 2, 3)))
        raw(img, HERE / "selfref.chd", 4096)
        base = [block(k) for k in range(16)]
        (work / "parent.img").write_bytes(b"".join(base))
        raw(work / "parent.img", HERE / "parent.chd", 4096)
        (work / "child.img").write_bytes(b"".join([*base[:9], block(99), *base[10:]]))
        chdman("createraw", "--input", str(work / "child.img"), "--output",
               str(HERE / "child.chd"), "--outputparent", str(HERE / "parent.chd"))
        (work / "plain.img").write_bytes(b"".join(base[:4]))
        raw(work / "plain.img", HERE / "plain.chd", 4096, "--compression", "none")
        _members_tar(HERE / "members.tar")
        raw(HERE / "members.tar", HERE / "members.chd", 512)
    return 0


if __name__ == "__main__":
    sys.exit(main())
