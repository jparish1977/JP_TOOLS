"""Windows drives and WSL: a drive letter's physical disk, admin rights, WSL paths.

A drive letter means something only on Windows. Elsewhere `F:` is an
ordinary relative path, so drive_letter() answers None off Windows.
"""

from __future__ import annotations

import ctypes
import os
import subprocess
import sys
from pathlib import Path


def drive_letter(source: str) -> str | None:
    """The upper-case letter when SOURCE is a Windows drive such as `F:`, on Windows only."""
    if sys.platform == "win32" and len(source) == 2 and source[0].isalpha() and source[1] == ":":
        return source[0].upper()
    return None


def disk_number(letter: str) -> int | None:
    """The physical disk holding drive LETTER, as PowerShell reports it, or None."""
    result = subprocess.run(
        ["powershell", "-NoProfile", "-Command",
         f"Get-Partition -DriveLetter {letter} | Select-Object -ExpandProperty DiskNumber"],
        capture_output=True, text=True, check=False,
    )
    out = result.stdout.strip()
    return int(out) if result.returncode == 0 and out.isdigit() else None


def physical_drive(number: int) -> str:
    """The Windows device path of physical disk NUMBER."""
    return f"\\\\.\\PhysicalDrive{number}"


def is_admin() -> bool:
    """Administrator on Windows, root elsewhere."""
    if sys.platform == "win32":
        return bool(ctypes.windll.shell32.IsUserAnAdmin())
    return os.geteuid() == 0


def relaunch_elevated() -> int:
    """Start this command again as administrator, in a new window. Returns this run's status.

    Only Windows can do this; elsewhere the answer is sudo, and the status is
    1. On Windows the elevated copy runs on its own, so the status here says
    only whether it was started.
    """
    if sys.platform != "win32":
        print("Error: This needs root. Run it again with sudo.")
        return 1
    print("Requesting administrator privileges; the elevated run opens in a new window.")
    params = subprocess.list2cmdline(sys.argv)
    # ShellExecuteW returns a value above 32 when it started the program.
    started = ctypes.windll.shell32.ShellExecuteW(None, "runas", sys.executable, params, None, 1)
    return 0 if started > 32 else 1


def wsl_path(path: Path | str) -> str:
    """C:\\foo\\bar as WSL sees it, /mnt/c/foo/bar; any other path with forward slashes."""
    p = str(path)
    if len(p) >= 2 and p[1] == ":":
        return f"/mnt/{p[0].lower()}/{p[2:].replace(chr(92), '/').lstrip('/')}"
    return p.replace("\\", "/")


def usb_drive_advice(source: str, number: int) -> list[str]:
    """Why WSL cannot read a USB drive directly, and the ways round it."""
    return [
        f"{source} is {physical_drive(number)} (disk {number}).",
        "WSL cannot read a USB drive as a block device until it is attached:",
        "  usbipd list",
        "  usbipd bind --busid <BUSID>",
        "  usbipd attach --wsl --busid <BUSID>",
        "and then run this again on the /dev/sdX device it appears as.",
        "Or boot a Linux live USB and run it there.",
    ]
