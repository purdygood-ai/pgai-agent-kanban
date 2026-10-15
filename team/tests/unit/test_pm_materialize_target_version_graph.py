"""
test_pm_materialize_target_version_graph.py
===========================================
The materializer (team/pm-agent/pm_materialize.py) only ever writes a plan
whose version is explicit and whose task graph can actually run:

  - a release plan whose target_version is ``auto``, ``next-patch`` or
    empty is refused dual-channel and writes no task folder (discovery
    writes the explicit version into the requirements file; the
    materializer never resolves one), while a workflow without release
    bookends reads an absent value as ``none``;
  - the dev tree comes from the project's own project.cfg even when
    PGAI_DEV_TREE_PATH names another repository;
  - when PM emits its own TESTER, or a CM task whose slug says "release",
    the one surviving closer never lists itself as a prerequisite, the
    injected TESTER never waits on CM-release, and CM-release lists no
    removed duplicate;
  - a depends_on cycle, a depends_on naming no task's sequence, or a task
    depending on itself exits non-zero naming the tasks involved and writes
    no folder.

The end-to-end cases run the real CLI entry point as a subprocess, the way
pm-agent.sh's ticket tells PM to, against a throwaway --team-root under
pytest's tmp_path; the graph helpers are also covered directly.
"""

from __future__ import annotations

import json
import pathlib
import subprocess
import sys

import pytest

from pgai_agent_kanban.lib.error_contract import parse_error_contract
from pgai_agent_kanban.lib.md_fields import section_body
from tests.fixtures.scrubbed_subprocess_env import build_scrubbed_env

try:
    import pm_agent.pm_materialize as pm  # installed via pm_agent package
except ImportError:
    sys.path.insert(0, str(pathlib.Path(__file__).parent.parent.parent / "pm-agent"))
    import pm_materialize as pm  # type: ignore[no-redef]

_TEAM_DIR = pathlib.Path(__file__).parent.parent.parent  # team/
_DEV_TREE_ROOT = _TEAM_DIR.parent
_MATERIALIZE_SCRIPT = _TEAM_DIR / "pm-agent" / "pm_materialize.py"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _plan_task(sequence, slug, role="CODER", depends_on=None):
    """One plan-JSON task entry (pre-ID-assignment schema)."""
    return {
        "sequence": sequence,
        "slug": slug,
        "title": slug.replace("-", " ").title(),
        "role": role,
        "assigned_agent": role.lower(),
        "working_directory": "REPLACED_BY_RUN",
        "git_repo": "none",
        "source_branch": "none",
        "goal": "Do the work.",
        "inputs": [],
        "context_paths": [],
        "required_output": "Done.",
        "constraints": [],
        "acceptance_criteria": ["It works."],
        "depends_on": depends_on or [],
        "notes": "none",
    }


def _release_plan(tasks, target_version="v1.4.2", workflow_type="release"):
    return {
        "workflow_type": workflow_type,
        "target_version": target_version,
        "test_required": "true",
        "human_approval_required": "auto",
        "project_name": "graph-fixture",
        "tasks": tasks,
    }


def _materialize(plan: dict, tmp_path: pathlib.Path, extra_env=None):
    """Run the real materializer CLI on *plan*; return (result, team_root)."""
    team_root = tmp_path / "team_root"
    team_root.mkdir(exist_ok=True)
    workspace = tmp_path / "workspace"
    workspace.mkdir(exist_ok=True)
    for task in plan.get("tasks", []):
        task["working_directory"] = str(workspace)
    plan_path = tmp_path / "plan.json"
    plan_path.write_text(json.dumps(plan), encoding="utf-8")
    env = build_scrubbed_env(
        home_dir=tmp_path / "home",
        extra_env={
            "PGAI_AGENT_KANBAN_ROOT_PATH": str(_DEV_TREE_ROOT),
            "PGAI_AGENT_KANBAN_TEMP_DIR": str(tmp_path / "scratch"),
            **(extra_env or {}),
        },
    )
    result = subprocess.run(
        [sys.executable, str(_MATERIALIZE_SCRIPT), str(plan_path),
         "--team-root", str(team_root), "--owner", "CLAUDE"],
        capture_output=True, text=True, env=env, timeout=120,
    )
    return result, team_root


