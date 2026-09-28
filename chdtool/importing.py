"""Formats chdman cannot read: convert or extract them first, then create the CHD.

A converter's output is written beside its input and removed afterwards
unless --keep-intermediate. A file already at that path is never overwritten
or removed: the conversion refuses instead. An archive is extracted into a
hidden directory of its own beside it, so only what came out of the archive
is converted, and each CHD is written beside the archive.
"""

from __future__ import annotations

import shutil
import tempfile
from dataclasses import dataclass
from pathlib import Path

from chdtool.chdman import CONVERTIBLE, CreateOptions
from chdtool.commands import create
from chdtool.output import missing
from chdtool.selection import select
from chdtool.tools import find_tool, require, run


@dataclass(frozen=True)
class Converter:
    tool: str
    leading: tuple[str, ...]  # arguments before the input path
    produces: str  # extension of the image it writes
    output_flag: tuple[str, ...]  # what precedes the output path; () = last argument

    def args(self, src: Path, out: Path) -> list[str]:
        return [*self.leading, str(src), *self.output_flag, str(out)]


_MAXCSO = Converter("maxcso", ("--decompress",), ".iso", ("-o",))
CONVERTERS: dict[str, Converter] = {
    ".cso": _MAXCSO,
    ".zso": _MAXCSO,
    ".dax": _MAXCSO,
    ".pbp": Converter("pbp2iso", (), ".iso", ()),
    ".nrg": Converter("nrg2iso", (), ".iso", ()),
    ".mdf": Converter("mdf2iso", (), ".iso", ()),
    ".ecm": Converter("ecm2bin", (), ".bin", ()),
}
ARCHIVES = frozenset({".7z", ".zip", ".rar", ".gz"})
IMPORTABLE = CONVERTIBLE | frozenset(CONVERTERS) | ARCHIVES

TOOL_HINTS = {
    "maxcso": "PSP: CSO/ZSO/DAX decompression (apt install maxcso OR github.com/unknownbrackets/maxcso)",
    "pbp2iso": "PSP: PBP to ISO (github.com/pspdev/pspsdk)",
    "nrg2iso": "Nero: NRG to ISO (apt install nrg2iso)",
    "mdf2iso": "Alcohol: MDF to ISO (apt install mdf2iso)",
    "ecm2bin": "ECM: error code modeler decode (apt install ecm)",
    "7z": "Archives: 7z/zip/rar/gz extraction (apt install p7zip-full)",
}


@dataclass(frozen=True)
class ImportOptions:
    compression: str | None = None
    force: bool = False
    dry_run: bool = False
    keep_intermediate: bool = False


def _require(tool: str) -> list[str]:
    return require(tool, f"Install {tool} and try again: {TOOL_HINTS[tool]}")


def import_image(src: Path, dst: Path | None, opts: ImportOptions) -> int:
    """Turn SRC, in any supported format, into the CHD DST (default: beside it)."""
    if not src.exists():
        return missing("File", src)
    ext = src.suffix.lower()
    if ext in ARCHIVES:
        return _import_archive(src, dst, opts)
    out = dst or src.with_suffix(".chd")
    if ext in CONVERTIBLE:
        print(f"Format {ext} is natively supported -- creating CHD directly")
        return create(src, out, CreateOptions(opts.compression, force=opts.force), opts.dry_run)
    converter = CONVERTERS.get(ext)
    if converter is None:
        print(f"Error: Unsupported format: {ext}")
        print(f"Supported formats: {', '.join(sorted(IMPORTABLE))}")
        return 1
    return _import_converted(src, out, converter, opts)


def _import_converted(src: Path, out: Path, converter: Converter, opts: ImportOptions) -> int:
    prefix = _require(converter.tool)
    mid = src.with_suffix(converter.produces)
    args = converter.args(src, mid)
    print(f"Step 1: {src.suffix.lower()} -> {converter.produces} (using {converter.tool})")
    if opts.dry_run:
        print(f"[DRY RUN] Would run: {converter.tool} {' '.join(args)}")
        print(f"[DRY RUN] Then: chdman create {mid} -> {out}")
        return 0
    if mid.exists():
        print(f"Error: {mid} already exists; the conversion would overwrite it")
        return 1
    try:
        if run(prefix, args, capture=False).returncode != 0:
            print(f"Error: {converter.tool} failed")
            return 1
        if not mid.exists():
            print(f"Error: Expected intermediate file not found: {mid}")
            return 1
        print(f"Step 2: {converter.produces} -> .chd (using chdman)")
        return create(mid, out, CreateOptions(opts.compression, force=opts.force), False)
    finally:
        if not opts.keep_intermediate and mid.exists():
            mid.unlink()
            print(f"Cleaned up intermediate: {mid.name}")


def _import_archive(src: Path, dst: Path | None, opts: ImportOptions) -> int:
    prefix = _require("7z")
    print("Archive detected -- extracting first")
    if opts.dry_run:
        print(f"[DRY RUN] Would extract {src} and convert contents")
        return 0
    work = Path(tempfile.mkdtemp(prefix=f".{src.stem}-", dir=src.parent))
    try:
        if run(prefix, ["x", str(src), f"-o{work}"], capture=False).returncode != 0:
            print(f"Error: Failed to extract {src}")
            return 1
        return _import_extracted(src, dst, work, opts)
    finally:
        if opts.keep_intermediate:
            print(f"Kept extracted files: {work}")
        else:
            shutil.rmtree(work)


def _import_extracted(src: Path, dst: Path | None, work: Path, opts: ImportOptions) -> int:
    images, notes = select(sorted(f for f in work.rglob("*") if f.is_file()), IMPORTABLE)
    for note in notes:
        print(f"Note: {note}")
    if not images:
        print("Error: No disc image found in archive")
        return 1
    codes = []
    for image in images:
        out = dst if dst is not None and len(images) == 1 else src.parent / f"{image.stem}.chd"
        print(f"Converting extracted: {image.name}")
        codes.append(import_image(image, out, opts))
    return max(codes)


def tool_status() -> int:
    """Which converters are installed."""
    print("Import tool status:")
    print()
    for tool, description in TOOL_HINTS.items():
        prefix = find_tool(tool)
        mark, status = ("+", f"INSTALLED ({' '.join(prefix)})") if prefix else ("-", "NOT INSTALLED")
        print(f"  {mark} {tool:15s} {status}")
        print(f"    {description}")
        print()
    return 0
