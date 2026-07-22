#!/usr/bin/env bash
# team/scripts/collect-reasoning-traces.sh
# Collect and bundle reasoning-trace files from per-project training corpora.
#
# Usage:
#   collect-reasoning-traces.sh <agent-type> [--last N] [--project <name>]
#
# Arguments:
#   <agent-type>       The role/agent name to filter on.
#                      Examples: coder, cm, tester, writer, pm
#                      Case-insensitive; canonical form is lowercase (directory name).
#
# Flags:
#   --last N           Emit the N most-recent traces. Default: 10.
#   --project <name>   Filter output to a single named project. Without this flag
#                      the collector gathers traces from every registered project's
#                      corpus directory. Unknown project names exit with an error.
#
# Output:
#   A markdown bundle on stdout. Each trace is preceded by a header line
#   containing the project, agent-type, filename, and modification timestamp.
#   Operator pipes to file as needed:
#     collect-reasoning-traces.sh coder --last 5 > /tmp/coder-traces.md
#     collect-reasoning-traces.sh coder --project pgai-agent-kanban > /tmp/coder-traces.md
#
# Exit codes:
#   0   Success (zero matching traces is not an error; an empty bundle is emitted).
#   1   Configuration error (missing agent-type, invalid --last value, unknown
#       --project name, or missing required environment).
#
# Path resolution:
#   Scans per-project training corpus directories:
#     $KANBAN_ROOT/projects/<project>/logs/training/<agent-type>/
#   Uses PGAI_AGENT_KANBAN_ROOT_PATH (default: $HOME/pgai_agent_kanban) and the
#   pp_* helpers from lib/project_paths.sh to locate corpus directories.
#   projects_cfg_list (from lib/projects.sh) provides the project enumeration.

set -euo pipefail
# shellcheck source=lib/env_bootstrap.sh
source "$(dirname "${BASH_SOURCE[0]}")/lib/env_bootstrap.sh"

# ---------------------------------------------------------------------------
# Resolve script dir and source helpers
# ---------------------------------------------------------------------------
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=lib/project_paths.sh
source "${SCRIPT_DIR}/lib/project_paths.sh"
# shellcheck source=lib/projects.sh
source "${SCRIPT_DIR}/lib/projects.sh"
# shellcheck source=lib/temp.sh
source "${SCRIPT_DIR}/lib/temp.sh"

# ---------------------------------------------------------------------------
# Parse arguments
# ---------------------------------------------------------------------------
AGENT_TYPE=""
LAST_N=10
PROJECT_FILTER=""

_show_help() {
    echo "Usage: $(basename "$0") <agent-type> [--last N] [--project <name>]"
    echo ""
    echo "Collect and bundle reasoning-trace files from per-project training corpora."
    echo ""
    echo "Arguments:"
    echo "  <agent-type>       Role/agent name to filter on (e.g. coder, cm, tester, writer, pm)."
    echo "                     Case-insensitive; corpus directories use lowercase names."
    echo ""
    echo "Flags:"
    echo "  --last N           Emit the N most-recent traces (default: 10)."
    echo "  --project <name>   Restrict output to this project only (default: all projects)."
    echo "  --help, -h         Show this help and exit."
    echo ""
    echo "Output:"
    echo "  A markdown bundle on stdout; each trace is preceded by a header line"
    echo "  containing project, agent-type, filename, and modification timestamp."
    echo "  Pipe to a file as needed:"
    echo "    $(basename "$0") coder --last 5 > coder-traces.md"
    echo "    $(basename "$0") coder --project pgai-agent-kanban > coder-traces.md"
    echo ""
    echo "Exit codes:"
    echo "  0   Success (zero matching traces is not an error; an empty bundle is emitted)."
    echo "  1   Configuration error (missing agent-type, invalid --last value, unknown"
    echo "      --project name, or missing required environment)."
    exit 0
}

usage() {
    echo "Usage: $(basename "$0") <agent-type> [--last N] [--project <name>]" >&2
    echo "  agent-type       Role/agent name to filter on (e.g. coder, cm, tester, writer, pm)" >&2
    echo "  --last N         Number of most-recent traces to include (default: 10)" >&2
    echo "  --project <name> Restrict output to this project only (default: all projects)" >&2
    exit 1
}

