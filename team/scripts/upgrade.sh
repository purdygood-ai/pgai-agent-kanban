#!/usr/bin/env bash
# upgrade.sh — Upgrade an existing pgai-agent-kanban installation.
#
# This is the UPGRADE path only.  If the target is NOT an existing kanban
# installation, upgrade.sh refuses and directs the operator to install.sh.
#
# TWO-PHASE UPGRADE ARCHITECTURE
# --------------------------------
# When invoked WITHOUT --phase2 (the normal operator invocation), this script
# acts as the PHASE 1 BOOTSTRAP: it parses args, validates the dev tree,
# creates the pre-upgrade backup tarball, probes the dev-tree's upgrade.sh
# (bash -n syntax check and --phase2 support check), then hands off execution
# via exec to <dev_tree>/team/scripts/upgrade.sh with --phase2 and the
# versioned protocol flags.  Phase 1 performs NO deposit, NO config changes,
# and NO crontab work — it is a small, stable bootstrap that changes rarely.
#
# PHASE 2 (the actual upgrade — deposit, state preservation, crontab, VERSION
# stamp, destination-keyed migrations) runs inside the DEV TREE's upgrade.sh,
# which executes with full knowledge of the version being installed. After
# the VERSION stamp, phase 2 applies every scripts/migrations/
# migrate_vX.Y.Z_to_vA.B.C.sh script whose destination version falls in
# (installed, target] via the SOURCE TREE's team/scripts/migrations/
# run-migrations.sh (the runner and migrations being deposited, never the
# installed copies) — see scripts/migrations/README.md for the selection rule
# and contract.
#
# PARTIAL DEPOSIT STOPS THE RUN: when any managed item that differs is left
# undeposited — no TTY and no --force, or the operator answers N or q at a
# prompt — phase 2 stops before the scheduler hooks, the VERSION stamp and
# the migrations, lists the skipped items, and exits non-zero with the
# --force re-run command. VERSION keeps the old version, so the re-run
# selects every migration from it. A managed directory whose every shipped
# file is already identical in place counts as current, not as differing.
#
# If the dev-tree script fails the bash -n probe or lacks --phase2 support,
# phase 1 fails loud with the manual-bootstrap instruction (cp + retry) and
# exits non-zero before depositing anything.
#
# Phase 2 likewise refuses, before depositing anything, a source tree that
# has no team/scripts/lib/version_stamp.sh (the helper that writes VERSION).
#
# Usage:
#   upgrade.sh [VERSION] [OPTIONS]
#
# VERSION (optional, forward-only):
#   A positional git tag to upgrade TO (e.g. "v2.2.0" or "ai_v2.2.0"; the
#   internal branch-prefix form is tolerated and normalized). When given,
#   upgrade.sh resolves the tag via the SAME shared team/scripts/lib/
#   version_resolve.sh mechanism install.sh's version-targeted install uses,
#   lays the upgrade down from that tag's clean detached-worktree tree, and
#   enforces a FORWARD-ONLY rule: the resolved target must be STRICTLY NEWER
#   (semver-aware) than the live install's committed VERSION file, or the
#   upgrade is refused before anything is deposited.
#     - Target == installed version: refused; message points to
#       `install.sh --force` as the repair path for a clean re-lay.
#     - Target <  installed version: refused; message points to a fresh
#       install (`install.sh <VERSION>`) as the sanctioned downgrade path —
#       install.sh lays down everything from scratch and may go backwards.
#   There is no --force override for direction: --force only controls
#   per-file overwrite prompts during deposit, never which way time moves.
#   Omit VERSION to upgrade using whatever the dev tree currently has
#   checked out, exactly as before this argument existed (the default,
#   back-compatible path).
#
# OPTIONS:
#   --force                Overwrite changed managed files without prompting.
#                          Without it, a run that leaves any changed item
#                          undeposited stops before the VERSION stamp and
#                          migrations and exits non-zero (see PARTIAL DEPOSIT).
#   --no-backup            Skip the pre-upgrade backup (advanced; not recommended).
#   --wake-tier small|medium|large|none
#                          Rewrite the crontab's kanban-managed lines for this
#                          tier (other cron lines are kept; none = leave the
#                          crontab alone). When OMITTED, the crontab is not
#                          touched; its tier is detected and reported only.
#   --add-claude-agents    Deploy provider-agent wrappers to $CLAUDE_CONFIG_DIR/agents/
#                          (default ~/.claude/agents/).
#   --add-codex-agents     Deploy provider-agent wrappers to ~/.codex/agents/.
#   --dev-tree PATH        Explicit path to dev tree (overrides kanban.cfg and env).
#   --stamp-version STRING Write STRING verbatim to VERSION; bypass git resolution
#                          and suppress divergence advisory.
#   --help, -h             Show this help.
#
# DEPRECATED OPTIONS (still accepted; emit a deprecation notice to stderr):
#   --crontab-tier small|medium|large|none   Use --wake-tier instead.
#
# INTERNAL (phase-2 re-exec only; do not pass by hand):
#   --phase2               Run as phase 2, the upgrade body; phase 1 passes it
#                          when it execs the dev tree's upgrade.sh.
#   --phase2-protocol N    Version of the phase-1/phase-2 handoff (phase 1
#                          passes 1); required with --phase2.
#   --kanban-root PATH     Kanban root phase 2 upgrades; required with --phase2.
#   --backup PATH          Pre-upgrade backup tarball phase 1 created (passed
#                          only when a backup was made).
#
# ENVIRONMENT:
#   PGAI_AGENT_KANBAN_ROOT_PATH   Kanban root (default: $HOME/pgai_agent_kanban)
#   PGAI_DEV_TREE_PATH            Dev tree path (required when not set in kanban.cfg)
#   CLAUDE_CONFIG_DIR             Claude Code config dir (default: $HOME/.claude);
#                                 --add-claude-agents deploys to its agents/ subdirectory
#
# PROTECTED SET (declared once here; never overwritten on upgrade):
#   Config files:   kanban.cfg, projects.cfg, secrets, shell-env, claude-env,
#                   pseudocron.env and each project's project.cfg
#   Projects dir:   projects/ (all per-project state: tasks, bugs, requirements,
#                   release-state, queues, release-notes). The one exception is
#                   each registered project's seeded intake templates -- see
#                   SEEDED INTAKE TEMPLATES below.
#   Crontab:        left untouched unless --wake-tier / --crontab-tier names a
#                   tier; then only its kanban-managed lines (the marked
#                   pgai-agent-kanban block and lines naming this root) are
#                   rewritten and every other line is kept
#   pseudocron.cfg: never overwritten once it exists (it may be hand-tuned);
#                   the pseudocron hook reads its tier from the file's
#                   "# Tier:" header and is skipped when there is none
#   Agent wrappers: $CLAUDE_CONFIG_DIR/agents/ (default ~/.claude/agents/) and
#                   ~/.codex/agents/ (opt-in only; when --add-claude-agents /
#                   --add-codex-agents are NOT passed, the installed wrappers
#                   are left exactly as-is). Every upgrade still compares them,
#                   read-only, with the subagents/MANIFEST.txt wrappers it
#                   ships: each missing or differing one is warned by name,
#                   e.g. "Agent wrapper out of date: claude/coder.md (...) --
#                   refresh: <root>/scripts/upgrade.sh --add-claude-agents",
#                   and the summary says "current" when all match. A provider
#                   whose agents directory does not exist is neither compared
#                   nor created.
#   Example templates: *_example files are refreshed; the ACTIVE config they seed
#                      (e.g. kanban.cfg, claude-env) is preserved
#   Runtime dirs:   logs/, locks/ — ensure-if-missing; never deleted
#
# NOTE: claude-env is create-if-absent on upgrade (seeded from the freshly
#   refreshed claude-env_example when no live claude-env exists yet — e.g. an
#   install upgrading from before the file was introduced) but, like every
#   other file in the protected set, never overwritten once it exists.
#
# SEEDED INTAKE TEMPLATES (refreshed, not protected):
#   Phase 2 refreshes every registered project's bugs/templates/BUG-TEMPLATE.md,
#   priority/templates/PRIORITY-TEMPLATE.md and
#   requirements/templates/REQUIREMENTS-TEMPLATE.md from the installed
#   templates/project/<workflow_type>/ set, the source create-project.sh seeds
#   from. Identical copies are left alone; missing ones are seeded; a copy that
#   differs (an older release's template or an operator edit) is kept beside
#   itself as <file>.<UTC timestamp>.bak and then overwritten, with one line
#   naming the project, file and backup. No other project file is written.
#
# BACKUP:
#   A tarball of the whole kanban root is created before any change, excluding
#   only volatile directories (logs/, locks/, __pycache__) and *.lock files.
#   The backup INCLUDES the secrets file (cleartext credentials) — keep backups
#   out of public artifacts; gitignore *-backup-*.tar.gz.
#   The tarball is chmod 600 on creation.

set -euo pipefail
# shellcheck source=lib/env_bootstrap.sh
source "$(dirname "${BASH_SOURCE[0]}")/lib/env_bootstrap.sh"

# ---------------------------------------------------------------------------
# PHASE 2 EARLY DETECTION
#
# Scan $@ before anything else.  When --phase2 is present this script is
# running as the dev-tree upgrade body: it was exec'd by the installed
# bootstrap (phase 1).  We extract --kanban-root now so that KANBAN_ROOT is
# correct for the root-relative config sourcing that follows.
#
# The phase2 flags recognised here are removed from the argument list later
# by the main argparse block; this early scan is read-only — it never shifts.
# ---------------------------------------------------------------------------
_PHASE2_MODE=false
_PHASE2_PROTOCOL=""
_PHASE2_KANBAN_ROOT=""
_PHASE2_BACKUP=""

for _p2_arg in "$@"; do
  [[ "$_p2_arg" == "--phase2" ]] && _PHASE2_MODE=true && break
done
unset _p2_arg

if [[ "$_PHASE2_MODE" == "true" ]]; then
  # Extract --kanban-root, --phase2-protocol, --backup from positional scan.
  _p2_prev=""
  for _p2_arg in "$@"; do
    case "$_p2_prev" in
      --kanban-root)    _PHASE2_KANBAN_ROOT="$_p2_arg" ;;
      --phase2-protocol) _PHASE2_PROTOCOL="$_p2_arg" ;;
      --backup)          _PHASE2_BACKUP="$_p2_arg" ;;
    esac
    _p2_prev="$_p2_arg"
  done
  unset _p2_arg _p2_prev

  # In phase 2 the kanban root comes from the --kanban-root argument (the
  # installed root), NOT from the executing dev-tree path or environment.
  if [[ -z "$_PHASE2_KANBAN_ROOT" ]]; then
    echo "[upgrade] ERROR: --phase2 requires --kanban-root <path>" >&2
    exit 1
  fi

  # Override KANBAN_ROOT immediately — all root-relative sourcing below uses it.
  KANBAN_ROOT="$_PHASE2_KANBAN_ROOT"
fi

# ---------------------------------------------------------------------------
# Resolve install location and source config helpers (BEFORE strict mode
# so that sourcing failures do not kill the script prematurely).
# ---------------------------------------------------------------------------
if [[ "$_PHASE2_MODE" != "true" ]]; then
  KANBAN_ROOT="${PGAI_AGENT_KANBAN_ROOT_PATH}"
fi

# Source optional config files.
[[ -f "$KANBAN_ROOT/bashrc"     ]] && source "$KANBAN_ROOT/bashrc"
[[ -f "$KANBAN_ROOT/env"        ]] && source "$KANBAN_ROOT/env"
[[ -f "$HOME/.config/pgai-kanban.cfg" ]] && source "$HOME/.config/pgai-kanban.cfg"

# Source INI parser for kanban.cfg access.
_UPG_INI_SH="${KANBAN_ROOT}/scripts/lib/ini_parser.sh"
[[ -f "$_UPG_INI_SH" ]] && source "$_UPG_INI_SH"
if [[ -f "$KANBAN_ROOT/kanban.cfg" ]]; then
    export PGAI_DEV_TREE_PATH="${PGAI_DEV_TREE_PATH:-$(read_ini "$KANBAN_ROOT/kanban.cfg" paths dev_tree_path "")}"
fi
unset _UPG_INI_SH

# Source library helpers (project_paths, temp).
[[ -f "$KANBAN_ROOT/scripts/lib/project_paths.sh" ]] && source "$KANBAN_ROOT/scripts/lib/project_paths.sh"
[[ -f "$KANBAN_ROOT/scripts/lib/temp.sh"          ]] && source "$KANBAN_ROOT/scripts/lib/temp.sh"

# ---------------------------------------------------------------------------
# Source the shared argument parser.
# Phase 1 (installed script): BASH_SOURCE[0] is the installed upgrade.sh.
# Phase 2 (dev-tree script):  BASH_SOURCE[0] is the dev-tree upgrade.sh.
# In both cases BASH_SOURCE resolves to the correct directory — no re-exec
# indirection required since the two-phase architecture eliminated the
# self-overwrite problem that necessitated the temp-copy guard.
# ---------------------------------------------------------------------------
_SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
_UPG_ARGPARSE_SH="${_SCRIPT_DIR}/lib/argparse.sh"
if [[ ! -f "$_UPG_ARGPARSE_SH" ]]; then
  echo "[upgrade] ERROR: argparse.sh not found: $_UPG_ARGPARSE_SH" >&2
  exit 1
fi
# shellcheck source=team/scripts/lib/argparse.sh
source "$_UPG_ARGPARSE_SH"
unset _UPG_ARGPARSE_SH
# shellcheck source=lib/script_common.sh
source "${_SCRIPT_DIR}/lib/script_common.sh"

# ---------------------------------------------------------------------------
# Repo root: install.sh lives one level above team/scripts/ (at the repo root).
# ---------------------------------------------------------------------------
REPO_ROOT="$(cd "$_SCRIPT_DIR/../.." && pwd)"
unset _SCRIPT_DIR

