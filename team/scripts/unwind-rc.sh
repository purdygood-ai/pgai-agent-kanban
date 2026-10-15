#!/usr/bin/env bash
# team/scripts/unwind-rc.sh
# Operator script: fully unwinds an in-flight release candidate across all state stores.
#
# Usage:
#   unwind-rc.sh --project <name> --key <vX.Y.Z> [--dry-run] [--confirm] [--force]
#
# Options:
#   --project NAME   project name; must match a directory under projects/<name>/ and
#                    be registered in projects.cfg (PGAI_PROJECT_NAME is the
#                    fallback when the flag is absent)
#   --key VERSION    RC version to unwind; must match the Active RC in release-state.md
#                    (or use --force to bypass the Active-RC-matches-version check)
#   --dry-run        print the unwind plan and exit 0 without modifying anything
#   --confirm        answer the confirmation prompt non-interactively (for a caller
#                    with no terminal on stdin); bypasses NO pre-flight check --
#                    the Active-RC mismatch check still fires
#   --force          bypass the Active-RC-version mismatch check (and, so a scripted
#                    caller can use it alone, the confirmation prompt); does NOT
#                    bypass the project-existence, version-format, or shipped-tag
#                    checks
#   --help, -h       print this help and exit 0
#
# This script parses flags, prints --help, and resolves the typed project
# name (--project, else PGAI_PROJECT_NAME). Everything else -- the pre-flight
# checks, the plan, the confirmation prompt and steps a-l -- is
# pgai_agent_kanban.cm.unwind.unwind_rc, reached through pp_run_ops. POST
# /operations/unwind-rc calls the same function in process; its body's
# "confirm" is the confirmation and its "force" never stands in for it.
#
# Pre-flight checks (any failure exits 1):
#   1. HALT file in place — either $KANBAN_ROOT/HALT (global) or
#      $KANBAN_ROOT/projects/<name>/HALT (per-project).  If neither exists,
#      error with instructions to halt first.
#   2. Project directory exists under projects/<name>/.
#   3. Version not in git tag list (unwinding a shipped tag is not supported).
#   4. Active RC in release-state.md matches <version>  (bypassable with --force).
#
# Actions:
#   a. Backup key state stores to <temp_root>/unwind-rc-<version>-backup-<ts>/
#   b. Inventory + confirmation prompt (or skip if --confirm/--force/--dry-run)
#   c. Git unwind: checkout prefixed main, delete local rc/<version>, and delete
#      it on origin only for a project that pushes (see "Origin" below)
#   d. Task folder state: mark matching task folders WONT-DO
#   e. Queue caches: flip matching entries to [x]
#   f. Requirements file: rename to *.SUPERSEDED-on-cancel-<ts>.md
#   g. PM plan markers: remove matching .materialized.* files
#   h. Priority backlog: flip bundled PRIORITY entries back to [ ]
#   i. Bug backlog: flip bundled BUG entries back to [ ]
#   j. release-state.md: reset Active RC to none
#   k. Discovery state cache: remove cancelled-version-related files
#   l. Per-RC release-state JSON: flip outcome to cancelled
#
# Origin:
#   Every origin call (the plan's `git ls-remote` check and step c's
#   `git push origin --delete`) is gated on the shared push decision
#   (pgai_agent_kanban.lib.push_decision, as cancel-rc applies it). A project
#   pushes only when it is registered in projects.cfg, its own project.cfg
#   exists and names dev_tree_path, and push_to_remote is not `false`.
#   - push_to_remote = false: prints
#       [unwind-rc] Push policy: push_to_remote=false — skipping origin operations. Local cleanup only.
#     and contacts no remote.
#   - not resolved (not registered, no project.cfg, or no dev_tree_path in it):
#     prints one line
#       [unwind-rc] skipped-unresolved-project: project '<name>': missing <piece>: ...; nothing pushed and no remote contacted
#     and contacts no remote.
#   The local steps run the same way in every case. REPO_ROOT and
#   PGAI_DEV_TREE_PATH choose where they run and never make a project pushable.
#
# Exit codes:
#   0   success (or --dry-run after printing plan)
#   1   pre-flight failure or user declined confirmation
#   2   partial completion (backup created but a step failed; inspect and recover)
#
# Configuration:
#   KANBAN_ROOT  — path to the kanban live install root
#                  (default: $HOME/pgai_agent_kanban)
#   REPO_ROOT    — the repository to unwind in, instead of the project's
#                  configured dev tree
#   PGAI_DEV_TREE_PATH — path to the dev tree git clone, used for the local
#                  steps when the project config names none (derived from
#                  kanban.cfg [paths] dev_tree_path when not set); such a
#                  project is unresolved and contacts no remote

# ---------------------------------------------------------------------------
# Argument parsing
# Done before strict mode so error messages print cleanly on bad flags.
# ---------------------------------------------------------------------------
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

# Source operator_args.sh (canonical flag parsing and --help rendering).
# shellcheck source=lib/operator_args.sh
source "${SCRIPT_DIR}/lib/operator_args.sh"

# Declared flag vocabulary: drives --help output; flags the command accepts.
OPERATOR_VALID_FLAGS=(project key dry-run confirm force help)

# Parse: operator_args_parse normalizes -h to --help before delegation.
# Value-taking: project, key.
# Boolean: dry-run, confirm, force, help.
operator_args_parse "$@"

