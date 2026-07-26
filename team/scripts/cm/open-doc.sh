#!/usr/bin/env bash
# cm-open-doc.sh
# CM operation: open a new document/creative project version.
#
# Usage:
#   cm-open-doc.sh <project-name> [target-version]
#   cm-open-doc.sh --help
#
# Arguments:
#   project-name    — project identifier (lowercase letters, digits, hyphens)
#   target-version  — optional semver version (e.g. v0.0.1); overrides
#                     PGAI_TARGET_VERSION env var when supplied.
#   --help, -h      — print full usage and exit 0
#
# Version resolution (first match wins):
#   1. $2 arg (target-version) — explicit override
#   2. PGAI_TARGET_VERSION env var — set by materializer / CM agent at runtime
#
# Behavior:
#   1. Resolve KANBAN_ROOT from env (PGAI_AGENT_KANBAN_ROOT_PATH or ~/pgai_agent_kanban)
#   2. Validate project name
#   3. Resolve the document version from PGAI_TARGET_VERSION / $2 (semver)
#   4. Compute the task-local scratch directory under PGAI_AGENT_KANBAN_TEMP_DIR:
#        $PGAI_AGENT_KANBAN_TEMP_DIR/doc/<project-name>/<semver>/{input,working,output}
#   5. Create {input,working,output} directories (idempotent)
#   6. Print summary; export PGAI_DOC_WORKING_DIR for callers who source this
#      script (informational; bash invocations can read from stdout)
#
# NOTE: The staging/requirements.md handoff has been removed.
#   The invoking script is responsible for placing requirements.md directly into
#   ${DOC_WORK_DIR}/input/ before (or after) open-doc.sh runs.  This script no
#   longer touches $KANBAN_ROOT/artifacts/<project>/staging/ on the semver path.
#
# NO git operations. Filesystem only.
#
# Idempotency: if the {input,working,output} directories already exist for this
# semver, the script re-cleans working/ (stale-content guard) and
# re-writes Active RC in release-state.md, then exits.
#
# Artifacts and state files touched:
#   WRITTEN:
#     $DOC_WORK_DIR/input/     (created; under PGAI_AGENT_KANBAN_TEMP_DIR)
#     $DOC_WORK_DIR/working/   (created AND cleaned on every open; under PGAI_AGENT_KANBAN_TEMP_DIR)
#     $DOC_WORK_DIR/output/    (created; under PGAI_AGENT_KANBAN_TEMP_DIR)
#     projects/<name>/release-state.md — ## Active RC set to TARGET_VERSION at open
#   NOT TOUCHED:
#     $KANBAN_ROOT/artifacts/  — not read or written on the semver path
#     PROJECT.md         — not read or written (## Next Version scheme retired)
#     Any git index or ref — this script performs no git operations
#     projects/<name>/artifacts/ — not written by this script (publish is in finalize)
#
# Configuration:
#   PGAI_AGENT_KANBAN_ROOT_PATH   — kanban root (defaults to ~/pgai_agent_kanban)
#   KANBAN_ROOT                   — alias (if PGAI_AGENT_KANBAN_ROOT_PATH not set)
#   PGAI_TARGET_VERSION           — semver version for this document run (e.g. v0.0.1)
#   PGAI_AGENT_KANBAN_TEMP_DIR    — temp dir root override (resolved via temp.sh resolver)

# --- Argument parsing ---
_cm_open_doc_usage() {
  echo "Usage: $(basename "$0") <project-name> [target-version]" >&2
  echo "" >&2
  echo "  project-name    project identifier, lowercase letters/digits/hyphens" >&2
  echo "  target-version  semver string (e.g. v0.0.1); overrides PGAI_TARGET_VERSION" >&2
  echo "  --help, -h      print full usage and exit 0" >&2
}

