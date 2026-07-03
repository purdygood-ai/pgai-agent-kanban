#!/usr/bin/env bash
# install-pseudocron.sh
# Install or update the pseudocron schedule files in the kanban root.
#
# What this script does:
#   1. Accepts a wake tier: small, medium, or large.
#      - small:  every-5-minutes schedule, low-resource hosts (default)
#      - medium: every-2-minutes with sub-minute stagger collapsed to minute granularity
#      - large:  every-minute schedule, 8+ core hosts
#   2. Loads the corresponding template from team/templates/install/pseudocron-{tier}.cfg.example.
#   3. Substitutes the __KANBAN_ROOT__ placeholder with the resolved kanban root path.
#   4. Writes pseudocron.cfg to the kanban root (idempotent: skip if identical).
#   5. Writes pseudocron.env to the kanban root from the example, if absent.
#
# NOTE on default tier:
#   When --wake-tier is not supplied, the tier defaults to "small".  This is
#   the most conservative choice and avoids resource contention on unknown
#   hardware.  Operators who know their hardware should pass --wake-tier explicitly.
#
# NOTE on idempotency:
#   Running this script twice with the same --wake-tier produces the same files as
#   running it once.  When the resolved content is identical to what is on disk,
#   the write is skipped silently.
#
# NOTE on pseudocron.env:
#   pseudocron.env is written only if it does not already exist.  An existing
#   pseudocron.env is NEVER overwritten — it may contain operator customizations.
#   To reset it, delete the existing file and re-run this script.
#
# Requirements:
#   - team/scripts/lib/safe_overwrite.sh must be present.
#   - team/scripts/lib/temp.sh must be present.
#   - team/templates/install/pseudocron-small.cfg.example, pseudocron-medium.cfg.example,
#     and pseudocron-large.cfg.example must exist in the dev tree.
#   - team/scripts/pseudocron.env.example must exist in the dev tree.
#
# Usage:
#   team/scripts/install-pseudocron.sh [--wake-tier small|medium|large] [--dry-run] [--yes] [--help]
#
# Options:
#   --wake-tier small|medium|large
#                          Select the pseudocron schedule tier.  Determines which
#                          template is written as pseudocron.cfg.  Default: small.
#                          Also accepted as --wake-tier=small|medium|large.
#   --dry-run              Show the actions that would be taken without writing any files.
#   --yes                  Skip the "Install pseudocron files?" confirmation prompt.
#                          Required for non-interactive (no-TTY) runs; without it a
#                          no-TTY invocation exits with code 2.
#   --help                 Print this usage and exit 0.

set -euo pipefail

# ---------------------------------------------------------------------------
# Locate this script's directory so we can find lib/ and templates/install/ relative
# to the dev tree, regardless of where the operator invokes it from.
# ---------------------------------------------------------------------------
_SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
_SAFE_OVERWRITE_LIB="${_SCRIPT_DIR}/lib/safe_overwrite.sh"
_TEMP_SH="${_SCRIPT_DIR}/lib/temp.sh"
_ARGPARSE_LIB="${_SCRIPT_DIR}/lib/argparse.sh"
_TEMPLATES_DIR="$(cd "${_SCRIPT_DIR}/../templates/install" 2>/dev/null && pwd)" || _TEMPLATES_DIR="${_SCRIPT_DIR}/../templates/install"
_ENV_EXAMPLE="${_SCRIPT_DIR}/pseudocron.env.example"

# ---------------------------------------------------------------------------
# Source the argparse library early — needed before flag processing.
# ---------------------------------------------------------------------------
if [[ ! -f "$_ARGPARSE_LIB" ]]; then
  echo "[install-pseudocron] ERROR: argparse.sh not found: $_ARGPARSE_LIB" >&2
  exit 1
fi
# shellcheck source=team/scripts/lib/argparse.sh
source "$_ARGPARSE_LIB"
unset _ARGPARSE_LIB

# ---------------------------------------------------------------------------
# Color helpers (only when stdout is a terminal)
# ---------------------------------------------------------------------------
if [[ -t 1 ]]; then
  C_RED=$'\033[0;31m'
  C_GREEN=$'\033[0;32m'
  C_YELLOW=$'\033[0;33m'
  C_BLUE=$'\033[0;34m'
  C_RESET=$'\033[0m'
