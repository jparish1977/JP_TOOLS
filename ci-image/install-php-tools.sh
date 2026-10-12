#!/bin/bash
# ci-image/install-php-tools.sh -- composer install for JP_TOOLS in CI, every
# job that needs the PHP tools calling this one script.
#
#   FLEET_READ_TOKEN=... OWNER=... SERVER=... bash ci-image/install-php-tools.sh
#
# composer.json requires iteration8/core and iteration8/utilities
# (find-dupes.php's library), private repositories this forge mirrors under
# the owner in the variable ITERATION8_LIBRARIES_OWNER (OWNER here), read with
# the secret FLEET_READ_TOKEN. SERVER is the forge's address (github.server_url).
# Where they are is global config, as on every box, so no forge address is in
# the repo. The token reaches composer in COMPOSER_AUTH, never in argv.
#
# Exit: 0 installed; 1 the token or the owner is not set (saying which to
# set), or composer failed.
printf '%s' jpt-php-tools > /proc/$$/comm 2>/dev/null
set -euo pipefail
if [ -z "${FLEET_READ_TOKEN:-}" ] || [ -z "${OWNER:-}" ] || [ -z "${SERVER:-}" ]; then
  echo "install-php-tools: set the secret FLEET_READ_TOKEN and the variable ITERATION8_LIBRARIES_OWNER:" >&2
  echo "composer.json requires iteration8/utilities, which is read from them." >&2
  exit 1
fi
for lib in core utilities; do
  composer config -g "repositories.iteration8-$lib" vcs "$SERVER/$OWNER/iteration8-$lib.git"
done
case "$SERVER" in http://*) composer config -g secure-http false ;; esac
auth=$(python3 -c 'import json, os, sys
print(json.dumps({"http-basic": {sys.argv[1]: {"username": "ci", "password": os.environ["FLEET_READ_TOKEN"]}}}))' "${SERVER#*://}")
COMPOSER_AUTH=$auth composer install --no-interaction --no-progress
