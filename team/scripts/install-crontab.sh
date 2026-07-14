#!/usr/bin/env bash
# install-crontab.sh
# Install or update the operator's crontab using a tier-appropriate schedule.
#
# What this script does:
#   1. Prompts the operator to select a wake tier: small, medium, or large.
#      - small:  every-5-minutes schedule, low-resource hosts (default)
#      - medium: every-2-minutes with sub-minute stagger, 2-4 core hosts
#      - large:  every-minute with pipeline-ordered stagger, 8+ core hosts
#      Default tier on Enter / no-TTY / timeout = small (see NOTE below).
#   2. Loads the corresponding template from team/templates/install/crontab-{tier}.example.
#   3. Substitutes __KANBAN_ROOT__ and __CLAUDE_PATH__ placeholders.
#   4. Passes the resolved template to safe_overwrite_crontab, which handles
#      backup, confirmation prompts, and atomic install.
#
# NOTE on default tier:
#   When no TTY is available (CI, cron, pipe) or when the operator presses
#   Enter without a value, the tier defaults to "small".  This is intentionally
#   the most conservative choice — it avoids resource contention on unknown
#   hardware.  Operators who know their hardware should always answer explicitly.
#
# NOTE on existing-crontab preservation:
#   Without --wake-tier: if a PGAI crontab already exists the replacement prompt
#   defaults to N (conservative).  The existing schedule is preserved unless
#   the operator explicitly answers Y.
#   With --wake-tier=<tier>: the named tier is applied unconditionally — any
#   existing PGAI (or non-PGAI) crontab entries are replaced after a backup.
#   Use --dry-run first if you want to preview the change.
#
# Idempotency:
#   Running this script twice with the same --wake-tier produces the same crontab
#   as running it once because safe_overwrite_crontab handles backup-then-write.
#
# Requirements:
#     or the kanban must be installed at the canonical default path
#     ($HOME/pgai_agent_kanban).
#   - team/scripts/lib/safe_overwrite.sh must be present (defines _prompt_tty,
#     safe_overwrite_crontab, warn_if_empty_crontab).
#   - team/templates/install/crontab-small.example, crontab-medium.example,
#     and crontab-large.example must exist in the dev tree.
#
# Usage:
#   team/scripts/install-crontab.sh [--dry-run] [--wake-tier small|medium|large] [--no-system-cron] [--yes] [--help]
#
# Options:
#   --dry-run              Show the substituted crontab that would be installed;
#                          do not modify anything.
#   --wake-tier small|medium|large
#                          Skip the interactive tier prompt and apply the selected
#                          tier unconditionally — existing PGAI (and non-PGAI)
#                          crontab entries are replaced after a backup.
#                          Without this flag, the replacement prompt defaults to N
#                          when a crontab already exists.
#                          Also accepted as --wake-tier=small|medium|large.
#   --no-system-cron       Skip crontab installation entirely — the OS crontab is
#                          left exactly as-is (no writes, no probes).  Use for
#                          cron-less / Docker / customer-site deployments where
#                          pseudocron.py drives the wake schedule instead.
#   --yes                  Explicit confirm — skip the "Install crontab?" prompt
#                          entirely, regardless of whether a TTY is present.
#                          Without this flag on a no-TTY run, the invocation
#                          declines with exit code 2 rather than silently
#                          defaulting to Y.  Required when calling from
#                          automated scripts (e.g. install.sh, CI pipelines,
#                          upgrade.sh --force, agent tasks).
#   --help                 Print this usage and exit 0.
#
# Deprecated flags (still accepted; emit a one-line deprecation notice to stderr):
#   --tier small|medium|large  Use --wake-tier <value> instead.
#   --tier=small|medium|large  Use --wake-tier=<value> instead.
#   --tier none                Use --no-system-cron instead.
#   --tier=none                Use --no-system-cron instead.

set -euo pipefail
# shellcheck source=lib/env_bootstrap.sh
source "$(dirname "${BASH_SOURCE[0]}")/lib/env_bootstrap.sh"

