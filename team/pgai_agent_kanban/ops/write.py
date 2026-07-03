"""
write.py — Python implementations of the write operations for the ops library.

This module provides the halt family, deposit_intake, close_item, wontdo_item,
delete_item, and reset_item as pure Python functions that operate on the filesystem.

Halt signal layout (mirrors operator_ops.sh):

    Per-project HALT signals:
        <project_root>/HALT         — hard-halt: wake loop exits cleanly
        <project_root>/HALT-AFTER   — soft-drain: evaluated by halt_after module;
                                       promoted to HALT when drain condition is met

    Global HALT signal:
        <kanban_root>/HALT          — blocks ALL projects at next wake iteration

Functions:

    halt(ctx, project)
        Create PROJECT_ROOT/HALT.  Idempotent.

    unhalt(ctx, project)
        Remove PROJECT_ROOT/HALT.  Idempotent.

    halt_after(ctx, project, token="rc")
        Write TOKEN to PROJECT_ROOT/HALT-AFTER.  Overwrites any existing sentinel.

    halt_global(ctx)
        Create KANBAN_ROOT/HALT.  Idempotent.

    unhalt_global(ctx)
        Remove KANBAN_ROOT/HALT.  Idempotent.

    deposit_intake(ctx, project, file_path)
        Copy a staged intake file into the correct project intake directory,
        routing by filename prefix.  Atomic: temp+chmod+rename.  Returns the
        deposited Path on success.

    close_item(ctx, project, key, state="done", note="", dry_run=False)
        Set the terminal state on a task or intake item identified by key.
        For agent tasks: always writes DONE to ## State, clears ## Blockers and
        ## Needs Human, flips the agent queue marker to [x].
        For intake items: sets ## Status to state (done|wont-do|superseded),
        optionally records note in ## Close Note, flips the backlog marker to [x].

    wontdo_item(ctx, project, key)
        Mark an agent task as WONT-DO.  Clears ## Blockers and ## Needs Human.
        Flips the agent queue marker to [x].

    delete_item(ctx, project, key, force=False)
        Delete a task (directory) or intake item (file) identified by key.
        Terminal-state guard: refuses non-terminal items unless force=True.
        Terminal states: DONE or WONT-DO for tasks; "done" for intake items.

    reset_item(ctx, project, key, keep_artifacts=False)
        Reset a task or intake item to a re-pickable state.
        For agent tasks: refuses if state is WORKING (Refused exception, exit 2).
          Regenerates status.md from the BACKLOG template (amnesia guarantee),
          clears artifacts/ and task logs/, flips queue marker to [ ], deletes
          the feature branch from the project dev tree, runs git worktree prune,
          and appends one line to the operator reset log.
        For bug/priority intake: resets ## Status to "open", flips backlog
          marker to [ ].
        For requirements intake: resets ## Status to "open", flips pm_backlog
          marker to [ ], clears the PM materializer's .materialized.* hash-marker.

All functions raise OpsError on argument or filesystem failure, NotFound when
the key is not found, Ambiguous when the key prefix matches multiple items,
and Refused when a guard rejects the operation.  They do not exit the process
and do not print to stdout — that is the caller's responsibility (CLI wrappers,
REST adapters).

CLI usage (bash delegation shim):
    python3 -m pgai_agent_kanban.ops halt        PROJECT_ROOT
    python3 -m pgai_agent_kanban.ops unhalt       PROJECT_ROOT
    python3 -m pgai_agent_kanban.ops halt_after   PROJECT_ROOT [TOKEN]
    python3 -m pgai_agent_kanban.ops halt_global  KANBAN_ROOT
    python3 -m pgai_agent_kanban.ops unhalt_global KANBAN_ROOT
    python3 -m pgai_agent_kanban.ops deposit_intake PROJECT_ROOT FILE_PATH
    python3 -m pgai_agent_kanban.ops close_item  PROJECT_ROOT KEY [STATE] [NOTE] [DRY_RUN]
    python3 -m pgai_agent_kanban.ops wontdo_item PROJECT_ROOT KEY
    python3 -m pgai_agent_kanban.ops delete_item PROJECT_ROOT KEY [FORCE]
    python3 -m pgai_agent_kanban.ops reset_item  PROJECT_ROOT KEY [KEEP_ARTIFACTS]

Note: the package-level entry point (python3 -m pgai_agent_kanban.ops) is the canonical
CLI path.  Running this submodule directly via -m pgai_agent_kanban.ops.write triggers a
runpy double-import RuntimeWarning because ops/__init__.py eagerly imports ops.write.
The _cli_main() function below is the implementation; __main__.py is the entry point.

Exit codes: 0 success, 1 error (message on stderr).
For deposit_intake: 2 routing refused, 3 target exists, 4 filesystem error.
For close_item/wontdo_item/delete_item: 2 ambiguous key, 3 not found, 4 state mutation failed,
    2 (delete) guard refused.
For reset_item: 2 WORKING state refusal or ambiguous key, 3 not found.
"""

from __future__ import annotations

import os
import re
import shutil
import sys
import tempfile
from pathlib import Path

from pgai_agent_kanban.ops.context import OpsContext
from pgai_agent_kanban.ops.errors import Ambiguous, IoError, NotFound, OpsError, Refused
from pgai_agent_kanban.ops.resolve import ResolveResult, resolve_item


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def halt(ctx: OpsContext, project: str) -> None:
    """Create the HALT sentinel for the named project.

    Idempotent: if PROJECT_ROOT/HALT already exists, returns without error.

    Args:
        ctx:     OpsContext carrying the kanban root path.
        project: Project name as registered in projects.cfg.

    Raises:
        OpsError: If the project root does not exist or the sentinel cannot
                  be created.
    """
    project_root = ctx.project_root(project)
    _require_dir(project_root, "halt")

    halt_path = project_root / "HALT"
    if halt_path.exists():
        return

    try:
        halt_path.touch()
    except OSError as exc:
        raise OpsError(
            f"halt: failed to create HALT sentinel at {halt_path}: {exc}"
        ) from exc


def unhalt(ctx: OpsContext, project: str) -> None:
    """Remove the HALT sentinel for the named project.

    Idempotent: if PROJECT_ROOT/HALT is already absent, returns without error.

    Args:
        ctx:     OpsContext carrying the kanban root path.
        project: Project name as registered in projects.cfg.

    Raises:
        OpsError: If the project root does not exist or the sentinel cannot
                  be removed.
    """
    project_root = ctx.project_root(project)
    _require_dir(project_root, "unhalt")

    halt_path = project_root / "HALT"
    if not halt_path.exists():
        return

    try:
        halt_path.unlink()
    except OSError as exc:
        raise OpsError(
            f"unhalt: failed to remove HALT sentinel at {halt_path}: {exc}"
        ) from exc


def halt_after(ctx: OpsContext, project: str, token: str = "rc") -> None:
    """Write TOKEN to the HALT-AFTER sentinel for the named project.

    The sentinel is evaluated by the Python halt_after module each wake cycle.
    When the drain condition for TOKEN is satisfied, the module promotes the
    sentinel to a hard HALT.  The wake loop does NOT check HALT-AFTER directly.

    Overwrites any existing HALT-AFTER file (the token may need updating when
    re-arming with a different drain event).

    Supported tokens:
        rc      — drain after current release candidate completes
        pm      — drain after current PM task completes
        coder   — drain after current CODER task completes
        writer  — drain after current WRITER task completes
        tester  — drain after current TESTER task completes
        cm      — drain after current CM task completes

    Args:
        ctx:     OpsContext carrying the kanban root path.
        project: Project name as registered in projects.cfg.
        token:   Drain event token.  Defaults to ``"rc"``.

    Raises:
        OpsError: If the project root does not exist or the sentinel cannot
                  be written.
    """
    project_root = ctx.project_root(project)
    _require_dir(project_root, "halt_after")

    halt_after_path = project_root / "HALT-AFTER"
    try:
        halt_after_path.write_text(f"{token}\n", encoding="utf-8")
    except OSError as exc:
        raise OpsError(
            f"halt_after: failed to write HALT-AFTER sentinel at {halt_after_path}: {exc}"
        ) from exc


