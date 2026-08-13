#!/usr/bin/env python
"""
JP_TOOLS/tests/test_spool_audit.py
Tests for spool-audit.py, the CUPS print-spool auditor.

No pytest, no dependencies -- the toolbox has none and this should not add the
first. Every test drives the pure functions with literal listings, so no
printer, no spool and no root are needed.

THE BUGS THIS PINS DOWN
All were found by running the tool or by mutating it, never by reading it.

  1. "Could not read" reported as "clean". The shell one-liner this replaced:
         sudo ls /var/spool/cups/ | grep -E 'd0*(85|86)' || echo CLEAN
     When sudo fails, ls prints nothing, grep matches nothing, and it says
     CLEAN. Measured 2026-08-12: it printed CLEAN while sudo had not run.

  2. Successful cleanup reported as failure. The next version listed every
     retained document regardless of which jobs were asked about, so deleting
     85 and 86 looked identical to doing nothing.

  3. tmp/ never audited. CUPS TempDir defaults to /var/spool/cups/tmp and
     holds document content during filtering. v1 listed only the top level and
     discarded "tmp" as a stray file -- and its test asserted that discard,
     pinning the blind spot in place. A spool with readable document data in
     tmp/ reported "VERDICT: spool is clean."

  4. --purge ignored the job ids it accepted. Verified against a fixture:
     `85 --purge` deleted d00085-001, d00077-001 AND d00099-001. On a shared
     household printer that destroys other people's documents.

  5. A failed delete reported as files respawning. sudo rm's return code was
     discarded, so a permissions failure printed "something is recreating
     them" and sent the user hunting a nonexistent process.

  6. Exit code re-merged the two states the data model keeps apart, so
     `spool-audit.py 85 && echo SAFE` never fired even when job 85 was gone.

  7. CUPS runtime files counted as leaked content. Found on the real machine,
     not in any fixture: tmp/ held cups-dbus-notifier-lockfile alongside the
     document temporaries. It inflated every count, and --purge deleted a
     lockfile out from under a running cupsd.

  8. The verdict lied after a successful --fix. It inferred "this host RETAINS
     printed data" from files being present, so immediately after --fix turned
     retention off it still told Joe retention was on, because documents from
     before the fix were still on disk. Retention is now read from cupsd.conf
     and reported as its own line.

MUTATION TESTED
Breaking each behaviour above must make this suite fail. Three mutations
survived the first version of these tests -- "purge deletes nothing", "exit
code always 0", "others list truncated" -- all in untested main()/CLI logic.

    python tests/test_spool_audit.py
"""

import importlib.util
import os
import pathlib
import sys
import tempfile
from pathlib import Path
from typing import Any

MODULE_PATH = Path(__file__).resolve().parent.parent / "spool-audit.py"

# Load the module under test from source, every time.
#
# The bytecode cache must be removed first. spec_from_file_location reads
# __pycache__/spool-audit.cpython-*.pyc and validates it on the recorded source
# size and mtime, so bytecode compiled from a modified source is reused when
# the edit happened to be the same length. On 2026-08-12 a mutation run left
# exactly that -- `return 2` -> `return 0` -- and the suite reported a bug that
# did not exist on disk while `git diff` showed a clean tree.
#
# `sys.dont_write_bytecode` does NOT fix this. It gates only the write half.
# Measured directly: with it set, a stale .pyc was still loaded and the module
# returned 0 while the source said 2. Deleting the cache is what actually
# works, and it is verified by that same reproduction.
sys.dont_write_bytecode = True
_cache = pathlib.Path(importlib.util.cache_from_source(str(MODULE_PATH)))
if _cache.exists():
    _cache.unlink()
importlib.invalidate_caches()

_spec = importlib.util.spec_from_file_location("spool_audit", MODULE_PATH)
assert _spec is not None and _spec.loader is not None
spool_audit = importlib.util.module_from_spec(_spec)
sys.modules["spool_audit"] = spool_audit
_spec.loader.exec_module(spool_audit)

classify = spool_audit.classify
parse_entry = spool_audit.parse_entry
render = spool_audit.render
victims_for = spool_audit.victims_for
Audit = spool_audit.Audit
Listing = spool_audit.Listing
TempFile = spool_audit.TempFile
is_harmless_temp = spool_audit.is_harmless_temp
temp_child_note = spool_audit.temp_child_note
parse_find_rows = spool_audit.parse_find_rows
Verdict = spool_audit.Verdict
Kind = spool_audit.Kind

