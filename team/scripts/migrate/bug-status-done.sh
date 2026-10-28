#!/usr/bin/env bash
# team/scripts/migrate/bug-status-done.sh
#
# One-time recovery: promote bug/priority items from "running" to "done" for
# all items that appeared in any already-shipped requirements bundle.
#
# Background:
#   The cm/release.sh success path (Step 16b) promotes bundled items from
#   "running" to "done" on each release. But items bundled before that step
#   was introduced remain stuck at "running" even though their RCs already shipped.
#   This script performs the retroactive promotion for those items.
#
# What this script does:
#   1. Discovers all requirements bundle files under
#      $KANBAN_ROOT/projects/<name>/requirements/*.md that have a ## Target Version
#      whose release tag exists in the project's dev tree (i.e., the RC shipped).
#      The release tag is the project's branch_prefix (project.cfg) followed by
#      the version: ai_vX.Y.Z with branch_prefix = ai_, bare vX.Y.Z with none.
#      A tag in the other form belongs to another lane and does not count.
#   2. For each shipped bundle, parses its ## Bundled Items section to extract
#      the absolute paths of referenced bug/priority files.
#   3. For each referenced file that exists AND whose ## Status is "running",
#      rewrites ## Status to "done".
#   4. Items whose ## Status is "open", "done", or anything other than "running"
#      are left untouched.
#   5. Items that do NOT appear in any shipped bundle are left untouched.
#
# Which bundles count as shipped, per project:
#   - Dev tree readable: only those whose release tag exists. A dev tree with
#     no release tag yet promotes nothing, and says so.
#   - Dev tree is not a git repository, or branch_prefix is invalid: nothing is
#     promoted, with a WARNING on stderr naming what to fix.
#   - No project.cfg, no dev_tree_path, or a path that does not exist: a
#     WARNING on stderr, and every bundle is treated as shipped.
#
# This script is IDEMPOTENT — safe to run multiple times.
#
# Usage:
#   bash team/scripts/migrate/bug-status-done.sh [--dry-run] [--help]
#
# Options:
#   --dry-run   Print what would be changed without writing any files.
#   --help, -h  Print this usage and exit 0 without reading or writing anything.
#
# Exit codes:
#   0 — success (or --help)
#   1 — unknown argument, kanban root not found, or a missing helper library
#
# Dependencies:
#   - python3: each project's requirements directory is processed by
#     pgai_agent_kanban.cm.shipped_bundle_items, run through pp_run_ops from
#     this script's own tree
#   - git (for tag existence checks)
#   - PGAI_AGENT_KANBAN_ROOT_PATH (or defaults to ~/pgai_agent_kanban)
#   - The project's project.cfg must contain a valid dev_tree_path entry;
#     its branch_prefix (optional) is read by pp_branch_prefix

set -euo pipefail

_SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=../lib/header_help.sh
source "$_SCRIPT_DIR/../lib/header_help.sh"

# --- Parse arguments ---
DRY_RUN=false
for arg in "$@"; do
  case "$arg" in
    --help|-h) header_help_print "${BASH_SOURCE[0]}"; exit 0 ;;
    --dry-run) DRY_RUN=true ;;
    *) echo "Unknown argument: $arg (run with --help for usage)" >&2; exit 1 ;;
  esac
done

# --- Resolve kanban root ---
KANBAN_ROOT="${PGAI_AGENT_KANBAN_ROOT_PATH:-$HOME/pgai_agent_kanban}"

if [[ ! -d "$KANBAN_ROOT" ]]; then
  echo "ERROR: kanban root not found: $KANBAN_ROOT" >&2
  echo "Set PGAI_AGENT_KANBAN_ROOT_PATH or run install.sh first." >&2
  exit 1
fi

# --- Source project_paths.sh helpers ---
if [[ ! -f "$_SCRIPT_DIR/../lib/project_paths.sh" ]]; then
  echo "ERROR: lib/project_paths.sh not found relative to $0" >&2
  exit 1
fi
# project_paths.sh needs KANBAN_ROOT exported
export KANBAN_ROOT
# shellcheck source=lib/project_paths.sh
source "$_SCRIPT_DIR/../lib/project_paths.sh"
# pp_run_ops: runs the bundle-processing module from this script's own tree.
# shellcheck source=lib/pp_run_ops.sh
source "$_SCRIPT_DIR/../lib/pp_run_ops.sh"

_dry_run_args=()
if [[ "$DRY_RUN" == "true" ]]; then
  _dry_run_args=(--dry-run)
fi

