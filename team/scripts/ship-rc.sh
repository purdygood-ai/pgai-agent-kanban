#!/usr/bin/env bash
# ship-rc.sh
# Human-invoked: manual escape hatch for shipping a release candidate.
#
# Usage:
#   ship-rc.sh --project <name> --key vX.Y.Z [--help]
#   ship-rc.sh --help
#
# Both --project and --key are REQUIRED. Positional invocation is not supported.
#
# --project <name>   project name (required; drives project resolution via pp layer)
# --key vX.Y.Z       release version (required; format: vX.Y.Z, e.g. v0.5.0)
# --help             print this message and exit 0
#
# Behavior:
#   1.  Validate version format
#   2.  Resolve project name and branch_prefix from project.cfg
#   3.  Verify PREFIXED_rc/<version> exists locally and on origin
#   4.  git fetch origin --tags
#   5.  git checkout PREFIXED_develop && git pull --ff-only
#   6.  git merge --squash -X theirs PREFIXED_rc/<version>  (RC wins on conflict)
#   7.  git commit -m "<version>: release candidate squashed to PREFIXED_develop"
#   8.  git push origin PREFIXED_develop
#   9.  git checkout PREFIXED_main && git pull --ff-only
#   10. git merge --squash -X theirs PREFIXED_develop  (develop wins on conflict)
#   11. git commit -m "Release <version>"
#   12. git tag PREFIXED_<version>
#   13. git push origin PREFIXED_main --tags
#   14. git push origin --delete PREFIXED_rc/<version>
#   15. git branch -D PREFIXED_rc/<version>
#
# Branch and tag prefixing
# ------------------------
# When the project's project.cfg sets [project] branch_prefix (e.g. "ai_"),
# ALL git refs are prefixed: rc branches become ai_rc/<version>, develop becomes
# ai_develop, main becomes ai_main, and the tag becomes ai_v<version>.
# When branch_prefix is empty (the common case), behavior is identical to earlier
# versions of this script: bare rc/<version>, develop, main, and v<version> tag.
#
# The -X theirs flag means "take the incoming side on conflict automatically."
# For a single-developer workflow where there are no competing commits on
# develop or main, this is correct and safe.
#
# Safety: all git steps are wrapped so the script halts cleanly on failure.
#
# Configuration:
#   REPO_ROOT — path to the repository root (default: per-project dev_tree_path from
#               project.cfg, then global PGAI_DEV_TREE_PATH, then script's parent-parent dir)

_ship_rc_usage() {
  echo "Usage: $(basename "$0") --project <name> --key vX.Y.Z [--help]" >&2
  echo "" >&2
  echo "  --project <name>  project name (required)" >&2
  echo "  --key vX.Y.Z      release version (required; format: vX.Y.Z)" >&2
  echo "  --help            print full documentation" >&2
}

_ship_rc_help() {
  cat <<'EOF'
Usage: ship-rc.sh --project <name> --key vX.Y.Z [--help]

Ship a release candidate by squash-merging it into develop and then main.

Both --project and --key are REQUIRED. Positional invocation is not supported.

Arguments:
  --project <name>  Required. Project name; drives project resolution via the pp layer.
  --key vX.Y.Z      Required. Release version in format vX.Y.Z (e.g. v0.5.0).
  --help            Print this help and exit 0.

Branch and tag prefixing
------------------------
When the project's project.cfg sets [project] branch_prefix (e.g. "ai_"),
ALL git refs operated on by this script are prefixed:

  rc branch:     ai_rc/<version>    (instead of rc/<version>)
  develop:       ai_develop         (instead of develop)
  main:          ai_main            (instead of main)
  tag:           ai_v<version>      (instead of v<version>)

When branch_prefix is empty or absent (the common case for pure-AI shops),
this script behaves identically to the earlier un-prefixed version.

The script will error pre-flight if it cannot resolve the project context
or if the prefixed RC branch does not exist locally and on origin.

Environment variables
---------------------
  REPO_ROOT                    Override the repository root directory (default:
                               per-project dev_tree_path from project.cfg, then
                               global PGAI_DEV_TREE_PATH, then script's parent-parent)
  PGAI_AGENT_KANBAN_ROOT_PATH  Path to kanban root (default: ~/pgai_agent_kanban)
EOF
}

# --- Argument parsing ---
PROJECT_ARG=""
VERSION=""

while [[ $# -gt 0 ]]; do
  case "$1" in
    --help|-h)
      _ship_rc_help
      exit 0
      ;;
    --project)
      if [[ -z "${2:-}" ]]; then
        echo "ERROR: --project requires a value" >&2
        echo "" >&2
        _ship_rc_usage
        exit 1
      fi
      PROJECT_ARG="$2"
      shift 2
      ;;
    --key)
      if [[ -z "${2:-}" ]]; then
        echo "ERROR: --key requires a value" >&2
        echo "" >&2
        _ship_rc_usage
        exit 1
      fi
      VERSION="$2"
      shift 2
      ;;
    --*)
      echo "ERROR: unknown flag: $1" >&2
      echo "" >&2
      _ship_rc_usage
      exit 1
      ;;
    *)
      echo "ERROR: positional arguments are not supported; use --project and --key flags" >&2
      echo "" >&2
      _ship_rc_usage
      exit 1
      ;;
  esac
