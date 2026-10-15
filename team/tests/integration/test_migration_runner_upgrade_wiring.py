"""
test_migration_runner_upgrade_wiring.py
========================================
Integration tests for the destination-version-selected migration runner
(team/scripts/migrations/run-migrations.sh) and its wiring into the upgrade
path (team/scripts/upgrade.sh, phase 2 body).

Before this runner existed, a migration script could work correctly by hand
yet never run automatically: nothing on the upgrade path invoked it, so an
existing install could upgrade straight past a config-format change and keep
a stale project.cfg with no operator signal that anything was missed. These
tests exercise the two layers that close that gap:

  1. run-migrations.sh directly -- the destination-keyed selection rule
     (only scripts whose destination falls in (--from, --to] run, in
     ascending order), the sparse no-op case, usage-error rejection, and
     that it forwards through to the real migration script correctly
     (seed / idempotent / operator-value-preserved).
  2. team/scripts/upgrade.sh's phase-2 body -- invoked directly with
     --phase2 (bypassing the phase-1 backup/bootstrap-probe wrapper, which
     performs no deposit or config work of its own) against a from-scratch
     synthetic kanban root, to prove the real, documented upgrade path
     (not just the runner in isolation) applies the migration and is
     idempotent on a second run.

No seam is mocked -- every test stands up a real temp kanban root and runs
the real scripts as subprocesses, asserting on the resulting on-disk
project.cfg and process exit codes.

Design notes:
  - All tests use tmp_path for scratch. No bare /tmp paths appear in this
    file.
  - PGAI_PROJECT_NAME and PGAI_DEV_TREE_PATH are explicitly cleared in every
    subprocess environment so an ambient value from the invoking (pytest)
    shell can never leak into a "run for every project" / "use this dev
    tree" default and silently narrow test coverage.
  - Test names describe the behavior under test; no bug IDs, version
    numbers, or gate tokens appear in function names (roles/TESTER.md
    Anti-pattern 6). The scripts' own filenames are version-suffixed by the
    project's naming convention (team/scripts/migrations/README.md); naming
    the artifact under test is not the same as naming a test after its own
    provenance.
"""

from __future__ import annotations

import os
import pathlib
import re
import subprocess

_TEAM_DIR = pathlib.Path(__file__).parent.parent.parent  # team/
_RUNNER_SCRIPT = _TEAM_DIR / "scripts" / "migrations" / "run-migrations.sh"
_UPGRADE_SCRIPT = _TEAM_DIR / "scripts" / "upgrade.sh"

_CFG_MISSING_KEY = """\
[project]
project_name = {name}
workflow_type = release

[release]
some_other_key = value
"""

