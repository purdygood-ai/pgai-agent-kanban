#!/usr/bin/env bash
# team/scripts/harness/strangers-path-check.sh
#
# The stranger's-path run-through: executes the documented container path
# end to end — bring up a fresh container from the shipped image, perform
# the documented install, bring the board up, and ship the helloworld demo
# — taking every command it runs from the shipped documents rather than
# from a transcribed copy, and reporting a per-stage pass/fail naming the
# document and line each executed step came from.
#
# ===========================================================================
# USAGE
# ===========================================================================
#   strangers-path-check.sh [--help]
#   PGAI_CONTAINER_TIER=1 strangers-path-check.sh --image <name|all> [--doc-root <dir>]
#   strangers-path-check.sh --operator-approved --image <name|all> [--doc-root <dir>]
#
#   Operator-only: this is the container tier. Without PGAI_CONTAINER_TIER=1
#   in the invoking shell or --operator-approved, or from inside an agent
#   wake, it prints one SKIPPED line and exits 0 before any stage runs --
#   see team/scripts/lib/container_tier.sh.
#
#   --image NAME      The image flavor(s) the container stage builds and
#                      launches, from team/config/container-images.txt:
#                      repeatable, or --image all. The documented build
#                      command (docs/docker.md Step 3, written for debian)
#                      is run with its docker/debian/ path and
#                      pgai-agent-kanban-debian tag swapped for each
#                      selected flavor. Default: kanban.cfg [release]
#                      container_images. With neither, the available images
#                      are listed and no stage runs (exit 0).
#   --operator-approved
#                      Operator authorization, same as PGAI_CONTAINER_TIER=1.
#   --doc-root <dir>  Read the documented step text from <dir> instead of
#                      this checkout's own docs/ and HOW_TO.md-adjacent
#                      tree. Only the DOCUMENT TEXT moves — every command
#                      this fixture actually RUNS (install.sh, docker/,
#                      team/scripts/...) still resolves from this
#                      checkout, self-located from this script's own path,
#                      exactly the "each tree finds itself" rule the rest
#                      of this framework follows. This is how a caller
#                      seeds a broken documented step without touching a
#                      real checkout: copy docs/docker.md (and any other
#                      file under test) into a scratch directory that
#                      mirrors the checkout's relative doc paths, edit the
#                      one command under test, and pass that scratch
#                      directory here. Default: this checkout's own root.
#   --help, -h         Print this message and exit 0.
#
# ===========================================================================
# THE FOUR STAGES
# ===========================================================================
#   container    Confirm a container engine answers, build the shipped
#                image, and launch a real container from it — mounted
#                against the root the install stage just populated —
#                proving the entrypoint's mount contract holds and a
#                login shell comes up inside it.
#   install      Populate a throwaway kanban root with the documented
#                container-path install command.
#   board        Start the scheduler and bring the tmux dashboard up
#                against that root, using the documented commands.
#   helloworld   Run team/scripts/harness/helloworld-flow-check.sh
#                end to end and quote ITS OWN per-stage outcome-file
#                evidence — this fixture does not re-implement that
#                evidence, it reuses it.
#
# Reported in that fixed order regardless of internal execution order.
# Execution runs install before container's own container-launch check,
# because the entrypoint's mount contract (docker/entrypoint.sh) requires
# an already-populated kanban root before a container can usefully start
# against it — see "EXECUTION ORDER VS REPORT ORDER" below.
#
# ===========================================================================
# VERDICT VOCABULARY (team/SOP-core.md, "Unusable build blocks; an
# unavailable build environment does not")
# ===========================================================================
#   PASS               The stage's documented commands ran and succeeded.
#   FAIL                A documented command ran and did not behave as
#                       documented — a finding about the TREE. Names the
#                       document and line the failing command came from.
#   COULD-NOT-ATTEMPT   The stage's environment precondition was not met
#                       (no container engine on PATH, no tmux on PATH) —
#                       nothing about the tree was exercised, so this is
#                       never treated as a tree failure. Names the missing
#                       resource.
#
# A COULD-NOT-ATTEMPT verdict never turns the run's exit code non-zero by
# itself. A FAIL does.
#
# ===========================================================================
# EXIT CODES
# ===========================================================================
#   0   every stage PASSed or reported COULD-NOT-ATTEMPT
#   1   at least one stage FAILed (a documented step did not behave as
#       documented)
#   2   invocation error (unknown flag, missing --doc-root value)
#   3   refused: an image name team/config/container-images.txt does not
#       declare (no stage ran)
#   A SKIPPED run (not authorized) and a run with no image selected exit 0
#   with no stage run.
#
# ===========================================================================
# EXECUTION ORDER VS REPORT ORDER
# ===========================================================================
# The four stages are REPORTED in the fixed order the ticket names them:
# container, install, board, helloworld. They do not RUN in that order.
# The container stage's own launch-check needs an already-populated
# kanban root (the entrypoint refuses a raw or missing root by design —
# docker/entrypoint.sh condition 3), so install must produce that root
# before the container stage's launch-check can mean anything. Internal
# execution is: install -> container (engine probe + image build + launch
# check against install's root) -> board (against the same root) ->
# helloworld (its own fully independent throwaway root). Every stage still
# reports its own verdict and provenance independently; only the ORDER
# they run in, not what each one proves, differs from the name list above.
#
# ===========================================================================
# DELIBERATE, DOCUMENTED DEVIATIONS FROM A LITERAL COMMAND
# ===========================================================================
# A fixture with no interactive terminal and no license to touch the
# operator's real $HOME/pgai_agent_kanban cannot run every documented
# command byte-for-byte. Each deviation below is narrow, is the same
# class of justified deviation team/scripts/harness/helloworld-flow-check.sh
# already documents for why it does not invoke a live model, and is
# called out at its call site with a comment naming the doc line it
# stands in for:
#
#   - Step 4 ("export PGAI_AGENT_KANBAN_ROOT_PATH=...") sets a THROWAWAY
#     path rather than the doc's own $HOME/pgai_agent_kanban literal —
#     using the literal would populate the operator's real install.
#   - Step 6 ("team/scripts/container-shell.sh") launches with `-it`,
#     which requires a real terminal this fixture does not have
#     ("cannot attach stdin to a TTY-enabled container because stdin is
#     not a terminal" is docker's own refusal for this exact shape). The
#     container stage instead runs the same engine, the same image tag,
#     and the same two mounts container-shell.sh documents, through
#     `docker run --rm` with piped stdin instead of `-it` — every mount
#     contract condition docker/entrypoint.sh checks still runs for real.
#   - Step 8 ("tail -f ...") would block forever in a non-interactive
#     harness. The board stage reads the same log file's contents
#     instead of tailing it, and checks for the same two banner lines
#     the doc says to look for.
#   - Step 9 ("scripts/dashboard/create.sh") ends in `exec tmux
#     attach-session`, which fails loud with no terminal attached after
#     the session is already fully built. The board stage treats that
#     specific, expected failure as non-fatal and confirms success by
#     checking the session exists instead.
#   - A throwaway kanban root always resolves under a temp directory
#     (this framework's own disposability rule — see "Temporary File
#     Hygiene" in team/roles/CODER.md), which trips install.sh's own
#     temp-target guard and leaves pseudocron.cfg/.env as `.example`
#     files only — install.sh's OWN printed remedy for that case is to
#     run team/scripts/install-pseudocron.sh directly, which is what the
#     board stage does before Step 7. This is install.sh's documented
#     CI-safety behavior working as designed, not a bug this fixture
#     works around silently — see board_stage()'s comment for the cited
#     remedy line.
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
# shellcheck source=../lib/error_contract.sh
source "${TEAM_DIR}/scripts/lib/error_contract.sh"

