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

import contextlib
import importlib.util
import errno
import io
import os
import pathlib
import signal
import stat
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
Audit = spool_audit.Audit
Listing = spool_audit.Listing
TempFile = spool_audit.TempFile
is_harmless_temp = spool_audit.is_harmless_temp
temp_child_note = spool_audit.temp_child_note
parse_retention = spool_audit.parse_retention
read_spool = spool_audit.read_spool
classify_top = spool_audit.classify_top
TopEntry = spool_audit.TopEntry
Entry = spool_audit.Entry
Verdict = spool_audit.Verdict
Kind = spool_audit.Kind
TEMP_SUBDIR = spool_audit.TEMP_SUBDIR

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
    """0 means NOTHING is left, not "the jobs you named are gone".

    The scoped meaning was the bug. Job identity is not content identity: a
    copy of job 85's document named d00085-001.bak carries no job id, so a
    scoped run said "those documents are GONE" and exited 0 over a readable
    copy. `85 --purge && echo SAFE` fired in four separate review rounds, in a
    different branch each time, because the scoped code kept being re-derived.
    The scoped answer still appears in the report text.
    """
    others_remain = classify(spool(["d00077-001"]), frozenset({85}))
    check("85 gone but the spool is not empty -> 1", others_remain.exit_code, 1)
    check_true("and the report still answers the question",
               "GONE" in "\n".join(render(others_remain, frozenset({85}))))

    truly_clean = classify(spool([]), frozenset({85}))
    check("nothing anywhere -> 0", truly_clean.exit_code, 0)

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
    check_true("ppd is not reported as content",
               "tmp/0777f6a7dc4fa" not in [e.name for e in audit.others])





def test_retention_honours_the_last_directive() -> None:
    """cupsd uses the LAST PreserveJobFiles line; the code returned on the first.

    A hand-edited config with No followed by Yes reported RETENTION: OFF on a
    host that was retaining documents. Danger reported as safety.

    The previous version of this test reimplemented the regex loop inline and
    asserted on its own copy -- it never called retention_state, so reverting
    that function to first-match left it green. A test that reimplements the
    thing it is testing tests the reimplementation.
    """
    with tempfile.NamedTemporaryFile("w", suffix=".conf", delete=False) as fh:
        fh.write("PreserveJobFiles No\nSomethingElse 1\nPreserveJobFiles Yes\n")
        last_wins = fh.name
    with tempfile.NamedTemporaryFile("w", suffix=".conf", delete=False) as fh:
        fh.write("PreserveJobFiles Yes\nPreserveJobFiles No\n")
        last_off = fh.name
    with tempfile.NamedTemporaryFile("w", suffix=".conf", delete=False) as fh:
        fh.write("# nothing relevant here\n")
        unset = fh.name
    try:
        check("No then Yes -> retaining", spool_audit.retention_state(last_wins), True)
        check("Yes then No -> not retaining", spool_audit.retention_state(last_off), False)
        # An absent directive means the compiled default, which on the hosts
        # measured here keeps documents. Guessing "off" would be a guess in the
        # dangerous direction.
        check("unset -> assume retaining", spool_audit.retention_state(unset), True)
        check("unreadable -> unknown", spool_audit.retention_state("/nonexistent/x.conf"), None)
    finally:
        for f in (last_wins, last_off, unset):
            os.unlink(f)








def test_runtime_names_are_only_honoured_inside_tempdir() -> None:
    """A cups-* name at the TOP LEVEL must not excuse a file unread.

    CUPS never creates cups-* files at the top level of the spool, so honouring
    the name there let `cups-dbus-secret` holding a password be dismissed:
    "VERDICT: spool is clean", exit 0, while the identical content named
    notes.txt exited 1. Dismissal on a name alone.
    """
    lock = TempFile(name="cups-dbus-notifier-lockfile", size=40, head="binary")
    check_true("honoured inside TempDir", is_harmless_temp(lock, in_tempdir=True))
    check_true("NOT honoured at the top level", not is_harmless_temp(lock, in_tempdir=False))

    # And the name must not outrank the unreadable-content guard.
    unread = TempFile(name="cups-filter-socket", size=99, head="")
    check_true("an unreadable cups- file is not excused",
               not is_harmless_temp(unread, in_tempdir=True))

    cache = TempFile(name=".cache/x", size=10, head="bin")
    check_true("the .cache rule is TempDir-only too",
               is_harmless_temp(cache, in_tempdir=True)
               and not is_harmless_temp(cache, in_tempdir=False))


