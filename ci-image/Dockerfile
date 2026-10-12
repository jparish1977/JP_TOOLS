# The fleet CI image: the runner's job image with every system tool the
# fleet's CI installs already in place, so a run checks instead of
# reinstalling PHP, shellcheck, PowerShell and the rest each time.
#
# Built inside the runner's docker-in-docker daemon on iteration8 by
# ci-image/build.sh, and served to jobs under the runner label `fleet-ci`.
# What it holds comes from the repos' own manifests, never a list kept here:
# requirements-system.txt (JP_TOOLS' gate tools and PHP extensions, pwsh and
# PSScriptAnalyzer at their pins, through install-pwsh.sh and its sha256
# check), plus the tools other repositories' CI installed on every run
# (ripgrep, poppler-utils, iproute2, pandoc, dvipng, opensp, sgml-data).
# Python's packages stay with each job (requirements-dev.txt through the
# runner's shared pip cache).
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
# the PHP tools need to install and run. mame-tools, testdisk, gddrescue and
# ntfs-3g are the real tools JP_TOOLS' recovery suites drive (chdman,
# photorec, ddrescue, mkntfs and ntfsls), from install-recovery.sh's apt set:
# without them those suites print SKIP in a plain run, and under pytest, where
# mutmut runs them, a suite that skips only in its __main__ fails instead.
# pandoc converts HTML; dvipng draws TeX equations from DVI; opensp and
# sgml-data: onsgmls validates SGML against the ISO entity sets. texlive-base
# is named because it is only a RECOMMENDS of dvipng: without it
# --no-install-recommends gave a dvipng with no TeX fonts (kpsewhich
# cmr10.tfm found nothing, measured in fleet-ci:b48264a), which reads every
# DVI's font as missing. build.sh checks onsgmls by parsing a valid one-line
# document: `onsgmls -v` alone parses an empty one and exits 1.
RUN echo 'Acquire::Retries "5";' > /etc/apt/apt.conf.d/80-retries \
    && apt-get update -q \
    && apt-get install -y -q --no-install-recommends \
        php-cli php-xml php-mbstring php-curl php-gd php-sqlite3 unzip composer \
        shellcheck cppcheck \
        ripgrep poppler-utils iproute2 \
        mame-tools testdisk gddrescue ntfs-3g \
        pandoc dvipng texlive-base opensp sgml-data \
    && rm -rf /var/lib/apt/lists/*

# Jobs run as fleet-ci, never root. The base image's user is root, so every
# job was root in its container: a larger escape surface, and a test of file
# permissions read a mode-000 file anyway and skipped, so CI never ran those
# cases. fleet-ci has no sudo: what a job used to install as root is already
# here. setup-python and pip write the tool cache, so fleet-ci owns it.
#
# The uid and gid are passed in by build.sh. Under a daemon that remaps users
# they are 994 and 981, and a job's host uid is the remap base plus that.
# Under one that does not, a job's uid IS that uid on the host, so build.sh
# passes the host's fleet-ci account (a system user, nologin, no other
# groups): a uid picked here could be someone's on the host (1001 was the web
# deploy account), and the host account reserves it and names it in ps.
ARG CI_UID
ARG CI_GID
RUN test -n "$CI_UID" && test -n "$CI_GID" \
    && groupadd --gid "$CI_GID" fleet-ci \
    && useradd --create-home --uid "$CI_UID" --gid "$CI_GID" --shell /bin/bash fleet-ci \
    && mkdir -p /opt/hostedtoolcache \
    && chown -R fleet-ci:fleet-ci /opt/hostedtoolcache

# PowerShell and PSScriptAnalyzer at their pins, installed for fleet-ci, who
# runs the jobs.
COPY requirements-system.txt install-pwsh.sh /opt/fleet-ci/
USER fleet-ci
WORKDIR /home/fleet-ci
RUN bash /opt/fleet-ci/install-pwsh.sh
ENV PATH=/home/fleet-ci/.local/pwsh:$PATH
