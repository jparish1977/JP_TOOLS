#!/usr/bin/env python3
"""Report documents left behind in the CUPS print spool, and remove the
leftovers that CUPS' own tools cannot reach.

Printing sends the whole document through CUPS, and CUPS may keep a copy after
the job finishes. If you ever print a password, a recovery sheet or a key, that
copy outlives the paper, on a machine that may not be yours.

Usage:
    python spool-audit.py                    # report on everything
    python spool-audit.py 85 86              # report, highlighting those jobs
    python spool-audit.py --include-control  # also count job control files
    python spool-audit.py --purge            # remove content-proven files
                                             # that `cancel` cannot reach
    python spool-audit.py --spool DIR        # audit a directory instead

Exit status: 0 nothing retained, 1 content still on disk (including anything
--purge failed to remove), 2 could not be read.

Reads directly, so it must run as root for the real spool (sudo). There is no
privilege-escalation path inside the tool: a second implementation of the same
listing kept disagreeing with the first, and that divergence caused a large
share of this file's history of bugs.

WHAT --purge REMOVES, AND WHY THE ANSWER IS A TYPE
    Exactly the files whose own first bytes prove them to be print data and
    which carry no job id -- the ones the report describes with "`cancel`
    cannot reach them". That is the one clearing job with no CUPS equivalent.
    Everything else is out of scope by classification, not by a check that
    could be forgotten:

      d<job>-<n>, c<job>  CUPS' own files. `cancel -a -x` removes them
                          through the daemon, correctly. See HOW TO CLEAR IT.
      unidentified files  never removed. "Not recognised as harmless" is the
                          right rule for a report and the wrong one for a
                          delete set; this tool does not destroy what it
                          could not identify.
      runtime artifacts   lockfiles, driver caches. Not content, not touched.

    So --purge takes no scope arguments. Job ids alongside it are refused --
    nothing it removes has one -- and no flag widens the set.

WHAT IT WILL NEVER DO, BY CONSTRUCTION
    The first destructive half of this file was cut entirely on 2026-08-14,
    after sixteen review rounds kept finding bugs in it: a --fix that could
    destroy a device node, a --fix that truncated cupsd.conf to zero bytes, a
    --purge that followed a symlinked TempDir out of the directory it was told
    to audit. Most of what it did -- retention off, jobs cancelled -- was
    re-implementing work CUPS already does correctly, and deleting it removed
    the bugs with it. What returned is only the residue removal above, built
    so those bugs have no code to live in rather than so they are checked for:

      - No file is ever written and no daemon is ever touched. --fix is not
        back: it was `cancel -a -x` plus one config line, and even a version
        that shells out to CUPS re-imports its worst bug -- acting on the
        LIVE daemon while pointed at some other --conf or --spool. Nothing
        here opens cupsd.conf for writing, so nothing can truncate it, and
        nothing calls os.replace, so nothing can destroy a device node.
      - Removal cannot traverse a symlink. Every path component is opened
        O_NOFOLLOW relative to the previous descriptor and the unlink is
        anchored to the last of them, so a symlink anywhere in the path is
        the kernel's ELOOP, not a path comparison that can go stale between
        the check and the unlink.
      - Only regular files are unlinked. The leaf is opened (O_NONBLOCK, so a
        fifo cannot hang the run) and fstat'd, and anything else is refused
        by type -- including a symlink, because unlinking a link while its
        target survives is a false assurance of destruction.

    The reporting half remains the part with no equivalent: answering "what
    is this file, actually" about spool contents you did not put there.

WHY THE PROBLEM EXISTS AT ALL
    CUPS documents PreserveJobFiles as defaulting to No, so document files
    should never survive a completed job. They do. Measured in a clean Ubuntu
    24.04 container on 2026-08-12, stock cupsd.conf with no PreserveJobFiles
    directive present:

        t+1s  d-files=1     t+30s  d-files=1
        t+10s d-files=1     t+60s  d-files=1

    The document sat there for the full minute. Writing "PreserveJobFiles No"
    explicitly stops it: a print afterwards left no document file at all. So an
    unset directive and an explicit No behave DIFFERENTLY, and the documented
    default cannot be relied on.

    That matches apple/cups issue #6083, open with no root cause and the
    repository archived in March 2026. The issue only reports macOS; this
    reproduces it on Linux.

HOW TO CLEAR IT
    With CUPS' own tools, which handle a running daemon correctly:

        cancel -a -x                      # cancel every job and its documents
        sudo nano /etc/cups/cupsd.conf    # add: PreserveJobFiles No
        sudo systemctl restart cups       # and stop it happening again

    The explicit "No" matters — see above, an unset directive is not the same
    thing. Re-run this tool afterwards to check the spool is actually empty,
    which is the job it is for. Whatever `cancel` could not reach -- content
    with no job id -- is what --purge is for.

WHY THIS EXISTS AS A TOOL
    The obvious one-liner is wrong in a way that reports danger as safety:

        sudo ls /var/spool/cups/ | grep -E 'd0*(85|86)' || echo CLEAN

    When sudo fails, ls prints nothing, grep matches nothing, and it announces
    CLEAN. A failed check and a clean result are indistinguishable. Every
    outcome here is therefore distinct, including "could not read" and "that
    path does not exist", and none of them collapse into a pass.

WHAT COUNTS AS A LEAK
    d<job>-<n>   top-level document files, the printed content
    tmp/*        CUPS TempDir, default /var/spool/cups/tmp. MAY hold document
                 content during filtering, but also holds PPD driver caches and
                 lockfiles that are not content at all. Recognised-harmless
                 files are reported separately; anything unrecognised is
                 treated as possible content, because over-reporting is the
                 safe direction here.
    c<job>       control files. Not document content, but they carry the job
                 title and submitting user, so a job named
                 "gpg-private-key.txt" is disclosure by itself. Counted only
                 with --include-control, because naming them in a report moves
                 that disclosure somewhere less protected than the spool.
    tmp/cups-*   CUPS runtime files (lockfiles, notifier sockets). NOT content.
                 Reported separately, never counted as a leak, never removed.
"""

from __future__ import annotations

import argparse
import contextlib
import errno
import os
import re
import stat
import sys
from collections.abc import Callable
from dataclasses import dataclass
from enum import Enum
from pathlib import Path

DEFAULT_SPOOL = "/var/spool/cups"
DEFAULT_CONF = "/etc/cups/cupsd.conf"
TEMP_SUBDIR = "tmp"

DOCUMENT = re.compile(r"^d(\d+)-(\d+)$")
CONTROL = re.compile(r"^c(\d+)$")

# CUPS keeps its own runtime files in TempDir alongside spooled document data.
# Measured on joe-Inspiron-17-7778, 2026-08-12: tmp/ held eight opaque document
# temporaries and one `cups-dbus-notifier-lockfile`. Counting the lockfile as
# leaked content inflates every total, and deleting it removes a lock from
# under a running cupsd. Named artifacts are reported separately and are never
# purged.
# The `|^cups-dbus-` alternative used to be here and dismissed ANY name with
# that prefix on the name alone: tmp/cups-dbus-secret holding a password
# printed "VERDICT: spool is clean" with exit 0, while byte-identical content
# named notes.txt exited 1. That is the one thing the docstring says this tool
# must never do, and it was redundant: the measured real artifact,
# cups-dbus-notifier-lockfile, already matches the lockfile alternative.
ARTIFACT = re.compile(r"^cups-.*(lockfile|notifier|socket)$")



