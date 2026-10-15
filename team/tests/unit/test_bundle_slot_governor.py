"""
test_bundle_slot_governor.py
============================
Which bundle holds discovery's bundling slot, and which is reported stuck:
``_disc_pending_bundle_of_type`` in team/scripts/lib/discovery_steps.sh and
the answer it reads, ``pgai_agent_kanban.lib.bundle_items.pending_bundle``.

  - A bundle an RC unwind renamed ``*.SUPERSEDED-on-cancel-*`` never holds
    the slot, even while its file still reads ``## Status running``.
  - ``## Status`` is read through the canonical state reader: a legacy
    ``superseded`` status reads as ``wont-do`` and frees the slot.
  - A ``running`` bundle whose ``## PM Task`` is WONT-DO or DONE and whose
    version is not the Active RC is stuck: it does not hold the slot, and a
    dual-channel ``discovery.bundle-stuck`` report names it with a remedy
    that, run as printed, closes it.
  - A ``running`` bundle still on its way -- PM task live, its version the
    Active RC, or a fact needed to tell unknown -- keeps holding the slot.

Every fixture is a throwaway kanban root under pytest's tmp_path; the bash
runs get an environment built from an explicit set naming that root.
"""

from __future__ import annotations

import pathlib
import shlex
import subprocess
import sys

import pytest

from pgai_agent_kanban.lib import bundle_items
from pgai_agent_kanban.lib.error_contract import parse_error_contract
from tests.fixtures.scrubbed_subprocess_env import build_scrubbed_env
from tests.unit.shell_harness import run_bash

_TEAM_DIR = pathlib.Path(__file__).resolve().parents[2]
_STATE_LIB = "scripts/lib/discovery_state.sh"
_STEPS_LIB = "scripts/lib/discovery_steps.sh"
_PROJECT = "slot-fixture"
_PM_TASK = "PM-20260101-001-decompose-v1-2-3-bugfix-bundle"
_BUNDLE = "v1.2.3-bugfix-bundle-20260101.md"


def _root(tmp_path: pathlib.Path, active_rc: str = "none") -> pathlib.Path:
    """A registered one-project kanban root; returns the project directory."""
    root = tmp_path / "kanban"
    project = root / "projects" / _PROJECT
    for sub in ("requirements", "bugs", "priority", "tasks/queues"):
        (project / sub).mkdir(parents=True, exist_ok=True)
    (root / "projects.cfg").write_text(f"[project:{_PROJECT}]\npriority=1\n", encoding="utf-8")
    (project / "project.cfg").write_text(
        f"[project]\nproject_name = {_PROJECT}\nworkflow_type = release\n", encoding="utf-8"
    )
    _release_state(project, active_rc)
    return project


def _release_state(project: pathlib.Path, active_rc: str) -> None:
    opened = ("none", "none") if active_rc == "none" else ("2026-01-01T00:00:00+00:00", "CM-20260101-000-open")
    (project / "release-state.md").write_text(
        f"# Release State\n\n## Active RC\n{active_rc}\n\n## RC Opened At\n{opened[0]}\n\n"
        f"## RC Opened By Task\n{opened[1]}\n\n## Last Released\nv1.2.2\n",
        encoding="utf-8",
    )


def _bundle(
    project: pathlib.Path,
    name: str = _BUNDLE,
    status: str = "running",
    pm_task: str = _PM_TASK,
    target: str = "v1.2.3",
) -> pathlib.Path:
    path = project / "requirements" / name
    path.write_text(
        f"# Requirements: {target} -- BUG bundle\n\n## Status\n{status}\n\n## PM Task\n\n{pm_task}\n\n"
        f"## Target Version\n\n{target}\n\n## Bundled Items\n\n- BUG-0001-x.md "
        f"(`{project / 'bugs' / 'BUG-0001-x.md'}`)\n",
        encoding="utf-8",
    )
    return path


def _pm_task(project: pathlib.Path, state: str, task_id: str = _PM_TASK) -> None:
    task = project / "tasks" / task_id
    task.mkdir(parents=True, exist_ok=True)
    (task / "status.md").write_text(f"# Status\n\n## State\n{state}\n\n## Summary\nx\n", encoding="utf-8")


