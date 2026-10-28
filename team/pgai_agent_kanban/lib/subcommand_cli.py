"""
subcommand_cli.py -- the one home for a verb-dispatching CLI's ``main()``.

A command-line module whose argparse parser gives each verb its handler with
``set_defaults(func=handler)`` takes its entry point from here instead of
writing the parse-and-dispatch body itself:

    main = subcommand_main(_build_parser)

    if __name__ == "__main__":
        sys.exit(main())

``main(argv=None)`` builds a fresh parser, parses *argv* (``sys.argv[1:]``
when None) and returns the chosen handler's return value as the exit code.
argparse's own exits pass through unchanged: ``--help`` exits 0 and a
usage error exits 2, both as ``SystemExit``.

Public API:
    subcommand_main(build_parser) -> main(argv=None) -> int
"""

from __future__ import annotations

import argparse
from typing import Callable, Optional

__all__ = ["subcommand_main"]


def subcommand_main(
    build_parser: Callable[[], argparse.ArgumentParser],
) -> Callable[[Optional[list]], int]:
    """Return a ``main(argv=None) -> int`` that parses *argv* with a parser
    from *build_parser* and returns what the chosen verb's ``func`` returns.

    *build_parser* is called on every ``main`` call, so each run parses with
    a fresh parser.
    """

    def main(argv: Optional[list] = None) -> int:
        """Command-line entry point; see the module docstring."""
        args = build_parser().parse_args(argv)
        return args.func(args)

    return main
