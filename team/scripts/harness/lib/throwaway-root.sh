#!/usr/bin/env bash
# team/scripts/harness/lib/throwaway-root.sh
#
# Generic throwaway-kanban-root builder for demo/behavioral harnesses.
#
# Not hard-wired to any one demo: a caller names a slug and gets back a
# fresh, unique directory suitable for use as PGAI_AGENT_KANBAN_ROOT_PATH,
# plus a helper to stand up a scratch git repo inside that same space. The
# helloworld-flow-check.sh harness is the first consumer; a later loop
# pointing a different demo at this file should not need to edit it.
#
# Public functions
# ----------------
#   harness_new_throwaway_root <slug>
#       Echoes the path of a freshly created, uniquely named directory
#       under $PGAI_AGENT_KANBAN_TEMP_DIR/harness/<slug>/ (resolved via
#       team/scripts/lib/temp.sh's pgai_temp_dir — never a bare tmp path).
#       Every call returns a brand-new directory, so consecutive runs never
#       collide and never require the caller to clean up a previous run's
#       output before starting a new one.
#
#   harness_init_scratch_repo <repo_path> <main_branch>
#       Creates <repo_path>, runs `git init --initial-branch=<main_branch>`,
#       sets repo-local user.name/user.email (never touches ~/.gitconfig),
#       and creates one initial empty commit on <main_branch>. Mirrors
#       team/demos/*/README.md "make a local scratch repo" setup steps.
#       Refuses rather than acting when <repo_path> already
#       resolves to a git repository -- fresh or not, born or unborn --
#       because that shape means the caller pointed this function at an
#       existing checkout instead of a throwaway scratch directory, and
#       the `git commit --allow-empty` step below would otherwise plant a
#       real, unaccounted-for commit into whatever that checkout has
#       currently checked out. Always pass a directory this function (or
#       harness_new_throwaway_root) created fresh.
#
# Sourcing
# --------
#   SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
#   source "${SCRIPT_DIR}/lib/throwaway-root.sh"
#
# This file sources team/scripts/lib/temp.sh itself (self-locating from its
# own path two directories up: harness/lib -> scripts/lib) so callers do not
# need to source it separately first.

[[ -n "${_HARNESS_THROWAWAY_ROOT_SH_LOADED:-}" ]] && return 0
_HARNESS_THROWAWAY_ROOT_SH_LOADED=1

_HARNESS_THROWAWAY_ROOT_LIB_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=../../lib/temp.sh
source "${_HARNESS_THROWAWAY_ROOT_LIB_DIR}/../../lib/temp.sh"

# ---------------------------------------------------------------------------
# harness_new_throwaway_root <slug>
# ---------------------------------------------------------------------------
harness_new_throwaway_root() {
  local slug="${1:?harness_new_throwaway_root: slug argument required}"
  local parent
  parent="$(pgai_temp_subdir "harness/${slug}")"
  local root
  root="$(mktemp -d "${parent}/run.XXXXXX")"
  printf '%s\n' "$root"
}

# ---------------------------------------------------------------------------
# harness_init_scratch_repo <repo_path> <main_branch>
# ---------------------------------------------------------------------------
harness_init_scratch_repo() {
  local repo_path="${1:?harness_init_scratch_repo: repo_path argument required}"
  local main_branch="${2:?harness_init_scratch_repo: main_branch argument required}"

  mkdir -p "$repo_path"

  # Refuse a directory that is already inside a git repository:
  # `git -C <dir> rev-parse --git-dir` succeeds whether <dir> IS a repo
  # root or is merely NESTED inside one, so this catches both "repo_path
  # is the caller's real checkout" and "repo_path is a subdirectory of
  # it" -- the two shapes that let this function's empty commit land
  # somewhere it does not belong. A directory this function has never
  # touched (the only contract it promises) has no .git anywhere above
  # it, so the check never false-positives on the normal call pattern.
  if git -C "$repo_path" rev-parse --git-dir >/dev/null 2>&1; then
    echo "harness_init_scratch_repo: refusing -- '${repo_path}' is already inside a git repository." >&2
    echo "This function only initializes a FRESH scratch repo; it must never run against an existing checkout." >&2
    echo "Pass a fresh, empty directory instead (e.g. one returned by harness_new_throwaway_root)." >&2
    return 1
  fi

  git -C "$repo_path" init --initial-branch="$main_branch" >/dev/null
  git -C "$repo_path" config --local user.email "harness@pgai.local"
  git -C "$repo_path" config --local user.name "PGAI Harness"
  git -C "$repo_path" commit --allow-empty -m "initial empty commit" >/dev/null
}
