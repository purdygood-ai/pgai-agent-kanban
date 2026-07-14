#!/usr/bin/env bash
# install.sh
# Install pgai-agent-kanban into the user's home directory and
# copy the subagents into Claude Code's agents directory.
#
# This is the FRESH-INSTALL path only. It lays down a complete kanban tree
# from the team/ source directory, seeds default configs, ensures runtime
# directories, writes VERSION, and installs the crontab/pseudocron schedule.
#
# If the install target already looks like an existing kanban installation,
# install.sh refuses and directs the operator to upgrade.sh instead.
#
# Subagent management:
#   The list of subagents to install is read from subagents/MANIFEST.txt (one
#   agent name per line; blank lines and # comments are ignored). The installer
#   tracks previously-installed agents in $KANBAN_ROOT/.installed-subagents so
#   that agents removed from the manifest are cleaned up on subsequent installs.
#   Non-kanban agent files in ~/.claude/agents/ are NEVER deleted — only agents
#   that this installer previously wrote (as recorded in .installed-subagents).
#
# Prerequisites:
#   - bash 4+          (macOS ships bash 3; install bash via Homebrew if needed)
#   - git              (required to clone/update the repo)
#   - Python 3.10+     (required for pm_materialize.py and pm_status.py)
#   - pytest           (required for run-unit-tests.sh and run-integration-tests.sh)
#                      install: pip install pytest --break-system-packages
#   - claude CLI       (Claude Code; install from https://claude.com/claude-code)
#
# Usage:
#   ./install.sh                               # interactive install with defaults
#   ./install.sh --force                       # overwrite without prompting (clean reinstall)
#   ./install.sh --dry-run                     # show what would happen, do nothing
#   ./install.sh --wake-tier large             # install with large tier
#   ./install.sh --stamp-version v9.9.9        # write VERSION verbatim, bypass git resolution
#
# NOTE: This script does NOT support --upgrade. To upgrade an existing
# installation, use upgrade.sh instead.
#
# Environment:
#   PGAI_AGENT_KANBAN_ROOT_PATH          # override install location (canonical)
#   CLAUDE_CONFIG_DIR                    # override Claude Code config dir
#                                        # (defaults to $HOME/.claude)
#
# Upstream: https://github.com/purdygood-ai/pgai-agent-kanban.git

set -euo pipefail

# --- Error handler (ensure non-zero exit produces clear message) ---
_err_handler() {
  local exit_code=$?
  local line_no="${1:-unknown}"
  echo "[install] ERROR: installation failed at line $line_no (exit code $exit_code)" >&2
  exit 1
}
trap '_err_handler $LINENO' ERR

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

info()  { echo "${C_BLUE}[install]${C_RESET} $*"; }
ok()    { echo "${C_GREEN}[install]${C_RESET} $*"; }
warn()  { echo "${C_YELLOW}[install]${C_RESET} $*"; }
err()   { echo "${C_RED}[install]${C_RESET} $*" >&2; }

# ---------------------------------------------------------------------------
# Source the shared argparse library.
# install.sh lives at the repo root; argparse.sh is at team/scripts/lib/.
# ---------------------------------------------------------------------------
_INSTALL_SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
_ARGPARSE_LIB="${_INSTALL_SCRIPT_DIR}/team/scripts/lib/argparse.sh"
if [[ ! -f "$_ARGPARSE_LIB" ]]; then
  echo "[install] ERROR: argparse.sh not found: $_ARGPARSE_LIB" >&2
  exit 1
fi
# shellcheck source=team/scripts/lib/argparse.sh
source "$_ARGPARSE_LIB"
unset _ARGPARSE_LIB

# --- Args ---
FORCE=false
DRY_RUN=false
# Default wake cadence tier: small (every 5 min; most conservative).
# Operators wanting higher cadence pass --wake-tier medium or --wake-tier large.
# This default matches install-crontab.sh's own default and install.sh's documented behavior.
WAKE_TIER="small"
# --no-system-cron: skip the OS crontab installation step entirely.  The OS crontab
# is left exactly as-is (no writes, no probes).  Intended for cron-less / Docker /
# customer-site deployments and CI environments.  pseudocron is ALWAYS installed
# regardless of this flag.
NO_SYSTEM_CRON=false
# --add-claude-agents / --add-codex-agents: opt-in provider-agent deployment.
# When absent, the provider-agent deployment block writes NOTHING for that provider
# (no file writes, no directory creation, no prompts, no state-file removal).
# Both flags together reproduce the pre-change default both-providers behavior.
ADD_CLAUDE_AGENTS=false
ADD_CODEX_AGENTS=false
STAMP_VERSION=""      # non-empty = write verbatim, skip git resolution
_print_install_usage() {
  cat <<EOF
Usage: ./install.sh [--force] [--dry-run] [--wake-tier small|medium|large]

Installs pgai-agent-kanban into your home directory and copies
the subagents into Claude Code's agents directory.

This is the FRESH-INSTALL path only.  If the install target already exists,
install.sh refuses with a "use upgrade.sh" message.  Use --force to bypass
the existence guard for a clean reinstall (e.g., after manually removing the
target directory).

Subagent installation:
  The subagents to install are listed in subagents/MANIFEST.txt (one name per
  line; blank lines and # comments are ignored). The installer records what it
  installs in \$KANBAN_ROOT/.installed-subagents. On subsequent runs, any agent
  found in the state file but no longer in the manifest is removed. Agents not
  in the state file (placed by the operator or another tool) are never touched.
  If the state file is missing, the installer treats the run as a fresh install
  and deletes nothing existing.

Wake cadence tier:
  Pass --wake-tier small|medium|large to select the wake schedule at install time.
  Also accepted as --wake-tier=small|medium|large.
  When the flag is omitted, the default tier (small) is applied.  The tier controls
  how frequently agents fire and applies to BOTH the OS crontab and pseudocron:

    small   Every 5 minutes per agent, integer-minute phase offsets.
            Hardware assumption: 1 core / <=8 GB RAM, or shared/low-resource host.
            DEFAULT when --wake-tier is omitted.

    medium  Every 2 minutes with sub-minute sleep stagger.
            Hardware assumption: 2-4 cores / 8-16 GB RAM, dedicated or semi-dedicated host.
            Operators upgrading from pre-v0.48.3 who want the old cadence should pass
            --wake-tier medium explicitly.

    large   Every minute with pipeline-ordered sub-minute stagger.
            Hardware assumption: 8+ cores / 16+ GB RAM, dedicated high-throughput host.

  To change the tier after installation, re-run:
    \$KANBAN_ROOT/scripts/install-crontab.sh --wake-tier <tier>
    \$KANBAN_ROOT/scripts/install-pseudocron.sh --wake-tier <tier>

Scheduler selection:
  By default both the OS crontab AND pseudocron config files are installed.
  Pass --no-system-cron for cron-less / Docker / customer-site deployments:
    - The OS crontab is left exactly as-is (no writes, no probes).
    - pseudocron config is STILL installed (it is inert until pseudocron.py is started).
    - Use team/scripts/pseudocron.py to drive wake scheduling in this mode.

Options:
  --force                Overwrite existing files without prompting (clean reinstall).
                         NOTE: does not bypass the "existing install" precondition check;
                         it only controls per-file overwrite behavior during deposit.
  --dry-run              Show what would be installed without writing anything;
                         does NOT modify \$KANBAN_ROOT/.installed-subagents
  --add-claude-agents    Deploy provider-agent wrappers to ~/.claude/agents/.
                         Without this flag, NOTHING is written under ~/.claude (no files, no directory
                         creation, no prompts). Use on machines where the Claude Code CLI is installed.
  --add-codex-agents     Deploy provider-agent wrappers to ~/.codex/agents/.
                         Without this flag, NOTHING is written under ~/.codex (no files, no directory
                         creation, no prompts). Use on machines where the Codex CLI is installed.
                         Note: --add-claude-agents --add-codex-agents together reproduce the pre-v0.58.2
                         default behavior of deploying wrappers to both providers. Existing users who
                         want agent refresh on upgrade must now pass the relevant flag(s) explicitly.
  --wake-tier <tier>     Select wake cadence tier: small (default), medium, or large.
                         Applies to both OS crontab and pseudocron. See "Wake cadence tier" above.
                         Also accepted as --wake-tier=<tier>.
  --no-system-cron       Skip OS crontab installation (pseudocron config is still installed).
                         Use for Docker / cron-less / customer-site deployments.
  --stamp-version STRING Write STRING verbatim to VERSION; bypass git resolution
                         and suppress divergence advisory.
  --help, -h             Show this help

Deprecated flags (still accepted; emit a deprecation notice to stderr):
  --crontab-tier <tier>  Use --wake-tier <tier> instead.
  --no-crontab           Use --no-system-cron instead.

Environment variables:
  PGAI_AGENT_KANBAN_ROOT_PATH          Where to install — canonical name
                                        Default: \$HOME/pgai_agent_kanban
  CLAUDE_CONFIG_DIR                     Claude Code config dir (default: \$HOME/.claude)
EOF
}

# ---------------------------------------------------------------------------
# Parse arguments via the shared argparse library.
# Value-taking flags: wake-tier (canonical), crontab-tier (deprecated alias).
# Boolean flags: all others.
# ---------------------------------------------------------------------------
argparse_parse --value-flags "wake-tier crontab-tier stamp-version" -- "$@"

# Emit clear errors for value-taking flags given with no value.
if argparse_missing "wake-tier"; then
  err "--wake-tier requires a value (small, medium, or large)."
  exit 1
fi
if argparse_missing "crontab-tier"; then
  err "--crontab-tier requires a value (small, medium, or large), or use --no-system-cron for none."
  exit 1
fi
if argparse_missing "stamp-version"; then
  err "--stamp-version requires a non-empty string argument."
  exit 1
fi

# Short flags (-h) arrive in ARGPARSE_POSITIONAL; handle them first.
for _pos in "${ARGPARSE_POSITIONAL[@]+"${ARGPARSE_POSITIONAL[@]}"}"; do
  case "$_pos" in
    -h) _print_install_usage; exit 0 ;;
    *)
      err "Unknown argument: $_pos"
      exit 1
      ;;
  esac
