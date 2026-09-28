#!/usr/bin/env python
"""
JP_TOOLS/tests/test_spool_audit_purge.py
spool-audit --purge: removes only leftover TempDir files, only regular files,
never through a link, and reports what it could not destroy. Every test drives
a real fixture filesystem; no root needed.

    python tests/test_spool_audit_purge.py
"""

import os
import pathlib
import signal
import sys
import tempfile

from _harness import FAILURES, check, check_true, run
from spool_audit_support import _Blocked, first_note, spool
from spoolaudit.classify import classify
from spoolaudit.model import Entry, Kind, Listing, Removal, Verdict
from spoolaudit.purge import delete_residue, purge_outcome
from spoolaudit.read import read_spool


def test_purge_refuses_a_path_that_steps_outside() -> None:
    """'..' is a real directory, not a symlink, so O_NOFOLLOW is no defence
    against it -- the component check is the one path decision left in code,
    and this proves it fires. No listing can produce such a name (iterdir
    cannot return one), so an entry carrying it did not come from the audit."""
    with tempfile.TemporaryDirectory() as raw:
        root = pathlib.Path(raw)
        (root / "spool").mkdir()
        target = root / "outside.ps"
        target.write_bytes(b"%!PS\n")

        res = delete_residue(
            str(root / "spool"),
            (Entry(name="../outside.ps", kind=Kind.TEMP),),
        )
        removed, failed = res.removed, res.failed
        check("nothing removed", removed, ())
        check("one refusal", len(failed), 1)
        check_true("the refusal says the name is not the audit's",
                   "refused" in first_note(failed))
        check_true("the file outside the spool survives", target.exists())


def test_purge_never_traverses_a_symlinked_directory() -> None:
    """A symlinked TempDir must not take the delete out of the audited
    directory. The refusal is the kernel's ELOOP, and this drives the deleter
    directly with the hostile entry, so the guard is proven independent of
    the audit ever refusing to produce one."""
    with tempfile.TemporaryDirectory() as raw:
        root = pathlib.Path(raw)
        outside = root / "outside"
        outside.mkdir()
        secret = outside / "secret.ps"
        secret.write_bytes(b"%!PS\nSECRET\n")
        spool = root / "spool"
        spool.mkdir()
        os.symlink(outside, spool / "tmp")

        res = delete_residue(
            str(spool), (Entry(name="tmp/secret.ps", kind=Kind.TEMP, in_temp=True),),
        )
        removed, failed = res.removed, res.failed
        check("nothing removed", removed, ())
        check("one refusal", len(failed), 1)
        check_true(f"named as a symlink, NOT followed (got: {first_note(failed)!r})",
                   "NOT followed" in first_note(failed))
        check_true("the file on the other side survives", secret.exists())


def test_purge_refuses_a_leaf_symlink_instead_of_claiming_destruction() -> None:
    """Unlinking a symlink removes the link and leaves the content readable,
    so 'removed' over one is a false assurance of destruction, the worst
    failure this tool can have."""
    with tempfile.TemporaryDirectory() as raw:
        root = pathlib.Path(raw)
        real = root / "real.ps"
        real.write_bytes(b"%!PS\n")
        spool = root / "spool"
        spool.mkdir()
        link = spool / "leak.ps"
        os.symlink(real, link)

        res = delete_residue(
            str(spool), (Entry(name="leak.ps", kind=Kind.TEMP),),
        )
        removed, failed = res.removed, res.failed
        check("nothing reported removed", removed, ())
        check("one refusal", len(failed), 1)
        check_true("says it is a symlink", "symlink" in first_note(failed))
        check_true("the target survives", real.exists())
        # The link too: removing it would be the same false claim one step
        # removed, since the report line would still read as content destroyed.
        check_true("the link itself is untouched", link.is_symlink())


