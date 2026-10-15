#!/usr/bin/env bash
# team/scripts/deep-verify.sh
# The DEEP-VERIFY tier's umbrella operator verb: run the expensive
# verification suites for a named project, on demand, and record what
# happened in that project's deep-verify ledger.
#
# Cold-build is the tier's only member today (team/scripts/
# run-cold-build-tests.sh); the verb is named "deep-verify", not
# "cold-build", so other expensive suites can join it later without a
# second entry point.
#
# Usage: PGAI_CONTAINER_TIER=1 deep-verify.sh --project <name> --image <name|all> [--help|-h]
#        PGAI_CONTAINER_TIER=1 deep-verify.sh --project <name> --scheduled
#        PGAI_CONTAINER_TIER=1 deep-verify.sh --scheduled
#        deep-verify.sh --operator-approved --project <name> --image all
#
# Operator-only: running the lane is the container tier. Before the lane
# starts, an invocation without operator authorization (PGAI_CONTAINER_TIER=1
# in the invoking environment, or --operator-approved) or from inside an
# agent wake prints one SKIPPED line and exits 0 -- nothing run, no ledger
# record, so the ledger keeps its last real result -- see
# team/scripts/lib/container_tier.sh. --help, project resolution and a
# --scheduled run that is not due never reach the gate and cost nothing.
# The lane inherits the authorization from this process's environment.
#
# Image selection: --image NAME (repeatable) or --image all picks which
# flavors from team/config/container-images.txt the lane builds; without
# the flag, kanban.cfg [release] container_images is the default. With
# neither, the script lists the available images and exits 0 without
# running the lane or writing a ledger record. An undeclared name refuses
# (exit 3) before anything runs. The selection is passed to the lane as
# --image flags.
#
# This script does NOT change what run-cold-build-tests.sh itself does,
# what it exits with, or the wording of its narration -- it wraps and
# records that lane, it does not re-litigate it (team/SOP-core.md
# "Gates and Refusals" -- "Unusable build blocks; an unavailable build
# environment does not").
#
# Verdict classification (pgai_agent_kanban.deep_verify.classify) maps the
# lane's exit code plus captured stdout+stderr onto exactly one of three
# SOP verdict classes -- built, failed-to-build, could-not-attempt -- and
# this script's own exit code says whether the run found anything wrong
# with the tree: 0 for built AND could-not-attempt (nothing about the tree
# is in question in either case), 1 for failed-to-build (a real tree
# defect) and for a built or could-not-attempt verdict whose lane still
# exited 1 because a non-build cold test failed (the images built, or no
# image could be built; either way a test did not pass). This mirrors
# run-cold-build-tests.sh's own exit-code contract, which already treats
# "every pair narrate-skipped" and "every pair built" as the same
# non-failure exit (0).
#
# Failure-path bug filing (pgai_agent_kanban.deep_verify.bug_filer, via
# lib/deep_verify_bug_filer.sh): a failed-to-build verdict files a bug into
# the project's bugs/ directory exactly like any other TESTER finding, or
# cites an existing open/running bug already covering the same failing
# (engine, flavor) pair set instead of filing a duplicate. A built verdict
# from a lane that exited non-zero (every image built, a non-build cold
# test failed) files -- or cites -- a bug naming the failing test's file
# and test id, keyed on the failing-test set. A could-not-attempt verdict
# from a lane that exited 1 (no image could be built, and a non-build cold
# test failed) files or cites that same test-failure bug: the unavailable
# build environment is not a finding, but the failing test is. Every other
# could-not-attempt verdict (SOP outcome 3 -- the verifying host, not the
# tree, is at fault: lane exit 0 with every pair narrate-skipped, lane
# exit 2 because pytest is not installed, or an exit code the classifier
# has no mapping for) files NO bug; only the ledger records it. Filing
# touches the
# project's bugs/ directory and the ledger ONLY -- never release-state.md,
# a task status.md, a queue file, or a HALT-family signal file, and it never
# creates or deletes a branch, so a run firing while an RC happens to be
# open leaves that RC untouched; the discovery pipeline's own Active-RC gate
# (team/scripts/lib/discovery.sh) is what defers bundling the filed bug
# until the RC closes, not anything this script does.
#
# --scheduled: the weekly, off-hours dispatch mode a cron/pseudocron entry
# invokes. Neither scheduler can express "once a week, off-hours" on its
# own for this per-project cadence -- cron's
# day-of-week field can, but pseudocron's job table matches minute-of-hour
# alone and would otherwise fire this check every hour, every day (see
# docs/pseudocron.md) -- so the due-check lives here instead, reading the
# project's deep-verify ledger and its [release] deep_verify /
# deep_verify_offhours_window config, each resolved from project.cfg, then
# kanban.cfg, then the built-in default (weekly; 03:00-03:59)
# (pgai_agent_kanban.deep_verify.schedule.evaluate_due is the pure decision
# function). A scheduled invocation that is not due (deep_verify=off,
# outside the off-hours window, or the last run is under a week old)
# invokes NO lane, starts NO container engine, writes NO ledger record,
# and exits 0 after printing exactly one narration line naming the
# project and when it next comes due. An unrecognized deep_verify value
# refuses dual-channel (CENSUS-ANCHOR deep-verify-02) and runs nothing --
# never a guessed mode.
#
# With --scheduled and --project both given, only that project is
# evaluated. With --scheduled and NO --project, this script fans out over
# every active (non-halted) project in projects.cfg, re-invoking itself
# once per project -- this is what lets a single cron/pseudocron entry
# cover every registered project without a second entry point.
#
# Exit codes:
#   0  = the run completed and the ledger records verdict=built or
#        verdict=could-not-attempt, with no failing non-build cold test
#        (that case is exit 1, below); OR (--scheduled) the scheduled
#        invocation was not due, or deep_verify=off, and nothing ran
#   1  = the run completed and the ledger records verdict=failed-to-build,
#        OR the ledger records verdict=built but the lane exited 1 because
#        a non-build cold test failed (a bug naming the test is filed or
#        cited), OR the ledger records verdict=could-not-attempt but the
#        lane exited 1 because a non-build cold test failed while no image
#        could be built (a bug naming the test is filed or cited), OR the
#        project name was not recognized (no record written),
#        OR (--scheduled) the project's deep_verify value is unrecognized,
#        OR (--scheduled, fan-out) at least one project's evaluation
#        returned non-zero (other projects in the fan-out still ran)
#   0  = also: SKIPPED (not authorized, or inside an agent wake) or no
#        image selected -- no lane ran and no ledger record was written
#   3  = refused: --image (or kanban.cfg [release] container_images) names
#        an image team/config/container-images.txt does not declare; no
#        lane ran and no ledger record was written
#
# Configuration:
#   PGAI_AGENT_KANBAN_ROOT_PATH        — kanban root (default: $HOME/pgai_agent_kanban)
#   PGAI_DEEP_VERIFY_COLD_LANE_CMD     — override the cold-lane command this
#                                        script invokes (default:
#                                        <this script's dir>/run-cold-build-tests.sh).
#                                        Tests use this seam to stub the lane
#                                        instead of spawning a real container
#                                        build -- the same idiom
#                                        team/scripts/lib/safe_overwrite.sh
#                                        uses for PGAI_CRONTAB_CMD. Unset or
#                                        empty means "use the real lane."
#   PGAI_DEEP_VERIFY_NOW               — ISO-8601 UTC timestamp overriding
#                                        "now" for the --scheduled due-check
#                                        only (e.g. 2026-09-03T03:15:00+00:00).
#                                        Tests use this seam to inject the
#                                        clock instead of depending on the
#                                        wall-clock date of the test run.
#                                        Unset or empty means "use the real
#                                        current UTC time."
#   PGAI_DEV_TREE_PATH                 — the tree whose VERSION/commit this
#                                        script records into the ledger, and
#                                        the tree it hands to the cold lane
#                                        (real or stubbed) via export. When
#                                        unset, resolved the same way
#                                        run-cold-build-tests.sh resolves it
#                                        (team/scripts/lib/dev_tree.sh
#                                        resolve_global_dev_tree: this env
#                                        var, then kanban.cfg's [paths]
#                                        dev_tree_path), falling back to this
#                                        script's own repo root only when
#                                        neither source yields a real
#                                        directory.

