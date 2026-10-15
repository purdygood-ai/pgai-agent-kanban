"""
test_overwatch_scaffolding_gates_resolution.py
=================================================
Integration fixtures proving the v2.23.0 scaffolding's central promise —
"ships dark, everywhere" — end to end: a project produced by the REAL
create-project.sh, and an existing project.cfg migrated by the REAL
migrate_v2.22.5_to_v2.23.0.sh, both seed overwatch_resolve commented out,
and a HALTed block on either one resolves NOTHING (declines exactly as it
did before the three-lane classifier existed) until the operator
uncomments the key and sets it true — at which point the identical block
resolves.

Coverage (requirements/v2.23.0-overwatch-block-resolution.md, Acceptance,
"Fresh create-project: cfg carries the commented key ... upgrade seeds the
commented key into an existing cfg without enabling; nothing resolves
until the operator uncomments and sets true"):

  - Fresh create-project.sh output: overwatch_resolve stays commented,
    a Lane-A-shaped HALT on that project declines and touches nothing,
    then uncommenting + setting true makes the SAME block resolve.
  - An existing (pre-v2.23.0) project.cfg migrated by the real
    migrate_v2.22.5_to_v2.23.0.sh script: same round trip.

test_create_project.py already covers the commented-key/zero-entry-
INTENT.md shape of create-project.sh's output in isolation, and
test_migrate_overwatch_resolve_seed.py already covers the migration
script's own seeding behavior in isolation. This file is the missing
third leg: proving the seeded-false state actually GATES resolution on a
real block, and that flipping the one key actually un-gates it — the
"nothing resolves until true" half of the acceptance criterion, which
neither of those two files exercises (neither ever calls
run_resolution_stage()).

Design:
  - All temp paths use pytest's tmp_path. No bare /tmp paths.
  - create-project.sh and migrate_v2.22.5_to_v2.23.0.sh run as real
    subprocesses against a temp kanban root — no seam mocked.
  - The Lane A recovery drill (unwind-rc.sh / cancel-rc.sh) also runs as
    a real subprocess once the key is flipped true, proving the "resolves"
    half is not merely "would have resolved" — mirrors
    test_overwatch_lane_a_drill.py's own technique (duplicated here per
    this test tree's established one-self-contained-fixture-set-per-file
    convention).
"""

from __future__ import annotations

import json
import os
import pathlib
import re
import subprocess

import pytest

from pgai_agent_kanban.overwatch.resolution_stage import DECLINED, EXECUTED, run_resolution_stage
from tests.fixtures.binary_guard import require_binary

_TEAM_DIR = pathlib.Path(__file__).parent.parent.parent  # team/
_CREATE_PROJECT_SCRIPT = _TEAM_DIR / "scripts" / "create-project.sh"
_MIGRATION_SCRIPT = _TEAM_DIR / "scripts" / "migrations" / "migrate_v2.22.5_to_v2.23.0.sh"
_KANBAN_PY_CM_DIR = _TEAM_DIR / "pgai_agent_kanban" / "cm"


@pytest.fixture(autouse=True)
def _require_git() -> None:
    require_binary("git", label="overwatch-scaffolding-gates-resolution")


# ---------------------------------------------------------------------------
# Fixture builders
# ---------------------------------------------------------------------------


def _run_git(repo_path: pathlib.Path, *args: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["git", "-C", str(repo_path), *args], capture_output=True, text=True, check=True
    )


def _init_git_repo_with_direct_commit(repo_path: pathlib.Path, main_branch: str, rc_branch: str) -> None:
    """One commit on *main_branch*, then *rc_branch* with one further direct
    (non-merge) commit — makes operator_commits_in_lineage extract True so
    the block fails CANCEL-AND-REBUNDLE and reaches the overwatch_resolve
    gate (same technique test_overwatch_lane_a_drill.py's own docstring
    documents)."""
    repo_path.mkdir(parents=True, exist_ok=True)
    subprocess.run(
        ["git", "init", f"--initial-branch={main_branch}", str(repo_path)],
        capture_output=True, check=True,
    )
    _run_git(repo_path, "config", "--local", "user.email", "test@pgai.local")
    _run_git(repo_path, "config", "--local", "user.name", "PGAI Test")
    (repo_path / "README.md").write_text("# Test repo\n", encoding="utf-8")
    _run_git(repo_path, "add", "README.md")
    _run_git(repo_path, "commit", "-m", "Initial commit")
    _run_git(repo_path, "checkout", "-b", rc_branch)
    (repo_path / "DIRECT.txt").write_text(f"direct commit on {rc_branch}\n", encoding="utf-8")
    _run_git(repo_path, "add", "DIRECT.txt")
    _run_git(repo_path, "commit", "-m", f"direct commit on {rc_branch}")
    _run_git(repo_path, "checkout", main_branch)


