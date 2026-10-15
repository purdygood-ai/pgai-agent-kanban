#!/usr/bin/env bash
# team/scripts/lib/discovery_eligibility.sh
#
# Discovery requirements-directory eligibility stage: the single primitive
# that enumerates every requirements/ file eligible for PM pickup, branching
# on the project's declared version_semantics (semver ceiling/ordering vs.
# label filename-lexical ordering). Sourced by team/scripts/lib/discovery.sh;
# extracted from team/scripts/lib/discovery_state.sh's own former body so
# that file's status-field/cache-mutation and bundle-writer concerns are not
# sharing a file with this considerably larger enumeration concern -- the
# split keeps both files single-purpose lib stages under the
# engine-decomposition's line ceiling (docs/blocking-site-census.md row 53).
# This is a behavior-preserving move: no stdout/stderr line, exit code, or
# on-disk file content changes as a result of this file existing.
#
# Public entry point
# -------------------
#   _disc_list_all_eligible_requirements <dir> <last_released> [max_minor] [max_major] [max_patch] [version_semantics]
#       Called directly by discovery_step_requirements in
#       team/scripts/lib/discovery_step_requirements.sh (Step 3 of the
#       pipeline) to enumerate every requirements file eligible for PM
#       pickup, before that step applies its own per-file pm_backlog
#       idempotency loop. Also called by team/scripts/lib/discovery_defer.sh
#       when resolving which items a deferral pass should consider.
#
# Dependencies (must already be sourced/defined by the caller before this
# file's functions are called)
# -----------------------------------------------------------------------
#   team/scripts/lib/pp_run_ops.sh — self-sourced below. The enumeration is
#     pgai_agent_kanban.discovery.eligibility, which imports
#     pgai_agent_kanban.lib.filename_contract for the single-source
#     intake/semver filename patterns.
#
# python3 must be on PATH.

# Guard against double-sourcing.
if declare -F _disc_list_all_eligible_requirements >/dev/null 2>&1; then
    return 0
fi

if ! declare -F pp_run_ops >/dev/null 2>&1; then
    # shellcheck source=pp_run_ops.sh
    source "$(dirname "${BASH_SOURCE[0]}")/pp_run_ops.sh"
fi

# _disc_list_all_eligible_requirements <dir> <last_released> [max_minor] [max_major] [max_patch] [version_semantics]
#
# List ALL requirements files that are eligible for PM pickup.  Behavior
# branches on <version_semantics> declared by the project's workflow plugin:
#
#   semver (release, document with semver)
#     - Ascending semver order (lowest version first), followed by auto-sentinels.
#     - Outputs every file with target_version > last_released.
#     - Ceiling-blocked files carry a structured suffix so the shell caller
#       can emit the documented rejection log line and continue iterating:
#         ":ceiling-blocked:<version>:<component>:<value>:<ceiling_name>:<ceiling_value>"
#     - Auto-sentinel files carry ":auto" suffix.
#     - Items with non-semver Target Version are logged and skipped.
#
#   label / none (testing-only and any future label-version plugin)
#     - Every unhandled item in requirements/ is eligible; no semver parse,
#       no ceiling filter (ceilings are version-consumption checkpoints that
#       only apply to semver release lanes).
#     - Ordering: filename-lexical ascending (deterministic, reproducible
#       across systems; avoids mtime variance on copy/restore operations).
#     - Auto-sentinel (Target Version: auto / next-patch / absent) is NOT
#       valid for label projects — these files are emitted with ":auto-invalid"
#       so the shell caller can fail loud with a named reason.
#     - Items with a Terminal ## Status (done / wont-do) are still filtered.
#     - One-line skip log is emitted to stderr for any skipped item.
#
# When <version_semantics> is absent or empty it defaults to "semver" for
# backward compatibility with callers that predate the parameter.
#
# max_minor, max_major, max_patch are the ceilings exactly as pp_max_minor /
# pp_max_major / pp_max_patch echo them: a number, or an empty string for the
# explicit `none` (no constraint). The empty string is handed to the Python
# module as `none`, because there an omitted value means the documented
# default instead. They are honoured only on the semver path.
# Output: one line per file, absolute path, possibly with ":auto",
#         ":auto-invalid", or structured ":ceiling-blocked:..." suffix.
#         Outputs nothing if no eligible file found.
_disc_list_all_eligible_requirements() {
    local dir="$1"
    local last_released="$2"
    local max_minor="${3:-}"
    local max_major="${4:-}"
    local max_patch="${5:-}"
    local version_semantics="${6:-semver}"

    pp_run_ops pgai_agent_kanban.discovery.eligibility \
        "$dir" "$last_released" "${max_minor:-none}" "${max_major:-none}" "${max_patch:-none}" \
        "$version_semantics"
}
