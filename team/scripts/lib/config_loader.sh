#!/usr/bin/env bash
# team/scripts/lib/config_loader.sh
#
# Single source of truth for every kanban.cfg key consumed by the wake chain.
#
# Usage
# -----
#   source "$(dirname "${BASH_SOURCE[0]}")/config_loader.sh"
#   load_config /path/to/kanban.cfg          # validate and export
#   config_get paths dev_tree_path           # optional explicit accessor
#   unset "${CONFIG_LOADER_WAKE_ENV_NAMES[@]}"  # drop inherited [wake] exports
#                                               # (see that array's comment)
#
# Safety invariants
# -----------------
#   - No top-level side effects when sourced — only function definitions.
#   - load_config reads the file once, validates, then exports env vars.
#   - config_get performs a single read_ini call against the already-validated
#     config path (set by load_config), or against an explicit path argument.
#   - Failure messages name the missing key AND the config file path.
#   - This module depends on ini_parser.sh (read_ini) being sourced already,
#     OR it sources ini_parser.sh from the same directory if needed.
#
# Key Registry
# ------------
# Every kanban.cfg key known to the wake chain is listed here — not only the
# keys load_config() itself reads via read_ini below, but every kanban.cfg
# key any file in the wake chain reads via read_ini or config_get, so this
# block is the accepted set for the WHOLE key, not just this function's own
# reads. load_config() checks every section and key in the file against
# this set (its machine-readable twin is _CONFIG_LOADER_KNOWN_KEYS below)
# and WARNS, naming the nearest known name, on anything outside it — a
# misspelled key would otherwise fall back to its default without a trace.
# A key an earlier kanban.cfg_example seeded but nothing reads any more is
# not listed here: it lives in _CONFIG_LOADER_RETIRED_KEYS below and draws
# a one-line NOTE naming deletion as the remedy instead of that warning.
# A warning never refuses: a typo must not stop a wake. Add a new key to
# BOTH the comment block and the array in the same change; the unit test
# for this module fails when they disagree. (project.cfg carries a distinct
# key set — [project], [versioning],
# [hooks], [debug], [training] — documented in project_paths.sh's own Key
# Registry instead; a [release] key is the one section genuinely shared by
# both files, per that section's own header note below.)
# Status: REQUIRED = fail loud + non-zero if absent or empty.
#         OPTIONAL = apply default if absent; never fails.
# Default shown is the canonical single default for that key.
#
# [paths]
#   dev_tree_path           OPTIONAL  ""  (install-time convenience and fallback
#                                          for projects whose project.cfg omits
#                                          dev_tree_path; an install may leave
#                                          this empty)
#   tmp_root                OPTIONAL  /tmp
#   tmp_subdir              OPTIONAL  pgai_kanban_tmp
#   cleanup_retention_days  OPTIONAL  30  (DEPRECATED spelling of [cleanup]
#                                          retention_days below, read by
#                                          config_cleanup_tunables for
#                                          team/scripts/cleanup/cleanup.sh only
#                                          when that key is absent or blank;
#                                          whenever it is present it draws one
#                                          deprecation WARNING per run)
#   worktree_base           OPTIONAL  ""  (read by team/scripts/lib/worktree.sh
#                                          via read_ini; absolute parent
#                                          directory for task worktrees. Empty =
#                                          <temp root>/worktrees)
#
# [providers]
#   ai_auth_mode            OPTIONAL  oauth
#   active                  OPTIONAL  claude
#   available               OPTIONAL  claude codex
#   llm_thinking_enabled    OPTIONAL  true  (read by
#                                            team/scripts/lib/provider_claude.sh
#                                            via config_get; controls the
#                                            provider's extended-thinking flag)
#   llm_effort              OPTIONAL  ""  (global reasoning-effort level; resolved
#                                          per role into PGAI_<ROLE>_EFFORT using
#                                          the same precedence tier and narration-
#                                          source discipline as PGAI_<ROLE>_MODEL —
#                                          see resolve_role_effort() below. Valid
#                                          values are the active provider's own
#                                          list (PROVIDER_EFFORT_VALUES in its
#                                          provider_<name>.sh); a provider that
#                                          declares no list passes any value
#                                          through unvalidated, narrated on
#                                          stderr. Absent
#                                          everywhere = no effort resolved, nothing
#                                          exported, byte-identical to today.)
#
# [chain]
#   pm_mode                 OPTIONAL  automatic
#
# [dashboard]
#   rows_per_column         OPTIONAL  21
#   min_rows_per_column     OPTIONAL  13
#   max_rows_per_column     OPTIONAL  34
#   min_rows_per_project    OPTIONAL  3
#   max_rows_per_project    OPTIONAL  8
#   max_rows                OPTIONAL  20
#   dashboard_status_glyph  OPTIONAL  ■  (single visible character; multi-char values
#                                         are rejected non-zero naming the key)
#   snapshot_ttl_seconds    OPTIONAL  5  (read by
#                                         team/scripts/dashboard/lib/snapshot.sh
#                                         via read_ini, not by load_config()
#                                         below; staleness bound for the
#                                         per-tick render snapshot cache)
#   event_band_read_bound_lines  OPTIONAL  20000  (read by
#                                         team/scripts/dashboard/event-band.sh
#                                         via read_ini, not by load_config()
#                                         below; per-agent cron-log tail-read
#                                         bound, in lines, for the schedule
#                                         window's event band)
#   session_name            OPTIONAL  pgai-kanban-dashboard  (read by
#                                         team/scripts/dashboard/create.sh and
#                                         kill.sh via read_ini; tmux session
#                                         name for the dashboard)
#   refresh_seconds         OPTIONAL  5  (read by team/scripts/dashboard/create.sh
#                                         via read_ini; pane refresh cadence)
#   color_done              OPTIONAL  green  (this and the eight color_* keys
#                                         below are read by the
#                                         team/scripts/dashboard/ show-*.sh and
#                                         queue-pane.sh renderers via read_ini:
#                                         tmux colour names for the status and
#                                         label text they draw)
#   color_working           OPTIONAL  yellow
#   color_blocked           OPTIONAL  red
#   color_waiting           OPTIONAL  yellow
#   color_label             OPTIONAL  cyan
#   color_dim               OPTIONAL  white
#   color_header            OPTIONAL  cyan
#   color_halt              OPTIONAL  red
#   color_ok                OPTIONAL  green
#
# [docker]
#   docker_exe              OPTIONAL  docker  (read by
#                                              team/scripts/lib/docker_engine.sh
#                                              via read_ini; binary the
#                                              container launcher invokes)
#   container_name          OPTIONAL  pgai-kanban  (read by
#                                              team/scripts/container-up.sh,
#                                              container-down.sh and
#                                              container-status.sh via
#                                              read_ini; name of the
#                                              long-running kanban container.
#                                              Each script's --name flag
#                                              overrides it)
#   image                   OPTIONAL  pgai-kanban:latest  (read by
#                                              team/scripts/container-up.sh
#                                              via read_ini; image the
#                                              container runs, built by
#                                              docker/build.sh. The
#                                              --image-tag flag overrides it)
#   container_stop_timeout_seconds  OPTIONAL  1800  (read by
#                                              team/scripts/container-down.sh
#                                              via read_ini; seconds to wait
#                                              for running agent sessions
#                                              before stopping the container.
#                                              The --now flag skips the wait)
#
# [api]
#   host                    OPTIONAL  127.0.0.1  (read by
#                                                 team/pgai_agent_kanban/api/config.py;
#                                                 must be a loopback address)
#   port                    OPTIONAL  8300  (read by
#                                            team/pgai_agent_kanban/api/config.py)
#   shell_out_timeout_seconds  OPTIONAL  300  (seconds a route waits for an
#                                            operator script before stopping
#                                            it and answering HTTP 504; read
#                                            by team/pgai_agent_kanban/api/config.py)
#
# [governance]
#   budget_bytes_coder      OPTIONAL  77824  (read by
#                                             team/scripts/lint_governance_byte_budgets.sh
#                                             via read_ini; per-role byte
#                                             ceiling for the assembled
#                                             read-order catalog. Absent =
#                                             "unbudgeted", not enforced —
#                                             the shipped value above is the
#                                             current re-baselined ceiling,
#                                             not a load-time fallback.)
#   budget_bytes_tester     OPTIONAL  118784
#   budget_bytes_po         OPTIONAL  73728
#   budget_bytes_pm         OPTIONAL  90112
#   budget_bytes_writer     OPTIONAL  77824
#   budget_bytes_cm         OPTIONAL  102400
#   budget_bytes_overwatch  OPTIONAL  90112
#
# [wake]
#   max_tasks_per_wake      OPTIONAL  5
#   max_runtime_seconds     OPTIONAL  14400
#   pause_between_tasks     OPTIONAL  5
#   stop_on_blocked         OPTIONAL  true
#   stop_file               OPTIONAL  ${PGAI_AGENT_KANBAN_TEMP_DIR}/wakeup/stop
#   pm_max_tasks            OPTIONAL  15
#   max_task_seconds        OPTIONAL  5400
#   max_project_seconds     OPTIONAL  14400  (per-project runtime cap; 0 = disabled)
#   kill_grace_seconds      OPTIONAL  30
#   transient_retry_cap     OPTIONAL  3  (single ceiling for the durable
#                                         ## Transient Retry record; see
#                                         transient_retry_limit in
#                                         overwatch_protocol.sh)
#   zero_progress_cap       OPTIONAL  3  (single ceiling for the durable
#                                         ## Zero Progress record — a SEPARATE
#                                         counter from transient_retry_cap
#                                         above; see zero_progress_cap() in
#                                         overwatch_protocol.sh. Read at
#                                         load_config() below; previously
#                                         missing from this registry despite
#                                         being read there.)
#   block_storm_window      OPTIONAL  3600  (seconds back from now; two
#                                         or more human-gated blocks
#                                         sharing one reason_code inside it
#                                         count as a systemic block storm
#                                         that gates a project's dispatch.
#                                         Exported as BLOCK_STORM_WINDOW by
#                                         load_config() below.)
#   block_hold_cap          OPTIONAL  3600  (seconds; the longest any
#                                         blocked-task dispatch hold lasts
#                                         from the block that began it.
#                                         Exported as BLOCK_HOLD_CAP by
#                                         load_config() below.)
#   usage_limit_pause_seconds  OPTIONAL  3600  (seconds dispatch to a
#                                         provider pauses fleet-wide after
#                                         its usage limit, when the
#                                         provider's output states no reset
#                                         time. Exported as
#                                         USAGE_LIMIT_PAUSE_SECONDS by
#                                         load_config() below.)
#   systemic_block_reasons  OPTIONAL  invalid_effort_config,workflow_type_unparseable,plugin_load_failed,branch_prefix_failed,invalid_assignment  (comma- or
#                                         space-separated Block Record
#                                         reason codes; one human-gated
#                                         block carrying any of them gates a
#                                         project's dispatch on its own.
#                                         Blank or absent means the default
#                                         list, owned by
#                                         pgai_agent_kanban.overwatch.
#                                         block_storm; "none" means no code.
#                                         An unknown code is warned about
#                                         and ignored, never refused.
#                                         Exported as SYSTEMIC_BLOCK_REASONS
#                                         by load_config() below.)
#   lock_wait_seconds       OPTIONAL  120  (seconds a wake waits, with
#                                         backoff, for a repo lock a wake
#                                         chain holds before it defers;
#                                         0 = one attempt. Exported as
#                                         LOCK_WAIT_SECONDS; read by
#                                         lib/wake_lock_wait.sh.)
#   lock_starvation_firings  OPTIONAL  3  (consecutive firings a role may
#                                         defer on repo-lock contention
#                                         with a BACKLOG task before the
#                                         starvation warning, after which
#                                         PM wakes skip discovery while
#                                         that task is ready; 0 = guard
#                                         off. Exported as
#                                         LOCK_STARVATION_FIRINGS.)
#   model_preflight_ttl_seconds  OPTIONAL  3600  (seconds a codex model
#                                         preflight PASS is remembered per
#                                         model; 0 = check every firing
#                                         with a task to dispatch. Exported
#                                         as MODEL_PREFLIGHT_TTL_SECONDS;
#                                         read by lib/provider_codex.sh.)
#   model_preflight_timeout_seconds  OPTIONAL  60  (seconds the codex model
#                                         preflight call may run before it
#                                         is stopped and fails; 0 = no
#                                         bound. Exported as
#                                         MODEL_PREFLIGHT_TIMEOUT_SECONDS;
#                                         read by lib/provider_codex.sh.)
#
# [models.claude]   — all OPTIONAL, default "" unless a line names a shipped value
#   default                               — shipped value "claude-sonnet-5-5";
#                                           fallback model for any unset role.
#                                           kanban.cfg_example ships the value;
#                                           absent or empty, the provider CLI
#                                           picks its own model (not a
#                                           load-time fallback).
#   pm, coder, writer, tester, cm, po    — per-role overrides
# [models.codex]    — all OPTIONAL, default "" unless a line names a shipped value
#   default                               — shipped value "gpt-6.1-sol";
#                                           fallback model for any unset role,
#                                           shipped and read when absent the
#                                           same way as the claude default.
#   pm, coder, writer, tester, cm, po    — per-role overrides
#
# [cleanup]  — resolved for team/scripts/cleanup/cleanup.sh by
#              config_cleanup_tunables (environment over cfg)
#   retention_days          OPTIONAL  30
#   trivial_log_bytes       OPTIONAL  1700
#   trivial_log_hours       OPTIONAL  6
#
# [release]  — deferral-posture keys. Shared by kanban.cfg
#              (framework-wide default) and a project's own project.cfg
#              (per-project override); both cfg formats are read through
#              this same registry. Booleans are spelled true/false ONLY —
#              'on'/'off' refuse loud naming the accepted set rather than
#              resolving silently into a posture nobody asked for. An
#              absent key takes its documented default and narrates that
#              fact once per run, not once per read.
#   deferred_bugs           OPTIONAL  false  (true|false — master switch:
#                                              whether bugs are deferred at
#                                              all. false = every bug opens
#                                              its own RC as today.)
#   deferred_bug_level      OPTIONAL  low    (low|medium|high — defers every
#                                              bug AT OR BELOW this severity;
#                                              a bug ABOVE the level still
#                                              triggers an RC normally. high
#                                              defers ALL bugs, which means
#                                              no bug can ever trigger an RC
#                                              on its own while level=high —
#                                              bugs run only once deferral
#                                              is lowered or lifted.)
#   defer_piggyback         OPTIONAL  true   (true|false — when a bug ABOVE
#                                              the level triggers an RC,
#                                              whether every currently-
#                                              deferred bug rides along with
#                                              it. false = the triggering
#                                              bug ships alone.)
#   deep_verify             OPTIONAL  weekly (weekly|off — the DEEP-VERIFY
#                                              scheduled-dispatch cadence.
#                                              Validated by load_config()
#                                              but NOT exported: read by
#                                              pp_deep_verify_mode() in
#                                              project_paths.sh, which
#                                              checks project.cfg first,
#                                              then this kanban.cfg value,
#                                              then defaults to weekly.
#                                              EXCEPTION to the
#                                              boolean-strictness rule
#                                              above: true|false are ALSO
#                                              accepted as aliases —
#                                              true=weekly, false=off — so
#                                              this key alone tolerates both
#                                              vocabularies rather than
#                                              wedging a cfg file already
#                                              shipped with one spelling.)
#   max_rc_retries          OPTIONAL  3  (NOT read/validated by load_config()
#                                         below — read by pp_max_rc_retries()
#                                         in team/scripts/lib/project_paths.sh,
#                                         which checks project.cfg first, then
#                                         falls back to this kanban.cfg value,
#                                         and fails loud only when the key is
#                                         absent from BOTH. Listed here anyway
#                                         because [release] is the one section
#                                         genuinely shared by both cfg files —
#                                         see the section header above.)
#   deep_verify_offhours_window  OPTIONAL  03:00-03:59  (HH:MM-HH:MM UTC; NOT
#                                         read/validated by load_config() —
#                                         read by pp_deep_verify_offhours_
#                                         window() in project_paths.sh, which
#                                         checks project.cfg first, then this
#                                         kanban.cfg value, then this
#                                         default. A malformed value counts
#                                         as absent at its tier, with one
#                                         warning, rather than refusing — a
#                                         scheduling nicety, not an invariant.)
#   overwatch_resolve       OPTIONAL  false (NOT read/validated by
#                                         load_config() below — read by
#                                         pp_overwatch_resolve() in
#                                         team/scripts/lib/project_paths.sh,
#                                         which checks project.cfg first,
#                                         then falls back to this kanban.cfg
#                                         value. Unlike max_rc_retries, absent
#                                         from BOTH resolves to 'false' —
#                                         never an error, never 'true'. Only
#                                         an unrecognized non-boolean value
#                                         refuses, dual-channel. Listed here
#                                         anyway because [release] is the one
#                                         section genuinely shared by both cfg
#                                         files — see the section header
#                                         above.)
#   ship_posture            OPTIONAL  matrix (NOT read/validated by
#                                         load_config() below — read by
#                                         pp_ship_posture() in
#                                         team/scripts/lib/project_paths.sh
#                                         and its Python counterpart
#                                         resolve_ship_posture() in
#                                         team/pgai_agent_kanban/overwatch/
#                                         resolution_matrix.py, which check
#                                         project.cfg first, then fall back
#                                         to this kanban.cfg value.
#                                         matrix|always: matrix applies the
#                                         ship-policy matrix as written;
#                                         always ships through the triggers
#                                         the posture covers and records
#                                         what they would have stopped on.
#                                         Absent or blank in BOTH resolves
#                                         to 'matrix'. An unrecognized value
#                                         also resolves to 'matrix' with one
#                                         warning line — never a refusal.)
#   ship_posture_confirm_remote  OPTIONAL  false (NOT read/validated by
#                                         load_config() below — read by the
#                                         same two resolvers through the same
#                                         project.cfg-then-kanban.cfg chain.
#                                         On a push_to_remote = true lane,
#                                         ship_posture = always takes effect
#                                         only when this is exactly 'true';
#                                         anything else resolves the posture
#                                         to 'matrix' with one warning line.
#                                         Not consulted on a push_to_remote
#                                         = false lane.)
#   container_images        OPTIONAL  ""  (NOT read/validated by
#                                         load_config() below — read from
#                                         kanban.cfg only by
#                                         team/scripts/lib/container_tier.sh
#                                         when an operator runs a
#                                         container-tier harness without
#                                         --image. Image names from
#                                         team/config/container-images.txt,
#                                         separated by commas or spaces, or
#                                         "all". Empty or absent: the
#                                         harness lists the images and
#                                         builds nothing. An undeclared name
#                                         is refused by the harness.)
#
# [locks]  — the second section shared by kanban.cfg (framework-wide
#            default) and a project's own project.cfg (per-project
#            override), alongside [release] above.
#   stale_lock_reap_after_wake_periods  OPTIONAL  3  (NOT read/validated by
#                                         load_config() below — read by
#                                         pp_stale_lock_reap_after_wake_periods()
#                                         in team/scripts/lib/project_paths.sh,
#                                         which checks project.cfg first, then
#                                         falls back to this kanban.cfg value,
#                                         defaulting to 3 when absent from
#                                         both. Consumed by
#                                         team/scripts/lib/lock_provenance.sh's
#                                         pgai_lock_acquire contention-path
#                                         reaper: a contended lock older than
#                                         this many wake periods is reaped
#                                         purely on age, independent of its
#                                         recorded holder's pid liveness. Also
#                                         read by the lane verdict's
#                                         STUCK-LOCK correlation
#                                         (pgai_agent_kanban.ops.locks.
#                                         project_stale_age_threshold, the
#                                         Python form of that file's
#                                         pgai_lock_stale_age_threshold_seconds)
#                                         -- the same key, never a second
#                                         threshold.)
#
# [overwatch]  — the third section shared by kanban.cfg (framework-wide
#                default) and a project's own project.cfg (per-project
#                override), alongside [release] and [locks] above.
#   block_triage            OPTIONAL  true  (NOT read/validated by
#                                         load_config() below — read by
#                                         pp_block_triage() in
#                                         team/scripts/lib/project_paths.sh
#                                         and its Python counterpart
#                                         resolve_block_triage() in
#                                         team/pgai_agent_kanban/overwatch/
#                                         resolution_matrix.py, which check
#                                         project.cfg first, then fall back
#                                         to this kanban.cfg value. true|false
#                                         only: true lets OVERWATCH's
#                                         block-triage sweep act on a Block
#                                         Record; false keeps it to writing
#                                         the record and its diagnosis.
#                                         Absent or blank in BOTH resolves to
#                                         'true'; an unrecognized value
#                                         refuses, dual-channel.)
#   requeue_budget          OPTIONAL  3     (NOT read/validated by
#                                         load_config() below — read by
#                                         resolve_requeue_budget() in
#                                         team/pgai_agent_kanban/overwatch/
#                                         block_requeue.py, which checks
#                                         project.cfg first, then falls
#                                         back to this kanban.cfg value,
#                                         then to 3. How many times block
#                                         triage may requeue one task over
#                                         the life of its Block Record,
#                                         across block reasons and epochs,
#                                         before it escalates instead. A
#                                         non-negative whole number; any
#                                         other value refuses, dual-channel.)
#   leaked_listener_min_age_seconds OPTIONAL 3600 (NOT read/validated by
#                                         load_config() below — read by
#                                         _cll_min_age_seconds() in
#                                         team/scripts/lib/overwatch-checks/
#                                         check-leaked-listeners.sh, from
#                                         kanban.cfg only. Seconds a listener
#                                         under the temp root must have run
#                                         before it counts as leaked. A
#                                         non-negative whole number; any other
#                                         value is reported and 3600 used.)
#
# [archive]  — the fourth section shared by kanban.cfg (framework-wide
#              default) and a project's own project.cfg (per-project
#              override), alongside [release], [locks] and [overwatch].
#   archive_count           OPTIONAL  55  (NOT read/validated by
#                                         load_config() below — read by
#                                         resolve_archive_count() in
#                                         team/pgai_agent_kanban/archive/
#                                         config.py, which checks
#                                         project.cfg first, then falls
#                                         back to this kanban.cfg value,
#                                         then to 55. How many of the newest
#                                         items per category (bugs,
#                                         priorities, requirements, tasks)
#                                         stay live when finished work is
#                                         archived; 0 disables archiving.
#                                         A non-integer or negative value
#                                         is refused dual-channel and the
#                                         run archives nothing — a wake or
#                                         release never stops over it.)
#
# [testing]  — gated test-suite settings. Kanban-wide only: project.cfg
#              carries no [testing] key and no per-project override. NOT
#              read/validated by load_config() below — a malformed value
#              here must not stop a wake that never runs a suite. The test
#              runners read each key through its resolver at the bottom of
#              this file — config_testing_workers, config_testing_coverage
#              and config_testing_pre_merge_affected_cap — which apply the
#              default, narrate it once, and refuse an unrecognized value
#              dual-channel.
#   workers                 OPTIONAL  3      (non-negative whole number —
#                                              the bounded parallel worker
#                                              budget the gated runners
#                                              hand to pytest-xdist. 0 or 1
#                                              means serial. Decimal digits
#                                              only; leading zeros are
#                                              normalised, so '03' reads as
#                                              3. Absent or blank = 3, the
#                                              same value kanban.cfg_example
#                                              ships, so an install whose
#                                              kanban.cfg predates this key
#                                              gets the bounded budget too.)
#   coverage                OPTIONAL  false  (true|false — whether the gated
#                                              runners may collect coverage.
#                                              Booleans are spelled
#                                              true/false ONLY; 'on'/'off'
#                                              refuse naming the accepted
#                                              set.)
#   pre_merge_affected_cap  OPTIONAL  60     (non-negative whole number —
#                                              the most affected test files
#                                              a pre-merge run selects; a
#                                              cap on files, not a worker
#                                              count. 0 means no cap.
#                                              Decimal digits only; leading
#                                              zeros are normalised. Absent
#                                              or blank = 60.)
#
# [install]  — install and upgrade settings. Kanban-wide only. NOT read by
#              load_config() below; the crontab step reads it.
#   crontab_backups_keep    OPTIONAL  10     (whole number of 1 or more —
#                                              how many crontab backups in
#                                              $HOME the crontab step keeps.
#                                              Read by
#                                              team/scripts/lib/safe_overwrite.sh
#                                              via read_ini; an invalid
#                                              value is refused dual-channel
#                                              and the crontab is left
#                                              unchanged. Absent or blank =
#                                              10.)

