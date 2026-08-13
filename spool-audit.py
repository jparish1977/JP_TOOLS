#!/usr/bin/env python3
"""Audit and clear documents left behind in the CUPS print spool.

Printing sends the whole document through CUPS, and CUPS may keep a copy after
the job finishes. If you ever print a password, a recovery sheet or a key, that
copy outlives the paper, on a machine that may not be yours.

Usage:
    python spool-audit.py                    # report on everything
    python spool-audit.py 85 86              # report, highlighting those jobs
    python spool-audit.py 85 86 --purge      # delete ONLY those jobs' documents
    python spool-audit.py --purge            # delete every retained document
    python spool-audit.py --include-control  # also count/remove job control files
    python spool-audit.py --include-unrecognised  # also DELETE unidentified files
    python spool-audit.py --fix              # stop CUPS retaining documents
    python spool-audit.py --spool DIR        # audit a directory instead

Reads directly, so it must run as root for the real spool (sudo). There is no
privilege-escalation path inside the tool: a second implementation of the same
listing kept disagreeing with the first, and that divergence caused a large
share of this file's history of bugs.

Reading the spool needs root, so this normally runs under sudo.

WHY THE PROBLEM EXISTS AT ALL
    CUPS documents PreserveJobFiles as defaulting to No, so document files
    should never survive a completed job. They do. Measured in a clean Ubuntu
    24.04 container on 2026-08-12, stock cupsd.conf with no PreserveJobFiles
    directive present:

        t+1s  d-files=1     t+30s  d-files=1
        t+10s d-files=1     t+60s  d-files=1

    The document sat there for the full minute. Writing "PreserveJobFiles No"
    explicitly -- which --fix does -- stops it: a print afterwards left no
    document file at all. So an unset directive and an explicit No behave
    DIFFERENTLY, and the documented default cannot be relied on.

    That matches apple/cups issue #6083, open with no root cause and the
    repository archived in March 2026. The issue only reports macOS; this
    reproduces it on Linux.

    It is the reason --fix is not ceremony around a setting you could just set.

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
                 with --include-control, and never removed without it.
    tmp/cups-*   CUPS runtime files (lockfiles, notifier sockets). NOT content.
                 Reported separately, never counted as a leak, never purged --
                 deleting them removes a lock from under a running cupsd.
"""

from __future__ import annotations

import argparse
import contextlib
import os
import re
import shutil
import stat
import subprocess
import sys
import tempfile
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
ARTIFACT = re.compile(r"^cups-.*(lockfile|notifier|socket)$|^cups-dbus-")



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


def should_restart_cups(conf: str) -> bool:
    """Should --fix restart the live daemon after writing `conf`?

    Only when `conf` IS the daemon's config. `--fix --conf /tmp/x` restarted
    the machine's real cups.service and reported success while
    /etc/cups/cupsd.conf was untouched, then "verified" by re-reading the file
    it had just written -- proof-shaped, and checking the wrong file.
    """
    here, system = _safe_resolve(Path(conf)), _safe_resolve(Path(DEFAULT_CONF))
    if here is not None and system is not None:
        return here == system
    return conf == DEFAULT_CONF


def _safe_resolve(p: Path) -> Path | None:
    """resolve() or None. It touches the filesystem and CAN raise.

    Same class as Path.exists() not swallowing EACCES: an unreadable component
    anywhere in the path makes resolve() raise, and `--spool /proc/1/root/x`
    crashed with a traceback. The containment roots are the safety boundary, so
    a root that cannot be resolved must stop a purge rather than be guessed at.
    """
    try:
        return p.resolve()
    except (OSError, RuntimeError):
        # RuntimeError("Symlink loop from ...") on ELOOP in CPython <= 3.12,
        # which CI pins and Ubuntu 24.04 ships. Catching only OSError let it
        # escape as a traceback with exit 1 -- the code this tool reserves for
        # "content you care about is still there", so a wrapper could not tell
        # a crash from a finding.
        return None


