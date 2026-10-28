"""
temp_root.py — the one Python resolver for the framework temp root.

Every Python module that needs the directory the framework writes its
scratch files under calls :func:`resolve_temp_root`; none carries its own
precedence or its own last-resort path. The bash twin is ``pgai_temp_dir``
(and ``pgai_configured_temp_root``) in ``team/scripts/lib/temp.sh``; the
rows of ``team/tests/fixtures/temp_root_cases.json`` are the shared
contract both answer to.

Precedence, highest first
-------------------------
1. ``PGAI_AGENT_KANBAN_TEMP_DIR`` when set and not blank (surrounding
   whitespace is dropped).
2. ``kanban.cfg [paths] tmp_root`` joined with ``tmp_subdir``, each key
   taking its own default when absent or blank: ``tmp_root`` defaults to
   :data:`DEFAULT_TMP_ROOT` (``/tmp``) and ``tmp_subdir`` to
   :data:`DEFAULT_TMP_SUBDIR` (``pgai_kanban_tmp``) -- the defaults
   kanban.cfg_example and the config_loader.sh key registry document. A
   cfg that sets only ``tmp_root = /data`` gives ``/data/pgai_kanban_tmp``.
   kanban.cfg is found one of two ways:

   - an explicit kanban root passed by the caller: ``<kanban_root>/kanban.cfg``
     and nothing else;
   - no kanban root given: ``$PGAI_AGENT_KANBAN_ROOT_PATH/kanban.cfg``
     (through :func:`pgai_agent_kanban.env.resolve_kanban_root`), and when
     that variable is unset or its kanban.cfg leaves ``tmp_root`` blank, the
     kanban.cfg of the install this module itself sits in (two directories
     above ``lib/``) -- the same self-locate step temp.sh takes.
3. :data:`FALLBACK_TEMP_ROOT` when no kanban.cfg is readable -- the same
   root both defaults give.

Values are read with :func:`pgai_agent_kanban.lib.ini.read_line_value`, the
line-for-line twin of bash ``read_ini``, so both resolvers see the same
``tmp_root`` and ``tmp_subdir`` for the same file.

The filesystem-root invariant
-----------------------------
A resolved value that canonicalises to ``/`` -- ``/``, ``///``, ``/.``,
``/tmp/..``, a ``tmp_root = /`` with ``tmp_subdir = /`` -- is replaced by
:data:`FALLBACK_TEMP_ROOT`, on every tier including the override, so no
caller is ever handed the whole filesystem as its scratch root. Only the
canonical form is compared: a value that passes comes back spelled as
configured (a trailing slash is kept, a symlinked root is not rewritten
to its target).

The resolver is read-only: it never creates the directory it names.

Public API:
    TEMP_DIR_ENV_VAR
    DEFAULT_TMP_ROOT
    DEFAULT_TMP_SUBDIR
    FALLBACK_TEMP_ROOT
    resolve_temp_root(kanban_root=None, *, honour_override=True) -> str
"""

from __future__ import annotations

import os
import pathlib
from typing import Union

from pgai_agent_kanban.env import resolve_kanban_root
from pgai_agent_kanban.lib import ini

__all__ = ["DEFAULT_TMP_ROOT", "DEFAULT_TMP_SUBDIR", "FALLBACK_TEMP_ROOT", "TEMP_DIR_ENV_VAR", "resolve_temp_root"]

#: The environment override, tier 1.
TEMP_DIR_ENV_VAR = "PGAI_AGENT_KANBAN_TEMP_DIR"

#: ``[paths] tmp_root`` when kanban.cfg leaves it absent or blank.
DEFAULT_TMP_ROOT = "/tmp"

#: ``[paths] tmp_subdir`` when kanban.cfg leaves it absent or blank.
DEFAULT_TMP_SUBDIR = "pgai_kanban_tmp"

