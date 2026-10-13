#!/usr/bin/env bash
# tools/lab/purge-prescrub-pull-refs.sh -- after a history rewrite of a forge's
# bare repository, delete the refs that still reach the old history, and purge
# its objects.
#
# ANSWERS: which refs of REPO still reach a commit that only the old history
#   had, and, with --apply, whether that history is gone afterwards. A rewrite
#   of the branches leaves a forge's pull-request refs (refs/pull/N/head and
#   /merge) on the old commits, so the history the rewrite removed is still
#   served, and a `push --mirror` copies it on.
#
# HOW: the old history is ARCHIVE's commits that no branch or tag of REPO
#   reaches. A ref of REPO other than a branch or tag that reaches one of them
#   is deleted (update-ref -d against its recorded value, so a ref moved since
#   the listing is left alone and named). Then the reflogs expire and
#   `git gc --prune=now` drops the unreachable objects. It is a purge, never an
#   archive: ARCHIVE itself is left as it is.
#
# PROOF, after --apply: no ref of REPO reaches an old commit, no old commit is
#   readable in REPO (cat-file -e fails for every one), and `git fsck` is
#   clean. Any of those failing exits 1. Without --apply it only lists.
#
# TESTED AGAINST: a scratch pair made from two clones of one repo, the second
#   rewritten, with a pull ref left on the old history (see the PR).
#
# NOT TESTED AGAINST: a forge writing to REPO during the gc; run it when no
#   push is expected. A repo with objects/info/alternates, which is refused.
#
# usage: purge-prescrub-pull-refs.sh REPO.git ARCHIVE.git [--apply]
set -uo pipefail
printf '%s' purge-prescrub > /proc/$$/comm 2>/dev/null

repo=${1:?usage: purge-prescrub-pull-refs.sh REPO.git ARCHIVE.git [--apply]}
archive=${2:?usage: purge-prescrub-pull-refs.sh REPO.git ARCHIVE.git [--apply]}
apply=${3:-}

check_args() {  # exit 2 on an unknown option, a path that is no repository, or alternates
  [ -z "$apply" ] || [ "$apply" = --apply ] || { echo "unknown option: $apply" >&2; exit 2; }
  local r
  for r in "$repo" "$archive"; do
    git -C "$r" rev-parse --is-bare-repository >/dev/null 2>&1 || { echo "not a git repository: $r" >&2; exit 2; }
  done
  if [ -s "$repo/objects/info/alternates" ]; then
    echo "refusing: $repo borrows objects (objects/info/alternates); a gc there cannot purge them" >&2
    exit 2
  fi
}

old_commits() {  # ARCHIVE's commits that no branch or tag of REPO reaches
  comm -23 <(git -C "$archive" rev-list --all | sort -u) \
           <(git -C "$repo" rev-list --branches --tags | sort -u)
}
reaching() {  # each ref of REPO, not a branch or tag, that reaches one: "SHA REF"
  git -C "$repo" for-each-ref --format='%(objectname) %(refname)' | while read -r sha ref; do
    case "$ref" in refs/heads/*|refs/tags/*) continue ;; esac
    if git -C "$repo" rev-list "$ref" | sort -u | comm -12 - "$t/old" | head -n 1 | grep -q .; then
      printf '%s %s\n' "$sha" "$ref"
    fi
  done
}

purge() {  # delete each listed ref against its recorded value, then drop what is unreachable
  local sha ref rc=0
  while read -r sha ref; do
    git -C "$repo" update-ref -d "$ref" "$sha" || { echo "NOT DELETED (moved since listed?): $ref" >&2; rc=1; }
  done < "$t/refs"
  git -C "$repo" reflog expire --expire=now --all
  git -C "$repo" gc --prune=now --quiet || { echo "gc failed" >&2; rc=1; }
  return "$rc"
}

prove() {  # 0 when no ref reaches an old commit, none is readable, and fsck is clean
  local left readable=0 c fsck=ok
  left=$(reaching | wc -l)
  while read -r c; do
    if git -C "$repo" cat-file -e "$c^{commit}" 2>/dev/null; then readable=$((readable + 1)); fi
  done < "$t/old"
  git -C "$repo" fsck --no-dangling --no-progress >/dev/null 2>"$t/fsck" || fsck="FAILED: $(head -n 3 "$t/fsck")"
  echo "after: refs reaching old commits $left, old commits still readable $readable, fsck $fsck"
  [ "$left" = 0 ] && [ "$readable" = 0 ] && [ "$fsck" = ok ]
}

check_args
t=$(mktemp -d); trap 'rm -rf "$t"' EXIT
old_commits > "$t/old"
reaching > "$t/refs"
echo "old commits (in the archive, on no branch or tag): $(wc -l < "$t/old")"
echo "refs reaching them: $(wc -l < "$t/refs")"
cut -d' ' -f2 "$t/refs" | sed 's/^/  /'
[ "$apply" = --apply ] || { echo "(listing only: --apply deletes them and purges)"; exit 0; }
rc=0
purge || rc=1
prove || rc=1
exit "$rc"
