"""The human-readable report of an audit."""

from __future__ import annotations

from spoolaudit.classify import safe_name
from spoolaudit.model import ARTIFACT, Audit, Kind, Verdict


def render(
    audit: Audit,
    jobs: frozenset[int],
    retention: bool | None = None,
    purge_failed: frozenset[str] = frozenset(),
    undestroyed: int = 0,
) -> list[str]:
    """Format an Audit for a human. Returns lines; printing is the caller's job.

    `retention` is whether CUPS is currently configured to keep documents, read
    from cupsd.conf. It is deliberately a separate input: inferring it from
    "are there files here" would say the host still retains data right after
    retention was turned off, because documents from before are still on disk.

    `purge_failed` names the entries a purge THIS RUN already failed to
    remove. The identified paragraph and the VERDICT were written for a
    pre-purge spool, and purge_outcome feeds this same function a post-purge
    one: without this input the capability sentence was printed four lines
    under proof that it did not hold, and the VERDICT advised --purge for an
    entry --purge had just failed on. It is an input to the one renderer
    rather than a second renderer, which is the drift purge_outcome exists
    to prevent.
    """
    if audit.verdict is Verdict.DENIED:
        # A non-permission error (ELOOP, ESTALE, EIO) must not be answered with
        # "re-run with sudo", which would fail identically. read_spool passes
        # the class name through unexamined when it knows it.
        detail = [u for u in audit.unexamined if u.startswith("could not read the spool")]
        if detail:
            return [
                f"COULD NOT READ THE SPOOL ({detail[0].split(': ', 1)[1]}).",
                "Nothing is proven either way. This is NOT a clean result.",
                "This is not a permissions problem; sudo will not help.",
            ]
        return [
            "COULD NOT READ THE SPOOL (permission denied).",
            "Nothing is proven either way. This is NOT a clean result.",
            "Re-run with sudo.",
        ]
    if audit.verdict is Verdict.NOT_A_DIRECTORY:
        return [
            "THAT PATH IS NOT A DIRECTORY.",
            "Nothing is proven either way. This is NOT a clean result.",
            "It exists; --spool needs the spool directory, not a file inside it.",
        ]
    if audit.verdict is Verdict.MISSING:
        return [
            "THAT PATH DOES NOT EXIST.",
            "Nothing is proven either way. This is NOT a clean result.",
            "Check --spool; this is a wrong path, not a permissions problem.",
        ]

    lines = [f"Spool holds {audit.total} retained file(s).", ""]

    if jobs:
        wanted = ", ".join(str(j) for j in sorted(jobs))
        lines.append(f"JOBS YOU ASKED ABOUT ({wanted}):")
        if audit.targeted_are_gone:
            lines.append("  none present. Those documents are GONE.")
        else:
            lines += [f"  >>> {safe_name(e.name)}  STILL PRESENT" for e in audit.targeted]
            lines.append("  clear these with: cancel -x " + " ".join(str(j) for j in sorted(jobs)))
        lines.append("")

    # Kind means EVIDENCE since the classification was split; location comes
    # from the path. Reading Kind as location inverted both explanatory blocks:
    # a tmp/ file was described as top-level and a top-level copy as TempDir
    # scratch, sending the operator to the wrong directory.
    # Split by EVIDENCE, because that is what Kind means. Merging TEMP with
    # UNRECOGNISED described a file whose first bytes say %!PS as "unrecognised,
    # may be document content", which understates a CONFIRMED leak as a maybe,
    # and undoes the split that was introduced to make Kind mean evidence.
    identified = [e for e in audit.others if e.kind is Kind.TEMP]
    unidentified = [e for e in audit.others if e.kind is Kind.UNRECOGNISED]
    rest = [e for e in audit.others if e.kind not in (Kind.TEMP, Kind.UNRECOGNISED)]

    lines.append(f"OTHER RETAINED FILES: {len(audit.others)}")
    # Stubborn first. The listing is capped at 20, so leading with the ordinary
    # job files would hide a stray passwords.txt or a TempDir leak behind 25
    # documents: the only files --purge would not remove would be the ones the
    # operator never saw.
    ordered = unidentified + identified + rest
    # The delete set is marked per entry, not only counted. "N of these" above
    # an unmarked mixed listing left the operator unable to determine WHICH
    # files "--purge removes exactly these files" meant, short of running the
    # purge and reading what it destroyed. An entry the purge just failed on
    # is not marked: the mark is the same per-entry promise the capability
    # sentence makes, and it has just been disproven for that entry.
    lines += [
        f"  {safe_name(e.name)}"
        + ("  <- --purge removes this"
           if e.kind is Kind.TEMP and e.name not in purge_failed else "")
        for e in ordered[:20]
    ]
    hidden = ordered[20:]
    if hidden:
        # Say what was hidden, not just how much. With the order above the
        # remainder is normally all ordinary job files, but say so only when
        # it is true.
        stubborn = sum(1 for e in hidden
                       if e.kind in (Kind.TEMP, Kind.UNRECOGNISED))
        if stubborn:
            lines.append(f"  ... and {len(hidden)} more, {stubborn} of which "
                         "carry no job id, so `cancel` cannot reach them")
        else:
            lines.append(f"  ... and {len(hidden)} more, all ordinary job files "
                         "that `cancel -a -x` clears")
    if identified:
        in_temp = sum(1 for e in identified if e.in_temp)
        lines.append("")
        lines.append(
            f"  {len(identified)} of these carry a print-data signature"
        )
        lines.append("  (PostScript, PDF or PJL), so they ARE document content by")
        lines.append("  evidence, not a maybe. They carry no job id, so they cannot be")
        lines.append("  targeted by job number, so `cancel` cannot reach them.")
        # The capability sentence asserts a future the caller may have already
        # spent: on a post-purge render with failures it sat four lines under
        # "delete FAILED". A run with failures does not get to promise.
        if not purge_failed:
            lines.append("  --purge removes exactly these files and nothing else.")
        if in_temp:
            lines.append(f"  {in_temp} of them are in the CUPS TempDir, mid-filter.")
    if unidentified:
        in_temp = sum(1 for e in unidentified if e.in_temp)
        lines.append("")
        lines.append(
            f"  {len(unidentified)} of these could not be identified at all,"
        )
        lines.append("  so they are not ruled out. Look at them before you")
        lines.append("  decide anything; this tool will not decide for you.")
        if in_temp:
            lines.append(f"  {in_temp} of them are in the CUPS TempDir.")

    if audit.artifacts:
        # Split by what they actually are. Filing a stray zero-length file
        # under "CUPS RUNTIME FILES" trains the operator to skim the one
        # section a real stray could hide in -- but dropping it from the report
        # instead would make it invisible, which is worse.
        # `e.in_temp and`, not the name alone. CUPS never creates cups-* files
        # at the top level of the spool -- is_harmless_temp says so and gates
        # the same regex on the same fact -- so a top-level cups-notifier was
        # filed under "CUPS RUNTIME FILES" and the operator told to leave alone
        # a file that, by this tool's own reasoning, CUPS cannot have created.
        # Not a disclosure: reaching audit.artifacts at the top level still
        # requires size 0 or a *PPD-Adobe header, verified against a fixture of
        # both. It is the same name-trust defect as the classifier's, at the
        # second site, which is this file's most repeated failure.
        runtime = [e for e in audit.artifacts
                   if e.in_temp and ARTIFACT.match(e.name.rsplit("/", 1)[-1])]
        other = [e for e in audit.artifacts if e not in runtime]
        if runtime:
            lines.append("")
            lines.append(f"CUPS RUNTIME FILES (not document content, leave them alone): {len(runtime)}")
            lines += [f"  {safe_name(e.name)}" for e in runtime]
        if other:
            lines.append("")
            lines.append(f"OTHER FILES, no print-data signature: {len(other)}")
            lines += [f"  {safe_name(e.name)}" for e in other]
            lines.append("  Empty, or a recognised driver cache. Listed so nothing is hidden.")

    if audit.unexamined:
        lines.append("")
        lines.append(f"COULD NOT EXAMINE: {len(audit.unexamined)}")
        lines += [f"  {u}" for u in audit.unexamined]
        lines.append("  Anything in there is unaccounted for. This is NOT a clean result.")

    if audit.uncounted_control:
        lines.append("")
        lines.append(
            f"NOT COUNTED: {audit.uncounted_control} control file(s). They carry the job"
        )
        lines.append("  title and submitting user, which can be disclosure by itself.")
        lines.append("  Use --include-control to count them.")

    lines.append("")
    if retention is True:
        lines.append("RETENTION: ON. CUPS is keeping documents. Set")
        lines.append("  'PreserveJobFiles No' in cupsd.conf and restart cups.")
    elif retention is False:
        lines.append("RETENTION: OFF (PreserveJobFiles No).")
    else:
        lines.append("RETENTION: unknown (could not read cupsd.conf).")

    if audit.unexamined:
        lines.append(
            f"VERDICT: INCOMPLETE. {audit.total} retained file(s) found, but "
            f"{len(audit.unexamined)} area(s) could not be examined."
        )
    elif audit.verdict is Verdict.RETAINED:
        # Name the remover that actually fits what remains. The old line said
        # `cancel -a -x` unconditionally, which over a spool holding only
        # TempDir residue named a command that clears none of it -- advice the
        # report's own "cancel cannot reach them" paragraph contradicts.
        everything = audit.targeted + audit.others
        # An entry --purge just failed on must not have --purge advised for
        # it: the advice would name a command whose failure is printed a few
        # lines up, in the same report.
        stuck = [e for e in everything
                 if e.kind is Kind.TEMP and e.name in purge_failed]
        how = []
        if any(e.job is not None for e in everything):
            how.append("cancel -a -x")
        if any(e.kind is Kind.TEMP and e.name not in purge_failed
               for e in everything):
            how.append("--purge")
        if how:
            advice = "Clear them with: " + ", then ".join(how)
            if stuck:
                advice += (" (--purge already FAILED on the rest; "
                           "see the NOT removed line(s) above)")
        elif stuck:
            advice = ("--purge just FAILED on what remains; "
                      "see the NOT removed line(s) above.")
        else:
            # Only unidentified files remain. No command fits by design:
            # nothing here removes what could not be identified.
            advice = ("Nothing removes what could not be identified; "
                      "look at the listing above.")
        lines.append(f"VERDICT: {audit.total} retained file(s) still on disk. {advice}")
    elif undestroyed:
        # The spool IS empty and the documents are NOT gone: --purge unlinked
        # the spool's entry while st_nlink said other hard links to the same
        # content survive. audit.total counts what is in the spool, so it is
        # correctly zero here, and "clean" read off that zero is a claim about
        # the DOCUMENTS that nothing measured. The verdict is the line read
        # last and believed, so it has to carry this too, not only the exit
        # code and the per-entry line above.
        #
        # Same shape as the failed-purge branch above: name the condition, then
        # point at the detail line rather than restating it.
        noun = "document" if undestroyed == 1 else "documents"
        lines.append(
            f"VERDICT: spool is EMPTY but {undestroyed} {noun} NOT destroyed; "
            'see the "removed but NOT destroyed" line(s) above.')
    else:
        lines.append("VERDICT: spool is clean.")
    return lines
