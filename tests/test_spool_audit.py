#!/usr/bin/env python
"""
JP_TOOLS/tests/test_spool_audit.py
Tests for spool-audit.py, the CUPS print-spool auditor.

No pytest, no dependencies -- the toolbox has none and this should not add the
first. Every test drives the pure functions with literal listings, so no
printer, no spool and no root are needed.

THE BUGS THIS PINS DOWN
All were found by running the tool or by mutating it, never by reading it.

  1. "Could not read" reported as "clean". The shell one-liner this replaced:
         sudo ls /var/spool/cups/ | grep -E 'd0*(85|86)' || echo CLEAN
     When sudo fails, ls prints nothing, grep matches nothing, and it says
     CLEAN. Measured 2026-08-12: it printed CLEAN while sudo had not run.

  2. Successful cleanup reported as failure. The next version listed every
     retained document regardless of which jobs were asked about, so deleting
     85 and 86 looked identical to doing nothing.

  3. tmp/ never audited. CUPS TempDir defaults to /var/spool/cups/tmp and
     holds document content during filtering. v1 listed only the top level and
     discarded "tmp" as a stray file -- and its test asserted that discard,
     pinning the blind spot in place. A spool with readable document data in
     tmp/ reported "VERDICT: spool is clean."

  4. --purge ignored the job ids it accepted. Verified against a fixture:
     `85 --purge` deleted d00085-001, d00077-001 AND d00099-001. On a shared
     household printer that destroys other people's documents.

  5. A failed delete reported as files respawning. sudo rm's return code was
     discarded, so a permissions failure printed "something is recreating
     them" and sent the user hunting a nonexistent process.

  6. Exit code re-merged the two states the data model keeps apart, so
     `spool-audit.py 85 && echo SAFE` never fired even when job 85 was gone.

MUTATION TESTED
Breaking each behaviour above must make this suite fail. Three mutations
survived the first version of these tests -- "purge deletes nothing", "exit
code always 0", "others list truncated" -- all in untested main()/CLI logic.

    python tests/test_spool_audit.py
"""

import importlib.util
import sys
from pathlib import Path
from typing import Any

MODULE_PATH = Path(__file__).resolve().parent.parent / "spool-audit.py"

_spec = importlib.util.spec_from_file_location("spool_audit", MODULE_PATH)
assert _spec is not None and _spec.loader is not None
spool_audit = importlib.util.module_from_spec(_spec)
# Register before executing. @dataclass resolves its own module through
# sys.modules to evaluate annotations, and a module loaded by path alone is not
# there yet, so every frozen dataclass raises AttributeError on import.
sys.modules["spool_audit"] = spool_audit
_spec.loader.exec_module(spool_audit)

classify = spool_audit.classify
parse_entry = spool_audit.parse_entry
render = spool_audit.render
victims_for = spool_audit.victims_for
Audit = spool_audit.Audit
Listing = spool_audit.Listing
Verdict = spool_audit.Verdict
Kind = spool_audit.Kind

FAILURES: list[str] = []


def check(label: str, actual: object, expected: object) -> None:
    if actual != expected:
        FAILURES.append(f"{label}: expected {expected!r}, got {actual!r}")


def check_true(label: str, value: bool) -> None:
    if not value:
        FAILURES.append(f"{label}: expected True")


def spool(top: list[str], temp: list[str] | None = None) -> Any:
    """Build a readable Listing fixture."""
    return Listing(Verdict.CLEAN, tuple(top), tuple(temp or []))


# --- filename classification ----------------------------------------------

def test_parse() -> None:
    doc = parse_entry("d00085-001", include_control=False)
    assert doc is not None
    check("document job id", doc.job, 85)
    check("document kind", doc.kind, Kind.DOCUMENT)

    check("control ignored by default", parse_entry("c00085", include_control=False), None)
    ctl = parse_entry("c00085", include_control=True)
    assert ctl is not None
    check("control counted when asked", ctl.kind, Kind.CONTROL)

    check("stray ignored", parse_entry("some-other-file", include_control=False), None)
    check("no false match", parse_entry("d00085", include_control=False), None)

    big = parse_entry("d123456-001", include_control=False)
    assert big is not None
    check("six digit job", big.job, 123456)


# --- bug 3: tmp/ is document data ------------------------------------------

def test_tempdir_is_not_clean() -> None:
    """A spool whose only content is in tmp/ must never report clean."""
    audit = classify(spool(top=["c00085"], temp=["cups-filter-abc123"]))

    check("tmp counted", audit.total, 1)
    check("not clean", audit.verdict, Verdict.RETAINED)
    text = "\n".join(render(audit, frozenset()))
    check_true("does not claim clean", "spool is clean" not in text)
    check_true("names the temp file", "tmp/cups-filter-abc123" in text)
    check_true("explains tmp", "TempDir" in text)


def test_tempdir_cannot_be_targeted_by_job() -> None:
    """Temp files carry no job id, so a job-scoped purge must not claim them."""
    audit = classify(spool(top=["d00085-001"], temp=["cups-xyz"]), frozenset({85}))

    check("one targeted", len(audit.targeted), 1)
    check("targeted is the document", audit.targeted[0].name, "d00085-001")
    check("temp is in others", audit.others[0].kind, Kind.TEMP)


