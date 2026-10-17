#!/usr/bin/env python3
"""
JP_TOOLS/tests/test_cli_help.py
chd.py's help, for itself and for every command, and deploy-all.py's help say
exactly what tests/fixtures/chd-help.txt and deploy-all-help.txt hold, at a
fixed width of 100 columns. The help is the commands' and flags'
documentation, so a change to it is a change made on purpose, in the fixture
too. Run in-process, as test_check_help.py runs check.py's, so mutation
testing sees every help string and command name it pins.

To refresh the fixtures after changing a command's or a flag's help:

    python3 tests/test_cli_help.py --write

    python3 tests/test_cli_help.py
"""

import contextlib
import io
import os
import sys
from collections.abc import Callable
from unittest import mock

from _harness import ROOT, check, load_script, run

chd = load_script("chd.py")
deploy_all = load_script("deploy-all.py")
FIXTURES = ROOT / "tests" / "fixtures"


def _help(main: Callable[[list[str]], int], prog: str, args: list[str]) -> tuple[object, str]:
    """(exit code, what was printed) for PROG ARGS --help, at 100 columns."""
    out = io.StringIO()
    code: object = None
    with mock.patch.object(sys, "argv", [prog]), mock.patch.dict(os.environ, {"COLUMNS": "100"}), \
            contextlib.redirect_stdout(out):
        try:
            main([*args, "--help"])
        except SystemExit as e:
            code = e.code
    return code, out.getvalue()


def _chd_help() -> tuple[list[object], str]:
    """chd.py --help, then each command's --help, each under a line naming it."""
    codes: list[object] = []
    text = ""
    for args in ([], *([name] for name in chd._COMMANDS)):
        code, out = _help(chd.main, "chd.py", args)
        codes.append(code)
        text += f"== chd.py {' '.join([*args, '--help'])}\n{out}"
    return codes, text


def _deploy_all_help() -> tuple[object, str]:
    return _help(deploy_all.main, "deploy-all.py", [])


def test_chd_help_is_the_fixture() -> None:
    codes, text = _chd_help()
    check("chd.py and its 13 commands: --help exits 0 for each", codes, [0] * 14)
    check("and prints the fixture, line for line", text.splitlines(),
          (FIXTURES / "chd-help.txt").read_text(encoding="utf-8").splitlines())


def test_deploy_all_help_is_the_fixture() -> None:
    code, text = _deploy_all_help()
    check("deploy-all.py --help exits 0", code, 0)
    check("and prints the fixture, line for line", text.splitlines(),
          (FIXTURES / "deploy-all-help.txt").read_text(encoding="utf-8").splitlines())


def _write() -> None:
    (FIXTURES / "chd-help.txt").write_text(_chd_help()[1], encoding="utf-8")
    (FIXTURES / "deploy-all-help.txt").write_text(_deploy_all_help()[1], encoding="utf-8")
    print("wrote tests/fixtures/chd-help.txt and tests/fixtures/deploy-all-help.txt")


if __name__ == "__main__":
    if sys.argv[1:] == ["--write"]:
        _write()
        sys.exit(0)
    sys.exit(run(globals(), "chd.py and deploy-all.py help"))
