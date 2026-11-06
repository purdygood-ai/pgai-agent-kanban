#!/usr/bin/env bash
# team/scripts/reliability-backfill.sh
# Operator back-fill for the block ledger: rebuild a project's block records,
# with class (human|any), cause (chain|operator) and reason_code, from the
# evidence already on disk, into a sidecar for review; --apply merges it.
#
# Usage:
#   reliability-backfill.sh --project NAME
#   reliability-backfill.sh --project NAME --apply
#   reliability-backfill.sh --help | -h
#
# Options:
#   --project NAME   Registered project to back-fill (default: $PGAI_PROJECT_NAME).
#                    Exact name or an unambiguous prefix.
#   --apply          Merge the reviewed sidecar into the ledger.
#
# Without --apply: reads the evidence and writes the sidecar
#   projects/<project>/metrics/blocked-intervals.backfill.jsonl
# beside the ledger (projects/<project>/metrics/blocked-intervals.jsonl),
# replacing any earlier sidecar. Nothing else is written: the ledger is
# byte-identical afterwards. Prints the sidecar path and every record the
# ledger does not have yet.
#
# Evidence read: the ledger; triage evidence (artifacts/block-evidence/
# triage-*.json) and status.md (## State, ## Block Record) of every task
# folder, live and archived (archive/<project>/tasks/); logs/sentinel-writes.log
# (who raised a HALT); metrics/rc/*.json and release-state.md (when each RC
# opened and closed).
#
# Sidecar: one JSON object per line -- a ledger record plus a "backfill"
# object: block (number, in start order), block_class (the class the block
# reads as), in_ledger (the ledger already has this line) and basis (the
# evidence). A block the ledger leaves open is closed by a triage "act"
# (class any, reason_code requeue) or, unless the task is BLOCKED now, at the
# close of its RC (reason_code rc_closed; a task block closed this way is
# class human -- only triage or a human returns a BLOCKED task to work). A
# triage "escalate" adds a block-escalate (class human). A HALT block takes
# the class of the task blocks of its RC it overlaps. The module docstring of
# pgai_agent_kanban.metrics.reliability_backfill has the full mapping.
#
# With --apply: appends to the ledger every sidecar record it does not have,
# through the ledger's own writers. An existing line is never rewritten or
# deleted, so a rebuilt field on a line the ledger already has stays in the
# sidecar only. Then rewrites metrics/reliability.json. Running it again --
# with or without --apply -- adds no records.
#
# Worked example (review, then merge):
#
#   Example: reliability-backfill.sh --project pgai-agent-kanban
#   Example: reliability-backfill.sh --project pgai-agent-kanban --apply
#
# Refusals (dual-channel: error / message / remedy on stderr; exit 2;
# nothing is written): no project named, an unknown project, a malformed
# ledger, --apply with no sidecar, a malformed sidecar.
#
# Working directory: irrelevant. The Python package is resolved from this
# script's own tree through pp_run_ops, never from the caller's cwd.
#
# Exit codes:
#   0  sidecar written, or sidecar applied
#   2  refused; nothing written

set -euo pipefail
# shellcheck source=lib/env_bootstrap.sh
source "$(dirname "$(readlink -f "${BASH_SOURCE[0]}")")/lib/env_bootstrap.sh"

_RB_SCRIPT_DIR="$(cd "$(dirname "$(readlink -f "${BASH_SOURCE[0]}")")" && pwd)"
_RB_LIB_DIR="${_RB_SCRIPT_DIR}/lib"

# shellcheck source=lib/operator_args.sh
source "${_RB_LIB_DIR}/operator_args.sh"
# shellcheck source=lib/project_paths.sh
source "${_RB_LIB_DIR}/project_paths.sh"
# shellcheck source=lib/error_contract.sh
source "${_RB_LIB_DIR}/error_contract.sh"
# shellcheck source=lib/pp_run_ops.sh
source "${_RB_LIB_DIR}/pp_run_ops.sh"

KANBAN_ROOT="${PGAI_AGENT_KANBAN_ROOT_PATH}"
export KANBAN_ROOT

# Declared flag vocabulary; --apply is a boolean outside the canonical set.
OPERATOR_VALID_FLAGS=(project apply help)

operator_args_parse "$@"

