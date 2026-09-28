"""Fake chdman and converters for the chd.py suites, and running chd.py in-process.

Each fake does just enough of the real tool's job for chd.py to carry on:
chdman writes the --output file, or for `info` prints a SHA1 of the input's
bytes and whatever FAKE_INFO holds; FAKE_RC is its exit status.
"""

import chd
from fake_tools import run_main

FAKE_CHDMAN = """\
import hashlib
args = sys.argv[1:]
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
import shutil
shutil.copyfile(sys.argv[1], sys.argv[2])
"""

# 7z x SRC -oDIR: extracts a zip.
FAKE_7Z = """\
import zipfile
zipfile.ZipFile(sys.argv[2]).extractall(sys.argv[3][2:])
"""


def cli(*args: str) -> tuple[int, str]:
    """(exit status, everything printed) for `chd.py ARGS`."""
    return run_main(chd.main, *args)
