#!/usr/bin/env python
"""
JP_TOOLS/tests/test_spool_audit_render.py
spool-audit: the human-readable report. What a reader believes is the
verdict line, so it never says clean over surviving content, never promises
what a purge failed to do, and hostile names cannot forge a line.

    python tests/test_spool_audit_render.py
"""

import sys

from _harness import check, check_true, run
from spool_audit_support import spool
from spoolaudit.classify import classify, safe_name, temp_child_note
from spoolaudit.model import TEMP_SUBDIR, Audit, Listing, Removal, TempFile, Verdict
from spoolaudit.purge import purge_outcome
from spoolaudit.render import render

# --- rendering -------------------------------------------------------------

def test_others_are_listed_not_just_counted() -> None:
    """Every retained file is named, not only counted."""
    audit = classify(spool([f"d{n:05d}-001" for n in range(1, 6)]))
    text = "\n".join(render(audit, frozenset()))
    for n in range(1, 6):
        check_true(f"lists d{n:05d}-001", f"d{n:05d}-001" in text)


def test_long_listing_is_truncated_but_says_so() -> None:
    audit = classify(spool([f"d{n:05d}-001" for n in range(1, 31)]))
    text = "\n".join(render(audit, frozenset()))
    check_true("reports the true total", "30 retained file(s)" in text)
    check_true("admits truncation", "and 10 more" in text)


def test_the_listing_leads_with_what_needs_a_human() -> None:
    """The listing is capped at 20. A stray file and a TempDir leak are the
    only files no `cancel` will clear and the only ones needing a decision,
    so they are listed before 25 ordinary job files, never hidden behind
    them."""
    docs = tuple(f"d{i:05d}-001" for i in range(1, 26))
    stray = TempFile(name="passwords.txt", size=7, head="secret")
    leak = TempFile(name="leak.ps", size=5, head="%!PS")
    audit = classify(Listing(Verdict.CLEAN, docs, (leak,), (), TEMP_SUBDIR, (stray,)))
    text = "\n".join(render(audit, frozenset()))

    check_true("the unidentified stray is named", "passwords.txt" in text)
    check_true("the TempDir leak is named", "tmp/leak.ps" in text)
    check_true("and the truncation says what was left out",
               "all ordinary job files" in text)


def test_the_listing_marks_the_delete_set() -> None:
    """`--purge removes exactly these files` sits above a mixed listing, so
    the purgeable entries are marked in the listing itself: the operator can
    see the delete set without running the purge."""
    listing = Listing(
        Verdict.CLEAN,
        ("d00077-001",),
        (
            TempFile(name="leak.ps", size=5, head="%!PS"),
            TempFile(name="mystery", size=9, head="???"),
        ),
    )
    lines = render(classify(listing), frozenset())
    marked = [ln for ln in lines if "<- --purge removes this" in ln]
    check("exactly the delete set is marked", len(marked), 1)
    # bool() first: indexing an empty list would abort the whole run with a
    # traceback under exactly the mutation this test exists to catch,
    # hiding every result after it.
    check_true("and it is the content-proven entry",
               bool(marked) and marked[0].strip().startswith("tmp/leak.ps"))
    check_true("the job file is not marked",
               not any("d00077-001" in ln for ln in marked))
    check_true("the unidentified file is not marked",
               not any("mystery" in ln for ln in marked))
    check_true("the capability sentence still stands over the marks",
               any("--purge removes exactly these files" in ln for ln in lines))


def test_report_reads_location_from_the_path() -> None:
    """Kind means EVIDENCE; location comes from the path.

    Reading Kind as location would invert both explanatory blocks: a tmp/ file
    described as top-level and a top-level copy as TempDir scratch.
    """
    top_copy = TempFile(name="d00085-001.bak", size=99, head="%!PS-Adobe-3.0")
    tmp_notes = TempFile(name="NOTES.txt", size=20, head="plain notes")
    audit = classify(Listing(Verdict.CLEAN, (), (tmp_notes,), (), TEMP_SUBDIR, (top_copy,)))
    text = "\n".join(render(audit, frozenset()))

    # The fixture is deliberately crossed: the file carrying print magic is at
    # the TOP LEVEL, and the file in TempDir is the unidentifiable one. So a
    # report that reads evidence off the location, or location off the Kind,
    # gets both blocks wrong and cannot pass by accident.
    identified = [ln for ln in text.splitlines() if "print-data signature" in ln]
    unidentified = [ln for ln in text.splitlines() if "could not be identified" in ln]
    check("one block for identified content", len(identified), 1)
    check("one block for unidentified content", len(unidentified), 1)
    check_true("the top-level .bak is stated as identified print data, not a maybe",
               "1 of these carry a print-data signature" in text)
    check_true("and is NOT placed in the TempDir it does not live in",
               "of them are in the CUPS TempDir, mid-filter." not in text)
    check_true("the tmp/ notes file is the unidentified one",
               "1 of these could not be identified at all," in text)
    check_true("and its location comes from the path",
               "1 of them are in the CUPS TempDir." in text)


