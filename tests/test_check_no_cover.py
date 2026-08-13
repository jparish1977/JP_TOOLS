#!/usr/bin/env python
"""
JP_TOOLS/tests/test_check_no_cover.py
Tests for check.py's `no-cover` check.

No pytest, matching the rest of tests/. Every case is a small source file
written to a temp directory and run through the real check, so this exercises
the parsing and the rule together rather than a reimplementation of either.

WHAT THIS PINS DOWN
The check exists because `# pragma: no cover` is a claim that a function has
nothing worth testing, and nothing rechecks that claim as the function grows.
On spool-audit.py the exempt region reached 430 lines of 1350 holding 51
branches, and every serious defect on that branch came from inside it. See
docs/coverage.md.

The rule: a pragma on a branchy function must state a reason. A pragma on a
branchless one need not, because it is self-evidently a wrapper.
"""

import importlib.util
import pathlib
import sys
import tempfile

MODULE_PATH = pathlib.Path(__file__).resolve().parent.parent / "check.py"

_cache = pathlib.Path(importlib.util.cache_from_source(str(MODULE_PATH)))
if _cache.exists():
    _cache.unlink()
importlib.invalidate_caches()

_spec = importlib.util.spec_from_file_location("check_mod", MODULE_PATH)
assert _spec is not None and _spec.loader is not None
check_mod = importlib.util.module_from_spec(_spec)
sys.modules["check_mod"] = check_mod
_spec.loader.exec_module(check_mod)

FAILURES: list[str] = []


def check(label: str, got: object, want: object) -> None:
    if got != want:
        FAILURES.append(f"{label}: got {got!r}, want {want!r}")


def rules_for(source: str) -> list[str]:
    """Run the real check over `source` and return the rules it fired."""
    with tempfile.TemporaryDirectory() as tmp:
        f = pathlib.Path(tmp) / "sample.py"
        f.write_text(source)
        result = check_mod.run_no_cover(str(f))
        return [i["rule"] for i in result["issues"]]


def test_a_branchless_wrapper_needs_no_reason() -> None:
    check("a one-line wrapper is accepted", rules_for(
        "def w(p):  # pragma: no cover\n    return p.unlink()\n"
    ), [])


def test_a_branchy_exemption_is_flagged() -> None:
    for label, src in [
        ("an if", "def f(p):  # pragma: no cover\n    if p:\n        return 1\n    return 2\n"),
        ("a try", "def f(p):  # pragma: no cover\n    try:\n        return p()\n    except OSError:\n        return None\n"),
        ("a loop", "def f(p):  # pragma: no cover\n    for x in p:\n        print(x)\n"),
        ("a while", "def f(p):  # pragma: no cover\n    while p:\n        p = p - 1\n"),
    ]:
        check(f"{label} in an exempt function is flagged",
              rules_for(src), ["no-cover-branchy"])


def test_a_stated_reason_is_accepted() -> None:
    check("a reason silences it", rules_for(
        "def f(p):  # pragma: no cover -- reason: runs systemctl on a live host\n"
        "    if p:\n        return 1\n    return 2\n"
    ), [])
    # An empty reason is not a reason. Accepting it would make the escape hatch
    # free, which is how the original pragmas became meaningless.
    check("an empty reason is not accepted", rules_for(
        "def f(p):  # pragma: no cover -- reason:\n"
        "    if p:\n        return 1\n    return 2\n"
    ), ["no-cover-branchy"])


def test_the_pragma_is_found_across_a_multi_line_signature() -> None:
    """Both forms are in use in this repo, and a check that saw only one would
    silently pass the other, which is this repo's most repeated defect."""
    check("pragma on the opening line of a wrapped signature", rules_for(
        "def f(  # pragma: no cover\n    a,\n    b,\n):\n    if a:\n        return b\n    return a\n"
    ), ["no-cover-branchy"])
    check("pragma on the closing line of a wrapped signature", rules_for(
        "def f(\n    a,\n    b,\n):  # pragma: no cover\n    if a:\n        return b\n    return a\n"
    ), ["no-cover-branchy"])


def test_an_unexempt_function_is_never_flagged() -> None:
    check("no pragma, no finding", rules_for(
        "def f(p):\n    if p:\n        return 1\n    return 2\n"
    ), [])


def test_a_pragma_outside_a_function_is_still_seen() -> None:
    """The first version walked FunctionDef only, so a pragma on a class, on a
    bare statement, or at module level was invisible to the gate AND to the
    inventory, which then printed "No coverage exemptions" as though it had
    looked. A checker that silently cannot see a construct commits the failure
    it was written to catch."""
    check("a class", rules_for(
        "class C:  # pragma: no cover\n    def m(self, p):\n"
        "        if p:\n            return 1\n        return 2\n"
    ), ["no-cover-branchy"])
    check("a bare if inside a function", rules_for(
        "def f(p):\n    if p:  # pragma: no cover\n        return 1\n    return 2\n"
    ), ["no-cover-branchy"])
    check("a module-level branch", rules_for(
        "import sys\nif sys.platform == 'win32':  # pragma: no cover\n    X = 1\n"
    ), ["no-cover-branchy"])


def test_prose_about_a_pragma_is_not_a_pragma() -> None:
    """This repo documents the marker in several files, including inside the
    function that implements the check. A line scan flagged that function using
    its own rule, which is a false positive that would train people to ignore
    it."""
    check("a standalone comment line is documentation, not a directive", rules_for(
        "# Explaining the rule:\n"
        "#     def f(): ...  # pragma: no cover\n"
        "def f(p):\n    if p:\n        return 1\n    return 2\n"
    ), [])
    # INSIDE a branchy function, which is where check.py's own explanatory
    # comment sits. The first version of this test put the prose at module
    # level, where it belongs to no branchy owner, so removing the
    # comment-versus-code guard changed nothing and the mutation survived.
    check("prose inside a branchy function is still not a directive", rules_for(
        "def f(p):\n"
        "    # The rule: a wrapper may carry # pragma: no cover\n"
        "    if p:\n        return 1\n    return 2\n"
    ), [])
    check("and neither is a docstring mentioning it", rules_for(
        'def f(p):\n    """Uses # pragma: no cover on IO leaves."""\n'
        "    if p:\n        return 1\n    return 2\n"
    ), [])
    check("but a real trailing pragma on the same code is still caught", rules_for(
        "# Explaining the rule:\n"
        "#     def f(): ...  # pragma: no cover\n"
        "def f(p):  # pragma: no cover\n    if p:\n        return 1\n    return 2\n"
    ), ["no-cover-branchy"])


def test_unparseable_source_is_left_to_ruff() -> None:
    """Reporting a syntax error here too would make one problem look like two,
    and ruff already reports it with a better message."""
    check("a syntax error yields no findings from this check",
          rules_for("def f(  :::\n"), [])


def main() -> int:
    for name, fn in sorted(globals().items()):
        if name.startswith("test_") and callable(fn):
            fn()
    if FAILURES:
        print(f"FAILED ({len(FAILURES)})")
        for f in FAILURES:
            print(f"  {f}")
        return 1
    print("all check.py no-cover tests passed")
    return 0


if __name__ == "__main__":
    sys.exit(main())