def _task_dirs(team_root: pathlib.Path) -> list:
    tasks = team_root / "tasks"
    if not tasks.is_dir():
        return []
    return sorted(d for d in tasks.iterdir() if d.is_dir() and d.name not in ("queues",))


def _prereqs(task_dir: pathlib.Path) -> list:
    """The task IDs a materialized README lists under ## Prerequisites."""
    body = section_body((task_dir / "README.md").read_text(encoding="utf-8"), "Prerequisites") or ""
    return [line.strip()[2:].strip() for line in body.splitlines() if line.strip().startswith("- ")]


def _only(dirs: list, role: str, contains: str = "") -> pathlib.Path:
    matches = [d for d in dirs if d.name.startswith(f"{role}-") and contains in d.name]
    assert len(matches) == 1, f"expected exactly one {role} task containing {contains!r}, got {[d.name for d in dirs]}"
    return matches[0]


def _refusal(result) -> dict:
    parsed = parse_error_contract(result.stderr[result.stderr.find("error:"):])
    assert parsed is not None, f"no dual-channel refusal on stderr:\n{result.stderr}"
    return parsed


def _assert_wrote_nothing(result, team_root: pathlib.Path) -> None:
    assert _task_dirs(team_root) == [], (
        f"a refused plan must write no task folder; found {[d.name for d in _task_dirs(team_root)]}\n"
        f"stderr:\n{result.stderr}"
    )
    queues = team_root / "tasks" / "queues"
    queued = [
        f"{backlog.name}: {line}"
        for backlog in (sorted(queues.glob("*_backlog.md")) if queues.is_dir() else [])
        for line in backlog.read_text(encoding="utf-8").splitlines()
        if line.lstrip().startswith("- [")
    ]
    assert queued == [], f"a refused plan must queue nothing; found {queued}"


def _git(repo: pathlib.Path, *args: str) -> None:
    subprocess.run(
        ["git", "-C", str(repo), *args], check=True, capture_output=True, text=True,
        env=build_scrubbed_env(home_dir=repo.parent / "githome", extra_env={
            "GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@example.com",
            "GIT_COMMITTER_NAME": "t", "GIT_COMMITTER_EMAIL": "t@example.com",
        }),
    )


def _make_repo(path: pathlib.Path) -> pathlib.Path:
    path.mkdir(parents=True)
    _git(path, "init", "-q", "-b", "main")
    (path / "README").write_text("x\n", encoding="utf-8")
    _git(path, "add", "README")
    _git(path, "commit", "-qm", "init")
    return path


# ---------------------------------------------------------------------------
# Non-explicit target_version
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("sentinel", ["auto", "next-patch", "", "AUTO"])
def test_release_plan_with_non_explicit_version_is_refused_and_writes_nothing(tmp_path, sentinel):
    plan = _release_plan([_plan_task(1, "implement-thing")], target_version=sentinel)

    result, team_root = _materialize(plan, tmp_path)

    assert result.returncode != 0, f"a {sentinel!r} target_version must be refused:\n{result.stderr}"
    refusal = _refusal(result)
    assert refusal["error"] == "materialize.target-version-not-explicit"
    assert "not an explicit version" in refusal["message"]
    assert str((tmp_path / "plan.json").resolve()) in refusal["remedy"], refusal["remedy"]
    assert "<" not in refusal["remedy"], f"the remedy must be runnable as printed: {refusal['remedy']}"
    _assert_wrote_nothing(result, team_root)


