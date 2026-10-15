#!/usr/bin/env bash
# team/scripts/harness/engine-behavior-baseline.sh
#
# Byte-format regression harness for three specific operator-facing engine
# surfaces (dashboard queue render, wake-log line shapes, CM ship-summary
# text) built from synthetic single-step fixtures.
#
# This harness is NOT the documented answer to "did agent behaviour change."
# team/scripts/harness/helloworld-flow-check.sh is — see this file's
# "RELATIONSHIP TO helloworld-flow-check.sh" section below for the resolution
# and the reasoning behind it.
#
# ===========================================================================
# USAGE
# ===========================================================================
#
#   engine-behavior-baseline.sh --capture <dir>
#   engine-behavior-baseline.sh --compare <dir>
#   engine-behavior-baseline.sh --help
#
# OPTIONS
#   --capture <dir>   Run all three captures and write normalized output into
#                      <dir> (created if absent). Always re-captures, even
#                      when <dir> already holds files from a prior run.
#   --compare <dir>    Diff a capture against the committed golden snapshots
#                      under team/tests/fixtures/engine-baseline/. If <dir>
#                      does not exist or is empty, a fresh capture is taken
#                      into it first (so "--compare <fresh-empty-dir>" is a
#                      self-contained one-shot check). If <dir> already
#                      contains capture output (e.g. from a prior --capture,
#                      possibly hand-edited to seed a diff), that existing
#                      output is compared as-is with NO re-capture — this is
#                      how a seeded one-byte change survives to the diff.
#   --help             Print this message and exit 0.
#
# EXIT CODES
#   0   Capture succeeded (--capture), or compare found no diffs (--compare).
#   1   Compare found one or more diffs.
#   2   Invocation error (missing argument, capture failure, etc.).
#
# ===========================================================================
# WHAT IS CAPTURED
# ===========================================================================
#
# Three operator-facing surfaces, each written as one file in the target
# directory:
#
#   board-render.txt   — team/scripts/dashboard/column-render.sh queue output
#                        for a synthetic single-project CODER backlog (one
#                        BACKLOG entry, one DONE entry).
#   wake-log.txt        — team/scripts/wake/harness.sh stdout+stderr for a
#                        synthetic TESTER task, run through the real wake
#                        substrate with the LLM call stubbed out.
#   ship-summary.txt    — the "Local release preparation complete." block
#                        (through EOF) from team/scripts/cm/release.sh,
#                        captured from a synthetic git repo carried through
#                        cm/open-rc.sh -> one feature commit -> cm/release.sh.
#
# HISTORY — why this harness uses synthetic fixtures instead of a real demo
# ---------------------------------------------------------------------------
# The requirements doc that commissioned this harness named a "helloworld
# flow" as the byte-identical capture target. No such project was
# registered in this tree at the time (see
# projects.cfg), so this harness substituted a self-contained synthetic
# fixture for each of the three named surfaces (wake logs, ship summary,
# board render), built fresh on every invocation so the harness never
# depended on kanban state that might not exist on a given host. That gap
# was hit again in a later release and is now closed by
# helloworld-flow-check.sh, which drives the real, shipped demo end to end
# against a throwaway, self-registering project root — see the relationship
# section below for what that means for this harness's own scope.
#
# ===========================================================================
# RELATIONSHIP TO helloworld-flow-check.sh
# ===========================================================================
# team/scripts/harness/helloworld-flow-check.sh is the documented answer to
# "did agent behaviour change" for the team/demos/helloworld-demo flow —
# it runs the real decompose -> CODER -> verify -> ship pipeline against
# hand-authored acceptance criteria fixed by the intake doc, self-registering
# its own throwaway project so the check never depends on a permanently
# registered pgai-helloworld project. When a release changes what agents
# actually produce (a materialized ticket's shape, an acceptance check's
# verdict, a release tag), that is the harness that catches it.
#
# This harness answers a different, narrower question: did three specific
# operator-facing RENDER SURFACES (dashboard queue text, wake substrate log
# line shapes, the CM release summary block) change byte-for-byte, using
# synthetic single-step fixtures this harness constructs itself rather than
# a real multi-role demo. helloworld-flow-check.sh does not exercise any of
# these three surfaces — it never calls column-render.sh, it drives its
# CODER/TESTER legs directly rather than through team/scripts/wake/harness.sh
# (see that harness's own header for why), and its outcome captures record
# stage verdicts, not rendered text. Losing this harness would mean no
# regression lock at all on the dashboard render or wake-log formats.
#
# Resolution: KEPT, not superseded. The two harnesses do not overlap after
# this change — each is the sole, documented answer to its own question, and
# neither claims the other's. If a future change makes helloworld-flow-check.sh
# (or a successor) capture these three render surfaces too, retire this
# harness at that point rather than letting the overlap re-open.
#
# ===========================================================================
# NORMALIZATION
# ===========================================================================
#
# Only volatile fields are scrubbed — never whole lines of operator text:
#   - ISO-8601 timestamps                    -> <TIMESTAMP>
#   - durations following "in "/"took "       -> <DURATION>
#   - PIDs ("pid 12345", "PID=12345")         -> "pid <PID>"
#   - absolute paths under the active temp root
#     (PGAI_AGENT_KANBAN_TEMP_DIR, default /tmp) -> <TMPPATH>
#   - git commit SHAs (7-40 lowercase hex chars, at least one a-f letter,
#     so plain numeric run/date/version literals are never touched)
#                                             -> <SHA>
#
# ===========================================================================
# ENVIRONMENT
# ===========================================================================
#   PGAI_AGENT_KANBAN_TEMP_DIR   Scratch root for ephemeral fixtures built
#                                during capture (default: /tmp/pgai_kanban_tmp).
#                                Never written to bare /tmp.
#
# The wake-log surface's capture step pins its own MAX_TASK_SECONDS and
# KILL_GRACE_SECONDS (see _GOLDEN_MAX_TASK_SECONDS / _GOLDEN_KILL_GRACE_SECONDS
# below) rather than reading them from the invoking shell, so neither
# variable needs to be set by the caller -- --capture and --compare produce
# the same wake-log bytes regardless of what the operator has configured.
#
set -euo pipefail

