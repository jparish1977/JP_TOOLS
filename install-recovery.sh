#!/bin/bash
# JP_TOOLS recovery tools installer: data recovery, imaging and forensics tools.
# Run directly on Linux, or inside WSL on Windows. apt installs everything the
# recovery scripts use; dnf, pacman and brew install the core set.
set -e

echo "==> Installing recovery & forensics tools"

if command -v apt &>/dev/null; then
    sudo apt update -qq

    echo ""
    echo "--- File recovery / carving ---"
    sudo apt install -y testdisk       # photorec + testdisk
    sudo apt install -y foremost       # alternative file carver
    sudo apt install -y scalpel        # configurable file carver

    echo ""
    echo "--- Disk imaging ---"
    sudo apt install -y gddrescue      # ddrescue: fault-tolerant dd, for image-disk.py

    echo ""
    echo "--- Filesystem recovery ---"
    sudo apt install -y ntfs-3g        # ntfsundelete, and ntfsls/ntfscat for the ntfs-* scripts
    sudo apt install -y extundelete 2>/dev/null || echo "    extundelete not available in this repo; skipping"

    echo ""
    echo "--- Analysis / forensics ---"
    sudo apt install -y sleuthkit      # filesystem forensics (fls, icat, etc.)
    sudo apt install -y binwalk        # embedded file/firmware scanner

    echo ""
    echo "--- Compressed storage ---"
    sudo apt install -y mame-tools     # chdman: CHD create/verify/extract

    echo ""
    echo "--- Format converters (for chd.py import) ---"
    sudo apt install -y nrg2iso        # Nero image converter
    sudo apt install -y mdf2iso        # Alcohol 120% image converter
    sudo apt install -y ecm            # ECM error code modeler (ecm2bin)
    sudo apt install -y p7zip-full     # 7z archive extraction
    sudo apt install -y maxcso 2>/dev/null || echo "    maxcso not in repo; install from github.com/unknownbrackets/maxcso"

elif command -v dnf &>/dev/null; then
    sudo dnf install -y testdisk foremost ddrescue ntfs-3g sleuthkit binwalk

elif command -v pacman &>/dev/null; then
    sudo pacman -S --noconfirm testdisk foremost ddrescue ntfs-3g sleuthkit binwalk

elif command -v brew &>/dev/null; then
    brew install testdisk foremost ddrescue sleuthkit binwalk

else
    echo "Error: No supported package manager found (apt, dnf, pacman, brew)"
    exit 1
fi

echo ""
echo "==> Windows-native photorec (reads a Windows drive letter directly)"
found=""
for candidate in "$HOME"/tools/testdisk/testdisk-*/photorec_win.exe \
                 /mnt/c/Users/*/tools/testdisk/testdisk-*/photorec_win.exe; do
    if [ -f "$candidate" ]; then
        found="$candidate"
        break
    fi
done
if [ -n "$found" ]; then
    echo "    found at $found"
else
    echo "    not found. Download from: https://www.cgsecurity.org/wiki/TestDisk_Download"
    echo "    and extract to ~/tools/testdisk/ in your Windows home"
fi

echo ""
echo "==> Verifying installed tools"
for tool in photorec testdisk foremost ddrescue ntfsundelete ntfsls ntfscat fls binwalk chdman nrg2iso mdf2iso ecm2bin 7z; do
    if command -v "$tool" &>/dev/null; then
        echo "    + $tool"
    else
        echo "    x $tool (not found)"
    fi
done
for tool in scalpel extundelete maxcso; do
    if command -v "$tool" &>/dev/null; then
        echo "    + $tool"
    else
        echo "    - $tool (optional, not installed)"
    fi
done

echo ""
echo "Done. Recovery tools ready."
echo ""
echo "Usage:"
echo "  python3 image-disk.py <device> <output.img>"
echo "  python3 recover.py <source> <output> --profile <code|art|writing|roms|all|full>"
echo "  python3 recover.py --list-profiles"