# ---------------------------------------------------------------------------
# Bootstrap: ensure read_ini is available.
# If ini_parser.sh has already been sourced (the normal case when wake_common.sh
# sources us), this is a cheap no-op guard.
# ---------------------------------------------------------------------------
if ! command -v read_ini >/dev/null 2>&1 \
    || ! command -v list_ini_keys >/dev/null 2>&1 \
    || ! command -v list_ini_sections >/dev/null 2>&1; then
    # shellcheck source=ini_parser.sh
    source "$(dirname "${BASH_SOURCE[0]}")/ini_parser.sh"
fi

# ---------------------------------------------------------------------------
# Bootstrap: ensure the shared dual-channel error-contract emitter is
# available. Every [release] posture-key refusal below speaks through
# pgai_emit_error_contract rather than a bare "echo ... >&2" — see
# team/SOP-core.md ("Gates and Refusals") for why every new gate must.
# ---------------------------------------------------------------------------
if ! command -v pgai_emit_error_contract >/dev/null 2>&1; then
    # shellcheck source=error_contract.sh
    source "$(dirname "${BASH_SOURCE[0]}")/error_contract.sh"
fi

# ---------------------------------------------------------------------------
# _CONFIG_LOADER_CFG_PATH — set by load_config; used by config_get.
# ---------------------------------------------------------------------------
_CONFIG_LOADER_CFG_PATH=""

