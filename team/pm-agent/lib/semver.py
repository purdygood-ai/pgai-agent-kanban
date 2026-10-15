"""team/pm-agent/lib/semver.py
Semantic version helpers for pm-agent callers, re-exported from
``pgai_agent_kanban.lib.semver`` -- the one implementation of version
ordering. Every name below is the shared module's own object; nothing is
redefined here.

Public API (see ``pgai_agent_kanban/lib/semver.py`` for behaviour):
    parse, compare, lt, le, gt, ge, eq, from_filename,
    parse_version, version_sort_key
"""

from __future__ import annotations

from pgai_agent_kanban.lib.semver import (
    compare,
    eq,
    from_filename,
    ge,
    gt,
    le,
    lt,
    parse,
    parse_version,
    version_sort_key,
)

__all__ = [
    "compare",
    "eq",
    "from_filename",
    "ge",
    "gt",
    "le",
    "lt",
    "parse",
    "parse_version",
    "version_sort_key",
]
