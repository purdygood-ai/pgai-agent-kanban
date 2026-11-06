#!/usr/bin/env python3
"""
provider_home.py — the directory a provider CLI reads its agents or skills from.

This is the one place the provider-home rule is written down. Every surface
that needs a provider directory resolves it here: the bash deploy helpers
(``provider_home_dir``, ``provider_agents_dir`` and ``provider_skills_dir`` in
``scripts/lib/subagent_deploy.sh``, used by install.sh, upgrade.sh and
install-skills.sh) call the command line below, and the skills module
(``skills_install.provider_skills_home``, used by GET /skills and its own
command line) imports the function.

The rule, for a provider and a *kind* (``agents`` or ``skills``):

    claude  $CLAUDE_CONFIG_DIR/<kind> when CLAUDE_CONFIG_DIR is set and
            non-empty (the directory Claude Code reads its config from),
            otherwise $HOME/.claude/<kind>
    other   $HOME/.<provider>/<kind>, e.g. $HOME/.codex/skills

A relative CLAUDE_CONFIG_DIR stays relative; callers that report a path
make it absolute themselves. When HOME is unset or empty, the account's home
directory is used.

The module imports only the standard library, so install.sh can resolve the
agent-wrapper directories before any optional dependency is installed.

Usage:
    python3 -m pgai_agent_kanban.lib.provider_home PROVIDER KIND
    python3 team/pgai_agent_kanban/lib/provider_home.py PROVIDER KIND   (same, by path)

Prints the directory on stdout with no trailing newline, so a bash caller's
``"$(...)"`` captures exactly the path. Exit 0 on success, 2 on a usage error
(a missing or extra argument).
``--help`` prints the usage and exits 0.

Example:
    python3 -m pgai_agent_kanban.lib.provider_home codex skills
    # -> /home/USER/.codex/skills

Public API:
    provider_home_dir(provider, kind, env=None) -> pathlib.Path
    main(argv=None) -> int
"""

from __future__ import annotations

import argparse
import os
import pathlib
import sys
from collections.abc import Mapping, Sequence

__all__ = ["main", "provider_home_dir"]


def provider_home_dir(provider: str, kind: str, env: Mapping[str, str] | None = None) -> pathlib.Path:
    """Return the directory *provider*'s CLI reads *kind* from.

    claude reads ``$CLAUDE_CONFIG_DIR/<kind>`` when ``CLAUDE_CONFIG_DIR`` is
    set and non-empty, otherwise ``$HOME/.claude/<kind>``; every other
    provider reads ``$HOME/.<provider>/<kind>``. A relative
    ``CLAUDE_CONFIG_DIR`` gives a relative path.

    Args:
        provider: provider name as listed in kanban.cfg ``[providers]
                  available``, e.g. ``claude`` or ``codex``.
        kind:     the subdirectory the CLI reads, e.g. ``agents`` or
                  ``skills``.
        env:      environment to read (default ``os.environ``). ``HOME``
                  falls back to the account's home directory when unset or
                  empty.
    """
    env = os.environ if env is None else env
    home = env.get("HOME") or str(pathlib.Path.home())
    if provider == "claude":
        base = env.get("CLAUDE_CONFIG_DIR") or os.path.join(home, ".claude")
    else:
        base = os.path.join(home, f".{provider}")
    return pathlib.Path(base) / kind


def main(argv: Sequence[str] | None = None) -> int:
    """Print the provider directory for PROVIDER and KIND; return the exit code."""
    parser = argparse.ArgumentParser(
        prog="python3 -m pgai_agent_kanban.lib.provider_home",
        description=(
            "Print the directory a provider CLI reads KIND from: claude "
            "$CLAUDE_CONFIG_DIR/KIND (default $HOME/.claude/KIND), any other "
            "provider $HOME/.PROVIDER/KIND. No trailing newline. Exit 0, or 2 "
            "on a usage error."
        ),
        epilog="Example: python3 -m pgai_agent_kanban.lib.provider_home codex skills",
    )
    parser.add_argument("provider", metavar="PROVIDER", help="Provider name, e.g. claude or codex.")
    parser.add_argument("kind", metavar="KIND", help="Subdirectory the CLI reads, e.g. agents or skills.")
    try:
        args = parser.parse_args(None if argv is None else list(argv))
    except SystemExit as exc:
        return 0 if exc.code in (0, None) else 2
    sys.stdout.write(str(provider_home_dir(args.provider, args.kind)))
    return 0


if __name__ == "__main__":
    sys.exit(main())
