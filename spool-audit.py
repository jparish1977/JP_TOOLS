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

# Passed to `find -printf`, which interprets the escapes itself. The backslashes
# MUST stay literal: writing "%y\t%l\t%P\0" in Python embeds a real NUL byte,
# and argv strings are NUL-terminated, so subprocess rejects it outright with
# ValueError: embedded null byte. That crashed every sudo TempDir listing, and
# no amount of running the tool as root would have shown it.
FIND_FORMAT = "%y\\t%l\\t%P\\0"

# Names only, NUL-separated, for the top-level listing. `ls -1` was used here
# and splits on newlines: a spool file named "evil\nd00099-001" came back as
# two entries, the real one (holding content) matching no regex and vanishing
# from the audit while a phantom was reported in its place. Same bug as the
# TempDir path had, on the more commonly taken path.
FIND_NAMES = "%P\\0"

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
        if self.asked_for_jobs:
            return 0 if self.targeted_are_gone else 1
        return 0 if self.verdict is Verdict.CLEAN else 1


def _safe_resolve(p: Path) -> Path | None:  # pragma: no cover
    """resolve() or None. It touches the filesystem and CAN raise.

    Same class as Path.exists() not swallowing EACCES: an unreadable component
    anywhere in the path makes resolve() raise, and `--spool /proc/1/root/x`
    crashed with a traceback. The containment roots are the safety boundary, so
    a root that cannot be resolved must stop a purge rather than be guessed at.
    """
    try:
        return p.resolve()
    except OSError:
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


