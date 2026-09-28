#!/usr/bin/env python3
"""Read a CHD v5 hunk map: how each hunk is stored, which hunks are
duplicates of others, and which files in a tar those duplicates belong to.

Usage:
    python chd-hunkmap.py <file.chd>                  Hunk map summary
    python chd-hunkmap.py <file.chd> --self-refs      Duplicate hunk groups
    python chd-hunkmap.py <file.chd> --trace <tar>    Groups traced to tar members
    python chd-hunkmap.py <file.chd> --json           The summary (and groups) as JSON

The map is decoded as chdman writes it and checked against the CRC16 it
carries, so a map this tool cannot decode is an error, never a summary.
Tracing assumes the CHD was made from the tar with `chd.py create`.

Exit status: 0 read, 1 unreadable or not CHD v5.
"""

from __future__ import annotations

import argparse
import dataclasses
import json
import sys
import tarfile
from collections import Counter
from pathlib import Path

from chdmap.header import Header, read_header
from chdmap.hunkmap import Hunk, Kind, read_map
from chdmap.trace import Group, Member, self_ref_groups, shared_pairs, tar_members


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument("chd", help="CHD file to analyze")
    parser.add_argument("--self-refs", action="store_true",
                        help="List self-referencing (duplicate) hunk groups")
    parser.add_argument("--trace", metavar="TAR",
                        help="Trace duplicate groups to the members of this tar")
    parser.add_argument("--json", action="store_true", help="Output as JSON, and nothing else")
    parser.add_argument("--top", type=int, default=20,
                        help="Duplicate groups to list (default: 20; JSON lists all)")
    return parser


def _mb(size: int) -> float:
    return round(size / 1024**2, 1)


_KIND_NAMES = {Kind.NONE: "uncompressed", Kind.SELF: "self_ref", Kind.PARENT: "parent_ref"}


def _kind_name(hunk: Hunk, header: Header) -> str:
    """The codec's own name for a compressed hunk, else what the hunk is."""
    if hunk.kind < len(header.codecs):
        return header.codecs[hunk.kind]
    return _KIND_NAMES.get(hunk.kind, hunk.kind.name.lower())


def _summary(path: Path, header: Header, counts: Counter[str]) -> list[str]:
    total = sum(counts.values())
    lines = [
        f"CHD v{header.version}: {path.name}",
        f"  Logical size: {header.logical_size:,} bytes ({_mb(header.logical_size)} MB)",
        f"  Hunk size:    {header.hunk_size:,} bytes",
        f"  Total hunks:  {header.hunk_count:,}",
        f"  Codecs:       {', '.join(header.codecs) or 'none (uncompressed)'}",
        "",
        f"Hunk map ({total} entries):",
    ]
    for name, count in counts.most_common():
        lines.append(f"  {name:20s} {count:>8,}  ({round(count / total * 100, 1)}%)")
    dup = counts.get("self_ref", 0) * header.hunk_size
    lines += ["", f"  Self-referencing hunks: {counts.get('self_ref', 0)}",
              f"  Deduplicated bytes:    {dup:,} ({_mb(dup)} MB)"]
    return lines


def _group_lines(g: Group, traced: bool) -> list[str]:
    more = "..." if len(g.referrers) > 10 else ""
    lines = ["", f"  Target hunk {g.target} (offset 0x{g.target_offset:X})",
             f"    Referenced by {len(g.referrers)} other hunk(s): {g.referrers[:10]}{more}"]
    if traced:
        lines.append(f"    Target file: {g.target_file or 'unknown'}")
        if g.referencing_files:
            lines.append("    Referencing files:")
            lines += [f"      - {f}" for f in g.referencing_files[:5]]
            if len(g.referencing_files) > 5:
                lines.append(f"      ... and {len(g.referencing_files) - 5} more")
        if g.cross_file:
            lines.append("    ** CROSS-FILE DUPLICATE **")
    return lines


def _cross_file_lines(groups: list[Group]) -> list[str]:
    cross = sum(1 for g in groups if g.cross_file)
    outside = sum(1 for g in groups if g.target_file is None)
    lines = ["", "  Summary:",
             f"    Same-file duplicates:  {len(groups) - cross - outside} groups",
             f"    Cross-file duplicates: {cross} groups",
             f"    Outside any member:    {outside} groups (tar headers and padding)"]
    pairs = shared_pairs(groups)
    if pairs:
        lines += ["", "  Top file pairs sharing blocks:"]
        lines += [f"    {n} shared blocks: {a} <-> {b}" for (a, b), n in pairs.most_common(10)]
    return lines


def _groups_report(groups: list[Group], top: int, traced: bool) -> list[str]:
    lines = ["", f"Top {min(top, len(groups))} duplicate block groups:"]
    for g in groups[:top]:
        lines += _group_lines(g, traced)
    return lines + (_cross_file_lines(groups) if traced else [])


def _json(path: Path, header: Header, counts: Counter[str], groups: list[Group] | None) -> str:
    doc: dict[str, object] = {
        "chd": str(path),
        "header": dataclasses.asdict(header) | {"hunk_count": header.hunk_count},
        "hunk_summary": dict(counts),
        "total_self_refs": counts.get("self_ref", 0),
        "dedup_bytes": counts.get("self_ref", 0) * header.hunk_size,
    }
    if groups is not None:
        doc["self_ref_groups"] = [dataclasses.asdict(g) | {"cross_file": g.cross_file}
                                  for g in groups]
    return json.dumps(doc, indent=2)


def _read(path: Path) -> tuple[Header, list[Hunk]]:
    with path.open("rb") as f:
        header = read_header(f)
        return header, read_map(f, header)


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    path = Path(args.chd)
    try:
        header, hunks = _read(path)
        members: list[Member] | None = tar_members(Path(args.trace)) if args.trace else None
    except (OSError, ValueError, tarfile.TarError) as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1
    counts = Counter(_kind_name(h, header) for h in hunks)
    wanted = args.self_refs or args.trace
    groups = self_ref_groups(hunks, header.hunk_size, members) if wanted else None
    if args.json:
        print(_json(path, header, counts, groups))
        return 0
    lines = _summary(path, header, counts)
    if groups is not None:
        lines += _groups_report(groups, args.top, members is not None)
    print("\n".join(lines))
    return 0


if __name__ == "__main__":
    sys.exit(main())
