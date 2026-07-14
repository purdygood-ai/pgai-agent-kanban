#!/usr/bin/env bash
# cm-finalize.sh
# CM operation: finalize a project version by packaging working/ into output/.
#
# Usage:
#   cm-finalize.sh <project-name> <version>
#
# Arguments:
#   project-name  — project identifier (e.g. kids-story-creek)
#   version       — semver string (e.g. v0.0.1): document workflow mode
#
# Behavior:
#   1. Resolve KANBAN_ROOT and temp root from env
#   2. Read requirement doc (## Artifact Name, ## Output Formats) for output name and formats
#   3. Find the deliverable .md file from WRITER polish task artifacts (PGAI_WRITER_POLISH_TASK_ARTIFACTS)
#   4. For each output format:
#       - markdown: copy working/<deliverable>.md to output/<output-name>.md
#       - pdf: pandoc convert if pandoc available; graceful warning skip if not
#       - other: print "not yet supported" message and log
#   5. Write output/SUMMARY.md (in the task-local output/ directory)
#   6. Publish main deliverable to:
#        projects/<project>/artifacts/v<semver>-<artifact_name>.<ext>
#      Never overwrites an existing entry; exits advisory error if collision.
#   7. Flip originating requirement ## Status: running -> done
#
# Publish rules:
#   - Publish path: projects/<project>/artifacts/v<semver>-<artifact_name>.<ext>
#   - Only the MAIN deliverable is published; SUMMARY and other intermediates
#     remain in the task-local output/ directory.
#   - A collision (destination already exists) is logged and skipped — prior
#     versions are NEVER overwritten.
#   - PGAI_TARGET_VERSION env var is NOT required; the semver version arg IS the
#     publish target version.
#   - PGAI_PROJECT_NAME must be set to identify the target artifacts directory.
#
# NO git operations. Filesystem only.
#
# Configuration:
#   PGAI_AGENT_KANBAN_ROOT_PATH         — kanban root (defaults to ~/pgai_agent_kanban)
#   PGAI_AGENT_KANBAN_TEMP_DIR          — temp dir root override (resolved via temp.sh resolver)
#   PGAI_TARGET_VERSION                 — semver; overrides $2 for callers that set it
#   PGAI_PROJECT_NAME                   — kanban project name (metadata source + publish target)
#   PGAI_ARTIFACT_NAME                  — artifact slug; defaults to OUTPUT_NAME from requirement doc
#   PGAI_WRITER_POLISH_TASK_ARTIFACTS   — path to WRITER polish task artifacts (required)
#   PGAI_DOC_WORKING_DIR                — explicit task-local scratch dir (overrides temp-root logic)

# --- Argument parsing ---
PROJECT_NAME="${1:-}"
VERSION_ARG="${2:-}"

if [[ -z "$PROJECT_NAME" || -z "$VERSION_ARG" ]]; then
  echo "ERROR: missing required arguments" >&2
  echo "" >&2
  echo "Usage: $(basename "$0") <project-name> <version>" >&2
  echo "" >&2
  echo "  project-name: lowercase letters, digits, hyphens (e.g. kids-story-creek)" >&2
  echo "  version: semver (e.g. v0.0.1)" >&2
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
_FINALIZE_SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=../lib/temp.sh
source "${_FINALIZE_SCRIPT_DIR}/../lib/temp.sh"
unset _FINALIZE_SCRIPT_DIR

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

# --- Validate version: must be semver ---
#
# Normalize: strip leading 'v' to get the bare version string for comparison.
_ver_bare="${VERSION_ARG#v}"

SEMVER_RE='^[0-9]+\.[0-9]+\.[0-9]+$'

if [[ "$_ver_bare" =~ $SEMVER_RE ]]; then
  TARGET_VERSION="v${_ver_bare}"   # canonical: always has v-prefix
else
  echo "ERROR: invalid version argument: '$VERSION_ARG'" >&2
  echo "Version must be semver (e.g. v0.0.1)" >&2
  exit 1
fi

# --- Resolve temp root via resolver ---
TEMP_ROOT="$(pgai_temp_dir)"

# --- Resolve paths ---
# Working dirs live under the per-project temp root.
# PGAI_DOC_WORKING_DIR may be set by callers who source open-doc.sh; if so, use it directly.
if [[ -n "${PGAI_DOC_WORKING_DIR:-}" ]]; then
  DOC_SCRATCH_DIR="${PGAI_DOC_WORKING_DIR}"
else
  # Per-project layout: $(pgai_project_temp_dir <project>)/doc/<semver>/
  # Mirrors the path open-doc.sh creates so finalize finds the right working dir.
  DOC_SCRATCH_DIR="$(pgai_project_temp_dir "${PROJECT_NAME}")/doc/${TARGET_VERSION}"
