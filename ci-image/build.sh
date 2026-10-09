#!/bin/bash
# ci-image/build.sh -- build the fleet CI image inside iteration8's runner
# daemon and tag it for the runner label.
#
#   ssh iteration8 'cd ~/projects/JP_TOOLS && bash ci-image/build.sh'
#
# Jobs run in the runner's docker-in-docker sidecar, so the image is built in
# THAT daemon, where the runner finds it; iteration8's own docker never holds
# it. The build context is a tar of ci-image/Dockerfile and the manifests it
# copies, from this checkout, piped into `docker build -`.
#
# Tags: fleet-ci:<this checkout's short commit>, then fleet-ci:current, which
# the runner's label serves (`fleet-ci:docker://fleet-ci:current`, with
# force_pull false, so the local image is used). Pass --no-current to build
# and tag only the commit, for proving a new image before it serves jobs.
#
# After it builds, it reads back inside the new image: that it runs as ci,
# not root, and that ci cannot sudo; then every pinned tool (check-pins.py's
# powershell arm, the PHP extensions, composer, shellcheck, cppcheck, rg,
# pdftotext, ss). Exit 0 only when the build and every read-back pass.
printf '%s' jpt-ci-image > /proc/$$/comm 2>/dev/null
set -euo pipefail
here=$(cd "$(dirname "$0")/.." && pwd)
dind=forgejo-runner-docker-in-docker-1
tag="fleet-ci:$(git -C "$here" rev-parse --short HEAD)"
current=1
[ "${1:-}" = "--no-current" ] && current=0

echo "ci-image: building $tag in $dind"
tar -C "$here" -cf - ci-image/Dockerfile requirements-system.txt install-pwsh.sh \
    | sudo -n docker exec -i "$dind" sh -c \
        'mkdir -p /tmp/fleet-ci && cd /tmp/fleet-ci && rm -rf ./* && tar -xf - && cp ci-image/Dockerfile .'
sudo -n docker exec "$dind" docker build -q -t "$tag" /tmp/fleet-ci

echo "ci-image: reading the tools back in $tag"
tar -C "$here" -cf - check-pins.py requirements-system.txt requirements-dev.txt composer.json \
        jpcheck platformtools jpexit \
    | sudo -n docker exec -i "$dind" docker run -i --rm "$tag" bash -c '
        set -e; mkdir /tmp/r && cd /tmp/r && tar -xf -
        if [ "$(id -u)" = 0 ]; then echo "FAIL: the image runs as root" >&2; exit 1; fi
        echo "user: $(id -un) uid $(id -u), not root"
        if sudo -n true 2>/dev/null; then echo "FAIL: ci can sudo" >&2; exit 1; fi
        echo "sudo: refused"
        python3 check-pins.py powershell
        php -m | grep -qx gd && php -m | grep -qx sqlite3 && php -m | grep -qx pdo_sqlite && echo "php extensions: gd sqlite3 pdo_sqlite"
        composer --version
        shellcheck --version | head -2 | tail -1
        cppcheck --version
        rg --version | head -1
        pdftotext -v 2>&1 | head -1
        ss -V'

if [ "$current" = 1 ]; then
    sudo -n docker exec "$dind" docker tag "$tag" fleet-ci:current
    echo "ci-image: $tag is now fleet-ci:current"
fi
