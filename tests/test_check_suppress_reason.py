#!/usr/bin/env python
"""
JP_TOOLS/tests/test_check_suppress_reason.py
Tests for check.py's `suppress-reason` check.

No pytest, matching the rest of tests/. Every case is a small source file
written to a temp directory and run through the real check.

WHAT THIS PINS DOWN
A swallowed exception is a decision that a failure does not matter here, and
without a reason on the line the decision is invisible. On 2026-09-15
projectbook was found naming 109 scripts' processes under
`try/except ImportError: pass` and `contextlib.suppress(ImportError)`, so a
script that failed to name itself ran on quietly, and the first fix added the
same blanket to 30 more. Nothing refused any of it.

The rule: a `contextlib.suppress(...)` or a handler that only passes,
continues, breaks or returns a default must carry `reason:` on its own line
or inside its body. A handler that does something (logs, raises, sets state)
is a decision the code shows and is not flagged.
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
        result = check_mod.run_suppress_reason(str(f))
        return [i["rule"] for i in result["issues"]]


def test_a_bare_suppress_is_flagged() -> None:
    check("contextlib.suppress with no reason", rules_for(
        "import contextlib\nwith contextlib.suppress(OSError):\n    os.unlink(p)\n"
    ), ["suppress-reason"])
    check("suppress imported by name", rules_for(
        "from contextlib import suppress\nwith suppress(ImportError):\n    import x\n"
    ), ["suppress-reason"])


def test_a_reasoned_suppress_is_accepted() -> None:
    check("a reason on the with line", rules_for(
        "import contextlib\nwith contextlib.suppress(OSError):  # reason: a stale cache is a miss\n    os.unlink(p)\n"
    ), [])
    check("an empty reason is not a reason", rules_for(
        "import contextlib\nwith contextlib.suppress(OSError):  # reason:\n    os.unlink(p)\n"
    ), ["suppress-reason"])
    check("a reason on the closing line of a wrapped with", rules_for(
        "import contextlib\nwith contextlib.suppress(\n    OSError,\n):  # reason: a stale cache is a miss\n    os.unlink(p)\n"
    ), [])


def test_a_silent_handler_is_flagged() -> None:
    for label, body in [
        ("continue in a loop", "    continue\n"),
        ("return None", "    return None\n"),
        ("return a constant", "    return 0\n"),
        ("return a bare name", "    return rows\n"),
        ("a bare return", "    return\n"),
        # jp-tools' review: the commonest silent defaults are these, and a
        # Constant-only test let every one of them through.
        ("return an empty list", "    return []\n"),
        ("return an empty dict", "    return {}\n"),
        ("return an empty tuple", "    return ()\n"),
        ("return a negative number", "    return -1\n"),
        ("return a literal list", "    return [0, '']\n"),
    ]:
        src = ("def f(rows):\n  for p in rows:\n   try:\n    open(p)\n   except OSError:\n" + body)
        check(f"a handler that only does {label} is flagged", rules_for(src), ["suppress-reason"])
    check("pass after a try body longer than one statement is flagged", rules_for(
        "try:\n    a()\n    b()\nexcept OSError:\n    pass\n"
    ), ["suppress-reason"])


def test_what_ruff_already_reports_is_not_counted_twice() -> None:
    """S110 owns a blind except-pass; SIM105 owns a typed except-pass on a
    one-statement try (and pushes it into the suppress() form this rule then
    sees). Counting them here too would make one problem look like two."""
    check("a bare except: pass is S110's", rules_for(
        "try:\n    x()\nexcept:\n    pass\n"
    ), [])
    check("except Exception: pass is S110's", rules_for(
        "try:\n    x()\nexcept Exception:\n    pass\n"
    ), [])
    check("a typed except: pass on a one-statement try is SIM105's", rules_for(
        "try:\n    x()\nexcept OSError:\n    pass\n"
    ), [])


def test_a_blind_catch_is_flagged_whatever_the_comment_says() -> None:
    """#58: a reason must not satisfy a blind catch. The fix for a blind catch
    is to log it or re-raise, never to annotate it."""
    check("except Exception returning a default, with a reason", rules_for(
        "try:\n    x()\nexcept Exception:  # reason: anything at all\n    return None\n"
    ), ["suppress-reason"])
    check("a bare except returning a default, with a reason", rules_for(
        "try:\n    x()\nexcept:  # reason: anything at all\n    return 0\n"
    ), ["suppress-reason"])
    check("BaseException inside a tuple", rules_for(
        "try:\n    x()\nexcept (OSError, BaseException):  # reason: no\n    return 0\n"
    ), ["suppress-reason"])
    check("suppress(Exception) with a reason", rules_for(
        "import contextlib\nwith contextlib.suppress(Exception):  # reason: no\n    x()\n"
    ), ["suppress-reason"])
    check("but a blind catch that LOGS is not this rule's business", rules_for(
        "try:\n    x()\nexcept Exception:\n    LOG.exception('x failed')\n    return None\n"
    ), [])


def test_a_reasoned_handler_is_accepted() -> None:
    check("a reason on the except line", rules_for(
        "try:\n    x()\nexcept OSError:  # reason: no ledger dir means no ledger; the caller says so\n    return None\n"
    ), [])
    check("a reason on the body line", rules_for(
        "try:\n    x()\nexcept OSError:\n    pass  # reason: the file is gone, which is what we wanted\n"
    ), [])
    check("a reason in a comment line inside the body", rules_for(
        "try:\n    x()\nexcept OSError:\n    # reason: not this check's job; ruff reports it\n    return []\n"
    ), [])


def test_a_handler_that_acts_is_not_this_checks_business() -> None:
    check("logging is a decision the code shows", rules_for(
        "try:\n    x()\nexcept OSError:\n    LOG.exception('x failed')\n    return None\n"
    ), [])
    check("re-raising", rules_for(
        "try:\n    x()\nexcept OSError as e:\n    raise SystemExit(str(e))\n"
    ), [])
    check("returning a computed value", rules_for(
        "try:\n    x()\nexcept OSError as e:\n    return str(e)\n"
    ), [])
    check("setting state and carrying on", rules_for(
        "try:\n    x()\nexcept OSError:\n    failed += 1\n"
    ), [])


def test_prose_is_not_a_reason_and_a_reason_is_scoped() -> None:
    check("a reason ABOVE the try does not reach the handler", rules_for(
        "# reason: written for the wrong line\ntry:\n    x()\nexcept OSError:\n    return None\n"
    ), ["suppress-reason"])
    check("two handlers, one reasoned: one finding", rules_for(
        "try:\n    x()\nexcept OSError:  # reason: expected\n    return 0\nexcept ValueError:\n    return 0\n"
    ), ["suppress-reason"])


def test_unparseable_source_is_left_to_ruff() -> None:
    check("a syntax error yields no findings from this check",
          rules_for("def f(  :::\n"), [])


def test_check_py_passes_its_own_rule() -> None:
    result = check_mod.run_suppress_reason(str(MODULE_PATH))
    check("check.py states a reason on every silent catch",
          [f"{i['line']}" for i in result["issues"]], [])


def main() -> int:
    for name, fn in sorted(globals().items()):
        if name.startswith("test_") and callable(fn):
            fn()
    if FAILURES:
        print(f"FAILED ({len(FAILURES)})")
        for f in FAILURES:
            print(f"FAIL {f}")      # "FAIL <label>: ...", the line mutate-on-target reads
        return 1
    print("all check.py suppress-reason tests passed")
    return 0


if __name__ == "__main__":
    sys.exit(main())