set -euo pipefail
# --- Bootstrap: self-locate → source shell-env → fail loud ---
# shellcheck source=lib/env_bootstrap.sh
source "$(dirname "${BASH_SOURCE[0]}")/lib/env_bootstrap.sh"

# --- Locate the lib directory relative to this script ---
_DV_SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
_DV_LIB_DIR="${_DV_SCRIPT_DIR}/lib"

# --- Source the standard argument layer ---
# shellcheck source=lib/operator_args.sh
source "${_DV_LIB_DIR}/operator_args.sh"

# --- Source the project path helpers ---
# shellcheck source=lib/project_paths.sh
source "${_DV_LIB_DIR}/project_paths.sh"

# --- Source the projects.cfg registry helpers (for --project validation) ---
# shellcheck source=lib/projects.sh
source "${_DV_LIB_DIR}/projects.sh"

# --- Source the dual-channel error-contract emitter ---
# shellcheck source=lib/error_contract.sh
source "${_DV_LIB_DIR}/error_contract.sh"

# --- Source the shared Python invocation helper ---
# shellcheck source=lib/pp_run_ops.sh
source "${_DV_LIB_DIR}/pp_run_ops.sh"

# --- Source the deep-verify ledger read/write adapter ---
# shellcheck source=lib/deep_verify_ledger.sh
source "${_DV_LIB_DIR}/deep_verify_ledger.sh"

