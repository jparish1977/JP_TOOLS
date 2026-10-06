#!/usr/bin/env python3
"""Rewrite one branch's history so a list of names appears nowhere in it, in a
fresh clone, and prove what is left. Never pushes.

ANSWERS: given a repo, a branch and a rules file, what does the branch look
  like with those names replaced in every commit message, author and
  committer identity, and text file of every commit, and does anything named
  survive? The rewritten clone is left in WORK for inspection, with
  filter-repo's old-to-new commit map; publishing it is a separate, manual
  step, printed at the end and never run.

    scrub-history.py SOURCE RULES.json --work DIR [--branch master]

SOURCE is a path or URL git can clone. RULES is JSON:

    {
      "keep":    ["vendor/package"],        substrings left exactly as they are,
                                            even where a replace would match them
      "replace": [["old", "new"], ...],     literal, in order: messages and text files
      "message_regex": [["pattern", "replacement"], ...],
                                            Python regexes, messages only, after
                                            every literal
      "mailmap": ["New Name <new@example> <old@example>", ...],
                                            git mailmap lines, applied to authors
                                            and committers
      "drop_paths": ["path/in/repo", ...],  files removed from every commit (a name
                                            that is itself the problem); untrack
                                            them at the tip first, or the tip
                                            check fails
      "absent":  ["old", ...]               must appear nowhere afterwards (keep
                                            tokens masked first); not inside a
                                            longer run of letters and digits,
                                            or a plain substring when the name
                                            holds / : ~ or .
    }

The rules file names exactly what must not be published, so it belongs
somewhere private, never in the repo being scrubbed.

Clean the branch's current files FIRST, by an ordinary reviewed change that
passes its tests: a blind replacement inside code (a repository URL built
from a name, a test listing strings that must never appear) breaks it. The
scrub then has to leave the tip's files byte-identical, and fails, listing
each as CHANGED, when it does not: history is rewritten, the code people run
is not.

How: git filter-repo --replace-text, --replace-message and --mailmap. Its
literals run in the order given, before any regex, so each keep token is
swapped for a placeholder first and restored last. filter-repo leaves a file
with a NUL byte in its first 8 KB alone as binary: a name inside one is
reported as BINARY, not rewritten, and not counted as clean.

The clone keeps filter-repo's refs/replace/* (old commit -> new). The push
printed at the end sends only HEAD to BRANCH, never those.

Exit status: 0 nothing named survives and the tip is unchanged; 1 something
named survives, a keep token moved, or a file at the tip changed (each
listed); 2 it could not run (bad rules, WORK not empty, git or
git-filter-repo failing).

TESTED AGAINST: a throwaway three-commit repo with a name in a message, an
  author and committer identity, a text file and a keep token beside it:
  clean history with a clean tip exits 0; a file named after it, untracked
  at the tip and listed in drop_paths, exits 0. Positive controls, each
  exit 1: a binary file holding the name (BINARY), a tip still naming it
  (CHANGED), the same named file without drop_paths (PATH), a named file
  whose bytes match an unnamed one that sorts first (PATH; the version that
  took paths from rev-list --objects passed it), and a name beside a hyphen
  (zeta-NAME.txt, PATH; a boundary counting - as a word character passed
  it). And a real
  409-commit branch: every name gone except what was listed (identities the
  mailmap missed, one prose use, one file name, and the tip files still to
  clean), and a first draft's blind replacements inside working code listed
  as CHANGED rather than passed.

NOT TESTED AGAINST: more than one branch (only BRANCH is cloned, so tags and
  other branches are neither rewritten nor checked); names split across a
  line break; a keep token that contains a replace target's placeholder text;
  file NAMES other than drop_paths (paths are not rewritten, only contents or
  whole files dropped; a name in any other path is reported as PATH).

It can mislead in one way: "nothing survives" means nothing in the scanned
forms. A name spelt differently from the rules (another case, an abbreviation)
is not looked for. Read the rules' absent list as the whole claim.
"""

import argparse
import json
import re
import shutil
import subprocess
import sys
from collections import Counter
from collections.abc import Callable
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from platformtools.procname import name_process

PLACEHOLDER = "@@SCRUB_KEEP_{}@@"
BINARY_PROBE = 8192   # filter-repo's own test: a NUL in the first 8 KB is binary

Rules = dict[str, Any]
Patterns = dict[str, re.Pattern[str]]


class RunError(Exception):
    """The scrub could not run; the message says why."""


def git(repo: Path, *args: str, check: bool = True) -> str:
    r = subprocess.run(["git", "-C", str(repo), *args], capture_output=True, text=True,
                       errors="replace", check=False)
    if check and r.returncode != 0:
        raise RunError(f"git {' '.join(args[:2])} failed: {r.stderr.strip()[:300]}")
    return r.stdout


