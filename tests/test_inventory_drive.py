#!/usr/bin/env python3
"""
JP_TOOLS/tests/test_inventory_drive.py
inventory-drive.py: what a run records, and that resuming re-reads an
interrupted entry whole instead of skipping it or recording it twice.

    python tests/test_inventory_drive.py
"""

import sys
import tempfile
from pathlib import Path

from _harness import check, check_true, load_script, run
from fake_tools import run_main

INV = load_script("inventory-drive.py")


def _volume(root: Path) -> Path:
    vol = root / "vol"
    for rel, data in (("A/x.bin", b"12"), ("A/sub/y.bin", b"345"), ("B/z.bin", b"6"),
                      ("B/w.bin", b"78"), ("$RECYCLE.BIN/gone", b"9"), ("top.txt", b"0")):
        (vol / rel).parent.mkdir(parents=True, exist_ok=True)
        (vol / rel).write_bytes(data)
    return vol


def _rows(tsv: Path) -> list[str]:
    return sorted(line.split("\t")[0] for line in tsv.read_text().splitlines()
                  if line and not line.startswith("#"))


def _done(tsv: Path) -> list[str]:
    return [line.split("\t")[1] for line in tsv.read_text().splitlines() if line.startswith("#done\t")]


def test_a_fresh_run() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        vol, tsv = _volume(Path(tmp)), Path(tmp) / "inv.tsv"
        check("exit", run_main(INV.main, str(vol), str(tsv))[0], 0)
        check("rows", _rows(tsv), ["A/sub/y.bin", "A/x.bin", "B/w.bin", "B/z.bin", "top.txt"])
        check("each entry finished", _done(tsv), ["A", "B", "top.txt"])
        check_true("header first", tsv.read_text().startswith("#path\tsize\tmtime\n"))


def test_an_interrupted_entry_is_re_read_whole() -> None:
    """A finished, B cut off after one row: B is recorded again, once, and A is not."""
    with tempfile.TemporaryDirectory() as tmp:
        vol, tsv = _volume(Path(tmp)), Path(tmp) / "inv.tsv"
        tsv.write_text("#path\tsize\tmtime\nA/x.bin\t2\t0\nA/sub/y.bin\t3\t0\n#done\tA\nB/w.bin\t2\t0\n")
        code, out = run_main(INV.main, str(vol), str(tsv))
        check("exit", code, 0)
        check_true("says so", "re-recording unfinished: B" in out)
        check("no duplicates, nothing missing", _rows(tsv),
              ["A/sub/y.bin", "A/x.bin", "B/w.bin", "B/z.bin", "top.txt"])
        check("A kept its old row", "A/x.bin\t2\t0" in tsv.read_text(), True)


def test_an_inventory_without_markers_is_trusted() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        vol, tsv = _volume(Path(tmp)), Path(tmp) / "inv.tsv"
        tsv.write_text("#path\tsize\tmtime\nA/x.bin\t2\t0\n")
        run_main(INV.main, str(vol), str(tsv))
        check("A not re-read", [r for r in _rows(tsv) if r.startswith("A/")], ["A/x.bin"])


def test_subdirs_and_missing_root() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        vol, tsv = _volume(Path(tmp)), Path(tmp) / "inv.tsv"
        run_main(INV.main, str(vol), str(tsv), "B")
        check("only B", _rows(tsv), ["B/w.bin", "B/z.bin"])
        check("missing root", run_main(INV.main, str(Path(tmp) / "nope"), str(tsv))[0], 1)
    for given, root in (("F:\\Roms\\x", "/mnt/f/Roms/x"), ("F:", "/mnt/f"), ("F:\\", "/mnt/f"),
                        ("/media/me/RED/", "/media/me/RED")):
        check(f"mount root {given}", INV.mount_root(given), root)


if __name__ == "__main__":
    sys.exit(run(globals(), "inventory-drive"))
