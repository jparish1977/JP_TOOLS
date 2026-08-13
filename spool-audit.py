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

Reading the spool needs root, so this normally runs under sudo.

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
import os
import re
import shutil
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
    # How to name the temp directory in output. Defaults to CUPS's "tmp", but
    # --temp can point elsewhere, and reporting a path that does not exist is
    # the tool telling you something false about where your data is.
    temp_label: str = TEMP_SUBDIR


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
    # Temp files carry document content but no recoverable job id, so they can
    # never be "targeted" by job number. They still count as retained data.
    temps = sorted(listing.temp, key=lambda f: f.name)
    entries += [
        Entry(name=f"{listing.temp_label}/{f.name}", kind=Kind.TEMP, job=None)
        for f in temps
        if not is_harmless_temp(f)
    ]
    artifacts = tuple(
        Entry(name=f"{listing.temp_label}/{f.name}", kind=Kind.ARTIFACT, job=None)
        for f in temps
        if is_harmless_temp(f)
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
    rest = [e for e in audit.others if e.kind is not Kind.TEMP]

    lines.append(f"OTHER RETAINED FILES: {len(audit.others)}")
    shown = (rest + temp)[:20]
    lines += [f"  {e.name}" for e in shown]
    if len(audit.others) > 20:
        lines.append(f"  ... and {len(audit.others) - 20} more")
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


def _priv(argv: list[str]) -> list[str]:  # pragma: no cover
    """Prefix with sudo only when we are not root AND sudo exists.

    Unconditionally shelling out to sudo crashed with FileNotFoundError on any
    system without it -- every container, every minimal image, and the case
    where the tool is already running as root, which is how it is normally
    used. Found by running real CUPS in a container on 2026-08-12; neither the
    fixture nor the real machine could show it, because both had sudo.
    """
    if os.geteuid() != 0 and shutil.which("sudo"):
        return ["sudo", "-n", *argv]
    return argv


def configured_tempdir(files_conf: str) -> str | None:  # pragma: no cover
    """TempDir from cups-files.conf, or None if unset or unreadable.

    TEMP_SUBDIR is only CUPS's default. A host that points TempDir elsewhere
    was audited by looking at a directory CUPS does not use, and the tool then
    reported "spool is clean" having never examined the one that holds document
    content mid-filter. An unperformed check must not read as a pass.
    """
    read = subprocess.run(_priv(["cat", files_conf]), capture_output=True, text=True, check=False)
    if read.returncode != 0:
        return None
    for line in read.stdout.splitlines():
        m = re.match(r"^\s*TempDir\s+(\S+)", line, re.IGNORECASE)
        if m:
            return m.group(1)
    return None


def _head(path: Path, n: int = 32) -> str:  # pragma: no cover
    """First bytes of a file, decoded lossily. Used only to recognise known
    harmless formats such as PPDs. Never printed, never logged."""
    try:
        with path.open("rb") as fh:
            return fh.read(n).decode("utf-8", "replace")
    except OSError:
        return ""


def _sudo_ls(path: str, files_only: bool = False) -> tuple[Verdict, tuple[str, ...]]:  # pragma: no cover
    """List a directory, optionally regular files only.

    `files_only` matters: the direct-read path filters with `p.is_file()`, so
    without the same filter here the two paths disagree about the same spool.
    Measured 2026-08-12 -- a real filter chain creates `tmp/.cache`, a
    DIRECTORY, which `ls -1` would have reported as leaked document content
    whenever the tool escalated through sudo rather than running as root.
    """
    argv = (
        # Recursive, relative paths, and the type letter so symlinks and
        # non-regular files are classified rather than silently dropped. The
        # earlier -maxdepth 1 -printf %f disagreed with the recursive direct
        # walk and hid nested documents in neither temp nor unexamined, and
        # basenames alone would collide across directories and make delete()
        # build the wrong path.
        ["find", path, "-mindepth", "1", "-maxdepth", "8", "-printf", "%y\t%l\t%P\n"]
        if files_only
        else ["ls", "-1", path]
    )
    proc = subprocess.run(_priv(argv), capture_output=True, text=True, check=False)
    if proc.returncode == 0:
        return Verdict.CLEAN, tuple(line for line in proc.stdout.splitlines() if line)
    if "No such file" in proc.stderr:
        return Verdict.MISSING, ()
    return Verdict.DENIED, ()


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
    if depth > 8:
        unexamined.append(f"{label}/{prefix} (nested deeper than 8, not examined)")
        return
    try:
        children = sorted(directory.iterdir())
    except OSError as exc:
        unexamined.append(f"{label}/{prefix} (unreadable: {exc.__class__.__name__})")
        return

    for f in children:
        link = f.is_symlink()
        try:
            target = os.readlink(f) if link else "?"
        except OSError:
            target = "?"
        name = f"{prefix}{f.name}"
        if not link and f.is_dir():
            _walk_temp(f, label, f"{name}/", found, unexamined, depth + 1)
            continue
        note = temp_child_note(
            label, name, is_symlink=link, is_dir=False,
            is_regular=not link and f.is_file(), target=target,
        )
        if note is None:
            try:
                found.append(TempFile(name=name, size=f.stat().st_size, head=_head(f)))
            except OSError:
                # A live spool deletes temp files continuously. Letting this
                # escape discarded every file already collected and reported
                # the whole TempDir unreadable -- losing the audit on exactly
                # the busy machine worth auditing.
                unexamined.append(f"{label}/{name} (vanished while reading)")
        else:
            unexamined.append(note)


def read_spool(spool: str, temp_dir: str | None = None) -> Listing:  # pragma: no cover
    """List the spool and its TempDir, distinguishing denied from missing."""
    root = Path(spool)
    try:
        top = tuple(sorted(p.name for p in root.iterdir()))
        verdict = Verdict.CLEAN
    except PermissionError:
        verdict, top = _sudo_ls(spool)
        if verdict in (Verdict.DENIED, Verdict.MISSING):
            return Listing(verdict)
    except FileNotFoundError:
        return Listing(Verdict.MISSING)
    except NotADirectoryError:
        return Listing(Verdict.NOT_A_DIRECTORY)
    except OSError:
        return Listing(Verdict.DENIED)

    temp: tuple[TempFile, ...] = ()
    unexamined: list[str] = []
    tdir = Path(temp_dir) if temp_dir else root / TEMP_SUBDIR
    label = TEMP_SUBDIR if tdir == root / TEMP_SUBDIR else str(tdir)
    # If TempDir is elsewhere, <spool>/tmp is stripped from the top-level
    # listing but never walked, so documents left there vanished from the audit
    # entirely and the tool reported clean. Record it rather than drop it.
    if tdir != root / TEMP_SUBDIR and TEMP_SUBDIR in top:
        unexamined.append(
            f"{TEMP_SUBDIR}/ (present but TempDir points at {tdir}; not examined)"
        )
    if True:
        try:
            # Path.exists() does NOT swallow EACCES, so probing it outside this
            # try crashed the tool with a traceback on a real 0710 spool and
            # made the sudo escalation below unreachable dead code.
            next(tdir.iterdir(), None)
            found: list[TempFile] = []
            _walk_temp(tdir, label, "", found, unexamined)
            temp = tuple(found)
        except PermissionError:
            # Names only through this path. Size and header are unknown, which
            # is_harmless_temp() treats as "possibly document content" rather
            # than assuming the safe answer.
            tverdict, rows = _sudo_ls(str(tdir), files_only=True)
            if tverdict is Verdict.CLEAN:
                collected = []
                for row in rows:
                    kind, target, name = (row.split("\t", 2) + ["", ""])[:3]
                    if kind == "d":
                        continue
                    note = temp_child_note(
                        label, name,
                        is_symlink=kind == "l",
                        is_dir=False,
                        is_regular=kind == "f",
                        target=target or "?",
                    )
                    if note is None:
                        collected.append(TempFile(name=name, size=-1, head=""))
                    else:
                        unexamined.append(note)
                temp = tuple(collected)
            else:
                # Do NOT fall through with an empty tuple. That turned an
                # unreadable TempDir into an empty one and printed
                # "VERDICT: spool is clean" over a readable document.
                unexamined.append(f"{label}/ (permission denied)")
        except OSError as exc:
            unexamined.append(f"{label}/ (unreadable: {exc.__class__.__name__})")

    return Listing(
        verdict,
        tuple(n for n in top if n != TEMP_SUBDIR),
        temp,
        tuple(unexamined),
        label,
    )


def delete(spool: str, entries: tuple[Entry, ...]) -> tuple[int, int]:  # pragma: no cover
    """Remove files. Returns (deleted, failed).

    A failed delete must be reported as a failed delete. Reporting it as
    "the files are still there" sends the user hunting a process that is
    recreating them, when in fact the removal never had permission.
    """
    deleted = failed = 0
    for e in entries:
        target = Path(spool) / e.name
        try:
            target.unlink()
            deleted += 1
            continue
        except FileNotFoundError:
            deleted += 1
            continue
        except PermissionError:
            pass
        rc = subprocess.run(_priv(["rm", "-f", str(target)]), check=False).returncode
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
    read = subprocess.run(_priv(["cat", conf]), capture_output=True, text=True, check=False)
    if read.returncode != 0:
        return None
    for line in read.stdout.splitlines():
        m = re.match(r"^\s*PreserveJobFiles\s+(\S+)", line, re.IGNORECASE)
        if m:
            return m.group(1).lower() not in ("no", "off", "false", "0")
    # Unset means the compiled default, which on the hosts measured here keeps
    # documents. Reporting "off" on an absent directive would be a guess in the
    # dangerous direction.
    return True


def _restart_cups() -> tuple[bool, str]:  # pragma: no cover
    """Restart CUPS via whatever init this host has. Returns (ok, how)."""
    if shutil.which("systemctl"):
        rc = subprocess.run(_priv(["systemctl", "restart", "cups"]), check=False)
        if rc.returncode == 0:
            return True, "systemctl"
    if shutil.which("service"):
        rc = subprocess.run(_priv(["service", "cups", "restart"]), check=False)
        if rc.returncode == 0:
            return True, "service"
    return False, "no working init command"


def disable_retention(conf: str) -> tuple[bool, str]:  # pragma: no cover
    """Set PreserveJobFiles No and restart CUPS. Returns (ok, detail).

    The path is passed as an argument, never interpolated into a shell string:
    this runs under sudo, and a conf path containing shell metacharacters would
    otherwise execute as root.
    """
    read = subprocess.run(_priv(["cat", conf]), capture_output=True, text=True, check=False)
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

    write = subprocess.run(
        _priv(["tee", conf]), input=body, capture_output=True, text=True, check=False
    )
    if write.returncode != 0:
        return False, f"could not write {conf}"

    ok, how = _restart_cups()
    if not ok:
        return False, (
            f"config updated but CUPS was NOT restarted ({how}). "
            "The running daemon still has the old setting."
        )

    verify = subprocess.run(_priv(["cat", conf]), capture_output=True, text=True, check=False)
    for line in verify.stdout.splitlines():
        if re.match(r"^\s*PreserveJobFiles\s+No\b", line, re.IGNORECASE):
            return True, f"restarted via {how}"
    return False, "config did not contain PreserveJobFiles No after writing"


def main(argv: list[str] | None = None) -> int:
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
    ap.add_argument("--temp", default=None,
                    help="TempDir to audit; default is TempDir from cups-files.conf, else <spool>/tmp")
    args = ap.parse_args(argv)

    # --fix and --purge are independent and both may be requested. Returning
    # after --fix silently dropped the purge and still exited 0.
    if args.fix:
        ok, detail = disable_retention(args.conf)
        print(f"PreserveJobFiles set to No; {detail}." if ok else f"FAILED: {detail}")
        if not ok:
            return 1
        if not args.purge:
            print("Existing files are untouched. Run --purge to clear them.")
            return 0

    jobs = frozenset(args.jobs)
    # Only consult the system cups-files.conf when auditing the system spool.
    # Otherwise `--spool /mnt/backup --purge` picked up an absolute TempDir from
    # the live config, and Path("/mnt/backup") / "/var/spool/cups/tmp/x"
    # resolves to the LIVE path -- reporting on, and deleting from, a directory
    # the user did not name.
    if args.temp:
        temp_dir: str | None = args.temp
    elif args.spool == DEFAULT_SPOOL:
        temp_dir = configured_tempdir(str(Path(args.conf).parent / "cups-files.conf"))
    else:
        temp_dir = None
    audit = classify(read_spool(args.spool, temp_dir), jobs, args.include_control)

    if not audit.readable:
        for line in render(audit, jobs):
            print(line)
        return 2

    if args.purge:
        victims = victims_for(audit)
        if not victims:
            if audit.unexamined:
                print(f"Nothing to purge, but {len(audit.unexamined)} area(s) could not be examined:")
                for u in audit.unexamined:
                    print(f"  {u}")
                print("This is NOT a clean result.")
                return 2
            print("Nothing to purge.")
            return 0
        scope = f"job(s) {', '.join(str(j) for j in sorted(jobs))}" if jobs else "all retained files"
        print(f"Deleting {len(victims)} file(s) [{scope}]...")
        deleted, failed = delete(args.spool, victims)
        if failed:
            print(f"DELETE FAILED for {failed} file(s); {deleted} removed.")
            print("This is a permissions problem, not files being recreated.")
            return 1
        after = classify(read_spool(args.spool, temp_dir), jobs, args.include_control)
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
        if after.uncounted_control:
            print(
                f"SCOPE CLEAN, but {after.uncounted_control} control file(s) were not "
                "counted or removed."
            )
            print("They carry the job title. Use --include-control to clear them too.")
            return 0
        print("SCOPE CLEAN.")
        return 0

    for line in render(audit, jobs, retention_state(args.conf)):
        print(line)
    return audit.exit_code


if __name__ == "__main__":
    sys.exit(main())
