#!/usr/bin/env python3
"""
JP_TOOLS/tests/test_ntfs_recovery.py
The NTFS recovery tools (ntfs-extract.py, ntfs-size.py, ntfs-inventory.py)
and their shared listing.

ntfsls reports size 0 for a directory AND for an empty file; branching on
size made every zero-byte file a directory that was never copied, silent
data loss with a plausible file count. `-F` marks directories with "/".

Two halves:
  - a FAKE ntfsls/ntfscat serving a tree from JSON: directory recursion, the
    depth limit, extraction (copy, skip, short read, failure, a name that
    escapes the destination) and inventory resume. Runs anywhere.
  - a REAL NTFS image built with mkntfs and ntfscp, holding an empty file and
    a small one: the empty file listed and copied as a file. Needs ntfs-3g;
    SKIP without it. (Directories in a real image need a root mount, which
    is why the directory cases use the fake.)

    python tests/test_ntfs_recovery.py
"""

import contextlib
import io
import json
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

from _harness import check, check_true, load_script, run
from fake_tools import calls, env, fake_tools, run_main
from ntfstools.listing import parse_listing

EXTRACT = load_script("ntfs-extract.py")
SIZE = load_script("ntfs-size.py")
INVENTORY = load_script("ntfs-inventory.py")

# FAKE_TREE is {"/dir": [[size, name, is_dir], ...]}; FAKE_DATA is {"/path": content}.
FAKE_NTFSLS = """\
path = sys.argv[sys.argv.index("-p") + 1]
for size, name, is_dir in json.loads(os.environ["FAKE_TREE"]).get(path.rstrip("/") or "/", []):
    print(f"{size:>10} Jan  1 00:00 2020 {name}{'/' if is_dir else ''}")
"""
FAKE_NTFSCAT = """\
data = json.loads(os.environ["FAKE_DATA"])
if sys.argv[2] not in data:
    print("ntfscat: read error", file=sys.stderr)
    sys.exit(1)
sys.stdout.write(data[sys.argv[2]])
"""

TREE = {
    "/": [[0, "docs", True], [0, "empty.bin", False], [5, "top.txt", False], [0, "$Extend", True]],
    "/docs": [[3, "a.txt", False], [0, "deep", True], [4, "short.txt", False], [2, "bad.txt", False]],
    "/docs/deep": [[6, "b.txt", False]],
}
DATA = {"/empty.bin": "", "/top.txt": "12345", "/docs/a.txt": "abc", "/docs/deep/b.txt": "abcdef",
        "/docs/short.txt": "ab"}


def _fakes(image: Path) -> contextlib.AbstractContextManager[None]:
    """The fake tree in the environment; IMAGE is a plain file standing in for the device."""
    image.write_bytes(b"not really ntfs")
    return env(FAKE_TREE=json.dumps(TREE), FAKE_DATA=json.dumps(DATA))


def test_parse_listing() -> None:
    text = "        0 Jan  1 00:00 2020 ./\n        0 Jan  1 00:00 2020 d/\n       11 Jan  1 00:00 2020 f\n"
    check("entries", [tuple(e) for e in parse_listing(text)], [(0, "d", True), (11, "f", False)])


def test_walk_and_depth_limit() -> None:
    with fake_tools(ntfsls=FAKE_NTFSLS) as (w, _log), _fakes(w / "img"):
        found = [p for p, _ in EXTRACT.collect(str(w / "img"), "/", 6, 30, 0)]
        check("every file, empty ones included", sorted(found),
              ["/docs/a.txt", "/docs/bad.txt", "/docs/deep/b.txt", "/docs/short.txt",
               "/empty.bin", "/top.txt"])
        err = io.StringIO()
        with contextlib.redirect_stderr(err):
            shallow = [p for p, _ in EXTRACT.collect(str(w / "img"), "/", 1, 30, 0)]
        check_true("depth 1 stops above deep/", "/docs/deep/b.txt" not in shallow and len(shallow) == 5)
        check_true("and says so", "depth limit 1 reached" in err.getvalue())