# Directory levels below TempDir that are walked. Both traversals must agree:
# _walk_temp allowed one level more than `find -maxdepth`, and find exits 0
# with no marker when it truncates, so a document nested past the limit on a
# sudo path yielded "spool is clean".
MAX_TEMP_DEPTH = 8

# Print-job formats. Content beats location: a file whose first bytes say it is
# a document is counted even if it sits somewhere caches normally live.
DOCUMENT_MAGIC = ("%!PS", "%PDF", "\x1b%-12345X", "\x04%!", "@PJL", "\x1b*")


class Verdict(Enum):
    """Distinct outcomes. Collapsing any two of these is the bug this prevents."""

    DENIED = "denied"
    MISSING = "missing"
    NOT_A_DIRECTORY = "not_a_directory"
    CLEAN = "clean"
    RETAINED = "retained"


class Kind(Enum):
    DOCUMENT = "document"
    TEMP = "temp"
    CONTROL = "control"
    ARTIFACT = "artifact"
    # A top-level file matching no known pattern. Its own kind so the report
    # cannot describe it as living in TempDir, which it does not.
    UNRECOGNISED = "unrecognised"


@dataclass(frozen=True)
class Entry:
    """One file in the spool. `job` is None for temp files, which are unattributable."""

    name: str
    kind: Kind
    job: int | None = None
    # Where the file was found, recorded at the point classify() knows it
    # rather than recovered from the name later. render() used to re-derive it
    # by matching the "tmp/" prefix, which made the name a second source of
    # truth for a fact the listing already had -- and re-applied the name trust
    # that is_harmless_temp explicitly refuses at the top level.
    in_temp: bool = False


@dataclass(frozen=True)
class TempFile:
    """A file in CUPS TempDir, with just enough to classify it.

    `head` is the first few bytes, decoded lossily, used only to recognise
    known-harmless formats. It is never printed. A file whose header could not
    be read never becomes a TempFile: it is listed as unexamined (#42). An
    empty string is a file that read as empty, which is "unknown", not
    "harmless".
    """

    name: str
    size: int
    head: str = ""


@dataclass(frozen=True)
class Listing:
    """Raw spool contents, or why they could not be read."""

    verdict: Verdict
    top: tuple[str, ...] = ()
    temp: tuple[TempFile, ...] = ()
    # Things known to exist but NOT examined: an unreadable TempDir, a
    # subdirectory under it. Without this the model could not express "top
    # level readable, TempDir not", and the code silently turned an unreadable
    # TempDir into an empty one and announced "spool is clean".
    unexamined: tuple[str, ...] = ()
    # Always "tmp" now that --temp is gone. Kept as a field so classify() has
    # one place to read it from rather than hardcoding the prefix twice.
    temp_label: str = TEMP_SUBDIR
    # Top-level entries matching neither d<n>-<n> nor c<n>. They were dropped
    # silently, so a document left as d00085-001.bak was invisible while the
    # tool printed "spool is clean" -- strictly worse than the `ls` this
    # replaced. TempDir already uses over-report-never-dismiss; the top level
    # now uses the same rule. LAST field on purpose: inserting it mid-struct
    # shifted every positional construction in the tests, which is the same
    # "changed one site, not its twins" failure this branch keeps hitting.
    extra: tuple[TempFile, ...] = ()


