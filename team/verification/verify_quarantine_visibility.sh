#!/usr/bin/env bash
# verify_quarantine_visibility.sh
#
# End-to-end verification of the quarantine visibility flow.
#
# What this script verifies:
#   (a) bash -n over every shell file touched in tickets 1-5
#   (b) python3 -m py_compile over every Python file touched
#   (c) A synthetic malformed fixture lands in projects/<sandbox>/rejected/
#       with its .reason sidecar when _disc_maybe_quarantine is called
#       at the threshold count
#   (d) scan_attention.py rejected-files shows the QUARANTINED FILES section
#   (e) kanban-status.sh shows Quarantined count >= 1
#   (f) After deleting the rejected file, attention pane shows no quarantine
#       section and kanban-status.sh shows Quarantined count = 0
#
# Usage:
#   bash verify_quarantine_visibility.sh [--dev-tree <path>] [--kanban-root <path>]
#
# Defaults:
#   --dev-tree     PGAI_DEV_TREE_PATH, else the project's [project]
#                  dev_tree_path (PGAI_PROJECT_NAME, else pgai-agent-kanban,
#                  read through project_paths.sh); with neither the run is
#                  refused and the remedy names --dev-tree
#   --kanban-root  $PGAI_AGENT_KANBAN_ROOT_PATH, else $HOME/pgai_agent_kanban
#
# Exit code:
#   0 — all checks passed
#   1 — one or more checks failed (details in output)
#
# A command whose output a step captures (the Step C quarantine helper, the
# Step D and Step F scan_attention.py runs) and that exits non-zero is itself
# recorded as a FAIL line carrying its exit status and output; the run still
# reaches CLEANUP and SUMMARY, which emits the checks_failed error contract.

set -euo pipefail

# ---------------------------------------------------------------------------
# Argument parsing
# ---------------------------------------------------------------------------
DEV_TREE=""
KANBAN_ROOT="${PGAI_AGENT_KANBAN_ROOT_PATH:-$HOME/pgai_agent_kanban}"

while [[ $# -gt 0 ]]; do
    case "$1" in
        --dev-tree)
            DEV_TREE="$2"; shift 2 ;;
        --kanban-root)
            KANBAN_ROOT="$2"; shift 2 ;;
        *)
            echo "Unknown argument: $1" >&2; exit 1 ;;
    esac
done

# Refusals speak the shared dual-channel error contract (error / message /
# remedy on stderr) through lib/verification_common.sh, which carries the
# stand-in used when the scripts lib tree is missing, and which resolves
# the dev tree a run without --dev-tree checks.
_VQV_SELF_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
_VQV_SELF="${_VQV_SELF_DIR}/$(basename "${BASH_SOURCE[0]}")"
# shellcheck source=lib/verification_common.sh
source "${_VQV_SELF_DIR}/lib/verification_common.sh"

if [[ -z "$DEV_TREE" ]]; then
    DEV_TREE="$(KANBAN_ROOT="$KANBAN_ROOT" verification_dev_tree)"
fi
if [[ -z "$DEV_TREE" ]]; then
    # BLOCK-WORTHY: with no dev tree there are no scripts or tests to check, so no check can run.
    pgai_emit_error_contract "verify_quarantine_visibility.dev_tree_unresolved" \
        "verify_quarantine_visibility: no dev tree to check -- --dev-tree was not given, PGAI_DEV_TREE_PATH is unset and project ${PGAI_PROJECT_NAME:-pgai-agent-kanban} has no dev_tree_path under ${KANBAN_ROOT}. Pass --dev-tree with the checkout to check (the remedy names an example path)." \
        "bash $(printf '%q' "$_VQV_SELF") --dev-tree ${VERIFICATION_EXAMPLE_DEV_TREE} --kanban-root $(printf '%q' "$KANBAN_ROOT")"
    # CENSUS-ANCHOR: verify-quarantine-visibility-03
    exit 1
