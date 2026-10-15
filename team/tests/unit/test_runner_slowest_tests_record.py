"""
test_runner_slowest_tests_record.py
===================================
A certification run of either gated runner records its slowest tests
(team/scripts/lib/gated_runner_durations.py, wired through the lane planner
gated_runner_lanes.py and gated_runner_lanes.sh).

WHAT IS PROMISED
----------------
- Every lane of a certification run (no --files, no --quarantined) carries
  ``--durations=25 --durations-min=1.0`` and a JUnit report; a targeted or
  quarantined run carries neither.
- After the last lane, ``lint/durations-<suite>.json`` beside the findings
  file names the slowest tests (at most 25, each at least 1.0s, slowest
  first) with their seconds, plus ``total_seconds`` and ``test_count``.
- A ``--files`` run writes no durations file.
- Recording is informational: it never changes the run's exit code.

The end-to-end cases run the REAL runner in the throwaway tree
test_runner_targeted_files.py builds (its docstring says why a fixture
tree), with ``[testing] workers = 1`` so the run is one single-process lane.
"""

from __future__ import annotations

import importlib.util
import json
import pathlib
import sys

import pytest

from tests.unit.test_runner_parallel_lanes import _LaneSandbox, _lane_fixture
from tests.unit.test_runner_targeted_files import _RUNNER_CONFIG, _RUNNER_NAMES, _describe

_TEAM_DIR = pathlib.Path(__file__).parent.parent.parent
_LIB_DIR = _TEAM_DIR / "scripts" / "lib"


def _load(name: str):
    spec = importlib.util.spec_from_file_location(f"{name}_under_test", _LIB_DIR / f"{name}.py")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


durations = _load("gated_runner_durations")
lanes = _load("gated_runner_lanes")

_DURATION_OPTS = ("--durations=25", "--durations-min=1.0")


# ---------------------------------------------------------------------------
# Planner: which lanes carry the options
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("workers", [1, 3])
def test_every_certification_lane_records_durations(workers: int) -> None:
    plan = lanes.plan_lanes(
        ["-m", "not quarantine", "tests"], suite="unit", workers=workers,
        xdist_available=True, durations_dir="/scratch/durations",
    )
    assert plan.durations_dir == "/scratch/durations"
    for lane in plan.lanes:
        for opt in _DURATION_OPTS:
            assert opt in lane.argv, (lane.name, lane.argv)
        assert f"--junitxml=/scratch/durations/unit-{lane.name}.xml" in lane.argv, lane.argv


@pytest.mark.parametrize("shape", ["targeted", "quarantined"])
def test_targeted_and_quarantined_runs_record_nothing(shape: str) -> None:
    plan = lanes.plan_lanes(
        ["tests/unit/test_a.py"], suite="unit", workers=3, xdist_available=True,
        targeted=shape == "targeted", quarantined=shape == "quarantined",
        durations_dir="/scratch/durations",
    )
    assert plan.durations_dir is None
    assert "durations-dir" not in lanes.encode_plan(plan)
    for lane in plan.lanes:
        assert not any(arg.startswith(("--durations", "--junitxml")) for arg in lane.argv), lane.argv


def test_no_durations_dir_leaves_the_argv_unchanged() -> None:
    plan = lanes.plan_lanes(["-m", "not quarantine", "tests"], suite="unit", workers=1)
    assert plan.lanes[0].argv == ["-m", "not quarantine", "tests"]


# ---------------------------------------------------------------------------
# Merging lane reports
# ---------------------------------------------------------------------------


def _junit(path: pathlib.Path, suite_time: float, cases: list[tuple[str, str, float]]) -> None:
    body = "".join(
        f'<testcase classname="{cls}" name="{name}" time="{secs}" />' for cls, name, secs in cases
    )
    path.write_text(
        f'<?xml version="1.0"?><testsuites><testsuite name="pytest" tests="{len(cases)}" '
        f'time="{suite_time}">{body}</testsuite></testsuites>',
        encoding="utf-8",
    )


