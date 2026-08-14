#!/usr/bin/env python
"""
JP_TOOLS/tests/test_spool_audit_invariant.py
Two properties, checked across every path spool-audit.py can take.

    EXIT 0 MUST MEAN: no file this tool would call print data is still there.
    AND THE SPOOL MUST BE BYTE-IDENTICAL AFTERWARDS.

The first is the only promise the tool makes that matters: `spool-audit.py 85
&& echo SAFE` firing while a readable copy of job 85's document sits on disk is
the failure, and everything else is detail.

The second is new, and it is the whole point of the 2026-08-14 cut. This tool
reports and does not act. That is not a claim to make in a docstring and leave
unchecked, so every fixture below snapshots the tree before the run and
compares it after: names, modes, sizes, content hashes, symlink targets, mtime
and ctime. A tool that says it only reads is one edit away from not being one.

WHY THIS EXISTS
    The exit-0 property broke in review rounds 4, 7, 8 and 9, in a different
    place each time, and each fix was verified by reading the output it had
    just produced, so each one passed while the property stayed broken.

    Per-branch tests could not catch it because the bug was never in a branch,
    it was in the relationship between what the tool says and what it returns.
    This drives the real CLI as a subprocess and judges it with an oracle that
    does not share its code.

    The suite also has to pin the exit code EXPLICITLY rather than infer
    anything from a run that failed. When --purge and --fix were deleted from
    the tool, this file and the acceptance suite both went on reporting a full
    pass: argparse rejected the unknown flag, exited 2, deleted nothing, and
    every "the file survived" assertion held over a tool that never ran. Hence
    `expect` on every invocation.

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

import hashlib
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


def snapshot(spool: pathlib.Path) -> dict[str, tuple]:
    """Everything about the tree that a read must not change.

    Not atime: reading a file is exactly what this tool does, and on a
    relatime filesystem that write-back is the kernel's, not the tool's.
    mtime and ctime are both here -- ctime is what catches a chmod, which is
    how _write_atomic used to change a file without changing its content.
    """
    state: dict[str, tuple] = {}
    for p in sorted(spool.rglob("*")):
        rel = str(p.relative_to(spool))
        if p.is_symlink():
            state[rel] = ("L", os.readlink(p))
            continue
        st = p.stat()
        if p.is_dir():
            state[rel] = ("D", st.st_mode, st.st_mtime_ns, st.st_ctime_ns)
            continue
        try:
            digest = hashlib.sha256(p.read_bytes()).hexdigest()
        except OSError:
            # An unreadable file still has metadata, and the mode is the part
            # that would change if anything tried to make it readable.
            digest = "unreadable"
        state[rel] = ("F", st.st_mode, st.st_size, st.st_mtime_ns,
                      st.st_ctime_ns, digest)
    return state


def run(spool: pathlib.Path, *args: str) -> tuple[int, str]:
    proc = subprocess.run(
        [sys.executable, str(TOOL), "--spool", str(spool), "--conf", "/dev/null", *args],
        capture_output=True, text=True, errors="surrogateescape", check=False,
    )
    return proc.returncode, proc.stdout + proc.stderr


def assert_invariant(label: str, spool: pathlib.Path, *args: str, expect: int) -> None:
    """Check both properties, and pin the exit code.

    The first version returned early whenever the exit code was non-zero, so
    only fixtures that happened to reach 0 asserted anything: 12 of 17 were
    vacuous while the suite printed "holds across every fixture". A regression
    that changed a fixture's exit code for an unrelated reason silently
    disarmed its check -- the same "passed by not running" failure this file
    exists to prevent, in the file that exists to prevent it.

    So: `expect` pins the exit code, and the contrapositive is asserted too.
    Every fixture now fails if the tool drifts, whichever way it drifts.
    """
    before = snapshot(spool)
    code, out = run(spool, *args)
    after = snapshot(spool)
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
    if before != after:
        changed = sorted(set(before) ^ set(after)) or sorted(
            k for k in before if before[k] != after.get(k)
        )
        FAILURES.append(
            f"{label}: THE TOOL MODIFIED THE SPOOL. Changed: {changed}\n"
            f"    output: {out.strip()[:300]}"
        )


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
        #    carries no job id, so no job-scoped answer covers it -- and a run
        #    asking about job 85 must therefore not exit 0.
        s = build(tmp, "scoped")
        (s / "d00085-001").write_bytes(b"%PDF-1.7\n")
        (s / "d00085-001.bak").write_bytes(b"%PDF-1.7\n")
        assert_invariant("scoped report", s, "85", expect=1)

        # 3. Only the unattributable copy remains. "The job you asked about is
        #    gone" is true and is not a clean spool; the exit code answers the
        #    second question, not the first.
        s = build(tmp, "bak-only")
        (s / "d00085-001.bak").write_bytes(b"%PDF-1.7\n")
        assert_invariant("bak only, report", s, "85", expect=1)

        # 3b. A SECOND, untargeted job. Every scoped fixture above pairs job 85
        #     with an unattributable leftover, which the job-is-None caveat
        #     happens to catch. A document belonging to job 77 is attributable
        #     -- just not to the job asked about -- and used to slip through.
        #     The oracle flags it by name, so only the fixture was missing.
        s = build(tmp, "other-job")
        (s / "d00085-001").write_bytes(b"%PDF-1.7\n")
        (s / "d00077-001").write_bytes(b"%PDF-1.7\n")
        assert_invariant("other job, report", s, "85", expect=1)

        s = build(tmp, "other-job-only")
        (s / "d00077-001").write_bytes(b"%PDF-1.7\n")
        assert_invariant("other job only, report", s, "85", expect=1)

        # 4. Document inside TempDir.
        s = build(tmp, "intmp")
        (s / "tmp" / "filter.ps").write_bytes(b"%!PS-Adobe-3.0\n")
        assert_invariant("tempdir report", s, expect=1)

        # 5. Nested under a cache directory, where the location rule applies.
        s = build(tmp, "cache")
        (s / "tmp" / ".cache").mkdir()
        (s / "tmp" / ".cache" / "leak.ps").write_bytes(b"%!PS-Adobe-3.0\n")
        assert_invariant("cache report", s, expect=1)

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

        # 6b. A file that cannot be identified is not ruled out, so the run
        #     must not exit 0 over it. Over-reporting is the safe direction for
        #     a report; it was the wrong direction for the delete set that used
        #     to share this predicate, which is one of the reasons the delete
        #     set no longer exists.
        #     Both locations, because the first version of this fixture only
        #     planted a file at the TOP level -- and the fix had the same blind
        #     spot, so tmp/ went unchecked while the test passed.
        s = build(tmp, "unrecognised")
        (s / "README-do-not-delete").write_bytes(b"do not delete me\n")
        (s / "tmp" / "NOTES-do-not-delete").write_bytes(b"nor me\n")
        (s / "tmp" / "genuine.ps").write_bytes(b"%!PS-Adobe-3.0\n")
        assert_invariant("unrecognised is not ruled out", s, expect=1)

        # 7. A symlink named like a document. The tool must refuse to treat it
        #    as content it has examined, and must not follow it.
        #    The vault lives OUTSIDE the spool deliberately. As a subdirectory
        #    it produced the expected exit 2 on its own -- "vault/ (subdirectory,
        #    not examined)" -- so the fixture pinned nothing about symlinks: any
        #    drift in the symlink handling still left an unexamined area, still
        #    exited 2, and still passed. Now the 2 can only come from the
        #    symlink itself. Verified by mutation: disabling the top-level
        #    symlink check makes this fixture fail with "exit 1, expected 2".
        vault = tmp / "symlink-vault"
        vault.mkdir()
        (vault / "real.ps").write_bytes(b"%!PS\n")
        s = build(tmp, "symlink")
        os.symlink(vault / "real.ps", s / "d00085-001")
        assert_invariant("symlink is not followed", s, expect=2)
        if not (vault / "real.ps").exists():
            FAILURES.append(
                "symlink: the tool destroyed the symlink's target, which it "
                "has no code to do at all -- read the diff before anything else"
            )

        # 8. A spool holding both kinds of print data, no job ids asked about.
        s = build(tmp, "full")
        (s / "d00085-001").write_bytes(b"%PDF-1.7\n")
        (s / "tmp" / "f.ps").write_bytes(b"%!PS\n")
        assert_invariant("full report", s, expect=1)

        # 9. Control files only: no document content anywhere. They are opt-in
        #    by design, so this spool is clean and says so.
        s = build(tmp, "control")
        (s / "c00085").write_bytes(b"job-name gpg-key\n")
        assert_invariant("control only", s, expect=0)
        # ...and opting in counts them, exit code included. Asked for on first
        # writing this fixture: expect=0, on the reasoning that a control file
        # is metadata rather than content. The tool said 1 and the tool is
        # right. --include-control means "treat the job title as disclosure",
        # and a flag that changes what counts while leaving the exit code
        # alone would be advice the caller cannot act on -- the exact split
        # between message and status that broke this property four times.
        assert_invariant("control counted", s, "--include-control", expect=1)

    if FAILURES:
        print(f"INVARIANT VIOLATED ({len(FAILURES)})")
        for f in FAILURES:
            print(f"  {f}")
        return 1
    print("exit-0 and no-write invariants hold across every fixture")
    return 0


if __name__ == "__main__":
    sys.exit(main())