# --- Source the deep-verify failure-path bug filer ---
# shellcheck source=lib/deep_verify_bug_filer.sh
source "${_DV_LIB_DIR}/deep_verify_bug_filer.sh"

# --- Source the operator-only container-tier gate ---
# shellcheck source=lib/container_tier.sh
source "${_DV_LIB_DIR}/container_tier.sh"

# --- Source the framework temp-file helpers (captured-output scratch file) ---
# shellcheck source=lib/temp.sh
source "${_DV_LIB_DIR}/temp.sh"

# --- Declare accepted flags for this script. The container-tier flags
# (--image, --operator-approved) are owned by lib/container_tier.sh and
# separated out before the standard parse. ---
OPERATOR_VALID_FLAGS=(project scheduled help)
_DV_ORIG_ARGS=("$@")
container_tier_take_args "$@"

# --- Parse arguments ---
operator_args_parse ${CONTAINER_TIER_REST_ARGS[@]+"${CONTAINER_TIER_REST_ARGS[@]}"}

# --- Handle --help ---
if argparse_has help; then
    operator_args_render_help_for_flags "deep-verify.sh" \
        "Run the DEEP-VERIFY tier for a project on demand and record the result." \
        OPERATOR_VALID_FLAGS \
        "" \
        "Runs the cold-build lane (team/scripts/run-cold-build-tests.sh) --" \
        "the tier's only member today -- and appends one record to the" \
        "named project's deep-verify ledger" \
        "(\$KANBAN_ROOT/projects/<name>/deep-verify-ledger.md) naming the" \
        "timestamp, the tree state exercised (version and commit), the" \
        "verdict (built / failed-to-build / could-not-attempt), and the" \
        "per-pair engine/flavor detail behind it." \
        "" \
        "--scheduled: the weekly, off-hours dispatch mode a cron/pseudocron" \
        "entry invokes. Self-gates on the project's deep-verify ledger and" \
        "its [release] deep_verify (weekly|off, true|false also accepted as" \
        "aliases) / deep_verify_offhours_window config, each resolved from" \
        "project.cfg, then kanban.cfg, then the default (weekly;" \
        "03:00-03:59) -- a scheduled invocation that" \
        "is not due costs nothing (no lane, no container engine, no ledger" \
        "write) and exits 0 after one narration line. Given with --project" \
        "it evaluates just that project; given alone it fans out over every" \
        "active project in projects.cfg." \
        "" \
        "Operator-only: running the lane is the container tier. Without" \
        "PGAI_CONTAINER_TIER=1 in the invoking shell or --operator-approved," \
        "or from inside an agent wake, it prints one SKIPPED line and exits" \
        "0 with no lane run and no ledger record. Operator form, e.g.:" \
        "  PGAI_CONTAINER_TIER=1 scripts/deep-verify.sh --project pgai-agent-kanban --image all" \
        "" \
        "  --image NAME       Build image NAME from team/config/container-images.txt" \
        "                     (repeatable), or --image all for every declared image." \
        "                     Default: kanban.cfg [release] container_images. With" \
        "                     neither, the available images are listed and nothing runs." \
        "  --operator-approved  Operator authorization, same as PGAI_CONTAINER_TIER=1." \
        "" \
        "A failed-to-build verdict files a bug naming the failing" \
        "engine/flavor/step. When every image built but a non-build cold" \
        "test failed, the verdict is built and a bug naming the failing" \
        "test's file and test id is filed. When no image could be built" \
        "(every pair narrate-skipped) and a non-build cold test failed, the" \
        "verdict is could-not-attempt and the same test bug is filed. An" \
        "open or running bug already covering the same failure is cited" \
        "instead of filing a duplicate. Any other could-not-attempt run" \
        "(the lane exited 0, or exited 2 because pytest is not installed)" \
        "files no bug." \
        "" \
        "Exit codes: 0 = verdict is built or could-not-attempt and no" \
        "non-build cold test failed (nothing wrong with the tree was found)," \
        "or the run was SKIPPED or no image was selected (no record" \
        "written); 1 = verdict is failed-to-build," \
        "or verdict is built but a non-build cold test failed (a bug naming" \
        "the test is filed or cited), or verdict is could-not-attempt but a" \
        "non-build cold test failed (the lane exited 1; a bug naming the" \
        "test is filed or cited), or --project did not resolve to a" \
        "registered project (no record is written in that case); 3 =" \
        "refused, an image name the manifest does not declare (no record" \
        "written)."
    exit 0
