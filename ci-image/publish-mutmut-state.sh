#!/bin/bash
# ci-image/publish-mutmut-state.sh -- put this checkout's mutmut state into the
# runner volume as its new current state, whole or not at all.
#
#   bash ci-image/publish-mutmut-state.sh STATE_DIR     (from the checkout)
#
# Takes ./mutants and ./baseline.json. Writes STATE_DIR/next, then renames it
# to STATE_DIR/current, so a job reading the volume sees the old state or the
# new one, never half of each. From mutants/ it leaves out mutmut-gate's .git
# link and anything git reports as untracked there that is not mutmut's own
# (.meta, .spans, mutmut-stats.json): what a mutant wrote is no state.
#
# A CI checkout is made under umask 0 and tar keeps modes, so the state is
# made not group- or world-writable before it goes in.
#
# Exit: 0 published; 2 nothing to publish, or STATE_DIR not writable.
#
# Used by mutmut-nightly.yml (the full run) and check.yml's master job (the
# changed functions after each merge). tests/test_publish_mutmut_state.py
# holds these rules.
printf '%s' jpt-mut-publish > /proc/$$/comm 2>/dev/null
set -euo pipefail
state=${1:?usage: publish-mutmut-state.sh STATE_DIR}
if [ ! -d mutants ] || [ ! -f baseline.json ]; then
  echo "publish-mutmut-state: no mutants/ and baseline.json here: nothing published" >&2
  exit 2
fi
if ! touch "$state/.write-probe-$$" 2>/dev/null; then
  echo "publish-mutmut-state: $state is not writable: nothing published" >&2
  exit 2
fi
rm -f "$state/.write-probe-$$"
next=$state/next
rm -rf "$next"
mkdir -p "$next"
stray=$(mktemp)
trap 'rm -f "$stray"' EXIT
git -C mutants status --porcelain --untracked-files=all \
  | sed -n 's/^?? //p' \
  | { grep -v -e '\.meta$' -e '\.spans$' -e '^mutmut-stats\.json$' || true; } \
  | sed 's|^|mutants/|' > "$stray"
echo "publish-mutmut-state: left out, written by mutants: $(wc -l < "$stray") path(s)"
tar -cf - --exclude=mutants/.git --exclude-from="$stray" mutants baseline.json | tar -C "$next" -xf -
chmod -R go-w "$next"
# An old/ a dead run left is no obstacle: current moves inside it, and both go.
if [ -d "$state/current" ]; then mv "$state/current" "$state/old"; fi
mv "$next" "$state/current"
rm -rf "$state/old"
echo "publish-mutmut-state: $(du -sh "$state/current" | cut -f1) in $state/current"
