#!/usr/bin/env bash
# team/verification/verify_script_exec_bits.sh
#
# Regression guard: assert that every committed .sh file under the canonical
# shell-script directories has mode 100755 (exec bit set) in the git index.
# CODER agents that Write/Edit new shell scripts may produce mode 0644 by
# default; this guard catches any such regression before it ships to a release.
#
# Directories scanned (relative to repo root):
#   team/scripts/       (recursive, *.sh only)
#   team/verification/  (recursive, *.sh only)
#
# Non-.sh files in those directories are not checked.
#
# Checks committed modes (via `git ls-files --stage`), not filesystem modes, so
# the guard works correctly against a fresh clone where filesystem modes may
# differ from committed modes.
#
# Usage:
#   verify_script_exec_bits.sh [--repo-root <path>]
#
# Options:
#   --repo-root <path>   Path to the git repository root.
#                        Default: auto-detected via `git rev-parse --show-toplevel`
#
# Exit codes:
#   0 — all .sh files in scanned directories have mode 100755
#   1 — one or more .sh files do not have mode 100755 (details printed to stdout)

set -euo pipefail

# ---------------------------------------------------------------------------
# Argument parsing
# ---------------------------------------------------------------------------
REPO_ROOT=""

while [[ $# -gt 0 ]]; do
    case "$1" in
        --repo-root)
            REPO_ROOT="$2"
            shift 2
            ;;
        --repo-root=*)
            REPO_ROOT="${1#*=}"
            shift
            ;;
        -h|--help)
            sed -n '2,/^set -/p' "$0" | grep '^#' | sed 's/^# \{0,1\}//'
            exit 0
            ;;
        *)
            echo "Unknown argument: $1" >&2
            exit 1
            ;;
    esac
done

# ---------------------------------------------------------------------------
# Resolve repo root
# ---------------------------------------------------------------------------
if [[ -z "$REPO_ROOT" ]]; then
    REPO_ROOT="$(git rev-parse --show-toplevel 2>/dev/null)" || {
        echo "ERROR: not inside a git repository and --repo-root not supplied." >&2
        exit 1
    }
fi

if [[ ! -d "$REPO_ROOT/.git" ]] && ! git -C "$REPO_ROOT" rev-parse --git-dir >/dev/null 2>&1; then
    echo "ERROR: $REPO_ROOT is not a git repository." >&2
    exit 1
fi

# ---------------------------------------------------------------------------
# Scan for .sh files not committed with mode 100755
# ---------------------------------------------------------------------------
# `git ls-files --stage <dir>` outputs lines of the form:
#   <mode> <hash> <stage>\t<path>
# We filter to paths ending in .sh and check that the mode field is 100755.

SCAN_DIRS=(
    "team/scripts"
    "team/verification"
)

bad_files=()

for dir in "${SCAN_DIRS[@]}"; do
    while IFS=$'\t' read -r mode path; do
        if [[ "$mode" != "100755" ]]; then
            bad_files+=("$path (mode $mode)")
        fi
    done < <(
        git -C "$REPO_ROOT" ls-files --stage "$dir" \
            | awk '$4 ~ /\.sh$/ { print $1 "\t" $4 }'
    )
done

# ---------------------------------------------------------------------------
# Report
# ---------------------------------------------------------------------------
if [[ ${#bad_files[@]} -eq 0 ]]; then
    echo "verify_script_exec_bits: OK — all .sh files have mode 100755."
    exit 0
fi

echo "verify_script_exec_bits: FAIL — the following .sh files are missing the exec bit (mode != 100755):"
for f in "${bad_files[@]}"; do
    echo "  $f"
done
echo ""
echo "Fix with:"
for f in "${bad_files[@]}"; do
    path="${f%% (*}"
    echo "  git update-index --chmod=+x $path"
done
exit 1