_CFG_WITH_OPERATOR_SET_TRUE = """\
[project]
project_name = {name}

[release]
overwatch_resolve = true
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
    """Build a minimal temp kanban root with one registered project.

    Deliberately does NOT pre-populate scripts/roles/etc: run-migrations.sh
    tests invoke the dev tree's own copy directly (it looks for sibling
    migrate_v*.sh scripts next to itself, not under the kanban root), and
    the upgrade.sh phase-2 test relies on upgrade.sh's own deposit loop to
    populate scripts/ (including scripts/migrations/) fresh -- exercising
    the real deposit-then-migrate sequence rather than a pre-seeded fixture.
    """
    root = parent / "kanban_root"
    root.mkdir(parents=True, exist_ok=True)
    (root / "kanban.cfg").write_text("[paths]\n\n[release]\n", encoding="utf-8")

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


def _clean_env(kanban_root: pathlib.Path) -> dict[str, str]:
    """Base subprocess environment: real env with kanban-root pinned and the
    two ambient variables that could silently narrow scope cleared."""
    env = dict(os.environ)
    env["PGAI_AGENT_KANBAN_ROOT_PATH"] = str(kanban_root)
    env.pop("PGAI_PROJECT_NAME", None)
    env.pop("PGAI_DEV_TREE_PATH", None)
    return env


def _run_runner(
    kanban_root: pathlib.Path,
    *,
    from_version: str,
    to_version: str,
    project: str | None = None,
    dry_run: bool = False,
    timeout: int = 30,
) -> subprocess.CompletedProcess:
    env = _clean_env(kanban_root)
    cmd = ["bash", str(_RUNNER_SCRIPT), "--from", from_version, "--to", to_version]
    if project is not None:
        cmd.extend(["--project", project])
    if dry_run:
        cmd.append("--dry-run")
    return subprocess.run(
        cmd, capture_output=True, text=True, env=env, cwd=str(kanban_root), timeout=timeout
    )


def _count_key_occurrences(cfg_text: str) -> int:
    return len(
        re.findall(r"^\s*#?\s*overwatch_resolve\s*=", cfg_text, re.MULTILINE)
    )


def _run_upgrade_phase2(
    kanban_root: pathlib.Path, *, timeout: int = 120
) -> subprocess.CompletedProcess:
    """Invoke upgrade.sh's phase-2 body directly (the actual upgrade work:
    deposit, VERSION stamp, migrations, retired-file sweep), bypassing the
    phase-1 wrapper's backup creation and bash -n / --phase2-support probes
    -- those are phase-1-only concerns with no bearing on migration wiring.
    This is exactly the invocation phase 1 execs into on a real operator
    run (team/scripts/upgrade.sh's own "Handing off to dev-tree upgrade.sh
    (phase 2...)" step), so it exercises the real, documented upgrade path's
    migration-relevant body, not a re-implementation of it.
    """
    env = _clean_env(kanban_root)
    cmd = [
        "bash",
        str(_UPGRADE_SCRIPT),
        "--phase2",
        "--phase2-protocol",
        "1",
        "--kanban-root",
        str(kanban_root),
        "--no-backup",
    ]
    return subprocess.run(
        cmd, capture_output=True, text=True, env=env, cwd=str(kanban_root), timeout=timeout
    )


# ---------------------------------------------------------------------------
# run-migrations.sh: destination-keyed selection and forwarding
# ---------------------------------------------------------------------------


class TestRunnerSeedsMissingKey:
    def test_first_application_seeds_the_commented_key(
        self, tmp_path: pathlib.Path
    ) -> None:
        root = _build_kanban_root_with_project(tmp_path, "runner-proj", _CFG_MISSING_KEY)
        cfg_path = _cfg_path(root, "runner-proj")
        assert _count_key_occurrences(cfg_path.read_text(encoding="utf-8")) == 0

        result = _run_runner(root, from_version="v2.22.5", to_version="v2.23.0")
        assert result.returncode == 0, (
            f"runner failed.\nstdout: {result.stdout}\nstderr: {result.stderr}"
        )

        after_text = cfg_path.read_text(encoding="utf-8")
        assert _count_key_occurrences(after_text) == 1, after_text
        assert "\noverwatch_resolve = " not in after_text, (
            f"The seeded key must be commented, never live.\n{after_text}"
        )

    def test_second_application_is_byte_identical(self, tmp_path: pathlib.Path) -> None:
        root = _build_kanban_root_with_project(tmp_path, "runner-proj-2", _CFG_MISSING_KEY)
        cfg_path = _cfg_path(root, "runner-proj-2")

        first = _run_runner(root, from_version="v2.22.5", to_version="v2.23.0")
        assert first.returncode == 0, (
            f"first run failed.\nstdout: {first.stdout}\nstderr: {first.stderr}"
        )
        after_first = cfg_path.read_text(encoding="utf-8")

        second = _run_runner(root, from_version="v2.22.5", to_version="v2.23.0")
        assert second.returncode == 0, (
            f"second run failed.\nstdout: {second.stdout}\nstderr: {second.stderr}"
        )
        after_second = cfg_path.read_text(encoding="utf-8")

        assert after_first == after_second, (
            "A second application must be a byte-identical no-op.\n"
            f"after first:\n{after_first}\nafter second:\n{after_second}"
        )


class TestRunnerPreservesOperatorValue:
    def test_operator_set_true_survives_the_runner(self, tmp_path: pathlib.Path) -> None:
        root = _build_kanban_root_with_project(
            tmp_path, "runner-operator-proj", _CFG_WITH_OPERATOR_SET_TRUE
        )
        cfg_path = _cfg_path(root, "runner-operator-proj")
        original = cfg_path.read_text(encoding="utf-8")

        result = _run_runner(root, from_version="v2.22.5", to_version="v2.23.0")
        assert result.returncode == 0, (
            f"runner failed.\nstdout: {result.stdout}\nstderr: {result.stderr}"
        )

        after = cfg_path.read_text(encoding="utf-8")
        assert after == original, (
            "An operator-set value must be left byte-for-byte unchanged by the "
            f"runner.\nbefore:\n{original}\nafter:\n{after}"
        )
        assert "overwatch_resolve = true" in after


class TestRunnerSelectionRule:
    def test_destination_outside_the_range_selects_nothing(
        self, tmp_path: pathlib.Path
    ) -> None:
        """The sparse case: a (from, to] range that does not cover v2.23.0's
        destination must apply nothing and still exit 0 (README.md "Sparse
        rule" -- absence of a needed migration is the normal case)."""
        root = _build_kanban_root_with_project(tmp_path, "runner-sparse-proj", _CFG_MISSING_KEY)
        cfg_path = _cfg_path(root, "runner-sparse-proj")
        original = cfg_path.read_text(encoding="utf-8")

        result = _run_runner(root, from_version="v2.20.0", to_version="v2.20.5")
        assert result.returncode == 0, (
            f"runner failed.\nstdout: {result.stdout}\nstderr: {result.stderr}"
        )
        assert "nothing to apply" in result.stdout

        after = cfg_path.read_text(encoding="utf-8")
        assert after == original, "An out-of-range invocation must not touch project.cfg."

    def test_from_equal_to_to_selects_nothing(self, tmp_path: pathlib.Path) -> None:
        """The (from, to] interval is half-open: from == to is an empty range."""
        root = _build_kanban_root_with_project(tmp_path, "runner-equal-proj", _CFG_MISSING_KEY)
        result = _run_runner(root, from_version="v2.23.0", to_version="v2.23.0")
        assert result.returncode == 0
        assert "nothing to apply" in result.stdout
        assert _count_key_occurrences(
            _cfg_path(root, "runner-equal-proj").read_text(encoding="utf-8")
        ) == 0


class TestRunnerFanOutRespectsExplicitProjectOnly:
    def test_single_project_flag_limits_the_run(self, tmp_path: pathlib.Path) -> None:
        root = _build_kanban_root_with_project(
            tmp_path,
            "runner-fanout-a",
            _CFG_MISSING_KEY,
            extra_projects={"runner-fanout-b": _CFG_MISSING_KEY},
        )
        result = _run_runner(
            root, from_version="v2.22.5", to_version="v2.23.0", project="runner-fanout-a"
        )
        assert result.returncode == 0, (
            f"runner failed.\nstdout: {result.stdout}\nstderr: {result.stderr}"
        )
        assert _count_key_occurrences(
            _cfg_path(root, "runner-fanout-a").read_text(encoding="utf-8")
        ) == 1
        assert _count_key_occurrences(
            _cfg_path(root, "runner-fanout-b").read_text(encoding="utf-8")
        ) == 0, "Explicit --project must not touch other registered projects."

    def test_no_project_flag_seeds_every_registered_project(
        self, tmp_path: pathlib.Path
    ) -> None:
        root = _build_kanban_root_with_project(
            tmp_path,
            "runner-fanout-c",
            _CFG_MISSING_KEY,
            extra_projects={"runner-fanout-d": _CFG_MISSING_KEY},
        )
        result = _run_runner(root, from_version="v2.22.5", to_version="v2.23.0")
        assert result.returncode == 0, (
            f"runner failed.\nstdout: {result.stdout}\nstderr: {result.stderr}"
        )
        for name in ("runner-fanout-c", "runner-fanout-d"):
            assert _count_key_occurrences(
                _cfg_path(root, name).read_text(encoding="utf-8")
            ) == 1, f"expected {name} to be seeded by the no-flag fan-out run"


class TestRunnerUsageErrors:
    def test_missing_from_is_a_usage_error(self, tmp_path: pathlib.Path) -> None:
        root = _build_kanban_root_with_project(tmp_path, "runner-usage-proj", _CFG_MISSING_KEY)
        env = _clean_env(root)
        result = subprocess.run(
            ["bash", str(_RUNNER_SCRIPT), "--to", "v2.23.0"],
            capture_output=True,
            text=True,
            env=env,
            cwd=str(root),
            timeout=30,
        )
        assert result.returncode == 1
        assert "--from" in result.stderr

    def test_malformed_version_is_rejected(self, tmp_path: pathlib.Path) -> None:
        root = _build_kanban_root_with_project(tmp_path, "runner-malformed-proj", _CFG_MISSING_KEY)
        result = _run_runner(root, from_version="not-a-version", to_version="v2.23.0")
        assert result.returncode == 1
        assert "semver" in result.stderr


# ---------------------------------------------------------------------------
# team/scripts/upgrade.sh phase-2 body: the real, documented upgrade path
# ---------------------------------------------------------------------------


class TestUpgradePhase2AppliesMigrationsAutomatically:
    def test_upgrade_seeds_the_key_with_no_operator_action(
        self, tmp_path: pathlib.Path
    ) -> None:
        """An existing install upgraded from v2.22.5 to whatever this dev
        tree's own committed VERSION is ends up with the seeded key, purely
        by running the documented upgrade path -- no separate operator
        command, no manual migration invocation."""
        root = _build_kanban_root_with_project(tmp_path, "upgrade-proj", _CFG_MISSING_KEY)
        (root / "VERSION").write_text("v2.22.5\n", encoding="utf-8")

        result = _run_upgrade_phase2(root)
        assert result.returncode == 0, (
            f"upgrade phase 2 failed.\nstdout: {result.stdout}\nstderr: {result.stderr}"
        )
        assert (root / "scripts" / "migrations" / "run-migrations.sh").is_file(), (
            "upgrade.sh's own deposit loop must populate scripts/migrations/ "
            "before the migration step runs."
        )

        after_text = _cfg_path(root, "upgrade-proj").read_text(encoding="utf-8")
        assert _count_key_occurrences(after_text) == 1, (
            f"Expected the upgrade path to seed overwatch_resolve.\n"
            f"stdout: {result.stdout}\nstderr: {result.stderr}\ncfg:\n{after_text}"
        )
        assert "\noverwatch_resolve = " not in after_text

    def test_running_the_upgrade_path_twice_is_byte_identical(
        self, tmp_path: pathlib.Path
    ) -> None:
        root = _build_kanban_root_with_project(tmp_path, "upgrade-proj-2", _CFG_MISSING_KEY)
        (root / "VERSION").write_text("v2.22.5\n", encoding="utf-8")

        first = _run_upgrade_phase2(root)
        assert first.returncode == 0, (
            f"first upgrade failed.\nstdout: {first.stdout}\nstderr: {first.stderr}"
        )
        cfg_path = _cfg_path(root, "upgrade-proj-2")
        after_first = cfg_path.read_text(encoding="utf-8")

        # Reset VERSION back to the pre-upgrade value so the second run
        # re-selects and re-applies the same migration range, directly
        # exercising the migration script's own idempotency detection
        # through the full upgrade path a second time (not merely a no-op
        # empty-range skip because VERSION was already advanced).
        (root / "VERSION").write_text("v2.22.5\n", encoding="utf-8")

        second = _run_upgrade_phase2(root)
        assert second.returncode == 0, (
            f"second upgrade failed.\nstdout: {second.stdout}\nstderr: {second.stderr}"
        )
        after_second = cfg_path.read_text(encoding="utf-8")

        assert after_first == after_second, (
            "Running the documented upgrade path a second time must leave "
            f"project.cfg byte-identical.\nafter first:\n{after_first}\n"
            f"after second:\n{after_second}"
        )

    def test_operator_set_value_survives_the_upgrade_path(
        self, tmp_path: pathlib.Path
    ) -> None:
        root = _build_kanban_root_with_project(
            tmp_path, "upgrade-operator-proj", _CFG_WITH_OPERATOR_SET_TRUE
        )
        (root / "VERSION").write_text("v2.22.5\n", encoding="utf-8")
        cfg_path = _cfg_path(root, "upgrade-operator-proj")
        original = cfg_path.read_text(encoding="utf-8")

        result = _run_upgrade_phase2(root)
        assert result.returncode == 0, (
            f"upgrade phase 2 failed.\nstdout: {result.stdout}\nstderr: {result.stderr}"
        )

        after = cfg_path.read_text(encoding="utf-8")
        assert after == original, (
            "An operator-set overwatch_resolve=true must survive the upgrade "
            f"path byte-for-byte.\nbefore:\n{original}\nafter:\n{after}"
        )

    def test_seeded_key_resolves_false_through_the_shared_accessor(
        self, tmp_path: pathlib.Path
    ) -> None:
        root = _build_kanban_root_with_project(tmp_path, "upgrade-resolve-proj", _CFG_MISSING_KEY)
        (root / "VERSION").write_text("v2.22.5\n", encoding="utf-8")

        result = _run_upgrade_phase2(root)
        assert result.returncode == 0, (
            f"upgrade phase 2 failed.\nstdout: {result.stdout}\nstderr: {result.stderr}"
        )

        env = dict(os.environ)
        env["KANBAN_ROOT"] = str(root)
        env["PGAI_PROJECT_NAME"] = "upgrade-resolve-proj"
        env.pop("PGAI_AGENT_KANBAN_ROOT_PATH", None)

        resolve_result = subprocess.run(
            ["bash", "-c", "source scripts/lib/project_paths.sh && pp_overwatch_resolve"],
            capture_output=True,
            text=True,
            env=env,
            cwd=str(root),
            timeout=30,
        )
        assert resolve_result.returncode == 0, (
            f"pp_overwatch_resolve failed.\nstdout: {resolve_result.stdout}\n"
            f"stderr: {resolve_result.stderr}"
        )
        assert resolve_result.stdout.strip() == "false", (
            f"Expected pp_overwatch_resolve to resolve to 'false' after the "
            f"upgrade path seeded the key.\nstdout: {resolve_result.stdout}"
        )