_cm_open_doc_help() {
  cat <<HELPTEXT
Usage: $(basename "$0") <project-name> [target-version]

Open a new document/creative project version: create the task-local scratch
directories (input/, working/, output/) and set Active RC in release-state.md.
No git operations — filesystem only.

Arguments:
  project-name    project identifier (lowercase letters, digits, hyphens;
                  e.g. kids-story-creek)
  target-version  semver version for this document run (e.g. v0.0.1);
                  overrides PGAI_TARGET_VERSION env var when supplied
  --help, -h      print this message and exit 0

Version resolution (first match wins):
  1. target-version positional argument
  2. PGAI_TARGET_VERSION environment variable

Exit codes:
  0  Working directories created (or already exist) and Active RC written
     (or --help requested)
  1  Missing project-name, invalid project name or version, or missing version

Example:
  cm-open-doc.sh kids-story-creek v0.0.1
  PGAI_TARGET_VERSION=v0.0.1 cm-open-doc.sh kids-story-creek

Configuration:
  PGAI_AGENT_KANBAN_ROOT_PATH  kanban root (default: ~/pgai_agent_kanban)
  PGAI_TARGET_VERSION          semver version (when not passed as \$2)
  PGAI_AGENT_KANBAN_TEMP_DIR   temp dir root override
HELPTEXT
}

# Handle --help/-h before positional assignment so it fires even when other
# args are absent.
for _arg in "$@"; do
  case "$_arg" in
    --help|-h)
      _cm_open_doc_help
      exit 0
      ;;
  esac
done
unset _arg

PROJECT_NAME="${1:-}"
VERSION_ARG="${2:-}"

if [[ -z "$PROJECT_NAME" ]]; then
  echo "ERROR: missing required argument <project-name>" >&2
  echo "" >&2
  _cm_open_doc_usage
  exit 1
fi

# shellcheck source=../lib/env_bootstrap.sh
source "$(dirname "${BASH_SOURCE[0]}")/../lib/env_bootstrap.sh"

# --- Resolve kanban root ---
KANBAN_ROOT="${PGAI_AGENT_KANBAN_ROOT_PATH}"

# --- Source optional config files (BEFORE strict mode) ---
[[ -f "$KANBAN_ROOT/bashrc" ]] && source "$KANBAN_ROOT/bashrc"
[[ -f "$KANBAN_ROOT/env" ]] && source "$KANBAN_ROOT/env"
[[ -f "$HOME/.config/pgai-kanban.cfg" ]] && source "$HOME/.config/pgai-kanban.cfg"

# --- Source temp.sh for resolver helpers (include guard in temp.sh prevents double-load) ---
_OPEN_DOC_SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=../lib/temp.sh
source "${_OPEN_DOC_SCRIPT_DIR}/../lib/temp.sh"
unset _OPEN_DOC_SCRIPT_DIR

# --- Enable strict mode ---
set -euo pipefail

# --- Clean exit handling ---
cleanup_on_exit() {
  local exit_code=$?
  jobs -p 2>/dev/null | xargs -r kill 2>/dev/null || true
  wait 2>/dev/null || true
  exit $exit_code
}
trap cleanup_on_exit EXIT

# --- Validate project name ---
PROJECT_NAME_RE='^[a-z0-9][a-z0-9-]*$'
if [[ ! "$PROJECT_NAME" =~ $PROJECT_NAME_RE ]]; then
  echo "ERROR: invalid project name: '$PROJECT_NAME'" >&2
  echo "Project names must match ^[a-z0-9][a-z0-9-]*$ (lowercase letters, digits, hyphens only)" >&2
  exit 1
fi

# --- Resolve target version ---
# Priority: $2 arg > PGAI_TARGET_VERSION env var
TARGET_VERSION="${VERSION_ARG:-${PGAI_TARGET_VERSION:-}}"

if [[ -z "$TARGET_VERSION" ]]; then
  echo "ERROR: document version not found." >&2
  echo "Set PGAI_TARGET_VERSION (e.g. export PGAI_TARGET_VERSION=v0.0.1) or pass as \$2." >&2
  exit 1
fi

