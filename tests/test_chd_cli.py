#!/usr/bin/env python3
"""
JP_TOOLS/tests/test_chd_cli.py
chd.py end to end against fake tools: the chdman arguments each command
sends, the exit status it returns, and what batches and imports leave on disk.
The arguments asserted here are the ones test_chd_chdman.py runs through a
real chdman.

    python tests/test_chd_cli.py
"""

import sys
import zipfile

from _harness import check, check_true, run
from chd_support import FAKE_7Z, FAKE_CHDMAN, FAKE_COPIER, cli
from fake_tools import calls, env, fake_tools

CUE = 'FILE "game.bin" BINARY\n  TRACK 01 MODE1/2048\n    INDEX 01 00:00:00\n'


def test_create_and_verify() -> None:
    with fake_tools(chdman=FAKE_CHDMAN) as (w, log):
        (w / "a.iso").write_bytes(b"iso")
        code, out = cli("create", str(w / "a.iso"), str(w / "a.chd"), "--compression", "best")
        check("create exit", code, 0)
        check("create argv", calls(log)[-1],
              ["chdman", "createraw", "--input", str(w / "a.iso"), "--output", str(w / "a.chd"),
               "--compression", "lzma", "--hunksize", "2048", "--unitsize", "512"])
        check_true("ratio reported", "Created:" in out)
        with env(FAKE_RC="3"):
            code, out = cli("verify", str(w / "a.chd"))
        check("verify passes chdman's status", code, 3)
        check_true("and says FAILED", "FAILED -- CHD is corrupt" in out)


def test_missing_input_and_missing_chdman() -> None:
    with fake_tools() as (w, _log):
        check("missing input", cli("verify", str(w / "nope.chd"))[0], 1)
        (w / "a.iso").write_bytes(b"iso")
        code, out = cli("create", str(w / "a.iso"), str(w / "a.chd"))
        check("no chdman", code, 2)
        check_true("names it", "chdman not found" in out and "mame-tools" in out)


def test_extract_picks_cd_by_metadata_tag() -> None:
    with fake_tools(chdman=FAKE_CHDMAN) as (w, log):
        (w / "a.chd").write_bytes(b"chd")
        with env(FAKE_INFO="Metadata:     Tag='CHT2'  Index=0"):
            check("cd exit", cli("extract", str(w / "a.chd"), str(w / "a.cue"))[0], 0)
        check("cd", calls(log)[-1][1], "extractcd")
        with env(FAKE_INFO="Metadata:     Tag='DVD '  Index=0"):
            cli("extract", str(w / "a.chd"), str(w / "a.iso"))
        check("not a cd", calls(log)[-1][1], "extractraw")


def test_delta_and_merge_name_the_parent() -> None:
    with fake_tools(chdman=FAKE_CHDMAN) as (w, log):
        for name in ("a.chd", "b.img"):
            (w / name).write_bytes(b"x")
        cli("delta", str(w / "a.chd"), str(w / "b.img"), str(w / "b.chd"))
        check("delta", calls(log)[-1],
              ["chdman", "createraw", "--input", str(w / "b.img"), "--output", str(w / "b.chd"),
               "--outputparent", str(w / "a.chd")])
        cli("merge", str(w / "a.chd"), str(w / "b.chd"), str(w / "m.chd"))
        check("merge", calls(log)[-1],
              ["chdman", "copy", "--input", str(w / "b.chd"), "--inputparent", str(w / "a.chd"),
               "--output", str(w / "m.chd")])


def test_compare_exit_status() -> None:
    with fake_tools(chdman=FAKE_CHDMAN) as (w, _log):
        (w / "a.chd").write_bytes(b"one")
        (w / "b.chd").write_bytes(b"one")
        (w / "c.chd").write_bytes(b"two")
        check("same", cli("compare", str(w / "a.chd"), str(w / "b.chd"))[0], 0)
        check("different", cli("compare", str(w / "a.chd"), str(w / "c.chd"))[0], 1)
        check("unreadable", cli("compare", str(w / "a.chd"), str(w / "nope.chd"))[0], 2)


