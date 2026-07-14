#!/usr/bin/env bash
# dashboard-column-render.sh
# Per-column rendering logic for the unified visibility window.
#
# Two calling forms:
#
#   INPUT COLUMN (discovery pipeline — bugs/, priority/, requirements/):
#     dashboard-column-render.sh input <dir> <cache_file> [rows] [width] [--label LABEL] [--kanban-root <path>]
#
#   AGENT QUEUE COLUMN:
#     dashboard-column-render.sh queue <backlog_file> [rows] [width] [--label LABEL] [--kanban-root <path>]
#
# Multi-project mode (--all-projects flag):
#   When --all-projects is provided, the <dir>/<backlog_file> positional
#   argument is IGNORED.  Instead the script reads projects.cfg to discover all
#   registered projects and renders a mixed-project view.  Each row is prefixed
#   with a colored project tag derived from the project's display_color in
#   projects.cfg.  Entry text is colored by the entry's ## Status field.
#
#   INPUT COLUMN (all projects):
#     dashboard-column-render.sh input any_placeholder any_placeholder [rows] [width] \
#       --label LABEL --kanban-root <path> --all-projects
#
#   AGENT QUEUE COLUMN (all projects):
#     dashboard-column-render.sh queue any_placeholder [rows] [width] \
#       --label LABEL --kanban-root <path> --all-projects
#
#   Subcommand-specific dir/file placeholders are required for positional
#   argument accounting but their values are not used when --all-projects is set.
#   Callers may pass the string "none" for placeholders.
#
# Arguments:
#   <dir>           Absolute path to bugs/, priority/, or requirements/
#   <cache_file>    Absolute path to bug_backlog.md, priority_backlog.md, or pm_backlog.md
#   <backlog_file>  Absolute path to coder_backlog.md (or pm_, writer_, etc.)
#   rows            Max rows to display (default: $DASHBOARD_ROWS_PER_COLUMN or 7)
#   width           Column width in characters (default: 38)
#
# Options:
#   --label NAME      Pane label for the header line (e.g. BUGS, CODER, REQUIREMENTS).
#                     Emits '=== NAME ===' as the first output line.
#   --kanban-root     Override the kanban root path.
#   --all-projects    Render mixed view from all registered projects (see above).
#   --column-type T   "bugs"|"priorities"|"requirements"|"pm"|"coder"|"writer"|"tester"|"cm"
#                     Required when --all-projects is set; identifies which data to gather
#                     per project.  Falls back to auto-detection from PANE_LABEL when absent.
#
# Output:
#   First line: '=== LABEL ===' (when --label is provided)
#   Then one item per line, ANSI-colored where applicable, truncated to <width>
#   characters (measured in visible characters — ANSI escape codes do not count
#   against width).  Emits '(empty)' when there are no items.
#
#   In multi-project mode each row is prefixed with a project color tag:
#     "■ " in the project's display_color (truecolor ANSI), or
#     "[<short>] " in plain text when NO_COLOR/TERM=dumb.
#
# Environment:
#   PGAI_AGENT_KANBAN_ROOT_PATH  — kanban root (for config.cfg and project paths)
#   DASHBOARD_ROWS_PER_COLUMN           — global rows override (read from config.cfg)
#   NO_COLOR                            — set non-empty to disable ANSI colors
#   TERM=dumb                           — also disables ANSI colors

set -euo pipefail
# shellcheck source=../lib/env_bootstrap.sh
source "$(dirname "${BASH_SOURCE[0]}")/../lib/env_bootstrap.sh"

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# TEAM_DIR: the 'team/' directory two levels above this script's location
# (team/scripts/dashboard/column-render.sh → team/scripts/dashboard → team/scripts → team)
TEAM_DIR="$(cd "${SCRIPT_DIR}/../.." && pwd)"

# Source project_paths lib
# shellcheck source=lib/project_paths.sh
source "${SCRIPT_DIR}/../lib/project_paths.sh"

# Source projects lib for projects_cfg_* helpers (multi-project registry).
# Required for --all-projects mode.  Sourced unconditionally so callers
# do not need to guard; the helpers are no-ops when projects.cfg is absent.
# shellcheck source=lib/projects.sh
source "${SCRIPT_DIR}/../lib/projects.sh"

# Source INI parser for reading dashboard layout values from kanban.cfg.
# shellcheck source=lib/ini_parser.sh
source "${SCRIPT_DIR}/../lib/ini_parser.sh"

# Source dev_tree helper (resolve_global_dev_tree / require_dev_tree)
# shellcheck source=lib/dev_tree.sh
source "${SCRIPT_DIR}/../lib/dev_tree.sh"

# ---------------------------------------------------------------------------
# Parse arguments
# ---------------------------------------------------------------------------
SUBCOMMAND=""          # "input" or "queue"
KANBAN_ROOT="${PGAI_AGENT_KANBAN_ROOT_PATH}"
PANE_LABEL=""          # optional label for header line
ALL_PROJECTS=false     # --all-projects flag: render mixed view from all projects
COLUMN_TYPE=""         # --column-type: e.g. "bugs", "priorities", "coder", etc.

# Positional args (subcommand-specific)
POSARGS=()

_args=("$@")
_i=0
while [[ $_i -lt ${#_args[@]} ]]; do
    arg="${_args[$_i]}"
    case "$arg" in
        -h|--help)
            grep '^#' "$0" | sed 's/^# \{0,1\}//' | head -80
            exit 0
            ;;
        --kanban-root)
            _next=$(( _i + 1 ))
            KANBAN_ROOT="${_args[$_next]:-$KANBAN_ROOT}"
            _i=$_next
            ;;
        --label)
            _next=$(( _i + 1 ))
            PANE_LABEL="${_args[$_next]:-}"
            _i=$_next
            ;;
        --all-projects)
            ALL_PROJECTS=true
            ;;
        --column-type)
            _next=$(( _i + 1 ))
            COLUMN_TYPE="${_args[$_next]:-}"
            _i=$_next
            ;;
        -*)
            echo "ERROR: Unknown option: $arg" >&2
            exit 1
            ;;
        *)
            POSARGS+=("$arg")
            ;;
    esac
    _i=$(( _i + 1 ))
done

if [[ ${#POSARGS[@]} -lt 1 ]]; then
    echo "ERROR: subcommand required: 'input' or 'queue'" >&2
    exit 1
fi

SUBCOMMAND="${POSARGS[0]}"

if [[ "$SUBCOMMAND" != "input" && "$SUBCOMMAND" != "queue" ]]; then
    echo "ERROR: Unknown subcommand '${SUBCOMMAND}'. Use 'input' or 'queue'." >&2
    exit 1
fi

# Auto-detect COLUMN_TYPE from PANE_LABEL when not explicitly set and
# --all-projects is active.  This avoids requiring callers to supply both
# --label and --column-type.
if [[ "$ALL_PROJECTS" == "true" && -z "$COLUMN_TYPE" && -n "$PANE_LABEL" ]]; then
    case "${PANE_LABEL,,}" in
        bugs)         COLUMN_TYPE="bugs" ;;
        priorities)   COLUMN_TYPE="priorities" ;;
        requirements) COLUMN_TYPE="requirements" ;;
        pm)           COLUMN_TYPE="pm" ;;
        coder)        COLUMN_TYPE="coder" ;;
        writer)       COLUMN_TYPE="writer" ;;
        tester)       COLUMN_TYPE="tester" ;;
        cm)           COLUMN_TYPE="cm" ;;
        *)            COLUMN_TYPE="" ;;
    esac
fi

# ---------------------------------------------------------------------------
# Source config — INI format (kanban.cfg) replaces legacy config.cfg
# read_ini available via project_paths.sh (sourced above).
# ---------------------------------------------------------------------------
if [[ -f "${KANBAN_ROOT}/kanban.cfg" ]]; then
    export PGAI_KANBAN_PM_MODE="${PGAI_KANBAN_PM_MODE:-$(read_ini "${KANBAN_ROOT}/kanban.cfg" chain pm_mode automatic)}"
    DASHBOARD_ROWS_PER_COLUMN="${DASHBOARD_ROWS_PER_COLUMN:-$(read_ini "${KANBAN_ROOT}/kanban.cfg" dashboard rows_per_column 21)}"
    export DASHBOARD_ROWS_PER_COLUMN
    # [dashboard] max_rows → DASHBOARD_MAX_ROWS
    # Precedence: per-project projects.cfg > env > kanban.cfg > 20.
    # An operator-exported DASHBOARD_MAX_ROWS still wins (env-over-cfg).
    DASHBOARD_MAX_ROWS="${DASHBOARD_MAX_ROWS:-$(read_ini "${KANBAN_ROOT}/kanban.cfg" dashboard max_rows 20)}"
    export DASHBOARD_MAX_ROWS
fi
export PGAI_DEV_TREE_PATH="${PGAI_DEV_TREE_PATH:-$(resolve_global_dev_tree)}"

# ---------------------------------------------------------------------------
# DASHBOARD_ROWS_PER_COLUMN — validate and apply default
# ---------------------------------------------------------------------------
_DEFAULT_ROWS=13
if [[ -z "${DASHBOARD_ROWS_PER_COLUMN:-}" ]]; then
    DASHBOARD_ROWS_PER_COLUMN="$_DEFAULT_ROWS"
elif ! [[ "${DASHBOARD_ROWS_PER_COLUMN}" =~ ^[0-9]+$ ]] || (( DASHBOARD_ROWS_PER_COLUMN <= 0 )); then
    echo "WARNING: DASHBOARD_ROWS_PER_COLUMN='${DASHBOARD_ROWS_PER_COLUMN}' is invalid; falling back to ${_DEFAULT_ROWS}" >&2
    DASHBOARD_ROWS_PER_COLUMN="$_DEFAULT_ROWS"
fi

# ---------------------------------------------------------------------------
# Dashboard layout values — read from kanban.cfg [dashboard] section.
# These four values control the dynamic per-project row allocation algorithm.
# Defaults match the canonical values from kanban.cfg.example.
# ---------------------------------------------------------------------------
_KANBAN_CFG="${KANBAN_ROOT}/kanban.cfg"
DASHBOARD_MIN_ROWS_PER_COLUMN="$(read_ini "$_KANBAN_CFG" dashboard min_rows_per_column 13)"
DASHBOARD_MAX_ROWS_PER_COLUMN="$(read_ini "$_KANBAN_CFG" dashboard max_rows_per_column 34)"
DASHBOARD_MIN_ROWS_PER_PROJECT="$(read_ini "$_KANBAN_CFG" dashboard min_rows_per_project 3)"
DASHBOARD_MAX_ROWS_PER_PROJECT="$(read_ini "$_KANBAN_CFG" dashboard max_rows_per_project 8)"

