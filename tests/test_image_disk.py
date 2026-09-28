#!/usr/bin/env python3
"""
JP_TOOLS/tests/test_image_disk.py
image-disk.py: the ddrescue passes it runs, and that a failing pass is its
exit status. Against a fake ddrescue always; with ddrescue installed, one
real image of a file, compared byte for byte.

    python tests/test_image_disk.py
"""

import shutil
import sys
import tempfile
from pathlib import Path
from unittest import mock

from _harness import check, check_true, load_script, run
from fake_tools import calls, env, fake_tools, run_main

# Copies the source (third from last) to the image (second from last). With
# FAKE_FAIL_SCRAPE set, the retry pass (-d) exits 5.
FAKE_DDRESCUE = """\
import shutil
source, image = sys.argv[-3], sys.argv[-2]
if "-d" in sys.argv and os.environ.get("FAKE_FAIL_SCRAPE"):
    sys.exit(5)
shutil.copyfile(source, image)
"""


IMAGE_DISK = load_script("image-disk.py")


def test_two_passes_then_done() -> None:
    with fake_tools(ddrescue=FAKE_DDRESCUE) as (w, log):
        (w / "src").write_bytes(b"disk")
        code, printed = run_main(IMAGE_DISK.main, str(w / "src"), str(w / "d.img"))
        check("exit", code, 0)
        check("passes", [c[1:-3] for c in calls(log)], [["-f", "-n"], ["-f", "-d", "-r", "3"]])
        check("map file defaults beside the image", calls(log)[0][-1], str(w / "d.log"))
        check_true("says how to recover", "recover.py" in printed)


def test_a_failed_scrape_is_the_exit_status() -> None:
    with fake_tools(ddrescue=FAKE_DDRESCUE) as (w, _log), env(FAKE_FAIL_SCRAPE="1"):
        (w / "src").write_bytes(b"disk")
        code, printed = run_main(IMAGE_DISK.main, str(w / "src"), str(w / "d.img"))
        check("exit", code, 5)
        check_true("says resume", "Pass 2 (scrape) failed" in printed and "resume" in printed)


def test_no_scrape_dry_run_and_missing_tool() -> None:
    with fake_tools(ddrescue=FAKE_DDRESCUE) as (w, log):
        (w / "src").write_bytes(b"disk")
        run_main(IMAGE_DISK.main, str(w / "src"), str(w / "d.img"), "--no-scrape")
        check("one pass", len(calls(log)), 1)
        check("dry run", run_main(IMAGE_DISK.main, "src", str(w / "e.img"), "--dry-run")[0], 0)
        check("dry run ran nothing", len(calls(log)), 1)
    with fake_tools() as (w, _log):
        check("no ddrescue", run_main(IMAGE_DISK.main, "src", str(w / "d.img"))[0], 2)


def test_a_device_needs_root() -> None:
    with fake_tools(ddrescue=FAKE_DDRESCUE) as (w, log), \
            mock.patch.object(IMAGE_DISK, "is_admin", return_value=False):
        code, printed = run_main(IMAGE_DISK.main, "/dev/sdz", str(w / "d.img"))
        check("exit", code, 1)
        check_true("says sudo", "sudo" in printed)
        check("nothing ran", calls(log), [])
    check("WSL runs under sudo", IMAGE_DISK._privileged(["wsl", "/usr/bin/ddrescue"], "/dev/sdb"),
          ["wsl", "sudo", "/usr/bin/ddrescue"])


def ddrescue_test_a_real_image_is_identical() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        w = Path(tmp)
        data = bytes(range(256)) * 4096
        (w / "src").write_bytes(data)
        code, _ = run_main(IMAGE_DISK.main, str(w / "src"), str(w / "d.img"), "--no-scrape")
        check("exit", code, 0)
        check_true("identical", (w / "d.img").read_bytes() == data)
        check_true("map file written",
                   (w / "d.log").read_text().startswith("# Mapfile. Created by GNU ddrescue"))


if __name__ == "__main__":
    tests = dict(globals())
    if shutil.which("ddrescue") is None:
        print("SKIP: ddrescue not installed; the fake-tool checks run alone")
    else:
        tests.update({k.removeprefix("ddrescue_"): v for k, v in globals().items()
                      if k.startswith("ddrescue_test_")})
    sys.exit(run(tests, "image-disk"))
