"""
test_artifacts.py
=================
Behavioral unit tests for team/pm-agent/lib/artifacts.py.

Covers the path-resolution helpers and the get_next_version() atomic
allocation function.  All tests use tmp_path as the synthetic kanban root;
no live filesystem paths or environment leakage.

The tests set kanban_root explicitly rather than relying on environment
variables so that monkeypatching is not needed for the basic happy-path
tests (though one test group verifies env-var resolution).
"""

from __future__ import annotations

import pathlib

import pytest

try:
    from pm_agent.lib.artifacts import (
        get_project_path,
        get_version_path,
        get_next_version,
        get_input_path,
        get_working_path,
        get_output_path,
        ArtifactsError,
        _resolve_kanban_root,
        _read_next_version_from_text,
        _write_next_version_in_text,
    )
except ImportError:
    import sys
    sys.path.insert(0, str(pathlib.Path(__file__).parent.parent.parent / "pm-agent"))
    from lib.artifacts import (  # type: ignore[no-redef]
        get_project_path,
        get_version_path,
        get_next_version,
        get_input_path,
        get_working_path,
        get_output_path,
        ArtifactsError,
        _resolve_kanban_root,
        _read_next_version_from_text,
        _write_next_version_in_text,
    )


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def test_read_next_version_from_text_extracts_integer() -> None:
    """_read_next_version_from_text returns the integer after '## Next Version'."""
    text = "# Project: Test\n\n## Next Version\n7\n"
    assert _read_next_version_from_text(text) == 7


def test_read_next_version_from_text_returns_zero_when_absent() -> None:
    """_read_next_version_from_text returns 0 when the section is missing."""
    text = "# Project: Test\n\n## Description\nNo version field here.\n"
    assert _read_next_version_from_text(text) == 0


def test_write_next_version_in_text_updates_existing_field() -> None:
    """_write_next_version_in_text replaces the existing Next Version value."""
    text = "# Project\n\n## Next Version\n3\n"
    result = _write_next_version_in_text(text, 4)
    assert "4" in result
    # Old value should no longer appear at the right position
    assert _read_next_version_from_text(result) == 4


def test_write_next_version_in_text_appends_when_field_absent() -> None:
    """_write_next_version_in_text appends a ## Next Version section when none exists."""
    text = "# Project\n"
    result = _write_next_version_in_text(text, 1)
    assert _read_next_version_from_text(result) == 1


# ---------------------------------------------------------------------------
# _resolve_kanban_root()
# ---------------------------------------------------------------------------


def test_resolve_kanban_root_uses_explicit_argument(tmp_path: pathlib.Path) -> None:
    """_resolve_kanban_root returns the explicit argument as a Path."""
    result = _resolve_kanban_root(kanban_root=str(tmp_path))
    assert result == tmp_path


