#!/usr/bin/env python3
"""
json_field.py — print one top-level field of a JSON object for a bash caller.

Bash scripts that hold a JSON document (a provider usage record, a
tokens.json, a per-RC state record) read single fields through this module
instead of an inline ``python3 -c`` program.

Usage:
    python3 -m pgai_agent_kanban.lib.json_field [--file] [--default TEXT]
        [--null TEXT] [--json | --string-only] SOURCE KEY
    python3 -m pgai_agent_kanban.lib.json_field [--file] --length SOURCE

SOURCE is the JSON text itself, or with ``--file`` the path of a file
holding it. ``--length`` prints the number of entries of the whole document
(a list or an object). Otherwise the document must be a JSON object and the
field KEY prints as:

  * a string, as written;
  * ``--json``: any value as compact JSON (``json.dumps``);
  * ``--string-only``: a string that is not blank, as written; any other
    value (``null``, a number, a blank string) prints the ``--default`` text;
  * ``null``: the ``--null`` text (default ``None``);
  * any other value: Python's ``str()`` of it (``True``, ``1``, ...).

An absent KEY prints the ``--default`` text (default empty). Exit 0; exit 1,
printing nothing on stdout and one line on stderr, when SOURCE is not
readable JSON, not an object (or with ``--length`` has no length), or KEY
is missing without ``--length``. ``--help`` prints this text and exits 0.

Public API:
    field_text(document, key, default="", null="None", as_json=False, string_only=False) -> str
"""

from __future__ import annotations

import argparse
import json
import sys
from typing import Any, Optional

__all__ = ["field_text"]


def field_text(
    document: Any,
    key: str,
    default: str = "",
    null: str = "None",
    as_json: bool = False,
    string_only: bool = False,
) -> str:
    """Return the text the command line prints for *key* of *document*.

    Args:
        document: A parsed JSON object (dict).
        key:      Top-level key to read.
        default:  Text for an absent key.
        null:     Text for a JSON ``null`` value.
        as_json:  Print the value as compact JSON instead.
        string_only: Only a non-blank string value prints; anything else
                  prints *default*.

    Raises:
        TypeError: *document* is not a dict.
    """
    if not isinstance(document, dict):
        raise TypeError(f"expected a JSON object, got {type(document).__name__}")
    if key not in document:
        return default
    value = document[key]
    if string_only:
        return value if isinstance(value, str) and value.strip() else default
    if as_json:
        return json.dumps(value)
    if value is None:
        return null
    return value if isinstance(value, str) else str(value)


def main(argv: Optional[list] = None) -> int:
    """Command-line entry point; see the module docstring."""
    parser = argparse.ArgumentParser(
        prog="python3 -m pgai_agent_kanban.lib.json_field",
        description="Print one top-level field of a JSON object, or the length of a JSON document.",
    )
    parser.add_argument("source", help="The JSON text, or with --file the path of a file holding it.")
    parser.add_argument("key", nargs="?", help="The top-level key to print (not with --length).")
    parser.add_argument("--length", action="store_true", help="Print the number of entries of the document.")
    parser.add_argument("--file", action="store_true", help="SOURCE is a file path.")
    parser.add_argument("--default", default="", help="Text printed for an absent key (default: empty).")
    parser.add_argument("--null", default="None", help="Text printed for a null value (default: None).")
    parser.add_argument("--json", action="store_true", help="Print the value as compact JSON.")
    parser.add_argument(
        "--string-only", action="store_true",
        help="Print the value only when it is a non-blank string; otherwise print the --default text.",
    )
    args = parser.parse_args(argv)
    try:
        if args.file:
            with open(args.source, encoding="utf-8") as handle:
                document = json.load(handle)
        else:
            document = json.loads(args.source)
        if args.length:
            if not isinstance(document, (list, dict)):
                raise TypeError(f"expected a JSON array or object, got {type(document).__name__}")
            text = str(len(document))
        elif args.key is None:
            raise ValueError("KEY is required without --length")
        else:
            text = field_text(
                document, args.key, default=args.default, null=args.null, as_json=args.json,
                string_only=args.string_only,
            )
    except (OSError, ValueError, TypeError) as exc:
        what = "the document's length" if args.length else f"field {args.key!r}"
        print(f"json_field: cannot read {what}: {exc}", file=sys.stderr)
        return 1
    print(text)
    return 0


if __name__ == "__main__":
    sys.exit(main())