#: The last-resort temp root, tier 3, and the value substituted for anything
#: that names the filesystem root: the two per-key defaults joined.
FALLBACK_TEMP_ROOT = f"{DEFAULT_TMP_ROOT}/{DEFAULT_TMP_SUBDIR}"

_CFG_FILENAME = "kanban.cfg"
_PATHS_SECTION = "paths"

PathLike = Union[str, "os.PathLike[str]"]


def _own_install_root() -> pathlib.Path:
    """The install root this module sits in: ``<root>/pgai_agent_kanban/lib/temp_root.py``.

    Located from this file's own path (not resolved through symlinks), as
    temp.sh locates its install root from its own directory, so each tree
    finds itself and never another.
    """
    return pathlib.Path(os.path.abspath(__file__)).parents[2]


def _paths_pair(cfg: pathlib.Path) -> "tuple[str, str]":
    """``(tmp_root, tmp_subdir)`` from *cfg*'s ``[paths]``; ``""`` for each one absent or unreadable."""
    tmp_root = (ini.read_line_value(cfg, _PATHS_SECTION, "tmp_root") or "").strip()
    tmp_subdir = (ini.read_line_value(cfg, _PATHS_SECTION, "tmp_subdir") or "").strip()
    return tmp_root, tmp_subdir


def _configured_pair(kanban_root: "PathLike | None") -> "tuple[str, str]":
    """The ``[paths]`` pair tier 2 reads, from the kanban.cfg the caller's choice locates.

    An explicit kanban root that is empty or blank names no kanban.cfg, so
    nothing is read and both keys take their defaults -- as ``pgai_temp_dir``
    reads nothing for an empty path -- rather than a kanban.cfg relative to
    the working directory.
    """
    if kanban_root is not None:
        if not str(kanban_root).strip():
            return "", ""
        return _paths_pair(pathlib.Path(kanban_root) / _CFG_FILENAME)
    tmp_root = tmp_subdir = ""
    try:
        tmp_root, tmp_subdir = _paths_pair(resolve_kanban_root() / _CFG_FILENAME)
    except RuntimeError:
        pass  # PGAI_AGENT_KANBAN_ROOT_PATH unset or blank: go straight to self-locate.
    if not tmp_root:
        own_cfg = _own_install_root() / _CFG_FILENAME
        if own_cfg.is_file():
            tmp_root, tmp_subdir = _paths_pair(own_cfg)
    return tmp_root, tmp_subdir


def _names_filesystem_root(value: str) -> bool:
    """True when *value* is empty or canonicalises to ``/``."""
    return not value or os.path.realpath(value) == os.sep


def resolve_temp_root(kanban_root: "PathLike | None" = None, *, honour_override: bool = True) -> str:
    """Return the framework temp root, resolved by the precedence in the module docstring.

    Args:
        kanban_root:     The kanban root whose kanban.cfg tier 2 reads. When
                         None, kanban.cfg is found through
                         ``PGAI_AGENT_KANBAN_ROOT_PATH`` and then this
                         module's own install root. An empty or blank root
                         reads no kanban.cfg (the per-key defaults apply).
        honour_override: When False, ``PGAI_AGENT_KANBAN_TEMP_DIR`` is
                         ignored and the answer is the root the
                         configuration alone gives (temp.sh's
                         ``pgai_configured_temp_root``), for a caller asking
                         whether a root is the framework's own.

    Returns:
        The temp root as a string, spelled as the winning tier gives it.
        Never empty and never a value that canonicalises to ``/``. The
        directory is not created.
    """
    value = os.environ.get(TEMP_DIR_ENV_VAR, "").strip() if honour_override else ""
    if not value:
        tmp_root, tmp_subdir = _configured_pair(kanban_root)
        value = f"{tmp_root or DEFAULT_TMP_ROOT}/{tmp_subdir or DEFAULT_TMP_SUBDIR}"
    if _names_filesystem_root(value):
        value = FALLBACK_TEMP_ROOT
    return value
