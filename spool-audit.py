#!/usr/bin/env python3
"""Audit and clear documents left behind in the CUPS print spool.

Printing sends the whole document through CUPS, and CUPS may keep a copy after
the job finishes. If you ever print a password, a recovery sheet or a key, that
copy outlives the paper, on a machine that may not be yours.

Usage:
    python spool-audit.py                    # report on everything
    python spool-audit.py 85 86              # report, highlighting those jobs
    python spool-audit.py --purge            # delete every retained document
    python spool-audit.py --fix              # stop CUPS retaining them at all
    python spool-audit.py --spool DIR        # audit a directory instead

Reading the spool needs root, so this normally runs under sudo.

WHY THIS EXISTS AS A TOOL
    The obvious one-liner is wrong in a way that reports danger as safety:

        sudo ls /var/spool/cups/ | grep -E 'd0*(85|86)' || echo CLEAN

    When sudo fails, ls prints nothing, grep matches nothing, and it announces
    CLEAN. A failed check and a clean result are indistinguishable. This tool
    treats "could not read" as its own outcome and never collapses it into a
    pass.

    The second version of that shell script had the opposite bug: it listed
    every retained document whether or not the jobs you asked about were among
    them, so a successful cleanup looked exactly like a failure. Targeted jobs
    and everything else are reported separately here, and the classification is
    a pure function so it can be tested without a printer.
"""

from __future__ import annotations

import argparse
import re
import subprocess
import sys
from dataclasses import dataclass
from enum import Enum
from pathlib import Path

DEFAULT_SPOOL = "/var/spool/cups"
DEFAULT_CONF = "/etc/cups/cupsd.conf"

# CUPS names control files c<jobid> and document files d<jobid>-<docnum>.
# Only the d-files hold what was actually printed.
DOCUMENT = re.compile(r"^d(\d+)-(\d+)$")


class Verdict(Enum):
    """The three distinct outcomes. Collapsing any two of these is the bug."""

    UNREADABLE = "unreadable"
    CLEAN = "clean"
    RETAINED = "retained"


@dataclass(frozen=True)
class Document:
    """One retained document file in the spool."""

    name: str
    job: int

    @property
    def is_document(self) -> bool:
        return True


@dataclass(frozen=True)
class Audit:
    """Result of classifying a spool listing. Pure data, no I/O, no printing."""

    verdict: Verdict
    targeted: tuple[Document, ...]
    others: tuple[Document, ...]

    @property
    def total(self) -> int:
        return len(self.targeted) + len(self.others)

    @property
    def targeted_are_gone(self) -> bool:
        """True when nothing you asked about is present.

        Deliberately independent of `verdict`: the jobs you care about can be
        gone while the spool still holds other people's documents. Reporting
        those two as one number is what made the shell version unreadable.
        """
        return not self.targeted


def parse_document(name: str) -> Document | None:
    """Return a Document for a CUPS document filename, else None."""
    m = DOCUMENT.match(name)
    if m is None:
        return None
    return Document(name=name, job=int(m.group(1)))


def classify(names: list[str], jobs: frozenset[int] = frozenset()) -> Audit:
    """Partition a spool listing into targeted and other retained documents.

    `names` is a directory listing. `jobs` are the job ids you asked about.
    Anything that is not a document file is ignored: control files are job
    history, which CUPS keeps by design and which leaks nothing.
    """
    docs = [d for d in (parse_document(n) for n in names) if d is not None]
    targeted = tuple(sorted((d for d in docs if d.job in jobs), key=lambda d: d.name))
    others = tuple(sorted((d for d in docs if d.job not in jobs), key=lambda d: d.name))
    verdict = Verdict.RETAINED if docs else Verdict.CLEAN
    return Audit(verdict=verdict, targeted=targeted, others=others)


