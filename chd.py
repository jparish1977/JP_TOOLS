#!/usr/bin/env python
"""
JP_TOOLS/chd.py
Wrapper for chdman — compressed hunk of data management.

Create, verify, extract, diff, and manage CHD archives for disc images,
raw disk dumps, and general compressed storage.

Usage:
    python chd.py create <input> <output.chd> [options]
    python chd.py verify <file.chd>
    python chd.py extract <file.chd> <output>
    python chd.py info <file.chd>
    python chd.py delta <parent.chd> <child-input> <child.chd>
    python chd.py merge <child.chd> <output.chd>
    python chd.py batch-create <directory> [--format <ext>] [--delete-originals]
    python chd.py batch-verify <directory>
    python chd.py compare <file1.chd> <file2.chd>
    python chd.py convert <input.chd> <output.chd> [--compression <type>]

Input formats:  .img, .bin, .iso, .raw, .cue, .gdi, .toc
Output:         .chd (compressed hunk of data)

Examples:
    python chd.py create game.iso game.chd
    python chd.py create disk.img disk.chd --compression lzma
    python chd.py create game.cue game.chd                      # CD with cue sheet
    python chd.py create game.gdi game.chd                      # Dreamcast GDI
    python chd.py delta base.chd updated.img updated.chd        # Delta/child CHD
    python chd.py batch-create ~/roms/psx --format iso           # Convert all ISOs
    python chd.py batch-verify ~/roms/chd                        # Verify all CHDs
    python chd.py info game.chd                                  # Show metadata
"""

import sys
import shutil
import subprocess
import argparse
import json
from pathlib import Path
from datetime import datetime


COMPRESSION_PRESETS = {
    "default": [],  # let chdman decide
    "lzma": ["--compression", "lzma"],
    "zlib": ["--compression", "zlib"],
    "flac": ["--compression", "flac"],  # best for audio tracks
    "none": ["--compression", "none"],
    "best": ["--compression", "lzma"],
    "fast": ["--compression", "zlib"],
}

# File extensions that can be converted to CHD
CONVERTIBLE_EXTENSIONS = {
    ".iso", ".img", ".bin", ".raw",  # raw disc/disk images
    ".cue",  # CD cue sheets (will include referenced .bin files)
    ".gdi",  # Dreamcast disc images
    ".toc",  # CD TOC files
}


def find_chdman():
    """Find chdman binary — check native Windows, PATH, then WSL."""
    # Native Windows locations
    if sys.platform == 'win32':
        native_paths = [
            Path.home() / "tools" / "mame" / "chdman.exe",
            Path.home() / "tools" / "chdman.exe",
        ]
        for p in native_paths:
            if p.exists():
                return str(p)

    # PATH
    path = shutil.which("chdman")
    if path:
        return path

    # Try WSL
    if sys.platform == 'win32':
        try:
            result = subprocess.run(
                ["wsl", "which", "chdman"],
                capture_output=True, text=True
            )
            if result.returncode == 0 and result.stdout.strip():
                return "wsl:" + result.stdout.strip()
        except FileNotFoundError:
            pass

    return None


def run_chdman(args, chdman_path=None):
    """Run chdman with the given arguments."""
    if chdman_path is None:
        chdman_path = find_chdman()

    if chdman_path is None:
        print("Error: chdman not found.")
        print("Install with:")
        print("  Ubuntu/Debian: sudo apt install mame-tools")
        print("  macOS: brew install rom-tools")
        print("  Windows: download MAME and add to PATH")
        sys.exit(2)

    if chdman_path.startswith("wsl:"):
        cmd = ["wsl", chdman_path[4:]] + args
    else:
        cmd = [chdman_path] + args

    result = subprocess.run(cmd, capture_output=True, text=True)
    return result


