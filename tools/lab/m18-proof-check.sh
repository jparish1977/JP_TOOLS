#!/bin/bash
# tools/lab/m18-proof-check.sh -- what the m18's WSL Ubuntu still lacks to run
# JP_TOOLS' whole proof (prove-head.sh) as iteration8 does. READ ONLY.
#
# ANSWERS: for each tool the suites and specs drive, is it there in WSL, and
#   at what version; does the JP_TOOLS clone there sit at the forge's master;
#   does the venv hold the test and mutation pins; how many cores and how much
#   memory a job could have; where a core dump would go. It installs, clones
#   and changes nothing: it is the list the setup is built from.
# WHY: Joe 2026-10-14, the m18 as a second place to run CI and the gate.
#   projectbook's pb/lab/m18-proof-box.sh already gave that WSL a JP_TOOLS
#   clone and the pinned gate tools; JP_TOOLS' own suites need more (PHP and
#   composer, node, pwsh, the recovery tools, pytest and mutmut).
# HOW: one shell script run in WSL, travelling base64 past cmd.exe, as
#   projectbook's and i8jobs' m18 tools do. Run on the Air, as joe, only when
#   the m18 is not running someone's jobs (check the board).
# TESTED AGAINST: nothing yet.
# NOT TESTED AGAINST: a WSL distro other than Ubuntu.
printf '%s' jpt-m18-check > /proc/$$/comm 2>/dev/null
set -euo pipefail
TARGET=jpari@jap-m18
DISTRO=Ubuntu
FORGE=ssh://git@iteration8:2222/joe

read -r -d '' probe <<'WSL' || true
have() { if command -v "$1" >/dev/null 2>&1; then printf '  ok       %-12s %s\n' "$1" "$("$@" 2>&1 | head -n 1)"; else printf '  MISSING  %s\n' "$1"; fi; }
echo "== tools the suites and specs drive"
have php --version; have composer --version; have node --version; have npm --version
have pwsh --version; have shellcheck --version; have cppcheck --version; have rg --version
have chdman; have photorec /version; have ddrescue --version; have mkntfs --version; have ntfsls --version
have pandoc --version; have git --version
echo "== the venv"
v=~/.local/share/jp-tools-venv/bin/python
if [ -x "$v" ]; then
    "$v" - <<'PY'
import importlib.metadata as m
for p in ("pytest", "pytest-xdist", "mutmut", "ruff", "mypy", "coverage"):
    try:
        print(f"  ok       {p:12s} {m.version(p)}")
    except m.PackageNotFoundError:
        print(f"  MISSING  {p}")
PY
else
    echo "  MISSING  ~/.local/share/jp-tools-venv"
fi
echo "== the clone"
if [ -d ~/projects/JP_TOOLS/.git ]; then
    echo "  here  $(git -C ~/projects/JP_TOOLS rev-parse --short HEAD) on $(git -C ~/projects/JP_TOOLS rev-parse --abbrev-ref HEAD)"
    echo "  forge $(git ls-remote "$FORGE_URL" refs/heads/master | cut -c1-7)"
else
    echo "  MISSING  ~/projects/JP_TOOLS"
fi
echo "== the box"
echo "  cores $(nproc), memory $(free -g | sed -n 's/^Mem: *\([0-9]*\).*/\1/p') GB, core_pattern $(cat /proc/sys/kernel/core_pattern)"
WSL

body=$(printf 'FORGE_URL=%s/JP_TOOLS.git\n%s\n' "$FORGE" "$probe" | base64 -w0)
ssh -o BatchMode=yes -o ConnectTimeout=15 "$TARGET" "wsl.exe -d $DISTRO -e bash -c \"echo $body | base64 -d | bash\""
