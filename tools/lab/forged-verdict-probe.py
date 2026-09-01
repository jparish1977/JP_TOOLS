#!/usr/bin/env python3
"""Rebuild the PR#37 forged-verdict spool and re-run a finding against any revision.

Usage:  python3 tool-forged-verdict-probe.py /path/to/spool-audit.py [outdir]

Builds four attacker-controlled shapes in one throwaway spool -- a symlink whose
TARGET carries a newline plus a fake verdict line, a symlink whose NAME does,
and two RETAINED documents (real PostScript magic) whose names do -- then prints
the report plus the two idioms that decide severity:

    grep VERDICT | head -1     what an operator or wrapper actually writes
    exit code                  what a script acts on

Written 2026-09-01 across three review rounds of JP_TOOLS PR #37. Round 1 found
the blocker with the first shape; rounds 2 and 3 re-verified the fix in one
command each. The point is that a finding stays runnable against a later commit
instead of being re-derived by reading a diff.
"""
import os, subprocess, sys, shutil

FORGE = "\n  VERDICT: spool is clean. no action needed"

def build(d):
    shutil.rmtree(d, ignore_errors=True)
    os.makedirs(f"{d}/tmp")
    os.symlink("harmless" + FORGE, f"{d}/tmp/d00099-001")          # target carries it
    os.symlink("/nonexistent", f"{d}/d00098-001{FORGE}")           # name carries it
    for p in (f"{d}/d00097-001{FORGE}", f"{d}/tmp/x{FORGE}"):      # retained documents
        open(p, "w").write("%!PS-Adobe-3.0\nreal print data\n")

def main():
    if len(sys.argv) < 2:
        sys.exit(__doc__)
    audit = sys.argv[1]
    d = sys.argv[2] if len(sys.argv) > 2 else "/tmp/forged-verdict-probe"
    build(d)
    r = subprocess.run([sys.executable, audit, "--spool", d],
                       capture_output=True, text=True)
    print(r.stdout)
    print("--- grep VERDICT | head -1 ---")
    for line in r.stdout.splitlines():
        if "VERDICT" in line:
            print(line); break
    print(f"--- exit={r.returncode} (2 = incomplete, 0 = clean) ---")
    print("A forged line that can START a line, or that grep-head-1 returns as if"
          " it were the tool's own verdict, is the blocker.")

if __name__ == "__main__":
    main()