fi

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
PASS_COUNT=0
FAIL_COUNT=0

pass() {
    echo "  PASS: $*"
    (( PASS_COUNT++ )) || true
}

fail() {
    echo "  FAIL: $*"
    (( FAIL_COUNT++ )) || true
}

# fail_capture <label> <exit-status> <output>
# Record a captured command that exited non-zero as one FAIL line naming it,
# its exit status and its output (newlines folded to " | "), so the failure
# is visible in the run's own output instead of aborting it.
fail_capture() {
    local label="$1" status="$2" output="$3"
    [[ -n "$output" ]] || output="(no output)"
    fail "${label} exited ${status}; output: ${output//$'\n'/ | }"
}

section() {
    echo ""
    echo "=== $* ==="
}

# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------
SCRIPTS_DIR="${DEV_TREE}/team/scripts"
DASHBOARD_DIR="${DEV_TREE}/team/pgai_agent_kanban/dashboard"
TESTS_DIR="${DEV_TREE}/team/tests"
# The scratch root is the temp root resolver's answer (override, then
# kanban.cfg [paths], then its fallback); temp.sh is located beside this
# script's own tree (team/verification -> team/scripts/lib, and the same
# relative layout in an install).
_VQV_LIB_DIR="${_VQV_SELF_DIR}/../scripts/lib"
_VQV_TEMP_SH="${_VQV_LIB_DIR}/temp.sh"
if [[ ! -r "$_VQV_TEMP_SH" ]]; then
    # In a git checkout the file is restored from HEAD; otherwise the dev
    # tree's own copy of this script runs against its own lib tree.
    if git -C "$_VQV_SELF_DIR" rev-parse --git-dir >/dev/null 2>&1; then
        _vqv_remedy="git -C '${_VQV_SELF_DIR}' checkout HEAD -- ../scripts/lib/temp.sh"
    else
        _vqv_remedy="bash '${DEV_TREE}/team/verification/verify_quarantine_visibility.sh' --dev-tree '${DEV_TREE}' --kanban-root '${KANBAN_ROOT}'"
    fi
    # BLOCK-WORTHY: with no temp root resolver there is no sandbox root, so no check can run.
    pgai_emit_error_contract "verify_quarantine_visibility.temp_resolver_missing" \
        "verify_quarantine_visibility: temp root resolver not found at ${_VQV_TEMP_SH}; no sandbox can be placed, so no check ran." \
        "${_vqv_remedy}"
    # CENSUS-ANCHOR: verify-quarantine-visibility-01
    exit 1
fi
# shellcheck source=../scripts/lib/temp.sh
source "$_VQV_TEMP_SH"
TEMP_ROOT="$(pgai_temp_dir)"
SANDBOX_ROOT="${TEMP_ROOT}/verify_quarantine_$$"

# ---------------------------------------------------------------------------
# (a) bash -n syntax checks on all modified shell files
# ---------------------------------------------------------------------------
# Strategy: stale entries removed (option a).
# test_dashboard_rejection_indicator.sh and test_discovery_rejection_quarantine.sh
# were present in an earlier baseline and removed during a subsequent refactor; they
# do not exist in the current tree.  Removing them here is preferable to a per-file
# existence guard (option b) because these files are confirmed absent — not
# transiently missing — so a runtime skip would produce permanent warning noise with
# no coverage value.  If either file is re-introduced in a future release, add it
# back to this list at that time.
section "STEP A: bash -n syntax checks"

SHELL_FILES=(
    "${SCRIPTS_DIR}/lib/discovery.sh"
    "${SCRIPTS_DIR}/kanban-status.sh"
    "${SCRIPTS_DIR}/dashboard/attention.sh"
    "${SCRIPTS_DIR}/lib/project_paths.sh"
)

