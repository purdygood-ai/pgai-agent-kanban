#!/usr/bin/env bash
# ---------------------------------------------------------------------------
# fixture_bare_mktemp_d_runtime_script.sh
# ---------------------------------------------------------------------------
# DELIBERATE BAD EXAMPLE -- DO NOT COPY THIS PATTERN INTO REAL SCRIPTS.
#
# This file is a lint fixture for the extended bare-mktemp-d detection added
# in v0.43.0 (task CODER-20260602-050-lint-runtime-mktemp-d).
#
# It contains a bare $(mktemp -d) call (no -p flag, no path argument) inside
# what appears to be a runtime script.  This is exactly the anti-pattern that
# lint_test_anti_patterns.py must flag when scanning team/scripts/ and its
# subdirectories.
#
# Purpose: when test_lint_bare_mktemp_d_runtime_detection.py runs
# _scan_file() with runtime_only=True directly against this file, it proves
# that the _AP2_SH_BARE_MKTEMP_D detection rule fires on a simulated runtime
# script path.
#
# This file is intentionally placed under team/tests/fixtures/ which is
# excluded from the normal CI lint scan (the 'fixtures' guard in
# lint_test_anti_patterns.py skips that directory).  Only the unit test
# exercises it via _scan_file() directly.
#
# Allowed form (what should be used instead):
#   source team/scripts/lib/temp.sh
#   WORKDIR=$(pgai_mktemp_d workdir)
# ---------------------------------------------------------------------------

set -euo pipefail

# The line below is the deliberate anti-pattern.  Do not copy this.
WORKDIR="$(mktemp -d)"
trap 'rm -rf "$WORKDIR"' EXIT

echo "Working in: $WORKDIR"
