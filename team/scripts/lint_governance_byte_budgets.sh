#!/usr/bin/env bash
# team/scripts/lint_governance_byte_budgets.sh
#
# Reports each role's assembled governance byte total against its
# configured budget in kanban.cfg [governance]. NARRATES ONLY -- never
# blocks. This is explicitly not a gate: it carries no census
# classification, no census anchor, and no dual-channel refusal, because it
# has no refusal to speak. See team/SOP-core.md "Gates and Refusals" for
# what a gate is; this script is not one.
#
# WHAT THIS MEASURES
# -------------------
# For each of the seven kanban roles (po, pm, coder, writer, tester, cm,
# overwatch), resolves that role's read-order manifest via
# team/scripts/lib/read_order.sh -- the single source of truth for manifest
# parsing; this script never re-parses read-order/*.txt itself -- and sums
# the on-disk byte size of every file the manifest names. That total is
# compared against the role's budget_bytes_<role> key under [governance]
# in kanban.cfg. A role with no configured key is reported "unbudgeted" --
# not an error, not a finding, just information.
#
# WHY IT NEVER BLOCKS
# --------------------
# Per team/SOP-core.md's "Quality findings never stop the loop" principle: an
# over-budget role today is expected while the SOP/role-file lean-up split
# is still landing across separate tickets. This script's only job is to
# make rulebook growth visible at the moment it happens instead of in a
# token meter three RCs later -- so it always exits 0, whatever the
# per-role verdicts read.
#
# CONFIG SOURCE
# --------------
# Like every other entry point in this directory, this script sources
# lib/env_bootstrap.sh as its first act, so PGAI_AGENT_KANBAN_ROOT_PATH is
# resolved and exported before any config lookup runs. Reads [governance]
# budget_bytes_<role> keys via team/scripts/lib/ini_parser.sh's read_ini.
# Config path resolution, highest priority first:
#   1. --kanban-cfg PATH   Explicit override (tests use this).
#   2. $PGAI_AGENT_KANBAN_ROOT_PATH/kanban.cfg   A running live install.
#   3. <this script's dir>/../kanban.cfg   Self-located live-install
#      layout, in case the bootstrapped root above has no kanban.cfg yet.
#   4. <this script's dir>/../../kanban.cfg_example   Self-located dev-tree
#      template (each tree finds itself -- see team/roles/CODER.md
#      Principle 6).
# A missing config file is not an error: read_ini returns "" for any key
# under a nonexistent file, so every role reports unbudgeted.
#
# Usage:
#   team/scripts/lint_governance_byte_budgets.sh [--kanban-cfg PATH] [--verbose]
#                                                 [--findings-file PATH]
#
#   --kanban-cfg PATH    Explicit kanban.cfg (or kanban.cfg_example) path.
#                        Overrides the resolution order above.
#   --verbose, -v        Narrate the resolved config path and read_order's
#                        own stderr narration (skipped manifest lines, etc.).
#   --findings-file PATH Write every over-budget role as one quality-class
#                        finding in a JSON array to PATH (schema below).
#                        An all-under-budget run writes an empty array --
#                        the file is always written when this flag is
#                        given, never left stale from a prior invocation.
#                        Callers merge it with
#                        team/scripts/lib/gated_runner_lint.sh's
#                        gated_runner_merge_findings_file.
#   --help, -h           Show this help and exit.
#
# Exit codes:
#   0   Always. This script narrates; it never blocks. See "WHY IT NEVER
#       BLOCKS" above. (A malformed CLI argument still exits 2 -- that is
#       ordinary operator-CLI usage validation, not a loop-facing gate; see
#       lint_gate_census_compliance.py's own operator-CLI exclusion for the
#       same class of site.)
#
# Output:
#   One line per role: role name, assembled bytes, configured budget (or
#   "-" when unbudgeted), and a verdict of "under", "OVER", "unbudgeted",
#   or "invalid-budget" (a non-numeric configured value, treated the same
#   as unbudgeted rather than crashing the comparison). An over-budget role
#   additionally prints one unconditional narration line -- role name,
#   assembled bytes, configured budget -- immediately after its table row,
#   whether or not --verbose is given: a soft size heuristic is an
#   advisory-config prompt about weight, not evidence anything is broken
#   (team/SOP-core.md "Blocking Severity: only real damage blocks"), so it
#   is surfaced plainly rather than buried behind a flag.
#
# FINDINGS-FILE SCHEMA (--findings-file only)
# ---------------------------------------------
# A JSON array, one object per over-budget role (empty when none):
#   {
#     "lint": "governance_byte_budget",
#     "severity": "quality",
#     "role": "<role>",
#     "assembled_bytes": <int>,
#     "budget_bytes": <int>,
#     "summary": "<role> over governance byte budget: assembled=<N> bytes budget=<N> bytes"
#   }
# Matches the shape team/scripts/lib/gated_runner_lint.sh's
# gated_runner_merge_findings_file expects from an already-structured,
# per-instance findings producer (the same shape lint_dual_channel_pair.py
# and lint_gate_census_compliance.py write via their own --findings-file).