log() {
  echo "[strangers-path-check] $*" >&2
}

_EXTRACT_PY="${SCRIPT_DIR}/lib/doc_step_extract.py"

# shellcheck source=../lib/container_tier.sh
source "${TEAM_DIR}/scripts/lib/container_tier.sh"

DOC_ROOT="${REPO_ROOT}"
_SPC_ORIG_ARGS=("$@")
container_tier_take_args "$@"
set -- ${CONTAINER_TIER_REST_ARGS[@]+"${CONTAINER_TIER_REST_ARGS[@]}"}

_USAGE_TEXT="$(grep '^#' "${BASH_SOURCE[0]}" | sed 's/^# \{0,1\}//' | sed -n '2,157p')"

while [[ $# -gt 0 ]]; do
  case "$1" in
    --help|-h)
      print_usage_and_exit 0 "$_USAGE_TEXT"
      ;;
    --doc-root)
      # Operator-CLI argument dispatch: out of the census's loop-bearing
      # scope, same reasoning team/scripts/run-cold-build-tests.sh's own
      # unrecognized-argument site documents — no autonomous chain agent
      # invokes this harness with malformed arguments, so no CENSUS-ANCHOR
      # here (see team/SOP-core.md, "Gates and Refusals").
      if [[ $# -lt 2 ]]; then
        echo "strangers-path-check.sh: --doc-root requires a directory argument." >&2
        exit 2
      fi
      DOC_ROOT="$2"
      shift 2
      ;;
    *)
      # Operator-CLI argument dispatch: out of census scope, same reasoning
      # as the --doc-root case immediately above.
      echo "strangers-path-check.sh: unknown argument: $1" >&2
      print_usage_and_exit 2 "$_USAGE_TEXT"
      ;;
  esac
done

if [[ ! -d "$DOC_ROOT" ]]; then
  # Operator-CLI argument dispatch: out of census scope, same reasoning
  # as the arg-parsing cases above.
  echo "strangers-path-check.sh: --doc-root '${DOC_ROOT}' is not a directory." >&2
  exit 2
fi

# --- Operator-only gate: skip (exit 0) before any stage touches an engine
# or root; then the image selection, which lists-and-exits with nothing
# selected and refuses an undeclared name, also before any stage ---
container_tier_require_operator "${BASH_SOURCE[0]}" ${_SPC_ORIG_ARGS[@]+"${_SPC_ORIG_ARGS[@]}"}
container_tier_select_images "${BASH_SOURCE[0]}" ${_SPC_ORIG_ARGS[@]+"${_SPC_ORIG_ARGS[@]}"}
_SPC_RERUN_COMMAND="$(container_tier_operator_command "${BASH_SOURCE[0]}" ${_SPC_ORIG_ARGS[@]+"${_SPC_ORIG_ARGS[@]}"})"
unset _SPC_ORIG_ARGS
docker_use_session_begin "strangers-path-check.sh" "$(IFS=,; echo "${CONTAINER_TIER_IMAGES[*]}")"

# ---------------------------------------------------------------------------
# extract_step <file-relative-to-DOC_ROOT> <heading-substring> [block-index]
#
# Sets STEP_FILE, STEP_HEADING_LINE, STEP_CMD_TEXTS (array), STEP_CMD_LINES
# (array, same indices). Returns 1 (with STEP_* left from the PREVIOUS call)
# when the heading or block cannot be found — callers turn that into a FAIL
# naming the extractor's own stderr.
# ---------------------------------------------------------------------------
STEP_FILE=""
STEP_HEADING_LINE=""
STEP_CMD_TEXTS=()
STEP_CMD_LINES=()
STEP_EXTRACT_ERROR=""

extract_step() {
  local rel_file="$1" heading="$2" block_index="${3:-0}"
  local abs_file="${DOC_ROOT}/${rel_file}"
  STEP_FILE="$rel_file"
  STEP_CMD_TEXTS=()
  STEP_CMD_LINES=()
  STEP_EXTRACT_ERROR=""

  local tsv_out
  if ! tsv_out="$(python3 "$_EXTRACT_PY" "$abs_file" "$heading" --block-index "$block_index" --tsv 2>&1)"; then
    STEP_EXTRACT_ERROR="$tsv_out"
    return 1
  fi

  local field a b c
  while IFS=$'\t' read -r field a b; do
    case "$field" in
      HEADING_LINE) STEP_HEADING_LINE="$a" ;;
      CMD)
        STEP_CMD_LINES+=("$a")
        STEP_CMD_TEXTS+=("$b")
        ;;
    esac
  done <<<"$tsv_out"
  return 0
}

