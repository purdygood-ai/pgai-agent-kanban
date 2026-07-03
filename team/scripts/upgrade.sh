#!/usr/bin/env bash
# upgrade.sh — Upgrade an existing pgai-agent-kanban installation.
#
# This is the UPGRADE path only.  It performs the same code deposit as
# install.sh but preserves the PROTECTED SET (operator-owned state) and backs
# up the kanban root before touching anything.
#
# If the target is NOT an existing kanban installation, upgrade.sh refuses and
# directs the operator to install.sh instead.
#
# Usage:
#   upgrade.sh [OPTIONS]
#
# OPTIONS:
#   --force                Overwrite changed managed files without prompting.
#   --no-backup            Skip the pre-upgrade backup (advanced; not recommended).
#   --wake-tier small|medium|large|none
#                          Override the crontab cadence tier on upgrade.
#                          When OMITTED, the existing tier is detected and preserved.
#   --add-claude-agents    Deploy provider-agent wrappers to ~/.claude/agents/.
#   --add-codex-agents     Deploy provider-agent wrappers to ~/.codex/agents/.
#   --dev-tree PATH        Explicit path to dev tree (overrides kanban.cfg and env).
#   --help, -h             Show this help.
#
# DEPRECATED OPTIONS (still accepted; emit a deprecation notice to stderr):
#   --crontab-tier small|medium|large|none   Use --wake-tier instead.
#
# ENVIRONMENT:
#   PGAI_AGENT_KANBAN_ROOT_PATH   Kanban root (default: $HOME/pgai_agent_kanban)
#   PGAI_DEV_TREE_PATH            Dev tree path (required when not set in kanban.cfg)
#
# PROTECTED SET (declared once here; never overwritten on upgrade):
#   Config files:   kanban.cfg, projects.cfg, secrets, shell-env, pseudocron.env
#                   and each project's project.cfg
#   Projects dir:   projects/ (all per-project state: tasks, bugs, requirements,
#                   release-state, queues, release-notes)
#   Crontab:        preserved by default; overwritten only on explicit --wake-tier
#                   / --crontab-tier change
#   Agent wrappers: ~/.claude/agents/ and ~/.codex/agents/ (opt-in only; when
#                   --add-claude-agents / --add-codex-agents are NOT passed,
#                   the installed wrappers are left exactly as-is)
#   Example templates: *_example files are refreshed; the ACTIVE config they seed
#                      (e.g. kanban.cfg) is preserved
#   Runtime dirs:   logs/, locks/ — ensure-if-missing; never deleted
#
# BACKUP:
#   A tarball of the whole kanban root is created before any change, excluding
#   only volatile directories (logs/, locks/, __pycache__) and *.lock files.
#   The backup INCLUDES the secrets file (cleartext credentials) — keep backups
#   out of public artifacts; gitignore *-backup-*.tar.gz.
#   The tarball is chmod 600 on creation.

set -euo pipefail

# ---------------------------------------------------------------------------
# Resolve install location and source config helpers (BEFORE strict mode
# so that sourcing failures do not kill the script prematurely).
# ---------------------------------------------------------------------------
KANBAN_ROOT="${PGAI_AGENT_KANBAN_ROOT_PATH:-$HOME/pgai_agent_kanban}"

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
# upgrade.sh lives at team/scripts/; argparse.sh is at team/scripts/lib/.
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
WAKE_TIER=""          # empty = detect and preserve existing tier
ADD_CLAUDE_AGENTS=false
ADD_CODEX_AGENTS=false
BACKUP_PATH=""

