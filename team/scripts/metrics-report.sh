#!/usr/bin/env bash
# team/scripts/metrics-report.sh
# Operator-facing metrics reporting CLI for the pgai-agent-kanban system.
#
# Reads per-RC and per-day rollup JSON files produced by
# team/scripts/lib/metrics_aggregator.py, the cumulative history CSV
# produced by team/scripts/lib/metrics_csv_writer.py, each project's
# reliability streaks in projects/<project>/metrics/reliability.json, and
# each project's operator inbox in projects/<project>/operator-inbox.md.
#
# USAGE:
#   metrics-report.sh --rc <version>             Dump per-RC JSON to stdout
#   metrics-report.sh --day <YYYY-MM-DD>         Dump per-day JSON to stdout
#   metrics-report.sh --csv                      Dump full history.csv to stdout
#   metrics-report.sh --csv --project <name>     Dump history.csv filtered by project
#   metrics-report.sh --format jsonl             Emit one JSON object per RC line
#   metrics-report.sh --tail                     Live-tail history.csv (one line per RC close)
#   metrics-report.sh --reliability              Hands-off and Clean streaks, every project
#   metrics-report.sh --reliability --project <name>
#                                                Hands-off and Clean streaks, one project
#   metrics-report.sh --reliability --format json --project <name>
#                                                Print the project's reliability.json
#   metrics-report.sh --inbox                    Open operator-inbox count, every project
#   metrics-report.sh --inbox --project <name>   Open operator-inbox count, one project
#   metrics-report.sh [-h|--help]                Show this help and exit
#
# FLAGS:
#   --rc <version>         Report for a specific RC (e.g. v0.24.12).
#                          Reads projects/<project>/metrics/rc/<version>.json.
#                          On-demand aggregation is invoked when the file is missing.
#   --day <YYYY-MM-DD>     Report for a specific UTC day.
#                          Reads projects/<project>/metrics/day/<date>.json.
#                          On-demand aggregation is invoked when the file is missing.
#                          The JSON printed carries one key the file does not:
#                          "inbox_open", the project's open operator-inbox count
#                          when the report is printed (null when the inbox cannot
#                          be read). A file that is not a JSON object is printed
#                          as stored, with a warning on stderr.
#   --csv                  Emit the full contents of projects/<project>/metrics/history.csv
#                          to stdout.  Combine with --project to filter to one project.
#   --format jsonl         Emit one JSON object per line (JSON Lines format).
#                          Without --rc or --day, streams all RC rollup files found in
#                          projects/<project>/metrics/rc/ sorted by RC version.
#                          Can be combined with --project.
#   --reliability          Print each project's reliability streaks from
#                          projects/<project>/metrics/reliability.json: the project
#                          name, then a Hands-off line (since the last block a human
#                          had to clear) and a Clean line (since the last block of any
#                          kind), each "<rcs> RCs / <hours>h   since <YYYY-MM-DD HH:MM>
#                          (<cause>)" in UTC, at most 70 columns -- the lines the
#                          rc-metrics pane's Reliability block shows. Without
#                          --project, every project in projects.cfg; a missing or
#                          unreadable file prints a placeholder line. Exits 0.
#                          Cannot be combined with --rc, --day, --csv or --tail.
#   --inbox                Print each project's open operator-inbox count from
#                          projects/<project>/operator-inbox.md, one line per
#                          project: "  <project>: <N> open" ("inbox unreadable"
#                          when the file cannot be read; a missing file is 0 open).
#                          Without --project, every project in projects.cfg.
#                          Exits 0. Cannot be combined with --rc, --day, --csv,
#                          --tail, --reliability or --format.
#   --format json          With --reliability: print the project's reliability.json
#                          as stored. Needs --project (or PGAI_PROJECT_NAME); exits 2
#                          when the file is missing or unreadable.
#   --now <iso>            With --reliability: count each streak's hours to this
#                          ISO-8601 time instead of now, e.g.
#                          --now 2026-10-18T12:00:00+00:00. The hours always run
#                          from the streak's "since" to the render time, not the
#                          figure stored at the file's last refresh.
#   --tail                 Live-tail history.csv using tail -f.  Streams each new row
#                          as it is appended by cm/release.sh / metrics_csv_writer.py.
#                          Press Ctrl-C to stop.
#   --project <name>       Project to operate on (required, except for --reliability
#                          and --inbox; or set PGAI_PROJECT_NAME env).
#   --kanban-root <path>   Override PGAI_AGENT_KANBAN_ROOT_PATH.
#   -h, --help             Show this help and exit.
#
# ON-DEMAND AGGREGATION:
#   When --rc or --day is requested and the corresponding rollup file is missing,
#   metrics-report.sh attempts to invoke metrics_aggregator.py to build it on
#   demand before reporting.  The aggregator is found at
#   team/scripts/lib/metrics_aggregator.py relative to the dev_tree_path
#   configured in PROJECT.cfg, or alongside this script.
#
# EXIT CODES:
#   0 -- success (warnings may appear on stderr)
#   1 -- usage error or unrecoverable configuration failure
#   2 -- requested data not found (no rollup file, empty CSV, etc.)

