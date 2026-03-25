# JP_TOOLS install — Windows PowerShell
Write-Host "==> Python tools"
pip install --upgrade ruff mypy pytest pytest-cov

Write-Host "`n==> Node tools"
if (Get-Command npm -ErrorAction SilentlyContinue) {
    npm install -g eslint prettier
} else {
    Write-Host "    npm not found — install Node.js from https://nodejs.org then re-run"
}

Write-Host "`n==> PHP tools"
if ((Get-Command php -ErrorAction SilentlyContinue) -and (Get-Command composer -ErrorAction SilentlyContinue)) {
    composer install --no-interaction
} else {
    Write-Host "    php or composer not found — install both then run: composer install"
}

Write-Host "`nDone. Usage:"
Write-Host "  python JP_TOOLS\check.py <path> --pretty"
Write-Host "  python JP_TOOLS\fix.py   <path> --dry-run"