# ---------------------------------------------------------------------------
# Cleanup handler — runs on EXIT (success or failure).
# Reports backup path on failure so the operator knows recovery is available.
# ---------------------------------------------------------------------------
_cleanup() {
  local exit_code=$?
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
_print_usage() {
  cat <<EOF
Usage: $(basename "$0") [OPTIONS]

Upgrade an existing pgai-agent-kanban installation.

This is the UPGRADE path only.  If the target is not an existing installation,
use install.sh instead.

OPTIONS:
  --force                 Overwrite changed managed files without prompting.
  --no-backup             Skip the pre-upgrade backup (advanced; not recommended).
  --wake-tier small|medium|large|none
                          Override the crontab cadence tier applied during upgrade.
                          When OMITTED, upgrade.sh detects and preserves the existing tier.
  --add-claude-agents     Deploy provider-agent wrappers to ~/.claude/agents/.
  --add-codex-agents      Deploy provider-agent wrappers to ~/.codex/agents/.
  --dev-tree PATH         Explicit path to dev tree (overrides env and kanban.cfg).
  --help, -h              Show this help.

DEPRECATED OPTIONS (still accepted; emit a deprecation notice to stderr):
  --crontab-tier small|medium|large|none   Use --wake-tier instead.

ENVIRONMENT:
  PGAI_AGENT_KANBAN_ROOT_PATH   Kanban root (default: \$HOME/pgai_agent_kanban)
  PGAI_DEV_TREE_PATH            Dev tree path (set via kanban.cfg or this env var)

EXAMPLES:
  $(basename "$0")                                      # upgrade, preserve tier
  $(basename "$0") --force                              # upgrade silently
  $(basename "$0") --wake-tier large                   # upgrade and change to large tier
  $(basename "$0") --wake-tier none                    # upgrade, skip crontab
  $(basename "$0") --add-claude-agents                 # upgrade and refresh claude wrappers
  $(basename "$0") --no-backup                         # upgrade without backup (advanced)
EOF
}

# ---------------------------------------------------------------------------
# Parse arguments
# ---------------------------------------------------------------------------
argparse_parse --value-flags "wake-tier crontab-tier dev-tree" -- "$@"

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

# Handle --help / -h.
if argparse_has "help" || argparse_has "h"; then
  _print_usage; exit 0
fi

# Handle short flags from ARGPARSE_POSITIONAL.
for _pos in "${ARGPARSE_POSITIONAL[@]+"${ARGPARSE_POSITIONAL[@]}"}"; do
  case "$_pos" in
    -h) _print_usage; exit 0 ;;
    -*)
      err "Unknown option: $_pos"
      err "Run '$(basename "$0") --help' for usage."
      exit 1
      ;;
    *)
      err "Unexpected positional argument: $_pos"
      err "Run '$(basename "$0") --help' for usage."
      exit 1
      ;;
  esac
done
unset _pos

# Reject unknown flags.
for _flag in "${!ARGPARSE_FLAGS[@]}"; do
  case "$_flag" in
    force|no-backup|add-claude-agents|add-codex-agents|dev-tree|wake-tier|crontab-tier|help|h) ;;
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
# Detect the installed crontab tier (when --wake-tier was NOT supplied).
#
# Detection order:
#   1. "# Tier: <x>" header line in `crontab -l`.
#   2. Body-match against tier templates in $KANBAN_ROOT/templates/install/.
#   3. Crontab exists but tier undetectable → fall back to "small".
#   4. crontab not on PATH or no crontab installed → "none".
# ---------------------------------------------------------------------------
_detect_installed_tier() {
  if ! command -v crontab >/dev/null 2>&1; then
    echo "none"; return 0
  fi

  local _raw
  _raw="$(crontab -l 2>/dev/null)" || true
  if [[ -z "$_raw" ]]; then
    echo "none"; return 0
  fi

  # Strategy 1: canonical "# Tier: <x>" header.
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

  # Strategy 2: match installed crontab body against tier templates.
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
        printf '%s\n' "$_iline" | grep -qF -- "$_agent_tok" || continue
        if [[ -n "$_sleep_tok" ]]; then
          printf '%s\n' "$_iline" | grep -qF -- "$_sleep_tok" || continue
        fi
        _found=true; break
      done <<< "$_raw"

      if [[ "$_found" != "true" ]]; then _all_ok=false; break; fi
    done < "$_tmpl"

    if [[ "$_all_ok" == "true" && "$_total" -gt 0 ]]; then
      echo "$_tier"; return 0
    fi
  done

  # Strategy 3: crontab exists but tier is unidentifiable → safe fallback.
  echo "small"; return 0
}

if [[ -z "$WAKE_TIER" ]]; then
  info "Detecting installed crontab tier (--wake-tier not supplied)..."
  WAKE_TIER="$(_detect_installed_tier)"
  info "Detected installed tier: ${WAKE_TIER} (will preserve)"
fi

# ---------------------------------------------------------------------------
# DEPOSIT — same tree as install.sh, protected items skipped.
#
# Helper: deposit_item <src-path> <kanban-root-relative-path> <label>
#   - Skips items that are in the protected set.
#   - Skips identical files silently.
#   - With FORCE_UPGRADE=true: overwrites non-protected items without prompting.
#   - Without force and with a TTY: prompts [y]es/[N]o/[a]ll/[q]uit.
#   - Without force and without a TTY: skips with a message.
# ---------------------------------------------------------------------------
_DEPOSIT_BULK_CHOICE=""

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

  # Bulk-quit: operator chose [q]uit earlier in this run.
  if [[ "$_DEPOSIT_BULK_CHOICE" == "quit" ]]; then
    warn "Skipped (quit): $label"
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
      return 0
    fi

    if [[ "$_DEPOSIT_BULK_CHOICE" != "all" ]]; then
      local _answer=""
      printf '  %s already exists. Overwrite? [y]es/[N]o/[a]ll/[q]uit: ' "$dst" > /dev/tty
      IFS= read -r _answer < /dev/tty || _answer=""
      case "$_answer" in
        [aA]*) _DEPOSIT_BULK_CHOICE="all" ;;
        [qQ]*) _DEPOSIT_BULK_CHOICE="quit"; warn "Skipped (quit): $label"; return 0 ;;
        [yY]*) : ;; # proceed
        *)     warn "Skipped: $label"; return 0 ;;
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