@dataclass(frozen=True)
class Audit:
    """Classified spool contents. Pure data: no I/O, no printing."""

    verdict: Verdict
    targeted: tuple[Entry, ...]
    others: tuple[Entry, ...]
    asked_for_jobs: bool = False
    artifacts: tuple[Entry, ...] = ()
    unexamined: tuple[str, ...] = ()
    uncounted_control: int = 0

    @property
    def total(self) -> int:
        return len(self.targeted) + len(self.others)

    @property
    def readable(self) -> bool:
        return self.verdict in (Verdict.CLEAN, Verdict.RETAINED)

    @property
    def complete(self) -> bool:
        """Was everything actually examined?

        A readable spool with an unreadable TempDir is NOT a clean result, and
        conflating the two is the failure this tool exists to prevent. Nothing
        may report "clean" while this is False.
        """
        return self.readable and not self.unexamined

    @property
    def targeted_are_gone(self) -> bool:
        """True when nothing you asked about is present.

        Deliberately independent of `verdict`: the jobs you care about can be
        gone while the spool still holds other people's documents.
        """
        return not self.targeted

    @property
    def exit_code(self) -> int:
        """0 satisfied, 1 something you care about is still there, 2 unknown.

        When job ids were given, this answers the question that was asked --
        "are MY documents gone?" -- rather than "is the spool empty?". Basing
        it on the whole spool would re-merge the two states the data model
        keeps apart, and `spool-audit.py 85 && echo SAFE` would never fire.
        """
        if not self.complete:
            return 2
        # STRICT: 0 means no print data is left anywhere, not "the jobs you
        # named are gone". Job identity is not content identity -- a copy of
        # job 85's document named d00085-001.bak carries no job id, so a
        # scoped run reported "those documents are GONE" and exited 0 over a
        # readable copy. That fired `85 --purge && echo SAFE` in four separate
        # review rounds, each time in a different branch, because the scoped
        # exit code kept being re-derived and kept being wrong.
        #
        # The scoped question is still answered, in the report text. The exit
        # code answers the only one a script can act on: is anything still
        # here?
        return 0 if self.verdict is Verdict.CLEAN else 1


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

    Pure so it can be tested without a filesystem. This decision lived inside
    read_spool, which carries `pragma: no cover`, and it silently dropped
    everything that was not a regular file. A FIFO and a broken symlink
    vanished from the report entirely, and a symlink to a document was counted,
    unlinked, and reported as destroyed while the target stayed readable.

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
    "Not recognised as harmless" is not evidence of anything, and using it to
    populate a delete set destroyed files whose only crime was being
    unfamiliar.
    """
    return any(f.head.startswith(m) for m in DOCUMENT_MAGIC)


def is_harmless_temp(f: TempFile, in_tempdir: bool = True) -> bool:
    """Is this TempDir file known NOT to be document content?

    TempDir is not one kind of thing. Measured on joe-Inspiron-17-7778,
    2026-08-12, after a plain CUPS restart with nothing printed:

        0777f6a7dc4fa  10917 bytes  PPD file, version "4.3"
        0777f6a8bdd78  10068 bytes  PPD file, version "4.3"
        cups-dbus-notifier-lockfile  0 bytes  empty

    Those are the driver cache cupsd regenerates on startup, not printed
    documents. Treating everything here as leaked content reported a user's own
    driver cache back to them as a secret, and --purge would have deleted it.

    Only recognised-harmless things return True. An unreadable header is
    "unknown", which stays classified as possible document content: the safe
    direction is to over-report, not to dismiss.
    """
    # Checked first, so nothing below can excuse an actual document. Calls
    # the positive question rather than repeating its body: the two were
    # identical expressions over the same constant, and duplicated blocks
    # drifting apart is the defect this file has produced most often.
    if identified_as_print(f):
        return False
    # Content that could NOT be read. Every rule below reasons from the first
    # bytes or the size, so a file whose header is unavailable must not be
    # excused by where it sits. This originally keyed on size == -1, a sentinel
    # only the removed sudo listing produced -- so deleting that path silently
    # disarmed the guard, and an unreadable file under tmp/.cache/ was
    # classified a harmless cache and reported "spool is clean".
    if not f.head and f.size > 0:
        return False
    # CUPS runtime names, and only inside TempDir. CUPS never creates cups-*
    # files at the top level of the spool, so honouring the name there let a
    # file called cups-dbus-secret holding a password be dismissed unread:
    # "VERDICT: spool is clean", exit 0, while the identical content named
    # notes.txt exited 1. Dismissal on a name alone, which is the one thing
    # this tool must not do -- and it sat BEFORE the unreadable-content guard,
    # so an unreadable file with a cups- name was excused as well.
    if in_tempdir and ARTIFACT.match(f.name.rsplit("/", 1)[-1]):
        return True
    # CUPS runs filters with HOME pointed at TempDir, so a filter's XDG cache
    # lands in tmp/.cache/. Measured in a container with a real filter chain:
    # 24 fontconfig cache files, which the recursive walk reported as possible
    # document content and --purge deleted. They are caches by definition of
    # where they are. Twenty-four false positives is noise that trains you to
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


def classify(
    listing: Listing,
    jobs: frozenset[int] = frozenset(),
    include_control: bool = False,
) -> Audit:
    """Partition a spool listing into targeted and other retained files."""
    if listing.verdict in (Verdict.DENIED, Verdict.MISSING, Verdict.NOT_A_DIRECTORY):
        # Carry unexamined through. Dropping it here is why the ELOOP/ESTALE
        # class name never reached the report and the user was told to re-run
        # with sudo, which would fail identically.
        return Audit(
            listing.verdict, (), (),
            asked_for_jobs=bool(jobs), unexamined=listing.unexamined,
        )

    entries = [e for e in (parse_entry(n, include_control) for n in listing.top) if e is not None]
    # Unrecognised top-level files go through exactly the same content check as
    # TempDir files, so a PostScript .bak is counted and an empty stray file is
    # not.
    extras = sorted(listing.extra, key=lambda f: f.name)
    entries += [
        Entry(
            name=f.name,
            kind=Kind.TEMP if identified_as_print(f) else Kind.UNRECOGNISED,
            job=None,
        )
        for f in extras
        if not is_harmless_temp(f, in_tempdir=False)
    ]
    # Temp files carry document content but no recoverable job id, so they can
    # never be "targeted" by job number. They still count as retained data.
    temps = sorted(listing.temp, key=lambda f: f.name)
    entries += [
        Entry(
            name=f"{listing.temp_label}/{f.name}",
            in_temp=True,
            # Positively identified as print data, or merely not ruled out?
            # Kind drove the delete set, and TempDir files were all TEMP, so
            # `--purge` destroyed tmp/notes.txt while sparing an identical
            # README at the top level. Evidence decides, not location.
            kind=Kind.TEMP if identified_as_print(f) else Kind.UNRECOGNISED,
            job=None,
        )
        for f in temps
        if not is_harmless_temp(f)
    ]
    artifacts = tuple(
        [
            Entry(name=f"{listing.temp_label}/{f.name}", kind=Kind.ARTIFACT,
                  job=None, in_temp=True)
            for f in temps
            if is_harmless_temp(f)
        ]
        + [
            Entry(name=f.name, kind=Kind.ARTIFACT, job=None)
            for f in extras
            if is_harmless_temp(f, in_tempdir=False)
        ]
    )

    targeted = tuple(sorted((e for e in entries if e.job in jobs), key=lambda e: e.name))
    others = tuple(sorted((e for e in entries if e.job not in jobs), key=lambda e: e.name))
    verdict = Verdict.RETAINED if entries else Verdict.CLEAN
    uncounted_control = 0 if include_control else sum(1 for n in listing.top if CONTROL.match(n))
    return Audit(
        verdict,
        targeted,
        others,
        asked_for_jobs=bool(jobs),
        artifacts=artifacts,
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
    to destroy (the old delete set deleted README-do-not-delete on exactly
    that confusion); ARTIFACT is not content at all. There are no scope
    flags to combine with, because there is nothing left to decide.

    Both partitions are scanned so the selection cannot depend on how the
    audit was scoped -- although in practice a purge run has no targeted
    entries, since --purge refuses job ids.
    """
    return tuple(e for e in (*audit.targeted, *audit.others) if e.kind is Kind.TEMP)


def safe_name(name: str) -> str:
    """A filename that cannot forge a report line.

    Names are interpolated into the report, and a file called
    "d00085-001\nVERDICT: spool is clean." printed a forged pass inside the
    listing. Newlines and carriage returns are escaped; --spool is documented
    for auditing directories this tool does not control.
    """
    out = name.replace("\\", "\\\\")
    # ESC too, not just \n and \r: "\x1b[1A\x1b[2K" moves the cursor up and
    # erases the line, so a filename could overwrite the verdict it appears
    # under. Escaping only line breaks left the report forgeable on any real
    # terminal.
    return "".join(ch if ch.isprintable() or ch == " " else repr(ch)[1:-1] for ch in out)