fi
WORKING_DIR="${DOC_SCRATCH_DIR}/working"
OUTPUT_DIR="${DOC_SCRATCH_DIR}/output"

if [[ ! -d "$DOC_SCRATCH_DIR" ]]; then
  echo "ERROR: document scratch directory not found: $DOC_SCRATCH_DIR" >&2
  echo "Run cm-open-doc.sh $PROJECT_NAME $TARGET_VERSION first to create the working directories." >&2
  exit 1
fi

# --- Validate working directory exists ---
if [[ ! -d "$WORKING_DIR" ]]; then
  echo "ERROR: working directory not found: $WORKING_DIR" >&2
  exit 1
fi

# --- Read metadata: output name and output formats ---
#
# Read from the originating requirement doc.
#   - ## Artifact Name  → OUTPUT_NAME  (fallback: project name with WARNING)
#   - ## Output Formats → OUTPUT_FORMATS (fallback: markdown with WARNING)
#   Requirement file is located by scanning the project's requirements/ directory
#   for the file whose ## Target Version matches TARGET_VERSION.  If PGAI_PROJECT_NAME
#   is not set, or no matching requirement file is found, fallbacks apply.

# Helper: read a markdown section body from a file.
# Args: <file_path> <section_pattern_regex>
read_md_field() {
  local file_path="$1"
  local field_pattern="$2"
  python3 - "$file_path" "$field_pattern" <<'PY'
import re, sys, pathlib

text = pathlib.Path(sys.argv[1]).read_text(encoding="utf-8")
pattern = sys.argv[2]

# Find the section matching the pattern and return its body.
# The boundary lookahead stops at: the next ## heading, a horizontal rule
# (three or more dashes on its own line), or end-of-file.  This prevents a
# '---' markdown rule from leaking into the captured body.
section_re = re.compile(
    r'##\s*' + pattern + r'\s*\n(.*?)(?=\n##|\n-{3,}\s*(?:\n|\Z)|\Z)',
    re.IGNORECASE | re.DOTALL,
)
m = section_re.search(text)
if m:
    body = m.group(1).strip()
    print(body)
else:
    print("")
PY
}

_meta_kanban_project="${PGAI_PROJECT_NAME:-}"
_meta_req_file=""

if [[ -n "$_meta_kanban_project" ]]; then
  _meta_req_dir="${KANBAN_ROOT}/projects/${_meta_kanban_project}/requirements"
  if [[ -d "$_meta_req_dir" ]]; then
    # Locate the requirement file whose ## Target Version matches TARGET_VERSION.
    _meta_req_file="$(python3 - "$_meta_req_dir" "$TARGET_VERSION" 2>/dev/null <<'PY' || true
import re, sys, pathlib

req_dir = pathlib.Path(sys.argv[1])
target = sys.argv[2].strip()
if not target.startswith("v"):
    target = "v" + target

version_pattern = re.compile(
    r'^\s*' + re.escape(target) + r'\s*$',
    re.MULTILINE,
)
target_ver_re = re.compile(
    r'##\s*Target\s*Version\s*\n(.*?)(?=\n##|\Z)',
    re.IGNORECASE | re.DOTALL,
)

for md_file in sorted(req_dir.glob("*.md")):
    if md_file.name.startswith("REQUIREMENTS-TEMPLATE"):
        continue
    try:
        text = md_file.read_text(encoding="utf-8")
    except OSError:
        continue
    m = target_ver_re.search(text)
    if not m:
        continue
    if version_pattern.search(m.group(1)):
        print(str(md_file))
        break
PY
)"
  fi
fi

if [[ -n "$_meta_req_file" ]]; then
  # Read ## Artifact Name from requirement doc.
  # Reduce to the first non-empty trimmed line so a trailing '---' rule or
  # blank lines in the requirement doc cannot corrupt the published filename.
  OUTPUT_NAME="$(read_md_field "$_meta_req_file" 'Artifact\s*Name' \
    | sed -n '/[^[:space:]]/{s/[[:space:]]*$//;p;q}')"
  if [[ -z "$OUTPUT_NAME" ]]; then
    OUTPUT_NAME="$PROJECT_NAME"
    echo "WARNING: ## Artifact Name not found in requirement doc; using project name as fallback: $OUTPUT_NAME"
  fi
  # Read ## Output Formats from requirement doc (optional field).
  OUTPUT_FORMATS_RAW="$(read_md_field "$_meta_req_file" 'Output\s*Formats?')"
