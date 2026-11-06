"""
test_plan_needs_operator_ride_along.py
======================================
Integration tests: a PM plan's ``needs_operator`` and ``ride_along`` fields,
through the real entry points -- pm_materialize.py, promote_bundled_items.py
(release Step 13c), the ops deposit verb, the operator-inbox answer command
and the discovery pipeline -- each run as a subprocess against a sandbox.

  - A bugfix-bundle plan parks one bundled bug on an operator question: the
    materializer sets it needs-operator with one inbox entry carrying the
    question; the release closes the other items and leaves it unchanged
    and out of the carried report; depositing the same defect is refused
    citing it; once the entry is answered, the next discovery pass bundles
    it.
  - With ``bundle_mixing = ride-along``, a requirement plan listing three
    eligible deferred bugs materializes and the three are ``running``; at
    ship time a ride-along a DONE task claims closes and an unclaimed one is
    carried.
  - The plan lint refuses -- writing no task folder, queue line or status --
    a list with a high-severity bug, a bug naming a deliverable file, more
    bugs than ``ride_along_max``, and any entry under the default
    ``separate``.
  - A plan without the fields writes exactly what a plan with both fields
    empty writes, and touches no intake file.

Each test builds its own sandbox kanban root under tmp_path, in a project
whose name the operator inbox accepts, and runs every subprocess with all
five kanban-root variables set or cleared. Fixture identifiers use the
fictitious BUG-9xxx range.
"""

from __future__ import annotations

import json
import os
import pathlib
import re
import subprocess
import sys
import textwrap

import pytest

from pgai_agent_kanban.ops.context import OpsContext
from pgai_agent_kanban.overwatch import inbox
from tests.fixtures.two_project import add_registered_project, build_two_project_root

_TEAM_DIR = pathlib.Path(__file__).resolve().parents[2]
_MATERIALIZE = _TEAM_DIR / "pm-agent" / "pm_materialize.py"
_PROMOTE = _TEAM_DIR / "scripts" / "cm" / "promote_bundled_items.py"
_SCRIPTS_LIB = _TEAM_DIR / "scripts" / "lib"
_PROJECT = "plan-intake"
_QUESTION = "Which package mirror should the installer use?"


# ---------------------------------------------------------------------------
# Sandbox
# ---------------------------------------------------------------------------


def _sandbox(tmp_path: pathlib.Path, release_cfg: str = "") -> tuple[pathlib.Path, pathlib.Path]:
    """``(kanban_root, project_root)``; *release_cfg* lines go under ``[release]``."""
    root = build_two_project_root(tmp_path)
    project_root = add_registered_project(root, _PROJECT)
    for sub in ("bugs", "requirements", "priority"):
        (project_root / sub).mkdir(exist_ok=True)
    (root / "tmp").mkdir(exist_ok=True)
    if release_cfg:
        with (project_root / "project.cfg").open("a", encoding="utf-8") as cfg:
            cfg.write(f"\n[release]\n{release_cfg}")
    return root, project_root


def _env(root: pathlib.Path) -> dict[str, str]:
    env = dict(os.environ)
    for name in ("PGAI_TASKS_DIR", "PGAI_PROJECT_ROOT", "PGAI_DEV_TREE_PATH", "PGAI_PROJECT_NAME"):
        env.pop(name, None)
    env["KANBAN_ROOT"] = str(root)
    env["TEAM_ROOT"] = str(root)
    env["PGAI_AGENT_KANBAN_ROOT_PATH"] = str(root)
    env["PGAI_AGENT_KANBAN_TEMP_DIR"] = str(root / "tmp")
    env["PGAI_DEFERRED_BUGS"] = "false"
    env["PYTHONPATH"] = str(_TEAM_DIR)
    return env


def _run(root: pathlib.Path, *cmd: str) -> subprocess.CompletedProcess:
    return subprocess.run(list(cmd), capture_output=True, text=True, env=_env(root), cwd=str(root), timeout=120)


def _bug(project_root: pathlib.Path, stem: str, status: str, severity: str = "low", body: str = "") -> pathlib.Path:
    path = project_root / "bugs" / f"{stem}.md"
    path.write_text(
        f"# {stem}\n\n**Severity:** {severity}\n\n## Status\n{status}\n\n## Symptom\n{body or 'synthetic'}\n",
        encoding="utf-8",
    )
    return path


