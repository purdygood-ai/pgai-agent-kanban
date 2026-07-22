#!/usr/bin/env bash
# fixture_no_help_script.sh
# =========================
# Deliberate bad-example fixture: a shell script that accepts positional
# arguments but does not implement a --help or -h flag.
#
# This fixture is excluded from the normal CI lint scan by the fixtures/
# directory guard.  It is scanned DIRECTLY by the unit tests in
# test_lint_help_presence.py to prove that lint_help_presence.py correctly
# reports a violation when a script has no help handler.
#
# DO NOT add --help handling here — the whole point is that the linter
# must flag this file when it is not on the exempt list.

set -euo pipefail

NAME="${1:-world}"
echo "Hello, ${NAME}!"
