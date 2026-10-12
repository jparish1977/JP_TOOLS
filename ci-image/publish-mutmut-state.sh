#!/bin/bash
# ci-image/publish-mutmut-state.sh -- put this checkout's mutmut state into the
# runner volume as its new current state, whole or not at all.
#
#   bash ci-image/publish-mutmut-state.sh STATE_DIR     (from the checkout)
#
# Takes ./mutants and ./baseline.json. The runner runs jobs side by side, so:
# - Publishers take STATE_DIR/.lock exclusively (flock): two never interleave.
# - Each publish is a new directory, STATE_DIR/v-<UTC time>-<pid>, never
#   changed once written; STATE_DIR/current is a symlink to it, replaced in
#   one rename. A reader (fetch-mutmut-state.sh) resolves the link once and
#   copies that directory, so it gets the old state or the new one whole.
# - The version current names and the one before it are kept; older ones go.
#   A reader mid-copy of the one before is not cut short.
# A current that is still a plain directory (the layout before this) is
# moved to a version first.
#
# From mutants/ it takes the files git tracks there and mutmut's own (.meta,
# .spans, mutmut-stats.json), so mutmut-gate's .git link, the linked installs
# and anything a mutant wrote stay out. The list goes to tar NUL-separated
# with -T, so no name is quoted or read as a pattern (GNU tar does not read
# --exclude-from NUL-separated: a name after the first was kept, measured).
#
# A CI checkout is made under umask 0 and tar keeps modes, so the state is
# made not group- or world-writable before it goes in.
#
# Exit: 0 published; 2 nothing to publish, or STATE_DIR not writable.
#
# Used by mutmut-nightly.yml (the full run) and check.yml's mutation-state job
# (the changed functions after each merge). tests/test_publish_mutmut_state.py
# holds these rules.
printf '%s' jpt-mut-publish > /proc/$$/comm 2>/dev/null
set -euo pipefail
state=${1:?usage: publish-mutmut-state.sh STATE_DIR}
if [ ! -d mutants ] || [ ! -f baseline.json ]; then
  echo "publish-mutmut-state: no mutants/ and baseline.json here: nothing published" >&2
  exit 2
fi
if ! { exec 9>>"$state/.lock"; } 2>/dev/null; then
  echo "publish-mutmut-state: $state is not writable: nothing published" >&2
  exit 2
fi
flock -x 9
version=v-$(date -u +%Y%m%dT%H%M%SZ)-$$
dir=$state/$version
mkdir "$dir"
keep=$(mktemp)
trap 'rm -f "$keep"' EXIT
# What goes in is listed, not what stays out: the files git tracks there that
# mutmut wrote, and mutmut's own, NUL-separated and read literally by -T.
mutmuts() { grep -z -e '\.meta$' -e '\.spans$' -e '^mutmut-stats\.json$' || true; }
{ git -C mutants ls-files -z; git -C mutants ls-files -z --others | mutmuts; } \
  | while IFS= read -r -d '' f; do
      if [ -e "mutants/$f" ]; then printf 'mutants/%s\0' "$f"; fi
    done > "$keep"
printf 'baseline.json\0' >> "$keep"
left=$(git -C mutants ls-files -z --others | { grep -z -v -e '\.meta$' -e '\.spans$' -e '^mutmut-stats\.json$' || true; } | tr -cd '\0' | wc -c)
echo "publish-mutmut-state: left out, written by mutants: $left path(s)"
tar -cf - --null --no-wildcards -T "$keep" | tar -C "$dir" -xf -
chmod -R go-w "$dir"
if [ -d "$state/current" ] && [ ! -L "$state/current" ]; then
  mv "$state/current" "$state/v-0-before-versions"
fi
ln -sfn "$version" "$state/current.new"
mv -T "$state/current.new" "$state/current"
find "$state" -mindepth 1 -maxdepth 1 -name 'v-*' -printf '%f\n' | sort | head -n -2 \
  | while IFS= read -r old; do rm -rf "${state:?}/$old"; done
echo "publish-mutmut-state: $(du -sh "$dir" | cut -f1) in $dir, now current"
