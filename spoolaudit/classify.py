"""Classifying spool files: document, control, CUPS artifact, or unknown."""

from __future__ import annotations

import re

from spoolaudit.model import (
    ARTIFACT,
    CONTROL,
    DOCUMENT,
    DOCUMENT_MAGIC,
    Audit,
    Entry,
    Kind,
    Listing,
    TempFile,
    TopDecision,
    TopEntry,
    Verdict,
)


def temp_child_note(
    label: str,
    name: str,
    *,
    is_symlink: bool,
    is_dir: bool,
    is_regular: bool,
    target: str = "?",
) -> str | None:
    """Why this TempDir entry was not examined, or None if it is a plain file.

    Pure, so it is tested without a filesystem. Anything that is not a regular
    file gets a note, so it appears in the report: a FIFO or a broken symlink
    must not vanish, and a symlink to a document must not be counted, unlinked
    and reported as destroyed while the target stays readable.

    Order matters: is_dir() and is_file() both FOLLOW symlinks, so symlinks
    must be tested first.
    """
    if is_symlink:
        return (
            f"{safe_name(label)}/{safe_name(name)} -> {safe_name(target)} (symlink, NOT followed; "
            "not examined, and deleting the link would not remove the target)"
        )
    if is_dir:
        # Callers recurse into real directories. This branch remains for a
        # directory that cannot be read, and for callers that do not descend.
        return f"{safe_name(label)}/{safe_name(name)}/ (subdirectory, not examined)"
    if not is_regular:
        return f"{safe_name(label)}/{safe_name(name)} (not a regular file, not examined)"
    return None


def identified_as_print(f: TempFile) -> bool:
    """Does this file's CONTENT say it is print data?

    The positive question, kept separate from is_harmless_temp's negative one.
    "Not recognised as harmless" is not evidence of anything, and must never
    put a file in a delete set.
    """
    return any(f.head.startswith(m) for m in DOCUMENT_MAGIC)


def is_harmless_temp(f: TempFile, in_tempdir: bool = True) -> bool:
    """Is this TempDir file known NOT to be document content?

    TempDir is not one kind of thing. After a plain CUPS restart with nothing
    printed it can hold:

        0777f6a7dc4fa  10917 bytes  PPD file, version "4.3"
        0777f6a8bdd78  10068 bytes  PPD file, version "4.3"
        cups-dbus-notifier-lockfile  0 bytes  empty

    Those are the driver cache cupsd regenerates on startup, not printed
    documents. Treating everything here as leaked content would report a user's
    own driver cache back to them as a secret, and --purge would delete it.

    Only recognised-harmless things return True. An unreadable header is
    "unknown", which stays classified as possible document content: the safe
    direction is to over-report, not to dismiss.
    """
    # Checked first, so nothing below can excuse an actual document. Calls
    # the positive question rather than repeating its body, so the two cannot
    # drift apart.
    if identified_as_print(f):
        return False
    # Content that could NOT be read. Every rule below reasons from the first
    # bytes or the size, so a file whose header is unavailable must not be
    # excused by where it sits: an unreadable file under tmp/.cache/ is not
    # known to be a harmless cache.
    if not f.head and f.size > 0:
        return False
    # CUPS runtime names, and only inside TempDir. CUPS never creates cups-*
    # files at the top level of the spool, so honouring the name there would
    # dismiss readable content on its name alone, the one thing this tool
    # must not do. That is also why this sits AFTER the unreadable guard.
    if in_tempdir and ARTIFACT.match(f.name.rsplit("/", 1)[-1]):
        return True
    # CUPS runs filters with HOME pointed at TempDir, so a filter's XDG cache
    # lands in tmp/.cache/: with a real filter chain, dozens of fontconfig
    # cache files. They are caches by definition of where they are, and
    # reporting them as possible document content is noise that trains you to
    # skim the report, which is the failure this tool exists to avoid.
    if in_tempdir and (f.name.startswith(".cache/") or "/.cache/" in f.name):
        return True
    if f.size == 0:
        return True
    return f.head.startswith("*PPD-Adobe")


def parse_entry(name: str, include_control: bool) -> Entry | None:
    """Classify a top-level spool filename."""
    m = DOCUMENT.match(name)
    if m is not None:
        return Entry(name=name, kind=Kind.DOCUMENT, job=int(m.group(1)))
    c = CONTROL.match(name)
    if c is not None and include_control:
        return Entry(name=name, kind=Kind.CONTROL, job=int(c.group(1)))
    return None


def _evidence_kind(f: TempFile) -> Kind:
    """TEMP when the file's own bytes say print data, else UNRECOGNISED.

    Evidence decides, not location: Kind drives the delete set, so a file
    merely not ruled out must never be TEMP, wherever it sits.
    """
    return Kind.TEMP if identified_as_print(f) else Kind.UNRECOGNISED


def _by_name(f: TempFile) -> str:
    return f.name


def _retained(listing: Listing, include_control: bool) -> list[Entry]:
    """Every entry that may be document content: job files, then the rest by evidence.

    Unrecognised top-level files go through exactly the same content check as
    TempDir files, so a PostScript .bak is counted and an empty stray is not.
    TempDir files carry no recoverable job id, so they can never be targeted
    by job number; they still count as retained data.
    """
    entries = [e for e in (parse_entry(n, include_control) for n in listing.top) if e is not None]
    entries += [Entry(name=f.name, kind=_evidence_kind(f))
                for f in sorted(listing.extra, key=_by_name)
                if not is_harmless_temp(f, in_tempdir=False)]
    entries += [Entry(name=f"{listing.temp_label}/{f.name}", kind=_evidence_kind(f), in_temp=True)
                for f in sorted(listing.temp, key=_by_name)
                if not is_harmless_temp(f)]
    return entries


