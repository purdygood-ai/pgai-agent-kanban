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
# Path fields (dev_tree_path, git_repo_url) are intentionally written empty.
# Edit project.cfg manually to supply these before the first release.
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
#   create-project.sh --project <name> --no-migrate             # skip auto-migration; append colon line (not recommended)
#   create-project.sh --project <name> --dry-run                # preview, no writes
#
# Path fields are NOT flag-overridable:
#   dev_tree_path and git_repo_url are always written empty. After creating the
#   project, edit projects/<name>/project.cfg directly to fill in these paths.
#   Flags --dev-tree, --dev-tree-path, --git-repo, and --git-repo-url are
#   rejected with a clear message pointing you at manual editing.
#
# Defaults written to project.cfg:
#   workflow_type=release
#   git_remote_name=origin
#   max_patch=21            (permissive; working out of the box)
#   max_minor=13            (permissive; ceilings are operator checkpoints)
#   max_major=0             (operator gate; raise explicitly when ready)
#   dev_tree_path=          (empty; edit manually)
#   git_repo_url=           (empty; edit manually)
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

# --- Resolve kanban root ---
KANBAN_ROOT="${PGAI_AGENT_KANBAN_ROOT_PATH:-$HOME/pgai_agent_kanban}"

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

# ---------------------------------------------------------------------------
# _reject_path_flag <flag-name>
# Prints a clear error message and exits 1 when the operator passes a flag
# that would set dev_tree_path or git_repo_url.
# ---------------------------------------------------------------------------
_reject_path_flag() {
    local flag="$1"
    echo "ERROR: ${flag} is not supported." >&2
    echo "" >&2
    echo "  dev_tree_path and git_repo_url are intentionally not flag-overridable." >&2
    echo "  They are written empty in project.cfg so you can fill in the correct" >&2
    echo "  paths for your machine without relying on fragile command-line flags." >&2
    echo "" >&2
    echo "  After creating the project, edit project.cfg directly:" >&2
    echo "    \$EDITOR \$KANBAN_ROOT/projects/<name>/project.cfg" >&2
    exit 1
}

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

# Declared flag vocabulary for all flags this command accepts.
OPERATOR_VALID_FLAGS=(project dry-run help workflow-type max-patch max-minor max-major git-remote priority color no-migrate dev-tree dev-tree-path git-repo git-repo-url h)

# --- Parse args ---
# Value-taking flags: project plus script-specific
#   (workflow-type, max-patch, max-minor, max-major, git-remote, priority, color,
#   dev-tree, dev-tree-path, git-repo, git-repo-url — last four rejected).
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
        "  --workflow-type TYPE workflow_type: release|document (default: release)" \
        "  --max-patch N        max_patch ceiling (default: 21)" \
        "  --max-minor N        max_minor ceiling (default: 13)" \
        "  --max-major N        max_major ceiling (default: 0)" \
        "  --git-remote NAME    git_remote_name (default: origin)" \
        "  --priority INT       registry priority (default: next available)" \
        "  --color '#RRGGBB'    registry display color (default: next unused)" \
        "  --no-migrate         skip auto-migration of projects.cfg (not recommended)"
    exit 0
fi

# Rejected path flags (value-taking or boolean — reject either way).
for _rf in dev-tree dev-tree-path git-repo git-repo-url; do
    if argparse_has "$_rf"; then
        _reject_path_flag "--${_rf}"
    fi
done
unset _rf

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

# Per-script value validation (unchanged from before).
if [[ -n "$MAX_PATCH" ]]; then _validate_non_negative_integer "--max-patch"  "$MAX_PATCH"; fi
if [[ -n "$MAX_MINOR" ]]; then _validate_non_negative_integer "--max-minor"  "$MAX_MINOR"; fi
if [[ -n "$MAX_MAJOR" ]]; then _validate_non_negative_integer "--max-major"  "$MAX_MAJOR"; fi
if [[ -n "$COLOR" ]];     then _validate_color_flag           "--color"       "$COLOR";    fi

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

# --- Validate workflow type against the canonical set ---
# Valid types are 'release' and 'document'. All other values exit non-zero
# with a clear error.
case "$WORKFLOW" in
    release|document)
        ;;
    *)
        echo "ERROR: unknown workflow type '${WORKFLOW}'; valid types are: release, document" >&2
        exit 1
        ;;
esac

