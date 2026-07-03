#!/usr/bin/env bash
# team/scripts/cost-report.sh
# Backward-compatible cost reporting wrapper.
#
# This script preserves the existing cost-by-day / cost-by-RC interface for
# operators and cron jobs that depend on it.  The human-readable output now
# includes a footer pointing to the new metrics-report.sh CLI, which provides
# richer per-RC JSON rollup, cumulative CSV, JSONL streaming, and live-tail
# surfaces.
#
# New code should use metrics-report.sh directly.  cost-report.sh is retained
# for backward compatibility.
#
# Reads roll-up files produced by team/pm-agent/aggregate_tokens.py and
# pricing data from team/scripts/lib/token_pricing.json to produce
# human-readable or CSV cost reports.
#
# USAGE:
#   cost-report.sh [--month YYYY-MM] [--day YYYY-MM-DD] [--rc <version>]
#                  [--week [YYYY-Www|current]] [--year [YYYY|current]]
#                  [--since YYYY-MM-DD] [--until YYYY-MM-DD]
#                  [--last N] [--last-N-days N]
#                  [--project <name>] [--csv]
#                  [--kanban-root <path>] [-h|--help]
#
# SCOPE FLAGS (at most one group; default is month-to-date):
#   --month YYYY-MM       Report on a specific calendar month.
#                         Default (no flag): current month-to-date.
#   --day YYYY-MM-DD      Report on a single day.
#   --rc <version>        Report on a single RC (e.g. v0.23.22).
#   --week [YYYY-Www|current]
#                         Report on an ISO week (Monday through Sunday).
#                         "current" or no argument = Monday through today.
#                         Example: --week 2026-W19
#   --year [YYYY|current]
#                         Report on a calendar year. "current" or no argument
#                         = Jan 1 through today. Example: --year 2026.
#   --since YYYY-MM-DD    Report from a specific date through today.
#                         May be combined with --until for an explicit range.
#   --until YYYY-MM-DD    Report from the earliest available day through the
#                         specified date. May be combined with --since.
#   --last N              Report on the N most recent days through today.
#                         --last 1 is today only; --last 7 is today + 6 prior.
#   --last-N-days N       Alias for --last N.
#
# OTHER FLAGS:
#   --project <name>      Project name (required; or set PGAI_PROJECT_NAME env).
#   --csv                 Emit machine-readable CSV rows instead of the
#                         human-readable block. Column order is stable.
#   --kanban-root <path>  Override PGAI_AGENT_KANBAN_ROOT_PATH.
#   -h, --help            Show this help and exit.
#
# AUTO-AGGREGATION:
#   If the required roll-up file is missing, the script invokes
#   aggregate_tokens.py to build it on demand, then produces the report.
#
# PRICING:
#   Reads team/scripts/lib/token_pricing.json from the project's dev_tree_path
#   (read from project.cfg) or falls back to the kanban root's copy.
#   Subscription comparison block values come from the "subscriptions" key.
#   Dollar amounts are never hardcoded in this script.
#
# AUTHORITATIVE COST SOURCE:
#   The Total cost displayed is sourced directly from the aggregator's cost_usd
#   field, which sums total_cost_usd from each tokens.json (the CLI's
#   authoritative per-invocation cost).  No recomputation from token counts
#   is performed at report time.  When legacy-schema tokens.json files are
#   present (pre-v0.24.3 data lacking total_cost_usd), the aggregator falls
#   back to pricing-table computation; a visible WARNING is shown at the top
#   of the report in that case.
#
# GRACEFUL DEGRADATION:
#   - If a model in a roll-up lacks a pricing entry, a warning is emitted to
#     stderr and cost_usd for that entry is treated as 0.
#   - If no data is available for the requested scope, prints a clear message
#     and exits 0.
#
# EXIT CODES:
#   0 -- success (warnings may appear on stderr)
#   1 -- usage error or unrecoverable configuration failure

set -euo pipefail

# ---------------------------------------------------------------------------
# Resolve script location
# ---------------------------------------------------------------------------
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
LIB_DIR="${SCRIPT_DIR}/lib"
PM_AGENT_DIR="${SCRIPT_DIR}/../pm-agent"

# Source project_paths.sh and projects.sh for projects_cfg_list (project name resolution).
# shellcheck source=lib/project_paths.sh
source "${LIB_DIR}/project_paths.sh"
# shellcheck source=lib/projects.sh
source "${LIB_DIR}/projects.sh"

# ---------------------------------------------------------------------------
# Defaults
# ---------------------------------------------------------------------------
SCOPE_TYPE="month"          # month | day | rc | range
SCOPE_VALUE=""              # derived below (default = current month)
PROJECT_NAME="${PGAI_PROJECT_NAME:-}"
CSV_MODE=0
KANBAN_ROOT_OVERRIDE=""

# New date-range flag state (resolved to SCOPE_TYPE=range before Python call)
_SCOPE_FLAG=""              # tracks which scope flag was set (for conflict detection)
_SINCE_DATE=""              # --since YYYY-MM-DD
_UNTIL_DATE=""              # --until YYYY-MM-DD

# ---------------------------------------------------------------------------
# Helper: reject conflicting scope flags
# ---------------------------------------------------------------------------
_set_scope_flag() {
    local flag="$1"
    # --since and --until are allowed to coexist with each other but not with
    # any other scope flag.
    if [[ -n "$_SCOPE_FLAG" ]]; then
        # Allow --since + --until combination
        if [[ ( "$_SCOPE_FLAG" == "--since" && "$flag" == "--until" ) ||
              ( "$_SCOPE_FLAG" == "--until" && "$flag" == "--since" ) ]]; then
            _SCOPE_FLAG="${_SCOPE_FLAG}+${flag}"
            return
        fi
        echo "ERROR: conflicting scope flags: ${_SCOPE_FLAG} and ${flag} cannot be combined." >&2
        echo "Run with --help for usage." >&2
        exit 1
    fi
    _SCOPE_FLAG="$flag"
}