def halt_global(ctx: OpsContext) -> None:
    """Create the global HALT sentinel at KANBAN_ROOT/HALT.

    The global HALT blocks ALL projects at the next wake-loop iteration.
    Idempotent: if the sentinel already exists, returns without error.

    Args:
        ctx: OpsContext carrying the kanban root path.

    Raises:
        OpsError: If the kanban root does not exist or the sentinel cannot
                  be created.
    """
    kanban_root = ctx.kanban_root
    _require_dir(kanban_root, "halt_global")

    halt_path = kanban_root / "HALT"
    if halt_path.exists():
        return

    try:
        halt_path.touch()
    except OSError as exc:
        raise OpsError(
            f"halt_global: failed to create global HALT sentinel at {halt_path}: {exc}"
        ) from exc


def unhalt_global(ctx: OpsContext) -> None:
    """Remove the global HALT sentinel at KANBAN_ROOT/HALT.

    Idempotent: if the sentinel is already absent, returns without error.

    Args:
        ctx: OpsContext carrying the kanban root path.

    Raises:
        OpsError: If the kanban root does not exist or the sentinel cannot
                  be removed.
    """
    kanban_root = ctx.kanban_root
    _require_dir(kanban_root, "unhalt_global")

    halt_path = kanban_root / "HALT"
    if not halt_path.exists():
        return

    try:
        halt_path.unlink()
    except OSError as exc:
        raise OpsError(
            f"unhalt_global: failed to remove global HALT sentinel at {halt_path}: {exc}"
        ) from exc


# ---------------------------------------------------------------------------
# Intake routing constants
# ---------------------------------------------------------------------------

# Patterns are checked in order; first match wins.
# Each entry: (compiled_regex, destination_subdir_name)
_INTAKE_ROUTES: list[tuple[re.Pattern[str], str]] = [
    (re.compile(r'^BUG-'), "bugs"),
    (re.compile(r'^PRIORITY-'), "priority"),
    (re.compile(r'^v[0-9].*\.md$'), "requirements"),
]


# ---------------------------------------------------------------------------
# deposit_intake
# ---------------------------------------------------------------------------


def deposit_intake(ctx: OpsContext, project: str, file_path: "Path | str") -> Path:
    """Copy a staged intake file into the correct project intake directory.

    Routes purely by the basename of ``file_path`` (case-sensitive prefix
    match).  This is a dumb router: it does not validate file contents,
    assign numbers, or check headings.  Content validation is the
    discovery pipeline's responsibility.

    Routing rules (case-sensitive, matched against the basename):
        ``BUG-*``       → ``<project_root>/bugs/``
        ``PRIORITY-*``  → ``<project_root>/priority/``
        ``v[0-9]*.md``  → ``<project_root>/requirements/``
        anything else   → Refused (``Refused`` exception raised)

    Copy semantics:
        - Source is never moved or deleted.  Copy semantics only.
        - Atomic: write to a temp file in the destination directory (same
          filesystem ensures rename is atomic), chmod 644, then rename into
          place.  Discovery never sees a partial file.
        - Deposited copy always has mode 0o644, regardless of source mode.

    Args:
        ctx:       OpsContext carrying the kanban root path.
        project:   Project name as registered in projects.cfg.
        file_path: Path to the staged file to deposit.  Must exist and be a
                   regular file.

    Returns:
        The absolute ``Path`` of the deposited file on success.

    Raises:
        OpsError:  If the project root does not exist, the source file is
                   missing or not a regular file, or the destination
                   directory does not exist.
        Refused:   If the filename does not match any known intake prefix.
        OpsError:  If the target already exists (no clobber).
        IoError:   If a filesystem error occurs during copy or rename.
    """
    src = Path(file_path)
    project_root = ctx.project_root(project)

    # --- Argument / precondition validation ---
    if not project_root.is_dir():
        raise OpsError(
            f"deposit_intake: project root does not exist or is not a directory: {project_root}"
        )

    if not src.exists():
        raise OpsError(
            f"deposit_intake: source file does not exist: {src}"
        )

    if not src.is_file():
        raise OpsError(
            f"deposit_intake: source is not a regular file: {src}"
        )

    # --- Route by filename prefix ---
    basename = src.name
    dest_subdir: str | None = None
    for pattern, subdir in _INTAKE_ROUTES:
        if pattern.match(basename):
            dest_subdir = subdir
            break

    if dest_subdir is None:
        raise Refused(
            f"deposit_intake: cannot route '{basename}': filename does not match "
            "BUG-*, PRIORITY-*, or vX.Y.Z-* intake patterns"
        )

    dest_dir = project_root / dest_subdir

    # --- Guard: destination directory must exist ---
    if not dest_dir.is_dir():
        raise OpsError(
            f"deposit_intake: destination directory does not exist: {dest_dir}"
        )

    target = dest_dir / basename

    # --- Guard: no clobber ---
    if target.exists():
        raise OpsError(
            f"deposit_intake: target already exists (no clobber): {target}"
        )

    # --- Atomic copy: mktemp in dest dir → copy → chmod 644 → rename ---
    # Using mktemp in the destination directory guarantees same-filesystem
    # placement, which makes the final rename(2) atomic.
    tmp_path: Path | None = None
    try:
        fd, tmp_str = tempfile.mkstemp(prefix=".deposit_intake_tmp_", dir=dest_dir)
        tmp_path = Path(tmp_str)
        os.close(fd)

        # Copy source content to temp file.
        try:
            shutil.copy2(src, tmp_path)
        except OSError as exc:
            raise IoError(
                f"deposit_intake: failed to copy {src} to {dest_dir}: {exc}"
            ) from exc

        # Set mode 644 on the temp file before the atomic rename.
        try:
            tmp_path.chmod(0o644)
        except OSError as exc:
            raise IoError(
                f"deposit_intake: failed to set mode 0o644 on temp file {tmp_path}: {exc}"
            ) from exc

        # Atomic rename: temp file becomes the target.
        try:
            tmp_path.rename(target)
        except OSError as exc:
            raise IoError(
                f"deposit_intake: failed to rename temp file to target {target}: {exc}"
            ) from exc

        # After successful rename, tmp_path no longer exists.
        tmp_path = None

    except (OpsError, IoError, Refused):
        # Clean up temp file on failure before re-raising.
        if tmp_path is not None and tmp_path.exists():
            try:
                tmp_path.unlink()
            except OSError:
                pass
        raise
    except OSError as exc:
        if tmp_path is not None and tmp_path.exists():
            try:
                tmp_path.unlink()
            except OSError:
                pass
        raise IoError(
            f"deposit_intake: filesystem error: {exc}"
        ) from exc

    return target


# ---------------------------------------------------------------------------
# close_item
# ---------------------------------------------------------------------------


def close_item(
    ctx: OpsContext,
    project: str,
    key: str,
    state: str = "done",
    note: str = "",
    dry_run: bool = False,
) -> None:
    """Set the terminal state on a task or intake item identified by key.

    For AGENT TASKS (task folder under tasks/):
        Always writes DONE to ## State, clears ## Blockers to "none" and
        ## Needs Human to "no", and flips the agent queue marker to [x].
        The ``state`` argument is intake vocabulary and is ignored for tasks.

    For INTAKE ITEMS (bug/priority/requirement):
        Sets ## Status to ``state`` (done|wont-do|superseded), optionally
        records ``note`` in a ## Close Note section, and flips the intake
        backlog marker to [x].

    The write is atomic: a temp file in the same directory is written, then
    renamed over the target (os.replace is atomic on POSIX).

    Args:
        ctx:     OpsContext carrying the kanban root path.
        project: Project name as registered in projects.cfg.
        key:     Task folder name or intake file base name.
        state:   Terminal state for intake items.  One of "done", "wont-do",
                 "superseded".  Ignored for agent tasks (tasks always close
                 as DONE).  Defaults to "done".
        note:    Optional free-form note recorded in ## Close Note section
                 of intake items.  Ignored for agent tasks.
        dry_run: When True, report the intended change to stdout without
                 writing anything.  Exits 0.

    Raises:
        OpsError:  If the project root is missing, key resolves to a bad path,
                   or the status.md/intake file cannot be read.
        NotFound:  If no item matches the key.
        Ambiguous: If the key prefix matches multiple items.
        IoError:   If the atomic write fails.
    """
    valid_states = ("done", "wont-do", "superseded")
    if state not in valid_states:
        raise OpsError(
            f"close_item: invalid state {state!r} (valid: {', '.join(valid_states)})"
        )

    result = resolve_item(ctx, project, key)

    if result.item_type == "task":
        task_dir = result.path
        status_file = task_dir / "status.md"

        if not status_file.is_file():
            raise OpsError(
                f"close_item: status.md not found for task {key!r}: {status_file}"
            )

        if dry_run:
            print(
                f"close_item (dry-run): would set task {key!r} state: "
                f"{result.state} -> DONE"
            )
            return

        _write_task_done(status_file, key)

        marker_file = _get_marker_file(
            ctx.project_root(project), result.item_type, result.path
        )
        marker_key = _get_item_key_in_marker(result.item_type, result.path)
        _flip_queue_marker(marker_file, marker_key, "x")
        return

    # --- Intake item branch ---
    item_path = result.path

    if dry_run:
        print(
            f"close_item (dry-run): would set {result.item_type} {key!r} status: "
            f"{result.state} -> {state}"
        )
        if note:
            print(f"close_item (dry-run): would record note: {note}")
        return

    _write_intake_status(item_path, state, note, key)

    marker_file = _get_marker_file(
        ctx.project_root(project), result.item_type, result.path
    )
    marker_key = _get_item_key_in_marker(result.item_type, result.path)
    _flip_backlog_marker(marker_file, marker_key, "x")


# ---------------------------------------------------------------------------
# wontdo_item
# ---------------------------------------------------------------------------


def wontdo_item(ctx: OpsContext, project: str, key: str) -> None:
    """Mark an agent task as WONT-DO.

    Writes WONT-DO to ## State, clears ## Blockers to "none" and ## Needs Human
    to "no", and flips the agent queue marker to [x].  Produces WONT-DO only;
    cannot set DONE through any argument combination.

    The write is atomic: temp file in the same directory, then os.replace.

    Args:
        ctx:     OpsContext carrying the kanban root path.
        project: Project name as registered in projects.cfg.
        key:     Task folder name or identifying prefix.

    Raises:
        OpsError:  If the project root is missing, the resolved item is not a
                   task, or status.md is absent.
        NotFound:  If no task matches the key.
        Ambiguous: If the prefix matches multiple tasks.
        IoError:   If the atomic write fails.
    """
    result = resolve_item(ctx, project, key)

    if result.item_type != "task":
        raise OpsError(
            f"wontdo_item: refused to mark {key!r} as WONT-DO — key resolves to "
            f"{result.item_type}, not a task. wontdo_item is a task-only operation."
        )

    task_dir = result.path
    status_file = task_dir / "status.md"

    if not status_file.is_file():
        raise OpsError(
            f"wontdo_item: status.md not found for task {key!r}: {status_file}"
        )

    _write_task_state(status_file, "WONT-DO", key)

    marker_file = _get_marker_file(
        ctx.project_root(project), result.item_type, result.path
    )
    marker_key = _get_item_key_in_marker(result.item_type, result.path)
    _flip_queue_marker(marker_file, marker_key, "x")


# ---------------------------------------------------------------------------
# delete_item
# ---------------------------------------------------------------------------


# Terminal states that permit deletion without --force.
_TASK_TERMINAL_STATES: frozenset[str] = frozenset({"DONE", "WONT-DO"})
_INTAKE_TERMINAL_STATES: frozenset[str] = frozenset({"done", "DONE", "WONT-DO"})


def delete_item(
    ctx: OpsContext,
    project: str,
    key: str,
    force: bool = False,
) -> None:
    """Delete a task (directory) or intake item (file) identified by key.

    TERMINAL-STATE GUARD (the safety-critical property):
        Deletion is refused unless the item is in a terminal state.
        Tasks: terminal states are DONE and WONT-DO.
        Intake items: terminal state is "done" (also accepts "DONE" and "WONT-DO").
        Pass ``force=True`` to override the guard unconditionally.

    The guard exists because delete is irreversible: deleting an in-flight
    task causes data loss with no recovery path.

    Args:
        ctx:     OpsContext carrying the kanban root path.
        project: Project name as registered in projects.cfg.
        key:     Task folder name or intake file base name.
        force:   When True, skip the terminal-state guard and delete
                 regardless of state.

    Raises:
        OpsError:  If the project root is missing or an argument is invalid.
        NotFound:  If no item matches the key.
        Ambiguous: If the key prefix matches multiple items.
        Refused:   If the guard rejects deletion (state is not terminal and
                   force is False).
        IoError:   If the filesystem deletion fails.
    """
    result = resolve_item(ctx, project, key)

    # --- Terminal-state guard ---
    if not force:
        is_terminal: bool
        if result.item_type == "task":
            is_terminal = result.state in _TASK_TERMINAL_STATES
        else:
            is_terminal = result.state in _INTAKE_TERMINAL_STATES

        if not is_terminal:
            raise Refused(
                f"delete_item: refused to delete {key!r} — state is {result.state!r} "
                f"(not a terminal state).\n"
                f"delete_item: use --force to delete regardless, or move the item to "
                f"DONE or WONT-DO first."
            )

    # --- Resolve marker file and key BEFORE deletion ---
    project_root = ctx.project_root(project)
    marker_file = _get_marker_file(project_root, result.item_type, result.path)
    marker_key = _get_item_key_in_marker(result.item_type, result.path)

    # --- Delete the item ---
    try:
        if result.item_type == "task":
            import shutil as _shutil
            _shutil.rmtree(result.path)
        else:
            result.path.unlink()
    except OSError as exc:
        raise IoError(
            f"delete_item: failed to remove {result.item_type} {result.path}: {exc}"
        ) from exc

    # --- Remove marker line from the queue/backlog file ---
    if result.item_type == "task":
        _remove_queue_marker(marker_file, marker_key)
    else:
        _remove_backlog_marker(marker_file, marker_key)


# ---------------------------------------------------------------------------
# reset_item
# ---------------------------------------------------------------------------


