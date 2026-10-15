#!/usr/bin/env python3
"""
pre_merge.py -- the pre-merge check run: the cheap whole-tree checks a
change should pass before it merges, run from a worktree in one command.

A targeted ``--files`` run checks only the tests a change names. Some slips
show up only across the whole tree: a lint allowance exceeded, a new
package module that crashes when started by file path, a generated document
left stale, a module reading the kanban root around its resolver, or an
untouched test that still asserts behaviour the change altered. This module
runs exactly those checks and prints one summary block. The gated runners
call it for ``--pre-merge``. It also runs on its own, which is useful for
debugging.

What runs
---------
With ``--suite unit`` (the default), in this order:

1. **by-path** -- ``tests/unit/test_package_modules_run_by_path.py``, the
   by-path module test over the whole ``pgai_agent_kanban`` package: every
   package module with a ``__main__`` block runs by file path with no
   PYTHONPATH.
2. **The five ratchet lints, in fail-on-regression mode.** Each lint is run
   exactly as the gated runners run it, plus a findings file of this run's
   own:

   ``inline-python``      ``team/scripts/lint_inline_python.py``
   ``python-duplicates``  ``team/scripts/lint_python_duplicates.py``
   ``engine-size``        ``team/scripts/lint_engine_size_dupe.py``
   ``api-route-impl``     ``team/scripts/lint_api_route_impl.py``
   ``echo-pipe-grep-q``   ``team/scripts/lint_echo_pipe_grep_q.py``

   The lints themselves are unchanged and keep their quality class: they
   exit 0 whatever they find (``lint_engine_size_dupe.py`` reports through
   its exit code instead, as it always has). This run judges what they
   report. Any finding fails the check: a file over its recorded allowance,
   an unlisted new file, an allowlist entry left above the measured count
   (the ratchet's "tighten in the same change"), a malformed allowlist line,
   an engine file over its line ceiling, a cross-file shell function
   collision, or an ``echo``/``printf`` piped into ``grep -q`` (a pipeline
   that can miss a match under ``pipefail``). Each finding's own summary
   line is printed, so the output names the file and the allowance it
   broke.

   After them runs ``tracked-ignored`` (``team/scripts/lint_tracked_ignored.py``),
   judged the same way: a tracked file an ignore rule matches, or a tracked
   generated file (a log, a lock, coverage output), fails the check. It is
   not a ratchet -- it keeps no allowlist -- and like the five it exits 0
   whatever it finds on a certification run.
3. **The four always-run whole-tree guards** (:data:`WHOLE_TREE_GUARDS`).
   Each checks the real tree for a property any change can break, so each
   runs whatever the diff: a test of such a property names none of the
   files it guards, and affected-test selection would never pick it. Each
   guard is the script the gated runners' quality-class block already runs,
   run the same way; the script itself is unchanged and keeps its class on
   a certification run. Here, exit 0 passes the guard, and anything else
   fails it, with the guard's own output and its fix printed beneath its
   row:

   ``guard: env-bootstrap``          ``team/scripts/lint_env_bootstrap.py``
       every Python module reads the kanban root through
       ``resolve_kanban_root()``, and every bash entry point that reads
       it sources the bootstrap prelude
   ``guard: operator-verb-docs``     ``team/scripts/generate_operator_verb_docs.py --check``
       the generated verb and route references in
       ``docs/operator-commands.md`` and ``docs/operator-api.md`` match a
       fresh render from the verb census and each verb's own ``--help``
   ``guard: dashboard-window-docs``  ``team/scripts/dashboard/generate_window_docs.py --check``
       the dashboard window surfaces match a fresh render from
       ``team/config/dashboard_windows.census``
   ``guard: docs-citations``         ``team/scripts/check_docs_citations.py``
       every ``docs/`` page an installed governance file cites is named
       in ``team/config/docs-manifest.txt``

   The guards' added wall time is reported in the summary block against
   :data:`GUARD_BUDGET_SECONDS` (5.0 seconds; the four measured about 1.9
   seconds together on the kanban's own tree). The budget is a soft
   estimate: going over it adds one line to the summary and never fails
   the run.

   Left out: ``team/scripts/lint_doc_command_truth.py`` (every command a
   document cites answers ``--help``). It took about 7.2 seconds alone on
   the same tree, more than the whole budget, because it starts one
   ``--help`` process per cited command, so its cost grows with the
   documents. It still runs in every certification run's quality-class
   block.

   The guards run as scripts, not as test files, so no test file runs both
   as a guard and as an affected test in one run.
4. **affected tests** -- the test files under ``team/tests/unit`` that the
   affected-test finder (:mod:`pgai_agent_kanban.testing.affected`) selects
   for ``git diff <base>...HEAD``, run with pytest. The by-path test file is
   left out because check 1 has already run it, and so is every test file
   the unit runner itself ignores (:data:`IGNORED_TEST_FILES`).

With ``--suite integration`` only the affected-tests check runs, over
``team/tests/integration``. This is the integration runner's equivalent.

Every check runs even after an earlier one fails, so a single run reports
everything that needs fixing.

The base
--------
``--base REF`` names the diff base. When it is omitted, the default base is
the RC branch this worktree was cut from. That is the local
``<prefix>rc/vX.Y.Z`` branch that HEAD is fewest commits ahead of. Ties go
to the RC branch with the higher version (compared with
:mod:`pgai_agent_kanban.lib.semver`). When the current branch is a
``<prefix>feature/...`` branch, only RC branches with that same prefix are
considered. With no RC branch the default falls back to ``<prefix>main``,
then ``main``. The summary always names the base it used and how it was
chosen. Only local refs are read; nothing is fetched. The diff is
``base...HEAD``, so a change that is not yet committed takes no part in
choosing affected tests. A dirty working tree is narrated.

The cap
-------
``--cap N`` is the most affected test files one run runs. The default is
60, the ``[testing] pre_merge_affected_cap`` default; the runners pass the
resolved key. 0 means no cap. When more files are affected than the cap
allows, the run says so ("143 affected test files; cap 60 applied") and
runs only the N most-referencing ones, in the finder's ranking order.

Workers
-------
``--workers N`` is the ``[testing] workers`` budget (default 1). Each
pytest invocation is planned by the gated runners' own lane planner
(``team/scripts/lib/gated_runner_lanes.py``). Above 1, with pytest-xdist
importable and more than one affected file, the affected tests run in a
parallel lane (``-n N --dist loadfile``) and then a lane for tests marked
``serial``. Otherwise one single-process lane runs. Checks run one after
another, so no more than N pytest workers are ever alive at once.

What a run writes
-----------------
Nothing outside its own scratch directory. That directory is created under
``$PGAI_AGENT_KANBAN_TEMP_DIR`` (the system temp directory when the
variable is unset) and removed when the run ends. It holds the lints'
findings files and pytest's basetemp. The guards only read the tree
(``--check`` renders in memory and writes nothing). The run never writes the
certification surfaces: not ``lint/findings-unit.json`` or
``findings-integration.json``, not the green ledger, and not
``targeted-*.exit``. Writing ``pre-merge.exit`` is the calling runner's job.
The lints and guards run without ``PGAI_PROJECT_NAME`` in their environment,
so a finding is not recorded in the kanban's refusal ledger. Otherwise the
environment is inherited, and the calling runner prepares it as it does for
any pytest run. Started directly, the affected tests run in the calling
shell's environment. Inside an agent wake, that environment names the live
kanban root, so give a direct run the project README's throwaway root, as
for any test run outside the gated runners.

Enforcement class
-----------------
Quality class with respect to releases. A failed or skipped pre-merge run
never blocks an RC by itself, and TESTER still certifies the full suites.
Within a CODER or WRITER ticket, the acceptance criterion requires exit 0
before merging. A failing run is fixed in the same session (fix the change,
tighten the allowlist, update the stale test) and run again.

Command line
------------
    python3 team/pgai_agent_kanban/testing/pre_merge.py
    python3 team/pgai_agent_kanban/testing/pre_merge.py --base ai_rc/v2.35.0 --workers 3 --cap 60
    python3 team/pgai_agent_kanban/testing/pre_merge.py --suite integration --base ai_main

``--repo DIR`` is the repository to check. It defaults to the git
repository holding the current directory.

Exit codes: 0 every check passed. 1 at least one check failed. 2
invocation error: an unknown base ref, a directory that is not a git
repository, no RC or main branch to default to, or a bad argument. These are
printed in the dual-channel error shape.
"""

