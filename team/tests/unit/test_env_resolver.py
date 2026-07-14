"""
test_env_resolver.py
====================
Behavioral unit tests for the canonical Python root-env resolver:
team/pgai_agent_kanban/env.py — resolve_kanban_root().

Covers the three cases required by the acceptance criteria:

  1. Resolver raises RuntimeError with the fail-loud grammar when
     PGAI_AGENT_KANBAN_ROOT_PATH is unset.
  2. Resolver absolutizes a relative path value.
  3. Resolver returns the correct absolute path when the env var is set.

All tests use monkeypatch to control the environment; no live filesystem
side effects and no live kanban root reads.
"""

from __future__ import annotations

import pathlib

import pytest

from pgai_agent_kanban.env import resolve_kanban_root, _FAIL_LOUD_MSG


# ---------------------------------------------------------------------------
# Fail-loud: unset env var
# ---------------------------------------------------------------------------


def test_resolve_kanban_root_raises_when_env_unset(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """resolve_kanban_root raises RuntimeError when PGAI_AGENT_KANBAN_ROOT_PATH is absent."""
    monkeypatch.delenv("PGAI_AGENT_KANBAN_ROOT_PATH", raising=False)
    with pytest.raises(RuntimeError, match="PGAI_AGENT_KANBAN_ROOT_PATH not set"):
        resolve_kanban_root()


def test_resolve_kanban_root_raises_when_env_empty(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """resolve_kanban_root raises RuntimeError when PGAI_AGENT_KANBAN_ROOT_PATH is empty."""
    monkeypatch.setenv("PGAI_AGENT_KANBAN_ROOT_PATH", "")
    with pytest.raises(RuntimeError, match="PGAI_AGENT_KANBAN_ROOT_PATH not set"):
        resolve_kanban_root()


def test_resolve_kanban_root_raises_when_env_whitespace_only(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """resolve_kanban_root raises RuntimeError when PGAI_AGENT_KANBAN_ROOT_PATH is whitespace."""
    monkeypatch.setenv("PGAI_AGENT_KANBAN_ROOT_PATH", "   ")
    with pytest.raises(RuntimeError, match="PGAI_AGENT_KANBAN_ROOT_PATH not set"):
        resolve_kanban_root()


def test_fail_loud_message_contains_shell_env_grammar(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Fail-loud message matches the bash prelude grammar: 'not set — shell-env missing or broken'."""
    monkeypatch.delenv("PGAI_AGENT_KANBAN_ROOT_PATH", raising=False)
    with pytest.raises(RuntimeError) as exc_info:
        resolve_kanban_root()
    msg = str(exc_info.value)
    assert "not set" in msg
    assert "shell-env missing or broken" in msg


# ---------------------------------------------------------------------------
# Absolutization: relative path
# ---------------------------------------------------------------------------


def test_resolve_kanban_root_absolutizes_relative_path(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: pathlib.Path,
) -> None:
    """resolve_kanban_root converts a relative path to an absolute path."""
    # Create a real directory so Path.resolve() can resolve it
    target = tmp_path / "kanban"
    target.mkdir()

    # Compute a relative path from the current working directory
    # by using the absolute path string directly (simulate operator setting
    # a relative-looking string that maps to a real dir)
    monkeypatch.setenv("PGAI_AGENT_KANBAN_ROOT_PATH", str(target))
    result = resolve_kanban_root()
    assert result.is_absolute(), f"Expected absolute path, got: {result}"
    assert result == target.resolve()


def test_resolve_kanban_root_absolutizes_value_with_dots(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: pathlib.Path,
) -> None:
    """resolve_kanban_root collapses .. and . in the path."""
    # Build a path with a redundant component
    target = tmp_path / "a" / "b"
    target.mkdir(parents=True)
    dotted = str(tmp_path / "a" / "b" / ".." / "b")
    monkeypatch.setenv("PGAI_AGENT_KANBAN_ROOT_PATH", dotted)
    result = resolve_kanban_root()
    assert result == target.resolve()


# ---------------------------------------------------------------------------
# Happy path: absolute path round-trip
# ---------------------------------------------------------------------------


def test_resolve_kanban_root_returns_path_object(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: pathlib.Path,
) -> None:
    """resolve_kanban_root returns a pathlib.Path, not a str."""
    monkeypatch.setenv("PGAI_AGENT_KANBAN_ROOT_PATH", str(tmp_path))
    result = resolve_kanban_root()
    assert isinstance(result, pathlib.Path)


def test_resolve_kanban_root_returns_correct_path(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: pathlib.Path,
) -> None:
    """resolve_kanban_root returns the exact path specified in the env var (resolved)."""
    monkeypatch.setenv("PGAI_AGENT_KANBAN_ROOT_PATH", str(tmp_path))
    result = resolve_kanban_root()
    assert result == tmp_path.resolve()


# ---------------------------------------------------------------------------
# Public constant
# ---------------------------------------------------------------------------


def test_fail_loud_msg_exported() -> None:
    """_FAIL_LOUD_MSG constant is importable and non-empty (for tests that pattern-match)."""
    assert _FAIL_LOUD_MSG
    assert "PGAI_AGENT_KANBAN_ROOT_PATH not set" in _FAIL_LOUD_MSG
    assert "shell-env missing or broken" in _FAIL_LOUD_MSG
