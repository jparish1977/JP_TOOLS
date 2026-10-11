#!/usr/bin/env bash
# tools/lab/i8-proof.sh -- forwards to the promoted ../../prove-head.sh.
#
# ANSWERS: nothing of its own. The proof was promoted out of the lab as
#   prove-head.sh once every merge depended on it (METHODOLOGY 2.9); this path
#   stays so batch jobs and checklists that name it keep working.
#
# TESTED AGAINST: tests/test_prove_head.py runs this path once and gets the
#   promoted script's verdict and exit code.
#
# NOT TESTED AGAINST: a copy of this file moved without the repo around it;
#   it finds the real script relative to itself.
exec "$(dirname "$0")/../../prove-head.sh" "$@"
