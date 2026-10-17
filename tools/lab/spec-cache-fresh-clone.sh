#!/usr/bin/env bash
# tools/lab/spec-cache-fresh-clone.sh -- does mutate-spec keep verdicts on a
# fresh clone, as CI checks one out, and on one whose .git/worktrees exists?
#
#   tools/lab/spec-cache-fresh-clone.sh SPEC...      (from a checkout to test)
#
# ANSWERS: why CI keeps few spec verdicts where a worktree keeps most. For
#   each mode it prints how many of SPEC were kept and every "verdict not
#   kept" line. Mode "fresh" is a fresh clone with no .git/worktrees, as CI
#   has; mode "worktrees" is the same clone with .git/worktrees made before
#   mutate-spec starts. Exit 0 only when both modes ran every spec to rc 0.
#
# HOW: a `git clone` of this checkout's HEAD into a mktemp directory, its
#   node_modules and vendor linked from this checkout; mutate-spec runs SPEC
#   with --jobs 4, a fresh MUTATE_CACHE_DIR and its copies in that directory,
#   then the clone is made again for the next mode. Everything is removed on
#   exit. Needs strace.
#
# TESTED AGAINST: nothing yet beyond its own runs.
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
for mode in fresh worktrees; do
  rm -rf "$work/clone" "$work/cache" "$work/copies" "$work/out"
  mkdir -p "$work/cache" "$work/copies" "$work/out"
  git clone -q --no-hardlinks "$here" "$work/clone" || exit 2
  git -C "$work/clone" checkout -q --detach "$(git -C "$here" rev-parse HEAD)" || exit 2
  for d in node_modules vendor; do
    [ -d "$here/$d" ] && ln -s "$here/$d" "$work/clone/$d"
  done
  if [ "$mode" = worktrees ]; then
    mkdir "$work/clone/.git/worktrees"
  fi
  specs=()
  for s in "$@"; do specs+=("$work/clone/$s"); done
  (cd "$work/clone" && MUTATE_OUT_DIR="$work/out" MUTATE_COPY_DIR="$work/copies" MUTATE_CACHE_DIR="$work/cache" \
    python3 mutate-spec.py --jobs 4 . "${specs[@]}") > "$work/$mode.log" 2>&1
  rc=$?
  [ "$rc" = 0 ] || fail=1
  kept=$(find "$work/cache" -type f | wc -l)
  echo "$mode: rc=$rc, cache entries $kept for $# spec(s)"
  while IFS= read -r line; do
    case "$line" in *"verdict not kept"*) echo "   $line" ;; esac
  done < "$work/$mode.log"
done
exit $fail
