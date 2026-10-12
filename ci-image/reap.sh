#!/bin/bash
# ci-image/reap.sh -- remove what CI jobs and image builds leave behind in
# the runner's daemon.
#
#   bash ci-image/reap.sh [--dry-run]     (on the runner's host, in a checkout)
#
# Jobs run in the runner's docker-in-docker sidecar. A task cancelled or killed
# before its first step leaves its container there, never started ("Created"),
# and nothing removes it; one that dies mid-run can leave it "Exited". Each such
# container keeps two volumes the runner made for it, <hash> (the workspace)
# and <hash>-env (/var/run/act, the job's environment, which may hold the
# job's credentials, so they are removed and never read). They are named
# volumes, so neither `docker rm -v` nor `docker volume prune` removes them.
#
# What it removes, in order:
#   1. FORGEJO-ACTIONS-TASK-* containers that are not running and were created
#      more than REAP_AGE_HOURS ago (default 24), then the hash-named volumes
#      each of them mounted. The shared caches (pip-cache, npm-cache,
#      composer-cache, toolcache) are named otherwise and are never touched.
#   2. fleet-ci image tags whose image is neither fleet-ci:current nor
#      fleet-ci:previous (the one build.sh replaced, kept for rollback).
#   3. Images that no runner label names (labels read from the runner's
#      config.yml) and that are not fleet-ci. An image a container still uses
#      is refused by docker and left.
# Environment: RUNNER_CONFIG (the runner's rendered config.yml; default under
# $HOME), CLAUDE_JOB (the job registry's `claude-job`, which the brake can stop;
# default the one on PATH), REAP_AGE_HOURS.
#
# Each removal is printed. --dry-run prints the same list and removes nothing.
# Exit 0 when every removal it tried succeeded; 1 when a removal failed, or
# when there is no claude-job to register with (a real run only).
#
# build.sh runs it after tagging fleet-ci:current, and the host runs it daily,
# registered with claude-job, so the brake stops it.
printf '%s' jpt-ci-reap > /proc/$$/comm 2>/dev/null
set -uo pipefail
dind=forgejo-runner-docker-in-docker-1
config=${RUNNER_CONFIG:-$HOME/forgejo-runner-data/config.yml}
age_h=${REAP_AGE_HOURS:-24}
dry=0
[ "${1:-}" = "--dry-run" ] && dry=1
fail=0
# A daily run as root has no claude-job on PATH, so it sets CLAUDE_JOB.
job=${CLAUDE_JOB:-$(command -v claude-job || true)}
# A missing claude-job is not a halt. Without this check, `start` failing to
# run reads as "the fleet is halted" below and every run exits 0 having
# removed nothing.
if [ "$dry" = 0 ] && { [ -z "$job" ] || [ ! -x "$job" ]; }; then
    echo "reap: no claude-job (${job:-none on PATH}); set CLAUDE_JOB; removing nothing" >&2
    exit 1
fi
if [ "$dry" = 0 ] && ! "$job" start fleet-ci-reap --writes runner-dind --every 1440; then
    echo "reap: the fleet is halted; removing nothing" >&2
    exit 0
fi

d() { sudo -n docker exec "$dind" docker "$@"; }

# act runs `d` for real; with --dry-run it only prints what it would run.
act() {
    echo "  $*"
    [ "$dry" = 1 ] && return 0
    d "$@" >/dev/null || { echo "  FAILED: docker $*" >&2; fail=1; }
}

# finish records the outcome with claude-job (not on a dry run) and exits.
finish() {
    if [ "$dry" = 1 ]; then
        echo "reap: --dry-run, nothing removed"
    else
        "$job" "done" fleet-ci-reap "$([ "$1" = 0 ] && echo ok || echo fail)"
    fi
    exit "$1"
}

# A dead task container is removed with the hash-named volumes it mounted.
reap_containers() {
    local cutoff vols=() id state created when v
    cutoff=$(( $(date +%s) - age_h * 3600 ))
    echo "reap: task containers not running, created before $(date -d "@$cutoff" '+%Y-%m-%d %H:%M %Z')"
    while IFS=$'\t' read -r id state created; do
        case "$state" in created|exited|dead) ;; *) continue ;; esac
        # CreatedAt reads "2026-10-07 12:00:00 -0400 EDT"; date takes it without the zone name.
        when=$(date -d "${created% *}" +%s) || continue
        [ "$when" -lt "$cutoff" ] || continue
        for v in $(d inspect -f '{{range .Mounts}}{{.Name}} {{end}}' "$id"); do
            [[ "$v" =~ ^[0-9a-f]{32}(-env)?$ ]] && vols+=("$v")
        done
        act rm "$id"
    done < <(d ps -a --filter name=FORGEJO-ACTIONS-TASK- --format '{{.ID}}	{{.State}}	{{.CreatedAt}}')
    echo "reap: their volumes"
    for v in "${vols[@]}"; do act volume rm "$v"; done
}

# build.sh names the image it replaced fleet-ci:previous. Creation times
# cannot say which came last: builds that reuse every cached layer share one.
reap_fleet_ci_tags() {
    local current previous tag full
    echo "reap: fleet-ci tags but current and previous"
    current=$(d image inspect -f '{{.Id}}' fleet-ci:current 2>/dev/null)
    previous=$(d image inspect -f '{{.Id}}' fleet-ci:previous 2>/dev/null)
    while read -r tag; do
        full=$(d image inspect -f '{{.Id}}' "fleet-ci:$tag")
        [ "$full" = "$current" ] || [ "$full" = "$previous" ] && continue
        act rmi "fleet-ci:$tag"
    done < <(d images fleet-ci --format '{{.Tag}}')
}

reap_unlabelled_images() {
    local labels image
    echo "reap: images no runner label names"
    if ! labels=$(sudo -n cat "$config" | grep -o 'docker://[^[:space:]]*' | cut -c10-); then
        echo "reap: cannot read the runner labels in $config; leaving every image" >&2
        finish 1
    fi
    while read -r image; do
        case "$image" in fleet-ci:*|'<none>:<none>') continue ;; esac
        printf '%s\n' "$labels" | grep -qxF "$image" && continue
        act rmi "$image"
    done < <(d images --format '{{.Repository}}:{{.Tag}}')
}

reap_containers
reap_fleet_ci_tags
reap_unlabelled_images
finish "$fail"
