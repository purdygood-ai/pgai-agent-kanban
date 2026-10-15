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
#   present (legacy-schema data lacking total_cost_usd), the aggregator falls
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
# shellcheck source=lib/env_bootstrap.sh
source "$(dirname "${BASH_SOURCE[0]}")/lib/env_bootstrap.sh"

# ---------------------------------------------------------------------------
# The whole report -- argument parsing, project and pricing resolution, the
# scope, the roll-up reads, the human-readable and CSV rendering -- is
# pgai_agent_kanban.lib.metrics.cost_report(), the same function GET /costs
# calls; this script hands it its own arguments unchanged. --help prints
# this file's header (line 2 up to the "set -euo pipefail" line).
#
# For a missing roll-up the report runs the aggregator beside this script
# (../pm-agent/aggregate_tokens.py), else the project's dev tree copy.
# ---------------------------------------------------------------------------
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=lib/pp_run_ops.sh
source "${SCRIPT_DIR}/lib/pp_run_ops.sh"

pp_run_ops pgai_agent_kanban.lib.metrics cost_report \
    --script="$0" \
    --aggregator="${SCRIPT_DIR}/../pm-agent/aggregate_tokens.py" \
    --default-kanban-root="${PGAI_AGENT_KANBAN_ROOT_PATH:-}" \
    --default-project="${PGAI_PROJECT_NAME:-}" \
    -- "$@"
