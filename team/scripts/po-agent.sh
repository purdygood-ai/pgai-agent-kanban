#!/usr/bin/env bash
# po-agent.sh
# Validate a brief file and invoke the PO subagent directly (one-shot).
# The PO subagent reads the brief, validates it, expands it into a full
# requirements document, and creates a PM ticket for decomposition.
#
# This is a one-shot human-initiated tool. PO is invoked immediately —
# it does NOT queue work for later processing (unlike pm-agent.sh).
#
# Usage:
#   po-agent.sh <brief.md>                     # validate and invoke PO subagent (full mode)
#   po-agent.sh <brief.md> --dry-run           # preview what would happen, no writes
#   po-agent.sh <brief.md> --output <dir>      # draft mode: write doc to <dir>/, no PM ticket
#
# Draft mode (--output <dir>):
#   Runs the full PO expansion and writes the resulting requirements document
#   as <dir>/<target-version>-<slug>.md.  No PM ticket, no backlog entry,
#   nothing written under projects/.  If the target file already exists, a
#   numeric suffix is appended (-2, -3, ...) and the decision is announced on
#   stdout.  The final line of every draft-mode run is:
#     DRAFT: <path>
#   --output combined with --dry-run is refused loudly (contradiction: dry-run
#   means no writes; --output means write a draft file).
#
# Configuration:
#   PGAI_AGENT_KANBAN_ROOT_PATH  — kanban root (default: $HOME/pgai_agent_kanban)
#
# Optional sourced files (in kanban root):
#   bashrc  — personal shell config
#   env     — environment tunables

# --- Resolve kanban root ---
# Done before strict mode so the error message is clean if it fails.
TEAM_ROOT="${PGAI_AGENT_KANBAN_ROOT_PATH}"

if [[ ! -d "$TEAM_ROOT" ]]; then
  echo "ERROR: kanban root not found: $TEAM_ROOT" >&2
  echo "Set PGAI_AGENT_KANBAN_ROOT_PATH or run install.sh" >&2
  exit 1
fi

# --- Source optional config files ---
# This MUST happen before `set -euo pipefail`. User bashrc files commonly
# contain unset variable references, conditional aliases that return non-zero,
# or interactive-only checks that would trip strict mode and silently kill
# the script. We accept whatever the user sources, then enable strict mode
# for our own code.
[[ -f "$TEAM_ROOT/bashrc" ]] && source "$TEAM_ROOT/bashrc"
[[ -f "$TEAM_ROOT/env" ]] && source "$TEAM_ROOT/env"
# $HOME/.config/pgai-kanban.cfg is operator-local bash config; sourced as-is.
[[ -f "$HOME/.config/pgai-kanban.cfg" ]] && source "$HOME/.config/pgai-kanban.cfg"
# Source: kanban.cfg [chain/paths] — INI format replaces legacy config.cfg
# Source ini_parser.sh directly since project_paths.sh has not been sourced yet.
_PO_SCRIPT_DIR_TMP="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
[[ -f "${_PO_SCRIPT_DIR_TMP}/lib/ini_parser.sh" ]] && source "${_PO_SCRIPT_DIR_TMP}/lib/ini_parser.sh"
# shellcheck source=lib/dev_tree.sh
source "${_PO_SCRIPT_DIR_TMP}/lib/dev_tree.sh"
if [[ -f "$TEAM_ROOT/kanban.cfg" ]]; then
    export PGAI_KANBAN_PM_MODE="${PGAI_KANBAN_PM_MODE:-$(read_ini "$TEAM_ROOT/kanban.cfg" chain pm_mode automatic)}"
fi
unset _PO_SCRIPT_DIR_TMP
export PGAI_DEV_TREE_PATH="${PGAI_DEV_TREE_PATH:-$(resolve_global_dev_tree)}"
# Classification (a): po-agent validates and queues a brief in $KANBAN_ROOT;
# no dev tree access required. Global require_dev_tree removed (D5).

