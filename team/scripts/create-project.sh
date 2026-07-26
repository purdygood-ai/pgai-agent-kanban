#!/usr/bin/env bash
# scripts/create-project.sh
#
# Bootstrap a new project under $KANBAN_ROOT/projects/<name>/.
#
# Creates the standard project layout: project.cfg (INI), queue files, requirements/,
# bugs/, priority/ (each with a templates/ subdir and README.md), an empty
# release-state.md, and registers the project in projects.cfg.
#
# New projects work out of the box: project.cfg is seeded with max_patch=21,
# max_minor=13, max_major=0 — permissive patch/minor ceilings so a fresh project
# demonstrates the chain immediately, while major releases remain an operator-only
# gate. Ceilings are operator checkpoints; raise or lower per project as needed.
#
# Path fields (dev_tree_path, git_repo_url) are written empty by default.
# Supply --dev-tree/--git-repo (see below) to fill them in at creation time,
# or edit project.cfg manually afterward.
#
# projects.cfg format handling:
#   When projects.cfg is in colon-legacy format, this script automatically
#   converts it to INI format before registering the new project (auto-migrate).
#   To suppress migration and append in legacy colon format instead, pass
#   --no-migrate. A mixed-format warning is emitted in that case because the
#   resulting file will have an INI section and colon lines, which is not
#   a supported stable state.
#
# Usage:
#   create-project.sh --project <name>                          # uses all defaults
#   create-project.sh --project <name> --workflow-type <type>   # override workflow_type (default: release)
#   create-project.sh --project <name> --max-patch <N>          # override max_patch (default: 21)
#   create-project.sh --project <name> --max-minor <N>          # override max_minor (default: 13)
#   create-project.sh --project <name> --max-major <N>          # override max_major (default: 0)
#   create-project.sh --project <name> --git-remote <name>      # override git_remote_name (default: origin)
#   create-project.sh --project <name> --priority <int>         # registry priority (default: next available)
#   create-project.sh --project <name> --color '#RRGGBB'        # registry display color (default: next unused palette entry)
#                                                               # (quote hex colors: unquoted '#' is treated as a shell comment)
#   create-project.sh --project <name> --dev-tree <path>        # write dev_tree_path (alias: --dev-tree-path)
#   create-project.sh --project <name> --git-repo <url>         # write git_repo_url (alias: --git-repo-url)
#   create-project.sh --project <name> --no-migrate             # skip auto-migration; append colon line (not recommended)
#   create-project.sh --project <name> --dry-run                # preview, no writes
#
# Path fields (--dev-tree / --dev-tree-path, --git-repo / --git-repo-url):
#   When supplied, the value is written verbatim into project.cfg (only a
#   non-empty check is applied — no further validation). When --dev-tree
#   names a path that does not currently exist on this host, the script WARNs
#   (naming the path) but still writes the value and exits 0 — projects are
#   often registered before their dev tree is cloned. When either flag is
#   omitted, its field is written empty exactly as before, and you edit
#   project.cfg manually to supply it later.
#
# Defaults written to project.cfg:
#   workflow_type=release
#   git_remote_name=origin
#   max_patch=21            (permissive; working out of the box)
#   max_minor=13            (permissive; ceilings are operator checkpoints)
#   max_major=0             (operator gate; raise explicitly when ready)
#   dev_tree_path=          (empty unless --dev-tree/--dev-tree-path given)
#   git_repo_url=           (empty unless --git-repo/--git-repo-url given)
#
# Typical operator workflow:
#   1. create-project.sh --project <name> [--workflow-type ...] [--max-minor ...]
#   2. $EDITOR projects/<name>/project.cfg   (set dev_tree_path, git_repo_url)
#   3. Drop requirements docs in projects/<name>/requirements/
#   4. Chain runs immediately; adjust ceilings as needed:
#      set-version-ceiling.sh --project <name> --minor N  # raise or lower per project
#
# This is idempotent in spirit: if the project directory already exists,
# the script aborts with an error rather than overwriting. To re-register
# an existing on-disk project that's missing from projects.cfg, use
# add-project.sh instead.
#
# Exit codes:
#   0 — project bootstrapped (or dry-run preview completed)
#   1 — usage error or missing required arguments
#   2 — project directory already exists
#   3 — script invoked outside a kanban install (KANBAN_ROOT not set/found)

set -euo pipefail
# shellcheck source=lib/env_bootstrap.sh
source "$(dirname "${BASH_SOURCE[0]}")/lib/env_bootstrap.sh"

# --- Resolve kanban root ---
KANBAN_ROOT="${PGAI_AGENT_KANBAN_ROOT_PATH}"