set -euo pipefail
# shellcheck source=lib/env_bootstrap.sh
source "$(dirname "${BASH_SOURCE[0]}")/lib/env_bootstrap.sh"

# ---------------------------------------------------------------------------
# Resolve script location and lib dir
# ---------------------------------------------------------------------------
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
LIB_DIR="${SCRIPT_DIR}/lib"

# Source project_paths.sh and projects.sh for projects_cfg_list (project name resolution).
# shellcheck source=lib/project_paths.sh
source "${LIB_DIR}/project_paths.sh"
# shellcheck source=lib/projects.sh
source "${LIB_DIR}/projects.sh"
# shellcheck source=lib/script_common.sh
source "${LIB_DIR}/script_common.sh"
# shellcheck source=lib/pp_run_ops.sh
source "${LIB_DIR}/pp_run_ops.sh"
# shellcheck source=lib/error_contract.sh
source "${LIB_DIR}/error_contract.sh"

# ---------------------------------------------------------------------------
# Defaults
# ---------------------------------------------------------------------------
MODE=""                 # rc | day | csv | jsonl | tail | reliability | inbox
RC_VERSION=""
DAY_VALUE=""
PROJECT_NAME="${PGAI_PROJECT_NAME:-}"
FORMAT_JSONL=0
KANBAN_ROOT_OVERRIDE=""
_CSV_FLAG=0
_FORMAT_FLAG=""
_PROJECT_EXPLICIT=0   # 1 when --project was explicitly passed
_RELIABILITY_FLAG=0   # 1 when --reliability was passed
_INBOX_FLAG=0         # 1 when --inbox was passed
_NOW_FLAG=""          # --now: the render time for --reliability text

# ---------------------------------------------------------------------------
# Usage
# ---------------------------------------------------------------------------
# Usage text as data (per lib/script_common.sh's print_usage_and_exit contract):
# the header comment block of this file, self-read via sed.  The sentinel
# capture preserves the header block's trailing blank line, which plain
# command substitution would otherwise swallow (it strips ALL trailing
# newlines); one newline is then trimmed back off because
# print_usage_and_exit's own printf adds exactly one — together this
# reproduces the pre-refactor byte-for-byte --help output.
_USAGE_TEXT="$(sed -n '2,/^set -euo pipefail/{ /^set -euo pipefail/d; s/^# \{0,1\}//; p }' "$0"; printf 'x')"
_USAGE_TEXT="${_USAGE_TEXT%x}"
_USAGE_TEXT="${_USAGE_TEXT%$'\n'}"

