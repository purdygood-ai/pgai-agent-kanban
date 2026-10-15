#!/usr/bin/env bash
# run-three-host-matrix.sh
# Runs the real unit + integration suites (team/scripts/run-unit-tests.sh and
# run-integration-tests.sh -- no second parallel test-running path) across
# the three environments the estate must survive on: the dev tree as it
# stands, an install with no git binary, and a container with no tmux
# binary. Each host is a genuinely constructed environment -- a real
# container with the binary actually uninstalled, not a PATH-scrubbed
# simulation of one -- because a scrubbed PATH proves the guard fixtures
# call shutil.which correctly; it does not prove the estate survives a host
# where the binary was never installed at all.
#
# The gitless and tmux-less hosts are built by layering ONE removal step
# on top of a shipped image the operator selects (docker/<image>/Dockerfile,
# the real workspace image, built the same way docs/docker.md documents) --
# never a second, hand-rolled Dockerfile lineage. Both derived images
# additionally install requirements-test.txt, the dependency set
# run-unit-tests.sh and run-integration-tests.sh need to execute at all;
# the shipped workspace image itself does not carry those (it is not meant
# to run its own test suite unassisted). Each container host runs once per
# selected image and is reported as <host>@<image>.
#
# Usage:
#   PGAI_CONTAINER_TIER=1 run-three-host-matrix.sh --image <name|all>
#                             [--host dev] [--host gitless] [--host tmuxless]
#                             [--engine docker|podman|<binary>] [--verbose]
#
# Operator-only: this is the container tier. Without operator authorization
# (PGAI_CONTAINER_TIER=1 or --operator-approved), or from inside an agent
# wake, it prints one SKIPPED line and exits 0 before any engine command or
# scratch tree -- see team/scripts/lib/container_tier.sh.
#
# Options:
#   --image NAME    Base image for the gitless and tmuxless hosts, from
#                   team/config/container-images.txt. Repeatable; --image all
#                   selects every declared image. Default: kanban.cfg
#                   [release] container_images. With neither, and a
#                   container host requested, the available images are
#                   listed and nothing is built (exit 0). The dev host needs
#                   no image.
#   --operator-approved
#                   Operator authorization, same as PGAI_CONTAINER_TIER=1.
#   --host NAME     Run only this host. Repeatable -- pass --host twice (or
#                   more) to select a subset. NAME is one of: dev, gitless,
#                   tmuxless. Omit entirely to run all three (the default,
#                   and what "one command runs all three hosts" means).
#   --engine NAME   Container engine binary to use for the gitless and
#                   tmuxless hosts, overriding kanban.cfg [docker] docker_exe
#                   and the "docker" default. Pointing this at a binary that
#                   does not exist on PATH is the supported way to force the
#                   not-attempted path for a container host on demand --
#                   the resolution failure is real, not simulated.
#   --verbose, -v   Pass --verbose through to the underlying test runners.
#   --help, -h      Show this help and exit 0.
#
# Report shape:
#   One block per attempted host naming: verdict (passed / failed /
#   not-attempted), the runtime, and -- for passed/failed -- the combined
#   unit+integration passed/failed/skipped/errored counts plus a skip
#   census (deduplicated skip reasons with counts, so a reader sees WHY a
#   host skipped what it skipped, not just how much). A not-attempted host
#   names the unavailable resource instead of a count -- it is never
#   conflated with a pass, and it never silently drops from the report.
#   A final line states the total wall-clock runtime across every host run.
#
# Exit codes:
#   0  = every requested host's verdict was PASSED.
#   0  = also: SKIPPED (not authorized) or no image selected for a
#        requested container host -- nothing was built or run.
#   1  = at least one requested host FAILED or was NOT-ATTEMPTED (a
#        not-attempted host is not "green" -- see the report for which
#        resource was missing) -- or an internal setup step (temp root,
#        dev-tree resolution, argument parsing) could not proceed.
#   3  = refused: an image name team/config/container-images.txt does not
#        declare (nothing built).
#
# Cleanup:
# Every image and scratch tree this script creates is removed before it
# exits, on every exit path (success, a host failure, an early argument
# error) -- no container, image, or scratch tree survives a run. This is
# reported as an explicit before/after image-listing line in the
# printed report so a reader does not have to take the claim on faith.
#
# Configuration:
#   PGAI_AGENT_KANBAN_ROOT_PATH  — kanban root (default: $HOME/pgai_agent_kanban)

# --- Bootstrap: self-locate → source shell-env → fail loud ---
# shellcheck source=lib/env_bootstrap.sh
# BLOCK-WORTHY: an unreachable bootstrap prelude leaves every downstream
# tunable/path resolution undefined for this runner.
# CENSUS-ANCHOR: run-three-host-matrix-env-bootstrap-01
source "$(dirname "${BASH_SOURCE[0]}")/lib/env_bootstrap.sh" || exit 1