def render(
    audit: Audit,
    jobs: frozenset[int],
    retention: bool | None = None,
    purge_failed: frozenset[str] = frozenset(),
    undestroyed: int = 0,
) -> list[str]:
    """Format an Audit for a human. Returns lines; printing is the caller's job.

    `retention` is whether CUPS is currently configured to keep documents, read
    from cupsd.conf. It is deliberately a separate input: inferring it from
    "are there files here" told Joe the host still retained data immediately
    after a successful --fix had turned retention off, because leftover
    documents from before the fix were still on disk.

    `purge_failed` names the entries a purge THIS RUN already failed to
    remove. The identified paragraph and the VERDICT were written for a
    pre-purge spool, and purge_outcome feeds this same function a post-purge
    one: without this input the capability sentence was printed four lines
    under proof that it did not hold, and the VERDICT advised --purge for an
    entry --purge had just failed on. It is an input to the one renderer
    rather than a second renderer, which is the drift purge_outcome exists
    to prevent.
    """
    if audit.verdict is Verdict.DENIED:
        # A non-permission error (ELOOP, ESTALE, EIO) must not be answered with
        # "re-run with sudo", which would fail identically. read_spool passes
        # the class name through unexamined when it knows it.
        detail = [u for u in audit.unexamined if u.startswith("could not read the spool")]
        if detail:
            return [
                f"COULD NOT READ THE SPOOL ({detail[0].split(': ', 1)[1]}).",
                "Nothing is proven either way. This is NOT a clean result.",
                "This is not a permissions problem; sudo will not help.",
            ]
        return [
            "COULD NOT READ THE SPOOL (permission denied).",
            "Nothing is proven either way. This is NOT a clean result.",
            "Re-run with sudo.",
        ]
    if audit.verdict is Verdict.NOT_A_DIRECTORY:
        return [
            "THAT PATH IS NOT A DIRECTORY.",
            "Nothing is proven either way. This is NOT a clean result.",
            "It exists; --spool needs the spool directory, not a file inside it.",
        ]
    if audit.verdict is Verdict.MISSING:
        return [
            "THAT PATH DOES NOT EXIST.",
            "Nothing is proven either way. This is NOT a clean result.",
            "Check --spool; this is a wrong path, not a permissions problem.",
        ]

    lines = [f"Spool holds {audit.total} retained file(s).", ""]

    if jobs:
        wanted = ", ".join(str(j) for j in sorted(jobs))
        lines.append(f"JOBS YOU ASKED ABOUT ({wanted}):")
        if audit.targeted_are_gone:
            lines.append("  none present. Those documents are GONE.")
        else:
            lines += [f"  >>> {safe_name(e.name)}  STILL PRESENT" for e in audit.targeted]
            lines.append("  clear these with: cancel -x " + " ".join(str(j) for j in sorted(jobs)))
        lines.append("")

    # Kind means EVIDENCE since the classification was split; location comes
    # from the path. Reading Kind as location inverted both explanatory blocks:
    # a tmp/ file was described as top-level and a top-level copy as TempDir
    # scratch, sending the operator to the wrong directory.
    # Split by EVIDENCE, because that is what Kind means. Merging TEMP with
    # UNRECOGNISED described a file whose first bytes say %!PS as "unrecognised,
    # may be document content", which understates a CONFIRMED leak as a maybe,
    # and undoes the split that was introduced to make Kind mean evidence.
    identified = [e for e in audit.others if e.kind is Kind.TEMP]
    unidentified = [e for e in audit.others if e.kind is Kind.UNRECOGNISED]
    rest = [e for e in audit.others if e.kind not in (Kind.TEMP, Kind.UNRECOGNISED)]

    lines.append(f"OTHER RETAINED FILES: {len(audit.others)}")
    # Stubborn first. The listing is capped at 20 and used to lead with the
    # ordinary job files, so a spool with 25 documents plus one stray named
    # passwords.txt and one TempDir leak printed neither of the last two
    # ANYWHERE: the only two files --purge would not remove were the two the
    # operator never saw. That is the "not worse than ls" promise failing at
    # precisely the point it exists for.
    ordered = unidentified + identified + rest
    # The delete set is marked per entry, not only counted. "N of these" above
    # an unmarked mixed listing left the operator unable to determine WHICH
    # files "--purge removes exactly these files" meant, short of running the
    # purge and reading what it destroyed. An entry the purge just failed on
    # is not marked: the mark is the same per-entry promise the capability
    # sentence makes, and it has just been disproven for that entry.
    lines += [
        f"  {safe_name(e.name)}"
        + ("  <- --purge removes this"
           if e.kind is Kind.TEMP and e.name not in purge_failed else "")
        for e in ordered[:20]
    ]
    hidden = ordered[20:]
    if hidden:
        # Say what was hidden, not just how much. With the order above the
        # remainder is normally all ordinary job files, but say so only when
        # it is true.
        stubborn = sum(1 for e in hidden
                       if e.kind in (Kind.TEMP, Kind.UNRECOGNISED))
        if stubborn:
            lines.append(f"  ... and {len(hidden)} more, {stubborn} of which "
                         "carry no job id, so `cancel` cannot reach them")
        else:
            lines.append(f"  ... and {len(hidden)} more, all ordinary job files "
                         "that `cancel -a -x` clears")
    if identified:
        in_temp = sum(1 for e in identified if e.in_temp)
        lines.append("")
        lines.append(
            f"  {len(identified)} of these carry a print-data signature"
        )
        lines.append("  (PostScript, PDF or PJL), so they ARE document content by")
        lines.append("  evidence, not a maybe. They carry no job id, so they cannot be")
        lines.append("  targeted by job number, so `cancel` cannot reach them.")
        # The capability sentence asserts a future the caller may have already
        # spent: on a post-purge render with failures it sat four lines under
        # "delete FAILED". A run with failures does not get to promise.
        if not purge_failed:
            lines.append("  --purge removes exactly these files and nothing else.")
        if in_temp:
            lines.append(f"  {in_temp} of them are in the CUPS TempDir, mid-filter.")
    if unidentified:
        in_temp = sum(1 for e in unidentified if e.in_temp)
        lines.append("")
        lines.append(
            f"  {len(unidentified)} of these could not be identified at all,"
        )
        lines.append("  so they are not ruled out. Look at them before you")
        lines.append("  decide anything; this tool will not decide for you.")
        if in_temp:
            lines.append(f"  {in_temp} of them are in the CUPS TempDir.")

    if audit.artifacts:
        # Split by what they actually are. Filing a stray zero-length file
        # under "CUPS RUNTIME FILES" trains the operator to skim the one
        # section a real stray could hide in -- but dropping it from the report
        # instead would make it invisible, which is worse.
        # `e.in_temp and`, not the name alone. CUPS never creates cups-* files
        # at the top level of the spool -- is_harmless_temp says so and gates
        # the same regex on the same fact -- so a top-level cups-notifier was
        # filed under "CUPS RUNTIME FILES" and the operator told to leave alone
        # a file that, by this tool's own reasoning, CUPS cannot have created.
        # Not a disclosure: reaching audit.artifacts at the top level still
        # requires size 0 or a *PPD-Adobe header, verified against a fixture of
        # both. It is the same name-trust defect as the classifier's, at the
        # second site, which is this file's most repeated failure.
        runtime = [e for e in audit.artifacts
                   if e.in_temp and ARTIFACT.match(e.name.rsplit("/", 1)[-1])]
        other = [e for e in audit.artifacts if e not in runtime]
        if runtime:
            lines.append("")
            lines.append(f"CUPS RUNTIME FILES (not document content, leave them alone): {len(runtime)}")
            lines += [f"  {safe_name(e.name)}" for e in runtime]
        if other:
            lines.append("")
            lines.append(f"OTHER FILES, no print-data signature: {len(other)}")
            lines += [f"  {safe_name(e.name)}" for e in other]
            lines.append("  Empty, or a recognised driver cache. Listed so nothing is hidden.")

    if audit.unexamined:
        lines.append("")
        lines.append(f"COULD NOT EXAMINE: {len(audit.unexamined)}")
        lines += [f"  {u}" for u in audit.unexamined]
        lines.append("  Anything in there is unaccounted for. This is NOT a clean result.")

    if audit.uncounted_control:
        lines.append("")
        lines.append(
            f"NOT COUNTED: {audit.uncounted_control} control file(s). They carry the job"
        )
        lines.append("  title and submitting user, which can be disclosure by itself.")
        lines.append("  Use --include-control to count them.")

    lines.append("")
    if retention is True:
        lines.append("RETENTION: ON. CUPS is keeping documents. Set")
        lines.append("  'PreserveJobFiles No' in cupsd.conf and restart cups.")
    elif retention is False:
        lines.append("RETENTION: OFF (PreserveJobFiles No).")
    else:
        lines.append("RETENTION: unknown (could not read cupsd.conf).")

    if audit.unexamined:
        lines.append(
            f"VERDICT: INCOMPLETE. {audit.total} retained file(s) found, but "
            f"{len(audit.unexamined)} area(s) could not be examined."
        )
    elif audit.verdict is Verdict.RETAINED:
        # Name the remover that actually fits what remains. The old line said
        # `cancel -a -x` unconditionally, which over a spool holding only
        # TempDir residue named a command that clears none of it -- advice the
        # report's own "cancel cannot reach them" paragraph contradicts.
        everything = audit.targeted + audit.others
        # An entry --purge just failed on must not have --purge advised for
        # it: the advice would name a command whose failure is printed a few
        # lines up, in the same report.
        stuck = [e for e in everything
                 if e.kind is Kind.TEMP and e.name in purge_failed]
        how = []
        if any(e.job is not None for e in everything):
            how.append("cancel -a -x")
        if any(e.kind is Kind.TEMP and e.name not in purge_failed
               for e in everything):
            how.append("--purge")
        if how:
            advice = "Clear them with: " + ", then ".join(how)
            if stuck:
                advice += (" (--purge already FAILED on the rest; "
                           "see the NOT removed line(s) above)")
        elif stuck:
            advice = ("--purge just FAILED on what remains; "
                      "see the NOT removed line(s) above.")
        else:
            # Only unidentified files remain. No command fits by design:
            # nothing here removes what could not be identified.
            advice = ("Nothing removes what could not be identified; "
                      "look at the listing above.")
        lines.append(f"VERDICT: {audit.total} retained file(s) still on disk. {advice}")
    elif undestroyed:
        # The spool IS empty and the documents are NOT gone: --purge unlinked
        # the spool's entry while st_nlink said other hard links to the same
        # content survive. audit.total counts what is in the spool, so it is
        # correctly zero here, and "clean" read off that zero is a claim about
        # the DOCUMENTS that nothing measured.
        #
        # This is the seventh path to print "spool is clean" over readable
        # content -- see the incident comments at the head of this file for the
        # other six -- and the first one created by a fix. The 2026-08-17 round
        # added the honest per-entry line and raised the exit code, and stopped
        # short of the verdict, which is the line meant to be read last and
        # believed. A script could tell B from A by the exit code; a human read
        # four lines of clean bill of health after the warning.
        #
        # Same shape as the failed-purge branch above: name the condition, then
        # point at the detail line rather than restating it.
        noun = "document" if undestroyed == 1 else "documents"
        lines.append(
            f"VERDICT: spool is EMPTY but {undestroyed} {noun} NOT destroyed; "
            'see the "removed but NOT destroyed" line(s) above.')
    else:
        lines.append("VERDICT: spool is clean.")
    return lines


