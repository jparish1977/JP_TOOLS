"""The spool's data model: what a listing, an entry and an audit are."""

from __future__ import annotations

import re
from dataclasses import dataclass
from enum import Enum

DEFAULT_SPOOL = "/var/spool/cups"
DEFAULT_CONF = "/etc/cups/cupsd.conf"
TEMP_SUBDIR = "tmp"

DOCUMENT = re.compile(r"^d(\d+)-(\d+)$")
CONTROL = re.compile(r"^c(\d+)$")

# CUPS keeps its own runtime files in TempDir alongside spooled document data:
# a typical tmp/ holds opaque document temporaries and a
# `cups-dbus-notifier-lockfile`. Counting the lockfile as leaked content
# inflates every total, and deleting it removes a lock from under a running
# cupsd. Named artifacts are reported separately and are never purged.
# Deliberately no bare `^cups-dbus-` prefix: that would dismiss any name with
# the prefix, a tmp/cups-dbus-secret holding a password included. The real
# artifact, cups-dbus-notifier-lockfile, matches the lockfile alternative.
ARTIFACT = re.compile(r"^cups-.*(lockfile|notifier|socket)$")

# Directory levels below TempDir that are walked. Every traversal must agree
# on it: find exits 0 with no marker when it truncates, so a traversal that
# stops a level early reports a document nested past the limit as "clean".
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
    # rather than recovered from the name later. Re-deriving it from a "tmp/"
    # prefix would make the name a second source of truth for a fact the
    # listing already has, and re-apply the name trust that is_harmless_temp
    # explicitly refuses at the top level.
    in_temp: bool = False


@dataclass(frozen=True)
class TempFile:
    """A file in CUPS TempDir, with just enough to classify it.

    `head` is the first few bytes, decoded lossily, used only to recognise
    known-harmless formats. It is never printed. A file whose header could not
    be read never becomes a TempFile: it is listed as unexamined. An
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
    # subdirectory under it. This is how the model says "top level readable,
    # TempDir not", so an unreadable TempDir can never read as an empty one.
    unexamined: tuple[str, ...] = ()
    # Always "tmp". A field so classify() has one place to read the prefix
    # from rather than hardcoding it twice.
    temp_label: str = TEMP_SUBDIR
    # Top-level entries matching neither d<n>-<n> nor c<n>, read for content
    # so a document left as d00085-001.bak is counted rather than hidden:
    # over-report, never dismiss, the same rule as TempDir. The last field,
    # so positional constructions of the earlier fields stay valid.
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

        Whether the jobs you named are gone is answered in the report text;
        the exit code says whether anything is still there (see below).
        """
        if not self.complete:
            return 2
        # STRICT: 0 means no print data is left anywhere, not "the jobs you
        # named are gone". Job identity is not content identity -- a copy of
        # job 85's document named d00085-001.bak carries no job id, so a
        # scoped exit code would be 0 over a readable copy and
        # `85 --purge && echo SAFE` would fire.
        #
        # The scoped question is still answered, in the report text. The exit
        # code answers the only one a script can act on: is anything still
        # here?
        return 0 if self.verdict is Verdict.CLEAN else 1


@dataclass(frozen=True)
class Removal:
    """What one purge pass did, entry by entry.

    `removed` is names whose content is gone. `undestroyed` is (name, other
    links): the spool's directory entry was unlinked, but st_nlink said that
    many OTHER hard links to the same content remain, so the entry is removed
    and the document is not destroyed -- two different claims, and collapsing
    them would print "removed:" and exit 0 over readable content. `failed` is
    report-safe notes for entries still present; `failed_names` mirrors it
    with the raw names, so the renderer can stop advising --purge for exactly
    the entries this run just failed on.
    """

    removed: tuple[str, ...] = ()
    undestroyed: tuple[tuple[str, int], ...] = ()
    failed: tuple[str, ...] = ()
    failed_names: tuple[str, ...] = ()


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
