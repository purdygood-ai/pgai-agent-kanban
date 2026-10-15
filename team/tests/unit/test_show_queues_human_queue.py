"""
test_show_queues_human_queue.py
===============================
Behavioral tests for the human-queue rendering in show-queues.sh --details.

Covers the load-bearing acceptance criterion from an earlier defect:

  A HUMAN-APPROVE gate task whose human_backlog.md queue entry carries a
  valid marker ('[W]' or ' ') must render in 'show-queues --details' with
  ZERO '⚠ no queue entry' warnings.

Tests:

  1. green path — HUMAN-APPROVE task + valid queue marker in human_backlog.md
     → output contains no '⚠ no queue entry' text.

  2. negative guard — same task but queue file is absent
     → output DOES contain '⚠ no queue entry' for the HUMAN-APPROVE task.
     (Proves the test actually guards the property; not merely asserting on
     an output that never emits the warning regardless of input.)

Also covers --project resolution (added alongside the human-queue tests
rather than in a new file, per this suite's own existing-test-home
convention):

  3. exact name vs. unambiguous prefix — both invocations render the
     identical compact board for the same underlying project.

  4. unknown name — refuses dual-channel (error:/message:/remedy:) with a
     non-zero exit and renders no board at all.

  5. absent --project (and PGAI_PROJECT_NAME unset): compact rendering
     refuses dual-channel with no counter lines -- on a registered root and
     on a single-project root whose <root>/tasks/queues/ holds real
     entries -- while --details' iterate-all loop still walks every
     registered project; show-multi.sh's registry-less queues fallback
     passes the same refusal through.

Fixture structure built by each test (under pytest tmp_path):
  <tmp_path>/
    projects.cfg                      # INI registry registering 'fixture-proj'
    projects/
      fixture-proj/
        tasks/
          HUMAN-APPROVE-v1.10.0-099/
            README.md                 # ## Role: HUMAN
            status.md                 # ## State: WAITING
          queues/
            human_backlog.md          # - [W] HUMAN-APPROVE-v1.10.0-099

Test isolation:
  All filesystem operations are under tmp_path (pytest-managed; redirected
  to PGAI_AGENT_KANBAN_TEMP_DIR/tests by the parent conftest.py autouse
  fixture).  The subprocess invokes show-queues.sh with PGAI_AGENT_KANBAN_ROOT_PATH
  pointing at tmp_path and TERM=dumb to suppress ANSI codes.

Wiring:
  This test is automatically included in the unit suite (run-unit-tests.sh)
  because it lives in team/tests/unit/ and follows the test_*.py naming
  convention that pytest discovers.
"""

from __future__ import annotations

import pathlib
import subprocess

import pytest

# ---------------------------------------------------------------------------
# Path to show-queues.sh.
#
# The unit test runner (run-unit-tests.sh) sets cwd to team/ before invoking
# pytest, so the script is reachable at scripts/dashboard/show-queues.sh from
# that cwd.  Using an absolute path derived from __file__ keeps the test
# working from any cwd (e.g. direct `pytest` invocations from the repo root).
# ---------------------------------------------------------------------------
_TEAM_DIR = pathlib.Path(__file__).parent.parent.parent  # team/tests/unit/ -> team/
_SHOW_QUEUES = _TEAM_DIR / "scripts" / "dashboard" / "show-queues.sh"
_SHOW_MULTI = _TEAM_DIR / "scripts" / "dashboard" / "show-multi.sh"

# Compact-mode counter row prefixes; none may reach stdout on a refusal.
_COUNTER_LABELS = ("pm:", "coder:", "writer:", "tester:", "cm:")

# Fixture: stable HUMAN-APPROVE task ID for the test
_TASK_ID = "HUMAN-APPROVE-v1.10.0-099"

# The warning string that MUST be absent (green-path test) / present (guard test)
_NO_QUEUE_ENTRY_WARNING = "⚠ no queue entry"

# Project name used in fixture
_PROJ = "fixture-proj"


# ---------------------------------------------------------------------------
# Fixture builder helpers
# ---------------------------------------------------------------------------