# ---------------------------------------------------------------------------
# _CONFIG_LOADER_KNOWN_KEYS — the Key Registry above in machine-readable
# form: one "section:key" token per accepted key. The set of known sections
# is derived from it. load_config() warns on any section or key in the
# cfg file that is not here; see _config_loader_warn_unknown_entries.
# Keep this array and the comment registry above in step — the unit test
# for this module compares them.
# ---------------------------------------------------------------------------
_CONFIG_LOADER_KNOWN_KEYS=(
    paths:dev_tree_path paths:tmp_root paths:tmp_subdir
    paths:cleanup_retention_days paths:worktree_base
    providers:ai_auth_mode providers:active
    providers:available providers:llm_thinking_enabled providers:llm_effort
    chain:pm_mode
    dashboard:rows_per_column dashboard:min_rows_per_column
    dashboard:max_rows_per_column dashboard:min_rows_per_project
    dashboard:max_rows_per_project dashboard:max_rows
    dashboard:dashboard_status_glyph dashboard:snapshot_ttl_seconds
    dashboard:event_band_read_bound_lines dashboard:session_name
    dashboard:refresh_seconds dashboard:color_done dashboard:color_working
    dashboard:color_blocked dashboard:color_waiting dashboard:color_label
    dashboard:color_dim dashboard:color_header dashboard:color_halt
    dashboard:color_ok
    docker:docker_exe docker:container_name docker:image
    docker:container_stop_timeout_seconds
    api:host api:port api:shell_out_timeout_seconds
    governance:budget_bytes_coder governance:budget_bytes_tester
    governance:budget_bytes_po governance:budget_bytes_pm
    governance:budget_bytes_writer governance:budget_bytes_cm
    governance:budget_bytes_overwatch
    wake:max_tasks_per_wake wake:max_runtime_seconds wake:pause_between_tasks
    wake:stop_on_blocked wake:stop_file wake:pm_max_tasks
    wake:max_task_seconds wake:max_project_seconds wake:kill_grace_seconds
    wake:transient_retry_cap wake:zero_progress_cap wake:block_storm_window
    wake:block_hold_cap wake:systemic_block_reasons wake:lock_wait_seconds
    wake:usage_limit_pause_seconds
    wake:lock_starvation_firings
    wake:model_preflight_ttl_seconds wake:model_preflight_timeout_seconds
    models.claude:default models.claude:pm models.claude:coder
    models.claude:writer models.claude:tester models.claude:cm
    models.claude:po
    models.codex:default models.codex:pm models.codex:coder
    models.codex:writer models.codex:tester models.codex:cm models.codex:po
    cleanup:retention_days cleanup:trivial_log_bytes cleanup:trivial_log_hours
    release:deferred_bugs release:deferred_bug_level release:defer_piggyback
    release:deep_verify release:max_rc_retries
    release:deep_verify_offhours_window release:overwatch_resolve
    release:ship_posture release:ship_posture_confirm_remote
    release:container_images
    locks:stale_lock_reap_after_wake_periods
    overwatch:block_triage overwatch:requeue_budget
    overwatch:leaked_listener_min_age_seconds
    archive:archive_count
    testing:workers testing:coverage testing:pre_merge_affected_cap
    install:crontab_backups_keep
)

# ---------------------------------------------------------------------------
# _CONFIG_LOADER_RETIRED_KEYS — keys an earlier kanban.cfg_example seeded
# into installs that nothing reads any more, one "section:key" token each.
# They are deliberately outside the Key Registry and _CONFIG_LOADER_KNOWN_KEYS:
# no code reads them and no document needs to describe them. load_config()
# ignores a retired key's value exactly as it ignores an unknown one, but
# narrates it as a one-line NOTE whose remedy deletes the line, rather than
# the unknown-key WARNING, whose nearest-name hint would suggest renaming
# it onto a live key that the section already carries.
#
#   providers:initial  the provider install.sh once wrote into a brand-new
#                      kanban.cfg; [providers] active is the only provider
#                      selector read at runtime.
# ---------------------------------------------------------------------------
_CONFIG_LOADER_RETIRED_KEYS=(
    providers:initial
)

# ---------------------------------------------------------------------------
# CONFIG_LOADER_WAKE_ENV_NAMES — every environment variable load_config()
# exports for a [wake] key, one name per key (the key upper-cased). Each
# of them resolves env-over-cfg: a name already set in the environment
# wins over the cfg value, which is the documented operator override
# (`LOCK_WAIT_SECONDS=30 wake-batch.sh ...`). The flip side is that a
# process launched from inside a wake inherits every one of them, and a
# nested load_config against a different kanban.cfg reads those inherited
# values as overrides. A caller that must let a different kanban.cfg's
# [wake] section govern -- the gated test runners, before pytest; a test
# fixture building a sandboxed wake's environment -- unsets these names
# first. tests/fixtures/scrubbed_subprocess_env.py carries the Python twin
# (WAKE_TUNABLE_ENV_NAMES); the unit test test_wake_env_names_scrub.py
# fails when either list and the [wake] exports below disagree.
# ---------------------------------------------------------------------------
CONFIG_LOADER_WAKE_ENV_NAMES=(
    MAX_TASKS_PER_WAKE MAX_RUNTIME_SECONDS PAUSE_BETWEEN_TASKS
    STOP_ON_BLOCKED STOP_FILE PM_MAX_TASKS
    MAX_TASK_SECONDS MAX_PROJECT_SECONDS KILL_GRACE_SECONDS
    TRANSIENT_RETRY_CAP ZERO_PROGRESS_CAP BLOCK_STORM_WINDOW BLOCK_HOLD_CAP
    SYSTEMIC_BLOCK_REASONS LOCK_WAIT_SECONDS LOCK_STARVATION_FIRINGS
    USAGE_LIMIT_PAUSE_SECONDS
    MODEL_PREFLIGHT_TTL_SECONDS MODEL_PREFLIGHT_TIMEOUT_SECONDS
)

