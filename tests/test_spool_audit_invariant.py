#!/usr/bin/env python3
"""
JP_TOOLS/tests/test_spool_audit_invariant.py
Two properties, checked across every path spool-audit.py can take.

    EXIT 0 MUST MEAN: no file this tool would call print data is still there.
    AND THE SPOOL MUST BE BYTE-IDENTICAL AFTERWARDS -- except, under --purge,
    for EXACTLY the files the fixture names as removed, with every survivor
    still byte-identical and only the parents of removed files allowed to
    move their timestamps.

The first is the promise that matters: `spool-audit.py 85 && echo SAFE`
firing while a readable copy of job 85's document sits on disk is the
failure. The second is what a report run and a purge run are allowed to
change: nothing, and precisely the declared victims. Every fixture snapshots
the tree before the run and compares it after: names, modes, sizes, content
hashes, symlink targets, mtime and ctime. "Removed exactly X" is asserted in
both directions: X gone, and nothing outside X touched.

The property lives in the relationship between what the tool says and what
it returns, not in any one branch, so this drives the real CLI as a
subprocess and judges it with an oracle that shares none of its code. Every
invocation pins its exit code: a run that fails before doing anything (an
unknown flag, exit 2) would otherwise pass every "the file survived" check.

THE ORACLE
    Deliberately dumber than the tool and independent of it: a regular file is
    print data if its first bytes carry a print-format magic number, or if its
    name matches d<job>-<doc> at the top level. If the tool exits 0 while the
    oracle can still find one, the tool is wrong, not the oracle. Sharing the
    tool's classifier would make this agree with the tool by construction.

    python tests/test_spool_audit_invariant.py
"""

import contextlib
import hashlib
import os
import re
import subprocess
import sys
import tempfile
from collections.abc import Iterator
from pathlib import Path, PurePosixPath

from _harness import FAILURES, ROOT, run

TOOL = ROOT / "spool-audit.py"

# Independent of the tool's DOCUMENT_MAGIC on purpose.
MAGIC = (b"%!PS", b"%PDF", b"\x1b%-12345X", b"@PJL", b"\x1b*", b"UNIRAST\x00")
# CUPS raster sync words, from cups/raster.h's hex values. A raster stream
# has the page header next, whose MediaClass is a C string in 64 bytes.
SYNC = tuple(w.to_bytes(4, order) for w in (0x52615333, 0x52615374, 0x52615332)
             for order in ("big", "little"))


def _is_print(head: bytes) -> bool:
    if any(head.startswith(m) for m in MAGIC):
        return True
    return head[:4] in SYNC and b"\x00" in head[4:68]
DOCNAME = re.compile(r"^d\d+-\d+$")
PDF = b"%PDF-1.7\n"
PS = b"%!PS-Adobe-3.0\n"

State = dict[str, tuple[object, ...]]


def oracle(spool: Path) -> list[str]:
    """Every path under SPOOL that a careful human would call print data."""
    hits = []
    for p in sorted(spool.rglob("*")):
        if p.is_symlink() or not p.is_file():
            continue
        rel = str(p.relative_to(spool))
        try:
            head = p.open("rb").read(68)
        except OSError:
            # reason: unreadable cannot be ruled out, so it counts; the tool
            # must not claim success over something neither could read
            hits.append(rel)
            continue
        if _is_print(head) or DOCNAME.match(p.name):
            hits.append(rel)
    return hits


def snapshot(spool: Path) -> State:
    """Everything about the tree that a read must not change.

    Not atime: reading is what this tool does, and on a relatime filesystem
    that write-back is the kernel's. ctime is here because it catches a chmod,
    which changes a file without changing its content.
    """
    state: State = {}
    for p in sorted(spool.rglob("*")):
        rel = str(p.relative_to(spool))
        if p.is_symlink():
            state[rel] = ("L", os.readlink(p))
            continue
        st = p.stat()
        if p.is_dir():
            state[rel] = ("D", st.st_mode, st.st_mtime_ns, st.st_ctime_ns)
            continue
        try:
            digest = hashlib.sha256(p.read_bytes()).hexdigest()
        except OSError:
            # reason: an unreadable file still has metadata, and its mode is
            # what would change if anything tried to make it readable
            digest = "unreadable"
        state[rel] = ("F", st.st_mode, st.st_size, st.st_mtime_ns, st.st_ctime_ns, digest)
    return state