def cmd_create(args):
    """Create a CHD from an input file."""
    input_path = Path(args.input).resolve()
    output_path = Path(args.output).resolve()

    if not input_path.exists():
        print(f"Error: Input file not found: {input_path}")
        sys.exit(1)

    ext = input_path.suffix.lower()

    # Determine chdman subcommand based on input type
    if ext in (".cue", ".gdi", ".toc"):
        subcmd = "createcd"
        input_flag = "--input"
    elif ext in (".img", ".bin", ".iso", ".raw"):
        subcmd = "createraw"
        input_flag = "--input"
        # Raw needs hunk size — default 4096 for hard drives, CD sector for discs
        if not args.hunk_size:
            args.hunk_size = "2048"  # CD sector size default
    else:
        print(f"Warning: Unknown extension {ext}, treating as raw image")
        subcmd = "createraw"
        input_flag = "--input"
        if not args.hunk_size:
            args.hunk_size = "4096"

    cmd = [subcmd, input_flag, str(input_path), "--output", str(output_path)]

    if args.compression and args.compression in COMPRESSION_PRESETS:
        cmd.extend(COMPRESSION_PRESETS[args.compression])
    elif args.compression:
        cmd.extend(["--compression", args.compression])

    if args.hunk_size:
        cmd.extend(["--hunksize", str(args.hunk_size)])

    if args.force:
        cmd.append("--force")

    input_size = input_path.stat().st_size
    print(f"Creating CHD from: {input_path} ({round(input_size / (1024**3), 2)} GB)")
    print(f"Output: {output_path}")
    print(f"Mode: {subcmd}")

    if args.dry_run:
        print(f"[DRY RUN] Would run: chdman {' '.join(cmd)}")
        return

    result = run_chdman(cmd)
    print(result.stdout)
    if result.stderr:
        print(result.stderr)

    if result.returncode == 0 and output_path.exists():
        output_size = output_path.stat().st_size
        ratio = round(output_size / input_size * 100, 1) if input_size > 0 else 0
        saved = input_size - output_size
        print(f"\nCreated: {output_path}")
        print(f"  Input:  {round(input_size / (1024**2), 1)} MB")
        print(f"  Output: {round(output_size / (1024**2), 1)} MB")
        print(f"  Ratio:  {ratio}% ({round(saved / (1024**2), 1)} MB saved)")

    sys.exit(result.returncode)


def cmd_verify(args):
    """Verify a CHD file's integrity."""
    chd_path = Path(args.file).resolve()

    if not chd_path.exists():
        print(f"Error: File not found: {chd_path}")
        sys.exit(1)

    print(f"Verifying: {chd_path}")
    result = run_chdman(["verify", "--input", str(chd_path)])
    print(result.stdout)
    if result.stderr:
        print(result.stderr)

    if result.returncode == 0:
        print("PASSED — CHD is valid")
    else:
        print("FAILED — CHD is corrupt or invalid")

    sys.exit(result.returncode)


def cmd_extract(args):
    """Extract a CHD back to raw format."""
    chd_path = Path(args.file).resolve()
    output_path = Path(args.output).resolve()

    if not chd_path.exists():
        print(f"Error: File not found: {chd_path}")
        sys.exit(1)

    # Detect if CD or raw
    info = get_chd_info(chd_path)
    if info and "CD-ROM" in info.get("type", ""):
        subcmd = "extractcd"
    else:
        subcmd = "extractraw"

    cmd = [subcmd, "--input", str(chd_path), "--output", str(output_path)]

    if args.force:
        cmd.append("--force")

    print(f"Extracting: {chd_path}")
    print(f"Output: {output_path}")

    if args.dry_run:
        print(f"[DRY RUN] Would run: chdman {' '.join(cmd)}")
        return

    result = run_chdman(cmd)
    print(result.stdout)
    if result.stderr:
        print(result.stderr)

    sys.exit(result.returncode)


def cmd_info(args):
    """Show CHD metadata."""
    chd_path = Path(args.file).resolve()

    if not chd_path.exists():
        print(f"Error: File not found: {chd_path}")
        sys.exit(1)

    result = run_chdman(["info", "--verbose", "--input", str(chd_path)])
    print(result.stdout)
    if result.stderr:
        print(result.stderr)

    if args.json:
        info = get_chd_info(chd_path)
        if info:
            print(json.dumps(info, indent=2))

    sys.exit(result.returncode)