# ---------------------------------------------------------------------------
# Locate this script's directory so we can find lib/ and templates/install/ relative
# to the dev tree, regardless of where the operator invokes it from.
# ---------------------------------------------------------------------------
_SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
_SAFE_OVERWRITE_LIB="${_SCRIPT_DIR}/lib/safe_overwrite.sh"
_TEMP_SH="${_SCRIPT_DIR}/lib/temp.sh"
_ARGPARSE_LIB="${_SCRIPT_DIR}/lib/argparse.sh"
_TEMPLATES_DIR="$(cd "${_SCRIPT_DIR}/../templates/install" 2>/dev/null && pwd)" || _TEMPLATES_DIR="${_SCRIPT_DIR}/../templates/install"

# ---------------------------------------------------------------------------
# Source the argparse library early — needed before flag processing.
# ---------------------------------------------------------------------------
if [[ ! -f "$_ARGPARSE_LIB" ]]; then
  echo "[install-crontab] ERROR: argparse.sh not found: $_ARGPARSE_LIB" >&2
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

info()  { echo "${C_BLUE}[install-crontab]${C_RESET} $*"; }
ok()    { echo "${C_GREEN}[install-crontab]${C_RESET} $*"; }
warn()  { echo "${C_YELLOW}[install-crontab]${C_RESET} WARNING: $*"; }
err()   { echo "${C_RED}[install-crontab]${C_RESET} ERROR: $*" >&2; }
die()   { err "$*"; exit 1; }

# ---------------------------------------------------------------------------
# Argument parsing
# ---------------------------------------------------------------------------
DRY_RUN=false
# TIER_ARG: non-empty when the operator supplied --wake-tier=X (or deprecated --tier=X).
# Empty means "ask interactively" (or fall back to default on no-TTY).
TIER_ARG=""
# NO_SYSTEM_CRON: set to true when --no-system-cron (or deprecated --tier=none) is supplied.
# When true, the script exits immediately without touching the OS crontab.
NO_SYSTEM_CRON=false
# YES_FLAG: set to true when --yes (or --non-interactive) is supplied.
# Enables non-interactive (no-TTY) runs by setting PGAI_CRONTAB_CONFIRM=1
# and bypassing the pre-flight "Install crontab?" prompt.
YES_FLAG=false

_print_usage() {
  cat >&2 <<USAGE
Usage: $(basename "$0") [--dry-run] [--wake-tier small|medium|large] [--no-system-cron] [--yes] [--help]

Install or update the crontab to a tier-appropriate wake-batch.sh schedule.

Tiers:
  small   Every 5 minutes per agent, integer-minute offsets.
          1 core / <=8 GB RAM, shared or low-resource hosts. (DEFAULT on Enter/no-TTY)
  medium  Every 2 minutes with sub-minute sleep stagger.
          2-4 cores / 8-16 GB RAM.
  large   Every minute with pipeline-ordered sub-minute stagger.
          8+ cores / 16+ GB RAM.

Options:
  --dry-run              Show the crontab that would be installed without modifying anything.
  --wake-tier small|medium|large
                         Skip the interactive tier prompt and apply the selected tier
                         unconditionally. Existing PGAI (and non-PGAI) crontab entries
                         are replaced after a backup. Without this flag, the replacement
                         prompt defaults to N when a crontab already exists.
                         Also accepted as --wake-tier=small|medium|large.
  --no-system-cron       Skip crontab installation entirely — the OS crontab is left
                         exactly as-is (no writes, no probes).  For cron-less / Docker /
                         customer-site deployments where pseudocron.py drives wakes.
  --yes                  Explicit confirm — skip the "Install crontab?" prompt regardless
                         of TTY presence.  Without this flag, a no-TTY invocation declines
                         with exit 2.
  --help                 Print this usage and exit 0.

Existing-crontab behavior:
  Without --wake-tier: if a PGAI crontab already exists the replacement prompt defaults to N.
  With    --wake-tier:  the named tier is applied unconditionally (after backup).
  With --no-system-cron: the crontab is left completely untouched regardless of existing state.

Deprecated flags (still accepted; emit a deprecation notice to stderr):
  --tier small|medium|large  Replaced by --wake-tier <value>.
  --tier=small|medium|large  Replaced by --wake-tier=<value>.
  --tier none                Replaced by --no-system-cron.
  --tier=none                Replaced by --no-system-cron.

Environment:
  PGAI_AGENT_KANBAN_ROOT_PATH          Kanban root — canonical name (v0.36.0+)
                                        Default (fresh install): $HOME/pgai_agent_kanban
USAGE
}

