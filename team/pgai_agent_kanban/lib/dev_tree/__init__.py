"""
pgai_agent_kanban.lib.dev_tree — where a project's repository is: the one dev_tree_path resolver.

The chain ``project.cfg_example`` documents for ``dev_tree_path``, first
non-empty value wins:

  1. the project's own ``[project] dev_tree_path`` (``project.cfg``, or the
     bare key in a legacy ``PROJECT.cfg``) -- :func:`project_dev_tree`;
  2. the ``PGAI_DEV_TREE_PATH`` environment variable, then ``kanban.cfg
     [paths] dev_tree_path`` -- :func:`global_dev_tree`.

:func:`resolve_dev_tree` is the whole chain. It is what the wake uses for a
project's worktrees and feature branches, so every reader of state the wake
writes there calls it. A reader of state only the project's own repository
can hold -- its release tags, its RC branches -- calls :func:`project_dev_tree`
alone: a project that names no repository of its own must never read
another project's release history as its own. The bash twin of the whole
chain is ``pp_resolve_dev_tree`` / ``pp_dev_tree_path`` in
``team/scripts/lib/project_paths.sh``; its global tier, and every bash
script's, is ``resolve_global_dev_tree`` in ``team/scripts/lib/dev_tree.sh``,
which runs this module's command line through ``pp_run_ops``.

Values are returned as written (surrounding blanks and one pair of quotes
removed); nothing is validated or resolved on disk.

The command line, for shell callers, is this package's ``__main__``
(``python3 -m pgai_agent_kanban.lib.dev_tree global [--kanban-cfg FILE]``);
it lives there so ``-m`` runs a module no importer of this one has loaded.

Public API:
    project_dev_tree(project_root) -> str
    global_dev_tree(kanban_root, env=None, *, kanban_cfg=None) -> str
    resolve_dev_tree(kanban_root, project_root, env=None) -> str
    kanban_root_of(project_root) -> pathlib.Path
"""

from __future__ import annotations

import os
import pathlib
from collections.abc import Mapping

from pgai_agent_kanban.lib import ini
from pgai_agent_kanban.lib.project_cfg import project_cfg_file, read_cfg_key, strip_one_quote_each_side

__all__ = ["global_dev_tree", "kanban_root_of", "project_dev_tree", "resolve_dev_tree"]


def _clean(value: str) -> str:
    return strip_one_quote_each_side(value.strip(), "\"'").strip()


def project_dev_tree(project_root: str | os.PathLike[str]) -> str:
    """The project's own ``[project] dev_tree_path``, or ``""`` when it names none."""
    cfg = project_cfg_file(pathlib.Path(project_root))
    return _clean(read_cfg_key(cfg, "project", "dev_tree_path")) if cfg is not None else ""


def global_dev_tree(
    kanban_root: str | os.PathLike[str],
    env: Mapping[str, str] | None = None,
    *,
    kanban_cfg: str | os.PathLike[str] | None = None,
) -> str:
    """``PGAI_DEV_TREE_PATH``, else ``kanban.cfg [paths] dev_tree_path``, else ``""``.

    The kanban.cfg read is *kanban_cfg* when given, else
    ``<kanban_root>/kanban.cfg``; a file that does not exist reads as no value.
    """
    environ = os.environ if env is None else env
    from_env = _clean(environ.get("PGAI_DEV_TREE_PATH", "") or "")
    if from_env:
        return from_env
    cfg = pathlib.Path(kanban_cfg) if kanban_cfg is not None else pathlib.Path(kanban_root) / "kanban.cfg"
    if not cfg.is_file():
        return ""
    return _clean(ini.read_line_value(cfg, "paths", "dev_tree_path") or "")


def resolve_dev_tree(
    kanban_root: str | os.PathLike[str],
    project_root: str | os.PathLike[str],
    env: Mapping[str, str] | None = None,
) -> str:
    """The documented chain: the project's own tree, else the global one, else ``""``."""
    return project_dev_tree(project_root) or global_dev_tree(kanban_root, env)


def kanban_root_of(project_root: str | os.PathLike[str]) -> pathlib.Path:
    """The kanban root holding *project_root*: two levels up from
    ``<root>/projects/<name>``, else *project_root* itself (a single-project
    layout, where the project is the root)."""
    path = pathlib.Path(project_root)
    return path.parent.parent if path.parent.name == "projects" else path

