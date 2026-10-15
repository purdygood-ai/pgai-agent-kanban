"""
test_migrate_overwatch_resolve_seed.py
=======================================
Integration tests for team/scripts/migrations/migrate_v2.22.5_to_v2.23.0.sh.

These tests exercise the real migration script end-to-end against synthetic
project.cfg fixtures shaped like pre-v2.23.0 installs (no overwatch_resolve
key present, in various [release]-section shapes). No seam is mocked --
each test stands up a real temp kanban root with a registered project, runs
the real script, and asserts on the resulting on-disk project.cfg.

Coverage:
  - Seeds the commented key into a cfg that lacks it (single-project mode)
  - Running the migration twice is idempotent (byte-identical second run)
  - An operator-set overwatch_resolve value is preserved untouched
  - A diff against the original cfg shows only the added commented block
  - A cfg with no [release] section at all still gets seeded
  - A cfg where [release] is the last section in the file still gets seeded
  - --dry-run previews without writing
  - No --project fans out over every registered project
  - The seeded key resolves to 'false' through pp_overwatch_resolve

Design notes:
  - All tests use tmp_path for scratch. No bare /tmp paths appear in this file.
  - Test names describe the behavior under test; no bug IDs, version numbers,
    or gate tokens appear in function names (roles/TESTER.md Anti-pattern 6).
    The migration script's own filename is version-suffixed by the project's
    naming convention (team/scripts/migrations/README.md); naming the artifact
    under test is not the same as naming a test after its own provenance.
"""

from __future__ import annotations

import difflib
import os
import pathlib
import re
import subprocess

_TEAM_DIR = pathlib.Path(__file__).parent.parent.parent  # team/
_MIGRATION_SCRIPT = _TEAM_DIR / "scripts" / "migrations" / "migrate_v2.22.5_to_v2.23.0.sh"

_LEGACY_CFG_WITH_RELEASE_SECTION = """\
[project]
project_name = {name}
workflow_type = release

[versioning]
max_patch = 21
max_minor = 13
max_major = 0

# ---------------------------------------------------------------------------
# [release] -- DEEP-VERIFY scheduled-dispatch settings (v2.18.0+)
# ---------------------------------------------------------------------------
[release]

# deep_verify: this project's DEEP-VERIFY scheduled-dispatch cadence --
# read by pp_deep_verify_mode() (team/scripts/lib/project_paths.sh).
# Accepted: weekly|off. Default: weekly
# deep_verify = weekly

# ---------------------------------------------------------------------------
# [debug] -- Diagnostic and introspection settings
# ---------------------------------------------------------------------------
[debug]
verbose_mode = false
"""

_LEGACY_CFG_NO_RELEASE_SECTION = """\
[project]
project_name = {name}
workflow_type = release

[versioning]
max_patch = 21
"""

_LEGACY_CFG_RELEASE_IS_LAST_SECTION = """\
[project]
project_name = {name}

[release]
# deep_verify = weekly
"""

_CFG_WITH_OPERATOR_SET_TRUE = """\
[project]
project_name = {name}

[release]
overwatch_resolve = true

[debug]
verbose_mode = false
"""


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _build_kanban_root_with_project(
    parent: pathlib.Path,
    project_name: str,
    cfg_template: str,
    *,
    extra_projects: dict[str, str] | None = None,
) -> pathlib.Path:
    """Build a temp kanban root with one registered project (plus any extras).

    Args:
        parent: Parent directory for the kanban root.
        project_name: The primary project's name.
        cfg_template: project.cfg content template (formatted with name=).
        extra_projects: Optional {name: cfg_template} for additional
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

    all_projects = {project_name: cfg_template}
    if extra_projects:
        all_projects.update(extra_projects)

    registry_lines = []
    for i, name in enumerate(all_projects, start=1):
        registry_lines.append(f"[project:{name}]\npriority={i}\n")
    (root / "projects.cfg").write_text("\n".join(registry_lines), encoding="utf-8")

    for name, template in all_projects.items():
        proj_dir = root / "projects" / name
        proj_dir.mkdir(parents=True, exist_ok=True)
        (proj_dir / "project.cfg").write_text(
            template.format(name=name), encoding="utf-8"
        )

    return root


def _cfg_path(kanban_root: pathlib.Path, project_name: str) -> pathlib.Path:
    return kanban_root / "projects" / project_name / "project.cfg"


def _run_migration(
    kanban_root: pathlib.Path,
    *,
    project: str | None = None,
    dry_run: bool = False,
    timeout: int = 30,
) -> subprocess.CompletedProcess:
    """Run migrate_v2.22.5_to_v2.23.0.sh against a temp kanban root.

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