def reset_item(
    ctx: OpsContext,
    project: str,
    key: str,
    keep_artifacts: bool = False,
) -> None:
    """Reset a task or intake item to a re-pickable state.

    DESIGN PHILOSOPHY: operator power tool — assumes intent, refuses only
    filesystem races (WORKING state), warns-and-proceeds on everything else.
    No confirmation prompts; the call is the confirmation.

    RESET MEANS AMNESIA for agent tasks:
        After a task reset, the task is indistinguishable from one the PM
        materializer created moments ago.  The next wake picks it up as if it
        had never run.  No prior summary, no prior artifacts, no repo residue
        an agent could read as dirty knowledge.

    For AGENT TASKS:
        - Raises Refused (exit 2) if state is WORKING (filesystem race guard).
        - Regenerates status.md from the BACKLOG template (template regeneration,
          not surgical edit — this is the amnesia guarantee).
        - Clears artifacts/ and the task's logs/ directory (unless
          keep_artifacts=True; logs are always cleared regardless).
        - Flips the agent queue marker to [ ] in <agent>_backlog.md.
        - Deletes feature/<task-id> branch from the project dev tree (if
          configured in project.cfg).  Warns and proceeds if deletion fails.
        - Runs git worktree prune on the dev tree.
        - Appends one line to <kanban_root>/logs/reset.log.

    For INTAKE ITEMS (bug/priority/requirement):
        - Resets ## Status to "open".
        - Flips the backlog marker to [ ] in the appropriate backlog file.
        - For requirements additionally: clears the PM materializer's
          .materialized.* hash-marker from the PM task's artifacts directory.
        - Warns and proceeds when ## Status is "running" (bundle may be in flight).

    Args:
        ctx:            OpsContext carrying the kanban root path.
        project:        Project name as registered in projects.cfg.
        key:            Task folder name, intake file base name, or key prefix.
        keep_artifacts: When True, preserve artifacts/ for agent task resets.
                        Logs are always cleared.  Ignored for intake resets.

    Raises:
        OpsError:  If the project root is missing or an argument is invalid.
        NotFound:  If no item matches the key.
        Ambiguous: If the key prefix matches multiple items.
        Refused:   If the item is in WORKING state (agent task only).
        IoError:   If a filesystem operation fails.
    """
    result = resolve_item(ctx, project, key)
    project_root = ctx.project_root(project)
    kanban_root = ctx.kanban_root

    if result.item_type == "task":
        _reset_agent_task(
            ctx=ctx,
            project=project,
            project_root=project_root,
            kanban_root=kanban_root,
            task_dir=result.path,
            task_id=result.path.name,
            current_state=result.state,
            keep_artifacts=keep_artifacts,
        )
    elif result.item_type == "bug":
        _reset_intake_item(
            item_path=result.path,
            current_status=result.state,
            backlog_file=_get_marker_file(project_root, result.item_type, result.path),
            item_key=_get_item_key_in_marker(result.item_type, result.path),
        )
    elif result.item_type == "priority":
        _reset_intake_item(
            item_path=result.path,
            current_status=result.state,
            backlog_file=_get_marker_file(project_root, result.item_type, result.path),
            item_key=_get_item_key_in_marker(result.item_type, result.path),
        )
    elif result.item_type == "requirement":
        pm_task_id = _read_md_field(result.path, "PM Task")
        # For requirements, the pm_backlog.md key is the PM task ID (not the
        # requirements filename stem).  We reset ## Status to 'open' atomically,
        # then flip the pm_backlog marker using the PM task ID.
        _reset_intake_item_status_only(
            item_path=result.path,
            current_status=result.state,
        )
        if pm_task_id and pm_task_id.lower() != "none":
            pm_backlog_file = _get_marker_file(project_root, result.item_type, result.path)
            _flip_backlog_marker(pm_backlog_file, pm_task_id, " ")
            _reset_requirement_extras(
                kanban_root=kanban_root,
                project=project,
                pm_task_id=pm_task_id,
            )
    else:
        raise OpsError(
            f"reset_item: unexpected resolved item type {result.item_type!r} for key {key!r}"
        )


# ---------------------------------------------------------------------------
# Internal reset helpers
# ---------------------------------------------------------------------------


def _reset_agent_task(
    ctx: OpsContext,
    project: str,
    project_root: Path,
    kanban_root: Path,
    task_dir: Path,
    task_id: str,
    current_state: str,
    keep_artifacts: bool,
) -> None:
    """Execute the agent-task reset procedure.

    Refuses if current_state is WORKING.  Otherwise: regenerates status.md,
    clears artifacts/ and logs/, flips the queue marker, deletes the feature
    branch from the dev tree, runs git worktree prune, and appends to the
    reset log.

    Args:
        ctx:           OpsContext (used to construct dev-tree paths via config).
        project:       Project name.
        project_root:  Absolute path to the project root.
        kanban_root:   Absolute path to the kanban root.
        task_dir:      Absolute path to the task folder.
        task_id:       Task folder basename (e.g. CODER-20260607-001-slug).
        current_state: Current ## State value from status.md.
        keep_artifacts: When True, skip clearing artifacts/ (logs always cleared).

    Raises:
        Refused:   If current_state is "WORKING".
        IoError:   If the status.md write or directory-clear fails.
    """
    # --- GUARD: refuse on WORKING (filesystem race) ---
    if current_state == "WORKING":
        raise Refused(
            f"reset_item: REFUSED — task {task_id!r} is in state WORKING.\n"
            f"  An agent process may currently hold this task.\n"
            f"  Wait for the agent to finish or investigate before resetting."
        )

    # --- Read Participant and Role from README.md ---
    readme = task_dir / "README.md"
    participant = "Claude"
    role = task_id.split("-")[0]
    if readme.is_file():
        participant_from_readme = _read_md_field(readme, "Owner") or _read_md_field(readme, "Participant")
        if participant_from_readme:
            participant = participant_from_readme
        role_from_readme = _read_md_field(readme, "Role")
        if role_from_readme:
            role = role_from_readme

    # --- Step 1: Regenerate status.md (amnesia guarantee) ---
    status_file = task_dir / "status.md"
    fresh_status = _generate_status_md(task_id, participant, role)
    _atomic_write(status_file, fresh_status)

    # --- Step 2: Clear artifacts/ and task logs/ ---
    artifacts_dir = task_dir / "artifacts"
    task_logs_dir = task_dir / "logs"

    if not keep_artifacts:
        _clear_directory_contents(artifacts_dir)

    _clear_directory_contents(task_logs_dir)

    # --- Step 3: Flip queue marker to [ ] ---
    marker_file = _get_marker_file(project_root, "task", task_dir)
    marker_key = _get_item_key_in_marker("task", task_dir)
    _flip_queue_marker(marker_file, marker_key, " ")

    # --- Step 4: Dev tree operations (feature branch + worktree prune) ---
    dev_tree = _read_dev_tree_path(project_root)
    branch_prefix = _read_branch_prefix(project_root)
    feature_branch = f"{branch_prefix}feature/{task_id}"

    if dev_tree is not None and dev_tree.is_dir():
        _git_delete_feature_branch(dev_tree, feature_branch)
        _git_worktree_prune(dev_tree)

    # --- Step 5: Log to ops reset record ---
    _append_reset_log(kanban_root, task_id)


def _reset_intake_item(
    item_path: Path,
    current_status: str,
    backlog_file: Path,
    item_key: str,
) -> None:
    """Reset an intake item (bug/priority) to 'open' status.

    Warns to stderr (via print) when current_status is 'running' but proceeds
    anyway.  Sets ## Status to 'open' atomically, then flips the backlog marker
    to [ ] using item_key.

    Args:
        item_path:      Path to the intake .md file.
        current_status: Current ## Status value.
        backlog_file:   Path to the backlog .md file for marker flip.
        item_key:       Key as it appears in the backlog file.

    Raises:
        OpsError: If the ## Status heading is absent.
        IoError:  If the atomic write fails.
    """
    _reset_intake_item_status_only(item_path, current_status)
    _flip_backlog_marker(backlog_file, item_key, " ")


def _reset_intake_item_status_only(item_path: Path, current_status: str) -> None:
    """Reset an intake item's ## Status to 'open' without touching any marker file.

    Used for requirements, where the backlog marker is keyed by PM task ID
    (not the requirements filename stem) and must be flipped by the caller
    with the correct key.

    Args:
        item_path:      Path to the intake .md file.
        current_status: Current ## Status value (for the running-state warning).

    Raises:
        OpsError: If the ## Status heading is absent.
        IoError:  If the atomic write fails.
    """
    if current_status == "running":
        import sys as _sys
        print(
            f"WARNING: intake item has ## Status: running (bundle RC may be in flight).\n"
            f"  Resetting now may cause double-handling on the next discovery idle tick.\n"
            f"  Proceeding with reset anyway.",
            file=_sys.stderr,
        )

    _write_intake_status_open(item_path)