for item in README.md DIRECTIVES.md OVERVIEW.md SOP.md roles scripts pm-agent workflows pgai_agent_kanban halt_after templates demos; do
  src="${DEV_TREE}/team/$item"
  [[ -e "$src" ]] || { warn "Source missing (skipping): team/$item"; continue; }
  deposit_item "$src" "$item" "team/$item"
done

# Refresh example templates (active configs derived from these are preserved).
for example in kanban.cfg_example shell-env_example secrets_example projects.cfg_example project.cfg_example; do
  src="${DEV_TREE}/$example"
  [[ -e "$src" ]] || continue
  deposit_item "$src" "$example" "$example"
done

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
# The deployed wrappers under ~/.claude/agents/ and ~/.codex/agents/ are in
# the protected set by default.  Passing --add-claude-agents / --add-codex-agents
# explicitly OPTs IN to refreshing those wrappers for the named provider.
# Without the flag, NOTHING is written under the provider directory.
# ---------------------------------------------------------------------------
_SUBAGENT_SAFE_OVERWRITE_LIB="$DEV_TREE/team/scripts/lib/safe_overwrite.sh"
_subagent_safe_overwrite_available=false
if [[ -f "$_SUBAGENT_SAFE_OVERWRITE_LIB" ]]; then
  # shellcheck source=team/scripts/lib/safe_overwrite.sh
  source "$_SUBAGENT_SAFE_OVERWRITE_LIB"
  _subagent_safe_overwrite_available=true
fi
unset _SUBAGENT_SAFE_OVERWRITE_LIB

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
info ""

for _provider in $_subagent_providers; do
  case "$_provider" in
    claude)
      if [[ "$ADD_CLAUDE_AGENTS" != "true" ]]; then
        info "Provider-agent wrappers: skipping claude (protected; pass --add-claude-agents to refresh)"
        continue
      fi
      ;;
    codex)
      if [[ "$ADD_CODEX_AGENTS" != "true" ]]; then
        info "Provider-agent wrappers: skipping codex (protected; pass --add-codex-agents to refresh)"
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
    continue
  fi

  _provider_agents_dir="$HOME/.${_provider}/agents"

  if [[ ! -d "$_provider_agents_dir" ]]; then
    if [[ "$FORCE_UPGRADE" == "true" ]]; then
      mkdir -p "$_provider_agents_dir"
      ok "Created: $_provider_agents_dir"
    else
      warn "Provider agents directory missing: $_provider_agents_dir"
      warn "To create and deploy, re-run with --force or create manually:"
      warn "  mkdir -p $_provider_agents_dir"
      continue
    fi
  fi

  for agent in "${CURRENT_SUBAGENTS[@]}"; do
    src="$DEV_TREE/subagents/${agent}.md"
    dst="${_provider_agents_dir}/${agent}.md"
    [[ -f "$src" ]] || { warn "Subagent source missing: $src"; continue; }

    if [[ "$FORCE_UPGRADE" == "true" ]]; then
      cp "$src" "$dst"
      ok "Upgraded: $_provider/$agent"
    elif [[ "$_subagent_safe_overwrite_available" == "true" ]]; then
      safe_overwrite_file "$src" "$dst" || true
    else
      deposit_item "$src" ".${_provider}/agents/${agent}.md" "subagent: $_provider/$agent"
    fi
  done

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

unset _provider _provider_agents_dir _subagent_providers
unset PREVIOUSLY_INSTALLED prev still_shipped current obsolete_file
unset _subagent_safe_overwrite_available _ANY_PROVIDER_FLAG_SET

# ---------------------------------------------------------------------------
# Scheduler hooks: crontab and pseudocron.
#
# Crontab: only touched when operator passed --wake-tier / --crontab-tier
#          (explicit change intent).  When WAKE_TIER was detected from the
#          existing install, we still apply it via install-crontab.sh to
#          ensure the latest tier template is used.  This matches the legacy
#          upgrade.sh behavior and is safe because the tier value is the
#          preserved one.
#
# Pseudocron: always installed (inert until pseudocron.py is started).
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

# Crontab hook.
_INSTALL_CRONTAB_SH="$KANBAN_ROOT/scripts/install-crontab.sh"
if [[ "$WAKE_TIER" == "none" ]]; then
  info "Crontab: skipped (tier=none — cron-less / Docker deployment)."
