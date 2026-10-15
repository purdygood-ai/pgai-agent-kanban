#!/usr/bin/env python3
"""
gated_runner_lanes.py -- the pytest lane plan and coverage gate for the
gated test runners (run-unit-tests.sh, run-integration-tests.sh).

Each runner assembles the pytest argv it has always run (test roots, the
ordering policy, the ``-m`` marker selection, ``--verbose``) and hands it to
``plan`` below together with the run's shape. The plan answers two
questions: is coverage collected, and which pytest invocations ("lanes")
run, with which argv. The runner executes the lanes one after the other
and combines their exit codes (gated_runner_lanes.sh).

LANES
-----
A full certification run with ``[testing] workers`` above 1 and
pytest-xdist importable runs two lanes, in this order:

    parallel  <argv, -m "<selection> and not serial"> -n <workers> --dist loadfile
    serial    <argv, -m "<selection> and serial">

Every other run is one lane whose argv is exactly the argv the runner
handed in:

    single    <argv>

The single-lane cases are: workers 0 or 1; pytest-xdist not importable
(one narrated line, never a failure); a ``--files`` targeted run (a
handful of files gains nothing from loadfile distribution and pays the
worker start-up cost); and a ``--quarantined`` run (its tests are
quarantined precisely because their verdict depends on order or timing).

The worker bound: only the parallel lane carries ``-n``, its value is
exactly ``workers``, and the lanes run sequentially, so a run never has
more than ``workers`` pytest worker processes alive at once.

A runner given ``--workers N`` (``--requested-workers N`` here) runs with
the smaller of N and ``[testing] workers``: the flag can lower the budget
for one run, never raise it. run-gated-suites.sh uses it to split one
budget between two suites running at the same time. A targeted or
quarantined run ignores it, as it ignores ``workers``.

The ``serial`` marker (registered in team/pyproject.toml) marks a test that
is not safe to run beside another test in a different process -- a fixed
port, a tmux server name, a shared file outside the test's own tmp dir.

COVERAGE
--------
``--coverage`` is honoured only when ``[testing] coverage = true``, or when
``PGAI_TESTING_COVERAGE=1`` is set in the invoking environment outside an
agent wake (``PGAI_AGENT_WAKE_ACTIVE`` is not ``1``). Otherwise the plan
narrates ``coverage disabled by config ([testing] coverage)`` and every
lane runs without coverage; the run's exit code is unaffected either way.
When coverage is on and pytest-cov is not importable, the plan warns and
runs without it, as the runners always have. When coverage is on, every
lane carries the coverage options and every lane after the first adds
``--cov-append``, so the report the last lane writes covers the whole run.

DURATIONS
---------
Given ``--durations-dir DIR``, a certification run (neither ``--targeted``
nor ``--quarantined``) records its slowest tests: every lane carries
``--durations=25 --durations-min=1.0`` and ``--junitxml=DIR/<suite>-<lane>.xml``
(gated_runner_durations.py states the options and thresholds), and the plan
names DIR so the runner can merge the lane reports into
``durations-<suite>.json`` after the last lane. Informational only: it never
changes a lane's exit code. A targeted or quarantined run records nothing.

CLI
---
    python3 gated_runner_lanes.py plan --suite unit|integration
        [--workers N] [--requested-workers N] [--targeted] [--quarantined]
        [--coverage-requested] [--coverage-config true|false]
        [--coverage-dir DIR] [--durations-dir DIR] -- <pytest argv...>

Writes NUL-terminated records on stdout, read by gated_runner_plan_lanes
in gated_runner_lanes.sh:

    say <line>                   a narration line for stdout
    warn <line>                  a narration line for stderr
    coverage-dir <dir>           coverage is on; the runner creates <dir>
    durations-dir <dir>          durations are recorded; the runner creates <dir>
    lane <name> <argc> <arg>...  one pytest invocation, in run order

Exits 0 with a plan, 2 on a usage error (argparse).
"""

from __future__ import annotations

import argparse
import importlib
import os
import re
import sys
from dataclasses import dataclass, field
from typing import Mapping, Sequence

# The durations options live beside this file; put this file's own directory
# on sys.path so they import whether this module runs as a script or is
# loaded from its path.
_LIB_DIR = os.path.dirname(os.path.abspath(__file__))
if _LIB_DIR not in sys.path:
    sys.path.insert(0, _LIB_DIR)