# ---------------------------------------------------------------------------
# Parse arguments
# ---------------------------------------------------------------------------
while [[ $# -gt 0 ]]; do
    case "$1" in
        --month)
            [[ -n "${2:-}" ]] || { echo "ERROR: --month requires YYYY-MM argument" >&2; exit 1; }
            _set_scope_flag "--month"
            SCOPE_TYPE="month"
            SCOPE_VALUE="$2"
            shift 2
            ;;
        --day)
            [[ -n "${2:-}" ]] || { echo "ERROR: --day requires YYYY-MM-DD argument" >&2; exit 1; }
            _set_scope_flag "--day"
            SCOPE_TYPE="day"
            SCOPE_VALUE="$2"
            shift 2
            ;;
        --rc)
            [[ -n "${2:-}" ]] || { echo "ERROR: --rc requires a version argument" >&2; exit 1; }
            _set_scope_flag "--rc"
            SCOPE_TYPE="rc"
            SCOPE_VALUE="$2"
            shift 2
            ;;
        --week)
            _set_scope_flag "--week"
            SCOPE_TYPE="range"
            # Optional argument: YYYY-Www or "current" (or absent)
            if [[ -n "${2:-}" && "${2}" != --* ]]; then
                _WEEK_ARG="$2"
                shift 2
            else
                _WEEK_ARG="current"
                shift
            fi
            ;;
        --year)
            _set_scope_flag "--year"
            SCOPE_TYPE="range"
            # Optional argument: YYYY or "current" (or absent)
            if [[ -n "${2:-}" && "${2}" != --* ]]; then
                _YEAR_ARG="$2"
                shift 2
            else
                _YEAR_ARG="current"
                shift
            fi
            ;;
        --since)
            [[ -n "${2:-}" ]] || { echo "ERROR: --since requires YYYY-MM-DD argument" >&2; exit 1; }
            _set_scope_flag "--since"
            SCOPE_TYPE="range"
            _SINCE_DATE="$2"
            shift 2
            ;;
        --until)
            [[ -n "${2:-}" ]] || { echo "ERROR: --until requires YYYY-MM-DD argument" >&2; exit 1; }
            _set_scope_flag "--until"
            SCOPE_TYPE="range"
            _UNTIL_DATE="$2"
            shift 2
            ;;
        --last|--last-N-days)
            [[ -n "${2:-}" ]] || { echo "ERROR: $1 requires an integer argument" >&2; exit 1; }
            _set_scope_flag "$1"
            SCOPE_TYPE="range"
            _LAST_N="$2"
            shift 2
            ;;
        --project)
            [[ -n "${2:-}" ]] || { echo "ERROR: --project requires a name argument" >&2; exit 1; }
            PROJECT_NAME="$2"
            shift 2
            ;;
        --csv)
            CSV_MODE=1
            shift
            ;;
        --kanban-root)
            [[ -n "${2:-}" ]] || { echo "ERROR: --kanban-root requires a path argument" >&2; exit 1; }
            KANBAN_ROOT_OVERRIDE="$2"
            shift 2
            ;;
        -h|--help)
            sed -n '2,/^set -euo pipefail/{ /^set -euo pipefail/d; s/^# \{0,1\}//; p }' "$0"
            exit 0
            ;;
        *)
            echo "ERROR: unknown argument: $1" >&2
            echo "Run with --help for usage." >&2
            exit 1
            ;;
    esac
done

# ---------------------------------------------------------------------------
# Resolve kanban root
# ---------------------------------------------------------------------------
KANBAN_ROOT="${KANBAN_ROOT_OVERRIDE:-${PGAI_AGENT_KANBAN_ROOT_PATH:-${HOME}/pgai_agent_kanban}}"

if [[ ! -d "$KANBAN_ROOT" ]]; then
    echo "ERROR: kanban root not found: ${KANBAN_ROOT}" >&2
    exit 1
fi

# ---------------------------------------------------------------------------
# Resolve project name — explicit only; no silent default.
# ---------------------------------------------------------------------------
if [[ -z "${PROJECT_NAME:-}" ]]; then
    echo "ERROR: no project specified. Pass --project <name> or set PGAI_PROJECT_NAME." >&2
    echo "Run with --help for usage." >&2
    exit 1
fi

PROJECT_DIR="${KANBAN_ROOT}/projects/${PROJECT_NAME}"
if [[ ! -d "$PROJECT_DIR" ]]; then
    echo "ERROR: project directory not found: ${PROJECT_DIR}" >&2
    exit 1
fi

# ---------------------------------------------------------------------------
# Resolve dev tree path (for pricing file location)
# ---------------------------------------------------------------------------
DEV_TREE_PATH=""
# Prefer project.cfg (lowercase); fall back to PROJECT.cfg for legacy installs.
PROJECT_CFG=""
if [[ -f "${PROJECT_DIR}/project.cfg" ]]; then
    PROJECT_CFG="${PROJECT_DIR}/project.cfg"
elif [[ -f "${PROJECT_DIR}/PROJECT.cfg" ]]; then
    PROJECT_CFG="${PROJECT_DIR}/PROJECT.cfg"
fi
if [[ -n "$PROJECT_CFG" ]]; then
    while IFS='=' read -r key val; do
        key="${key%%#*}"   # strip inline comments
        key="${key// /}"   # trim spaces
        val="${val## }"    # trim leading space
        val="${val%% }"    # trim trailing space
        val="${val#\"}"    # strip surrounding quotes
        val="${val%\"}"
        val="${val#\'}"
        val="${val%\'}"
        if [[ "$key" == "dev_tree_path" && -n "$val" ]]; then
            DEV_TREE_PATH="$val"
            break
        fi
    done < "$PROJECT_CFG"
fi

# ---------------------------------------------------------------------------
# Locate token_pricing.json
# ---------------------------------------------------------------------------
PRICING_REL="team/scripts/lib/token_pricing.json"
PRICING_FILE=""
if [[ -n "$DEV_TREE_PATH" && -f "${DEV_TREE_PATH}/${PRICING_REL}" ]]; then
    PRICING_FILE="${DEV_TREE_PATH}/${PRICING_REL}"
elif [[ -f "${KANBAN_ROOT}/${PRICING_REL}" ]]; then
    PRICING_FILE="${KANBAN_ROOT}/${PRICING_REL}"
fi

if [[ -z "$PRICING_FILE" ]]; then
    echo "WARNING: token_pricing.json not found; subscription comparison unavailable." >&2
fi

# ---------------------------------------------------------------------------
# Locate aggregate_tokens.py
# ---------------------------------------------------------------------------
AGGREGATE_PY=""
if [[ -f "${PM_AGENT_DIR}/aggregate_tokens.py" ]]; then
    AGGREGATE_PY="${PM_AGENT_DIR}/aggregate_tokens.py"
elif [[ -n "$DEV_TREE_PATH" && -f "${DEV_TREE_PATH}/team/pm-agent/aggregate_tokens.py" ]]; then
    AGGREGATE_PY="${DEV_TREE_PATH}/team/pm-agent/aggregate_tokens.py"
fi

# ---------------------------------------------------------------------------
# Default scope value (current month-to-date if nothing specified)
# ---------------------------------------------------------------------------
if [[ -z "$SCOPE_VALUE" && "$SCOPE_TYPE" == "month" && -z "$_SCOPE_FLAG" ]]; then
    SCOPE_VALUE="$(date +%Y-%m)"
