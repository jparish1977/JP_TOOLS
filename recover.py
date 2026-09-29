#!/usr/bin/env python3
"""Scan a drive or disk image for recoverable files with photorec.

Selects file types by profile, sorts what photorec recovers into one
directory per extension, and writes recovery_summary.json beside them.

Usage:
    python recover.py <source> <output-dir> [--profile <profile>] [--dry-run]
    python recover.py --list-profiles

Source can be:
    /dev/sdX        a block device
    /dev/sdX1       a partition
    disk.img        a raw disk image (make one with image-disk.py)
    F:              a Windows drive letter, on Windows: read as its physical
                    drive by the Windows build of photorec, as administrator

Profiles: code, art, writing, roms, all (the four together), and full
(everything photorec can find, no filter). --list-profiles shows the
extensions in each.

Examples:
    python recover.py /dev/sdb1 ./recovered --profile code
    python recover.py F: ./recovered --profile art --dry-run
    python recover.py disk.img ./recovered --profile all

Exit status: photorec's, 1 when the source cannot be read as given, 2 when
photorec is not installed. photorec itself exits 0 on a command line it
refused, so check the recovered count, not only the status.
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
import time
from pathlib import Path

# The packages beside this file import when another program loads it by path.
sys.path.insert(0, str(Path(__file__).resolve().parent))

from platformtools.windows import (
    disk_number,
    drive_letter,
    is_admin,
    physical_drive,
    relaunch_elevated,
    usb_drive_advice,
)
from recovery.photorec import (
    PHOTOREC_HINT,
    SUMMARY_NAME,
    find_photorec,
    organize_output,
    photorec_args,
    summarize,
)
from recovery.profiles import FULL, PROFILES, fileopt_command


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument("source", nargs="?", help="Drive, partition, or disk image to scan")
    parser.add_argument("output", nargs="?", help="Output directory for recovered files")
    parser.add_argument("--profile", choices=[*PROFILES, FULL], default="all",
                        help="File type profile to recover (default: all)")
    parser.add_argument("--dry-run", action="store_true",
                        help="Show what would be done without scanning")
    parser.add_argument("--list-profiles", action="store_true",
                        help="List available profiles and their file types")
    parser.add_argument("--no-organize", action="store_true",
                        help="Leave photorec's recup_dir.N layout as it is")
    parser.add_argument("--json", action="store_true", help="Output summary as JSON")
    return parser


def _list_profiles() -> int:
    for name, exts in PROFILES.items():
        print(f"{name}:")
        print(f"  {', '.join(sorted(exts))}")
        print()
    return 0


def _source(arg: str, prefix: list[str]) -> str | None:
    """What photorec reads for ARG, or None (having said why) when it cannot read it.

    A Windows drive letter becomes its physical drive for the Windows build of
    photorec. WSL cannot see a USB drive until it is attached, so for a WSL
    photorec this says how, and stops.
    """
    letter = drive_letter(arg)
    if letter is None:
        return arg
    number = disk_number(letter)
    if number is None:
        print(f"Error: Could not find the physical drive for {arg}")
        return None
    if prefix[0] != "wsl":
        print(f"Using Windows photorec on {physical_drive(number)}")
        return physical_drive(number)
    for line in usb_drive_advice(arg, number):
        print(line)
    print("Or image it first with image-disk.py and recover from the image.")
    return None


def _plan(source: str, output: Path, profile: str, enable_line: str, dry: bool) -> None:
    print(f"{'[DRY RUN] ' if dry else ''}Recovery scan")
    print(f"  Source:    {source}")
    print(f"  Output:    {output}")
    print(f"  Profile:   {profile}")
    if profile != FULL:
        print(f"  File types: {enable_line}")
    print()


def _report(summary: dict[str, object], as_json: bool) -> None:
    if as_json:
        print(json.dumps(summary, indent=2))
        return
    print("\nRecovery complete:")
    print(f"  Files recovered: {summary['files_recovered']}")
    print(f"  Total size:      {summary['total_size_mb']} MB")
    print(f"  Elapsed:         {summary['elapsed_seconds']}s")
    print(f"  Output:          {summary['output']}")


def _recover(args: argparse.Namespace, prefix: list[str], source: str) -> int:
    output = Path(args.output).resolve()
    enable_line, cmd_str = fileopt_command(args.profile, PROFILES.get(args.profile, []))
    _plan(source, output, args.profile, enable_line, args.dry_run)
    if args.dry_run:
        print("Dry run: no scan performed.")
        return 0
    if source.startswith("\\\\.\\") and not is_admin():
        return relaunch_elevated()
    output.mkdir(parents=True, exist_ok=True)
    argv = photorec_args(prefix, source, output, cmd_str)
    print(f"Running: {' '.join(argv)}")
    print("This may take a while...\n")
    start = time.monotonic()
    rc = subprocess.run(argv, check=False).returncode
    elapsed = time.monotonic() - start
    if rc == 0 and not args.no_organize:
        moved = organize_output(output)
        if moved:
            print(f"\nOrganized {moved} recovered files by extension.")
    summary = summarize(args.source, output, args.profile, rc, elapsed)
    _report(summary, args.json)
    (output / SUMMARY_NAME).write_text(json.dumps(summary, indent=2))
    return rc


def main(argv: list[str] | None = None) -> int:
    parser = _parser()
    args = parser.parse_args(argv)
    if args.list_profiles:
        return _list_profiles()
    if not args.source or not args.output:
        parser.error("source and output are required (use --list-profiles to see options)")
    prefix = find_photorec(prefer_windows=drive_letter(args.source) is not None)
    if prefix is None:
        print("Error: photorec not found.")
        print(PHOTOREC_HINT)
        return 2
    source = _source(args.source, prefix)
    if source is None:
        return 1
    return _recover(args, prefix, source)


if __name__ == "__main__":
    sys.exit(main())
