#!/usr/bin/env bash
# team/scripts/lib/wake_blocked_ledger.sh
#
# Thin bash adapter over pgai_agent_kanban.metrics.blocked_ledger (the
# per-project block ledger) for the wake path's two HALT-skip sites in
# wake_common.sh: the fleet-scoped flag at the kanban root and the
# per-project flag under projects/<name>/.
#
# This is the one place every project and fleet HALT is recorded, whatever
# wrote it (an operator, a CM release trigger, OVERWATCH, the zero-progress
# cap, the substrate): the wake observes the HALT file itself rather than
# each writer stamping its own.
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
# stderr block) that is never written to the wake log nor recorded in any
# refusal ledger. A genuine refusal is recorded in the refusal ledger of
# the project being stamped (passed as --project), never the ambient one.
# A write that genuinely failed for any other reason -- an unwritable
# metrics directory, a malformed ledger, anything else the CLI raises --
# carries a different key (or none, if the failure happened before the CLI
# could even emit the contract) and still logs, so a real fault keeps
# speaking.
#
# What each interval records
# --------------------------
#   version      the project's Active RC; "no-rc" (the ledger's
#                NO_RC_VERSION) when the Active RC is "none" or unreadable,
#                so a HALT seen between releases is recorded too. "no-rc" is
#                never a vX.Y.Z version, so no per-RC blocked-minutes total
#                ever counts it.
#   cause        on the block-start: "operator" or "chain", from the
#                sentinel-writes log through the HALT cause helper
#                ("pp_run_ops pgai_agent_kanban.lib.sentinel_provenance
#                cause <HALT>"). A project HALT with no
#                operator record (halt, halt_after promote, ...) reads
#                "chain"; the fleet-scoped HALT is always "operator". Omitted
#                (reads "chain") when the helper cannot answer.
#   reason_code  on the block-start: the HALT file's own "error:" key when
#                it has one (CM's HALT file carries it as "# error:   KEY";
#                a leading "#" and any spacing are accepted), otherwise the
#                fixed label for the source: "halt_project" or
#                "halt_global". On the block-end: "halt_cleared".
#   class        the block-end is class "any": a HALT this boundary sees go
#                away was removed by something other than unhalt.sh (an
#                OVERWATCH resolution, CM clearing its own), because
#                unhalt.sh closes its own "halt:project" block first, class
#                human, and the wake's later end attempt is then a no-op.
#
# An operator HALT stays recorded (cause operator) because it drives the
# per-RC blocked minutes; the reliability streaks exclude it by its cause.
#
# Closing across an RC change
# ---------------------------
# A start is attempted under the version the wake sees on that cycle, so a
# HALT that spans an RC opening opens one interval under "no-rc" and a
# second under the new RC from the first cycle the RC is seen -- exactly the
# RC interval this adapter wrote before it recorded "no-rc" at all. An "end"
# therefore closes the source under the current version AND under the other
# attribution: "no-rc" while an RC is active, the project's "Last Released"
# version while none is. Before each end call the ledger file is checked for
# a line naming the source (and that version); with none there is nothing
# that could be open, the Python call is skipped, and an ordinary HALT-free
# wake cycle for a project with no HALT history costs no Python start at all.
#
# Public entry points
# --------------------
#   pgai_stamp_halt_boundary <project_name> <source_label> <start|end> [<cause> [<reason_code>]]
#       Stamps *source_label* ("halt:project" or "halt:global") for
#       *project_name* as described above. *cause* and *reason_code*, when
#       given, are used for a start instead of being read from the HALT
#       file (the fleet entry point reads the root HALT once and passes
#       both down). Best-effort throughout: every failure is swallowed --
#       CENSUS CLASS CONVERT (docs/blocking-site-census.md) -- because this
#       call rides beside a HALT skip/log it must never change the outcome
#       of. An already-open start or an end with no open start (the
#       boundary already in the state this call asked for) is swallowed
#       silently, with no wake-log line, per the ledger CLI's
#       blocked-ledger.no-op error key above; a genuine failure -- an
#       unwritable metrics directory, a malformed existing ledger, anything
#       else -- is swallowed but still logged. Always returns 0.
#
#   pgai_stamp_fleet_halt_boundary <source_label> <start|end>
#       Same as above, but calls pgai_stamp_halt_boundary for every project
#       in projects_cfg_list -- the fleet-scoped HALT blocks every project
#       at once, so every one of them opens/closes its own interval on the
#       same wake cycle. For a start, the root HALT's cause and reason_code
#       are read once and passed to every project.
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