def test_lane_reports_merge_slowest_first_above_the_threshold(tmp_path: pathlib.Path) -> None:
    parallel = tmp_path / "unit-parallel.xml"
    serial = tmp_path / "unit-serial.xml"
    _junit(parallel, 10.5, [("tests.unit.test_a", "test_fast", 0.2), ("tests.unit.test_a", "test_slow", 2.5)])
    _junit(serial, 4.25, [("tests.unit.test_b.TestX", "test_mid[1]", 1.25), ("tests.unit.test_b", "test_edge", 1.0)])
    broken = tmp_path / "unit-broken.xml"
    broken.write_text("<not-xml", encoding="utf-8")

    summary = durations.summarize([parallel, serial, broken, tmp_path / "absent.xml"], "unit")

    assert summary == {
        "suite": "unit",
        "limit": 25,
        "min_seconds": 1.0,
        "tests": [
            {"test": "tests.unit.test_a::test_slow", "seconds": 2.5},
            {"test": "tests.unit.test_b.TestX::test_mid[1]", "seconds": 1.25},
            {"test": "tests.unit.test_b::test_edge", "seconds": 1.0},
        ],
        "total_seconds": 14.75,
        "test_count": 4,
    }


def test_the_list_is_capped_at_the_limit(tmp_path: pathlib.Path) -> None:
    report = tmp_path / "unit-single.xml"
    _junit(report, 100.0, [("tests.unit.test_many", f"test_{n:02d}", 1.0 + n / 100) for n in range(40)])
    summary = durations.summarize([report], "unit")
    assert len(summary["tests"]) == 25
    assert summary["tests"][0] == {"test": "tests.unit.test_many::test_39", "seconds": 1.39}
    assert summary["test_count"] == 40


def test_no_readable_report_writes_nothing(tmp_path: pathlib.Path) -> None:
    out = tmp_path / "lint" / "durations-unit.json"
    line = durations.record("unit", out, [tmp_path / "absent.xml"])
    assert not out.exists()
    assert "no durations file" in line


# ---------------------------------------------------------------------------
# End to end through the real runners
# ---------------------------------------------------------------------------

_SLOW_TEST = (
    "import time\n\n"
    "def test_sleeps_past_the_threshold():\n    time.sleep(1.2)\n\n"
    "def test_quick():\n    assert True\n"
)


@pytest.mark.parametrize("runner_name", _RUNNER_NAMES)
def test_certification_run_writes_the_slowest_tests_beside_the_findings_file(
    runner_name: str, tmp_path: pathlib.Path
) -> None:
    team = _lane_fixture(tmp_path, runner_name, _SLOW_TEST)
    sandbox = _LaneSandbox(tmp_path, "workers = 1\n")
    suite = _RUNNER_CONFIG[runner_name]["suite"]

    result = sandbox.run(team, runner_name)

    assert result.returncode == 0, _describe(result)
    record_path = sandbox.temp_dir / "lint" / f"durations-{suite}.json"
    assert record_path.parent == (sandbox.temp_dir / "lint" / f"findings-{suite}.json").parent
    assert record_path.is_file(), _describe(result)
    record = json.loads(record_path.read_text(encoding="utf-8"))
    names = [entry["test"] for entry in record["tests"]]
    assert len(names) == 1 and names[0].endswith("::test_sleeps_past_the_threshold"), record
    assert record["tests"][0]["seconds"] >= 1.2, record
    assert record["total_seconds"] >= 1.2, record
    assert record["test_count"] == 3, record  # the slow file's two tests plus the builder's test_named.py
    assert "--durations=25 --durations-min=1.0" in result.stdout, _describe(result)
    assert not list((sandbox.temp_dir / "durations").glob("*")), "the lane reports were left behind"


@pytest.mark.parametrize("runner_name", _RUNNER_NAMES)
def test_targeted_run_writes_no_durations_file(runner_name: str, tmp_path: pathlib.Path) -> None:
    team = _lane_fixture(tmp_path, runner_name, _SLOW_TEST)
    sandbox = _LaneSandbox(tmp_path, "workers = 1\n")
    suite = _RUNNER_CONFIG[runner_name]["suite"]
    target = f"{_RUNNER_CONFIG[runner_name]['test_dir']}/test_named.py"

    result = sandbox.run(team, runner_name, "--files", target)

    assert result.returncode == 0, _describe(result)
    assert not (sandbox.temp_dir / "lint" / f"durations-{suite}.json").exists(), _describe(result)
    assert "--durations" not in result.stdout, _describe(result)


def test_a_failing_run_keeps_its_exit_code_and_still_records(tmp_path: pathlib.Path) -> None:
    team = _lane_fixture(
        tmp_path, "run-unit-tests.sh",
        _SLOW_TEST + "\ndef test_fails():\n    assert False, 'fixture failure'\n",
    )
    sandbox = _LaneSandbox(tmp_path, "workers = 1\n")

    result = sandbox.run(team, "run-unit-tests.sh")

    assert result.returncode == 1, _describe(result)
    assert (sandbox.temp_dir / "lint" / "durations-unit.json").is_file(), _describe(result)
