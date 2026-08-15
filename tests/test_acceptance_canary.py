#!/usr/bin/env python
"""
JP_TOOLS/tests/test_acceptance_canary.py
Run the acceptance suite against tools that do nothing, and find the checks
that pass anyway.

    python tests/test_acceptance_canary.py [path/to/acceptance-suite.sh]

WHY
    A check is only evidence if something could make it fail. On 2026-08-14
    --purge and --fix were deleted from spool-audit.py and
    tests/test_spool_audit_acceptance.sh still reported "13 passed, 0 failed":
    argparse rejected the unknown flags, exited 2 having done nothing, and the
    two checks of the form "the file was not deleted" held over a tool that
    never ran.

    Per-check fixes -- assert the status, name the flag -- close the holes
    they were written for. This closes the class: a check no stub can break is
    vacuous by construction, whether or not anyone has thought of the reason.

THE RULE, AND WHY IT IS NOT "THE SUITE MUST FAIL"
    Suggested by thinkpad-session over the fleet mailbox as "swap the tool for
    a stub, assert the SUITE fails", with the claim that it was the one check
    that catches a bug nobody knows about yet. Tested before adopting, and
    that version does not work: the pre-cut suite fails against all four stubs
    below, because its other checks are real. It would have reported a clean
    bill of health on the exact day two of its checks were vacuous.

    Measured against the pre-cut suite, stub `silent-failure` (exit 2, the
    argparse case): 7 checks FAILED and 6 still printed PASS, among them
    "symlink target survives purge" and "symlinked TempDir root not followed"
    -- the two that were actually broken.

    So the assertion is per CHECK, not per suite: every check must fail
    against AT LEAST ONE stub. That distinguishes the two genuinely vacuous
    checks from ones that merely agree with a given stub by coincidence --
    "unreadable TempDir exits 2" passes against the stub that exits 2 and
    fails against the one that exits 0, which is a real check meeting a
    coincidence, not a hole.

THE STUBS
      silent-success   exit 0, no output   -- the classic vacuous pass
      silent-failure   exit 2, no output   -- what argparse does with a bad flag
      liar             exit 0, "spool is clean" over anything at all
      wrong-status     exit 1 always       -- status pinned but never earned
      vandal           deletes what it is pointed at, and truncates the target
                       of any symlink it finds, then exits 0 -- so that "the
                       spool was not modified" is a claim a stub can refute
"""

import pathlib
import re
import shutil
import subprocess
import sys
import tempfile

REPO = pathlib.Path(__file__).resolve().parent.parent
DEFAULT_SUITE = REPO / "tests" / "test_spool_audit_acceptance.sh"

STUBS = {
    "silent-success": "import sys\nsys.exit(0)\n",
    "silent-failure": "import sys\nsys.exit(2)\n",
    "liar": 'import sys\nprint("VERDICT: spool is clean.")\nsys.exit(0)\n',
    "wrong-status": "import sys\nsys.exit(1)\n",
    # A stub that DOES something, so that "nothing was touched" becomes an
    # assertion a stub can break. Without this the no-write checks added with
    # the 2026-08-14 cut pass against every stub -- correctly flagged as
    # unbreakable by this canary on the day they were written, because no
    # other stub here can write either.
    "vandal": (
        "import os, sys\n"
        "spool = sys.argv[sys.argv.index('--spool') + 1] "
        "if '--spool' in sys.argv else '.'\n"
        # followlinks=True on purpose: a symlinked TempDir pointing outside
        # the spool is exactly the escape the suite checks for, and a vandal
        # that stops at the symlink cannot refute "the file outside the spool
        # is untouched" -- which left that check unbreakable and flagged.
        "for root, dirs, files in os.walk(spool, followlinks=True):\n"
        "    for name in files:\n"
        "        p = os.path.join(root, name)\n"
        "        try:\n"
        "            if os.path.islink(p):\n"
        "                open(os.path.realpath(p), 'w').close()\n"
        "            else:\n"
        "                os.unlink(p)\n"
        "        except OSError:\n"
        "            pass\n"
        "sys.exit(0)\n"
    ),
}

# `no` prints the label with the reason appended; `ok` prints it bare. Strip
# the reason so one check has one name across stubs.
REASON = re.compile(r" \((?:got |output lacked |THE TOOL NEVER RAN).*\)$")

FAILURES: list[str] = []


def run_against_stub(suite: pathlib.Path, source: str) -> dict[str, str]:
    """Run the suite with a stub in place of the tool. Returns label -> result."""
    with tempfile.TemporaryDirectory() as raw:
        fake_repo = pathlib.Path(raw)
        (fake_repo / "tests").mkdir()
        # The suite resolves its own repo from BASH_SOURCE, so a copy placed
        # here finds the stub instead of the real tool. That is the whole
        # trick, and it is why the suite must never hardcode a tool path.
        target = fake_repo / "tests" / suite.name
        shutil.copy(suite, target)
        (fake_repo / "spool-audit.py").write_text(source, encoding="utf-8")

        proc = subprocess.run(
            ["bash", str(target)],
            capture_output=True, text=True, check=False,
            env={"PATH": "/usr/bin:/bin", "PYTHON": sys.executable,
                 "HOME": str(fake_repo)},
        )
    results = {}
    for line in proc.stdout.splitlines():
        text = line.strip()
        for marker in ("PASS", "FAIL", "SKIP"):
            if text.startswith(marker + "  "):
                label = REASON.sub("", text[len(marker) + 2:]).strip()
                results[label] = marker
                break
    return results


def main(argv: list[str] | None = None) -> int:
    args = sys.argv[1:] if argv is None else argv
    suite = pathlib.Path(args[0]).resolve() if args else DEFAULT_SUITE
    if not suite.exists():
        print(f"SKIP: {suite} not found")
        return 0
    if not shutil.which("bash"):
        print("SKIP: bash not available")
        return 0

    print(f"Canary run against {suite.name}:")
    per_stub = {name: run_against_stub(suite, src) for name, src in STUBS.items()}
    for name, results in per_stub.items():
        broke = sum(1 for v in results.values() if v == "FAIL")
        print(f"  {name}: {broke} of {len(results)} checks failed")

    every_label = sorted({lbl for r in per_stub.values() for lbl in r})
    if not every_label:
        FAILURES.append(
            "the suite reported no checks at all against any stub, so this "
            "canary compared nothing -- which is the failure it exists to catch"
        )

    for label in every_label:
        outcomes = {name: r.get(label, "absent") for name, r in per_stub.items()}
        # SKIP is not a pass and not a failure; a check skipped under every
        # stub simply was not exercised here.
        if all(v in ("PASS", "absent") for v in outcomes.values()):
            if any(v == "PASS" for v in outcomes.values()):
                FAILURES.append(
                    f"'{label}' passes against every stub, so nothing this "
                    "canary can do makes it fail. It is satisfied by a tool "
                    "that does not run."
                )

    if FAILURES:
        print(f"VACUOUS CHECKS ({len(FAILURES)})")
        for f in FAILURES:
            print(f"  {f}")
        return 1
    print("every check in the suite is broken by at least one stub")
    return 0


if __name__ == "__main__":
    sys.exit(main())