def _build_fixture(root: pathlib.Path, *, include_queue_entry: bool) -> None:
    """Populate *root* with the minimal kanban structure needed for the tests.

    Args:
        root:                The tmp_path directory to populate.
        include_queue_entry: When True, write human_backlog.md with a valid
                             '[W]' marker for _TASK_ID.  When False, omit the
                             queue file entirely to trigger the warning path.
    """
    # --- projects.cfg (INI format) ---
    (root / "projects.cfg").write_text(
        f"[project:{_PROJ}]\npriority=1\n",
        encoding="utf-8",
    )

    # --- Task folder: HUMAN-APPROVE task ---
    task_dir = root / "projects" / _PROJ / "tasks" / _TASK_ID
    task_dir.mkdir(parents=True, exist_ok=True)

    # README.md — role must be HUMAN so show-queues routes to the human agent
    (task_dir / "README.md").write_text(
        f"# Task: human gate\n\n## Role\nHUMAN\n",
        encoding="utf-8",
    )

    # status.md — WAITING state (the normal state for a newly injected gate)
    (task_dir / "status.md").write_text(
        "# Status\n\n## State\nWAITING\n",
        encoding="utf-8",
    )

    # --- Queue directory ---
    queues_dir = root / "projects" / _PROJ / "tasks" / "queues"
    queues_dir.mkdir(parents=True, exist_ok=True)

    if include_queue_entry:
        # Valid [W] marker — task has prerequisites so WAITING is the correct
        # initial marker per queue_marker_for_task semantics.
        (queues_dir / "human_backlog.md").write_text(
            f"# HUMAN Backlog\n- [W] {_TASK_ID}\n",
            encoding="utf-8",
        )


def _run_show_queues(tmp_path: pathlib.Path) -> subprocess.CompletedProcess:
    """Invoke show-queues.sh --details --project fixture-proj against tmp_path.

    Returns the completed process so the caller can inspect stdout/stderr and
    the return code.

    Environment:
      PGAI_AGENT_KANBAN_ROOT_PATH -> tmp_path (redirected away from live install)
      TERM=dumb                   -> disables ANSI color codes for easier assertion
    """
    import os

    env = dict(os.environ)
    env["PGAI_AGENT_KANBAN_ROOT_PATH"] = str(tmp_path)
    env["TERM"] = "dumb"
    # Clear variables that the autouse _block_live_kanban_writes fixture may
    # have redirected; the subprocess should derive all paths from our root.
    env.pop("PGAI_TASKS_DIR", None)
    env.pop("PGAI_QUEUE_DIR", None)
    env.pop("PGAI_RELEASE_STATE_PATH", None)

    return subprocess.run(
        ["bash", str(_SHOW_QUEUES), "--details", "--project", _PROJ],
        capture_output=True,
        text=True,
        env=env,
        timeout=30,
    )


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


def test_human_approve_task_with_valid_queue_marker_renders_without_warning(
    tmp_path: pathlib.Path,
) -> None:
    """show-queues --details emits no '⚠ no queue entry' when human_backlog.md has a valid marker.

    Green-path acceptance criterion (an earlier defect):
      A HUMAN-APPROVE task whose human_backlog.md carries a '[W]' marker
      renders in the details view with zero '⚠ no queue entry' occurrences.
    """
    _build_fixture(tmp_path, include_queue_entry=True)

    result = _run_show_queues(tmp_path)

    # Script must exit cleanly
    assert result.returncode == 0, (
        f"show-queues.sh exited with {result.returncode}; "
        f"stderr: {result.stderr!r}"
    )

    # Human section must be present — confirms the task was rendered at all
    assert _TASK_ID in result.stdout, (
        f"Expected {_TASK_ID!r} in output but it was absent.\n"
        f"stdout:\n{result.stdout}"
    )

    # The load-bearing assertion: zero '⚠ no queue entry' occurrences
    assert _NO_QUEUE_ENTRY_WARNING not in result.stdout, (
        f"'⚠ no queue entry' warning present in show-queues output for "
        f"{_TASK_ID!r}; human_backlog.md has a valid marker but the "
        f"dashboard still warns.  Output:\n{result.stdout}"
    )


