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
"""

from __future__ import annotations

import os
from pathlib import Path

# The canonical message prefix matches the bash prelude's fail-loud grammar
# (env_bootstrap.sh line: "PGAI_AGENT_KANBAN_ROOT_PATH not set — shell-env
# missing or broken at <candidate>/shell-env").  Python has no candidate path
# because bash owns sourcing; the clause after the em-dash adapts accordingly.
_FAIL_LOUD_MSG = (
    "PGAI_AGENT_KANBAN_ROOT_PATH not set — shell-env missing or broken"
)


def resolve_kanban_root() -> Path:
    """Read, absolutize, and return the kanban root path from the environment.

    Reads ``PGAI_AGENT_KANBAN_ROOT_PATH`` from the process environment,
    absolutizes the value, and returns it as a :class:`pathlib.Path`.

    Raises:
        RuntimeError: When ``PGAI_AGENT_KANBAN_ROOT_PATH`` is unset or empty,
            with the message:
            ``PGAI_AGENT_KANBAN_ROOT_PATH not set — shell-env missing or broken``

    Returns:
        An absolute :class:`pathlib.Path` pointing to the kanban root.

    Note:
        This function reads the environment on every call.  Callers that
        construct long-lived objects (e.g. ``OpsContext``) should call this
        once at construction time and store the result.
    """
    raw = os.environ.get("PGAI_AGENT_KANBAN_ROOT_PATH", "").strip()
    if not raw:
        raise RuntimeError(_FAIL_LOUD_MSG)
    return Path(raw).resolve()
