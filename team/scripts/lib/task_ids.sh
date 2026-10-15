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
#   e.g. PM-YYYYMMDD-NNN-decompose-vX-Y-Z
#        CODER-YYYYMMDD-NNN-implement-feature
#
# Canonical shape — the one regex every id-accepting site validates against
# before an id is joined onto a directory path:
#   ^(CLAUDE-)?[A-Z]+-[0-9]{8}-[0-9]{3}-[a-z0-9-]+$
# The optional CLAUDE- participant prefix is the legacy form older task
# folders still carry; it is admitted so those ids keep parsing, and it is
# the only optional part. Anything else — '..', a path separator, a dot, an
# upper-case or underscored slug — is outside the shape. An id that passes
# cannot contain '/' or '..', so it cannot escape the directory it is joined
# onto. kanban_task_id_pattern prints the shape; kanban_task_id_is_canonical
# tests an id against it; the field splitter below (_kanban_parse_id_internal)
# only splits an id into its parts and is not a shape gate.
#
# ===========================================================================
# API SUMMARY
# ===========================================================================
#
# Source this file:
#   source "$(dirname "${BASH_SOURCE[0]}")/task_ids.sh"
#
# Emission:
#   SEQ=$(kanban_next_task_seq "$TASKS_DIR" "CODER" "YYYYMMDD")
#   ID=$(kanban_task_id "$TASKS_DIR" "CODER" "YYYYMMDD" "fix-something")
#   # → "CODER-YYYYMMDD-NNN-fix-something"
#
# Parsing:
#   kanban_parse_task_id "CODER-YYYYMMDD-NNN-implement-feature"
#   # Populates: _TASK_AGENT  _TASK_DATE  _TASK_SEQ  _TASK_SLUG  _TASK_PARTICIPANT
#
# Canonical-shape validation:
#   kanban_task_id_pattern                                  # → the shape regex, on stdout
#   kanban_task_id_is_canonical "CODER-YYYYMMDD-NNN-slug"   # exit 0 iff the id has that shape
#   kanban_task_id_role "CLAUDE-PM-YYYYMMDD-NNN-slug"       # → PM (legacy prefix dropped)
#   kanban_task_ids_in_files FILE [FILE ...]                # → every task id the files mention
#
# Individual field accessors:
#   kanban_task_agent       "CODER-YYYYMMDD-NNN-implement-feature"  # → CODER
#   kanban_task_date        "CODER-YYYYMMDD-NNN-implement-feature"  # → YYYYMMDD
#   kanban_task_seq         "CODER-YYYYMMDD-NNN-implement-feature"  # → NNN
#   kanban_task_slug        "CODER-YYYYMMDD-NNN-implement-feature"  # → implement-feature
#   kanban_task_participant "CODER-YYYYMMDD-NNN-implement-feature"  # → claude (default)

# ---------------------------------------------------------------------------
# kanban_task_id_pattern  → stdout
#
# Print the canonical task-id shape as a POSIX ERE that bash's [[ =~ ]] and
# Python's re module read identically, so a bash caller and a python3
# heredoc validate against the same literal instead of each carrying a
# copy. This function is the single source of that pattern; every validator
# calls it rather than restating the regex.
#
# Output: the pattern (stdout). Exit: always 0.
# ---------------------------------------------------------------------------
kanban_task_id_pattern() {
    printf '%s\n' '^(CLAUDE-)?[A-Z]+-[0-9]{8}-[0-9]{3}-[a-z0-9-]+$'
}


# ---------------------------------------------------------------------------
# kanban_task_id_is_canonical  ID
#
# Returns 0 when ID has the canonical shape kanban_task_id_pattern prints,
# 1 otherwise (including an empty ID). Prints nothing. A caller about to
# join ID onto a directory path checks this first: a passing id contains
# no '/', no '..', and no character outside [A-Za-z0-9-].
# ---------------------------------------------------------------------------
kanban_task_id_is_canonical() {
    local id="${1:-}"
    local pattern
    pattern="$(kanban_task_id_pattern)"
    [[ "$id" =~ $pattern ]]
}


# ---------------------------------------------------------------------------
# kanban_task_id_role  ID  → stdout
#
# Print the role (agent) part of a canonical task ID, with or without the
# legacy CLAUDE- prefix: PM-YYYYMMDD-NNN-slug and CLAUDE-PM-YYYYMMDD-NNN-slug
# both print PM. Returns 1, printing nothing, when ID is not canonical
# (kanban_task_id_is_canonical), so a directory scan can select task folders
# by role without restating the task-ID shape.
# ---------------------------------------------------------------------------
kanban_task_id_role() {
    local id="${1:-}"
    kanban_task_id_is_canonical "$id" || return 1
    local unprefixed="${id#CLAUDE-}"
    if [[ "$unprefixed" != "$id" ]] && kanban_task_id_is_canonical "$unprefixed"; then
        id="$unprefixed"
    fi
    printf '%s\n' "${id%%-*}"
}


# ---------------------------------------------------------------------------
# kanban_task_ids_in_files  FILE [FILE ...]  → stdout
#
# Print every task ID mentioned anywhere in the given files, one per line in
# the order found, with or without the legacy CLAUDE- prefix. The search is
# kanban_task_id_pattern with its ^/$ anchors dropped, so text such as a
# requirements bundle is read with the same shape every validator uses.
# Unreadable or missing files contribute nothing. Exit: always 0.
# ---------------------------------------------------------------------------
kanban_task_ids_in_files() {
    (( $# )) || return 0
    local pattern
    pattern="$(kanban_task_id_pattern)"
    pattern="${pattern#^}"
    pattern="${pattern%\$}"
    grep -ohE "$pattern" -- "$@" 2>/dev/null || true
}


# ---------------------------------------------------------------------------
# _kanban_parse_id_internal  ID
#
# Internal helper.  Splits a raw task ID string into its component parts.
# It is a field splitter, not the shape gate: an id is validated against
# kanban_task_id_pattern by the sites that turn ids into paths.
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
