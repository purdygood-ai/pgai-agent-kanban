#!/usr/bin/env bash
# team/scripts/lib/wake_guard_wiring.sh
# Wires the git remote-boundary guard (team/scripts/lib/git_boundary_guard.sh)
# into wake dispatch: activates the "CM is the ONLY toucher of any remote"
# boundary (team/SOP-core.md, "Git Repositories: Roles and Boundaries") for
# one dispatched task. Single-concern stage, extracted from
# team/scripts/lib/wake_driver_invoke.sh so the mechanism and the
# documentation of both exported variables live together, and so
# wake_driver_invoke.sh's own size stays under the 800-line-per-file ceiling
# (team/scripts/lint_engine_size_dupe.py) enforced across the wake pipeline's
# stage files. Behavior-preserving move: same exports, same PATH mutation,
# same fail-clean-on-missing-shim-directory warning as when this code lived
# inline in wake_driver_invoke.sh.
#
# Public entry point
# -------------------
#   wd_install_git_boundary_guard
#       Exports, per SOP-core's Documentation Discipline Rule, the two
#       variables team/scripts/lib/git_boundary_guard.sh reads, plus the
#       local-only flag the framework's version lookups read:
#         PGAI_AGENT_ROLE                        The dispatched SUBAGENT
#                                                 (lowercase: coder, writer,
#                                                 cm, tester, po, pm) — the
#                                                 guard's positive, explicit
#                                                 role signal. Never inferred
#                                                 from process name, TTY, or
#                                                 an unrelated variable.
#                                                 Exported for every role,
#                                                 including cm, so the
#                                                 guard's own role=="cm"
#                                                 pass-through path (rather
#                                                 than its
#                                                 role-undeterminable
#                                                 fail-open path) is what
#                                                 lets CM's remote
#                                                 operations through.
#         PGAI_GIT_BOUNDARY_GUARD_EVIDENCE_FILE  Absolute path to this
#                                                 task's own evidence file,
#                                                 always under its logs/
#                                                 directory:
#                                                 <task_log_dir>/git-boundary-guard.jsonl.
#                                                 Never written to bare
#                                                 /tmp.
#         PGAI_GIT_LOCAL_ONLY                    Exported as "1" for every
#                                                 SUBAGENT other than "cm";
#                                                 never set, cleared, or
#                                                 otherwise touched for cm,
#                                                 whose fetches are the ones
#                                                 the boundary exists to let
#                                                 through. Read by the
#                                                 version lookup
#                                                 (project_paths.sh's
#                                                 pp_last_released_version)
#                                                 and the tag probe
#                                                 (discovery_guards.sh's
#                                                 _disc_git_tag_exists):
#                                                 while set, both answer
#                                                 from the refs already on
#                                                 disk instead of reaching
#                                                 for origin first. A non-CM
#                                                 role's reach is refused by
#                                                 the guard below in any
#                                                 case, so skipping it
#                                                 changes no answer -- it
#                                                 only stops an evidence
#                                                 record being spent on a
#                                                 crossing nobody attempted.
#                                                 Setting the flag HERE, in
#                                                 the launcher, keeps that
#                                                 suppression in force
#                                                 whatever copy of
#                                                 project_paths.sh the
#                                                 dispatched task ends up
#                                                 sourcing (an installed
#                                                 copy may lag the tree). It
#                                                 suppresses only those two
#                                                 lookups' own reach, never
#                                                 the guard: a genuine
#                                                 non-CM fetch, pull or push
#                                                 is still refused and still
#                                                 recorded exactly once.
#       Then, for every SUBAGENT other than "cm" whose shim executable
#       (<SCRIPT_DIR>/git-guard/git — the file the guard actually needs,
#       not merely the directory that is meant to hold it) is present and
#       executable, pins GIT_GUARD_REAL_GIT (when not already set — an
#       operator's own export always wins) to the first "git" the
#       dispatch's PATH resolves to AT THIS POINT, before the shim
#       directory below is added ahead of it. This is the one moment in
#       the whole dispatch where that PATH lookup is guaranteed to land on
#       the real binary rather than looping back through the shim itself:
#       every git invocation the dispatched task makes afterwards —
#       including one a test fixture routes through its own PATH-shadowing
#       stand-in "git" placed ahead of everything for its own purposes —
#       inherits this single pinned value instead of each one repeating
#       PATH resolution against an environment that may, by then, no
#       longer have an unshimmed "git" anywhere on it at all. ONE exception
#       to that inheritance: the guard's own test module
#       (team/tests/unit/test_git_boundary_guard.py) is exactly such a
#       fixture, and it asserts on what the shim does with ITS stub, not
#       the real binary this pin resolves to. Both gated runners
#       (team/scripts/run-unit-tests.sh, team/scripts/run-integration-
#       tests.sh) scrub GIT_GUARD_REAL_GIT and PGAI_GIT_GUARD_DEPTH from
#       their own process immediately before launching pytest, so that
#       module's assertions hold whether or not the session invoking the
#       runner was itself guarded. The module also scrubs itself: an
#       autouse fixture clears both variables from every test's own
#       environment (monkeypatch.delenv, non-raising), so the same module
#       is green whether pytest is launched by a gated runner, by hand
#       from a clean shell, or by hand from inside a guarded agent session
#       that still exports the ambient pin — the runner-level scrub above
#       remains the authoritative signal and stays in place; the module's
#       own scrub is additive, not a substitute for it. This pin's
#       inheritance model is otherwise unchanged and every other dispatched
#       task still gets it.
#       Then, for
#       every SUBAGENT other than "cm", prepends <SCRIPT_DIR>/git-guard
#       (the executable shim, team/scripts/wake/git-guard/git) ahead of
#       the real git on PATH. CM's PATH never gains the shim directory,
#       and CM never gets a GIT_GUARD_REAL_GIT pin either, so CM's git
#       resolution and every CM release operation are byte-identical to
#       an install without this guard. If the shim executable is missing
#       — whether its directory does not exist at all, or exists but does
#       not hold an executable "git" file, the state an install that
#       failed to deposit the shim leaves behind — dispatch still proceeds
#       (fail clean, not fail closed) and a warning naming the missing
#       directory is logged via log() (wake_bootstrap.sh); no pin is set
#       in that case either, since there is no shim for it to protect
#       anything from.
#
# Contract: called from within the caller's own stack frame (never in a
# subshell) and relies on bash's dynamic scoping to read
# wd_invoke_task's/process_one_task's own `local` variables directly, the
# same convention wake_driver_invoke.sh's sibling stage files use. Reads:
#   SUBAGENT task_log_dir task_id SCRIPT_DIR
# and exports PGAI_AGENT_ROLE, PGAI_GIT_BOUNDARY_GUARD_EVIDENCE_FILE,
# (for every non-cm SUBAGENT) PGAI_GIT_LOCAL_ONLY, (conditionally)
# GIT_GUARD_REAL_GIT, and (conditionally) a mutated PATH into the calling
# shell's own environment — a subshell call ($(...), a pipeline stage) would
# discard all five.
#
# Dependencies: log() (wake_bootstrap.sh). Sourced by
# team/scripts/lib/wake_driver.sh ahead of wake_driver_invoke.sh, the same
# way read_order.sh is; never invoked standalone.
# ---------------------------------------------------------------------------