echo "=== scripts/migrate/bug-status-done.sh ==="
echo "Kanban root: $KANBAN_ROOT"
[[ "$DRY_RUN" == "true" ]] && echo "(DRY RUN — no files will be written)"
echo ""

declare -i total_promoted=0
declare -i total_skipped_not_running=0
declare -i total_skipped_missing=0
declare -i total_bundles_processed=0
declare -i total_bundles_shipped=0

# --- Iterate every project ---
for project_dir in "$KANBAN_ROOT/projects"/*/; do
  [[ -d "$project_dir" ]] || continue
  project_name="$(basename "$project_dir")"
  echo "Project: $project_name"

  requirements_dir="${project_dir}requirements"
  if [[ ! -d "$requirements_dir" ]]; then
    echo "  no requirements/ directory — skipping"
    continue
  fi

  # Locate the dev tree for this project (needed for git tag checks).
  # Prefer project.cfg (lowercase); fall back to PROJECT.cfg for legacy installs.
  cfg_file=""
  if [[ -f "${project_dir}project.cfg" ]]; then
    cfg_file="${project_dir}project.cfg"
  elif [[ -f "${project_dir}PROJECT.cfg" ]]; then
    cfg_file="${project_dir}PROJECT.cfg"
  fi
  dev_tree=""
  if [[ -n "$cfg_file" ]]; then
    dev_tree="$(grep -E '^[[:space:]]*dev_tree_path[[:space:]]*=' "$cfg_file" \
      | head -n1 \
      | sed 's/^[^=]*=[[:space:]]*//; s/^["'"'"']//; s/["'"'"']$//')"
  fi

  if [[ -z "$dev_tree" || ! -d "$dev_tree" ]]; then
    echo "  WARNING: dev_tree_path not found in project.cfg — cannot check shipped tags." >&2
    echo "  All 'running' items in any bundle will be treated as shipped." >&2
    pp_run_ops pgai_agent_kanban.cm.shipped_bundle_items "${_dry_run_args[@]}" \
      --treat-all-shipped -- "$requirements_dir"
    echo ""
    continue
  fi

  # Shipped versions: the project's release tags in the dev tree (tags on any
  # branch, local ones included, so a freshly released RC counts), each with
  # the branch_prefix stripped to the bare vX.Y.Z a bundle's Target Version
  # carries. With branch_prefix = ai_ only ai_vX.Y.Z counts; with none, only
  # vX.Y.Z. Anything that stops the tags being read leaves the set empty, and
  # an empty set promotes nothing.
  declare -A shipped_tags=()
  tag_prefix=""
  if ! tag_prefix="$(pp_branch_prefix "$project_name")"; then
    echo "  WARNING: invalid branch_prefix in $cfg_file — cannot name this project's release tags; nothing is promoted." >&2
    echo "  Set [project] branch_prefix to letters, digits, _ and - only (e.g. branch_prefix = ai_), or leave it empty." >&2
  elif ! git -C "$dev_tree" rev-parse --git-dir &>/dev/null; then
    echo "  WARNING: dev tree $dev_tree is not a git repository — cannot check shipped tags; nothing is promoted." >&2
    echo "  Point [project] dev_tree_path in $cfg_file at the project's git repository." >&2
  else
    while IFS= read -r tag; do
      [[ -n "$tag" ]] && shipped_tags["${tag#"$tag_prefix"}"]=1
    done < <(git -C "$dev_tree" tag --list "${tag_prefix}v*" 2>/dev/null \
              | grep -E "^${tag_prefix}v[0-9]+\.[0-9]+\.[0-9]+\$" || true)
    if [[ ${#shipped_tags[@]} -eq 0 ]]; then
      echo "  no release tag matching ${tag_prefix}vX.Y.Z in dev tree $dev_tree"
    fi
  fi

  # Promote the running items of this project's shipped bundles
  # (pgai_agent_kanban.cm.shipped_bundle_items.promote_shipped_bundle_items;
  # every ## section is read by pgai_agent_kanban.task_status.read_section).
  # An empty set promotes nothing.
  pp_run_ops pgai_agent_kanban.cm.shipped_bundle_items "${_dry_run_args[@]}" -- \
    "$requirements_dir" "${!shipped_tags[@]}"

  echo ""
done

echo "=== scripts/migrate/bug-status-done.sh: complete ==="
# An if, not `[[ ]] && echo`: a false test as the last command would
# become the script's exit status and turn a successful real run into 1.
if [[ "$DRY_RUN" == "true" ]]; then
  echo "(DRY RUN — no files were written)"
fi