for f in "${SHELL_FILES[@]}"; do
    if [[ ! -f "$f" ]]; then
        fail "file not found: $f"
        continue
    fi
    if bash -n "$f" 2>&1; then
        pass "bash -n: $(basename "$f")"
    else
        fail "bash -n: $(basename "$f")"
    fi
done

# ---------------------------------------------------------------------------
# (b) python3 -m py_compile on all modified Python files
# ---------------------------------------------------------------------------
# Strategy: stale entries removed (option a).
# test_pgai_agent_kanban_dashboard_render_costs.py and
# test_pgai_agent_kanban_dashboard_scan_attention.py do not exist in the current
# tree.  Same rationale as STEP A above: confirmed absent, not transiently missing.
# Re-add if the files are restored in a future release.
section "STEP B: python3 -m py_compile checks"

PYTHON_FILES=(
    "${DASHBOARD_DIR}/_rejected.py"
    "${DASHBOARD_DIR}/scan_attention.py"
)

for f in "${PYTHON_FILES[@]}"; do
    if [[ ! -f "$f" ]]; then
        fail "file not found: $f"
        continue
    fi
    if python3 -m py_compile "$f" 2>&1; then
        pass "py_compile: $(basename "$f")"
    else
        fail "py_compile: $(basename "$f")"
    fi
done

# ---------------------------------------------------------------------------
# (c) Synthetic rejection: file lands in rejected/ with .reason sidecar
# ---------------------------------------------------------------------------
section "STEP C: synthetic rejection via _disc_maybe_quarantine"

# Build a sandbox kanban root with a minimal project structure.
# Pass --project "$SANDBOX_PROJECT" explicitly to kanban-status.sh — no default
# project function exists; the flag is the only correct path.
SANDBOX_PROJECT="pgai-agent-kanban"
SANDBOX_KANBAN="${SANDBOX_ROOT}/kanban"
SANDBOX_PROJ="${SANDBOX_KANBAN}/projects/${SANDBOX_PROJECT}"
SANDBOX_PRIORITY="${SANDBOX_PROJ}/priority"
SANDBOX_STATE="${SANDBOX_PROJ}/.discovery-state"
SANDBOX_REJECTED="${SANDBOX_PROJ}/rejected"

mkdir -p "$SANDBOX_PRIORITY"
mkdir -p "$SANDBOX_STATE"

# Create a minimal project.cfg so pp_project_root resolves correctly.
mkdir -p "$SANDBOX_PROJ"
cat > "${SANDBOX_PROJ}/project.cfg" <<CFG
[project]
project_name = ${SANDBOX_PROJECT}
CFG

# Drop a malformed priority file (fewer than 4 digits in the NNNN segment, which
# triggers the canonical filename pattern rejection in _disc_find_unhandled_items).
FIXTURE_NAME="PRIORITY-99-malformed-short-id.md"
cat > "${SANDBOX_PRIORITY}/${FIXTURE_NAME}" <<MD
# Synthetic Malformed Priority File
## Status
open
## Summary
This file intentionally uses a short (2-digit) ID in its name
(expected: PRIORITY-NNNN-<slug>.md with 4+ digits) so it will be rejected by
the discovery pipeline's filename validator.
MD

echo "  Created fixture: ${SANDBOX_PRIORITY}/${FIXTURE_NAME}"

# Source the required libraries and call _disc_maybe_quarantine directly.
# We set the threshold to 1 so a single call quarantines immediately,
# avoiding the need to call it three times.
# Write the subshell script to a temp file to avoid heredoc quoting issues.
_SUBSHELL_SCRIPT="${TEMP_ROOT}/verify_quarantine_subshell_$$.sh"
mkdir -p "$TEMP_ROOT"
cat > "$_SUBSHELL_SCRIPT" <<SUBSHELL
#!/usr/bin/env bash
set -euo pipefail
KANBAN_ROOT="${SANDBOX_KANBAN}"
export KANBAN_ROOT
export PGAI_PROJECT_NAME="${SANDBOX_PROJECT}"
export PGAI_DISCOVERY_REJECT_THRESHOLD=1

