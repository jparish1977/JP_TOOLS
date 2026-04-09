#!/usr/bin/env python
"""
JP_TOOLS/recover.py
Scan a drive or disk image for recoverable files using photorec.

Wraps photorec (from testdisk package) with sensible defaults for
recovering code, art, documents, and other project files.

Usage:
    python recover.py <source> <output-dir> [--profile <profile>] [--dry-run] [--list-profiles]

Source can be:
    /dev/sdX        — raw block device
    /dev/sdX1       — partition
    disk.img        — raw disk image file
    F:              — Windows drive letter (auto-mapped via WSL)

Profiles:
    code        — JS, HTML, PHP, Python, JSON, XML, CSS, SQL, shell scripts
    art         — PNG, JPG, TIFF, PSD, BMP, GIF, SVG, WebP
    writing     — TXT, PDF, DOC/DOCX, ODT, RTF, MD
    all         — All of the above
    full        — Everything photorec can find (no filter)

Examples:
    python recover.py /dev/sdb1 ./recovered --profile code
    python recover.py F: ./recovered --profile art --dry-run
    python recover.py disk.img ./recovered --profile all
"""

import os
import sys
import shutil
import subprocess
import argparse
import json
import ctypes
from pathlib import Path
from datetime import datetime


def is_admin():
    """Check if running with admin privileges on Windows."""
    try:
        return ctypes.windll.shell32.IsUserAnAdmin()
    except AttributeError:
        # Not Windows
        return os.geteuid() == 0


def elevate_and_rerun():
    """Re-run this script as administrator on Windows."""
    if sys.platform != "win32":
        print("Error: This operation requires root. Run with sudo.")
        sys.exit(1)

    print("Requesting administrator privileges...")
    params = " ".join(f'"{a}"' for a in sys.argv)
    ctypes.windll.shell32.ShellExecuteW(
        None, "runas", sys.executable, params, None, 1
    )


PROFILES = {
    "code": [
        "js", "html", "htm", "php", "py", "json", "xml",
        "css", "sql", "sh", "pl", "rb", "java", "c", "h",
        "cpp", "cs", "ts", "jsx", "tsx", "vue", "yaml", "yml",
        "toml", "ini", "conf", "cfg", "env", "gitignore",
    ],
    "art": [
        "png", "jpg", "jpeg", "tiff", "tif", "psd", "bmp",
        "gif", "svg", "webp", "xcf", "raw", "cr2", "nef",
        "ico", "ai", "eps",
    ],
    "writing": [
        "txt", "pdf", "doc", "docx", "odt", "rtf", "md",
        "tex", "epub", "csv", "ods", "xlsx", "xls", "pptx",
        "wpd",
    ],
    "roms": [
        "nes", "snes", "smc", "sfc", "gb", "gbc", "gba", "nds",
        "n64", "z64", "v64", "gen", "md", "sms", "gg",
        "iso", "bin", "cue", "img", "chd", "cso",
        "a26", "a78", "lnx", "pce", "ngp",
        "zip", "7z", "rar", "gz",
    ],
}
PROFILES["all"] = PROFILES["code"] + PROFILES["art"] + PROFILES["writing"] + PROFILES["roms"]


# photorec file family names — maps our extensions to photorec's internal names
# photorec uses its own naming; we generate the config to enable only what we want
PHOTOREC_FAMILIES = {
    "js": "txt", "html": "html", "htm": "html", "php": "php",
    "py": "py", "json": "txt", "xml": "xml", "css": "txt",
    "sql": "txt", "sh": "txt", "pl": "pl", "rb": "rb",
    "java": "java", "c": "txt", "h": "txt", "cpp": "txt",
    "cs": "txt", "ts": "txt", "jsx": "txt", "tsx": "txt",
    "vue": "txt", "yaml": "txt", "yml": "txt", "toml": "txt",
    "ini": "txt", "conf": "txt", "cfg": "txt", "env": "txt",
    "gitignore": "txt",
    "png": "png", "jpg": "jpg", "jpeg": "jpg", "tiff": "tif",
    "tif": "tif", "psd": "psd", "bmp": "bmp", "gif": "gif",
    "svg": "svg", "webp": "webp", "xcf": "xcf", "raw": "raw",
    "cr2": "cr2", "nef": "nef", "ico": "ico", "ai": "ai",
    "eps": "eps",
    "wpd": "wpd",
    "txt": "txt", "pdf": "pdf", "doc": "doc", "docx": "docx",
    "odt": "odt", "rtf": "rtf", "md": "txt", "tex": "tex",
    "epub": "epub", "csv": "csv", "ods": "ods", "xlsx": "xlsx",
    "xls": "xls", "pptx": "pptx",
}


