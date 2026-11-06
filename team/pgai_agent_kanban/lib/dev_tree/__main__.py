"""
pgai_agent_kanban.lib.dev_tree -- command-line entry point.

Invoked by bash as ``pp_run_ops pgai_agent_kanban.lib.dev_tree global
[--kanban-cfg FILE]`` (``resolve_global_dev_tree`` in
``team/scripts/lib/dev_tree.sh``), so the global tiers of the dev_tree_path
chain are implemented once, in :func:`pgai_agent_kanban.lib.dev_tree.global_dev_tree`.

    global [--kanban-cfg FILE]
        Prints ``PGAI_DEV_TREE_PATH``, else ``kanban.cfg [paths]
        dev_tree_path``, else nothing, with no trailing newline, and exits 0.
        The kanban.cfg read is FILE when given, else ``kanban.cfg`` under
        ``PGAI_AGENT_KANBAN_ROOT_PATH`` (default ``~/pgai_agent_kanban``).

``--help`` prints the usage and exits 0; a usage error exits 2.
"""

from __future__ import annotations

import argparse
import pathlib
import sys
from collections.abc import Sequence

# Run by file path (``python3 .../dev_tree/__main__.py``) or loaded from a
# file outside its package, this module has no parent package and the
# interpreter puts only its own directory on sys.path, so the package imports
# below would not resolve. Put this file's own package root -- the directory
# holding the pgai_agent_kanban/ this file sits in -- at the front of sys.path
# so the imports resolve from the same tree, never another. Imported as a
# package member, sys.path is left untouched.
if not __package__:
    _OWN_PACKAGE_ROOT = str(pathlib.Path(__file__).resolve().parents[3])
    if _OWN_PACKAGE_ROOT not in sys.path:
        sys.path.insert(0, _OWN_PACKAGE_ROOT)

from pgai_agent_kanban.env import resolve_kanban_root_or_default  # noqa: E402
from pgai_agent_kanban.lib.dev_tree import global_dev_tree  # noqa: E402


def _cli_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python3 -m pgai_agent_kanban.lib.dev_tree",
        description=(
            "Print the global dev tree: PGAI_DEV_TREE_PATH, else kanban.cfg [paths] dev_tree_path, "
            "else nothing. The value is printed as written (blanks and one pair of quotes removed), "
            "with no trailing newline, and is not checked on disk."
        ),
    )
    sub = parser.add_subparsers(dest="action", required=True, metavar="ACTION")
    p_global = sub.add_parser(
        "global",
        help="print the global dev tree (environment, then kanban.cfg [paths] dev_tree_path)",
    )
    p_global.add_argument(
        "--kanban-cfg",
        metavar="FILE",
        help="kanban.cfg to read (default: kanban.cfg under PGAI_AGENT_KANBAN_ROOT_PATH, else ~/pgai_agent_kanban)",
    )
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    """Command-line entry: print the requested value; return the exit status to use."""
    args = _cli_parser().parse_args(argv)
    sys.stdout.write(global_dev_tree(resolve_kanban_root_or_default(), kanban_cfg=args.kanban_cfg))
    return 0


if __name__ == "__main__":
    sys.exit(main())
