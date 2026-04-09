#!/usr/bin/env python
"""
JP_TOOLS/chd-hunkmap.py
Read and analyze a CHD v5 hunk map — find self-referencing (duplicate) hunks
and trace them back to files in a tar archive.

Usage:
    python chd-hunkmap.py <file.chd>                     Show hunk map summary
    python chd-hunkmap.py <file.chd> --self-refs         List all self-referencing hunks
    python chd-hunkmap.py <file.chd> --trace <tar>       Trace self-refs back to tar file entries
    python chd-hunkmap.py <file.chd> --json              Output as JSON
"""

import struct
import sys
import argparse
import json
import tarfile
from pathlib import Path
from collections import defaultdict


COMPRESSION_NAMES = {
    0: "codec_0",
    1: "codec_1",
    2: "codec_2",
    3: "codec_3",
    4: "uncompressed",
    5: "self_ref",
    6: "parent_ref",
}


def read_chd_header(f):
    """Read CHD v5 header (124 bytes)."""
    magic = f.read(8)
    if magic != b'MComprHD':
        raise ValueError(f"Not a CHD file (magic: {magic})")

    f.read(4)  # header_size — consumed to advance file position
    version = struct.unpack('>I', f.read(4))[0]

    if version != 5:
        raise ValueError(f"Only CHD v5 supported, got v{version}")

    codecs = []
    for _ in range(4):
        codec_bytes = f.read(4)
        codec = codec_bytes.decode('ascii', errors='replace').strip('\x00')
        codecs.append(codec if codec else None)

    logical_size = struct.unpack('>Q', f.read(8))[0]
    map_offset = struct.unpack('>Q', f.read(8))[0]
    meta_offset = struct.unpack('>Q', f.read(8))[0]
    hunk_size = struct.unpack('>I', f.read(4))[0]
    unit_size = struct.unpack('>I', f.read(4))[0]
    raw_sha1 = f.read(20).hex()
    sha1 = f.read(20).hex()
    parent_sha1 = f.read(20).hex()

    hunk_count = (logical_size + hunk_size - 1) // hunk_size

    return {
        'version': version,
        'codecs': [c for c in codecs if c],
        'logical_size': logical_size,
        'map_offset': map_offset,
        'meta_offset': meta_offset,
        'hunk_size': hunk_size,
        'unit_size': unit_size,
        'hunk_count': hunk_count,
        'raw_sha1': raw_sha1,
        'sha1': sha1,
        'parent_sha1': parent_sha1,
    }


class BitReader:
    """Read individual bits from a byte buffer."""

    def __init__(self, data):
        self.data = data
        self.byte_pos = 0
        self.bit_pos = 0

    def read_bits(self, count):
        """Read `count` bits and return as integer."""
        result = 0
        for _ in range(count):
            if self.byte_pos >= len(self.data):
                return result
            byte = self.data[self.byte_pos]
            bit = (byte >> (7 - self.bit_pos)) & 1
            result = (result << 1) | bit
            self.bit_pos += 1
            if self.bit_pos >= 8:
                self.bit_pos = 0
                self.byte_pos += 1
        return result

    def bits_remaining(self):
        return (len(self.data) - self.byte_pos) * 8 - self.bit_pos