# Validate: fall back to defaults if values are not positive integers.
[[ "${DASHBOARD_MIN_ROWS_PER_COLUMN}" =~ ^[0-9]+$ ]] && (( DASHBOARD_MIN_ROWS_PER_COLUMN > 0 )) || DASHBOARD_MIN_ROWS_PER_COLUMN=13
[[ "${DASHBOARD_MAX_ROWS_PER_COLUMN}" =~ ^[0-9]+$ ]] && (( DASHBOARD_MAX_ROWS_PER_COLUMN > 0 )) || DASHBOARD_MAX_ROWS_PER_COLUMN=34
[[ "${DASHBOARD_MIN_ROWS_PER_PROJECT}" =~ ^[0-9]+$ ]] && (( DASHBOARD_MIN_ROWS_PER_PROJECT > 0 )) || DASHBOARD_MIN_ROWS_PER_PROJECT=3
[[ "${DASHBOARD_MAX_ROWS_PER_PROJECT}" =~ ^[0-9]+$ ]] && (( DASHBOARD_MAX_ROWS_PER_PROJECT > 0 )) || DASHBOARD_MAX_ROWS_PER_PROJECT=8

# ---------------------------------------------------------------------------
# ANSI color support
# ---------------------------------------------------------------------------
USE_COLOR=true
if [[ "${TERM:-}" == "dumb" ]] || [[ -n "${NO_COLOR:-}" ]]; then
    USE_COLOR=false
fi

_ansi() {
    local color="$1"
    if [[ "$USE_COLOR" != "true" ]]; then echo ""; return; fi
    case "$color" in
        red)     echo $'\033[0;31m' ;;
        green)   echo $'\033[0;32m' ;;
        yellow)  echo $'\033[0;33m' ;;
        white)   echo $'\033[0;37m' ;;
        cyan)    echo $'\033[0;36m' ;;
        dim)     echo $'\033[2m' ;;
        reset)   echo $'\033[0m' ;;
        *)       echo "" ;;
    esac
}

C_RED="$(_ansi red)"
C_YELLOW="$(_ansi yellow)"
C_GREEN="$(_ansi green)"
C_WHITE="$(_ansi white)"
C_DIM="$(_ansi dim)"
RESET="$(_ansi reset)"

# The per-column header is deliberately neutral: per-project halt scope is
# surfaced by show-header / show-multi / status-bottom / status-right /
# attention indicators, not by the column header itself.  Any future
# per-column halt tint must derive the owning project from the column's
# input directory (post-v0.1003.0 explicit-resolution machinery), never
# from registry order.
HALT_HEADER_COLOR=""
HALT_HEADER_MARKER=""

# ---------------------------------------------------------------------------
# Emit pane label header if provided
# ---------------------------------------------------------------------------
if [[ -n "$PANE_LABEL" ]]; then
    if [[ -n "$HALT_HEADER_COLOR" ]]; then
        echo "${HALT_HEADER_COLOR}=== ${PANE_LABEL} ===${RESET}"
    else
        echo "=== ${PANE_LABEL} ===${HALT_HEADER_MARKER}"
    fi
fi

# ---------------------------------------------------------------------------
# Multi-project mode dispatch
#
# When --all-projects is set, gather data from every project registered in
# projects.cfg.  Each row is prefixed with a colored project tag derived from
# the project's display_color field.  Entry text color reflects the entry's
# ## Status field (open=white, running=amber, done=green, blocked=red).
#
# A 6-digit truecolor ANSI sequence is used for the project tag when USE_COLOR
# is true.  In NO_COLOR / TERM=dumb mode the tag falls back to "[<short>] "
# where <short> is the first 4 chars of the project name.
# ---------------------------------------------------------------------------
if [[ "$ALL_PROJECTS" == "true" ]]; then
    # Determine positional width/rows from POSARGS (same slots as single-project).
    # input: <subcommand> <dir> <cache> [rows] [width]
    # queue: <subcommand> <backlog> [rows] [width]
    if [[ "$SUBCOMMAND" == "input" ]]; then
        ROWS="${POSARGS[3]:-$DASHBOARD_ROWS_PER_COLUMN}"
        WIDTH="${POSARGS[4]:-38}"
    else
        ROWS="${POSARGS[2]:-$DASHBOARD_ROWS_PER_COLUMN}"
        WIDTH="${POSARGS[3]:-22}"
    fi

    if ! [[ "$ROWS" =~ ^[0-9]+$ ]] || (( ROWS <= 0 )); then
        ROWS="$DASHBOARD_ROWS_PER_COLUMN"
    fi
    if ! [[ "$WIDTH" =~ ^[0-9]+$ ]] || (( WIDTH <= 0 )); then
        WIDTH=38
    fi

    # Collect project list and their colors from projects.cfg.
    # Build two parallel arrays: PROJ_NAMES and PROJ_COLORS.
    # Projects are read in priority order (as returned by projects_cfg_list).
    PROJ_NAMES=()
    PROJ_COLORS=()
    PROJ_MAX_ROWS=()
    _cfg_path="$(projects_cfg_path)"
    while IFS= read -r _proj; do
        [[ -z "$_proj" ]] && continue
        PROJ_NAMES+=("$_proj")
        # projects_cfg_color requires KANBAN_ROOT to be set (it is).
        _color="$(projects_cfg_color "$_proj" 2>/dev/null || echo "")"
        [[ -z "$_color" ]] && _color="#888780"
        PROJ_COLORS+=("$_color")
        # per-project max_rows: precedence is per-project cfg > DASHBOARD_MAX_ROWS > 20
        _max_rows="$(KANBAN_ROOT="$KANBAN_ROOT" projects_cfg_max_rows "$_proj" 2>/dev/null || echo "${DASHBOARD_MAX_ROWS:-20}")"
        [[ -z "$_max_rows" ]] && _max_rows="${DASHBOARD_MAX_ROWS:-20}"
        PROJ_MAX_ROWS+=("$_max_rows")
    done < <(KANBAN_ROOT="$KANBAN_ROOT" projects_cfg_list 2>/dev/null || true)

    if [[ ${#PROJ_NAMES[@]} -eq 0 ]]; then
        echo "(empty)"
        exit 0
    fi

    # Build per-project directory/file lists for the chosen column type.
    # Also build the corresponding release-state info for requirements columns.
    PROJ_INPUT_DIRS=()
    PROJ_CACHE_FILES=()
    PROJ_BACKLOG_FILES=()
    PROJ_RELEASE_STATES=()
    PROJ_TASKS_DIRS=()

    for _p in "${PROJ_NAMES[@]}"; do
        _p_root="$(KANBAN_ROOT="$KANBAN_ROOT" pp_project_root "$_p" 2>/dev/null || true)"
        _p_tasks="$(KANBAN_ROOT="$KANBAN_ROOT" pp_tasks_dir "$_p" 2>/dev/null || true)"
        # Queue file paths are resolved through pp_queue_path (canonical flat
        # layout: queues/<agent>_backlog.md).  Do NOT construct these paths
        # inline; pp_queue_path is the single source of truth for queue file
        # locations.
        PROJ_TASKS_DIRS+=("${_p_tasks}")
        case "${COLUMN_TYPE}" in
            bugs)
                _dir="$(KANBAN_ROOT="$KANBAN_ROOT" pp_bugs_dir "$_p" 2>/dev/null || true)"
                _cache="$(KANBAN_ROOT="$KANBAN_ROOT" pp_queue_path "$_p" "bug" 2>/dev/null || true)"
                PROJ_INPUT_DIRS+=("${_dir}")
                PROJ_CACHE_FILES+=("${_cache}")
                PROJ_BACKLOG_FILES+=("none")
                PROJ_RELEASE_STATES+=("none")
                ;;
            priorities)
                _dir="$(KANBAN_ROOT="$KANBAN_ROOT" pp_priority_dir "$_p" 2>/dev/null || true)"
                _cache="$(KANBAN_ROOT="$KANBAN_ROOT" pp_queue_path "$_p" "priority" 2>/dev/null || true)"
                PROJ_INPUT_DIRS+=("${_dir}")
                PROJ_CACHE_FILES+=("${_cache}")
                PROJ_BACKLOG_FILES+=("none")
                PROJ_RELEASE_STATES+=("none")
                ;;
            requirements)
                _dir="$(KANBAN_ROOT="$KANBAN_ROOT" pp_requirements_dir "$_p" 2>/dev/null || true)"
                _cache="$(KANBAN_ROOT="$KANBAN_ROOT" pp_queue_path "$_p" "pm" 2>/dev/null || true)"
                _rs="$(KANBAN_ROOT="$KANBAN_ROOT" pp_release_state "$_p" 2>/dev/null || true)"
                PROJ_INPUT_DIRS+=("${_dir}")
                PROJ_CACHE_FILES+=("${_cache}")
                PROJ_BACKLOG_FILES+=("none")
                PROJ_RELEASE_STATES+=("${_rs}")
                ;;
            pm)
                PROJ_INPUT_DIRS+=("none")
                PROJ_CACHE_FILES+=("none")
                PROJ_BACKLOG_FILES+=("$(KANBAN_ROOT="$KANBAN_ROOT" pp_queue_path "$_p" "pm" 2>/dev/null || true)")
                PROJ_RELEASE_STATES+=("none")
                ;;
            coder)
                PROJ_INPUT_DIRS+=("none")
                PROJ_CACHE_FILES+=("none")
                PROJ_BACKLOG_FILES+=("$(KANBAN_ROOT="$KANBAN_ROOT" pp_queue_path "$_p" "coder" 2>/dev/null || true)")
                PROJ_RELEASE_STATES+=("none")
                ;;
            writer)
                PROJ_INPUT_DIRS+=("none")
                PROJ_CACHE_FILES+=("none")
                PROJ_BACKLOG_FILES+=("$(KANBAN_ROOT="$KANBAN_ROOT" pp_queue_path "$_p" "writer" 2>/dev/null || true)")
                PROJ_RELEASE_STATES+=("none")
                ;;
            tester)
                PROJ_INPUT_DIRS+=("none")
                PROJ_CACHE_FILES+=("none")
                PROJ_BACKLOG_FILES+=("$(KANBAN_ROOT="$KANBAN_ROOT" pp_queue_path "$_p" "tester" 2>/dev/null || true)")
                PROJ_RELEASE_STATES+=("none")
                ;;
            cm)
                PROJ_INPUT_DIRS+=("none")
                PROJ_CACHE_FILES+=("none")
                PROJ_BACKLOG_FILES+=("$(KANBAN_ROOT="$KANBAN_ROOT" pp_queue_path "$_p" "cm" 2>/dev/null || true)")
                PROJ_RELEASE_STATES+=("none")
                ;;
            *)
                # Unknown column type — fall back to empty for this project
                PROJ_INPUT_DIRS+=("none")
                PROJ_CACHE_FILES+=("none")
                PROJ_BACKLOG_FILES+=("none")
                PROJ_RELEASE_STATES+=("none")
                ;;
        esac
    done

    # Serialize arrays to newline-delimited strings for Python.
    # Export so the heredoc Python subprocess can read them via os.environ.
    export _PROJ_NAMES_STR
    export _PROJ_COLORS_STR
    export _PROJ_MAX_ROWS_STR
    export _PROJ_INPUT_DIRS_STR
    export _PROJ_CACHE_FILES_STR
    export _PROJ_BACKLOG_FILES_STR
    export _PROJ_RELEASE_STATES_STR
    export _PROJ_TASKS_DIRS_STR
    _PROJ_NAMES_STR="$(printf '%s\n' "${PROJ_NAMES[@]}")"
    _PROJ_COLORS_STR="$(printf '%s\n' "${PROJ_COLORS[@]}")"
    _PROJ_MAX_ROWS_STR="$(printf '%s\n' "${PROJ_MAX_ROWS[@]}")"
    _PROJ_INPUT_DIRS_STR="$(printf '%s\n' "${PROJ_INPUT_DIRS[@]}")"
    _PROJ_CACHE_FILES_STR="$(printf '%s\n' "${PROJ_CACHE_FILES[@]}")"
    _PROJ_BACKLOG_FILES_STR="$(printf '%s\n' "${PROJ_BACKLOG_FILES[@]}")"
    _PROJ_RELEASE_STATES_STR="$(printf '%s\n' "${PROJ_RELEASE_STATES[@]}")"
    _PROJ_TASKS_DIRS_STR="$(printf '%s\n' "${PROJ_TASKS_DIRS[@]}")"

    # Resolve per-project RC info for requirements columns.
    # For non-requirements columns all entries will be "none" — Python ignores them.
    # Export so Python subprocess can read them via os.environ.
    export _ALL_ACTIVE_RCS
    export _ALL_LAST_RELEASED
    _ALL_ACTIVE_RCS=""
    _ALL_LAST_RELEASED=""
    for _p in "${PROJ_NAMES[@]}"; do
        if [[ "${COLUMN_TYPE}" == "requirements" ]]; then
            _p_rs="$(KANBAN_ROOT="$KANBAN_ROOT" pp_release_state "$_p" 2>/dev/null || true)"
            _p_arc="none"
            if [[ -f "$_p_rs" ]]; then
                # Liberal parse — read first header only, trim whitespace,
                # then validate: accept only vX.Y.Z semver or 'none'; anything else → 'none'.
                _p_arc="$(awk '/^## Active RC/{found=1;next} found && /^[[:space:]]*$/{next} found{print; exit}' "$_p_rs" 2>/dev/null | tr -d '[:space:]')" || _p_arc="none"
                if [[ "$_p_arc" == "none" ]] || [[ -z "$_p_arc" ]]; then
                    _p_arc="none"
                elif ! [[ "$_p_arc" =~ ^v[0-9]+\.[0-9]+\.[0-9]+$ ]]; then
                    _p_arc="none"  # malformed / partial / unexpected — treat as none (no [active])
                fi
            fi
            _ALL_ACTIVE_RCS="${_ALL_ACTIVE_RCS}${_p_arc}"$'\n'
            _p_lr="$(KANBAN_ROOT="$KANBAN_ROOT" pp_last_released_version "$_p" 2>/dev/null || echo "none")"
            [[ "$_p_lr" == "v0.0.0" ]] && _p_lr="none"
            _ALL_LAST_RELEASED="${_ALL_LAST_RELEASED}${_p_lr}"$'\n'
        else
            _ALL_ACTIVE_RCS="${_ALL_ACTIVE_RCS}none"$'\n'
            _ALL_LAST_RELEASED="${_ALL_LAST_RELEASED}none"$'\n'
        fi
    done

    python3 - \
        "$ROWS" \
        "$WIDTH" \
        "$SUBCOMMAND" \
        "$COLUMN_TYPE" \
        "$USE_COLOR" \
        "${C_RED}" "${C_YELLOW}" "${C_GREEN}" "${C_WHITE}" "${C_DIM}" "${RESET}" \
        --min-rows-per-column "${DASHBOARD_MIN_ROWS_PER_COLUMN}" \
        --max-rows-per-column "${DASHBOARD_MAX_ROWS_PER_COLUMN}" \
        --min-rows-per-project "${DASHBOARD_MIN_ROWS_PER_PROJECT}" \
        --max-rows-per-project "${DASHBOARD_MAX_ROWS_PER_PROJECT}" \
    <<PYEOF
