#!/usr/bin/env python3
"""
JP_TOOLS/tests/test_measure.py
jpcheck/measure.py: each language's functions, and its code outside every
function as "(top level)", measured by that language's standard tool.

WHAT IS CHECKED
    For each language, a sample whose numbers were counted by hand: each
    function's lines, complexity and first line, and the top level's. A file
    with no code outside its functions has no top level. A nested function is
    counted into the file's total once, whether or not its language's tool
    also counts it into the parent. A file that does not parse is refused
    (ValueError), not measured as empty.

    A tool that is not installed prints SKIP for its language: not a pass.

    python3 tests/test_measure.py
"""

import shutil
import sys

from _harness import check, run
from jpcheck.common import _php_bin, node_module_roots
from jpcheck.measure import TOP_LEVEL, file_complexity, measure

PYTHON = '''"""A docstring is not top-level code."""
import os

x = os.environ.get("X")
if x and x > "a":
    print(1)
for i in range(2):
    pass


def f(a):
    if a:
        return 1
    return 2


class K:
    def m(self, b):
        def inner(c):
            if c:
                return 1
            return 0
        if b:
            return inner(b)
        return 0
'''

PHP = """<?php

declare(strict_types=1);

namespace Demo;

use Foo\\Bar;

$x = $argv[1] ?? 'a';
if ($x === 'a') {
    echo 1;
} elseif ($x === 'b') {
    echo 2;
}
$f = static function (int $n): int {
    return $n > 1 ? 1 : 2;
};

function top(int $a): int
{
    if ($a) {
        return 1;
    }
    return 2;
}

final class K
{
    public function m(int $b): int
    {
        foreach ([1, 2] as $i) {
            if ($i === $b) {
                return $i;
            }
        }
        return 0;
    }
}
"""

JS = """import { readFileSync } from "fs";

const arg = process.argv[2] ?? "a";
if (arg === "a" || arg === "b") {
  console.log(1);
} else if (arg === "c") {
  console.log(2);
}

export function top(a) {
  if (a) {
    return 1;
  }
  return 2;
}

const helper = (x) => (x > 1 ? 1 : 2);

class K {
  m(b) {
    for (const i of [1, 2]) {
      if (i === b) return i;
    }
    const inner = () => (b ? 1 : 0);
    return inner();
  }
}

export default new K();
"""

SHELL = """#!/usr/bin/env bash
set -u

greet() {
  if [ "$1" = a ]; then
    echo a
  elif [ "$1" = b ]; then
    echo b
  fi
}

x=${1:-a}
if [ "$x" = a ] && [ -n "$x" ] || [ "$x" = c ]; then
  greet "$x"
fi
for i in 1 2; do
  case "$i" in
    1) echo one ;;
    2) echo two ;;
  esac
done
while false; do :; done
"""

C = """#include <stdio.h>

static int pick(int a, int b)
{
    if (a > 1 && b > 1) {
        return 1;
    }
    for (int i = 0; i < a; i++) {
        switch (i) {
        case 1: return 2;
        case 2: return 3;
        default: break;
        }
    }
    return a ? b : 0;
}

int main(void)
{
    printf("%d\\n", pick(1, 2));
    return 0;
}
"""

# (language, source, {name: (lines, complexity, first line)}, file complexity).
# Python and PHP count a nested function into its parent (mccabe, phpcs); the
# file total counts it once. ESLint does not, so JS's total adds it.
CASES = [
    # K.m is 4: its own if, plus inner's 2, which mccabe counts into it.
    ("python", PYTHON, {"f": (4, 2, 11), "K.m": (8, 4, 18), "K.m.inner": (4, 2, 19),
                        TOP_LEVEL: (5, 3, 4)}, 9),
    ("php", PHP, {"top": (7, 2, 19), "K.m": (9, 3, 29), TOP_LEVEL: (9, 5, 9)}, 10),
    ("js", JS, {"top": (6, 2, 10), "helper": (1, 2, 17), "K.m": (7, 3, 20),
                "K.m.inner": (1, 2, 24), TOP_LEVEL: (7, 5, 3)}, 14),
    ("shell", SHELL, {"greet": (7, 3, 4), TOP_LEVEL: (12, 8, 2)}, 11),
    ("cpp", C, {"pick": (14, 7, 3), "main": (5, 1, 18)}, 8),
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


def missing(lang: str) -> str | None:
    """The tool LANG's measure needs, when it is not installed."""
    need = {"python": shutil.which("ruff") is not None,
            "php": bool(_php_bin("php-parse") and _php_bin("phpcs")),
            "js": bool(shutil.which("node") and any((r / "eslint").is_dir()
                                                   for r in node_module_roots())),
            "shell": shutil.which("shfmt") is not None,
            "cpp": shutil.which("lizard") is not None}
    return None if need[lang] else lang


SKIPPED: set[str] = set()


def ready(lang: str) -> bool:
    if missing(lang):
        SKIPPED.add(lang)
        return False
    return True


def test_each_language_measures_its_sample() -> None:
    for lang, src, want, total in CASES:
        if not ready(lang):
            continue
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