# ---------------------------------------------------------------------------
# run_isolated <NAME=value>... -- <command> [args...]
#
# Runs a subprocess with a clean environment (env -i) plus only the
# NAME=value pairs the caller supplies (and PATH/HOME so python3/git/bash/
# docker/tmux still resolve). Mirrors helloworld-flow-check.sh's own
# run_isolated: this fixture's invoking wake session may export
# PGAI_AGENT_KANBAN_ROOT_PATH and friends pointed at the LIVE kanban-self
# install, and install.sh / install-pseudocron.sh resolve some of that
# ambient state before this script's own explicit assignment would
# otherwise win. Duplicated here (rather than factored into a shared lib
# helloworld-flow-check.sh also sources) to avoid an edit to that
# already-shipped, independently-tested harness for an eleven-line helper.
# ---------------------------------------------------------------------------
run_isolated() {
  local -a assigns=()
  while [[ $# -gt 0 && "$1" != "--" ]]; do
    assigns+=("$1")
    shift
  done
  shift || true
  env -i PATH="$PATH" HOME="$HOME" "${assigns[@]}" "$@"
}

# ---------------------------------------------------------------------------
# probe_container_engine
# Echoes "docker" or "podman" — whichever answers `<engine> version` — on
# stdout and returns 0. Returns 1 (nothing on stdout) when neither engine
# is on PATH or neither answers. This is a REACHABILITY PROBE (team/SOP-core.md
# "the boundary between 2 and 3 is evidence, not inference"): its exit
# status is what distinguishes a could-not-attempt verdict from a real
# build failure below.
# ---------------------------------------------------------------------------
probe_container_engine() {
  local candidate
  for candidate in docker podman; do
    if command -v "$candidate" >/dev/null 2>&1 && pgai_docker "$candidate" version >/dev/null 2>&1; then
      printf '%s\n' "$candidate"
      return 0
    fi
  done
  return 1
}

# ---------------------------------------------------------------------------
# run_doc_block <cwd> <logfile> <cmd1> [<cmd2> ...]
#
# Runs every command in ONE shell invocation (joined by newlines), the same
# way a human pastes a multi-line fenced block into one terminal — a
# multi-line documented block frequently depends on an earlier line's
# `export`/`set -a` surviving into a later line (docs/docker.md's Build
# section is exactly this shape: `set -a && . docker/cli-versions.env &&
# set +a` on one line, then `docker build ... --build-arg
# CLAUDE_CODE_VERSION="$CLAUDE_CODE_VERSION" ...` on the next). Running each
# line as its own subprocess would silently lose that state and misreport
# a documentation defect that is not really there.
#
# Sets RUN_DOC_BLOCK_EXIT and echoes nothing; caller reads the log file at
# <logfile> for output. Returns the block's own exit status.
# ---------------------------------------------------------------------------
run_doc_block() {
  local cwd="$1" logfile="$2"
  shift 2
  local script
  script="$(printf '%s\n' "$@")"
  set +e
  ( cd "$cwd" && bash -c "$script" ) > "$logfile" 2>&1
  RUN_DOC_BLOCK_EXIT=$?
  set -e
  return "$RUN_DOC_BLOCK_EXIT"
}

# ---------------------------------------------------------------------------
# with_isolated_tmux -- <command> [args...]
#
# Runs <command> with PATH prefixed by a throwaway shim directory whose
# `tmux` execs the real tmux binary against a private, per-run socket
# (`tmux -L <unique-name>`). This is how the board stage never touches
# a real operator dashboard session that happens to be running on the
# same host under the default socket, and never collides with a fixed
# session name (pgai-kanban-dashboard, pseudocron) a prior fixture run
# left behind. Requires TMUX_SHIM_DIR and TMUX_SOCKET_NAME to already be
# set (board_stage() does this once for its whole run).
# ---------------------------------------------------------------------------
with_isolated_tmux() {
  PATH="${TMUX_SHIM_DIR}:${PATH}" "$@"
}

# ---------------------------------------------------------------------------
# Stage bookkeeping: printed at the end, in the fixed report order
# (container, install, board, helloworld) regardless of execution order.
# ---------------------------------------------------------------------------
declare -A STAGE_VERDICT
declare -A STAGE_DETAIL
STAGE_VERDICT[container]="not-attempted"
STAGE_VERDICT[install]="not-attempted"
STAGE_VERDICT[board]="not-attempted"
STAGE_VERDICT[helloworld]="not-attempted"

STAGE_ORDER=(container install board helloworld)

RUN_ROOT="$(harness_new_throwaway_root "strangers-path-check")"
INSTALL_ROOT="${RUN_ROOT}/install"
mkdir -p "$INSTALL_ROOT"
CLAUDE_CONFIG_DIR="${RUN_ROOT}/claude-config"

log "run root: ${RUN_ROOT}"
log "install root (throwaway): ${INSTALL_ROOT}"

# ===========================================================================
# STAGE: install
# ===========================================================================
install_stage() {
  # Step 4 (docs/docker.md) documents:
  #   export PGAI_AGENT_KANBAN_ROOT_PATH="$HOME/pgai_agent_kanban"
  # This fixture sets the SAME variable to a throwaway path instead of that
  # literal — see the header's "DELIBERATE, DOCUMENTED DEVIATIONS" section.
  if ! extract_step "docs/docker.md" "Step 4 — Choose the kanban root and export it"; then
    STAGE_VERDICT[install]="FAIL"
    STAGE_DETAIL[install]="could not extract Step 4 from docs/docker.md: ${STEP_EXTRACT_ERROR}"
    return
  fi
  local step4_line="${STEP_CMD_LINES[0]:-${STEP_HEADING_LINE}}"

  if ! extract_step "docs/docker.md" "Step 5 — Populate the kanban root"; then
    STAGE_VERDICT[install]="FAIL"
    STAGE_DETAIL[install]="could not extract Step 5 from docs/docker.md: ${STEP_EXTRACT_ERROR}"
    return
  fi
  local install_cmd="${STEP_CMD_TEXTS[0]:-}"
  local install_line="${STEP_CMD_LINES[0]:-}"

  if [[ -z "$install_cmd" ]]; then
    STAGE_VERDICT[install]="FAIL"
    STAGE_DETAIL[install]="docs/docker.md:${STEP_HEADING_LINE} — Step 5's fenced block has no command"
    return
  fi

  log "install: docs/docker.md:${step4_line} sets PGAI_AGENT_KANBAN_ROOT_PATH=${INSTALL_ROOT} (throwaway, not the doc's literal path)"
  log "install: docs/docker.md:${install_line} — ${install_cmd}"

  local install_log="${RUN_ROOT}/install.log"
  set +e
  ( cd "$REPO_ROOT" && run_isolated \
      PGAI_AGENT_KANBAN_ROOT_PATH="$INSTALL_ROOT" \
      CLAUDE_CONFIG_DIR="$CLAUDE_CONFIG_DIR" \
      -- bash -c "$install_cmd" ) > "$install_log" 2>&1
  local install_exit=$?
  set -e

  if [[ "$install_exit" -ne 0 ]]; then
    STAGE_VERDICT[install]="FAIL"
    STAGE_DETAIL[install]="docs/docker.md:${install_line} — '${install_cmd}' exited ${install_exit}. Log: ${install_log}"
    return
  fi

  if ! grep -q "Installation complete" "$install_log"; then
    STAGE_VERDICT[install]="FAIL"
    STAGE_DETAIL[install]="docs/docker.md:${install_line} — '${install_cmd}' exited 0 but did not print the documented 'Installation complete!' line. Log: ${install_log}"
    return
  fi

  if [[ ! -e "${INSTALL_ROOT}/scripts/pseudocron.py" ]]; then
    STAGE_VERDICT[install]="FAIL"
    STAGE_DETAIL[install]="docs/docker.md:${install_line} — install reported complete, but scripts/pseudocron.py (the file docs/docker.md Step 5 names as the install-layout marker) is missing from ${INSTALL_ROOT}"
    return
  fi

  STAGE_VERDICT[install]="PASS"
  STAGE_DETAIL[install]="docs/docker.md:${install_line} — '${install_cmd}' exited 0; 'Installation complete!' printed; ${INSTALL_ROOT}/scripts/pseudocron.py present. Log: ${install_log}"
}

# ===========================================================================
# STAGE: container
# ===========================================================================
# container_stage runs the per-image check below once for every selected
# image and folds the results into the one container verdict the report
# carries: FAIL if any image failed, else COULD-NOT-ATTEMPT if any image
# could not be attempted, else PASS. Each image's detail is kept, prefixed
# with its name.
container_stage() {
  local image verdict="PASS" detail=""
  for image in "${CONTAINER_TIER_IMAGES[@]}"; do
    STAGE_VERDICT[container]="not-attempted"
    STAGE_DETAIL[container]=""
    _container_stage_image "$image"
    case "${STAGE_VERDICT[container]}" in
      FAIL) verdict="FAIL" ;;
      COULD-NOT-ATTEMPT) [[ "$verdict" != "FAIL" ]] && verdict="COULD-NOT-ATTEMPT" ;;
    esac
    detail="${detail}${detail:+ | }[${image}] ${STAGE_DETAIL[container]}"
  done
  STAGE_VERDICT[container]="$verdict"
  STAGE_DETAIL[container]="$detail"
}