from __future__ import annotations

import argparse
import importlib.util
import json
import os
import pathlib
import re
import shutil
import subprocess
import sys
import tempfile
import time
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field

# Run by file path (``python3 .../pre_merge.py``) or loaded from a file
# outside its package, this module has no parent package and the interpreter
# puts only its own directory on sys.path, so the package imports below would
# not resolve. Put this file's own package root -- the directory holding the
# pgai_agent_kanban/ this file sits in -- at the front of sys.path so the
# imports resolve from the same tree, never another. Imported as a package
# member, sys.path is left untouched.
if not __package__:
    _OWN_PACKAGE_ROOT = str(pathlib.Path(__file__).resolve().parents[2])
    if _OWN_PACKAGE_ROOT not in sys.path:
        sys.path.insert(0, _OWN_PACKAGE_ROOT)

from pgai_agent_kanban.lib import semver  # noqa: E402
from pgai_agent_kanban.lib.error_contract import format_error_contract  # noqa: E402
from pgai_agent_kanban.testing.affected import (  # noqa: E402
    DiffReadError,
    _git as git_output,
    affected_tests,
    names_from_diff,
)

# ---------------------------------------------------------------------------
# What runs
# ---------------------------------------------------------------------------

#: The default cap on affected test files, the [testing]
#: pre_merge_affected_cap default.
DEFAULT_CAP = 60

#: The by-path module test, relative to the repository's team/ directory.
BY_PATH_TEST = "tests/unit/test_package_modules_run_by_path.py"

#: Each suite's test tree, relative to team/.
SUITE_TEST_DIRS: dict[str, str] = {"unit": "tests/unit", "integration": "tests/integration"}

#: Test files a suite's runner never collects. Affected selection leaves
#: them out too, so this run never starts what the runner deliberately
#: skips (an image-building test is the container tier's, never a wake's).
IGNORED_TEST_FILES: dict[str, tuple[str, ...]] = {
    "unit": ("tests/unit/test_entrypoint_shipped_image.py",),
    "integration": (),
}

#: The marker selection every gated run applies by default.
MARKER_SELECTION = "not quarantine"

