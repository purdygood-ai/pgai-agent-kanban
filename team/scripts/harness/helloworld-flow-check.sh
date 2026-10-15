#!/usr/bin/env bash
# team/scripts/harness/helloworld-flow-check.sh
#
# Runs the shipped team/demos/helloworld-demo flow end to end from one
# command, against a throwaway, self-registering kanban root — no operator
# action, no edit to the live projects.cfg, no registered pgai-helloworld
# project required on the host.
#
# ===========================================================================
# USAGE
# ===========================================================================
#   helloworld-flow-check.sh [--help]
#   helloworld-flow-check.sh --capture <dir>
#   helloworld-flow-check.sh --compare <dir>
#
#   --help, -h       Print this message and exit 0.
#   --capture <dir>  Run the flow and write one normalized, diffable outcome
#                     file per stage (decompose.txt, verify.txt, ship.txt)
#                     into <dir>, creating it if absent. Always re-runs the
#                     flow and overwrites whatever is already in <dir>.
#   --compare <dir>  Diff a capture against the committed golden outcomes
#                     under team/tests/fixtures/helloworld-flow/. If <dir>
#                     does not exist or holds no outcome files, a fresh
#                     --capture into <dir> is taken first (so
#                     "--compare <fresh-empty-dir>" is a self-contained
#                     one-shot check). If <dir> already holds outcome files
#                     (e.g. from a prior --capture, possibly hand-edited to
#                     seed a diff), that existing output is compared
#                     AS-IS — the flow is not re-run and the files are not
#                     overwritten — which is how a seeded one-byte change
#                     survives to the diff.
#
#   A bare invocation with no flags (or with only --help) runs the flow and
#   prints a pass/fail summary per stage; it captures nothing to disk.
#
# ===========================================================================
# EXIT CODES
# ===========================================================================
# Bare invocation (no --capture/--compare):
#   0   All driven stages passed.
#   1   Setup failed (throwaway root, project registration, intake deposit,
#       or decompose) before any stage outcome could be produced.
#   2   One of the three driven stages (decompose, verify, ship) failed.
#
# --capture / --compare:
#   0   --capture wrote all three outcome files; or --compare found no
#       differences against the golden directory.
#   1   --compare found one or more differences (including a missing
#       expected file on either side of the diff).
#   2   Invocation error: a bad or missing flag argument, or the underlying
#       flow failed (setup or a driven stage) before an outcome could be
#       captured at all. This does not redefine the bare-invocation codes
#       above: a bare invocation still uses the three codes immediately
#       above unchanged, regardless of which flags a --capture/--compare
#       invocation also supports.
#
# ===========================================================================
# WHAT THIS SCRIPT DOES
# ===========================================================================
# Reproduces team/demos/helloworld-demo/README.md's setup and run steps
# without operator action, then drives the resulting tickets to completion:
#
#   1. Stands up a throwaway kanban root under $PGAI_AGENT_KANBAN_TEMP_DIR
#      (via team/scripts/harness/lib/throwaway-root.sh) and a throwaway
#      scratch git repo inside that same space (the demo's "make a local
#      scratch repo" step).
#   2. Registers pgai-helloworld inside that throwaway root ONLY, via the
#      real team/scripts/create-project.sh (the demo's "create the project"
#      step), pointed at the scratch repo, with push_to_remote patched to
#      false (the demo's "wire the project to your scratch repo" step —
#      there is no interactive $EDITOR session in a harness run).
#   3. Deposits team/demos/helloworld-demo/intake/v0.1.0-helloworld.md via
#      the real team/scripts/intake.sh (the demo's "run it" step).
#   4. DECOMPOSE — authors the plan JSON matching the intake doc's own
#      "Suggested Decomposition" (one CODER ticket) and materializes it via
#      the real team/pm-agent/pm_materialize.py, which injects the CM-open-rc
#      / TESTER / CM-release bookends for a release-workflow plan. This is
#      the same materializer production PM invokes after an LLM produces a
#      plan; the plan is hand-authored here because it is fixed by the
#      intake doc, not something an LLM needs to infer.
#   5. SHIP-side execution — walks the four materialized tickets in
#      dependency order and performs each role's real action:
#        - CM-open-rc:  team/scripts/cm/open-rc.sh (real script)
#        - CODER:       writes hello.py + test_hello.py (content fixed by
#                        the intake doc's Deliverables section) on a feature
#                        branch cut from the RC branch, commits, merges
#                        --no-ff into the RC branch, deletes the feature
#                        branch — the same sequence CODER.md's Procedure A
#                        documents.
#        - TESTER:      runs the intake doc's own acceptance-criteria
#                        commands (python3 hello.py, pytest, py_compile)
#                        against the merged RC branch and plants a report.
#        - CM-release:  team/scripts/cm/release.sh (real script) — tags the
#                        release in the scratch repo.
#
# DETERMINISM / WHY THE HARNESS PERFORMS CODER'S AND TESTER'S WORK DIRECTLY
# ---------------------------------------------------------------------------
# No live model invocation is added to a path the test suite runs, because a
# model call is slow and non-deterministic and this harness's whole value is
# a byte-identical, script-driven comparison. team/scripts/wake/harness.sh
# (the stub wake provider) is documented to simulate TESTER completion
# generically, but it does not know how to write hello.py or run pytest —
# that content is what a live CODER/TESTER subagent would normally supply.
# Since the helloworld intake fully fixes that content (one function, one
# print statement, one test), writing it here is not "faking" the flow; it
# is the deterministic subset available once model-driven authorship is
# ruled out for the reason above. The CM legs (open-rc.sh, release.sh) are
# always real production script invocations — nothing about the release
# engine itself is simulated.
#
# ===========================================================================
# WHAT --capture RECORDS (and why it is byte-identical across two runs)
# ===========================================================================
# One file per stage, each recording the OUTCOME rather than a transcript —
# a summary of the shape "decompose produced N tickets with these roles and
# slugs" is more durable and readable than a scrubbed log:
#
#   decompose.txt   — the count of materialized tickets and, for each, its
#                      role and slug (NOT its full task ID: the ID embeds
#                      today's date and a sequence number, both of which are
#                      already deterministic given a fresh throwaway root
#                      per run, but role+slug is the durable identity a
#                      governance change would actually perturb, so that is
#                      what is captured).
#   verify.txt      — the verdict and the fixed list of acceptance commands
#                      the TESTER leg ran (python3 hello.py, pytest, py_compile).
#   ship.txt        — the verdict and the release tag cm/release.sh cut.
#
# Every field captured above is already deterministic on a stock install
# (fixed intake content, fixed project-registration defaults, fixed
# version) — no timestamp, PID, absolute temp path, or commit SHA is ever
# written into these files, which is what makes two consecutive --capture
# runs into different directories byte-identical without needing to blank
# any operator-facing text. Each outcome string is still piped through
# team/scripts/harness/lib/normalize_baseline_text.py before being written
# (the same normalizer engine-behavior-baseline.sh uses — reused rather
# than re-implemented, so the two harnesses cannot drift onto two different
# scrubbing rule sets) as defense in depth, so a future field added to
# these outcomes that DOES carry a volatile value is scrubbed automatically
# instead of silently breaking the byte-identical guarantee.
#
# ===========================================================================
# RELATIONSHIP TO team/scripts/harness/engine-behavior-baseline.sh
# ===========================================================================
# RESOLUTION: this harness — helloworld-flow-check.sh — is the documented
# answer to "did agent behaviour change." It drives the real,
# shipped team/demos/helloworld-demo flow end to end (decompose -> CODER ->
# verify -> ship) against a throwaway, self-registering project root, and
# its --capture/--compare contract makes "unchanged from before" a mechanical
# diff against a committed golden rather than a judgement call. When a
# release changes what agents actually produce — a materialized ticket's
# shape, an acceptance check's verdict, a release tag — this is the harness
# that catches it.
#
# engine-behavior-baseline.sh is KEPT, not superseded, but it no longer
# claims to answer the same question. It captures byte-normalized snapshots
# of three operator-facing RENDER surfaces (dashboard queue render, wake-log
# line shapes, CM ship-summary text) from synthetic single-step fixtures —
# a narrower, format-regression concern this harness does not cover at all
# (this harness never calls column-render.sh, drives its CODER/TESTER legs
# directly rather than through team/scripts/wake/harness.sh, and its outcome
# captures record stage verdicts, not rendered text). See that harness's own
# header, section "RELATIONSHIP TO helloworld-flow-check.sh", for the same
# resolution stated from its side — the two headers must agree, and this is
# that agreement.
#
# The two harnesses' --capture/--compare contracts still mirror each other
# (same flag names, same exit-code shape, same normalizer) purely so they
# stay easy to reason about side by side; that shared shape is not itself a
# claim of overlapping coverage.
#
# GOLDEN DIRECTORY
# ---------------------------------------------------------------------------
#   team/tests/fixtures/helloworld-flow/
#     decompose.txt
#     verify.txt
#     ship.txt
#
# REPEATABILITY
# ===========================================================================
# Every invocation creates a brand-new throwaway root via
# harness_new_throwaway_root (mktemp -d under
# $PGAI_AGENT_KANBAN_TEMP_DIR/harness/helloworld-flow-check/), so a second
# consecutive run never collides with the first and needs no manual cleanup.
# Nothing under the operator's live $PGAI_AGENT_KANBAN_ROOT_PATH is read or
# written by this script.
#
# ===========================================================================
# ENVIRONMENT
# ===========================================================================
#   PGAI_AGENT_KANBAN_TEMP_DIR   Scratch root for the throwaway kanban root
#                                and scratch git repo (default:
#                                pgai_kanban_tmp under the system temp dir —
#                                see team/scripts/lib/temp.sh).
#
set -euo pipefail

