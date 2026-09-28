#!/usr/bin/env python
"""
JP_TOOLS/tests/test_spool_audit_classify.py
spool-audit: which spool files are documents, control files, CUPS artifacts or
unknown, and what the audit's verdict and exit code are. Pure functions over
literal listings: no printer, no spool, no root.

    python tests/test_spool_audit_classify.py
"""

import sys

from _harness import check, check_true, run
from spool_audit_support import spool
from spoolaudit.classify import classify, classify_top, is_harmless_temp, parse_entry, purgeable
from spoolaudit.model import TEMP_SUBDIR, Kind, Listing, TempFile, TopDecision, TopEntry, Verdict
from spoolaudit.render import render

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


# --- tmp/ is document data -------------------------------------------------

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


# --- targeted gone vs spool clean ------------------------------------------

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


# --- exit codes ------------------------------------------------------------

def test_exit_codes() -> None:
    """0 means NOTHING is left, not "the jobs you named are gone".

    Job identity is not content identity: a copy of job 85's document named
    d00085-001.bak carries no job id, so a scoped exit code would say 0 over a
    readable copy and `85 --purge && echo SAFE` would fire. The scoped answer
    appears in the report text.
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


# --- the unreadable and clean states stay distinct -------------------------

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


def test_ppd_files_are_not_leaks() -> None:
    """A CUPS restart with NOTHING printed leaves ~10KB PPD files in tmp/. They
    are the driver cache cupsd regenerates, not printed documents, and
    reporting them as leaked content would call a user's own driver cache a
    secret."""
    ppd = TempFile(name="0777f6a7dc4fa", size=10917, head="*PPD-Adobe: \"4.3\"")
    doc = TempFile(name="006816a8026a5", size=4096, head="%!PS-Adobe-3.0")
    audit = classify(Listing(Verdict.CLEAN, (), (ppd, doc)))

    check("only the document counts", audit.total, 1)
    check("the document is the one kept", audit.others[0].name, "tmp/006816a8026a5")
    check("ppd is an artifact", len(audit.artifacts), 1)
    check_true("ppd is not reported as content",
               "tmp/0777f6a7dc4fa" not in [e.name for e in audit.others])








def test_runtime_names_are_only_honoured_inside_tempdir() -> None:
    """A cups-* name at the TOP LEVEL must not excuse a file unread.

    CUPS never creates cups-* files at the top level of the spool, so honouring
    the name there would dismiss readable content on its name alone.
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
    """A bare `cups-dbus-` prefix would match any such name and dismiss it
    unread. Dismissal on a name alone is the one thing this tool must never do.
    The real artifact, cups-dbus-notifier-lockfile, matches on `lockfile`.
    """
    real = TempFile(name="cups-dbus-notifier-lockfile", size=0, head="")
    check_true("the real artifact is still recognised",
               is_harmless_temp(real, in_tempdir=True))
    for name in ("cups-lpd-socket", "cups-something-notifier"):
        check_true(f"and so is {name}",
                   is_harmless_temp(TempFile(name=name, size=0, head=""), in_tempdir=True))

    secret = TempFile(name="cups-dbus-secret", size=23, head="private notes, plain text")
    check_true("but a cups-dbus- name holding readable content is NOT excused",
               not is_harmless_temp(secret, in_tempdir=True))


def test_zero_length_temp_is_harmless() -> None:
    """A zero-byte file cannot be document content.

    cups-dbus-notifier-lockfile is the real instance, 0 bytes. Disabling the
    size rule must fail this test.
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

    With a real filter chain that is dozens of fontconfig cache files. They
    are caches by virtue of their location, not possible document content.
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
    # excuse it.
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
    """Over-report, never dismiss: a file with no readable header (size -1, an
    empty head) must NOT read as harmless."""
    unknown = TempFile(name="0777f6a7dc4fa", size=-1, head="")
    audit = classify(Listing(Verdict.CLEAN, (), (unknown,)))

    check_true("not harmless", not is_harmless_temp(unknown))
    check("counted as possible content", audit.total, 1)
    check("no artifacts", len(audit.artifacts), 0)


def test_cups_runtime_files_are_not_leaks() -> None:
    """A lockfile beside a document temporary, as a real TempDir holds them."""
    audit = classify(Listing(Verdict.CLEAN, (), (
        TempFile(name="cups-dbus-notifier-lockfile", size=0, head=""),
        TempFile(name="006816a8026a5", size=4096, head="%!PS-Adobe-3.0"),
    )))

    check("only the real temp file counts", audit.total, 1)
    check("one artifact", len(audit.artifacts), 1)
    check("artifact kind", audit.artifacts[0].kind, Kind.ARTIFACT)

    # Never counted as content: an artifact miscounted as content would enter
    # the delete set and remove a lock from under a running cupsd. The delete
    # set is Kind-driven, so this classification IS the guard.
    names = [e.name for e in audit.others]
    check_true("lockfile is not reported as content",
               "tmp/cups-dbus-notifier-lockfile" not in names)

    text = "\n".join(render(audit, frozenset()))
    check_true("reported separately", "CUPS RUNTIME FILES" in text)
    check_true("and says to leave them alone", "leave them alone" in text)


def test_a_cups_name_at_the_top_level_is_not_a_cups_runtime_file() -> None:
    """The name-trust rule holds in the REPORT, not only in the classifier.

    is_harmless_temp gates the ARTIFACT regex behind in_tempdir, because CUPS
    never creates cups-* files at the top level of the spool. The report must
    apply the same gate: a top-level cups-notifier listed under "CUPS RUNTIME
    FILES" would tell the operator to leave alone a file CUPS could not have
    created.
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


