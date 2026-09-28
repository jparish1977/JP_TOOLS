"""chdman's command line: the arguments each job needs, and what `info` reports."""

from __future__ import annotations

import re
import subprocess
from dataclasses import dataclass
from pathlib import Path

from platformtools.tools import require, run

# Where a MAME download usually leaves chdman on Windows, which puts nothing
# on PATH.
WINDOWS_CHDMAN = (
    Path.home() / "tools" / "mame" / "chdman.exe",
    Path.home() / "tools" / "chdman.exe",
)

CHDMAN_HINT = (
    "Install with:\n"
    "  Ubuntu/Debian: sudo apt install mame-tools\n"
    "  macOS: brew install rom-tools\n"
    "  Windows: download MAME and put chdman on PATH"
)

COMPRESSION_PRESETS: dict[str, list[str]] = {
    "default": [],  # chdman's own choice
    "lzma": ["--compression", "lzma"],
    "zlib": ["--compression", "zlib"],
    "flac": ["--compression", "flac"],  # best for audio tracks
    "none": ["--compression", "none"],
    "best": ["--compression", "lzma"],
    "fast": ["--compression", "zlib"],
}

# CD descriptions: chdman builds a CD CHD from these with createcd, and they
# name the track files they cover.
SHEETS = frozenset({".cue", ".gdi", ".toc"})
# Images chdman stores byte for byte with createraw.
RAW_IMAGES = frozenset({".iso", ".img", ".bin", ".raw"})
CONVERTIBLE = SHEETS | RAW_IMAGES

# createraw refuses to run without a unit size, and a hunk must hold a whole
# number of units. 512 is a disk sector and divides every hunk size below.
RAW_UNIT_SIZE = "512"
# A disc image's hunk is one 2048-byte data sector; anything else is treated
# as a disk dump.
DISC_HUNK_SIZE = "2048"
OTHER_HUNK_SIZE = "4096"

# Metadata tags chdman writes only into CD and GD-ROM CHDs.
_CD_TAG = re.compile(r"Tag='(CHT2|CHTR|CHCD|CHGT|CHGD)'")


@dataclass(frozen=True)
class CreateOptions:
    compression: str | None = None
    hunk_size: str | None = None
    force: bool = False
    # A child CHD stores only what differs from this parent, and takes its
    # hunk and unit sizes from it.
    parent: Path | None = None


def compression_args(name: str | None) -> list[str]:
    """chdman's flags for a preset name, or for a codec list given as-is."""
    if not name:
        return []
    return COMPRESSION_PRESETS.get(name, ["--compression", name])


def create_args(src: Path, dst: Path, opts: CreateOptions) -> list[str]:
    """chdman arguments that turn SRC into the CHD DST."""
    ext = src.suffix.lower()
    subcmd = "createcd" if ext in SHEETS else "createraw"
    args = [subcmd, "--input", str(src), "--output", str(dst)]
    args += compression_args(opts.compression)
    if opts.parent is not None:
        args += ["--outputparent", str(opts.parent)]
        if opts.hunk_size:
            args += ["--hunksize", opts.hunk_size]
    elif subcmd == "createraw":
        default = DISC_HUNK_SIZE if ext in RAW_IMAGES else OTHER_HUNK_SIZE
        args += ["--hunksize", opts.hunk_size or default, "--unitsize", RAW_UNIT_SIZE]
    elif opts.hunk_size:
        args += ["--hunksize", opts.hunk_size]
    if opts.force:
        args.append("--force")
    return args


def chdman(args: list[str]) -> subprocess.CompletedProcess[str]:
    """Run chdman, captured. Raises ToolMissingError when it is not installed."""
    return run(require("chdman", CHDMAN_HINT, WINDOWS_CHDMAN), args)


def info_text(chd: Path) -> str | None:
    """chdman's verbose info for CHD, or None when chdman could not read it."""
    result = chdman(["info", "--verbose", "--input", str(chd)])
    return result.stdout if result.returncode == 0 else None


def parse_info(text: str) -> dict[str, str]:
    """`Key: value` lines as {key: value}, keys lower-case with underscores."""
    info: dict[str, str] = {}
    for line in text.splitlines():
        key, sep, value = line.partition(":")
        if sep and key.strip():
            info[key.strip().lower().replace(" ", "_")] = value.strip()
    return info


def is_cd(text: str) -> bool:
    """Does this info text describe a CD or GD-ROM CHD?"""
    return _CD_TAG.search(text) is not None
