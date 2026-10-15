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


@pytest.mark.parametrize(
    "cfg_content, expected",
    [
        pytest.param(
            "FOO=bar\nBAZ=qux\n", {"FOO": "bar", "BAZ": "qux"},
            id="reads_key_value_pairs",
        ),
        pytest.param(
            'KEY="/some/path with spaces"\n', {"KEY": "/some/path with spaces"},
            id="strips_double_quoted_values",
        ),
        pytest.param(
            "KEY='/opt/value'\n", {"KEY": "/opt/value"},
            id="strips_single_quoted_values",
        ),
        pytest.param(
            "# this is a comment\nFOO=real\n", {"FOO": "real"},
            id="ignores_comment_lines",
        ),
        pytest.param(
            "\n\nFOO=bar\n\n", {"FOO": "bar"},
            id="ignores_blank_lines",
        ),
        pytest.param(
            "export MY_VAR=hello\n", {"MY_VAR": "hello"},
            id="accepts_export_prefix",
        ),
    ],
)
def test_parse_cfg_reads_shell_style_key_value_file(
    tmp_path: pathlib.Path, cfg_content: str, expected: dict
) -> None:
    """_parse_cfg parses a bash-sourcing-style KEY=value file into exactly the dict
    the content specifies — quotes stripped, comments and blank lines skipped, and
    an 'export ' prefix tolerated."""
    cfg_file = tmp_path / "test.cfg"
    cfg_file.write_text(cfg_content, encoding="utf-8")
    result = _parse_cfg(str(cfg_file))
    assert result == expected


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
    """Environment variables override install config.cfg values when the
    caller passes no explicit kanban_root (the ambient-environment path)."""
    (tmp_path / "config.cfg").write_text(
        "PGAI_REQUIREMENTS_DIR=/from-cfg\n", encoding="utf-8"
    )
    monkeypatch.setenv("PGAI_AGENT_KANBAN_ROOT_PATH", str(tmp_path))
    monkeypatch.setenv("PGAI_REQUIREMENTS_DIR", "/from-env")
    monkeypatch.delenv("PGAI_PROJECT_ROOT", raising=False)
    result = get_config(kanban_root=None)
    assert result["PGAI_REQUIREMENTS_DIR"] == "/from-env"


