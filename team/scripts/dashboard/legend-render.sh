#!/usr/bin/env bash
# dashboard-legend-render.sh
# Renders the persistent one-line color legend for the unified 8-column
# visibility window (unified 8-column layout).
#
# Output shape:
#   PROJECT: <tag> <name>  <tag> <name>  ...   |   STATUS: open  running  done  blocked
#
# Each project tag is rendered in its display_color (truecolor ANSI when the
# terminal supports it; "[abbr]" fallback in NO_COLOR / TERM=dumb mode). The
# status keywords are rendered in their mapped status colors so the operator can
# read the convention directly from the legend without needing external reference.
#
# Usage:
#   dashboard-legend-render.sh [--kanban-root <path>]
#
# Environment:
#   PGAI_AGENT_KANBAN_ROOT_PATH  — kanban root override
#   NO_COLOR                            — set non-empty to disable ANSI colors
#   TERM=dumb                           — also disables ANSI colors
#
# Exit 0 always.

set -euo pipefail
# shellcheck source=../lib/env_bootstrap.sh
source "$(dirname "${BASH_SOURCE[0]}")/../lib/env_bootstrap.sh"

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

# Source project_paths lib for pp_* helpers
# shellcheck source=lib/project_paths.sh
source "${SCRIPT_DIR}/../lib/project_paths.sh"

# Source projects lib for projects_cfg_* helpers
# shellcheck source=lib/projects.sh
source "${SCRIPT_DIR}/../lib/projects.sh"

# Source legend template constants
# shellcheck source=lib/dashboard_legend.sh
source "${SCRIPT_DIR}/../lib/dashboard_legend.sh"

# Source dev_tree helper (resolve_global_dev_tree / require_dev_tree)
# shellcheck source=lib/dev_tree.sh
source "${SCRIPT_DIR}/../lib/dev_tree.sh"

# ---------------------------------------------------------------------------
# Parse arguments
# ---------------------------------------------------------------------------
KANBAN_ROOT="${PGAI_AGENT_KANBAN_ROOT_PATH}"

while [[ $# -gt 0 ]]; do
    case "$1" in
        --kanban-root)
            KANBAN_ROOT="${2:-$KANBAN_ROOT}"
            shift 2
            ;;
        -h|--help)
            grep '^#' "$0" | sed 's/^# \{0,1\}//' | head -30
            exit 0
            ;;
        *)
            echo "ERROR: Unknown argument: $1" >&2
            exit 1
            ;;
    esac
done

# ---------------------------------------------------------------------------
# Source config (INI format via kanban.cfg; replaces legacy config.cfg)
# read_ini available via project_paths.sh (sourced above).
# ---------------------------------------------------------------------------
if [[ -f "${KANBAN_ROOT}/kanban.cfg" ]]; then
    export PGAI_KANBAN_PM_MODE="${PGAI_KANBAN_PM_MODE:-$(read_ini "${KANBAN_ROOT}/kanban.cfg" chain pm_mode automatic)}"
    DASHBOARD_ROWS_PER_COLUMN="${DASHBOARD_ROWS_PER_COLUMN:-$(read_ini "${KANBAN_ROOT}/kanban.cfg" dashboard rows_per_column 21)}"
    export DASHBOARD_ROWS_PER_COLUMN
fi
export PGAI_DEV_TREE_PATH="${PGAI_DEV_TREE_PATH:-$(resolve_global_dev_tree)}"

# ---------------------------------------------------------------------------
# ANSI color support
# ---------------------------------------------------------------------------
USE_COLOR=true
if [[ "${TERM:-}" == "dumb" ]] || [[ -n "${NO_COLOR:-}" ]]; then
    USE_COLOR=false
fi

# ---------------------------------------------------------------------------
# Build the legend line using a Python helper for consistent ANSI handling.
# Python handles truecolor hex-to-rgb conversion and text assembly.
# ---------------------------------------------------------------------------

# Collect project names and their display colors via projects_cfg helpers.
# Serialize to newline-delimited env vars for the Python subprocess.
PROJ_NAMES=()
PROJ_COLORS=()

while IFS= read -r _proj; do
    [[ -z "$_proj" ]] && continue
    PROJ_NAMES+=("$_proj")
    _color="$(KANBAN_ROOT="$KANBAN_ROOT" projects_cfg_color "$_proj" 2>/dev/null || echo "")"
    [[ -z "$_color" ]] && _color="#888780"
    PROJ_COLORS+=("$_color")
done < <(KANBAN_ROOT="$KANBAN_ROOT" projects_cfg_list 2>/dev/null || true)

