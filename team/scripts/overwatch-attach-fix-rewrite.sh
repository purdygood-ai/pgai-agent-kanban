#!/usr/bin/env bash
# overwatch-attach-fix-rewrite.sh
# Thin wrapper around team/pgai_agent_kanban/overwatch/fix_rewrite.py's CLI
# -- Lane A recovery-playbook step (a), team/roles/OVERWATCH.md "Lane A --
# bad-fix regression": appends a dated, OVERWATCH-attributed
# "## Fix (OVERWATCH rewrite)" block to a bug file, naming the failed
# approach as FORBIDDEN, stating the correct direction, and reproducing
# ground-truth references verbatim.
#
# forbidden_approach and correct_direction are never derived automatically
# -- this is a judgment call about which fix was actually correct, and the
# caller (an operator, or a future reasoning-tier agent) supplies it. Once
# this has run, pgai_agent_kanban.overwatch.resolution_stage's Lane A drill
# (_run_lane_a) finds the blamed bug carrying an attributed rewrite and
# runs steps (b)-(e) on its next firing instead of declining.
#
# All validation and the fail-loud preconditions (missing bug file, no
# existing '## Fix' section, empty forbidden_approach, empty
# ground_truth_refs) live in fix_rewrite.py. This wrapper carries no
# business logic: it delegates to the shared pp_run_ops helper (which
# resolves a correct, cwd-independent PYTHONPATH) and forwards every
# argument unchanged.
#
# Usage:
#   overwatch-attach-fix-rewrite.sh --project <name> --kanban-root <path> \
#       --bug-id BUG-NNNN \
#       --forbidden-approach "<what was implemented and caused the regression>" \
#       --correct-direction "<the correct fix, stated on its own terms>" \
#       --ground-truth-ref "<verbatim reference>" [--ground-truth-ref <ref> ...]
#
# Run '--help' for the full flag reference (delegates straight to
# fix_rewrite.py's argparse --help).
#
# Exit codes:
#   0   the rewrite was appended successfully.
#   1   bad input, no matching bug file, or a fail-loud precondition —
#       a message is printed to stderr; nothing is written.
#
# The script resolves the repository root from its own location (via
# pp_run_ops.sh's self-location) so it can be invoked from any working
# directory.

set -euo pipefail

_SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

if ! command -v python3 >/dev/null 2>&1; then
    echo "ERROR: python3 not found in PATH." >&2
    exit 1
fi

# shellcheck source=lib/pp_run_ops.sh
source "${_SCRIPT_DIR}/lib/pp_run_ops.sh"

# Forward all arguments unchanged — fix_rewrite.py's main() owns argument
# parsing and validation.
pp_run_ops pgai_agent_kanban.overwatch.fix_rewrite "$@"
