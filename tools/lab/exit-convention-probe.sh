#!/bin/sh
# What does a tool exit with on FINDINGS, on CLEAN, and on REFUSAL?
#
# Built ad hoc on 2026-09-01 and retyped four times, which is the argument for
# it being a file. It produced the nine-runner table in JP_TOOLS
# METHODOLOGY-s10-DRAFT.md item 17, and the finding that check.py's single
# "error when returncode not in (0,1)" rule is wrong for three of its twelve
# runners -- cppcheck inverts it, the stylelint node runner means "crashed",
# and phpcs exits 2 on an ORDINARY findings run.
#
# WHY IT IS NOT "run the tool and look": the answer is per tool, it is three
# cases not one, and the case that matters -- refusal -- is the one nobody
# constructs by accident. Every wrong answer this fleet got about exit codes
# came from testing one case.
#
# WHAT IT WAS TESTED AGAINST: nine runners on joe-MacBookAir, 2026-09-01 --
# ruff 0.16.1, mypy, eslint, prettier, stylelint, cppcheck, phpstan, phpcs,
# rector. Positive control is built in: it REFUSES a run where dirty and clean
# are the same path, because that yields three identical rows that look like a
# finding about the tool and are a fact about the inputs. That guard exists
# because this script's own first demo did exactly that.
#
# WHAT IT WAS NOT TESTED AGAINST: one box, one afternoon, one version of each
# tool. Nothing on Windows. No tool whose exit codes are configurable at
# runtime, which several linters have and none of the nine used. It reports
# THREE cases; a tool with a fourth meaningful code will be described
# incompletely and the script will not say so.
#
# WHERE IT MISLED ITS OWN AUTHOR: the first four phpcs rows were wrong because
# the probe ran RAW phpcs while check.py invokes it with --standard. Under
# phpcs's PEAR default a deliberately tidy file reported five errors and one
# warning, the same counts as the deliberately dirty one by coincidence, and
# that near-match was nearly written up as an anomaly worth investigating.
#
# USAGE
#   exit-convention-probe.sh "<cmd template with {} for the target>" \
#                            <dirty-file> <clean-file> <missing-path>
# EXAMPLE
#   exit-convention-probe.sh "ruff check --output-format json {}" bad.py ok.py nope.py
#
# RUN IT WITH THE TOOL'S REAL CONFIGURATION. A probe that omits the project's
# --standard or --config measures a different instrument: four phpcs rows were
# wrong that way before anyone noticed, and the wrong numbers looked plausible.
set -u
[ $# -ge 4 ] || { echo "usage: $0 '<cmd with {}>' <dirty> <clean> <missing>"; exit 2; }
TMPL=$1; DIRTY=$2; CLEAN=$3; MISSING=$4
probe() {
    lbl=$1; target=$2
    cmd=$(printf '%s' "$TMPL" | sed "s#{}#$target#")
    out=$(eval "$cmd" 2>/dev/null); rc=$?
    err=$(eval "$cmd" 2>&1 >/dev/null)
    printf '  %-22s rc=%-3s stdout=%-6s stderr=%s\n' "$lbl" "$rc" \
        "$([ -n "$out" ] && echo yes || echo EMPTY)" \
        "$([ -n "$err" ] && echo yes || echo EMPTY)"
}
# REFUSE A NON-DISCRIMINATING RUN. Feeding the same file as dirty and clean
# yields three identical rows that look like a finding about the tool and are
# a fact about the inputs. Done twice on the day this was written, once with
# stylelint and once in this script's own first demo.
if [ "$DIRTY" = "$CLEAN" ]; then
    echo "REFUSING: dirty and clean are the same path ($DIRTY)."
    echo "  Three identical rows would tell you nothing about the tool."
    exit 2
fi
for f in "$DIRTY" "$CLEAN"; do
    [ -e "$f" ] || { echo "REFUSING: $f does not exist; that is the MISSING case, not a fixture."; exit 2; }
done
[ -e "$MISSING" ] && { echo "REFUSING: $MISSING exists, so the missing-target row is not testing a missing target."; exit 2; }
echo "probing: $TMPL"
probe "file WITH findings" "$DIRTY"
probe "clean file"         "$CLEAN"
probe "target missing"     "$MISSING"
echo "  -- read it as: which rc means REFUSAL, and does any rc mean two things?"