def _build_minimal_kanban_root(tmp_path: pathlib.Path) -> pathlib.Path:
    root = tmp_path / "kanban_root"
    root.mkdir(parents=True, exist_ok=True)
    (root / "kanban.cfg").write_text(
        "[paths]\n\n[chain]\npm_mode = automatic\n\n"
        "[wake]\nmax_tasks_per_wake = 1\n\n[release]\nmax_rc_retries = 3\n",
        encoding="utf-8",
    )
    (root / "logs").mkdir(parents=True, exist_ok=True)
    return root


def _install_operator_verb_scripts(kanban_root: pathlib.Path) -> None:
    """Copy scripts/ and pgai_agent_kanban/cm/*.py into *kanban_root* — the
    live-install layout the Lane A drill's subprocess calls (unwind-rc.sh,
    cancel-rc.sh) resolve against. create-project.sh itself does not need
    or write these; they are added here only so a subsequent
    run_resolution_stage() call, once the flag is flipped true, can
    actually execute the drill."""
    import shutil

    cm_py_dir = kanban_root / "pgai_agent_kanban" / "cm"
    cm_py_dir.mkdir(parents=True, exist_ok=True)
    for py_file in _KANBAN_PY_CM_DIR.glob("*.py"):
        (cm_py_dir / py_file.name).write_text(py_file.read_text(encoding="utf-8"), encoding="utf-8")
    shutil.copytree(_TEAM_DIR / "scripts", kanban_root / "scripts")


def _run_create_project(
    kanban_root: pathlib.Path,
    project_name: str,
    dev_tree_path: pathlib.Path,
    fixture_temp_root: pathlib.Path,
) -> subprocess.CompletedProcess:
    """Run the real create-project.sh against *kanban_root*.

    create-project.sh itself never touches a remote, but its subprocess
    environment establishes PGAI_AGENT_KANBAN_TEMP_DIR from
    *fixture_temp_root* -- this fixture's own resolved test temp root
    (team/tests/conftest.py's pgai_tests_temp_dir, an ancestor of every
    path this module's repositories live under) -- and clears any
    PGAI_AGENT_ROLE the caller happens to carry, rather than forwarding
    both ambient values wholesale via dict(os.environ). create-project.sh
    runs under no pipeline role of its own; a caller's role is never this
    fixture's to pass on to it.
    """
    base_env = dict(os.environ)
    base_env["PGAI_AGENT_KANBAN_ROOT_PATH"] = str(kanban_root)
    base_env["PGAI_AGENT_KANBAN_TEMP_DIR"] = str(fixture_temp_root)
    base_env.pop("PGAI_DEV_TREE_PATH", None)
    base_env.pop("PGAI_AGENT_ROLE", None)
    return subprocess.run(
        [
            "bash", str(_CREATE_PROJECT_SCRIPT),
            "--project", project_name,
            "--workflow-type", "release",
            "--dev-tree", str(dev_tree_path),
        ],
        capture_output=True, text=True, env=base_env, cwd=str(kanban_root), timeout=60,
    )


def _run_migration(kanban_root: pathlib.Path, project_name: str) -> subprocess.CompletedProcess:
    env = dict(os.environ)
    env["PGAI_AGENT_KANBAN_ROOT_PATH"] = str(kanban_root)
    env.pop("PGAI_PROJECT_NAME", None)
    env.pop("PGAI_DEV_TREE_PATH", None)
    return subprocess.run(
        ["bash", str(_MIGRATION_SCRIPT), "--project", project_name],
        capture_output=True, text=True, env=env, cwd=str(kanban_root), timeout=30,
    )


def _write_release_state_md(proj: pathlib.Path, active_rc: str) -> None:
    (proj / "release-state.md").write_text(
        "# Release State\n\n"
        f"## Active RC\n{active_rc}\n\n"
        "## RC Opened At\n2026-09-10T00:00:00Z\n\n"
        "## RC Opened By Task\nCM-20260910-001-open\n\n"
        "## Last Released\nv0.0.0\n",
        encoding="utf-8",
    )


