"""
error_contract.py — Shared dual-channel error-contract emitter.

Every fail-loud site — HALT reasons, gate refusals, hook failures, blocker
narrations, release-state events — speaks both channels through this one
primitive: a stable machine key a tool can grep and match against the
resolution matrix, and a plain-language message plus a runnable remedy a
stranger can act on without the internal ledger open.

House shape (fixed field order, colon-aligned, exactly three lines)::

    error:   <machine key>
    message: <what stopped and why, in a stranger's words>
    remedy:  <what to do next, verbatim-runnable where possible>

This module provides the primitive only. It does not itself convert any
existing fail-loud site — call sites are converted by later tickets in this
release; this module is the shared implementation they all import. The
sibling shell implementation (team/scripts/lib/error_contract.sh) covers
bash fail-loud sites; this module covers Python ones. Both emit the
byte-identical house shape so a grep for "^error:" behaves the same
regardless of which language produced the line.

Public API
----------
    format_error_contract(error, message, remedy) -> str
        Return the three-line house-shape block as a string. Use this when
        the caller needs the formatted text embedded elsewhere — a HALT
        reason, a release-state event body, a return value — rather than
        printed directly.

    emit_error_contract(error, message, remedy, *, file=sys.stderr) -> None
        Print the three-line house-shape block to *file* (stderr by
        default). Use this at the point of failure, the same place a bare
        ``print(..., file=sys.stderr)`` fail-loud line would go today.

Both functions require all three fields to be non-empty (after stripping
whitespace) — a missing field defeats the point of a dual-channel contract,
so a blank error, message, or remedy raises ``ValueError`` rather than
silently emitting a two-field or one-field block.
"""

from __future__ import annotations

import sys
from typing import Optional, TextIO

# ---------------------------------------------------------------------------
# House-shape field labels (colon-aligned to match the requirements doc's
# documented shape: "error:", "message:", "remedy:" each padded so the
# value column starts at the same position).
# ---------------------------------------------------------------------------
_ERROR_LABEL = "error:   "
_MESSAGE_LABEL = "message: "
_REMEDY_LABEL = "remedy:  "


def format_error_contract(error: str, message: str, remedy: str) -> str:
    """Return the three-line house-shape error-contract block.

    Args:
        error: Stable machine key (gate/trigger/check id). Must remain
            greppable and resolution-matrix-addressable — callers pass the
            same literal id their documentation and resolution matrix use.
        message: Plain-language explanation of what stopped and why,
            written so a stranger with no internal ledger open understands
            it.
        remedy: What to do next, verbatim-runnable where possible (per the
            prescriptive-remedy standard in docs/coding-standards.md
            "Gates prescribe, not just diagnose").

    Returns:
        The three-line block, newline-separated, with no trailing newline.
        Field order is fixed: error, then message, then remedy.

    Raises:
        ValueError: if any of error, message, remedy is empty or
            whitespace-only.

    Example:
        >>> print(format_error_contract(
        ...     "gate.rc-branch-mismatch",
        ...     "the release stopped because the version-bump step expected "
        ...     "the code checkout to be on the release branch, and it wasn't",
        ...     "git checkout rc/v1.2.3 && re-run the release script",
        ... ))
        error:   gate.rc-branch-mismatch
        message: the release stopped because the version-bump step expected the code checkout to be on the release branch, and it wasn't
        remedy:  git checkout rc/v1.2.3 && re-run the release script
    """
    _validate_fields(error=error, message=message, remedy=remedy)
    return (
        f"{_ERROR_LABEL}{error}\n"
        f"{_MESSAGE_LABEL}{message}\n"
        f"{_REMEDY_LABEL}{remedy}"
    )


def emit_error_contract(
    error: str,
    message: str,
    remedy: str,
    *,
    file: Optional[TextIO] = None,
) -> None:
    """Print the three-line house-shape error-contract block to *file*.

    Args:
        error: Stable machine key. See :func:`format_error_contract`.
        message: Plain-language message. See :func:`format_error_contract`.
        remedy: Runnable remedy. See :func:`format_error_contract`.
        file: Stream to print to. Defaults to ``sys.stderr`` — the same
            channel a bare fail-loud ``print(..., file=sys.stderr)`` line
            would use. Resolved at call time (not bound as a mutable
            default) so callers relying on a redirected ``sys.stderr``
            (test capture, a reassigned stream) see the redirection take
            effect.

    Raises:
        ValueError: propagated from :func:`format_error_contract` if any
            field is empty or whitespace-only.
    """
    if file is None:
        file = sys.stderr
    print(format_error_contract(error, message, remedy), file=file)


def _validate_fields(*, error: str, message: str, remedy: str) -> None:
    """Raise ValueError naming every blank field, or return silently.

    Not itself routed through the house shape (that would be a
    tautological self-reference for a validation failure over the very
    fields the contract requires); this is a plain diagnostic instead.
    """
    blank_fields = [
        name
        for name, value in (
            ("error", error),
            ("message", message),
            ("remedy", remedy),
        )
        if not value or not value.strip()
    ]
    if blank_fields:
        raise ValueError(
            "error_contract: all three fields (error, message, remedy) are "
            "required and must be non-empty; blank/missing: "
            f"{', '.join(blank_fields)}"
        )
