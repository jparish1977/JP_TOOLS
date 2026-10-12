"""Reading the spool: the top level and TempDir, without following links."""

from __future__ import annotations

import os
import stat
from collections.abc import Callable
from pathlib import Path

from spoolaudit.classify import classify_top, safe_name, temp_child_note
from spoolaudit.model import (
    HEAD_BYTES,
    MAX_TEMP_DEPTH,
    TEMP_SUBDIR,
    Listing,
    TempFile,
    TopEntry,
    Verdict,
)

# --- I/O boundary ----------------------------------------------------------
# Thin wrappers over privileged operations, kept free of logic so everything
# above stays testable without a printer, a spool or root.


# POSIX open flags, 0 where the platform has none, so the module imports
# everywhere; CUPS spools only exist where they are real.
_HEAD_FLAGS = (os.O_RDONLY | getattr(os, "O_NONBLOCK", 0) | getattr(os, "O_NOFOLLOW", 0)
               | getattr(os, "O_CLOEXEC", 0))


def _head(path: Path, n: int = HEAD_BYTES) -> str | None:
    """First bytes of a regular file, decoded lossily, or None when the file
    could not be read or is not a regular file. Used only to recognise known
    harmless formats such as PPDs. Never printed, never logged.

    None, not "": "could not read" and "read nothing" are different facts. A
    caller has to decide what an unreadable file is, and both callers list it
    as unexamined rather than classify it.

    The guards are here, not in the callers: opened O_NONBLOCK so a fifo
    cannot hang the run, O_NOFOLLOW so a file swapped for a symlink after the
    caller's lstat is not followed, and fstat'd so only a regular file is
    read. A fifo opens fine non-blocking and reads as empty, which would be
    "read nothing" for something that was never read."""
    try:
        fd = os.open(path, _HEAD_FLAGS)
    except OSError:
        # reason: None IS the recorded outcome, "could not read", and both
        # callers list such a file as unexamined rather than classify it
        return None
    try:
        if not stat.S_ISREG(os.fstat(fd).st_mode):
            return None
        return os.read(fd, n).decode("utf-8", "replace")
    except OSError:
        # reason: as above; a read that fails is "could not read"
        return None
    finally:
        os.close(fd)


def _readlink(path: Path) -> str:
    """A symlink's target for the report, or "?" when there is none to read:
    not a link (readlink fails EINVAL), or one that cannot be read."""
    try:
        return os.readlink(path)
    except OSError:
        # reason: the target is only printed; "?" says there is none to show
        return "?"


def _lstat(path: Path, shown: str, unexamined: list[str]) -> os.stat_result | None:
    """lstat PATH, or record why not and return None.

    ONE lstat decides the type: is_symlink()/is_dir()/is_file() each swallow
    errors and return False, which would call a file deleted mid-walk "not a
    regular file". A permission error is not a race, and calling it
    "vanished" would send the user hunting a busy spool instead of running
    sudo.
    """
    try:
        return path.lstat()
    except PermissionError:
        unexamined.append(f"{shown} (permission denied on stat)")
    except OSError:
        unexamined.append(f"{shown} (vanished while reading)")
    return None


def _read_content(path: Path, name: str, size: int, found: list[TempFile],
                  unexamined: list[str]) -> None:
    """Record a regular file by its first bytes, or as unexamined if unreadable."""
    head = _head(path)
    if head is None:
        unexamined.append(f"{safe_name(name)} (could not be read)")
    else:
        found.append(TempFile(name=name, size=size, head=head))


def _walk_temp(
    directory: Path,
    label: str,
    prefix: str,
    found: list[TempFile],
    unexamined: list[str],
    depth: int = 0,
) -> None:
    """Collect regular files under a TempDir, descending into subdirectories.

    Subdirectories are walked, not flagged: a real filter chain creates
    tmp/.cache, and a permanent INCOMPLETE on every machine that has ever
    printed would train the operator to skim the report.

    Symlinks are never followed, at any depth.
    """
    if depth >= MAX_TEMP_DEPTH:
        unexamined.append(
            f"{safe_name(label)}/{safe_name(prefix)} (nested deeper than {MAX_TEMP_DEPTH}, not examined)"
        )
        return
    try:
        children = sorted(directory.iterdir())
    except OSError as exc:
        unexamined.append(f"{safe_name(label)}/{safe_name(prefix)} (unreadable: {exc.__class__.__name__})")
        return
    for f in children:
        _walk_child(f, label, f"{prefix}{f.name}", found, unexamined, depth)


def _walk_child(f: Path, label: str, name: str, found: list[TempFile],
                unexamined: list[str], depth: int) -> None:
    st = _lstat(f, f"{safe_name(label)}/{safe_name(name)}", unexamined)
    if st is None:
        return
    link = stat.S_ISLNK(st.st_mode)
    if not link and stat.S_ISDIR(st.st_mode):
        _walk_temp(f, label, f"{name}/", found, unexamined, depth + 1)
        return
    note = temp_child_note(
        label, name, is_symlink=link, is_dir=False,
        is_regular=stat.S_ISREG(st.st_mode), target=_readlink(f) if link else "?",
    )
    if note is None:
        _read_content(f, name, st.st_size, found, unexamined)
    else:
        unexamined.append(note)


