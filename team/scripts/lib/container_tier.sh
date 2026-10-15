#!/usr/bin/env bash
# team/scripts/lib/container_tier.sh
#
# Operator-only gate and image selection for the container tier: the
# harnesses that build images, pull base images, or start containers on the
# host (run-cold-build-tests.sh, run-three-host-matrix.sh,
# harness/strangers-path-check.sh, deep-verify.sh, and the cold-build-now.sh
# alias). That tier is the heaviest work the host does, and on a shared
# host it is the operator's call when it runs -- never an agent's.
#
# Authorization
# -------------
# A harness runs only when BOTH hold:
#   - the operator authorized it: PGAI_CONTAINER_TIER=1 is set in the
#     invoking environment (the operator's own interactive shell, a crontab
#     line the operator wrote, or the environment the operator started the
#     API server in), OR the operator passed --operator-approved on the
#     command line; and
#   - the invocation is not inside an agent wake (PGAI_AGENT_WAKE_ACTIVE is
#     not 1; the wake driver exports that signal into every agent session).
# The variable's value must be exactly "1"; anything else is "not
# authorized". Once a harness is authorized, this library exports
# PGAI_CONTAINER_TIER=1 so a harness it runs (deep-verify.sh running
# run-cold-build-tests.sh) inherits the authorization.
#
# An unauthorized call is NOT a failure. The harness prints one SKIPPED
# line (status, the machine key container_tier.operator_only, the reason
# in plain words, and the exact command an operator would run), appends a
# SKIPPED record to the docker-use log, and exits 0 -- an agent that
# reaches for the container tier never fails its caller and never stalls
# an RC. Nothing is built, pulled or started, and no verdict or ledger
# record is written: a skipped run is never reported as a pass.
#
# The wake bootstrap calls container_tier_scrub_env after it has sourced
# shell-env, claude-env, secrets and kanban.cfg, so an operator shell that
# exports the authorization never hands it to an agent the wake spawns.
#
# Image selection
# ---------------
# The images a harness may build are the flavors declared in
# team/config/container-images.txt (one docker/<flavor> directory name per
# line). The operator picks which ones a run builds:
#   --image NAME   build NAME (repeatable; a comma-separated list is also
#                  accepted, e.g. --image debian,rocky9)
#   --image all    build every image the manifest declares
# With no --image flag, a selection inherited from a parent harness
# (PGAI_CONTAINER_IMAGES, below) applies, and otherwise kanban.cfg
# [release] container_images supplies the default (same vocabulary: names
# separated by commas or spaces, or "all"). With none of these, the
# harness lists the available images and exits 0 without building
# anything. An image name the manifest does not declare is refused before
# anything is built.
#
# The selection is exported as PGAI_CONTAINER_IMAGES (space-separated, in
# manifest order) for the processes a harness starts -- the pytest cold
# lane reads it to parametrize its builds, and a harness run inside it
# (strangers-path-check.sh under the cold lane) builds the same images,
# the way it inherits the authorization.
#
# Public API
# ----------
#   container_tier_take_args [args...]
#       Separates the container-tier flags (--operator-approved, --image
#       NAME, --image=NAME) from a harness's own arguments. Sets
#       CONTAINER_TIER_OPERATOR_APPROVED (0|1), CONTAINER_TIER_IMAGE_REQUEST
#       (array of the requested names, as typed), CONTAINER_TIER_REST_ARGS
#       (array of every other argument, order preserved) and
#       CONTAINER_TIER_PASSTHROUGH_ARGS (the container-tier flags again, in
#       argv form, for a harness that re-invokes itself or another
#       harness). Call it before the harness parses its own flags, then
#       parse CONTAINER_TIER_REST_ARGS.
#
#   container_tier_authorized
#       Returns 0 when the invocation is authorized (rules above), 1
#       otherwise. Prints nothing.
#
#   container_tier_require_operator <script-path> [original-args...]
#       Returns 0 when authorized (and exports PGAI_CONTAINER_TIER=1).
#       Otherwise prints the one-line SKIPPED narration to stdout, records
#       a SKIPPED docker-use event, and exits the calling script with
#       status 0. <script-path> and the original arguments are used to
#       build the command an operator would run. Call it after --help
#       handling and before the first engine, build, pull, or run command.
#
#   container_tier_select_images <script-path> [original-args...]
#       Resolves the image selection (flag, then inherited
#       PGAI_CONTAINER_IMAGES, then kanban.cfg default) into
#       CONTAINER_TIER_IMAGES (array, manifest order) and exports
#       PGAI_CONTAINER_IMAGES. With nothing selected it lists the available
#       images and exits the calling script with status 0. An undeclared
#       name refuses dual-channel and exits with
#       CONTAINER_TIER_IMAGE_REFUSAL_EXIT (3). Call it after
#       container_tier_require_operator.
#
#   container_tier_manifest_images
#       Echoes the manifest's flavor names, one per line, in file order.
#
#   container_tier_operator_command <script-path> [args...]
#       Echoes the shell-quoted command an operator runs from their own
#       shell: PGAI_CONTAINER_TIER=1 bash <script-path> [args...]. Any
#       --operator-approved in args is dropped (the prefix already
#       authorizes).
#
#   container_tier_scrub_env
#       Unsets the authorization variable in the current shell, so no
#       process started from it afterwards inherits it.

