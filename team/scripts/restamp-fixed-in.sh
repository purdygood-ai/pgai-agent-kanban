#!/usr/bin/env bash
# team/scripts/restamp-fixed-in.sh
# Operator back-fill for a bug's "## Fixed In" value: stamp one named release
# version onto a named set of bug files in a project's bug ledger.
#
# The release engine records "## Fixed In: <version>" on the bugs a release
# closed while it ships. When that record did not land for some release, run
# this afterwards with the version that shipped and the bugs it closed. It
# writes through the same writeback the release engine uses
# (pgai_agent_kanban.cm.fixed_in_writeback.write_fixed_in), so the value lands
# in the same place and shape.
#
# Usage:
#   restamp-fixed-in.sh --project NAME --version vX.Y.Z BUG [BUG ...]
#   restamp-fixed-in.sh --project NAME --version vX.Y.Z --replace-from vA.B.C BUG [BUG ...]
#   restamp-fixed-in.sh --help | -h
#
# Options:
#   --project NAME     Registered project whose bugs/ ledger is stamped
#                      (default: $PGAI_PROJECT_NAME). Exact name or an
#                      unambiguous prefix.
#   --version vX.Y.Z   The release that fixed the named bugs. The leading
#                      'v' is optional; what is written always carries it.
#   --replace-from vA.B.C
#                      Correction mode: also overwrite a "## Fixed In" that
#                      is exactly vA.B.C (a value the release engine wrote
#                      wrongly) with --version. Opt-in and exact-match only.
#   BUG                A bug to stamp, named by its id, its file name, that
#                      name without .md, or a path ending in one of those
#                      (e.g. BUG-0101 or BUG-0101-short-slug.md).
#
# Archived bugs: a name that matches no file in the project's live bugs/
# ledger is looked up, by the same rules, in the project's archive
# ($KANBAN_ROOT/archive/<project>/bugs/), and an archived bug is stamped in
# place in the archive; its output line ends in "(archived)". A live bug
# always wins -- the archive is consulted only for a name with no live match.
#
# Per named bug:
#   - status done, "## Fixed In" empty   -> stamped
#   - "## Fixed In" already set          -> skipped (never overwritten)
#   - status anything but done           -> skipped (not written)
# Bugs not named are never written. One line is printed per named bug, then
#   Fixed-In restamp vX.Y.Z: N stamped, S skipped.
#
# With --replace-from vA.B.C, per named bug:
#   - status done, "## Fixed In" exactly vA.B.C -> replaced with vX.Y.Z
#   - "## Fixed In" empty                        -> stamped, as above
#   - status anything but done                   -> skipped, as above
#   - "## Fixed In" any other value              -> the whole run is refused
#     (fixed_in_restamp.replace_from_mismatch); nothing is written
# and the result line reads
#   Fixed-In restamp vX.Y.Z: N stamped, R replaced from vA.B.C, S skipped.
#
# Refusals (dual-channel: error / message / remedy on stderr; exit 2; nothing
# is written): no --project, missing or malformed --version, a malformed
# --replace-from, no bug named, a bug name matching no file or more than one,
# no live bugs/ directory for the project, or (with --replace-from) a named bug
# whose "## Fixed In" is neither empty nor the --replace-from value. Every name is resolved before the first write, so a bad list
# never produces a partial back-fill.
#
# Worked example (back-fill a past release). Release v1.4.0 shipped a bundle
# of three bugs that were closed but never stamped; record it for them:
#
#   Example: restamp-fixed-in.sh --project pgai-agent-kanban --version v1.4.0 BUG-0101 BUG-0102 BUG-0103
#
# Run it once per release, each with that release's own bug list (the
# "## Bundled Items" of the release's requirements bundle). A second run is
# harmless: every bug it already stamped reports as skipped.
#
# Worked example (correct a wrong stamp). Release v1.5.0 recorded itself on
# three bugs that actually shipped in v1.4.0; rewrite them:
#
#   Example: restamp-fixed-in.sh --project pgai-agent-kanban --version v1.4.0 --replace-from v1.5.0 BUG-0101 BUG-0102 BUG-0103
#
# Name only the bugs the wrong release really mis-stamped: a correction never
# touches an unnamed bug, and re-running it on a bug it already corrected is
# refused (its value is no longer the --replace-from one), writing nothing.
#
# Working directory: irrelevant. The Python package is resolved from this
# script's own tree through pp_run_ops, never from the caller's cwd.
#
# Exit codes:
#   0  every named bug was stamped, replaced or skipped
#   1  at least one named bug could not be read or written (the others
#      still landed; the per-bug lines say which)
#   2  refused (bad invocation); nothing was written

set -euo pipefail
# shellcheck source=lib/env_bootstrap.sh
source "$(dirname "${BASH_SOURCE[0]}")/lib/env_bootstrap.sh"

_RS_SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
_RS_LIB_DIR="${_RS_SCRIPT_DIR}/lib"

