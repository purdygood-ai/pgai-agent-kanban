#!/usr/bin/env bash
# team/scripts/migrate/bug-status-done.sh
#
# One-time recovery: promote bug/priority items from "running" to "done" for
# all items that appeared in any already-shipped requirements bundle.
#
# Background:
#   The cm-release.sh success path (Step 16b) promotes bundled items from
#   "running" to "done" on each release. But items bundled before that step
#   was introduced remain stuck at "running" even though their RCs already shipped.
#   This script performs the retroactive promotion for those items.
#
# What this script does:
#   1. Discovers all requirements bundle files under
#      $KANBAN_ROOT/projects/<name>/requirements/*.md that have a ## Target Version
#      whose corresponding semver tag exists in the dev tree (i.e., the RC shipped).
#   2. For each shipped bundle, parses its ## Bundled Items section to extract
#      the absolute paths of referenced bug/priority files.
#   3. For each referenced file that exists AND whose ## Status is "running",
#      rewrites ## Status to "done".
#   4. Items whose ## Status is "open", "done", or anything other than "running"
#      are left untouched.
#   5. Items that do NOT appear in any shipped bundle are left untouched.
#
# This script is IDEMPOTENT — safe to run multiple times.
#
# Usage:
#   bash team/scripts/migrate/bug-status-done.sh [--dry-run]
#
# Options:
#   --dry-run   Print what would be changed without writing any files.
#
# Dependencies:
#   - python3 (stdlib only)
#   - git (for tag existence checks)
#   - PGAI_AGENT_KANBAN_ROOT_PATH (or defaults to ~/pgai_agent_kanban)
#   - The project's project.cfg must contain a valid dev_tree_path entry

set -euo pipefail

# --- Parse arguments ---
DRY_RUN=false
for arg in "$@"; do
  case "$arg" in
    --dry-run) DRY_RUN=true ;;
    *) echo "Unknown argument: $arg" >&2; exit 1 ;;
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
_SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
if [[ ! -f "$_SCRIPT_DIR/../lib/project_paths.sh" ]]; then
  echo "ERROR: lib/project_paths.sh not found relative to $0" >&2
  exit 1
fi
# project_paths.sh needs KANBAN_ROOT exported
export KANBAN_ROOT
# shellcheck source=lib/project_paths.sh
source "$_SCRIPT_DIR/../lib/project_paths.sh"

echo "=== migrate-bug-status-done.sh ==="
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
    dev_tree=""
  fi

  # Build set of shipped version tags from the dev tree (tags on any branch,
  # not just origin/main, since we want local tags too for freshly-released RCs).
  declare -A shipped_tags=()
  if [[ -n "$dev_tree" ]] && git -C "$dev_tree" rev-parse --git-dir &>/dev/null 2>&1; then
    while IFS= read -r tag; do
      [[ -n "$tag" ]] && shipped_tags["$tag"]=1
    done < <(git -C "$dev_tree" tag --list 'v*' 2>/dev/null \
              | grep -E '^v[0-9]+\.[0-9]+\.[0-9]+$' || true)
  fi

  # Delegate the actual file processing to Python for robust markdown parsing.
  python3 - "$requirements_dir" "$DRY_RUN" "${!shipped_tags[@]}" <<'PYEOF'
import sys, re, pathlib

requirements_dir = pathlib.Path(sys.argv[1])
dry_run = sys.argv[2].lower() == "true"
shipped_tags = set(sys.argv[3:])  # may be empty if dev_tree unavailable

# Regex: parse ## Target Version field
_TARGET_VERSION_RE = re.compile(
    r'^##\s+Target Version\s*\n\s*(v[\d.]+)',
    re.MULTILINE | re.IGNORECASE,
)

# Regex: find ## Bundled Items section
_BUNDLED_ITEMS_HDR_RE = re.compile(
    r'^##\s+Bundled Items\s*$',
    re.MULTILINE | re.IGNORECASE,
)

