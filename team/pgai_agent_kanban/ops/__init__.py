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
    get_release_state, get_deep_verify_status, get_attention, get_metrics,
    get_next_firings, get_status, get_test_report, get_task_readme,
    list_rejected

Write / resolve facade:
    resolve_item, ResolveResult, resolve_project_name
    halt, unhalt, halt_after, halt_global, unhalt_global
    defer, undefer, CampaignChange
    set_version_ceiling, CeilingReport, switch_provider, ProviderSwitch
    create_project, add_project, remove_project, ProjectOutcome
    list_locks, LockListing, release_lock, LockRelease, LockRefusal
    acquire_lock, repo_lock_path, HeldLock, LockContended
    resolve_verdicts, VerdictReport
    deposit_intake
    close_item, wontdo_item, delete_item
    reset_item
    set_marker, remove_marker, CANONICAL_MARKERS
    queue_entries, append_queue_lines
    replace_marker, ensure_marker, queue_markers, bracket_marker, parse_scan_rule, scan_queue
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
    get_deep_verify_status,
    get_attention,
    get_metrics,
    get_next_firings,
    get_status,
    get_test_report,
    get_task_readme,
    list_rejected,
)
from pgai_agent_kanban.ops.projects import (
    create_project,
    add_project,
    remove_project,
    ProjectOutcome,
)
from pgai_agent_kanban.ops.locks import (
    list_locks,
    LockListing,
    release_lock,
    LockRelease,
    LockRefusal,
    acquire_lock,
    repo_lock_path,
    HeldLock,
    LockContended,
)
from pgai_agent_kanban.ops.verdict import (
    resolve_verdicts,
    VerdictReport,
)
from pgai_agent_kanban.ops.resolve import (
    resolve_item,
    ResolveResult,
    resolve_project_name,
)
from pgai_agent_kanban.ops.write import (
    halt,
    unhalt,
    halt_after,
    halt_global,
    unhalt_global,
    defer,
    undefer,
    CampaignChange,
    set_version_ceiling,
    CeilingReport,
    switch_provider,
    ProviderSwitch,
    deposit_intake,
    close_item,
    wontdo_item,
    delete_item,
    reset_item,
    set_marker,
    remove_marker,
    CANONICAL_MARKERS,
    queue_entries,
    append_queue_lines,
    replace_marker,
    ensure_marker,
    queue_markers,
    bracket_marker,
    parse_scan_rule,
    scan_queue,
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
    "get_deep_verify_status",
    "get_attention",
    "get_metrics",
    "get_next_firings",
    "get_status",
    "get_test_report",
    "get_task_readme",
    "list_rejected",
    # Write / resolve facade
    "resolve_item",
    "ResolveResult",
    "resolve_project_name",
    # Halt write operations
    "halt",
    "unhalt",
    "halt_after",
    "halt_global",
    "unhalt_global",
    # Deferral campaign write operations
    "defer",
    "undefer",
    "CampaignChange",
    # Configuration write operations
    "set_version_ceiling",
    "CeilingReport",
    "switch_provider",
    "ProviderSwitch",
    # Project registry write operations
    "create_project",
    "add_project",
    "remove_project",
    "ProjectOutcome",
    # Advisory-lock listing and release
    "list_locks",
    "LockListing",
    "release_lock",
    "LockRelease",
    "LockRefusal",
    # Advisory-lock acquisition with provenance
    "acquire_lock",
    "repo_lock_path",
    "HeldLock",
    "LockContended",
    # Lane verdicts (why, verdict-report, GET /verdicts)
    "resolve_verdicts",
    "VerdictReport",
    # Intake write operation
    "deposit_intake",
    # Status write operations
    "close_item",
    "wontdo_item",
    "delete_item",
    # Reset write operation
    "reset_item",
    # Queue-marker write operations
    "set_marker",
    "remove_marker",
    "CANONICAL_MARKERS",
    # Queue-line moves between queue files (whole lines, markers unchanged)
    "queue_entries",
    "append_queue_lines",
    # Queue-line text rewrite and reads (the wake's marker writer and scans)
    "replace_marker",
    "ensure_marker",
    "queue_markers",
    "bracket_marker",
    "parse_scan_rule",
    "scan_queue",
]