def cmd_delta(args):
    """Create a delta (child) CHD from a parent."""
    parent_path = Path(args.parent).resolve()
    input_path = Path(args.input).resolve()
    output_path = Path(args.output).resolve()

    if not parent_path.exists():
        print(f"Error: Parent CHD not found: {parent_path}")
        sys.exit(1)
    if not input_path.exists():
        print(f"Error: Input file not found: {input_path}")
        sys.exit(1)

    ext = input_path.suffix.lower()
    if ext in (".cue", ".gdi", ".toc"):
        subcmd = "createcd"
    else:
        subcmd = "createraw"

    cmd = [subcmd,
           "--input", str(input_path),
           "--output", str(output_path),
           "--inputparent", str(parent_path)]

    print("Creating delta CHD")
    print(f"  Parent: {parent_path}")
    print(f"  Input:  {input_path}")
    print(f"  Output: {output_path}")

    if args.dry_run:
        print(f"[DRY RUN] Would run: chdman {' '.join(cmd)}")
        return

    result = run_chdman(cmd)
    print(result.stdout)
    if result.stderr:
        print(result.stderr)

    if result.returncode == 0 and output_path.exists():
        parent_size = parent_path.stat().st_size
        input_size = input_path.stat().st_size
        output_size = output_path.stat().st_size
        print(f"\n  Parent size: {round(parent_size / (1024**2), 1)} MB")
        print(f"  Input size:  {round(input_size / (1024**2), 1)} MB")
        print(f"  Delta size:  {round(output_size / (1024**2), 1)} MB")

    sys.exit(result.returncode)


def cmd_merge(args):
    """Merge a child CHD with its parent into a standalone CHD."""
    child_path = Path(args.child).resolve()
    output_path = Path(args.output).resolve()

    if not child_path.exists():
        print(f"Error: Child CHD not found: {child_path}")
        sys.exit(1)

    cmd = ["copy",
           "--input", str(child_path),
           "--output", str(output_path)]

    if args.force:
        cmd.append("--force")

    print("Merging child CHD into standalone")
    print(f"  Child:  {child_path}")
    print(f"  Output: {output_path}")

    if args.dry_run:
        print(f"[DRY RUN] Would run: chdman {' '.join(cmd)}")
        return

    result = run_chdman(cmd)
    print(result.stdout)
    if result.stderr:
        print(result.stderr)

    sys.exit(result.returncode)


def cmd_batch_create(args):
    """Convert all matching files in a directory to CHD."""
    directory = Path(args.directory).resolve()
    if not directory.is_dir():
        print(f"Error: Not a directory: {directory}")
        sys.exit(1)

    extensions = set()
    if args.format:
        for fmt in args.format.split(","):
            ext = fmt.strip().lower()
            if not ext.startswith("."):
                ext = "." + ext
            extensions.add(ext)
    else:
        extensions = CONVERTIBLE_EXTENSIONS

    # Find all convertible files
    files = []
    for f in sorted(directory.rglob("*")):
        if f.is_file() and f.suffix.lower() in extensions:
            chd_path = f.with_suffix(".chd")
            if not chd_path.exists() or args.force:
                files.append(f)

    if not files:
        print(f"No convertible files found in {directory}")
        print(f"Looking for: {', '.join(sorted(extensions))}")
        return

    print(f"Found {len(files)} files to convert in {directory}")
    total_input = sum(f.stat().st_size for f in files)
    print(f"Total input size: {round(total_input / (1024**3), 2)} GB")
    print()

    if args.dry_run:
        for f in files:
            print(f"  [DRY RUN] {f.name} -> {f.stem}.chd")
        return

    converted = 0
    failed = 0
    total_saved = 0

    for i, f in enumerate(files, 1):
        chd_path = f.with_suffix(".chd")
        print(f"[{i}/{len(files)}] {f.name}")

        ext = f.suffix.lower()
        if ext in (".cue", ".gdi", ".toc"):
            subcmd = "createcd"
        else:
            subcmd = "createraw"
            hunk_args = ["--hunksize", "2048"]

        cmd = [subcmd, "--input", str(f), "--output", str(chd_path)]
        if subcmd == "createraw":
            cmd.extend(hunk_args)

        result = run_chdman(cmd)

        if result.returncode == 0 and chd_path.exists():
            input_size = f.stat().st_size
            output_size = chd_path.stat().st_size
            saved = input_size - output_size
            total_saved += saved
            ratio = round(output_size / input_size * 100, 1) if input_size > 0 else 0
            print(f"  OK — {ratio}% ({round(saved / (1024**2), 1)} MB saved)")
            converted += 1

            if args.delete_originals:
                # For cue/gdi, also delete the referenced bin/track files
                if ext == ".cue":
                    delete_cue_files(f)
                elif ext == ".gdi":
                    delete_gdi_files(f)
                f.unlink()
                print("  Deleted original")
        else:
            print(f"  FAILED: {result.stderr.strip()}")
            failed += 1

    print(f"\nBatch complete: {converted} converted, {failed} failed")
    print(f"Total space saved: {round(total_saved / (1024**3), 2)} GB")


