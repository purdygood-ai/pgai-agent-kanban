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
#   PGAI_DEFAULT_PALETTE            — read-only array of 8 default colors
#   projects_cfg_color  <name>      — echo display_color for <name>;
#                                     falls back to next deterministic palette
#                                     entry when the color field is absent
#   projects_cfg_next_color         — echo the lowest-index palette entry not
#                                     currently used by any registered project;
#                                     wraps to index 0 when all 8 are consumed
#   projects_cfg_next_priority      — echo max(existing priorities) + 1,
#                                     or 1 when projects.cfg is empty
#
# Color format rules
# ------------------
#   Accepted:   #RRGGBB  (e.g. #378ADD — six hex digits, case-insensitive)
#   Rejected:   0xRRGGBB  (must use # prefix — not 0x)
#   Rejected:   #RGB      (three-digit short form not supported)
#   Validation  happens only at write/check time; this library reads and
#               echoes whatever is stored. Format checks belong in
#               create-project.sh and add-project.sh.
#
# Source order
# ------------
# This library may be sourced independently, but callers that also need the
# full projects.sh API should source projects.sh, which sources this file.
# This file does NOT source projects.sh to avoid circular dependencies.
# It calls projects_cfg_path() — callers must ensure projects.sh (or at
# minimum its projects_cfg_path definition) is sourced first.

# ---------------------------------------------------------------------------
# PGAI_DEFAULT_PALETTE — eight visually distinct project colors.
# Order: blue, teal, coral, amber, pink, purple, green, gray.
# This array is the single source of truth for the default palette —
# do not duplicate it elsewhere.
# ---------------------------------------------------------------------------
PGAI_DEFAULT_PALETTE=(
    "#378ADD" # blue   (~210°)
    "#D85A30" # coral  (~15°)    ← jump across to warm
    "#639922" # green  (~90°)    ← jump to green
    "#D4537E" # pink   (~340°)   ← jump to magenta
    "#1D9E75" # teal   (~165°)   ← jump to cyan-green
    "#BA7517" # amber  (~40°)    ← jump to gold
    "#7F77DD" # purple (~245°)   ← jump to violet
    "#888780" # gray            (last resort)
)
readonly PGAI_DEFAULT_PALETTE

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
# Handles both INI format (dashboard_color field) and colon-legacy format
# (third colon-separated field). Format is detected automatically.
# ---------------------------------------------------------------------------
_projects_cfg_read_raw_color() {
    local name="$1"
    [[ -z "$name" ]] && return 1

    local cfg
    cfg="$(projects_cfg_path)"
    [[ -f "$cfg" ]] || return 1

    # Detect format: check first non-comment, non-blank line
    local fmt
    fmt="$(awk '
        /^[[:space:]]*#/ { next }
        /^[[:space:]]*$/ { next }
        {
            if (substr($0, 1, 1) == "[") { print "ini" }
            else { print "colon-legacy" }
            found = 1
            exit
        }
        END { if (!found) print "colon-legacy" }
    ' "$cfg")"

    if [[ "$fmt" == "ini" ]]; then
        # INI format: look for dashboard_color under [project:NAME] section.
        # Exit 0 with empty stdout when the section exists but has no dashboard_color
        # (callers such as projects_cfg_color must distinguish between
        # "project not registered" [exit 1] and "registered but no explicit color"
        # [exit 0, empty output] so that the palette fallback path is taken).
        awk -v n="$name" '
            /^[[:space:]]*#/ { next }
            /^[[:space:]]*$/ { next }
            /^\[project:[a-zA-Z0-9_-]+\]$/ {
                cur = substr($0, 10, length($0) - 10)
                if (cur == n) section_found = 1
                next
            }
            /^[[:space:]]*\[/ { cur = ""; next }
            /^[a-z_]+[[:space:]]*=/ && cur == n {
                eq = index($0, "=")
                key = substr($0, 1, eq - 1)
                val = substr($0, eq + 1)
                gsub(/^[[:space:]]+|[[:space:]]+$/, "", key)
                gsub(/^[[:space:]]+|[[:space:]]+$/, "", val)
                if (key == "dashboard_color") { print val; color_found=1; exit }
            }
            END {
                if (color_found) exit 0   # color printed above
                if (section_found) exit 0 # section exists, no explicit color — return empty
                exit 1                    # section not found — project not registered
            }
        ' "$cfg"
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
# Handles both INI and colon-legacy formats.
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
    fmt="$(awk '
        /^[[:space:]]*#/ { next }
        /^[[:space:]]*$/ { next }
        {
            if (substr($0, 1, 1) == "[") { print "ini" }
            else { print "colon-legacy" }
            found = 1
            exit
        }
        END { if (!found) print "colon-legacy" }
    ' "$cfg")"

    if [[ "$fmt" == "ini" ]]; then
        # INI format: collect sections in order, sort by priority, find ordinal of <name>
        awk -v n="$name" -v psz="$palette_size" '
            /^[[:space:]]*#/ { next }
            /^[[:space:]]*$/ { next }
            /^\[project:[a-zA-Z0-9_-]+\]$/ {
                cur = substr($0, 10, length($0) - 10)
                section_line[cur] = NR
                section_order[++n_sections] = cur
                next
            }
            /^[[:space:]]*\[/ { next }
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
                    p  = (prio[nm] == "" ? 999 : prio[nm])
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
#        where ordinal is the project's sorted registration position (0-based).
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
        _fmt="$(awk '
            /^[[:space:]]*#/ { next }
            /^[[:space:]]*$/ { next }
            {
                if (substr($0, 1, 1) == "[") { print "ini" }
                else { print "colon-legacy" }
                found = 1
                exit
            }
            END { if (!found) print "colon-legacy" }
        ' "$cfg")"

        local palette_size="${#PGAI_DEFAULT_PALETTE[@]}"

        if [[ "$_fmt" == "ini" ]]; then
            # INI format: collect all section names and their explicit colors,
            # sorted by (priority, line-order) — same order as
            # _projects_cfg_palette_index_for.  For each project, use the
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
            done < <(awk -v psz="$palette_size" -v pal_str="$_awk_palette" '
                BEGIN {
                    n_pal = split(pal_str, pal, " ")
                }
                /^[[:space:]]*#/ { next }
                /^[[:space:]]*$/ { next }
                /^\[project:[a-zA-Z0-9_-]+\]$/ {
                    cur = substr($0, 10, length($0) - 10)
                    section_line[cur] = NR
                    section_order[++n_sections] = cur
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
                    # Build sort keys: "priority\tline_no\tname"
                    for (i = 1; i <= n_sections; i++) {
                        nm = section_order[i]
                        p  = (prio[nm] == "" ? 999 : prio[nm])
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
# Handles both INI and colon-legacy formats.
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
    fmt="$(awk '
        /^[[:space:]]*#/ { next }
        /^[[:space:]]*$/ { next }
        {
            if (substr($0, 1, 1) == "[") { print "ini" }
            else { print "colon-legacy" }
            found = 1
            exit
        }
        END { if (!found) print "colon-legacy" }
    ' "$cfg")"

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

    echo $(( max_prio + 1 ))
    return 0
}