FAILURES: list[str] = []


def check(label: str, actual: object, expected: object) -> None:
    if actual != expected:
        FAILURES.append(f"{label}: expected {expected!r}, got {actual!r}")


def check_true(label: str, value: bool) -> None:
    if not value:
        FAILURES.append(f"{label}: expected True")


def spool(top: list[str], temp: list[str] | None = None) -> Any:
    """Build a readable Listing fixture. Temp files default to unrecognised."""
    return Listing(
        Verdict.CLEAN,
        tuple(top),
        tuple(TempFile(name=n, size=100, head="%!PS") for n in (temp or [])),
    )


# --- filename classification ----------------------------------------------

def test_parse() -> None:
    doc = parse_entry("d00085-001", include_control=False)
    assert doc is not None
    check("document job id", doc.job, 85)
    check("document kind", doc.kind, Kind.DOCUMENT)

    check("control ignored by default", parse_entry("c00085", include_control=False), None)
    ctl = parse_entry("c00085", include_control=True)
    assert ctl is not None
    check("control counted when asked", ctl.kind, Kind.CONTROL)

    check("stray ignored", parse_entry("some-other-file", include_control=False), None)
    check("no false match", parse_entry("d00085", include_control=False), None)

    big = parse_entry("d123456-001", include_control=False)
    assert big is not None
    check("six digit job", big.job, 123456)


# --- bug 3: tmp/ is document data ------------------------------------------

def test_tempdir_is_not_clean() -> None:
    """A spool whose only content is in tmp/ must never report clean."""
    audit = classify(spool(top=["c00085"], temp=["cups-filter-abc123"]))

    check("tmp counted", audit.total, 1)
    check("not clean", audit.verdict, Verdict.RETAINED)
    text = "\n".join(render(audit, frozenset()))
    check_true("does not claim clean", "spool is clean" not in text)
    check_true("names the temp file", "tmp/cups-filter-abc123" in text)
    check_true("explains tmp", "TempDir" in text)


def test_tempdir_cannot_be_targeted_by_job() -> None:
    """Temp files carry no job id, so a job-scoped purge must not claim them."""
    audit = classify(spool(top=["d00085-001"], temp=["cups-xyz"]), frozenset({85}))

    check("one targeted", len(audit.targeted), 1)
    check("targeted is the document", audit.targeted[0].name, "d00085-001")
    check("temp is in others", audit.others[0].kind, Kind.TEMP)


# --- bug 4: purge scope ----------------------------------------------------

def test_purge_honours_job_filter() -> None:
    """The 2026-08-12 case: '85 --purge' must not delete 77 and 99."""
    audit = classify(spool(["d00085-001", "d00077-001", "d00099-001"]), frozenset({85}))
    victims = victims_for(audit)

    check("only one victim", len(victims), 1)
    check("and it is job 85", victims[0].name, "d00085-001")


def test_purge_without_jobs_takes_everything() -> None:
    audit = classify(spool(["d00085-001", "d00077-001"], temp=["cups-abc"]))
    check("all three", len(victims_for(audit)), 3)


# --- bug 2: targeted gone vs spool clean -----------------------------------

def test_targeted_gone_but_spool_not_clean() -> None:
    audit = classify(spool(["c00085", "d00077-001", "d00082-001"]), frozenset({85, 86}))

    check_true("targeted are gone", audit.targeted_are_gone)
    check("verdict still retained", audit.verdict, Verdict.RETAINED)
    check("others counted", len(audit.others), 2)

    text = "\n".join(render(audit, frozenset({85, 86})))
    check_true("says they are gone", "Those documents are GONE" in text)
    check_true("still reports leftovers", "retained file(s) still on disk" in text)


def test_targeted_still_present() -> None:
    audit = classify(spool(["d00085-001", "d00077-001"]), frozenset({85}))
    check("one targeted", len(audit.targeted), 1)
    check_true("not reported gone", not audit.targeted_are_gone)
    text = "\n".join(render(audit, frozenset({85})))
    check_true("names it", "d00085-001" in text)
    check_true("flags it", "STILL PRESENT" in text)


# --- bug 6: exit codes -----------------------------------------------------

