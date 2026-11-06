#!/usr/bin/env bash
# team/scripts/lib/projects.sh
#
# Shared helpers for managing the projects.cfg registry — the central list
# of projects this kanban installation knows about.
#
# The registry verbs themselves (create-project, add-project, remove-project)
# run in Python, pgai_agent_kanban/ops/projects.py, whose registry helpers
# read and write projects.cfg by the same line rules as the functions here,
# so a registry either side wrote reads back the same on the other. A change
# to a rule here needs the same change there.
#
# Supported formats
# -----------------
#
# INI format (current, preferred):
#
#   # Comments start with '#'. Blank lines are ignored.
#   # One [project:NAME] section per project. Key=value fields follow.
#
#   [project:pgai-chomp-man]
#   priority=1
#   description=Release-workflow demo project (see demos/chomp-man-demo/)
#   enabled=true
#   dashboard_color=#378ADD
#   dashboard_max_rows=20
#
#   # Minimal: only priority set; color comes from the palette by
#   # registration order, all other fields use defaults.
#   [project:pgai-three-bears]
#   priority=2
#
# Colon-delimited format (legacy, deprecated):
#
#   # Comments start with '#'. Blank lines are ignored.
#   # Each non-comment line: <project_name>:<priority>[:<display_color>]
#   # Lower priority number = higher priority (sorted ascending)
#   # display_color is optional; format is #RRGGBB (HTML hex). When absent,
#   # a deterministic palette fallback is used at read time.
#
#   pgai-chomp-man:1:#378ADD
#   pgai-three-bears:2
#   marketing-site:3:#D85A30
#
# Format detection
# ----------------
# The library inspects the first non-comment, non-blank line of projects.cfg
# (a comment line starts with '#' or ';', after optional whitespace):
#   - Starts with '[' after optional whitespace → INI mode
#   - Otherwise → colon-legacy mode (deprecation warning emitted once)
# pgai_agent_kanban.lib.ini.projects_registry reads the same file as INI by
# the same rule, so both readers list the same projects.
#
# INI parsing notes
# -----------------
# - Section header: [project:NAME] alone on its line, NAME matching the
#   project-name rule PROJECTS_CFG_NAME_RE (lib/projects_cfg.sh), matched
#   after leading and trailing whitespace is dropped, so an indented header,
#   a header with trailing spaces and a file with CRLF line endings read as
#   they do for the Python registry
#   (pgai_agent_kanban.lib.ini.projects_registry); a header followed by a
#   trailing comment ("[project:alpha] # first") is no project section. The
#   awk readers share the rule through PROJECTS_CFG_AWK_HEADER
#   (lib/projects_cfg.sh).
#   A [project:...] section whose name does not match that rule
#   ([project:bad.name], [project:bad name], [project:My_App]) registers no
#   project and ends the project section before it; no reader credits its keys to any
#   project, and projects_cfg_unaccepted_names lists its name so a caller
#   can report it.
#   Other section patterns (wrong brackets, typos) emit a line-pointing error.
# - Field key regex: ^[a-z_]+[[:space:]]*= (lowercase letters, underscores, optional whitespace around =)
# - Duplicate [project:NAME] sections: last-wins per key (a later section
#   overwrites the keys it sets and leaves the others). This matches the
#   behavior most operators expect from INI-style files and avoids hard
#   errors during hand-editing. projects_cfg_list lists the name once, at
#   its first section's position, sorted by the last priority any of its
#   sections sets -- as ini.projects_registry reads it. The per-field
#   readers (projects_cfg_priority, projects_cfg_field and so
#   projects_cfg_enabled, projects_cfg_max_rows, projects_cfg_color) and the
#   bulk loader _projects_cfg_parse_ini read each key from the last section
#   that sets it, and a key only an earlier section sets keeps that value --
#   the same per-key merge as ini.projects_registry.
#   projects_cfg_registration prints the first section only.
# - Empty [project:NAME] sections (no fields) are valid. All fields default
#   to their code defaults when not present.
#
# Public API
# ----------
#   projects_cfg_path                       — echo path to projects.cfg
#   projects_cfg_ensure                     — create projects.cfg with header if missing
#   projects_cfg_list [--registration-order]
#                                           — echo project names in priority order, one per line
#                                             (--registration-order: in projects.cfg file order).
#                                             Exits non-zero with a clear error when projects.cfg
#                                             is missing or empty.
#   projects_cfg_unaccepted_names           — echo, once each in file order, the name of every
#                                             [project:...] section whose name the registry does
#                                             not accept (projects_cfg_list never lists it).
#                                             Prints nothing and returns 0 when there is none.
#   projects_cfg_active                     — like projects_cfg_list but excludes halted projects
#                                             (those with a per-project HALT file). Requires
#                                             lib/project_paths.sh sourced first.
#   projects_cfg_has <name>                 — return 0 if registered, 1 otherwise
#   projects_cfg_registration <name>        — echo <name>'s INI section or colon-legacy line verbatim
#   projects_cfg_add <name> [priority]      — register a project; idempotent
#   projects_cfg_remove <name>              — unregister; idempotent
#   projects_cfg_priority <name>            — echo the priority for <name>, or empty if unregistered
#   projects_cfg_format <path>              — echo 'ini' or 'colon-legacy' for the given file
#   projects_cfg_max_rows <name>            — echo dashboard_max_rows for <name> (default 20)
#   projects_cfg_field <name> <field>       — echo any parsed field value for <name>
#   projects_resolve_release_hook_path <name> <phase>
#                                           — echo the resolved hook path for the given phase
#                                             (pre-squash | pre-tag | post-tag) using three-tier
#                                             precedence: (a) project.cfg [hooks] key (cfg),
#                                             (b) kanban-side projects/<name>/hooks/ (kanban-side),
#                                             (c) <dev_tree_path>/.pgai/hooks/ (in-repo).
#                                             Echoes empty string when no hook found at any tier.
#                                             Sets global _PGAI_HOOK_LAST_SOURCE to the winning
#                                             source label (cfg|kanban-side|in-repo) or "" when
#                                             no hook is found. Callers that need the source label
#                                             may read _PGAI_HOOK_LAST_SOURCE immediately after.
#
# Color helpers (from lib/projects_cfg.sh, sourced below)
# --------------------------------------------------------
#   PGAI_DEFAULT_PALETTE                    — read-only array of 8 default hex colors
#   projects_cfg_color <name>               — echo display_color for <name> (explicit or palette fallback)
#   projects_cfg_next_color                 — echo the lowest-index palette entry not yet used
#   projects_cfg_next_priority              — echo max(existing priorities) + 1, or 1 when empty
#
# All functions return 0 on success and a non-zero status on error. Errors
# are printed to stderr.
#
# Source order
# ------------
# This library MUST be sourced AFTER lib/project_paths.sh in the caller, because:
#   - projects_cfg_active calls pp_project_halted to filter halted projects.
#   - KANBAN_ROOT or PGAI_AGENT_KANBAN_ROOT_PATH must be set.

# Source the color/priority helpers from the sibling file.
# __dir resolves to the directory of THIS file regardless of how it is sourced.
__projects_sh_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=./projects_cfg.sh
source "${__projects_sh_dir}/projects_cfg.sh"
# shellcheck source=./temp.sh
if ! declare -F pgai_temp_dir >/dev/null 2>&1; then
    source "${__projects_sh_dir}/temp.sh"