fi

# ---------------------------------------------------------------------------
# Resolve date-range scope flags into SCOPE_VALUE="START|END|LABEL"
# ---------------------------------------------------------------------------
if [[ "$SCOPE_TYPE" == "range" ]]; then
    TODAY="$(date +%Y-%m-%d)"

    # -- --week ---------------------------------------------------------------
    if [[ -n "${_WEEK_ARG:-}" ]]; then
        if [[ "$_WEEK_ARG" == "current" ]]; then
            # Monday of current ISO week (date +%u gives 1=Mon..7=Sun)
            DOW=$(date +%u)
            RANGE_START=$(date -d "${TODAY} -$((DOW - 1)) days" +%Y-%m-%d)
            RANGE_END="$TODAY"
            RANGE_LABEL="Week $(date -d "$RANGE_START" +%Y-W%V) (${RANGE_START} through ${RANGE_END})"
        else
            # YYYY-Www format
            _WYEAR="${_WEEK_ARG%-W*}"
            _WN="${_WEEK_ARG##*-W}"
            # ISO week: Jan 4 is always in week 1; compute Monday of week _WN
            # Formula: find Jan 1, get its ISO DOW, then offset.
            _JAN1_DOW=$(date -d "${_WYEAR}-01-01" +%u)  # 1=Mon
            _DAYS_TO_MON=$(( (_WN - 1) * 7 - _JAN1_DOW + 1 ))
            RANGE_START=$(date -d "${_WYEAR}-01-01 +${_DAYS_TO_MON} days" +%Y-%m-%d)
            RANGE_END=$(date -d "${RANGE_START} +6 days" +%Y-%m-%d)
            # Cap end at today if in current week
            if [[ "$RANGE_END" > "$TODAY" ]]; then
                RANGE_END="$TODAY"
            fi
            RANGE_LABEL="Week ${_WEEK_ARG} (${RANGE_START} through ${RANGE_END})"
        fi

    # -- --year ---------------------------------------------------------------
    elif [[ -n "${_YEAR_ARG:-}" ]]; then
        if [[ "$_YEAR_ARG" == "current" ]]; then
            _YR="$(date +%Y)"
            RANGE_START="${_YR}-01-01"
            RANGE_END="$TODAY"
            RANGE_LABEL="Year ${_YR} YTD (${RANGE_START} through ${RANGE_END})"
        else
            _YR="$_YEAR_ARG"
            RANGE_START="${_YR}-01-01"
            RANGE_END="${_YR}-12-31"
            # Cap end at today if within current year
            if [[ "$RANGE_END" > "$TODAY" ]]; then
                RANGE_END="$TODAY"
            fi
            RANGE_LABEL="Year ${_YR} (${RANGE_START} through ${RANGE_END})"
        fi

    # -- --last N -------------------------------------------------------------
    elif [[ -n "${_LAST_N:-}" ]]; then
        if ! [[ "$_LAST_N" =~ ^[0-9]+$ ]]; then
            echo "ERROR: --last requires a positive integer argument (got '${_LAST_N}')" >&2
            exit 1
        fi
        if [[ "$_LAST_N" -lt 1 ]]; then
            echo "ERROR: --last requires N >= 1 (got ${_LAST_N}). Use --day today for a single day." >&2
            exit 1
        fi
        RANGE_START=$(date -d "${TODAY} -$((_LAST_N - 1)) days" +%Y-%m-%d)
        RANGE_END="$TODAY"
        RANGE_LABEL="Last ${_LAST_N} days (${RANGE_START} through ${RANGE_END})"

    # -- --since / --until (one or both) -------------------------------------
    else
        # At least one of _SINCE_DATE or _UNTIL_DATE must be set to reach here
        if [[ -n "$_SINCE_DATE" && -n "$_UNTIL_DATE" ]]; then
            RANGE_START="$_SINCE_DATE"
            RANGE_END="$_UNTIL_DATE"
            RANGE_LABEL="Range ${RANGE_START} through ${RANGE_END}"
        elif [[ -n "$_SINCE_DATE" ]]; then
            RANGE_START="$_SINCE_DATE"
            RANGE_END="$TODAY"
            RANGE_LABEL="Since ${RANGE_START} through ${RANGE_END}"
        else
            # --until only: start is sentinel "earliest" resolved by Python
            RANGE_START="earliest"
            RANGE_END="$_UNTIL_DATE"
            RANGE_LABEL="Through ${RANGE_END}"
        fi
    fi

    # Encode range into SCOPE_VALUE as START|END|LABEL (pipe-delimited)
    # Label uses | as separator-safe char since dates and labels have no pipes.
    SCOPE_VALUE="${RANGE_START}|${RANGE_END}|${RANGE_LABEL}"
fi

# ---------------------------------------------------------------------------
# Normalise scope: ensure RC starts with 'v'
# ---------------------------------------------------------------------------
if [[ "$SCOPE_TYPE" == "rc" ]]; then
    if [[ "${SCOPE_VALUE:0:1}" != "v" ]]; then
        SCOPE_VALUE="v${SCOPE_VALUE}"
    fi
fi

# ---------------------------------------------------------------------------
# Paths to roll-up files
# ---------------------------------------------------------------------------
USAGE_RC_DIR="${PROJECT_DIR}/usage/rc"
USAGE_DAILY_DIR="${PROJECT_DIR}/usage/daily"

# ---------------------------------------------------------------------------
# Helper: invoke aggregator to build a missing roll-up
# ---------------------------------------------------------------------------
_invoke_aggregator() {
    local rollup_type="$1"   # "rc" or "day"
    local rollup_val="$2"

    if [[ -z "$AGGREGATE_PY" ]]; then
        echo "WARNING: aggregate_tokens.py not found; cannot auto-aggregate." >&2
        return 1
    fi

    local extra_flag
    if [[ "$rollup_type" == "rc" ]]; then
        extra_flag="--rc"
    else
        extra_flag="--day"
    fi

    echo "[cost-report] Roll-up missing; invoking aggregator for ${rollup_type}=${rollup_val} ..." >&2
    python3 "$AGGREGATE_PY" \
        --project "$PROJECT_NAME" \
        "$extra_flag" "$rollup_val" \
        ${KANBAN_ROOT_OVERRIDE:+--kanban-root "$KANBAN_ROOT_OVERRIDE"} \
        >&2 || true
}

# ---------------------------------------------------------------------------
# The report engine is implemented in Python for reliable float arithmetic.
# We build the Python program as a heredoc and run it, passing all runtime
# variables as arguments.
# ---------------------------------------------------------------------------

