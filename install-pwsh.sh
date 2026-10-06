#!/bin/bash
# install-pwsh.sh -- PowerShell 7 and PSScriptAnalyzer, at the versions
# requirements-system.txt names, for the PowerShell arm of check.py. CI runs
# it; a person on a box without pwsh can too. No sudo.
#
#   1. pwsh: left alone when one is on PATH (check-pins.py then says whether
#      it is at least the tested version). Otherwise that exact release is
#      downloaded from PowerShell's GitHub releases into ~/.local/pwsh, linux
#      x64, checked against the SHA-256 on the manifest's pwsh line before it
#      is unpacked, and put on PATH, for a CI step after this one too
#      ($GITHUB_PATH).
#      It needs the ICU libraries: DOTNET_SYSTEM_GLOBALIZATION_INVARIANT=1
#      would start it without them, but PowerShellGet then refuses to load
#      ("en-us is an invalid culture identifier"), so Install-Module fails.
#   2. PSScriptAnalyzer: installed from the PowerShell Gallery at its pin, for
#      the current user. Any error stops pwsh with a non-zero exit, so a
#      failed install fails this script rather than printing and going on.
#      -Force gets past the Gallery's untrusted-repository prompt for this one
#      install; the Gallery's trust setting is left as the user has it.
#
# Exit status: 0 both in place; 1 a step failed, with its own message.
printf '%s' install-pwsh > /proc/$$/comm 2>/dev/null
set -euo pipefail
here=$(cd "$(dirname "$0")" && pwd)

# Column COL (2 the version, 5 the tarball's sha256:HASH) of the manifest
# line for TOOL, as check-pins.py reads them; a missing line or column stops
# here, saying which.
field_of() {
    local tool=$1 col=$2 name ver _apt _win hash _rest
    while read -r name ver _apt _win hash _rest; do
        [ "$name" = "$tool" ] || continue
        if [ "$col" = 2 ] && [ -n "$ver" ]; then echo "$ver"; return 0; fi
        if [ "$col" = 5 ] && [ "${hash#sha256:}" != "$hash" ]; then echo "${hash#sha256:}"; return 0; fi
        break
    done < "$here/requirements-system.txt"
    echo "install-pwsh: no '$tool' line with column $col in requirements-system.txt" >&2
    return 1
}

pwsh_version=$(field_of pwsh 2)
pssa_version=$(field_of pwsh:PSScriptAnalyzer 2)

if ! command -v pwsh >/dev/null 2>&1; then
    dest="$HOME/.local/pwsh"
    tarball="powershell-$pwsh_version-linux-x64.tar.gz"
    pwsh_sha256=$(field_of pwsh 5)
    echo "install-pwsh: PowerShell $pwsh_version into $dest"
    mkdir -p "$dest"
    curl -fsSL "https://github.com/PowerShell/PowerShell/releases/download/v$pwsh_version/$tarball" \
        -o "$dest/$tarball"
    if ! echo "$pwsh_sha256  $dest/$tarball" | sha256sum --check --quiet --strict; then
        rm -f "$dest/$tarball"
        echo "install-pwsh: $tarball does not match the sha256 in requirements-system.txt; not unpacked" >&2
        exit 1
    fi
    tar -xzf "$dest/$tarball" -C "$dest"
    rm -f "$dest/$tarball"
    chmod +x "$dest/pwsh"
    export PATH="$dest:$PATH"
    if [ -n "${GITHUB_PATH:-}" ]; then
        echo "$dest" >> "$GITHUB_PATH"
    fi
fi

echo "install-pwsh: PSScriptAnalyzer $pssa_version"
pwsh -NoProfile -NonInteractive -Command \
    "\$ErrorActionPreference = 'Stop'; Install-Module PSScriptAnalyzer -RequiredVersion $pssa_version -Scope CurrentUser -Force"
# shellcheck disable=SC2016  # $PSVersionTable is PowerShell's, so it is single-quoted on purpose
pwsh -NoProfile -NonInteractive -Command '$PSVersionTable.PSVersion.ToString()'