def cmd_batch_verify(args):
    """Verify all CHD files in a directory."""
    directory = Path(args.directory).resolve()
    if not directory.is_dir():
        print(f"Error: Not a directory: {directory}")
        sys.exit(1)

    files = sorted(directory.rglob("*.chd"))
    if not files:
        print(f"No CHD files found in {directory}")
        return

    print(f"Verifying {len(files)} CHD files in {directory}")
    print()

    passed = 0
    failed = 0
    errors = []

    for i, f in enumerate(files, 1):
        result = run_chdman(["verify", "--input", str(f)])
        if result.returncode == 0:
            print(f"  [{i}/{len(files)}] PASS — {f.name}")
            passed += 1
        else:
            print(f"  [{i}/{len(files)}] FAIL — {f.name}")
            errors.append(str(f))
            failed += 1

    print(f"\nResults: {passed} passed, {failed} failed")
    if errors:
        print("Failed files:")
        for e in errors:
            print(f"  {e}")

    if args.json:
        summary = {
            "directory": str(directory),
            "total": len(files),
            "passed": passed,
            "failed": failed,
            "failed_files": errors,
            "timestamp": datetime.now().isoformat(),
        }
        print(json.dumps(summary, indent=2))


def cmd_compare(args):
    """Compare two CHD files."""
    file1 = Path(args.file1).resolve()
    file2 = Path(args.file2).resolve()

    info1 = get_chd_info(file1)
    info2 = get_chd_info(file2)

    if info1 and info2:
        print(f"File 1: {file1.name}")
        print(f"  Size: {round(file1.stat().st_size / (1024**2), 1)} MB")
        print(f"  SHA1: {info1.get('sha1', 'unknown')}")
        print()
        print(f"File 2: {file2.name}")
        print(f"  Size: {round(file2.stat().st_size / (1024**2), 1)} MB")
        print(f"  SHA1: {info2.get('sha1', 'unknown')}")
        print()

        if info1.get("sha1") and info2.get("sha1"):
            if info1["sha1"] == info2["sha1"]:
                print("MATCH — identical content")
            else:
                print("DIFFERENT — content differs")


