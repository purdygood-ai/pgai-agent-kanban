"""
wake_budget.py -- the defaults for kanban.cfg ``[wake] max_task_seconds`` and
``[wake] max_runtime_seconds``.

``max_task_seconds`` bounds one task's agent run; ``max_runtime_seconds``
bounds a whole wake batch. Either key set to 0 disables its bound; an unset
or blank key takes the default here, never 0. This module is the one place
the two defaults are written; every reader asks it rather than restating
the numbers.

Usage (import)::

    from pgai_agent_kanban.lib.wake_budget import MAX_RUNTIME_SECONDS_DEFAULT, MAX_TASK_SECONDS_DEFAULT

Usage (command line, for shell callers -- config_loader.sh's
``config_wake_max_task_seconds_default`` and
``config_wake_max_runtime_seconds_default`` run this through ``pp_run_ops``)::

    python3 -m pgai_agent_kanban.lib.wake_budget default max_task_seconds
    python3 -m pgai_agent_kanban.lib.wake_budget default max_runtime_seconds

Prints the key's default as plain decimal digits and exits 0. ``--help``
prints the usage and exits 0; anything else is a usage error, exit 2.

Public API:
    MAX_TASK_SECONDS_DEFAULT: int
    MAX_RUNTIME_SECONDS_DEFAULT: int
    main(argv=None) -> int
"""

from __future__ import annotations

import argparse
import sys
from collections.abc import Sequence

__all__ = ["MAX_RUNTIME_SECONDS_DEFAULT", "MAX_TASK_SECONDS_DEFAULT", "main"]

#: Seconds one task's agent run may take when ``[wake] max_task_seconds`` is
#: unset or blank (90 minutes).
MAX_TASK_SECONDS_DEFAULT = 5400

#: Seconds a whole wake batch may run when ``[wake] max_runtime_seconds`` is
#: unset or blank (4 hours).
MAX_RUNTIME_SECONDS_DEFAULT = 14400

_DEFAULTS = {
    "max_task_seconds": MAX_TASK_SECONDS_DEFAULT,
    "max_runtime_seconds": MAX_RUNTIME_SECONDS_DEFAULT,
}


def _cli_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python3 -m pgai_agent_kanban.lib.wake_budget",
        description=(
            "Print the default for a kanban.cfg [wake] budget key: max_task_seconds (one task's agent run) "
            "or max_runtime_seconds (a whole wake batch)."
        ),
    )
    parser.add_argument(
        "action",
        choices=["default"],
        help="'default': print the key's default number of seconds on stdout",
    )
    parser.add_argument("key", choices=sorted(_DEFAULTS), help="the [wake] key whose default to print")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    """Command-line entry: print the key's default; return the exit status to use."""
    args = _cli_parser().parse_args(argv)
    print(_DEFAULTS[args.key])
    return 0


if __name__ == "__main__":
    sys.exit(main())
