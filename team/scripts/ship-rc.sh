#!/usr/bin/env bash
# ship-rc.sh
# Human-invoked: manual escape hatch for shipping a release candidate.
#
# Usage:
#   ship-rc.sh --project <name> --key vX.Y.Z [--dry-run] [--help]
#   ship-rc.sh --help
#
# Both --project and --key are REQUIRED. Positional invocation is not supported.
#
# --project <name>   project name (required; exact, or an unambiguous prefix of
#                    a registered name; PGAI_PROJECT_NAME is the fallback when
#                    the flag is absent)
# --key vX.Y.Z       release version (required; format: vX.Y.Z, e.g. v0.5.0)
# --dry-run          print the plan (project, version, branch names) and exit 0;
#                    no git operations are performed
# --help             print this message and exit 0
#
# This script parses flags, prints --help and the usage errors, and resolves
# the typed project name (--project, else PGAI_PROJECT_NAME). Everything else
# -- the pre-flight checks, the plan, the git steps, the release hooks and the
# release-state.md reset -- is pgai_agent_kanban.cm.ship.ship_rc, reached
# through pp_run_ops. POST /operations/ship-rc calls the same function in
# process.
#
# Behavior (the step labels ship_rc prints):
#   Pre-flight: version format; project name and branch_prefix from
#       project.cfg; the repository is a git repository with a clean
#       working tree; the tag PREFIXED_v<version> does not exist yet
#   1.  Verify PREFIXED_rc/<version> exists locally and on origin
#   2.  git fetch origin --tags
#   3.  git checkout PREFIXED_main && git merge --ff-only origin/PREFIXED_main
#   3b. Pre-squash hook: resolve and run (if present); HALT on failure
#   4.  git merge --squash PREFIXED_rc/<version>  (modify/delete-only
#       conflicts are resolved by taking the RC's side)
#   5.  git commit -m "Release <version>"
#   5a. Fidelity gate: git diff --quiet PREFIXED_rc/<version> PREFIXED_main
#       Exit 1 before tag if trees diverge.
#   5b. Pre-tag hook: resolve and run (if present); HALT on failure
#   6.  git tag PREFIXED_v<version>
#   6b. Post-tag hook: resolve and run (if present); failure is logged, not blocking
#   7.  git push origin PREFIXED_main refs/tags/PREFIXED_v<version>
#       (the release tag's own ref only, never every local tag)
#   8.  git push origin --delete PREFIXED_rc/<version>
#   9.  git branch -D PREFIXED_rc/<version>
#   Post-ship: release-state.md Active RC / RC Opened At / RC Opened By Task
#       reset to none (Last Released is left untouched; the tag is canonical)
#
# Push decision: origin is touched only for a project the shared push-decision
# resolver (team/pgai_agent_kanban/lib/push_decision.py) answers "push" for --
# registered in projects.cfg, with its own project.cfg naming dev_tree_path
# and not setting push_to_remote = false. A registered project with no
# push_to_remote key still pushes. REPO_ROOT and PGAI_DEV_TREE_PATH never make
# a project pushable.
#
# Local-only projects: when project.cfg sets [project] push_to_remote = false,
# or the project is unresolved (not registered, no project.cfg, or no
# dev_tree_path in it), every origin step (the origin branch check, the
# fetch, the fast-forward from origin, both pushes) is skipped and narrated as
# "[push_to_remote=false] skipping origin <kind> for <project>: <command>";
# the squash, tag and local branch delete still happen. An unresolved project
# first logs one line naming the missing piece:
#   [ship-rc] skipped-unresolved-project: project '<name>': missing <piece>: ...
#
# Hook resolution (all three phases use the same precedence order):
#   (a) project.cfg [hooks] cm_release_<phase>_hook        (cfg)
#   (b) projects/<name>/hooks/cm-release-<phase>.sh        (kanban-side)
#   (c) <dev_tree_path>/.pgai/hooks/cm-release-<phase>.sh  (in-repo)
# Each phase prints exactly one resolution line before running (or "none configured").
# Set cm_release_<phase>_hook_required=true in project.cfg to HALT when absent.
# Resolution is team/scripts/lib/cm_release_hooks.sh, shared with cm/release.sh.
#
# Branch and tag prefixing
# ------------------------
# When the project's project.cfg sets [project] branch_prefix (e.g. "ai_"),
# ALL git refs are prefixed: rc branches become ai_rc/<version>, main becomes
# ai_main, and the tag becomes ai_v<version>.
# When branch_prefix is empty (the common case), behavior is identical to earlier
# versions of this script: bare rc/<version>, main, and v<version> tag.
#
# Exit codes:
#   0   shipped (or --dry-run / --help)
#   1   usage error, refused pre-flight check, HALT, or a failed git step
#
# Configuration:
#   REPO_ROOT — path to the repository root (default: per-project dev_tree_path from
#               project.cfg, then global PGAI_DEV_TREE_PATH, then script's parent-parent dir).
#               Only a project.cfg dev_tree_path project can push; the fallbacks
#               serve the local steps of a project that contacts no remote.