def is_harmless_temp(f: TempFile) -> bool:
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
    if ARTIFACT.match(f.name.rsplit("/", 1)[-1]):
        return True
    # Unknown content. The sudo listing path cannot stat or read children, so
    # it yields size -1 and an empty head -- and every rule below reasons from
    # content or size. Without this, the .cache/ exemption fired on a document
    # the root path counted, so `tmp/.cache/leak.ps` was reported as leaked
    # content when run as root and dismissed as a cache when run via sudo.
    # Nothing may be excused by location when its content was never read.
    if f.size < 0 and not f.head:
        return False
    # CUPS runs filters with HOME pointed at TempDir, so a filter's XDG cache
    # lands in tmp/.cache/. Measured in a container with a real filter chain:
    # 24 fontconfig cache files, which the recursive walk reported as possible
    # document content and --purge deleted. They are caches by definition of
    # where they are. Twenty-four false positives is noise that trains you to
    # skim the report, which is the failure this tool exists to avoid.
    if f.name.startswith(".cache/") or "/.cache/" in f.name:
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
        return Audit(listing.verdict, (), (), asked_for_jobs=bool(jobs))

    entries = [e for e in (parse_entry(n, include_control) for n in listing.top) if e is not None]
    # Unrecognised top-level files go through exactly the same content check as
    # TempDir files, so a PostScript .bak is counted and an empty stray file is
    # not.
    extras = sorted(listing.extra, key=lambda f: f.name)
    entries += [
        Entry(name=f.name, kind=Kind.UNRECOGNISED, job=None)
        for f in extras
        if not is_harmless_temp(f)
    ]
    # Temp files carry document content but no recoverable job id, so they can
    # never be "targeted" by job number. They still count as retained data.
    temps = sorted(listing.temp, key=lambda f: f.name)
    entries += [
        Entry(name=f"{listing.temp_label}/{f.name}", kind=Kind.TEMP, job=None)
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
            if is_harmless_temp(f)
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


def victims_for(audit: Audit) -> tuple[Entry, ...]:
    """What --purge should delete.

    If job ids were given, only those. Deleting everything when the user named
    specific jobs destroys other people's documents on a shared printer, which
    is precisely the machine this tool is aimed at.
    """
    return audit.targeted if audit.asked_for_jobs else audit.targeted + audit.others


def render(audit: Audit, jobs: frozenset[int], retention: bool | None = None) -> list[str]:
    """Format an Audit for a human. Returns lines; printing is the caller's job.

    `retention` is whether CUPS is currently configured to keep documents, read
    from cupsd.conf. It is deliberately a separate input: inferring it from
    "are there files here" told Joe the host still retained data immediately
    after a successful --fix had turned retention off, because leftover
    documents from before the fix were still on disk.
    """
    if audit.verdict is Verdict.DENIED:
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
            lines += [f"  >>> {e.name}  STILL PRESENT" for e in audit.targeted]
            lines.append("  remove ONLY these with: --purge")
        lines.append("")

    temp = [e for e in audit.others if e.kind is Kind.TEMP]
    unknown = [e for e in audit.others if e.kind is Kind.UNRECOGNISED]
    rest = [e for e in audit.others if e.kind not in (Kind.TEMP, Kind.UNRECOGNISED)]

    lines.append(f"OTHER RETAINED FILES: {len(audit.others)}")
    shown = (rest + unknown + temp)[:20]
    lines += [f"  {e.name}" for e in shown]
    if len(audit.others) > 20:
        lines.append(f"  ... and {len(audit.others) - 20} more")
    if unknown:
        lines.append("")
        lines.append(
            f"  {len(unknown)} of these are top-level files matching no known"
        )
        lines.append("  spool pattern. Their content looks like print data.")
    if temp:
        lines.append("")
        lines.append(
            f"  {len(temp)} of these are in the CUPS TempDir, unrecognised."
        )
        lines.append("  They may be document content mid-filter. They carry no job id,")
        lines.append("  so they cannot be targeted by number. Purge without job ids.")

    if audit.artifacts:
        lines.append("")
        lines.append(f"CUPS RUNTIME FILES (not document content, never purged): {len(audit.artifacts)}")
        lines += [f"  {e.name}" for e in audit.artifacts]

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


# --- I/O boundary ----------------------------------------------------------
# Thin wrappers over privileged operations, kept free of logic so everything
# above stays testable without a printer, a spool or root.


def _head(path: Path, n: int = 32) -> str:  # pragma: no cover
    """First bytes of a file, decoded lossily. Used only to recognise known
    harmless formats such as PPDs. Never printed, never logged."""
    try:
        with path.open("rb") as fh:
            return fh.read(n).decode("utf-8", "replace")
    except OSError:
        return ""


def _walk_temp(  # pragma: no cover
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
            try:
                found.append(TempFile(name=name, size=st.st_size, head=_head(f)))
            except OSError:
                unexamined.append(f"{label}/{name} (vanished while reading)")
        else:
            unexamined.append(note)


def read_spool(spool: str) -> Listing:  # pragma: no cover
    """List the spool and its TempDir. Direct reads only.

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
        top = tuple(sorted(p.name for p in root.iterdir()))
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
        return Listing(Verdict.DENIED, (f"({exc.__class__.__name__})",))

    temp: tuple[TempFile, ...] = ()
    unexamined: list[str] = []
    tdir: Path | None = root / TEMP_SUBDIR
    # A TempDir that is ITSELF a symlink is never followed: `<spool>/tmp ->
    # /anywhere` made --purge delete outside the audited spool and print
    # SCOPE CLEAN.
    try:
        if tdir.is_symlink():
            unexamined.append(
                f"{TEMP_SUBDIR} -> {os.readlink(tdir)} (TempDir is a symlink, NOT followed)"
            )
            tdir = None
    except OSError:
        pass

    try:
        if tdir is None:
            raise FileNotFoundError  # symlinked TempDir, already recorded
        # Path.exists() does NOT swallow EACCES, so probing it outside this try
        # crashed with a traceback on a real 0710 spool.
        next(tdir.iterdir(), None)
        found: list[TempFile] = []
        _walk_temp(tdir, TEMP_SUBDIR, "", found, unexamined)
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

    # Anything at the top level that is neither a document, a control file nor
    # the TempDir. Read the same way TempDir files are, so a document copied to
    # d00085-001.bak is counted rather than dropped; `ls` would have shown it.
    extra: list[TempFile] = []
    for n in top:
        if n == TEMP_SUBDIR or DOCUMENT.match(n) or CONTROL.match(n):
            continue
        f = root / n
        try:
            st = f.lstat()
        except OSError:
            unexamined.append(f"{n} (vanished while reading)")
            continue
        if stat.S_ISLNK(st.st_mode) or not stat.S_ISREG(st.st_mode):
            note = temp_child_note(
                ".", n,
                is_symlink=stat.S_ISLNK(st.st_mode),
                is_dir=stat.S_ISDIR(st.st_mode),
                is_regular=False,
            )
            unexamined.append((note or f"{n} (not examined)").replace("./", "", 1))
            continue
        try:
            extra.append(TempFile(name=n, size=st.st_size, head=_head(f)))
        except OSError:
            unexamined.append(f"{n} (vanished while reading)")

    return Listing(
        verdict,
        tuple(n for n in top if n != TEMP_SUBDIR),
        temp,
        tuple(unexamined),
        TEMP_SUBDIR,
        tuple(extra),
    )



def delete(  # pragma: no cover
    spool: str, entries: tuple[Entry, ...], roots: tuple[Path, ...]
) -> tuple[int, int]:
    """Remove files, refusing anything outside `roots`. Returns (deleted, failed).

    A failed delete must be reported as a failed delete. Reporting it as
    "the files are still there" sends the user hunting a process that is
    recreating them, when in fact the removal never had permission.
    """
    deleted = failed = 0
    for e in entries:
        target = Path(spool) / e.name
        # Resolve before checking: a symlinked TempDir, or an absolute name
        # replacing the join, both land outside the roots only after
        # resolution. Refusing here is the last line of defence -- the walk
        # should not have offered such a path in the first place.
        try:
            resolved = target.resolve()
        except OSError:
            failed += 1
            continue
        if not is_inside(resolved, roots):
            print(f"  REFUSED (outside the audited directory): {e.name} -> {resolved}")
            failed += 1
            continue
        try:
            target.unlink()
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
        rc = subprocess.run(["rm", "-f", str(target)], check=False).returncode
        if rc != 0:
            failed += 1
            continue
        # Reaching the sudo fallback means unlink() already hit EACCES, so the
        # invoker cannot stat children either and target.exists() RAISES rather
        # than returning False. Letting that escape aborted a purge mid-way
        # with a traceback and no summary of what had been removed.
        try:
            gone = not target.exists()
        except OSError:
            gone = True  # rm reported success and we cannot see the file
        deleted += 1 if gone else 0
        failed += 0 if gone else 1
    return deleted, failed


def retention_state(conf: str) -> bool | None:  # pragma: no cover
    """Is CUPS configured to keep job files? None if the config is unreadable."""
    read = subprocess.run(["cat", conf], capture_output=True, text=True, check=False)
    if read.returncode != 0:
        return None
    # cupsd honours the LAST matching directive. Returning on the first made a
    # hand-edited config with "No" followed by "Yes" report RETENTION: OFF on a
    # host that was retaining documents -- danger reported as safety.
    setting: str | None = None
    for line in read.stdout.splitlines():
        m = re.match(r"^\s*PreserveJobFiles\s+(\S+)", line, re.IGNORECASE)
        if m:
            setting = m.group(1).lower()
    if setting is not None:
        return setting not in ("no", "off", "false", "0")
    # Unset means the compiled default, which on the hosts measured here keeps
    # documents. Reporting "off" on an absent directive would be a guess in the
    # dangerous direction.
    return True


def _restart_cups() -> tuple[bool, str]:  # pragma: no cover
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


def disable_retention(conf: str) -> tuple[bool, str]:  # pragma: no cover
    """Set PreserveJobFiles No and restart CUPS. Returns (ok, detail).

    The path is passed as an argument, never interpolated into a shell string:
    this runs under sudo, and a conf path containing shell metacharacters would
    otherwise execute as root.
    """
    read = subprocess.run(["cat", conf], capture_output=True, text=True, check=False)
    if read.returncode != 0:
        return False, f"could not read {conf}"

    lines = read.stdout.splitlines()
    out, replaced = [], False
    for line in lines:
        if re.match(r"^\s*PreserveJobFiles\b", line, re.IGNORECASE):
            out.append("PreserveJobFiles No")
            replaced = True
        else:
            out.append(line)
    if not replaced:
        out.append("PreserveJobFiles No")
    body = "\n".join(out) + "\n"

    # tee opens with O_TRUNC before writing, so an interrupted write leaves the
    # daemon's config empty and nothing to restore from. Copy it aside first.
    backup = f"{conf}.spool-audit.bak"
    saved = subprocess.run(["cp", "-p", conf, backup], check=False)
    write = subprocess.run(
        ["tee", conf], input=body, capture_output=True, text=True, check=False
    )
    if write.returncode != 0 and saved.returncode == 0:
        return False, f"could not write {conf}; original preserved at {backup}"
    if write.returncode != 0:
        return False, f"could not write {conf}"

    ok, how = _restart_cups()
    if not ok:
        return False, (
            f"config updated but CUPS was NOT restarted ({how}). "
            "The running daemon still has the old setting."
        )

    verify = subprocess.run(["cat", conf], capture_output=True, text=True, check=False)
    for line in verify.stdout.splitlines():
        if re.match(r"^\s*PreserveJobFiles\s+No\b", line, re.IGNORECASE):
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
        "--include-control",
        action="store_true",
        help="also count/remove c<job> control files, which carry job titles",
    )
    ap.add_argument("--spool", default=DEFAULT_SPOOL, help=f"spool directory (default {DEFAULT_SPOOL})")
    ap.add_argument("--conf", default=DEFAULT_CONF, help=f"cupsd.conf path (default {DEFAULT_CONF})")
    args = ap.parse_args(argv)

    # --fix and --purge are independent and both may be requested. Returning
    # after --fix silently dropped the purge and still exited 0.
    if args.fix:
        ok, detail = disable_retention(args.conf)
        print(f"PreserveJobFiles set to No; {detail}." if ok else f"FAILED: {detail}")
        if not ok and not args.purge:
            return 1
        if not ok:
            # The config may be written and only the restart failed. Returning
            # here dropped the purge -- the same early-return this block was
            # rewritten to remove, in the failure branch instead of the success
            # one. The documents on disk are still worth clearing.
            print("Continuing to --purge anyway; files on disk are unaffected by the restart.")
        elif not args.purge:
            print("Existing files are untouched. Run --purge to clear them.")
            return 0

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
        return 2

    if args.purge:
        if spool_root is None:
            print("REFUSING TO PURGE: the audited path could not be resolved,")
            print("  so nothing can be proven to be inside it.")
            return 2
        victims = victims_for(audit)
        if not victims:
            if audit.uncounted_control:
                print(
                    f"Nothing to purge, but {audit.uncounted_control} control file(s) "
                    "remain, carrying the job title. Use --include-control."
                )
            if audit.unexamined:
                print(f"Nothing to purge, but {len(audit.unexamined)} area(s) could not be examined:")
                for u in audit.unexamined:
                    print(f"  {u}")
                print("This is NOT a clean result.")
                return 2
            if not audit.uncounted_control:
                print("Nothing to purge.")
            return 0
        scope = f"job(s) {', '.join(str(j) for j in sorted(jobs))}" if jobs else "all retained files"
        print(f"Deleting {len(victims)} file(s) [{scope}]...")
        deleted, failed = delete(args.spool, victims, allowed)
        if failed:
            print(f"DELETE FAILED for {failed} file(s); {deleted} removed.")
            # Do not name a cause. Permissions is the common one, but a
            # directory matching the document pattern, a read-only mount and a
            # symlink loop all land here, and asserting the wrong cause sends
            # the user somewhere useless -- which is the mistake this line was
            # written to fix in the first place.
            print("Not files being recreated: the removals themselves failed.")
            return 1
        after = classify(read_spool(args.spool), jobs, args.include_control)
        if not after.readable:
            print(f"{deleted} removed, but the spool could not be re-read to confirm.")
            return 2
        remaining = len(victims_for(after))
        print(f"{deleted} removed. Remaining in scope: {remaining}")
        if after.unexamined:
            # The re-read goes through the same path, so an unreadable TempDir
            # would otherwise be counted as zero and announced as clean.
            print(f"NOT CLEAN: {len(after.unexamined)} area(s) could not be examined:")
            for u in after.unexamined:
                print(f"  {u}")
            return 2
        if remaining:
            print("STILL PRESENT after a successful delete.")
            return 1
        caveats = []
        if after.uncounted_control:
            caveats.append(
                f"{after.uncounted_control} control file(s) were not counted or "
                "removed; they carry the job title. Use --include-control."
            )
        # Files with no job id cannot be targeted by job number, so a scoped
        # purge leaves them -- including ones this tool classified as print
        # data. Saying only "SCOPE CLEAN" made `85 --purge && echo SAFE` fire
        # over a d00085-001.bak it had just called a PDF.
        unattributable = [e for e in after.others if e.job is None]
        if jobs and unattributable:
            caveats.append(
                f"{len(unattributable)} file(s) carry no job id and cannot be purged "
                "by number. Re-run --purge without job ids to clear them."
            )
        if caveats:
            print("SCOPE CLEAN for the jobs named, but:")
            for c in caveats:
                print(f"  {c}")
            return 0
        print("SCOPE CLEAN.")
        return 0

    for line in render(audit, jobs, retention_state(args.conf)):
        print(line)
    return audit.exit_code


if __name__ == "__main__":
    sys.exit(main())
