#!/usr/bin/env bash
# team/scripts/lib/metrics_rollup_refresh.sh
#
# Refreshes a per-RC metrics rollup when a task's tokens.json lands after
# that rollup was already written.
#
# WHY THIS EXISTS
# ---------------
# The per-RC rollup (projects/<name>/metrics/rc/<version>.json) is written
# by metrics_aggregator.py from release finalize, and the aggregator counts
# only the tasks that already have an artifacts/tokens.json at that moment.
# The wake driver writes a task's tokens.json after its agent session ends,
# so the CM release task that runs finalize is, by construction, never in
# the rollup it just wrote: its usage lands minutes later, and every shipped
# RC under-counted CM by one task, with that task's tokens and wall time
# missing. Any other task attributed to an RC whose rollup already exists
# (a straggler finishing after ship) is missed the same way.
#
# WHAT IT DOES
# ------------
#   metrics_rollup_refresh_for_task <task_dir> <kanban_root> <project_name>
#
#   1. Reads "## Release Version" from <task_dir>/README.md -- the same
#      field metrics_aggregator.py attributes tasks by. No README, no
#      field, or a value of "none": nothing to do, return 0 silently.
#   2. Looks for <kanban_root>/projects/<project_name>/metrics/rc/<version>.json.
#      Absent: the RC has not been finalized yet; finalize will write the
#      rollup and it will already include this task. Return 0 silently.
#   3. Present: re-run the aggregator for that version so the rollup is
#      recomputed from every tokens.json now on disk, this task's included.
#      The existing rollup's own "workflow_type" is read and passed back
#      through --workflow-type so a document-workflow rollup is not flipped
#      to the aggregator's "release" default by the refresh.
#
# The aggregator invoked is the copy beside this file
# ("$(dirname "${BASH_SOURCE[0]}")/metrics_aggregator.py") -- each tree
# finds itself; the live install and a dev tree each run their own copy.
#
# Always returns 0. A failed refresh narrates one "[metrics] WARNING" line
# on stderr and leaves the previously written rollup in place -- the task's
# own outcome never depends on metrics succeeding, exactly as release
# finalize's own metrics step is non-blocking. All narration goes to
# stderr; the caller decides where it lands (the wake driver appends it to
# the task log).
#
# What it deliberately does NOT do:
#   - It never scans for stale rollups. Only a tokens.json written while
#     this refresh is live triggers it; rollups written earlier keep their
#     finalize-time contents (metrics_aggregator.py, "BACKFILL DECISION").
#   - It never touches metrics/history.csv. That file is append-only and
#     its writer skips an RC that already has a row, so the row appended
#     at finalize keeps the finalize-time figures (docs/OPERATIONS.md,
#     "Cumulative history.csv").
#
# Dependencies: bash 4+, awk, python3 (the aggregator), and pp_run_ops for
# the workflow_type read (pgai_agent_kanban.lib.json_field), self-sourced
# below; safe to source standalone.

if ! declare -F pp_run_ops >/dev/null 2>&1; then
  # shellcheck source=pp_run_ops.sh
  source "$(dirname "${BASH_SOURCE[0]}")/pp_run_ops.sh"
fi

# ---------------------------------------------------------------------------
# _metrics_rollup_read_release_version <readme_path>
#
# Prints the value under "## Release Version" in <readme_path>, or nothing.
# Liberal in what it accepts: the heading may carry trailing whitespace,
# any number of blank lines may separate it from its value, and the value
# is trimmed of surrounding whitespace (including a CR). The search ends at
# the next "#"-prefixed line, so an empty field never reads the next
# section's first line as its value.
# ---------------------------------------------------------------------------
_metrics_rollup_read_release_version() {
  local readme_path="$1"
  awk '
    found {
      if ($0 ~ /^[[:space:]]*#/) exit
      if (!NF) next
      sub(/^[[:space:]]+/, ""); sub(/[[:space:]]+$/, "")
      print; exit
    }
    /^[[:space:]]*##[[:space:]]+Release[[:space:]]+Version[[:space:]]*$/ { found = 1 }
  ' "$readme_path" 2>/dev/null
}

# ---------------------------------------------------------------------------
# metrics_rollup_refresh_for_task <task_dir> <kanban_root> <project_name>
#
# See the file header for the contract. Prints narration to stderr only;
# stdout stays empty. Always returns 0.
# ---------------------------------------------------------------------------
metrics_rollup_refresh_for_task() {
  local task_dir="${1:-}"
  local kanban_root="${2:-}"
  local project_name="${3:-}"

  if [[ -z "$task_dir" || -z "$kanban_root" || -z "$project_name" ]]; then
    echo "[metrics] rollup refresh skipped: task_dir='${task_dir}' kanban_root='${kanban_root}' project='${project_name}' -- all three are required" >&2
    return 0
  fi

  local readme="${task_dir}/README.md"
  [[ -f "$readme" ]] || return 0

  local version
  version="$(_metrics_rollup_read_release_version "$readme")"
  if [[ -z "$version" || "$version" == "none" ]]; then
    return 0
  fi
  [[ "$version" == v* ]] || version="v${version}"

  local rollup="${kanban_root}/projects/${project_name}/metrics/rc/${version}.json"
  [[ -f "$rollup" ]] || return 0

  local aggregator
  aggregator="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/metrics_aggregator.py"
  if [[ ! -f "$aggregator" ]]; then
    echo "[metrics] WARNING: rollup refresh for ${version} skipped: aggregator not found at ${aggregator}; ${rollup} keeps its finalize-time contents" >&2
    return 0
  fi

  # Preserve the rollup's own workflow_type across the refresh; a rollup
  # whose field cannot be read is refreshed under the aggregator's default.
  local workflow_type
  workflow_type="$(pp_run_ops pgai_agent_kanban.lib.json_field --file --string-only --default release \
    "$rollup" workflow_type 2>/dev/null)" || workflow_type=""
  [[ -n "$workflow_type" ]] || workflow_type="release"

  echo "[metrics] rollup refresh: $(basename "$task_dir") landed tokens.json for ${version}; recomputing ${rollup}" >&2

  local refresh_rc=0
  (
    set +e
    python3 "$aggregator" \
      --project       "$project_name" \
      --rc            "$version" \
      --kanban-root   "$kanban_root" \
      --workflow-type "$workflow_type" \
      2>&1 | sed 's/^/[metrics] /' >&2
    exit "${PIPESTATUS[0]}"
  ) || refresh_rc=$?

  if [[ $refresh_rc -ne 0 ]]; then
    # CENSUS CLASS: CONVERT (docs/blocking-site-census.md) -- a refresh that
    # fails is a metrics-quality finding. The rollup written at finalize is
    # still on disk and still valid; the task's result is unaffected.
    echo "[metrics] WARNING: rollup refresh for ${version} exited with code ${refresh_rc}; ${rollup} keeps its finalize-time contents. Re-run by hand: python3 ${aggregator} --project ${project_name} --rc ${version} --kanban-root ${kanban_root} --workflow-type ${workflow_type}" >&2
  fi
  return 0
}