# ---------------------------------------------------------------------------
# _config_loader_nearest_name <needle> <candidate>...
#
# Prints the candidate with the smallest edit distance (Levenshtein) to
# <needle>; ties go to the earliest candidate. Prints nothing when no
# candidate is given. Used only to make an unknown-key warning actionable,
# so the cost of the DP is paid on the warning path alone.
# ---------------------------------------------------------------------------
_config_loader_nearest_name() {
    local needle="$1"
    shift
    [[ $# -gt 0 ]] || return 0
    printf '%s\n' "$@" | awk -v needle="$needle" '
        function lev(a, b,    la, lb, i, j, cost, prev, cur) {
            la = length(a); lb = length(b)
            for (j = 0; j <= lb; j++) prev[j] = j
            for (i = 1; i <= la; i++) {
                cur[0] = i
                for (j = 1; j <= lb; j++) {
                    cost = (substr(a, i, 1) == substr(b, j, 1)) ? 0 : 1
                    cur[j] = prev[j] + 1
                    if (cur[j - 1] + 1 < cur[j]) cur[j] = cur[j - 1] + 1
                    if (prev[j - 1] + cost < cur[j]) cur[j] = prev[j - 1] + cost
                }
                for (j = 0; j <= lb; j++) prev[j] = cur[j]
            }
            return prev[lb]
        }
        NF == 0 { next }
        {
            d = lev(needle, $0)
            if (best == "" || d < bestd) { best = $0; bestd = d }
        }
        END { if (best != "") print best }
    '
}

# ---------------------------------------------------------------------------
# _config_loader_key_lines <cfg> <section> <key>
#
# Print the line number of every "<key> = ..." line under [<section>] in
# <cfg>, one per line, parsing sections, comments and keys exactly as
# ini_parser.sh's list_ini_keys does. Used only to make a retired-key note's
# remedy point at the exact line, so its cost is paid on that path alone.
# ---------------------------------------------------------------------------
_config_loader_key_lines() {
    local cfg="$1" section="$2" key="$3"
    awk -v want_section="$section" -v want_key="$key" '
        BEGIN { secname = "" }
        { sub(/\r$/, "") }
        /^\[/ {
            secname = $0
            sub(/^\[[ \t]*/, "", secname)
            sub(/[ \t]*\].*$/, "", secname)
            next
        }
        /^[ \t]*$/ { next }
        /^[ \t]*[#;]/ { next }
        {
            eq_pos = index($0, "=")
            if (eq_pos == 0) { next }
            raw_key = substr($0, 1, eq_pos - 1)
            gsub(/^[ \t]+|[ \t]+$/, "", raw_key)
            if (secname == want_section && raw_key == want_key) print NR
        }
    ' "$cfg"
}

# ---------------------------------------------------------------------------
# _config_loader_note_retired_key <cfg> <section> <key>
#
# Write the one-line stderr NOTE for a retired key: what it is, that its
# value is ignored, and a remedy that deletes it. The remedy is a sed
# command scoped to both the line number AND the key's own text, so pasting
# it a second time (after the line numbers shift) deletes nothing else.
# ---------------------------------------------------------------------------
_config_loader_note_retired_key() {
    local cfg="$1" section="$2" key="$3"
    local -a lines=()
    local n sed_args="" remedy
    mapfile -t lines < <(_config_loader_key_lines "$cfg" "$section" "$key")
    for n in "${lines[@]}"; do
        [[ "$n" =~ ^[0-9]+$ ]] || continue
        sed_args+=" -e '${n}{/^[[:space:]]*${key}[[:space:]]*=/d}'"
    done
    if [[ -n "$sed_args" ]]; then
        remedy="sed -i${sed_args} $(printf '%q' "$cfg")"
    else
        remedy="delete the '${key} = ...' line under [${section}] in ${cfg}"
    fi
    echo "config_loader: load_config: NOTE: [${section}] ${key} in ${cfg} is a retired kanban.cfg key that nothing reads; its value is ignored and the line is safe to delete (do not rename it). Remove it with: ${remedy}" >&2
}

# ---------------------------------------------------------------------------
# _config_loader_warn_unknown_entries <cfg>
#
# Walk every section header and key=value line in <cfg> and write one
# stderr WARNING per section, and per key under a known section, that the
# Key Registry does not list — naming the nearest known section or key so
# a misspelling is a one-line fix. Keys under an unknown section are
# covered by that section's own warning and are not repeated. A key in
# _CONFIG_LOADER_RETIRED_KEYS draws one NOTE instead (however many times
# the file repeats it), naming deletion as the remedy rather than a rename.
#
# CONVERT (docs/blocking-site-census.md): a warning, never a refusal, and
# always returns 0. A misspelled key falls back to its default exactly as
# before; what changes is that the fallback is no longer silent. Refusing
# here would let a typo stop every wake on the install.
# ---------------------------------------------------------------------------
_config_loader_warn_unknown_entries() {
    local cfg="$1"
    local -A known_keys=() known_sections=() warned_sections=()
    local -A retired_keys=() noted_retired=()
    local entry section key nearest
    local -a candidates

    for entry in "${_CONFIG_LOADER_KNOWN_KEYS[@]}"; do
        known_keys["$entry"]=1
        known_sections["${entry%%:*}"]=1
    done
    for entry in "${_CONFIG_LOADER_RETIRED_KEYS[@]}"; do
        retired_keys["$entry"]=1
    done

    while IFS= read -r section; do
        [[ -n "$section" ]] || continue
        [[ -n "${known_sections[$section]:-}" ]] && continue
        [[ -n "${warned_sections[$section]:-}" ]] && continue
        warned_sections["$section"]=1
        nearest="$(_config_loader_nearest_name "$section" "${!known_sections[@]}")"
        echo "config_loader: load_config: WARNING: section [${section}] in ${cfg} is not a known kanban.cfg section; every key under it is ignored. Nearest known section: [${nearest}]." >&2
    done < <(list_ini_sections "$cfg")

    # Split on the tab by hand: read's IFS handling would strip a LEADING
    # tab as whitespace, folding a key above the first section header
    # (an empty section name) into the key field.
    local line
    while IFS= read -r line; do
        section="${line%%$'\t'*}"
        key="${line#*$'\t'}"
        [[ -n "$key" && "$line" == *$'\t'* ]] || continue
        if [[ -z "$section" ]]; then

            echo "config_loader: load_config: WARNING: key '${key}' in ${cfg} sits above the first section header, belongs to no section, and is ignored." >&2
            continue
        fi
        [[ -n "${known_sections[$section]:-}" ]] || continue
        [[ -n "${known_keys[${section}:${key}]:-}" ]] && continue
        if [[ -n "${retired_keys[${section}:${key}]:-}" ]]; then
            if [[ -z "${noted_retired[${section}:${key}]:-}" ]]; then
                noted_retired["${section}:${key}"]=1
                _config_loader_note_retired_key "$cfg" "$section" "$key"
            fi
            continue
        fi
        candidates=()
        for entry in "${_CONFIG_LOADER_KNOWN_KEYS[@]}"; do
            [[ "${entry%%:*}" == "$section" ]] && candidates+=("${entry#*:}")
        done
        nearest="$(_config_loader_nearest_name "$key" "${candidates[@]}")"
        echo "config_loader: load_config: WARNING: [${section}] ${key} in ${cfg} is not a known kanban.cfg key and is ignored. Nearest known key: ${nearest}." >&2
    done < <(list_ini_keys "$cfg")

    return 0
}

# ---------------------------------------------------------------------------
# _config_loader_export_wake_integer <cfg> <key> <env_name> <default>
#
# Resolve one [wake] integer key with the loader's standard precedence
# (env var already set > cfg value > default), refuse anything that is not
# a run of decimal digits, and export it as <env_name>. A blank cfg value
# reads as absent and takes the default, exactly as a removed or
# commented-out line does.
#
# BLOCK-WORTHY (docs/blocking-site-census.md): every one of these keys
# feeds bash arithmetic in the wake loop, so a value carrying a unit
# suffix, a sign, or trailing text would either abort a batch mid-run with
# an arithmetic error or bound it by a number nobody chose. Refusing at
# load time, before any task is selected, is the earlier and louder of the
# two failures. The remedy carries the digits the value started with when
# it had any (300s -> 300), else the key's default.
# ---------------------------------------------------------------------------
_config_loader_export_wake_integer() {
    local cfg="$1" key="$2" env_name="$3" default="$4"
    local value source remedy suggested from_env="false"

    if [[ -n "${!env_name:-}" ]]; then
        value="${!env_name}"
        source="environment variable ${env_name}"
        from_env="true"
    else
        value="$(read_ini "$cfg" wake "$key" "")"
        [[ -n "$value" ]] || value="$default"
        source="[wake] ${key} in ${cfg}"
    fi

    if [[ ! "$value" =~ ^[0-9]+$ ]]; then
        suggested="${value%%[^0-9]*}"
        [[ -n "$suggested" ]] || suggested="$default"
        if [[ "$from_env" == "true" ]]; then
            remedy="export ${env_name}=${suggested}"
        else
            remedy="sed -i 's/^[[:space:]]*${key}[[:space:]]*=.*/${key} = ${suggested}/' \"${cfg}\""
        fi
        pgai_emit_error_contract \
            "config_loader.wake.not_an_integer" \
            "config_loader: load_config: ${source} is '${value}', which is not a whole number; [wake] ${key} must be decimal digits only (no unit suffix, sign, or trailing text)." \
            "${remedy}"
        # CENSUS-ANCHOR: config-loader-wake-integer-01
        return 1
    fi

    export "${env_name}=${value}"
    return 0
}

# ---------------------------------------------------------------------------
# _CONFIG_LOADER_NARRATED_DEFAULTS — space-separated list of [release]
# posture keys whose absent-key default has already been narrated in this
# process. Module-level (not local to load_config) so the "once per run"
# guarantee holds across multiple load_config calls in the same process
# (e.g. kanban.cfg then a project's project.cfg) — a key absent from BOTH
# narrates its default exactly once, not once per cfg file.
# ---------------------------------------------------------------------------
_CONFIG_LOADER_NARRATED_DEFAULTS=""

# ---------------------------------------------------------------------------
# _config_loader_narrate_default_once <key> <default_value> [section] [caller]
#
# Writes a one-line stderr narration the FIRST time <key>'s documented
# default is applied in this process, and is a silent no-op on every
# subsequent call for the same <key>. This is what keeps a key read in a
# loop from producing a narration line per iteration (the doctrine named
# in the "[release]" Key Registry entry above). <section> defaults to
# release and <caller> to load_config; the [testing] resolvers pass their
# own. The once-only memory is keyed by <key> alone, so key names narrated
# here must stay unique across sections.
# ---------------------------------------------------------------------------
_config_loader_narrate_default_once() {
    local key="$1"
    local value="$2"
    local section="${3:-release}"
    local caller="${4:-load_config}"
    case " ${_CONFIG_LOADER_NARRATED_DEFAULTS} " in
        *" ${key} "*)
            return 0
            ;;
    esac
    echo "config_loader: ${caller}: [${section}] ${key} absent; applying documented default '${value}'." >&2
    _CONFIG_LOADER_NARRATED_DEFAULTS="${_CONFIG_LOADER_NARRATED_DEFAULTS} ${key}"
}

# ---------------------------------------------------------------------------
# _config_loader_active_provider
#
# Prints the active provider name: read_active_provider when the provider
# lib's active_provider.sh is loaded, else [providers] active from the cfg
# file the most recent load_config read, else "claude".
# ---------------------------------------------------------------------------
_config_loader_active_provider() {
    local _provider=""
    if command -v read_active_provider >/dev/null 2>&1 && [[ -n "$_CONFIG_LOADER_CFG_PATH" ]]; then
        _provider="$(read_active_provider "${_CONFIG_LOADER_CFG_PATH%/*}")"
    elif [[ -n "$_CONFIG_LOADER_CFG_PATH" ]]; then
        _provider="$(read_ini "$_CONFIG_LOADER_CFG_PATH" providers active claude)"
    fi
    printf '%s\n' "${_provider:-claude}"
}

# ---------------------------------------------------------------------------
# pgai_provider_effort_values <provider>
#
# Prints the reasoning-effort levels <provider>'s CLI accepts, space
# separated, as the provider declares them in its own lib
# (PROVIDER_EFFORT_VALUES in team/scripts/lib/provider_<provider>.sh, a
# sibling of this file). The declaration is read from the file, never by
# sourcing it, so no provider code runs at config load. Returns 1 and prints
# nothing when the provider has no lib or its lib declares no levels.
# ---------------------------------------------------------------------------
pgai_provider_effort_values() {
    local provider="${1:-}"
    [[ "$provider" =~ ^[a-z0-9][a-z0-9_-]*$ ]] || return 1
    local lib
    lib="$(dirname "${BASH_SOURCE[0]}")/provider_${provider}.sh"
    [[ -f "$lib" ]] || return 1
    local line values
    local decl_re='^[[:space:]]*PROVIDER_EFFORT_VALUES[[:space:]]*=[[:space:]]*["'\'']?([^"'\''#]*)'
    while IFS= read -r line || [[ -n "$line" ]]; do
        if [[ "$line" =~ $decl_re ]]; then
            # Collapse runs of whitespace; the output is canonical.
            values="$(printf '%s' "${BASH_REMATCH[1]}" | tr -s '[:space:]' ' ')"
            values="${values# }"
            values="${values% }"
            [[ -n "$values" ]] || return 1
            printf '%s\n' "$values"
            return 0
        fi
    done < "$lib"
    return 1
}

# ---------------------------------------------------------------------------
# _config_loader_check_effort <value> <source> <provider> <remedy>
#
# Returns 0 when <provider> declares <value> among its reasoning-effort
# levels (pgai_provider_effort_values). When <provider> declares a list
# that does not include <value>, emits the dual-channel refusal naming
# <source> (the key that supplied the value), the value, the provider and
# its valid list, with <remedy> as the runnable fix, and returns 1.
#
# A provider that declares no list at all (no provider_<name>.sh lib, or a
# lib without a PROVIDER_EFFORT_VALUES line -- e.g. the test-only harness
# lane) gives the loader nothing to check against, so the value is not
# validated: one narration line goes to stderr naming the provider, the
# source and the value, and the function returns 0 so the value passes
# through unchanged. Refusing here would turn every inherited effort
# setting into a config-load failure for such a provider.
# ---------------------------------------------------------------------------
_config_loader_check_effort() {
    local value="$1" source="$2" provider="$3" remedy="$4"
    local valid candidate
    if ! valid="$(pgai_provider_effort_values "$provider")"; then
        echo "config_loader: provider '${provider}' declares no reasoning-effort levels (no PROVIDER_EFFORT_VALUES in provider_${provider}.sh); ${source} value '${value}' is passed through unvalidated." >&2
        return 0
    fi
    for candidate in $valid; do
        [[ "$value" == "$candidate" ]] && return 0
    done
    # BLOCK-WORTHY (docs/blocking-site-census.md): an invariant by design --
    # an effort level the active provider's CLI does not accept would be
    # passed on every dispatch for the role and fail there, so it is refused
    # at config load instead. Reached only for a provider that declares a
    # list; a provider declaring none returned above.
    pgai_emit_error_contract \
        "config_loader.providers.invalid_effort" \
        "config_loader: reasoning-effort value '${value}' supplied by ${source} is not accepted by the active provider '${provider}' (valid for ${provider}: ${valid})." \
        "$remedy"
    # CENSUS-ANCHOR: config-loader-effort-01
    return 1
}

# ---------------------------------------------------------------------------
# _config_loader_effort_example <provider>
#
# Prints one level <provider> accepts, for a runnable remedy: "high" when it
# is listed, else the provider's first declared level. Prints nothing when
# the provider declares no levels.
# ---------------------------------------------------------------------------
_config_loader_effort_example() {
    local valid
    valid="$(pgai_provider_effort_values "${1:-}")" || valid=""
    case " ${valid} " in
        "  ") printf '\n' ;;
        *" high "*) printf 'high\n' ;;
        *) printf '%s\n' "${valid%% *}" ;;
    esac
}

# ---------------------------------------------------------------------------
# resolve_role_effort <role> [provider]
#
# Resolve the reasoning-effort value in force for <role>, mirroring the
# PGAI_<ROLE>_MODEL precedence tier and narration-source discipline:
#
#   1. PGAI_<ROLE>_EFFORT already exported (env) — wins.
#   2. [providers] llm_effort from the most recently load_config'd cfg file
#      (a single global key; there is no per-role matrix).
#   3. Neither set — absent. No effort resolved; this is today's behavior.
#
# The resolved value is validated against the levels the provider declares
# (pgai_provider_effort_values): [provider] when given, else the loaded
# provider lib's PROVIDER_NAME, else the active provider from the cfg. A
# provider that declares no levels is not validated: the value resolves as
# usual and one narration line on stderr says it was passed through
# unvalidated (_config_loader_check_effort).
#
# This is the shared resolution mechanism both provider wake lanes
# (scripts/wake/claude.sh, scripts/wake/codex.sh) call to determine the
# effort value and its narration source, exactly as they call the model
# resolution logic today.
#
# On success, prints "<value>|<source>" to stdout and returns 0.
#   <source> is one of:
#     "PGAI_<ROLE>_EFFORT env"        (tier 1)
#     "[providers] llm_effort cfg"    (tier 2)
#     ""                              (absent — <value> is also empty)
# On a value a provider with a declared list does not accept, emits the
# dual-channel refusal config_loader.providers.invalid_effort on stderr
# naming the exact source key, the value and the provider's valid list,
# prints nothing to stdout, and returns 1.
# ---------------------------------------------------------------------------
resolve_role_effort() {
    local role="${1:-}"
    if [[ -z "$role" ]]; then
        echo "ERROR: resolve_role_effort: role argument is required." >&2
        return 1
    fi
    local provider="${2:-${PROVIDER_NAME:-}}"
    [[ -n "$provider" ]] || provider="$(_config_loader_active_provider)"
    # A level the provider accepts, named in a refusal's remedy. A refusal
    # only happens for a provider that declares levels, so this is normally
    # set; the empty-example branches below are defensive and remove the
    # value instead.
    local example
    example="$(_config_loader_effort_example "$provider")"

    local upper="${role^^}"
    local env_var="PGAI_${upper}_EFFORT"
    local env_value="${!env_var:-}"

    if [[ -n "$env_value" ]]; then
        local env_remedy="unset ${env_var}"
        [[ -n "$example" ]] && env_remedy+=", or set it to a level ${provider} accepts: export ${env_var}=${example}"
        _config_loader_check_effort "$env_value" "$env_var" "$provider" "$env_remedy" || return 1
        printf '%s|%s\n' "$env_value" "${env_var} env"
        return 0
    fi

    local cfg_value=""
    if [[ -n "$_CONFIG_LOADER_CFG_PATH" ]]; then
        cfg_value="$(read_ini "$_CONFIG_LOADER_CFG_PATH" providers llm_effort "")"
    fi

    if [[ -n "$cfg_value" ]]; then
        local cfg_q cfg_remedy
        cfg_q="$(printf '%q' "$_CONFIG_LOADER_CFG_PATH")"
        if [[ -n "$example" ]]; then
            cfg_remedy="sed -i 's/^[[:space:]]*llm_effort[[:space:]]*=.*/llm_effort = ${example}/' ${cfg_q}"
        else
            cfg_remedy="sed -i '/^[[:space:]]*llm_effort[[:space:]]*=/d' ${cfg_q}"
        fi
        _config_loader_check_effort "$cfg_value" "[providers] llm_effort" "$provider" "$cfg_remedy" || return 1
        printf '%s|%s\n' "$cfg_value" "[providers] llm_effort cfg"
        return 0
    fi

    printf '|\n'
    return 0
}

# ---------------------------------------------------------------------------
# load_config <cfg_path>
#
# Read kanban.cfg, validate all REQUIRED keys, apply defaults for absent
# OPTIONAL keys, and export the canonical env vars.
#
# On success: returns 0 and all env vars are exported.
# On failure: writes an error to stderr naming the missing key + file path,
#             returns non-zero.
# ---------------------------------------------------------------------------
load_config() {
    local cfg="${1:-}"

    if [[ -z "$cfg" ]]; then
        echo "ERROR: load_config: no config file path supplied." >&2
        return 1
    fi

    if [[ ! -f "$cfg" ]]; then
        echo "ERROR: load_config: config file not found: $cfg" >&2
        return 1
    fi

    # Store path for config_get.
    _CONFIG_LOADER_CFG_PATH="$cfg"

    # Warn (never refuse) on every section or key the Key Registry does not
    # list, naming the nearest known name, before any value is read.
    _config_loader_warn_unknown_entries "$cfg"

    # -----------------------------------------------------------------------
    # OPTIONAL keys — apply defaults when absent, then export env vars.
    # Precedence: env var already set > config value > registry default.
    # -----------------------------------------------------------------------

    # [paths] dev_tree_path → PGAI_DEV_TREE_PATH (OPTIONAL; empty when not set)
    # Install-time convenience and fallback for projects whose project.cfg omits
    # dev_tree_path. An install may leave this empty;
    # downstream consumers that require a dev tree use require_dev_tree() from
    # scripts/lib/dev_tree.sh. No disk-existence check here — the loader
    # validates config shape, not infrastructure state.
    local _dev_tree_path
    _dev_tree_path="$(read_ini "$cfg" paths dev_tree_path "")"
    export PGAI_DEV_TREE_PATH="${PGAI_DEV_TREE_PATH:-${_dev_tree_path}}"

    # [paths] tmp_root + tmp_subdir → PGAI_AGENT_KANBAN_TEMP_DIR
    # If PGAI_AGENT_KANBAN_TEMP_DIR is already set in the environment, skip.
    if [[ -z "${PGAI_AGENT_KANBAN_TEMP_DIR:-}" ]]; then
        local _tmp_root _tmp_subdir
        _tmp_root="$(read_ini "$cfg" paths tmp_root "/tmp")"
        _tmp_subdir="$(read_ini "$cfg" paths tmp_subdir "pgai_kanban_tmp")"
        _tmp_root="${_tmp_root:-/tmp}"
        _tmp_subdir="${_tmp_subdir:-pgai_kanban_tmp}"
        export PGAI_AGENT_KANBAN_TEMP_DIR="${_tmp_root}/${_tmp_subdir}"
    fi

    # Export TMPDIR so that mktemp and other tools that respect TMPDIR
    # land under the configured framework temp root rather than /tmp.
    # TMPDIR is derived only from the already-resolved PGAI_AGENT_KANBAN_TEMP_DIR
    # value above — it MUST NOT re-read tmp_root/tmp_subdir or re-implement
    # the concat.  Mirror pgai_temp_dir's mkdir -p to ensure the directory exists.
    mkdir -p "${PGAI_AGENT_KANBAN_TEMP_DIR}"
    export TMPDIR="${PGAI_AGENT_KANBAN_TEMP_DIR}"

    # [providers] ai_auth_mode → AI_AUTH_MODE
    export AI_AUTH_MODE="${AI_AUTH_MODE:-$(read_ini "$cfg" providers ai_auth_mode oauth)}"

    # [chain] pm_mode → PGAI_KANBAN_PM_MODE
    export PGAI_KANBAN_PM_MODE="${PGAI_KANBAN_PM_MODE:-$(read_ini "$cfg" chain pm_mode automatic)}"

    # [dashboard] rows_per_column → DASHBOARD_ROWS_PER_COLUMN
    DASHBOARD_ROWS_PER_COLUMN="${DASHBOARD_ROWS_PER_COLUMN:-$(read_ini "$cfg" dashboard rows_per_column 21)}"
    export DASHBOARD_ROWS_PER_COLUMN

    # [dashboard] dashboard_status_glyph → DASHBOARD_STATUS_GLYPH
    # OPTIONAL; default is U+25A0 "■". Validated: must be exactly one visible
    # character. Multi-character values exit non-zero with an error naming the
    # key and the config file path — do not silently truncate.
    if [[ -z "${DASHBOARD_STATUS_GLYPH:-}" ]]; then
        local _glyph _char_count
        _glyph="$(read_ini "$cfg" dashboard dashboard_status_glyph "■")"
        _glyph="${_glyph:-■}"
        _char_count=$(printf '%s' "$_glyph" | wc -m)
        if [[ "$_char_count" -ne 1 ]]; then
            echo "ERROR: load_config: dashboard_status_glyph must be exactly one character (got ${_char_count}): ${cfg}" >&2
            return 1
        fi
        export DASHBOARD_STATUS_GLYPH="$_glyph"
    fi

    # Every [wake] integer key below resolves through
    # _config_loader_export_wake_integer: env var already set > cfg value >
    # default, refused dual-channel unless the resolved value is decimal
    # digits only. These are the values the wake loop feeds to bash
    # arithmetic, so a non-number here never reaches it.

    # [wake] max_tasks_per_wake → MAX_TASKS_PER_WAKE
    _config_loader_export_wake_integer "$cfg" max_tasks_per_wake MAX_TASKS_PER_WAKE 5 || return 1

    # [wake] max_runtime_seconds → MAX_RUNTIME_SECONDS
    # Whole-batch wall-clock budget; the outermost of the three runtime
    # ceilings, which must nest task < project <= batch (see max_task_seconds
    # and max_project_seconds below).
    _config_loader_export_wake_integer "$cfg" max_runtime_seconds MAX_RUNTIME_SECONDS 14400 || return 1

    # [wake] pause_between_tasks → PAUSE_BETWEEN_TASKS
    _config_loader_export_wake_integer "$cfg" pause_between_tasks PAUSE_BETWEEN_TASKS 5 || return 1

    # [wake] stop_on_blocked → STOP_ON_BLOCKED
    export STOP_ON_BLOCKED="${STOP_ON_BLOCKED:-$(read_ini "$cfg" wake stop_on_blocked true)}"

    # [wake] stop_file → STOP_FILE
    # Default derives from the already-resolved temp dir (single-resolver discipline:
    # consumes PGAI_AGENT_KANBAN_TEMP_DIR set above; does not re-read tmp_root/tmp_subdir).
    export STOP_FILE="${STOP_FILE:-$(read_ini "$cfg" wake stop_file "${PGAI_AGENT_KANBAN_TEMP_DIR}/wakeup/stop")}"

    # [wake] pm_max_tasks → PM_MAX_TASKS
    _config_loader_export_wake_integer "$cfg" pm_max_tasks PM_MAX_TASKS 15 || return 1

    # [wake] max_task_seconds → MAX_TASK_SECONDS
    # Hard per-task timeout.  On expiry the wake script sends SIGTERM to the
    # provider's whole process group, then SIGKILL to the group (after
    # kill_grace_seconds), waits for the group to empty, and marks the task
    # BLOCKED with the exceeded-runtime reason text.  Default: 5400 (90 min).
    #
    # Nesting invariant: task < project <= batch.  The watchdog is armed, at
    # each dispatch, with min(MAX_TASK_SECONDS, remaining MAX_PROJECT_SECONDS
    # budget, remaining MAX_RUNTIME_SECONDS budget) — wake_task_budget.sh —
    # so this value is the task's own ceiling, never a licence to outrun the
    # project or the batch.  A value at or above either enclosing ceiling
    # (equality included) draws a startup WARNING from run_project_chain.
    # 0 disables this per-task bound alone; the remaining project and batch
    # budgets still cap the task.
    _config_loader_export_wake_integer "$cfg" max_task_seconds MAX_TASK_SECONDS 5400 || return 1

    # [wake] max_project_seconds → MAX_PROJECT_SECONDS
    # Per-project runtime cap.  Each project in the multi-project iteration gets
    # its own elapsed clock that resets at the start of run_project_chain.  When
    # elapsed time for that project exceeds MAX_PROJECT_SECONDS the project task
    # loop exits and the next project gets its own fresh clock.  This prevents a
    # stalled project 1 from consuming the entire batch budget and starving
    # projects 2..N.  Default: same as MAX_RUNTIME_SECONDS when unset.
    # Set to 0 to disable the per-project cap (unlimited per project).
    # Nesting invariant: task < project <= batch — the project cap may equal
    # the batch cap (single-project installs), but the per-task limit must
    # stay strictly below it; a project cap above the batch cap draws a
    # startup WARNING and has no effect.
    _config_loader_export_wake_integer "$cfg" max_project_seconds MAX_PROJECT_SECONDS "${MAX_RUNTIME_SECONDS}" || return 1

    # [wake] kill_grace_seconds → KILL_GRACE_SECONDS
    # Seconds between SIGTERM and SIGKILL when the per-task timeout fires;
    # also bounds how long finalization waits for the provider's process
    # group to empty.  Default: 30.
    _config_loader_export_wake_integer "$cfg" kill_grace_seconds KILL_GRACE_SECONDS 30 || return 1

    # [wake] transient_retry_cap → TRANSIENT_RETRY_CAP
    # The ONE ceiling for the durable ## Transient Retry record.
    # Consumed by wake_common.sh's handle_working_after_exit / recheck_blocked_tasks
    # and by OVERWATCH's check-transient-api-error.sh, both via
    # transient_retry_limit() in overwatch_protocol.sh — never hardcode a second
    # ceiling elsewhere. Default: 3.
    _config_loader_export_wake_integer "$cfg" transient_retry_cap TRANSIENT_RETRY_CAP 3 || return 1

    # [wake] zero_progress_cap → ZERO_PROGRESS_CAP
    # The ONE ceiling for the durable ## Zero Progress record — a SEPARATE
    # counter from transient_retry_cap above (see overwatch_protocol.sh's
    # zero_progress_* header comment for why). Consumed by
    # reconcile_stale_active (wake_reconcile_sweeps.sh) and
    # handle_working_after_exit's non-transient fallback (wake_lifecycle.sh),
    # both via zero_progress_cap() in overwatch_protocol.sh — never hardcode
    # a second ceiling elsewhere. Default: 3.
    _config_loader_export_wake_integer "$cfg" zero_progress_cap ZERO_PROGRESS_CAP 3 || return 1

    # [wake] block_storm_window → BLOCK_STORM_WINDOW
    # Seconds back from now. Two or more human-gated blocks sharing one
    # reason_code inside this window are a systemic block storm, the
    # condition under which the dispatch gate holds a whole project; a lone
    # task-local block gates only its own dependents. Default: 3600.
    _config_loader_export_wake_integer "$cfg" block_storm_window BLOCK_STORM_WINDOW 3600 || return 1

    # [wake] block_hold_cap → BLOCK_HOLD_CAP
    # Seconds. The longest the blocked-task dispatch gate holds a project,
    # measured from the block that began the hold, whatever the blocks are;
    # the hold lifts by itself then even if no one resolves them. Default:
    # 3600.
    _config_loader_export_wake_integer "$cfg" block_hold_cap BLOCK_HOLD_CAP 3600 || return 1

    # [wake] usage_limit_pause_seconds → USAGE_LIMIT_PAUSE_SECONDS
    # Seconds. When a provider reports its usage limit without a reset time
    # the wake can read, dispatch to that provider pauses fleet-wide for
    # this long (pgai_agent_kanban.lib.provider_pause). A stated reset time
    # always wins. Default: 3600.
    _config_loader_export_wake_integer "$cfg" usage_limit_pause_seconds USAGE_LIMIT_PAUSE_SECONDS 3600 || return 1

    # [wake] systemic_block_reasons → SYSTEMIC_BLOCK_REASONS
    # Block Record reason codes that gate a project's dispatch on their own,
    # without a storm. Exported as written (blank when absent): the reader,
    # pgai_agent_kanban.overwatch.block_storm, owns the default list, reads
    # "none" as no code, and warns about and ignores an unknown code rather
    # than refusing -- a typo must not stop a wake.
    export SYSTEMIC_BLOCK_REASONS="${SYSTEMIC_BLOCK_REASONS:-$(read_ini "$cfg" wake systemic_block_reasons "")}"

    # [wake] lock_wait_seconds → LOCK_WAIT_SECONDS
    # Seconds a wake keeps retrying, with backoff, for a repo lock whose
    # holder is another wake chain before it defers the project; any other
    # holder is not waited on. Never longer than the batch's remaining
    # runtime budget; one budget per project per firing, shared by the
    # first pass and the end-of-iteration retry. 0 = a single attempt.
    # Consumed by lib/wake_lock_wait.sh. Default: 120.
    _config_loader_export_wake_integer "$cfg" lock_wait_seconds LOCK_WAIT_SECONDS 120 || return 1

    # [wake] lock_starvation_firings → LOCK_STARVATION_FIRINGS
    # Consecutive firings a role may end deferred on repo-lock contention
    # while it has a BACKLOG task before the lock-starvation warning is
    # logged; from then until that role takes the lock, PM wakes skip
    # discovery while its task is ready. 0 turns the guard off. Consumed by
    # lib/wake_lock_wait.sh. Default: 3.
    _config_loader_export_wake_integer "$cfg" lock_starvation_firings LOCK_STARVATION_FIRINGS 3 || return 1

    # [wake] model_preflight_ttl_seconds → MODEL_PREFLIGHT_TTL_SECONDS
    # Seconds a codex model-preflight PASS is remembered per model, so a
    # firing inside the window dispatches without another real model
    # request. 0 checks on every firing that has a task to dispatch.
    # Consumed by lib/provider_codex.sh. Default: 3600.
    _config_loader_export_wake_integer "$cfg" model_preflight_ttl_seconds MODEL_PREFLIGHT_TTL_SECONDS 3600 || return 1

    # [wake] model_preflight_timeout_seconds → MODEL_PREFLIGHT_TIMEOUT_SECONDS
    # Bound on the codex model-preflight call, which runs under the
    # project's repo lock: on expiry the call is stopped, the preflight
    # fails, and run_project_chain releases the lock. 0 = no bound.
    # Consumed by lib/provider_codex.sh. Default: 60.
    _config_loader_export_wake_integer "$cfg" model_preflight_timeout_seconds MODEL_PREFLIGHT_TIMEOUT_SECONDS 60 || return 1

    # [cleanup] retention_days → PGAI_CLEANUP_RETENTION_DAYS
    export PGAI_CLEANUP_RETENTION_DAYS="${PGAI_CLEANUP_RETENTION_DAYS:-$(read_ini "$cfg" cleanup retention_days 30)}"

    # [cleanup] trivial_log_bytes → PGAI_CLEANUP_TRIVIAL_LOG_BYTES
    export PGAI_CLEANUP_TRIVIAL_LOG_BYTES="${PGAI_CLEANUP_TRIVIAL_LOG_BYTES:-$(read_ini "$cfg" cleanup trivial_log_bytes 1700)}"

    # [cleanup] trivial_log_hours → PGAI_CLEANUP_TRIVIAL_LOG_HOURS
    export PGAI_CLEANUP_TRIVIAL_LOG_HOURS="${PGAI_CLEANUP_TRIVIAL_LOG_HOURS:-$(read_ini "$cfg" cleanup trivial_log_hours 6)}"

    # -----------------------------------------------------------------------
    # [release] deferral-posture keys — deferred_bugs, deferred_bug_level,
    # defer_piggyback, deep_verify. See the "[release]" Key Registry entry
    # above for accepted values, defaults, and the boolean-strictness /
    # deep_verify-exception doctrine. read_ini's section scoping means a
    # same-named key sitting in some OTHER section of $cfg (e.g. a stray
    # defer_piggyback under [training]) is never read here and never
    # triggers a refusal — only the [release]-scoped value is this
    # registry's business.
    #
    # Every refusal below is BLOCK-WORTHY (docs/blocking-site-census.md):
    # an unrecognized value for any of these four keys cannot be silently
    # resolved into a posture nobody asked for — the same invariant-by-
    # design reasoning CENSUS-ANCHOR deep-verify-02 already applies to
    # deep_verify in team/scripts/deep-verify.sh.
    # -----------------------------------------------------------------------

    # [release] deferred_bugs → PGAI_DEFERRED_BUGS (master switch: whether
    # bugs are deferred at all).
    local _rel_deferred_bugs
    _rel_deferred_bugs="$(read_ini "$cfg" release deferred_bugs "")"
    if [[ -z "$_rel_deferred_bugs" ]]; then
        _config_loader_narrate_default_once deferred_bugs "false"
        _rel_deferred_bugs="false"
    fi
    case "$_rel_deferred_bugs" in
        true|false) ;;
        *)
            pgai_emit_error_contract \
                "config_loader.release.invalid_deferred_bugs" \
                "config_loader: load_config: [release] deferred_bugs='${_rel_deferred_bugs}' in ${cfg} is not an accepted value." \
                "Set [release] deferred_bugs to 'true' or 'false' in ${cfg} (accepted: true|false; 'on'/'off' are not accepted spellings)."
            # CENSUS-ANCHOR: config-loader-deferred-bugs-01
            return 1
            ;;
    esac
    export PGAI_DEFERRED_BUGS="$_rel_deferred_bugs"

    # [release] deferred_bug_level → PGAI_DEFERRED_BUG_LEVEL (which
    # severities wait). low|medium|high; high defers every bug, so no bug
    # can ever trigger an RC on its own while level=high.
    local _rel_deferred_bug_level
    _rel_deferred_bug_level="$(read_ini "$cfg" release deferred_bug_level "")"
    if [[ -z "$_rel_deferred_bug_level" ]]; then
        _config_loader_narrate_default_once deferred_bug_level "low"
        _rel_deferred_bug_level="low"
    fi
    case "$_rel_deferred_bug_level" in
        low|medium|high) ;;
        *)
            pgai_emit_error_contract \
                "config_loader.release.invalid_deferred_bug_level" \
                "config_loader: load_config: [release] deferred_bug_level='${_rel_deferred_bug_level}' in ${cfg} is not an accepted value." \
                "Set [release] deferred_bug_level to one of low|medium|high in ${cfg}."
            # CENSUS-ANCHOR: config-loader-deferred-bug-level-01
            return 1
            ;;
    esac
    export PGAI_DEFERRED_BUG_LEVEL="$_rel_deferred_bug_level"

    # [release] defer_piggyback → PGAI_DEFER_PIGGYBACK (whether a bug ABOVE
    # the level triggering an RC carries every deferred bug along with it).
    local _rel_defer_piggyback
    _rel_defer_piggyback="$(read_ini "$cfg" release defer_piggyback "")"
    if [[ -z "$_rel_defer_piggyback" ]]; then
        _config_loader_narrate_default_once defer_piggyback "true"
        _rel_defer_piggyback="true"
    fi
    case "$_rel_defer_piggyback" in
        true|false) ;;
        *)
            pgai_emit_error_contract \
                "config_loader.release.invalid_defer_piggyback" \
                "config_loader: load_config: [release] defer_piggyback='${_rel_defer_piggyback}' in ${cfg} is not an accepted value." \
                "Set [release] defer_piggyback to 'true' or 'false' in ${cfg} (accepted: true|false; 'on'/'off' are not accepted spellings)."
            # CENSUS-ANCHOR: config-loader-defer-piggyback-01
            return 1
            ;;
    esac
    export PGAI_DEFER_PIGGYBACK="$_rel_defer_piggyback"

    # [release] deep_verify — validated here, exported nowhere. The value is
    # read where it is used: pp_deep_verify_mode() in project_paths.sh (and
    # its Python twin in ops/read.py) resolves project.cfg > kanban.cfg >
    # weekly on every call, so this kanban.cfg tier takes effect for every
    # project that does not set its own. This check only makes an
    # unrecognized kanban-wide value refuse at load, as the other three
    # [release] posture keys do. Accepted values are its word spellings
    # weekly|off PLUS true|false as an explicit exception to the
    # boolean-strictness rule above (true=weekly, false=off).
    local _rel_deep_verify
    _rel_deep_verify="$(read_ini "$cfg" release deep_verify "")"
    if [[ -z "$_rel_deep_verify" ]]; then
        _config_loader_narrate_default_once deep_verify "weekly"
        _rel_deep_verify="weekly"
    fi
    case "$_rel_deep_verify" in
        weekly|true|off|false) ;;
        *)
            pgai_emit_error_contract \
                "config_loader.release.invalid_deep_verify" \
                "config_loader: load_config: [release] deep_verify='${_rel_deep_verify}' in ${cfg} is not an accepted value." \
                "Set [release] deep_verify to one of weekly|off (or true|false) in ${cfg}."
            # CENSUS-ANCHOR: config-loader-deep-verify-01
            return 1
            ;;
    esac

    # [models.<provider>] per-role model overrides → PGAI_<ROLE>_MODEL
    # The active provider is resolved lazily: if read_active_provider is available
    # (sourced by the provider lib), use it; otherwise fall back to the [providers]
    # active key from this same config.
    local _active_provider
    _active_provider="$(_config_loader_active_provider)"

    local _models_section="models.${_active_provider}"

    # Read the provider default once; applied uniformly to every unset role below.
    # Resolution order per role:
    #   1. PGAI_<ROLE>_MODEL already exported (e.g. from env) — wins, never overwritten.
    #   2. [models.<provider>] <role>   — per-role config value.
    #   3. [models.<provider>] default  — provider-wide fallback (this tier).
    # When all three are empty the role's env var remains unset; the wake path
    # will log a non-blocking warning and let the provider CLI choose its default.
    local _provider_default_model
    _provider_default_model="$(read_ini "$cfg" "$_models_section" "default" "")"

    local _role _model _upper _var
    for _role in pm coder writer tester cm po; do
        _upper="${_role^^}"
        _var="PGAI_${_upper}_MODEL"
        # Skip roles already set in the environment (tier 1 wins).
        if [[ -n "${!_var:-}" ]]; then
            continue
        fi
        # Tier 2: per-role config value.
        _model="$(read_ini "$cfg" "$_models_section" "$_role" "")"
        # Tier 3: provider default fallback when per-role is also empty.
        if [[ -z "$_model" ]]; then
            _model="$_provider_default_model"
        fi
        if [[ -n "$_model" ]]; then
            export "$_var"="$_model"
        fi
    done

    # [providers] llm_effort → PGAI_<ROLE>_EFFORT
    # Resolved exactly like PGAI_<ROLE>_MODEL above (env wins over cfg) via
    # resolve_role_effort() — a single global cfg key, no per-role matrix.
    # Absent everywhere means nothing is exported, which is byte-identical
    # to today's behavior (no PGAI_<ROLE>_EFFORT var exists at all).  A
    # value the active provider does not accept fails load_config loud,
    # naming the exact source key that supplied it and the provider's list.
    local _role_e _resolved_e _resolved_e_value _resolved_e_source _var_e
    for _role_e in pm coder writer tester cm po; do
        _resolved_e=""
        _resolved_e="$(resolve_role_effort "$_role_e")"
        if [[ $? -ne 0 ]]; then
            return 1
        fi
        _resolved_e_value="${_resolved_e%%|*}"
        _resolved_e_source="${_resolved_e#*|}"
        if [[ -n "$_resolved_e_value" ]]; then
            _var_e="PGAI_${_role_e^^}_EFFORT"
            export "$_var_e"="$_resolved_e_value"
        fi
        # _resolved_e_source is not exported here — it is derivable on demand
        # by re-calling resolve_role_effort(), which is what the provider
        # wake lanes do at dispatch time for their narration line.
        unset _resolved_e_source
    done

    return 0
}

