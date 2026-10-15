#!/usr/bin/env bash
# team/scripts/lib/dashboard_palette.sh
#
# Single-source reader for the SEMANTIC dashboard color keys declared in
# team/config/default_palette.txt (the "%<key> <color-name> <comment>"
# section documented there). This is the bash counterpart of
# pgai_agent_kanban.lib.palette's load_semantic_palette() /
# resolve_semantic_color() -- both read the same on-disk file, which is
# what the extended parity test in
# team/tests/unit/test_palette_bash_python_parity.py proves structurally.
#
# Every renderer under team/scripts/dashboard/ and team/scripts/lib/dashboard/
# that needs a color for a MEANING ("HALT is active", "this task is
# blocked", "this is a framing label") calls dashboard_semantic_color or
# dashboard_semantic_tmux_fg below instead of hand-deciding "red" or
# hand-transcribing a color-name-to-escape-code table of its own. This
# file is deliberately NOT under team/scripts/dashboard/ or
# team/scripts/lib/dashboard/ -- the raw ANSI escape bytes this file (via
# ansi_code(), team/scripts/lib/script_common.sh) and its Python
# counterpart resolve to are meant to exist in exactly these two
# non-renderer library homes, never hand-copied into a renderer script.
#
# Public API
# ----------
#   dashboard_semantic_color <key>
#       Echoes the ANSI escape sequence (via ansi_code()) for the color
#       name the palette file declares for <key>. Echoes an empty string
#       in NO_COLOR mode (USE_COLOR != "true"), matching ansi_code()'s own
#       contract. Callers set USE_COLOR before calling, exactly as they do
#       for ansi_code() today.
#
#   dashboard_semantic_tmux_fg <key>
#       Echoes tmux-native "#[fg=<color-name>]" markup for <key> -- for
#       callers that emit tmux status-format strings (status-bottom.sh,
#       status-right.sh) rather than raw ANSI escapes. Never gates on
#       USE_COLOR itself; callers already branch on color mode and only
#       call this from the color-enabled branch (matching the existing
#       call-site pattern this replaces).
#
# Both functions return 1 and refuse dual-channel (see the CENSUS-ANCHOR
# comment at the refusal site below) when <key> is not declared in the
# palette file -- fail-loud by name, never a silent default/uncolored
# render. See "Missing-key refusal" below.
#
# Missing-key refusal
# --------------------
# BLOCK-WORTHY (team/SOP-core.md, "Gates and Refusals"): a dashboard
# renderer that asks for an undeclared semantic key and gets back an
# empty/default color instead of a loud failure would silently mis-render
# state an operator relies on to notice a HALT or a blocked task -- the
# exact silent-miscolor failure mode this single-source design exists to
# prevent. Refusing here is an invariant-by-design stop, not a quality
# finding.
#
# Sourcing
# --------
#   SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
#   # shellcheck source=lib/dashboard_palette.sh
#   source "${SCRIPT_DIR}/../lib/dashboard_palette.sh"
#
# Self-location: the palette file is resolved relative to THIS file's own
# BASH_SOURCE (team/scripts/lib/dashboard_palette.sh -> up two levels ->
# team/ -> config/default_palette.txt), never via
# PGAI_AGENT_KANBAN_ROOT_PATH -- each tree (dev checkout or live install)
# finds its own bundled copy (SOP-core.md, "Each tree finds itself").
#
# Dependencies: team/scripts/lib/script_common.sh (ansi_code()) and
# team/scripts/lib/error_contract.sh (pgai_emit_error_contract), both
# sourced below if not already loaded.

# Guard against double-sourcing.
[[ -n "${_DASHBOARD_PALETTE_LOADED:-}" ]] && return 0
_DASHBOARD_PALETTE_LOADED=1

_DASHBOARD_PALETTE_LIB_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

# shellcheck source=script_common.sh
source "${_DASHBOARD_PALETTE_LIB_DIR}/script_common.sh"
# shellcheck source=error_contract.sh
source "${_DASHBOARD_PALETTE_LIB_DIR}/error_contract.sh"

#: Path to the canonical palette definition file. Mirrors the resolution
#: in team/scripts/lib/projects_cfg.sh and pgai_agent_kanban.lib.palette:
#: team/scripts/lib/ -> up one level -> team/scripts/ -> up one level ->
#: team/ -> config/default_palette.txt. Resolved to an absolute,
#: dot-free path (cd -P, matching _DASHBOARD_PALETTE_LIB_DIR's own
#: resolution) so refusal messages name the same canonical path the
#: Python reader names -- both readers must agree byte-for-byte on the
#: file they name, not just on its resolved identity.
_DASHBOARD_PALETTE_FILE="$(cd "${_DASHBOARD_PALETTE_LIB_DIR}/../../config" 2>/dev/null && pwd -P)/default_palette.txt"

# Associative array cache: semantic key -> color name. Populated once by
# _dashboard_palette_load on first lookup; never re-read from disk after.
declare -A _DASHBOARD_PALETTE_MAP 2>/dev/null || true
_DASHBOARD_PALETTE_MAP_LOADED=""

