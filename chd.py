#!/usr/bin/env python3
"""CHD archive management: a wrapper for MAME's chdman.

Create, verify, extract, compare and re-compress CHD archives of disc images
and raw disk dumps, one file or a whole directory at a time.

Usage:
    python chd.py create <input> <output.chd> [options]
    python chd.py verify <file.chd>
    python chd.py extract <file.chd> <output>
    python chd.py info <file.chd> [--json]
    python chd.py delta <parent.chd> <child-input> <child.chd>
    python chd.py merge <parent.chd> <child.chd> <output.chd>
    python chd.py compare <file1.chd> <file2.chd>
    python chd.py convert <input.chd> <output.chd> [--compression <type>]
    python chd.py import <input> [<output.chd>]
    python chd.py batch-create <directory> [--format <ext>] [--delete-originals]
    python chd.py batch-verify <directory>
    python chd.py batch-import <directory> [--format <ext>] [--delete-originals]
    python chd.py import-tools

chdman reads .iso .img .bin .raw directly (createraw) and .cue .gdi .toc as
CDs (createcd). `import` also takes .cso .zso .dax .pbp .nrg .mdf .ecm and
.7z .zip .rar .gz archives, converting with the tools `import-tools` lists.

A cue, gdi or toc sheet and the track files it names are one disc: batches
convert the sheet, not its tracks on their own, and --delete-originals
deletes the tracks with it, never anything outside the sheet's directory.

Examples:
    python chd.py create game.iso game.chd
    python chd.py create disk.img disk.chd --compression lzma
    python chd.py create game.cue game.chd                     # CD with cue sheet
    python chd.py delta base.chd updated.img updated.chd       # child of base.chd
    python chd.py merge base.chd updated.chd standalone.chd    # child made whole
    python chd.py batch-create ~/roms/psx --format iso
    python chd.py batch-verify ~/roms/chd

Exit status: the failing chdman or converter status, 1 for a missing input
or any failed file in a batch, 2 when a required program is not installed.
`compare` exits 0 for identical, 1 for different, 2 for unreadable.
"""

from __future__ import annotations

import argparse
import sys
from collections.abc import Callable
from pathlib import Path

from chdtool import batch, commands
from chdtool.chdman import COMPRESSION_PRESETS, CreateOptions
from chdtool.importing import ImportOptions, import_image, tool_status
from chdtool.tools import ToolMissingError

_PRESETS = list(COMPRESSION_PRESETS)

_FLAG_HELP = {
    "force": "Overwrite existing output",
    "dry-run": "Print what would run, and run nothing",
    "keep-intermediate": "Keep converted and extracted files after conversion",
    "delete-originals": "Delete each original (and a sheet's tracks) once its CHD is made",
}

_Sub = argparse._SubParsersAction  # the type add_subparsers() returns


def _flags(p: argparse.ArgumentParser, *names: str) -> None:
    for name in names:
        p.add_argument(f"--{name}", action="store_true", help=_FLAG_HELP[name])


def _paths(p: argparse.ArgumentParser, *names: tuple[str, str]) -> None:
    for name, help_text in names:
        p.add_argument(name, help=help_text)


def _single_parsers(sub: _Sub[argparse.ArgumentParser]) -> None:
    p = sub.add_parser("create", help="Create CHD from disc image or raw dump")
    _paths(p, ("input", "Input file (.iso, .img, .bin, .raw, .cue, .gdi, .toc)"),
           ("output", "Output CHD file"))
    p.add_argument("--compression", choices=_PRESETS, help="Compression preset")
    p.add_argument("--hunk-size", help="Hunk size in bytes, a multiple of 512 (default: auto)")
    _flags(p, "force", "dry-run")
    p = sub.add_parser("verify", help="Verify CHD integrity")
    _paths(p, ("file", "CHD file to verify"))
    p = sub.add_parser("extract", help="Extract CHD to a cue sheet (CDs) or raw image")
    _paths(p, ("file", "CHD file to extract"), ("output", "Output file (a .cue for a CD)"))
    _flags(p, "force", "dry-run")
    p = sub.add_parser("info", help="Show CHD metadata")
    _paths(p, ("file", "CHD file"))
    p.add_argument("--json", action="store_true", help="Output as JSON")
    p = sub.add_parser("delta", help="Create delta (child) CHD from parent")
    _paths(p, ("parent", "Parent CHD file"), ("input", "Input file for child"),
           ("output", "Output child CHD file"))
    _flags(p, "dry-run")
    p = sub.add_parser("merge", help="Merge child CHD with its parent into a standalone CHD")
    _paths(p, ("parent", "Parent CHD file the child was made against"),
           ("child", "Child CHD file"), ("output", "Output standalone CHD file"))
    _flags(p, "force", "dry-run")
    p = sub.add_parser("compare", help="Compare two CHD files")
    _paths(p, ("file1", "First CHD file"), ("file2", "Second CHD file"))
    p = sub.add_parser("convert", help="Re-compress CHD with different settings")
    _paths(p, ("input", "Input CHD file"), ("output", "Output CHD file"))
    p.add_argument("--compression", choices=_PRESETS, help="Compression preset")
    _flags(p, "force", "dry-run")


