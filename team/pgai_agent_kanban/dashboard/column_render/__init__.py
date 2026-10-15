"""
pgai_agent_kanban.dashboard.column_render — the dashboard's board column panes.

``team/scripts/dashboard/column-render.sh`` resolves its arguments,
configuration and colours in bash and renders through this package, one
Python process per pane render (``python3 -m
pgai_agent_kanban.dashboard.column_render``; see ``__main__`` for the
command line).  Item statuses come from ``pgai_agent_kanban.board`` — the
classifier ``GET /board`` uses — so the panes and the board agree about
every item, WONT-DO included.

Modules
-------
cells
    Pure formatting shared by both panes: palette, display status and
    colour, ANSI-aware truncation, compact IDs, row-window selection.
single
    One project's input or queue column.
multi
    The mixed-project column (``--all-projects``), one block per project.
board_snapshot
    The shared per-tick board snapshot every mixed-project column renders
    its slice from, so the board's gather runs once per tick, not once per
    column.
"""

from pgai_agent_kanban.dashboard.column_render.cells import DeferCards, Palette, display_status, status_color
from pgai_agent_kanban.dashboard.column_render.multi import (
    Layout,
    ProjectSource,
    gather_project_entries,
    render_multi_project_column,
    sources_from_env,
)
from pgai_agent_kanban.dashboard.column_render.single import (
    queue_items,
    render_input_column,
    render_queue_column,
)

__all__ = [
    "DeferCards",
    "Layout",
    "Palette",
    "ProjectSource",
    "display_status",
    "gather_project_entries",
    "queue_items",
    "render_input_column",
    "render_multi_project_column",
    "render_queue_column",
    "sources_from_env",
    "status_color",
]
