"""The commands that work on every matching file under a directory.

Each exits 1 when any file failed, so a script can tell a partial batch from
a complete one. A file is skipped when a CHD of its name already sits beside
it, unless --force.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from chdtool.chdman import CONVERTIBLE, CreateOptions, chdman, create_args
from chdtool.importing import IMPORTABLE, ImportOptions, import_image
from chdtool.output import gb, mb, not_a_directory
from chdtool.selection import delete_original, disc_size, parse_extensions, pending


@dataclass
class _Tally:
    converted: int = 0
    failed: int = 0
    saved: int = 0


def _print_notes(notes: list[str]) -> None:
    for note in notes:
        print(f"Note: {note}")


def _delete(image: Path) -> None:
    failures = delete_original(image)
    for failure in failures:
        print(f"  NOT deleted: {failure}")
    if not failures:
        print("  Deleted original")


def _create_one(image: Path, force: bool, delete: bool, tally: _Tally) -> None:
    chd = image.with_suffix(".chd")
    size = disc_size(image)
    result = chdman(create_args(image, chd, CreateOptions(force=force)))
    if result.returncode != 0 or not chd.exists():
        print(f"  FAILED: {result.stderr.strip()}")
        tally.failed += 1
        return
    out = chd.stat().st_size
    tally.converted += 1
    tally.saved += size - out
    ratio = round(out / size * 100, 1) if size > 0 else 0
    print(f"  OK -- {ratio}% ({mb(size - out)} MB saved)")
    if delete:
        _delete(image)


def batch_create(directory: Path, formats: str | None, force: bool, delete: bool, dry: bool) -> int:
    """Convert every image chdman reads directly under DIRECTORY."""
    if not directory.is_dir():
        return not_a_directory(directory)
    extensions = parse_extensions(formats, CONVERTIBLE)
    files, notes = pending(directory, extensions, force)
    _print_notes(notes)
    if not files:
        print(f"No convertible files found in {directory}")
        print(f"Looking for: {', '.join(sorted(extensions))}")
        return 0
    print(f"Found {len(files)} files to convert in {directory}")
    print(f"Total input size: {gb(sum(disc_size(f) for f in files))} GB")
    print()
    if dry:
        for f in files:
            print(f"  [DRY RUN] {f.name} -> {f.stem}.chd")
        return 0
    tally = _Tally()
    for i, f in enumerate(files, 1):
        print(f"[{i}/{len(files)}] {f.name}")
        _create_one(f, force, delete, tally)
    print(f"\nBatch complete: {tally.converted} converted, {tally.failed} failed")
    print(f"Total space saved: {gb(tally.saved)} GB")
    return 1 if tally.failed else 0


def batch_verify(directory: Path, as_json: bool) -> int:
    """Verify every CHD under DIRECTORY."""
    if not directory.is_dir():
        return not_a_directory(directory)
    files = sorted(directory.rglob("*.chd"))
    if not files:
        print(f"No CHD files found in {directory}")
        return 0
    print(f"Verifying {len(files)} CHD files in {directory}")
    print()
    failed: list[str] = []
    for i, f in enumerate(files, 1):
        ok = chdman(["verify", "--input", str(f)]).returncode == 0
        print(f"  [{i}/{len(files)}] {'PASS' if ok else 'FAIL'} -- {f.name}")
        if not ok:
            failed.append(str(f))
    print(f"\nResults: {len(files) - len(failed)} passed, {len(failed)} failed")
    if failed:
        print("Failed files:")
        for name in failed:
            print(f"  {name}")
    if as_json:
        print(json.dumps({
            "directory": str(directory),
            "total": len(files),
            "passed": len(files) - len(failed),
            "failed": len(failed),
            "failed_files": failed,
            "timestamp": datetime.now().astimezone().isoformat(),
        }, indent=2))
    return 1 if failed else 0


def batch_import(directory: Path, formats: str | None, opts: ImportOptions, delete: bool) -> int:
    """Import every supported file under DIRECTORY, whatever its format."""
    if not directory.is_dir():
        return not_a_directory(directory)
    files, notes = pending(directory, parse_extensions(formats, IMPORTABLE), opts.force)
    _print_notes(notes)
    if not files:
        print(f"No convertible files found in {directory}")
        return 0
    print(f"Found {len(files)} files to import in {directory}")
    print()
    tally = _Tally()
    for i, f in enumerate(files, 1):
        print(f"\n[{i}/{len(files)}] {f.name}")
        if import_image(f, None, opts) != 0:
            tally.failed += 1
            continue
        tally.converted += 1
        if delete and not opts.dry_run:
            _delete(f)
    print(f"\nBatch import complete: {tally.converted} converted, {tally.failed} failed")
    return 1 if tally.failed else 0