def is_inside(candidate: Path, roots: tuple[Path, ...]) -> bool:
    """Is `candidate` at or below one of `roots`? Pure path arithmetic.

    THE invariant this tool was missing. Five findings across three review
    rounds were all one absent containment rule, re-decided ad hoc at each
    site and therefore right at some and wrong at others:

      - an absolute --temp replaced the spool in a pathlib join, so --purge
        deleted from the live spool when told to audit a backup copy
      - a relative --temp built a path that did not exist, and the resulting
        FileNotFoundError counted as a successful delete
      - `--spool /var/spool/cups/` with a trailing slash compared unequal and
        skipped the configured TempDir
      - symlinks among TempDir's children were followed and reported destroyed
        while their targets survived
      - TempDir ITSELF being a symlink was never checked, so --purge deleted a
        file outside the named spool and printed SCOPE CLEAN

    Each fix taught one site the rule and left its twins wrong. Deciding it
    once, here, is what makes the class unable to recur.

    Both arguments must already be resolved: resolution touches the filesystem,
    this does not.
    """
    return any(candidate == r or candidate.is_relative_to(r) for r in roots)


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
            "removing it would not remove the target)"
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
    # Checked first, so nothing below can excuse an actual document.
    if any(f.head.startswith(m) for m in DOCUMENT_MAGIC):
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
            Entry(name=f"{listing.temp_label}/{f.name}", kind=Kind.ARTIFACT, job=None)
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


def victims_for(audit: Audit, include_unrecognised: bool = False) -> tuple[Entry, ...]:
    """What --purge should delete.

    If job ids were given, only those. Deleting everything when the user named
    specific jobs destroys other people's documents on a shared printer, which
    is precisely the machine this tool is aimed at.
    """
    pool = audit.targeted if audit.asked_for_jobs else audit.targeted + audit.others
    if include_unrecognised:
        return pool
    # "Over-report, never dismiss" is the right rule for a REPORT and the wrong
    # one for a DELETE SET, and the same predicate was serving both. A file is
    # UNRECOGNISED because it failed to be recognised as harmless, not because
    # it matched a print format -- so `--purge` deleted README-do-not-delete,
    # plain text with no print magic, and printed SCOPE CLEAN. Never destroy
    # what could not be identified; say so instead.
    return tuple(e for e in pool if e.kind is not Kind.UNRECOGNISED)


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
            lines.append("  remove ONLY these with: --purge")
        lines.append("")

    # Kind means EVIDENCE since the classification was split; location comes
    # from the path. Reading Kind as location inverted both explanatory blocks:
    # a tmp/ file was described as top-level and a top-level copy as TempDir
    # scratch, sending the operator to the wrong directory.
    prefix = f"{TEMP_SUBDIR}/"
    unattributed = [e for e in audit.others if e.kind in (Kind.TEMP, Kind.UNRECOGNISED)]
    temp = [e for e in unattributed if e.name.startswith(prefix)]
    unknown = [e for e in unattributed if not e.name.startswith(prefix)]
    rest = [e for e in audit.others if e.kind not in (Kind.TEMP, Kind.UNRECOGNISED)]

    lines.append(f"OTHER RETAINED FILES: {len(audit.others)}")
    shown = (rest + unknown + temp)[:20]
    lines += [f"  {safe_name(e.name)}" for e in shown]
    if len(audit.others) > 20:
        lines.append(f"  ... and {len(audit.others) - 20} more")
    if unknown:
        lines.append("")
        lines.append(
            f"  {len(unknown)} of these are top-level files matching no known"
        )
        lines.append("  spool pattern. Not identified as harmless, so not ruled out.")
    if temp:
        lines.append("")
        lines.append(
            f"  {len(temp)} of these are in the CUPS TempDir, unrecognised."
        )
        lines.append("  They may be document content mid-filter. They carry no job id,")
        lines.append("  so they cannot be targeted by number. Purge without job ids.")

    if audit.artifacts:
        # Split by what they actually are. Filing a stray zero-length file
        # under "CUPS RUNTIME FILES" trains the operator to skim the one
        # section a real stray could hide in -- but dropping it from the report
        # instead would make it invisible, which is worse.
        runtime = [e for e in audit.artifacts if ARTIFACT.match(e.name.rsplit("/", 1)[-1])]
        other = [e for e in audit.artifacts if e not in runtime]
        if runtime:
            lines.append("")
            lines.append(f"CUPS RUNTIME FILES (not document content, never purged): {len(runtime)}")
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
        lines.append("  Use --include-control to count and purge them.")

    lines.append("")
    if retention is True:
        lines.append("RETENTION: ON. CUPS is keeping documents. --fix stops that.")
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
        lines.append(f"VERDICT: {audit.total} retained file(s) still on disk. --purge clears them.")
    else:
        lines.append("VERDICT: spool is clean.")
    return lines