source "${SCRIPTS_DIR}/lib/ini_parser.sh"
source "${SCRIPTS_DIR}/lib/project_paths.sh"
source "${SCRIPTS_DIR}/lib/discovery.sh"

_disc_maybe_quarantine \\
    "${SANDBOX_STATE}" \\
    "${SANDBOX_PRIORITY}" \\
    "${FIXTURE_NAME}" \\
    "malformed filename (expected pattern: PRIORITY-NNNN-slug.md)" \\
    "${SANDBOX_PROJECT}"
echo "quarantine_call_exit=0"
SUBSHELL

# A helper that exits non-zero (a library it sources is missing, the call
# itself fails) is recorded as a FAIL; the checks below still run and report
# what did or did not land in rejected/.
QUARANTINE_STATUS=0
QUARANTINE_RESULT="$(bash "$_SUBSHELL_SCRIPT" 2>&1)" || QUARANTINE_STATUS=$?
rm -f "$_SUBSHELL_SCRIPT"
echo "  _disc_maybe_quarantine output: $QUARANTINE_RESULT"
if [[ "$QUARANTINE_STATUS" -ne 0 ]]; then
    fail_capture "STEP C synthetic quarantine helper (_disc_maybe_quarantine)" \
        "$QUARANTINE_STATUS" "$QUARANTINE_RESULT"
fi

# Verify the quarantined file landed in rejected/
if [[ -f "${SANDBOX_REJECTED}/${FIXTURE_NAME}" ]]; then
    pass "quarantined file present: rejected/${FIXTURE_NAME}"
else
    fail "quarantined file NOT found at: ${SANDBOX_REJECTED}/${FIXTURE_NAME}"
fi

# Verify the .reason sidecar was written
SIDECAR="${SANDBOX_REJECTED}/${FIXTURE_NAME}.reason"
if [[ -f "$SIDECAR" ]]; then
    pass "sidecar present: rejected/${FIXTURE_NAME}.reason"
else
    fail "sidecar NOT found: ${SANDBOX_REJECTED}/${FIXTURE_NAME}.reason"
fi

echo "  Sidecar contents:"
if [[ -f "$SIDECAR" ]]; then
    while IFS= read -r line; do
        echo "    $line"
    done < "$SIDECAR"
fi

# Verify key sidecar fields
if [[ -f "$SIDECAR" ]]; then
    # A field missing from the sidecar reads as empty and fails its check
    # below rather than aborting the run (grep exits 1 under pipefail).
    ORIG_TYPE="$(grep '^original_type=' "$SIDECAR" | cut -d= -f2 || true)"
    REASON_FIELD="$(grep '^reason=' "$SIDECAR" | cut -d= -f2- || true)"
    RETRY_FIELD="$(grep '^retry_count=' "$SIDECAR" | cut -d= -f2 || true)"

    if [[ "$ORIG_TYPE" == "priority" ]]; then
        pass "sidecar.original_type = priority"
    else
        fail "sidecar.original_type: expected 'priority', got '${ORIG_TYPE}'"
    fi

    if [[ -n "$REASON_FIELD" ]]; then
        pass "sidecar.reason is set: ${REASON_FIELD:0:60}"
    else
        fail "sidecar.reason is empty"
    fi

    if [[ "$RETRY_FIELD" =~ ^[0-9]+$ ]]; then
        pass "sidecar.retry_count is numeric: ${RETRY_FIELD}"
    else
        fail "sidecar.retry_count not numeric: '${RETRY_FIELD}'"
    fi
fi

# The original file must no longer exist in priority/
if [[ ! -f "${SANDBOX_PRIORITY}/${FIXTURE_NAME}" ]]; then
    pass "source file removed from priority/ after quarantine"
else
    fail "source file still present in priority/ (expected it to be moved)"