# ---------------------------------------------------------------------------
# Self-locate — each tree finds itself; never import from a sibling tree.
# ---------------------------------------------------------------------------
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/../../.." && pwd)"
TEAM_DIR="${REPO_ROOT}/team"

_COLUMN_RENDER="${TEAM_DIR}/scripts/dashboard/column-render.sh"
_WAKE_HARNESS="${TEAM_DIR}/scripts/wake/harness.sh"
_OPEN_RC="${TEAM_DIR}/scripts/cm/open-rc.sh"
_RELEASE_SH="${TEAM_DIR}/scripts/cm/release.sh"
_CM_PY_DIR="${TEAM_DIR}/pgai_agent_kanban/cm"

GOLDEN_DIR="${TEAM_DIR}/tests/fixtures/engine-baseline"

# The committed wake-log.txt golden (GOLDEN_DIR/wake-log.txt line 5) records
# the wake bootstrap's derived background-wait ceiling computed from these
# exact two values. This is the single source of truth for both of them —
# capture_wake_log below pins its own subprocess to these values instead of
# inheriting MAX_TASK_SECONDS / KILL_GRACE_SECONDS from the invoking shell,
# so a golden captured on any host reproduces the same bytes as one captured
# on any other, and the compare step (which pins nothing extra of its own)
# agrees with it by construction. Changing either value here requires
# recapturing and recommitting the wake-log golden in the same change.
_GOLDEN_MAX_TASK_SECONDS="5400"
_GOLDEN_KILL_GRACE_SECONDS="30"

SURFACE_NAMES=(board-render wake-log ship-summary)

TEMP_ROOT="${PGAI_AGENT_KANBAN_TEMP_DIR:-/tmp/pgai_kanban_tmp}"
SCRATCH_PARENT="${TEMP_ROOT}/harness/engine-behavior-baseline"

