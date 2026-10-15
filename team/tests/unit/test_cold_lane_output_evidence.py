"""
test_cold_lane_output_evidence.py
=================================
The deep-verify classifier (pgai_agent_kanban.deep_verify.classify) reads
two lines out of the cold-build lane's output: the per-pair
"[cold-build] SKIP — <reason>" narration of a pair skipped for an
unavailable engine or registry, and the per-pair "Image built
successfully" line of a pair that built. Both are plain print() calls in
cold tests that skip or pass, so they reach the lane's output only when the
lane's pytest arguments stop pytest from capturing them away.

These tests prove that on real pytest output, not on hand-written text.
Each run writes a tiny tests/cold/ tree under pytest's tmp directory, runs
`python3 -m pytest` on it as a subprocess with exactly the lane's pytest
arguments (COLD_LANE_PYTEST_ARGS, read from
team/scripts/lib/cold_lane_pytest_args.sh -- the same file
run-cold-build-tests.sh sources), and classifies what pytest printed plus
the line the lane itself appends.

Coverage:
  - An engine-less run (every (engine, flavor) pair narrate-skipped, one
    skip raised in a fixture and one in a test body, nothing built)
    classifies could-not-attempt both when an engine-free cold test fails
    (exit 1) and when every engine-free test passes (exit 0).
  - A run in which every (engine, flavor) pair prints its "Image built
    successfully" line yields each pair from the classifier's built-pair
    reader and classifies built with no narrate-skipped note: the
    engine-free test that exercises _narrate_skip() itself puts no skip
    line into the lane output.
  - Every line the classifier reads from the engine-unavailable fixtures in
    tests/fixtures/deep_verify_lane_output.py also appears in that real
    output, so the fixtures cannot drift from what pytest prints.

No container engine is used, and the real cold tests are never imported or
run: the generated tree's narration helper and its engine-free narration
test are the source text of _narrate_skip() and
test_engine_absent_skip_reason_names_the_engine() read out of
team/tests/cold/test_docker_cold_build.py, so a change to how either prints
reaches these tests too.
"""

from __future__ import annotations

import ast
import os
import pathlib
import signal
import subprocess
import sys
import textwrap

import pytest

from pgai_agent_kanban.deep_verify.classify import (
    VERDICT_BUILT,
    VERDICT_COULD_NOT_ATTEMPT,
    _ALL_SKIPPED_MARKER,
    _BUILT_PAIR_RE,
    _SKIP_REASON_RE,
    _extract_built_pairs,
    classify_cold_build_result,
    extract_failing_tests,
)
from tests.fixtures.deep_verify_lane_output import (
    ENGINE_UNAVAILABLE_FAILING_TEST_FILE,
    ENGINE_UNAVAILABLE_FAILING_TEST_ID,
    ENGINE_UNAVAILABLE_FAILING_TEST_NODEID,
    ENGINE_UNAVAILABLE_NON_BUILD_FAILURE_OUTPUT,
    ENGINE_UNAVAILABLE_OTHER_TESTS_PASSED_OUTPUT,
    ENGINE_UNAVAILABLE_SKIP_REASON,
)
from tests.fixtures.scrubbed_subprocess_env import build_scrubbed_env

_TEAM_DIR = pathlib.Path(__file__).resolve().parent.parent.parent
_LANE_ARGS_LIB = _TEAM_DIR / "scripts" / "lib" / "cold_lane_pytest_args.sh"
_COLD_BUILD_MODULE = _TEAM_DIR / "tests" / "cold" / "test_docker_cold_build.py"

# The lines run-cold-build-tests.sh prints after pytest returns, for the
# two exit codes these tests produce.
_LANE_FAILED_TRAILER = "Cold-build lane FAILED (exit code: 1)\n"
_LANE_PASSED_TRAILER = "Cold-build lane PASSED\n"

_SUBPROCESS_TIMEOUT_SECONDS = 120

_PAIRS = [(engine, flavor) for engine in ("docker", "podman") for flavor in ("debian", "rocky9")]

_BODY_SKIP_REASON = (
    "rocky9 base-image reachability probe failed under podman: `podman pull "
    "rockylinux:9` did not succeed (registry unreachable); treating this as an "
    "environmental network gap rather than a Dockerfile defect"
)

# The cold-build module's engine-free test that checks _narrate_skip()'s
# skip reason, copied into every generated tree beside the helper.
_IN_TEST_NARRATION_TEST = "test_engine_absent_skip_reason_names_the_engine"


