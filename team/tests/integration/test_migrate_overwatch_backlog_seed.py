"""
test_migrate_overwatch_backlog_seed.py
=======================================
Integration tests for team/scripts/migrations/migrate_v2.24.7_to_v2.24.8.sh,
a narrated no-op: OVERWATCH keeps no queue file, so the step writes nothing
and says so, while still validating its arguments.

These tests run the real migration script end-to-end against synthetic
project fixtures. No seam is mocked -- each test stands up a real temp
kanban root with a registered project, runs the real script, and asserts on
its output and the resulting on-disk state.

Coverage:
  - A project with no tasks/queues/overwatch_backlog.md gets none (and no
    tasks/queues/ directory); the run narrates the no-op and exits 0
  - An existing overwatch_backlog.md -- empty or carrying real entries -- is
    left byte-for-byte unchanged
  - --dry-run behaves the same and writes nothing
  - No --project narrates the no-op for every registered project
  - --help still names every flag

Design notes:
  - All tests use tmp_path for scratch. No bare /tmp paths appear in this file.
  - The migration script's own filename is version-suffixed by the project's
    naming convention (team/scripts/migrations/README.md); naming the artifact
    under test is not the same as naming a test after its own provenance.
"""

from __future__ import annotations

import os
import pathlib
import subprocess

_TEAM_DIR = pathlib.Path(__file__).parent.parent.parent  # team/
_MIGRATION_SCRIPT = _TEAM_DIR / "scripts" / "migrations" / "migrate_v2.24.7_to_v2.24.8.sh"


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _build_kanban_root_with_project(
    parent: pathlib.Path,
    project_name: str,
    *,
    seed_backlog_content: str | None = None,
    extra_projects: dict[str, str | None] | None = None,
) -> pathlib.Path:
    """Build a temp kanban root with one registered project (plus any extras).

    Args:
        parent: Parent directory for the kanban root.
        project_name: The primary project's name.
        seed_backlog_content: If given, pre-create
            tasks/queues/overwatch_backlog.md with this content before running
            the migration (simulates an already-seeded or hand-authored file).
            If None, the project starts with no queue file at all.
        extra_projects: Optional {name: seed_backlog_content} for additional
            registered projects, used by the fan-out tests.

    Returns:
        pathlib.Path -- the kanban root directory.
    """
    root = parent / "kanban_root"
    root.mkdir(parents=True, exist_ok=True)

    (root / "kanban.cfg").write_text(
        "[paths]\n\n[chain]\npm_mode = automatic\n\n[wake]\nmax_tasks_per_wake = 1\n",
        encoding="utf-8",
    )
    (root / "logs").mkdir(parents=True, exist_ok=True)

    all_projects = {project_name: seed_backlog_content}
    if extra_projects:
        all_projects.update(extra_projects)

    registry_lines = []
    for i, name in enumerate(all_projects, start=1):
        registry_lines.append(f"[project:{name}]\npriority={i}\n")
    (root / "projects.cfg").write_text("\n".join(registry_lines), encoding="utf-8")

    for name, content in all_projects.items():
        proj_dir = root / "projects" / name
        proj_dir.mkdir(parents=True, exist_ok=True)
        (proj_dir / "project.cfg").write_text(
            f"[project]\nproject_name = {name}\n", encoding="utf-8"
        )
        if content is not None:
            queues_dir = proj_dir / "tasks" / "queues"
            queues_dir.mkdir(parents=True, exist_ok=True)
            (queues_dir / "overwatch_backlog.md").write_text(content, encoding="utf-8")

    return root


def _backlog_path(kanban_root: pathlib.Path, project_name: str) -> pathlib.Path:
    return kanban_root / "projects" / project_name / "tasks" / "queues" / "overwatch_backlog.md"


def _run_migration(
    kanban_root: pathlib.Path,
    *,
    project: str | None = None,
    dry_run: bool = False,
    timeout: int = 30,
) -> subprocess.CompletedProcess:
    """Run migrate_v2.24.7_to_v2.24.8.sh against a temp kanban root.

    PGAI_PROJECT_NAME is explicitly cleared (never inherited) so a
    no-`--project` invocation reliably exercises the all-registered-
    projects fan-out rather than picking up whatever project name the
    invoking shell happens to carry.
    """
    env = dict(os.environ)
    env["PGAI_AGENT_KANBAN_ROOT_PATH"] = str(kanban_root)
    env.pop("PGAI_PROJECT_NAME", None)
    env.pop("PGAI_DEV_TREE_PATH", None)

    cmd = ["bash", str(_MIGRATION_SCRIPT)]
    if project is not None:
        cmd.extend(["--project", project])
    if dry_run:
        cmd.append("--dry-run")

    return subprocess.run(
        cmd,
        capture_output=True,
        text=True,
        env=env,
        cwd=str(kanban_root),
        timeout=timeout,
    )


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