# ---------------------------------------------------------------------------
# config_get <section> <key> [default]
#
# Explicit accessor for a single kanban.cfg key.
# Uses the config path set by the most recent load_config call.
# If load_config has not been called, returns default (or empty string).
#
# Example:
#   value=$(config_get paths dev_tree_path "")
# ---------------------------------------------------------------------------
config_get() {
    local section="${1:-}"
    local key="${2:-}"
    local default="${3:-}"

    if [[ -z "$section" || -z "$key" ]]; then
        echo "ERROR: config_get: section and key are required." >&2
        return 1
    fi

    if [[ -z "$_CONFIG_LOADER_CFG_PATH" ]]; then
        # load_config has not been called; return default.
        printf '%s' "$default"
        return 0
    fi

    read_ini "$_CONFIG_LOADER_CFG_PATH" "$section" "$key" "$default"
}

# ---------------------------------------------------------------------------
# _config_loader_testing_value <cfg> <key>
#
# Prints the raw [testing] <key> value from <cfg>, or nothing when <cfg> is
# empty, names no regular file, or carries no such key. A kanban.cfg that
# does not exist (a dev tree with no installed config) reads exactly like
# one whose [testing] section is absent: every key takes its default.
# ---------------------------------------------------------------------------
_config_loader_testing_value() {
    local cfg="$1" key="$2"
    [[ -n "$cfg" && -f "$cfg" ]] || return 0
    read_ini "$cfg" testing "$key" ""
}

