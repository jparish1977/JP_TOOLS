#!/usr/bin/env python3
"""
JP_TOOLS/tests/test_diff_functions.py
diff-functions.py: finding a JS function by each definition form, ending it
where its braces balance, and the two views of the difference.

    python tests/test_diff_functions.py
"""

import sys
import tempfile
from pathlib import Path

from _harness import check, check_true, load_script, run
from fake_tools import run_main

JS = """\
const helper = 1;
function alpha(x) {
  if (x) {
    return 1;
  }
  return 2;
}
"beta"(x) {
  if (x) {
    return 1;
  }
  return 3;
}
const gamma = (x) => x;
obj = {
  "delta": function (y) {
    return y;
  },
};
"""


DIFF = load_script("diff-functions.py")


def _js() -> tempfile.TemporaryDirectory[str]:
    tmp = tempfile.TemporaryDirectory()
    (Path(tmp.name) / "a.js").write_text(JS)
    return tmp


def test_each_definition_form() -> None:
    with _js() as tmp:
        f = str(Path(tmp) / "a.js")
        lines, start = DIFF.extract_function(f, "alpha")
        check("function name(", (start, len(lines), lines[-1]), (2, 6, "}"))
        check("quoted method", DIFF.extract_function(f, "beta")[1], 8)
        check("arrow: one line, no braces", DIFF.extract_function(f, "gamma")[0],
              ["const gamma = (x) => x;"])
        check("name: function", DIFF.extract_function(f, "delta")[0][-1], "  },")


def test_missing_is_an_error() -> None:
    with _js() as tmp:
        f = str(Path(tmp) / "a.js")
        code, out = run_main(DIFF.main, f, "alpha", "nope")
        check("exit", code, 1)
        check_true("names it", "function 'nope' not found" in out)
        check("missing file", run_main(DIFF.main, str(Path(tmp) / "b.js:x"), f"{f}:alpha")[0], 1)


def test_side_by_side_marks_differences() -> None:
    with _js() as tmp:
        f = str(Path(tmp) / "a.js")
        code, out = run_main(DIFF.main, f, "alpha", "beta", "--width", "60")
        check("exit", code, 0)
        rows = out.splitlines()[3:-1]  # half = 60 // 2 - 2 = 28 columns, then the marker
        check("markers", [r[28] for r in rows], ["!", " ", " ", " ", "!", " "])


def test_unified_and_stats() -> None:
    with _js() as tmp:
        f = str(Path(tmp) / "a.js")
        code, out = run_main(DIFF.main, f"{f}:alpha", f"{f}:beta", "--unified", "--stats")
        check("exit", code, 0)
        check_true("a changed line", "-  return 2;" in out and "+  return 3;" in out)
        check_true("similarity", "Similarity: 66.7%" in out)


if __name__ == "__main__":
    sys.exit(run(globals(), "diff-functions"))
