"""
env.py — Canonical resolver for the kanban root environment variable.

This module is the single, authoritative reader of PGAI_AGENT_KANBAN_ROOT_PATH
in Python.  Every Python entry point that needs the kanban root must call
``resolve_kanban_root()``; no other module may call
``os.environ["PGAI_AGENT_KANBAN_ROOT_PATH"]`` or
``os.environ.get("PGAI_AGENT_KANBAN_ROOT_PATH")`` directly.

Design contract (mirrors team/scripts/lib/env_bootstrap.sh):

  1. Reads ``PGAI_AGENT_KANBAN_ROOT_PATH`` from the process environment.
     Python never parses shell-env files — bash owns sourcing; Python owns
     consuming.  The api-server launcher (api-server.sh) sources shell-env and
     hands the resolved value to uvicorn via the inherited process environment,
     so by the time any Python code runs the variable is already present.

  2. Absolutizes the value with ``pathlib.Path.resolve()`` so the returned
     path is always absolute regardless of how the caller set the env var.

  3. Fails loud when the variable is unset or empty, raising ``RuntimeError``
     with a message that matches the bash prelude grammar:

         PGAI_AGENT_KANBAN_ROOT_PATH not set — shell-env missing or broken

     Callers should let this propagate; it is a programmer / configuration
     error, not a recoverable condition.

Usage::

    from pgai_agent_kanban.env import resolve_kanban_root

    kanban_root = resolve_kanban_root()   # Path, always absolute
    kanban_root = resolve_kanban_root(args.kanban_root, home_default=True)

``resolve_kanban_root(override, home_default=True)`` is the CLI form: an
explicit root first, then the variable, then ``~/pgai_agent_kanban``.
``resolve_kanban_root_or_default`` is the form the PM planner's helpers
use: the same precedence, the value returned as given, not absolutized.
"""

from __future__ import annotations

import os
from pathlib import Path

# The canonical message prefix matches the bash prelude's fail-loud grammar
# (env_bootstrap.sh line: "PGAI_AGENT_KANBAN_ROOT_PATH not set — shell-env
# missing or broken at <candidate>/shell-env").  Python has no candidate path
# because bash owns sourcing; the clause after the em-dash adapts accordingly.
_FAIL_LOUD_MSG = "PGAI_AGENT_KANBAN_ROOT_PATH not set — shell-env missing or broken"


def resolve_kanban_root(
    override: str | os.PathLike[str] | None = None,
    *,
    home_default: bool = False,
) -> Path:
    """Read, absolutize, and return the kanban root path.

    Precedence: *override* when given and non-empty, then
    ``PGAI_AGENT_KANBAN_ROOT_PATH``, then -- only with ``home_default=True``
    -- ``~/pgai_agent_kanban``. A leading ``~`` is expanded and the result
    absolutized, so the returned path is always absolute.

    Args:
        override: An explicit root, such as a ``--kanban-root`` CLI value.
        home_default: Fall back to ``~/pgai_agent_kanban`` instead of raising
            when neither *override* nor the variable is set -- the
            fresh-install default the token and metrics roll-up CLIs use.

    Raises:
        RuntimeError: When no root is given, ``PGAI_AGENT_KANBAN_ROOT_PATH``
            is unset or empty, and *home_default* is False, with the message:
            ``PGAI_AGENT_KANBAN_ROOT_PATH not set — shell-env missing or broken``

    Returns:
        An absolute :class:`pathlib.Path`. Whether it exists is the caller's
        check.

    Note:
        This function reads the environment on every call.  Callers that
        construct long-lived objects (e.g. ``OpsContext``) should call this
        once at construction time and store the result.
    """
    raw = str(override).strip() if override is not None else ""
    if not raw:
        raw = os.environ.get("PGAI_AGENT_KANBAN_ROOT_PATH", "").strip()
    if not raw:
        if not home_default:
            raise RuntimeError(_FAIL_LOUD_MSG)
        return (Path.home() / "pgai_agent_kanban").resolve()
    return Path(raw).expanduser().resolve()


def resolve_kanban_root_or_default(kanban_root: str | os.PathLike[str] | None = None) -> Path:
    """The kanban root: *kanban_root* when given, else the variable, else ~/pgai_agent_kanban.

    The environment is read on every call (no caching), so a test that sets
    the variable gets its own root. The value is returned as given, not
    absolutized.
    """
    if kanban_root is not None:
        return Path(kanban_root)
    return Path(os.environ.get("PGAI_AGENT_KANBAN_ROOT_PATH") or str(Path.home() / "pgai_agent_kanban"))