def cmd_convert(args):
    """Re-compress a CHD with different settings."""
    input_path = Path(args.input).resolve()
    output_path = Path(args.output).resolve()

    if not input_path.exists():
        print(f"Error: File not found: {input_path}")
        sys.exit(1)

    cmd = ["copy", "--input", str(input_path), "--output", str(output_path)]

    if args.compression and args.compression in COMPRESSION_PRESETS:
        cmd.extend(COMPRESSION_PRESETS[args.compression])
    elif args.compression:
        cmd.extend(["--compression", args.compression])

    if args.force:
        cmd.append("--force")

    input_size = input_path.stat().st_size
    print(f"Re-compressing: {input_path}")
    print(f"Output: {output_path}")

    if args.dry_run:
        print(f"[DRY RUN] Would run: chdman {' '.join(cmd)}")
        return

    result = run_chdman(cmd)
    print(result.stdout)
    if result.stderr:
        print(result.stderr)

    if result.returncode == 0 and output_path.exists():
        output_size = output_path.stat().st_size
        print(f"\n  Before: {round(input_size / (1024**2), 1)} MB")
        print(f"  After:  {round(output_size / (1024**2), 1)} MB")
        diff = input_size - output_size
        if diff > 0:
            print(f"  Saved:  {round(diff / (1024**2), 1)} MB")
        elif diff < 0:
            print(f"  Grew:   {round(abs(diff) / (1024**2), 1)} MB (worse compression)")

    sys.exit(result.returncode)


def cmd_import(args):
    """Convert any supported disc image format to CHD, handling intermediate conversions."""
    input_path = Path(args.input).resolve()
    output_path = Path(args.output).resolve() if args.output else input_path.with_suffix(".chd")

    if not input_path.exists():
        print(f"Error: File not found: {input_path}")
        sys.exit(1)

    ext = input_path.suffix.lower()

    # Formats chdman handles directly
    native_formats = {".iso", ".img", ".bin", ".raw", ".cue", ".gdi", ".toc"}

    # Formats that need intermediate conversion
    converters = {
        ".cso": ("maxcso", ["--decompress", str(input_path)], ".iso"),
        ".zso": ("maxcso", ["--decompress", str(input_path)], ".iso"),
        ".dax": ("maxcso", ["--decompress", str(input_path)], ".iso"),
        ".pbp": ("pbp2iso", [str(input_path)], ".iso"),
        ".nrg": ("nrg2iso", [str(input_path)], ".iso"),
        ".mdf": ("mdf2iso", [str(input_path)], ".iso"),
        ".ecm": ("ecm2bin", [str(input_path)], ".bin"),
        ".ape": ("ffmpeg", ["-i", str(input_path), "-f", "wav"], ".wav"),
        ".7z":  ("7z", ["x", str(input_path), f"-o{input_path.parent}"], None),
        ".zip": ("7z", ["x", str(input_path), f"-o{input_path.parent}"], None),
        ".rar": ("7z", ["x", str(input_path), f"-o{input_path.parent}"], None),
        ".gz":  ("7z", ["x", str(input_path), f"-o{input_path.parent}"], None),
    }

    if ext in native_formats:
        # Direct conversion
        print(f"Format {ext} is natively supported — creating CHD directly")
        args_obj = argparse.Namespace(
            input=str(input_path), output=str(output_path),
            compression=args.compression, hunk_size=None,
            force=args.force, dry_run=args.dry_run
        )
        cmd_create(args_obj)
        return

    if ext not in converters:
        print(f"Error: Unsupported format: {ext}")
        print(f"Supported formats: {', '.join(sorted(native_formats | set(converters.keys())))}")
        sys.exit(1)

    tool_name, tool_args, intermediate_ext = converters[ext]

    # Check if conversion tool is available
    tool_path = shutil.which(tool_name)
    wsl_fallback = False
    if not tool_path:
        result = subprocess.run(["wsl", "which", tool_name], capture_output=True, text=True)
        if result.returncode == 0 and result.stdout.strip():
            wsl_fallback = True
        else:
            print(f"Error: {tool_name} not found.")
            print(f"Required to convert {ext} files.")
            print(f"Install {tool_name} and try again.")
            sys.exit(2)

    # Archives need special handling — extract then find the image inside
    if ext in (".7z", ".zip", ".rar", ".gz"):
        print("Archive detected — extracting first")
        if args.dry_run:
            print(f"[DRY RUN] Would extract {input_path} and convert contents")
            return

        if wsl_fallback:
            cmd = ["wsl", tool_name] + tool_args
        else:
            cmd = [tool_name] + tool_args

        result = subprocess.run(cmd)
        if result.returncode != 0:
            print(f"Error: Failed to extract {input_path}")
            sys.exit(1)

        # Find extracted image files
        found = []
        for f in input_path.parent.iterdir():
            if f.suffix.lower() in native_formats and f.stem != input_path.stem:
                found.append(f)

        if not found:
            # Check for nested convertible formats
            for f in input_path.parent.iterdir():
                if f.suffix.lower() in converters:
                    found.append(f)

        if not found:
            print("Error: No disc image found in archive")
            sys.exit(1)

        for f in found:
            chd_out = f.with_suffix(".chd")
            print(f"Converting extracted: {f.name}")
            import_args = argparse.Namespace(
                input=str(f), output=str(chd_out),
                compression=args.compression, force=args.force,
                dry_run=False, keep_intermediate=args.keep_intermediate
            )
            cmd_import(import_args)
        return

    # Standard intermediate conversion
    intermediate_path = input_path.with_suffix(intermediate_ext)

    print(f"Step 1: {ext} → {intermediate_ext} (using {tool_name})")

    if args.dry_run:
        print(f"[DRY RUN] Would run: {tool_name} {' '.join(tool_args)}")
        print(f"[DRY RUN] Then: chdman create {intermediate_path} → {output_path}")
        return

    # Run the intermediate converter
    if wsl_fallback:
        cmd = ["wsl", tool_name] + tool_args
    else:
        cmd = [tool_name] + tool_args

    # Some tools need the output path appended
    if tool_name in ("nrg2iso", "mdf2iso", "pbp2iso", "ecm2bin"):
        cmd.append(str(intermediate_path))

    result = subprocess.run(cmd)
    if result.returncode != 0:
        print(f"Error: {tool_name} failed")
        sys.exit(1)

    if not intermediate_path.exists():
        # maxcso replaces the file in-place with new extension
        possible = input_path.with_suffix(intermediate_ext)
        if possible.exists():
            intermediate_path = possible
        else:
            print(f"Error: Expected intermediate file not found: {intermediate_path}")
            sys.exit(1)

    print(f"Step 2: {intermediate_ext} → .chd (using chdman)")

    create_args = argparse.Namespace(
        input=str(intermediate_path), output=str(output_path),
        compression=args.compression, hunk_size=None,
        force=args.force, dry_run=False
    )
    cmd_create(create_args)

    # Clean up intermediate file unless told to keep it
    if not args.keep_intermediate and intermediate_path.exists():
        intermediate_path.unlink()
        print(f"Cleaned up intermediate: {intermediate_path.name}")