if [[ ! -d "$KANBAN_ROOT" ]]; then
    echo "ERROR: kanban root not found: $KANBAN_ROOT" >&2
    echo "Set PGAI_AGENT_KANBAN_ROOT_PATH or run install.sh" >&2
    exit 3
fi

# Source helpers
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=lib/projects.sh
source "${SCRIPT_DIR}/lib/projects.sh"
# shellcheck source=lib/operator_args.sh
source "${SCRIPT_DIR}/lib/operator_args.sh"
# shellcheck source=lib/workflow.sh
source "${SCRIPT_DIR}/lib/workflow.sh"

# ---------------------------------------------------------------------------
# _validate_non_negative_integer <flag-name> <value>
# Exits 1 if the value is not a non-negative integer.
# ---------------------------------------------------------------------------
_validate_non_negative_integer() {
    local flag="$1" value="$2"
    if ! [[ "$value" =~ ^[0-9]+$ ]]; then
        echo "ERROR: ${flag} requires a non-negative integer; got: '${value}'" >&2
        exit 1
    fi
}

# ---------------------------------------------------------------------------
# _validate_non_empty <flag-name> <value>
# Exits 1 if value is empty (e.g. the operator passed --flag= explicitly).
# ---------------------------------------------------------------------------
_validate_non_empty() {
    local flag="$1" value="$2"
    if [[ -z "$value" ]]; then
        echo "ERROR: ${flag} requires a non-empty value." >&2
        exit 1
    fi
}

# --- Default values ---
NAME=""
WORKFLOW="release"
GIT_REMOTE_NAME="origin"
MAX_PATCH="21"
MAX_MINOR="13"
MAX_MAJOR="0"
PRIORITY=""
COLOR=""
DRY_RUN="false"
NO_MIGRATE="false"
DEV_TREE_PATH=""
GIT_REPO_URL=""

# Declared flag vocabulary for all flags this command accepts.
OPERATOR_VALID_FLAGS=(project dry-run help workflow-type max-patch max-minor max-major git-remote priority color no-migrate dev-tree dev-tree-path git-repo git-repo-url h)

# --- Parse args ---
# Value-taking flags: project plus script-specific
#   (workflow-type, max-patch, max-minor, max-major, git-remote, priority, color,
#   dev-tree/dev-tree-path, git-repo/git-repo-url — the last two pairs are
#   flag/alias pairs that write dev_tree_path/git_repo_url into project.cfg).
# Boolean flags: dry-run, no-migrate, force, yes, help.
argparse_parse \
    --value-flags "project workflow-type max-patch max-minor max-major git-remote priority color dev-tree dev-tree-path git-repo git-repo-url" \
    -- "$@"

# Emit clear errors for value-taking flags given with no value.
for _vf in workflow-type max-patch max-minor max-major git-remote priority color dev-tree dev-tree-path git-repo git-repo-url; do
    if argparse_missing "$_vf"; then
        echo "ERROR: --${_vf} requires a value." >&2
        exit 1
    fi
done
unset _vf

# Handle --help / -h.
if argparse_has "help" || argparse_has "h"; then
    operator_args_render_help_for_flags "create-project.sh" \
        "Bootstrap a new project under \$KANBAN_ROOT/projects/<name>/." \
        OPERATOR_VALID_FLAGS \
        "" \
        "  --workflow-type TYPE workflow_type: any registered ready plugin type (default: release)" \
        "  --max-patch N        max_patch ceiling (default: 21)" \
        "  --max-minor N        max_minor ceiling (default: 13)" \
        "  --max-major N        max_major ceiling (default: 0)" \
        "  --git-remote NAME    git_remote_name (default: origin)" \
        "  --priority INT       registry priority (default: next available)" \
        "  --color '#RRGGBB'    registry display color (default: next unused)" \
        "  --dev-tree PATH      dev_tree_path (alias: --dev-tree-path; default: empty, edit manually)" \
        "  --git-repo URL       git_repo_url (alias: --git-repo-url; default: empty, edit manually)" \
        "  --no-migrate         skip auto-migration of projects.cfg (not recommended)" \
        "" \
        "Note: when --dev-tree names a path that does not currently exist, the" \
        "script WARNs (naming the path) but still writes the value and exits 0."
    exit 0
fi

# Reject unexpected positional arguments (hard-cut: no positional project name).
if [[ "${#ARGPARSE_POSITIONAL[@]}" -gt 0 ]]; then
    echo "ERROR: unexpected positional argument: ${ARGPARSE_POSITIONAL[0]}" >&2
    echo "Usage: create-project.sh --project <name> [--workflow-type <type>] [--max-minor <N>] [--max-major <N>]" >&2
    exit 1
fi

