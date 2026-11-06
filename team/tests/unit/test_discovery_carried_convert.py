"""
test_discovery_carried_convert.py
=================================
The discovery item scan leaves needs-operator and carried items out, and
``pgai_agent_kanban.discovery.carried`` parks each carried item on an
operator question:

  - ``python3 -m pgai_agent_kanban.discovery.items scan`` prints no path for
    a needs-operator or a carried bug and names each on a stderr skip line;
  - ``carried_items`` finds a ``running`` item only when every bundle
    listing it is closed, names the newest such bundle by version, and
    reads the reason from that bundle's PM task status.md;
  - the inbox question names the item, the bundle and the reason, or says
    no reason was recorded;
  - ``convert`` writes the status and one inbox entry, prints nothing on
    stdout, writes nothing on a second run, and leaves the item ``running``
    with a WARNING when it cannot convert.

Every fixture is a throwaway kanban root under pytest's tmp_path, and every
subprocess runs with all five kanban-root variables set or cleared.
Fixture identifiers use the fictitious BUG-9xxx / PRIORITY-9xxx range.
"""

from __future__ import annotations

import os
import pathlib
import subprocess
import sys

import pytest

from pgai_agent_kanban.discovery import carried
from pgai_agent_kanban.ops.context import OpsContext
from pgai_agent_kanban.overwatch import inbox

_TEAM_DIR = pathlib.Path(__file__).resolve().parents[2]
_PROJECT = "carried-unit"
_PM_TASK = "PM-20990101-001-decompose-v9-9-9-bugfix-bundle"


def _root(tmp_path: pathlib.Path, project: str = _PROJECT) -> pathlib.Path:
    """A sandbox kanban root holding one project's intake directories."""
    root = tmp_path / "kanban"
    project_root = root / "projects" / project
    for sub in ("bugs", "priority", "requirements", "tasks/queues"):
        (project_root / sub).mkdir(parents=True, exist_ok=True)
    (root / "locks").mkdir(exist_ok=True)
    (root / "tmp").mkdir(exist_ok=True)
    (root / "projects.cfg").write_text(f"[project:{project}]\npriority = 1\nenabled = true\n", encoding="utf-8")
    return project_root


def _env(root: pathlib.Path) -> dict[str, str]:
    env = dict(os.environ)
    env.pop("PGAI_TASKS_DIR", None)
    env.pop("PGAI_PROJECT_ROOT", None)
    env["KANBAN_ROOT"] = str(root)
    env["PGAI_AGENT_KANBAN_ROOT_PATH"] = str(root)
    env["PGAI_AGENT_KANBAN_TEMP_DIR"] = str(root / "tmp")
    env["PYTHONPATH"] = str(_TEAM_DIR)
    return env


def _item(project_root: pathlib.Path, subdir: str, name: str, status: str) -> pathlib.Path:
    path = project_root / subdir / name
    path.write_text(
        f"# {name[:-3]}\n\n**Severity:** medium\n\n## Status\n{status}\n\n## Symptom\nsynthetic\n", encoding="utf-8"
    )
    return path


def _bundle(
    project_root: pathlib.Path, name: str, status: str, items: list[pathlib.Path], pm_task: str = "none"
) -> pathlib.Path:
    path = project_root / "requirements" / name
    lines = "".join(f"- {item.name} (`{item}`)\n" for item in items)
    path.write_text(
        f"# {name[:-3]}\n\n## Status\n{status}\n\n## PM Task\n{pm_task}\n\n## Bundled Items\n\n{lines}",
        encoding="utf-8",
    )
    return path


def _pm_status(project_root: pathlib.Path, summary: str, task: str = _PM_TASK) -> None:
    folder = project_root / "tasks" / task
    folder.mkdir(parents=True, exist_ok=True)
    (folder / "status.md").write_text(f"## State\nDONE\n\n## Summary\n{summary}\n", encoding="utf-8")


def _status(path: pathlib.Path) -> str:
    lines = path.read_text(encoding="utf-8").splitlines()
    index = next(i for i, line in enumerate(lines) if line.strip().lower() == "## status")
    return next(line.strip() for line in lines[index + 1 :] if line.strip())


def _run(root: pathlib.Path, module: str, *args: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, "-m", module, *args],
        capture_output=True,
        text=True,
        env=_env(root),
        cwd=str(root),
        timeout=60,
    )


def _open_entries(project_root: pathlib.Path):
    return inbox.list_open(OpsContext(kanban_root=project_root.parent.parent), project_root.name)


def _tree_bytes(project_root: pathlib.Path) -> dict[str, bytes]:
    return {str(f): f.read_bytes() for f in sorted(project_root.rglob("*")) if f.is_file()}