def create_disk_image(source, output_img, dry_run=False):
    """Create a raw disk image from a drive letter or device.

    Uses dd on Linux or PowerShell on Windows to read the raw disk.
    """
    output_path = Path(output_img).resolve()

    # Resolve Windows drive letter to physical drive
    if len(source) <= 3 and source[0].isalpha() and source.endswith(":"):
        letter = source[0].upper()
        result = subprocess.run(
            ["powershell", "-Command",
             f"Get-Partition -DriveLetter {letter} | "
             f"Select-Object -ExpandProperty DiskNumber"],
            capture_output=True, text=True
        )
        if result.returncode != 0 or not result.stdout.strip().isdigit():
            print(f"Error: Could not find physical drive for {source}")
            sys.exit(1)

        disk_num = result.stdout.strip()

        # Get disk size
        result = subprocess.run(
            ["powershell", "-Command",
             f"Get-Disk -Number {disk_num} | Select-Object -ExpandProperty Size"],
            capture_output=True, text=True
        )
        disk_size = int(result.stdout.strip()) if result.returncode == 0 else 0
        size_gb = round(disk_size / (1024**3), 1)

        phys_drive = f"\\\\.\\PhysicalDrive{disk_num}"
        print(f"Source: {source} -> {phys_drive} ({size_gb} GB)")
        print(f"Output: {output_path}")
        print(f"This will create a {size_gb} GB image file.")
        print()

        if dry_run:
            print("[DRY RUN] No image created.")
            return

        # Check for admin privileges
        if not is_admin():
            elevate_and_rerun()
            sys.exit(0)

        # Use WSL dd through the /mnt passthrough
        # Or use PowerShell's raw disk read
        print("Creating raw image with dd...")
        print(f"This will take a while for {size_gb} GB...\n")

        # dd via WSL, reading the Windows physical drive
        wsl_output = output_path
        if str(wsl_output)[1] == ":":
            wsl_output = f"/mnt/{str(output_path)[0].lower()}/{str(output_path)[2:].replace(chr(92), '/')}"

        cmd = [
            "wsl", "sudo", "dd",
            f"if=/dev/sd{chr(ord('a') + int(disk_num))}",
            f"of={wsl_output}",
            "bs=4M", "status=progress",
        ]
        print(f"Running: {' '.join(cmd)}")
        result = subprocess.run(cmd)

        if result.returncode == 0:
            actual_size = output_path.stat().st_size if output_path.exists() else 0
            print(f"\nImage created: {output_path} ({round(actual_size / (1024**3), 1)} GB)")
            print("Now recover files with:")
            print(f"  python recover.py {output_path} ./recovered --profile all")
        else:
            print(f"\ndd failed with exit code {result.returncode}")
            print("You may need to run as administrator or use a Linux live USB.")
        sys.exit(result.returncode)
    else:
        # Linux device — straightforward dd
        print(f"Source: {source}")
        print(f"Output: {output_path}")

        if dry_run:
            print("[DRY RUN] No image created.")
            return

        cmd = ["dd", f"if={source}", f"of={str(output_path)}", "bs=4M", "status=progress"]
        print(f"Running: {' '.join(cmd)}")
        result = subprocess.run(cmd)
        sys.exit(result.returncode)


def find_photorec(prefer_native=False):
    """Find photorec binary — check native Windows, then WSL."""
    # Check for native Windows photorec (from testdisk package)
    # Look in common locations
    native_paths = [
        Path.home() / "tools" / "testdisk" / "testdisk-7.1" / "photorec_win.exe",
        Path.home() / "tools" / "testdisk" / "photorec_win.exe",
    ]
    for p in native_paths:
        if p.exists():
            if prefer_native:
                return [str(p)], "native"
            # Still found it, save as fallback
            native = [str(p)]
            break
    else:
        native = None

    # Direct path (Linux or already on PATH)
    path = shutil.which("photorec")
    if path:
        return [path], "native"

    # Try WSL
    result = subprocess.run(
        ["wsl", "which", "photorec"],
        capture_output=True, text=True
    )
    if result.returncode == 0 and result.stdout.strip():
        return ["wsl", result.stdout.strip()], "wsl"

    # Fall back to native Windows if found earlier
    if native:
        return native, "native"

    return None, None


