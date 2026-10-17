#!/bin/bash
# ci-image/readback.sh -- run by build.sh INSIDE a freshly built fleet CI
# image, from a directory holding the files build.sh tars in (check-pins.py,
# the manifests, jpcheck/, platformtools/, jpexit/ and this script).
#
#   WANT_UID=N bash ci-image/readback.sh
#
# Reads the image back: it runs as fleet-ci's uid N, not root, and cannot
# sudo; then every tool the image is for, each by USING it where that can
# fail: check-pins.py's powershell arm, the PHP extensions, composer, the
# shell linter, cppcheck, rg, pdftotext, ss, pandoc, dvipng with the TeX font a
# DVI names, onsgmls parsing a document and the ISO entity catalog, ffmpeg
# drawing a frame, ImageMagick writing a PNG that identify reads, ffprobe
# measuring a frame's width (exactly), nroff rendering a man page, and docbook-to-man
# (`instant`) turning a DocBook refentry into one with no parse error.
# Exit 0 only when every one passes; the first that fails ends it, saying
# which.
printf '%s' jpt-readback > /proc/$$/comm 2>/dev/null
set -eu

# `ver N CMD...` runs CMD with its output captured whole, fails on its exit
# code, then prints line N. `CMD | head -1` cannot do that: without pipefail
# a missing CMD still passed (head exits 0), and with pipefail a CMD that
# writes on after head closes dies of SIGPIPE (141), a flaky fail.
ver() {
    local n=$1 out rc=0
    shift
    out=$("$@" 2>&1) || rc=$?
    if [ "$rc" -ne 0 ]; then echo "FAIL: $* exited $rc" >&2; return 1; fi
    printf '%s\n' "$out" | head -n "$n" | tail -n 1
}

# `has TEXT CMD...`: CMD exits 0 AND says TEXT. docbook-to-man exits 0 on a
# document onsgmls rejects, so an exit code alone proves nothing.
has() {
    local want=$1 out rc=0
    shift
    out=$("$@" 2>&1) || rc=$?
    case "$rc:$out" in
        0:*"$want"*) echo "$* -> $want" ;;
        *) echo "FAIL: $* exited $rc without saying $want" >&2; printf '%s\n' "$out" >&2; return 1 ;;
    esac
}

# `is TEXT CMD...`: CMD exits 0 and says exactly TEXT, nothing more: a
# measurement must equal its value, not contain it (16 is in 160).
is() {
    local want=$1 out rc=0
    shift
    out=$("$@" 2>&1) || rc=$?
    if [ "$rc" -eq 0 ] && [ "$out" = "$want" ]; then echo "$* -> $want"; return 0; fi
    echo "FAIL: $* exited $rc saying [$out], not [$want]" >&2
    return 1
}

# A valid one-line document: `onsgmls -v` alone reads an empty one and exits
# 1 ("end of document in prolog").
sgml_probe() { printf '<!DOCTYPE x [<!ELEMENT x - O EMPTY>]><x>' | onsgmls -v; }
man_probe() { printf '.TH PROBE 1\n.SH NAME\nprobe \\- a probe page\n' | nroff -man; }
docbook_probe() {
    printf '%s\n' '<!DOCTYPE refentry PUBLIC "-//OASIS//DTD DocBook V4.1//EN"><refentry><refmeta><refentrytitle>probe</refentrytitle><manvolnum>1</manvolnum></refmeta><refnamediv><refname>probe</refname><refpurpose>a probe page</refpurpose></refnamediv><refsect1><title>T</title><para>x</para></refsect1></refentry>' > p.sgml
    docbook-to-man p.sgml 2> p.err
    if [ -s p.err ]; then cat p.err; return 1; fi
}

if [ "$(id -u)" != "$WANT_UID" ]; then echo "FAIL: runs as uid $(id -u), not fleet-ci uid $WANT_UID" >&2; exit 1; fi
echo "user: $(id -un) uid $(id -u)"
if sudo -n true 2>/dev/null; then echo "FAIL: fleet-ci can sudo" >&2; exit 1; fi
echo "sudo: refused"
python3 check-pins.py powershell
for ext in gd sqlite3 pdo_sqlite; do
    php -m | grep -qx "$ext" || { echo "FAIL: PHP extension $ext missing" >&2; exit 1; }
done
echo "php extensions: gd sqlite3 pdo_sqlite"
ver 1 composer --version
ver 2 shellcheck --version
ver 1 cppcheck --version
ver 1 rg --version
ver 1 pdftotext -v
ver 1 ss -V
ver 1 pandoc --version
ver 1 dvipng --version
ver 1 kpsewhich cmr10.tfm
ver 1 sgml_probe
test -f /usr/share/sgml/entities/sgml-iso-entities-8879.1986/catalog \
    || { echo "FAIL: sgml-data ISO entity catalog missing" >&2; exit 1; }
echo "sgml-data: ISO entity catalog present"
# Says nothing when it works; the line after says so.
ver 1 ffmpeg -v error -f lavfi -i color=c=black:s=16x16:d=0.1 -frames:v 1 -f null - > /dev/null
echo "ffmpeg: drew a frame"
ver 1 ffmpeg -version
ver 1 convert -size 8x8 xc:white probe.png
ver 1 identify probe.png
is 16 ffprobe -v error -f lavfi -i color=c=black:s=16x16:d=0.1 -show_entries stream=width -of csv=p=0
has "a probe page" man_probe
has '.TH "probe" "1"' docbook_probe
# A job may trace its own children: the kept-verdict cache depends on it.
has 'execve("' strace -f -qq -e trace=execve true
