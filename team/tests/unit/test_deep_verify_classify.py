"""
test_deep_verify_classify.py
==============================
Unit tests for pgai_agent_kanban.deep_verify.classify — the pure function
that maps a run-cold-build-tests.sh invocation's exit code plus captured
stdout+stderr onto one of the three SOP verdict classes (team/SOP-core.md,
"Gates and Refusals" -- "Unusable build blocks; an unavailable build
environment does not").

Coverage:
  - exit 0 with per-pair "Image built successfully" lines -> built, detail
    names every built (engine, flavor) pair.
  - exit 0 with the script's own "all (engine, flavor) pairs skipped"
    narration -> could-not-attempt, detail names the unavailable resource
    from the captured SKIP reason.
  - exit 1 with a pytest.fail-shaped Dockerfile failure message ->
    failed-to-build, detail names the failing engine/flavor pair and step.
  - exit 1 with the cli-versions-env-missing / cli-versions-empty stable
    error keys -> failed-to-build (tree defect, not an engine/flavor pair
    failure) without inventing a pair that was never built.
  - exit 2 (pytest not installed) -> could-not-attempt, an environment gap
    distinct from both other verdicts.
  - An undocumented exit code -> could-not-attempt with a detail that says
    plainly the outcome is ambiguous, never guessed into built or
    failed-to-build.
  - The built and could-not-attempt records for a mixed run (some pairs
    built, some narrate-skipped) are still distinguishable: built wins
    because something real was learned, and the skip is folded into detail.
  - exit 1 where pytest's short test summary names only a non-build cold
    test (every image-build test passed) -> built, detail naming the
    failing test; a real Dockerfile failure in the same run still ->
    failed-to-build; an image-build test failing without a structured step
    line -> failed-to-build; an engine-unavailable run in the full lane
    shape -> could-not-attempt.
  - An engine-less run in which every pair is narrate-skipped and no pair
    built, without the all-skipped marker -> could-not-attempt, whether a
    non-build cold test failed (exit 1, detail naming the failing test and
    a skip reason) or the other cold tests passed (exit 0). A built pair in
    the same shape keeps the run built.
  - extract_failing_tests() reads FAILED/ERROR node ids from pytest's
    short test summary and tells image-build tests from the rest.

Every assertion is against the pure classify_cold_build_result() function --
no subprocess, no container engine, no filesystem. Function names describe
behaviour under test, never bug/task IDs (team/roles/TESTER.md, Test Authoring
Guidelines, anti-pattern 6).
"""

from __future__ import annotations

from pgai_agent_kanban.deep_verify.classify import (
    VALID_VERDICTS,
    VERDICT_BUILT,
    VERDICT_COULD_NOT_ATTEMPT,
    VERDICT_FAILED_TO_BUILD,
    classify_cold_build_result,
    extract_failing_tests,
    shows_build_evidence,
)
from tests.fixtures.deep_verify_lane_output import (
    BUILD_TEST_FAILURE_WITHOUT_STEP_OUTPUT,
    DOCKERFILE_FAILURE_WITH_NON_BUILD_FAILURE_OUTPUT,
    ENGINE_UNAVAILABLE_ALL_SKIPPED_OUTPUT,
    ENGINE_UNAVAILABLE_FAILING_TEST_NODEID,
    ENGINE_UNAVAILABLE_NON_BUILD_FAILURE_OUTPUT,
    ENGINE_UNAVAILABLE_OTHER_TESTS_PASSED_OUTPUT,
    ENGINE_UNAVAILABLE_SKIP_REASON,
    NON_BUILD_TEST_FAILURE_OUTPUT,
    NON_BUILD_TEST_NODEID,
)


def test_verdict_vocabulary_has_exactly_three_values() -> None:
    assert VALID_VERDICTS == ("built", "failed-to-build", "could-not-attempt")


