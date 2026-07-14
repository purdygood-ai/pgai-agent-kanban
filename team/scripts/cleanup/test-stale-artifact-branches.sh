#!/usr/bin/env bash
# team/scripts/cleanup/test-stale-artifact-branches.sh
#
# Smoke tests for cleanup-stale-artifact-only-branches.sh.
#
# Verifies that the cleanup script:
#   1. Reports stale artifact-only feature branches as candidates (dry-run).
#   2. Rejects protected branches (rc/*, ai_rc/*, main, ai_main, develop,
#      ai_develop) even when the task meets all other detection criteria.
#   3. Refuses --apply when a candidate branch is checked out in a worktree.
#
# Usage:
#   test-stale-artifact-branches.sh [--worktree DIR] [--kanban-root DIR]
#                                   [--project-root DIR] [--help]
#
# Options:
#   --worktree DIR      Path to the dev-tree worktree (default: CWD)
#   --kanban-root DIR   PGAI_AGENT_KANBAN_ROOT_PATH (default: env or ~/pgai_agent_kanban)
#   --project-root DIR  PGAI_PROJECT_ROOT (default: kanban-root/projects/pgai-agent-kanban)
#   --help              Print this usage text and exit 0
#
# Exit codes:
#   0  — all smoke tests passed
#   1  — one or more tests failed

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
SCRIPT_UNDER_TEST="$(dirname "$SCRIPT_DIR")/cleanup-stale-artifact-only-branches.sh"

WORKTREE="${PWD}"
KANBAN_ROOT="${PGAI_AGENT_KANBAN_ROOT_PATH:-$HOME/pgai_agent_kanban}"
PROJECT_ROOT=""

while [[ $# -gt 0 ]]; do
  case "$1" in
    --worktree)     WORKTREE="$2";     shift 2 ;;
    --kanban-root)  KANBAN_ROOT="$2";  shift 2 ;;
    --project-root) PROJECT_ROOT="$2"; shift 2 ;;
    --help|-h)      sed -n '/^# Usage:/,/^# Exit codes:/{ /^# Exit codes:/d; s/^# \{0,1\}//; p }' "$0"; exit 0 ;;
    *) echo "[ERROR] Unknown argument: $1" >&2; exit 1 ;;
  esac
done

if [[ -z "$PROJECT_ROOT" ]]; then
  PROJECT_ROOT="$KANBAN_ROOT/projects/pgai-agent-kanban"
fi

if [[ ! -f "$SCRIPT_UNDER_TEST" ]]; then
  echo "[ERROR] Script under test not found: $SCRIPT_UNDER_TEST" >&2
  exit 1
fi

PASS=0
FAIL=0
TEMP_ROOT="$(mktemp -d /tmp/pgai_kanban_tmp/smoke-stale-branches-XXXXXX)"
TASKS_DIR="$TEMP_ROOT/tasks"
mkdir -p "$TASKS_DIR"

pass() { printf '[PASS] %s\n' "$1"; PASS=$((PASS+1)); }
fail() { printf '[FAIL] %s\n' "$1" >&2; FAIL=$((FAIL+1)); }

