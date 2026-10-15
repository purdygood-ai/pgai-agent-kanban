#!/usr/bin/env python3
"""
gated_suites.py -- the run plan for run-gated-suites.sh, which runs the
unit and integration gated suites (run-unit-tests.sh,
run-integration-tests.sh) from one command.

Splitting the worker budget
---------------------------
``[testing] workers`` (kanban.cfg, read by the entry point through
config_testing_workers) is the most pytest workers the two suites may have
alive between them.

    workers >= 2   concurrent: both suites start together. The unit suite
                   gets ``--workers <workers - 1>`` and the integration
                   suite ``--workers 1``. Each suite gets at least 1 and the
                   two add up to exactly ``workers``.
    workers 0, 1   sequential: the unit suite runs, then the integration
                   suite. Each gets ``--workers <workers>``, so it runs
                   single-process exactly as it does on its own. Two
                   suites cannot each have a worker without going over a
                   budget of 1, so below 2 they take turns.

A runner given ``--workers N`` runs with the smaller of N and ``[testing]
workers`` (gated_runner_lanes.py worker_budget), so neither child can go
over the budget it was handed, whatever the planner says.

Running together safely
-----------------------
In concurrent mode each child also gets:

- ``--no-scratch-sweep``. After a green run, a runner removes
  ``${PGAI_AGENT_KANBAN_TEMP_DIR}/tests``. The other suite may still be
  using that scratch, so the entry point sweeps once, after both suites
  exit 0.
- with ``--coverage``, a ``COVERAGE_FILE`` of its own under
  ``<coverage-root>/<suite>-data/``. Both runners start pytest from the
  same team/ directory, and pytest-cov combines every coverage data file
  it finds beside its own. Shared data files would mix the two suites'
  coverage. The JSON reports (``<coverage-root>/<suite>.json``) are
  unchanged.

Sequential mode passes neither: each runner runs exactly as it does when
started by hand.

Flags common to both runners (``--coverage``, ``--verbose``,
``--reuse-if-green``) pass through to each child unchanged. Coverage is
still decided inside each runner by its own gate, and nothing here can
turn it on.

Exit codes
----------
combine_exits: 0 when both suites exited 0. Otherwise the first non-zero
code in suite order (unit, then integration).

CLI
---
    python3 gated_suites.py plan --workers N [--coverage] [--verbose]
        [--reuse-if-green] [--coverage-root DIR]
    python3 gated_suites.py combine CODE [CODE ...]

``plan`` writes NUL-terminated records on stdout, read by
run-gated-suites.sh:

    mode concurrent|sequential
    say <line>                        a narration line for stdout
    dir <path>                        a directory to create before starting
    sweep-after                       sweep <temp-root>/tests after both pass
    run <suite> <runner> <envc> <NAME=value>... <argc> <arg>...
                                      one child run, in start order

``combine`` prints the combined exit code. Both exit 0, or 2 on a usage
error (argparse).
"""

from __future__ import annotations

import argparse
import sys
from dataclasses import dataclass, field
from typing import Sequence

# (suite, runner basename) in start order. The runners sit beside
# run-gated-suites.sh in the same scripts/ directory.
SUITES: tuple[tuple[str, str], ...] = (
    ("unit", "run-unit-tests.sh"),
    ("integration", "run-integration-tests.sh"),
)

CONCURRENT = "concurrent"
SEQUENTIAL = "sequential"

# The smallest budget that gives each suite a worker of its own.
MIN_CONCURRENT_WORKERS = len(SUITES)


@dataclass(frozen=True)
class SuiteRun:
    """One child runner invocation: its suite, runner, flags and extra env."""

    suite: str
    runner: str
    argv: list[str]
    env: dict[str, str] = field(default_factory=dict)


@dataclass
class SuitesPlan:
    """How the two suites run, in start order, plus what to narrate."""

    mode: str
    runs: list[SuiteRun]
    say: list[str] = field(default_factory=list)
    dirs: list[str] = field(default_factory=list)
    sweep_after: bool = False


def split_workers(workers: int) -> dict[str, int] | None:
    """Each suite's share of *workers* when the suites can run together.

    Returns None when *workers* is below MIN_CONCURRENT_WORKERS, where the
    suites run one after the other instead. Otherwise the integration suite
    gets 1 and the unit suite the rest, so the shares sum to *workers*.
    """
    if workers < MIN_CONCURRENT_WORKERS:
        return None
    return {"unit": workers - 1, "integration": 1}


def _passthrough(coverage: bool, verbose: bool, reuse_if_green: bool) -> list[str]:
    flags: list[str] = []
    if coverage:
        flags.append("--coverage")
    if verbose:
        flags.append("--verbose")
    if reuse_if_green:
        flags.append("--reuse-if-green")
    return flags


