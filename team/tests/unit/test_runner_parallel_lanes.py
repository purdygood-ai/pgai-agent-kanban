"""
test_runner_parallel_lanes.py
=============================
Behavioural tests for the gated runners' pytest lanes and coverage gate
(team/scripts/lib/gated_runner_lanes.py, team/scripts/lib/gated_runner_lanes.sh,
run-unit-tests.sh, run-integration-tests.sh).

WHAT THE LANES PROMISE
----------------------
- With ``[testing] workers`` above 1 and pytest-xdist importable, a full
  run is two pytest invocations: a parallel lane whose argv carries
  ``-n <workers> --dist loadfile`` and the run's selection ``and not
  serial``, then a serial lane whose argv carries the selection ``and
  serial`` and no ``-n``;
- with workers 0 or 1 the run is one lane whose argv is the runner's
  single-process argv, unchanged -- no ``-n``;
- with pytest-xdist not importable the run is that same single lane and
  says so in exactly one narrated line, never failing;
- a ``--files`` or ``--quarantined`` run is always one single-process lane;
- across every lane the worker count never exceeds ``workers``;
- the lanes' exit codes combine into one: the first failure wins, a lane
  that collected nothing (pytest exit 5) is not a failure on its own.

WHAT THE COVERAGE GATE PROMISES
-------------------------------
- ``--coverage`` is honoured only when ``[testing] coverage = true``, or
  when PGAI_TESTING_COVERAGE=1 is set outside an agent wake;
- otherwise the run prints ``coverage disabled by config ([testing]
  coverage)`` and passes no coverage option, and its exit code is
  unaffected;
- inside an agent wake (PGAI_AGENT_WAKE_ACTIVE=1) the variable has no
  effect, and the wake bootstrap unsets it.

WHY BOTH A PLANNER LAYER AND A FIXTURE TREE
-------------------------------------------
The argv and the coverage decision are asserted on the planner directly,
where every case is cheap and exact. The end-to-end promises -- the narrated
fallback, the combined exit code, the one findings file -- are asserted by
running the REAL runner against a throwaway REPO_ROOT-shaped tree built by
test_runner_targeted_files.py's fixture builder (the reason a fixture tree
is needed is in that module's docstring), with a kanban.cfg written into
the sandbox kanban root so the worker budget is stated, not defaulted.
Fixture runs that start pytest-xdist workers of their own carry the
``serial`` marker, so the outer suite runs them single-process and the host
never has more workers alive than the configured budget.
"""

from __future__ import annotations

import importlib.util
import json
import pathlib
import shutil
import subprocess
import sys

import pytest

from tests.fixtures.scrubbed_subprocess_env import build_scrubbed_env
from tests.unit.test_runner_targeted_files import (
    _RUNNER_CONFIG,
    _RUNNER_NAMES,
    _build_fixture,
    _describe,
)

_TEAM_DIR = pathlib.Path(__file__).parent.parent.parent
_LIB_DIR = _TEAM_DIR / "scripts" / "lib"
_PLANNER_PATH = _LIB_DIR / "gated_runner_lanes.py"
_LANES_SH = _LIB_DIR / "gated_runner_lanes.sh"
_WAKE_BOOTSTRAP_SH = _LIB_DIR / "wake_bootstrap.sh"
_PYPROJECT = _TEAM_DIR / "pyproject.toml"

_DISABLED_LINE = "coverage disabled by config ([testing] coverage)"
_XDIST_FALLBACK = "pytest-xdist is not importable"

# The single-process argv shape both runners assemble.
_UNIT_ARGV = [
    "-p",
    "no:randomly",
    "tests/unit/",
    "pgai_agent_kanban/api/tests/",
    "--ignore=tests/unit/test_entrypoint_shipped_image.py",
    "-m",
    "not quarantine",
]


