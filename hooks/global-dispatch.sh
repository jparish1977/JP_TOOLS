#!/bin/sh
# JP_TOOLS global hook DISPATCHER. Every hook in the directory that
# install-global-hooks.py points git's global core.hooksPath at is two lines
# running this with its own name, so every repository on the box, every clone
# and every `git init` to come, runs through here.
#
#   sh hooks/global-dispatch.sh HOOK_NAME [git's arguments]
#
# pre-commit: scratch-refs.py --hook first (METHODOLOGY 2.11, rule 6). A
# commit naming a scratch path is refused, and so is every commit when the
# check cannot run: it FAILS CLOSED, with no variable that lets one through.
#
# Then, for every hook name, the repository's own hook, when it has one: a
# global core.hooksPath replaces .git/hooks, so without this a repository's
# own hooks (the JP_TOOLS shim, projectbook's, romtools') would stop running.
# The repository's hook gets git's arguments and stdin unchanged, and its
# exit status is the hook's.
#
# A repository whose LOCAL core.hooksPath is set never reaches here: git reads
# the local setting first. install-global-hooks.py names each such repository
# it finds, and that repository's own hooks must run scratch-refs.py --hook.
name=$1
shift
tools=$(cd "$(dirname "$0")/.." && pwd)
if [ "$name" = pre-commit ]; then
    PY=python3
    command -v "$PY" >/dev/null 2>&1 || PY=python
    "$PY" "$tools/scratch-refs.py" --hook
    rc=$?
    if [ "$rc" -eq 1 ]; then
        exit 1
    elif [ "$rc" -ne 0 ]; then
        echo ""
        echo "====================================="
        echo " JP_TOOLS global hook is BROKEN: the scratch check ($tools/scratch-refs.py --hook) exited $rc."
        echo " NOTHING WAS CHECKED, so the commit is refused. Fix: run it by hand to see why:"
        echo "   $PY \"$tools/scratch-refs.py\" --check"
        echo "====================================="
        exit 1
    fi
fi
own="$(git rev-parse --git-common-dir)/hooks/$name"
if [ -f "$own" ] && [ -x "$own" ]; then
    exec "$own" "$@"
fi
exit 0
