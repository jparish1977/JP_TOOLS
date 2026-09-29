#!/usr/bin/env python3
"""
JP_TOOLS/tests/test_measure.py
jpcheck/measure.py: each language's functions, and its code outside every
function as "(top level)", measured by that language's standard tool.

WHAT IS CHECKED
    For each language, a sample in tests/fixtures/measure/ whose numbers were
    counted by hand: each function's lines, complexity and first line, and the
    top level's. A file with no code outside its functions has no top level.
    A nested function is counted into the file's total once, whether or not
    its language's tool also counts it into the parent. A file that does not
    parse is refused (ValueError), not measured as empty.

    A tool that is not installed prints SKIP for its language: not a pass.

    python3 tests/test_measure.py
"""

import shutil
import sys

from _harness import ROOT, check, run
from jpcheck.common import _php_bin, node_module_roots
from jpcheck.measure import TOP_LEVEL, file_complexity, measure

FIXTURES = ROOT / "tests" / "fixtures" / "measure"

# (language, fixture, {name: (lines, complexity, first line)}, file complexity).
# Python and PHP count a nested function into its parent (mccabe, phpcs), so
# the file total counts it once; ESLint does not, so JS's total adds it. In
# PHP an arrow function is no phpcs function: its ternary counts where it is.
CASES = [
    # K.m is 4: its own if, plus inner's 2, which mccabe counts into it.
    ("python", "sample.py", {"f": (4, 2, 11), "K.m": (8, 4, 18), "K.m.inner": (4, 2, 19),
                             TOP_LEVEL: (5, 3, 4)}, 9),
    ("php", "sample.php", {"top": (8, 2, 16), "K.m": (10, 3, 27), TOP_LEVEL: (7, 5, 7)}, 10),
    ("js", "sample.mjs", {"top": (6, 2, 8), "helper": (1, 2, 15), "K.m": (7, 3, 18),
                          "K.m.inner": (1, 2, 22), TOP_LEVEL: (7, 5, 1)}, 14),
    ("shell", "sample.sh", {"greet": (7, 3, 4), TOP_LEVEL: (12, 8, 2)}, 11),
    ("cpp", "sample.c", {"pick": (14, 7, 3), "main": (5, 1, 18)}, 8),
]

# The same languages with only functions: no top level.
NO_TOP = [
    ("python", "def f():\n    return 1\n"),
    ("php", "<?php\n\nfunction f(): int\n{\n    return 1;\n}\n"),
    ("js", "export function f() {\n  return 1;\n}\n"),
    ("shell", "f() {\n  echo 1\n}\n"),
]

BROKEN = [("python", "def f(:\n"), ("php", "<?php\nfunction (\n"), ("js", "function (\n"),
          ("shell", "if then fi fi\n")]

SKIPPED: set[str] = set()


def installed(lang: str) -> bool:
    """Whether the tool LANG's measure needs is installed."""
    return {"python": lambda: shutil.which("ruff") is not None,
            "php": lambda: bool(_php_bin("php-parse") and _php_bin("phpcs")),
            "js": lambda: bool(shutil.which("node") and any(
                (r / "eslint").is_dir() for r in node_module_roots())),
            "shell": lambda: shutil.which("shfmt") is not None,
            "cpp": lambda: shutil.which("lizard") is not None}[lang]()


def ready(lang: str) -> bool:
    if not installed(lang):
        SKIPPED.add(lang)
        return False
    return True


def test_each_language_measures_its_sample() -> None:
    for lang, fixture, want, total in CASES:
        if not ready(lang):
            continue
        src = (FIXTURES / fixture).read_text(encoding="utf-8")
        lines, funcs = measure(lang, src)
        check(f"{lang}: physical lines", lines, len(src.splitlines()))
        check(f"{lang}: each function and the top level", funcs, want)
        check(f"{lang}: the file total counts each function once", file_complexity(funcs, lang),
              total)


def test_no_code_outside_functions_is_no_top_level() -> None:
    for lang, src in NO_TOP:
        if ready(lang):
            check(f"{lang}: functions only, no top level",
                  TOP_LEVEL in measure(lang, src)[1], False)


def test_a_file_that_does_not_parse_is_refused() -> None:
    for lang, src in BROKEN:
        if not ready(lang):
            continue
        try:
            measure(lang, src)
            refused = False
        except (SyntaxError, ValueError):
            refused = True
        check(f"{lang}: a file that does not parse is refused, not measured empty", refused, True)


if __name__ == "__main__":
    rc = run(globals(), "measure")
    if SKIPPED:
        print(f"SKIP: not installed, not measured: {', '.join(sorted(SKIPPED))}")
    sys.exit(rc)
