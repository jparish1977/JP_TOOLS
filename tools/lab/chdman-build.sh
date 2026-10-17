#!/usr/bin/env bash
# tools/lab/chdman-build.sh -- build one MAME release's chdman from source,
# beside the packaged one, to compare them
#
#   [SYMBOLS=1] bash tools/lab/chdman-build.sh [TAG] [JOBS]
#
# SYMBOLS=1 builds with MAME's own debug symbols, into ~/src/TAG-sym and
# ~/.local/opt/TAG-sym, beside the plain build: what a backtrace
# (chdman-hang-backtrace.sh) needs, with no download.
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
# Two steps: the top-level makefile generates the project makefiles (with
# USE_QTDEBUG=0, or it looks for Qt's qmake6 and moc for MAME's debugger
# window, which no tool uses, and stops before compiling anything); then
# the generated makefile builds the chdman target alone. A whole TOOLS=1
# build also compiles MAME's display layer, which wants SDL2_ttf's headers,
# and chdman uses none of it. The first step also makes
# build/generated/version.cpp, which only the top-level makefile writes and
# chdman needs: a fresh tree stops without it ("No rule to make target
# '../../../../generated/version.cpp'").
#
# Tested: mame0289 on Ubuntu 24.04 (g++ 13.3, SDL2 headers, no Qt, no
# SDL2_ttf): a TOOLS=1 build stopped first on Qt, then (with USE_QTDEBUG=0)
# on SDL2/SDL_ttf.h; the chdman target alone then built, but only because
# that tree already held version.cpp from the TOOLS=1 attempt, and a fresh
# SYMBOLS=1 tree stopped on it. NOT tested: any other tag.
printf '%s' chdman-build > /proc/$$/comm 2>/dev/null || true
set -eu
tag=${1:-mame0289}
jobs=${2:-$(nproc)}
symbols=${SYMBOLS:-0}
suffix=""
if [ "$symbols" = 1 ]; then suffix=-sym; fi
src=$HOME/src/$tag$suffix
dest=$HOME/.local/opt/$tag$suffix
if [ ! -d "$src/.git" ]; then
    git clone -q --depth 1 --branch "$tag" https://github.com/mamedev/mame.git "$src"
fi
cd "$src"
git log --oneline -1
project=build/projects/sdl/mame/gmake-linux
{ make -s TOOLS=1 EMULATOR=0 USE_QTDEBUG=0 NOWERROR=1 SYMBOLS="$symbols" "$project/Makefile" \
    build/generated/version.cpp &&
  make -s -C "$project" config=release64 -j"$jobs" chdman; } > "$src/build.log" 2>&1 || {
    echo "chdman-build: the build failed; the end of $src/build.log:"
    tail -n 30 "$src/build.log"
    exit 1
}
built=$(find "$src" -name chdman -type f -perm -u+x -newer "$src/makefile" | head -1)
[ -n "$built" ] || { echo "chdman-build: the build said it finished, but no chdman binary is there"; exit 1; }
mkdir -p "$dest"
cp "$built" "$dest/chdman"
"$dest/chdman" --help 2>&1 | head -1
echo "chdman-build: $dest/chdman"
