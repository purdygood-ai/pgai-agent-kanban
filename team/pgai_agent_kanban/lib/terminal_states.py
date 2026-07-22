"""
terminal_states.py — Canonical terminal-state vocabulary for intake and task items.

This module is the single definition point for the terminal-state string vocabulary
used across delete, close, and selection code.  All consumers import from here;
no second inline list may appear anywhere in those code paths.

Canonical terminal states (lowercase-normalized forms):
    done        — item completed successfully
    wont-do     — item intentionally not addressed

For agent TASKS the states are written in uppercase ("DONE", "WONT-DO").
For INTAKE items (bugs, priority, requirements) they are written in lowercase
("done", "wont-do").  Both forms normalize to the same canonical value after
``normalize()`` is applied.

Public API:
    normalize(state)          → str: strip + lowercase; stable canonical form
    is_terminal(state)        → bool: True when the normalized state is terminal

an earlier defect (selection filter ticket 5) may import ``is_terminal`` directly:
    from pgai_agent_kanban.lib.terminal_states import is_terminal
"""

from __future__ import annotations

# ---------------------------------------------------------------------------
# Canonical terminal-state vocabulary (normalized, lowercase)
# ---------------------------------------------------------------------------

#: The set of normalized terminal state strings.
#: A state is terminal when an item is definitively resolved and no further
#: agent action is expected.  Superseded is NOT terminal — it is a disposition
#: label, not a completion state used by the delete guard.
TERMINAL_STATES: frozenset[str] = frozenset({"done", "wont-do"})


# ---------------------------------------------------------------------------
# Public helpers
# ---------------------------------------------------------------------------


def normalize(state: str) -> str:
    """Return the canonical form of a state string.

    Strips surrounding whitespace and lowercases the value.  Handles both
    uppercase task forms ("DONE", "WONT-DO") and lowercase intake forms
    ("done", "wont-do"), as well as any historical variant spellings that
    appear in on-disk files.

    Args:
        state: Raw state string as read from a status/status.md field.

    Returns:
        Normalized (stripped, lowercased) state string.

    Examples:
        >>> normalize("DONE")
        'done'
        >>> normalize("  WONT-DO  ")
        'wont-do'
        >>> normalize("wont-do")
        'wont-do'
    """
    return state.strip().lower()


def is_terminal(state: str) -> bool:
    """Return True when the state is a terminal state.

    A terminal state means the item is definitively resolved ("done") or
    intentionally not addressed ("wont-do").  The delete guard uses this
    predicate: items in terminal states may be deleted without --force;
    items in non-terminal states (open, running, working, blocked, etc.)
    are refused.

    Accepts both uppercase task forms and lowercase intake forms — both are
    normalized before comparison.

    Args:
        state: Raw state string as read from a status/status.md field.

    Returns:
        True when the normalized state is "done" or "wont-do".

    Examples:
        >>> is_terminal("done")
        True
        >>> is_terminal("DONE")
        True
        >>> is_terminal("wont-do")
        True
        >>> is_terminal("WONT-DO")
        True
        >>> is_terminal("running")
        False
        >>> is_terminal("open")
        False
    """
    return normalize(state) in TERMINAL_STATES
