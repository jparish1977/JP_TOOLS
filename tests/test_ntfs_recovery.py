#!/usr/bin/env python
"""
JP_TOOLS/tests/test_ntfs_recovery.py
Tests for the NTFS recovery tools, against a real NTFS filesystem.

Builds a small NTFS image with mkntfs, seeds it with the case that used to be
mishandled, and drives the tools' own functions against it. No pytest, no
dependencies -- the toolbox has none and this should not add the first.

THE BUG THIS PINS DOWN
    ntfsls reports size 0 for a directory *and* for an empty file, so code that
    branched on size treated every zero-byte file as a directory: it recursed
    into it, found nothing, and never copied it. On a recovery pass that is
    silent data loss, and the file count looks correct either way.

    Measured before the fix, on an image holding four files: collect() returned
    one of them.

Skips rather than fails when the environment cannot support it:
  - no ntfsls/ntfscat/mkntfs  -> skip (ntfs-3g not installed)
  - cannot mount              -> the directory-recursion checks are skipped;
                                 mounting needs root, and everything else still
                                 runs unprivileged against the image file

    python tests/test_ntfs_recovery.py
"""

import contextlib
import importlib.util
import io
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
REQUIRED = ("mkntfs", "ntfsls", "ntfscat", "ntfscp")


def load(name: str, path: Path):
    """Import a hyphenated script by path."""
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:
        raise ImportError(f"cannot load {path}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


class EnvironmentUnavailable(RuntimeError):
    """The tools are installed but this machine cannot build the fixture."""


def build_image(directory: Path) -> Path:
    """A 20MB NTFS volume holding an empty file and a normal one."""
    image = directory / "test.ntfs"
    with open(image, "wb") as handle:
        handle.truncate(20 * 1024 * 1024)
    # check=False plus an explicit raise: on a machine where the tools exist
    # but the environment cannot build an image, check=True raised an uncaught
    # CalledProcessError and turned CI red for a reason unrelated to any diff.
    # A missing capability is a SKIP, not a failure.
    made = subprocess.run(["mkntfs", "-F", "-q", "-L", "jptest", str(image)],
                          check=False, capture_output=True)
    if made.returncode != 0:
        raise EnvironmentUnavailable(
            f"mkntfs failed: {made.stderr.decode('utf-8', 'replace').strip()[:200]}")

    empty = directory / "empty.bin"
    empty.touch()
    small = directory / "small.bin"
    small.write_text("hello world")
    for source, name in ((empty, "/empty.bin"), (small, "/small.bin")):
        subprocess.run(["ntfscp", "-f", str(image), str(source), name],
                       check=True, capture_output=True)
    return image


def add_directories(image: Path, mountpoint: Path) -> bool:
    """Create a real directory tree inside the image. Needs root; may fail."""
    mountpoint.mkdir(exist_ok=True)
    try:
        subprocess.run(["mount", "-o", "loop", str(image), str(mountpoint)],
                       check=True, capture_output=True)
    except (subprocess.CalledProcessError, FileNotFoundError):
        return False
    try:
        (mountpoint / "realdir" / "deeper").mkdir(parents=True, exist_ok=True)
        (mountpoint / "realdir" / "inner.bin").touch()
        (mountpoint / "realdir" / "deeper" / "deep.bin").touch()
    finally:
        subprocess.run(["umount", str(mountpoint)], check=False, capture_output=True)
    return True


def main() -> int:
    missing = [t for t in REQUIRED if shutil.which(t) is None]
    if missing:
        print(f"SKIP: ntfs-3g tools not installed ({', '.join(missing)})")
        return 0

    extract = load("ntfs_extract", ROOT / "ntfs-extract.py")
    # The image is a plain file, so no privilege is needed to read it.
    extract.as_root = lambda cmd: cmd

    failures = []

    def check(condition: bool, message: str) -> None:
        if not condition:
            failures.append(message)

    with tempfile.TemporaryDirectory() as tmp:
        directory = Path(tmp)
        try:
            image = build_image(directory)
        except EnvironmentUnavailable as exc:
            print(f"SKIP: cannot build an NTFS fixture here ({exc})")
            return 0
        have_dirs = add_directories(image, directory / "mnt")

        rows = extract.listdir(str(image), "/", 30)
        entries = {name: (size, is_dir) for size, name, is_dir in rows}
        check(bool(rows), "listdir returned nothing")

        # An empty file must be a FILE. Branching on size made it a directory.
        check(entries.get("empty.bin") == (0, False),
              f"empty.bin should be (0, False), got {entries.get('empty.bin')}")
        check(entries.get("small.bin") == (11, False),
              f"small.bin should be (11, False), got {entries.get('small.bin')}")
        check(not any(n.endswith("/") for n in entries),
              "classify marker was not stripped from a name")

        found = {path for path, _ in extract.collect(str(image), "/", 0, 6, 30, 0)}
        check("/empty.bin" in found,
              "collect() dropped the zero-byte file (silent data loss)")
        check("/small.bin" in found, "collect() dropped /small.bin")

        if have_dirs:
            check(entries.get("realdir") == (0, True),
                  f"realdir should be (0, True), got {entries.get('realdir')}")
            for path in ("/realdir/inner.bin", "/realdir/deeper/deep.bin"):
                check(path in found, f"collect() did not recurse to {path}")

            # The depth limit must announce itself; a truncated tree otherwise
            # looks exactly like a complete one.
            buffer = io.StringIO()
            with contextlib.redirect_stderr(buffer):
                shallow = {p for p, _ in extract.collect(str(image), "/", 0, 1, 30, 0)}
            warned = buffer.getvalue()
            check("/realdir/deeper/deep.bin" not in shallow,
                  "depth limit did not actually stop the walk")
            check("depth limit" in warned,
                  "depth limit truncated the walk silently")
        else:
            print("  note: mounting needs root, so directory recursion and the "
                  "depth warning were not exercised")

    # Containment: names come off a damaged volume and this may run as root.
    for root, candidate, expected in (
        ("/dest", "/dest/sub/file.bin", True),
        ("/dest", "/dest/../../etc/passwd", False),
        ("/dest", "/dest", True),
        ("/dest", "/destination/other", False),
    ):
        actual = extract.within(root, candidate)
        check(actual == expected,
              f"within({root!r}, {candidate!r}) = {actual}, expected {expected}")

    # The sibling tools share the classification, so pin it there too. They are
    # read as source: ntfs-inventory.py parses argv at import, so it cannot be
    # imported without exiting.
    for name in ("ntfs-size.py", "ntfs-inventory.py"):
        source = (ROOT / name).read_text(encoding="utf-8")
        check('"-F"' in source, f"{name} does not pass -F to ntfsls")
        check("is_dir" in source, f"{name} still classifies by size")

    if failures:
        print("FAIL")
        for failure in failures:
            print(f"  - {failure}")
        return 1
    count = 16 if have_dirs else 11
    note = "" if have_dirs else " (directory cases skipped, needs root)"
    print(f"PASS: {count} checks{note}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