python3 - \
    "$SCOPE_TYPE" \
    "$SCOPE_VALUE" \
    "$PROJECT_NAME" \
    "$USAGE_RC_DIR" \
    "$USAGE_DAILY_DIR" \
    "${PRICING_FILE:-}" \
    "${AGGREGATE_PY:-}" \
    "$CSV_MODE" \
    "$KANBAN_ROOT_OVERRIDE" \
    "$KANBAN_ROOT" \
<<'PY_EOF'
"""
cost-report.sh inline Python engine.

sys.argv layout (all strings):
  1  scope_type       "month" | "day" | "rc" | "range"
  2  scope_value      e.g. "2026-05", "2026-05-16", "v0.23.22",
                      or for range: "START|END|LABEL" where START is
                      "YYYY-MM-DD" or "earliest", END is "YYYY-MM-DD".
  3  project_name     e.g. "my-project"
  4  usage_rc_dir     absolute path to usage/rc/
  5  usage_daily_dir  absolute path to usage/daily/
  6  pricing_file     absolute path to token_pricing.json (or empty)
  7  aggregate_py     absolute path to aggregate_tokens.py (or empty)
  8  csv_mode         "1" for CSV, "0" for human-readable
  9  kanban_root_override  empty or explicit override
  10 kanban_root      resolved kanban root
"""

from __future__ import annotations

import json
import os
import pathlib
import subprocess
import sys
from datetime import date, datetime, timezone
from typing import Any


# ---------------------------------------------------------------------------
# Args
# ---------------------------------------------------------------------------
scope_type        = sys.argv[1]
scope_value       = sys.argv[2]
project_name      = sys.argv[3]
usage_rc_dir      = pathlib.Path(sys.argv[4])
usage_daily_dir   = pathlib.Path(sys.argv[5])
pricing_file_str  = sys.argv[6]
aggregate_py      = sys.argv[7]
csv_mode          = sys.argv[8] == "1"
kanban_root_over  = sys.argv[9]
kanban_root       = pathlib.Path(sys.argv[10])


# ---------------------------------------------------------------------------
# Pricing helpers
# ---------------------------------------------------------------------------

_pricing_warned: set[str] = set()


def load_pricing() -> dict[str, Any]:
    if not pricing_file_str:
        return {}
    p = pathlib.Path(pricing_file_str)
    if not p.is_file():
        return {}
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError) as exc:
        print(f"WARNING: malformed token_pricing.json: {exc}", file=sys.stderr)
        return {}


def model_cost(pricing: dict[str, Any], model: str, provider: str,
               input_tok: int, output_tok: int,
               cache_create: int, cache_read: int) -> float:
    """Return cost_usd given pricing table and token counts.
    Emits a single stderr warning per unknown model and returns 0.0."""
    if not pricing:
        return 0.0
    providers_block = pricing.get("providers", {})
    model_rates: dict[str, Any] | None = None

    pb = providers_block.get(provider, {})
    model_rates = pb.get("models", {}).get(model)
    if model_rates is None:
        for _pb in providers_block.values():
            cand = _pb.get("models", {}).get(model)
            if cand is not None:
                model_rates = cand
                break

    if model_rates is None:
        key = f"{provider}/{model}"
        if key not in _pricing_warned:
            print(
                f"WARNING: model '{model}' not found in token_pricing.json;"
                " cost_usd=0 for this entry.",
                file=sys.stderr,
            )
            _pricing_warned.add(key)
        return 0.0

    return round(
        input_tok    * float(model_rates.get("input_per_1m",            0)) / 1_000_000
        + output_tok * float(model_rates.get("output_per_1m",           0)) / 1_000_000
        + cache_create * float(model_rates.get("cache_creation_per_1m", 0)) / 1_000_000
        + cache_read   * float(model_rates.get("cache_read_per_1m",     0)) / 1_000_000,
        6,
    )


def cache_read_savings(pricing: dict[str, Any], model: str, provider: str,
                       cache_read: int) -> float:
    """Return how much cache reads saved vs. paying full input price."""
    if not pricing or cache_read == 0:
        return 0.0
    providers_block = pricing.get("providers", {})
    pb = providers_block.get(provider, {})
    model_rates = pb.get("models", {}).get(model)
    if model_rates is None:
        for _pb in providers_block.values():
            cand = _pb.get("models", {}).get(model)
            if cand is not None:
                model_rates = cand
                break
    if model_rates is None:
        return 0.0
    full_input_price  = float(model_rates.get("input_per_1m", 0))
    cache_read_price  = float(model_rates.get("cache_read_per_1m", 0))
    saved_per_1m = full_input_price - cache_read_price
    return round(cache_read * saved_per_1m / 1_000_000, 6)


# ---------------------------------------------------------------------------
# Aggregator invocation helper
# ---------------------------------------------------------------------------

def try_aggregate(rollup_type: str, rollup_val: str) -> None:
    """Invoke aggregate_tokens.py to build a missing roll-up, best-effort."""
    if not aggregate_py:
        return
    flag = "--rc" if rollup_type == "rc" else "--day"
    cmd = [sys.executable, aggregate_py, "--project", project_name, flag, rollup_val]
    if kanban_root_over:
        cmd += ["--kanban-root", kanban_root_over]
    try:
        subprocess.run(cmd, check=False, capture_output=False)
    except OSError:
        pass


# ---------------------------------------------------------------------------
# Load roll-up data
# ---------------------------------------------------------------------------