def _audit(spool: Path, *args: str) -> tuple[int, str]:
    proc = subprocess.run(
        [sys.executable, str(TOOL), "--spool", str(spool), "--conf", "/dev/null", *args],
        capture_output=True, text=True, errors="surrogateescape", check=False,
    )
    return proc.returncode, proc.stdout + proc.stderr


def _parents(removed: set[str]) -> set[str]:
    return {str(q) for r in removed for q in PurePosixPath(r).parents if str(q) != "."}


def _changes(before: State, after: State, gone: set[str]) -> list[str]:
    """What differs between BEFORE and AFTER, other than GONE going.

    A parent directory of a removed file may move its timestamps (an unlink
    updates them); its type and mode still may not change.
    """
    parents = _parents(gone)
    problems = []
    for k in sorted(set(before) | set(after)):
        b, a = before.get(k), after.get(k)
        if k in gone:
            if a is not None:
                problems.append(f"{k} should have been removed and was not")
        elif b is None:
            problems.append(f"{k} appeared")
        elif a is None:
            problems.append(f"{k} was removed and should not have been")
        elif b != a and not (k in parents and b[0] == a[0] == "D" and b[1] == a[1]):
            problems.append(f"{k} was modified")
    return problems


def invariant(label: str, spool: Path, *args: str, expect: int,
              removed: tuple[str, ...] = ()) -> None:
    """Run the tool on SPOOL and check both properties, and that it exits EXPECT.

    REMOVED names what a --purge fixture expects gone, exactly. A fixture
    naming a path it never planted is a fixture bug, and fails as one.
    """
    gone = set(removed)
    before = snapshot(spool)
    FAILURES.extend(f"{label}: FIXTURE BUG: expects {r!r} removed but never planted it"
                    for r in sorted(gone - set(before)))
    code, out = _audit(spool, *args)
    left = oracle(spool)
    tail = f"\n    output: {out.strip()[:300]}"
    if code != expect:
        FAILURES.append(f"{label}: exit {code}, expected {expect}{tail}")
    if code == 0 and left:
        FAILURES.append(f"{label}: exited 0 with print data still present: {left}{tail}")
    problems = _changes(before, snapshot(spool), gone)
    if problems:
        FAILURES.append(f"{label}: THE TOOL TOUCHED WHAT IT SHOULD NOT: {problems}{tail}")


def _still(label: str, path: Path, content: bytes | None = None) -> None:
    """PATH, outside the spool, must survive (with CONTENT, when given)."""
    if not path.exists():
        FAILURES.append(f"{label}: {path.name}, OUTSIDE the spool, is gone")
    elif content is not None and path.read_bytes() != content:
        FAILURES.append(f"{label}: {path.name}, OUTSIDE the spool, changed")


@contextlib.contextmanager
def spool(**files: bytes) -> Iterator[Path]:
    """A spool with a tmp/ and FILES (name: content; `tmp__x` means tmp/x)."""
    with tempfile.TemporaryDirectory() as raw:
        s = Path(raw) / "spool"
        (s / "tmp").mkdir(parents=True)
        for name, content in files.items():
            path = s / name.replace("__", "/")
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(content)
        yield s


# --- report runs -------------------------------------------------------------

def test_empty() -> None:
    with spool() as s:
        invariant("empty", s, expect=0)


def test_scoped_to_a_job() -> None:
    """A copy under an unrecognised name carries no job id, so no job-scoped
    answer covers it; a run asking about job 85 must not exit 0 over it, nor
    over another job's document."""
    with spool(**{"d00085-001": PDF, "d00085-001.bak": PDF}) as s:
        invariant("scoped report", s, "85", expect=1)
    with spool(**{"d00085-001.bak": PDF}) as s:
        invariant("bak only, report", s, "85", expect=1)
    with spool(**{"d00085-001": PDF, "d00077-001": PDF}) as s:
        invariant("other job, report", s, "85", expect=1)
    with spool(**{"d00077-001": PDF}) as s:
        invariant("other job only, report", s, "85", expect=1)


