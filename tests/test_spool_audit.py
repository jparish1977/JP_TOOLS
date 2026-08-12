#!/usr/bin/env python
"""
JP_TOOLS/tests/test_spool_audit.py
Tests for spool-audit.py, the CUPS print-spool auditor.

No pytest, no dependencies -- the toolbox has none and this should not add the
first. Runs anywhere: every test drives the pure classification functions with
literal directory listings, so no printer, no spool and no root are needed.

THE BUGS THIS PINS DOWN

    1. "Could not read" reported as "clean". The shell one-liner this replaced
       was:
           sudo ls /var/spool/cups/ | grep -E 'd0*(85|86)' || echo CLEAN
       When sudo fails, ls prints nothing, grep matches nothing, and it says
       CLEAN. A failed check is indistinguishable from a safe result. Measured
       for real on 2026-08-12: it printed CLEAN while sudo had not run at all.

    2. Successful cleanup reported as failure. The next version listed every
       retained document regardless of which jobs were asked about, so after
       deleting jobs 85 and 86 the leftover documents from other prints made it
       look like nothing had happened.

    Both are the same underlying error: collapsing distinct outcomes into one.
    Verdict and targeted_are_gone are therefore deliberately independent.

    python tests/test_spool_audit.py
"""

import importlib.util
import sys
from pathlib import Path

MODULE_PATH = Path(__file__).resolve().parent.parent / "spool-audit.py"

_spec = importlib.util.spec_from_file_location("spool_audit", MODULE_PATH)
assert _spec is not None and _spec.loader is not None
spool_audit = importlib.util.module_from_spec(_spec)
# Register before executing. @dataclass resolves its own module through
# sys.modules to evaluate annotations, and a module loaded by path alone is not
# there yet, so every frozen dataclass in the file raises AttributeError on
# import. Nothing to do with the code under test.
sys.modules["spool_audit"] = spool_audit
_spec.loader.exec_module(spool_audit)

classify = spool_audit.classify
parse_document = spool_audit.parse_document
render = spool_audit.render
Verdict = spool_audit.Verdict
Audit = spool_audit.Audit

FAILURES: list[str] = []


def check(label: str, actual: object, expected: object) -> None:
    if actual != expected:
        FAILURES.append(f"{label}: expected {expected!r}, got {actual!r}")


def check_true(label: str, value: bool) -> None:
    if not value:
        FAILURES.append(f"{label}: expected True")


# --- filename parsing ------------------------------------------------------

def test_parse() -> None:
    doc = parse_document("d00085-001")
    assert doc is not None
    check("document job id", doc.job, 85)

    # Control files are job history. CUPS keeps them by design and they hold
    # no document content, so they must never be counted as a leak.
    check("control file ignored", parse_document("c00085"), None)
    check("stray file ignored", parse_document("tmp"), None)
    check("no false match", parse_document("d00085"), None)

    # Job numbers are zero-padded to five digits but roll past it. Matching on
    # a fixed-width pattern would silently miss every job above 99999.
    big = parse_document("d123456-001")
    assert big is not None
    check("six digit job", big.job, 123456)


# --- the reporting bug -----------------------------------------------------

def test_targeted_gone_but_spool_not_clean() -> None:
    """The exact 2026-08-12 case: 85 and 86 deleted, other documents remain."""
    listing = ["c00085", "c00086", "d00077-001", "d00082-001"]
    audit = classify(listing, frozenset({85, 86}))

    check_true("targeted are gone", audit.targeted_are_gone)
    check("verdict still retained", audit.verdict, Verdict.RETAINED)
    check("other documents counted", len(audit.others), 2)

    text = "\n".join(render(audit, frozenset({85, 86})))
    check_true("says the targeted ones are gone", "Those documents are GONE" in text)
    check_true("still warns about retention", "RETAINS printed documents" in text)


def test_targeted_still_present() -> None:
    listing = ["c00085", "d00085-001", "d00077-001"]
    audit = classify(listing, frozenset({85}))

    check("one targeted", len(audit.targeted), 1)
    check("one other", len(audit.others), 1)
    check_true("not reported gone", not audit.targeted_are_gone)

    text = "\n".join(render(audit, frozenset({85})))
    check_true("names the file", "d00085-001" in text)
    check_true("flags it", "STILL PRESENT" in text)


# --- the three outcomes stay distinct --------------------------------------

def test_clean_spool() -> None:
    audit = classify(["c00085", "c00086"], frozenset({85}))
    check("clean verdict", audit.verdict, Verdict.CLEAN)
    check("nothing retained", audit.total, 0)
    check_true("targeted gone", audit.targeted_are_gone)
    check_true("says clean", "spool is clean" in "\n".join(render(audit, frozenset({85}))))


def test_unreadable_is_not_clean() -> None:
    """The original bug. An unreadable spool must never render as safe."""
    audit = Audit(Verdict.UNREADABLE, (), ())
    text = "\n".join(render(audit, frozenset()))

    check_true("says it could not read", "COULD NOT READ" in text)
    check_true("explicitly denies cleanliness", "NOT a clean result" in text)
    check_true("never claims clean", "spool is clean" not in text)

    # An empty listing and an unreadable one produce different verdicts even
    # though both yield zero documents. That distinction is the whole point.
    empty = classify([], frozenset())
    check("empty listing is clean", empty.verdict, Verdict.CLEAN)
    check_true("and unreadable is not", audit.verdict is not empty.verdict)


def test_no_jobs_requested() -> None:
    """With no job ids, everything is 'other' and no targeted section appears."""
    audit = classify(["d00077-001"], frozenset())
    check("nothing targeted", len(audit.targeted), 0)
    check("all other", len(audit.others), 1)
    check_true("no targeted section", "JOBS YOU ASKED ABOUT" not in "\n".join(render(audit, frozenset())))


def main() -> int:
    for name, fn in sorted(globals().items()):
        if name.startswith("test_") and callable(fn):
            fn()

    if FAILURES:
        print(f"FAILED ({len(FAILURES)})")
        for f in FAILURES:
            print(f"  {f}")
        return 1
    print("all spool-audit tests passed")
    return 0


if __name__ == "__main__":
    sys.exit(main())