done

if [[ -z "$VERSION" ]]; then
  echo "ERROR: missing required flag --key vX.Y.Z" >&2
  echo "" >&2
  _ship_rc_usage
  exit 1
fi

if [[ -z "$PROJECT_ARG" && -z "${PGAI_PROJECT_NAME:-}" ]]; then
  echo "ERROR: missing required flag --project <name>" >&2
  echo "" >&2
  _ship_rc_usage
  exit 1
fi

# --- Resolve script dir (used for sourcing libs; REPO_ROOT resolved after project context) ---
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

# --- Source optional config files (BEFORE strict mode) ---
# The kanban bashrc/env may have unset vars, non-zero returns, or interactive
# aliases that would trip strict mode. Source them first.
KANBAN_ROOT="${PGAI_AGENT_KANBAN_ROOT_PATH:-$HOME/pgai_agent_kanban}"
TEAM_ROOT="${PGAI_AGENT_KANBAN_ROOT_PATH:-$HOME/pgai_agent_kanban}"
[[ -f "$KANBAN_ROOT/bashrc" ]] && source "$KANBAN_ROOT/bashrc"
[[ -f "$KANBAN_ROOT/env" ]] && source "$KANBAN_ROOT/env"
# $HOME/.config/pgai-kanban.cfg is operator-local bash config; sourced as-is.
[[ -f "$HOME/.config/pgai-kanban.cfg" ]] && source "$HOME/.config/pgai-kanban.cfg"
# Source: kanban.cfg [chain/paths] — INI format replaces legacy config.cfg
_SRC_SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
[[ -f "${_SRC_SCRIPT_DIR}/lib/ini_parser.sh" ]] && source "${_SRC_SCRIPT_DIR}/lib/ini_parser.sh"
# shellcheck source=lib/dev_tree.sh
source "${_SRC_SCRIPT_DIR}/lib/dev_tree.sh"
if [[ -f "$TEAM_ROOT/kanban.cfg" ]]; then
    export PGAI_KANBAN_PM_MODE="${PGAI_KANBAN_PM_MODE:-$(read_ini "$TEAM_ROOT/kanban.cfg" chain pm_mode automatic)}"
fi
unset _SRC_SCRIPT_DIR
export PGAI_DEV_TREE_PATH="${PGAI_DEV_TREE_PATH:-$(resolve_global_dev_tree)}"
# REPO_ROOT is resolved after project context (below) so it can prefer the
# per-project dev_tree_path rather than a script-relative path.

# --- Source project path helpers ---
# shellcheck source=lib/project_paths.sh
source "$SCRIPT_DIR/lib/project_paths.sh"

# --- Enable strict mode for our own code ---
set -euo pipefail

# --- Clean exit handling ---
cleanup_on_exit() {
  local exit_code=$?
  jobs -p 2>/dev/null | xargs -r kill 2>/dev/null || true
  wait 2>/dev/null || true
  exit $exit_code
}
trap cleanup_on_exit EXIT

# --- Validate version format ---
VERSION_REGEX='^v[0-9]+\.[0-9]+\.[0-9]+$'
if [[ ! "$VERSION" =~ $VERSION_REGEX ]]; then
  echo "ERROR: invalid version format: '$VERSION'" >&2
  echo "Expected format: vX.Y.Z (e.g. v0.5.0)" >&2
  exit 1
fi

# --- Resolve project context ---
# Resolution order: --project flag > PGAI_PROJECT_NAME env var > error.
if [[ -n "$PROJECT_ARG" ]]; then
    export PGAI_PROJECT_NAME="$PROJECT_ARG"
fi

PROJECT_NAME="$(pp_require_project_context "")" || {
  echo "" >&2
  echo "ERROR: project context is required." >&2
  echo "  Pass --project <name>, or set PGAI_PROJECT_NAME=<name> in the environment." >&2
  exit 1
}

# --- Resolve REPO_ROOT (default to dev tree, not script-relative path) ---
# Resolution order (first non-empty value wins):
#   1. Explicit REPO_ROOT env-var override (documented, useful for tests).
#   2. Per-project dev_tree_path from project.cfg (authoritative for --project-scoped runs).
#   3. Global PGAI_DEV_TREE_PATH (already resolved above from kanban.cfg or env).
#   4. Script-relative $SCRIPT_DIR/../.. (last-resort fallback: works only from the dev tree).
if [[ -z "${REPO_ROOT:-}" ]]; then
  _proj_root="$(pp_project_root "$PROJECT_NAME" 2>/dev/null)" || _proj_root=""
  _proj_cfg=""
  if [[ -n "$_proj_root" ]]; then
    _proj_cfg="$(_pp_project_cfg_file "$_proj_root")"
  fi
  _per_project_dev_tree=""
  if [[ -n "$_proj_cfg" ]]; then
    _per_project_dev_tree="$(_pp_read_cfg_key "$_proj_cfg" project dev_tree_path "")"
  fi
  if [[ -n "$_per_project_dev_tree" ]]; then
    REPO_ROOT="$_per_project_dev_tree"
  elif [[ -n "${PGAI_DEV_TREE_PATH:-}" ]]; then
    REPO_ROOT="$PGAI_DEV_TREE_PATH"
  else
    REPO_ROOT="$(cd "$SCRIPT_DIR/../.." && pwd)"
  fi
  unset _proj_root _proj_cfg _per_project_dev_tree
fi

# --- Resolve prefixed branch and tag names ---
# pp_prefix_branch and pp_prefix_tag read branch_prefix from project.cfg.
# When branch_prefix is empty (pure-AI shop or unconfigured), the names are
# returned unchanged (rc/v0.5.0, develop, main, v0.5.0).
RC_BRANCH="$(pp_prefix_branch "$PROJECT_NAME" "rc/$VERSION")"
DEVELOP_BRANCH="$(pp_prefix_branch "$PROJECT_NAME" "develop")"
MAIN_BRANCH="$(pp_prefix_branch "$PROJECT_NAME" "main")"
RELEASE_TAG="$(pp_prefix_tag "$PROJECT_NAME" "$VERSION")"

# Helper: run a git command and report the step name on failure.
# Usage: git_step "Step N description" git -C ... <args>
git_step() {
  local step_desc="$1"
  shift
  if ! "$@"; then
    echo "" >&2
    echo "ERROR: git operation failed at step: $step_desc" >&2
    echo "Halting before any further git operations." >&2
    echo "Recover manually by inspecting git state and re-running, or revert as needed." >&2
    exit 1
  fi
}

