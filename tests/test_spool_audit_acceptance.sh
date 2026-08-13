#!/bin/bash
# Does spool-audit.py clear the bar Joe set?
#   1. not worse than `ls` -- nothing may be invisible
#   2. it works
#   3. none of the flaws we fought all day
#
# Each check names the flaw it is guarding against. Prints PASS/FAIL per case.
#
# This is the black-box suite: it drives the real CLI and reads what an operator
# would read, where tests/test_spool_audit.py tests the functions underneath.
# Both are needed. Round after round, the bug was in the gap between a function
# behaving correctly and the report describing it correctly.
#
# FLAW 5's fixture is the one piece here that was not invented. The name
# cups-dbus-notifier-lockfile came from a real CUPS spool, and no fixture built
# from what we already suspected would have contained it.
set -u
# Resolved from this script's own location, not a hardcoded home directory: the
# repo is deployed to other machines under other paths, and a wrong T would run
# every check against the wrong copy of the tool -- or against no tool at all,
# which prints failures that look like findings.
REPO=$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)
T="$REPO/spool-audit.py"
if [ ! -f "$T" ]; then
  echo "FAIL  cannot find spool-audit.py at $T"
  exit 2
fi
W=$(mktemp -d); trap 'chmod -R u+rwX "$W" 2>/dev/null; rm -rf "$W"' EXIT
pass=0; fail=0; skip=0
ok () { echo "  PASS  $1"; pass=$((pass+1)); }
no () { echo "  FAIL  $1"; fail=$((fail+1)); }
sk () { echo "  SKIP  $1"; skip=$((skip+1)); }
ck () { if [ "$2" = "$3" ]; then ok "$1"; else no "$1 (got '$2', want '$3')"; fi; }
# An exit code on its own is not evidence of the reason for it. argparse exits 2
# for a malformed argument, exactly like a genuinely unreadable spool does, so
# when a bad edit to this script turned `--spool "$W/x"` into `--spool"$W/x"`
# all three exit-2 cases below still passed while testing nothing at all. Assert
# the explanation too, so the code and the reason have to agree.
has () { case "$3" in *"$2"*) ok "$1";; *) no "$1 (output lacked '$2')";; esac; }

echo "FLAW 1: a check that did not run must never read as a pass"
# Root bypasses permission bits, so chmod 000 does not make a directory
# unreadable to it and these two cannot be tested as root -- and the tool is
# normally run under sudo, so that is not a hypothetical. Skipped loudly and
# counted separately rather than silently passing, which would make this suite
# commit the very flaw it is named after.
if [ "$(id -u)" = 0 ]; then
  # Covered uid-independently by test_unreadable_tempdir_is_never_clean in
  # tests/test_spool_audit.py, which injects the PermissionError instead of
  # relying on permission bits root does not obey.
  sk "unreadable TempDir exits 2 (running as root; chmod 000 does not apply)"
  sk "unreadable is reported as denied (running as root)"
  sk "unreadable never says clean (running as root)"
else
  mkdir -p "$W/denied/tmp"; printf '%%!PS\n' > "$W/denied/tmp/doc.ps"; chmod 000 "$W/denied/tmp"
  out=$(python3 "$T" --spool "$W/denied" --conf /dev/null 2>&1); rc=$?
  ck "unreadable TempDir exits 2" "$rc" "2"
  has "unreadable is reported as denied" "permission denied" "$out"
  case "$out" in *"spool is clean"*) no "unreadable never says clean";; *) ok "unreadable never says clean";; esac
  chmod 755 "$W/denied/tmp"
fi
out=$(python3 "$T" --spool "$W/nonexistent" --conf /dev/null 2>&1); rc=$?
ck "missing path exits 2" "$rc" "2"
has "missing path says so" "THAT PATH DOES NOT EXIST" "$out"
touch "$W/afile"; out=$(python3 "$T" --spool "$W/afile" --conf /dev/null 2>&1); rc=$?
ck "not-a-directory exits 2" "$rc" "2"
has "not-a-directory says so" "THAT PATH IS NOT A DIRECTORY" "$out"

