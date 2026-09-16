#!/usr/bin/env python3
"""Which remote branches hold nothing the base branch does not already have?

ANSWERS a question asked on 2026-09-16, when joe/JP_TOOLS carried 29 branches
  besides master and nobody could say which were finished. Reading a forge's
  branch page does not answer it: a merged branch and an abandoned one look
  identical there, and a branch that landed REBASED looks unmerged because
  none of its commit ids are in the base. So the page invites two opposite
  mistakes -- keeping dead refs forever, and deleting work that never landed.

WHAT IT MEASURES, per branch, is reachability and nothing else:
  MERGED      the tip is an ancestor of the base, so deleting it loses no
              object. This is the only class deleted without being named.
  AHEAD n     n commits are reachable from the tip and not from the base.
              Refused unless the branch is named to --also, and the commits
              are printed first so the caller sees what they are orphaning.
  A rebased-and-landed branch reads as AHEAD, which is correct: its CONTENT
  is in the base and its COMMITS are not, and only a person can say whether
  the second matters. The tool will not guess, because guessing wrong here is
  the unrecoverable direction.

TESTED AGAINST: joe/JP_TOOLS on the build server, 2026-09-16. The positive control
  is the classification itself -- run against origin with --base
  origin/master it put 25 branches in MERGED and 5 in AHEAD, and each of the
  five was checked by hand: suppress-reason (open PR #104, 3 commits),
  feature/methodology-instruments (33, landed rebased as #72),
  feature/autoit-triage and feature/vm-image-boot (1 each, files absent from
  master), fix/photorec-fileopt-syntax (1, fix absent from recover.py:319).
  A negative control was run too: with --base origin/feature/spool-purge the
  same 29 branches split 20/9 instead of 25/4, so the verdict tracks the base
  rather than being a property of the branch. A --also naming no branch at
  all exits 2 and deletes nothing.

NOT TESTED AGAINST:
  - A push mirror. Whether a deletion here reaches a downstream mirror is the
    mirror's business, and this tool neither checks nor claims it. JP_TOOLS
    push-mirrors to GitHub; confirm there separately.
  - A protected branch. The delete will simply fail and be reported; nothing
    tries to lift protection.
  - Any remote other than one named on the command line. It never infers a
    remote, because "prune the branches" against the wrong one is exactly the
    mistake worth making impossible.
  - Worktrees and local branches. Remote refs only.
"""

import argparse
import subprocess
import sys


def git(*args: str) -> str:
    """Run git and return its stdout, raising on a non-zero exit."""
    out = subprocess.run(
        ["git", *args], capture_output=True, text=True, check=True
    )
    return out.stdout.strip()


def remote_branches(remote: str, base: str) -> list[str]:
    """Every branch on `remote` except the base itself, shortest name first."""
    refs = git(
        "for-each-ref", "--format=%(refname:short)", f"refs/remotes/{remote}"
    ).splitlines()
    return [r for r in refs if r != base and not r.endswith("/HEAD")]


def ahead(ref: str, base: str) -> list[str]:
    """Commits reachable from ref and not from base, newest first."""
    out = git("log", "--oneline", f"{base}..{ref}")
    return out.splitlines() if out else []


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--remote", required=True, help="the remote to prune")
    ap.add_argument(
        "--base",
        required=True,
        help="the ref a branch must be an ancestor of, e.g. origin/master",
    )
    ap.add_argument(
        "--also",
        action="append",
        default=[],
        help="delete this branch even though it is AHEAD; short name, "
        "repeatable. Its orphaned commits are printed first.",
    )
    ap.add_argument(
        "--go",
        action="store_true",
        help="actually delete. Without it nothing is pushed.",
    )
    args = ap.parse_args()

    prefix = f"{args.remote}/"
    named = set(args.also)
    merged, kept, forced = [], [], []

    for ref in remote_branches(args.remote, args.base):
        short = ref[len(prefix):]
        unique = ahead(ref, args.base)
        if not unique:
            merged.append(short)
        elif short in named:
            forced.append((short, unique))
        else:
            kept.append((short, unique))

    for short, unique in sorted(kept):
        print(f"KEEP    {short}  ({len(unique)} ahead)")
    for short in sorted(merged):
        print(f"MERGED  {short}")
    for short, unique in sorted(forced):
        print(f"FORCED  {short}  ({len(unique)} ahead, named to --also)")
        for line in unique:
            print(f"          {line}")

    missing = named - {s for s, _ in forced} - set(merged)
    if missing:
        print(
            f"\n--also named {', '.join(sorted(missing))}, which is not a "
            f"branch on {args.remote}",
            file=sys.stderr,
        )
        return 2

    doomed = merged + [s for s, _ in forced]
    print(f"\n{len(doomed)} to delete, {len(kept)} kept")
    if not args.go:
        print("dry run -- pass --go to delete")
        return 0

    failed = []
    for short in doomed:
        # check=False deliberately: one protected or already-gone branch must
        # not abandon the rest of the list half-pruned.
        r = subprocess.run(
            ["git", "push", args.remote, "--delete", short],
            capture_output=True,
            text=True,
            check=False,
        )
        if r.returncode == 0:
            print(f"deleted {short}")
        else:
            failed.append(short)
            print(f"FAILED  {short}: {r.stderr.strip()}", file=sys.stderr)

    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
