#!/usr/bin/env python3
"""Recover deleted files from NTFS or ext3/ext4 filesystems.

Unlike photorec (which carves raw data by signature), this uses filesystem
metadata to find recently deleted files with their original names and
directory structure. Works best on recently deleted files, before the space
has been overwritten.

NTFS uses ntfsundelete (ntfs-3g): listing and recovery both honour --match,
a glob such as "*.js". ext3/ext4 lists with fls (sleuthkit), filtered by
--match, and recovers with extundelete, which restores everything and takes
no pattern: --match with an ext recovery is refused rather than ignored.

Usage:
    python undelete.py <device-or-image> <output-dir> [--type ntfs|ext] [--list-only] [--match <pattern>]

Examples:
    python undelete.py /dev/sdb1 ./recovered --type ntfs
    python undelete.py /dev/sdb1 ./recovered --type ntfs --match "*.js"
    python undelete.py disk.img ./recovered --type ext
    python undelete.py /dev/sdb1 ./recovered --list-only

Exit status: the recovery tool's, 1 when the filesystem cannot be detected
or the arguments cannot be honoured, 2 when a required tool is missing.
"""

import argparse
import fnmatch
import subprocess
import sys
import time
from pathlib import Path

# The packages beside this file import when another program loads it by path.
sys.path.insert(0, str(Path(__file__).resolve().parent))

from platformtools.tools import ToolMissingError, find_tool
from platformtools.windows import wsl_path

LISTED = 100

HINTS = {
    "ntfsundelete": "install ntfs-3g",
    "fls": "install sleuthkit",
    "extundelete": "install extundelete",
    "blkid": "install util-linux",
}


def _windows_sleuthkit(name: str) -> tuple[Path, ...]:
    """Where the sleuthkit Windows download usually lands; it puts nothing on PATH."""
    root = Path.home() / "tools" / "sleuthkit"
    return (root / "sleuthkit-4.14.0-win32" / "bin" / f"{name}.exe", root / "bin" / f"{name}.exe")


def tool(name: str) -> list[str]:
    """NAME's argv prefix, under sudo inside WSL. Raises ToolMissingError."""
    prefix = find_tool(name, _windows_sleuthkit(name))
    if prefix is None:
        raise ToolMissingError(name, HINTS.get(name, f"install {name}"))
    return ["wsl", "sudo", *prefix[1:]] if prefix[0] == "wsl" else prefix


def _path_for(prefix: list[str], path: Path) -> str:
    return wsl_path(path) if prefix[0] == "wsl" else str(path)


def detect_fs_type(source: str) -> str | None:
    """"ntfs" or "ext" from blkid, or None when blkid is missing or says neither."""
    try:
        prefix = tool("blkid")
    except ToolMissingError:
        # reason: None is "could not detect", which main() reports and asks for --type
        return None
    output = subprocess.run([*prefix, source], capture_output=True, text=True, check=False).stdout.lower()
    if "ntfs" in output:
        return "ntfs"
    return "ext" if "ext4" in output or "ext3" in output else None


def ntfs_command(source: str, output: Path | None, match: str | None) -> list[str]:
    """ntfsundelete: a scan (OUTPUT None) or a recovery into OUTPUT."""
    prefix = tool("ntfsundelete")
    if output is None:
        return [*prefix, source, "--scan", *(["--match", match] if match else [])]
    return [*prefix, source, "--undelete", "--destination", _path_for(prefix, output),
            "--match", match or "*"]


def ext_recover_command(source: str, output: Path) -> list[str]:
    prefix = tool("extundelete")
    return [*prefix, source, "--restore-all", "--output-dir", _path_for(prefix, output)]


def list_ext(source: str, match: str | None) -> int:
    """Deleted entries on ext3/ext4 from fls, filtered by MATCH; the first 100 printed."""
    result = subprocess.run([*tool("fls"), "-r", "-d", source], capture_output=True, text=True, check=False)
    lines = result.stdout.strip().splitlines()
    if match:
        lines = [e for e in lines if fnmatch.fnmatch(e.lower(), f"*{match.lower()}*")]
    for line in lines[:LISTED]:
        print(line)
    if len(lines) > LISTED:
        print(f"... and {len(lines) - LISTED} more")
    print(f"\nTotal deleted entries found: {len(lines)}")
    return result.returncode


def _run(cmd: list[str], capture: bool) -> int:
    print(f"Running: {' '.join(cmd)}")
    result = subprocess.run(cmd, capture_output=capture, text=True, check=False)
    if capture:
        print(result.stdout)
        if result.stderr:
            print(result.stderr, file=sys.stderr)
    return result.returncode


def undelete(args: argparse.Namespace, fs_type: str) -> int:
    """List or recover, for FS_TYPE. Raises ToolMissingError."""
    output = None if args.list_only else Path(args.output).resolve()
    print(f"{'Scanning' if output is None else 'Recovering'} deleted files on {args.source}...")
    if output is not None:
        output.mkdir(parents=True, exist_ok=True)
        print(f"Output: {output}")
    if fs_type == "ntfs":
        return _run(ntfs_command(args.source, output, args.match), capture=output is None)
    if output is None:
        return list_ext(args.source, args.match)
    return _run(ext_recover_command(args.source, output), capture=False)


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("source", help="Device or disk image to scan")
    parser.add_argument("output", nargs="?", help="Output directory for recovered files")
    parser.add_argument("--type", choices=["ntfs", "ext"],
                        help="Filesystem type (auto-detected if not specified)")
    parser.add_argument("--list-only", action="store_true",
                        help="List deleted files without recovering them")
    parser.add_argument("--match", metavar="PATTERN",
                        help="Filename pattern to match (e.g. '*.js', '*.psd')")
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = _parser()
    args = parser.parse_args(argv)
    if not args.list_only and not args.output:
        parser.error("output directory is required unless using --list-only")
    fs_type = args.type or detect_fs_type(args.source)
    if not fs_type:
        print("Could not auto-detect filesystem type. Use --type ntfs or --type ext")
        return 1
    if fs_type == "ext" and args.match and not args.list_only:
        print("Error: extundelete restores everything and takes no pattern; "
              "use --list-only with --match to find files, or drop --match")
        return 1
    start = time.monotonic()
    try:
        rc = undelete(args, fs_type)
    except ToolMissingError as exc:
        print(f"Error: {exc.name} not found; {exc.hint}.")
        return 2
    print(f"\nElapsed: {time.monotonic() - start:.1f}s")
    return rc


if __name__ == "__main__":
    sys.exit(main())