@dataclass(frozen=True)
class Outcome:
    """What to print and what to exit with. Pure: no I/O, no printing.

    main()'s decision logic lived inline and untested, and it produced findings
    in every review round of this branch: a --fix failure discarded so the run
    exited 0, a caveat present in one branch and missing from its twin, an exit
    code that collapsed two states. The tested parts of this file stopped
    generating defects; this part never did, because nothing exercised it.
    """

    lines: tuple[str, ...] = ()
    code: int = 0


def leftover_caveats(
    audit: Audit, jobs: frozenset[int], include_unrecognised: bool = False
) -> tuple[str, ...]:
    """What a purge could not remove, and why. ONE function, both call sites.

    The "nothing to purge" branch and the post-purge branch each grew their own
    version of this and drifted: the caveat about files carrying no job id was
    added to one and not the other, so `85 --purge` over a spool holding only a
    d00085-001.bak printed "Nothing to purge." and exited 0 over a file it had
    just classified as a PDF. Sharing the function is what stops the twins
    diverging again.
    """
    out = []
    if audit.uncounted_control:
        out.append(
            f"{audit.uncounted_control} control file(s) were not counted or removed; "
            "they carry the job title. Use --include-control."
        )
    # ANY content a scoped purge will not touch, not just the unattributable
    # kind. Checking only `job is None` meant a document belonging to job 77
    # produced no caveat, so `85 --purge` printed SCOPE CLEAN and exited 0 with
    # a readable PDF on disk. The invariant fixtures had the same blind spot:
    # every scoped case paired job 85 with an unattributable leftover, never
    # with another job's document.
    unrecognised = [e for e in audit.others if e.kind is Kind.UNRECOGNISED]
    if unrecognised and jobs:
        # Checked BEFORE the flag: unrecognised files carry no job id, so a
        # scoped purge can never reach them however the flag is set. The old
        # ordering handed scoped runs advice that could not work, and only
        # corrected itself after they had followed it.
        out.append(
            f"{len(unrecognised)} unidentified file(s) carry no job id, so a scoped "
            "purge cannot reach them. Re-run --purge --include-unrecognised "
            "without job ids."
        )
    elif unrecognised and not include_unrecognised:
        out.append(
            f"{len(unrecognised)} file(s) could not be identified and were NOT "
            "deleted. Inspect them, then use --include-unrecognised if they "
            "really are print data."
        )

    if jobs:
        # Exclude the unrecognised: they are reported above, and counting them
        # here too read as two files when there was one.
        no_job = [e for e in audit.others
                  if e.job is None and e.kind is not Kind.UNRECOGNISED]
        other_job = [e for e in audit.others if e.job is not None]
        if no_job:
            out.append(
                f"{len(no_job)} file(s) carry no job id and cannot be purged by job "
                "number. Re-run --purge without job ids to clear them."
            )
        if other_job:
            js = ", ".join(str(j) for j in sorted({e.job for e in other_job if e.job is not None}))
            out.append(
                f"{len(other_job)} file(s) belong to other job(s) ({js}) and were "
                "not touched. Re-run --purge without job ids to clear them."
            )
    return tuple(out)