# Regex: extract absolute path from backtick-paren notation on a line
_ITEM_PATH_RE = re.compile(r'\(\`([^`]+)\`\)')

# Regex: ## Status heading + value on the next line (for "running")
_STATUS_RUNNING_RE = re.compile(
    r'^(##\s+Status\s*\n\s*)running(\s*)$',
    re.MULTILINE | re.IGNORECASE,
)
_STATUS_ANY_RE = re.compile(
    r'^##\s+Status\s*\n\s*(\S+)',
    re.MULTILINE | re.IGNORECASE,
)

promoted = 0
skipped_not_running = 0
skipped_missing = 0
bundles_processed = 0
bundles_shipped = 0

# Iterate all bundle files (any .md matching a bundle pattern).
for bundle_file in sorted(requirements_dir.glob('*.md')):
    if bundle_file.name.lower() in ('readme.md',):
        continue
    # Only process bundle files (name contains "bundle")
    if 'bundle' not in bundle_file.name.lower():
        continue

    try:
        text = bundle_file.read_text(encoding='utf-8')
    except OSError as e:
        print(f"  WARN: cannot read {bundle_file.name}: {e}", flush=True)
        continue

    bundles_processed += 1

    # Extract target version from the bundle file.
    tv_m = _TARGET_VERSION_RE.search(text)
    if not tv_m:
        print(f"  skip (no ## Target Version): {bundle_file.name}", flush=True)
        continue
    target_version = tv_m.group(1).strip()

    # Determine if this version shipped.
    # If no shipped_tags are available (no dev_tree), treat all bundles as shipped.
    if shipped_tags and target_version not in shipped_tags:
        # Not yet shipped — skip.
        continue

    bundles_shipped += 1
    print(f"  bundle: {bundle_file.name} (version={target_version}, SHIPPED)", flush=True)

    # Parse ## Bundled Items section.
    lines = text.splitlines()
    in_section = False
    item_paths = []
    for line in lines:
        if _BUNDLED_ITEMS_HDR_RE.match(line):
            in_section = True
            continue
        if in_section:
            if re.match(r'^\s*##', line):
                break
            m = _ITEM_PATH_RE.search(line)
            if m:
                item_paths.append(m.group(1).strip())

    if not item_paths:
        print(f"    no bundled item paths found", flush=True)
        continue

    for path_str in item_paths:
        p = pathlib.Path(path_str)
        if not p.exists():
            print(f"    WARN: item not found (skipped): {path_str}", flush=True)
            skipped_missing += 1
            continue

        try:
            content = p.read_text(encoding='utf-8')
        except OSError as e:
            print(f"    WARN: cannot read {p.name}: {e}", flush=True)
            skipped_missing += 1
            continue

        status_m = _STATUS_RUNNING_RE.search(content)
        if not status_m:
            cur_m = _STATUS_ANY_RE.search(content)
            cur_val = cur_m.group(1) if cur_m else "no-status-field"
            print(f"    skip (status={cur_val}): {p.name}", flush=True)
            skipped_not_running += 1
            continue

        if dry_run:
            print(f"    [DRY RUN] would promote (running -> done): {p.name}", flush=True)
        else:
            new_content = (
                content[:status_m.start()]
                + status_m.group(1)
                + "done"
                + status_m.group(2)
                + content[status_m.end():]
            )
            p.write_text(new_content, encoding='utf-8')
            print(f"    promoted (running -> done): {p.name}", flush=True)
        promoted += 1

print(f"", flush=True)
print(f"  Bundles processed: {bundles_processed}", flush=True)
print(f"  Bundles shipped:   {bundles_shipped}", flush=True)
print(f"  Items promoted:    {promoted}", flush=True)
print(f"  Items skipped (not running): {skipped_not_running}", flush=True)
print(f"  Items missing:     {skipped_missing}", flush=True)
PYEOF

  echo ""
done

echo "=== migrate-bug-status-done.sh: complete ==="
[[ "$DRY_RUN" == "true" ]] && echo "(DRY RUN — no files were written)"