class RollupRecord:
    """A normalised in-memory view of one or more roll-up JSON files."""

    def __init__(self) -> None:
        self.total_input:             int   = 0
        self.total_output:            int   = 0
        self.total_cache_create:      int   = 0
        self.total_cache_read:        int   = 0
        self.total_cost:              float = 0.0
        # Per-category cost breakdowns (populated from aggregator output fields)
        self.total_input_cost:        float = 0.0
        self.total_cache_create_cost: float = 0.0
        self.total_cache_read_cost:   float = 0.0
        self.total_output_cost:       float = 0.0
        self.total_invocations:       int   = 0
        self.by_agent:                dict[str, dict[str, Any]] = {}
        self.by_model:                dict[str, dict[str, Any]] = {}
        self.days_with_activity:      int   = 0
        self.rcs_shipped:             int   = 0
        self.cache_read_savings:      float = 0.0
        self.days_observed:           int   = 0     # days with non-zero cost (for avg)
        self.scope_label:             str   = ""    # human-readable scope description
        self.daily_costs:             list[float] = []  # per-day cost list (for projections)
        # True when at least one absorbed file used legacy pricing-table fallback
        # (i.e., per-category costs are non-zero, meaning the aggregator computed
        # cost from token counts rather than from total_cost_usd).
        self.has_legacy_data:         bool  = False

    def absorb_rc_file(self, data: dict[str, Any], pricing: dict[str, Any]) -> None:
        """Absorb an RC-level roll-up file."""
        tasks = data.get("tasks", [])
        for task in tasks:
            i   = int(task.get("input", 0) or 0)
            o   = int(task.get("output", 0) or 0)
            cc  = int(task.get("cache_creation_tokens", 0) or 0)
            cr  = int(task.get("cache_read_tokens", 0) or 0)
            inv = int(task.get("invocations", 1) or 1)
            ag  = str(task.get("agent", "unknown")).lower()
            mdl = str(task.get("model", "unknown"))

            # Use stored total cost from aggregator (it had access to per-task tokens.json
            # and per-category rates at aggregation time)
            cost = float(task.get("cost_usd", 0) or 0)

            # Per-category costs — populated by updated aggregator; default to 0
            # for legacy roll-up files that predate the category-cost fields.
            i_cost  = float(task.get("input_cost_usd",          0) or 0)
            cc_cost = float(task.get("cache_creation_cost_usd",  0) or 0)
            cr_cost = float(task.get("cache_read_cost_usd",      0) or 0)
            o_cost  = float(task.get("output_cost_usd",          0) or 0)
            # Detect legacy-schema data: per-category costs are only populated
            # by the aggregator when it used pricing-table fallback (legacy schema).
            cat_sum = i_cost + cc_cost + cr_cost + o_cost
            if cat_sum > 0:
                self.has_legacy_data = True

            self.total_input             += i
            self.total_output            += o
            self.total_cache_create      += cc
            self.total_cache_read        += cr
            self.total_input_cost        += i_cost
            self.total_cache_create_cost += cc_cost
            self.total_cache_read_cost   += cr_cost
            self.total_output_cost       += o_cost
            self.total_cost              += cost
            self.total_invocations       += inv

            if ag not in self.by_agent:
                self.by_agent[ag] = {"input": 0, "output": 0, "cost_usd": 0.0, "invocations": 0}
            self.by_agent[ag]["input"]       += i
            self.by_agent[ag]["output"]      += o
            self.by_agent[ag]["cost_usd"]    += cost
            self.by_agent[ag]["invocations"] += inv

            if mdl not in self.by_model:
                self.by_model[mdl] = {"input": 0, "output": 0, "cost_usd": 0.0, "invocations": 0}
            self.by_model[mdl]["input"]       += i
            self.by_model[mdl]["output"]      += o
            self.by_model[mdl]["cost_usd"]    += cost
            self.by_model[mdl]["invocations"] += inv

        self.rcs_shipped += 1

    def absorb_day_file(self, data: dict[str, Any], pricing: dict[str, Any]) -> None:
        """Absorb a daily roll-up file."""
        totals = data.get("totals", {})
        i    = int(totals.get("input_tokens",  0) or 0)
        o    = int(totals.get("output_tokens", 0) or 0)
        cc   = int(totals.get("cache_creation_tokens", 0) or 0)
        cr   = int(totals.get("cache_read_tokens",     0) or 0)
        cost = float(totals.get("cost_usd",   0) or 0)
        inv  = int(totals.get("invocations",  0) or 0)

        # Per-category costs — populated by updated aggregator; default 0 for
        # legacy daily files that predate the category-cost fields.
        i_cost  = float(totals.get("input_cost_usd",          0) or 0)
        cc_cost = float(totals.get("cache_creation_cost_usd",  0) or 0)
        cr_cost = float(totals.get("cache_read_cost_usd",      0) or 0)
        o_cost  = float(totals.get("output_cost_usd",          0) or 0)
        # Detect legacy-schema data: per-category costs are only populated
        # by the aggregator when it used pricing-table fallback (legacy schema).
        cat_sum = i_cost + cc_cost + cr_cost + o_cost
        if cat_sum > 0:
            self.has_legacy_data = True

        self.total_input             += i
        self.total_output            += o
        self.total_cache_create      += cc
        self.total_cache_read        += cr
        self.total_input_cost        += i_cost
        self.total_cache_create_cost += cc_cost
        self.total_cache_read_cost   += cr_cost
        self.total_output_cost       += o_cost
        self.total_cost              += cost
        self.total_invocations       += inv

        if i > 0 or o > 0 or cc > 0 or cr > 0 or cost > 0:
            self.days_with_activity += 1
            self.daily_costs.append(cost)

        for ag, aval in data.get("by_agent", {}).items():
            ai   = int(aval.get("input",       0) or 0)
            ao   = int(aval.get("output",      0) or 0)
            ac   = float(aval.get("cost_usd",  0) or 0)
            ainv = int(aval.get("invocations", 0) or 0)

            ag = ag.lower()
            if ag not in self.by_agent:
                self.by_agent[ag] = {"input": 0, "output": 0, "cost_usd": 0.0, "invocations": 0}
            self.by_agent[ag]["input"]       += ai
            self.by_agent[ag]["output"]      += ao
            self.by_agent[ag]["cost_usd"]    += ac
            self.by_agent[ag]["invocations"] += ainv

        for mdl, mval in data.get("by_model", {}).items():
            mi   = int(mval.get("input",       0) or 0)
            mo   = int(mval.get("output",      0) or 0)
            mc   = float(mval.get("cost_usd",  0) or 0)
            minv = int(mval.get("invocations", 0) or 0)

            if mdl not in self.by_model:
                self.by_model[mdl] = {"input": 0, "output": 0, "cost_usd": 0.0, "invocations": 0}
            self.by_model[mdl]["input"]       += mi
            self.by_model[mdl]["output"]      += mo
            self.by_model[mdl]["cost_usd"]    += mc
            self.by_model[mdl]["invocations"] += minv

        for rc in data.get("rcs_shipped", []):
            if rc:
                self.rcs_shipped += 1


def _load_json_file(path: pathlib.Path) -> dict[str, Any] | None:
    if not path.is_file():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(data, dict):
            raise ValueError("not a JSON object")
        return data
    except (json.JSONDecodeError, ValueError, OSError) as exc:
        print(f"WARNING: cannot read {path}: {exc}", file=sys.stderr)
        return None


# ---------------------------------------------------------------------------
# Build the RollupRecord for the requested scope
# ---------------------------------------------------------------------------

pricing = load_pricing()

rec = RollupRecord()

def _no_data(scope_label: str) -> None:
    print(f"no data for {scope_label}", file=sys.stdout)
    sys.exit(0)

