#!/usr/bin/env bash
# team/scripts/lib/ini_parser.sh
# Minimal awk-based bash INI parser for the pgai-agent-kanban framework.
#
# Source this file to get read_ini and write_ini in your shell:
#   source "$(dirname "${BASH_SOURCE[0]}")/ini_parser.sh"
#
# Functions
# ---------
#   read_ini  <file> <section> <key> [default]
#     Read a value from an INI file.
#     Returns the value (with leading/trailing whitespace stripped).
#     Returns default (or empty string) with exit 0 if section/key is absent.
#     Returns exit 1 and writes to stderr only on file I/O errors (unreadable).
#     Comments (#, ;) and blank lines are ignored.
#     An inline comment -- whitespace followed by '#' or ';' after the value
#     ("key = 300 # five minutes") -- is stripped from the value. A value
#     that itself BEGINS with '#' or ';' is kept as-is (a status glyph of
#     "#" is a value, not a comment), because the strip requires at least
#     one whitespace character between the value and the comment marker.
#     A carriage return ending a line (a file saved with CRLF endings) is
#     stripped before any other handling, so every key, section, and
#     value reads the same as it would from an LF file.
#     Section names may contain dots (e.g., [project.foo]).
#     Values may contain '=' characters.
#
#   list_ini_sections <file>
#     Print every section name in the file, one per line, in file order
#     (duplicates included). Prints nothing for a missing file.
#
#   list_ini_keys <file>
#     Print every key=value line as "<section><TAB><key>", one per line, in
#     file order. A key above the first section header carries an empty
#     section name. Applies the same comment, CRLF, and whitespace rules as
#     read_ini, so what this lists is exactly what read_ini would resolve.
#     Prints nothing for a missing file.
#
#   write_ini <file> <section> <key> <value>
#     Update an existing key or add a new key=value under [section].
#     Creates the section at end-of-file if it does not exist.
#     Creates the file if it does not exist.
#     Writes to stderr and returns non-zero on write errors.
#
# Safety invariants:
#   - No top-level side effects when sourced — only function definitions.
#   - Functions do not modify the caller's environment (all locals).
#   - Failure writes go to stderr; stdout carries only the result value.

