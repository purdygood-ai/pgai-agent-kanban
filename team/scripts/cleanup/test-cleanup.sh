#!/usr/bin/env bash
# team/scripts/cleanup/test-cleanup.sh
#
# Garbage-collect leaked /tmp/tmp.* directories from bash integration tests.
#
# Bash test functions that exited abnormally before the EXIT trap fired (e.g.,
# the process was SIGKILLed) may leave behind mktemp-created directories.
# This script removes any /tmp/tmp.* directory older than 60 minutes.
#
# Usage:
#   bash team/scripts/cleanup/test-cleanup.sh          # dry-run: list what would be removed
#   bash team/scripts/cleanup/test-cleanup.sh --delete  # actually remove them
#
# Options:
#   --delete    Remove the stale directories (default: dry run, list only).
#   --help, -h  Print this usage and exit 0 without scanning or removing anything.
#
# Exit codes:
#   0 — scan finished (informational output only), or --help
#   1 — unknown argument; nothing is scanned or removed

set -euo pipefail

# shellcheck source=../lib/header_help.sh
source "$(dirname "${BASH_SOURCE[0]}")/../lib/header_help.sh"
# shellcheck source=../lib/error_contract.sh
source "$(dirname "${BASH_SOURCE[0]}")/../lib/error_contract.sh"

DRY_RUN=true
for arg in "$@"; do
    case "$arg" in
        --help|-h)
            header_help_print "${BASH_SOURCE[0]}"
            exit 0
            ;;
        --delete)
            DRY_RUN=false
            ;;
        *)
            # BLOCK-WORTHY: an argument this script does not define (a
            # mistyped --delete, say) may mean the operator expected a
            # different run than the default dry run; refused before
            # anything is scanned or removed rather than ignored.
            pgai_emit_error_contract "test_cleanup.unknown_argument" \
                "test-cleanup.sh: unknown argument '$arg'. Usage: test-cleanup.sh [--delete]. Accepted: --delete, --help. Nothing was scanned or removed." \
                "bash $(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/test-cleanup.sh"
            exit 1
            ;;
    esac
done

STALE_DIRS=$(find /tmp -maxdepth 1 -type d -name 'tmp.*' -mmin +60 2>/dev/null || true)

if [[ -z "$STALE_DIRS" ]]; then
    echo "No stale /tmp/tmp.* directories found (older than 60 minutes)."
    exit 0
fi

COUNT=$(echo "$STALE_DIRS" | wc -l)

if $DRY_RUN; then
    echo "Dry run: $COUNT stale /tmp/tmp.* director$([ "$COUNT" -eq 1 ] && echo y || echo ies) found (older than 60 minutes)."
    echo "Run with --delete to remove them."
    echo ""
    echo "$STALE_DIRS"
else
    echo "Removing $COUNT stale /tmp/tmp.* director$([ "$COUNT" -eq 1 ] && echo y || echo ies) older than 60 minutes..."
    find /tmp -maxdepth 1 -type d -name 'tmp.*' -mmin +60 -exec rm -rf {} + 2>/dev/null || true
    echo "Done."
fi