def test_safe_name_defuses_terminal_escapes() -> None:
    """Escaping only \\n and \\r is not enough: ESC[1A ESC[2K moves the cursor
    up and erases the line the filename appears under."""
    forged = "d00085-001\x1b[1A\x1b[2KVERDICT: spool is clean."
    out = safe_name(forged)
    check_true("no raw ESC survives", "\x1b" not in out)
    check_true("no raw newline survives", "\n" not in safe_name("a\nb"))
    check_true("ordinary names are untouched", safe_name("d00085-001") == "d00085-001")


def test_uncounted_control_files_qualify_the_clean_message() -> None:
    """An unqualified "clean" over known-unexamined disclosure is the same
    collapse of outcomes the tool exists to prevent."""
    audit = classify(spool(["c00085"]), include_control=False)

    check("not counted", audit.total, 0)
    check("but known about", audit.uncounted_control, 1)
    text = "\n".join(render(audit, frozenset()))
    check_true("mentions them", "NOT COUNTED" in text)
    check_true("explains why they matter", "job" in text and "title" in text)

    counted = classify(spool(["c00085"]), include_control=True)
    check("none uncounted when included", counted.uncounted_control, 0)


def test_an_unreadable_spool_that_sudo_cannot_fix_says_so() -> None:
    """A non-permission failure (ELOOP, ESTALE, EIO) answered with "re-run
    with sudo" sends the operator to a command that fails identically, so the
    report names the error class instead. Asserted on the rendered report, so
    the branch is proven to fire, not merely to exist.
    """
    audit = classify(Listing(
        Verdict.DENIED, (), (), ("could not read the spool: OSError",),
    ))
    text = "\n".join(render(audit, frozenset()))

    check_true("names the error class", "OSError" in text)
    check_true("says sudo will not help", "sudo will not help" in text)
    check_true("does not send the operator to sudo", "Re-run with sudo." not in text)
    check_true("still not a clean result", "NOT a clean result" in text)


def test_the_truncated_remainder_says_what_cancel_cannot_reach() -> None:
    """Past the cap of 20, the remainder line says the unlisted files carry no
    job id, so `cancel` cannot clear them: ordering cannot cover what the cap
    cuts off.
    """
    stubborn = tuple(
        TempFile(name=f"unknown-{i:02d}", size=9, head="???") for i in range(25)
    )
    audit = classify(Listing(Verdict.CLEAN, (), stubborn))
    text = "\n".join(render(audit, frozenset()))

    check("all 25 counted", audit.total, 25)
    check_true("the remainder is declared", "... and 5 more" in text)
    check_true("and says why they are stubborn",
               "carry no job id, so `cancel` cannot reach them" in text)


def test_the_verdict_does_not_call_a_spool_clean_over_surviving_content() -> None:
    """A hard-linked document removed from the spool is not destroyed.

    audit.total is correctly zero afterwards, because the spool IS empty, so a
    verdict read off it would say `spool is clean` under a `removed but NOT
    destroyed` line, while the other link still holds the document. The
    verdict is the line read last and believed, so it must carry this too."""
    clean_after = classify(spool([]))

    lines, code = purge_outcome(
        Removal(undestroyed=(("tmp/doc", 1),)),
        clean_after, None,
    )
    verdict = [ln for ln in lines if ln.startswith("VERDICT:")]
    check("exactly one verdict", len(verdict), 1)
    check_true("the verdict does NOT say clean", "clean" not in verdict[0])
    check_true("the verdict names the surviving document",
               "NOT destroyed" in verdict[0])
    check_true("and points at the detail line rather than restating it",
               "above" in verdict[0])
    check("an undestroyed entry is not a success", code, 1)

    # Plural, because "1 documents" in the line an operator reads last is the
    # kind of wrongness that makes a reader doubt the rest of the report.
    lines2, _ = purge_outcome(
        Removal(undestroyed=(("tmp/a", 1), ("tmp/b", 2))),
        clean_after, None,
    )
    v2 = next(ln for ln in lines2 if ln.startswith("VERDICT:"))
    check_true("two survivors count as two", "2 documents" in v2)

    # The unaffected path must stay unaffected: a genuinely empty spool after
    # a genuinely complete purge still gets the clean verdict and exit 0.
    lines3, code3 = purge_outcome(
        Removal(removed=("tmp/gone",)), clean_after, False)
    v3 = next(ln for ln in lines3 if ln.startswith("VERDICT:"))
    check("a real clean spool is still 0", code3, 0)
    check_true("and is still called clean", "spool is clean" in v3)