import argparse, os, re, sys, pathlib, textwrap

# ---------------------------------------------------------------------------
# Resolve the pgai_agent_kanban package from the live-install anchor only.
# The live kanban root ($PGAI_AGENT_KANBAN_ROOT_PATH) is the single sys.path
# candidate — the dev tree is DATA to the live runtime, never CODE.  A missing
# or broken live package raises ImportError immediately with the live path in
# the traceback, making deployment gaps visible rather than silently masking
# them via a dev-tree fallback.
# ---------------------------------------------------------------------------
_kanban_root = os.environ.get("PGAI_AGENT_KANBAN_ROOT_PATH", "")
if _kanban_root and _kanban_root not in sys.path:
    sys.path.insert(0, _kanban_root)

from pgai_agent_kanban.dashboard.status_priority_cap import status_priority_cap as _status_priority_cap  # noqa: E402

# Parse dashboard layout configuration injected as CLI args by the bash wrapper.
# These values come from kanban.cfg [dashboard] section via read_ini.
# parse_known_args allows coexistence with the existing positional sys.argv usage.
_layout_parser = argparse.ArgumentParser(add_help=False)
_layout_parser.add_argument('--min-rows-per-column', type=int, default=13,
                             dest='min_rows_per_column')
_layout_parser.add_argument('--max-rows-per-column', type=int, default=34,
                             dest='max_rows_per_column')
_layout_parser.add_argument('--min-rows-per-project', type=int, default=3,
                             dest='min_rows_per_project')
_layout_parser.add_argument('--max-rows-per-project', type=int, default=8,
                             dest='max_rows_per_project')
_layout_args, _ = _layout_parser.parse_known_args()

# compute_layout: dynamic per-project row allocation algorithm.
# Returns (effective_per_project, total_rows_to_render).
def compute_layout(projects_count):
    min_col  = _layout_args.min_rows_per_column
    max_col  = _layout_args.max_rows_per_column
    min_proj = _layout_args.min_rows_per_project
    max_proj = _layout_args.max_rows_per_project

    effective = max_proj
    while effective > min_proj and projects_count * effective > max_col:
        effective -= 1

    if projects_count * effective > max_col:
        # Even minimum overflows; accept it
        effective = min_proj

    total = max(projects_count * effective, min_col)
    total = min(total, max_col)
    return effective, total

width        = int(sys.argv[2])
subcommand   = sys.argv[3]          # "input" or "queue"
column_type  = sys.argv[4]          # "bugs", "priorities", "requirements", "pm", "coder", ...
use_color    = sys.argv[5].lower() == "true"
C_RED, C_YELLOW, C_GREEN, C_WHITE, C_DIM, RESET = sys.argv[6:12]

# ---------------------------------------------------------------------------
# Read parallel project arrays from the environment (set by bash above).
# ---------------------------------------------------------------------------
def _env_lines(varname):
    """Return non-empty lines from an env var (newline-delimited list)."""
    raw = os.environ.get(varname, "")
    return [ln for ln in raw.splitlines() if ln]

proj_names         = _env_lines("_PROJ_NAMES_STR")
proj_colors        = _env_lines("_PROJ_COLORS_STR")
proj_max_rows_raw  = _env_lines("_PROJ_MAX_ROWS_STR")
proj_input_dirs    = _env_lines("_PROJ_INPUT_DIRS_STR")
proj_cache_files   = _env_lines("_PROJ_CACHE_FILES_STR")
proj_backlog_files = _env_lines("_PROJ_BACKLOG_FILES_STR")
proj_release_states = _env_lines("_PROJ_RELEASE_STATES_STR")
proj_tasks_dirs    = _env_lines("_PROJ_TASKS_DIRS_STR")
all_active_rcs     = _env_lines("_ALL_ACTIVE_RCS")
all_last_released  = _env_lines("_ALL_LAST_RELEASED")

n_projects = len(proj_names)

# Compute dynamic layout using the injected config values.
# effective_per_project: rows allocated per project (the minimum allocation floor).
# rows: total display rows for this column (replaces the legacy DASHBOARD_ROWS_PER_COLUMN).
if n_projects > 0:
    effective_per_project, rows = compute_layout(n_projects)
else:
    effective_per_project = _layout_args.min_rows_per_project
    rows = _layout_args.min_rows_per_column

# Pad short lists with "none" so index access is safe.
def _pad(lst, n, default="none"):
    return lst + [default] * (n - len(lst))

proj_colors         = _pad(proj_colors,         n_projects, "#888780")
proj_max_rows_raw   = _pad(proj_max_rows_raw,   n_projects, "20")
proj_input_dirs     = _pad(proj_input_dirs,     n_projects)
proj_cache_files    = _pad(proj_cache_files,    n_projects)
proj_backlog_files  = _pad(proj_backlog_files,  n_projects)
proj_release_states = _pad(proj_release_states, n_projects)
proj_tasks_dirs     = _pad(proj_tasks_dirs,     n_projects)
all_active_rcs      = _pad(all_active_rcs,      n_projects, "")
all_last_released   = _pad(all_last_released,   n_projects, "")

# Parse per-project max_rows values (fall back to 20 on bad values)
def _parse_max_rows(s, default=20):
    try:
        v = int(s)
        return v if v >= 1 else default
    except (ValueError, TypeError):
        return default

proj_max_rows = [_parse_max_rows(r) for r in proj_max_rows_raw]

# ---------------------------------------------------------------------------
# ANSI helpers
# ---------------------------------------------------------------------------
ANSI_ESC_RE = re.compile(r'\033\[[0-9;]*m')

def visible_len(s):
    return len(ANSI_ESC_RE.sub("", s))

def truncate_ansi(text, max_width):
    """Truncate to max_width visible chars, preserving ANSI codes."""
    if visible_len(text) <= max_width:
        return text
    result = []
    vlen = 0
    i = 0
    target = max_width - 1
    while i < len(text):
        m = ANSI_ESC_RE.match(text, i)
        if m:
            result.append(m.group(0))
            i = m.end()
            continue
        ch = text[i]
        if vlen >= target:
            break
        result.append(ch)
        vlen += 1
        i += 1
    if use_color and any(ANSI_ESC_RE.search(s) for s in result):
        result.append("\033[0m")
    result.append("…")
    return "".join(result)

def hex_to_rgb(hex_color):
    """Parse #RRGGBB to (r, g, b) tuple, return None on failure."""
    h = hex_color.lstrip("#")
    if len(h) != 6:
        return None
    try:
        return int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16)
    except ValueError:
        return None