# ---------------------------------------------------------------------------
# Parse arguments
# ---------------------------------------------------------------------------
while [[ $# -gt 0 ]]; do
    case "$1" in
        --rc)
            [[ -n "${2:-}" ]] || { echo "ERROR: --rc requires a version argument" >&2; exit 1; }
            RC_VERSION="$2"
            shift 2
            ;;
        --day)
            [[ -n "${2:-}" ]] || { echo "ERROR: --day requires YYYY-MM-DD argument" >&2; exit 1; }
            DAY_VALUE="$2"
            shift 2
            ;;
        --csv)
            _CSV_FLAG=1
            shift
            ;;
        --format)
            [[ -n "${2:-}" ]] || { echo "ERROR: --format requires a format name argument" >&2; exit 1; }
            _FORMAT_FLAG="$2"
            shift 2
            ;;
        --tail)
            MODE="tail"
            shift
            ;;
        --reliability)
            _RELIABILITY_FLAG=1
            shift
            ;;
        --inbox)
            _INBOX_FLAG=1
            shift
            ;;
        --now)
            [[ -n "${2:-}" ]] || { echo "ERROR: --now requires an ISO-8601 time argument" >&2; exit 1; }
            _NOW_FLAG="$2"
            shift 2
            ;;
        --project)
            [[ -n "${2:-}" ]] || { echo "ERROR: --project requires a name argument" >&2; exit 1; }
            PROJECT_NAME="$2"
            _PROJECT_EXPLICIT=1
            shift 2
            ;;
        --kanban-root)
            [[ -n "${2:-}" ]] || { echo "ERROR: --kanban-root requires a path argument" >&2; exit 1; }
            KANBAN_ROOT_OVERRIDE="$2"
            shift 2
            ;;
        -h|--help)
            print_usage_and_exit 0 "$_USAGE_TEXT"
            ;;
        *)
            echo "ERROR: unknown argument: $1" >&2
            echo "Run with --help for usage." >&2
            exit 1
            ;;
    esac
done

# ---------------------------------------------------------------------------
# --inbox stands alone: no other operation and no --format
# ---------------------------------------------------------------------------
# _inbox_refuse <error-key> <message> <remedy>: the one refusal site the
# --inbox checks share (metrics_report.inbox_combined,
# metrics_report.inbox_unknown_project).
_inbox_refuse() {
    # AGENT-RESOLVABLE (docs/blocking-site-census.md): a usage error refused
    # before any inbox is read; nothing is written anywhere, and the caller
    # corrects its own next call.
    pgai_emit_error_contract "$1" "$2" "$3"
    # CENSUS-ANCHOR: metrics-report-inbox-01
    exit 1
}

if [[ "$_INBOX_FLAG" -eq 1 ]]; then
    _inbox_others=()
    [[ -n "$RC_VERSION" ]] && _inbox_others+=(--rc)
    [[ -n "$DAY_VALUE" ]] && _inbox_others+=(--day)
    [[ "$_CSV_FLAG" -eq 1 ]] && _inbox_others+=(--csv)
    [[ "$MODE" == "tail" ]] && _inbox_others+=(--tail)
    [[ "$_RELIABILITY_FLAG" -eq 1 ]] && _inbox_others+=(--reliability)
    [[ -n "$_FORMAT_FLAG" ]] && _inbox_others+=(--format)
    if [[ "${#_inbox_others[@]}" -gt 0 ]]; then
        _inbox_refuse "metrics_report.inbox_combined" \
            "--inbox prints open operator-inbox counts on its own and cannot be combined with ${_inbox_others[*]}; nothing was printed." \
            "$(printf '%q' "${SCRIPT_DIR}/metrics-report.sh")${KANBAN_ROOT_OVERRIDE:+ --kanban-root $(printf '%q' "$KANBAN_ROOT_OVERRIDE")} --inbox${PROJECT_NAME:+ --project $(printf '%q' "$PROJECT_NAME")}"
    fi
    MODE="inbox"