else
  # No requirement file found (PGAI_PROJECT_NAME not set or no version match).
  # Fall back gracefully — warn and use project name.
  OUTPUT_NAME="$PROJECT_NAME"
  echo "WARNING: Could not locate requirement doc for $TARGET_VERSION; using project name as Output Name fallback: $OUTPUT_NAME"
  OUTPUT_FORMATS_RAW=""
fi

# Parse list items from output formats (lines starting with - or *)
OUTPUT_FORMATS=()
while IFS= read -r line; do
  # Strip leading "- " or "* " list markers
  item="$(echo "$line" | sed -E 's/^[[:space:]]*[-*][[:space:]]*//' | sed -E 's/[[:space:]]+$//')"
  if [[ -n "$item" ]]; then
    OUTPUT_FORMATS+=("$item")
  fi
done <<< "$OUTPUT_FORMATS_RAW"

if [[ ${#OUTPUT_FORMATS[@]} -eq 0 ]]; then
  echo "WARNING: No output formats found in requirement doc; defaulting to markdown"
  OUTPUT_FORMATS=("markdown")
fi

echo "Project: $PROJECT_NAME $TARGET_VERSION"
echo "Output name: $OUTPUT_NAME"
echo "Output formats: ${OUTPUT_FORMATS[*]}"
echo ""

# --- Locate the deliverable .md file ---
#
# The canonical deliverable is the WRITER polish task's polished.md, sourced via
# PGAI_WRITER_POLISH_TASK_ARTIFACTS.  Reading from the temp working/ dir is
# unreliable: that dir may contain stale content from a prior run or a recreated
# project.
# - If PGAI_WRITER_POLISH_TASK_ARTIFACTS is set and polished.md exists there,
#   use it as the deliverable (single source of truth).
# - If PGAI_WRITER_POLISH_TASK_ARTIFACTS is not set, or polished.md is absent,
#   finalize FAILS LOUDLY (non-zero, clear error) — never falls back to stale
#   working-dir content.

_writer_artifacts_dir="${PGAI_WRITER_POLISH_TASK_ARTIFACTS:-}"
if [[ -z "$_writer_artifacts_dir" ]]; then
  echo "ERROR: PGAI_WRITER_POLISH_TASK_ARTIFACTS is not set." >&2
  echo "  finalize requires the WRITER polish task artifacts directory to source the deliverable." >&2
  echo "  Set PGAI_WRITER_POLISH_TASK_ARTIFACTS to the polish task's artifacts/ path before running finalize." >&2
  exit 1
fi
_writer_polished="${_writer_artifacts_dir}/polished.md"
if [[ ! -f "$_writer_polished" ]]; then
  echo "ERROR: WRITER polished.md not found at ${_writer_polished}" >&2
  echo "  finalize must publish the deliverable the WRITER actually produced for this version." >&2
  echo "  Ensure the WRITER polish task has completed and written polished.md to its artifacts/ directory." >&2
  echo "  PGAI_WRITER_POLISH_TASK_ARTIFACTS=${_writer_artifacts_dir}" >&2
  exit 1
fi
DELIVERABLE="$_writer_polished"
echo "Source deliverable (WRITER polish artifacts): $DELIVERABLE"

# --- Ensure output directory exists ---
mkdir -p "$OUTPUT_DIR"

# --- Track what was produced ---
OUTPUTS_PRODUCED=()
OUTPUTS_SKIPPED=()
OUTPUTS_UNSUPPORTED=()
# Published artifacts (versioned copies in projects/<proj>/artifacts/).
_PUB_ARTIFACTS_PUBLISHED=()

# --- Process each output format ---
for FORMAT in "${OUTPUT_FORMATS[@]}"; do
  FORMAT_LOWER="${FORMAT,,}"  # lowercase

  case "$FORMAT_LOWER" in
    markdown|md)
      DEST="$OUTPUT_DIR/${OUTPUT_NAME}.md"
      echo "[markdown] Copying to $DEST..."
      cp "$DELIVERABLE" "$DEST"
      OUTPUTS_PRODUCED+=("$DEST")
      echo "[markdown] Done."
      ;;

    pdf)
      DEST="$OUTPUT_DIR/${OUTPUT_NAME}.pdf"
      if command -v pandoc >/dev/null 2>&1; then
        echo "[pdf] Converting with pandoc to $DEST..."
        if pandoc "$DELIVERABLE" -o "$DEST" 2>/dev/null; then
          OUTPUTS_PRODUCED+=("$DEST")
          echo "[pdf] Done."
        else
          echo "WARNING: [pdf] pandoc conversion failed; skipping PDF output." >&2
          OUTPUTS_SKIPPED+=("pdf (pandoc error)")
        fi
      else
        echo "WARNING: [pdf] pandoc not available; skipping PDF output." >&2
        OUTPUTS_SKIPPED+=("pdf (pandoc not installed)")
      fi
      ;;

    *)
      echo "NOTE: [${FORMAT}] not yet supported in v0.16.0; skipping."
      OUTPUTS_UNSUPPORTED+=("$FORMAT")
      ;;
  esac
