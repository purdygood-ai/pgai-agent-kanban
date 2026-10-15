#!/usr/bin/env bash
# run-cold-build-tests.sh
# Cold-build lane: builds the image flavors the operator selects from
# team/config/container-images.txt (docker/<flavor>/Dockerfile), under
# every container engine present on the host (docker and podman at
# minimum), with no layer cache and a forced base-image re-pull.
#
# Usage:
#   PGAI_CONTAINER_TIER=1 run-cold-build-tests.sh --image <name|all> [--verbose]
#   run-cold-build-tests.sh --operator-approved --image <name|all> [--verbose]
#
# Operator-only: this is the container tier. Without operator authorization
# (PGAI_CONTAINER_TIER=1 or --operator-approved), or from inside an agent
# wake, it prints one SKIPPED line and exits 0 before any engine command --
# see team/scripts/lib/container_tier.sh.
#
# Image selection: --image NAME (repeatable) or --image all; default
# kanban.cfg [release] container_images. With neither, the available images
# are listed and nothing is built (exit 0). The selection reaches the pytest
# lane as PGAI_CONTAINER_IMAGES.
#
# Exit codes:
#   0  = every (engine, flavor) pair built successfully, or narrate-skipped
#        for a documented environmental reason (engine absent, base-image
#        registry unreachable) — never a silent pass; also SKIPPED (not
#        authorized) and "no image selected", where nothing was built
#   1  = a Dockerfile failed to build under a reachable base image -- a
#        tree defect, not an environmental gap
#   2  = pytest not installed
#   3  = refused: an image name team/config/container-images.txt does not
#        declare (nothing built)
#
# Scope:
#   Runs every test module under team/tests/cold/: the Dockerfile cold-build
#   sweep (test_docker_cold_build.py) plus the container-launching coverage
#   relocated there (test_dashboard_smoke_container.py,
#   test_container_shell_launcher.py). This lane is deliberately NOT part of
#   run-unit-tests.sh or run-integration-tests.sh -- three-plus flavors times
#   two engines built cold (no cache, forced base re-pull), plus an
#   in-container smoke pass, is minutes of work, not the fast loop every
#   CODER runs on every ticket.
#
# pytest arguments:
#   python3 -m pytest tests/cold/ --capture=no [--verbose] </dev/null, run
#   from team/. The arguments are defined once, in
#   team/scripts/lib/cold_lane_pytest_args.sh (COLD_LANE_PYTEST_ARGS);
#   --verbose is appended only when this script gets --verbose.
#   --capture=no puts every test's stdout in the lane output, skipped and
#   passed tests included: the per-pair "[cold-build] SKIP — <reason>"
#   narration and "Image built successfully" lines are the evidence
#   deep-verify's classifier reads to tell a built run from one that could
#   not be attempted. stdin comes from /dev/null, as it does under pytest's
#   default capture, so no test's subprocess can read the caller's terminal.
#
# pytest basetemp:
#   Every invocation of this script claims its own pytest basetemp under
#   ${PGAI_AGENT_KANBAN_TEMP_DIR}/pytest/, a directory named
#   cold-<scope>.XXXXXX where <scope> is PGAI_TASK_ID when the wake layer
#   exported one and pid<pid> otherwise, and the random suffix is mktemp's
#   (gated_runner_pytest_basetemp in team/scripts/lib/gated_runner_lint.sh).
#   pytest removes and recreates whatever --basetemp it is given at session
#   start, so a path shared with any other live pytest session on the host
#   -- a second run of this script, a sibling agent's run, another runner --
#   would delete that session's fixture directories mid-run; a directory
#   per invocation removes the interference entirely. The directory is
#   removed on this script's own exit path, whatever the exit code, so the
#   pytest/ subtree never accumulates one directory per historical run.
#
# Repository root: run from a checkout (<repo>/team/scripts/), the lane
# builds that checkout; run from an install (<kanban-root>/scripts/), it
# builds the resolved dev tree -- PGAI_DEV_TREE_PATH, else kanban.cfg
# [paths] dev_tree_path. resolve_runner_repo_root in
# team/scripts/lib/dev_tree.sh holds the rule.
#
# Configuration:
#   PGAI_AGENT_KANBAN_ROOT_PATH  — kanban root (default: $HOME/pgai_agent_kanban)
#   PGAI_DEV_TREE_PATH           — dev tree an install-layout run works in

