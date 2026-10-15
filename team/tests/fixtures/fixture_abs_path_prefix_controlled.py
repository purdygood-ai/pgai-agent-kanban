"""fixture_abs_path_prefix_controlled.py
==========================================
NEGATIVE CONTROL for the Anti-pattern 8 check in
lint_test_anti_patterns.py: this file asserts on absolute paths built under
a root the test itself constructs -- via ``.startswith(expected_prefix)``
against a value derived from that same root, and via
``.relative_to(root).parts`` -- rather than searching the whole absolute
path for a hardcoded path-segment literal. The check must stay silent on
this file: both forms compute their expectation from a value the test
controls, so neither is coupled to the ambient scratch root's own prefix.

This fixture is excluded from the normal CI lint scan by the fixtures/
directory guard. It is scanned DIRECTLY by
team/tests/unit/test_lint_abs_path_substring_assertion.py to prove the
check does NOT fire here.
"""

from __future__ import annotations

import pathlib


def _assert_project_scoped_routing(resolved_path: str, kanban_root: pathlib.Path, project_name: str) -> None:
    """Controlled-prefix form: the expectation is computed, not hardcoded."""
    expected_prefix = str(kanban_root / "projects" / project_name / "logs" / "debug")
    assert resolved_path.startswith(expected_prefix), (
        f"Debug log resolved to wrong path for a project-scoped task.\n"
        f"Expected path under: {expected_prefix}\n"
        f"Got: {resolved_path}"
    )


def _assert_project_less_routing(resolved_path: str, kanban_root: pathlib.Path) -> None:
    """Controlled kanban_root-relative form: no hardcoded absolute-path literal."""
    resolved_relative_parts = pathlib.Path(resolved_path).relative_to(kanban_root).parts
    assert "projects" not in resolved_relative_parts, (
        f"Project-less debug log was incorrectly routed to a project directory: "
        f"{resolved_path}"
    )