def _load_planner():
    spec = importlib.util.spec_from_file_location("gated_runner_lanes_under_test", _PLANNER_PATH)
    if spec is None or spec.loader is None:
        raise ImportError(f"cannot load {_PLANNER_PATH}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


lanes = _load_planner()


def _plan(argv=_UNIT_ARGV, **kwargs):
    kwargs.setdefault("suite", "unit")
    kwargs.setdefault("xdist_available", True)
    kwargs.setdefault("pytest_cov_available", True)
    return lanes.plan_lanes(list(argv), **kwargs)


def _value_after(argv: list[str], flag: str) -> str | None:
    """The value following the last *flag* in *argv*, or None."""
    for index in range(len(argv) - 1, -1, -1):
        if argv[index] == flag and index + 1 < len(argv):
            return argv[index + 1]
    return None


def _contains_run(argv: list[str], run: list[str]) -> bool:
    return any(argv[i : i + len(run)] == run for i in range(len(argv) - len(run) + 1))


# ---------------------------------------------------------------------------
# Assembled argv: parallel, serial, single
# ---------------------------------------------------------------------------


def test_workers_three_with_xdist_runs_a_parallel_lane_then_a_serial_lane() -> None:
    plan = _plan(workers=3)

    assert [lane.name for lane in plan.lanes] == ["parallel", "serial"]
    parallel, serial = plan.lanes
    assert _contains_run(parallel.argv, ["-n", "3", "--dist", "loadfile"]), parallel.argv
    assert _value_after(parallel.argv, "-m") == "not quarantine and not serial"
    assert _value_after(serial.argv, "-m") == "not quarantine and serial"
    assert "-n" not in serial.argv, serial.argv
    assert "--dist" not in serial.argv, serial.argv
    # Everything but the selection is the runner's own argv, in order.
    for lane in plan.lanes:
        assert lane.argv[: _UNIT_ARGV.index("-m")] == _UNIT_ARGV[: _UNIT_ARGV.index("-m")]


@pytest.mark.parametrize("workers", [0, 1])
def test_workers_zero_or_one_runs_the_single_process_argv_unchanged(workers: int) -> None:
    plan = _plan(workers=workers)

    assert [lane.name for lane in plan.lanes] == ["single"]
    assert plan.lanes[0].argv == _UNIT_ARGV
    assert "-n" not in plan.lanes[0].argv


def test_xdist_not_importable_runs_single_process_with_one_narrated_line() -> None:
    plan = _plan(workers=3, xdist_available=False)

    assert [lane.name for lane in plan.lanes] == ["single"]
    assert plan.lanes[0].argv == _UNIT_ARGV
    fallback = [line for line in plan.say if _XDIST_FALLBACK in line]
    assert len(fallback) == 1, plan.say
    assert "workers = 3" in fallback[0]


@pytest.mark.parametrize("flag", ["targeted", "quarantined"])
def test_targeted_and_quarantined_runs_are_single_process_whatever_the_budget(flag: str) -> None:
    plan = _plan(workers=3, **{flag: True})

    assert [lane.name for lane in plan.lanes] == ["single"]
    assert plan.lanes[0].argv == _UNIT_ARGV


@pytest.mark.parametrize("workers", range(0, 9))
def test_worker_count_across_lanes_never_exceeds_the_budget(workers: int) -> None:
    plan = _plan(workers=workers)

    requested = [int(_value_after(lane.argv, "-n")) for lane in plan.lanes if "-n" in lane.argv]
    assert sum(requested) <= max(workers, 1)
    if workers > 1:
        assert requested == [workers]


def test_quarantined_selection_composes_into_both_lanes() -> None:
    argv = [*_UNIT_ARGV[:-1], "quarantine"]
    assert lanes.lane_selection("quarantine", serial=True) == "quarantine and serial"
    assert lanes.lane_selection("quarantine", serial=False) == "quarantine and not serial"
    assert lanes.lane_selection("a or b", serial=True) == "(a or b) and serial"
    assert lanes.lane_selection("", serial=True) == "serial"
    plan = _plan(argv=argv, workers=2)
    assert [lane.name for lane in plan.lanes] == ["parallel", "serial"]


def test_argv_without_a_selection_gains_one_per_lane() -> None:
    plan = _plan(argv=["-p", "no:randomly", "tests/unit/"], workers=2)

    assert _value_after(plan.lanes[0].argv, "-m") == "not serial"
    assert _value_after(plan.lanes[1].argv, "-m") == "serial"


# ---------------------------------------------------------------------------
# Coverage decision
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("requested", "config", "environ", "enabled", "narration"),
    [
        pytest.param(False, False, {}, False, None, id="not-requested"),
        pytest.param(False, True, {"PGAI_TESTING_COVERAGE": "1"}, False, None, id="not-requested-ignores-config"),
        pytest.param(True, False, {}, False, _DISABLED_LINE, id="requested-config-unset"),
        pytest.param(True, True, {}, True, "coverage enabled by config", id="config-true"),
        pytest.param(True, False, {"PGAI_TESTING_COVERAGE": "1"}, True, "PGAI_TESTING_COVERAGE=1", id="operator-env"),
        pytest.param(True, False, {"PGAI_TESTING_COVERAGE": "true"}, False, _DISABLED_LINE, id="env-must-be-1"),
        pytest.param(
            True,
            False,
            {"PGAI_TESTING_COVERAGE": "1", "PGAI_AGENT_WAKE_ACTIVE": "1"},
            False,
            _DISABLED_LINE,
            id="env-ignored-in-wake",
        ),
        pytest.param(
            True, True, {"PGAI_AGENT_WAKE_ACTIVE": "1"}, True, "coverage enabled by config", id="config-true-in-wake"
        ),
    ],
)
def test_coverage_decision(requested, config, environ, enabled, narration) -> None:
    decision = lanes.coverage_decision(requested, config, environ)

    assert decision.enabled is enabled
    if narration is None:
        assert decision.narration is None
    else:
        assert narration in (decision.narration or ""), decision.narration


