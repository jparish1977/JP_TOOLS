"""Removing leftover document files, one regular file at a time, never through a link."""

from __future__ import annotations

import errno
import os
import stat

from spoolaudit.classify import safe_name
from spoolaudit.model import Audit, Entry, Removal
from spoolaudit.render import render


def purge_outcome(
    result: Removal,
    after: Audit,
    retention: bool | None,
) -> tuple[list[str], int]:
    """What a purge run prints and exits with, from what actually happened.

    Pure, so main() carries no untested decision. The after-state goes through
    render(), the same function the report path uses, so there is no second
    description of a spool to fall out of step with the first.
    """
    lines = [f"removed: {safe_name(n)}" for n in result.removed]
    # Removed-but-not-destroyed is its own line, never a bare "removed:". An
    # operator reads "removed" as "destroyed", and for a multiply-linked file
    # that reading is false: identical readable content survives at every
    # other link. CUPS itself cannot create a hard link, so anything here was
    # linked by a hand this tool cannot follow -- say so and raise the exit
    # code below.
    lines += [
        f"removed but NOT destroyed: {safe_name(n)} ({k} other hard link(s) "
        "to the same content remain readable elsewhere)"
        for n, k in result.undestroyed
    ]
    # The notes arrive already escaped: they are built around safe_name at the
    # point of failure, where the raw name is still in hand.
    lines += [f"NOT removed: {n}" for n in result.failed]
    lines.append("")
    lines.append("The spool as it stands now:")
    lines += render(after, frozenset(), retention,
                    purge_failed=frozenset(result.failed_names),
                    undestroyed=len(result.undestroyed))
    # A failed removal means the file is demonstrably still there (or, for a
    # refusal, was never touched), and an undestroyed one means the content
    # is -- just not at this path -- so the exit code may rise but never
    # fall: a clean-looking re-read does not erase either. after.exit_code
    # already answers 2 for an unreadable or incomplete re-read.
    return lines, max(after.exit_code,
                      1 if (result.failed or result.undestroyed) else 0)


# CUPS spools live on POSIX filesystems, and the anchored unlink below is
# built from POSIX openat semantics. A platform without them gets a refusal,
# not a fallback to path arithmetic, which is raceable.
_CAN_ANCHOR = (
    hasattr(os, "O_DIRECTORY")
    and hasattr(os, "O_NOFOLLOW")
    and os.open in os.supports_dir_fd
    and os.unlink in os.supports_dir_fd
)


def _component_note(rel: str, part: str, fd: int, exc: OSError) -> str:
    """Why a path component refused to open, with symlinks named as such.

    The kernel refuses a symlinked component, but on Linux as ENOTDIR, not
    ELOOP, when O_DIRECTORY is set alongside O_NOFOLLOW: the directory check
    sees the link itself and wins. Both classes therefore lstat the component
    so a symlink is reported as the symlink it is; the lstat is for the
    MESSAGE only, after the refusal has already happened, so racing it cannot
    reopen the path.
    """
    if exc.errno in (errno.ELOOP, errno.ENOTDIR):
        try:
            link = stat.S_ISLNK(os.lstat(part, dir_fd=fd).st_mode)
        except OSError as e:  # reason: the lstat is for the message only, after the refusal; the message says it could not tell
            return (f"{safe_name(rel)} (could not reach it: {exc.__class__.__name__}; whether "
                    f"{safe_name(part)} is a symlink is unknown: {e.__class__.__name__})")
        if link:
            return (f"{safe_name(rel)} (refused: {safe_name(part)} is "
                    "a symlink, NOT followed)")
    return f"{safe_name(rel)} (could not reach it: {exc.__class__.__name__})"


def _open_parents(root_fd: int, rel: str, parents: list[str],
                  opened: list[int]) -> tuple[int, str | None]:
    """(descriptor of the leaf's directory, None), or (-1, why it could not be reached).

    Every component is opened O_NOFOLLOW | O_DIRECTORY relative to the
    previous descriptor, so a symlink anywhere in the path is refused by the
    kernel instead of traversed. Each descriptor opened is appended to OPENED
    for the caller to close.
    """
    fd = root_fd
    for part in parents:
        try:
            fd = os.open(part, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW
                         | os.O_CLOEXEC, dir_fd=fd)
        except FileNotFoundError:
            # A PARENT that is gone is not the file being gone: a renamed
            # parent leaves the content alive at its new path, so this is a
            # note, told apart from a real removal.
            return -1, (f"{safe_name(rel)} (a parent component vanished mid-run; "
                        "NOT removed by this tool, and it may survive elsewhere)")
        except OSError as exc:
            return -1, _component_note(rel, part, fd, exc)
        opened.append(fd)
    return fd, None