# Handle --help / -h.
if argparse_has "help"; then
    operator_args_render_help_for_flags "unwind-rc.sh" \
        "Fully unwind an in-flight release candidate across all state stores." \
        OPERATOR_VALID_FLAGS \
        "" \
        "  (--key: RC version to unwind, format vX.Y.Z; must match Active RC in release-state.md)" \
        "  (--confirm: answers the confirmation prompt non-interactively; bypasses no pre-flight check)" \
        "  (--force: skips Active-RC-version mismatch check and the prompt; does not bypass existence or tag checks)"
    exit 0
fi

# Reject unexpected positional arguments (hard-cut: no positional fallback).
if [[ "${#ARGPARSE_POSITIONAL[@]}" -gt 0 ]]; then
    echo "ERROR: unexpected positional argument: ${ARGPARSE_POSITIONAL[0]}" >&2
    echo "Usage: unwind-rc.sh --project <name> --key <vX.Y.Z> [--dry-run] [--confirm] [--force]" >&2
    exit 1
fi

# Reject any flag not in the declared vocabulary.
operator_args_validate_known "$(basename "$0")" OPERATOR_VALID_FLAGS || exit 1

# Require --project and --key; emit error for missing-value flags.
if argparse_missing "project"; then
    echo "ERROR: --project requires a value" >&2
    exit 1
fi
if argparse_missing "key"; then
    echo "ERROR: --key requires a value" >&2
    exit 1
fi

# Extract values.
PROJECT_ARG="$(operator_args_project)"
VERSION_ARG="$(operator_args_get key)"
DRY_RUN=0; argparse_has "dry-run" && DRY_RUN=1
CONFIRM=0; argparse_has "confirm" && CONFIRM=1
FORCE=0;   argparse_has "force"   && FORCE=1

if [[ -z "$PROJECT_ARG" || -z "$VERSION_ARG" ]]; then
    echo "ERROR: --project and --key are required." >&2
    echo "Usage: unwind-rc.sh --project <name> --key <vX.Y.Z> [--dry-run] [--confirm] [--force]" >&2
    exit 1
fi

# ---------------------------------------------------------------------------
# Bootstrap: self-locate → source shell-env → fail loud
# ---------------------------------------------------------------------------
# Must happen before the first use of PGAI_AGENT_KANBAN_ROOT_PATH so the
# script runs from a fresh shell without manual pre-sourcing.  Explicit
# operator exports win via env_bootstrap.sh's idempotency guard.
# shellcheck source=lib/env_bootstrap.sh
source "$(dirname "${BASH_SOURCE[0]}")/lib/env_bootstrap.sh" || exit 1

KANBAN_ROOT="${PGAI_AGENT_KANBAN_ROOT_PATH}"

# Source env/config before strict mode — these files may use unset vars or
# return non-zero from innocent operations.
[[ -f "$KANBAN_ROOT/bashrc" ]]               && source "$KANBAN_ROOT/bashrc"
[[ -f "$KANBAN_ROOT/env" ]]                  && source "$KANBAN_ROOT/env"
[[ -f "$HOME/.config/pgai-kanban.cfg" ]]     && source "$HOME/.config/pgai-kanban.cfg"

# shellcheck source=lib/ini_parser.sh
[[ -f "${SCRIPT_DIR}/lib/ini_parser.sh" ]] && source "${SCRIPT_DIR}/lib/ini_parser.sh"

# kanban.cfg [paths] dev_tree_path backs PGAI_DEV_TREE_PATH when it is unset;
# unwind_rc uses it for a project whose config names no dev tree.
if [[ -f "$KANBAN_ROOT/kanban.cfg" ]]; then
    export PGAI_DEV_TREE_PATH="${PGAI_DEV_TREE_PATH:-$(read_ini "$KANBAN_ROOT/kanban.cfg" paths dev_tree_path "")}"
fi

# shellcheck source=lib/temp.sh
source "${SCRIPT_DIR}/lib/temp.sh"
# shellcheck source=lib/pp_run_ops.sh
source "${SCRIPT_DIR}/lib/pp_run_ops.sh"

# The backup goes under the framework temp root, resolved once here.
_TMP_ROOT="$(pgai_temp_dir)"

set -euo pipefail

# ---------------------------------------------------------------------------
# Unwind
# unwind_rc prints the plan and the narration, asks for confirmation on this
# script's stdin unless --confirm, --force or --dry-run was given (--force
# answers the prompt here, as it always has), and exits with the code
# documented in the header. REPO_ROOT, when set, names the repository
# directly instead of the project's configured dev tree.
# ---------------------------------------------------------------------------
UNWIND_ARGS=(--kanban-root "$KANBAN_ROOT" --project "$PROJECT_ARG" --key "$VERSION_ARG"
             --temp-root "$_TMP_ROOT")
[[ "$DRY_RUN" -eq 1 ]] && UNWIND_ARGS+=(--dry-run)
[[ "$CONFIRM" -eq 1 ]] && UNWIND_ARGS+=(--confirm)
[[ "$FORCE"   -eq 1 ]] && UNWIND_ARGS+=(--force)
if [[ -n "${REPO_ROOT:-}" ]]; then
    UNWIND_ARGS+=(--repo-root "$REPO_ROOT")
fi
pp_run_ops pgai_agent_kanban.cm unwind_rc "${UNWIND_ARGS[@]}"
