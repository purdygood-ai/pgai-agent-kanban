#!/usr/bin/env bash
# subagent_deploy.sh
# Shared helper for provider-agent wrapper deployment. Sourced by both
# install.sh and team/scripts/upgrade.sh so the two entry points share one
# implementation of the agents-directory resolution and creation steps and
# the read-only deployed-wrapper comparison, instead of drifting apart with
# copy-pasted logic.
#
# Depends on the sourcing script defining info()/ok()/err() (both install.sh
# and upgrade.sh define these with matching signatures: info()/ok() write a
# narration line to stdout, err() writes to stderr).

# ---------------------------------------------------------------------------
# provider_agents_dir <provider>
#
# Prints the directory a provider's agent wrappers are deployed to:
#   claude  $CLAUDE_CONFIG_DIR/agents when CLAUDE_CONFIG_DIR is set and
#           non-empty (the directory Claude Code reads its config from),
#           otherwise $HOME/.claude/agents.
#   other   $HOME/.<provider>/agents (e.g. $HOME/.codex/agents).
#
# Arguments:
#   provider  Provider name as listed in kanban.cfg [providers] available.
# ---------------------------------------------------------------------------
provider_agents_dir() {
  local provider="$1"
  case "$provider" in
    claude) printf '%s' "${CLAUDE_CONFIG_DIR:-$HOME/.claude}/agents" ;;
    *)      printf '%s' "$HOME/.${provider}/agents" ;;
  esac
}

# ---------------------------------------------------------------------------
# ensure_provider_agents_dir <agents_dir> [dry_run]
#
# Ensures <agents_dir> exists, creating it with `mkdir -p` when absent, so a
# provider-agent wrapper deploy never silently no-ops just because the
# target directory has not been created yet by the provider CLI.
#
# Arguments:
#   agents_dir  Absolute path to the provider's agents directory, as
#               printed by provider_agents_dir (e.g. ~/.claude/agents,
#               ~/.codex/agents).
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

# ---------------------------------------------------------------------------
# subagent_wrapper_drift <source_dir> <agents_dir> <name>...
#
# Read-only comparison of deployed provider-agent wrappers against the
# wrappers this tree ships. For each <name>, compares
# <source_dir>/<name>.md (the shipped wrapper, e.g. subagents/coder.md) with
# <agents_dir>/<name>.md (the deployed copy) and prints one line per wrapper
# that is out of date, in manifest order:
#
#   missing <name>   the deployed copy does not exist
#   differs <name>   the deployed copy exists but is not byte-identical
#
# Wrappers that match print nothing, so empty output means every named
# wrapper is current. A name whose shipped source is absent is skipped —
# there is nothing to compare it against (the deploy loop narrates that
# case on its own).
#
# Never writes, creates, or removes anything: callers use it to report
# drift on the protected (no opt-in flag) path, where nothing under a
# provider's agents directory may change. Needs no narration helpers.
#
# Arguments:
#   source_dir  Directory holding the shipped wrappers (<repo>/subagents).
#   agents_dir  Provider agents directory, as printed by provider_agents_dir.
#   name...     Wrapper names as listed in subagents/MANIFEST.txt.
#
# Returns:
#   0 — comparison ran (output lists any drift).
#   1 — <agents_dir> is not a directory; nothing was compared. Callers
#       decide what an absent directory means (upgrade.sh skips it).
#   2 — usage error: fewer than two arguments.
# ---------------------------------------------------------------------------
subagent_wrapper_drift() {
  if [[ $# -lt 2 ]]; then
    printf 'subagent_wrapper_drift: usage: subagent_wrapper_drift <source_dir> <agents_dir> <name>...\n' >&2
    return 2
  fi
  local source_dir="$1"
  local agents_dir="$2"
  shift 2

  [[ -d "$agents_dir" ]] || return 1

  local name src dst
  for name in "$@"; do
    src="${source_dir}/${name}.md"
    dst="${agents_dir}/${name}.md"
    [[ -f "$src" ]] || continue
    if [[ ! -f "$dst" ]]; then
      printf 'missing %s\n' "$name"
    elif ! cmp -s "$src" "$dst"; then
      printf 'differs %s\n' "$name"
    fi
  done
  return 0
}
