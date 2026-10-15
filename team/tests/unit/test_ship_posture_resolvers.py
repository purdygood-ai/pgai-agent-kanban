"""
test_ship_posture_resolvers.py
==============================
Unit tests proving the two ship-posture resolvers agree:

  - pp_ship_posture()      in team/scripts/lib/project_paths.sh (bash)
  - resolve_ship_posture() in team/pgai_agent_kanban/overwatch/resolution_matrix.py

Each case builds one synthetic kanban root under tmp_path (a kanban.cfg and
one project's config file) and runs BOTH resolvers against that same root.
The bash side runs as a real subprocess that sources the shipped library;
the Python side is the real function, its warning stream captured. For
every case the two must return the same posture, both must return it
without failing, both must print the same number of warning lines (zero
or one), and a warning's text after its per-language prefix must be
identical.

Cases covered:
  - key absent everywhere -> matrix, silent
  - kanban.cfg only (matrix, and always on a local-only lane)
  - project.cfg overriding kanban.cfg in both directions
  - a blank project.cfg value falling through to kanban.cfg
  - an inline comment after the value
  - a legacy PROJECT.cfg (bare key=value) read by both
  - an unrecognized value in either file -> matrix + one warning naming the
    file path and the raw value
  - the remote-lane guard: push_to_remote = true (explicit or by default)
    with always and no confirm -> matrix + one warning; a non-true confirm
    value -> matrix + one warning naming it; confirm = true in either file
    -> always; push_to_remote = false -> always with no confirm key at all

No live kanban root is touched: KANBAN_ROOT points at tmp_path.
"""

from __future__ import annotations

import io
import pathlib
from dataclasses import dataclass
from typing import Optional

import pytest

from pgai_agent_kanban.overwatch.resolution_matrix import (
    SHIP_POSTURE_ALWAYS,
    SHIP_POSTURE_MATRIX,
    resolve_ship_posture,
)
from tests.unit.shell_harness import run_bash

_LIB = "scripts/lib/project_paths.sh"
_PROJECT = "posture-proj"
_BASH_PREFIX = "project_paths.sh: pp_ship_posture: "
_PY_PREFIX = "resolution_matrix: resolve_ship_posture: "


@dataclass(frozen=True)
class PostureCase:
    """One fixture kanban root and the posture both resolvers must return.

    ``project_cfg`` / ``kanban_cfg`` are the file bodies (None = the file
    does not exist). ``project_cfg_name`` selects project.cfg or the legacy
    PROJECT.cfg. ``warn_must_name`` lists substrings the single warning line
    must carry; an empty tuple means no warning at all is expected.
    """

    id: str
    project_cfg: Optional[str]
    kanban_cfg: Optional[str]
    expected: str
    warn_must_name: tuple = ()
    project_cfg_name: str = "project.cfg"


_LOCAL = "[project]\npush_to_remote = false\n"
_REMOTE = "[project]\npush_to_remote = true\n"

