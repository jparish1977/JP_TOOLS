"""What the chd.py suites share: fake tools on PATH, and running chd.py in-process.

A fake tool is a small Python script that appends its argv, as one JSON
line, to the log named by FAKE_LOG, then does just enough of the real tool's
job for chd.py to carry on: it writes the --output file, or for `info`
prints a SHA1 of the input's bytes and whatever FAKE_INFO holds.
"""

import contextlib
import io
import json
import os
import sys
import tempfile
from collections.abc import Iterator
from pathlib import Path

import chd

FAKE_CHDMAN = """\
import hashlib, json, os, sys
args = sys.argv[1:]
with open(os.environ["FAKE_LOG"], "a") as fh:
    fh.write(json.dumps(["chdman", *args]) + "\\n")
rc = int(os.environ.get("FAKE_RC", "0"))
if args[0] == "info":
    data = open(args[args.index("--input") + 1], "rb").read()
    print("SHA1:         " + hashlib.sha1(data).hexdigest())
    print(os.environ.get("FAKE_INFO", ""))
elif rc == 0 and "--output" in args:
    open(args[args.index("--output") + 1], "wb").write(b"chd")
if rc:
    print("fake chdman failed", file=sys.stderr)
sys.exit(rc)
"""

# nrg2iso SRC DST: copies SRC to DST.
FAKE_COPIER = """\
import json, os, shutil, sys
with open(os.environ["FAKE_LOG"], "a") as fh:
    fh.write(json.dumps([os.path.basename(sys.argv[0]), *sys.argv[1:]]) + "\\n")
shutil.copyfile(sys.argv[1], sys.argv[2])
"""

# 7z x SRC -oDIR: extracts a zip.
FAKE_7Z = """\
import json, os, sys, zipfile
with open(os.environ["FAKE_LOG"], "a") as fh:
    fh.write(json.dumps(["7z", *sys.argv[1:]]) + "\\n")
zipfile.ZipFile(sys.argv[2]).extractall(sys.argv[3][2:])
"""


def install(bin_dir: Path, name: str, body: str) -> None:
    tool = bin_dir / name
    tool.write_text(f"#!{sys.executable}\n{body}")
    tool.chmod(0o755)


@contextlib.contextmanager
def fake_tools(**tools: str) -> Iterator[tuple[Path, Path]]:
    """(work dir, log) with only the named fake TOOLS on PATH.

    PATH is replaced, not extended, so a real chdman on the box cannot answer
    in place of the fake one. Pass names that are not identifiers, such as
    7z, as `**{"7z": FAKE_7Z}`.
    """
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        bin_dir = root / "bin"
        work = root / "work"
        bin_dir.mkdir()
        work.mkdir()
        for name, body in tools.items():
            install(bin_dir, name, body)
        log = root / "log"
        log.touch()
        with env(PATH=str(bin_dir), FAKE_LOG=str(log)):
            yield work, log


@contextlib.contextmanager
def env(**values: str) -> Iterator[None]:
    """Set environment VALUES for the duration, then put back what was there."""
    saved = {k: os.environ.get(k) for k in values}
    os.environ.update(values)
    try:
        yield
    finally:
        for k, v in saved.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v


def calls(log: Path) -> list[list[str]]:
    return [json.loads(line) for line in log.read_text().splitlines()]


def cli(*args: str) -> tuple[int, str]:
    """(exit status, everything printed) for `chd.py ARGS`."""
    out = io.StringIO()
    with contextlib.redirect_stdout(out):
        code = chd.main(list(args))
    return code, out.getvalue()