# ---------------------------------------------------------------------------
# _dashboard_palette_load
# Internal: parses the palette file's semantic-key section (lines whose
# first whitespace-delimited token starts with '%') into
# _DASHBOARD_PALETTE_MAP. A no-op after the first successful call.
# Returns 1 (dual-channel refusal) if the file is missing -- this is a
# tree-integrity failure (the single-source palette file itself is gone),
# distinct from the per-key missing-key refusal in the lookup functions
# below.
# ---------------------------------------------------------------------------
_dashboard_palette_load() {
    [[ -n "$_DASHBOARD_PALETTE_MAP_LOADED" ]] && return 0

    if [[ ! -f "$_DASHBOARD_PALETTE_FILE" ]]; then
        pgai_emit_error_contract \
            "dashboard-palette.file-missing" \
            "the semantic dashboard palette file was not found at '${_DASHBOARD_PALETTE_FILE}'" \
            "restore team/config/default_palette.txt (dev tree) or config/default_palette.txt (live install) from version control"
        # BLOCK-WORTHY: every dashboard renderer's color meaning depends on
        # this file; a missing file means the tree cannot render any
        # semantic color at all, not merely one key -- verification unable
        # to run for the whole color system (team/SOP-core.md, "Gates and
        # Refusals").
        # CENSUS-ANCHOR: dashboard-palette-file-missing-01
        return 1
    fi

    local line key rest color_name
    while IFS= read -r line || [[ -n "$line" ]]; do
        [[ "$line" == %* ]] || continue
        rest="${line#%}"
        # shellcheck disable=SC2206 # word-splitting is the intended parse here
        set -- $rest
        key="$1"
        color_name="$2"
        [[ -z "$key" || -z "$color_name" ]] && continue
        _DASHBOARD_PALETTE_MAP["$key"]="$color_name"
    done < "$_DASHBOARD_PALETTE_FILE"

    _DASHBOARD_PALETTE_MAP_LOADED=1
    return 0
}

# ---------------------------------------------------------------------------
# _dashboard_palette_resolve_name <key> <out_var>
# Internal: resolves <key> to its declared color name via out_var (nameref).
# Returns 1 with a dual-channel refusal (naming the key and the palette
# file) when <key> is not declared.
# ---------------------------------------------------------------------------
_dashboard_palette_resolve_name() {
    local key="$1"
    local -n _dp_out="$2"

    _dashboard_palette_load || return 1

    if [[ -z "${_DASHBOARD_PALETTE_MAP[$key]+set}" ]]; then
        pgai_emit_error_contract \
            "dashboard-palette.key-missing" \
            "no semantic color key '${key}' is declared in ${_DASHBOARD_PALETTE_FILE}" \
            "add a %${key} entry to team/config/default_palette.txt naming a real color and a short description, following the same three-column shape as the existing '%halt_active red HALT sentinel active (full stop)' line, then re-render"
        # BLOCK-WORTHY: see the module header's "Missing-key refusal" note
        # -- silently returning an empty/default color here would render
        # the wrong (or no) color for state an operator relies on, which
        # is the exact failure mode this single-source design exists to
        # prevent (team/SOP-core.md, "Gates and Refusals").
        # CENSUS-ANCHOR: dashboard-palette-key-missing-01
        return 1
    fi

    _dp_out="${_DASHBOARD_PALETTE_MAP[$key]}"
    return 0
}

# ---------------------------------------------------------------------------
# dashboard_semantic_color <key>
# See "Public API" above.
# ---------------------------------------------------------------------------
dashboard_semantic_color() {
    local key="$1"
    local color_name=""
    _dashboard_palette_resolve_name "$key" color_name || return 1
    ansi_code "$color_name"
}

# ---------------------------------------------------------------------------
# dashboard_semantic_tmux_fg <key>
# See "Public API" above.
# ---------------------------------------------------------------------------
dashboard_semantic_tmux_fg() {
    local key="$1"
    local color_name=""
    _dashboard_palette_resolve_name "$key" color_name || return 1
    printf '#[fg=%s]' "$color_name"
}

# ---------------------------------------------------------------------------
# DASHBOARD_TMUX_ACTIVE_WINDOW_STYLE
#
# tmux-native markup for the active-window label in the session's
# status-format window list (team/scripts/dashboard/create.sh). This is a
# fixed tmux styling convention, not a task/entry color meaning keyed off
# the semantic palette -- it has no corresponding "%key" entry in
# team/config/default_palette.txt -- so it lives here as a plain constant
# rather than behind dashboard_semantic_tmux_fg(). Single-sourced so
# create.sh's two status-format templates (line 0: framework windows,
# line 1: drill windows) never hand-transcribe the literal a second time.
# ---------------------------------------------------------------------------
DASHBOARD_TMUX_ACTIVE_WINDOW_STYLE='#[fg=white bold]'
