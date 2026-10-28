"""
terminal_states.py — Canonical state vocabulary for intake and task items.

This module is the single definition point for the intake status vocabulary
and the terminal-state strings used across delete, close, and selection
code.  All consumers import from here; no second inline list may appear
anywhere in those code paths.

Intake status vocabulary (the ``## Status`` of a bug, priority or
requirement file), in lifecycle order:

Active states -- the item still needs something:
    open            — waiting to be bundled and worked
    running         — bundled into an RC that is working it
    needs-operator  — left out of every ticket because a human must answer
                      first; its question is an open operator-inbox entry,
                      and answering that entry sets the item back to open

Closed (terminal) states -- the closed states an item can be closed to, and
the only ones close.sh / ops.write.close_item accept:
    done        — item completed successfully
    wont-do     — item intentionally not addressed
    duplicate   — item reports the same defect as another item; its
                  ``## Close Note`` names the surviving item

Legacy alias, read but never written:
    superseded  — read as ``wont-do``.  It is not a kanban state; an item
                  that reports the same defect as another closes as
                  ``duplicate`` with a pointer, and one another item
                  otherwise subsumed closes as ``wont-do`` with a note.
                  Existing files that still carry it are not rewritten, so
                  every reader maps it through ``canonical_state()``.

For agent TASKS the states are written in uppercase ("DONE", "WONT-DO").
For INTAKE items (bugs, priority, requirements) they are written in lowercase
("done", "wont-do").  Both forms normalize to the same canonical value after
``normalize()`` is applied.

Public API:
    OPEN_STATE, RUNNING_STATE, NEEDS_OPERATOR_STATE
                              → str: the three active intake states
    DUPLICATE_STATE           → str: the closed state that carries a pointer
    ACTIVE_INTAKE_STATES      → tuple: the active intake states, in order
    CLOSED_STATES             → tuple: the closed states, in display order
    INTAKE_STATES             → tuple: every intake status, active then closed
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
per legacy alias (``done done``, ``wont-do wont-do``, ``duplicate
duplicate``, ``superseded wont-do``) and exits 0.  Any other argument prints
usage to stderr and exits 2.
"""

from __future__ import annotations

import sys
from types import MappingProxyType
from typing import Mapping, Sequence

# ---------------------------------------------------------------------------
# Canonical intake vocabulary (normalized, lowercase)
# ---------------------------------------------------------------------------

#: An intake item waiting to be bundled and worked.
OPEN_STATE = "open"

#: An intake item bundled into an RC that is working it.
RUNNING_STATE = "running"

#: An intake item waiting on a human answer recorded in the operator inbox.
NEEDS_OPERATOR_STATE = "needs-operator"

#: The closed state of an item that reports the same defect as another; its
#: ``## Close Note`` names the surviving item.
DUPLICATE_STATE = "duplicate"

#: The active intake states, in lifecycle order: an item in one of these
#: still needs something.
ACTIVE_INTAKE_STATES: tuple[str, ...] = (OPEN_STATE, RUNNING_STATE, NEEDS_OPERATOR_STATE)

#: The closed states, in display order: the states close.sh /
#: ops.write.close_item accept and every reader treats as closed.  This is
#: the one list; readers import it rather than spelling their own.
CLOSED_STATES: tuple[str, ...] = ("done", "wont-do", DUPLICATE_STATE)

#: Every intake ``## Status`` value the kanban writes: active, then closed.
INTAKE_STATES: tuple[str, ...] = ACTIVE_INTAKE_STATES + CLOSED_STATES

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

    A terminal state means the item is definitively resolved ("done"),
    intentionally not addressed ("wont-do"), or closed in favour of the
    item it duplicates ("duplicate").  The delete guard uses this
    predicate: items in terminal states may be deleted without --force;
    items in non-terminal states (open, running, needs-operator, working,
    blocked, etc.) are refused.

    Accepts both uppercase task forms and lowercase intake forms — both are
    normalized before comparison — and reads a legacy alias as the state it
    stands for (``superseded`` is ``wont-do``, so it is terminal).

    Args:
        state: Raw state string as read from a status/status.md field.

    Returns:
        True when the canonical state is one of ``CLOSED_STATES``.

    Examples:
        >>> is_terminal("done")
        True
        >>> is_terminal("DONE")
        True
        >>> is_terminal("wont-do")
        True
        >>> is_terminal("WONT-DO")
        True
        >>> is_terminal("duplicate")
        True
        >>> is_terminal("superseded")
        True
        >>> is_terminal("running")
        False
        >>> is_terminal("needs-operator")
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
        [('done', 'done'), ('wont-do', 'wont-do'), ('duplicate', 'duplicate'), ('superseded', 'wont-do')]
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
