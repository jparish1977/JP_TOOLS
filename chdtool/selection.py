"""Which files a batch converts, and the track files a CD sheet covers.

A sheet (.cue, .gdi, .toc) and the track files it names are ONE disc. The
sheet is converted; its tracks are not converted again on their own, and they
are deleted along with it.
"""

from __future__ import annotations

import shlex
from collections.abc import Iterable
from pathlib import Path

from chdtool.chdman import SHEETS


def parse_extensions(spec: str | None, default: frozenset[str]) -> frozenset[str]:
    """`iso,.bin, CUE` as {".iso", ".bin", ".cue"}; DEFAULT when SPEC is empty."""
    if not spec:
        return default
    exts = (e.strip().lower() for e in spec.split(","))
    return frozenset(e if e.startswith(".") else f".{e}" for e in exts if e)


def _quoted_name(line: str) -> str | None:
    parts = line.split('"')
    return parts[1] if len(parts) >= 3 else None


def _gdi_name(line: str) -> str | None:
    # A track line: number, start sector, type, sector size, FILE, offset.
    # The first line of the file is the track count, which has one field.
    try:
        fields = shlex.split(line)
    except ValueError:
        # reason: an unbalanced quote is not a track line, so it names no file
        return None
    return fields[4] if len(fields) >= 5 else None


def sheet_tracks(sheet: Path) -> list[Path]:
    """The track files SHEET names, beside it. Raises OSError when it cannot be read."""
    # Bytes decoded with surrogateescape: a sheet is not guaranteed UTF-8,
    # and the names in it must come back out as the bytes on disk.
    text = sheet.read_bytes().decode("utf-8", "surrogateescape")
    gdi = sheet.suffix.lower() == ".gdi"
    names: list[str] = []
    for line in text.splitlines():
        stripped = line.strip()
        if gdi:
            name = _gdi_name(stripped)
        elif stripped.upper().startswith(("FILE", "DATAFILE")):
            name = _quoted_name(stripped)
        else:
            name = None
        if name:
            names.append(name)
    return [sheet.parent / n for n in names]


def select(files: Iterable[Path], extensions: frozenset[str]) -> tuple[list[Path], list[str]]:
    """(files to convert, notes) from FILES: matching EXTENSIONS, minus sheet tracks.

    A sheet that cannot be read is still selected, and a note says its tracks
    could not be excluded.
    """
    candidates = [f for f in files if f.suffix.lower() in extensions]
    tracks: set[Path] = set()
    notes: list[str] = []
    for f in candidates:
        if f.suffix.lower() in SHEETS:
            try:
                tracks.update(sheet_tracks(f))
            except OSError as exc:
                notes.append(f"could not read {f.name} ({exc.__class__.__name__}); "
                             "its track files may be converted on their own")
    return [f for f in candidates if f not in tracks], notes


def pending(directory: Path, extensions: frozenset[str], force: bool) -> tuple[list[Path], list[str]]:
    """Files under DIRECTORY to convert: selected, and without a CHD beside them unless FORCE."""
    files = (f for f in sorted(directory.rglob("*")) if f.is_file())
    chosen, notes = select(files, extensions)
    return [f for f in chosen if force or not f.with_suffix(".chd").exists()], notes


def disc_size(image: Path) -> int:
    """Bytes in IMAGE, plus the tracks it names when it is a sheet and they can be read."""
    size = image.stat().st_size
    if image.suffix.lower() in SHEETS:
        try:
            tracks = sheet_tracks(image)
        except OSError:
            # reason: the size is for the report only; select() has already
            # said this sheet could not be read
            return size
        size += sum(t.stat().st_size for t in tracks if t.is_file())
    return size


def _inside(path: Path, directory: Path) -> bool:
    return path.resolve().is_relative_to(directory.resolve())


def delete_original(image: Path) -> list[str]:
    """Delete IMAGE and, for a sheet, the tracks it names. Returns what was not deleted.

    A sheet is text anyone could have written, so a track is deleted only
    when it resolves inside the sheet's own directory: `FILE "/etc/x"` or
    `FILE "../x"` names nothing this deletes. A sheet that cannot be read is
    left in place with its tracks, rather than removed and its tracks orphaned.
    """
    if image.suffix.lower() not in SHEETS:
        victims = [image]
    else:
        try:
            tracks = sheet_tracks(image)
        except OSError as exc:
            return [f"{image.name} and its tracks (could not read it: {exc.__class__.__name__})"]
        victims = [*tracks, image]
    inside = [f for f in victims if _inside(f, image.parent)]
    failures = [f"{t.name} (outside {image.parent.name}/, not deleted)"
                for t in victims if t not in inside]
    for f in inside:
        try:
            f.unlink()
        except FileNotFoundError:
            # reason: a track the sheet names but that is not there is already gone
            continue
        except OSError as exc:
            failures.append(f"{f.name} ({exc.__class__.__name__})")
    return failures