def plan_suites(
    workers: int,
    *,
    coverage: bool = False,
    verbose: bool = False,
    reuse_if_green: bool = False,
    coverage_root: str = "",
) -> SuitesPlan:
    """Build the run plan for one run-gated-suites.sh invocation.

    *workers* is ``[testing] workers``. *coverage_root* is the directory
    the runners write their coverage JSON into; concurrent coverage runs
    keep their data files in a subdirectory of it per suite.
    """
    if workers < 0:
        raise ValueError(f"workers must be a non-negative whole number, got {workers}")
    passthrough = _passthrough(coverage, verbose, reuse_if_green)
    shares = split_workers(workers)

    if shares is None:
        plan = SuitesPlan(mode=SEQUENTIAL, runs=[])
        plan.say.append(
            f"Gated suites: [testing] workers = {workers} is below {MIN_CONCURRENT_WORKERS}, too few to give "
            f"each suite a worker of its own; running "
            + ", then ".join(suite for suite, _ in SUITES)
            + ", one after the other."
        )
        for suite, runner in SUITES:
            plan.runs.append(SuiteRun(suite=suite, runner=runner, argv=["--workers", str(workers), *passthrough]))
        return plan

    plan = SuitesPlan(mode=CONCURRENT, runs=[], sweep_after=True)
    plan.say.append(
        f"Gated suites: [testing] workers = {workers}; running "
        + " and ".join(f"{suite} (--workers {shares[suite]})" for suite, _ in SUITES)
        + f" at the same time -- {sum(shares.values())} workers in total."
    )
    for suite, runner in SUITES:
        env: dict[str, str] = {}
        if coverage and coverage_root:
            data_dir = f"{coverage_root.rstrip('/')}/{suite}-data"
            plan.dirs.append(data_dir)
            env["COVERAGE_FILE"] = f"{data_dir}/.coverage"
        plan.runs.append(
            SuiteRun(
                suite=suite,
                runner=runner,
                argv=["--workers", str(shares[suite]), "--no-scratch-sweep", *passthrough],
                env=env,
            )
        )
    return plan


def requested_workers(run: SuiteRun) -> int:
    """The ``--workers`` value in *run*'s argv (the last one wins, as in the
    runners' own parse loop). Raises ValueError when there is none."""
    argv = run.argv
    for index in range(len(argv) - 2, -1, -1):
        if argv[index] == "--workers":
            return int(argv[index + 1])
    raise ValueError(f"{run.runner} argv carries no --workers: {argv}")


def peak_workers(plan: SuitesPlan) -> int:
    """The most workers the plan can have alive at once: the sum of the
    children's ``--workers`` when they run together, the largest single
    one when they take turns."""
    counts = [requested_workers(run) for run in plan.runs]
    if plan.mode == CONCURRENT:
        return sum(counts)
    return max(counts, default=0)


def combine_exits(codes: Sequence[int]) -> int:
    """The entry point's exit code: 0 when every suite exited 0, otherwise
    the first non-zero code in suite order."""
    for code in codes:
        if code != 0:
            return code
    return 0


def encode_plan(plan: SuitesPlan) -> str:
    """Render *plan* as the NUL-terminated record stream the shell reads."""
    tokens: list[str] = ["mode", plan.mode]
    for line in plan.say:
        tokens += ["say", line]
    for path in plan.dirs:
        tokens += ["dir", path]
    if plan.sweep_after:
        tokens.append("sweep-after")
    for run in plan.runs:
        env_pairs = [f"{name}={value}" for name, value in run.env.items()]
        tokens += ["run", run.suite, run.runner, str(len(env_pairs)), *env_pairs, str(len(run.argv)), *run.argv]
    return "".join(f"{token}\0" for token in tokens)


def _non_negative_int(text: str) -> int:
    try:
        value = int(text, 10)
    except ValueError:
        raise argparse.ArgumentTypeError(f"expected a non-negative whole number, got {text!r}") from None
    if value < 0:
        raise argparse.ArgumentTypeError(f"expected a non-negative whole number, got {text!r}")
    return value


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="gated_suites.py",
        description="Plan how run-gated-suites.sh runs the unit and integration gated suites.",
    )
    sub = parser.add_subparsers(dest="command", required=True)
    plan = sub.add_parser(
        "plan",
        help="print the run plan as NUL-terminated records",
        description=(
            "Print the run plan as NUL-terminated records (mode, say, dir, sweep-after, run): "
            "concurrent with the worker budget split when workers >= 2, sequential below."
        ),
    )
    plan.add_argument("--workers", type=_non_negative_int, required=True, help="[testing] workers")
    plan.add_argument("--coverage", action="store_true", help="pass --coverage to both runners")
    plan.add_argument("--verbose", action="store_true", help="pass --verbose to both runners")
    plan.add_argument("--reuse-if-green", action="store_true", help="pass --reuse-if-green to both runners")
    plan.add_argument(
        "--coverage-root",
        default="",
        help="the runners' coverage directory; concurrent coverage runs keep per-suite data files under it",
    )
    combine = sub.add_parser("combine", help="print the combined exit code of the suites, in suite order")
    combine.add_argument("codes", nargs="+", type=_non_negative_int, help="each suite's exit code, in suite order")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    """Run the suites planner CLI.

    ``plan --workers N [...]`` writes the two-suite plan to stdout as the
    record stream the entry point reads; ``combine CODE...`` prints the
    combined exit code (0 when every suite exited 0, else the first non-zero
    in suite order). Exits 0; 2 on a usage error (argparse).
    """
    args = _build_parser().parse_args(argv)
    if args.command == "combine":
        print(combine_exits(args.codes))
        return 0
    plan = plan_suites(
        args.workers,
        coverage=args.coverage,
        verbose=args.verbose,
        reuse_if_green=args.reuse_if_green,
        coverage_root=args.coverage_root,
    )
    sys.stdout.write(encode_plan(plan))
    sys.stdout.flush()
    return 0


if __name__ == "__main__":
    sys.exit(main())