# --- Resolve kanban root and script directory ---
TEAM_ROOT="${PGAI_AGENT_KANBAN_ROOT_PATH}"
_RTM_SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

# --- Source optional config files ---
[[ -f "$TEAM_ROOT/bashrc" ]] && source "$TEAM_ROOT/bashrc"
[[ -f "$TEAM_ROOT/env" ]] && source "$TEAM_ROOT/env"
[[ -f "$HOME/.config/pgai-kanban.cfg" ]] && source "$HOME/.config/pgai-kanban.cfg"
# shellcheck source=lib/ini_parser.sh
[[ -f "${_RTM_SCRIPT_DIR}/lib/ini_parser.sh" ]] && source "${_RTM_SCRIPT_DIR}/lib/ini_parser.sh"
# shellcheck source=lib/dev_tree.sh
source "${_RTM_SCRIPT_DIR}/lib/dev_tree.sh"
# shellcheck source=lib/temp.sh
source "${_RTM_SCRIPT_DIR}/lib/temp.sh"
# shellcheck source=lib/docker_engine.sh
source "${_RTM_SCRIPT_DIR}/lib/docker_engine.sh"
# shellcheck source=lib/script_common.sh
source "${_RTM_SCRIPT_DIR}/lib/script_common.sh"
if [[ -f "$TEAM_ROOT/kanban.cfg" ]]; then
    export PGAI_KANBAN_PM_MODE="${PGAI_KANBAN_PM_MODE:-$(read_ini "$TEAM_ROOT/kanban.cfg" chain pm_mode automatic)}"
fi

# Self-locate fallback for dev tree.
_RTM_SELF_ROOT="$(cd "${_RTM_SCRIPT_DIR}/../.." && pwd)"
unset _RTM_SCRIPT_DIR
export PGAI_DEV_TREE_PATH="${PGAI_DEV_TREE_PATH:-$(resolve_global_dev_tree)}"
if [[ -z "${PGAI_DEV_TREE_PATH:-}" && -d "${_RTM_SELF_ROOT}" ]]; then
    export PGAI_DEV_TREE_PATH="${_RTM_SELF_ROOT}"
fi
unset _RTM_SELF_ROOT
require_dev_tree "${PGAI_DEV_TREE_PATH:-}" "$TEAM_ROOT/kanban.cfg"
REPO_ROOT="${PGAI_DEV_TREE_PATH}"

# --- Now enable strict mode ---
set -uo pipefail

# --- Clean exit handling: tear every image and scratch tree down first ---
# Split in two so the report's own before/after image inspection can prove
# image/container removal WHILE the report is being printed (the scratch
# tree holding the per-host log files the report's messages point readers
# at needs to survive until the report has actually been printed) -- the
# scratch tree itself is still guaranteed gone by the time this process
# exits, via _rtm_teardown_scratch in the EXIT trap below, on every path
# including one that errors out before the report is ever reached.
_RTM_CREATED_TAGS=()
_rtm_teardown_images() {
    if [[ -n "${_RTM_ENGINE:-}" ]]; then
        local _tag _i
        # Derived images first (newest first), then the bases they sit on.
        for (( _i=${#_RTM_CREATED_TAGS[@]}-1; _i>=0; _i-- )); do
            _tag="${_RTM_CREATED_TAGS[$_i]}"
            pgai_docker "${_RTM_ENGINE}" image rm --force "${_tag}" >/dev/null 2>&1
        done
    fi
    _RTM_CREATED_TAGS=()
    return 0
}
_rtm_teardown_scratch() {
    [[ -n "${_RTM_SCRATCH:-}" ]] && pgai_temp_cleanup "${_RTM_SCRATCH}" >/dev/null 2>&1
    return 0
}
_rtm_teardown() {
    # AGENT-RESOLVABLE-adjacent cleanup, not a gate: this never blocks the
    # exit it runs ahead of -- every command below is best-effort so a
    # teardown failure can never mask this run's real exit code. Runs on
    # every exit path (the EXIT trap below), including one that never
    # reaches the explicit mid-script teardown call near the report.
    _rtm_teardown_images
    _rtm_teardown_scratch
    return 0
}
CLEANUP_ON_EXIT_PRE_HOOK=_rtm_teardown
trap cleanup_on_exit EXIT

# --- Parse arguments (the container-tier flags --image and
# --operator-approved are separated out by lib/container_tier.sh first) ---
_RTM_ORIG_ARGS=("$@")
# shellcheck source=lib/container_tier.sh
source "$(dirname "${BASH_SOURCE[0]}")/lib/container_tier.sh"
container_tier_take_args "$@"
set -- ${CONTAINER_TIER_REST_ARGS[@]+"${CONTAINER_TIER_REST_ARGS[@]}"}
HOSTS=()
VERBOSE=false
ENGINE_OVERRIDE=""
while [[ $# -gt 0 ]]; do
    case "$1" in
        --host)
            [[ $# -lt 2 ]] && { echo "run-three-host-matrix.sh: --host requires a value (dev, gitless, or tmuxless)" >&2; exit 1; }
            HOSTS+=("$2")
            shift 2
            ;;
        --host=*)
            HOSTS+=("${1#--host=}")
            shift
            ;;
        --engine)
            [[ $# -lt 2 ]] && { echo "run-three-host-matrix.sh: --engine requires a value" >&2; exit 1; }
            ENGINE_OVERRIDE="$2"
            shift 2
            ;;
        --engine=*)
            ENGINE_OVERRIDE="${1#--engine=}"
            shift
            ;;
        --verbose|-v)
            VERBOSE=true
            shift
            ;;
        --help|-h)
            # The whole leading comment block, up to the first blank line.
            sed -n '2,/^$/p' "${BASH_SOURCE[0]}" | grep '^#' | sed 's/^# \{0,1\}//'
            exit 0
            ;;
        *)
            echo "run-three-host-matrix.sh: unrecognized argument: $1" >&2
            echo "Run 'run-three-host-matrix.sh --help' for usage." >&2
            exit 1
            ;;
    esac
done

if [[ "${#HOSTS[@]}" -eq 0 ]]; then
    HOSTS=(dev gitless tmuxless)
fi
for _h in "${HOSTS[@]}"; do
    case "$_h" in
        dev|gitless|tmuxless) ;;
        *)
            echo "run-three-host-matrix.sh: unrecognized --host value '${_h}' (expected dev, gitless, or tmuxless)" >&2
            exit 1
            ;;
    esac