else
  C_RED=""; C_GREEN=""; C_YELLOW=""; C_BLUE=""; C_RESET=""
fi

info()  { echo "${C_BLUE}[install-pseudocron]${C_RESET} $*"; }
ok()    { echo "${C_GREEN}[install-pseudocron]${C_RESET} $*"; }
warn()  { echo "${C_YELLOW}[install-pseudocron]${C_RESET} WARNING: $*"; }
err()   { echo "${C_RED}[install-pseudocron]${C_RESET} ERROR: $*" >&2; }
die()   { err "$*"; exit 1; }

# ---------------------------------------------------------------------------
# Argument parsing
# ---------------------------------------------------------------------------
DRY_RUN=false
# WAKE_TIER_ARG: non-empty when the operator supplied --wake-tier=X on the CLI.
# Empty means use the default ("small").
WAKE_TIER_ARG=""
# YES_FLAG: set to true when --yes is supplied.
YES_FLAG=false

_print_usage() {
  cat >&2 <<USAGE
Usage: $(basename "$0") [--wake-tier small|medium|large] [--dry-run] [--yes] [--help]

Install or update pseudocron.cfg and pseudocron.env in the kanban root.

Tiers:
  small   Every 5 minutes per agent, integer-minute offsets.
          1 core / <=8 GB RAM, shared or low-resource hosts. (DEFAULT)
  medium  Every 2 minutes, with sub-minute sleep stagger collapsed to minute
          granularity (pseudocron has no sub-minute scheduling).
          2-4 cores / 8-16 GB RAM.
  large   Every minute, all agents fire each minute.
          8+ cores / 16+ GB RAM.

Options:
  --wake-tier small|medium|large
                         Select the pseudocron schedule tier.  Determines which
                         template file is written as pseudocron.cfg in the kanban root.
                         Default: small. Also accepted as --wake-tier=small|medium|large.
  --dry-run              Show the planned actions without writing any files.
  --yes                  Skip the "Install pseudocron files?" confirmation prompt
                         regardless of TTY presence.  Required for non-interactive
                         (no-TTY) runs; without it, a no-TTY invocation exits with
                         code 2 to prevent silent overwrites.
  --help                 Print this usage and exit 0.

Files written to the kanban root:
  pseudocron.cfg   Pseudocron schedule derived from the chosen tier template.
                   __KANBAN_ROOT__ is substituted with the resolved kanban root path.
                   Skipped silently if the file already exists with identical content.
                   Backed up and overwritten when content differs (with --wake-tier explicit).
  pseudocron.env   Pre-filled environment variable file consumed by pseudocron.py.
                   Written from team/scripts/pseudocron.env.example only if absent.
                   Existing pseudocron.env is NEVER overwritten (operator customizations).

Idempotency:
  Running this script twice with the same --wake-tier produces the same files.
  Identical-content writes are silent no-ops.

Environment:
  PGAI_AGENT_KANBAN_ROOT_PATH    Kanban root — canonical name
                                  Default (fresh install): \$HOME/pgai_agent_kanban
USAGE
}

# ---------------------------------------------------------------------------
# Parse arguments via the shared argparse library.
# Value-taking flags: wake-tier.
# Boolean flags: dry-run, yes, non-interactive, help, h.
# ---------------------------------------------------------------------------
argparse_parse --value-flags "wake-tier" -- "$@"

# Emit a clear error when a value-taking flag is given without a value.
if argparse_missing "wake-tier"; then
  err "--wake-tier requires a value (small, medium, or large)."
  exit 1
fi

# Short flags (-h) arrive in ARGPARSE_POSITIONAL; handle them first.
for _pos in "${ARGPARSE_POSITIONAL[@]+"${ARGPARSE_POSITIONAL[@]}"}"; do
  case "$_pos" in
    -h) _print_usage; exit 0 ;;
    *)
      err "Unknown argument: $_pos"
      _print_usage
      exit 1
      ;;
  esac
