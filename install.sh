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
# extension, `pwsh:MODULE` a PowerShell module, anything else a command on PATH.
present() {
    case "$1" in
        php:*) command -v php >/dev/null 2>&1 && php -m | grep -qix "${1#php:}" ;;
        pwsh:*) command -v pwsh >/dev/null 2>&1 &&
            pwsh -NoProfile -Command "if (Get-Module -ListAvailable '${1#pwsh:}') { exit 0 } else { exit 1 }" ;;
        *) command -v "$1" >/dev/null 2>&1 ;;
    esac
}

# How to install an entry that is missing: a module from the PowerShell
# Gallery at its pin, anything else from apt.
how_to_install() {
    case "$1" in
        pwsh:*) echo "pwsh -c 'Install-Module ${1#pwsh:} -RequiredVersion $2 -Scope CurrentUser'" ;;
        *) echo "sudo apt install $3" ;;
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
            echo "    MISSING: $tool (tested on $min). Install: $(how_to_install "$tool" "$min" "$apt_pkg")"
        elif [ "${tool#php:}" != "$tool" ]; then
            echo "    $tool: loaded (tested on PHP $min)"
        elif [ "${tool#pwsh:}" != "$tool" ]; then
            echo "    $tool: installed (pinned at $min)"
        else
            echo "    $tool: $("$tool" --version 2>/dev/null | head -n 2 | tail -n 1) (tested on $min)"
        fi
    done < "$here/requirements-system.txt"
}

# composer.json requires find-dupes.php's private libraries, which need PHP
# extensions and their repositories in the global composer config; without
# either, this stops. The system-tools report runs first, so a missing
# extension is already on the screen, with its package, when it does.
install_php_tools() {
    if ! command -v composer &>/dev/null || ! command -v php &>/dev/null; then
        echo "    php or composer not found: skipping PHPStan/phpcs/Rector"
        echo "    Install PHP and Composer then re-run: composer install"
        return
    fi
    composer --working-dir="$here" install --no-interaction || {
        echo "    composer install failed. A php:EXT reported MISSING above is"
        echo "    installed with the package named there. If it stopped on"
        echo "    iteration8/* instead, see the README, 'Private libraries:"
        echo "    find-dupes.php', for the two repositories."
        exit 1
    }
}

echo ""
echo "==> System tools (requirements-system.txt: apt, not pip/npm/composer)"
report_system_tools

echo ""
echo "==> PHP tools (requires php + composer)"
install_php_tools

echo ""
echo "Done. Usage:"
echo "  python3 JP_TOOLS/check.py <path> [--pretty]"
echo "  python3 JP_TOOLS/fix.py   <path> [--dry-run]"