def test_purge_refuses_what_is_not_a_regular_file() -> None:
    """The device-node analogue, testable without root: a fifo occupying a
    victim's name. Two guards must both fire -- O_NONBLOCK so the open does
    not hang waiting for a writer (a hang in a security tool reads as 'still
    checking'), and S_ISREG so the unlink is refused by type."""
    if not hasattr(os, "mkfifo") or not hasattr(signal, "SIGALRM"):
        print("  note: no os.mkfifo/SIGALRM on this platform, fifo purge case skipped")
        return

    def _too_slow(signum: int, frame: object) -> None:
        raise _Blocked("blocked opening a fifo victim")

    previous = signal.signal(signal.SIGALRM, _too_slow)
    signal.alarm(5)
    try:
        with tempfile.TemporaryDirectory() as raw:
            spool = pathlib.Path(raw)
            fifo = spool / "leak.ps"
            os.mkfifo(fifo)

            res = delete_residue(
                str(spool), (Entry(name="leak.ps", kind=Kind.TEMP),),
            )
            removed, failed = res.removed, res.failed
            check("nothing removed", removed, ())
            check("one refusal", len(failed), 1)
            check_true("refused by type", "not a regular file" in first_note(failed))
            check_true("the fifo still exists", fifo.exists() or fifo.is_fifo())
    except _Blocked:
        FAILURES.append(
            "delete_residue BLOCKED opening a fifo instead of refusing it; "
            "the O_NONBLOCK guard is not firing, and in the real tool this is "
            "a hang with no output"
        )
    finally:
        signal.alarm(0)
        signal.signal(signal.SIGALRM, previous)


def test_purge_counts_an_already_gone_file_as_removed() -> None:
    """The goal state is 'no file at that path', however reached. A live
    spool deletes its own temp files; racing one is not a failure.

    A missing PARENT is not the same thing: a renamed parent leaves the
    content alive at its new path, so "removed" there would claim a
    destruction the tool did not perform. It is a failure note, told apart
    from a removal."""
    with tempfile.TemporaryDirectory() as raw:
        spool = pathlib.Path(raw)
        res = delete_residue(
            str(spool),
            (
                Entry(name="gone.ps", kind=Kind.TEMP),
                Entry(name="tmp/also-gone.ps", kind=Kind.TEMP, in_temp=True),
            ),
        )
        check("a leaf already gone counts as removed", sorted(res.removed), ["gone.ps"])
        note = ("tmp/also-gone.ps (a parent component vanished mid-run; NOT removed by "
                "this tool, and it may survive elsewhere)")
        check("a leaf under a parent that is gone is a note, not a removal",
              tuple(res.failed), (note,))


def test_purge_removes_only_its_victims() -> None:
    """The one positive case, with a bystander: the victim goes, the
    neighbour survives byte-identical."""
    with tempfile.TemporaryDirectory() as raw:
        spool = pathlib.Path(raw)
        (spool / "tmp").mkdir()
        victim = spool / "tmp" / "leak.ps"
        victim.write_bytes(b"%!PS-Adobe-3.0\n")
        bystander = spool / "tmp" / "ppd"
        bystander.write_bytes(b'*PPD-Adobe: "4.3"\n')

        res = delete_residue(
            str(spool), (Entry(name="tmp/leak.ps", kind=Kind.TEMP, in_temp=True),),
        )
        check("the victim is reported removed", res.removed, ("tmp/leak.ps",))
        check("no failures", res.failed, ())
        # A single-link file is destroyed by its unlink, so it must NOT be
        # flagged removed-but-not-destroyed: a mutant that always raises the
        # flag would turn every clean purge into a false alarm and exit 1.
        check("nothing merely unlinked", res.undestroyed, ())
        check_true("and is actually gone", not victim.exists())
        check("the bystander is byte-identical",
              bystander.read_bytes(), b'*PPD-Adobe: "4.3"\n')


def test_purge_reports_a_hard_linked_victim_as_removed_but_not_destroyed() -> None:
    """Unlinking one link of a multiply-linked file removes the entry and
    destroys nothing: identical readable content survives at every other
    link, so `removed:` there would be a false assurance of destruction.
    Refusing is not the remedy either (that leaves the spool copy in place
    too): keep unlinking, say removed-but-not-destroyed, and raise the exit
    code. st_nlink comes from the fstat already taken for the S_ISREG check."""
    with tempfile.TemporaryDirectory() as raw:
        root = pathlib.Path(raw)
        spool = root / "spool"
        (spool / "tmp").mkdir(parents=True)
        victim = spool / "tmp" / "leak.ps"
        victim.write_bytes(b"%!PS-Adobe-3.0\nSECRET\n")
        other = root / "other-link.ps"   # outside the spool, same inode
        os.link(victim, other)

        res = delete_residue(
            str(spool), (Entry(name="tmp/leak.ps", kind=Kind.TEMP, in_temp=True),),
        )
        check_true("the spool's entry IS still unlinked", not victim.exists())
        check("but it is not counted as destroyed", res.removed, ())
        check("and it is not a failure either", res.failed, ())
        check("it is removed-but-not-destroyed, with the surviving link count",
              res.undestroyed, (("tmp/leak.ps", 1),))
        check("the content survives byte-identical at the other link",
              other.read_bytes(), b"%!PS-Adobe-3.0\nSECRET\n")

        after = classify(read_spool(str(spool)))
        lines, code = purge_outcome(res, after, None)
        check("the exit code is raised even over a clean re-read", code, 1)
        check_true("the report says removed but NOT destroyed",
                   any(ln.startswith("removed but NOT destroyed: tmp/leak.ps")
                       for ln in lines))
        check_true("with the surviving links named",
                   any("1 other hard link(s)" in ln for ln in lines))
        check_true("and never a bare 'removed:' claim for it",
                   not any(ln == "removed: tmp/leak.ps" for ln in lines))