# ---- RC scope ---------------------------------------------------------------
if scope_type == "rc":
    rc_file = usage_rc_dir / f"{scope_value}-tokens.json"
    if not rc_file.is_file():
        try_aggregate("rc", scope_value)
    data = _load_json_file(rc_file)
    if data is None:
        _no_data(f"rc={scope_value}")
    totals = data.get("totals", {})
    if not data.get("tasks") and int(totals.get("invocations", 0)) == 0:
        _no_data(f"rc={scope_value}")
    rec.absorb_rc_file(data, pricing)
    rec.scope_label = f"RC {scope_value}"

# ---- Day scope --------------------------------------------------------------
elif scope_type == "day":
    day_file = usage_daily_dir / f"{scope_value}.json"
    if not day_file.is_file():
        try_aggregate("day", scope_value)
    data = _load_json_file(day_file)
    if data is None:
        _no_data(f"day={scope_value}")
    if int(data.get("totals", {}).get("invocations", 0)) == 0 and \
       float(data.get("totals", {}).get("cost_usd", 0)) == 0:
        _no_data(f"day={scope_value}")
    rec.absorb_day_file(data, pricing)
    rec.scope_label = f"Day {scope_value}"

# ---- Month scope ------------------------------------------------------------
elif scope_type == "month":
    # Iterate over all daily files matching YYYY-MM-*.json
    month_prefix = scope_value  # e.g. "2026-05"
    found_any = False

    # Also check RC files that shipped during this month by reading their shipped_at
    # (day files are simpler — iterate by filename prefix)
    usage_daily_dir.mkdir(parents=True, exist_ok=True)

    day_files_for_month: list[pathlib.Path] = sorted(
        p for p in usage_daily_dir.iterdir()
        if p.name.startswith(month_prefix) and p.name.endswith(".json")
    )

    if not day_files_for_month:
        # Try to aggregate all available days (best-effort)
        if aggregate_py:
            cmd = [
                sys.executable, aggregate_py,
                "--project", project_name,
                "--all",
            ]
            if kanban_root_over:
                cmd += ["--kanban-root", kanban_root_over]
            try:
                subprocess.run(cmd, check=False, capture_output=False)
            except OSError:
                pass
        day_files_for_month = sorted(
            p for p in usage_daily_dir.iterdir()
            if p.name.startswith(month_prefix) and p.name.endswith(".json")
        )

    # Determine how many days into the month we have observed (for projection)
    today = date.today()
    month_year, month_m = int(month_prefix[:4]), int(month_prefix[5:7])
    if today.year == month_year and today.month == month_m:
        # Current month: days elapsed so far
        days_in_scope = today.day
        month_label = today.strftime("%B %Y")
    else:
        # Past month: full month
        import calendar
        days_in_scope = calendar.monthrange(month_year, month_m)[1]
        month_label = f"{month_prefix[:4]}-{month_prefix[5:7]}"

    rec.scope_label = f"{month_label}"
    rec.days_observed = days_in_scope

    for day_file in day_files_for_month:
        data = _load_json_file(day_file)
        if data is None:
            continue
        rec.absorb_day_file(data, pricing)
        found_any = True

    # Count distinct RCs shipped this month by scanning RC dir
    rc_seen: set[str] = set()
    if usage_rc_dir.is_dir():
        for rc_file in usage_rc_dir.iterdir():
            if not rc_file.name.endswith("-tokens.json"):
                continue
            rd = _load_json_file(rc_file)
            if rd is None:
                continue
            shipped_at = rd.get("shipped_at", "")
            if shipped_at.startswith(month_prefix):
                rc_seen.add(rd.get("version", ""))
    rec.rcs_shipped = len(rc_seen)

    if not found_any:
        _no_data(f"month={scope_value}")

# ---- Range scope (week / year / since / until / last N) ------------------
elif scope_type == "range":
    # scope_value = "START|END|LABEL"
    parts = scope_value.split("|", 2)
    if len(parts) != 3:
        print(f"ERROR: malformed range scope_value: {scope_value!r}", file=sys.stderr)
        sys.exit(1)
    range_start_str, range_end_str, range_label = parts

    usage_daily_dir.mkdir(parents=True, exist_ok=True)

    # Gather all daily files in the directory, sorted ascending by name.
    all_day_files: list[pathlib.Path] = sorted(
        p for p in usage_daily_dir.iterdir()
        if p.suffix == ".json" and len(p.stem) == 10
        # stems are YYYY-MM-DD — 10 chars; simple filter avoids non-date files
    )

    # Determine effective start/end
    if range_start_str == "earliest":
        # --until only: use earliest available file date as start
        if all_day_files:
            range_start_str = all_day_files[0].stem
        else:
            _no_data(f"range (no day-rollup files found)")

    # Filter files within [range_start_str, range_end_str] using string comparison
    # (YYYY-MM-DD lexicographic order matches chronological order)
    range_day_files: list[pathlib.Path] = [
        p for p in all_day_files
        if range_start_str <= p.stem <= range_end_str
    ]

    found_any = False
    for day_file in range_day_files:
        data = _load_json_file(day_file)
        if data is None:
            continue
        rec.absorb_day_file(data, pricing)
        found_any = True

    if not found_any:
        _no_data(f"range ({range_start_str} through {range_end_str})")

    rec.scope_label = range_label
    # Count days in calendar range (for display; some may have no data)
    try:
        from datetime import timedelta
        _d_start = date.fromisoformat(range_start_str)
        _d_end   = date.fromisoformat(range_end_str)
        rec.days_observed = (_d_end - _d_start).days + 1
    except (ValueError, AttributeError):
        rec.days_observed = 0

else:
    print(f"ERROR: unknown scope_type '{scope_type}'", file=sys.stderr)
    sys.exit(1)


# ---------------------------------------------------------------------------
# Format numbers
# ---------------------------------------------------------------------------

def fmt_tokens(n: int) -> str:
    """Format token count with commas, e.g. 4250000 -> '4,250,000'."""
    return f"{n:,}"


def fmt_usd(v: float) -> str:
    """Format dollar amount, e.g. 577.50 -> '$577.50'."""
    if v >= 1000:
        return f"${v:,.0f}"
    return f"${v:.2f}"


def fmt_pct(part: float, total: float) -> str:
    if total == 0:
        return " 0%"
    pct = part / total * 100
    return f"{pct:.0f}%"


# ---------------------------------------------------------------------------
# Subscription comparison data
# ---------------------------------------------------------------------------

def get_subscriptions(pricing: dict[str, Any]) -> dict[str, float]:
    return pricing.get("subscriptions", {})


# ---------------------------------------------------------------------------
# Projection helper
# ---------------------------------------------------------------------------

def projected_monthly(rec: RollupRecord, cost_total: float | None = None) -> float | None:
    """Linear extrapolation: (cost_total / days_with_activity) * 30.

    cost_total defaults to rec.total_cost if not provided; callers may pass
    display_total (the sum of per-category costs) for a more accurate projection.
    """
    if scope_type != "month":
        return None
    if rec.days_with_activity == 0:
        return None
    total = cost_total if cost_total is not None else rec.total_cost
    daily = total / rec.days_with_activity
    return daily * 30


