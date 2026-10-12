#!/usr/bin/env python
"""
JP_TOOLS/tests/test_spool_audit_config.py
spool-audit: whether cupsd.conf keeps job files. Retention is read from the
config, never inferred from files being present; the last directive wins; a
config path that is a fifo cannot block the run.

    python tests/test_spool_audit_config.py
"""

import errno
import os
import pathlib
import signal
import sys
import tempfile
from pathlib import Path

from _harness import FAILURES, check, check_true, run
from spool_audit_support import _Blocked, spool
from spoolaudit.classify import classify
from spoolaudit.config import _read_conf, parse_retention, retention_state
from spoolaudit.model import Listing, Verdict
from spoolaudit.render import render


def test_retention_honours_the_last_directive() -> None:
    """cupsd uses the LAST PreserveJobFiles line.

    Returning on the first would make a hand-edited config with No followed by
    Yes report RETENTION: OFF on a host that is retaining documents: danger
    reported as safety. This calls retention_state itself; a test that
    reimplements the thing it is testing tests the reimplementation.
    """
    with tempfile.TemporaryDirectory() as raw:
        d = Path(raw)
        (d / "last_wins.conf").write_text(
            "PreserveJobFiles No\nSomethingElse 1\nPreserveJobFiles Yes\n", encoding="utf-8")
        (d / "last_off.conf").write_text(
            "PreserveJobFiles Yes\nPreserveJobFiles No\n", encoding="utf-8")
        (d / "unset.conf").write_text("# nothing relevant here\n", encoding="utf-8")
        check("No then Yes -> retaining", retention_state(str(d / "last_wins.conf")), True)
        check("Yes then No -> not retaining", retention_state(str(d / "last_off.conf")), False)
        # An absent directive means the compiled default, which on the hosts
        # measured here keeps documents. Guessing "off" would be a guess in the
        # dangerous direction.
        check("unset -> assume retaining", retention_state(str(d / "unset.conf")), True)
        check("unreadable -> unknown", retention_state("/nonexistent/x.conf"), None)


def test_reading_the_config_never_blocks_on_a_fifo() -> None:
    """A hang in a security tool reads as "still checking".

    os.stat on a fifo returns; opening one waits for a writer that never
    arrives, so `--conf /path/to/fifo` would hang forever with no output. The
    guard refuses before the open.

    THE DEADLINE IS PART OF THE TEST. Without it, breaking the guard makes
    this test hang rather than fail, and on a CI runner a hang is a job
    timeout hours later instead of a failed assertion. One deadline covers the
    whole body, so every path out of it is loud.
    """
    if not hasattr(os, "mkfifo") or not hasattr(signal, "SIGALRM"):
        print("  note: no os.mkfifo/SIGALRM on this platform, fifo case skipped")
        return

    def _too_slow(signum: int, frame: object) -> None:
        raise _Blocked("blocked on a fifo")

    previous = signal.signal(signal.SIGALRM, _too_slow)
    signal.alarm(5)
    try:
        with tempfile.TemporaryDirectory() as raw:
            fifo = Path(raw) / "cupsd.conf"
            os.mkfifo(fifo)

            try:
                _read_conf(str(fifo))
            except OSError as exc:
                check("refused with EINVAL", exc.errno, errno.EINVAL)
            else:
                FAILURES.append("_read_conf read a fifo instead of refusing it")

            # And the caller turns that into "unknown", not into a false "OFF".
            check("retention is unknown, not off",
                  retention_state(str(fifo)), None)

            audit = classify(Listing(Verdict.CLEAN))
            text = "\n".join(render(audit, frozenset(),
                                    retention_state(str(fifo))))
            check_true("and the report says so",
                       "RETENTION: unknown (could not read cupsd.conf)" in text)
    except _Blocked:
        FAILURES.append(
            "reading a fifo BLOCKED instead of being refused; the S_ISFIFO "
            "guard is not firing, and in the real tool this is a hang with no "
            "output rather than an error"
        )
    finally:
        signal.alarm(0)
        signal.signal(signal.SIGALRM, previous)


def test_retention_is_read_not_inferred() -> None:
    """Retention off with documents still on disk reports OFF, not RETAINS."""
    audit = classify(spool(["d00079-001"]))

    off = "\n".join(render(audit, frozenset(), retention=False))
    check_true("says retention off", "RETENTION: OFF" in off)

    # No timing claim: restarting CUPS regenerates driver caches, so files
    # present after retention was turned off may postdate the change.
    check_true("makes no claim about when files appeared", "predate" not in off)
    check_true("does not claim host retains", "CUPS is keeping documents" not in off)
    check_true("still reports the leftovers", "retained file(s) still on disk" in off)

    on = "\n".join(render(audit, frozenset(), retention=True))
    check_true("says retention on", "RETENTION: ON" in on)

    unknown = "\n".join(render(audit, frozenset(), retention=None))
    check_true("admits not knowing", "RETENTION: unknown" in unknown)


def test_parse_retention_precedence() -> None:
    """The last matching directive wins, and an absent one is not "off".

    Tested on the pure parser, not only through a config file on disk. First
    match would make a hand-edited config with No followed by Yes report
    RETENTION: OFF on a host that is retaining documents: danger reported as
    safety.
    """
    cases = [
        (b"", True, "an absent directive means the compiled default, which keeps"),
        (b"PreserveJobFiles No\n", False, "an explicit No"),
        (b"PreserveJobFiles Yes\n", True, "an explicit Yes"),
        (b"PreserveJobFiles No\nPreserveJobFiles Yes\n", True, "the LAST wins"),
        (b"PreserveJobFiles Yes\nPreserveJobFiles No\n", False, "the last wins both ways"),
        (b"  preservejobfiles   off  \n", False, "case and leading whitespace"),
        (b"PreserveJobFiles 0\n", False, "0 counts as off"),
        (b"PreserveJobFiles false\n", False, "false counts as off"),
        (b"PreserveJobFiles no\n", False, "no, lowercase, counts as off"),
        (b"PreserveJobFiles On\n", True, "anything else keeps"),
        (b"# PreserveJobFiles Yes\n", True, "a commented directive is not a directive"),
        (b"# d\xe9partement\nPreserveJobFiles No\n", False, "a non-UTF-8 byte elsewhere"),
    ]
    for body, want, why in cases:
        check(f"retention, {why}", parse_retention(body), want)


def test_retention_state_reads_and_delegates() -> None:
    """The reader half of the retention check. parse_retention has the rule;
    this has the file handling, including the case that matters most: an
    unreadable config is None, which is not the same as "not retaining"."""
    with tempfile.TemporaryDirectory() as tmp:
        root = pathlib.Path(tmp)
        off = root / "off.conf"
        off.write_bytes(b"PreserveJobFiles No\n")
        check("an explicit No reads as not retaining",
              retention_state(str(off)), False)

        on = root / "on.conf"
        on.write_bytes(b"PreserveJobFiles Yes\n")
        check("an explicit Yes reads as retaining",
              retention_state(str(on)), True)

        # None, not False. Reporting an unreadable config as "not retaining"
        # would print RETENTION: OFF over a host that is keeping documents,
        # which is danger reported as safety.
        check("an unreadable config is None, never False",
              retention_state(str(root / "nope.conf")), None)

        latin1 = root / "latin1.conf"
        latin1.write_bytes(b"# Imprimante d\xe9partement\nPreserveJobFiles No\n")
        check("a non-UTF-8 byte does not break the read",
              retention_state(str(latin1)), False)


if __name__ == "__main__":
    sys.exit(run(globals(), "config"))