def test_coverage_disabled_plan_carries_no_coverage_option() -> None:
    decision = lanes.coverage_decision(True, False, {})
    plan = _plan(workers=3, coverage=decision, coverage_dir="/cov")

    assert _DISABLED_LINE in plan.say
    assert plan.coverage_dir is None
    for lane in plan.lanes:
        assert not [arg for arg in lane.argv if arg.startswith("--cov")], lane.argv


def test_coverage_enabled_parallel_plan_appends_on_the_serial_lane() -> None:
    decision = lanes.coverage_decision(True, True, {})
    plan = _plan(workers=3, coverage=decision, coverage_dir="/cov")

    parallel, serial = plan.lanes
    expected = lanes.coverage_args("unit", "/cov")
    assert "--cov-report=json:/cov/unit.json" in expected
    assert parallel.argv[-len(expected) :] == expected
    assert serial.argv[-len(expected) - 1 :] == [*expected, "--cov-append"]
    assert "--cov-append" not in parallel.argv
    assert plan.coverage_dir == "/cov"


def test_coverage_enabled_single_plan_matches_the_previous_options() -> None:
    plan = _plan(
        workers=1, suite="integration", coverage=lanes.coverage_decision(True, True, {}), coverage_dir="/cov"
    )

    assert plan.lanes[0].argv == [
        *_UNIT_ARGV,
        "--cov=pgai_agent_kanban",
        "--cov=pm-agent",
        "--cov=scripts/lib",
        "--cov-branch",
        "--cov-report=term",
        "--cov-report=json:/cov/integration.json",
    ]


def test_coverage_enabled_without_pytest_cov_warns_and_runs_without_it() -> None:
    plan = _plan(
        workers=1,
        coverage=lanes.coverage_decision(True, True, {}),
        coverage_dir="/cov",
        pytest_cov_available=False,
    )

    assert plan.lanes[0].argv == _UNIT_ARGV
    assert plan.coverage_dir is None
    assert any("pytest-cov not installed" in line for line in plan.warn)


# ---------------------------------------------------------------------------
# Shell side: plan parsing, fallback, exit combination, scrub
# ---------------------------------------------------------------------------


def _bash(tmp_path: pathlib.Path, script: str, **extra_env: str) -> subprocess.CompletedProcess:
    home = tmp_path / "home"
    home.mkdir(exist_ok=True)
    return subprocess.run(
        ["bash", "-c", f'source "{_LANES_SH}"\n{script}'],
        capture_output=True,
        text=True,
        timeout=60,
        cwd=str(tmp_path),
        env=build_scrubbed_env(
            home_dir=home, extra_path_dirs=[pathlib.Path(sys.executable).parent], extra_env=extra_env
        ),
    )


