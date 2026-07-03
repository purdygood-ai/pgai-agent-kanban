"""team/pm-agent/lib/semver.py
Semantic version helpers for the pm-agent.

Pure Python, no external dependencies. Python 3.10 compatible.
Comparison is done via tuple arithmetic on (major, minor, patch) ints so
cross-decade components work correctly (e.g. 0.9.x < 0.10.x).

Public API
----------
parse(version_str) -> tuple[int, int, int]
    Parse a version string, stripping an optional leading "v" prefix.
    Raises ValueError if the string is not in X.Y.Z form.

compare(a, b) -> int
    Returns -1 if a < b, 0 if a == b, 1 if a > b.
    Accepts either raw version strings or pre-parsed tuples.

lt(a, b) -> bool   True if a < b
le(a, b) -> bool   True if a <= b
gt(a, b) -> bool   True if a > b
ge(a, b) -> bool   True if a >= b
eq(a, b) -> bool   True if a == b

from_filename(filename) -> str
    Returns the first ``v\\d+\\.\\d+\\.\\d+`` token found in the filename
    (basename only), or an empty string if none is found.
"""

from __future__ import annotations

import re
from typing import Union

# Type alias: callers may pass either a raw string or a pre-parsed tuple.
_VersionLike = Union[str, tuple[int, int, int]]

# Pattern used by from_filename and parse.
_VERSION_RE = re.compile(r"v(\d+)\.(\d+)\.(\d+)")
_PLAIN_RE = re.compile(r"^(\d+)\.(\d+)\.(\d+)$")


def parse(version_str: str) -> tuple[int, int, int]:
    """Parse a version string into a (major, minor, patch) int tuple.

    Accepts versions with or without a leading ``v``/``V`` prefix.

    Parameters
    ----------
    version_str:
        Version string such as ``"v0.17.1"`` or ``"0.17.1"``.

    Returns
    -------
    tuple[int, int, int]
        ``(major, minor, patch)``

    Raises
    ------
    ValueError
        If *version_str* cannot be parsed.
    """
    s = version_str.strip()
    # Strip optional 'v' or 'V' prefix.
    if s and s[0] in ("v", "V"):
        s = s[1:]
    m = _PLAIN_RE.match(s)
    if not m:
        raise ValueError(
            f"Cannot parse version {version_str!r}: expected X.Y.Z (with optional v prefix)"
        )
    return (int(m.group(1)), int(m.group(2)), int(m.group(3)))


def _as_tuple(v: _VersionLike) -> tuple[int, int, int]:
    """Return *v* as a parsed tuple, parsing it first if it is a string."""
    if isinstance(v, tuple):
        return v
    return parse(v)


def compare(a: _VersionLike, b: _VersionLike) -> int:
    """Compare two versions.

    Returns
    -------
    int
        ``-1`` if *a* < *b*, ``0`` if equal, ``1`` if *a* > *b*.
    """
    ta = _as_tuple(a)
    tb = _as_tuple(b)
    if ta < tb:
        return -1
    if ta > tb:
        return 1
    return 0


def lt(a: _VersionLike, b: _VersionLike) -> bool:
    """Return True if *a* < *b*."""
    return compare(a, b) == -1


def le(a: _VersionLike, b: _VersionLike) -> bool:
    """Return True if *a* <= *b*."""
    return compare(a, b) <= 0


def gt(a: _VersionLike, b: _VersionLike) -> bool:
    """Return True if *a* > *b*."""
    return compare(a, b) == 1


def ge(a: _VersionLike, b: _VersionLike) -> bool:
    """Return True if *a* >= *b*."""
    return compare(a, b) >= 0


def eq(a: _VersionLike, b: _VersionLike) -> bool:
    """Return True if *a* == *b*."""
    return compare(a, b) == 0


def from_filename(filename: str) -> str:
    """Extract the first ``v\\d+\\.\\d+\\.\\d+`` token from *filename*.

    Only the basename of *filename* is examined so that directory path
    components do not produce spurious matches.

    Parameters
    ----------
    filename:
        A filename (or full path).  Only the basename is searched.

    Returns
    -------
    str
        The first matching version token (e.g. ``"v0.17.0"``), or an
        empty string if no match is found.
    """
    import os
    base = os.path.basename(filename)
    m = _VERSION_RE.search(base)
    if not m:
        return ""
    # Reconstruct the full "vX.Y.Z" token from the captured groups so the
    # return value is always consistently prefixed with "v".
    return f"v{m.group(1)}.{m.group(2)}.{m.group(3)}"
