#!/usr/bin/env bash
# team/scripts/lib/container_common.sh
#
# Helpers the container lifecycle scripts (container-up.sh, container-down.sh,
# container-status.sh) share. Source it after the script has sourced its
# engine and ops libraries:
#
#   source "${_SCRIPT_DIR}/lib/container_common.sh"
#
#   container_first_line FILE  -- the first non-blank line of FILE (empty
#                                 when none).
#   container_kv_get KEY TEXT  -- the value of the first "KEY=value" line in
#                                 TEXT.
#   container_query_state      -- inspects container ${NAME} into
#                                 ${_WORK}/inspect.{out,err,rc} and sets
#                                 _STATE (running|stopped|absent) and
#                                 _STATE_IMAGE. A failed query that is not
#                                 "no such container" is refused through the
#                                 caller's _refuse_engine.
#
# container_query_state reads the caller's ENGINE, NAME, _WORK and _MODULE,
# and calls its pgai_docker (lib/docker_use.sh), pp_run_ops (lib/pp_run_ops.sh) and
# _refuse_engine WHAT DETAIL REMEDY, which each script defines with its own
# error key and census anchor.

container_first_line() {
    [[ -r "$1" ]] || return 0
    local line
    while IFS= read -r line || [[ -n "${line}" ]]; do
        [[ -n "${line//[[:space:]]/}" ]] && { printf '%s\n' "${line}"; return 0; }
    done < "$1"
    return 0
}

container_kv_get() {
    local key="$1" line
    while IFS= read -r line; do
        if [[ "${line}" == "${key}="* ]]; then
            printf '%s\n' "${line#"${key}="}"
            return 0
        fi
    done <<< "$2"
    return 0
}

container_query_state() {
    local rc=0 prc=0 parsed
    pgai_docker "${ENGINE}" container inspect "${NAME}" \
        >"${_WORK}/inspect.out" 2>"${_WORK}/inspect.err" || rc=$?
    printf '%s\n' "${rc}" > "${_WORK}/inspect.rc"
    parsed="$(pp_run_ops "${_MODULE}" container-state --name "${NAME}" --input "${_WORK}/inspect.out" \
        --exit-code "${rc}" --stderr-file "${_WORK}/inspect.err" 2>"${_WORK}/state.err")" || prc=$?
    if [[ "${prc}" -ne 0 && "${prc}" -ne 3 ]]; then
        _refuse_engine "the state query for container '${NAME}'" \
            "$(container_first_line "${_WORK}/state.err")" "$(printf '%q info' "${ENGINE}")"
    fi
    _STATE="$(container_kv_get state "${parsed}")"
    _STATE_IMAGE="$(container_kv_get image "${parsed}")"
}
