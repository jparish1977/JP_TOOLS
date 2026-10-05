#!/usr/bin/env python3
"""
JP_TOOLS/tests/test_inventory_drive.py
inventory-drive.py: what a run records, and that resuming re-reads an
interrupted entry whole instead of skipping it or recording it twice.

    python tests/test_inventory_drive.py
"""

import os
import sys
import tempfile
from pathlib import Path

from _harness import check, check_true, load_script, run
from fake_tools import run_main
from jpexit import NOT_RUN, OK

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
        check("exit", run_main(INV.main, str(vol), str(tsv))[0], OK)
        check("rows", _rows(tsv), ["A/sub/y.bin", "A/x.bin", "B/w.bin", "B/z.bin", "top.txt"])
        check("each entry finished", _done(tsv), ["A", "B", "top.txt"])
        check_true("header first", tsv.read_text().startswith("#path\tsize\tmtime\n"))


def test_an_interrupted_entry_is_re_read_whole() -> None:
    """A finished, B cut off after one row: B is recorded again, once, and A is not."""
    with tempfile.TemporaryDirectory() as tmp:
        vol, tsv = _volume(Path(tmp)), Path(tmp) / "inv.tsv"
        tsv.write_text("#path\tsize\tmtime\nA/x.bin\t2\t0\nA/sub/y.bin\t3\t0\n#done\tA\nB/w.bin\t2\t0\n")
        code, out = run_main(INV.main, str(vol), str(tsv))
        check("exit", code, OK)
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


def test_a_file_that_cannot_be_stat_d_leaves_its_entry_unfinished() -> None:
    """A dangling link in A cannot be stat'd: named, A not marked done (the
    next run re-reads it), B and top.txt done, and the run is NOT_RUN.
    Control: test_a_fresh_run, the same volume without it, is OK."""
    with tempfile.TemporaryDirectory() as tmp:
        vol, tsv = _volume(Path(tmp)), Path(tmp) / "inv.tsv"
        (vol / "A" / "gone.lnk").symlink_to(vol / "A" / "nowhere")
        code, out = run_main(INV.main, str(vol), str(tsv))
        check("stat failure: exit", code, NOT_RUN)
        check("stat failure: A left unfinished", _done(tsv), ["B", "top.txt"])
        check_true("stat failure: named", "not inventoried: " in out and "gone.lnk" in out)


def test_a_directory_that_cannot_be_read_leaves_its_entry_unfinished() -> None:
    """os.walk skipped an unreadable directory without a word, and A was
    marked done with its subtree missing."""
    if os.geteuid() == 0:
        print("SKIP: running as root, which reads a mode-000 directory anyway")
        return
    with tempfile.TemporaryDirectory() as tmp:
        vol, tsv = _volume(Path(tmp)), Path(tmp) / "inv.tsv"
        locked = vol / "A" / "sub"
        locked.chmod(0)
        try:
            code, out = run_main(INV.main, str(vol), str(tsv))
        finally:
            locked.chmod(0o755)
        check("unreadable dir: exit", code, NOT_RUN)
        check("unreadable dir: A left unfinished", _done(tsv), ["B", "top.txt"])
        check_true("unreadable dir: named", "not inventoried: " in out and "A/sub" in out)


def test_subdirs_and_missing_root() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        vol, tsv = _volume(Path(tmp)), Path(tmp) / "inv.tsv"
        run_main(INV.main, str(vol), str(tsv), "B")
        check("only B", _rows(tsv), ["B/w.bin", "B/z.bin"])
        check("missing root", run_main(INV.main, str(Path(tmp) / "nope"), str(tsv))[0], NOT_RUN)
    for given, root in (("F:\\Roms\\x", "/mnt/f/Roms/x"), ("F:", "/mnt/f"), ("F:\\", "/mnt/f"),
                        ("/media/me/RED/", "/media/me/RED")):
        check(f"mount root {given}", INV.mount_root(given), root)


if __name__ == "__main__":
    sys.exit(run(globals(), "inventory-drive"))
