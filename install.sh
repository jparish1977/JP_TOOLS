#!/bin/bash
# JP_TOOLS install: sets up the code quality tools check.py runs, from the
# repo's own manifests (requirements-dev.txt, package.json, composer.json).
set -e
here=$(cd "$(dirname "$0")" && pwd)

echo "==> Python tools (pinned in requirements-dev.txt)"
pip install -r "$here/requirements-dev.txt"

echo ""
echo "==> Node tools (requires npm)"
if command -v npm &>/dev/null; then
    npm --prefix "$here" install
else
    echo "    npm not found: skipping eslint/prettier/stylelint"
    echo "    Install Node.js from https://nodejs.org then re-run this script"
fi

echo ""
echo "==> PHP tools (requires php + composer)"
if command -v composer &>/dev/null && command -v php &>/dev/null; then
    composer --working-dir="$here" install --no-interaction
else
    echo "    php or composer not found: skipping PHPStan/phpcs/Rector"
    echo "    Install PHP and Composer then re-run: composer install"
fi

echo ""
echo "==> System tools (requirements-system.txt: apt, not pip/npm/composer)"
# Reported, not installed: installing needs sudo, which is the operator's
# call. A missing tool here is an arm of check.py that will exit 2 as not
# run until it is installed.
while read -r tool min apt_pkg _win; do
    case "$tool" in ''|'#'*) continue ;; esac
    if command -v "$tool" >/dev/null 2>&1; then
        echo "    $tool: $("$tool" --version 2>/dev/null | head -n 2 | tail -n 1) (tested on $min)"
    else
        echo "    MISSING: $tool (tested on $min). Install: sudo apt install $apt_pkg"
    fi
done < "$here/requirements-system.txt"

echo ""
echo "Done. Usage:"
echo "  python3 JP_TOOLS/check.py <path> [--pretty]"
echo "  python3 JP_TOOLS/fix.py   <path> [--dry-run]"