# --- bug 4: purge scope ----------------------------------------------------

def test_purge_honours_job_filter() -> None:
    """The 2026-08-12 case: '85 --purge' must not delete 77 and 99."""
    audit = classify(spool(["d00085-001", "d00077-001", "d00099-001"]), frozenset({85}))
    victims = victims_for(audit)

    check("only one victim", len(victims), 1)
    check("and it is job 85", victims[0].name, "d00085-001")


def test_purge_without_jobs_takes_everything() -> None:
    audit = classify(spool(["d00085-001", "d00077-001"], temp=["cups-abc"]))
    check("all three", len(victims_for(audit)), 3)


# --- bug 2: targeted gone vs spool clean -----------------------------------

def test_targeted_gone_but_spool_not_clean() -> None:
    audit = classify(spool(["c00085", "d00077-001", "d00082-001"]), frozenset({85, 86}))

    check_true("targeted are gone", audit.targeted_are_gone)
    check("verdict still retained", audit.verdict, Verdict.RETAINED)
    check("others counted", len(audit.others), 2)

    text = "\n".join(render(audit, frozenset({85, 86})))
    check_true("says they are gone", "Those documents are GONE" in text)
    check_true("still warns", "RETAINS printed data" in text)


def test_targeted_still_present() -> None:
    audit = classify(spool(["d00085-001", "d00077-001"]), frozenset({85}))
    check("one targeted", len(audit.targeted), 1)
    check_true("not reported gone", not audit.targeted_are_gone)
    text = "\n".join(render(audit, frozenset({85})))
    check_true("names it", "d00085-001" in text)
    check_true("flags it", "STILL PRESENT" in text)


# --- bug 6: exit codes -----------------------------------------------------

def test_exit_codes() -> None:
    """`spool-audit.py 85 && echo SAFE` must fire when 85 is gone."""
    gone = classify(spool(["d00077-001"]), frozenset({85}))
    check("asked about 85, it is gone -> 0", gone.exit_code, 0)

    present = classify(spool(["d00085-001"]), frozenset({85}))
    check("asked about 85, still there -> 1", present.exit_code, 1)

    dirty = classify(spool(["d00077-001"]))
    check("no jobs asked, spool dirty -> 1", dirty.exit_code, 1)

    clean = classify(spool([]))
    check("no jobs asked, spool clean -> 0", clean.exit_code, 0)

    check("denied -> 2", classify(Listing(Verdict.DENIED)).exit_code, 2)
    check("missing -> 2", classify(Listing(Verdict.MISSING)).exit_code, 2)


# --- bug 1: the three unreadable/clean states stay distinct -----------------

def test_clean_spool() -> None:
    audit = classify(spool(["c00085"]), frozenset({85}))
    check("clean", audit.verdict, Verdict.CLEAN)
    check("nothing retained", audit.total, 0)
    check_true("says clean", "spool is clean" in "\n".join(render(audit, frozenset({85}))))


def test_denied_is_not_clean() -> None:
    audit = classify(Listing(Verdict.DENIED))
    text = "\n".join(render(audit, frozenset()))

    check_true("says permission denied", "permission denied" in text)
    check_true("denies cleanliness", "NOT a clean result" in text)
    check_true("never claims clean", "spool is clean" not in text)
    check_true("not readable", not audit.readable)

    empty = classify(spool([]))
    check("empty listing is clean", empty.verdict, Verdict.CLEAN)
    check_true("denied differs from empty", audit.verdict is not empty.verdict)


def test_missing_path_is_distinct_from_denied() -> None:
    """A typo in --spool must not send the user looking for root."""
    audit = classify(Listing(Verdict.MISSING))
    text = "\n".join(render(audit, frozenset()))

    check_true("says path does not exist", "DOES NOT EXIST" in text)
    check_true("does not blame permissions", "permission denied" not in text)
    check_true("denies cleanliness", "NOT a clean result" in text)


# --- control files ---------------------------------------------------------

def test_control_files_opt_in() -> None:
    """Control files carry job titles, so they are disclosure, but opt-in."""
    default = classify(spool(["c00085"]), include_control=False)
    check("ignored by default", default.total, 0)

    opted = classify(spool(["c00085"]), include_control=True)
    check("counted when asked", opted.total, 1)
    check("as control", opted.others[0].kind, Kind.CONTROL)


# --- rendering -------------------------------------------------------------

def test_others_are_listed_not_just_counted() -> None:
    """Mutation 'others[:0]' survived the first suite: nothing read the list."""
    audit = classify(spool([f"d{n:05d}-001" for n in range(1, 6)]))
    text = "\n".join(render(audit, frozenset()))
    for n in range(1, 6):
        check_true(f"lists d{n:05d}-001", f"d{n:05d}-001" in text)


def test_long_listing_is_truncated_but_says_so() -> None:
    audit = classify(spool([f"d{n:05d}-001" for n in range(1, 31)]))
    text = "\n".join(render(audit, frozenset()))
    check_true("reports the true total", "30 retained file(s)" in text)
    check_true("admits truncation", "and 10 more" in text)


def test_no_jobs_requested() -> None:
    audit = classify(spool(["d00077-001"]))
    check("nothing targeted", len(audit.targeted), 0)
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