done
unset _h

# --- Operator-only gate: skip (exit 0) before any engine command or
# scratch tree. Then, when a container host is requested, the image
# selection: lists-and-exits with nothing selected, refuses an undeclared
# name -- also before any engine command. ---
container_tier_require_operator "${BASH_SOURCE[0]}" ${_RTM_ORIG_ARGS[@]+"${_RTM_ORIG_ARGS[@]}"}
_RTM_NEEDS_IMAGE=false
for _h in "${HOSTS[@]}"; do
    [[ "$_h" != "dev" ]] && _RTM_NEEDS_IMAGE=true
done
unset _h
if [[ "$_RTM_NEEDS_IMAGE" == "true" ]]; then
    container_tier_select_images "${BASH_SOURCE[0]}" ${_RTM_ORIG_ARGS[@]+"${_RTM_ORIG_ARGS[@]}"}
    docker_use_session_begin "run-three-host-matrix.sh" "$(IFS=,; echo "${CONTAINER_TIER_IMAGES[*]}")"
fi
unset _RTM_ORIG_ARGS

# --- Scratch root for this run: images, tree copies, per-host pytest logs ---
_RTM_SCRATCH="$(pgai_mktemp_d host_matrix)"
mkdir -p "${_RTM_SCRATCH}/logs"

# --- Ambient-environment scrubbing ---
# A wake-invoked agent session (this one included) carries project-scoped
# variables -- PGAI_PROJECT_NAME chief among them -- that a genuinely fresh
# checkout or container never has set. Left in place, they leak into every
# subprocess a test spawns and can silently resolve a "no --project given"
# code path to a real project instead of genuinely exercising the absent-
# flag behavior a test claims to cover. Scrubbing this fixed list before
# every host run (dev included) is what makes "the dev tree as it stands"
# mean the tree a fresh checkout would see, not this session's own wake
# context.
_RTM_SCRUB_VARS=(
    PGAI_PROJECT_NAME PGAI_PROJECT_ROOT PGAI_TASKS_DIR
    PP_TEMP_DIR PP_project_name PP_dev_tree_path
    PGAI_AGENT_WAKE_CONTEXT PGAI_RELEASE_STATE_PATH PGAI_QUEUE_DIR
)