def cmd_batch_import(args):
    """Convert all disc images in a directory to CHD, handling any format."""
    directory = Path(args.directory).resolve()
    if not directory.is_dir():
        print(f"Error: Not a directory: {directory}")
        sys.exit(1)

    all_supported = CONVERTIBLE_EXTENSIONS | {".cso", ".zso", ".dax", ".pbp", ".nrg",
                                                ".mdf", ".ecm", ".7z", ".zip", ".rar", ".gz"}

    extensions = set()
    if args.format:
        for fmt in args.format.split(","):
            ext = fmt.strip().lower()
            if not ext.startswith("."):
                ext = "." + ext
            extensions.add(ext)
    else:
        extensions = all_supported

    files = []
    for f in sorted(directory.rglob("*")):
        if f.is_file() and f.suffix.lower() in extensions:
            chd_path = f.with_suffix(".chd")
            if not chd_path.exists() or args.force:
                files.append(f)

    if not files:
        print(f"No convertible files found in {directory}")
        return

    print(f"Found {len(files)} files to import in {directory}")
    print()

    converted = 0
    failed = 0

    for i, f in enumerate(files, 1):
        print(f"\n[{i}/{len(files)}] {f.name}")
        try:
            import_args = argparse.Namespace(
                input=str(f), output=None,
                compression=args.compression, force=args.force,
                dry_run=args.dry_run, keep_intermediate=args.keep_intermediate
            )
            cmd_import(import_args)
            converted += 1

            if args.delete_originals and not args.dry_run:
                f.unlink()
                print(f"  Deleted original: {f.name}")
        except SystemExit as e:
            if e.code != 0:
                failed += 1
        except Exception as e:
            print(f"  Error: {e}")
            failed += 1

    print(f"\nBatch import complete: {converted} converted, {failed} failed")