fi

# --- Reject any flag not in the declared vocabulary ---
operator_args_validate_known "deep-verify.sh" OPERATOR_VALID_FLAGS || exit 1

# --- Resolve kanban root ---
KANBAN_ROOT="${PGAI_AGENT_KANBAN_ROOT_PATH}"
export KANBAN_ROOT

# --- Resolve --project. Required in every mode EXCEPT the --scheduled
# fan-out case (--scheduled given, --project absent), which iterates every
# active project instead of naming one. ---
_dv_project="$(operator_args_project)"
if [[ -z "${_dv_project}" ]] && ! argparse_has scheduled; then
    printf 'deep-verify.sh: --project is required (or set $PGAI_PROJECT_NAME)\n' >&2
    exit 1
fi

# --- --scheduled fan-out: no --project means "every active project" ---
# Re-invokes this same script once per project, each with an explicit
# --project, so the single-project path below (due-check, lane run,
# ledger write) is never duplicated. Never stops on one project's
# failure -- a fleet-wide side lane must not let one project's bad
# config or red build hide another project's result.
if argparse_has scheduled && [[ -z "${_dv_project}" ]]; then
    _dv_fanout_exit=0
    _dv_fanout_saw_project=0
    while IFS= read -r _dv_fanout_project; do
        [[ -z "${_dv_fanout_project}" ]] && continue
        _dv_fanout_saw_project=1
        if ! bash "${_DV_SCRIPT_DIR}/deep-verify.sh" --project "${_dv_fanout_project}" --scheduled \
            ${CONTAINER_TIER_PASSTHROUGH_ARGS[@]+"${CONTAINER_TIER_PASSTHROUGH_ARGS[@]}"}; then
            _dv_fanout_exit=1
        fi
    done < <(projects_cfg_active 2>/dev/null || true)
    if [[ "${_dv_fanout_saw_project}" -eq 0 ]]; then
        echo "deep-verify: scheduled fan-out found no active registered projects (see $(projects_cfg_path))"
    fi
    exit "${_dv_fanout_exit}"
fi

# --- Resolve --project by exact match then unambiguous prefix. ---
# pp_resolve_project_name already refuses (BLOCK-WORTHY, its own
# project_resolve.not_found dual-channel refusal) when the resolved name
# does not match a projects.cfg entry, so no separate registration check
# is needed here.
_dv_project="$(pp_resolve_project_name "${_dv_project}")" || exit 1