def test_batch_create_converts_the_sheet_and_deletes_the_disc() -> None:
    with fake_tools(chdman=FAKE_CHDMAN) as (w, log):
        (w / "game.cue").write_text(CUE)
        (w / "game.bin").write_bytes(b"bin")
        (w / "other.iso").write_bytes(b"iso")
        code, _ = cli("batch-create", str(w), "--delete-originals")
        check("exit", code, 0)
        check("converted", [c[1:3] for c in calls(log)],
              [["createcd", "--input"], ["createraw", "--input"]])
        check("left", sorted(p.name for p in w.iterdir()), ["game.chd", "other.chd"])


def test_batch_create_fails_when_a_file_fails() -> None:
    with fake_tools(chdman=FAKE_CHDMAN) as (w, _log), env(FAKE_RC="1"):
        (w / "a.iso").write_bytes(b"iso")
        code, out = cli("batch-create", str(w), "--delete-originals")
        check("exit", code, 1)
        check_true("original kept", (w / "a.iso").exists())
        check_true("says why", "fake chdman failed" in out)


def test_import_without_the_converter_is_exit_2() -> None:
    """No converter on PATH is a message and exit 2, not a traceback."""
    with fake_tools(chdman=FAKE_CHDMAN) as (w, _log):
        (w / "g.nrg").write_bytes(b"nrg")
        code, out = cli("import", str(w / "g.nrg"))
        check("exit", code, 2)
        check_true("names it", "nrg2iso not found" in out)


def test_import_converts_then_cleans_up() -> None:
    with fake_tools(chdman=FAKE_CHDMAN, nrg2iso=FAKE_COPIER) as (w, log):
        (w / "g.nrg").write_bytes(b"nrg")
        check("exit", cli("import", str(w / "g.nrg"))[0], 0)
        check("steps", [c[:2] for c in calls(log)], [["nrg2iso", str(w / "g.nrg")],
                                                    ["chdman", "createraw"]])
        check("left", sorted(p.name for p in w.iterdir()), ["g.chd", "g.nrg"])


def test_import_never_overwrites_an_existing_intermediate() -> None:
    with fake_tools(chdman=FAKE_CHDMAN, nrg2iso=FAKE_COPIER) as (w, log):
        (w / "g.nrg").write_bytes(b"nrg")
        (w / "g.iso").write_bytes(b"mine")
        check("exit", cli("import", str(w / "g.nrg"))[0], 1)
        check("untouched", (w / "g.iso").read_bytes(), b"mine")
        check("nothing ran", calls(log), [])


def test_import_archive_converts_only_what_it_held() -> None:
    with fake_tools(chdman=FAKE_CHDMAN, named={"7z": FAKE_7Z}) as (w, log):
        with zipfile.ZipFile(w / "game.zip", "w") as z:
            z.writestr("game.cue", CUE)
            z.writestr("game.bin", "bin")
        (w / "unrelated.iso").write_bytes(b"iso")
        check("exit", cli("import", str(w / "game.zip"))[0], 0)
        check("cue only", [c[1] for c in calls(log) if c[0] == "chdman"], ["createcd"])
        check("left", sorted(p.name for p in w.iterdir()),
              ["game.chd", "game.zip", "unrelated.iso"])


def test_batch_import_counts_and_deletes_after_success() -> None:
    with fake_tools(chdman=FAKE_CHDMAN, nrg2iso=FAKE_COPIER) as (w, _log):
        (w / "a.nrg").write_bytes(b"nrg")
        (w / "b.iso").write_bytes(b"iso")
        code, out = cli("batch-import", str(w), "--delete-originals")
        check("exit", code, 0)
        check_true("counted", "2 converted, 0 failed" in out)
        check("left", sorted(p.name for p in w.iterdir()), ["a.chd", "b.chd"])


if __name__ == "__main__":
    sys.exit(run(globals(), "chd cli"))
