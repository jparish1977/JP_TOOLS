#!/usr/bin/env python
"""
JP_TOOLS/tests/test_check_noqa.py
Tests for check.py's `noqa` check: the three things that fail, and the silence
about everything else.

No pytest, matching the rest of tests/. Every case is a small source file
written to a temp directory and run through the real check.

WHAT THIS PINS DOWN
A `# pragma: no cover` has to say why, and a `# noqa` is held to the same bar:
no suppression without a real reason.

The rule, in three parts, because they are not one thing:
  * a marker must state a reason, since for a structural suppression the
    sentence is the evidence
  * a BLANKET `# noqa` with no codes, which turns off rules nobody has written
    yet and names none of them
  * `BLE001` and `S110` over a handler that SWALLOWS are not available under
    any reason, because the evidence there is the exception recorded

And the case the check exists for: without it, a `# noqa` takes a blind
swallow from 2 errors to 0, suppress-reason included, because that check
defers the shape to ruff and a noqa is how you stop ruff reporting it.
"""

import pathlib
import sys
import tempfile
from typing import Any

import jpcheck_loader

check_mod = jpcheck_loader.load("suppressions")

FAILURES: list[str] = []


def check(label: str, got: object, want: object) -> None:
    if got != want:
        FAILURES.append(f"{label}: got {got!r}, want {want!r}")


def _run(source: str) -> list[dict[str, Any]]:
    with tempfile.TemporaryDirectory() as tmp:
        f = pathlib.Path(tmp) / "sample.py"
        f.write_text(source)
        issues: list[dict[str, Any]] = check_mod.run_noqa(str(f))["issues"]
        return issues


def errors_for(source: str) -> list[str]:
    """The rules the noqa check fails on."""
    return [i["rule"] for i in _run(source) if i["severity"] == "error"]


def quiet_about(source: str) -> list[str]:
    """Everything the check emits, of any severity.

    THIS CHECK REFUSES AND DOES NOT LIST, so an accepted marker produces
    NOTHING, not a warning. Pinned as its own idea rather than folded into
    errors_for: "accepted" and "accepted and announced" are different.
    """
    return [i["rule"] for i in _run(source)]


def suppress_rules_for(source: str) -> list[str]:
    with tempfile.TemporaryDirectory() as tmp:
        f = pathlib.Path(tmp) / "sample.py"
        f.write_text(source)
        return [i["rule"] for i in check_mod.run_suppress_reason(str(f))["issues"]]


# A structural suppression whose evidence is a sentence.
E402 = "import os\nsys.path.insert(0, '/x')\nimport dt  # noqa: E402 -- found at run time above\n"


def test_a_reasoned_marker_passes_silently() -> None:
    check("a reasoned E402 is not an error", errors_for(E402), [])
    check("and it is not announced either", quiet_about(E402), [])


def test_a_marker_with_no_reason_is_an_error() -> None:
    check("no reason at all", errors_for("import dt  # noqa: E402\n"),
          ["noqa-no-reason"])
    check("a dangling dash is not a reason",
          errors_for("import dt  # noqa: E402 --\n"), ["noqa-no-reason"])
    check("`reason:` spelled out is accepted, as no-cover spells it",
          errors_for("import dt  # noqa: E402  # reason: found at run time\n"), [])


def test_a_blanket_noqa_is_an_error_even_with_a_reason() -> None:
    check("bare noqa, reasoned", errors_for("import dt  # noqa -- it is fine\n"),
          ["noqa-blanket"])
    check("and the message says what it is that makes it one",
          ["suppresses every rule on this line" in i["message"]
           for i in _run("import dt  # noqa -- it is fine\n")], [True])


SWALLOW = ("def f():\n    try:\n        g()\n        g()\n"
           "    except Exception:  # noqa: BLE001,S110 -- {why}\n        pass\n")
RECORDS = ("def f():\n    try:\n        return g()\n"
           "    except Exception as e:  # noqa: BLE001 -- a crash is a failed check\n"
           "        return f'{type(e).__name__}: {e}'\n")


