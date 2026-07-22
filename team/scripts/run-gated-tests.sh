#!/usr/bin/env bash
# run-gated-tests.sh
# Run the docker-gated test suite against the install-shaped fixture volume.
# Exercises docker/entrypoint.sh dispatch modes (default pseudocron, dashboard,
# passthrough) using a bind-mount shaped like a real install.sh output — not
# the dev tree.  Tests skip cleanly when no docker daemon is available.
#
# Usage:
#   run-gated-tests.sh [--verbose]
#
# Exit codes:
#   0  = all tests passed (or all docker-gated tests skipped — no docker daemon)
#   1  = test failure
#   2  = pytest not installed
#
# Docker gate:
#   When the docker binary is absent or the docker daemon is unreachable, the
#   test module skips itself with a narrated reason.  This script treats
#   pytest exit code 5 (no tests collected) as success so a docker-absent CI
#   environment produces a clean exit.  Exit code 0 from a full skip run still
#   surfaces in the test log as SKIPPED, not silently absent.
#
# Scope:
#   Runs ONLY tests in team/tests/unit/test_entrypoint_install_shaped.py.
#   To run the full unit suite (all docker and non-docker tests), use
#   run-unit-tests.sh instead.
#
# Configuration:
#   PGAI_AGENT_KANBAN_ROOT_PATH  — kanban root (default: $HOME/pgai_agent_kanban)

# --- Bootstrap: self-locate → source shell-env → fail loud ---
# shellcheck source=lib/env_bootstrap.sh
source "$(dirname "${BASH_SOURCE[0]}")/lib/env_bootstrap.sh" || exit 1

# --- Resolve kanban root and script directory ---
TEAM_ROOT="${PGAI_AGENT_KANBAN_ROOT_PATH}"
_RGT_SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

# --- Source optional config files ---
[[ -f "$TEAM_ROOT/bashrc" ]] && source "$TEAM_ROOT/bashrc"
[[ -f "$TEAM_ROOT/env" ]] && source "$TEAM_ROOT/env"
[[ -f "$HOME/.config/pgai-kanban.cfg" ]] && source "$HOME/.config/pgai-kanban.cfg"
# shellcheck source=lib/ini_parser.sh
[[ -f "${_RGT_SCRIPT_DIR}/lib/ini_parser.sh" ]] && source "${_RGT_SCRIPT_DIR}/lib/ini_parser.sh"
# shellcheck source=lib/dev_tree.sh
source "${_RGT_SCRIPT_DIR}/lib/dev_tree.sh"
if [[ -f "$TEAM_ROOT/kanban.cfg" ]]; then
    export PGAI_KANBAN_PM_MODE="${PGAI_KANBAN_PM_MODE:-$(read_ini "$TEAM_ROOT/kanban.cfg" chain pm_mode automatic)}"
fi
# shellcheck source=lib/temp.sh
[[ -f "${_RGT_SCRIPT_DIR}/lib/temp.sh" ]] && source "${_RGT_SCRIPT_DIR}/lib/temp.sh"

# Self-locate fallback for dev tree.
_RGT_SELF_ROOT="$(cd "${_RGT_SCRIPT_DIR}/../.." && pwd)"
unset _RGT_SCRIPT_DIR
export PGAI_DEV_TREE_PATH="${PGAI_DEV_TREE_PATH:-$(resolve_global_dev_tree)}"
if [[ -z "${PGAI_DEV_TREE_PATH:-}" && -d "${_RGT_SELF_ROOT}" ]]; then
    export PGAI_DEV_TREE_PATH="${_RGT_SELF_ROOT}"
fi
unset _RGT_SELF_ROOT
require_dev_tree "${PGAI_DEV_TREE_PATH:-}" "$TEAM_ROOT/kanban.cfg"

# --- Now enable strict mode ---
set -euo pipefail

# --- Clean exit handling ---
cleanup_on_exit() {
  local exit_code=$?
  jobs -p 2>/dev/null | xargs -r kill 2>/dev/null || true
  wait 2>/dev/null || true
  exit $exit_code
}
trap cleanup_on_exit EXIT

# --- Resolve script directory to find the repo root ---
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/../.." && pwd)"

# --- Parse arguments ---
VERBOSE=false
while [[ $# -gt 0 ]]; do
  case "$1" in
    --verbose|-v)
      VERBOSE=true
      shift
      ;;
    --help|-h)
      cat <<EOF
Usage: $(basename "$0") [--verbose]

Run the docker-gated test suite for pgai-agent-kanban entrypoint dispatch.

Tests verify that the container entrypoint correctly dispatches pseudocron,
dashboard, and passthrough modes when the /pgai_agent_kanban bind-mount is
INSTALL-SHAPED (no top-level team/ directory, scripts at scripts/).

Both docker/rhel9 and docker/debian flavors are exercised.

When no docker daemon is available, all docker-gated tests skip cleanly.
This script treats pytest exit code 5 (no tests collected or all skipped)
as success.

Options:
  --verbose, -v   Pass --verbose flag through to pytest
  --help, -h      Show this help

Exit codes:
  0  = all tests passed (or all docker tests skipped — no docker daemon)
  1  = test failure
  2  = pytest not installed

Related runners:
  run-unit-tests.sh         Full unit test suite (includes docker tests)
  run-integration-tests.sh  Integration test suite
EOF
      exit 0
      ;;
    *)
      echo "ERROR: Unknown argument: $1" >&2
      exit 1
      ;;
  esac
done

# --- Check pytest is available ---
if ! command -v pytest >/dev/null 2>&1 && ! python3 -m pytest --version >/dev/null 2>&1; then
  echo "ERROR: pytest not installed. Install it with: pip install pytest" >&2
  exit 2
fi

# --- Redirect pytest basetemp under the framework temp root ---
export PGAI_AGENT_KANBAN_TEMP_DIR="$(pgai_temp_dir)"
mkdir -p "${PGAI_AGENT_KANBAN_TEMP_DIR}"
_PYTEST_BASETEMP="${PGAI_AGENT_KANBAN_TEMP_DIR}/pytest/gated"
mkdir -p "$_PYTEST_BASETEMP"
export PYTEST_ADDOPTS="${PYTEST_ADDOPTS:+${PYTEST_ADDOPTS} }--basetemp=${_PYTEST_BASETEMP}"
unset _PYTEST_BASETEMP

# --- Build pytest arguments ---
# Run only the install-shaped fixture test module (the docker-gated suite).
PYTEST_ARGS=(
  "tests/unit/test_entrypoint_install_shaped.py"
)
if [[ "$VERBOSE" == "true" ]]; then
  PYTEST_ARGS+=("--verbose")
fi

# --- Run tests ---
cd "$REPO_ROOT/team"
echo "Running docker-gated tests in $(pwd)/tests/unit/test_entrypoint_install_shaped.py"
set +e
python3 -m pytest "${PYTEST_ARGS[@]}"
PYTEST_EXIT=$?
set -e

# Treat exit code 5 (no tests collected / all skipped) as success.
# This ensures a docker-absent environment exits cleanly.
if [[ $PYTEST_EXIT -eq 5 ]]; then
  echo "Docker-gated tests: all skipped (no docker daemon available) — exit 0"
  exit 0
fi

if [[ $PYTEST_EXIT -ne 0 ]]; then
  echo "Docker-gated tests FAILED (exit code: $PYTEST_EXIT)" >&2
  exit 1
fi

echo "Docker-gated tests PASSED"
exit 0