def test_get_config_env_var_wins_over_default(
    tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Environment variables override computed defaults when the caller
    passes no explicit kanban_root (the ambient-environment path)."""
    monkeypatch.setenv("PGAI_AGENT_KANBAN_ROOT_PATH", str(tmp_path))
    monkeypatch.setenv("PGAI_TASKS_DIR", "/env-override/tasks")
    monkeypatch.delenv("PGAI_PROJECT_ROOT", raising=False)
    result = get_config(kanban_root=None)
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
    """When PGAI_PROJECT_ROOT is set and the caller passes no explicit
    kanban_root, runtime state paths are scoped to the project root."""
    project_root = tmp_path / "projects" / "my-project"
    project_root.mkdir(parents=True)
    monkeypatch.setenv("PGAI_AGENT_KANBAN_ROOT_PATH", str(tmp_path))
    monkeypatch.setenv("PGAI_PROJECT_ROOT", str(project_root))
    monkeypatch.delenv("PGAI_REQUIREMENTS_DIR", raising=False)
    monkeypatch.delenv("PGAI_TASKS_DIR", raising=False)
    result = get_config(kanban_root=None)
    # Paths that were kanban-root-scoped should now be project-root-scoped
    assert result["PGAI_REQUIREMENTS_DIR"] == str(project_root / "requirements")
    assert result["PGAI_TASKS_DIR"] == str(project_root / "tasks")


def test_get_config_env_var_still_wins_over_project_root(
    tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Env vars take precedence over PGAI_PROJECT_ROOT scoping when the
    caller passes no explicit kanban_root (the ambient-environment path)."""
    project_root = tmp_path / "projects" / "my-project"
    project_root.mkdir(parents=True)
    monkeypatch.setenv("PGAI_AGENT_KANBAN_ROOT_PATH", str(tmp_path))
    monkeypatch.setenv("PGAI_PROJECT_ROOT", str(project_root))
    monkeypatch.setenv("PGAI_TASKS_DIR", "/hardcoded/from-env")
    result = get_config(kanban_root=None)
    assert result["PGAI_TASKS_DIR"] == "/hardcoded/from-env"


# ---------------------------------------------------------------------------
# get_config() — explicit kanban_root precedence layer
# ---------------------------------------------------------------------------
#
# An explicit kanban_root is the CLI-flag / caller-named case (e.g.
# pm_materialize.py's --team-root). It gets a precedence layer of its own,
# above PGAI_PROJECT_ROOT scoping and the matching per-key environment
# variable, for the None-defaulted PGAI_*_DIR keys — see the module
# docstring. The tests above cover the no-override (kanban_root=None) path;
# these cover the explicit-override path.


# The None-defaulted PGAI_*_DIR keys and the root-relative suffix each
# resolves to. Mirrors the suffix_map inside get_config(); kept here as
# fixture data rather than imported so the test independently pins down
# what the corrected loader is expected to produce.
_ROOT_DERIVED_DIR_SUFFIXES = {
    "PGAI_REQUIREMENTS_DIR": "requirements",
    "PGAI_PRIORITY_DIR": "tasks/queues/priority",
    "PGAI_ARCHIVE_DIR": "tasks/archive",
    "PGAI_BRIEFS_DIR": "briefs",
    "PGAI_TASKS_DIR": "tasks",
    "PGAI_LOGS_DIR": "logs",
}


@pytest.mark.parametrize("dir_key", sorted(_ROOT_DERIVED_DIR_SUFFIXES))
def test_get_config_explicit_root_wins_over_conflicting_env_var(
    tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch, dir_key: str
) -> None:
    """get_config(kanban_root=X) returns every None-defaulted PGAI_*_DIR key
    under X even when the matching environment variable names somewhere
    else entirely — an explicit --team-root must
    outrank the ambient environment every wake session exports."""
    monkeypatch.setenv(dir_key, "/somewhere/the/caller/did/not/name")
    monkeypatch.delenv("PGAI_PROJECT_ROOT", raising=False)
    result = get_config(kanban_root=str(tmp_path))
    assert result[dir_key] == str(tmp_path / _ROOT_DERIVED_DIR_SUFFIXES[dir_key])
    assert result["KANBAN_ROOT"] == str(tmp_path)


def test_get_config_explicit_root_wins_over_project_root_env(
    tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """An explicit kanban_root outranks PGAI_PROJECT_ROOT scoping too: the
    directories still resolve under the explicit root, not the project
    root named by the ambient environment."""
    project_root = tmp_path / "elsewhere" / "some-other-project"
    project_root.mkdir(parents=True)
    monkeypatch.setenv("PGAI_PROJECT_ROOT", str(project_root))
    monkeypatch.delenv("PGAI_TASKS_DIR", raising=False)
    monkeypatch.delenv("PGAI_REQUIREMENTS_DIR", raising=False)
    result = get_config(kanban_root=str(tmp_path))
    assert result["PGAI_TASKS_DIR"] == str(tmp_path / "tasks")
    assert result["PGAI_REQUIREMENTS_DIR"] == str(tmp_path / "requirements")


def test_get_config_explicit_root_wins_over_both_project_root_and_dir_env(
    tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """An explicit kanban_root outranks PGAI_PROJECT_ROOT scoping and a
    directly-conflicting PGAI_TASKS_DIR simultaneously — the exact
    combination that once let ten task folders get
    written into the live install despite an explicit --team-root."""
    project_root = tmp_path / "elsewhere" / "some-other-project"
    project_root.mkdir(parents=True)
    monkeypatch.setenv("PGAI_PROJECT_ROOT", str(project_root))
    monkeypatch.setenv(
        "PGAI_TASKS_DIR", str(tmp_path / "live-root" / "projects" / "pgai-agent-kanban" / "tasks")
    )
    result = get_config(kanban_root=str(tmp_path))
    assert result["PGAI_TASKS_DIR"] == str(tmp_path / "tasks")
    assert result["KANBAN_ROOT"] == str(tmp_path)


def test_get_config_explicit_root_wins_over_user_wide_cfg(
    tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """An explicit kanban_root outranks a user-wide ~/.config/pgai-kanban.cfg
    entry for the None-defaulted PGAI_*_DIR keys too: that file
    is ambient, home-directory-scoped configuration, not data the caller
    placed under the root it named, so it must not silently redirect a
    root-derived key any more than an environment variable may."""
    fake_home = tmp_path / "home"
    (fake_home / ".config").mkdir(parents=True)
    (fake_home / ".config" / "pgai-kanban.cfg").write_text(
        "PGAI_REQUIREMENTS_DIR=/from-user-wide-cfg\n", encoding="utf-8"
    )
    monkeypatch.setattr(pathlib.Path, "home", lambda: fake_home)
    monkeypatch.delenv("PGAI_REQUIREMENTS_DIR", raising=False)
    monkeypatch.delenv("PGAI_PROJECT_ROOT", raising=False)
    root = tmp_path / "root"
    root.mkdir()
    result = get_config(kanban_root=str(root))
    assert result["PGAI_REQUIREMENTS_DIR"] == str(root / "requirements")


def test_get_config_explicit_root_still_honours_install_cfg(
    tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """An explicit kanban_root outranks ambient environment, but not the
    install cfg living inside the named root itself — that file is data
    the caller placed under the root it named, not ambient environment."""
    (tmp_path / "config.cfg").write_text(
        "PGAI_REQUIREMENTS_DIR=/custom/requirements\n", encoding="utf-8"
    )
    monkeypatch.delenv("PGAI_REQUIREMENTS_DIR", raising=False)
    monkeypatch.delenv("PGAI_PROJECT_ROOT", raising=False)
    result = get_config(kanban_root=str(tmp_path))
    assert result["PGAI_REQUIREMENTS_DIR"] == "/custom/requirements"