CONTAINER_TIER_AUTH_VAR="PGAI_CONTAINER_TIER"
CONTAINER_TIER_IMAGE_REFUSAL_EXIT=3

_CONTAINER_TIER_LIB_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# team/scripts/lib -> team/config (dev tree) and scripts/lib -> config
# (installed root): the manifest is two directories up from this file.
CONTAINER_TIER_MANIFEST="${_CONTAINER_TIER_LIB_DIR}/../../config/container-images.txt"

if ! declare -F pgai_emit_error_contract >/dev/null 2>&1; then
    # shellcheck source=error_contract.sh
    source "${_CONTAINER_TIER_LIB_DIR}/error_contract.sh"
fi
if ! declare -F docker_use_record >/dev/null 2>&1; then
    # shellcheck source=docker_use.sh
    source "${_CONTAINER_TIER_LIB_DIR}/docker_use.sh"
fi

CONTAINER_TIER_OPERATOR_APPROVED="${CONTAINER_TIER_OPERATOR_APPROVED:-0}"
CONTAINER_TIER_IMAGE_REQUEST=()
CONTAINER_TIER_REST_ARGS=()
CONTAINER_TIER_PASSTHROUGH_ARGS=()
CONTAINER_TIER_IMAGES=()

container_tier_take_args() {
    CONTAINER_TIER_IMAGE_REQUEST=()
    CONTAINER_TIER_REST_ARGS=()
    CONTAINER_TIER_PASSTHROUGH_ARGS=()
    while [[ $# -gt 0 ]]; do
        case "$1" in
            --operator-approved)
                CONTAINER_TIER_OPERATOR_APPROVED=1
                shift
                ;;
            --image)
                # A missing value is kept as an empty request so the
                # selection step refuses it, naming the manifest list.
                CONTAINER_TIER_IMAGE_REQUEST+=("${2-}")
                if [[ $# -ge 2 ]]; then shift 2; else shift; fi
                ;;
            --image=*)
                CONTAINER_TIER_IMAGE_REQUEST+=("${1#--image=}")
                shift
                ;;
            *)
                CONTAINER_TIER_REST_ARGS+=("$1")
                shift
                ;;
        esac
    done
    [[ "${CONTAINER_TIER_OPERATOR_APPROVED}" == "1" ]] && CONTAINER_TIER_PASSTHROUGH_ARGS+=(--operator-approved)
    local _ct_req
    for _ct_req in ${CONTAINER_TIER_IMAGE_REQUEST[@]+"${CONTAINER_TIER_IMAGE_REQUEST[@]}"}; do
        CONTAINER_TIER_PASSTHROUGH_ARGS+=(--image "${_ct_req}")
    done
    return 0
}