# --- Bootstrap: self-locate → source shell-env → fail loud ---
# shellcheck source=lib/env_bootstrap.sh
# CENSUS-ANCHOR: run-cold-build-tests-01
source "$(dirname "${BASH_SOURCE[0]}")/lib/env_bootstrap.sh" || exit 1

# --- Resolve kanban root and script directory ---
TEAM_ROOT="${PGAI_AGENT_KANBAN_ROOT_PATH}"
_RCB_SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

# --- Source optional config files ---
[[ -f "$TEAM_ROOT/bashrc" ]] && source "$TEAM_ROOT/bashrc"
[[ -f "$TEAM_ROOT/env" ]] && source "$TEAM_ROOT/env"
[[ -f "$HOME/.config/pgai-kanban.cfg" ]] && source "$HOME/.config/pgai-kanban.cfg"
# shellcheck source=lib/ini_parser.sh
[[ -f "${_RCB_SCRIPT_DIR}/lib/ini_parser.sh" ]] && source "${_RCB_SCRIPT_DIR}/lib/ini_parser.sh"
# shellcheck source=lib/dev_tree.sh
source "${_RCB_SCRIPT_DIR}/lib/dev_tree.sh"
if [[ -f "$TEAM_ROOT/kanban.cfg" ]]; then
    export PGAI_KANBAN_PM_MODE="${PGAI_KANBAN_PM_MODE:-$(read_ini "$TEAM_ROOT/kanban.cfg" chain pm_mode automatic)}"
fi
# shellcheck source=lib/temp.sh
[[ -f "${_RCB_SCRIPT_DIR}/lib/temp.sh" ]] && source "${_RCB_SCRIPT_DIR}/lib/temp.sh"
# shellcheck source=lib/gated_runner_lint.sh
[[ -f "${_RCB_SCRIPT_DIR}/lib/gated_runner_lint.sh" ]] && source "${_RCB_SCRIPT_DIR}/lib/gated_runner_lint.sh"

# Self-locate fallback for dev tree.
_RCB_SELF_ROOT="$(cd "${_RCB_SCRIPT_DIR}/../.." && pwd)"
unset _RCB_SCRIPT_DIR
export PGAI_DEV_TREE_PATH="${PGAI_DEV_TREE_PATH:-$(resolve_global_dev_tree)}"
if [[ -z "${PGAI_DEV_TREE_PATH:-}" && -d "${_RCB_SELF_ROOT}" ]]; then
    export PGAI_DEV_TREE_PATH="${_RCB_SELF_ROOT}"
fi
unset _RCB_SELF_ROOT
require_dev_tree "${PGAI_DEV_TREE_PATH:-}" "$TEAM_ROOT/kanban.cfg"

# --- Now enable strict mode ---
set -euo pipefail

# --- Clean exit handling ---
# shellcheck source=lib/script_common.sh
source "$(dirname "${BASH_SOURCE[0]}")/lib/script_common.sh"
trap cleanup_on_exit EXIT

# --- Release this invocation's pytest basetemp on every exit path ---
# Registered ahead of the claim below, through script_common.sh's
# CLEANUP_ON_EXIT_PRE_HOOK, so it fires on a normal fall-through, the
# pytest-failure `exit 1`, and a `set -e` abort alike. An abort before the
# claim has happened is a no-op release (the helper ignores an empty path),
# and the helper never affects this runner's own exit code.
_rcb_release_basetemp_on_exit() {
  gated_runner_pytest_basetemp_cleanup "${_RCB_PYTEST_BASETEMP:-}"
}
CLEANUP_ON_EXIT_PRE_HOOK=_rcb_release_basetemp_on_exit

