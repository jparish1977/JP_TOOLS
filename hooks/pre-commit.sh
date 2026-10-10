#!/bin/sh
# JP_TOOLS pre-commit TEMPLATE. Run by the shim that install-hooks.py writes
# into each repo's .git/hooks/pre-commit, never installed itself.
#
# ONE COPY, HERE. The installed hook is a shim that runs this file from the
# JP_TOOLS tree it resolved, so a change here reaches every repo at its next
# commit with no reinstall. A copy taken at install time would go stale with
# nothing to say so.
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

check_interface() {
    if [ "$JP_TOOLS_HOOK_API" != "$TEMPLATE_API" ]; then
        broken "this repo's installed hook speaks interface '${JP_TOOLS_HOOK_API:-none}', and the JP_TOOLS template at $TOOLS_DIR serves $TEMPLATE_API." \
            "reinstall the hook: python3 \"$TOOLS_DIR/install-hooks.py\" --remove . && python3 \"$TOOLS_DIR/install-hooks.py\" ."
    fi
}

# python3 everywhere it exists; several Linux boxes ship no bare `python`,
# and Windows installs it under that name only.
choose_python() {
    PY=python3
    command -v "$PY" >/dev/null 2>&1 || PY=python
}

# JP_TOOLS' repository, read with the committing repo's git environment
# cleared: in a linked worktree GIT_DIR is set, and it outranks `git -C`.
tools_git() {
    (unset GIT_DIR GIT_WORK_TREE GIT_INDEX_FILE GIT_COMMON_DIR; git -C "$TOOLS_DIR" "$@")
}

# A tree at master can still lack a flag this template passes, if the two are
# ever out of step. Ask check.py, and refuse rather than let "unrecognized
# arguments" read as a failing check.
check_flags() {
    HELP=$("$PY" "$CHECK_PY" --help 2>/dev/null)
    NEED="--skip-unsupported"
    [ -f "$BASELINE_FILE" ] && NEED="--skip-unsupported --baseline --touched"
    MISSING=""
    for flag in $NEED; do
        case "$HELP" in
            *"$flag"*) ;;
            *) MISSING="$MISSING $flag" ;;
        esac
    done
    if [ -n "$MISSING" ]; then
        broken "the check.py at $TOOLS_DIR has no$MISSING (on $(tools_git rev-parse --abbrev-ref HEAD 2>/dev/null || echo '?') $(tools_git rev-parse --short HEAD 2>/dev/null))." \
            "git -C \"$TOOLS_DIR\" pull --ff-only"
    fi
}

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
check_emdashes() {
    if [ -n "$JP_TOOLS_ALLOW_EMDASH" ]; then
        echo "JP_TOOLS hook: JP_TOOLS_ALLOW_EMDASH is set; em-dashes are allowed in this commit." >&2
        return
    fi
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
}

# Why check.py skipped a file, from the one JSON object it prints for a skip
# ({"target": ..., "skipped": "no language detected"}, or "ignored by
# .jp-tools-ignore: ENTRY (WHERE)"): the reason up to its first colon, so one
# kind counts as one. Prints nothing for a file that was checked.
skip_reason() {
    printf '%s' "$1" | "$PY" -c 'import json, sys
try:
    doc = json.load(sys.stdin)
except ValueError:
    sys.exit(0)  # not one JSON object: a report, so the file was checked
if isinstance(doc, dict) and isinstance(doc.get("skipped"), str):
    print(doc["skipped"].split(":")[0])'
}

# Each staged file through check.py; sets rc (1 on any failure), checked, and
# skipped, one reason per line for each file check.py skipped.
#
# Split on newlines only, so paths with spaces survive.
#
# WARNING to whoever adds the next flag to this loop: newline IFS also DISABLES
# word-splitting on spaces, so a variable holding "--flag value" arrives as ONE
# argument, and argparse reports it as unrecognized. Pass flags as separate
# literal arguments, never through a variable.
check_staged() {
    OLDIFS=$IFS
    IFS='
'
    rc=0
    checked=0
    skipped=""
    for f in $STAGED; do
        [ -f "$f" ] || continue     # staged then removed from the tree
        # --skip-unsupported: a staged README or JSON file exits 0 rather than 2,
        # and says it was skipped, which is how it is told from a checked file.
        if [ -f "$BASELINE_FILE" ]; then
            out=$("$PY" "$CHECK_PY" "$f" --baseline "$BASELINE_FILE" --touched --pretty --skip-unsupported)
        else
            out=$("$PY" "$CHECK_PY" "$f" --pretty --skip-unsupported)
        fi
        file_rc=$?
        printf '%s\n' "$out"
        why=""
        if [ "$file_rc" -ne 0 ]; then
            rc=1
        else
            why=$(skip_reason "$out")
        fi
        if [ -n "$why" ]; then
            skipped="$skipped$why
"
        else
            checked=$((checked + 1))
        fi
    done
    IFS=$OLDIFS
}