fi

# ---------------------------------------------------------------------------
# Validate --format value: "json" with --reliability, otherwise "jsonl"
# ---------------------------------------------------------------------------
if [[ "$MODE" == "inbox" ]]; then
    : # validated above
elif [[ "$_RELIABILITY_FLAG" -eq 1 ]]; then
    if [[ "$MODE" == "tail" || -n "$RC_VERSION" || -n "$DAY_VALUE" || "$_CSV_FLAG" -eq 1 ]]; then
        echo "ERROR: --reliability cannot be combined with --rc, --day, --csv or --tail" >&2
        echo "Run with --help for usage." >&2
        exit 1
    fi
    if [[ -n "$_FORMAT_FLAG" && "$_FORMAT_FLAG" != "json" ]]; then
        echo "ERROR: unsupported --format value with --reliability: '${_FORMAT_FLAG}' (supported: json)" >&2
        exit 1
    fi
    MODE="reliability"
elif [[ -n "$_FORMAT_FLAG" ]]; then
    if [[ "$_FORMAT_FLAG" != "jsonl" ]]; then
        echo "ERROR: unsupported --format value: '${_FORMAT_FLAG}' (supported: jsonl)" >&2
        exit 1
    fi
    FORMAT_JSONL=1
fi

# ---------------------------------------------------------------------------
# Resolve mode from flag combinations
# ---------------------------------------------------------------------------
if [[ "$MODE" == "tail" || "$MODE" == "reliability" || "$MODE" == "inbox" ]]; then
    : # already set
elif [[ -n "$RC_VERSION" ]]; then
    MODE="rc"
elif [[ -n "$DAY_VALUE" ]]; then
    MODE="day"
elif [[ "$_CSV_FLAG" -eq 1 && "$FORMAT_JSONL" -eq 1 ]]; then
    # --csv --format jsonl: treat as jsonl (CSV rows emitted as JSON objects)
    MODE="jsonl"
elif [[ "$_CSV_FLAG" -eq 1 ]]; then
    MODE="csv"
elif [[ "$FORMAT_JSONL" -eq 1 ]]; then
    MODE="jsonl"
else
    echo "ERROR: no operation specified." >&2
    echo "Run with --help for usage." >&2
    exit 1
fi

# ---------------------------------------------------------------------------
# Resolve kanban root
# ---------------------------------------------------------------------------
KANBAN_ROOT="${KANBAN_ROOT_OVERRIDE:-${PGAI_AGENT_KANBAN_ROOT_PATH}}"

if [[ ! -d "$KANBAN_ROOT" ]]; then
    echo "ERROR: kanban root not found: ${KANBAN_ROOT}" >&2
    exit 1
fi