def _write_ship_policy_halt(path: pathlib.Path) -> None:
    path.write_text(
        "# HALT created by CM at 2026-09-10T00:00:00Z\n"
        "# Reason: TESTER systemic_risk=high (TESTER-20260910-001)\n"
        "# Resolution: Operator review required. See CM task status.md for full reason.\n",
        encoding="utf-8",
    )


def _write_bug(bugs_dir: pathlib.Path, bug_id: str, status: str = "assigned") -> pathlib.Path:
    path = bugs_dir / f"{bug_id}.md"
    path.write_text(
        f"# Bug: {bug_id}\n\n## Status\n{status}\n\n## Symptom\nsample regression\n\n"
        "## Fix\nOriginal (ambiguous) fix direction.\n",
        encoding="utf-8",
    )
    return path


def _write_bundle(requirements_dir: pathlib.Path, version: str, bug_basename: str, bugs_dir: pathlib.Path) -> pathlib.Path:
    bundle_path = requirements_dir / f"{version}-bugfix-bundle-20260910.md"
    bundle_path.write_text(
        f"# Requirements: {version} — BUG bundle\n\n"
        "## Status\nrunning\n\n"
        f"## Target Version\n{version}\n\n"
        "## Bundled Items\n\n"
        f"- {bug_basename} (`{bugs_dir / bug_basename}`)\n",
        encoding="utf-8",
    )
    return bundle_path


def _write_task(tasks_dir: pathlib.Path, task_id: str, version: str, state: str, role: str = "CODER") -> None:
    task_dir = tasks_dir / task_id
    task_dir.mkdir(parents=True, exist_ok=True)
    (task_dir / "README.md").write_text(
        f"# Task: {task_id}\n\n## Task ID\n{task_id}\n\n## Role\n{role}\n\n"
        f"## Source Branch\nai_rc/{version}\n\n## Release Version\n{version}\n",
        encoding="utf-8",
    )
    (task_dir / "status.md").write_text(
        f"# Status\n\n## Task\n{task_id}\n\n## State\n{state}\n\n## Blockers\nnone\n",
        encoding="utf-8",
    )


def _write_lane_a_tester_report(tasks_dir: pathlib.Path, tester_task_id: str, version: str, bug_id: str) -> None:
    task_dir = tasks_dir / tester_task_id
    (task_dir / "artifacts").mkdir(parents=True, exist_ok=True)
    _write_task(tasks_dir, tester_task_id, version, "DONE", role="TESTER")
    short_id = bug_id.split("-")[0] + "-" + bug_id.split("-")[1]
    (task_dir / "artifacts" / "report.md").write_text(
        "# TESTER Report\n\n"
        "### Finding 1: dashboard column count regression\n"
        "**Systemic Risk:** high\n"
        "**Regression:** yes\n"
        f"**Filed As:** {bug_id} (Path C)\n\n"
        f"Traces to {short_id}, whose fix in this RC inverted the column check.\n",
        encoding="utf-8",
    )


def _attach_fix_rewrite(kanban_root: pathlib.Path, project_name: str, bug_id: str) -> None:
    """Perform Lane A recovery-playbook step (a) — the reasoning tier's Fix
    rewrite — through the REAL shipped CLI
    (team/scripts/overwatch-attach-fix-rewrite.sh, invoked from its actual
    dev-tree location so its own-tree-root resolves the full
    pgai_agent_kanban package; *kanban_root* here only ever carries
    pgai_agent_kanban/cm/*.py via _install_operator_verb_scripts, not the
    whole package), as a subprocess — never a direct call to
    rewrite_bug_fix_section() from this test file, mirroring
    test_overwatch_lane_a_drill.py's own convention. Satisfies
    pgai_agent_kanban.overwatch.resolution_stage._run_lane_a()'s
    rewrite-precondition guard — which requires a written Fix section
    before the drill may run — before a subsequent run_resolution_stage()
    call is expected to execute the drill."""
    short_id = bug_id.split("-")[0] + "-" + bug_id.split("-")[1]
    script = _TEAM_DIR / "scripts" / "overwatch-attach-fix-rewrite.sh"
    result = subprocess.run(
        [
            str(script),
            "--project", project_name,
            "--kanban-root", str(kanban_root),
            "--bug-id", short_id,
            "--forbidden-approach", "Inverting the column-count comparison operator.",
            "--correct-direction", "Compare using the original comparison direction.",
            "--ground-truth-ref", "the bug's own original ## Fix section, applied verbatim in this RC's bundle",
        ],
        capture_output=True, text=True,
    )
    assert result.returncode == 0, (
        f"overwatch-attach-fix-rewrite.sh failed:\nstdout:\n{result.stdout}\nstderr:\n{result.stderr}"
    )


