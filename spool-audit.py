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
    Turning retention off and cancelling jobs is work CUPS already does
    correctly (`cancel -a -x` plus one config line), so this tool does not do
    it. The only removal is the residue removal above, built so the dangerous
    bugs have no code to live in rather than so they are checked for:

      - No file is ever written and no daemon is ever touched. Even a version
        that shells out to CUPS would risk acting on the LIVE daemon while
        pointed at some other --conf or --spool. Nothing
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
    should never survive a completed job. They do. In a clean Ubuntu 24.04
    container, stock cupsd.conf with no PreserveJobFiles directive present:

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
import sys

from spoolaudit.classify import classify, purgeable
from spoolaudit.config import retention_state
from spoolaudit.model import DEFAULT_CONF, DEFAULT_SPOOL
from spoolaudit.purge import delete_residue, purge_outcome
from spoolaudit.read import read_spool
from spoolaudit.render import render


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
