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

REVIEW ROUND 1 found the hole this header's own reasoning had left open.
  The remote is never inferred, "because 'prune the branches' against the
  wrong one is exactly the mistake worth making impossible" -- and --base,
  which is what decides MERGED, had no such check. `--base master` instead of
  `--base origin/master` is one token shorter, matches no `origin/...` name,
  measures `origin/master` against the LOCAL `master`, and puts the remote's
  default branch in the class that is deleted without being named. Reproduced
  on a throwaway bare remote by `another repo` and again here: 1 to delete, and
  the one is `master`. --base must now name a ref on the remote, and the
  remote's HEAD is HELD whatever the classification says.

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
import contextlib
import ctypes
import subprocess
import sys


def set_proc_name(name: str) -> None:
    """Name this process for btop and `pgrep -x`, per the fleet convention.

    PR_SET_NAME is 15, and the kernel truncates to that. Best effort: a box
    without a glibc prctl just keeps the name it had.
    """
    with contextlib.suppress(OSError, AttributeError):
        ctypes.CDLL(None).prctl(15, name.encode()[:15], 0, 0, 0)


def git(*args: str) -> str:
    """Run git and return its stdout, exiting with git's own message on error.

    check=True alone raises CalledProcessError, whose str() is "returned
    non-zero exit status 128" and whose stderr -- the sentence naming the bad
    ref -- is captured into the exception and never printed.
    """
    out = subprocess.run(["git", *args], capture_output=True, text=True, check=False)
    if out.returncode != 0:
        sys.exit(f"git {' '.join(args)}: {out.stderr.strip() or out.returncode}")
    return out.stdout.strip()


def remote_head(remote: str) -> str | None:
    """The remote's default branch as `<remote>/<name>`, or None if unknown.

    Asks the REMOTE, not the clone. A clone made with `git clone` has a local
    `refs/remotes/<remote>/HEAD`, and one built any other way does not: the
    fixture this guard was first tested against had none, so the protection
    looked present and did nothing. ls-remote answers from the server.
    """
    out = subprocess.run(
        ["git", "ls-remote", "--symref", remote, "HEAD"],
        capture_output=True, text=True, check=False,
    )
    if out.returncode == 0:
        for line in out.stdout.splitlines():
            if line.startswith("ref:"):
                ref = line.split()[1]
                if ref.startswith("refs/heads/"):
                    return f"{remote}/{ref[len('refs/heads/'):]}"
    # Fall back to the clone's own idea, which may not exist either.
    local = subprocess.run(
        ["git", "symbolic-ref", "--short", f"refs/remotes/{remote}/HEAD"],
        capture_output=True, text=True, check=False,
    )
    return local.stdout.strip() or None if local.returncode == 0 else None


def remote_branches(remote: str, base: str) -> list[str]:
    """Every branch on `remote` except the base itself, in refname order."""
    refs = git(
        "for-each-ref", "--format=%(refname:short)", f"refs/remotes/{remote}"
    ).splitlines()
    return [r for r in refs if r != base and not r.endswith("/HEAD")]


def ahead(ref: str, base: str) -> list[str]:
    """Commits reachable from ref and not from base, newest first."""
    out = git("log", "--oneline", f"{base}..{ref}")
    return out.splitlines() if out else []


def validate_base(remote: str, base: str) -> None:
    """Refuse a base that is not a ref on this remote, and say why.

    The base decides what MERGED means, and MERGED is the class deleted
    without anyone naming it. A base off this remote never matches the
    remote's own branch by name, so the remote's default branch gets measured
    against a LOCAL ref of the same name, finds nothing unique, and joins the
    delete set. `--base master` for `--base origin/master` is one token
    shorter and does exactly that.
    """
    prefix = f"{remote}/"
    if not base.startswith(prefix):
        sys.exit(
            f"--base {base} is not a ref on {remote}. "
            f"Use --base {prefix}{base.split('/')[-1]}: a base off this "
            f"remote puts the remote's own default branch in the delete set."
        )
    # No --quiet: that suppresses the stderr line naming the bad ref, which is
    # the whole message the caller needs.
    git("rev-parse", "--verify", f"{base}^{{commit}}")


def main() -> int:
    set_proc_name("prune-branches")
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
    validate_base(args.remote, args.base)

    # Belt and braces: validate_base is about a MISTYPED base, this is about
    # the outcome. Nothing here should ever delete the branch the remote
    # points HEAD at, however it was classified.
    protected = remote_head(args.remote)

    named = set(args.also)
    merged, kept, forced, held = [], [], [], []

    for ref in remote_branches(args.remote, args.base):
        short = ref[len(prefix):]
        if ref == protected:
            held.append(short)
            continue
        unique = ahead(ref, args.base)
        if not unique:
            merged.append(short)
        elif short in named:
            forced.append((short, unique))
        else:
            kept.append((short, unique))

    for short in sorted(held):
        print(f"HELD    {short}  ({args.remote}/HEAD points here)")
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
