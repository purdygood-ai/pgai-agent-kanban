"""
pgai_agent_kanban.ops — shared operations library for the kanban system.

Public surface
--------------
Error hierarchy:
    OpsError, NotFound, Ambiguous, Refused, IoError

Execution context:
    OpsContext

Read facade:
    list_projects, get_halt_state, get_queues, get_task_status,
    get_release_state, get_attention, get_metrics, get_next_firings

Write / resolve facade:
    resolve_item, ResolveResult
    halt, unhalt, halt_after, halt_global, unhalt_global
    deposit_intake
    close_item, wontdo_item, delete_item
    reset_item
"""

from pgai_agent_kanban.ops.errors import (
    OpsError,
    NotFound,
    Ambiguous,
    Refused,
    IoError,
)
from pgai_agent_kanban.ops.context import OpsContext
from pgai_agent_kanban.ops.read import (
    list_projects,
    get_halt_state,
    get_queues,
    get_task_status,
    get_release_state,
    get_attention,
    get_metrics,
    get_next_firings,
)
from pgai_agent_kanban.ops.resolve import (
    resolve_item,
    ResolveResult,
)
from pgai_agent_kanban.ops.write import (
    halt,
    unhalt,
    halt_after,
    halt_global,
    unhalt_global,
    deposit_intake,
    close_item,
    wontdo_item,
    delete_item,
    reset_item,
)

__all__ = [
    # Error hierarchy
    "OpsError",
    "NotFound",
    "Ambiguous",
    "Refused",
    "IoError",
    # Execution context
    "OpsContext",
    # Read facade
    "list_projects",
    "get_halt_state",
    "get_queues",
    "get_task_status",
    "get_release_state",
    "get_attention",
    "get_metrics",
    "get_next_firings",
    # Write / resolve facade
    "resolve_item",
    "ResolveResult",
    # Halt write operations
    "halt",
    "unhalt",
    "halt_after",
    "halt_global",
    "unhalt_global",
    # Intake write operation
    "deposit_intake",
    # Status write operations
    "close_item",
    "wontdo_item",
    "delete_item",
    # Reset write operation
    "reset_item",
]