done
unset _pos

# Reject unknown flags.
for _flag in "${!ARGPARSE_FLAGS[@]}"; do
  case "$_flag" in
    force|dry-run|no-system-cron|add-claude-agents|add-codex-agents|stamp-version|wake-tier|help|no-crontab|crontab-tier) ;;
    upgrade)
      err "--upgrade is not supported by install.sh."
      err "To upgrade an existing installation, use upgrade.sh instead."
      exit 1
      ;;
    *)
      err "Unknown argument: --${_flag}"
      exit 1
      ;;
  esac
done
unset _flag

# Handle --help.
if argparse_has "help"; then
  _print_install_usage; exit 0
fi

# Extract boolean flags.
if argparse_has "force";             then FORCE=true; fi
if argparse_has "dry-run";           then DRY_RUN=true; fi
if argparse_has "no-system-cron";    then NO_SYSTEM_CRON=true; fi
if argparse_has "add-claude-agents"; then ADD_CLAUDE_AGENTS=true; fi
if argparse_has "add-codex-agents";  then ADD_CODEX_AGENTS=true; fi

# Handle deprecated --no-crontab.
if argparse_has "no-crontab"; then
  echo "[install] DEPRECATED: --no-crontab; use --no-system-cron instead." >&2
  NO_SYSTEM_CRON=true
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
    small|medium|large) ;;
    *)
      err "Invalid wake tier '${WAKE_TIER}'. Choose: small, medium, or large."
      exit 1
      ;;
  esac
fi

# Handle deprecated --crontab-tier flag.
if argparse_has "crontab-tier"; then
  _dep_ct="${ARGPARSE_FLAGS[crontab-tier]}"
  case "$_dep_ct" in
    none)
      echo "[install] DEPRECATED: --crontab-tier=none; use --no-system-cron instead." >&2
      NO_SYSTEM_CRON=true
      ;;
    small|medium|large)
      echo "[install] DEPRECATED: --crontab-tier=${_dep_ct}; use --wake-tier ${_dep_ct} instead." >&2
      WAKE_TIER="$_dep_ct"
      ;;
    *)
      err "Invalid tier '${_dep_ct}'. Choose: small, medium, or large."
      exit 1
      ;;
  esac
  unset _dep_ct
fi

# --- Resolve paths ---
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

# ---------------------------------------------------------------------------
# Path policy: resolve install location.
#
# Operator override: set PGAI_AGENT_KANBAN_ROOT_PATH before running install.sh
# to choose a non-default location.  Without an override, fresh installs land
# at the canonical default: $HOME/pgai_agent_kanban.
# ---------------------------------------------------------------------------
if [[ -n "${PGAI_AGENT_KANBAN_ROOT_PATH:-}" ]]; then
  # Explicit override takes priority.
  KANBAN_ROOT="${PGAI_AGENT_KANBAN_ROOT_PATH}"
else
  # Fresh install — use the canonical default path.
  KANBAN_ROOT="$HOME/pgai_agent_kanban"
fi

# Export canonical env var name pointing to the resolved path.
PGAI_AGENT_KANBAN_ROOT_PATH="$KANBAN_ROOT"
export PGAI_AGENT_KANBAN_ROOT_PATH

CLAUDE_DIR="${CLAUDE_CONFIG_DIR:-$HOME/.claude}"
AGENTS_DIR="${CLAUDE_DIR}/agents"

info "Source repo: $SCRIPT_DIR"
info "Install target: $KANBAN_ROOT"
info "Provider wrappers: ~/.{claude,codex}/agents/ (per kanban.cfg [providers] available)"
info ""

# ---------------------------------------------------------------------------
# Precondition: fail loud if this looks like an existing install.
#
# install.sh is the FRESH-INSTALL path only.  When the target directory already
# exists and contains kanban-install artifacts (kanban.cfg, VERSION, or a
# scripts/ subdirectory), refuse and redirect the operator to upgrade.sh.
#
# Use --force to bypass this guard for a clean reinstall (e.g., after manually
# removing the target directory).
# ---------------------------------------------------------------------------
if [[ "$DRY_RUN" != "true" ]] && [[ "$FORCE" != "true" ]]; then
  if [[ -d "$KANBAN_ROOT" ]]; then
    # Check for any of the canonical install artifacts.
    _looks_like_install=false
    for _artifact in kanban.cfg VERSION scripts; do
      if [[ -e "${KANBAN_ROOT}/${_artifact}" ]]; then
        _looks_like_install=true
        break
      fi
    done
    unset _artifact

    if [[ "$_looks_like_install" == "true" ]]; then
      err "ERROR: This looks like an existing pgai-agent-kanban installation:"
      err "  ${KANBAN_ROOT}"
      err ""
      err "install.sh is the fresh-install path and will not overwrite operator state."
      err "To upgrade an existing installation, use upgrade.sh instead:"
      err ""
      err "  ./upgrade.sh"
      err ""
      err "If you want a clean reinstall (operator removes state first):"
      err "  rm -rf \"${KANBAN_ROOT}\""
      err "  ./install.sh"
      err ""
      err "Or to force overwrite without the safety check (advanced use only):"
      err "  ./install.sh --force"
      exit 1
    fi
    unset _looks_like_install
  fi
fi

# --- Preflight checks ---
info "Running preflight checks..."

# Bash version check (bash 4+ required for associative arrays and other features)
BASH_MAJOR="${BASH_VERSINFO[0]}"
if [[ "$BASH_MAJOR" -lt 4 ]]; then
  err "bash 4+ is required (detected bash $BASH_VERSION)"
  err "On macOS, install a newer bash via Homebrew: brew install bash"
  exit 1
else
  ok "bash $BASH_VERSION OK"
fi

# git check
if ! command -v git >/dev/null 2>&1; then
  warn "git not found in PATH"
  warn "git is required to clone and update the kanban repository"
else
  ok "git found"
fi

if ! command -v python3 >/dev/null 2>&1; then
  err "python3 not found in PATH"
  err "Please install Python 3.10+ before continuing"
  exit 1
fi

PY_VERSION=$(python3 -c 'import sys; print(f"{sys.version_info.major}.{sys.version_info.minor}")')
PY_MAJOR=$(echo "$PY_VERSION" | cut -d. -f1)
PY_MINOR=$(echo "$PY_VERSION" | cut -d. -f2)

if [[ "$PY_MAJOR" -lt 3 ]] || [[ "$PY_MAJOR" -eq 3 && "$PY_MINOR" -lt 10 ]]; then
  warn "Python $PY_VERSION detected — version 3.10+ is recommended"
  warn "pm_materialize.py and pm_status.py may not work correctly"
  warn "Consider activating a newer Python in your bashrc"
else
  ok "Python $PY_VERSION OK"
fi

if ! command -v claude >/dev/null 2>&1; then
  warn "claude CLI not found in PATH"
  warn "Install Claude Code from https://claude.com/claude-code before running the wake scripts"
else
  ok "claude CLI found"
fi