_PRINT_LANES = (
    'for i in "${!GATED_RUNNER_LANE_NAMES[@]}"; do\n'
    '  gated_runner_lane_args "$i"\n'
    '  printf "LANE %s:" "${GATED_RUNNER_LANE_NAMES[i]}"\n'
    '  printf " [%s]" "${GATED_RUNNER_LANE_ARGS[@]}"\n'
    '  printf "\\n"\n'
    "done\n"
    'printf "COVDIR=%s\\n" "$GATED_RUNNER_COVERAGE_DIR"\n'
)


def _lane_lines(stdout: str) -> dict[str, str]:
    return {
        line.split(":", 1)[0].removeprefix("LANE "): line.split(":", 1)[1]
        for line in stdout.splitlines()
        if line.startswith("LANE ")
    }


def test_shell_plan_reads_the_planner_records_into_lane_arrays(tmp_path: pathlib.Path) -> None:
    argv = " ".join(f"'{a}'" for a in _UNIT_ARGV)
    result = _bash(
        tmp_path,
        f"gated_runner_plan_lanes --suite unit --workers 3 --coverage-requested --coverage-config true "
        f"--coverage-dir /cov -- {argv} --verbose\n{_PRINT_LANES}",
    )

    assert result.returncode == 0, _describe(result)
    lines = _lane_lines(result.stdout)
    assert list(lines) == ["parallel", "serial"], _describe(result)
    assert "[-n] [3] [--dist] [loadfile]" in lines["parallel"]
    assert "[-m] [not quarantine and not serial]" in lines["parallel"]
    assert "[-m] [not quarantine and serial]" in lines["serial"]
    assert "[-n]" not in lines["serial"]
    assert lines["serial"].endswith("[--cov-append]")
    assert "COVDIR=/cov" in result.stdout
    assert "coverage enabled by config" in result.stdout


def test_shell_plan_falls_back_to_the_given_argv_when_the_planner_fails(tmp_path: pathlib.Path) -> None:
    result = _bash(tmp_path, f"gated_runner_plan_lanes --suite no-such-suite -- -p no:randomly 'tests/a b.py'\n{_PRINT_LANES}")

    assert result.returncode == 0, _describe(result)
    assert _lane_lines(result.stdout) == {"single": " [-p] [no:randomly] [tests/a b.py]"}
    assert "[lanes] the lane planner" in result.stdout
    assert "COVDIR=\n" in result.stdout


@pytest.mark.parametrize(
    ("codes", "combined"),
    [
        (["0"], "0"),
        (["1"], "1"),
        (["5"], "5"),
        (["0", "5"], "0"),
        (["5", "0"], "0"),
        (["0", "1"], "1"),
        (["1", "0"], "1"),
        (["5", "1"], "1"),
        (["5", "5"], "5"),
        (["2", "1"], "2"),
    ],
)
def test_lane_exit_codes_combine_into_one(tmp_path: pathlib.Path, codes: list[str], combined: str) -> None:
    result = _bash(tmp_path, f"gated_runner_combine_lane_exits {' '.join(codes)}")

    assert result.returncode == 0, _describe(result)
    assert result.stdout == f"{combined}\n"


def test_scrub_removes_the_coverage_switch_from_the_shell_and_its_children(tmp_path: pathlib.Path) -> None:
    result = _bash(
        tmp_path,
        "gated_runner_coverage_scrub_env\nbash -c 'printf \"%s\" \"${PGAI_TESTING_COVERAGE-unset}\"'",
        PGAI_TESTING_COVERAGE="1",
    )

    assert result.returncode == 0, _describe(result)
    assert result.stdout == "unset"


def test_wake_bootstrap_scrubs_the_coverage_switch_beside_the_container_tier_scrub() -> None:
    lines = [line.strip() for line in _WAKE_BOOTSTRAP_SH.read_text(encoding="utf-8").splitlines()]
    calls = [line for line in lines if line and not line.startswith("#")]

    assert "container_tier_scrub_env" in calls
    assert "gated_runner_coverage_scrub_env" in calls
    tier = calls.index("container_tier_scrub_env")
    cov = calls.index("gated_runner_coverage_scrub_env")
    between = calls[tier + 1 : cov]
    assert len(between) <= 1 and all(line.startswith("source ") for line in between), (
        "the coverage scrub must sit beside the container-tier scrub (only its own source line between them); "
        f"found {between}"
    )


