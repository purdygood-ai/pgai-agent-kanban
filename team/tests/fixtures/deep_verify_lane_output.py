"""
deep_verify_lane_output.py
==========================
Captured run-cold-build-tests.sh output fixtures for the deep-verify
classifier, bug filer and deep-verify.sh tests.

Each constant is the combined stdout+stderr shape the cold lane prints:
pytest's progress lines, its FAILURES block, its short test summary and
final counts, then the lane's own closing narration. Passing tests' output
is captured by pytest and never printed, so an all-builds-passed run shows
the build modules only as progress dots.
"""

from __future__ import annotations

NON_BUILD_TEST_FILE = "tests/cold/test_strangers_path_check_harness.py"
NON_BUILD_TEST_ID = "test_seeded_break_in_install_step_fails_and_cites_doc_and_line"
NON_BUILD_TEST_NODEID = f"{NON_BUILD_TEST_FILE}::{NON_BUILD_TEST_ID}"

_SESSION_HEADER = """\
Running cold-build lane in /repo/team/tests/cold/
============================= test session starts ==============================
platform linux -- Python 3.9.21, pytest-8.4.2, pluggy-1.6.0
rootdir: /repo/team
configfile: pyproject.toml
collected 104 items

"""

_LANE_FAILED_TRAILER = """\
Cold-build lane wall-clock runtime: 813s
Cold-build lane FAILED (exit code: 1)
Next diagnostic step: scroll up to the pytest output directly above —
its short-test-summary 'FAILED <file>::<test>' lines, and the
'[cold-build] BUILD STDOUT/STDERR' blocks above them, name the specific
flavor, engine, and failing Dockerfile step. There is no command that
fixes a real Dockerfile defect.
"""

# Exit 1: every image-build test passed (debian and rocky9, docker and
# podman) and one non-build cold test failed.
NON_BUILD_TEST_FAILURE_OUTPUT = (
    _SESSION_HEADER
    + "tests/cold/test_container_shell_launcher.py ........                    [  7%]\n"
    "tests/cold/test_dashboard_operator_window.py ........                   [ 15%]\n"
    "tests/cold/test_dashboard_smoke_container.py ....                       [ 19%]\n"
    "tests/cold/test_docker_build_fixture.py ..                              [ 21%]\n"
    "tests/cold/test_docker_cold_build.py .................................. [ 53%]\n"
    "............................................                            [ 98%]\n"
    f"{NON_BUILD_TEST_FILE} F.                      [100%]\n"
    "\n"
    "=================================== FAILURES ===================================\n"
    f"_______ {NON_BUILD_TEST_ID} _______\n"
    "\n"
    "E       AssertionError: expected exit 1 with a broken Step 5 command; got 0.\n"
    "E       stdout:\n"
    "E       strangers-path-check: no container image selected -- nothing was built\n"
    "\n"
    f"{NON_BUILD_TEST_FILE}:132: AssertionError\n"
    "=========================== short test summary info ============================\n"
    f"FAILED {NON_BUILD_TEST_NODEID} - AssertionError: expected exit 1 with a brok...\n"
    "======================== 1 failed, 103 passed in 812.34s =======================\n"
    + _LANE_FAILED_TRAILER
)

# Exit 1: a real Dockerfile failure (structured engine/flavor/step line in
# the build test's failure) alongside the same non-build test failure.
DOCKERFILE_FAILURE_WITH_NON_BUILD_FAILURE_OUTPUT = (
    _SESSION_HEADER
    + "tests/cold/test_docker_cold_build.py ....F............................ [ 53%]\n"
    f"{NON_BUILD_TEST_FILE} F.                      [100%]\n"
    "\n"
    "=================================== FAILURES ===================================\n"
    "_______ test_image_builds_without_error[docker-debian] _______\n"
    "E       Failed: docker build of docker/debian/Dockerfile failed (exit 1) at "
    "step: RUN apt-get install -y tmux; base-image reachability was confirmed for "
    "every FROM in this Dockerfile before the build started, so this is a "
    "Dockerfile defect, not an environmental gap — see build output above\n"
    "=========================== short test summary info ============================\n"
    "FAILED tests/cold/test_docker_cold_build.py::test_image_builds_without_error[docker-debian]"
    " - Failed: docker build of docker/debian/Dockerfile failed (exit 1) at step: RUN...\n"
    f"FAILED {NON_BUILD_TEST_NODEID} - AssertionError: expected exit 1 with a brok...\n"
    "======================== 2 failed, 102 passed in 790.02s =======================\n"
    + _LANE_FAILED_TRAILER
)