def project_tag_color(hex_color):
    """
    Return an ANSI truecolor escape to set foreground to hex_color.
    Returns empty string when use_color is False.
    """
    if not use_color:
        return ""
    rgb = hex_to_rgb(hex_color)
    if rgb is None:
        return ""
    r, g, b = rgb
    return f"\033[38;2;{r};{g};{b}m"

# ---------------------------------------------------------------------------
# Status-to-color mapping for entry text.
# Status colors per spec:
#   open        → default (C_WHITE)
#   running     → amber   (C_YELLOW — closest ANSI approximation to #BA7517)
#   done        → green   (C_GREEN  — closest ANSI approximation to #639922)
#   wont-do     → green   (terminal-resolved state, same category as done)
#   blocked     → red     (C_RED    — closest ANSI approximation to #E24B4A)
#
# "done" and "wont-do" are both terminal-resolved states: the item is settled
# and needs no further attention. They share the green color arm so operators
# can distinguish "still pending" from "settled, no action required" at a glance.
# ---------------------------------------------------------------------------
def status_to_color(status):
    s = (status or "").strip().lower()
    if s == "running":
        return C_YELLOW
    if s in ("done", "wont-do"):
        # Both are terminal-resolved states — green means "settled, no action needed"
        return C_GREEN
    if s == "blocked":
        return C_RED
    # open or anything else
    return C_WHITE

# ---------------------------------------------------------------------------
# Build a short project tag for no-color mode: first 4 chars of project name.
# ---------------------------------------------------------------------------
def plain_tag(proj_name):
    short = (proj_name or "?")[:4]
    return f"[{short}]"

# ---------------------------------------------------------------------------
# Readers for input files (bugs, priorities, requirements)
# ---------------------------------------------------------------------------
STATUS_RE     = re.compile(r'^##\s+Status\s*\n+\s*(\S+)', re.M | re.IGNORECASE)
TARGET_VER_RE = re.compile(r'^##\s+Target Version\s*\n+\s*(v?\d+\.\d+\.\d+)', re.M | re.IGNORECASE)
BUG_ID_RE     = re.compile(r'^(BUG-\d+)', re.IGNORECASE)
PRIORITY_ID_RE= re.compile(r'^(PRIORITY-\d+)', re.IGNORECASE)
REQ_ID_RE     = re.compile(r'^(v\d+\.\d+\.\d+)(?:-.*)?$', re.IGNORECASE)

def get_file_status(path):
    try:
        text = pathlib.Path(path).read_text(encoding="utf-8", errors="replace")
        m = STATUS_RE.search(text)
        return m.group(1).strip().lower() if m else "open"
    except OSError:
        return "open"

def get_target_version(path):
    try:
        text = pathlib.Path(path).read_text(encoding="utf-8", errors="replace")
        m = TARGET_VER_RE.search(text)
        return m.group(1).strip() if m else ""
    except OSError:
        return ""

def compact_id(path, is_req):
    stem = pathlib.Path(path).stem
    if is_req:
        m = REQ_ID_RE.match(stem)
        return m.group(1) if m else stem
    m = BUG_ID_RE.match(stem)
    if m:
        return m.group(1)
    m = PRIORITY_ID_RE.match(stem)
    if m:
        return m.group(1)
    return stem

def parse_version(v):
    v = v.lstrip("vV")
    parts = v.split(".")
    if len(parts) < 3:
        return None
    try:
        return tuple(int(p) for p in parts[:3])
    except ValueError:
        return None

def matches_active_rc(version_str, active_rc):
    if not active_rc or active_rc.lower() == "none":
        return False
    tv = parse_version(version_str)
    arc = parse_version(active_rc)
    if tv is None or arc is None:
        return False
    return tv == arc

def is_shipped(version_str, last_released):
    # SIBLING: two-arg is_shipped — multi-project path (this PYEOF heredoc).
    # Receives last_released per-entry from all_last_released[idx] (built in
    # the bash loop that calls pp_last_released_version per project).
    # Sentinel guard and comparison logic must stay in sync with the one-arg
    # is_shipped() in the single-project PY heredoc below (~line 1530).
    if not last_released or last_released.lower() in ("none", "v0.0.0", ""):
        return False
    tv = parse_version(version_str)
    lv = parse_version(last_released)
    if tv is None or lv is None:
        return False
    return tv <= lv

# ---------------------------------------------------------------------------
# Readers for queue (agent backlog) files
# ---------------------------------------------------------------------------
# Queue line pattern — accepts BOTH task ID formats:
#   Old format: CLAUDE-<AGENT>-YYYYMMDD-NNN-slug   (PARTICIPANT-AGENT prefix)
#   New format: <AGENT>-YYYYMMDD-NNN-slug            (agent-only prefix)
#
# Capture groups:
#   1  marker character (x, A, B, W, space, …)
#   2  full task ID string (everything after the marker, trimmed)
#   3  date string (YYYYMMDD) — always group 3 for old format, group 5 for new
#   4  sequence number        — always group 4 for old format, group 6 for new
#
# Because group indices differ by format, the caller must use the helper
# function parse_queue_line_match() below to extract date and seq fields
# in a format-agnostic way.
#
# Old format example: - [ ] CLAUDE-CODER-20260517-061-extract-templates
# New format example: - [ ] CODER-20260518-002-implement-feature
QUEUE_LINE_RE = re.compile(
    r'^\s*-\s+\[(.)\]\s+'
    r'(((?:CLAUDE|CODEX|GEMINI)-[A-Z][A-Z0-9]*-(\d{8})-(\d+)-\S+)'  # old: grp2=id grp3=date grp4=seq
    r'|([A-Z][A-Z0-9]*-(\d{8})-(\d+)-\S+))',                          # new: grp5=id grp6=date grp7=seq
    re.IGNORECASE,
)

def parse_queue_line_match(m):
    """
    Extract (task_id, date_str, seq_str) from a QUEUE_LINE_RE match.
    Works for both old (PARTICIPANT-AGENT-DATE-SEQ-slug) and new (AGENT-DATE-SEQ-slug) formats.

    Group layout (from QUEUE_LINE_RE):
      group(1) = marker
      group(2) = full task ID (always present when any format matched)
      group(3) = old-format id body (present only for old format)
      group(4) = date string (old format)
      group(5) = seq string  (old format)
      group(6) = new-format id body (present only for new format)
      group(7) = date string (new format)
      group(8) = seq string  (new format)

    Returns (task_id, date_str, seq_str) or None when no format could be parsed.
    """
    task_id = m.group(2)
    if not task_id:
        return None
    if m.group(3) is not None:
        # Old format: PARTICIPANT-AGENT-DATE-SEQ-slug
        return task_id, m.group(4), m.group(5)
    else:
        # New format: AGENT-DATE-SEQ-slug
        return task_id, m.group(7), m.group(8)
STATE_RE = re.compile(r'^##\s+State\s*\n+\s*(\S+)', re.M | re.IGNORECASE)

def read_task_state(tasks_dir, task_id):
    status_path = pathlib.Path(tasks_dir) / task_id / "status.md"
    if not status_path.is_file():
        return ""
    try:
        text = status_path.read_text(encoding="utf-8", errors="replace")
        m = STATE_RE.search(text)
        return m.group(1).strip().upper() if m else ""
    except OSError:
        return ""

def marker_to_state(marker):
    m = marker.upper()
    if m == "X":
        return "DONE"
    if m == "A":
        return "WORKING"
    if m == "B":
        return "BLOCKED"
    if m == "W":
        return "WAITING"
    return "BACKLOG"

def queue_state_to_status(state):
    """Map a task state to a display status (for status_to_color)."""
    s = state.upper()
    if s in ("WORKING",):
        return "running"
    if s in ("DONE", "WONT-DO"):
        return "done"
    if s == "BLOCKED":
        return "blocked"
    return "open"

def classify_state(state):
    s = state.upper()
    if s in ("WORKING", "BACKLOG", "WAITING"):
        return "active"
    if s in ("DONE", "WONT-DO", "BLOCKED"):
        return "closed"
    return "open"

# ---------------------------------------------------------------------------
# ID-based sort key helpers.
#
# For input columns the sort key is derived from the compact ID, not mtime:
#   - REQUIREMENTS: semver tuple (major, minor, patch) — higher is newer
#   - All others:   integer from the numeric suffix (e.g. BUG-NNNN → NNNN)
#
# For queue columns the sort key is (date_str, seq) — already integer-based.
#
# In both cases DESC means largest key value first.
# ---------------------------------------------------------------------------

NUMERIC_SUFFIX_RE = re.compile(r'(\d+)\s*$')

def id_sort_key_input(cid, is_req):
    """
    Return a sort key (higher == newer/larger/DESC-first) for a compact ID.

    REQUIREMENTS column uses semver tuple: (major, minor, patch).
    All other columns extract the trailing integer from the compact ID.
    Returns a value that can be compared; unknown IDs sort last (key = -1 or (0,0,0)).
    """
    if is_req:
        v = parse_version(cid)
        return v if v is not None else (0, 0, 0)
    m = NUMERIC_SUFFIX_RE.search(cid)
    return int(m.group(1)) if m else -1

# ---------------------------------------------------------------------------
# Collect all entries from all projects (multi-project, mixed).
# Each entry is a dict with keys:
#   proj_name, proj_color, display_id, status, sort_key, entry_type
# ---------------------------------------------------------------------------
# Per-project entry lists (one inner list per project).
# Index matches PROJ_NAMES order.  Populated by the IS_INPUT / IS_QUEUE
# collection loop below; consumed by the per-project block rendering loop.
per_proj_entries = [[] for _ in range(n_projects)]

IS_INPUT  = (subcommand == "input")
IS_QUEUE  = (subcommand == "queue")
IS_REQ    = (column_type == "requirements")

