#!/usr/bin/env bash
# subagent_deploy.sh
# Shared helper for provider-agent wrapper deployment. Sourced by both
# install.sh and team/scripts/upgrade.sh so the two entry points share one
# implementation of the agents-directory creation step instead of drifting
# apart with copy-pasted logic.
#
# Depends on the sourcing script defining info()/ok()/err() (both install.sh
# and upgrade.sh define these with matching signatures: info()/ok() write a
# narration line to stdout, err() writes to stderr).

# ---------------------------------------------------------------------------
# ensure_provider_agents_dir <agents_dir> [dry_run]
#
# Ensures <agents_dir> exists, creating it with `mkdir -p` when absent, so a
# provider-agent wrapper deploy never silently no-ops just because the
# target directory has not been created yet by the provider CLI.
#
# Arguments:
#   agents_dir  Absolute path to the provider's agents directory
#               (e.g. ~/.claude/agents, ~/.codex/agents).
#   dry_run     "true" or "false" (default "false"). In dry-run mode a
#               missing directory is reported but not created, so the
#               caller's dry-run reporting can continue into the per-agent
#               copy loop without mutating the filesystem.
#
# Narration:
#   - Directory already exists: silent (unchanged behavior).
#   - Directory created:        "created <path>" via ok().
#   - Dry-run, dir missing:     "[dry-run] would create directory: <path>"
#                                via info().
#   - Creation fails (permissions, read-only HOME, missing parent, etc.):
#     a loud error naming the failing path via err(); the function returns
#     1 and the caller must treat that as fatal (exit non-zero) rather than
#     skip the provider silently.
#
# Returns:
#   0 — directory exists, was created, or dry-run reported without creating.
#   1 — directory creation failed.
# ---------------------------------------------------------------------------
ensure_provider_agents_dir() {
  local agents_dir="$1"
  local dry_run="${2:-false}"

  if [[ -d "$agents_dir" ]]; then
    return 0
  fi

  if [[ "$dry_run" == "true" ]]; then
    info "[dry-run] would create directory: $agents_dir"
    return 0
  fi

  if ! mkdir -p "$agents_dir" 2>/dev/null; then
    err "Failed to create provider agents directory: $agents_dir"
    err "Check write permissions on the parent directory (HOME may be read-only)."
    return 1
  fi

  ok "created $agents_dir"
  return 0
}
