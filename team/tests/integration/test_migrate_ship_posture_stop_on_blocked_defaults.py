"""
test_migrate_ship_posture_stop_on_blocked_defaults.py
=====================================================
Integration tests for team/scripts/migrations/migrate_v2.50.9_to_v2.51.0.sh,
the upgrade migration that moves an install's kanban.cfg off the previously
shipped [release] ship_posture = matrix and [wake] stop_on_blocked = true
onto the new defaults, and leaves every project.cfg alone.

The behaviour of the shared logic is pinned in
tests/unit/test_cfg_default_upgrade.py; these tests run the real script
under bash strict mode and assert what the adapter adds: argument handling,
fan-out over registered projects, exit codes, the on-disk effect and the
operator-facing lines, plus selection by run-migrations.sh.

Every test stands up its own kanban root under tmp_path and runs the script
with an environment built from an explicit set: the kanban root, KANBAN_ROOT
and the temp dir point into the sandbox, and PGAI_TASKS_DIR,
PGAI_PROJECT_ROOT and PGAI_PROJECT_NAME are absent.
"""

from __future__ import annotations

import hashlib
import pathlib
import subprocess

from pgai_agent_kanban.lib.error_contract import parse_error_contract
from pgai_agent_kanban.overwatch.resolution_matrix import resolve_ship_posture
from tests.fixtures.scrubbed_subprocess_env import build_scrubbed_env

_TEAM_DIR = pathlib.Path(__file__).resolve().parents[2]  # team/
_MIGRATIONS_DIR = _TEAM_DIR / "scripts" / "migrations"
_MIGRATION_SCRIPT = _MIGRATIONS_DIR / "migrate_v2.50.9_to_v2.51.0.sh"
_RUNNER_SCRIPT = _MIGRATIONS_DIR / "run-migrations.sh"

_OLD_KANBAN_CFG = """\
[paths]
temp_dir = /srv/kanban-tmp

[wake]
# What a task that finishes in BLOCKED state stops.
max_tasks_per_wake = 5
stop_on_blocked = true
pause_between_tasks = 5

[release]
# --- Ship posture ---
ship_posture = matrix
deferred_bugs = false
"""

_PROJECT_CFGS = {
    "project-a": "[project]\nproject_name = project-a\npush_to_remote = false\n\n[release]\nship_posture = matrix\n",
    "project-b": "[project]\nproject_name = project-b\npush_to_remote = false\n\n[release]\nship_posture = always\n",
    "project-c": "[project]\nproject_name = project-c\npush_to_remote = false\n",
}


def _build_root(parent: pathlib.Path, kanban_cfg: str = _OLD_KANBAN_CFG) -> pathlib.Path:
    """A kanban root with kanban.cfg and three registered projects."""
    root = parent / "kanban_root"
    (root / "tmp").mkdir(parents=True)
    (root / "kanban.cfg").write_text(kanban_cfg, encoding="utf-8")
    registry = [f"[project:{name}]\npriority={i}\n" for i, name in enumerate(_PROJECT_CFGS, start=1)]
    (root / "projects.cfg").write_text("\n".join(registry), encoding="utf-8")
    for name, text in _PROJECT_CFGS.items():
        proj = root / "projects" / name
        proj.mkdir(parents=True)
        (proj / "project.cfg").write_text(text, encoding="utf-8")
    return root


def _env(root: pathlib.Path, home: pathlib.Path) -> dict[str, str]:
    return build_scrubbed_env(
        home_dir=home,
        extra_env={
            "PGAI_AGENT_KANBAN_ROOT_PATH": str(root),
            "KANBAN_ROOT": str(root),
            "PGAI_AGENT_KANBAN_TEMP_DIR": str(root / "tmp"),
        },
    )


def _run(root: pathlib.Path, tmp_path: pathlib.Path, *args: str) -> subprocess.CompletedProcess:
    home = tmp_path / "home"
    home.mkdir(exist_ok=True)
    return subprocess.run(
        ["bash", str(_MIGRATION_SCRIPT), *args],
        capture_output=True,
        text=True,
        env=_env(root, home),
        cwd=str(tmp_path),
        timeout=60,
    )


def _project_hashes(root: pathlib.Path) -> dict[str, str]:
    return {
        name: hashlib.sha256((root / "projects" / name / "project.cfg").read_bytes()).hexdigest()
        for name in _PROJECT_CFGS
    }


def _explain(result: subprocess.CompletedProcess) -> str:
    return f"exit {result.returncode}\nstdout:\n{result.stdout}\nstderr:\n{result.stderr}"


class TestKanbanCfgMovesOntoTheNewDefaults:
    def test_both_lines_change_and_nothing_else_does(self, tmp_path: pathlib.Path) -> None:
        root = _build_root(tmp_path)

        result = _run(root, tmp_path)

        assert result.returncode == 0, _explain(result)
        after = (root / "kanban.cfg").read_text(encoding="utf-8")
        expected = _OLD_KANBAN_CFG.replace("stop_on_blocked = true", "stop_on_blocked = false").replace(
            "ship_posture = matrix", "ship_posture = always"
        )
        assert after == expected, _explain(result)

    def test_the_output_states_each_change_with_its_revert_line(self, tmp_path: pathlib.Path) -> None:
        root = _build_root(tmp_path)
        cfg = root / "kanban.cfg"

        result = _run(root, tmp_path)

        assert result.returncode == 0, _explain(result)
        assert "[changed]  [release] ship_posture: matrix -> always" in result.stdout, _explain(result)
        assert "[changed]  [wake] stop_on_blocked: true -> false" in result.stdout, _explain(result)
        assert f"under [release] in {cfg}: ship_posture = matrix" in result.stdout, _explain(result)
        assert f"under [wake] in {cfg}: stop_on_blocked = true" in result.stdout, _explain(result)

    def test_a_project_without_its_own_value_now_resolves_always(self, tmp_path: pathlib.Path) -> None:
        root = _build_root(tmp_path)
        assert resolve_ship_posture("project-c", str(root)) == "matrix"

        result = _run(root, tmp_path)

        assert result.returncode == 0, _explain(result)
        assert resolve_ship_posture("project-c", str(root)) == "always"
        assert resolve_ship_posture("project-a", str(root)) == "matrix"


