#!/usr/bin/env bash
# Seeded fixture: a driver that sources two stage files both defining the
# same function name. Used by team/tests/unit/test_lint_engine_size_dupe.py
# to prove the duplication check fires on a real same-process collision
# (both stage files are reachable from this driver, so bash's
# last-definition-wins semantics would silently shadow one implementation).
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

source "${SCRIPT_DIR}/lib/stage_a.sh"
source "${SCRIPT_DIR}/lib/stage_b.sh"

seeded_shared_helper