def fix_outcome(ok: bool, detail: str, also_purging: bool) -> Outcome:
    """Result of --fix. A failure must never be forgotten by a later success."""
    if ok:
        line = f"PreserveJobFiles set to No; {detail}."
        if also_purging:
            return Outcome((line,), 0)
        return Outcome((line, "Existing files are untouched. Run --purge to clear them."), 0)
    lines = [f"FAILED: {detail}"]
    if also_purging:
        # The config may be written and only the restart failed, so the files on
        # disk are still worth clearing -- but the failure has to survive into
        # the exit code, or `--fix --purge && echo SAFE` fires with retention
        # still on and the next print leaks again.
        lines.append("Continuing to --purge anyway; files on disk are unaffected.")
    return Outcome(tuple(lines), 1)


def purge_precheck(
    audit: Audit, jobs: frozenset[int], roots_ok: bool, include_unrecognised: bool = False
) -> Outcome | None:
    """Decide before deleting. None means "go ahead and delete"."""
    if not roots_ok:
        return Outcome((
            "REFUSING TO PURGE: the audited path could not be resolved,",
            "  so nothing can be proven to be inside it.",
        ), 2)
    if victims_for(audit, include_unrecognised):
        return None

    lines = ["Nothing to purge."]
    code = 1 if audit.total else 0
    caveats = leftover_caveats(audit, jobs, include_unrecognised)
    if caveats:
        # Previously this printed the single line "Nothing to purge." over a
        # retained document and exited 0, while the report path on the same
        # spool said "1 retained file(s) still on disk" and exited 1. The tool
        # contradicted itself depending on the flag.
        scope = "for the job(s) named" if jobs else ""
        state = f"{audit.total} file(s) still on disk" if audit.total else "see below"
        headline = f"Nothing to purge{' ' + scope if scope else ''}: {state}."
        lines = [headline] + [f"  {c}" for c in caveats]
    if audit.unexamined:
        lines += [f"{len(audit.unexamined)} area(s) could not be examined:"]
        lines += [f"  {u}" for u in audit.unexamined]
        lines += ["This is NOT a clean result."]
        return Outcome(tuple(lines), 2)
    return Outcome(tuple(lines), code)


def purge_outcome(
    deleted: int, failed: int, after: Audit | None, jobs: frozenset[int],
    include_unrecognised: bool = False,
) -> Outcome:
    """Decide after deleting. `after` is None when the re-read failed."""
    if failed:
        # 1, not 2. A review round argued this was "cannot tell" and I applied
        # it without forming my own view. `failed` is incremented on a
        # containment refusal, a PermissionError from unlink, or an
        # IsADirectoryError -- in every one of those the file is demonstrably
        # STILL THERE. That is what 1 means here. Only a resolve failure is
        # genuinely unknown, and it is the narrow case.
        return Outcome((
            f"DELETE FAILED for {failed} file(s); {deleted} removed.",
            # Do not name a cause: permissions, a directory matching the
            # document pattern, a read-only mount and a symlink loop all land
            # here, and asserting the wrong one sends the user somewhere
            # useless.
            "Not files being recreated: the removals themselves failed.",
        ), 1)
    if after is None:
        return Outcome((f"{deleted} removed, but the spool could not be re-read to confirm.",), 2)

    remaining = len(victims_for(after, include_unrecognised))
    lines = [f"{deleted} removed. Remaining in scope: {remaining}"]
    if after.unexamined:
        lines += [f"NOT CLEAN: {len(after.unexamined)} area(s) could not be examined:"]
        lines += [f"  {u}" for u in after.unexamined]
        return Outcome(tuple(lines), 2)
    if remaining:
        return Outcome((*lines, "STILL PRESENT after a successful delete."), 1)
    caveats = leftover_caveats(after, jobs, include_unrecognised)
    if caveats:
        # The exit code comes from what REMAINS, not from a caveat string
        # existing: uncounted control files are opt-in by design and the report
        # path calls that spool clean, so making them force 1 here had the two
        # paths contradict each other on the same spool.
        lines += ["NOT FULLY CLEAN:" if after.total else "Note:"]
        lines += [f"  {c}" for c in caveats]
        return Outcome(tuple(lines), 1 if after.total else 0)
    return Outcome((*lines, "SCOPE CLEAN."), 0)



# --- I/O boundary ----------------------------------------------------------
# Thin wrappers over privileged operations, kept free of logic so everything
# above stays testable without a printer, a spool or root.


