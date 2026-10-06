#!/usr/bin/env python3
"""How much would a PSScriptAnalyzer gate find across a tree of git repos?

ANSWERS: for every git-tracked .ps1/.psm1/.psd1 file in each repo directly
  under the given roots: how many files, how many are clean, findings by
  PSScriptAnalyzer severity and by rule, and which repos carry the most: the
  count a PowerShell gate's design is chosen from, rather than a guess
  (METHODOLOGY 9 item 1: a rule red everywhere on day one gets switched off).

TESTED AGAINST: a directory of cloned repos on Linux, pwsh 7.6.5,
  PSScriptAnalyzer 1.25.0, 2026-10-05; the counts are in JP_TOOLS #111.

NOT TESTED AGAINST: a repo whose files git cannot list (not a git repo, so
  not swept); an extensionless script whose #! names pwsh (not looked for);
  PSScriptAnalyzer versions other than 1.25.0, whose rules may differ.

Every file is analysed in ONE pwsh process (pwsh takes a second or more to
start), which prints one JSON array of findings.

Exit status: 0 measured, 2 pwsh or the module missing, or its output did not
parse (a count that could not be taken is not a zero).

usage: tools/lab/psscriptanalyzer-sweep.py [ROOT ...]      (default: the current directory)
"""
import collections
import json
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

# The repo root, so platformtools imports from here.
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from platformtools.procname import name_process

SUFFIXES = (".ps1", ".psm1", ".psd1")

# Reads the file list from SWEEP_LIST, one path a line; prints every finding
# as {"path", "severity", "rule"}, an empty array when there are none. Each
# path is wildcard-escaped: -Path expands [ ] * ?, and an unescaped a[1].ps1
# would be analysed as nothing and counted clean.
ANALYSE = (
    "Import-Module PSScriptAnalyzer -ErrorAction Stop; "
    "$found = foreach ($f in Get-Content -LiteralPath $env:SWEEP_LIST) { "
    "Invoke-ScriptAnalyzer -Path ([System.Management.Automation.WildcardPattern]::Escape($f)) "
    "| ForEach-Object { "
    "[pscustomobject]@{ path = $f; severity = [string]$_.Severity; rule = $_.RuleName } } }; "
    "ConvertTo-Json -InputObject @($found) -Depth 3 -Compress"
)


def ps_files(root: str) -> list[tuple[str, str]]:
    """(repo, path) for every PowerShell file tracked in the repos directly under ROOT."""
    out = []
    for repo in sorted(os.listdir(root)):
        rpath = os.path.join(root, repo)
        if not os.path.isdir(os.path.join(rpath, ".git")):
            continue
        ls = subprocess.run(["git", "-C", rpath, "ls-files"], capture_output=True,
                            text=True, check=False).stdout.split("\n")
        out += [(repo, os.path.join(rpath, rel)) for rel in ls
                if rel.lower().endswith(SUFFIXES) and os.path.isfile(os.path.join(rpath, rel))]
    return out


def analyse(paths: list[str]) -> list[dict[str, str]] | None:
    """Every finding in PATHS, or None when pwsh's output does not parse."""
    with tempfile.NamedTemporaryFile("w", suffix=".txt", delete=False, encoding="utf-8") as fh:
        fh.write("\n".join(paths) + "\n")
        listing = fh.name
    try:
        r = subprocess.run(["pwsh", "-NoProfile", "-NonInteractive", "-Command", ANALYSE],
                           capture_output=True, text=True, check=False,
                           env=dict(os.environ, SWEEP_LIST=listing))
    finally:
        os.unlink(listing)
    try:
        found: list[dict[str, str]] = json.loads(r.stdout or "null")
    except ValueError:
        print(f"psscriptanalyzer-sweep: pwsh exit {r.returncode}: {r.stderr.strip()[-300:]}",
              file=sys.stderr)
        return None
    if found is None:
        print(f"psscriptanalyzer-sweep: pwsh printed nothing (exit {r.returncode}): "
              f"{r.stderr.strip()[-300:]}", file=sys.stderr)
        return None
    return found


def report(files: list[tuple[str, str]], found: list[dict[str, str]]) -> None:
    repo_of = {p: repo for repo, p in files}
    with_findings = {f["path"] for f in found}
    print(f"files with zero findings: {len(files) - len(with_findings)}")
    print("findings by severity:", dict(collections.Counter(f["severity"] for f in found)))
    print("top rules:", collections.Counter(f["rule"] for f in found).most_common(10))
    print("top repos by findings:",
          collections.Counter(repo_of.get(f["path"], "?") for f in found).most_common(8))


def main() -> int:
    name_process("psa-sweep")
    if not shutil.which("pwsh"):
        print("pwsh not found; nothing measured", file=sys.stderr)
        return 2
    files = [f for r in (sys.argv[1:] or ["."]) for f in ps_files(r)]
    print(f"PowerShell files: {len(files)} in {len({r for r, _ in files})} repos")
    if not files:
        return 0
    found = analyse([p for _, p in files])
    if found is None:
        return 2
    report(files, found)
    return 0


if __name__ == "__main__":
    sys.exit(main())