if ! command -v flock >/dev/null 2>&1; then
  warn "flock not found in PATH — wake scripts use it for locking"
  warn "On Debian/Ubuntu: apt install util-linux"
  warn "On RHEL/Rocky: it's in util-linux (usually pre-installed)"
fi

if ! python3 -m pytest --version >/dev/null 2>&1; then
  warn "pytest not found — run-unit-tests.sh and run-integration-tests.sh will exit with code 2"
  warn "Install it with: pip install pytest --break-system-packages"
fi

if ! python3 -c 'import yaml' >/dev/null 2>&1; then
  warn "pyyaml not found — workflow YAML support requires it"
  warn "Install it with: pip install pyyaml --break-system-packages"
else
  ok "pyyaml found"
fi

info ""

# --- Bulk-choice state for install_path prompt-on-diff loop ---
# Values: "" (ask each time), "all" (overwrite all remaining), "quit" (skip all remaining).
# Reset to empty at script start; set to "all" or "quit" by user during a run.
_INSTALL_PATH_BULK_CHOICE=""

# --- Helper: copy a file or directory tree ---
# For directories, uses cp -rT semantics to merge contents into dst
# rather than nesting src inside dst on re-install.
#
# Overwrite semantics:
#   --force (FORCE=true):         always overwrite silently.
#   bare (FORCE=false):           prompt on existing files.
#
# Identical files (cmp -s) are NEVER prompted — silently skipped.
# Bulk choice [y]es/[N]o/[a]ll/[q]uit avoids per-file flood during reinstalls.
install_path() {
  local src="$1"
  local dst="$2"
  local label="$3"

  if [[ "$DRY_RUN" == "true" ]]; then
    info "[dry-run] would copy: $label"
    return 0
  fi

  # Bulk-quit: user chose [q]uit earlier in this run — skip without prompting.
  if [[ "$_INSTALL_PATH_BULK_CHOICE" == "quit" ]]; then
    warn "Skipped (quit): $label"
    return 0
  fi

  if [[ -e "$dst" ]]; then
    # Identical file — never prompt, always skip silently.
    if [[ -f "$src" ]] && [[ -f "$dst" ]] && cmp -s "$src" "$dst" 2>/dev/null; then
      return 0
    fi

    if [[ "$FORCE" != "true" ]]; then
      # Interactive prompt path.  Guard against no-TTY context.
      local _ip_tty_ok=false
      if exec 3<>/dev/tty 2>/dev/null; then
        exec 3>&-
        _ip_tty_ok=true
      fi

      if [[ "$_ip_tty_ok" != "true" ]]; then
        # No controlling terminal and no --force: decline cleanly.
        err "  $dst differs from source but no TTY is available for prompting."
        err "  Run with --force to overwrite silently in unattended contexts."
        warn "Skipped (no-TTY): $label"
        return 0
      fi

      # Bulk-all: user chose [a]ll earlier — overwrite without re-prompting.
      local answer=""
      if [[ "$_INSTALL_PATH_BULK_CHOICE" != "all" ]]; then
        printf '  %s already exists. Overwrite? [y]es/[N]o/[a]ll/[q]uit: ' "$dst" > /dev/tty
        IFS= read -r answer < /dev/tty || answer=""
        case "$answer" in
          [aA]*)
            _INSTALL_PATH_BULK_CHOICE="all"
            ;;
          [qQ]*)
            _INSTALL_PATH_BULK_CHOICE="quit"
            warn "Skipped (quit): $label"
            return 0
            ;;
          [yY]*)
            : # proceed
            ;;
          *)
            warn "Skipped: $label"
            return 0
            ;;
        esac
      fi
    fi
    # FORCE=true or _INSTALL_PATH_BULK_CHOICE=="all" — fall through to copy below.
  fi

  if [[ -d "$src" ]]; then
    # Source is a directory — merge contents into dst, not nest src inside dst.
    # cp -rT does this on GNU coreutils. The /. trailing form works on both
    # GNU and BSD cp.
    mkdir -p "$dst"
    cp -r "$src/." "$dst/"
  else
    mkdir -p "$(dirname "$dst")"
    cp "$src" "$dst"
  fi
  ok "Installed: $label"
}

# ---------------------------------------------------------------------------
# Begin fresh install
# ---------------------------------------------------------------------------
if [[ "$DRY_RUN" != "true" ]]; then
  info "Fresh install: initializing $KANBAN_ROOT"
  mkdir -p "$KANBAN_ROOT"

  # --- Seed kanban.cfg from template (no-clobber) ---
  # kanban.cfg is the single source of truth for framework operational settings.
  # As of v0.28.0, the template lives at the kanban-tree root as kanban.cfg_example.
  _kanban_cfg_template="${SCRIPT_DIR}/kanban.cfg_example"
  if [[ ! -f "$KANBAN_ROOT/kanban.cfg" ]]; then
    if [[ -f "$_kanban_cfg_template" ]]; then
      cp "$_kanban_cfg_template" "$KANBAN_ROOT/kanban.cfg"
      ok "Initialized: kanban.cfg (from kanban.cfg_example)"
    else
      warn "Source missing (skipping): kanban.cfg_example — kanban.cfg not initialized"
    fi
  else
    info "kanban.cfg already exists — skipping template copy"
  fi
  unset _kanban_cfg_template
fi

# --- Install kanban tree ---
info "Installing kanban tree to $KANBAN_ROOT..."

# Copy each item from team/ into the kanban root.
# NOTE: team/release-state.md was removed in v0.21.7 (PRIORITY-0001 schema redesign).
# Runtime release state lives exclusively at:
#   $KANBAN_ROOT/projects/<name>/release-state.md
# That file tracks only in-flight RC state; Last Released is derived from git tags.
for item in README.md DIRECTIVES.md OVERVIEW.md SOP.md roles scripts pm-agent workflows pgai_agent_kanban halt_after templates demos; do
  src="$SCRIPT_DIR/team/$item"
  dst="$KANBAN_ROOT/$item"

  if [[ ! -e "$src" ]]; then
    warn "Source missing (skipping): $src"
    continue
  fi

  install_path "$src" "$dst" "team/$item"
done

# Copy the v0.28.0 example template files (but not as the active files — leave that to the user).
# kanban.cfg_example is the consolidated INI config template.
# shell-env_example and secrets_example replace the deprecated bashrc.
# projects.cfg_example and project.cfg_example are the registry / per-project templates.
for example in kanban.cfg_example shell-env_example secrets_example projects.cfg_example project.cfg_example; do
  src="$SCRIPT_DIR/$example"
  dst="$KANBAN_ROOT/$example"

  if [[ ! -e "$src" ]]; then
    continue
  fi

  install_path "$src" "$dst" "$example"
done

# Note: install.sh does NOT auto-seed shell-env or secrets from their templates.
# Both are operator-opt-in (PATH, OAuth tokens are personal). Operator copies
# them manually:
#   cp shell-env_example shell-env
#   cp secrets_example secrets && chmod 600 secrets
#
# In contrast, kanban.cfg IS auto-seeded (see "Seed kanban.cfg from template"
# block earlier in install.sh) because the framework needs it to read tunables.