# --- Resolve script directory and the repository root ---
# Run from a checkout (team/scripts/), the root is that checkout; run from an
# install (scripts/), it is the resolved dev tree, PGAI_DEV_TREE_PATH
# (resolve_runner_repo_root in lib/dev_tree.sh).
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(resolve_runner_repo_root "$SCRIPT_DIR" "$TEAM_ROOT/kanban.cfg")"

# shellcheck source=lib/error_contract.sh
source "$SCRIPT_DIR/lib/error_contract.sh"

# shellcheck source=lib/container_tier.sh
source "$SCRIPT_DIR/lib/container_tier.sh"

# shellcheck source=lib/cold_lane_pytest_args.sh
source "$SCRIPT_DIR/lib/cold_lane_pytest_args.sh"

# --- Parse arguments (the container-tier flags --image and
# --operator-approved are separated out by lib/container_tier.sh first) ---
_RCB_ORIG_ARGS=("$@")
container_tier_take_args "$@"
set -- ${CONTAINER_TIER_REST_ARGS[@]+"${CONTAINER_TIER_REST_ARGS[@]}"}
VERBOSE=false
while [[ $# -gt 0 ]]; do
  case "$1" in
    --verbose|-v)
      VERBOSE=true
      shift
      ;;
    --help|-h)
      cat <<EOF
Usage: PGAI_CONTAINER_TIER=1 $(basename "$0") --image <name|all> [--verbose]

Cold-build lane for the pgai-agent-kanban container images.

Operator-only. This is the container tier: without PGAI_CONTAINER_TIER=1
in the invoking shell or --operator-approved, or from inside an agent
wake, it prints one SKIPPED line and exits 0 before any engine command.

Builds the image flavors selected with --image from
team/config/container-images.txt (docker/<flavor>/Dockerfile), under every
container engine present on the host (docker and podman at minimum), with
--no-cache and --pull (no layer reuse, forced base-image re-pull). A
Dockerfile that only builds because a machine happens to hold the right
cached layers is exactly what this lane catches.

The flavor list is read from team/config/container-images.txt -- a
directory under docker/ that the manifest does not name is never built.
Each engine's availability is checked independently: a
host missing one engine still runs the full sweep under the other, and the
skipped engine is narrated by name, never silently dropped from coverage.

A registry-reachability probe runs before every build starts. A probe
failure narrate-skips that specific (engine, flavor) pair, naming the
image reference and the probe command. Once a build starts, a non-zero
exit is always attributed to the Dockerfile -- never guessed at as a
network problem -- and the failure names the flavor, the engine, and the
Dockerfile step that failed.

This lane also runs the in-container dashboard smoke harness and the
container-shell launcher coverage (team/tests/cold/
test_dashboard_smoke_container.py, test_container_shell_launcher.py) --
both docker-availability-gated the same way the cold-build sweep is.

Not part of the fast loop: run-unit-tests.sh and run-integration-tests.sh
do not collect this lane. Run it explicitly, or from a release-ritual step
that has budgeted the wall-clock time for a cold, multi-engine sweep.

pytest runs from team/ as:
  python3 -m pytest ${COLD_LANE_PYTEST_ARGS[*]} [--verbose] </dev/null
--capture=no prints every test's output, skipped and passed tests
included, so each pair's "[cold-build] SKIP — <reason>" or "Image built
successfully" line reaches the lane output that deep-verify classifies.
The arguments are defined in scripts/lib/cold_lane_pytest_args.sh.

Options:
  --image NAME         Build image NAME (repeatable), or --image all for every
                       image the manifest declares. Default: kanban.cfg
                       [release] container_images. With neither, the
                       available images are listed and nothing is built.
  --operator-approved  Operator authorization, same as PGAI_CONTAINER_TIER=1
  --verbose, -v        Pass --verbose flag through to pytest
  --help, -h           Show this help

