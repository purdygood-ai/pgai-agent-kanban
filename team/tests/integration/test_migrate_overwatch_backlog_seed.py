"""
test_migrate_overwatch_backlog_seed.py
=======================================
Integration tests for team/scripts/migrations/migrate_v2.24.7_to_v2.24.8.sh.

These tests exercise the real migration script end-to-end against synthetic
project fixtures shaped like pre-v2.24.8 installs (no
tasks/queues/overwatch_backlog.md at all). No seam is mocked -- each test
stands up a real temp kanban root with a registered project, runs the real
script, and asserts on the resulting on-disk state.

Coverage:
  - Creates the queue file for a project that lacks it (single-project mode)
  - Running the migration twice is idempotent (byte-identical second run)
  - An existing overwatch_backlog.md -- empty or carrying real entries -- is
    left untouched
  - No --project fans out over every registered project
  - The queue file's own tasks/queues/ directory is created when missing

Design notes:
  - All tests use tmp_path for scratch. No bare /tmp paths appear in this file.
  - Test names describe the behavior under test; no bug IDs, version numbers,
    or gate tokens appear in function names (roles/TESTER.md Anti-pattern 6).
    The migration script's own filename is version-suffixed by the project's
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
            If None, the project starts with no queue file at all -- the
            common pre-v2.24.8 shape this migration targets.
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


class TestMigrationSeedsMissingBacklog:
    """The migration creates overwatch_backlog.md for a project that lacks it."""

    def test_creates_the_queue_file_when_absent(self, tmp_path: pathlib.Path) -> None:
        root = _build_kanban_root_with_project(tmp_path, "legacy-proj")
        backlog = _backlog_path(root, "legacy-proj")
        assert not backlog.exists(), "Fixture must not already carry the queue file."

        result = _run_migration(root, project="legacy-proj")
        assert result.returncode == 0, (
            f"migration failed.\nstdout: {result.stdout}\nstderr: {result.stderr}"
        )

        assert backlog.is_file(), (
            f"Expected overwatch_backlog.md to be created at {backlog}.\n"
            f"stdout: {result.stdout}"
        )
        content = backlog.read_text(encoding="utf-8")
        assert "Overwatch Backlog" in content, f"Expected a title line.\n{content}"

    def test_creates_the_queues_directory_when_missing(self, tmp_path: pathlib.Path) -> None:
        """A project whose tasks/queues/ directory does not exist at all still
        gets it created -- not just the file inside it."""
        root = _build_kanban_root_with_project(tmp_path, "no-queues-dir-proj")
        queues_dir = root / "projects" / "no-queues-dir-proj" / "tasks" / "queues"
        assert not queues_dir.exists()

        result = _run_migration(root, project="no-queues-dir-proj")
        assert result.returncode == 0, (
            f"migration failed.\nstdout: {result.stdout}\nstderr: {result.stderr}"
        )
        assert _backlog_path(root, "no-queues-dir-proj").is_file()

    def test_running_twice_is_a_byte_identical_no_op(self, tmp_path: pathlib.Path) -> None:
        root = _build_kanban_root_with_project(tmp_path, "legacy-proj-2")
        backlog = _backlog_path(root, "legacy-proj-2")

        first = _run_migration(root, project="legacy-proj-2")
        assert first.returncode == 0, (
            f"first migration run failed.\nstdout: {first.stdout}\nstderr: {first.stderr}"
        )
        after_first = backlog.read_text(encoding="utf-8")

        second = _run_migration(root, project="legacy-proj-2")
        assert second.returncode == 0, (
            f"second migration run failed.\nstdout: {second.stdout}\nstderr: {second.stderr}"
        )
        after_second = backlog.read_text(encoding="utf-8")

        assert after_first == after_second, (
            "Running the migration a second time must be a byte-identical no-op.\n"
            f"after first run:\n{after_first}\nafter second run:\n{after_second}"
        )


class TestMigrationPreservesExistingBacklog:
    """An already-present overwatch_backlog.md -- empty or with real entries
    -- is never touched."""

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
            "The migration must never overwrite an operator-populated queue file."
        )


class TestMigrationDryRun:
    """--dry-run previews without writing."""

    def test_dry_run_makes_no_change(self, tmp_path: pathlib.Path) -> None:
        root = _build_kanban_root_with_project(tmp_path, "dry-run-proj")
        backlog = _backlog_path(root, "dry-run-proj")

        result = _run_migration(root, project="dry-run-proj", dry_run=True)
        assert result.returncode == 0, (
            f"dry-run failed.\nstdout: {result.stdout}\nstderr: {result.stderr}"
        )

        assert not backlog.exists(), (
            f"--dry-run must not create overwatch_backlog.md.\nstdout: {result.stdout}"
        )


class TestMigrationFanOutOverAllProjects:
    """With no --project, the migration seeds every registered project."""

    def test_seeds_every_registered_project_when_no_project_given(
        self, tmp_path: pathlib.Path
    ) -> None:
        root = _build_kanban_root_with_project(
            tmp_path,
            "fanout-proj-a",
            extra_projects={"fanout-proj-b": None},
        )

        result = _run_migration(root)
        assert result.returncode == 0, (
            f"fan-out migration failed.\nstdout: {result.stdout}\nstderr: {result.stderr}"
        )

        for name in ("fanout-proj-a", "fanout-proj-b"):
            assert _backlog_path(root, name).is_file(), (
                f"Expected {name}'s overwatch_backlog.md to be seeded by the fan-out run."
            )

    def test_fan_out_leaves_an_already_seeded_project_untouched(
        self, tmp_path: pathlib.Path
    ) -> None:
        root = _build_kanban_root_with_project(
            tmp_path,
            "fanout-fresh-proj",
            extra_projects={"fanout-seeded-proj": "# Overwatch Backlog\n\n"},
        )
        seeded_backlog = _backlog_path(root, "fanout-seeded-proj")
        original_text = seeded_backlog.read_text(encoding="utf-8")

        result = _run_migration(root)
        assert result.returncode == 0

        assert _backlog_path(root, "fanout-fresh-proj").is_file()
        assert seeded_backlog.read_text(encoding="utf-8") == original_text, (
            "The fan-out run must not touch a project that already has the queue file."
        )