def _status(path: pathlib.Path) -> str:
    lines = path.read_text(encoding="utf-8").splitlines()
    index = next(i for i, line in enumerate(lines) if line.strip().lower() == "## status")
    return next(line.strip() for line in lines[index + 1 :] if line.strip())


def _task(seq: int, slug: str, inputs: list, version: str) -> dict:
    return {
        "sequence": seq,
        "slug": slug,
        "title": f"Fix {slug}",
        "role": "CODER",
        "working_directory": "/dev/null",
        "git_repo": "none",
        "source_branch": f"rc/{version}",
        "goal": f"Fix {slug}.",
        "inputs": [str(p) for p in inputs],
        "context_paths": [],
        "required_output": "The fix.",
        "constraints": [],
        "acceptance_criteria": ["It works."],
        "depends_on": [],
        "notes": "",
    }


def _plan(version: str, requirement: pathlib.Path, tasks: list, **fields) -> dict:
    plan = {
        "project_name": _PROJECT,
        "workflow_type": "release",
        "target_version": version,
        "test_required": "false",
        "human_approval_required": "auto",
        "requirements_path": str(requirement),
        "tasks": tasks,
    }
    plan.update(fields)
    return plan


def _materialize(root, project_root, tmp_path, plan: dict, name: str = "plan") -> subprocess.CompletedProcess:
    plan_dir = tmp_path / f"plans-{name}"
    plan_dir.mkdir()
    plan_file = plan_dir / "plan.json"
    plan_file.write_text(json.dumps(plan, indent=2), encoding="utf-8")
    return _run(
        root,
        sys.executable,
        str(_MATERIALIZE),
        str(plan_file),
        "--team-root",
        str(project_root),
        "--requirements-path",
        plan["requirements_path"],
    )


def _task_dirs(project_root: pathlib.Path) -> list[pathlib.Path]:
    return sorted(d for d in (project_root / "tasks").iterdir() if d.is_dir() and d.name != "queues")


def _mark_all_done(project_root: pathlib.Path) -> None:
    for task_dir in _task_dirs(project_root):
        status = task_dir / "status.md"
        status.write_text(
            re.sub(r"(?m)^(## State\n)\S+", r"\1DONE", status.read_text(encoding="utf-8")), encoding="utf-8"
        )


def _snapshot(project_root: pathlib.Path) -> dict[str, bytes]:
    """Every byte the materializer could write: tasks, queues, intake, requirements, inbox."""
    paths = [project_root / sub for sub in ("tasks", "bugs", "requirements", "priority")]
    paths.append(project_root / "operator-inbox.md")
    files = [f for p in paths for f in ([p] if p.is_file() else sorted(p.rglob("*")) if p.is_dir() else [])]
    return {str(f): f.read_bytes() for f in files if f.is_file()}


def _open_entries(root: pathlib.Path) -> tuple:
    return inbox.list_open(OpsContext(kanban_root=root), _PROJECT)


def _run_discovery(root: pathlib.Path) -> subprocess.CompletedProcess:
    script = textwrap.dedent(f"""\
        #!/usr/bin/env bash
        set -euo pipefail
        source "{_SCRIPTS_LIB}/ini_parser.sh"
        source "{_SCRIPTS_LIB}/temp.sh"
        source "{_SCRIPTS_LIB}/semver.sh"
        source "{_SCRIPTS_LIB}/project_paths.sh"
        source "{_SCRIPTS_LIB}/discovery.sh"
        discovery_run_pipeline "{_PROJECT}"
        echo "DISCOVERY_STATUS=${{DISCOVERY_LAST_STATUS}}"
    """)
    return _run(root, "bash", "-c", script)


# ---------------------------------------------------------------------------
# needs_operator: the bundled bug the PM cannot act on
# ---------------------------------------------------------------------------