Exit codes:
  0  = every (engine, flavor) pair built, or narrate-skipped for a
       documented environmental reason (engine absent, registry unreachable);
       also SKIPPED (not authorized) or no image selected (nothing built)
  1  = a Dockerfile failed to build under a reachable base image
  2  = pytest not installed
  3  = refused: an image name the manifest does not declare (nothing built)

Related runners:
  run-unit-tests.sh         Full unit test suite (no docker builds)
  run-integration-tests.sh  Integration test suite (no docker builds)
  run-gated-tests.sh        Docker-gated entrypoint tests (warm, docker-only)
EOF
      exit 0
      ;;
    *)
      # Partial remedy (docs/coding-standards.md "Gates prescribe, not just
      # diagnose"): the offending argument is named deterministically, but
      # which valid flag the operator meant to pass is user intent this
      # runner cannot resolve — --help names every accepted flag as the
      # next step. Operator-CLI argument dispatch: out of the census's
      # loop-bearing scope (the autonomous loop always invokes this runner
      # with well-formed arguments), so no CENSUS-ANCHOR here.
      echo "FAIL: unrecognized argument '$1'." >&2
      echo "" >&2
      echo "Cause(s):" >&2
      echo "  - '$1' does not match any flag this runner accepts." >&2
      echo "" >&2
      echo "To fix:" >&2
      echo "  1. bash \"${BASH_SOURCE[0]}\" --help          # list every accepted flag" >&2
      echo "  2. Re-run with the corrected flag, e.g.: $(container_tier_operator_command "${BASH_SOURCE[0]}" --image all --verbose)" >&2
      echo "" >&2
      echo "Rule: this runner accepts only the flags listed in its --help output; an" >&2
      echo "unrecognized argument is rejected rather than silently ignored." >&2
      exit 1
      ;;
  esac
done

# --- Operator-only gate: skip (exit 0) before anything touches a container
# engine; then the image selection, which lists-and-exits or refuses an
# undeclared name, also before any engine command ---
container_tier_require_operator "${BASH_SOURCE[0]}" ${_RCB_ORIG_ARGS[@]+"${_RCB_ORIG_ARGS[@]}"}
container_tier_select_images "${BASH_SOURCE[0]}" ${_RCB_ORIG_ARGS[@]+"${_RCB_ORIG_ARGS[@]}"}
unset _RCB_ORIG_ARGS

# --- Check pytest is available ---
# Full remedy (docs/coding-standards.md "Gates prescribe, not just diagnose"):
# pytest's absence has one universal, verbatim-runnable fix.
if ! command -v pytest >/dev/null 2>&1 && ! python3 -m pytest --version >/dev/null 2>&1; then
  echo "FAIL: pytest is not installed in this Python environment." >&2
  echo "" >&2
  echo "Cause(s):" >&2
  echo "  - no 'pytest' executable found on PATH" >&2
  echo "  - 'python3 -m pytest --version' also failed (pytest not importable)" >&2
  echo "" >&2
  echo "To fix:" >&2
  echo "  1. pip install pytest" >&2
  echo "  2. $(container_tier_operator_command "${BASH_SOURCE[0]}" --image all)" >&2
  echo "" >&2
  echo "Rule: this runner requires pytest as a hard dependency; it cannot execute" >&2
  echo "the cold-build lane without it." >&2
  # BLOCK-WORTHY: verification unable to run at all -- pytest missing means
  # the cold-build sweep cannot execute, not that it ran clean.
  # CENSUS-ANCHOR: run-cold-build-tests-02
  exit 2
fi

# --- Pinned-CLI-version preflight intentionally not carried by this lane:
# an earlier lineage of this script gated the build on docker/cli-versions.env
# (via a shared test fixture, passing CLAUDE_CODE_VERSION/CODEX_VERSION as
# --build-arg to every real flavor build) and each docker/<flavor>/Dockerfile
# carried a matching ARG guard. Neither docker/cli-versions.env nor any
# Dockerfile ARG guard for those two keys exists in the Dockerfiles this
# lane builds today, so gating on the missing file would make every
# invocation refuse immediately regardless of whether the shipped
# Dockerfiles actually build -- not an honest verdict about the tree this
# lane is asked to build. ---

