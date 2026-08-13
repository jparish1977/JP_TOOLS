#!/usr/bin/env python
"""
JP_TOOLS/tests/test_spool_audit_invariant.py
One property, checked across every path spool-audit.py can take.

    EXIT 0 MUST MEAN: no file this tool would call print data is still there.

That is the only promise the tool makes that matters. `spool-audit.py 85
--purge && echo SAFE` firing while a readable copy of job 85's document sits on
disk is the failure; everything else is detail.

WHY THIS EXISTS
    That property broke in review rounds 4, 7, 8 and 9, in a different place
    each time: a scoped purge that could not target unattributable files, a
    "nothing to purge" branch missing its twin's caveat, and then the caveat
    added as a MESSAGE without the exit code. Each fix was verified by reading
    the output it had just produced, so each one passed while the property
    stayed broken.

    Per-branch tests could not catch it because the bug was never in a branch,
    it was in the relationship between what the tool says and what it returns.
    This drives the real CLI as a subprocess and judges it with an oracle that
    does not share its code.

THE ORACLE
    Deliberately dumber than the tool and independent of it: a regular file is
    print data if its first bytes carry a print-format magic number, or if its
    name matches d<job>-<doc> at the top level. If the tool exits 0 while the
    oracle can still find one, the tool is wrong -- not the oracle.

    Being dumber is the point. Sharing the tool's classifier would make this
    test agree with the tool by construction, which is how the previous checks
    came to pass over real breakage.

    python tests/test_spool_audit_invariant.py
"""

import os
import pathlib
import re
import subprocess
import sys
import tempfile

TOOL = pathlib.Path(__file__).resolve().parent.parent / "spool-audit.py"

# Independent of the tool's DOCUMENT_MAGIC on purpose.
MAGIC = (b"%!PS", b"%PDF", b"\x1b%-12345X", b"@PJL", b"\x1b*")
DOCNAME = re.compile(r"^d\d+-\d+$")

FAILURES: list[str] = []


def oracle(spool: pathlib.Path) -> list[str]:
    """Every path under `spool` that a careful human would call print data."""
    hits = []
    for p in sorted(spool.rglob("*")):
        if p.is_symlink() or not p.is_file():
            continue
        rel = str(p.relative_to(spool))
        try:
            head = p.open("rb").read(16)
        except OSError:
            # Unreadable: cannot rule it out, so it counts. The tool must not
            # claim success over something neither of us could read.
            hits.append(rel)
            continue
        if any(head.startswith(m) for m in MAGIC) or DOCNAME.match(p.name):
            hits.append(rel)
    return hits


def run(spool: pathlib.Path, *args: str) -> tuple[int, str]:
    proc = subprocess.run(
        [sys.executable, str(TOOL), "--spool", str(spool), "--conf", "/dev/null", *args],
        capture_output=True, text=True, errors="surrogateescape", check=False,
    )
    return proc.returncode, proc.stdout + proc.stderr


def assert_invariant(label: str, spool: pathlib.Path, *args: str, expect: int) -> None:
    """Check the property in BOTH directions, and pin the exit code.

    The first version returned early whenever the exit code was non-zero, so
    only fixtures that happened to reach 0 asserted anything: 12 of 17 were
    vacuous while the suite printed "holds across every fixture". A regression
    that changed a fixture's exit code for an unrelated reason silently
    disarmed its check -- the same "passed by not running" failure this file
    exists to prevent, in the file that exists to prevent it.

    So: `expect` pins the exit code, and the contrapositive is asserted too.
    Every fixture now fails if the tool drifts, whichever way it drifts.
    """
    code, out = run(spool, *args)
    left = oracle(spool)

    if code != expect:
        FAILURES.append(
            f"{label}: exit {code}, expected {expect}\n    output: {out.strip()[:300]}"
        )
    if code == 0 and left:
        FAILURES.append(
            f"{label}: exited 0 with print data still present: {left}\n"
            f"    output: {out.strip()[:300]}"
        )
    # No trailing branches: the two checks above are the whole assertion. The
    # earlier version had four if/return no-ops here that read as coverage and
    # were not -- in the file whose subject is checks that pass by not running.


