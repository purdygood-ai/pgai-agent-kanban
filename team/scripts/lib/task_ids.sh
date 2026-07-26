#!/usr/bin/env bash
# team/scripts/lib/task_ids.sh
#
# Shared task-ID generation and parsing helpers for all kanban agents.
#
# All agents (PM, CODER, WRITER, TESTER, CM) must use the same
# sequence-number logic so that consecutive task creations on a given date
# produce distinct IDs.
#
# ===========================================================================
# TASK ID FORMAT
# ===========================================================================
#
# Format:
#   <AGENT>-YYYYMMDD-NNN-slug
#   e.g. PM-20260518-001-decompose-v0-26-1
# provenance-allowlist: remediation-pending — cited ID belongs in commit history; remove when rewriting comment
#        CODER-20260518-002-implement-feature
#
# ===========================================================================
# API SUMMARY
# ===========================================================================
#
# Source this file:
#   source "$(dirname "${BASH_SOURCE[0]}")/task_ids.sh"
#
# Emission:
#   SEQ=$(kanban_next_task_seq "$TASKS_DIR" "CODER" "20260518")
#   ID=$(kanban_task_id "$TASKS_DIR" "CODER" "20260518" "fix-something")
# provenance-allowlist: remediation-pending — cited ID belongs in commit history; remove when rewriting comment
#   # → "CODER-20260518-001-fix-something"
#
# Parsing:
# provenance-allowlist: remediation-pending — cited ID belongs in commit history; remove when rewriting comment
#   kanban_parse_task_id "CODER-20260518-002-implement-feature"
#   # Populates: _TASK_AGENT  _TASK_DATE  _TASK_SEQ  _TASK_SLUG  _TASK_PARTICIPANT
#
# Individual field accessors:
# provenance-allowlist: remediation-pending — cited ID belongs in commit history; remove when rewriting comment
#   kanban_task_agent       "CODER-20260518-002-implement-feature"  # → CODER
# provenance-allowlist: remediation-pending — cited ID belongs in commit history; remove when rewriting comment
#   kanban_task_date        "CODER-20260518-002-implement-feature"  # → 20260518
# provenance-allowlist: remediation-pending — cited ID belongs in commit history; remove when rewriting comment
#   kanban_task_seq         "CODER-20260518-002-implement-feature"  # → 002
# provenance-allowlist: remediation-pending — cited ID belongs in commit history; remove when rewriting comment
#   kanban_task_slug        "CODER-20260518-002-implement-feature"  # → implement-feature
# provenance-allowlist: remediation-pending — cited ID belongs in commit history; remove when rewriting comment
#   kanban_task_participant "CODER-20260518-002-implement-feature"  # → claude (default)

# ---------------------------------------------------------------------------
# _kanban_parse_id_internal  ID
#
# Internal helper.  Parses a raw task ID string into its component parts.
# Sets the following variables in the calling scope:
#
#   _TASK_PARTICIPANT  — always "" (no participant prefix in current format)
#   _TASK_AGENT        — e.g. "CODER"
#   _TASK_DATE         — e.g. "20260518"
#   _TASK_SEQ          — e.g. "002"
#   _TASK_SLUG         — e.g. "implement-feature"
#
# Returns 0 on successful parse, 1 if the ID matches the expected format.
# ---------------------------------------------------------------------------
_kanban_parse_id_internal() {
    local id="$1"

    _TASK_PARTICIPANT=""
    _TASK_AGENT=""
    _TASK_DATE=""
    _TASK_SEQ=""
    _TASK_SLUG=""

    # Format: AGENT-DATE-SEQ-slug
    if [[ "$id" =~ ^([A-Z]+)-([0-9]{8})-([0-9]+)-(.+)$ ]]; then
        _TASK_AGENT="${BASH_REMATCH[1]}"
        _TASK_DATE="${BASH_REMATCH[2]}"
        _TASK_SEQ="${BASH_REMATCH[3]}"
        _TASK_SLUG="${BASH_REMATCH[4]}"
        return 0
    fi

    return 1
}


# ---------------------------------------------------------------------------
# kanban_parse_task_id  ID
#
# Parse a task ID and expose its fields as variables in the calling scope:
#
#   _TASK_PARTICIPANT  — always "" in the current format
#   _TASK_AGENT        — the agent role (CODER, PM, WRITER, TESTER, CM, …)
#   _TASK_DATE         — 8-digit date stamp (YYYYMMDD)
#   _TASK_SEQ          — zero-padded sequence number (e.g. "003")
#   _TASK_SLUG         — kebab-case slug
#
# Arguments:
#   $1  ID  — the task ID string to parse
#
# Output: none (sets variables in caller's scope)
# Returns: 0 on successful parse, 1 on unrecognized format (fields are empty)
# ---------------------------------------------------------------------------
kanban_parse_task_id() {
    _kanban_parse_id_internal "$1"
}