#: pytest exit code for "no tests collected".
_PYTEST_NO_TESTS = 5

_LANES_PLANNER = pathlib.Path("scripts") / "lib" / "gated_runner_lanes.py"

_RC_BRANCH_RE = re.compile(r"^(?P<prefix>.*?)rc/(?P<version>v\d+\.\d+\.\d+)\s*$")
_FEATURE_BRANCH_RE = re.compile(r"^(?P<prefix>.*?)feature/")
_FAILED_NODE_RE = re.compile(r"^(?:FAILED|ERROR)\s+(?P<node>\S+?)(?:\s+-\s+.*)?$")


@dataclass(frozen=True)
class RatchetLint:
    """One ratchet lint, how it is invoked, and how its verdict is read.

    ``reports_by`` is ``"findings"`` for a lint that always exits 0 and
    writes a JSON findings file (any entry fails the check), or ``"exit"``
    for a lint that reports through its exit code (non-zero fails it).
    """

    name: str
    script: str
    reports_by: str


#: The five ratchet lints, then the tracked-files lint, in run order. Paths
#: are relative to team/.
RATCHET_LINTS: tuple[RatchetLint, ...] = (
    RatchetLint("inline-python", "scripts/lint_inline_python.py", "findings"),
    RatchetLint("python-duplicates", "scripts/lint_python_duplicates.py", "findings"),
    RatchetLint("engine-size", "scripts/lint_engine_size_dupe.py", "exit"),
    RatchetLint("api-route-impl", "scripts/lint_api_route_impl.py", "findings"),
    RatchetLint("echo-pipe-grep-q", "scripts/lint_echo_pipe_grep_q.py", "findings"),
    RatchetLint("tracked-ignored", "scripts/lint_tracked_ignored.py", "findings"),
)

#: lint_engine_size_dupe.py's exit code for a usage error (no findings).
_ENGINE_LINT_USAGE_EXIT = 2


@dataclass(frozen=True)
class WholeTreeGuard:
    """One always-run whole-tree guard: a script that checks the real tree
    for a property any change can break, whatever files the change touches.

    ``argv`` is the guard script, relative to team/, then its arguments.
    The script reports through its exit code: 0 clean, 1 a finding (printed
    on its output), anything else the guard could not run. ``fix`` is
    printed beneath a failing row: what clears the finding, as a command
    run from the repository root.
    """

    name: str
    argv: tuple[str, ...]
    fix: str

    @property
    def script(self) -> str:
        """The guard script, relative to team/."""
        return self.argv[0]

    @property
    def command(self) -> str:
        """The guard as a command run from the repository root."""
        return " ".join(["python3", f"team/{self.script}", *self.argv[1:]])


#: The always-run whole-tree guards, in run order. Paths are relative to
#: team/. Chosen by measured run cost; the module docstring names the one
#: candidate left out and why.
WHOLE_TREE_GUARDS: tuple[WholeTreeGuard, ...] = (
    WholeTreeGuard(
        "env-bootstrap", ("scripts/lint_env_bootstrap.py",),
        "read the kanban root through resolve_kanban_root() from pgai_agent_kanban.env "
        "(Python) or source scripts/lib/env_bootstrap.sh (bash) at each site named above, "
        "then re-check: python3 team/scripts/lint_env_bootstrap.py",
    ),
    WholeTreeGuard(
        "operator-verb-docs", ("scripts/generate_operator_verb_docs.py", "--check"),
        "regenerate, then commit docs/operator-commands.md and docs/operator-api.md: "
        "python3 team/scripts/generate_operator_verb_docs.py",
    ),
    WholeTreeGuard(
        "dashboard-window-docs", ("scripts/dashboard/generate_window_docs.py", "--check"),
        "regenerate, then commit team/scripts/dashboard/create.sh, docs/DASHBOARD-PANES.md "
        "and docs/dashboard.md: python3 team/scripts/dashboard/generate_window_docs.py",
    ),
    # The docs-citations fix names both remedies rather than only deferring
    # to the checker's To-fix block: the guard's output above it is cut at
    # _GUARD_OUTPUT_LINES, which can hide that block when findings are many.
    WholeTreeGuard(
        "docs-citations", ("scripts/check_docs_citations.py",),
        "follow the checker's To fix steps: for a page not yet delivered, add the manifest "
        "line it names to team/config/docs-manifest.txt; for a recorded exclusion "
        "(EXCLUDED-CITATION-TARGETS), never add a manifest line -- drop the docs/ prefix "
        "and say the page is in the repository checkout only, or remove the citation; "
        "then re-check: python3 team/scripts/check_docs_citations.py",
    ),
)

#: The wall time, in seconds, the always-run guards together are expected
#: to add to a run. A soft estimate: the summary reports the guards' total
#: against it, and going over it never fails the run.
GUARD_BUDGET_SECONDS = 5.0

#: The most lines of a failing guard's output printed beneath its row.
_GUARD_OUTPUT_LINES = 20


# ---------------------------------------------------------------------------
# Results
# ---------------------------------------------------------------------------


@dataclass
class CheckResult:
    """One check's verdict: its name, pass/fail, how long it took, and the
    detail lines that explain a failure (or narrate a pass). ``guard`` marks
    an always-run whole-tree guard, whose time counts against the budget."""

    name: str
    passed: bool
    duration: float = 0.0
    detail: list[str] = field(default_factory=list)
    note: str = ""
    guard: bool = False