def test_serial_marker_is_registered_so_strict_markers_accepts_it(tmp_path: pathlib.Path) -> None:
    shutil.copy2(_PYPROJECT, tmp_path / "pyproject.toml")
    (tmp_path / "test_marked.py").write_text(
        "import pytest\n\n@pytest.mark.serial\ndef test_marked_serial():\n    assert True\n\n"
        "def test_unmarked():\n    assert True\n",
        encoding="utf-8",
    )
    home = tmp_path / "home"
    home.mkdir()
    result = subprocess.run(
        [sys.executable, "-m", "pytest", "-p", "no:randomly", "--strict-markers", "--collect-only", "-q",
         "-m", "serial", "test_marked.py"],
        capture_output=True,
        text=True,
        timeout=60,
        cwd=str(tmp_path),
        env=build_scrubbed_env(home_dir=home, extra_path_dirs=[pathlib.Path(sys.executable).parent]),
    )

    assert result.returncode == 0, _describe(result)
    assert "test_marked.py::test_marked_serial" in result.stdout, _describe(result)
    assert "test_unmarked" not in result.stdout, _describe(result)


# ---------------------------------------------------------------------------
# The real runners against a fixture tree
# ---------------------------------------------------------------------------

_LANE_MIX_FILE = "test_lane_mix.py"


class _LaneSandbox:
    """Throwaway kanban root (with a kanban.cfg), temp root and HOME."""

    def __init__(self, tmp_path: pathlib.Path, testing_cfg: str) -> None:
        self.kanban_root = tmp_path / "kanban_root"
        self.temp_dir = tmp_path / "temp_root"
        self.home = tmp_path / "home"
        self.gate_log = tmp_path / "gate.log"
        for d in (self.kanban_root, self.temp_dir, self.home):
            d.mkdir()
        self.gate_log.touch()
        (self.kanban_root / "kanban.cfg").write_text(f"[testing]\n{testing_cfg}", encoding="utf-8")

    def run(self, team: pathlib.Path, runner_name: str, *args: str, **extra_env: str) -> subprocess.CompletedProcess:
        env = build_scrubbed_env(
            home_dir=self.home,
            extra_path_dirs=[pathlib.Path(sys.executable).parent],
            extra_env={
                "PGAI_AGENT_KANBAN_ROOT_PATH": str(self.kanban_root),
                "KANBAN_ROOT": str(self.kanban_root),
                "PGAI_AGENT_KANBAN_TEMP_DIR": str(self.temp_dir),
                "GATE_LOG": str(self.gate_log),
                **extra_env,
            },
        )
        return subprocess.run(
            ["bash", str(team / "scripts" / runner_name), *args],
            capture_output=True,
            text=True,
            timeout=180,
            env=env,
            cwd=str(team.parent),
        )


def _lane_fixture(tmp_path: pathlib.Path, runner_name: str, source: str) -> pathlib.Path:
    """Fixture tree with the real pyproject.toml and one mixed test file;
    the shared builder's deliberately failing test_other.py is removed so a
    full run reaches only what this module writes."""
    team = _build_fixture(tmp_path, runner_name)
    shutil.copy2(_PYPROJECT, team / "pyproject.toml")
    test_dir = team / _RUNNER_CONFIG[runner_name]["test_dir"]
    (test_dir / "test_other.py").unlink()
    (test_dir / _LANE_MIX_FILE).write_text(source, encoding="utf-8")
    return team


def _replay_lines(stdout: str) -> list[str]:
    return [line for line in stdout.splitlines() if line.startswith("Replay this exact run: ")]


_PASSING_MIX = (
    "import pytest\n\n"
    "def test_unmarked_passes():\n    assert True\n\n"
    "@pytest.mark.serial\n"
    "def test_serial_passes():\n    assert True\n"
)
_FAILING_SERIAL_MIX = (
    "import pytest\n\n"
    "def test_unmarked_one_passes():\n    assert True\n\n"
    "def test_unmarked_two_passes():\n    assert True\n\n"
    "@pytest.mark.serial\n"
    "def test_serial_marked_fails():\n    assert False, 'serial-marked fixture failure'\n"
)