done
unset _pos

# Reject unknown flags.
for _flag in "${!ARGPARSE_FLAGS[@]}"; do
  case "$_flag" in
    dry-run|wake-tier|yes|non-interactive|help) ;;
    *)
      err "Unknown argument: --${_flag}"
      _print_usage
      exit 1
      ;;
  esac
done
unset _flag

# Handle --help.
if argparse_has "help"; then
  _print_usage; exit 0
fi

# Extract boolean flags.
if argparse_has "dry-run";                               then DRY_RUN=true; fi
if argparse_has "yes" || argparse_has "non-interactive"; then YES_FLAG=true; fi

# Extract canonical value flag.
if argparse_has "wake-tier"; then
  WAKE_TIER_ARG="${ARGPARSE_FLAGS[wake-tier]}"
fi

# Validate --wake-tier if supplied.
if [[ -n "$WAKE_TIER_ARG" ]]; then
  case "$WAKE_TIER_ARG" in
    small|medium|large) ;;
    *)
      die "Invalid wake tier '$WAKE_TIER_ARG'. Choose: small, medium, or large."
      ;;
  esac
fi

# Apply default tier when none was supplied.
SELECTED_TIER="${WAKE_TIER_ARG:-small}"

# ---------------------------------------------------------------------------
# Source the safe_overwrite library (provides _prompt_tty, safe_overwrite_file).
# ---------------------------------------------------------------------------
if [[ ! -f "$_SAFE_OVERWRITE_LIB" ]]; then
  die "safe_overwrite.sh not found: $_SAFE_OVERWRITE_LIB"
fi
# shellcheck source=team/scripts/lib/safe_overwrite.sh
source "$_SAFE_OVERWRITE_LIB"

if [[ ! -f "$_TEMP_SH" ]]; then
  die "temp.sh not found: $_TEMP_SH"
fi
# shellcheck source=team/scripts/lib/temp.sh
source "$_TEMP_SH"
unset _TEMP_SH

# ---------------------------------------------------------------------------
# Resolve kanban root
#
# Env var precedence:
#   1. PGAI_AGENT_KANBAN_ROOT_PATH          (canonical env var override)
#   2. $HOME/pgai_agent_kanban               (canonical default)
# ---------------------------------------------------------------------------
_KANBAN_ROOT_ENV="${PGAI_AGENT_KANBAN_ROOT_PATH:-}"
if [[ -n "$_KANBAN_ROOT_ENV" ]]; then
  KANBAN_ROOT="$_KANBAN_ROOT_ENV"
else
  KANBAN_ROOT="$HOME/pgai_agent_kanban"
fi
unset _KANBAN_ROOT_ENV

# Validate kanban root
if [[ ! -d "$KANBAN_ROOT" ]]; then
  die "Kanban root not found: $KANBAN_ROOT
  Set PGAI_AGENT_KANBAN_ROOT_PATH or install to the default location."
fi

# Resolve absolute path for kanban root (for placeholder substitution).
KANBAN_ROOT_ABS="$(readlink -f "$KANBAN_ROOT")"

info "Kanban root: $KANBAN_ROOT_ABS"

# ---------------------------------------------------------------------------
# Pre-flight: "Install pseudocron files?" yes/no
#
# Evaluation order (mirrors install-crontab.sh logic):
#   1. --yes supplied → consent already given; proceed regardless of TTY.
#   2. No TTY and no --yes → decline with exit code 2.
#   3. TTY present and no --yes → prompt the operator interactively.
# ---------------------------------------------------------------------------
_preflight_tty_ok=false
if exec 3<>/dev/tty 2>/dev/null; then
    exec 3>&-
    _preflight_tty_ok=true
fi

if [[ "$YES_FLAG" == "true" ]]; then
    info "Confirmed via --yes: proceeding with pseudocron file installation."
elif [[ "$_preflight_tty_ok" == "false" ]]; then
    err "No TTY detected and --yes not supplied."
    err "Non-interactive pseudocron installs require the --yes flag to prevent"
    err "silent overwrites.  Re-run with --yes when you intend a non-interactive install."
    err "Example: $(basename "$0") --wake-tier large --yes"
    exit 2
