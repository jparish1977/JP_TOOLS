#!/bin/bash
# ci-image/mutation-tools.sh -- every tool a mutmut run needs, for the three
# jobs that run one: check.yml's mutation (a PR) and mutation-state (a merge),
# and mutmut-nightly.yml. One script, so the three cannot drift apart.
#
#   FLEET_READ_TOKEN=... OWNER=... SERVER=... bash ci-image/mutation-tools.sh
#
# The tests mutmut runs need the Python pins (requirements-dev.txt), the JS
# tools (npm), PowerShell with PSScriptAnalyzer (install-pwsh.sh) and the PHP
# tools (install-php-tools.sh, which takes the three variables). Run from the
# checkout, after setup-python and setup-node.
#
# Exit: 0 all in place; otherwise the failing step's own status and message.
printf '%s' jpt-mut-tools > /proc/$$/comm 2>/dev/null
set -euo pipefail
pip install -r requirements-dev.txt
npm install
bash install-pwsh.sh
bash ci-image/install-php-tools.sh