def test_ble001_over_a_swallow_is_refused_whatever_the_reason() -> None:
    check("a damned good reason does not make a blind catch less blind",
          errors_for(SWALLOW.format(why="dtserve raises anything")),
          ["noqa-not-explainable"])


def test_ble001_over_a_recorded_exception_is_accepted() -> None:
    # The evidence for a broad catch is the exception recorded rather than a
    # sentence. A handler that records it (a test harness reporting the
    # failure, say) has met that bar, so refusing it buys nothing.
    check("a handler that reports the exception is not refused",
          errors_for(RECORDS), [])


def test_a_noqa_cannot_hide_a_swallow_from_suppress_reason() -> None:
    # suppress-reason skips a blind except-pass because ruff's S110 owns it;
    # `# noqa: S110` is how ruff stops owning it, and without this the shape
    # would read 0 errors from every tool.
    bare = "def f():\n    try:\n        g()\n    except Exception:\n        pass\n"
    hushed = ("def f():\n    try:\n        g()\n"
              "    except Exception:  # noqa: BLE001,S110\n        pass\n")
    check("without a noqa, ruff owns it and this check stands aside",
          suppress_rules_for(bare), [])
    check("with one, the deferral is cancelled and it is reported here",
          suppress_rules_for(hushed), ["suppress-reason"])


def test_the_marker_is_matched_case_insensitively() -> None:
    # ruff's directive is case-insensitive, so every rule here has to be too:
    # otherwise `# NOQA: BLE001,S110` over a blind swallow reads 0 from every
    # tool, the hole this check exists to close.
    check("NOQA over a swallow is still refused",
          errors_for(SWALLOW.replace("noqa", "NOQA").format(why="x")),
          ["noqa-not-explainable"])
    check("a bare NOQA is still a blanket",
          errors_for("import dt  # NOQA -- it is fine\n"), ["noqa-blanket"])
    check("NOQA with no reason is still refused",
          errors_for("import dt  # NOQA: E402\n"), ["noqa-no-reason"])


def test_a_marker_that_is_not_a_marker_is_not_counted() -> None:
    check("inside a string literal",
          errors_for('SAMPLE = "x = 1  # noqa"\n'), [])
    check("prose in a standalone comment, which suppresses nothing",
          errors_for("# the rule: a bare '# noqa' names no codes\nx = 1\n"), [])
    check("neither shape produces anything at all",
          quiet_about('# about "# noqa" markers\nSAMPLE = "y  # noqa: E402"\n'), [])


def test_type_ignore_is_never_this_check_s_business() -> None:
    src = "x = y  # type: ignore[assignment]\n"
    check("an unreasoned type: ignore is not an error", errors_for(src), [])
    check("and nothing is said about it", quiet_about(src), [])


def test_an_unparseable_file_yields_nothing_from_this_check() -> None:
    check("ruff reports a file that will not tokenize; twice is one problem "
          "looking like two", errors_for("def f(  :::\n"), [])


def test_check_py_passes_its_own_rule() -> None:
    root = jpcheck_loader.ROOT
    issues = (check_mod.run_noqa(str(root / "check.py"))["issues"]
              + check_mod.run_noqa(str(root / "jpcheck"))["issues"])
    check("check.py and jpcheck/ state a reason on every marker they carry",
          [f"{i['file']}:{i['line']}:{i['rule']}" for i in issues if i["severity"] == "error"], [])


def main() -> int:
    for name, fn in sorted(globals().items()):
        if name.startswith("test_") and callable(fn):
            fn()
    if FAILURES:
        print(f"FAILED ({len(FAILURES)})")
        for f in FAILURES:
            print(f"FAIL {f}")      # "FAIL <label>: ...", the line mutate-on-target reads
        return 1
    print("all check.py noqa tests passed")
    return 0


if __name__ == "__main__":
    sys.exit(main())
