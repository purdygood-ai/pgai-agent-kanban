#!/usr/bin/env python3
"""
module_probe.py — report whether Python modules import, for a bash caller.

install.sh asks whether an optional dependency (``yaml``) is importable
through this module instead of an inline ``python3 -c 'import ...'``
program.

Usage:
    python3 -m pgai_agent_kanban.lib.module_probe MODULE [MODULE ...]

Each MODULE is imported in turn. Exit 0 when every one imports; exit 1 at
the first that does not, naming it in one line on stderr. Nothing is
printed on stdout. ``--help`` prints this text and exits 0.

Public API:
    missing_modules(names) -> list[str]
"""

from __future__ import annotations

import argparse
import importlib
import sys
from typing import Iterable, Optional

__all__ = ["missing_modules"]


def missing_modules(names: Iterable[str]) -> list[str]:
    """Return the names in *names* that fail to import, in the order given.

    Any exception raised while importing counts as "does not import" -- a
    module that is present but broken is no more usable than an absent one.
    """
    missing = []
    for name in names:
        try:
            importlib.import_module(name)
        except Exception:  # noqa: BLE001 -- any failure means "not importable here"
            missing.append(name)
    return missing


def main(argv: Optional[list[str]] = None) -> int:
    """Run the probe: exit 0 when every MODULE imports, 1 otherwise."""
    parser = argparse.ArgumentParser(
        prog="python3 -m pgai_agent_kanban.lib.module_probe",
        description=(
            "Import each MODULE; exit 0 when every one imports, 1 (naming the "
            "first that does not on stderr) otherwise. Prints nothing on stdout."
        ),
    )
    parser.add_argument("modules", nargs="+", metavar="MODULE", help="A module name, e.g. yaml.")
    args = parser.parse_args(argv)
    missing = missing_modules(args.modules)
    if missing:
        print(f"module_probe: cannot import {missing[0]}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
