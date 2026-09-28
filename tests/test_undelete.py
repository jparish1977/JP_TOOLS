#!/usr/bin/env python3
"""
JP_TOOLS/tests/test_undelete.py
undelete.py against fake blkid, ntfsundelete, fls and extundelete: the
command each path runs, detection, and the refusals.

    python tests/test_undelete.py
"""

import sys

from _harness import check, check_true, load_script, run
from fake_tools import calls, env, fake_tools, run_main

UNDELETE = load_script("undelete.py")

FAKE_BLKID = 'print(os.environ.get("FAKE_BLKID", ""))\n'
FAKE_FLS = 'print("r/r * 12: gone.js\\nr/r * 13: gone.psd\\nd/d * 14: olddir")\n'
FAKE_OK = "sys.exit(0)\n"


def test_ntfs_recovery_detected_by_blkid() -> None:
    with fake_tools(blkid=FAKE_BLKID, ntfsundelete=FAKE_OK) as (w, log), \
            env(FAKE_BLKID='/dev/x: TYPE="ntfs"'):
        code, _ = run_main(UNDELETE.main, "disk.img", str(w / "out"), "--match", "*.js")
        check("exit", code, 0)
        check("command", calls(log)[-1], ["ntfsundelete", "disk.img", "--undelete", "--destination",
                                         str(w / "out"), "--match", "*.js"])


def test_ntfs_scan_without_match_recovers_nothing() -> None:
    with fake_tools(ntfsundelete=FAKE_OK) as (_w, log):
        run_main(UNDELETE.main, "disk.img", "--type", "ntfs", "--list-only")
        check("scan", calls(log), [["ntfsundelete", "disk.img", "--scan"]])


def test_ext_listing_filters_by_match() -> None:
    with fake_tools(fls=FAKE_FLS) as (_w, _log):
        code, out = run_main(UNDELETE.main, "disk.img", "--type", "ext", "--list-only", "--match", ".js")
        check("exit", code, 0)
        check_true("only the match", "gone.js" in out and "gone.psd" not in out)
        check_true("counted", "Total deleted entries found: 1" in out)


def test_ext_recovery_with_a_match_is_refused() -> None:
    """extundelete takes no pattern: ignoring --match would restore everything."""
    with fake_tools(extundelete=FAKE_OK) as (w, log):
        code, _ = run_main(UNDELETE.main, "disk.img", str(w / "out"), "--type", "ext", "--match", "*.js")
        check("refused", code, 1)
        check("nothing ran", calls(log), [])
        check("without a match it runs", run_main(UNDELETE.main, "disk.img", str(w / "out"),
                                                  "--type", "ext")[0], 0)
        check("restore-all", calls(log)[-1][:4], ["extundelete", "disk.img", "--restore-all", "--output-dir"])


def test_undetected_and_missing_tools() -> None:
    with fake_tools(blkid=FAKE_BLKID) as (w, _log), env(FAKE_BLKID="vfat"):
        check("undetected", run_main(UNDELETE.main, "disk.img", str(w / "out"))[0], 1)
    with fake_tools() as (w, _log):
        code, out = run_main(UNDELETE.main, "disk.img", str(w / "out"), "--type", "ntfs")
        check("missing tool", code, 2)
        check_true("says how", "install ntfs-3g" in out)


if __name__ == "__main__":
    sys.exit(run(globals(), "undelete"))
