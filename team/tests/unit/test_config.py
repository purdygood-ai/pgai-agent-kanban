"""
test_config.py
==============
Behavioral unit tests for team/pm-agent/lib/config.py.

config.py was identified as a near-0% coverage target.  These tests exercise
the four-layer precedence chain:

    1. Built-in defaults (relative-to-root paths when value is None)
    2. User-wide config  (~/.config/pgai-kanban.cfg)
    3. Per-install config ($KANBAN_ROOT/config.cfg)
    4. Environment variables (always win)

All filesystem and environment interactions are isolated: tmp_path is used
for config files and monkeypatch is used to control os.environ.  No bare
/tmp paths and no live filesystem side effects.
"""

from __future__ import annotations

import os
import pathlib

import pytest

try:
    from pm_agent.lib import config as config_mod
    from pm_agent.lib.config import get_config, _parse_cfg
except ImportError:
    import sys
    sys.path.insert(0, str(pathlib.Path(__file__).parent.parent.parent / "pm-agent"))
    from lib import config as config_mod  # type: ignore[no-redef]
    from lib.config import get_config, _parse_cfg  # type: ignore[no-redef]


# ---------------------------------------------------------------------------
# _parse_cfg() — low-level parser
# ---------------------------------------------------------------------------


def test_parse_cfg_reads_key_value_pairs(tmp_path: pathlib.Path) -> None:
    """_parse_cfg parses a simple KEY=value file into a dict."""
    cfg_file = tmp_path / "test.cfg"
    cfg_file.write_text("FOO=bar\nBAZ=qux\n", encoding="utf-8")
    result = _parse_cfg(str(cfg_file))
    assert result == {"FOO": "bar", "BAZ": "qux"}


def test_parse_cfg_strips_double_quoted_values(tmp_path: pathlib.Path) -> None:
    """_parse_cfg strips matching outer double quotes from values."""
    cfg_file = tmp_path / "test.cfg"
    cfg_file.write_text('KEY="/some/path with spaces"\n', encoding="utf-8")
    result = _parse_cfg(str(cfg_file))
    assert result["KEY"] == "/some/path with spaces"


def test_parse_cfg_strips_single_quoted_values(tmp_path: pathlib.Path) -> None:
    """_parse_cfg strips matching outer single quotes from values."""
    cfg_file = tmp_path / "test.cfg"
    cfg_file.write_text("KEY='/opt/value'\n", encoding="utf-8")
    result = _parse_cfg(str(cfg_file))
    assert result["KEY"] == "/opt/value"


def test_parse_cfg_ignores_comment_lines(tmp_path: pathlib.Path) -> None:
    """_parse_cfg skips lines starting with '#'."""
    cfg_file = tmp_path / "test.cfg"
    cfg_file.write_text("# this is a comment\nFOO=real\n", encoding="utf-8")
    result = _parse_cfg(str(cfg_file))
    assert "# this is a comment" not in result
    assert result.get("FOO") == "real"


def test_parse_cfg_ignores_blank_lines(tmp_path: pathlib.Path) -> None:
    """_parse_cfg skips blank/empty lines without raising."""
    cfg_file = tmp_path / "test.cfg"
    cfg_file.write_text("\n\nFOO=bar\n\n", encoding="utf-8")
    result = _parse_cfg(str(cfg_file))
    assert result == {"FOO": "bar"}


def test_parse_cfg_accepts_export_prefix(tmp_path: pathlib.Path) -> None:
    """_parse_cfg handles lines with 'export KEY=value' (bash sourcing style)."""
    cfg_file = tmp_path / "test.cfg"
    cfg_file.write_text("export MY_VAR=hello\n", encoding="utf-8")
    result = _parse_cfg(str(cfg_file))
    assert result.get("MY_VAR") == "hello"


def test_parse_cfg_returns_empty_dict_for_nonexistent_file() -> None:
    """_parse_cfg returns {} when the file does not exist (no exception)."""
    result = _parse_cfg("/nonexistent/path/config.cfg")
    assert result == {}