def _reset_requirement_extras(
    kanban_root: Path,
    project: str,
    pm_task_id: str,
) -> None:
    """Clear the PM materializer's idempotence hash-marker for a requirement.

    Finds the PM task folder under <kanban_root>/projects/<project>/tasks/<pm_task_id>/
    and deletes any .materialized.* files in its artifacts/ directory.

    This allows the PM materializer to re-decompose the requirement on the
    next pickup without skipping due to the "already materialized" idempotence
    check.

    Prints informational messages to stdout about the hash-marker deletion.

    Args:
        kanban_root: Absolute path to the kanban root.
        project:     Project name.
        pm_task_id:  PM task ID read from ## PM Task in the requirements file.
    """
    pm_task_dir = kanban_root / "projects" / project / "tasks" / pm_task_id
    pm_artifacts = pm_task_dir / "artifacts"
    if not pm_artifacts.is_dir():
        print(
            f"hash-marker: PM task artifacts dir not found: {pm_artifacts}\n"
            f"  (PM may not have run yet, or task folder was already cleared)"
        )
        return
    cleared_count = 0
    for marker in pm_artifacts.glob(".materialized.*"):
        if marker.is_file():
            try:
                marker.unlink()
                print(f"hash-marker: deleted '{marker.name}' from {pm_artifacts}")
                cleared_count += 1
            except OSError:
                pass
    if cleared_count == 0:
        print(
            f"hash-marker: no .materialized.* marker found in {pm_artifacts} (already absent)"
        )


def _generate_status_md(task_id: str, participant: str, role: str) -> str:
    """Return a freshly-generated status.md content string (State=BACKLOG).

    This is the amnesia guarantee: template regeneration, not surgical edit.
    Mirrors the _reset_generate_status_md bash function in lib/reset.sh.

    Args:
        task_id:     Full task ID (e.g. CODER-20260607-001-slug).
        participant: Task owner/participant name.
        role:        Agent role (e.g. CODER, PM, WRITER).

    Returns:
        A UTF-8 string suitable for writing to status.md.
    """
    return (
        f"# Status\n"
        f"\n"
        f"## Task\n"
        f"{task_id}\n"
        f"\n"
        f"## Participant\n"
        f"{participant}\n"
        f"\n"
        f"## Role\n"
        f"{role}\n"
        f"\n"
        f"## State\n"
        f"BACKLOG\n"
        f"\n"
        f"## Summary\n"
        f"Task created by PM Agent. Waiting for {participant} to pull from backlog and begin work.\n"
        f"\n"
        f"## Artifacts\n"
        f"none\n"
        f"\n"
        f"## Blockers\n"
        f"none\n"
        f"\n"
        f"## Needs Human\n"
        f"no\n"
        f"\n"
        f"## Next Recommended Step\n"
        f"{participant} should read task README.md and begin work. Move to WORKING when starting.\n"
        f"\n"
        f"## Instruction Conflicts\n"
        f"none\n"
    )


def _clear_directory_contents(directory: Path) -> None:
    """Remove all contents of directory (files and subdirectories).

    Idempotent: no error if directory is absent or already empty.

    Args:
        directory: Path to the directory to clear.
    """
    if not directory.is_dir():
        return
    import shutil as _shutil
    for entry in directory.iterdir():
        if entry.is_dir():
            _shutil.rmtree(entry)
        else:
            try:
                entry.unlink()
            except OSError:
                pass


def _read_md_field(file_path: Path, header: str) -> str:
    """Read the first non-blank value line following a ## header in a markdown file.

    Returns the value stripped of surrounding whitespace, or an empty string
    when the header is not found or has no value.

    Args:
        file_path: Path to the .md file.
        header:    Section heading name (without the ## prefix).

    Returns:
        The first non-blank, non-heading line after the header, or ''.
    """
    if not file_path.is_file():
        return ""
    try:
        text = file_path.read_text(encoding="utf-8")
    except OSError:
        return ""
    pattern = re.compile(
        r"^##\s+" + re.escape(header) + r"\s*\n(.*?)(?=\n##|\Z)",
        re.M | re.S | re.IGNORECASE,
    )
    m = pattern.search(text)
    if not m:
        return ""
    for line in m.group(1).splitlines():
        stripped = line.strip()
        if stripped:
            return stripped
    return ""


def _write_intake_status_open(item_path: Path) -> None:
    """Set ## Status to 'open' in an intake .md file atomically.

    Args:
        item_path: Path to the intake .md file.

    Raises:
        OpsError: If the ## Status heading is absent.
        IoError:  If the atomic write fails.
    """
    try:
        text = item_path.read_text(encoding="utf-8")
    except OSError as exc:
        raise OpsError(
            f"reset_item: failed to read {item_path}: {exc}"
        ) from exc

    new_text, n_status = re.subn(
        r"(## Status\n(?:[ \t]*\n)*)([^\n]+)",
        lambda m: m.group(1) + "open",
        text,
        count=1,
    )

    if n_status == 0:
        raise OpsError(
            f"reset_item: no ## Status heading found in {item_path}; left untouched"
        )

    if not new_text.endswith("\n"):
        new_text += "\n"

    _atomic_write(item_path, new_text)


def _read_project_cfg_value(project_root: Path, section: str, key: str) -> str:
    """Read a single value from project.cfg (INI format) or PROJECT.cfg (legacy).

    Tries project.cfg first, then PROJECT.cfg.  Returns '' when neither file
    exists or the key is absent.

    Args:
        project_root: Absolute path to the project root.
        section:      INI section name (e.g. "project").
        key:          Key name within the section (e.g. "dev_tree_path").

    Returns:
        The value string, or '' when not found.
    """
    for cfg_name in ("project.cfg", "PROJECT.cfg"):
        cfg_path = project_root / cfg_name
        if not cfg_path.is_file():
            continue
        try:
            text = cfg_path.read_text(encoding="utf-8")
        except OSError:
            continue
        in_section = False
        section_re = re.compile(r"^\[" + re.escape(section) + r"\]", re.IGNORECASE)
        any_section_re = re.compile(r"^\[")
        key_re = re.compile(r"^\s*" + re.escape(key) + r"\s*=\s*(.+)", re.IGNORECASE)
        for line in text.splitlines():
            if section_re.match(line):
                in_section = True
                continue
            if in_section:
                if any_section_re.match(line):
                    break
                m = key_re.match(line)
                if m:
                    # Strip surrounding double-quotes (bash convention).
                    val = m.group(1).strip().strip('"')
                    return val
    return ""


def _read_dev_tree_path(project_root: Path) -> "Path | None":
    """Return the dev_tree_path for the project, or None if not configured.

    Args:
        project_root: Absolute path to the project root.

    Returns:
        Absolute Path to the dev tree, or None when unset or empty.
    """
    val = _read_project_cfg_value(project_root, "project", "dev_tree_path")
    if val:
        return Path(val)
    return None


def _read_branch_prefix(project_root: Path) -> str:
    """Return the branch_prefix from project.cfg, or '' when unset.

    Args:
        project_root: Absolute path to the project root.

    Returns:
        Branch prefix string (e.g. 'ai_'), or '' for the default (no prefix).
    """
    return _read_project_cfg_value(project_root, "project", "branch_prefix")


