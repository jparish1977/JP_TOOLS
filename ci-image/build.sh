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
# Tagging current first tags the image it replaces fleet-ci:previous, then
# runs reap.sh, which removes every other fleet-ci tag.
#
# Jobs run as fleet-ci, uid 994 gid 981 in the image. Which uid that is on the
# host depends on the runner's daemon:
# - remapping users (docker info lists name=userns): a job's uid is the remap
#   range's base plus 994, so no host account is involved and none is
#   required;
# - not remapping: a job's uid IS that uid on the host, so it must be an
#   account nobody else has. The host's fleet-ci account (a system user,
#   nologin, no other groups) reserves it, and the image takes that account's
#   uid and gid; without the account it stops.
# The runner's shared cache volumes are owned by the image's fleet-ci, so
# jobs can write them; a volume created afresh is root's and would silently
# stop caching. Each is mounted into every job by the runner's config
# (iteration8_server forgejo-runner): pip-cache (PIP_CACHE_DIR), npm-cache
# (npm_config_cache), composer-cache (COMPOSER_CACHE_DIR), and toolcache at
# /opt/hostedtoolcache, where setup-python and setup-node look before they
# download. build.sh fills toolcache from ci-image/toolcache.txt, so jobs
# only read it: jobs filling it raced and failed (CI run 308).
#
# After it builds, it reads back inside the new image: that it runs as
# fleet-ci's uid, not root, and cannot sudo; then every pinned tool
# (check-pins.py's powershell arm, the PHP extensions, composer, shellcheck,
# cppcheck, rg, pdftotext, ss). Exit 0 only when every step passes.
printf '%s' jpt-ci-image > /proc/$$/comm 2>/dev/null
set -euo pipefail
here=$(cd "$(dirname "$0")/.." && pwd)
dind=forgejo-runner-docker-in-docker-1
tag="fleet-ci:$(git -C "$here" rev-parse --short HEAD)"
current=1
[ "${1:-}" = "--no-current" ] && current=0
# fleet-ci's uid and gid for the image, into uid and gid (see the header).
fleet_ci_ids() {
    if sudo -n docker exec "$dind" docker info -f '{{range .SecurityOptions}}{{.}} {{end}}' \
            | tr ' ' '\n' | grep -qx 'name=userns'; then
        uid=994 gid=981
        echo "ci-image: $dind remaps users, so no host account is needed"
    elif ! uid=$(id -u fleet-ci 2>/dev/null) || ! gid=$(id -g fleet-ci 2>/dev/null); then
        echo "ci-image: $dind does not remap users and there is no fleet-ci account on this host; create it first:" >&2
        echo "  sudo useradd --system --no-create-home --home-dir /nonexistent --shell /usr/sbin/nologin --user-group fleet-ci" >&2
        exit 1
    fi
}
fleet_ci_ids

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
        if [ "$(id -u)" != "$WANT_UID" ]; then echo "FAIL: runs as uid $(id -u), not fleet-ci uid $WANT_UID" >&2; exit 1; fi
        echo "user: $(id -un) uid $(id -u)"
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
for vol in pip-cache npm-cache composer-cache toolcache; do
    sudo -n docker exec "$dind" docker volume create "$vol" >/dev/null
    sudo -n docker exec "$dind" docker run --rm --user 0 -v "$vol:/v" "$tag" chown -R "$uid:$gid" /v
    sudo -n docker exec "$dind" docker run --rm -v "$vol:/v" "$tag" sh -c 'touch /v/.w && rm /v/.w' \
        || { echo "FAIL: $vol is not writable by fleet-ci" >&2; exit 1; }
    echo "  $vol: writable by fleet-ci"
done

echo "ci-image: the tool cache, filled from ci-image/toolcache.txt"
# Each release once, as fleet-ci, into the toolcache volume, in the layout
# setup-python and setup-node read (TOOL/VERSION/x64 and x64.complete); one
# already complete is left alone.
while read -r tool version sha url; do
    case "$tool" in ''|'#'*) continue ;; esac
    sudo -n docker exec "$dind" docker run --rm -v toolcache:/opt/hostedtoolcache \
        -e TOOL="$tool" -e VERSION="$version" -e SHA="$sha" -e URL="$url" "$tag" bash -c '
        set -euo pipefail
        name=$TOOL; [ "$TOOL" = python ] && name=Python
        dest=/opt/hostedtoolcache/$name/$VERSION
        if [ -f "$dest/x64.complete" ]; then echo "  $TOOL $VERSION: already cached"; exit 0; fi
        rm -rf "$dest"
        w=$(mktemp -d); cd "$w"
        curl -fsSL "$URL" -o pkg
        echo "$SHA  pkg" | sha256sum --check --strict --quiet \
            || { echo "FAIL: $TOOL $VERSION does not match its sha256" >&2; exit 1; }
        case "$TOOL" in
            python) mkdir x && tar -xzf pkg -C x && cd x && bash ./setup.sh >/dev/null ;;
            node) mkdir -p "$dest/x64" && tar -xJf pkg -C "$dest/x64" --strip-components=1 \
                  && touch "$dest/x64.complete" ;;
            *) echo "FAIL: no way to cache $TOOL" >&2; exit 1 ;;
        esac
        [ -f "$dest/x64.complete" ] || { echo "FAIL: $TOOL $VERSION not marked complete" >&2; exit 1; }
        echo "  $TOOL $VERSION: cached"'
done < "$here/ci-image/toolcache.txt"

# The image current replaces becomes fleet-ci:previous, for rollback; reap.sh
# keeps those two and removes every other fleet-ci tag.
promote() {
    local old new
    old=$(sudo -n docker exec "$dind" docker image inspect -f '{{.Id}}' fleet-ci:current 2>/dev/null || true)
    new=$(sudo -n docker exec "$dind" docker image inspect -f '{{.Id}}' "$tag")
    if [ -n "$old" ] && [ "$old" != "$new" ]; then
        sudo -n docker exec "$dind" docker tag fleet-ci:current fleet-ci:previous
        echo "ci-image: the image it replaces is now fleet-ci:previous"
    fi
    sudo -n docker exec "$dind" docker tag "$tag" fleet-ci:current
    echo "ci-image: $tag is now fleet-ci:current"
    bash "$here/ci-image/reap.sh"
}
if [ "$current" = 1 ]; then promote; fi