def test_release_plan_without_target_version_field_is_refused(tmp_path):
    plan = _release_plan([_plan_task(1, "implement-thing")])
    del plan["target_version"]

    result, team_root = _materialize(plan, tmp_path)

    assert result.returncode != 0
    assert _refusal(result)["error"] == "materialize.target-version-not-explicit"
    assert "(absent)" in _refusal(result)["message"]
    _assert_wrote_nothing(result, team_root)


def test_refusal_remedy_names_the_requirements_file_when_one_is_given(tmp_path):
    requirements = tmp_path / "v1.4.2-feature.md"
    requirements.write_text("# R\n\n## Status\nrunning\n\n## Target Version\n\nv1.4.2\n", encoding="utf-8")
    plan = _release_plan([_plan_task(1, "implement-thing")], target_version="auto")
    plan["requirements_path"] = str(requirements)

    result, team_root = _materialize(plan, tmp_path)

    assert result.returncode != 0
    assert str(requirements) in _refusal(result)["remedy"]
    _assert_wrote_nothing(result, team_root)


def test_explicit_release_version_materializes_and_names_the_bookends(tmp_path):
    plan = _release_plan([_plan_task(1, "implement-thing")], target_version="v1.4.2")

    result, team_root = _materialize(plan, tmp_path)

    assert result.returncode == 0, result.stderr
    names = [d.name for d in _task_dirs(team_root)]
    assert any(n.startswith("CM-") and n.endswith("open-rc-1-4-2") for n in names), names
    assert any(n.startswith("CM-") and n.endswith("release-1-4-2") for n in names), names


def test_workflow_without_release_bookends_reads_absent_version_as_none(tmp_path):
    plan = _release_plan([_plan_task(1, "verify-the-implementation", role="TESTER")],
                         workflow_type="testing-only")
    del plan["target_version"]

    result, team_root = _materialize(plan, tmp_path)

    assert result.returncode == 0, result.stderr
    assert "Target version: none" in result.stderr
    assert _task_dirs(team_root), "the plan should have materialized"


def test_auto_is_refused_even_without_release_bookends(tmp_path):
    plan = _release_plan([_plan_task(1, "verify-the-implementation", role="TESTER")],
                         target_version="auto", workflow_type="testing-only")

    result, team_root = _materialize(plan, tmp_path)

    assert result.returncode != 0
    assert _refusal(result)["error"] == "materialize.target-version-not-explicit"
    _assert_wrote_nothing(result, team_root)


# ---------------------------------------------------------------------------
# Dev tree precedence
# ---------------------------------------------------------------------------


def test_project_cfg_dev_tree_wins_over_the_global_environment(tmp_path, monkeypatch):
    project_repo = _make_repo(tmp_path / "project-repo")
    other_repo = _make_repo(tmp_path / "other-repo")
    team_root = tmp_path / "team_root"
    team_root.mkdir()
    (team_root / "project.cfg").write_text(
        f"[project]\nproject_name = graph-fixture\ndev_tree_path = {project_repo}\n", encoding="utf-8"
    )
    monkeypatch.setenv("PGAI_DEV_TREE_PATH", str(other_repo))

    assert pm._resolve_dev_tree_path(str(team_root)) == str(project_repo)


def test_environment_dev_tree_is_the_fallback_when_the_project_names_none(tmp_path, monkeypatch):
    other_repo = _make_repo(tmp_path / "other-repo")
    team_root = tmp_path / "team_root"
    team_root.mkdir()
    (team_root / "project.cfg").write_text("[project]\nproject_name = graph-fixture\n", encoding="utf-8")
    monkeypatch.setenv("PGAI_DEV_TREE_PATH", str(other_repo))
    monkeypatch.delenv("PGAI_AGENT_KANBAN_ROOT_PATH", raising=False)

    assert pm._resolve_dev_tree_path(str(team_root)) == str(other_repo)


# ---------------------------------------------------------------------------
# PM-emitted closers: no self-prerequisite after dedupe
# ---------------------------------------------------------------------------


