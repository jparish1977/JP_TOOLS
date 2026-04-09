#!/usr/bin/env python
"""
JP_TOOLS/undelete.py
Recover deleted files from NTFS or ext3/ext4 filesystems.

Unlike photorec (which carves raw data by signature), this tool uses
filesystem metadata to find recently deleted files with their original
names and directory structure intact. Works best on recently deleted files
before the space has been overwritten.

Usage:
    python undelete.py <device-or-image> <output-dir> [--type ntfs|ext] [--list-only] [--match <pattern>]

Examples:
    python undelete.py /dev/sdb1 ./recovered --type ntfs
    python undelete.py /dev/sdb1 ./recovered --type ntfs --match "*.js"
    python undelete.py disk.img ./recovered --type ext
    python undelete.py /dev/sdb1 ./recovered --list-only
"""

import sys
import shutil
import subprocess
import argparse
from pathlib import Path
from datetime import datetime


def find_tool(name):
    """Find a tool, checking local installs, PATH, then WSL."""
    # Check native Windows tool locations
    if sys.platform == 'win32':
        win_paths = [
            Path.home() / "tools" / "sleuthkit" / "sleuthkit-4.14.0-win32" / "bin" / f"{name}.exe",
            Path.home() / "tools" / "sleuthkit" / "bin" / f"{name}.exe",
        ]
        for p in win_paths:
            if p.exists():
                return [str(p)]

    # Check PATH
    path = shutil.which(name)
    if path:
        return [path]

    # Try WSL only on Windows
    if sys.platform == 'win32':
        try:
            result = subprocess.run(
                ["wsl", "which", name],
                capture_output=True, text=True
            )
            if result.returncode == 0 and result.stdout.strip():
                return ["wsl", result.stdout.strip()]
        except FileNotFoundError:
            pass

    return None


def win_to_wsl_path(win_path):
    p = str(win_path)
    if len(p) >= 2 and p[1] == ":":
        return f"/mnt/{p[0].lower()}/{p[2:].replace(chr(92), '/')}"
    return p.replace("\\", "/")


def detect_fs_type(source):
    """Try to detect filesystem type."""
    cmd = find_tool("blkid")
    if not cmd:
        return None

    if cmd[0] == "wsl":
        cmd = ["wsl", "sudo", "blkid", source]
    else:
        cmd = cmd + [source]

    result = subprocess.run(cmd, capture_output=True, text=True)
    output = result.stdout.lower()

    if "ntfs" in output:
        return "ntfs"
    elif "ext4" in output or "ext3" in output:
        return "ext"
    return None


def list_deleted_ntfs(source, match=None):
    """List deleted files on NTFS using ntfsundelete."""
    tool = find_tool("ntfsundelete")
    if not tool:
        print("Error: ntfsundelete not found. Install ntfs-3g.")
        sys.exit(2)

    cmd = tool[:]
    if tool[0] == "wsl":
        cmd = ["wsl", "sudo", "ntfsundelete", source, "--scan"]
    else:
        cmd = tool + [source, "--scan"]

    if match:
        cmd.extend(["--match", match])

    print(f"Scanning for deleted files on {source}...")
    result = subprocess.run(cmd, capture_output=True, text=True)
    print(result.stdout)
    if result.stderr:
        print(result.stderr, file=sys.stderr)
    return result.returncode


def recover_ntfs(source, output_dir, match=None):
    """Recover deleted files from NTFS using ntfsundelete."""
    tool = find_tool("ntfsundelete")
    if not tool:
        print("Error: ntfsundelete not found. Install ntfs-3g.")
        sys.exit(2)

    output_path = Path(output_dir).resolve()
    output_path.mkdir(parents=True, exist_ok=True)

    if tool[0] == "wsl":
        wsl_output = win_to_wsl_path(output_path)
        cmd = ["wsl", "sudo", "ntfsundelete", source,
               "--undelete", "--destination", wsl_output]
    else:
        cmd = tool + [source, "--undelete",
                      "--destination", str(output_path)]

    if match:
        cmd.extend(["--match", match])
    else:
        cmd.extend(["--match", "*"])

    print(f"Recovering deleted files from {source}...")
    print(f"Output: {output_path}")
    print(f"Running: {' '.join(cmd)}")
    result = subprocess.run(cmd)
    return result.returncode