# ---------------------------------------------------------------------------
# _rtm_parse_pytest_counts <combined-log-file>
# Echo "passed failed skipped errors" parsed from every pytest summary line
# in the log (a host runs two suites -- unit then integration -- each
# producing its own summary line; counts are SUMMED across both).
# ---------------------------------------------------------------------------
_rtm_parse_pytest_counts() {
    local log="$1"
    local passed=0 failed=0 skipped=0 errors=0 n
    while IFS= read -r summary; do
        n="$(grep -oE '[0-9]+ passed' <<<"$summary" | grep -oE '[0-9]+' || true)"
        [[ -n "$n" ]] && passed=$((passed + n))
        n="$(grep -oE '[0-9]+ failed' <<<"$summary" | grep -oE '[0-9]+' || true)"
        [[ -n "$n" ]] && failed=$((failed + n))
        n="$(grep -oE '[0-9]+ skipped' <<<"$summary" | grep -oE '[0-9]+' || true)"
        [[ -n "$n" ]] && skipped=$((skipped + n))
        n="$(grep -oE '[0-9]+ error' <<<"$summary" | grep -oE '[0-9]+' || true)"
        [[ -n "$n" ]] && errors=$((errors + n))
    done < <(grep -E '^=+ .*(passed|failed|error).* in [0-9.]+s.*=+$' "$log" || true)
    printf '%s %s %s %s\n' "$passed" "$failed" "$skipped" "$errors"
}

# ---------------------------------------------------------------------------
# _rtm_skip_census <combined-log-file>
# Print deduplicated skip reasons with counts, one per line, sorted by
# count descending. Requires the caller to have run pytest with -ra (or
# -rs) so each skip's "SKIPPED [n] file:line: reason" line is present --
# this runner sets PYTEST_ADDOPTS=-ra for every host before invoking the
# real test runners.
# ---------------------------------------------------------------------------
_rtm_skip_census() {
    local log="$1"
    grep -E '^SKIPPED \[[0-9]+\] ' "$log" \
        | sed -E 's/^SKIPPED \[[0-9]+\] [^:]+:[0-9]+: //' \
        | sort | uniq -c | sort -rn
}

# ---------------------------------------------------------------------------
# _rtm_run_suites_in <workdir> <log-file> <env-args...>
# Run run-unit-tests.sh then run-integration-tests.sh inside <workdir>,
# under the given env overrides, appending combined stdout+stderr to
# <log-file>. Returns the highest exit code the two runners produced (0 if
# both were 0).
# ---------------------------------------------------------------------------
_rtm_run_suites_in() {
    local workdir="$1" log="$2"
    shift 2
    local rc_unit rc_integration
    env "$@" bash -c "cd '${workdir}' && bash team/scripts/run-unit-tests.sh $([[ "$VERBOSE" == "true" ]] && echo --verbose)" >>"$log" 2>&1
    rc_unit=$?
    env "$@" bash -c "cd '${workdir}' && bash team/scripts/run-integration-tests.sh $([[ "$VERBOSE" == "true" ]] && echo --verbose)" >>"$log" 2>&1
    rc_integration=$?
    if [[ "$rc_unit" -ge "$rc_integration" ]]; then
        return "$rc_unit"
    fi
    return "$rc_integration"
}

# ---------------------------------------------------------------------------
# Per-host result storage (parallel associative arrays keyed by host name).
# ---------------------------------------------------------------------------
declare -A RESULT_VERDICT RESULT_PASSED RESULT_FAILED RESULT_SKIPPED RESULT_ERRORED
declare -A RESULT_CENSUS RESULT_RUNTIME RESULT_NOTE

_rtm_run_host_dev() {
    local start end rc log
    start=$(date +%s)
    log="${_RTM_SCRATCH}/logs/dev.log"
    : > "$log"
    local env_args=("PYTEST_ADDOPTS=-ra") _v
    for _v in "${_RTM_SCRUB_VARS[@]}"; do
        env_args+=("${_v}=")
    done
    unset _v
    _rtm_run_suites_in "$REPO_ROOT" "$log" "${env_args[@]}"
    rc=$?
    end=$(date +%s)
    RESULT_RUNTIME[dev]=$((end - start))
    if [[ "$rc" -eq 2 ]]; then
        RESULT_VERDICT[dev]="not-attempted"
        RESULT_NOTE[dev]="pytest (or a required pre-flight tool) is not available in this environment -- see ${log}"
        return
    fi
    read -r p f s e < <(_rtm_parse_pytest_counts "$log")
    RESULT_PASSED[dev]=$p; RESULT_FAILED[dev]=$f; RESULT_SKIPPED[dev]=$s; RESULT_ERRORED[dev]=$e
    RESULT_CENSUS[dev]="$(_rtm_skip_census "$log")"
    if [[ "$rc" -eq 0 ]]; then
        RESULT_VERDICT[dev]="passed"
    else
        RESULT_VERDICT[dev]="failed"
    fi
}