def test_exit_codes() -> None:
    """`spool-audit.py 85 && echo SAFE` must fire when 85 is gone."""
    gone = classify(spool(["d00077-001"]), frozenset({85}))
    check("asked about 85, it is gone -> 0", gone.exit_code, 0)

    present = classify(spool(["d00085-001"]), frozenset({85}))
    check("asked about 85, still there -> 1", present.exit_code, 1)

    dirty = classify(spool(["d00077-001"]))
    check("no jobs asked, spool dirty -> 1", dirty.exit_code, 1)

    clean = classify(spool([]))
    check("no jobs asked, spool clean -> 0", clean.exit_code, 0)

    check("denied -> 2", classify(Listing(Verdict.DENIED)).exit_code, 2)
    check("missing -> 2", classify(Listing(Verdict.MISSING)).exit_code, 2)


# --- bug 1: the three unreadable/clean states stay distinct -----------------

def test_clean_spool() -> None:
    audit = classify(spool(["c00085"]), frozenset({85}))
    check("clean", audit.verdict, Verdict.CLEAN)
    check("nothing retained", audit.total, 0)
    check_true("says clean", "spool is clean" in "\n".join(render(audit, frozenset({85}))))


def test_denied_is_not_clean() -> None:
    audit = classify(Listing(Verdict.DENIED))
    text = "\n".join(render(audit, frozenset()))

    check_true("says permission denied", "permission denied" in text)
    check_true("denies cleanliness", "NOT a clean result" in text)
    check_true("never claims clean", "spool is clean" not in text)
    check_true("not readable", not audit.readable)

    empty = classify(spool([]))
    check("empty listing is clean", empty.verdict, Verdict.CLEAN)
    check_true("denied differs from empty", audit.verdict is not empty.verdict)


def test_missing_path_is_distinct_from_denied() -> None:
    """A typo in --spool must not send the user looking for root."""
    audit = classify(Listing(Verdict.MISSING))
    text = "\n".join(render(audit, frozenset()))

    check_true("says path does not exist", "DOES NOT EXIST" in text)
    check_true("does not blame permissions", "permission denied" not in text)
    check_true("denies cleanliness", "NOT a clean result" in text)


# --- control files ---------------------------------------------------------

def test_control_files_opt_in() -> None:
    """Control files carry job titles, so they are disclosure, but opt-in."""
    default = classify(spool(["c00085"]), include_control=False)
    check("ignored by default", default.total, 0)

    opted = classify(spool(["c00085"]), include_control=True)
    check("counted when asked", opted.total, 1)
    check("as control", opted.others[0].kind, Kind.CONTROL)


# --- rendering -------------------------------------------------------------

def test_others_are_listed_not_just_counted() -> None:
    """Mutation 'others[:0]' survived the first suite: nothing read the list."""
    audit = classify(spool([f"d{n:05d}-001" for n in range(1, 6)]))
    text = "\n".join(render(audit, frozenset()))
    for n in range(1, 6):
        check_true(f"lists d{n:05d}-001", f"d{n:05d}-001" in text)


def test_long_listing_is_truncated_but_says_so() -> None:
    audit = classify(spool([f"d{n:05d}-001" for n in range(1, 31)]))
    text = "\n".join(render(audit, frozenset()))
    check_true("reports the true total", "30 retained file(s)" in text)
    check_true("admits truncation", "and 10 more" in text)


def test_ppd_files_are_not_leaks() -> None:
    """Measured on joe-Inspiron-17-7778: a CUPS restart with NOTHING printed
    left two 10KB files in tmp/ that `file` identified as PPDs. They are the
    driver cache cupsd regenerates, not printed documents, and reporting them
    as leaked content told Joe his own driver cache was a secret."""
    ppd = TempFile(name="0777f6a7dc4fa", size=10917, head="*PPD-Adobe: \"4.3\"")
    doc = TempFile(name="006816a8026a5", size=4096, head="%!PS-Adobe-3.0")
    audit = classify(Listing(Verdict.CLEAN, (), (ppd, doc)))

    check("only the document counts", audit.total, 1)
    check("the document is the one kept", audit.others[0].name, "tmp/006816a8026a5")
    check("ppd is an artifact", len(audit.artifacts), 1)
    check_true("ppd not a purge victim", "tmp/0777f6a7dc4fa" not in [e.name for e in victims_for(audit)])


