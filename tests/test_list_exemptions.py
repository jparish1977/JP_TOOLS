#!/usr/bin/env python3
"""
JP_TOOLS/tests/test_list_exemptions.py
list-exemptions.py, in-process against a planted tree.

WHAT IS CHECKED
    - gather() reads a tree's project files (a venv inside it is skipped) or
      one file, and counts every line it read; a .py it cannot read is named
      on stderr and left out of the total, not counted as read.
    - The report lists every exemption, most branches first, with the share
      of lines exempt; a branchless one reads as a wrapper, a branchy one with
      no reason says so, and PENDING ones are counted apart.
    - --json gives the same facts; no exemptions says what was scanned.
    - Exit 0 whatever is found, 1 under --strict with a branchy exemption
      that states no reason, 2 for a path that does not exist.

    python3 tests/test_list_exemptions.py
"""

import contextlib
import io
import json
import re
import sys
import tempfile
from pathlib import Path

from _harness import check, check_true, load_script, run

le = load_script("list-exemptions.py")

PLANTED = """\
def wrap():  # pragma: no cover
    return 1

def branchy(x):  # pragma: no cover
    if x:
        return 1
    return 2

def pending(x):  # pragma: no cover reason: PENDING seams for the socket
    for i in x:
        print(i)
"""

REPORT = [
    "EXEMPT FROM COVERAGE: 3 function(s), 6 of 14 lines (42.9%)",
    "",
    "  function  lines  br  reason",
    "  branchy      3   1  *** NO REASON GIVEN ***",
    "  pending      2   1  PENDING seams for the socket",
    "  wrap         1   0  (wrapper, no branches)",
    "",
    "  1 of 3 say PENDING: seams owed, not untestable.",
    "  1 branchy exemption(s) state no reason.",
    "  A reason is not a justification. Read them; nothing else can.",
]


def _tree(root: Path) -> Path:
    (root / "sub").mkdir()
    (root / "a.py").write_text(PLANTED, encoding="utf-8")
    (root / "sub" / "b.py").write_text("x = 1\ny = 2\nz = 3\n", encoding="utf-8")
    (root / "venv" / "lib").mkdir(parents=True)
    (root / "venv" / "pyvenv.cfg").write_text("home = /usr\n", encoding="utf-8")
    (root / "venv" / "lib" / "dep.py").write_text("def f():  # pragma: no cover\n    pass\n",
                                                  encoding="utf-8")
    return root


def _main(*argv: str) -> tuple[int, str, str]:
    out, err = io.StringIO(), io.StringIO()
    with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
        rc = le.main(list(argv))
    return rc, out.getvalue(), err.getvalue()


def test_gather_reads_the_project_and_counts_its_lines() -> None:
    with tempfile.TemporaryDirectory() as td:
        root = _tree(Path(td))
        rows, total = le.gather(root)
        check("a tree: every exemption in its project files, the venv left out",
              [(r["function"], r["line"], r["lines"], r["branches"], r["reason"]) for r in rows],
              [("wrap", 1, 1, 0, ""), ("branchy", 4, 3, 1, ""),
               ("pending", 9, 2, 1, "PENDING seams for the socket")])
        check("  and every line read, venv's not", total, 14)
        rows, total = le.gather(root / "sub" / "b.py")
        check("one file: just that file", (rows, total), ([], 3))


def test_a_file_it_cannot_read_is_named_and_not_counted() -> None:
    with tempfile.TemporaryDirectory() as td:
        root = _tree(Path(td))
        (root / "odd.py").mkdir()  # a directory with a .py name: rglob finds it, reading fails
        err = io.StringIO()
        with contextlib.redirect_stderr(err):
            rows, total = le.gather(root)
        check("still the readable files' lines only", (len(rows), total), (3, 14))
        check_true("  and the unreadable one named, saying it is NOT in the total",
                   err.getvalue().startswith(f"list-exemptions: {root / 'odd.py'} unreadable (")
                   and err.getvalue().endswith("); NOT in the total\n"))


