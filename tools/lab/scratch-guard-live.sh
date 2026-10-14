#!/bin/bash
# tools/lab/scratch-guard-live.sh -- is the scratch commit guard live on this
# box: a real commit through the hook install-hooks.py installs, using the
# JP_TOOLS checkout every repo's hook runs (~/projects/JP_TOOLS).
#
#   bash tools/lab/scratch-guard-live.sh
#
# ANSWERS: does a commit naming a scratch path get refused here, today.
# In a throwaway repo: a clean file commits (the control), then a file naming
# a scratch path must be refused with the guard's own message. Prints
# LIVE or NOT LIVE and exits 0 or 1.
#
# TESTED AGAINST: the Air and iteration8 at ff294ff, the commit that added the
# guard: both LIVE, the clean control commit going through on each.
# NOT TESTED AGAINST: a checkout from before the guard (the NOT LIVE case has
# not been seen), or Windows, where the hook runs from another checkout path.
printf '%s' jpt-guard-live > /proc/$$/comm 2>/dev/null
set -u
tools=${JP_TOOLS_DIR:-$HOME/projects/JP_TOOLS}
repo=$(mktemp -d)
trap 'rm -rf "$repo"' EXIT
g() { git -C "$repo" -c user.email=t@example.invalid -c user.name=t -c commit.gpgsign=false "$@"; }
g init -q
python3 "$tools/install-hooks.py" "$repo" > /dev/null
echo clean > "$repo/a.md"
g add a.md
if ! g commit -q -m control > /dev/null 2>&1; then
  echo "NOT LIVE: the control commit was refused, so the hook itself is not working"; exit 1
fi
printf 'run ~/%s/x.sh\n' "scr""atch" > "$repo/b.md"
g add b.md
if ! out=$(g commit -m "relies on scratch" 2>&1) && [[ $out == *"relies on scratch:"* ]]; then
  echo "LIVE: $(git -C "$tools" rev-parse --short HEAD) refused the commit naming a scratch path"; exit 0
fi
echo "NOT LIVE at $(git -C "$tools" rev-parse --short HEAD): the commit naming a scratch path went through"
exit 1
