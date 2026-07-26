#!/usr/bin/env bash
# team/scripts/unwind-rc.sh
# Operator script: fully unwinds an in-flight release candidate across all state stores.
#
# Usage:
#   unwind-rc.sh --project <name> --key <vX.Y.Z> [--dry-run] [--force]
#
# Options:
#   --project NAME   project name; must match a directory under projects/<name>/ and
#                    be registered in projects.cfg
#   --key VERSION    RC version to unwind; must match the Active RC in release-state.md
#                    (or use --force to bypass the Active-RC-matches-version check)
#   --dry-run        print the unwind plan and exit 0 without modifying anything
#   --force          bypass the Active-RC-version mismatch check; does NOT bypass the
#                    project-existence, version-format, or shipped-tag checks
#   --help, -h       print this help and exit 0
#
# Pre-flight checks (any failure exits 1):
#   1. HALT file in place — either $KANBAN_ROOT/HALT (global) or
#      $KANBAN_ROOT/projects/<name>/HALT (per-project).  If neither exists,
#      error with instructions to halt first.
#   2. Project directory exists under projects/<name>/.
#   3. Version not in git tag list (unwinding a shipped tag is not supported).
#   4. Active RC in release-state.md matches <version>  (bypassable with --force).
#
# Actions:
#   a. Backup key state stores to <temp_root>/unwind-rc-<version>-backup-<ts>/
#   b. Inventory + confirmation prompt (or skip if --force/--dry-run)
#   c. Git unwind: checkout prefixed main, delete local and remote rc/<version>
#   d. Task folder state: mark matching task folders WONT-DO
#   e. Queue caches: flip matching entries to [x]
#   f. Requirements file: rename to *.SUPERSEDED-on-cancel-<ts>.md
#   g. PM plan markers: remove matching .materialized.* files
#   h. Priority backlog: flip bundled PRIORITY entries back to [ ]
#   i. Bug backlog: flip bundled BUG entries back to [ ]
#   j. release-state.md: reset Active RC to none
#   k. Discovery state cache: remove cancelled-version-related files
#   l. Per-RC release-state JSON: flip outcome to cancelled (write_rc_state.py cancel)
#
# Exit codes:
#   0   success (or --dry-run after printing plan)
#   1   pre-flight failure or user declined confirmation
#   2   partial completion (backup created but a step failed; inspect and recover)
#
# Configuration:
#   KANBAN_ROOT  — path to the kanban live install root
#                  (default: $HOME/pgai_agent_kanban)
#   PGAI_DEV_TREE_PATH — path to the dev tree git clone
#                  (derived from project config when not set)

# ---------------------------------------------------------------------------
# Argument parsing
# Done before strict mode so error messages print cleanly on bad flags.
# ---------------------------------------------------------------------------
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

# Source operator_args.sh (canonical flag parsing and --help rendering).
# shellcheck source=lib/operator_args.sh
source "${SCRIPT_DIR}/lib/operator_args.sh"

# Declared flag vocabulary: drives --help output; flags the command accepts.
OPERATOR_VALID_FLAGS=(project key dry-run force help)

# Parse: operator_args_parse normalizes -h to --help before delegation.
# Value-taking: project, key.
# Boolean: dry-run, force, help.
operator_args_parse "$@"

# Handle --help / -h.
if argparse_has "help"; then
    operator_args_render_help_for_flags "unwind-rc.sh" \
        "Fully unwind an in-flight release candidate across all state stores." \
        OPERATOR_VALID_FLAGS \
        "" \
        "  (--key: RC version to unwind, format vX.Y.Z; must match Active RC in release-state.md)" \
        "  (--force: skips Active-RC-version mismatch check; does not bypass existence or tag checks)"
    exit 0
fi

# Reject unexpected positional arguments (hard-cut: no positional fallback).
if [[ "${#ARGPARSE_POSITIONAL[@]}" -gt 0 ]]; then
    echo "ERROR: unexpected positional argument: ${ARGPARSE_POSITIONAL[0]}" >&2
    echo "Usage: unwind-rc.sh --project <name> --key <vX.Y.Z> [--dry-run] [--force]" >&2
    exit 1
fi

# Reject any flag not in the declared vocabulary.
operator_args_validate_known "$(basename "$0")" OPERATOR_VALID_FLAGS || exit 1

# Require --project and --key; emit error for missing-value flags.
if argparse_missing "project"; then
    echo "ERROR: --project requires a value" >&2
    exit 1
fi
if argparse_missing "key"; then
    echo "ERROR: --key requires a value" >&2
    exit 1
fi

# Extract values.
PROJECT_ARG="$(operator_args_project)"
VERSION_ARG="$(operator_args_get key)"
DRY_RUN=0; argparse_has "dry-run" && DRY_RUN=1
FORCE=0;   argparse_has "force"   && FORCE=1

if [[ -z "$PROJECT_ARG" || -z "$VERSION_ARG" ]]; then
    echo "ERROR: --project and --key are required." >&2
    echo "Usage: unwind-rc.sh --project <name> --key <vX.Y.Z> [--dry-run] [--force]" >&2
    exit 1
fi

# ---------------------------------------------------------------------------
# Resolve library paths (BEFORE strict mode)
# ---------------------------------------------------------------------------

# ---------------------------------------------------------------------------
# Bootstrap: self-locate → source shell-env → fail loud
# ---------------------------------------------------------------------------
# Must happen before the first use of PGAI_AGENT_KANBAN_ROOT_PATH so the
# script runs from a fresh shell without manual pre-sourcing.  Explicit
# operator exports win via env_bootstrap.sh's idempotency guard.
# shellcheck source=lib/env_bootstrap.sh
source "$(dirname "${BASH_SOURCE[0]}")/lib/env_bootstrap.sh" || exit 1

# Resolve KANBAN_ROOT
# PGAI_AGENT_KANBAN_ROOT_PATH is now set by env_bootstrap.sh or the operator.
KANBAN_ROOT="${PGAI_AGENT_KANBAN_ROOT_PATH}"

# Source env/config before strict mode — these files may use unset vars or
# return non-zero from innocent operations.
[[ -f "$KANBAN_ROOT/bashrc" ]]               && source "$KANBAN_ROOT/bashrc"
[[ -f "$KANBAN_ROOT/env" ]]                  && source "$KANBAN_ROOT/env"
[[ -f "$HOME/.config/pgai-kanban.cfg" ]]     && source "$HOME/.config/pgai-kanban.cfg"

# Source INI parser (needed before project_paths.sh)
# shellcheck source=lib/ini_parser.sh
[[ -f "${SCRIPT_DIR}/lib/ini_parser.sh" ]] && source "${SCRIPT_DIR}/lib/ini_parser.sh"

# Source kanban.cfg for PGAI_DEV_TREE_PATH when not already set
TEAM_ROOT="$KANBAN_ROOT"
if [[ -f "$TEAM_ROOT/kanban.cfg" ]]; then
    export PGAI_DEV_TREE_PATH="${PGAI_DEV_TREE_PATH:-$(read_ini "$TEAM_ROOT/kanban.cfg" paths dev_tree_path "")}"
fi

# Source project path helpers
# shellcheck source=lib/project_paths.sh
source "${SCRIPT_DIR}/lib/project_paths.sh"
# Source temp.sh for resolver helpers (include guard in temp.sh prevents double-load)
# shellcheck source=lib/temp.sh
source "${SCRIPT_DIR}/lib/temp.sh"

# ---------------------------------------------------------------------------
# Resolve the temp root ONCE here so both the plan echo and the backup path
# reference the same value.  No hardcoded /tmp literal in operator output.
# ---------------------------------------------------------------------------
_TMP_ROOT="$(pgai_temp_dir)"

# ---------------------------------------------------------------------------
# Enable strict mode for our own code
# ---------------------------------------------------------------------------
set -euo pipefail

# ---------------------------------------------------------------------------
# Resolve project name and repo root
# ---------------------------------------------------------------------------
PROJECT_NAME="$(pp_require_project_context "$PROJECT_ARG")" || {
    echo "" >&2
    echo "ERROR: could not resolve project context for '$PROJECT_ARG'." >&2
    exit 1
}