else
    _install_response=""
    _prompt_tty 'Install or update pseudocron files? [Y/n] ' _install_response
    case "${_install_response:-Y}" in
      [yY]*)
        ;;  # continue
      *)
        info "Pseudocron installation declined."
        info "To install manually, re-run: $(basename "$0")"
        exit 0
        ;;
    esac
    unset _install_response
fi
unset _preflight_tty_ok

info "Selected tier: $SELECTED_TIER"

# ---------------------------------------------------------------------------
# Validate the pseudocron.env example source before proceeding.
# ---------------------------------------------------------------------------
if [[ ! -f "$_ENV_EXAMPLE" ]]; then
  die "pseudocron.env.example not found: $_ENV_EXAMPLE"
fi

# ---------------------------------------------------------------------------
# Locate the tier template
# ---------------------------------------------------------------------------
TIER_TEMPLATE="${_TEMPLATES_DIR}/pseudocron-${SELECTED_TIER}.cfg.example"
if [[ ! -f "$TIER_TEMPLATE" ]]; then
  die "Pseudocron tier template not found for tier '$SELECTED_TIER': $TIER_TEMPLATE"
fi

# ---------------------------------------------------------------------------
# Resolve output paths
# ---------------------------------------------------------------------------
PSEUDOCRON_CFG="${KANBAN_ROOT_ABS}/pseudocron.cfg"
PSEUDOCRON_ENV="${KANBAN_ROOT_ABS}/pseudocron.env"

# ---------------------------------------------------------------------------
# Substitute __KANBAN_ROOT__ placeholder into a temp file.
# The temp file is removed on EXIT (success or failure).
# pgai_mktemp routes under the resolved framework temp root.
# ---------------------------------------------------------------------------
_RESOLVED_CFG="$(pgai_mktemp pseudocron_cfg_resolved)"
trap 'rm -f "$_RESOLVED_CFG"' EXIT

if ! sed -e "s|__KANBAN_ROOT__|${KANBAN_ROOT_ABS}|g" \
     "$TIER_TEMPLATE" > "$_RESOLVED_CFG"; then
  die "Failed to substitute placeholders in template: $TIER_TEMPLATE"
fi

# Sanity check: no unresolved placeholder tokens remain.
if grep -qE '__KANBAN_ROOT__' "$_RESOLVED_CFG" 2>/dev/null; then
  die "Resolved template still contains __KANBAN_ROOT__ tokens — substitution failed."
fi

# ---------------------------------------------------------------------------
# Dry-run: print the planned actions and exit.
# ---------------------------------------------------------------------------
if [[ "$DRY_RUN" == "true" ]]; then
  echo ""
  info "=== DRY RUN: actions that would be performed (tier: $SELECTED_TIER) ==="
  echo ""
  info "  [1] Write pseudocron.cfg -> $PSEUDOCRON_CFG"
  info "      Source template: $TIER_TEMPLATE"
  info "      Substitution: __KANBAN_ROOT__ -> $KANBAN_ROOT_ABS"
  if [[ -f "$PSEUDOCRON_CFG" ]]; then
    if cmp -s "$_RESOLVED_CFG" "$PSEUDOCRON_CFG"; then
      info "      Status: content is already identical — would be a no-op."
    else
      info "      Status: content differs — would be backed up and overwritten."
    fi
  else
    info "      Status: file does not exist — would be installed."
  fi
  echo ""
  info "  [2] Write pseudocron.env -> $PSEUDOCRON_ENV"
  if [[ -f "$PSEUDOCRON_ENV" ]]; then
    info "      Status: already exists — would be left untouched (idempotent)."
  else
    info "      Source: $_ENV_EXAMPLE"
    info "      Status: file does not exist — would be installed from example."
  fi
  echo ""
  info "=== Resolved pseudocron.cfg content (tier: $SELECTED_TIER) ==="
  echo ""
  cat "$_RESOLVED_CFG"
  echo ""
  info "=== end dry run ==="
  echo ""
  info "Run without --dry-run to apply."
  exit 0
fi