def test_a_cups_prefixed_name_does_not_excuse_readable_content() -> None:
    """The ARTIFACT pattern used to carry `|^cups-dbus-`, which matched any
    name with that prefix and dismissed it unread. tmp/cups-dbus-secret holding
    a password printed "VERDICT: spool is clean" with exit 0, while identical
    content named notes.txt exited 1. Dismissal on a name alone is the one
    thing the docstring says this tool must never do.

    The alternative was also unnecessary: the artifact actually measured on the
    real machine is cups-dbus-notifier-lockfile, which matches on `lockfile`.
    """
    real = TempFile(name="cups-dbus-notifier-lockfile", size=0, head="")
    check_true("the measured real artifact is still recognised",
               is_harmless_temp(real, in_tempdir=True))
    for name in ("cups-lpd-socket", "cups-something-notifier"):
        check_true(f"and so is {name}",
                   is_harmless_temp(TempFile(name=name, size=0, head=""), in_tempdir=True))

    secret = TempFile(name="cups-dbus-secret", size=23, head="root password: hunter2")
    check_true("but a cups-dbus- name holding readable content is NOT excused",
               not is_harmless_temp(secret, in_tempdir=True))


def test_the_listing_leads_with_what_needs_a_human() -> None:
    """The listing is capped at 20 and used to lead with ordinary job files, so
    a spool of 25 documents plus one stray and one TempDir leak named NEITHER
    of the last two anywhere. Those two are exactly the files no `cancel` will
    clear and the only ones needing a decision, and they were the two the
    operator never saw. That is "not worse than ls" failing at exactly the
    point it exists for."""
    docs = tuple(f"d{i:05d}-001" for i in range(1, 26))
    stray = TempFile(name="passwords.txt", size=7, head="secret")
    leak = TempFile(name="leak.ps", size=5, head="%!PS")
    audit = classify(Listing(Verdict.CLEAN, docs, (leak,), (), TEMP_SUBDIR, (stray,)))
    text = "\n".join(render(audit, frozenset()))

    check_true("the unidentified stray is named", "passwords.txt" in text)
    check_true("the TempDir leak is named", "tmp/leak.ps" in text)
    check_true("and the truncation says what was left out",
               "all ordinary job files" in text)


def test_report_reads_location_from_the_path() -> None:
    """Kind means EVIDENCE since the split; location comes from the path.

    Reading Kind as location inverted both explanatory blocks: a tmp/ file was
    described as top-level and a top-level copy as TempDir scratch.
    """
    top_copy = TempFile(name="d00085-001.bak", size=99, head="%!PS-Adobe-3.0")
    tmp_notes = TempFile(name="NOTES.txt", size=20, head="plain notes")
    audit = classify(Listing(Verdict.CLEAN, (), (tmp_notes,), (), TEMP_SUBDIR, (top_copy,)))
    text = "\n".join(render(audit, frozenset()))

    # The fixture is deliberately crossed: the file carrying print magic is at
    # the TOP LEVEL, and the file in TempDir is the unidentifiable one. So a
    # report that reads evidence off the location, or location off the Kind,
    # gets both blocks wrong and cannot pass by accident.
    identified = [ln for ln in text.splitlines() if "print-data signature" in ln]
    unidentified = [ln for ln in text.splitlines() if "could not be identified" in ln]
    check("one block for identified content", len(identified), 1)
    check("one block for unidentified content", len(unidentified), 1)
    check_true("the top-level .bak is stated as identified print data, not a maybe",
               "1 of these carry a print-data signature" in text)
    check_true("and is NOT placed in the TempDir it does not live in",
               "of them are in the CUPS TempDir, mid-filter." not in text)
    check_true("the tmp/ notes file is the unidentified one",
               "1 of these could not be identified at all," in text)
    check_true("and its location comes from the path",
               "1 of them are in the CUPS TempDir." in text)