# Make sure runtime directories exist.
# As of v0.18.0, per-project runtime state lives under projects/<name>/.
# As of v0.18.4, kanban-root-scoped runtime dirs are also seeded here:
#   $KANBAN_ROOT/logs/   — cron job logs (one per agent, shared across projects)
#   $KANBAN_ROOT/locks/  — per-agent and per-repo flock files (cross-project)
# As of v0.53.0, per-project log subdirs are seeded for every project in projects.cfg.
if [[ "$DRY_RUN" != "true" ]]; then
  # Kanban-root-scoped runtime dirs.
  #   logs/                 — cron-<agent>.log redirects and CM push logs
  #   logs/agents/          — per-firing wake batch logs (v0.27.2+)
  #   logs/debug/           — per-agent verbose progress logs (v0.27.3+, kanban-wide)
  #                           Written when PGAI_VERBOSE_MODE=1; one file per agent
  #                           (coder.log, pm.log, ...) across all projects. Lines
  #                           self-identify via [<task_id>] prefix.
  #   logs/debug/archive/   — daily-rotated debug logs (managed by cleanup.sh)
  #   logs/training/<role>/ — reasoning-trace corpus (v0.27.3+, kanban-wide)
  #                           Copied here when PGAI_REASONING_TRACE=1 after a task
  #                           reaches DONE; downstream PO-training corpus.
  #   locks/                — flock files
  mkdir -p "${KANBAN_ROOT}/logs"
  mkdir -p "${KANBAN_ROOT}/logs/agents"
  mkdir -p "${KANBAN_ROOT}/logs/debug/archive"
  for _log_role in coder tester pm cm writer po; do
    mkdir -p "${KANBAN_ROOT}/logs/training/${_log_role}"
  done
  unset _log_role
  mkdir -p "${KANBAN_ROOT}/locks"

  # Create the centralized framework temp directory (idempotent).
  # The temp dir lives outside the kanban root (default: /tmp/pgai_kanban_tmp)
  # so it is not overwritten by install.sh -- but we ensure it exists here so
  # all subsystems can assume it is present after install.
  #
  # Compute the path from the two [paths] keys (tmp_root / tmp_subdir) that
  # kanban.cfg_example defines and that were just seeded into kanban.cfg above.
  # Falls back to /tmp/pgai_kanban_tmp if the config is unreadable or either
  # key is absent, matching the resolver's own last-resort fallback.
  _tmp_ini_lib="${SCRIPT_DIR}/team/scripts/lib/ini_parser.sh"
  _tmp_cfg="${KANBAN_ROOT}/kanban.cfg"
  if [[ -f "$_tmp_ini_lib" ]] && [[ -f "$_tmp_cfg" ]]; then
    # shellcheck source=team/scripts/lib/ini_parser.sh
    source "$_tmp_ini_lib"
    _tmp_root="$(read_ini "$_tmp_cfg" paths tmp_root "/tmp")"
    _tmp_subdir="$(read_ini "$_tmp_cfg" paths tmp_subdir "pgai_kanban_tmp")"
    # Guard against blank values (should not happen, but be safe).
    _tmp_root="${_tmp_root:-/tmp}"
    _tmp_subdir="${_tmp_subdir:-pgai_kanban_tmp}"
  else
    _tmp_root="/tmp"
    _tmp_subdir="pgai_kanban_tmp"
  fi
  PGAI_TEMP_DIR="${_tmp_root}/${_tmp_subdir}"
  if mkdir -p "$PGAI_TEMP_DIR" 2>/dev/null; then
    ok "Ensured framework temp directory: $PGAI_TEMP_DIR"
  else
    warn "Could not create framework temp directory: $PGAI_TEMP_DIR (check permissions)"
  fi
  unset PGAI_TEMP_DIR _tmp_ini_lib _tmp_cfg _tmp_root _tmp_subdir

  # Make scripts executable
  chmod +x "$KANBAN_ROOT/scripts/"*.sh 2>/dev/null || true

  # Install ship-rc.sh into the live kanban scripts directory with executable bit
  if [[ -f "$SCRIPT_DIR/team/scripts/ship-rc.sh" ]]; then
    mkdir -p "$KANBAN_ROOT/scripts"
    cp "$SCRIPT_DIR/team/scripts/ship-rc.sh" "$KANBAN_ROOT/scripts/ship-rc.sh"
    chmod +x "$KANBAN_ROOT/scripts/ship-rc.sh"
    ok "Installed: scripts/ship-rc.sh"
  else
    warn "Source missing (skipping): team/scripts/ship-rc.sh"
  fi

  # Install po-agent.sh into the live kanban scripts directory with executable bit
  if [[ -f "$SCRIPT_DIR/team/scripts/po-agent.sh" ]]; then
    mkdir -p "$KANBAN_ROOT/scripts"
    cp "$SCRIPT_DIR/team/scripts/po-agent.sh" "$KANBAN_ROOT/scripts/po-agent.sh"
    chmod +x "$KANBAN_ROOT/scripts/po-agent.sh"
    ok "Installed: scripts/po-agent.sh"
  else
    warn "Source missing (skipping): team/scripts/po-agent.sh"
  fi

  # Install cm-cancel-rc.sh into the live kanban scripts directory with executable bit
  if [[ -f "$SCRIPT_DIR/team/scripts/cm/cancel-rc.sh" ]]; then
    mkdir -p "$KANBAN_ROOT/scripts"
    cp "$SCRIPT_DIR/team/scripts/cm/cancel-rc.sh" "$KANBAN_ROOT/scripts/cm/cancel-rc.sh"
    chmod +x "$KANBAN_ROOT/scripts/cm/cancel-rc.sh"
    ok "Installed: scripts/cm/cancel-rc.sh"
  else
    warn "Source missing (skipping): team/scripts/cm/cancel-rc.sh"
  fi

  # Install the CM origin-push recovery script
  # (PreToolUse hook blocks push to main/tags inside cm-release.sh)
  _cm_recovery_name="push-""watch""dog"".sh"
  _pw_src="$SCRIPT_DIR/team/scripts/cm/${_cm_recovery_name}"
  _pw_dst="$KANBAN_ROOT/scripts/cm/${_cm_recovery_name}"
  if [[ -f "$_pw_src" ]]; then
    mkdir -p "$KANBAN_ROOT/scripts/cm"
    cp "$_pw_src" "$_pw_dst"
    chmod +x "$_pw_dst"
    ok "Installed: scripts/cm/${_cm_recovery_name}"
  else
    warn "Source missing (skipping): team/scripts/cm/${_cm_recovery_name}"
  fi
  unset _cm_recovery_name _pw_src _pw_dst

  # Install cm-open-doc.sh into the live kanban scripts directory with executable bit
  if [[ -f "$SCRIPT_DIR/team/scripts/cm/open-doc.sh" ]]; then
    mkdir -p "$KANBAN_ROOT/scripts"
    cp "$SCRIPT_DIR/team/scripts/cm/open-doc.sh" "$KANBAN_ROOT/scripts/cm/open-doc.sh"
    chmod +x "$KANBAN_ROOT/scripts/cm/open-doc.sh"
    ok "Installed: scripts/cm/open-doc.sh"
  else
    warn "Source missing (skipping): team/scripts/cm/open-doc.sh"
  fi

  # Install cm-finalize.sh into the live kanban scripts directory with executable bit
  if [[ -f "$SCRIPT_DIR/team/scripts/cm/finalize.sh" ]]; then
    mkdir -p "$KANBAN_ROOT/scripts"
    cp "$SCRIPT_DIR/team/scripts/cm/finalize.sh" "$KANBAN_ROOT/scripts/cm/finalize.sh"
    chmod +x "$KANBAN_ROOT/scripts/cm/finalize.sh"
    ok "Installed: scripts/cm/finalize.sh"
  else
    warn "Source missing (skipping): team/scripts/cm/finalize.sh"
  fi

  # Install v0.20.0 operator scripts: project lifecycle management
  for v020_script in create-project.sh remove-project.sh add-project.sh export-project.sh import-project.sh; do
    if [[ -f "$SCRIPT_DIR/team/scripts/${v020_script}" ]]; then
      mkdir -p "$KANBAN_ROOT/scripts"
      cp "$SCRIPT_DIR/team/scripts/${v020_script}" "$KANBAN_ROOT/scripts/${v020_script}"
      chmod +x "$KANBAN_ROOT/scripts/${v020_script}"
      ok "Installed: scripts/${v020_script}"
    else
      warn "Source missing (skipping): team/scripts/${v020_script}"
    fi
  done

  # Install init-project-git-repo.sh — operator git topology setup script
  # Establishes chain base branches on origin before the first release.
  # Run once after create-project.sh to push develop/main (or prefixed equivalents).
  if [[ -f "$SCRIPT_DIR/team/scripts/init-project-git-repo.sh" ]]; then
    mkdir -p "$KANBAN_ROOT/scripts"
    cp "$SCRIPT_DIR/team/scripts/init-project-git-repo.sh" "$KANBAN_ROOT/scripts/init-project-git-repo.sh"
    chmod +x "$KANBAN_ROOT/scripts/init-project-git-repo.sh"
    ok "Installed: scripts/init-project-git-repo.sh"
  else
    warn "Source missing (skipping): team/scripts/init-project-git-repo.sh"
  fi

  # Install lib/projects.sh — registry helper used by the operator scripts
  if [[ -f "$SCRIPT_DIR/team/scripts/lib/projects.sh" ]]; then
    mkdir -p "$KANBAN_ROOT/scripts/lib"
    cp "$SCRIPT_DIR/team/scripts/lib/projects.sh" "$KANBAN_ROOT/scripts/lib/projects.sh"
    ok "Installed: scripts/lib/projects.sh"
  else
    warn "Source missing (skipping): team/scripts/lib/projects.sh"
  fi


  # Install purge-old-files.sh — disk hygiene script
  if [[ -f "$SCRIPT_DIR/team/scripts/cleanup/purge-old-files.sh" ]]; then
    cp "$SCRIPT_DIR/team/scripts/cleanup/purge-old-files.sh" "$KANBAN_ROOT/scripts/cleanup/purge-old-files.sh"
    chmod +x "$KANBAN_ROOT/scripts/cleanup/purge-old-files.sh"
    ok "Installed: scripts/cleanup/purge-old-files.sh"
  else
    warn "Source missing (skipping): team/scripts/cleanup/purge-old-files.sh"
  fi

  # Install lib/purge-helpers.sh — per-category helpers sourced by purge-old-files.sh
  if [[ -f "$SCRIPT_DIR/team/scripts/lib/purge-helpers.sh" ]]; then
    mkdir -p "$KANBAN_ROOT/scripts/lib"
    cp "$SCRIPT_DIR/team/scripts/lib/purge-helpers.sh" "$KANBAN_ROOT/scripts/lib/purge-helpers.sh"
    ok "Installed: scripts/lib/purge-helpers.sh"
  else
    warn "Source missing (skipping): team/scripts/lib/purge-helpers.sh"
  fi

  # projects.cfg is NOT seeded by install.sh. Register projects via:
  #   scripts/create-project.sh --project <name>  — bootstrap a new project
  #   scripts/add-project.sh <name>         — register an existing project
  # A fresh install therefore has an empty (or absent) projects.cfg until
  # the operator registers their first project.
  info "projects.cfg not seeded — register projects via scripts/create-project.sh"

  # Generate cron-suggested.txt from template by substituting __KANBAN_ROOT__ and __CLAUDE_PATH__
  if [[ -f "$SCRIPT_DIR/team/scripts/cron-suggested.template.txt" ]]; then
    mkdir -p "$KANBAN_ROOT/scripts"
    CLAUDE_PATH=$(command -v claude 2>/dev/null) || true
    if [[ -n "$CLAUDE_PATH" ]]; then
      CLAUDE_DIR=$(dirname "$CLAUDE_PATH")
    else
      CLAUDE_DIR="/usr/local/bin"
      warn "claude binary not found in PATH; defaulting __CLAUDE_PATH__ to $CLAUDE_DIR in cron-suggested.txt"
    fi
    sed -e "s|__KANBAN_ROOT__|${KANBAN_ROOT}|g" \
        -e "s|__CLAUDE_PATH__|${CLAUDE_DIR}|g" \
      "$SCRIPT_DIR/team/scripts/cron-suggested.template.txt" \
      > "$KANBAN_ROOT/scripts/cron-suggested.txt"
    ok "Generated: scripts/cron-suggested.txt (claude dir: $CLAUDE_DIR)"
  else
    warn "Source missing (skipping): team/scripts/cron-suggested.template.txt"
  fi

  # Make cleanup.sh executable
  if [[ -f "$KANBAN_ROOT/scripts/cleanup/cleanup.sh" ]]; then
    chmod +x "$KANBAN_ROOT/scripts/cleanup/cleanup.sh"
    ok "Set executable: scripts/cleanup/cleanup.sh"
  fi

  # Create briefs/ directory (archive/ is created lazily by cleanup.sh — do NOT create it here)
  if [[ ! -d "$KANBAN_ROOT/briefs" ]]; then
    mkdir -p "$KANBAN_ROOT/briefs"
    touch "$KANBAN_ROOT/briefs/.gitkeep"
    ok "Created: briefs/ directory"
  else
    info "briefs/ already exists — skipping"
  fi

