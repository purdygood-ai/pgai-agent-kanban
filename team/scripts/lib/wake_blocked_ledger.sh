#!/usr/bin/env bash
# team/scripts/lib/wake_blocked_ledger.sh
#
# Thin bash adapter over pgai_agent_kanban.metrics.blocked_ledger (the
# per-RC blocked-interval ledger) for the wake path's two HALT-skip sites in
# wake_common.sh: the fleet-scoped flag at the kanban root and the
# per-project flag under projects/<name>/.
#
# Neither call site tracks "was this already blocked last cycle" itself.
# Each wake cycle simply attempts the boundary event that matches what it
# currently observes -- HALT present -> attempt "start", HALT absent ->
# attempt "end" -- and lets the ledger module's own duplicate-open / orphaned-
# close refusals turn every attempt after the first into a silent no-op.
# That is what makes "the first cycle the HALT appears" and "the first cycle
# it's gone" fall out of the module's existing pairing invariant instead of
# needing a second piece of state here.
#
# "Silent" is literal: the ledger CLI marks that designed no-op with its own
# distinct machine error key (blocked-ledger.no-op, in its dual-channel
# stderr block) that is never written to the wake log. A write that
# genuinely failed for any other reason -- an unwritable metrics directory,
# a malformed ledger, anything else the CLI raises -- carries a different
# key (or none, if the failure happened before the CLI could even emit the
# contract) and still logs, so a real fault keeps speaking.
#
# Public entry points
# --------------------
#   pgai_stamp_halt_boundary <project_name> <source_label> <start|end>
#       Resolves *project_name*'s own Active RC via _disc_get_release_field
#       (discovery_guards.sh). With Active RC "none" or unreadable, writes
#       nothing -- per the ticket's "stamp only for the project's active RC"
#       constraint, there is no RC to attribute the interval to. Otherwise
#       calls the ledger module's CLI for *source_label* against that RC.
#       Best-effort throughout: every failure is swallowed -- CENSUS CLASS
#       CONVERT (docs/blocking-site-census.md) -- because this call rides
#       beside a HALT skip/log it must never change the outcome of. An
#       already-open start or an end with no open start (the boundary
#       already in the state this call asked for) is swallowed silently,
#       with no wake-log line, per the ledger CLI's blocked-ledger.no-op
#       error key above; a genuine failure -- an unwritable metrics
#       directory, a malformed existing ledger, anything else -- is
#       swallowed but still logged.
#
#   pgai_stamp_fleet_halt_boundary <source_label> <start|end>
#       Same as above, but calls pgai_stamp_halt_boundary for every project
#       in projects_cfg_list -- the fleet-scoped HALT blocks every project's
#       own active RC at once, so every one of them opens/closes its own
#       interval on the same wake cycle.
#
# Dependencies (must already be sourced/defined by the caller before this
# file's functions are called): log() (wake_bootstrap.sh); pp_run_ops
# (pp_run_ops.sh, sourced by wake_bootstrap.sh -- the shared cwd-independent
# module-CLI invoker the whole tree routes every pgai_agent_kanban package
# call through, per team/tests/unit/test_pp_run_ops_sweep.py's sweep
# invariant); projects_cfg_list (projects.sh); pp_project_root
# (project_paths.sh); _disc_get_release_field (discovery_guards.sh, sourced
# transitively via discovery.sh). All are already sourced by
# wake_bootstrap.sh, which wake_common.sh sources before this file. python3
# must be on PATH.

# ---------------------------------------------------------------------------
# pgai_stamp_halt_boundary <project_name> <source_label> <kind>
# ---------------------------------------------------------------------------
pgai_stamp_halt_boundary() {
  local project_name="$1" source_label="$2" kind="$3"
  local active_rc project_root ledger_path timestamp
  local _stamp_stderr _stamp_error_key

  active_rc="$(_disc_get_release_field "$project_name" "Active RC" 2>/dev/null || true)"
  if [[ -z "$active_rc" || "$active_rc" == "none" ]]; then
    return 0
  fi

  project_root="$(pp_project_root "$project_name" 2>/dev/null)" || return 0
  ledger_path="${project_root}/metrics/blocked-intervals.jsonl"
  timestamp="$(date -u +'%Y-%m-%dT%H:%M:%S+00:00')"

  # Capture stderr only (stdout discarded) so a genuine failure's
  # dual-channel error block can be inspected for its machine error key.
  # The assignment's own exit status is pp_run_ops's exit status (bash:
  # "var=$(cmd)" with nothing else on the line takes cmd's status), so the
  # success path returns before ever looking at stderr.
  _stamp_stderr="$(pp_run_ops pgai_agent_kanban.metrics.blocked_ledger \
        "$kind" "$ledger_path" "$active_rc" "$source_label" "$timestamp" 2>&1 >/dev/null)" && return 0

  # The write did not apply. The ledger CLI's dual-channel stderr block
  # names blocked-ledger.no-op when the boundary was already in the state
  # this call asked for -- the designed steady state on every ordinary
  # wake cycle -- and that case stays silent. Any other key, or no key at
  # all (the failure happened before the CLI could emit the contract),
  # is a genuine fault and still logs.
  _stamp_error_key="$(printf '%s\n' "$_stamp_stderr" | sed -n 's/^error:[[:space:]]*//p' | head -n1)"
  if [[ "$_stamp_error_key" != "blocked-ledger.no-op" ]]; then
    log "project ${project_name}: blocked-ledger ${kind} stamp for source ${source_label} did not apply (best-effort, ignored)"
  fi
  return 0
}

# ---------------------------------------------------------------------------
# pgai_stamp_fleet_halt_boundary <source_label> <kind>
# ---------------------------------------------------------------------------
pgai_stamp_fleet_halt_boundary() {
  local source_label="$1" kind="$2" _fleet_project
  while IFS= read -r _fleet_project; do
    [[ -z "$_fleet_project" ]] && continue
    pgai_stamp_halt_boundary "$_fleet_project" "$source_label" "$kind"
  done < <(projects_cfg_list 2>/dev/null)
  return 0
}
