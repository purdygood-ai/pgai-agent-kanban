"""
project_cfg.py — locate a project's config file and read one value from it,
by the rules ``team/scripts/lib/project_paths.sh`` applies.

The Python twins of ``_pp_project_cfg_file``, ``_pp_read_cfg_key`` and
``pp_resolve_tiered_cfg_value``, for a caller whose answer must equal the bash
resolver's for the same file:

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

``read_first_assignment(cfg_file, key)``
    The first ``key = value`` line anywhere in *cfg_file*, section-blind,
    every surrounding quote stripped. Not a bash twin: the line scan the
    PM materializer has always used for its per-project fields.

``resolve_tiered_cfg_value(kanban_root, project_root, section, key, default="", *, legacy=True)``
    ``[section] key`` through the per-project config, then kanban.cfg, then
    *default*, reporting which tier answered. The Python twin of
    ``pp_resolve_tiered_cfg_value`` in ``team/scripts/lib/project_paths.sh``;
    both answer identically for the same files.

Tier resolution
---------------
Precedence, highest first, the first non-blank value winning::

    <project_root>/project.cfg    (or a legacy PROJECT.cfg, see below)
    <kanban_root>/kanban.cfg
    the caller's default

A tier whose file is missing or unreadable, whose section is absent, whose
key is absent, or whose value is blank (``key =``, ``key =   ``) passes to
the next tier; a lower tier is never read once a higher one answers. The
per-project file is read by :func:`read_cfg_key`
and kanban.cfg by ``ini.read_line_value``, so values carry the bash
``read_ini`` rules (inline comment stripped, key matched exactly, CRLF read
as LF). An empty *project_root* skips the project tier (root-only).

The per-project file is ``project.cfg`` when it exists, else a legacy
``PROJECT.cfg`` (:func:`project_cfg_file`); with ``legacy=False`` only
``project.cfg`` is read and a lone ``PROJECT.cfg`` is ignored. The tier is
reported as ``"project.cfg"`` for either file name; ``source`` names the
file actually read.

The resolver does no value validation: a caller validates the winning
value itself and names ``source`` in its refusal.
"""

from __future__ import annotations

import dataclasses
import os
import pathlib
import re
from typing import Optional, Union

from pgai_agent_kanban.lib import ini

__all__ = [
    "TIER_DEFAULT",
    "TIER_KANBAN_CFG",
    "TIER_PROJECT_CFG",
    "TieredValue",
    "project_cfg_file",
    "read_cfg_key",
    "read_first_assignment",
    "resolve_tiered_cfg_value",
    "strip_one_quote_each_side",
]

PathLike = Union[str, "os.PathLike[str]"]

#: Tier names :func:`resolve_tiered_cfg_value` reports, as its bash twin prints them.
TIER_PROJECT_CFG = "project.cfg"
TIER_KANBAN_CFG = "kanban.cfg"
TIER_DEFAULT = "default"


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


def read_first_assignment(cfg_file: Optional[PathLike], key: str) -> str:
    """The first ``key = value`` line in *cfg_file* naming *key*, in any section.

    Section headers are ignored, so a sectionless file reads the same as an
    INI one and a *key* under an earlier section wins over ``[project]``.
    A line is skipped when it holds no ``=`` or starts with ``#`` once
    blanks are trimmed; *key* is matched exactly after trimming; an inline
    comment is kept as part of the value. Every surrounding blank, ``'`` and
    ``"`` is stripped from the value.

    Args:
        cfg_file: The config file, or ``None`` / ``""`` for none.
        key:      The bare key name, e.g. ``"review_agent"``.

    Returns:
        The stripped value, or ``""`` when *cfg_file* is unset or not a file,
        cannot be read, or holds no line for *key*.

    Raises:
        UnicodeDecodeError: *cfg_file* is not valid UTF-8.
    """
    if not cfg_file:
        return ""
    path = pathlib.Path(cfg_file)
    if not path.is_file():
        return ""
    try:
        for line in path.read_text(encoding="utf-8").splitlines():
            stripped = line.strip()
            if "=" not in stripped or stripped.startswith("#"):
                continue
            name, _, value = stripped.partition("=")
            if name.strip() == key:
                return value.strip().strip("'\"")
    except OSError:
        pass
    return ""


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


@dataclasses.dataclass(frozen=True)
class TieredValue:
    """One value resolved through project.cfg > kanban.cfg > default.

    Attributes:
        value:  The winning value as read (never blank unless it is the
                caller's default).
        tier:   ``"project.cfg"``, ``"kanban.cfg"`` or ``"default"``.
        source: The file the value came from; ``""`` for the default.
    """

    value: str
    tier: str
    source: str


def resolve_tiered_cfg_value(
    kanban_root: PathLike,
    project_root: "Optional[PathLike]",
    section: str,
    key: str,
    default: str = "",
    *,
    legacy: bool = True,
) -> TieredValue:
    """Return ``[section] key`` from project.cfg, else kanban.cfg, else *default*.

    See "Tier resolution" in the module docstring for the full rules. The
    bash twin is ``pp_resolve_tiered_cfg_value`` in
    ``team/scripts/lib/project_paths.sh``.

    Args:
        kanban_root:  The kanban root; ``<kanban_root>/kanban.cfg`` is the
                      second tier.
        project_root: The project's root directory, or ``None`` / ``""``
                      to skip the project tier.
        section:      Section name.
        key:          Key name, matched exactly.
        default:      Returned, with tier ``"default"``, when neither file
                      carries a non-blank value.
        legacy:       Read a legacy ``PROJECT.cfg`` when ``project.cfg`` is
                      absent (default True).

    Returns:
        The winning value with its tier and source file.
    """
    if project_root:
        root = pathlib.Path(project_root)
        cfg = project_cfg_file(root) if legacy else root / "project.cfg"
        if cfg is not None:
            value = read_cfg_key(cfg, section, key)
            if value:
                return TieredValue(value, TIER_PROJECT_CFG, str(cfg))
    kanban_cfg = pathlib.Path(kanban_root) / "kanban.cfg"
    value = ini.read_line_value(kanban_cfg, section, key) or ""
    if value:
        return TieredValue(value, TIER_KANBAN_CFG, str(kanban_cfg))
    return TieredValue(default, TIER_DEFAULT, "")