def test_a_needs_operator_entry_parks_the_bug_through_release_dedupe_and_answer(tmp_path) -> None:
    root, project_root = _sandbox(tmp_path)
    fixed_a = _bug(project_root, "BUG-9901-installer-path", "running")
    waiting = _bug(project_root, "BUG-9902-installer-mirror", "running")
    fixed_b = _bug(project_root, "BUG-9903-installer-log", "running")
    bundle = project_root / "requirements" / "v9.9.1-bugfix-bundle-20990101.md"
    bundle.write_text(
        "# v9.9.1-bugfix-bundle-20990101\n\n## Status\nrunning\n\n## Target Version\nv9.9.1\n\n"
        "## Workflow Type\nrelease\n\n## Bundled Items\n\n"
        + "".join(f"- {b.name} (`{b}`)\n" for b in (fixed_a, waiting, fixed_b)),
        encoding="utf-8",
    )
    plan = _plan(
        "v9.9.1",
        bundle,
        [_task(1, "installer-fixes", [fixed_a, fixed_b], "v9.9.1")],
        needs_operator=[{"item": "BUG-9902", "question": _QUESTION}],
    )

    result = _materialize(root, project_root, tmp_path, plan)

    assert result.returncode == 0, result.stderr
    assert _status(waiting) == "needs-operator"
    [entry] = _open_entries(root)
    assert entry.source == waiting.stem
    assert entry.question == _QUESTION
    assert any("installer-fixes" in d.name for d in _task_dirs(project_root))

    # Release Step 13c: the work ticket is DONE and names the other two items.
    _mark_all_done(project_root)
    waiting_before = waiting.read_bytes()
    shipped = _run(
        root,
        sys.executable,
        str(_PROMOTE),
        str(bundle),
        "v9.9.1",
        str(project_root / "tasks"),
        str(project_root / "bugs"),
    )
    assert shipped.returncode == 0, shipped.stderr
    assert _status(fixed_a) == "done" and _status(fixed_b) == "done"
    assert waiting.read_bytes() == waiting_before
    report = shipped.stdout[shipped.stdout.index("post-ship report") :]
    assert "Carried (not closed): 0 item(s)" in report
    assert waiting.name not in report
    assert _status(bundle) == "done"

    # The TESTER's re-file of the same defect is refused, citing the parked bug.
    draft = tmp_path / "BUG-9950-installer-mirror-again.md"
    draft.write_text(
        "# BUG-9950-installer-mirror-again\n\n**Severity:** medium\n\n## Status\nopen\n\n"
        "## Symptom\nSame defect as BUG-9902: the installer still picks no mirror.\n",
        encoding="utf-8",
    )
    deposit = _run(root, sys.executable, "-m", "pgai_agent_kanban.ops", "deposit_intake", str(project_root), str(draft))
    assert deposit.returncode == 5, deposit.stdout + deposit.stderr
    assert "BUG-9902" in deposit.stderr
    assert not (project_root / "bugs" / draft.name).exists()

    # Answering the entry reopens the bug, and the next discovery pass bundles it.
    answered = _run(
        root,
        sys.executable,
        "-m",
        "pgai_agent_kanban.overwatch.inbox",
        "answer",
        "--project",
        _PROJECT,
        "--kanban-root",
        str(root),
        "--id",
        entry.entry_id,
        "--answer",
        "Use the primary mirror.",
    )
    assert answered.returncode == 0, answered.stderr
    assert _status(waiting) == "open"
    discovered = _run_discovery(root)
    assert discovered.returncode == 0, discovered.stdout + discovered.stderr
    assert "DISCOVERY_STATUS=produced_work" in discovered.stdout, discovered.stdout + discovered.stderr
    [new_bundle] = [b for b in (project_root / "requirements").glob("*-bugfix-bundle-*.md") if b != bundle]
    assert waiting.name in new_bundle.read_text(encoding="utf-8")
    assert _status(waiting) == "running"


# ---------------------------------------------------------------------------
# ride_along: deferred bugs ticketed inside a requirement RC
# ---------------------------------------------------------------------------

_DELIVERABLES = (
    "## Deliverables\n"
    "1. **Close path.** `team/scripts/close.sh` gains a duplicate state.\n"
    "2. **Docs.** OPERATIONS.md gains the lifecycle.\n"
)


def _requirement(project_root: pathlib.Path) -> pathlib.Path:
    path = project_root / "requirements" / "v9.10.0-convergence.md"
    path.write_text(
        f"# v9.10.0-convergence\n\n## Status\nrunning\n\n## Target Version\nv9.10.0\n\n{_DELIVERABLES}\n"
        "## Acceptance\n- It converges.\n",
        encoding="utf-8",
    )
    return path


def _eligible(project_root: pathlib.Path) -> list[pathlib.Path]:
    return [
        _bug(project_root, "BUG-9911-glyph-width", "open", "low"),
        _bug(project_root, "BUG-9912-help-typo", "open", "medium", body="team/scripts/status.sh --help misspells it."),
        _bug(project_root, "BUG-9913-log-prefix", "open", "low"),
    ]