def list_deleted_ext(source, match=None):
    """List deleted files on ext3/ext4 using sleuthkit fls."""
    tool = find_tool("fls")
    if not tool:
        print("Error: fls not found. Install sleuthkit.")
        sys.exit(2)

    if tool[0] == "wsl":
        cmd = ["wsl", "sudo", "fls", "-r", "-d", source]
    else:
        cmd = tool + ["-r", "-d", source]

    print(f"Scanning for deleted files on {source}...")
    result = subprocess.run(cmd, capture_output=True, text=True)

    lines = result.stdout.strip().split("\n") if result.stdout.strip() else []

    if match:
        import fnmatch
        lines = [entry for entry in lines if fnmatch.fnmatch(entry.lower(), f"*{match.lower()}*")]

    for line in lines[:100]:
        print(line)

    if len(lines) > 100:
        print(f"... and {len(lines) - 100} more")

    print(f"\nTotal deleted entries found: {len(lines)}")
    return result.returncode


def recover_ext(source, output_dir, match=None):
    """Recover deleted files from ext3/ext4 using extundelete."""
    tool = find_tool("extundelete")
    if not tool:
        print("Error: extundelete not found. Install extundelete.")
        sys.exit(2)

    output_path = Path(output_dir).resolve()
    output_path.mkdir(parents=True, exist_ok=True)

    if tool[0] == "wsl":
        wsl_output = win_to_wsl_path(output_path)
        cmd = ["wsl", "sudo", "extundelete", source,
               "--restore-all", "--output-dir", wsl_output]
    else:
        cmd = tool + [source, "--restore-all",
                      "--output-dir", str(output_path)]

    print(f"Recovering deleted files from {source}...")
    print(f"Output: {output_path}")
    print(f"Running: {' '.join(cmd)}")
    result = subprocess.run(cmd)
    return result.returncode


def main():
    parser = argparse.ArgumentParser(
        description="Recover deleted files from NTFS or ext3/ext4 filesystems.",
    )
    parser.add_argument("source", help="Device or disk image to scan")
    parser.add_argument("output", nargs="?", help="Output directory for recovered files")
    parser.add_argument("--type", choices=["ntfs", "ext"],
                        help="Filesystem type (auto-detected if not specified)")
    parser.add_argument("--list-only", action="store_true",
                        help="List deleted files without recovering them")
    parser.add_argument("--match", metavar="PATTERN",
                        help="Filename pattern to match (e.g., '*.js', '*.psd')")
    args = parser.parse_args()

    if not args.list_only and not args.output:
        parser.error("output directory is required unless using --list-only")

    # Detect filesystem type
    fs_type = args.type
    if not fs_type:
        fs_type = detect_fs_type(args.source)
        if not fs_type:
            print("Could not auto-detect filesystem type. Use --type ntfs or --type ext")
            sys.exit(1)
        print(f"Detected filesystem: {fs_type}")

    start = datetime.now()

    if args.list_only:
        if fs_type == "ntfs":
            rc = list_deleted_ntfs(args.source, args.match)
        else:
            rc = list_deleted_ext(args.source, args.match)
    else:
        if fs_type == "ntfs":
            rc = recover_ntfs(args.source, args.output, args.match)
        else:
            rc = recover_ext(args.source, args.output, args.match)

    elapsed = (datetime.now() - start).total_seconds()
    print(f"\nElapsed: {round(elapsed, 1)}s")
    sys.exit(rc)


if __name__ == "__main__":
    main()
