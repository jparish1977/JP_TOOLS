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
# Jobs run as fleet-ci, the HOST's account of that name (a system user on
# iteration8, nologin, no other groups): the runner's daemon does not remap
# users, so the image takes that account's uid and gid. Without the account
# it stops. The runner's shared cache volumes (pip-cache, npm-cache) are
# owned by the same uid, so jobs can write them; a volume created afresh is
# root's and would silently stop caching.
#
# After it builds, it reads back inside the new image: that it runs as the
# host's fleet-ci uid, not root, and cannot sudo; then every pinned tool
# (check-pins.py's powershell arm, the PHP extensions, composer, shellcheck,
# cppcheck, rg, pdftotext, ss). Exit 0 only when every step passes.
printf '%s' jpt-ci-image > /proc/$$/comm 2>/dev/null
set -euo pipefail
here=$(cd "$(dirname "$0")/.." && pwd)
dind=forgejo-runner-docker-in-docker-1
tag="fleet-ci:$(git -C "$here" rev-parse --short HEAD)"
current=1
[ "${1:-}" = "--no-current" ] && current=0
if ! uid=$(id -u fleet-ci 2>/dev/null) || ! gid=$(id -g fleet-ci 2>/dev/null); then
    echo "ci-image: no fleet-ci account on this host; create it first:" >&2
    echo "  sudo useradd --system --no-create-home --home-dir /nonexistent --shell /usr/sbin/nologin --user-group fleet-ci" >&2
    exit 1
fi

echo "ci-image: building $tag in $dind as fleet-ci uid $uid gid $gid"
tar -C "$here" -cf - ci-image/Dockerfile requirements-system.txt install-pwsh.sh \
    | sudo -n docker exec -i "$dind" sh -c \
        'mkdir -p /tmp/fleet-ci && cd /tmp/fleet-ci && rm -rf ./* && tar -xf - && cp ci-image/Dockerfile .'
sudo -n docker exec "$dind" docker build -q --build-arg "CI_UID=$uid" --build-arg "CI_GID=$gid" \
    -t "$tag" /tmp/fleet-ci

echo "ci-image: reading the tools back in $tag"
tar -C "$here" -cf - check-pins.py requirements-system.txt requirements-dev.txt composer.json \
        jpcheck platformtools jpexit \
    | sudo -n docker exec -i "$dind" docker run -i --rm -e "WANT_UID=$uid" "$tag" bash -c '
        set -e; mkdir /tmp/r && cd /tmp/r && tar -xf -
        if [ "$(id -u)" != "$WANT_UID" ]; then echo "FAIL: runs as uid $(id -u), not the host fleet-ci uid $WANT_UID" >&2; exit 1; fi
        echo "user: $(id -un) uid $(id -u), the host fleet-ci account"
        if sudo -n true 2>/dev/null; then echo "FAIL: fleet-ci can sudo" >&2; exit 1; fi
        echo "sudo: refused"
        python3 check-pins.py powershell
        for ext in gd sqlite3 pdo_sqlite; do
            php -m | grep -qx "$ext" || { echo "FAIL: PHP extension $ext missing" >&2; exit 1; }
        done
        echo "php extensions: gd sqlite3 pdo_sqlite"
        composer --version
        shellcheck --version | head -2 | tail -1
        cppcheck --version
        rg --version | head -1
        pdftotext -v 2>&1 | head -1
        ss -V'

echo "ci-image: the runner's cache volumes, owned by fleet-ci"
for vol in pip-cache npm-cache; do
    sudo -n docker exec "$dind" docker volume create "$vol" >/dev/null
    sudo -n docker exec "$dind" docker run --rm --user 0 -v "$vol:/v" "$tag" chown -R "$uid:$gid" /v
    sudo -n docker exec "$dind" docker run --rm -v "$vol:/v" "$tag" sh -c 'touch /v/.w && rm /v/.w' \
        || { echo "FAIL: $vol is not writable by fleet-ci" >&2; exit 1; }
    echo "  $vol: writable by fleet-ci"
done

if [ "$current" = 1 ]; then
    sudo -n docker exec "$dind" docker tag "$tag" fleet-ci:current
    echo "ci-image: $tag is now fleet-ci:current"
fi
