#!/usr/bin/env bash
# team/scripts/lib/push_decision.sh
# Bash surface over the shared push-decision resolver
# (pgai_agent_kanban.lib.push_decision): whether an out-of-band push site
# may push a project's refs to its remote.
#
# Every out-of-band push site (OVERWATCH check-push-lag.sh, CM
# push-watchdog.sh) decides through pp_push_decision, so all of them apply
# one rule. The rule lives in the Python module's docstring; in short, a
# project resolves only when it is registered in projects.cfg, its own
# project.cfg (or legacy PROJECT.cfg) exists, and that file names a
# dev_tree_path. An inherited PGAI_DEV_TREE_PATH is never consulted.
#
# Source this file to get pp_push_decision:
#   source "$(dirname "${BASH_SOURCE[0]}")/push_decision.sh"
#
# pp_push_decision <project>
#   Requires KANBAN_ROOT. Always returns 0 and sets, in the caller's scope:
#     PP_PUSH_DECISION     push | no-push | unresolved
#     PP_PUSH_DEV_TREE     the project.cfg dev_tree_path ("" unless resolved)
#     PP_PUSH_CFG_FILE     the project config file read ("" when none)
#     PP_PUSH_MISSING      "" when resolved, else registration | project-cfg |
#                          dev-tree-path | resolver
#     PP_PUSH_STATUS_LINE  "" when resolved, else the one line to log:
#                          "skipped-unresolved-project: project '<name>': ..."
#   A site pushes only when PP_PUSH_DECISION is "push". "no-push" is the
#   push_to_remote = false posture (staged by design). "unresolved" means
#   push nothing, contact no remote, and log PP_PUSH_STATUS_LINE.
#
#   Fails closed: when KANBAN_ROOT is unset or the resolver cannot run or
#   answers with something unrecognised, the decision is "unresolved" with
#   PP_PUSH_MISSING=resolver -- never "push".

# shellcheck source=pp_run_ops.sh
if ! declare -F pp_run_ops >/dev/null 2>&1; then
    source "$(dirname "${BASH_SOURCE[0]}")/pp_run_ops.sh"
fi

pp_push_decision() {
    local _ppd_project="${1:-}"

    PP_PUSH_DECISION="unresolved"
    PP_PUSH_DEV_TREE=""
    PP_PUSH_CFG_FILE=""
    PP_PUSH_MISSING="resolver"
    PP_PUSH_STATUS_LINE="skipped-unresolved-project: project '${_ppd_project}': missing resolver: the push-decision resolver did not answer; nothing pushed and no remote contacted"

    if [[ -z "${KANBAN_ROOT:-}" ]]; then
        PP_PUSH_STATUS_LINE="skipped-unresolved-project: project '${_ppd_project}': missing resolver: KANBAN_ROOT is not set; nothing pushed and no remote contacted"
        return 0
    fi

    local _ppd_out="" _ppd_rc=0
    _ppd_out="$(pp_run_ops pgai_agent_kanban.lib.push_decision \
        --kanban-root "${KANBAN_ROOT}" --project "${_ppd_project}" 2>/dev/null)" || _ppd_rc=$?
    if (( _ppd_rc != 0 )); then
        PP_PUSH_STATUS_LINE="skipped-unresolved-project: project '${_ppd_project}': missing resolver: the push-decision resolver (pgai_agent_kanban.lib.push_decision) exited ${_ppd_rc}; nothing pushed and no remote contacted"
        return 0
    fi

    local _ppd_decision="" _ppd_dev_tree="" _ppd_cfg="" _ppd_missing="" _ppd_line=""
    local _ppd_row _ppd_key _ppd_val
    while IFS= read -r _ppd_row; do
        # Split on the first '=' only; a value may itself contain '='.
        _ppd_key="${_ppd_row%%=*}"
        _ppd_val="${_ppd_row#*=}"
        case "${_ppd_key}" in
            decision)    _ppd_decision="${_ppd_val}" ;;
            dev_tree)    _ppd_dev_tree="${_ppd_val}" ;;
            cfg_file)    _ppd_cfg="${_ppd_val}" ;;
            missing)     _ppd_missing="${_ppd_val}" ;;
            status_line) _ppd_line="${_ppd_val}" ;;
        esac
    done <<< "${_ppd_out}"

    case "${_ppd_decision}" in
        push|no-push)
            PP_PUSH_DECISION="${_ppd_decision}"
            PP_PUSH_DEV_TREE="${_ppd_dev_tree}"
            PP_PUSH_CFG_FILE="${_ppd_cfg}"
            PP_PUSH_MISSING=""
            PP_PUSH_STATUS_LINE=""
            ;;
        unresolved)
            PP_PUSH_CFG_FILE="${_ppd_cfg}"
            PP_PUSH_MISSING="${_ppd_missing}"
            PP_PUSH_STATUS_LINE="${_ppd_line}"
            ;;
        *)
            # Unrecognised answer: keep the fail-closed defaults set above.
            ;;
    esac
    return 0
}