def test_safe_name_defuses_terminal_escapes() -> None:
    """Escaping only \\n and \\r left the report forgeable: ESC[1A ESC[2K
    moves the cursor up and erases the line the filename appears under."""
    forged = "d00085-001\x1b[1A\x1b[2KVERDICT: spool is clean."
    out = spool_audit.safe_name(forged)
    check_true("no raw ESC survives", "\x1b" not in out)
    check_true("no raw newline survives", "\n" not in spool_audit.safe_name("a\nb"))
    check_true("ordinary names are untouched", spool_audit.safe_name("d00085-001") == "d00085-001")




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

    # A file whose content could NOT be read: _head() returns "" on any OSError,
    # so a mode-000 file has a real size and an empty header. Location must not
    # excuse it. This guard originally keyed on size == -1, a sentinel only the
    # since-removed sudo listing produced, so deleting that path silently
    # disarmed it and an unreadable file under .cache/ was called a cache and
    # the spool reported clean.
    unread = TempFile(name=".cache/deep/leak.ps", size=4096, head="")
    check_true("unreadable content under .cache is NOT harmless", not is_harmless_temp(unread))
    unread_ppd = TempFile(name="ppd", size=10917, head="")
    check_true("nor is an unreadable file named like a PPD", not is_harmless_temp(unread_ppd))
    # A named CUPS runtime file is still recognised by name alone, and a
    # genuinely empty file is still harmless.
    lock = TempFile(name="cups-dbus-notifier-lockfile", size=0, head="")
    check_true("named artifacts still recognised", is_harmless_temp(lock))
    empty = TempFile(name="whatever", size=0, head="")
    check_true("zero-length is still harmless", is_harmless_temp(empty))

    audit = classify(Listing(Verdict.CLEAN, (), (fc, doc)))
    check("only the document counts", audit.total, 1)
    check_true("cache is not reported as content",
               ".cache/fontconfig" not in "".join(e.name for e in audit.others))


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

    # Never counted as content. The tool no longer deletes anything, so the
    # stake is the report: calling a live cupsd lockfile a leak sends the
    # operator to remove a lock from under a running daemon.
    names = [e.name for e in audit.others]
    check_true("lockfile is not reported as content",
               "tmp/cups-dbus-notifier-lockfile" not in names)

    text = "\n".join(render(audit, frozenset()))
    check_true("reported separately", "CUPS RUNTIME FILES" in text)
    check_true("and says to leave them alone", "leave them alone" in text)


def test_a_cups_name_at_the_top_level_is_not_a_cups_runtime_file() -> None:
    """The name-trust rule holds in the REPORT, not only in the classifier.

    is_harmless_temp gates the ARTIFACT regex behind in_tempdir, because CUPS
    never creates cups-* files at the top level of the spool and honouring the
    name there dismissed a file called cups-dbus-secret unread. render() then
    re-derived the same judgement from the basename with no such gate, so a
    top-level cups-notifier was listed under "CUPS RUNTIME FILES" and the
    operator told to leave alone a file CUPS could not have created.

    Not a disclosure hole -- reaching audit.artifacts at the top level still
    needs size 0 or a *PPD-Adobe header -- but it is the same defect at a
    second site, which is this file's most repeated failure. Found by
    thinkpad-session reviewing d36c661 over the fleet mailbox, reproduced here
    before being believed.
    """
    top_lockfile = TempFile(name="cups-fake-lockfile", size=0, head="")
    real_lockfile = TempFile(name="cups-dbus-notifier-lockfile", size=0, head="")
    audit = classify(Listing(
        Verdict.CLEAN, (), (real_lockfile,), (), TEMP_SUBDIR, (top_lockfile,),
    ))
    text = "\n".join(render(audit, frozenset()))
    runtime_block = text.split("CUPS RUNTIME FILES")[1].split("\n\n")[0]

    check_true("the real TempDir lockfile is a runtime file",
               "tmp/cups-dbus-notifier-lockfile" in runtime_block)
    check_true("the top-level one is NOT filed as a runtime file",
               "cups-fake-lockfile" not in runtime_block)
    check_true("it is still listed, so nothing is hidden",
               "cups-fake-lockfile" in text)


def test_an_unreadable_spool_that_sudo_cannot_fix_says_so() -> None:
    """The corrected advice is rendered, not merely written.

    A non-permission failure (ELOOP, ESTALE, EIO) answered with "re-run with
    sudo" sends the operator to a command that fails identically, and the fix
    was to pass the error class through to the report. Coverage of d36c661
    showed the corrected branch never executed in any test: the guard existed
    and nothing proved it fires. Reported by thinkpad-session, who rebuilt the
    pinned gate and read the missing-statement list rather than the percentage.
    """
    audit = classify(Listing(
        Verdict.DENIED, (), (), ("could not read the spool: OSError",),
    ))
    text = "\n".join(render(audit, frozenset()))

    check_true("names the error class", "OSError" in text)
    check_true("says sudo will not help", "sudo will not help" in text)
    check_true("does not send the operator to sudo", "Re-run with sudo." not in text)
    check_true("still not a clean result", "NOT a clean result" in text)


