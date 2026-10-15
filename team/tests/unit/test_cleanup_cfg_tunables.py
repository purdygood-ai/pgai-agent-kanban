"""
test_cleanup_cfg_tunables.py
============================
Behavioral unit tests for how team/scripts/cleanup/cleanup.sh resolves its
three kanban.cfg [cleanup] tunables through config_loader.sh's
config_cleanup_tunables:

  - [cleanup] retention_days, trivial_log_bytes and trivial_log_hours in
    the sandbox kanban.cfg are the values a --dry-run reports and applies,
    with no PGAI_CLEANUP_* variable set.
  - A PGAI_CLEANUP_* variable set in the environment wins over the cfg.
  - With none of them anywhere, the documented defaults 30 / 1700 / 6 apply.
  - The deprecated [paths] cleanup_retention_days is used when
    [cleanup] retention_days is absent, and ignored when it is present;
    either way the run narrates exactly one deprecation warning, including
    a project-less run that fans out to one child per registered project.

Every cleanup.sh invocation runs --dry-run against a throwaway kanban root
under pytest's tmp_path with all five live-install names set or cleared
(PGAI_AGENT_KANBAN_ROOT_PATH, KANBAN_ROOT, PGAI_AGENT_KANBAN_TEMP_DIR set
to the sandbox; PGAI_TASKS_DIR, PGAI_PROJECT_ROOT absent), on an
environment built from an explicit set (build_scrubbed_env) so no
PGAI_CLEANUP_* variable the invoking shell exported can reach the run.
"""

from __future__ import annotations

import os
import pathlib
import re
import time

import pytest

from tests.fixtures.scrubbed_subprocess_env import build_scrubbed_env
from tests.unit.shell_harness import run_bash

_CLEANUP_SCRIPT = "scripts/cleanup/cleanup.sh"
_CONFIG_LOADER = "scripts/lib/config_loader.sh"
_PROJECT = "demo"
_DEPRECATION = "[paths] cleanup_retention_days"


def _sandbox(tmp_path: pathlib.Path, cfg_text: str | None, projects=(_PROJECT,)) -> pathlib.Path:
    """A kanban root with a release-state.md per project, a projects.cfg
    registering them, and (when given) a kanban.cfg."""
    root = tmp_path / "kanban_root"
    for priority, name in enumerate(projects, start=1):
        project_dir = root / "projects" / name
        project_dir.mkdir(parents=True)
        (project_dir / "release-state.md").write_text(
            "# Release State\n\n## Active RC\nnone\n\n## Last Released\nv1.0.0\n",
            encoding="utf-8",
        )
    (root / "projects.cfg").write_text(
        "\n".join(f"[project:{n}]\npriority = {i}\n" for i, n in enumerate(projects, start=1)),
        encoding="utf-8",
    )
    if cfg_text is not None:
        (root / "kanban.cfg").write_text(cfg_text, encoding="utf-8")
    return root


def _env(tmp_path: pathlib.Path, root: pathlib.Path, **overrides: str) -> dict[str, str]:
    return build_scrubbed_env(
        home_dir=tmp_path / "home",
        extra_env={
            "PGAI_AGENT_KANBAN_ROOT_PATH": str(root),
            "KANBAN_ROOT": str(root),
            "PGAI_AGENT_KANBAN_TEMP_DIR": str(root / "temp"),
            **overrides,
        },
    )


def _dry_run(tmp_path: pathlib.Path, root: pathlib.Path, *args: str, **env: str):
    cmd = " ".join(["bash", _CLEANUP_SCRIPT, *args, "--dry-run"])
    return run_bash(tmp_path, cmd, base_env=_env(tmp_path, root, **env))


def _summary_value(stdout: str, label: str) -> str:
    """The value of one '<label>: <value>' line in the run summary."""
    match = re.search(rf"cleanup: {label}:\s+(\S+)", stdout)
    assert match is not None, f"summary has no {label} line:\n{stdout}"
    return match.group(1)


def _diag(result) -> str:
    return f"--- stdout ---\n{result.stdout}\n--- stderr ---\n{result.stderr}"


# ---------------------------------------------------------------------------
# [cleanup] keys in kanban.cfg are honoured; the environment wins over them
# ---------------------------------------------------------------------------

_KEYS = [
    pytest.param("trivial_log_bytes", "PGAI_CLEANUP_TRIVIAL_LOG_BYTES", "8192", "4096", id="trivial_log_bytes"),
    pytest.param("retention_days", "PGAI_CLEANUP_RETENTION_DAYS", "45", "60", id="retention_days"),
    pytest.param("trivial_log_hours", "PGAI_CLEANUP_TRIVIAL_LOG_HOURS", "12", "3", id="trivial_log_hours"),
]


@pytest.mark.parametrize("key, env_name, cfg_value, env_value", _KEYS)
def test_cfg_value_is_reported_with_no_environment_variable_set(
    tmp_path: pathlib.Path, key: str, env_name: str, cfg_value: str, env_value: str
) -> None:
    root = _sandbox(tmp_path, f"[cleanup]\n{key} = {cfg_value}\n")
    result = _dry_run(tmp_path, root, "--project", _PROJECT)
    assert result.returncode == 0, _diag(result)
    assert _summary_value(result.stdout, key) == cfg_value, _diag(result)


@pytest.mark.parametrize("key, env_name, cfg_value, env_value", _KEYS)
def test_environment_value_wins_over_cfg_value(
    tmp_path: pathlib.Path, key: str, env_name: str, cfg_value: str, env_value: str
) -> None:
    root = _sandbox(tmp_path, f"[cleanup]\n{key} = {cfg_value}\n")
    result = _dry_run(tmp_path, root, "--project", _PROJECT, **{env_name: env_value})
    assert result.returncode == 0, _diag(result)
    assert _summary_value(result.stdout, key) == env_value, _diag(result)


