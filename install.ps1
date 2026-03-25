# JP_TOOLS install — Windows PowerShell
Write-Host "==> Python tools"
pip install --upgrade ruff mypy pytest pytest-cov

Write-Host "`n==> Node tools"
if (Get-Command npm -ErrorAction SilentlyContinue) {
    npm install -g eslint prettier
} else {
    Write-Host "    npm not found — install Node.js from https://nodejs.org then re-run"
}

Write-Host "`nDone. Usage:"
Write-Host "  python JP_TOOLS\check.py <path> --pretty"
Write-Host "  python JP_TOOLS\fix.py   <path> --dry-run"
