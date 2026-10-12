#!/bin/sh
# JP_TOOLS commit-msg TEMPLATE. Run by the shim that install-hooks.py writes
# into each repo's .git/hooks/commit-msg, never installed itself. One copy,
# here, as for hooks/pre-commit.sh: a change reaches every repo at its next
# commit with no reinstall.
#
# INTERFACE: the shim exports TOOLS_DIR and JP_TOOLS_HOOK_API, and passes on
# git's one argument, the file holding the proposed message.
#
# Refuses a message that carries a link to a Claude Code session
# (claude.ai/code/session_...). Claude Code run from claude.ai appends one to
# its commits; it points at a private session, and a commit message is
# published with the repository and kept in its history for good. Every line
# of the message is read, comments included, down to git's scissors line:
# below it `git commit -v` puts the staged diff, which git cuts off before
# the message is saved, so a change to a file that mentions such a link is
# not refused for it.
#
# Also reads a LINT-DEBT-OK line for files the pre-commit hook held; see below.

TEMPLATE_API=1

broken() {
    echo ""
    echo "====================================="
    echo " JP_TOOLS commit-msg hook is BROKEN: $1"
    echo " NOTHING WAS CHECKED. This is not a problem with your message."
    echo " Fix: $2"
    echo "====================================="
    exit 1
}

if [ "$JP_TOOLS_HOOK_API" != "$TEMPLATE_API" ]; then
    broken "this repo's installed hook speaks interface '${JP_TOOLS_HOOK_API:-none}', and the JP_TOOLS template at $TOOLS_DIR serves $TEMPLATE_API." \
        "reinstall the hook: python3 \"$TOOLS_DIR/install-hooks.py\" --remove . && python3 \"$TOOLS_DIR/install-hooks.py\" ."
fi

msg_file=$1
if [ -z "$msg_file" ] || [ ! -f "$msg_file" ]; then
    broken "git passed no readable message file ('${msg_file}')." \
        "reinstall the hook so its shim passes git's argument on."
fi

# The scissors line is the comment character, a space, 24 dashes, " >8 " and
# 24 dashes; any comment character is allowed. Lines keep their numbers.
found=$(sed '/^. -\{24\} >8 -\{24\}$/,$d' "$msg_file" | grep -n -i -E 'claude\.ai/code/session_')
if [ -n "$found" ]; then
    echo ""
    echo "====================================="
    echo " JP_TOOLS: commit REFUSED: the message links a Claude Code session."
    echo " A session link points at a private session and would be published"
    echo " with this repository's history. Line(s):"
    echo "$found" | while IFS= read -r line; do
        echo "   $line"
    done
    echo " Fix: delete those line(s) from the message and commit again; if a"
    echo " tool adds them, configure its commit attribution not to."
    echo "====================================="
    exit 1
fi

# LINT-DEBT-OK. hooks/pre-commit.sh holds a file that is no worse but fixed
# none of its recorded findings, and records the hold (see its LINT-DEBT-OK
# note): the index's tree, the baseline blob staged before it ran, the paths.
# A line 'LINT-DEBT-OK: <reason>' in the message lets them through, and the
# reason stays in the history. Without one the commit is refused and the
# baseline put back as it was staged, as a failing pre-commit does. A hold
# for another tree was left by a commit that never reached its message.
refuse_lint_debt() {
    top=$(git rev-parse --show-toplevel)
    if [ -n "$prior" ]; then
        git cat-file blob "$prior" > "$top/quality-baseline.json" \
            && git add -- "$top/quality-baseline.json"
    fi
    echo ""
    echo "====================================="
    echo " JP_TOOLS: commit REFUSED: these need a reason in the message, and it"
    echo " gives none:"
    echo "$held" | sed 's/^/   /'
    echo " Fix: fix at least one finding in each file (and leave the baseline no"
    echo " higher than HEAD's), or add a line to the message:"
    echo "   LINT-DEBT-OK: <why this change cannot>"
    echo "====================================="
    exit 1
}

check_lint_debt() {
    debt_file=$(git rev-parse --git-path jp-tools-lint-debt)
    [ -f "$debt_file" ] || return 0
    held_tree=$(head -n 1 "$debt_file")
    prior=$(head -n 2 "$debt_file" | tail -n 1)
    held=$(tail -n +3 "$debt_file")
    rm -f "$debt_file"
    [ "$held_tree" = "$(git write-tree 2>/dev/null)" ] || return 0
    if sed '/^. -\{24\} >8 -\{24\}$/,$d' "$msg_file" | grep -q -E '^LINT-DEBT-OK:[[:space:]]*[^[:space:]]'; then
        echo "JP_TOOLS: LINT-DEBT-OK in the message; committed without fixing a finding in:"
        echo "$held" | sed 's/^/   /'
        return 0
    fi
    refuse_lint_debt
}

check_lint_debt
exit 0