# ---------------------------------------------------------------------------
# Self-locate — each tree finds itself; never import from a sibling tree.
# ---------------------------------------------------------------------------
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/../../.." && pwd)"
TEAM_DIR="${REPO_ROOT}/team"

# shellcheck source=../lib/script_common.sh
source "${SCRIPT_DIR}/../lib/script_common.sh"
# shellcheck source=lib/throwaway-root.sh
source "${SCRIPT_DIR}/lib/throwaway-root.sh"
# shellcheck source=../lib/project_paths.sh
source "${TEAM_DIR}/scripts/lib/project_paths.sh"
# shellcheck source=../lib/wake_task_state.sh
source "${TEAM_DIR}/scripts/lib/wake_task_state.sh"

# wake_task_state.sh's ensure_status_file() calls log(); this harness is not
# run through the wake substrate, so define the minimal stderr logger it
# expects rather than pull in the rest of wake_common.sh.
log() {
  echo "[helloworld-flow-check] $*" >&2
}

# ---------------------------------------------------------------------------
# --capture / --compare constants and helpers.
# ---------------------------------------------------------------------------
GOLDEN_DIR="${TEAM_DIR}/tests/fixtures/helloworld-flow"
OUTCOME_NAMES=(decompose verify ship)
TEMP_ROOT="${PGAI_AGENT_KANBAN_TEMP_DIR:-/tmp/pgai_kanban_tmp}"
_NORMALIZE_PY="${SCRIPT_DIR}/lib/normalize_baseline_text.py"

