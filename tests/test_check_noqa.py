#!/usr/bin/env python
"""
JP_TOOLS/tests/test_check_noqa.py
Tests for check.py's `noqa` check: the three things that fail, and the silence
about everything else.

No pytest, matching the rest of tests/. Every case is a small source file
written to a temp directory and run through the real check.

WHAT THIS PINS DOWN
A `# pragma: no cover` has to say why and `list-exemptions.py` lists every one.
A `# noqa` had neither, so it suppressed a finding with nothing required of it
and nothing listing it (#58). Joe, 2026-09-14: "the damned good reasons need to
be damned good reasons, not an easy escape route."

The rule, in three parts, because they are not one thing:
  * a marker must state a reason, since for a structural suppression the
    sentence is the evidence
  * a BLANKET `# noqa` with no codes, which turns off rules nobody has written
    yet and names none of them
  * `BLE001` and `S110` over a handler that SWALLOWS are not available under
    any reason, because the evidence there is the exception recorded

And one regression, which is why the check exists at all: a `# noqa` used to
take a blind swallow from 2 errors to 0, suppress-reason included, because that
check defers the shape to ruff and a noqa is how you stop ruff reporting it.
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


def _run(source: str) -> list[dict]:
    with tempfile.TemporaryDirectory() as tmp:
        f = pathlib.Path(tmp) / "sample.py"
        f.write_text(source)
        return check_mod.run_noqa(str(f))["issues"]


def errors_for(source: str) -> list[str]:
    """The rules the noqa check fails on."""
    return [i["rule"] for i in _run(source) if i["severity"] == "error"]


def quiet_about(source: str) -> list[str]:
    """Everything the check emits, of any severity.

    THIS CHECK REFUSES AND DOES NOT LIST (Joe, 2026-09-16), so an accepted
    marker produces NOTHING, not a warning. Pinned as its own idea rather than
    folded into errors_for: the first version listed every marker, and the
    difference between "accepted" and "accepted and announced" is the whole of
    that decision.
    """
    return [i["rule"] for i in _run(source)]


def suppress_rules_for(source: str) -> list[str]:
    with tempfile.TemporaryDirectory() as tmp:
        f = pathlib.Path(tmp) / "sample.py"
        f.write_text(source)
        return [i["rule"] for i in check_mod.run_suppress_reason(str(f))["issues"]]


# The shape #58 is about: a structural suppression whose evidence is a sentence.
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


def test_BLE001_over_a_swallow_is_refused_whatever_the_reason() -> None:
    check("a damned good reason does not make a blind catch less blind",
          errors_for(SWALLOW.format(why="dtserve raises anything")),
          ["noqa-not-explainable"])


def test_BLE001_over_a_RECORDED_exception_is_accepted() -> None:
    # #58's argument is that the evidence is the exception recorded rather than
    # a sentence. A handler that records it has met that bar, so refusing it
    # buys nothing. Measured against another repo 2026-09-16: the literal
    # reading fires on 17 sites, every one a test harness of this exact shape.
    check("a handler that reports the exception is not refused",
          errors_for(RECORDS), [])


def test_a_noqa_cannot_hide_a_swallow_from_suppress_reason() -> None:
    # THE REGRESSION. suppress-reason skips a blind except-pass because ruff's
    # S110 owns it; `# noqa: S110` is how ruff stops owning it, and the shape
    # then measured 0 errors from all five tools.
    bare = "def f():\n    try:\n        g()\n    except Exception:\n        pass\n"
    hushed = ("def f():\n    try:\n        g()\n"
              "    except Exception:  # noqa: BLE001,S110\n        pass\n")
    check("without a noqa, ruff owns it and this check stands aside",
          suppress_rules_for(bare), [])
    check("with one, the deferral is cancelled and it is reported here",
          suppress_rules_for(hushed), ["suppress-reason"])


def test_the_marker_is_matched_case_insensitively() -> None:
    # ruff's directive is case-insensitive, so every rule here has to be too.
    # Matched case-sensitively, `# NOQA: BLE001,S110` over a blind swallow read
    # 0 from every tool: the hole this check exists to close, reopened by
    # holding shift. jp-tools' review of #109.
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
    check("and nothing is said about it: #58 leaves that open and dropping "
          "the listing left nothing for it to belong to", quiet_about(src), [])


def test_an_unparseable_file_yields_nothing_from_this_check() -> None:
    check("ruff reports a file that will not tokenize; twice is one problem "
          "looking like two", errors_for("def f(  :::\n"), [])


def test_check_py_passes_its_own_rule() -> None:
    issues = check_mod.run_noqa(str(MODULE_PATH))["issues"]
    check("check.py states a reason on every marker it carries",
          [f"{i['line']}:{i['rule']}" for i in issues if i["severity"] == "error"], [])


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