# ---------------------------------------------------------------------------
# config_testing_workers [cfg]
#
# Resolve [testing] workers — the bounded parallel worker budget for the
# gated test runners — and print it on stdout as plain decimal digits.
# 0 and 1 both mean serial; the caller decides what to do with each.
#
# <cfg> defaults to the file the most recent load_config call recorded.
# Absent, blank, or no cfg file at all: prints the default 3 and narrates
# that once per process on stderr. Leading zeros are dropped ('03' -> 3,
# '00' -> 0) so the printed value is safe in bash arithmetic.
#
# Returns 0 with the value on stdout, or 1 with nothing on stdout and the
# dual-channel refusal on stderr when the value is not decimal digits only.
# ---------------------------------------------------------------------------
config_testing_workers() {
    local cfg="${1:-${_CONFIG_LOADER_CFG_PATH}}"
    local value suggested
    value="$(_config_loader_testing_value "$cfg" workers)"

    if [[ -z "$value" ]]; then
        _config_loader_narrate_default_once workers "3" testing config_testing_workers
        printf '%s\n' "3"
        return 0
    fi

    if [[ ! "$value" =~ ^[0-9]+$ ]]; then
        suggested="${value%%[^0-9]*}"
        [[ -n "$suggested" ]] || suggested="3"
        # BLOCK-WORTHY (docs/blocking-site-census.md): an invariant by
        # design. The value bounds how many pytest workers a gated run
        # starts on a shared host; guessing a number from 'abc' or '3x'
        # would run a budget nobody chose, so the runner is refused before
        # it starts anything. The remedy edits the key inside [testing]
        # alone, leaving any same-named key in another section as it was.
        pgai_emit_error_contract \
            "config_loader.testing.invalid_workers" \
            "config_loader: config_testing_workers: [testing] workers='${value}' in ${cfg} is not an accepted value; it must be a non-negative whole number in decimal digits (0 or 1 = serial)." \
            "sed -i '/^[[:space:]]*\\[testing\\][[:space:]]*\$/,/^[[:space:]]*\\[/ s/^[[:space:]]*workers[[:space:]]*=.*/workers = ${suggested}/' \"${cfg}\""
        # CENSUS-ANCHOR: config-loader-testing-workers-01
        return 1
    fi

    # Drop leading zeros without arithmetic, which would read them as octal.
    value="${value#"${value%%[!0]*}"}"
    printf '%s\n' "${value:-0}"
    return 0
}