# --- Now enable strict mode for our own code ---
set -euo pipefail
# shellcheck source=lib/env_bootstrap.sh
source "$(dirname "${BASH_SOURCE[0]}")/lib/env_bootstrap.sh"

# --- Source project paths helper ---
KANBAN_ROOT="$TEAM_ROOT"
export KANBAN_ROOT
_SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
if [[ -f "$_SCRIPT_DIR/lib/project_paths.sh" ]]; then
    source "$_SCRIPT_DIR/lib/project_paths.sh"
fi

# --- Source shared task-ID helper ---
if [[ -f "$_SCRIPT_DIR/lib/task_ids.sh" ]]; then
    source "$_SCRIPT_DIR/lib/task_ids.sh"
fi

# --- Clean exit handling ---
cleanup_on_exit() {
  local exit_code=$?
  jobs -p 2>/dev/null | xargs -r kill 2>/dev/null || true
  wait 2>/dev/null || true
  exit $exit_code
}
trap cleanup_on_exit EXIT

# --- Defaults ---
DRY_RUN=false
BRIEF_FILE=""
_PO_PROJECT_ARG=""
OUTPUT_DIR=""

# --- Parse args ---
while [[ $# -gt 0 ]]; do
  case "$1" in
    --dry-run)
      DRY_RUN=true; shift ;;
    --project)
      if [[ -z "${2:-}" ]]; then
        echo "ERROR: --project requires a project name" >&2
        exit 1
      fi
      _PO_PROJECT_ARG="$2"
      shift 2
      ;;
    --output)
      if [[ -z "${2:-}" ]]; then
        echo "ERROR: --output requires a directory path" >&2
        exit 1
      fi
      OUTPUT_DIR="$2"
      shift 2
      ;;
    --help|-h)
      cat <<EOF
Usage: po-agent.sh <brief.md> [options]

Validate a brief file and invoke the PO subagent directly (one-shot).
The PO subagent expands the brief into a full requirements document and
creates a PM ticket for decomposition.

Options:
  --project <name>  Project name (required in full mode; not needed with --output)
  --dry-run         Preview what would happen without invoking the subagent
  --output <dir>    Draft mode: write the expanded document to <dir>/ instead of
                    creating a PM ticket.  The dir is created if missing.  An
                    existing same-named file gets a numeric suffix (-2, -3, ...).
                    Final stdout line: DRAFT: <path>.  Mutually exclusive with
                    --dry-run.
  --help, -h        Show this help

Unlike pm-agent.sh, PO is invoked immediately rather than queued.
EOF
      exit 0
      ;;
    -*)
      echo "ERROR: Unknown option: $1" >&2
      exit 1
      ;;
    *)
      if [[ -z "$BRIEF_FILE" ]]; then
        BRIEF_FILE="$1"
      else
        echo "ERROR: Unexpected argument: $1" >&2
        exit 1
      fi
      shift
      ;;
  esac
done

# --- Require brief file argument ---
if [[ -z "$BRIEF_FILE" ]]; then
  echo "ERROR: Brief file is required." >&2
  echo "Usage: po-agent.sh <brief.md> [--project <name>] [--dry-run]" >&2
  exit 1
fi

# --- Validate brief file exists ---
if [[ ! -f "$BRIEF_FILE" ]]; then
  echo "ERROR: brief file not found: $BRIEF_FILE" >&2
  exit 1
fi

# Resolve to absolute path so the subagent can read it from any cwd
BRIEF_FILE="$(cd "$(dirname "$BRIEF_FILE")" && pwd)/$(basename "$BRIEF_FILE")"

# --- Extract Target Version from brief file ---
# Looks for a line starting with v<digits>.<digits>.<digits> under ## Target Version
TARGET_VERSION="$(awk '/^## Target Version/{flag=1; next} /^##/{flag=0} flag && /^v[0-9]/{print; exit}' "$BRIEF_FILE" | tr -d '[:space:]')"

