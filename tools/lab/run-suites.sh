#!/usr/bin/env bash
# tools/lab/run-suites.sh -- forwards to the promoted ../../run-suites.sh.
#
# ANSWERS: nothing of its own. run-suites.sh was promoted out of the lab once
#   every merge depended on it (METHODOLOGY 2.9); this path stays so callers
#   that name it (other repos' review scripts, checklists) keep working.
#
# TESTED AGAINST: tests/test_run_suites.py runs this path once and gets the
#   promoted script's count line and exit code.
#
# NOT TESTED AGAINST: a copy of this file moved without the repo around it;
#   it finds the real script relative to itself.
exec "$(dirname "$0")/../../run-suites.sh" "$@"