def _leaf_refusal(fd: int, leaf: str, rel: str) -> tuple[str | None, os.stat_result | None]:
    """(None, its stat) for a regular file to unlink; (note, None) to refuse; (None, None) if gone.

    The leaf is opened O_NONBLOCK, so a fifo cannot hang the run, and
    fstat'd, and anything that is not a regular file is refused by type.
    That includes a leaf that became a symlink: unlinking a link while its
    target survives would print "removed" over readable content.
    """
    try:
        lfd = os.open(leaf, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK
                      | os.O_CLOEXEC, dir_fd=fd)
    except FileNotFoundError:
        # reason: the LEAF is gone between listing and open: nothing to delete, which is 'gone'
        return None, None
    except OSError as exc:
        if exc.errno == errno.ELOOP:
            return (f"{safe_name(rel)} (refused: a symlink, NOT removed; "
                    "unlinking a link does not destroy its target)"), None
        return f"{safe_name(rel)} (refused, could not verify it: {exc.__class__.__name__})", None
    try:
        st = os.fstat(lfd)
    finally:
        os.close(lfd)
    if not stat.S_ISREG(st.st_mode):
        return f"{safe_name(rel)} (refused: not a regular file)", None
    return None, st


def _unlink_at(root_fd: int, rel: str) -> tuple[str | None, int]:
    """Remove `rel` under an already-open spool directory, or say why not.

    Returns (note, survivors). `note` is None when the file is gone -- which
    includes finding it already gone, the goal state however reached -- and a
    report-safe note on any refusal or failure. `survivors` is how many OTHER
    hard links to the same content remain after a successful unlink, read
    from st_nlink on the fstat already taken for the S_ISREG check -- no
    second stat, no second window. Unlinking one name of a multiply-linked
    file removes the entry and destroys nothing, and the caller must not be
    allowed to collapse those two claims.

    Containment is enforced by the kernel, not by path arithmetic: a resolved
    path compared against the spool root can stop being true between the
    comparison and the unlink, and a descriptor cannot be redirected that
    way. The one path decision left in code is refusing '', '.' and '..' as
    components, because '..' is a real directory, not a symlink, and
    O_NOFOLLOW would step through it and out of the spool without complaint.

    The window between the leaf's fstat and the unlink is real but sits
    inside the audited directory: anyone who can swap files there can already
    read them.
    """
    parts = rel.split("/")
    if any(p in ("", ".", "..") for p in parts):
        # No listing can produce such a name -- iterdir cannot return one --
        # so an entry containing it did not come from the audit.
        return f"{safe_name(rel)} (refused: not a name the audit could have produced)", 0
    opened: list[int] = []
    try:
        fd, note = _open_parents(root_fd, rel, parts[:-1], opened)
        if note is not None:
            return note, 0
        note, st = _leaf_refusal(fd, parts[-1], rel)
        if st is None:
            return note, 0
        try:
            os.unlink(parts[-1], dir_fd=fd)
        except FileNotFoundError:
            # reason: the leaf went between open and unlink: nothing left to delete, which is 'gone'
            return None, 0
        except OSError as exc:
            return (f"{safe_name(rel)} (delete FAILED: {exc.__class__.__name__}; "
                    "it is still there)"), 0
        # st_nlink counted this entry, so anything above 1 is links that
        # SURVIVE the unlink just performed. The unlink still happens -- a
        # refusal would leave the spool copy in place too -- but the claim
        # "removed" must not become the claim "destroyed".
        return None, max(0, st.st_nlink - 1)
    finally:
        for f in opened:
            os.close(f)


def delete_residue(
    spool: str,
    entries: tuple[Entry, ...],
    *,
    can_anchor: bool = _CAN_ANCHOR,
) -> Removal:
    """Unlink `entries` under `spool`. Returns a Removal, per entry.

    No injectable unlink and no injectable resolve, on purpose: the safety
    property IS the syscall pattern, and a seam there would let the tests
    bypass it. The suites drive real fixture filesystems instead, with no
    root needed. The only seam is the platform capability, which a test on
    Linux could not otherwise make false.

    The spool root itself is opened WITHOUT O_NOFOLLOW: the operator named
    that path, the same trust read_spool extends when it lists through it.
    Everything below the root refuses symlinks, matching the listing exactly,
    so the purge can only reach what the audit reached, by the same rules.
    """
    if not can_anchor:
        return _refuse_all(entries, "refused: no openat/unlinkat on this platform, so "
                                    "removal cannot be anchored to the audited directory")
    try:
        root_fd = os.open(spool, os.O_RDONLY | os.O_DIRECTORY | os.O_CLOEXEC)
    except OSError as exc:
        return _refuse_all(entries, f"the spool itself could not be opened: {exc.__class__.__name__}")
    removed: list[str] = []
    undestroyed: list[tuple[str, int]] = []
    failed: list[str] = []
    failed_names: list[str] = []
    try:
        for e in entries:
            note, survivors = _unlink_at(root_fd, e.name)
            if note is not None:
                failed.append(note)
                failed_names.append(e.name)
            elif survivors:
                undestroyed.append((e.name, survivors))
            else:
                removed.append(e.name)
    finally:
        os.close(root_fd)
    return Removal(tuple(removed), tuple(undestroyed), tuple(failed),
                   tuple(failed_names))


def _refuse_all(entries: tuple[Entry, ...], why: str) -> Removal:
    """A Removal in which every entry failed, for one reason."""
    return Removal(failed=tuple(f"{safe_name(e.name)} ({why})" for e in entries),
                   failed_names=tuple(e.name for e in entries))