# ---------------------------------------------------------------------------
# Usage
# ---------------------------------------------------------------------------
# shellcheck source=../lib/script_common.sh
source "${SCRIPT_DIR}/../lib/script_common.sh"

# Usage text as data (per lib/script_common.sh's print_usage_and_exit contract):
# the header comment block of this file, self-read via grep/sed.
_USAGE_TEXT="$(grep '^#' "${BASH_SOURCE[0]}" | sed 's/^# \{0,1\}//' | sed -n '2,122p')"

# ---------------------------------------------------------------------------
# Argument parsing
# ---------------------------------------------------------------------------
MODE=""
TARGET_DIR=""

while [[ $# -gt 0 ]]; do
  case "$1" in
    --help|-h)
      print_usage_and_exit 0 "$_USAGE_TEXT"
      ;;
    --capture)
      MODE="capture"
      TARGET_DIR="${2:-}"
      shift 2
      ;;
    --compare)
      MODE="compare"
      TARGET_DIR="${2:-}"
      shift 2
      ;;
    *)
      echo "ERROR: unknown argument: $1" >&2
      print_usage_and_exit 2 "$_USAGE_TEXT"
      ;;
  esac
done

if [[ -z "$MODE" ]]; then
  echo "ERROR: one of --capture <dir> or --compare <dir> is required." >&2
  print_usage_and_exit 2 "$_USAGE_TEXT"
fi

if [[ -z "$TARGET_DIR" ]]; then
  echo "ERROR: --${MODE} requires a directory argument." >&2
  exit 2
fi

# ---------------------------------------------------------------------------
# Normalization — scrub volatile fields only (see header comment).
#
# Delegates to lib/normalize_baseline_text.py rather than an inline heredoc:
# `python3 - <<'PY' ... PY` would make python read its OWN script from stdin,
# which consumes the very stdin the piped capture text needs to land on.
# ---------------------------------------------------------------------------
_NORMALIZE_PY="${SCRIPT_DIR}/lib/normalize_baseline_text.py"

normalize_text() {
  local scratch_prefix="$1"
  python3 "$_NORMALIZE_PY" "$scratch_prefix" "$TEMP_ROOT"
}

# ---------------------------------------------------------------------------
# Surface 1: board render
# ---------------------------------------------------------------------------
capture_board_render() {
  local scratch="$1"
  local out_file="$2"
  local root="${scratch}/board-root"
  local proj="${root}/projects/harness-fixture"

  mkdir -p "${proj}/tasks/queues"

  cat > "${root}/projects.cfg" <<'EOF'
[project:harness-fixture]
priority=1
description=Behavior-baseline board-render fixture (synthetic, ephemeral)
enabled=true
EOF

  # provenance-allowlist: fixture literal, not a real task ID — the
  # AGENT-YYYYMMDD-NNN-slug shape is required so column-render.sh's compact-ID
  # parser exercises its real code path against this synthetic entry.
  mkdir -p "${proj}/tasks/CODER-20260101-001-sample-backlog-entry"
  cat > "${proj}/tasks/CODER-20260101-001-sample-backlog-entry/status.md" <<'EOF'
# Status

## State
BACKLOG
EOF

  # provenance-allowlist: fixture literal, not a real task ID — see note above.
  mkdir -p "${proj}/tasks/CODER-20260101-002-sample-done-entry"
  cat > "${proj}/tasks/CODER-20260101-002-sample-done-entry/status.md" <<'EOF'
# Status

## State
DONE
EOF

  # provenance-allowlist: fixture literals, not real task IDs — see note above.
  cat > "${proj}/tasks/queues/coder_backlog.md" <<'EOF'
# CODER Backlog

- [ ] CODER-20260101-001-sample-backlog-entry — sample in-flight fixture entry
- [x] CODER-20260101-002-sample-done-entry — sample completed fixture entry
EOF

  local raw
  raw="$(
    PGAI_AGENT_KANBAN_ROOT_PATH="${TEAM_DIR}" \
    NO_COLOR=1 \
    TERM=dumb \
    bash "${_COLUMN_RENDER}" queue none 5 38 \
      --label CODER \
      --kanban-root "${root}" \
      --all-projects \
      --column-type coder \
    2>&1
  )" || {
    echo "FAIL: board-render capture failed (column-render.sh exited non-zero)." >&2
    echo "$raw" >&2
    return 1
  }

  printf '%s\n' "$raw" | normalize_text "$scratch" > "$out_file"
}

