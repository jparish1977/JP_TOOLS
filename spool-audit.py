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
    tmp/*        CUPS TempDir, default /var/spool/cups/tmp, holds document
                 content during filtering. Auditing only the top level reports
                 CLEAN while readable document data sits one directory down.
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


class Verdict(Enum):
    """Distinct outcomes. Collapsing any two of these is the bug this prevents."""

    DENIED = "denied"
    MISSING = "missing"
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
class Listing:
    """Raw spool contents, or why they could not be read."""

    verdict: Verdict
    top: tuple[str, ...] = ()
    temp: tuple[str, ...] = ()


@dataclass(frozen=True)
class Audit:
    """Classified spool contents. Pure data: no I/O, no printing."""

    verdict: Verdict
    targeted: tuple[Entry, ...]
    others: tuple[Entry, ...]
    asked_for_jobs: bool = False
    artifacts: tuple[Entry, ...] = ()

    @property
    def total(self) -> int:
        return len(self.targeted) + len(self.others)

    @property
    def readable(self) -> bool:
        return self.verdict in (Verdict.CLEAN, Verdict.RETAINED)

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
        if not self.readable:
            return 2
        if self.asked_for_jobs:
            return 0 if self.targeted_are_gone else 1
        return 0 if self.verdict is Verdict.CLEAN else 1


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
    if listing.verdict in (Verdict.DENIED, Verdict.MISSING):
        return Audit(listing.verdict, (), (), asked_for_jobs=bool(jobs))

    entries = [e for e in (parse_entry(n, include_control) for n in listing.top) if e is not None]
    # Temp files carry document content but no recoverable job id, so they can
    # never be "targeted" by job number. They still count as retained data.
    entries += [
        Entry(name=f"{TEMP_SUBDIR}/{n}", kind=Kind.TEMP, job=None)
        for n in listing.temp
        if not ARTIFACT.match(n)
    ]
    artifacts = tuple(
        Entry(name=f"{TEMP_SUBDIR}/{n}", kind=Kind.ARTIFACT, job=None)
        for n in sorted(listing.temp)
        if ARTIFACT.match(n)
    )

    targeted = tuple(sorted((e for e in entries if e.job in jobs), key=lambda e: e.name))
    others = tuple(sorted((e for e in entries if e.job not in jobs), key=lambda e: e.name))
    verdict = Verdict.RETAINED if entries else Verdict.CLEAN
    return Audit(verdict, targeted, others, asked_for_jobs=bool(jobs), artifacts=artifacts)


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
            f"  {len(temp)} of these are in {TEMP_SUBDIR}/ (CUPS TempDir). They hold"
        )
        lines.append("  document content but carry no job id, so they cannot be")
        lines.append("  targeted by job number. Purge without job ids to remove them.")

    if audit.artifacts:
        lines.append("")
        lines.append(f"CUPS RUNTIME FILES (not document content, never purged): {len(audit.artifacts)}")
        lines += [f"  {e.name}" for e in audit.artifacts]

    lines.append("")
    if retention is True:
        lines.append("RETENTION: ON. CUPS is keeping documents. --fix stops that.")
    elif retention is False:
        note = " Files listed above predate the fix." if audit.total else ""
        lines.append(f"RETENTION: OFF (PreserveJobFiles No).{note}")
    else:
        lines.append("RETENTION: unknown (could not read cupsd.conf).")

    if audit.verdict is Verdict.RETAINED:
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


def _sudo_ls(path: str) -> tuple[Verdict, tuple[str, ...]]:  # pragma: no cover
    proc = subprocess.run(
        _priv(["ls", "-1", path]), capture_output=True, text=True, check=False
    )
    if proc.returncode == 0:
        return Verdict.CLEAN, tuple(line for line in proc.stdout.splitlines() if line)
    if "No such file" in proc.stderr:
        return Verdict.MISSING, ()
    return Verdict.DENIED, ()


def read_spool(spool: str) -> Listing:  # pragma: no cover
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
        return Listing(Verdict.MISSING)
    except OSError:
        return Listing(Verdict.DENIED)

    temp: tuple[str, ...] = ()
    if TEMP_SUBDIR in top:
        tdir = root / TEMP_SUBDIR
        try:
            temp = tuple(sorted(p.name for p in tdir.iterdir() if p.is_file()))
        except PermissionError:
            tverdict, temp = _sudo_ls(str(tdir))
            if tverdict is not Verdict.CLEAN:
                temp = ()
        except OSError:
            temp = ()

    return Listing(verdict, tuple(n for n in top if n != TEMP_SUBDIR), temp)


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
        if rc == 0 and not target.exists():
            deleted += 1
        else:
            failed += 1
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
    audit = classify(read_spool(args.spool), jobs, args.include_control)

    if not audit.readable:
        for line in render(audit, jobs):
            print(line)
        return 2

    retention = retention_state(args.conf)

    if args.purge:
        victims = victims_for(audit)
        if not victims:
            print("Nothing to purge.")
            return 0
        scope = f"job(s) {', '.join(str(j) for j in sorted(jobs))}" if jobs else "all retained files"
        print(f"Deleting {len(victims)} file(s) [{scope}]...")
        deleted, failed = delete(args.spool, victims)
        if failed:
            print(f"DELETE FAILED for {failed} file(s); {deleted} removed.")
            print("This is a permissions problem, not files being recreated.")
            return 1
        after = classify(read_spool(args.spool), jobs, args.include_control)
        if not after.readable:
            print(f"{deleted} removed, but the spool could not be re-read to confirm.")
            return 2
        remaining = len(victims_for(after))
        print(f"{deleted} removed. Remaining in scope: {remaining}")
        print("SCOPE CLEAN." if remaining == 0 else "STILL PRESENT after a successful delete.")
        return 0 if remaining == 0 else 1

    for line in render(audit, jobs, retention):
        print(line)
    return audit.exit_code


if __name__ == "__main__":
    sys.exit(main())