def test_tempdir_and_nested_cache() -> None:
    with spool(**{"tmp__filter.ps": PS}) as s:
        invariant("tempdir report", s, expect=1)
    with spool(**{"tmp__.cache__leak.ps": PS}) as s:
        invariant("cache report", s, expect=1)
    # A rasterised page under .cache/, where the name rule exempts anything
    # its content does not identify.
    with spool(**{"tmp__.cache__page": b"RaS2" + b"PwgRaster\x00" * 8}) as s:
        invariant("cache raster report", s, expect=1)


def test_an_unreadable_file_is_unexamined() -> None:
    """Exit 2, not 1: the tool could not look. As root, chmod 000 does not stop
    a read, and the fixture would test a readable file instead, so it is skipped."""
    if getattr(os, "geteuid", lambda: 1)() == 0:
        print("  note: running as root, unreadable-file fixture skipped")
        return
    with spool(**{"tmp__secret.ps": b"%!PS\n"}) as s:
        (s / "tmp" / "secret.ps").chmod(0o000)
        invariant("unreadable report", s, expect=2)
        (s / "tmp" / "secret.ps").chmod(0o644)


def test_unrecognised_is_not_ruled_out() -> None:
    """At the top level and in tmp/: a file that cannot be identified keeps exit off 0."""
    with spool(**{"README-do-not-delete": b"do not delete me\n",
                  "tmp__NOTES-do-not-delete": b"nor me\n",
                  "tmp__genuine.ps": PS}) as s:
        invariant("unrecognised is not ruled out", s, expect=1)


def test_a_symlink_named_like_a_document() -> None:
    """Not followed and not examined. The target lives OUTSIDE the spool, so the
    exit 2 can only come from the symlink, not from an unexamined subdirectory."""
    with spool() as s:
        vault = s.parent / "vault"
        vault.mkdir()
        (vault / "real.ps").write_bytes(b"%!PS\n")
        os.symlink(vault / "real.ps", s / "d00085-001")
        invariant("symlink is not followed", s, expect=2)
        _still("symlink", vault / "real.ps")


def test_both_kinds_and_control_files() -> None:
    with spool(**{"d00085-001": PDF, "tmp__f.ps": b"%!PS\n"}) as s:
        invariant("full report", s, expect=1)
    with spool(c00085=b"job-name gpg-key\n") as s:
        invariant("control only", s, expect=0)
        # --include-control treats a job title as disclosure, so it counts,
        # exit code included: a flag that changed what counts and not the
        # status would be advice the caller cannot act on.
        invariant("control counted", s, "--include-control", expect=1)


# --- purge runs --------------------------------------------------------------

def test_purge_removes_the_residue_and_only_the_residue() -> None:
    with spool(**{"tmp__leak.ps": PS, "tmp__ppd": b'*PPD-Adobe: "4.3"\n',
                  "tmp__cups-dbus-notifier-lockfile": b""}) as s:
        invariant("purge removes the residue and only the residue", s, "--purge",
                  expect=0, removed=("tmp/leak.ps",))


def test_purge_leaves_job_files_and_the_unidentified() -> None:
    """Job files are CUPS' own (`cancel`); the unidentified is never destroyed."""
    with spool(**{"d00085-001": PDF, "README-do-not-delete": b"do not delete me\n",
                  "tmp__leak.ps": b"%!PS\n"}) as s:
        invariant("purge leaves job files and the unidentified", s, "--purge",
                  expect=1, removed=("tmp/leak.ps",))
    with spool(**{"README-do-not-delete": b"do not delete me\n"}) as s:
        invariant("purge never removes the unidentified", s, "--purge", expect=1)


def test_purge_removes_a_content_proven_stray() -> None:
    """A copy at the top level with no job id is unreachable by cancel: residue."""
    with spool(**{"d00085-001.bak": PDF}) as s:
        invariant("purge removes a content-proven stray", s, "--purge",
                  expect=0, removed=("d00085-001.bak",))


