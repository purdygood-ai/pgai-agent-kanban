#!/usr/bin/env bash
# team/scripts/validate-workflow.sh
#
# Operator gate: validates a workflow plugin before it is flipped to
# status = ready for live use.
#
# This command runs the four plugin contract checks (manifest validity,
# hook presence, stub detection, capability-value validity) against the
# named workflow type in the live workflows directory. It requires only
# bash and the pgai-agent-kanban lib files — no dev tree, no Python, no
# pytest.
#
# Use this command after authoring a new workflow plugin and before setting
# status = ready in workflow.cfg. If the command exits 0, the plugin
# satisfies every structural contract the framework requires.
#
# Exit codes
# ----------
#   0   All four contract checks passed. The plugin is structurally valid.
#   1   One or more contract checks failed. The failure reason is printed
#       to stdout naming the violated check and the specific problem.
#   2   Usage error (unknown flag, missing --type argument, etc.).
#
# Usage
# -----
#   validate-workflow.sh --type <name> [--workflows-dir <dir>]
#   validate-workflow.sh --help
#
# Examples
# --------
#   # Validate the "acme-deploy" plugin under the live-install workflows dir:
#   validate-workflow.sh --type acme-deploy
#
#   # Validate against a custom workflows directory:
#   validate-workflow.sh --type acme-deploy --workflows-dir /path/to/workflows
#
#   # Validate a plugin in a dev tree (auto-resolved when TEAM_ROOT is set):
#   validate-workflow.sh --type testing-only

set -euo pipefail
# shellcheck source=lib/env_bootstrap.sh
source "$(dirname "${BASH_SOURCE[0]}")/lib/env_bootstrap.sh"

# ---------------------------------------------------------------------------
# Script identity and lib root
# ---------------------------------------------------------------------------
_SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
_LIB_DIR="${_SCRIPT_DIR}/lib"

# ---------------------------------------------------------------------------
# Usage / help
# ---------------------------------------------------------------------------
_usage() {
    cat <<'EOF'
Usage: validate-workflow.sh --type <name> [--workflows-dir <dir>]
       validate-workflow.sh --help

Validates a workflow plugin against the pgai-agent-kanban plugin contract.

A plugin passes when all four contract checks succeed:
  1. Manifest validity     — workflow.cfg exists, has required fields,
                             status = ready (not scaffold or absent).
  2. Hook presence         — workflow.sh defines all eight wf_* hooks.
  3. Stub detection        — no hook body contains "NOT IMPLEMENTED"
                             (the scaffold marker the generator inserts).
  4. Capability validity   — git_mode, version_semantics (when set), and
                             finalize (when set) have allowed values.

Exits 0 when the plugin passes all checks.
Exits 1 with a plain-text reason when any check fails.
Exits 2 on usage errors.

Options:
  --type <name>            Required. The workflow type name to validate.
                           Must match a subdirectory under the workflows
                           root that contains workflow.cfg and workflow.sh.

  --workflows-dir <dir>    Optional. Override the workflows root directory.
                           Defaults to:
                             1. $PGAI_AGENT_KANBAN_ROOT_PATH/workflows/
                                (live-install path)
                             2. team/workflows/ relative to the dev tree
                                (when running inside the kanban source tree)

  --help                   Print this message and exit 0.

Contract check details:

  Manifest validity checks that workflow.cfg is present and contains:
    [workflow]     name, status
    [capabilities] git_mode, agents
  The status must be "ready"; "scaffold" and any other value fail.

  Hook presence checks that workflow.sh defines every wf_* function:
    wf_resolve_target_version, wf_git_mode, wf_pre_task, wf_post_task,
    wf_finalize, wf_agents, wf_bundle_source_branch, wf_dashboard_render

  Stub detection scans each hook body for the literal "NOT IMPLEMENTED".
  The workflow generator places this marker in scaffold outputs. Any
  unimplemented hook causes this check to fail.

  Capability validity confirms that:
    git_mode         is one of: none, ro, rw
    version_semantics (when set) is one of: semver, label, none
    finalize         (when set) is one of: tag, publish, report

Examples:
  # Validate a plugin on a live install:
  validate-workflow.sh --type acme-deploy

  # Validate against a custom workflows root:
  validate-workflow.sh --type acme-deploy --workflows-dir /opt/mykanban/workflows

  # Validate a dev-tree plugin (workflows root auto-resolved):
  validate-workflow.sh --type testing-only
EOF
}

# ---------------------------------------------------------------------------
# Argument parsing
# ---------------------------------------------------------------------------
_type_name=""
_workflows_dir=""

