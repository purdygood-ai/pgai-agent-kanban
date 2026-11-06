#!/usr/bin/env bash
# team/scripts/migrations/migrate_v2.53.1_to_v2.53.2.sh
#
# Seeds the kanban's own test-runner commands into the project.cfg
# [testing] section of the project that builds the kanban itself:
#
#   targeted_test_command = bash team/scripts/run-unit-tests.sh --files {files} && bash team/scripts/run-integration-tests.sh --files {files}
#   pre_merge_command     = bash team/scripts/run-unit-tests.sh --pre-merge
#
# The PM materializer now takes the targeted-run and pre-merge criteria it
# writes into CODER and WRITER tickets from the project's own project.cfg,
# and a project that sets neither key gets neither criterion. With these
# two lines the kanban's own tickets keep the criteria they carried before,
# word for word. Every other project is left to its operator
# (project.cfg_example [testing] documents both keys).
#
# Which project is the kanban's own — by property, never by name:
#   - The kanban's source tree is the repository this script sits in
#     (<tree>/team/scripts/migrations/, the tree upgrade.sh installs from).
#     Run from an installed root (<root>/scripts/migrations/, no repository
#     above it) it is the configured dev tree: PGAI_DEV_TREE_PATH, then
#     kanban.cfg [paths] dev_tree_path.
#   - A registered project is the kanban's own when its dev tree — its
#     project.cfg [project] dev_tree_path, else that same configured dev
#     tree — is the source tree: the same directory once symlinks are
#     resolved, or another checkout of the same git repository (the clean
#     tag worktree a version-targeted upgrade installs from shares the dev
#     tree's repository). The registered name plays no part.
#
# What this migration does:
#   - For each registered project that has a project.cfg and is the
#     kanban's own: adds each of the two keys that file's [testing] section
#     has no line for, after the section's last key (appending a [testing]
#     section at the end of the file when there is none).
#   - Leaves a key alone when the section already has a line for it,
#     whatever its value — a blank value is the operator's choice of no
#     criterion. A commented "# key =" line is not a setting and does not
#     stop the seed.
#   - Narrates a [skip] line for every other registered project and never
#     writes its project.cfg, and never writes kanban.cfg.
#   - --project narrows the run to that one project; PGAI_PROJECT_NAME is
#     not read, so an operator's exported project name never narrows an
#     upgrade's run.
#
# The logic lives in pgai_agent_kanban.lib.cfg_key_seed; this script parses
# arguments, picks the file and calls it.
#
# Contract (team/scripts/migrations/README.md):
#   - Additive:        only adds lines; no existing line is changed, removed
#                      or reordered.
#   - Non-destructive: a key already present is kept byte-for-byte; the file
#                      is replaced atomically and keeps its permission bits.
#   - Idempotent:      a second run finds both keys present, writes nothing,
#                      and leaves the file byte-identical.
#
# Usage:
#   migrate_v2.53.1_to_v2.53.2.sh                    # every registered project that is the kanban's own
#   migrate_v2.53.1_to_v2.53.2.sh --project pgai-agent-kanban   # the same, narrowed to one project
#   migrate_v2.53.1_to_v2.53.2.sh --dry-run          # preview only, no writes
#
# Exit codes:
#   0 — success (including "already present", "not the kanban's own",
#       "not registered", "no project.cfg" and "source tree not found")
#   1 — usage error
#   3 — KANBAN_ROOT not set or not found
#   4 — project.cfg could not be rewritten (left unchanged)

set -euo pipefail
# shellcheck source=../lib/env_bootstrap.sh
source "$(dirname "$(readlink -f "${BASH_SOURCE[0]}")")/../lib/env_bootstrap.sh"

_MIG_SCRIPT_DIR="$(cd "$(dirname "$(readlink -f "${BASH_SOURCE[0]}")")" && pwd)"
_MIG_SCRIPT_PATH="${_MIG_SCRIPT_DIR}/$(basename "${BASH_SOURCE[0]}")"
# shellcheck source=../lib/error_contract.sh
source "${_MIG_SCRIPT_DIR}/../lib/error_contract.sh"

KANBAN_ROOT="${PGAI_AGENT_KANBAN_ROOT_PATH}"
if [[ ! -d "$KANBAN_ROOT" ]]; then
    # BLOCK-WORTHY: with no kanban root there is no project.cfg to seed, and
    # reporting success would let an upgrade claim the keys reached an
    # install this script never saw.
    pgai_emit_error_contract "migration.kanban_root_missing" \
        "kanban root not found: ${KANBAN_ROOT} -- set PGAI_AGENT_KANBAN_ROOT_PATH to the install root or run install.sh." \
        "PGAI_AGENT_KANBAN_ROOT_PATH=\"\$HOME/pgai_agent_kanban\" bash $(printf '%q' "$_MIG_SCRIPT_PATH")"
    # CENSUS-ANCHOR: migrate-runner-cfg-root-missing-01
    exit 3