@pytest.mark.parametrize("cfg_text", [None, "[cleanup]\n", "[cleanup]\nretention_days =\ntrivial_log_bytes =\n"])
def test_absent_blank_or_missing_cfg_takes_documented_defaults(tmp_path: pathlib.Path, cfg_text: str | None) -> None:
    root = _sandbox(tmp_path, cfg_text)
    result = _dry_run(tmp_path, root, "--project", _PROJECT)
    assert result.returncode == 0, _diag(result)
    resolved = {k: _summary_value(result.stdout, k) for k in ("retention_days", "trivial_log_bytes", "trivial_log_hours")}
    assert resolved == {"retention_days": "30", "trivial_log_bytes": "1700", "trivial_log_hours": "6"}, _diag(result)
    assert _DEPRECATION not in result.stdout + result.stderr, _diag(result)


def test_cfg_trivial_log_bytes_is_the_threshold_the_purge_applies(tmp_path: pathlib.Path) -> None:
    """A 4000-byte, 7-hour-old batch log is above the 1700 default but below
    a configured 8192, so only the configured threshold makes it trivial."""
    root = _sandbox(tmp_path, "[cleanup]\ntrivial_log_bytes = 8192\n")
    agents = root / "logs" / "agents"
    agents.mkdir(parents=True)
    log = agents / "coder-batch-20261009-000000.log"
    log.write_text("x" * 4000, encoding="utf-8")
    old = time.time() - 7 * 3600
    os.utime(log, (old, old))

    result = _dry_run(tmp_path, root, "--project", _PROJECT)
    assert result.returncode == 0, _diag(result)
    assert "size<8192B" in result.stdout, _diag(result)
    assert f"[DRY-RUN] trivial-purge would delete: {log}" in result.stdout, _diag(result)
    assert _summary_value(result.stdout, "trivial_logs_deleted") == "1", _diag(result)
    assert log.exists(), "--dry-run must not delete"


# ---------------------------------------------------------------------------
# Deprecated [paths] cleanup_retention_days
# ---------------------------------------------------------------------------


def _deprecation_lines(result) -> list[str]:
    return [l for l in (result.stdout + result.stderr).splitlines() if _DEPRECATION in l and "deprecated" in l]


def test_paths_retention_is_used_when_cleanup_retention_is_absent_with_one_warning(tmp_path: pathlib.Path) -> None:
    root = _sandbox(tmp_path, "[paths]\ncleanup_retention_days = 45\n\n[cleanup]\ntrivial_log_bytes = 1700\n")
    result = _dry_run(tmp_path, root, "--project", _PROJECT)
    assert result.returncode == 0, _diag(result)
    assert _summary_value(result.stdout, "retention_days") == "45", _diag(result)
    warnings = _deprecation_lines(result)
    assert len(warnings) == 1, _diag(result)
    assert "[cleanup] retention_days = 45" in warnings[0], "the warning names the edit that retires the key"


def test_cleanup_retention_wins_over_paths_retention_with_one_warning(tmp_path: pathlib.Path) -> None:
    root = _sandbox(tmp_path, "[paths]\ncleanup_retention_days = 45\n\n[cleanup]\nretention_days = 20\n")
    result = _dry_run(tmp_path, root, "--project", _PROJECT)
    assert result.returncode == 0, _diag(result)
    assert _summary_value(result.stdout, "retention_days") == "20", _diag(result)
    warnings = _deprecation_lines(result)
    assert len(warnings) == 1, _diag(result)
    assert "ignored" in warnings[0], warnings[0]


def test_environment_retention_wins_and_suppresses_the_deprecation_warning(tmp_path: pathlib.Path) -> None:
    root = _sandbox(tmp_path, "[paths]\ncleanup_retention_days = 45\n")
    result = _dry_run(tmp_path, root, "--project", _PROJECT, PGAI_CLEANUP_RETENTION_DAYS="60")
    assert result.returncode == 0, _diag(result)
    assert _summary_value(result.stdout, "retention_days") == "60", _diag(result)
    assert _deprecation_lines(result) == [], _diag(result)


def test_project_less_run_narrates_the_deprecation_once_and_every_project_uses_it(tmp_path: pathlib.Path) -> None:
    root = _sandbox(tmp_path, "[paths]\ncleanup_retention_days = 45\n", projects=("alpha", "beta"))
    result = _dry_run(tmp_path, root)
    assert result.returncode == 0, _diag(result)
    assert len(_deprecation_lines(result)) == 1, _diag(result)
    reported = re.findall(r"cleanup: retention_days:\s+(\S+)", result.stdout)
    assert reported == ["45", "45"], _diag(result)


# ---------------------------------------------------------------------------
# The resolver on its own
# ---------------------------------------------------------------------------


def test_resolver_defaults_to_the_cfg_load_config_recorded(tmp_path: pathlib.Path) -> None:
    root = _sandbox(tmp_path, "[cleanup]\ntrivial_log_hours = 9\n")
    script = (
        f"source {_CONFIG_LOADER} && load_config {str(root / 'kanban.cfg')!r} >/dev/null 2>&1; "
        "unset PGAI_CLEANUP_TRIVIAL_LOG_HOURS; config_cleanup_tunables && "
        'echo "hours=${PGAI_CLEANUP_TRIVIAL_LOG_HOURS}"'
    )
    result = run_bash(tmp_path, script, base_env=_env(tmp_path, root))
    assert result.returncode == 0, _diag(result)
    assert "hours=9" in result.stdout, _diag(result)
