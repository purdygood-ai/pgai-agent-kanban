#!/usr/bin/env bash
# team/scripts/switch-provider.sh
# Switch the active LLM provider for the pgai-agent-kanban framework.
#
# Usage:
#   switch-provider.sh --provider <provider>
#   switch-provider.sh [--help|-h]
#
# Options:
#   --provider NAME     One of: claude, codex, gemini (required)
#   --help, -h          Print this help and exit
#
# Behaviour:
#   1. Validates that --provider is a recognized value.
#   2. Checks that the target provider's CLI binary is installed in PATH.
#      Refuses to switch (exits non-zero) when the CLI is not found.
#   3. Updates kanban.cfg [providers] active in place (preserves comments
#      and other keys via section-scoped awk replacement).
#   4. Prints a confirmation message showing the old and new provider values.
#
# Provider-to-CLI mapping:
#   claude  → claude
#   codex   → codex
#   gemini  → gemini
#
# $KANBAN_ROOT defaults to $PGAI_AGENT_KANBAN_ROOT_PATH, then falls
# back to $HOME/pgai_agent_kanban when that variable is not set.
#
# Exit codes:
#   0   Provider written successfully.
#   1   Invalid provider name, CLI not found, or write failure.

set -euo pipefail

# ---------------------------------------------------------------------------
# Resolve kanban root and source operator_args.sh
# ---------------------------------------------------------------------------
KANBAN_ROOT="${PGAI_AGENT_KANBAN_ROOT_PATH:-${HOME}/pgai_agent_kanban}"

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=lib/operator_args.sh
source "${SCRIPT_DIR}/lib/operator_args.sh"

# Declared flag vocabulary: ALL flags this command accepts or consumes.
# provider is included so operator_args_validate_known rejects any unlisted flag.
OPERATOR_VALID_FLAGS=(provider help)

# ---------------------------------------------------------------------------
# Parse arguments
# ---------------------------------------------------------------------------
# Value-taking: provider.
# Boolean: help.
argparse_parse \
    --value-flags "provider" \
    -- "$@"

# Emit error for value-taking flags given with no value.
if argparse_missing "provider"; then
    echo "switch-provider.sh: error: --provider requires a value" >&2
    exit 1
fi

# Handle --help / -h.
if argparse_has "help" || argparse_has "h"; then
    operator_args_render_help_for_flags "switch-provider.sh" \
        "Switch the active LLM provider for the kanban framework." \
        OPERATOR_VALID_FLAGS \
        "" \
        "  --provider accepts: claude, codex, gemini  (required)"
    exit 0
fi

# Reject unexpected positional arguments (hard-cut: no positional provider).
if [[ "${#ARGPARSE_POSITIONAL[@]}" -gt 0 ]]; then
    echo "switch-provider.sh: error: unexpected positional argument: ${ARGPARSE_POSITIONAL[0]}" >&2
    echo "Usage: switch-provider.sh --provider <claude|codex|gemini>" >&2
    exit 1
fi

# Reject any flag not in the declared vocabulary.
operator_args_validate_known "$(basename "$0")" OPERATOR_VALID_FLAGS || exit 1

# ---------------------------------------------------------------------------
# Extract --provider value
# ---------------------------------------------------------------------------
if ! argparse_has "provider"; then
    echo "switch-provider.sh: error: --provider is required" >&2
    echo "Usage: switch-provider.sh --provider <claude|codex|gemini>" >&2
    exit 1
fi

REQUESTED_PROVIDER="${ARGPARSE_FLAGS[provider]}"

if [[ -z "$REQUESTED_PROVIDER" ]]; then
    echo "switch-provider.sh: error: --provider requires a non-empty value" >&2
    exit 1
fi

# ---------------------------------------------------------------------------
# Validate provider name (case-insensitive input; canonical lowercase output)
# ---------------------------------------------------------------------------
PROVIDER_LC=$(printf '%s' "$REQUESTED_PROVIDER" | tr '[:upper:]' '[:lower:]')

case "$PROVIDER_LC" in
    claude|codex|gemini)
        ;;
    *)
        echo "switch-provider.sh: error: unrecognized provider '${REQUESTED_PROVIDER}'" >&2
        echo "  Valid providers: claude, codex, gemini" >&2
        exit 1
        ;;
esac

# ---------------------------------------------------------------------------
# Map provider name to the CLI binary that must be installed
# ---------------------------------------------------------------------------
case "$PROVIDER_LC" in
    claude)  CLI_BIN="claude"  ;;
    codex)   CLI_BIN="codex"   ;;
    gemini)  CLI_BIN="gemini"  ;;
esac