# _container_stage_image <image>: the container check for one image flavor.
_container_stage_image() {
  local image="$1"
  local engine
  if ! engine="$(probe_container_engine)"; then
    STAGE_VERDICT[container]="COULD-NOT-ATTEMPT"
    STAGE_DETAIL[container]="no container engine answered: neither 'docker version' nor 'podman version' succeeded (checked both names on PATH). This is an environment gap, not a tree failure — team/SOP-core.md 'Unusable build blocks; an unavailable build environment does not'."
    return
  fi

  if ! extract_step "docs/docker.md" "Step 2 — Confirm a container engine answers"; then
    STAGE_VERDICT[container]="FAIL"
    STAGE_DETAIL[container]="could not extract Step 2 from docs/docker.md: ${STEP_EXTRACT_ERROR}"
    return
  fi
  local engine_check_cmd="${STEP_CMD_TEXTS[0]:-}"
  local engine_check_line="${STEP_CMD_LINES[0]:-}"
  # Doc text says "docker version" literally; substitute the resolved
  # engine name when podman answered instead (docs/docker.md's own
  # footnote: "On a podman host run `podman version` instead").
  engine_check_cmd="${engine_check_cmd/docker/$engine}"
  log "container: docs/docker.md:${engine_check_line} — ${engine_check_cmd}"
  if ! bash -c "$engine_check_cmd" >/dev/null 2>&1; then
    STAGE_VERDICT[container]="FAIL"
    STAGE_DETAIL[container]="docs/docker.md:${engine_check_line} — '${engine_check_cmd}' failed even though the probe above succeeded; non-deterministic environment flake or a genuine engine regression. Re-run to confirm."
    return
  fi

  if ! extract_step "docs/docker.md" "Step 3 — Build an image"; then
    STAGE_VERDICT[container]="FAIL"
    STAGE_DETAIL[container]="could not extract Step 3 from docs/docker.md: ${STEP_EXTRACT_ERROR}"
    return
  fi
  local build_log="${RUN_ROOT}/build-${image}.log"
  # Run as ONE shell invocation, not one subprocess per line — see
  # run_doc_block()'s header: this block's first line (`set -a && .
  # docker/cli-versions.env && set +a`) exports the two build-arg values
  # the second line's `docker build --build-arg ...` reads, and that
  # export would not survive a fresh subprocess per line.
  local -a build_lines=()
  local build_cmd_line="${STEP_CMD_LINES[-1]:-${STEP_HEADING_LINE}}"
  local i
  for i in "${!STEP_CMD_TEXTS[@]}"; do
    local cmd="${STEP_CMD_TEXTS[$i]}"
    cmd="${cmd//docker build/${engine} build}"
    # The documented block builds debian; swap in the selected flavor.
    cmd="${cmd//docker\/debian\//docker\/${image}\/}"
    cmd="${cmd//pgai-agent-kanban-debian:/pgai-agent-kanban-${image}:}"
    build_lines+=("$cmd")
    log "container: docs/docker.md:${STEP_CMD_LINES[$i]} — ${cmd}"
  done
  if ! run_doc_block "$REPO_ROOT" "$build_log" "${build_lines[@]}"; then
    STAGE_VERDICT[container]="FAIL"
    STAGE_DETAIL[container]="docs/docker.md:${build_cmd_line} — build block exited ${RUN_DOC_BLOCK_EXIT}. Log: ${build_log}"
    return
  fi
  local image_tag="pgai-agent-kanban-${image}:latest"

  # Step 6 documents `team/scripts/container-shell.sh`, which runs the
  # engine with `-it` — a real terminal this fixture does not have. See
  # the header's "DELIBERATE, DOCUMENTED DEVIATIONS" entry for Step 6.
  if ! extract_step "docs/docker.md" "Step 6 — Launch the workspace shell"; then
    STAGE_VERDICT[container]="FAIL"
    STAGE_DETAIL[container]="could not extract Step 6 from docs/docker.md: ${STEP_EXTRACT_ERROR}"
    return
  fi
  local launch_cmd="${STEP_CMD_TEXTS[0]:-}"
  local launch_line="${STEP_CMD_LINES[0]:-}"

  if [[ "${STAGE_VERDICT[install]}" != "PASS" ]]; then
    STAGE_VERDICT[container]="FAIL"
    STAGE_DETAIL[container]="docs/docker.md:${launch_line} — '${launch_cmd}' needs an installed kanban root to mount (docker/entrypoint.sh's mount contract, condition 3); the install stage did not PASS, so no root exists to launch against. See the install stage's own verdict."
    return
  fi

  log "container: docs/docker.md:${launch_line} — ${launch_cmd} (non-interactive equivalent: same engine/image/mounts, piped stdin instead of -it; see header)"
  local launch_log="${RUN_ROOT}/launch-${image}.log"
  set +e
  printf 'pwd\ntest -e scripts/pseudocron.py && echo ENTRYPOINT_MOUNT_OK\nexit 0\n' | \
    pgai_docker "$engine" run --rm -i \
      -v "${HOME}:${HOME}" \
      -v "${INSTALL_ROOT}:${INSTALL_ROOT}" \
      -e "HOME=${HOME}" \
      -e "PGAI_AGENT_KANBAN_ROOT_PATH=${INSTALL_ROOT}" \
      "$image_tag" > "$launch_log" 2>&1
  local launch_exit=$?
  set -e

  if [[ "$launch_exit" -ne 0 ]]; then
    STAGE_VERDICT[container]="FAIL"
    STAGE_DETAIL[container]="docs/docker.md:${launch_line} — non-interactive launch of '${image_tag}' exited ${launch_exit}. Log: ${launch_log}"
    return
  fi
  if ! grep -qx "$INSTALL_ROOT" "$launch_log"; then
    STAGE_VERDICT[container]="FAIL"
    STAGE_DETAIL[container]="docs/docker.md:${launch_line} — container did not land in the kanban root (expected pwd '${INSTALL_ROOT}'). Log: ${launch_log}"
    return
  fi
  if ! grep -q "ENTRYPOINT_MOUNT_OK" "$launch_log"; then
    STAGE_VERDICT[container]="FAIL"
    STAGE_DETAIL[container]="docs/docker.md:${launch_line} — container started but scripts/pseudocron.py was not visible inside it (mount did not carry the install layout through). Log: ${launch_log}"
    return
  fi

  STAGE_VERDICT[container]="PASS"
  STAGE_DETAIL[container]="docs/docker.md:${engine_check_line} engine answered (${engine}); docs/docker.md:${build_cmd_line} image '${image_tag}' built; docs/docker.md:${launch_line} container launched, mount contract held, landed in ${INSTALL_ROOT}. Logs: ${build_log}, ${launch_log}"
}