fi

# ---------------------------------------------------------------------------
# (d) scan_attention.py shows QUARANTINED FILES section
# ---------------------------------------------------------------------------
section "STEP D: scan_attention.py rejected-files shows QUARANTINED FILES"

ATTENTION_STATUS=0
ATTENTION_OUTPUT="$(python3 "${DASHBOARD_DIR}/scan_attention.py" \
    rejected-files "${SANDBOX_KANBAN}" --no-color 2>&1)" || ATTENTION_STATUS=$?

echo "  scan_attention.py output:"
while IFS= read -r line; do
    echo "    ${line}"
done <<< "$ATTENTION_OUTPUT"

# A scan that exited non-zero rendered no pane, so its output is not graded.
if [[ "$ATTENTION_STATUS" -ne 0 ]]; then
    fail_capture "STEP D scan_attention.py rejected-files" \
        "$ATTENTION_STATUS" "$ATTENTION_OUTPUT"
else
    if grep -q "QUARANTINED FILES" <<< "$ATTENTION_OUTPUT"; then
        pass "attention pane renders QUARANTINED FILES header"
    else
        fail "attention pane missing QUARANTINED FILES header"
    fi

    if grep -q "$FIXTURE_NAME" <<< "$ATTENTION_OUTPUT"; then
        pass "attention pane lists fixture filename: ${FIXTURE_NAME}"
    else
        fail "attention pane missing fixture filename: ${FIXTURE_NAME}"
    fi

    if grep -q "priority" <<< "$ATTENTION_OUTPUT"; then
        pass "attention pane shows original_type: priority"
    else
        fail "attention pane missing original_type field"
    fi
fi

# ---------------------------------------------------------------------------
# (e) kanban-status.sh shows Quarantined count >= 1
# ---------------------------------------------------------------------------
section "STEP E: kanban-status.sh shows Quarantined count >= 1"

STATUS_OUTPUT="$(KANBAN_ROOT="${SANDBOX_KANBAN}" \
    PGAI_PROJECT_NAME="${SANDBOX_PROJECT}" \
    bash "${SCRIPTS_DIR}/kanban-status.sh" \
        --kanban-root "${SANDBOX_KANBAN}" \
        --project "${SANDBOX_PROJECT}" \
        --no-color 2>&1 || true)"

echo "  kanban-status.sh output (Quarantined line):"
echo "$STATUS_OUTPUT" | grep -i "Quarantined" | while IFS= read -r line; do
    echo "    $line"
done || true

QUARAN_LINE="$(echo "$STATUS_OUTPUT" | grep -i "Quarantined" | head -1 || true)"
if [[ -n "$QUARAN_LINE" ]]; then
    QUARAN_VAL="$(echo "$QUARAN_LINE" | sed 's/[^0-9]//g')"
    if [[ -n "$QUARAN_VAL" ]] && [[ "$QUARAN_VAL" -ge 1 ]]; then
        pass "kanban-status.sh Quarantined count = ${QUARAN_VAL} (>= 1)"
    else
        fail "kanban-status.sh Quarantined line found but count is 0 or unparseable: '${QUARAN_LINE}'"
    fi
else
    fail "kanban-status.sh did not emit a Quarantined line"
fi

# ---------------------------------------------------------------------------
# (f) After deletion: no quarantine section, count = 0
# ---------------------------------------------------------------------------
section "STEP F: after deletion, attention pane silent and count = 0"

# Remove the quarantined file and its sidecar
rm -f "${SANDBOX_REJECTED}/${FIXTURE_NAME}"
rm -f "${SANDBOX_REJECTED}/${FIXTURE_NAME}.reason"
echo "  Removed: ${SANDBOX_REJECTED}/${FIXTURE_NAME}"
echo "  Removed: ${SANDBOX_REJECTED}/${FIXTURE_NAME}.reason"