def _establish_drill_environment(monkeypatch: pytest.MonkeyPatch, fixture_temp_root: pathlib.Path) -> None:
    """Establish, on THIS test process's own environment, the two values
    the git boundary guard (team/scripts/lib/git_boundary_guard.sh) reads
    when the drill below drives the copied CM operator verb scripts
    (cancel-rc.sh, unwind-rc.sh) as subprocesses --
    pgai_agent_kanban.overwatch.resolution_stage builds each of those
    subprocess environments from dict(os.environ), so this process's own
    environment is the only lever this fixture has over what they see.

    PGAI_AGENT_ROLE is set to "cm": the drill runs these scripts exactly
    as CM's own dispatch would, proving they complete for real rather
    than merely proving a refusal was never observed. PGAI_AGENT_KANBAN_TEMP_DIR
    is set to *fixture_temp_root* -- this fixture's own resolved test temp
    root (team/tests/conftest.py's pgai_tests_temp_dir, an ancestor of
    every throwaway repository the drill touches) -- rather than left at
    whatever value the process that launched this test suite happened to
    export, so the guard's fixture-local exemption is never at the mercy
    of the caller's own environment either.
    """
    monkeypatch.setenv("PGAI_AGENT_ROLE", "cm")
    monkeypatch.setenv("PGAI_AGENT_KANBAN_TEMP_DIR", str(fixture_temp_root))


def _overlay_lane_a_block(proj: pathlib.Path, version: str, bug_id: str, *, kanban_root: pathlib.Path, project_name: str) -> pathlib.Path:
    """Lay a Lane-A-shaped HALTed block on top of an already-scaffolded
    project (fresh create-project.sh output, or a migrated legacy cfg).
    Returns the HALT file path."""
    for sub in ("requirements", "bugs", "tasks/queues", "release-state"):
        (proj / sub).mkdir(parents=True, exist_ok=True)

    _write_release_state_md(proj, version)
    halt_path = proj / "HALT"
    _write_ship_policy_halt(halt_path)
    (proj / "release-state" / f"{version}.json").write_text(
        json.dumps({"rc": version, "attempt": 1, "outcome": "in_progress", "family_key": None}),
        encoding="utf-8",
    )
    _write_bug(proj / "bugs", bug_id)
    _attach_fix_rewrite(kanban_root, project_name, bug_id)
    (proj / "tasks" / "queues" / "bug_backlog.md").write_text(f"- [x] {bug_id}\n", encoding="utf-8")
    _write_bundle(proj / "requirements", version, f"{bug_id}.md", proj / "bugs")
    _write_lane_a_tester_report(proj / "tasks", "TESTER-20260910-101-verify", version, bug_id)
    return halt_path


def _live_intent_entry_lines(intent_text: str) -> list:
    """Every unfenced '- YYYY-MM-DD: ...' bullet line in INTENT.md text —
    same fence-stripping logic test_create_project.py's own
    _live_intent_entry_lines() uses, duplicated per this test tree's
    established one-self-contained-fixture-set-per-file convention. The
    template's worked example is deliberately fenced so it is never
    counted as a live entry."""
    lines = []
    in_fence = False
    for line in intent_text.splitlines():
        if line.strip().startswith("```"):
            in_fence = not in_fence
            continue
        if in_fence:
            continue
        if re.match(r"^-\s+\d{4}-\d{2}-\d{2}:", line.strip()):
            lines.append(line)
    return lines


_LIVE_PUSH_TO_REMOTE_TRUE = re.compile(r"^[ \t]*push_to_remote[ \t]*=[ \t]*true[ \t]*$", re.MULTILINE)