# ---------------------------------------------------------------------------
# Parse arguments via the shared argparse library.
# Value-taking flags: wake-tier (canonical), tier (deprecated alias).
# Boolean flags: dry-run, no-system-cron, yes, non-interactive, help, h.
# ---------------------------------------------------------------------------
argparse_parse --value-flags "wake-tier tier" -- "$@"

# Emit clear errors for value-taking flags given with no value.
if argparse_missing "wake-tier"; then
  err "--wake-tier requires a value (small, medium, or large)."
  exit 1
fi
if argparse_missing "tier"; then
  err "--tier requires a value (small, medium, or large), or use --no-system-cron for none."
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
    dry-run|wake-tier|no-system-cron|yes|non-interactive|help|tier) ;;
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
if argparse_has "dry-run";          then DRY_RUN=true; fi
if argparse_has "no-system-cron";   then NO_SYSTEM_CRON=true; fi
if argparse_has "yes" || argparse_has "non-interactive"; then YES_FLAG=true; fi

# Extract canonical value flag.
if argparse_has "wake-tier"; then
  TIER_ARG="${ARGPARSE_FLAGS[wake-tier]}"
fi

# Handle deprecated --tier flag.
if argparse_has "tier"; then
  _dep_tier="${ARGPARSE_FLAGS[tier]}"
  case "$_dep_tier" in
    none)
      echo "[install-crontab] DEPRECATED: --tier=none; use --no-system-cron instead." >&2
      NO_SYSTEM_CRON=true
      ;;
    small|medium|large)
      echo "[install-crontab] DEPRECATED: --tier=${_dep_tier}; use --wake-tier ${_dep_tier} instead." >&2
      TIER_ARG="$_dep_tier"
      ;;
    *)
      err "Invalid tier '${_dep_tier}' for deprecated --tier. Choose: small, medium, large, or none."
      exit 1
      ;;
  esac
  unset _dep_tier
fi

# Validate --wake-tier if supplied.
if [[ -n "$TIER_ARG" ]]; then
  case "$TIER_ARG" in
    small|medium|large) ;;
    *)
      die "Invalid wake tier '$TIER_ARG'. Choose: small, medium, or large."
      ;;
  esac
fi

# Short-circuit for --no-system-cron: leave the OS crontab exactly as-is.
if [[ "$NO_SYSTEM_CRON" == "true" ]]; then
  info "--no-system-cron set — skipping crontab installation (cron-less / Docker deployment)."
  info "Wake agents with team/scripts/pseudocron.py instead of system cron."
  exit 0
fi

# ---------------------------------------------------------------------------
# Source the safe_overwrite library (provides _prompt_tty, safe_overwrite_crontab,
# warn_if_empty_crontab).
# ---------------------------------------------------------------------------
if [[ ! -f "$_SAFE_OVERWRITE_LIB" ]]; then
  die "safe_overwrite.sh not found: $_SAFE_OVERWRITE_LIB"
fi
# shellcheck source=team/scripts/lib/safe_overwrite.sh
source "$_SAFE_OVERWRITE_LIB"
# shellcheck source=team/scripts/lib/temp.sh
if [[ ! -f "$_TEMP_SH" ]]; then
  die "temp.sh not found: $_TEMP_SH"
fi
source "$_TEMP_SH"
unset _TEMP_SH

# ---------------------------------------------------------------------------
# Resolve kanban root
# ---------------------------------------------------------------------------
KANBAN_ROOT="${PGAI_AGENT_KANBAN_ROOT_PATH}"

# Validate kanban root
if [[ ! -d "$KANBAN_ROOT" ]]; then
  die "Kanban root not found: $KANBAN_ROOT
  Set PGAI_AGENT_KANBAN_ROOT_PATH or install to the default location."
fi

# Resolve absolute path for kanban root (for placeholder substitution in cron entries).
KANBAN_ROOT_ABS="$(readlink -f "$KANBAN_ROOT")"

info "Kanban root: $KANBAN_ROOT_ABS"

# ---------------------------------------------------------------------------
# Resolve __CLAUDE_PATH__: directory containing the claude binary.
# Falls back to /usr/local/bin if claude is not on PATH.
# ---------------------------------------------------------------------------
_claude_bin=$(command -v claude 2>/dev/null) || true
if [[ -n "$_claude_bin" ]]; then
  CLAUDE_DIR="$(dirname "$_claude_bin")"
