"""
test_run_gated_suites.py
========================
Behavioural tests for run-gated-suites.sh, the one-command entry point
that runs both gated suites (run-unit-tests.sh, run-integration-tests.sh)
inside one kanban.cfg ``[testing] workers`` budget, and for its planner,
team/scripts/lib/gated_suites.py.

WHAT THE ENTRY POINT PROMISES
-----------------------------
- With ``workers`` 2 or more, both suites start at the same time: unit with
  ``--workers <workers-1>``, integration with ``--workers 1``. Fed through
  the runners' own lane planner, that is ``-n <workers-1>`` for unit and a
  single-process integration run, and the workers alive at once never add
  up to more than ``workers``;
- with ``workers`` 0 or 1, unit runs and finishes before integration starts;
- each suite's runner writes its own findings file exactly as it does when
  run on its own, and the entry point adds a one-line ``<suite>.exit`` and a
  ``<suite>.log`` (the runner's stdout and stderr) per suite;
- the entry point's exit status is 0 only when both suites exit 0;
- ``--coverage`` passes through to each runner's own coverage gate and
  nothing else -- the entry point never sets PGAI_TESTING_COVERAGE;
- concurrent children skip their own scratch sweep and the entry point
  sweeps once, only after both pass;
- ``--help`` exits 0 and names every flag; a bad argument, a malformed
  budget or a missing runner is refused before any suite starts.

HOW
---
The argv split and the worker arithmetic are asserted on the planner
directly, then composed with the lane planner the runners use
(gated_runner_lanes.py), so the bound is checked on the pytest argv each
child would really run. The ordering promises are asserted by running the
REAL entry point from a throwaway scripts/ directory whose two runners are
stand-ins. The stand-ins record their argv and environment, and they
rendezvous on marker files, so "at the same time" and "one after the
other" are observed rather than inferred from timing. The evidence promises
(exit files, findings files, logs, a failing integration test) are asserted
by running the REAL entry point over the REAL runners against fixture
suites, built with test_runner_targeted_files.py's fixture builder.
"""

from __future__ import annotations

import importlib.util
import json
import os
import pathlib
import re
import shutil
import signal
import subprocess
import sys

import pytest

from tests.fixtures.scrubbed_subprocess_env import build_scrubbed_env
from tests.unit.test_runner_targeted_files import _build_fixture, _describe

_TEAM_DIR = pathlib.Path(__file__).parent.parent.parent
_SCRIPTS_DIR = _TEAM_DIR / "scripts"
_LIB_DIR = _SCRIPTS_DIR / "lib"
_ENTRY_POINT = "run-gated-suites.sh"
_SUITES = ("unit", "integration")
_RUNNERS = {"unit": "run-unit-tests.sh", "integration": "run-integration-tests.sh"}
_PYPROJECT = _TEAM_DIR / "pyproject.toml"


