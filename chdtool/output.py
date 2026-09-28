"""What every command prints the same way."""

from __future__ import annotations

import subprocess
from pathlib import Path


def mb(size: int) -> float:
    return round(size / 1024**2, 1)


def gb(size: int) -> float:
    return round(size / 1024**3, 2)


def show(result: subprocess.CompletedProcess[str]) -> None:
    """Pass a tool's own output through."""
    print(result.stdout)
    if result.stderr:
        print(result.stderr)


def missing(what: str, path: Path) -> int:
    """Report a missing input. Returns the exit status for it."""
    print(f"Error: {what} not found: {path}")
    return 1


def not_a_directory(path: Path) -> int:
    print(f"Error: Not a directory: {path}")
    return 1


def dry_run(args: list[str]) -> int:
    print(f"[DRY RUN] Would run: chdman {' '.join(args)}")
    return 0