def _stage_pushes_locally(cfg_path: pathlib.Path) -> None:
    """Set the generated cfg's push_to_remote from true to false — the
    documented operator edit for local-only mode.

    create-project.sh seeds push_to_remote = true, and this fixture's dev
    repo has no origin: cancel-rc.sh would fail on its origin branch
    delete, and the Lane A drill stops at a failed cancel with the HALT
    and bundle doc left in place. Staging pushes locally — the same
    posture test_overwatch_lane_a_drill.py's own project.cfg and this
    file's legacy cfg template carry — lets both RC steps really succeed
    offline, so the resolved run below exercises the full drill.

    Fails loud unless exactly one live push_to_remote = true line is
    present: anything else means the generated shape changed and this
    edit would no longer mean what it says.
    """
    text = cfg_path.read_text(encoding="utf-8")
    matches = _LIVE_PUSH_TO_REMOTE_TRUE.findall(text)
    assert len(matches) == 1, (
        f"expected exactly one live 'push_to_remote = true' line in {cfg_path}, "
        f"found {len(matches)}:\n{text}"
    )
    cfg_path.write_text(
        _LIVE_PUSH_TO_REMOTE_TRUE.sub("push_to_remote = false", text, count=1),
        encoding="utf-8",
    )


def _rc_step_error_lines(actions_log: pathlib.Path) -> list:
    """Every auto-fix-error line in an OVERWATCH actions.log that names one
    of the Lane A drill's RC steps (unwind-rc.sh or cancel-rc.sh). Empty
    when the log is absent."""
    if not actions_log.is_file():
        return []
    return [
        line
        for line in actions_log.read_text(encoding="utf-8").splitlines()
        if "auto-fix-error" in line and re.search(r"(unwind|cancel)-rc\.sh", line)
    ]


def _flip_overwatch_resolve_true(cfg_path: pathlib.Path) -> None:
    """The one operator action the scaffolding is designed to require:
    uncomment the seeded key and set it true. A blind string replace on the
    exact commented form both create-project.sh and the migration script
    seed — if this replace matches nothing, the seeded shape changed and
    this test's precondition assumption needs revisiting, so it fails loud
    rather than silently leaving the key false."""
    text = cfg_path.read_text(encoding="utf-8")
    assert "# overwatch_resolve = false" in text, (
        f"expected the seeded commented form '# overwatch_resolve = false' in {cfg_path}, "
        f"found:\n{text}"
    )
    cfg_path.write_text(
        text.replace("# overwatch_resolve = false", "overwatch_resolve = true"),
        encoding="utf-8",
    )


# ---------------------------------------------------------------------------
# Fresh create-project.sh output
# ---------------------------------------------------------------------------