# --- Validate Target Version ---
VERSION_REGEX='^v[0-9]+\.[0-9]+\.[0-9]+$'
if [[ -z "$TARGET_VERSION" ]] || [[ ! "$TARGET_VERSION" =~ $VERSION_REGEX ]]; then
  echo "ERROR: invalid Target Version in brief file." >&2
  echo "Expected a line matching vX.Y.Z under '## Target Version'." >&2
  echo "Found: '${TARGET_VERSION:-<none>}'" >&2
  exit 1
fi

# --- Refuse --output combined with --dry-run (contradiction) ---
if [[ -n "$OUTPUT_DIR" && "$DRY_RUN" == "true" ]]; then
  echo "ERROR: --output and --dry-run are mutually exclusive." >&2
  echo "--dry-run means no writes; --output means write a draft file." >&2
  echo "Choose one: --output <dir> to produce a draft, or --dry-run to preview metadata." >&2
  exit 1
fi

# --- Validate claude CLI is available ---
if ! command -v claude >/dev/null 2>&1; then
  echo "ERROR: claude CLI not found in PATH" >&2
  echo "Install the Claude CLI and ensure it is in your PATH." >&2
  exit 1
fi

# --- Draft mode: compute output path, handle collisions, invoke subagent ---
if [[ -n "$OUTPUT_DIR" ]]; then
  # Derive slug from the brief's basename (no extension), normalised to
  # lowercase with runs of non-alphanumeric characters collapsed to hyphens.
  _BRIEF_SLUG="$(basename "$BRIEF_FILE" .md \
    | tr '[:upper:]' '[:lower:]' \
    | sed 's/[^a-z0-9]\+/-/g; s/^-//; s/-$//')"
  _DRAFT_BASENAME="${TARGET_VERSION}-${_BRIEF_SLUG}"

  # Create the output directory if it does not exist.
  mkdir -p "$OUTPUT_DIR"

  # Resolve collision: never overwrite an existing file.
  _DRAFT_PATH="${OUTPUT_DIR}/${_DRAFT_BASENAME}.md"
  _DRAFT_SUFFIX=1
  while [[ -e "$_DRAFT_PATH" ]]; do
    _DRAFT_SUFFIX=$(( _DRAFT_SUFFIX + 1 ))
    _DRAFT_PATH="${OUTPUT_DIR}/${_DRAFT_BASENAME}-${_DRAFT_SUFFIX}.md"
  done
  if [[ "$_DRAFT_SUFFIX" -gt 1 ]]; then
    echo "NOTICE: output collision — writing to ${_DRAFT_BASENAME}-${_DRAFT_SUFFIX}.md instead of ${_DRAFT_BASENAME}.md"
  fi

  echo "=== PO Agent: Draft Mode ==="
  echo ""
  echo "Brief File     : $BRIEF_FILE"
  echo "Target Version : $TARGET_VERSION"
  echo "Output Dir     : $OUTPUT_DIR"
  echo "Draft File     : $_DRAFT_PATH"
  echo ""
  echo "Handing off to PO subagent (draft mode — no PM ticket will be created)..."
  echo ""

  claude -p --dangerously-skip-permissions \
    "Use the po subagent to process the brief at ${BRIEF_FILE} in DRAFT MODE. The Target Version is ${TARGET_VERSION}. The kanban root is ${TEAM_ROOT}. Validate the Target Version field, expand the brief into a full requirements document using your full expansion process (governance read, tree verification, 9-rule method, Assumptions ledger). Write the resulting requirements document to exactly this path: ${_DRAFT_PATH}. Do NOT create a PM ticket. Do NOT write a backlog entry. Do NOT write anything under projects/. Write ONLY the requirements document file to the exact path specified."

  echo ""
  echo "DRAFT: ${_DRAFT_PATH}"
  exit 0
fi