elif [[ "$_is_temp_root" == "true" ]]; then
  info "Crontab: skipped — target is under a temp/test directory (temp-target guard)."
elif ! command -v crontab >/dev/null 2>&1; then
  warn "Crontab: crontab not found in PATH — skipping crontab update."
elif [[ ! -f "$_INSTALL_CRONTAB_SH" ]]; then
  warn "Crontab: install-crontab.sh not found — skipping crontab update."
  warn "To install manually: $KANBAN_ROOT/scripts/install-crontab.sh --wake-tier=${WAKE_TIER}"
else
  info "Crontab: applying tier '${WAKE_TIER}' via install-crontab.sh..."
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
if [[ "$_is_temp_root" == "true" ]]; then
  info "Pseudocron: skipped — target is under a temp/test directory (temp-target guard)."
elif [[ ! -f "$_INSTALL_PSEUDOCRON_SH" ]]; then
  warn "Pseudocron: install-pseudocron.sh not found — skipping."
  warn "To install manually: $KANBAN_ROOT/scripts/install-pseudocron.sh --wake-tier=${WAKE_TIER}"
else
  info "Pseudocron: installing tier '${WAKE_TIER}' via install-pseudocron.sh..."
  _pc_result=0
  PGAI_AGENT_KANBAN_ROOT_PATH="$KANBAN_ROOT" "$_INSTALL_PSEUDOCRON_SH" \
    --wake-tier="${WAKE_TIER}" --yes || _pc_result=$?
  case "$_pc_result" in
    0) ok "Pseudocron: ready (tier: ${WAKE_TIER})" ;;
    2) warn "Pseudocron: not installed (operator declined)." ;;
    *)
      warn "Pseudocron: installation failed (exit $_pc_result)."
      warn "To install manually: $KANBAN_ROOT/scripts/install-pseudocron.sh --wake-tier=${WAKE_TIER}"
      ;;
  esac
  unset _pc_result
fi
unset _INSTALL_PSEUDOCRON_SH _is_temp_root

# ---------------------------------------------------------------------------
# Write VERSION file.
# ---------------------------------------------------------------------------
# Best-effort: determine the latest released tag from the dev tree using the
# shared reachability-independent resolver.  Do NOT fail the upgrade if
# version resolution fails.
#
# We source team/scripts/dashboard/lib/version.sh and call
# get_latest_released_tag() DIRECTLY — never get_kanban_version(), which
# short-circuits on the existing $KANBAN_ROOT/VERSION (Tier 1) and would echo
# the currently-installed stamp rather than the target tag.  The entire point
# of the upgrade path is to stamp the target (latest released) tag, not to
# echo back what is already installed.
#
# get_latest_released_tag() uses:
#   git -C <repo_root> tag --merged origin/main | sort -V | tail -1
# This is reachability-independent: the result is the same regardless of which
# branch the dev tree is checked out on.
if [[ -d "$DEV_TREE/.git" ]]; then
  _VER_SH="$DEV_TREE/team/scripts/dashboard/lib/version.sh"
  if [[ -f "$_VER_SH" ]]; then
    # shellcheck source=team/scripts/dashboard/lib/version.sh
    source "$_VER_SH"
    _cur_version="$(get_latest_released_tag "$DEV_TREE" || true)"
  fi
  _cur_version="${_cur_version:-unknown-dev}"
  unset _VER_SH
  ok "Target tag resolved: $_cur_version"
  printf '%s\n' "$_cur_version" > "$KANBAN_ROOT/VERSION"
  ok "VERSION updated: $_cur_version"
  unset _cur_version
fi

# ---------------------------------------------------------------------------
# Success summary
# ---------------------------------------------------------------------------
echo ""
ok "Upgrade complete!"
echo ""
echo "${C_BOLD}Summary:${C_RESET}"
echo "  Kanban root:       $KANBAN_ROOT"
if [[ -n "$BACKUP_PATH" && -f "$BACKUP_PATH" ]]; then
  echo "  Backup tarball:    $BACKUP_PATH  (chmod 600; includes secrets)"
fi
echo "  Dev tree:          $DEV_TREE"
echo "  Wake tier:         ${WAKE_TIER}"
echo ""
echo "${C_BOLD}Protected (preserved):${C_RESET}"
echo "  kanban.cfg, projects.cfg, secrets, shell-env, pseudocron.env"
echo "  projects/ directory (all per-project state)"
echo "  crontab tier (detected and re-applied: ${WAKE_TIER})"
echo "  agent wrappers (pass --add-claude-agents / --add-codex-agents to refresh)"
echo ""
echo "See $KANBAN_ROOT/README.md for post-upgrade notes."
echo ""

exit 0