# ===========================================================================
# MODE: reliability  --  print reliability streaks (text) or reliability.json
# ===========================================================================
# Rendered by pgai_agent_kanban.metrics.reliability_view, the renderer the
# rc-metrics pane's Reliability block uses, so the two print the same lines.
if [[ "$MODE" == "reliability" ]]; then
    if [[ "$_FORMAT_FLAG" == "json" ]]; then
        if [[ -z "${PROJECT_NAME:-}" ]]; then
            echo "ERROR: --reliability --format json needs a project. Pass --project <name> or set PGAI_PROJECT_NAME." >&2
            echo "Run with --help for usage." >&2
            exit 1
        fi
        if [[ ! -d "${KANBAN_ROOT}/projects/${PROJECT_NAME}" ]]; then
            echo "ERROR: project directory not found: ${KANBAN_ROOT}/projects/${PROJECT_NAME}" >&2
            exit 1
        fi
        pp_run_ops pgai_agent_kanban.metrics.reliability_view json --kanban-root "$KANBAN_ROOT" "$PROJECT_NAME"
        exit $?
    fi

    _RELIABILITY_PROJECTS=()
    if [[ "$_PROJECT_EXPLICIT" -eq 1 ]]; then
        if [[ ! -d "${KANBAN_ROOT}/projects/${PROJECT_NAME}" ]]; then
            echo "ERROR: project directory not found: ${KANBAN_ROOT}/projects/${PROJECT_NAME}" >&2
            exit 1
        fi
        _RELIABILITY_PROJECTS=("$PROJECT_NAME")
    else
        while IFS= read -r _p; do
            [[ -n "$_p" ]] && _RELIABILITY_PROJECTS+=("$_p")
        done < <(KANBAN_ROOT="$KANBAN_ROOT" projects_cfg_list 2>/dev/null || true)
    fi
    _RELIABILITY_NOW_ARGS=()
    [[ -n "$_NOW_FLAG" ]] && _RELIABILITY_NOW_ARGS=(--now "$_NOW_FLAG")
    pp_run_ops pgai_agent_kanban.metrics.reliability_view text --kanban-root "$KANBAN_ROOT" \
        ${_RELIABILITY_NOW_ARGS[@]+"${_RELIABILITY_NOW_ARGS[@]}"} \
        ${_RELIABILITY_PROJECTS[@]+"${_RELIABILITY_PROJECTS[@]}"}
    exit $?
fi

# ===========================================================================
# MODE: inbox  --  print each project's open operator-inbox count
# ===========================================================================
# Read through pgai_agent_kanban.metrics.inbox_view, which reads the inbox
# with the inbox module -- the same reader the attention pane's OPERATOR
# INBOX block uses.
if [[ "$MODE" == "inbox" ]]; then
    _INBOX_PROJECTS=()
    if [[ "$_PROJECT_EXPLICIT" -eq 1 ]]; then
        if [[ ! -d "${KANBAN_ROOT}/projects/${PROJECT_NAME}" ]]; then
            _inbox_refuse "metrics_report.inbox_unknown_project" \
                "project '${PROJECT_NAME}' has no directory at ${KANBAN_ROOT}/projects/${PROJECT_NAME}, so it has no operator inbox; nothing was printed." \
                "ls $(printf '%q' "${KANBAN_ROOT}/projects")"
        fi
        _INBOX_PROJECTS=("$PROJECT_NAME")
    else
        while IFS= read -r _p; do
            [[ -n "$_p" ]] && _INBOX_PROJECTS+=("$_p")
        done < <(KANBAN_ROOT="$KANBAN_ROOT" projects_cfg_list 2>/dev/null || true)
    fi
    pp_run_ops pgai_agent_kanban.metrics.inbox_view counts --kanban-root "$KANBAN_ROOT" \
        ${_INBOX_PROJECTS[@]+"${_INBOX_PROJECTS[@]}"}
    exit $?
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

METRICS_DIR="${PROJECT_DIR}/metrics"
METRICS_RC_DIR="${METRICS_DIR}/rc"
METRICS_DAY_DIR="${METRICS_DIR}/day"
HISTORY_CSV="${METRICS_DIR}/history.csv"

# ---------------------------------------------------------------------------
# Locate metrics_aggregator.py for on-demand aggregation
# ---------------------------------------------------------------------------
AGGREGATOR_PY=""
# 1. Alongside this script (dev-tree path)
if [[ -f "${LIB_DIR}/metrics_aggregator.py" ]]; then
    AGGREGATOR_PY="${LIB_DIR}/metrics_aggregator.py"
fi
# 2. Try dev_tree_path from project.cfg (prefer lowercase; fall back to uppercase for legacy installs)
PROJECT_CFG=""
if [[ -f "${PROJECT_DIR}/project.cfg" ]]; then
    PROJECT_CFG="${PROJECT_DIR}/project.cfg"
elif [[ -f "${PROJECT_DIR}/PROJECT.cfg" ]]; then
    PROJECT_CFG="${PROJECT_DIR}/PROJECT.cfg"