def _head(path: Path, n: int = 32) -> str:  # pragma: no cover -- reason: a read
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



def _unlink_path(p: Path) -> None:  # pragma: no cover
    p.unlink()


def _resolve_path(p: Path) -> Path:  # pragma: no cover
    return p.resolve()


def delete(
    spool: str,
    entries: tuple[Entry, ...],
    roots: tuple[Path, ...],
    *,
    unlink: Callable[[Path], None] = _unlink_path,
    resolve: Callable[[Path], Path] = _resolve_path,
) -> tuple[int, int]:
    """Remove files, refusing anything outside `roots`. Returns (deleted, failed).

    A failed delete must be reported as a failed delete. Reporting it as
    "the files are still there" sends the user hunting a process that is
    recreating them, when in fact the removal never had permission.

    `unlink` and `resolve` are injectable because the mapping from error to
    outcome IS the logic of this function, and it was untestable while the whole
    thing carried `pragma: no cover`. Getting that mapping wrong is bug 5 in the
    test suite's header: a permission failure reported as files respawning. The
    two defaults above are the only part that touches the filesystem, and they
    have no branches, which is what a wrapper exempt from coverage should look
    like.
    """
    deleted = failed = 0
    for e in entries:
        target = Path(spool) / e.name
        # Resolve before checking: a symlinked TempDir, or an absolute name
        # replacing the join, both land outside the roots only after
        # resolution. Refusing here is the last line of defence -- the walk
        # should not have offered such a path in the first place.
        try:
            resolved = resolve(target)
        except (OSError, RuntimeError):
            failed += 1
            continue
        if not is_inside(resolved, roots):
            print(f"  REFUSED (outside the audited directory): {e.name} -> {resolved}")
            failed += 1
            continue
        try:
            unlink(target)
            deleted += 1
            continue
        except FileNotFoundError:
            deleted += 1
            continue
        except PermissionError:
            pass
        except OSError:
            # IsADirectoryError (a directory named d00085-001 matches DOCUMENT
            # and both listing paths return directory names), EROFS on a
            # read-only --spool, ELOOP, EBUSY. Any of these used to escape and
            # kill the purge mid-way, after files were already gone and before
            # any summary was printed.
            failed += 1
            continue
        # No `rm` fallback: it would run as the same uid with the same
        # directory permissions and fail identically. It was a leftover from
        # the removed escalation path and contradicted the documented "no
        # privilege-escalation path inside the tool".
        failed += 1
    return deleted, failed


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


def retention_state(conf: str) -> bool | None:  # pragma: no cover -- reason: read only, parse_retention decides
    """Is CUPS configured to keep job files? None if the config is unreadable.

    Nothing but the read lives here now. Read as bytes for the same reason
    disable_retention writes bytes: a printer config is not guaranteed UTF-8,
    and decoding it is a step that can fail or corrupt for no benefit when all
    that is wanted is a directive match.
    """
    try:
        body = Path(conf).read_bytes()
    except OSError:
        return None
    return parse_retention(body)


def _restart_cups() -> tuple[bool, str]:  # pragma: no cover -- reason: PENDING seams, runs a live init
    """Restart CUPS via whatever init this host has. Returns (ok, how)."""
    if shutil.which("systemctl"):
        rc = subprocess.run(["systemctl", "restart", "cups"], check=False)
        if rc.returncode == 0:
            return True, "systemctl"
    if shutil.which("service"):
        rc = subprocess.run(["service", "cups", "restart"], check=False)
        if rc.returncode == 0:
            return True, "service"
    return False, "no working init command"