def _batch_parsers(sub: _Sub[argparse.ArgumentParser]) -> None:
    p = sub.add_parser("import", help="Convert any disc image format to CHD")
    p.add_argument("input", help="Input file (ISO, CSO, PBP, NRG, MDF, ECM, CUE, GDI, 7z, zip, ...)")
    p.add_argument("output", nargs="?", help="Output CHD file (default: same name with .chd)")
    p.add_argument("--compression", choices=_PRESETS)
    _flags(p, "force", "keep-intermediate", "dry-run")
    p = sub.add_parser("batch-create", help="Convert all matching files in a directory")
    _paths(p, ("directory", "Directory to scan"))
    p.add_argument("--format", help="File extensions to convert (comma-separated, e.g. iso,bin,cue)")
    _flags(p, "delete-originals", "force", "dry-run")
    p = sub.add_parser("batch-verify", help="Verify all CHDs in a directory")
    _paths(p, ("directory", "Directory to scan"))
    p.add_argument("--json", action="store_true", help="Output summary as JSON")
    p = sub.add_parser("batch-import", help="Convert all disc images in a directory to CHD")
    _paths(p, ("directory", "Directory to scan"))
    p.add_argument("--format", help="File extensions to convert (comma-separated)")
    p.add_argument("--compression", choices=_PRESETS)
    _flags(p, "delete-originals", "keep-intermediate", "force", "dry-run")
    sub.add_parser("import-tools", help="Show which format conversion tools are installed")


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    sub = parser.add_subparsers(dest="command", help="Command to run")
    _single_parsers(sub)
    _batch_parsers(sub)
    return parser


def _p(value: str) -> Path:
    return Path(value).resolve()


def _import_options(a: argparse.Namespace) -> ImportOptions:
    return ImportOptions(a.compression, a.force, a.dry_run, a.keep_intermediate)


_COMMANDS: dict[str, Callable[[argparse.Namespace], int]] = {
    "create": lambda a: commands.create(
        _p(a.input), _p(a.output), CreateOptions(a.compression, a.hunk_size, a.force), a.dry_run),
    "verify": lambda a: commands.verify(_p(a.file)),
    "extract": lambda a: commands.extract(_p(a.file), _p(a.output), a.force, a.dry_run),
    "info": lambda a: commands.info(_p(a.file), a.json),
    "delta": lambda a: commands.delta(_p(a.parent), _p(a.input), _p(a.output), a.dry_run),
    "merge": lambda a: commands.merge(
        _p(a.parent), _p(a.child), _p(a.output), a.force, a.dry_run),
    "compare": lambda a: commands.compare(_p(a.file1), _p(a.file2)),
    "convert": lambda a: commands.convert(
        _p(a.input), _p(a.output), a.compression, a.force, a.dry_run),
    "import": lambda a: import_image(
        _p(a.input), _p(a.output) if a.output else None, _import_options(a)),
    "batch-create": lambda a: batch.batch_create(
        _p(a.directory), a.format, a.force, a.delete_originals, a.dry_run),
    "batch-verify": lambda a: batch.batch_verify(_p(a.directory), a.json),
    "batch-import": lambda a: batch.batch_import(
        _p(a.directory), a.format, _import_options(a), a.delete_originals),
    "import-tools": lambda a: tool_status(),
}


def main(argv: list[str] | None = None) -> int:
    parser = _parser()
    args = parser.parse_args(argv)
    if not args.command:
        parser.print_help()
        return 0
    try:
        return _COMMANDS[args.command](args)
    except ToolMissingError as exc:
        print(f"Error: {exc.name} not found.")
        print(exc.hint)
        return 2


if __name__ == "__main__":
    sys.exit(main())
