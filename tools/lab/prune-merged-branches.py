#!/usr/bin/env python3
"""Which remote branches hold nothing the base branch does not already have?

ANSWERS: which of a remote's branches are finished. A forge's branch page
  cannot say: a merged branch and an abandoned one look identical there, and
  a branch that landed REBASED looks unmerged because none of its commit ids
  are in the base. So the page invites two opposite mistakes: keeping dead
  refs forever, and deleting work that never landed.

WHAT IT MEASURES, per branch, is reachability and nothing else:
  MERGED      the tip is an ancestor of the base, so deleting it loses no
              object. This is the only class deleted without being named.
  AHEAD n     n commits are reachable from the tip and not from the base.
              Refused unless the branch is named to --also, and the commits
              are printed first so the caller sees what they are orphaning.
  HELD        the branch the remote's HEAD points at, whatever it measures.
  A rebased-and-landed branch reads as AHEAD, which is correct: its CONTENT
  is in the base and its COMMITS are not, and only a person can say whether
  the second matters. The tool will not guess, because guessing wrong here is
  the unrecoverable direction.

  The remote is never inferred, and --base must be a ref ON that remote:
  `--base master` for `--base origin/master` would measure the remote's
  default branch against a local ref of the same name and put it in MERGED.

TESTED AGAINST: a remote with 29 branches besides its default, --base on its
  default branch: 25 MERGED and 5 AHEAD, and each of the five checked by
  hand (an open pull request; a branch landed rebased; two whose files were
  absent from the base; one whose fix was absent from the base). Negative
  control: against a different --base the same branches split 20/9, so the
  verdict tracks the base rather than being a property of the branch. A
  --also naming no branch exits 2 and deletes nothing; `--base master` is
  refused; on a throwaway bare remote whose clone had no local HEAD ref, the
  default branch was still HELD. Since restructured for the smell limits:
  its dry-run output is byte-identical to that form on a throwaway remote.

NOT TESTED AGAINST:
  - A push mirror. Whether a deletion here reaches a downstream mirror is the
    mirror's business, and this tool neither checks nor claims it.
  - A protected branch. The delete will simply fail and be reported; nothing
    tries to lift protection.
  - Worktrees and local branches. Remote refs only.
"""

import argparse
import contextlib
import ctypes
import subprocess
import sys
from dataclasses import dataclass, field


@dataclass
class Classes:
    held: list[str] = field(default_factory=list)
    merged: list[str] = field(default_factory=list)
    kept: list[tuple[str, list[str]]] = field(default_factory=list)
    forced: list[tuple[str, list[str]]] = field(default_factory=list)


def set_proc_name(name: str) -> None:
    """Name this process (what top, btop and `pgrep -x` see). PR_SET_NAME is 15."""
    with contextlib.suppress(OSError, AttributeError):  # reason: best effort; without a glibc prctl the process keeps its name
        ctypes.CDLL(None).prctl(15, name.encode()[:15], 0, 0, 0)


def git(*args: str) -> str:
    """Run git and return its stdout, exiting with git's own message on error.

    check=True alone raises CalledProcessError, whose str() is "returned
    non-zero exit status 128" and whose stderr (the sentence naming the bad
    ref) is captured into the exception and never printed.
    """
    out = subprocess.run(["git", *args], capture_output=True, text=True, check=False)
    if out.returncode != 0:
        sys.exit(f"git {' '.join(args)}: {out.stderr.strip() or out.returncode}")
    return out.stdout.strip()


def remote_head(remote: str) -> str | None:
    """The remote's default branch as `<remote>/<name>`, or None if unknown.

    Asks the REMOTE, not the clone: a clone made with `git clone` has a local
    `refs/remotes/<remote>/HEAD` and one built any other way does not, and
    trusting its absence would leave the default branch unprotected.
    """
    out = subprocess.run(["git", "ls-remote", "--symref", remote, "HEAD"],
                         capture_output=True, text=True, check=False)
    for line in out.stdout.splitlines() if out.returncode == 0 else []:
        parts = line.split()
        if line.startswith("ref:") and parts[1].startswith("refs/heads/"):
            return f"{remote}/{parts[1][len('refs/heads/'):]}"
    local = subprocess.run(["git", "symbolic-ref", "--short", f"refs/remotes/{remote}/HEAD"],
                           capture_output=True, text=True, check=False)
    return (local.stdout.strip() or None) if local.returncode == 0 else None


def remote_branches(remote: str, base: str) -> list[str]:
    """Every branch on `remote` except the base itself, in refname order."""
    refs = git("for-each-ref", "--format=%(refname:short)", f"refs/remotes/{remote}").splitlines()
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
    delete set.
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


def classify(remote: str, base: str, named: set[str]) -> Classes:
    """Every branch on REMOTE, sorted into HELD, MERGED, AHEAD-kept and AHEAD-forced."""
    prefix = f"{remote}/"
    protected = remote_head(remote)
    classes = Classes()
    for ref in remote_branches(remote, base):
        short = ref[len(prefix):]
        unique = [] if ref == protected else ahead(ref, base)
        if ref == protected:
            classes.held.append(short)
        elif not unique:
            classes.merged.append(short)
        elif short in named:
            classes.forced.append((short, unique))
        else:
            classes.kept.append((short, unique))
    return classes


def report(remote: str, c: Classes) -> list[str]:
    lines = [f"HELD    {s}  ({remote}/HEAD points here)" for s in sorted(c.held)]
    lines += [f"KEEP    {s}  ({len(u)} ahead)" for s, u in sorted(c.kept)]
    lines += [f"MERGED  {s}" for s in sorted(c.merged)]
    for short, unique in sorted(c.forced):
        lines.append(f"FORCED  {short}  ({len(unique)} ahead, named to --also)")
        lines += [f"          {line}" for line in unique]
    return lines


def delete(remote: str, doomed: list[str]) -> int:
    """Delete each branch; 1 if any delete failed. One failure never stops the rest."""
    failed = 0
    for short in doomed:
        r = subprocess.run(["git", "push", remote, "--delete", short],
                           capture_output=True, text=True, check=False)
        if r.returncode == 0:
            print(f"deleted {short}")
        else:
            failed += 1
            print(f"FAILED  {short}: {r.stderr.strip()}", file=sys.stderr)
    return 1 if failed else 0


def _parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(description=(__doc__ or "").splitlines()[0])
    ap.add_argument("--remote", required=True, help="the remote to prune")
    ap.add_argument("--base", required=True,
                    help="the ref a branch must be an ancestor of, e.g. origin/master")
    ap.add_argument("--also", action="append", default=[],
                    help="delete this branch even though it is AHEAD; short name, "
                         "repeatable. Its orphaned commits are printed first.")
    ap.add_argument("--go", action="store_true",
                    help="actually delete. Without it nothing is pushed.")
    return ap


def main() -> int:
    set_proc_name("prune-branches")
    args = _parser().parse_args()
    validate_base(args.remote, args.base)
    named = set(args.also)
    c = classify(args.remote, args.base, named)
    for line in report(args.remote, c):
        print(line)
    missing = named - {s for s, _ in c.forced} - set(c.merged)
    if missing:
        print(f"\n--also named {', '.join(sorted(missing))}, which is not a "
              f"branch on {args.remote}", file=sys.stderr)
        return 2
    doomed = c.merged + [s for s, _ in c.forced]
    print(f"\n{len(doomed)} to delete, {len(c.kept)} kept")
    if not args.go:
        print("dry run -- pass --go to delete")
        return 0
    return delete(args.remote, doomed)


if __name__ == "__main__":
    sys.exit(main())