# --- Color helpers ---
if [[ -t 1 ]]; then
  C_RED=$'\033[0;31m'
  C_GREEN=$'\033[0;32m'
  C_YELLOW=$'\033[0;33m'
  C_BLUE=$'\033[0;34m'
  C_BOLD=$'\033[1m'
  C_RESET=$'\033[0m'
else
  C_RED=""; C_GREEN=""; C_YELLOW=""; C_BLUE=""; C_BOLD=""; C_RESET=""
fi

info()  { echo "${C_BLUE}[upgrade]${C_RESET} $*"; }
ok()    { echo "${C_GREEN}[upgrade]${C_RESET} $*"; }
warn()  { echo "${C_YELLOW}[upgrade]${C_RESET} $*"; }
err()   { echo "${C_RED}[upgrade]${C_RESET} $*" >&2; }

# ---------------------------------------------------------------------------
# _upg_resolve_branch_prefix
#
# Echoes the registered project's [project] branch_prefix from projects.cfg
# / project.cfg (via pp_branch_prefix), or "" when no project is registered,
# the helper is unavailable, or lookup fails for any reason. Single
# definition shared by the version-targeted-upgrade tag resolution block and
# the VERSION_DETAIL git-describe advisory further down, so the two call
# sites cannot drift on how the prefix is derived.
# ---------------------------------------------------------------------------
_upg_resolve_branch_prefix() {
  local prefix=""
  if declare -f pp_branch_prefix >/dev/null 2>&1; then
    local projects_cfg="${KANBAN_ROOT}/projects.cfg"
    if [[ -f "$projects_cfg" ]]; then
      local project_name
      project_name="$(awk \
        '/^\[project:[a-zA-Z0-9_-]+\]/{match($0,/\[project:([a-zA-Z0-9_-]+)\]/,a);print a[1];exit}' \
        "$projects_cfg" 2>/dev/null || true)"
      if [[ -n "$project_name" ]]; then
        prefix="$(KANBAN_ROOT="$KANBAN_ROOT" pp_branch_prefix "$project_name" 2>/dev/null || true)"
      fi
    fi
  fi
  printf '%s' "$prefix"
}

# ---------------------------------------------------------------------------
# PROTECTED SET — single explicit manifest
#
# Items listed here are NEVER overwritten during an upgrade.  Any addition
# to this list must happen here and only here (not scattered inline).
#
# Semantics:
#   PROTECTED_FILES — individual file paths relative to KANBAN_ROOT that must
#                     not be overwritten.  Checked by deposit_protected_guard().
#   PROTECTED_DIRS  — directory paths relative to KANBAN_ROOT whose entire
#                     tree is preserved.  Checked by deposit_protected_guard().
#
# The projects/ directory is handled separately via _is_protected_path().
# Per-project project.cfg entries are derived dynamically from projects.cfg.
# ---------------------------------------------------------------------------
PROTECTED_FILES=(
  "kanban.cfg"
  "projects.cfg"
  "secrets"
  "shell-env"
  "claude-env"
  "pseudocron.env"
)

PROTECTED_DIRS=(
  "projects"
)

# Returns 0 (true) when a kanban-root-relative path is protected.
# Called for every deposit target; skips overwrite when true.
_is_protected_path() {
  local rel_path="$1"

  # Static file list.
  local pf
  for pf in "${PROTECTED_FILES[@]}"; do
    [[ "$rel_path" == "$pf" ]] && return 0
  done

  # Static directory list (match the dir itself or anything under it).
  local pd
  for pd in "${PROTECTED_DIRS[@]}"; do
    [[ "$rel_path" == "$pd" || "$rel_path" == "$pd/"* ]] && return 0
  done

  # Dynamic: per-project project.cfg files
  # Pattern: projects/<any-name>/project.cfg
  if [[ "$rel_path" =~ ^projects/[^/]+/project\.cfg$ ]]; then
    return 0
  fi

  return 1
}

# ---------------------------------------------------------------------------
# Argument declarations (set before argparse so cleanup can read them).
# ---------------------------------------------------------------------------
FORCE_UPGRADE=false
DO_BACKUP=true
WAKE_TIER=""          # empty = leave the crontab untouched (tier detected, reported only)
ADD_CLAUDE_AGENTS=false
ADD_CODEX_AGENTS=false
BACKUP_PATH=""
STAMP_VERSION=""      # non-empty = write verbatim, skip git resolution
REQUESTED_VERSION=""  # non-empty = optional positional VERSION argument (e.g. "v2.2.0"):
                       # upgrade TO that tag's tree, forward-only enforced. See
                       # "Version-targeted upgrade (forward-only)" below.

# ---------------------------------------------------------------------------
# Cleanup handler — runs on EXIT (success or failure).
# Reports backup path on failure so the operator knows recovery is available.
# ---------------------------------------------------------------------------
_cleanup() {
  local exit_code=$?
  # Version-targeted upgrade: prune the clean-source detached worktree (if
  # one was materialized) on EVERY exit path — success, failure, or an
  # early forward-only refusal. version_resolve_prune is idempotent and a
  # no-op when no worktree was ever created (the no-argument path).
  if [[ -n "${_UPG_CLEAN_SRC_WORKTREE:-}" ]] && declare -f version_resolve_prune >/dev/null 2>&1; then
    version_resolve_prune "${_UPG_CLEAN_SRC_REPO:-}" "$_UPG_CLEAN_SRC_WORKTREE" >/dev/null 2>&1 || true
  fi
  if [[ $exit_code -ne 0 ]]; then
    err "Upgrade failed (exit code $exit_code)."
    if [[ -n "$BACKUP_PATH" && -f "$BACKUP_PATH" ]]; then
      err "Your pre-upgrade backup is at: $BACKUP_PATH"
      err "Restore with: tar -xzf \"$BACKUP_PATH\" -C \"\$(dirname \"$KANBAN_ROOT\")\""
    fi
  fi
  exit $exit_code
}
trap '_cleanup' EXIT

# ---------------------------------------------------------------------------
# Usage
# ---------------------------------------------------------------------------
# Usage text as data (per lib/script_common.sh's print_usage_and_exit contract).
_USAGE_TEXT="$(cat <<EOF
Usage: $(basename "$0") [VERSION] [OPTIONS]

Upgrade an existing pgai-agent-kanban installation.

This is the UPGRADE path only (phase-1 bootstrap).  It validates the dev tree,
creates a pre-upgrade backup, and hands execution to the dev tree's upgrade.sh
as phase 2 (the actual upgrade).  If the target is not an existing installation,
use install.sh instead.

Phase 2 also applies every migrations/migrate_vX.Y.Z_to_vA.B.C.sh script whose
destination version falls in (installed, target], via the source tree's
team/scripts/migrations/run-migrations.sh -- see scripts/migrations/README.md.
Most upgrades select zero scripts (most version bumps need no migration); this
is the normal, non-error case.

When a changed item is left undeposited (no TTY and no --force, or an N or q
answer at a prompt), the upgrade stops before the crontab/pseudocron hooks,
the VERSION stamp and the migrations, names the skipped items, and exits
non-zero; re-run with --force to finish. VERSION keeps the old version, so
the re-run applies every migration from it.

Phase 2 also refreshes every registered project's seeded intake templates
(bugs/templates/BUG-TEMPLATE.md, priority/templates/PRIORITY-TEMPLATE.md,
requirements/templates/REQUIREMENTS-TEMPLATE.md) from the installed
templates/project/<workflow_type>/ set. A copy that differs is kept as
<file>.<UTC timestamp>.bak beside itself, then overwritten, with one line
naming the project and file; the rest of projects/ stays protected.