def resolve_source(source):
    """Resolve source to a path photorec understands.

    Handles Windows drive letters (F:) by mapping to /dev/ via WSL.
    """
    # Windows drive letter
    if len(source) <= 3 and source[0].isalpha() and source.endswith(":"):
        letter = source[0].lower()

        # Try lsblk first (works for native WSL block devices)
        result = subprocess.run(
            ["wsl", "bash", "-c",
             f"lsblk -rno NAME,MOUNTPOINT 2>/dev/null | grep '/mnt/{letter}' | head -1 | cut -d' ' -f1"],
            capture_output=True, text=True
        )
        if result.returncode == 0 and result.stdout.strip():
            dev = result.stdout.strip()
            return f"/dev/{dev}"

        # USB drives on Windows aren't visible as block devices in WSL.
        # We need to attach them via usbipd or create a raw image first.
        # Get the physical drive number from Windows.
        result = subprocess.run(
            ["powershell", "-Command",
             f"Get-Partition -DriveLetter {letter.upper()} | "
             f"Select-Object -ExpandProperty DiskNumber"],
            capture_output=True, text=True
        )
        if result.returncode == 0 and result.stdout.strip().isdigit():
            disk_num = result.stdout.strip()
            phys_drive = f"\\\\.\\PhysicalDrive{disk_num}"
            print(f"Detected {source} as {phys_drive} (disk {disk_num})")
            print()
            print("WSL cannot directly access USB block devices.")
            print("Options:")
            print("  1. Create a raw image first:")
            print(f"     python recover.py --image {source} drive_image.img")
            print("     python recover.py drive_image.img ./recovered --profile all")
            print("  2. Attach the USB drive to WSL (requires usbipd):")
            print("     usbipd list")
            print("     usbipd bind --busid <BUSID>")
            print("     usbipd attach --wsl --busid <BUSID>")
            print("     Then use /dev/sdX directly")
            print("  3. Boot a Linux live USB and run photorec natively")
            sys.exit(1)

        print(f"Warning: Could not auto-detect device for {source}")
        print("Try specifying the device directly (e.g., /dev/sdb1)")
        sys.exit(1)

    return source


def run_photorec(photorec_cmd, source, output_dir, profile_name, extensions, dry_run=False):
    """Run photorec with the given parameters."""
    output_path = Path(output_dir).resolve()
    output_path.mkdir(parents=True, exist_ok=True)

    # For WSL, convert Windows output path to /mnt/x/... format
    if photorec_cmd[0] == "wsl":
        win_path = str(output_path)
        if win_path[1] == ":":
            wsl_path = f"/mnt/{win_path[0].lower()}/{win_path[2:].replace(chr(92), '/')}"
        else:
            wsl_path = win_path.replace("\\", "/")
        photorec_output = wsl_path
    else:
        photorec_output = str(output_path)

    if profile_name == "full":
        enable_line = "everything,enable"
    else:
        families = set()
        for ext in extensions:
            if ext in PHOTOREC_FAMILIES:
                families.add(PHOTOREC_FAMILIES[ext])
        enable_line = ",".join(sorted(families)) + ",enable"

    print(f"{'[DRY RUN] ' if dry_run else ''}Recovery scan")
    print(f"  Source:    {source}")
    print(f"  Output:    {output_path}")
    print(f"  Profile:   {profile_name}")
    if profile_name != "full":
        print(f"  File types: {enable_line}")
    print()

    if dry_run:
        print("Dry run — no scan performed.")
        print(f"Would run photorec on {source}")
        print(f"Output would go to {output_path}")
        return 0

    # Check for admin privileges if accessing physical drives
    if source.startswith("\\\\.\\Physical") and not is_admin():
        elevate_and_rerun()
        sys.exit(0)

    # Build photorec command
    # photorec /d <output> /cmd <source> fileopt,everything,disable,<families>,enable,search
    if profile_name == "full":
        cmd_str = "fileopt,everything,enable,search"
    else:
        cmd_str = f"fileopt,everything,disable,{enable_line},search"

    cmd = photorec_cmd + [
        "/d", photorec_output + "/",
        "/cmd", source,
        cmd_str,
    ]

    print(f"Running: {' '.join(cmd)}")
    print("This may take a while...\n")

    result = subprocess.run(cmd)
    return result.returncode


def organize_output(output_dir):
    """Organize photorec output by file type."""
    output_path = Path(output_dir)
    if not output_path.exists():
        return

    # photorec creates recup_dir.1, recup_dir.2, etc.
    moved = 0
    for recup_dir in output_path.glob("recup_dir.*"):
        for f in recup_dir.iterdir():
            if f.is_file():
                ext = f.suffix.lower().lstrip(".")
                if not ext:
                    ext = "unknown"
                dest_dir = output_path / ext
                dest_dir.mkdir(exist_ok=True)
                dest = dest_dir / f.name
                f.rename(dest)
                moved += 1
        # Remove empty recup_dir
        try:
            recup_dir.rmdir()
        except OSError:
            pass

    if moved:
        print(f"\nOrganized {moved} recovered files by extension.")


