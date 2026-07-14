#!/usr/bin/env bash
# status_glyphs.sh — single home for tmux status-bar glyph literals.
#
# Sourced by team/scripts/dashboard/status-bottom.sh and
# team/scripts/dashboard/status-right.sh.  All glyph literals live here
# and nowhere else.  The two siblings' glyph-consuming blocks delegate
# entirely to this lib (wake-bracket precedent).
#
# Glyphs are only prepended in rich mode (USE_COLOR / color-capable terminal).
# NO_COLOR / TERM=dumb output is unchanged — callers omit the glyph prefix.
#
# Usage:
#   source "$(path-to)/team/scripts/lib/status_glyphs.sh"
#   # Variables available after sourcing:
#   #   GLYPH_VERSION    — leading glyph for install version and project:version segments
#   #   GLYPH_PM_AUTO    — leading glyph for PM:auto segment
#   #   GLYPH_PM_MANUAL  — leading glyph for PM:manual segment
#   #   GLYPH_APPROVAL   — leading glyph for APPROVAL(n) segment
#   #   GLYPH_HALT       — leading glyph for HALT (full stop) segment
#   #   GLYPH_HALT_AFTER — leading glyph for HALT-AFTER (draining) segment
#   #   GLYPH_TIMESTAMP  — leading glyph for the date/time segment
#
# Retheme: edit the literals in this file only.  The two sibling scripts
# pick up the change automatically on next render.
#
# Terminal note: emoji glyphs may render as double-width on some terminals.
# If a terminal mis-aligns the bar, set NO_COLOR for that session — the
# dumb-mode bar is byte-identical to the pre-glyph baseline by design.

# Guard against double-sourcing.
[[ -n "${_STATUS_GLYPHS_LOADED:-}" ]] && return 0
_STATUS_GLYPHS_LOADED=1

# Version segments (install version and project:version)
GLYPH_VERSION="📝"

# PM mode segments
GLYPH_PM_AUTO="🟢"
GLYPH_PM_MANUAL="🟡"

# Approval gate segment (already present before this RC; kept here for
# the single-source principle).
GLYPH_APPROVAL="✋"

# Halt segments — HALT (full stop) vs HALT-AFTER (draining) must be
# visually distinct at a glance.
GLYPH_HALT="🛑"
GLYPH_HALT_AFTER="⚠️"

# Timestamp segment
GLYPH_TIMESTAMP="📅"
