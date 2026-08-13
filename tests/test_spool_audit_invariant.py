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


def assert_invariant(label: str, spool: pathlib.Path, *args: str) -> None:
    code, out = run(spool, *args)
    if code != 0:
        return  # only exit 0 makes the promise
    left = oracle(spool)
    if left:
        FAILURES.append(
            f"{label}: exited 0 with print data still present: {left}\n"
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
        assert_invariant("empty", build(tmp, "empty"))

        # 2. A document and a copy of it under an unrecognised name. The copy
        #    carries no job id, so a scoped purge cannot target it -- and must
        #    therefore not exit 0.
        s = build(tmp, "scoped")
        (s / "d00085-001").write_bytes(b"%PDF-1.7\n")
        (s / "d00085-001.bak").write_bytes(b"%PDF-1.7\n")
        assert_invariant("scoped report", s, "85")
        assert_invariant("scoped purge", s, "85", "--purge")

        # 3. Only the unattributable copy remains.
        s = build(tmp, "bak-only")
        (s / "d00085-001.bak").write_bytes(b"%PDF-1.7\n")
        assert_invariant("bak only, scoped purge", s, "85", "--purge")
        assert_invariant("bak only, report", s, "85")

        # 3b. A SECOND, untargeted job. Every scoped fixture above pairs job 85
        #     with an unattributable leftover, which the job-is-None caveat
        #     happens to catch. A document belonging to job 77 is attributable
        #     -- just not to the job asked about -- and slipped through both
        #     purge branches. The oracle flags it by name, so only the fixture
        #     was missing.
        s = build(tmp, "other-job")
        (s / "d00085-001").write_bytes(b"%PDF-1.7\n")
        (s / "d00077-001").write_bytes(b"%PDF-1.7\n")
        assert_invariant("other job, scoped purge", s, "85", "--purge")
        assert_invariant("other job, report", s, "85")

        s = build(tmp, "other-job-only")
        (s / "d00077-001").write_bytes(b"%PDF-1.7\n")
        assert_invariant("other job only, scoped purge", s, "85", "--purge")

        # 4. Document inside TempDir.
        s = build(tmp, "intmp")
        (s / "tmp" / "filter.ps").write_bytes(b"%!PS-Adobe-3.0\n")
        assert_invariant("tempdir report", s)
        assert_invariant("tempdir purge", s, "--purge")

        # 5. Nested under a cache directory, where the location rule applies.
        s = build(tmp, "cache")
        (s / "tmp" / ".cache").mkdir()
        (s / "tmp" / ".cache" / "leak.ps").write_bytes(b"%!PS-Adobe-3.0\n")
        assert_invariant("cache report", s)
        assert_invariant("cache purge", s, "--purge")

        # 6. Unreadable file: neither side can rule it out.
        s = build(tmp, "unreadable")
        p = s / "tmp" / "secret.ps"
        p.write_bytes(b"%!PS\n")
        os.chmod(p, 0o000)
        assert_invariant("unreadable report", s)
        os.chmod(p, 0o644)

        # 7. A symlink named like a document. Unlinking it destroys nothing.
        s = build(tmp, "symlink")
        (s / "vault").mkdir()
        (s / "vault" / "real.ps").write_bytes(b"%!PS\n")
        os.symlink("vault/real.ps", s / "d00085-001")
        assert_invariant("symlink purge", s, "--purge")

        # 8. Full purge with no job ids: the one case that should reach 0.
        s = build(tmp, "full")
        (s / "d00085-001").write_bytes(b"%PDF-1.7\n")
        (s / "tmp" / "f.ps").write_bytes(b"%!PS\n")
        assert_invariant("full purge", s, "--purge")

        # 9. Control files only: no document content anywhere.
        s = build(tmp, "control")
        (s / "c00085").write_bytes(b"job-name gpg-key\n")
        assert_invariant("control only", s)
        assert_invariant("control only purge", s, "--purge")

    if FAILURES:
        print(f"INVARIANT VIOLATED ({len(FAILURES)})")
        for f in FAILURES:
            print(f"  {f}")
        return 1
    print("exit-0 invariant holds across every fixture")
    return 0


if __name__ == "__main__":
    sys.exit(main())