# ---------------------------------------------------------------------------
# read_ini <file> <section> <key> [default]
# ---------------------------------------------------------------------------
read_ini() {
    local file="$1"
    local section="$2"
    local key="$3"
    local default="${4:-}"

    # File I/O guard: missing file is not an error — return default.
    if [[ ! -e "$file" ]]; then
        printf '%s' "$default"
        return 0
    fi

    if [[ ! -r "$file" ]]; then
        printf 'read_ini: cannot read file: %s\n' "$file" >&2
        return 1
    fi

    # Use a sentinel prefix "FOUND:" so we can distinguish:
    #   - key found with blank value  -> awk prints "FOUND:"
    #   - key found with value "x"   -> awk prints "FOUND:x"
    #   - key not found              -> awk prints nothing (empty string)
    local raw_output
    raw_output=$(awk \
        -v target_section="$section" \
        -v target_key="$key" \
        '
        # A CRLF-saved file ends every line in \r; drop it before any other
        # rule looks at the line so keys, sections, and values match an LF
        # file byte-for-byte.
        { sub(/\r$/, "") }

        # Track whether we are inside the target section.
        /^\[/ {
            # Strip brackets and surrounding whitespace to get section name.
            # Handles: [section], [ section ], [section.with.dots]
            secname = $0
            sub(/^\[[ \t]*/, "", secname)
            sub(/[ \t]*\].*$/, "", secname)
            in_section = (secname == target_section)
            next
        }

        # Skip blank lines and comments (# and ;) everywhere.
        /^[ \t]*$/ { next }
        /^[ \t]*[#;]/ { next }

        # Inside the target section, look for the target key.
        in_section {
            # Split on the first = only (value may contain = characters).
            eq_pos = index($0, "=")
            if (eq_pos == 0) { next }

            raw_key = substr($0, 1, eq_pos - 1)
            raw_val = substr($0, eq_pos + 1)

            # Strip leading and trailing whitespace from key.
            gsub(/^[ \t]+|[ \t]+$/, "", raw_key)
            # Strip leading whitespace from value, then an inline comment
            # (whitespace followed by # or ;), then trailing whitespace.
            # Requiring the whitespace means a value that starts with # or ;
            # survives intact.
            sub(/^[ \t]+/, "", raw_val)
            sub(/[ \t]+[#;].*$/, "", raw_val)
            sub(/[ \t]+$/, "", raw_val)

            if (raw_key == target_key) {
                # Print sentinel + value so blank values are distinguishable
                # from "not found" (bash command substitution strips trailing
                # newlines, making empty output ambiguous without a sentinel).
                printf "FOUND:%s", raw_val
                exit 0
            }
        }
        ' "$file")

    if [[ "$raw_output" == FOUND:* ]]; then
        # Key was found; strip sentinel and return the value (may be empty).
        printf '%s' "${raw_output#FOUND:}"
    else
        # Key was not found; return default (may be empty).
        printf '%s' "$default"
    fi
    return 0
}

# ---------------------------------------------------------------------------
# list_ini_sections <file>
# Prints every section name, one per line, in file order.
# ---------------------------------------------------------------------------
list_ini_sections() {
    local file="$1"

    if [[ ! -e "$file" ]]; then
        return 0
    fi
    if [[ ! -r "$file" ]]; then
        printf 'list_ini_sections: cannot read file: %s\n' "$file" >&2
        return 1
    fi

    awk '
        { sub(/\r$/, "") }
        /^\[/ {
            secname = $0
            sub(/^\[[ \t]*/, "", secname)
            sub(/[ \t]*\].*$/, "", secname)
            print secname
        }
        ' "$file"
}

# ---------------------------------------------------------------------------
# list_ini_keys <file>
# Prints "<section><TAB><key>" for every key=value line, in file order,
# under the same comment / CRLF / whitespace rules read_ini applies.
# ---------------------------------------------------------------------------
list_ini_keys() {
    local file="$1"

    if [[ ! -e "$file" ]]; then
        return 0
    fi
    if [[ ! -r "$file" ]]; then
        printf 'list_ini_keys: cannot read file: %s\n' "$file" >&2
        return 1
    fi

    awk '
        BEGIN { secname = "" }
        { sub(/\r$/, "") }
        /^\[/ {
            secname = $0
            sub(/^\[[ \t]*/, "", secname)
            sub(/[ \t]*\].*$/, "", secname)
            next
        }
        /^[ \t]*$/ { next }
        /^[ \t]*[#;]/ { next }
        {
            eq_pos = index($0, "=")
            if (eq_pos == 0) { next }
            raw_key = substr($0, 1, eq_pos - 1)
            gsub(/^[ \t]+|[ \t]+$/, "", raw_key)
            if (raw_key == "") { next }
            printf "%s\t%s\n", secname, raw_key
        }
        ' "$file"
}

# ---------------------------------------------------------------------------
# write_ini <file> <section> <key> <value>
# ---------------------------------------------------------------------------
write_ini() {
    local file="$1"
    local section="$2"
    local key="$3"
    local value="$4"

    # Create the file if it does not exist.
    if [[ ! -e "$file" ]]; then
        if ! touch "$file" 2>/dev/null; then
            printf 'write_ini: cannot create file: %s\n' "$file" >&2
            return 1
        fi
    fi

    if [[ ! -w "$file" ]]; then
        printf 'write_ini: cannot write to file: %s\n' "$file" >&2
        return 1
    fi

    # Use awk to rewrite the file atomically via a temp file.
    # Strategy:
    #   - If section and key both exist: replace the value line.
    #   - If section exists but key is absent: insert after the section header.
    #   - If section is absent: append [section] + key=value at end of file.
    local tmpfile
    tmpfile=$(mktemp "${file}.XXXXXX") || {
        printf 'write_ini: cannot create temp file for: %s\n' "$file" >&2
        return 1
    }

    awk \
        -v target_section="$section" \
        -v target_key="$key" \
        -v new_value="$value" \
        '
        BEGIN {
            in_section   = 0
            found_key    = 0
            section_seen = 0
        }

        /^\[/ {
            # Entering a new section.
            # If we were in the target section and key was not yet found,
            # inject it now (just before the next section header).
            if (in_section && !found_key) {
                print target_key "=" new_value
                found_key = 1
            }
            secname = $0
            sub(/^\[[ \t]*/, "", secname)
            sub(/[ \t]*\].*$/, "", secname)
            in_section   = (secname == target_section)
            if (in_section) section_seen = 1
            print
            next
        }

        in_section && !found_key {
            # Skip blank lines and comment lines without consuming them prematurely.
            if (/^[ \t]*$/ || /^[ \t]*[#;]/) {
                print
                next
            }
            eq_pos = index($0, "=")
            if (eq_pos > 0) {
                raw_key = substr($0, 1, eq_pos - 1)
                gsub(/^[ \t]+|[ \t]+$/, "", raw_key)
                if (raw_key == target_key) {
                    # Replace this line.
                    print target_key "=" new_value
                    found_key = 1
                    next
                }
            }
        }

        { print }

        END {
            if (!found_key) {
                if (!section_seen) {
                    # Section does not exist — append it.
                    print ""
                    print "[" target_section "]"
                }
                print target_key "=" new_value
            }
        }
        ' "$file" > "$tmpfile" && mv "$tmpfile" "$file" || {
            rm -f "$tmpfile"
            printf 'write_ini: failed to write: %s\n' "$file" >&2
            return 1
        }
    return 0
}
