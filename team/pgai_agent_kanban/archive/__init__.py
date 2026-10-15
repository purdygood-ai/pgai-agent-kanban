"""
pgai_agent_kanban.archive — moving finished work out of a live project.

After a release ships, finished items beyond ``[archive] archive_count`` per
category move from ``<kanban_root>/projects/<project>/`` to
``<kanban_root>/archive/<project>/`` under the same relative names, so live
scans see only recent and unfinished work. Nothing is deleted and every
archived item can be found by ID and restored.

This package holds the pieces every archive operation shares:

``layout``   where the live and archive trees keep each category
             (:func:`archive_root`, :func:`archive_layout`,
             :func:`live_layout`, :class:`ProjectLayout`, and
             :func:`archive_layout_for_category_dir`, the archive a live
             category directory belongs to).
``config``   the ``archive_count`` resolver
             (:func:`resolve_archive_count`, :func:`effective_archive_count`).
``lookup``   finding an item by ID (:func:`find_archived`, :func:`find_item`,
             :func:`classify_item_id`), and the archived-and-not-live
             task IDs prerequisite resolution treats as DONE
             (:func:`archived_task_ids`; module CLI verb
             ``archived_tasks``), and the live-plus-archived task
             folders the token and metrics aggregators walk
             (:func:`archived_task_dirs`, :func:`task_dirs_with_archived`).
``plan``     choosing what a run would move, moving nothing
             (:func:`plan`, :class:`ArchivePlan`).
``apply``    moving a plan's items into the archive under the project's
             repo lock, and moving one item back (:func:`apply`,
             :func:`restore`, :class:`RunResult`).
``move``     the one file/directory move both use (:func:`move_path`).
``ledger``   ``ARCHIVE-LEDGER.tsv``, one row per item moved or restored
             (:class:`LedgerRow`, :func:`read_rows`).
``reconcile`` moving live queue lines that name an item which is no longer
             live -- archived, or a finished task whose folder is in
             neither tree -- to the archive's queue files, with a dry run
             (:func:`plan_queue_reconcile`, :func:`apply_queue_reconcile`,
             :func:`render_queue_reconcile_plan`); ``scripts/archive.sh
             --reconcile-queues [--dry-run]``.
``legacy``   emptying the requirements archive older installs kept inside
             the live project (``tasks/archive/requirements/``) into the
             archive tree, one ledger row per file
             (:func:`plan_legacy`, :func:`migrate_legacy`).
``run``      the one operation ``scripts/archive.sh`` and
             ``POST /operations/archive`` both call: resolve the project,
             then plan and apply, preview, restore, or reconcile queue lines
             (:func:`run_archive`, :func:`render_plan`, :class:`ArchiveRun`).
             ``python3 -m pgai_agent_kanban.archive run_archive ...`` is
             its module CLI.
"""

from pgai_agent_kanban.archive.apply import (
    RESULT_FAILED,
    RESULT_OK,
    RESULT_SKIPPED,
    ArchiveRestoreError,
    RunResult,
    apply,
    restore,
)
from pgai_agent_kanban.archive.config import (
    ARCHIVE_COUNT_DEFAULT,
    ArchiveCount,
    ArchiveCountError,
    effective_archive_count,
    resolve_archive_count,
)
from pgai_agent_kanban.archive.layout import (
    ITEM_CATEGORIES,
    ArchiveLayoutError,
    ProjectLayout,
    archive_layout,
    archive_layout_for_category_dir,
    archive_layout_for_tasks_dir,
    archive_root,
    live_layout,
)
from pgai_agent_kanban.archive.ledger import CATEGORY_RESTORE, LedgerRow, read_rows
from pgai_agent_kanban.archive.legacy import LegacyMove, LegacyPlan, migrate_legacy, plan_legacy
from pgai_agent_kanban.archive.lookup import (
    ItemId,
    archived_task_dirs,
    archived_task_ids,
    classify_item_id,
    find_archived,
    find_item,
    task_dirs_with_archived,
)
from pgai_agent_kanban.archive.move import ArchiveMoveError, move_path
from pgai_agent_kanban.archive.plan import ArchivePlan, HeldItem, PlannedItem, QueueLine, TrainingLog, plan
from pgai_agent_kanban.archive.reconcile import (
    QueueReconcilePlan,
    ReconcileLine,
    apply_queue_reconcile,
    plan_queue_reconcile,
    render_queue_reconcile_plan,
)
from pgai_agent_kanban.archive.run import ArchiveRun, render_plan, run_archive

__all__ = [
    "ARCHIVE_COUNT_DEFAULT",
    "CATEGORY_RESTORE",
    "ITEM_CATEGORIES",
    "RESULT_FAILED",
    "RESULT_OK",
    "RESULT_SKIPPED",
    "ArchiveCount",
    "ArchiveCountError",
    "ArchiveLayoutError",
    "ArchiveMoveError",
    "ArchivePlan",
    "ArchiveRestoreError",
    "ArchiveRun",
    "HeldItem",
    "ItemId",
    "LedgerRow",
    "LegacyMove",
    "LegacyPlan",
    "PlannedItem",
    "ProjectLayout",
    "QueueLine",
    "QueueReconcilePlan",
    "ReconcileLine",
    "RunResult",
    "TrainingLog",
    "apply",
    "apply_queue_reconcile",
    "archive_layout",
    "archive_layout_for_category_dir",
    "archive_layout_for_tasks_dir",
    "archive_root",
    "archived_task_dirs",
    "archived_task_ids",
    "classify_item_id",
    "effective_archive_count",
    "find_archived",
    "find_item",
    "live_layout",
    "migrate_legacy",
    "move_path",
    "plan",
    "plan_legacy",
    "plan_queue_reconcile",
    "read_rows",
    "render_plan",
    "render_queue_reconcile_plan",
    "resolve_archive_count",
    "restore",
    "run_archive",
    "task_dirs_with_archived",
]
