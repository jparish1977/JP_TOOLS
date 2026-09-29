#!/usr/bin/env python3
"""
JP_TOOLS/tests/test_check_directive_reason.py
A suppression directive a linter honours fails unless its line says why.

WHY
    shellcheck, cppcheck, phpstan, phpcs, eslint and stylelint each honour a
    comment that turns a finding off, and none asks for a reason. Measured
    before this check: a C out-of-bounds write that fails the gate (exit 1)
    passed with `// cppcheck-suppress arrayIndexOutOfBounds` above it (exit 0);
    a PHP type error the same with `// @phpstan-ignore-next-line`; a
    `# shellcheck disable=SC2086` with no reason passed. Python alone required
    the reason (noqa-no-reason).

WHAT IS CHECKED
    Per language: the directive with no reason is found and unreasoned; with a
    reason in each form its tool accepts, reasoned. A string that merely holds
    the words is not a directive. End to end: `check.py --tools
    directive-reason` exits 1 on a reasonless directive and 0 with a reason,
    and `--lang shell` and `--lang cpp` are accepted. Needs no linter.

    python3 tests/test_check_directive_reason.py
"""

import json
import subprocess
import sys
import tempfile
from pathlib import Path

from _harness import ROOT, check, run
from jpcheck.directives import directives

CHECK_PY = ROOT / "check.py"

# (language, directive line with no reason, the same line with each reason form)
CASES = [
    ("shell", "# shellcheck disable=SC2086",
     ["# shellcheck disable=SC2086  # the words are split on purpose",
      "# shellcheck disable=SC2317,SC2329 -- invoked by the trap"]),
    ("cpp", "  // cppcheck-suppress arrayIndexOutOfBounds",
     ["  // cppcheck-suppress arrayIndexOutOfBounds ; written past on purpose",
      "  // cppcheck-suppress arrayIndexOutOfBounds -- written past on purpose",
      "  // cppcheck-suppress arrayIndexOutOfBounds // written past on purpose"]),
    ("php", "    // @phpstan-ignore-next-line",
     ["    // @phpstan-ignore return.type (returns a string on purpose)",
      "    // @phpstan-ignore-next-line -- returns a string on purpose"]),
    ("php", "    // phpcs:ignore Generic.Files.LineLength",
     ["    // phpcs:ignore Generic.Files.LineLength -- a URL that cannot wrap"]),
    ("js", "// eslint-disable-next-line no-eval",
     ["// eslint-disable-next-line no-eval -- evaluates a user formula on purpose"]),
    ("js", "/* eslint-disable */",
     ["/* eslint-disable -- generated file, not edited by hand */"]),
    ("css", "/* stylelint-disable-next-line color-no-hex */",
     ["/* stylelint-disable-next-line color-no-hex -- the brand colour */"]),
]


def one(line: str, lang: str) -> list[dict[str, object]]:
    return directives(line + "\n", lang)


def test_each_directive_needs_a_reason() -> None:
    for lang, bare, reasoned in CASES:
        found = one(bare, lang)
        check(f"{lang}: {bare.strip()!r} is found, unreasoned",
              [d["reasoned"] for d in found], [False])
        for line in reasoned:
            check(f"{lang}: {line.strip()!r} is found, reasoned",
                  [d["reasoned"] for d in one(line, lang)], [True])


def test_a_string_is_not_a_directive() -> None:
    check("shell: the words inside a string are not a directive",
          one('echo "see shellcheck disable=SC2086 in the docs"', "shell"), [])
    check("js: the words inside a string are not a directive",
          one('const s = "eslint-disable-next-line no-eval";', "js"), [])
    check("control: the same directive in a comment is found",
          len(one("x=1  # shellcheck disable=SC2086", "shell")), 1)


def test_a_separator_with_no_prose_is_no_reason() -> None:
    check("js: '--' and nothing after it is not a reason",
          [d["reasoned"] for d in one("// eslint-disable-line no-eval --", "js")], [False])
    check("shell: an empty second comment is not a reason",
          [d["reasoned"] for d in one("# shellcheck disable=SC2086  #", "shell")], [False])


def gate(path: Path, *extra: str) -> tuple[int, str, list[str]]:
    """check.py's exit code, the check's own status, and the rules it reported."""
    r = subprocess.run([sys.executable, str(CHECK_PY), str(path), "--tools", "directive-reason",
                        *extra], capture_output=True, text=True, check=False)
    try:
        doc = json.loads(r.stdout)
    except ValueError:
        # reason: not a check.py report: no rules, which fails the checks that expect one
        return r.returncode, "", []
    status = next((str(c["status"]) for c in doc["checks"]), "")
    return r.returncode, status, [str(i["rule"]) for c in doc["checks"] for i in c["issues"]]


def test_the_gate_fails_a_reasonless_directive() -> None:
    with tempfile.TemporaryDirectory() as td:
        d = Path(td)
        (d / "bare.sh").write_text("#!/bin/sh\n# shellcheck disable=SC2086\necho $1\n", encoding="utf-8")
        (d / "why.sh").write_text("#!/bin/sh\n# shellcheck disable=SC2086  # split on purpose\n"
                                  "echo $1\n", encoding="utf-8")
        (d / "bare.c").write_text("int main(void) {\n  int a[2];\n"
                                  "  // cppcheck-suppress arrayIndexOutOfBounds\n"
                                  "  a[2] = 0;\n  return a[0];\n}\n", encoding="utf-8")
        check("a reasonless shellcheck directive fails the gate",
              gate(d / "bare.sh"), (1, "fail", ["directive-no-reason"]))
        check("the same with a reason passes", gate(d / "why.sh"), (0, "pass", []))
        check("a reasonless cppcheck-suppress fails the gate",
              gate(d / "bare.c"), (1, "fail", ["directive-no-reason"]))
        check("--lang shell is accepted", gate(d / "why.sh", "--lang", "shell")[0], 0)
        check("--lang cpp is accepted", gate(d / "bare.c", "--lang", "cpp")[0], 1)


if __name__ == "__main__":
    sys.exit(run(globals(), "directive-reason"))