# --- Resolve template directory for the selected workflow type ---
# Templates live at team/templates/project/<workflow>/ in the dev tree and at
# templates/project/<workflow>/ in the installed tree.  SCRIPT_DIR is one level
# below the templates root in both cases (dev: team/scripts/; installed:
# scripts/), so ../templates/project/<workflow>/ resolves correctly either way.
TEMPLATE_DIR="${SCRIPT_DIR}/../templates/project/${WORKFLOW}"
if [[ ! -d "$TEMPLATE_DIR" ]]; then
    echo "ERROR: template directory not found for workflow type '${WORKFLOW}': ${TEMPLATE_DIR}" >&2
    exit 1
fi

# --- Print plan ---
echo "Creating project: $NAME"
echo "  Project root:   $PROJECT_DIR"
echo "  Workflow:       $WORKFLOW"
echo "  Dev tree:       <empty — edit project.cfg after creation>"
echo "  Git repo:       <empty — edit project.cfg after creation>"
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
# NOTE: dev_tree_path and git_repo_url are intentionally written empty.
# The operator must edit project.cfg manually to supply machine-specific paths.
#
# branch_prefix must be written bare (no surrounding quotes).
# For hybrid shops where AI and human branches share the same repo: write ai_.
# For pure-AI installs, clear branch_prefix in project.cfg after creation.
# Never write branch_prefix = "" — the INI reader would see the literal 2-char
# string "" rather than empty, causing version-resolution to seek non-existent tags.
_BRANCH_PREFIX_LINE="branch_prefix = ai_"

cat > "${PROJECT_DIR}/project.cfg" <<EOF
# project.cfg for ${NAME}
# Generated by create-project.sh on $(date -Iseconds)
#
# INI-format per-project configuration.
# Parsed by: team/scripts/lib/ini_parser.sh::read_ini
#
# IMPORTANT: dev_tree_path and git_repo_url are intentionally empty.
# Edit this file to supply them before the chain runs its first release.
#
# Ceilings are operator checkpoints — raise or lower per project as needed:
#   set-version-ceiling.sh --project ${NAME} --minor <N>

[project]
project_name = ${NAME}
workflow_type = ${WORKFLOW}
git_remote_name = ${GIT_REMOTE_NAME}
dev_tree_path =
git_repo_url =
# --- Git branch topology (release workflow only; ignored for document) -----
# Before this project's FIRST release, the chain's two base branches must
# exist: <prefix>main and <prefix>develop. With the default prefix that is
# ai_main and ai_develop; with an empty prefix, main and develop. Your
# repo's own default branch (e.g. main) is untouched and coexists.
#   push_to_remote = true  -> both must also exist ON ORIGIN: CM pulls
#                             <prefix>develop at RC-open and pushes releases.
#   push_to_remote = false -> local branches in dev_tree_path suffice.
# For push_to_remote = true, one idempotent command creates and pushes both:
#   init-project-git-repo.sh --project ${NAME}
# For push_to_remote = false, create them locally instead (the init script
# ALWAYS pushes — wrong for local-only mode):
#   git branch <prefix>main main && git branch <prefix>develop main
# Default for hybrid shops where AI and human branches share the same repo.
# Set empty (branch_prefix =) for pure-AI installs; never use quoted-empty ("").
${_BRANCH_PREFIX_LINE}
# push_to_remote: When true (default), CM pushes to origin as normal.
# Set to false for local-only / demo / customer-site mode where the AI chain
# must never touch origin — releases are built locally and the operator pushes
# manually if and when they choose.  Default: true (preserves existing behavior).
# NOTE: see the branch-topology block above — true requires <prefix>main and
# <prefix>develop on origin BEFORE the first release (init-project-git-repo.sh).
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
unset _BRANCH_PREFIX_LINE
echo "  + wrote project.cfg"

# --- Seed queue files ---
# Read queue file list from the workflow's template directory.
# Format: <filename>:<title>:<description>  (comment lines start with #, empty lines skipped)
_queue_count=0
while IFS=':' read -r queue_file title desc; do
    # Skip comment lines and empty lines
    [[ -z "$queue_file" || "$queue_file" =~ ^[[:space:]]*# ]] && continue
    # Trim any trailing whitespace from fields
    queue_file="${queue_file%"${queue_file##*[![:space:]]}"}"
    title="${title%"${title##*[![:space:]]}"}"
    desc="${desc%"${desc##*[![:space:]]}"}"
    [[ -z "$queue_file" ]] && continue
    target="${PROJECT_DIR}/tasks/queues/${queue_file}"
    cat > "$target" <<EOF
# ${title}

Tasks ready for the ${desc} to pull. Markers:

- \`[ ]\` pending (BACKLOG, ready to pull)
- \`[W]\` waiting on prerequisites
- \`[A]\` actively being worked
- \`[x]\` done or won't-do

EOF
    (( _queue_count++ )) || true
done < "${TEMPLATE_DIR}/queue-files.list"
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