done

# --- Write SUMMARY.md ---
SUMMARY_PATH="$OUTPUT_DIR/SUMMARY.md"
FINALIZED_AT="$(date -Iseconds)"

cat > "$SUMMARY_PATH" <<EOF
# Finalization Summary

## Project
${PROJECT_NAME}

## Version
${TARGET_VERSION}

## Finalized At
${FINALIZED_AT}

## Source Deliverable
${DELIVERABLE}

## Output Name
${OUTPUT_NAME}

## Outputs Produced
EOF

if [[ ${#OUTPUTS_PRODUCED[@]} -gt 0 ]]; then
  for out in "${OUTPUTS_PRODUCED[@]}"; do
    echo "- $out" >> "$SUMMARY_PATH"
  done
else
  echo "- (none)" >> "$SUMMARY_PATH"
fi

if [[ ${#OUTPUTS_SKIPPED[@]} -gt 0 ]]; then
  echo "" >> "$SUMMARY_PATH"
  echo "## Skipped (not available)" >> "$SUMMARY_PATH"
  for skip in "${OUTPUTS_SKIPPED[@]}"; do
    echo "- $skip" >> "$SUMMARY_PATH"
  done
fi

if [[ ${#OUTPUTS_UNSUPPORTED[@]} -gt 0 ]]; then
  echo "" >> "$SUMMARY_PATH"
  echo "## Not Supported" >> "$SUMMARY_PATH"
  for unsupported in "${OUTPUTS_UNSUPPORTED[@]}"; do
    echo "- ${unsupported}: not yet supported in v0.16.0" >> "$SUMMARY_PATH"
  done
fi

echo "" >> "$SUMMARY_PATH"
echo "## Status" >> "$SUMMARY_PATH"
echo "Finalized" >> "$SUMMARY_PATH"
# Note: a "## Published Artifacts" section is appended to this file after the
# publish step below completes (when artifacts are copied to projects/<proj>/artifacts/).

echo ""
echo "Wrote summary: $SUMMARY_PATH"

# --- Print summary ---
echo ""
echo "cm-finalize.sh complete."
echo "  Project:         $PROJECT_NAME"
echo "  Version:         $TARGET_VERSION"
echo "  Source:          $DELIVERABLE"
echo "  Output dir:      $OUTPUT_DIR"
if [[ ${#OUTPUTS_PRODUCED[@]} -gt 0 ]]; then
  for out in "${OUTPUTS_PRODUCED[@]}"; do
    echo "  Produced:        $out"
  done
fi
if [[ ${#OUTPUTS_SKIPPED[@]} -gt 0 ]]; then
  for skip in "${OUTPUTS_SKIPPED[@]}"; do
    echo "  Skipped:         $skip"
  done
fi
if [[ ${#OUTPUTS_UNSUPPORTED[@]} -gt 0 ]]; then
  for fmt in "${OUTPUTS_UNSUPPORTED[@]}"; do
    echo "  Not supported:   $fmt"
  done
fi
echo ""
echo "Last completed: $PROJECT_NAME $TARGET_VERSION"

# --- Publish final document(s) to projects/<kanban-project>/artifacts/ ---
#
# The publish step runs unconditionally when PGAI_PROJECT_NAME is set.
# TARGET_VERSION (the semver arg) is used as the versioned artifact name.
# PGAI_TARGET_VERSION env var is NOT required — the $2 arg provides the version.
# Publish path: projects/<project>/artifacts/v<semver>-<artifact_name>.<ext>
#
# - PGAI_PROJECT_NAME must be set to identify the kanban project.
# - ONLY the main deliverable is published (not SUMMARY or other intermediates).
# - Prior versions are NEVER overwritten (collision → error log, advisory).
# - PGAI_ARTIFACT_NAME overrides OUTPUT_NAME as the slug in the artifact filename.
#
# Optional summary variant:
#   When PGAI_WRITER_POLISH_TASK_ARTIFACTS is set, scan that directory for a file
#   whose name ends with "-summary.<ext>" and copy it as
#   v<semver>-<artifact_name>-summary.<ext>.
#
# Collision policy:
#   NEVER overwrite an existing artifacts/ entry. A collision is an error (logged
#   to stderr) but does not abort finalize (step is advisory). Versioned naming
#   must guarantee uniqueness across releases; if a collision occurs, the operator
#   must resolve it manually.

echo ""
echo "[publish] Checking whether to publish to projects artifacts library..."

_pub_artifact_name="${PGAI_ARTIFACT_NAME:-${OUTPUT_NAME}}"
_pub_kanban_project="${PGAI_PROJECT_NAME:-}"
_pub_writer_artifacts_dir="${PGAI_WRITER_POLISH_TASK_ARTIFACTS:-}"
_pub_target_version="$TARGET_VERSION"

if [[ -z "$_pub_kanban_project" ]]; then
  echo "  [publish] PGAI_PROJECT_NAME not set; skipping artifacts/ publish." >&2
  echo "  [publish] Set PGAI_PROJECT_NAME to enable versioned artifact publishing." >&2
else
  # Normalize target version: ensure it starts with 'v'.
  _pub_ver="${_pub_target_version}"
  [[ "${_pub_ver}" != v* ]] && _pub_ver="v${_pub_ver}"

  # Destination directory.
  _pub_dest_dir="${KANBAN_ROOT}/projects/${_pub_kanban_project}/artifacts"

  # Find the packaged markdown output (primary artifact).
  _pub_src_md="${OUTPUT_DIR}/${OUTPUT_NAME}.md"

  if [[ ! -f "$_pub_src_md" ]]; then
    echo "  [publish] WARNING: packaged output not found at ${_pub_src_md}; skipping publish." >&2
    echo "  [publish] The finalize step must produce a markdown output before publish can run." >&2
  else
    # Ensure the destination directory exists.
    if ! mkdir -p "$_pub_dest_dir" 2>/dev/null; then
      echo "  [publish] WARNING: could not create artifacts directory ${_pub_dest_dir}; skipping publish." >&2
    else
      # Determine destination filename.
      _pub_dest_filename="${_pub_ver}-${_pub_artifact_name}.md"
      _pub_dest_path="${_pub_dest_dir}/${_pub_dest_filename}"

      # Collision check — never overwrite.
      if [[ -e "$_pub_dest_path" ]]; then
        echo "  [publish] ERROR: destination already exists: ${_pub_dest_path}" >&2
        echo "  [publish] Versioned artifact filenames must be unique across releases." >&2
        echo "  [publish] Resolve the collision manually before re-running finalize." >&2
        # Report the error but do not abort finalize (step is advisory).
        _pub_collision=true
      else
        _pub_collision=false
        # Copy the packaged output to the artifacts library.
        if cp "$_pub_src_md" "$_pub_dest_path" 2>/dev/null; then
          echo "  [publish] Published: ${_pub_dest_path}"
          _PUB_ARTIFACTS_PUBLISHED+=("$_pub_dest_path")
        else
          echo "  [publish] WARNING: cp failed for ${_pub_src_md} -> ${_pub_dest_path}" >&2
        fi
      fi

      # --- Summary variant ---
      # When PGAI_WRITER_POLISH_TASK_ARTIFACTS is set, look for a *-summary.md
      # (or *-summary.<ext>) file in the WRITER polish task's artifacts/ directory.
      # This is the optional executive-summary companion produced by some WRITER tasks.
      if [[ "${_pub_collision}" == "false" && -n "$_pub_writer_artifacts_dir" ]]; then
        if [[ ! -d "$_pub_writer_artifacts_dir" ]]; then
          echo "  [publish] WARNING: PGAI_WRITER_POLISH_TASK_ARTIFACTS dir not found: ${_pub_writer_artifacts_dir}" >&2
        else
          # Find the first file matching *-summary.* (prefer .md).
          _pub_summary_src=""
          # Check for <artifact_name>-summary.md first (most specific match).
          if [[ -f "${_pub_writer_artifacts_dir}/${_pub_artifact_name}-summary.md" ]]; then
            _pub_summary_src="${_pub_writer_artifacts_dir}/${_pub_artifact_name}-summary.md"
          else
            # Fall back to any *-summary.md in the directory.
            while IFS= read -r -d '' _f; do
              _pub_summary_src="$_f"
              break
            done < <(find "$_pub_writer_artifacts_dir" -maxdepth 1 -type f \
                     -name '*-summary.md' -print0 2>/dev/null | sort -z)
          fi

          if [[ -n "$_pub_summary_src" ]]; then
            # Derive the extension (should be .md but be explicit).
            _pub_summary_ext="${_pub_summary_src##*.}"
            _pub_summary_dest="${_pub_dest_dir}/${_pub_ver}-${_pub_artifact_name}-summary.${_pub_summary_ext}"
            if [[ -e "$_pub_summary_dest" ]]; then
              echo "  [publish] WARNING: summary destination already exists: ${_pub_summary_dest}; skipping summary copy." >&2
            else
              if cp "$_pub_summary_src" "$_pub_summary_dest" 2>/dev/null; then
                echo "  [publish] Published summary: ${_pub_summary_dest}"
                _PUB_ARTIFACTS_PUBLISHED+=("$_pub_summary_dest")
              else
                echo "  [publish] WARNING: cp failed for summary ${_pub_summary_src} -> ${_pub_summary_dest}" >&2
              fi
            fi
          else
            echo "  [publish] No summary variant found in ${_pub_writer_artifacts_dir}; skipping summary publish."
          fi
        fi
      fi  # summary variant block

    fi  # mkdir succeeded

    # Append published artifacts to the SUMMARY.md (add a section after "## Status").
    if [[ ${#_PUB_ARTIFACTS_PUBLISHED[@]} -gt 0 ]]; then
      echo "" >> "$SUMMARY_PATH"
      echo "## Published Artifacts" >> "$SUMMARY_PATH"
      for _pa in "${_PUB_ARTIFACTS_PUBLISHED[@]}"; do
        echo "- $_pa" >> "$SUMMARY_PATH"
      done
    fi

  fi  # src_md exists

  # --- Record Last Released and clear Active RC in release-state.md ---
  #
  # Document-workflow projects have no git repo and therefore no git tag to
  # serve as the canonical "last released version" record.  After the artifact
  # is published above, write '## Last Released' to the project-scoped
  # release-state.md so that pp_last_released_version and the dashboard can
  # return the correct version for this project.
  #
  # Active RC is cleared to 'none' here (mirroring release.sh Step 15 which
  # clears Active RC after release). open-doc.sh sets Active RC at doc-open;
  # finalize clears it at finalize — symmetric lifecycle.
  #
  # Failure is advisory — we warn and continue; finalize exits 0 regardless.
  #
  # Format mirrors release.sh Step 15: the file is rewritten as a complete
  # here-doc so there are never partial writes or stray fields.
  _rs_path="${KANBAN_ROOT}/projects/${_pub_kanban_project}/release-state.md"
  echo ""
  echo "Recording Last Released and clearing Active RC in release-state.md..."
  if [[ ! -f "$_rs_path" ]]; then
    echo "  WARNING: release-state.md not found at ${_rs_path}; cannot record Last Released." >&2
    echo "  Create the file (projects/${_pub_kanban_project}/release-state.md) and re-run finalize to record the version." >&2
  else
    # Write the complete release-state.md with:
    #   Active RC cleared to 'none' (mirrors release.sh Step 15)
    #   Last Released set to the published semver
    # Using a here-doc (no post-hoc sed/regex) mirrors the canonical format
    # established by release.sh Step 15.
    if cat > "$_rs_path" <<EOF
# Release State

## Active RC
none

## RC Opened At
none

## RC Opened By Task
none

## Last Released
${_pub_ver}
EOF
    then
      echo "  release-state.md updated: Active RC -> none, Last Released -> ${_pub_ver}"
      echo "  Path: ${_rs_path}"
    else
      echo "  WARNING: failed to write release-state.md at ${_rs_path}" >&2
      echo "  Last Released will not be recorded; pp_last_released_version may return v0.0.0." >&2
    fi
  fi

fi  # publish target version and project resolved

# --- Flip originating document requirement ## Status: running -> done ---
#
# At finalize time, the document pipeline is complete: all WRITER tasks are DONE
# and the deliverable is packaged. The originating requirement file (in the
# kanban project's requirements/ directory) should now be marked 'done' so the
# dashboard shows it as complete and the discovery pipeline does not re-queue it.
#
# Resolution strategy:
#   1. PGAI_PROJECT_NAME env var (set by the wake script) identifies the kanban
#      project (e.g. pgai_agent_kanban_documentation).  If absent, we skip with
#      a warning — non-fatal.
#   2. Delegate entirely to promote_bundled_items.py --doc-mode, passing the
#      exact TARGET_VERSION from the $2 argument (never derived).
#      That function scans requirements/ for the file whose ## Target Version
#      matches the semver verbatim, then promotes its ## Status running -> done.
#      Exits non-zero if no match found; idempotent if already done.
#
# The step is advisory on failure: a warning is logged and finalize exits 0.
# (promote_bundled_items.py --doc-mode exits non-zero when no match; that exit
# is captured with || true so finalize itself always exits 0 after this step.)
echo ""
echo "Promoting document requirement ## Status from running to done..."

_finalize_kanban_project="${PGAI_PROJECT_NAME:-}"
_finalize_promote_script="$(dirname "${BASH_SOURCE[0]}")/promote_bundled_items.py"

if [[ -z "$_finalize_kanban_project" ]]; then
  echo "  WARNING: PGAI_PROJECT_NAME is not set; cannot locate requirements directory." >&2
  echo "  The requirement file's ## Status will remain at 'running'." >&2
  echo "  Set PGAI_PROJECT_NAME before invoking cm-finalize.sh to enable auto-promotion." >&2
elif [[ ! -f "$_finalize_promote_script" ]]; then
  echo "  WARNING: promote_bundled_items.py not found at $_finalize_promote_script" >&2
  echo "  The requirement file's ## Status will remain at 'running'." >&2
else
  _finalize_req_dir="${KANBAN_ROOT}/projects/${_finalize_kanban_project}/requirements"
  if [[ ! -d "$_finalize_req_dir" ]]; then
    echo "  WARNING: requirements directory not found: $_finalize_req_dir" >&2
    echo "  The requirement file's ## Status will remain at 'running'." >&2
  else
    # Delegate to promote_bundled_items.py --doc-mode, passing TARGET_VERSION
    # VERBATIM from the $2 argument.  No derivation, no transformation.
    # || true: advisory — a non-zero exit (no match) is logged by the script
    # itself; finalize continues and exits 0 regardless.
    python3 "$_finalize_promote_script" --doc-mode \
      "$_finalize_req_dir" "$TARGET_VERSION" || \
      echo "  WARNING: promote_bundled_items.py --doc-mode exited non-zero; requirement status may not have been updated." >&2

  fi  # req_dir exists
fi  # kanban_project and promote_script resolved
echo ""

# --- Write per-version release-state JSON with shipped outcome ---
#
# This block MUST run BEFORE the metrics aggregation below so that
# closed_at is present in the release-state JSON when metrics_aggregator.py
# reads it to compute wall_time_minutes.  Mirrors release.sh's close-then-aggregate
# ordering (Step 15 closes; Step 15b/19 aggregates).
#
# Only when a known kanban project name is set.
# Non-blocking: a failure logs a warning and finalize exits 0 regardless.
# Uses the same helper (write_rc_state.py ship) as release.sh, so there is
# exactly ONE implementation of the closed_at write.
if [[ -n "${_pub_kanban_project:-}" ]]; then
  _rc_state_dir="${KANBAN_ROOT}/projects/${_pub_kanban_project}/release-state"
  _rc_state_json="${_rc_state_dir}/${TARGET_VERSION}.json"
  _closed_at_utc="$(date -u +%Y-%m-%dT%H:%M:%SZ)"
  mkdir -p "$_rc_state_dir" 2>/dev/null || true
  python3 "$KANBAN_ROOT/pgai_agent_kanban/cm/write_rc_state.py" ship \
      "$_rc_state_json" "$TARGET_VERSION" "$_closed_at_utc" 2>&1 || \
    echo "[cm-finalize] WARNING: could not update per-version release-state JSON at $_rc_state_json" >&2
fi

# --- Metrics aggregation and CSV append (document workflow, semver runs only) ---
#
# Mirrors release.sh Step 19.  Runs AFTER the artifact is published, the
# requirement status is promoted, AND closed_at has been written to the
# per-version release-state JSON (written above).  This ordering ensures
# the aggregator reads both opened_at and closed_at and computes a real
# wall_time_minutes, which lands in the history.csv row the dashboard reads.
#
# Two sub-steps:
#   a. Invoke metrics_aggregator.py --workflow-type document to write the
#      per-version JSON rollup at:
#      projects/<kanban-project>/metrics/rc/<version>.json
#   b. Invoke metrics_csv_writer.py to append one row to the cumulative
#      history CSV at:
#      projects/<kanban-project>/metrics/history.csv
#
# Both sub-steps are NON-BLOCKING: any failure is captured to stderr with a
# [metrics] WARNING prefix and finalize continues to exit 0.  A metrics hiccup
# must never fail a finalize.
#
# Idempotency: metrics_aggregator.py rewrites the JSON deterministically from
# source tokens.json files (atomic via os.replace).  metrics_csv_writer.py
# skips duplicate rows inside an exclusive flock, so running finalize twice on
# the same document version yields at most one history.csv row.
#
# Arguments are passed explicitly: --project and --rc (as document version).
# Neither script is allowed to infer the project name from cwd.

if [[ -n "${_pub_kanban_project:-}" ]]; then
  echo "[metrics] Running document-workflow metrics aggregation for $TARGET_VERSION (non-blocking)..."

  _fin_metrics_agg_script="$KANBAN_ROOT/scripts/lib/metrics_aggregator.py"
  _fin_metrics_csv_script="$KANBAN_ROOT/scripts/lib/metrics_csv_writer.py"
  _fin_metrics_json=""  # set if step a succeeds; used by step b

  # Step a: per-version JSON rollup
  if [[ -f "$_fin_metrics_agg_script" ]]; then
    _fin_agg_rc=0
    (
      set +e
      python3 "$_fin_metrics_agg_script" \
        --project      "$_pub_kanban_project" \
        --rc           "$TARGET_VERSION" \
        --workflow-type document \
        --kanban-root  "$KANBAN_ROOT" \
        2>&1 | sed 's/^/[metrics] /'
      exit "${PIPESTATUS[0]}"
    ) || _fin_agg_rc=$?
    if [[ $_fin_agg_rc -ne 0 ]]; then
      echo "[metrics] WARNING: metrics_aggregator.py exited with code $_fin_agg_rc — per-version JSON rollup may be incomplete; finalize continues." >&2
    else
      # Derive the rollup path from the known output convention.
      _fin_metrics_json="$KANBAN_ROOT/projects/${_pub_kanban_project}/metrics/rc/${TARGET_VERSION}.json"
      if [[ ! -f "$_fin_metrics_json" ]]; then
        echo "[metrics] WARNING: expected rollup file not found after aggregation: $_fin_metrics_json" >&2
        _fin_metrics_json=""
      fi
    fi
  else
    echo "[metrics] WARNING: metrics_aggregator.py not found at $_fin_metrics_agg_script — skipping per-version JSON rollup." >&2
  fi

  # Step b: append row to cumulative history.csv
  if [[ -n "$_fin_metrics_json" && -f "$_fin_metrics_csv_script" ]]; then
    _fin_history_csv="$KANBAN_ROOT/projects/${_pub_kanban_project}/metrics/history.csv"
    _fin_csv_rc=0
    (
      set +e
      python3 "$_fin_metrics_csv_script" \
        --csv-path    "$_fin_history_csv" \
        --rollup-json "$_fin_metrics_json" \
        2>&1 | sed 's/^/[metrics] /'
      exit "${PIPESTATUS[0]}"
    ) || _fin_csv_rc=$?
    if [[ $_fin_csv_rc -ne 0 ]]; then
      echo "[metrics] WARNING: metrics_csv_writer.py exited with code $_fin_csv_rc — history.csv row may not have been appended; finalize continues." >&2
    fi
  elif [[ -z "$_fin_metrics_json" ]]; then
    echo "[metrics] WARNING: per-version JSON rollup was not written — skipping history.csv append." >&2
  else
    echo "[metrics] WARNING: metrics_csv_writer.py not found at $_fin_metrics_csv_script — skipping history.csv append." >&2
  fi

  echo ""
fi  # kanban project set

# --- Success-gated doc working-dir cleanup (non-blocking) ---
# Runs ONLY here on the explicit success path — the artifact has been published
# to projects/<project>/artifacts/ (confirmed above by _PUB_ARTIFACTS_PUBLISHED),
# requirement status promoted, and metrics written.
#
# MUST NOT be placed in cleanup_on_exit / trap EXIT (fires on failure too).
# Only cleans THIS version's working/ dir — never the output/ dir that holds the
# packaged artifact, and never another project's or version's temp.
# Uses pgai_temp_cleanup (not raw rm -rf) — it refuses paths outside the root.
#
# Guard: only run when:
#   - _PUB_ARTIFACTS_PUBLISHED is non-empty (publish step confirmed at least one
#     artifact landed in projects/<project>/artifacts/)
#   - WORKING_DIR exists and is a directory (nothing to do if already gone)
if [[ ${#_PUB_ARTIFACTS_PUBLISHED[@]} -gt 0 ]]; then
  echo "[finalize cleanup] Removing doc working/ dir after successful publish (non-blocking)..."
  if [[ -d "${WORKING_DIR:-}" ]]; then
    pgai_temp_cleanup "$WORKING_DIR" 2>&1 | sed 's/^/[finalize cleanup] /' || \
      echo "[finalize cleanup] WARNING: pgai_temp_cleanup failed for $WORKING_DIR — skipping." >&2
    echo "[finalize cleanup] Removed: $WORKING_DIR"
  else
    echo "[finalize cleanup] working/ dir does not exist (already cleaned or never created): ${WORKING_DIR:-<unset>}"
  fi
fi

exit 0