# ---------------------------------------------------------------------------
# _rtm_resolve_engine
# Resolve the container engine binary once, shared by both container hosts.
# Sets _RTM_ENGINE on success. On failure sets _RTM_ENGINE_UNAVAILABLE to a
# human-readable reason and leaves _RTM_ENGINE empty -- callers report
# not-attempted rather than treating this as fatal to the whole run.
# ---------------------------------------------------------------------------
_rtm_resolve_engine() {
    [[ -n "${_RTM_ENGINE:-}" || -n "${_RTM_ENGINE_UNAVAILABLE:-}" ]] && return 0
    local engine=""
    if [[ -n "$ENGINE_OVERRIDE" ]]; then
        if command -v "$ENGINE_OVERRIDE" >/dev/null 2>&1; then
            engine="$ENGINE_OVERRIDE"
        else
            _RTM_ENGINE_UNAVAILABLE="configured --engine '${ENGINE_OVERRIDE}' is not an executable on PATH"
            return 0
        fi
    else
        local err
        if ! err="$(resolve_docker_exe "$TEAM_ROOT" 2>&1)"; then
            _RTM_ENGINE_UNAVAILABLE="container engine binary is not available (${err})"
            return 0
        fi
        engine="$err"
    fi
    if ! pgai_docker "$engine" info >/dev/null 2>&1; then
        _RTM_ENGINE_UNAVAILABLE="${engine} daemon is not reachable (\`${engine} info\` failed)"
        return 0
    fi
    _RTM_ENGINE="$engine"
}

# ---------------------------------------------------------------------------
# _rtm_ensure_base_image <image>
# Build the shipped docker/<image>/Dockerfile once per image (shared by both
# container hosts), tagged under this run's scratch namespace, and record
# the tag in _RTM_BASE_TAG[<image>]. Returns 1 (setting _RTM_BASE_BUILD_ERROR)
# on any build failure -- callers report not-attempted, naming the base
# image as the unconstructable resource.
# ---------------------------------------------------------------------------
declare -A _RTM_BASE_TAG=()
_rtm_ensure_base_image() {
    local image="$1"
    [[ -n "${_RTM_BASE_TAG[$image]:-}" ]] && return 0
    local tag="pgai-kanban-host-matrix-${image}-base-$$:test"
    local pins_file="${REPO_ROOT}/docker/cli-versions.env"
    if [[ ! -f "$pins_file" ]]; then
        _RTM_BASE_BUILD_ERROR="pinned CLI versions file not found: ${pins_file}"
        return 1
    fi
    local claude_version codex_version
    claude_version="$(grep -E '^CLAUDE_CODE_VERSION=' "$pins_file" | cut -d= -f2-)"
    codex_version="$(grep -E '^CODEX_VERSION=' "$pins_file" | cut -d= -f2-)"
    if [[ -z "$claude_version" || -z "$codex_version" ]]; then
        _RTM_BASE_BUILD_ERROR="could not read CLAUDE_CODE_VERSION/CODEX_VERSION from ${pins_file}"
        return 1
    fi
    if ! pgai_docker "${_RTM_ENGINE}" build \
        -f "${REPO_ROOT}/docker/${image}/Dockerfile" \
        --build-arg "CLAUDE_CODE_VERSION=${claude_version}" \
        --build-arg "CODEX_VERSION=${codex_version}" \
        -t "$tag" "$REPO_ROOT" \
        >"${_RTM_SCRATCH}/logs/base-build-${image}.log" 2>&1; then
        _RTM_BASE_BUILD_ERROR="docker/${image}/Dockerfile build failed -- see ${_RTM_SCRATCH}/logs/base-build-${image}.log"
        return 1
    fi
    _RTM_CREATED_TAGS+=("$tag")
    _RTM_BASE_TAG[$image]="$tag"
}

# ---------------------------------------------------------------------------
# _rtm_build_derived_image <host-name> <image> <package-to-remove>
# Build a thin image FROM <image>'s base with exactly one package removed
# and the test dependency set installed. The removal uses whichever
# package manager the base image carries (apt-get or dnf). Sets
# _RTM_DERIVED_TAG on success; returns 1 on failure.
# ---------------------------------------------------------------------------
_rtm_build_derived_image() {
    local host="$1" image="$2" package="$3"
    local dockerfile="${_RTM_SCRATCH}/Dockerfile.${host}-${image}"
    _RTM_DERIVED_TAG=""
    {
        printf 'FROM %s\n' "${_RTM_BASE_TAG[$image]}"
        printf 'USER root\n'
        printf 'COPY requirements.txt requirements-test.txt /tmp/\n'
        printf 'RUN PIP_BREAK_SYSTEM_PACKAGES=1 python3 -m pip install --no-cache-dir -r /tmp/requirements-test.txt\n'
        printf 'RUN if command -v apt-get >/dev/null 2>&1; then apt-get remove -y --purge %s && apt-get autoremove -y && rm -rf /var/lib/apt/lists/*; elif command -v dnf >/dev/null 2>&1; then dnf remove -y %s && dnf clean all; else echo "no apt-get or dnf in the base image" >&2; exit 1; fi\n' "$package" "$package"
        printf 'USER kanban\n'
    } > "$dockerfile"
    local tag="pgai-kanban-host-matrix-${host}-${image}-$$:test"
    if ! pgai_docker "${_RTM_ENGINE}" build -f "$dockerfile" -t "$tag" "$REPO_ROOT" \
        >"${_RTM_SCRATCH}/logs/${host}-${image}-build.log" 2>&1; then
        return 1
    fi
    _RTM_CREATED_TAGS+=("$tag")
    _RTM_DERIVED_TAG="$tag"
}