# ---------------------------------------------------------------------------
# CSV output
# ---------------------------------------------------------------------------

if csv_mode:
    import csv
    import io

    buf = io.StringIO()
    writer = csv.writer(buf, lineterminator="\n")

    # Header — agent columns first (stable), model columns appended at end
    writer.writerow([
        "scope_type", "scope_value", "project",
        "total_input_tokens", "total_output_tokens",
        "total_cache_create_tokens", "total_cache_read_tokens",
        "total_cost_usd", "total_invocations",
        "days_with_activity", "rcs_shipped",
        "agent", "agent_input_tokens", "agent_output_tokens",
        "agent_cost_usd", "agent_invocations",
        "model", "model_input_tokens", "model_output_tokens",
        "model_cost_usd", "model_invocations",
    ])

    # Summary row (agent="ALL", model="ALL")
    writer.writerow([
        scope_type, scope_value, project_name,
        rec.total_input, rec.total_output,
        rec.total_cache_create, rec.total_cache_read,
        round(rec.total_cost, 6), rec.total_invocations,
        rec.days_with_activity, rec.rcs_shipped,
        "ALL", rec.total_input, rec.total_output,
        round(rec.total_cost, 6), rec.total_invocations,
        "ALL", rec.total_input, rec.total_output,
        round(rec.total_cost, 6), rec.total_invocations,
    ])

    # Per-agent rows (model columns blank — agent rows are agent-level aggregations)
    for ag in sorted(rec.by_agent.keys()):
        av = rec.by_agent[ag]
        writer.writerow([
            scope_type, scope_value, project_name,
            rec.total_input, rec.total_output,
            rec.total_cache_create, rec.total_cache_read,
            round(rec.total_cost, 6), rec.total_invocations,
            rec.days_with_activity, rec.rcs_shipped,
            ag,
            av["input"], av["output"],
            round(av["cost_usd"], 6), av["invocations"],
            "", "", "", "", "",
        ])

    # Per-model rows (agent columns blank — model rows are model-level aggregations)
    for mdl in sorted(rec.by_model.keys()):
        mv = rec.by_model[mdl]
        writer.writerow([
            scope_type, scope_value, project_name,
            rec.total_input, rec.total_output,
            rec.total_cache_create, rec.total_cache_read,
            round(rec.total_cost, 6), rec.total_invocations,
            rec.days_with_activity, rec.rcs_shipped,
            "", "", "", "", "",
            mdl,
            mv["input"], mv["output"],
            round(mv["cost_usd"], 6), mv["invocations"],
        ])

    print(buf.getvalue(), end="")
    sys.exit(0)


# ---------------------------------------------------------------------------
# Human-readable output
# ---------------------------------------------------------------------------

subs = get_subscriptions(pricing)

# Determine whether per-category cost data is available from the aggregator.
# This flag is used for token totals display (showing cost per category).
have_category_costs = (
    rec.total_input_cost != 0
    or rec.total_cache_create_cost != 0
    or rec.total_cache_read_cost != 0
    or rec.total_output_cost != 0
)

# Canonical cost to display: always use rec.total_cost (the aggregator's
# authoritative sum of total_cost_usd / cost_usd fields).  This avoids
# recomputation drift and is correct for both new-schema and legacy-schema data.
display_total = rec.total_cost

proj_monthly = projected_monthly(rec, display_total)
daily_avg = display_total / max(rec.days_with_activity, 1) if scope_type == "month" else None

# Compute per-day invocation averages for by-agent section
if scope_type in ("month", "range") and rec.days_with_activity > 0:
    days_denom = rec.days_with_activity
elif scope_type == "day":
    days_denom = 1
else:
    days_denom = None

lines: list[str] = []

# --- Fallback warning (prepended before header when legacy data present) ---
# Visible single warning when the aggregator used pricing-table fallback for
# some or all tokens.json files (legacy schema, pre-v0.24.3 captures).
# New-schema files use the CLI's authoritative total_cost_usd; legacy files
# use token_pricing.json which may under-report due to multi-model usage.
if rec.has_legacy_data:
    lines.append("*** WARNING: some or all costs in this report were computed from the")
    lines.append("*** pricing table (legacy tokens.json schema, pre-v0.24.3).  Total")
    lines.append("*** cost may be under-reported.  Upgrade token_capture.sh to fix.")
    lines.append("")

# --- Header ---
scope_display = rec.scope_label
lines.append(f"=== Token Usage: {scope_display} — {project_name} ===")

if scope_type == "month":
    days_elapsed = rec.days_observed if rec.days_observed else "?"
    lines.append(f"Days with activity: {rec.days_with_activity} / {days_elapsed}")
    lines.append(f"RCs shipped:        {rec.rcs_shipped}")
elif scope_type == "range":
    days_elapsed = rec.days_observed if rec.days_observed else "?"
    lines.append(f"Days with activity: {rec.days_with_activity} / {days_elapsed}")
elif scope_type == "day":
    lines.append(f"RCs on this day:    {rec.rcs_shipped}")
elif scope_type == "rc":
    lines.append(f"RC:                 {scope_value}")

lines.append(f"Total invocations:  {rec.total_invocations:,}")
lines.append("")

# --- Token totals (four-category breakdown) ---
# Display all four Anthropic billing categories: Input (new), Cache writes,
# Cache reads, and Output — each with token count and dollar cost.
# Categories are always shown even when zero (constraint from task spec).
lines.append("Token totals:")

# have_category_costs and display_total are computed above (before daily_avg).

def _tok_cost_line(label: str, tokens: int, cost: float) -> str:
    """Format a single token-category line: label, token count, cost."""
    tok_str  = fmt_tokens(tokens)
    cost_str = fmt_usd(cost)
    # Align columns: label padded to 16 chars, token count right-justified in 15 chars
    return f"  {label:<16} {tok_str:>15} tokens    {cost_str}"

if have_category_costs:
    lines.append(_tok_cost_line("Input (new):",   rec.total_input,        rec.total_input_cost))
    lines.append(_tok_cost_line("Cache writes:",  rec.total_cache_create, rec.total_cache_create_cost))
    lines.append(_tok_cost_line("Cache reads:",   rec.total_cache_read,   rec.total_cache_read_cost))
    lines.append(_tok_cost_line("Output:",        rec.total_output,       rec.total_output_cost))