def test_all_pairs_built_yields_built_verdict_naming_every_pair() -> None:
    output = "\n".join(
        [
            "[cold-build] [docker] [claude-code] Building (--no-cache --pull) ...",
            "[cold-build] [docker] [claude-code] Image built successfully: tag:test",
            "[cold-build] [podman] [claude-code] Image built successfully: tag:test",
            "[cold-build] [docker] [codex] Image built successfully: tag:test",
            "[cold-build] [podman] [codex] Image built successfully: tag:test",
            "Cold-build lane PASSED",
        ]
    )
    result = classify_cold_build_result(0, output)
    assert result.verdict == VERDICT_BUILT
    assert "docker/claude-code: built" in result.detail
    assert "podman/claude-code: built" in result.detail
    assert "docker/codex: built" in result.detail
    assert "podman/codex: built" in result.detail


def test_all_pairs_skipped_yields_could_not_attempt_naming_unavailable_resource() -> None:
    output = "\n".join(
        [
            "[cold-build] SKIP — claude-code base-image reachability probe failed "
            "under docker: `docker pull ghcr.io/example/base:latest` did not "
            "succeed (dial tcp: lookup ghcr.io: no such host); treating this as "
            "an environmental network gap rather than a Dockerfile defect",
            "Cold-build lane: all (engine, flavor) pairs skipped (see narration "
            "above) — exit 0",
        ]
    )
    result = classify_cold_build_result(0, output)
    assert result.verdict == VERDICT_COULD_NOT_ATTEMPT
    assert "ghcr.io" in result.detail
    assert "no such host" in result.detail


def test_exit_zero_with_no_recognizable_narration_still_records_built() -> None:
    """A shape this parser cannot extract per-pair detail from, but the
    lane's own exit code and PASSED banner are unambiguous -- built, with an
    honest note that no structured detail was captured."""
    result = classify_cold_build_result(0, "Cold-build lane PASSED\n")
    assert result.verdict == VERDICT_BUILT
    assert "no per-pair" in result.detail


def test_dockerfile_failure_names_engine_flavor_and_step() -> None:
    output = (
        "E       Failed: docker build of docker/claude-code/Dockerfile failed "
        "(exit 1) at step: RUN npm ci; base-image reachability was confirmed "
        "for every FROM in this Dockerfile before the build started, so this "
        "is a Dockerfile defect, not an environmental gap — see build output "
        "above"
    )
    result = classify_cold_build_result(1, output)
    assert result.verdict == VERDICT_FAILED_TO_BUILD
    assert "docker/claude-code" in result.detail
    assert "RUN npm ci" in result.detail


def test_multiple_dockerfile_failures_are_all_named() -> None:
    output = "\n".join(
        [
            "E       Failed: docker build of docker/claude-code/Dockerfile "
            "failed (exit 1) at step: RUN npm ci; base-image reachability was "
            "confirmed",
            "E       Failed: podman build of docker/codex/Dockerfile failed "
            "(exit 1) at step: COPY entrypoint.sh /entrypoint.sh; base-image "
            "reachability was confirmed",
        ]
    )
    result = classify_cold_build_result(1, output)
    assert result.verdict == VERDICT_FAILED_TO_BUILD
    assert "docker/claude-code" in result.detail
    assert "podman/codex" in result.detail


def test_missing_cli_versions_env_is_failed_to_build_not_a_pair_failure() -> None:
    output = (
        "error:   run-cold-build-tests.cli-versions-env-missing\n"
        "message: docker/cli-versions.env does not exist\n"
        "remedy:  restore it\n"
    )
    result = classify_cold_build_result(1, output)
    assert result.verdict == VERDICT_FAILED_TO_BUILD
    assert "cli-versions.env" in result.detail
    assert "run-cold-build-tests.cli-versions-env-missing" in result.detail


def test_empty_cli_versions_pin_is_failed_to_build() -> None:
    output = (
        "error:   run-cold-build-tests.cli-versions-empty\n"
        "message: CLAUDE_CODE_VERSION resolved empty\n"
        "remedy:  set it\n"
    )
    result = classify_cold_build_result(1, output)
    assert result.verdict == VERDICT_FAILED_TO_BUILD
    assert "run-cold-build-tests.cli-versions-empty" in result.detail


def test_pytest_not_installed_is_could_not_attempt() -> None:
    result = classify_cold_build_result(2, "FAIL: pytest is not installed\n")
    assert result.verdict == VERDICT_COULD_NOT_ATTEMPT
    assert "pytest" in result.detail