fi

# shellcheck source=../lib/operator_args.sh
source "${_MIG_SCRIPT_DIR}/../lib/operator_args.sh"
# shellcheck source=../lib/projects.sh
source "${_MIG_SCRIPT_DIR}/../lib/projects.sh"
# shellcheck source=../lib/dev_tree.sh
source "${_MIG_SCRIPT_DIR}/../lib/dev_tree.sh"
# shellcheck source=../lib/pp_run_ops.sh
declare -F pp_run_ops >/dev/null || source "${_MIG_SCRIPT_DIR}/../lib/pp_run_ops.sh"

# The values this migration seeds.
_MIG_TARGETED_TEST_COMMAND="bash team/scripts/run-unit-tests.sh --files {files} && bash team/scripts/run-integration-tests.sh --files {files}"
_MIG_PRE_MERGE_COMMAND="bash team/scripts/run-unit-tests.sh --pre-merge"

OPERATOR_VALID_FLAGS=(project dry-run help)
# The built-in --project line names a PGAI_PROJECT_NAME default; this script
# never reads that variable, so it states its own default instead.
declare -A OPERATOR_FLAG_HELP=(
    [project]="  --project NAME     Narrow the run to this project (default: every registered project)"
)

_USAGE_TEXT="$(operator_args_render_help_for_flags "migrate_v2.53.1_to_v2.53.2.sh" \
    "Seed [testing] targeted_test_command and pre_merge_command (the kanban's own runner commands) into the project.cfg of every registered project whose dev tree is the kanban's own source tree (the repository this script sits in; from an installed root, PGAI_DEV_TREE_PATH or kanban.cfg [paths] dev_tree_path) and that lacks them. A project matches by its dev tree -- the same directory, or another checkout of the same git repository -- never by its name; no other project.cfg is written. Only an explicit --project narrows the run; PGAI_PROJECT_NAME is not read." \
    OPERATOR_VALID_FLAGS)"

operator_args_argparse --value-flags "project" -- "$@"

if argparse_missing "project"; then
    # BLOCK-WORTHY: a usage error; nothing has been read or written yet.
    pgai_emit_error_contract "migration.project_value_missing" \
        "--project requires a project name." \
        "bash $(printf '%q' "$_MIG_SCRIPT_PATH") --dry-run"
    # CENSUS-ANCHOR: migrate-runner-cfg-project-value-01
    exit 1
fi

if argparse_has "help" || argparse_has "h"; then
    printf '%s\n' "$_USAGE_TEXT" >&2
    exit 0
fi

if [[ "${#ARGPARSE_POSITIONAL[@]}" -gt 0 ]]; then
    # BLOCK-WORTHY: a usage error; nothing has been read or written yet.
    pgai_emit_error_contract "migration.unexpected_positional" \
        "unexpected positional argument: ${ARGPARSE_POSITIONAL[0]} -- this script takes only --project, --dry-run and --help." \
        "bash $(printf '%q' "$_MIG_SCRIPT_PATH") --help"
    # CENSUS-ANCHOR: migrate-runner-cfg-positional-01
    exit 1
fi

operator_args_validate_known "$(basename "$0")" OPERATOR_VALID_FLAGS || exit 1

DRY_RUN="false"
argparse_has "dry-run" && DRY_RUN="true"
# The flag alone narrows the run: an exported PGAI_PROJECT_NAME naming some
# other project must not stop an upgrade from seeding the kanban project.
SINGLE_PROJECT="$(operator_args_get project)"

# _mig_source_tree — print the kanban's source tree, or return 1 when none
# can be located. Checkout layout (<tree>/team/scripts/migrations/): the
# tree this script sits in. Install layout (<root>/scripts/migrations/):
# the configured dev tree, when it names a directory.
_mig_source_tree() {
    local scripts_dir global
    scripts_dir="$(cd "${_MIG_SCRIPT_DIR}/.." && pwd)" || return 1
    if [[ "$(basename "$(dirname "$scripts_dir")")" == "team" ]]; then
        resolve_runner_repo_root "$scripts_dir"
        return
    fi
    global="$(resolve_global_dev_tree)"
    [[ -n "$global" && -d "$global" ]] || return 1
    PGAI_DEV_TREE_PATH="$global" resolve_runner_repo_root "$scripts_dir"
}

# _mig_canonical_dir <path> — the directory with every symlink resolved,
# or return 1 when it is not a directory.
_mig_canonical_dir() {
    [[ -n "$1" && -d "$1" ]] || return 1
    (cd "$1" && pwd -P)
}

# _mig_git_identity <dir> — the canonical git common directory of the
# checkout whose top level is <dir>; return 1 when <dir> is not the top of
# a git checkout. Every worktree of one repository prints the same value.
_mig_git_identity() {
    local top common
    top="$(git -C "$1" rev-parse --show-toplevel 2>/dev/null)" || return 1
    [[ "$(_mig_canonical_dir "$top")" == "$1" ]] || return 1
    common="$(git -C "$1" rev-parse --path-format=absolute --git-common-dir 2>/dev/null)" || return 1
    _mig_canonical_dir "$common"
}

# _mig_project_dev_tree <project.cfg> — the project's dev tree: its own
# [project] dev_tree_path, else the configured dev tree. May be empty.
_mig_project_dev_tree() {
    local own
    own="$(read_ini "$1" project dev_tree_path "")"
    if [[ -n "$own" ]]; then
        printf '%s' "$own"
    else
        resolve_global_dev_tree
    fi
}

if ! _MIG_SOURCE_TREE="$(_mig_source_tree)" || ! _MIG_SOURCE_TREE="$(_mig_canonical_dir "$_MIG_SOURCE_TREE")"; then
    echo "migrate_v2.53.1_to_v2.53.2.sh: seeding [testing] runner commands into the kanban's own project"
    echo "  [skip]  kanban source tree not found: no repository above $(printf '%q' "$_MIG_SCRIPT_PATH") and no existing directory in PGAI_DEV_TREE_PATH or ${KANBAN_ROOT}/kanban.cfg [paths] dev_tree_path -- nothing to seed"
    exit 0
fi
_MIG_SOURCE_GIT="$(_mig_git_identity "$_MIG_SOURCE_TREE")" || _MIG_SOURCE_GIT=""
unset _MIG_SCRIPT_DIR

echo "migrate_v2.53.1_to_v2.53.2.sh: seeding [testing] runner commands into the project whose dev tree is the kanban's source tree ${_MIG_SOURCE_TREE}"

if [[ -n "$SINGLE_PROJECT" ]]; then
    if ! projects_cfg_has "$SINGLE_PROJECT"; then
        echo "  [skip]  ${SINGLE_PROJECT}: not registered (see $(projects_cfg_path)) -- nothing to seed"
        exit 0
    fi
    _MIG_PROJECTS=("$SINGLE_PROJECT")
else
    mapfile -t _MIG_PROJECTS < <(projects_cfg_list --registration-order 2>/dev/null || true)
fi

if [[ "${#_MIG_PROJECTS[@]}" -eq 0 ]]; then
    echo "  [skip]  no project registered (see $(projects_cfg_path)) -- nothing to seed"
    exit 0
fi

_MIG_EXIT=0
_MIG_MATCHED=0
for _mig_name in "${_MIG_PROJECTS[@]}"; do
    [[ -n "$_mig_name" ]] || continue
    _mig_cfg="${KANBAN_ROOT}/projects/${_mig_name}/project.cfg"
    if [[ ! -f "$_mig_cfg" ]]; then
        echo "  [skip]  ${_mig_name}: no project.cfg at ${_mig_cfg} -- nothing to seed"
        continue
    fi
    _mig_tree="$(_mig_project_dev_tree "$_mig_cfg")"
    if [[ -z "$_mig_tree" ]]; then
        echo "  [skip]  ${_mig_name}: no dev tree configured -- not the kanban's own project, no change"
        continue
    fi
    _mig_same="false"
    if _mig_canon="$(_mig_canonical_dir "$_mig_tree")"; then
        if [[ "$_mig_canon" == "$_MIG_SOURCE_TREE" ]]; then
            _mig_same="true"
        elif [[ -n "$_MIG_SOURCE_GIT" ]] && [[ "$(_mig_git_identity "$_mig_canon" || true)" == "$_MIG_SOURCE_GIT" ]]; then
            _mig_same="true"
        fi
    fi
    if [[ "$_mig_same" != "true" ]]; then
        echo "  [skip]  ${_mig_name}: dev tree ${_mig_tree} is not the kanban's source tree -- no change"
        continue
    fi

    _MIG_MATCHED=$((_MIG_MATCHED + 1))
    _mig_args=(
        --cfg "$_mig_cfg"
        --label "$_mig_name"
        --key testing targeted_test_command "$_MIG_TARGETED_TEST_COMMAND"
        --key testing pre_merge_command "$_MIG_PRE_MERGE_COMMAND"
        --rerun-command "bash $(printf '%q' "$_MIG_SCRIPT_PATH") --project $(printf '%q' "$_mig_name")"
    )
    [[ "$DRY_RUN" == "true" ]] && _mig_args+=(--dry-run)

    _mig_rc=0
    pp_run_ops pgai_agent_kanban.lib.cfg_key_seed "${_mig_args[@]}" || _mig_rc=$?
    # A project.cfg that cannot be rewritten fails the run, after every
    # other matching project has had its turn.
    if [[ "$_mig_rc" -ne 0 ]]; then
        _MIG_EXIT="$_mig_rc"
    fi
done

if [[ "$_MIG_MATCHED" -eq 0 ]]; then
    echo "  [skip]  no registered project's dev tree is ${_MIG_SOURCE_TREE} -- nothing to seed"
fi
exit "$_MIG_EXIT"