# Derive REPO_ROOT from project config unless already overridden
if [[ -z "${REPO_ROOT:-}" ]]; then
    pp_load_config "$PROJECT_NAME" || {
        echo "ERROR: could not load project config for '$PROJECT_NAME'" >&2
        echo "  Expected: $(pp_project_root "$PROJECT_NAME" 2>/dev/null || echo "<unresolvable>")/project.cfg" >&2
        exit 1
    }
    REPO_ROOT="${PP_dev_tree_path:-${PGAI_DEV_TREE_PATH:-}}"
    if [[ -z "$REPO_ROOT" ]]; then
        echo "ERROR: dev_tree_path is not set in project config for '$PROJECT_NAME'" >&2
        echo "  Set REPO_ROOT in the environment or add dev_tree_path to the project config." >&2
        exit 1
    fi
fi

VERSION="$VERSION_ARG"
RC_BRANCH="$(pp_prefix_branch "$PROJECT_NAME" "rc/$VERSION")"
MAIN_BRANCH="$(pp_prefix_branch "$PROJECT_NAME" "main")"
RELEASE_TAG="$(pp_prefix_tag "$PROJECT_NAME" "$VERSION")"

# ---------------------------------------------------------------------------
# Helper: read a named field from a markdown file
# Usage: _read_md_field <file> <heading>
# Finds "## <heading>" and returns the next non-blank non-comment line.
# ---------------------------------------------------------------------------
_read_md_field() {
    local file="$1"
    local heading="$2"
    python3 - "$file" "$heading" <<'PY'
import pathlib, sys
text = pathlib.Path(sys.argv[1]).read_text()
heading = sys.argv[2]
lines = text.splitlines()
for i, line in enumerate(lines):
    if line.strip() == f"## {heading}":
        for follow in lines[i+1:]:
            v = follow.strip()
            if v and not v.startswith("#"):
                print(v)
                raise SystemExit(0)
        break
print("none")
PY
}

# ---------------------------------------------------------------------------
# Resolve key paths
# ---------------------------------------------------------------------------
PROJECT_ROOT="$(pp_project_root "$PROJECT_NAME")"
RELEASE_STATE="$(pp_release_state "$PROJECT_NAME")"
TASKS_DIR="$(pp_tasks_dir "$PROJECT_NAME")"
REQUIREMENTS_DIR="$(pp_requirements_dir "$PROJECT_NAME")"
PRIORITY_DIR="$(pp_priority_dir "$PROJECT_NAME")"
BUGS_DIR="$(pp_bugs_dir "$PROJECT_NAME")"

# ---------------------------------------------------------------------------
# PRE-FLIGHT CHECK 1: Version format
# Must be done early so error messages reference the raw argument.
# ---------------------------------------------------------------------------
VERSION_REGEX='^v[0-9]+\.[0-9]+\.[0-9]+$'
if [[ ! "$VERSION" =~ $VERSION_REGEX ]]; then
    echo "ERROR: invalid version format: '$VERSION'" >&2
    echo "  Expected format: vX.Y.Z  (e.g. v0.15.4)" >&2
    exit 1
fi

# ---------------------------------------------------------------------------
# PRE-FLIGHT CHECK 2: HALT file in place
# Require either a global HALT ($KANBAN_ROOT/HALT) or a per-project HALT
# ($PROJECT_ROOT/HALT).  unwind-rc.sh makes destructive changes; we require
# the operator to have explicitly halted the system first.
#
# --force does NOT bypass this check.
# ---------------------------------------------------------------------------
GLOBAL_HALT_FILE="$KANBAN_ROOT/HALT"
PROJECT_HALT_FILE="$PROJECT_ROOT/HALT"

if [[ ! -f "$GLOBAL_HALT_FILE" ]] && [[ ! -f "$PROJECT_HALT_FILE" ]]; then
    echo "ERROR: no HALT file found." >&2
    echo "" >&2
    echo "  unwind-rc.sh requires the system to be halted before it will run." >&2
    echo "  This prevents the wake scripts from scheduling new work while the" >&2
    echo "  unwind is in progress." >&2
    echo "" >&2
    echo "  To halt the entire kanban:" >&2
    echo "    touch $GLOBAL_HALT_FILE" >&2
    echo "" >&2
    echo "  To halt only this project:" >&2
    echo "    touch $PROJECT_HALT_FILE" >&2
    echo "" >&2
    echo "  After the unwind completes, remove the HALT file to resume." >&2
    exit 1
fi

HALT_SCOPE="global"
[[ ! -f "$GLOBAL_HALT_FILE" ]] && HALT_SCOPE="per-project"

# ---------------------------------------------------------------------------
# PRE-FLIGHT CHECK 3: Project directory exists
# --force does NOT bypass this check.
# ---------------------------------------------------------------------------
if [[ ! -d "$PROJECT_ROOT" ]]; then
    echo "ERROR: project directory not found: $PROJECT_ROOT" >&2
    echo "  Project '$PROJECT_NAME' does not exist under projects/." >&2
    exit 1
fi

# ---------------------------------------------------------------------------
# PRE-FLIGHT CHECK 4: Version is not a shipped git tag
# Unwinding a shipped tag is not supported; the tag lives on origin/main and
# cannot be unwound by this script.  --force does NOT bypass this check.
# ---------------------------------------------------------------------------
if git -C "$REPO_ROOT" rev-parse --verify "refs/tags/$RELEASE_TAG" >/dev/null 2>&1; then
    echo "ERROR: $VERSION is a shipped git tag." >&2
    echo "" >&2
    echo "  unwind-rc.sh does not support unwinding a version that has already" >&2
    echo "  been tagged and released.  The tag exists at:" >&2
    echo "    $(git -C "$REPO_ROOT" rev-list -n1 "$RELEASE_TAG" 2>/dev/null || echo '<unknown>')" >&2
    echo "" >&2
    echo "  Shipped releases cannot be unwound.  If you need to re-roll a" >&2
    echo "  broken release, create a new patch version instead." >&2
    exit 1
fi

# ---------------------------------------------------------------------------
# PRE-FLIGHT CHECK 5: release-state.md exists and Active RC matches version
# --force bypasses only the version-mismatch check, not the missing-file check.
# ---------------------------------------------------------------------------
if [[ ! -f "$RELEASE_STATE" ]]; then
    echo "ERROR: release-state.md not found: $RELEASE_STATE" >&2
    echo "  Cannot determine Active RC for project '$PROJECT_NAME'." >&2
    exit 1
fi

ACTIVE_RC="$(_read_md_field "$RELEASE_STATE" "Active RC")"

if [[ "$ACTIVE_RC" == "none" ]]; then
    echo "ERROR: Active RC is 'none' in $RELEASE_STATE" >&2
    echo "" >&2
    echo "  There is no active RC to unwind for project '$PROJECT_NAME'." >&2
    echo "  If the RC was already unwound, verify with verify-rc-state.sh." >&2
    exit 1
fi

if [[ "$ACTIVE_RC" != "$VERSION" ]]; then
    if [[ "$FORCE" -eq 1 ]]; then
        echo "WARNING: Active RC mismatch (--force bypassing check)." >&2
        echo "  Active RC: $ACTIVE_RC" >&2
        echo "  Requested: $VERSION" >&2
        echo "" >&2
    else
        echo "ERROR: Active RC mismatch." >&2
        echo "" >&2
        echo "  release-state.md shows Active RC = '$ACTIVE_RC'" >&2
        echo "  You requested unwind of '$VERSION'" >&2
        echo "" >&2
        echo "  Only the active RC can be unwound without --force." >&2
        echo "  Use --force to override this check (e.g. partial-unwind recovery)." >&2
        exit 1
    fi
fi

# ---------------------------------------------------------------------------
# INVENTORY: Enumerate what WILL be touched
# This block collects all items across all 11 step categories.
# ---------------------------------------------------------------------------