def test_undocumented_exit_code_records_ambiguity_not_a_guess() -> None:
    result = classify_cold_build_result(137, "killed\n")
    assert result.verdict == VERDICT_COULD_NOT_ATTEMPT
    assert "137" in result.detail
    assert "ambiguity" in result.detail or "ambiguous" in result.detail.lower()


def test_mixed_run_with_some_built_and_some_skipped_is_built() -> None:
    """Exit 0 without the script's own 'all skipped' banner means at least
    one pair actually ran and passed -- something real was learned about
    the tree, so this is built, with the skip folded into the detail
    rather than silently dropped."""
    output = "\n".join(
        [
            "[cold-build] [docker] [claude-code] Image built successfully: tag:test",
            "[cold-build] SKIP — codex base-image reachability probe failed "
            "under podman: engine not found",
            "Cold-build lane PASSED",
        ]
    )
    result = classify_cold_build_result(0, output)
    assert result.verdict == VERDICT_BUILT
    assert "docker/claude-code: built" in result.detail
    assert "codex" in result.detail


def test_built_and_could_not_attempt_records_are_distinguishable_by_verdict_alone() -> None:
    """The two records differ in verdict value; a caller never has to
    re-parse either detail string to tell them apart."""
    built = classify_cold_build_result(
        0,
        "[cold-build] [docker] [claude-code] Image built successfully: tag\n"
        "Cold-build lane PASSED\n",
    )
    could_not_attempt = classify_cold_build_result(
        0,
        "[cold-build] SKIP — engine absent\n"
        "Cold-build lane: all (engine, flavor) pairs skipped (see narration "
        "above) — exit 0\n",
    )
    assert built.verdict != could_not_attempt.verdict
    assert built.verdict == VERDICT_BUILT
    assert could_not_attempt.verdict == VERDICT_COULD_NOT_ATTEMPT


# ---------------------------------------------------------------------------
# Exit 1 from a failing test rather than a failing build
# ---------------------------------------------------------------------------


def test_non_build_test_failure_with_every_build_passing_classifies_built() -> None:
    result = classify_cold_build_result(1, NON_BUILD_TEST_FAILURE_OUTPUT)
    assert result.verdict == VERDICT_BUILT
    assert result.verdict != VERDICT_FAILED_TO_BUILD
    assert NON_BUILD_TEST_NODEID in result.detail
    assert "no structured" not in result.detail
    assert "\n" not in result.detail


def test_real_dockerfile_failure_alongside_a_non_build_failure_is_failed_to_build() -> None:
    result = classify_cold_build_result(1, DOCKERFILE_FAILURE_WITH_NON_BUILD_FAILURE_OUTPUT)
    assert result.verdict == VERDICT_FAILED_TO_BUILD
    assert "docker/debian" in result.detail
    assert "RUN apt-get install -y tmux" in result.detail


def test_build_test_failure_without_structured_step_is_failed_to_build() -> None:
    result = classify_cold_build_result(1, BUILD_TEST_FAILURE_WITHOUT_STEP_OUTPUT)
    assert result.verdict == VERDICT_FAILED_TO_BUILD
    assert "test_tmux_version_meets_floor_in_built_image[podman-rocky9]" in result.detail


def test_engine_unavailable_full_lane_output_is_could_not_attempt() -> None:
    result = classify_cold_build_result(0, ENGINE_UNAVAILABLE_ALL_SKIPPED_OUTPUT)
    assert result.verdict == VERDICT_COULD_NOT_ATTEMPT
    assert "engine 'docker' is not available" in result.detail


def test_exit_one_naming_no_failing_test_stays_failed_to_build() -> None:
    result = classify_cold_build_result(1, "Cold-build lane FAILED (exit code: 1)\n")
    assert result.verdict == VERDICT_FAILED_TO_BUILD


# ---------------------------------------------------------------------------
# No pair built and every pair narrate-skipped, without the all-skipped marker
# ---------------------------------------------------------------------------


