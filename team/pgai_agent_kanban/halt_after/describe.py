"""
describe.py — the HALT-AFTER token's operator-facing text, for bash callers.

The bash surfaces that show a HALT-AFTER token (the halt-after.sh arm
confirmation, the dashboard halt-scope line, the RC-close spend narration)
print it through :func:`pgai_agent_kanban.halt_after.token.describe_token`
by way of this command line, so none of them formats the token itself.

Usage (``python3 -m pgai_agent_kanban.halt_after.describe``):

  describe TOKEN
      Write describe_token(TOKEN) to stdout, with no trailing newline.
  describe-raw CONTENT
      Print describe_token(parse_token(CONTENT)), or CONTENT stripped when it
      does not parse (parse_token logs why on stderr).

A wrong argument count prints the usage on stderr and exits 2; ``--help``
prints this text and exits 0.
"""

from __future__ import annotations

import os
import sys
from typing import Optional

# Run by file path (``python3 .../describe.py``) or loaded from a file
# outside its package, this module has no parent package and the interpreter
# puts only its own directory on sys.path, so the package import below would
# not resolve. Put this file's own package root -- the directory holding the
# pgai_agent_kanban/ this file sits in -- at the front of sys.path so the
# import resolves from the same tree, never another, and take this file's
# own directory off sys.path: it is no import root, and the token.py beside
# this file would shadow the standard library's token module. Imported as a
# package member, sys.path is left untouched.
if not __package__:
    _OWN_DIR = os.path.dirname(os.path.abspath(__file__))
    sys.path[:] = [entry for entry in sys.path if os.path.abspath(entry or os.curdir) != _OWN_DIR]
    _OWN_PACKAGE_ROOT = os.path.dirname(os.path.dirname(_OWN_DIR))
    if _OWN_PACKAGE_ROOT not in sys.path:
        sys.path.insert(0, _OWN_PACKAGE_ROOT)

from pgai_agent_kanban.halt_after.token import describe_token, parse_token  # noqa: E402

__all__ = ["main"]


def main(argv: Optional[list] = None) -> int:
    """Command-line entry point; see the module docstring."""
    args = sys.argv[1:] if argv is None else argv
    if args and args[0] in ("-h", "--help"):
        print(__doc__.strip())
        return 0
    if len(args) != 2 or args[0] not in ("describe", "describe-raw"):
        print(
            "usage: python3 -m pgai_agent_kanban.halt_after.describe {describe TOKEN | describe-raw CONTENT}",
            file=sys.stderr,
        )
        return 2
    if args[0] == "describe":
        sys.stdout.write(describe_token(args[1]))
        return 0
    token = parse_token(args[1])
    print(describe_token(token) if token else args[1].strip())
    return 0


if __name__ == "__main__":
    sys.exit(main())
