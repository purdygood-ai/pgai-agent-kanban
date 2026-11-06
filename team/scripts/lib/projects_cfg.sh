#!/usr/bin/env bash
# team/scripts/lib/projects_cfg.sh
#
# Color and priority helpers for the projects.cfg registry.
#
# This library extends the projects.cfg format to support an optional third
# colon-separated field: display_color (#RRGGBB hex).
#
# Extended format (one project per line):
#
#   # Comments start with '#'. Blank lines are ignored.
#   # Each non-comment line: <project_name>:<priority>[:<display_color>]
#   #   - project_name  — must match a directory under projects/<name>/
#   #   - priority      — integer; lower = higher priority (sorted ascending)
#   #   - display_color — optional; HTML hex color (#RRGGBB); if absent, the
#   #                     next deterministic palette entry is used at read time
#   #
#   pgai-chomp-man:1:#378ADD
#   pgai-three-bears:2
#   marketing-site:3:#D85A30
#
# Public API
# ----------
#   PGAI_DEFAULT_PALETTE            — read-only array of 8 default colors,
#                                     built at source time from
#                                     team/config/default_palette.txt
#   projects_cfg_color  <name>      — echo display_color for <name>;
#                                     falls back to next deterministic palette
#                                     entry when the color field is absent
#   projects_cfg_next_color         — echo the lowest-index palette entry not
#                                     currently used by any registered project;
#                                     wraps to index 0 when all 8 are consumed
#   projects_cfg_next_priority      — echo max(existing priorities) + 1,
#                                     or 1 when projects.cfg is empty
#   PROJECTS_CFG_NAME_RE            — the project-name rule, an anchored
#                                     extended regex every registry reader
#                                     and name check takes (see below)
#   PROJECTS_CFG_AWK_HEADER         — awk text defining project_section(line),
#                                     project_section_name(line) and
#                                     project_header(line), the [project:NAME]
#                                     header rule; prepend it to an awk
#                                     program (see below)
#
# Color format rules
# ------------------
#   Accepted:   #RRGGBB  (e.g. #378ADD — six hex digits, case-insensitive)
#   Rejected:   0xRRGGBB  (must use # prefix — not 0x)
#   Rejected:   #RGB      (three-digit short form not supported)
#   Validation  happens only at write/check time; this library reads and
#               echoes whatever is stored. Format checks belong to the
#               registry verbs (create_project / add_project in
#               pgai_agent_kanban/ops/projects.py).
#
# Source order
# ------------
# This library may be sourced independently, but callers that also need the
# full projects.sh API should source projects.sh, which sources this file.
# This file does NOT source projects.sh to avoid circular dependencies.
# It calls projects_cfg_path() and projects_cfg_format() — callers must
# ensure projects.sh (or at minimum those two definitions) is sourced first.

# ---------------------------------------------------------------------------
# PROJECTS_CFG_NAME_RE — the project-name rule, defined here and nowhere
# else: a lowercase ASCII letter or digit, then lowercase letters, digits
# and '-'. A [project:NAME] section whose NAME fails it is no project to
# any registry reader, and the registry verbs (create, add, remove) refuse
# any other name.
#
#   [[ "$name" =~ $PROJECTS_CFG_NAME_RE ]]     # bash (leave it unquoted)
#
# Every other reader takes it from this line: PROJECTS_CFG_AWK_HEADER
# below embeds it in project_header, and project_name_rule in
# pgai_agent_kanban/lib/ini.py reads this assignment for the Python readers
# and pgai_agent_kanban/ops/projects.py. Keep it one single-quoted
# assignment on its own line, written in the syntax bash, awk and Python
# regexes share (anchors, bracket expressions, '*'), and free of '/' so awk
# can embed it as a regex literal.
#
# Defined before the palette load below, with the header rule, so a missing
# default_palette.txt leaves the name rule in place.
# ---------------------------------------------------------------------------
PROJECTS_CFG_NAME_RE='^[a-z0-9][a-z0-9-]*$'