def test_pm_emitted_tester_leaves_one_tester_that_does_not_wait_on_itself(tmp_path):
    plan = _release_plan([
        _plan_task(1, "implement-thing"),
        _plan_task(2, "verify-the-work", role="TESTER"),
    ])

    result, team_root = _materialize(plan, tmp_path)

    assert result.returncode == 0, result.stderr
    dirs = _task_dirs(team_root)
    tester = _only(dirs, "TESTER")
    coder = _only(dirs, "CODER")
    release = _only(dirs, "CM", "release")
    assert "verify-the-work" not in tester.name, "the injected TESTER must be the survivor"
    assert _prereqs(tester) == [coder.name], _prereqs(tester)
    kept = {d.name for d in dirs}
    assert tester.name in _prereqs(release)
    assert set(_prereqs(release)) <= kept, (
        f"CM-release lists a task that was not written: {set(_prereqs(release)) - kept}"
    )
    assert release.name not in _prereqs(release)


def test_pm_emitted_release_task_leaves_one_release_that_does_not_wait_on_itself(tmp_path):
    plan = _release_plan([
        _plan_task(1, "implement-thing"),
        _plan_task(2, "ship-the-release", role="CM"),
    ])

    result, team_root = _materialize(plan, tmp_path)

    assert result.returncode == 0, result.stderr
    dirs = _task_dirs(team_root)
    release = _only(dirs, "CM", "release")
    tester = _only(dirs, "TESTER")
    coder = _only(dirs, "CODER")
    assert "ship-the-release" not in release.name, "the injected CM-release must be the survivor"
    assert release.name not in _prereqs(release), _prereqs(release)
    assert _prereqs(release) == [coder.name, tester.name], _prereqs(release)
    assert release.name not in _prereqs(tester), "TESTER must never wait on CM-release"
    assert _prereqs(tester) == [coder.name], _prereqs(tester)


# ---------------------------------------------------------------------------
# Graph refusals
# ---------------------------------------------------------------------------


def test_two_task_depends_on_cycle_is_refused_naming_both_tasks(tmp_path):
    plan = _release_plan([
        _plan_task(1, "first-half", depends_on=[2]),
        _plan_task(2, "second-half", depends_on=[1]),
    ])

    result, team_root = _materialize(plan, tmp_path)

    assert result.returncode != 0, result.stderr
    refusal = _refusal(result)
    assert refusal["error"] == "materialize.task-graph-invalid"
    cycle_line = next(p for p in refusal["message"].split("; ") if "cycle:" in p)
    assert "first-half" in cycle_line and "second-half" in cycle_line, refusal["message"]
    assert "<" not in refusal["remedy"]
    _assert_wrote_nothing(result, team_root)


def test_depends_on_naming_a_missing_sequence_is_refused(tmp_path):
    plan = _release_plan([
        _plan_task(1, "implement-thing"),
        _plan_task(2, "follow-up", depends_on=[7]),
    ])

    result, team_root = _materialize(plan, tmp_path)

    assert result.returncode != 0, result.stderr
    refusal = _refusal(result)
    assert refusal["error"] == "materialize.task-graph-invalid"
    assert "unresolved depends_on" in refusal["message"]
    assert "follow-up" in refusal["message"] and "7" in refusal["message"], refusal["message"]
    _assert_wrote_nothing(result, team_root)


def test_task_depending_on_itself_is_refused(tmp_path):
    plan = _release_plan([_plan_task(1, "implement-thing", depends_on=[1])])

    result, team_root = _materialize(plan, tmp_path)

    assert result.returncode != 0, result.stderr
    refusal = _refusal(result)
    assert "self-reference" in refusal["message"] and "implement-thing" in refusal["message"]
    _assert_wrote_nothing(result, team_root)


