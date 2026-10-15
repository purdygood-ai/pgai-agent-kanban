"""fixture_abs_path_substring_assertion.py
===========================================
DELIBERATE BAD EXAMPLE -- DO NOT COPY THIS PATTERN INTO REAL TESTS.

This file carries the exact shape the Anti-pattern 8 check in
lint_test_anti_patterns.py exists to catch: an assertion that searches an
absolute path for a hardcoded path-segment literal ("/projects/") using the
``in`` / ``not in`` containment operator, rather than comparing against a
value the test itself computed. The literal's surrounding prefix is
whatever the ambient scratch root (PGAI_AGENT_KANBAN_TEMP_DIR / pytest's
``tmp_path``) happens to be -- a caller whose own scratch root contains the
same substring (the multi-project layout puts scratch space at
``.../projects/<name>/...``) makes this assertion fail with no defect in
the routing code it is meant to exercise.

This fixture is excluded from the normal CI lint scan by the fixtures/
directory guard. It is scanned DIRECTLY by
team/tests/unit/test_lint_abs_path_substring_assertion.py to prove the
check fires on exactly this shape and names the file and line where the
uncontrolled substring assertion sits.

DO NOT add an opt-out marker here -- the whole point is that the check
must flag this file.
"""

from __future__ import annotations

import pathlib


def _assert_project_less_routing(resolved_path: str, kanban_root: pathlib.Path) -> None:
    """Mirror of the substring-assertion shape this fixture exists to catch.

    The line below is the deliberate anti-pattern: it searches the whole
    absolute path for a hardcoded "/projects/" literal instead of asserting
    against something the test computed (e.g. the path relative to
    kanban_root).
    """
    assert "/projects/" not in resolved_path, (
        f"Project-less debug log was incorrectly routed to a project directory: "
        f"{resolved_path}"
    )
