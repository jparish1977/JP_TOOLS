#!/usr/bin/env python3
"""
JP_TOOLS/tests/test_node_bin.py
jpcheck.common._node_bin, in this process: where an npm tool is found.

WHAT IS CHECKED
    - Each tool root's node_modules/.bin is looked in, in order, and the exact
      path of the first match is returned.
    - In one .bin, NAME.cmd comes before NAME (Windows' npm writes the .cmd).
    - A root without the tool is passed over for the next one.
    - With no root holding it, PATH answers: NAME, else NAME.cmd, else None.
    tool_roots and shutil.which are replaced, so nothing on the box answers.

    python3 tests/test_node_bin.py
"""

import shutil
import sys
import tempfile
from pathlib import Path
from unittest import mock

from _harness import check, run
from jpcheck import common


def found(roots: list[Path], on_path: dict[str, str]) -> str | None:
    """_node_bin("prettier") with ROOTS as the tool roots and ON_PATH as PATH."""
    with mock.patch.object(common, "tool_roots", lambda: tuple(roots)), \
            mock.patch.object(shutil, "which", on_path.get):
        return common._node_bin("prettier")


def bin_dir(root: Path, *names: str) -> Path:
    b = root / "node_modules" / ".bin"
    b.mkdir(parents=True, exist_ok=True)
    for n in names:
        (b / n).write_text("", encoding="utf-8")
    return b


def test_a_tool_roots_node_modules() -> None:
    with tempfile.TemporaryDirectory() as td:
        a, b = Path(td) / "a", Path(td) / "b"
        bb = bin_dir(b, "prettier")
        check("the second root holds it: its exact path, PATH not asked",
              found([a, b], {"prettier": "/path/prettier"}), str(bb / "prettier"))
        ab = bin_dir(a, "prettier")
        check("both hold it: the first root's", found([a, b], {}), str(ab / "prettier"))
        bin_dir(a, "prettier.cmd")
        check("NAME.cmd before NAME in one .bin", found([a, b], {}), str(ab / "prettier.cmd"))


def test_a_bin_dir_with_only_the_cmd() -> None:
    with tempfile.TemporaryDirectory() as td:
        a = Path(td) / "a"
        ab = bin_dir(a, "prettier.cmd")
        check("only NAME.cmd: it is found", found([a], {}), str(ab / "prettier.cmd"))


def test_path_answers_when_no_root_has_it() -> None:
    with tempfile.TemporaryDirectory() as td:
        a = Path(td) / "a"
        bin_dir(a, "other")
        check("PATH's NAME", found([a], {"prettier": "/p/prettier", "prettier.cmd": "/p/prettier.cmd"}),
              "/p/prettier")
        check("else PATH's NAME.cmd", found([a], {"prettier.cmd": "/p/prettier.cmd"}), "/p/prettier.cmd")
        check("else None", found([a], {}), None)
        check("no roots at all: PATH", found([], {"prettier": "/p/prettier"}), "/p/prettier")


if __name__ == "__main__":
    sys.exit(run(globals(), "_node_bin"))
