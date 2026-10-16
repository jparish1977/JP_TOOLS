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
#     partial stage (git add -p) those differ. Where quality-baseline.json
#     exists the count is also written into it, so there a file with unstaged
#     changes as well is refused rather than measured.
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

# Directory $1 as a physical path, symlinks resolved; nothing when it is not one.
physical() {
    (cd "$1" 2>/dev/null && pwd -P)
}

# The check.py the staged files are linted with: TOOLS_DIR's, except for a
# commit in JP_TOOLS itself, from a worktree of the repository TOOLS_DIR is
# (the same git common directory), which is linted by that worktree's own
# check.py and so its own configs: a branch changing configs/ruff.toml is
# held to the rules it brings, not master's. The em-dash and scratch checks
# stay TOOLS_DIR's, so a branch cannot weaken them; the lint does not: a
# branch's own check.py and configs decide whether its commits pass here, so
# this hook cannot hold a branch to master's lint rules. CI is the gate that
# does. Paths are compared physically, so a symlinked one still matches.
# TOOLS_DIR's own checkout already runs its check.py, so it is passed over.
choose_check_py() {
    CHECK_PY="$TOOLS_DIR/check.py"
    top=$(git rev-parse --show-toplevel) || return 0
    [ -f "$top/check.py" ] || return 0
    [ "$(physical "$top")" = "$(physical "$TOOLS_DIR")" ] && return 0
    ours=$(physical "$(git rev-parse --path-format=absolute --git-common-dir)")
    theirs=$(physical "$(tools_git rev-parse --path-format=absolute --git-common-dir)")
    [ -n "$ours" ] && [ "$ours" = "$theirs" ] || return 0
    CHECK_PY="$top/check.py"
    echo "JP_TOOLS: a commit in JP_TOOLS itself: linting with this worktree's check.py ($top), its own configs." >&2
}

# A tree at master can still lack a flag this template passes, if the two are
# ever out of step. Ask check.py, and refuse rather than let "unrecognized
# arguments" read as a failing check.
check_flags() {
    HELP=$("$PY" "$CHECK_PY" --help 2>/dev/null)
    NEED="--skip-unsupported"
    [ -f "$BASELINE_FILE" ] && NEED="--skip-unsupported --baseline --touched --raised-over"
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

# NOTHING RELIED ON RUNS FROM SCRATCH (METHODOLOGY 2.11, rule 6). Every staged
# file is read whole, as staged, and one naming a scratch path refuses the
# commit. There is no variable that lets it through: commit the thing the
# file points at, and point at that.
# FAILS CLOSED, as the em-dash check: 0 none, 1 listed, anything else BROKEN.
# The refusal is scratch-refs.py's own (--hook), so this hook and the global
# one (hooks/global-dispatch.sh) print the same words.
check_scratch_refs() {
    "$PY" "$TOOLS_DIR/scratch-refs.py" --hook
    refs_rc=$?
    if [ "$refs_rc" -eq 1 ]; then
        exit 1
    elif [ "$refs_rc" -ne 0 ]; then
        broken "the scratch check (scratch-refs.py --hook) exited $refs_rc, so it did not run." \
            "run it by hand to see why: $PY \"$TOOLS_DIR/scratch-refs.py\" --check"
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

# Each staged file through check.py; sets rc (1 on any failure), checked,
# skipped, one reason per line for each file check.py skipped, and held, one
# line per file check.py found no worse but not lowered (jpexit.NOT_LOWERED,
# 3), naming it and why, which the commit message decides: see LINT-DEBT-OK below.
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
    held=""
    for f in $STAGED; do
        [ -f "$f" ] || continue     # staged then removed from the tree
        # The working copy is what is measured, and with a baseline its count
        # is written back and committed: with unstaged changes as well, that
        # would be a count the committed file does not have.
        if [ -f "$BASELINE_FILE" ] && ! git diff --quiet -- "$f"; then
            echo ""
            echo " REFUSED $f: it has unstaged changes as well as staged ones. The gate"
            echo " measures the working copy and writes its count into quality-baseline.json,"
            echo " so it would commit a count this commit's $f does not have."
            echo " Fix: stage all of it, or set the rest aside first:"
            echo "   git stash push --keep-index && git commit ... && git stash pop"
            rc=1
            continue
        fi
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
        if [ "$file_rc" -eq 3 ]; then
            held="$held$f: changed, and fixed none of its recorded findings
"
        elif [ "$file_rc" -ne 0 ]; then
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
    git diff --quiet -- "$BASELINE_FILE" && return 0
    # `git commit <path>` commits from a temporary index, so the baseline the
    # hook lowered and staged reaches the commit but not the index: the index
    # keeps the older copy while the working copy equals HEAD's. Staging it
    # stages no change to the commit, and puts the index back in step.
    if git diff --quiet HEAD -- "$BASELINE_FILE" 2>/dev/null; then
        git add -- "$BASELINE_FILE" || broken "could not stage $BASELINE_FILE to bring the index back to HEAD's copy." \
            "stage it by hand: git add quality-baseline.json"
        echo "JP_TOOLS: the index held an older quality-baseline.json than HEAD (a \`git commit <path>\` leaves it so); staged HEAD's copy." >&2
        return 0
    fi
    echo ""
    echo "====================================="
    echo " Pre-commit check REFUSED: quality-baseline.json has unstaged changes."
    echo " The gate writes lowered counts into it and stages it with the commit,"
    echo " which would commit your unstaged edit too."
    echo " Fix: stage it (git add quality-baseline.json) or discard the edit."
    echo "====================================="
    exit 1
}

# RAISED. The baseline may only fall: a staged quality-baseline.json that
# raises any count over HEAD's is held like a file below. It is how a re-record
# commits, and also how `git restore quality-baseline.json` after a path
# commit (which puts the index's older copy back) would silently undo a gain.
# Compared as staged before this run: the run itself only lowers counts.
check_raised() {
    [ -n "$PRIOR_BASELINE" ] || return 0
    head_blob=$(git rev-parse -q --verify "HEAD:quality-baseline.json" 2>/dev/null) || return 0
    [ "$head_blob" = "$PRIOR_BASELINE" ] && return 0
    old=$(git rev-parse --git-path jp-tools-baseline-head.json)
    new=$(git rev-parse --git-path jp-tools-baseline-staged.json)
    { git cat-file blob "$head_blob" > "$old" && git cat-file blob "$PRIOR_BASELINE" > "$new"; } \
        || broken "could not read HEAD's and the staged quality-baseline.json." \
            "run git cat-file blob HEAD:quality-baseline.json by hand to see why."
    raised=$("$PY" "$CHECK_PY" "$new" --raised-over "$old" 2>&1)
    raised_rc=$?
    rm -f "$old" "$new"
    case $raised_rc in
        0) ;;
        1)
            printf '%s\n' "$raised"
            echo " If this is \`git restore\` putting back an older copy, stage HEAD's instead:"
            echo "   git checkout HEAD -- quality-baseline.json"
            held="${held}quality-baseline.json: raises a count over HEAD's (above)