# The ledger's version for a block seen while the Active RC is "none"
# (pgai_agent_kanban.metrics.blocked_ledger NO_RC_VERSION).
_PGAI_LEDGER_NO_RC_VERSION="no-rc"

# ---------------------------------------------------------------------------
# _pgai_halt_file_for_source <project_root> <source_label>
#   The HALT file a source observes: <kanban_root>/HALT for "halt:global",
#   <project_root>/HALT otherwise. <project_root> is
#   <kanban_root>/projects/<name>, as pp_project_root returns it.
# ---------------------------------------------------------------------------
_pgai_halt_file_for_source() {
  local project_root="$1" source_label="$2"
  if [[ "$source_label" == "halt:global" ]]; then
    printf '%s/HALT\n' "$(dirname "$(dirname "$project_root")")"
  else
    printf '%s/HALT\n' "$project_root"
  fi
}

# ---------------------------------------------------------------------------
# _pgai_halt_cause <halt_file>
#   "operator" or "chain" from the HALT cause helper; empty when the helper
#   could not answer (the caller then omits --cause, which reads "chain").
#   The kanban root is derived from the HALT file's own directory by the
#   helper.
# ---------------------------------------------------------------------------
_pgai_halt_cause() {
  local halt_file="$1" cause
  cause="$(pp_run_ops pgai_agent_kanban.lib.sentinel_provenance cause "$halt_file" 2>/dev/null)" || cause=""
  case "$cause" in
    operator|chain) printf '%s\n' "$cause" ;;
    *) printf '\n' ;;
  esac
}

# ---------------------------------------------------------------------------
# _pgai_halt_reason_code <halt_file> <source_label>
#   The HALT file's first "error:" key (leading "#" and spacing tolerated),
#   or the source's fixed label ("halt:project" -> "halt_project").
# ---------------------------------------------------------------------------
_pgai_halt_reason_code() {
  local halt_file="$1" source_label="$2" key=""
  if [[ -r "$halt_file" ]]; then
    key="$(sed -n -E 's/^[#[:space:]]*error:[[:space:]]*([^[:space:]]+).*/\1/p' "$halt_file" 2>/dev/null | head -n1)"
  fi
  if [[ -n "$key" ]]; then
    printf '%s\n' "$key"
  else
    printf '%s\n' "${source_label//:/_}"
  fi
}

# ---------------------------------------------------------------------------
# _pgai_ledger_names <ledger_path> <source_label> [<version>]
#   True when some ledger line names *source_label* (and *version*, when
#   given). A cheap pre-check only: no such line means nothing for that
#   source could be open, so an end call would be a certain no-op. The
#   ledger module still decides every write.
# ---------------------------------------------------------------------------
_pgai_ledger_names() {
  local ledger_path="$1" source_label="$2" version="${3:-}"
  [[ -f "$ledger_path" ]] || return 1
  local lines
  lines="$(grep -F -- "\"${source_label}\"" "$ledger_path" 2>/dev/null)" || return 1
  [[ -z "$version" ]] && return 0
  grep -qF -- "\"${version}\"" <<< "$lines"
}

