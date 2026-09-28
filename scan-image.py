#!/usr/bin/env python3
"""Scan a raw disk image or device for embedded files and signatures.

Uses binwalk for firmware and embedded-file detection, and its own scanner
for the file types standard tools miss (ROMs, disc images).

Usage:
    python scan-image.py <image-or-device> [--output <dir>] [--extract] [--signatures <type>]

Signature types:
    default     binwalk's built-in signatures (archives, images, firmware, ...)
    roms        retro ROM and disc-image magic bytes (the table below)
    all         both

A ROM hit is a CANDIDATE: the magic bytes are at the right place for that
format, and nothing about the rest of the file is checked. Formats with no
reliable magic (Atari 2600, TurboGrafx, Intellivision, raw SNES, GDI) are not
scanned for at all. The scan reads in chunks that overlap so a signature
spanning a boundary is found, and each is reported once. A block device is
measured by seeking to its end, since stat() reports its size as 0.

With --output, the ROM hits are saved to rom_scan_results.json (the first
10,000, with `results_truncated` saying whether there were more).

Examples:
    python scan-image.py disk.img --signatures all
    python scan-image.py disk.img --extract --output ./extracted
    python scan-image.py /dev/sdb --signatures roms --output ./roms
"""

import argparse
import json
import subprocess
import sys
import time
from collections import Counter
from collections.abc import Iterator
from datetime import datetime
from pathlib import Path
from typing import BinaryIO, NamedTuple

from platformtools.tools import find_tool
from platformtools.windows import wsl_path


class Signature(NamedTuple):
    name: str
    offset: int  # where the magic sits within the file
    magic: bytes


ROM_SIGNATURES = [
    Signature("NES", 0, b"NES\x1a"),
    Signature("Game Boy", 0x104, b"\xce\xed\x66\x66\xcc\x0d\x00\x0b"),
    Signature("Game Boy Advance", 0x04, b"\x24\xff\xae\x51\x69\x9a\xa2\x21"),
    Signature("N64 (big-endian)", 0, b"\x80\x37\x12\x40"),
    Signature("N64 (little-endian)", 0, b"\x40\x12\x37\x80"),
    Signature("N64 (byte-swapped)", 0, b"\x37\x80\x40\x12"),
    Signature("Sega Genesis", 0x100, b"SEGA"),
    Signature("Sega Master System", 0x7FF0, b"TMR SEGA"),
    Signature("ISO 9660", 0x8001, b"CD001"),
    Signature("CHD (MAME)", 0, b"MComprHD"),
    Signature("CSO (compressed ISO)", 0, b"CISO"),
    Signature("CUE sheet", 0, b"FILE "),
    Signature("PBP (PSP)", 0, b"\x00PBP\x00"),
    Signature("WBFS (Wii)", 0, b"WBFS"),
    Signature("NKit (Wii/GC)", 0, b"NKIT"),
    Signature("GCM (GameCube)", 0x1C, b"\xC2\x33\x9F\x3D"),
    Signature("3DS", 0x100, b"NCSD"),
    Signature("NDS", 0xC0, b"\x24\xFF\xDE\x01"),
    Signature("Atari 7800", 1, b"ATARI7800"),
    Signature("Atari Lynx", 0, b"LYNX"),
    Signature("Neo Geo", 0x100, b"NEO-GEO"),
    Signature("FDS (Famicom Disk)", 0, b"FDS\x1a"),
    Signature("Sega 32X", 0x100, b"SEGA 32X"),
    Signature("Sega CD", 0, b"SEGADISCSYSTEM"),
    Signature("WordPerfect", 0, b"\xFF\x57\x50\x43"),
]

# Longer than any magic, so one that spans a chunk boundary is whole in the next chunk.
OVERLAP = 256
CHUNK = 64 * 1024 * 1024
SAVED = 10_000


class Hit(NamedTuple):
    type: str
    offset: int  # where the file would start
    magic_at: int


