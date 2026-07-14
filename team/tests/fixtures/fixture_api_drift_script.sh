#!/usr/bin/env bash
# fixture_api_drift_script.sh
#
# Deliberate drift fixture for lint_api_parity.py testing.
#
# This script intentionally declares an extra operator flag (--frobnicate)
# that has no corresponding field in any API body model.  The parity lint
# must detect this as drift and exit 1 naming this script, the flag, and
# the model class.
#
# This file lives under tests/fixtures/ and is excluded from gated runner
# pytest collection by design (fixtures/ is in lint_orphan_tests.py's
# _EXCLUDED_DIRS).  Do not move it out of fixtures/.

set -euo pipefail

OPERATOR_VALID_FLAGS=(project frobnicate help)

# (Body of the script is intentionally minimal — only the flag declaration
# matters for parity-lint testing.)
echo "fixture_api_drift_script: this is a test fixture, not a real operator script." >&2
exit 1