def _git_delete_feature_branch(dev_tree: Path, feature_branch: str) -> None:
    """Delete a feature branch from the dev tree, if it exists.

    Tries -d (safe) first; falls back to -D (force) with a warning to stderr
    if the branch is not fully merged.  Both outcomes are non-fatal — warns and
    proceeds if deletion fails.

    Args:
        dev_tree:       Absolute path to the git repository.
        feature_branch: Branch name to delete (e.g. "feature/CODER-...").
    """
    import subprocess as _sp
    import sys as _sys

    # Check if branch exists.
    check = _sp.run(
        ["git", "-C", str(dev_tree), "rev-parse", "--verify",
         f"refs/heads/{feature_branch}"],
        capture_output=True,
    )
    if check.returncode != 0:
        return  # Branch does not exist; nothing to do.

    # Check if branch is merged into any RC branch before deleting.
    _warn_if_merged(dev_tree, feature_branch)

    # Try safe delete first.
    r = _sp.run(
        ["git", "-C", str(dev_tree), "branch", "-d", feature_branch],
        capture_output=True,
    )
    if r.returncode == 0:
        return

    # Fall back to force-delete with warning.
    print(
        f"WARNING: feature branch '{feature_branch}' could not be deleted with -d "
        f"(not fully merged); force-deleting with -D.",
        file=_sys.stderr,
    )
    _sp.run(
        ["git", "-C", str(dev_tree), "branch", "-D", feature_branch],
        capture_output=True,
    )


def _warn_if_merged(dev_tree: Path, feature_branch: str) -> None:
    """Print a warning to stderr if feature_branch is already merged into an RC branch.

    Non-fatal: warns and returns regardless.

    Args:
        dev_tree:       Absolute path to the git repository.
        feature_branch: Branch name to check.
    """
    import subprocess as _sp
    import sys as _sys

    # List all RC branches.
    r = _sp.run(
        ["git", "-C", str(dev_tree), "branch", "--list", "rc/*"],
        capture_output=True,
        text=True,
    )
    if r.returncode != 0:
        return

    for line in r.stdout.splitlines():
        candidate = line.strip().lstrip("* ").strip()
        if not candidate:
            continue
        is_ancestor = _sp.run(
            ["git", "-C", str(dev_tree), "merge-base", "--is-ancestor",
             f"refs/heads/{feature_branch}", f"refs/heads/{candidate}"],
            capture_output=True,
        )
        if is_ancestor.returncode == 0:
            print(
                f"WARNING: feature branch '{feature_branch}' is already merged into "
                f"'{candidate}'.\n"
                f"  Re-running this task will produce a second merge commit on that branch.\n"
                f"  Proceeding with reset anyway.",
                file=_sys.stderr,
            )
            return


def _git_worktree_prune(dev_tree: Path) -> None:
    """Run git worktree prune on the dev tree, ignoring errors.

    Args:
        dev_tree: Absolute path to the git repository.
    """
    import subprocess as _sp
    _sp.run(
        ["git", "-C", str(dev_tree), "worktree", "prune"],
        capture_output=True,
    )


def _append_reset_log(kanban_root: Path, task_id: str) -> None:
    """Append one line to the operator reset log.

    The log lives at <kanban_root>/logs/reset.log.  This path is outside the
    per-project task logs that agents read, so it is not injected into agent
    prompts.

    Args:
        kanban_root: Absolute path to the kanban root.
        task_id:     Task ID to record in the log.
    """
    import datetime as _dt
    ops_log = kanban_root / "logs" / "reset.log"
    try:
        ops_log.parent.mkdir(parents=True, exist_ok=True)
        ts = _dt.datetime.now().astimezone().isoformat(timespec="seconds")
        with ops_log.open("a", encoding="utf-8") as fh:
            fh.write(f"reset: {task_id} reset by operator at {ts}\n")
    except OSError:
        pass  # Non-fatal: log write failure does not abort the reset.


# ---------------------------------------------------------------------------
# Internal state-write helpers (atomic: temp+rename)
# ---------------------------------------------------------------------------


def _write_task_done(status_file: Path, key: str) -> None:
    """Write DONE to ## State, clear ## Blockers and ## Needs Human atomically.

    Args:
        status_file: Path to the task's status.md.
        key:         Task key (used in error messages only).

    Raises:
        OpsError: If ## State heading is absent.
        IoError:  If the atomic write fails.
    """
    try:
        text = status_file.read_text(encoding="utf-8")
    except OSError as exc:
        raise OpsError(
            f"close_item: failed to read {status_file}: {exc}"
        ) from exc

    new_text, n_state = re.subn(
        r"(## State\n(?:[ \t]*\n)*)([^\n]+)",
        r"\1DONE",
        text,
        count=1,
    )

    if n_state == 0:
        raise OpsError(
            f"close_item: no ## State heading found in {status_file}; left untouched"
        )

    new_text, _ = re.subn(
        r"(## Blockers\n(?:[ \t]*\n)*)([^\n]+)",
        r"\1none",
        new_text,
        count=1,
    )
    new_text, _ = re.subn(
        r"(## Needs Human\n(?:[ \t]*\n)*)([^\n]+)",
        r"\1no",
        new_text,
        count=1,
    )

    if not new_text.endswith("\n"):
        new_text += "\n"

    _atomic_write(status_file, new_text)


def _write_task_state(status_file: Path, state: str, key: str) -> None:
    """Write STATE to ## State, clear ## Blockers and ## Needs Human atomically.

    Used by wontdo_item to write WONT-DO.

    Args:
        status_file: Path to the task's status.md.
        state:       New state string to write (e.g. "WONT-DO").
        key:         Task key (used in error messages only).

    Raises:
        OpsError: If ## State heading is absent.
        IoError:  If the atomic write fails.
    """
    try:
        text = status_file.read_text(encoding="utf-8")
    except OSError as exc:
        raise OpsError(
            f"wontdo_item: failed to read {status_file}: {exc}"
        ) from exc

    new_text, n_state = re.subn(
        r"(## State\n(?:[ \t]*\n)*)([^\n]+)",
        rf"\g<1>{state}",
        text,
        count=1,
    )

    if n_state == 0:
        raise OpsError(
            f"wontdo_item: no ## State heading found in {status_file}; left untouched"
        )

    new_text, _ = re.subn(
        r"(## Blockers\n(?:[ \t]*\n)*)([^\n]+)",
        r"\1none",
        new_text,
        count=1,
    )
    new_text, _ = re.subn(
        r"(## Needs Human\n(?:[ \t]*\n)*)([^\n]+)",
        r"\1no",
        new_text,
        count=1,
    )

    if not new_text.endswith("\n"):
        new_text += "\n"

    _atomic_write(status_file, new_text)


def _write_intake_status(item_path: Path, state: str, note: str, key: str) -> None:
    """Write STATE to ## Status and optionally record NOTE in ## Close Note.

    The write is atomic (temp file in the same directory + os.replace).

    Args:
        item_path: Path to the intake .md file.
        state:     New status string (e.g. "done", "wont-do", "superseded").
        note:      Optional close note text.  Empty string means no change
                   to ## Close Note.
        key:       Item key (used in error messages only).

    Raises:
        OpsError: If ## Status heading is absent.
        IoError:  If the atomic write fails.
    """
    try:
        text = item_path.read_text(encoding="utf-8")
    except OSError as exc:
        raise OpsError(
            f"close_item: failed to read {item_path}: {exc}"
        ) from exc

    new_text, n_status = re.subn(
        r"(## Status\n(?:[ \t]*\n)*)([^\n]+)",
        lambda m: m.group(1) + state,
        text,
        count=1,
    )

    if n_status == 0:
        raise OpsError(
            f"close_item: no ## Status heading found in {item_path}; left untouched"
        )

    if note:
        if re.search(r"## Close Note\n", new_text):
            new_text, _ = re.subn(
                r"(## Close Note\n(?:[ \t]*\n)*)([^\n]+)",
                lambda m: m.group(1) + note,
                new_text,
                count=1,
            )
        else:
            new_text, _ = re.subn(
                r"(## Status\n(?:[ \t]*\n)*[^\n]+)",
                lambda m: m.group(0) + "\n\n## Close Note\n" + note,
                new_text,
                count=1,
            )

    if not new_text.endswith("\n"):
        new_text += "\n"

    _atomic_write(item_path, new_text)


