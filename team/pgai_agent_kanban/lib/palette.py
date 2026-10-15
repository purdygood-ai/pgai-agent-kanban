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

    ansi_code(color_name, use_color) -> str
        Python counterpart of team/scripts/lib/script_common.sh's
        ansi_code() -- the single Python-side home for the ANSI SGR escape
        table (black, red, green, yellow, blue, magenta, cyan, white,
        bold, dim, reset). Dashboard renderer scripts that assemble ANSI
        output in an embedded Python heredoc call this instead of hand-
        transcribing the escape table themselves.

    load_semantic_palette(palette_path=None) -> dict[str, str]
        Semantic key -> color-name mapping read from this same file's
        "%<key> <color-name>" section (see team/config/default_palette.txt
        for the documented parse rule).  This is the Python counterpart of
        team/scripts/lib/dashboard_palette.sh's semantic-key reader --
        both read the same on-disk file, which is what the extended
        parity test in team/tests/unit/test_palette_bash_python_parity.py
        proves structurally.

    resolve_semantic_color(key, use_color, palette_path=None) -> str
        Look up a semantic key and return its ANSI escape (via
        ansi_code()), or the empty string when use_color is False.
        Raises PaletteKeyError (naming the key and the palette file) when
        the key is not declared -- fail-loud by name, never a silent
        default color.  See PaletteKeyError's docstring for the dual-
        channel refusal this raises through.

Usage::

    from pgai_agent_kanban.lib.palette import load_default_palette

    palette = load_default_palette()
    color = palette[registration_index % len(palette)]
"""

from __future__ import annotations

import functools
import pathlib
import re

from pgai_agent_kanban.lib.error_contract import format_error_contract

__all__ = [
    "load_default_palette",
    "PaletteFileError",
    "PaletteKeyError",
    "DEFAULT_PALETTE_FILE",
    "ansi_code",
    "load_semantic_palette",
    "resolve_semantic_color",
]

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


class PaletteKeyError(PaletteFileError):
    """Raised when a semantic color key is not declared in the palette file.

    BLOCK-WORTHY (team/SOP-core.md, "Gates and Refusals"): a dashboard
    renderer that asks for an undeclared semantic key and silently falls
    back to a default/empty color would mis-render state an operator
    relies on (a HALT indicator, a blocked-task color) without any
    signal that the color is wrong -- the exact silent-miscolor failure
    mode the single-source semantic palette exists to prevent. Refusing
    here, naming the key and the file, is an invariant-by-design stop.

    The exception message carries the full dual-channel house shape
    (error/message/remedy) produced by
    ``pgai_agent_kanban.lib.error_contract.format_error_contract`` so a
    caller that lets this propagate (or prints ``str(exc)``) still speaks
    both channels, matching the sibling bash refusal in
    team/scripts/lib/dashboard_palette.sh (CENSUS-ANCHOR:
    dashboard-palette-key-missing-01 there; the same key/message/remedy
    triple on this side).
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


# ---------------------------------------------------------------------------
# ANSI SGR escape table (Python counterpart of script_common.sh's ansi_code)
# ---------------------------------------------------------------------------

#: The color/style names both the bash primitive (ansi_code() in
#: team/scripts/lib/script_common.sh) and this Python primitive recognize.
#: Values are the exact SGR escape sequences already in use throughout the
#: dashboard renderers this module's callers replace -- this table is a
#: byte-for-byte transcription of script_common.sh's case statement, so
#: converting a renderer to call ansi_code() here changes no rendered byte.
_ANSI_CODES: dict[str, str] = {
    "black": "\033[0;30m",
    "red": "\033[0;31m",
    "green": "\033[0;32m",
    "yellow": "\033[0;33m",
    "blue": "\033[0;34m",
    "magenta": "\033[0;35m",
    "cyan": "\033[0;36m",
    "white": "\033[0;37m",
    "bold": "\033[1m",
    "dim": "\033[2m",
    "reset": "\033[0m",
    # Compound style: gray/white background + black foreground -- the one
    # non-plain-foreground code a dashboard renderer needs (attention.sh's
    # highlight bar). Mirrors script_common.sh's "gray_bg" case arm.
    "gray_bg": "\033[47;30m",
    # Legacy compound/variant codes carried over byte-for-byte from
    # pre-existing renderer call sites (cron-tail.sh) rather than
    # normalized away -- mirrors script_common.sh's matching case arms.
    "bold_cyan": "\033[1;36m",
    "yellow_bright": "\033[33m",
    "green_bright": "\033[32m",
}