def _env(project: pathlib.Path) -> dict:
    root = project.parents[1]
    (root / "tmp").mkdir(exist_ok=True)
    home = root / "home"
    home.mkdir(exist_ok=True)
    return build_scrubbed_env(
        home_dir=home,
        extra_path_dirs=[pathlib.Path(sys.executable).parent],
        extra_env={
            "KANBAN_ROOT": str(root),
            "PGAI_AGENT_KANBAN_ROOT_PATH": str(root),
            "PGAI_AGENT_KANBAN_TEMP_DIR": str(root / "tmp"),
        },
    )


def _query(tmp_path: pathlib.Path, project: pathlib.Path, type_slug: str = "bugfix"):
    return run_bash(
        tmp_path,
        f"source {_STATE_LIB} && source {_STEPS_LIB} && "
        f"_disc_pending_bundle_of_type '{project / 'requirements'}' '{type_slug}'",
        base_env=_env(project),
    )


# ---------------------------------------------------------------------------
# Superseded bundles never hold the slot
# ---------------------------------------------------------------------------


def test_a_superseded_name_frees_the_slot_even_while_its_file_reads_running(tmp_path) -> None:
    project = _root(tmp_path)
    _bundle(project, "v1.2.3-bugfix-bundle-20260101.SUPERSEDED-on-cancel-2026-01-02T00:00:00+00:00.md")
    _pm_task(project, "BACKLOG")

    result = _query(tmp_path, project)

    assert result.returncode == 1, result.stdout + result.stderr
    assert result.stdout == ""


def test_a_legacy_superseded_status_reads_as_wont_do_and_frees_the_slot(tmp_path) -> None:
    project = _root(tmp_path)
    _bundle(project, status="superseded")

    result = _query(tmp_path, project)

    assert result.returncode == 1, result.stdout + result.stderr
    assert result.stdout == ""


def test_a_live_bundle_beside_a_superseded_one_still_holds_the_slot(tmp_path) -> None:
    project = _root(tmp_path)
    _bundle(project, "v1.2.3-bugfix-bundle-20260101.SUPERSEDED-on-cancel-2026-01-02T00:00:00+00:00.md")
    live = _bundle(project, "v1.2.4-bugfix-bundle-20260103.md", status="open", target="v1.2.4")

    result = _query(tmp_path, project)

    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == str(live)


# ---------------------------------------------------------------------------
# Stuck bundles are reported, not pending
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("pm_state", ["WONT-DO", "DONE"])
def test_a_running_bundle_whose_pm_task_ended_outside_the_active_rc_is_reported_stuck(
    tmp_path, pm_state: str
) -> None:
    project = _root(tmp_path, active_rc="none")
    bundle = _bundle(project)
    _pm_task(project, pm_state)

    result = _query(tmp_path, project)

    assert result.returncode == 1, result.stdout + result.stderr
    assert result.stdout == "", "a stuck bundle must not be echoed as the pending holder"
    fields = parse_error_contract(result.stderr)
    assert fields is not None, result.stderr
    assert fields["error"] == "discovery.bundle-stuck"
    assert str(bundle) in fields["message"] and _PM_TASK in fields["message"] and pm_state in fields["message"]
    assert "<" not in fields["remedy"], "the remedy must carry no placeholder"
    assert f"--project {_PROJECT} --key {bundle.stem} --state wont-do" in fields["remedy"]