# ---------------------------------------------------------------------------
# The item scan
# ---------------------------------------------------------------------------


def test_scan_prints_no_path_for_a_needs_operator_bug_and_names_it_on_stderr(tmp_path) -> None:
    project_root = _root(tmp_path)
    waiting = _item(project_root, "bugs", "BUG-9401-waiting-thing.md", "needs-operator")
    open_bug = _item(project_root, "bugs", "BUG-9402-open-thing.md", "open")

    result = _run(
        project_root.parent.parent, "pgai_agent_kanban.discovery.items", "scan", str(project_root / "bugs"), "BUG-"
    )

    assert result.returncode == 0, result.stderr
    assert result.stdout.splitlines() == [str(open_bug.resolve())]
    skips = [line for line in result.stderr.splitlines() if line.startswith(f"skipping {waiting.name}:")]
    assert len(skips) == 1, result.stderr
    assert "needs-operator" in skips[0]


def test_scan_leaves_a_carried_bug_out_and_writes_nothing(tmp_path) -> None:
    project_root = _root(tmp_path)
    bug = _item(project_root, "bugs", "BUG-9403-carried-thing.md", "running")
    _bundle(project_root, "v9.9.9-bugfix-bundle-20990101.md", "done", [bug])
    before = _tree_bytes(project_root)

    result = _run(
        project_root.parent.parent, "pgai_agent_kanban.discovery.items", "scan", str(project_root / "bugs"), "BUG-"
    )

    assert result.returncode == 0, result.stderr
    assert result.stdout == ""
    assert f"skipping {bug.name}: carried" in result.stderr
    assert _tree_bytes(project_root) == before


# ---------------------------------------------------------------------------
# Which items are carried, and the question
# ---------------------------------------------------------------------------


def test_carried_items_reads_the_newest_closed_bundle_and_its_pm_reason(tmp_path) -> None:
    project_root = _root(tmp_path)
    bug = _item(project_root, "bugs", "BUG-9404-carried-thing.md", "running")
    in_flight = _item(project_root, "bugs", "BUG-9405-in-flight-thing.md", "running")
    orphan = _item(project_root, "bugs", "BUG-9406-orphan-thing.md", "running")
    fixed = _item(project_root, "bugs", "BUG-9407-fixed-thing.md", "done")
    _bundle(project_root, "v9.9.9-bugfix-bundle-20990101.md", "done", [bug, fixed], "PM-20990101-009-older")
    # v9.9.10 is newer than v9.9.9 by version, though it sorts first as text.
    _bundle(project_root, "v9.9.10-bugfix-bundle-20990102.md", "wont-do", [bug, in_flight], _PM_TASK)
    _bundle(project_root, "v9.9.11-bugfix-bundle-20990103.md", "running", [in_flight])
    _pm_status(
        project_root,
        "Agent: PM. One ticket written. BUG-9404 is left out: the operator must pick the "
        "port. BUG-94041 is unrelated. Nothing else.",
    )

    found = carried.carried_items(project_root / "bugs")

    assert [item.path.name for item in found] == [bug.name]
    [item] = found
    assert item.bundle.name == "v9.9.10-bugfix-bundle-20990102.md"
    assert item.pm_task == _PM_TASK
    assert item.reason == "BUG-9404 is left out: the operator must pick the port."
    assert orphan.name not in {i.path.name for i in found}


@pytest.mark.parametrize(
    ("pm_task", "summary", "expected"),
    [
        (
            _PM_TASK,
            "Agent: PM. BUG-9408 needs the operator's retry limit.",
            f"Reason recorded by PM task {_PM_TASK}: BUG-9408 needs the operator's retry limit.",
        ),
        (
            _PM_TASK,
            "Agent: PM. Plan written.",
            f"No reason was recorded: PM task {_PM_TASK}'s status.md does not name BUG-9408.",
        ),
        ("none", None, "No reason was recorded: bundle v9.9.9-bugfix-bundle-20990101.md names no PM task."),
    ],
    ids=["reason-recorded", "pm-task-silent", "no-pm-task"],
)
def test_question_names_the_item_the_bundle_and_the_reason(tmp_path, pm_task, summary, expected) -> None:
    project_root = _root(tmp_path)
    bug = _item(project_root, "bugs", "BUG-9408-carried-thing.md", "running")
    _bundle(project_root, "v9.9.9-bugfix-bundle-20990101.md", "done", [bug], pm_task)
    if summary is not None:
        _pm_status(project_root, summary)

    [item] = carried.carried_items(project_root / "bugs")
    question = carried.carried_question(item)

    assert question.startswith(f"{bug.stem} was carried: bundle v9.9.9-bugfix-bundle-20990101.md closed")
    assert expected in question, question