# Reject any flag not in the declared vocabulary.
operator_args_validate_known "create-project.sh" OPERATOR_VALID_FLAGS || exit 1

# Extract boolean flags.
if argparse_has "dry-run";   then DRY_RUN="true"; fi
if argparse_has "no-migrate"; then NO_MIGRATE="true"; fi

# Extract value flags.
NAME="$(operator_args_project)"
if argparse_has "workflow-type"; then WORKFLOW="${ARGPARSE_FLAGS[workflow-type]}"; fi
if argparse_has "max-patch";     then MAX_PATCH="${ARGPARSE_FLAGS[max-patch]}"; fi
if argparse_has "max-minor";     then MAX_MINOR="${ARGPARSE_FLAGS[max-minor]}"; fi
if argparse_has "max-major";     then MAX_MAJOR="${ARGPARSE_FLAGS[max-major]}"; fi
if argparse_has "git-remote";    then GIT_REMOTE_NAME="${ARGPARSE_FLAGS[git-remote]}"; fi
if argparse_has "priority";      then PRIORITY="${ARGPARSE_FLAGS[priority]}"; fi
if argparse_has "color";         then COLOR="${ARGPARSE_FLAGS[color]}"; fi

# dev-tree/dev-tree-path and git-repo/git-repo-url are flag/alias pairs that
# both write the same project.cfg field. The alias is checked after the
# canonical flag so it wins when the operator supplies both (same precedence
# as install-crontab.sh's --wake-tier/--tier pair).
if argparse_has "dev-tree";      then DEV_TREE_PATH="${ARGPARSE_FLAGS[dev-tree]}"; fi
if argparse_has "dev-tree-path"; then DEV_TREE_PATH="${ARGPARSE_FLAGS[dev-tree-path]}"; fi
if argparse_has "git-repo";      then GIT_REPO_URL="${ARGPARSE_FLAGS[git-repo]}"; fi
if argparse_has "git-repo-url";  then GIT_REPO_URL="${ARGPARSE_FLAGS[git-repo-url]}"; fi

# Per-script value validation (unchanged from before).
if [[ -n "$MAX_PATCH" ]]; then _validate_non_negative_integer "--max-patch"  "$MAX_PATCH"; fi
if [[ -n "$MAX_MINOR" ]]; then _validate_non_negative_integer "--max-minor"  "$MAX_MINOR"; fi
if [[ -n "$MAX_MAJOR" ]]; then _validate_non_negative_integer "--max-major"  "$MAX_MAJOR"; fi
if [[ -n "$COLOR" ]];     then _validate_color_flag           "--color"       "$COLOR";    fi

# dev-tree/git-repo: only a non-empty check (values are written verbatim).
# A path that does not currently exist is legitimate (projects are often
# registered before their dev tree is cloned) — WARN below, do not fail.
if argparse_has "dev-tree" || argparse_has "dev-tree-path"; then
    _validate_non_empty "--dev-tree/--dev-tree-path" "$DEV_TREE_PATH"
fi
if argparse_has "git-repo" || argparse_has "git-repo-url"; then
    _validate_non_empty "--git-repo/--git-repo-url" "$GIT_REPO_URL"
fi
if [[ -n "$DEV_TREE_PATH" && ! -e "$DEV_TREE_PATH" ]]; then
    echo "WARNING: --dev-tree path does not currently exist: ${DEV_TREE_PATH}" >&2
    echo "         Writing it into project.cfg anyway; clone or create the tree before the first release." >&2
fi

if [[ -z "$NAME" ]]; then
    echo "ERROR: project name is required (--project <name>)" >&2
    echo "Usage: create-project.sh --project <name> [--workflow-type <type>] [--max-minor <N>] [--max-major <N>]" >&2
    exit 1
fi

# Sanity check the name (alphanumeric, hyphens, underscores)
if ! [[ "$NAME" =~ ^[a-zA-Z][a-zA-Z0-9_-]*$ ]]; then
    echo "ERROR: project name '$NAME' must be alphanumeric (with optional hyphens/underscores)" >&2
    echo "       and must start with a letter." >&2
    exit 1
fi

PROJECT_DIR="${KANBAN_ROOT}/projects/${NAME}"

if [[ -d "$PROJECT_DIR" ]]; then
    echo "ERROR: project directory already exists: $PROJECT_DIR" >&2
    echo "       Use add-project.sh to register an existing project, or" >&2
    echo "       remove-project.sh to clear it before re-creating." >&2
    exit 2
fi