# Normalize: ensure v-prefix
[[ "${TARGET_VERSION}" != v* ]] && TARGET_VERSION="v${TARGET_VERSION}"

# Validate semver pattern: vX.Y.Z (major.minor.patch, all non-negative integers)
SEMVER_RE='^v[0-9]+\.[0-9]+\.[0-9]+$'
if [[ ! "$TARGET_VERSION" =~ $SEMVER_RE ]]; then
  echo "ERROR: invalid semver version: '$TARGET_VERSION'" >&2
  echo "Expected format: vX.Y.Z (e.g. v0.0.1, v1.2.3)" >&2
  exit 1
fi

# --- Resolve per-project temp root via resolver ---
# pgai_project_temp_dir returns $(pgai_temp_dir)/projects/<project-name>.
# Using the per-project subtree prevents cross-project temp collisions.
TEMP_ROOT="$(pgai_temp_dir)"
_PROJECT_TEMP="$(pgai_project_temp_dir "${PROJECT_NAME}")"

# --- Compute task-local scratch directory ---
# Layout: $(_PROJECT_TEMP)/doc/<semver>/
# Layout uses a per-project subtree to eliminate shared doc namespace collisions.
DOC_WORK_DIR="${_PROJECT_TEMP}/doc/${TARGET_VERSION}"

# --- Helper: write Active RC to release-state.md ---
# Called on BOTH first-open and idempotent-re-open paths.
# Reads the existing Last Released field to preserve it; writes Active RC = TARGET_VERSION.
# Non-blocking: failure logs a warning; open-doc continues.
write_active_rc() {
  local rs_path="${KANBAN_ROOT}/projects/${PROJECT_NAME}/release-state.md"
  if [[ ! -f "$rs_path" ]]; then
    echo "[cm-open-doc] WARNING: release-state.md not found at ${rs_path}; cannot write Active RC." >&2
    echo "[cm-open-doc]   Create the file (projects/${PROJECT_NAME}/release-state.md) before running open-doc." >&2
    return
  fi
  # Read existing Last Released to preserve it; fall back to 'none'.
  local last_released
  last_released="$(python3 - "$rs_path" 'Last Released' <<'PY' || echo "none"
import re, sys, pathlib
text = pathlib.Path(sys.argv[1]).read_text(encoding="utf-8")
pattern = sys.argv[2]
m = re.search(r"##\s*" + re.escape(pattern) + r"\s*\n(.*?)(?=\n##|\Z)", text, re.IGNORECASE | re.DOTALL)
print(m.group(1).strip() if m else "none")
PY
)"
  [[ -z "$last_released" ]] && last_released="none"

  # Write complete release-state.md with Active RC = TARGET_VERSION.
  # Mirrors open-rc.sh Step (lines ~315-334): canonical here-doc, no sed/regex.
  if cat > "$rs_path" <<EOF
# Release State

## Active RC
${TARGET_VERSION}

## RC Opened At
none

## RC Opened By Task
none

## Last Released
${last_released}
EOF
  then
    echo "  release-state.md updated: Active RC -> ${TARGET_VERSION}"
    echo "  Path: ${rs_path}"
  else
    echo "[cm-open-doc] WARNING: failed to write release-state.md at ${rs_path}" >&2
  fi
}

