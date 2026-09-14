#!/bin/bash
# JP_TOOLS install — sets up code quality tools
set -e

echo "==> Python tools"
pip install --upgrade ruff mypy pytest pytest-cov

echo ""
echo "==> Node tools (requires npm)"
if command -v npm &>/dev/null; then
    npm install -g eslint prettier
else
    echo "    npm not found — skipping eslint/prettier"
    echo "    Install Node.js from https://nodejs.org then re-run this script"
fi

echo ""
echo "==> PHP tools (requires php + composer)"
if command -v composer &>/dev/null && command -v php &>/dev/null; then
    composer install --no-interaction
else
    echo "    php or composer not found — skipping PHPStan/phpcs/Rector"
    echo "    Install PHP and Composer then re-run: composer install"
fi

echo ""
echo "==> System tools (requirements-system.txt: apt, not pip/npm/composer)"
# Reported, not installed: installing needs sudo, which is the operator's
# call (joe-MacBookAir prompts for it). A missing tool here is an arm of
# check.py that will exit 2 as not run until it is installed.
here=$(cd "$(dirname "$0")" && pwd)
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
echo "  python JP_TOOLS/check.py <path> [--pretty]"
echo "  python JP_TOOLS/fix.py   <path> [--dry-run]"
