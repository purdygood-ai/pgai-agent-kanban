"""
test_pm_materialize_acceptance_policy.py
========================================
Materializer-level tests for the targeted-test acceptance policy.

When pm_materialize.py emits a CODER or WRITER ticket, full gated-runner
invocations in its acceptance criteria and constraints are rewritten to
targeted ``--files`` runs, the fixed targeted-test constraint is appended,
each rewrite is narrated on stderr and recorded in ``## Notes``, and the
fixed pre-merge criterion closes the acceptance criteria. TESTER and CM
tickets render byte-identically to output without the policy.

Two layers:
  1. The real CLI entry point as a subprocess (as pm-agent.sh invokes it),
     asserting the rendered CODER README and the stderr narration.
  2. In-process ticket emission into two roots — one with the policy, one
     with it replaced by a pass-through — comparing README bytes per role.

All temp paths use pytest's ``tmp_path`` fixture (never bare /tmp).
"""

from __future__ import annotations

import json
import pathlib
import re
import subprocess
import sys

try:
    import pm_agent.pm_materialize as pm  # installed via pm_agent package
    from pm_agent.lib import acceptance_policy as ap
except ImportError:
    sys.path.insert(
        0, str(pathlib.Path(__file__).parent.parent.parent / "pm-agent")
    )
    import pm_materialize as pm  # type: ignore[no-redef]
    from lib import acceptance_policy as ap  # type: ignore[no-redef]

from tests.fixtures.scrubbed_subprocess_env import build_scrubbed_env


_TEAM_DIR = pathlib.Path(__file__).parent.parent.parent  # team/
_DEV_TREE_ROOT = _TEAM_DIR.parent
_MATERIALIZE_SCRIPT = _TEAM_DIR / "pm-agent" / "pm_materialize.py"

BARE_RUNNER_CRITERION = "`bash team/scripts/run-unit-tests.sh` exits 0"


def _plan_task(sequence, slug, role, acceptance, constraints=None, notes="none"):
    """A plan-JSON task entry in the shape the pm subagent emits."""
    return {
        "sequence": sequence,
        "slug": slug,
        "title": slug.replace("-", " ").title(),
        "role": role,
        "assigned_agent": role.lower(),
        "working_directory": "REPLACED_BELOW",
        "git_repo": "none",
        "source_branch": "none",
        "goal": "Do the work.",
        "inputs": [],
        "context_paths": [],
        "required_output": "Done.",
        "constraints": list(constraints or []),
        "acceptance_criteria": list(acceptance),
        "depends_on": [],
        "notes": notes,
    }


def _section(readme: str, heading: str) -> str:
    """Return the body of a ``## <heading>`` section of a README."""
    match = re.search(
        rf"^##\s+{re.escape(heading)}\s*\n(.*?)(?=^##\s|\Z)", readme, re.M | re.S
    )
    assert match, f"section '## {heading}' missing from README:\n{readme}"
    return match.group(1)


def _run_materialize(plan: dict, team_root: pathlib.Path, tmp_path: pathlib.Path):
    """Invoke the real pm_materialize.py entry point against a throwaway root.

    The workflow definitions resolve from this dev tree, and --team-root
    alone decides where tickets land. The child environment is built from
    an explicit minimal set (PATH, a scratch HOME, LANG, and the one root
    this test is about) rather than inherited, so no PGAI_* path override
    or other variable exported by the invoking shell can reach the
    materializer and change the README bytes the golden comparison reads.
    """
    workspace = tmp_path / "workspace"
    workspace.mkdir(exist_ok=True)
    for task in plan.get("tasks", []):
        task["working_directory"] = str(workspace)
    plan_path = tmp_path / "plan.json"
    plan_path.write_text(json.dumps(plan), encoding="utf-8")

    home = tmp_path / "home"
    home.mkdir(exist_ok=True)
    env = build_scrubbed_env(
        home_dir=home,
        extra_env={"PGAI_AGENT_KANBAN_ROOT_PATH": str(_DEV_TREE_ROOT)},
    )

    return subprocess.run(
        [
            sys.executable, str(_MATERIALIZE_SCRIPT), str(plan_path),
            "--team-root", str(team_root), "--owner", "CLAUDE",
        ],
        capture_output=True, text=True, env=env, timeout=60,
    )


def _only_folder(tasks_root: pathlib.Path, role: str) -> pathlib.Path:
    folders = [d for d in tasks_root.iterdir() if d.is_dir() and d.name.startswith(f"{role}-")]
    assert len(folders) == 1, f"expected one {role} folder, got {[f.name for f in folders]}"
    return folders[0]


# ---------------------------------------------------------------------------
# Layer 1 — real entry point
# ---------------------------------------------------------------------------


