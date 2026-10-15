#!/usr/bin/env bash
# team/scripts/cm/cancel-rc.sh
# Human-invoked: cleanly abandons an active release candidate branch.
# The remote it touches is the project's git remote: project.cfg [project]
# git_remote_name, origin when absent or blank (pp_git_remote_name /
# pgai_agent_kanban.lib.git_remote); "origin" below means that remote.
#
# Usage:
#   cm/cancel-rc.sh --project <name> --key vX.Y.Z [--yes] [--force] [--help]
#
# Both --project and --key are REQUIRED. Positional invocation is not supported.
#
# --project <name>   project name (required; overrides PGAI_PROJECT_NAME env var)
# --key vX.Y.Z       RC version to cancel (required; format: vX.Y.Z)
# --yes              skip interactive confirmation prompt (for scripted/non-interactive use)
# --force            bypass only the Active-RC-matches-version check; never the
#                    confirmation. The RC branch and per-RC record of vX.Y.Z are
#                    cleaned up and release-state.md, which then describes a
#                    different RC, is left unchanged.
# --help             print this message and exit 0
#
# This script parses flags, prints --help, and resolves the project name
# (--project, else PGAI_PROJECT_NAME). Everything else -- the checks, the
# confirmation prompt, the git steps, the release-state.md reset and the
# per-RC archive -- is pgai_agent_kanban.cm.cancel.cancel_rc, reached through
# pp_run_ops. POST /operations/cancel-rc calls the same function in process.
#
# =============================================================================
# FOOTPRINT — what this script touches and what it deliberately leaves alone
# =============================================================================
#
# TOUCHES (modifies or deletes):
#   rc/<version> branch on origin
#     - Deleted via `git push origin --delete rc/<version>` if the branch
#       exists on origin and the project pushes (see "Push decision" below).
#       Skipped (idempotent) if already absent.
#   rc/<version> branch locally
#     - Deleted via `git branch -D rc/<version>` if the branch exists locally.
#       Switches to main first if currently on the rc branch.
#       Skipped (idempotent) if already absent.
#   $KANBAN_ROOT/projects/<name>/release-state.md
#     - Three fields are reset to `none`:
#         Active RC
#         RC Opened At
#         RC Opened By Task
#     - Skipped (idempotent) if Active RC is already `none`.
#   $KANBAN_ROOT/projects/<name>/release-state/<version>.json
#     - Marked cancelled, then moved to release-state/history/ as
#       <version>-cancelled-<timestamp>.json, freeing the version for re-use.
#   main branch (local)
#     - After resetting release-state.md the script checks out main and runs
#       `merge --ff-only origin/main` (when the project pushes).
#       This ensures the repo is on main after cancellation.
#
# DOES NOT TOUCH (explicitly preserved):
#   main branch on origin   — never pushed
#   git tags                — no tags are created, moved, or deleted
#   ## Last Released field in release-state.md
#                           — read from the file before the rewrite and
#                             written back byte-for-byte; cancel never
#                             clears, alters, or computes this value. The
#                             git tag created by cm/release.sh remains the
#                             canonical Last Released record (roles/CM.md
#                             "Release state" — the field is monotonic
#                             and latching; cm-cancel-rc must never clear it).
#   Task folders            — task status.md files are not modified by this script;
#                             caller is responsible for marking tasks WONT-DO
#   Queue files             — coder_backlog.md, cm_backlog.md, etc. are not modified
#   requirements/ files     — requirements files are not renamed or deleted
#   priority/ files         — priority files are not modified
#   bugs/ files             — bug files are not modified
#   Artifacts               — task artifacts/ directories are not modified
#   Dev tree source files   — no files under the git worktree are modified
#   $KANBAN_ROOT/archive/<name>/
#                           — a cancelled RC never runs the post-release
#                             archive; only cm/release.sh's finalize does,
#                             after a release ships
#
# For a full unwind (task folders, queue markers, requirements files,
# priority files, bug files, PM plan markers) use unwind-rc.sh instead.
#
# If you need to re-run the CM release step without deleting the rc branch, use
# reset.sh --project <name> --key <version> instead.
#
# =============================================================================
#
# Behavior:
#   1.  Validates version format
#   2.  Validates repository state (must be on main or rc/<version>)
#   3.  git fetch origin   (skipped when the project does not push, and skipped
#       with a narrated line when PGAI_AGENT_ROLE names a role other than
#       cm -- only CM touches the remote; the guard would refuse the fetch)
#   4.  Reads Active RC from project-scoped release-state.md (live install) and
#       verifies Active RC = <version> (or none; --force bypasses this check)
#   5.  Lists pending tasks for the cancelled RC version
#   6.  Prompts for confirmation (unless --yes passed)
#   7.  Deletes rc/<version> branch from origin (if the project pushes and the
#       branch exists there)
#   8.  Deletes rc/<version> branch locally (if it exists)
#   9.  Resets project-scoped release-state.md: clears Active RC + RC Opened At +
#       RC Opened By Task. The ## Last Released field is read before the rewrite
#       and carried forward unchanged — the git tag created by cm/release.sh
#       remains the canonical Last Released record.
#  10.  Marks the per-RC JSON cancelled and archives it to release-state/history/
#  11.  Prints success message and exits 0
#
# release-state.md is the project-scoped live install file only:
#   $KANBAN_ROOT/projects/<name>/release-state.md
# The dev tree's team/release-state.md is NOT read or written by this script.
#
# Push decision:
#   Origin is touched only for a project the shared push-decision resolver
#   (team/pgai_agent_kanban/lib/push_decision.py) answers "push" for --
#   registered in projects.cfg, with its own project.cfg naming dev_tree_path
#   and not setting push_to_remote = false. A registered project with no
#   push_to_remote key still pushes. push_to_remote = false, or an unresolved
#   project (not registered, no project.cfg, or no dev_tree_path in it),
#   contacts no remote at all: no fetch, no ls-remote, no push, no pull from
#   origin. REPO_ROOT and PGAI_DEV_TREE_PATH never make a project pushable.
#   An unresolved project logs one line naming the missing piece, before the
#   repository is resolved:
#     [cm-cancel-rc] skipped-unresolved-project: project '<name>': missing <piece>: ...
#
# Idempotency:
#   Safe to re-run if a step failed midway. Steps 7 and 8 are skipped if the
#   branch does not exist. Step 9 is skipped if Active RC is already none.
#
# Exit codes:
#   0  cancelled, or nothing left to cancel
#   1  usage error, a refused check, or the confirmation declined
#   N  a git step failed: git's own exit code
#
# Configuration:
#   PGAI_PROJECT_NAME — fallback project name (when --project flag not used)
#   REPO_ROOT         — override path to the repository root (normally derived
#                       from the project's project.cfg dev_tree_path)
#   KANBAN_ROOT       — path to the kanban root (default: $HOME/pgai_agent_kanban)

