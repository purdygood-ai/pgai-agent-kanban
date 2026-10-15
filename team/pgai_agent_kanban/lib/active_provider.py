"""
active_provider.py — read the active agent provider from kanban.cfg.

Python counterpart of ``read_active_provider`` in
``team/scripts/lib/active_provider.sh``: the same key, the same accepted
values and the same fall-back to ``claude``, so a Python surface (the queue
pane's "Active provider:" line) names the provider the bash wake path uses.

    provider, warning = read_active_provider(kanban_root)

``warning`` is ``""`` when ``[providers] active`` holds a recognised value,
and otherwise a one-line explanation of why ``claude`` was used; callers
that print to a terminal send it to stderr, as the bash helper does.
"""

from __future__ import annotations

import pathlib

from pgai_agent_kanban.lib import ini

__all__ = ["DEFAULT_PROVIDER", "VALID_PROVIDERS", "read_active_provider"]

#: Providers ``[providers] active`` may name -- the same set
#: active_provider.sh's ``case`` statement accepts.
VALID_PROVIDERS: tuple[str, ...] = ("claude", "codex", "gemini")

#: The provider used when kanban.cfg is absent or names no valid provider.
DEFAULT_PROVIDER = "claude"


def read_active_provider(kanban_root: "pathlib.Path | str") -> tuple[str, str]:
    """Return ``(provider, warning)`` from ``<kanban_root>/kanban.cfg``.

    The value of ``[providers] active`` has all whitespace removed and is
    lowercased before it is checked against :data:`VALID_PROVIDERS`.

    Args:
        kanban_root: The kanban root holding kanban.cfg.

    Returns:
        ``(provider, "")`` for a recognised value; otherwise
        ``(DEFAULT_PROVIDER, warning)`` where *warning* says why: kanban.cfg
        is missing, the key is unset or empty, or the value is not a
        recognised provider.
    """
    cfg = pathlib.Path(kanban_root) / "kanban.cfg"
    if not cfg.is_file():
        return DEFAULT_PROVIDER, (
            f"active_provider: kanban.cfg not found at '{cfg}'; defaulting to '{DEFAULT_PROVIDER}'"
        )
    value = "".join(ini.get_value(cfg, "providers", "active").split()).lower()
    if value in VALID_PROVIDERS:
        return value, ""
    if not value:
        return DEFAULT_PROVIDER, (
            "active_provider: kanban.cfg [providers] active is unset or empty; "
            f"defaulting to '{DEFAULT_PROVIDER}'"
        )
    return DEFAULT_PROVIDER, (
        f"active_provider: unrecognized provider '{value}' in kanban.cfg [providers] active; "
        f"valid values: {' '.join(VALID_PROVIDERS)}; defaulting to '{DEFAULT_PROVIDER}'"
    )