def test_real_entry_point_rewrites_bare_runner_acceptance_on_coder_ticket(
    tmp_path: pathlib.Path,
) -> None:
    team_root = tmp_path / "team_root"
    team_root.mkdir()
    plan = {
        "workflow_type": "release",
        "target_version": "v1.0.0",
        "test_required": "true",
        "human_approval_required": "auto",
        "project_name": "acceptance-policy-fixture",
        "tasks": [
            _plan_task(1, "implement-thing", "CODER",
                       ["The thing works.", BARE_RUNNER_CRITERION],
                       constraints=["Be careful."]),
        ],
    }

    result = _run_materialize(plan, team_root, tmp_path)
    assert result.returncode == 0, f"STDOUT: {result.stdout}\nSTDERR: {result.stderr}"

    coder_dir = _only_folder(team_root / "tasks", "CODER")
    readme = (coder_dir / "README.md").read_text()

    acceptance = _section(readme, "Acceptance Criteria")
    assert "--files" in acceptance
    assert f"- [ ] {ap.TARGETED_RUN_CRITERION}" in acceptance
    assert f"- [ ] {BARE_RUNNER_CRITERION}\n" not in acceptance
    assert "- [ ] The thing works." in acceptance

    constraints = _section(readme, "Constraints")
    assert f"- {ap.TARGETED_RUN_CONSTRAINT}" in constraints
    assert constraints.count(ap.TARGETED_RUN_CONSTRAINT) == 1
    assert "- Be careful." in constraints

    notes = _section(readme, "Notes")
    assert "Targeted-test policy:" in notes
    assert BARE_RUNNER_CRITERION in notes

    narration = [
        line for line in result.stderr.splitlines()
        if "acceptance-policy:" in line and coder_dir.name in line
    ]
    assert len(narration) == 1, result.stderr
    assert "rewrote full-runner acceptance criterion" in narration[0]
    assert "run-unit-tests.sh" in narration[0]

    # The injected TESTER ticket carries neither the rewrite nor the constraint.
    tester_readme = (_only_folder(team_root / "tasks", "TESTER") / "README.md").read_text()
    assert ap.TARGETED_RUN_CONSTRAINT not in tester_readme
    assert "Targeted-test policy:" not in tester_readme


def test_real_entry_point_closes_coder_and_writer_criteria_with_pre_merge(
    tmp_path: pathlib.Path,
) -> None:
    team_root = tmp_path / "team_root"
    team_root.mkdir()
    plan = {
        "workflow_type": "release",
        "target_version": "v1.0.0",
        "test_required": "true",
        "human_approval_required": "auto",
        "project_name": "acceptance-policy-fixture",
        "tasks": [
            _plan_task(1, "implement-thing", "CODER",
                       ["The thing works.", BARE_RUNNER_CRITERION]),
            # The plan already wrote the fixed text, mid-list: it still
            # renders once, last.
            _plan_task(2, "document-thing", "WRITER",
                       [ap.PRE_MERGE_CRITERION, "The guide explains the thing."]),
        ],
    }

    result = _run_materialize(plan, team_root, tmp_path)
    assert result.returncode == 0, f"STDOUT: {result.stdout}\nSTDERR: {result.stderr}"

    expected_line = f"- [ ] {ap.PRE_MERGE_CRITERION}"
    for role in ("CODER", "WRITER"):
        readme = (_only_folder(team_root / "tasks", role) / "README.md").read_text()
        bullets = [
            line for line in _section(readme, "Acceptance Criteria").splitlines()
            if line.startswith("- [ ]")
        ]
        assert bullets[-1] == expected_line, f"{role} README:\n{readme}"
        assert bullets.count(expected_line) == 1, f"{role} README:\n{readme}"

    # Placing the criterion is not a rewrite: the WRITER ticket, which names
    # no other runner, gets no policy note and no narration.
    writer_dir = _only_folder(team_root / "tasks", "WRITER")
    writer_readme = (writer_dir / "README.md").read_text()
    assert "Targeted-test policy:" not in _section(writer_readme, "Notes")
    assert not [
        line for line in result.stderr.splitlines()
        if "acceptance-policy:" in line and writer_dir.name in line
    ], result.stderr

    for folder in (team_root / "tasks").iterdir():
        if folder.is_dir() and folder.name.split("-", 1)[0] in ("TESTER", "CM"):
            assert ap.PRE_MERGE_CRITERION not in (folder / "README.md").read_text(), folder.name


# ---------------------------------------------------------------------------
# Layer 2 — byte identity against output without the policy
# ---------------------------------------------------------------------------