def _atomic_write(target: Path, content: str) -> None:
    """Write content to target atomically using a temp file in the same directory.

    Creates a temp file in target.parent (same filesystem), writes content,
    then calls os.replace which is atomic on POSIX.

    Args:
        target:  Destination path.
        content: UTF-8 string to write.

    Raises:
        IoError: If the mkstemp, write, or rename step fails.
    """
    tmp_path: Path | None = None
    try:
        import tempfile as _tempfile
        fd, tmp_str = _tempfile.mkstemp(
            prefix=".write_tmp_", dir=target.parent
        )
        tmp_path = Path(tmp_str)
        os.close(fd)
        tmp_path.write_text(content, encoding="utf-8")
        os.replace(tmp_path, target)
        tmp_path = None
    except OSError as exc:
        raise IoError(
            f"_atomic_write: failed writing {target}: {exc}"
        ) from exc
    finally:
        if tmp_path is not None:
            try:
                tmp_path.unlink()
            except OSError:
                pass


# ---------------------------------------------------------------------------
# Internal marker-sync helpers
#
# These mirror the bash _sync_flip_queue_marker, _sync_flip_backlog_marker,
# _sync_remove_queue_marker, _sync_remove_backlog_marker, _op_get_marker_file,
# and _op_get_item_key_in_marker helpers in operator_ops.sh.
#
# All helpers are operate-if-present: missing file is a no-op.
# ---------------------------------------------------------------------------


def _get_marker_file(
    project_root: Path,
    item_type: str,
    item_path: Path,
) -> Path:
    """Return the path to the queue/backlog file for the given item.

    Routing (mirrors _op_get_marker_file in operator_ops.sh):
        task        → PROJECT_ROOT/tasks/queues/<agent>_backlog.md
        bug         → PROJECT_ROOT/tasks/queues/bug_backlog.md
        priority    → PROJECT_ROOT/tasks/queues/priority_backlog.md
        requirement → PROJECT_ROOT/tasks/queues/pm_backlog.md

    The agent for a task is derived from the task folder basename:
    CODER-20260622-001-slug → coder.

    Args:
        project_root: Absolute path to the project root directory.
        item_type:    One of "task", "bug", "priority", "requirement".
        item_path:    Absolute path to the item (directory for tasks, file for intake).

    Returns:
        Path to the marker file (may not exist; callers check existence).
    """
    queues_dir = project_root / "tasks" / "queues"

    if item_type == "task":
        basename = item_path.name
        # ROLE-YYYYMMDD-NNN-slug → ROLE → lowercase
        agent_upper = basename.split("-")[0]
        agent_lower = agent_upper.lower()
        return queues_dir / f"{agent_lower}_backlog.md"
    elif item_type == "bug":
        return queues_dir / "bug_backlog.md"
    elif item_type == "priority":
        return queues_dir / "priority_backlog.md"
    elif item_type == "requirement":
        return queues_dir / "pm_backlog.md"
    else:
        raise OpsError(
            f"_get_marker_file: unknown item type {item_type!r}"
        )


def _get_item_key_in_marker(item_type: str, item_path: Path) -> str:
    """Return the key as it appears in the queue/backlog file.

    For tasks:   the full task folder basename
    For intake:  the file basename without .md extension

    Args:
        item_type: One of "task", "bug", "priority", "requirement".
        item_path: Absolute path to the item.

    Returns:
        The key string used in the marker file.
    """
    if item_type == "task":
        return item_path.name
    else:
        return item_path.stem


def _flip_queue_marker(queue_file: Path, item_key: str, target_char: str) -> None:
    """Flip the marker for item_key in a task queue file to target_char.

    Matches lines of the form:  <ws>- [<marker>] <ws><item_key><word_boundary>
    Operates-if-present: returns without error if the file does not exist.

    Args:
        queue_file:  Path to the queue .md file.
        item_key:    The task folder name as it appears in the queue line.
        target_char: The new marker character (e.g. "x" to close, " " to reset).
    """
    if not queue_file.is_file():
        return

    try:
        text = queue_file.read_text(encoding="utf-8")
    except OSError:
        return

    entry_re = re.compile(
        rf"^(\s*-\s+\[)[^\]]*(\]\s+{re.escape(item_key)})(\b.*)?$",
        re.MULTILINE,
    )
    new_text = entry_re.sub(
        lambda m: m.group(1) + target_char + m.group(2) + (m.group(3) or ""),
        text,
        count=1,
    )

    if new_text != text:
        _atomic_write(queue_file, new_text)


def _flip_backlog_marker(backlog_file: Path, item_key: str, target_char: str) -> None:
    """Flip the marker for item_key in an intake backlog file to target_char.

    Case-insensitive match.  Operates-if-present.

    Args:
        backlog_file: Path to the intake backlog .md file.
        item_key:     The intake item key as it appears in the backlog line.
        target_char:  The new marker character.
    """
    if not backlog_file.is_file():
        return

    try:
        text = backlog_file.read_text(encoding="utf-8")
    except OSError:
        return

    entry_re = re.compile(
        rf"^(\s*-\s+\[)[^\]]*(\]\s+{re.escape(item_key)})(\b.*)?$",
        re.MULTILINE | re.IGNORECASE,
    )
    new_text = entry_re.sub(
        lambda m: m.group(1) + target_char + m.group(2) + (m.group(3) or ""),
        text,
        count=1,
    )

    if new_text != text:
        _atomic_write(backlog_file, new_text)


def _remove_queue_marker(queue_file: Path, item_key: str) -> None:
    """Remove the marker line for item_key from a task queue file.

    Used by delete_item: after the task is gone there must be no dangling line.
    Operates-if-present.

    Args:
        queue_file: Path to the queue .md file.
        item_key:   The task folder name as it appears in the queue line.
    """
    if not queue_file.is_file():
        return

    try:
        text = queue_file.read_text(encoding="utf-8")
    except OSError:
        return

    entry_re = re.compile(
        rf"^\s*-\s+\[[^\]]*\]\s+{re.escape(item_key)}\b.*\n?",
        re.MULTILINE,
    )
    new_text = entry_re.sub("", text, count=1)

    if new_text != text:
        _atomic_write(queue_file, new_text)


def _remove_backlog_marker(backlog_file: Path, item_key: str) -> None:
    """Remove the marker line for item_key from an intake backlog file.

    Used by delete_item on bug/priority/requirement items.  Operates-if-present.

    Args:
        backlog_file: Path to the intake backlog .md file.
        item_key:     The intake item key as it appears in the backlog line.
    """
    if not backlog_file.is_file():
        return

    try:
        text = backlog_file.read_text(encoding="utf-8")
    except OSError:
        return

    entry_re = re.compile(
        rf"^\s*-\s+\[[^\]]*\]\s+{re.escape(item_key)}\b.*\n?",
        re.MULTILINE | re.IGNORECASE,
    )
    new_text = entry_re.sub("", text, count=1)

    if new_text != text:
        _atomic_write(backlog_file, new_text)


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _require_dir(path: Path, op: str) -> None:
    """Raise OpsError if path is not an existing directory.

    Args:
        path: Path to validate.
        op:   Operation name for the error message.

    Raises:
        OpsError: If path does not exist or is not a directory.
    """
    if not path.is_dir():
        raise OpsError(
            f"{op}: path does not exist or is not a directory: {path}"
        )