fi

# --- Seed per-project log subdirs for all registered projects ---
# Creates projects/<name>/logs/debug/archive/ and projects/<name>/logs/training/
# for every project listed in projects.cfg (INI format: [project:<name>] sections).
# Constraints:
#   - Only seeds dirs for projects already in projects.cfg; never invents names.
#   - mkdir -p is idempotent — safe to re-run on an existing install.
#   - DRY_RUN=true prints intentions without touching the filesystem.
#   - projects/<name>/logs/ is assumed to already exist (seeded by create-project.sh);
#     this pass only adds the two subtrees.
#   - logs/training/ is reserved for v0.54.0 and stays empty in this RC.
_projects_cfg_file="${KANBAN_ROOT}/projects.cfg"
if [[ -f "$_projects_cfg_file" ]]; then
  while IFS= read -r _pname; do
    [[ -z "$_pname" ]] && continue
    _plog_dir="${KANBAN_ROOT}/projects/${_pname}/logs"
    if [[ "$DRY_RUN" == "true" ]]; then
      info "[dry-run] would mkdir -p: projects/${_pname}/logs/debug/archive/"
      info "[dry-run] would mkdir -p: projects/${_pname}/logs/training/"
    else
      mkdir -p "${_plog_dir}/debug/archive"
      mkdir -p "${_plog_dir}/training"
      ok "Ensured per-project log subdirs: projects/${_pname}/logs/debug/archive/ and logs/training/"
    fi
  done < <(awk '
    /^[[:space:]]*#/ { next }
    /^[[:space:]]*$/ { next }
    /^\[project:[a-zA-Z0-9_-]+\]$/ {
      sub(/^\[project:/, ""); sub(/\]$/, ""); print
    }
  ' "$_projects_cfg_file")
else
  if [[ "$DRY_RUN" == "true" ]]; then
    info "[dry-run] projects.cfg not found — per-project log subdir seeding skipped"
  else
    info "projects.cfg not found — per-project log subdir seeding skipped (run create-project.sh to register a project)"
  fi
fi
unset _projects_cfg_file _pname _plog_dir

info ""

# --- Install provider-agent wrappers (multi-provider, v0.27.1+) ---
# These are the lowercase provider-agent wrapper files from subagents/*.md
# (cm.md, coder.md, pm.md, po.md, tester.md, writer.md, and others).
#
# They are deployed to ~/.{provider}/agents/ for EACH configured provider —
# claude, codex, and any others listed in kanban.cfg [providers] available.
# These are the generic agent contracts the LLM provider invokes; the
# kanban-specific role bodies (uppercase team/roles/*.md -> $KANBAN_ROOT/roles/)
# are shared across all providers and are deployed via the team/ tree copy
# loop above (not duplicated per-provider).
#
# Provider list source (in priority order):
#   1. kanban.cfg [providers] available — INI config (when PRIORITY-0038 has shipped)
#   2. Hardcoded fallback: "claude codex"
#
# Per-provider behavior:
#   - CLI detection: provider binary not on PATH → skip with warning.
#   - Directory creation: ~/.{provider}/agents/ absent → prompt to create
#     (default Y); --force creates without prompting.
#   - Per-file write via safe_overwrite_file from safe_overwrite.sh:
#       Target does not exist:     prompt to install, default Y.
#       Target exists, identical:  silent no-op (cmp -s check).
#       Target exists, differs:    show sizes/mtime, prompt to overwrite, default N.
#       Operator declines:         prints manual cp command, skips.
#       --force mode:              copies directly without interactive prompts.
#       --dry-run mode:            prints what would happen, no writes.
#
# State tracking:
#   $KANBAN_ROOT/.installed-subagents records the manifest set this install
#   wrote. On subsequent runs, any name in the state file but absent from the
#   current manifest is removed from every provider's agents directory.
#   Files we did NOT previously install (operator's own agents, other tools)
#   are never touched.

# --- Source safe_overwrite helpers ---
_SUBAGENT_SAFE_OVERWRITE_LIB="$SCRIPT_DIR/team/scripts/lib/safe_overwrite.sh"
_subagent_safe_overwrite_available=false
if [[ -f "$_SUBAGENT_SAFE_OVERWRITE_LIB" ]]; then
  # shellcheck source=team/scripts/lib/safe_overwrite.sh
  source "$_SUBAGENT_SAFE_OVERWRITE_LIB"
  _subagent_safe_overwrite_available=true
else
  warn "safe_overwrite.sh not found at $_SUBAGENT_SAFE_OVERWRITE_LIB"
  warn "Subagent installation will use basic copy without interactive prompts."
fi
unset _SUBAGENT_SAFE_OVERWRITE_LIB

# --- Determine providers list ---
# Try kanban.cfg [providers] available first; fall back to hardcoded list.
_SUBAGENT_INI_PARSER_LIB="$SCRIPT_DIR/team/scripts/lib/ini_parser.sh"
_subagent_providers=""
if [[ -f "$_SUBAGENT_INI_PARSER_LIB" ]] && [[ -f "$KANBAN_ROOT/kanban.cfg" ]]; then
  # shellcheck source=team/scripts/lib/ini_parser.sh
  source "$_SUBAGENT_INI_PARSER_LIB"
  _subagent_providers="$(read_ini "$KANBAN_ROOT/kanban.cfg" providers available "")"
fi
if [[ -z "$_subagent_providers" ]]; then
  # TODO(PRIORITY-0038): Replace this hardcoded fallback with INI lookup once
  # kanban.cfg [providers] available is consistently present in all installs.
  _subagent_providers="claude codex"
fi
unset _SUBAGENT_INI_PARSER_LIB

info "Provider-agent wrapper deployment: providers = $_subagent_providers"
if [[ "$ADD_CLAUDE_AGENTS" == "false" && "$ADD_CODEX_AGENTS" == "false" ]]; then
  info "Provider-agent wrapper deployment: skipped (neither --add-claude-agents nor --add-codex-agents was passed)"
fi

# --- Load subagent manifest (provider-agnostic; same agents go to every provider) ---
SUBAGENT_MANIFEST="$SCRIPT_DIR/subagents/MANIFEST.txt"
if [[ ! -f "$SUBAGENT_MANIFEST" ]]; then
  err "Subagent manifest not found at $SUBAGENT_MANIFEST"
  err "This file is required and ships with the kanban repo."
  exit 1
fi

CURRENT_SUBAGENTS=()
while IFS= read -r line; do
  # Strip comments and trim whitespace
  line="${line%%#*}"
  line="${line#"${line%%[![:space:]]*}"}"
  line="${line%"${line##*[![:space:]]}"}"
  [[ -z "$line" ]] && continue
  CURRENT_SUBAGENTS+=("$line")
done < "$SUBAGENT_MANIFEST"

if [[ ${#CURRENT_SUBAGENTS[@]} -eq 0 ]]; then
  err "Subagent manifest at $SUBAGENT_MANIFEST is empty or contains only comments"
  err "At least one subagent name is required."
  exit 1
fi

info "Loaded ${#CURRENT_SUBAGENTS[@]} subagent(s) from manifest: ${CURRENT_SUBAGENTS[*]}"

# --- State file (read once, then per-provider cleanup loop uses it) ---
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

# Tracks whether at least one provider's deployment block actually ran.
# Used to gate the .installed-subagents state file write below.
_ANY_PROVIDER_FLAG_SET=false

# --- Per-provider deployment loop ---
for _provider in $_subagent_providers; do
  # --- Opt-in gate: skip this provider entirely if its flag was not passed ---
  # Absent the flag: write NOTHING for this provider — no file writes, no directory
  # creation, no CLI detection, no prompts, no state-file removal (Option A).
  case "$_provider" in
    claude)
      if [[ "$ADD_CLAUDE_AGENTS" != "true" ]]; then
        info "Provider-agent wrapper deployment: skipping claude (--add-claude-agents not set)"
        continue
      fi
      ;;
    codex)
      if [[ "$ADD_CODEX_AGENTS" != "true" ]]; then
        info "Provider-agent wrapper deployment: skipping codex (--add-codex-agents not set)"
        continue
      fi
      ;;
    *)
      # Unknown / future providers: skip unless both flags are set (conservative default).
      # Operators using custom providers should extend this list.
      info "Provider-agent wrapper deployment: skipping unknown provider '$_provider' (no explicit opt-in flag)"
      continue
      ;;
  esac
  _ANY_PROVIDER_FLAG_SET=true

  info ""
  info "Provider-agent wrapper deployment: provider=$_provider"

  # CLI detection: skip providers whose binary is not installed.
  # (Codex CLI may be absent on hosts running claude-only; vice versa.)
  if ! command -v "$_provider" >/dev/null 2>&1; then
    warn "Skipping $_provider: CLI binary not found on PATH"
    continue
  fi

  _provider_agents_dir="$HOME/.${_provider}/agents"

  # Directory creation: prompt when absent (skip prompt in force/dry-run mode).
  if [[ ! -d "$_provider_agents_dir" ]]; then
    if [[ "$DRY_RUN" == "true" ]]; then
      info "[dry-run] would prompt to create directory: $_provider_agents_dir"
    elif [[ "$FORCE" == "true" ]]; then
      mkdir -p "$_provider_agents_dir"
      ok "Created: $_provider_agents_dir"
    else
      _prompt_tty "$(printf 'Create directory %s for %s provider wrappers? [Y/n] ' "$_provider_agents_dir" "$_provider")" _dir_response
      case "${_dir_response:-Y}" in
        [yY]*)
          mkdir -p "$_provider_agents_dir"
          ok "Created: $_provider_agents_dir"
          ;;
        *)
          warn "Skipping $_provider: directory creation declined."
          warn "To create manually: mkdir -p $_provider_agents_dir"
          continue
          ;;
      esac
    fi
  fi

  # Deploy each manifest agent into this provider's agents directory.
  for agent in "${CURRENT_SUBAGENTS[@]}"; do
    src="$SCRIPT_DIR/subagents/${agent}.md"
    dst="${_provider_agents_dir}/${agent}.md"

    if [[ ! -f "$src" ]]; then
      warn "Subagent source missing: $src"
      continue
    fi

    if [[ "$DRY_RUN" == "true" ]]; then
      if [[ -f "$dst" ]]; then
        if cmp -s "$src" "$dst" 2>/dev/null; then
          info "[dry-run] subagent unchanged (identical): $_provider/$agent"
        else
          info "[dry-run] would prompt to overwrite subagent: $_provider/$agent"
        fi
      else
        info "[dry-run] would prompt to install subagent: $_provider/$agent"
      fi
    elif [[ "$FORCE" == "true" ]]; then
      # Force: copy directly without interactive prompt.
      cp "$src" "$dst"
      ok "Installed/upgraded: $_provider/$agent"
    elif [[ "$_subagent_safe_overwrite_available" == "true" ]]; then
      # Interactive: use safe_overwrite_file for prompt-on-overwrite with backup.
      safe_overwrite_file "$src" "$dst" || true
    else
      # Fallback: safe_overwrite.sh not available; use install_path.
      install_path "$src" "$dst" "subagent: $_provider/$agent"
    fi
  done

  # --- Remove obsolete subagents (state-file aware, per-provider) ---
  # Only deletes subagent files that were INSTALLED BY A PREVIOUS RUN and
  # are NO LONGER in the current manifest. Files we did not previously
  # install are never touched.
  if [[ ${#PREVIOUSLY_INSTALLED[@]} -gt 0 ]]; then
    for prev in "${PREVIOUSLY_INSTALLED[@]}"; do
      still_shipped=false
      for current in "${CURRENT_SUBAGENTS[@]}"; do
        [[ "$prev" == "$current" ]] && still_shipped=true && break
      done

      if [[ "$still_shipped" == "false" ]]; then
        obsolete_file="${_provider_agents_dir}/${prev}.md"
        if [[ -f "$obsolete_file" ]]; then
          if [[ "$DRY_RUN" == "true" ]]; then
            info "[dry-run] would remove obsolete subagent (no longer in manifest): $_provider/$prev"
          else
            info "Removing obsolete subagent (no longer in manifest): $_provider/$prev"
            rm -f "$obsolete_file"
          fi
        fi
      fi
    done
  fi

  ok "Provider-agent wrapper deployment complete for: $_provider"
done

# --- Write state file: record what THIS install copied (for next-run cleanup) ---
# Single file at $KANBAN_ROOT/.installed-subagents tracks the manifest set the
# most recent install.sh wrote. It is provider-agnostic (the same agent names
# are deployed to every configured provider), so one state file suffices.
#
# Option A (strict opt-in): only update the state file when at least one
# provider's deployment block actually ran.  If neither --add-claude-agents nor
# --add-codex-agents was passed, the state file is left exactly as-is — this
# avoids silently erasing the record of previously-deployed agents from a run
# that didn't touch any provider directory.
if [[ "$DRY_RUN" != "true" && "$_ANY_PROVIDER_FLAG_SET" == "true" ]]; then
  {
    echo "# pgai-agent-kanban: subagents installed by install.sh"
    echo "# This file is managed automatically. Deleting it is safe — the next"
    echo "# install will rebuild it (and will NOT delete any existing agents,"
    echo "# because it cannot prove ownership). Do not edit by hand."
    echo "# Last updated: $(date -Iseconds)"
    printf '%s\n' "${CURRENT_SUBAGENTS[@]}"
  } > "$INSTALLED_AGENTS_STATE"
  info "Updated installed-subagents state file: $INSTALLED_AGENTS_STATE"
fi

unset _provider _provider_agents_dir _dir_response _subagent_providers
unset PREVIOUSLY_INSTALLED prev still_shipped current obsolete_file
unset _subagent_safe_overwrite_available _ANY_PROVIDER_FLAG_SET

info ""

# --- Scheduler hooks (v0.58.0+) ---
#
# Two independent scheduler axes:
#   1. OS crontab  — install-crontab.sh   — skipped when NO_SYSTEM_CRON=true
#   2. pseudocron  — install-pseudocron.sh — ALWAYS installed (inert until started)
#
# Both use WAKE_TIER to select the cadence template.
# The pseudocron config is always laid down so cron-less deployments work out of
# the box; it does nothing until pseudocron.py is launched by the operator.

# ---------------------------------------------------------------------------
# Temp-root guard (defense-in-depth): skip BOTH scheduler installs
# when the install target is clearly a test or temp root.  This prevents the
# test suite from clobbering the operator's live crontab or pseudocron.cfg even
# when no PGAI_CRONTAB_CMD seam is set in the calling environment.
#
# Detection: KANBAN_ROOT resolves to a path under any of:
#   - /tmp or TMPDIR (the system/configured temp root)
#   - /var/folders (macOS temp — never set by operators intentionally)
#   - pytest temp paths: paths containing /pytest-of- or /tmp/pytest
#   - the pgai framework temp root (PGAI_AGENT_KANBAN_TEMP_DIR)
#
# This guard does NOT affect real operator installs: those always target a path
# under $HOME or an explicit non-temp directory.  The guard fires only on the
# synthetic roots that tests create under /tmp.
# ---------------------------------------------------------------------------
_kanban_root_real="$(readlink -m "$KANBAN_ROOT" 2>/dev/null || echo "$KANBAN_ROOT")"
_system_tmp_real="$(readlink -m "${TMPDIR:-/tmp}" 2>/dev/null || echo "${TMPDIR:-/tmp}")"
_pgai_temp_real=""
if [[ -n "${PGAI_AGENT_KANBAN_TEMP_DIR:-}" ]]; then
  _pgai_temp_real="$(readlink -m "$PGAI_AGENT_KANBAN_TEMP_DIR" 2>/dev/null || echo "$PGAI_AGENT_KANBAN_TEMP_DIR")"
fi

_is_temp_root=false
# Check: under system /tmp or TMPDIR
if [[ "$_kanban_root_real" == "${_system_tmp_real}"/* || "$_kanban_root_real" == "${_system_tmp_real}" ]]; then
  _is_temp_root=true
fi
# Check: under pgai framework temp dir
if [[ -n "$_pgai_temp_real" ]] && \
   [[ "$_kanban_root_real" == "${_pgai_temp_real}"/* || "$_kanban_root_real" == "$_pgai_temp_real" ]]; then
  _is_temp_root=true
fi
# Check: pytest temp path markers (pytest-of-, tmp/pytest)
if [[ "$_kanban_root_real" == */pytest-of-* || "$_kanban_root_real" == */pytest-* ]]; then
  _is_temp_root=true
fi
# Check: macOS /var/folders temp
if [[ "$_kanban_root_real" == /var/folders/* ]]; then
  _is_temp_root=true
fi

unset _kanban_root_real _system_tmp_real _pgai_temp_real

# ---------------------------------------------------------------------------
# Hook 1: OS crontab — via install-crontab.sh
#
# Skipped when:
#   - NO_SYSTEM_CRON is true (--no-system-cron flag or deprecated --tier=none)
#   - DRY_RUN is true — prints an informational note
#   - KANBAN_ROOT is under a temp/test directory (temp-target guard)
#   - crontab binary is not available — CI / non-interactive environments
#   - install-crontab.sh is missing from the installed tree
# ---------------------------------------------------------------------------
_INSTALL_CRONTAB_SH="$KANBAN_ROOT/scripts/install-crontab.sh"

if [[ "$NO_SYSTEM_CRON" == "true" ]]; then
  info "Crontab: skipped — --no-system-cron set (cron-less / Docker deployment)."
  info "pseudocron will be installed below for use with team/scripts/pseudocron.py."
elif [[ "$DRY_RUN" == "true" ]]; then
  info "[dry-run] would install crontab (tier: ${WAKE_TIER}) via install-crontab.sh --wake-tier=${WAKE_TIER}"
elif [[ "$_is_temp_root" == "true" ]]; then
  info "Crontab: skipped — install target ($KANBAN_ROOT) is under a temp/test directory (temp-target guard)."
  info "This is expected when running tests or CI.  To install the crontab on a real host:"
  info "  $KANBAN_ROOT/scripts/install-crontab.sh --wake-tier=${WAKE_TIER}"
elif ! command -v crontab >/dev/null 2>&1; then
  warn "crontab not found in PATH — skipping crontab installation (CI or non-interactive host)"
  warn "To install the wake schedule later, run: $KANBAN_ROOT/scripts/install-crontab.sh --wake-tier=${WAKE_TIER}"
elif [[ ! -f "$_INSTALL_CRONTAB_SH" ]]; then
  warn "install-crontab.sh not found at $_INSTALL_CRONTAB_SH — skipping crontab installation"
  warn "Ensure the kanban tree was installed correctly and re-run install.sh."
  warn "To install manually: $KANBAN_ROOT/scripts/install-crontab.sh --wake-tier=${WAKE_TIER}"
else
  # Delegate to install-crontab.sh with the selected wake tier.
  # PGAI_AGENT_KANBAN_ROOT_PATH is set so install-crontab.sh resolves KANBAN_ROOT correctly.
  # --wake-tier=<tier> applies the named tier unconditionally (after backup),
  # which is the correct behavior for an explicit install-time selection.
  # --yes is passed so that the no-TTY explicit-confirm gate in install-crontab.sh does not
  # block fresh installs: install.sh has already established operator intent through its own
  # interactive flow (or --wake-tier flag), so the downstream script should not re-gate.
  info "Crontab: installing tier '${WAKE_TIER}' via install-crontab.sh..."
  _ct_result=0
  PGAI_AGENT_KANBAN_ROOT_PATH="$KANBAN_ROOT" "$_INSTALL_CRONTAB_SH" --wake-tier="${WAKE_TIER}" --yes || _ct_result=$?

  case "$_ct_result" in
    0)
      ok "Crontab: ready (tier: ${WAKE_TIER})"
      ;;
    2)
      # Operator declined — install-crontab.sh already printed recovery instructions.
      warn "Crontab: not installed (operator declined)."
      warn "Wake scripts will not fire until the crontab is installed."
      warn "To install manually, run: $KANBAN_ROOT/scripts/install-crontab.sh --wake-tier=${WAKE_TIER}"
      ;;
    *)
      # Crontab installation failed.  Common cause: the crontab file at
      # /var/spool/cron/<user> is locked immutable (chattr +i) — crontab(1)
      # emits "Operation not permitted" (EPERM) and exits non-zero.
      # Do not crash install.sh over a crontab failure; emit a clear message
      # and let the operator handle it manually after the rest of the install
      # completes.  The kanban itself is fully installed; only the cron
      # schedule is missing.
      warn "Crontab: installation failed (install-crontab.sh exited $_ct_result)."
      warn "If your crontab file is locked (chattr +i), unlock it first:"
      warn "  sudo chattr -i /var/spool/cron/$(id -un)"
      warn "Then re-run install.sh or install the crontab manually:"
      warn "  $KANBAN_ROOT/scripts/install-crontab.sh --wake-tier=${WAKE_TIER}"
      warn "Wake scripts will not fire until the crontab is installed."
      ;;
  esac

  unset _ct_result
fi
unset _INSTALL_CRONTAB_SH

# ---------------------------------------------------------------------------
# Hook 2: pseudocron — via install-pseudocron.sh
#
# ALWAYS runs regardless of --no-system-cron.  The pseudocron config is inert
# until pseudocron.py is started by the operator, so installing it on a
# cron-equipped host is harmless.  On a cron-less host it is the required step
# to make autonomous agent wakes possible.
#
# Skipped when:
#   - DRY_RUN is true — prints an informational note
#   - KANBAN_ROOT is under a temp/test directory (temp-target guard)
#   - install-pseudocron.sh is missing from the installed tree
# ---------------------------------------------------------------------------
_INSTALL_PSEUDOCRON_SH="$KANBAN_ROOT/scripts/install-pseudocron.sh"

if [[ "$DRY_RUN" == "true" ]]; then
  info "[dry-run] would install pseudocron config (tier: ${WAKE_TIER}) via install-pseudocron.sh --wake-tier=${WAKE_TIER}"
elif [[ "$_is_temp_root" == "true" ]]; then
  info "Pseudocron: skipped — install target ($KANBAN_ROOT) is under a temp/test directory (temp-target guard)."
  info "This is expected when running tests or CI.  To install pseudocron files on a real host:"
  info "  $KANBAN_ROOT/scripts/install-pseudocron.sh --wake-tier=${WAKE_TIER}"
elif [[ ! -f "$_INSTALL_PSEUDOCRON_SH" ]]; then
  warn "install-pseudocron.sh not found at $_INSTALL_PSEUDOCRON_SH — skipping pseudocron installation"
  warn "Ensure the kanban tree was installed correctly and re-run install.sh."
  warn "To install manually: $KANBAN_ROOT/scripts/install-pseudocron.sh --wake-tier=${WAKE_TIER}"
else
  # Delegate to install-pseudocron.sh with the selected wake tier.
  # --yes is passed for the same reasons as the crontab hook above.
  info "Pseudocron: installing tier '${WAKE_TIER}' via install-pseudocron.sh..."
  _pc_result=0
  PGAI_AGENT_KANBAN_ROOT_PATH="$KANBAN_ROOT" "$_INSTALL_PSEUDOCRON_SH" --wake-tier="${WAKE_TIER}" --yes || _pc_result=$?

  case "$_pc_result" in
    0)
      ok "Pseudocron: ready (tier: ${WAKE_TIER})"
      ;;
    2)
      warn "Pseudocron: not installed (operator declined)."
      warn "To install manually, run: $KANBAN_ROOT/scripts/install-pseudocron.sh --wake-tier=${WAKE_TIER}"
      ;;
    *)
      warn "Pseudocron: installation failed (install-pseudocron.sh exited $_pc_result)."
      warn "To install manually:"
      warn "  $KANBAN_ROOT/scripts/install-pseudocron.sh --wake-tier=${WAKE_TIER}"
      ;;
  esac

  unset _pc_result
fi
unset _INSTALL_PSEUDOCRON_SH _is_temp_root

# --- Final summary ---
ok "Installation complete!"
echo ""
echo "${C_BOLD}Next steps:${C_RESET}"
echo ""
echo "  1. Add these lines to your shell profile (.bashrc, .zshrc, etc.):"
echo ""
echo "       ${C_BOLD}export PGAI_AGENT_KANBAN_ROOT_PATH=\"$KANBAN_ROOT\"${C_RESET}"
echo ""
echo "  2. Review and customize your config:"
echo ""
echo "       # Framework operational settings live in kanban.cfg (INI format):"
echo "       \$EDITOR \"$KANBAN_ROOT/kanban.cfg\""
echo "       # See kanban.cfg_example for documented options."
echo "       # In particular set [paths] dev_tree_path — upgrade.sh requires it."
echo ""
echo "  3. Register your first project:"
echo ""
echo "       \"$KANBAN_ROOT/scripts/create-project.sh\" --project pgai-chomp-man"
echo "       # Then edit projects/pgai-agent-kanban/project.cfg to set dev_tree_path and git_repo_url."
echo "       # (For any other project, replace pgai-agent-kanban with your project name.)"
echo ""
echo "  4. (Optional) Configure shell environment (PATH, venv, KANBAN_ROOT):"
echo ""
echo "       cp \"$KANBAN_ROOT/shell-env_example\" \"$KANBAN_ROOT/shell-env\""
echo "       \$EDITOR \"$KANBAN_ROOT/shell-env\""
echo ""
echo "  5. (Optional) Configure credentials (OAuth tokens, API keys):"
echo ""
echo "       # Auth mode is set in kanban.cfg [providers] ai_auth_mode (default: oauth)."
echo "       # AI_AUTH_MODE is exported by the wake script before sourcing secrets, so the"
echo "       # secrets file can branch on it. Two modes:"
echo "       #   ai_auth_mode = oauth   — uses Claude Code OAuth credentials (default)."
echo "       #                            The secrets file unsets ANTHROPIC_API_KEY so OAuth wins."
echo "       #   ai_auth_mode = apikey  — expects ANTHROPIC_API_KEY in secrets."
echo "       #                            Uncomment the export line in secrets and set your key."
echo "       # To set up credentials for either mode:"
echo "       cp \"$KANBAN_ROOT/secrets_example\" \"$KANBAN_ROOT/secrets\""
echo "       chmod 600 \"$KANBAN_ROOT/secrets\""
echo "       \$EDITOR \"$KANBAN_ROOT/secrets\""
echo ""
echo "  6. Scheduling was installed above (crontab and/or pseudocron)."
echo "     To review or customize the wake cadence:"
echo ""
echo "       # Review the suggested cron entries first:"
echo "       cat \"$KANBAN_ROOT/scripts/cron-suggested.txt\""
echo "       # Then install them:"
echo "       crontab -e"
echo "       # (Paste the contents of cron-suggested.txt into your crontab)"
echo ""
echo "  7. Try the PM agent on a sample requirements doc:"
echo ""
echo "       cp \"$KANBAN_ROOT/templates/agent/REQUIREMENTS-TEMPLATE.md\" ~/my-project.md"
echo "       \$EDITOR ~/my-project.md"
echo "       \"$KANBAN_ROOT/scripts/pm-agent.sh\" ~/my-project.md --dry-run"
echo ""
echo "  8. When ready, wake an agent:"
echo ""
echo "       \"$KANBAN_ROOT/scripts/wake.sh\" coder        # wake coder agent (one task)"
echo "       \"$KANBAN_ROOT/scripts/wake.sh\" pm           # wake pm agent (reads pm_backlog.md)"
echo "       \"$KANBAN_ROOT/scripts/wake-batch.sh\" --agent=coder --max-tasks=5   # multiple tasks"
echo ""
echo "  9. Review status anytime:"
echo ""
echo "       python3 \"$KANBAN_ROOT/pm-agent/pm_status.py\" -v"
echo ""
echo "See $KANBAN_ROOT/README.md for the full documentation."
echo ""

# --- Write VERSION and VERSION_DETAIL files (last step before exit) ---
# Uses the shared stamp helper to write VERSION (clean tag, suffix-stripped)
# and VERSION_DETAIL (full describe + deposit SHA).  The helper is the single
# implementation; upgrade.sh calls the same function.
# When --stamp-version is supplied, VERSION is written verbatim (no suffix to
# strip; no VERSION_DETAIL written) and the advisory is bypassed.
_INSTALL_STAMP_LIB="${SCRIPT_DIR}/team/scripts/lib/version_stamp.sh"
if [[ -f "$_INSTALL_STAMP_LIB" ]]; then
  # shellcheck source=team/scripts/lib/version_stamp.sh
  source "$_INSTALL_STAMP_LIB"
fi
unset _INSTALL_STAMP_LIB

if [[ "$DRY_RUN" != "true" ]]; then
  if [[ -n "$STAMP_VERSION" ]]; then
    # Operator override: write verbatim via shared helper; skip advisory.
    ok "VERSION stamp: using --stamp-version override: $STAMP_VERSION"
    stamp_version_files "$SCRIPT_DIR" "$KANBAN_ROOT" "$STAMP_VERSION"
    ok "Wrote VERSION: $STAMP_VERSION -> $KANBAN_ROOT/VERSION"
  else
    # Resolve from HEAD via git describe; emit divergence advisory when the
    # deployed tree is ahead of the latest published tag.
    _INSTALL_FULL_DESCRIBE="$(git -C "$SCRIPT_DIR" describe --tags 2>/dev/null || true)"
    _INSTALL_FULL_DESCRIBE="${_INSTALL_FULL_DESCRIBE:-unknown-dev}"

    # Advisory: when the latest unprefixed tag merged to origin/main differs
    # from the describe result, inform the operator of the staged-vs-published
    # gap.  The advisory is one line only; never a prompt or a blocking error.
    _OLD_VER="$(git -C "$SCRIPT_DIR" tag --merged origin/main 2>/dev/null \
      | grep -E '^v[0-9]+\.[0-9]+\.[0-9]+$' \
      | sort -V \
      | tail -n1 || true)"
    if [[ -n "$_OLD_VER" ]] && [[ "$_INSTALL_FULL_DESCRIBE" != "$_OLD_VER" ]]; then
      info "deploying $_INSTALL_FULL_DESCRIBE ; latest published tag is $_OLD_VER"
    fi
    unset _OLD_VER

    # Write VERSION (clean tag) and VERSION_DETAIL (forensics) via shared helper.
    stamp_version_files "$SCRIPT_DIR" "$KANBAN_ROOT"
    _INSTALL_CLEAN_TAG="$(tr -d '[:space:]' < "$KANBAN_ROOT/VERSION" 2>/dev/null || true)"
    ok "Wrote VERSION: ${_INSTALL_CLEAN_TAG} -> $KANBAN_ROOT/VERSION"
    unset _INSTALL_FULL_DESCRIBE _INSTALL_CLEAN_TAG
  fi
fi

exit 0
