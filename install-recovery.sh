#!/bin/bash
# JP_TOOLS recovery tools installer
# Installs data recovery and forensics tools.
# Run directly on Linux, or via WSL on Windows.
set -e

echo "==> Installing recovery & forensics tools"

# Detect package manager
if command -v apt &>/dev/null; then
    PKG="apt"
    sudo apt update -qq

    echo ""
    echo "--- File recovery / carving ---"
    sudo apt install -y testdisk       # photorec + testdisk
    sudo apt install -y foremost       # alternative file carver
    sudo apt install -y scalpel        # configurable file carver

    echo ""
    echo "--- Disk imaging ---"
    sudo apt install -y gddrescue      # ddrescue — fault-tolerant dd

    echo ""
    echo "--- Filesystem recovery ---"
    sudo apt install -y ntfs-3g        # includes ntfsundelete
    sudo apt install -y extundelete 2>/dev/null || echo "    extundelete not available in this repo — skipping"

    echo ""
    echo "--- Analysis / forensics ---"
    sudo apt install -y sleuthkit      # filesystem forensics (fls, icat, etc.)
    sudo apt install -y binwalk        # embedded file/firmware scanner

    echo ""
    echo "--- Compressed storage ---"
    sudo apt install -y mame-tools     # chdman — CHD create/verify/extract/diff

    echo ""
    echo "--- Format converters (for CHD import pipeline) ---"
    sudo apt install -y nrg2iso        # Nero image converter
    sudo apt install -y mdf2iso        # Alcohol 120% image converter
    sudo apt install -y ecm            # ECM error code modeler (ecm2bin)
    sudo apt install -y p7zip-full     # 7z archive extraction
    sudo apt install -y ffmpeg         # audio/video format conversion
    sudo apt install -y maxcso 2>/dev/null || echo "    maxcso not in repo — install from github.com/unknownbrackets/maxcso"

elif command -v dnf &>/dev/null; then
    PKG="dnf"
    sudo dnf install -y testdisk foremost ddrescue ntfs-3g sleuthkit binwalk

elif command -v pacman &>/dev/null; then
    PKG="pacman"
    sudo pacman -S --noconfirm testdisk foremost ddrescue ntfs-3g sleuthkit binwalk

elif command -v brew &>/dev/null; then
    PKG="brew"
    brew install testdisk foremost ddrescue sleuthkit binwalk

else
    echo "Error: No supported package manager found (apt, dnf, pacman, brew)"
    exit 1
fi

echo ""
echo "==> Windows-native tools"
# Check if we're in WSL and native testdisk exists
WIN_TESTDISK="$HOME/tools/testdisk/testdisk-7.1/photorec_win.exe"
if [ -f "/mnt/c/Users/jpari/tools/testdisk/testdisk-7.1/photorec_win.exe" ]; then
    echo "    Native Windows photorec found at ~/tools/testdisk/"
elif [ -f "$WIN_TESTDISK" ]; then
    echo "    Native Windows photorec found at $WIN_TESTDISK"
else
    echo "    Native Windows photorec not found."
    echo "    Download from: https://www.cgsecurity.org/wiki/TestDisk_Download"
    echo "    Extract to: ~/tools/testdisk/"
fi

echo ""
echo "==> Verifying installed tools"
TOOLS="photorec testdisk foremost ddrescue ntfsundelete fls binwalk chdman nrg2iso mdf2iso ecm2bin 7z ffmpeg"
for tool in $TOOLS; do
    if command -v "$tool" &>/dev/null; then
        echo "    ✓ $tool"
    else
        echo "    ✗ $tool (not found)"
    fi
done

# Optional: scalpel, extundelete
for tool in scalpel extundelete; do
    if command -v "$tool" &>/dev/null; then
        echo "    ✓ $tool"
    else
        echo "    - $tool (optional, not installed)"
    fi
done

echo ""
echo "Done. Recovery tools ready."
echo ""
echo "Usage:"
echo "  python recover.py <source> <output> --profile <code|art|writing|roms|all|full>"
echo "  python recover.py <source> --image <output.img>"
echo "  python recover.py --list-profiles"