def _diagnostic(result: subprocess.CompletedProcess) -> str:
    return f"--- output ---\n{result.stdout}\n--- stderr ---\n{result.stderr}"


def _run(argv: list, *, cwd: pathlib.Path, env: dict) -> subprocess.CompletedProcess:
    """Run *argv* in its own process group with stdout and stderr merged,
    as the lane's caller captures them; on timeout kill the whole group,
    reap it, and re-raise."""
    proc = subprocess.Popen(
        argv,
        cwd=str(cwd),
        env=env,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        start_new_session=True,
    )
    try:
        stdout, _ = proc.communicate(timeout=_SUBPROCESS_TIMEOUT_SECONDS)
    except subprocess.TimeoutExpired:
        try:
            os.killpg(os.getpgid(proc.pid), signal.SIGKILL)
        except ProcessLookupError:
            pass
        proc.communicate()
        raise
    return subprocess.CompletedProcess(proc.args, proc.returncode, stdout, "")


def _subprocess_env(work_dir: pathlib.Path, extra_env: dict | None = None) -> dict:
    home = work_dir / "home"
    home.mkdir(exist_ok=True)
    return build_scrubbed_env(
        home_dir=home,
        extra_path_dirs=[pathlib.Path(sys.executable).parent],
        extra_env=extra_env,
    )


def _lane_pytest_args(work_dir: pathlib.Path) -> list:
    """COLD_LANE_PYTEST_ARGS as bash sees it after sourcing the lane's lib."""
    result = _run(
        [
            "bash",
            "-c",
            'set -euo pipefail; source "$1"; printf "%s\\0" "${COLD_LANE_PYTEST_ARGS[@]}"',
            "bash",
            str(_LANE_ARGS_LIB),
        ],
        cwd=_TEAM_DIR,
        env=_subprocess_env(work_dir),
    )
    assert result.returncode == 0, _diagnostic(result)
    args = [arg for arg in result.stdout.split("\0") if arg]
    assert args, f"{_LANE_ARGS_LIB} defined no COLD_LANE_PYTEST_ARGS\n{_diagnostic(result)}"
    return args


def _cold_build_function_source(name: str) -> str:
    """The source text of function *name* from the real cold-build module,
    read without importing or running that module."""
    module_text = _COLD_BUILD_MODULE.read_text(encoding="utf-8")
    for node in ast.parse(module_text).body:
        if isinstance(node, ast.FunctionDef) and node.name == name:
            return ast.get_source_segment(module_text, node) + "\n"
    raise AssertionError(f"no {name}() definition found in {_COLD_BUILD_MODULE}")


def _narration_source() -> str:
    """The real _narrate_skip() helper and the real engine-free test that
    exercises it, as one block of module text."""
    return (
        _cold_build_function_source("_narrate_skip")
        + "\n\n"
        + _cold_build_function_source(_IN_TEST_NARRATION_TEST)
    )