# ---------------------------------------------------------------------------
# get_config() — defaults layer
# ---------------------------------------------------------------------------


def test_get_config_returns_dict_with_all_known_keys(
    tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """get_config returns a dict that contains all expected PGAI_* keys."""
    monkeypatch.delenv("PGAI_AGENT_KANBAN_ROOT_PATH", raising=False)
    monkeypatch.delenv("PGAI_PROJECT_ROOT", raising=False)
    for key in config_mod._DEFAULTS:
        monkeypatch.delenv(key, raising=False)
    result = get_config(kanban_root=str(tmp_path))
    for key in config_mod._DEFAULTS:
        assert key in result, f"Expected key {key!r} in config result"
    assert "KANBAN_ROOT" in result


def test_get_config_default_requirements_dir_is_relative_to_root(
    tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """get_config derives PGAI_REQUIREMENTS_DIR relative to kanban_root by default."""
    monkeypatch.delenv("PGAI_REQUIREMENTS_DIR", raising=False)
    monkeypatch.delenv("PGAI_PROJECT_ROOT", raising=False)
    result = get_config(kanban_root=str(tmp_path))
    expected = str(tmp_path / "requirements")
    assert result["PGAI_REQUIREMENTS_DIR"] == expected


def test_get_config_default_tasks_dir_is_relative_to_root(
    tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """get_config derives PGAI_TASKS_DIR relative to kanban_root by default."""
    monkeypatch.delenv("PGAI_TASKS_DIR", raising=False)
    monkeypatch.delenv("PGAI_PROJECT_ROOT", raising=False)
    result = get_config(kanban_root=str(tmp_path))
    assert result["PGAI_TASKS_DIR"] == str(tmp_path / "tasks")


def test_get_config_default_cleanup_retention_days_is_string_30(
    tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """PGAI_CLEANUP_RETENTION_DAYS defaults to '30' (the baked-in constant)."""
    monkeypatch.delenv("PGAI_CLEANUP_RETENTION_DAYS", raising=False)
    monkeypatch.delenv("PGAI_PROJECT_ROOT", raising=False)
    result = get_config(kanban_root=str(tmp_path))
    assert result["PGAI_CLEANUP_RETENTION_DAYS"] == "30"


def test_get_config_kanban_root_field_reflects_argument(
    tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """get_config always writes the resolved kanban_root into KANBAN_ROOT."""
    monkeypatch.delenv("PGAI_PROJECT_ROOT", raising=False)
    result = get_config(kanban_root=str(tmp_path))
    assert result["KANBAN_ROOT"] == str(tmp_path)


# ---------------------------------------------------------------------------
# get_config() — install config layer
# ---------------------------------------------------------------------------


def test_get_config_install_cfg_overrides_default(
    tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """get_config reads per-install config.cfg and applies it over defaults."""
    # Write a config.cfg in the kanban root
    cfg_content = "PGAI_REQUIREMENTS_DIR=/custom/requirements\n"
    (tmp_path / "config.cfg").write_text(cfg_content, encoding="utf-8")
    monkeypatch.delenv("PGAI_REQUIREMENTS_DIR", raising=False)
    monkeypatch.delenv("PGAI_PROJECT_ROOT", raising=False)
    result = get_config(kanban_root=str(tmp_path))
    assert result["PGAI_REQUIREMENTS_DIR"] == "/custom/requirements"


def test_get_config_install_cfg_ignores_unknown_keys(
    tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """get_config does not include keys from install cfg that are not in _DEFAULTS."""
    cfg_content = "UNKNOWN_KEY=some_value\nPGAI_TASKS_DIR=/custom/tasks\n"
    (tmp_path / "config.cfg").write_text(cfg_content, encoding="utf-8")
    monkeypatch.delenv("PGAI_TASKS_DIR", raising=False)
    monkeypatch.delenv("PGAI_PROJECT_ROOT", raising=False)
    result = get_config(kanban_root=str(tmp_path))
    assert "UNKNOWN_KEY" not in result
    assert result["PGAI_TASKS_DIR"] == "/custom/tasks"


# ---------------------------------------------------------------------------
# get_config() — environment variable layer (highest precedence)
# ---------------------------------------------------------------------------


def test_get_config_env_var_wins_over_install_cfg(
    tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Environment variables override install config.cfg values."""
    (tmp_path / "config.cfg").write_text(
        "PGAI_REQUIREMENTS_DIR=/from-cfg\n", encoding="utf-8"
    )
    monkeypatch.setenv("PGAI_REQUIREMENTS_DIR", "/from-env")
    monkeypatch.delenv("PGAI_PROJECT_ROOT", raising=False)
    result = get_config(kanban_root=str(tmp_path))
    assert result["PGAI_REQUIREMENTS_DIR"] == "/from-env"


def test_get_config_env_var_wins_over_default(
    tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Environment variables override computed defaults."""
    monkeypatch.setenv("PGAI_TASKS_DIR", "/env-override/tasks")
    monkeypatch.delenv("PGAI_PROJECT_ROOT", raising=False)
    result = get_config(kanban_root=str(tmp_path))
    assert result["PGAI_TASKS_DIR"] == "/env-override/tasks"


def test_get_config_cleanup_days_overridden_by_env(
    tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """PGAI_CLEANUP_RETENTION_DAYS env var overrides the '30' default."""
    monkeypatch.setenv("PGAI_CLEANUP_RETENTION_DAYS", "90")
    monkeypatch.delenv("PGAI_PROJECT_ROOT", raising=False)
    result = get_config(kanban_root=str(tmp_path))
    assert result["PGAI_CLEANUP_RETENTION_DAYS"] == "90"


# ---------------------------------------------------------------------------
# get_config() — kanban_root resolution
# ---------------------------------------------------------------------------


def test_get_config_resolves_root_from_env_when_arg_is_none(
    tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """get_config uses PGAI_AGENT_KANBAN_ROOT_PATH env var when kanban_root is None."""
    monkeypatch.setenv("PGAI_AGENT_KANBAN_ROOT_PATH", str(tmp_path))
    monkeypatch.delenv("PGAI_PROJECT_ROOT", raising=False)
    for key in config_mod._DEFAULTS:
        monkeypatch.delenv(key, raising=False)
    result = get_config(kanban_root=None)
    assert result["KANBAN_ROOT"] == str(tmp_path)


# ---------------------------------------------------------------------------
# get_config() — PGAI_PROJECT_ROOT override layer
# ---------------------------------------------------------------------------


def test_get_config_project_root_overrides_kanban_root_scoped_paths(
    tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """When PGAI_PROJECT_ROOT is set, runtime state paths are scoped to the project root."""
    project_root = tmp_path / "projects" / "my-project"
    project_root.mkdir(parents=True)
    monkeypatch.setenv("PGAI_PROJECT_ROOT", str(project_root))
    monkeypatch.delenv("PGAI_REQUIREMENTS_DIR", raising=False)
    monkeypatch.delenv("PGAI_TASKS_DIR", raising=False)
    result = get_config(kanban_root=str(tmp_path))
    # Paths that were kanban-root-scoped should now be project-root-scoped
    assert result["PGAI_REQUIREMENTS_DIR"] == str(project_root / "requirements")
    assert result["PGAI_TASKS_DIR"] == str(project_root / "tasks")


def test_get_config_env_var_still_wins_over_project_root(
    tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Env vars take precedence even when PGAI_PROJECT_ROOT is set."""
    project_root = tmp_path / "projects" / "my-project"
    project_root.mkdir(parents=True)
    monkeypatch.setenv("PGAI_PROJECT_ROOT", str(project_root))
    monkeypatch.setenv("PGAI_TASKS_DIR", "/hardcoded/from-env")
    result = get_config(kanban_root=str(tmp_path))
    assert result["PGAI_TASKS_DIR"] == "/hardcoded/from-env"