def read_compressed_map(f, header):
    """Read the compressed hunk map using variable-width bitstream."""
    f.seek(header['map_offset'])

    # Map header (16 bytes)
    map_size = struct.unpack('>I', f.read(4))[0]
    first_offset_bytes = f.read(6)
    first_offset = int.from_bytes(first_offset_bytes, 'big')
    f.read(2)  # map_crc — consumed to advance file position
    size_bits = struct.unpack('B', f.read(1))[0]
    self_bits = struct.unpack('B', f.read(1))[0]
    parent_bits = struct.unpack('B', f.read(1))[0]
    f.read(1)  # reserved — consumed to advance file position

    # Read and decompress the map data
    compressed_map_data = f.read(map_size)

    import zlib
    try:
        decompressed = zlib.decompress(compressed_map_data, -15)
    except zlib.error:
        try:
            decompressed = zlib.decompress(compressed_map_data)
        except zlib.error:
            decompressed = compressed_map_data

    # The decompressed data is a bitstream where each entry has:
    #   compression_type: 3 bits (0-3=codec, 4=uncompressed, 5=self, 6=parent)
    #   length:           size_bits (compressed size)
    #   offset/ref:       depends on type
    #     compressed:      offset delta (cumulative from first_offset)
    #     uncompressed:    offset delta
    #     self_ref:        self_bits (target hunk number)
    #     parent_ref:      parent_bits (target hunk in parent)
    #   crc:              16 bits

    reader = BitReader(decompressed)
    entries = []
    current_offset = first_offset

    for i in range(header['hunk_count']):
        if reader.bits_remaining() < 3:
            break

        comp_type = reader.read_bits(3)

        if comp_type == 5:  # self_ref
            target = reader.read_bits(self_bits)
            crc = reader.read_bits(16)
            entry = {
                'hunk': i,
                'type': 5,
                'type_name': 'self_ref',
                'block_size': 0,
                'offset': 0,
                'target_hunk': target,
                'crc': crc,
            }
        elif comp_type == 6:  # parent_ref
            target = reader.read_bits(parent_bits)
            crc = reader.read_bits(16)
            entry = {
                'hunk': i,
                'type': 6,
                'type_name': 'parent_ref',
                'block_size': 0,
                'offset': 0,
                'target_hunk': target,
                'crc': crc,
            }
        elif comp_type == 4:  # uncompressed
            length = reader.read_bits(size_bits)
            crc = reader.read_bits(16)
            entry = {
                'hunk': i,
                'type': 4,
                'type_name': 'uncompressed',
                'block_size': header['hunk_size'],
                'offset': current_offset,
                'crc': crc,
            }
            current_offset += header['hunk_size']
        else:  # compressed (codec 0-3)
            length = reader.read_bits(size_bits)
            crc = reader.read_bits(16)
            entry = {
                'hunk': i,
                'type': comp_type,
                'type_name': COMPRESSION_NAMES.get(comp_type, f'codec_{comp_type}'),
                'block_size': length,
                'offset': current_offset,
                'crc': crc,
            }
            current_offset += length

        entries.append(entry)

    return entries, {
        'map_size': map_size,
        'first_offset': first_offset,
        'size_bits': size_bits,
        'self_bits': self_bits,
        'parent_bits': parent_bits,
    }


def read_uncompressed_map(f, header):
    """Read uncompressed hunk map (simple offset table)."""
    f.seek(header['map_offset'])
    entries = []
    for i in range(header['hunk_count']):
        offset = struct.unpack('>I', f.read(4))[0]
        entries.append({
            'hunk': i,
            'type': 4,  # uncompressed
            'type_name': 'uncompressed',
            'block_size': header['hunk_size'],
            'offset': offset,
            'crc': 0,
        })
    return entries, {}


def build_tar_map(tar_path, hunk_size):
    """Build a map of byte offset ranges to tar file entries."""
    file_map = []
    with tarfile.open(tar_path, 'r') as tar:
        for member in tar.getmembers():
            if member.isfile():
                # tar data starts at the member's offset + header (512 bytes typically)
                data_offset = member.offset_data
                file_map.append({
                    'name': member.name,
                    'offset': data_offset,
                    'size': member.size,
                    'start_hunk': data_offset // hunk_size,
                    'end_hunk': (data_offset + member.size - 1) // hunk_size if member.size > 0 else data_offset // hunk_size,
                })
    return file_map


def hunk_to_file(hunk_num, tar_map, hunk_size):
    """Find which file(s) a hunk belongs to."""
    byte_offset = hunk_num * hunk_size
    for entry in tar_map:
        if entry['offset'] <= byte_offset < entry['offset'] + entry['size']:
            return entry['name']
    return None


def analyze_self_refs(entries, tar_map=None, hunk_size=4096):
    """Analyze self-referencing hunks and optionally trace to files."""
    self_refs = [e for e in entries if e['type'] == 5]

    # Group by target — which hunks are being referenced by multiple self-refs?
    target_groups = defaultdict(list)
    for ref in self_refs:
        target_groups[ref['target_hunk']].append(ref['hunk'])

    results = []
    for target, referrers in sorted(target_groups.items()):
        group = {
            'target_hunk': target,
            'target_byte_offset': target * hunk_size,
            'referencing_hunks': referrers,
            'count': len(referrers),
        }

        if tar_map:
            target_file = hunk_to_file(target, tar_map, hunk_size)
            ref_files = set()
            for r in referrers:
                rf = hunk_to_file(r, tar_map, hunk_size)
                if rf:
                    ref_files.add(rf)

            group['target_file'] = target_file
            group['referencing_files'] = sorted(ref_files)
            group['cross_file'] = target_file not in ref_files if target_file else False

        results.append(group)

    return results