wd_install_git_boundary_guard() {
  export PGAI_AGENT_ROLE="$SUBAGENT"
  export PGAI_GIT_BOUNDARY_GUARD_EVIDENCE_FILE="${task_log_dir}/git-boundary-guard.jsonl"
  if [[ "$SUBAGENT" != "cm" ]]; then
    # Version lookups answer from local refs for every non-CM dispatch; the
    # guard below would refuse their fetch anyway (see the header entry).
    export PGAI_GIT_LOCAL_ONLY=1
    local _git_boundary_guard_shim_dir="${SCRIPT_DIR}/git-guard"
    local _git_boundary_guard_shim_file="${_git_boundary_guard_shim_dir}/git"
    if [[ -f "$_git_boundary_guard_shim_file" && -x "$_git_boundary_guard_shim_file" ]]; then
      if [[ -z "${GIT_GUARD_REAL_GIT:-}" ]]; then
        local _git_boundary_guard_real_git
        _git_boundary_guard_real_git="$(command -v git 2>/dev/null || true)"
        if [[ -n "$_git_boundary_guard_real_git" && -x "$_git_boundary_guard_real_git" ]]; then
          export GIT_GUARD_REAL_GIT="$_git_boundary_guard_real_git"
        fi
      fi
      export PATH="${_git_boundary_guard_shim_dir}:${PATH}"
    else
      log "WARNING: git-boundary-guard shim directory not found at ${_git_boundary_guard_shim_dir}; dispatching task ${task_id} (role ${SUBAGENT}) without the guard on PATH"
    fi
  fi
}