def cmd_import_tools(args):
    """Show status of all format conversion tools."""
    print("Import tool status:")
    print()
    for tool, description in IMPORT_TOOLS.items():
        path = shutil.which(tool)
        if path:
            status = f"INSTALLED ({path})"
        else:
            # Check WSL
            result = subprocess.run(["wsl", "which", tool],
                                    capture_output=True, text=True)
            if result.returncode == 0 and result.stdout.strip():
                status = f"INSTALLED (WSL: {result.stdout.strip()})"
            else:
                status = "NOT INSTALLED"

        marker = "+" if "INSTALLED" in status and "NOT" not in status else "-"
        print(f"  {marker} {tool:15s} {status}")
        print(f"    {description}")
        print()


IMPORT_TOOLS = {
    "maxcso": "PSP: CSO/ZSO/DAX decompression (apt install maxcso OR github.com/unknownbrackets/maxcso)",
    "pbp2iso": "PSP: PBP to ISO (github.com/pspdev/pspsdk)",
    "nrg2iso": "Nero: NRG to ISO (apt install nrg2iso)",
    "mdf2iso": "Alcohol: MDF to ISO (apt install mdf2iso)",
    "ecm2bin": "ECM: error code modeler decode (apt install ecm)",
    "ffmpeg": "Audio: various audio format conversion (apt install ffmpeg)",
    "7z": "Archives: 7z/zip/rar/gz extraction (apt install p7zip-full)",
}


def get_chd_info(chd_path):
    """Parse chdman info output into a dict."""
    result = run_chdman(["info", "--verbose", "--input", str(chd_path)])
    if result.returncode != 0:
        return None

    info = {}
    for line in result.stdout.splitlines():
        if ":" in line:
            key, _, value = line.partition(":")
            info[key.strip().lower().replace(" ", "_")] = value.strip()
    return info


def delete_cue_files(cue_path):
    """Delete bin files referenced by a cue sheet."""
    try:
        with open(cue_path) as f:
            for line in f:
                if line.strip().upper().startswith("FILE"):
                    parts = line.strip().split('"')
                    if len(parts) >= 2:
                        bin_file = cue_path.parent / parts[1]
                        if bin_file.exists():
                            bin_file.unlink()
    except Exception:
        pass


def delete_gdi_files(gdi_path):
    """Delete track files referenced by a GDI file."""
    try:
        with open(gdi_path) as f:
            for line in f:
                parts = line.strip().split()
                if len(parts) >= 5:
                    track_file = gdi_path.parent / parts[4]
                    if track_file.exists():
                        track_file.unlink()
    except Exception:
        pass


