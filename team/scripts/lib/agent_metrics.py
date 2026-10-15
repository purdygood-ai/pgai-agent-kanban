#!/usr/bin/env python3
"""
agent_metrics.py -- team/scripts/lib/ entry point for the agent-metrics
renderer, which lives in pgai_agent_kanban.lib.metrics.agents (its
arithmetic in pgai_agent_kanban.lib.agent_metrics).

Re-exports the renderer and the arithmetic so a caller that puts
team/scripts/lib/ on sys.path keeps importing them from ``agent_metrics``,
and runs the renderer's CLI when executed directly:

    python3 team/scripts/lib/agent_metrics.py --kanban-root PATH --project NAME [SPAN]

See pgai_agent_kanban.lib.metrics.agents for the arguments, the rendered
block and the exit codes. scripts/dashboard/agent-metrics.sh reaches the
same main() through pp_run_ops pgai_agent_kanban.lib.metrics
compute_agent_averages.

The package sits beside team/scripts/ in both the dev tree (team/) and the
live install (the install root), so this file locates it from its own
path -- two levels above team/scripts/lib/ -- and never from the
environment.
"""

from __future__ import annotations

import pathlib
import sys

_OWN_ROOT = str(pathlib.Path(__file__).resolve().parent.parent.parent)  # lib/ -> scripts/ -> root
if _OWN_ROOT not in sys.path:
    sys.path.insert(0, _OWN_ROOT)

from pgai_agent_kanban.lib.metrics.agents import (  # noqa: E402
    compute_agent_averages,
    load_rc_window,
    main,
    render_project_block,
    round_one_decimal,
    span_footer,
    span_from_args,
    span_header,
    total_tokens,
)

__all__ = [
    "compute_agent_averages",
    "load_rc_window",
    "main",
    "render_project_block",
    "round_one_decimal",
    "span_footer",
    "span_from_args",
    "span_header",
    "total_tokens",
]

if __name__ == "__main__":
    sys.exit(main())