def test_the_truncated_remainder_says_what_cancel_cannot_reach() -> None:
    """The worst reported failure on this branch, and its fix was untested.

    A spool of 25 documents plus a stray and a TempDir leak listed neither of
    the last two: the only files no `cancel` can clear were the two the
    operator never saw. Stubborn-first ordering fixed the listing, and this
    line covers what ordering cannot -- the ones past the cap of 20.
    """
    stubborn = tuple(
        TempFile(name=f"unknown-{i:02d}", size=9, head="???") for i in range(25)
    )
    audit = classify(Listing(Verdict.CLEAN, (), stubborn))
    text = "\n".join(render(audit, frozenset()))

    check("all 25 counted", audit.total, 25)
    check_true("the remainder is declared", "... and 5 more" in text)
    check_true("and says why they are stubborn",
               "carry no job id, so `cancel` cannot reach them" in text)


class _Blocked(BaseException):
    """Raised by the fifo deadline below.

    BaseException, not Exception, and deliberately NOT a subclass of OSError.
    The first version raised TimeoutError -- which IS an OSError subclass -- so
    retention_state's own `except OSError: return None` swallowed the alarm and
    turned a tool that hung forever into a tidy "retention unknown". The test
    would then have passed over the exact failure it exists to catch, using the
    error type as the disguise.
    """


def test_reading_the_config_never_blocks_on_a_fifo() -> None:
    """A hang in a security tool reads as "still checking".

    os.stat on a fifo returns; opening one waits for a writer that never
    arrives, so `--conf /path/to/fifo` would hang forever with no output. The
    guard refuses before the open. Untested until 2026-08-14 despite existing
    for exactly this.

    THE DEADLINE IS PART OF THE TEST. thinkpad-session mutation-checked the
    first version of this and it HUNG rather than failed: exit 124 under
    `timeout`. That proved the test drives a real fifo -- stronger evidence
    than a coverage line disappearing, because it shows discrimination and not
    just execution -- but the test had inherited the defect it was written to
    pin. On a GitHub runner a hang is a six-hour job timeout and someone
    reading a stuck build instead of a failed assertion.

    My first repair armed the alarm around two of the three calls and left the
    render one bare, so the mutant still hung for the full 120s. One deadline
    around the whole body, and every path out of it is loud.
    """
    if not hasattr(os, "mkfifo") or not hasattr(signal, "SIGALRM"):
        print("  note: no os.mkfifo/SIGALRM on this platform, fifo case skipped")
        return

    def _too_slow(signum: int, frame: object) -> None:
        raise _Blocked("blocked on a fifo")

    previous = signal.signal(signal.SIGALRM, _too_slow)
    signal.alarm(5)
    try:
        with tempfile.TemporaryDirectory() as raw:
            fifo = Path(raw) / "cupsd.conf"
            os.mkfifo(fifo)

            try:
                spool_audit._read_conf(str(fifo))
            except OSError as exc:
                check("refused with EINVAL", exc.errno, errno.EINVAL)
            else:
                FAILURES.append("_read_conf read a fifo instead of refusing it")

            # And the caller turns that into "unknown", not into a false "OFF".
            check("retention is unknown, not off",
                  spool_audit.retention_state(str(fifo)), None)

            audit = classify(Listing(Verdict.CLEAN))
            text = "\n".join(render(audit, frozenset(),
                                    spool_audit.retention_state(str(fifo))))
            check_true("and the report says so",
                       "RETENTION: unknown (could not read cupsd.conf)" in text)
    except _Blocked:
        FAILURES.append(
            "reading a fifo BLOCKED instead of being refused; the S_ISFIFO "
            "guard is not firing, and in the real tool this is a hang with no "
            "output rather than an error"
        )
    finally:
        signal.alarm(0)
        signal.signal(signal.SIGALRM, previous)


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
        check("the racing file is recorded", len(unexamined), 1)
        # Assert the RIGHT note. The previous version checked only counts, so it
        # passed while the race branch was unreachable: is_file() swallowed the
        # error first and the file was reported as "not a regular file", which
        # on a busy spool misdescribes every racing temp file as a device.
        check_true("reported as a race, not as a device", "vanished" in unexamined[0])