def _load(name: str, path: pathlib.Path):
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:
        raise ImportError(f"cannot load {path}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


suites = _load("gated_suites_under_test", _LIB_DIR / "gated_suites.py")
lanes = _load("gated_runner_lanes_for_suites_test", _LIB_DIR / "gated_runner_lanes.py")


def _value_after(argv: list[str], flag: str) -> str | None:
    for index in range(len(argv) - 1, -1, -1):
        if argv[index] == flag and index + 1 < len(argv):
            return argv[index + 1]
    return None


def _run_by_suite(plan) -> dict:
    return {run.suite: run for run in plan.runs}


# ---------------------------------------------------------------------------
# Planner: the split, the order, the bound
# ---------------------------------------------------------------------------


def test_workers_three_runs_both_suites_together_with_unit_two_and_integration_one() -> None:
    plan = suites.plan_suites(3)

    assert plan.mode == suites.CONCURRENT
    assert [run.suite for run in plan.runs] == ["unit", "integration"]
    runs = _run_by_suite(plan)
    assert runs["unit"].runner == "run-unit-tests.sh"
    assert runs["integration"].runner == "run-integration-tests.sh"
    assert _value_after(runs["unit"].argv, "--workers") == "2"
    assert _value_after(runs["integration"].argv, "--workers") == "1"
    for run in plan.runs:
        assert "--no-scratch-sweep" in run.argv, run.argv
    assert plan.sweep_after is True
    assert suites.peak_workers(plan) == 3


@pytest.mark.parametrize("workers", [0, 1])
def test_workers_below_two_runs_unit_then_integration_each_alone(workers: int) -> None:
    plan = suites.plan_suites(workers)

    assert plan.mode == suites.SEQUENTIAL
    assert [run.suite for run in plan.runs] == ["unit", "integration"]
    for run in plan.runs:
        assert run.argv == ["--workers", str(workers)]
        assert run.env == {}
    assert plan.sweep_after is False
    assert any("one after the other" in line for line in plan.say), plan.say


@pytest.mark.parametrize("workers", range(0, 10))
def test_peak_workers_never_exceed_the_budget_and_each_suite_gets_one(workers: int) -> None:
    plan = suites.plan_suites(workers)

    assert suites.peak_workers(plan) <= max(workers, 1)
    if workers >= 2:
        assert plan.mode == suites.CONCURRENT
        assert suites.peak_workers(plan) == workers
        assert all(suites.requested_workers(run) >= 1 for run in plan.runs)
    else:
        assert plan.mode == suites.SEQUENTIAL


@pytest.mark.parametrize("workers", range(2, 9))
def test_child_pytest_argv_from_the_lane_planner_stays_inside_the_budget(workers: int) -> None:
    """Feed each child's --workers through the lane planner the runners use
    and count the -n values in the pytest argv the two children would run
    side by side."""
    plan = suites.plan_suites(workers)
    total = 0
    pytest_argv = {}
    for run in plan.runs:
        lane_plan = lanes.plan_lanes(
            ["-p", "no:randomly", f"tests/{run.suite}/", "-m", "not quarantine"],
            suite=run.suite,
            workers=workers,
            requested_workers=suites.requested_workers(run),
            xdist_available=True,
            pytest_cov_available=True,
        )
        pytest_argv[run.suite] = [lane.argv for lane in lane_plan.lanes]
        # Lanes inside one runner run one after the other: its peak is its
        # largest -n (1 for a single-process lane).
        total += max(int(_value_after(argv, "-n") or 1) for argv in pytest_argv[run.suite])

    assert total <= workers, pytest_argv
    integration = pytest_argv["integration"]
    assert len(integration) == 1 and "-n" not in integration[0], integration
    if workers >= 3:
        assert _value_after(pytest_argv["unit"][0], "-n") == str(workers - 1), pytest_argv["unit"]


def test_workers_three_unit_pytest_argv_carries_n_two() -> None:
    unit = _run_by_suite(suites.plan_suites(3))["unit"]
    lane_plan = lanes.plan_lanes(
        ["tests/unit/", "-m", "not quarantine"],
        suite="unit",
        workers=3,
        requested_workers=suites.requested_workers(unit),
        xdist_available=True,
        pytest_cov_available=True,
    )

    assert [lane.name for lane in lane_plan.lanes] == ["parallel", "serial"]
    assert _value_after(lane_plan.lanes[0].argv, "-n") == "2"
    assert "-n" not in lane_plan.lanes[1].argv


@pytest.mark.parametrize(
    ("configured", "requested", "expected"),
    [(3, None, 3), (3, 2, 2), (3, 1, 1), (3, 0, 0), (1, 5, 1), (0, 3, 0), (2, 2, 2)],
)
def test_runner_workers_flag_lowers_the_budget_and_never_raises_it(
    configured: int, requested: int | None, expected: int
) -> None:
    workers, _source, narration = lanes.worker_budget(configured, requested)

    assert workers == expected
    if requested is None:
        assert narration is None
    elif requested > configured:
        assert f"above [testing] workers = {configured}" in (narration or ""), narration
    else:
        assert f"--workers {requested}" in (narration or ""), narration


def test_targeted_and_quarantined_runs_ignore_the_workers_flag() -> None:
    for flag in ("targeted", "quarantined"):
        plan = lanes.plan_lanes(
            ["tests/unit/"], suite="unit", workers=3, requested_workers=2, xdist_available=True, **{flag: True}
        )
        assert [lane.name for lane in plan.lanes] == ["single"]
        assert not any(line.startswith("Worker budget:") for line in plan.say), plan.say


def test_passthrough_flags_reach_both_children_unchanged() -> None:
    for workers in (1, 3):
        plan = suites.plan_suites(workers, coverage=True, verbose=True, reuse_if_green=True, coverage_root="/cov")
        for run in plan.runs:
            assert run.argv[-3:] == ["--coverage", "--verbose", "--reuse-if-green"], run.argv
            assert "PGAI_TESTING_COVERAGE" not in run.env


def test_concurrent_coverage_runs_keep_separate_coverage_data_files() -> None:
    plan = suites.plan_suites(3, coverage=True, coverage_root="/cov/")
    files = {run.suite: run.env.get("COVERAGE_FILE") for run in plan.runs}

    assert files == {"unit": "/cov/unit-data/.coverage", "integration": "/cov/integration-data/.coverage"}
    assert sorted(plan.dirs) == ["/cov/integration-data", "/cov/unit-data"]
    assert all(run.env == {} for run in suites.plan_suites(3, coverage_root="/cov").runs)
    assert all(run.env == {} for run in suites.plan_suites(1, coverage=True, coverage_root="/cov").runs)


@pytest.mark.parametrize(
    ("codes", "combined"),
    [([0, 0], 0), ([0, 1], 1), ([1, 0], 1), ([2, 1], 2), ([1, 2], 1), ([0, 2], 2)],
)
def test_suite_exit_codes_combine_first_failure_in_suite_order(codes: list[int], combined: int) -> None:
    assert suites.combine_exits(codes) == combined


def test_plan_records_round_trip_through_the_cli() -> None:
    result = subprocess.run(
        [sys.executable, str(_LIB_DIR / "gated_suites.py"), "plan", "--workers", "3", "--coverage",
         "--coverage-root", "/cov"],
        capture_output=True,
        text=True,
        timeout=60,
        cwd=str(_TEAM_DIR),
    )

    assert result.returncode == 0, _describe(result)
    tokens = result.stdout.split("\0")
    assert tokens[:2] == ["mode", "concurrent"]
    unit_at = tokens.index("run")
    assert tokens[unit_at : unit_at + 10] == [
        "run", "unit", "run-unit-tests.sh", "1", "COVERAGE_FILE=/cov/unit-data/.coverage",
        "4", "--workers", "2", "--no-scratch-sweep", "--coverage",
    ]


# ---------------------------------------------------------------------------
# The real entry point over stand-in runners: order, argv, files, refusals
# ---------------------------------------------------------------------------

# A stand-in runner. It records its argv and the two coverage variables it
# was started with, whether a stale exit file for its suite was present,
# and whether the other suite had started or finished when it started.
# With STUB_RENDEZVOUS=1 it waits (bounded) for the other suite to start,
# which only happens when the two run at the same time.
_STUB_RUNNER = r"""#!/usr/bin/env bash
suite="__SUITE__"
other="__OTHER__"
d="$STUB_DIR"
printf '%s\n' "$@" > "$d/$suite.argv"
printf '%s\n' "${COVERAGE_FILE-<unset>}" > "$d/$suite.coverage_file"
printf '%s\n' "${PGAI_TESTING_COVERAGE-<unset>}" > "$d/$suite.coverage_env"
[[ -e "$STUB_OUT_DIR/$suite.exit" ]] && touch "$d/$suite.saw_stale_exit"
[[ -e "$d/$other.started" ]] && touch "$d/$suite.saw_other_started"
[[ -e "$d/$other.done" ]] && touch "$d/$suite.saw_other_done"
touch "$d/$suite.started"
if [[ "${STUB_RENDEZVOUS:-0}" == "1" ]]; then
  for _ in $(seq 1 200); do
    if [[ -e "$d/$other.started" ]]; then touch "$d/$suite.met_other"; break; fi
    sleep 0.05
  done
fi
if [[ "${STUB_HANG:-0}" == "1" ]]; then
  sleep 60 &
  echo "$!" > "$d/$suite.grandchild.pid"
  wait
fi
echo "$suite stdout line"
echo "$suite stderr line" >&2
touch "$d/$suite.done"
exit_var="STUB_EXIT_$suite"
exit "${!exit_var:-0}"
"""


class _StubTree:
    """A throwaway scripts/ dir holding the real entry point and lib/ plus
    two stand-in runners, with its own kanban root, temp root and HOME."""

    def __init__(self, tmp_path: pathlib.Path, workers: str | None) -> None:
        self.scripts = tmp_path / "fixture" / "scripts"
        self.scripts.mkdir(parents=True)
        shutil.copy2(_SCRIPTS_DIR / _ENTRY_POINT, self.scripts / _ENTRY_POINT)
        shutil.copytree(_LIB_DIR, self.scripts / "lib")
        for suite, other in (("unit", "integration"), ("integration", "unit")):
            runner = self.scripts / _RUNNERS[suite]
            runner.write_text(_STUB_RUNNER.replace("__SUITE__", suite).replace("__OTHER__", other), encoding="utf-8")
            runner.chmod(0o755)
        self.kanban_root = tmp_path / "kanban_root"
        self.temp_dir = tmp_path / "temp_root"
        self.home = tmp_path / "home"
        self.stub_dir = tmp_path / "stub"
        for d in (self.kanban_root, self.temp_dir, self.home, self.stub_dir):
            d.mkdir()
        if workers is not None:
            (self.kanban_root / "kanban.cfg").write_text(f"[testing]\nworkers = {workers}\n", encoding="utf-8")
        self.out_dir = self.temp_dir

    def env(self, **extra: str) -> dict:
        return build_scrubbed_env(
            home_dir=self.home,
            extra_path_dirs=[pathlib.Path(sys.executable).parent],
            extra_env={
                "PGAI_AGENT_KANBAN_ROOT_PATH": str(self.kanban_root),
                "KANBAN_ROOT": str(self.kanban_root),
                "PGAI_AGENT_KANBAN_TEMP_DIR": str(self.temp_dir),
                "STUB_DIR": str(self.stub_dir),
                "STUB_OUT_DIR": str(self.out_dir),
                **extra,
            },
        )

    def run(self, *args: str, timeout: int = 60, **extra: str) -> subprocess.CompletedProcess:
        return _run_owned(["bash", str(self.scripts / _ENTRY_POINT), *args], self.env(**extra), self.scripts, timeout)

    def marker(self, name: str) -> bool:
        return (self.stub_dir / name).exists()

    def argv(self, suite: str) -> list[str]:
        return (self.stub_dir / f"{suite}.argv").read_text(encoding="utf-8").splitlines()

    def recorded(self, suite: str, what: str) -> str:
        return (self.stub_dir / f"{suite}.{what}").read_text(encoding="utf-8").strip()


def _run_owned(argv: list[str], env: dict, cwd: pathlib.Path, timeout: int) -> subprocess.CompletedProcess:
    """Run *argv* in its own process group; on timeout kill the whole group
    (the entry point starts runners, which start their own children)."""
    proc = subprocess.Popen(
        argv,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        env=env,
        cwd=str(cwd),
        start_new_session=True,
    )
    try:
        stdout, stderr = proc.communicate(timeout=timeout)
    except subprocess.TimeoutExpired:
        os.killpg(os.getpgid(proc.pid), signal.SIGKILL)
        proc.communicate()
        raise
    return subprocess.CompletedProcess(argv, proc.returncode, stdout, stderr)


def test_workers_three_starts_both_stub_runners_together_with_the_split_argv(tmp_path: pathlib.Path) -> None:
    tree = _StubTree(tmp_path, "3")

    result = tree.run(STUB_RENDEZVOUS="1")

    assert result.returncode == 0, _describe(result)
    assert tree.marker("unit.met_other") and tree.marker("integration.met_other"), (
        "each suite must see the other start before it finishes\n" + _describe(result)
    )
    assert tree.argv("unit") == ["--workers", "2", "--no-scratch-sweep"]
    assert tree.argv("integration") == ["--workers", "1", "--no-scratch-sweep"]
    assert "-- 3 workers in total" in result.stdout, _describe(result)


def test_workers_one_runs_unit_to_completion_before_integration_starts(tmp_path: pathlib.Path) -> None:
    tree = _StubTree(tmp_path, "1")

    result = tree.run()

    assert result.returncode == 0, _describe(result)
    assert not tree.marker("unit.saw_other_started"), "integration started before unit"
    assert tree.marker("integration.saw_other_done"), "integration started before unit finished"
    assert tree.argv("unit") == ["--workers", "1"]
    assert tree.argv("integration") == ["--workers", "1"]
    assert "one after the other" in result.stdout, _describe(result)


@pytest.mark.parametrize("workers", ["1", "3"])
def test_writes_one_line_exit_files_and_the_runner_output_as_logs(workers: str, tmp_path: pathlib.Path) -> None:
    tree = _StubTree(tmp_path, workers)
    for suite in _SUITES:
        (tree.out_dir / f"{suite}.exit").write_text("0\n", encoding="utf-8")

    result = tree.run(STUB_EXIT_integration="1")

    assert result.returncode == 1, _describe(result)
    assert (tree.out_dir / "unit.exit").read_text(encoding="utf-8") == "0\n"
    assert (tree.out_dir / "integration.exit").read_text(encoding="utf-8") == "1\n"
    for suite in _SUITES:
        assert not tree.marker(f"{suite}.saw_stale_exit"), f"the previous {suite}.exit was still in place"
        log = (tree.out_dir / f"{suite}.log").read_text(encoding="utf-8")
        assert f"{suite} stdout line" in log and f"{suite} stderr line" in log, log
    assert "Gated suites: unit exit 0, integration exit 1 -> exit 1" in result.stdout, _describe(result)
    assert "Last 20 lines of the integration log" in result.stdout, _describe(result)


@pytest.mark.parametrize(("unit_exit", "integration_exit", "expected"), [("2", "1", 2), ("0", "0", 0), ("1", "0", 1)])
def test_entry_point_exit_is_the_first_failing_suite(
    unit_exit: str, integration_exit: str, expected: int, tmp_path: pathlib.Path
) -> None:
    tree = _StubTree(tmp_path, "3")

    result = tree.run(STUB_EXIT_unit=unit_exit, STUB_EXIT_integration=integration_exit)

    assert result.returncode == expected, _describe(result)


def test_out_dir_receives_the_exit_files_and_logs(tmp_path: pathlib.Path) -> None:
    tree = _StubTree(tmp_path, "2")
    tree.out_dir = tmp_path / "temp_root" / "tester"

    result = tree.run("--out-dir", str(tree.out_dir))

    assert result.returncode == 0, _describe(result)
    assert sorted(p.name for p in tree.out_dir.iterdir()) == [
        "integration.exit", "integration.log", "unit.exit", "unit.log",
    ]
    assert not (tree.temp_dir / "unit.exit").exists()


def test_coverage_passes_through_without_switching_the_gate(tmp_path: pathlib.Path) -> None:
    tree = _StubTree(tmp_path, "3")

    result = tree.run("--coverage")

    assert result.returncode == 0, _describe(result)
    for suite in _SUITES:
        assert "--coverage" in tree.argv(suite)
        assert tree.recorded(suite, "coverage_env") == "<unset>"
        assert tree.recorded(suite, "coverage_file") == str(tree.temp_dir / "coverage" / f"{suite}-data" / ".coverage")
        assert (tree.temp_dir / "coverage" / f"{suite}-data").is_dir()


def test_concurrent_green_run_sweeps_scratch_once_after_both_pass(tmp_path: pathlib.Path) -> None:
    tree = _StubTree(tmp_path, "3")
    scratch = tree.temp_dir / "tests" / "left-by-a-suite"
    scratch.mkdir(parents=True)

    result = tree.run()

    assert result.returncode == 0, _describe(result)
    assert not (tree.temp_dir / "tests").exists(), _describe(result)


@pytest.mark.parametrize(("workers", "fail"), [("3", "1"), ("1", "0")])
def test_scratch_is_kept_after_a_red_concurrent_run_and_left_to_the_runners_when_sequential(
    workers: str, fail: str, tmp_path: pathlib.Path
) -> None:
    tree = _StubTree(tmp_path, workers)
    scratch = tree.temp_dir / "tests" / "left-by-a-suite"
    scratch.mkdir(parents=True)

    result = tree.run(STUB_EXIT_integration=fail)

    assert result.returncode == int(fail), _describe(result)
    assert scratch.is_dir(), _describe(result)
    assert "Sweeping post-suite scratch" not in result.stdout, _describe(result)


def test_term_stops_both_runners_and_their_children(tmp_path: pathlib.Path) -> None:
    tree = _StubTree(tmp_path, "3")
    proc = subprocess.Popen(
        ["bash", str(tree.scripts / _ENTRY_POINT)],
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        env=tree.env(STUB_HANG="1"),
        cwd=str(tree.scripts),
        start_new_session=True,
    )
    try:
        pid_files = [tree.stub_dir / f"{suite}.grandchild.pid" for suite in _SUITES]
        for _ in range(400):
            if all(p.exists() and p.read_text(encoding="utf-8").strip() for p in pid_files):
                break
            subprocess.run(["sleep", "0.05"], check=False)
        grandchildren = [int(p.read_text(encoding="utf-8")) for p in pid_files]
        proc.send_signal(signal.SIGTERM)
        stdout, stderr = proc.communicate(timeout=30)
    finally:
        if proc.poll() is None:
            os.killpg(os.getpgid(proc.pid), signal.SIGKILL)
            proc.communicate()

    assert proc.returncode == 143, f"stdout:\n{stdout}\nstderr:\n{stderr}"
    for pid in grandchildren:
        for _ in range(100):
            if not pathlib.Path(f"/proc/{pid}").exists():
                break
            subprocess.run(["sleep", "0.05"], check=False)
        assert not pathlib.Path(f"/proc/{pid}").exists(), f"a runner's child (pid {pid}) outlived the entry point"


def test_planner_failure_falls_back_to_one_runner_after_the_other(tmp_path: pathlib.Path) -> None:
    tree = _StubTree(tmp_path, "3")
    (tree.scripts / "lib" / "gated_suites.py").write_text("import sys\nsys.exit(3)\n", encoding="utf-8")

    result = tree.run("--verbose")

    assert result.returncode == 0, _describe(result)
    assert "[gated-suites] the run planner" in result.stdout, _describe(result)
    assert tree.marker("integration.saw_other_done")
    assert tree.argv("unit") == ["--verbose"] and tree.argv("integration") == ["--verbose"]


def test_help_exits_zero_and_describes_every_flag(tmp_path: pathlib.Path) -> None:
    tree = _StubTree(tmp_path, "3")

    result = tree.run("--help")

    assert result.returncode == 0, _describe(result)
    for flag in ("--coverage", "--verbose", "--reuse-if-green", "--out-dir DIR", "--help", "[testing] workers",
                 "<suite>.exit", "<suite>.log", "--no-scratch-sweep", "COVERAGE_FILE", "PGAI_TESTING_COVERAGE=1"):
        assert flag in result.stdout, f"--help does not mention {flag!r}\n" + _describe(result)
    assert not any(tree.stub_dir.iterdir()), "--help started a runner"


@pytest.mark.parametrize(
    ("args", "workers", "key"),
    [
        (["--files", "tests/unit/test_x.py"], "3", "run-gated-suites.unrecognized-argument"),
        (["--out-dir"], "3", "run-gated-suites.out-dir-missing"),
        ([], "3x", "config_loader.testing.invalid_workers"),
    ],
)
def test_refusals_start_no_suite(args: list[str], workers: str, key: str, tmp_path: pathlib.Path) -> None:
    tree = _StubTree(tmp_path, workers)

    result = tree.run(*args)

    assert result.returncode == 1, _describe(result)
    assert key in result.stderr, _describe(result)
    assert not any(tree.stub_dir.iterdir()), "a runner started despite the refusal"


def test_missing_runner_is_refused_before_either_suite_starts(tmp_path: pathlib.Path) -> None:
    tree = _StubTree(tmp_path, "3")
    (tree.scripts / "run-integration-tests.sh").unlink()

    result = tree.run()

    assert result.returncode == 1, _describe(result)
    assert "run-gated-suites.runner-missing" in result.stderr, _describe(result)
    assert not tree.marker("unit.started")


# ---------------------------------------------------------------------------
# The real entry point over the real runners, against fixture suites
# ---------------------------------------------------------------------------

_PASSING_TEST = "def test_fixture_passes():\n    assert True\n"
_FAILING_TEST = "def test_fixture_integration_fails():\n    assert False, 'integration fixture failure'\n"


def _both_runner_fixture(tmp_path: pathlib.Path, *, integration_fails: bool) -> pathlib.Path:
    """One fixture tree holding the real entry point, both real runners and
    the real lib/, plus a one-test suite per runner."""
    team = _build_fixture(tmp_path, "run-unit-tests.sh")
    scripts = team / "scripts"
    shutil.copy2(_SCRIPTS_DIR / "run-integration-tests.sh", scripts / "run-integration-tests.sh")
    shutil.copy2(_SCRIPTS_DIR / _ENTRY_POINT, scripts / _ENTRY_POINT)
    shutil.copy2(_PYPROJECT, team / "pyproject.toml")
    (team / "tests" / "unit" / "test_other.py").unlink()
    integration = team / "tests" / "integration"
    integration.mkdir(parents=True)
    (integration / "test_fixture_integration.py").write_text(
        _FAILING_TEST if integration_fails else _PASSING_TEST, encoding="utf-8"
    )
    return team


class _RealSandbox:
    def __init__(self, tmp_path: pathlib.Path, name: str, workers: str) -> None:
        base = tmp_path / name
        self.kanban_root = base / "kanban_root"
        self.temp_dir = base / "temp_root"
        self.home = base / "home"
        self.gate_log = base / "gate.log"
        for d in (self.kanban_root, self.temp_dir, self.home):
            d.mkdir(parents=True)
        self.gate_log.touch()
        (self.kanban_root / "kanban.cfg").write_text(f"[testing]\nworkers = {workers}\n", encoding="utf-8")

    def run(self, team: pathlib.Path, script: str, *args: str) -> subprocess.CompletedProcess:
        env = build_scrubbed_env(
            home_dir=self.home,
            extra_path_dirs=[pathlib.Path(sys.executable).parent],
            extra_env={
                "PGAI_AGENT_KANBAN_ROOT_PATH": str(self.kanban_root),
                "KANBAN_ROOT": str(self.kanban_root),
                "PGAI_AGENT_KANBAN_TEMP_DIR": str(self.temp_dir),
                "GATE_LOG": str(self.gate_log),
            },
        )
        return _run_owned(["bash", str(team / "scripts" / script), *args], env, team.parent, 300)

    def findings(self, suite: str) -> dict:
        return json.loads((self.temp_dir / "lint" / f"findings-{suite}.json").read_text(encoding="utf-8"))


def _shape(document: dict) -> tuple:
    """What a findings file says, without the per-run values (timestamps,
    run tokens, paths under a sandbox)."""
    findings = document.get("findings") or []
    return (
        sorted(document),
        document.get("runner"),
        sorted((f.get("lint"), f.get("severity")) for f in findings if isinstance(f, dict)),
    )


@pytest.mark.serial
def test_real_runners_write_the_same_evidence_through_the_entry_point(tmp_path: pathlib.Path) -> None:
    """Runs both suites at once (workers = 2: one worker each, no xdist),
    hence the serial mark. The separate runners, run in a second sandbox
    against the same fixture tree, are the reference."""
    team = _both_runner_fixture(tmp_path, integration_fails=False)
    via_entry = _RealSandbox(tmp_path, "entry", "2")
    separate = _RealSandbox(tmp_path, "separate", "2")

    result = via_entry.run(team, _ENTRY_POINT)
    reference = {suite: separate.run(team, _RUNNERS[suite]) for suite in _SUITES}

    assert result.returncode == 0, _describe(result)
    assert "running unit (--workers 1) and integration (--workers 1) at the same time" in result.stdout
    for suite in _SUITES:
        assert reference[suite].returncode == 0, _describe(reference[suite])
        exit_file = via_entry.temp_dir / f"{suite}.exit"
        assert exit_file.read_text(encoding="utf-8") == "0\n"
        assert _shape(via_entry.findings(suite)) == _shape(separate.findings(suite))
        log = (via_entry.temp_dir / f"{suite}.log").read_text(encoding="utf-8")
        findings_line = f"[gated-runner] findings file written: {via_entry.temp_dir}/lint/findings-{suite}.json"
        assert findings_line in log, log
        assert "Replay this exact run:" in log, log
        assert "Worker budget: 1 for this run (--workers 1; [testing] workers = 2)." in log, log
        assert "left to the caller (--no-scratch-sweep)" in log, log
        # The separate run's own stdout carries the same runner lines.
        assert f"findings file written: {separate.temp_dir}/lint/findings-{suite}.json" in reference[suite].stdout


@pytest.mark.serial
def test_one_failing_integration_test_gives_integration_one_unit_zero_and_a_nonzero_run(
    tmp_path: pathlib.Path,
) -> None:
    """Runs both suites at once (workers = 2), hence the serial mark."""
    team = _both_runner_fixture(tmp_path, integration_fails=True)
    sandbox = _RealSandbox(tmp_path, "entry", "2")

    result = sandbox.run(team, _ENTRY_POINT)

    assert result.returncode != 0, _describe(result)
    assert (sandbox.temp_dir / "unit.exit").read_text(encoding="utf-8") == "0\n"
    assert (sandbox.temp_dir / "integration.exit").read_text(encoding="utf-8") == "1\n"
    assert "Gated suites: unit exit 0, integration exit 1 -> exit 1" in result.stdout, _describe(result)
    integration_log = (sandbox.temp_dir / "integration.log").read_text(encoding="utf-8")
    assert "test_fixture_integration_fails" in integration_log, integration_log
    for suite in _SUITES:
        assert sandbox.findings(suite)["runner"] == _RUNNERS[suite]


def test_real_runner_caps_a_workers_flag_above_the_configured_budget(tmp_path: pathlib.Path) -> None:
    """The guard: --workers can never raise the budget. workers = 1 is
    configured, --workers 5 is asked for, and the run stays single-process."""
    team = _both_runner_fixture(tmp_path, integration_fails=False)
    sandbox = _RealSandbox(tmp_path, "runner", "1")

    result = sandbox.run(team, "run-unit-tests.sh", "--workers", "5")

    assert result.returncode == 0, _describe(result)
    assert "--workers 5 is above [testing] workers = 1; this run uses 1" in result.stdout, _describe(result)
    replays = [line for line in result.stdout.splitlines() if line.startswith("Replay this exact run: ")]
    assert len(replays) == 1 and not re.search(r"\s-n\s", replays[0]), replays


@pytest.mark.parametrize("runner_name", ["run-unit-tests.sh", "run-integration-tests.sh"])
def test_real_runner_refuses_a_non_numeric_workers_flag(runner_name: str, tmp_path: pathlib.Path) -> None:
    team = _both_runner_fixture(tmp_path, integration_fails=False)
    sandbox = _RealSandbox(tmp_path, "runner", "3")

    result = sandbox.run(team, runner_name, "--workers", "two")

    assert result.returncode == 1, _describe(result)
    suite = runner_name.removeprefix("run-").removesuffix("-tests.sh")
    assert f"run-{suite}-tests.workers-flag-invalid" in result.stderr, _describe(result)
    assert "Replay this exact run:" not in result.stdout, "pytest must not start on a refused --workers"


@pytest.mark.parametrize("runner_name", ["run-unit-tests.sh", "run-integration-tests.sh"])
def test_real_runner_help_documents_the_workers_and_scratch_sweep_flags(runner_name: str, tmp_path: pathlib.Path) -> None:
    team = _both_runner_fixture(tmp_path, integration_fails=False)
    sandbox = _RealSandbox(tmp_path, "runner", "3")

    result = sandbox.run(team, runner_name, "--help")

    assert result.returncode == 0, _describe(result)
    for text in ("--workers N", "never", "--no-scratch-sweep", "run-gated-suites.sh"):
        assert text in result.stdout, f"--help does not mention {text!r}\n" + _describe(result)