# --- Claim this invocation's own pytest basetemp under the framework temp root ---
# gated_runner_pytest_basetemp (team/scripts/lib/gated_runner_lint.sh)
# resolves a directory that belongs to this invocation alone (its shape is
# in this script's header), keeping this run's fixture directories out of
# reach of every other live pytest session on the host; the exit hook
# registered above removes it on every exit path, so the pytest/ subtree
# never holds one directory per historical run. The variable stays set,
# unexported, so that hook can find it. PYTEST_ADDOPTS is appended to (not
# overwritten) in case the caller already has flags set. The temp root is
# exported first so conftest.py's PYTEST_DEBUG_TEMPROOT guard sees it.
export PGAI_AGENT_KANBAN_TEMP_DIR="$(pgai_temp_dir)"
_RCB_PYTEST_BASETEMP="$(gated_runner_pytest_basetemp cold)"
export PYTEST_ADDOPTS="${PYTEST_ADDOPTS:+${PYTEST_ADDOPTS} }--basetemp=${_RCB_PYTEST_BASETEMP}"

# --- Build pytest arguments ---
# COLD_LANE_PYTEST_ARGS (lib/cold_lane_pytest_args.sh) is the one definition
# of what the lane collects and how it captures output; the header's
# "pytest arguments" section says why each argument is there.
PYTEST_ARGS=("${COLD_LANE_PYTEST_ARGS[@]}")
if [[ "$VERBOSE" == "true" ]]; then
  PYTEST_ARGS+=("--verbose")
fi

# --- Run the cold-build lane, inside one docker-use session (START now,
# FINISH with duration and exit code when this script exits) ---
docker_use_session_begin "run-cold-build-tests.sh" "$(IFS=,; echo "${CONTAINER_TIER_IMAGES[*]}")"
cd "$REPO_ROOT/team"
echo "Running cold-build lane in $(pwd)/tests/cold/"
_RCB_START_EPOCH=$(date +%s)
set +e
# stdin from /dev/null: with capture off, pytest no longer points the
# tests' stdin there itself.
python3 -m pytest "${PYTEST_ARGS[@]}" </dev/null
PYTEST_EXIT=$?
set -e
_RCB_END_EPOCH=$(date +%s)
echo "Cold-build lane wall-clock runtime: $(( _RCB_END_EPOCH - _RCB_START_EPOCH ))s"
unset _RCB_START_EPOCH _RCB_END_EPOCH

# Treat exit code 5 (no tests collected / all skipped) as success — every
# engine absent on this host is a documented environmental condition, not
# a lane failure, and every skip along the way was already narrated above.
if [[ $PYTEST_EXIT -eq 5 ]]; then
  echo "Cold-build lane: all (engine, flavor) pairs skipped (see narration above) — exit 0"
  exit 0
fi

if [[ $PYTEST_EXIT -ne 0 ]]; then
  # Non-deterministic (exempt) per docs/coding-standards.md "The
  # non-deterministic exemption" — a real Dockerfile build failure has no
  # verbatim fix; point at the evidence pytest already printed above.
  echo "Cold-build lane FAILED (exit code: $PYTEST_EXIT)" >&2
  echo "Next diagnostic step: scroll up to the pytest output directly above —" >&2
  echo "its short-test-summary 'FAILED <file>::<test>' lines, and the" >&2
  echo "'[cold-build] BUILD STDOUT/STDERR' blocks above them, name the specific" >&2
  echo "flavor, engine, and failing Dockerfile step. There is no command that" >&2
  echo "fixes a real Dockerfile defect." >&2
  # BLOCK-WORTHY: breakage-class — a Dockerfile that fails to build cold is
  # an unusable image; the sweep is the gate that would have caught it
  # before it shipped.
  # CENSUS-ANCHOR: run-cold-build-tests-03
  exit 1
fi

echo "Cold-build lane PASSED"
exit 0
