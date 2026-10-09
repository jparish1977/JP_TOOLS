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

    The PHP tools run as `php PROXY`, so proxies without the executable bit
    (Windows cannot execute an extensionless script) measure the same. A
    measuring tool that cannot be run makes the smells check not run, never a
    warning about the file.

    A PHP string literal that decodes to bytes that are not UTF-8 ("\\x89PNG")
    measures like any other: the tree's JSON cannot fail on it.

    A tool that is not installed prints SKIP for its language: not a pass.

    python3 tests/test_measure.py
"""

import shutil
import sys
import tempfile
from pathlib import Path
from unittest import mock

from _harness import ROOT, check, run
from jpcheck import measure as measure_module
from jpcheck import smells as smells_module
from jpcheck.common import _php_bin, node_module_roots
from jpcheck.measure import TOP_LEVEL, file_complexity, measure
from jpcheck.smells import run_smells

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


def test_php_non_utf8_literal_is_measured() -> None:
    """A literal whose decoded bytes are not UTF-8 is measured, not refused as
    a parse error. The control, without the \\x89, measures the same."""
    if not ready("php"):
        return
    body = "<?php\n\nfunction f(): string\n{\n    return \"{}PNG\";\n}\n"
    for label, escape in (("control, plain literal", ""), ("non-UTF-8 literal", "\\x89")):
        src = body.replace("{}", escape)
        try:
            got: object = measure("php", src)[1]
        except (ValueError, measure_module.ToolMissingError) as e:
            got = f"not measured: {type(e).__name__}: {e}"
        check(f"php: {label} is measured", got, {"f": (4, 1, 3)})


def test_php_runner_failure_is_not_a_parse_error() -> None:
    """php-parser that cannot be loaded makes the measure's tool missing, not a
    'could not be parsed' about the file."""
    if not ready("php"):
        return
    with tempfile.TemporaryDirectory() as tmp:
        empty = Path(tmp) / "autoload.php"
        empty.write_text("<?php\n", encoding="utf-8")
        with mock.patch.object(measure_module, "_php_autoload", return_value=empty):
            try:
                measure("php", "<?php\n\nfunction f(): int\n{\n    return 1;\n}\n")
                got = "measured"
            except measure_module.ToolMissingError:
                got = "tool missing"
            except ValueError:
                got = "refused as a parse error"
    check("php: a runner that cannot load php-parser is a missing tool", got, "tool missing")


def wrapper_proxies(tmp: Path) -> dict[str, str]:
    """A phpcs proxy that is a PHP script WITHOUT the executable bit, requiring
    the real one: what Windows sees in vendor/bin, where an extensionless
    script cannot be executed directly (WinError 193). The tree comes from
    jp_php_ast.php, which has no executable bit either."""
    out = {}
    for name in ("phpcs",):
        w = tmp / name
        w.write_text(f"<?php\nrequire {str(_php_bin(name))!r};\n", encoding="utf-8")
        w.chmod(0o644)
        out[name] = str(w)
    return out


def test_php_tools_run_through_php() -> None:
    """The PHP measure runs its tools as `php PROXY`, so a proxy that cannot be
    executed on its own measures the same."""
    if not ready("php"):
        return
    lang, fixture, want, _ = CASES[1]
    src = (FIXTURES / fixture).read_text(encoding="utf-8")
    with tempfile.TemporaryDirectory() as tmp, \
            mock.patch.object(measure_module, "_php_bin", wrapper_proxies(Path(tmp)).get):
        try:
            got: object = measure(lang, src)[1]
        except OSError as e:
            got = f"{type(e).__name__}: {e}"
    check("php: proxies that cannot be executed directly still measure the sample, through php",
          got, want)


def test_a_tool_that_cannot_run_is_not_a_finding() -> None:
    """A measuring tool that cannot be run makes the smells check not run
    (status error, exit 2), never a 'could not be parsed' warning that exits 0."""
    with tempfile.TemporaryDirectory() as tmp:
        f = Path(tmp) / "a.php"
        f.write_text("<?php\n\nfunction f(): int\n{\n    return 1;\n}\n", encoding="utf-8")
        cannot = OSError(193, "%1 is not a valid Win32 application")
        with mock.patch.object(smells_module, "measure", side_effect=cannot):
            result = run_smells(str(f))
    check("a measuring tool that cannot run: smells did not run, and says why",
          (result["status"], result["issues"], "could not be run" in result.get("note", "")),
          ("error", [], True))


if __name__ == "__main__":
    rc = run(globals(), "measure")
    if SKIPPED:
        print(f"SKIP: not installed, not measured: {', '.join(sorted(SKIPPED))}")
    sys.exit(rc)
