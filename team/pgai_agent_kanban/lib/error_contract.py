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

    emit_error_contract(error, message, remedy, *, file=sys.stderr,
                        project=None) -> None
        Print the three-line house-shape block to *file* (stderr by
        default). Use this at the point of failure, the same place a bare
        ``print(..., file=sys.stderr)`` fail-loud line would go today.

    parse_error_contract(text) -> dict | None
        Read the house shape back out of captured text -- a subprocess's
        stderr, a Refused exception's message -- and return
        ``{"error", "message", "remedy"}``, or None when *text* carries no
        such block. The inverse of format_error_contract; the one parser
        every surface that re-emits a refusal as structured data (the
        operator API's JSON refusal bodies) shares, so a change to the
        house shape is made in this module and nowhere else.

    dual_channel_from_text(text, fallback_error_key, fallback_remedy) -> dict
        parse_error_contract with a guaranteed answer: the parsed triple
        when *text* carries the house shape, otherwise *text* itself as
        the message under *fallback_error_key* with *fallback_remedy* as
        the next step. The one renderer a CLI and its REST twin share so
        that the same refusal prints the same three fields on both -- a
        surface that formats str(exc) on its own has no remedy to print
        the moment a raiser stops producing the house shape.

Both functions require all three fields to be non-empty (after stripping
whitespace) — a missing field defeats the point of a dual-channel contract,
so a blank error, message, or remedy raises ``ValueError`` rather than
silently emitting a two-field or one-field block.

Refusal-ledger recording
-------------------------
Every call to ``emit_error_contract`` also records the refusal as the
most-recent-refusal entry for the project in scope
(``pgai_agent_kanban.lib.refusal_ledger``) — wired into this ONE shared
function rather than into each call site, so a refusal converted to this
emitter next year is queryable for free, with no per-site plumbing. The
sibling shell emitter (``team/scripts/lib/error_contract.sh``) records the
same way through the same Python module, so both languages produce the
exact same on-disk record shape.

  - Project scope: the optional ``project`` keyword argument, falling back
    to the ambient ``PGAI_PROJECT_NAME`` environment variable (the same
    resolution order every project-scoped helper in this framework uses).
    When neither is set — a kanban-root-level refusal, or one that fires
    before a project is resolved — recording is skipped entirely: it goes
    NOWHERE, never guesses a project, and never writes into an arbitrary
    project's directory.
  - Surface: derived automatically from the immediate caller's stack frame
    — no call site needs to pass this.
  - Recording is best-effort: it never raises, and it never changes the
    block this function prints. A ledger-write failure (an unresolvable
    kanban root, an unwritable path) is swallowed silently, exactly like a
    project-less call. The one exception to silence is a project the
    root's projects.cfg does not register: the ledger writes nothing and
    narrates one ``refusal_ledger: ...`` line on stderr. Recording runs
    after the fields validate and BEFORE the block is printed, so that line
    precedes the ``error:`` label -- :func:`parse_error_contract` ignores
    text before the label, so the block's remedy is never extended by it.
"""

from __future__ import annotations

import inspect
import os
import re
import sys
from typing import Optional, TextIO

# ---------------------------------------------------------------------------
# House-shape field labels (colon-aligned: "error:", "message:", "remedy:"
# each padded so the value column starts at the same position).
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
    project: Optional[str] = None,
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
        project: Project to record the refusal against in the
            most-recent-refusal ledger (see module docstring, "Refusal-
            ledger recording"). Falls back to the ambient
            ``PGAI_PROJECT_NAME`` environment variable when omitted;
            recording is skipped entirely when neither is set.

    Raises:
        ValueError: propagated from :func:`format_error_contract` if any
            field is empty or whitespace-only.
    """
    if file is None:
        file = sys.stderr
    block = format_error_contract(error, message, remedy)
    # Recorded before printing: any ledger narration then precedes the
    # block rather than trailing its remedy (see "Refusal-ledger recording").
    _record_refusal_best_effort(error, message, remedy, project=project)
    print(block, file=file)


# Liberal reader for the house shape: tolerates any run of whitespace
# after each label and between the three lines, and takes the remedy as
# everything after its label to the end of the text -- a remedy is the
# last thing a refusal site prints before it exits, so the trailing text is
# the remedy, newline included. Postel's law for a parser: liberal in what
# it accepts (the writer above is strict in what it produces).
_ERROR_CONTRACT_BLOCK_RE = re.compile(
    r"error:\s*(?P<error>.*?)\s*\n\s*message:\s*(?P<message>.*?)\s*\n\s*remedy:\s*(?P<remedy>.*)\Z",
    re.DOTALL,
)


def parse_error_contract(text: str) -> Optional[dict]:
    """Return the ``{"error", "message", "remedy"}`` triple embedded in *text*.

    Reads the three-line house shape :func:`format_error_contract` produces
    back out of captured output -- a subprocess's stderr, a ``Refused``
    exception's message. Text before the ``error:`` label (an earlier
    narration line, a script's own ``[tag]`` prefix line) is ignored; the
    three values are returned stripped of surrounding whitespace.

    Args:
        text: Captured text that may contain one house-shape block.

    Returns:
        A dict with exactly the keys ``error``, ``message`` and ``remedy``
        when the block is present; ``None`` when *text* is empty or carries
        no ``error:``/``message:``/``remedy:`` triple -- the caller decides
        what to do with a plain-text refusal (typically wrap it under a
        fallback key), this function never invents one.

    Example:
        >>> parse_error_contract(
        ...     "release-locks.sh: resolving...\n"
        ...     "error:   release_locks.not_found\n"
        ...     "message: no lock matches 'nope'.\n"
        ...     "remedy:  list the locks first, then re-run.\n"
        ... )
        {'error': 'release_locks.not_found', 'message': "no lock matches 'nope'.", 'remedy': 'list the locks first, then re-run.'}
    """
    if not text:
        return None
    m = _ERROR_CONTRACT_BLOCK_RE.search(text)
    if not m:
        return None
    return {
        "error": m.group("error").strip(),
        "message": m.group("message").strip(),
        "remedy": m.group("remedy").strip(),
    }


def dual_channel_from_text(
    text: str, fallback_error_key: str, fallback_remedy: str
) -> dict:
    """Return an ``{"error", "message", "remedy"}`` triple for *text*, always.

    The shared refusal renderer for a CLI verb and its REST twin: both hand
    the same refusal text (typically ``str(exc)`` of a ``Refused``) to this
    one function, so the three fields each surface shows are produced by
    the same code path and cannot drift -- the REST body's ``remedy`` value
    and the CLI's ``remedy:`` line are the same string by construction.

    Args:
        text: Captured refusal text that may carry one house-shape block.
        fallback_error_key: Stable machine key used when *text* carries no
            house-shape block.
        fallback_remedy: Runnable next step used in the same case; must be
            non-empty so the triple never loses its remedy channel.

    Returns:
        The parsed triple when *text* carries the house shape (see
        :func:`parse_error_contract`); otherwise ``{"error":
        fallback_error_key, "message": text, "remedy": fallback_remedy}``.
    """
    parsed = parse_error_contract(text)
    if parsed is not None:
        return parsed
    return {"error": fallback_error_key, "message": text, "remedy": fallback_remedy}


def _record_refusal_best_effort(
    error: str, message: str, remedy: str, *, project: Optional[str]
) -> None:
    """Best-effort refusal-ledger recording for emit_error_contract.

    Resolves the caller's surface (file:function of the immediate caller of
    emit_error_contract, one frame up from this helper) and delegates to
    pgai_agent_kanban.lib.refusal_ledger.record_refusal(), which never
    raises and is itself a no-op when no project is in scope. Any error
    here (including the refusal-ledger module being unavailable) is
    swallowed — recording is diagnostics, never a gate.
    """
    try:
        from pgai_agent_kanban.lib.refusal_ledger import record_refusal

        resolved_project = project or os.environ.get("PGAI_PROJECT_NAME")
        surface = _caller_surface()
        record_refusal(
            error,
            message,
            remedy,
            project=resolved_project,
            surface=surface,
        )
    except Exception:  # noqa: BLE001 - recording is diagnostics, never a gate
        pass


def _caller_surface() -> str:
    """Return "<basename(file)>:<function>" for emit_error_contract's
    immediate caller (the actual refusal site).

    Stack depth from here: [0] this function, [1] _record_refusal_best_effort
    (who called this), [2] emit_error_contract (who called that), [3] the
    real refusal site that called emit_error_contract.
    """
    try:
        frame = inspect.stack()[3]
        name = os.path.basename(frame.filename)
        function = frame.function
        if function and function != "<module>":
            return f"{name}:{function}"
        return name
    except Exception:  # noqa: BLE001 - surface is best-effort metadata
        return "unknown"


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
