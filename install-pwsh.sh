#!/bin/bash
# install-pwsh.sh -- PowerShell 7 and PSScriptAnalyzer, at the versions
# requirements-system.txt names, for the PowerShell arm of check.py. CI runs
# it; a person on a box without pwsh can too. No sudo.
#
#   1. pwsh: left alone when one is on PATH (check-pins.py then says whether
#      it is at least the tested version). Otherwise that exact release is
#      downloaded from PowerShell's GitHub releases into ~/.local/pwsh, linux
#      x64, and put on PATH, for a CI step after this one too ($GITHUB_PATH).
#      It needs the ICU libraries: DOTNET_SYSTEM_GLOBALIZATION_INVARIANT=1
#      would start it without them, but PowerShellGet then refuses to load
#      ("en-us is an invalid culture identifier"), so Install-Module fails.
#   2. PSScriptAnalyzer: installed from the PowerShell Gallery at its pin, for
#      the current user. Any error stops pwsh with a non-zero exit, so a
#      failed install fails this script rather than printing and going on.
#
# Exit status: 0 both in place; 1 a step failed, with its own message.
printf '%s' install-pwsh > /proc/$$/comm 2>/dev/null
set -euo pipefail
here=$(cd "$(dirname "$0")" && pwd)

# "pwsh VERSION" and "pwsh:PSScriptAnalyzer VERSION" from the manifest, as
# check-pins.py reads them; a missing line stops here, saying which.
version_of() {
    local tool=$1 name ver _rest
    while read -r name ver _rest; do
        if [ "$name" = "$tool" ]; then echo "$ver"; return 0; fi
    done < "$here/requirements-system.txt"
    echo "install-pwsh: no '$tool' line in requirements-system.txt" >&2
    return 1
}

pwsh_version=$(version_of pwsh)
pssa_version=$(version_of pwsh:PSScriptAnalyzer)

if ! command -v pwsh >/dev/null 2>&1; then
    dest="$HOME/.local/pwsh"
    tarball="powershell-$pwsh_version-linux-x64.tar.gz"
    echo "install-pwsh: PowerShell $pwsh_version into $dest"
    mkdir -p "$dest"
    curl -fsSL "https://github.com/PowerShell/PowerShell/releases/download/v$pwsh_version/$tarball" \
        -o "$dest/$tarball"
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
    "\$ErrorActionPreference = 'Stop'; Set-PSRepository PSGallery -InstallationPolicy Trusted; Install-Module PSScriptAnalyzer -RequiredVersion $pssa_version -Scope CurrentUser -Force"
# shellcheck disable=SC2016  # $PSVersionTable is PowerShell's, so it is single-quoted on purpose
pwsh -NoProfile -NonInteractive -Command '$PSVersionTable.PSVersion.ToString()'