def test_human_approve_task_without_queue_entry_renders_warning(
    tmp_path: pathlib.Path,
) -> None:
    """show-queues --details emits '⚠ no queue entry' when human_backlog.md is absent.

    Negative guard — proves the green-path test is not vacuously true:
    removing the queue file causes the warning to appear, confirming that
    the absence assertion above is a real guard and not a no-op.
    """
    _build_fixture(tmp_path, include_queue_entry=False)

    result = _run_show_queues(tmp_path)

    # Script must exit cleanly regardless of queue state
    assert result.returncode == 0, (
        f"show-queues.sh exited with {result.returncode}; "
        f"stderr: {result.stderr!r}"
    )

    # Human section must be present
    assert _TASK_ID in result.stdout, (
        f"Expected {_TASK_ID!r} in output but it was absent.\n"
        f"stdout:\n{result.stdout}"
    )

    # The guard assertion: warning IS present when queue entry is missing
    assert _NO_QUEUE_ENTRY_WARNING in result.stdout, (
        f"Expected '⚠ no queue entry' warning in output for {_TASK_ID!r} "
        f"when human_backlog.md is absent, but the warning was not found.\n"
        f"stdout:\n{result.stdout}"
    )


# ---------------------------------------------------------------------------
# --project resolution: exact name, unambiguous prefix, unknown name, and
# the legitimate absent-value modes.
# ---------------------------------------------------------------------------

# A project name with a hyphen boundary so an unambiguous prefix ("resolve")
# is meaningful to test against (pp_resolve_project_name's prefix match
# requires the registered name to continue with a literal hyphen right
# after the typed prefix -- see lib/project_paths.sh).
_RESOLVE_PROJ = "resolve-target"
_RESOLVE_SIBLING = "unrelated-other-project"


def _build_resolve_fixture(root: pathlib.Path) -> None:
    """Register _RESOLVE_PROJ plus an unrelated sibling project, each with
    an empty queues/ directory -- enough for show-queues.sh's compact
    render to produce all-zero counts without erroring."""
    (root / "projects.cfg").write_text(
        f"[project:{_RESOLVE_PROJ}]\npriority=1\n"
        f"[project:{_RESOLVE_SIBLING}]\npriority=2\n",
        encoding="utf-8",
    )
    for name in (_RESOLVE_PROJ, _RESOLVE_SIBLING):
        (root / "projects" / name / "tasks" / "queues").mkdir(
            parents=True, exist_ok=True
        )


def _run_show_queues_argv(
    tmp_path: pathlib.Path, argv: list[str], script: pathlib.Path = _SHOW_QUEUES
) -> subprocess.CompletedProcess:
    """Invoke show-queues.sh (or *script*) with an arbitrary argv against tmp_path.

    Same environment isolation as _run_show_queues, generalised to accept
    the caller's own flag list instead of hardcoding --details --project.
    PGAI_PROJECT_NAME and PGAI_PROJECT_ROOT are cleared so an absent
    --project really names no project.
    """
    import os

    env = dict(os.environ)
    env["PGAI_AGENT_KANBAN_ROOT_PATH"] = str(tmp_path)
    env["KANBAN_ROOT"] = str(tmp_path)
    env["TERM"] = "dumb"
    env.pop("PGAI_TASKS_DIR", None)
    env.pop("PGAI_QUEUE_DIR", None)
    env.pop("PGAI_RELEASE_STATE_PATH", None)
    env.pop("PGAI_PROJECT_NAME", None)
    env.pop("PGAI_PROJECT_ROOT", None)

    return subprocess.run(
        ["bash", str(script), *argv],
        capture_output=True,
        text=True,
        env=env,
        timeout=30,
    )


