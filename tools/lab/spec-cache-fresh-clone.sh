#!/usr/bin/env bash
# tools/lab/spec-cache-fresh-clone.sh -- does mutate-spec keep verdicts on a
# fresh clone, as CI checks one out, and on one whose .git/worktrees exists?
#
#   tools/lab/spec-cache-fresh-clone.sh SPEC...      (from a checkout to test)
#
# ANSWERS: why CI keeps few spec verdicts where a worktree keeps most, and
#   what in .git changes while mutate-spec runs. For each mode it prints how
#   many of SPEC were kept, every "verdict not kept" line, and each entry of
#   the clone's .git (and .git itself) whose type, size or ctime differs
#   after mutate-spec from before it, or that came or went. Mode "fresh" is a
#   fresh clone with no .git/worktrees, as CI has; mode "worktrees" is the
#   same clone with .git/worktrees made before mutate-spec starts; mode
#   "refreshed" is a fresh clone whose index git has refreshed first
#   (`git update-index --refresh`), as a fresh clone's racily clean index is
#   rewritten by the first git command that reads it. Exit 0 only when every
#   mode ran every spec to rc 0.
#
# HOW: a `git clone` of this checkout's HEAD into a mktemp directory, its
#   node_modules and vendor linked from this checkout; mutate-spec runs SPEC
#   with --jobs 4, a fresh MUTATE_CACHE_DIR and its copies in that directory,
#   then the clone is made again for the next mode. Everything is removed on
#   exit. Needs strace.
#
# TESTED AGAINST: 4 specs (jpt-scratch-on-hook, jpt-skip-dirs-on-files,
#   jpt-pyproject-on-pyproject, jpt-scratch-on-scratch-refs): fresh and
#   worktrees each 0 of 4 kept, all "<clone>/.git changed while its runs ran".
#
# NOT TESTED AGAINST: specs whose runs need network or the CI image's tools;
#   a box without strace, where mutate-spec refuses MUTATE_CACHE_DIR.
printf '%s' jpt-cache-clone > /proc/$$/comm 2>/dev/null || true
set -u
[ $# -gt 0 ] || { echo "usage: spec-cache-fresh-clone.sh SPEC..." >&2; exit 2; }
here=$(git rev-parse --show-toplevel) || exit 2
work=$(mktemp -d)
trap 'rm -rf "$work"' EXIT
fail=0
gitlist() {
  find "$work/clone/.git" -maxdepth 1 -printf '%P\t%y %s %C@\n' | sort
}
for mode in fresh worktrees refreshed; do
  rm -rf "$work/clone" "$work/cache" "$work/copies" "$work/out"
  mkdir -p "$work/cache" "$work/copies" "$work/out"
  git clone -q --no-hardlinks "$here" "$work/clone" || exit 2
  git -C "$work/clone" checkout -q --detach "$(git -C "$here" rev-parse HEAD)" || exit 2
  for d in node_modules vendor; do
    [ -d "$here/$d" ] && ln -s "$here/$d" "$work/clone/$d"
  done
  if [ "$mode" = worktrees ]; then
    mkdir "$work/clone/.git/worktrees"
  elif [ "$mode" = refreshed ]; then
    git -C "$work/clone" update-index -q --refresh
  fi
  specs=()
  for s in "$@"; do specs+=("$work/clone/$s"); done
  gitlist > "$work/before"
  (cd "$work/clone" && MUTATE_OUT_DIR="$work/out" MUTATE_COPY_DIR="$work/copies" MUTATE_CACHE_DIR="$work/cache" \
    python3 mutate-spec.py --jobs 4 . "${specs[@]}") > "$work/$mode.log" 2>&1
  rc=$?
  gitlist > "$work/after"
  diff "$work/before" "$work/after" | while IFS= read -r line; do
    case "$line" in "<"*|">"*) echo "   .git ${line/$'\t'/  }" ;; esac
  done
  [ "$rc" = 0 ] || fail=1
  kept=$(find "$work/cache" -type f | wc -l)
  echo "$mode: rc=$rc, cache entries $kept for $# spec(s)"
  while IFS= read -r line; do
    case "$line" in *"verdict not kept"*) echo "   $line" ;; esac
  done < "$work/$mode.log"
done
exit $fail