# shellcheck source=lib/operator_args.sh
source "${_RS_LIB_DIR}/operator_args.sh"
# shellcheck source=lib/project_paths.sh
source "${_RS_LIB_DIR}/project_paths.sh"
# shellcheck source=lib/error_contract.sh
source "${_RS_LIB_DIR}/error_contract.sh"
# shellcheck source=lib/pp_run_ops.sh
source "${_RS_LIB_DIR}/pp_run_ops.sh"

KANBAN_ROOT="${PGAI_AGENT_KANBAN_ROOT_PATH}"
export KANBAN_ROOT

# Declared flag vocabulary; --version is not in the canonical value-flag set.
OPERATOR_VALID_FLAGS=(project version replace-from help)

operator_args_parse --extra-value-flags "version replace-from" "$@"

if argparse_has help; then
    operator_args_render_help_for_flags "restamp-fixed-in.sh" \
        "Stamp '## Fixed In: <version>' on the named bugs of a project's ledger." \
        OPERATOR_VALID_FLAGS \
        "  --version vX.Y.Z   Release that fixed the named bugs (leading 'v' optional)" \
        "  --replace-from vA.B.C" \
        "                     Correction mode: also overwrite a '## Fixed In' that is" \
        "                     exactly vA.B.C with --version; refuse the whole run," \
        "                     writing nothing, if a named bug carries any other value" \
        "  BUG ...            Bugs to stamp by id or file name, e.g. BUG-0101" \
        "" \
        "Stamps each named bug whose status is done and whose '## Fixed In' is" \
        "empty. A bug whose value is already set is skipped, never overwritten;" \
        "a bug that is not done is skipped; a bug not named is never written." \
        "Prints one line per named bug and a closing result line." \
        "" \
        "A name with no match in the project's live bugs/ ledger is looked up in" \
        "the project's archive (archive/<project>/bugs/) and stamped in place" \
        "there; its line ends in '(archived)'. A live bug always wins." \
        "" \
        "Every name is resolved before the first write: a missing or malformed" \
        "--version, no bug named, or a name matching no file, live or archived" \
        "(or several), is refused with exit 2 and nothing is written." \
        "" \
        "Worked example -- back-fill a past release, v1.4.0, on the three bugs it closed:" \
        "  Example: restamp-fixed-in.sh --project pgai-agent-kanban --version v1.4.0 BUG-0101 BUG-0102 BUG-0103" \
        "Run it once per release, each with that release's own bug list; a second" \
        "run is harmless -- every bug it already stamped reports as skipped." \
        "" \
        "Worked example -- correct a wrong stamp: v1.5.0 recorded itself on three" \
        "bugs that shipped in v1.4.0. Only a value exactly v1.5.0 is replaced:" \
        "  Example: restamp-fixed-in.sh --project pgai-agent-kanban --version v1.4.0 --replace-from v1.5.0 BUG-0101 BUG-0102 BUG-0103" \
        "A named bug whose value is neither empty nor v1.5.0 refuses the run" \
        "(fixed_in_restamp.replace_from_mismatch, exit 2) and nothing is written." \
        "" \
        "Exit codes: 0 = done (stamped, replaced or skipped). 1 = a named bug could not" \
        "be read or written. 2 = refused; nothing written."
    exit 0
fi

operator_args_validate_known "restamp-fixed-in.sh" OPERATOR_VALID_FLAGS || exit 2

_rs_project_raw="$(operator_args_project)"
if [[ -z "${_rs_project_raw}" ]]; then
    # BLOCK-WORTHY (docs/blocking-site-census.md): with no project named there
    # is no bug ledger to resolve, and guessing one could stamp the wrong
    # project's bugs.
    _rs_example="team/scripts/restamp-fixed-in.sh --project pgai-agent-kanban --version v1.4.0 BUG-0101"  # example invocation
    pgai_emit_error_contract \
        "fixed_in_restamp.project_required" \
        "restamp-fixed-in.sh: no project named -- the bug ledger to stamp is always one project's." \
        "${_rs_example}"
    # CENSUS-ANCHOR: restamp-fixed-in-project-required-01
    exit 2
fi

# Exact name, then unambiguous prefix; refuses on its own (anchored in the
# census at project-resolve-not-found-01 / project-resolve-ambiguous-01).
_rs_project="$(pp_resolve_project_name "${_rs_project_raw}")" || exit 2
_rs_bugs_dir="$(pp_bugs_dir "${_rs_project}")" || exit 2

# Version, bug-list and bugs-directory validation all happen in the module,
# before its first write, so every refusal shares one path.
_rs_args=(--bugs-dir "${_rs_bugs_dir}" --project "${_rs_project}")
if argparse_has version; then
    _rs_args+=(--version "$(operator_args_get version)")
fi
if argparse_has replace-from; then
    _rs_args+=(--replace-from "$(operator_args_get replace-from)")
fi
pp_run_ops pgai_agent_kanban.cm.fixed_in_restamp "${_rs_args[@]}" \
    -- "${ARGPARSE_POSITIONAL[@]+"${ARGPARSE_POSITIONAL[@]}"}"
