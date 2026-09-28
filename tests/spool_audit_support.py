"""Fixtures shared by the spool-audit test files."""

from spoolaudit.model import Listing, TempFile, Verdict


class _Blocked(BaseException):
    """Raised by a test's alarm when a read or open blocks on a fifo.

    BaseException, deliberately NOT a subclass of OSError: the code under test
    catches OSError, and an alarm it could swallow would turn a hang into a
    tidy "unknown" and let the test pass over the failure it exists to catch.
    """


def spool(top: list[str], temp: list[str] | None = None) -> Listing:
    """Build a readable Listing fixture. Temp files default to unrecognised."""
    return Listing(
        Verdict.CLEAN,
        tuple(top),
        tuple(TempFile(name=n, size=100, head="%!PS") for n in (temp or [])),
    )


def first_note(failed: tuple[object, ...]) -> str:
    """failed[0], surviving a mutant that refuses nothing.

    Indexing an empty tuple aborts the whole run with a traceback at the
    first broken guard, hiding every result after it. Under mutation the
    interesting output is ALL the failures, so an absent note becomes a
    string no real note matches instead of an IndexError.
    """
    return str(failed[0]) if failed else "<no refusal was recorded>"
