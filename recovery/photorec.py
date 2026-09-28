"""Running photorec, and sorting and summing up what it recovered."""

from __future__ import annotations

from datetime import datetime
from pathlib import Path

from platformtools.tools import find_tool
from platformtools.windows import wsl_path

# Where the TestDisk download for Windows usually lands; it puts nothing on PATH.
WINDOWS_PHOTOREC = (
    Path.home() / "tools" / "testdisk" / "testdisk-7.1" / "photorec_win.exe",
    Path.home() / "tools" / "testdisk" / "photorec_win.exe",
)

PHOTOREC_HINT = (
    "Install with:\n"
    "  Ubuntu/Debian: sudo apt install testdisk\n"
    "  macOS: brew install testdisk\n"
    "  Windows: download from https://www.cgsecurity.org/wiki/TestDisk_Download\n"
    "           and extract to ~/tools/testdisk/\n"
    "  Windows WSL: wsl sudo apt install testdisk"
)

# photorec writes into <output>/recup_dir.1, recup_dir.2, ...
RECUP_BASE = "recup_dir"
REPORT_NAME = "report.xml"
SUMMARY_NAME = "recovery_summary.json"


def find_photorec(prefer_windows: bool) -> list[str] | None:
    """photorec's argv prefix. PREFER_WINDOWS picks the Windows build over PATH and WSL,
    which is the build that can read a Windows physical drive."""
    return find_tool("photorec", WINDOWS_PHOTOREC, windows_first=prefer_windows)


def photorec_args(prefix: list[str], source: str, output: Path, cmd_str: str) -> list[str]:
    """The full photorec command: recover SOURCE into OUTPUT, selected by CMD_STR."""
    out = wsl_path(output) if prefix[0] == "wsl" else str(output)
    return [*prefix, "/d", f"{out}/{RECUP_BASE}", "/cmd", source, cmd_str]


def organize_output(output: Path) -> int:
    """Move recovered files from recup_dir.N into one directory per extension.

    Returns how many moved. photorec's own report.xml is not a recovered file:
    it stays in its recup_dir, and so does that recup_dir.
    """
    moved = 0
    for recup in sorted(output.glob(f"{RECUP_BASE}.*")):
        for f in sorted(recup.iterdir()):
            if f.is_file() and f.name != REPORT_NAME:
                dest = output / (f.suffix.lower().lstrip(".") or "unknown")
                dest.mkdir(exist_ok=True)
                f.rename(dest / f.name)
                moved += 1
        try:
            recup.rmdir()
        except OSError:
            # reason: not empty, so it holds the report or something this did
            # not move, and stays where photorec put it
            continue
    return moved


def summarize(source: str, output: Path, profile: str, rc: int, elapsed: float) -> dict[str, object]:
    """What a run recovered, for the report and for recovery_summary.json."""
    files = [f for f in output.rglob("*")
             if f.is_file() and f.name not in (SUMMARY_NAME, REPORT_NAME)]
    total = sum(f.stat().st_size for f in files)
    return {
        "source": source,
        "output": str(output),
        "profile": profile,
        "files_recovered": len(files),
        "total_size_bytes": total,
        "total_size_mb": round(total / 1024**2, 1),
        "elapsed_seconds": round(elapsed, 1),
        "exit_code": rc,
        "timestamp": datetime.now().astimezone().isoformat(),
    }