else
  CLAUDE_DIR="/usr/local/bin"
  warn "claude binary not found in PATH; defaulting __CLAUDE_PATH__ to $CLAUDE_DIR in crontab"
fi
unset _claude_bin

# ---------------------------------------------------------------------------
# Pre-flight: "Install crontab?" yes/no
#
# This question gates the tier prompt — the tier prompt MUST NOT appear
# if the operator declines installation.
#
# Evaluation order:
#   1. --yes supplied → consent already given; proceed regardless of TTY.
#      Export PGAI_CRONTAB_CONFIRM=1 so safe_overwrite_crontab's own no-TTY
#      gate also passes.
#   2. No TTY and no --yes → decline with exit code 2.  Prevents automated
#      runs from silently overwriting the live crontab.
#   3. TTY present and no --yes → prompt the operator interactively.
#
# Hoisting the YES_FLAG check above the TTY check ensures that --yes means
# "consent given — do not prompt" universally (TTY or not), which is what
# upgrade.sh --force (which forwards --yes) requires on interactive hosts.
# ---------------------------------------------------------------------------

# Detect TTY using the same exec 3<>/dev/tty test used by _prompt_tty and
# safe_overwrite_crontab, so all three components agree on "no TTY."
_preflight_tty_ok=false
if exec 3<>/dev/tty 2>/dev/null; then
    exec 3>&-
    _preflight_tty_ok=true
fi

if [[ "$YES_FLAG" == "true" ]]; then
    # Caller explicitly confirmed — proceed without prompting regardless of
    # whether a TTY is present.  Export PGAI_CRONTAB_CONFIRM=1 so the
    # safe_overwrite_crontab no-TTY gate also passes.
    export PGAI_CRONTAB_CONFIRM=1
    info "Confirmed via --yes: proceeding with crontab installation."
elif [[ "$_preflight_tty_ok" == "false" ]]; then
    # No controlling terminal and no --yes flag — decline to protect the
    # live crontab.
    err "No TTY detected and --yes not supplied."
    err "Non-interactive crontab installs require the --yes flag to prevent"
    err "silent overwrites.  Re-run with --yes when you intend a non-interactive install."
    err "Example: $(basename "$0") --wake-tier large --yes"
    exit 2
else
    # Interactive run — prompt the operator.
    _install_response=""
    _prompt_tty 'Install or update PGAI crontab? [Y/n] ' _install_response
    case "${_install_response:-Y}" in
      [yY]*)
        ;;  # continue to tier prompt
      *)
        info "Crontab installation declined."
        info "To install manually, re-run: $(basename "$0")"
        exit 0
        ;;
    esac
    unset _install_response
fi
unset _preflight_tty_ok

# ---------------------------------------------------------------------------
# Tier prompt
#
# Only reached when the operator answered Y above.
#
# If --wake-tier was supplied on the command line (or a deprecated --tier alias),
# skip the prompt entirely.
# Otherwise ask interactively via _prompt_tty (handles no-TTY gracefully).
#
# Default on Enter / no-TTY / timeout = "small":
#   - Most conservative choice; avoids resource contention on unknown hardware.
#   - Operators who know their hardware should answer explicitly.
# ---------------------------------------------------------------------------
if [[ -n "$TIER_ARG" ]]; then
  SELECTED_TIER="$TIER_ARG"
else
  _tier_response=""
  _prompt_tty 'Select wake tier [small/medium/large] (default: small): ' _tier_response
  case "${_tier_response:-small}" in
    small|medium|large)
      SELECTED_TIER="${_tier_response:-small}"
      ;;
    none)
      info "Tier 'none' selected — skipping crontab installation (cron-less / Docker deployment)."
      info "Use --no-system-cron or team/scripts/pseudocron.py for cron-less deployments."
      exit 0
      ;;
    *)
      warn "Unrecognised tier '${_tier_response}' — defaulting to small."
      SELECTED_TIER="small"
      ;;
  esac
  unset _tier_response
fi

info "Selected tier: $SELECTED_TIER"