def load_rules(path: Path) -> Rules:
    try:
        rules = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as e:
        raise RunError(f"cannot read rules {path}: {e}") from e
    if not isinstance(rules, dict):
        raise RunError(f"rules {path} must be a JSON object")
    for key in ("keep", "replace", "message_regex", "mailmap", "drop_paths", "absent"):
        rules.setdefault(key, [])
    for old, new in rules["replace"] + rules["message_regex"]:
        if "\n" in old + new or "==>" in old + new:
            raise RunError(f"a rule cannot hold a newline or '==>': {old!r}")
    for tok in rules["keep"]:
        if "\n" in tok or "==>" in tok:
            raise RunError(f"a keep token cannot hold a newline or '==>': {tok!r}")
    for pattern, _ in rules["message_regex"]:
        re.compile(pattern)
    if not rules["absent"]:
        raise RunError("rules name nothing under 'absent', so nothing could be checked")
    return rules


def expressions(rules: Rules, messages: bool) -> str:
    """filter-repo's expressions file: keep tokens to placeholders, the
    replacements in order, placeholders back; message regexes last."""
    lines = [f"literal:{tok}==>{PLACEHOLDER.format(i)}" for i, tok in enumerate(rules["keep"])]
    lines += [f"literal:{old}==>{new}" for old, new in rules["replace"]]
    lines += [f"literal:{PLACEHOLDER.format(i)}==>{tok}" for i, tok in enumerate(rules["keep"])]
    if messages:
        lines += [f"regex:{pattern}==>{repl}" for pattern, repl in rules["message_regex"]]
    return "\n".join(lines) + "\n"


def matcher(names: list[str]) -> Patterns:
    """A name not inside a longer run of letters and digits: found beside -,
    _ or . (zeta-name.txt, name_x), not inside a hash or a longer word."""
    return {n: re.compile(re.escape(n) if re.search(r"[/:~.]", n)
                          else r"(?<![A-Za-z0-9])" + re.escape(n) + r"(?![A-Za-z0-9])") for n in names}


def _log_hits(repo: Path, ref: str, pats: Patterns, masked: Callable[[str], str]) -> list[str]:
    """MESSAGE and IDENTITY hits, per commit."""
    found = []
    log = git(repo, "log", ref, "--format=%H%x00%an <%ae>%x00%cn <%ce>%x00%B%x01")
    for rec in log.split("\x01"):
        parts = rec.strip("\n").split("\x00")
        if len(parts) < 4:
            continue
        sha, author, committer, message = parts
        for n, p in pats.items():
            if p.search(masked(message)):
                found.append(f"MESSAGE {sha[:10]} {n}")
            if p.search(author) or p.search(committer):
                found.append(f"IDENTITY {sha[:10]} {n}")
    return found


def _path_hits(repo: Path, ref: str, pats: Patterns, masked: Callable[[str], str]) -> list[str]:
    """PATH hits, from every commit's own changes, merges included: rev-list
    --objects names each object once, under ONE path, so a named file whose
    bytes match another file's would never be looked at. A directory's name is
    checked as part of every path under it."""
    every_path = set(git(repo, "log", "--format=", "--name-only", "--no-renames", "-m", ref).splitlines())
    return [f"PATH {path} {n}" for path in sorted(every_path - {""})
            for n, p in pats.items() if p.search(masked(path))]


def _content_hits(repo: Path, ref: str, pats: Patterns, masked: Callable[[str], str]) -> list[str]:
    """TEXT and BINARY hits, once per blob: identical bytes get one verdict, so
    the paths it was seen under are only there to say where."""
    paths: dict[str, set[str]] = {}
    for line in git(repo, "rev-list", "--objects", ref).splitlines():
        sha, _, path = line.partition(" ")
        if path:
            paths.setdefault(sha, set()).add(path)
    data = subprocess.run(["git", "-C", str(repo), "cat-file", "--batch"],
                          input="\n".join(paths).encode(), capture_output=True, check=True).stdout
    found, i = [], 0
    while i < len(data):
        nl = data.index(b"\n", i)
        sha, typ, size = data[i:nl].decode().split()
        blob = data[nl + 1:nl + 1 + int(size)]
        i = nl + 1 + int(size) + 1
        if typ != "blob":
            continue
        kind = "BINARY" if b"\0" in blob[:BINARY_PROBE] else "TEXT"
        text = masked(blob.decode("utf-8", "replace"))
        found += [f"{kind} {', '.join(sorted(paths[sha]))} {n}" for n, p in pats.items() if p.search(text)]
    return found


def survivors(repo: Path, ref: str, rules: Rules) -> list[str]:
    """Every place an absent name is still found on REF: MESSAGE, IDENTITY,
    PATH, and TEXT or BINARY file contents. Keep tokens are masked first."""
    pats = matcher(rules["absent"])

    def masked(text: str) -> str:
        for tok in rules["keep"]:
            text = text.replace(tok, "")
        return text

    found = _log_hits(repo, ref, pats, masked) + _path_hits(repo, ref, pats, masked)
    return sorted(set(found + _content_hits(repo, ref, pats, masked)))


