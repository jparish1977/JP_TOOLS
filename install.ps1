# JP_TOOLS install -- Windows PowerShell
[Diagnostics.CodeAnalysis.SuppressMessageAttribute('PSAvoidUsingWriteHost', '', Justification = 'an installer run by a person at a console: its progress lines are for them')]
param()

Write-Host "==> Python tools (pinned in requirements-dev.txt)"
pip install -r "$PSScriptRoot\requirements-dev.txt"

Write-Host "`n==> Node tools"
if (Get-Command npm -ErrorAction SilentlyContinue) {
    npm --prefix "$PSScriptRoot" install
} else {
    Write-Host "    npm not found -- install Node.js from https://nodejs.org then re-run"
}

Write-Host "`n==> PHP tools"
if ((Get-Command php -ErrorAction SilentlyContinue) -and (Get-Command composer -ErrorAction SilentlyContinue)) {
    composer --working-dir="$PSScriptRoot" install --no-interaction
} else {
    Write-Host "    php or composer not found -- install both then run: composer install"
}

Write-Host "`nDone. Usage:"
Write-Host "  python JP_TOOLS\check.py <path> --pretty"
Write-Host "  python JP_TOOLS\fix.py   <path> --dry-run"