# ---------------------------------------------------------------------------
# config_testing_coverage [cfg]
#
# Resolve [testing] coverage — whether the gated test runners may collect
# coverage — and print 'true' or 'false' on stdout. Kanban-wide only: there
# is no project.cfg key for it. This reads the cfg file alone; any
# environment override is the runner's own business, not this resolver's.
#
# <cfg> defaults to the file the most recent load_config call recorded.
# Absent, blank, or no cfg file at all: prints the default 'false' and
# narrates that once per process on stderr.
#
# Returns 0 with the value on stdout, or 1 with nothing on stdout and the
# dual-channel refusal on stderr for anything but true|false.
# ---------------------------------------------------------------------------
config_testing_coverage() {
    local cfg="${1:-${_CONFIG_LOADER_CFG_PATH}}"
    local value
    value="$(_config_loader_testing_value "$cfg" coverage)"

    if [[ -z "$value" ]]; then
        _config_loader_narrate_default_once coverage "false" testing config_testing_coverage
        printf '%s\n' "false"
        return 0
    fi

    case "$value" in
        true|false) ;;
        *)
            # BLOCK-WORTHY (docs/blocking-site-census.md): an invariant by
            # design, the same boolean-strictness rule the [release]
            # posture keys follow. 'on', 'yes' or a typo cannot be resolved
            # silently into a coverage setting nobody asked for. The remedy
            # edits the key inside [testing] alone, leaving any same-named
            # key in another section as it was.
            pgai_emit_error_contract \
                "config_loader.testing.invalid_coverage" \
                "config_loader: config_testing_coverage: [testing] coverage='${value}' in ${cfg} is not an accepted value (accepted: true|false; 'on'/'off' are not accepted spellings)." \
                "sed -i '/^[[:space:]]*\\[testing\\][[:space:]]*\$/,/^[[:space:]]*\\[/ s/^[[:space:]]*coverage[[:space:]]*=.*/coverage = false/' \"${cfg}\""
            # CENSUS-ANCHOR: config-loader-testing-coverage-01
            return 1
            ;;
    esac

    printf '%s\n' "$value"
    return 0
}

