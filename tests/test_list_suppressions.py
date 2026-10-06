#!/usr/bin/env python3
"""
JP_TOOLS/tests/test_list_suppressions.py
list-suppressions.py: every suppression in a tree, in every language check.py
detects, listed with what it turns off and its stated reason, the ones
check.py lets pass included.

WHAT IS CHECKED
    - Python: a noqa with a reason, one without, a blanket one with and
      without a reason, and a `# type: ignore[...]`, each with its file, line,
      codes and reason. A noqa code is listed upper-case (ruff reads any
      case); a type: ignore code as written (mypy reads only lower-case).
    - shell, C, PHP, JS, CSS: each language's directive, as written, with
      its reason; a reason in a form its tool does not honour shows as none.
    - Not listed: a marker inside a string, a sentence ABOUT noqa, a
      file-level `# ruff: noqa`, a file of no detected language, and anything
      under a skipped directory.
    - --strict exits 1, and counts exactly the suppressions check.py's own
      rules (noqa, directive-reason) refuse in the same tree.
    - The report counts by kind and marks a missing reason; a path that does
      not exist exits 2.

    python3 tests/test_list_suppressions.py
"""

import contextlib
import io
import json
import subprocess
import sys
import tempfile
from pathlib import Path

from _harness import ROOT, check, check_true, load_script, run
from jpexit import FINDINGS, NOT_RUN, OK

LIST = load_script("list-suppressions.py")

FILES = {
    "a.py": ("import os\nimport sys\nsys.path.insert(0, '.')\n"
             "import json  # noqa: E402 -- after the sys.path insert above\n"
             "import re  # noqa: e402\n"
             "x = os.sep  # noqa\n"
             "y: int = json.loads('1')  # type: ignore[no-any-return]\n"
             "z = 1  # noqa -- a reason, but no codes\n"
             "print(re, x, y, z)\n"),
    "b.py": ("# A sentence about noqa: a `# noqa` needs a reason.\n"
             "TEXT = 'z = 1  # noqa: F401'\n"
             "print(TEXT)\n"),
    "c.py": "# ruff: noqa\nimport os\n",
    "s.sh": "#!/bin/sh\n# shellcheck disable=SC2086  # split on purpose\necho $1\n",
    "c.c": "int main(void) {\n  // cppcheck-suppress unusedVariable\n  int x;\n  return 0;\n}\n",
    "p.php": ("<?php\n// phpcs:ignore Generic.PHP.NoSilencedErrors -- may fail on purpose\n$a = @f();\n"
              "// phpcs:ignore Generic.PHP.NoSilencedErrors reason: dropped by phpcs\n$b = @f();\n"),
    "j.js": "// eslint-disable-next-line no-eval -- a user formula\neval('1');\n",
    "s.css": "/* stylelint-disable-next-line color-no-hex */\na { color: #fff; }\n",
    "notes.txt": "# noqa: E402 -- not code\n",
    "vendor/v.py": "import os  # noqa: F401\n",
}

WANT = [
    ("a.py", 4, "noqa", ["E402"], "after the sys.path insert above"),
    ("a.py", 5, "noqa", ["E402"], ""),
    ("a.py", 6, "noqa", [], ""),
    ("a.py", 7, "type-ignore", ["no-any-return"], ""),
    ("a.py", 8, "noqa", [], "a reason, but no codes"),
    ("c.c", 2, "cppcheck-suppress", ["cppcheck-suppress unusedVariable"], ""),
    ("j.js", 1, "eslint-disable", ["eslint-disable-next-line no-eval"], "a user formula"),
    ("p.php", 2, "phpcs", ["phpcs:ignore Generic.PHP.NoSilencedErrors"], "may fail on purpose"),
    ("p.php", 4, "phpcs", ["phpcs:ignore Generic.PHP.NoSilencedErrors reason: dropped by phpcs"], ""),
    ("s.css", 1, "stylelint-disable", ["stylelint-disable-next-line color-no-hex"], ""),
    ("s.sh", 2, "shellcheck disable", ["shellcheck disable=SC2086"], "split on purpose"),
]


def tree(tmp: Path) -> Path:
    for name, body in FILES.items():
        (tmp / name).parent.mkdir(parents=True, exist_ok=True)
        (tmp / name).write_text(body, encoding="utf-8")
    return tmp


def listed(*argv: str) -> tuple[int, str]:
    out = io.StringIO()
    with contextlib.redirect_stdout(out), contextlib.redirect_stderr(out):
        code = LIST.main(list(argv))
    return code, out.getvalue()


def test_every_suppression_and_nothing_else() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        code, out = listed(str(tree(Path(tmp))), "--json")
    doc = json.loads(out)
    rows = sorted((r["file"], r["line"], r["kind"], r["codes"], r["reason"]) for r in doc["suppressions"])
    check("each suppression, every language, with what it names and its reason", rows, WANT)
    check("--json: exit, and the files scanned (8 code files; notes.txt and vendor/ not)",
          (code, doc["files_scanned"]), (OK, 8))


def refusals(t: Path) -> int:
    """How many suppressions check.py's own rules refuse across the tree."""
    n = 0
    for name in FILES:
        if name.endswith(".txt") or name.startswith("vendor/"):
            continue
        r = subprocess.run([sys.executable, str(ROOT / "check.py"), str(t / name),
                            "--tools", "noqa,directive-reason"],
                           capture_output=True, text=True, check=False)
        n += sum(1 for c in json.loads(r.stdout).get("checks", []) for i in c["issues"]
                 if i["rule"] in ("noqa-no-reason", "noqa-blanket", "directive-no-reason"))
    return n


def test_strict_agrees_with_check_py() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        t = tree(Path(tmp))
        code, out = listed(str(t), "--strict", "--json")
        gate = refusals(t)
    check("--strict with refused suppressions: exit", code, FINDINGS)
    check("--strict counts exactly what check.py refuses", json.loads(out)["refused"], gate)
    check_true("control: check.py refuses some here", gate > 0)


def test_the_report() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        code, out = listed(str(tree(Path(tmp))))
    check("the report: exit", code, OK)
    check_true("the report: the total and files", "11 in 6 of 8 file(s) scanned" in out)
    check_true("the report: counts by kind", "noqa 4" in out and "phpcs 2" in out)
    check_true("the report: noqa counted by code", "noqa by code: E402 2" in out)
    check_true("the report: a missing reason is marked", "*** NO REASON GIVEN ***" in out)


def test_nothing_to_list_and_no_such_path() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        (Path(tmp) / "clean.py").write_text("x = 1\n", encoding="utf-8")
        code, out = listed(tmp)
        check("a clean tree: exit, and it says so", (code, "No suppressions" in out), (OK, True))
        check("a path that does not exist: exit", listed(str(Path(tmp) / "gone"))[0], NOT_RUN)


if __name__ == "__main__":
    sys.exit(run(globals(), "list-suppressions"))