fi
if [[ -z "$AGGREGATOR_PY" && -n "$PROJECT_CFG" ]]; then
    while IFS='=' read -r key val; do
        key="${key%%#*}"
        key="${key// /}"
        val="${val## }"
        val="${val%% }"
        val="${val#\"}"
        val="${val%\"}"
        val="${val#\'}"
        val="${val%\'}"
        if [[ "$key" == "dev_tree_path" && -n "$val" ]]; then
            _cand="${val}/team/scripts/lib/metrics_aggregator.py"
            if [[ -f "$_cand" ]]; then
                AGGREGATOR_PY="$_cand"
            fi
            break
        fi
    done < "$PROJECT_CFG"
fi

# ---------------------------------------------------------------------------
# Helper: invoke aggregator on demand
# ---------------------------------------------------------------------------
_invoke_aggregator() {
    local flag="$1"    # --rc or --day
    local value="$2"

    if [[ -z "$AGGREGATOR_PY" ]]; then
        echo "[metrics-report] WARNING: metrics_aggregator.py not found; cannot auto-aggregate." >&2
        return 0
    fi

    echo "[metrics-report] Rollup file missing; invoking aggregator (${flag} ${value}) ..." >&2
    python3 "$AGGREGATOR_PY" \
        --project "$PROJECT_NAME" \
        "$flag" "$value" \
        ${KANBAN_ROOT_OVERRIDE:+--kanban-root "$KANBAN_ROOT_OVERRIDE"} \
        >&2 || true
}

# ---------------------------------------------------------------------------
# Normalise RC version: ensure it starts with 'v'
# ---------------------------------------------------------------------------
_normalise_rc() {
    local ver="$1"
    if [[ "${ver:0:1}" != "v" ]]; then
        ver="v${ver}"
    fi
    echo "$ver"
}

# ===========================================================================
# MODE: rc  --  print per-RC JSON rollup
# ===========================================================================
if [[ "$MODE" == "rc" ]]; then
    RC_VERSION="$(_normalise_rc "$RC_VERSION")"
    RC_FILE="${METRICS_RC_DIR}/${RC_VERSION}.json"

    if [[ ! -f "$RC_FILE" ]]; then
        _invoke_aggregator "--rc" "$RC_VERSION"
    fi

    if [[ ! -f "$RC_FILE" ]]; then
        echo "ERROR: no metrics rollup found for RC '${RC_VERSION}'" >&2
        echo "  Expected: ${RC_FILE}" >&2
        echo "  Run: python3 ${AGGREGATOR_PY:-team/scripts/lib/metrics_aggregator.py} --project ${PROJECT_NAME} --rc ${RC_VERSION}" >&2
        exit 2
    fi

    cat "$RC_FILE"
    exit 0
fi

# ===========================================================================
# MODE: day  --  print per-day JSON rollup
# ===========================================================================
if [[ "$MODE" == "day" ]]; then
    DAY_FILE="${METRICS_DAY_DIR}/${DAY_VALUE}.json"

    if [[ ! -f "$DAY_FILE" ]]; then
        _invoke_aggregator "--day" "$DAY_VALUE"
    fi

    if [[ ! -f "$DAY_FILE" ]]; then
        echo "ERROR: no metrics rollup found for day '${DAY_VALUE}'" >&2
        echo "  Expected: ${DAY_FILE}" >&2
        echo "  Run: python3 ${AGGREGATOR_PY:-team/scripts/lib/metrics_aggregator.py} --project ${PROJECT_NAME} --day ${DAY_VALUE}" >&2
        exit 2
    fi

    # The rollup as stored, plus "inbox_open": the project's open
    # operator-inbox count now (pgai_agent_kanban.metrics.inbox_view).
    pp_run_ops pgai_agent_kanban.metrics.inbox_view day --kanban-root "$KANBAN_ROOT" \
        --project "$PROJECT_NAME" "$DAY_FILE"
    exit $?