class TestProjectCfgIsNeverWritten:
    def test_project_values_survive_and_are_named(self, tmp_path: pathlib.Path) -> None:
        root = _build_root(tmp_path)
        before = _project_hashes(root)

        result = _run(root, tmp_path)

        assert result.returncode == 0, _explain(result)
        assert _project_hashes(root) == before
        assert "[keeps]  project-a: project.cfg [release] ship_posture = matrix" in result.stdout, _explain(result)
        assert "[keeps]  project-b: project.cfg [release] ship_posture = always" in result.stdout, _explain(result)
        assert "[inherits]  project-c:" in result.stdout, _explain(result)

    def test_project_flag_narrows_only_the_report(self, tmp_path: pathlib.Path) -> None:
        root = _build_root(tmp_path)

        result = _run(root, tmp_path, "--project", "project-b")

        assert result.returncode == 0, _explain(result)
        assert "project-b:" in result.stdout
        assert "project-a:" not in result.stdout and "project-c:" not in result.stdout, _explain(result)
        assert "ship_posture = always" in (root / "kanban.cfg").read_text(encoding="utf-8")


class TestRepeatAndPreview:
    def test_a_second_run_changes_nothing_and_says_already_applied(self, tmp_path: pathlib.Path) -> None:
        root = _build_root(tmp_path)
        first = _run(root, tmp_path)
        assert first.returncode == 0, _explain(first)
        after_first = (root / "kanban.cfg").read_bytes()

        second = _run(root, tmp_path)

        assert second.returncode == 0, _explain(second)
        assert (root / "kanban.cfg").read_bytes() == after_first
        assert "already applied" in second.stdout, _explain(second)
        assert "[changed]" not in second.stdout, _explain(second)

    def test_dry_run_writes_nothing_and_prints_what_would_change(self, tmp_path: pathlib.Path) -> None:
        root = _build_root(tmp_path)

        result = _run(root, tmp_path, "--dry-run")

        assert result.returncode == 0, _explain(result)
        assert (root / "kanban.cfg").read_text(encoding="utf-8") == _OLD_KANBAN_CFG
        assert "[would-change]  [release] ship_posture: matrix -> always" in result.stdout, _explain(result)
        assert "[would-change]  [wake] stop_on_blocked: true -> false" in result.stdout, _explain(result)
        assert "nothing written" in result.stdout, _explain(result)

    def test_a_cfg_without_either_key_is_left_alone(self, tmp_path: pathlib.Path) -> None:
        text = "[paths]\n\n[release]\n"
        root = _build_root(tmp_path, kanban_cfg=text)

        result = _run(root, tmp_path)

        assert result.returncode == 0, _explain(result)
        assert (root / "kanban.cfg").read_text(encoding="utf-8") == text
        assert "already applied" in result.stdout, _explain(result)


class TestRunnerSelectsTheMigration:
    def test_runner_dry_run_over_the_range_selects_this_script(self, tmp_path: pathlib.Path) -> None:
        root = _build_root(tmp_path)
        home = tmp_path / "home"
        home.mkdir()

        result = subprocess.run(
            ["bash", str(_RUNNER_SCRIPT), "--from", "v2.50.9", "--to", "v2.51.0", "--dry-run"],
            capture_output=True,
            text=True,
            env=_env(root, home),
            cwd=str(tmp_path),
            timeout=60,
        )

        assert result.returncode == 0, _explain(result)
        assert f"[run] {_MIGRATION_SCRIPT.name}" in result.stdout, _explain(result)
        assert "[would-change]  [release] ship_posture" in result.stdout, _explain(result)
        assert (root / "kanban.cfg").read_text(encoding="utf-8") == _OLD_KANBAN_CFG


class TestArguments:
    def test_help_exits_zero(self, tmp_path: pathlib.Path) -> None:
        root = _build_root(tmp_path)
        result = _run(root, tmp_path, "--help")
        assert result.returncode == 0, _explain(result)
        assert "--dry-run" in result.stderr, _explain(result)

    def test_an_unknown_flag_is_a_usage_error(self, tmp_path: pathlib.Path) -> None:
        root = _build_root(tmp_path)
        result = _run(root, tmp_path, "--bogus")
        assert result.returncode == 1, _explain(result)
        assert (root / "kanban.cfg").read_text(encoding="utf-8") == _OLD_KANBAN_CFG

    def test_a_missing_kanban_root_is_refused_dual_channel(self, tmp_path: pathlib.Path) -> None:
        missing = tmp_path / "no_such_root"
        home = tmp_path / "home"
        home.mkdir()

        result = subprocess.run(
            ["bash", str(_MIGRATION_SCRIPT)],
            capture_output=True,
            text=True,
            env=_env(missing, home),
            cwd=str(tmp_path),
            timeout=60,
        )

        assert result.returncode == 3, _explain(result)
        contract = parse_error_contract(result.stderr)
        assert contract is not None, _explain(result)
        assert contract["error"] == "migration.kanban_root_missing"
        assert str(_MIGRATION_SCRIPT) in contract["remedy"]
