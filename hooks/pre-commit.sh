#!/bin/sh
# JP_TOOLS pre-commit TEMPLATE. Run by the shim that install-hooks.py writes
# into each repo's .git/hooks/pre-commit, never installed itself.
#
# ONE COPY, HERE. The installed hook is a shim that runs this file from the
# JP_TOOLS tree it resolved, so a change here reaches every repo at its next
# commit with no reinstall. A copy taken at install time would go stale with
# nothing to say so. The design is on JP_TOOLS #65.
#
# INTERFACE: the shim exports TOOLS_DIR (the tree it checked) and
# JP_TOOLS_HOOK_API. A shim from another interface is refused, never guessed at.
#
# Runs check.py against the STAGED FILES, one at a time. Staged files, not the
# repo root: a whole-repo gate is only installable on a repo that is already
# clean. Per-file means the bar applies to code you were already editing. See
# METHODOLOGY.md, "Adopting this in a codebase you inherited".
#
# Two limits, stated because neither is visible when it bites:
#   - It checks the file in the WORKING TREE, not the staged blob. With a
#     partial stage (git add -p) those differ.
#   - The file list is split on newlines, so a filename containing a literal
#     newline is not handled. Spaces are.

TEMPLATE_API=1

broken() {
    echo ""
    echo "====================================="
    echo " JP_TOOLS hook is BROKEN: $1"
    echo " NOTHING WAS CHECKED. This is not a problem with your changes."
    echo " Fix: $2"
    echo " To commit without the gate: git commit --no-verify"
    echo "====================================="
    exit 1
}

if [ "$JP_TOOLS_HOOK_API" != "$TEMPLATE_API" ]; then
    broken "this repo's installed hook speaks interface '${JP_TOOLS_HOOK_API:-none}', and the JP_TOOLS template at $TOOLS_DIR serves $TEMPLATE_API." \
        "reinstall the hook: python3 \"$TOOLS_DIR/install-hooks.py\" --remove . && python3 \"$TOOLS_DIR/install-hooks.py\" ."
fi

# python3 everywhere it exists; several Linux boxes ship no bare `python`,
# and Windows installs it under that name only.
PY=python3
command -v "$PY" >/dev/null 2>&1 || PY=python

# An inherited repo ratchets against a recorded baseline rather than against
# zero. If one is committed at the repo root, use it. check.py REFUSES a
# baseline recorded in a different mode, so a whole-repo baseline dropped here
# fails loudly instead of reporting scope difference as regression.
BASELINE_FILE="$(git rev-parse --show-toplevel)/quality-baseline.json"

# A tree at master can still lack a flag this template passes, if the two are
# ever out of step. Ask check.py, and refuse rather than let "unrecognized
# arguments" read as a failing check. #65 part 1 (#73).
CHECK_PY="$TOOLS_DIR/check.py"
HELP=$("$PY" "$CHECK_PY" --help 2>/dev/null)
NEED="--skip-unsupported"
[ -f "$BASELINE_FILE" ] && NEED="--skip-unsupported --baseline"
MISSING=""
for flag in $NEED; do
    case "$HELP" in
        *"$flag"*) ;;
        *) MISSING="$MISSING $flag" ;;
    esac
done
if [ -n "$MISSING" ]; then
    broken "the check.py at $TOOLS_DIR has no$MISSING (on $(git -C "$TOOLS_DIR" rev-parse --abbrev-ref HEAD 2>/dev/null || echo '?') $(git -C "$TOOLS_DIR" rev-parse --short HEAD 2>/dev/null))." \
        "git -C \"$TOOLS_DIR\" pull --ff-only"
fi

# NO NEW EM-DASHES (the fleet's rule, in CLAUDE.md). Only the lines this commit
# ADDS are checked, so text already there is never retrofitted, and the
# refusal gives the one command that fixes them.
# JP_TOOLS_ALLOW_EMDASH=1 lets through a commit that genuinely needs one, and
# it is announced every time, because an exported variable outlives the
# commit it was set for.
# It runs BEFORE the STAGED list below, which is ACM for check.py: a commit
# that only renames files would exit there, and a rename can add lines.
# fix-dashes.py finds its own files.
# FAILS CLOSED: --check exits 0 (none) or 1 (listed). Anything else means the
# check did not run, which is BROKEN, never clean.
if [ -n "$JP_TOOLS_ALLOW_EMDASH" ]; then
    echo "JP_TOOLS hook: JP_TOOLS_ALLOW_EMDASH is set; em-dashes are allowed in this commit." >&2
else
    DASHED=$("$PY" "$TOOLS_DIR/fix-dashes.py" --check)
    dash_rc=$?
    if [ "$dash_rc" -eq 1 ] && [ -n "$DASHED" ]; then
        echo ""
        echo "====================================="
        echo " Pre-commit check FAILED: this commit adds em-dashes:"
        echo "$DASHED"
        echo " Fix: python3 \"$TOOLS_DIR/fix-dashes.py\"   (changes only those lines, re-stages them)"
        echo " If one is genuinely needed: JP_TOOLS_ALLOW_EMDASH=1 git commit ..."
        echo "====================================="
        exit 1
    elif [ "$dash_rc" -ne 0 ]; then
        broken "the em-dash check (fix-dashes.py --check) exited $dash_rc, so it did not run." \
            "run it by hand to see why: $PY \"$TOOLS_DIR/fix-dashes.py\" --check"
    fi
fi

STAGED=$(git diff --cached --name-only --diff-filter=ACM)
if [ -z "$STAGED" ]; then
    exit 0
fi

# Split on newlines only, so paths with spaces survive.
#
# WARNING to whoever adds the next flag to this loop: newline IFS also DISABLES
# word-splitting on spaces, so a variable holding "--flag value" arrives as ONE
# argument, and argparse reports it as unrecognized. Pass flags as separate
# literal arguments, never through a variable.
OLDIFS=$IFS
IFS='
'
rc=0
checked=0
for f in $STAGED; do
    [ -f "$f" ] || continue     # staged then removed from the tree
    # --skip-unsupported: a staged README or JSON file exits 0 rather than 2.
    if [ -f "$BASELINE_FILE" ]; then
        "$PY" "$CHECK_PY" "$f" --baseline "$BASELINE_FILE" --pretty --skip-unsupported || rc=1
    else
        "$PY" "$CHECK_PY" "$f" --pretty --skip-unsupported || rc=1
    fi
    checked=$((checked + 1))
done
IFS=$OLDIFS

if [ $rc -ne 0 ]; then
    echo ""
    echo "====================================="
    echo " Pre-commit check FAILED"
    echo " Fix the issues above, then re-commit"
    echo " To skip: git commit --no-verify"
    echo "====================================="
    exit 1
fi

# Say so on success too. A gate that only speaks when it fails is
# indistinguishable from a gate that never ran.
echo "JP_TOOLS: $checked staged file(s) checked, clean."
exit 0