def _list_names(p: Path) -> list[str]:
    """The names in directory P, sorted so the listing is the same on every run."""
    return sorted(q.name for q in p.iterdir())


def read_spool(
    spool: str,
    *,
    lister: Callable[[Path], list[str]] = _list_names,
    walk: Callable[..., None] = _walk_temp,
) -> Listing:
    """List the spool and its TempDir. Direct reads only.

    `lister` and `walk` are injectable so the two error-to-verdict mappings can
    be tested without a filesystem that produces ELOOP or ESTALE on demand,
    and without being a particular user. The one that matters most: an
    unreadable TempDir must never fall through as an empty one.

    There is one listing path. The tool needs root to read /var/spool/cups, so
    it is run under sudo, and an unreadable spool reports DENIED rather than
    taking a second code path with different rules. TempDir is always
    `<spool>/tmp`: one directory, so labels, containment and path comparison
    have one meaning.
    """
    root = Path(spool)
    try:
        top = tuple(lister(root))
    except PermissionError:
        return Listing(Verdict.DENIED)
    except FileNotFoundError:
        return Listing(Verdict.MISSING)
    except NotADirectoryError:
        return Listing(Verdict.NOT_A_DIRECTORY)
    except OSError as exc:
        # ELOOP, ESTALE, EIO. Calling all of them "permission denied, re-run
        # with sudo" would send the user to do something that fails identically.
        return Listing(Verdict.DENIED, (), (), (f"could not read the spool: {exc.__class__.__name__}",))
    unexamined: list[str] = []
    temp = _read_temp(root, lister, walk, unexamined)
    suspect, extra = _read_top(root, top, unexamined)
    return Listing(
        Verdict.CLEAN,
        tuple(n for n in top if n != TEMP_SUBDIR and n not in suspect),
        temp,
        tuple(unexamined),
        TEMP_SUBDIR,
        tuple(extra),
    )


def _temp_dir(root: Path, unexamined: list[str]) -> Path | None:
    """The TempDir to walk, or None when it is a symlink.

    A TempDir that is ITSELF a symlink is never followed: `<spool>/tmp ->
    /anywhere` would take --purge outside the audited spool.
    """
    tdir = root / TEMP_SUBDIR
    try:
        is_link = tdir.is_symlink()
    except OSError:
        # reason: is_symlink() raises on EACCES rather than answering; the
        # listing that follows meets the same error and records it
        return tdir
    if is_link:
        unexamined.append(f"{TEMP_SUBDIR} -> {safe_name(_readlink(tdir))} "
                          "(TempDir is a symlink, NOT followed)")
        return None
    return tdir


def _read_temp(root: Path, lister: Callable[[Path], list[str]], walk: Callable[..., None],
               unexamined: list[str]) -> tuple[TempFile, ...]:
    tdir = _temp_dir(root, unexamined)
    if tdir is None:
        return ()
    found: list[TempFile] = []
    try:
        # Listed first, inside this try: Path.exists() does NOT swallow
        # EACCES, so probing outside it would crash on a 0710 spool.
        lister(tdir)
        walk(tdir, TEMP_SUBDIR, "", found, unexamined)
    except PermissionError:
        # Never an empty tuple: an unreadable TempDir is not an empty one.
        unexamined.append(f"{TEMP_SUBDIR}/ (permission denied)")
    except FileNotFoundError:
        # reason: no TempDir at all is nothing to examine, not something
        # unexamined; otherwise a spool without a tmp/ could never exit 0
        return ()
    except NotADirectoryError:
        unexamined.append(f"{TEMP_SUBDIR} (exists but is not a directory, not examined)")
    except OSError as exc:
        unexamined.append(f"{TEMP_SUBDIR}/ (unreadable: {exc.__class__.__name__})")
    return tuple(found)


def _read_top(root: Path, top: tuple[str, ...],
              unexamined: list[str]) -> tuple[list[str], list[TempFile]]:
    """(names to keep out of the job listing, top-level files read for content).

    Every top-level name is stat'd, including ones matching the document and
    control patterns, so the never-follow-symlinks rule holds here as in
    TempDir: unlinking a symlink named d00085-001 and printing "1 removed"
    while the target survives is false assurance of destruction. A HARD link
    is the same risk in another shape, and _unlink_at reports it after the
    fact from st_nlink.

    Names matching neither pattern are read the same way TempDir files are,
    so a document copied to d00085-001.bak is counted rather than dropped;
    `ls` would have shown it.
    """
    suspect: list[str] = []
    extra: list[TempFile] = []
    for n in top:
        if n == TEMP_SUBDIR:
            continue
        f = root / n
        st = _lstat(f, safe_name(n), unexamined)
        if st is None:
            suspect.append(n)
            continue
        # _readlink answers "?" for anything that is not a link, so it is
        # asked of every entry rather than only the ones lstat says are links.
        d = classify_top(TopEntry(
            name=n, is_symlink=stat.S_ISLNK(st.st_mode), is_dir=stat.S_ISDIR(st.st_mode),
            is_regular=stat.S_ISREG(st.st_mode), target=_readlink(f),
        ))
        if d.note is not None:
            unexamined.append(d.note)
        if d.suspect:
            suspect.append(n)
        if d.wants_content:
            _read_content(f, n, st.st_size, extra, unexamined)
    return suspect, extra
