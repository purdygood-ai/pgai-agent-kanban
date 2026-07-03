#!/usr/bin/env python3
"""
token.py — HALT-AFTER token parser.

Reads a HALT-AFTER file's content string and returns the canonical lowercase
event token, or None when the content is invalid (unknown token).

Rules:
  - Parse with .strip().lower()
  - Empty string (after strip) defaults to 'rc'
  - Supported tokens: rc, pm, coder, writer, tester, cm
  - rc:vX.Y.Z is also supported — the version suffix captures the arm-time RC
    version.  The suffix pattern is: rc:[a-zA-Z0-9._+-]+
  - Anything else: log a warning and return None (treat as absent)

The caller is responsible for logging or surfacing the None case; this module
only parses.
"""

import logging
import re
import sys

logger = logging.getLogger(__name__)

SUPPORTED_TOKENS = frozenset({"rc", "pm", "coder", "writer", "tester", "cm"})

# Pattern matching a versioned rc token, e.g. rc:v0.40.0 or rc:v1.2.3-alpha
_RC_VERSIONED_RE = re.compile(r"^rc:[a-zA-Z0-9._+\-]+$")


def parse_token(content: str) -> "str | None":
    """Parse a HALT-AFTER file's content into a canonical event token.

    Applies ``.strip()`` to *content* (preserving original case for the
    version suffix, but lowercasing the ``rc`` prefix for normalisation).
    An empty result defaults to ``'rc'``.  Returns a supported lowercase token
    string on success, or ``None`` when the token is not in the supported set.

    In addition to the base supported tokens, this function accepts versioned
    rc tokens of the form ``rc:vX.Y.Z`` (or any ``rc:<suffix>`` where the
    suffix matches ``[a-zA-Z0-9._+-]+``).  The versioned token is returned
    as-is (after stripping surrounding whitespace) with the ``rc:`` prefix
    lowercased.  For example, ``"rc:v0.40.0"`` → ``"rc:v0.40.0"``.

    A ``None`` return must be treated as if the HALT-AFTER file were absent —
    the caller must NOT silently halt on an invalid token.

    Args:
        content: Raw file content of the HALT-AFTER sentinel file.

    Returns:
        A lowercase token string from the supported set (or a versioned rc
        token ``"rc:vX.Y.Z"``), or ``None`` for an invalid / unrecognised
        token.

    Examples:
        >>> parse_token('')
        'rc'
        >>> parse_token('  RC\\n')
        'rc'
        >>> parse_token('\\tcoder\\n')
        'coder'
        >>> parse_token('rc:v0.40.0')
        'rc:v0.40.0'
        >>> parse_token('deploy') is None
        True
    """
    stripped = content.strip()

    # Empty file defaults to 'rc' — broadest drain, most intuitive default
    if not stripped:
        return "rc"

    # Check for versioned rc token BEFORE lowercasing so the version suffix
    # retains its original casing (e.g. "v0.40.0" not "v0.40.0" — same here,
    # but matters if callers use mixed-case tags).
    # Lower only the "rc:" prefix for the match check.
    lower_prefix = stripped[:3].lower() if len(stripped) >= 3 else stripped.lower()
    if lower_prefix == "rc:" and _RC_VERSIONED_RE.match("rc:" + stripped[3:]):
        # Return canonical form: "rc:" prefix lowercased, suffix as-is
        return "rc:" + stripped[3:]

    normalised = stripped.lower()

    if normalised in SUPPORTED_TOKENS:
        return normalised

    logger.warning(
        "HALT-AFTER: invalid token %r (supported: %s, or rc:vX.Y.Z) — treating as absent",
        normalised,
        ", ".join(sorted(SUPPORTED_TOKENS)),
    )
    return None