from gated_runner_durations import lane_args as durations_lane_args  # noqa: E402

# The marker name a test carries when it is not parallel-safe.
SERIAL_MARKER = "serial"

# The environment variable an operator sets (=1) in their own shell to
# collect coverage for one run while [testing] coverage is false. The wake
# bootstrap unsets it (gated_runner_coverage_scrub_env), and this module
# ignores it whenever the agent-wake signal below is set.
COVERAGE_ENV_VAR = "PGAI_TESTING_COVERAGE"
AGENT_WAKE_ENV_VAR = "PGAI_AGENT_WAKE_ACTIVE"

# Narration printed when --coverage is requested but not allowed.
COVERAGE_DISABLED_LINE = "coverage disabled by config ([testing] coverage)"

# The source trees coverage measures, relative to the team/ directory the
# runners run pytest from. Mirrors [tool.coverage.run] source in
# team/pyproject.toml.
COVERAGE_SOURCES = ("pgai_agent_kanban", "pm-agent", "scripts/lib")

_XDIST_INSTALL_HINT = "python3 -m pip install 'pytest-xdist>=3.8'"


@dataclass(frozen=True)
class CoverageDecision:
    """Whether a run collects coverage, and the line that says why not."""

    enabled: bool
    narration: str | None = None


@dataclass
class Lane:
    """One pytest invocation: a name for the narration and its argv."""

    name: str
    argv: list[str]


@dataclass
class LanePlan:
    """The lanes a run executes, in order, plus what to narrate first."""

    lanes: list[Lane]
    say: list[str] = field(default_factory=list)
    warn: list[str] = field(default_factory=list)
    coverage_dir: str | None = None
    durations_dir: str | None = None


def module_importable(name: str) -> bool:
    """True when ``import <name>`` succeeds in this interpreter.

    The import itself is the test, not a spec lookup: a package that is
    present but fails to import is not usable by pytest either.
    """
    try:
        importlib.import_module(name)
    except Exception:  # noqa: BLE001 -- any import failure means "not usable"
        return False
    return True


def coverage_decision(
    requested: bool, config_enabled: bool, environ: Mapping[str, str]
) -> CoverageDecision:
    """Decide whether a ``--coverage`` request is honoured.

    Honoured when ``[testing] coverage`` is true, or when the operator set
    PGAI_TESTING_COVERAGE=1 outside an agent wake. A request that is not
    honoured is narrated; no request is no coverage and no narration.
    """
    if not requested:
        return CoverageDecision(enabled=False)
    if config_enabled:
        return CoverageDecision(enabled=True, narration="coverage enabled by config ([testing] coverage = true)")
    env_on = environ.get(COVERAGE_ENV_VAR, "") == "1"
    in_wake = environ.get(AGENT_WAKE_ENV_VAR, "") == "1"
    if env_on and not in_wake:
        return CoverageDecision(
            enabled=True,
            narration=f"coverage enabled for this run by {COVERAGE_ENV_VAR}=1 ([testing] coverage is false)",
        )
    if env_on and in_wake:
        return CoverageDecision(
            enabled=False,
            narration=(
                f"{COVERAGE_DISABLED_LINE}; {COVERAGE_ENV_VAR} has no effect inside an agent wake "
                f"({AGENT_WAKE_ENV_VAR}=1)"
            ),
        )
    return CoverageDecision(enabled=False, narration=COVERAGE_DISABLED_LINE)


def coverage_args(suite: str, coverage_dir: str) -> list[str]:
    """The pytest-cov options a coverage run passes, JSON report per suite."""
    return [
        *(f"--cov={source}" for source in COVERAGE_SOURCES),
        "--cov-branch",
        "--cov-report=term",
        f"--cov-report=json:{coverage_dir}/{suite}.json",
    ]


def lane_selection(selection: str, serial: bool) -> str:
    """Compose a lane's -m expression from the run's own selection.

    ``not`` binds tighter than ``and`` in pytest's marker grammar, so a
    selection without ``or`` composes as-is; one with ``or`` is
    parenthesized first.
    """
    term = SERIAL_MARKER if serial else f"not {SERIAL_MARKER}"
    selection = selection.strip()
    if not selection:
        return term
    if re.search(r"\bor\b", selection):
        selection = f"({selection})"
    return f"{selection} and {term}"