# ---------------------------------------------------------------------------
# _pgai_ledger_stamp <project_name> <kind> <ledger_path> <version> <source_label> <timestamp> [ledger CLI options...]
#   One ledger CLI call; silent on success and on blocked-ledger.no-op,
#   one wake-log line on any other failure.
# ---------------------------------------------------------------------------
_pgai_ledger_stamp() {
  local project_name="$1" kind="$2" ledger_path="$3" version="$4" source_label="$5" timestamp="$6"
  shift 6
  local _stamp_stderr _stamp_error_key

  # Capture stderr only (stdout discarded) so a genuine failure's
  # dual-channel error block can be inspected for its machine error key.
  # The assignment's own exit status is pp_run_ops's exit status (bash:
  # "var=$(cmd)" with nothing else on the line takes cmd's status), so the
  # success path returns before ever looking at stderr.
  # --project names the project being stamped, so a genuine refusal is
  # recorded in its own refusal ledger rather than the ambient project's.
  _stamp_stderr="$(pp_run_ops pgai_agent_kanban.metrics.blocked_ledger \
        "$kind" "$ledger_path" "$version" "$source_label" "$timestamp" \
        "$@" --project "$project_name" 2>&1 >/dev/null)" && return 0

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
# pgai_stamp_halt_boundary <project_name> <source_label> <kind> [<cause> [<reason_code>]]
# ---------------------------------------------------------------------------
pgai_stamp_halt_boundary() {
  local project_name="$1" source_label="$2" kind="$3"
  local cause="${4-}" reason_code="${5-}"
  local active_rc version project_root ledger_path timestamp halt_file
  local other_version _end_version

  project_root="$(pp_project_root "$project_name" 2>/dev/null)" || return 0
  [[ -n "$project_root" ]] || return 0
  ledger_path="${project_root}/metrics/blocked-intervals.jsonl"
  timestamp="$(date -u +'%Y-%m-%dT%H:%M:%S+00:00')"

  active_rc="$(_disc_get_release_field "$project_name" "Active RC" 2>/dev/null || true)"
  active_rc="${active_rc//[[:space:]]/}"
  if [[ -z "$active_rc" || "${active_rc,,}" == "none" ]]; then
    version="$_PGAI_LEDGER_NO_RC_VERSION"
  else
    version="$active_rc"
  fi

  if [[ "$kind" == "start" ]]; then
    halt_file="$(_pgai_halt_file_for_source "$project_root" "$source_label")"
    [[ -n "$cause" ]] || cause="$(_pgai_halt_cause "$halt_file")"
    [[ -n "$reason_code" ]] || reason_code="$(_pgai_halt_reason_code "$halt_file" "$source_label")"
    local -a _opts=(--reason-code "$reason_code")
    [[ -n "$cause" ]] && _opts+=(--cause "$cause")
    _pgai_ledger_stamp "$project_name" start "$ledger_path" "$version" "$source_label" "$timestamp" "${_opts[@]}"
    return 0
  fi

  # end: nothing can be open for a source the ledger never names.
  _pgai_ledger_names "$ledger_path" "$source_label" || return 0
  if [[ "$version" == "$_PGAI_LEDGER_NO_RC_VERSION" ]]; then
    other_version="$(_disc_get_release_field "$project_name" "Last Released" 2>/dev/null || true)"
    other_version="${other_version//[[:space:]]/}"
    [[ "${other_version,,}" == "none" ]] && other_version=""
  else
    other_version="$_PGAI_LEDGER_NO_RC_VERSION"
  fi
  for _end_version in "$version" "$other_version"; do
    [[ -n "$_end_version" ]] || continue
    _pgai_ledger_names "$ledger_path" "$source_label" "$_end_version" || continue
    _pgai_ledger_stamp "$project_name" "$kind" "$ledger_path" "$_end_version" "$source_label" "$timestamp" \
      --class any --reason-code halt_cleared
  done
  return 0
}

# ---------------------------------------------------------------------------
# pgai_stamp_fleet_halt_boundary <source_label> <kind>
# ---------------------------------------------------------------------------
pgai_stamp_fleet_halt_boundary() {
  local source_label="$1" kind="$2" _fleet_project
  local _fleet_cause="" _fleet_reason="" _fleet_read=0 _fleet_root _fleet_halt
  while IFS= read -r _fleet_project; do
    [[ -z "$_fleet_project" ]] && continue
    if [[ "$kind" == "start" && "$_fleet_read" == 0 ]]; then
      # The root HALT is the same file for every project: read its cause
      # and reason_code once.
      _fleet_read=1
      if _fleet_root="$(pp_project_root "$_fleet_project" 2>/dev/null)" && [[ -n "$_fleet_root" ]]; then
        _fleet_halt="$(_pgai_halt_file_for_source "$_fleet_root" "$source_label")"
        _fleet_cause="$(_pgai_halt_cause "$_fleet_halt")"
        _fleet_reason="$(_pgai_halt_reason_code "$_fleet_halt" "$source_label")"
      fi
    fi
    pgai_stamp_halt_boundary "$_fleet_project" "$source_label" "$kind" "$_fleet_cause" "$_fleet_reason"
  done < <(projects_cfg_list 2>/dev/null)
  return 0
}