def _write_cold_tree(root: pathlib.Path, *, engine_free_test_fails: bool, builds: bool) -> None:
    """Write a tests/cold/ tree shaped like the lane's: an image-build module
    whose (engine, flavor) pairs narrate-skip through _narrate_skip() -- in
    a module-scoped fixture, as cold_built_image does, and in a test body --
    or, with *builds*, whose passing test prints every pair's built line;
    either way beside the real engine-free narration test; and an
    engine-free module whose first test fails when
    *engine_free_test_fails*."""
    cold = root / "tests" / "cold"
    cold.mkdir(parents=True)
    # Pins the rootdir to this tree so no ini file above it applies.
    (root / "pytest.ini").write_text("[pytest]\n", encoding="utf-8")

    if builds:
        build_module = (
            "import pytest\n\n\n"
            + _narration_source()
            + textwrap.dedent(
                f"""\


                _PAIRS = {_PAIRS!r}


                @pytest.mark.parametrize(
                    ("engine", "flavor"),
                    _PAIRS,
                    ids=[f"{{engine}}-{{flavor}}" for engine, flavor in _PAIRS],
                )
                def test_image_builds_without_error(engine, flavor):
                    print(
                        f"\\n[cold-build] [{{engine}}] [{{flavor}}] Building "
                        f"(--no-cache --pull) from docker/{{flavor}}/Dockerfile ...",
                        flush=True,
                    )
                    print(
                        f"[cold-build] [{{engine}}] [{{flavor}}] Image built "
                        f"successfully: pgai-cold-build-{{engine}}-{{flavor}}:probe",
                        flush=True,
                    )
                """
            )
        )
    else:
        build_module = (
            "import pytest\n\n\n"
            + _narration_source()
            + textwrap.dedent(
                f"""\


                _PAIRS = [
                    (engine, flavor)
                    for engine in ("docker", "podman")
                    for flavor in ("debian", "rocky9")
                ]


                @pytest.fixture(
                    scope="module",
                    params=_PAIRS,
                    ids=[f"{{engine}}-{{flavor}}" for engine, flavor in _PAIRS],
                )
                def cold_built_image(request):
                    engine, flavor = request.param
                    _narrate_skip(
                        f"{{engine}} binary not found or {{engine}} unreachable; "
                        f"cold-build lane requires a working {{engine}} installation "
                        f"to exercise the {{flavor}} flavor under {{engine}}"
                    )


                def test_image_builds_without_error(cold_built_image):
                    pass


                def test_tmux_version_meets_floor_in_built_image(cold_built_image):
                    pass


                def test_base_image_probe_failure_skips_the_pair():
                    _narrate_skip({_BODY_SKIP_REASON!r})
                """
            )
        )
    (cold / "test_docker_cold_build.py").write_text(build_module, encoding="utf-8")

    first_assertion = (
        "assert False, (\n"
        '        "team/scripts/dashboard/create.sh not found at "\n'
        '        "/repo/team/scripts/dashboard/create.sh; this file must be committed "\n'
        '        "to the repository"\n'
        "    )"
        if engine_free_test_fails
        else "pass"
    )
    (root / ENGINE_UNAVAILABLE_FAILING_TEST_FILE).write_text(
        f"def {ENGINE_UNAVAILABLE_FAILING_TEST_ID}():\n"
        f"    {first_assertion}\n\n\n"
        "def test_operator_window_layout_is_declared():\n"
        "    pass\n",
        encoding="utf-8",
    )


class _LaneRun:
    """One lane-shaped pytest run: its exit code, the output the lane's
    caller captures (pytest's output plus the lane's trailer for that exit
    code), and the raw subprocess result for failure messages."""

    def __init__(self, result: subprocess.CompletedProcess) -> None:
        self.result = result
        self.exit_code = result.returncode
        trailer = _LANE_FAILED_TRAILER if result.returncode == 1 else _LANE_PASSED_TRAILER
        self.output = result.stdout + trailer

    @property
    def diagnostic(self) -> str:
        return _diagnostic(self.result)


def _run_lane_pytest(
    work_dir: pathlib.Path, *, engine_free_test_fails: bool, builds: bool = False
) -> _LaneRun:
    """Run pytest with the lane's arguments on a tree generated under
    *work_dir*, as the lane does: from the tree's root, with a basetemp of
    its own in PYTEST_ADDOPTS."""
    lane_args = _lane_pytest_args(work_dir)
    root = work_dir / "lane"
    _write_cold_tree(root, engine_free_test_fails=engine_free_test_fails, builds=builds)
    env = _subprocess_env(
        work_dir,
        {
            "PYTEST_ADDOPTS": f"--basetemp={work_dir / 'lane-basetemp'}",
            "PYTHONDONTWRITEBYTECODE": "1",
            "COLUMNS": "80",
        },
    )
    return _LaneRun(_run([sys.executable, "-m", "pytest", *lane_args], cwd=root, env=env))


@pytest.fixture(scope="module")
def engine_less_failing_run(tmp_path_factory: pytest.TempPathFactory) -> _LaneRun:
    """Every pair narrate-skipped, nothing built, one engine-free test failing."""
    return _run_lane_pytest(
        tmp_path_factory.mktemp("engine-less-failing"), engine_free_test_fails=True
    )


@pytest.fixture(scope="module")
def engine_less_passing_run(tmp_path_factory: pytest.TempPathFactory) -> _LaneRun:
    """Every pair narrate-skipped, nothing built, every engine-free test passing."""
    return _run_lane_pytest(
        tmp_path_factory.mktemp("engine-less-passing"), engine_free_test_fails=False
    )


def _classifier_read_lines(output: str) -> dict:
    """The lines of *output* the classifier keys on, by kind."""
    return {
        "skip": {m.group(0) for m in _SKIP_REASON_RE.finditer(output)},
        "failing": {(t.outcome, t.nodeid) for t in extract_failing_tests(output)},
        "built": {(m.group("engine"), m.group("flavor")) for m in _BUILT_PAIR_RE.finditer(output)},
        "all_skipped_marker": _ALL_SKIPPED_MARKER in output,
    }


# ---------------------------------------------------------------------------
# Engine-less runs classify could-not-attempt on real output
# ---------------------------------------------------------------------------