def test_purge_does_not_follow_a_symlinked_tempdir() -> None:
    """Nothing beyond the link can become a victim. With residue beside it, what
    was seen is removed and the unexamined link keeps the verdict at 2."""
    with spool() as s:
        outside = s.parent / "outside"
        outside.mkdir()
        (outside / "out.ps").write_bytes(b"%!PS\nOUT\n")
        (s / "tmp").rmdir()
        os.symlink(outside, s / "tmp")
        invariant("purge does not follow a symlinked TempDir", s, "--purge", expect=2)
        (s / "d00085-001.bak").write_bytes(PDF)
        invariant("purge removes what it saw, stays INCOMPLETE", s, "--purge",
                  expect=2, removed=("d00085-001.bak",))
        _still("purge-escape", outside / "out.ps", b"%!PS\nOUT\n")


def test_purge_does_not_unlink_a_symlink() -> None:
    """Removing the link would print 'removed' over content that survives."""
    with spool() as s:
        vault = s.parent / "vault"
        vault.mkdir()
        (vault / "real.ps").write_bytes(b"%!PS\n")
        os.symlink(vault / "real.ps", s / "leak.ps")
        invariant("purge does not unlink a symlink", s, "--purge", expect=2)
        _still("purge-symlink", vault / "real.ps")


def test_purge_under_cache_is_evidence_driven() -> None:
    """Content decides, location does not: the fontconfig cache stays, the document goes."""
    with spool(**{"tmp__.cache__fc.cache-9": b"\x02\x00bin",
                  "tmp__.cache__planted.ps": PS}) as s:
        invariant("purge under .cache is evidence-driven", s, "--purge",
                  expect=0, removed=("tmp/.cache/planted.ps",))


def test_purge_takes_raster_and_not_text_that_looks_like_it() -> None:
    """A raster page goes; text whose first word starts with a sync word does
    not, under .cache/ or in tmp/ itself."""
    with spool(**{"tmp__.cache__page": b"3SaR" + b"\x00" * 64 + b"\x01bitmap",
                  "tmp__.cache__list.txt": b"RaStafarian playlist\n"}) as s:
        invariant("purge raster, keep text", s, "--purge",
                  expect=0, removed=("tmp/.cache/page",))
    with spool(**{"tmp__notes.txt": b"UNIRONED notes, plain text\n"}) as s:
        invariant("UNIR text is not purged", s, "--purge", expect=1)


def test_purge_handles_a_hostile_name() -> None:
    """The report escapes the name; the unlink must not, and neither may derail the run."""
    with spool() as s:
        (s / "evil\nd00099-001").write_bytes(b"%!PS\n")
        invariant("purge handles a hostile name", s, "--purge",
                  expect=0, removed=("evil\nd00099-001",))


def test_purge_ignores_control_files() -> None:
    """Counting control files changes the exit code, never the delete set."""
    with spool(c00085=b"job-name gpg-key\n") as s:
        invariant("purge ignores control files", s, "--purge", expect=0)
        invariant("counting control files widens no delete set", s,
                  "--include-control", "--purge", expect=1)


def test_purge_of_a_hard_linked_file_never_reads_as_success() -> None:
    """The spool's entry is unlinked, but the content survives at the other link.

    The second link lives OUTSIDE the spool: inside, it would itself be
    content-proven and enter the delete set.
    """
    secret = b"%!PS-Adobe-3.0\nSECRET\n"
    with spool(**{"tmp__leak.ps": secret}) as s:
        vault = s.parent / "hardlink-vault"
        vault.mkdir()
        os.link(s / "tmp" / "leak.ps", vault / "other-link.ps")
        invariant("purge of a hard-linked file never reads as success", s, "--purge",
                  expect=1, removed=("tmp/leak.ps",))
        _still("purge-hardlink", vault / "other-link.ps", secret)


if __name__ == "__main__":
    sys.exit(run(globals(), "exit-0 and no-write invariant"))