def test_project_exact_name_and_unambiguous_prefix_render_identical_board(
    tmp_path: pathlib.Path,
) -> None:
    """--project resolve-target (exact) and --project resolve (unambiguous
    prefix) must resolve to the same project and render byte-identical
    compact boards -- the resolver routes a supplied value through exact-
    then-prefix matching before any path is built from it."""
    _build_resolve_fixture(tmp_path)

    exact_result = _run_show_queues_argv(tmp_path, ["--project", _RESOLVE_PROJ])
    prefix_result = _run_show_queues_argv(tmp_path, ["--project", "resolve"])

    assert exact_result.returncode == 0, (
        f"exact-name invocation failed: {exact_result.stderr}"
    )
    assert prefix_result.returncode == 0, (
        f"unambiguous-prefix invocation failed: {prefix_result.stderr}"
    )
    assert prefix_result.stdout == exact_result.stdout, (
        "an unambiguous prefix must render the identical board as the full "
        f"name.\nexact:\n{exact_result.stdout}\nprefix:\n{prefix_result.stdout}"
    )


def test_unregistered_project_name_refuses_dual_channel_and_renders_no_board(
    tmp_path: pathlib.Path,
) -> None:
    """--project <unregistered-name> against a populated registry exits
    non-zero, emits the resolver's dual-channel refusal (error:/message:/
    remedy:), and renders no board at all -- never a full board of zeros
    for a project that was never found."""
    _build_resolve_fixture(tmp_path)

    result = _run_show_queues_argv(
        tmp_path, ["--project", "definitely-unregistered-project-name"]
    )

    assert result.returncode != 0, (
        f"expected a non-zero exit for an unregistered project name; "
        f"stdout:\n{result.stdout}\nstderr:\n{result.stderr}"
    )
    assert "error:" in result.stderr, result.stderr
    assert "message:" in result.stderr, result.stderr
    assert "remedy:" in result.stderr, result.stderr
    # No board rendered: none of the compact-mode agent rows, and no
    # "Active provider" preamble line, ever reach stdout.
    assert result.stdout == "", (
        f"expected no board rendered for an unresolved project; "
        f"stdout:\n{result.stdout}"
    )


def _assert_no_project_refusal(result: subprocess.CompletedProcess) -> None:
    """Exit 1, the show_queues.no_project dual-channel block on stderr, and
    nothing at all on stdout -- no provider line, no counter row."""
    assert result.returncode == 1, (
        f"expected exit 1 with no project named; "
        f"stdout:\n{result.stdout}\nstderr:\n{result.stderr}"
    )
    lines = result.stderr.splitlines()
    assert "error:   show_queues.no_project" in lines, result.stderr
    message = next(line for line in lines if line.startswith("message: "))
    remedy = next(line for line in lines if line.startswith("remedy:  "))
    assert "pass --project <name> or set PGAI_PROJECT_NAME" in message
    assert "--project " in remedy and "<" not in remedy
    assert result.stdout == ""
    assert not any(label in result.stdout for label in _COUNTER_LABELS)


def test_absent_project_flag_refuses_compact_board_on_a_registered_root(
    tmp_path: pathlib.Path,
) -> None:
    """No --project and no PGAI_PROJECT_NAME on a multi-project root:
    compact mode refuses rather than print counters it never read, and the
    remedy names a registered project to pass."""
    _build_resolve_fixture(tmp_path)

    result = _run_show_queues_argv(tmp_path, [])

    _assert_no_project_refusal(result)
    assert f"--project {_RESOLVE_PROJ}" in result.stderr


def test_absent_project_flag_with_details_iterates_every_registered_project(
    tmp_path: pathlib.Path,
) -> None:
    """--details with no --project still walks every registered project
    (the iterate-all branch): a task in each project is listed."""
    _build_resolve_fixture(tmp_path)
    task_ids = {}
    for number, name in enumerate((_RESOLVE_PROJ, _RESOLVE_SIBLING), start=1):
        task_id = f"CODER-20260101-00{number}-in-{name}"
        task_dir = tmp_path / "projects" / name / "tasks" / task_id
        task_dir.mkdir(parents=True)
        (task_dir / "status.md").write_text("## State\nBACKLOG\n", encoding="utf-8")
        (task_dir / "README.md").write_text("## Role\nCODER\n", encoding="utf-8")
        (tmp_path / "projects" / name / "tasks" / "queues" / "coder_backlog.md").write_text(
            f"- [ ] {task_id}\n", encoding="utf-8"
        )
        task_ids[name] = task_id

    result = _run_show_queues_argv(tmp_path, ["--details"])

    assert result.returncode == 0, (
        f"show-queues.sh --details with no --project failed: {result.stderr}"
    )
    listed = result.stdout.splitlines()
    for name, task_id in task_ids.items():
        assert f"  {task_id}" in listed, f"{name}'s task missing:\n{result.stdout}"


