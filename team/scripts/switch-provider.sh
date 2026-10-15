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
#   Thin adapter: parses the flags via operator_args.sh and hands the
#   provider to the Python switch_provider function in
#   pgai_agent_kanban.ops.write (the same function
#   POST /operations/switch-provider calls), which:
#   1. Validates that --provider is a recognized value (case-insensitive),
#      refusing an unknown one with the dual-channel error/message/remedy
#      block.
#   2. Checks that the target provider's CLI binary is installed in PATH.
#      Refuses to switch (exits non-zero) when the CLI is not found.
#   3. Updates kanban.cfg [providers] active in place: comment lines that
#      mention "active" and every other key and section keep their bytes,
#      and the file is replaced atomically with its permission mode
#      unchanged.
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
# shellcheck source=lib/env_bootstrap.sh
source "$(dirname "${BASH_SOURCE[0]}")/lib/env_bootstrap.sh"

# ---------------------------------------------------------------------------
# Resolve kanban root and source operator_args.sh
# ---------------------------------------------------------------------------
KANBAN_ROOT="${PGAI_AGENT_KANBAN_ROOT_PATH}"

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=lib/operator_args.sh
source "${SCRIPT_DIR}/lib/operator_args.sh"
# shellcheck source=lib/pp_run_ops.sh
source "${SCRIPT_DIR}/lib/pp_run_ops.sh"

# Declared flag vocabulary: ALL flags this command accepts or consumes.
# provider is included so operator_args_validate_known rejects any unlisted flag.
OPERATOR_VALID_FLAGS=(provider help)

# ---------------------------------------------------------------------------
# Parse arguments
# ---------------------------------------------------------------------------
# Value-taking: provider.
# Boolean: help.
operator_args_argparse \
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
# Validate, check the CLI, and write kanban.cfg via the Python library
# ---------------------------------------------------------------------------
# kanban.cfg [providers] active is the single source of truth. The legacy
# $KANBAN_ROOT/active-provider file is no longer read or written.
pp_run_ops pgai_agent_kanban.ops switch_provider "${KANBAN_ROOT}" "${REQUESTED_PROVIDER}" || exit 1
exit 0
