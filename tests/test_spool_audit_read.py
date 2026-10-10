#!/usr/bin/env python
"""
JP_TOOLS/tests/test_spool_audit_read.py
spool-audit: reading the spool and TempDir. Links are never followed,
nothing present goes unreported, and every way a listing can fail is its own
verdict. Runs against real fixture directories; no root needed.

    python tests/test_spool_audit_read.py
"""

import os
import pathlib
import sys
import tempfile
import threading
from collections.abc import Callable
from unittest import mock

from _harness import check, check_true, run
from spoolaudit.classify import classify, temp_child_note
from spoolaudit.model import Listing, TempFile, Verdict
from spoolaudit.read import _head, _list_names, _walk_temp, read_spool
from spoolaudit.render import render


def test_symlinks_are_never_followed() -> None:
    """The worst failure this tool can have: a false assurance of destruction.

    A symlink in TempDir pointing at a document, counted as content, would be
    unlinked by --purge while the target stays fully readable. Missing a file
    is bad; telling someone their secret is destroyed when it is not is worse.
    """
    note = temp_child_note(
        "tmp", "a-symlink.ps",
        is_symlink=True, is_dir=False, is_regular=False, target="../outside/secret.ps",
    )
    assert note is not None
    check_true("not examined", "symlink" in note)
    check_true("names the target", "../outside/secret.ps" in note)
    check_true("warns removal is useless", "would not remove the target" in note)

    audit = classify(Listing(Verdict.CLEAN, (), (), (note,)))
    check("cannot conclude", audit.exit_code, 2)
    check_true("never clean", "spool is clean" not in "\n".join(render(audit, frozenset())))


def test_non_regular_files_are_reported() -> None:
    """A FIFO, a subdirectory or a broken symlink must appear in the report,
    never be silently absent from every section."""
    fifo = temp_child_note("tmp", "a-fifo", is_symlink=False, is_dir=False, is_regular=False)
    assert fifo is not None
    check_true("says not regular", "not a regular file" in fifo)

    subdir = temp_child_note("tmp", ".cache", is_symlink=False, is_dir=True, is_regular=False)
    assert subdir is not None
    check_true("subdirectory noted", "subdirectory" in subdir)

    plain = temp_child_note("tmp", "0777f6a7", is_symlink=False, is_dir=False, is_regular=True)
    check("a plain file is examinable", plain, None)


def test_symlink_takes_precedence_over_dir_and_file() -> None:
    """is_dir() and is_file() both follow symlinks, so a symlink to a directory
    would be classified as a subdirectory and its target walked."""
    note = temp_child_note(
        "tmp", "link-to-dir", is_symlink=True, is_dir=True, is_regular=False, target="/etc",
    )
    assert note is not None
    check_true("reported as a symlink, not a subdirectory", "symlink" in note)
    check_true("not called a subdirectory", "subdirectory" not in note)


def test_unexamined_areas_are_never_clean() -> None:
    """An unreadable TempDir is not an empty one.

    A spool holding a PostScript document under a mode-000 tmp/ must never
    read as clean. Listing carries `unexamined` so it can say "top level
    readable, TempDir not".
    """
    listing = Listing(Verdict.CLEAN, (), (), ("tmp/ (permission denied)",))
    audit = classify(listing)

    check("verdict is not RETAINED", audit.verdict, Verdict.CLEAN)
    check_true("but it is NOT complete", not audit.complete)
    check("exit 2, cannot conclude", audit.exit_code, 2)

    text = "\n".join(render(audit, frozenset()))
    check_true("never claims clean", "spool is clean" not in text)
    check_true("says incomplete", "INCOMPLETE" in text)
    check_true("names the area", "tmp/ (permission denied)" in text)
    check_true("denies cleanliness", "NOT a clean result" in text)


def test_subdirectory_under_tempdir_is_reported() -> None:
    """tmp/.cache is a real directory a filter chain creates. A depth-1 listing
    would drop it and anything inside it."""
    audit = classify(Listing(Verdict.CLEAN, (), (), ("tmp/.cache/ (subdirectory, not examined)",)))
    check("exit 2", audit.exit_code, 2)
    check_true("named", ".cache" in "\n".join(render(audit, frozenset())))


def test_not_a_directory_is_its_own_outcome() -> None:
    """Pointing --spool at a file must not say "THAT PATH DOES NOT EXIST", a
    false statement about a file sitting right there."""
    audit = classify(Listing(Verdict.NOT_A_DIRECTORY))
    text = "\n".join(render(audit, frozenset()))

    check("exit 2", audit.exit_code, 2)
    check_true("says not a directory", "NOT A DIRECTORY" in text)
    check_true("does not claim absence", "DOES NOT EXIST" not in text)