@dataclass
class PreMergeRun:
    """Everything one run decided and found."""

    repo: pathlib.Path
    suite: str
    base: str
    base_reason: str
    cap: int | None
    workers: int
    checks: list[CheckResult] = field(default_factory=list)
    narration: list[str] = field(default_factory=list)
    duration: float = 0.0

    @property
    def passed(self) -> bool:
        """True when every check in the run passed (also true for a run with no checks)."""
        return all(check.passed for check in self.checks)

    @property
    def failed(self) -> list[CheckResult]:
        """Return the checks that did not pass, in run order."""
        return [check for check in self.checks if not check.passed]

    @property
    def guards(self) -> list[CheckResult]:
        """The always-run whole-tree guards' results, in run order."""
        return [check for check in self.checks if check.guard]


@dataclass(eq=False)
class InvocationError(RuntimeError):
    """The run cannot start; carries the dual-channel fields: a stable
    machine ``error`` key, a plain ``message`` and a runnable ``remedy``."""

    error: str
    message: str
    remedy: str

    def __post_init__(self) -> None:
        super().__init__(self.message)


# ---------------------------------------------------------------------------
# The base
# ---------------------------------------------------------------------------


def _local_branches(repo: pathlib.Path) -> list[str]:
    listed = git_output(repo, "for-each-ref", "--format=%(refname:short)", "refs/heads/")
    return [line.strip() for line in listed.splitlines() if line.strip()]


def _current_branch(repo: pathlib.Path) -> str:
    return git_output(repo, "rev-parse", "--abbrev-ref", "HEAD").strip()


def _commits_ahead(repo: pathlib.Path, base: str) -> int:
    """How many commits HEAD has that *base* does not."""
    return int(git_output(repo, "rev-list", "--count", f"{base}..HEAD").strip() or "0")


def default_base(repo: str | pathlib.Path) -> tuple[str, str]:
    """Resolve the default diff base for *repo*: ``(ref, how it was chosen)``.

    The RC branch HEAD was cut from: of the local ``<prefix>rc/vX.Y.Z``
    branches (only those sharing the current ``<prefix>feature/`` branch's
    prefix, when HEAD is on one), the one HEAD is fewest commits ahead of,
    the higher version winning a tie. With no RC branch, ``<prefix>main``,
    then ``main``.

    Raises:
        InvocationError: no RC or main branch exists to default to.
        DiffReadError: *repo* is not a git repository.
    """
    repo_path = pathlib.Path(repo)
    branches = _local_branches(repo_path)
    current = _current_branch(repo_path)
    feature = _FEATURE_BRANCH_RE.match(current)
    prefix = feature.group("prefix") if feature else None

    rc_branches = [
        b for b in branches
        if (m := _RC_BRANCH_RE.match(b)) and (prefix is None or m.group("prefix") == prefix)
    ]
    if rc_branches:
        def rank(branch: str) -> tuple[int, tuple[int, int, int]]:
            version = _RC_BRANCH_RE.match(branch).group("version")
            major, minor, patch = semver.version_sort_key(version)
            return (_commits_ahead(repo_path, branch), (-major, -minor, -patch))

        chosen = min(rc_branches, key=rank)
        if len(rc_branches) == 1:
            return chosen, "default: the only local RC branch"
        return chosen, f"default: the RC branch HEAD is nearest, of {len(rc_branches)} local RC branches"

    for candidate in ([f"{prefix}main"] if prefix else []) + ["main"]:
        if candidate in branches:
            return candidate, "default: no local RC branch, so the main branch"

    raise InvocationError(
        "pre_merge.no_default_base",
        f"no local RC branch (<prefix>rc/vX.Y.Z) or main branch exists in {repo_path} "
        f"to compare HEAD against, so the affected tests cannot be chosen; "
        f"local branches: {', '.join(branches) or '(none)'}",
        f"python3 team/pgai_agent_kanban/testing/pre_merge.py --repo {repo_path} --base HEAD~1",
    )


# ---------------------------------------------------------------------------
# Running things
# ---------------------------------------------------------------------------


def _run(argv: Sequence[str], cwd: pathlib.Path, env: dict[str, str] | None = None) -> subprocess.CompletedProcess:
    """Run *argv* in *cwd*, capturing combined output as text."""
    return subprocess.run(
        list(argv), cwd=str(cwd), env=env, stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT, text=True, check=False,
    )


def _timed(check: Callable[[], CheckResult]) -> CheckResult:
    started = time.monotonic()
    result = check()
    result.duration = time.monotonic() - started
    return result


def _lint_env() -> dict[str, str]:
    """The environment a lint runs in: inherited, minus the project name
    that would record a finding in the kanban's refusal ledger."""
    env = dict(os.environ)
    env.pop("PGAI_PROJECT_NAME", None)
    return env


#: This module's own team/ directory (the kanban root in a live install).
#: The lane planner is run machinery, so it comes from this tree; the lints
#: and tests come from the repository under check.
_OWN_TEAM_DIR = pathlib.Path(__file__).resolve().parents[2]

_LANE_PLANNER_MODULE = "_pre_merge_gated_runner_lanes"