_CASES = [
    PostureCase("absent_everywhere", None, None, SHIP_POSTURE_MATRIX),
    PostureCase("absent_with_empty_files", "[project]\n", "[release]\n", SHIP_POSTURE_MATRIX),
    PostureCase("kanban_only_matrix", _LOCAL, "[release]\nship_posture = matrix\n", SHIP_POSTURE_MATRIX),
    PostureCase("kanban_only_always_local_lane", _LOCAL, "[release]\nship_posture = always\n", SHIP_POSTURE_ALWAYS),
    PostureCase(
        "project_always_overrides_kanban_matrix",
        _LOCAL + "[release]\nship_posture = always\n",
        "[release]\nship_posture = matrix\n",
        SHIP_POSTURE_ALWAYS,
    ),
    PostureCase(
        "project_matrix_overrides_kanban_always",
        _LOCAL + "[release]\nship_posture = matrix\n",
        "[release]\nship_posture = always\n",
        SHIP_POSTURE_MATRIX,
    ),
    PostureCase(
        "blank_project_value_falls_through_to_kanban",
        _LOCAL + "[release]\nship_posture =\n",
        "[release]\nship_posture = always\n",
        SHIP_POSTURE_ALWAYS,
    ),
    PostureCase(
        "inline_comment_after_value",
        _LOCAL + "[release]\nship_posture = always   # checkpoint lane\n",
        None,
        SHIP_POSTURE_ALWAYS,
    ),
    PostureCase(
        "legacy_project_cfg_bare_key",
        "push_to_remote=false\nship_posture=always\n",
        None,
        SHIP_POSTURE_ALWAYS,
        project_cfg_name="PROJECT.cfg",
    ),
    PostureCase(
        "unrecognized_project_value",
        _LOCAL + "[release]\nship_posture = alwasy\n",
        "[release]\nship_posture = always\n",
        SHIP_POSTURE_MATRIX,
        warn_must_name=("'alwasy'", "{project_cfg_path}", "ship_posture"),
    ),
    PostureCase(
        "unrecognized_kanban_value",
        _LOCAL,
        "[release]\nship_posture = Always\n",
        SHIP_POSTURE_MATRIX,
        warn_must_name=("'Always'", "{kanban_cfg_path}", "ship_posture"),
    ),
    PostureCase(
        "remote_lane_always_without_confirm",
        _REMOTE + "[release]\nship_posture = always\n",
        None,
        SHIP_POSTURE_MATRIX,
        warn_must_name=("push_to_remote = true", "ship_posture_confirm_remote", "{project_cfg_path}"),
    ),
    PostureCase(
        "remote_lane_by_default_always_without_confirm",
        "[project]\n",
        "[release]\nship_posture = always\n",
        SHIP_POSTURE_MATRIX,
        warn_must_name=("push_to_remote = true", "ship_posture_confirm_remote", "{kanban_cfg_path}"),
    ),
    PostureCase(
        "remote_lane_confirm_false",
        _REMOTE + "[release]\nship_posture = always\nship_posture_confirm_remote = false\n",
        None,
        SHIP_POSTURE_MATRIX,
        warn_must_name=("'false'", "ship_posture_confirm_remote"),
    ),
    PostureCase(
        "remote_lane_confirm_unrecognized",
        _REMOTE + "[release]\nship_posture = always\nship_posture_confirm_remote = yes\n",
        None,
        SHIP_POSTURE_MATRIX,
        warn_must_name=("'yes'", "ship_posture_confirm_remote"),
    ),
    PostureCase(
        "remote_lane_confirm_true_in_project",
        _REMOTE + "[release]\nship_posture = always\nship_posture_confirm_remote = true\n",
        None,
        SHIP_POSTURE_ALWAYS,
    ),
    PostureCase(
        "remote_lane_confirm_true_in_kanban",
        _REMOTE + "[release]\nship_posture = always\n",
        "[release]\nship_posture_confirm_remote = true\n",
        SHIP_POSTURE_ALWAYS,
    ),
    PostureCase(
        "remote_lane_project_confirm_false_overrides_kanban_true",
        _REMOTE + "[release]\nship_posture = always\nship_posture_confirm_remote = false\n",
        "[release]\nship_posture_confirm_remote = true\n",
        SHIP_POSTURE_MATRIX,
        warn_must_name=("'false'", "{project_cfg_path}"),
    ),
    PostureCase(
        "local_lane_always_needs_no_confirm",
        _LOCAL + "[release]\nship_posture = always\n",
        None,
        SHIP_POSTURE_ALWAYS,
    ),
    PostureCase(
        "remote_lane_matrix_needs_no_confirm",
        _REMOTE + "[release]\nship_posture = matrix\n",
        None,
        SHIP_POSTURE_MATRIX,
    ),
]


@dataclass(frozen=True)
class Resolved:
    """What one resolver produced for one fixture root."""

    posture: str
    warnings: list
    returncode: int
    raw: str  # full captured output, for failure messages


def _build_root(tmp_path: pathlib.Path, case: PostureCase) -> dict:
    """Write the case's config files under tmp_path; return the paths used."""
    root = tmp_path / "kanban"
    project_root = root / "projects" / _PROJECT
    project_root.mkdir(parents=True)
    project_cfg_path = project_root / case.project_cfg_name
    kanban_cfg_path = root / "kanban.cfg"
    if case.project_cfg is not None:
        project_cfg_path.write_text(case.project_cfg, encoding="utf-8")
    if case.kanban_cfg is not None:
        kanban_cfg_path.write_text(case.kanban_cfg, encoding="utf-8")
    return {
        "root": root,
        "project_cfg_path": str(project_cfg_path),
        "kanban_cfg_path": str(kanban_cfg_path),
    }


def _warning_bodies(text: str, prefix: str) -> list:
    """Every warning line in *text*, with the resolver's own prefix removed."""
    return [line[len(prefix):] for line in text.splitlines() if line.startswith(prefix)]