def test_find_output_survives_hostile_filenames() -> None:
    """A newline in a filename split one row into two: the real name truncated,
    plus a phantom entry. --purge then deleted a path that did not exist,
    counted the FileNotFoundError as success, and the file stayed. Verified
    against real find output before the fix."""
    out = "f\t\tevil\nd00099-001\0f\t\tnormal.ps\0"
    rows = parse_find_rows(out)

    check("two entries, not three", len(rows), 2)
    check("newline stays inside the name", rows[0][2], "evil\nd00099-001")
    check("and the next file is intact", rows[1][2], "normal.ps")

    # Tabs inside a name must survive too: maxsplit=2 keeps them in the name.
    check("tab in name", parse_find_rows("f\t\ta\tb.ps\0")[0][2], "a\tb.ps")

    # Symlinks carry their target in the middle field.
    kind, target, name = parse_find_rows("l\t/etc/passwd\tsneaky\0")[0]
    check("symlink type", kind, "l")
    check("target preserved", target, "/etc/passwd")
    check("name preserved", name, "sneaky")

    check("empty output is no rows", parse_find_rows(""), [])


def test_symlinks_are_never_followed() -> None:
    """The worst failure this tool can have: a false assurance of destruction.

    Peer tested 2026-08-12 before merge. A symlink in TempDir pointing at a
    document was counted as content, --purge unlinked THE SYMLINK, and the tool
    printed "SCOPE CLEAN" and exited 0 while the target was still fully
    readable. Missing a file is bad; telling someone their secret is destroyed
    when it is not is worse.
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
    """A FIFO and a broken symlink appeared in NO section, and the tool then
    announced the spool was clean over them."""
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
    """The cardinal bug, found by review on 2026-08-12 and reproduced.

    An unreadable TempDir was silently turned into an empty one, so a spool
    holding a readable PostScript document under a mode-000 tmp/ printed
    "VERDICT: spool is clean" and exited 0. Listing had no way to say "top
    level readable, TempDir not", so this is a data-model gap, not a missing
    branch.
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
    """tmp/.cache is a real directory a filter chain creates. Depth-1 listing
    dropped it and anything inside, so a document under it appeared in no
    section and survived --purge."""
    audit = classify(Listing(Verdict.CLEAN, (), (), ("tmp/.cache/ (subdirectory, not examined)",)))
    check("exit 2", audit.exit_code, 2)
    check_true("named", ".cache" in "\n".join(render(audit, frozenset())))


def test_not_a_directory_is_its_own_outcome() -> None:
    """Pointing --spool at a file said "THAT PATH DOES NOT EXIST", a false
    statement about a file sitting right there."""
    audit = classify(Listing(Verdict.NOT_A_DIRECTORY))
    text = "\n".join(render(audit, frozenset()))

    check("exit 2", audit.exit_code, 2)
    check_true("says not a directory", "NOT A DIRECTORY" in text)
    check_true("does not claim absence", "DOES NOT EXIST" not in text)


def test_uncounted_control_files_qualify_the_clean_message() -> None:
    """An unqualified "clean" over known-unexamined disclosure is the same
    collapse of outcomes the tool exists to prevent."""
    audit = classify(spool(["c00085"]), include_control=False)

    check("not counted", audit.total, 0)
    check("but known about", audit.uncounted_control, 1)
    text = "\n".join(render(audit, frozenset()))
    check_true("mentions them", "NOT COUNTED" in text)
    check_true("explains why they matter", "job" in text and "title" in text)

    counted = classify(spool(["c00085"]), include_control=True)
    check("none uncounted when included", counted.uncounted_control, 0)


def test_zero_length_temp_is_harmless() -> None:
    """A zero-byte file cannot be document content.

    Mutation testing found the suite did not pin this: disabling the size rule
    entirely still passed. cups-dbus-notifier-lockfile is the real instance,
    0 bytes, and treating it as leaked content is what started this whole
    thread.
    """
    empty_named = TempFile(name="cups-dbus-notifier-lockfile", size=0, head="")
    empty_odd = TempFile(name="0777f6a7dc4fa", size=0, head="")

    check_true("named artifact is harmless", is_harmless_temp(empty_named))
    check_true("so is any zero-length file", is_harmless_temp(empty_odd))

    nonempty = TempFile(name="0777f6a7dc4fa", size=1, head="%")
    check_true("one byte is not zero", not is_harmless_temp(nonempty))

    audit = classify(Listing(Verdict.CLEAN, (), (empty_odd,)))
    check("not counted as content", audit.total, 0)
    check("reported as runtime", len(audit.artifacts), 1)


