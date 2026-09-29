#!/bin/bash
# JP_TOOLS recovery tools installer: data recovery, imaging and forensics tools.
# Run directly on Linux, or inside WSL on Windows. apt installs everything the
# recovery scripts use; dnf, pacman and brew install the core set.
set -e

install_apt() {
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
}

# The recovery tools, by whichever package manager this system has.
install_packages() {
    if command -v apt &>/dev/null; then
        install_apt
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
}

# Windows-native photorec, which reads a Windows drive letter directly.
find_photorec_win() {
    echo ""
    echo "==> Windows-native photorec (reads a Windows drive letter directly)"
    local candidate
    for candidate in "$HOME"/tools/testdisk/testdisk-*/photorec_win.exe \
                     /mnt/c/Users/*/tools/testdisk/testdisk-*/photorec_win.exe; do
        if [ -f "$candidate" ]; then
            echo "    found at $candidate"
            return
        fi
    done
    echo "    not found. Download from: https://www.cgsecurity.org/wiki/TestDisk_Download"
    echo "    and extract to ~/tools/testdisk/ in your Windows home"
}

# One line per TOOL: + when installed, else MARK and LABEL.
report_tools() {
    local mark=$1 label=$2 tool
    shift 2
    for tool in "$@"; do
        if command -v "$tool" &>/dev/null; then
            echo "    + $tool"
        else
            echo "    $mark $tool $label"
        fi
    done
}

echo "==> Installing recovery & forensics tools"
install_packages
find_photorec_win

echo ""
echo "==> Verifying installed tools"
report_tools x "(not found)" photorec testdisk foremost ddrescue ntfsundelete ntfsls \
    ntfscat fls binwalk chdman nrg2iso mdf2iso ecm2bin 7z
report_tools - "(optional, not installed)" scalpel extundelete maxcso

echo ""
echo "Done. Recovery tools ready."
echo ""
echo "Usage:"
echo "  python3 image-disk.py <device> <output.img>"
echo "  python3 recover.py <source> <output> --profile <code|art|writing|roms|all|full>"
echo "  python3 recover.py --list-profiles"
