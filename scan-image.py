#!/usr/bin/env python
"""
JP_TOOLS/scan-image.py
Scan a raw disk image or device for embedded files and signatures.

Uses binwalk for firmware/embedded file detection and custom scanners
for file types that standard tools miss (ROM files, etc.).

Usage:
    python scan-image.py <image-or-device> [--output <dir>] [--extract] [--signatures <type>]

Signature types:
    default     — binwalk's built-in signatures (archives, images, firmware, etc.)
    roms        — scan for retro ROM magic bytes (NES, SNES, GB, N64, Genesis, etc.)
    all         — both default and ROM signatures

Examples:
    python scan-image.py disk.img --signatures all
    python scan-image.py disk.img --extract --output ./extracted
    python scan-image.py disk.img --signatures roms --output ./roms
"""

import sys
import shutil
import subprocess
import argparse
import json
from pathlib import Path
from datetime import datetime


# ROM magic byte signatures
# Format: (name, offset_from_start, magic_bytes, expected_size_range)
ROM_SIGNATURES = [
    ("NES", 0, b"NES\x1a", (16*1024, 4*1024*1024)),
    ("SNES (SMC header)", 0x200, b"\x00" * 2, None),  # Too generic alone, needs validation
    ("Game Boy", 0x104, b"\xce\xed\x66\x66\xcc\x0d\x00\x0b", (32*1024, 8*1024*1024)),
    ("Game Boy Advance", 0x04, b"\x24\xff\xae\x51\x69\x9a\xa2\x21", (256*1024, 32*1024*1024)),
    ("N64 (big-endian)", 0, b"\x80\x37\x12\x40", (1*1024*1024, 64*1024*1024)),
    ("N64 (little-endian)", 0, b"\x40\x12\x37\x80", (1*1024*1024, 64*1024*1024)),
    ("N64 (byte-swapped)", 0, b"\x37\x80\x40\x12", (1*1024*1024, 64*1024*1024)),
    ("Sega Genesis", 0x100, b"SEGA", (128*1024, 8*1024*1024)),
    ("Sega Master System", 0x7FF0, b"TMR SEGA", (32*1024, 1*1024*1024)),
    ("NDS", 0, None, None),  # NDS has a complex header, skip for now
    ("Atari 2600", 0, None, None),  # No reliable magic, skip
    ("ISO 9660", 0x8001, b"CD001", (1*1024*1024, 8*1024*1024*1024)),
    ("CHD (MAME)", 0, b"MComprHD", (1*1024*1024, 8*1024*1024*1024)),
    ("CSO (compressed ISO)", 0, b"CISO", (1*1024*1024, 4*1024*1024*1024)),
    ("GDI (Dreamcast)", 0, None, None),  # Text file, no reliable magic
    ("CUE sheet", 0, b"FILE ", (50, 10*1024)),
    ("PBP (PSP)", 0, b"\x00PBP\x00", (1*1024*1024, 2*1024*1024*1024)),
    ("WBFS (Wii)", 0, b"WBFS", (1*1024*1024*1024, 8*1024*1024*1024)),
    ("NKit (Wii/GC)", 0, b"NKIT", (1*1024*1024*1024, 8*1024*1024*1024)),
    ("GCM (GameCube)", 0x1C, b"\xC2\x33\x9F\x3D", (1*1024*1024*1024, 2*1024*1024*1024)),
    ("3DS", 0x100, b"NCSD", (256*1024*1024, 8*1024*1024*1024)),
    ("NDS", 0xC0, b"\x24\xFF\xDE\x01", (1*1024*1024, 512*1024*1024)),
    ("Atari 7800", 1, b"ATARI7800", (8*1024, 512*1024)),
    ("Atari Lynx", 0, b"LYNX", (64*1024, 512*1024)),
    ("TurboGrafx/PCE", 0, None, None),  # No reliable magic
    ("Neo Geo", 0x100, b"NEO-GEO", (256*1024, 64*1024*1024)),
    ("FDS (Famicom Disk)", 0, b"FDS\x1a", (65500, 2*65500)),
    ("Intellivision", 0, None, None),  # No reliable magic
    ("Sega 32X", 0x100, b"SEGA 32X", (512*1024, 4*1024*1024)),
    ("Sega CD", 0, b"SEGADISCSYSTEM", (1*1024*1024, 700*1024*1024)),
    ("WordPerfect", 0, b"\xFF\x57\x50\x43", (1*1024, 50*1024*1024)),
]

# Only include signatures with actual magic bytes
ROM_SIGNATURES = [(name, off, magic, size)
                  for name, off, magic, size in ROM_SIGNATURES
                  if magic is not None and len(magic) >= 4]


def find_binwalk():
    """Find binwalk binary."""
    path = shutil.which("binwalk")
    if path:
        return [path]

    result = subprocess.run(
        ["wsl", "which", "binwalk"],
        capture_output=True, text=True
    )
    if result.returncode == 0 and result.stdout.strip():
        return ["wsl", result.stdout.strip()]

    return None