def test_walk_survives_a_file_vanishing() -> None:
    """A live spool deletes temp files continuously. Losing the whole walk to
    one racing file would discard the audit on exactly the busy machine worth
    auditing."""
    with tempfile.TemporaryDirectory() as tmp:
        root = pathlib.Path(tmp)
        for i in range(5):
            (root / f"f{i}.ps").write_text("%!PS\n")

        real_stat = pathlib.Path.stat

        def flaky(self: pathlib.Path, *, follow_symlinks: bool = True) -> os.stat_result:
            if self.name == "f2.ps":
                raise FileNotFoundError(2, "gone")
            return real_stat(self, follow_symlinks=follow_symlinks)

        found: list[TempFile] = []
        unexamined: list[str] = []
        with mock.patch.object(pathlib.Path, "stat", flaky):
            _walk_temp(root, "tmp", "", found, unexamined)

        check("the other four survive", len(found), 4)
        check("the racing file is recorded", len(unexamined), 1)
        # Assert the RIGHT note, not only the counts: a racing temp file
        # reported as "not a regular file" would misdescribe it as a device.
        check_true("reported as a race, not as a device", "vanished" in unexamined[0])


def test_nothing_is_invisible() -> None:
    """The "not worse than ls" guarantee, as a test.

    The alternative to this tool is `sudo ls` read by a human, and `ls` never
    hides a file: a document copied to d00085-001.bak, an unrecognised stray
    file, a subdirectory. Anything present must appear SOMEWHERE in the
    report: as content, as a runtime artifact, or as unexamined.
    """
    with tempfile.TemporaryDirectory() as tmp:
        root = pathlib.Path(tmp)
        (root / "tmp" / ".cache").mkdir(parents=True)
        (root / "d00085-001").write_text("%!PS\n")
        (root / "c00085").touch()
        (root / "d00085-001.bak").write_text("%!PS\ncopy\n")
        (root / "stray-file").write_text("data\n")
        (root / "weird-dir").mkdir()
        (root / "tmp" / "ppd").write_text('*PPD-Adobe: "4.3"\n')
        (root / "tmp" / "real.ps").write_text("%!PS\n")
        (root / "tmp" / ".cache" / "fc").write_text("binary\n")

        listing = read_spool(str(root))
        audit = classify(listing, frozenset(), include_control=True)
        report = "\n".join(render(audit, frozenset()))

        def reported(rel: str) -> bool:
            """Whole-entry match on the relative path, not a substring.

            `"d00085-001" in report` is satisfied by the report line for
            d00085-001.bak, so it could not see the real document dropped
            while its backup is kept. Entries print as an
            indented relative path, optionally followed by an annotation, and a
            parent directory appears only as the prefix of its children.
            """
            for line in report.splitlines():
                s = line.strip()
                if s == rel or s.startswith(rel + " ") or s.startswith(rel + "/"):
                    return True
            return False

        present = sorted(
            str(q.relative_to(root)) for q in root.rglob("*")
        )
        invisible = [name for name in present if not reported(name)]
        check("every path appears in the report", invisible, [])

        # And the ones that matter are counted as content, not merely mentioned.
        counted = " ".join(e.name for e in audit.others)
        check_true("a .bak of a document is counted", "d00085-001.bak" in counted)
        check_true("an unrecognised stray is counted", "stray-file" in counted)
        check_true("a subdirectory is flagged", any("weird-dir" in u for u in audit.unexamined))


def test_walk_has_a_depth_limit() -> None:
    """Unbounded recursion on a hostile or pathological tree is a hang, and a
    hang in a security tool reads as 'still checking' forever."""
    with tempfile.TemporaryDirectory() as tmp:
        root = pathlib.Path(tmp)
        deep = root
        for i in range(14):
            deep = deep / f"d{i}"
        deep.mkdir(parents=True)
        (deep / "burrowed.ps").write_text("%!PS\n")

        found: list[TempFile] = []
        unexamined: list[str] = []
        _walk_temp(root, "tmp", "", found, unexamined)

        check("nothing collected past the limit", len(found), 0)
        check_true("and it says why", any("deeper than" in u for u in unexamined))