# --- Resolve target project (full mode only) ---
# Requires explicit --project <name> or $PGAI_PROJECT_NAME; fails loudly when
# neither is provided.  Callers that previously relied on the first-registered
# default must now supply the project explicitly.
_PO_PROJECT="${_PO_PROJECT_ARG:-${PGAI_PROJECT_NAME:-}}"
if [[ -z "$_PO_PROJECT" ]]; then
  echo "ERROR: no project specified; pass --project <name> or set PGAI_PROJECT_NAME" >&2
  exit 1
fi

# --- Compute DATE_STAMP and TASKS_DIR for the PM task ID ---
# Format: PM-YYYYMMDD-NNN-decompose-<slug>  (new format, no PARTICIPANT prefix)
# Uses the shared kanban_task_id helper so that all agents (PM, CODER,
# WRITER, TESTER, CM) share one canonical sequence-number implementation.
DATE_STAMP="$(date +%Y%m%d)"
TASKS_DIR="$(pp_tasks_dir "$_PO_PROJECT")"
mkdir -p "$TASKS_DIR"
# Pre-compute SEQ_PAD for display and for passing to the subagent prompt.
SEQ_PAD="$(kanban_next_task_seq "$TASKS_DIR" "PM" "$DATE_STAMP")"

# --- Preview in dry-run mode ---
if [[ "$DRY_RUN" == "true" ]]; then
  echo "=== DRY-RUN: PO Agent Preview ==="
  echo ""
  echo "Brief File     : $BRIEF_FILE"
  echo "Target Version : $TARGET_VERSION"
  echo "Kanban Root    : $TEAM_ROOT"
  echo "Date Stamp     : $DATE_STAMP"
  echo "Sequence       : $SEQ_PAD"
  echo "PM Task ID     : PM-${DATE_STAMP}-${SEQ_PAD}-decompose-<slug>"
  echo "Subagent       : po  (via: claude -p --dangerously-skip-permissions)"
  echo ""
  echo "Would invoke PO subagent with:"
  echo "  claude -p --dangerously-skip-permissions \\"
  echo "    \"Use the po subagent. Read the brief at $BRIEF_FILE ...\""
  echo ""
  echo "Dry run complete. No files written and no subagent invoked."
  echo "To run for real: po-agent.sh $BRIEF_FILE"
  exit 0
fi

# --- Invoke PO subagent ---
echo "=== PO Agent: Invoking PO subagent ==="
echo ""
echo "Brief File     : $BRIEF_FILE"
echo "Target Version : $TARGET_VERSION"
echo "Kanban Root    : $TEAM_ROOT"
echo "Date Stamp     : $DATE_STAMP"
echo "Sequence       : $SEQ_PAD"
echo "PM Task ID     : PM-${DATE_STAMP}-${SEQ_PAD}-decompose-<slug>"
echo ""
echo "Handing off to PO subagent..."
echo ""

claude -p --dangerously-skip-permissions \
  "Use the po subagent to process the brief at ${BRIEF_FILE}. The Target Version is ${TARGET_VERSION}. The kanban root is ${TEAM_ROOT}. Use DATE_STAMP=${DATE_STAMP} and SEQ_PAD=${SEQ_PAD} when constructing the PM task ID. The canonical PM task ID format is PM-${DATE_STAMP}-${SEQ_PAD}-decompose-<slug> (no PARTICIPANT prefix) where <slug> is derived from the brief filename. Validate the Target Version field, expand the brief into a full requirements document, and create a PM ticket so the PM agent can decompose the work into kanban tasks."

echo ""
echo "=== PO Agent Complete ==="
echo ""
echo "The PO subagent has finished. Check the kanban root for:"
echo "  - A new requirements document (written by the PO subagent)"
echo "  - A new PM ticket in the pm_backlog queue"
echo ""
echo "To process the PM ticket, run:"
echo "  wake-batch.sh --agent=pm"