def test_the_report() -> None:
    with tempfile.TemporaryDirectory() as td:
        root = _tree(Path(td))
        rc, out, err = _main(str(root))
        check("every exemption, most branches first, with the share exempt", out.splitlines(), REPORT)
        check("  exit 0: reading the inventory is the point", (rc, err), (0, ""))
        rc, _, _ = _main(str(root), "--strict")
        check("--strict with a branchy exemption stating no reason: exit 1", rc, 1)


def test_a_report_with_no_pending_and_none_unjustified() -> None:
    with tempfile.TemporaryDirectory() as td:
        f = Path(td) / "w.py"
        f.write_text("def wrap():  # pragma: no cover\n    return 1\n", encoding="utf-8")
        rc, out, _ = _main(str(f), "--strict")
        check("only a wrapper: no PENDING or no-reason lines, and --strict passes",
              (rc, out.splitlines()),
              (0, ["EXEMPT FROM COVERAGE: 1 function(s), 1 of 2 lines (50.0%)", "",
                   "  function  lines  br  reason", "  wrap      1   0  (wrapper, no branches)", "",
                   "  A reason is not a justification. Read them; nothing else can."]))
        out_buf = io.StringIO()
        with contextlib.redirect_stdout(out_buf):
            le.print_report([{"function": "f", "lines": 0, "branches": 0, "reason": "r"}], 0, 0, [])
        check_true("no lines scanned: the share is 0.0%, not a division", "(0.0%)" in out_buf.getvalue())


def test_json() -> None:
    with tempfile.TemporaryDirectory() as td:
        root = _tree(Path(td))
        rc, out, _ = _main(str(root), "--json")
        rows, _ = le.gather(root)
        check("--json: the rows and the totals, indented 2",
              (rc, out), (0, json.dumps({"exemptions": rows, "exempt_lines": 6, "total_lines": 14,
                                         "unjustified": 1}, indent=2) + "\n"))


def test_nothing_exempt_and_nothing_there() -> None:
    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        (root / "b.py").write_text("x = 1\n", encoding="utf-8")
        check("no exemptions: says what was scanned", _main(str(root)),
              (0, f"No coverage exemptions under {root} (1 lines scanned).\n", ""))
        missing = root / "gone"
        check("a path that does not exist: exit 2, said on stderr", _main(str(missing)),
              (2, "", f"no such path: {missing}\n"))
        with contextlib.chdir(root):
            check("no target: the current directory", _main()[1],
                  "No coverage exemptions under . (1 lines scanned).\n")


def test_help() -> None:
    out = io.StringIO()
    with contextlib.redirect_stdout(out), contextlib.suppress(SystemExit):  # reason: --help prints, then argparse exits 0; the printed text is what is checked
        le.main(["--help"])
    text = " ".join(out.getvalue().split())
    for want in ("Inventory every `# pragma: no cover` in a file or tree, and what it claims.",
                 "file or directory", "machine-readable output",
                 "exit 1 if any branchy exemption states no reason"):
        check_true(f"--help says, as words: {want}",
                   re.search(rf"(?<![A-Za-z]){re.escape(want)}(?![A-Za-z])", text) is not None)
    check_true("  the description is the docstring's first line only", "WHY THIS IS SEPARATE" not in text)


def test_strict_passes_a_branchy_exemption_that_states_a_reason() -> None:
    with tempfile.TemporaryDirectory() as td:
        f = Path(td) / "r.py"
        f.write_text("def f(x):  # pragma: no cover reason: the socket half\n    if x:\n        return 1\n",
                     encoding="utf-8")
        rc, out, _ = _main(str(f), "--strict", "--json")
        check("a branchy exemption with a reason: --strict passes, none unjustified",
              (rc, json.loads(out)["unjustified"]), (0, 0))


if __name__ == "__main__":
    sys.exit(run(globals(), "list-exemptions"))