def test_no_jobs_requested() -> None:
    audit = classify(spool(["d00077-001"]))
    check("nothing targeted", len(audit.targeted), 0)
    check_true("no targeted section", "JOBS YOU ASKED ABOUT" not in "\n".join(render(audit, frozenset())))


def test_purgeable_is_selected_by_evidence_alone() -> None:
    """The delete set is Kind.TEMP and nothing else, decided by the settled
    classification rather than re-derived: DOCUMENT and CONTROL carry job ids
    and belong to `cancel`; UNRECOGNISED is never destroyed (a README beside
    the spool is not print data); ARTIFACT is not content. Built over classify()'s real output, not hand-made entries,
    so the selection is tested against what the audit actually produces."""
    listing = Listing(
        Verdict.CLEAN,
        ("d00085-001", "c00085"),
        (
            TempFile(name="leak.ps", size=64, head="%!PS-Adobe-3.0"),
            TempFile(name="NOTES.txt", size=20, head="plain notes"),
            TempFile(name="cups-dbus-notifier-lockfile", size=0, head=""),
        ),
        (),
        TEMP_SUBDIR,
        (
            TempFile(name="d00085-001.bak", size=64, head="%PDF-1.7"),
            TempFile(name="README", size=10, head="hello"),
        ),
    )
    audit = classify(listing, frozenset(), include_control=True)
    victims = sorted(e.name for e in purgeable(audit))
    check("only content-proven, job-less files are in the delete set",
          victims, ["d00085-001.bak", "tmp/leak.ps"])

    # And a scoped audit cannot smuggle a document into the set through the
    # targeted partition: main() refuses jobs with --purge, but the selection
    # itself must not depend on that refusal.
    scoped = classify(listing, frozenset({85}), include_control=True)
    check("a scoped audit selects the same set",
          sorted(e.name for e in purgeable(scoped)), victims)


def test_kind_temp_means_no_job_id_that_classify_looked_for() -> None:
    """What purgeable()'s docstring says, pinned. Inside TempDir the walk
    classifies by content alone and never applies parse_entry, so a
    job-NAMED file in tmp/ holding print magic is TEMP and IS purged --
    CUPS does not put d-files in TempDir, so nothing real is at stake. The
    same name at the top level parses as DOCUMENT first and is `cancel`'s,
    never this tool's. So TEMP does not mean "carries no job id"."""
    in_tmp = classify(Listing(
        Verdict.CLEAN, (),
        (TempFile(name="d00086-001", size=9, head="%PDF-1.7"),),
    ))
    check("a job-named file in tmp/ is TEMP, by content alone",
          in_tmp.others[0].kind, Kind.TEMP)
    check("and IS in the delete set",
          [e.name for e in purgeable(in_tmp)], ["tmp/d00086-001"])

    at_top = classify(spool(["d00086-001"]))
    check("the same name at the top level is DOCUMENT",
          at_top.others[0].kind, Kind.DOCUMENT)
    check("and is never purgeable", purgeable(at_top), ())




def test_classify_top_treats_named_and_unnamed_alike() -> None:
    """The never-follow-symlinks rule holds at the top level as in TempDir.

    Otherwise a symlink named d00085-001 would be unlinked, "1 removed"
    printed, and the target left readable: false assurance of destruction,
    the worst failure this tool can have.
    """
    def top(name: str, is_symlink: bool = False, is_dir: bool = False,
            is_regular: bool = True) -> TopDecision:
        return classify_top(TopEntry(name=name, target="/elsewhere", is_symlink=is_symlink,
                                     is_dir=is_dir, is_regular=is_regular))

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


if __name__ == "__main__":
    sys.exit(run(globals(), "classification"))