# ---------------------------------------------------------------------------
# Refuse to switch if the target provider's CLI is not in PATH
# ---------------------------------------------------------------------------
if ! command -v "$CLI_BIN" >/dev/null 2>&1; then
    echo "switch-provider.sh: error: cannot switch to provider '${PROVIDER_LC}'" >&2
    echo "  CLI binary '${CLI_BIN}' not found in PATH" >&2
    echo "  Install ${CLI_BIN} before switching to this provider." >&2
    exit 1
fi

# ---------------------------------------------------------------------------
# Read current provider from kanban.cfg [providers] active
# ---------------------------------------------------------------------------
# kanban.cfg [providers] active is the single source of truth. The legacy
# $KANBAN_ROOT/active-provider file is no longer read or written.
KANBAN_CFG="${KANBAN_ROOT}/kanban.cfg"
if [[ ! -f "$KANBAN_CFG" ]]; then
    echo "switch-provider.sh: error: kanban.cfg not found at ${KANBAN_CFG}" >&2
    echo "  Run install.sh first to seed the configuration file." >&2
    exit 1
fi

# Source ini_parser for read_ini.
INI_PARSER="${KANBAN_ROOT}/scripts/lib/ini_parser.sh"
if [[ ! -f "$INI_PARSER" ]]; then
    echo "switch-provider.sh: error: ini_parser.sh not found at ${INI_PARSER}" >&2
    exit 1
fi
# shellcheck source=/dev/null
source "$INI_PARSER"

_MISSING_ACTIVE="__pgai_switch_provider_missing_active__"
OLD_PROVIDER="$(read_ini "$KANBAN_CFG" providers active "$_MISSING_ACTIVE")"
if [[ "$OLD_PROVIDER" == "$_MISSING_ACTIVE" ]]; then
    echo "switch-provider.sh: error: [providers] section in kanban.cfg has no 'active' key" >&2
    echo "  Edit ${KANBAN_CFG} manually and add: active = ${PROVIDER_LC}" >&2
    exit 1
fi
[[ -z "$OLD_PROVIDER" ]] && OLD_PROVIDER="(none)"

# ---------------------------------------------------------------------------
# Write the new provider value to kanban.cfg [providers] active
# ---------------------------------------------------------------------------
# Rewrite only a real data key in [providers]. Comment lines that mention
# "active" are ignored, and every unrelated key/section is printed unchanged.
_TMP_CFG="$(mktemp "${KANBAN_CFG}.XXXXXX")" || {
    echo "switch-provider.sh: error: failed to create temp file beside '${KANBAN_CFG}'" >&2
    exit 1
}

if awk -v new="$PROVIDER_LC" '
    BEGIN {
        in_section = 0
        replaced = 0
    }

    /^[ \t]*\[/ {
        secname = $0
        sub(/^[ \t]*\[[ \t]*/, "", secname)
        sub(/[ \t]*\].*$/, "", secname)
        in_section = (secname == "providers")
        print
        next
    }

    in_section && !replaced {
        if ($0 ~ /^[ \t]*$/ || $0 ~ /^[ \t]*[#;]/) {
            print
            next
        }

        eq_pos = index($0, "=")
        if (eq_pos > 0) {
            raw_key = substr($0, 1, eq_pos - 1)
            gsub(/^[ \t]+|[ \t]+$/, "", raw_key)
            if (raw_key == "active") {
                print "active = " new
                replaced = 1
                next
            }
        }
    }

    { print }

    END {
        if (!replaced) {
            exit 42
        }
    }
' "$KANBAN_CFG" > "$_TMP_CFG"; then
    if ! mv "$_TMP_CFG" "$KANBAN_CFG"; then
        echo "switch-provider.sh: error: failed to update '${KANBAN_CFG}'" >&2
        rm -f "$_TMP_CFG"
        exit 1
    fi
else
    _AWK_RC=$?
    rm -f "$_TMP_CFG"
    if [[ "$_AWK_RC" -eq 42 ]]; then
        echo "switch-provider.sh: error: [providers] section in kanban.cfg has no 'active' key" >&2
        echo "  Edit ${KANBAN_CFG} manually and add: active = ${PROVIDER_LC}" >&2
    else
        echo "switch-provider.sh: error: failed to rewrite '${KANBAN_CFG}'" >&2
    fi
    exit 1
fi

# ---------------------------------------------------------------------------
# Confirm
# ---------------------------------------------------------------------------
echo "Active provider switched: ${OLD_PROVIDER} → ${PROVIDER_LC}"
echo "  Updated: ${KANBAN_CFG} [providers] active"
echo "  CLI confirmed in PATH: ${CLI_BIN}"
echo ""
echo "The change takes effect on the next cron tick (typically 1-2 minutes)."