# ---------------------------------------------------------------------------
# _rtm_ensure_gitless_tree_copy
# Archive the dev tree's committed HEAD into a scratch copy for the
# gitless host -- a plain tree with no .git at all, matching what an
# install actually looks like, and avoiding any risk of a test run
# mutating the real worktree it was launched from.
# ---------------------------------------------------------------------------
_rtm_ensure_gitless_tree_copy() {
    [[ -n "${_RTM_GITLESS_TREE_COPY:-}" ]] && return 0
    local copy="${_RTM_SCRATCH}/gitless-tree-copy"
    mkdir -p "$copy"
    if ! (cd "$REPO_ROOT" && git archive HEAD) | tar -x -C "$copy" 2>"${_RTM_SCRATCH}/logs/gitless-tree-copy-error.log"; then
        _RTM_TREE_COPY_ERROR="git archive of the dev tree's HEAD failed -- see ${_RTM_SCRATCH}/logs/gitless-tree-copy-error.log"
        return 1
    fi
    _RTM_GITLESS_TREE_COPY="$copy"
}

# ---------------------------------------------------------------------------
# _rtm_ensure_tmuxless_tree_copy
# Clone the dev tree's current commit into a scratch copy for the
# tmux-less host -- a REAL, self-contained git repository (its own .git,
# not a worktree pointer back to this run's tree), so every git-dependent
# test still runs normally on this host. Only tmux is meant to be absent
# here; using the same no-.git archive the gitless host needs would make
# every git-dependent test on THIS host fail outright (git present but no
# repository to operate on) instead of behaving exactly as it does on the
# dev tree -- an artifact of this script's own tree construction, not a
# finding about the shipped estate.
# ---------------------------------------------------------------------------
_rtm_ensure_tmuxless_tree_copy() {
    [[ -n "${_RTM_TMUXLESS_TREE_COPY:-}" ]] && return 0
    local copy="${_RTM_SCRATCH}/tmuxless-tree-copy"
    mkdir -p "$copy"
    if ! git clone --no-hardlinks --quiet "$REPO_ROOT" "$copy" >"${_RTM_SCRATCH}/logs/tmuxless-tree-copy.log" 2>&1; then
        _RTM_TREE_COPY_ERROR="git clone of the dev tree failed -- see ${_RTM_SCRATCH}/logs/tmuxless-tree-copy.log"
        return 1
    fi
    _RTM_TMUXLESS_TREE_COPY="$copy"
}