for idx in range(n_projects):
    pname  = proj_names[idx]
    pcolor = proj_colors[idx]

    if IS_INPUT:
        idir  = proj_input_dirs[idx]
        if idir == "none" or not pathlib.Path(idir).is_dir():
            continue

        active_rc    = all_active_rcs[idx] if all_active_rcs[idx].lower() not in ("none", "") else ""
        last_released = all_last_released[idx] if all_last_released[idx].lower() not in ("none", "") else ""

        candidates = []
        for entry in pathlib.Path(idir).iterdir():
            if not entry.is_file():
                continue
            if not entry.name.endswith(".md"):
                continue
            if entry.name.upper().startswith("README"):
                continue
            candidates.append(entry)

        # Build entry list for this project, with ID-based sort keys
        proj_item_list = []
        for path in candidates:
            cid    = compact_id(path, IS_REQ)
            status = get_file_status(path)

            if IS_REQ:
                target_ver = get_target_version(path)
                if target_ver and matches_active_rc(target_ver, active_rc):
                    suffix = " [active]"
                    status = "running"
                elif target_ver and is_shipped(target_ver, last_released):
                    status = "done"
                    suffix = ""
                else:
                    suffix = ""
                disp_id = cid + suffix
            else:
                disp_id = cid

            # Compute ID-based numeric/semver sort key.
            # Store the raw cid (without suffix) for sort key extraction.
            _sk = id_sort_key_input(cid, IS_REQ)

            proj_item_list.append({
                "proj_name":  pname,
                "proj_color": pcolor,
                "display_id": disp_id,
                "status":     status,
                "id_sort_key": _sk,
                "entry_type": "input",
                "state":      None,
                "date_str":   "",
                "seq":        0,
            })

        # Sort this project's items DESC by ID sort key (highest/newest first)
        proj_item_list.sort(key=lambda e: e["id_sort_key"], reverse=True)

        # For requirements columns, preserve the full uncapped list so the
        # active-window algorithm can access entries beyond proj_max_rows.
        # For all other input columns, apply per-project max_rows cap with
        # status-priority-aware selection: WORKING rows are never evicted,
        # BLOCKED rows are evicted only after all non-attention rows are gone.
        if IS_REQ:
            per_proj_entries[idx] = proj_item_list  # uncapped; windowing applies later
        else:
            p_max = proj_max_rows[idx]
            per_proj_entries[idx] = _status_priority_cap(proj_item_list, p_max)

    elif IS_QUEUE:
        bfile = proj_backlog_files[idx]
        tasks_dir = proj_tasks_dirs[idx]
        if bfile == "none" or not pathlib.Path(bfile).is_file():
            continue

        content = pathlib.Path(bfile).read_text(encoding="utf-8", errors="replace")
        proj_item_list = []
        for line in content.splitlines():
            m = QUEUE_LINE_RE.match(line)
            if not m:
                continue
            marker = m.group(1)
            parsed = parse_queue_line_match(m)
            if not parsed:
                continue
            task_id, date_str, seq_str = parsed

            state = read_task_state(tasks_dir, task_id)
            if not state:
                state = marker_to_state(marker)

            status = queue_state_to_status(state)
            seq_int = int(seq_str)

            proj_item_list.append({
                "proj_name":  pname,
                "proj_color": pcolor,
                "display_id": f"{date_str}-{seq_int:03d}",
                "status":     status,
                "id_sort_key": (date_str, seq_int),
                "entry_type": "queue",
                "state":      state,
                "date_str":   date_str,
                "seq":        seq_int,
            })

        # Sort this project's queue items DESC by (date_str, seq) — newest first
        proj_item_list.sort(key=lambda e: e["id_sort_key"], reverse=True)

        # Apply per-project max_rows cap with status-priority-aware selection:
        # WORKING rows are never evicted; BLOCKED next; remainder fills remaining slots.
        p_max = proj_max_rows[idx]
        per_proj_entries[idx] = _status_priority_cap(proj_item_list, p_max)

# ---------------------------------------------------------------------------
# Apply per-project minimum allocation + DESC global sort.
#
# For input columns (non-requirements): straight DESC-sorted set.
# For requirements input columns: active-window scrolling (see below).
# For queue columns: state-bucketed middle-position-active window.
# ---------------------------------------------------------------------------

# Shared helper: compute an active-centered window.
# Used by both the multi-project requirements path (here) and the
# single-project input path (in the separate PY heredoc below).
#
# Args:
#   all_entries   sorted list (DESC) of entries; each must have "status" field
#   rows          total row budget (includes marker rows)
#   is_multi      True = multi-project dicts; False = path objects (unused here)
#
# Returns (win_start, win_end, n_above, n_below)
#   win_start, win_end: slice indices into all_entries
#   n_above: count of hidden entries above window (0 if none)
#   n_below: count of hidden entries below window (0 if none)
def _req_window(all_entries, rows):
    N = len(all_entries)
    if N <= rows:
        return 0, N, 0, 0

    # Find active entry (status == "running")
    active_idx = next(
        (i for i, e in enumerate(all_entries) if e.get("status") == "running"),
        None
    )
    if active_idx is None:
        # No active entry: show newest rows items (top of DESC list)
        return 0, rows, 0, N - rows

    i = active_idx
    R = rows

    # Two-pass: pass 0 conservatively assumes 2 markers; pass 1 refines.
    _win_start, _win_end = 0, 0
    for _pass in range(2):
        if _pass == 0:
            _markers = 2
        else:
            _markers = (1 if _win_start > 0 else 0) + (1 if _win_end < N else 0)

        content_budget = max(1, R - _markers)

        if i < 3:
            _win_start = 0
            _win_end = min(N, content_budget)
        elif i + 3 >= N:
            _win_end = N
            _win_start = max(0, N - content_budget)
        else:
            half_before = content_budget // 2
            _win_start = max(0, i - half_before)
            _win_end = min(N, _win_start + content_budget)
            if _win_end == N:
                _win_start = max(0, N - content_budget)

    n_above = _win_start
    n_below = N - _win_end
    return _win_start, _win_end, n_above, n_below


# ---------------------------------------------------------------------------
# Per-project render blocks for all column types.
#
# Input columns: each project gets an INDEPENDENT visibility window within
# its own effective_per_project row allocation rather than merging all projects
# into a single list with a single active anchor.
#
# Queue/agent columns: the same per-project block rendering applies. Each
# project gets an independent middle-position-active window just like
# requirements/bugs/priorities columns.
#
# per_proj_render_blocks: list of lists, one sub-list per project.
# Each sub-list is a sequence of pre-formatted output strings (already tagged).
# The render section emits them sequentially (project 0, project 1, ...).
#
# For requirements:
#   Each project's items are DESC-sorted; the active-window algorithm (_req_window)
#   is applied within that project's own effective_per_project budget.
#   Per-project "... (N more above/below)" markers are emitted inside the block.
#
# For bugs / priorities:
#   Each project's items are DESC-sorted; the top effective_per_project items are
#   shown with an optional "... and N more" truncation marker.
#
# For queue (agent) columns:
#   Each project's items are DESC-sorted; middle-position-active windowing is
#   applied per-project within the effective_per_project budget.
# ---------------------------------------------------------------------------

per_proj_render_blocks = []   # populated by IS_INPUT and IS_QUEUE branches below

# ---------------------------------------------------------------------------
# Tag rendering helper — defined here so IS_INPUT branches can call it.
# Defined here so IS_INPUT branches can call _render_entry_tagged during
# the per-project block population loop.
# ---------------------------------------------------------------------------

# Tag width: "■ " = 2 visible chars, or "[xxxx] " = 7 visible chars (no color).
# Reserve that many chars from width for the tag, leaving the rest for the ID.
TAG_VIS_LEN = 2   # "■ " in color mode
TAG_NC_LEN  = 7   # "[xxxx] " in no-color mode (4-char short name + brackets + space)

def _render_entry_tagged(e):
    """Render one multi-project entry dict as a tagged line."""
    pcolor     = e["proj_color"]
    display_id = e["display_id"]
    status     = e["status"]

    if use_color:
        tag_color = project_tag_color(pcolor)
        tag       = f"{tag_color}■{RESET} "
        tag_vis   = TAG_VIS_LEN
    else:
        tag     = f"[{e['proj_name'][:4]}] "
        tag_vis = len(tag)

    if use_color:
        sc  = status_to_color(status)
        txt = f"{sc}{display_id}{RESET}"
    else:
        txt = display_id

    avail = max(1, width - tag_vis)
    return tag + truncate_ansi(txt, avail)

if IS_INPUT and IS_REQ:
    # Requirements column: per-project active-window scrolling.
    # Each project computes its own independent window rather than sharing one
    # merged window across all projects.
    for idx in range(n_projects):
        proj_items = per_proj_entries[idx]
        block_lines = []

        if not proj_items:
            per_proj_render_blocks.append(block_lines)
            continue

        # Window budget for this project.  Use effective_per_project as the
        # per-project row cap; the two-pass algorithm in _req_window handles
        # the above/below markers within this budget.
        budget = effective_per_project

        _rs, _re, _n_above, _n_below = _req_window(proj_items, budget)
        win_entries = proj_items[_rs:_re]

        if _n_above > 0:
            marker = f"... ({_n_above} more above)"
            block_lines.append(truncate_ansi(marker, width))

        for e in win_entries:
            block_lines.append(_render_entry_tagged(e))

        if _n_below > 0:
            marker = f"... ({_n_below} more below)"
            block_lines.append(truncate_ansi(marker, width))

        per_proj_render_blocks.append(block_lines)

elif IS_INPUT:
    # Bugs / priorities column: per-project block rendering.
    # Each project shows its newest effective_per_project items; if the project
    # has more items, a "... and N more" truncation marker is appended.
    for idx in range(n_projects):
        proj_items = per_proj_entries[idx]
        block_lines = []

        if not proj_items:
            per_proj_render_blocks.append(block_lines)
            continue

        budget = effective_per_project
        total  = len(proj_items)

        if total > budget:
            # Show budget-1 items + one truncation marker
            for e in proj_items[:budget - 1]:
                block_lines.append(_render_entry_tagged(e))
            n_more = total - (budget - 1)
            block_lines.append(truncate_ansi(f"... and {n_more} more", width))
        else:
            for e in proj_items[:budget]:
                block_lines.append(_render_entry_tagged(e))

        per_proj_render_blocks.append(block_lines)

elif IS_QUEUE:
    # Queue (agent) columns: per-project block rendering.
    #
    # Each registered project gets an independent middle-position-active
    # visibility window within its own effective_per_project row budget, just
    # as requirements/bugs/priorities columns do via per_proj_render_blocks.
    # The per-project block approach ensures each project gets its fair share
    # of the column: each
    # project is guaranteed its own effective_per_project-row block,
    # independent of other projects' task counts or recency.
    budget = effective_per_project

    for idx in range(n_projects):
        proj_items = per_proj_entries[idx]
        block_lines = []

        if not proj_items:
            per_proj_render_blocks.append(block_lines)
            continue

        # Bucket this project's items by state class.
        # "open"   → nothing maps here with current classify_state
        # "active" → WORKING, BACKLOG, WAITING
        # "closed" → DONE, WONT-DO, BLOCKED
        # (per_proj_entries[idx] is already sorted DESC by date/seq)
        q_open   = [e for e in proj_items if classify_state(e["state"]) == "open"]
        q_active = [e for e in proj_items if classify_state(e["state"]) == "active"]
        q_closed = [e for e in proj_items if classify_state(e["state"]) == "closed"]

        # Middle-position-active windowing within this project's budget.
        n_active_p = min(len(q_active), budget)
        active_slice_p = q_active[:n_active_p]

        remaining_p    = budget - n_active_p
        above_ideal_p  = remaining_p // 2
        below_ideal_p  = remaining_p - above_ideal_p

        above_avail_p  = min(above_ideal_p, len(q_open))
        leftover_above_p = above_ideal_p - above_avail_p
        below_avail_p  = min(below_ideal_p + leftover_above_p, len(q_closed))
        leftover_below_p = (below_ideal_p + leftover_above_p) - below_avail_p
        above_final_p  = min(above_avail_p + leftover_below_p, len(q_open))

        window_entries = (
            q_open[:above_final_p]
            + active_slice_p
            + q_closed[:below_avail_p]
        )

        for e in window_entries:
            block_lines.append(_render_entry_tagged(e))

        per_proj_render_blocks.append(block_lines)