def _load_lane_planner():
    """Load the gated runners' lane planner from this module's own tree.

    Loaded when a run first needs it, not at import, so importing this
    module never touches sys.path (the planner puts its own directory there
    to import its durations sibling).
    """
    loaded = sys.modules.get(_LANE_PLANNER_MODULE)
    if loaded is not None:
        return loaded
    path = _OWN_TEAM_DIR / _LANES_PLANNER
    spec = importlib.util.spec_from_file_location(_LANE_PLANNER_MODULE, path)
    if spec is None or spec.loader is None or not path.is_file():
        raise InvocationError(
            "pre_merge.lane_planner_missing",
            f"the gated runners' lane planner is missing at {path}; this run plans its "
            f"pytest lanes with it",
            f"ls -l {path.parent}",
        )
    module = importlib.util.module_from_spec(spec)
    # Registered before it runs: its dataclasses look their module up by name.
    sys.modules[_LANE_PLANNER_MODULE] = module
    try:
        spec.loader.exec_module(module)
    except BaseException:
        sys.modules.pop(_LANE_PLANNER_MODULE, None)
        raise
    return module


def combine_lane_exits(codes: Sequence[int]) -> int:
    """Combine pytest lane exit codes as the gated runners do.

    The first code that is neither 0 nor 5 (no tests collected) wins.
    Otherwise 0 if any lane passed, else 5.
    """
    failures = [code for code in codes if code not in (0, _PYTEST_NO_TESTS)]
    if failures:
        return failures[0]
    return 0 if 0 in codes else _PYTEST_NO_TESTS


def failed_test_files(output: str) -> list[str]:
    """The test files named by pytest's ``FAILED`` / ``ERROR`` summary
    lines in *output*, in first-seen order, without duplicates."""
    files: list[str] = []
    for line in output.splitlines():
        match = _FAILED_NODE_RE.match(line.strip())
        if match:
            path = match.group("node").split("::", 1)[0]
            if path not in files:
                files.append(path)
    return files


@dataclass
class _PytestOutcome:
    exit_code: int
    output: str
    narration: list[str]


def _run_pytest(
    team_dir: pathlib.Path, files: Sequence[str], *, suite: str, workers: int,
    scratch: pathlib.Path, label: str, single_lane: bool,
) -> _PytestOutcome:
    """Run pytest on *files* from *team_dir* through the lane planner."""
    planner = _load_lane_planner()
    argv = [
        "-p", "no:randomly", *files,
        *(f"--ignore={ignored}" for ignored in IGNORED_TEST_FILES.get(suite, ())),
        "-m", MARKER_SELECTION,
    ]
    plan = planner.plan_lanes(argv, suite=suite, workers=workers, targeted=single_lane)
    if single_lane:
        # The planner's single-lane wording names a --files run; say why here.
        narration = [f"Parallel workers: none for {len(files)} test file(s); one single-process pytest run."]
    else:
        narration = [*plan.say, *plan.warn]
    codes: list[int] = []
    output: list[str] = []
    for lane in plan.lanes:
        basetemp = scratch / f"pytest-{label}-{lane.name}"
        done = _run(
            [sys.executable, "-m", "pytest", *lane.argv, f"--basetemp={basetemp}"], team_dir,
        )
        codes.append(done.returncode)
        output.append(done.stdout)
    return _PytestOutcome(combine_lane_exits(codes), "".join(output), narration)


def _tail(text: str, lines: int = 40) -> list[str]:
    kept = [line for line in text.splitlines() if line.strip()]
    return kept[-lines:]


# ---------------------------------------------------------------------------
# The checks
# ---------------------------------------------------------------------------


def check_by_path(team_dir: pathlib.Path, *, scratch: pathlib.Path) -> CheckResult:
    """Run the by-path module test over the whole package, in one
    single-process lane (one test file gains nothing from loadfile
    distribution)."""
    result = CheckResult("by-path", passed=False)
    if not (team_dir / BY_PATH_TEST).is_file():
        result.detail = [f"{BY_PATH_TEST} is missing under {team_dir}; the by-path check cannot run"]
        return result
    outcome = _run_pytest(
        team_dir, [BY_PATH_TEST], suite="unit", workers=1, scratch=scratch,
        label="by-path", single_lane=True,
    )
    result.passed = outcome.exit_code == 0
    if result.passed:
        result.note = BY_PATH_TEST
        return result
    failing = failed_test_files(outcome.output)
    nodes = [
        line.strip() for line in outcome.output.splitlines()
        if _FAILED_NODE_RE.match(line.strip())
    ]
    result.note = f"pytest exited {outcome.exit_code}"
    result.detail = nodes or _tail(outcome.output)
    if not failing and outcome.exit_code == _PYTEST_NO_TESTS:
        result.detail.append(f"{BY_PATH_TEST} collected no tests")
    result.detail.append(
        "a package module with a __main__ block needs the 'if not __package__:' "
        "self-location guard (copy it from team/pgai_agent_kanban/testing/affected.py)"
    )
    return result