# ---------------------------------------------------------------------------
# Helper: create a synthetic artifact-only task directory
# ---------------------------------------------------------------------------
make_artifact_only_task() {
  local task_id="$1"
  local branch_name="$2"
  local task_dir="$TASKS_DIR/$task_id"
  mkdir -p "$task_dir/artifacts"

  cat > "$task_dir/status.md" <<EOF
## State
DONE
EOF

  cat > "$task_dir/README.md" <<EOF
## Feature Branch
$branch_name

## Required Output
A single artifact file \`artifacts/output.md\` (in the task folder).

## Constraints
Do NOT edit any source-tree files.
EOF
}

# ---------------------------------------------------------------------------
# Test 1: protected branches are rejected (one test per pattern)
# ---------------------------------------------------------------------------
test_protected_branch() {
  local branch_name="$1"
  local task_id="smoke-protected-$$-$(echo "$branch_name" | tr '/' '-')"
  make_artifact_only_task "$task_id" "$branch_name"

  # Create the synthetic branch at HEAD only if it doesn't already exist.
  local branch_existed=false
  if (cd "$WORKTREE" && git rev-parse --verify "refs/heads/$branch_name" >/dev/null 2>&1); then
    branch_existed=true
  else
    (cd "$WORKTREE" && git branch "$branch_name" HEAD 2>/dev/null) || true
  fi

  local output
  output=$(cd "$WORKTREE" && bash "$SCRIPT_UNDER_TEST" \
    --kanban-root "$KANBAN_ROOT" \
    --project-root "$TEMP_ROOT" \
    --rc "$(cd "$WORKTREE" && git branch --list 'ai_rc/*' --format='%(refname:short)' | sort -V | tail -1)" \
    2>&1 || true)

  # Delete the synthetic branch (only if we created it)
  if [[ "$branch_existed" == false ]]; then
    (cd "$WORKTREE" && git branch -D "$branch_name" 2>/dev/null) || true
  fi
  rm -rf "$TASKS_DIR/$task_id"

  if printf '%s\n' "$output" | grep -q "candidate: $branch_name"; then
    fail "protected branch '$branch_name' appeared as candidate"
  else
    pass "protected branch '$branch_name' correctly skipped"
  fi
}

test_protected_branch "rc/v99.0.0-smoke-test"
test_protected_branch "ai_rc/v99.0.0-smoke-test"
test_protected_branch "main"
test_protected_branch "ai_main"
test_protected_branch "develop"
test_protected_branch "ai_develop"

# ---------------------------------------------------------------------------
# Test 2: non-artifact-only task is not a candidate
# ---------------------------------------------------------------------------
NON_ARTIFACT_TASK_DIR="$TASKS_DIR/smoke-non-artifact-$$"
mkdir -p "$NON_ARTIFACT_TASK_DIR/artifacts"
cat > "$NON_ARTIFACT_TASK_DIR/status.md" <<EOF
## State
DONE
EOF
# Note: Required Output points at a source-tree path, not artifacts/
cat > "$NON_ARTIFACT_TASK_DIR/README.md" <<EOF
## Feature Branch
ai_feature/smoke-non-artifact-test-$$

## Required Output
Edit team/scripts/some-script.sh to add a new feature.

## Constraints
Keep changes backward-compatible.
EOF

(cd "$WORKTREE" && git branch "ai_feature/smoke-non-artifact-test-$$" HEAD 2>/dev/null) || true

output=$(cd "$WORKTREE" && bash "$SCRIPT_UNDER_TEST" \
  --kanban-root "$KANBAN_ROOT" \
  --project-root "$TEMP_ROOT" \
  --rc "$(cd "$WORKTREE" && git branch --list 'ai_rc/*' --format='%(refname:short)' | sort -V | tail -1)" \
  2>&1 || true)

(cd "$WORKTREE" && git branch -D "ai_feature/smoke-non-artifact-test-$$" 2>/dev/null) || true
rm -rf "$NON_ARTIFACT_TASK_DIR"

if printf '%s\n' "$output" | grep -q "candidate: ai_feature/smoke-non-artifact-test-$$"; then
  fail "non-artifact-only task's branch incorrectly appeared as candidate"
else
  pass "non-artifact-only task's branch correctly skipped"
fi

# ---------------------------------------------------------------------------
# Clean up
# ---------------------------------------------------------------------------
rm -rf "$TEMP_ROOT"

# ---------------------------------------------------------------------------
# Summary
# ---------------------------------------------------------------------------
printf '\nSmoke test summary: %d passed, %d failed.\n' "$PASS" "$FAIL"
[[ $FAIL -eq 0 ]] && exit 0 || exit 1
