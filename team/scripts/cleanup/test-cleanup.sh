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
# Exit code: 0 always (informational output only).

set -euo pipefail

DRY_RUN=true
if [[ "${1:-}" == "--delete" ]]; then
    DRY_RUN=false
fi

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
