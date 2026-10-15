"""
pgai_agent_kanban.board — the kanban board's status classification.

``GET /board`` and the dashboard's column panes read their columns, column
kinds and item statuses from here, so every surface that shows the board
assigns an item the same status.  The classification rules, the board
status vocabulary, and the WONT-DO decision (WONT-DO renders as
``"wont-do"``, never folded into ``"done"``) are documented in
``pgai_agent_kanban.board.classifier``.

Public surface
--------------
Columns:
    COLUMN_ORDER, COLUMN_KINDS

Status vocabulary:
    WONT_DO_STATUS

Classification:
    classify_input_item, classify_queue_item, classify_intake_status,
    classify_requirement

Readers:
    get_active_rc_for_project, get_last_released_for_project,
    get_target_version_for_item, read_intake_status,
    read_task_state_from_status_md, parse_queue_line, marker_to_state
"""

from pgai_agent_kanban.board.classifier import (
    COLUMN_KINDS,
    COLUMN_ORDER,
    WONT_DO_STATUS,
    classify_input_item,
    classify_intake_status,
    classify_queue_item,
    classify_requirement,
    get_active_rc_for_project,
    get_last_released_for_project,
    get_target_version_for_item,
    marker_to_state,
    parse_queue_line,
    read_intake_status,
    read_task_state_from_status_md,
)

__all__ = [
    "COLUMN_KINDS",
    "COLUMN_ORDER",
    "WONT_DO_STATUS",
    "classify_input_item",
    "classify_intake_status",
    "classify_queue_item",
    "classify_requirement",
    "get_active_rc_for_project",
    "get_last_released_for_project",
    "get_target_version_for_item",
    "marker_to_state",
    "parse_queue_line",
    "read_intake_status",
    "read_task_state_from_status_md",
]