container_tier_authorized() {
    [[ "${PGAI_AGENT_WAKE_ACTIVE:-0}" == "1" ]] && return 1
    [[ "${PGAI_CONTAINER_TIER:-}" == "1" ]] && return 0
    [[ "${CONTAINER_TIER_OPERATOR_APPROVED:-0}" == "1" ]] && return 0
    return 1
}

container_tier_operator_command() {
    local script_path="${1:?container_tier_operator_command: script path required}"
    shift
    local -a args=()
    local arg
    for arg in "$@"; do
        [[ "$arg" == "--operator-approved" ]] && continue
        args+=("$arg")
    done
    # An absolute path, so the command runs as printed from any directory.
    if [[ "${script_path}" != /* && -e "${script_path}" ]]; then
        script_path="$(cd "$(dirname "${script_path}")" && pwd)/$(basename "${script_path}")"
    fi
    local cmd
    cmd="$(printf '%q ' bash "${script_path}" ${args[@]+"${args[@]}"})"
    printf '%s=1 %s\n' "${CONTAINER_TIER_AUTH_VAR}" "${cmd% }"
}

container_tier_require_operator() {
    local script_path="${1:?container_tier_require_operator: script path required}"
    shift
    if container_tier_authorized; then
        export PGAI_CONTAINER_TIER=1
        return 0
    fi

    local reason
    if [[ "${PGAI_AGENT_WAKE_ACTIVE:-0}" == "1" ]]; then
        reason="this invocation is inside an agent wake, and agents never run the container tier"
    else
        reason="${CONTAINER_TIER_AUTH_VAR}=1 is not set and --operator-approved was not passed"
    fi
    local script_name
    script_name="$(basename "${script_path}")"

    # CONVERT (quality class, team/SOP-core.md "Blocking Severity"): an
    # unauthorized call is narrated and skipped, never refused. The SKIPPED
    # line carries the machine key and the plain-language reason on one
    # line; nothing was built, pulled or started, no verdict or ledger
    # record is written, and the caller sees exit 0, so an agent that
    # reaches for the container tier never fails its task or stalls an RC.
    printf '%s: SKIPPED key=container_tier.operator_only reason="container tier is operator-only: %s; nothing was built, pulled or started" operator_command="%s"\n' \
        "${script_name}" "${reason}" "$(container_tier_operator_command "${script_path}" "$@")"
    docker_use_record SKIPPED "${script_name}" "-"
    exit 0
}

container_tier_manifest_images() {
    [[ -f "${CONTAINER_TIER_MANIFEST}" ]] || return 1
    local line name
    local -A seen=()
    while IFS= read -r line || [[ -n "$line" ]]; do
        name="${line#"${line%%[![:space:]]*}"}"
        name="${name%"${name##*[![:space:]]}"}"
        [[ -z "$name" || "$name" == \#* ]] && continue
        [[ -n "${seen[$name]:-}" ]] && continue
        seen["$name"]=1
        printf '%s\n' "$name"
    done < "${CONTAINER_TIER_MANIFEST}"
}

# _container_tier_cfg_default: echoes kanban.cfg [release] container_images
# (empty when the file, section or key is absent).
_container_tier_cfg_default() {
    local cfg="${PGAI_AGENT_KANBAN_ROOT_PATH:-${HOME}/pgai_agent_kanban}/kanban.cfg"
    [[ -f "$cfg" ]] || return 0
    if ! declare -F read_ini >/dev/null 2>&1; then
        # shellcheck source=ini_parser.sh
        source "${_CONTAINER_TIER_LIB_DIR}/ini_parser.sh" 2>/dev/null || return 0
    fi
    read_ini "$cfg" release container_images "" 2>/dev/null || true
}

container_tier_select_images() {
    local script_path="${1:?container_tier_select_images: script path required}"
    shift
    local script_name
    script_name="$(basename "${script_path}")"
    CONTAINER_TIER_IMAGES=()

    local -a manifest=()
    mapfile -t manifest < <(container_tier_manifest_images)
    local manifest_list="${manifest[*]:-(none declared)}"

    # Requested names: the --image flags, else a selection inherited from a
    # parent harness, else the kanban.cfg default.
    local -a requested=()
    local source_label="--image"
    local entry
    local -a parts=()
    if [[ ${#CONTAINER_TIER_IMAGE_REQUEST[@]} -gt 0 ]]; then
        for entry in "${CONTAINER_TIER_IMAGE_REQUEST[@]}"; do
            if [[ -z "$entry" ]]; then
                requested+=("")
                continue
            fi
            IFS=$', \t' read -r -a parts <<< "$entry"
            requested+=(${parts[@]+"${parts[@]}"})
        done
    elif [[ -n "${PGAI_CONTAINER_IMAGES:-}" ]]; then
        source_label="PGAI_CONTAINER_IMAGES (inherited from a parent harness)"
        IFS=$', \t' read -r -a parts <<< "${PGAI_CONTAINER_IMAGES}"
        requested+=(${parts[@]+"${parts[@]}"})
    else
        local cfg_value
        cfg_value="$(_container_tier_cfg_default)"
        source_label="kanban.cfg [release] container_images"
        IFS=$', \t' read -r -a parts <<< "$cfg_value"
        requested+=(${parts[@]+"${parts[@]}"})
    fi

    if [[ ${#requested[@]} -eq 0 ]]; then
        printf '%s: no container image selected -- nothing was built.\n' "${script_name}"
        printf '%s: available images (%s): %s\n' "${script_name}" "team/config/container-images.txt" "${manifest_list}"
        printf '%s: choose with --image NAME (repeatable) or --image all, or set kanban.cfg [release] container_images. Example:\n' "${script_name}"
        printf '  %s\n' "$(container_tier_operator_command "${script_path}" "$@" --image all)"
        exit 0
    fi

    local -A wanted=()
    local -a unknown=()
    local name m found
    for name in "${requested[@]}"; do
        if [[ "$name" == "all" ]]; then
            for m in "${manifest[@]}"; do wanted["$m"]=1; done
            continue
        fi
        found=0
        for m in "${manifest[@]}"; do
            [[ "$m" == "$name" ]] && { found=1; break; }
        done
        if [[ "$found" -eq 1 ]]; then
            wanted["$name"]=1
        else
            unknown+=("'${name}'")
        fi
    done

    if [[ ${#unknown[@]} -gt 0 ]]; then
        local -a kept_args=()
        local skip_next=0 arg
        for arg in "$@"; do
            if [[ "$skip_next" -eq 1 ]]; then skip_next=0; continue; fi
            case "$arg" in
                --image) skip_next=1; continue ;;
                --image=*) continue ;;
            esac
            kept_args+=("$arg")
        done
        docker_use_record REFUSED "${script_name}" "$(IFS=,; echo "${requested[*]}")" "reason=unknown-image"
        # BLOCK-WORTHY: invariant by design -- the manifest is the one
        # declared list of images the container tier builds, so a name it
        # does not declare is never guessed at or built. The refusal fires
        # before any engine command; nothing was built, pulled or started.
        pgai_emit_error_contract \
            "container_tier.unknown_image" \
            "${script_name} refused: ${source_label} names ${unknown[*]}, which team/config/container-images.txt does not declare. Declared images: ${manifest_list}. Nothing was built, pulled or started." \
            "$(container_tier_operator_command "${script_path}" ${kept_args[@]+"${kept_args[@]}"} --image all)"
        # CENSUS-ANCHOR: container-tier-unknown-image-01
        exit "${CONTAINER_TIER_IMAGE_REFUSAL_EXIT}"
    fi

    for m in "${manifest[@]}"; do
        [[ -n "${wanted[$m]:-}" ]] && CONTAINER_TIER_IMAGES+=("$m")
    done
    export PGAI_CONTAINER_IMAGES="${CONTAINER_TIER_IMAGES[*]}"
    return 0
}

container_tier_scrub_env() {
    unset PGAI_CONTAINER_TIER
}