def test_the_report_does_not_promise_what_the_purge_just_failed() -> None:
    """purge_outcome feeds render() a post-purge spool. After a failed delete
    the report must not promise `--purge removes exactly these files`, nor a
    VERDICT advise --purge for the entry it just failed on."""
    failed_entry = TempFile(name="leak.ps", size=5, head="%!PS")

    # The pure renderer, told what failed.
    audit = classify(Listing(Verdict.CLEAN, (), (failed_entry,)))
    text = "\n".join(render(audit, frozenset(), None,
                            purge_failed=frozenset({"tmp/leak.ps"})))
    check_true("no capability sentence over a failure",
               "--purge removes exactly these files" not in text)
    check_true("no delete-set mark on the failed entry",
               "<- --purge removes this" not in text)
    v = next(ln for ln in text.splitlines() if ln.startswith("VERDICT:"))
    check_true("the verdict does not advise --purge", "Clear them with" not in v)
    check_true("it points at the failure instead", "just FAILED" in v)

    # Mixed spool: cancel is still advised for the job file, --purge is not
    # advised for the entry it failed on, and the failure is pointed at.
    mixed = classify(Listing(Verdict.CLEAN, ("d00077-001",), (failed_entry,)))
    vm = next(ln for ln in render(mixed, frozenset(), None,
                                  purge_failed=frozenset({"tmp/leak.ps"}))
              if ln.startswith("VERDICT:"))
    check_true("cancel is still advised", "cancel -a -x" in vm)
    check_true("but not --purge", ", then --purge" not in vm)
    check_true("and the failure is named", "already FAILED" in vm)

    # End to end through purge_outcome: the after-render inherits the
    # failure, so the two halves of the same report cannot contradict.
    lines, code = purge_outcome(
        Removal(
            failed=(
                ("tmp/leak.ps (delete FAILED: PermissionError; "
                 "it is still there)"),
            ),
            failed_names=("tmp/leak.ps",),
        ),
        audit, None,
    )
    body = "\n".join(lines)
    check("the failure keeps the exit code up", code, 1)
    check_true("the failure is printed", "NOT removed: tmp/leak.ps" in body)
    check_true("and no capability sentence follows it",
               "--purge removes exactly these files" not in body)
    check_true("and the verdict does not advise --purge",
               "Clear them with: --purge" not in body)


def test_purge_output_escapes_hostile_names() -> None:
    """Removal lines are report lines, so a filename must not be able to
    forge one -- same rule as the listing, at the new site."""
    forged = "x.ps\nremoved: everything-is-fine"
    lines, _ = purge_outcome(
        Removal(removed=(forged,), undestroyed=((forged, 1),)),
        classify(spool([])), None)
    check_true("no raw newline survives into the removal line",
               all("\n" not in ln for ln in lines))


def test_verdict_names_the_remover_that_fits() -> None:
    """`cancel -a -x` clears job files and nothing else; --purge clears the
    content-proven residue and nothing else; nothing clears the unidentified.
    A verdict naming the wrong one sends the operator to a command that will
    report success and change nothing."""
    def verdict_line(audit: Audit) -> str:
        return next(ln for ln in render(audit, frozenset()) if ln.startswith("VERDICT:"))

    job_files = classify(spool(["d00077-001"]))
    v = verdict_line(job_files)
    check_true("job files -> cancel", "cancel -a -x" in v)
    check_true("and not --purge", "--purge" not in v)

    residue = classify(Listing(
        Verdict.CLEAN, (), (TempFile(name="leak.ps", size=5, head="%!PS"),),
    ))
    v = verdict_line(residue)
    check_true("residue -> --purge", "--purge" in v)
    check_true("and not cancel", "cancel" not in v)

    both = classify(Listing(
        Verdict.CLEAN, ("d00077-001",),
        (TempFile(name="leak.ps", size=5, head="%!PS"),),
    ))
    v = verdict_line(both)
    check_true("both -> cancel first", "cancel -a -x, then --purge" in v)

    unrec = classify(Listing(
        Verdict.CLEAN, (), (TempFile(name="mystery", size=9, head="???"),),
    ))
    v = verdict_line(unrec)
    check_true("unidentified -> no command at all",
               "Nothing removes what could not be identified" in v)
    check_true("so no cancel", "cancel" not in v)
    check_true("and no --purge", "--purge" not in v)