def test_purge_refuses_when_it_cannot_anchor() -> None:
    """The platform seam is the ONE injectable thing, because a test on Linux
    cannot make openat unavailable, and a guard nothing can make fire is a
    vacuous check. Refusal, not fallback: path arithmetic is the raceable
    thing the anchor exists to avoid."""
    with tempfile.TemporaryDirectory() as raw:
        spool = pathlib.Path(raw)
        victim = spool / "leak.ps"
        victim.write_bytes(b"%!PS\n")

        res = delete_residue(
            str(spool), (Entry(name="leak.ps", kind=Kind.TEMP),), can_anchor=False,
        )
        removed, failed = res.removed, res.failed
        check("the refused entry is named for the renderer too",
              res.failed_names, ("leak.ps",))
        check("nothing removed", removed, ())
        check("every entry refused", len(failed), 1)
        check_true("and the note says why", "cannot be anchored" in first_note(failed))
        check_true("the file survives", victim.exists())


def test_purge_reports_a_failed_unlink_as_still_there() -> None:
    """A denied unlink must read as a failed removal, not as files being
    recreated. Root bypasses the permission bit, so this is skipped there,
    and the error class is asserted so the note cannot drift into naming a
    cause it does not know."""
    if getattr(os, "geteuid", lambda: 1)() == 0:
        print("  note: running as root, failed-unlink purge case skipped")
        return
    with tempfile.TemporaryDirectory() as raw:
        spool = pathlib.Path(raw)
        (spool / "tmp").mkdir()
        victim = spool / "tmp" / "leak.ps"
        victim.write_bytes(b"%!PS\n")
        os.chmod(spool / "tmp", 0o555)
        try:
            res = delete_residue(
                str(spool),
                (Entry(name="tmp/leak.ps", kind=Kind.TEMP, in_temp=True),),
            )
        finally:
            os.chmod(spool / "tmp", 0o755)
        removed, failed = res.removed, res.failed
        check("nothing removed", removed, ())
        check("one failure", len(failed), 1)
        check("and its raw name is carried for the renderer",
              res.failed_names, ("tmp/leak.ps",))
        check_true("named as a failed delete", "delete FAILED" in first_note(failed))
        check_true("with the error class", "PermissionError" in first_note(failed))
        check_true("the file is indeed still there", victim.exists())


def test_purge_outcome_never_forgets_a_failure() -> None:
    """The exit code may rise but never fall: a clean-looking re-read must
    not erase a removal that failed, or `--purge && echo SAFE` fires over a
    file the tool just said it could not remove."""
    clean_after = classify(spool([]))
    lines, code = purge_outcome(
        Removal(
            removed=("a.ps",),
            failed=("b.ps (delete FAILED: PermissionError; it is still there)",),
            failed_names=("b.ps",),
        ),
        clean_after, None,
    )
    check("a failure keeps exit at 1 over a clean re-read", code, 1)
    check_true("the failure is printed", any("NOT removed" in ln for ln in lines))
    check_true("the removal is printed", any(ln == "removed: a.ps" for ln in lines))

    _, ok_code = purge_outcome(
        Removal(removed=("a.ps",)), clean_after, False)
    check("no failures over a clean spool is 0", ok_code, 0)

    dirty_after = classify(spool(["d00077-001"]))
    _, dirty_code = purge_outcome(
        Removal(), dirty_after, None)
    check("what remains drives the code", dirty_code, 1)

    denied_after = classify(Listing(Verdict.DENIED))
    lines4, code4 = purge_outcome(
        Removal(removed=("a.ps",)), denied_after, None)
    check("an unreadable re-read is 2, not a success", code4, 2)
    check_true("and says nothing is proven",
               any("NOT a clean result" in ln for ln in lines4))


if __name__ == "__main__":
    sys.exit(run(globals(), "purge"))