def keep_counts(repo: Path, ref: str, keep: list[str]) -> Counter[str]:
    """How often each keep token appears in REF's tip tree: the scrub must not move it."""
    counts: Counter[str] = Counter()
    for tok in keep:
        out = git(repo, "grep", "-c", "-F", tok, ref, check=False)
        counts[tok] = sum(int(line.rsplit(":", 1)[1]) for line in out.splitlines() if line)
    return counts


def filter_repo(clone: Path, work: Path, rules: Rules) -> None:
    """git filter-repo over CLONE with RULES' expressions, mailmap and drops,
    their files written into WORK."""
    (work / "replace-text").write_text(expressions(rules, messages=False), encoding="utf-8")
    (work / "replace-message").write_text(expressions(rules, messages=True), encoding="utf-8")
    (work / "mailmap").write_text("\n".join(rules["mailmap"]) + "\n", encoding="utf-8")
    args = ["git", "-C", str(clone), "filter-repo", "--force",
            "--replace-text", str(work / "replace-text"),
            "--replace-message", str(work / "replace-message")]
    if rules["mailmap"]:
        args += ["--mailmap", str(work / "mailmap")]
    if rules["drop_paths"]:
        args += ["--invert-paths"] + [arg for p in rules["drop_paths"] for arg in ("--path", p)]
    r = subprocess.run(args, capture_output=True, text=True, check=False)
    if r.returncode != 0:
        raise RunError(f"git filter-repo failed: {r.stderr.strip()[-500:]}")
    commit_map = clone / ".git" / "filter-repo" / "commit-map"
    if commit_map.is_file():
        shutil.copy(commit_map, work / "commit-map")


def scrub(source: str, rules: Rules, work: Path, branch: str) -> int:
    if work.exists() and any(work.iterdir()):
        raise RunError(f"{work} is not empty: give a new directory")
    if shutil.which("git-filter-repo") is None:
        raise RunError("git-filter-repo is not installed (apt git-filter-repo)")
    clone = work / "repo"
    r = subprocess.run(["git", "clone", "-q", "--no-local", "--single-branch", "--branch", branch,
                        source, str(clone)], capture_output=True, text=True, check=False)
    if r.returncode != 0:
        raise RunError(f"clone failed: {r.stderr.strip()[:300]}")
    old_tip = git(clone, "rev-parse", "HEAD").strip()
    # Listed before filter-repo runs: it prunes the old commits and trees.
    old_files = git(clone, "ls-tree", "-r", "--full-tree", "HEAD").splitlines()
    keep_before = keep_counts(clone, "HEAD", rules["keep"])
    filter_repo(clone, work, rules)
    new_files = git(clone, "ls-tree", "-r", "--full-tree", "HEAD").splitlines()
    tip_changes = [f"TIP {path}" for path in
                   sorted({line.split("\t", 1)[1] for line in set(old_files) ^ set(new_files)})]
    keep_after = keep_counts(clone, "HEAD", rules["keep"])
    left = survivors(clone, "HEAD", rules) + [
        f"KEEP {tok}: {keep_before[tok]} at the old tip, {keep_after[tok]} now"
        for tok in rules["keep"] if keep_before[tok] != keep_after[tok]]
    print(f"scrub-history: {branch} {old_tip[:10]} -> {git(clone, 'rev-parse', 'HEAD').strip()[:10]}, "
          f"{git(clone, 'rev-list', '--count', 'HEAD').strip()} commits, in {clone}")
    print(f"scrub-history: old-to-new commit map: {work / 'commit-map'}")
    return report(left, tip_changes, branch, old_tip)


def report(left: list[str], tip_changes: list[str], branch: str, old_tip: str) -> int:
    """Print what is left and what changed at the tip; 1 if either, else 0
    with the push a person would run."""
    for line in left:
        print(f"scrub-history: LEFT {line}")
    for line in tip_changes:
        print(f"scrub-history: CHANGED {line}")
    if left or tip_changes:
        print(f"scrub-history: {len(left)} place(s) still name something or moved a keep token, "
              f"{len(tip_changes)} file(s) at the tip changed")
        return 1
    print("scrub-history: nothing named survives in messages, identities, paths or file contents,")
    print("scrub-history: and the tip's files are byte-identical to the source's: only history changed")
    print("scrub-history: NOT PUSHED. To publish, after reading the clone, from it:")
    print(f"    git push --force-with-lease={branch}:{old_tip} SOURCE HEAD:{branch}")
    return 0


def main(argv: list[str] | None = None) -> int:
    name_process("scrub-history")
    ap = argparse.ArgumentParser(description="Rewrite a branch's history without a list of names, "
                                             "in a fresh clone. Never pushes.")
    ap.add_argument("source", help="the repo to clone: a path or URL")
    ap.add_argument("rules", type=Path, help="the rules JSON (keep it private)")
    ap.add_argument("--work", type=Path, required=True, help="a new or empty directory for the clone")
    ap.add_argument("--branch", default="master")
    args = ap.parse_args(argv)
    try:
        return scrub(args.source, load_rules(args.rules), args.work, args.branch)
    except (RunError, re.error, subprocess.CalledProcessError) as e:
        print(f"scrub-history: NOT RUN: {e}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