def main():
    parser = argparse.ArgumentParser(
        description="Recover files from a drive or disk image using photorec.",
    )
    parser.add_argument("source", nargs="?", help="Drive, partition, or disk image to scan")
    parser.add_argument("output", nargs="?", help="Output directory for recovered files")
    parser.add_argument("--profile", choices=list(PROFILES.keys()) + ["full"], default="all",
                        help="File type profile to recover (default: all)")
    parser.add_argument("--dry-run", action="store_true",
                        help="Show what would be done without scanning")
    parser.add_argument("--list-profiles", action="store_true",
                        help="List available profiles and their file types")
    parser.add_argument("--no-organize", action="store_true",
                        help="Don't reorganize output by file type")
    parser.add_argument("--json", action="store_true",
                        help="Output summary as JSON")
    parser.add_argument("--image", metavar="OUTPUT_IMG",
                        help="Create a raw disk image instead of recovering files. "
                             "Source must be a drive letter or device. "
                             "Example: --image drive.img F:")
    args = parser.parse_args()

    if args.list_profiles:
        for name, exts in PROFILES.items():
            print(f"{name}:")
            print(f"  {', '.join(sorted(exts))}")
            print()
        return

    if args.image:
        if not args.source:
            parser.error("source is required with --image")
        # --image OUTPUT_IMG SOURCE — source is the drive, image is the output file
        create_disk_image(args.source, args.image, args.dry_run)
        return

    if not args.source or not args.output:
        parser.error("source and output are required (use --list-profiles to see options)")

    # Determine if source is a Windows drive letter — prefer native photorec
    is_windows_drive = (len(args.source) <= 3 and args.source[0].isalpha()
                        and args.source.endswith(":"))

    # Find photorec
    photorec_cmd, photorec_mode = find_photorec(prefer_native=is_windows_drive)
    if not photorec_cmd:
        print("Error: photorec not found.")
        print("Install with:")
        print("  Ubuntu/Debian: sudo apt install testdisk")
        print("  macOS: brew install testdisk")
        print("  Windows: download from https://www.cgsecurity.org/wiki/TestDisk_Download")
        print("           extract to ~/tools/testdisk/")
        print("  Windows WSL: wsl sudo apt install testdisk")
        sys.exit(2)

    # For Windows drives, use native photorec with \\.\PhysicalDriveN
    if is_windows_drive and photorec_mode == "native":
        letter = args.source[0].upper()
        result = subprocess.run(
            ["powershell", "-Command",
             f"Get-Partition -DriveLetter {letter} | "
             f"Select-Object -ExpandProperty DiskNumber"],
            capture_output=True, text=True
        )
        if result.returncode == 0 and result.stdout.strip().isdigit():
            disk_num = result.stdout.strip()
            source = f"\\\\.\\PhysicalDrive{disk_num}"
            print(f"Using native Windows photorec on {source}")
        else:
            print(f"Error: Could not find physical drive for {args.source}")
            sys.exit(1)
    else:
        source = resolve_source(args.source)

    extensions = PROFILES.get(args.profile, [])

    start = datetime.now()

    rc = run_photorec(
        photorec_cmd, source, args.output,
        args.profile, extensions, args.dry_run
    )

    elapsed = (datetime.now() - start).total_seconds()

    if rc == 0 and not args.dry_run and not args.no_organize:
        organize_output(args.output)

    # Summary
    if not args.dry_run:
        output_path = Path(args.output)
        file_count = sum(1 for _ in output_path.rglob("*") if _.is_file())
        total_size = sum(f.stat().st_size for f in output_path.rglob("*") if f.is_file())

        summary = {
            "source": args.source,
            "output": str(output_path),
            "profile": args.profile,
            "files_recovered": file_count,
            "total_size_bytes": total_size,
            "total_size_mb": round(total_size / (1024 * 1024), 1),
            "elapsed_seconds": round(elapsed, 1),
            "exit_code": rc,
            "timestamp": datetime.now().isoformat(),
        }

        if args.json:
            print(json.dumps(summary, indent=2))
        else:
            print("\nRecovery complete:")
            print(f"  Files recovered: {file_count}")
            print(f"  Total size:      {summary['total_size_mb']} MB")
            print(f"  Elapsed:         {summary['elapsed_seconds']}s")
            print(f"  Output:          {output_path}")

        # Write summary to output dir
        summary_path = output_path / "recovery_summary.json"
        with open(summary_path, "w") as f:
            json.dump(summary, f, indent=2)

    sys.exit(rc)


if __name__ == "__main__":
    main()
