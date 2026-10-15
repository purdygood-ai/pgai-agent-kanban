#!/usr/bin/env python3
"""
number_format.py -- team/scripts/lib/ entry point for the shared number
formats, which live in pgai_agent_kanban.lib.number_format.

Re-exports format_int() and format_one_decimal() so a script or test that
puts team/scripts/lib/ on sys.path keeps importing them as
``from number_format import format_int``, and runs the package module's
CLI when executed directly:

    python3 team/scripts/lib/number_format.py 5644879
    python3 team/scripts/lib/number_format.py --one-decimal 41000
    python3 team/scripts/lib/number_format.py --help

The package sits beside team/scripts/ in both the dev tree (team/) and the
live install (the install root), so this file locates it from its own
path -- two levels above team/scripts/lib/ -- and never from the
environment.
"""

from __future__ import annotations

import pathlib
import sys

_OWN_ROOT = str(pathlib.Path(__file__).resolve().parent.parent.parent)  # lib/ -> scripts/ -> root
if _OWN_ROOT not in sys.path:
    sys.path.insert(0, _OWN_ROOT)

from pgai_agent_kanban.lib.number_format import format_int, format_one_decimal, main  # noqa: E402

__all__ = ["format_int", "format_one_decimal", "main"]

if __name__ == "__main__":
    sys.exit(main())
