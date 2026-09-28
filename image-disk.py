#!/usr/bin/env python3
"""Make a disk image with ddrescue, which survives bad sectors and resumes.

Two passes: a fast copy of everything readable, then a scrape that retries
the bad areas three times. The map file (--log) records what is done, so an
interrupted run picks up where it stopped.

Usage:
    python image-disk.py <source> <output.img> [--log <mapfile>] [--dry-run] [--no-scrape]

Source can be:
    /dev/sdX        a block device (needs root)
    F:              a Windows drive letter: prints how to attach it to WSL,
                    since WSL cannot read a USB drive until it is attached

Examples:
    python image-disk.py /dev/sdb ~/images/drive.img
    python image-disk.py /dev/sdb ~/images/drive.img --log ~/images/drive.log

Exit status: ddrescue's (the first pass that fails), 1 when the source cannot
be read as given, 2 when ddrescue is not installed.
"""

from __future__ import annotations

import argparse
import subprocess
import sys
import time
from pathlib import Path

from platformtools.tools import find_tool
from platformtools.windows import disk_number, drive_letter, is_admin, usb_drive_advice, wsl_path

DDRESCUE_HINT = (
    "Install with:\n"
    "  Ubuntu/Debian: sudo apt install gddrescue\n"
    "  macOS: brew install ddrescue"
)


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument("source", help="Device or drive letter to image")
    parser.add_argument("output", help="Output image file path")
    parser.add_argument("--log", help="ddrescue map file, which makes a run resumable "
                                      "(default: <output>.log)")
    parser.add_argument("--dry-run", action="store_true",
                        help="Show what would be done without imaging")
    parser.add_argument("--no-scrape", action="store_true",
                        help="Skip the scraping pass (faster, may miss bad sectors)")
    return parser


def find_ddrescue() -> list[str] | None:
    """ddrescue's argv prefix; some distributions name it gddrescue."""
    return find_tool("ddrescue") or find_tool("gddrescue")


def passes(prefix: list[str], source: str, image: str, mapfile: str,
           scrape: bool) -> list[list[str]]:
    """The ddrescue commands, in order: a fast copy, then (SCRAPE) the retries."""
    fast = [*prefix, "-f", "-n", source, image, mapfile]
    retry = [*prefix, "-f", "-d", "-r", "3", source, image, mapfile]
    return [fast, retry] if scrape else [fast]


def _source(arg: str) -> str | None:
    """The device to image, or None (having said why) for a Windows drive letter."""
    letter = drive_letter(arg)
    if letter is None:
        return arg
    number = disk_number(letter)
    if number is None:
        print(f"Error: Could not find the physical drive for {arg}")
        return None
    for line in usb_drive_advice(arg, number):
        print(line)
    return None


def _privileged(prefix: list[str], source: str) -> list[str] | None:
    """The prefix that can read SOURCE, or None when this needs root and is not it.

    In WSL a device needs WSL's root, which Windows administrator rights do
    not give, so a WSL ddrescue always runs under sudo there.
    """
    if not source.startswith("/dev/"):
        return prefix
    if prefix[0] == "wsl":
        return ["wsl", "sudo", *prefix[1:]]
    return prefix if is_admin() else None


def _image(runs: list[list[str]], output: Path, mapfile: Path) -> int:
    start = time.monotonic()
    for label, argv in zip(("Pass 1 (fast copy)", "Pass 2 (scrape)"), runs, strict=False):
        print(f"\n{label}: {' '.join(argv)}")
        rc = subprocess.run(argv, check=False).returncode
        if rc != 0:
            print(f"\n{label} failed with exit code {rc}; run the same command again to resume.")
            return rc
    size = output.stat().st_size if output.exists() else 0
    print("\nImaging complete:")
    print(f"  Size:    {round(size / 1024**3, 1)} GB")
    print(f"  Elapsed: {round(time.monotonic() - start, 1)}s")
    print(f"  Image:   {output}")
    print(f"  Log:     {mapfile}")
    print("\nRecover files with:")
    print(f"  python recover.py {output} ./recovered --profile all")
    return 0


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    found = find_ddrescue()
    if found is None:
        print("Error: ddrescue not found.")
        print(DDRESCUE_HINT)
        return 2
    source = _source(args.source)
    if source is None:
        return 1
    output = Path(args.output).resolve()
    mapfile = Path(args.log).resolve() if args.log else output.with_suffix(".log")
    print(f"Source:  {source}")
    print(f"Output:  {output}")
    print(f"Log:     {mapfile}")
    if args.dry_run:
        print("\n[DRY RUN] No image created.")
        return 0
    prefix = _privileged(found, source)
    if prefix is None:
        print("Error: Reading a block device needs root. Run it again with sudo.")
        return 1
    output.parent.mkdir(parents=True, exist_ok=True)
    as_seen = wsl_path if prefix[0] == "wsl" else str
    runs = passes(prefix, source, as_seen(output), as_seen(mapfile), not args.no_scrape)
    return _image(runs, output, mapfile)


if __name__ == "__main__":
    sys.exit(main())