set -euo pipefail
# shellcheck source=lib/env_bootstrap.sh
source "$(dirname "${BASH_SOURCE[0]}")/lib/env_bootstrap.sh"

_GBB_SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=lib/read_order.sh
source "${_GBB_SCRIPT_DIR}/lib/read_order.sh"
# shellcheck source=lib/ini_parser.sh
source "${_GBB_SCRIPT_DIR}/lib/ini_parser.sh"

# The seven roles in the catalog: the six RC pipeline agents (OVERVIEW.md)
# plus OVERWATCH, the horizontal observer outside the pipeline.
_GBB_ROLES=(po pm coder writer tester cm overwatch)

_gbb_usage() {
    cat <<'EOF'
Usage: lint_governance_byte_budgets.sh [--kanban-cfg PATH] [--verbose]
                                        [--findings-file PATH]

Reports each role's assembled governance byte total (summed via
team/scripts/lib/read_order.sh) against its configured budget in
kanban.cfg [governance]. Narrates only -- always exits 0.

Options:
  --kanban-cfg PATH    Explicit kanban.cfg (or kanban.cfg_example) path.
  --verbose, -v        Narrate the resolved config path and read_order's
                       own stderr narration (skipped manifest lines, etc.).
  --findings-file PATH Write over-budget roles as quality-class findings
                       (JSON array, empty when none) to PATH.
  --help, -h           Show this help and exit.
EOF
}

_gbb_cfg_override=""
_gbb_verbose=false
_gbb_findings_file=""

while [[ $# -gt 0 ]]; do
    case "$1" in
        --kanban-cfg)
            _gbb_cfg_override="${2:-}"
            shift 2
            ;;
        --kanban-cfg=*)
            _gbb_cfg_override="${1#*=}"
            shift
            ;;
        --verbose|-v)
            _gbb_verbose=true
            shift
            ;;
        --findings-file)
            _gbb_findings_file="${2:-}"
            shift 2
            ;;
        --findings-file=*)
            _gbb_findings_file="${1#*=}"
            shift
            ;;
        --help|-h)
            _gbb_usage
            exit 0
            ;;
        *)
            printf 'lint_governance_byte_budgets.sh: unknown argument: %s\n' "$1" >&2
            _gbb_usage >&2
            exit 2
            ;;
    esac
done

# ---------------------------------------------------------------------------
# _gbb_resolve_cfg_path -- highest-priority existing candidate wins. The
# lowest-priority candidate (dev-tree kanban.cfg_example) is returned even
# when it does not exist so that read_ini's graceful missing-file default
# still applies rather than this script inventing its own empty-config path.
# ---------------------------------------------------------------------------
_gbb_resolve_cfg_path() {
    if [[ -n "$_gbb_cfg_override" ]]; then
        printf '%s' "$_gbb_cfg_override"
        return 0
    fi

    if [[ -n "${PGAI_AGENT_KANBAN_ROOT_PATH:-}" && -f "${PGAI_AGENT_KANBAN_ROOT_PATH}/kanban.cfg" ]]; then
        printf '%s' "${PGAI_AGENT_KANBAN_ROOT_PATH}/kanban.cfg"
        return 0
    fi

    # Live-install layout: this script sits at <root>/scripts/, one level
    # below root. Self-locate rather than trust an env var that may be
    # stale or unset.
    local _live_candidate="${_GBB_SCRIPT_DIR}/../kanban.cfg"
    if [[ -f "$_live_candidate" ]]; then
        printf '%s' "$_live_candidate"
        return 0
    fi

    # Dev-tree layout: team/scripts/ sits two levels below the repo root
    # that ships kanban.cfg_example.
    printf '%s' "${_GBB_SCRIPT_DIR}/../../kanban.cfg_example"
}

_gbb_cfg_path="$(_gbb_resolve_cfg_path)"

if [[ "$_gbb_verbose" == true ]]; then
    if [[ -f "$_gbb_cfg_path" ]]; then
        printf 'lint_governance_byte_budgets: reading budgets from %s\n' "$_gbb_cfg_path" >&2
    else
        printf 'lint_governance_byte_budgets: no config file found (tried %s); every role reports unbudgeted\n' "$_gbb_cfg_path" >&2
    fi