def _count_key_occurrences(cfg_text: str) -> int:
    """Count lines that set/mention overwatch_resolve= (commented or not)."""
    return len(
        re.findall(r"^\s*#?\s*overwatch_resolve\s*=", cfg_text, re.MULTILINE)
    )


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


class TestMigrationSeedsMissingKey:
    """The migration adds the commented key exactly once to a cfg that lacks it."""

    def test_adds_exactly_one_occurrence_of_the_key_to_a_cfg_missing_it(
        self, tmp_path: pathlib.Path
    ) -> None:
        root = _build_kanban_root_with_project(
            tmp_path, "legacy-proj", _LEGACY_CFG_WITH_RELEASE_SECTION
        )
        cfg_path = _cfg_path(root, "legacy-proj")
        before_text = cfg_path.read_text(encoding="utf-8")
        assert _count_key_occurrences(before_text) == 0, (
            "Fixture must not already carry overwatch_resolve."
        )

        result = _run_migration(root, project="legacy-proj")
        assert result.returncode == 0, (
            f"migration failed.\nstdout: {result.stdout}\nstderr: {result.stderr}"
        )

        after_text = cfg_path.read_text(encoding="utf-8")
        assert _count_key_occurrences(after_text) == 1, (
            f"Expected exactly one overwatch_resolve occurrence after migration.\n{after_text}"
        )
        assert "\noverwatch_resolve = " not in after_text, (
            f"The seeded key must be commented out, never live.\n{after_text}"
        )

    def test_running_twice_adds_no_additional_occurrence(
        self, tmp_path: pathlib.Path
    ) -> None:
        root = _build_kanban_root_with_project(
            tmp_path, "legacy-proj-2", _LEGACY_CFG_WITH_RELEASE_SECTION
        )
        cfg_path = _cfg_path(root, "legacy-proj-2")

        first = _run_migration(root, project="legacy-proj-2")
        assert first.returncode == 0, (
            f"first migration run failed.\nstdout: {first.stdout}\nstderr: {first.stderr}"
        )
        after_first = cfg_path.read_text(encoding="utf-8")

        second = _run_migration(root, project="legacy-proj-2")
        assert second.returncode == 0, (
            f"second migration run failed.\nstdout: {second.stdout}\nstderr: {second.stderr}"
        )
        after_second = cfg_path.read_text(encoding="utf-8")

        assert _count_key_occurrences(after_second) == 1, (
            f"A second run must not add a duplicate occurrence.\n{after_second}"
        )
        assert after_first == after_second, (
            "Running the migration a second time must be a byte-identical no-op.\n"
            f"after first run:\n{after_first}\nafter second run:\n{after_second}"
        )

    def test_diff_against_original_shows_only_the_added_commented_block(
        self, tmp_path: pathlib.Path
    ) -> None:
        root = _build_kanban_root_with_project(
            tmp_path, "legacy-proj-3", _LEGACY_CFG_WITH_RELEASE_SECTION
        )
        cfg_path = _cfg_path(root, "legacy-proj-3")
        original_text = cfg_path.read_text(encoding="utf-8")

        result = _run_migration(root, project="legacy-proj-3")
        assert result.returncode == 0, (
            f"migration failed.\nstdout: {result.stdout}\nstderr: {result.stderr}"
        )
        migrated_text = cfg_path.read_text(encoding="utf-8")

        diff_lines = list(
            difflib.unified_diff(
                original_text.splitlines(),
                migrated_text.splitlines(),
                lineterm="",
            )
        )
        removed = [
            line for line in diff_lines
            if line.startswith("-") and not line.startswith("---")
        ]
        added = [
            line for line in diff_lines
            if line.startswith("+") and not line.startswith("+++")
        ]
        assert removed == [], (
            f"Migration must never remove or alter an existing line.\ndiff:\n"
            + "\n".join(diff_lines)
        )
        assert any("overwatch_resolve" in line for line in added), (
            f"Expected the diff's added lines to include the overwatch_resolve block.\n"
            + "\n".join(diff_lines)
        )

    def test_seeds_a_cfg_with_no_release_section_at_all(
        self, tmp_path: pathlib.Path
    ) -> None:
        """A pre-v2.18.0-shaped cfg with no [release] section still gets seeded."""
        root = _build_kanban_root_with_project(
            tmp_path, "no-release-proj", _LEGACY_CFG_NO_RELEASE_SECTION
        )
        result = _run_migration(root, project="no-release-proj")
        assert result.returncode == 0, (
            f"migration failed.\nstdout: {result.stdout}\nstderr: {result.stderr}"
        )

        after_text = _cfg_path(root, "no-release-proj").read_text(encoding="utf-8")
        assert re.search(r"^\[release\]\s*$", after_text, re.MULTILINE), (
            f"Expected a [release] section to be created.\n{after_text}"
        )
        assert _count_key_occurrences(after_text) == 1, (
            f"Expected exactly one overwatch_resolve occurrence.\n{after_text}"
        )

    def test_seeds_a_cfg_where_release_is_the_last_section_in_the_file(
        self, tmp_path: pathlib.Path
    ) -> None:
        root = _build_kanban_root_with_project(
            tmp_path, "release-last-proj", _LEGACY_CFG_RELEASE_IS_LAST_SECTION
        )
        result = _run_migration(root, project="release-last-proj")
        assert result.returncode == 0, (
            f"migration failed.\nstdout: {result.stdout}\nstderr: {result.stderr}"
        )

        after_text = _cfg_path(root, "release-last-proj").read_text(encoding="utf-8")
        assert _count_key_occurrences(after_text) == 1, (
            f"Expected exactly one overwatch_resolve occurrence.\n{after_text}"
        )
        # The pre-existing deep_verify comment line must still be present and
        # precede the newly-appended block -- proving insertion, not replacement.
        assert "# deep_verify = weekly" in after_text
        assert after_text.index("# deep_verify = weekly") < after_text.index(
            "overwatch_resolve"
        )


