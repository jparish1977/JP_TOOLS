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

# Whether a requirements-system.txt entry is present: `php:EXT` is a PHP
# extension, anything else a command on PATH.
present() {
    case "$1" in
        php:*) command -v php >/dev/null 2>&1 && php -m | grep -qix "${1#php:}" ;;
        *) command -v "$1" >/dev/null 2>&1 ;;
    esac
}

# Reported, not installed: installing needs sudo, which is the operator's
# call. A missing tool here is an arm of check.py that will exit 2 as not
# run until it is installed.
report_system_tools() {
    local tool min apt_pkg _win
    while read -r tool min apt_pkg _win; do
        case "$tool" in ''|'#'*) continue ;; esac
        if ! present "$tool"; then
            echo "    MISSING: $tool (tested on $min). Install: sudo apt install $apt_pkg"
        elif [ "${tool#php:}" != "$tool" ]; then
            echo "    $tool: loaded (tested on PHP $min)"
        else
            echo "    $tool: $("$tool" --version 2>/dev/null | head -n 2 | tail -n 1) (tested on $min)"
        fi
    done < "$here/requirements-system.txt"
}

echo ""
echo "==> PHP tools (requires php + composer)"
if command -v composer &>/dev/null && command -v php &>/dev/null; then
    # composer.json requires find-dupes.php's private libraries: without
    # their repositories in the global composer config, this stops on them.
    composer --working-dir="$here" install --no-interaction || {
        echo "    composer install failed. If it stopped on the build server/*, see the"
        echo "    README, 'Private libraries: find-dupes.php', for the two repositories."
        exit 1
    }
else
    echo "    php or composer not found: skipping PHPStan/phpcs/Rector"
    echo "    Install PHP and Composer then re-run: composer install"
fi

echo ""
echo "==> System tools (requirements-system.txt: apt, not pip/npm/composer)"
report_system_tools

echo ""
echo "Done. Usage:"
echo "  python3 JP_TOOLS/check.py <path> [--pretty]"
echo "  python3 JP_TOOLS/fix.py   <path> [--dry-run]"