def test_nothing_is_invisible() -> None:
    """The "not worse than ls" guarantee, as a test.

    The baseline this tool replaced was `sudo ls` read by a human, and `ls`
    never hides a file. Measured 2026-08-12: the tool hid three -- a document
    copied to d00085-001.bak, an unrecognised stray file, and a subdirectory --
    while printing a verdict. Anything present must appear SOMEWHERE in the
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

        listing = spool_audit.read_spool(str(root))
        audit = classify(listing, frozenset(), include_control=True)
        report = "\n".join(render(audit, frozenset()))

        def reported(rel: str) -> bool:
            """Whole-entry match on the relative path, not a substring.

            `"d00085-001" in report` is satisfied by the report line for
            d00085-001.bak, so a regression that dropped the real document
            while keeping its backup passed this check. Entries print as an
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

        found: list = []
        unexamined: list = []
        spool_audit._walk_temp(root, "tmp", "", found, unexamined)

        check("nothing collected past the limit", len(found), 0)
        check_true("and it says why", any("deeper than" in u for u in unexamined))


def test_unreadable_tempdir_is_never_clean() -> None:
    """The cardinal regression, tested without needing to be a particular user.

    This is bug 3 above and the reason the tool exists: an unreadable TempDir
    must never read as an empty one. It was covered only by the acceptance
    suite, which makes the directory unreadable with chmod 000 -- and root
    bypasses permission bits, so that check skips itself under exactly the sudo
    invocation the tool documents. The regression was therefore uncovered in
    the only run that matters.

    Injecting the PermissionError tests the handler directly and is independent
    of uid, so it holds as root and as a normal user alike. Mutating the
    `except PermissionError` branch in read_spool to `pass` makes this fail.
    """
    with tempfile.TemporaryDirectory() as tmp:
        root = pathlib.Path(tmp)
        (root / "tmp").mkdir()
        (root / "d00085-001").write_text("%!PS\n")

        def deny(*_args: object, **_kwargs: object) -> None:
            raise PermissionError(13, "Permission denied")

        # Passed in, not monkeypatched onto the module. The first version of
        # this test swapped spool_audit._walk_temp, which worked only while
        # read_spool looked the function up as a global; giving read_spool a
        # real seam broke it immediately and correctly.
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


# --- main()'s decision logic, which was inline and untested ---------------
#
# It produced findings in EVERY review round of this branch while the pure,
# tested functions above produced almost none. That was the pattern, and these
# tests are the response to it.









def test_parse_retention_precedence() -> None:
    """The last matching directive wins, and an absent one is not "off".

    Both rules were previously reachable only through retention_state, which
    carried `pragma: no cover`, so the only thing that ever exercised them was
    an end-to-end run on a machine whose config happened to have the right
    shape. The "last wins" rule had already been got wrong once: a hand-edited
    config with No followed by Yes reported RETENTION: OFF on a host that was
    retaining documents, which is danger reported as safety.
    """
    cases = [
        (b"", True, "an absent directive means the compiled default, which keeps"),
        (b"PreserveJobFiles No\n", False, "an explicit No"),
        (b"PreserveJobFiles Yes\n", True, "an explicit Yes"),
        (b"PreserveJobFiles No\nPreserveJobFiles Yes\n", True, "the LAST wins"),
        (b"PreserveJobFiles Yes\nPreserveJobFiles No\n", False, "the last wins both ways"),
        (b"  preservejobfiles   off  \n", False, "case and leading whitespace"),
        (b"PreserveJobFiles 0\n", False, "0 counts as off"),
        (b"# PreserveJobFiles Yes\n", True, "a commented directive is not a directive"),
        (b"# d\xe9partement\nPreserveJobFiles No\n", False, "a non-UTF-8 byte elsewhere"),
    ]
    for body, want, why in cases:
        check(f"retention, {why}", parse_retention(body), want)




