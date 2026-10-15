"""
test_ops_resolve_project_name.py — the Python project-name resolver agrees
with the bash one, byte for byte.

``pgai_agent_kanban.ops.resolve.resolve_project_name`` is what the operator
API runs before calling an ops function, in place of the
``pp_resolve_project_name`` step every project-scoped CLI script runs first
(``team/scripts/lib/project_paths.sh``). Each case below gives both the same
registry and the same typed name and asserts the same answer: the same
resolved name on success, and on a refusal the same three-line
``error``/``message``/``remedy`` block -- the Python exception's text plus a
newline is exactly what the bash resolver writes to stderr, which is what an
HTTP caller and a CLI user each see.
"""

from __future__ import annotations

import pathlib
from typing import Optional

import pytest

from pgai_agent_kanban.ops.context import OpsContext
from pgai_agent_kanban.ops.errors import Ambiguous, NotFound, OpsError
from pgai_agent_kanban.ops.resolve import resolve_project_name
from tests.unit.shell_harness import run_bash

_LIB = "scripts/lib/project_paths.sh"

_TWO_PROJECTS = "[project:alpha-app]\npriority = 2\n\n[project:beta]\npriority = 1\n"
_SHARED_PREFIX = "[project:web-api]\npriority = 1\n\n[project:web-ui]\npriority = 2\n"
_EMPTY_REGISTRY = "# no projects registered yet\n"


def _bash_resolve(tmp_path: pathlib.Path, kanban_root: pathlib.Path, typed: str):
    """Run the bash resolver against *kanban_root* for *typed*.

    PGAI_PROJECT_NAME is cleared: an ambient project name (a wake exports
    one) would put a project in scope for the refusal ledger, whose
    narration for a project this board does not register would then sit
    on stderr ahead of the block this test compares byte for byte.
    """
    return run_bash(
        tmp_path,
        f'source {_LIB} && pp_resolve_project_name "$TYPED"',
        extra_env={"KANBAN_ROOT": str(kanban_root), "TYPED": typed, "PGAI_PROJECT_NAME": ""},
    )


def _board(tmp_path: pathlib.Path, registry: Optional[str]) -> pathlib.Path:
    root = tmp_path / "board"
    root.mkdir()
    if registry is not None:
        (root / "projects.cfg").write_text(registry, encoding="utf-8")
    return root


@pytest.mark.parametrize(
    "registry, typed, expected",
    [
        pytest.param(_TWO_PROJECTS, "beta", "beta", id="exact-name"),
        pytest.param(_TWO_PROJECTS, "alpha", "alpha-app", id="unique-hyphen-prefix"),
        pytest.param(None, "anything-goes", "anything-goes", id="no-registry-passes-through"),
    ],
)
def test_resolved_name_matches_the_bash_resolver(
    tmp_path: pathlib.Path, registry: Optional[str], typed: str, expected: str
) -> None:
    root = _board(tmp_path, registry)
    bash = _bash_resolve(tmp_path, root, typed)
    assert bash.returncode == 0, f"bash resolver refused:\n{bash.stderr}"
    assert bash.stdout.strip() == expected, bash.stdout

    assert resolve_project_name(OpsContext(kanban_root=root), typed) == expected


@pytest.mark.parametrize(
    "registry, typed, error_type, error_key",
    [
        pytest.param(_TWO_PROJECTS, "ghost", NotFound, "project_resolve.not_found", id="unregistered-name"),
        pytest.param(_TWO_PROJECTS, "alp", NotFound, "project_resolve.not_found", id="prefix-without-hyphen-boundary"),
        pytest.param(_EMPTY_REGISTRY, "ghost", NotFound, "project_resolve.not_found", id="empty-registry"),
        pytest.param(_SHARED_PREFIX, "web", Ambiguous, "project_resolve.ambiguous", id="shared-prefix"),
    ],
)
def test_refusal_text_is_byte_identical_to_the_bash_resolver(
    tmp_path: pathlib.Path,
    registry: str,
    typed: str,
    error_type: type,
    error_key: str,
) -> None:
    root = _board(tmp_path, registry)
    bash = _bash_resolve(tmp_path, root, typed)
    assert bash.returncode == 1, f"bash resolver should refuse with exit 1:\n{bash.stdout}{bash.stderr}"
    assert error_key in bash.stderr, bash.stderr

    with pytest.raises(error_type) as caught:
        resolve_project_name(OpsContext(kanban_root=root), typed)
    assert f"{caught.value}\n" == bash.stderr


def test_ambiguous_refusal_names_every_candidate(tmp_path: pathlib.Path) -> None:
    root = _board(tmp_path, _SHARED_PREFIX)
    with pytest.raises(Ambiguous) as caught:
        resolve_project_name(OpsContext(kanban_root=root), "web")
    assert caught.value.candidates == ["web-api", "web-ui"]


def test_unparseable_registry_is_an_ops_error_naming_the_file(tmp_path: pathlib.Path) -> None:
    root = _board(tmp_path, "[project:broken\npriority = 1\n")
    with pytest.raises(OpsError) as caught:
        resolve_project_name(OpsContext(kanban_root=root), "broken")
    assert "projects.cfg" in str(caught.value)