# --- Bootstrap: self-locate → source shell-env → fail loud ---
# Must happen before the first use of PGAI_AGENT_KANBAN_ROOT_PATH so the
# script runs from a fresh shell without manual pre-sourcing.  Explicit
# operator exports win via env_bootstrap.sh's idempotency guard.
# shellcheck source=lib/env_bootstrap.sh
# BLOCK-WORTHY: an unreachable bootstrap prelude leaves every downstream
# tunable/path resolution undefined for this release-shipping script.
# CENSUS-ANCHOR: ship-rc-env-bootstrap-01
source "$(dirname "${BASH_SOURCE[0]}")/lib/env_bootstrap.sh" || exit 1

_ship_rc_usage() {
  echo "Usage: $(basename "$0") --project <name> --key vX.Y.Z [--dry-run] [--help]" >&2
  echo "" >&2
  echo "  --project <name>  project name (required)" >&2
  echo "  --key vX.Y.Z      release version (required; format: vX.Y.Z)" >&2
  echo "  --dry-run         print plan and exit 0; no git operations performed" >&2
  echo "  --help            print full documentation" >&2
}

_ship_rc_help() {
  cat <<'EOF'
Usage: ship-rc.sh --project <name> --key vX.Y.Z [--dry-run] [--help]

Ship a release candidate by squash-merging it directly into main (single-lane).

Both --project and --key are REQUIRED. Positional invocation is not supported.

Arguments:
  --project <name>  Required. Project name; drives project resolution via the pp layer.
  --key vX.Y.Z      Required. Release version in format vX.Y.Z (e.g. v0.5.0).
  --dry-run         Print the plan (project, version, branch names) and exit 0.
                    No git operations are performed. Safe to use from the API.
  --help            Print this help and exit 0.

Branch and tag prefixing
------------------------
When the project's project.cfg sets [project] branch_prefix (e.g. "ai_"),
ALL git refs operated on by this script are prefixed:

  rc branch:     ai_rc/<version>    (instead of rc/<version>)
  main:          ai_main            (instead of main)
  tag:           ai_v<version>      (instead of v<version>)

When branch_prefix is empty or absent (the common case for pure-AI shops),
this script behaves identically to the earlier un-prefixed version.

The script will error pre-flight if it cannot resolve the project context
or if the prefixed RC branch does not exist locally and on origin.

Environment variables
---------------------
  REPO_ROOT                    Override the repository root directory (default:
                               per-project dev_tree_path from project.cfg, then
                               global PGAI_DEV_TREE_PATH, then script's parent-parent)
  PGAI_AGENT_KANBAN_ROOT_PATH  Path to kanban root (default: ~/pgai_agent_kanban)
EOF
}

# --- Argument parsing ---
PROJECT_ARG=""
VERSION=""
DRY_RUN=""

while [[ $# -gt 0 ]]; do
  case "$1" in
    --help|-h)
      _ship_rc_help
      exit 0
      ;;
    --project)
      if [[ -z "${2:-}" ]]; then
        echo "ERROR: --project requires a value" >&2
        echo "" >&2
        _ship_rc_usage
        exit 1
      fi
      PROJECT_ARG="$2"
      shift 2
      ;;
    --key)
      if [[ -z "${2:-}" ]]; then
        echo "ERROR: --key requires a value" >&2
        echo "" >&2
        _ship_rc_usage
        exit 1
      fi
      VERSION="$2"
      shift 2
      ;;
    --dry-run)
      DRY_RUN="1"
      shift
      ;;
    --*)
      echo "ERROR: unknown flag: $1" >&2
      echo "" >&2
      _ship_rc_usage
      exit 1
      ;;
    *)
      echo "ERROR: positional arguments are not supported; use --project and --key flags" >&2
      echo "" >&2
      _ship_rc_usage
      exit 1
      ;;
  esac