def ansi_code(color_name: str, use_color: bool) -> str:
    """Return the ANSI SGR escape for *color_name*, or "" when disabled.

    Python counterpart of ``ansi_code()`` in
    ``team/scripts/lib/script_common.sh``.  Dashboard renderer scripts that
    assemble ANSI-colored output from an embedded Python heredoc call this
    instead of hand-transcribing the escape-code table themselves -- the
    exact single-source pattern the bash side already follows for callers
    that go through the shared bash function.

    Args:
        color_name: One of the eleven recognized names (see module
            docstring). An unrecognized name returns "" rather than
            raising -- this mirrors the bash primitive's own contract
            (unknown color -> empty string, not a refusal), because an
            unrecognized *color name* is a different failure class than
            an unrecognized *semantic key* (see ``resolve_semantic_color``,
            which does refuse loudly for the latter).
        use_color: When False, always returns "" regardless of color_name
            (matches ansi_code()'s ``USE_COLOR`` gate).

    Returns:
        The escape sequence, or "" when use_color is False or color_name
        is not recognized.
    """
    if not use_color:
        return ""
    return _ANSI_CODES.get(color_name, "")


# ---------------------------------------------------------------------------
# Semantic dashboard color keys
# ---------------------------------------------------------------------------

#: Sigil marking a semantic-key data line in the shared definition file --
#: matches the parse rule documented in team/config/default_palette.txt's
#: own "Semantic dashboard color keys" section. Mirrors the bash reader's
#: `[[ "$line" == %* ]]` check in team/scripts/lib/dashboard_palette.sh.
_SEMANTIC_KEY_LINE_RE = re.compile(r"^%(\S+)\s+(\S+)")


@functools.lru_cache(maxsize=None)
def _load_semantic_palette_cached(path_str: str) -> tuple[tuple[str, str], ...]:
    """Read and parse the semantic-key section of *path_str*.

    Cached per distinct path, mirroring ``_load_palette_cached``. Returns
    an ordered tuple of (key, color_name) pairs rather than a dict so the
    result is hashable for ``lru_cache``; ``load_semantic_palette`` below
    converts it to a dict for callers.
    """
    path = pathlib.Path(path_str)
    if not path.is_file():
        raise PaletteFileError(
            f"pgai_agent_kanban.lib.palette: FATAL: canonical palette "
            f"definition file not found: '{path}' -- cannot resolve "
            f"semantic dashboard color keys"
        )

    entries: list[tuple[str, str]] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        match = _SEMANTIC_KEY_LINE_RE.match(line)
        if not match:
            continue
        entries.append((match.group(1), match.group(2)))

    return tuple(entries)


def load_semantic_palette(
    palette_path: pathlib.Path | None = None,
) -> dict[str, str]:
    """Return the semantic key -> color-name mapping as a dict.

    Reads the "%<key> <color-name>" section of
    ``team/config/default_palette.txt`` (see that file's own documented
    parse rule).  This is the Python counterpart of
    ``team/scripts/lib/dashboard_palette.sh``'s semantic-key reader; both
    read the same on-disk file, which is what
    ``team/tests/unit/test_palette_bash_python_parity.py`` proves
    structurally rather than by inspection.

    Args:
        palette_path: Override the definition file location. Defaults to
            DEFAULT_PALETTE_FILE. Production callers should not need this;
            it exists so tests can point at a fixture file.

    Returns:
        Dict mapping each declared semantic key to its color-name value.
        Does NOT raise when the map is empty -- an empty semantic section
        is a valid (if unusual) state; the per-key refusal in
        ``resolve_semantic_color`` is what fails loud, not this loader.
        Raises PaletteFileError when the file itself is missing.
    """
    path = palette_path if palette_path is not None else DEFAULT_PALETTE_FILE
    return dict(_load_semantic_palette_cached(str(path)))


def resolve_semantic_color(
    key: str,
    use_color: bool,
    palette_path: pathlib.Path | None = None,
) -> str:
    """Resolve a semantic key to its ANSI escape, or "" when use_color=False.

    Looks up *key* in the semantic palette (``load_semantic_palette``) and
    returns ``ansi_code(color_name, use_color)`` for the declared color
    name.

    Args:
        key: The semantic key to resolve (e.g. "status_running",
            "halt_active").
        use_color: When False, returns "" without needing *key* to be
            declared -- a NO_COLOR render never needs the color table, so
            it never pays for (or can be broken by) a missing key. This
            mirrors the bash side's own NO_COLOR short-circuit convention
            elsewhere in the dashboard scripts.
        palette_path: Override the definition file location; see
            ``load_semantic_palette``.

    Returns:
        The ANSI escape sequence, or "" when use_color is False.

    Raises:
        PaletteKeyError: When use_color is True and *key* is not declared
            in the palette file.  The exception message carries the full
            dual-channel house shape (error/message/remedy) naming the key
            and the file that was read -- see ``PaletteKeyError``'s
            docstring.
    """
    if not use_color:
        return ""

    semantic_map = load_semantic_palette(palette_path)
    if key not in semantic_map:
        path = palette_path if palette_path is not None else DEFAULT_PALETTE_FILE
        raise PaletteKeyError(
            format_error_contract(
                "dashboard-palette.key-missing",
                f"no semantic color key '{key}' is declared in {path}",
                f"add a %{key} entry to team/config/default_palette.txt "
                f"naming a real color and a short description, following "
                f"the same three-column shape as the existing "
                f"'%halt_active red HALT sentinel active (full stop)' "
                f"line, then re-render",
            )
        )

    return ansi_code(semantic_map[key], use_color)