# --- Validate workflow type against the plugin registry ---
# Acceptable types are any plugin in workflows/ with status = ready.
# Scaffold-status plugins are refused with the status named; unknown types
# are refused with an error that lists the discovered ready types.
#
# Workflows root: installed at $KANBAN_ROOT/workflows/; in the dev tree at
# team/workflows/ two levels above SCRIPT_DIR (team/scripts/ → team/).
_WORKFLOWS_DIR="${KANBAN_ROOT}/workflows"
if [[ ! -d "$_WORKFLOWS_DIR" ]]; then
    # Fall back to dev-tree location when installed workflows/ is absent.
    _WORKFLOWS_DIR="${SCRIPT_DIR}/../workflows"
fi

# ---------------------------------------------------------------------------
# _list_ready_workflow_types <workflows_root>
#
# Scans <workflows_root>/*/workflow.cfg for plugins whose status = ready.
# Prints each ready type name on its own line.  Requires read_ini (loaded
# above via lib/workflow.sh → lib/ini_parser.sh).
# ---------------------------------------------------------------------------
_list_ready_workflow_types() {
    local _wf_root="$1"
    local _cfg _type _status
    for _cfg in "${_wf_root}"/*/workflow.cfg; do
        [[ -f "$_cfg" ]] || continue
        _type="$(basename "$(dirname "$_cfg")")"
        _status="$(read_ini "$_cfg" workflow status "" 2>/dev/null || true)"
        [[ "$_status" == "ready" ]] && printf '%s\n' "$_type"
    done
}

# Attempt to load the plugin via the registry.  wf_load_plugin validates the
# manifest and exposes WF_MANIFEST_STATUS and WF_MANIFEST_AGENTS on success.
_WF_LOAD_EXIT=0
wf_load_plugin --workflows-dir "$_WORKFLOWS_DIR" "$WORKFLOW" 2>/dev/null || _WF_LOAD_EXIT=$?

if [[ "$_WF_LOAD_EXIT" -ne 0 ]]; then
    # Distinguish scaffold status from fully unknown type so the error names
    # the concrete reason (required by the acceptance criteria).
    _PROBE_CFG="${_WORKFLOWS_DIR}/${WORKFLOW}/workflow.cfg"
    if [[ -f "$_PROBE_CFG" ]]; then
        # Plugin directory exists — the manifest failed validation.  Surface
        # the status value if it is scaffold; use the library's error text
        # for other manifest failures.
        _PROBE_STATUS="$(read_ini "$_PROBE_CFG" workflow status "" 2>/dev/null || true)"
        if [[ "$_PROBE_STATUS" == "scaffold" ]]; then
            echo "ERROR: workflow type '${WORKFLOW}' cannot be used: plugin status is 'scaffold' — flip status to 'ready' after implementing all hooks" >&2
        else
            echo "ERROR: workflow type '${WORKFLOW}' is not valid: ${WF_LOAD_ERROR}" >&2
        fi
    else
        # Unknown type — enumerate discovered ready types to help the operator.
        _READY_TYPES="$(  _list_ready_workflow_types "$_WORKFLOWS_DIR" | sort | tr '\n' ' ' | sed 's/ $//'  )"
        if [[ -n "$_READY_TYPES" ]]; then
            echo "ERROR: unknown workflow type '${WORKFLOW}'; discovered ready types are: ${_READY_TYPES}" >&2
        else
            echo "ERROR: unknown workflow type '${WORKFLOW}'; no ready workflow plugins found under ${_WORKFLOWS_DIR}" >&2
        fi
    fi
    exit 1
fi
unset _WF_LOAD_EXIT _PROBE_CFG _PROBE_STATUS _READY_TYPES

# --- Resolve template directory for the selected workflow type ---
# Templates live at team/templates/project/<workflow>/ in the dev tree and at
# templates/project/<workflow>/ in the installed tree.  SCRIPT_DIR is one level
# below the templates root in both cases (dev: team/scripts/; installed:
# scripts/), so ../templates/project/<workflow>/ resolves correctly either way.
#
# Fallback: when no type-specific template directory exists (e.g. a minimal
# plugin that ships no templates/), use the release/ directory as a
# type-agnostic base.  The BUG-TEMPLATE, PRIORITY-TEMPLATE, REQUIREMENTS-
# TEMPLATE, and README files in release/ are workflow-type neutral.
# Queue files are generated from the plugin manifest agents field in this case
# (see "Seed queue files" section below).
TEMPLATE_DIR="${SCRIPT_DIR}/../templates/project/${WORKFLOW}"
TEMPLATE_FALLBACK="false"
if [[ ! -d "$TEMPLATE_DIR" ]]; then
    TEMPLATE_DIR="${SCRIPT_DIR}/../templates/project/release"
    TEMPLATE_FALLBACK="true"
    if [[ ! -d "$TEMPLATE_DIR" ]]; then
        echo "ERROR: template directory not found for workflow type '${WORKFLOW}' and fallback release/ template is also missing" >&2
        exit 1
    fi
fi

# --- Print plan ---
echo "Creating project: $NAME"
echo "  Project root:   $PROJECT_DIR"
echo "  Workflow:       $WORKFLOW"
echo "  Dev tree:       ${DEV_TREE_PATH:-<empty — edit project.cfg after creation>}"
echo "  Git repo:       ${GIT_REPO_URL:-<empty — edit project.cfg after creation>}"
echo "  Git remote:     $GIT_REMOTE_NAME"
echo "  max_patch:      $MAX_PATCH"
echo "  max_minor:      $MAX_MINOR"
echo "  max_major:      $MAX_MAJOR"
echo "  Priority:       ${PRIORITY:-<next available>}"
echo "  Color:          ${COLOR:-<next unused palette entry>}"
# Report projects.cfg format intent (resolved lazily when file exists)
_plan_cfg="$(projects_cfg_path)"
if [[ -f "$_plan_cfg" ]]; then
    _plan_fmt="$(projects_cfg_format "$_plan_cfg")"
    if [[ "$_plan_fmt" == "colon-legacy" ]]; then
        if [[ "$NO_MIGRATE" == "true" ]]; then
            echo "  projects.cfg:   colon-legacy (--no-migrate: will append colon line; WARNING: mixed format)"
        else
            echo "  projects.cfg:   colon-legacy (will auto-migrate to INI before registering)"
        fi
    else
        echo "  projects.cfg:   INI format"
    fi
    unset _plan_fmt
fi
unset _plan_cfg
echo ""

if [[ "$DRY_RUN" == "true" ]]; then
    echo "[DRY-RUN] No changes will be made."
    echo ""
    echo "[DRY-RUN] Template files that would be copied from ${TEMPLATE_DIR}:"
    echo "  cp ${TEMPLATE_DIR}/BUG-TEMPLATE.md -> ${PROJECT_DIR}/bugs/templates/BUG-TEMPLATE.md"
    echo "  cp ${TEMPLATE_DIR}/PRIORITY-TEMPLATE.md -> ${PROJECT_DIR}/priority/templates/PRIORITY-TEMPLATE.md"
    echo "  cp ${TEMPLATE_DIR}/REQUIREMENTS-TEMPLATE.md -> ${PROJECT_DIR}/requirements/templates/REQUIREMENTS-TEMPLATE.md"
    echo "  cp ${TEMPLATE_DIR}/README-bugs.md -> ${PROJECT_DIR}/bugs/README.md"
    echo "  cp ${TEMPLATE_DIR}/README-priority.md -> ${PROJECT_DIR}/priority/README.md"
    echo "  cp ${TEMPLATE_DIR}/README-requirements.md -> ${PROJECT_DIR}/requirements/README.md"
    if [[ -f "${TEMPLATE_DIR}/BRIEF-EXAMPLE.md" ]]; then
        echo "  cp ${TEMPLATE_DIR}/BRIEF-EXAMPLE.md -> ${PROJECT_DIR}/brief-example.md"
    fi
    exit 0
fi

# --- Create directory layout ---
mkdir -p "${PROJECT_DIR}"
mkdir -p "${PROJECT_DIR}/tasks/queues"
mkdir -p "${PROJECT_DIR}/tasks/queues/plans"
mkdir -p "${PROJECT_DIR}/bugs/templates"
mkdir -p "${PROJECT_DIR}/priority/templates"
mkdir -p "${PROJECT_DIR}/requirements/templates"
mkdir -p "${PROJECT_DIR}/artifacts"
mkdir -p "${PROJECT_DIR}/release-notes"

# Per-project metrics directory.
# The dashboard cost/metrics pane reads projects/<name>/metrics/history.csv.
# The directory is created empty here — the first metrics write creates
# history.csv. mkdir -p is idempotent; re-running does not destroy existing data.
mkdir -p "${PROJECT_DIR}/metrics"

# Per-project log directory.
# Currently houses only cm-push-watchdog.log (per-project git-push tracking).
# Agent-scope debug and training logs live at $KANBAN_ROOT/logs/.
mkdir -p "${PROJECT_DIR}/logs"

echo "  + created directory layout"

# --- Write project.cfg (INI format) ---
# dev_tree_path and git_repo_url are written verbatim from --dev-tree/--git-repo
# (and their --dev-tree-path/--git-repo-url aliases) when supplied; otherwise
# they are written empty exactly as before, and the operator edits project.cfg
# manually to supply machine-specific paths.
#
# branch_prefix must be written bare (no surrounding quotes).
# For hybrid shops where AI and human branches share the same repo: write ai_.
# For pure-AI installs, clear branch_prefix in project.cfg after creation.
# Never write branch_prefix = "" — the INI reader would see the literal 2-char
# string "" rather than empty, causing version-resolution to seek non-existent tags.
_BRANCH_PREFIX_LINE="branch_prefix = ai_"

# dev_tree_path/git_repo_url lines and the header comment describing them are
# built conditionally so the omitted-flag case stays byte-identical to the
# original always-empty output.
_DEV_TREE_LINE="dev_tree_path ="
_GIT_REPO_LINE="git_repo_url ="
[[ -n "$DEV_TREE_PATH" ]] && _DEV_TREE_LINE="dev_tree_path = ${DEV_TREE_PATH}"
[[ -n "$GIT_REPO_URL" ]]  && _GIT_REPO_LINE="git_repo_url = ${GIT_REPO_URL}"

if [[ -n "$DEV_TREE_PATH" || -n "$GIT_REPO_URL" ]]; then
    _PATH_FIELDS_COMMENT="# dev_tree_path and/or git_repo_url were supplied via --dev-tree/--git-repo
# at creation time. Edit this file directly if either value needs to change."
else
    _PATH_FIELDS_COMMENT="# IMPORTANT: dev_tree_path and git_repo_url are intentionally empty.
# Edit this file to supply them before the chain runs its first release."
fi

cat > "${PROJECT_DIR}/project.cfg" <<EOF
# project.cfg for ${NAME}
# Generated by create-project.sh on $(date -Iseconds)
#
# INI-format per-project configuration.
# Parsed by: team/scripts/lib/ini_parser.sh::read_ini
#
${_PATH_FIELDS_COMMENT}
#
# Ceilings are operator checkpoints — raise or lower per project as needed:
#   set-version-ceiling.sh --project ${NAME} --minor <N>

[project]
project_name = ${NAME}
workflow_type = ${WORKFLOW}
git_remote_name = ${GIT_REMOTE_NAME}
${_DEV_TREE_LINE}
${_GIT_REPO_LINE}
# --- Git branch topology (release workflow only; ignored for document) -----
# Before this project's FIRST release, the chain's single base branch must
# exist: <prefix>main. With the default prefix that is ai_main; with an
# empty prefix, main. Your repo's own default branch is untouched and coexists.
#   push_to_remote = true  -> the branch must also exist ON ORIGIN: CM
#                             branches RC from <prefix>main and squashes back.
#   push_to_remote = false -> local branch in dev_tree_path suffices.
# For push_to_remote = true, one idempotent command creates and pushes it:
#   init-project-git-repo.sh --project ${NAME}
# For push_to_remote = false, create the branch locally instead (the init
# script ALWAYS pushes — wrong for local-only mode):
#   git branch <prefix>main main
# Default for hybrid shops where AI and human branches share the same repo.
# Set empty (branch_prefix =) for pure-AI installs; never use quoted-empty ("").
${_BRANCH_PREFIX_LINE}
# push_to_remote: When true (default), CM pushes to origin as normal.
# Set to false for local-only / demo / customer-site mode where the AI chain
# must never touch origin — releases are built locally and the operator pushes
# manually if and when they choose.  Default: true (preserves existing behavior).
# NOTE: see the branch-topology block above — true requires <prefix>main on
# origin BEFORE the first release (init-project-git-repo.sh).
push_to_remote = true

[versioning]
max_patch = ${MAX_PATCH}
max_minor = ${MAX_MINOR}
max_major = ${MAX_MAJOR}

# ---------------------------------------------------------------------------
# [debug] — Diagnostic and introspection settings
# ---------------------------------------------------------------------------
[debug]

# verbose_mode: When true, agents emit additional diagnostic output during
# their run (expanded INI reads, queue scans, git operations). Useful for
# troubleshooting agent behavior or validating a new install.
# Set to false (or leave absent) for normal production operation.
# Default: false
verbose_mode = false

# verbose_agents: Comma-separated list of agent roles that have debug/verbose
# output enabled when verbose_mode = true. Only roles listed here emit debug
# logs. Use a subset (e.g. coder,writer) to limit noise to specific agents.
# Allowed values: pm, coder, writer, tester, cm (any subset, comma-separated)
# Default: pm,coder,writer,tester,cm
verbose_agents = pm,coder,writer,tester,cm

# ---------------------------------------------------------------------------
# [training] — Training corpus and reasoning-trace settings
# ---------------------------------------------------------------------------
[training]

# reasoning_trace: When true, agents write a reasoning trace (task context,
# decisions, and chain-of-thought notes) to the project's training corpus at
# $KANBAN_ROOT/projects/${NAME}/logs/training/<agent>/<timestamp>-<task-id>.md. Useful for
# building fine-tuning datasets or auditing agent reasoning.
# Set to false (or leave absent) for normal production operation.
# Default: false
reasoning_trace = false

# training_agents: Comma-separated list of agent roles that emit reasoning
# traces when reasoning_trace = true. Only roles listed here write training
# logs. Use a subset (e.g. coder,writer) to limit corpus to specific agents.
# Allowed values: pm, coder, writer, tester, cm (any subset, comma-separated)
# Default: (empty — no agents; explicit opt-in required)
training_agents =
EOF
unset _BRANCH_PREFIX_LINE _DEV_TREE_LINE _GIT_REPO_LINE _PATH_FIELDS_COMMENT
echo "  + wrote project.cfg"

# --- Seed queue files ---
# When the workflow type ships a type-specific template directory, read the
# queue file list from that directory (format: <filename>:<title>:<description>).
# When using the minimal fallback template, derive queue files from the plugin
# manifest's agents field (WF_MANIFEST_AGENTS, set by wf_load_plugin above)
# plus the always-present bug_backlog and priority_backlog queues.
#
# Standard agent → queue-file mapping for fallback generation:
#   pm     → pm_backlog.md:PM Backlog:pm agent
#   coder  → coder_backlog.md:Coder Backlog:coder agent
#   writer → writer_backlog.md:Writer Backlog:writer agent
#   tester → tester_backlog.md:Tester Backlog:tester agent
#   cm     → cm_backlog.md:CM Backlog:cm agent
# Plus always: bug_backlog.md and priority_backlog.md
_write_queue_file() {
    local _qf="$1" _title="$2" _desc="$3"
    local _target="${PROJECT_DIR}/tasks/queues/${_qf}"
    cat > "$_target" <<EOF
# ${_title}

Tasks ready for the ${_desc} to pull. Markers:

- \`[ ]\` pending (BACKLOG, ready to pull)
- \`[W]\` waiting on prerequisites
- \`[A]\` actively being worked
- \`[x]\` done or won't-do

EOF
}

_queue_count=0
if [[ "$TEMPLATE_FALLBACK" == "false" && -f "${TEMPLATE_DIR}/queue-files.list" ]]; then
    # Type-specific template directory exists — use its queue-files.list.
    while IFS=':' read -r queue_file title desc; do
        # Skip comment lines and empty lines
        [[ -z "$queue_file" || "$queue_file" =~ ^[[:space:]]*# ]] && continue
        # Trim any trailing whitespace from fields
        queue_file="${queue_file%"${queue_file##*[![:space:]]}"}"
        title="${title%"${title##*[![:space:]]}"}"
        desc="${desc%"${desc##*[![:space:]]}"}"
        [[ -z "$queue_file" ]] && continue
        _write_queue_file "$queue_file" "$title" "$desc"
        (( _queue_count++ )) || true
    done < "${TEMPLATE_DIR}/queue-files.list"
else
    # Minimal fallback: derive queue files from the plugin manifest agents.
    # WF_MANIFEST_AGENTS is a comma-separated list set by wf_load_plugin.
    _agents_csv="${WF_MANIFEST_AGENTS:-}"
    IFS=',' read -ra _agents_arr <<< "$_agents_csv"
    for _agent in "${_agents_arr[@]}"; do
        _agent="${_agent// /}"  # strip any spaces
        [[ -z "$_agent" ]] && continue
        case "$_agent" in
            pm)     _write_queue_file "pm_backlog.md"     "PM Backlog"     "pm agent"     ;;
            coder)  _write_queue_file "coder_backlog.md"  "Coder Backlog"  "coder agent"  ;;
            writer) _write_queue_file "writer_backlog.md" "Writer Backlog" "writer agent" ;;
            tester) _write_queue_file "tester_backlog.md" "Tester Backlog" "tester agent" ;;
            cm)     _write_queue_file "cm_backlog.md"     "CM Backlog"     "cm agent"     ;;
            *)      : ;;  # unknown agent role — skip gracefully
        esac
        (( _queue_count++ )) || true
    done
    unset _agents_csv _agents_arr _agent
    # Always seed the shared intake queues regardless of agent roster.
    _write_queue_file "bug_backlog.md"      "Bug Backlog"      "bug triage"       && (( _queue_count++ )) || true
    _write_queue_file "priority_backlog.md" "Priority Backlog" "priority intake"   && (( _queue_count++ )) || true
fi
echo "  + seeded ${_queue_count} queue files"

# --- Seed templates ---
cp "${TEMPLATE_DIR}/BUG-TEMPLATE.md"          "${PROJECT_DIR}/bugs/templates/BUG-TEMPLATE.md"
cp "${TEMPLATE_DIR}/PRIORITY-TEMPLATE.md"     "${PROJECT_DIR}/priority/templates/PRIORITY-TEMPLATE.md"
cp "${TEMPLATE_DIR}/REQUIREMENTS-TEMPLATE.md" "${PROJECT_DIR}/requirements/templates/REQUIREMENTS-TEMPLATE.md"
echo "  + seeded 3 templates"

# --- Seed README.md files for each subdir ---
cp "${TEMPLATE_DIR}/README-bugs.md"          "${PROJECT_DIR}/bugs/README.md"
cp "${TEMPLATE_DIR}/README-priority.md"      "${PROJECT_DIR}/priority/README.md"
cp "${TEMPLATE_DIR}/README-requirements.md"  "${PROJECT_DIR}/requirements/README.md"
echo "  + seeded 3 README.md files"

# --- Seed optional BRIEF-EXAMPLE.md (if present in workflow templates) ---
if [[ -f "${TEMPLATE_DIR}/BRIEF-EXAMPLE.md" ]]; then
    cp "${TEMPLATE_DIR}/BRIEF-EXAMPLE.md" "${PROJECT_DIR}/brief-example.md"
    echo "  + seeded brief-example.md"
fi

# --- Seed release-state.md ---
# Schema: Active RC, RC Opened At, RC Opened By Task, Last Released.
# For code projects the git tag is the canonical Last Released record;
# release.sh (Step 15) also writes this field after each release.
# For document-workflow projects (no git repo) finalize.sh writes
# Last Released after each publish so pp_last_released_version can read it
# The field is seeded as 'none' for all new projects.
cat > "${PROJECT_DIR}/release-state.md" <<EOF
# Release State

## Active RC
none

## RC Opened At
none

## RC Opened By Task
none

## Last Released
none
EOF
echo "  + seeded release-state.md (includes Last Released field)"

# --- Handle projects.cfg format: auto-migrate or warn ---
# Detect current format using projects_cfg_format (no inline grep).
_cfg_file="$(projects_cfg_path)"
projects_cfg_ensure
_cfg_fmt="$(projects_cfg_format "$_cfg_file")"

if [[ "$_cfg_fmt" == "colon-legacy" ]]; then
    if [[ "$NO_MIGRATE" == "true" ]]; then
        echo "WARNING: projects.cfg is in colon-legacy format and --no-migrate was given." >&2
        echo "WARNING: The new project will be appended as a colon-format line." >&2
        echo "WARNING: This produces a mixed-format file that is not a supported stable state." >&2
        echo "WARNING: Run 'scripts/migrate/projects-cfg.sh' (or re-run without --no-migrate)" >&2
        echo "WARNING: to convert projects.cfg to INI format." >&2
    else
        # Auto-migrate: convert colon-legacy to INI before registering.
        projects_cfg_colon_to_ini "$_cfg_file"
        _cfg_fmt="ini"
    fi
fi
unset _cfg_file _cfg_fmt

# --- Auto-assign priority and color if not operator-supplied ---
# Priority: max(existing) + 1 via projects_cfg_next_priority helper.
# Color: next unused palette entry via projects_cfg_next_color helper.
# Operator-supplied values always override auto-assignment.
if [[ -z "$PRIORITY" ]]; then
    PRIORITY="$(projects_cfg_next_priority)"
fi
if [[ -z "$COLOR" ]]; then
    COLOR="$(projects_cfg_next_color)"
fi

# --- Register in projects.cfg ---
projects_cfg_add "$NAME" "$PRIORITY" "$COLOR"
final_priority="$(projects_cfg_priority "$NAME")"
final_color="$(projects_cfg_color "$NAME")"
echo "  + registered in projects.cfg (priority=${final_priority}, color=${final_color})"

echo ""
echo "Done. Project '${NAME}' is ready (max_patch=${MAX_PATCH}, max_minor=${MAX_MINOR}, max_major=${MAX_MAJOR})."
echo ""
echo "Next steps:"
echo "  1. Edit project.cfg to set dev_tree_path and git_repo_url:"
echo "     \$EDITOR ${PROJECT_DIR}/project.cfg"
echo "  2. Push the chain's base branches to origin (run once, before first release):"
echo "     init-project-git-repo.sh --project ${NAME}"
echo "  3. Drop requirements docs in: ${PROJECT_DIR}/requirements/"
echo "  4. The chain runs immediately (ceilings are operator checkpoints)."
echo "     To raise or lower a ceiling: set-version-ceiling.sh --project ${NAME} --minor <N>"