def _artifacts(listing: Listing) -> tuple[Entry, ...]:
    """Files known not to be content: TempDir's first, then the top level's."""
    in_temp = [Entry(name=f"{listing.temp_label}/{f.name}", kind=Kind.ARTIFACT, in_temp=True)
               for f in sorted(listing.temp, key=_by_name) if is_harmless_temp(f)]
    top = [Entry(name=f.name, kind=Kind.ARTIFACT)
           for f in sorted(listing.extra, key=_by_name) if is_harmless_temp(f, in_tempdir=False)]
    return tuple(in_temp + top)


def classify(
    listing: Listing,
    jobs: frozenset[int] = frozenset(),
    include_control: bool = False,
) -> Audit:
    """Partition a spool listing into targeted and other retained files."""
    if listing.verdict in (Verdict.DENIED, Verdict.MISSING, Verdict.NOT_A_DIRECTORY):
        # Carry unexamined through: it names the error class, so the report
        # does not tell the user to re-run with sudo over an ELOOP or ESTALE,
        # which would fail identically.
        return Audit(
            listing.verdict, (), (),
            asked_for_jobs=bool(jobs), unexamined=listing.unexamined,
        )
    entries = sorted(_retained(listing, include_control), key=lambda e: e.name)
    uncounted_control = 0 if include_control else sum(1 for n in listing.top if CONTROL.match(n))
    return Audit(
        Verdict.RETAINED if entries else Verdict.CLEAN,
        tuple(e for e in entries if e.job in jobs),
        tuple(e for e in entries if e.job not in jobs),
        asked_for_jobs=bool(jobs),
        artifacts=_artifacts(listing),
        unexamined=listing.unexamined,
        uncounted_control=uncounted_control,
    )


def purgeable(audit: Audit) -> tuple[Entry, ...]:
    """What --purge removes: files proven to be print data by their own bytes.

    The whole scope decision is one Kind test, because the classification
    already answers it. Kind.TEMP means "content-identified, carrying no job
    id that classify() looked for" -- precisely the set `cancel` cannot
    reach. Note the qualifier: inside TempDir the walk classifies by content
    alone and never applies parse_entry, so tmp/d00086-001 holding %PDF is
    TEMP and is purged. That is correct -- CUPS does not put d-files in
    TempDir, and a real job file at the top level parses as DOCUMENT first,
    so `cancel`'s own files are never in this set. DOCUMENT and CONTROL
    carry job ids and are CUPS' own to remove; UNRECOGNISED merely failed to
    be recognised as harmless, which is grounds to report and never grounds
    to destroy; ARTIFACT is not content at all. There are no scope flags to
    combine with, because there is nothing left to decide.

    Both partitions are scanned so the selection cannot depend on how the
    audit was scoped -- although in practice a purge run has no targeted
    entries, since --purge refuses job ids.
    """
    return tuple(e for e in (*audit.targeted, *audit.others) if e.kind is Kind.TEMP)


def safe_name(name: str) -> str:
    """A filename that cannot forge a report line.

    Names are interpolated into the report, so a file called
    "d00085-001\\nVERDICT: spool is clean." would print a forged pass inside
    the listing. --spool is documented for auditing directories this tool
    does not control.
    """
    out = name.replace("\\", "\\\\")
    # Every unprintable character, ESC included, not just \n and \r:
    # "\x1b[1A\x1b[2K" moves the cursor up and erases the line, so a filename
    # could overwrite the verdict it appears under.
    out = "".join(ch if ch.isprintable() or ch == " " else repr(ch)[1:-1] for ch in out)
    # And the word itself, in any case. The listing prints before the verdict,
    # so a name merely containing "VERDICT: spool is clean." would be the first
    # line `grep VERDICT | head -1` returns. Its V is written as \x56 (or \x76),
    # the same escape as above, so the name stays readable and exact.
    return _VERDICT_V.sub(lambda m: f"\\x{ord(m.group()):02x}", out)


_VERDICT_V = re.compile(r"v(?=erdict)", re.IGNORECASE)


def classify_top(e: TopEntry) -> TopDecision:
    """Which bucket a top-level spool entry belongs in.

    One function for every top-level name, job-shaped or not, so the
    never-follow-symlinks rule cannot hold at one site and not the other: a
    symlink named d00085-001 must not be unlinked and reported removed while
    the target stays readable.
    """
    named = bool(DOCUMENT.match(e.name) or CONTROL.match(e.name))
    if e.is_symlink or not e.is_regular:
        note = temp_child_note(
            ".", e.name,
            is_symlink=e.is_symlink,
            is_dir=e.is_dir,
            is_regular=False,
            target=e.target,
        )
        # `suspect` removes the name from Listing.top, which is the list
        # classify() turns into jobs. Only a name that PARSES as a job file
        # needs that: an unexamined symlink called d00085-001 must not become a
        # document. A name matching neither pattern is ignored by classify()
        # anyway, so it is left alone rather than given a second meaning here.
        return TopDecision(
            note=(note or f"{safe_name(e.name)} (not examined)").replace("./", "", 1),
            suspect=named,
        )
    if named:
        return TopDecision()
    # Read the same way TempDir files are, so a document copied to
    # d00085-001.bak is counted rather than dropped; `ls` would have shown it.
    return TopDecision(wants_content=True)