def test_engine_unavailable_run_with_a_failing_non_build_test_is_could_not_attempt() -> None:
    assert "Image built successfully" not in ENGINE_UNAVAILABLE_NON_BUILD_FAILURE_OUTPUT
    assert "[cold-build] SKIP — docker" in ENGINE_UNAVAILABLE_NON_BUILD_FAILURE_OUTPUT
    assert "[cold-build] SKIP — podman" in ENGINE_UNAVAILABLE_NON_BUILD_FAILURE_OUTPUT

    result = classify_cold_build_result(1, ENGINE_UNAVAILABLE_NON_BUILD_FAILURE_OUTPUT)

    assert result.verdict == VERDICT_COULD_NOT_ATTEMPT
    assert result.verdict != VERDICT_BUILT
    assert ENGINE_UNAVAILABLE_FAILING_TEST_NODEID in result.detail
    assert ENGINE_UNAVAILABLE_SKIP_REASON in result.detail
    assert "\n" not in result.detail


def test_engine_unavailable_run_exiting_zero_without_the_all_skipped_marker_is_could_not_attempt() -> None:
    output = ENGINE_UNAVAILABLE_OTHER_TESTS_PASSED_OUTPUT
    assert "Image built successfully" not in output
    assert "all (engine, flavor) pairs skipped" not in output
    assert "23 passed, 17 skipped" in output

    result = classify_cold_build_result(0, output)

    assert result.verdict == VERDICT_COULD_NOT_ATTEMPT
    assert result.verdict != VERDICT_BUILT
    assert ENGINE_UNAVAILABLE_SKIP_REASON in result.detail
    assert "\n" not in result.detail


def test_a_built_pair_keeps_a_skipping_run_with_a_failing_non_build_test_built() -> None:
    output = ENGINE_UNAVAILABLE_NON_BUILD_FAILURE_OUTPUT.replace(
        "tests/cold/test_docker_cold_build.py",
        "[cold-build] [podman] [debian] Image built successfully: tag:test\n"
        "tests/cold/test_docker_cold_build.py",
        1,
    )

    result = classify_cold_build_result(1, output)

    assert result.verdict == VERDICT_BUILT
    assert "podman/debian: built" in result.detail
    assert ENGINE_UNAVAILABLE_FAILING_TEST_NODEID in result.detail


def test_shows_build_evidence_needs_a_built_pair_or_no_narrated_skip() -> None:
    assert shows_build_evidence("Cold-build lane PASSED\n") is True
    assert shows_build_evidence(NON_BUILD_TEST_FAILURE_OUTPUT) is True
    assert shows_build_evidence(ENGINE_UNAVAILABLE_OTHER_TESTS_PASSED_OUTPUT) is False
    assert shows_build_evidence(
        "[cold-build] [docker] [debian] Image built successfully: tag\n"
        "[cold-build] SKIP — podman binary not found or podman unreachable\n"
    ) is True


def test_extract_failing_tests_reads_summary_node_ids_and_build_modules() -> None:
    tests = extract_failing_tests(DOCKERFILE_FAILURE_WITH_NON_BUILD_FAILURE_OUTPUT)
    assert [t.nodeid for t in tests] == [
        "tests/cold/test_docker_cold_build.py::test_image_builds_without_error[docker-debian]",
        NON_BUILD_TEST_NODEID,
    ]
    assert [t.is_build_test for t in tests] == [True, False]
    assert tests[1].file == "tests/cold/test_strangers_path_check_harness.py"
    assert tests[1].test_id == "test_seeded_break_in_install_step_fails_and_cites_doc_and_line"
    assert tests[1].outcome == "FAILED"


def test_extract_failing_tests_reads_error_lines_and_collection_errors_once_each() -> None:
    output = (
        "ERROR tests/cold/test_dashboard_smoke_container.py::test_smoke[debian] - RuntimeError\n"
        "FAILED tests/cold/test_dashboard_smoke_container.py::test_smoke[debian]\n"
        "ERROR tests/cold/test_container_shell_launcher.py - ImportError: no module\n"
    )
    tests = extract_failing_tests(output)
    assert [t.nodeid for t in tests] == [
        "tests/cold/test_dashboard_smoke_container.py::test_smoke[debian]",
        "tests/cold/test_container_shell_launcher.py",
    ]
    assert tests[1].test_id == ""