def test_unreadable_tempdir_is_never_clean() -> None:
    """An unreadable TempDir never reads as clean, whatever user runs this.

    A chmod-000 fixture cannot test this as root, because root bypasses
    permission bits, and root is exactly how the tool is documented to run.
    Injecting the PermissionError tests the handler directly and holds for any
    uid. Mutating the `except PermissionError` branch in read_spool to `pass`
    makes this fail.
    """
    with tempfile.TemporaryDirectory() as tmp:
        root = pathlib.Path(tmp)
        (root / "tmp").mkdir()
        (root / "d00085-001").write_text("%!PS\n")

        def deny(*_args: object, **_kwargs: object) -> None:
            raise PermissionError(13, "Permission denied")

        # Passed in through read_spool's seam, not monkeypatched onto the
        # module, so the test does not depend on how read_spool looks it up.
        listing = read_spool(str(root), walk=deny)

        audit = classify(listing, frozenset(), include_control=True)
        report = "\n".join(render(audit, frozenset()))
        check("an unreadable TempDir exits 2", audit.exit_code, 2)
        check_true(
            "and the TempDir is named as unexamined",
            any("permission denied" in u.lower() for u in audit.unexamined),
        )
        check_true(
            "and the report never calls it clean",
            "spool is clean" not in report.lower(),
        )


def test_walk_against_a_real_filesystem() -> None:
    """The traversal against a real directory tree.

    An unreadable TempDir read as clean, a subdirectory dropped with its
    contents, a symlink followed: those live in traversal, not in
    classification, so tests over literal listings cannot see them. No root
    and no printer needed.
    """
    with tempfile.TemporaryDirectory() as tmp:
        root = pathlib.Path(tmp)
        (root / ".cache" / "deep").mkdir(parents=True)
        (root / ".cache" / "deep" / "leak.ps").write_text("%!PS\nnested\n")
        (root / ".cache" / "driver.ppd").write_text('*PPD-Adobe: "4.3"\n')
        (root / "plain.ps").write_text("%!PS\ntop level\n")
        (root / "empty").touch()
        os.symlink("/etc/passwd", root / "a-link")
        try:
            os.mkfifo(root / "a-fifo")
            have_fifo = True
        except (OSError, AttributeError):
            have_fifo = False

        found: list[TempFile] = []
        unexamined: list[str] = []
        _walk_temp(root, "tmp", "", found, unexamined)

        names = sorted(f.name for f in found)
        check("nested file found", "\n".join(names).count(".cache/deep/leak.ps"), 1)
        check_true("top level found", "plain.ps" in names)
        check_true("nested ppd found", ".cache/driver.ppd" in names)

        notes = "\n".join(unexamined)
        check_true("symlink not followed", "a-link -> /etc/passwd" in notes)
        check_true("and says so", "would not remove the target" in notes)
        check_true("symlink not in found", not any("a-link" in n for n in names))
        if have_fifo:
            check_true("fifo reported", "a-fifo" in notes and "not a regular file" in notes)

        # The nested PPD must still classify as a harmless driver cache, and
        # the nested document must not.
        audit = classify(Listing(Verdict.CLEAN, (), tuple(found), tuple(unexamined)))
        content = sorted(e.name for e in audit.others)
        check_true("nested document counted", any("leak.ps" in c for c in content))
        check_true("nested ppd is an artifact", any("driver.ppd" in e.name for e in audit.artifacts))
        check_true("empty file is an artifact", any("empty" in e.name for e in audit.artifacts))
        check("cannot be clean with a symlink present", audit.exit_code, 2)


def test_read_spool_maps_every_listing_failure_to_its_own_verdict() -> None:
    """Four outcomes, four verdicts. Collapsing any two would let "could not
    read" pass for "nothing there", which is what this tool exists to prevent."""
    def raiser(exc: BaseException) -> Callable[[pathlib.Path], list[str]]:
        def go(_p: pathlib.Path) -> list[str]:
            raise exc
        return go

    for exc, want, why in [
        (PermissionError(), Verdict.DENIED, "unreadable"),
        (FileNotFoundError(), Verdict.MISSING, "no such path"),
        (NotADirectoryError(), Verdict.NOT_A_DIRECTORY, "a file, not a directory"),
    ]:
        got = read_spool("/does-not-matter", lister=raiser(exc))
        check(f"listing failure, {why}", got.verdict, want)

    # ELOOP, ESTALE and EIO must NOT be reported as "permission denied, re-run
    # with sudo", which sends the user to do something that fails identically.
    odd = read_spool("/x", lister=raiser(OSError(40, "Too many levels of symbolic links")))
    check("an unexpected OSError is DENIED", odd.verdict, Verdict.DENIED)
    check_true("and names the error class instead of blaming permissions",
               any("OSError" in u for u in odd.unexamined))


