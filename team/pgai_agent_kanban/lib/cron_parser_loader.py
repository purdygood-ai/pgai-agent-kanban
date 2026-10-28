"""
cron_parser_loader.py -- the one home for loading cron_parser.py by file path.

The dashboard's next-firing readers (``dashboard/cron_firings.py``,
``dashboard/roster_entries.py``) are handed the parser as an absolute file
path (``team/pm-agent/lib/cron_parser.py`` in the repository,
``pm-agent/lib/cron_parser.py`` in an install) rather than finding it on
sys.path, so they load it from that path through importlib.

Usage (import)::

    from pgai_agent_kanban.lib.cron_parser_loader import load_cron_parser
    firings = load_cron_parser(parser_path).next_firings(crontab_text, now=None)
"""

from __future__ import annotations

import importlib.util
import pathlib

__all__ = ["load_cron_parser"]


def load_cron_parser(parser_path: pathlib.Path):
    """Load cron_parser module from an absolute file path via importlib."""
    spec = importlib.util.spec_from_file_location("cron_parser", parser_path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod
