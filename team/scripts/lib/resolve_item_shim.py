#!/usr/bin/env python3
"""resolve_item_shim.py — CLI shim for bash delegation to resolve_item.

Usage:
    python3 resolve_item_shim.py PROJECT_ROOT KEY

This script is the sole bridge between bash callers and the Python
resolve_item implementation in pgai_agent_kanban.ops.resolve.  It locates
the team/ package root relative to its own path, adds it to sys.path, and
delegates to _cli_main() with the same stdout/exit-code contract as the
former bash resolve_item() function.

Stdout (on success, exit 0 or 2):
    Three lines:
        Line 1: item type — task | bug | priority | requirement
        Line 2: absolute path to the item
        Line 3: current state value

Exit codes:
    0  success (exactly one match)
    1  argument / filesystem error
    2  ambiguous prefix (multiple matches); first match emitted to stdout;
       warning emitted to stderr
    3  not found
"""
from __future__ import annotations

import os
import sys
import pathlib


def _find_team_dir() -> pathlib.Path:
    """Locate the team/ directory containing the pgai_agent_kanban package.

    Resolution order:
    1. PGAI_DEV_TREE_PATH environment variable (set by run-unit-tests.sh and
       the test harness when the shim is copied to a synthetic scripts_stub/).
    2. Walk up from this file's location until pgai_agent_kanban/__init__.py
       is found.  This handles the normal install at team/scripts/lib/ as well
       as any copy made by test fixtures.

    Raises:
        RuntimeError: When neither resolution path succeeds.
    """
    # 1. Honour the canonical dev-tree env var.
    dev_tree = os.environ.get("PGAI_DEV_TREE_PATH", "")
    if dev_tree:
        candidate = pathlib.Path(dev_tree)
        if (candidate / "pgai_agent_kanban" / "__init__.py").is_file():
            return candidate

    # 2. Walk upward from this file to find a directory that contains
    #    pgai_agent_kanban/ops/resolve.py.
    here = pathlib.Path(__file__).resolve().parent
    for _ in range(10):
        candidate = here
        if (candidate / "pgai_agent_kanban" / "ops" / "resolve.py").is_file():
            return candidate
        parent = here.parent
        if parent == here:
            break
        here = parent

    raise RuntimeError(
        f"resolve_item_shim: cannot locate team/ directory containing "
        f"pgai_agent_kanban package.  Set PGAI_DEV_TREE_PATH to the "
        f"team/ directory path."
    )


_TEAM_DIR = _find_team_dir()

if str(_TEAM_DIR) not in sys.path:
    sys.path.insert(0, str(_TEAM_DIR))

from pgai_agent_kanban.ops.resolve import _cli_main  # noqa: E402

if __name__ == "__main__":
    sys.exit(_cli_main(sys.argv[1:]))
