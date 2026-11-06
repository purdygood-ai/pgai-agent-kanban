"""
hook_timeout.py -- the default for kanban.cfg ``[release] hook_timeout_seconds``.

The key is the most seconds a release lifecycle hook (pre-squash, pre-tag,
post-tag), and the step that resolves which hook a phase runs, may take
before its process group is stopped. This module is the one place its
default is written; every reader asks it rather than restating the number.

Usage (import)::

    from pgai_agent_kanban.lib.hook_timeout import hook_timeout_default_seconds

    seconds = hook_timeout_default_seconds()

Usage (command line, for shell callers -- config_loader.sh's
``config_release_hook_timeout_seconds_default`` runs this through
``pp_run_ops``)::

    python3 -m pgai_agent_kanban.lib.hook_timeout default

Prints the default as plain decimal digits and exits 0. ``--help`` prints
the usage and exits 0; anything else is a usage error, exit 2.

Public API:
    hook_timeout_default_seconds() -> int
    main(argv=None) -> int
"""

from __future__ import annotations

import argparse
import sys
from collections.abc import Sequence

__all__ = ["hook_timeout_default_seconds", "main"]


def hook_timeout_default_seconds() -> int:
    """Seconds a release hook may run when ``[release] hook_timeout_seconds`` is absent, blank or unusable."""
    return 900


def _cli_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python3 -m pgai_agent_kanban.lib.hook_timeout",
        description=(
            "Print the default for kanban.cfg [release] hook_timeout_seconds: the seconds a release "
            "lifecycle hook may run before its process group is stopped."
        ),
    )
    parser.add_argument(
        "action",
        choices=["default"],
        help="'default': print the default number of seconds on stdout",
    )
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    """Command-line entry: print the default; return the exit status to use."""
    _cli_parser().parse_args(argv)
    print(hook_timeout_default_seconds())
    return 0


if __name__ == "__main__":
    sys.exit(main())