def disable_retention(conf: str) -> tuple[bool, str]:  # pragma: no cover -- reason: PENDING seams, rewrites a live cupsd.conf as root
    """Set PreserveJobFiles No and restart CUPS. Returns (ok, detail).

    The path is passed as an argument, never interpolated into a shell string:
    this runs under sudo, and a conf path containing shell metacharacters would
    otherwise execute as root.
    """
    # Bytes end to end. Reading with errors="replace" and writing the result
    # back is a lossy round-trip of the machine's printer config: a Latin-1
    # comment or printer name came back as U+FFFD, so `--fix` silently corrupted
    # every non-UTF-8 byte in the file. Worse, the replacement character is
    # unencodable in an ASCII locale, and UnicodeEncodeError is a ValueError,
    # which `except OSError` does not catch -- cupsd.conf was left at 0 bytes
    # with an uncaught traceback and exit 1, the code reserved for "content you
    # care about is still there". Bytes cannot be mistranscoded and cannot raise
    # on encode. This file is a config, not text we need to interpret.
    try:
        lines = Path(conf).read_bytes().splitlines()
    except OSError as exc:
        return False, f"could not read {conf}: {exc.__class__.__name__}"

    out, replaced = [], False
    for line in lines:
        if re.match(rb"^\s*PreserveJobFiles\b", line, re.IGNORECASE):
            out.append(b"PreserveJobFiles No")
            replaced = True
        else:
            out.append(line)
    if not replaced:
        out.append(b"PreserveJobFiles No")
    body = b"\n".join(out) + b"\n"

    # The write truncates before it writes, so an interrupted write leaves the
    # daemon's config empty and nothing to restore from. Copy it aside first.
    backup = Path(f"{conf}.spool-audit.bak")
    # Never overwrite: running --fix twice used to replace the pre-fix original
    # with the already-fixed copy, leaving nothing to restore. Done in Python
    # rather than `cp -p -n`, which is a crash path when cp is not on PATH and
    # warns on coreutils >= 9.2.
    backup_ok = True
    if not backup.exists():
        try:
            shutil.copy2(conf, backup)
        except OSError:
            backup_ok = False
    if not backup_ok:
        # The copy exists precisely because the write truncates. Proceeding
        # without it means an interrupted write leaves an empty cupsd.conf and
        # nothing to restore from.
        return False, f"could not back up {conf}; refusing to rewrite it"
    # Written in Python rather than piped through `tee`, for the same reason the
    # backup above no longer shells out to `cp`: an absent tee raises
    # FileNotFoundError, which nothing catches and which exits 1 -- the code
    # this tool reserves for "content you care about is still there", so a
    # wrapper could not tell a crash from a finding. The script already runs as
    # root, so tee bought no privilege.
    #
    # Written to a temp file and renamed over the original, never truncated in
    # place: cupsd.conf belongs to a running daemon, and any failure mid-write
    # leaves it empty. os.replace is atomic within a directory, so the daemon
    # sees either the old file or the new one and never a partial one. The
    # backup above stays, because it covers the case this does not: a bad but
    # complete rewrite.
    try:
        src = os.stat(conf)
    except OSError as exc:
        return False, f"could not stat {conf} ({exc.__class__.__name__})"
    fd, tmp_name = tempfile.mkstemp(
        dir=str(Path(conf).parent), prefix=".spool-audit-", suffix=".tmp"
    )
    try:
        with os.fdopen(fd, "wb") as fh:
            fh.write(body)
            fh.flush()
            # The daemon is restarted immediately after this. Without fsync a
            # crash between rename and restart can leave the rename durable and
            # the contents not.
            os.fsync(fh.fileno())
        # mkstemp creates 0600 owned by the caller, and os.replace keeps the
        # REPLACEMENT's mode and owner, not the original's. Renaming without
        # this would quietly change cupsd.conf from its packaged root:lp 0640 --
        # a permissions change nobody asked for, made by a tool whose whole
        # purpose is not to damage what it touches.
        os.chmod(tmp_name, stat.S_IMODE(src.st_mode))
        try:
            os.chown(tmp_name, src.st_uid, src.st_gid)
        except OSError:
            now = os.stat(tmp_name)
            if (now.st_uid, now.st_gid) != (src.st_uid, src.st_gid):
                raise
        os.replace(tmp_name, conf)
    except OSError as exc:
        with contextlib.suppress(OSError):
            os.unlink(tmp_name)
        return False, (
            f"could not write {conf} ({exc.__class__.__name__}); "
            f"original untouched, backup at {backup}"
        )

    if not should_restart_cups(conf):
        # Do NOT restart the live daemon for a config it does not read.
        # `--fix --conf /tmp/x` restarted the machine's real cups.service and
        # reported success while /etc/cups/cupsd.conf was untouched, then
        # "verified" by re-reading the file it had just written.
        return True, f"wrote {conf} (not the system config, so cups was not restarted)"
    ok, how = _restart_cups()
    if not ok:
        return False, (
            f"config updated but CUPS was NOT restarted ({how}). "
            "The running daemon still has the old setting."
        )

    # Re-read from disk rather than trusting `body`: the point is to confirm what
    # the daemon will actually read. An unreadable file here is a failure, not a
    # pass -- returning True on an empty read would report a fix that may not be
    # on disk at all.
    try:
        verified = Path(conf).read_bytes()
    except OSError as exc:
        return False, (
            f"wrote and restarted, but could not re-read {conf} "
            f"({exc.__class__.__name__}) to confirm the setting"
        )
    for line in verified.splitlines():
        if re.match(rb"^\s*PreserveJobFiles\s+No\b", line, re.IGNORECASE):
            return True, f"restarted via {how}"
    return False, "config did not contain PreserveJobFiles No after writing"


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
    ap.add_argument("jobs", nargs="*", type=int, help="job ids to highlight or purge")
    ap.add_argument("--purge", action="store_true", help="delete retained files")
    ap.add_argument("--fix", action="store_true", help="stop CUPS retaining documents")
    ap.add_argument(
        "--include-unrecognised",
        action="store_true",
        help="also DELETE files that could not be identified (default: report only)",
    )
    ap.add_argument(
        "--include-control",
        action="store_true",
        help="also count/remove c<job> control files, which carry job titles",
    )
    ap.add_argument("--spool", default=DEFAULT_SPOOL, help=f"spool directory (default {DEFAULT_SPOOL})")
    ap.add_argument("--conf", default=DEFAULT_CONF, help=f"cupsd.conf path (default {DEFAULT_CONF})")
    args = ap.parse_args(argv)

    # --fix and --purge are independent and both may be requested.
    fix_code = 0
    if args.fix:
        outcome = fix_outcome(*disable_retention(args.conf), also_purging=args.purge)
        for line in outcome.lines:
            print(line)
        fix_code = outcome.code
        if fix_code and not args.purge:
            return fix_code

    jobs = frozenset(args.jobs)
    audit = classify(read_spool(args.spool), jobs, args.include_control)

    # The audited region is exactly the spool. Anything resolving outside it is
    # refused by delete(), which is what stops a symlinked TempDir taking a
    # purge out of the directory the user named.
    spool_root = _safe_resolve(Path(args.spool))
    allowed = (spool_root,) if spool_root is not None else ()

    if not audit.readable:
        for line in render(audit, jobs):
            print(line)
        return max(fix_code, 2)

    if args.purge:
        pre = purge_precheck(
            audit, jobs, roots_ok=spool_root is not None,
            include_unrecognised=args.include_unrecognised,
        )
        if pre is not None:
            for line in pre.lines:
                print(line)
            return max(fix_code, pre.code)

        victims = victims_for(audit, args.include_unrecognised)
        scope = f"job(s) {', '.join(str(j) for j in sorted(jobs))}" if jobs else "all retained files"
        print(f"Deleting {len(victims)} file(s) [{scope}]...")
        deleted, failed = delete(args.spool, victims, allowed)
        after = classify(read_spool(args.spool), jobs, args.include_control) if not failed else None
        post = purge_outcome(
            deleted, failed, after if (after and after.readable) else None, jobs,
            include_unrecognised=args.include_unrecognised,
        )
        for line in post.lines:
            print(line)
        # max(), not the post code alone: a --fix that failed must not be
        # erased by a purge that succeeded.
        return max(fix_code, post.code)

    for line in render(audit, jobs, retention_state(args.conf)):
        print(line)
    # --fix on its own used to return 0 without ever reading the spool, so
    # `--fix && echo SAFE` fired on a spool full of retained documents. The
    # exit code means one thing throughout: nothing is left.
    return max(fix_code, audit.exit_code)


if __name__ == "__main__":
    sys.exit(main())
