"""
terminal_states.py — Canonical terminal-state vocabulary for intake and task items.

This module is the single definition point for the terminal-state string vocabulary
used across delete, close, and selection code.  All consumers import from here;
no second inline list may appear anywhere in those code paths.

Canonical terminal states (lowercase-normalized forms) -- the closed states
an item can be closed to, and the only ones close.sh / ops.write.close_item
accept:
    done        — item completed successfully
    wont-do     — item intentionally not addressed

Legacy alias, read but never written:
    superseded  — read as ``wont-do``.  It is not a kanban state; an item
                  that another item subsumed closes as ``wont-do`` with a
                  note.  Existing files that still carry it are not
                  rewritten, so every reader maps it through
                  ``canonical_state()``.

For agent TASKS the states are written in uppercase ("DONE", "WONT-DO").
For INTAKE items (bugs, priority, requirements) they are written in lowercase
("done", "wont-do").  Both forms normalize to the same canonical value after
``normalize()`` is applied.

Public API:
    CLOSED_STATES             → tuple: the closed states, in display order
    LEGACY_STATE_ALIASES      → mapping: retired state -> the state it reads as
    TERMINAL_STATES           → frozenset of CLOSED_STATES
    normalize(state)          → str: strip + lowercase; stable canonical form
    canonical_state(state)    → str: normalize, then map a legacy alias
    is_terminal(state)        → bool: True when the canonical state is terminal

Callers implementing selection filtering may import ``is_terminal`` directly:
    from pgai_agent_kanban.lib.terminal_states import is_terminal

Bash readers, which cannot import, read the same list from the command line:
    python3 -m pgai_agent_kanban.lib.terminal_states closed-map
prints one ``<state> <closed state it reads as>`` line per closed state and
per legacy alias (``done done``, ``wont-do wont-do``, ``superseded wont-do``)
and exits 0.  Any other argument prints usage to stderr and exits 2.
"""

from __future__ import annotations

import sys
from types import MappingProxyType
from typing import Mapping, Sequence

# ---------------------------------------------------------------------------
# Canonical terminal-state vocabulary (normalized, lowercase)
# ---------------------------------------------------------------------------

#: The closed states, in display order: the states close.sh /
#: ops.write.close_item accept and every reader treats as closed.  This is
#: the one list; readers import it rather than spelling their own.
CLOSED_STATES: tuple[str, ...] = ("done", "wont-do")

#: Retired state values still found in existing files, mapped to the closed
#: state each reads as.  Never written: close_item refuses them.
LEGACY_STATE_ALIASES: Mapping[str, str] = MappingProxyType({"superseded": "wont-do"})

#: The set of normalized terminal state strings.
#: A state is terminal when an item is definitively resolved and no further
#: agent action is expected.
TERMINAL_STATES: frozenset[str] = frozenset(CLOSED_STATES)


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


def canonical_state(state: str) -> str:
    """Return *state* normalized, with a legacy alias mapped to what it reads as.

    Readers of an existing file's ``## Status`` / ``## State`` use this so a
    retired value (``superseded``) is treated as the closed state it stands
    for (``wont-do``).  Any other value is returned normalized and unchanged.

    Examples:
        >>> canonical_state("superseded")
        'wont-do'
        >>> canonical_state("  Done ")
        'done'
        >>> canonical_state("open")
        'open'
    """
    s = normalize(state)
    return LEGACY_STATE_ALIASES.get(s, s)


def is_terminal(state: str) -> bool:
    """Return True when the state is a terminal state.

    A terminal state means the item is definitively resolved ("done") or
    intentionally not addressed ("wont-do").  The delete guard uses this
    predicate: items in terminal states may be deleted without --force;
    items in non-terminal states (open, running, working, blocked, etc.)
    are refused.

    Accepts both uppercase task forms and lowercase intake forms — both are
    normalized before comparison — and reads a legacy alias as the state it
    stands for (``superseded`` is ``wont-do``, so it is terminal).

    Args:
        state: Raw state string as read from a status/status.md field.

    Returns:
        True when the canonical state is "done" or "wont-do".

    Examples:
        >>> is_terminal("done")
        True
        >>> is_terminal("DONE")
        True
        >>> is_terminal("wont-do")
        True
        >>> is_terminal("WONT-DO")
        True
        >>> is_terminal("superseded")
        True
        >>> is_terminal("running")
        False
        >>> is_terminal("open")
        False
    """
    return canonical_state(state) in TERMINAL_STATES


def closed_state_map() -> list[tuple[str, str]]:
    """Return ``(state, closed state it reads as)`` pairs, closed states first.

    Every closed state maps to itself; every legacy alias maps to the closed
    state it stands for.  This is the list the bash readers load through the
    ``closed-map`` command, so they never spell a closed-state list of their
    own.

    Examples:
        >>> closed_state_map()
        [('done', 'done'), ('wont-do', 'wont-do'), ('superseded', 'wont-do')]
    """
    pairs = [(state, state) for state in CLOSED_STATES]
    pairs.extend(sorted(LEGACY_STATE_ALIASES.items()))
    return pairs


def main(argv: Sequence[str] | None = None) -> int:
    """Command-line entry point; see the module docstring for the contract."""
    args = list(sys.argv[1:] if argv is None else argv)
    if args != ["closed-map"]:
        print(
            "usage: python3 -m pgai_agent_kanban.lib.terminal_states closed-map",
            file=sys.stderr,
        )
        return 2
    for state, reads_as in closed_state_map():
        print(f"{state} {reads_as}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