class TestMigrationIsANarratedNoOp:
    def test_project_without_a_queue_file_gets_none(self, tmp_path: pathlib.Path) -> None:
        root = _build_kanban_root_with_project(tmp_path, "legacy-proj")
        queues_dir = root / "projects" / "legacy-proj" / "tasks" / "queues"

        result = _run_migration(root, project="legacy-proj")

        assert result.returncode == 0, (
            f"migration failed.\nstdout: {result.stdout}\nstderr: {result.stderr}"
        )
        assert not queues_dir.exists(), (
            f"The no-op migration must create nothing under {queues_dir}.\nstdout: {result.stdout}"
        )
        assert "[no-op]  legacy-proj:" in result.stdout, result.stdout

    def test_running_twice_changes_nothing(self, tmp_path: pathlib.Path) -> None:
        root = _build_kanban_root_with_project(tmp_path, "legacy-proj-2")
        before = sorted(str(p.relative_to(root)) for p in root.rglob("*"))

        for _ in range(2):
            result = _run_migration(root, project="legacy-proj-2")
            assert result.returncode == 0, (
                f"migration failed.\nstdout: {result.stdout}\nstderr: {result.stderr}"
            )

        assert sorted(str(p.relative_to(root)) for p in root.rglob("*")) == before


class TestMigrationPreservesExistingBacklog:
    """A leftover overwatch_backlog.md -- empty or with real entries -- is
    never touched."""

    def test_existing_empty_backlog_is_left_byte_for_byte_unchanged(
        self, tmp_path: pathlib.Path
    ) -> None:
        root = _build_kanban_root_with_project(
            tmp_path, "already-seeded-proj", seed_backlog_content="# Overwatch Backlog\n\n"
        )
        backlog = _backlog_path(root, "already-seeded-proj")
        original_text = backlog.read_text(encoding="utf-8")

        result = _run_migration(root, project="already-seeded-proj")
        assert result.returncode == 0, (
            f"migration failed.\nstdout: {result.stdout}\nstderr: {result.stderr}"
        )

        assert backlog.read_text(encoding="utf-8") == original_text, (
            "An existing overwatch_backlog.md must be left byte-for-byte unchanged."
        )

    def test_existing_backlog_with_real_entries_is_left_untouched(
        self, tmp_path: pathlib.Path
    ) -> None:
        entries_text = "# Overwatch Backlog\n\n- [ ] some-hand-added-entry\n"
        root = _build_kanban_root_with_project(
            tmp_path, "operator-populated-proj", seed_backlog_content=entries_text
        )
        backlog = _backlog_path(root, "operator-populated-proj")

        result = _run_migration(root, project="operator-populated-proj")
        assert result.returncode == 0, (
            f"migration failed.\nstdout: {result.stdout}\nstderr: {result.stderr}"
        )

        assert backlog.read_text(encoding="utf-8") == entries_text, (
            "The migration must never touch an existing queue file."
        )


class TestMigrationDryRun:
    def test_dry_run_is_accepted_and_writes_nothing(self, tmp_path: pathlib.Path) -> None:
        root = _build_kanban_root_with_project(tmp_path, "dry-run-proj")
        backlog = _backlog_path(root, "dry-run-proj")

        result = _run_migration(root, project="dry-run-proj", dry_run=True)
        assert result.returncode == 0, (
            f"dry-run failed.\nstdout: {result.stdout}\nstderr: {result.stderr}"
        )

        assert not backlog.exists(), f"--dry-run wrote {backlog}.\nstdout: {result.stdout}"
        assert "[no-op]  dry-run-proj:" in result.stdout, result.stdout


class TestMigrationFanOutOverAllProjects:
    def test_narrates_every_registered_project_and_writes_nothing(
        self, tmp_path: pathlib.Path
    ) -> None:
        root = _build_kanban_root_with_project(
            tmp_path,
            "fanout-proj-a",
            extra_projects={"fanout-proj-b": "# Overwatch Backlog\n\n"},
        )
        kept = _backlog_path(root, "fanout-proj-b")
        kept_text = kept.read_text(encoding="utf-8")

        result = _run_migration(root)
        assert result.returncode == 0, (
            f"fan-out migration failed.\nstdout: {result.stdout}\nstderr: {result.stderr}"
        )

        for name in ("fanout-proj-a", "fanout-proj-b"):
            assert f"[no-op]  {name}:" in result.stdout, result.stdout
        assert not _backlog_path(root, "fanout-proj-a").exists()
        assert kept.read_text(encoding="utf-8") == kept_text


class TestMigrationHelp:
    def test_help_names_every_flag(self, tmp_path: pathlib.Path) -> None:
        root = _build_kanban_root_with_project(tmp_path, "help-proj")
        env = dict(os.environ)
        env["PGAI_AGENT_KANBAN_ROOT_PATH"] = str(root)
        result = subprocess.run(
            ["bash", str(_MIGRATION_SCRIPT), "--help"],
            capture_output=True, text=True, env=env, cwd=str(root), timeout=30,
        )
        assert result.returncode == 0, result.stderr
        for flag in ("--project", "--dry-run", "--help"):
            assert flag in result.stderr, result.stderr
