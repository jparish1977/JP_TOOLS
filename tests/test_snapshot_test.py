#!/usr/bin/env python3
"""
JP_TOOLS/tests/test_snapshot_test.py
snapshot-test.py: the differences it finds between two JSON values, and its
exit status for identical, different, and could-not-capture. Capturing runs
a real Node script when node is installed.

    python tests/test_snapshot_test.py
"""

import json
import shutil
import sys
import tempfile
from pathlib import Path

from _harness import check, check_true, load_script, run
from fake_tools import run_main

SNAP = load_script("snapshot-test.py")


def _paths(a: object, b: object) -> list[str]:
    """The paths deep_diff reports between A and B."""
    return [d.path for d in SNAP.deep_diff(a, b)]


def test_deep_diff() -> None:
    check("identical", _paths({"a": [1, {"b": 2}]}, {"a": [1, {"b": 2}]}), [])
    check("a nested value", _paths({"a": [1, {"b": 2}]}, {"a": [1, {"b": 3}]}), ["a[1].b"])
    check("a key on one side", _paths({"a": 1}, {"b": 1}), ["a", "b"])
    check("length, then the common prefix", _paths([1, 2, 3], [1, 9]), [".length", "[1]"])
    check("1 equals 1.0", _paths(1, 1.0), [])
    check("within tolerance", _paths(0.1 + 0.2, 0.3), [])
    check("true is not 1", _paths(True, 1), ["(root)"])


def _snapshot(path: Path, data: object) -> str:
    path.write_text(json.dumps({"name": path.stem, "timestamp": "t", "data": data}))
    return str(path)


def test_compare_exit_status() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        d = Path(tmp)
        a = _snapshot(d / "a.json", {"count": 4})
        b = _snapshot(d / "b.json", {"count": 4})
        c = _snapshot(d / "c.json", {"count": 5})
        check("identical", run_main(SNAP.main, "compare", a, b)[0], 0)
        code, out = run_main(SNAP.main, "compare", a, c)
        check("different", code, 1)
        check_true("summary", "count: 4 -> 5  +1 (+25.0%)" in out)
        check("unreadable", run_main(SNAP.main, "compare", a, str(d / "nope.json"))[0], 2)


def node_test_capture_and_diff() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        d = Path(tmp)
        (d / "ok.mjs").write_text("console.log(JSON.stringify({count: 4}));\n")
        (d / "bad.mjs").write_text("process.exit(3);\n")
        out = str(d / "snaps")
        check("capture", run_main(SNAP.main, "capture", "--name", "base", "--script",
                                  str(d / "ok.mjs"), "--output", out)[0], 0)
        check("diff, same", run_main(SNAP.main, "diff", "--name", "after", "--script",
                                     str(d / "ok.mjs"), "--baseline", f"{out}/base.json",
                                     "--output", out)[0], 0)
        check("script failed", run_main(SNAP.main, "capture", "--name", "x", "--script",
                                        str(d / "bad.mjs"), "--output", out)[0], 2)


if __name__ == "__main__":
    tests = dict(globals())
    if shutil.which("node") is None:
        print("SKIP: node not installed; capture is not exercised")
    else:
        tests.update({k.removeprefix("node_"): v for k, v in globals().items()
                      if k.startswith("node_test_")})
    sys.exit(run(tests, "snapshot-test"))
