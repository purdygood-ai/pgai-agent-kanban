"""
test_dev_tree_bash.py
=====================
Behavioral unit tests for team/scripts/lib/dev_tree.sh.

Tests source the shell script and invoke resolve_global_dev_tree and
require_dev_tree via the bash harness.  kanban.cfg fixtures are written to
tmp_path (never to a live install).

Functions under test:
  - resolve_global_dev_tree: resolution order env > kanban.cfg > empty
  - require_dev_tree:        exits 1 with a diagnostic message on failure
"""

from __future__ import annotations

import pathlib

import pytest

from tests.unit.shell_harness import run_bash

_LIB = "scripts/lib/dev_tree.sh"


def _source(func_call: str) -> str:
    """Return a bash snippet that sources dev_tree.sh then calls func_call."""
    return f"source {_LIB} && {func_call}"


# ---------------------------------------------------------------------------
# resolve_global_dev_tree
# ---------------------------------------------------------------------------


def test_resolve_uses_pgai_dev_tree_path_env_var(tmp_path: pathlib.Path) -> None:
    """resolve_global_dev_tree uses PGAI_DEV_TREE_PATH when it is set."""
    result = run_bash(
        tmp_path,
        _source("resolve_global_dev_tree"),
        extra_env={"PGAI_DEV_TREE_PATH": "/custom/dev/tree"},
    )
    assert result.returncode == 0
    assert result.stdout.strip() == "/custom/dev/tree"


def test_resolve_falls_back_to_kanban_cfg(tmp_path: pathlib.Path) -> None:
    """resolve_global_dev_tree reads dev_tree_path from kanban.cfg when env is unset."""
    kanban_root = tmp_path / "kanban"
    kanban_root.mkdir()
    cfg = kanban_root / "kanban.cfg"
    cfg.write_text("[paths]\ndev_tree_path = /from/config\n", encoding="utf-8")

    result = run_bash(
        tmp_path,
        _source("resolve_global_dev_tree"),
        extra_env={
            "PGAI_DEV_TREE_PATH": "",
            "PGAI_AGENT_KANBAN_ROOT_PATH": str(kanban_root),
        },
    )
    assert result.returncode == 0
    assert result.stdout.strip() == "/from/config"


def test_resolve_env_takes_priority_over_kanban_cfg(tmp_path: pathlib.Path) -> None:
    """resolve_global_dev_tree prefers the env var over kanban.cfg."""
    kanban_root = tmp_path / "kanban"
    kanban_root.mkdir()
    cfg = kanban_root / "kanban.cfg"
    cfg.write_text("[paths]\ndev_tree_path = /from/config\n", encoding="utf-8")

    result = run_bash(
        tmp_path,
        _source("resolve_global_dev_tree"),
        extra_env={
            "PGAI_DEV_TREE_PATH": "/from/env",
            "PGAI_AGENT_KANBAN_ROOT_PATH": str(kanban_root),
        },
    )
    assert result.returncode == 0
    assert result.stdout.strip() == "/from/env"


def test_resolve_returns_empty_when_neither_source_configured(tmp_path: pathlib.Path) -> None:
    """resolve_global_dev_tree returns empty string when neither env nor cfg is set."""
    empty_root = tmp_path / "empty_kanban"
    empty_root.mkdir()
    # No kanban.cfg present; no PGAI_DEV_TREE_PATH
    result = run_bash(
        tmp_path,
        _source("resolve_global_dev_tree"),
        extra_env={
            "PGAI_DEV_TREE_PATH": "",
            "PGAI_AGENT_KANBAN_ROOT_PATH": str(empty_root),
        },
    )
    assert result.returncode == 0
    assert result.stdout.strip() == ""


def test_resolve_never_exits_nonzero(tmp_path: pathlib.Path) -> None:
    """resolve_global_dev_tree never fails (exits 0) even with no configuration."""
    result = run_bash(
        tmp_path,
        _source("resolve_global_dev_tree"),
        extra_env={
            "PGAI_DEV_TREE_PATH": "",
            "PGAI_AGENT_KANBAN_ROOT_PATH": str(tmp_path / "nonexistent"),
        },
    )
    assert result.returncode == 0


# ---------------------------------------------------------------------------
# require_dev_tree
# ---------------------------------------------------------------------------


def test_require_dev_tree_succeeds_when_path_is_real_directory(
    tmp_path: pathlib.Path,
) -> None:
    """require_dev_tree returns 0 when the supplied path exists and is a directory."""
    real_dir = tmp_path / "dev_tree"
    real_dir.mkdir()
    result = run_bash(
        tmp_path,
        _source(f"require_dev_tree '{real_dir}' kanban.cfg && echo ok"),
    )
    assert result.returncode == 0
    assert "ok" in result.stdout


def test_require_dev_tree_exits_nonzero_when_path_is_empty_string(
    tmp_path: pathlib.Path,
) -> None:
    """require_dev_tree exits 1 and writes an error when the path is empty."""
    result = run_bash(
        tmp_path,
        # Prevent the exit 1 from terminating the whole bash -c invocation
        # so we can capture the exit code and stderr.
        f"source {_LIB}; require_dev_tree '' kanban.cfg; echo exit:$?",
    )
    # The script echoes the exit code; require_dev_tree exited 1.
    assert "exit:1" in result.stdout or result.returncode == 1
    assert "not configured" in result.stderr or "dev_tree_path" in result.stderr


def test_require_dev_tree_exits_nonzero_when_path_does_not_exist(
    tmp_path: pathlib.Path,
) -> None:
    """require_dev_tree exits 1 and writes an error when the path does not exist."""
    absent = tmp_path / "absent_dev_tree"
    result = run_bash(
        tmp_path,
        f"source {_LIB}; require_dev_tree '{absent}' kanban.cfg; echo exit:$?",
    )
    assert "exit:1" in result.stdout or result.returncode == 1
    assert "does not exist" in result.stderr


def test_require_dev_tree_error_message_mentions_context(tmp_path: pathlib.Path) -> None:
    """require_dev_tree includes the context string in the error message."""
    absent = tmp_path / "no_such_dir"
    result = run_bash(
        tmp_path,
        f"source {_LIB}; require_dev_tree '{absent}' /my/kanban.cfg; echo exit:$?",
    )
    assert "/my/kanban.cfg" in result.stderr or "kanban.cfg" in result.stderr
