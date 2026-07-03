"""
_rejected.py — Sidecar parser for the unified rejected/ directory.

Each quarantined file <F> placed in a project's rejected/ directory may have a
companion sidecar file <F>.reason in the same directory.  This module provides
read_reason_sidecar(), which parses that file and returns a typed dict.

Sidecar file format
-------------------
Key=value lines, one per line, newline-terminated.  Single-line values only —
embed a literal newline as the two-character sequence ``\n``.

Known keys:

    original_type  — intake queue the file came from: bugs | priority | requirements
    original_dir   — absolute path of the source directory before rejection
    rejected_at    — ISO 8601 UTC timestamp, e.g. 2026-05-28T14:00:00Z
    reason         — single-line free-text description of why the file was rejected
    retry_count    — integer; number of times the file was seen before quarantine

Unknown keys are silently ignored (forward compatibility).
Missing keys return None.

Usage::

    from team.pgai_agent_kanban.dashboard._rejected import read_reason_sidecar

    info = read_reason_sidecar("/path/to/rejected/BUG-0001-foo.md.reason")
    print(info["reason"])          # e.g. "malformed filename"
    print(info["retry_count"])     # e.g. 3, or None if absent
"""

from __future__ import annotations

import pathlib
from typing import Optional


# ---------------------------------------------------------------------------
# Public type alias for the sidecar dict returned by read_reason_sidecar.
# Keys are always present; values are None when the key was absent in the file.
# ---------------------------------------------------------------------------

SIDECAR_KEYS = frozenset(
    {"original_type", "original_dir", "rejected_at", "reason", "retry_count"}
)


def read_reason_sidecar(
    sidecar_path: "str | pathlib.Path",
) -> dict[str, Optional[str]]:
    """Parse a .reason sidecar file and return a dict with all known keys.

    Args:
        sidecar_path: Path to the ``.reason`` file co-located with the
            quarantined file inside the project's ``rejected/`` directory.

    Returns:
        A dict with the following keys (all always present):

        - ``original_type`` (str | None): intake queue (bugs|priority|requirements)
        - ``original_dir``  (str | None): absolute source directory path
        - ``rejected_at``   (str | None): ISO 8601 UTC rejection timestamp
        - ``reason``        (str | None): free-text rejection reason
        - ``retry_count``   (str | None): raw integer string; caller may cast with
          ``int(info["retry_count"])`` after a None check.

        All values are returned as strings (or None when absent).  The caller is
        responsible for any type conversion beyond string.

    Notes:
        - Lines that do not contain ``=`` are silently skipped.
        - Unknown keys are silently ignored (forward compatibility).
        - The embedded newline escape sequence ``\\n`` is NOT decoded here;
          callers that need to preserve literal newlines in ``reason`` must
          handle that substitution themselves.
        - OSError (e.g. file not found) is silently caught; the function returns
          an all-None dict so callers do not need a try/except at every call site.
    """
    result: dict[str, Optional[str]] = {key: None for key in SIDECAR_KEYS}

    path = pathlib.Path(sidecar_path)
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return result

    for raw_line in text.splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#"):
            # Blank lines and comment lines (starting with #) are ignored.
            continue
        if "=" not in line:
            # Not a key=value line; skip silently.
            continue
        key, _, value = line.partition("=")
        key = key.strip()
        value = value.strip()
        if key in SIDECAR_KEYS:
            result[key] = value if value else None

    return result


def sidecar_path_for(quarantined_file: "str | pathlib.Path") -> pathlib.Path:
    """Return the expected .reason sidecar path for a quarantined file.

    Convenience helper: given ``/path/to/rejected/BUG-0001-foo.md``, returns
    ``/path/to/rejected/BUG-0001-foo.md.reason``.

    Args:
        quarantined_file: Path to the quarantined file inside rejected/.

    Returns:
        pathlib.Path with ``.reason`` appended to the quarantined file path.
    """
    p = pathlib.Path(quarantined_file)
    return p.parent / (p.name + ".reason")