if argparse_has help; then
    operator_args_render_help_for_flags "reliability-backfill.sh" \
        "Rebuild a project's block-ledger records (class, cause, reason_code) from existing evidence into a sidecar for review; --apply merges it." \
        OPERATOR_VALID_FLAGS \
        "  --apply            Merge the reviewed sidecar into the ledger" \
        "" \
        "Without --apply, writes only the sidecar, beside the ledger:" \
        "  projects/<project>/metrics/blocked-intervals.backfill.jsonl" \
        "The ledger (metrics/blocked-intervals.jsonl) is left byte-identical." \
        "" \
        "Evidence read: the ledger; triage-*.json under artifacts/block-evidence/ and" \
        "status.md (## State, ## Block Record) of every task folder, live and archived;" \
        "logs/sentinel-writes.log; metrics/rc/*.json; release-state.md." \
        "" \
        "Each sidecar line is a ledger record plus a 'backfill' object: block," \
        "block_class, in_ledger (the ledger already has the line) and basis (the" \
        "evidence). An open block is closed by a triage 'act' (class any) or, unless" \
        "the task is BLOCKED now, at its RC's close (reason_code rc_closed; a task" \
        "block so closed is class human). A triage 'escalate' marks a block human." \
        "A HALT block takes the class of the task blocks of its RC it overlaps." \
        "" \
        "--apply appends every sidecar record the ledger lacks; it never rewrites or" \
        "deletes a line, then refreshes metrics/reliability.json. A second run, with or" \
        "without --apply, adds no records." \
        "" \
        "Worked example -- review, then merge:" \
        "  Example: reliability-backfill.sh --project pgai-agent-kanban" \
        "  Example: reliability-backfill.sh --project pgai-agent-kanban --apply" \
        "" \
        "Exit codes: 0 = sidecar written or applied. 2 = refused (no or unknown project," \
        "malformed ledger, no sidecar to apply, malformed sidecar); nothing written."
    exit 0
fi

_rb_unknown=""
if ! _rb_unknown="$(operator_args_validate_known "reliability-backfill.sh" OPERATOR_VALID_FLAGS 2>&1)"; then
    # BLOCK-WORTHY (docs/blocking-site-census.md): an argument this script
    # does not define names no back-fill it could run; refused rather than
    # ignored, before anything is read or written.
    pgai_emit_error_contract \
        "reliability-backfill.unknown_argument" \
        "${_rb_unknown//$'\n'/; }. Accepted: --project NAME, --apply, --help. Nothing was written." \
        "${_RB_SCRIPT_DIR}/reliability-backfill.sh --help"
    # CENSUS-ANCHOR: reliability-backfill-unknown-argument-01
    exit 2
fi

_rb_project_raw="$(operator_args_project)"
if [[ -z "${_rb_project_raw}" ]]; then
    # BLOCK-WORTHY (docs/blocking-site-census.md): with no project named there
    # is no ledger to rebuild, and guessing one could write records into the
    # wrong project's ledger.
    pgai_emit_error_contract \
        "reliability-backfill.project_required" \
        "reliability-backfill.sh: no project named -- the block ledger to rebuild is always one project's." \
        "${_RB_SCRIPT_DIR}/reliability-backfill.sh --project pgai-agent-kanban"
    # CENSUS-ANCHOR: reliability-backfill-project-required-01
    exit 2
fi

# Exact name, then unambiguous prefix. The resolver prints its own
# dual-channel refusal (project-resolve-not-found-01 /
# project-resolve-ambiguous-01); this site only stops on it.
# BLOCK-WORTHY (docs/blocking-site-census.md): an unregistered or ambiguous
# name names no ledger to rebuild.
# CENSUS-ANCHOR: reliability-backfill-project-unresolved-01
_rb_project="$(pp_resolve_project_name "${_rb_project_raw}")" || exit 2

# The module refuses an unregistered project directory, a malformed ledger,
# --apply with no sidecar and a malformed sidecar, all before its first write.
_rb_args=(--project "${_rb_project}" --kanban-root "${KANBAN_ROOT}")
if argparse_has apply; then
    _rb_args+=(--apply)
fi
pp_run_ops pgai_agent_kanban.metrics.reliability_backfill "${_rb_args[@]}"
