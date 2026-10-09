# The fleet CI image: the runner's job image with every system tool the
# fleet's CI installs already in place, so a run checks instead of
# reinstalling PHP, shellcheck, PowerShell and the rest each time.
#
# Built inside the runner's docker-in-docker daemon on iteration8 by
# ci-image/build.sh, and served to jobs under the runner label `fleet-ci`.
# What it holds comes from the repos' own manifests, never a list kept here:
# requirements-system.txt (JP_TOOLS' gate tools and PHP extensions, pwsh and
# PSScriptAnalyzer at their pins, through install-pwsh.sh and its sha256
# check), plus the tools other repos' CI installed per run (projectbook:
# ripgrep, poppler-utils, iproute2). Python's packages stay with each job
# (requirements-dev.txt through the runner's shared pip cache).
#
# A pin that moves in a manifest is not in the image until it is rebuilt.
# check-pins.py in CI reads each tool back against its pin, so a stale image
# fails the run that needs the new pin rather than passing on the old one.
FROM ghcr.io/catthehacker/ubuntu:act-24.04

ENV DEBIAN_FRONTEND=noninteractive

# One update and one install (an update per install took 2.5 times as long,
# projectbook's pb/lab/ci-apt-time.sh), retrying a mirror address that does
# not answer. composer.json's PHP extensions are requirements-system.txt's
# php:* lines; php-xml, php-mbstring, php-curl and unzip are what composer and
# the PHP tools need to install and run.
RUN echo 'Acquire::Retries "5";' > /etc/apt/apt.conf.d/80-retries \
    && apt-get update -q \
    && apt-get install -y -q --no-install-recommends \
        php-cli php-xml php-mbstring php-curl php-gd php-sqlite3 unzip composer \
        shellcheck cppcheck \
        ripgrep poppler-utils iproute2 \
    && rm -rf /var/lib/apt/lists/*

# Jobs run as ci, never root. The base image's user is root, so every job
# was root in its container: a larger escape surface, and a test of file
# permissions read a mode-000 file anyway and skipped, so CI never ran those
# cases. ci has no sudo: what a job used to install as root is already here.
# setup-python and pip write the tool cache, so ci owns it.
RUN useradd --create-home --uid 1001 --shell /bin/bash ci \
    && mkdir -p /opt/hostedtoolcache \
    && chown -R ci:ci /opt/hostedtoolcache

# PowerShell and PSScriptAnalyzer at their pins, installed for ci, who runs
# the jobs.
COPY requirements-system.txt install-pwsh.sh /opt/fleet-ci/
USER ci
WORKDIR /home/ci
RUN bash /opt/fleet-ci/install-pwsh.sh
ENV PATH=/home/ci/.local/pwsh:$PATH