@dataclass(frozen=True)
class Removal:
    """What one purge pass did, entry by entry.

    `removed` is names whose content is gone. `undestroyed` is (name, other
    links): the spool's directory entry was unlinked, but st_nlink said that
    many OTHER hard links to the same content remain, so the entry is removed
    and the document is not destroyed -- two different claims, and collapsing
    them printed "removed:" plus exit 0 over readable content. `failed` is
    report-safe notes for entries still present; `failed_names` mirrors it
    with the raw names, so the renderer can stop advising --purge for exactly
    the entries this run just failed on.
    """

    removed: tuple[str, ...] = ()
    undestroyed: tuple[tuple[str, int], ...] = ()
    failed: tuple[str, ...] = ()
    failed_names: tuple[str, ...] = ()


def purge_outcome(
    result: Removal,
    after: Audit,
    retention: bool | None,
) -> tuple[list[str], int]:
    """What a purge run prints and exits with, from what actually happened.

    Pure, so main() carries no untested decision -- the shape whose absence
    produced findings in every review round of the first destructive half.

    The old half had purge_outcome AND leftover_caveats AND a nothing-to-purge
    branch: three renderers that had to agree about what remained, and they
    drifted twice. Here the after-state goes through render(), the same
    function the report path uses, so there is no second description of a
    spool to fall out of step with the first.
    """
    lines = [f"removed: {safe_name(n)}" for n in result.removed]
    # Removed-but-not-destroyed is its own line, never a bare "removed:". An
    # operator reads "removed" as "destroyed", and for a multiply-linked file
    # that reading is false: identical readable content survives at every
    # other link. CUPS itself cannot create a hard link (verified against
    # cupsd and its filters/backends), so anything here was linked by a hand
    # this tool cannot follow -- say so and raise the exit code below.
    lines += [
        f"removed but NOT destroyed: {safe_name(n)} ({k} other hard link(s) "
        "to the same content remain readable elsewhere)"
        for n, k in result.undestroyed
    ]
    # The notes arrive already escaped: they are built around safe_name at the
    # point of failure, where the raw name is still in hand.
    lines += [f"NOT removed: {n}" for n in result.failed]
    lines.append("")
    lines.append("The spool as it stands now:")
    lines += render(after, frozenset(), retention,
                    purge_failed=frozenset(result.failed_names),
                    undestroyed=len(result.undestroyed))
    # A failed removal means the file is demonstrably still there (or, for a
    # refusal, was never touched), and an undestroyed one means the content
    # is -- just not at this path -- so the exit code may rise but never
    # fall: a clean-looking re-read does not erase either. after.exit_code
    # already answers 2 for an unreadable or incomplete re-read.
    return lines, max(after.exit_code,
                      1 if (result.failed or result.undestroyed) else 0)


# --- I/O boundary ----------------------------------------------------------
# Thin wrappers over privileged operations, kept free of logic so everything
# above stays testable without a printer, a spool or root.


def _head(path: Path, n: int = 32) -> str | None:
    """First bytes of a file, decoded lossily, or None when the file could
    not be read. Used only to recognise known harmless formats such as PPDs.
    Never printed, never logged.

    None, not "": "could not read" and "read nothing" used to be the same
    value (#42), so the distinction lived in every caller's habit of failing
    closed on "". A caller now has to decide what an unreadable file is, and
    both callers list it as unexamined rather than classify it."""
    try:
        with path.open("rb") as fh:
            return fh.read(n).decode("utf-8", "replace")
    except OSError:
        # reason: None IS the recorded outcome, "could not read", and both
        # callers list such a file as unexamined rather than classify it (#42)
        return None


def _walk_temp(
    directory: Path,
    label: str,
    prefix: str,
    found: list[TempFile],
    unexamined: list[str],
    depth: int = 0,
) -> None:
    """Collect regular files under a TempDir, descending into subdirectories.

    Flagging a subdirectory as unexaminable turned a silent miss into a
    permanent false alarm: a real filter chain creates tmp/.cache, so the tool
    reported INCOMPLETE forever on any machine that had ever printed properly
    and could never say clean. Recurring known-good flags train you to skim the
    report, which is worse than not running it. So look inside instead.

    Symlinks are never followed, at any depth.
    """
    if depth >= MAX_TEMP_DEPTH:
        unexamined.append(
            f"{safe_name(label)}/{safe_name(prefix)} (nested deeper than {MAX_TEMP_DEPTH}, not examined)"
        )
        return
    try:
        children = sorted(directory.iterdir())
    except OSError as exc:
        unexamined.append(f"{safe_name(label)}/{safe_name(prefix)} (unreadable: {exc.__class__.__name__})")
        return

    for f in children:
        name = f"{prefix}{f.name}"
        # ONE lstat decides the type. is_symlink()/is_dir()/is_file() each
        # swallow errors and return False, so a file deleted mid-walk was
        # classified "not a regular file" and the race branch below could never
        # execute -- while its test passed by asserting only counts.
        try:
            st = f.lstat()
        except PermissionError:
            # Not a race. A directory readable but not traversable lands here,
            # and calling it "vanished" sends the user hunting a busy spool
            # instead of running sudo.
            unexamined.append(f"{safe_name(label)}/{safe_name(name)} (permission denied on stat)")
            continue
        except OSError:
            unexamined.append(f"{safe_name(label)}/{safe_name(name)} (vanished while reading)")
            continue

        link = stat.S_ISLNK(st.st_mode)
        if not link and stat.S_ISDIR(st.st_mode):
            _walk_temp(f, label, f"{name}/", found, unexamined, depth + 1)
            continue
        try:
            target = os.readlink(f) if link else "?"
        except OSError:
            target = "?"
        note = temp_child_note(
            label, name, is_symlink=link, is_dir=False,
            is_regular=stat.S_ISREG(st.st_mode), target=target,
        )
        if note is None:
            # st is already known; _head() answers None for a file it could
            # not read, and that file is UNEXAMINED, never classified (#42).
            head = _head(f)
            if head is None:
                unexamined.append(f"{safe_name(name)} (could not be read)")
            else:
                found.append(TempFile(name=name, size=st.st_size, head=head))
        else:
            unexamined.append(note)