# ---------------------------------------------------------------------------
# Registry-less root: pp_resolve_project_name passes an unresolvable name
# through unchanged, by documented design, when projects.cfg does not exist
# at all -- the directory-existence backstop in ops.read.get_queues (the
# function show-queues.sh renders through) is what must still refuse it here.
# ---------------------------------------------------------------------------


def test_unresolvable_project_name_on_registryless_root_refuses_and_renders_no_board(
    tmp_path: pathlib.Path,
) -> None:
    """--project <any-name> against a kanban root with no projects.cfg at
    all exits non-zero, emits a dual-channel refusal, and renders no
    board -- the resolver's registry-less pass-through means this backstop,
    not the resolver's own not-found refusal, is what must catch it."""
    # Deliberately no projects.cfg and no projects/ directory anywhere
    # under tmp_path -- a genuinely registry-less root.
    result = _run_show_queues_argv(tmp_path, ["--project", "bogus-name"])

    assert result.returncode != 0, (
        f"expected a non-zero exit on a registry-less root; "
        f"stdout:\n{result.stdout}\nstderr:\n{result.stderr}"
    )
    assert "error:" in result.stderr, result.stderr
    assert "message:" in result.stderr, result.stderr
    assert "remedy:" in result.stderr, result.stderr
    # No board rendered: none of the compact-mode agent rows, and no
    # "Active provider" preamble line, ever reach stdout.
    assert result.stdout == "", (
        f"expected no board rendered on a registry-less root; "
        f"stdout:\n{result.stdout}"
    )


def _build_single_project_root(root: pathlib.Path) -> None:
    """A single-project root: no projects.cfg, queues at
    <root>/tasks/queues/ -- coder_backlog.md holding one BACKLOG and one
    DONE entry, each with its task folder."""
    for task_id, state, marker in (
        ("CODER-20260101-001-alpha", "BACKLOG", " "),
        ("CODER-20260101-002-beta", "DONE", "x"),
    ):
        task_dir = root / "tasks" / task_id
        task_dir.mkdir(parents=True)
        (task_dir / "status.md").write_text(f"# Status\n\n## State\n{state}\n", encoding="utf-8")
        queue = root / "tasks" / "queues" / "coder_backlog.md"
        queue.parent.mkdir(parents=True, exist_ok=True)
        with queue.open("a", encoding="utf-8") as handle:
            handle.write(f"- [{marker}] {task_id}\n")


def test_absent_project_flag_refuses_on_a_single_project_root_with_real_queues(
    tmp_path: pathlib.Path,
) -> None:
    """A root with no projects.cfg whose tasks/queues/coder_backlog.md
    holds real entries: the board reads registered project directories
    only, so compact mode refuses the same way rather than report those
    entries as 0/0; --details (nothing registered to walk) still exits 0."""
    _build_single_project_root(tmp_path)

    _assert_no_project_refusal(_run_show_queues_argv(tmp_path, []))

    details_result = _run_show_queues_argv(tmp_path, ["--details"])
    assert details_result.returncode == 0, (
        f"--details mode failed on a registry-less root: {details_result.stderr}"
    )


def test_show_multi_registryless_queues_fallback_surfaces_the_refusal(
    tmp_path: pathlib.Path,
) -> None:
    """show-multi.sh --mode queues on a root with no projects.cfg hands
    off to show-queues.sh with no --project; the refusal and its exit
    status come straight through, and no counter row is printed."""
    _build_single_project_root(tmp_path)

    result = _run_show_queues_argv(tmp_path, ["--mode", "queues"], script=_SHOW_MULTI)

    _assert_no_project_refusal(result)