# ---------------------------------------------------------------------------
# _rtm_run_container_host <host-name> <image> <package-to-remove>
# Construct (base image -> derived image -> tree copy), run both suites
# inside a --rm container, and record the result under "<host>@<image>".
# Any construction failure records not-attempted naming the specific
# resource that could not be built; it never falls through to a false pass
# or a bare crash.
# ---------------------------------------------------------------------------
_rtm_run_container_host() {
    local tree_host="$1" image="$2" package="$3"
    local host="${tree_host}@${image}"
    local start
    start=$(date +%s)

    _rtm_resolve_engine
    if [[ -z "${_RTM_ENGINE:-}" ]]; then
        RESULT_VERDICT[$host]="not-attempted"
        RESULT_NOTE[$host]="${_RTM_ENGINE_UNAVAILABLE}"
        RESULT_RUNTIME[$host]=$(( $(date +%s) - start ))
        return
    fi
    # Before-inspection: captured once, the first time any container host
    # actually has a working engine -- this is the "before" half of the
    # before-and-after inspection the report prints, taken before this
    # run's own images exist so a reader can see nothing of this run's
    # making was already there.
    if [[ -z "${_RTM_IMAGES_BEFORE_CAPTURED:-}" ]]; then
        _RTM_IMAGES_BEFORE="$(pgai_docker "${_RTM_ENGINE}" images --filter 'reference=pgai-kanban-host-matrix-*' --format '{{.Repository}}:{{.Tag}}' 2>/dev/null)"
        _RTM_CONTAINERS_BEFORE="$(pgai_docker "${_RTM_ENGINE}" ps -a --filter 'ancestor=pgai-kanban-host-matrix-*' --format '{{.ID}}' 2>/dev/null)"
        _RTM_IMAGES_BEFORE_CAPTURED=1
    fi

    if ! _rtm_ensure_base_image "$image"; then
        RESULT_VERDICT[$host]="not-attempted"
        RESULT_NOTE[$host]="${_RTM_BASE_BUILD_ERROR}"
        RESULT_RUNTIME[$host]=$(( $(date +%s) - start ))
        return
    fi

    local tag
    if ! _rtm_build_derived_image "$tree_host" "$image" "$package"; then
        RESULT_VERDICT[$host]="not-attempted"
        RESULT_NOTE[$host]="derived image build (removing ${package} from ${image}) failed -- see ${_RTM_SCRATCH}/logs/${tree_host}-${image}-build.log"
        RESULT_RUNTIME[$host]=$(( $(date +%s) - start ))
        return
    fi
    tag="${_RTM_DERIVED_TAG}"

    local tree_copy
    if [[ "$tree_host" == "gitless" ]]; then
        if ! _rtm_ensure_gitless_tree_copy; then
            RESULT_VERDICT[$host]="not-attempted"
            RESULT_NOTE[$host]="${_RTM_TREE_COPY_ERROR}"
            RESULT_RUNTIME[$host]=$(( $(date +%s) - start ))
            return
        fi
        tree_copy="${_RTM_GITLESS_TREE_COPY}"
    else
        if ! _rtm_ensure_tmuxless_tree_copy; then
            RESULT_VERDICT[$host]="not-attempted"
            RESULT_NOTE[$host]="${_RTM_TREE_COPY_ERROR}"
            RESULT_RUNTIME[$host]=$(( $(date +%s) - start ))
            return
        fi
        tree_copy="${_RTM_TMUXLESS_TREE_COPY}"
    fi

    local log="${_RTM_SCRATCH}/logs/${tree_host}-${image}.log"
    : > "$log"
    # A throwaway root INSIDE the disposable container's own filesystem --
    # unrelated to the host's real framework temp root, and gone the moment
    # the --rm container exits. Named to avoid any resemblance to that host
    # path rather than reuse its literal.
    local rootdir="/var/tmp/host-matrix-kanban-root"
    pgai_docker "${_RTM_ENGINE}" run --rm \
        -v "${tree_copy}:/workspace:rw" \
        -e "PGAI_AGENT_KANBAN_ROOT_PATH=${rootdir}" \
        -e "PGAI_DEV_TREE_PATH=/workspace" \
        -e "PYTEST_ADDOPTS=-ra" \
        --entrypoint bash \
        "$tag" \
        -lc "mkdir -p '${rootdir}' && cd /workspace && bash team/scripts/run-unit-tests.sh $([[ "$VERBOSE" == "true" ]] && echo --verbose)" \
        >>"$log" 2>&1
    local rc_unit=$?
    pgai_docker "${_RTM_ENGINE}" run --rm \
        -v "${tree_copy}:/workspace:rw" \
        -e "PGAI_AGENT_KANBAN_ROOT_PATH=${rootdir}" \
        -e "PGAI_DEV_TREE_PATH=/workspace" \
        -e "PYTEST_ADDOPTS=-ra" \
        --entrypoint bash \
        "$tag" \
        -lc "mkdir -p '${rootdir}' && cd /workspace && bash team/scripts/run-integration-tests.sh $([[ "$VERBOSE" == "true" ]] && echo --verbose)" \
        >>"$log" 2>&1
    local rc_integration=$?
    local rc=$rc_unit
    [[ "$rc_integration" -gt "$rc" ]] && rc=$rc_integration

    RESULT_RUNTIME[$host]=$(( $(date +%s) - start ))
    if [[ "$rc" -eq 2 ]]; then
        RESULT_VERDICT[$host]="not-attempted"
        RESULT_NOTE[$host]="pytest (or a required pre-flight tool) is not available inside the ${host} container -- see ${log}"
        return
    fi
    read -r p f s e < <(_rtm_parse_pytest_counts "$log")
    RESULT_PASSED[$host]=$p; RESULT_FAILED[$host]=$f; RESULT_SKIPPED[$host]=$s; RESULT_ERRORED[$host]=$e
    RESULT_CENSUS[$host]="$(_rtm_skip_census "$log")"
    if [[ "$rc" -eq 0 ]]; then
        RESULT_VERDICT[$host]="passed"
    else
        RESULT_VERDICT[$host]="failed"
    fi
}

