#!/usr/bin/env python3
"""
JP_TOOLS/tests/test_check_help.py
check.py --help says exactly what tests/fixtures/check-help.txt holds, at a
fixed width of 100 columns. The help is the flags' documentation, so a change
to it is a change made on purpose, in the fixture too. Run in-process, so
mutation testing sees every help string it pins.

To refresh the fixture after changing a flag's help:

    COLUMNS=100 python3 check.py --help > tests/fixtures/check-help.txt

    python3 tests/test_check_help.py
"""

import contextlib
import io
import os
import sys
from unittest import mock

from _harness import ROOT, check, load_script, run

check_mod = load_script("check.py")
FIXTURE = ROOT / "tests" / "fixtures" / "check-help.txt"


def test_the_help_is_the_fixture() -> None:
    out = io.StringIO()
    code = None
    with mock.patch.object(sys, "argv", ["check.py", "--help"]), \
            mock.patch.dict(os.environ, {"COLUMNS": "100"}), contextlib.redirect_stdout(out):
        try:
            check_mod.main()
        except SystemExit as e:
            code = e.code
    check("--help exits 0", code, 0)
    check("and prints the fixture, line for line", out.getvalue().splitlines(),
          FIXTURE.read_text(encoding="utf-8").splitlines())


if __name__ == "__main__":
    sys.exit(run(globals(), "check.py help"))