" ;;
        *) broken "check.py --raised-over exited $raised_rc, so the staged baseline was not compared with HEAD's: $raised" \
            "run it by hand: $PY \"$CHECK_PY\" NEW.json --raised-over OLD.json" ;;
    esac
}

# LINT-DEBT-OK. A file that is no worse but fixed none of its recorded
# findings is held rather than failed, and so is a baseline that raises a
# count; the commit message decides: hooks/commit-msg.sh lets them through on
# a line 'LINT-DEBT-OK: <reason>', so the reason is kept in the history, and
# refuses the commit otherwise. Only these are waived; a file that got worse
# fails here and no line clears it.
# The hold is DEBT_FILE: the tree of the index being committed, the baseline
# blob as it was staged before this run (for commit-msg to put back), then a
# line per held item, naming it and why. The tree ties it to this commit, so a
# hold left by a commit that never reached its message is not read by a later one.
# The hook git runs is core.hooksPath's, else the repository's own. A global
# hook (install-global-hooks.py) is a stub running global-dispatch.sh, which
# runs the repository's own in turn, so behind a stub the repository's own is
# the one that reads the line. git runs only an executable hook.
commit_msg_hook_installed() {
    hooks=$(git config --get core.hooksPath) || hooks=$(git rev-parse --git-path hooks)
    hook="$hooks/commit-msg"
    if grep -q "/hooks/global-dispatch.sh\" commit-msg " "$hook" 2>/dev/null; then
        hook="$(git rev-parse --git-common-dir)/hooks/commit-msg"
    fi
    [ -x "$hook" ] && grep -q "^# JP_TOOLS commit-msg hook" "$hook" 2>/dev/null
}

hold_needs_commit_msg() {
    [ -n "$held" ] && [ "$rc" -eq 0 ] || return 0
    commit_msg_hook_installed && return 0
    echo ""
    echo " REFUSED: these need a reason in the message:"
    printf '%s' "$held" | sed 's/^/   /'
    echo " A 'LINT-DEBT-OK: <reason>' line in the message would let them through,"
    echo " but this repo has no JP_TOOLS commit-msg hook to read it."
    echo " Fix: python3 \"$TOOLS_DIR/install-hooks.py\" ."
    rc=1
}

write_hold() {
    [ -n "$held" ] && [ "$rc" -eq 0 ] || return 0
    tree=$(git write-tree) || broken "git write-tree failed, so the LINT-DEBT-OK hold cannot be recorded." \
        "run git write-tree by hand to see why."
    { echo "$tree"; echo "$PRIOR_BASELINE"; printf '%s' "$held"; } > "$DEBT_FILE" \
        || broken "could not write $DEBT_FILE." "check that the git directory is writable."
    echo "JP_TOOLS: held for a reason in the message:"
    printf '%s' "$held" | sed 's/^/   /'
    echo "  The commit goes through only with a line 'LINT-DEBT-OK: <reason>' in its message."
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
        case "${GIT_INDEX_FILE##*/}" in
            next-index-*)
                echo "  This is \`git commit <path>\`: git does not carry it into your index, so"
                echo "  \`git status\` shows it changed there until your next commit, which stages"
                echo "  HEAD's copy (or run: git add quality-baseline.json)." ;;
        esac
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
    state=clean
    [ -n "$held" ] && state="none worse, $(printf '%s' "$held" | wc -l | tr -d ' ') held for LINT-DEBT-OK"
    if [ "$checked" -eq 0 ] && [ -n "$summary" ]; then
        echo "JP_TOOLS: 0 of $total staged file(s) checked, nothing here to check ($summary)."
    elif [ -n "$summary" ]; then
        echo "JP_TOOLS: $checked of $total staged file(s) checked, $state ($summary)."
    else
        echo "JP_TOOLS: $checked of $total staged file(s) checked, $state."
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
choose_check_py
DEBT_FILE=$(git rev-parse --git-path jp-tools-lint-debt)
rm -f "$DEBT_FILE"
check_flags
check_emdashes
check_scratch_refs
STAGED=$(git diff --cached --name-only --diff-filter=ACM)
[ -z "$STAGED" ] && exit 0
baseline_must_be_clean
PRIOR_BASELINE=$(git rev-parse -q --verify ":quality-baseline.json" 2>/dev/null)
check_staged
check_raised
hold_needs_commit_msg
settle_baseline
write_hold
verdict