def test_the_stuck_remedy_run_as_printed_closes_the_bundle_and_frees_its_items(tmp_path) -> None:
    project = _root(tmp_path, active_rc="v2.0.0")
    bundle = _bundle(project, target="v1.2.3")
    item = project / "bugs" / "BUG-0001-x.md"
    item.write_text("# BUG-0001-x\n\n## Status\nrunning\n", encoding="utf-8")
    _pm_task(project, "WONT-DO")
    remedy = parse_error_contract(_query(tmp_path, project).stderr)["remedy"]

    run = subprocess.run(
        ["bash", "-c", remedy], capture_output=True, text=True, env=_env(project),
        cwd=str(tmp_path), timeout=60,
    )

    assert run.returncode == 0, run.stdout + run.stderr
    assert bundle_items.pending_bundle(project / "requirements", "bugfix").stuck == ()
    assert "## Status\nwont-do" in bundle.read_text(encoding="utf-8")
    assert bundle_items.carried_item_names(project / "requirements") == frozenset({item.name})


def test_a_stuck_bundle_does_not_hide_a_later_pending_one(tmp_path) -> None:
    project = _root(tmp_path)
    _bundle(project)
    _pm_task(project, "WONT-DO")
    live = _bundle(project, "v1.2.4-bugfix-bundle-20260103.md", status="open", target="v1.2.4")

    result = _query(tmp_path, project)

    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == str(live)
    assert "discovery.bundle-stuck" in result.stderr


# ---------------------------------------------------------------------------
# A running bundle on its way keeps the slot
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("active_rc", "pm_state", "make_task"),
    [
        ("none", "WORKING", True),     # PM still decomposing
        ("v1.2.3", "DONE", True),      # its RC is open
        ("none", "DONE", False),       # PM task folder gone: unknown
    ],
    ids=["pm-task-live", "version-is-the-active-rc", "pm-task-folder-missing"],
)
def test_a_running_bundle_that_may_still_ship_holds_the_slot(
    tmp_path, active_rc: str, pm_state: str, make_task: bool
) -> None:
    project = _root(tmp_path, active_rc=active_rc)
    bundle = _bundle(project)
    if make_task:
        _pm_task(project, pm_state)

    result = _query(tmp_path, project)

    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == str(bundle)
    assert "discovery.bundle-stuck" not in result.stderr


def test_an_unreadable_release_state_never_turns_a_bundle_stuck(tmp_path) -> None:
    project = _root(tmp_path)
    (project / "release-state.md").write_text("# Release State\n\n## Active RC\nNONE\n", encoding="utf-8")
    bundle = _bundle(project)
    _pm_task(project, "WONT-DO")

    answer = bundle_items.pending_bundle(project / "requirements", "bugfix")

    assert answer.pending == bundle and answer.stuck == ()


# ---------------------------------------------------------------------------
# The module answer and its command
# ---------------------------------------------------------------------------


def test_pending_command_prints_stuck_lines_then_the_holder(tmp_path) -> None:
    project = _root(tmp_path)
    stuck = _bundle(project)
    _pm_task(project, "WONT-DO")
    live = _bundle(project, "v1.2.4-bugfix-bundle-20260103.md", status=" Open ", target="v1.2.4")
    _bundle(project, "v1.2.5-priority-bundle-20260104.md", status="open", target="v1.2.5")

    run = subprocess.run(
        [sys.executable, "-m", "pgai_agent_kanban.lib.bundle_items", "pending",
         str(project / "requirements"), "bugfix"],
        capture_output=True, text=True, cwd=str(_TEAM_DIR), env=_env(project), timeout=60,
    )

    assert run.returncode == 0, run.stderr
    assert run.stdout.splitlines() == [
        f"stuck\t{stuck}\t{_PM_TASK}\twont-do\tv1.2.3",
        f"pending\t{live}",
    ]


def test_the_governor_names_the_project_given_by_its_caller(tmp_path) -> None:
    project = _root(tmp_path)
    _bundle(project)
    _pm_task(project, "WONT-DO")
    requirements = shlex.quote(str(project / "requirements"))

    result = run_bash(
        tmp_path,
        f"source {_STATE_LIB} && source {_STEPS_LIB} && "
        f"_disc_pending_bundle_of_type {requirements} bugfix other-name "
        f"{shlex.quote(str(project / 'tasks'))} {shlex.quote(str(project / 'release-state.md'))}",
        base_env=_env(project),
    )

    assert result.returncode == 1
    assert "--project other-name " in parse_error_contract(result.stderr)["remedy"]