def test_a_long_reason_is_cut_at_a_word_boundary(tmp_path) -> None:
    reason = carried.carried_reason("## Summary\nBUG-9409 " + "word " * 400 + "end.\n", "BUG-9409-x.md")

    assert len(reason) <= carried.REASON_MAX_CHARS + 4
    assert reason.startswith("BUG-9409 word") and reason.endswith(" ...")


# ---------------------------------------------------------------------------
# The conversion
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("subdir", "name", "bundle_name"),
    [
        ("bugs", "BUG-9410-carried-thing.md", "v9.9.9-bugfix-bundle-20990101.md"),
        ("priority", "PRIORITY-9410-carried-thing.md", "v9.9.9-priority-bundle-20990101.md"),
    ],
    ids=["bug", "priority"],
)
def test_convert_parks_the_item_once_and_a_second_run_writes_nothing(tmp_path, subdir, name, bundle_name) -> None:
    project_root = _root(tmp_path)
    root = project_root.parent.parent
    item = _item(project_root, subdir, name, "running")
    _bundle(project_root, bundle_name, "done", [item], _PM_TASK)
    item_id = "-".join(name.split("-")[:2])
    _pm_status(project_root, f"Agent: PM. {item_id} waits on the operator's choice of port.")

    first = _run(root, "pgai_agent_kanban.discovery.carried", "convert", str(project_root / subdir))

    assert first.returncode == 0, first.stderr
    assert first.stdout == ""
    assert f"converted {name} to needs-operator" in first.stderr
    assert _status(item) == "needs-operator"
    [entry] = _open_entries(project_root)
    assert entry.source == item.stem
    assert bundle_name in entry.question
    assert "waits on the operator's choice of port." in entry.question

    before = _tree_bytes(project_root)
    second = _run(root, "pgai_agent_kanban.discovery.carried", "convert", str(project_root / subdir))

    assert second.returncode == 0, second.stderr
    assert second.stdout == "" and second.stderr == ""
    assert _tree_bytes(project_root) == before


def test_convert_outside_a_projects_layout_warns_and_writes_nothing(tmp_path) -> None:
    project_root = tmp_path / "loose"
    for sub in ("bugs", "requirements"):
        (project_root / sub).mkdir(parents=True)
    bug = _item(project_root, "bugs", "BUG-9411-carried-thing.md", "running")
    _bundle(project_root, "v9.9.9-bugfix-bundle-20990101.md", "done", [bug])
    before = _tree_bytes(project_root)

    result = _run(tmp_path, "pgai_agent_kanban.discovery.carried", "convert", str(project_root / "bugs"))

    assert result.returncode == 0, result.stderr
    assert result.stdout == ""
    assert result.stderr.startswith("WARNING: cannot convert carried item(s) BUG-9411-carried-thing.md")
    assert _tree_bytes(project_root) == before


def test_a_refused_conversion_leaves_the_item_running_and_names_it(tmp_path) -> None:
    # The operator inbox refuses a project name with '_', so the verb puts
    # the status back and the conversion narrates the failure.
    project_root = _root(tmp_path, project="carried_unit")
    root = project_root.parent.parent
    bug = _item(project_root, "bugs", "BUG-9412-carried-thing.md", "running")
    _bundle(project_root, "v9.9.9-bugfix-bundle-20990101.md", "done", [bug])

    result = _run(root, "pgai_agent_kanban.discovery.carried", "convert", str(project_root / "bugs"))

    assert result.returncode == 0, result.stderr
    assert result.stdout == ""
    warnings = [line for line in result.stderr.splitlines() if line.startswith("WARNING:")]
    assert len(warnings) == 1 and bug.name in warnings[0], result.stderr
    assert "inbox.bad_project" in result.stderr
    assert _status(bug) == "running"
    assert not (project_root / "operator-inbox.md").exists()


def test_list_prints_each_carried_item_and_its_bundle(tmp_path) -> None:
    project_root = _root(tmp_path)
    bug = _item(project_root, "bugs", "BUG-9413-carried-thing.md", "running")
    bundle = _bundle(project_root, "v9.9.9-bugfix-bundle-20990101.md", "done", [bug])

    result = _run(project_root.parent.parent, "pgai_agent_kanban.discovery.carried", "list", str(project_root / "bugs"))

    assert result.returncode == 0, result.stderr
    assert result.stdout.splitlines() == [f"{bug.resolve()}\t{bundle}"]


def test_a_wrong_argument_count_prints_usage_and_exits_2(tmp_path) -> None:
    result = _run(tmp_path, "pgai_agent_kanban.discovery.carried", "convert")

    assert result.returncode == 2
    assert "usage: python3 -m pgai_agent_kanban.discovery.carried convert DIR" in result.stderr
