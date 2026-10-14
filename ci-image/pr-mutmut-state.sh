#!/bin/bash
# ci-image/pr-mutmut-state.sh -- a pull request's own mutmut state, kept
# between its pushes, so a push re-tests only what changed since the last one.
#
#   bash ci-image/pr-mutmut-state.sh restore PR_DIR BRANCH   (after fetch-mutmut-state.sh)
#   bash ci-image/pr-mutmut-state.sh save    PR_DIR BRANCH   (after mutmut-gate.py run)
#
# Run from the checkout. An entry is PR_DIR/<BRANCH>@<state version>, a state
# directory as publish-mutmut-state.sh writes it (versions, a current link, a
# lock) and fetch-mutmut-state.sh reads it, holding the run's mutants/ and the
# state's baseline. The state version is the one fetch-mutmut-state.sh copied
# (./mutmut-state-version): the verdicts kept for a PR stand only against the
# state they were judged from, so after the nightly moves the state, a PR's
# first push starts from the new state again.
#
# restore: when this branch has an entry for this state version, its mutants/
# replaces the state's copy; mutmut-gate then judges only what changed since
# the commit that run judged (mutmut-gate.json). The baseline stays the
# state's. The entry is copied beside the checkout first and swapped in only
# when that worked; with no entry, one that cannot be read, or no PR_DIR (not
# mounted), the state's copy is kept.
#
# save: this run's mutants/ becomes the branch's entry for the state version,
# whole or not at all; entries for another state version untouched for three
# hours (no job runs that long) are removed, so PR_DIR holds about one day's
# open PRs. A save that cannot write PR_DIR says so and changes nothing.
#
# BRANCH is the pull request's head branch, which names its entry. One with a
# character outside [A-Za-z0-9._-] has those as "_" and then "-" and the
# first 12 hex digits of the name's SHA-256, so two branches the flattening
# makes alike (a/b, a_b) never share an entry. Exit: 0, whatever was or was
# not kept; 2 a usage error or no state copied here.
printf '%s' jpt-mut-pr > /proc/$$/comm 2>/dev/null
set -euo pipefail
here=$(cd "$(dirname "$0")" && pwd)
verb=${1:-}; pr=${2:-}; branch=${3:-}
if [ -z "$pr" ] || [ -z "$branch" ] || { [ "$verb" != restore ] && [ "$verb" != save ]; }; then
  echo "usage: pr-mutmut-state.sh restore|save PR_DIR BRANCH" >&2
  exit 2
fi
if [ ! -f mutmut-state-version ] || [ ! -d mutants ]; then
  echo "pr-mutmut-state: no state copied here (fetch-mutmut-state.sh first)" >&2
  exit 2
fi
version=$(cat mutmut-state-version)
flat=$(printf '%s' "$branch" | tr -c 'A-Za-z0-9._-' '_')
if [ "$flat" != "$branch" ]; then
  flat=$flat-$(printf '%s' "$branch" | sha256sum | cut -c1-12)
fi
entry=$pr/$flat@$version

restore() {
  if [ ! -d "$pr" ]; then
    echo "pr-mutmut-state: no $pr mounted: this push starts from the state $version"
    return
  fi
  if [ ! -e "$entry/current" ]; then
    echo "pr-mutmut-state: nothing kept for $branch against state $version: this push starts from the state"
    return
  fi
  copy=$(mktemp -d ./.pr-mutmut-state.XXXXXX)
  trap 'rm -rf "$copy"' EXIT
  if ! (cd "$copy" && bash "$here/fetch-mutmut-state.sh" "$entry") > /dev/null 2>&1; then
    echo "pr-mutmut-state: could not read $branch's kept state: this push starts from the state $version"
    return
  fi
  rm -rf mutants
  mv "$copy/mutants" mutants
  echo "pr-mutmut-state: restored $branch's own state against $version: only what changed since its last run is judged"
}

save() {
  if ! mkdir -p "$entry" 2>/dev/null; then
    echo "pr-mutmut-state: $pr is not writable: nothing kept"
    return
  fi
  cp mutmut-baseline.json baseline.json
  trap 'rm -f baseline.json' EXIT
  if ! bash "$here/publish-mutmut-state.sh" "$entry" > /dev/null; then
    echo "pr-mutmut-state: could not keep $branch's state: the next push starts from the state"
    return
  fi
  find "$pr" -mindepth 1 -maxdepth 1 -name '*@*' ! -name "*@$version" -mmin +180 -exec rm -rf {} +
  echo "pr-mutmut-state: kept $branch's state against $version for its next push"
}

"$verb"