class TestFreshCreateProjectGatesResolution:
    def test_nothing_resolves_until_the_seeded_key_is_uncommented_and_set_true(
        self,
        tmp_path: pathlib.Path,
        pgai_tests_temp_dir: pathlib.Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        project_name = "fresh_scaffold_project"
        version = "v2.23.11"

        repo_path = tmp_path / "dev_repo"
        _init_git_repo_with_direct_commit(repo_path, "ai_main", f"ai_rc/{version}")

        kanban_root = _build_minimal_kanban_root(tmp_path)
        create_result = _run_create_project(kanban_root, project_name, repo_path, pgai_tests_temp_dir)
        assert create_result.returncode == 0, (
            f"create-project.sh failed.\nstdout: {create_result.stdout}\nstderr: {create_result.stderr}"
        )

        proj = kanban_root / "projects" / project_name
        cfg_path = proj / "project.cfg"
        cfg_before = cfg_path.read_text(encoding="utf-8")
        assert "# overwatch_resolve = false" in cfg_before, (
            f"expected create-project.sh's seeded commented key in {cfg_path}:\n{cfg_before}"
        )
        assert "\noverwatch_resolve = " not in cfg_before, (
            "the freshly generated cfg must not carry a live overwatch_resolve value"
        )
        intent_path = proj / "INTENT.md"
        assert intent_path.is_file()
        assert _live_intent_entry_lines(intent_path.read_text(encoding="utf-8")) == [], (
            "freshly seeded INTENT.md must carry zero live dated entries"
        )
        _stage_pushes_locally(cfg_path)

        _establish_drill_environment(monkeypatch, pgai_tests_temp_dir)
        _install_operator_verb_scripts(kanban_root)
        bug_id = "BUG-0201-inverted-column"
        halt_path = _overlay_lane_a_block(proj, version, bug_id, kanban_root=kanban_root, project_name=project_name)
        bug_text_before = (proj / "bugs" / f"{bug_id}.md").read_text(encoding="utf-8")
        release_state_before = (proj / "release-state.md").read_text(encoding="utf-8")

        # Still dark: the block declines and touches nothing.
        outcome_before = run_resolution_stage(project_name, str(kanban_root))
        assert outcome_before.status == DECLINED, outcome_before.reason
        assert "lane" not in outcome_before.reason.lower(), outcome_before.reason
        assert halt_path.is_file()
        assert (proj / "release-state.md").read_text(encoding="utf-8") == release_state_before
        assert (proj / "bugs" / f"{bug_id}.md").read_text(encoding="utf-8") == bug_text_before
        assert not (proj / "auto-resolutions.md").exists()

        # The one operator action: uncomment, set true.
        _flip_overwatch_resolve_true(cfg_path)

        # The identical block now resolves, with both RC steps succeeding.
        outcome_after = run_resolution_stage(project_name, str(kanban_root))
        assert outcome_after.status == EXECUTED, outcome_after.reason
        assert _rc_step_error_lines(proj / "overwatch" / "actions.log") == []
        assert not halt_path.exists()
        release_state_after = (proj / "release-state.md").read_text(encoding="utf-8")
        assert "## Active RC\nnone" in release_state_after
        assert "## OVERWATCH Decision" in release_state_after
        assert "Lane: A" in release_state_after


# ---------------------------------------------------------------------------
# Migrated (pre-v2.23.0) project.cfg
# ---------------------------------------------------------------------------


_LEGACY_CFG_TEMPLATE = """\
[project]
project_name = {name}
dev_tree_path = {dev_tree}
branch_prefix = ai_
push_to_remote = false
workflow_type = release

[versioning]
max_patch = 21
max_minor = 13
max_major = 0

[release]
max_rc_retries = 3

[debug]
verbose_mode = false
"""


class TestMigratedCfgGatesResolution:
    def test_nothing_resolves_until_the_migrated_key_is_uncommented_and_set_true(
        self,
        tmp_path: pathlib.Path,
        pgai_tests_temp_dir: pathlib.Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        project_name = "migrated_scaffold_project"
        version = "v2.23.12"

        repo_path = tmp_path / "dev_repo"
        _init_git_repo_with_direct_commit(repo_path, "ai_main", f"ai_rc/{version}")

        kanban_root = _build_minimal_kanban_root(tmp_path)
        proj = kanban_root / "projects" / project_name
        proj.mkdir(parents=True, exist_ok=True)
        cfg_path = proj / "project.cfg"
        cfg_path.write_text(
            _LEGACY_CFG_TEMPLATE.format(name=project_name, dev_tree=repo_path), encoding="utf-8"
        )
        (kanban_root / "projects.cfg").write_text(
            f"[project:{project_name}]\npriority=1\n", encoding="utf-8"
        )

        migration_result = _run_migration(kanban_root, project_name)
        assert migration_result.returncode == 0, (
            f"migration failed.\nstdout: {migration_result.stdout}\nstderr: {migration_result.stderr}"
        )
        cfg_after_migration = cfg_path.read_text(encoding="utf-8")
        assert "# overwatch_resolve = false" in cfg_after_migration
        assert "\noverwatch_resolve = " not in cfg_after_migration

        _establish_drill_environment(monkeypatch, pgai_tests_temp_dir)
        _install_operator_verb_scripts(kanban_root)
        bug_id = "BUG-0202-inverted-column"
        halt_path = _overlay_lane_a_block(proj, version, bug_id, kanban_root=kanban_root, project_name=project_name)
        bug_text_before = (proj / "bugs" / f"{bug_id}.md").read_text(encoding="utf-8")
        release_state_before = (proj / "release-state.md").read_text(encoding="utf-8")

        outcome_before = run_resolution_stage(project_name, str(kanban_root))
        assert outcome_before.status == DECLINED, outcome_before.reason
        assert "lane" not in outcome_before.reason.lower(), outcome_before.reason
        assert halt_path.is_file()
        assert (proj / "release-state.md").read_text(encoding="utf-8") == release_state_before
        assert (proj / "bugs" / f"{bug_id}.md").read_text(encoding="utf-8") == bug_text_before

        _flip_overwatch_resolve_true(cfg_path)

        outcome_after = run_resolution_stage(project_name, str(kanban_root))
        assert outcome_after.status == EXECUTED, outcome_after.reason
        assert not halt_path.exists()
        release_state_after = (proj / "release-state.md").read_text(encoding="utf-8")
        assert "## Active RC\nnone" in release_state_after
        assert "Lane: A" in release_state_after