def test_read_spool_distinguishes_the_tempdir_failures() -> None:
    """An absent TempDir is not an unexamined one; otherwise a spool without a
    tmp/ could never exit 0."""
    with tempfile.TemporaryDirectory() as tmp:
        root = pathlib.Path(tmp)
        (root / "tmp").mkdir()

        def raiser(exc: BaseException) -> Callable[..., None]:
            def go(*_a: object, **_k: object) -> None:
                raise exc
            return go

        gone = read_spool(str(root), walk=raiser(FileNotFoundError()))
        check("no TempDir at all leaves nothing unexamined", list(gone.unexamined), [])

        notdir = read_spool(str(root), walk=raiser(NotADirectoryError()))
        check_true("a TempDir that is not a directory is unexamined",
                   any("not a directory" in u for u in notdir.unexamined))

        io_err = read_spool(str(root), walk=raiser(OSError(5, "I/O error")))
        check_true("an I/O error on TempDir names the class",
                   any("OSError" in u for u in io_err.unexamined))


def test_head_reads_a_prefix_and_never_raises() -> None:
    """`_head` is a read, and a read is testable with a temp file, so it is
    tested rather than exempted from coverage."""
    with tempfile.TemporaryDirectory() as tmp:
        root = pathlib.Path(tmp)
        f = root / "doc.ps"
        f.write_bytes(b"%!PS-Adobe-3.0\nlots more content after the prefix\n")
        check("reads only the first n bytes", _head(f, 4), "%!PS")
        head = _head(f)
        check_true("and the default reads enough to recognise a format",
                   head is not None and head.startswith("%!PS-Adobe"))
        raster = root / "page"
        raster.write_bytes(b"RaSt" + b"m" * 63 + b"\x00" + b"\x01" * 64)
        check("the default reaches the end of a raster MediaClass field",
              _head(raster), "RaSt" + "m" * 63 + "\x00")

        empty = root / "empty"
        empty.touch()
        check("an empty file gives an empty head", _head(empty), "")

        # An unreadable file arrives as None rather than raising (raising would
        # abort the whole listing over one file) and rather than "" ("could not
        # read" is not "read nothing"). Both callers list a None as unexamined,
        # never classify it.
        missing = root / "not-there"
        check("a missing file gives None, not '', and does not raise",
              _head(missing), None)

        binary = root / "raw"
        binary.write_bytes(b"\xff\xfe\x00garbage")
        check_true("undecodable bytes are replaced, not raised",
                   isinstance(_head(binary), str))


def test_head_reads_only_a_regular_file() -> None:
    """`_head` protects itself rather than relying on its callers' lstat.

    A caller that hands it a fifo must not hang the run (as root, on a live
    machine, silently); a fifo or a directory is not "read nothing"; and a
    file swapped for a symlink after the caller's lstat is not followed. A
    hang is caught by running the read in a thread with a deadline.
    """
    with tempfile.TemporaryDirectory() as tmp:
        root = pathlib.Path(tmp)
        target = root / "doc.ps"
        target.write_bytes(b"%!PS-Adobe-3.0\n")
        link = root / "link"
        link.symlink_to(target)
        check("a symlink to a document is not followed", _head(link), None)

        sub = root / "sub"
        sub.mkdir()
        check("a directory gives None", _head(sub), None)

        # A regular file that opens and then fails to read (EIO on a bad
        # disk) is "could not read" too, never a crash of the whole listing.
        with mock.patch("spoolaudit.read.os.read", side_effect=OSError(5, "I/O error")):
            check("a read error after the open gives None", _head(target), None)

        if not hasattr(os, "mkfifo"):
            print("  note: no mkfifo on this platform, fifo checks skipped")
            return
        fifo = root / "fifo"
        os.mkfifo(fifo)
        got: list[str | None] = []
        reader = threading.Thread(target=lambda: got.append(_head(fifo)), daemon=True)
        reader.start()
        reader.join(5)
        check_true("a fifo with no writer does not hang the read", not reader.is_alive())
        check("a fifo gives None, not ''", got, [None])


def test_list_names_is_sorted_and_raises() -> None:
    """The real lister, not a fake: names only, sorted, so the listing is the
    same on every run, and a listing failure reaches read_spool as the error
    it is, which maps it to a verdict."""
    with tempfile.TemporaryDirectory() as tmp:
        root = pathlib.Path(tmp)
        for name in ("d00085-001", "c00085", "tmp", "Z-upper"):
            (root / name).touch()
        check("names, sorted", _list_names(root),
              ["Z-upper", "c00085", "d00085-001", "tmp"])
        try:
            _list_names(root / "not-there")
            check_true("a missing directory raises", False)
        except FileNotFoundError:
            check_true("a missing directory raises", True)
        check("and read_spool calls it MISSING",
              read_spool(str(root / "not-there")).verdict, Verdict.MISSING)


if __name__ == "__main__":
    sys.exit(run(globals(), "reading"))