# ---------------------------------------------------------------------------
# Surface 2: wake log line shapes
# ---------------------------------------------------------------------------
capture_wake_log() {
  local scratch="$1"
  local out_file="$2"
  local root="${scratch}/wake-root"
  local proj="${root}/projects/harness-fixture"

  mkdir -p "${proj}/tasks/queues" "${proj}/logs" "${root}/logs/agents" "${root}/logs/debug" "${root}/locks"

  # workflow dispatcher requires a workflows/ root with the plugin matching
  # the task's ## Workflow Type (release, used below).
  mkdir -p "${root}/workflows"
  if [[ -d "${TEAM_DIR}/workflows/release" ]]; then
    cp -r "${TEAM_DIR}/workflows/release" "${root}/workflows/release"
  fi

  cat > "${root}/kanban.cfg" <<'EOF'
[paths]

[chain]
pm_mode = automatic

[wake]
max_tasks_per_wake = 5

[providers]
active = harness
EOF

  cat > "${root}/projects.cfg" <<'EOF'
[project:harness-fixture]
priority=1
description=Behavior-baseline wake-log fixture (synthetic, ephemeral)
enabled=true
EOF

  cat > "${proj}/project.cfg" <<EOF
[project]
project_name = harness-fixture
dev_tree_path = /dev/null
git_repo_url =
git_remote_name = origin
workflow_type = release
is_self_build = false
push_to_remote = false

[versioning]
max_patch = 99
max_minor = 9
max_major = 0
EOF

  cat > "${proj}/release-state.md" <<'EOF'
# Release State

## Active RC
none

## RC Opened At
none

## RC Opened By Task
none
EOF

  for agent in coder pm writer tester cm bug priority; do
    printf '# %s Backlog\n\n' "$(echo "$agent" | tr '[:lower:]' '[:upper:]')" \
      > "${proj}/tasks/queues/${agent}_backlog.md"
  done

  # provenance-allowlist: fixture literal, not a real task ID — see the
  # board-render fixture note above for why the ID shape is required.
  local task_id="TESTER-20260101-001-sample-verification-task"
  mkdir -p "${proj}/tasks/${task_id}/artifacts"
  cat > "${proj}/tasks/${task_id}/README.md" <<EOF
# Task: Sample Verification Task

## Task ID
${task_id}

## Role
TESTER

## Workflow Type
release

## Source Branch
main
EOF

  cat > "${proj}/tasks/${task_id}/status.md" <<'EOF'
# Status

## State
BACKLOG

## Summary
none
EOF

  printf -- '- [ ] %s — sample fixture verification ticket\n' "$task_id" \
    > "${proj}/tasks/queues/tester_backlog.md"

  local raw rc
  set +e
  raw="$(
    env -u PGAI_TASKS_DIR -u PGAI_QUEUE_DIR -u PGAI_RELEASE_STATE_PATH \
        -u PGAI_REQUIREMENTS_DIR -u PGAI_DEV_TREE_PATH \
      PGAI_AGENT_KANBAN_ROOT_PATH="${root}" \
      PGAI_PROJECT_ROOT="${proj}" \
      PGAI_PROJECT_NAME="harness-fixture" \
      PYTHONPATH="${TEAM_DIR}${PYTHONPATH:+:${PYTHONPATH}}" \
      MAX_TASK_SECONDS="${_GOLDEN_MAX_TASK_SECONDS}" \
      KILL_GRACE_SECONDS="${_GOLDEN_KILL_GRACE_SECONDS}" \
      bash "${_WAKE_HARNESS}" --agent=tester --max-tasks=1 \
    2>&1
  )"
  rc=$?
  set -e
  if [[ "$rc" -ne 0 ]]; then
    echo "FAIL: wake-log capture failed (wake/harness.sh exited ${rc})." >&2
    echo "$raw" >&2
    return 1
  fi

  printf '%s\n' "$raw" | normalize_text "$scratch" > "$out_file"
}