def _ride_plan(requirement: pathlib.Path, ticketed: list, ride_along: list) -> dict:
    return _plan("v9.10.0", requirement, [_task(1, "convergence", ticketed, "v9.10.0")], ride_along=ride_along)


def test_three_eligible_ride_alongs_run_and_ship_as_bundle_items(tmp_path) -> None:
    root, project_root = _sandbox(tmp_path, "bundle_mixing = ride-along\n")
    requirement = _requirement(project_root)
    bugs = _eligible(project_root)

    result = _materialize(root, project_root, tmp_path, _ride_plan(requirement, bugs[:2], [b.stem for b in bugs]))

    assert result.returncode == 0, result.stderr
    assert [_status(b) for b in bugs] == ["running"] * 3
    text = requirement.read_text(encoding="utf-8")
    for bug in bugs:
        assert f"- {bug.name} (`{bug}`) — ride-along" in text

    # Ship: the DONE ticket names two of the three.
    _mark_all_done(project_root)
    shipped = _run(
        root,
        sys.executable,
        str(_PROMOTE),
        str(requirement),
        "v9.10.0",
        str(project_root / "tasks"),
        str(project_root / "bugs"),
    )
    assert shipped.returncode == 0, shipped.stderr
    assert [_status(b) for b in bugs] == ["done", "done", "running"]
    report = shipped.stdout[shipped.stdout.index("post-ship report") :]
    assert "Carried (not closed): 1 item(s)" in report and bugs[2].name in report
    assert _status(requirement) == "done"


@pytest.mark.parametrize(
    ("release_cfg", "extra", "names"),
    [
        (
            "bundle_mixing = ride-along\nride_along_max = 4\n",
            ("BUG-9914-crash-on-start", "high", ""),
            ["BUG-9914-crash-on-start.md", "high"],
        ),
        (
            "bundle_mixing = ride-along\n",
            ("BUG-9915-close-flag", "low", "close.sh rejects --note."),
            ["BUG-9915-close-flag.md", "close.sh"],
        ),
        ("bundle_mixing = ride-along\nride_along_max = 2\n", None, ["ride_along_max allows 2"]),
        ("", None, ["bundle_mixing resolves to 'separate'"]),
    ],
    ids=["high-severity", "file-overlap", "over-max", "separate-default"],
)
def test_the_plan_lint_refuses_and_nothing_is_written(tmp_path, release_cfg, extra, names) -> None:
    root, project_root = _sandbox(tmp_path, release_cfg)
    requirement = _requirement(project_root)
    bugs = _eligible(project_root)
    if extra is not None:
        stem, severity, body = extra
        bugs.append(_bug(project_root, stem, "open", severity, body=body))
    before = _snapshot(project_root)

    result = _materialize(root, project_root, tmp_path, _ride_plan(requirement, bugs, [b.stem for b in bugs]))

    assert result.returncode == 1, result.stdout + result.stderr
    assert re.search(r"(?m)^error:\s+ride_along_lint\.", result.stderr), result.stderr
    for name in names:
        assert name in result.stderr, result.stderr
    assert _snapshot(project_root) == before
    assert not list((tmp_path / "plans-plan").glob(".materialized.*"))


def test_a_plan_without_the_fields_writes_what_a_plan_with_them_empty_writes(tmp_path) -> None:
    trees = {}
    for name, fields in (("absent", {}), ("empty", {"needs_operator": [], "ride_along": []})):
        root, project_root = _sandbox(tmp_path / name)
        requirement = _requirement(project_root)
        bugs = _eligible(project_root)
        intake_before = {k: v for k, v in _snapshot(project_root).items() if "/tasks/" not in k}

        result = _materialize(
            root,
            project_root,
            tmp_path / name,
            _plan("v9.10.0", requirement, [_task(1, "convergence", bugs, "v9.10.0")], **fields),
        )

        assert result.returncode == 0, result.stderr
        assert "Plan intake fields" not in result.stderr
        assert {k: v for k, v in _snapshot(project_root).items() if "/tasks/" not in k} == intake_before
        trees[name] = {
            k.split(f"{name}/", 1)[1]: v.replace(str(tmp_path / name).encode(), b"ROOT")
            for k, v in _snapshot(project_root).items()
            if "/tasks/" in k
        }
    assert trees["absent"] == trees["empty"]