def test_extract() -> None:
    """Copies, skips what is present, rejects a short read and a failed read."""
    with fake_tools(keep_path=True, ntfsls=FAKE_NTFSLS, ntfscat=FAKE_NTFSCAT) as (w, log), _fakes(w / "img"):
        dest = w / "out"
        (dest / "docs").mkdir(parents=True)
        (dest / "top.txt").write_text("12345")
        code, out = run_main(EXTRACT.main, str(w / "img"), "/", str(dest))
        check("exit: failures present", code, 1)
        check_true("counts", "copied 3, skipped 1 already present, failed 2" in out)
        check("empty file copied as a file", (dest / "empty.bin").read_bytes(), b"")
        check("deep file", (dest / "docs" / "deep" / "b.txt").read_text(), "abcdef")
        check("no partial files left", sorted(p.name for p in dest.rglob("*.part")), [])
        check("present file not re-read", [c for c in calls(log) if c[-1] == "/top.txt" and c[0] == "ntfscat"], [])


def test_containment() -> None:
    for root, candidate, expected in (("/dest", "/dest/sub/file.bin", True),
                                      ("/dest", "/dest/../../etc/passwd", False),
                                      ("/dest", "/dest", True), ("/dest", "/destination/other", False)):
        check(f"within({candidate})", EXTRACT.within(root, candidate), expected)


def test_size() -> None:
    with fake_tools(keep_path=True, ntfsls=FAKE_NTFSLS) as (w, _log), _fakes(w / "img"):
        code, out = run_main(SIZE.main, str(w / "img"))
        check("exit", code, 0)
        check_true("docs subtree summed", "docs" in out and "4 files" in out)
        check_true("total", "TOTAL" in out and "      6 files" in out)


def test_inventory_resumes_an_interrupted_entry() -> None:
    with fake_tools(keep_path=True, ntfsls=FAKE_NTFSLS) as (w, _log), _fakes(w / "img"):
        tsv = w / "inv.tsv"
        tsv.write_text("#path\tsize\n/top.txt\t5\n#done\ttop.txt\n/docs/a.txt\t3\n")
        code, out = run_main(INVENTORY.main, str(w / "img"), str(tsv))
        check("exit", code, 0)
        check_true("docs re-read", "re-recording unfinished: docs" in out)
        rows = [line.split("\t")[0] for line in tsv.read_text().splitlines() if not line.startswith("#")]
        check("each file once, $Extend never", sorted(rows),
              ["/docs/a.txt", "/docs/bad.txt", "/docs/deep/b.txt", "/docs/short.txt",
               "/empty.bin", "/top.txt"])
        check("importing it does not run it", run_main(INVENTORY.main, "--help")[0], 0)


def _real_image(directory: Path) -> Path | None:
    """A 20MB NTFS image holding empty.bin and small.bin, or None (said) when it cannot be built."""
    image = directory / "test.ntfs"
    with open(image, "wb") as handle:
        handle.truncate(20 * 1024 * 1024)
    steps = [["mkntfs", "-F", "-q", "-L", "jptest", str(image)]]
    (directory / "empty.bin").touch()
    (directory / "small.bin").write_text("hello world")
    steps += [["ntfscp", "-f", str(image), str(directory / n), f"/{n}"] for n in ("empty.bin", "small.bin")]
    for step in steps:
        made = subprocess.run(step, check=False, capture_output=True)
        if made.returncode != 0:
            print(f"SKIP: cannot build an NTFS fixture here ({step[0]}: "
                  f"{made.stderr.decode('utf-8', 'replace').strip()[:120]})")
            return None
    return image


def ntfs_test_a_real_empty_file_is_a_file() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        d = Path(tmp)
        image = _real_image(d)
        if image is None:
            return
        found = dict(EXTRACT.collect(str(image), "/", 6, 30, 0))
        check("empty.bin found as a file", found.get("/empty.bin"), 0)
        check("small.bin", found.get("/small.bin"), 11)
        check("extract", run_main(EXTRACT.main, str(image), "/", str(d / "out"))[0], 0)
        check("empty copied", (d / "out" / "empty.bin").read_bytes(), b"")
        check("small copied", (d / "out" / "small.bin").read_text(), "hello world")


if __name__ == "__main__":
    tests = dict(globals())
    missing = [t for t in ("mkntfs", "ntfsls", "ntfscat", "ntfscp") if shutil.which(t) is None]
    if missing:
        print(f"SKIP: ntfs-3g tools not installed ({', '.join(missing)}); the fake-tool checks run alone")
    else:
        tests.update({k.removeprefix("ntfs_"): v for k, v in globals().items()
                      if k.startswith("ntfs_test_")})
    sys.exit(run(tests, "ntfs recovery"))