# Exit 1: an image-build test failed without the structured step line.
BUILD_TEST_FAILURE_WITHOUT_STEP_OUTPUT = (
    _SESSION_HEADER
    + "tests/cold/test_docker_cold_build.py ......F.......................... [ 53%]\n"
    "\n"
    "=========================== short test summary info ============================\n"
    "FAILED tests/cold/test_docker_cold_build.py::test_tmux_version_meets_floor_in_built_image[podman-rocky9]"
    " - AssertionError: tmux 3.1 is below the floor 3.2\n"
    "======================== 1 failed, 103 passed in 801.10s =======================\n"
    + _LANE_FAILED_TRAILER
)

# Exit 0: no engine on the host, so no (engine, flavor) pair was collected,
# pytest exited 5, and the lane printed its all-skipped narration.
ENGINE_UNAVAILABLE_ALL_SKIPPED_OUTPUT = (
    _SESSION_HEADER.replace("collected 104 items", "collected 0 items")
    + "[cold-build] SKIP — engine 'docker' is not available on this host "
    "(binary not on PATH); every docker pair is skipped\n"
    "[cold-build] SKIP — engine 'podman' is not available on this host "
    "(binary not on PATH); every podman pair is skipped\n"
    "============================ no tests ran in 0.31s =============================\n"
    "Cold-build lane wall-clock runtime: 4s\n"
    "Cold-build lane: all (engine, flavor) pairs skipped (see narration above) — exit 0\n"
)

# Engine-less runs in which the lane collects every module under
# tests/cold/: the image-build tests narrate-skip every (engine, flavor)
# pair, while the engine-free cold modules still run. pytest does not exit
# 5, so the lane prints no all-skipped marker and no pair prints
# "Image built successfully".
ENGINE_UNAVAILABLE_FAILING_TEST_FILE = "tests/cold/test_dashboard_operator_window.py"
ENGINE_UNAVAILABLE_FAILING_TEST_ID = "test_create_sh_exists"
ENGINE_UNAVAILABLE_FAILING_TEST_NODEID = (
    f"{ENGINE_UNAVAILABLE_FAILING_TEST_FILE}::{ENGINE_UNAVAILABLE_FAILING_TEST_ID}"
)
ENGINE_UNAVAILABLE_SKIP_REASON = (
    "docker binary not found or docker unreachable; cold-build lane requires "
    "a working docker installation to exercise the debian flavor under docker"
)


def _engine_unavailable_progress(operator_window_progress: str) -> str:
    """Progress lines and per-pair skip narration for an engine-less run;
    *operator_window_progress* is the dashboard-operator-window module's
    result characters (its first test is the one the failing shape fails)."""
    narration = "".join(
        f"[cold-build] SKIP — {engine} binary not found or {engine} unreachable; "
        f"cold-build lane requires a working {engine} installation to exercise "
        f"the {flavor} flavor under {engine}\n"
        for engine in ("docker", "podman")
        for flavor in ("debian", "rocky9")
    )
    return (
        _SESSION_HEADER.replace("collected 104 items", "collected 40 items")
        + "tests/cold/test_container_shell_launcher.py ssss                     [ 10%]\n"
        f"{ENGINE_UNAVAILABLE_FAILING_TEST_FILE} {operator_window_progress}"
        "                   [ 30%]\n"
        "tests/cold/test_dashboard_smoke_container.py ss                         [ 35%]\n"
        "tests/cold/test_docker_build_fixture.py ..                              [ 40%]\n"
        + narration
        + "tests/cold/test_docker_cold_build.py ............sssssssssss          [ 92%]\n"
        "tests/cold/test_strangers_path_check_harness.py ...                    [100%]\n"
        "\n"
    )


# Exit 1: every pair narrate-skipped, nothing built, and one engine-free
# cold test failed.
ENGINE_UNAVAILABLE_NON_BUILD_FAILURE_OUTPUT = (
    _engine_unavailable_progress("F.......")
    + "=================================== FAILURES ===================================\n"
    f"_______ {ENGINE_UNAVAILABLE_FAILING_TEST_ID} _______\n"
    "\n"
    "E       AssertionError: team/scripts/dashboard/create.sh not found at "
    "/repo/team/scripts/dashboard/create.sh; this file must be committed to the "
    "repository\n"
    "\n"
    f"{ENGINE_UNAVAILABLE_FAILING_TEST_FILE}:438: AssertionError\n"
    "=========================== short test summary info ============================\n"
    f"FAILED {ENGINE_UNAVAILABLE_FAILING_TEST_NODEID} - AssertionError: "
    "team/scripts/dashboard/create.sh not fo...\n"
    "================== 1 failed, 22 passed, 17 skipped in 3.10s ===================\n"
    + _LANE_FAILED_TRAILER.replace("813s", "4s")
)

# Exit 0: every pair narrate-skipped, nothing built, and every engine-free
# cold test passed.
ENGINE_UNAVAILABLE_OTHER_TESTS_PASSED_OUTPUT = (
    _engine_unavailable_progress("........")
    + "======================= 23 passed, 17 skipped in 3.10s ========================\n"
    "Cold-build lane wall-clock runtime: 4s\n"
    "Cold-build lane PASSED\n"
)