def hits_in(f: BinaryIO, size: int, chunk_size: int = CHUNK) -> Iterator[Hit]:
    """Every signature in the first SIZE bytes of F, each once, in scan order."""
    seen: set[tuple[str, int]] = set()
    offset = 0
    while offset < size:
        f.seek(offset)
        chunk = f.read(chunk_size)
        if not chunk:
            return
        for sig in ROM_SIGNATURES:
            pos = chunk.find(sig.magic)
            while pos != -1:
                at = offset + pos
                if at - sig.offset >= 0 and (sig.name, at) not in seen:
                    seen.add((sig.name, at))
                    yield Hit(sig.name, at - sig.offset, at)
                pos = chunk.find(sig.magic, pos + 1)
        if len(chunk) <= OVERLAP:
            return
        offset += len(chunk) - OVERLAP


def device_size(f: BinaryIO) -> int:
    """Bytes in F, measured by seeking to its end (stat() says 0 for a block device)."""
    size = f.seek(0, 2)
    f.seek(0)
    return size


def _save(output_dir: str, source: str, found: list[Hit], counts: Counter[str]) -> Path:
    out = Path(output_dir)
    out.mkdir(parents=True, exist_ok=True)
    path = out / "rom_scan_results.json"
    path.write_text(json.dumps({
        "source": source,
        "signatures_found": len(found),
        "results": [{**h._asdict(), "hex_offset": f"0x{h.offset:X}"} for h in found[:SAVED]],
        "results_truncated": len(found) > SAVED,
        "summary": dict(counts),
        "timestamp": datetime.now().astimezone().isoformat(),
    }, indent=2))
    return path


def scan_for_roms(source: str, output_dir: str | None = None, chunk_size: int = CHUNK) -> list[Hit]:
    """Scan an image or device for ROM signatures; report, and save them with OUTPUT_DIR.

    A read error part-way (a failing drive) ends the scan: what was found
    before it is kept, reported and saved, and the error is said.
    """
    found: list[Hit] = []
    try:
        with open(source, "rb") as f:
            size = device_size(f)
            print(f"Scanning {source} ({size / 1024**3:.1f} GB) for ROM signatures...")
            for hit in hits_in(f, size, chunk_size):
                found.append(hit)
    except OSError as exc:
        print(f"Error: reading {source} stopped: {exc}; {len(found)} signatures found before it")
    counts = Counter(h.type for h in found)
    print(f"\nScan complete: {len(found)} signatures found")
    for sig_type, count in counts.most_common():
        print(f"  {sig_type}: {count}")
    if output_dir:
        print(f"\nResults saved to {_save(output_dir, source, found, counts)}")
    return found


def run_binwalk(source: str, output_dir: str | None = None, extract: bool = False) -> str:
    """binwalk's signature scan of SOURCE (and extraction into OUTPUT_DIR). Returns its output."""
    binwalk = find_tool("binwalk")
    if binwalk is None:
        print("Warning: binwalk not found; skipping the standard signature scan")
        return ""
    as_seen = wsl_path if binwalk[0] == "wsl" else str
    cmd = list(binwalk)
    if extract and output_dir:
        cmd += ["--extract", "--directory", as_seen(output_dir)]
    cmd.append(as_seen(source))
    print(f"Running binwalk: {' '.join(cmd)}")
    result = subprocess.run(cmd, capture_output=True, text=True, check=False)
    print(result.stdout)
    if result.stderr:
        print(result.stderr, file=sys.stderr)
    return result.stdout


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Scan disk images for embedded files and ROM signatures.")
    parser.add_argument("source", help="Disk image or device to scan")
    parser.add_argument("--output", "-o", help="Output directory for results/extracted files")
    parser.add_argument("--extract", action="store_true", help="Extract found files (binwalk only)")
    parser.add_argument("--signatures", choices=["default", "roms", "all"], default="default",
                        help="Which signatures to scan for (default: default)")
    args = parser.parse_args(argv)
    start = time.monotonic()
    if args.signatures in ("default", "all"):
        print("=== Standard signature scan (binwalk) ===\n")
        run_binwalk(args.source, args.output, args.extract)
    if args.signatures in ("roms", "all"):
        print("\n=== ROM signature scan ===\n")
        scan_for_roms(args.source, args.output)
    print(f"\nTotal elapsed: {time.monotonic() - start:.1f}s")
    return 0


if __name__ == "__main__":
    sys.exit(main())