# --- --scheduled single-project due-check: self-gates on the ledger's
# last-run date and the off-hours window before anything costly happens.
# Runs only for the single-project path (the fan-out branch above already
# returned for the no-project case). ---
if argparse_has scheduled; then
    # NOTE: the mode read is the condition of this if-statement (not a bare
    # assignment) so that `set -e` does not abort the script on a non-zero
    # return before the dual-channel refusal below gets to run.
    #
    # stdout and stderr are captured separately (not merged with 2>&1):
    # pp_deep_verify_mode can also write an advisory misplaced-key warning
    # to stderr on the SUCCESS path, as well as its refusal diagnostic on
    # the failure path. Merging both streams into one variable would
    # corrupt _dv_mode with warning text on a success that also warns; a
    # temp file keeps the two apart so the success path relays the warning
    # untouched and the failure path still gets the full diagnostic text
    # for the error contract below.
    _dv_mode_stderr_file="$(pgai_mktemp deep_verify_mode_stderr)"
    if _dv_mode="$(pp_deep_verify_mode "${_dv_project}" 2>"${_dv_mode_stderr_file}")"; then
        if [[ -s "${_dv_mode_stderr_file}" ]]; then
            cat "${_dv_mode_stderr_file}" >&2
        fi
        rm -f "${_dv_mode_stderr_file}"
    else
        _dv_mode_output="$(cat "${_dv_mode_stderr_file}")"
        rm -f "${_dv_mode_stderr_file}"
        # The resolver's diagnostic names the file that supplied the value
        # ("... for project 'P' in FILE [release] deep_verify ..."): the
        # project's project.cfg, or kanban.cfg when the project sets none.
        # The refusal names that same file, so the remedy edits the right one.
        _dv_mode_source=""
        if [[ "${_dv_mode_output}" =~ for[[:space:]]+project[[:space:]]+\'[^\']*\'[[:space:]]+in[[:space:]]+(.+)[[:space:]]+\[release\][[:space:]]+deep_verify ]]; then
            _dv_mode_source="${BASH_REMATCH[1]}"
        fi
        _dv_mode_source="${_dv_mode_source:-project.cfg or kanban.cfg}"
        # BLOCK-WORTHY (roles/CODER.md, "Authoring a new gate"): an
        # unrecognized deep_verify value cannot be silently resolved to a guessed
        # schedule mode -- deferral by typo must not be possible in the
        # silent direction. Refusing
        # is an invariant-by-design stop, the same reasoning the resolver's
        # own project_resolve.not_found refusal applies to an unregistered
        # --project; it runs nothing and writes no ledger record.
        pgai_emit_error_contract \
            "deep_verify.invalid_schedule_mode" \
            "project '${_dv_project}' has an unrecognized [release] deep_verify value in ${_dv_mode_source}: ${_dv_mode_output}" \
            "Set [release] deep_verify in ${_dv_mode_source} to 'weekly' or 'off' (aliases 'true'/'false' also accepted) for project '${_dv_project}', or remove it there: the value resolves from project.cfg, then kanban.cfg, then the default 'weekly'."
        # CENSUS-ANCHOR: deep-verify-02
        exit 1
    fi

    if [[ "${_dv_mode}" == "off" ]]; then
        echo "deep-verify: project=${_dv_project} scheduled run skipped (deep_verify=off)"
        exit 0
    fi

    _dv_offhours_window="$(pp_deep_verify_offhours_window "${_dv_project}")"
    _dv_now_iso="${PGAI_DEEP_VERIFY_NOW:-$(date -u +%Y-%m-%dT%H:%M:%S+00:00)}"

    _dv_last_run_iso=""
    if _dv_ledger_latest="$(deep_verify_ledger_read_latest "${_dv_project}")"; then
        _dv_last_run_iso="$(printf '%s\n' "${_dv_ledger_latest}" | sed -n 's/^timestamp=//p')"
    else
        # A malformed ledger cannot be trusted to answer "is this due" --
        # fail safe (skip this cycle, do not guess) rather than fail open
        # (running the lane on ambiguous state). Writes no record, touches
        # no task state; the operator fixes the ledger and the next
        # scheduled firing (or an on-demand run) re-evaluates cleanly.
        echo "deep-verify: project=${_dv_project} scheduled run skipped -- ledger unreadable: ${_dv_ledger_latest}" >&2
        exit 0
    fi

    # --- Evaluate due-ness (pure decision, routed through pp_run_ops per
    # this file's own convention -- never a bare python module invocation). ---
    {
        IFS= read -r _dv_due_line
        IFS= read -r _dv_narration_line
    } < <(pp_run_ops pgai_agent_kanban.deep_verify.schedule \
        "${_dv_project}" "${_dv_mode}" "${_dv_last_run_iso}" "${_dv_now_iso}" "${_dv_offhours_window}")

    _dv_due="${_dv_due_line#due=}"
    _dv_narration="${_dv_narration_line#narration=}"

    echo "${_dv_narration}"

    if [[ "${_dv_due}" != "true" ]]; then
        exit 0
    fi
    # else: due -- fall through into the same on-demand execution path below.
fi

# --- Resolve the tree state actually exercised. Mirrors
# run-cold-build-tests.sh's own resolution precedence (team/scripts/lib/
# dev_tree.sh resolve_global_dev_tree: PGAI_DEV_TREE_PATH env var, then
# kanban.cfg's [paths] dev_tree_path), falling back to this script's own
# repo root (../.. from team/scripts/, where both scripts live) only when
# neither of those sources resolves to a real directory. --project names
# the ledger's owner, not which tree gets built (run-cold-build-tests.sh
# always builds the image flavors this framework declares in
# team/config/container-images.txt; see its own header comment), so this resolution is independent of --project.
#
# Exporting the resolved value here -- before the cold lane below is
# invoked -- guarantees the ledger records the SAME tree the lane actually
# builds: a real run-cold-build-tests.sh child process honors an
# already-exported PGAI_DEV_TREE_PATH ahead of its own resolution, so the
# two scripts never describe two different trees.
#
# In a repository checkout, the self-location fallback and the configured
# dev tree usually agree (team/scripts/../.. IS the checkout). From an
# installed kanban root they do not: team/scripts/../.. is the parent of
# the install, which has no VERSION file and is not a git repository, so
# the operator's configured dev_tree_path (or an inherited
# PGAI_DEV_TREE_PATH) is what makes the record correct there. ---
# shellcheck source=lib/dev_tree.sh
source "${_DV_LIB_DIR}/dev_tree.sh"
_DV_SELF_ROOT="$(cd "${_DV_SCRIPT_DIR}/../.." && pwd)"
_DV_REPO_ROOT="${PGAI_DEV_TREE_PATH:-$(resolve_global_dev_tree)}"
if [[ -z "${_DV_REPO_ROOT}" || ! -d "${_DV_REPO_ROOT}" ]]; then
    _DV_REPO_ROOT="${_DV_SELF_ROOT}"
fi
export PGAI_DEV_TREE_PATH="${_DV_REPO_ROOT}"
unset _DV_SELF_ROOT

_dv_version="unknown"
if [[ -f "${_DV_REPO_ROOT}/VERSION" ]]; then
    _dv_version="$(tr -d '[:space:]' < "${_DV_REPO_ROOT}/VERSION")"
elif git -C "${_DV_REPO_ROOT}" rev-parse --git-dir >/dev/null 2>&1; then
    # shellcheck source=lib/version_stamp.sh
    source "${_DV_LIB_DIR}/version_stamp.sh"
    _dv_version="$(git -C "${_DV_REPO_ROOT}" describe --tags --match "$(git_describe_tag_pattern "")" 2>/dev/null || echo unknown)"
fi
[[ -z "${_dv_version}" ]] && _dv_version="unknown"

_dv_commit="unknown"
if git -C "${_DV_REPO_ROOT}" rev-parse --git-dir >/dev/null 2>&1; then
    _dv_commit="$(git -C "${_DV_REPO_ROOT}" rev-parse --short HEAD 2>/dev/null || echo unknown)"
fi
[[ -z "${_dv_commit}" ]] && _dv_commit="unknown"

# --- Operator-only gate: the lane below builds images and starts
# containers, so nothing past this point runs without authorization. An
# unauthorized call prints SKIPPED and exits 0 here, before the ledger. ---
container_tier_require_operator "${BASH_SOURCE[0]}" ${_DV_ORIG_ARGS[@]+"${_DV_ORIG_ARGS[@]}"}

# --- Image selection: lists and exits 0 when nothing is selected, refuses
# an undeclared name; either way before the lane or the ledger. ---
container_tier_select_images "${BASH_SOURCE[0]}" ${_DV_ORIG_ARGS[@]+"${_DV_ORIG_ARGS[@]}"}
_dv_lane_image_args=()
for _dv_image in "${CONTAINER_TIER_IMAGES[@]}"; do
    _dv_lane_image_args+=(--image "${_dv_image}")
done
unset _dv_image
docker_use_session_begin "deep-verify.sh" "$(IFS=,; echo "${CONTAINER_TIER_IMAGES[*]}")"

# --- Run the cold lane, capturing combined stdout+stderr for verdict
# classification while still streaming it live for an operator watching an
# on-demand run. ---
_dv_cold_lane_cmd="${PGAI_DEEP_VERIFY_COLD_LANE_CMD:-${_DV_SCRIPT_DIR}/run-cold-build-tests.sh}"
_dv_output_file="$(pgai_mktemp deep_verify_cold_output)"

echo "deep-verify: running the cold-build lane for project '${_dv_project}' (tree ${_DV_REPO_ROOT} @ ${_dv_commit})..."
set +e
bash "${_dv_cold_lane_cmd}" "${_dv_lane_image_args[@]}" 2>&1 | tee "${_dv_output_file}"
_dv_lane_exit="${PIPESTATUS[0]}"
set -e

# --- Classify the outcome (pure function, no re-litigation of the lane's
# own decisions -- see the classifier module docstring). Routed through
# the shared pp_run_ops helper (never a bare python module invocation) so
# package resolution is cwd-independent regardless of where this script is
# invoked from. ---
{
    IFS= read -r _dv_verdict
    IFS= read -r _dv_detail
} < <(pp_run_ops pgai_agent_kanban.deep_verify.classify \
    "${_dv_lane_exit}" "${_dv_output_file}")

# --- Record the result in the project's ledger. ---
deep_verify_ledger_write "${_dv_project}" "${_dv_version}" "${_dv_commit}" "${_dv_verdict}" "${_dv_detail}"

echo "deep-verify: project=${_dv_project} version=${_dv_version} commit=${_dv_commit} verdict=${_dv_verdict}"
echo "deep-verify: detail: ${_dv_detail}"

# --- Failure path: file a bug (or cite an existing one covering the same
# failure) exactly like any other TESTER finding -- never touch whatever RC
# happens to be open (team/SOP-core.md "Gates and Refusals" -- "Unusable
# build blocks; an unavailable build environment does not"). Three cases
# enter it: failed-to-build; built from a lane that still exited non-zero
# (every image built, a non-build cold test failed); and could-not-attempt
# from a lane that exited 1 (no image could be built, a non-build cold test
# failed -- the classifier's only could-not-attempt verdict for exit 1). In
# the last two the filer names the failing test. The could-not-attempt case
# keys on lane exit 1 exactly, not on "non-zero": exit 2 (pytest not
# installed) and an unmapped exit code are also could-not-attempt, ran no
# test, and must file nothing. The captured output file is read here
# (still on disk from the classify step above) and removed immediately
# after, on every path -- a clean built run and every other
# could-not-attempt verdict never enter this block; their output file is
# removed by the unconditional cleanup a few lines further down instead. ---
if [[ "${_dv_verdict}" == "failed-to-build" ]] \
    || [[ "${_dv_verdict}" == "built" && "${_dv_lane_exit}" -ne 0 ]] \
    || [[ "${_dv_verdict}" == "could-not-attempt" && "${_dv_lane_exit}" -eq 1 ]]; then
    _dv_bug_outcome="" _dv_bug_id="" _dv_bug_path="" _dv_bug_pairs="" _dv_bug_tests=""
    while IFS= read -r _dv_bug_line; do
        case "${_dv_bug_line}" in
            outcome=*) _dv_bug_outcome="${_dv_bug_line#outcome=}" ;;
            bug_id=*) _dv_bug_id="${_dv_bug_line#bug_id=}" ;;
            bug_path=*) _dv_bug_path="${_dv_bug_line#bug_path=}" ;;
            pairs=*) _dv_bug_pairs="${_dv_bug_line#pairs=}" ;;
            tests=*) _dv_bug_tests="${_dv_bug_line#tests=}" ;;
        esac
    done < <(deep_verify_bug_filer_file "${_dv_project}" "${_dv_version}" "${_dv_commit}" "${_dv_output_file}")

    _dv_bug_subject="${_dv_bug_pairs:-${_dv_bug_tests:-an unstructured failure}}"
    case "${_dv_bug_outcome}" in
        duplicate)
            echo "deep-verify: citing existing ${_dv_bug_id} for ${_dv_bug_subject} (no duplicate filed)"
            ;;
        filed|no-pairs)
            echo "deep-verify: filed ${_dv_bug_id} (${_dv_bug_path}) for ${_dv_bug_subject}"
            ;;
        *)
            echo "deep-verify: WARNING: bug filer produced no recognizable outcome for this ${_dv_verdict} run (project=${_dv_project}, commit=${_dv_commit}, lane exit ${_dv_lane_exit}); see ${_dv_output_file} while it exists" >&2
            ;;
    esac

    rm -f "${_dv_output_file}"
    exit 1
fi

rm -f "${_dv_output_file}"
exit 0