# "4 skipped: no language detected" when every skip has one reason, "4
# skipped: 3 no language detected, 1 ignored by .jp-tools-ignore" when they
# differ, and nothing when no file was skipped.
skipped_summary() {
    [ -n "$skipped" ] || return 0
    n=$(printf '%s' "$skipped" | wc -l | tr -d ' ')
    counted=$(printf '%s' "$skipped" | sort | uniq -c)
    if [ "$(printf '%s\n' "$counted" | wc -l)" -eq 1 ]; then
        echo "$n skipped: $(printf '%s' "$skipped" | head -n 1)"
        return 0
    fi
    kinds=$(printf '%s\n' "$counted" | while read -r count why; do
        printf '%s %s, ' "$count" "$why"
    done)
    echo "$n skipped: ${kinds%, }"
}

# check.py --touched writes a lowered count back to the baseline. The
# baseline must match the index before the run, so what the hook stages
# afterwards is only what check.py wrote, never someone's own unstaged edit.
baseline_must_be_clean() {
    [ -f "$BASELINE_FILE" ] || return 0
    if ! git diff --quiet -- "$BASELINE_FILE"; then
        echo ""
        echo "====================================="
        echo " Pre-commit check REFUSED: quality-baseline.json has unstaged changes."
        echo " The gate writes lowered counts into it and stages it with the commit,"
        echo " which would commit your unstaged edit too."
        echo " Fix: stage it (git add quality-baseline.json) or discard the edit."
        echo "====================================="
        exit 1
    fi
}

# A passing commit carries the lowered baseline with it, so the gain is kept.
# A failing one puts the file back as it was staged: the counts were lowered
# for a commit that is not happening.
settle_baseline() {
    [ -f "$BASELINE_FILE" ] || return 0
    git diff --quiet -- "$BASELINE_FILE" && return 0
    if [ "$rc" -eq 0 ]; then
        git add -- "$BASELINE_FILE" || broken "could not stage the lowered $BASELINE_FILE." \
            "stage it by hand: git add quality-baseline.json"
        echo "JP_TOOLS: quality-baseline.json lowered, and staged with this commit."
    else
        git checkout -- "$BASELINE_FILE"
    fi
}

# The verdict. Say so on success too: a gate that only speaks when it fails
# is indistinguishable from a gate that never ran.
verdict() {
    if [ "$rc" -ne 0 ]; then
        echo ""
        echo "====================================="
        echo " Pre-commit check FAILED"
        echo " Fix the issues above, then re-commit"
        echo " To skip: git commit --no-verify"
        echo "====================================="
        exit 1
    fi
    # The denominator is in the verdict (METHODOLOGY 10.14): a skipped file
    # was not checked, and is never counted as if it had been.
    total=$((checked + $(printf '%s' "$skipped" | wc -l)))
    summary=$(skipped_summary)
    if [ "$checked" -eq 0 ] && [ -n "$summary" ]; then
        echo "JP_TOOLS: 0 of $total staged file(s) checked, nothing here to check ($summary)."
    elif [ -n "$summary" ]; then
        echo "JP_TOOLS: $checked of $total staged file(s) checked, clean ($summary)."
    else
        echo "JP_TOOLS: $checked of $total staged file(s) checked, clean."
    fi
    exit 0
}

check_interface
choose_python
# An inherited repo ratchets against a recorded baseline rather than against
# zero. If one is committed at the repo root, use it. check.py REFUSES a
# baseline recorded in a different mode, so a whole-repo baseline dropped here
# fails loudly instead of reporting scope difference as regression.
BASELINE_FILE="$(git rev-parse --show-toplevel)/quality-baseline.json"
CHECK_PY="$TOOLS_DIR/check.py"
check_flags
check_emdashes
STAGED=$(git diff --cached --name-only --diff-filter=ACM)
[ -z "$STAGED" ] && exit 0
baseline_must_be_clean
check_staged
settle_baseline
verdict
