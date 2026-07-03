#!/usr/bin/env bash
# team/scripts/lib/dashboard_legend.sh
#
# Legend template for the unified 8-column visibility window.
#
# Purpose
# -------
# The visibility grid encodes two dimensions of color per row:
#
#   1. PROJECT color — the small tag at the left edge of each row. Fixed per
#      project; sourced from the optional third field of projects.cfg
#      (name:priority[:display_color]) and falls back to PGAI_DEFAULT_PALETTE.
#   2. STATUS color — the color of the entry text itself. Dynamic; reflects
#      the row's lifecycle state (open / running / done / blocked).
#
# Operators must be able to re-learn that convention without leaving the
# dashboard. This file provides the one-line legend the visibility window
# renders beneath the grid.
#
# Output shape
# ------------
# A single rendered line containing two logically distinct rows of
# information separated by a vertical bar:
#
#   PROJECT: <tag> <name>  <tag> <name>  ...   |   STATUS: open  running  done  blocked
#
# The two halves can be thought of as two rows folded onto a single visual
# line — terse on purpose so the legend fits beneath the grid without
# scrolling.
#
# The status values themselves are rendered in their mapped colors so the
# operator can read the convention off the legend directly (open in the
# default text color, running amber, done green, blocked red).
#
# Color sources
# -------------
# - Project palette and per-project color resolution come from
#   team/scripts/lib/projects_cfg.sh — specifically the PGAI_DEFAULT_PALETTE
#   array and projects_cfg_color() function. This file does NOT duplicate
#   the palette; callers source projects_cfg.sh first, then iterate the
#   registered projects to fill in the PROJECT half of the legend.
# - Status-to-ANSI mapping lives in dashboard-column-render.sh
#   (status_to_color() helper). The renderer paints the status keywords in
#   the legend with the same function so legend and grid never disagree.
#
# Template variables
# ------------------
# The renderer in dashboard-column-render.sh / dashboard-create.sh is
# expected to substitute these placeholders:
#
#   {PROJECTS_BLOCK}  — concatenated "<tag> <project_name>" pairs, one per
#                       registered, non-halted project, separated by two
#                       spaces. Each <tag> is the project's color square
#                       (truecolor ANSI when the terminal supports it,
#                       "[abbr]" fallback otherwise).
#   {STATUS_BLOCK}    — the four status keywords "open  running  done
#                       blocked", each painted with its own ANSI color
#                       and reset back to default between words.
#
# A stable separator " | " divides the two halves so the legend reads as
# two rows of information on a single visual line. The renderer is free to
# expand the separator to a softer Unicode bar; the template's literal " | "
# is the minimum-compatibility default.

# ---------------------------------------------------------------------------
# DASHBOARD_LEGEND_TEMPLATE
# Single-line legend template with placeholder variables.
#
# Do not change this string without updating dashboard-column-render.sh
# (which performs the substitution) and the matching section in
# team/SOP.md "Dashboard color conventions".
# ---------------------------------------------------------------------------
DASHBOARD_LEGEND_TEMPLATE='PROJECT: {PROJECTS_BLOCK}   |   STATUS: {STATUS_BLOCK}'
readonly DASHBOARD_LEGEND_TEMPLATE

# ---------------------------------------------------------------------------
# DASHBOARD_LEGEND_STATUS_KEYWORDS
# Ordered list of status keywords to render in the STATUS half of the legend.
# Order matches the lifecycle progression open -> running -> done, with
# blocked listed last as the attention-grabbing terminal state.
# wont-do is included after done: both are terminal-resolved states (green).
# ---------------------------------------------------------------------------
DASHBOARD_LEGEND_STATUS_KEYWORDS=(open running done wont-do blocked)
readonly DASHBOARD_LEGEND_STATUS_KEYWORDS

# ---------------------------------------------------------------------------
# DASHBOARD_LEGEND_PROJECT_TAG_GLYPH
# The character used as the project color tag in the PROJECT half of the
# legend. Defaults to a filled Unicode square so the tag is visible at a
# glance; renderers in NO_COLOR or dumb-terminal mode substitute a short
# bracketed abbreviation ("[abbr]") via dashboard-column-render.sh's
# plain_tag() helper.
# ---------------------------------------------------------------------------
DASHBOARD_LEGEND_PROJECT_TAG_GLYPH='■'
readonly DASHBOARD_LEGEND_PROJECT_TAG_GLYPH