export _LEGEND_PROJ_NAMES_STR
export _LEGEND_PROJ_COLORS_STR
export _LEGEND_TEMPLATE
export _LEGEND_STATUS_KEYWORDS_STR
_LEGEND_PROJ_NAMES_STR="$(printf '%s\n' "${PROJ_NAMES[@]+"${PROJ_NAMES[@]}"}")"
_LEGEND_PROJ_COLORS_STR="$(printf '%s\n' "${PROJ_COLORS[@]+"${PROJ_COLORS[@]}"}")"

# Pass the DASHBOARD_LEGEND_TEMPLATE and DASHBOARD_LEGEND_STATUS_KEYWORDS
# constants (sourced from lib/dashboard_legend.sh above) to the Python
# subprocess so the assembled legend always matches the canonical template.
_LEGEND_TEMPLATE="${DASHBOARD_LEGEND_TEMPLATE}"
_LEGEND_STATUS_KEYWORDS_STR="${DASHBOARD_LEGEND_STATUS_KEYWORDS[*]}"

python3 - "$USE_COLOR" <<'PYEOF'
import os, re, sys

use_color = sys.argv[1].lower() == "true"

# ---------------------------------------------------------------------------
# ANSI helpers
# ---------------------------------------------------------------------------
def truecolor(hex_color):
    """Return a truecolor ANSI escape for the given #RRGGBB hex color."""
    if not use_color:
        return ""
    h = hex_color.lstrip("#")
    if len(h) != 6:
        return ""
    try:
        r, g, b = int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16)
    except ValueError:
        return ""
    return f"\033[38;2;{r};{g};{b}m"

RESET = "\033[0m" if use_color else ""

# Status-to-ANSI color mapping (mirrors dashboard-column-render.sh).
# Using standard ANSI colors as the closest terminal approximation to the
# specified hex values (#BA7517, #639922, #E24B4A).
#
# "done" and "wont-do" are both terminal-resolved states — green means
# "settled, no action required". They share the green color arm.
def status_color(s):
    if not use_color:
        return ""
    if s == "running":
        return "\033[0;33m"   # yellow / amber
    if s in ("done", "wont-do"):
        # Both are terminal-resolved states — green means "settled, no action needed"
        return "\033[0;32m"   # green
    if s == "blocked":
        return "\033[0;31m"   # red
    return "\033[0;37m"       # white / default (open)

# ---------------------------------------------------------------------------
# Read project data and template constants from environment
# ---------------------------------------------------------------------------
def env_lines(varname):
    raw = os.environ.get(varname, "")
    return [ln for ln in raw.splitlines() if ln]

proj_names  = env_lines("_LEGEND_PROJ_NAMES_STR")
proj_colors = env_lines("_LEGEND_PROJ_COLORS_STR")

# DASHBOARD_LEGEND_TEMPLATE — sourced from lib/dashboard_legend.sh via bash.
# Fall back to the canonical format if for any reason the env var is empty.
legend_template = os.environ.get("_LEGEND_TEMPLATE", "").strip()
if not legend_template:
    legend_template = "PROJECT: {PROJECTS_BLOCK}   |   STATUS: {STATUS_BLOCK}"

# DASHBOARD_LEGEND_STATUS_KEYWORDS — space-separated list from bash array.
# Fall back to the canonical four keywords when the env var is empty.
raw_keywords = os.environ.get("_LEGEND_STATUS_KEYWORDS_STR", "").strip()
status_keywords = raw_keywords.split() if raw_keywords else ["open", "running", "done", "blocked"]

# Pad colors list to match names length
while len(proj_colors) < len(proj_names):
    proj_colors.append("#888780")

# ---------------------------------------------------------------------------
# Build the PROJECT block
# ---------------------------------------------------------------------------
tag_glyph = "■"  # ■  (filled square)

project_parts = []
for name, color in zip(proj_names, proj_colors):
    if use_color:
        tag = f"{truecolor(color)}{tag_glyph}{RESET}"
    else:
        short = name[:4] if name else "????"
        tag = f"[{short}]"
    project_parts.append(f"{tag} {name}")

projects_block = "  ".join(project_parts) if project_parts else "(no projects)"

# ---------------------------------------------------------------------------
# Build the STATUS block
# ---------------------------------------------------------------------------
status_parts = []
for kw in status_keywords:
    if use_color:
        sc = status_color(kw)
        status_parts.append(f"{sc}{kw}{RESET}")
    else:
        status_parts.append(kw)

status_block = "  ".join(status_parts)

# ---------------------------------------------------------------------------
# Assemble final legend line by substituting {PROJECTS_BLOCK} and
# {STATUS_BLOCK} into DASHBOARD_LEGEND_TEMPLATE.
# ---------------------------------------------------------------------------
legend = legend_template.replace("{PROJECTS_BLOCK}", projects_block).replace(
    "{STATUS_BLOCK}", status_block
)
print(legend)
PYEOF