fi

# ---------------------------------------------------------------------------
# Table header + per-role rows.
# ---------------------------------------------------------------------------
printf '%-11s %14s %14s   %s\n' "ROLE" "ASSEMBLED" "BUDGET" "VERDICT"

_gbb_any_over=false
_gbb_over_roles=()
_gbb_over_assembled=()
_gbb_over_budget=()
for _gbb_role in "${_GBB_ROLES[@]}"; do
    if [[ "$_gbb_verbose" == true ]]; then
        _gbb_assembled="$(read_order_role_byte_total "$_gbb_role")"
    else
        _gbb_assembled="$(read_order_role_byte_total "$_gbb_role" 2>/dev/null)"
    fi

    _gbb_budget="$(read_ini "$_gbb_cfg_path" governance "budget_bytes_${_gbb_role}" "")"

    if [[ -z "$_gbb_budget" ]]; then
        printf '%-11s %14s %14s   %s\n' "$_gbb_role" "$_gbb_assembled" "-" "unbudgeted"
        continue
    fi

    if ! [[ "$_gbb_budget" =~ ^[0-9]+$ ]]; then
        printf '%-11s %14s %14s   %s\n' "$_gbb_role" "$_gbb_assembled" "$_gbb_budget" "invalid-budget"
        continue
    fi

    if [[ "$_gbb_assembled" -gt "$_gbb_budget" ]]; then
        printf '%-11s %14s %14s   %s\n' "$_gbb_role" "$_gbb_assembled" "$_gbb_budget" "OVER"
        # Unconditional per-role narration (not gated behind --verbose): an
        # over-budget reading is advisory config, not evidence of damage
        # (team/SOP-core.md "Blocking Severity: only real damage blocks"),
        # so it is always visible to whatever invoked this script rather
        # than requiring an extra flag to surface it.
        printf 'lint_governance_byte_budgets: %s over governance byte budget: assembled=%s bytes budget=%s bytes\n' \
            "$_gbb_role" "$_gbb_assembled" "$_gbb_budget"
        _gbb_any_over=true
        _gbb_over_roles+=("$_gbb_role")
        _gbb_over_assembled+=("$_gbb_assembled")
        _gbb_over_budget+=("$_gbb_budget")
    else
        printf '%-11s %14s %14s   %s\n' "$_gbb_role" "$_gbb_assembled" "$_gbb_budget" "under"
    fi
done

if [[ "$_gbb_verbose" == true && "$_gbb_any_over" == true ]]; then
    printf '\nlint_governance_byte_budgets: one or more roles over budget -- narrated only, not a gate; see team/SOP-core.md "Gates and Refusals".\n' >&2
fi

# ---------------------------------------------------------------------------
# --findings-file: write every over-budget role as one quality-class finding
# (empty array when none). Always writes when the flag is given, so a
# downstream findings file never carries a stale entry from a prior
# invocation. See the FINDINGS-FILE SCHEMA header comment for the shape.
# ---------------------------------------------------------------------------
if [[ -n "$_gbb_findings_file" ]]; then
    mkdir -p "$(dirname "$_gbb_findings_file")"
    _gbb_findings_json="[]"
    if [[ ${#_gbb_over_roles[@]} -gt 0 ]]; then
        _gbb_findings_json="$(
            for _gbb_i in "${!_gbb_over_roles[@]}"; do
                printf '%s\t%s\t%s\n' \
                    "${_gbb_over_roles[$_gbb_i]}" \
                    "${_gbb_over_assembled[$_gbb_i]}" \
                    "${_gbb_over_budget[$_gbb_i]}"
            done | python3 -c '
import json
import sys

findings = []
for line in sys.stdin:
    line = line.rstrip("\n")
    if not line:
        continue
    role, assembled, budget = line.split("\t")
    assembled_i, budget_i = int(assembled), int(budget)
    findings.append({
        "lint": "governance_byte_budget",
        "severity": "quality",
        "role": role,
        "assembled_bytes": assembled_i,
        "budget_bytes": budget_i,
        "summary": (
            f"{role} over governance byte budget: "
            f"assembled={assembled_i} bytes budget={budget_i} bytes"
        ),
    })
print(json.dumps(findings, indent=2, sort_keys=True))
'
        )"
    fi
    printf '%s\n' "$_gbb_findings_json" > "$_gbb_findings_file"
fi

# NARRATE, DO NOT BLOCK: always exit 0, whatever the per-role verdicts
# above read. This is not a gate -- it carries no census classification,
# no census anchor, and no refusal to classify (see the file header).
exit 0