Version-targeted upgrade (forward-only):
  Pass an optional positional VERSION argument to upgrade TO a specific
  released tag's tree, instead of whatever the dev tree currently has
  checked out:

    $(basename "$0") v2.2.0          # upgrade to tag v2.2.0's tree
    $(basename "$0") ai_v2.2.0       # same tag; the internal "ai_" prefix form
                                     # is tolerated and normalized

  Resolution: the tag is resolved via the same shared team/scripts/lib/
  version_resolve.sh mechanism install.sh's version-targeted install uses --
  if the tag does not exist, upgrade.sh fails loud, names the requested
  version, and lists the nearest available tags.  On success, the resolved
  tag's tree is materialized into a temporary detached worktree and the
  ENTIRE upgrade (kanban tree, subagent manifest, templates, the committed
  VERSION file) is laid down from that clean tree; the dev tree's own
  checked-out branch is never touched, and the temporary worktree is removed
  before upgrade.sh exits (success, failure, or forward-only refusal).

  FORWARD-ONLY RULE: the resolved target must be STRICTLY NEWER (semver-
  aware comparison against the live install's committed VERSION file) than
  what is currently installed, or the upgrade is refused before anything is
  deposited:
    - Target == installed version: refused. Use \`install.sh --force\` to
      force a clean re-lay of the currently installed version.
    - Target <  installed version:  refused. The only sanctioned way to move
      an installation backwards is a fresh install (\`install.sh <VERSION>\`),
      which lays down everything from scratch and may go backwards.
  There is no --force override for direction -- --force only controls
  per-file overwrite prompts during deposit, never which way time moves.

  Omit VERSION entirely to upgrade using whatever the dev tree currently has
  checked out (the default, back-compatible path).

OPTIONS:
  --force                 Overwrite changed managed files without prompting.
                          Without it, a run that leaves any changed item
                          undeposited stops before the VERSION stamp and
                          migrations and exits non-zero.
  --no-backup             Skip the pre-upgrade backup (advanced; not recommended).
  --wake-tier small|medium|large|none
                          Rewrite the crontab's kanban-managed lines for this tier;
                          every other crontab line is kept (none = leave it alone).
                          When OMITTED, the crontab is not touched: the installed
                          tier is detected and reported only. pseudocron.cfg is
                          never overwritten by an upgrade either way.
  --add-claude-agents     Deploy provider-agent wrappers to \$CLAUDE_CONFIG_DIR/agents/
                          (default ~/.claude/agents/).
  --add-codex-agents      Deploy provider-agent wrappers to ~/.codex/agents/.
                          Without either flag the deployed wrappers are
                          protected: nothing under a provider's agents
                          directory is written or created, but each existing
                          directory is compared with the wrappers this upgrade
                          ships. A missing or differing wrapper is warned by
                          provider and file name with the refresh command
                          (upgrade.sh --add-<provider>-agents); when all match,
                          the summary reports the wrappers as current.
  --dev-tree PATH         Explicit path to dev tree (overrides env and kanban.cfg).
  --stamp-version STRING  Write STRING verbatim to VERSION; bypass git resolution
                          and suppress divergence advisory.
  --help, -h              Show this help.

DEPRECATED OPTIONS (still accepted; emit a deprecation notice to stderr):
  --crontab-tier small|medium|large|none   Use --wake-tier instead.

INTERNAL (phase-2 re-exec only; do not pass by hand):
  --phase2                Run as phase 2, the upgrade body; phase 1 passes it
                          when it execs the dev tree's upgrade.sh.
  --phase2-protocol N     Version of the phase-1/phase-2 handoff (phase 1
                          passes 1); required with --phase2.
  --kanban-root PATH      Kanban root phase 2 upgrades; required with --phase2.
  --backup PATH           Pre-upgrade backup tarball phase 1 created (passed
                          only when a backup was made).

ENVIRONMENT:
  PGAI_AGENT_KANBAN_ROOT_PATH   Kanban root (default: \$HOME/pgai_agent_kanban)
  PGAI_DEV_TREE_PATH            Dev tree path (set via kanban.cfg or this env var)
  CLAUDE_CONFIG_DIR             Claude Code config dir (default: \$HOME/.claude);
                                --add-claude-agents deploys to its agents/ subdirectory

EXAMPLES:
  $(basename "$0")                                      # upgrade, crontab untouched
  $(basename "$0") --force                              # upgrade silently
  $(basename "$0") --wake-tier large                   # upgrade; kanban cron lines -> large tier
  $(basename "$0") --wake-tier none                    # upgrade, skip crontab
  $(basename "$0") --add-claude-agents                 # upgrade and refresh claude wrappers
  $(basename "$0") --no-backup                         # upgrade without backup (advanced)
  $(basename "$0") v2.2.0                              # forward-only upgrade to tag v2.2.0
EOF
)"

# ---------------------------------------------------------------------------
# Parse arguments
# phase2 value flags (phase2-protocol, kanban-root, backup) are registered
# here so the parser consumes them cleanly; they are validated in the phase-2
# dispatch block below.
# ---------------------------------------------------------------------------
argparse_parse --value-flags "wake-tier crontab-tier dev-tree stamp-version phase2-protocol kanban-root backup" -- "$@"

# Validate value-taking flags were given a value.
if argparse_missing "wake-tier"; then
  err "--wake-tier requires a value (small, medium, large, or none)."
  exit 1
fi
if argparse_missing "crontab-tier"; then
  err "--crontab-tier requires a value (small, medium, large, or none)."
  exit 1
fi
if argparse_missing "dev-tree"; then
  err "--dev-tree requires a path argument."
  exit 1
fi
if argparse_missing "stamp-version"; then
  err "--stamp-version requires a non-empty string argument."
  exit 1
fi
if argparse_missing "phase2-protocol"; then
  err "--phase2-protocol requires a protocol number."
  exit 1
fi
if argparse_missing "kanban-root"; then
  err "--kanban-root requires a path argument."
  exit 1
fi
if argparse_missing "backup"; then
  err "--backup requires a path argument."
  exit 1
fi

# Handle --help / -h.
if argparse_has "help" || argparse_has "h"; then
  print_usage_and_exit 0 "$_USAGE_TEXT"
fi

# Handle short flags and the optional positional VERSION argument from
# ARGPARSE_POSITIONAL. At most one non-flag positional is accepted (the
# VERSION argument, forward-only enforced); a second one is an error. This
# same code path runs for BOTH phase 1 and phase 2 invocations (identical
# argv is forwarded across the --phase2 re-exec via "$@"), so REQUESTED_VERSION
# ends up populated consistently in the phase-2 process without any extra
# thread-through flag.
for _pos in "${ARGPARSE_POSITIONAL[@]+"${ARGPARSE_POSITIONAL[@]}"}"; do
  case "$_pos" in
    -h) print_usage_and_exit 0 "$_USAGE_TEXT" ;;
    -*)
      err "Unknown option: $_pos"
      err "Run '$(basename "$0") --help' for usage."
      exit 1
      ;;
    *)
      if [[ -n "$REQUESTED_VERSION" ]]; then
        err "Unknown argument: $_pos"
        err "(a version argument was already given: '${REQUESTED_VERSION}'; only one is accepted)"
        err "Run '$(basename "$0") --help' for usage."
        exit 1
      fi
      REQUESTED_VERSION="$_pos"
      ;;
  esac
done
unset _pos

# Reject unknown flags.
for _flag in "${!ARGPARSE_FLAGS[@]}"; do
  case "$_flag" in
    force|no-backup|add-claude-agents|add-codex-agents|dev-tree|stamp-version|wake-tier|crontab-tier|help|h) ;;
    phase2|phase2-protocol|kanban-root|backup) ;;
    *)
      err "Unknown option: --${_flag}"
      err "Run '$(basename "$0") --help' for usage."
      exit 1
      ;;
  esac
done
unset _flag

# Extract boolean flags.
if argparse_has "force";             then FORCE_UPGRADE=true; fi
if argparse_has "no-backup";         then DO_BACKUP=false; fi
if argparse_has "add-claude-agents"; then ADD_CLAUDE_AGENTS=true; fi
if argparse_has "add-codex-agents";  then ADD_CODEX_AGENTS=true; fi

# Extract --dev-tree.
if argparse_has "dev-tree"; then
  PGAI_DEV_TREE_PATH="${ARGPARSE_FLAGS[dev-tree]}"
fi

# Extract --stamp-version.
if argparse_has "stamp-version"; then
  STAMP_VERSION="${ARGPARSE_FLAGS[stamp-version]}"
  if [[ -z "$STAMP_VERSION" ]]; then
    err "--stamp-version requires a non-empty string."
    exit 1
  fi
fi

# Extract canonical --wake-tier value.
if argparse_has "wake-tier"; then
  WAKE_TIER="${ARGPARSE_FLAGS[wake-tier]}"
  case "$WAKE_TIER" in
    small|medium|large|none) ;;
    *)
      err "Invalid wake tier '${WAKE_TIER}'. Choose: small, medium, large, or none."
      exit 1
      ;;
  esac
fi

# Handle deprecated --crontab-tier.
if argparse_has "crontab-tier"; then
  _dep_ct="${ARGPARSE_FLAGS[crontab-tier]}"
  case "$_dep_ct" in
    small|medium|large|none) ;;
    *)
      err "Invalid tier '${_dep_ct}' for deprecated --crontab-tier. Choose: small, medium, large, or none."
      exit 1
      ;;
  esac
  warn "DEPRECATED: --crontab-tier; use --wake-tier ${_dep_ct} instead."
  # Canonical --wake-tier wins if both were supplied.
  if [[ -z "$WAKE_TIER" ]]; then
    WAKE_TIER="$_dep_ct"
  fi
  unset _dep_ct
fi

# ---------------------------------------------------------------------------
# PHASE 2 DISPATCH
#
# When invoked with --phase2 (exec'd by the phase-1 bootstrap), skip the
# precondition check, backup creation, dev-tree validation, and the phase-1
# exec handoff — those steps already ran in phase 1.  Validate the protocol
# number, resolve KANBAN_ROOT and BACKUP_PATH from the phase-2 args, then
# fall through to the upgrade body (tier detection, deposit, scheduler hooks,
# VERSION stamp, divergence advisory).
#
# Lib sourcing is BASH_SOURCE-relative: in phase 2 BASH_SOURCE[0] is the
# dev-tree upgrade.sh and _SCRIPT_DIR resolves to its directory correctly.
#
# The manual-bootstrap instruction is the one-liner the operator can paste
# to recover from a protocol-mismatch: copy the new upgrade.sh into the
# installed scripts/ and retry.
# ---------------------------------------------------------------------------
if [[ "$_PHASE2_MODE" == "true" ]]; then
  # Validate the protocol number — only 1 is accepted in this version.
  _p2_proto="${_PHASE2_PROTOCOL:-}"
  if [[ -z "$_p2_proto" ]]; then
    err "Phase-2 error: --phase2-protocol is required."
    err "Manual bootstrap: cp \$(dirname \"\${BASH_SOURCE[0]}\")/upgrade.sh \"\$KANBAN_ROOT/scripts/\" then retry"
    exit 1
  fi
  if [[ "$_p2_proto" != "1" ]]; then
    err "Phase-2 error: unknown protocol version '$_p2_proto' (only protocol 1 is supported by this script)."
    err "Manual bootstrap: cp \$(dirname \"\${BASH_SOURCE[0]}\")/upgrade.sh \"\$KANBAN_ROOT/scripts/\" then retry"
    exit 1
  fi
  unset _p2_proto

  # KANBAN_ROOT is already set from the early detection block; confirm it is
  # accessible.  The phase-1 bootstrap already created the pre-upgrade backup,
  # so we only record its path here for the _cleanup error handler.
  if [[ -n "$_PHASE2_BACKUP" ]]; then
    BACKUP_PATH="$_PHASE2_BACKUP"
  fi

  # Infer DEV_TREE from BASH_SOURCE (this script lives at <dev_tree>/team/scripts/).
  DEV_TREE="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"

  info "Phase 2 running (protocol 1): kanban root = $KANBAN_ROOT"
  info "Dev tree (BASH_SOURCE-relative): $DEV_TREE"

  # -------------------------------------------------------------------------
  # VERSION-TARGETED UPGRADE (forward-only)
  #
  # When REQUESTED_VERSION is non-empty (operator ran e.g. "upgrade.sh
  # v2.2.0"), resolve it against DEV_TREE's own git tags via the shared
  # team/scripts/lib/version_resolve.sh mechanism (the same module
  # install.sh's version-targeted install uses, so the two scripts cannot
  # drift on tag normalization/resolution/materialization), enforce the
  # forward-only rule against the live install's committed VERSION file,
  # and — on success — redirect DEV_TREE to the resolved tag's clean
  # detached worktree so every "$DEV_TREE" reference for the rest of this
  # script (the deposit loop, subagent manifest, VERSION stamp, retired-file
  # manifest, etc.) reads from that tag's tree instead of whatever DEV_TREE
  # currently has checked out — mirroring install.sh's SCRIPT_DIR redirect.
  #
  # Forward-only rule: the resolved target must be STRICTLY newer (semver-
  # aware, via version_resolve_forward_only_check) than the installed
  # version, or the upgrade is refused before anything is deposited. There
  # is no --force override for direction; --force only controls per-file
  # overwrite prompts during deposit.
  #
  # No-argument back-compat: REQUESTED_VERSION empty means this entire
  # block is a no-op — DEV_TREE keeps pointing at the checked-out dev tree
  # exactly as it always has, and upgrade.sh behaves byte-identically to
  # before this argument existed.
  # -------------------------------------------------------------------------
  _UPG_TARGET_VERSION=""
  _UPG_RESOLVED_TAG=""
  _UPG_INSTALLED_VERSION=""
  if [[ -n "$REQUESTED_VERSION" ]]; then
    if [[ ! -e "$DEV_TREE/.git" ]]; then
      err "ERROR: a version argument was given ('${REQUESTED_VERSION}') but '${DEV_TREE}' is not a git checkout (no .git found)."
      err "Version-targeted upgrade requires a git checkout of the pgai-agent-kanban repo so the requested tag can be resolved."
      exit 1
    fi

    _UPG_VERSION_RESOLVE_LIB="$DEV_TREE/team/scripts/lib/version_resolve.sh"
    if [[ ! -f "$_UPG_VERSION_RESOLVE_LIB" ]]; then
      err "ERROR: version_resolve.sh not found: $_UPG_VERSION_RESOLVE_LIB"
      err "This file is required for version-targeted upgrades and ships with the kanban repo."
      exit 1
    fi
    # shellcheck source=team/scripts/lib/version_resolve.sh
    source "$_UPG_VERSION_RESOLVE_LIB"
    unset _UPG_VERSION_RESOLVE_LIB

    _UPG_BRANCH_PREFIX="$(_upg_resolve_branch_prefix)"

    if ! _UPG_RESOLVED_TAG="$(version_resolve_resolve_tag "$REQUESTED_VERSION" "$DEV_TREE" "$_UPG_BRANCH_PREFIX")"; then
      exit 1
    fi

    info "Version-targeted upgrade: '${REQUESTED_VERSION}' resolved to tag '${_UPG_RESOLVED_TAG}'"

    _UPG_ORIGINAL_DEV_TREE="$DEV_TREE"
    _UPG_CLEAN_SRC_WORKTREE="$(version_resolve_materialize "$DEV_TREE" "$_UPG_RESOLVED_TAG")"
    if [[ -z "$_UPG_CLEAN_SRC_WORKTREE" ]]; then
      err "ERROR: failed to materialize tag '${_UPG_RESOLVED_TAG}' into a clean worktree."
      exit 1
    fi
    # Read by _cleanup() (registered on the EXIT trap at the top of this
    # script) so the worktree is pruned on every exit path.
    _UPG_CLEAN_SRC_REPO="$_UPG_ORIGINAL_DEV_TREE"

    # Target version identity: the resolved tag's OWN committed VERSION file
    # (the same "committed file IS the identity" rule version_stamp.sh
    # applies at deposit time). Fall back to the normalized requested string
    # only if that tag's tree predates the committed-VERSION convention.
    if [[ -f "$_UPG_CLEAN_SRC_WORKTREE/VERSION" ]]; then
      _UPG_TARGET_VERSION="$(tr -d '[:space:]' < "$_UPG_CLEAN_SRC_WORKTREE/VERSION")"
    fi
    if [[ -z "$_UPG_TARGET_VERSION" ]]; then
      _UPG_TARGET_VERSION="$(version_resolve_normalize "$REQUESTED_VERSION" "$_UPG_BRANCH_PREFIX")"
    fi

    # Installed version identity: the LIVE install's own committed VERSION
    # file. Missing/empty (very old installs predating VERSION tracking)
    # degrades to v0.0.0 so the comparison below still has a baseline — any
    # real target will compare newer than v0.0.0.
    if [[ -f "$KANBAN_ROOT/VERSION" ]]; then
      _UPG_INSTALLED_VERSION="$(tr -d '[:space:]' < "$KANBAN_ROOT/VERSION")"
    fi
    if [[ -z "$_UPG_INSTALLED_VERSION" ]]; then
      warn "No installed VERSION file found at $KANBAN_ROOT/VERSION; treating installed version as v0.0.0 for the forward-only comparison."
      _UPG_INSTALLED_VERSION="v0.0.0"
    fi

    # ---- Forward-only enforcement (no --force override for direction) ----
    if semver_eq "$_UPG_TARGET_VERSION" "$_UPG_INSTALLED_VERSION"; then
      err "ERROR: refusing to re-lay: requested version '${_UPG_TARGET_VERSION}' is ALREADY the installed version '${_UPG_INSTALLED_VERSION}'."
      err ""
      err "upgrade.sh's forward-only rule refuses same-version re-lays as well as downgrades."
      err "To force a clean re-lay of the currently installed version, use install.sh's repair path instead:"
      err ""
      err "  ./install.sh --force"
      err ""
      err "Nothing has been deposited; your existing installation at ${_UPG_INSTALLED_VERSION} is unchanged."
      exit 1
    elif ! version_resolve_forward_only_check "$_UPG_INSTALLED_VERSION" "$_UPG_TARGET_VERSION"; then
      err "ERROR: refusing to upgrade: requested version '${_UPG_TARGET_VERSION}' is OLDER than the installed version '${_UPG_INSTALLED_VERSION}'."
      err ""
      err "upgrade.sh enforces a forward-only rule: it never moves an existing installation backwards in version."
      err "There is no --force override for direction (--force only controls per-file overwrite prompts)."
      err "The only sanctioned way to move an installation to an older version is a fresh install, which lays"
      err "down everything from scratch and may go backwards:"
      err ""
      err "  ./install.sh ${REQUESTED_VERSION} --force"
      err ""
      err "Nothing has been deposited; your existing installation at ${_UPG_INSTALLED_VERSION} is unchanged."
      exit 1
    fi

    info "Forward-only check: ${_UPG_INSTALLED_VERSION} -> ${_UPG_TARGET_VERSION}: OK (target is newer)"
    info "Laying down from clean checkout: $_UPG_CLEAN_SRC_WORKTREE"
    info "Dev tree's checked-out branch in $_UPG_ORIGINAL_DEV_TREE is untouched (detached worktree)."

    # Redirect DEV_TREE: every "$DEV_TREE" reference for the rest of this
    # script now reads from the resolved tag's tree instead of the
    # checked-out dev tree — see block comment above.
    DEV_TREE="$_UPG_CLEAN_SRC_WORKTREE"
  fi

  # Fall through to the upgrade body below — tier detection, deposit,
  # scheduler hooks, VERSION stamp, divergence advisory all proceed normally.
fi

# ---------------------------------------------------------------------------
# PHASE 1 ONLY: precondition check, backup, dev-tree validation, exec handoff.
#
# These steps run when upgrade.sh is invoked normally (no --phase2).  They
# are the full phase-1 bootstrap: validate the install, back it up, probe
# the dev-tree script, and exec into it as phase 2.  Phase 2 (the actual
# upgrade body) runs the rest of this file; these steps are skipped there.
# ---------------------------------------------------------------------------
if [[ "$_PHASE2_MODE" != "true" ]]; then

# ---------------------------------------------------------------------------
# PRECONDITION: fail loud if target is NOT an existing kanban install.
#
# upgrade.sh is the UPGRADE path only.  When the target directory does not
# exist, or does not contain any of the canonical install artifacts, the
# operator must run install.sh first.
# ---------------------------------------------------------------------------
info "Pre-flight: checking install target..."

_looks_like_install=false
if [[ -d "$KANBAN_ROOT" ]]; then
  for _artifact in kanban.cfg VERSION scripts; do
    if [[ -e "${KANBAN_ROOT}/${_artifact}" ]]; then
      _looks_like_install=true
      break
    fi
  done
fi
unset _artifact

if [[ "$_looks_like_install" != "true" ]]; then
  err "ERROR: ${KANBAN_ROOT} does not look like an existing pgai-agent-kanban installation."
  err ""
  err "upgrade.sh is the upgrade path and requires an existing installation."
  err "To install for the first time, use install.sh instead:"
  err ""
  err "  ./install.sh"
  err ""
  err "If your kanban root is in a non-default location, set:"
  err "  export PGAI_AGENT_KANBAN_ROOT_PATH=/path/to/your/kanban"
  exit 1
fi
unset _looks_like_install

ok "Install target found: $KANBAN_ROOT"

# ---------------------------------------------------------------------------
# PRE-UPGRADE BACKUP
#
# Creates a chmod 600 tarball of the entire kanban root, excluding only
# volatile directories and files that change every minute.  The backup
# INCLUDES the secrets file so that a full restore is possible.
#
# Exclusion list (volatiles only):
#   logs/        — cron-job logs written every agent firing
#   locks/       — per-agent flock files (transient)
#   __pycache__  — Python bytecode cache (regenerated automatically)
#   *.lock       — lock files
# ---------------------------------------------------------------------------
if [[ "$DO_BACKUP" == "true" ]]; then
  TIMESTAMP=$(date +%Y%m%d-%H%M%S)
  BACKUP_PATH="$HOME/pgai-kanban-backup-${TIMESTAMP}.tar.gz"
  _KANBAN_BASENAME="$(basename "$KANBAN_ROOT")"

  info "Creating pre-upgrade backup: $BACKUP_PATH"
  info "(Backup includes secrets; tarball is chmod 600.)"

  # Touch the file and chmod 600 BEFORE writing data so the secrets file
  # is never world-readable even for a moment.
  touch "$BACKUP_PATH"
  chmod 600 "$BACKUP_PATH"

  # rc=1 from tar means "file changed as we read it" (a cron agent wrote
  # a log while tar ran) — non-fatal.  rc>=2 is a real I/O failure — abort.
  tar_rc=0
  tar -czf "$BACKUP_PATH" \
      --exclude="${_KANBAN_BASENAME}/logs" \
      --exclude="${_KANBAN_BASENAME}/locks" \
      --exclude="${_KANBAN_BASENAME}/__pycache__" \
      --exclude="${_KANBAN_BASENAME}/*.lock" \
      -C "$(dirname "$KANBAN_ROOT")" "$_KANBAN_BASENAME" \
      || tar_rc=$?
  unset _KANBAN_BASENAME

  if [[ $tar_rc -eq 1 ]]; then
    warn "Backup completed with warnings (rc=1: some files changed during archiving — non-fatal)."
  elif [[ $tar_rc -ge 2 ]]; then
    err "Backup failed: tar exited with rc=${tar_rc}.  See stderr above for details."
    exit $tar_rc
  fi
  unset tar_rc

  ok "Backup created (chmod 600): $BACKUP_PATH"
else
  warn "Skipping backup (--no-backup specified).  No recovery tarball will be available."
fi

# ---------------------------------------------------------------------------
# Verify dev tree path (needed to locate install.sh and subagents/).
# ---------------------------------------------------------------------------
DEV_TREE="${PGAI_DEV_TREE_PATH:-}"
if [[ -z "$DEV_TREE" ]]; then
  # Fallback: upgrade.sh may itself live in the dev tree.
  # team/scripts/upgrade.sh → repo root is two levels up.
  # BASH_SOURCE[0] is always the installed upgrade.sh in phase 1.
  _candidate="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." 2>/dev/null && pwd)" || true
  if [[ -f "${_candidate}/install.sh" ]]; then
    DEV_TREE="$_candidate"
    info "Dev tree inferred from script location: $DEV_TREE"
  fi
  unset _candidate
fi

if [[ -z "$DEV_TREE" ]]; then
  err "dev_tree_path not configured."
  err "Set PGAI_DEV_TREE_PATH or [paths] dev_tree_path in $KANBAN_ROOT/kanban.cfg,"
  err "or use --dev-tree PATH."
  exit 1
fi

if [[ ! -d "$DEV_TREE" ]]; then
  err "Dev tree not found at: $DEV_TREE"
  exit 1
fi

# install.sh must exist in the dev tree.
INSTALL_SH="$DEV_TREE/install.sh"
if [[ ! -f "$INSTALL_SH" ]]; then
  err "install.sh not found at: $INSTALL_SH"
  err "Ensure the dev tree path is correct (--dev-tree PATH or kanban.cfg [paths] dev_tree_path)."
  exit 1
fi

ok "Dev tree: $DEV_TREE"

# ---------------------------------------------------------------------------
# PHASE 1 BOOTSTRAP: probe and exec handoff to the dev-tree upgrade.sh.
#
# This is the phase-1 (bootstrap) code path.  Phase 1 performs NO deposit,
# NO config changes, and NO crontab work.  It validates the dev-tree script,
# then replaces itself (via exec) with the dev-tree script running as phase 2.
#
# Steps:
#   1. Locate <dev_tree>/team/scripts/upgrade.sh.
#   2. Run bash -n on it — a syntax error means the new script is broken;
#      fail loud before anything is deposited.
#   3. Probe for --phase2 support (grep -q) — an older or wrong script
#      without --phase2 would silently run as a standalone upgrade; fail loud.
#   4. exec the dev-tree script as phase 2, passing versioned protocol flags
#      and all original operator args.  exec replaces this process so phase 1
#      performs no deposit, no config change, no crontab work.
# ---------------------------------------------------------------------------
_DEV_UPGRADE_SH="${DEV_TREE}/team/scripts/upgrade.sh"

if [[ ! -f "$_DEV_UPGRADE_SH" ]]; then
  err "Dev-tree upgrade.sh not found: $_DEV_UPGRADE_SH"
  err "Manual bootstrap: cp ${_DEV_UPGRADE_SH} scripts/ then retry"
  exit 1
fi

info "Probing dev-tree upgrade.sh: bash -n check..."
if ! bash -n "$_DEV_UPGRADE_SH" 2>/dev/null; then
  # Run again capturing stderr for the error message.
  _bash_n_err="$(bash -n "$_DEV_UPGRADE_SH" 2>&1 || true)"
  err "Dev-tree upgrade.sh failed bash -n syntax check:"
  err "$_bash_n_err"
  err "Nothing has been deposited.  Your pre-upgrade backup is still intact."
  err "Manual bootstrap: cp ${_DEV_UPGRADE_SH} scripts/ then retry"
  unset _bash_n_err _DEV_UPGRADE_SH
  exit 1
fi
info "bash -n: OK"

info "Probing dev-tree upgrade.sh: --phase2 support check..."
if ! grep -q -- '--phase2' "$_DEV_UPGRADE_SH"; then
  err "Dev-tree upgrade.sh does not appear to support --phase2 (it may be an older script or a downgrade)."
  err "Nothing has been deposited.  Your pre-upgrade backup is still intact."
  err "Manual bootstrap: cp ${_DEV_UPGRADE_SH} scripts/ then retry"
  unset _DEV_UPGRADE_SH
  exit 1
fi
info "--phase2 support: OK"

# Build the --backup argument: only pass it when a backup was actually created.
_phase2_backup_args=()
if [[ -n "$BACKUP_PATH" ]]; then
  _phase2_backup_args=("--backup" "$BACKUP_PATH")
fi

info "Handing off to dev-tree upgrade.sh (phase 2, protocol 1)..."
exec bash "$_DEV_UPGRADE_SH" \
  --phase2 \
  --phase2-protocol 1 \
  --kanban-root "$KANBAN_ROOT" \
  "${_phase2_backup_args[@]+"${_phase2_backup_args[@]}"}" \
  "$@"

# End of phase-1-only block (exec above replaces this process; the fi below
# is only reached in phase 2 where the if-block was skipped entirely).
fi  # [[ "$_PHASE2_MODE" != "true" ]]

# ---------------------------------------------------------------------------
# PRE-DEPOSIT: the source tree must carry the shared version-stamp helper.
#
# The VERSION stamp that follows the deposit calls stamp_version_files and
# git_describe_tag_pattern, both defined only in the deposited tree's
# team/scripts/lib/version_stamp.sh. A tree without that file (a damaged or
# hand-assembled copy) is refused here, before the first file is laid down,
# so the root is never left holding the new tree's files beside the old
# VERSION with no migrations run. install.sh refuses the same tree the same
# way. The helper is sourced here, once, for the stamp step further down.
#
# The remedy depends on where DEV_TREE came from: a version-targeted upgrade
# reads a tag's tree, so the fix is to upgrade from the checked-out tree
# instead; a git checkout can restore the file from HEAD; a tree with no
# .git (an archive export or tarball) is replaced by a fresh clone.
# ---------------------------------------------------------------------------
_UPG_STAMP_LIB="$DEV_TREE/team/scripts/lib/version_stamp.sh"
if [[ ! -f "$_UPG_STAMP_LIB" ]]; then
  if [[ -n "${_UPG_RESOLVED_TAG:-}" ]]; then
    _upg_stamp_remedy="'${_UPG_ORIGINAL_DEV_TREE}/team/scripts/upgrade.sh' --dev-tree '${_UPG_ORIGINAL_DEV_TREE}'"
  elif [[ -e "$DEV_TREE/.git" ]]; then
    _upg_stamp_remedy="git -C '${DEV_TREE}' checkout HEAD -- team/scripts/lib/version_stamp.sh && '${DEV_TREE}/team/scripts/upgrade.sh' --dev-tree '${DEV_TREE}'"
  else
    _upg_stamp_remedy="git clone https://github.com/purdygood-ai/pgai-agent-kanban.git '${HOME}/pgai-agent-kanban-src' && '${HOME}/pgai-agent-kanban-src/team/scripts/upgrade.sh' --dev-tree '${HOME}/pgai-agent-kanban-src'"
  fi
  # shellcheck source=lib/error_contract.sh
  source "$(dirname "${BASH_SOURCE[0]}")/lib/error_contract.sh"
  # BLOCK-WORTHY (docs/blocking-site-census.md): depositing a tree that
  # cannot stamp its own VERSION leaves the root half-upgraded -- new files,
  # the old VERSION, no migrations -- a state-corrupting partial upgrade.
  pgai_emit_error_contract \
    "upgrade.stamp-helper-missing" \
    "The source tree ${DEV_TREE} has no version-stamp helper at ${_UPG_STAMP_LIB}; without it the upgrade cannot write VERSION after depositing. Nothing has been deposited and ${KANBAN_ROOT} is unchanged." \
    "${_upg_stamp_remedy}"
  # CENSUS-ANCHOR: upgrade-stamp-helper-missing-01
  exit 1
fi
# shellcheck source=team/scripts/lib/version_stamp.sh
source "$_UPG_STAMP_LIB"
unset _UPG_STAMP_LIB

# ---------------------------------------------------------------------------
# Detect the installed crontab tier (when --wake-tier was NOT supplied).
#
# The result is reported only: a plain upgrade never writes the crontab, so
# detection can never turn into an applied tier.
#
# Detection order:
#   1. crontab not available, no crontab installed, or no wake job line
#      (a non-comment line running wake.sh, wake-batch.sh or wake-now.sh)
#      → "none", whatever "# Tier:" header or cleanup lines remain.
#   2. "# Tier: <x>" header line in `crontab -l`.
#   3. Body-match against tier templates in $KANBAN_ROOT/templates/install/.
#   4. Wake job lines that match no tier → "custom" (no tier is invented).
#
# The crontab is read through PGAI_CRONTAB_CMD when set (the same seam
# lib/safe_overwrite.sh uses), else the crontab on PATH.
# ---------------------------------------------------------------------------
_detect_installed_tier() {
  local _crontab_cmd="${PGAI_CRONTAB_CMD:-crontab}"
  if ! command -v "$_crontab_cmd" >/dev/null 2>&1; then
    echo "none"; return 0
  fi

  local _raw
  _raw="$("$_crontab_cmd" -l 2>/dev/null)" || true
  if [[ -z "$_raw" ]]; then
    echo "none"; return 0
  fi

  # Strategy 1: no wake job line means no wake tier is installed. A header
  # left behind with only cleanup lines (or nothing) is not a tier.
  # Here-strings, not a pipe into grep -q: under pipefail an early grep -q
  # exit would fail the writer and read as "no match".
  local _job_lines
  _job_lines="$(grep -vE '^[[:space:]]*(#|[A-Za-z_][A-Za-z0-9_]*[[:space:]]*=)' <<< "$_raw")" || true
  if ! grep -qE 'wake(-batch|-now)?\.sh' <<< "$_job_lines"; then
    echo "none"; return 0
  fi

  # Strategy 2: canonical "# Tier: <x>" header.
  local _tier_header
  _tier_header="$(printf '%s\n' "$_raw" \
    | grep -iE '^[[:space:]]*#[[:space:]]*Tier:[[:space:]]*(small|medium|large)' \
    | head -1 \
    | sed -E 's/^[[:space:]]*#[[:space:]]*Tier:[[:space:]]*//' \
    | tr '[:upper:]' '[:lower:]' \
    | tr -d '[:space:]')" || true
  case "$_tier_header" in
    small|medium|large) echo "$_tier_header"; return 0 ;;
  esac

  # Strategy 3: match installed crontab body against tier templates.
  local _templates_dir="${KANBAN_ROOT}/templates/install"
  local _tier
  for _tier in large medium small; do
    local _tmpl="${_templates_dir}/crontab-${_tier}.example"
    [[ -f "$_tmpl" ]] || continue

    local _total=0 _all_ok=true
    while IFS= read -r _tline; do
      [[ -z "$_tline" || "$_tline" =~ ^[[:space:]]*# || "$_tline" =~ ^[[:space:]]*[A-Z_]+= ]] && continue
      [[ "$_tline" != *"wake-batch.sh"* ]] && continue

      local _cron_expr _agent_tok _sleep_tok
      _cron_expr="$(printf '%s\n' "$_tline" | awk '{print $1,$2,$3,$4,$5}')"
      _agent_tok="$(printf '%s\n' "$_tline" | grep -oE -- '--agent=[a-z]+' | head -1)" || true
      _sleep_tok="$(printf '%s\n' "$_tline" | grep -oE -- '--sleep=[0-9]+' | head -1)" || true
      [[ -z "$_agent_tok" ]] && continue
      _total=$(( _total + 1 ))

      local _found=false
      while IFS= read -r _iline; do
        [[ "$_iline" != *"wake-batch.sh"* ]] && continue
        local _i_expr
        _i_expr="$(printf '%s\n' "$_iline" | awk '{print $1,$2,$3,$4,$5}')"
        [[ "$_i_expr" != "$_cron_expr" ]] && continue
        grep -qF -- "$_agent_tok" <<< "$_iline" || continue
        if [[ -n "$_sleep_tok" ]]; then
          grep -qF -- "$_sleep_tok" <<< "$_iline" || continue
        fi
        _found=true; break
      done <<< "$_raw"

      if [[ "$_found" != "true" ]]; then _all_ok=false; break; fi
    done < "$_tmpl"

    if [[ "$_all_ok" == "true" && "$_total" -gt 0 ]]; then
      echo "$_tier"; return 0
    fi
  done

  # Strategy 4: wake job lines exist but match no shipped tier.
  echo "custom"; return 0
}

_UPG_DETECTED_TIER=""
if [[ -z "$WAKE_TIER" ]]; then
  info "Detecting installed crontab tier (--wake-tier not supplied)..."
  _UPG_DETECTED_TIER="$(_detect_installed_tier)"
  info "Detected installed tier: ${_UPG_DETECTED_TIER} (crontab left untouched; pass --wake-tier to change it)"
fi

# ---------------------------------------------------------------------------
# DEPOSIT — same tree as install.sh, protected items skipped.
#
# Helper: deposit_item <src-path> <kanban-root-relative-path> <label>
#   - Skips items that are in the protected set.
#   - Skips identical files silently, and a directory whose every source file
#     is already identical in place (extra files at the root do not count;
#     see _deposit_dir_is_current).
#   - With FORCE_UPGRADE=true: overwrites non-protected items without prompting.
#   - Without force and with a TTY: prompts [y]es/[N]o/[a]ll/[q]uit.
#   - Without force and without a TTY: skips with a message.
#   Every differing item left undeposited (no TTY, N, q) is recorded in
#   _DEPOSIT_SKIPPED; a non-empty list stops the run before the scheduler
#   hooks, the VERSION stamp and the migrations (see "PARTIAL DEPOSIT").
# ---------------------------------------------------------------------------
_DEPOSIT_BULK_CHOICE=""
_DEPOSIT_SKIPPED=()

# _deposit_dir_is_current <src-dir> <dst-dir>
#
# Returns 0 when depositing <src-dir> onto <dst-dir> would change nothing:
# every file under <src-dir> exists under <dst-dir> with identical content.
# Files only present under <dst-dir> are ignored (the deposit copies over and
# never deletes), as are Python bytecode caches, which differ between any two
# trees that ran Python. Returns 1 on any difference, or when diff is not
# available (the item is then treated as differing, as before).
_deposit_dir_is_current() {
  local src="$1" dst="$2"
  [[ -d "$src" && -d "$dst" ]] || return 1
  command -v diff >/dev/null 2>&1 || return 1
  local _report _rc=0
  _report="$(LC_ALL=C diff -rq -x __pycache__ -x '*.pyc' -x .pytest_cache "$src" "$dst" 2>&1)" || _rc=$?
  [[ "$_rc" -le 1 ]] || return 1
  # Only "Only in <dst...>" lines are acceptable: extra files at the root.
  local _line
  while IFS= read -r _line; do
    [[ -z "$_line" ]] && continue
    [[ "$_line" == "Only in ${dst}: "* || "$_line" == "Only in ${dst}/"* ]] && continue
    return 1
  done <<< "$_report"
  return 0
}

deposit_item() {
  local src="$1"
  local rel_path="$2"
  local label="$3"
  local dst="${KANBAN_ROOT}/${rel_path}"

  # Skip protected items — the whole point of upgrade vs. install.
  if _is_protected_path "$rel_path"; then
    info "Protected (preserved): $label"
    return 0
  fi

  if [[ ! -e "$src" ]]; then
    warn "Source missing (skipping): $src"
    return 0
  fi

  # Identical file — always skip silently.
  if [[ -f "$src" && -f "$dst" ]] && cmp -s "$src" "$dst" 2>/dev/null; then
    return 0
  fi
  # Directory already current — every shipped file identical in place.
  if [[ -d "$src" && -d "$dst" ]] && _deposit_dir_is_current "$src" "$dst"; then
    return 0
  fi

  # Bulk-quit: operator chose [q]uit earlier in this run.
  if [[ "$_DEPOSIT_BULK_CHOICE" == "quit" ]]; then
    warn "Skipped (quit): $label"
    _DEPOSIT_SKIPPED+=("$label")
    return 0
  fi

  if [[ -e "$dst" && "$FORCE_UPGRADE" != "true" ]]; then
    # Interactive prompt path.
    local _tty_ok=false
    if exec 3<>/dev/tty 2>/dev/null; then
      exec 3>&-
      _tty_ok=true
    fi

    if [[ "$_tty_ok" != "true" ]]; then
      # No TTY and no --force: skip cleanly.
      err "  $dst differs from source but no TTY is available for prompting."
      err "  Re-run with --force to overwrite silently."
      warn "Skipped (no-TTY): $label"
      _DEPOSIT_SKIPPED+=("$label")
      return 0
    fi

    if [[ "$_DEPOSIT_BULK_CHOICE" != "all" ]]; then
      local _answer=""
      printf '  %s already exists. Overwrite? [y]es/[N]o/[a]ll/[q]uit: ' "$dst" > /dev/tty
      IFS= read -r _answer < /dev/tty || _answer=""
      case "$_answer" in
        [aA]*) _DEPOSIT_BULK_CHOICE="all" ;;
        [qQ]*) _DEPOSIT_BULK_CHOICE="quit"; warn "Skipped (quit): $label"; _DEPOSIT_SKIPPED+=("$label"); return 0 ;;
        [yY]*) : ;; # proceed
        *)     warn "Skipped: $label"; _DEPOSIT_SKIPPED+=("$label"); return 0 ;;
      esac
    fi
  fi

  # Write the file or directory tree.
  if [[ -d "$src" ]]; then
    mkdir -p "$dst"
    cp -r "$src/." "$dst/"
  else
    mkdir -p "$(dirname "$dst")"
    cp "$src" "$dst"
  fi
  ok "Upgraded: $label"
}

# ---------------------------------------------------------------------------
# Source tree: same items as install.sh deposits.
# ---------------------------------------------------------------------------
info ""
info "Depositing upgraded kanban tree..."

for item in README.md DIRECTIVES.md OVERVIEW.md SOP-core.md roles scripts pm-agent workflows pgai_agent_kanban halt_after templates demos config read-order; do
  src="${DEV_TREE}/team/$item"
  [[ -e "$src" ]] || { warn "Source missing (skipping): team/$item"; continue; }
  deposit_item "$src" "$item" "team/$item"
done

# ---------------------------------------------------------------------------
# docs/ reference set: same manifest install.sh reads, delivered through
# deposit_item so its backup, protected-item and identical-file-skip
# semantics apply exactly as they do to the team/ items above. docs/ is a
# repository-root directory, not a team/ subdirectory, so it needs its own
# loop here — resolving these paths against ${DEV_TREE}/team/ would look for
# files that do not exist.
# ---------------------------------------------------------------------------
DOCS_MANIFEST="${DEV_TREE}/team/config/docs-manifest.txt"
DOCS_TO_DEPOSIT=()
if [[ ! -f "$DOCS_MANIFEST" ]]; then
  # Tolerant, like every "Source missing" check in the loop above: an older
  # dev tree checked out from before this manifest existed should not fail
  # the whole upgrade over a set of reference docs it never shipped either.
  warn "Docs manifest not found (skipping docs/ deposit): $DOCS_MANIFEST"
else
  while IFS= read -r doc_line; do
    doc_line="${doc_line%%#*}"
    doc_line="${doc_line#"${doc_line%%[![:space:]]*}"}"
    doc_line="${doc_line%"${doc_line##*[![:space:]]}"}"
    [[ -z "$doc_line" ]] && continue
    DOCS_TO_DEPOSIT+=("$doc_line")
  done < "$DOCS_MANIFEST"
fi

for doc_item in "${DOCS_TO_DEPOSIT[@]}"; do
  src="${DEV_TREE}/docs/${doc_item}"
  [[ -e "$src" ]] || { warn "Source missing (skipping): docs/$doc_item"; continue; }
  deposit_item "$src" "docs/$doc_item" "docs/$doc_item"
done
unset doc_item DOCS_MANIFEST DOCS_TO_DEPOSIT doc_line

# Refresh example templates (active configs derived from these are preserved).
# project.cfg_example is published from team/config/project.cfg_example, the
# one copy of its text (deposited above as config/project.cfg_example, which
# create-project.sh and add-project.sh render each new project.cfg from). An
# existing project's own project.cfg is never touched here.
for example in kanban.cfg_example shell-env_example secrets_example projects.cfg_example project.cfg_example claude-env_example; do
  src="${DEV_TREE}/$example"
  [[ "$example" == "project.cfg_example" ]] && src="${DEV_TREE}/team/config/project.cfg_example"
  [[ -e "$src" ]] || continue
  deposit_item "$src" "$example" "$example"
done

# ---------------------------------------------------------------------------
# Seed claude-env from template (no-clobber), same idiom as install.sh's
# fresh-install "Seed claude-env from template" block.
#
# claude-env is in PROTECTED_FILES above, so deposit_item never overwrites an
# existing one — but deposit_item also never CREATES a missing one, since
# protected paths short-circuit before the copy. An install upgrading from
# before claude-env existed as a shipped file would otherwise never get it
# at all. This block closes that gap: create-if-absent only, using the
# claude-env_example that was just refreshed above. An operator's existing
# claude-env — hand-tuned, possibly mid-upgrade-run — is never touched.
# ---------------------------------------------------------------------------
_claude_env_template="${DEV_TREE}/claude-env_example"
if [[ ! -f "${KANBAN_ROOT}/claude-env" ]]; then
  if [[ -f "$_claude_env_template" ]]; then
    cp "$_claude_env_template" "${KANBAN_ROOT}/claude-env"
    ok "Initialized: claude-env (from claude-env_example)"
  else
    warn "Source missing (skipping): claude-env_example — claude-env not initialized"
  fi
fi
unset _claude_env_template

# ---------------------------------------------------------------------------
# Ensure runtime directories exist (never delete, only create if missing).
# ---------------------------------------------------------------------------
info ""
info "Ensuring runtime directories..."

for _rdir in \
    logs \
    logs/agents \
    logs/debug \
    logs/debug/archive \
    locks; do
  if [[ ! -d "${KANBAN_ROOT}/${_rdir}" ]]; then
    mkdir -p "${KANBAN_ROOT}/${_rdir}"
    ok "Created runtime dir: ${_rdir}"
  fi
done

for _log_role in coder tester pm cm writer po; do
  if [[ ! -d "${KANBAN_ROOT}/logs/training/${_log_role}" ]]; then
    mkdir -p "${KANBAN_ROOT}/logs/training/${_log_role}"
  fi
done
unset _log_role _rdir

# Seed per-project log subdirs for all registered projects (idempotent).
_projects_cfg_file="${KANBAN_ROOT}/projects.cfg"
if [[ -f "$_projects_cfg_file" ]]; then
  while IFS= read -r _pname; do
    [[ -z "$_pname" ]] && continue
    _plog_dir="${KANBAN_ROOT}/projects/${_pname}/logs"
    mkdir -p "${_plog_dir}/debug/archive"
    mkdir -p "${_plog_dir}/training"
  done < <(awk '
    /^[[:space:]]*#/ { next }
    /^[[:space:]]*$/ { next }
    /^\[project:[a-zA-Z0-9_-]+\]$/ {
      sub(/^\[project:/, ""); sub(/\]$/, ""); print
    }
  ' "$_projects_cfg_file")
fi
unset _projects_cfg_file _pname _plog_dir

# ---------------------------------------------------------------------------
# Refresh registered projects' seeded intake templates (see SEEDED INTAKE
# TEMPLATES in the header). Runs after the deposit above, through the
# INSTALLED scripts/lib/pp_run_ops.sh with PYTHONPATH cleared, so the
# installed pgai_agent_kanban.ops module reads the installed
# templates/project/ set -- never this dev tree's. Idempotent: a re-run over
# current copies writes nothing and prints one no-op line. A failure warns
# and the upgrade carries on: a stale template copy is a quality issue, not
# a broken install.
# ---------------------------------------------------------------------------
if [[ -f "$KANBAN_ROOT/scripts/lib/pp_run_ops.sh" ]]; then
  if ! PYTHONPATH="" bash "$KANBAN_ROOT/scripts/lib/pp_run_ops.sh" pgai_agent_kanban.ops \
      refresh_seeded_templates --kanban-root="$KANBAN_ROOT"; then
    warn "Seeded template refresh reported problems (see the lines above); other projects were still refreshed."
    warn "Re-run it after fixing the cause: bash \"$KANBAN_ROOT/scripts/lib/pp_run_ops.sh\" pgai_agent_kanban.ops refresh_seeded_templates --kanban-root=\"$KANBAN_ROOT\""
  fi
else
  warn "Seeded template refresh skipped: $KANBAN_ROOT/scripts/lib/pp_run_ops.sh not installed"
fi

# ---------------------------------------------------------------------------
# Make scripts executable (idempotent).
# ---------------------------------------------------------------------------
if [[ -d "$KANBAN_ROOT/scripts" ]]; then
  chmod +x "$KANBAN_ROOT/scripts/"*.sh 2>/dev/null || true
fi

# ---------------------------------------------------------------------------
# briefs/ directory — ensure-if-missing.
# ---------------------------------------------------------------------------
if [[ ! -d "$KANBAN_ROOT/briefs" ]]; then
  mkdir -p "$KANBAN_ROOT/briefs"
  touch "$KANBAN_ROOT/briefs/.gitkeep"
  ok "Created: briefs/ directory"
fi

# ---------------------------------------------------------------------------
# Provider-agent wrapper deployment (opt-in; protected unless flag is passed).
#
# The deployed wrappers under $CLAUDE_CONFIG_DIR/agents/ (default
# ~/.claude/agents/) and ~/.codex/agents/ are in the protected set by
# default; provider_agents_dir (team/scripts/lib/subagent_deploy.sh) names
# each provider's directory.  Passing --add-claude-agents / --add-codex-agents
# explicitly OPTs IN to refreshing those wrappers for the named provider.
# Without the flag, NOTHING is written under the provider directory.
#
# On that protected path the deployed wrappers are still compared, read-only
# (subagent_wrapper_drift in team/scripts/lib/subagent_deploy.sh), with the
# ones this tree ships under subagents/: each missing or differing wrapper
# gets a warning naming provider and file plus the runnable refresh command,
# and an all-match result says the wrappers are current. A provider whose
# agents directory does not exist is neither compared nor created. The final
# summary reports one line per provider from the same results.
#
# Once opted in, the target agents directory is always created via mkdir -p
# (ensure_provider_agents_dir in team/scripts/lib/subagent_deploy.sh) when
# absent, before any wrapper is copied — a missing directory never causes a
# silent no-op. Creation failure (permissions, read-only HOME) fails loud
# naming the path and exits non-zero. Narration: "created <path>" once if
# the directory had to be created, then "deployed N wrapper(s)" always.
# ---------------------------------------------------------------------------
_SUBAGENT_SAFE_OVERWRITE_LIB="$DEV_TREE/team/scripts/lib/safe_overwrite.sh"
_subagent_safe_overwrite_available=false
if [[ -f "$_SUBAGENT_SAFE_OVERWRITE_LIB" ]]; then
  # shellcheck source=team/scripts/lib/safe_overwrite.sh
  source "$_SUBAGENT_SAFE_OVERWRITE_LIB"
  _subagent_safe_overwrite_available=true
fi
unset _SUBAGENT_SAFE_OVERWRITE_LIB

_SUBAGENT_DEPLOY_LIB="$DEV_TREE/team/scripts/lib/subagent_deploy.sh"
if [[ -f "$_SUBAGENT_DEPLOY_LIB" ]]; then
  # shellcheck source=team/scripts/lib/subagent_deploy.sh
  source "$_SUBAGENT_DEPLOY_LIB"
else
  err "subagent_deploy.sh not found at $_SUBAGENT_DEPLOY_LIB"
  err "This file is required and ships with the kanban repo."
  exit 1
fi
unset _SUBAGENT_DEPLOY_LIB

_SUBAGENT_INI_PARSER_LIB="$DEV_TREE/team/scripts/lib/ini_parser.sh"
_subagent_providers=""
if [[ -f "$_SUBAGENT_INI_PARSER_LIB" ]] && [[ -f "$KANBAN_ROOT/kanban.cfg" ]]; then
  # shellcheck source=team/scripts/lib/ini_parser.sh
  source "$_SUBAGENT_INI_PARSER_LIB"
  _subagent_providers="$(read_ini "$KANBAN_ROOT/kanban.cfg" providers available "")"
fi
[[ -z "$_subagent_providers" ]] && _subagent_providers="claude codex"
unset _SUBAGENT_INI_PARSER_LIB

SUBAGENT_MANIFEST="$DEV_TREE/subagents/MANIFEST.txt"
CURRENT_SUBAGENTS=()
if [[ -f "$SUBAGENT_MANIFEST" ]]; then
  while IFS= read -r line; do
    line="${line%%#*}"
    line="${line#"${line%%[![:space:]]*}"}"
    line="${line%"${line##*[![:space:]]}"}"
    [[ -z "$line" ]] && continue
    CURRENT_SUBAGENTS+=("$line")
  done < "$SUBAGENT_MANIFEST"
fi

INSTALLED_AGENTS_STATE="$KANBAN_ROOT/.installed-subagents"
PREVIOUSLY_INSTALLED=()
if [[ -f "$INSTALLED_AGENTS_STATE" ]]; then
  while IFS= read -r prev_line; do
    prev_line="${prev_line%%#*}"
    prev_line="${prev_line#"${prev_line%%[![:space:]]*}"}"
    prev_line="${prev_line%"${prev_line##*[![:space:]]}"}"
    [[ -z "$prev_line" ]] && continue
    PREVIOUSLY_INSTALLED+=("$prev_line")
  done < "$INSTALLED_AGENTS_STATE"
fi

_ANY_PROVIDER_FLAG_SET=false
# One entry per provider, printed under "Protected (preserved)" in the final
# summary (e.g. "claude: current (7 wrappers match the shipped copies)").
_WRAPPER_SUMMARY_LINES=()

# _report_protected_wrapper_drift <provider>
#
# Protected path for a provider whose opt-in flag was not passed. Compares
# the deployed wrappers with $DEV_TREE/subagents/ through
# subagent_wrapper_drift, which only reads, then narrates:
#   - agents directory absent: one skip line; not compared, not created.
#   - drift: one warning per missing or differing wrapper, naming
#     <provider>/<name>.md and the refresh command
#     ($KANBAN_ROOT/scripts/upgrade.sh --add-<provider>-agents).
#   - no drift: one line saying the wrappers are current.
# Appends the provider's outcome to _WRAPPER_SUMMARY_LINES. Writes nothing
# under any provider agents directory.
_report_protected_wrapper_drift() {
  local provider="$1"
  local agents_dir refresh_cmd drift state name
  local -a drifted=()
  agents_dir="$(provider_agents_dir "$provider")"
  refresh_cmd="$KANBAN_ROOT/scripts/upgrade.sh --add-${provider}-agents"

  if [[ ! -d "$agents_dir" ]]; then
    info "Provider-agent wrappers: skipping $provider (protected; no agents directory at $agents_dir, so nothing to compare)"
    _WRAPPER_SUMMARY_LINES+=("$provider: not compared (no agents directory at $agents_dir)")
    return 0
  fi
  if [[ ${#CURRENT_SUBAGENTS[@]} -eq 0 ]]; then
    info "Provider-agent wrappers: skipping $provider (protected; no wrappers listed in $SUBAGENT_MANIFEST to compare)"
    _WRAPPER_SUMMARY_LINES+=("$provider: not compared (no wrappers listed in subagents/MANIFEST.txt)")
    return 0
  fi

  drift="$(subagent_wrapper_drift "$DEV_TREE/subagents" "$agents_dir" "${CURRENT_SUBAGENTS[@]}")" || drift=""
  while read -r state name; do
    [[ -n "$name" ]] || continue
    drifted+=("$provider/$name.md")
    if [[ "$state" == "missing" ]]; then
      warn "Agent wrapper out of date: $provider/$name.md (missing from $agents_dir) -- refresh: $refresh_cmd"
    else
      warn "Agent wrapper out of date: $provider/$name.md (differs from subagents/$name.md) -- refresh: $refresh_cmd"
    fi
  done <<< "$drift"

  if [[ ${#drifted[@]} -eq 0 ]]; then
    info "Provider-agent wrappers: $provider wrappers current (all ${#CURRENT_SUBAGENTS[@]} match subagents/; protected, nothing written)"
    _WRAPPER_SUMMARY_LINES+=("$provider: current (${#CURRENT_SUBAGENTS[@]} wrappers match the shipped copies)")
  else
    info "Provider-agent wrappers: $provider has ${#drifted[@]} out-of-date wrapper(s); protected, nothing written"
    local joined
    joined="$(printf '%s, ' "${drifted[@]}")"
    _WRAPPER_SUMMARY_LINES+=("$provider: ${#drifted[@]} out of date (${joined%, }) -- refresh: $refresh_cmd")
  fi
  return 0
}

info ""

for _provider in $_subagent_providers; do
  case "$_provider" in
    claude)
      if [[ "$ADD_CLAUDE_AGENTS" != "true" ]]; then
        _report_protected_wrapper_drift claude
        continue
      fi
      ;;
    codex)
      if [[ "$ADD_CODEX_AGENTS" != "true" ]]; then
        _report_protected_wrapper_drift codex
        continue
      fi
      ;;
    *)
      info "Provider-agent wrappers: skipping unknown provider '$_provider' (no explicit opt-in flag)"
      continue
      ;;
  esac
  _ANY_PROVIDER_FLAG_SET=true

  info "Provider-agent wrapper deployment: provider=$_provider"

  if ! command -v "$_provider" >/dev/null 2>&1; then
    warn "Skipping $_provider: CLI binary not found on PATH"
    _WRAPPER_SUMMARY_LINES+=("$_provider: not refreshed (CLI binary not found on PATH)")
    continue
  fi

  _provider_agents_dir="$(provider_agents_dir "$_provider")"

  # Directory creation: always ensured before deploying wrappers — a missing
  # target directory must never silently no-op the deploy. See
  # ensure_provider_agents_dir() in team/scripts/lib/subagent_deploy.sh for
  # the fail-loud contract on creation failure.
  if ! ensure_provider_agents_dir "$_provider_agents_dir" "false"; then
    exit 1
  fi

  _provider_deployed_count=0
  for agent in "${CURRENT_SUBAGENTS[@]}"; do
    src="$DEV_TREE/subagents/${agent}.md"
    dst="${_provider_agents_dir}/${agent}.md"
    [[ -f "$src" ]] || { warn "Subagent source missing: $src"; continue; }

    _provider_deployed_count=$((_provider_deployed_count + 1))

    if [[ "$FORCE_UPGRADE" == "true" ]]; then
      cp "$src" "$dst"
      ok "Upgraded: $_provider/$agent"
    elif [[ "$_subagent_safe_overwrite_available" == "true" ]]; then
      safe_overwrite_file "$src" "$dst" || true
    else
      deposit_item "$src" ".${_provider}/agents/${agent}.md" "subagent: $_provider/$agent"
    fi
  done
  ok "deployed ${_provider_deployed_count} wrapper(s)"

  # Remove obsolete subagents (previously installed, no longer in manifest).
  if [[ ${#PREVIOUSLY_INSTALLED[@]} -gt 0 ]]; then
    for prev in "${PREVIOUSLY_INSTALLED[@]}"; do
      still_shipped=false
      for current in "${CURRENT_SUBAGENTS[@]}"; do
        [[ "$prev" == "$current" ]] && still_shipped=true && break
      done
      if [[ "$still_shipped" == "false" ]]; then
        obsolete_file="${_provider_agents_dir}/${prev}.md"
        if [[ -f "$obsolete_file" ]]; then
          info "Removing obsolete subagent (no longer in manifest): $_provider/$prev"
          rm -f "$obsolete_file"
        fi
      fi
    done
  fi

  ok "Provider-agent wrapper deployment complete for: $_provider"
  _WRAPPER_SUMMARY_LINES+=("$_provider: refreshed (--add-${_provider}-agents)")
done

# Update .installed-subagents state file when at least one provider ran.
if [[ "$_ANY_PROVIDER_FLAG_SET" == "true" ]] && [[ ${#CURRENT_SUBAGENTS[@]} -gt 0 ]]; then
  {
    echo "# pgai-agent-kanban: subagents installed by upgrade.sh"
    echo "# This file is managed automatically."
    echo "# Last updated: $(date -Iseconds)"
    printf '%s\n' "${CURRENT_SUBAGENTS[@]}"
  } > "$INSTALLED_AGENTS_STATE"
  info "Updated installed-subagents state file: $INSTALLED_AGENTS_STATE"
fi

unset _provider _provider_agents_dir _provider_deployed_count _subagent_providers
unset PREVIOUSLY_INSTALLED prev still_shipped current obsolete_file
unset _subagent_safe_overwrite_available _ANY_PROVIDER_FLAG_SET

# ---------------------------------------------------------------------------
# PARTIAL DEPOSIT: stop before the hooks, the VERSION stamp and migrations.
#
# deposit_item records every differing item it left undeposited (no TTY and
# no --force, or an N / q answer). Stamping VERSION over such a root would
# claim a version whose files are not all in place, and the migrations for
# that version would be selected out of every later run's range. So the run
# stops here: VERSION keeps the old version, the crontab and pseudocron are
# untouched, and the --force re-run selects every migration from the old
# version.
# ---------------------------------------------------------------------------
if [[ ${#_DEPOSIT_SKIPPED[@]} -gt 0 ]]; then
  _upg_skip_list="$(printf '%s, ' "${_DEPOSIT_SKIPPED[@]:0:10}")"
  _upg_skip_list="${_upg_skip_list%, }"
  if [[ ${#_DEPOSIT_SKIPPED[@]} -gt 10 ]]; then
    _upg_skip_list+=" and $(( ${#_DEPOSIT_SKIPPED[@]} - 10 )) more"
  fi
  _upg_old_version="$(tr -d '[:space:]' < "$KANBAN_ROOT/VERSION" 2>/dev/null || true)"
  _upg_rerun_dev_tree="${_UPG_ORIGINAL_DEV_TREE:-$DEV_TREE}"
  _upg_rerun="PGAI_AGENT_KANBAN_ROOT_PATH='${KANBAN_ROOT}' '${KANBAN_ROOT}/scripts/upgrade.sh'"
  [[ -n "$REQUESTED_VERSION" ]] && _upg_rerun+=" '${REQUESTED_VERSION}'"
  _upg_rerun+=" --dev-tree '${_upg_rerun_dev_tree}'"
  [[ -n "$WAKE_TIER" ]] && _upg_rerun+=" --wake-tier ${WAKE_TIER}"
  _upg_rerun+=" --force"
  # shellcheck source=lib/error_contract.sh
  source "$(dirname "${BASH_SOURCE[0]}")/lib/error_contract.sh"
  # BLOCK-WORTHY (docs/blocking-site-census.md): stamping VERSION over a
  # partly deposited root records a version whose files are not all in
  # place and drops that version's migrations from every later run's range
  # -- a state-corrupting partial upgrade.
  pgai_emit_error_contract \
    "upgrade.partial-deposit" \
    "The upgrade left ${#_DEPOSIT_SKIPPED[@]} changed item(s) undeposited (no TTY and no --force, or an N/q answer): ${_upg_skip_list}. It stopped before the crontab/pseudocron hooks, the VERSION stamp and the migrations: VERSION is still ${_upg_old_version:-unset} and no migration ran. Re-run with --force to deposit everything and finish." \
    "${_upg_rerun}"
  # CENSUS-ANCHOR: upgrade-partial-deposit-01
  exit 1
fi

# ---------------------------------------------------------------------------
# Scheduler hooks: crontab and pseudocron.
#
# Crontab: written only when --wake-tier / --crontab-tier names a tier. Then
#          install-crontab.sh rewrites the kanban-managed lines (the marked
#          pgai-agent-kanban block and lines naming this root) and keeps every
#          other line. With no tier given the crontab is not read for writing
#          and not touched; the detected tier above is reported only.
#
# Pseudocron: the tier is read from pseudocron.cfg's own "# Tier:" header,
#          never from the host crontab (a container host runs pseudocron with
#          an empty host crontab). No file or no header means no pseudocron
#          set-up on this host: the hook is skipped. Otherwise
#          install-pseudocron.sh --keep-existing leaves pseudocron.cfg
#          byte-identical (it may be hand-tuned) and only seeds a missing
#          pseudocron.env.
# ---------------------------------------------------------------------------
info ""

# Temp-root guard: skip scheduler hooks when the target is a test directory.
_kanban_root_real="$(readlink -m "$KANBAN_ROOT" 2>/dev/null || echo "$KANBAN_ROOT")"
_system_tmp_real="$(readlink -m "${TMPDIR:-/tmp}" 2>/dev/null || echo "${TMPDIR:-/tmp}")"
_is_temp_root=false
if [[ "$_kanban_root_real" == "${_system_tmp_real}"/* || "$_kanban_root_real" == "${_system_tmp_real}" ]]; then
  _is_temp_root=true
fi
if [[ -n "${PGAI_AGENT_KANBAN_TEMP_DIR:-}" ]]; then
  _pgai_temp_real="$(readlink -m "$PGAI_AGENT_KANBAN_TEMP_DIR" 2>/dev/null || echo "$PGAI_AGENT_KANBAN_TEMP_DIR")"
  if [[ "$_kanban_root_real" == "${_pgai_temp_real}"/* || "$_kanban_root_real" == "$_pgai_temp_real" ]]; then
    _is_temp_root=true
  fi
fi
if [[ "$_kanban_root_real" == */pytest-of-* || "$_kanban_root_real" == */pytest-* ]]; then
  _is_temp_root=true
fi
if [[ "$_kanban_root_real" == /var/folders/* ]]; then
  _is_temp_root=true
fi
unset _kanban_root_real _system_tmp_real

# _read_pseudocron_tier: echoes small|medium|large from the first
# "# Tier: <x>" line of $KANBAN_ROOT/pseudocron.cfg, or "none" when the file
# or the header is absent.
_read_pseudocron_tier() {
  local _cfg="$KANBAN_ROOT/pseudocron.cfg" _tier=""
  [[ -f "$_cfg" ]] || { echo "none"; return 0; }
  _tier="$(grep -iE '^[[:space:]]*#[[:space:]]*Tier:[[:space:]]*(small|medium|large)([[:space:]]|$)' "$_cfg" \
    | head -1 \
    | sed -E 's/^[[:space:]]*#[[:space:]]*Tier:[[:space:]]*//' \
    | awk '{print tolower($1)}')" || true
  case "$_tier" in
    small|medium|large) echo "$_tier" ;;
    *) echo "none" ;;
  esac
}

# Crontab hook.
_INSTALL_CRONTAB_SH="$KANBAN_ROOT/scripts/install-crontab.sh"
if [[ -z "$WAKE_TIER" ]]; then
  info "Crontab: left untouched (--wake-tier not supplied; detected tier: ${_UPG_DETECTED_TIER:-none})."
elif [[ "$WAKE_TIER" == "none" ]]; then
  info "Crontab: skipped (tier=none — cron-less / Docker deployment)."
elif [[ "$_is_temp_root" == "true" ]]; then
  info "Crontab: skipped — target is under a temp/test directory (temp-target guard)."
elif ! command -v "${PGAI_CRONTAB_CMD:-crontab}" >/dev/null 2>&1; then
  warn "Crontab: crontab not found in PATH — skipping crontab update."
elif [[ ! -f "$_INSTALL_CRONTAB_SH" ]]; then
  warn "Crontab: install-crontab.sh not found — skipping crontab update."
  warn "To install manually: $KANBAN_ROOT/scripts/install-crontab.sh --wake-tier=${WAKE_TIER}"
else
  info "Crontab: applying tier '${WAKE_TIER}' to the kanban-managed lines via install-crontab.sh..."
  _ct_result=0
  PGAI_AGENT_KANBAN_ROOT_PATH="$KANBAN_ROOT" "$_INSTALL_CRONTAB_SH" \
    --wake-tier="${WAKE_TIER}" --yes || _ct_result=$?
  case "$_ct_result" in
    0) ok "Crontab: ready (tier: ${WAKE_TIER})" ;;
    2) warn "Crontab: not installed (operator declined)." ;;
    *)
      warn "Crontab: installation failed (exit $_ct_result)."
      warn "To install manually: $KANBAN_ROOT/scripts/install-crontab.sh --wake-tier=${WAKE_TIER}"
      ;;
  esac
  unset _ct_result
fi
unset _INSTALL_CRONTAB_SH

# Pseudocron hook.
_INSTALL_PSEUDOCRON_SH="$KANBAN_ROOT/scripts/install-pseudocron.sh"
_UPG_PSEUDOCRON_TIER="$(_read_pseudocron_tier)"
if [[ "$_UPG_PSEUDOCRON_TIER" == "none" ]]; then
  info "Pseudocron: skipped (no tier in $KANBAN_ROOT/pseudocron.cfg — pseudocron is not set up on this host)."
elif [[ "$_is_temp_root" == "true" ]]; then
  info "Pseudocron: skipped — target is under a temp/test directory (temp-target guard)."
elif [[ ! -f "$_INSTALL_PSEUDOCRON_SH" ]]; then
  warn "Pseudocron: install-pseudocron.sh not found — skipping."
else
  info "Pseudocron: tier '${_UPG_PSEUDOCRON_TIER}' (from pseudocron.cfg); keeping pseudocron.cfg as-is..."
  _pc_result=0
  PGAI_AGENT_KANBAN_ROOT_PATH="$KANBAN_ROOT" "$_INSTALL_PSEUDOCRON_SH" \
    --wake-tier="${_UPG_PSEUDOCRON_TIER}" --keep-existing --yes || _pc_result=$?
  case "$_pc_result" in
    0) ok "Pseudocron: ready (tier: ${_UPG_PSEUDOCRON_TIER}; pseudocron.cfg kept)" ;;
    2) warn "Pseudocron: not installed (operator declined)." ;;
    *)
      warn "Pseudocron: installation failed (exit $_pc_result)."
      warn "To check by hand: $KANBAN_ROOT/scripts/install-pseudocron.sh --wake-tier=${_UPG_PSEUDOCRON_TIER} --keep-existing --dry-run"
      ;;
  esac
  unset _pc_result
fi
unset _INSTALL_PSEUDOCRON_SH _is_temp_root

# ---------------------------------------------------------------------------
# Capture the pre-upgrade VERSION for the migration runner below, before
# stamp_version_files() overwrites $KANBAN_ROOT/VERSION with the new one.
# Falls back to v0.0.0 (same fallback the version-targeted-upgrade forward-
# only check above uses) for a very old install that predates VERSION
# tracking -- the destination-keyed selection rule then simply selects every
# migration whose destination is <= the target, which is correct: an
# install with no VERSION file has no config layout newer migrations can
# assume already happened.
_UPG_MIGRATION_FROM_VERSION="v0.0.0"
if [[ -f "$KANBAN_ROOT/VERSION" ]]; then
  _upg_pre_version="$(tr -d '[:space:]' < "$KANBAN_ROOT/VERSION")"
  [[ -n "$_upg_pre_version" ]] && _UPG_MIGRATION_FROM_VERSION="$_upg_pre_version"
  unset _upg_pre_version
fi

# ---------------------------------------------------------------------------
# Write VERSION and VERSION_DETAIL files.
# ---------------------------------------------------------------------------
# Uses the shared stamp helper to propagate VERSION and write VERSION_DETAIL.
# The helper is the single implementation; install.sh calls the same function.
#
# Normal deposit path (no --stamp-version):
#   - VERSION comes from the dev tree's committed VERSION file when present.
#     The committed file IS the identity; no git-describe rewrite occurs.
#   - VERSION_DETAIL (full describe + deposit SHA) is always tool-written as
#     deployment provenance; it is not committed.
#   - A version-targeted upgrade passes the tag it resolved (_UPG_RESOLVED_TAG),
#     so VERSION_DETAIL starts with that tag (e.g. "ai_v2.33.0  deposit-sha=...").
#
# Override path (--stamp-version STRING):
#   - VERSION is written verbatim from the explicit value.
#   - VERSION_DETAIL is NOT written, and any earlier one is removed, so no
#     VERSION_DETAIL names a deposit other than the stamped one.
#
# The helper was sourced before the deposit (see "PRE-DEPOSIT" above); a tree
# without it never reaches this point.

# The stamp is written on every path, whether or not DEV_TREE is a git
# checkout, exactly as install.sh calls the helper: the override always
# lands in VERSION, and a normal deposit always copies the tree's committed
# VERSION and replaces or removes VERSION_DETAIL (the helper skips its own
# git work when there is no .git). Only the work that needs git sits under
# the .git test below: the branch-prefix resolution and the describe-based
# divergence advisory.
#
# -e (exists), not -d (is-a-directory): a version-targeted upgrade redirects
# DEV_TREE to a detached git worktree (see "VERSION-TARGETED UPGRADE" above),
# and a dev tree may itself be a linked worktree; either way its .git is a
# FILE (a gitdir pointer), not a directory. -e accepts both shapes, as
# stamp_version_files does on every path, so VERSION_DETAIL is written from
# either; a version-targeted upgrade also passes the tag it resolved so
# VERSION_DETAIL names that tag. git -C works unchanged against either shape.
_UPG_BRANCH_PREFIX=""
if [[ -e "$DEV_TREE/.git" ]]; then
  # Resolve branch_prefix for this project so the git-describe --match
  # pattern uses the project's own tag shape (<prefix>v[0-9]*). Shared with
  # the version-targeted-upgrade resolution block above so the two call
  # sites cannot drift on how the prefix is derived.
  _UPG_BRANCH_PREFIX="$(_upg_resolve_branch_prefix)"
fi

if [[ -n "$STAMP_VERSION" ]]; then
  # Operator override: write verbatim via shared helper; skip advisory.
  ok "VERSION stamp: using --stamp-version override: $STAMP_VERSION"
  stamp_version_files "$DEV_TREE" "$KANBAN_ROOT" "$STAMP_VERSION"
  ok "VERSION updated: $STAMP_VERSION"
else
  if [[ -e "$DEV_TREE/.git" ]]; then
    # Normal deposit: describe is used only for the advisory and VERSION_DETAIL,
    # not to determine VERSION.  VERSION comes from the dev tree's committed file.
    _upg_match_pattern="$(git_describe_tag_pattern "$_UPG_BRANCH_PREFIX")"
    _cur_full_describe="$(git -C "$DEV_TREE" describe --tags --match "$_upg_match_pattern" 2>/dev/null || true)"
    _cur_full_describe="${_cur_full_describe:-unknown-dev}"

    # Advisory: when the latest published tag differs from the describe result,
    # inform the operator of the staged-vs-published gap.  The advisory is one
    # line only; never a prompt or a blocking error.
    _VER_SH="$DEV_TREE/team/scripts/dashboard/lib/version.sh"
    if [[ -f "$_VER_SH" ]]; then
      # shellcheck source=team/scripts/dashboard/lib/version.sh
      source "$_VER_SH"
      _old_answer="$(get_latest_released_tag "$DEV_TREE" || true)"
      if [[ -n "$_old_answer" ]] && [[ "$_cur_full_describe" != "$_old_answer" ]]; then
        info "deploying $_cur_full_describe ; latest published tag is $_old_answer"
      fi
      unset _old_answer
    fi
    unset _VER_SH _upg_match_pattern _cur_full_describe
  fi

  # Propagate committed VERSION (when present) and write VERSION_DETAIL when
  # DEV_TREE is a git checkout. branch_prefix is passed so the describe
  # inside stamp_version_files is prefix-aware when building VERSION_DETAIL;
  # on a version-targeted upgrade the resolved tag is passed too (empty
  # otherwise), so VERSION_DETAIL names the tag actually laid down.
  stamp_version_files "$DEV_TREE" "$KANBAN_ROOT" "" "$_UPG_BRANCH_PREFIX" "${_UPG_RESOLVED_TAG:-}"
  _upg_clean_tag="$(tr -d '[:space:]' < "$KANBAN_ROOT/VERSION" 2>/dev/null || true)"
  # The helper leaves VERSION_DETAIL in place only when it wrote it, and
  # writes no VERSION at all from a tree with neither a committed VERSION
  # nor a .git -- say so rather than report the old VERSION as updated.
  if [[ -f "$KANBAN_ROOT/VERSION_DETAIL" ]]; then
    ok "VERSION updated: ${_upg_clean_tag} (from source tree; detail in VERSION_DETAIL)"
  elif [[ -f "$DEV_TREE/VERSION" ]]; then
    ok "VERSION updated: ${_upg_clean_tag} (from source tree; not a git checkout, so no VERSION_DETAIL)"
  else
    warn "VERSION unchanged: ${_upg_clean_tag:-none} (source tree has no committed VERSION and is not a git checkout; no VERSION_DETAIL)"
  fi
  unset _upg_clean_tag
fi
unset _UPG_BRANCH_PREFIX

# ---------------------------------------------------------------------------
# Apply destination-version-selected migrations (team/scripts/migrations/).
#
# This is the auto-runner team/scripts/migrations/README.md ("Auto-runner")
# promises: every migration script whose destination version falls in
# (installed, target] is applied automatically on upgrade, in ascending
# order, so an existing install picks up a config/layout change (e.g. the
# overwatch_resolve seed) without the operator needing to know a
# migration script exists or run it by hand.
#
# _UPG_MIGRATION_FROM_VERSION was captured above, before stamp_version_files
# overwrote $KANBAN_ROOT/VERSION. The "to" version is read now, after the
# stamp, so it names what this run deposited on every path -- the
# --stamp-version value, or the dev tree's committed VERSION, whether or not
# DEV_TREE is a git checkout. A deposit from a tree with no .git that carries
# a newer committed VERSION therefore selects the migrations in that range,
# as the same deposit from a checkout does. Only when the stamp leaves VERSION
# as it was (a re-deposit of the installed version, or a tree with neither a
# committed VERSION nor a .git, where the helper writes no VERSION) does
# _UPG_MIGRATION_TO_VERSION equal _UPG_MIGRATION_FROM_VERSION -- an empty
# (X, X] range selects nothing, which is correct: the installed version did
# not change.
#
# The runner is the SOURCE tree's own team/scripts/migrations/
# run-migrations.sh, so the migrations selected are the ones shipped with the
# version being stamped -- never an older installed runner's set. The root
# it migrates is passed explicitly as PGAI_AGENT_KANBAN_ROOT_PATH.
_UPG_MIGRATION_TO_VERSION="$_UPG_MIGRATION_FROM_VERSION"
if [[ -f "$KANBAN_ROOT/VERSION" ]]; then
  _upg_post_version="$(tr -d '[:space:]' < "$KANBAN_ROOT/VERSION")"
  [[ -n "$_upg_post_version" ]] && _UPG_MIGRATION_TO_VERSION="$_upg_post_version"
  unset _upg_post_version
fi

_UPG_MIGRATIONS_RUNNER="$DEV_TREE/team/scripts/migrations/run-migrations.sh"
if [[ -f "$_UPG_MIGRATIONS_RUNNER" ]]; then
  # A stamped identity (--stamp-version, or --dev-tree/--stamp-version test
  # fixtures) can legitimately be any operator-chosen string, not a released
  # semver tag -- that escape hatch exists precisely to "bypass git tag
  # resolution" (see --stamp-version's own header comment above). Real
  # migrations are keyed off released vX.Y.Z tags, so a from/to pair that
  # is not well-formed semver has nothing a migration script's destination
  # could ever match; skip the check rather than fail the whole upgrade
  # on an identity migrations were never meant to reason about.
  if [[ ! "$_UPG_MIGRATION_FROM_VERSION" =~ ^v?[0-9]+\.[0-9]+\.[0-9]+$ ]] \
    || [[ ! "$_UPG_MIGRATION_TO_VERSION" =~ ^v?[0-9]+\.[0-9]+\.[0-9]+$ ]]; then
    if [[ "$_UPG_MIGRATION_FROM_VERSION" != "$_UPG_MIGRATION_TO_VERSION" ]]; then
      info "Migrations: skipped -- '${_UPG_MIGRATION_FROM_VERSION}' or '${_UPG_MIGRATION_TO_VERSION}' is not a released vX.Y.Z version (stamped/test identity)."
    fi
  elif [[ "$_UPG_MIGRATION_FROM_VERSION" != "$_UPG_MIGRATION_TO_VERSION" ]]; then
    info "Checking for migrations in (${_UPG_MIGRATION_FROM_VERSION}, ${_UPG_MIGRATION_TO_VERSION}]..."
    _upg_mig_rc=0
    PGAI_AGENT_KANBAN_ROOT_PATH="$KANBAN_ROOT" bash "$_UPG_MIGRATIONS_RUNNER" \
      --from "$_UPG_MIGRATION_FROM_VERSION" --to "$_UPG_MIGRATION_TO_VERSION" || _upg_mig_rc=$?
    if [[ "$_upg_mig_rc" -ne 0 ]]; then
      err "ERROR: migration runner failed (exit ${_upg_mig_rc}) applying (${_UPG_MIGRATION_FROM_VERSION}, ${_UPG_MIGRATION_TO_VERSION}]."
      err "Deposit and VERSION stamp already completed; re-run this upgrade (or"
      err "PGAI_AGENT_KANBAN_ROOT_PATH=$KANBAN_ROOT $_UPG_MIGRATIONS_RUNNER --from ${_UPG_MIGRATION_FROM_VERSION} --to ${_UPG_MIGRATION_TO_VERSION} directly)"
      err "after fixing the underlying cause -- migrations are additive/idempotent, so"
      err "re-running is safe and already-applied ones no-op."
      # Classification (docs/blocking-site-census.md vocabulary): BLOCK-WORTHY
      # -- letting the upgrade report success here would silently leave an
      # existing install missing a config change a later release depends on
      # (see team/scripts/migrations/README.md, "Migration contract"); the
      # operator has no other signal that this step did not complete.
      # upgrade.sh is not on the census lint's scanned file list
      # (team/scripts/lint_gate_census_compliance.py _CENSUS_BASH_FILES) as of
      # this writing -- like every other pre-existing exit site in this file
      # (e.g. the manifest hygiene check above) -- so this is recorded in
      # prose rather than a formal CENSUS-ANCHOR / Locator Index entry.
      exit 1
    fi
    unset _upg_mig_rc
  fi
else
  warn "Migration runner not found (skipping): $_UPG_MIGRATIONS_RUNNER"
fi
unset _UPG_MIGRATIONS_RUNNER _UPG_MIGRATION_FROM_VERSION _UPG_MIGRATION_TO_VERSION

# ---------------------------------------------------------------------------
# Retired-file migration: move manifest-listed fossils to the graveyard.
#
# The retirement manifest ($KANBAN_ROOT/templates/retired-files.txt) lists
# kanban-root-relative paths that the framework once deposited and has since
# retired.  For each entry that exists at the live root, this step MOVES
# (never deletes) the file into $KANBAN_ROOT/retired/<UTC-ts>/<relative-path>
# so that two independent recovery layers are always available: the graveyard
# and the pre-upgrade backup tarball.
#
# Only exact manifest-listed paths are touched.  Operator-authored files
# (custom workflow plugins, wrappers, anything not listed in the manifest)
# are untouchable by construction.  A manifest entry whose path is absent
# from the live root is a silent no-op.
#
# The hygiene lint runs before any file is moved; a malformed manifest
# (glob characters, absolute paths, ".." segments) aborts the upgrade here.
# ---------------------------------------------------------------------------
_RETIRED_MANIFEST="${KANBAN_ROOT}/templates/retired-files.txt"
_RETIRED_LINT_SH="${KANBAN_ROOT}/scripts/lib/retired_files_lint.sh"
_RETIRED_TS="$(date -u +%Y%m%dT%H%M%SZ)"
_RETIRED_COUNT=0
_RETIRED_DEST_LABEL=""

if [[ -f "$_RETIRED_MANIFEST" ]]; then
  # Hygiene pre-check: abort if the manifest is malformed.
  if [[ -f "$_RETIRED_LINT_SH" ]]; then
    # shellcheck source=team/scripts/lib/retired_files_lint.sh
    source "$_RETIRED_LINT_SH"
    if ! lint_retired_files_manifest "$_RETIRED_MANIFEST"; then
      err "Manifest hygiene check failed: $_RETIRED_MANIFEST"
      err "Fix the manifest entries listed above, then re-run the upgrade."
      exit 1
    fi
  else
    warn "Retired-files lint helper not found; skipping manifest hygiene check: $_RETIRED_LINT_SH"
  fi

  # Retire each manifest-listed path that exists at the live root.
  while IFS= read -r _ret_line || [[ -n "$_ret_line" ]]; do
    # Skip blank lines and comment lines.
    [[ -z "$_ret_line" ]]       && continue
    [[ "$_ret_line" == \#* ]]   && continue

    _ret_src="${KANBAN_ROOT}/${_ret_line}"
    if [[ -f "$_ret_src" ]]; then
      _ret_dst_dir="${KANBAN_ROOT}/retired/${_RETIRED_TS}/$(dirname "$_ret_line")"
      mkdir -p "$_ret_dst_dir"
      mv -- "$_ret_src" "${KANBAN_ROOT}/retired/${_RETIRED_TS}/${_ret_line}"
      warn "Retired: ${_ret_line} -> retired/${_RETIRED_TS}/${_ret_line} (restore by moving back)"
      _RETIRED_COUNT=$(( _RETIRED_COUNT + 1 ))
      _RETIRED_DEST_LABEL="retired/${_RETIRED_TS}/"
    fi
  done < "$_RETIRED_MANIFEST"
fi

unset _RETIRED_MANIFEST _RETIRED_LINT_SH _RETIRED_TS _ret_line _ret_src _ret_dst_dir

# ---------------------------------------------------------------------------
# Success summary
# ---------------------------------------------------------------------------
echo ""
ok "Upgrade complete!"
if [[ -n "$REQUESTED_VERSION" ]]; then
  ok "upgraded ${_UPG_INSTALLED_VERSION} -> ${_UPG_TARGET_VERSION} (from tag ${_UPG_RESOLVED_TAG})"
fi
echo ""
echo "${C_BOLD}Summary:${C_RESET}"
echo "  Kanban root:       $KANBAN_ROOT"
if [[ -n "$BACKUP_PATH" && -f "$BACKUP_PATH" ]]; then
  echo "  Backup tarball:    $BACKUP_PATH  (chmod 600; includes secrets)"
fi
echo "  Dev tree:          $DEV_TREE"
if [[ -n "$REQUESTED_VERSION" ]]; then
  echo "  Version:           ${_UPG_INSTALLED_VERSION} -> ${_UPG_TARGET_VERSION} (tag ${_UPG_RESOLVED_TAG})"
fi
if [[ -n "$WAKE_TIER" ]]; then
  echo "  Wake tier:         ${WAKE_TIER} (--wake-tier)"
else
  echo "  Wake tier:         ${_UPG_DETECTED_TIER:-none} (detected; crontab untouched)"
fi
if [[ "$_RETIRED_COUNT" -gt 0 ]]; then
  echo "  Retired:           ${_RETIRED_COUNT} file(s) -> ${_RETIRED_DEST_LABEL}"
else
  echo "  Retired:           none"
fi
echo ""
echo "${C_BOLD}Protected (preserved):${C_RESET}"
echo "  kanban.cfg, projects.cfg, secrets, shell-env, claude-env, pseudocron.env"
echo "  projects/ directory (all per-project state; seeded intake templates refreshed, see above)"
if [[ -z "$WAKE_TIER" ]]; then
  echo "  crontab (left untouched; detected tier: ${_UPG_DETECTED_TIER:-none})"
else
  echo "  crontab lines outside the kanban-managed block (tier ${WAKE_TIER} applied to the block)"
fi
echo "  pseudocron.cfg (kept as-is; tier: ${_UPG_PSEUDOCRON_TIER:-none})"
if [[ ${#_WRAPPER_SUMMARY_LINES[@]} -gt 0 ]]; then
  for _wrapper_line in "${_WRAPPER_SUMMARY_LINES[@]}"; do
    echo "  agent wrappers, ${_wrapper_line}"
  done
else
  echo "  agent wrappers (pass --add-claude-agents / --add-codex-agents to refresh)"
fi
echo ""
echo "See $KANBAN_ROOT/README.md for post-upgrade notes."
echo ""

unset _RETIRED_COUNT _RETIRED_DEST_LABEL _WRAPPER_SUMMARY_LINES _wrapper_line
unset _UPG_DETECTED_TIER _UPG_PSEUDOCRON_TIER

exit 0