fi
unset __projects_sh_dir

# ---------------------------------------------------------------------------
# _projects_cfg_deprecation_warned
# Guard variable — set to 1 once the deprecation warning has been emitted.
# Reset between process invocations; persists across calls within a single
# shell process (source-once semantics).
# ---------------------------------------------------------------------------
_projects_cfg_deprecation_warned=${_projects_cfg_deprecation_warned:-0}

# ---------------------------------------------------------------------------
# _projects_cfg_emit_deprecation_warning
# Emit the colon-format deprecation warning exactly once per process.
# Subsequent calls within the same process are no-ops.
# ---------------------------------------------------------------------------
_projects_cfg_emit_deprecation_warning() {
    if [[ "$_projects_cfg_deprecation_warned" -eq 0 ]]; then
        echo "[projects.cfg] WARNING: colon-format detected. This format is deprecated." >&2
        echo "[projects.cfg] WARNING: Run 'scripts/migrate/projects-cfg.sh' to convert to INI format." >&2
        echo "[projects.cfg] WARNING: Old format will be removed in a future major release." >&2
        _projects_cfg_deprecation_warned=1
    fi
}

# ---------------------------------------------------------------------------
# projects_cfg_path — echo absolute path to projects.cfg
# ---------------------------------------------------------------------------
projects_cfg_path() {
    local root
    root="${KANBAN_ROOT:-${PGAI_AGENT_KANBAN_ROOT_PATH:-$HOME/pgai_agent_kanban}}"
    echo "${root}/projects.cfg"
}

# ---------------------------------------------------------------------------
# projects_cfg_ensure — create projects.cfg with default header if it doesn't
# exist. Idempotent (no-op when file already present).
# ---------------------------------------------------------------------------
projects_cfg_ensure() {
    local cfg
    cfg="$(projects_cfg_path)"
    if [[ ! -f "$cfg" ]]; then
        mkdir -p "$(dirname "$cfg")"
        cat > "$cfg" <<'EOF'
# projects.cfg — Registry of projects this kanban installation manages.
#
# Format: INI-style with one [project:NAME] section per project.
#
# Fields per section:
#   priority             — integer; lower = higher priority for wake-script iteration (required)
#   description          — free-text description (optional)
#   enabled              — true|false; default true (optional)
#   dashboard_color      — hex color #RRGGBB for dashboard tag (optional)
#   dashboard_max_rows   — integer rows shown per visibility column (optional; default 20)
#                          Valid range: 5–100. Values outside that range are clamped with a warning.
#
# Future dashboard_* fields can be added without format changes.
#
# Edit by hand or via operator scripts (create-project.sh, add-project.sh, remove-project.sh).
# Restart the tmux dashboard session after edits.
#
# Example:
#
#   [project:pgai-chomp-man]
#   priority=1
#   description=Release-workflow demo project (see demos/chomp-man-demo/)
#   enabled=true
#   dashboard_color=#378ADD
#   dashboard_max_rows=20
#
#   # Minimal: only priority set; color comes from the palette by
#   # registration order, all other fields use defaults.
#   [project:pgai-three-bears]
#   priority=2
EOF
    fi
    return 0
}