# ---------------------------------------------------------------------------
# PROJECTS_CFG_AWK_HEADER — the one [project:NAME] header rule every awk
# program reading an INI projects.cfg uses. Prepend it to the program text:
#
#   awk -v n="$name" "${PROJECTS_CFG_AWK_HEADER}"'
#       (hdr = project_header($0)) != "" { cur = hdr; next }
#       ...
#   ' "$cfg"
#
# Recognising a project section and accepting its name are two steps:
#
#   project_section(line)       1 when <line> is a [project:...] header of
#                               any name (an empty one included), else 0.
#   project_section_name(line)  the text between "[project:" and the last
#                               "]" of such a header, as written; "" for any
#                               other line.
#   project_header(line)        NAME when <line> is a project section whose
#                               name the registry accepts -- NAME matches
#                               PROJECTS_CFG_NAME_RE -- and "" otherwise.
#
# Every registry reader lists and reads projects through project_header, so
# a section whose name the registry does not accept ([project:bad.name],
# [project:bad name], [project:My_App]) is no project; projects_cfg_unaccepted_names
# (lib/projects.sh) names such sections through project_section, so a
# caller can report them instead of losing them.
#
# Leading and trailing whitespace on the line, the CR of a CRLF line ending
# included, is ignored, as pgai_agent_kanban.lib.ini.projects_registry
# ignores it for the same file. Anything else after the closing "]" -- a
# trailing comment such as "[project:alpha] # first" -- makes the line no
# project section at all. The line itself is not changed, so a program that
# prints $0 keeps its bytes.
#
# It is defined before the palette load below so that a missing or
# malformed default_palette.txt stays a colour-only failure: every registry
# reader in projects.sh still has its header rule.
#
# This is the bash header rule: every bash registry reader, project-name
# resolution in lib/project_paths.sh included (through projects_cfg_list),
# reads headers through it. The Python rules are _PROJECT_SECTION_RE and
# _project_header in pgai_agent_kanban/ops/projects.py;
# tests/unit/test_registry_reader_parity.py feeds the same header variants
# through both and expects one answer.
# ---------------------------------------------------------------------------
PROJECTS_CFG_AWK_HEADER='
function project_section(line) {
    sub(/^[[:space:]]+/, "", line)
    sub(/[[:space:]]+$/, "", line)
    return (line ~ /^\[project:.*\]$/)
}
function project_section_name(line) {
    if (!project_section(line)) return ""
    sub(/^[[:space:]]+/, "", line)
    sub(/[[:space:]]+$/, "", line)
    return substr(line, 10, length(line) - 10)
}
function project_header(line,    name) {
    name = project_section_name(line)
    if (name !~ /'"${PROJECTS_CFG_NAME_RE}"'/) return ""
    return name
}
'