def build(tmp: pathlib.Path, name: str) -> pathlib.Path:
    d = tmp / name
    (d / "tmp").mkdir(parents=True)
    return d


def main() -> int:
    if not TOOL.exists():
        print(f"SKIP: {TOOL} not found")
        return 0

    with tempfile.TemporaryDirectory() as raw:
        tmp = pathlib.Path(raw)

        # 1. Empty spool: nothing to find, exit 0 is honest.
        assert_invariant("empty", build(tmp, "empty"), expect=0)

        # 2. A document and a copy of it under an unrecognised name. The copy
        #    carries no job id, so a scoped purge cannot target it -- and must
        #    therefore not exit 0.
        s = build(tmp, "scoped")
        (s / "d00085-001").write_bytes(b"%PDF-1.7\n")
        (s / "d00085-001.bak").write_bytes(b"%PDF-1.7\n")
        assert_invariant("scoped report", s, "85", expect=1)
        assert_invariant("scoped purge", s, "85", "--purge", expect=1)

        # 3. Only the unattributable copy remains.
        s = build(tmp, "bak-only")
        (s / "d00085-001.bak").write_bytes(b"%PDF-1.7\n")
        assert_invariant("bak only, scoped purge", s, "85", "--purge", expect=1)
        assert_invariant("bak only, report", s, "85", expect=1)

        # 3b. A SECOND, untargeted job. Every scoped fixture above pairs job 85
        #     with an unattributable leftover, which the job-is-None caveat
        #     happens to catch. A document belonging to job 77 is attributable
        #     -- just not to the job asked about -- and slipped through both
        #     purge branches. The oracle flags it by name, so only the fixture
        #     was missing.
        s = build(tmp, "other-job")
        (s / "d00085-001").write_bytes(b"%PDF-1.7\n")
        (s / "d00077-001").write_bytes(b"%PDF-1.7\n")
        assert_invariant("other job, scoped purge", s, "85", "--purge", expect=1)
        assert_invariant("other job, report", s, "85", expect=1)

        s = build(tmp, "other-job-only")
        (s / "d00077-001").write_bytes(b"%PDF-1.7\n")
        assert_invariant("other job only, scoped purge", s, "85", "--purge", expect=1)

        # 4. Document inside TempDir.
        s = build(tmp, "intmp")
        (s / "tmp" / "filter.ps").write_bytes(b"%!PS-Adobe-3.0\n")
        assert_invariant("tempdir report", s, expect=1)
        assert_invariant("tempdir purge", s, "--purge", expect=0)

        # 5. Nested under a cache directory, where the location rule applies.
        s = build(tmp, "cache")
        (s / "tmp" / ".cache").mkdir()
        (s / "tmp" / ".cache" / "leak.ps").write_bytes(b"%!PS-Adobe-3.0\n")
        assert_invariant("cache report", s, expect=1)
        assert_invariant("cache purge", s, "--purge", expect=0)

        # 6. Unreadable file: neither side can rule it out. Skipped as root,
        #    where chmod 000 does not stop a read -- the fixture would silently
        #    degrade into "a readable %!PS file" and test a different path.
        #    This tool's documented invocation is `sudo spool-audit.py`, and CI
        #    containers commonly run as root, so this is not hypothetical.
        if getattr(os, "geteuid", lambda: 1)() != 0:
            s = build(tmp, "unreadable")
            p = s / "tmp" / "secret.ps"
            p.write_bytes(b"%!PS\n")
            os.chmod(p, 0o000)
            assert_invariant("unreadable report", s, expect=1)
            os.chmod(p, 0o644)
        else:
            print("  note: running as root, unreadable-file fixture skipped")

        # 6b. A file that cannot be identified must never be deleted by a
        #     blanket --purge. Over-report is right for a report and wrong for
        #     a delete set; one predicate served both, and --purge destroyed a
        #     plain-text README while printing SCOPE CLEAN.
        #     Both locations, because the first version of this fixture only
        #     planted a file at the TOP level -- and the fix had the same blind
        #     spot, so tmp/ went on deleting blind while the test passed. That
        #     is three fixtures in a row inheriting the bug's own blind spot.
        s = build(tmp, "unrecognised")
        (s / "README-do-not-delete").write_bytes(b"do not delete me\n")
        (s / "tmp" / "NOTES-do-not-delete").write_bytes(b"nor me\n")
        (s / "tmp" / "genuine.ps").write_bytes(b"%!PS-Adobe-3.0\n")
        assert_invariant("unrecognised is not deleted", s, "--purge", expect=1)
        for keep in ("README-do-not-delete", "tmp/NOTES-do-not-delete"):
            if not (s / keep).exists():
                FAILURES.append(f"unrecognised: --purge deleted {keep}, which it could not identify")
        # ...while a file that IS identifiable as print data must still go.
        if (s / "tmp" / "genuine.ps").exists():
            FAILURES.append("unrecognised: --purge spared tmp/genuine.ps, which carries print magic")

        # 7. A symlink named like a document. Unlinking it destroys nothing.
        # The vault lives OUTSIDE the spool deliberately. As a subdirectory it
        # produced the expected exit 2 on its own -- "vault/ (subdirectory, not
        # examined)" -- so the fixture pinned nothing about symlinks: any drift
        # in the symlink handling still left an unexamined area, still exited 2,
        # and still passed. Now the 2 can only come from the symlink itself.
        # Verified by mutation: disabling the top-level symlink check makes this
        # fixture fail with "exit 1, expected 2", where before it passed.
        #
        # The exit code is the load-bearing assertion here, not the exists()
        # check below. unlink() does not follow symlinks, so the target survives
        # even when every guard is removed -- three of them, as it turns out:
        # the symlink note, the not-regular note, and delete()'s containment,
        # which refuses on the resolved path. The exists() check is kept for the
        # change that would defeat all three, a purge that resolves before it
        # deletes, and it is cheap insurance against exactly that.
        vault = tmp / "symlink-vault"
        vault.mkdir()
        (vault / "real.ps").write_bytes(b"%!PS\n")
        s = build(tmp, "symlink")
        os.symlink(vault / "real.ps", s / "d00085-001")
        assert_invariant("symlink purge", s, "--purge", expect=2)
        if not (vault / "real.ps").exists():
            FAILURES.append(
                "symlink: --purge destroyed the symlink's target, "
                "the false assurance of destruction this file exists to catch"
            )

        # 8. Full purge with no job ids: the one case that should reach 0.
        s = build(tmp, "full")
        (s / "d00085-001").write_bytes(b"%PDF-1.7\n")
        (s / "tmp" / "f.ps").write_bytes(b"%!PS\n")
        assert_invariant("full purge", s, "--purge", expect=0)

        # 9. Control files only: no document content anywhere.
        s = build(tmp, "control")
        (s / "c00085").write_bytes(b"job-name gpg-key\n")
        assert_invariant("control only", s, expect=0)
        # Control files are opt-in by design, so the report calls this spool clean.
        # The purge path must agree: making a caveat string force exit 1 had the
        # two paths contradict each other on the same directory.
        assert_invariant("control only purge", s, "--purge", expect=0)

    if FAILURES:
        print(f"INVARIANT VIOLATED ({len(FAILURES)})")
        for f in FAILURES:
            print(f"  {f}")
        return 1
    print("exit-0 invariant holds across every fixture")
    return 0


if __name__ == "__main__":
    sys.exit(main())
