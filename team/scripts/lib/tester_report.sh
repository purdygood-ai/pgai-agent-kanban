#!/usr/bin/env bash
# team/scripts/lib/tester_report.sh
# Bash surface over the one TESTER-report parser
# (pgai_agent_kanban.reports.tester_report). Shell callers read a report's
# recommendation, systemic risk, summary and findings through this file and
# never match the report text themselves; the parse rules live in the
# Python module's docstring, stated once.
#
# Source this file to get tester_report_json and tester_report:
#   source "$(dirname "${BASH_SOURCE[0]}")/tester_report.sh"
#
# Public API
# ----------
#   tester_report_json <report_path>
#       Prints the parsed report as JSON -- byte-for-byte what the parser
#       module's own CLI prints for "<report_path> --json" (its docstring's
#       "Usage (CLI)") -- and returns the parser's exit status: 0 for any
#       readable report (one with no findings included), 1 when the report
#       cannot be read (the reason goes to stderr), 2 on a usage error.
#
#   tester_report [args...]
#       Runs the parser CLI with the given arguments unchanged: the report
#       path (or "-" for standard input), optional --json, or --help for
#       the parser's own usage text.
#
# Run directly instead of sourced, the file is the same CLI:
#   team/scripts/lib/tester_report.sh <report_path> [--json]
#   team/scripts/lib/tester_report.sh --help
#
# The module is run through pp_run_ops (pp_run_ops.sh, sourced here and
# self-located), so the package resolves from this file's own tree whatever
# the working directory. Reads only; writes nothing.

# Include guard: safe to source multiple times (only loads once).
[[ -n "${_PGAI_TESTER_REPORT_SH_LOADED:-}" ]] && return 0
_PGAI_TESTER_REPORT_SH_LOADED=1

if ! declare -F pp_run_ops >/dev/null 2>&1; then
    # shellcheck source=pp_run_ops.sh
    source "$(dirname "${BASH_SOURCE[0]}")/pp_run_ops.sh"
fi

# ---------------------------------------------------------------------------
# tester_report [args...]
# See "Public API" above.
# ---------------------------------------------------------------------------
tester_report() {
    pp_run_ops pgai_agent_kanban.reports.tester_report "$@"
}

# ---------------------------------------------------------------------------
# tester_report_json <report_path>
# See "Public API" above.
# ---------------------------------------------------------------------------
tester_report_json() {
    local report_path="${1:-}"
    if [[ -z "${report_path}" ]]; then
        echo "tester_report_json: a report path is required, e.g. tester_report_json tasks/TESTER-20260101-001-verify/artifacts/report.md" >&2
        return 2
    fi
    tester_report "${report_path}" --json
}

# Direct execution: run the CLI with every argument passed through, so
# --help, -h and a report path all reach the parser unchanged.
if [[ "${BASH_SOURCE[0]}" == "${0}" ]]; then
    set -euo pipefail
    tester_report "$@"
fi
