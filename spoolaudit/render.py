"""The human-readable report of an audit."""

from __future__ import annotations

from spoolaudit.classify import safe_name
from spoolaudit.model import ARTIFACT, Audit, Entry, Kind, Verdict

# How many retained files the listing names before summarising the rest.
LISTING_CAP = 20

_NOT_CLEAN = "Nothing is proven either way. This is NOT a clean result."


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
    remove, and `undestroyed` counts entries it unlinked while other hard
    links keep the content. purge_outcome feeds this same function a
    post-purge spool, so the report must not promise or advise --purge for an
    entry --purge has just failed on. They are inputs to the one renderer
    rather than a second renderer, so the two reports cannot drift apart.
    """
    unreadable = _unreadable(audit)
    if unreadable is not None:
        return unreadable
    lines = [f"Spool holds {audit.total} retained file(s).", ""]
    lines += _jobs_section(audit, jobs)
    lines += _others_section(audit, purge_failed)
    lines += _artifacts_section(audit)
    lines += _unexamined_section(audit)
    lines += _control_section(audit)
    lines.append("")
    lines += _retention_lines(retention)
    lines.append(_verdict(audit, purge_failed, undestroyed))
    return lines


def _unreadable(audit: Audit) -> list[str] | None:
    """The whole report when the spool could not be read, else None."""
    if audit.verdict is Verdict.DENIED:
        # A non-permission error (ELOOP, ESTALE, EIO) must not be answered with
        # "re-run with sudo", which would fail identically. read_spool passes
        # the class name through unexamined when it knows it.
        detail = [u for u in audit.unexamined if u.startswith("could not read the spool")]
        if detail:
            return [f"COULD NOT READ THE SPOOL ({detail[0].split(': ', 1)[1]}).", _NOT_CLEAN,
                    "This is not a permissions problem; sudo will not help."]
        return ["COULD NOT READ THE SPOOL (permission denied).", _NOT_CLEAN, "Re-run with sudo."]
    if audit.verdict is Verdict.NOT_A_DIRECTORY:
        return ["THAT PATH IS NOT A DIRECTORY.", _NOT_CLEAN,
                "It exists; --spool needs the spool directory, not a file inside it."]
    if audit.verdict is Verdict.MISSING:
        return ["THAT PATH DOES NOT EXIST.", _NOT_CLEAN,
                "Check --spool; this is a wrong path, not a permissions problem."]
    return None


def _jobs_section(audit: Audit, jobs: frozenset[int]) -> list[str]:
    if not jobs:
        return []
    wanted = ", ".join(str(j) for j in sorted(jobs))
    lines = [f"JOBS YOU ASKED ABOUT ({wanted}):"]
    if audit.targeted_are_gone:
        lines.append("  none present. Those documents are GONE.")
    else:
        lines += [f"  >>> {safe_name(e.name)}  STILL PRESENT" for e in audit.targeted]
        lines.append("  clear these with: cancel -x " + " ".join(str(j) for j in sorted(jobs)))
    return [*lines, ""]


def _others_section(audit: Audit, purge_failed: frozenset[str]) -> list[str]:
    """The listing of retained files, then what the identified and unidentified ones mean.

    Split by EVIDENCE, because that is what Kind means; location comes from
    the path. A file whose first bytes say %!PS is a CONFIRMED leak, not a
    maybe, and a tmp/ file is not a top-level one.
    """
    identified = [e for e in audit.others if e.kind is Kind.TEMP]
    unidentified = [e for e in audit.others if e.kind is Kind.UNRECOGNISED]
    rest = [e for e in audit.others if e.kind not in (Kind.TEMP, Kind.UNRECOGNISED)]
    lines = [f"OTHER RETAINED FILES: {len(audit.others)}"]
    # Stubborn first. The listing is capped, so leading with the ordinary job
    # files would hide a stray passwords.txt or a TempDir leak behind them:
    # the only files `cancel` cannot remove would be the ones never shown.
    lines += _listing(unidentified + identified + rest, purge_failed)
    lines += _identified_note(identified, purge_failed)
    lines += _unidentified_note(unidentified)
    return lines


def _listing(ordered: list[Entry], purge_failed: frozenset[str]) -> list[str]:
    # The delete set is marked per entry, so the operator can see WHICH files
    # "--purge removes exactly these files" means without running the purge.
    # An entry the purge just failed on is not marked: the mark is a promise,
    # and it has just been disproven for that entry.
    lines = [
        f"  {safe_name(e.name)}"
        + ("  <- --purge removes this" if e.kind is Kind.TEMP and e.name not in purge_failed else "")
        for e in ordered[:LISTING_CAP]
    ]
    hidden = ordered[LISTING_CAP:]
    if hidden:
        # Say what was hidden, not just how much.
        stubborn = sum(1 for e in hidden if e.kind in (Kind.TEMP, Kind.UNRECOGNISED))
        if stubborn:
            lines.append(f"  ... and {len(hidden)} more, {stubborn} of which "
                         "carry no job id, so `cancel` cannot reach them")
        else:
            lines.append(f"  ... and {len(hidden)} more, all ordinary job files "
                         "that `cancel -a -x` clears")
    return lines


def _identified_note(identified: list[Entry], purge_failed: frozenset[str]) -> list[str]:
    if not identified:
        return []
    lines = [
        "",
        f"  {len(identified)} of these carry a print-data signature",
        "  (PostScript, PDF, PJL, PCL or raster), so they ARE document",
        "  content by evidence, not a maybe. They carry no job id, so",
        "  they cannot be targeted by job number, so `cancel` cannot",
        "  reach them.",
    ]
    # A run with purge failures does not get to promise what --purge does.
    if not purge_failed:
        lines.append("  --purge removes exactly these files and nothing else.")
    in_temp = sum(1 for e in identified if e.in_temp)
    if in_temp:
        lines.append(f"  {in_temp} of them are in the CUPS TempDir, mid-filter.")
    return lines


def _unidentified_note(unidentified: list[Entry]) -> list[str]:
    if not unidentified:
        return []
    lines = [
        "",
        f"  {len(unidentified)} of these could not be identified at all,",
        "  so they are not ruled out. Look at them before you",
        "  decide anything; this tool will not decide for you.",
    ]
    in_temp = sum(1 for e in unidentified if e.in_temp)
    if in_temp:
        lines.append(f"  {in_temp} of them are in the CUPS TempDir.")
    return lines


def _artifacts_section(audit: Audit) -> list[str]:
    """Artifacts, split by what they actually are.

    Filing a stray zero-length file under "CUPS RUNTIME FILES" would train the
    operator to skim the one section a real stray could hide in, and dropping
    it would make it invisible. Only a TempDir file is a runtime file: CUPS
    never creates cups-* files at the top level, the same fact
    is_harmless_temp gates the name on.
    """
    runtime = [e for e in audit.artifacts
               if e.in_temp and ARTIFACT.match(e.name.rsplit("/", 1)[-1])]
    other = [e for e in audit.artifacts if e not in runtime]
    lines: list[str] = []
    if runtime:
        lines += ["", f"CUPS RUNTIME FILES (not document content, leave them alone): {len(runtime)}",
                  *_names(runtime)]
    if other:
        lines += ["", f"OTHER FILES, no print-data signature: {len(other)}", *_names(other),
                  "  Empty, or a recognised driver cache. Listed so nothing is hidden."]
    return lines


def _names(entries: list[Entry]) -> list[str]:
    return [f"  {safe_name(e.name)}" for e in entries]


def _unexamined_section(audit: Audit) -> list[str]:
    if not audit.unexamined:
        return []
    return ["", f"COULD NOT EXAMINE: {len(audit.unexamined)}",
            *[f"  {u}" for u in audit.unexamined],
            "  Anything in there is unaccounted for. This is NOT a clean result."]


def _control_section(audit: Audit) -> list[str]:
    if not audit.uncounted_control:
        return []
    return ["",
            f"NOT COUNTED: {audit.uncounted_control} control file(s). They carry the job",
            "  title and submitting user, which can be disclosure by itself.",
            "  Use --include-control to count them."]


def _retention_lines(retention: bool | None) -> list[str]:
    if retention is True:
        return ["RETENTION: ON. CUPS is keeping documents. Set",
                "  'PreserveJobFiles No' in cupsd.conf and restart cups."]
    if retention is False:
        return ["RETENTION: OFF (PreserveJobFiles No)."]
    return ["RETENTION: unknown (could not read cupsd.conf)."]


def _verdict(audit: Audit, purge_failed: frozenset[str], undestroyed: int) -> str:
    if audit.unexamined:
        return (f"VERDICT: INCOMPLETE. {audit.total} retained file(s) found, but "
                f"{len(audit.unexamined)} area(s) could not be examined.")
    if audit.verdict is Verdict.RETAINED:
        return (f"VERDICT: {audit.total} retained file(s) still on disk. "
                f"{_retained_advice(audit, purge_failed)}")
    if undestroyed:
        # The spool IS empty and the documents are NOT gone: --purge unlinked
        # the spool's entry while other hard links to the same content
        # survive. audit.total is correctly zero, so "clean" read off it would
        # be a claim about the DOCUMENTS that nothing measured. The verdict is
        # the line read last and believed, so it names the condition and
        # points at the detail line.
        noun = "document" if undestroyed == 1 else "documents"
        return (f"VERDICT: spool is EMPTY but {undestroyed} {noun} NOT destroyed; "
                'see the "removed but NOT destroyed" line(s) above.')
    return "VERDICT: spool is clean."


def _retained_advice(audit: Audit, purge_failed: frozenset[str]) -> str:
    """Name the remover that fits what remains, and never one that just failed.

    `cancel -a -x` clears job files and nothing else; --purge clears the
    content-proven residue; nothing clears the unidentified. Advising --purge
    for an entry it just failed on would name a command whose failure is
    printed a few lines up.
    """
    everything = audit.targeted + audit.others
    stuck = [e for e in everything if e.kind is Kind.TEMP and e.name in purge_failed]
    how = _removers(everything, purge_failed)
    if how:
        advice = "Clear them with: " + ", then ".join(how)
        if stuck:
            advice += " (--purge already FAILED on the rest; see the NOT removed line(s) above)"
        return advice
    if stuck:
        return "--purge just FAILED on what remains; see the NOT removed line(s) above."
    # Only unidentified files remain, and nothing removes what could not be
    # identified.
    return "Nothing removes what could not be identified; look at the listing above."


def _removers(everything: tuple[Entry, ...], purge_failed: frozenset[str]) -> list[str]:
    """The commands that fit what remains, in the order to run them."""
    how = []
    if any(e.job is not None for e in everything):
        how.append("cancel -a -x")
    if any(e.kind is Kind.TEMP and e.name not in purge_failed for e in everything):
        how.append("--purge")
    return how
