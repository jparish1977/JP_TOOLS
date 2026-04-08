#!/usr/bin/env python
"""
JP_TOOLS/image-disk.py
Create a forensic disk image using ddrescue.

ddrescue is fault-tolerant — it handles bad sectors, retries intelligently,
and keeps a log so you can resume interrupted imaging.

Usage:
    python image-disk.py <source> <output.img> [--log <logfile>] [--dry-run]

Source can be:
    /dev/sdX        — block device (Linux)
    F:              — Windows drive letter (runs via WSL)

Examples:
    python image-disk.py /dev/sdb ~/images/drive.img
    python image-disk.py F: D:/images/drive.img
    python image-disk.py /dev/sdb ~/images/drive.img --log ~/images/drive.log
"""

import os
import sys
import shutil
import subprocess
import argparse
import ctypes
from pathlib import Path
from datetime import datetime


def is_admin():
    try:
        return ctypes.windll.shell32.IsUserAnAdmin()
    except AttributeError:
        return os.geteuid() == 0


def win_to_wsl_path(win_path):
    """Convert C:\\foo\\bar to /mnt/c/foo/bar."""
    p = str(win_path)
    if len(p) >= 2 and p[1] == ":":
        return f"/mnt/{p[0].lower()}/{p[2:].replace(chr(92), '/')}"
    return p.replace("\\", "/")


def resolve_source(source):
    """Resolve Windows drive letter to WSL device path."""
    if len(source) <= 3 and source[0].isalpha() and source.endswith(":"):
        letter = source[0].upper()
        result = subprocess.run(
            ["powershell", "-Command",
             f"Get-Partition -DriveLetter {letter} | "
             f"Select-Object -ExpandProperty DiskNumber"],
            capture_output=True, text=True
        )
        if result.returncode == 0 and result.stdout.strip().isdigit():
            disk_num = result.stdout.strip()
            # WSL maps Windows disks — but USB drives aren't usually visible.
            # We need usbipd or to use the Windows \\.\PhysicalDriveN path.
            print(f"Detected {source} as PhysicalDrive{disk_num}")
            print()
            print("For WSL ddrescue, the drive must be attached to WSL via usbipd,")
            print("or you can use the native Linux boot approach.")
            print()
            print("To attach via usbipd:")
            print("  usbipd list")
            print("  usbipd bind --busid <BUSID>")
            print("  usbipd attach --wsl --busid <BUSID>")
            print("Then run again with the /dev/sdX device.")
            print()
            print("Or boot a Linux live USB and run:")
            print(f"  ddrescue /dev/sdX {source}.img {source}.log")
            sys.exit(1)
        else:
            print(f"Error: Could not find physical drive for {source}")
            sys.exit(1)

    return source


def find_ddrescue():
    """Find ddrescue binary."""
    for name in ("ddrescue", "gddrescue"):
        path = shutil.which(name)
        if path:
            return [path]

    # Try WSL
    result = subprocess.run(
        ["wsl", "which", "ddrescue"],
        capture_output=True, text=True
    )
    if result.returncode == 0 and result.stdout.strip():
        return ["wsl", result.stdout.strip()]

    return None


def main():
    parser = argparse.ArgumentParser(
        description="Create a forensic disk image using ddrescue.",
    )
    parser.add_argument("source", help="Device or drive letter to image")
    parser.add_argument("output", help="Output image file path")
    parser.add_argument("--log", help="ddrescue log file (enables resume). "
                        "Defaults to <output>.log")
    parser.add_argument("--dry-run", action="store_true",
                        help="Show what would be done without imaging")
    parser.add_argument("--no-scrape", action="store_true",
                        help="Skip the scraping pass (faster, may miss bad sectors)")
    args = parser.parse_args()

    ddrescue = find_ddrescue()
    if not ddrescue:
        print("Error: ddrescue not found.")
        print("Install with:")
        print("  Ubuntu/Debian: sudo apt install gddrescue")
        print("  macOS: brew install ddrescue")
        sys.exit(2)

    source = resolve_source(args.source)
    output = Path(args.output).resolve()
    log_file = Path(args.log).resolve() if args.log else output.with_suffix(".log")

    output.parent.mkdir(parents=True, exist_ok=True)

    # Convert paths for WSL if needed
    if ddrescue[0] == "wsl":
        out_path = win_to_wsl_path(output)
        log_path = win_to_wsl_path(log_file)
    else:
        out_path = str(output)
        log_path = str(log_file)

    print(f"Source:  {source}")
    print(f"Output:  {output}")
    print(f"Log:     {log_file}")
    print()

    if args.dry_run:
        print("[DRY RUN] No image created.")
        return

    # Check if we need elevation (Linux)
    if source.startswith("/dev/") and not is_admin():
        if ddrescue[0] == "wsl":
            ddrescue = ["wsl", "sudo", "ddrescue"]
        else:
            print("Error: Reading block devices requires root. Run with sudo.")
            sys.exit(1)

    start = datetime.now()

    # First pass — fast copy of good sectors
    cmd = ddrescue + ["-f", "-n", source, out_path, log_path]
    print(f"Pass 1 (fast copy): {' '.join(cmd)}")
    result = subprocess.run(cmd)

    if result.returncode != 0:
        print(f"\nFirst pass failed with exit code {result.returncode}")
        sys.exit(result.returncode)

    # Second pass — scrape bad sectors (retry)
    if not args.no_scrape:
        cmd = ddrescue + ["-f", "-d", "-r", "3", source, out_path, log_path]
        print(f"\nPass 2 (scrape): {' '.join(cmd)}")
        result = subprocess.run(cmd)

    elapsed = (datetime.now() - start).total_seconds()
    size = output.stat().st_size if output.exists() else 0

    print("\nImaging complete:")
    print(f"  Size:    {round(size / (1024**3), 1)} GB")
    print(f"  Elapsed: {round(elapsed, 1)}s")
    print(f"  Image:   {output}")
    print(f"  Log:     {log_file}")
    print("\nRecover files with:")
    print(f"  python recover.py {output} ./recovered --profile all")


if __name__ == "__main__":
    main()
