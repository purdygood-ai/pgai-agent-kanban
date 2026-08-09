"""
palette.py — Shared, cached loader for the canonical default project palette.

Single source of truth for the eight default project colors used when a
project has no explicit ``dashboard_color`` set in projects.cfg.  This
module is the Python counterpart to ``PGAI_DEFAULT_PALETTE`` in
team/scripts/lib/projects_cfg.sh -- both read the same on-disk definition
file instead of hand-transcribing a duplicate copy of the array/list:

    team/config/default_palette.txt            (dev tree)
    <kanban_root>/config/default_palette.txt   (live install)

Prior to this module, board.py and projects.py each hand-transcribed their
own copy of the palette array, and those two copies (and the bash array)
had already drifted from each other and from the canonical arrangement.
This module exists to eliminate that class of drift by giving both Python
call sites one shared, cached read of the single on-disk definition file.

Self-location:  the definition file is resolved relative to this module's
own ``__file__``, never via ``PGAI_AGENT_KANBAN_ROOT_PATH``.  In both the
dev tree (``team/pgai_agent_kanban/lib/palette.py`` -> up three levels ->
``team/``) and the live install (``pgai_agent_kanban/lib/palette.py`` -> up
three levels -> the kanban root), the definition file sits at the same
relative depth: ``<that directory>/config/default_palette.txt``.  This
mirrors the sibling bash resolver's ``BASH_SOURCE``-relative approach in
projects_cfg.sh -- each tree finds its OWN bundled copy of the file.

Public API:
    load_default_palette(palette_path=None) -> tuple[str, ...]
        Ordered tuple of "#RRGGBB" hex color strings, cached after the
        first successful read of a given path.  Raises PaletteFileError
        (naming the path) when the file is missing or contains no valid
        entries -- fail-closed, never a silent empty palette.

Usage::

    from pgai_agent_kanban.lib.palette import load_default_palette

    palette = load_default_palette()
    color = palette[registration_index % len(palette)]
"""

from __future__ import annotations

import functools
import pathlib
import re

__all__ = ["load_default_palette", "PaletteFileError", "DEFAULT_PALETTE_FILE"]

# ---------------------------------------------------------------------------
# Definition file location
# ---------------------------------------------------------------------------

#: Path to the canonical palette definition file, resolved relative to this
#: module's own location so each tree (dev checkout or live install) finds
#: its own bundled copy.  See module docstring "Self-location".
DEFAULT_PALETTE_FILE: pathlib.Path = (
    pathlib.Path(__file__).resolve().parent.parent.parent / "config" / "default_palette.txt"
)

# Parse rule mirrors team/config/default_palette.txt's own documented
# contract: a line is a DATA line only when its first whitespace-delimited
# token matches a six-digit hex color; everything else (the header block,
# blank lines, per-entry hue comments) is a comment and is skipped.
_HEX_RE = re.compile(r"^#[0-9A-Fa-f]{6}$")


class PaletteFileError(RuntimeError):
    """Raised when the palette definition file is missing or malformed.

    Always names the path that was read, so the operator can locate and
    repair the definition file without re-reading this module's source.
    """


# ---------------------------------------------------------------------------
# Loader
# ---------------------------------------------------------------------------


@functools.lru_cache(maxsize=None)
def _load_palette_cached(path_str: str) -> tuple[str, ...]:
    """Read and parse the file at *path_str* into an ordered hex-color tuple.

    Cached per distinct path so repeated calls do not re-read the file from
    disk.  A raised PaletteFileError is never cached -- a call against a
    path that starts (or later starts) resolving successfully is retried
    on every invocation until it succeeds.
    """
    path = pathlib.Path(path_str)
    if not path.is_file():
        raise PaletteFileError(
            f"pgai_agent_kanban.lib.palette: FATAL: canonical palette "
            f"definition file not found: '{path}' -- cannot build the "
            f"default palette"
        )

    hex_colors: list[str] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        # Follows the definition file's own documented "Reading in Python"
        # recipe: split into at most 3 fields, keep the first only when it
        # is a valid #RRGGBB token.
        parts = line.split(None, 2)
        if not parts or not _HEX_RE.match(parts[0]):
            continue
        hex_colors.append(parts[0])

    if not hex_colors:
        raise PaletteFileError(
            f"pgai_agent_kanban.lib.palette: FATAL: canonical palette "
            f"definition file is malformed (no valid #RRGGBB entries "
            f"found): '{path}'"
        )

    return tuple(hex_colors)


def load_default_palette(
    palette_path: pathlib.Path | None = None,
) -> tuple[str, ...]:
    """Return the ordered default palette as a tuple of "#RRGGBB" strings.

    Reads and parses ``team/config/default_palette.txt`` (or its live-
    install counterpart at ``<kanban_root>/config/default_palette.txt``),
    keeping only the hex color from each data line -- matching
    ``PGAI_DEFAULT_PALETTE`` in team/scripts/lib/projects_cfg.sh element
    for element.  Comment lines, blank lines, and the file's own header
    block are skipped per the file's documented parse rule.

    Results are cached (keyed by the resolved path) so repeated calls do
    not re-read the file from disk.

    Args:
        palette_path: Override the definition file location.  Defaults to
            DEFAULT_PALETTE_FILE.  Production callers should not need
            this; it exists so tests can point at a fixture file (for
            example, to exercise the fail-closed raise) without disturbing
            the cache built from the real definition file.

    Returns:
        Ordered tuple of "#RRGGBB" hex color strings; order is significant
        (palette assignment is by index).

    Raises:
        PaletteFileError: When the file is missing or contains no valid
            #RRGGBB entries.  The message names the path that was read.
    """
    path = palette_path if palette_path is not None else DEFAULT_PALETTE_FILE
    return _load_palette_cached(str(path))
