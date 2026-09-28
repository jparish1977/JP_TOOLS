"""The commands that work on one image or one CHD. Each returns its exit status."""

from __future__ import annotations

import json
from pathlib import Path

from chdtool.chdman import (
    CONVERTIBLE,
    CreateOptions,
    chdman,
    compression_args,
    create_args,
    info_text,
    is_cd,
    parse_info,
)
from chdtool.output import dry_run, gb, mb, missing, show


def _report_ratio(size: int, dst: Path) -> None:
    out = dst.stat().st_size
    ratio = round(out / size * 100, 1) if size > 0 else 0
    print(f"\nCreated: {dst}")
    print(f"  Input:  {mb(size)} MB")
    print(f"  Output: {mb(out)} MB")
    print(f"  Ratio:  {ratio}% ({mb(size - out)} MB saved)")


def create(src: Path, dst: Path, opts: CreateOptions, dry: bool) -> int:
    """Create the CHD DST from the image SRC."""
    if not src.exists():
        return missing("Input file", src)
    if src.suffix.lower() not in CONVERTIBLE:
        print(f"Warning: Unknown extension {src.suffix.lower()}, treating as raw image")
    args = create_args(src, dst, opts)
    size = src.stat().st_size
    print(f"Creating CHD from: {src} ({gb(size)} GB)")
    print(f"Output: {dst}")
    print(f"Mode: {args[0]}")
    if dry:
        return dry_run(args)
    result = chdman(args)
    show(result)
    if result.returncode == 0 and dst.exists():
        _report_ratio(size, dst)
    return result.returncode


def verify(chd: Path) -> int:
    if not chd.exists():
        return missing("File", chd)
    print(f"Verifying: {chd}")
    result = chdman(["verify", "--input", str(chd)])
    show(result)
    print("PASSED -- CHD is valid" if result.returncode == 0
          else "FAILED -- CHD is corrupt or invalid")
    return result.returncode


def extract(chd: Path, dst: Path, force: bool, dry: bool) -> int:
    """Extract CHD to DST: a cue sheet and its tracks for a CD, the raw bytes otherwise."""
    if not chd.exists():
        return missing("File", chd)
    text = info_text(chd)
    if text is None:
        print(f"Error: chdman could not read {chd}")
        return 1
    subcmd = "extractcd" if is_cd(text) else "extractraw"
    args = [subcmd, "--input", str(chd), "--output", str(dst)]
    if force:
        args.append("--force")
    print(f"Extracting: {chd}")
    print(f"Output: {dst}")
    if dry:
        return dry_run(args)
    result = chdman(args)
    show(result)
    return result.returncode


def info(chd: Path, as_json: bool) -> int:
    """chdman's verbose info, or with AS_JSON the same fields as a JSON object."""
    if not chd.exists():
        return missing("File", chd)
    result = chdman(["info", "--verbose", "--input", str(chd)])
    if as_json and result.returncode == 0:
        print(json.dumps(parse_info(result.stdout), indent=2))
    else:
        show(result)
    return result.returncode


def delta(parent: Path, src: Path, dst: Path, dry: bool) -> int:
    """Create DST from SRC as a child of PARENT, storing only what differs."""
    if not parent.exists():
        return missing("Parent CHD", parent)
    if not src.exists():
        return missing("Input file", src)
    args = create_args(src, dst, CreateOptions(parent=parent))
    print("Creating delta CHD")
    print(f"  Parent: {parent}")
    print(f"  Input:  {src}")
    print(f"  Output: {dst}")
    if dry:
        return dry_run(args)
    result = chdman(args)
    show(result)
    if result.returncode == 0 and dst.exists():
        print(f"\n  Parent size: {mb(parent.stat().st_size)} MB")
        print(f"  Input size:  {mb(src.stat().st_size)} MB")
        print(f"  Delta size:  {mb(dst.stat().st_size)} MB")
    return result.returncode


def merge(parent: Path, child: Path, dst: Path, force: bool, dry: bool) -> int:
    """Write CHILD, read through its PARENT, as the standalone CHD DST."""
    for what, path in (("Parent CHD", parent), ("Child CHD", child)):
        if not path.exists():
            return missing(what, path)
    args = ["copy", "--input", str(child), "--inputparent", str(parent), "--output", str(dst)]
    if force:
        args.append("--force")
    print("Merging child CHD into standalone")
    print(f"  Parent: {parent}")
    print(f"  Child:  {child}")
    print(f"  Output: {dst}")
    if dry:
        return dry_run(args)
    result = chdman(args)
    show(result)
    return result.returncode


def convert(src: Path, dst: Path, compression: str | None, force: bool, dry: bool) -> int:
    """Re-compress the CHD SRC into DST."""
    if not src.exists():
        return missing("File", src)
    args = ["copy", "--input", str(src), "--output", str(dst), *compression_args(compression)]
    if force:
        args.append("--force")
    print(f"Re-compressing: {src}")
    print(f"Output: {dst}")
    if dry:
        return dry_run(args)
    size = src.stat().st_size
    result = chdman(args)
    show(result)
    if result.returncode == 0 and dst.exists():
        out = dst.stat().st_size
        print(f"\n  Before: {mb(size)} MB")
        print(f"  After:  {mb(out)} MB")
        if out < size:
            print(f"  Saved:  {mb(size - out)} MB")
        elif out > size:
            print(f"  Grew:   {mb(out - size)} MB (worse compression)")
    return result.returncode


def compare(first: Path, second: Path) -> int:
    """0 when both CHDs carry the same SHA1, 1 when they differ, 2 when either is unreadable."""
    sha1s: list[str] = []
    for chd in (first, second):
        text = info_text(chd) if chd.exists() else None
        sha1 = parse_info(text).get("sha1") if text is not None else None
        if not sha1:
            print(f"Error: could not read the SHA1 of {chd}")
            return 2
        print(f"{chd.name}")
        print(f"  Size: {mb(chd.stat().st_size)} MB")
        print(f"  SHA1: {sha1}")
        print()
        sha1s.append(sha1)
    if sha1s[0] == sha1s[1]:
        print("MATCH -- identical content")
        return 0
    print("DIFFERENT -- content differs")
    return 1