# --- Argument parsing ---
# Done before strict mode so error messages are clean.
PROJECT_ARG=""
VERSION=""
YES_FLAG=0
FORCE_FLAG=0

_cm_cancel_rc_usage() {
  echo "Usage: $(basename "$0") --project <name> --key vX.Y.Z [--yes] [--force] [--help]" >&2
  echo "" >&2
  echo "  --project <name>  project name (required)" >&2
  echo "  --key vX.Y.Z      RC version to cancel (required; format: vX.Y.Z)" >&2
  echo "  --yes             skip interactive confirmation prompt" >&2
  echo "  --force           bypass the Active-RC-matches-version check (not the prompt)" >&2
  echo "  --help            print full usage and footprint documentation" >&2
}

_cm_cancel_rc_help() {
  cat <<HELPTEXT
Usage: $(basename "$0") --project <name> --key vX.Y.Z [--yes] [--force] [--help]

Cancel an active release candidate branch and reset release-state.md.

Both --project and --key are REQUIRED. Positional invocation is not supported.

Arguments:
  --project <name>  project name; drives project resolution via the pp layer
  --key vX.Y.Z      RC version to cancel (format: vX.Y.Z, e.g. v0.15.4)
  --yes             skip the interactive confirmation prompt
  --force           bypass only the Active-RC-matches-version check; the
                    prompt still runs unless --yes is also given. The RC
                    branch and its per-RC record are cleaned up and
                    release-state.md, which then names a different RC,
                    is left unchanged
  --help            print this message and exit 0

FOOTPRINT — what this script touches vs. leaves alone. <remote> is the
project's git remote: project.cfg [project] git_remote_name, origin when
that key is absent or blank.

  TOUCHES:
    <remote>/rc/<version>        deleted (git push <remote> --delete)
    local rc/<version>           deleted (git branch -D)
    release-state.md             Active RC / RC Opened At / RC Opened By Task
                                 reset to 'none'
    main branch (local)          checked out; fast-forward pulled from <remote> when push_to_remote=true

  DOES NOT TOUCH:
    main branch on <remote>      never pushed (operator pushes via finalize-release.sh if needed)
    git tags                     never created, moved, or deleted
    release-state.md ## Last Released field
                                 read before the rewrite and written back
                                 unchanged (canonical value comes from the
                                 git tag; cancel never clears it)
    task folders / status.md     not modified; caller must mark tasks WONT-DO
    queue files                  coder_backlog.md, cm_backlog.md, etc. not modified
    requirements/ files          not renamed or deleted
    priority/ files              not modified
    bugs/ files                  not modified
    artifacts/                   not modified
    dev tree source files        not modified

  NOTE: For a full unwind (task folders, queue markers, requirements file,
  priority files, bug files, PM plan markers) use the top-level
  scripts/unwind-rc.sh instead.

  NOTE: To re-run the CM release step without deleting the rc branch, use
  reset.sh --project <name> --key <version> instead.

Configuration:
  PGAI_PROJECT_NAME   fallback project name (when --project flag not used)
  REPO_ROOT           override path to the repository root
  KANBAN_ROOT         path to the kanban root (default: \$HOME/pgai_agent_kanban)
HELPTEXT
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    --help|-h)
      _cm_cancel_rc_help
      exit 0
      ;;
    --project)
      if [[ -z "${2:-}" ]]; then
        echo "ERROR: --project requires a value" >&2
        echo "" >&2
        _cm_cancel_rc_usage
        exit 1
      fi
      PROJECT_ARG="$2"
      shift 2
      ;;
    --key)
      if [[ -z "${2:-}" ]]; then
        echo "ERROR: --key requires a value" >&2
        echo "" >&2
        _cm_cancel_rc_usage
        exit 1
      fi
      VERSION="$2"
      shift 2
      ;;
    --yes|-y)
      YES_FLAG=1
      shift
      ;;
    --force)
      FORCE_FLAG=1
      shift
      ;;
    --*)
      echo "ERROR: unknown flag: $1" >&2
      echo "" >&2
      _cm_cancel_rc_usage
      exit 1
      ;;
    *)
      echo "ERROR: positional arguments are not supported; use --project and --key flags" >&2
      echo "" >&2
      _cm_cancel_rc_usage
      exit 1
      ;;
  esac
