# JP_TOOLS recovery tools installer (Windows)
# Downloads and extracts Windows-native recovery and forensics tools.
# Run from PowerShell (admin not required for download/extract).

$ErrorActionPreference = "Stop"
$toolsDir = Join-Path $env:USERPROFILE "tools"

Write-Host "==> JP_TOOLS Recovery Tools Installer (Windows)" -ForegroundColor Cyan
Write-Host ""

# --- TestDisk / PhotoRec ---
$testdiskDir = Join-Path $toolsDir "testdisk"
if (Test-Path (Join-Path $testdiskDir "testdisk-7.1\photorec_win.exe")) {
    Write-Host "  + testdisk/photorec already installed" -ForegroundColor Green
} else {
    Write-Host "  Installing testdisk/photorec..."
    $url = "https://www.cgsecurity.org/testdisk-7.1.win64.zip"
    $zip = Join-Path $env:TEMP "testdisk-7.1.win64.zip"
    New-Item -ItemType Directory -Path $testdiskDir -Force | Out-Null
    Invoke-WebRequest -Uri $url -OutFile $zip
    Expand-Archive -Path $zip -DestinationPath $testdiskDir -Force
    Remove-Item $zip
    Write-Host "  + testdisk/photorec installed" -ForegroundColor Green
}

# --- Sleuth Kit ---
$sleuthkitDir = Join-Path $toolsDir "sleuthkit"
if (Test-Path (Join-Path $sleuthkitDir "sleuthkit-4.14.0-win32\bin\fls.exe")) {
    Write-Host "  + sleuthkit already installed" -ForegroundColor Green
} else {
    Write-Host "  Installing sleuthkit..."
    $url = "https://github.com/sleuthkit/sleuthkit/releases/download/sleuthkit-4.14.0/sleuthkit-4.14.0-win32.zip"
    $zip = Join-Path $env:TEMP "sleuthkit-4.14.0-win32.zip"
    New-Item -ItemType Directory -Path $sleuthkitDir -Force | Out-Null
    Invoke-WebRequest -Uri $url -OutFile $zip
    Expand-Archive -Path $zip -DestinationPath $sleuthkitDir -Force
    Remove-Item $zip
    Write-Host "  + sleuthkit installed" -ForegroundColor Green
}

# --- MAME / chdman ---
$mameDir = Join-Path $toolsDir "mame"
if (Test-Path (Join-Path $mameDir "chdman.exe")) {
    Write-Host "  + chdman already installed" -ForegroundColor Green
} else {
    Write-Host "  Installing MAME (for chdman)..."
    $latestRelease = Invoke-RestMethod -Uri "https://api.github.com/repos/mamedev/mame/releases/latest"
    $asset = $latestRelease.assets | Where-Object { $_.name -match "x64\.exe$" } | Select-Object -First 1
    if ($asset) {
        $exe = Join-Path $env:TEMP $asset.name
        Invoke-WebRequest -Uri $asset.browser_download_url -OutFile $exe
        New-Item -ItemType Directory -Path $mameDir -Force | Out-Null
        Start-Process -FilePath $exe -ArgumentList "-o`"$mameDir`" -y" -Wait -NoNewWindow
        Remove-Item $exe
        Write-Host "  + chdman installed" -ForegroundColor Green
    } else {
        Write-Host "  - Could not find MAME x64 release" -ForegroundColor Red
    }
}

# --- Verify ---
Write-Host ""
Write-Host "==> Verifying installed tools" -ForegroundColor Cyan

$tools = @(
    @{ Name = "photorec"; Path = Join-Path $testdiskDir "testdisk-7.1\photorec_win.exe" },
    @{ Name = "testdisk"; Path = Join-Path $testdiskDir "testdisk-7.1\testdisk_win.exe" },
    @{ Name = "qphotorec"; Path = Join-Path $testdiskDir "testdisk-7.1\qphotorec_win.exe" },
    @{ Name = "fls"; Path = Join-Path $sleuthkitDir "sleuthkit-4.14.0-win32\bin\fls.exe" },
    @{ Name = "icat"; Path = Join-Path $sleuthkitDir "sleuthkit-4.14.0-win32\bin\icat.exe" },
    @{ Name = "mmls"; Path = Join-Path $sleuthkitDir "sleuthkit-4.14.0-win32\bin\mmls.exe" },
    @{ Name = "img_stat"; Path = Join-Path $sleuthkitDir "sleuthkit-4.14.0-win32\bin\img_stat.exe" },
    @{ Name = "fidentify"; Path = Join-Path $testdiskDir "testdisk-7.1\fidentify_win.exe" },
    @{ Name = "chdman"; Path = Join-Path $mameDir "chdman.exe" }
)

foreach ($tool in $tools) {
    if (Test-Path $tool.Path) {
        Write-Host "    + $($tool.Name)" -ForegroundColor Green
    } else {
        Write-Host "    - $($tool.Name) (not found)" -ForegroundColor Red
    }
}

Write-Host ""
Write-Host "==> WSL tools (optional, for additional capabilities)" -ForegroundColor Cyan
Write-Host "  Run install-recovery.sh inside WSL for: foremost, ddrescue, binwalk, chdman, scalpel"
Write-Host ""
Write-Host "Done." -ForegroundColor Green