# --- Count task folders matching this RC ---
TASK_FOLDERS_ACTIVE=()
TASK_FOLDERS_ALL=()
if [[ -d "$TASKS_DIR" ]]; then
    for d in "$TASKS_DIR"/*/; do
        [[ -d "$d" ]] || continue
        status_file="$d/status.md"
        readme_file="$d/README.md"
        [[ -f "$readme_file" ]] || continue

        # Include if the task README references this version
        if grep -q "$VERSION" "$readme_file" 2>/dev/null; then
            TASK_FOLDERS_ALL+=("$(basename "$d")")
            if [[ -f "$status_file" ]]; then
                task_state="$(_read_md_field "$status_file" "State")"
                if [[ "$task_state" != "DONE" && "$task_state" != "WONT-DO" ]]; then
                    TASK_FOLDERS_ACTIVE+=("$(basename "$d")")
                fi
            fi
        fi
    done
fi

# --- Count non-[x] queue entries referencing this RC ---
QUEUE_ENTRIES_TO_CLOSE=()
QUEUE_FILES=(
    "$TASKS_DIR/queues/coder_backlog.md"
    "$TASKS_DIR/queues/cm_backlog.md"
    "$TASKS_DIR/queues/pm_backlog.md"
    "$TASKS_DIR/queues/tester_backlog.md"
    "$TASKS_DIR/queues/writer_backlog.md"
    "$TASKS_DIR/queues/bug_backlog.md"
    "$TASKS_DIR/queues/priority_backlog.md"
)
for qfile in "${QUEUE_FILES[@]}"; do
    [[ -f "$qfile" ]] || continue
    # Find non-[x] lines referencing this version
    while IFS= read -r line; do
        if [[ "$line" =~ ^\[\ \]|\[W\]|\[A\]|\[B\] ]] && echo "$line" | grep -q "$VERSION"; then
            QUEUE_ENTRIES_TO_CLOSE+=("$(basename "$qfile"): $line")
        fi
    done < "$qfile"
done

# --- Find requirements file for this version ---
REQUIREMENTS_FILE=""
if [[ -d "$REQUIREMENTS_DIR" ]]; then
    for f in "$REQUIREMENTS_DIR"/${VERSION}-*.md; do
        # Skip already-superseded files
        [[ "$f" == *SUPERSEDED* ]] && continue
        if [[ -f "$f" ]]; then
            REQUIREMENTS_FILE="$f"
            break
        fi
    done
fi

# --- Find PM plan markers referencing this RC ---
PLAN_MARKERS=()
PLANS_DIR="$TASKS_DIR/queues/plans"
if [[ -d "$PLANS_DIR" ]]; then
    for mf in "$PLANS_DIR"/.materialized.*; do
        [[ -f "$mf" ]] || continue
        if grep -q "$VERSION" "$mf" 2>/dev/null; then
            PLAN_MARKERS+=("$mf")
        fi
    done
fi

# --- Check RC branches ---
RC_LOCAL_EXISTS=0
RC_REMOTE_EXISTS=0
if git -C "$REPO_ROOT" rev-parse --verify "refs/heads/$RC_BRANCH" >/dev/null 2>&1; then
    RC_LOCAL_EXISTS=1
fi
if git -C "$REPO_ROOT" ls-remote --exit-code --heads origin "$RC_BRANCH" >/dev/null 2>&1; then
    RC_REMOTE_EXISTS=1
fi

# --- Find PRIORITY backlog entries to flip back ---
PRIORITY_ENTRIES_TO_FLIP=()
PRIORITY_BACKLOG="$(pp_queue_path "$PROJECT_NAME" "priority")"
if [[ -f "$PRIORITY_BACKLOG" && -n "$REQUIREMENTS_FILE" ]]; then
    # Read the bundled items from the requirements file
    while IFS= read -r pname; do
        [[ -z "$pname" ]] && continue
        # Check if this priority is marked [x] in the backlog
        if grep -q "^\[x\].*$pname" "$PRIORITY_BACKLOG" 2>/dev/null; then
            PRIORITY_ENTRIES_TO_FLIP+=("$pname")
        fi
    done < <(grep -oE 'PRIORITY-[0-9]+[^`)"[:space:]]+' "$REQUIREMENTS_FILE" 2>/dev/null | sort -u)
fi

# --- Find BUG backlog entries to flip back ---
BUG_ENTRIES_TO_FLIP=()
BUG_BACKLOG="$(pp_queue_path "$PROJECT_NAME" "bug")"
if [[ -f "$BUG_BACKLOG" && -n "$REQUIREMENTS_FILE" ]]; then
    while IFS= read -r bname; do
        [[ -z "$bname" ]] && continue
        if grep -q "^\[x\].*$bname" "$BUG_BACKLOG" 2>/dev/null; then
            BUG_ENTRIES_TO_FLIP+=("$bname")
        fi
    done < <(grep -oE 'BUG-[0-9]+[^`)"[:space:]]+' "$REQUIREMENTS_FILE" 2>/dev/null | sort -u)
fi

# --- Check discovery state cache ---
DISCOVERY_STATE_DIR="$PROJECT_ROOT/.discovery-state"
DISCOVERY_FILES_TO_REMOVE=()
if [[ -d "$DISCOVERY_STATE_DIR" ]]; then
    while IFS= read -r df; do
        DISCOVERY_FILES_TO_REMOVE+=("$df")
    done < <(find "$DISCOVERY_STATE_DIR" -name "*${VERSION}*" 2>/dev/null || true)
fi

# --- Check per-RC release-state JSON ---
RC_STATE_JSON="${PROJECT_ROOT}/release-state/${VERSION}.json"
RC_STATE_JSON_EXISTS=0
RC_STATE_JSON_OUTCOME=""
if [[ -f "$RC_STATE_JSON" ]]; then
    RC_STATE_JSON_EXISTS=1
    RC_STATE_JSON_OUTCOME="$(python3 -c "
import json, sys
try:
    d = json.loads(open(sys.argv[1]).read())
    print(d.get('outcome','(unknown)'))
except Exception:
    print('(unreadable)')
" "$RC_STATE_JSON" 2>/dev/null || echo "(unreadable)")"
fi

# ---------------------------------------------------------------------------
# PRINT PLAN
# Always print the plan before prompting (or before exiting on --dry-run).
# ---------------------------------------------------------------------------
TS="$(date -Iseconds)"

echo ""
echo "============================================================"
echo "  unwind-rc.sh — Unwind Plan"
echo "============================================================"
echo ""
echo "  Project:    $PROJECT_NAME"
echo "  Version:    $VERSION"
echo "  HALT scope: $HALT_SCOPE"
[[ "$DRY_RUN" -eq 1 ]] && echo "  Mode:       DRY RUN (no changes will be made)"
[[ "$FORCE"   -eq 1 ]] && echo "  Flags:      --force (Active-RC mismatch check bypassed)"
echo ""
echo "  Active RC in release-state.md: $ACTIVE_RC"
echo ""
echo "------------------------------------------------------------"
echo "  What will be touched:"
echo "------------------------------------------------------------"
echo ""

# a. Backup
echo "  a. Backup"
echo "     Destination: ${_TMP_ROOT}/unwind-rc-${VERSION}-backup-<ts>/"
echo "     Scope: tasks/queues/, requirements/, priority/, release-state.md,"
echo "            release-state/ (per-RC JSON directory), matching task folders"
echo ""

# b. (this plan is the b. step)

# c. Git unwind
echo "  c. Git unwind"
if [[ "$RC_LOCAL_EXISTS" -eq 1 ]]; then
    echo "     - Delete local branch:  $RC_BRANCH  [EXISTS]"
else
    echo "     - Delete local branch:  $RC_BRANCH  [not found — skip]"
fi
if [[ "$RC_REMOTE_EXISTS" -eq 1 ]]; then
    echo "     - Delete remote branch: origin/$RC_BRANCH  [EXISTS]"
else
    echo "     - Delete remote branch: origin/$RC_BRANCH  [not found — skip]"
fi
echo ""

# d. Task folder state
echo "  d. Task folders -> WONT-DO"
if [[ "${#TASK_FOLDERS_ACTIVE[@]}" -gt 0 ]]; then
    echo "     ${#TASK_FOLDERS_ACTIVE[@]} active task(s) will be marked WONT-DO:"
    for t in "${TASK_FOLDERS_ACTIVE[@]}"; do
        echo "     - $t"
    done
else
    echo "     No active task folders referencing $VERSION found."
fi
if [[ "${#TASK_FOLDERS_ALL[@]}" -gt "${#TASK_FOLDERS_ACTIVE[@]}" ]]; then
    already_done=$(( ${#TASK_FOLDERS_ALL[@]} - ${#TASK_FOLDERS_ACTIVE[@]} ))
    echo "     ($already_done task(s) already in DONE/WONT-DO — no change)"
fi
echo ""

# e. Queue caches
echo "  e. Queue entries -> [x]"
if [[ "${#QUEUE_ENTRIES_TO_CLOSE[@]}" -gt 0 ]]; then
    echo "     ${#QUEUE_ENTRIES_TO_CLOSE[@]} open queue entry/entries will be closed:"
    for qe in "${QUEUE_ENTRIES_TO_CLOSE[@]}"; do
        echo "     - $qe"
    done
else
    echo "     No open queue entries referencing $VERSION found."
fi
echo ""

# f. Requirements file
echo "  f. Requirements file -> renamed to *.SUPERSEDED-on-cancel-<ts>.md"
if [[ -n "$REQUIREMENTS_FILE" ]]; then
    echo "     Source: $(basename "$REQUIREMENTS_FILE")"
    echo "     Target: $(basename "$REQUIREMENTS_FILE" .md).SUPERSEDED-on-cancel-${TS}.md"
else
    echo "     No requirements file matching $VERSION found."
fi
echo ""

# g. PM plan markers
echo "  g. PM plan markers -> removed"
if [[ "${#PLAN_MARKERS[@]}" -gt 0 ]]; then
    echo "     ${#PLAN_MARKERS[@]} marker(s) will be removed:"
    for pm in "${PLAN_MARKERS[@]}"; do
        echo "     - $(basename "$pm")"
    done
else
    echo "     No .materialized.* markers referencing $VERSION found."
fi
echo ""

# h. Priority backlog
echo "  h. Priority backlog -> flip [x] back to [ ]"
if [[ "${#PRIORITY_ENTRIES_TO_FLIP[@]}" -gt 0 ]]; then
    echo "     ${#PRIORITY_ENTRIES_TO_FLIP[@]} PRIORITY entry/entries will be re-opened:"
    for pe in "${PRIORITY_ENTRIES_TO_FLIP[@]}"; do
        echo "     - $pe"
    done
else
    echo "     No PRIORITY backlog entries to re-open."
fi
echo ""

# i. Bug backlog
echo "  i. Bug backlog -> flip [x] back to [ ]"
if [[ "${#BUG_ENTRIES_TO_FLIP[@]}" -gt 0 ]]; then
    echo "     ${#BUG_ENTRIES_TO_FLIP[@]} BUG entry/entries will be re-opened:"
    for be in "${BUG_ENTRIES_TO_FLIP[@]}"; do
        echo "     - $be"
    done
else
    echo "     No BUG backlog entries to re-open."
fi
echo ""

# j. release-state.md
echo "  j. release-state.md -> reset Active RC to none"
echo "     File: $RELEASE_STATE"
echo ""

# k. Discovery state cache
echo "  k. Discovery state cache -> remove version-related files"
if [[ "${#DISCOVERY_FILES_TO_REMOVE[@]}" -gt 0 ]]; then
    echo "     ${#DISCOVERY_FILES_TO_REMOVE[@]} file(s) will be removed:"
    for df in "${DISCOVERY_FILES_TO_REMOVE[@]}"; do
        echo "     - $(basename "$df")"
    done
else
    echo "     No discovery state files for $VERSION found."
fi
echo ""

# l. Per-RC release-state JSON
echo "  l. Per-RC release-state JSON -> flip outcome to cancelled"
echo "     File: $RC_STATE_JSON"
if [[ "$RC_STATE_JSON_EXISTS" -eq 1 ]]; then
    if [[ "$RC_STATE_JSON_OUTCOME" == "cancelled" ]]; then
        echo "     (already cancelled — will be a no-op)"
    else
        echo "     Current outcome: $RC_STATE_JSON_OUTCOME  ->  cancelled"
    fi
else
    echo "     (file not found — will skip; absent JSON is not an error)"
fi
echo ""

echo "============================================================"
echo ""

# ---------------------------------------------------------------------------
# DRY RUN: exit cleanly after printing plan
# ---------------------------------------------------------------------------
if [[ "$DRY_RUN" -eq 1 ]]; then
    echo "Dry run complete.  No changes were made."
    exit 0
fi

# ---------------------------------------------------------------------------
# CONFIRMATION PROMPT (unless --force)
# --force bypasses both the Active-RC mismatch check (pre-flight) AND the
# confirmation prompt (so it can be used in non-interactive scripted calls).
# ---------------------------------------------------------------------------
if [[ "$FORCE" -eq 0 ]]; then
    echo "This will permanently modify state across all stores listed above."
    echo "There is NO undo except the backup created in step (a)."
    echo ""
    read -r -p "Proceed with unwind of $RC_BRANCH? Type 'yes' to continue: " _confirm
    if [[ "$_confirm" != "yes" ]]; then
        echo ""
        echo "Unwind aborted. No changes were made."
        exit 1
    fi
    echo ""
fi

# ---------------------------------------------------------------------------
# ACTION STEPS (a-l)
# All steps are implemented below.
# ---------------------------------------------------------------------------

# ---------------------------------------------------------------------------
# Step a: Backup
# Copy key state stores to <temp_root>/unwind-rc-<version>-backup-<ts>/ BEFORE
# any destructive action.  Backup MUST succeed before we proceed.
# Uses _TMP_ROOT resolved at the top of the script for a consistent operator
# path (same value printed in the plan echo above).
# ---------------------------------------------------------------------------
echo "--- Step a: Backup ---"
BACKUP_TS="$(date +%Y%m%dT%H%M%S)"
# Operator-visible path: <temp_root>/unwind-rc-<version>-backup-<ts>/
BACKUP_DIR="${_TMP_ROOT}/unwind-rc-${VERSION}-backup-${BACKUP_TS}"
unset _TMP_ROOT

echo "  Creating backup directory: $BACKUP_DIR"
if ! mkdir -p "$BACKUP_DIR"; then
    echo "ERROR: could not create backup directory: $BACKUP_DIR" >&2
    exit 2
fi

# Helper: copy a source path into the backup, preserving relative structure.
# Usage: _backup_path <source_path> <relative_dest_subpath>
_backup_path() {
    local src="$1"
    local rel="$2"
    local dest="$BACKUP_DIR/$rel"
    if [[ -e "$src" ]]; then
        mkdir -p "$(dirname "$dest")"
        if ! cp -a "$src" "$dest"; then
            echo "ERROR: backup copy failed: $src -> $dest" >&2
            exit 2
        fi
        echo "  Backed up: $rel"
    else
        echo "  Skipped (not found): $rel"
    fi
}

# Derive paths relative to PROJECT_ROOT for backup naming.
QUEUES_SRC="$TASKS_DIR/queues"
REQUIREMENTS_SRC="$REQUIREMENTS_DIR"
PRIORITY_SRC="$PRIORITY_DIR"
RELEASE_STATE_SRC="$RELEASE_STATE"

# tasks/queues/
_backup_path "$QUEUES_SRC" "tasks/queues"

# requirements/
_backup_path "$REQUIREMENTS_SRC" "requirements"

# priority/
_backup_path "$PRIORITY_SRC" "priority"

# release-state.md (at project root level)
_backup_path "$RELEASE_STATE_SRC" "release-state.md"

# release-state/ (per-RC JSON directory)
RC_STATE_DIR_SRC="$PROJECT_ROOT/release-state"
_backup_path "$RC_STATE_DIR_SRC" "release-state"

# Matching task folders
TASK_BACKUP_COUNT=0
for tname in "${TASK_FOLDERS_ALL[@]}"; do
    tsrc="$TASKS_DIR/$tname"
    _backup_path "$tsrc" "tasks/$tname"
    (( TASK_BACKUP_COUNT++ )) || true
done

echo "  Backup complete: $BACKUP_DIR"
echo "  (To restore: cp -a ${BACKUP_DIR}/. <project-root>/)"
echo ""

# ---------------------------------------------------------------------------
# Step c: Git unwind
# 1. Checkout main so we are not on the branch we are about to delete.
# 2. Delete local rc/<version> if it exists.
# 3. Delete remote origin/rc/<version> if it exists.
# 4. Find orphan feature/CLAUDE-* branches whose feature-branch field in
#    their task README matches a cancelled task; warn and optionally delete.
# ---------------------------------------------------------------------------
echo "--- Step c: Git unwind ---"

# Switch to main so we are not standing on the RC branch.
echo "  Checking out $MAIN_BRANCH ..."
if ! git -C "$REPO_ROOT" checkout "$MAIN_BRANCH" 2>&1; then
    echo "ERROR: could not checkout $MAIN_BRANCH in $REPO_ROOT" >&2
    exit 2
fi

# Delete local RC branch (-D forces deletion even if not fully merged,
# which is correct here — we are intentionally discarding the RC).
if git -C "$REPO_ROOT" rev-parse --verify "refs/heads/$RC_BRANCH" >/dev/null 2>&1; then
    echo "  Deleting local branch: $RC_BRANCH"
    if ! git -C "$REPO_ROOT" branch -D "$RC_BRANCH" 2>&1; then
        echo "ERROR: failed to delete local branch $RC_BRANCH" >&2
        exit 2
    fi
    echo "  Local branch $RC_BRANCH deleted."
else
    echo "  Local branch $RC_BRANCH not found — skipping."
fi

# Delete remote origin/rc/<version>.
if git -C "$REPO_ROOT" ls-remote --exit-code --heads origin "$RC_BRANCH" >/dev/null 2>&1; then
    echo "  Deleting remote branch: origin/$RC_BRANCH"
    if ! git -C "$REPO_ROOT" push origin --delete "$RC_BRANCH" 2>&1; then
        echo "ERROR: failed to delete remote branch origin/$RC_BRANCH" >&2
        exit 2
    fi
    echo "  Remote branch origin/$RC_BRANCH deleted."
else
    echo "  Remote branch origin/$RC_BRANCH not found — skipping."
fi

# Detect orphan feature/CLAUDE-* branches whose task README references this RC.
# We collect all local feature/CLAUDE-* branches, then check each task folder
# to see if the feature-branch field matches a local branch AND the task folder
# is in TASK_FOLDERS_ALL (i.e. it belongs to this RC).
echo ""
echo "  Checking for orphan feature/CLAUDE-* branches ..."
ORPHAN_BRANCHES=()

# Build a set of task IDs in TASK_FOLDERS_ALL for fast lookup.
declare -A CANCELLED_TASK_IDS
for tname in "${TASK_FOLDERS_ALL[@]}"; do
    CANCELLED_TASK_IDS["$tname"]=1
done

# Enumerate local branches matching feature/CLAUDE-*
while IFS= read -r branch_name; do
    branch_name="${branch_name#  }"  # strip leading whitespace
    branch_name="${branch_name# }"
    branch_name="${branch_name#\* }"  # strip leading "* " for current branch

    [[ "$branch_name" == feature/CLAUDE-* ]] || continue

    # Derive task ID from branch name: feature/<task-id> → <task-id>
    task_id="${branch_name#feature/}"

    # Only flag if this task ID belongs to the cancelled RC.
    if [[ -n "${CANCELLED_TASK_IDS[$task_id]:-}" ]]; then
        ORPHAN_BRANCHES+=("$branch_name")
    fi
done < <(git -C "$REPO_ROOT" branch 2>/dev/null)

if [[ "${#ORPHAN_BRANCHES[@]}" -gt 0 ]]; then
    echo "  WARNING: Found ${#ORPHAN_BRANCHES[@]} orphan feature branch(es) from cancelled tasks:"
    for ob in "${ORPHAN_BRANCHES[@]}"; do
        echo "    $ob"
        # Attempt to delete; use -D to force since RC is being cancelled.
        if git -C "$REPO_ROOT" branch -D "$ob" 2>/dev/null; then
            echo "    -> deleted."
        else
            echo "    -> WARNING: could not delete $ob — may already be gone or checked out."
        fi
    done
else
    echo "  No orphan feature/CLAUDE-* branches found."
fi
echo ""

# ---------------------------------------------------------------------------
# Step d: Task folder state -> WONT-DO + Cancellation Note
#
# For every task folder whose README references the cancelled RC version,
# (a) flip its status.md State to WONT-DO and (b) append a
# `## Cancellation Note (TS)` block recording the reason.
#
# Idempotency: skip task folders that already carry a `## Cancellation Note`
# block — re-running must not append duplicate blocks (constraint from the
# task README + the priority spec acceptance criteria).
# ---------------------------------------------------------------------------
echo "--- Step d: Task folder state ---"

D_FLIPPED=0
D_ALREADY=0
D_NO_STATUS=0

if [[ "${#TASK_FOLDERS_ALL[@]}" -gt 0 ]]; then
    for tname in "${TASK_FOLDERS_ALL[@]}"; do
        status_file="$TASKS_DIR/$tname/status.md"
        if [[ ! -f "$status_file" ]]; then
            echo "  $tname — no status.md (skipped)"
            D_NO_STATUS=$((D_NO_STATUS + 1))
            continue
        fi

        # Idempotency: if a Cancellation Note already exists, leave the file alone.
        # We anchor to start-of-line so we don't trip on the literal phrase in
        # body text or quoted blocks.
        if grep -qE "^## Cancellation Note" "$status_file"; then
            echo "  $tname — already cancelled (skipping)"
            D_ALREADY=$((D_ALREADY + 1))
            continue
        fi

        # Mutate: replace the value under `## State` with WONT-DO and append the
        # Cancellation Note block.  Use Python for safe in-place rewrite — bash
        # sed for multiline section edits is fragile and version-dependent.
        python3 - "$status_file" "$VERSION" "$TS" "$tname" <<'PY'
import pathlib
import re
import sys

status_path = pathlib.Path(sys.argv[1])
version     = sys.argv[2]
ts          = sys.argv[3]
task_id     = sys.argv[4]

text = status_path.read_text()

# Replace the first non-blank line after `## State` with `WONT-DO`.
# Regex anatomy:
#   (## State\n(?:[ \t]*\n)*)   — heading + optional blank lines (group 1)
#   ([^\n]+)                    — current state value to replace
# count=1 ensures only the first occurrence is touched.
new_text, n = re.subn(
    r'(## State\n(?:[ \t]*\n)*)([^\n]+)',
    r'\1WONT-DO',
    text,
    count=1,
)

if n == 0:
    # No `## State` heading found — leave the file alone but warn.  Without
    # a state block we cannot guarantee a clean flip, so we do not append a
    # Cancellation Note either; the operator can inspect manually.
    sys.stderr.write(f"  WARNING: no `## State` heading found in {status_path}; left untouched\n")
    sys.exit(3)

# Ensure trailing newline before appending the Cancellation Note block.
if not new_text.endswith("\n"):
    new_text += "\n"

note = (
    f"\n## Cancellation Note ({ts})\n"
    f"This task was cancelled as part of `unwind-rc.sh --key {version}` "
    f"unwinding the in-flight release candidate.  The state was flipped "
    f"to WONT-DO by the unwind script; the work itself was not completed.\n"
)
new_text += note

status_path.write_text(new_text)
PY
        rc=$?
        if [[ $rc -eq 0 ]]; then
            echo "  $tname -> WONT-DO (+ Cancellation Note)"
            D_FLIPPED=$((D_FLIPPED + 1))
        elif [[ $rc -eq 3 ]]; then
            # Malformed status.md — already warned to stderr inside python.
            :
        else
            echo "ERROR: failed to update $status_file (rc=$rc)" >&2
            exit 2
        fi
    done
fi

echo "  Step d summary: $D_FLIPPED cancelled, $D_ALREADY already-cancelled, $D_NO_STATUS missing-status."
echo ""

# ---------------------------------------------------------------------------
# Step e: Queue caches -> flip cancelled-task entries to [x]
#
# Build a set of task IDs from TASK_FOLDERS_ALL, then walk every backlog file
# and flip any `- [<marker>] <task-id>` line whose <task-id> is in the set to
# `[x]`.  Marker characters in the kanban: ` `, `W`, `A`, `B`, `x`.
#
# Idempotency: lines already at `[x]` are left untouched.
# Ordering: the queue is rewritten line-for-line so original ordering and
# surrounding content are preserved exactly.
# ---------------------------------------------------------------------------
echo "--- Step e: Queue caches ---"

E_FLIPPED_TOTAL=0
E_ALREADY_TOTAL=0

if [[ "${#TASK_FOLDERS_ALL[@]}" -gt 0 ]]; then
    # Pass the cancelled-task ID set to python via env var (newline-separated)
    # — keeps argv tractable when many tasks belong to one RC.
    PGAI_CANCEL_TASK_IDS="$(printf '%s\n' "${TASK_FOLDERS_ALL[@]}")"
    export PGAI_CANCEL_TASK_IDS

    for qfile in "${QUEUE_FILES[@]}"; do
        [[ -f "$qfile" ]] || continue

        # Python returns "flipped already" on stdout so bash can sum them.
        if ! result="$(python3 - "$qfile" <<'PY'
import os
import pathlib
import re
import sys

qpath = pathlib.Path(sys.argv[1])
task_set = {
    t for t in os.environ.get("PGAI_CANCEL_TASK_IDS", "").splitlines()
    if t.strip()
}

text = qpath.read_text()
out_chunks = []
flipped = 0
already = 0

# Match a checklist line:  - [<marker>] <task-id> [optional trailing text]
# Tolerate leading whitespace (some queues indent) and either tab or spaces
# between `-` and `[`.
pattern = re.compile(r'^(\s*-\s+)\[([^\]])\](\s+)(\S+)(.*)$')

for line in text.splitlines(keepends=True):
    stripped = line.rstrip("\n")
    m = pattern.match(stripped)
    if m:
        prefix, marker, mid, tid, suffix = m.groups()
        if tid in task_set:
            if marker == 'x':
                already += 1
            else:
                new_line = f"{prefix}[x]{mid}{tid}{suffix}"
                if line.endswith("\n"):
                    new_line += "\n"
                line = new_line
                flipped += 1
    out_chunks.append(line)

if flipped > 0:
    qpath.write_text("".join(out_chunks))

print(f"{flipped} {already}")
PY
)"; then
            echo "ERROR: queue rewrite failed for $qfile" >&2
            exit 2
        fi

        # Parse "flipped already" pair
        read -r qf qa <<< "$result"
        if [[ "$qf" -gt 0 || "$qa" -gt 0 ]]; then
            echo "  $(basename "$qfile"): flipped=$qf already=$qa"
        fi
        E_FLIPPED_TOTAL=$((E_FLIPPED_TOTAL + qf))
        E_ALREADY_TOTAL=$((E_ALREADY_TOTAL + qa))
    done

    unset PGAI_CANCEL_TASK_IDS
fi

echo "  Step e summary: $E_FLIPPED_TOTAL flipped, $E_ALREADY_TOTAL already-[x]."
echo ""

# ---------------------------------------------------------------------------
# Step f: Requirements file -> rename with .SUPERSEDED-on-cancel-<ts> suffix
#
# REQUIREMENTS_FILE was populated in the inventory; it skips already-renamed
# `*SUPERSEDED*` files, so on a re-run the variable is empty and we no-op.
#
# Rename via `git mv` when the file is tracked in a git worktree (so dev-tree
# history records the rename); fall back to plain `mv` when not tracked (the
# live install is not a git worktree).
# ---------------------------------------------------------------------------
echo "--- Step f: Requirements file ---"

SUPERSEDED_REQUIREMENTS_FILE=""   # set below when the rename happens or file already renamed

if [[ -z "$REQUIREMENTS_FILE" ]]; then
    echo "  No requirements file to rename (none found, or already superseded)."
    # On a re-run, the file is already superseded; scan for it so steps h/i
    # can still read the bundled item list.
    if [[ -d "$REQUIREMENTS_DIR" ]]; then
        for _sr in "$REQUIREMENTS_DIR"/${VERSION}-*.SUPERSEDED-on-cancel-*.md; do
            if [[ -f "$_sr" ]]; then
                SUPERSEDED_REQUIREMENTS_FILE="$_sr"
                echo "  Found already-superseded file: $(basename "$_sr")"
                break
            fi
        done
    fi
else
    f_dir="$(dirname "$REQUIREMENTS_FILE")"
    f_base="$(basename "$REQUIREMENTS_FILE" .md)"
    f_new_name="${f_base}.SUPERSEDED-on-cancel-${TS}.md"
    f_new_path="${f_dir}/${f_new_name}"

    if [[ -e "$f_new_path" ]]; then
        echo "  Target already exists: $f_new_name — skipping rename."
        SUPERSEDED_REQUIREMENTS_FILE="$f_new_path"
    else
        # Detect git-tracked status by asking the worktree that contains the file.
        tracked=0
        if git -C "$f_dir" rev-parse --show-toplevel >/dev/null 2>&1; then
            if git -C "$f_dir" ls-files --error-unmatch -- "$REQUIREMENTS_FILE" >/dev/null 2>&1; then
                tracked=1
            fi
        fi

        if [[ "$tracked" -eq 1 ]]; then
            echo "  git mv: $(basename "$REQUIREMENTS_FILE") -> $f_new_name"
            if ! git -C "$f_dir" mv -- "$REQUIREMENTS_FILE" "$f_new_path"; then
                echo "ERROR: git mv failed for $REQUIREMENTS_FILE" >&2
                exit 2
            fi
        else
            echo "  mv (untracked): $(basename "$REQUIREMENTS_FILE") -> $f_new_name"
            if ! mv -- "$REQUIREMENTS_FILE" "$f_new_path"; then
                echo "ERROR: mv failed for $REQUIREMENTS_FILE" >&2
                exit 2
            fi
        fi
        SUPERSEDED_REQUIREMENTS_FILE="$f_new_path"
    fi
fi
echo ""

# ---------------------------------------------------------------------------
# Step g: PM plan markers -> remove .materialized.* files referencing this RC
#
# PLAN_MARKERS was populated in the inventory by content-matching every
# .materialized.* file under `tasks/queues/plans/` against $VERSION.  Delete
# each match.  Idempotent: a re-run finds no markers and no-ops.
# ---------------------------------------------------------------------------
echo "--- Step g: PM plan markers ---"

G_REMOVED=0
if [[ "${#PLAN_MARKERS[@]}" -gt 0 ]]; then
    for mf in "${PLAN_MARKERS[@]}"; do
        if [[ -f "$mf" ]]; then
            if ! rm -- "$mf"; then
                echo "ERROR: failed to remove $mf" >&2
                exit 2
            fi
            echo "  removed: $(basename "$mf")"
            G_REMOVED=$((G_REMOVED + 1))
        fi
    done
fi
echo "  Step g summary: $G_REMOVED marker(s) removed."
echo ""

# ---------------------------------------------------------------------------
# Step h: Priority backlog -> flip bundled PRIORITY entries back to [ ]
#         and reset each priority file's ## Status header back to `open`.
#
# Reads the ## Bundled Items section from SUPERSEDED_REQUIREMENTS_FILE to
# discover which PRIORITY-NNNN items were part of the cancelled RC.  For each:
#   1. Flip its `[x]` marker in priority_backlog.md back to `[ ]`.
#   2. Reset its `## Status` header to `open` so discovery re-bundles it.
#
# Idempotency:
#   - A `[ ]` marker is left untouched (already flipped back).
#   - A priority file whose `## Status` is already `open` is not rewritten.
#   - If SUPERSEDED_REQUIREMENTS_FILE is empty or has no bundled items, no-ops.
# ---------------------------------------------------------------------------
echo "--- Step h: Priority backlog ---"

H_FLIPPED=0
H_ALREADY=0
H_STATUS_RESET=0
H_STATUS_ALREADY=0

if [[ -z "$SUPERSEDED_REQUIREMENTS_FILE" ]]; then
    echo "  No superseded requirements file found — nothing to flip."
else
    # Extract PRIORITY-NNNN identifiers from the ## Bundled Items section.
    # The section lists lines like:
    #   - PRIORITY-NNNN-slug.md (`/path/to/file`)
    # We extract the bare filename stem (without .md) from each line.
    PRIORITY_BACKLOG_FILE="$(pp_queue_path "$PROJECT_NAME" "priority")"

    while IFS= read -r priority_id; do
        [[ -z "$priority_id" ]] && continue

        # --- 1. Flip backlog marker ---
        # Backlog format: `- [x] PRIORITY-NNNN-slug`
        # The check and the Python regex must match the leading `- ` prefix.
        if [[ -f "$PRIORITY_BACKLOG_FILE" ]]; then
            if grep -qE "^\s*-\s+\[x\]\s+${priority_id}" "$PRIORITY_BACKLOG_FILE" 2>/dev/null; then
                # Flip [x] back to [ ]
                python3 - "$PRIORITY_BACKLOG_FILE" "$priority_id" <<'PY'
import pathlib, re, sys
qpath = pathlib.Path(sys.argv[1])
pid   = sys.argv[2]
text  = qpath.read_text()
# Match `- [x] <pid>` (with optional leading whitespace) and flip to `- [ ] <pid>`.
new_text, n = re.subn(
    r'(?m)^(\s*-\s+)\[x\](\s+' + re.escape(pid) + r')',
    r'\1[ ]\2',
    text,
    count=1,
)
if n > 0:
    qpath.write_text(new_text)
    sys.exit(0)
sys.exit(1)
PY
                if [[ $? -eq 0 ]]; then
                    echo "  priority_backlog: $priority_id -> [ ]"
                    H_FLIPPED=$((H_FLIPPED + 1))
                else
                    echo "  priority_backlog: $priority_id — [x] line not found (already flipped?)"
                    H_ALREADY=$((H_ALREADY + 1))
                fi
            else
                echo "  priority_backlog: $priority_id — not [x] (already open or absent)"
                H_ALREADY=$((H_ALREADY + 1))
            fi
        fi

        # --- 2. Reset priority file ## Status to open ---
        # Resolve the full path from the superseded requirements file's bundled list.
        priority_file_path=""
        priority_file_path="$(grep -oP '(?<=\`)[^`]+'"${priority_id}"'[^`]*(?=\`)' "$SUPERSEDED_REQUIREMENTS_FILE" 2>/dev/null | head -1 || true)"

        # Fallback: search PRIORITY_DIR directly by filename prefix.
        if [[ -z "$priority_file_path" || ! -f "$priority_file_path" ]]; then
            for _pf in "$PRIORITY_DIR"/${priority_id}*.md; do
                if [[ -f "$_pf" ]]; then
                    priority_file_path="$_pf"
                    break
                fi
            done
        fi

        if [[ -n "$priority_file_path" && -f "$priority_file_path" ]]; then
            current_status="$(_read_md_field "$priority_file_path" "Status")"
            if [[ "$current_status" == "open" ]]; then
                echo "  $priority_id — ## Status already open"
                H_STATUS_ALREADY=$((H_STATUS_ALREADY + 1))
            else
                # Rewrite ## Status to open using python3 for safe in-place edit.
                python3 - "$priority_file_path" <<'PY'
import pathlib, re, sys
p = pathlib.Path(sys.argv[1])
text = p.read_text()
new_text, n = re.subn(
    r'(## Status\n(?:[ \t]*\n)*)([^\n]+)',
    r'\1open',
    text,
    count=1,
)
if n > 0:
    p.write_text(new_text)
    sys.exit(0)
sys.exit(1)
PY
                if [[ $? -eq 0 ]]; then
                    echo "  $priority_id — ## Status reset to open (was: $current_status)"
                    H_STATUS_RESET=$((H_STATUS_RESET + 1))
                else
                    echo "  WARNING: could not reset ## Status in $priority_file_path" >&2
                fi
            fi
        else
            echo "  WARNING: could not find priority file for $priority_id — ## Status not reset" >&2
        fi

    done < <(python3 - "$SUPERSEDED_REQUIREMENTS_FILE" <<'PY'
import pathlib, re, sys
text = pathlib.Path(sys.argv[1]).read_text()
# Find the ## Bundled Items section and extract PRIORITY-NNNN identifiers.
# Lines in the section look like:
#   - PRIORITY-NNNN-slug.md (`/path/…`)
# or just:
#   - PRIORITY-NNNN-slug.md
in_section = False
for line in text.splitlines():
    if line.strip() == "## Bundled Items":
        in_section = True
        continue
    if in_section:
        if line.startswith("## "):
            break
        m = re.search(r'\b(PRIORITY-\d+[^\s`)"]*)', line)
        if m:
            # Strip trailing .md suffix if present.
            ident = m.group(1)
            if ident.endswith(".md"):
                ident = ident[:-3]
            print(ident)
PY
)
fi

echo "  Step h summary: backlog-flipped=$H_FLIPPED already-open=$H_ALREADY status-reset=$H_STATUS_RESET status-already-open=$H_STATUS_ALREADY"
echo ""

# ---------------------------------------------------------------------------
# Step i: Bug backlog -> flip bundled BUG entries back to [ ]
#         and reset each bug file's ## Status header back to `open`.
#
# Same logic as step h but for BUG-NNNN entries and bug_backlog.md.
# ---------------------------------------------------------------------------
echo "--- Step i: Bug backlog ---"

I_FLIPPED=0
I_ALREADY=0
I_STATUS_RESET=0
I_STATUS_ALREADY=0

if [[ -z "$SUPERSEDED_REQUIREMENTS_FILE" ]]; then
    echo "  No superseded requirements file found — nothing to flip."
else
    BUG_BACKLOG_FILE="$(pp_queue_path "$PROJECT_NAME" "bug")"

    while IFS= read -r bug_id; do
        [[ -z "$bug_id" ]] && continue

        # --- 1. Flip backlog marker ---
        # Backlog format: `- [x] BUG-NNNN-slug`
        if [[ -f "$BUG_BACKLOG_FILE" ]]; then
            if grep -qE "^\s*-\s+\[x\]\s+${bug_id}" "$BUG_BACKLOG_FILE" 2>/dev/null; then
                python3 - "$BUG_BACKLOG_FILE" "$bug_id" <<'PY'
import pathlib, re, sys
qpath = pathlib.Path(sys.argv[1])
bid   = sys.argv[2]
text  = qpath.read_text()
# Match `- [x] <bid>` (with optional leading whitespace) and flip to `- [ ] <bid>`.
new_text, n = re.subn(
    r'(?m)^(\s*-\s+)\[x\](\s+' + re.escape(bid) + r')',
    r'\1[ ]\2',
    text,
    count=1,
)
if n > 0:
    qpath.write_text(new_text)
    sys.exit(0)
sys.exit(1)
PY
                if [[ $? -eq 0 ]]; then
                    echo "  bug_backlog: $bug_id -> [ ]"
                    I_FLIPPED=$((I_FLIPPED + 1))
                else
                    echo "  bug_backlog: $bug_id — [x] line not found (already flipped?)"
                    I_ALREADY=$((I_ALREADY + 1))
                fi
            else
                echo "  bug_backlog: $bug_id — not [x] (already open or absent)"
                I_ALREADY=$((I_ALREADY + 1))
            fi
        fi

        # --- 2. Reset bug file ## Status to open ---
        bug_file_path=""
        bug_file_path="$(grep -oP '(?<=\`)[^`]+'"${bug_id}"'[^`]*(?=\`)' "$SUPERSEDED_REQUIREMENTS_FILE" 2>/dev/null | head -1 || true)"

        if [[ -z "$bug_file_path" || ! -f "$bug_file_path" ]]; then
            for _bf in "$BUGS_DIR"/${bug_id}*.md; do
                if [[ -f "$_bf" ]]; then
                    bug_file_path="$_bf"
                    break
                fi
            done
        fi

        if [[ -n "$bug_file_path" && -f "$bug_file_path" ]]; then
            current_status="$(_read_md_field "$bug_file_path" "Status")"
            if [[ "$current_status" == "open" ]]; then
                echo "  $bug_id — ## Status already open"
                I_STATUS_ALREADY=$((I_STATUS_ALREADY + 1))
            else
                python3 - "$bug_file_path" <<'PY'
import pathlib, re, sys
p = pathlib.Path(sys.argv[1])
text = p.read_text()
new_text, n = re.subn(
    r'(## Status\n(?:[ \t]*\n)*)([^\n]+)',
    r'\1open',
    text,
    count=1,
)
if n > 0:
    p.write_text(new_text)
    sys.exit(0)
sys.exit(1)
PY
                if [[ $? -eq 0 ]]; then
                    echo "  $bug_id — ## Status reset to open (was: $current_status)"
                    I_STATUS_RESET=$((I_STATUS_RESET + 1))
                else
                    echo "  WARNING: could not reset ## Status in $bug_file_path" >&2
                fi
            fi
        else
            echo "  WARNING: could not find bug file for $bug_id — ## Status not reset" >&2
        fi

    done < <(python3 - "$SUPERSEDED_REQUIREMENTS_FILE" <<'PY'
import pathlib, re, sys
text = pathlib.Path(sys.argv[1]).read_text()
in_section = False
for line in text.splitlines():
    if line.strip() == "## Bundled Items":
        in_section = True
        continue
    if in_section:
        if line.startswith("## "):
            break
        m = re.search(r'\b(BUG-\d+[^\s`)"]*)', line)
        if m:
            ident = m.group(1)
            if ident.endswith(".md"):
                ident = ident[:-3]
            print(ident)
PY
)
fi

echo "  Step i summary: backlog-flipped=$I_FLIPPED already-open=$I_ALREADY status-reset=$I_STATUS_RESET status-already-open=$I_STATUS_ALREADY"
echo ""

# ---------------------------------------------------------------------------
# Step j: release-state.md -> reset Active RC, RC Opened At, RC Opened By Task
#         to `none`; preserve Last Released, Last Released At, Last Released By
#         Task verbatim.
#
# Uses Python for safe in-place rewrite.  The file is already backed up in
# step (a), so a partial write here still has a recoverable original.
#
# Idempotency: if all three target fields already read `none`, skip the write.
# ---------------------------------------------------------------------------
echo "--- Step j: release-state.md ---"

python3 - "$RELEASE_STATE" <<'PY'
import pathlib, re, sys

rs_path = pathlib.Path(sys.argv[1])
text = rs_path.read_text()

def set_md_field(text, heading, new_value):
    """Replace the first non-blank line after `## <heading>` with new_value."""
    return re.sub(
        r'(## ' + re.escape(heading) + r'\n(?:[ \t]*\n)*)([^\n]+)',
        r'\g<1>' + new_value,
        text,
        count=1,
    )

def get_md_field(text, heading):
    """Return the first non-blank line after `## <heading>`, or '' if absent."""
    m = re.search(
        r'## ' + re.escape(heading) + r'\n(?:[ \t]*\n)*([^\n]+)',
        text,
    )
    return m.group(1).strip() if m else ''

# Check if already reset.
arc   = get_md_field(text, 'Active RC')
roa   = get_md_field(text, 'RC Opened At')
robot = get_md_field(text, 'RC Opened By Task')

if arc == 'none' and roa == 'none' and robot == 'none':
    print('  release-state.md: already reset — nothing to do.')
    sys.exit(0)

new_text = text
new_text = set_md_field(new_text, 'Active RC',           'none')
new_text = set_md_field(new_text, 'RC Opened At',        'none')
new_text = set_md_field(new_text, 'RC Opened By Task',   'none')

rs_path.write_text(new_text)

# Verify Last Released fields were not touched.
lr  = get_md_field(new_text, 'Last Released')
lra = get_md_field(new_text, 'Last Released At')
lrb = get_md_field(new_text, 'Last Released By Task')

print(f'  Active RC        -> none  (was: {arc})')
print(f'  RC Opened At     -> none  (was: {roa})')
print(f'  RC Opened By Task-> none  (was: {robot})')
print(f'  Last Released      preserved: {lr}')
print(f'  Last Released At   preserved: {lra}')
print(f'  Last Released By Task preserved: {lrb}')
PY

if [[ $? -ne 0 ]]; then
    echo "ERROR: failed to reset release-state.md" >&2
    exit 2
fi
echo ""

# ---------------------------------------------------------------------------
# Step k: Discovery state cache -> remove version-related files
#
# Removes files under projects/<name>/.discovery-state/ whose names contain
# the cancelled version string.  The directory itself is preserved (it may
# contain state for other versions or unrelated data).
#
# Idempotency: `find` returns nothing on re-run; rm never called.
# ---------------------------------------------------------------------------
echo "--- Step k: Discovery state cache ---"

DISCOVERY_STATE_DIR="$PROJECT_ROOT/.discovery-state"
K_REMOVED=0

if [[ ! -d "$DISCOVERY_STATE_DIR" ]]; then
    echo "  No .discovery-state directory found — nothing to remove."
else
    while IFS= read -r df; do
        [[ -f "$df" ]] || continue
        if rm -- "$df"; then
            echo "  removed: $(basename "$df")"
            K_REMOVED=$((K_REMOVED + 1))
        else
            echo "  WARNING: could not remove $df" >&2
        fi
    done < <(find "$DISCOVERY_STATE_DIR" -maxdepth 2 -name "*${VERSION}*" 2>/dev/null || true)
fi

echo "  Step k summary: $K_REMOVED file(s) removed."
echo ""

# ---------------------------------------------------------------------------
# Step l: Per-RC release-state JSON -> flip outcome to cancelled
#
# The per-RC metrics record at projects/<name>/release-state/<version>.json
# is written by cm/open-rc.sh with outcome=in_progress.  After an unwind that
# record must reflect the cancellation so metrics_aggregator.py does not carry
# a phantom in-progress RC.
#
# Semantics:
#   - Best-effort: if the JSON is absent (RC unwound before open-rc wrote it),
#     skip silently — never fail the unwind because of a missing JSON.
#   - Idempotent: if the JSON is already cancelled, write_rc_state.py cancel
#     overwrites with the same values — no harmful side effect.
#   - Schema single-sourced: write goes through write_rc_state.py cancel, not
#     ad-hoc bash JSON.
#
# closed_at uses UTC ISO8601 to match the open-rc convention.
# ---------------------------------------------------------------------------
echo "--- Step l: Per-RC release-state JSON ---"

WRITE_RC_STATE_PY="$KANBAN_ROOT/pgai_agent_kanban/cm/write_rc_state.py"
# Recompute RC_STATE_JSON (may have changed if the inventory was stale)
RC_STATE_JSON="${PROJECT_ROOT}/release-state/${VERSION}.json"
UNWIND_TS_UTC="$(date -u +%Y-%m-%dT%H:%M:%SZ)"

if [[ ! -f "$RC_STATE_JSON" ]]; then
    echo "  Per-RC JSON not found: $RC_STATE_JSON"
    echo "  Skipping — absent JSON is not an error (RC may have been unwound before open-rc wrote it)."
elif [[ ! -f "$WRITE_RC_STATE_PY" ]]; then
    echo "  WARNING: write_rc_state.py not found at $WRITE_RC_STATE_PY — skipping JSON reconciliation." >&2
    echo "  The per-RC JSON at $RC_STATE_JSON was NOT updated." >&2
else
    if python3 "$WRITE_RC_STATE_PY" cancel "$RC_STATE_JSON" "$VERSION" "$UNWIND_TS_UTC"; then
        echo "  Per-RC JSON reconciled: outcome=cancelled, closed_at=$UNWIND_TS_UTC"
    else
        echo "  WARNING: write_rc_state.py cancel returned non-zero for $RC_STATE_JSON — JSON may be unchanged." >&2
    fi
fi

echo ""

# ---------------------------------------------------------------------------
# Summary
# ---------------------------------------------------------------------------
echo ""
echo "============================================================"
echo "  unwind-rc.sh complete — all steps (a-l) finished."
echo "  RC $VERSION has been fully unwound."
echo "  Backup is at: $BACKUP_DIR"
echo "  (To restore: cp -a ${BACKUP_DIR}/. $(dirname "$PROJECT_ROOT")/)"
echo "============================================================"
echo ""

exit 0