# --- Idempotency check ---
if [[ -d "${DOC_WORK_DIR}/input" && -d "${DOC_WORK_DIR}/working" && -d "${DOC_WORK_DIR}/output" ]]; then
  echo "NOTE: working area for ${PROJECT_NAME} ${TARGET_VERSION} already exists at ${DOC_WORK_DIR}"

  # --- Clean working/ on every open (stale-content guard) ---
  # Wipes and recreates working/ so stale content from a prior run or a recreated
  # project can never survive to finalize. input/ and output/ are preserved.
  echo "Cleaning working dir (stale-content guard): ${DOC_WORK_DIR}/working"
  rm -rf "${DOC_WORK_DIR}/working"
  mkdir -p "${DOC_WORK_DIR}/working"
  echo "  Cleaned and recreated: ${DOC_WORK_DIR}/working"

  # --- Write Active RC to release-state.md (idempotent recovery path) ---
  write_active_rc

  # --- Write per-RC release-state JSON with opened_at (idempotent recovery path) ---
  # Mirrors open-rc.sh lines 352-358. Overwrites on recovery re-runs (idempotent).
  # Non-blocking: any failure is logged as a warning; open-doc continues.
  RC_STATE_DIR="${KANBAN_ROOT}/projects/${PROJECT_NAME}/release-state"
  RC_STATE_JSON="${RC_STATE_DIR}/${TARGET_VERSION}.json"
  OPENED_AT_UTC="$(date -u +%Y-%m-%dT%H:%M:%SZ)"
  mkdir -p "$RC_STATE_DIR"
  python3 "$KANBAN_ROOT/pgai_agent_kanban/cm/write_rc_state.py" open \
      "$TARGET_VERSION" "$OPENED_AT_UTC" > "$RC_STATE_JSON" || \
    echo "[cm-open-doc] WARNING: could not write per-RC release-state JSON at $RC_STATE_JSON" >&2

  echo ""
  echo "Active project: ${PROJECT_NAME} ${TARGET_VERSION}"
  echo "  Working dir:  ${DOC_WORK_DIR}"
  echo "PGAI_DOC_WORKING_DIR=${DOC_WORK_DIR}"
  exit 0
fi

# --- Create task-local scratch directories ---
echo "Creating ${DOC_WORK_DIR}/{input,working,output}..."
mkdir -p "${DOC_WORK_DIR}/input"
mkdir -p "${DOC_WORK_DIR}/working"
mkdir -p "${DOC_WORK_DIR}/output"
echo "  Created: ${DOC_WORK_DIR}/input"
echo "  Created: ${DOC_WORK_DIR}/working"
echo "  Created: ${DOC_WORK_DIR}/output"

# --- Write Active RC to release-state.md ---
# Records Active RC at open so the bare-'rc' HALT-AFTER drain captures the
# in-flight version rather than promoting immediately.
write_active_rc

# --- Write per-RC release-state JSON with opened_at ---
# Written after successful working directory creation so it is only present when
# the scratch dirs actually exist. Mirrors open-rc.sh lines 352-358.
# Schema: { "closed_at", "opened_at", "outcome", "rc" } (sort_keys=True)
# Non-blocking: any failure is logged as a warning; open-doc continues.
RC_STATE_DIR="${KANBAN_ROOT}/projects/${PROJECT_NAME}/release-state"
RC_STATE_JSON="${RC_STATE_DIR}/${TARGET_VERSION}.json"
OPENED_AT_UTC="$(date -u +%Y-%m-%dT%H:%M:%SZ)"
mkdir -p "$RC_STATE_DIR"
python3 "$KANBAN_ROOT/pgai_agent_kanban/cm/write_rc_state.py" open \
    "$TARGET_VERSION" "$OPENED_AT_UTC" > "$RC_STATE_JSON" || \
  echo "[cm-open-doc] WARNING: could not write per-RC release-state JSON at $RC_STATE_JSON" >&2
echo "  Per-RC release-state JSON written: $RC_STATE_JSON"

# --- Print summary ---
echo ""
echo "cm-open-doc.sh complete."
echo "  Project:         ${PROJECT_NAME}"
echo "  Version:         ${TARGET_VERSION}"
echo "  Temp root:       ${TEMP_ROOT}"
echo "  Input dir:       ${DOC_WORK_DIR}/input"
echo "  Working dir:     ${DOC_WORK_DIR}/working"
echo "  Output dir:      ${DOC_WORK_DIR}/output"
echo ""
echo "Active project: ${PROJECT_NAME} ${TARGET_VERSION}"
echo "PGAI_DOC_WORKING_DIR=${DOC_WORK_DIR}"
exit 0