def win_to_wsl_path(win_path):
    p = str(win_path)
    if len(p) >= 2 and p[1] == ":":
        return f"/mnt/{p[0].lower()}/{p[2:].replace(chr(92), '/')}"
    return p.replace("\\", "/")


def run_binwalk(source, output_dir=None, extract=False):
    """Run binwalk signature scan."""
    binwalk = find_binwalk()
    if not binwalk:
        print("Warning: binwalk not found — skipping standard signature scan")
        return []

    if binwalk[0] == "wsl":
        src = win_to_wsl_path(source) if ":" in str(source) else str(source)
        cmd = ["wsl", "binwalk"]
    else:
        src = str(source)
        cmd = binwalk[:]

    if extract and output_dir:
        out = win_to_wsl_path(output_dir) if ":" in str(output_dir) else str(output_dir)
        cmd.extend(["--extract", "--directory", out])

    cmd.append(src)

    print(f"Running binwalk: {' '.join(cmd)}")
    result = subprocess.run(cmd, capture_output=True, text=True)
    print(result.stdout)
    if result.stderr:
        print(result.stderr, file=sys.stderr)

    return result.stdout


def scan_for_roms(source, output_dir=None, chunk_size=64*1024*1024):
    """Scan a raw image for ROM file signatures."""
    source_path = Path(source)
    if not source_path.exists():
        print(f"Error: {source} not found")
        return []

    file_size = source_path.stat().st_size
    found = []

    print(f"Scanning {source} ({round(file_size / (1024**3), 1)} GB) for ROM signatures...")
    print(f"Signatures: {', '.join(name for name, _, _, _ in ROM_SIGNATURES)}")
    print()

    # Build a search plan: for each signature, what bytes to look for
    # We scan in chunks and look for magic bytes
    with open(source, "rb") as f:
        offset = 0
        chunk_num = 0
        while offset < file_size:
            chunk = f.read(chunk_size)
            if not chunk:
                break

            for sig_name, sig_offset, magic, size_range in ROM_SIGNATURES:
                # Search for magic bytes in this chunk
                search_start = 0
                while True:
                    pos = chunk.find(magic, search_start)
                    if pos == -1:
                        break

                    # The magic is at chunk position `pos`, but the file would
                    # start at `pos - sig_offset` (since the magic is at sig_offset
                    # within the file)
                    file_start = offset + pos - sig_offset
                    if file_start < 0:
                        search_start = pos + 1
                        continue

                    hit = {
                        "type": sig_name,
                        "offset": file_start,
                        "magic_at": offset + pos,
                        "hex_offset": f"0x{file_start:X}",
                    }
                    found.append(hit)

                    if len(found) % 100 == 0:
                        print(f"  Found {len(found)} signatures so far... "
                              f"({round(offset / file_size * 100, 1)}%)")

                    search_start = pos + 1

            new_offset = offset + len(chunk) - 256  # Overlap to catch signatures at boundaries
            if new_offset <= offset:
                break  # Prevent infinite loop at end of file
            offset = new_offset
            f.seek(offset)
            chunk_num += 1

            # Progress every 1GB
            if chunk_num % 16 == 0:
                pct = round(offset / file_size * 100, 1)
                print(f"  {pct}% scanned, {len(found)} signatures found...")

    print(f"\nScan complete: {len(found)} signatures found")

    # Summary by type
    from collections import Counter
    counts = Counter(h["type"] for h in found)
    for sig_type, count in counts.most_common():
        print(f"  {sig_type}: {count}")

    # Save results
    if output_dir:
        out_path = Path(output_dir)
        out_path.mkdir(parents=True, exist_ok=True)
        results_file = out_path / "rom_scan_results.json"
        with open(results_file, "w") as f:
            json.dump({
                "source": str(source),
                "signatures_found": len(found),
                "results": found[:10000],  # Cap at 10k to avoid huge files
                "summary": dict(counts),
                "timestamp": datetime.now().isoformat(),
            }, f, indent=2)
        print(f"\nResults saved to {results_file}")

    return found


def main():
    parser = argparse.ArgumentParser(
        description="Scan disk images for embedded files and ROM signatures.",
    )
    parser.add_argument("source", help="Disk image or device to scan")
    parser.add_argument("--output", "-o", help="Output directory for results/extracted files")
    parser.add_argument("--extract", action="store_true",
                        help="Extract found files (binwalk only)")
    parser.add_argument("--signatures", choices=["default", "roms", "all"],
                        default="default",
                        help="Which signatures to scan for (default: default)")
    args = parser.parse_args()

    start = datetime.now()

    if args.signatures in ("default", "all"):
        print("=== Standard signature scan (binwalk) ===\n")
        run_binwalk(args.source, args.output, args.extract)

    if args.signatures in ("roms", "all"):
        print("\n=== ROM signature scan ===\n")
        scan_for_roms(args.source, args.output)

    elapsed = (datetime.now() - start).total_seconds()
    print(f"\nTotal elapsed: {round(elapsed, 1)}s")


if __name__ == "__main__":
    main()