_args=("$@")
_i=0
while [[ $_i -lt ${#_args[@]} ]]; do
    arg="${_args[$_i]}"
    case "$arg" in
        --help|-h)
            _show_help
            ;;
        --last)
            _next=$(( _i + 1 ))
            if [[ $_next -ge ${#_args[@]} ]]; then
                echo "ERROR: --last requires a numeric argument" >&2
                usage
            fi
            LAST_N="${_args[$_next]}"
            if ! [[ "$LAST_N" =~ ^[1-9][0-9]*$ ]]; then
                echo "ERROR: --last argument must be a positive integer, got: ${LAST_N}" >&2
                usage
            fi
            _i=$(( _next + 1 ))
            ;;
        --project)
            _next=$(( _i + 1 ))
            if [[ $_next -ge ${#_args[@]} ]]; then
                echo "ERROR: --project requires a project name argument" >&2
                usage
            fi
            PROJECT_FILTER="${_args[$_next]}"
            _i=$(( _next + 1 ))
            ;;
        --project=*)
            PROJECT_FILTER="${arg#--project=}"
            _i=$(( _i + 1 ))
            ;;
        --*)
            echo "ERROR: Unknown flag: ${arg}" >&2
            usage
            ;;
        *)
            if [[ -z "$AGENT_TYPE" ]]; then
                AGENT_TYPE="$arg"
            else
                echo "ERROR: Unexpected argument: ${arg}" >&2
                usage
            fi
            _i=$(( _i + 1 ))
            ;;
    esac
done

if [[ -z "$AGENT_TYPE" ]]; then
    echo "ERROR: agent-type is required" >&2
    usage
fi

# Normalize agent type to lowercase (corpus directories use lowercase names)
AGENT_TYPE_LOWER="${AGENT_TYPE,,}"

# ---------------------------------------------------------------------------
# Resolve kanban root
# ---------------------------------------------------------------------------
KANBAN_ROOT="${PGAI_AGENT_KANBAN_ROOT_PATH}"
export KANBAN_ROOT

# ---------------------------------------------------------------------------
# Validate --project filter when given
# ---------------------------------------------------------------------------
if [[ -n "$PROJECT_FILTER" ]]; then
    if ! projects_cfg_has "$PROJECT_FILTER"; then
        echo "ERROR: unknown project '${PROJECT_FILTER}'" >&2
        echo "       Registered projects:" >&2
        projects_cfg_list 2>/dev/null | sed 's/^/         /' >&2
        exit 1
    fi
fi

# ---------------------------------------------------------------------------
# Build the list of projects to scan
# ---------------------------------------------------------------------------
declare -a PROJECTS_TO_SCAN=()
if [[ -n "$PROJECT_FILTER" ]]; then
    PROJECTS_TO_SCAN=("$PROJECT_FILTER")
else
    while IFS= read -r _p; do
        [[ -n "$_p" ]] && PROJECTS_TO_SCAN+=("$_p")
    done < <(projects_cfg_list 2>/dev/null)
fi

# ---------------------------------------------------------------------------
# Collect (mtime, project, filepath) for trace files, across all target projects.
# Build a list sorted by recency; take the last N.
# ---------------------------------------------------------------------------
TRACE_LIST_FILE="$(pgai_mktemp collect_traces)"
trap 'rm -f "$TRACE_LIST_FILE"' EXIT

for _proj in "${PROJECTS_TO_SCAN[@]}"; do
    _proj_root="$(pp_project_root "$_proj" 2>/dev/null)" || continue
    _corpus_dir="${_proj_root}/logs/training/${AGENT_TYPE_LOWER}"
    [[ -d "$_corpus_dir" ]] || continue

    for _trace_file in "${_corpus_dir}"/*.md; do
        [[ -f "$_trace_file" ]] || continue
        mtime="$(stat -c '%Y' "$_trace_file" 2>/dev/null || stat -f '%m' "$_trace_file" 2>/dev/null || echo "0")"
        printf '%s\t%s\t%s\n' "$mtime" "$_proj" "$_trace_file" >> "$TRACE_LIST_FILE"
    done
done

# Sort by mtime (oldest first, newest last) and take the last N.
SELECTED="$(sort -k1,1n "$TRACE_LIST_FILE" | tail -n "$LAST_N")"

# ---------------------------------------------------------------------------
# Emit the bundle header
# ---------------------------------------------------------------------------
SELECTED_COUNT=0
if [[ -n "$SELECTED" ]]; then
    SELECTED_COUNT="$(printf '%s\n' "$SELECTED" | grep -c . || true)"
fi

printf '# Reasoning Trace Bundle\n'
printf '# Agent type: %s\n' "$AGENT_TYPE_LOWER"
if [[ -n "$PROJECT_FILTER" ]]; then
    printf '# Project filter: %s\n' "$PROJECT_FILTER"
else
    printf '# Projects scanned: %s\n' "${PROJECTS_TO_SCAN[*]}"
fi
printf '# Requested: last %d traces\n' "$LAST_N"
printf '# Found: %d traces\n' "$SELECTED_COUNT"
printf '# Generated: %s\n' "$(date -Iseconds)"
printf '\n'

if [[ "$SELECTED_COUNT" -eq 0 ]]; then
    if [[ -n "$PROJECT_FILTER" ]]; then
        printf '(no trace files found for agent type %s in project %s)\n' "$AGENT_TYPE_LOWER" "$PROJECT_FILTER"
    else
        printf '(no trace files found for agent type %s in any project)\n' "$AGENT_TYPE_LOWER"
    fi
    exit 0
fi

# ---------------------------------------------------------------------------
# Emit each trace with a header.
# ---------------------------------------------------------------------------
while IFS=$'\t' read -r _mtime _proj _trace_file; do
    [[ -z "$_trace_file" ]] && continue

    basename_file="$(basename "$_trace_file")"
    mtime_human="$(date -d "@${_mtime}" -Iseconds 2>/dev/null || date -r "$_mtime" -Iseconds 2>/dev/null || echo "${_mtime}")"

    # Emit section separator and header
    echo "---"
    echo ""
    printf '## Project: %s  Agent: %s  File: %s  [%s]\n\n' \
        "$_proj" "$AGENT_TYPE_LOWER" "$basename_file" "$mtime_human"

    # Emit the trace content
    cat "$_trace_file"

    # Ensure a trailing newline between sections
    printf '\n'

done <<< "$SELECTED"