done

if [[ -z "$VERSION" ]]; then
  echo "ERROR: missing required flag --key vX.Y.Z" >&2
  echo "" >&2
  _cm_cancel_rc_usage
  exit 1
fi

if [[ -z "$PROJECT_ARG" && -z "${PGAI_PROJECT_NAME:-}" ]]; then
  echo "ERROR: missing required flag --project <name>" >&2
  echo "" >&2
  _cm_cancel_rc_usage
  exit 1
fi

# shellcheck source=../lib/env_bootstrap.sh
source "$(dirname "${BASH_SOURCE[0]}")/../lib/env_bootstrap.sh"

# --- Resolve script directory ---
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

# --- Source optional config files (BEFORE strict mode) ---
# The kanban bashrc/env may have unset vars, non-zero returns, or interactive
# aliases that would trip strict mode. Source them first.
KANBAN_ROOT="${PGAI_AGENT_KANBAN_ROOT_PATH}"
TEAM_ROOT="${PGAI_AGENT_KANBAN_ROOT_PATH}"
[[ -f "$KANBAN_ROOT/bashrc" ]] && source "$KANBAN_ROOT/bashrc"
[[ -f "$KANBAN_ROOT/env" ]] && source "$KANBAN_ROOT/env"
[[ -f "$HOME/.config/pgai-kanban.cfg" ]] && source "$HOME/.config/pgai-kanban.cfg"
# Source ini_parser.sh for read_ini (needed before project_paths.sh is available).
# shellcheck source=lib/ini_parser.sh
[[ -f "${SCRIPT_DIR}/../lib/ini_parser.sh" ]] && source "${SCRIPT_DIR}/../lib/ini_parser.sh"
# Source kanban.cfg — INI format replaces legacy config.cfg
if [[ -f "$TEAM_ROOT/kanban.cfg" ]]; then
    export PGAI_KANBAN_PM_MODE="${PGAI_KANBAN_PM_MODE:-$(read_ini "$TEAM_ROOT/kanban.cfg" chain pm_mode automatic)}"
    export PGAI_DEV_TREE_PATH="${PGAI_DEV_TREE_PATH:-$(read_ini "$TEAM_ROOT/kanban.cfg" paths dev_tree_path "")}"
fi

# --- Source project path helpers ---
# shellcheck source=lib/project_paths.sh
source "$(dirname "${BASH_SOURCE[0]}")/../lib/project_paths.sh"

# --- Source the python-module runner (cwd-independent PYTHONPATH) ---
# shellcheck source=../lib/pp_run_ops.sh
source "$(dirname "${BASH_SOURCE[0]}")/../lib/pp_run_ops.sh"

# --- Enable strict mode for our own code ---
set -euo pipefail

# --- Resolve project context ---
# Resolution order: --project flag > PGAI_PROJECT_NAME env var > error.
# pp_require_project_context prints a diagnostic and returns 1 when neither is set.
PROJECT_NAME="$(pp_require_project_context "${PROJECT_ARG:-}")" || {
  echo "" >&2
  echo "ERROR: project context is required." >&2
  echo "  Pass --project <name>, or set PGAI_PROJECT_NAME=<name> in the environment." >&2
  exit 1
}

# --- Cancel ---
# cancel_rc prints the plan and the narration, asks for confirmation on this
# script's stdin unless --yes was given, and exits with the code documented
# in the header. REPO_ROOT, when set, names the repository directly instead
# of project.cfg's dev_tree_path.
CANCEL_ARGS=(--kanban-root "$KANBAN_ROOT" --project "$PROJECT_NAME" --key "$VERSION")
if [[ $YES_FLAG -eq 1 ]]; then
  CANCEL_ARGS+=(--yes)
fi
if [[ $FORCE_FLAG -eq 1 ]]; then
  CANCEL_ARGS+=(--force)
fi
if [[ -n "${REPO_ROOT:-}" ]]; then
  CANCEL_ARGS+=(--repo-root "$REPO_ROOT")
fi
pp_run_ops pgai_agent_kanban.cm cancel_rc "${CANCEL_ARGS[@]}"