# ---------------------------------------------------------------------------
# Run every requested host.
# ---------------------------------------------------------------------------
_RTM_TOTAL_START=$(date +%s)
_RTM_RESULT_KEYS=()
for _host in "${HOSTS[@]}"; do
    case "$_host" in
        dev)
            _RTM_RESULT_KEYS+=(dev)
            echo "=== [dev] starting ==="
            _rtm_run_host_dev
            echo "=== [dev] verdict: ${RESULT_VERDICT[dev]:-unknown} (${RESULT_RUNTIME[dev]:-0}s) ==="
            ;;
        gitless|tmuxless)
            _pkg=git
            [[ "$_host" == "tmuxless" ]] && _pkg=tmux
            for _image in "${CONTAINER_TIER_IMAGES[@]}"; do
                _key="${_host}@${_image}"
                _RTM_RESULT_KEYS+=("$_key")
                echo "=== [${_key}] starting ==="
                _rtm_run_container_host "$_host" "$_image" "$_pkg"
                echo "=== [${_key}] verdict: ${RESULT_VERDICT[$_key]:-unknown} (${RESULT_RUNTIME[$_key]:-0}s) ==="
            done
            ;;
    esac
done
unset _host _image _key _pkg
_RTM_TOTAL_RUNTIME=$(( $(date +%s) - _RTM_TOTAL_START ))

# --- Teardown BEFORE the after-inspection, so "after" means after cleanup ---
# Images and containers only here (not the scratch tree yet): the report
# below still needs to name the per-host log paths under the scratch tree.
# The EXIT trap tears the scratch tree down once the report has been
# printed and this process actually exits.
_rtm_teardown_images

# --- Before-and-after image/container inspection (printed as report evidence) ---
_RTM_IMAGES_AFTER=""
if [[ -n "${_RTM_ENGINE:-}" ]]; then
    _RTM_IMAGES_AFTER="$(pgai_docker "${_RTM_ENGINE}" images --filter "reference=pgai-kanban-host-matrix-*" --format '{{.Repository}}:{{.Tag}}' 2>/dev/null)"
    _RTM_CONTAINERS_AFTER="$(pgai_docker "${_RTM_ENGINE}" ps -a --filter "ancestor=pgai-kanban-host-matrix-*" --format '{{.ID}}' 2>/dev/null)"
fi

# ---------------------------------------------------------------------------
# Print the report.
# ---------------------------------------------------------------------------
echo ""
echo "############################################################"
echo "# Three-host green matrix report"
echo "############################################################"
_RTM_ANY_FAILED=false
_RTM_ANY_NOT_PASSED=false
for _host in "${_RTM_RESULT_KEYS[@]}"; do
    echo ""
    echo "--- host: ${_host} ---"
    echo "verdict:  ${RESULT_VERDICT[$_host]:-unknown}"
    echo "runtime:  ${RESULT_RUNTIME[$_host]:-0}s"
    case "${RESULT_VERDICT[$_host]:-unknown}" in
        passed|failed)
            echo "passed:   ${RESULT_PASSED[$_host]:-0}"
            echo "failed:   ${RESULT_FAILED[$_host]:-0}"
            echo "skipped:  ${RESULT_SKIPPED[$_host]:-0}"
            echo "errored:  ${RESULT_ERRORED[$_host]:-0}"
            echo "skip census:"
            if [[ -n "${RESULT_CENSUS[$_host]:-}" ]]; then
                echo "${RESULT_CENSUS[$_host]}"
            else
                echo "  (no skips)"
            fi
            [[ "${RESULT_VERDICT[$_host]}" == "failed" ]] && _RTM_ANY_FAILED=true
            [[ "${RESULT_VERDICT[$_host]}" != "passed" ]] && _RTM_ANY_NOT_PASSED=true
            ;;
        not-attempted)
            echo "reason:   ${RESULT_NOTE[$_host]:-unknown resource unavailable}"
            _RTM_ANY_NOT_PASSED=true
            ;;
        *)
            echo "reason:   internal error -- host did not record a verdict"
            _RTM_ANY_NOT_PASSED=true
            ;;
    esac
done
echo ""
echo "--- image/container inspection: before this run vs. after its teardown ---"
if [[ -n "${_RTM_ENGINE:-}" ]]; then
    echo "images before   -- matching this run's namespace: ${_RTM_IMAGES_BEFORE:-(none)}"
    echo "images after     -- matching this run's namespace: ${_RTM_IMAGES_AFTER:-(none)}"
    echo "containers before -- matching this run's namespace: ${_RTM_CONTAINERS_BEFORE:-(none)}"
    echo "containers after  -- matching this run's namespace: ${_RTM_CONTAINERS_AFTER:-(none)}"
else
    echo "(no container engine was used this run -- no image/container hosts were attempted)"
fi
echo ""
echo "total runtime: ${_RTM_TOTAL_RUNTIME}s"
echo "############################################################"

if [[ "$_RTM_ANY_FAILED" == "true" || "$_RTM_ANY_NOT_PASSED" == "true" ]]; then
    exit 1
fi
exit 0
