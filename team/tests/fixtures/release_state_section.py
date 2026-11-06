"""
release_state_section.py
========================
Read one release-state field the way the release-state readers do, keeping
an absent heading, an empty field and a value apart.

The section comes from ``pgai_agent_kanban.task_status.read_section`` with
the release-state reading (``exact_heading`` and ``comments``, as
``cm/read_state_field`` reads it) and the value from
``task_status.first_value_line``. Tests use it to assert what a written
release-state.md holds without folding the three cases into ``none`` the
way ``read_state_field`` does.

Usage:

    from tests.fixtures.release_state_section import release_state_section_value

    assert release_state_section_value(path, "Active RC") == "v1.2.3"
"""

from __future__ import annotations

import os
import pathlib

from pgai_agent_kanban.task_status import first_value_line, read_section


def release_state_section_value(path: str | os.PathLike[str], heading: str) -> str | None:
    """Return the ``## <heading>`` field value of the release-state file at *path*.

    Returns:
        ``None`` when the file does not exist or the heading is absent;
        ``""`` when the heading has no value before the next ``##`` heading,
        a ``---`` rule or the end of the file; otherwise the value, stripped
        (which may be the literal ``"none"``).
    """
    p = pathlib.Path(path)
    if not p.exists():
        return None
    body = read_section(p.read_text(encoding="utf-8"), heading, exact_heading=True, comments=True)
    return None if body is None else first_value_line(body)
