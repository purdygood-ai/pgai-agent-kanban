#!/usr/bin/env bash
# dashboard_constants.sh — Shared constants for dashboard layout and verification.
#
# Source this file in any script that needs to know the canonical dashboard
# layout values.  Define each constant ONCE here; consumers must not
# hardcode these values independently (anti-divergence guarantee).
#
# Usage:
#   source "${SCRIPT_DIR}/../lib/dashboard_constants.sh"   # from dashboard/
#   source "${SCRIPT_DIR}/lib/dashboard_constants.sh"      # from scripts/

# ---------------------------------------------------------------------------
# Window-0 layout constants
# ---------------------------------------------------------------------------

# PGAI_WINDOW0_RIGHT_COL_PCT — Width of window-0's right column (QUEUES pane)
# as an integer percentage of the total window width.
#
# This value is consumed by:
#   - team/scripts/dashboard/create.sh       (split-window + resize-pane)
#   - team/scripts/dashboard/verify-window0-geometry.sh (geometry assertion)
#
# Changing this value here automatically propagates to both the layout and
# the verifier, preserving the anti-divergence guarantee.
#
# Value: 25  (confirmed correct in production; hardcoded here as convention over configuration)
PGAI_WINDOW0_RIGHT_COL_PCT=25