def _shadow_xdist(tmp_path: pathlib.Path) -> dict[str, str]:
    """Environment in which `import xdist` fails: a PYTHONPATH package of
    that name that raises ImportError, with pytest's entry-point plugin
    autoload off so pytest itself does not try to load the real plugin."""
    shadow = tmp_path / "no_xdist"
    (shadow / "xdist").mkdir(parents=True)
    (shadow / "xdist" / "__init__.py").write_text(
        "raise ImportError('pytest-xdist is absent in this fixture environment')\n", encoding="utf-8"
    )
    return {"PYTHONPATH": str(shadow), "PYTEST_DISABLE_PLUGIN_AUTOLOAD": "1"}


@pytest.mark.parametrize("runner_name", _RUNNER_NAMES)
def test_runner_without_xdist_narrates_one_line_and_runs_serially(runner_name: str, tmp_path: pathlib.Path) -> None:
    team = _lane_fixture(tmp_path, runner_name, _PASSING_MIX)
    sandbox = _LaneSandbox(tmp_path, "workers = 3\n")

    result = sandbox.run(team, runner_name, **_shadow_xdist(tmp_path))

    assert result.returncode == 0, _describe(result)
    output = result.stdout + result.stderr
    assert output.count(_XDIST_FALLBACK) == 1, _describe(result)
    replays = _replay_lines(result.stdout)
    assert len(replays) == 1, _describe(result)
    assert " -n " not in replays[0], replays[0]
    assert "3 passed" in result.stdout, _describe(result)  # the mix file plus the builder's test_named.py


@pytest.mark.parametrize(
    ("runner_name", "workers"),
    [("run-unit-tests.sh", "0"), ("run-unit-tests.sh", "1"), ("run-integration-tests.sh", "1")],
)
def test_runner_with_workers_zero_or_one_runs_one_lane_without_n(
    runner_name: str, workers: str, tmp_path: pathlib.Path
) -> None:
    team = _lane_fixture(tmp_path, runner_name, _PASSING_MIX)
    sandbox = _LaneSandbox(tmp_path, f"workers = {workers}\n")

    result = sandbox.run(team, runner_name)

    assert result.returncode == 0, _describe(result)
    replays = _replay_lines(result.stdout)
    assert len(replays) == 1, _describe(result)
    assert " -n " not in replays[0] and "--dist" not in replays[0], replays[0]
    assert "-m 'not quarantine'" in replays[0] or '-m not\\ quarantine' in replays[0], replays[0]
    assert "Lane 1 of" not in result.stdout, _describe(result)


@pytest.mark.serial
@pytest.mark.parametrize("runner_name", _RUNNER_NAMES)
def test_failing_serial_test_yields_one_nonzero_exit_and_one_findings_file(
    runner_name: str, tmp_path: pathlib.Path
) -> None:
    """Starts two pytest-xdist workers of its own, hence the serial mark."""
    team = _lane_fixture(tmp_path, runner_name, _FAILING_SERIAL_MIX)
    sandbox = _LaneSandbox(tmp_path, "workers = 2\n")
    suite = _RUNNER_CONFIG[runner_name]["suite"]

    result = sandbox.run(team, runner_name, "--verbose")

    assert result.returncode == 1, _describe(result)
    assert "Lane 1 of 2: parallel" in result.stdout, _describe(result)
    assert "Lane 2 of 2: serial" in result.stdout, _describe(result)
    replays = _replay_lines(result.stdout)
    assert len(replays) == 2, _describe(result)
    assert "-n 2 --dist loadfile" in replays[0], replays[0]
    assert " -n " not in replays[1], replays[1]
    assert "Lane exit codes: 0 1 (in lane order) -> combined exit code 1" in result.stdout, _describe(result)
    assert "test_serial_marked_fails" in result.stdout, _describe(result)
    findings = sorted((sandbox.temp_dir / "lint").glob("findings-*.json"))
    assert findings == [sandbox.temp_dir / "lint" / f"findings-{suite}.json"], findings
    document = json.loads(findings[0].read_text(encoding="utf-8"))
    assert document["runner"] == runner_name


