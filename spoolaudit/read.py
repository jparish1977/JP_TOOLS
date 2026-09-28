"""Reading the spool: the top level and TempDir, without following links."""

from __future__ import annotations

import os
import stat
from collections.abc import Callable
from pathlib import Path

from spoolaudit.classify import classify_top, safe_name, temp_child_note
from spoolaudit.model import MAX_TEMP_DEPTH, TEMP_SUBDIR, Listing, TempFile, TopEntry, Verdict

# --- I/O boundary ----------------------------------------------------------
# Thin wrappers over privileged operations, kept free of logic so everything
# above stays testable without a printer, a spool or root.


def _head(path: Path, n: int = 32) -> str | None:
    """First bytes of a file, decoded lossily, or None when the file could
    not be read. Used only to recognise known harmless formats such as PPDs.
    Never printed, never logged.

    None, not "": "could not read" and "read nothing" are different facts. A
    caller has to decide what an unreadable file is, and both callers list it
    as unexamined rather than classify it."""
    try:
        with path.open("rb") as fh:
            return fh.read(n).decode("utf-8", "replace")
    except OSError:
        # reason: None IS the recorded outcome, "could not read", and both
        # callers list such a file as unexamined rather than classify it
        return None


def _walk_temp(
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
            f"{safe_name(label)}/{safe_name(prefix)} (nested deeper than {MAX_TEMP_DEPTH}, not examined)"
        )
        return
    try:
        children = sorted(directory.iterdir())
    except OSError as exc:
        unexamined.append(f"{safe_name(label)}/{safe_name(prefix)} (unreadable: {exc.__class__.__name__})")
        return

    for f in children:
        name = f"{prefix}{f.name}"
        # ONE lstat decides the type. is_symlink()/is_dir()/is_file() each
        # swallow errors and return False, so a file deleted mid-walk was
        # classified "not a regular file" and the race branch below could never
        # execute -- while its test passed by asserting only counts.
        try:
            st = f.lstat()
        except PermissionError:
            # Not a race. A directory readable but not traversable lands here,
            # and calling it "vanished" sends the user hunting a busy spool
            # instead of running sudo.
            unexamined.append(f"{safe_name(label)}/{safe_name(name)} (permission denied on stat)")
            continue
        except OSError:
            unexamined.append(f"{safe_name(label)}/{safe_name(name)} (vanished while reading)")
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
            # st is already known; _head() answers None for a file it could
            # not read, and that file is UNEXAMINED, never classified.
            head = _head(f)
            if head is None:
                unexamined.append(f"{safe_name(name)} (could not be read)")
            else:
                found.append(TempFile(name=name, size=st.st_size, head=head))
        else:
            unexamined.append(note)


def _list_names(p: Path) -> list[str]:  # pragma: no cover
    return sorted(q.name for q in p.iterdir())