def _run_bash_resolver(tmp_path: pathlib.Path, root: pathlib.Path) -> Resolved:
    result = run_bash(
        tmp_path,
        f"source {_LIB} && pp_ship_posture {_PROJECT}",
        extra_env={"KANBAN_ROOT": str(root), "PGAI_PROJECT_NAME": ""},
    )
    return Resolved(
        posture=result.stdout.strip(),
        warnings=_warning_bodies(result.stderr, _BASH_PREFIX),
        returncode=result.returncode,
        raw=f"--- stdout ---\n{result.stdout}\n--- stderr ---\n{result.stderr}",
    )


def _run_python_resolver(root: pathlib.Path) -> Resolved:
    stream = io.StringIO()
    posture = resolve_ship_posture(_PROJECT, str(root), stream=stream)
    text = stream.getvalue()
    return Resolved(
        posture=posture,
        warnings=_warning_bodies(text, _PY_PREFIX),
        returncode=0,
        raw=f"--- returned ---\n{posture}\n--- stream ---\n{text}",
    )


@pytest.mark.parametrize("case", _CASES, ids=[c.id for c in _CASES])
def test_bash_and_python_ship_posture_resolvers_agree(tmp_path: pathlib.Path, case: PostureCase) -> None:
    """Both resolvers return the expected posture for the same fixture root,
    exit cleanly, and print the same warning (or none)."""
    paths = _build_root(tmp_path, case)
    bash = _run_bash_resolver(tmp_path, paths["root"])
    python = _run_python_resolver(paths["root"])
    diag = f"bash:\n{bash.raw}\npython:\n{python.raw}"

    assert bash.returncode == 0, diag
    assert bash.posture == case.expected, diag
    assert python.posture == case.expected, diag

    expected_warnings = 1 if case.warn_must_name else 0
    assert len(bash.warnings) == expected_warnings, diag
    assert len(python.warnings) == expected_warnings, diag
    assert bash.warnings == python.warnings, diag

    for needle in case.warn_must_name:
        assert needle.format(**paths) in bash.warnings[0], diag


def test_bash_resolver_writes_nothing_but_the_warning_to_stderr(tmp_path: pathlib.Path) -> None:
    """The unrecognized-value fallback is exactly one stderr line in total —
    nothing else from the library leaks around it — and stdout is only the
    posture."""
    case = next(c for c in _CASES if c.id == "unrecognized_project_value")
    paths = _build_root(tmp_path, case)
    result = run_bash(
        tmp_path,
        f"source {_LIB} && pp_ship_posture {_PROJECT}",
        extra_env={"KANBAN_ROOT": str(paths["root"])},
    )
    diag = f"--- stdout ---\n{result.stdout}\n--- stderr ---\n{result.stderr}"
    assert result.returncode == 0, diag
    assert result.stdout == "matrix\n", diag
    assert len(result.stderr.splitlines()) == 1, diag


def test_python_resolver_defaults_its_warning_stream_to_stderr(
    tmp_path: pathlib.Path, capsys: pytest.CaptureFixture
) -> None:
    """Without an explicit stream the Python resolver warns on stderr, one
    line, and leaves stdout untouched."""
    case = next(c for c in _CASES if c.id == "remote_lane_always_without_confirm")
    paths = _build_root(tmp_path, case)
    assert resolve_ship_posture(_PROJECT, str(paths["root"])) == SHIP_POSTURE_MATRIX
    captured = capsys.readouterr()
    assert captured.out == ""
    assert len(captured.err.splitlines()) == 1, captured.err


def test_bash_resolver_uses_pgai_project_name_when_no_argument(tmp_path: pathlib.Path) -> None:
    """Like every pp_* accessor, the project name falls back to
    PGAI_PROJECT_NAME when no argument is passed."""
    case = next(c for c in _CASES if c.id == "local_lane_always_needs_no_confirm")
    paths = _build_root(tmp_path, case)
    result = run_bash(
        tmp_path,
        f"source {_LIB} && pp_ship_posture",
        extra_env={"KANBAN_ROOT": str(paths["root"]), "PGAI_PROJECT_NAME": _PROJECT},
    )
    diag = f"--- stdout ---\n{result.stdout}\n--- stderr ---\n{result.stderr}"
    assert result.returncode == 0, diag
    assert result.stdout.strip() == SHIP_POSTURE_ALWAYS, diag