# ---------------------------------------------------------------------------
# Render
# ---------------------------------------------------------------------------

if IS_INPUT or IS_QUEUE:
    # All column types now use per-project block rendering.
    # Emit each project's block sequentially.  If every project produced an
    # empty block, emit "(empty)" so the caller always gets at least one line.
    # (IS_INPUT: populated by the requirements/bugs/priorities branches above.
    #  IS_QUEUE: populated by the queue branch above.)
    any_output = False
    for block_lines in per_proj_render_blocks:
        for line in block_lines:
            print(line)
            any_output = True

    if not any_output:
        print("(empty)")
PYEOF

    exit 0
fi

# ---------------------------------------------------------------------------
# Subcommand dispatch
# ---------------------------------------------------------------------------
if [[ "$SUBCOMMAND" == "input" ]]; then
    # input <dir> <cache_file> [rows] [width]
    if [[ ${#POSARGS[@]} -lt 3 ]]; then
        echo "ERROR: 'input' requires <dir> and <cache_file>" >&2
        echo "Usage: $0 input <dir> <cache_file> [rows] [width]" >&2
        exit 1
    fi
    INPUT_DIR="${POSARGS[1]}"
    CACHE_FILE="${POSARGS[2]}"
    ROWS="${POSARGS[3]:-$DASHBOARD_ROWS_PER_COLUMN}"
    WIDTH="${POSARGS[4]:-38}"

    # Validate rows/width are positive integers
    if ! [[ "$ROWS" =~ ^[0-9]+$ ]] || (( ROWS <= 0 )); then
        echo "WARNING: rows='${ROWS}' invalid; using ${DASHBOARD_ROWS_PER_COLUMN}" >&2
        ROWS="$DASHBOARD_ROWS_PER_COLUMN"
    fi
    if ! [[ "$WIDTH" =~ ^[0-9]+$ ]] || (( WIDTH <= 0 )); then
        echo "WARNING: width='${WIDTH}' invalid; using 38" >&2
        WIDTH=38
    fi

    # Determine if this is a requirements/ column (suffix indicators apply)
    IS_REQUIREMENTS=false
    if [[ "$INPUT_DIR" == *requirements* ]]; then
        IS_REQUIREMENTS=true
    fi

    # Resolve the project that owns INPUT_DIR so all per-project lookups
    # (last_released, active RC, version ceilings, max_rows) use that project's
    # own values rather than a substituted default.
    #
    # Root-cause note: falling back to a default project here is exactly the
    # when the match failed, so a managed project's requirements were colored
    # against the self-build's version instead of their own.
    # Fix: match INPUT_DIR against each registered project's requirements/,
    # bugs/, and priority/ paths; use the matched project.  If no project
    # matches, _RENDER_PROJECT is left empty and all per-project lookups
    # produce empty strings — the Python renderer receives empty last_released
    # and treats requirements as unshipped (white/open), which is the correct
    # safe default.
    #
    # SIBLING NOTE — is_shipped() siblings (single-project and multi-project
    # heredocs).  This file contains two is_shipped()/last_released
    # implementations that must remain logically consistent:
    #   1. Two-arg is_shipped(version_str, last_released) — multi-project PYEOF
    #      heredoc (~line 811); receives last_released per-entry from
    #      all_last_released[idx] built in the bash loop (~lines 534-556).
    #   2. One-arg is_shipped(version_str) — single-project PY heredoc (~line
    #      1492); closes over the single `last_released` variable from sys.argv.
    # The sentinel guard logic in both must stay identical.  Until these are
    # collapsed into a single implementation, treat changes to either as
    # requiring a corresponding audit of the other.

    # Normalize INPUT_DIR for path comparison (strip trailing slash; resolve symlinks
    # conservatively with realpath -m so non-existent dirs still match).
    _INPUT_DIR_NORM="$(realpath -m "$INPUT_DIR" 2>/dev/null || echo "$INPUT_DIR")"
    _INPUT_PROJECT=""
    while IFS= read -r _p; do
        [[ -z "$_p" ]] && continue
        # Check requirements/, bugs/, and priority/ for this project.
        for _dir_fn in pp_requirements_dir pp_bugs_dir pp_priority_dir; do
            _p_dir="$(KANBAN_ROOT="$KANBAN_ROOT" "$_dir_fn" "$_p" 2>/dev/null || true)"
            [[ -z "$_p_dir" ]] && continue
            _p_dir_norm="$(realpath -m "$_p_dir" 2>/dev/null || echo "$_p_dir")"
            if [[ "$_INPUT_DIR_NORM" == "$_p_dir_norm" ]]; then
                _INPUT_PROJECT="$_p"
                break 2
            fi
        done
    done < <(KANBAN_ROOT="$KANBAN_ROOT" projects_cfg_list 2>/dev/null || true)
    # Use the matched project; when no match, leave empty so per-project
    # lookups below produce empty values (safe open/unshipped rendering).
    _RENDER_PROJECT="$_INPUT_PROJECT"

    ACTIVE_RC=""
    MAX_MINOR=""
    MAX_MAJOR=""
    LAST_RELEASED=""

    # Resolve per-project max_rows (precedence: per-project cfg > DASHBOARD_MAX_ROWS > 20)
    PROJ_MAX_ROWS="$(KANBAN_ROOT="$KANBAN_ROOT" projects_cfg_max_rows "$_RENDER_PROJECT" 2>/dev/null || echo "${DASHBOARD_MAX_ROWS:-20}")"
    [[ -z "$PROJ_MAX_ROWS" ]] && PROJ_MAX_ROWS="${DASHBOARD_MAX_ROWS:-20}"

    if [[ "$IS_REQUIREMENTS" == "true" ]]; then
        # Read Active RC from release-state.md for the rendered project.
        RELEASE_STATE="$(pp_release_state "$_RENDER_PROJECT" 2>/dev/null)" || RELEASE_STATE=""
        if [[ -f "$RELEASE_STATE" ]]; then
            # Liberal parse — read first header only, trim whitespace,
            # then validate: accept only vX.Y.Z semver or 'none'; anything else → empty.
            ACTIVE_RC="$(awk '/^## Active RC/{found=1;next} found && /^[[:space:]]*$/{next} found{print; exit}' "$RELEASE_STATE" 2>/dev/null | tr -d '[:space:]')" || ACTIVE_RC=""
            if [[ "$ACTIVE_RC" == "none" ]] || [[ -z "$ACTIVE_RC" ]]; then
                ACTIVE_RC=""
            elif ! [[ "$ACTIVE_RC" =~ ^v[0-9]+\.[0-9]+\.[0-9]+$ ]]; then
                ACTIVE_RC=""  # malformed / partial / unexpected — treat as none (no [active])
            fi
        fi
        MAX_MINOR="$(pp_max_minor "$_RENDER_PROJECT" 2>/dev/null)" || MAX_MINOR=""
        MAX_MAX_MAJOR="$(pp_max_major "$_RENDER_PROJECT" 2>/dev/null)" || MAX_MAX_MAJOR=""
        MAX_MAJOR="${MAX_MAX_MAJOR:-}"
        # Resolve last_released for this entry's own project (never a default).
        # The sentinel guard ("none" / "v0.0.0") is preserved: Python's
        # is_shipped() returns False when last_released is empty, which ensures
        # a fresh project with no releases never renders open requirements green.
        LAST_RELEASED="$(pp_last_released_version "$_RENDER_PROJECT" 2>/dev/null)" || LAST_RELEASED=""
        # Strip sentinel values so Python receives "" and is_shipped() returns False.
        [[ "$LAST_RELEASED" == "v0.0.0" || "${LAST_RELEASED,,}" == "none" ]] && LAST_RELEASED=""
    fi

    # Delegate to Python for the rendering logic
    python3 - \
        "$INPUT_DIR" \
        "$CACHE_FILE" \
        "$ROWS" \
        "$WIDTH" \
        "$IS_REQUIREMENTS" \
        "$ACTIVE_RC" \
        "$MAX_MINOR" \
        "$MAX_MAJOR" \
        "$USE_COLOR" \
        "${C_RED}" "${C_YELLOW}" "${C_GREEN}" "${C_WHITE}" "${C_DIM}" "${RESET}" \
        "$LAST_RELEASED" \
        "$PROJ_MAX_ROWS" \
    <<'PY'
import os, re, sys, pathlib

input_dir       = pathlib.Path(sys.argv[1])
cache_file      = pathlib.Path(sys.argv[2])
rows            = int(sys.argv[3])
width           = int(sys.argv[4])
is_requirements = sys.argv[5].lower() == "true"
active_rc       = sys.argv[6].strip()    # e.g. "v0.21.16" or ""
max_minor_str   = sys.argv[7].strip()    # e.g. "21" or ""
max_major_str   = sys.argv[8].strip()    # e.g. "0" or ""
use_color       = sys.argv[9].lower() == "true"
C_RED, C_YELLOW, C_GREEN, C_WHITE, C_DIM, RESET = sys.argv[10:16]
last_released   = sys.argv[16].strip() if len(sys.argv) > 16 else ""  # e.g. "v0.21.25" or ""
# max_rows: per-project cap with '... and N more' indicator when exceeded
_max_rows_str   = sys.argv[17].strip() if len(sys.argv) > 17 else "20"
try:
    max_rows = int(_max_rows_str)
    if max_rows < 1:
        max_rows = 20
except (ValueError, TypeError):
    max_rows = 20

max_minor = int(max_minor_str) if max_minor_str else None
max_major = int(max_major_str) if max_major_str else None

# ---------------------------------------------------------------------------
# List files sorted by mtime, newest first. Filter out non-.md and README.
# No cache-based filtering: show newest N (up to rows) regardless of lifecycle/queue state.
# ---------------------------------------------------------------------------
if not input_dir.is_dir():
    print("(empty)")
    sys.exit(0)

candidates = []
for entry in input_dir.iterdir():
    if not entry.is_file():
        continue
    if not entry.name.endswith(".md"):
        continue
    if entry.name.upper().startswith("README"):
        continue
    candidates.append(entry)

# Sort newest first by mtime
candidates.sort(key=lambda p: p.stat().st_mtime, reverse=True)

if not candidates:
    print("(empty)")
    sys.exit(0)

# ---------------------------------------------------------------------------
# Version parsing helpers
# ---------------------------------------------------------------------------
def parse_version(v):
    """Parse vX.Y.Z or X.Y.Z into (major, minor, patch) tuple, or None."""
    v = v.lstrip("vV")
    parts = v.split(".")
    if len(parts) < 3:
        return None
    try:
        return tuple(int(p) for p in parts[:3])
    except ValueError:
        return None

def matches_active_rc(version_str):
    """Return True if version_str matches the active RC."""
    if not active_rc:
        return False
    tv = parse_version(version_str)
    arc = parse_version(active_rc)
    if tv is None or arc is None:
        return False
    return tv == arc

def is_shipped(version_str):
    """Return True if version_str <= last_released version.

    SIBLING: one-arg is_shipped — single-project path (this PY heredoc).
    Closes over the module-level `last_released` variable (sys.argv[16]),
    which is resolved per the project that owns INPUT_DIR (owning-project resolution;
    see bash section above where _RENDER_PROJECT is derived).
    Sentinel guard and comparison logic must stay in sync with the two-arg
    is_shipped(version_str, last_released) in the multi-project PYEOF
    heredoc above (~line 811).

    Returns False when last_released is empty, None, or the fresh-install
    sentinel ("none" or "v0.0.0", case-insensitive), ensuring a fresh
    project never renders open requirements as green/done.
    """
    if not last_released or last_released.lower() in ("none", "v0.0.0", ""):
        return False
    tv = parse_version(version_str)
    lv = parse_version(last_released)
    if tv is None or lv is None:
        return False
    return tv <= lv

# ---------------------------------------------------------------------------
# For requirements: extract Target Version from file header
# ---------------------------------------------------------------------------
TARGET_VER_RE = re.compile(r'^##\s+Target Version\s*\n+\s*(v?\d+\.\d+\.\d+)', re.M | re.IGNORECASE)

def get_target_version(path):
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
        m = TARGET_VER_RE.search(text)
        return m.group(1).strip() if m else ""
    except OSError:
        return ""

# ---------------------------------------------------------------------------
# For bugs/priority: extract Status field from file
# ---------------------------------------------------------------------------
STATUS_RE = re.compile(r'^##\s+Status\s*\n+\s*(\S+)', re.M | re.IGNORECASE)

def get_file_status(path):
    """Return the Status field value (e.g. open, running, done) or empty string."""
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
        m = STATUS_RE.search(text)
        return m.group(1).strip().lower() if m else ""
    except OSError:
        return ""

# ---------------------------------------------------------------------------
# Compact ID helpers
# ---------------------------------------------------------------------------
BUG_ID_RE      = re.compile(r'^(BUG-\d+)', re.IGNORECASE)
PRIORITY_ID_RE = re.compile(r'^(PRIORITY-\d+)', re.IGNORECASE)
# Requirements: vX.Y.Z optionally followed by any suffix (bundle OR operator-authored slug).
# Capture group is always just the vX.Y.Z portion; everything after the first '-' is dropped
# for display purposes.  Operator-authored filenames like
# v0.23.0-multi-project-iteration match this pattern; only the vX.Y.Z portion is kept.
REQ_ID_RE      = re.compile(r'^(v\d+\.\d+\.\d+)(?:-.*)?$', re.IGNORECASE)

def compact_id(path, is_req):
    """Return the compact display ID for the file."""
    stem = path.stem  # filename without .md
    if is_req:
        m = REQ_ID_RE.match(stem)
        return m.group(1) if m else stem
    m = BUG_ID_RE.match(stem)
    if m:
        return m.group(1)
    m = PRIORITY_ID_RE.match(stem)
    if m:
        return m.group(1)
    return stem  # fallback: full stem

# ---------------------------------------------------------------------------
# Visible-length-aware truncation: ANSI codes do not count toward width.
# If a string + suffix would exceed width, truncate the base and add "…".
# ---------------------------------------------------------------------------
ANSI_ESC_RE = re.compile(r'\033\[[0-9;]*m')

def visible_len(s):
    return len(ANSI_ESC_RE.sub("", s))

def truncate_ansi(text, max_width):
    """
    Truncate `text` so its visible length <= max_width.
    ANSI escape codes are preserved but not counted toward width.
    If truncation is needed, the last visible character is replaced with '…'.
    """
    if visible_len(text) <= max_width:
        return text

    # Walk character by character; track visible length
    result = []
    vlen = 0
    i = 0
    # Reserve 1 char for the ellipsis
    target = max_width - 1

    while i < len(text):
        # Check for ANSI escape at this position
        m = ANSI_ESC_RE.match(text, i)
        if m:
            result.append(m.group(0))
            i = m.end()
            continue
        ch = text[i]
        if vlen >= target:
            break
        result.append(ch)
        vlen += 1
        i += 1

    # Append RESET before ellipsis to avoid color bleed
    if use_color and any(ANSI_ESC_RE.search(s) for s in result):
        result.append("\033[0m")
    result.append("…")
    return "".join(result)

# ---------------------------------------------------------------------------
# Render items — newest N (up to rows), no cache-based skip.
#
# Requirements column (is_requirements == True) and N > rows:
#   Use active-window scrolling: center the visible window around the [active]
#   entry so it is always visible.  Show "... (N more above)" / "... (N more
#   below)" markers when entries are hidden outside the window.
#
#   Window rules (i = active index, N = total, R = row budget):
#     - i < 3:        show items 0 .. content_budget-1  (start of queue)
#     - i+3 >= N:     show items N-content_budget .. N-1 (end of queue)
#     - otherwise:    center around i, filling content_budget rows
#   where content_budget = R - (1 if above_hidden else 0)
#                             - (1 if below_hidden else 0)
#
# Non-requirements columns and requirements columns with N <= rows:
#   Apply per-project max_rows cap: if total items exceed max_rows, show
#   (max_rows - 1) items followed by a '... and N more' truncation row.
# ---------------------------------------------------------------------------

def render_entry(path):
    """Return the formatted display string for one requirements or other entry."""
    cid = compact_id(path, is_requirements)

    if is_requirements:
        target_ver = get_target_version(path)
        suffix = ""
        color = ""

        if target_ver:
            if matches_active_rc(target_ver):
                suffix = " [active]"
                color = C_YELLOW
            elif is_shipped(target_ver):
                color = C_GREEN
            else:
                color = C_WHITE
        else:
            color = C_WHITE

        if suffix:
            if use_color:
                display = f"{color}{cid}{suffix}{RESET}"
            else:
                display = f"{cid}{suffix}"
        else:
            if use_color and color:
                display = f"{color}{cid}{RESET}"
            else:
                display = cid
    else:
        status = get_file_status(path)
        if status == "running":
            color = C_YELLOW
        elif status in ("done", "wont-do"):
            color = C_GREEN
        elif status == "blocked":
            color = C_RED
        else:
            color = C_WHITE

        if use_color and color:
            display = f"{color}{cid}{RESET}"
        else:
            display = cid

    return truncate_ansi(display, width)


# Effective display limit: respect both rows (pane height) and max_rows.
effective_limit = min(rows, max_rows)
total_candidates = len(candidates)

output_lines = []

if is_requirements and total_candidates > rows:
    # Active-window scrolling for requirements column.
    # Find the [active] entry index (the one matching the active RC).
    active_idx = None
    for _idx, _path in enumerate(candidates):
        _tv = get_target_version(_path)
        if _tv and matches_active_rc(_tv):
            active_idx = _idx
            break

    if active_idx is None:
        # No active entry found — fall back to showing the newest rows items.
        for path in candidates[:rows]:
            output_lines.append(render_entry(path))
    else:
        N = total_candidates
        R = rows
        i = active_idx

        # Determine window position using a two-pass approach:
        # Pass 1: assume 2 markers (worst case) to find content budget.
        # Pass 2: refine with actual marker count.
        for _pass in range(2):
            if _pass == 0:
                # Conservative: assume both markers present
                _markers = 2
            else:
                # Actual markers from pass 1 result
                _markers = (1 if _win_start > 0 else 0) + (1 if _win_end < N else 0)

            content_budget = max(1, R - _markers)

            if i < 3:
                # Near start: show from beginning
                _win_start = 0
                _win_end = min(N, content_budget)
            elif i + 3 >= N:
                # Near end: show to end
                _win_end = N
                _win_start = max(0, N - content_budget)
            else:
                # Middle: center around i
                half_before = content_budget // 2
                _win_start = max(0, i - half_before)
                _win_end = min(N, _win_start + content_budget)
                # If end hit the boundary, shift start back
                if _win_end == N:
                    _win_start = max(0, N - content_budget)

        win_start = _win_start
        win_end   = _win_end
        above_hidden = win_start > 0
        below_hidden = win_end < N

        if above_hidden:
            marker = f"... ({win_start} more above)"
            output_lines.append(truncate_ansi(marker, width))

        for path in candidates[win_start:win_end]:
            output_lines.append(render_entry(path))

        if below_hidden:
            n_below = N - win_end
            marker = f"... ({n_below} more below)"
            output_lines.append(truncate_ansi(marker, width))

elif total_candidates > max_rows:
    # Non-requirements column (or requirements with N <= rows already handled above):
    # show max_rows-1 items + one '... and N more' row.
    for path in candidates[:max_rows - 1]:
        output_lines.append(render_entry(path))
    output_lines.append(truncate_ansi(f"... and {total_candidates - (max_rows - 1)} more", width))

else:
    for path in candidates[:effective_limit]:
        output_lines.append(render_entry(path))

if output_lines:
    for line in output_lines:
        print(line)
else:
    print("(empty)")
PY

elif [[ "$SUBCOMMAND" == "queue" ]]; then
    # queue <backlog_file> [rows] [width]
    if [[ ${#POSARGS[@]} -lt 2 ]]; then
        echo "ERROR: 'queue' requires <backlog_file>" >&2
        echo "Usage: $0 queue <backlog_file> [rows] [width]" >&2
        exit 1
    fi
    BACKLOG_FILE="${POSARGS[1]}"
    ROWS="${POSARGS[2]:-$DASHBOARD_ROWS_PER_COLUMN}"
    WIDTH="${POSARGS[3]:-22}"

    # Validate rows/width
    if ! [[ "$ROWS" =~ ^[0-9]+$ ]] || (( ROWS <= 0 )); then
        echo "WARNING: rows='${ROWS}' invalid; using ${DASHBOARD_ROWS_PER_COLUMN}" >&2
        ROWS="$DASHBOARD_ROWS_PER_COLUMN"
    fi
    if ! [[ "$WIDTH" =~ ^[0-9]+$ ]] || (( WIDTH <= 0 )); then
        echo "WARNING: width='${WIDTH}' invalid; using 22" >&2
        WIDTH=22
    fi

    # Resolve the tasks directory (for reading status.md files).
    # Explicit-drop: when no project context is available, leave
    # TASKS_DIR empty so the Python renderer shows queue entries without status
    # lookups rather than silently substituting the first-registered project.
    # Callers that need per-project status lookups must pass a resolved TASKS_DIR
    # via the environment or a future --project argument.
    TASKS_DIR=""
    # Resolve per-project max_rows (precedence: per-project cfg > DASHBOARD_MAX_ROWS > 20)
    QUEUE_PROJ_MAX_ROWS="${DASHBOARD_MAX_ROWS:-20}"

    python3 - \
        "$BACKLOG_FILE" \
        "$ROWS" \
        "$WIDTH" \
        "$USE_COLOR" \
        "${C_RED}" "${C_YELLOW}" "${C_GREEN}" "${C_WHITE}" "${C_DIM}" "${RESET}" \
        "$TASKS_DIR" \
        "$QUEUE_PROJ_MAX_ROWS" \
    <<'PY'
import re, sys, pathlib

backlog_file = pathlib.Path(sys.argv[1])
rows         = int(sys.argv[2])
width        = int(sys.argv[3])
use_color    = sys.argv[4].lower() == "true"
C_RED, C_YELLOW, C_GREEN, C_WHITE, C_DIM, RESET = sys.argv[5:11]
tasks_dir    = pathlib.Path(sys.argv[11])
# max_rows: per-project cap with '... and N more' indicator when exceeded
_max_rows_str = sys.argv[12] if len(sys.argv) > 12 else "20"
try:
    max_rows = int(_max_rows_str)
    if max_rows < 1:
        max_rows = 20
except (ValueError, TypeError):
    max_rows = 20

# Cap at 13 per the middle-position-active algorithm regardless of caller rows arg.
MAX_ITEMS = 13
rows = min(rows, MAX_ITEMS)

ANSI_ESC_RE = re.compile(r'\033\[[0-9;]*m')

def visible_len(s):
    return len(ANSI_ESC_RE.sub("", s))

def truncate_ansi(text, max_width):
    """Truncate to max_width visible chars, preserving ANSI codes."""
    if visible_len(text) <= max_width:
        return text

    result = []
    vlen = 0
    i = 0
    target = max_width - 1

    while i < len(text):
        m = ANSI_ESC_RE.match(text, i)
        if m:
            result.append(m.group(0))
            i = m.end()
            continue
        ch = text[i]
        if vlen >= target:
            break
        result.append(ch)
        vlen += 1
        i += 1

    if use_color and any(ANSI_ESC_RE.search(s) for s in result):
        result.append("\033[0m")
    result.append("…")
    return "".join(result)

# ---------------------------------------------------------------------------
# Read task's status.md to get the authoritative ## State value.
# Falls back to the queue marker if the status.md is unreachable.
# ---------------------------------------------------------------------------
STATE_RE = re.compile(r'^##\s+State\s*\n+\s*(\S+)', re.M | re.IGNORECASE)

def get_task_state_from_status(task_id):
    """Return the ## State value from the task's status.md, or '' on failure."""
    status_path = tasks_dir / task_id / "status.md"
    if not status_path.is_file():
        return ""
    try:
        text = status_path.read_text(encoding="utf-8", errors="replace")
        m = STATE_RE.search(text)
        return m.group(1).strip().upper() if m else ""
    except OSError:
        return ""

def marker_to_state(marker):
    """Convert queue marker char to a state string (fallback when status.md missing)."""
    m = marker.upper()
    if m == 'X':
        return "DONE"
    if m == 'A':
        return "WORKING"
    if m == 'B':
        return "BLOCKED"
    if m == 'W':
        return "WAITING"
    return "BACKLOG"

def state_to_color(state):
    """Map a state string to the appropriate ANSI color code."""
    s = state.upper()
    if s == "BLOCKED":
        return C_RED
    if s in ("WORKING",):
        return C_YELLOW
    if s in ("DONE", "WONT-DO"):
        return C_GREEN
    # BACKLOG, WAITING, or anything else → white
    return C_WHITE

def classify_state(state):
    """
    Classify a task state into one of three buckets for window positioning.

    Returns:
        "active"  — WORKING, BACKLOG, WAITING  (center block)
        "closed"  — DONE, WONT-DO, BLOCKED     (below active block)
        "open"    — any other non-empty state   (above active block)
    """
    s = state.upper()
    if s in ("WORKING", "BACKLOG", "WAITING"):
        return "active"
    if s in ("DONE", "WONT-DO", "BLOCKED"):
        return "closed"
    return "open"

# ---------------------------------------------------------------------------
# Parse backlog file — ALL task IDs (no skip-on-[x]).
#
# Accepts BOTH task ID formats:
#   Old format: CLAUDE-<AGENT>-YYYYMMDD-NNN-slug   (PARTICIPANT-AGENT prefix)
#   New format: <AGENT>-YYYYMMDD-NNN-slug            (agent-only prefix)
#
# We collect all matching lines, sort newest first by (date, seq), take top N.
# ---------------------------------------------------------------------------
QUEUE_LINE_RE = re.compile(
    r'^\s*-\s+\[(.)\]\s+'
    r'(((?:CLAUDE|CODEX|GEMINI)-[A-Z][A-Z0-9]*-(\d{8})-(\d+)-\S+)'  # old: grp2=id grp3=date grp4=seq
    r'|([A-Z][A-Z0-9]*-(\d{8})-(\d+)-\S+))',                          # new: grp5=id grp6=date grp7=seq
    re.IGNORECASE,
)

def parse_queue_line_match(m):
    """
    Extract (task_id, date_str, seq_str) from a QUEUE_LINE_RE match.
    Works for both old (PARTICIPANT-AGENT-DATE-SEQ-slug) and new (AGENT-DATE-SEQ-slug) formats.

    Group layout (from QUEUE_LINE_RE):
      group(1) = marker
      group(2) = full task ID (always present when any format matched)
      group(3) = old-format id body (present only for old format)
      group(4) = date string (old format)
      group(5) = seq string  (old format)
      group(6) = new-format id body (present only for new format)
      group(7) = date string (new format)
      group(8) = seq string  (new format)

    Returns (task_id, date_str, seq_str) or None when no format could be parsed.
    """
    task_id = m.group(2)
    if not task_id:
        return None
    if m.group(3) is not None:
        # Old format: PARTICIPANT-AGENT-DATE-SEQ-slug
        return task_id, m.group(4), m.group(5)
    else:
        # New format: AGENT-DATE-SEQ-slug
        return task_id, m.group(7), m.group(8)

if not backlog_file.is_file():
    print("(empty)")
    sys.exit(0)

content = backlog_file.read_text(encoding="utf-8", errors="replace")

raw_entries = []  # list of (date_str, seq, task_id, marker)
for line in content.splitlines():
    m = QUEUE_LINE_RE.match(line)
    if not m:
        continue
    marker = m.group(1)
    parsed = parse_queue_line_match(m)
    if not parsed:
        continue
    task_id, date_str, seq_str = parsed

    raw_entries.append((date_str, int(seq_str), task_id, marker))

if not raw_entries:
    print("(empty)")
    sys.exit(0)

# Sort newest first: by (date_str DESC, seq DESC)
raw_entries.sort(key=lambda e: (e[0], e[1]), reverse=True)

# ---------------------------------------------------------------------------
# Resolve states and split into buckets (all items, newest-first order).
# ---------------------------------------------------------------------------
all_items = []  # list of (date_str, seq, task_id, state)
for date_str, seq, task_id, marker in raw_entries:
    state = get_task_state_from_status(task_id)
    if not state:
        state = marker_to_state(marker)
    all_items.append((date_str, seq, task_id, state))

# Partition into three buckets preserving newest-first sort within each.
open_items   = [(d, s, tid, st) for d, s, tid, st in all_items if classify_state(st) == "open"]
active_items = [(d, s, tid, st) for d, s, tid, st in all_items if classify_state(st) == "active"]
closed_items = [(d, s, tid, st) for d, s, tid, st in all_items if classify_state(st) == "closed"]

# ---------------------------------------------------------------------------
# Middle-position-active window algorithm.
#
# Window has `rows` slots (default cap: 10).
# 1. Clamp active_items to at most `rows` entries (newest first).
# 2. Remaining slots split evenly: half above (open bucket), half below (closed).
#    Odd remainder goes to the below (closed) slot.
# 3. Unused slots (when a bucket is smaller than its ideal allocation) are
#    reallocated to the other non-active bucket so the window stays full.
# 4. Fill above from open_items (newest first), below from closed_items (newest first).
# 5. Final render order: open_slice (above), active_slice, closed_slice (below).
#
# Edge cases:
#   all-active   : active fills all rows; no open/closed slots.
#   all-done     : closed fills all rows; open bucket is empty, realloc to below.
#   fewer-than-10: only items that exist are shown; no blank-line padding.
#   empty        : "(empty)" printed; no error.
# ---------------------------------------------------------------------------
n_rows   = rows
n_active = min(len(active_items), n_rows)
active_slice = active_items[:n_active]

remaining = n_rows - n_active
# Ideal split: above gets floor, below gets ceil (odd remainder → below).
n_above_ideal = remaining // 2
n_below_ideal = remaining - n_above_ideal

# Clamp each bucket to what's actually available, then give unused slots to the other.
n_above_avail = min(n_above_ideal, len(open_items))
leftover_above = n_above_ideal - n_above_avail  # slots open can't fill → give to closed

n_below_avail = min(n_below_ideal + leftover_above, len(closed_items))
# Symmetric: if closed can't fill all its slots, give back to open.
leftover_below = (n_below_ideal + leftover_above) - n_below_avail
n_above_final = min(n_above_avail + leftover_below, len(open_items))

open_slice   = open_items[:n_above_final]
closed_slice = closed_items[:n_below_avail]

# Final ordered list for rendering: open (above) → active (center) → closed (below)
window = open_slice + active_slice + closed_slice

# ---------------------------------------------------------------------------
# Apply per-project max_rows cap with '... and N more' truncation indicator.
# max_rows limits total visible rows including the truncation row itself.
# Total items available = len(all_items) (all entries in the backlog).
#
# When total_items > max_rows, truncate the window to max_rows-1 entries and
# emit '... and N more' where N = total_items - n_actually_shown.
# n_actually_shown = min(max_rows-1, len(window)) accounts for the queue
# window algorithm already capping output at MAX_ITEMS.
# ---------------------------------------------------------------------------
total_items = len(all_items)
if total_items > max_rows:
    # Truncated view: target max_rows-1 items, but window may be smaller.
    n_want = max_rows - 1
    window = window[:n_want]
    n_actually_shown = len(window)
    n_more = total_items - n_actually_shown
    show_truncation = True
else:
    show_truncation = False
    n_more = 0

# ---------------------------------------------------------------------------
# Render
# ---------------------------------------------------------------------------
output_count = 0
for date_str, seq, task_id, state in window:
    short_id = f"{date_str}-{seq:03d}"

    color = state_to_color(state) if use_color else ""

    if use_color and color:
        display = f"{color}{short_id}{RESET}"
    else:
        display = short_id

    display = truncate_ansi(display, width)
    print(display)
    output_count += 1

if show_truncation:
    trunc_line = f"... and {n_more} more"
    print(truncate_ansi(trunc_line, width))
    output_count += 1

if output_count == 0:
    print("(empty)")
PY

fi
