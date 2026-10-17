#!/usr/bin/env python3
"""
JP_TOOLS/tests/test_chd_chdman.py
chd.py against a real chdman: every command that writes a CHD, read back
and compared byte for byte with what went in. The fake-tool suite asserts the
arguments; this one proves chdman accepts them.

Needs chdman on PATH (Debian/Ubuntu: mame-tools). Without it this prints
SKIP and runs nothing.

Each test holds chdman's stall to STALL seconds. chdman hangs now and then
making a child CHD on a loaded box, 0.264 and 0.289 alike (7 and 13 in
1600 with eight at once: tools/lab/chdman-delta-hang.sh,
oob/chdman-hangs/CHDMAN-NOTE.md). These images are a few hundred KB and
take about 0.2 s, so a call silent this long is hung; the watchdog kills it
and runs it once more. The kill comes up to a second (run_watched's poll)
after STALL, and kill plus rerun stays under the shortest time mutmut gives a
mutant's tests, (estimate + timeout_constant 1) x timeout_multiplier 5 x 2
seconds of CPU, halved for the wall clock: 5 s with a zero estimate. At
anything longer a hang ends the mutant's run first, mutmut counts it a
timeout, and the gate counts a timeout as a kill, so a survivor hides.

    python tests/test_chd_chdman.py
"""

import subprocess
import sys
import tempfile
from collections.abc import Callable
from pathlib import Path
from unittest import mock

from _harness import check, check_true, run
from chd_support import cli
from chdmap.header import read_header
from chdmap.hunkmap import Kind, read_map
from chdtool import chdman as chdman_mod
from chdtool.chdman import WINDOWS_CHDMAN
from platformtools.tools import find_tool

SECTOR = 2048
STALL = 2.0


def _short_stall(test: Callable[[], None]) -> Callable[[], None]:
    """TEST run with chdman's stall held to STALL."""
    return mock.patch.object(chdman_mod, "CHDMAN_STALL", STALL)(test)


def _image(path: Path, sectors: int, seed: int) -> bytes:
    data = bytes((i * seed + i // 7) % 251 for i in range(sectors * SECTOR))
    path.write_bytes(data)
    return data


@_short_stall
def test_raw_image_round_trip() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        w = Path(tmp)
        data = _image(w / "a.iso", 64, 3)
        check("create", cli("create", str(w / "a.iso"), str(w / "a.chd"))[0], 0)
        check("verify", cli("verify", str(w / "a.chd"))[0], 0)
        check("extract", cli("extract", str(w / "a.chd"), str(w / "out.iso"))[0], 0)
        check_true("same bytes", (w / "out.iso").read_bytes() == data)
        check("convert", cli("convert", str(w / "a.chd"), str(w / "c.chd"),
                             "--compression", "zlib")[0], 0)
        check("compare same content", cli("compare", str(w / "a.chd"), str(w / "c.chd"))[0], 0)


@_short_stall
def test_delta_then_merge_round_trip() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        w = Path(tmp)
        base = _image(w / "a.img", 64, 5)
        changed = bytearray(base)
        changed[5000:5007] = b"changed"
        (w / "b.img").write_bytes(changed)
        check("parent", cli("create", str(w / "a.img"), str(w / "a.chd"))[0], 0)
        check("delta", cli("delta", str(w / "a.chd"), str(w / "b.img"), str(w / "b.chd"))[0], 0)
        check_true("child is small", (w / "b.chd").stat().st_size < len(base) // 4)
        check("merge", cli("merge", str(w / "a.chd"), str(w / "b.chd"), str(w / "m.chd"))[0], 0)
        check("extract", cli("extract", str(w / "m.chd"), str(w / "m.img"))[0], 0)
        check_true("merged bytes", (w / "m.img").read_bytes() == bytes(changed))
        check("parent and child differ", cli("compare", str(w / "a.chd"), str(w / "m.chd"))[0], 1)


def _chdman_table(chd: Path) -> dict[str, int]:
    """chdman info's hunk table: {type name: hunks}."""
    out = subprocess.run(["chdman", "info", "--verbose", "--input", str(chd)],
                         capture_output=True, text=True, check=True).stdout
    table = out.split("-----\n", 1)[-1] if "-----" in out else ""
    rows = (line.split(None, 2) for line in table.splitlines() if line.strip())
    return {r[2].strip(): int(r[0].replace(",", "")) for r in rows if len(r) == 3}


def _decoded(chd: Path) -> tuple[int, int]:
    """(self references, parent references) as chdmap decodes them."""
    with chd.open("rb") as f:
        hunks = read_map(f, read_header(f))
    return (sum(h.kind is Kind.SELF for h in hunks), sum(h.kind is Kind.PARENT for h in hunks))


@_short_stall
def test_hunkmap_agrees_with_chdman() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        w = Path(tmp)
        blocks = [_image(w / f"b{k}", 2, k + 11) for k in range(4)]
        (w / "d.img").write_bytes(b"".join(blocks[i] for i in (0, 1, 0, 0, 2, 1, 3, 3, 0)))
        cli("create", str(w / "d.img"), str(w / "d.chd"))
        changed = bytearray((w / "d.img").read_bytes())
        changed[:7] = b"changed"
        (w / "e.img").write_bytes(changed)
        cli("delta", str(w / "d.chd"), str(w / "e.img"), str(w / "e.chd"))
        for name in ("d.chd", "e.chd"):
            table = _chdman_table(w / name)
            check(f"{name} self and parent",
                  _decoded(w / name),
                  (table.get("Copy from self", 0), table.get("Copy from parent", 0)))
        check_true("the image did have duplicates", _decoded(w / "d.chd")[0] > 0)


@_short_stall
def test_cd_extracts_to_a_cue_sheet() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        w = Path(tmp)
        data = _image(w / "game.bin", 32, 7)
        (w / "game.cue").write_text(
            'FILE "game.bin" BINARY\n  TRACK 01 MODE1/2048\n    INDEX 01 00:00:00\n')
        check("batch", cli("batch-create", str(w))[0], 0)
        check_true("one CHD for the disc", sorted(p.name for p in w.glob("*.chd")) == ["game.chd"])
        check("extract", cli("extract", str(w / "game.chd"), str(w / "out.cue"))[0], 0)
        check_true("a cue sheet", (w / "out.cue").read_text().startswith("FILE"))
        check_true("track bytes", (w / "out.bin").read_bytes() == data)


if __name__ == "__main__":
    if find_tool("chdman", WINDOWS_CHDMAN) is None:
        print("SKIP: chdman not found")
        sys.exit(0)
    sys.exit(run(globals(), "chd against chdman"))