# ---------------------------------------------------------------------------
# Locate the tier template
# ---------------------------------------------------------------------------
TIER_TEMPLATE="${_TEMPLATES_DIR}/crontab-${SELECTED_TIER}.example"
if [[ ! -f "$TIER_TEMPLATE" ]]; then
  die "Crontab template not found for tier '$SELECTED_TIER': $TIER_TEMPLATE"
fi

# ---------------------------------------------------------------------------
# Substitute __KANBAN_ROOT__ and __CLAUDE_PATH__ placeholders into a temp file.
# The temp file is removed on EXIT (success or failure).
# pgai_mktemp routes under the resolved framework temp root (via temp.sh resolver)
# rather than landing directly in /tmp.
# ---------------------------------------------------------------------------
_RESOLVED_TEMPLATE="$(pgai_mktemp crontab_resolved)"
trap 'rm -f "$_RESOLVED_TEMPLATE"' EXIT

if ! sed -e "s|__KANBAN_ROOT__|${KANBAN_ROOT_ABS}|g" \
         -e "s|__CLAUDE_PATH__|${CLAUDE_DIR}|g" \
     "$TIER_TEMPLATE" > "$_RESOLVED_TEMPLATE"; then
  die "Failed to substitute placeholders in template: $TIER_TEMPLATE"
fi

# Sanity check: no unresolved placeholder tokens remain.
if grep -qE '__KANBAN_ROOT__|__CLAUDE_PATH__' "$_RESOLVED_TEMPLATE" 2>/dev/null; then
  die "Resolved template still contains placeholder tokens — substitution failed."
fi

# ---------------------------------------------------------------------------
# Dry-run: print the substituted content and exit.
# ---------------------------------------------------------------------------
if [[ "$DRY_RUN" == "true" ]]; then
  echo ""
  info "=== DRY RUN: crontab that would be installed (tier: $SELECTED_TIER) ==="
  cat "$_RESOLVED_TEMPLATE"
  info "=== end dry run ==="
  echo ""
  info "Run without --dry-run to apply."
  exit 0
fi

# ---------------------------------------------------------------------------
# Hand off to safe_overwrite_crontab.
# It handles:
#   (a) No existing crontab  — install prompt, default Y
#   (b) PGAI entries present — upgrade prompt, default N (or forced when --tier supplied)
#   (c) Non-PGAI entries     — replace prompt, default N (or forced when --tier supplied)
# Returns 0 on success, 1 on backup failure, 2 on operator decline.
#
# When --tier was supplied on the CLI (TIER_ARG non-empty), pass force_overwrite=true
# so that any existing crontab is replaced unconditionally (after backup).  This is
# the correct behavior for an explicit tier selection: the operator said "I want tier X"
# and the script must apply it, not silently preserve the old schedule.
# ---------------------------------------------------------------------------
info "Crontab tier: $SELECTED_TIER — reviewing current crontab state..."

# Determine whether to force-overwrite existing entries.
# force_overwrite=true only when the operator explicitly passed --wake-tier=X
# (or a deprecated --tier=X alias that was mapped to TIER_ARG).
if [[ -n "$TIER_ARG" ]]; then
  _FORCE_OVERWRITE="true"
else
  _FORCE_OVERWRITE="false"
fi

_overwrite_result=0
safe_overwrite_crontab "$_RESOLVED_TEMPLATE" "$_FORCE_OVERWRITE" || _overwrite_result=$?
unset _FORCE_OVERWRITE

case "$_overwrite_result" in
  0)
    ok "Crontab installed successfully (tier: $SELECTED_TIER)."
    # Post-install sanity check: warn loudly if crontab is empty after operation.
    warn_if_empty_crontab || true
    ;;
  2)
    # Operator declined — safe_overwrite_crontab already printed recovery instructions.
    warn "Crontab not installed (operator declined)."
    warn "To install manually (tier: $SELECTED_TIER):"
    warn "  sed -e \"s|__KANBAN_ROOT__|${KANBAN_ROOT_ABS}|g\" \\"
    warn "      -e \"s|__CLAUDE_PATH__|${CLAUDE_DIR}|g\" \\"
    warn "    ${TIER_TEMPLATE} | crontab -"
    ;;
  *)
    die "Crontab installation failed (safe_overwrite_crontab exited $_overwrite_result)."
    ;;
esac