def render(audit: Audit, jobs: frozenset[int]) -> list[str]:
    """Format an Audit for a human. Returns lines; printing is the caller's job."""
    if audit.verdict is Verdict.UNREADABLE:
        return [
            "COULD NOT READ THE SPOOL (needs root).",
            "Nothing is proven either way. This is NOT a clean result.",
        ]

    lines = [f"Spool holds {audit.total} retained document file(s).", ""]

    if jobs:
        wanted = ", ".join(str(j) for j in sorted(jobs))
        lines.append(f"JOBS YOU ASKED ABOUT ({wanted}):")
        if audit.targeted_are_gone:
            lines.append("  none present. Those documents are GONE.")
        else:
            lines += [f"  >>> {d.name}  STILL PRESENT" for d in audit.targeted]
            lines.append("  remove with: --purge")
        lines.append("")

    lines.append(f"OTHER RETAINED DOCUMENTS: {len(audit.others)}")
    lines += [f"  {d.name}" for d in audit.others[:20]]
    if len(audit.others) > 20:
        lines.append(f"  ... and {len(audit.others) - 20} more")

    lines.append("")
    if audit.verdict is Verdict.RETAINED:
        lines += [
            "VERDICT: this host RETAINS printed documents.",
            "         --fix stops that permanently; --purge clears what is there.",
        ]
    else:
        lines.append("VERDICT: spool is clean.")
    return lines


# --- I/O boundary ----------------------------------------------------------
# Thin wrappers over privileged operations. Kept free of logic so the domain
# above stays testable without a printer, a spool or root.


def read_spool(spool: str) -> list[str] | None:  # pragma: no cover
    """List the spool directory, or None if it cannot be read."""
    try:
        return sorted(p.name for p in Path(spool).iterdir())
    except PermissionError:
        proc = subprocess.run(
            ["sudo", "ls", "-1", spool], capture_output=True, text=True, check=False
        )
        if proc.returncode != 0:
            return None
        return sorted(line for line in proc.stdout.splitlines() if line)
    except OSError:
        return None


def delete(spool: str, docs: tuple[Document, ...]) -> None:  # pragma: no cover
    """Remove document files, falling back to sudo when unprivileged."""
    for d in docs:
        target = str(Path(spool) / d.name)
        try:
            Path(target).unlink()
        except PermissionError:
            subprocess.run(["sudo", "rm", "-f", target], check=False)
        except FileNotFoundError:
            pass


def disable_retention(conf: str) -> bool:  # pragma: no cover
    """Set PreserveJobFiles No and restart CUPS. True if it took."""
    script = (
        f"if grep -qiE '^ *PreserveJobFiles' {conf}; then "
        f"sed -i -E 's/^ *PreserveJobFiles.*/PreserveJobFiles No/I' {conf}; "
        f"else printf '\\nPreserveJobFiles No\\n' >> {conf}; fi"
    )
    if subprocess.run(["sudo", "sh", "-c", script], check=False).returncode != 0:
        return False
    subprocess.run(["sudo", "systemctl", "restart", "cups"], check=False)
    check = subprocess.run(
        ["sudo", "grep", "-iE", "^PreserveJobFiles", conf],
        capture_output=True,
        text=True,
        check=False,
    )
    return "no" in check.stdout.lower()


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("jobs", nargs="*", type=int, help="job ids to highlight")
    ap.add_argument("--purge", action="store_true", help="delete every retained document")
    ap.add_argument("--fix", action="store_true", help="stop CUPS retaining documents")
    ap.add_argument("--spool", default=DEFAULT_SPOOL, help=f"spool directory (default {DEFAULT_SPOOL})")
    ap.add_argument("--conf", default=DEFAULT_CONF, help=f"cupsd.conf path (default {DEFAULT_CONF})")
    args = ap.parse_args(argv)

    if args.fix:
        ok = disable_retention(args.conf)
        print("PreserveJobFiles set to No; cups restarted." if ok else "FAILED to change CUPS config.")
        print("Existing documents are untouched. Run --purge to clear them.")
        return 0 if ok else 1

    names = read_spool(args.spool)
    if names is None:
        for line in render(Audit(Verdict.UNREADABLE, (), ()), frozenset()):
            print(line)
        return 2

    jobs = frozenset(args.jobs)
    audit = classify(names, jobs)

    if args.purge:
        victims = audit.targeted + audit.others
        if not victims:
            print("Nothing to purge.")
            return 0
        print(f"Deleting {len(victims)} retained document file(s)...")
        delete(args.spool, victims)
        after = read_spool(args.spool)
        if after is None:
            print("Deleted, but the spool could not be re-read to confirm.")
            return 2
        left = classify(after, jobs).total
        print(f"Remaining document files: {left}")
        print("SPOOL CLEAN." if left == 0 else "STILL PRESENT -- something is recreating them.")
        return 0 if left == 0 else 1

    for line in render(audit, jobs):
        print(line)
    return 0 if audit.verdict is Verdict.CLEAN else 1


if __name__ == "__main__":
    sys.exit(main())