def _with_selection(argv: Sequence[str], selection: str) -> list[str]:
    """Return *argv* with the value after its last ``-m`` replaced by
    *selection*, or with ``-m <selection>`` appended when it has none."""
    out = list(argv)
    for index in range(len(out) - 1, -1, -1):
        if out[index] == "-m" and index + 1 < len(out):
            out[index + 1] = selection
            return out
    return [*out, "-m", selection]


def _selection_of(argv: Sequence[str]) -> str:
    """The value after the last ``-m`` in *argv*, or "" when there is none."""
    for index in range(len(argv) - 1, -1, -1):
        if argv[index] == "-m" and index + 1 < len(argv):
            return argv[index + 1]
    return ""


def worker_budget(configured: int, requested: int | None) -> tuple[int, str, str | None]:
    """Resolve one run's worker budget from ``[testing] workers`` and a
    runner's ``--workers`` request.

    Returns ``(workers, source, narration)``: the budget the run uses, the
    setting it came from written as a prefix for the number ("[testing]
    workers = " or "--workers ", for the "Parallel workers:" line),
    and one line to narrate, or None when no ``--workers`` was given. The
    request can only lower the budget: a value above *configured* is
    narrated and the configured budget is used instead.
    """
    if requested is None:
        return configured, "[testing] workers = ", None
    if requested <= configured:
        return (
            requested,
            "--workers ",
            f"Worker budget: {requested} for this run (--workers {requested}; [testing] workers = {configured}).",
        )
    return (
        configured,
        "[testing] workers = ",
        f"Worker budget: --workers {requested} is above [testing] workers = {configured}; "
        f"this run uses {configured}, the configured budget.",
    )


def plan_lanes(
    argv: Sequence[str],
    *,
    suite: str,
    workers: int,
    requested_workers: int | None = None,
    targeted: bool = False,
    quarantined: bool = False,
    coverage: CoverageDecision = CoverageDecision(enabled=False),
    coverage_dir: str = "",
    xdist_available: bool | None = None,
    pytest_cov_available: bool | None = None,
    durations_dir: str = "",
) -> LanePlan:
    """Build the lane plan for one runner invocation.

    *argv* is the pytest argv the runner would run as a single process.
    *workers* is ``[testing] workers``; *requested_workers* is the runner's
    ``--workers`` value, or None (see worker_budget).
    *xdist_available* / *pytest_cov_available* default to probing this
    interpreter; tests pass them explicitly. The probes run only when their
    answer can change the plan.
    *durations_dir*, when set, records a certification run's slowest tests
    (see DURATIONS in the module docstring); a targeted or quarantined run
    ignores it.
    """
    plan = LanePlan(lanes=[])
    record_durations = bool(durations_dir) and not targeted and not quarantined
    if record_durations:
        plan.durations_dir = durations_dir

    def durations(lane_name: str) -> list[str]:
        if not record_durations:
            return []
        return durations_lane_args(f"{durations_dir}/{suite}-{lane_name}.xml")

    if coverage.narration:
        plan.say.append(coverage.narration)

    cov: list[str] = []
    if coverage.enabled:
        if pytest_cov_available is None:
            pytest_cov_available = module_importable("pytest_cov")
        if pytest_cov_available:
            cov = coverage_args(suite, coverage_dir)
            plan.coverage_dir = coverage_dir
        else:
            plan.warn.append("[coverage] pytest-cov not installed — skipping coverage")

    source = "[testing] workers = "
    if not targeted and not quarantined:
        workers, source, budget_line = worker_budget(workers, requested_workers)
        if budget_line:
            plan.say.append(budget_line)

    single_reason = None
    if targeted:
        single_reason = "none for a --files targeted run; one single-process pytest run"
    elif quarantined:
        single_reason = "none for a --quarantined run; one single-process pytest run"
    elif workers <= 1:
        single_reason = f"none ({source}{workers}); one single-process pytest run"
    else:
        if xdist_available is None:
            xdist_available = module_importable("xdist")
        if not xdist_available:
            single_reason = (
                f"none -- {source}{workers}, but pytest-xdist is not importable "
                f"in this Python ({_XDIST_INSTALL_HINT}); running serially in one process"
            )

    if single_reason is not None:
        plan.say.append(f"Parallel workers: {single_reason}.")
        plan.lanes.append(Lane(name="single", argv=[*argv, *cov, *durations("single")]))
        return plan

    selection = _selection_of(argv)
    plan.say.append(
        f"Parallel workers: {workers} (pytest-xdist -n {workers} --dist loadfile) for tests not marked "
        f"'{SERIAL_MARKER}', then the '{SERIAL_MARKER}'-marked tests in one process."
    )
    plan.lanes.append(
        Lane(
            name="parallel",
            argv=[
                *_with_selection(argv, lane_selection(selection, serial=False)),
                "-n",
                str(workers),
                "--dist",
                "loadfile",
                *cov,
                *durations("parallel"),
            ],
        )
    )
    plan.lanes.append(
        Lane(
            name="serial",
            argv=[
                *_with_selection(argv, lane_selection(selection, serial=True)),
                *cov,
                *(["--cov-append"] if cov else []),
                *durations("serial"),
            ],
        )
    )
    return plan