class TestMigrationPreservesOperatorValue:
    """An operator-set overwatch_resolve value is never touched."""

    def test_existing_true_value_is_left_byte_for_byte_unchanged(
        self, tmp_path: pathlib.Path
    ) -> None:
        root = _build_kanban_root_with_project(
            tmp_path, "operator-set-proj", _CFG_WITH_OPERATOR_SET_TRUE
        )
        cfg_path = _cfg_path(root, "operator-set-proj")
        original_text = cfg_path.read_text(encoding="utf-8")

        result = _run_migration(root, project="operator-set-proj")
        assert result.returncode == 0, (
            f"migration failed.\nstdout: {result.stdout}\nstderr: {result.stderr}"
        )

        after_text = cfg_path.read_text(encoding="utf-8")
        assert after_text == original_text, (
            "An operator-set overwatch_resolve value must be left byte-for-byte "
            f"unchanged.\nbefore:\n{original_text}\nafter:\n{after_text}"
        )
        assert "overwatch_resolve = true" in after_text, (
            "The migration must never flip an operator's true value to false "
            f"or comment it out.\n{after_text}"
        )


class TestMigrationDryRun:
    """--dry-run previews without writing."""

    def test_dry_run_makes_no_change_to_the_cfg(self, tmp_path: pathlib.Path) -> None:
        root = _build_kanban_root_with_project(
            tmp_path, "dry-run-proj", _LEGACY_CFG_WITH_RELEASE_SECTION
        )
        cfg_path = _cfg_path(root, "dry-run-proj")
        original_text = cfg_path.read_text(encoding="utf-8")

        result = _run_migration(root, project="dry-run-proj", dry_run=True)
        assert result.returncode == 0, (
            f"dry-run failed.\nstdout: {result.stdout}\nstderr: {result.stderr}"
        )

        after_text = cfg_path.read_text(encoding="utf-8")
        assert after_text == original_text, (
            f"--dry-run must not write to project.cfg.\nstdout: {result.stdout}"
        )