done

if [[ -z "$VERSION" ]]; then
  echo "ERROR: missing required flag --key vX.Y.Z" >&2
  echo "" >&2
  _ship_rc_usage
  exit 1
fi

if [[ -z "$PROJECT_ARG" && -z "${PGAI_PROJECT_NAME:-}" ]]; then
  echo "ERROR: missing required flag --project <name>" >&2
  echo "" >&2
  _ship_rc_usage
  exit 1
fi

# --- Source optional config files (BEFORE strict mode) ---
# The kanban bashrc/env may have unset vars, non-zero returns, or interactive
# aliases that would trip strict mode. Source them first; a release hook
# inherits whatever they export.
# PGAI_AGENT_KANBAN_ROOT_PATH is set by env_bootstrap.sh (sourced above) or the operator.
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
KANBAN_ROOT="${PGAI_AGENT_KANBAN_ROOT_PATH}"
[[ -f "$KANBAN_ROOT/bashrc" ]] && source "$KANBAN_ROOT/bashrc"
[[ -f "$KANBAN_ROOT/env" ]] && source "$KANBAN_ROOT/env"
# $HOME/.config/pgai-kanban.cfg is operator-local bash config; sourced as-is.
[[ -f "$HOME/.config/pgai-kanban.cfg" ]] && source "$HOME/.config/pgai-kanban.cfg"
# shellcheck source=lib/ini_parser.sh
[[ -f "${SCRIPT_DIR}/lib/ini_parser.sh" ]] && source "${SCRIPT_DIR}/lib/ini_parser.sh"
# shellcheck source=lib/dev_tree.sh
source "${SCRIPT_DIR}/lib/dev_tree.sh"
if [[ -f "$KANBAN_ROOT/kanban.cfg" ]]; then
    export PGAI_KANBAN_PM_MODE="${PGAI_KANBAN_PM_MODE:-$(read_ini "$KANBAN_ROOT/kanban.cfg" chain pm_mode automatic)}"
fi
# kanban.cfg [paths] dev_tree_path backs PGAI_DEV_TREE_PATH when it is unset;
# ship_rc uses it for a project whose config names no dev tree.
export PGAI_DEV_TREE_PATH="${PGAI_DEV_TREE_PATH:-$(resolve_global_dev_tree)}"

# shellcheck source=lib/pp_run_ops.sh
source "${SCRIPT_DIR}/lib/pp_run_ops.sh"

set -euo pipefail

# --- Ship ---
# Resolution order for the project: --project flag > PGAI_PROJECT_NAME.
# ship_rc resolves the typed name (exact, then unambiguous prefix), prints the
# plan or the narration, and exits with the code documented in the header.
# REPO_ROOT, when set, names the repository directly instead of the
# project's configured dev tree.
if [[ -n "$PROJECT_ARG" ]]; then
    export PGAI_PROJECT_NAME="$PROJECT_ARG"
fi
SHIP_ARGS=(--kanban-root "$KANBAN_ROOT" --project "${PGAI_PROJECT_NAME}" --key "$VERSION")
[[ -n "$DRY_RUN" ]] && SHIP_ARGS+=(--dry-run)
if [[ -n "${REPO_ROOT:-}" ]]; then
    SHIP_ARGS+=(--repo-root "$REPO_ROOT")
fi
pp_run_ops pgai_agent_kanban.cm ship_rc "${SHIP_ARGS[@]}"
