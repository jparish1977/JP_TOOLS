#!/usr/bin/env bash
# tools/lab/chdman-build.sh -- build one MAME release's chdman from source,
# beside the packaged one, to compare them
#
#   bash tools/lab/chdman-build.sh [TAG] [JOBS]
#
# Clones mamedev/mame at TAG (default mame0289) into ~/src/TAG (shallow), builds
# MAME's tools only (TOOLS=1 EMULATOR=0, JOBS at once, default the CPU count),
# and copies chdman to ~/.local/opt/TAG/chdman. Nothing on PATH changes: the
# packaged chdman stays what every other caller runs. Prints the built
# binary's version line. An existing ~/src/TAG is reused, so a failed build
# can be run again.
#
# Why: chdman 0.264 hangs now and then making a child CHD under load
# (oob/chdman-hangs/CHDMAN-NOTE.md); whether a later release does is measured
# with tools/lab/chdman-delta-hang.sh, which takes the chdman to run as CHDMAN.
#
# Tested: not yet run when written. NOT tested: any tag but the default; any
# box but one with the build tools MAME needs (a C++17 compiler, python3,
# make); whether the tools build wants SDL headers.
printf '%s' chdman-build > /proc/$$/comm 2>/dev/null || true
set -eu
tag=${1:-mame0289}
jobs=${2:-$(nproc)}
src=$HOME/src/$tag
dest=$HOME/.local/opt/$tag
if [ ! -d "$src/.git" ]; then
    git clone -q --depth 1 --branch "$tag" https://github.com/mamedev/mame.git "$src"
fi
cd "$src"
git log --oneline -1
make -s TOOLS=1 EMULATOR=0 NOWERROR=1 -j"$jobs" > "$src/build.log" 2>&1 || {
    echo "chdman-build: the build failed; the end of $src/build.log:"
    tail -n 30 "$src/build.log"
    exit 1
}
mkdir -p "$dest"
cp chdman "$dest/chdman"
"$dest/chdman" --help 2>&1 | head -1
echo "chdman-build: $dest/chdman"
