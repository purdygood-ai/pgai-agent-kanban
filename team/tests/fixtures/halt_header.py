"""
halt_header.py — the dashboard header's HALT reading, for route parity tests.

GET /board (``projects[]``) and GET /projects/{name} report ``halt`` /
``halt_text`` for a project; the dashboard header (GET /dashboard/header,
``show-header.sh``) draws the same HALT state on its ``chain=`` line, one
``[<KIND> <SCOPE>[ <event>]]`` bracket per active scope.  This module gives
the tests on both routes one golden board to read, one parser that turns
the header line into the ``(halt, halt_text)`` pair a route should
report, and one list of sentinel layouts to compare them on.

Every root is a throwaway golden board under the test's ``tmp_path``.
"""

from __future__ import annotations

import pathlib
import re
from typing import Any

import pytest

from tests.fixtures.dashboard_render_fixture import AMBIENT_LEAK_ENV_VARS_TO_CLEAR
from tests.fixtures.golden_board import PROJECT_NAME, build_synthetic_board

__all__ = [
    "HEADER_HALT_CASES",
    "build_halt_board",
    "header_halt_pair",
    "write_sentinels",
]

_PKG_DIR = pathlib.Path(__file__).resolve().parents[2] / "pgai_agent_kanban"

#: One HALT bracket on the header's chain= line: kind, scope, optional event.
_BRACKET_RE = re.compile(r"\[(HALT|HALT-AFTER)\s+(\S+?)(?:\s+([^\]]+?))?\s*\]")

#: (sentinel files relative to the kanban root, the pair both routes report).
#: Each layout leaves the header one bracket, so the header names the pair.
HEADER_HALT_CASES = [
    pytest.param({}, (False, ""), id="no-sentinel"),
    pytest.param({"HALT": "halted\n"}, (True, "HALT"), id="kanban-wide-halt"),
    pytest.param({f"projects/{PROJECT_NAME}/HALT-AFTER": "rc\n"}, (False, "HALT-AFTER rc"),
                 id="project-halt-after"),
    pytest.param({"HALT-AFTER": "rc\n"}, (False, "HALT-AFTER rc"), id="kanban-wide-halt-after"),
    pytest.param({"HALT": "halted\n", "HALT-AFTER": "rc\n"}, (True, "HALT"),
                 id="kanban-wide-halt-beats-kanban-wide-halt-after"),
]


def build_halt_board(tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch) -> pathlib.Path:
    """Build the golden board under *tmp_path* and point the environment at it.

    The package is symlinked beside the board's ``scripts/`` (the header
    script reaches it there), the ambient project variables are cleared,
    and the root and temp variables name the board and a scratch directory
    inside *tmp_path*.  ``NO_COLOR`` keeps the header line free of escapes.
    """
    root = tmp_path / "halt-board"
    build_synthetic_board(root, with_queues=True)
    (root / "pgai_agent_kanban").symlink_to(_PKG_DIR, target_is_directory=True)
    scratch = tmp_path / "halt-board-scratch"
    scratch.mkdir()
    for key in AMBIENT_LEAK_ENV_VARS_TO_CLEAR:
        monkeypatch.delenv(key, raising=False)
    monkeypatch.setenv("KANBAN_ROOT", str(root))
    monkeypatch.setenv("PGAI_AGENT_KANBAN_ROOT_PATH", str(root))
    monkeypatch.setenv("PGAI_AGENT_KANBAN_TEMP_DIR", str(scratch))
    monkeypatch.setenv("NO_COLOR", "1")
    return root


def write_sentinels(root: pathlib.Path, sentinels: "dict[str, str]") -> None:
    """Write each sentinel body to its path relative to *root*."""
    for rel_path, body in sentinels.items():
        path = root / rel_path
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(body, encoding="utf-8")


def header_halt_pair(client: Any, project: str = PROJECT_NAME) -> "tuple[bool, str]":
    """Return the ``(halt, halt_text)`` pair GET /dashboard/header draws for *project*.

    Reads the header's ``chain=`` line: no bracket is ``(False, "")``, a
    ``[HALT <scope>]`` bracket ``(True, "HALT")``, a ``[HALT-AFTER <scope>
    <event>]`` bracket ``(False, "HALT-AFTER <event>")``.  Only the
    ``GLOBAL`` scope and *project*'s own are read; more than one such
    bracket fails, since the pair can name only one.
    """
    resp = client.get("/dashboard/header", params={"project": project})
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["exit_code"] == 0, body["stderr"]
    lines = [line for line in body["stdout"].splitlines() if "chain=" in line]
    assert len(lines) == 1, f"expected one chain= line in the header:\n{body['stdout']}"
    brackets = [m for m in _BRACKET_RE.finditer(lines[0]) if m.group(2) in ("GLOBAL", project)]
    assert len(brackets) <= 1, f"the header names more than one HALT scope: {lines[0]!r}"
    if not brackets:
        return (False, "")
    kind, _scope, event = brackets[0].groups()
    if kind == "HALT":
        return (True, "HALT")
    return (False, f"HALT-AFTER {event}" if event else "HALT-AFTER")