# ---------------------------------------------------------------------------
# CLI entrypoint (bash delegation shim)
# ---------------------------------------------------------------------------


def _cli_main(argv: list[str] | None = None) -> int:
    """CLI shim for bash wrapper delegation.

    Canonical invocation is via the package entry point:
        python3 -m pgai_agent_kanban.ops halt        PROJECT_ROOT
        python3 -m pgai_agent_kanban.ops unhalt       PROJECT_ROOT
        python3 -m pgai_agent_kanban.ops halt_after   PROJECT_ROOT [TOKEN]
        python3 -m pgai_agent_kanban.ops halt_global  KANBAN_ROOT
        python3 -m pgai_agent_kanban.ops unhalt_global KANBAN_ROOT
        python3 -m pgai_agent_kanban.ops close_item  PROJECT_ROOT KEY [STATE] [NOTE] [DRY_RUN]
        python3 -m pgai_agent_kanban.ops wontdo_item PROJECT_ROOT KEY
        python3 -m pgai_agent_kanban.ops delete_item PROJECT_ROOT KEY [FORCE]
        python3 -m pgai_agent_kanban.ops reset_item  PROJECT_ROOT KEY [KEEP_ARTIFACTS]

    Exit codes:
        0  Success
        1  Error / argument error (printed to stderr)
        2  Ambiguous key (multiple matches); also used by delete_item for guard refusal;
           also used by reset_item for WORKING state refusal
        3  Not found
        4  State mutation failed / I/O error
    """
    args = argv if argv is not None else sys.argv[1:]

    if not args:
        print(
            "write: VERB argument is required\n"
            "  verbs: halt, unhalt, halt_after, halt_global, unhalt_global,"
            " deposit_intake, close_item, wontdo_item, delete_item, reset_item",
            file=sys.stderr,
        )
        return 1

    verb = args[0]
    rest = args[1:]

    try:
        if verb == "halt":
            if len(rest) != 1:
                print("write halt: PROJECT_ROOT argument is required", file=sys.stderr)
                return 1
            project_root = Path(rest[0])
            ctx, project = _ctx_for_project_root(project_root)
            halt(ctx, project)

        elif verb == "unhalt":
            if len(rest) != 1:
                print("write unhalt: PROJECT_ROOT argument is required", file=sys.stderr)
                return 1
            project_root = Path(rest[0])
            ctx, project = _ctx_for_project_root(project_root)
            unhalt(ctx, project)

        elif verb == "halt_after":
            if len(rest) < 1:
                print("write halt_after: PROJECT_ROOT argument is required", file=sys.stderr)
                return 1
            project_root = Path(rest[0])
            token = rest[1] if len(rest) > 1 else "rc"
            ctx, project = _ctx_for_project_root(project_root)
            halt_after(ctx, project, token)

        elif verb == "halt_global":
            if len(rest) != 1:
                print("write halt_global: KANBAN_ROOT argument is required", file=sys.stderr)
                return 1
            kanban_root = Path(rest[0])
            ctx = OpsContext(kanban_root=kanban_root)
            halt_global(ctx)

        elif verb == "unhalt_global":
            if len(rest) != 1:
                print("write unhalt_global: KANBAN_ROOT argument is required", file=sys.stderr)
                return 1
            kanban_root = Path(rest[0])
            ctx = OpsContext(kanban_root=kanban_root)
            unhalt_global(ctx)

        elif verb == "deposit_intake":
            if len(rest) != 2:
                print(
                    "write deposit_intake: PROJECT_ROOT and FILE_PATH arguments are required",
                    file=sys.stderr,
                )
                return 1
            project_root = Path(rest[0])
            file_path = Path(rest[1])
            ctx, project = _ctx_for_project_root(project_root)
            deposited = deposit_intake(ctx, project, file_path)
            # Print the deposited path to stdout (Seam 1: bash wrapper captures this).
            print(deposited)

        elif verb == "close_item":
            if len(rest) < 2:
                print(
                    "write close_item: PROJECT_ROOT and KEY arguments are required",
                    file=sys.stderr,
                )
                return 1
            project_root = Path(rest[0])
            key = rest[1]
            state = rest[2] if len(rest) > 2 else "done"
            note = rest[3] if len(rest) > 3 else ""
            dry_run = len(rest) > 4 and rest[4] == "1"
            ctx, project = _ctx_for_project_root(project_root)
            close_item(ctx, project, key, state=state, note=note, dry_run=dry_run)

        elif verb == "wontdo_item":
            if len(rest) < 2:
                print(
                    "write wontdo_item: PROJECT_ROOT and KEY arguments are required",
                    file=sys.stderr,
                )
                return 1
            project_root = Path(rest[0])
            key = rest[1]
            ctx, project = _ctx_for_project_root(project_root)
            wontdo_item(ctx, project, key)

        elif verb == "delete_item":
            if len(rest) < 2:
                print(
                    "write delete_item: PROJECT_ROOT and KEY arguments are required",
                    file=sys.stderr,
                )
                return 1
            project_root = Path(rest[0])
            key = rest[1]
            force = len(rest) > 2 and rest[2] == "1"
            ctx, project = _ctx_for_project_root(project_root)
            delete_item(ctx, project, key, force=force)

        elif verb == "reset_item":
            if len(rest) < 2:
                print(
                    "write reset_item: PROJECT_ROOT and KEY arguments are required",
                    file=sys.stderr,
                )
                return 1
            project_root = Path(rest[0])
            key = rest[1]
            keep_artifacts = len(rest) > 2 and rest[2] == "1"
            ctx, project = _ctx_for_project_root(project_root)
            reset_item(ctx, project, key, keep_artifacts=keep_artifacts)

        else:
            print(
                f"write: unknown verb {verb!r}\n"
                "  verbs: halt, unhalt, halt_after, halt_global, unhalt_global,"
                " deposit_intake, close_item, wontdo_item, delete_item, reset_item",
                file=sys.stderr,
            )
            return 1

    except Ambiguous as exc:
        print(exc, file=sys.stderr)
        return 2
    except NotFound as exc:
        print(exc, file=sys.stderr)
        return 3
    except Refused as exc:
        # For deposit_intake: Refused = routing refused (rc=2)
        # For delete_item: Refused = guard refused (rc=2)
        print(exc, file=sys.stderr)
        return 2
    except IoError as exc:
        print(exc, file=sys.stderr)
        return 4
    except OpsError as exc:
        # Distinguish no-clobber (return 3) from other OpsErrors (return 1).
        msg = str(exc)
        if "already exists (no clobber)" in msg:
            print(exc, file=sys.stderr)
            return 3
        print(exc, file=sys.stderr)
        return 1

    return 0


def _ctx_for_project_root(project_root: Path) -> tuple[OpsContext, str]:
    """Derive an OpsContext and project name from an absolute project root path.

    Expects PROJECT_ROOT to follow the layout:
        <kanban_root>/projects/<project>/

    Args:
        project_root: Absolute path to the project root directory.

    Returns:
        Tuple of (OpsContext, project_name).

    Raises:
        OpsError: If project_root does not match the expected layout.
    """
    project_root = project_root.resolve()
    # Derive: kanban_root = project_root.parent.parent, project = project_root.name
    # Validate: <kanban_root>/projects/<project> == project_root
    project_name = project_root.name
    kanban_root = project_root.parent.parent
    ctx = OpsContext(kanban_root=kanban_root)
    derived = ctx.project_root(project_name).resolve()
    if derived != project_root:
        raise OpsError(
            f"write: cannot derive project from path {project_root} "
            f"(expected layout <kanban_root>/projects/<project>/)"
        )
    return ctx, project_name


if __name__ == "__main__":
    sys.exit(_cli_main())
