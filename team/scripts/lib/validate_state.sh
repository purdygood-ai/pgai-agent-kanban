#!/usr/bin/env bash
# team/scripts/lib/validate_state.sh
#
# Validates that a kanban task state string is one of the six canonical states.
# Exits 0 for valid states (no output).
# Exits 1 with an error on stderr for invalid states.
#
# The six canonical states:
#   BACKLOG, WAITING, WORKING, BLOCKED, DONE, WONT-DO
#
# Usage:
#   validate_state.sh <STATE>            # positional argument
#   echo "STATE" | validate_state.sh    # stdin
#   validate_state.sh                    # reads from stdin (interactive or piped)
#
# Examples:
#   validate_state.sh WORKING            # exits 0, no output
#   echo "FOO" | validate_state.sh       # exits 1, stderr: "ERROR: Unknown state..."
#
# Note: this script is a standalone helper. It is not sourced by other scripts.
# Call it directly whenever state validation is needed.

set -euo pipefail

# ---------------------------------------------------------------------------
# Determine the state token: positional arg takes precedence over stdin.
# ---------------------------------------------------------------------------
if [[ $# -ge 1 ]]; then
    state="$1"
else
    # Read from stdin; strip leading/trailing whitespace and newlines.
    IFS= read -r state || true
    state="${state#"${state%%[![:space:]]*}"}"
    state="${state%"${state##*[![:space:]]}"}"
fi

# ---------------------------------------------------------------------------
# Validate
# ---------------------------------------------------------------------------
case "$state" in
    BACKLOG|WAITING|WORKING|BLOCKED|DONE|WONT-DO)
        # Valid canonical state — exit silently.
        exit 0
        ;;
    "")
        printf 'ERROR: validate_state.sh: no state supplied (empty input)\n' >&2
        exit 1
        ;;
    *)
        printf "ERROR: Unknown state '%s' — not in the canonical six (BACKLOG, WAITING, WORKING, BLOCKED, DONE, WONT-DO)\n" "$state" >&2
        exit 1
        ;;
esac