# ---------------------------------------------------------------------------
# Write pseudocron.cfg.
#
# When --wake-tier is explicitly supplied by the operator, treat this as an
# unconditional-overwrite intent (mirrors install-crontab.sh --tier behavior):
# back up the existing file and write the new one without prompting.
# When --wake-tier is NOT supplied (interactive default-small case), route
# through safe_overwrite_file which will prompt the operator before overwriting
# a changed file.
# ---------------------------------------------------------------------------
info "Writing pseudocron.cfg (tier: $SELECTED_TIER)..."

if [[ -n "$WAKE_TIER_ARG" && -f "$PSEUDOCRON_CFG" ]]; then
  # Explicit tier + file exists: force-overwrite with backup (skip prompt).
  if cmp -s "$_RESOLVED_CFG" "$PSEUDOCRON_CFG"; then
    info "pseudocron.cfg already up to date (identical content) — skipping write."
  else
    _bak="${HOME}/.pseudocron.cfg.before-install-$(date +%Y%m%d-%H%M%S).bak"
    cp "$PSEUDOCRON_CFG" "$_bak" || die "Failed to back up existing pseudocron.cfg to $_bak"
    cp "$_RESOLVED_CFG" "$PSEUDOCRON_CFG" || die "Failed to write pseudocron.cfg"
    ok "pseudocron.cfg updated (tier: $SELECTED_TIER, backup: $_bak)."
    unset _bak
  fi
else
  # No explicit tier (default small) or file does not exist yet:
  # let safe_overwrite_file prompt the operator if content differs.
  _cfg_result=0
  safe_overwrite_file "$_RESOLVED_CFG" "$PSEUDOCRON_CFG" || _cfg_result=$?
  case "$_cfg_result" in
    0)
      ok "pseudocron.cfg installed/updated (tier: $SELECTED_TIER)."
      ;;
    2)
      warn "pseudocron.cfg not installed (operator declined)."
      warn "To install manually (tier: $SELECTED_TIER):"
      warn "  sed \"s|__KANBAN_ROOT__|${KANBAN_ROOT_ABS}|g\" \\"
      warn "    ${TIER_TEMPLATE} > ${PSEUDOCRON_CFG}"
      ;;
    *)
      die "pseudocron.cfg installation failed (safe_overwrite_file exited $_cfg_result)."
      ;;
  esac
fi

# ---------------------------------------------------------------------------
# Write pseudocron.env — only if it does not already exist (idempotent).
# If it already exists with any content, leave it untouched: the operator
# may have customized it.  To reset, delete the file and re-run.
# ---------------------------------------------------------------------------
info "Checking pseudocron.env..."

if [[ -f "$PSEUDOCRON_ENV" ]]; then
  info "pseudocron.env already exists at $PSEUDOCRON_ENV — leaving untouched."
else
  info "pseudocron.env not found — installing from example..."
  _env_result=0
  safe_overwrite_file "$_ENV_EXAMPLE" "$PSEUDOCRON_ENV" || _env_result=$?
  case "$_env_result" in
    0)
      ok "pseudocron.env installed at $PSEUDOCRON_ENV."
      ok "Edit it to fill in real values (PGAI_AGENT_KANBAN_ROOT_PATH, PATH, HOME, ANTHROPIC_API_KEY)."
      ;;
    2)
      warn "pseudocron.env not installed (operator declined)."
      warn "To install manually:"
      warn "  cp ${_ENV_EXAMPLE} ${PSEUDOCRON_ENV}"
      ;;
    *)
      die "pseudocron.env installation failed (safe_overwrite_file exited $_env_result)."
      ;;
  esac
fi

ok "Done.  Pseudocron files are in place."
info "Next steps:"
info "  1. Edit $PSEUDOCRON_ENV and fill in real values."
info "  2. Start pseudocron (it finds pseudocron.cfg/.env from PGAI_AGENT_KANBAN_ROOT_PATH;"
info "     it takes no flags — see docs/pseudocron.md):"
info "       python3 ${KANBAN_ROOT_ABS}/scripts/pseudocron.py \\"
info "           >> ${KANBAN_ROOT_ABS}/logs/pseudocron.log 2>&1"
