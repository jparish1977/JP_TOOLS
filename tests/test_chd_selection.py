#!/usr/bin/env python3
"""
JP_TOOLS/tests/test_chd_selection.py
chd.py's pure parts: chdman's arguments, what `info` says, which files a
batch converts, and what --delete-originals is allowed to delete.

    python tests/test_chd_selection.py
"""

import sys
import tempfile
from pathlib import Path

from _harness import check, check_true, run
from chdtool.chdman import CreateOptions, compression_args, create_args, is_cd, parse_info
from chdtool.selection import delete_original, parse_extensions, pending, select, sheet_tracks

CUE = 'FILE "game.bin" BINARY\n  TRACK 01 MODE1/2048\n    INDEX 01 00:00:00\n'


def test_raw_images_get_a_unit_size() -> None:
    """createraw refuses to run without --unitsize; an image gets 2048-byte hunks."""
    src, dst = Path("/d/a.iso"), Path("/d/a.chd")
    check("iso", create_args(src, dst, CreateOptions()),
          ["createraw", "--input", "/d/a.iso", "--output", "/d/a.chd",
           "--hunksize", "2048", "--unitsize", "512"])
    check("unknown extension gets 4096",
          create_args(Path("/d/a.dump"), dst, CreateOptions())[-3], "4096")
    check("explicit hunk size wins",
          create_args(src, dst, CreateOptions(hunk_size="8192"))[-3], "8192")


def test_sheets_are_cds() -> None:
    args = create_args(Path("/d/g.CUE"), Path("/d/g.chd"), CreateOptions(force=True))
    check("cue", args, ["createcd", "--input", "/d/g.CUE", "--output", "/d/g.chd", "--force"])
    check("gdi", create_args(Path("g.gdi"), Path("g.chd"), CreateOptions())[0], "createcd")


def test_a_child_takes_its_geometry_from_the_parent() -> None:
    args = create_args(Path("b.img"), Path("b.chd"), CreateOptions(parent=Path("a.chd")))
    check("child", args, ["createraw", "--input", "b.img", "--output", "b.chd",
                          "--outputparent", "a.chd"])


def test_compression() -> None:
    check("preset", compression_args("best"), ["--compression", "lzma"])
    check("default preset", compression_args("default"), [])
    check("codec list passes through", compression_args("cdlz,cdfl"),
          ["--compression", "cdlz,cdfl"])
    check("none given", compression_args(None), [])


def test_info() -> None:
    text = ("File Version: 5\nSHA1:         abc\nData SHA1:    def\n"
            "Metadata:     Tag='CHT2'  Index=0  Length=86 bytes\n")
    info = parse_info(text)
    check("sha1", info.get("sha1"), "abc")
    check("data sha1", info.get("data_sha1"), "def")
    check_true("CD tag", is_cd(text))
    check("DVD is not a CD", is_cd("Metadata:     Tag='DVD '  Index=0"), False)
    check("no metadata is not a CD", is_cd("SHA1: abc"), False)


def test_extensions() -> None:
    default = frozenset({".iso"})
    check("spec", parse_extensions("iso, .BIN,,cue", default), frozenset({".iso", ".bin", ".cue"}))
    check("empty is the default", parse_extensions(None, default), default)


def test_sheet_tracks() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        d = Path(tmp)
        (d / "g.cue").write_text(CUE + 'FILE "g (Track 2).bin" BINARY\n')
        (d / "g.gdi").write_text('2\n1 0 4 2352 track01.bin 0\n2 450 0 2352 "track 02.raw" 0\n')
        (d / "g.toc").write_text('CD_ROM\nTRACK MODE1\nDATAFILE "data.iso"\n')
        check("cue", sheet_tracks(d / "g.cue"), [d / "game.bin", d / "g (Track 2).bin"])
        check("gdi", sheet_tracks(d / "g.gdi"), [d / "track01.bin", d / "track 02.raw"])
        check("toc", sheet_tracks(d / "g.toc"), [d / "data.iso"])


def test_a_sheet_and_its_tracks_are_one_disc() -> None:
    """The cue is converted; the bin it names is not converted on its own too."""
    with tempfile.TemporaryDirectory() as tmp:
        d = Path(tmp)
        for name in ("game.bin", "other.iso", "done.iso", "done.chd", "notes.txt"):
            (d / name).write_bytes(b"x")
        (d / "game.cue").write_text(CUE)
        exts = frozenset({".cue", ".bin", ".iso"})
        files, notes = pending(d, exts, force=False)
        check("pending", [f.name for f in files], ["game.cue", "other.iso"])
        check("no notes", notes, [])
        files, _ = pending(d, exts, force=True)
        check("force includes converted", [f.name for f in files],
              ["done.iso", "game.cue", "other.iso"])


def test_an_unreadable_sheet_is_said() -> None:
    chosen, notes = select([Path("/nonexistent/x.cue")], frozenset({".cue"}))
    check("still selected", chosen, [Path("/nonexistent/x.cue")])
    check_true("and noted", len(notes) == 1 and "x.cue" in notes[0])


def test_delete_stays_in_the_sheets_directory() -> None:
    """A sheet is text; `FILE "../x"` must not delete a file outside its directory."""
    with tempfile.TemporaryDirectory() as tmp:
        d = Path(tmp) / "disc"
        d.mkdir()
        outside = Path(tmp) / "keep.bin"
        outside.write_bytes(b"x")
        (d / "game.bin").write_bytes(b"x")
        (d / "game.cue").write_text(CUE + 'FILE "../keep.bin" BINARY\nFILE "gone.bin" BINARY\n')
        failures = delete_original(d / "game.cue")
        check("disc directory empty", sorted(p.name for p in d.iterdir()), [])
        check_true("outside kept", outside.exists())
        check_true("and reported", len(failures) == 1 and "keep.bin" in failures[0])


def test_an_unreadable_sheet_is_not_deleted() -> None:
    """Deleting a sheet whose tracks are unknown would orphan them."""
    with tempfile.TemporaryDirectory() as tmp:
        sheet = Path(tmp) / "dir.cue"
        sheet.mkdir()  # reading it raises IsADirectoryError, an OSError
        failures = delete_original(sheet)
        check_true("kept", sheet.exists())
        check_true("reported", len(failures) == 1 and "could not read" in failures[0])


if __name__ == "__main__":
    sys.exit(run(globals(), "chd selection"))