def main():
    parser = argparse.ArgumentParser(
        description="CHD archive management — create, verify, extract, diff, batch convert.",
    )
    subparsers = parser.add_subparsers(dest="command", help="Command to run")

    # create
    p = subparsers.add_parser("create", help="Create CHD from disc image or raw dump")
    p.add_argument("input", help="Input file (.iso, .img, .bin, .cue, .gdi, .raw)")
    p.add_argument("output", help="Output CHD file")
    p.add_argument("--compression", choices=list(COMPRESSION_PRESETS.keys()),
                    help="Compression preset")
    p.add_argument("--hunk-size", help="Hunk size in bytes (default: auto)")
    p.add_argument("--force", action="store_true", help="Overwrite existing output")
    p.add_argument("--dry-run", action="store_true")

    # verify
    p = subparsers.add_parser("verify", help="Verify CHD integrity")
    p.add_argument("file", help="CHD file to verify")

    # extract
    p = subparsers.add_parser("extract", help="Extract CHD to raw format")
    p.add_argument("file", help="CHD file to extract")
    p.add_argument("output", help="Output file")
    p.add_argument("--force", action="store_true")
    p.add_argument("--dry-run", action="store_true")

    # info
    p = subparsers.add_parser("info", help="Show CHD metadata")
    p.add_argument("file", help="CHD file")
    p.add_argument("--json", action="store_true", help="Output as JSON")

    # delta
    p = subparsers.add_parser("delta", help="Create delta (child) CHD from parent")
    p.add_argument("parent", help="Parent CHD file")
    p.add_argument("input", help="Input file for child")
    p.add_argument("output", help="Output child CHD file")
    p.add_argument("--dry-run", action="store_true")

    # merge
    p = subparsers.add_parser("merge", help="Merge child CHD into standalone")
    p.add_argument("child", help="Child CHD file")
    p.add_argument("output", help="Output standalone CHD file")
    p.add_argument("--force", action="store_true")
    p.add_argument("--dry-run", action="store_true")

    # batch-create
    p = subparsers.add_parser("batch-create", help="Convert all matching files in a directory")
    p.add_argument("directory", help="Directory to scan")
    p.add_argument("--format", help="File extensions to convert (comma-separated, e.g., iso,bin,cue)")
    p.add_argument("--delete-originals", action="store_true",
                    help="Delete original files after successful conversion")
    p.add_argument("--force", action="store_true", help="Overwrite existing CHDs")
    p.add_argument("--dry-run", action="store_true")

    # batch-verify
    p = subparsers.add_parser("batch-verify", help="Verify all CHDs in a directory")
    p.add_argument("directory", help="Directory to scan")
    p.add_argument("--json", action="store_true", help="Output summary as JSON")

    # import (convert any format to CHD)
    p = subparsers.add_parser("import", help="Convert any disc image format to CHD")
    p.add_argument("input", help="Input file (ISO, CSO, PBP, NRG, MDF, ECM, CUE, GDI, 7z, zip, etc.)")
    p.add_argument("output", nargs="?", help="Output CHD file (default: same name with .chd)")
    p.add_argument("--compression", choices=list(COMPRESSION_PRESETS.keys()))
    p.add_argument("--force", action="store_true")
    p.add_argument("--keep-intermediate", action="store_true",
                    help="Don't delete intermediate files after conversion")
    p.add_argument("--dry-run", action="store_true")

    # batch-import
    p = subparsers.add_parser("batch-import", help="Convert all disc images in a directory to CHD")
    p.add_argument("directory", help="Directory to scan")
    p.add_argument("--format", help="File extensions to convert (comma-separated)")
    p.add_argument("--compression", choices=list(COMPRESSION_PRESETS.keys()))
    p.add_argument("--delete-originals", action="store_true")
    p.add_argument("--keep-intermediate", action="store_true")
    p.add_argument("--force", action="store_true")
    p.add_argument("--dry-run", action="store_true")

    # import-tools (show required tools)
    p = subparsers.add_parser("import-tools", help="Show required tools for format conversion")

    # compare
    p = subparsers.add_parser("compare", help="Compare two CHD files")
    p.add_argument("file1", help="First CHD file")
    p.add_argument("file2", help="Second CHD file")

    # convert
    p = subparsers.add_parser("convert", help="Re-compress CHD with different settings")
    p.add_argument("input", help="Input CHD file")
    p.add_argument("output", help="Output CHD file")
    p.add_argument("--compression", choices=list(COMPRESSION_PRESETS.keys()),
                    help="Compression preset")
    p.add_argument("--force", action="store_true")
    p.add_argument("--dry-run", action="store_true")

    args = parser.parse_args()

    if not args.command:
        parser.print_help()
        sys.exit(0)

    commands = {
        "create": cmd_create,
        "verify": cmd_verify,
        "extract": cmd_extract,
        "info": cmd_info,
        "delta": cmd_delta,
        "merge": cmd_merge,
        "batch-create": cmd_batch_create,
        "batch-verify": cmd_batch_verify,
        "compare": cmd_compare,
        "convert": cmd_convert,
        "import": cmd_import,
        "batch-import": cmd_batch_import,
        "import-tools": cmd_import_tools,
    }

    commands[args.command](args)


if __name__ == "__main__":
    main()
