#!/usr/bin/env python3
"""Report documents left behind in the CUPS print spool. Reports only.

Printing sends the whole document through CUPS, and CUPS may keep a copy after
the job finishes. If you ever print a password, a recovery sheet or a key, that
copy outlives the paper, on a machine that may not be yours.

Usage:
    python spool-audit.py                    # report on everything
    python spool-audit.py 85 86              # report, highlighting those jobs
    python spool-audit.py --include-control  # also count job control files
    python spool-audit.py --spool DIR        # audit a directory instead

Exit status: 0 nothing retained, 1 content still on disk, 2 could not be read.

Reads directly, so it must run as root for the real spool (sudo). There is no
privilege-escalation path inside the tool: a second implementation of the same
listing kept disagreeing with the first, and that divergence caused a large
share of this file's history of bugs.

THIS TOOL NEVER WRITES
    It opens files to read their first bytes and it reads cupsd.conf. It does
    not delete, edit, restart or configure anything, and it takes no flag that
    would. That is a deliberate cut, made 2026-08-14 after this file had spent
    fifteen review rounds mostly on its own destructive half: a --fix that
    could destroy a device node, a --fix that truncated cupsd.conf to zero
    bytes, a --purge that followed a symlinked TempDir out of the directory it
    was told to audit. None of those bugs were possible in the reporting half,
    and the clearing they were re-implementing is something CUPS already does
    correctly. See "HOW TO CLEAR IT" below.

    The reporting half is the part with no equivalent: answering "what is this
    file, actually" about spool contents you did not put there.

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
    which is the job it is for.

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
                 Reported separately, never counted as a leak.
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
    known-harmless formats. It is never printed. Empty string means the header
    could not be read, which is treated as "unknown", not as "harmless".
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
            f"{label}/{name} -> {target} (symlink, NOT followed; "
            "not examined, and deleting the link would not remove the target)"
        )
    if is_dir:
        # Callers recurse into real directories. This branch remains for a
        # directory that cannot be read, and for callers that do not descend.
        return f"{label}/{name}/ (subdirectory, not examined)"
    if not is_regular:
        return f"{label}/{name} (not a regular file, not examined)"
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


def render(audit: Audit, jobs: frozenset[int], retention: bool | None = None) -> list[str]:
    """Format an Audit for a human. Returns lines; printing is the caller's job.

    `retention` is whether CUPS is currently configured to keep documents, read
    from cupsd.conf. It is deliberately a separate input: inferring it from
    "are there files here" told Joe the host still retained data immediately
    after a successful --fix had turned retention off, because leftover
    documents from before the fix were still on disk.
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
    lines += [f"  {safe_name(e.name)}" for e in ordered[:20]]
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
        lines.append(f"VERDICT: {audit.total} retained file(s) still on disk. "
                     "Clear them with: cancel -a -x")
    else:
        lines.append("VERDICT: spool is clean.")
    return lines


# --- I/O boundary ----------------------------------------------------------
# Thin wrappers over privileged operations, kept free of logic so everything
# above stays testable without a printer, a spool or root.


def _head(path: Path, n: int = 32) -> str:
    """First bytes of a file, decoded lossily. Used only to recognise known
    harmless formats such as PPDs. Never printed, never logged."""
    try:
        with path.open("rb") as fh:
            return fh.read(n).decode("utf-8", "replace")
    except OSError:
        return ""


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
            f"{label}/{prefix} (nested deeper than {MAX_TEMP_DEPTH}, not examined)"
        )
        return
    try:
        children = sorted(directory.iterdir())
    except OSError as exc:
        unexamined.append(f"{label}/{prefix} (unreadable: {exc.__class__.__name__})")
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
            unexamined.append(f"{label}/{name} (permission denied on stat)")
            continue
        except OSError:
            unexamined.append(f"{label}/{name} (vanished while reading)")
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
            # No try here: st is already known and _head() swallows its own
            # OSError, so the handler that used to wrap this could not fire.
            # An unreadable file arrives with head="" and is treated as
            # possible content by is_harmless_temp.
            found.append(TempFile(name=name, size=st.st_size, head=_head(f)))
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
            note=(note or f"{e.name} (not examined)").replace("./", "", 1),
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
                f"{TEMP_SUBDIR} -> {os.readlink(default_tmp)} "
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
            unexamined.append(f"{n} (permission denied on stat)")
            suspect.append(n)
            continue
        except OSError:
            unexamined.append(f"{n} (vanished while reading)")
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
            try:
                extra.append(TempFile(name=n, size=st.st_size, head=_head(f)))
            except OSError:
                unexamined.append(f"{n} (vanished while reading)")

    return Listing(
        verdict,
        tuple(n for n in top if n != TEMP_SUBDIR and n not in suspect),
        temp,
        tuple(unexamined),
        TEMP_SUBDIR,
        tuple(extra),
    )



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

    Nothing but the read lives here now. Read as bytes for the same reason
    this reads bytes: a printer config is not guaranteed UTF-8,
    and decoding it is a step that can fail or corrupt for no benefit when all
    that is wanted is a directive match.
    """
    try:
        body = _read_conf(conf)
    except OSError:
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
    with contextlib.suppress(AttributeError, ValueError):
        sys.stdout.reconfigure(errors="backslashreplace")  # type: ignore[union-attr]

    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    ap.add_argument("jobs", nargs="*", type=int, help="job ids to highlight")
    ap.add_argument(
        "--include-control",
        action="store_true",
        help="also count c<job> control files, which carry job titles",
    )
    ap.add_argument("--spool", default=DEFAULT_SPOOL, help=f"spool directory (default {DEFAULT_SPOOL})")
    ap.add_argument("--conf", default=DEFAULT_CONF, help=f"cupsd.conf path (default {DEFAULT_CONF})")
    args = ap.parse_args(argv)

    jobs = frozenset(args.jobs)
    audit = classify(read_spool(args.spool), jobs, args.include_control)

    if not audit.readable:
        for line in render(audit, jobs):
            print(line)
        return 2

    for line in render(audit, jobs, retention_state(args.conf)):
        print(line)
    # The exit code means one thing and only one thing: nothing is left. It
    # never reports on an action, because this tool takes none.
    return audit.exit_code


if __name__ == "__main__":
    sys.exit(main())
