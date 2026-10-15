#!/bin/bash
# tools/lab/ci-mutation-repro.sh -- check.yml's PR mutation job, run as CI
# runs it, on the runner's own daemon, without the forge.
#
#   bash tools/lab/ci-mutation-repro.sh TREE BRANCH [LOG]     (on iteration8)
#
# ANSWERS: does the PR mutation job's result come from the job's setting, or
#   from the code? TREE is a checkout at the head to try, composer-installed
#   (prove-head.sh leaves one so), whose repository holds the history CI's
#   fetch-depth 0 clone would. BRANCH names the PR whose kept mutmut state to
#   restore. In the runner's docker-in-docker daemon, in fleet-ci:current with
#   the mutation job's --cpus 24 and its volumes, it clones that history to
#   CI's workspace path, checks out TREE's HEAD, puts the toolcache Python
#   3.12 first on PATH as setup-python does, installs requirements-dev.txt,
#   then runs the job's steps: fetch-mutmut-state.sh, pr-mutmut-state.sh
#   restore, mutmut-gate.py run --pr, mutmut results, gate --pr. Everything it
#   prints goes to LOG (default ~/.cache/jp-tools-ci-repro-<head>.log) and to
#   stdout. Exit: the job's.
#
#   The image runs as fleet-ci, which cannot make /workspace (the runner
#   mounts it for a job), so /workspace is a tmpfs owned by that user here.
#
#   It never runs pr-mutmut-state.sh save, so the PR's kept state is read and
#   left as it was. The PR volume is mounted read-write only because
#   fetch-mutmut-state.sh takes its lock file for writing.
#
# TESTED AGAINST: #233's head a48a0d0 and its kept state (batch2), the run
#   CI's mutation job failed (run 416) in the tracing pass.
#
# NOT TESTED AGAINST: what the job gets from the network and the runner that
#   this does not: setup-node and npm install, install-pwsh.sh and the PHP
#   libraries from the forge (vendor/ is TREE's, copied in). Suites needing
#   node or pwsh skip here where CI runs them; read a difference in those as
#   that before reading it as the code.
printf '%s' jpt-ci-repro > /proc/$$/comm 2>/dev/null
set -euo pipefail
tree=$(cd "${1:?TREE: a checkout at the head, composer-installed}" && pwd)
branch=${2:?BRANCH: the pull request branch whose kept state to restore}
head=$(git -C "$tree" rev-parse HEAD)
log=${3:-$HOME/.cache/jp-tools-ci-repro-$(git -C "$tree" rev-parse --short HEAD).log}
dind=forgejo-runner-docker-in-docker-1
stage=$(mktemp -d "$HOME/.cache/jp-tools-ci-repro.XXXXXX")
trap 'rm -rf "$stage"' EXIT

git -C "$tree" bundle create "$stage/repo.bundle" --all 2> /dev/null
cat > "$stage/job.sh" <<'JOB'
set -euo pipefail
head=$1 branch=$2
mkdir -p /workspace/joe
git clone -q /tmp/in/repo.bundle /workspace/joe/JP_TOOLS
cd /workspace/joe/JP_TOOLS
git checkout -q --detach "$head"
mv /tmp/in/vendor .
py=$(ls -d /opt/hostedtoolcache/Python/3.12.*/x64/bin | tail -n 1)
export PATH=$py:$PATH PYTHONUNBUFFERED=1 MUTMUT_WORKERS=24
echo "ci-mutation-repro: $(git log --oneline -1), $(python --version), $(nproc) cpus"
pip install -q -r requirements-dev.txt
bash ci-image/fetch-mutmut-state.sh /mutmut-state
bash ci-image/pr-mutmut-state.sh restore /mutmut-pr "$branch"
python mutmut-gate.py run --pr --max-children "$MUTMUT_WORKERS"
python -m mutmut results --all true > mutmut-results.txt
python mutmut-gate.py gate mutmut-baseline.json --results mutmut-results.txt --pr
JOB
tar -C "$stage" -cf "$stage/in.tar" repo.bundle job.sh
tar -C "$tree" -rf "$stage/in.tar" vendor
echo "ci-mutation-repro: $head ($branch) into $log"
sudo -n docker exec -i "$dind" docker run -i --rm --cpus 24 \
    -v mutmut-state:/mutmut-state:ro -v jp-tools-mutmut-pr:/mutmut-pr \
    -v toolcache:/opt/hostedtoolcache:ro -v pip-cache:/pip-cache -e PIP_CACHE_DIR=/pip-cache \
    --tmpfs "/workspace:rw,exec,size=8g,uid=$(sudo -n docker exec "$dind" docker run --rm --entrypoint id fleet-ci:current -u)" \
    --entrypoint bash fleet-ci:current \
    -c "mkdir -p /tmp/in && tar -C /tmp/in -xf - && bash /tmp/in/job.sh $head $branch" \
    < "$stage/in.tar" 2>&1 | tee "$log"
exit "${PIPESTATUS[0]}"