class TestMigrationFanOutOverAllProjects:
    """With no --project, the migration seeds every registered project."""

    def test_seeds_every_registered_project_when_no_project_given(
        self, tmp_path: pathlib.Path
    ) -> None:
        root = _build_kanban_root_with_project(
            tmp_path,
            "fanout-proj-a",
            _LEGACY_CFG_WITH_RELEASE_SECTION,
            extra_projects={"fanout-proj-b": _LEGACY_CFG_NO_RELEASE_SECTION},
        )

        result = _run_migration(root)
        assert result.returncode == 0, (
            f"fan-out migration failed.\nstdout: {result.stdout}\nstderr: {result.stderr}"
        )

        for name in ("fanout-proj-a", "fanout-proj-b"):
            after_text = _cfg_path(root, name).read_text(encoding="utf-8")
            assert _count_key_occurrences(after_text) == 1, (
                f"Expected {name}'s project.cfg to be seeded by the fan-out run.\n{after_text}"
            )

    def test_fan_out_is_idempotent_across_the_whole_registry(
        self, tmp_path: pathlib.Path
    ) -> None:
        root = _build_kanban_root_with_project(
            tmp_path,
            "fanout-idem-a",
            _LEGACY_CFG_WITH_RELEASE_SECTION,
            extra_projects={"fanout-idem-b": _LEGACY_CFG_WITH_RELEASE_SECTION},
        )

        first = _run_migration(root)
        assert first.returncode == 0
        snapshot = {
            name: _cfg_path(root, name).read_text(encoding="utf-8")
            for name in ("fanout-idem-a", "fanout-idem-b")
        }

        second = _run_migration(root)
        assert second.returncode == 0

        for name in ("fanout-idem-a", "fanout-idem-b"):
            assert _cfg_path(root, name).read_text(encoding="utf-8") == snapshot[name], (
                f"Second fan-out run must be a byte-identical no-op for {name}."
            )


class TestMigratedKeyResolvesThroughTheSharedAccessor:
    """The seeded key resolves to 'false' through pp_overwatch_resolve, exactly
    like a freshly create-project.sh-generated project.cfg does.
    """

    def test_pp_overwatch_resolve_returns_false_after_migration(
        self, tmp_path: pathlib.Path
    ) -> None:
        root = _build_kanban_root_with_project(
            tmp_path, "resolve-after-migrate-proj", _LEGACY_CFG_WITH_RELEASE_SECTION
        )
        result = _run_migration(root, project="resolve-after-migrate-proj")
        assert result.returncode == 0, (
            f"migration failed.\nstdout: {result.stdout}\nstderr: {result.stderr}"
        )

        env = dict(os.environ)
        env["KANBAN_ROOT"] = str(root)
        env["PGAI_PROJECT_NAME"] = "resolve-after-migrate-proj"

        resolve_result = subprocess.run(
            ["bash", "-c", "source scripts/lib/project_paths.sh && pp_overwatch_resolve"],
            capture_output=True,
            text=True,
            env=env,
            cwd=str(_TEAM_DIR),
            timeout=30,
        )
        assert resolve_result.returncode == 0, (
            f"pp_overwatch_resolve failed.\nstdout: {resolve_result.stdout}\nstderr: {resolve_result.stderr}"
        )
        assert resolve_result.stdout.strip() == "false", (
            f"Expected pp_overwatch_resolve to resolve to 'false' after migration.\n"
            f"stdout: {resolve_result.stdout}"
        )