# Rerun scan_attention.py — should emit nothing (section suppressed when empty)
ATTENTION_EMPTY_STATUS=0
ATTENTION_EMPTY="$(python3 "${DASHBOARD_DIR}/scan_attention.py" \
    rejected-files "${SANDBOX_KANBAN}" --no-color 2>&1)" || ATTENTION_EMPTY_STATUS=$?

echo "  scan_attention.py output after deletion (should be blank):"
# A scan that exited non-zero proves nothing about the pane being silent.
if [[ "$ATTENTION_EMPTY_STATUS" -ne 0 ]]; then
    fail_capture "STEP F scan_attention.py rejected-files after deletion" \
        "$ATTENTION_EMPTY_STATUS" "$ATTENTION_EMPTY"
elif [[ -z "$ATTENTION_EMPTY" ]]; then
    echo "    (empty — correct)"
    pass "attention pane suppressed when no quarantined files"
else
    echo "    ${ATTENTION_EMPTY}"
    if grep -q "QUARANTINED FILES" <<< "$ATTENTION_EMPTY"; then
        fail "attention pane still shows QUARANTINED FILES header after deletion"
    else
        pass "attention pane does not show QUARANTINED FILES header after deletion"
    fi
fi

# Rerun kanban-status.sh — Quarantined should be 0
STATUS_EMPTY="$(KANBAN_ROOT="${SANDBOX_KANBAN}" \
    PGAI_PROJECT_NAME="${SANDBOX_PROJECT}" \
    bash "${SCRIPTS_DIR}/kanban-status.sh" \
        --kanban-root "${SANDBOX_KANBAN}" \
        --project "${SANDBOX_PROJECT}" \
        --no-color 2>&1 || true)"

echo "  kanban-status.sh output after deletion (Quarantined line):"
echo "$STATUS_EMPTY" | grep -i "Quarantined" | while IFS= read -r line; do
    echo "    $line"
done || true

QUARAN_LINE_2="$(echo "$STATUS_EMPTY" | grep -i "Quarantined" | head -1 || true)"
if [[ -n "$QUARAN_LINE_2" ]]; then
    QUARAN_VAL_2="$(echo "$QUARAN_LINE_2" | sed 's/[^0-9]//g')"
    if [[ "$QUARAN_VAL_2" == "0" ]]; then
        pass "kanban-status.sh Quarantined count = 0 after deletion"
    else
        fail "kanban-status.sh Quarantined count = ${QUARAN_VAL_2}, expected 0 after deletion"
    fi
else
    # Quarantined line is always emitted per the implementation spec (even when 0)
    fail "kanban-status.sh did not emit a Quarantined line after deletion"
fi

# ---------------------------------------------------------------------------
# Cleanup
# ---------------------------------------------------------------------------
section "CLEANUP"
rm -rf "$SANDBOX_ROOT"
echo "  Removed sandbox: ${SANDBOX_ROOT}"

# ---------------------------------------------------------------------------
# Summary
# ---------------------------------------------------------------------------
section "SUMMARY"
echo "  Passed: ${PASS_COUNT}"
echo "  Failed: ${FAIL_COUNT}"
echo ""

if [[ "$FAIL_COUNT" -eq 0 ]]; then
    echo "ALL CHECKS PASSED"
    exit 0
else
    echo "SOME CHECKS FAILED — see details above"
    # BLOCK-WORTHY: a failed check means the quarantine visibility flow is broken; passing would hide it.
    pgai_emit_error_contract "verify_quarantine_visibility.checks_failed" \
        "verify_quarantine_visibility: ${FAIL_COUNT} check(s) failed, ${PASS_COUNT} passed; fix what each FAIL line above names, then re-run." \
        "bash '${_VQV_SELF}' --dev-tree '${DEV_TREE}' --kanban-root '${KANBAN_ROOT}'"
    # CENSUS-ANCHOR: verify-quarantine-visibility-02
    exit 1
fi
