"""
pgai_agent_kanban.lib.token_pricing — where a kanban tree keeps its token
pricing table and its token aggregator.

Both files belong to the kanban, never to a project: ``token_pricing.json``
sits in ``scripts/lib/`` and ``aggregate_tokens.py`` in ``pm-agent/`` of a
kanban tree. On an installed kanban that tree is the kanban root; in a
source checkout it is the repository's ``team/`` directory. One rule serves
both layouts, and no project's ``dev_tree_path`` is ever a candidate.

- :func:`pricing_table_path` / :func:`aggregator_path` name the file inside
  one given tree. ``pm-agent/aggregate_tokens.py`` passes its own tree.
- :func:`find_pricing_table` / :func:`find_aggregator` resolve the file for
  a kanban root: the root first (the install layout), then the tree this
  package ships in (a source checkout run against a root that carries no
  copy). The first existing file wins; None when neither exists.
  :func:`kanban_tree_candidates` lists the trees they look in.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Optional, Union

__all__ = [
    "AGGREGATOR_REL_PATH",
    "OWN_TREE_ROOT",
    "PRICING_REL_PATH",
    "aggregator_path",
    "find_aggregator",
    "find_pricing_table",
    "kanban_tree_candidates",
    "pricing_table_path",
]

#: The pricing table, relative to a kanban tree.
PRICING_REL_PATH = Path("scripts") / "lib" / "token_pricing.json"

#: The token aggregator, relative to a kanban tree.
AGGREGATOR_REL_PATH = Path("pm-agent") / "aggregate_tokens.py"

#: The kanban tree this package ships in: the kanban root of an install, the
#: repository's ``team/`` directory in a source checkout.
OWN_TREE_ROOT = Path(__file__).resolve().parents[2]

_PathLike = Union[str, "os.PathLike[str]"]


def pricing_table_path(tree_root: _PathLike) -> Path:
    """The pricing table inside the kanban tree *tree_root* (``<tree>/scripts/lib/token_pricing.json``)."""
    return Path(tree_root) / PRICING_REL_PATH


def aggregator_path(tree_root: _PathLike) -> Path:
    """The token aggregator inside the kanban tree *tree_root* (``<tree>/pm-agent/aggregate_tokens.py``)."""
    return Path(tree_root) / AGGREGATOR_REL_PATH


def kanban_tree_candidates(kanban_root: Optional[_PathLike]) -> tuple[Path, ...]:
    """The kanban trees a lookup for *kanban_root* reads, in order.

    *kanban_root* first, when given, then :data:`OWN_TREE_ROOT`; a tree
    named twice (an installed kanban, whose package sits under its own
    root) is listed once.
    """
    trees: list[Path] = []
    for tree in ((Path(kanban_root),) if kanban_root else ()) + (OWN_TREE_ROOT,):
        if all(tree.resolve() != seen.resolve() for seen in trees):
            trees.append(tree)
    return tuple(trees)


def _first_existing(kanban_root: Optional[_PathLike], rel_path: Path) -> Optional[Path]:
    for tree in kanban_tree_candidates(kanban_root):
        candidate = tree / rel_path
        if candidate.is_file():
            return candidate
    return None


def find_pricing_table(kanban_root: Optional[_PathLike]) -> Optional[Path]:
    """The pricing table a report for *kanban_root* reads, or None when no candidate tree has one."""
    return _first_existing(kanban_root, PRICING_REL_PATH)


def find_aggregator(kanban_root: Optional[_PathLike]) -> Optional[Path]:
    """The token aggregator a report for *kanban_root* runs, or None when no candidate tree has one."""
    return _first_existing(kanban_root, AGGREGATOR_REL_PATH)
