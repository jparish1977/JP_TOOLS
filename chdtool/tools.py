"""Finding and running the external programs: chdman and the format converters."""

from __future__ import annotations

import shutil
import subprocess
import sys
from pathlib import Path

# Where a MAME download usually leaves chdman on Windows, which puts nothing
# on PATH.
_WINDOWS_CHDMAN = (
    Path.home() / "tools" / "mame" / "chdman.exe",
    Path.home() / "tools" / "chdman.exe",
)


class ToolMissingError(Exception):
    """A program the command needs is not installed. `hint` says how to get it."""

    def __init__(self, name: str, hint: str) -> None:
        super().__init__(name)
        self.name = name
        self.hint = hint


def find_tool(name: str) -> list[str] | None:
    """The argv prefix that runs NAME, or None when it is not installed.

    Looks in chdman's usual Windows folders (chdman only, Windows only), then
    PATH, then inside WSL on Windows.
    """
    if sys.platform == "win32" and name == "chdman":
        for p in _WINDOWS_CHDMAN:
            if p.exists():
                return [str(p)]
    path = shutil.which(name)
    if path:
        return [path]
    if sys.platform == "win32":
        return _in_wsl(name)
    return None


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


def require(name: str, hint: str) -> list[str]:
    """The argv prefix that runs NAME. Raises ToolMissingError when it is not installed."""
    prefix = find_tool(name)
    if prefix is None:
        raise ToolMissingError(name, hint)
    return prefix


def run(prefix: list[str], args: list[str], *, capture: bool = True) -> subprocess.CompletedProcess[str]:
    """Run a tool. The caller reads the exit status; a failure is not an exception."""
    return subprocess.run([*prefix, *args], capture_output=capture, text=True, check=False)
