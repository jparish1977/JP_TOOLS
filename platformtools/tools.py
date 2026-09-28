"""Finding and running external programs: on PATH, in a Windows folder, or in WSL."""

from __future__ import annotations

import shutil
import subprocess
import sys
from collections.abc import Sequence
from pathlib import Path


class ToolMissingError(Exception):
    """A program the command needs is not installed. `hint` says how to get it."""

    def __init__(self, name: str, hint: str) -> None:
        super().__init__(name)
        self.name = name
        self.hint = hint


def find_tool(name: str, windows_paths: Sequence[Path] = (), *,
              windows_first: bool = True) -> list[str] | None:
    """The argv prefix that runs NAME, or None when it is not installed.

    Looks on PATH and, on Windows, in WINDOWS_PATHS (the folders a download
    that puts nothing on PATH usually lands in) and inside WSL. WINDOWS_PATHS
    are tried before PATH when WINDOWS_FIRST, after WSL otherwise.
    """
    windows = sys.platform == "win32"
    native = next((str(p) for p in windows_paths if p.exists()), None) if windows else None
    if native and windows_first:
        return [native]
    path = shutil.which(name)
    if path:
        return [path]
    in_wsl = _in_wsl(name) if windows else None
    if in_wsl:
        return in_wsl
    return [native] if native else None


def _in_wsl(name: str) -> list[str] | None:
    try:
        result = subprocess.run(
            ["wsl", "which", name], capture_output=True, text=True, check=False
        )
    except FileNotFoundError:
        # reason: no WSL on this box, so the tool is not inside it either
        return None
    found = result.stdout.strip()
    return ["wsl", found] if result.returncode == 0 and found else None


def require(name: str, hint: str, windows_paths: Sequence[Path] = ()) -> list[str]:
    """The argv prefix that runs NAME. Raises ToolMissingError when it is not installed."""
    prefix = find_tool(name, windows_paths)
    if prefix is None:
        raise ToolMissingError(name, hint)
    return prefix


def run(prefix: list[str], args: list[str], *, capture: bool = True) -> subprocess.CompletedProcess[str]:
    """Run a tool. The caller reads the exit status; a failure is not an exception."""
    return subprocess.run([*prefix, *args], capture_output=capture, text=True, check=False)
