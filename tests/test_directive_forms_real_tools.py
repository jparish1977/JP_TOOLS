#!/usr/bin/env python3
"""
JP_TOOLS/tests/test_directive_forms_real_tools.py
Every reason form directive-reason accepts is one its own tool honours.

WHY
    A form a tool does not honour is worse than no reason: after a shellcheck
    directive `-- why` is a parse error, and `reason: why` after an eslint,
    stylelint or phpcs directive makes the tool drop the suppression while the
    line reads as explained. directive-reason once accepted both, from a claim
    that they had been measured when they had not. So each accepted form is
    run through the real tool here, next to a control without the directive
    that the tool must report.

A tool that is not installed prints SKIP for its cases; that is not a pass.
Found on the tool's own PATH or in this checkout's vendor/bin and
node_modules/.bin, as check.py finds them.

    python3 tests/test_directive_forms_real_tools.py
"""

import shutil
import subprocess
import sys
import tempfile
from collections.abc import Callable
from pathlib import Path

from _harness import ROOT, check, run
from jpcheck.directives import directives

SKIPPED: list[str] = []


def tool(name: str) -> str | None:
    for d in (ROOT / "vendor" / "bin", ROOT / "node_modules" / ".bin"):
        if (d / name).exists():
            return str(d / name)
    return shutil.which(name)


def reports(argv: list[str], d: Path) -> bool:
    """Whether the tool, run in D, exits non-zero: it reported the finding."""
    return subprocess.run(argv, capture_output=True, text=True, check=False, cwd=d).returncode != 0


Argv = Callable[[str, Path], list[str]]


def _shellcheck(exe: str, _d: Path) -> list[str]:
    return [exe, "-f", "gcc", "t.sh"]


def _cppcheck(exe: str, _d: Path) -> list[str]:
    return [exe, "--inline-suppr", "--error-exitcode=1", "--quiet", "t.c"]


def _phpstan(exe: str, _d: Path) -> list[str]:
    return [exe, "analyse", "--no-progress", "--level", "8", "--error-format", "raw", "t.php"]


def _phpcs(exe: str, _d: Path) -> list[str]:
    return [exe, "--standard=Generic", "--sniffs=Generic.PHP.NoSilencedErrors", "--report=emacs",
            "t.php"]


def _eslint(exe: str, _d: Path) -> list[str]:
    return [exe, "--no-config-lookup", "--rule", '{"no-eval": "error"}', "t.js"]


def _stylelint(exe: str, d: Path) -> list[str]:
    (d / "sl.json").write_text('{"rules": {"color-no-hex": true}}', encoding="utf-8")
    return [exe, "--config", "sl.json", "t.css"]


C_OOB = "int main(void) {\n  int a[2];\n{D}\n  a[2] = 0;\n  return a[0];\n}\n"
PHP_TYPE = '<?php\n\ndeclare(strict_types=1);\n\nfunction f(): int\n{\n{D}\n    return "x";\n}\n'
PHP_AT = '<?php\n\ndeclare(strict_types=1);\n\n{D}\n$a = @file_get_contents("x");\n'

# (tool, argv, lang, file name, source with {D} where the directive goes,
#  the directive in each accepted reason form)
CASES: list[tuple[str, Argv, str, str, str, list[str]]] = [
    ("shellcheck", _shellcheck, "shell", "t.sh", "#!/bin/bash\n{D}\necho $1\n",
     ["# shellcheck disable=SC2086  # the words are split on purpose"]),
    ("cppcheck", _cppcheck, "cpp", "t.c", C_OOB,
     ["  // cppcheck-suppress arrayIndexOutOfBounds ; written past on purpose",
      "  // cppcheck-suppress arrayIndexOutOfBounds // written past on purpose",
      "  // cppcheck-suppress arrayIndexOutOfBounds -- written past on purpose"]),
    ("phpstan", _phpstan, "php", "t.php", PHP_TYPE,
     ["    // @phpstan-ignore-next-line -- returns a string on purpose",
      "    // @phpstan-ignore return.type (returns a string on purpose)"]),
    ("phpcs", _phpcs, "php", "t.php", PHP_AT,
     ["// phpcs:ignore Generic.PHP.NoSilencedErrors.Discouraged -- the read may fail"]),
    ("eslint", _eslint, "js", "t.js", '{D}\neval("1");\n',
     ["// eslint-disable-next-line no-eval -- evaluates a user formula on purpose"]),
    ("stylelint", _stylelint, "css", "t.css", "a {\n  {D}\n  color: #fff;\n}\n",
     ["/* stylelint-disable-next-line color-no-hex -- the brand colour */"]),
]


def _one_case(name: str, argv: Argv, lang: str, fname: str, source: str,
              forms: list[str]) -> None:
    exe = tool(name)
    if exe is None:
        SKIPPED.append(name)
        return
    with tempfile.TemporaryDirectory() as td:
        d = Path(td)
        (d / fname).write_text(source.replace("{D}", ""), encoding="utf-8")
        check(f"control: {name} reports the finding with no directive",
              reports(argv(exe, d), d), True)
        for form in forms:
            (d / fname).write_text(source.replace("{D}", form), encoding="utf-8")
            check(f"{name} honours {form.strip()!r}", reports(argv(exe, d), d), False)
            check(f"directive-reason accepts {form.strip()!r}",
                  [x["reasoned"] for x in directives(form + "\n", lang)], [True])


def test_every_accepted_form_is_honoured_by_its_tool() -> None:
    for case in CASES:
        _one_case(*case)


if __name__ == "__main__":
    rc = run(globals(), "directive forms against the real tools")
    if SKIPPED:
        print(f"SKIP: not installed, not checked: {', '.join(SKIPPED)}")
    sys.exit(rc)