# ---------------------------------------------------------------------------
# projects_cfg_format <path>
# Echo 'ini' if the file's first non-comment, non-blank line starts with '['
# (after optional whitespace). Echo 'colon-legacy' otherwise. A comment line
# starts with '#' or ';' after optional whitespace.
# Returns 0 on success. Returns 1 if the path argument is missing.
#
# Special case: when the file contains only comments and blank lines (no data
# lines), the function checks for the canonical INI sentinel comment written
# by projects_cfg_ensure:
#   "# Format: INI-style"
# If that line is present, the file is treated as INI (it was freshly created
# by projects_cfg_ensure and has no project sections yet). This prevents the
# first create-project.sh invocation on an empty registry from being written
# in colon-legacy format.
#
# This function does NOT parse the file beyond format detection.
# ---------------------------------------------------------------------------
projects_cfg_format() {
    local path="${1:-}"
    if [[ -z "$path" ]]; then
        echo "projects_cfg_format: path argument is required" >&2
        return 1
    fi

    if [[ ! -f "$path" ]]; then
        # Missing file → treat as colon-legacy (empty/missing = legacy default)
        echo "colon-legacy"
        return 0
    fi

    local fmt
    fmt="$(awk '
        /^[[:space:]]*[#;]/ { next }
        /^[[:space:]]*$/ { next }
        {
            if ($0 ~ /^[[:space:]]*\[/) {
                print "ini"
            } else {
                print "colon-legacy"
            }
            found = 1
            exit
        }
        END {
            # File has only comments/blanks — no data lines found.
            # Emit a special token so the shell can apply the sentinel check.
            if (!found) print "no-data"
        }
    ' "$path")"

    # When the file has only comments/blanks (no data lines), check whether the
    # canonical INI sentinel comment is present ("# Format: INI-style ...").
    # projects_cfg_ensure writes this line when it creates a fresh projects.cfg.
    # Its presence means this is an INI registry that has no project sections yet
    # (the empty state). Without this check, the first projects_cfg_add call would
    # write a colon-format line instead of an INI section.
    # The sentinel check only fires for "no-data" files — if an actual colon-format
    # data line was found, fmt is already "colon-legacy" and we never reach here.
    if [[ "$fmt" == "no-data" ]]; then
        if grep -qF '# Format: INI-style' "$path"; then
            fmt="ini"
        else
            fmt="colon-legacy"
        fi
    fi

    echo "${fmt:-colon-legacy}"
    return 0
}

# ---------------------------------------------------------------------------
# _projects_cfg_parse_ini <cfg_path>
# Internal helper. Parse an INI-format projects.cfg and populate the
# associative arrays below (declared in the calling scope):
#   projects_priority[name]    — integer priority
#   projects_color[name]       — #RRGGBB color string (may be empty)
#   projects_max_rows[name]    — dashboard_max_rows integer
#   projects_enabled[name]     — "true" or "false"
#   projects_description[name] — free-text description
#
# Callers must declare these as associative arrays (declare -A) before calling.
#
# Duplicate [project:NAME] sections merge per key: each array holds the value
# from the last section that sets the matching key, a key only an earlier
# section sets keeps that value, and a key no section sets keeps its default
# (enabled "true", the others empty) -- the same merge as the per-field
# readers and ini.projects_registry.
#
# Malformed section headers (lines starting with '[' that are not
# [project:NAME] with NAME matching PROJECTS_CFG_NAME_RE, lib/projects_cfg.sh)
# emit a line-pointing error to stderr and are skipped (parsing continues).
#
# dashboard_max_rows is validated to [5, 100]. Values outside that range
# are clamped and a warning is emitted to stderr.
#
# Returns 0 (errors are reported but do not abort the parse).
# ---------------------------------------------------------------------------
_projects_cfg_parse_ini() {
    local cfg="$1"
    [[ -f "$cfg" ]] || return 0

    local _cur_name=""
    local _line_no=0

    while IFS= read -r _line; do
        (( _line_no++ )) || true

        # Strip trailing whitespace
        _line="${_line%"${_line##*[![:space:]]}"}"

        # Skip comments and blank lines
        [[ "$_line" =~ ^[[:space:]]*# ]] && continue
        [[ "$_line" =~ ^[[:space:]]*$ ]] && continue

        # Section header? An indented one is a header too, as every other
        # registry reader (and ini.projects_registry) reads it.
        if [[ "$_line" =~ ^[[:space:]]*\[ ]]; then
            _line="${_line#"${_line%%[![:space:]]*}"}"
            # A [project:NAME] header whose NAME the project-name rule accepts.
            _cur_name=""
            if [[ "$_line" =~ ^\[project:(.*)\]$ ]]; then
                _cur_name="${BASH_REMATCH[1]}"
                [[ "$_cur_name" =~ $PROJECTS_CFG_NAME_RE ]] || _cur_name=""
            fi
            if [[ -n "$_cur_name" ]]; then
                # Initialize defaults the first time a name is seen only. A
                # repeated section merges per key: the keys it sets overwrite
                # the earlier values, every other key keeps its value.
                # projects_enabled is always set here, so it marks "seen".
                if [[ -z "${projects_enabled["$_cur_name"]+set}" ]]; then
                    projects_priority["$_cur_name"]=""
                    projects_color["$_cur_name"]=""
                    projects_max_rows["$_cur_name"]=""
                    projects_enabled["$_cur_name"]="true"
                    projects_description["$_cur_name"]=""
                fi
            else
                echo "[projects.cfg] ERROR: malformed section header at line ${_line_no}: ${_line}" >&2
                echo "[projects.cfg]        Expected: [project:NAME] where NAME matches ${PROJECTS_CFG_NAME_RE}" >&2
            fi
            continue
        fi

        # Key=value line? Only process if inside a valid section.
        [[ -z "$_cur_name" ]] && continue

        # Field key must match ^[a-z_]+[[:space:]]*= (whitespace-tolerant)
        if [[ ! "$_line" =~ ^[a-z_]+[[:space:]]*= ]]; then
            # Not a recognized field — silently skip (forward compatibility)
            continue
        fi

        local _key="${_line%%=*}"
        local _val="${_line#*=}"
        # Strip leading/trailing whitespace from key
        _key="${_key%"${_key##*[![:space:]]}"}"
        # Strip leading/trailing whitespace from value
        _val="${_val#"${_val%%[![:space:]]*}"}"
        _val="${_val%"${_val##*[![:space:]]}"}"

        case "$_key" in
            priority)
                projects_priority["$_cur_name"]="$_val"
                ;;
            dashboard_color)
                projects_color["$_cur_name"]="$_val"
                ;;
            dashboard_max_rows)
                # Validate and clamp to [5, 100]
                if [[ ! "$_val" =~ ^[0-9]+$ ]]; then
                    echo "[projects.cfg] WARNING: dashboard_max_rows for '${_cur_name}' is not a positive integer ('${_val}'); using default 20" >&2
                    projects_max_rows["$_cur_name"]=20
                elif (( _val < 5 )); then
                    echo "[projects.cfg] WARNING: dashboard_max_rows for '${_cur_name}' is ${_val} (below minimum 5); clamping to 5" >&2
                    projects_max_rows["$_cur_name"]=5
                elif (( _val > 100 )); then
                    echo "[projects.cfg] WARNING: dashboard_max_rows for '${_cur_name}' is ${_val} (above maximum 100); clamping to 100" >&2
                    projects_max_rows["$_cur_name"]=100
                else
                    projects_max_rows["$_cur_name"]="$_val"
                fi
                ;;
            enabled)
                projects_enabled["$_cur_name"]="$_val"
                ;;
            description)
                projects_description["$_cur_name"]="$_val"
                ;;
            *)
                # Unknown field — silently skip (forward compatibility for future
                # dashboard_* fields not yet recognized by this version)
                ;;
        esac
    done < "$cfg"

    return 0
}

# ---------------------------------------------------------------------------
# _projects_cfg_parse_colon <cfg_path>
# Internal helper. Parse a colon-delimited (legacy) projects.cfg and populate
# the same associative arrays as _projects_cfg_parse_ini. Emits the
# deprecation warning once (via _projects_cfg_emit_deprecation_warning).
#
# Colon format: <name>:<priority>[:<color>]
# Populates only projects_priority and projects_color (the only fields the
# format supports). projects_max_rows, projects_enabled, and
# projects_description receive default values.
#
# Returns 0 always.
# ---------------------------------------------------------------------------
_projects_cfg_parse_colon() {
    local cfg="$1"
    [[ -f "$cfg" ]] || return 0

    _projects_cfg_emit_deprecation_warning

    while IFS= read -r _line; do
        # Skip comments and blank lines
        [[ "$_line" =~ ^[[:space:]]*# ]] && continue
        [[ "$_line" =~ ^[[:space:]]*$ ]] && continue

        # Parse colon-delimited: name:priority[:color]
        local _name _prio _color
        IFS=: read -r _name _prio _color <<< "$_line"

        # Strip whitespace
        _name="${_name#"${_name%%[![:space:]]*}"}"
        _name="${_name%"${_name##*[![:space:]]}"}"
        _prio="${_prio#"${_prio%%[![:space:]]*}"}"
        _prio="${_prio%"${_prio##*[![:space:]]}"}"
        _color="${_color#"${_color%%[![:space:]]*}"}"
        _color="${_color%"${_color##*[![:space:]]}"}"

        [[ -z "$_name" ]] && continue

        projects_priority["$_name"]="${_prio:-}"
        projects_color["$_name"]="${_color:-}"
        projects_max_rows["$_name"]=""
        projects_enabled["$_name"]="true"
        projects_description["$_name"]=""
    done < "$cfg"

    return 0
}

# ---------------------------------------------------------------------------
# _projects_cfg_load
# Internal. Load (or reload) projects.cfg into the module-level associative
# arrays. Detects format and dispatches to the appropriate parser.
#
# The arrays are module-level (global-scope) so that projects_cfg_list,
# projects_cfg_color, and the new helpers all share one parse of the file
# per invocation chain.
#
# Callers that need fresh data after an edit should call
# _projects_cfg_invalidate_cache first.
# ---------------------------------------------------------------------------
declare -gA _projects_cfg_loaded_from=""  2>/dev/null || true   # path of last loaded file
declare -gA projects_priority  2>/dev/null || true
declare -gA projects_color     2>/dev/null || true
declare -gA projects_max_rows  2>/dev/null || true
declare -gA projects_enabled   2>/dev/null || true
declare -gA projects_description 2>/dev/null || true

_projects_cfg_load() {
    local cfg
    cfg="$(projects_cfg_path)"

    # Re-parse only when the file path changes or hasn't been loaded yet.
    # This ensures callers within the same process share one parse.
    if [[ "${_projects_cfg_loaded_from:-}" == "$cfg" ]]; then
        return 0
    fi

    # Clear previous data
    projects_priority=()
    projects_color=()
    projects_max_rows=()
    projects_enabled=()
    projects_description=()
    _projects_cfg_loaded_from="$cfg"

    [[ -f "$cfg" ]] || return 0

    local fmt
    fmt="$(projects_cfg_format "$cfg")"

    if [[ "$fmt" == "ini" ]]; then
        _projects_cfg_parse_ini "$cfg"
    else
        _projects_cfg_parse_colon "$cfg"
    fi

    return 0
}

# ---------------------------------------------------------------------------
# _projects_cfg_invalidate_cache
# Internal. Force a re-parse on the next _projects_cfg_load call.
# Call after any operation that modifies projects.cfg (add, remove, etc.).
# ---------------------------------------------------------------------------
_projects_cfg_invalidate_cache() {
    _projects_cfg_loaded_from=""
}

# ---------------------------------------------------------------------------
# projects_cfg_colon_to_ini <cfg_path>
# Convert a colon-delimited projects.cfg to INI format in-place.
#
# Each colon-format data line  <name>:<priority>[:<color>]  is rewritten as:
#
#   [project:<name>]
#   priority=<priority>
#   dashboard_color=<color>     (omitted when color field is absent/empty)
#
# Comment lines and blank lines that precede the first data line are preserved
# as a leading comment block (though their content may be stale — operators
# should update them by hand). Comment lines embedded inside the data section
# are reproduced before the INI section that follows them.
#
# The canonical INI header (same text as projects_cfg_ensure) is prepended,
# replacing the original leading comment block (if any), because the colon-
# format header documents colon-format syntax that no longer applies.
#
# Idempotent: if projects_cfg_format returns 'ini' the function returns 0
# without modifying the file.
#
# Returns 0 on success; 1 on error (file missing, format already ini, etc.).
#
# Emits one line to stdout on success:
#   "[migration] projects.cfg: converted from colon-legacy to INI format (N entries)"
# ---------------------------------------------------------------------------
projects_cfg_colon_to_ini() {
    local cfg="${1:-}"
    if [[ -z "$cfg" ]]; then
        echo "projects_cfg_colon_to_ini: path argument is required" >&2
        return 1
    fi

    if [[ ! -f "$cfg" ]]; then
        echo "projects_cfg_colon_to_ini: file not found: $cfg" >&2
        return 1
    fi

    # Idempotency: already INI — nothing to do.
    local fmt
    fmt="$(projects_cfg_format "$cfg")"
    if [[ "$fmt" == "ini" ]]; then
        return 0
    fi

    # Build the canonical INI header (matches projects_cfg_ensure's heredoc).
    local canonical_header
    canonical_header='# projects.cfg — Registry of projects this kanban installation manages.
#
# Format: INI-style with one [project:NAME] section per project.
#
# Fields per section:
#   priority             — integer; lower = higher priority for wake-script iteration (required)
#   description          — free-text description (optional)
#   enabled              — true|false; default true (optional)
#   dashboard_color      — hex color #RRGGBB for dashboard tag (optional)
#   dashboard_max_rows   — integer rows shown per visibility column (optional; default 20)
#                          Valid range: 5–100. Values outside that range are clamped with a warning.
#
# Future dashboard_* fields can be added without format changes.
#
# Edit by hand or via operator scripts (create-project.sh, add-project.sh, remove-project.sh).
# Restart the tmux dashboard session after edits.
#
# Example:
#
#   [project:pgai-chomp-man]
#   priority=1
#   description=Release-workflow demo project (see demos/chomp-man-demo/)
#   enabled=true
#   dashboard_color=#378ADD
#   dashboard_max_rows=20
#
#   # Minimal: only priority set; color comes from the palette by
#   # registration order, all other fields use defaults.
#   [project:pgai-three-bears]
#   priority=2'

    local tmp
    tmp="$(pgai_mktemp projects_tmp)"

    # Convert: strip leading comment block; emit canonical header; emit INI sections.
    local entry_count
    entry_count="$(awk '
        /^[[:space:]]*#/ { next }
        /^[[:space:]]*$/ { next }
        NF { count++ }
        END { print (count == "" ? 0 : count) }
    ' "$cfg")"

    {
        printf '%s\n' "$canonical_header"
        awk '
            /^[[:space:]]*#/ { next }
            /^[[:space:]]*$/ { next }
            {
                # Parse colon-delimited: name:priority[:color]
                n_fields = split($0, fields, ":")
                name  = fields[1]
                prio  = fields[2]
                color = (n_fields >= 3) ? fields[3] : ""

                # Strip whitespace
                gsub(/^[[:space:]]+|[[:space:]]+$/, "", name)
                gsub(/^[[:space:]]+|[[:space:]]+$/, "", prio)
                gsub(/^[[:space:]]+|[[:space:]]+$/, "", color)

                if (name == "") next

                printf "\n[project:%s]\n", name
                if (prio != "") printf "priority=%s\n", prio
                if (color != "") printf "dashboard_color=%s\n", color
            }
        ' "$cfg"
    } > "$tmp"

    mv "$tmp" "$cfg"
    _projects_cfg_invalidate_cache
    echo "[migration] projects.cfg: converted from colon-legacy to INI format (${entry_count} entries)"
    return 0
}

# ---------------------------------------------------------------------------
# _projects_cfg_list_order <priority|registration>
# Internal filter for projects_cfg_list. Reads "<line>\t<priority>\t<name>"
# rows in file order and writes them sorted by priority then file line
# ("priority"), or unchanged ("registration").
# ---------------------------------------------------------------------------
_projects_cfg_list_order() {
    if [[ "${1:-}" == "registration" ]]; then
        cat
    else
        sort -k2,2n -k1,1n
    fi
}

# ---------------------------------------------------------------------------
# projects_cfg_list [--registration-order] — echo project names in priority
# order, one per line. Skips comments and blank lines.
#
# Works with both INI and colon-legacy formats (format detected automatically).
# An INI name registered by more than one [project:NAME] section is listed
# once, at its first section's position, with the last priority set. Any
# other section -- a [project:...] one whose name the registry rejects
# ([project:bad.name]) or one of another kind ([defaults]) -- ends the
# section before it, so a priority under it is no project's.
#
# Sort order:
#   Primary key:   priority (ascending, numeric). An absent or non-integer
#                  priority reads as 999 (sorted last), as the Python
#                  registry (ini.projects_registry) reads it.
#   Tie-break key: registration order — the order entries appear in
#                  projects.cfg (earlier file position = higher priority
#                  among equal-priority projects).
#
# --registration-order skips the priority sort: names come out in the order
# their entries appear in projects.cfg, so the first line is the first
# project registered in the file.
#
# When projects.cfg is missing or contains no registered projects, exits
# non-zero with a clear error — no silent default fallback.
# ---------------------------------------------------------------------------
projects_cfg_list() {
    local order="priority"
    [[ "${1:-}" == "--registration-order" ]] && order="registration"

    local cfg
    cfg="$(projects_cfg_path)"

    local fmt
    fmt="$(projects_cfg_format "$cfg")"

    local names=""
    if [[ -f "$cfg" ]]; then
        if [[ "$fmt" == "ini" ]]; then
            # INI format: parse sections in order, emit sorted by priority.
            # Use awk to collect (line_number, priority, name) then sort.
            names="$(awk "${PROJECTS_CFG_AWK_HEADER}"'
                /^[[:space:]]*#/ { next }
                /^[[:space:]]*$/ { next }
                (hdr = project_header($0)) != "" {
                    cur = hdr
                    # A repeated section adds no second entry: the name
                    # keeps its first position, and its priority is the
                    # last one any of its sections sets.
                    if (!(cur in section_line)) {
                        section_line[cur] = NR
                        section_order[++n_sections] = cur
                    }
                    next
                }
                # Any other section -- a [project:...] one with a rejected
                # name, or one of another kind such as [defaults] -- is no
                # project: its keys belong to none.
                /^[[:space:]]*\[/ { cur = ""; next }
                /^[a-z_]+[[:space:]]*=/ && cur != "" {
                    eq = index($0, "=")
                    key = substr($0, 1, eq - 1)
                    val = substr($0, eq + 1)
                    gsub(/^[[:space:]]+|[[:space:]]+$/, "", key)
                    gsub(/^[[:space:]]+|[[:space:]]+$/, "", val)
                    if (key == "priority") {
                        prio[cur] = val
                    }
                    next
                }
                END {
                    for (i = 1; i <= n_sections; i++) {
                            nm = section_order[i]
                        # Only a plain integer is a priority; an absent or
                        # non-integer value (priority=abc) reads as 999 and
                        # sorts last -- as ini.projects_registry reads it.
                        # (printf %d alone would turn "abc" into 0, first.)
                        p  = (prio[nm] ~ /^[+-]?[0-9]+$/ ? prio[nm] : 999)
                        printf "%010d\t%d\t%s\n", section_line[nm], p, nm
                    }
                }
            ' "$cfg" | _projects_cfg_list_order "$order" | awk -F'\t' '{ print $3 }')"
        else
            # Colon-legacy format: existing sort logic (no deprecation warning here;
            # the warning is emitted by _projects_cfg_emit_deprecation_warning via
            # projects_cfg_has and other functions that parse the file directly).
            names="$(awk -F: '
                /^[[:space:]]*#/ { next }
                /^[[:space:]]*$/ { next }
                NF >= 2 {
                    name = $1
                    prio = $2
                    gsub(/^[[:space:]]+|[[:space:]]+$/, "", name)
                    gsub(/^[[:space:]]+|[[:space:]]+$/, "", prio)
                    if (prio !~ /^[+-]?[0-9]+$/) prio = 999
                    printf "%010d\t%d\t%s\n", NR, prio, name
                }
            ' "$cfg" | _projects_cfg_list_order "$order" | awk -F'\t' '{ print $3 }')"
        fi
    fi

    if [[ -z "$names" ]]; then
        echo "projects_cfg_list: ERROR: no projects registered in $(projects_cfg_path)" >&2
        echo "  Register a project via scripts/create-project.sh or scripts/add-project.sh." >&2
        return 1
    fi

    echo "$names"
}

# ---------------------------------------------------------------------------
# projects_cfg_unaccepted_names — echo the name of every [project:...]
# section of an INI projects.cfg whose name the registry does not accept:
# project_section recognises it, project_header refuses its name
# (PROJECTS_CFG_AWK_HEADER, lib/projects_cfg.sh). [project:bad.name] gives
# "bad.name", [project:bad name] "bad name", [project:] an empty line.
#
# Names come out in file order, each once, exactly as written between
# "[project:" and the closing "]". projects_cfg_list never lists them, so a
# caller that must not lose a mistyped registration (wake-batch.sh) reads
# both. A missing projects.cfg, a colon-legacy one (whose names
# projects_cfg_list prints as written) and a registry with no such section
# print nothing. Always returns 0.
# ---------------------------------------------------------------------------
projects_cfg_unaccepted_names() {
    local cfg
    cfg="$(projects_cfg_path)"
    [[ -f "$cfg" && -r "$cfg" ]] || return 0
    [[ "$(projects_cfg_format "$cfg")" == "ini" ]] || return 0
    awk "${PROJECTS_CFG_AWK_HEADER}"'
        /^[[:space:]]*#/ { next }
        project_section($0) && project_header($0) == "" {
            name = project_section_name($0)
            if (!(name in seen)) { seen[name] = 1; print name }
        }
    ' "$cfg" || true
    return 0
}

# ---------------------------------------------------------------------------
# projects_cfg_active — echo only non-halted project names in priority order.
#
# Same sort order and tie-break as projects_cfg_list (priority ascending,
# registration order for same-priority entries).  Projects whose per-project
# HALT file exists are silently excluded from the output.
#
# Requires lib/project_paths.sh to be sourced BEFORE this library (for
# pp_project_halted).
#
# When projects.cfg is missing or empty, behaves the same as projects_cfg_list
# — exits non-zero with a clear error; no silent default fallback.
# ---------------------------------------------------------------------------
projects_cfg_active() {
    local project
    while IFS= read -r project; do
        if ! pp_project_halted "$project" 2>/dev/null; then
            echo "$project"
        fi
    done < <(projects_cfg_list)
}

# ---------------------------------------------------------------------------
# projects_cfg_enabled <name> — return 0 when <name> is enabled, 1 when
# projects.cfg sets enabled=false for it.
#
# The one answer the wake loop, PM discovery, the OVERWATCH sweep and the
# overwatch wake skip a project on; `why` (pgai_agent_kanban/ops/
# verdict.py) reads the same rule through the Python twin,
# pgai_agent_kanban.lib.ini.project_enabled -- a change to the rule here
# needs the same change there.
#
# Only the value false disables (any case, surrounding whitespace ignored).
# An absent key, an empty value, true, an unregistered project and a
# colon-legacy registry (which has no enabled field) all read as enabled.
# Any other value reads as enabled and prints one warning per process on
# stderr naming the value and the fix. Read-only listings
# (projects_cfg_list and every dashboard built on it) still list a
# disabled project; only the loops that do work skip it.
# ---------------------------------------------------------------------------
declare -gA _PROJECTS_CFG_ENABLED_WARNED 2>/dev/null || true

projects_cfg_enabled() {
    local name="${1:-}"
    [[ -z "$name" ]] && return 0

    local raw
    raw="$(projects_cfg_field "$name" enabled 2>/dev/null)" || return 0
    # projects_cfg_field strips surrounding whitespace; compare lowercased.
    case "${raw,,}" in
        false) return 1 ;;
        ""|true) return 0 ;;
    esac
    local warn_key
    warn_key="$(projects_cfg_path)|${name}|${raw}"
    if [[ -z "${_PROJECTS_CFG_ENABLED_WARNED[$warn_key]:-}" ]]; then
        _PROJECTS_CFG_ENABLED_WARNED[$warn_key]=1
        echo "[projects.cfg] WARNING: enabled for '${name}' is '${raw}' (only true or false are recognised); treating the project as enabled -- set enabled=false to pause it or enabled=true to silence this warning" >&2
    fi
    return 0
}

# ---------------------------------------------------------------------------
# projects_cfg_disabled_skip_line <name> — echo the one line a loop narrates
# when it skips <name> because projects_cfg_enabled returned 1. Each loop
# prefixes it in its own log style; the text after the prefix is the same
# everywhere, so one grep finds every skip.
# ---------------------------------------------------------------------------
projects_cfg_disabled_skip_line() {
    echo "project ${1:-}: disabled in projects.cfg (enabled=false) — skipped"
}

# ---------------------------------------------------------------------------
# projects_cfg_has <name> — return 0 if registered, 1 otherwise. In an INI
# registry <name> is registered when a header project_header accepts names
# it (PROJECTS_CFG_AWK_HEADER), so a name projects_cfg_list does not list --
# [project:bad.name], [project:My_App] -- is not registered here either.
# ---------------------------------------------------------------------------
projects_cfg_has() {
    local name="$1"
    [[ -z "$name" ]] && return 1

    local cfg
    cfg="$(projects_cfg_path)"
    [[ -f "$cfg" ]] || return 1

    local fmt
    fmt="$(projects_cfg_format "$cfg")"

    if [[ "$fmt" == "ini" ]]; then
        # The header rule every registry reader uses: leading and trailing
        # whitespace (a CRLF's CR included) ignored, the name held to
        # PROJECTS_CFG_NAME_RE.
        awk -v n="$name" "${PROJECTS_CFG_AWK_HEADER}"'
            project_header($0) == n { found = 1; exit }
            END { exit !found }
        ' "$cfg"
    else
        grep -qE "^[[:space:]]*${name}[[:space:]]*:" "$cfg"
    fi
}

# ---------------------------------------------------------------------------
# projects_cfg_registration <name> — echo <name>'s registration exactly as
# projects.cfg holds it, for a caller that records or copies the entry.
#
#   INI          — the first [project:NAME] section: its header line and
#                  every line after it (comments and blank lines included)
#                  up to the next line starting with '[' (after optional
#                  whitespace) or end of file.
#   colon-legacy — the first data line whose name field is NAME, e.g.
#                  "NAME:1:#378ADD".
#
# Lines are printed unaltered; the header is found by the same rule as
# projects_cfg_has, so leading and trailing whitespace on it is tolerated. Returns 0
# with the text on stdout, or 1 with nothing printed when NAME is empty,
# projects.cfg is missing, or NAME is not registered.
# ---------------------------------------------------------------------------
projects_cfg_registration() {
    local name="${1:-}"
    [[ -z "$name" ]] && return 1

    local cfg
    cfg="$(projects_cfg_path)"
    [[ -f "$cfg" ]] || return 1

    if [[ "$(projects_cfg_format "$cfg")" == "ini" ]]; then
        awk -v n="$name" "${PROJECTS_CFG_AWK_HEADER}"'
            !found && project_header($0) == n { found = 1; print; next }
            found && /^[[:space:]]*\[/ { exit }
            found { print }
            END { exit !found }
        ' "$cfg"
    else
        awk -v n="$name" '
            /^[[:space:]]*#/ { next }
            /^[[:space:]]*$/ { next }
            {
                colon = index($0, ":")
                if (colon == 0) next
                field = substr($0, 1, colon - 1)
                gsub(/^[[:space:]]+|[[:space:]]+$/, "", field)
                if (field == n) { found = 1; print; exit }
            }
            END { exit !found }
        ' "$cfg"
    fi
}

# ---------------------------------------------------------------------------
# projects_cfg_priority <name> — echo the priority value for <name>.
# Returns 0 with the priority on stdout if registered. Returns 1 silently if not.
# INI: the priority of the last [project:NAME] section that sets one
# (_projects_cfg_ini_value in lib/projects_cfg.sh).
# ---------------------------------------------------------------------------
projects_cfg_priority() {
    local name="$1"
    [[ -z "$name" ]] && return 1

    local cfg
    cfg="$(projects_cfg_path)"
    [[ -f "$cfg" ]] || return 1

    local fmt
    fmt="$(projects_cfg_format "$cfg")"

    if [[ "$fmt" == "ini" ]]; then
        _projects_cfg_ini_value "$cfg" "$name" priority || return 1
    else
        awk -F: -v n="$name" '
            /^[[:space:]]*#/ { next }
            /^[[:space:]]*$/ { next }
            {
                gsub(/^[[:space:]]+|[[:space:]]+$/, "", $1)
                gsub(/^[[:space:]]+|[[:space:]]+$/, "", $2)
                if ($1 == n) { print $2; found=1; exit }
            }
            END { if (!found) exit 1 }
        ' "$cfg"
    fi
}

# ---------------------------------------------------------------------------
# projects_cfg_max_rows <name>
# Echo the dashboard_max_rows value for the named project.
# Returns the per-project value if set, or 20 (the code default) if absent.
#
# Precedence (highest to lowest):
#   1. Per-project dashboard_max_rows in projects.cfg (from the last
#      [project:NAME] section that sets it -- _projects_cfg_ini_value)
#   2. DASHBOARD_MAX_ROWS env var (global fallback from kanban.cfg [dashboard] max_rows)
#   3. Code default: 20
#
# Only valid for INI-format projects.cfg. For colon-legacy format, per-project
# max_rows is not supported — returns the global fallback or 20.
#
# Returns 0 always.
# ---------------------------------------------------------------------------
projects_cfg_max_rows() {
    local name="$1"
    local default_rows="${DASHBOARD_MAX_ROWS:-20}"

    if [[ -z "$name" ]]; then
        echo "projects_cfg_max_rows: project name is required" >&2
        echo "$default_rows"
        return 0
    fi

    local cfg
    cfg="$(projects_cfg_path)"

    local fmt
    fmt="$(projects_cfg_format "$cfg")"

    if [[ "$fmt" != "ini" ]]; then
        # Colon-legacy: per-project max_rows not supported
        echo "$default_rows"
        return 0
    fi

    [[ -f "$cfg" ]] || { echo "$default_rows"; return 0; }

    local raw_val
    raw_val="$(_projects_cfg_ini_value "$cfg" "$name" dashboard_max_rows 2>/dev/null)"

    if [[ -z "$raw_val" ]]; then
        # Field absent — use global fallback or code default
        echo "$default_rows"
        return 0
    fi

    # Validate and clamp (mirrors _projects_cfg_parse_ini logic)
    if [[ ! "$raw_val" =~ ^[0-9]+$ ]]; then
        echo "[projects.cfg] WARNING: dashboard_max_rows for '${name}' is not a positive integer ('${raw_val}'); using default ${default_rows}" >&2
        echo "$default_rows"
    elif (( raw_val < 5 )); then
        echo "[projects.cfg] WARNING: dashboard_max_rows for '${name}' is ${raw_val} (below minimum 5); clamping to 5" >&2
        echo 5
    elif (( raw_val > 100 )); then
        echo "[projects.cfg] WARNING: dashboard_max_rows for '${name}' is ${raw_val} (above maximum 100); clamping to 100" >&2
        echo 100
    else
        echo "$raw_val"
    fi

    return 0
}

# ---------------------------------------------------------------------------
# projects_cfg_field <name> <field>
# Generic accessor for any field stored in a project's INI section.
# Echoes the raw field value (no type coercion, no clamping).
# Returns 0 with the value on stdout when found.
# Returns 1 (with empty stdout) when the project is not registered or the
# field is absent.
#
# INI: a project registered by more than one [project:NAME] section reads
# <field> from the last section that sets it, and a field only an earlier
# section sets keeps that value (_projects_cfg_ini_value in
# lib/projects_cfg.sh), as pgai_agent_kanban.lib.ini.projects_registry reads
# the same file.
#
# <field> must be a recognized INI key name (priority, description, enabled,
# dashboard_color, dashboard_max_rows, or any future dashboard_* key).
#
# Note: for colon-legacy format only 'priority' and 'dashboard_color' are
# available; all other fields return empty.
# ---------------------------------------------------------------------------
projects_cfg_field() {
    local name="${1:-}"
    local field="${2:-}"

    if [[ -z "$name" || -z "$field" ]]; then
        echo "projects_cfg_field: project name and field name are required" >&2
        return 1
    fi

    local cfg
    cfg="$(projects_cfg_path)"

    local fmt
    fmt="$(projects_cfg_format "$cfg")"

    if [[ "$fmt" == "ini" ]]; then
        [[ -f "$cfg" ]] || return 1

        local result
        result="$(_projects_cfg_ini_value "$cfg" "$name" "$field")" || return 1
        echo "$result"
        return 0
    else
        # Colon-legacy: only priority and dashboard_color available
        case "$field" in
            priority)
                projects_cfg_priority "$name"
                return $?
                ;;
            dashboard_color)
                _projects_cfg_read_raw_color "$name"
                return $?
                ;;
            *)
                # Field not available in colon format
                return 1
                ;;
        esac
    fi
}

# ---------------------------------------------------------------------------
# projects_cfg_add <name> [priority] [color] — register a project. Idempotent.
# If the project is already registered, the priority and color are updated to
# the new values (or left unchanged when the respective arg is omitted).
#
# If <priority> is not provided, the next available priority is computed as
# (max-existing-priority + 1), starting from 1 on an empty registry.
#
# If <color> is provided, the entry is written in the three-field form
# name:priority:#RRGGBB (colon) or dashboard_color=#RRGGBB (INI). If absent,
# only priority is written (the color helper falls back to the palette at
# read time).
#
# For new registrations, callers should prefer passing an explicit color so
# the registry stores the full three-field form.
# ---------------------------------------------------------------------------
projects_cfg_add() {
    local name="$1"
    local priority="${2:-}"
    local color="${3:-}"

    if [[ -z "$name" ]]; then
        echo "projects_cfg_add: project name is required" >&2
        return 1
    fi

    projects_cfg_ensure
    local cfg
    cfg="$(projects_cfg_path)"

    local fmt
    fmt="$(projects_cfg_format "$cfg")"

    # If already registered: update priority and/or color (if supplied) or no-op
    if projects_cfg_has "$name"; then
        if [[ -n "$priority" || -n "$color" ]]; then
            if [[ "$fmt" == "ini" ]]; then
                local tmp
                tmp="$(pgai_mktemp projects_tmp)"
                awk -v n="$name" -v p="$priority" -v c="$color" "${PROJECTS_CFG_AWK_HEADER}"'
                    (hdr = project_header($0)) != "" {
                        cur = hdr
                        in_target = (cur == n)
                        print; next
                    }
                    /^[[:space:]]*\[/ { in_target = 0; print; next }
                    in_target && /^priority=/ && p != "" {
                        print "priority=" p; next
                    }
                    in_target && /^dashboard_color=/ && c != "" {
                        print "dashboard_color=" c; next
                    }
                    { print }
                ' "$cfg" > "$tmp"
                mv "$tmp" "$cfg"
            else
                # Colon-legacy: in-place update
                local tmp
                tmp="$(pgai_mktemp projects_tmp)"
                awk -F: -v n="$name" -v p="$priority" -v c="$color" '
                    BEGIN { OFS=":" }
                    /^[[:space:]]*#/ { print; next }
                    /^[[:space:]]*$/ { print; next }
                    {
                        name_field = $1
                        gsub(/^[[:space:]]+|[[:space:]]+$/, "", name_field)
                        if (name_field == n) {
                            new_p = (p != "") ? p : $2
                            gsub(/^[[:space:]]+|[[:space:]]+$/, "", new_p)
                            existing_c = (NF >= 3) ? $3 : ""
                            gsub(/^[[:space:]]+|[[:space:]]+$/, "", existing_c)
                            new_c = (c != "") ? c : existing_c
                            gsub(/^[[:space:]]+|[[:space:]]+$/, "", new_c)
                            if (new_c != "") {
                                print n ":" new_p ":" new_c
                            } else {
                                print n ":" new_p
                            }
                            next
                        }
                        print
                    }
                ' "$cfg" > "$tmp"
                mv "$tmp" "$cfg"
            fi
        fi
        _projects_cfg_invalidate_cache
        return 0
    fi

    # New registration: compute priority if not supplied
    if [[ -z "$priority" ]]; then
        local max_prio
        if [[ "$fmt" == "ini" ]]; then
            max_prio="$(awk '
                /^[a-z_]+[[:space:]]*=/ {
                    eq = index($0, "=")
                    key = substr($0, 1, eq - 1)
                    val = substr($0, eq + 1)
                    gsub(/^[[:space:]]+|[[:space:]]+$/, "", key)
                    gsub(/^[[:space:]]+|[[:space:]]+$/, "", val)
                    if (key == "priority" && val ~ /^[0-9]+$/ && val+0 > max+0) max = val
                }
                END { print (max == "" ? 0 : max) }
            ' "$cfg")"
        else
            max_prio="$(awk -F: '
                /^[[:space:]]*#/ { next }
                /^[[:space:]]*$/ { next }
                NF >= 2 {
                    p = $2
                    gsub(/^[[:space:]]+|[[:space:]]+$/, "", p)
                    if (p ~ /^[0-9]+$/ && p+0 > max+0) max = p
                }
                END { print (max == "" ? 0 : max) }
            ' "$cfg")"
        fi
        priority=$((max_prio + 1))
    fi

    # Append new entry
    if [[ "$fmt" == "ini" ]]; then
        {
            echo ""
            echo "[project:${name}]"
            echo "priority=${priority}"
            [[ -n "$color" ]] && echo "dashboard_color=${color}"
        } >> "$cfg"
    else
        # Colon-legacy: append in legacy format
        if [[ -n "$color" ]]; then
            echo "${name}:${priority}:${color}" >> "$cfg"
        else
            echo "${name}:${priority}" >> "$cfg"
        fi
    fi

    _projects_cfg_invalidate_cache
    return 0
}

# ---------------------------------------------------------------------------
# projects_cfg_remove <name> — unregister a project. Idempotent.
# (Does NOT delete the project directory under projects/<name>/.)
# Handles both INI and colon-legacy formats.
# ---------------------------------------------------------------------------
projects_cfg_remove() {
    local name="$1"

    if [[ -z "$name" ]]; then
        echo "projects_cfg_remove: project name is required" >&2
        return 1
    fi

    local cfg
    cfg="$(projects_cfg_path)"
    [[ -f "$cfg" ]] || return 0

    if ! projects_cfg_has "$name"; then
        return 0
    fi

    local fmt
    fmt="$(projects_cfg_format "$cfg")"

    local tmp
    tmp="$(pgai_mktemp projects_tmp)"

    if [[ "$fmt" == "ini" ]]; then
        # Remove the entire [project:NAME] section (header + all key=value lines
        # until the next section or end of file).
        awk -v n="$name" "${PROJECTS_CFG_AWK_HEADER}"'
            (hdr = project_header($0)) != "" {
                cur = hdr
                if (cur == n) { skip = 1; next }
                skip = 0; print; next
            }
            /^[[:space:]]*\[/ {
                skip = 0; print; next
            }
            skip { next }
            { print }
        ' "$cfg" > "$tmp"
    else
        # Colon-legacy: remove the matching line
        awk -F: -v n="$name" '
            /^[[:space:]]*#/ { print; next }
            /^[[:space:]]*$/ { print; next }
            {
                name_field = $1
                gsub(/^[[:space:]]+|[[:space:]]+$/, "", name_field)
                if (name_field == n) next
                print
            }
        ' "$cfg" > "$tmp"
    fi

    mv "$tmp" "$cfg"
    _projects_cfg_invalidate_cache
    return 0
}

# ---------------------------------------------------------------------------
# projects_resolve_release_hook_path <project_name> <phase>
#
# Resolves the hook path for <phase> using a three-tier precedence lookup and
# echoes the resolved absolute path, or an empty string when no hook is found.
#
# Arguments:
#   <project_name>  — project name (must match a directory under projects/)
#   <phase>         — one of: pre-squash | pre-tag | post-tag
#
# Precedence (highest to lowest):
#   (a) project.cfg [hooks] cm_release_<phase>_hook  (cfg)
#   (b) $KANBAN_ROOT/projects/<name>/hooks/cm-release-<phase>.sh  (kanban-side)
#   (c) <dev_tree_path>/.pgai/hooks/cm-release-<phase>.sh  (in-repo)
#
# Side effect:
#   Sets the global variable _PGAI_HOOK_LAST_SOURCE to the winning source label
#   (cfg | kanban-side | in-repo) or empty string when no hook is found.
#   Callers that need the source label may read this variable immediately after
#   calling projects_resolve_release_hook_path.
#
# Path resolution rules for tier (a):
#   - Leading '/' → treated as an absolute path; returned as-is.
#   - No leading '/' → resolved relative to dev_tree_path from project.cfg.
#
# Tier (b) is a plain file existence check on the kanban-side hooks directory.
# Tier (c) is a plain file existence check on <dev_tree_path>/.pgai/hooks/.
# For tier (c), an existing-but-non-executable file is NOT silently skipped;
# the path is still returned and the source is set to in-repo — the caller
# is responsible for the executability check (cm_resolve_and_enforce_hook in
# cm_release_hooks.sh enforces the fail-loud rule for in-repo hooks).
#
# Requires lib/project_paths.sh to be sourced first (for pp_project_root and
# _pp_project_cfg_file).
#
# Example:
#   hook_path="$(projects_resolve_release_hook_path pgai-agent-kanban pre-squash)"
#   src="$_PGAI_HOOK_LAST_SOURCE"   # cfg | kanban-side | in-repo | ""
# ---------------------------------------------------------------------------
# Module-level: last source label written by projects_resolve_release_hook_path.
_PGAI_HOOK_LAST_SOURCE=""

projects_resolve_release_hook_path() {
    local name="${1:-}"
    local phase="${2:-}"

    # Clear source label before any return path.
    _PGAI_HOOK_LAST_SOURCE=""

    if [[ -z "$name" ]]; then
        echo "projects_resolve_release_hook_path: project name is required" >&2
        return 1
    fi
    if [[ -z "$phase" ]]; then
        echo "projects_resolve_release_hook_path: phase is required (pre-squash | pre-tag | post-tag)" >&2
        return 1
    fi

    # Map phase identifier to the project.cfg field name.
    # Phase names use hyphens; cfg field names use underscores.
    local field_name
    case "$phase" in
        pre-squash)  field_name="cm_release_pre_squash_hook" ;;
        pre-tag)     field_name="cm_release_pre_tag_hook"    ;;
        post-tag)    field_name="cm_release_post_tag_hook"   ;;
        *)
            echo "projects_resolve_release_hook_path: unknown phase '${phase}'; expected pre-squash | pre-tag | post-tag" >&2
            return 1
            ;;
    esac

    # Locate project.cfg for this project.
    local _krp_kanban_root
    _krp_kanban_root="${KANBAN_ROOT:-${PGAI_AGENT_KANBAN_ROOT_PATH:-$HOME/pgai_agent_kanban}}"
    local project_root cfg_file
    project_root="$(KANBAN_ROOT="$_krp_kanban_root" \
        pp_project_root "$name" 2>/dev/null)" || {
        echo "" ; return 0
    }
    cfg_file="$(_pp_project_cfg_file "$project_root")"

    # ---------------------------------------------------------------------------
    # Tier (a): project.cfg [hooks] cm_release_<phase>_hook
    # ---------------------------------------------------------------------------
    if [[ -n "$cfg_file" && -f "$cfg_file" ]]; then
        local raw_value
        raw_value="$(grep -E "^[[:space:]]*${field_name}[[:space:]]*=" "$cfg_file" \
            | head -n1 \
            | sed 's|^[^=]*=[[:space:]]*||; s|[[:space:]]*$||; s|^["'"'"']||; s|["'"'"']$||')"

        if [[ -n "$raw_value" ]]; then
            # Absolute path: return as-is.
            if [[ "$raw_value" == /* ]]; then
                _PGAI_HOOK_LAST_SOURCE="cfg"
                echo "$raw_value"
                return 0
            fi

            # Relative path: resolve against the project's own repository
            # (pp_dev_tree_path; a hook lives in no other).
            local dev_tree
            dev_tree="$(KANBAN_ROOT="$_krp_kanban_root" pp_dev_tree_path "$name" 2>/dev/null)" || dev_tree=""

            if [[ -z "$dev_tree" ]]; then
                echo "projects_resolve_release_hook_path: cannot resolve relative hook path '${raw_value}': dev_tree_path not set in ${cfg_file}" >&2
                echo ""
                return 0
            fi

            _PGAI_HOOK_LAST_SOURCE="cfg"
            echo "${dev_tree}/${raw_value}"
            return 0
        fi
    fi

    # ---------------------------------------------------------------------------
    # Tier (b): kanban-side projects/<name>/hooks/cm-release-<phase>.sh
    # ---------------------------------------------------------------------------
    local kanban_side_hook
    kanban_side_hook="${_krp_kanban_root}/projects/${name}/hooks/cm-release-${phase}.sh"
    if [[ -f "$kanban_side_hook" ]]; then
        _PGAI_HOOK_LAST_SOURCE="kanban-side"
        echo "$kanban_side_hook"
        return 0
    fi

    # ---------------------------------------------------------------------------
    # Tier (c): in-repo <dev_tree_path>/.pgai/hooks/cm-release-<phase>.sh
    # ---------------------------------------------------------------------------
    local dev_tree_for_inrepo=""
    if [[ -n "$cfg_file" && -f "$cfg_file" ]]; then
        dev_tree_for_inrepo="$(KANBAN_ROOT="$_krp_kanban_root" pp_dev_tree_path "$name" 2>/dev/null)" || dev_tree_for_inrepo=""
    fi

    if [[ -n "$dev_tree_for_inrepo" ]]; then
        local in_repo_hook
        in_repo_hook="${dev_tree_for_inrepo}/.pgai/hooks/cm-release-${phase}.sh"
        if [[ -f "$in_repo_hook" ]]; then
            _PGAI_HOOK_LAST_SOURCE="in-repo"
            echo "$in_repo_hook"
            return 0
        fi
    fi

    # No hook found at any tier.
    echo ""
    return 0
}