def test_xdg_cache_under_tempdir_is_not_content() -> None:
    """CUPS runs filters with HOME=TempDir, so filters write .cache/ there.

    Measured in a container with a real filter chain: 24 fontconfig cache
    files, reported by the recursive walk as possible document content and
    deleted by --purge. They are caches by virtue of their location.
    """
    fc = TempFile(name=".cache/fontconfig/d589a488-le64.cache-9", size=8192, head="\x02\x00")
    check_true("cache is harmless", is_harmless_temp(fc))

    nested = TempFile(name="sub/.cache/other", size=10, head="x")
    check_true("nested cache too", is_harmless_temp(nested))

    doc = TempFile(name=".cachey/leak.ps", size=10, head="%!PS")
    check_true("a lookalike directory is NOT exempt", not is_harmless_temp(doc))

    # Content beats location. A PostScript file parked under .cache/ is still
    # a document, and exempting it by path would be dismissal, which this tool
    # is not allowed to do.
    planted = TempFile(name=".cache/deep/leak.ps", size=64, head="%!PS-Adobe-3.0")
    check_true("a document under .cache is still counted", not is_harmless_temp(planted))

    pdf = TempFile(name=".cache/x", size=64, head="%PDF-1.7")
    check_true("PDF too", not is_harmless_temp(pdf))

    # The exemption is for a .cache DIRECTORY component, not any path that
    # happens to contain the string. This decoy carries no document magic, so
    # only the anchoring can save it -- an unanchored match would wrongly
    # exempt it.
    decoy = TempFile(name="job.cache-backup/blob", size=4096, head="\x00\x01binary")
    check_true("substring in a name is not a cache dir", not is_harmless_temp(decoy))

    audit = classify(Listing(Verdict.CLEAN, (), (fc, doc)))
    check("only the document counts", audit.total, 1)
    check_true("cache not purged", ".cache/fontconfig" not in "".join(e.name for e in victims_for(audit)))


def test_ppd_match_is_anchored() -> None:
    """A substring match would under-report, which is the harmful direction.

    Mutation testing found the suite accepted `"PPD" in head` in place of
    `head.startswith("*PPD-Adobe")`. A printed document whose first bytes merely
    mention PPD would then be reclassified as a harmless driver cache and
    dropped from the leak count.
    """
    decoy = TempFile(name="0777f6a7dc4fa", size=4096, head="%!PS-Adobe-3.0 PPD notes")
    check_true("substring is not enough", not is_harmless_temp(decoy))

    real = TempFile(name="0777f6a8bdd78", size=10917, head='*PPD-Adobe: "4.3"')
    check_true("anchored prefix is", is_harmless_temp(real))

    audit = classify(Listing(Verdict.CLEAN, (), (decoy,)))
    check("decoy counted as possible content", audit.total, 1)


def test_unknown_header_is_treated_as_content() -> None:
    """Over-report, never dismiss. The sudo listing path cannot read headers,
    so it yields size -1 and an empty head; that must NOT read as harmless."""
    unknown = TempFile(name="0777f6a7dc4fa", size=-1, head="")
    audit = classify(Listing(Verdict.CLEAN, (), (unknown,)))

    check_true("not harmless", not is_harmless_temp(unknown))
    check("counted as possible content", audit.total, 1)
    check("no artifacts", len(audit.artifacts), 0)


def test_cups_runtime_files_are_not_leaks() -> None:
    """Found on joe-Inspiron-17-7778, not in any fixture I invented."""
    audit = classify(Listing(Verdict.CLEAN, (), (
        TempFile(name="cups-dbus-notifier-lockfile", size=0, head=""),
        TempFile(name="006816a8026a5", size=4096, head="%!PS-Adobe-3.0"),
    )))

    check("only the real temp file counts", audit.total, 1)
    check("one artifact", len(audit.artifacts), 1)
    check("artifact kind", audit.artifacts[0].kind, Kind.ARTIFACT)

    # Never deleted: removing a lockfile under a running cupsd is not cleanup.
    names = [e.name for e in victims_for(audit)]
    check_true("lockfile not a purge victim", "tmp/cups-dbus-notifier-lockfile" not in names)

    text = "\n".join(render(audit, frozenset()))
    check_true("reported separately", "CUPS RUNTIME FILES" in text)
    check_true("says never purged", "never purged" in text)