def _emit_plan(tasks_root: pathlib.Path, team_root: pathlib.Path, tasks=None) -> dict:
    """Emit a release plan into *tasks_root*; return {task_id: README text}.

    By default the plan holds a CODER ticket and a plan-authored TESTER
    ticket that both name a bare runner; *tasks* replaces those plan-authored
    entries. The injected CM/TESTER bookends are always added.
    """
    if tasks is None:
        tasks = [
            _plan_task(1, "implement-thing", "CODER", [BARE_RUNNER_CRITERION],
                       constraints=["Be careful."]),
            _plan_task(2, "verify-thing-by-hand", "TESTER", [BARE_RUNNER_CRITERION],
                       constraints=["bash team/scripts/run-integration-tests.sh exits 0"]),
        ]
    for i, task in enumerate(tasks, start=1):
        task["working_directory"] = str(_workspace(team_root))
        task["task_id"] = f"{task['role']}-20260101-{i:03d}-{task['slug']}"
        task["prerequisite_ids"] = []
    pm.inject_cm_bookends(tasks, "v1.0.0", "20260101", 10, "CLAUDE")

    tasks_root.mkdir(parents=True)
    rendered = {}
    for task in tasks:
        task_id = pm.create_task_folder(
            task, str(team_root), release_version="v1.0.0",
            workflow_type="release", tasks_root=str(tasks_root),
        )
        assert task_id, f"emission refused for {task['task_id']}"
        rendered[task_id] = (tasks_root / task_id / "README.md").read_bytes()
    return rendered


def _workspace(team_root: pathlib.Path) -> pathlib.Path:
    path = team_root / "workspace"
    path.mkdir(exist_ok=True)
    return path


def test_tester_and_cm_readmes_are_byte_identical_to_output_without_policy(
    tmp_path: pathlib.Path, monkeypatch,
) -> None:
    team_root = tmp_path / "team_root"
    team_root.mkdir()

    with_policy = _emit_plan(tmp_path / "with_policy", team_root)

    def _no_policy(role, acceptance_criteria, constraints):
        return ap.PolicyResult(
            acceptance_criteria=list(acceptance_criteria or []),
            constraints=list(constraints or []),
        )

    monkeypatch.setattr(pm, "apply_acceptance_policy", _no_policy)
    golden = _emit_plan(tmp_path / "golden", team_root)

    assert set(with_policy) == set(golden)
    roles_seen = set()
    for task_id, golden_bytes in golden.items():
        role = task_id.split("-", 1)[0]
        roles_seen.add(role)
        if role == "CODER":
            # The pass-through really disabled the policy: CODER output differs.
            assert with_policy[task_id] != golden_bytes
        else:
            assert with_policy[task_id] == golden_bytes, (
                f"{task_id} README changed under the acceptance policy"
            )
    assert {"CODER", "TESTER", "CM"} <= roles_seen


def test_pre_merge_criterion_changes_only_coder_and_writer_readmes(
    tmp_path: pathlib.Path, monkeypatch,
) -> None:
    """Against output without the pre-merge criterion, CODER and WRITER
    READMEs differ by exactly that one criterion line and the TESTER and CM
    READMEs (plan-authored and injected bookends) are byte-identical."""
    team_root = tmp_path / "team_root"
    team_root.mkdir()

    def _plan():
        return [
            _plan_task(1, "implement-thing", "CODER", [BARE_RUNNER_CRITERION, "It works."],
                       constraints=["Be careful."]),
            _plan_task(2, "document-thing", "WRITER", ["The guide explains it."]),
            _plan_task(3, "verify-thing-by-hand", "TESTER", [BARE_RUNNER_CRITERION]),
        ]

    with_criterion = _emit_plan(tmp_path / "with_criterion", team_root, _plan())

    real_policy = pm.apply_acceptance_policy

    def _policy_without_pre_merge(role, acceptance_criteria, constraints):
        result = real_policy(role, acceptance_criteria, constraints)
        return ap.PolicyResult(
            acceptance_criteria=[
                c for c in result.acceptance_criteria if c != ap.PRE_MERGE_CRITERION
            ],
            constraints=result.constraints,
            rewrites=result.rewrites,
            constraint_added=result.constraint_added,
        )

    monkeypatch.setattr(pm, "apply_acceptance_policy", _policy_without_pre_merge)
    golden = _emit_plan(tmp_path / "golden", team_root, _plan())

    assert set(with_criterion) == set(golden)
    criterion_line = f"- [ ] {ap.PRE_MERGE_CRITERION}\n".encode()
    roles_seen = set()
    for task_id, golden_bytes in golden.items():
        role = task_id.split("-", 1)[0]
        roles_seen.add(role)
        if role in ("CODER", "WRITER"):
            assert with_criterion[task_id].count(criterion_line) == 1, task_id
            assert with_criterion[task_id].replace(criterion_line, b"", 1) == golden_bytes, (
                f"{task_id} README differs by more than the pre-merge criterion"
            )
        else:
            assert with_criterion[task_id] == golden_bytes, (
                f"{task_id} README changed under the pre-merge criterion"
            )
    assert {"CODER", "WRITER", "TESTER", "CM"} <= roles_seen