# ---------------------------------------------------------------------------
# config_testing_pre_merge_affected_cap [cfg]
#
# Resolve [testing] pre_merge_affected_cap — the most affected test files a
# pre-merge run selects — and print it on stdout as plain decimal digits.
# It caps how many test files are chosen, not how many workers run them:
# the run still uses the [testing] workers budget.
#
# 0 means no cap: every affected test file is selected. The affected-test
# finder (pgai_agent_kanban.testing.affected) takes that as cap=None and
# refuses a literal 0, so a caller translates 0 before handing it on; any
# other value is passed through as the finder's cap.
#
# <cfg> defaults to the file the most recent load_config call recorded.
# Absent, blank, or no cfg file at all: prints the default 60 and narrates
# that once per process on stderr. Leading zeros are dropped ('040' -> 40,
# '00' -> 0) so the printed value is safe in bash arithmetic.
#
# Returns 0 with the value on stdout, or 1 with nothing on stdout and the
# dual-channel refusal on stderr when the value is not decimal digits only.
# ---------------------------------------------------------------------------
config_testing_pre_merge_affected_cap() {
    local cfg="${1:-${_CONFIG_LOADER_CFG_PATH}}"
    local value suggested
    value="$(_config_loader_testing_value "$cfg" pre_merge_affected_cap)"

    if [[ -z "$value" ]]; then
        _config_loader_narrate_default_once pre_merge_affected_cap "60" testing config_testing_pre_merge_affected_cap
        printf '%s\n' "60"
        return 0
    fi

    if [[ ! "$value" =~ ^[0-9]+$ ]]; then
        suggested="${value%%[^0-9]*}"
        [[ -n "$suggested" ]] || suggested="60"
        # BLOCK-WORTHY (docs/blocking-site-census.md): an invariant by
        # design, the same rule [testing] workers follows. The value decides
        # how many affected test files a pre-merge run selects; guessing a
        # number from 'abc' or '-5' would run a selection nobody chose, so
        # the pre-merge run is refused before it selects anything. Only that
        # run reads the key, so the refusal never stops a wake or an RC.
        # The remedy edits the key inside [testing] alone, leaving any
        # same-named key in another section as it was.
        pgai_emit_error_contract \
            "config_loader.testing.invalid_pre_merge_affected_cap" \
            "config_loader: config_testing_pre_merge_affected_cap: [testing] pre_merge_affected_cap='${value}' in ${cfg} is not an accepted value; it must be a non-negative whole number in decimal digits (0 = no cap)." \
            "sed -i '/^[[:space:]]*\\[testing\\][[:space:]]*\$/,/^[[:space:]]*\\[/ s/^[[:space:]]*pre_merge_affected_cap[[:space:]]*=.*/pre_merge_affected_cap = ${suggested}/' \"${cfg}\""
        # CENSUS-ANCHOR: config-loader-testing-pre-merge-cap-01
        return 1
    fi

    # Drop leading zeros without arithmetic, which would read them as octal.
    value="${value#"${value%%[!0]*}"}"
    printf '%s\n' "${value:-0}"
    return 0
}

# ---------------------------------------------------------------------------
# config_cleanup_tunables [cfg]
#
# Resolve the [cleanup] keys cleanup.sh runs on and export each one:
#
#   [cleanup] retention_days     -> PGAI_CLEANUP_RETENTION_DAYS     (default 30)
#   [cleanup] trivial_log_bytes  -> PGAI_CLEANUP_TRIVIAL_LOG_BYTES  (default 1700)
#   [cleanup] trivial_log_hours  -> PGAI_CLEANUP_TRIVIAL_LOG_HOURS  (default 6)
#
# Precedence, highest first: a non-empty value already in the environment,
# the [cleanup] key, the default. A blank cfg value reads as absent, and a
# <cfg> that names no regular file resolves every key to its default.
# Values are passed through as written, not validated.
#
# retention_days has one more tier, between [cleanup] and the default: the
# deprecated [paths] cleanup_retention_days, the spelling an older
# kanban.cfg carries. Whenever that key is present and the environment has
# not already decided the value, one WARNING on stderr names it and the
# edit that retires it: its value is used when [cleanup] retention_days is
# absent or blank, and ignored otherwise. Because the resolved value is
# exported, a child process that resolves again inherits it as an
# environment override and does not repeat the warning.
#
# <cfg> defaults to the file the most recent load_config call recorded.
# Narration only, never a refusal: always returns 0.
# ---------------------------------------------------------------------------
config_cleanup_tunables() {
    local cfg="${1:-${_CONFIG_LOADER_CFG_PATH}}"
    local have_cfg=false
    [[ -n "$cfg" && -f "$cfg" ]] && have_cfg=true

    local cleanup_value legacy_value
    if [[ -z "${PGAI_CLEANUP_RETENTION_DAYS:-}" ]]; then
        cleanup_value=""
        legacy_value=""
        if [[ "$have_cfg" == "true" ]]; then
            cleanup_value="$(read_ini "$cfg" cleanup retention_days "" || true)"
            legacy_value="$(read_ini "$cfg" paths cleanup_retention_days "" || true)"
        fi
        if [[ -n "$cleanup_value" ]]; then
            PGAI_CLEANUP_RETENTION_DAYS="$cleanup_value"
            if [[ -n "$legacy_value" ]]; then
                echo "config_loader: config_cleanup_tunables: WARNING: [paths] cleanup_retention_days = ${legacy_value} in ${cfg} is deprecated and ignored; [cleanup] retention_days = ${cleanup_value} is used. Delete the [paths] cleanup_retention_days line." >&2
            fi
        elif [[ -n "$legacy_value" ]]; then
            PGAI_CLEANUP_RETENTION_DAYS="$legacy_value"
            echo "config_loader: config_cleanup_tunables: WARNING: [paths] cleanup_retention_days = ${legacy_value} in ${cfg} is deprecated; using it because [cleanup] retention_days is absent. Set [cleanup] retention_days = ${legacy_value} and delete the [paths] cleanup_retention_days line." >&2
        else
            PGAI_CLEANUP_RETENTION_DAYS="30"
        fi
    fi
    export PGAI_CLEANUP_RETENTION_DAYS

    if [[ -z "${PGAI_CLEANUP_TRIVIAL_LOG_BYTES:-}" ]]; then
        PGAI_CLEANUP_TRIVIAL_LOG_BYTES=""
        [[ "$have_cfg" == "true" ]] \
            && PGAI_CLEANUP_TRIVIAL_LOG_BYTES="$(read_ini "$cfg" cleanup trivial_log_bytes "" || true)"
        PGAI_CLEANUP_TRIVIAL_LOG_BYTES="${PGAI_CLEANUP_TRIVIAL_LOG_BYTES:-1700}"
    fi
    export PGAI_CLEANUP_TRIVIAL_LOG_BYTES

    if [[ -z "${PGAI_CLEANUP_TRIVIAL_LOG_HOURS:-}" ]]; then
        PGAI_CLEANUP_TRIVIAL_LOG_HOURS=""
        [[ "$have_cfg" == "true" ]] \
            && PGAI_CLEANUP_TRIVIAL_LOG_HOURS="$(read_ini "$cfg" cleanup trivial_log_hours "" || true)"
        PGAI_CLEANUP_TRIVIAL_LOG_HOURS="${PGAI_CLEANUP_TRIVIAL_LOG_HOURS:-6}"
    fi
    export PGAI_CLEANUP_TRIVIAL_LOG_HOURS

    return 0
}
