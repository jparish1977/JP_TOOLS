#!/usr/bin/env python3
"""
JP_TOOLS/tests/test_acceptance_canary.py
Run the acceptance suite against tools that do nothing, and find the checks
that pass anyway.

    python tests/test_acceptance_canary.py [path/to/acceptance-suite.sh]

WHY
    A check is only evidence if something could make it fail. A check of the
    form "the file was not deleted" holds over a tool that never ran: remove
    a flag, argparse exits 2 having done nothing, and the check still passes.
    Per-check fixes (assert the status, name the flag) close the holes they
    were written for. This closes the class: a check no stub can break is
    vacuous by construction, whether or not anyone has thought of the reason.

THE RULE IS PER CHECK, NOT "THE SUITE MUST FAIL"
    Against a stub, a suite fails as soon as any one of its real checks does,
    so "the suite fails against a stub" holds while individual checks are
    vacuous. Every CHECK must fail against AT LEAST ONE stub. That separates a
    vacuous check from one that merely agrees with a given stub by
    coincidence: "unreadable TempDir exits 2" passes against the stub that
    exits 2 and fails against the one that exits 0, a real check meeting a
    coincidence, not a hole.

THE STUBS
      silent-success   exit 0, no output: the classic vacuous pass
      silent-failure   exit 2, no output: what argparse does with a bad flag
      liar             exit 0, "spool is clean" over anything at all
      wrong-status     exit 1 always: status pinned but never earned
      vandal           deletes what it is pointed at, and truncates the target
                       of any symlink it finds, then exits 0, so that "the
                       spool was not modified" is a claim a stub can refute
"""

import pathlib
import re
import shutil
import subprocess
import sys
import tempfile

from _harness import ROOT as REPO

DEFAULT_SUITE = REPO / "tests" / "test_spool_audit_acceptance.sh"

STUBS = {
    "silent-success": "import sys\nsys.exit(0)\n",
    "silent-failure": "import sys\nsys.exit(2)\n",
    "liar": 'import sys\nprint("VERDICT: spool is clean.")\nsys.exit(0)\n',
    "wrong-status": "import sys\nsys.exit(1)\n",
    # A stub that DOES something, so that "nothing was touched" is an
    # assertion a stub can break; no other stub here writes.
    "vandal": (
        "import os, sys\n"
        "spool = sys.argv[sys.argv.index('--spool') + 1] "
        "if '--spool' in sys.argv else '.'\n"
        # followlinks=True on purpose: a symlinked TempDir pointing outside
        # the spool is exactly the escape the suite checks for, and a vandal
        # that stopped at the symlink could not refute "the file outside the
        # spool is untouched".
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
MARKERS = ("PASS", "FAIL", "SKIP")


def parse_results(stdout: str) -> dict[str, str]:
    """The suite's output as {check label: PASS, FAIL or SKIP}."""
    results = {}
    for line in stdout.splitlines():
        text = line.strip()
        marker = next((m for m in MARKERS if text.startswith(m + "  ")), None)
        if marker:
            results[REASON.sub("", text[len(marker) + 2:]).strip()] = marker
    return results


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
            ["bash", str(target)], capture_output=True, text=True, check=False,
            env={"PATH": "/usr/bin:/bin", "PYTHON": sys.executable, "HOME": str(fake_repo)},
        )
    return parse_results(proc.stdout)


def vacuous(per_stub: dict[str, dict[str, str]]) -> list[str]:
    """Why the canary fails: each check no stub breaks, or no checks at all.

    SKIP is neither a pass nor a failure; a check skipped under every stub
    was not exercised here.
    """
    labels = sorted({label for results in per_stub.values() for label in results})
    if not labels:
        return [("the suite reported no checks at all against any stub, so this "
                 "canary compared nothing, which is the failure it exists to catch")]
    problems = []
    for label in labels:
        outcomes = [results.get(label, "absent") for results in per_stub.values()]
        if "PASS" in outcomes and all(v in ("PASS", "absent") for v in outcomes):
            problems.append(f"'{label}' passes against every stub, so nothing this canary "
                            "can do makes it fail. It is satisfied by a tool that does not run.")
    return problems


def _skip(suite: pathlib.Path) -> str | None:
    if not suite.exists():
        return f"SKIP: {suite} not found"
    return None if shutil.which("bash") else "SKIP: bash not available"


def main(argv: list[str] | None = None) -> int:
    args = sys.argv[1:] if argv is None else argv
    suite = pathlib.Path(args[0]).resolve() if args else DEFAULT_SUITE
    skip = _skip(suite)
    if skip:
        print(skip)
        return 0
    print(f"Canary run against {suite.name}:")
    per_stub = {name: run_against_stub(suite, src) for name, src in STUBS.items()}
    for name, results in per_stub.items():
        broke = sum(1 for v in results.values() if v == "FAIL")
        print(f"  {name}: {broke} of {len(results)} checks failed")
    problems = vacuous(per_stub)
    if problems:
        print(f"VACUOUS CHECKS ({len(problems)})")
        for p in problems:
            print(f"FAIL {p}")
        return 1
    print("every check in the suite is broken by at least one stub")
    return 0


if __name__ == "__main__":
    sys.exit(main())
