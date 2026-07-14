#!/usr/bin/env bash
# team/scripts/metrics-report.sh
# Operator-facing metrics reporting CLI for the pgai-agent-kanban system.
#
# Reads per-RC and per-day rollup JSON files produced by
# team/scripts/lib/metrics_aggregator.py and the cumulative history CSV
# produced by team/scripts/lib/metrics_csv_writer.py.
#
# USAGE:
#   metrics-report.sh --rc <version>             Dump per-RC JSON to stdout
#   metrics-report.sh --day <YYYY-MM-DD>         Dump per-day JSON to stdout
#   metrics-report.sh --csv                      Dump full history.csv to stdout
#   metrics-report.sh --csv --project <name>     Dump history.csv filtered by project
#   metrics-report.sh --format jsonl             Emit one JSON object per RC line
#   metrics-report.sh --tail                     Live-tail history.csv (one line per RC close)
#   metrics-report.sh [-h|--help]                Show this help and exit
#
# FLAGS:
#   --rc <version>         Report for a specific RC (e.g. v0.24.12).
#                          Reads projects/<project>/metrics/rc/<version>.json.
#                          On-demand aggregation is invoked when the file is missing.
#   --day <YYYY-MM-DD>     Report for a specific UTC day.
#                          Reads projects/<project>/metrics/day/<date>.json.
#                          On-demand aggregation is invoked when the file is missing.
#   --csv                  Emit the full contents of projects/<project>/metrics/history.csv
#                          to stdout.  Combine with --project to filter to one project.
#   --format jsonl         Emit one JSON object per line (JSON Lines format).
#                          Without --rc or --day, streams all RC rollup files found in
#                          projects/<project>/metrics/rc/ sorted by RC version.
#                          Can be combined with --project.
#   --tail                 Live-tail history.csv using tail -f.  Streams each new row
#                          as it is appended by cm-release.sh / metrics_csv_writer.py.
#                          Press Ctrl-C to stop.
#   --project <name>       Project to operate on (required; or set PGAI_PROJECT_NAME env).
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

# ---------------------------------------------------------------------------
# Defaults
# ---------------------------------------------------------------------------
MODE=""                 # rc | day | csv | jsonl | tail
RC_VERSION=""
DAY_VALUE=""
PROJECT_NAME="${PGAI_PROJECT_NAME:-}"
FORMAT_JSONL=0
KANBAN_ROOT_OVERRIDE=""
_CSV_FLAG=0
_FORMAT_FLAG=""
_PROJECT_EXPLICIT=0   # 1 when --project was explicitly passed

# ---------------------------------------------------------------------------
# Usage
# ---------------------------------------------------------------------------
_usage() {
    sed -n '2,/^set -euo pipefail/{ /^set -euo pipefail/d; s/^# \{0,1\}//; p }' "$0"
}

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
            _usage
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
# Validate --format value (currently only "jsonl" is supported)
# ---------------------------------------------------------------------------
if [[ -n "$_FORMAT_FLAG" ]]; then
    if [[ "$_FORMAT_FLAG" != "jsonl" ]]; then
        echo "ERROR: unsupported --format value: '${_FORMAT_FLAG}' (supported: jsonl)" >&2
        exit 1
    fi
    FORMAT_JSONL=1
fi

# ---------------------------------------------------------------------------
# Resolve mode from flag combinations
# ---------------------------------------------------------------------------
if [[ "$MODE" == "tail" ]]; then
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

    cat "$DAY_FILE"
    exit 0
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
    # are valid and callers may want all rows).
    # Use Python for reliable CSV column extraction to handle quoting correctly.
    _CSV_PROJECT_FILTER=""
    if [[ "$_PROJECT_EXPLICIT" -eq 1 ]]; then
        _CSV_PROJECT_FILTER="$PROJECT_NAME"
    fi

    python3 - "$HISTORY_CSV" "$_CSV_PROJECT_FILTER" <<'PY_CSV_FILTER'
import csv, sys, pathlib

csv_path = pathlib.Path(sys.argv[1])
project_filter = sys.argv[2]   # filter to this project; "" means all

rows = []
header = None

with open(csv_path, newline="", encoding="utf-8") as fh:
    reader = csv.DictReader(fh)
    header = reader.fieldnames or []
    for row in reader:
        if not project_filter or row.get("project", "") == project_filter:
            rows.append(row)

if not rows:
    # Print header even when no rows match so callers can parse the schema
    print(",".join(header))
    sys.exit(0)

import io
buf = io.StringIO()
writer = csv.DictWriter(buf, fieldnames=header, lineterminator="\n",
                        extrasaction="ignore")
writer.writeheader()
writer.writerows(rows)
print(buf.getvalue(), end="")
PY_CSV_FILTER

    exit "${PIPESTATUS[0]:-$?}"
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

    # Emit each JSON file on a single line (compact JSON).
    # Pass project name as filter only when --project was explicitly given.
    _JSONL_PROJECT_FILTER=""
    if [[ "$_PROJECT_EXPLICIT" -eq 1 ]]; then
        _JSONL_PROJECT_FILTER="$PROJECT_NAME"
    fi

    python3 - "${RC_FILES[@]}" "$_JSONL_PROJECT_FILTER" <<'PY_JSONL'
import json, sys, pathlib

# Last arg is project name (filter); all prior args are file paths
project_filter = sys.argv[-1]
file_paths = sys.argv[1:-1]

for path_str in file_paths:
    p = pathlib.Path(path_str)
    if not p.is_file():
        continue
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError) as exc:
        import sys as _sys
        print(f"WARNING: skipping {p}: {exc}", file=_sys.stderr)
        continue
    if project_filter and data.get("project", "") != project_filter:
        continue
    # Emit as compact single-line JSON
    print(json.dumps(data, separators=(",", ":"), sort_keys=True))
PY_JSONL

    exit "${PIPESTATUS[0]:-$?}"
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