def test_artifact_only_spool_is_clean() -> None:
    """A spool holding nothing but a lockfile is clean, not retained."""
    # A real lockfile is zero bytes with no content. The generic fixture
    # stamps PostScript magic on every temp file, which would make this one
    # look like a document -- correctly, since content beats name.
    lock = TempFile(name="cups-dbus-notifier-lockfile", size=0, head="")
    audit = classify(Listing(Verdict.CLEAN, (), (lock,)))
    check("clean", audit.verdict, Verdict.CLEAN)
    check("exit 0", audit.exit_code, 0)

    # And if something with that name did contain a document, content wins.
    impostor = TempFile(name="cups-dbus-notifier-lockfile", size=99, head="%!PS-Adobe")
    check_true("content beats the name", not is_harmless_temp(impostor))


def test_retention_is_read_not_inferred() -> None:
    """The 2026-08-12 case: --fix succeeded, next report still said RETAINS."""
    audit = classify(spool(["d00079-001"]))

    off = "\n".join(render(audit, frozenset(), retention=False))
    check_true("says retention off", "RETENTION: OFF" in off)

    # No timing claim. --fix restarts CUPS, and the restart itself regenerates
    # driver caches, so files present afterwards may POSTdate the fix. Measured
    # on the Inspiron: 0 files before --fix, 2 PPDs after, nothing printed.
    check_true("makes no claim about when files appeared", "predate" not in off)
    check_true("does not claim host retains", "CUPS is keeping documents" not in off)
    check_true("still reports the leftovers", "retained file(s) still on disk" in off)

    on = "\n".join(render(audit, frozenset(), retention=True))
    check_true("says retention on", "RETENTION: ON" in on)

    unknown = "\n".join(render(audit, frozenset(), retention=None))
    check_true("admits not knowing", "RETENTION: unknown" in unknown)


def test_no_jobs_requested() -> None:
    audit = classify(spool(["d00077-001"]))
    check("nothing targeted", len(audit.targeted), 0)
    check_true("no targeted section", "JOBS YOU ASKED ABOUT" not in "\n".join(render(audit, frozenset())))


def test_walk_survives_a_file_vanishing() -> None:
    """A live spool deletes temp files continuously. Losing the whole walk to
    one racing file discarded the audit on exactly the busy machine worth
    auditing."""
    with tempfile.TemporaryDirectory() as tmp:
        root = pathlib.Path(tmp)
        for i in range(5):
            (root / f"f{i}.ps").write_text("%!PS\n")

        real_stat = pathlib.Path.stat

        def flaky(self, *a, **k):  # type: ignore[no-untyped-def]
            if self.name == "f2.ps":
                raise FileNotFoundError(2, "gone")
            return real_stat(self, *a, **k)

        pathlib.Path.stat = flaky  # type: ignore[method-assign]
        try:
            found: list = []
            unexamined: list = []
            spool_audit._walk_temp(root, "tmp", "", found, unexamined)
        finally:
            pathlib.Path.stat = real_stat  # type: ignore[method-assign]

        check("the other four survive", len(found), 4)
        check_true("the racing file is recorded, not dropped", len(unexamined) == 1)


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

        found: list = []
        unexamined: list = []
        spool_audit._walk_temp(root, "tmp", "", found, unexamined)

        check("nothing collected past the limit", len(found), 0)
        check_true("and it says why", any("deeper than" in u for u in unexamined))


def test_walk_against_a_real_filesystem() -> None:
    """The one test that touches disk, and it exists on purpose.

    Every test above drives pure functions with literal data, which is why
    read_spool -- the layer carrying `pragma: no cover` -- produced nearly
    every serious bug on this branch: an unreadable TempDir read as clean, a
    subdirectory dropped with its contents, a symlink followed and reported as
    destroyed. Those live in traversal, not in classification, so classification
    tests cannot see them. No root and no printer needed.
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

        found: list = []
        unexamined: list = []
        spool_audit._walk_temp(root, "tmp", "", found, unexamined)

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


def main() -> int:
    for name, fn in sorted(globals().items()):
        if name.startswith("test_") and callable(fn):
            fn()

    if FAILURES:
        print(f"FAILED ({len(FAILURES)})")
        for f in FAILURES:
            print(f"  {f}")
        return 1
    print("all spool-audit tests passed")
    return 0


if __name__ == "__main__":
    sys.exit(main())