# Helper: auto-resolve UD/DU modify/delete conflicts after a failed git merge --squash.
# Usage: _ship_rc_autoresolve_ud_conflicts <repo_root> <squash_label> <source_branch_label>
#
# Returns:
#   0  — all conflicts were UD/DU and have been resolved; caller may proceed with commit.
#   1  — one or more UU (content) conflicts found; caller MUST exit.
#   2  — unexpected conflict type or git status parsing failure; caller MUST exit.
#
# Side effects on exit 0:
#   - All UD/DU paths have been staged (git rm or git add as appropriate).
#   - Each resolution is logged to stdout with the exact line:
#       cm-release: auto-resolved modify/delete: <path> (took develop's deletion)
#       cm-release: auto-resolved modify/delete: <path> (took develop's version)
#
_ship_rc_autoresolve_ud_conflicts() {
  local repo_root="$1"
  local squash_label="$2"
  local source_branch_label="$3"

  echo "[ship-rc] Squash produced conflicts; inspecting git status..."

  local porcelain_output
  porcelain_output="$(git -C "$repo_root" status --porcelain 2>&1)"

  local has_uu=0
  local has_ud=0
  local ud_paths=()
  local ud_actions=()

  while IFS= read -r _line; do
    [[ -z "$_line" ]] && continue
    local _xy="${_line:0:2}"
    local _path="${_line:3}"

    case "$_xy" in
      UU)
        has_uu=1
        echo "[ship-rc] Content conflict (UU) detected: $_path — cannot auto-resolve." >&2
        ;;
      UD)
        # Their (source branch's) side deleted the file — take the deletion.
        has_ud=1
        ud_paths+=("$_path")
        ud_actions+=("delete")
        ;;
      DU)
        # Their (source branch's) side kept/modified the file — take their version.
        has_ud=1
        ud_paths+=("$_path")
        ud_actions+=("keep")
        ;;
      AA|DD|AU|UA|DA|AD)
        has_uu=1
        echo "[ship-rc] Unexpected conflict type '${_xy}' for path: $_path — cannot auto-resolve." >&2
        ;;
      *)
        ;;
    esac
  done <<< "$porcelain_output"

  if [[ $has_ud -eq 0 && $has_uu -eq 0 ]]; then
    echo "[ship-rc] WARNING: squash returned non-zero but git status shows no U entries." >&2
    return 2
  fi

  if [[ $has_uu -eq 1 ]]; then
    echo "[ship-rc] UU content conflict(s) detected; cannot auto-resolve." >&2
    echo "[ship-rc] UD/DU paths (if any) will NOT be auto-resolved into a partial commit." >&2
    return 1
  fi

  # Pure UD/DU: auto-resolve by taking the source branch's side.
  echo "[ship-rc] All conflicts are modify/delete (UD/DU); auto-resolving by taking ${source_branch_label}'s side..."
  local i
  for (( i=0; i<${#ud_paths[@]}; i++ )); do
    local _p="${ud_paths[$i]}"
    local _action="${ud_actions[$i]}"
    if [[ "$_action" == "delete" ]]; then
      git -C "$repo_root" rm --force -- "$_p" >/dev/null 2>&1
      echo "cm-release: auto-resolved modify/delete: ${_p} (took develop's deletion)"
    else
      git -C "$repo_root" checkout --theirs -- "$_p"
      git -C "$repo_root" add -- "$_p"
      echo "cm-release: auto-resolved modify/delete: ${_p} (took develop's version)"
    fi
  done

  # Verify no U entries remain.
  local _remaining_u
  _remaining_u="$(git -C "$repo_root" status --porcelain 2>/dev/null | grep -E '^(UU|UD|DU|AA|DD|AU|UA|DA|AD)' || true)"
  if [[ -n "$_remaining_u" ]]; then
    echo "[ship-rc] ERROR: U entries remain after auto-resolve attempt:" >&2
    echo "$_remaining_u" >&2
    return 2
  fi

  local _n="${#ud_paths[@]}"
  echo "[ship-rc] Auto-resolve complete: ${_n} modify/delete path(s) resolved for squash: ${squash_label}"
  return 0
}

# --- Sanity checks ---
echo "Shipping release: $VERSION (project: $PROJECT_NAME)"
echo "  RC branch:    $RC_BRANCH"
echo "  Develop:      $DEVELOP_BRANCH"
echo "  Main:         $MAIN_BRANCH"
echo "  Tag:          $RELEASE_TAG"
echo ""

# Check we're in a git repo
if ! git -C "$REPO_ROOT" rev-parse --git-dir >/dev/null 2>&1; then
  echo "ERROR: not in a git repository at $REPO_ROOT" >&2
  exit 1
fi

# Working tree must be clean
if ! git -C "$REPO_ROOT" diff-index --quiet HEAD -- 2>/dev/null; then
  echo "ERROR: working tree is dirty. Commit or stash changes first." >&2
  git -C "$REPO_ROOT" status --short >&2
  exit 1
fi

# Check tag doesn't already exist
if git -C "$REPO_ROOT" rev-parse --verify "refs/tags/$RELEASE_TAG" >/dev/null 2>&1; then
  echo "ERROR: tag '$RELEASE_TAG' already exists locally" >&2
  exit 1
fi

# --- Step 1: Verify RC branch exists locally and on origin ---
echo "[Step 1] Verifying $RC_BRANCH exists locally and on origin..."
if ! git -C "$REPO_ROOT" rev-parse --verify "refs/heads/$RC_BRANCH" >/dev/null 2>&1; then
  echo "ERROR: branch '$RC_BRANCH' does not exist locally" >&2
  exit 1
fi
if ! git -C "$REPO_ROOT" ls-remote --exit-code --heads origin "$RC_BRANCH" >/dev/null 2>&1; then
  echo "ERROR: branch '$RC_BRANCH' does not exist on origin" >&2
  exit 1
fi
echo "  Confirmed: $RC_BRANCH exists locally and on origin."

# --- Step 2: git fetch origin --tags ---
echo "[Step 2] Fetching origin and tags..."
git_step "git fetch origin --tags" git -C "$REPO_ROOT" fetch origin --tags

# --- Step 3: git checkout DEVELOP_BRANCH && git pull --ff-only ---
echo "[Step 3] Checking out $DEVELOP_BRANCH and pulling..."
git_step "git checkout $DEVELOP_BRANCH" git -C "$REPO_ROOT" checkout "$DEVELOP_BRANCH"
git_step "git pull --ff-only $DEVELOP_BRANCH" git -C "$REPO_ROOT" merge --ff-only "origin/$DEVELOP_BRANCH"

# --- Step 4: git merge --squash -X theirs RC_BRANCH ---
echo "[Step 4] Squash-merging $RC_BRANCH into $DEVELOP_BRANCH (-X theirs: RC wins on conflict)..."
_ship_squash_develop_rc=0
git -C "$REPO_ROOT" merge --squash -X theirs "$RC_BRANCH" || _ship_squash_develop_rc=$?
if [[ $_ship_squash_develop_rc -ne 0 ]]; then
  _ship_autoresolve_rc=0
  _ship_rc_autoresolve_ud_conflicts "$REPO_ROOT" "${RC_BRANCH} into ${DEVELOP_BRANCH}" "develop" || _ship_autoresolve_rc=$?
  if [[ $_ship_autoresolve_rc -ne 0 ]]; then
    echo "" >&2
    echo "ERROR: git operation failed at step: git merge --squash $RC_BRANCH" >&2
    echo "Halting before any further git operations." >&2
    echo "Recover manually by inspecting git state and re-running, or revert as needed." >&2
    exit 1
  fi
fi

# --- Step 5: git commit ---
echo "[Step 5] Committing squash on $DEVELOP_BRANCH..."
git_step "git commit ($DEVELOP_BRANCH squash)" git -C "$REPO_ROOT" commit -m "$VERSION: release candidate squashed to $DEVELOP_BRANCH"

# --- Step 6: git push origin DEVELOP_BRANCH ---
echo "[Step 6] Pushing $DEVELOP_BRANCH to origin..."
git_step "git push origin $DEVELOP_BRANCH" git -C "$REPO_ROOT" push origin "$DEVELOP_BRANCH"

# --- Step 7: git checkout MAIN_BRANCH && git pull --ff-only ---
echo "[Step 7] Checking out $MAIN_BRANCH and pulling..."
git_step "git checkout $MAIN_BRANCH" git -C "$REPO_ROOT" checkout "$MAIN_BRANCH"
git_step "git pull --ff-only $MAIN_BRANCH" git -C "$REPO_ROOT" merge --ff-only "origin/$MAIN_BRANCH"

# --- Step 8: git merge --squash -X theirs DEVELOP_BRANCH ---
echo "[Step 8] Squash-merging $DEVELOP_BRANCH into $MAIN_BRANCH (-X theirs: develop wins on conflict)..."
_ship_squash_main_rc=0
git -C "$REPO_ROOT" merge --squash -X theirs "$DEVELOP_BRANCH" || _ship_squash_main_rc=$?
if [[ $_ship_squash_main_rc -ne 0 ]]; then
  _ship_autoresolve_main_rc=0
  _ship_rc_autoresolve_ud_conflicts "$REPO_ROOT" "${DEVELOP_BRANCH} into ${MAIN_BRANCH}" "develop" || _ship_autoresolve_main_rc=$?
  if [[ $_ship_autoresolve_main_rc -ne 0 ]]; then
    echo "" >&2
    echo "ERROR: git operation failed at step: git merge --squash $DEVELOP_BRANCH" >&2
    echo "Halting before any further git operations." >&2
    echo "Recover manually by inspecting git state and re-running, or revert as needed." >&2
    exit 1
  fi
fi

# --- Step 9: git commit ---
echo "[Step 9] Committing squash on $MAIN_BRANCH..."
git_step "git commit ($MAIN_BRANCH squash)" git -C "$REPO_ROOT" commit -m "Release $VERSION"

# --- Step 10: git tag RELEASE_TAG ---
echo "[Step 10] Tagging $RELEASE_TAG on $MAIN_BRANCH..."
git_step "git tag $RELEASE_TAG" git -C "$REPO_ROOT" tag "$RELEASE_TAG"

# --- Step 11: git push origin MAIN_BRANCH --tags ---
echo "[Step 11] Pushing $MAIN_BRANCH and tags to origin..."
git_step "git push origin $MAIN_BRANCH --tags" git -C "$REPO_ROOT" push origin "$MAIN_BRANCH" --tags

# --- Step 12: git push origin --delete RC_BRANCH ---
echo "[Step 12] Deleting $RC_BRANCH on origin..."
git_step "git push origin --delete $RC_BRANCH" git -C "$REPO_ROOT" push origin --delete "$RC_BRANCH"

# --- Step 13: git branch -D RC_BRANCH ---
echo "[Step 13] Deleting local branch $RC_BRANCH..."
git_step "git branch -D $RC_BRANCH" git -C "$REPO_ROOT" branch -D "$RC_BRANCH"

# --- Post-ship: reconcile kanban release-state.md ---
# Mirror cancel-rc.sh step (j): reset Active RC / RC Opened At / RC Opened By Task
# to none in the project-scoped live-install release-state.md.
# Runs only here (after all git steps succeed); a partial/failed ship never reaches
# this block.  Does NOT write Last Released — the git tag created above is canonical.
echo "[Post-ship] Reconciling release-state.md..."

_RELEASE_STATE_FILE="$(pp_release_state "$PROJECT_NAME" 2>/dev/null)" || _RELEASE_STATE_FILE=""

if [[ -z "$_RELEASE_STATE_FILE" ]]; then
  echo "  WARNING: pp_release_state could not resolve path for '$PROJECT_NAME'." >&2
  echo "  Skipping release-state.md reconciliation." >&2
  echo "  You may need to manually reset Active RC / RC Opened At / RC Opened By Task to none." >&2
else
  python3 - "$_RELEASE_STATE_FILE" <<'PY'
import pathlib, re, sys

rs_path = pathlib.Path(sys.argv[1])

if not rs_path.exists():
    print(f"  WARNING: release-state.md not found: {rs_path}",  file=sys.stderr)
    print(f"  Skipping reconciliation.", file=sys.stderr)
    sys.exit(0)

text = rs_path.read_text()

def set_md_field(text, heading, new_value):
    """Replace the first non-blank line after `## <heading>` with new_value."""
    return re.sub(
        r'(## ' + re.escape(heading) + r'\n(?:[ \t]*\n)*)([^\n]+)',
        r'\g<1>' + new_value,
        text,
        count=1,
    )

def get_md_field(text, heading):
    """Return the first non-blank line after `## <heading>`, or '' if absent."""
    m = re.search(
        r'## ' + re.escape(heading) + r'\n(?:[ \t]*\n)*([^\n]+)',
        text,
    )
    return m.group(1).strip() if m else ''

# Idempotency check: if all three fields already read `none`, nothing to do.
arc   = get_md_field(text, 'Active RC')
roa   = get_md_field(text, 'RC Opened At')
robot = get_md_field(text, 'RC Opened By Task')

if arc == 'none' and roa == 'none' and robot == 'none':
    print('  release-state.md: already reset — nothing to do.')
    sys.exit(0)

new_text = text
new_text = set_md_field(new_text, 'Active RC',          'none')
new_text = set_md_field(new_text, 'RC Opened At',       'none')
new_text = set_md_field(new_text, 'RC Opened By Task',  'none')

rs_path.write_text(new_text)

print(f'  Active RC         -> none  (was: {arc})')
print(f'  RC Opened At      -> none  (was: {roa})')
print(f'  RC Opened By Task -> none  (was: {robot})')
print('  Last Released fields left untouched (git tag is canonical).')
PY

  if [[ $? -ne 0 ]]; then
    echo "  WARNING: release-state.md reconciliation returned non-zero." >&2
    echo "  Verify and manually reset Active RC / RC Opened At / RC Opened By Task if needed." >&2
  fi
fi
unset _RELEASE_STATE_FILE

echo "  NOTE: The CM release task (if one exists) is not auto-closed by this script."
echo "  If a CM task for $VERSION remains open, mark it DONE manually."
echo ""

echo ""
echo "Release complete."
echo "  Version:    $VERSION"
echo "  Tagged as:  $RELEASE_TAG  (on $MAIN_BRANCH)"
echo "  RC deleted: $RC_BRANCH (local + origin)"
echo ""
echo "Summary of what shipped:"
echo "  - $RC_BRANCH squashed to $DEVELOP_BRANCH and pushed"
echo "  - $DEVELOP_BRANCH squashed to $MAIN_BRANCH and pushed"
echo "  - Tag $RELEASE_TAG created and pushed"
echo "  - $RC_BRANCH deleted from origin and locally"
exit 0