def test_resolve_kanban_root_uses_env_var_when_arg_is_none(
    tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """_resolve_kanban_root reads PGAI_AGENT_KANBAN_ROOT_PATH when kanban_root is None."""
    monkeypatch.setenv("PGAI_AGENT_KANBAN_ROOT_PATH", str(tmp_path))
    result = _resolve_kanban_root(kanban_root=None)
    assert result == tmp_path


# ---------------------------------------------------------------------------
# get_project_path()
# ---------------------------------------------------------------------------


def test_get_project_path_returns_artifacts_subdir(tmp_path: pathlib.Path) -> None:
    """get_project_path returns $kanban_root/artifacts/<project-name>."""
    result = get_project_path("my-project", kanban_root=tmp_path)
    assert result == tmp_path / "artifacts" / "my-project"


def test_get_project_path_accepts_numeric_start(tmp_path: pathlib.Path) -> None:
    """get_project_path accepts project names starting with a digit."""
    result = get_project_path("3d-project", kanban_root=tmp_path)
    assert result == tmp_path / "artifacts" / "3d-project"


def test_get_project_path_raises_for_uppercase_name(tmp_path: pathlib.Path) -> None:
    """get_project_path raises ArtifactsError for project names with uppercase letters."""
    with pytest.raises(ArtifactsError, match="Invalid project name"):
        get_project_path("MyProject", kanban_root=tmp_path)


def test_get_project_path_raises_for_leading_hyphen(tmp_path: pathlib.Path) -> None:
    """get_project_path raises ArtifactsError when the name starts with a hyphen."""
    with pytest.raises(ArtifactsError, match="Invalid project name"):
        get_project_path("-bad", kanban_root=tmp_path)


def test_get_project_path_raises_for_empty_name(tmp_path: pathlib.Path) -> None:
    """get_project_path raises ArtifactsError for an empty project name."""
    with pytest.raises(ArtifactsError, match="Invalid project name"):
        get_project_path("", kanban_root=tmp_path)


def test_get_project_path_does_not_create_directory(tmp_path: pathlib.Path) -> None:
    """get_project_path returns the path without creating the directory."""
    result = get_project_path("my-project", kanban_root=tmp_path)
    assert not result.exists()


# ---------------------------------------------------------------------------
# get_version_path()
# ---------------------------------------------------------------------------


def test_get_version_path_returns_versioned_subdirectory(tmp_path: pathlib.Path) -> None:
    """get_version_path returns $project_path/v<N>."""
    result = get_version_path("my-project", 3, kanban_root=tmp_path)
    assert result == tmp_path / "artifacts" / "my-project" / "v3"


def test_get_version_path_raises_for_zero_version(tmp_path: pathlib.Path) -> None:
    """get_version_path raises ArtifactsError when version is 0."""
    with pytest.raises(ArtifactsError, match="positive integer"):
        get_version_path("my-project", 0, kanban_root=tmp_path)


def test_get_version_path_raises_for_negative_version(tmp_path: pathlib.Path) -> None:
    """get_version_path raises ArtifactsError when version is negative."""
    with pytest.raises(ArtifactsError, match="positive integer"):
        get_version_path("my-project", -1, kanban_root=tmp_path)


def test_get_version_path_raises_for_non_integer_version(tmp_path: pathlib.Path) -> None:
    """get_version_path raises ArtifactsError when version is not an integer."""
    with pytest.raises(ArtifactsError, match="positive integer"):
        get_version_path("my-project", "1", kanban_root=tmp_path)  # type: ignore[arg-type]


# ---------------------------------------------------------------------------
# get_next_version()
# ---------------------------------------------------------------------------


def test_get_next_version_allocates_version_one_on_first_call(
    tmp_path: pathlib.Path,
) -> None:
    """get_next_version allocates v1 when no PROJECT.md exists yet."""
    version_path = get_next_version("new-project", kanban_root=tmp_path)
    assert version_path == tmp_path / "artifacts" / "new-project" / "v1"
    assert version_path.is_dir()


def test_get_next_version_creates_version_directory(tmp_path: pathlib.Path) -> None:
    """get_next_version creates the allocated version directory on disk."""
    version_path = get_next_version("dir-test", kanban_root=tmp_path)
    assert version_path.exists()
    assert version_path.is_dir()


def test_get_next_version_increments_on_successive_calls(
    tmp_path: pathlib.Path,
) -> None:
    """get_next_version allocates v1, then v2, then v3 on successive calls."""
    v1 = get_next_version("sequential", kanban_root=tmp_path)
    v2 = get_next_version("sequential", kanban_root=tmp_path)
    v3 = get_next_version("sequential", kanban_root=tmp_path)
    assert v1 == tmp_path / "artifacts" / "sequential" / "v1"
    assert v2 == tmp_path / "artifacts" / "sequential" / "v2"
    assert v3 == tmp_path / "artifacts" / "sequential" / "v3"


def test_get_next_version_honours_existing_next_version_field(
    tmp_path: pathlib.Path,
) -> None:
    """get_next_version uses the existing ## Next Version field when PROJECT.md exists."""
    project_dir = tmp_path / "artifacts" / "pre-existing"
    project_dir.mkdir(parents=True)
    (project_dir / "PROJECT.md").write_text(
        "# Project: pre-existing\n\n## Next Version\n5\n", encoding="utf-8"
    )
    version_path = get_next_version("pre-existing", kanban_root=tmp_path)
    assert version_path == tmp_path / "artifacts" / "pre-existing" / "v5"


def test_get_next_version_updates_project_md_after_allocation(
    tmp_path: pathlib.Path,
) -> None:
    """get_next_version increments ## Next Version in PROJECT.md after each allocation."""
    get_next_version("counter-check", kanban_root=tmp_path)
    project_md = tmp_path / "artifacts" / "counter-check" / "PROJECT.md"
    content = project_md.read_text(encoding="utf-8")
    # After first allocation (v1), Next Version should be 2
    assert _read_next_version_from_text(content) == 2


# ---------------------------------------------------------------------------
# get_input_path(), get_working_path(), get_output_path()
# ---------------------------------------------------------------------------


def test_get_input_path_returns_input_subdirectory_and_creates_it(
    tmp_path: pathlib.Path,
) -> None:
    """get_input_path returns $version_path/input/ and creates it."""
    path = get_input_path("my-project", 1, kanban_root=tmp_path)
    assert path == tmp_path / "artifacts" / "my-project" / "v1" / "input"
    assert path.is_dir()


def test_get_working_path_returns_working_subdirectory_and_creates_it(
    tmp_path: pathlib.Path,
) -> None:
    """get_working_path returns $version_path/working/ and creates it."""
    path = get_working_path("my-project", 2, kanban_root=tmp_path)
    assert path == tmp_path / "artifacts" / "my-project" / "v2" / "working"
    assert path.is_dir()


def test_get_output_path_returns_output_subdirectory_and_creates_it(
    tmp_path: pathlib.Path,
) -> None:
    """get_output_path returns $version_path/output/ and creates it."""
    path = get_output_path("my-project", 3, kanban_root=tmp_path)
    assert path == tmp_path / "artifacts" / "my-project" / "v3" / "output"
    assert path.is_dir()


def test_get_input_path_raises_for_invalid_project_name(tmp_path: pathlib.Path) -> None:
    """get_input_path propagates ArtifactsError for invalid project names."""
    with pytest.raises(ArtifactsError):
        get_input_path("InvalidName", 1, kanban_root=tmp_path)


def test_get_working_path_raises_for_zero_version(tmp_path: pathlib.Path) -> None:
    """get_working_path raises ArtifactsError when version is 0."""
    with pytest.raises(ArtifactsError):
        get_working_path("my-project", 0, kanban_root=tmp_path)