def test_engine_less_run_with_a_failing_engine_free_test_classifies_could_not_attempt(
    engine_less_failing_run: _LaneRun,
) -> None:
    run = engine_less_failing_run
    assert run.exit_code == 1, run.diagnostic

    classification = classify_cold_build_result(run.exit_code, run.output)

    assert classification.verdict == VERDICT_COULD_NOT_ATTEMPT, (
        f"{classification}\n{run.diagnostic}"
    )
    assert ENGINE_UNAVAILABLE_FAILING_TEST_NODEID in classification.detail
    assert ENGINE_UNAVAILABLE_SKIP_REASON in classification.detail
    assert _BODY_SKIP_REASON in classification.detail


def test_engine_less_run_with_passing_engine_free_tests_classifies_could_not_attempt(
    engine_less_passing_run: _LaneRun,
) -> None:
    run = engine_less_passing_run
    assert run.exit_code == 0, run.diagnostic

    classification = classify_cold_build_result(run.exit_code, run.output)

    assert classification.verdict == VERDICT_COULD_NOT_ATTEMPT, (
        f"{classification}\n{run.diagnostic}"
    )
    assert ENGINE_UNAVAILABLE_SKIP_REASON in classification.detail
    assert _BODY_SKIP_REASON in classification.detail


# ---------------------------------------------------------------------------
# A passing build's built line is read from real output
# ---------------------------------------------------------------------------


@pytest.fixture(scope="module")
def every_pair_built_run(tmp_path_factory: pytest.TempPathFactory) -> _LaneRun:
    """Every pair built, every engine-free test passing."""
    return _run_lane_pytest(
        tmp_path_factory.mktemp("every-pair-built"), engine_free_test_fails=False, builds=True
    )


def test_passing_build_tests_print_built_pairs_the_classifier_reads(
    every_pair_built_run: _LaneRun,
) -> None:
    run = every_pair_built_run
    assert run.exit_code == 0, run.diagnostic

    # Sorted: a test-order plugin may run the pairs in any order.
    expected = sorted(f"{engine}/{flavor}: built" for engine, flavor in _PAIRS)
    assert sorted(_extract_built_pairs(run.output)) == expected, run.diagnostic
    classification = classify_cold_build_result(run.exit_code, run.output)
    assert classification.verdict == VERDICT_BUILT
    for pair in expected:
        assert pair in classification.detail


def test_run_with_every_pair_built_classifies_built_with_no_narrate_skipped_note(
    every_pair_built_run: _LaneRun,
) -> None:
    run = every_pair_built_run
    assert run.exit_code == 0, run.diagnostic
    # Four pair builds, the engine-free narration test, and the two tests of
    # the engine-free module all ran and passed.
    assert "7 passed" in run.output, run.diagnostic

    classification = classify_cold_build_result(run.exit_code, run.output)

    assert classification.verdict == VERDICT_BUILT, f"{classification}\n{run.diagnostic}"
    assert "narrate-skipped" not in classification.detail, (
        f"{classification}\n{run.diagnostic}"
    )
    assert not _SKIP_REASON_RE.search(run.output), run.diagnostic


# ---------------------------------------------------------------------------
# The engine-unavailable fixtures carry only lines real output carries
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("fixture_output", "real_run_fixture"),
    [
        (ENGINE_UNAVAILABLE_NON_BUILD_FAILURE_OUTPUT, "engine_less_failing_run"),
        (ENGINE_UNAVAILABLE_OTHER_TESTS_PASSED_OUTPUT, "engine_less_passing_run"),
    ],
    ids=["exit-1-engine-free-test-failed", "exit-0-engine-free-tests-passed"],
)
def test_engine_unavailable_fixture_lines_appear_in_real_lane_output(
    request: pytest.FixtureRequest, fixture_output: str, real_run_fixture: str
) -> None:
    run = request.getfixturevalue(real_run_fixture)
    fixture_lines = _classifier_read_lines(fixture_output)
    real_lines = _classifier_read_lines(run.output)

    assert fixture_lines["skip"], "the fixture narrates no skip; nothing to compare"
    assert fixture_lines["skip"] <= real_lines["skip"], (
        f"fixture skip lines absent from real output: "
        f"{sorted(fixture_lines['skip'] - real_lines['skip'])}\n{run.diagnostic}"
    )
    assert fixture_lines["failing"] == real_lines["failing"], run.diagnostic
    assert fixture_lines["built"] == real_lines["built"], run.diagnostic
    assert fixture_lines["all_skipped_marker"] == real_lines["all_skipped_marker"]