def main():
    parser = argparse.ArgumentParser(
        description="Analyze CHD v5 hunk map — find duplicate blocks and trace to files.",
    )
    parser.add_argument("chd", help="CHD file to analyze")
    parser.add_argument("--self-refs", action="store_true",
                        help="List all self-referencing (duplicate) hunks")
    parser.add_argument("--trace", metavar="TAR",
                        help="Trace self-refs back to files in this tar archive")
    parser.add_argument("--json", action="store_true",
                        help="Output as JSON")
    parser.add_argument("--top", type=int, default=20,
                        help="Show top N duplicate groups (default: 20)")
    args = parser.parse_args()

    chd_path = Path(args.chd)
    if not chd_path.exists():
        print(f"Error: {chd_path} not found")
        sys.exit(1)

    with open(chd_path, 'rb') as f:
        header = read_chd_header(f)

        print(f"CHD v{header['version']}: {chd_path.name}")
        print(f"  Logical size: {header['logical_size']:,} bytes ({round(header['logical_size'] / (1024**2), 1)} MB)")
        print(f"  Hunk size:    {header['hunk_size']:,} bytes")
        print(f"  Total hunks:  {header['hunk_count']:,}")
        print(f"  Codecs:       {', '.join(header['codecs'])}")
        print()

        # Read hunk map
        is_compressed = header['codecs'][0] != '' and header['codecs'][0] is not None
        if is_compressed:
            entries, map_info = read_compressed_map(f, header)
        else:
            entries, map_info = read_uncompressed_map(f, header)

    if not entries:
        print("Error: Could not parse hunk map")
        sys.exit(1)

    # Summary by type
    type_counts = defaultdict(int)
    for e in entries:
        type_counts[e['type_name']] += 1

    print(f"Hunk map ({len(entries)} entries):")
    for name, count in sorted(type_counts.items(), key=lambda x: -x[1]):
        pct = round(count / len(entries) * 100, 1)
        print(f"  {name:20s} {count:>8,}  ({pct}%)")

    self_ref_count = type_counts.get('self_ref', 0)
    print(f"\n  Self-referencing hunks: {self_ref_count}")
    print(f"  Deduplicated bytes:    {self_ref_count * header['hunk_size']:,} ({round(self_ref_count * header['hunk_size'] / (1024**2), 1)} MB)")

    # Build tar map if tracing
    tar_map = None
    if args.trace:
        tar_path = Path(args.trace)
        if not tar_path.exists():
            print(f"Error: Tar file not found: {tar_path}")
            sys.exit(1)
        print(f"\nBuilding tar file map from {tar_path.name}...")
        tar_map = build_tar_map(str(tar_path), header['hunk_size'])
        print(f"  {len(tar_map)} files in tar")

    # Analyze self-refs
    if args.self_refs or args.trace:
        print("\nAnalyzing self-references...")
        groups = analyze_self_refs(entries, tar_map, header['hunk_size'])

        if args.json:
            output = {
                'chd': str(chd_path),
                'header': header,
                'hunk_summary': dict(type_counts),
                'self_ref_groups': groups[:args.top] if not args.json else groups,
                'total_self_refs': self_ref_count,
                'dedup_bytes': self_ref_count * header['hunk_size'],
            }
            print(json.dumps(output, indent=2, default=str))
        else:
            # Sort by count descending
            groups.sort(key=lambda g: g['count'], reverse=True)

            print(f"\nTop {min(args.top, len(groups))} duplicate block groups:")
            for g in groups[:args.top]:
                print(f"\n  Target hunk {g['target_hunk']} (offset 0x{g['target_byte_offset']:X})")
                print(f"    Referenced by {g['count']} other hunk(s): {g['referencing_hunks'][:10]}{'...' if g['count'] > 10 else ''}")

                if tar_map:
                    print(f"    Target file: {g.get('target_file', 'unknown')}")
                    ref_files = g.get('referencing_files', [])
                    if ref_files:
                        print("    Referencing files:")
                        for rf in ref_files[:5]:
                            print(f"      - {rf}")
                        if len(ref_files) > 5:
                            print(f"      ... and {len(ref_files) - 5} more")
                    if g.get('cross_file'):
                        print("    ** CROSS-FILE DUPLICATE **")

            # Cross-file summary
            if tar_map:
                cross_file = [g for g in groups if g.get('cross_file')]
                same_file = [g for g in groups if not g.get('cross_file')]
                print("\n  Summary:")
                print(f"    Same-file duplicates:  {len(same_file)} groups")
                print(f"    Cross-file duplicates: {len(cross_file)} groups")
                if cross_file:
                    # Find which file pairs share the most blocks
                    pair_counts = defaultdict(int)
                    for g in cross_file:
                        target = g.get('target_file', 'unknown')
                        for rf in g.get('referencing_files', []):
                            pair = tuple(sorted([target, rf]))
                            pair_counts[pair] += 1

                    print("\n  Top file pairs sharing blocks:")
                    for pair, count in sorted(pair_counts.items(), key=lambda x: -x[1])[:10]:
                        print(f"    {count} shared blocks: {pair[0]} <-> {pair[1]}")


if __name__ == "__main__":
    main()