while [[ $# -gt 0 ]]; do
    case "$1" in
        --help|-h)
            _usage
            exit 0
            ;;
        --type)
            if [[ $# -lt 2 || -z "$2" ]]; then
                echo "validate-workflow.sh: --type requires a non-empty argument" >&2
                exit 2
            fi
            _type_name="$2"
            shift 2
            ;;
        --type=*)
            _type_name="${1#--type=}"
            if [[ -z "$_type_name" ]]; then
                echo "validate-workflow.sh: --type= requires a non-empty value" >&2
                exit 2
            fi
            shift
            ;;
        --workflows-dir)
            if [[ $# -lt 2 || -z "$2" ]]; then
                echo "validate-workflow.sh: --workflows-dir requires a non-empty argument" >&2
                exit 2
            fi
            _workflows_dir="$2"
            shift 2
            ;;
        --workflows-dir=*)
            _workflows_dir="${1#--workflows-dir=}"
            if [[ -z "$_workflows_dir" ]]; then
                echo "validate-workflow.sh: --workflows-dir= requires a non-empty value" >&2
                exit 2
            fi
            shift
            ;;
        --)
            shift
            break
            ;;
        -*)
            echo "validate-workflow.sh: unknown option: $1" >&2
            echo "Run 'validate-workflow.sh --help' for usage." >&2
            exit 2
            ;;
        *)
            echo "validate-workflow.sh: unexpected positional argument: $1" >&2
            echo "Run 'validate-workflow.sh --help' for usage." >&2
            exit 2
            ;;
    esac
done

if [[ -z "$_type_name" ]]; then
    echo "validate-workflow.sh: --type <name> is required" >&2
    echo "Run 'validate-workflow.sh --help' for usage." >&2
    exit 2
fi

# ---------------------------------------------------------------------------
# Source lib dependencies
# ---------------------------------------------------------------------------

# ini_parser.sh: needed by workflow-contract.sh.
if [[ ! -f "${_LIB_DIR}/ini_parser.sh" ]]; then
    echo "validate-workflow.sh: cannot find ${_LIB_DIR}/ini_parser.sh — verify the install is intact" >&2
    exit 1
fi
# shellcheck source=lib/ini_parser.sh
source "${_LIB_DIR}/ini_parser.sh"

# workflow-contract.sh: the shared contract-check library.
if [[ ! -f "${_LIB_DIR}/workflow-contract.sh" ]]; then
    echo "validate-workflow.sh: cannot find ${_LIB_DIR}/workflow-contract.sh — verify the install is intact" >&2
    exit 1
fi
# shellcheck source=lib/workflow-contract.sh
source "${_LIB_DIR}/workflow-contract.sh"

# ---------------------------------------------------------------------------
# Resolve the workflows root directory
# ---------------------------------------------------------------------------

_resolve_workflows_root() {
    local explicit_dir="${1:-}"

    # Explicit override takes priority.
    if [[ -n "$explicit_dir" ]]; then
        if [[ -d "$explicit_dir" ]]; then
            printf '%s' "$explicit_dir"
            return 0
        else
            echo "validate-workflow.sh: --workflows-dir does not exist: ${explicit_dir}" >&2
            return 1
        fi
    fi

    # Live-install path: $PGAI_AGENT_KANBAN_ROOT_PATH/workflows/.
    if [[ -n "${PGAI_AGENT_KANBAN_ROOT_PATH}" ]]; then
        local _live="${PGAI_AGENT_KANBAN_ROOT_PATH}/workflows"
        if [[ -d "$_live" ]]; then
            printf '%s' "$_live"
            return 0
        fi
    fi

    # Dev-tree path: team/workflows/ relative to TEAM_ROOT or this script's
    # grandparent. The script lives at team/scripts/validate-workflow.sh,
    # so one directory up is team/scripts/ and two directories up is team/.
    local _team_root="${TEAM_ROOT:-}"
    if [[ -z "$_team_root" ]]; then
        # Two levels up from _SCRIPT_DIR is the repo root; team/ is then a
        # sibling of team/scripts/. But _SCRIPT_DIR IS team/scripts/, so
        # one level up is team/. Check both.
        _team_root="$(cd "${_SCRIPT_DIR}/.." && pwd)"
    fi
    local _dev_dir="${_team_root}/workflows"
    if [[ -d "$_dev_dir" ]]; then
        printf '%s' "$_dev_dir"
        return 0
    fi

    echo "validate-workflow.sh: workflows root not found (checked PGAI_AGENT_KANBAN_ROOT_PATH/workflows/, team/workflows/); use --workflows-dir to specify the path" >&2
    return 1
}

_workflows_root=""
if ! _workflows_root="$(_resolve_workflows_root "$_workflows_dir")"; then
    exit 1
fi

# ---------------------------------------------------------------------------
# Locate the plugin directory
# ---------------------------------------------------------------------------

_plugin_dir="${_workflows_root}/${_type_name}"

if [[ ! -d "$_plugin_dir" ]]; then
    echo "FAIL: workflow type '${_type_name}' not found — no directory at ${_plugin_dir}"
    exit 1
fi

# ---------------------------------------------------------------------------
# Run the contract checks
# ---------------------------------------------------------------------------

echo "Validating workflow plugin: ${_type_name}"
echo "Plugin directory: ${_plugin_dir}"
echo ""

if wfc_check_all "$_plugin_dir" "$_type_name"; then
    echo "PASS: workflow type '${_type_name}' satisfies all contract checks."
    exit 0
else
    echo "FAIL: ${WFC_ERROR}"
    exit 1
fi