# ===========================================================================
# STAGE: board
# ===========================================================================
_board_stage_cleanup_tmux() {
  [[ -n "${TMUX_SHIM_DIR:-}" ]] && with_isolated_tmux tmux kill-server >/dev/null 2>&1
  return 0
}

board_stage() {
  if ! command -v tmux >/dev/null 2>&1; then
    STAGE_VERDICT[board]="COULD-NOT-ATTEMPT"
    STAGE_DETAIL[board]="no 'tmux' binary found on PATH. This is an environment gap, not a tree failure — team/SOP-core.md 'Unusable build blocks; an unavailable build environment does not'."
    return
  fi

  if [[ "${STAGE_VERDICT[install]}" != "PASS" ]]; then
    STAGE_VERDICT[board]="FAIL"
    STAGE_DETAIL[board]="board-up needs the install stage's kanban root; the install stage did not PASS. See the install stage's own verdict."
    return
  fi

  # A throwaway kanban root always resolves under a temp directory (this
  # framework's own disposability rule), which trips install.sh's own
  # temp-target guard (install.sh, "Hook 2: pseudocron") and leaves
  # pseudocron.cfg/.env as .example files only. install.sh's own printed
  # remedy for that case names the exact command below — this is that
  # remedy, run for real, not a silent workaround.
  local pc_log="${RUN_ROOT}/install-pseudocron.log"
  set +e
  ( cd "$REPO_ROOT" && run_isolated \
      PGAI_AGENT_KANBAN_ROOT_PATH="$INSTALL_ROOT" \
      -- bash team/scripts/install-pseudocron.sh --wake-tier=small --yes ) > "$pc_log" 2>&1
  local pc_exit=$?
  set -e
  if [[ "$pc_exit" -ne 0 ]]; then
    STAGE_VERDICT[board]="FAIL"
    STAGE_DETAIL[board]="team/scripts/install-pseudocron.sh --wake-tier=small --yes exited ${pc_exit} (this is install.sh's own documented remedy for a temp-rooted install, not a step 7/8/9 command itself). Log: ${pc_log}"
    return
  fi

  # docs/pseudocron.md "Setup" (the three variables that matter) — literal
  # values, no $VAR expansion (the parser performs none). The throwaway
  # root's own path stands in for the doc's example $HOME/pgai_agent_kanban.
  {
    echo ""
    echo "export PGAI_AGENT_KANBAN_ROOT_PATH=${INSTALL_ROOT}"
    echo "export HOME=${HOME}"
    echo "export PATH=${PATH}"
  } >> "${INSTALL_ROOT}/pseudocron.env"

  TMUX_SHIM_DIR="$(pgai_temp_subdir "harness/strangers-path-check/tmux-shim")"
  TMUX_SHIM_DIR="$(mktemp -d "${TMUX_SHIM_DIR}/run.XXXXXX")"
  local real_tmux
  real_tmux="$(command -v tmux)"
  local socket_name="pgai_strangers_path_$$"
  cat > "${TMUX_SHIM_DIR}/tmux" <<EOF
#!/usr/bin/env bash
exec "${real_tmux}" -L "${socket_name}" "\$@"
EOF
  chmod +x "${TMUX_SHIM_DIR}/tmux"
  # Isolation, not deception: the shim only changes WHICH tmux socket the
  # documented commands below talk to, so this run can never collide with
  # a real operator dashboard already running under the default socket on
  # this same host, and never trips over a fixed session name a prior
  # fixture run left behind.

  trap '_board_stage_cleanup_tmux' RETURN

  if ! extract_step "docs/docker.md" "Step 7 — Start exactly one scheduler"; then
    STAGE_VERDICT[board]="FAIL"
    STAGE_DETAIL[board]="could not extract Step 7 from docs/docker.md: ${STEP_EXTRACT_ERROR}"
    return
  fi
  local sched_cmd="${STEP_CMD_TEXTS[0]:-}"
  local sched_line="${STEP_CMD_LINES[0]:-}"
  log "board: docs/docker.md:${sched_line} — ${sched_cmd}"

  set +e
  with_isolated_tmux env PGAI_AGENT_KANBAN_ROOT_PATH="$INSTALL_ROOT" bash -c "$sched_cmd"
  local sched_exit=$?
  set -e
  if [[ "$sched_exit" -ne 0 ]]; then
    STAGE_VERDICT[board]="FAIL"
    STAGE_DETAIL[board]="docs/docker.md:${sched_line} — '${sched_cmd}' exited ${sched_exit}"
    return
  fi

  # Step 8 documents `tail -f`, which would block forever headless; this
  # reads the same log file for the same two banner lines instead.
  if ! extract_step "docs/docker.md" "Step 8 — Confirm the heartbeat"; then
    STAGE_VERDICT[board]="FAIL"
    STAGE_DETAIL[board]="could not extract Step 8 from docs/docker.md: ${STEP_EXTRACT_ERROR}"
    return
  fi
  local heartbeat_line="${STEP_CMD_LINES[0]:-${STEP_HEADING_LINE}}"
  local pseudocron_log="${INSTALL_ROOT}/logs/pseudocron.log"
  local waited=0
  while [[ "$waited" -lt 10 ]]; do
    if [[ -f "$pseudocron_log" ]] && grep -q "vars loaded" "$pseudocron_log" 2>/dev/null; then
      break
    fi
    sleep 1
    waited=$((waited + 1))
  done
  if [[ ! -f "$pseudocron_log" ]] || ! grep -q "jobs loaded" "$pseudocron_log" || ! grep -q "vars loaded" "$pseudocron_log"; then
    STAGE_VERDICT[board]="FAIL"
    STAGE_DETAIL[board]="docs/docker.md:${heartbeat_line} — pseudocron.log never printed both documented startup banner lines ('jobs loaded', 'vars loaded') within 10s. Log: ${pseudocron_log}"
    return
  fi
  if grep -q "vars loaded: 0\|vars loaded from.*: 0\| 0 vars loaded" "$pseudocron_log"; then
    STAGE_VERDICT[board]="FAIL"
    STAGE_DETAIL[board]="docs/docker.md:${heartbeat_line} — pseudocron reported 0 vars loaded; docs/docker.md names this as the fix-before-anything-else condition. Log: ${pseudocron_log}"
    return
  fi

  if ! extract_step "docs/docker.md" "Step 9 — Bring the board up"; then
    STAGE_VERDICT[board]="FAIL"
    STAGE_DETAIL[board]="could not extract Step 9 from docs/docker.md: ${STEP_EXTRACT_ERROR}"
    return
  fi
  local dash_cmd="${STEP_CMD_TEXTS[0]:-}"
  local dash_line="${STEP_CMD_LINES[0]:-}"
  log "board: docs/docker.md:${dash_line} — ${dash_cmd} (ends in 'exec tmux attach-session', which fails loud with no tty attached after the session is already built — see header)"

  local dash_log="${RUN_ROOT}/dashboard-create.log"
  set +e
  ( cd "$INSTALL_ROOT" && with_isolated_tmux env PGAI_AGENT_KANBAN_ROOT_PATH="$INSTALL_ROOT" \
      timeout 90 bash -c "$dash_cmd" < /dev/null ) > "$dash_log" 2>&1
  set -e
  # The exit code above is expected to be non-zero (the final attach
  # step's "open terminal failed: not a terminal") even on a fully
  # successful board-up; session existence is the real verdict.

  if ! with_isolated_tmux tmux has-session -t pgai-kanban-dashboard 2>/dev/null; then
    STAGE_VERDICT[board]="FAIL"
    STAGE_DETAIL[board]="docs/docker.md:${dash_line} — '${dash_cmd}' did not leave a 'pgai-kanban-dashboard' tmux session running. Log: ${dash_log}"
    return
  fi

  local window_count
  window_count="$(with_isolated_tmux tmux list-windows -t pgai-kanban-dashboard 2>/dev/null | wc -l)"

  STAGE_VERDICT[board]="PASS"
  STAGE_DETAIL[board]="docs/docker.md:${sched_line} pseudocron started (banners confirmed at docs/docker.md:${heartbeat_line}); docs/docker.md:${dash_line} dashboard session 'pgai-kanban-dashboard' up with ${window_count} windows. Logs: ${pseudocron_log}, ${dash_log}"
}