@dataclass(frozen=True)
class TopEntry:
    """One top-level spool name, with the facts read from disk about it.

    Everything read_spool learns from lstat and readlink, and nothing else, so
    the decision below can be made without a filesystem.
    """

    name: str
    is_symlink: bool
    is_dir: bool
    is_regular: bool
    target: str = "?"


@dataclass(frozen=True)
class TopDecision:
    """Where a top-level entry goes. `wants_content` means read its head."""

    note: str | None = None
    suspect: bool = False
    wants_content: bool = False


def classify_top(e: TopEntry) -> TopDecision:
    """Which bucket a top-level spool entry belongs in.

    This was two near-identical blocks inside read_spool, one for names
    matching d<n>-<n> or c<n> and one for everything else, differing only in
    whether the name was suppressed from `top`. Duplicated blocks that must
    agree are the single most repeated defect on this branch: the
    never-follow-symlinks rule was applied to TempDir and not here, and a
    symlink named d00085-001 was unlinked, "1 removed" printed, and the target
    left readable. One function now, so there is no second site to forget.
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


def _list_names(p: Path) -> list[str]:  # pragma: no cover
    return sorted(q.name for q in p.iterdir())


def read_spool(
    spool: str,
    *,
    lister: Callable[[Path], list[str]] = _list_names,
    walk: Callable[..., None] = _walk_temp,
) -> Listing:
    """List the spool and its TempDir. Direct reads only.

    `lister` and `walk` are injectable so the two error-to-verdict mappings can
    be tested without needing a filesystem that produces ELOOP or ESTALE on
    demand, and without needing to be a particular user. Both mappings have
    been wrong before, and the TempDir one in the way that matters most: an
    unreadable TempDir fell through as an empty one and printed "spool is
    clean" over a readable document.

    The sudo escalation path is gone. It was a second implementation of this
    same listing and the two disagreed repeatedly -- different depth limits,
    different handling of directories, different classification of files whose
    content could not be read -- and that divergence produced a large share of
    the defects on this branch. The tool needs root to read /var/spool/cups
    anyway, so run it under sudo. An unreadable spool now reports DENIED
    instead of quietly taking a different code path with different rules.

    --temp and the cups-files.conf lookup are gone for the same reason: they
    multiplied the number of directories that could be "the" TempDir, and every
    finding about labels, containment and path comparison came from that.
    """
    root = Path(spool)
    try:
        top = tuple(lister(root))
        verdict = Verdict.CLEAN
    except PermissionError:
        return Listing(Verdict.DENIED)
    except FileNotFoundError:
        return Listing(Verdict.MISSING)
    except NotADirectoryError:
        return Listing(Verdict.NOT_A_DIRECTORY)
    except OSError as exc:
        # ELOOP, ESTALE, EIO. Calling all of them "permission denied, re-run
        # with sudo" sends the user to do something that will fail identically.
        return Listing(Verdict.DENIED, (), (), (f"could not read the spool: {exc.__class__.__name__}",))

    temp: tuple[TempFile, ...] = ()
    unexamined: list[str] = []
    default_tmp = root / TEMP_SUBDIR
    tdir: Path | None = default_tmp
    # A TempDir that is ITSELF a symlink is never followed: `<spool>/tmp ->
    # /anywhere` made --purge delete outside the audited spool and print
    # SCOPE CLEAN. Read through `default_tmp`, which is never None, so the
    # optional-ness of `tdir` cannot leak into os.readlink.
    try:
        if default_tmp.is_symlink():
            unexamined.append(
                f"{TEMP_SUBDIR} -> {safe_name(os.readlink(default_tmp))} "
                "(TempDir is a symlink, NOT followed)"
            )
            tdir = None
    except OSError:
        pass

    try:
        if tdir is None:
            raise FileNotFoundError  # symlinked TempDir, already recorded
        # Path.exists() does NOT swallow EACCES, so probing it outside this try
        # crashed with a traceback on a real 0710 spool.
        lister(tdir)
        found: list[TempFile] = []
        walk(tdir, TEMP_SUBDIR, "", found, unexamined)
        temp = tuple(found)
    except PermissionError:
        # Do NOT fall through with an empty tuple: that turned an unreadable
        # TempDir into an empty one and printed "spool is clean" over a
        # readable document.
        unexamined.append(f"{TEMP_SUBDIR}/ (permission denied)")
    except FileNotFoundError:
        # reason: no TempDir at all is nothing to examine, not something unexamined (above)
        # No TempDir at all. Nothing to examine is not the same as something
        # unexamined, and treating it as unexamined meant a spool without a
        # tmp/ could never exit 0.
        pass
    except NotADirectoryError:
        unexamined.append(f"{TEMP_SUBDIR} (exists but is not a directory, not examined)")
    except OSError as exc:
        unexamined.append(f"{TEMP_SUBDIR}/ (unreadable: {exc.__class__.__name__})")

    # Every top-level name is stat'd, including ones matching the document and
    # control patterns. They used to skip straight past this check, so the
    # never-follow-symlinks rule applied only to TempDir: a symlink named
    # d00085-001 was unlinked, "1 removed" was printed, and the target survived.
    # False assurance of destruction, the worst failure this tool can have.
    #
    # That property is NOT unique to symlinks, and stating it as though it were
    # is what hid the next instance: a HARD link passes S_ISREG, is unlinked
    # correctly, and still leaves identical content readable under the other
    # name. Three reviewers hit it independently on 2026-08-17. So this refusal
    # is not the whole answer to "did the content actually die" -- _unlink_at
    # reports st_nlink survivors, and purge_outcome raises the exit code, so the
    # claim matches what happened rather than what was attempted.
    #
    # Names matching neither pattern are read the same way TempDir files are,
    # so a document copied to d00085-001.bak is counted rather than dropped;
    # `ls` would have shown it.
    suspect: list[str] = []
    extra: list[TempFile] = []
    for n in top:
        if n == TEMP_SUBDIR:
            continue
        f = root / n
        try:
            st = f.lstat()
        except PermissionError:
            unexamined.append(f"{safe_name(n)} (permission denied on stat)")
            suspect.append(n)
            continue
        except OSError:
            unexamined.append(f"{safe_name(n)} (vanished while reading)")
            suspect.append(n)
            continue
        tgt = "?"
        if stat.S_ISLNK(st.st_mode):
            try:
                tgt = os.readlink(f)
            except OSError:
                tgt = "?"
        d = classify_top(TopEntry(
            name=n,
            is_symlink=stat.S_ISLNK(st.st_mode),
            is_dir=stat.S_ISDIR(st.st_mode),
            is_regular=stat.S_ISREG(st.st_mode),
            target=tgt,
        ))
        if d.note is not None:
            unexamined.append(d.note)
        if d.suspect:
            suspect.append(n)
        if d.wants_content:
            # The try/except OSError that stood here could never fire: _head()
            # catches its own. It answers None instead, and that is unexamined.
            head = _head(f)
            if head is None:
                unexamined.append(f"{safe_name(n)} (could not be read)")
            else:
                extra.append(TempFile(name=n, size=st.st_size, head=head))

    return Listing(
        verdict,
        tuple(n for n in top if n != TEMP_SUBDIR and n not in suspect),
        temp,
        tuple(unexamined),
        TEMP_SUBDIR,
        tuple(extra),
    )



# CUPS spools live on POSIX filesystems, and the anchored unlink below is
# built from POSIX openat semantics. A platform without them gets a refusal,
# not a fallback to path arithmetic -- the arithmetic is the thing that was
# raceable.
_CAN_ANCHOR = (
    hasattr(os, "O_DIRECTORY")
    and hasattr(os, "O_NOFOLLOW")
    and os.open in os.supports_dir_fd
    and os.unlink in os.supports_dir_fd
)


def _component_note(rel: str, part: str, fd: int, exc: OSError) -> str:
    """Why a path component refused to open, with symlinks named as such.

    The kernel refuses a symlinked component -- but as ENOTDIR, not ELOOP,
    when O_DIRECTORY is set alongside O_NOFOLLOW (measured on Linux 7.0: the
    directory check sees the link itself and wins). Both classes therefore
    lstat the component so a symlink is reported as the symlink it is; the
    lstat is for the MESSAGE only, after the refusal has already happened,
    so racing it cannot reopen the path.
    """
    if exc.errno in (errno.ELOOP, errno.ENOTDIR):
        with contextlib.suppress(OSError):  # reason: the lstat is for the message only, after the refusal; a failed one loses the symlink detail
            if stat.S_ISLNK(os.lstat(part, dir_fd=fd).st_mode):
                return (f"{safe_name(rel)} (refused: {safe_name(part)} is "
                        "a symlink, NOT followed)")
    return f"{safe_name(rel)} (could not reach it: {exc.__class__.__name__})"


def _unlink_at(root_fd: int, rel: str) -> tuple[str | None, int]:
    """Remove `rel` under an already-open spool directory, or say why not.

    Returns (note, survivors). `note` is None when the file is gone -- which
    includes finding it already gone, the goal state however reached -- and a
    report-safe note on any refusal or failure. `survivors` is how many OTHER
    hard links to the same content remain after a successful unlink, read
    from st_nlink on the fstat already taken for the S_ISREG check -- no
    second stat, no second window. Unlinking one name of a multiply-linked
    file removes the entry and destroys nothing, and the caller must not be
    allowed to collapse those two claims.

    Containment is enforced by the kernel, not by path arithmetic: every
    directory component is opened O_NOFOLLOW | O_DIRECTORY relative to the
    previous descriptor, and the unlink is anchored to the last of them, so a
    symlink anywhere in the path fails with ELOOP instead of being traversed.
    The old delete() resolved the path and compared it against the spool
    root, a fact that could stop being true between the comparison and the
    unlink; a descriptor cannot be redirected that way. The one path decision
    left in code is refusing '', '.' and '..' as components, because '..' is
    a real directory, not a symlink, and O_NOFOLLOW would step through it and
    out of the spool without complaint.

    The leaf is opened (O_NONBLOCK, so a fifo cannot hang the run -- the
    _read_conf lesson, applied before it is relearned) and fstat'd, and
    anything that is not a regular file is refused by type. That includes a
    leaf that became a symlink: unlinking a link while its target survives
    would print "removed" over readable content, the false assurance of
    destruction that is the worst failure this tool can have. The window
    between that fstat and the unlink is real but sits inside the audited
    directory: anyone who can swap files there can already read them.
    """
    parts = rel.split("/")
    if any(p in ("", ".", "..") for p in parts):
        # No listing can produce such a name -- iterdir cannot return one --
        # so an entry containing it did not come from the audit.
        return f"{safe_name(rel)} (refused: not a name the audit could have produced)", 0
    opened: list[int] = []
    try:
        fd = root_fd
        for part in parts[:-1]:
            try:
                fd = os.open(part, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW
                             | os.O_CLOEXEC, dir_fd=fd)
            except FileNotFoundError:
                # A PARENT that is gone is not the file being gone: a renamed
                # parent leaves the content alive at its new path, and "gone"
                # here reported a destruction this tool did not perform (#45).
                # Said as a note, so it is told apart from a real removal.
                return (f"{safe_name(rel)} (a parent component vanished mid-run; "
                        "NOT removed by this tool, and it may survive elsewhere)"), 0
            except OSError as exc:
                return _component_note(rel, part, fd, exc), 0
            opened.append(fd)
        leaf = parts[-1]
        try:
            lfd = os.open(leaf, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK
                          | os.O_CLOEXEC, dir_fd=fd)
        except FileNotFoundError:
            # reason: the LEAF is gone between listing and open: nothing to delete, (None, 0) is 'gone'
            return None, 0
        except OSError as exc:
            if exc.errno == errno.ELOOP:
                return (f"{safe_name(rel)} (refused: a symlink, NOT removed; "
                        "unlinking a link does not destroy its target)"), 0
            return f"{safe_name(rel)} (refused, could not verify it: {exc.__class__.__name__})", 0
        try:
            st = os.fstat(lfd)
        finally:
            os.close(lfd)
        if not stat.S_ISREG(st.st_mode):
            return f"{safe_name(rel)} (refused: not a regular file)", 0
        try:
            os.unlink(leaf, dir_fd=fd)
        except FileNotFoundError:
            # reason: the leaf went between open and unlink: nothing left to delete, (None, 0) is 'gone'
            return None, 0
        except OSError as exc:
            return (f"{safe_name(rel)} (delete FAILED: {exc.__class__.__name__}; "
                    "it is still there)"), 0
        # st_nlink counted this entry, so anything above 1 is links that
        # SURVIVE the unlink just performed. The unlink still happens -- a
        # refusal would leave the spool copy in place too, handing the
        # operator two copies of the document instead of one -- but the
        # claim "removed" must not become the claim "destroyed".
        return None, max(0, st.st_nlink - 1)
    finally:
        for f in opened:
            os.close(f)


def delete_residue(
    spool: str,
    entries: tuple[Entry, ...],
    *,
    can_anchor: bool = _CAN_ANCHOR,
) -> Removal:
    """Unlink `entries` under `spool`. Returns a Removal, per entry.

    No injectable unlink and no injectable resolve, on purpose: in the old
    delete() the seams were the test surface, and the safety property lived
    in the defaults the tests bypassed. Here the safety property IS the
    syscall pattern, so the suites drive real fixture filesystems -- no root
    needed for any of them. The only seam is the platform capability, which a
    test on Linux could not otherwise make false.

    The spool root itself is opened WITHOUT O_NOFOLLOW: the operator named
    that path, the same trust read_spool extends when it lists through it.
    Everything below the root refuses symlinks, matching the listing exactly,
    so the purge can only reach what the audit reached, by the same rules.
    """
    if not can_anchor:
        return Removal(
            failed=tuple(
                f"{safe_name(e.name)} (refused: no openat/unlinkat on this "
                "platform, so removal cannot be anchored to the audited directory)"
                for e in entries
            ),
            failed_names=tuple(e.name for e in entries),
        )
    try:
        root_fd = os.open(spool, os.O_RDONLY | os.O_DIRECTORY | os.O_CLOEXEC)
    except OSError as exc:
        return Removal(
            failed=tuple(
                f"{safe_name(e.name)} (the spool itself could not be opened: "
                f"{exc.__class__.__name__})"
                for e in entries
            ),
            failed_names=tuple(e.name for e in entries),
        )
    removed: list[str] = []
    undestroyed: list[tuple[str, int]] = []
    failed: list[str] = []
    failed_names: list[str] = []
    try:
        for e in entries:
            note, survivors = _unlink_at(root_fd, e.name)
            if note is not None:
                failed.append(note)
                failed_names.append(e.name)
            elif survivors:
                undestroyed.append((e.name, survivors))
            else:
                removed.append(e.name)
    finally:
        os.close(root_fd)
    return Removal(tuple(removed), tuple(undestroyed), tuple(failed),
                   tuple(failed_names))


def parse_retention(body: bytes) -> bool:
    """Does this config body leave CUPS keeping job files?

    Pure, so the precedence rule can be tested without a filesystem or a CUPS
    install. It used to live inside retention_state, under `pragma: no cover`,
    where the rule below was got wrong once already and nothing could have
    caught it: the exemption is why the only test of this was an end-to-end run
    on a machine that happened to have the right config.
    """
    # cupsd honours the LAST matching directive. Returning on the first made a
    # hand-edited config with "No" followed by "Yes" report RETENTION: OFF on a
    # host that was retaining documents -- danger reported as safety.
    setting: bytes | None = None
    for line in body.splitlines():
        m = re.match(rb"^\s*PreserveJobFiles\s+(\S+)", line, re.IGNORECASE)
        if m:
            setting = m.group(1).lower()
    if setting is not None:
        return setting not in (b"no", b"off", b"false", b"0")
    # Unset means the compiled default, which on the hosts measured here keeps
    # documents. Reporting "off" on an absent directive would be a guess in the
    # dangerous direction.
    return True


def retention_state(conf: str) -> bool | None:
    """Is CUPS configured to keep job files? None if the config is unreadable.

    Nothing but the read lives here now, and it reads bytes: a printer config
    is not guaranteed UTF-8, and decoding it is a step that can fail or corrupt
    for no benefit when all that is wanted is a directive match.
    """
    try:
        body = _read_conf(conf)
    except OSError:
        # reason: None is the documented "config unreadable", which render()
        # and purge_outcome() show as such rather than as a retention verdict
        return None
    return parse_retention(body)


def _read_conf(path: str) -> bytes:
    """Read a config file, refusing the types that would hang.

    Path.read_bytes() on a FIFO BLOCKS FOREVER waiting for a writer, and a hang
    in a security tool reads as "still checking" rather than as a failure.
    --conf takes an arbitrary path, so the type is checked before opening.

    Character devices are deliberately still allowed: `--conf /dev/null` is a
    legitimate "empty config" used throughout this repo's own suites, and it
    reads as EOF immediately. That is why this refuses less than a writer
    would have to, which requires a regular file because REPLACING /dev/null is a very
    different act from reading it.
    """
    st = os.stat(path)
    if stat.S_ISFIFO(st.st_mode) or stat.S_ISSOCK(st.st_mode):
        raise OSError(errno.EINVAL, "would block on a fifo or socket", path)
    return Path(path).read_bytes()


def main(argv: list[str] | None = None) -> int:
    # Filenames are bytes on Linux and arrive as str with lone surrogates, from
    # both listing paths. print() encodes strictly and dies mid-report with
    # UnicodeEncodeError, exit 1 -- which this tool uses for "content is still
    # there", so a wrapper cannot tell a crash from a finding. The parsing side
    # was hardened for hostile names; the output side undid it.
    with contextlib.suppress(AttributeError, ValueError):  # reason: a stdout with no reconfigure (a StringIO under test) keeps strict encoding, as above
        sys.stdout.reconfigure(errors="backslashreplace")  # type: ignore[union-attr]

    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    ap.add_argument("jobs", nargs="*", type=int, help="job ids to highlight")
    ap.add_argument(
        "--purge",
        action="store_true",
        help="remove files identified as print data by their own content, "
             "which `cancel` cannot reach; never job files, never "
             "unidentified files",
    )
    ap.add_argument(
        "--include-control",
        action="store_true",
        help="also count c<job> control files, which carry job titles",
    )
    ap.add_argument("--spool", default=DEFAULT_SPOOL, help=f"spool directory (default {DEFAULT_SPOOL})")
    ap.add_argument("--conf", default=DEFAULT_CONF, help=f"cupsd.conf path (default {DEFAULT_CONF})")
    args = ap.parse_args(argv)

    if args.purge and args.jobs:
        # Refused, not reinterpreted and not silently ignored: the scoped
        # purge ("85 --purge") is the invocation that fired `&& echo SAFE` in
        # four separate review rounds of the deleted half. Nothing --purge
        # removes carries a job id, so a job-scoped purge is a request this
        # tool cannot mean anything by. Job files are CUPS' own.
        ap.error(
            "--purge cannot be scoped by job id; nothing it removes has one. "
            "For job files use: cancel -x "
            + " ".join(str(j) for j in sorted(set(args.jobs)))
        )

    jobs = frozenset(args.jobs)
    audit = classify(read_spool(args.spool), jobs, args.include_control)

    if not audit.readable:
        for line in render(audit, jobs):
            print(line)
        return 2

    if args.purge:
        victims = purgeable(audit)
        if not victims:
            # An incomplete audit lands here too, with victims empty or not:
            # removing what WAS positively seen is safe regardless, and the
            # exit code below stays 2 through audit.exit_code either way.
            print("--purge: nothing here is identified as print data by its own")
            print("content, so there is nothing this tool will remove. Job files")
            print("belong to `cancel`; unidentified files are yours to judge.")
            print("")
            for line in render(audit, jobs, retention_state(args.conf)):
                print(line)
            return audit.exit_code
        result = delete_residue(args.spool, victims)
        after = classify(read_spool(args.spool), frozenset(), args.include_control)
        lines, code = purge_outcome(result, after, retention_state(args.conf))
        for line in lines:
            print(line)
        return code

    for line in render(audit, jobs, retention_state(args.conf)):
        print(line)
    # On the report path the exit code means one thing and only one thing:
    # nothing is left. It never reports on an action, because none was taken.
    return audit.exit_code


if __name__ == "__main__":
    sys.exit(main())