echo "FLAW 2: nothing may be invisible (the ls baseline)"
mkdir -p "$W/acct/tmp/.cache"
touch "$W/acct/d00085-001" "$W/acct/c00085"
echo x > "$W/acct/d00085-001.bak"; echo y > "$W/acct/stray"; mkdir "$W/acct/subdir"
: > "$W/acct/empty-stray"   # zero length: the case that slipped through
printf '*PPD-Adobe: "4.3"\n' > "$W/acct/tmp/ppd"; printf '%%!PS\n' > "$W/acct/tmp/real.ps"
echo z > "$W/acct/tmp/.cache/fc"
python3 "$T" --spool "$W/acct" --include-control --conf /dev/null > "$W/o.txt" 2>&1
missing=0
while read -r f; do
  # Whole-entry match on the relative path, not a substring of any basename.
  # `grep -qF d00085-001` is satisfied by the report line for d00085-001.bak,
  # so a regression that dropped the real document while keeping its backup
  # passed this check -- and "nothing is invisible" is the one promise here
  # that is strictly stronger than `ls`. The report prints each entry as an
  # indented relative path, optionally followed by an annotation (" -> target",
  # " (subdirectory, not examined)"); a parent directory appears only as the
  # prefix of its children, hence the "/" case.
  awk -v p="$f" '
    { line = $0; sub(/^[[:space:]]+/, "", line)
      if (index(line, p) == 1) {
        c = substr(line, length(p) + 1, 1)
        if (c == "" || c == " " || c == "/") { found = 1 }
      } }
    END { exit !found }' "$W/o.txt" \
    || { missing=$((missing+1)); echo "        invisible: $f"; }
done < <(find "$W/acct" -mindepth 1 | sed "s|^$W/acct/||")
ck "every path under the spool is reported" "$missing" "0"

echo "FLAW 3: never claim a secret was destroyed when it was not"
mkdir -p "$W/sym/tmp" "$W/vault"; printf '%%!PS\nSECRET\n' > "$W/vault/keep.ps"
ln -s "$W/vault/keep.ps" "$W/sym/tmp/link.ps"
python3 "$T" --spool "$W/sym" --purge --conf /dev/null >/dev/null 2>&1
[ -f "$W/vault/keep.ps" ] && ok "symlink target survives purge" || no "symlink target survives purge"
mkdir -p "$W/root/tmp2"; printf '%%!PS\nOUT\n' > "$W/root/tmp2/out.ps"
mkdir -p "$W/esc"; ln -s "$W/root/tmp2" "$W/esc/tmp"
python3 "$T" --spool "$W/esc" --purge --conf /dev/null >/dev/null 2>&1
[ -f "$W/root/tmp2/out.ps" ] && ok "symlinked TempDir root not followed" || no "symlinked TempDir root not followed"

echo "FLAW 4: a fix must apply to every path, not one of them"
mkdir -p "$W/nl"; printf '%%!PS\n' > "$W/nl/$(printf 'evil\nd00099-001')"
python3 "$T" --spool "$W/nl" --conf /dev/null 2>&1 | grep -q "d00099-001" \
  && ok "newline filename survives the listing" || no "newline filename survives the listing"

echo "FLAW 5: known-harmless things must not be reported as secrets"
mkdir -p "$W/noise/tmp/.cache/fontconfig"
printf '*PPD-Adobe: "4.3"\n' > "$W/noise/tmp/ppd"
: > "$W/noise/tmp/cups-dbus-notifier-lockfile"
for i in 1 2 3 4 5; do echo bin > "$W/noise/tmp/.cache/fontconfig/c$i.cache-9"; done
out=$(python3 "$T" --spool "$W/noise" --conf /dev/null 2>&1); rc=$?
ck "a spool of only caches is clean" "$rc" "0"
case "$out" in *"spool is clean"*) ok "and says so";; *) no "and says so";; esac

echo
if [ "$skip" -gt 0 ]; then
  echo "  $pass passed, $fail failed, $skip SKIPPED (not run -- not the same as passed)"
else
  echo "  $pass passed, $fail failed"
fi
exit $((fail > 0))