# Delegates to the shared normalizer (team/scripts/harness/lib/
# normalize_baseline_text.py) rather than a second, drifting rule set.
# Reads raw text on stdin, writes normalized text on stdout.
normalize_text() {
  local scratch_prefix="$1"
  python3 "$_NORMALIZE_PY" "$scratch_prefix" "$TEMP_ROOT"
}

_USAGE_TEXT="$(grep '^#' "${BASH_SOURCE[0]}" | sed 's/^# \{0,1\}//' | sed -n '2,194p')"

# ---------------------------------------------------------------------------
# run_isolated <NAME=value>... -- <command> [args...]
#
# Runs a subprocess with a CLEAN environment (env -i) plus only the
# NAME=value pairs the caller supplies (and PATH/HOME, so python3/git/bash
# still resolve). This is load-bearing, not cosmetic: this harness's own
# invoking wake session exports PGAI_TASKS_DIR, PGAI_PROJECT_ROOT,
# PGAI_DEV_TREE_PATH, and friends pointed at the LIVE kanban-self project.
# create-project.sh/intake.sh/cm scripts self-resolve KANBAN_ROOT from a
# single explicit variable, but pm_materialize.py's get_config() lets any
# PGAI_* env var it recognizes (PGAI_TASKS_DIR in particular) win over the
# --team-root flag — an ambient PGAI_TASKS_DIR silently redirects task-folder
# writes to wherever that ambient value points, regardless of --team-root.
# A prefix assignment on the command line does not protect against this: it
# only ADDS a variable, it does not remove ones already exported in this
# script's own shell. env -i is what actually clears the hazard.
# ---------------------------------------------------------------------------
run_isolated() {
  local -a assigns=()
  while [[ $# -gt 0 && "$1" != "--" ]]; do
    assigns+=("$1")
    shift
  done
  shift || true  # drop the "--" separator
  env -i PATH="$PATH" HOME="$HOME" "${assigns[@]}" "$@"
}

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
      # Guard the shift count explicitly (rather than `shift 2` unguarded):
      # under `set -e`, a `shift 2` with only one argument left aborts the
      # script immediately with exit 1 before the "requires a directory
      # argument" check below ever runs, which would misreport a missing
      # argument as a diff-found exit instead of an invocation error.
      if [[ $# -lt 2 ]]; then
        echo "ERROR: --capture requires a directory argument." >&2
        exit 2
      fi
      MODE="capture"
      TARGET_DIR="$2"
      shift 2
      ;;
    --compare)
      if [[ $# -lt 2 ]]; then
        echo "ERROR: --compare requires a directory argument." >&2
        exit 2
      fi
      MODE="compare"
      TARGET_DIR="$2"
      shift 2
      ;;
    *)
      echo "ERROR: unknown argument: $1" >&2
      print_usage_and_exit 2 "$_USAGE_TEXT"
      ;;
  esac
done

PROJECT_NAME="pgai-helloworld"
VERSION="v0.1.0"
INTAKE_FILE="${TEAM_DIR}/demos/helloworld-demo/intake/v0.1.0-helloworld.md"

STAGE_DECOMPOSE="not-attempted"
STAGE_VERIFY="not-attempted"
STAGE_SHIP="not-attempted"

# Ticket identity captured during the decompose stage (role+slug only — see
# the "WHAT --capture RECORDS" header section for why full task IDs are not
# used). Populated by run_flow(); read by build_decompose_capture().
CM_OPEN_ID=""
CODER_ID=""
TESTER_ID=""
CM_RELEASE_ID=""
RELEASE_TAG=""

fail_setup() {
  echo "FAIL (setup): $*" >&2
  # A setup failure in --capture/--compare mode means nothing was captured
  # at all — that is an invocation-error outcome (exit 2), not a "diff
  # found" outcome (exit 1). A bare invocation keeps ticket 1's exit 1.
  if [[ "$MODE" == "capture" || "$MODE" == "compare" ]]; then
    exit 2
  fi
  exit 1
}

fail_stage() {
  local stage="$1"
  shift
  echo "FAIL (${stage}): $*" >&2
  echo "[helloworld-flow-check] decompose: ${STAGE_DECOMPOSE}"
  echo "[helloworld-flow-check] verify: ${STAGE_VERIFY}"
  echo "[helloworld-flow-check] ship: ${STAGE_SHIP}"
  exit 2
}

# ---------------------------------------------------------------------------
# _ticket_role_slug <task_id>
# Splits a materialized ROLE-YYYYMMDD-NNN-slug task ID into "role slug",
# dropping the volatile date/sequence fields (see header comment).
# ---------------------------------------------------------------------------
_ticket_role_slug() {
  local id="$1"
  local role="${id%%-*}"
  local rest="${id#*-}"   # date-seq-slug...
  rest="${rest#*-}"        # seq-slug...
  local slug="${rest#*-}"  # slug (may itself contain dashes)
  printf '%s %s' "$role" "$slug"
}

build_decompose_capture() {
  local cm_open_role cm_open_slug coder_role coder_slug
  local tester_role tester_slug cm_release_role cm_release_slug
  read -r cm_open_role cm_open_slug <<<"$(_ticket_role_slug "$CM_OPEN_ID")"
  read -r coder_role coder_slug <<<"$(_ticket_role_slug "$CODER_ID")"
  read -r tester_role tester_slug <<<"$(_ticket_role_slug "$TESTER_ID")"
  read -r cm_release_role cm_release_slug <<<"$(_ticket_role_slug "$CM_RELEASE_ID")"
  cat <<EOF
stage: decompose
result: PASS
ticket_count: 4
ticket 1: role=${cm_open_role} slug=${cm_open_slug}
ticket 2: role=${coder_role} slug=${coder_slug}
ticket 3: role=${tester_role} slug=${tester_slug}
ticket 4: role=${cm_release_role} slug=${cm_release_slug}
EOF
}

build_verify_capture() {
  cat <<'EOF'
stage: verify
result: PASS
check: python3 hello.py -> exact stdout "Hello, World!"
check: python3 -m pytest test_hello.py -> exit 0
check: python3 -m py_compile hello.py -> exit 0
EOF
}

build_ship_capture() {
  cat <<EOF
stage: ship
result: PASS
release_tag: ${RELEASE_TAG}
EOF
}

# write_capture_files <dir> — run only after run_flow() has completed all
# three stages successfully (fail_setup/fail_stage exit before this point on
# any failure, so every field these builders read is already populated).
write_capture_files() {
  local dir="$1"
  mkdir -p "$dir"
  build_decompose_capture | normalize_text "$ROOT" > "${dir}/decompose.txt"
  build_verify_capture | normalize_text "$ROOT" > "${dir}/verify.txt"
  build_ship_capture | normalize_text "$ROOT" > "${dir}/ship.txt"
}

_dir_has_captures() {
  local dir="$1"
  local name
  for name in "${OUTCOME_NAMES[@]}"; do
    if [[ -s "${dir}/${name}.txt" ]]; then
      return 0
    fi
  done
  return 1
}

compare_against_golden() {
  local dir="$1"
  local status=0
  local name golden fresh

  for name in "${OUTCOME_NAMES[@]}"; do
    golden="${GOLDEN_DIR}/${name}.txt"
    fresh="${dir}/${name}.txt"

    if [[ ! -f "$golden" ]]; then
      echo "FAIL: golden outcome missing: ${golden}" >&2
      echo "  To fix: run 'bash ${BASH_SOURCE[0]} --capture ${GOLDEN_DIR}' and commit the result." >&2
      status=1
      continue
    fi
    if [[ ! -f "$fresh" ]]; then
      echo "FAIL: expected capture file missing: ${fresh}" >&2
      status=1
      continue
    fi

    if ! diff -u "$golden" "$fresh"; then
      echo "FAIL: ${name}.txt differs from golden ${golden}"
      status=1
    fi
  done

  if [[ "$status" -eq 0 ]]; then
    echo "[helloworld-flow-check] compare OK — ${#OUTCOME_NAMES[@]} outcomes match goldens under ${GOLDEN_DIR}."
  fi
  exit "$status"
}

# ===========================================================================
# run_flow — everything from "stand up the throwaway root" through
# "STAGE_SHIP=PASS", i.e. the whole decompose/coder/verify/ship drive
# described in the header. A bare invocation, --capture, and --compare (on a
# not-yet-captured directory) all call this exactly once; --compare against
# an already-captured directory does not call it at all (see header).
# ===========================================================================
run_flow() {
[[ -f "$INTAKE_FILE" ]] || fail_setup "intake file not found: ${INTAKE_FILE}"

# ---------------------------------------------------------------------------
# Step 1 — throwaway root + scratch repo (demo Setup step 1)
# ---------------------------------------------------------------------------
ROOT="$(harness_new_throwaway_root "helloworld-flow-check")"
REPO_PATH="${ROOT}/scratch-repo/pgai-helloworld-src"

# cm/open-rc.sh and cm/release.sh shell out to python helpers at
# $KANBAN_ROOT/pgai_agent_kanban/cm/*.py — the live-install layout install.sh
# deploys alongside a real kanban root. A throwaway root needs the same
# layout seeded, or those scripts fail with "can't open file ...read_state_
# field.py". Mirrors engine-behavior-baseline.sh's capture_ship_summary.
mkdir -p "${ROOT}/pgai_agent_kanban/cm"
cp "${TEAM_DIR}/pgai_agent_kanban/cm/"*.py "${ROOT}/pgai_agent_kanban/cm/"

log "throwaway root: ${ROOT}"
log "scratch repo:   ${REPO_PATH}"

# ---------------------------------------------------------------------------
# Step 2 — register the project (demo Setup step 2), throwaway root ONLY
# ---------------------------------------------------------------------------
run_isolated PGAI_AGENT_KANBAN_ROOT_PATH="$ROOT" -- \
  bash "${TEAM_DIR}/scripts/create-project.sh" \
    --project "$PROJECT_NAME" \
    --workflow-type release \
    --dev-tree "$REPO_PATH" \
  || fail_setup "create-project.sh failed for ${PROJECT_NAME}"

PROJECT_ROOT="${ROOT}/projects/${PROJECT_NAME}"
PROJECT_CFG="${PROJECT_ROOT}/project.cfg"
[[ -f "$PROJECT_CFG" ]] || fail_setup "project.cfg not written at ${PROJECT_CFG}"

# demo Setup step 3 ($EDITOR project.cfg) — no interactive editor in a
# harness run; push_to_remote is the one field create-project.sh's default
# does not already match the demo's local-only intent (branch_prefix
# defaults to ai_ already; dev_tree_path was supplied above; git_repo_url
# is intentionally left empty for a local-only install).
sed -i 's/^push_to_remote = true$/push_to_remote = false/' "$PROJECT_CFG"
grep -q '^push_to_remote = false$' "$PROJECT_CFG" \
  || fail_setup "could not patch push_to_remote=false in ${PROJECT_CFG}"

# ---------------------------------------------------------------------------
# Resolve the prefix-aware branch/tag names via the real project_paths.sh
# helpers (not hardcoded), now that the project is registered.
# ---------------------------------------------------------------------------
export PGAI_AGENT_KANBAN_ROOT_PATH="$ROOT"
export KANBAN_ROOT="$ROOT"
export PGAI_PROJECT_NAME="$PROJECT_NAME"

MAIN_BRANCH="$(pp_prefix_branch "$PROJECT_NAME" "main")"
RC_BRANCH="$(pp_prefix_branch "$PROJECT_NAME" "rc/${VERSION}")"
RELEASE_TAG="$(pp_prefix_tag "$PROJECT_NAME" "$VERSION")"

log "main branch: ${MAIN_BRANCH}  rc branch: ${RC_BRANCH}  release tag: ${RELEASE_TAG}"

harness_init_scratch_repo "$REPO_PATH" "$MAIN_BRANCH"

# ---------------------------------------------------------------------------
# Step 3 — deposit the intake doc via the real intake path (demo Run step)
# ---------------------------------------------------------------------------
DEPOSITED_INTAKE_OUTPUT="$(
  run_isolated \
    PGAI_AGENT_KANBAN_ROOT_PATH="$ROOT" \
    PGAI_PROJECT_NAME="$PROJECT_NAME" \
    -- \
    bash "${TEAM_DIR}/scripts/intake.sh" --project "$PROJECT_NAME" --file "$INTAKE_FILE"
)" || fail_setup "intake.sh failed to deposit ${INTAKE_FILE}"

DEPOSITED_INTAKE_PATH="$(printf '%s\n' "$DEPOSITED_INTAKE_OUTPUT" | sed -n 's/^intake: deposited //p' | tail -n1)"
[[ -f "$DEPOSITED_INTAKE_PATH" ]] || fail_setup "intake.sh did not report a deposited file path (got: ${DEPOSITED_INTAKE_OUTPUT})"
log "deposited intake: ${DEPOSITED_INTAKE_PATH}"

# ===========================================================================
# STAGE: decompose
# ===========================================================================
TASKS_DIR="${PROJECT_ROOT}/tasks"
QUEUES_DIR="${TASKS_DIR}/queues"
PLAN_JSON="${ROOT}/plan.json"

python3 - "$PLAN_JSON" "$PROJECT_NAME" "$VERSION" "$REPO_PATH" "$DEPOSITED_INTAKE_PATH" <<'PY'
import json
import sys

plan_path, project_name, version, repo_path, requirements_path = sys.argv[1:6]

plan = {
    "project_name": project_name,
    "workflow_type": "release",
    "test_required": "true",
    "target_version": version,
    "human_approval_required": "auto",
    "requirements_path": requirements_path,
    "tasks": [
        {
            "sequence": 1,
            "slug": "hello-world-script",
            "title": "Implement hello.py and test_hello.py",
            "role": "CODER",
            "working_directory": repo_path,
            "git_repo": "none",
            "source_branch": "none",
            "goal": (
                "Ship hello.py (prints exactly 'Hello, World!') and "
                "test_hello.py (asserts stdout equality) per the intake doc."
            ),
            "inputs": [requirements_path],
            "context_paths": [],
            "required_output": "hello.py and test_hello.py in the repo root.",
            "constraints": [
                "No argparse, logging, config files, or dependencies beyond "
                "the standard library.",
            ],
            "acceptance_criteria": [
                "python3 hello.py outputs exactly 'Hello, World!'",
                "python3 -m pytest test_hello.py -v exits 0",
                "test asserts stdout equality to 'Hello, World!\\n', not a substring",
                "python3 -m py_compile hello.py compiles clean",
            ],
            "depends_on": [],
            "notes": "Deterministic content fixed by the intake doc; see harness header.",
        }
    ],
}

with open(plan_path, "w", encoding="utf-8") as fh:
    json.dump(plan, fh, indent=2)
PY

set +e
MATERIALIZE_OUTPUT="$(
  run_isolated \
    PGAI_AGENT_KANBAN_ROOT_PATH="$ROOT" \
    PGAI_PROJECT_ROOT="$PROJECT_ROOT" \
    PGAI_PROJECT_NAME="$PROJECT_NAME" \
    PYTHONPATH="${TEAM_DIR}/pm-agent:${TEAM_DIR}" \
    -- \
    python3 "${TEAM_DIR}/pm-agent/pm_materialize.py" \
      --team-root "$PROJECT_ROOT" \
      --requirements-path "$DEPOSITED_INTAKE_PATH" \
      "$PLAN_JSON" 2>&1
)"
MATERIALIZE_EXIT=$?
set -e
printf '%s\n' "$MATERIALIZE_OUTPUT" >&2

if [[ "$MATERIALIZE_EXIT" -ne 0 ]]; then
  STAGE_DECOMPOSE="FAIL (pm_materialize.py exited ${MATERIALIZE_EXIT})"
  fail_stage decompose "pm_materialize.py failed"
fi

CM_OPEN_DIR="$(find "$TASKS_DIR" -maxdepth 1 -type d -name 'CM-*-open-rc-*' | head -n1)"
CM_RELEASE_DIR="$(find "$TASKS_DIR" -maxdepth 1 -type d -name 'CM-*-release-*' | head -n1)"
CODER_DIR="$(find "$TASKS_DIR" -maxdepth 1 -type d -name 'CODER-*' | head -n1)"
TESTER_DIR="$(find "$TASKS_DIR" -maxdepth 1 -type d -name 'TESTER-*' | head -n1)"

if [[ -z "$CM_OPEN_DIR" || -z "$CM_RELEASE_DIR" || -z "$CODER_DIR" || -z "$TESTER_DIR" ]]; then
  STAGE_DECOMPOSE="FAIL (missing materialized tickets)"
  fail_stage decompose "expected CM-open-rc, CODER, TESTER, and CM-release tickets under ${TASKS_DIR}; found CM_OPEN='${CM_OPEN_DIR}' CODER='${CODER_DIR}' TESTER='${TESTER_DIR}' CM_RELEASE='${CM_RELEASE_DIR}'"
fi

CM_OPEN_ID="$(basename "$CM_OPEN_DIR")"
CM_RELEASE_ID="$(basename "$CM_RELEASE_DIR")"
CODER_ID="$(basename "$CODER_DIR")"
TESTER_ID="$(basename "$TESTER_DIR")"

STAGE_DECOMPOSE="PASS (4 tickets materialized: ${CM_OPEN_ID}, ${CODER_ID}, ${TESTER_ID}, ${CM_RELEASE_ID})"
log "decompose: ${STAGE_DECOMPOSE}"

# ---------------------------------------------------------------------------
# Small helper: mark a materialized ticket DONE with a one-line summary and
# flip its queue marker to [x]. Reuses the real status-file primitives from
# wake_task_state.sh (set_state / set_block / mark_backlog) rather than
# hand-rolling status.md regex here.
# ---------------------------------------------------------------------------
complete_ticket() {
  local task_dir="$1" queue_file="$2" task_id="$3" summary="$4"
  ensure_status_file "${task_dir}/status.md"
  set_state "${task_dir}/status.md" "DONE"
  set_block "${task_dir}/status.md" "Summary" "$summary"
  set_block "${task_dir}/status.md" "Needs Human" "no"
  BACKLOG="$queue_file" mark_backlog "$task_id" "x"
}

# ===========================================================================
# CM-open-rc leg — real cm/open-rc.sh
# ===========================================================================
set +e
OPEN_RC_OUTPUT="$(
  run_isolated \
    PGAI_AGENT_KANBAN_ROOT_PATH="$ROOT" \
    KANBAN_ROOT="$ROOT" \
    TEAM_ROOT="$ROOT" \
    PGAI_PROJECT_NAME="$PROJECT_NAME" \
    REPO_ROOT="$REPO_PATH" \
    -- \
    bash "${TEAM_DIR}/scripts/cm/open-rc.sh" --project "$PROJECT_NAME" "$VERSION" "$CM_OPEN_ID" 2>&1
)"
OPEN_RC_EXIT=$?
set -e
printf '%s\n' "$OPEN_RC_OUTPUT" >&2
if [[ "$OPEN_RC_EXIT" -ne 0 ]]; then
  STAGE_SHIP="FAIL (cm/open-rc.sh exited ${OPEN_RC_EXIT})"
  fail_stage ship "cm/open-rc.sh failed for ${CM_OPEN_ID}"
fi
complete_ticket "$CM_OPEN_DIR" "${QUEUES_DIR}/cm_backlog.md" "$CM_OPEN_ID" \
  "Harness executed cm/open-rc.sh ${VERSION}; RC branch ${RC_BRANCH} opened."

# ===========================================================================
# CODER leg — deterministic content fixed by the intake doc (see header)
# ===========================================================================
FEATURE_BRANCH="feature/${CODER_ID}"

git -C "$REPO_PATH" checkout "$RC_BRANCH"
git -C "$REPO_PATH" checkout -b "$FEATURE_BRANCH"

cat > "${REPO_PATH}/hello.py" <<'PY'
def main():
    print("Hello, World!")


if __name__ == "__main__":
    main()
PY

cat > "${REPO_PATH}/test_hello.py" <<'PY'
import io
import sys

from hello import main


def test_hello_world_output():
    captured = io.StringIO()
    old_stdout = sys.stdout
    sys.stdout = captured
    try:
        main()
    finally:
        sys.stdout = old_stdout
    assert captured.getvalue() == "Hello, World!\n"
PY

git -C "$REPO_PATH" add hello.py test_hello.py
git -C "$REPO_PATH" commit -m "feat: add hello.py and test_hello.py" >/dev/null
git -C "$REPO_PATH" checkout "$RC_BRANCH"
MERGE_OUTPUT="$(git -C "$REPO_PATH" merge --no-ff "$FEATURE_BRANCH" -m "Merge ${FEATURE_BRANCH} into ${RC_BRANCH}" 2>&1)" \
  || fail_stage ship "merge of ${FEATURE_BRANCH} into ${RC_BRANCH} failed: ${MERGE_OUTPUT}"
MERGE_SHA="$(git -C "$REPO_PATH" rev-parse --short HEAD)"
git -C "$REPO_PATH" branch -d "$FEATURE_BRANCH" >/dev/null

complete_ticket "$CODER_DIR" "${QUEUES_DIR}/coder_backlog.md" "$CODER_ID" \
  "Harness wrote hello.py and test_hello.py; merged --no-ff into ${RC_BRANCH} (commit ${MERGE_SHA}), feature branch deleted."
log "coder: merged ${FEATURE_BRANCH} into ${RC_BRANCH} (${MERGE_SHA})"

# ===========================================================================
# STAGE: verify (TESTER leg) — the intake doc's own acceptance commands
# ===========================================================================
TESTER_ARTIFACT_DIR="${TESTER_DIR}/artifacts"
mkdir -p "$TESTER_ARTIFACT_DIR"

VERIFY_LOG="${ROOT}/verify.log"
: > "$VERIFY_LOG"

VERIFY_OK=1

HELLO_OUTPUT="$(cd "$REPO_PATH" && python3 hello.py 2>>"$VERIFY_LOG")" || VERIFY_OK=0
if [[ "$HELLO_OUTPUT" != "Hello, World!" ]]; then
  VERIFY_OK=0
  echo "python3 hello.py printed '${HELLO_OUTPUT}', expected exactly 'Hello, World!'" >> "$VERIFY_LOG"
fi

# -u PYTEST_ADDOPTS / -u PYTEST_DEBUG_TEMPROOT: when this harness itself runs
# from inside the suite's own pytest session (team/tests/integration/
# test_helloworld_flow_harness.py), it inherits run-integration-tests.sh's
# PYTEST_ADDOPTS (a --basetemp pinned under the OUTER suite's temp root).
# Without stripping it here, this INNER pytest run over the demo repo's own
# test_hello.py reuses that same basetemp path, which sits at or above
# $REPO_PATH (both are under the same throwaway root) — pytest refuses a
# --basetemp that is the current working directory or a parent of it, so the
# verify stage would fail every time this harness runs under the suite
# rather than standalone. The demo's test_hello.py has its own scratch needs
# fully satisfied by pytest's own defaults; it does not need the outer
# suite's basetemp policy.
if ! (cd "$REPO_PATH" && env -u PYTEST_ADDOPTS -u PYTEST_DEBUG_TEMPROOT python3 -m pytest test_hello.py -v) >> "$VERIFY_LOG" 2>&1; then
  VERIFY_OK=0
fi

if ! (cd "$REPO_PATH" && python3 -m py_compile hello.py) >> "$VERIFY_LOG" 2>&1; then
  VERIFY_OK=0
fi

if [[ "$VERIFY_OK" -ne 1 ]]; then
  STAGE_VERIFY="FAIL (see ${VERIFY_LOG})"
  cat "$VERIFY_LOG" >&2
  fail_stage verify "acceptance-criteria commands failed; log at ${VERIFY_LOG}"
fi

STAGE_VERIFY="PASS (python3 hello.py, pytest test_hello.py, py_compile all green)"
log "verify: ${STAGE_VERIFY}"

# The report's head -- title and recommendation -- is the committed fixture
# shared with wake/harness.sh, so report-format text stays out of this
# script and the TESTER report parser alone reads it; this run's own
# sections follow it.
cat "${SCRIPT_DIR}/fixtures/synthetic-tester-report-head.md" - > "${TESTER_ARTIFACT_DIR}/report.md" <<REPORT

## Summary
helloworld-flow-check.sh harness: hello.py and test_hello.py verified against
the intake doc's acceptance criteria on branch ${RC_BRANCH} in ${REPO_PATH}.

## Evidence
- python3 hello.py printed exactly "Hello, World!"
- python3 -m pytest test_hello.py -v exited 0
- python3 -m py_compile hello.py compiled clean

## Findings
none
REPORT

complete_ticket "$TESTER_DIR" "${QUEUES_DIR}/tester_backlog.md" "$TESTER_ID" \
  "Harness ran the intake doc's acceptance commands against ${RC_BRANCH}; all green. See artifacts/report.md."

# ===========================================================================
# STAGE: ship (CM-release leg) — real cm/release.sh
# ===========================================================================
set +e
RELEASE_OUTPUT="$(
  run_isolated \
    PGAI_AGENT_KANBAN_ROOT_PATH="$ROOT" \
    KANBAN_ROOT="$ROOT" \
    TEAM_ROOT="$ROOT" \
    PGAI_PROJECT_NAME="$PROJECT_NAME" \
    REPO_ROOT="$REPO_PATH" \
    -- \
    bash "${TEAM_DIR}/scripts/cm/release.sh" --project "$PROJECT_NAME" 2>&1
)"
RELEASE_EXIT=$?
set -e
printf '%s\n' "$RELEASE_OUTPUT" >&2
if [[ "$RELEASE_EXIT" -ne 0 ]]; then
  STAGE_SHIP="FAIL (cm/release.sh exited ${RELEASE_EXIT})"
  fail_stage ship "cm/release.sh failed for ${PROJECT_NAME}"
fi

# Captured, not piped: grep -q stops at its first match, and under pipefail
# git still writing a long tag list would turn a found tag into a miss.
if ! grep -qx "$RELEASE_TAG" <<< "$(git -C "$REPO_PATH" tag)"; then
  STAGE_SHIP="FAIL (tag ${RELEASE_TAG} not found after cm/release.sh)"
  fail_stage ship "expected tag ${RELEASE_TAG} not found in ${REPO_PATH} after cm/release.sh"
fi

if ! git -C "$REPO_PATH" show "${MAIN_BRANCH}:hello.py" >/dev/null 2>&1; then
  STAGE_SHIP="FAIL (hello.py not present on ${MAIN_BRANCH} after release)"
  fail_stage ship "hello.py missing from ${MAIN_BRANCH} tip in ${REPO_PATH} after cm/release.sh"
fi

complete_ticket "$CM_RELEASE_DIR" "${QUEUES_DIR}/cm_backlog.md" "$CM_RELEASE_ID" \
  "Harness executed cm/release.sh; tagged ${RELEASE_TAG} in ${REPO_PATH}."

STAGE_SHIP="PASS (tagged ${RELEASE_TAG} in ${REPO_PATH})"
log "ship: ${STAGE_SHIP}"
}

# ===========================================================================
# Mode dispatch — run the flow (unless reusing an already-captured --compare
# directory, see header), then report per mode.
# ===========================================================================
if [[ "$MODE" == "compare" && -d "$TARGET_DIR" ]] && _dir_has_captures "$TARGET_DIR"; then
  log "reusing existing capture in ${TARGET_DIR} (not re-running the flow)"
else
  run_flow
  if [[ "$MODE" == "capture" || "$MODE" == "compare" ]]; then
    write_capture_files "$TARGET_DIR"
  fi
fi

case "$MODE" in
  "")
    echo "[helloworld-flow-check] throwaway root: ${ROOT}"
    echo "[helloworld-flow-check] decompose: ${STAGE_DECOMPOSE}"
    echo "[helloworld-flow-check] verify: ${STAGE_VERIFY}"
    echo "[helloworld-flow-check] ship: ${STAGE_SHIP}"
    exit 0
    ;;
  capture)
    echo "[helloworld-flow-check] capture complete: ${TARGET_DIR}"
    exit 0
    ;;
  compare)
    compare_against_golden "$TARGET_DIR"
    ;;
esac
