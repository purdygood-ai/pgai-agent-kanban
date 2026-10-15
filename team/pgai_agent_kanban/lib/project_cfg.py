"""
project_cfg.py — locate a project's config file and read one value from it,
by the rules ``team/scripts/lib/project_paths.sh`` applies.

The Python twins of ``_pp_project_cfg_file`` and ``_pp_read_cfg_key``, for a
caller whose answer must equal the bash resolver's for the same file:

``project_cfg_file(project_root)``
    ``<project_root>/project.cfg`` (INI) when it is a file, else the legacy
    ``<project_root>/PROJECT.cfg``, else ``None``.

``read_cfg_key(cfg_file, section, key)``
    The ``read_ini`` answer for ``[section] key``
    (``pgai_agent_kanban.lib.ini.read_line_value``); for a legacy
    ``PROJECT.cfg`` (no sections) whose ``read_ini`` answer is empty, the
    first bare ``key = value`` line, with one surrounding quote stripped from
    each side. ``""`` when nothing is found.

``strip_one_quote_each_side(value, quotes)``
    *value* with at most one leading and one trailing character from
    *quotes* removed.
"""

from __future__ import annotations

import pathlib
import re
from typing import Optional

from pgai_agent_kanban.lib import ini

__all__ = ["project_cfg_file", "read_cfg_key", "strip_one_quote_each_side"]


def project_cfg_file(project_root: pathlib.Path) -> Optional[pathlib.Path]:
    """``project.cfg`` when present, else a legacy ``PROJECT.cfg``, else None."""
    for name in ("project.cfg", "PROJECT.cfg"):
        candidate = project_root / name
        if candidate.is_file():
            return candidate
    return None


def strip_one_quote_each_side(value: str, quotes: str) -> str:
    """*value* without one leading and one trailing character from *quotes*."""
    if value[:1] and value[:1] in quotes:
        value = value[1:]
    if value[-1:] and value[-1:] in quotes:
        value = value[:-1]
    return value


def read_cfg_key(cfg_file: pathlib.Path, section: str, key: str) -> str:
    """One per-project config value, by the ``_pp_read_cfg_key`` rules.

    The ``read_ini`` answer for ``[section] key``; for a legacy
    ``PROJECT.cfg`` (no sections) whose ``read_ini`` answer is empty, the
    first bare ``key = value`` line, with one surrounding quote stripped.
    """
    value = ini.read_line_value(cfg_file, section, key) or ""
    if value or cfg_file.name != "PROJECT.cfg":
        return value
    bare = re.compile(r"^[ \t\n\r\f\v]*" + re.escape(key) + r"[ \t\n\r\f\v]*=")
    try:
        lines = cfg_file.read_text(encoding="utf-8", errors="replace").splitlines()
    except OSError:
        return ""
    for line in lines:
        if bare.match(line):
            raw = line.split("=", 1)[1].lstrip(" \t\n\r\f\v").rstrip(" \t\n\r\f\v")
            return strip_one_quote_each_side(raw, "\"'")
    return ""
