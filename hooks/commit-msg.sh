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
# is read, comments included: a link is refused wherever it is.

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

found=$(grep -n -i -E 'claude\.ai/code/session_' "$msg_file")
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
exit 0