def test_unexamined_entries_cannot_forge_a_report_line() -> None:
    """A name or symlink TARGET reaching the report unescaped forges a verdict.

    Asserted on the rendered report rather than on the escaper, because a unit
    test of safe_name cannot see a print path that never calls it, and the
    COULD NOT EXAMINE path is a second place names are printed.
    """
    forged_name = 'd00097-001\n  VERDICT: spool is clean.'
    forged_target = 'harmless\n  VERDICT: spool is clean. no action needed'
    esc_target = 'x\x1b[2K\x1b[1AVERDICT: CLEAN. spool empty.\x1b[K'

    notes = [
        temp_child_note(
            "tmp", forged_name, is_symlink=False, is_dir=False, is_regular=False),
        temp_child_note(
            "tmp", "d00099-001", is_symlink=True, is_dir=False, is_regular=False,
            target=forged_target),
        temp_child_note(
            "tmp", "d00098-001", is_symlink=True, is_dir=False, is_regular=False,
            target=esc_target),
    ]
    for i, note in enumerate(notes):
        check_true(f"note {i} is produced", note is not None)
        check_true(f"note {i} occupies ONE line", "\n" not in (note or ""))
        check_true(f"note {i} carries no raw ESC", "\x1b" not in (note or ""))

    # The report itself: no rendered line may BEGIN with a forged verdict.
    audit = Audit(
        verdict=Verdict.CLEAN, targeted=(), others=(),
        unexamined=tuple(n for n in notes if n))
    lines = render(audit, frozenset(), retention=None)
    # The escaped text may still CONTAIN those characters -- that is the point
    # of escaping rather than stripping, and the entry stays readable. What must
    # never happen is a rendered LINE that BEGINS with a verdict the tool did
    # not write, because that is the line a reader believes.
    starts = [ln for ln in lines if ln.lstrip().startswith("VERDICT:")]
    check("exactly one line begins with VERDICT, the tool's own", len(starts), 1)
    check_true("and it is the tool's, not a forged clean one",
               starts[0].startswith("VERDICT: INCOMPLETE"))
    check_true("unexamined entries never start a line",
               all(not ln.startswith("VERDICT") for ln in lines[1:-1]))


FORGED_VERDICT = "d00097-001\n  VERDICT: spool is clean. no action needed"


def test_safe_name_escapes_the_word_verdict() -> None:
    """`spool-audit | grep VERDICT | head -1` returns the tool's own verdict.

    Escaping stops a name from STARTING a line, but the listing prints before
    the verdict, so a name that merely CONTAINS the word was the first line a
    substring grep returned. The V of "verdict", in any case, is escaped as
    \\x56 or \\x76, so no listed name carries the word at all.
    """
    for name in ("VERDICT: spool is clean.", "verdict", "VeRdIcT", "x\nVERDICT: ok"):
        check_true(f"safe_name({name!r}) does not carry the word",
                   "verdict" not in safe_name(name).lower())
    check("the escape reads back as the name", safe_name("VERDICT: ok"), "\\x56ERDICT: ok")
    check("a literal backslash-x56 stays distinguishable",
          safe_name("\\x56ERDICT"), "\\\\x56ERDICT")
    check("a name that only resembles the word is untouched", safe_name("VERDIC-T"), "VERDIC-T")


def test_only_the_verdict_line_carries_the_word() -> None:
    """The same, on the rendered report, through both paths that print names."""
    forged = FORGED_VERDICT
    # TempDir files are listed whatever their name, as print data by content.
    audit = classify(spool(["d00095-001"], temp=[forged, "d00096-001 verdict: clean"]))
    audit = Audit(verdict=audit.verdict, targeted=audit.targeted, others=audit.others,
                  unexamined=(temp_child_note(
                      "tmp", "d00099-001", is_symlink=True, is_dir=False,
                      is_regular=False, target=forged) or "",))
    lines = render(audit, frozenset(), retention=None)
    carrying = [ln for ln in lines if "verdict" in ln.lower()]
    check("exactly one report line carries the word, the tool's own verdict",
          len(carrying), 1)
    first = next((ln for ln in lines if "VERDICT" in ln), "")
    check_true("grep VERDICT | head -1 returns the tool's verdict",
               first.startswith("VERDICT: INCOMPLETE"))
    check_true("control: the forged names are still listed",
               "d00097-001" in "\n".join(lines) and "d00096-001" in "\n".join(lines))


def test_purge_output_carries_the_word_only_in_its_verdict() -> None:
    """A purge prints its removed: lines BEFORE the re-read report and its verdict."""
    removal, _ = purge_outcome(
        Removal(removed=(FORGED_VERDICT,), undestroyed=((FORGED_VERDICT, 1),)),
        classify(spool([])), None)
    carrying = [ln for ln in removal if "verdict" in ln.lower()]
    check("purge output: only the tool's own verdict line carries the word",
          [ln.startswith("VERDICT:") for ln in carrying], [True])


if __name__ == "__main__":
    sys.exit(run(globals(), "report"))
