#!/bin/bash
# ci-image/fleet-ci-ids.sh -- which uid and gid build.sh builds the image's
# fleet-ci account with, for the runner daemon DIND (build.sh's header says
# why each case is what it is).
#
#   bash ci-image/fleet-ci-ids.sh DIND     prints "UID GID"; anything said to
#                                          a person goes to stderr
#
#   DIND remaps users (docker info lists name=userns)   994 981
#   it does not, and the host has a fleet-ci account    that account's ids
#   it does not, and the host has none                  exit 1, saying how to make it
#   DIND could not be asked                             exit 2, nothing built
#
# A probe that could not answer is never read as "does not remap": on a host
# with the account that would build with the host's uid under a remapping
# daemon, and the cache volumes owned by the image's fleet-ci would stop
# caching without a word. The answer is captured before it is read, so no
# pipeline can turn a true answer false.
#
# tests/test_ci_image_ids.py holds these rules with stand-ins for sudo and id.
printf '%s' jpt-ci-ids > /proc/$$/comm 2>/dev/null
set -euo pipefail
dind=$1
if ! opts=$(sudo -n docker exec "$dind" docker info -f '{{range .SecurityOptions}}{{.}} {{end}}'); then
    echo "ci-image: could not ask $dind whether it remaps users; nothing built" >&2
    exit 2
fi
case " $opts " in
    *" name=userns "*)
        echo "ci-image: $dind remaps users, so no host account is needed" >&2
        echo "994 981"
        ;;
    *)
        if ! uid=$(id -u fleet-ci 2>/dev/null) || ! gid=$(id -g fleet-ci 2>/dev/null); then
            echo "ci-image: $dind does not remap users and there is no fleet-ci account on this host; create it first:" >&2
            echo "  sudo useradd --system --no-create-home --home-dir /nonexistent --shell /usr/sbin/nologin --user-group fleet-ci" >&2
            exit 1
        fi
        echo "$uid $gid"
        ;;
esac
