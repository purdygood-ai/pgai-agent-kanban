#!/usr/bin/env bash
# team/verification/verify_script_exec_bits.sh
#
# Regression guard: assert that every committed regular .sh file under the
# canonical shell-script directories has mode 100755 (exec bit set) in the
# git index.
# CODER agents that Write/Edit new shell scripts may produce mode 0644 by
# default; this guard catches any such regression before it ships to a release.
#
# Directories scanned (relative to repo root):
#   team/scripts/       (recursive, *.sh only)
#   team/verification/  (recursive, *.sh only)
#
# Non-.sh files in those directories are not checked.
#
# Only regular files (index modes 100644 and 100755) are judged. Every other
# .sh index entry is skipped, not reported, because `git update-index
# --chmod=+x` cannot fix it:
#   120000  symlink — its own mode is not what executes, its target's is. A
#           target that is itself a committed .sh under a scanned directory
#           is checked as the regular file it is.
#   160000  gitlink (a submodule whose path ends in .sh) — a commit pointer,
#           not a file; it has no exec bit to set.
# Skipping them keeps every printed remedy a chmod of regular files that
# runs as printed and fixes every file it names.
#
# Checks committed modes (via `git ls-files --stage -z`), not filesystem modes,
# so the guard works correctly against a fresh clone where filesystem modes may
# differ from committed modes. Index entries are read NUL-delimited, so a path
# containing whitespace or any other character is checked whole.
#
# On failure the "Fix with:" lines are shell-quoted and run from the repo
# root; the error-contract remedy on stderr carries `git -C <repo>` and runs
# from any directory.
#
# Usage:
#   verify_script_exec_bits.sh [--repo-root <path>]
#
# Options:
#   --repo-root <path>   Path to the git repository root.
#                        Default: auto-detected via `git rev-parse --show-toplevel`
#
# Exit codes:
#   0 — all regular .sh files in scanned directories have mode 100755
#       (symlink and gitlink entries are skipped, never a failure)
#   1 — one or more regular .sh files are at mode 100644 (details printed to stdout)

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
# Dual-channel error contract
# ---------------------------------------------------------------------------
# Refusals print error / message / remedy on stderr through the shared
# emitter (lib/verification_common.sh, which carries the stand-in used when
# the scripts lib tree is missing).
_VSEB_SELF_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
_VSEB_SELF="${_VSEB_SELF_DIR}/$(basename "${BASH_SOURCE[0]}")"
# shellcheck source=lib/verification_common.sh
source "${_VSEB_SELF_DIR}/lib/verification_common.sh"

# The repository a --repo-root remedy names: the checkout holding this
# script, else PGAI_DEV_TREE_PATH or the project's dev_tree_path, else a
# neutral example path the reader replaces with their own checkout.
_vseb_example_root="$(git -C "$_VSEB_SELF_DIR" rev-parse --show-toplevel 2>/dev/null)" \
    || _vseb_example_root="$(verification_dev_tree)"
[[ -n "$_vseb_example_root" ]] || _vseb_example_root="$VERIFICATION_EXAMPLE_DEV_TREE"

# ---------------------------------------------------------------------------
# Resolve repo root
# ---------------------------------------------------------------------------
if [[ -z "$REPO_ROOT" ]]; then
    REPO_ROOT="$(git rev-parse --show-toplevel 2>/dev/null)" || {
        # BLOCK-WORTHY: outside a git repository there is no index to read, so the check cannot run.
        pgai_emit_error_contract "verify_script_exec_bits.not_in_git_repo" \
            "verify_script_exec_bits: ${PWD} is not inside a git repository and --repo-root was not supplied, so there is no index to read committed modes from. Pass --repo-root with the checkout to check (the remedy names ${_vseb_example_root})." \
            "bash $(printf '%q' "$_VSEB_SELF") --repo-root $(printf '%q' "$_vseb_example_root")"
        # CENSUS-ANCHOR: verify-script-exec-bits-01
        exit 1
    }
fi

if [[ ! -d "$REPO_ROOT/.git" ]] && ! git -C "$REPO_ROOT" rev-parse --git-dir >/dev/null 2>&1; then
    # BLOCK-WORTHY: a --repo-root that is not a git repository has no index to read, so the check cannot run.
    pgai_emit_error_contract "verify_script_exec_bits.repo_root_not_git" \
        "verify_script_exec_bits: --repo-root ${REPO_ROOT} is not a git repository, so there is no index to read committed modes from." \
        "bash $(printf '%q' "$_VSEB_SELF") --repo-root $(printf '%q' "$_vseb_example_root")"
    # CENSUS-ANCHOR: verify-script-exec-bits-02
    exit 1
fi

# ---------------------------------------------------------------------------
# Scan for .sh files not committed with mode 100755
# ---------------------------------------------------------------------------
# `git ls-files --stage -z <dir>` outputs NUL-terminated records of the form:
#   <mode> <hash> <stage>\t<path>
# The path is everything after the first tab, taken verbatim (-z also turns
# off git's C-style quoting of unusual paths). We keep paths ending in .sh,
# skip every entry that is not a regular file (symlink 120000, gitlink
# 160000; see the header), and report each regular file not at 100755.

SCAN_DIRS=(
    "team/scripts"
    "team/verification"
)

bad_paths=()
bad_modes=()

for dir in "${SCAN_DIRS[@]}"; do
    while IFS= read -r -d '' record; do
        path="${record#*$'\t'}"
        mode="${record%% *}"
        [[ "$path" == *.sh ]] || continue
        case "$mode" in
            100755)
                ;;
            100*)
                # A regular file without the exec bit: --chmod=+x fixes it.
                bad_paths+=("$path")
                bad_modes+=("$mode")
                ;;
            *)
                # Not a regular file (symlink, gitlink): no exec bit to set.
                ;;
        esac
    done < <(git -C "$REPO_ROOT" ls-files --stage -z -- "$dir")
done

# ---------------------------------------------------------------------------
# Report
# ---------------------------------------------------------------------------
if [[ ${#bad_paths[@]} -eq 0 ]]; then
    echo "verify_script_exec_bits: OK — all .sh files have mode 100755."
    exit 0
fi

echo "verify_script_exec_bits: FAIL — the following .sh files are missing the exec bit (mode != 100755):"
for i in "${!bad_paths[@]}"; do
    echo "  ${bad_paths[$i]} (mode ${bad_modes[$i]})"
done
echo ""
echo "Fix with:"
for path in "${bad_paths[@]}"; do
    echo "  git update-index --chmod=+x $(printf '%q' "$path")"
done
_vseb_quoted_paths="$(printf '%q ' "${bad_paths[@]}")"
# BLOCK-WORTHY: a script committed without its exec bit fails "Permission denied" when invoked directly.
pgai_emit_error_contract "verify_script_exec_bits.exec_bit_missing" \
    "verify_script_exec_bits: ${#bad_paths[@]} .sh file(s) under ${SCAN_DIRS[*]} are committed without mode 100755 (listed above)." \
    "git -C $(printf '%q' "$REPO_ROOT") update-index --chmod=+x ${_vseb_quoted_paths% }"
# CENSUS-ANCHOR: verify-script-exec-bits-03
exit 1