# ---------------------------------------------------------------------------
# Surface 3: CM ship summary block
# ---------------------------------------------------------------------------
capture_ship_summary() {
  local scratch="$1"
  local out_file="$2"
  local bare_remote="${scratch}/ship/bare_remote.git"
  local repo_path="${scratch}/ship/dev_repo"
  local root="${scratch}/ship/kanban_root"
  local proj="${root}/projects/harness-fixture"
  local version="v0.1.0"

  mkdir -p "$bare_remote" "$repo_path"
  git init --bare "$bare_remote" >/dev/null
  git -C "$repo_path" init --initial-branch=main >/dev/null
  git -C "$repo_path" config --local user.email "harness@pgai.local"
  git -C "$repo_path" config --local user.name "PGAI Harness"
  printf '# Synthetic release fixture repo\n' > "${repo_path}/README.md"
  mkdir -p "${repo_path}/release-notes"
  : > "${repo_path}/release-notes/PUBLISHED"
  git -C "$repo_path" add README.md release-notes/PUBLISHED
  git -C "$repo_path" commit -m "Initial commit" >/dev/null
  git -C "$repo_path" remote add origin "$bare_remote"
  git -C "$repo_path" push origin main >/dev/null 2>&1

  mkdir -p "$proj" "${root}/logs" "${root}/locks" "${proj}/release-state" "${proj}/tasks/queues"
  cat > "${root}/kanban.cfg" <<'EOF'
[paths]

[chain]
pm_mode = automatic

[wake]
max_tasks_per_wake = 1
max_runtime_seconds = 600
EOF

  cat > "${root}/projects.cfg" <<'EOF'
[project:harness-fixture]
priority=1
description=Behavior-baseline ship-summary fixture (synthetic, ephemeral)
enabled=true
EOF

  cat > "${proj}/project.cfg" <<EOF
[project]
project_name = harness-fixture
dev_tree_path = ${repo_path}
git_repo_url = git@example.com:harness-fixture.git
git_remote_name = origin
workflow_type = release
is_self_build = false
push_to_remote = false

[versioning]
max_patch = 99
max_minor = 9
max_major = 0
EOF

  cat > "${proj}/release-state.md" <<'EOF'
# Release State

## Active RC
none

## RC Opened At
none

## RC Opened By Task
none
EOF

  mkdir -p "${root}/pgai_agent_kanban/cm"
  cp "${_CM_PY_DIR}"/*.py "${root}/pgai_agent_kanban/cm/" 2>/dev/null || true

  local open_out release_out rc

  set +e
  open_out="$(
    env -u PGAI_DEV_TREE_PATH \
      PGAI_AGENT_KANBAN_ROOT_PATH="${root}" \
      KANBAN_ROOT="${root}" \
      TEAM_ROOT="${root}" \
      PGAI_PROJECT_NAME="harness-fixture" \
      REPO_ROOT="${repo_path}" \
      bash "${_OPEN_RC}" "${version}" \
    2>&1
  )"
  rc=$?
  set -e
  if [[ "$rc" -ne 0 ]]; then
    echo "FAIL: ship-summary capture failed (cm/open-rc.sh exited ${rc})." >&2
    echo "$open_out" >&2
    return 1
  fi

  local rc_branch="rc/${version}"
  git -C "$repo_path" checkout "$rc_branch" >/dev/null 2>&1
  printf 'harness fixture feature content\n' > "${repo_path}/feature.txt"
  git -C "$repo_path" add feature.txt
  git -C "$repo_path" commit -m "feat: add fixture feature file" >/dev/null
  git -C "$repo_path" checkout main >/dev/null 2>&1

  set +e
  release_out="$(
    env -u PGAI_DEV_TREE_PATH \
      PGAI_AGENT_KANBAN_ROOT_PATH="${root}" \
      KANBAN_ROOT="${root}" \
      TEAM_ROOT="${root}" \
      PGAI_PROJECT_NAME="harness-fixture" \
      REPO_ROOT="${repo_path}" \
      bash "${_RELEASE_SH}" \
    2>&1
  )"
  rc=$?
  set -e
  if [[ "$rc" -ne 0 ]]; then
    echo "FAIL: ship-summary capture failed (cm/release.sh exited ${rc})." >&2
    echo "$release_out" >&2
    return 1
  fi

  local summary_block
  summary_block="$(printf '%s\n' "$release_out" | sed -n '/^Local release preparation complete\.$/,$p')"
  if [[ -z "$summary_block" ]]; then
    echo "FAIL: ship-summary capture did not find the 'Local release preparation complete.' marker in cm/release.sh output." >&2
    echo "$release_out" >&2
    return 1
  fi

  printf '%s\n' "$summary_block" | normalize_text "$scratch" > "$out_file"
}

# ---------------------------------------------------------------------------
# Capture orchestration
# ---------------------------------------------------------------------------
run_capture() {
  local dir="$1"
  mkdir -p "$dir"
  local scratch
  scratch="$(mktemp -d "${SCRATCH_PARENT}.XXXXXX" 2>/dev/null || { mkdir -p "$SCRATCH_PARENT" && mktemp -d "${SCRATCH_PARENT}/run.XXXXXX"; })"
  trap 'rm -rf "$scratch"' RETURN

  echo "[engine-behavior-baseline] capturing board-render -> ${dir}/board-render.txt"
  capture_board_render "$scratch" "${dir}/board-render.txt"

  echo "[engine-behavior-baseline] capturing wake-log -> ${dir}/wake-log.txt"
  capture_wake_log "$scratch" "${dir}/wake-log.txt"

  echo "[engine-behavior-baseline] capturing ship-summary -> ${dir}/ship-summary.txt"
  capture_ship_summary "$scratch" "${dir}/ship-summary.txt"
}

_dir_has_captures() {
  local dir="$1"
  local name
  for name in "${SURFACE_NAMES[@]}"; do
    if [[ -s "${dir}/${name}.txt" ]]; then
      return 0
    fi
  done
  return 1
}

run_compare() {
  local dir="$1"

  if [[ ! -d "$dir" ]] || ! _dir_has_captures "$dir"; then
    echo "[engine-behavior-baseline] ${dir} has no existing capture — capturing fresh."
    run_capture "$dir"
  fi

  mkdir -p "$SCRATCH_PARENT"
  local diff_tmp
  diff_tmp="$(mktemp "${SCRATCH_PARENT}/diff.XXXXXX")"
  trap 'rm -f "$diff_tmp"' RETURN

  local status=0
  local name
  for name in "${SURFACE_NAMES[@]}"; do
    local golden="${GOLDEN_DIR}/${name}.txt"
    local fresh="${dir}/${name}.txt"

    if [[ ! -f "$golden" ]]; then
      echo "FAIL: golden snapshot missing: ${golden}" >&2
      echo "  To fix: run 'bash ${BASH_SOURCE[0]} --capture ${GOLDEN_DIR}' and commit the result." >&2
      status=1
      continue
    fi
    if [[ ! -f "$fresh" ]]; then
      echo "FAIL: expected capture file missing: ${fresh}" >&2
      status=1
      continue
    fi

    if ! diff -u "$golden" "$fresh" > "$diff_tmp" 2>&1; then
      echo "FAIL: ${name}.txt differs from golden ${golden}"
      cat "$diff_tmp"
      status=1
    fi
  done

  if [[ "$status" -eq 0 ]]; then
    echo "[engine-behavior-baseline] compare OK — ${#SURFACE_NAMES[@]} surfaces match goldens under ${GOLDEN_DIR}."
  fi
  return "$status"
}

case "$MODE" in
  capture)
    run_capture "$TARGET_DIR"
    echo "[engine-behavior-baseline] capture complete: $TARGET_DIR"
    ;;
  compare)
    run_compare "$TARGET_DIR"
    ;;
esac