def check_ratchet_lint(
    team_dir: pathlib.Path, lint: RatchetLint, *, scratch: pathlib.Path,
) -> CheckResult:
    """Run one ratchet lint as the gated runners do and judge its findings."""
    result = CheckResult(f"lint: {lint.name}", passed=False)
    script = team_dir / lint.script
    if not script.is_file():
        result.detail = [f"team/{lint.script} is missing; the {lint.name} lint cannot run"]
        return result
    argv = [sys.executable, str(script)]
    findings_file = scratch / f"findings-{lint.name}.json"
    if lint.reports_by == "findings":
        argv += ["--findings-file", str(findings_file)]
    done = _run(argv, team_dir.parent, env=_lint_env())

    if lint.reports_by == "exit":
        if done.returncode == 0:
            result.passed = True
            return result
        if done.returncode == _ENGINE_LINT_USAGE_EXIT:
            result.note = "the lint could not run"
            result.detail = _tail(done.stdout)
            return result
        result.note = f"exit {done.returncode}"
        # The lint prints each finding indented two spaces under its FAIL line.
        result.detail = [
            line.strip() for line in done.stdout.splitlines()
            if line.startswith("  ") and line.strip()
        ] or _tail(done.stdout)
        return result

    try:
        findings = json.loads(findings_file.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        result.note = "the lint could not run"
        result.detail = [f"no readable findings file ({exc}); lint exited {done.returncode}", *_tail(done.stdout, 10)]
        return result
    if done.returncode != 0:
        result.note = "the lint could not run"
        result.detail = [f"lint exited {done.returncode}", *_tail(done.stdout, 10)]
        return result
    result.passed = not findings
    if findings:
        result.note = f"{len(findings)} finding(s)"
        result.detail = [f"[{f.get('kind', '?')}] {f.get('summary', f)}" for f in findings]
    return result


def _head(text: str, lines: int, rerun: str) -> list[str]:
    """The first *lines* non-blank lines of *text*, and a closing line
    naming how many more there are and the command that prints them all."""
    kept = [line for line in text.splitlines() if line.strip()]
    if len(kept) <= lines:
        return kept
    return [*kept[:lines], f"... {len(kept) - lines} more line(s); to see them all: {rerun}"]


def check_whole_tree_guard(team_dir: pathlib.Path, guard: WholeTreeGuard) -> CheckResult:
    """Run one always-run whole-tree guard over the tree and judge its exit
    code: 0 passes, anything else fails, with the guard's output and fix
    beneath the row."""
    result = CheckResult(f"guard: {guard.name}", passed=False, guard=True)
    script = team_dir / guard.script
    if not script.is_file():
        result.note = "the guard could not run"
        result.detail = [f"team/{guard.script} is missing; the {guard.name} guard cannot run"]
        return result
    done = _run([sys.executable, str(script), *guard.argv[1:]], team_dir.parent, env=_lint_env())
    if done.returncode == 0:
        result.passed = True
        return result
    result.note = "finding(s)" if done.returncode == 1 else f"the guard could not run (exit {done.returncode})"
    result.detail = [
        *_head(done.stdout, _GUARD_OUTPUT_LINES, guard.command),
        f"fix: {guard.fix}",
    ]
    return result


def check_affected_tests(
    team_dir: pathlib.Path, names: Sequence[str], *, suite: str,
    cap: int | None, workers: int, scratch: pathlib.Path,
    narrate: Callable[[str], None],
) -> CheckResult:
    """Select the test files citing *names* (the names changed in
    ``base...HEAD``) and run them."""
    label = "affected tests" if suite == "unit" else f"affected {suite} tests"
    result = CheckResult(label, passed=False)
    tests_root = team_dir / SUITE_TEST_DIRS[suite]
    if not tests_root.is_dir():
        result.passed = True
        result.note = f"no {SUITE_TEST_DIRS[suite]} directory; nothing to select"
        return result

    ranked = affected_tests(names, tests_root, None)
    left_out = set(IGNORED_TEST_FILES.get(suite, ()))
    if suite == "unit":
        left_out.add(BY_PATH_TEST)
    relative = [p.relative_to(team_dir).as_posix() for p in ranked]
    candidates = [r for r in relative if r not in left_out]
    skipped = [r for r in relative if r in left_out]
    if skipped:
        narrate(f"affected tests: left out (run by the by-path check or ignored by the runner): {', '.join(skipped)}")
    selected = candidates if cap is None else candidates[:cap]
    if cap is not None and len(candidates) > cap:
        narrate(
            f"affected tests: {len(candidates)} affected test files; cap {cap} applied, "
            f"running the {cap} most-referencing"
        )
    else:
        narrate(f"affected tests: {len(candidates)} affected test file(s)")
    for path in selected:
        narrate(f"  {path}")

    if not selected:
        result.passed = True
        result.note = "no affected test files"
        return result

    outcome = _run_pytest(
        team_dir, selected, suite=suite, workers=workers, scratch=scratch,
        label=suite, single_lane=len(selected) < 2,
    )
    for line in outcome.narration:
        narrate(f"affected tests: {line}")
    count = f"{len(selected)} of {len(candidates)} affected test file(s) run"
    result.passed = outcome.exit_code in (0, _PYTEST_NO_TESTS)
    if result.passed:
        result.note = count
        return result
    failing = failed_test_files(outcome.output)
    result.note = f"{count}; pytest exited {outcome.exit_code}"
    result.detail = (
        [f"failing test file: {path}" for path in failing] if failing else _tail(outcome.output)
    )
    if failing:
        result.detail.append(
            "a failing test asserting behaviour this change altered is updated in the same change"
        )
    return result


# ---------------------------------------------------------------------------
# The run
# ---------------------------------------------------------------------------


def _dirty(repo: pathlib.Path) -> bool:
    return bool(git_output(repo, "status", "--porcelain", "--untracked-files=no").strip())


def _scratch_root() -> str | None:
    temp = os.environ.get("PGAI_AGENT_KANBAN_TEMP_DIR", "").strip()
    if not temp:
        return None
    pathlib.Path(temp).mkdir(parents=True, exist_ok=True)
    return temp


def run_pre_merge(
    repo: str | pathlib.Path, *, suite: str = "unit", base: str | None = None,
    cap: int | None = DEFAULT_CAP, workers: int = 1,
    narrate: Callable[[str], None] = print,
) -> PreMergeRun:
    """Run the pre-merge checks for *repo* and return what they found.

    Args:
        repo: any directory inside the git repository to check.
        suite: ``"unit"`` (by-path, the five ratchet lints and the
            tracked-files lint, the always-run
            whole-tree guards, affected unit tests) or ``"integration"``
            (affected integration tests only).
        base: the diff base; None resolves :func:`default_base`.
        cap: the most affected test files to run; None for no cap.
        workers: the ``[testing] workers`` budget for pytest.
        narrate: receives each progress line as the run goes.

    Raises:
        InvocationError: no default base, or the lane planner is missing.
        DiffReadError: *repo* is not a git repository or *base* is unknown.
        ValueError: *suite*, *cap* or *workers* is out of range.
    """
    if suite not in SUITE_TEST_DIRS:
        raise ValueError(f"suite must be one of {', '.join(SUITE_TEST_DIRS)}; got {suite!r}")
    if cap is not None and (isinstance(cap, bool) or not isinstance(cap, int) or cap < 1):
        raise ValueError(f"cap must be a whole number of test files, 1 or more, or None; got {cap!r}")
    if isinstance(workers, bool) or not isinstance(workers, int) or workers < 0:
        raise ValueError(f"workers must be a non-negative whole number; got {workers!r}")

    started = time.monotonic()
    toplevel = pathlib.Path(git_output(pathlib.Path(repo), "rev-parse", "--show-toplevel").strip())
    team_dir = toplevel / "team"
    if base is None:
        base, base_reason = default_base(toplevel)
    else:
        base_reason = "given with --base"
    # Read the diff before any check runs, so an unknown ref stops the run
    # as an invocation error rather than as a failed check.
    names = names_from_diff(toplevel, base)

    run = PreMergeRun(
        repo=toplevel, suite=suite, base=base, base_reason=base_reason, cap=cap, workers=workers,
    )

    def say(line: str) -> None:
        run.narration.append(line)
        narrate(f"pre-merge: {line}")

    say(f"repo {toplevel}; suite {suite}; base {base} ({base_reason}); "
        f"cap {cap if cap is not None else 'none'}; workers {workers}")
    if _dirty(toplevel):
        say("uncommitted changes are not part of base...HEAD and take no part in choosing "
            "affected tests; commit them and re-run to include them")

    scratch = pathlib.Path(tempfile.mkdtemp(prefix="pre-merge-", dir=_scratch_root()))
    try:
        if suite == "unit":
            say(f"by-path: running {BY_PATH_TEST}")
            run.checks.append(_timed(lambda: check_by_path(team_dir, scratch=scratch)))
            for lint in RATCHET_LINTS:
                say(f"lint: running {lint.name} (team/{lint.script})")
                run.checks.append(_timed(lambda lint=lint: check_ratchet_lint(team_dir, lint, scratch=scratch)))
            for guard in WHOLE_TREE_GUARDS:
                say(f"guard: running {guard.name} ({guard.command})")
                run.checks.append(_timed(lambda guard=guard: check_whole_tree_guard(team_dir, guard)))
        run.checks.append(_timed(lambda: check_affected_tests(
            team_dir, names, suite=suite, cap=cap, workers=workers, scratch=scratch, narrate=say,
        )))
    finally:
        shutil.rmtree(scratch, ignore_errors=True)
    run.duration = time.monotonic() - started
    return run


def format_summary(run: PreMergeRun) -> str:
    """The summary block: one row per check with its verdict and duration,
    the detail of each failure beneath its row, the always-run guards'
    added wall time against :data:`GUARD_BUDGET_SECONDS` (when any ran),
    and a closing verdict."""
    rule = "=" * 72
    width = max(len(check.name) for check in run.checks) if run.checks else 10
    lines = [
        rule,
        "pre-merge summary",
        f"base:    {run.base} ({run.base_reason})",
        f"suite:   {run.suite}; cap {run.cap if run.cap is not None else 'none'}; "
        f"workers {run.workers}",
        "-" * 72,
    ]
    for check in run.checks:
        verdict = "PASS" if check.passed else "FAIL"
        row = f"{check.name:<{width}}  {verdict}  {check.duration:7.1f}s"
        if check.note:
            row += f"  {check.note}"
        lines.append(row)
        if not check.passed:
            lines.extend(f"    {line}" for line in check.detail)
    lines.append("-" * 72)
    guards = run.guards
    if guards:
        guard_seconds = sum(check.duration for check in guards)
        # CONVERT: a time budget is a soft estimate. Going over it is this
        # one line and never changes the run's verdict.
        if guard_seconds > GUARD_BUDGET_SECONDS:
            lines.append(
                f"guards:  {len(guards)} always-run guard(s) added {guard_seconds:.1f}s, over the "
                f"{GUARD_BUDGET_SECONDS:.1f}s budget (a soft estimate; it never fails the run)"
            )
        else:
            lines.append(
                f"guards:  {len(guards)} always-run guard(s) added {guard_seconds:.1f}s of the "
                f"{GUARD_BUDGET_SECONDS:.1f}s budget"
            )
    failed = run.failed
    if failed:
        lines.append(
            f"pre-merge: FAIL -- {len(failed)} of {len(run.checks)} check(s) failed "
            f"({', '.join(c.name for c in failed)}) in {run.duration:.1f}s"
        )
    else:
        lines.append(f"pre-merge: PASS -- all {len(run.checks)} check(s) passed in {run.duration:.1f}s")
    lines.append(rule)
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Command line
# ---------------------------------------------------------------------------


def _non_negative_int(text: str) -> int:
    try:
        value = int(text)
    except ValueError:
        value = -1
    if value < 0:
        raise argparse.ArgumentTypeError(f"expected a non-negative whole number, got {text!r}")
    return value


#: The always-run guards' names, as the help text lists them.
_GUARD_NAMES = ", ".join(guard.name for guard in WHOLE_TREE_GUARDS)


def _build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="pre_merge.py",
        description=(
            "Run the pre-merge checks from a worktree: the by-path module test, "
            "the five ratchet lints in fail-on-regression mode and the tracked-files "
            "lint, the always-run "
            f"whole-tree guards ({_GUARD_NAMES}; whatever the diff, their added "
            f"wall time reported against a {GUARD_BUDGET_SECONDS:.1f}s budget that "
            "never fails the run), and the tests affected by 'git diff BASE...HEAD'. "
            "Prints one summary block; exits non-zero when any check fails."
        ),
        epilog=(
            "Examples:\n"
            "  python3 team/pgai_agent_kanban/testing/pre_merge.py\n"
            "  python3 team/pgai_agent_kanban/testing/pre_merge.py --base ai_rc/v2.35.0 --workers 3 --cap 60\n"
            "  python3 team/pgai_agent_kanban/testing/pre_merge.py --suite integration --base ai_main\n"
            "\n"
            "Exit codes: 0 every check passed; 1 a check failed; "
            "2 invocation error (unknown base, not a git repository, no default base)."
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument(
        "--base", metavar="REF", default=None,
        help="diff base for choosing affected tests (default: the local RC branch "
             "this worktree was cut from; with none, the main branch)",
    )
    parser.add_argument(
        "--suite", choices=tuple(SUITE_TEST_DIRS), default="unit",
        help=f"unit: by-path, the five ratchet lints and the tracked-files lint, the always-run whole-tree guards "
             f"({_GUARD_NAMES}; {GUARD_BUDGET_SECONDS:.1f}s budget) and affected unit tests "
             f"(default); integration: affected integration tests only",
    )
    parser.add_argument(
        "--cap", metavar="N", type=_non_negative_int, default=DEFAULT_CAP,
        help=f"most affected test files to run, the most-referencing first; "
             f"0 means no cap (default: {DEFAULT_CAP}, the [testing] "
             f"pre_merge_affected_cap default)",
    )
    parser.add_argument(
        "--workers", metavar="N", type=_non_negative_int, default=1,
        help="[testing] workers budget for pytest; 0 or 1 runs single-process (default: 1)",
    )
    parser.add_argument(
        "--repo", metavar="DIR", default=".",
        help="a directory inside the git repository to check (default: the current directory)",
    )
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    """Command-line entry point; returns the process exit code."""
    args = _build_arg_parser().parse_args(argv)
    cap = None if args.cap == 0 else args.cap
    # Invocation errors stop the run before any check: printed in the
    # dual-channel shape and mapped to exit 2, as the affected-test
    # finder's own command line does.
    try:
        run = run_pre_merge(
            args.repo, suite=args.suite, base=args.base, cap=cap, workers=args.workers,
            narrate=lambda line: print(line, flush=True),
        )
    except (DiffReadError, InvocationError) as exc:
        print(format_error_contract(exc.error, exc.message, exc.remedy), file=sys.stderr)
        return 2
    except ValueError as exc:
        print(format_error_contract(
            "pre_merge.bad_argument", str(exc),
            "python3 team/pgai_agent_kanban/testing/pre_merge.py --help",
        ), file=sys.stderr)
        return 2

    print(format_summary(run), flush=True)
    if run.passed:
        return 0
    # AGENT-RESOLVABLE within a ticket, CONVERT with respect to a release: a
    # failed check stops the feature branch's merge until it is fixed in the same
    # session, and never blocks an RC by itself (TESTER still certifies).
    rerun = (
        f"python3 {pathlib.Path(__file__).resolve()} "
        f"--repo {run.repo} --suite {run.suite} --base {run.base} --workers {run.workers} "
        f"--cap {run.cap if run.cap is not None else 0}"
    )
    print(format_error_contract(
        "pre_merge.checks_failed",
        f"{len(run.failed)} pre-merge check(s) failed: "
        f"{', '.join(c.name for c in run.failed)}; the summary above names each failure",
        rerun,
    ), file=sys.stderr)
    return 1


if __name__ == "__main__":
    sys.exit(main())