def test_quoted_sequence_numbers_still_resolve(tmp_path):
    plan = _release_plan([
        _plan_task(1, "implement-thing"),
        _plan_task(2, "follow-up", depends_on=["1"]),
    ])

    result, team_root = _materialize(plan, tmp_path)

    assert result.returncode == 0, result.stderr
    dirs = _task_dirs(team_root)
    first = _only(dirs, "CODER", "implement-thing")
    assert first.name in _prereqs(_only(dirs, "CODER", "follow-up"))


# ---------------------------------------------------------------------------
# Graph helpers, directly
# ---------------------------------------------------------------------------


def _t(task_id, prereqs=(), synthetic=False, **extra):
    task = {"task_id": task_id, "role": task_id.split("-")[0], "slug": task_id.lower(),
            "prerequisite_ids": list(prereqs)}
    if synthetic:
        task["_synthetic"] = True
    task.update(extra)
    return task


def test_validate_task_graph_accepts_a_sound_graph():
    tasks = [_t("CM-open"), _t("CODER-a", ["CM-open"]), _t("TESTER-v", ["CODER-a"]),
             _t("CM-rel", ["CODER-a", "TESTER-v"])]
    assert pm.validate_task_graph(tasks) == []


def test_validate_task_graph_reports_only_the_cycle_members():
    tasks = [_t("CODER-a", ["CODER-c"]), _t("CODER-b", ["CODER-a"]), _t("CODER-c", ["CODER-b"]),
             _t("TESTER-v", ["CODER-a"])]
    problems = pm.validate_task_graph(tasks)
    assert len(problems) == 1, problems
    assert "CODER-a, CODER-b, CODER-c" in problems[0]
    assert "TESTER-v" not in problems[0], "a task downstream of a cycle is not part of it"


def test_validate_task_graph_ignores_prerequisites_outside_the_batch():
    assert pm.validate_task_graph([_t("CODER-a", ["CODER-from-an-earlier-run"])]) == []


def test_resolve_prerequisites_returns_unresolvable_entries():
    tasks = [{"sequence": 1, "task_id": "CODER-a", "depends_on": [9]},
             {"sequence": 2, "task_id": "CODER-b", "depends_on": [1]}]
    assert pm.resolve_prerequisites(tasks) == [("CODER-a", 9)]
    assert tasks[1]["prerequisite_ids"] == ["CODER-a"]


def test_dedupe_drops_removed_closer_duplicates_from_synthetic_closers():
    tasks = [
        _t("CM-open", synthetic=True, cm_operation="open-rc"),
        _t("CODER-a", ["CM-open"]),
        _t("TESTER-pm", ["CM-open"]),
        _t("CM-pm-release", ["CM-open"], slug="ship-the-release"),
        _t("TESTER-v", ["CODER-a", "TESTER-pm", "CM-pm-release"], synthetic=True,
           inputs=["CODER-a", "TESTER-pm", "CM-pm-release"]),
        _t("CM-rel", ["CODER-a", "TESTER-pm", "CM-pm-release", "TESTER-v"], synthetic=True,
           cm_operation="release"),
    ]

    kept, _, count = pm.dedupe_materialization_tasks(tasks)

    assert count == 2
    by_id = {t["task_id"]: t for t in kept}
    assert by_id["TESTER-v"]["prerequisite_ids"] == ["CODER-a"]
    assert by_id["TESTER-v"]["inputs"] == ["CODER-a"]
    assert by_id["CM-rel"]["prerequisite_ids"] == ["CODER-a", "TESTER-v"]
    assert pm.validate_task_graph(kept) == []


def test_dedupe_leaves_a_self_reference_the_plan_carried_for_the_graph_check():
    tasks = [_t("CODER-a", ["CODER-a"])]
    kept, _, _ = pm.dedupe_materialization_tasks(tasks)
    assert kept[0]["prerequisite_ids"] == ["CODER-a"]
    assert any("self-reference" in p for p in pm.validate_task_graph(kept))