else:
    # Legacy roll-up: no per-category cost data — show token counts without costs
    lines.append(f"  Input (new):              {fmt_tokens(rec.total_input):>15} tokens")
    lines.append(f"  Cache writes:             {fmt_tokens(rec.total_cache_create):>15} tokens")
    lines.append(f"  Cache reads:              {fmt_tokens(rec.total_cache_read):>15} tokens")
    lines.append(f"  Output:                   {fmt_tokens(rec.total_output):>15} tokens")

lines.append("")

# --- By agent ---
if rec.by_agent:
    lines.append("By agent (cost share):")
    sorted_agents = sorted(rec.by_agent.items(), key=lambda kv: kv[1]["cost_usd"], reverse=True)
    for ag, av in sorted_agents:
        pct = fmt_pct(av["cost_usd"], display_total)
        ag_label = ag.upper()
        cost_str = fmt_usd(av["cost_usd"])
        if days_denom is not None and days_denom > 0:
            avg_inv = av["invocations"] / days_denom
            lines.append(f"  {ag_label:<7} {cost_str} ({pct})  — {avg_inv:.0f} invocations/day avg")
        else:
            lines.append(f"  {ag_label:<7} {cost_str} ({pct})  — {av['invocations']} invocations")
    lines.append("")

# --- By model ---
if rec.by_model:
    lines.append("By model:")
    sorted_models = sorted(rec.by_model.items(), key=lambda kv: kv[1]["cost_usd"], reverse=True)
    for mdl, mv in sorted_models:
        pct = fmt_pct(mv["cost_usd"], display_total)
        cost_str = fmt_usd(mv["cost_usd"])
        lines.append(f"  {mdl:<28} {cost_str} ({pct})")
    lines.append("")

# --- Total cost ---
# display_total is the sum of all four category costs when available, else
# the aggregated cost_usd total. Computed above (before daily_avg) so that
# daily_avg and proj_monthly also use the canonical cost figure.
lines.append(f"Total cost:         {fmt_usd(display_total)}")

if daily_avg is not None:
    lines.append(f"Daily average:      {fmt_usd(daily_avg)}")

if proj_monthly is not None:
    lines.append(f"Projected monthly:  {fmt_usd(proj_monthly)} (extrapolated from observed days)")

lines.append("")

# --- Subscription comparison ---
if subs:
    lines.append("Subscription comparison:")

    pro_credit  = float(subs.get("claude_pro_programmatic_credit", 0))
    max_credit  = float(subs.get("claude_max_programmatic_credit", 0))
    plus_credit = float(subs.get("chatgpt_plus", 0))
    pro2_credit = float(subs.get("chatgpt_pro", 0))

    # Use projected monthly for comparison when available, else display_total
    compare_cost = proj_monthly if proj_monthly is not None else display_total

    def _shortfall(credit: float, cost: float) -> str:
        diff = credit - cost
        if diff >= 0:
            return f"${diff:.0f} surplus"
        return f"${abs(diff):,.0f} short"

    lines.append(f"  Anthropic Pro programmatic credit ({fmt_usd(pro_credit)}):   {_shortfall(pro_credit, compare_cost)}")
    lines.append(f"  Anthropic Max programmatic credit ({fmt_usd(max_credit)}):  {_shortfall(max_credit, compare_cost)}")
    api_str = fmt_usd(compare_cost)
    lines.append(f"  Anthropic API direct (pay-as-you-go):           {api_str}")

    plus_str = fmt_usd(plus_credit)
    lines.append(f"  Codex via ChatGPT Plus ({plus_str}):              {_shortfall(plus_credit, compare_cost)}")
    pro2_str = fmt_usd(pro2_credit)
    lines.append(f"  Codex via ChatGPT Pro ({pro2_str}):               {_shortfall(pro2_credit, compare_cost)}")

    lines.append("")

    # Cheapest-if-mixed: Max subscription + API overflow
    overflow = max(0, compare_cost - max_credit)
    mixed_cost = max_credit + overflow  # subscription cost + overflow at API rates
    # Actually: you pay $200/month subscription; beyond $200 in programmatic credits you pay API
    # So mixed_cost is: subscription_price ($200) + API charges above the credit
    # But we don't know the subscription dollar amount, only the credit amount.
    # Treat credit = subscription cost (simplification; the credit IS the value you get).
    # Pure API cost = compare_cost; mixed = max(compare_cost, max_credit) since at $200 sub
    # you cover up to $200 of usage; beyond that you pay the excess at API rates.
    # Savings vs pure API = min(max_credit, compare_cost)
    savings_vs_api = min(max_credit, compare_cost)
    lines.append("Cheapest if mixed:")
    mixed_display = fmt_usd(max(0, compare_cost - savings_vs_api) + max_credit)
    # Simpler: mixed total = max(compare_cost, max_credit) is wrong.
    # Correct: you pay $200 for subscription, get $200 of credits.
    # If usage < $200: you pay $200 for subscription (wasted credits).
    # If usage > $200: you pay $200 + (usage - $200) = usage (no savings vs API!).
    # The real savings model: subscription is prepaid; credits reduce the overage.
    # Show savings as: if compare_cost > max_credit, savings = max_credit (you avoided $200 of API cost).
    # If compare_cost <= max_credit, savings = compare_cost (covered entirely by subscription).
    savings = min(max_credit, compare_cost)
    # Cost to operator: subscription price ($200) + API overflow
    operator_cost = max_credit + max(0, compare_cost - max_credit)
    savings_vs_pure_api = compare_cost - operator_cost if compare_cost > max_credit else compare_cost - max_credit
    if savings_vs_pure_api < 0:
        # Sub costs more than API when usage is low
        lines.append(
            f"  Max subscription + API overflow: {fmt_usd(operator_cost)}"
            f" (costs ${abs(savings_vs_pure_api):.0f} MORE vs. pure API at low usage)"
        )
    else:
        lines.append(
            f"  Max subscription + API overflow: {fmt_usd(operator_cost)}"
            f" (saves {fmt_usd(savings_vs_pure_api)} vs. pure API)"
        )

if not csv_mode:
    # Append a footer pointing operators to the new metrics surface.
    # This keeps backward compatibility (all legacy fields preserved above)
    # while surfacing the richer per-RC / streaming / JSONL interface.
    lines.append("")
    lines.append("--- New metrics surface (per-RC rollup + streaming) ---")
    lines.append("  metrics-report.sh --rc <version>   per-RC JSON rollup")
    lines.append("  metrics-report.sh --csv             cumulative RC history CSV")
    lines.append("  metrics-report.sh --format jsonl    JSON Lines for streaming")
    lines.append("  metrics-report.sh --tail            live-tail RC close events")
    lines.append("  metrics-report.sh --help            full usage")

print("\n".join(lines))
sys.exit(0)
PY_EOF