# ===========================================================================
# STAGE: helloworld
# ===========================================================================
helloworld_stage() {
  local hw_capture_dir
  hw_capture_dir="$(harness_new_throwaway_root "strangers-path-check-helloworld")"
  local hw_script="${SCRIPT_DIR}/helloworld-flow-check.sh"
  local hw_log="${RUN_ROOT}/helloworld-flow-check.log"

  log "helloworld: ${hw_script} --compare ${hw_capture_dir}"
  set +e
  "$hw_script" --compare "$hw_capture_dir" > "$hw_log" 2>&1
  local hw_exit=$?
  set -e

  if [[ "$hw_exit" -ne 0 ]]; then
    STAGE_VERDICT[helloworld]="FAIL"
    STAGE_DETAIL[helloworld]="team/scripts/harness/helloworld-flow-check.sh --compare exited ${hw_exit}. Log: ${hw_log}"
    return
  fi

  STAGE_VERDICT[helloworld]="PASS"
  STAGE_DETAIL[helloworld]="helloworld-flow-check.sh --compare ${hw_capture_dir} exited 0 (outcome files match team/tests/fixtures/helloworld-flow/ goldens). Log: ${hw_log}"
}

# ===========================================================================
# Run — install before container's launch-check (see "EXECUTION ORDER VS
# REPORT ORDER" in the header); report in the fixed name-list order after.
# ===========================================================================
install_stage
container_stage
board_stage
helloworld_stage