fi

# ===========================================================================
# MODE: csv  --  print history.csv (optionally filtered by project)
# ===========================================================================
if [[ "$MODE" == "csv" ]]; then
    if [[ ! -f "$HISTORY_CSV" ]]; then
        echo "ERROR: no metrics history CSV found: ${HISTORY_CSV}" >&2
        echo "  The file is created on first RC close by metrics_csv_writer.py." >&2
        exit 2
    fi

    # Filter by project column when --project was explicitly passed;
    # otherwise emit the entire CSV unmodified (multi-project history files
    # are valid and callers may want all rows). The CSV is re-read and
    # re-written by pgai_agent_kanban.lib.metrics.history_csv(), which
    # handles quoting and prints the header alone when no row matches.
    _CSV_PROJECT_FILTER=""
    if [[ "$_PROJECT_EXPLICIT" -eq 1 ]]; then
        _CSV_PROJECT_FILTER="$PROJECT_NAME"
    fi

    pp_run_ops pgai_agent_kanban.lib.metrics history_csv "$HISTORY_CSV" "$_CSV_PROJECT_FILTER"
    exit 0
fi

# ===========================================================================
# MODE: jsonl  --  emit one JSON object per RC, one per line
# ===========================================================================
if [[ "$MODE" == "jsonl" ]]; then
    # Collect all per-RC rollup JSON files for the project, sorted by filename
    # (which sorts by RC version since filenames are v<semver>.json).
    if [[ ! -d "$METRICS_RC_DIR" ]]; then
        echo "ERROR: no metrics/rc directory found: ${METRICS_RC_DIR}" >&2
        echo "  Run aggregation first: python3 ${AGGREGATOR_PY:-team/scripts/lib/metrics_aggregator.py} --project ${PROJECT_NAME} --all" >&2
        exit 2
    fi

    # Find and sort RC JSON files
    RC_FILES=()
    while IFS= read -r -d '' f; do
        RC_FILES+=("$f")
    done < <(find "$METRICS_RC_DIR" -maxdepth 1 -name 'v*.json' -print0 2>/dev/null | sort -z)

    if [[ "${#RC_FILES[@]}" -eq 0 ]]; then
        echo "ERROR: no RC rollup files found in ${METRICS_RC_DIR}" >&2
        exit 2
    fi

    # Emit each JSON file on a single line (compact, keys sorted) through
    # pgai_agent_kanban.lib.metrics.rc_rollups_jsonl(); an unreadable file
    # is skipped with a warning on stderr. Pass project name as filter only
    # when --project was explicitly given.
    _JSONL_PROJECT_FILTER=""
    if [[ "$_PROJECT_EXPLICIT" -eq 1 ]]; then
        _JSONL_PROJECT_FILTER="$PROJECT_NAME"
    fi

    pp_run_ops pgai_agent_kanban.lib.metrics rc_rollups_jsonl "$_JSONL_PROJECT_FILTER" "${RC_FILES[@]}"
    exit 0
fi

# ===========================================================================
# MODE: tail  --  live-tail history.csv using tail -f
# ===========================================================================
if [[ "$MODE" == "tail" ]]; then
    if [[ ! -f "$HISTORY_CSV" ]]; then
        echo "[metrics-report] history.csv does not exist yet: ${HISTORY_CSV}" >&2
        echo "[metrics-report] Waiting for it to be created (Ctrl-C to abort) ..." >&2
        # Poll until the file appears, then hand off to tail -f
        while [[ ! -f "$HISTORY_CSV" ]]; do
            sleep 2
        done
    fi

    echo "[metrics-report] Tailing ${HISTORY_CSV}  (Ctrl-C to stop)" >&2
    exec tail -f "$HISTORY_CSV"
fi

# Should never reach here; all modes handled above.
echo "ERROR: internal: unhandled mode '${MODE}'" >&2
exit 1
