"""
cache_marker_reset.py -- reset one item's processed marker in a backlog cache.

OVERWATCH's cache-marker drift check
(team/scripts/lib/overwatch-checks/check-cache-marker-drift.sh) finds a bug
or priority file whose ``## Status`` is ``open`` while its line in
``bug_backlog.md`` or ``priority_backlog.md`` still reads ``[x]``. After it
backs the cache file up, it calls this module to turn that item's ``[x]``
back to ``[ ]``.

Only a line of the shape ``<ws>-<ws>[x]<ws><item id>`` followed by
whitespace or the end of the line is rewritten -- the dash must be
followed by at least one blank, and the bracket must hold exactly ``x``.
Every such line for the item is reset; the ``x`` is the only character
that changes, so the list prefix, the spacing and the description keep
their bytes. An item id that is a prefix of a longer id never matches
the longer one's line. No other line is touched,
including a line for the same item that carries another marker.

The cache file is read as UTF-8 with undecodable bytes replaced, and
written back as UTF-8 in place.

Usage:
    python3 -m pgai_agent_kanban.overwatch.cache_marker_reset [--] CACHE_FILE ITEM_ID

On success prints, on stderr,
``Reset <n> entry(ies) for <item id> from [x] to [ ] in <cache file>`` and
exits 0. Exits 1 with one stderr line, and the file unchanged, when the
cache file cannot be read (``Cannot read cache file: <error>``), when it
holds no ``[x]`` line for the item
(``Could not find [x] entry for <item id> in <cache file>; no change made.``),
or when it cannot be written (``Failed to write cache file: <error>``).
``--help`` prints this text and exits 0.

Public API:
    reset_processed_marker_text(text, item_id) -> tuple[str, int]
    reset_processed_marker(cache_file, item_id) -> int
    main(argv=None) -> int
"""

from __future__ import annotations

import argparse
import pathlib
import re
import sys

__all__ = [
    "CacheMarkerResetError",
    "main",
    "reset_processed_marker",
    "reset_processed_marker_text",
]


class CacheMarkerResetError(Exception):
    """The reset could not be made; the message is the one stderr line the
    command prints, and the cache file is unchanged."""


def reset_processed_marker_text(text: str, item_id: str) -> tuple[str, int]:
    """Return *text* with every ``- [x] <item_id>`` line reset to ``- [ ]``,
    and the number of lines reset.

    See the module docstring for the exact line shape matched.
    """
    entry_re = re.compile(
        rf"^(\s*-\s+\[)x(\]\s+{re.escape(item_id)})(\s.*)?$",
        re.M,
    )
    return entry_re.subn(
        lambda m: m.group(1) + " " + m.group(2) + (m.group(3) if m.group(3) else ""),
        text,
    )


def reset_processed_marker(cache_file: pathlib.Path | str, item_id: str) -> int:
    """Reset *item_id*'s ``[x]`` lines in *cache_file* to ``[ ]`` and return
    how many were reset (at least 1).

    Raises:
        CacheMarkerResetError: the file cannot be read, holds no ``[x]``
            line for *item_id* (nothing is written), or cannot be written.
    """
    cache_path = pathlib.Path(cache_file)
    try:
        text = cache_path.read_text(encoding="utf-8", errors="replace")
    except OSError as e:
        raise CacheMarkerResetError(f"Cannot read cache file: {e}") from e

    new_text, n = reset_processed_marker_text(text, item_id)
    if n == 0:
        raise CacheMarkerResetError(f"Could not find [x] entry for {item_id} in {cache_path}; no change made.")

    try:
        cache_path.write_text(new_text, encoding="utf-8")
    except OSError as e:
        raise CacheMarkerResetError(f"Failed to write cache file: {e}") from e
    return n


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python3 -m pgai_agent_kanban.overwatch.cache_marker_reset",
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("cache_file", help="The backlog cache file (bug_backlog.md or priority_backlog.md).")
    parser.add_argument("item_id", help="The bug or priority id whose [x] line is reset.")
    return parser


def main(argv: list | None = None) -> int:
    """Command-line entry point; see the module docstring."""
    args = _build_parser().parse_args(argv)
    try:
        n = reset_processed_marker(args.cache_file, args.item_id)
    except CacheMarkerResetError as exc:
        print(exc, file=sys.stderr)
        return 1
    print(
        f"Reset {n} entry(ies) for {args.item_id} from [x] to [ ] in {pathlib.Path(args.cache_file)}",
        file=sys.stderr,
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