def encode_plan(plan: LanePlan) -> str:
    """Render *plan* as the NUL-terminated record stream the shell reads."""
    tokens: list[str] = []
    for line in plan.say:
        tokens += ["say", line]
    for line in plan.warn:
        tokens += ["warn", line]
    if plan.coverage_dir:
        tokens += ["coverage-dir", plan.coverage_dir]
    if plan.durations_dir:
        tokens += ["durations-dir", plan.durations_dir]
    for lane in plan.lanes:
        tokens += ["lane", lane.name, str(len(lane.argv)), *lane.argv]
    return "".join(f"{token}\0" for token in tokens)


def _non_negative_int(text: str) -> int:
    value = int(text)
    if value < 0:
        raise argparse.ArgumentTypeError(f"expected a non-negative whole number, got {text!r}")
    return value


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="gated_runner_lanes.py",
        description="Plan the pytest lanes and the coverage decision for a gated test runner.",
    )
    sub = parser.add_subparsers(dest="command", required=True)
    plan = sub.add_parser(
        "plan",
        help="print the lane plan as NUL-terminated records",
        description=(
            "Print the lane plan for one runner invocation as NUL-terminated records "
            "(say, warn, coverage-dir, lane). Everything after -- is the pytest argv "
            "the runner would run as a single process."
        ),
    )
    plan.add_argument("--suite", required=True, choices=("unit", "integration"), help="suite name; names the coverage JSON report")
    plan.add_argument("--workers", type=_non_negative_int, default=1, help="[testing] workers (0 or 1 = single process)")
    plan.add_argument(
        "--requested-workers",
        type=_non_negative_int,
        default=None,
        help="the runner's --workers value; lowers the budget for this run, never raises it",
    )
    plan.add_argument("--targeted", action="store_true", help="a --files targeted run (always single process)")
    plan.add_argument("--quarantined", action="store_true", help="a --quarantined run (always single process)")
    plan.add_argument("--coverage-requested", action="store_true", help="the runner was given --coverage")
    plan.add_argument("--coverage-config", choices=("true", "false"), default="false", help="[testing] coverage")
    plan.add_argument("--coverage-dir", default="", help="directory the coverage JSON report is written into")
    plan.add_argument(
        "--durations-dir",
        default="",
        help="directory the lanes' timing reports are written into; a certification run records "
             "its slowest tests there (a targeted or quarantined run ignores it)",
    )
    plan.add_argument("pytest_argv", nargs=argparse.REMAINDER, help="-- followed by the pytest argv")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    """Run the lane planner CLI: ``plan --suite SUITE [...] -- PYTEST_ARGV``.

    Writes the lane plan to stdout as the record stream the runner reads.
    Exits 0 with a plan; 2 on a usage error (argparse).
    """
    args = _build_parser().parse_args(argv)
    pytest_argv = list(args.pytest_argv)
    if pytest_argv[:1] == ["--"]:
        pytest_argv = pytest_argv[1:]
    decision = coverage_decision(args.coverage_requested, args.coverage_config == "true", os.environ)
    plan = plan_lanes(
        pytest_argv,
        suite=args.suite,
        workers=args.workers,
        requested_workers=args.requested_workers,
        targeted=args.targeted,
        quarantined=args.quarantined,
        coverage=decision,
        coverage_dir=args.coverage_dir,
        durations_dir=args.durations_dir,
    )
    sys.stdout.write(encode_plan(plan))
    sys.stdout.flush()
    return 0


if __name__ == "__main__":
    sys.exit(main())
