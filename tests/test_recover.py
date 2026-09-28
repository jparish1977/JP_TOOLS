#!/usr/bin/env python3
"""
JP_TOOLS/tests/test_recover.py
recover.py against a fake photorec: the command it runs, how it sorts what
comes back, and what it reports. The /cmd string itself, and the family
names against a real photorec, are test_photorec_fileopt.py.

    python tests/test_recover.py
"""

import json
import sys
from unittest import mock

from _harness import check, check_true, run
from fake_tools import calls, env, fake_tools, run_main
from platformtools import windows
from recover import main
from recovery.profiles import PROFILES, fileopt_command

# Writes two recovered files and photorec's report into <base>.1.
FAKE_PHOTOREC = """\
base = sys.argv[sys.argv.index("/d") + 1]
os.makedirs(base + ".1", exist_ok=True)
for name in ("f0000100.jpg", "f0000200.txt", "report.xml"):
    open(os.path.join(base + ".1", name), "w").write("x")
sys.exit(int(os.environ.get("FAKE_RC", "0")))
"""


def test_runs_photorec_and_sorts_by_extension() -> None:
    with fake_tools(photorec=FAKE_PHOTOREC) as (w, log):
        out = w / "out"
        code, printed = run_main(main, "disk.img", str(out), "--profile", "art")
        check("exit", code, 0)
        _, cmd_str = fileopt_command("art", PROFILES["art"])
        check("argv", calls(log), [["photorec", "/d", f"{out}/recup_dir", "/cmd",
                                    "disk.img", cmd_str]])
        check("sorted", sorted(p.name for p in out.iterdir()),
              ["jpg", "recovery_summary.json", "recup_dir.1", "txt"])
        check("report left with photorec's dir",
              [p.name for p in (out / "recup_dir.1").iterdir()], ["report.xml"])
        summary = json.loads((out / "recovery_summary.json").read_text())
        check("counted, report not", summary["files_recovered"], 2)
        check_true("printed", "Files recovered: 2" in printed)


def test_no_organize_and_json() -> None:
    with fake_tools(photorec=FAKE_PHOTOREC) as (w, _log):
        out = w / "out"
        code, printed = run_main(main, "disk.img", str(out), "--no-organize", "--json")
        check("exit", code, 0)
        check("left as photorec wrote it", sorted(p.name for p in out.iterdir()),
              ["recovery_summary.json", "recup_dir.1"])
        check("json only", json.loads(printed[printed.index("{"):])["files_recovered"], 2)


def test_a_failed_scan_is_its_status_and_not_sorted() -> None:
    with fake_tools(photorec=FAKE_PHOTOREC) as (w, _log), env(FAKE_RC="3"):
        code, _ = run_main(main, "disk.img", str(w / "out"))
        check("exit", code, 3)
        check_true("not sorted", (w / "out" / "recup_dir.1" / "f0000100.jpg").exists())


def test_dry_run_and_missing_photorec() -> None:
    with fake_tools(photorec=FAKE_PHOTOREC) as (w, log):
        check("dry run", run_main(main, "disk.img", str(w / "out"), "--dry-run")[0], 0)
        check("nothing ran", calls(log), [])
    with fake_tools() as (w, _log):
        code, printed = run_main(main, "disk.img", str(w / "out"))
        check("no photorec", code, 2)
        check_true("says how", "apt install testdisk" in printed)


def test_drive_letters_only_on_windows() -> None:
    check("elsewhere a path", windows.drive_letter("F:"), None)
    with mock.patch.object(sys, "platform", "win32"):
        check("letter", windows.drive_letter("f:"), "F")
        check("not a letter", windows.drive_letter("F:x"), None)


def test_a_windows_drive_letter() -> None:
    """The Windows build reads the physical drive; under WSL a USB drive stops with advice."""
    with mock.patch("recover.drive_letter", return_value="F"), \
            mock.patch("recover.disk_number", return_value=2):
        with mock.patch("recover.find_photorec", return_value=["wsl", "/usr/bin/photorec"]):
            code, printed = run_main(main, "F:", "out", "--dry-run")
        check("wsl stops", code, 1)
        check_true("advice", "usbipd attach --wsl" in printed)
        with mock.patch("recover.find_photorec", return_value=["photorec_win.exe"]):
            code, printed = run_main(main, "F:", "out", "--dry-run")
        check("windows build", code, 0)
        check_true("physical drive", "Source:    \\\\.\\PhysicalDrive2" in printed)


def test_wsl_paths() -> None:
    check("drive", windows.wsl_path("C:\\Users\\me\\out"), "/mnt/c/Users/me/out")
    check("posix", windows.wsl_path("/home/me/out"), "/home/me/out")


if __name__ == "__main__":
    sys.exit(run(globals(), "recover"))
