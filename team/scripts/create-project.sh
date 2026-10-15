#!/usr/bin/env bash
# scripts/create-project.sh
#
# Bootstrap a new project under $KANBAN_ROOT/projects/<name>/.
#
# Thin adapter: parses the flags via operator_args.sh, then hands every
# value to the Python create_project function in
# pgai_agent_kanban.ops.projects (the same function
# POST /operations/create-project calls) through pp_run_ops. That function
# validates the values, the name and the workflow type, prints the plan and
# writes the project. This script passes its own scripts/ directory, so the
# workflow templates are read from ../templates/project/ beside it.
#
# Creates the standard project layout: project.cfg (INI), queue files, requirements/,
# bugs/, priority/ (each with a templates/ subdir and README.md), an empty
# release-state.md, and the project README (projects/<name>/README.md, copied
# from templates/project/README-TEMPLATE.md for the operator to fill in), and
# registers the project in projects.cfg.
#
# project.cfg is rendered from the project.cfg template
# (config/project.cfg_example; team/config/ in the repository), so it carries
# every key the kanban reads, each documented, with only the project-specific
# values below filled in.
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
#   3. $EDITOR projects/<name>/README.md     (fill in the project's runners, lints and contracts)
#   4. Drop requirements docs in projects/<name>/requirements/
#   5. Chain runs immediately; adjust ceilings as needed:
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
# shellcheck source=lib/operator_args.sh
source "${SCRIPT_DIR}/lib/operator_args.sh"
# shellcheck source=lib/pp_run_ops.sh
source "${SCRIPT_DIR}/lib/pp_run_ops.sh"

# Declared flag vocabulary for all flags this command accepts.
OPERATOR_VALID_FLAGS=(project dry-run help workflow-type max-patch max-minor max-major git-remote priority color no-migrate dev-tree dev-tree-path git-repo git-repo-url h)

# --- Parse args ---
# Value-taking flags: project plus script-specific
#   (workflow-type, max-patch, max-minor, max-major, git-remote, priority, color,
#   dev-tree/dev-tree-path, git-repo/git-repo-url — the last two pairs are
#   flag/alias pairs that write dev_tree_path/git_repo_url into project.cfg).
# Boolean flags: dry-run, no-migrate, help.
operator_args_argparse \
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

# --- Hand the values to create_project ---
# Only flags the operator gave are passed (as --name=value, so a value that
# starts with '-' stays a value); create_project applies the defaults.
# dev-tree/dev-tree-path and git-repo/git-repo-url are flag/alias pairs that
# both write the same project.cfg field. The alias is checked after the
# canonical flag so it wins when the operator supplies both (same precedence
# as install-crontab.sh's --wake-tier/--tier pair).
_ops_args=(--kanban-root="$KANBAN_ROOT" --scripts-dir="$SCRIPT_DIR" --project="$(operator_args_project)")
for _vf in workflow-type max-patch max-minor max-major git-remote priority color; do
    if argparse_has "$_vf"; then _ops_args+=(--"${_vf}=${ARGPARSE_FLAGS[$_vf]}"); fi
done
unset _vf
if argparse_has "dev-tree-path"; then
    _ops_args+=(--dev-tree="${ARGPARSE_FLAGS[dev-tree-path]}")
elif argparse_has "dev-tree"; then
    _ops_args+=(--dev-tree="${ARGPARSE_FLAGS[dev-tree]}")
fi
if argparse_has "git-repo-url"; then
    _ops_args+=(--git-repo="${ARGPARSE_FLAGS[git-repo-url]}")
elif argparse_has "git-repo"; then
    _ops_args+=(--git-repo="${ARGPARSE_FLAGS[git-repo]}")
fi
if argparse_has "no-migrate"; then _ops_args+=(--no-migrate); fi
if argparse_has "dry-run";    then _ops_args+=(--dry-run); fi

# Exit codes pass through: 1 for a usage or value error, 2 when the project
# directory already exists.
_rc=0
pp_run_ops pgai_agent_kanban.ops create_project "${_ops_args[@]}" || _rc=$?
exit "$_rc"