echo ""
echo "===================================================================="
echo "strangers-path-check: run root ${RUN_ROOT}"
echo "===================================================================="
ANY_FAIL=false
for stage in "${STAGE_ORDER[@]}"; do
  verdict="${STAGE_VERDICT[$stage]}"
  detail="${STAGE_DETAIL[$stage]:-}"
  printf '%-12s %s\n' "$stage" "$verdict"
  [[ -n "$detail" ]] && printf '             %s\n' "$detail"
  [[ "$verdict" == "FAIL" ]] && ANY_FAIL=true
done
echo "===================================================================="

if [[ "$ANY_FAIL" == "true" ]]; then
  _failed_stages=""
  for stage in "${STAGE_ORDER[@]}"; do
    [[ "${STAGE_VERDICT[$stage]}" == "FAIL" ]] && _failed_stages="${_failed_stages}${_failed_stages:+, }${stage}"
  done
  # BLOCK-WORTHY: breakage-class — a documented step that does not behave
  # as documented is an unusable stranger's path, the same doctrine
  # team/scripts/run-cold-build-tests.sh applies to a Dockerfile that
  # fails to build cold: the defect is in the tree, not the environment,
  # and this fixture is the gate that would have caught it before the
  # release shipped.
  pgai_emit_error_contract \
    "strangers_path_check.stage_failed" \
    "One or more stranger's-path stages failed: ${_failed_stages}. See the per-stage detail lines printed above for the document, line, and command that did not behave as documented." \
    "Read the FAIL detail line(s) above for the exact docs/docker.md:<line> citation and log path per failed stage; fix the tree (or the doc) at that line, then re-run: ${_SPC_RERUN_COMMAND}"
  # CENSUS-ANCHOR: strangers-path-check-stage-failed-01
  exit 1
fi
exit 0