# ---------------------------------------------------------------------------
# _projects_cfg_ini_value <cfg_path> <name> <key>
# The one per-field reader of an INI projects.cfg. Echoes the value of <key>
# for project <name>, whitespace-trimmed and otherwise unaltered.
#
# A project registered by more than one [project:NAME] section reads per
# key: the last section that sets <key> wins, and a key only an earlier
# section sets keeps that earlier value -- as
# pgai_agent_kanban.lib.ini.projects_registry merges the same file, and as
# _Registry._ini_field in pgai_agent_kanban/ops/projects.py reads it.
#
# Only key=value lines at the start of the line whose key is lowercase
# letters and underscores are read; '#' lines are comments; a section of
# another kind ends the project's section.
#
# Returns 0 with the value on stdout when some section of <name> sets
# <key> (the value may be empty), 2 with nothing printed when <name> is
# registered but no section of it sets <key>, and 1 with nothing printed
# when no [project:NAME] section exists.
#
# Defined before the palette load below, with PROJECTS_CFG_AWK_HEADER, so
# the field readers in projects.sh keep working when default_palette.txt is
# missing or malformed.
# ---------------------------------------------------------------------------
_projects_cfg_ini_value() {
    local cfg="$1" name="$2" key="$3"
    awk -v n="$name" -v f="$key" "${PROJECTS_CFG_AWK_HEADER}"'
        /^[[:space:]]*#/ { next }
        /^[[:space:]]*$/ { next }
        (hdr = project_header($0)) != "" {
            cur = hdr
            if (cur == n) section_found = 1
            next
        }
        /^[[:space:]]*\[/ { cur = ""; next }
        /^[a-z_]+[[:space:]]*=/ && cur == n {
            eq = index($0, "=")
            k = substr($0, 1, eq - 1)
            v = substr($0, eq + 1)
            gsub(/^[[:space:]]+|[[:space:]]+$/, "", k)
            gsub(/^[[:space:]]+|[[:space:]]+$/, "", v)
            if (k == f) { val = v; found = 1 }
        }
        END {
            if (found) { print val; exit 0 }
            if (section_found) exit 2
            exit 1
        }
    ' "$cfg"
}

# ---------------------------------------------------------------------------
# PGAI_DEFAULT_PALETTE — eight visually distinct project colors.
#
# The canonical definition lives in team/config/default_palette.txt — this
# is NOT a hand-transcribed copy. This array is built by reading that file
# at source time, below, so the file is the single source of truth for the
# default palette; do not hand-transcribe its values elsewhere. Sourcing
# this library fails loud (naming the file path, never a silent empty or
# partial palette) if the definition file is missing or malformed.
# ---------------------------------------------------------------------------
_projects_cfg_palette_file="$(dirname "${BASH_SOURCE[0]}")/../../config/default_palette.txt"

if [[ ! -f "$_projects_cfg_palette_file" ]]; then
    echo "projects_cfg.sh: FATAL: canonical palette definition file not found: '${_projects_cfg_palette_file}' — cannot build PGAI_DEFAULT_PALETTE" >&2
    unset _projects_cfg_palette_file
    return 1 2>/dev/null || exit 1
fi

# Parse rule (must match team/config/default_palette.txt's own documented
# rule): a line is a data line only when its first whitespace-delimited
# token matches ^#[0-9A-Fa-f]{6}$. Every other line (header prose, blank
# lines, per-entry hue comments that don't start with a hex token) is
# skipped. Order is preserved as read from the file.
PGAI_DEFAULT_PALETTE=()
while IFS= read -r _pcf_line || [[ -n "$_pcf_line" ]]; do
    read -r _pcf_hex _pcf_rest <<< "$_pcf_line"
    [[ "$_pcf_hex" =~ ^#[0-9A-Fa-f]{6}$ ]] || continue
    PGAI_DEFAULT_PALETTE+=("$_pcf_hex")
done < "$_projects_cfg_palette_file"

if [[ "${#PGAI_DEFAULT_PALETTE[@]}" -eq 0 ]]; then
    echo "projects_cfg.sh: FATAL: canonical palette definition file is malformed (no valid #RRGGBB entries found): '${_projects_cfg_palette_file}'" >&2
    unset _projects_cfg_palette_file _pcf_line _pcf_hex _pcf_rest
    return 1 2>/dev/null || exit 1
fi

readonly PGAI_DEFAULT_PALETTE
unset _projects_cfg_palette_file _pcf_line _pcf_hex _pcf_rest

# ---------------------------------------------------------------------------
# _projects_cfg_validate_color <color>
# Internal helper. Returns 0 if <color> is a valid #RRGGBB hex string;
# prints an error to stderr and returns 1 otherwise.
# ---------------------------------------------------------------------------
_projects_cfg_validate_color() {
    local color="$1"
    # Must be exactly #RRGGBB — seven chars, leading #, six hex digits.
    if [[ "$color" =~ ^#[0-9A-Fa-f]{6}$ ]]; then
        return 0
    fi
    # Diagnose the common mistake patterns.
    if [[ "$color" =~ ^0[xX][0-9A-Fa-f]{6}$ ]]; then
        echo "projects_cfg: invalid color '${color}': use #RRGGBB (not 0xRRGGBB)" >&2
    elif [[ "$color" =~ ^#[0-9A-Fa-f]{3}$ ]]; then
        echo "projects_cfg: invalid color '${color}': short-form #RGB is not supported; use full #RRGGBB" >&2
    else
        echo "projects_cfg: invalid color '${color}': expected #RRGGBB (e.g. #378ADD)" >&2
    fi
    return 1
}

# ---------------------------------------------------------------------------
# _projects_cfg_read_raw_color <name>
# Internal helper. Echoes the literal color field from projects.cfg for the
# named project, or empty string if the field is absent or not set.
# Does NOT validate format.
#
# Handles both INI format (dashboard_color field, from the last
# [project:NAME] section that sets it) and colon-legacy format (third
# colon-separated field). Format is detected automatically.
# ---------------------------------------------------------------------------
_projects_cfg_read_raw_color() {
    local name="$1"
    [[ -z "$name" ]] && return 1

    local cfg
    cfg="$(projects_cfg_path)"
    [[ -f "$cfg" ]] || return 1

    # Detect format with the registry's one format detector (projects.sh).
    local fmt
    fmt="$(projects_cfg_format "$cfg")"

    if [[ "$fmt" == "ini" ]]; then
        # INI format: dashboard_color from the last [project:NAME] section
        # that sets it (_projects_cfg_ini_value). Exit 0 with empty stdout
        # when the project is registered but no section of it sets
        # dashboard_color (callers such as projects_cfg_color must
        # distinguish between "project not registered" [exit 1] and
        # "registered but no explicit color" [exit 0, empty output] so that
        # the palette fallback path is taken).
        local rc=0
        _projects_cfg_ini_value "$cfg" "$name" dashboard_color || rc=$?
        case "$rc" in
            0|2) return 0 ;;
            *) return 1 ;;
        esac
    else
        # Colon-legacy format: third colon-separated field
        awk -F: -v n="$name" '
            /^[[:space:]]*#/ { next }
            /^[[:space:]]*$/ { next }
            {
                name_field = $1
                gsub(/^[[:space:]]+|[[:space:]]+$/, "", name_field)
                if (name_field == n) {
                    if (NF >= 3) {
                        color = $3
                        gsub(/^[[:space:]]+|[[:space:]]+$/, "", color)
                        if (color != "") { print color }
                    }
                    found = 1
                    exit
                }
            }
            END { if (!found) exit 1 }
        ' "$cfg"
    fi
}

# ---------------------------------------------------------------------------
# _projects_cfg_palette_index_for <name>
# Internal helper. Computes a deterministic palette index for the named
# project based on its registration order within projects.cfg (0-based,
# modulo palette size). Returns the index on stdout.
#
# Handles both INI and colon-legacy formats. In INI the order is
# projects_cfg_list's: a repeated section keeps its first position, and any
# other section -- a [project:...] one whose name the registry rejects, or
# one of another kind such as [defaults] -- ends the section before it, so
# a priority under it moves no project's slot.
# ---------------------------------------------------------------------------
_projects_cfg_palette_index_for() {
    local name="$1"
    [[ -z "$name" ]] && return 1

    local cfg
    cfg="$(projects_cfg_path)"
    [[ -f "$cfg" ]] || { echo "0"; return 0; }

    local palette_size="${#PGAI_DEFAULT_PALETTE[@]}"

    # Detect format
    local fmt
    fmt="$(projects_cfg_format "$cfg")"

    if [[ "$fmt" == "ini" ]]; then
        # INI format: collect sections in order, sort by priority, find ordinal of <name>
        awk -v n="$name" -v psz="$palette_size" "${PROJECTS_CFG_AWK_HEADER}"'
            /^[[:space:]]*#/ { next }
            /^[[:space:]]*$/ { next }
            (hdr = project_header($0)) != "" {
                cur = hdr
                # A repeated section keeps the first position, as in
                # projects_cfg_list.
                if (!(cur in section_line)) {
                    section_line[cur] = NR
                    section_order[++n_sections] = cur
                }
                next
            }
            # Any other section -- a [project:...] one with a rejected name,
            # or one of another kind such as [defaults] -- is no project:
            # its keys belong to none, as in projects_cfg_list.
            /^[[:space:]]*\[/ { cur = ""; next }
            /^[a-z_]+[[:space:]]*=/ && cur != "" {
                eq = index($0, "=")
                key = substr($0, 1, eq - 1)
                val = substr($0, eq + 1)
                gsub(/^[[:space:]]+|[[:space:]]+$/, "", key)
                gsub(/^[[:space:]]+|[[:space:]]+$/, "", val)
                if (key == "priority") prio[cur] = val
                next
            }
            END {
                for (i = 1; i <= n_sections; i++) {
                    nm = section_order[i]
                    # An absent or non-integer priority reads as 999, as
                    # in projects_cfg_list.
                    p  = (prio[nm] ~ /^[+-]?[0-9]+$/ ? prio[nm] : 999)
                    row[i] = sprintf("%010d\t%d\t%s", section_line[nm], p, nm)
                }
                # Bubble sort by priority then line order
                for (i = 1; i <= n_sections; i++) {
                    for (j = i+1; j <= n_sections; j++) {
                        split(row[i], ai, "\t")
                        split(row[j], aj, "\t")
                        if (ai[2]+0 > aj[2]+0 || (ai[2]+0 == aj[2]+0 && ai[1]+0 > aj[1]+0)) {
                            tmp = row[i]; row[i] = row[j]; row[j] = tmp
                        }
                    }
                }
                for (i = 1; i <= n_sections; i++) {
                    split(row[i], a, "\t")
                    if (a[3] == n) { print (i-1) % psz; exit 0 }
                }
                print 0
            }
        ' "$cfg"
    else
        # Colon-legacy format: original logic
        awk -F: -v n="$name" -v psz="$palette_size" '
            /^[[:space:]]*#/ { next }
            /^[[:space:]]*$/ { next }
            NF >= 2 {
                name_field = $1
                prio       = $2
                gsub(/^[[:space:]]+|[[:space:]]+$/, "", name_field)
                gsub(/^[[:space:]]+|[[:space:]]+$/, "", prio)
                if (prio == "") prio = 999
                row[NR] = sprintf("%010d\t%d\t%s", NR, prio, name_field)
            }
            END {
                n_rows = length(row)
                for (i = 1; i <= n_rows; i++) keys[i] = row[i]
                for (i = 1; i <= n_rows; i++) {
                    for (j = i+1; j <= n_rows; j++) {
                        split(keys[i], ai, "\t")
                        split(keys[j], aj, "\t")
                        if (ai[2]+0 > aj[2]+0 || (ai[2]+0 == aj[2]+0 && ai[1]+0 > aj[1]+0)) {
                            tmp = keys[i]; keys[i] = keys[j]; keys[j] = tmp
                        }
                    }
                }
                for (i = 1; i <= n_rows; i++) {
                    split(keys[i], a, "\t")
                    if (a[3] == n) { print (i-1) % psz; exit 0 }
                }
                print 0
            }
        ' "$cfg"
    fi
}

# ---------------------------------------------------------------------------
# projects_cfg_color <name>
# Echo the display_color for the named project.
#
# Resolution order:
#   1. If the projects.cfg line for <name> has a third field (color), echo it.
#   2. Otherwise, compute a deterministic palette fallback: the palette entry
#      at index = (registration ordinal of <name>) % 8.
#
# Returns 0 on success, 1 if <name> is not registered.
# ---------------------------------------------------------------------------
projects_cfg_color() {
    local name="$1"
    if [[ -z "$name" ]]; then
        echo "projects_cfg_color: project name is required" >&2
        return 1
    fi

    # Try to read an explicit color field.
    local raw_color
    raw_color="$(_projects_cfg_read_raw_color "$name")"
    local rc=$?

    # _projects_cfg_read_raw_color exits 1 when the project is not registered.
    if [[ $rc -ne 0 ]]; then
        return 1
    fi

    if [[ -n "$raw_color" ]]; then
        echo "$raw_color"
        return 0
    fi

    # No color field — fall back to palette.
    local idx
    idx="$(_projects_cfg_palette_index_for "$name")"
    echo "${PGAI_DEFAULT_PALETTE[$idx]}"
    return 0
}

# ---------------------------------------------------------------------------
# projects_cfg_next_color
# Echo the lowest-index palette entry not currently used as an effective color
# by any registered project.
#
# Algorithm:
#   1. Collect the effective color for every registered project.
#      - For projects with an explicit dashboard_color field (INI) or third
#        colon-field (colon-legacy), that explicit value is the effective color.
#      - For projects WITHOUT an explicit color, the effective color is the
#        implicit palette fallback: PGAI_DEFAULT_PALETTE[ordinal % palette_size]
#        where ordinal is the project's sorted registration position (0-based):
#        by priority, then file order. In INI an absent priority, or one that
#        is not a plain, optionally signed integer, reads as 999 -- the
#        reading _projects_cfg_palette_index_for (projects_cfg_color) uses.
#      Both explicit and implicit assignments are treated as "in use" so that
#      a newly registered project never silently duplicates an existing color.
#      (Counting only explicit colors would let projects migrated to INI format
#      without dashboard_color fields fail to reserve their implicit palette
#      slots — causing new projects to receive
#      palette[0] even when two existing projects already implicitly held
#      palette[0] and palette[1].)
#   2. Walk PGAI_DEFAULT_PALETTE in order; echo the first entry not in the
#      collected effective-color set.
#   3. If all 8 entries are consumed, wrap around to index 0 (with a warning
#      to stderr) — the operator should manually edit projects.cfg to
#      disambiguate.
#
# Returns 0 always (wraps, never errors).
# ---------------------------------------------------------------------------
projects_cfg_next_color() {
    local cfg
    cfg="$(projects_cfg_path)"

    # Collect effective colors currently in use (upper-cased for comparison).
    # Handles both INI (dashboard_color field + implicit palette fallback) and
    # colon-legacy (third colon-field + implicit palette fallback).
    local used_colors=()

    if [[ -f "$cfg" ]]; then
        local _fmt
        _fmt="$(projects_cfg_format "$cfg")"

        local palette_size="${#PGAI_DEFAULT_PALETTE[@]}"

        if [[ "$_fmt" == "ini" ]]; then
            # INI format: collect all section names and their explicit colors,
            # sorted by (priority, line-order) — same order as
            # _projects_cfg_palette_index_for, where an absent or non-integer
            # priority reads as 999 and sorts last by file order.  A project registered by two
            # sections is one project at its first header's line; a section
            # of another kind ends the one before it.  For each project, use the
            # explicit dashboard_color when present; otherwise compute the
            # implicit palette index from the project's sorted ordinal.
            local _awk_palette=""
            local _pi
            for (( _pi = 0; _pi < palette_size; _pi++ )); do
                _awk_palette="${_awk_palette} ${PGAI_DEFAULT_PALETTE[$_pi]}"
            done
            _awk_palette="${_awk_palette# }"   # trim leading space

            while IFS= read -r line; do
                used_colors+=("$line")
            done < <(awk -v psz="$palette_size" -v pal_str="$_awk_palette" "${PROJECTS_CFG_AWK_HEADER}"'
                BEGIN {
                    n_pal = split(pal_str, pal, " ")
                }
                /^[[:space:]]*#/ { next }
                /^[[:space:]]*$/ { next }
                (hdr = project_header($0)) != "" {
                    cur = hdr
                    # A repeated section keeps the first position, as in
                    # _projects_cfg_palette_index_for: one project, one slot.
                    if (!(cur in section_line)) {
                        section_line[cur] = NR
                        section_order[++n_sections] = cur
                    }
                    next
                }
                /^[[:space:]]*\[/ { cur = ""; next }
                cur && /^[a-z_]+[[:space:]]*=/ {
                    eq = index($0, "=")
                    key = substr($0, 1, eq - 1)
                    val = substr($0, eq + 1)
                    gsub(/^[[:space:]]+|[[:space:]]+$/, "", key)
                    gsub(/^[[:space:]]+|[[:space:]]+$/, "", val)
                    if (key == "priority") prio[cur] = val
                    if (key == "dashboard_color" && val != "") explicit_color[cur] = val
                }
                END {
                    # Build sort keys: "priority\tline_no\tname". An absent
                    # priority, or one that is not a plain, optionally signed
                    # integer (abc, 7abc), reads as 999, as in
                    # _projects_cfg_palette_index_for -- so the slots counted
                    # here are the slots projects_cfg_color hands out.
                    for (i = 1; i <= n_sections; i++) {
                        nm = section_order[i]
                        p  = (prio[nm] ~ /^[+-]?[0-9]+$/ ? prio[nm] : 999)
                        row[i] = sprintf("%d\t%010d\t%s", p+0, section_line[nm], nm)
                    }
                    # Bubble sort by (priority, line_order) ascending
                    for (i = 1; i <= n_sections; i++) {
                        for (j = i+1; j <= n_sections; j++) {
                            split(row[i], ai, "\t")
                            split(row[j], aj, "\t")
                            if (ai[1]+0 > aj[1]+0 ||
                                (ai[1]+0 == aj[1]+0 && ai[2]+0 > aj[2]+0)) {
                                tmp = row[i]; row[i] = row[j]; row[j] = tmp
                            }
                        }
                    }
                    # Emit the effective color for every project: explicit color
                    # when dashboard_color is set; implicit palette slot otherwise.
                    # Two-field rows (no dashboard_color) occupy their implicit
                    # palette slot — a new project must not receive a color already
                    # held implicitly by an existing project.
                    ordinal = 0
                    for (i = 1; i <= n_sections; i++) {
                        split(row[i], a, "\t")
                        nm = a[3]
                        if (nm in explicit_color) {
                            print toupper(explicit_color[nm])
                        } else {
                            print toupper(pal[(ordinal % psz) + 1])
                        }
                        ordinal++
                    }
                }
            ' "$cfg")
        else
            # Colon-legacy format: collect effective colors in the same way.
            # Projects with an explicit 3rd field use that; projects without one
            # use their implicit palette slot based on sorted registration order.
            local _awk_palette=""
            local _pi
            for (( _pi = 0; _pi < palette_size; _pi++ )); do
                _awk_palette="${_awk_palette} ${PGAI_DEFAULT_PALETTE[$_pi]}"
            done
            _awk_palette="${_awk_palette# }"

            while IFS= read -r line; do
                used_colors+=("$line")
            done < <(awk -F: -v psz="$palette_size" -v pal_str="$_awk_palette" '
                BEGIN {
                    n_pal = split(pal_str, pal, " ")
                }
                /^[[:space:]]*#/ { next }
                /^[[:space:]]*$/ { next }
                NF >= 2 {
                    name_field = $1
                    prio       = $2
                    gsub(/^[[:space:]]+|[[:space:]]+$/, "", name_field)
                    gsub(/^[[:space:]]+|[[:space:]]+$/, "", prio)
                    if (prio == "") prio = 999
                    row[NR] = sprintf("%d\t%010d\t%s", prio+0, NR, name_field)
                    if (NF >= 3) {
                        color = $3
                        gsub(/^[[:space:]]+|[[:space:]]+$/, "", color)
                        if (color != "") explicit_color[name_field] = color
                    }
                }
                END {
                    n_rows = 0
                    for (k in row) n_rows++
                    # Sort keys
                    for (i = 1; i <= NR; i++) if (!(i in row)) continue
                    # Collect all row keys
                    n_keys = 0
                    for (k in row) keys[++n_keys] = row[k]
                    # Bubble sort
                    for (i = 1; i <= n_keys; i++) {
                        for (j = i+1; j <= n_keys; j++) {
                            split(keys[i], ai, "\t")
                            split(keys[j], aj, "\t")
                            if (ai[1]+0 > aj[1]+0 ||
                                (ai[1]+0 == aj[1]+0 && ai[2]+0 > aj[2]+0)) {
                                tmp = keys[i]; keys[i] = keys[j]; keys[j] = tmp
                            }
                        }
                    }
                    # Emit the effective color for every project: explicit color
                    # when a third colon-field is set; implicit palette slot
                    # otherwise.  Two-field rows occupy their implicit palette slot
                    # — a new project must not receive a color already held
                    # implicitly by an existing project.
                    ordinal = 0
                    for (i = 1; i <= n_keys; i++) {
                        split(keys[i], a, "\t")
                        nm = a[3]
                        if (nm in explicit_color) {
                            print toupper(explicit_color[nm])
                        } else {
                            print toupper(pal[(ordinal % psz) + 1])
                        }
                        ordinal++
                    }
                }
            ' "$cfg")
        fi
    fi

    local palette_size="${#PGAI_DEFAULT_PALETTE[@]}"
    local i entry upper_entry

    # Walk palette; return the first entry not in the effective-color set.
    for (( i = 0; i < palette_size; i++ )); do
        entry="${PGAI_DEFAULT_PALETTE[$i]}"
        upper_entry="${entry^^}"   # Bash 4+ parameter expansion
        local found=0
        local used
        for used in "${used_colors[@]+"${used_colors[@]}"}"; do
            if [[ "$used" == "$upper_entry" ]]; then
                found=1
                break
            fi
        done
        if [[ $found -eq 0 ]]; then
            echo "$entry"
            return 0
        fi
    done

    # All palette entries consumed — wrap around with a warning.
    echo "projects_cfg_next_color: all palette entries in use; wrapping to index 0 — edit projects.cfg to disambiguate" >&2
    echo "${PGAI_DEFAULT_PALETTE[0]}"
    return 0
}

# ---------------------------------------------------------------------------
# projects_cfg_next_priority
# Echo max(existing priorities) + 1, or 1 when projects.cfg is empty.
#
# Handles both INI and colon-legacy formats. In INI only projects count, as
# projects_cfg_list lists them: a priority under a section of another kind
# ([defaults], or a [project:...] one whose name the registry rejects) is no
# project's, and a project registered by two sections counts once, with the
# priority its last section that sets one gives it (_projects_cfg_ini_value).
# A priority that is not a plain unsigned integer is skipped.
# Returns 0 always.
# ---------------------------------------------------------------------------
projects_cfg_next_priority() {
    local cfg
    cfg="$(projects_cfg_path)"

    if [[ ! -f "$cfg" ]]; then
        echo "1"
        return 0
    fi

    # Detect format
    local fmt
    fmt="$(projects_cfg_format "$cfg")"

    local max_prio
    if [[ "$fmt" == "ini" ]]; then
        max_prio="$(awk "${PROJECTS_CFG_AWK_HEADER}"'
            /^[[:space:]]*#/ { next }
            /^[[:space:]]*$/ { next }
            (hdr = project_header($0)) != "" { cur = hdr; next }
            /^[[:space:]]*\[/ { cur = ""; next }
            cur != "" && /^[a-z_]+[[:space:]]*=/ {
                eq = index($0, "=")
                key = substr($0, 1, eq - 1)
                val = substr($0, eq + 1)
                gsub(/^[[:space:]]+|[[:space:]]+$/, "", key)
                gsub(/^[[:space:]]+|[[:space:]]+$/, "", val)
                if (key == "priority") prio[cur] = val
            }
            END {
                for (nm in prio) {
                    if (prio[nm] ~ /^[0-9]+$/ && (max == "" || prio[nm]+0 > max+0)) max = prio[nm]
                }
                print (max == "" ? 0 : max + 0)
            }
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

    echo $(( max_prio + 1 ))
    return 0
}