@pytest.mark.serial
def test_passing_parallel_run_with_empty_serial_lane_exits_zero(tmp_path: pathlib.Path) -> None:
    """No test carries the serial marker yet: the serial lane collects
    nothing (pytest exit 5), which is not a failure. Starts two pytest-xdist
    workers of its own, hence the serial mark."""
    runner_name = "run-unit-tests.sh"
    team = _lane_fixture(tmp_path, runner_name, "def test_plain_passes():\n    assert True\n")
    sandbox = _LaneSandbox(tmp_path, "workers = 2\n")

    result = sandbox.run(team, runner_name)

    assert result.returncode == 0, _describe(result)
    assert "Lane exit codes: 0 5 (in lane order) -> combined exit code 0" in result.stdout, _describe(result)


def _has_cov(result: subprocess.CompletedProcess) -> bool:
    return any("--cov=" in line for line in _replay_lines(result.stdout))


_UNIT = "run-unit-tests.sh"
_INTEGRATION = "run-integration-tests.sh"


# The decision itself is the planner's, covered case by case above; these
# runs prove each runner wires it through (both runners for the config
# cases, the unit runner for the environment cases).
@pytest.mark.parametrize(
    ("runner_name", "testing_cfg", "extra_env", "collected", "narration"),
    [
        pytest.param(_UNIT, "workers = 1\n", {}, False, _DISABLED_LINE, id="unit-config-unset"),
        pytest.param(_INTEGRATION, "workers = 1\n", {}, False, _DISABLED_LINE, id="integration-config-unset"),
        pytest.param(_UNIT, "workers = 1\ncoverage = true\n", {}, True, "coverage enabled by config", id="unit-config-true"),
        pytest.param(
            _INTEGRATION, "workers = 1\ncoverage = true\n", {}, True, "coverage enabled by config",
            id="integration-config-true",
        ),
        pytest.param(
            _UNIT, "workers = 1\n", {"PGAI_TESTING_COVERAGE": "1"}, True, "PGAI_TESTING_COVERAGE=1",
            id="unit-operator-env",
        ),
        pytest.param(
            _UNIT,
            "workers = 1\n",
            {"PGAI_TESTING_COVERAGE": "1", "PGAI_AGENT_WAKE_ACTIVE": "1"},
            False,
            _DISABLED_LINE,
            id="unit-env-ignored-in-wake",
        ),
    ],
)
def test_runner_coverage_gate(
    runner_name: str, testing_cfg: str, extra_env: dict, collected: bool, narration: str, tmp_path: pathlib.Path
) -> None:
    team = _lane_fixture(tmp_path, runner_name, _PASSING_MIX)
    sandbox = _LaneSandbox(tmp_path, testing_cfg)

    result = sandbox.run(team, runner_name, "--coverage", **extra_env)

    assert result.returncode == 0, _describe(result)
    assert narration in result.stdout, _describe(result)
    assert _has_cov(result) is collected, _describe(result)


@pytest.mark.parametrize("runner_name", _RUNNER_NAMES)
def test_runner_refuses_a_malformed_worker_budget(runner_name: str, tmp_path: pathlib.Path) -> None:
    team = _lane_fixture(tmp_path, runner_name, _PASSING_MIX)
    sandbox = _LaneSandbox(tmp_path, "workers = 3x\n")

    result = sandbox.run(team, runner_name)

    assert result.returncode == 1, _describe(result)
    assert "config_loader.testing.invalid_workers" in result.stderr, _describe(result)
    assert not _replay_lines(result.stdout), "pytest must not start on a refused worker budget"


@pytest.mark.parametrize("runner_name", _RUNNER_NAMES)
def test_help_documents_workers_serial_lane_and_coverage_gate(runner_name: str, tmp_path: pathlib.Path) -> None:
    team = _build_fixture(tmp_path, runner_name)
    sandbox = _LaneSandbox(tmp_path, "")

    result = sandbox.run(team, runner_name, "--help")

    assert result.returncode == 0, _describe(result)
    for text in ("[testing] workers", "-n <workers> --dist loadfile", "@pytest.mark.serial",
                 "PGAI_TESTING_COVERAGE=1", _DISABLED_LINE, "[testing] coverage = true"):
        assert text in result.stdout, f"--help does not mention {text!r}\n" + _describe(result)