def read_spool(
    spool: str,
    *,
    lister: Callable[[Path], list[str]] = _list_names,
    walk: Callable[..., None] = _walk_temp,
) -> Listing:
    """List the spool and its TempDir. Direct reads only.

    `lister` and `walk` are injectable so the two error-to-verdict mappings can
    be tested without needing a filesystem that produces ELOOP or ESTALE on
    demand, and without needing to be a particular user. Both mappings have
    been wrong before, and the TempDir one in the way that matters most: an
    unreadable TempDir fell through as an empty one and printed "spool is
    clean" over a readable document.

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
        top = tuple(lister(root))
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
        return Listing(Verdict.DENIED, (), (), (f"could not read the spool: {exc.__class__.__name__}",))

    temp: tuple[TempFile, ...] = ()
    unexamined: list[str] = []
    default_tmp = root / TEMP_SUBDIR
    tdir: Path | None = default_tmp
    # A TempDir that is ITSELF a symlink is never followed: `<spool>/tmp ->
    # /anywhere` made --purge delete outside the audited spool and print
    # SCOPE CLEAN. Read through `default_tmp`, which is never None, so the
    # optional-ness of `tdir` cannot leak into os.readlink.
    try:
        if default_tmp.is_symlink():
            unexamined.append(
                f"{TEMP_SUBDIR} -> {safe_name(os.readlink(default_tmp))} "
                "(TempDir is a symlink, NOT followed)"
            )
            tdir = None
    except OSError:
        pass

    try:
        if tdir is None:
            raise FileNotFoundError  # symlinked TempDir, already recorded
        # Path.exists() does NOT swallow EACCES, so probing it outside this try
        # crashed with a traceback on a real 0710 spool.
        lister(tdir)
        found: list[TempFile] = []
        walk(tdir, TEMP_SUBDIR, "", found, unexamined)
        temp = tuple(found)
    except PermissionError:
        # Do NOT fall through with an empty tuple: that turned an unreadable
        # TempDir into an empty one and printed "spool is clean" over a
        # readable document.
        unexamined.append(f"{TEMP_SUBDIR}/ (permission denied)")
    except FileNotFoundError:
        # reason: no TempDir at all is nothing to examine, not something unexamined (above)
        # No TempDir at all. Nothing to examine is not the same as something
        # unexamined, and treating it as unexamined meant a spool without a
        # tmp/ could never exit 0.
        pass
    except NotADirectoryError:
        unexamined.append(f"{TEMP_SUBDIR} (exists but is not a directory, not examined)")
    except OSError as exc:
        unexamined.append(f"{TEMP_SUBDIR}/ (unreadable: {exc.__class__.__name__})")

    # Every top-level name is stat'd, including ones matching the document and
    # control patterns, so the never-follow-symlinks rule is not limited to
    # TempDir: unlinking a symlink named d00085-001 and printing "1 removed"
    # while the target survives is false assurance of destruction, the worst
    # failure this tool can have.
    #
    # That property is NOT unique to symlinks: a HARD link passes S_ISREG, is
    # unlinked correctly, and still leaves identical content readable under the
    # other name. So this refusal is not the whole answer to "did the content actually die" -- _unlink_at
    # reports st_nlink survivors, and purge_outcome raises the exit code, so the
    # claim matches what happened rather than what was attempted.
    #
    # Names matching neither pattern are read the same way TempDir files are,
    # so a document copied to d00085-001.bak is counted rather than dropped;
    # `ls` would have shown it.
    suspect: list[str] = []
    extra: list[TempFile] = []
    for n in top:
        if n == TEMP_SUBDIR:
            continue
        f = root / n
        try:
            st = f.lstat()
        except PermissionError:
            unexamined.append(f"{safe_name(n)} (permission denied on stat)")
            suspect.append(n)
            continue
        except OSError:
            unexamined.append(f"{safe_name(n)} (vanished while reading)")
            suspect.append(n)
            continue
        tgt = "?"
        if stat.S_ISLNK(st.st_mode):
            try:
                tgt = os.readlink(f)
            except OSError:
                tgt = "?"
        d = classify_top(TopEntry(
            name=n,
            is_symlink=stat.S_ISLNK(st.st_mode),
            is_dir=stat.S_ISDIR(st.st_mode),
            is_regular=stat.S_ISREG(st.st_mode),
            target=tgt,
        ))
        if d.note is not None:
            unexamined.append(d.note)
        if d.suspect:
            suspect.append(n)
        if d.wants_content:
            # The try/except OSError that stood here could never fire: _head()
            # catches its own. It answers None instead, and that is unexamined.
            head = _head(f)
            if head is None:
                unexamined.append(f"{safe_name(n)} (could not be read)")
            else:
                extra.append(TempFile(name=n, size=st.st_size, head=head))

    return Listing(
        verdict,
        tuple(n for n in top if n != TEMP_SUBDIR and n not in suspect),
        temp,
        tuple(unexamined),
        TEMP_SUBDIR,
        tuple(extra),
    )