# ---------------------------------------------------------------------------
# kanban_task_agent       ID → stdout
# kanban_task_date        ID → stdout
# kanban_task_seq         ID → stdout
# kanban_task_slug        ID → stdout
# kanban_task_participant ID → stdout
#
# Convenience accessors that parse an ID and print a single field.
# Each returns 0; prints "" on parse failure.
#
# kanban_task_participant always returns 'claude' (the default provider);
# the current task ID format carries no participant prefix.
# ---------------------------------------------------------------------------
kanban_task_agent() {
    local id="$1"
    _kanban_parse_id_internal "$id" || { echo ""; return 0; }
    echo "$_TASK_AGENT"
}

kanban_task_date() {
    local id="$1"
    _kanban_parse_id_internal "$id" || { echo ""; return 0; }
    echo "$_TASK_DATE"
}

kanban_task_seq() {
    local id="$1"
    _kanban_parse_id_internal "$id" || { echo ""; return 0; }
    echo "$_TASK_SEQ"
}

kanban_task_slug() {
    local id="$1"
    _kanban_parse_id_internal "$id" || { echo ""; return 0; }
    echo "$_TASK_SLUG"
}

kanban_task_participant() {
    local id="$1"
    _kanban_parse_id_internal "$id" || { echo "claude"; return 0; }
    echo "claude"
}


# ---------------------------------------------------------------------------
# kanban_next_task_seq  TASKS_DIR ROLE DATE_STAMP
#
# Find the next available sequence number for (ROLE, DATE_STAMP).
#
# Scans all subdirectories of TASKS_DIR whose names match:
#   ROLE-DATE-NNN-*
#
# Returns (max_found + 1) as a zero-padded three-digit string.
# Returns "001" when no matching folder exists.
#
# Arguments:
#   $1  TASKS_DIR   — absolute path to the tasks directory
#   $2  ROLE        — e.g. "PM", "CODER", "WRITER", "TESTER", "CM"
#   $3  DATE_STAMP  — e.g. "20260518"
#
# Output: three-digit zero-padded next sequence number (stdout)
# Exit:   always 0
# ---------------------------------------------------------------------------
kanban_next_task_seq() {
    local tasks_dir="$1"
    local role="$2"
    local date_stamp="$3"

    if [[ -z "$tasks_dir" || -z "$role" || -z "$date_stamp" ]]; then
        echo "task_ids.sh: kanban_next_task_seq: missing argument(s)" >&2
        printf '%03d' 1
        return 0
    fi

    if [[ ! -d "$tasks_dir" ]]; then
        # Tasks directory does not exist yet — first task gets 001.
        printf '%03d' 1
        return 0
    fi

    local max_seq=0
    local name seq_candidate

    # Prefix: ROLE-DATE_STAMP-
    # e.g. CODER-20260518-
    local prefix="${role}-${date_stamp}-"

    for d in "${tasks_dir}"/*/; do
        [[ -d "$d" ]] || continue
        name="$(basename "$d")"

        if [[ "$name" == ${prefix}* ]]; then
            local rest="${name#${prefix}}"
            seq_candidate="${rest%%-*}"
            if [[ "$seq_candidate" =~ ^[0-9]{1,6}$ ]]; then
                local num=$(( 10#$seq_candidate ))
                (( num > max_seq )) && max_seq=$num
            fi
        fi
    done

    printf '%03d' $(( max_seq + 1 ))
    return 0
}


# ---------------------------------------------------------------------------
# kanban_task_id  TASKS_DIR ROLE DATE_STAMP SLUG
#
# Compute the next available task ID for (ROLE, DATE_STAMP, SLUG).
# Calls kanban_next_task_seq internally.
#
# Emits: ROLE-DATE_STAMP-NNN-SLUG
#
# Arguments:
#   $1  TASKS_DIR   — absolute path to the tasks directory
#   $2  ROLE        — e.g. "PM", "CODER", "WRITER", "TESTER", "CM"
#   $3  DATE_STAMP  — e.g. "20260518"
#   $4  SLUG        — kebab-case slug, e.g. "decompose-v0-26-0-requirements"
#
# Output: full task ID string (stdout), e.g. CODER-20260518-003-fix-something
# Exit:   always 0
# ---------------------------------------------------------------------------
kanban_task_id() {
    local tasks_dir="$1"
    local role="$2"
    local date_stamp="$3"
    local slug="$4"

    local seq_pad
    seq_pad="$(kanban_next_task_seq "$tasks_dir" "$role" "$date_stamp")"
    echo "${role}-${date_stamp}-${seq_pad}-${slug}"
}