def test_classify_top_treats_named_and_unnamed_alike() -> None:
    """The rule that must hold at BOTH sites, now that there is only one.

    This was two near-identical blocks, and the bug they produced is the worst
    this tool can have: the never-follow-symlinks rule was applied to TempDir
    and not to the top level, so a symlink named d00085-001 was unlinked, "1
    removed" was printed, and the target stayed readable. False assurance of
    destruction.
    """
    def top(name: str, **kw: bool) -> object:
        opts = {"is_symlink": False, "is_dir": False, "is_regular": True}
        opts.update(kw)
        return classify_top(TopEntry(name=name, target="/elsewhere", **opts))  # type: ignore[arg-type]

    plain = top("d00085-001")
    check("a real document file is examined normally", (plain.note, plain.suspect), (None, False))
    check_true("and is not read as extra content", not plain.wants_content)

    link = top("d00085-001", is_symlink=True, is_regular=False)
    check_true("a symlink NAMED like a document is not examined", link.note is not None)
    check_true("and is suppressed from top, so it cannot become a job", link.suspect)

    d = top("d00085-001", is_dir=True, is_regular=False)
    check_true("a DIRECTORY named like a document is not examined", d.note is not None)
    check_true("and is suppressed too", d.suspect)

    ctl = top("c00085")
    check("a real control file is examined normally", (ctl.note, ctl.suspect), (None, False))

    stray = top("stray-file")
    check_true("an unrecognised regular file is read as possible content", stray.wants_content)
    check("and is not flagged unexamined", stray.note, None)

    odd = top("stray-file", is_symlink=True, is_regular=False)
    check_true("an unrecognised symlink is flagged, not read", odd.note is not None)
    check_true("and is not read as content", not odd.wants_content)

    fifo = top("a-pipe", is_regular=False)
    check_true("a non-regular file is flagged rather than dropped", fifo.note is not None)


def test_read_spool_maps_every_listing_failure_to_its_own_verdict() -> None:
    """Four outcomes, four verdicts. Collapsing any two is the bug this tool
    exists to prevent: the one-liner it replaced could not tell "could not
    read" from "nothing there"."""
    def raiser(exc: BaseException) -> object:
        def go(_p: object) -> list[str]:
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
    """An absent TempDir is not an unexamined one. Treating it as unexamined
    meant a spool without a tmp/ could never exit 0."""
    with tempfile.TemporaryDirectory() as tmp:
        root = pathlib.Path(tmp)
        (root / "tmp").mkdir()

        def raiser(exc: BaseException) -> object:
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
    """`_head` carried `pragma: no cover -- reason: a read`, which is three
    words that satisfy a regex rather than a justification. It is testable with
    a temp file, so it is now tested and the exemption is gone."""
    with tempfile.TemporaryDirectory() as tmp:
        root = pathlib.Path(tmp)
        f = root / "doc.ps"
        f.write_bytes(b"%!PS-Adobe-3.0\nlots more content after the prefix\n")
        check("reads only the first n bytes", spool_audit._head(f, 4), "%!PS")
        check_true("and the default reads enough to recognise a format",
                   spool_audit._head(f).startswith("%!PS-Adobe"))

        empty = root / "empty"
        empty.touch()
        check("an empty file gives an empty head", spool_audit._head(empty), "")

        # An unreadable file must arrive as "" rather than raising: callers
        # treat "" as UNKNOWN, which is_harmless_temp then declines to dismiss.
        # Raising here would abort the whole listing over one file.
        missing = root / "not-there"
        check("a missing file gives '' and does not raise",
              spool_audit._head(missing), "")

        binary = root / "raw"
        binary.write_bytes(b"\xff\xfe\x00garbage")
        check_true("undecodable bytes are replaced, not raised",
                   isinstance(spool_audit._head(binary), str))


def test_retention_state_reads_and_delegates() -> None:
    """The reader half of the retention check. parse_retention has the rule;
    this has the file handling, including the case that matters most: an
    unreadable config is None, which is not the same as "not retaining"."""
    with tempfile.TemporaryDirectory() as tmp:
        root = pathlib.Path(tmp)
        off = root / "off.conf"
        off.write_bytes(b"PreserveJobFiles No\n")
        check("an explicit No reads as not retaining",
              spool_audit.retention_state(str(off)), False)

        on = root / "on.conf"
        on.write_bytes(b"PreserveJobFiles Yes\n")
        check("an explicit Yes reads as retaining",
              spool_audit.retention_state(str(on)), True)

        # None, not False. Reporting an unreadable config as "not retaining"
        # would print RETENTION: OFF over a host that is keeping documents,
        # which is danger reported as safety.
        check("an unreadable config is None, never False",
              spool_audit.retention_state(str(root / "nope.conf")), None)

        latin1 = root / "latin1.conf"
        latin1.write_bytes(b"# Imprimante d\xe9partement\nPreserveJobFiles No\n")
        check("a non-UTF-8 byte does not break the read",
              spool_audit.retention_state(str(latin1)), False)












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
