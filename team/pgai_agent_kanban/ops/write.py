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

Sentinel provenance:

    halt, halt_global, halt_after and defer each append one record to
    <kanban_root>/logs/sentinel-writes.log after a write that changed their
    sentinel (none for an idempotent no-op).  The record is one line of
    tab-separated key=value fields -- time, epoch, op, path, sha256, entry
    (the writing process's command line), pid, ppid, parent (the parent's
    command line, e.g. the halt-after.sh that called the CLI shim), user,
    test (the pytest node id when written under pytest, else "-").  The
    format is defined once in pgai_agent_kanban.lib.sentinel_provenance; the
    sentinel's own bytes are unchanged.  team/scripts/lib/
    sentinel_leak_check.sh reads the log to tell an operator or agent write
    made during a gated suite from a sentinel the suite left behind.  A
    record that cannot be appended is narrated as one stderr warning and
    never fails the write or changes its exit code.

Functions:

    halt(ctx, project)
        Create PROJECT_ROOT/HALT.  Idempotent.  Records provenance when it
        creates the file.

    unhalt(ctx, project)
        Remove PROJECT_ROOT/HALT.  Idempotent.

    halt_after(ctx, project, token="rc")
        Write TOKEN to PROJECT_ROOT/HALT-AFTER.  Overwrites any existing sentinel.
        Records provenance when the file's content changed.

    halt_global(ctx)
        Create KANBAN_ROOT/HALT.  Idempotent.  Records provenance when it
        creates the file.

    unhalt_global(ctx)
        Remove KANBAN_ROOT/HALT.  Idempotent.

    defer(ctx, project, until)
        Arm the DEFER-LOW-BUGS campaign file for a project, until the named
        release.  Validates UNTIL first, then resolves PROJECT exactly as
        resolve_project_name does.  Idempotent for the same target; a
        different target replaces the file and records provenance.
        Returns a CampaignChange.

    undefer(ctx, project)
        Remove a project's DEFER-LOW-BUGS campaign file.  Idempotent.
        Returns a CampaignChange.

    set_version_ceiling(ctx, project, show=False, minor=None, major=None,
                        no_minor=False, no_major=False, dry_run=False)
        Show or set max_minor / max_major in the project's project.cfg
        [versioning] section; --no-minor / --no-major write the explicit
        no-ceiling spelling ``none``.  Every other line keeps its bytes;
        the write is atomic and keeps the file's mode.  Returns a
        CeilingReport carrying the lines set-version-ceiling.sh prints.

    switch_provider(ctx, provider)
        Set kanban.cfg [providers] active to a recognized provider whose CLI
        is on PATH.  Every other line keeps its bytes; the write is atomic
        and keeps the file's mode.  Returns a ProviderSwitch.

    deposit_intake(ctx, project, file_path)
        Copy a staged intake file into the correct project intake directory,
        routing by filename prefix.  Atomic and no-clobber: temp+chmod, then
        hard-link the temp file to the target (fails if the target exists)
        and unlink the temp.  Returns the deposited Path on success.

    close_item(ctx, project, key, state="done", note="", dry_run=False)
        Set the terminal state on a task or intake item identified by key.
        For agent tasks: always writes DONE to ## State, clears ## Blockers and
        ## Needs Human, flips the agent queue marker to [x].
        For intake items: sets ## Status to state (one of
        lib.terminal_states.CLOSED_STATES: done|wont-do), optionally records
        note in ## Close Note, flips the backlog marker to [x].

    wontdo_item(ctx, project, key)
        Mark an agent task as WONT-DO.  Clears ## Blockers and ## Needs Human.
        Flips the agent queue marker to [x].

    delete_item(ctx, project, key, force=False)
        Delete a task (directory) or intake item (file) identified by key.
        Terminal-state guard: refuses non-terminal items unless force=True.
        Terminal states: "done" and "wont-do" (case-insensitive; defined in
        lib.terminal_states).

    reset_item(ctx, project, key, keep_artifacts=False, force=False)
        Reset a task or intake item to a re-pickable state.
        For agent tasks: refuses if state is WORKING (Refused exception, exit 2).
          Without --force, a stale worktree (registration or on-disk path) raises
          Refused (exit 2) after printing the removal recipe to stderr.  With
          --force, performs cleanup (worktree remove, prune, rm -rf, branch delete)
          narrating each step; raises MountPinned (exit 4) if mount-pinned.
          Regenerates status.md from the BACKLOG template (amnesia guarantee),
          clears artifacts/ and task logs/, flips queue marker to [ ], deletes
          the feature branch from the project dev tree, runs git worktree prune,
          and appends one line to the operator reset log.
        For bug/priority intake: resets ## Status to "open", flips backlog
          marker to [ ].
        For requirements intake: resets ## Status to "open", flips pm_backlog
          marker to [ ], clears the PM materializer's .materialized.* hash-marker.

    set_marker(queue_file, item_key, marker, ignore_case=False)
        Rewrite the bracket marker of every queue line for item_key to one of
        " ", "W", "A", "B", "x" ("" reads as " "), leaving the "- " prefix and
        description untouched.  Returns False when the file or line is absent.

    remove_marker(queue_file, item_key, ignore_case=False)
        Delete every queue line for item_key.  Returns False when the file or
        line is absent.

    queue_entries(queue_file, item_key, ignore_case=False)
        Return every queue line for item_key, whole, in file order — the
        lines remove_marker would delete.  [] when the file or line is absent.

    append_queue_lines(queue_file, lines)
        Append whole queue lines, markers as given, skipping any line the
        file already holds byte for byte; creates a missing file.  Atomic.
        Returns the number of lines appended.  This and remove_marker are
        how a line moves between a live queue file and the archive's copy.

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
    python3 -m pgai_agent_kanban.ops defer        KANBAN_ROOT PROJECT UNTIL
    python3 -m pgai_agent_kanban.ops undefer      KANBAN_ROOT PROJECT
    python3 -m pgai_agent_kanban.ops set_version_ceiling KANBAN_ROOT PROJECT SHOW MINOR MAJOR
                                                         NO_MINOR NO_MAJOR DRY_RUN
    python3 -m pgai_agent_kanban.ops switch_provider KANBAN_ROOT PROVIDER
    python3 -m pgai_agent_kanban.ops deposit_intake PROJECT_ROOT FILE_PATH
    python3 -m pgai_agent_kanban.ops close_item  PROJECT_ROOT KEY [STATE] [NOTE] [DRY_RUN]
    python3 -m pgai_agent_kanban.ops wontdo_item PROJECT_ROOT KEY
    python3 -m pgai_agent_kanban.ops delete_item PROJECT_ROOT KEY [FORCE]
    python3 -m pgai_agent_kanban.ops reset_item  PROJECT_ROOT KEY [KEEP_ARTIFACTS] [FORCE]
    python3 -m pgai_agent_kanban.ops set_marker    QUEUE_FILE KEY MARKER [IGNORE_CASE]
    python3 -m pgai_agent_kanban.ops remove_marker QUEUE_FILE KEY [IGNORE_CASE]
    python3 -m pgai_agent_kanban.ops render_marker QUEUE_FILE KEY MARKER [IGNORE_CASE]
    python3 -m pgai_agent_kanban.ops get_markers   QUEUE_FILE KEY [IGNORE_CASE]
    python3 -m pgai_agent_kanban.ops scan_queue    QUEUE_FILE TASKS_ROOT ID_PATTERN LIMIT RULE...

Note: the package-level entry point (python3 -m pgai_agent_kanban.ops) is the canonical
CLI path.  Running this submodule directly via -m pgai_agent_kanban.ops.write triggers a
runpy double-import RuntimeWarning because ops/__init__.py eagerly imports ops.write.
The _cli_main() function below is the implementation; __main__.py is the entry point.

Exit codes: 0 success, 1 error (message on stderr).
For defer/undefer: 1 for every refusal (the dual-channel block on stderr) and
    for a filesystem error; the success narration is printed to stdout.
For set_version_ceiling: 2 the project has no project.cfg, 1 every other refusal
    (no action, a ceiling that is not a non-negative integer -- the dual-channel
    block on stderr -- or a write failure); the report lines go to stdout.
    PROJECT is the resolved name; SHOW, NO_MINOR, NO_MAJOR and DRY_RUN are
    "true"/"false"; an empty MINOR or MAJOR leaves that ceiling alone.
For switch_provider: 1 for every refusal (an unrecognized provider -- the
    dual-channel block on stderr -- a CLI missing from PATH, no kanban.cfg, no
    [providers] active key, a write failure); the confirmation goes to stdout.
For deposit_intake: 2 routing refused, 3 target exists, 4 filesystem error.
For close_item/wontdo_item/delete_item: 2 ambiguous key, 3 not found, 4 state mutation failed,
    2 (delete) guard refused.
For reset_item: 2 WORKING state refusal, ambiguous key, or stale-worktree refusal (no --force);
    3 not found; 4 mount-pinned path blocks --force cleanup.
For set_marker/remove_marker: 1 MARKER is not a queue marker, 3 no line for KEY (or no
    QUEUE_FILE), 4 the queue file cannot be read or rewritten.
render_marker prints the queue file with KEY's lines set to MARKER and writes nothing
    (the wake commits it through atomic_write.sh): 1 bad MARKER, 3 no line for KEY.
get_markers prints "[M]" for each line for KEY: 3 no line (nothing printed), 4 unreadable.
scan_queue prints the keys of lines a MARKERS=STATES rule selects (see parse_scan_rule),
    at most LIMIT (0 = all): 1 a bad argument or rule, 4 an unreadable queue file. A
    task whose status.md cannot be read (not valid UTF-8, or an OS error) is left out
    of the keys and named in one stderr line; the scan goes on and still exits 0.
"""

from __future__ import annotations

import dataclasses
import os
import re
import shlex
import shutil
import sys
import tempfile
from pathlib import Path

from pgai_agent_kanban import task_status as _task_status
from pgai_agent_kanban.lib.atomic_write import atomic_write
from pgai_agent_kanban.lib import defer_posture as _defer_posture
from pgai_agent_kanban.lib import ini
from pgai_agent_kanban.lib.active_provider import VALID_PROVIDERS
from pgai_agent_kanban.lib.error_contract import (
    emit_error_contract,
    format_error_contract,
    parse_error_contract,
)
from pgai_agent_kanban.lib.filename_contract import (
    INTAKE_FILENAME_NOT_BARE,
    IntakeFilenameRefused,
    intake_destination,
)
from pgai_agent_kanban.lib.md_fields import read_field as _read_md_field
from pgai_agent_kanban.lib.sentinel_provenance import record_sentinel_write
from pgai_agent_kanban.lib.version_ceiling import (
    CEILING_SECTION,
    NO_CEILING,
    CeilingValueError,
    read_ceiling,
)
from pgai_agent_kanban.lib.terminal_states import (
    CLOSED_STATES,
    LEGACY_STATE_ALIASES,
    is_terminal as _is_terminal_state,
)
from pgai_agent_kanban.ops.context import OpsContext
from pgai_agent_kanban.ops.errors import (
    Ambiguous,
    IoError,
    MountPinned,
    NotFound,
    OpsError,
    Refused,
    cli_exit_code,
)
from pgai_agent_kanban.ops.resolve import ResolveResult, resolve_item, resolve_project_name


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def halt(ctx: OpsContext, project: str) -> None:
    """Create the HALT sentinel for the named project.

    Idempotent: if PROJECT_ROOT/HALT already exists, returns without error.

    A HALT this call creates gets a provenance record (op ``halt``) in
    ``<kanban_root>/logs/sentinel-writes.log`` -- see "Sentinel provenance"
    in the module docstring.  An existing HALT is left alone and recorded
    nowhere.

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
    record_sentinel_write(ctx.kanban_root, halt_path, "halt")


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

    When the write changes the file (it was absent or held other bytes) a
    provenance record (op ``halt_after``) is appended to
    ``<kanban_root>/logs/sentinel-writes.log`` -- see "Sentinel provenance"
    in the module docstring -- and any rc-gate latch (``HALT-AFTER.rc-gate``,
    a drain satisfied while an RC was open) is removed, since it was latched
    against the previous arm.  Re-arming the same token rewrites the file but
    adds no record and keeps the latch.  The file holds only TOKEN either way.

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
    content = f"{token}\n"
    try:
        previous: "bytes | None" = halt_after_path.read_bytes()
    except OSError:
        previous = None
    try:
        halt_after_path.write_text(content, encoding="utf-8")
    except OSError as exc:
        raise OpsError(
            f"halt_after: failed to write HALT-AFTER sentinel at {halt_after_path}: {exc}"
        ) from exc
    if previous != content.encode("utf-8"):
        record_sentinel_write(ctx.kanban_root, halt_after_path, "halt_after")
        # A drain latched against the previous token belongs to that arm,
        # not this one: clear it so it can never promote on the new token.
        # Imported here: the halt_after package imports task_status, which
        # imports this module, so a module-level import would be circular.
        from pgai_agent_kanban.halt_after.rc_gate import clear_latch

        clear_latch(project_root)


def halt_global(ctx: OpsContext) -> None:
    """Create the global HALT sentinel at KANBAN_ROOT/HALT.

    The global HALT blocks ALL projects at the next wake-loop iteration.
    Idempotent: if the sentinel already exists, returns without error.

    A HALT this call creates gets a provenance record (op ``halt_global``)
    in ``<kanban_root>/logs/sentinel-writes.log`` -- see "Sentinel
    provenance" in the module docstring.

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
    record_sentinel_write(kanban_root, halt_path, "halt_global")


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
# defer / undefer -- the DEFER-LOW-BUGS campaign file
# ---------------------------------------------------------------------------

# Body of an armed campaign file; the target line is appended. The comment
# names the three operator-facing places the file is armed, cleared and read.
_CAMPAIGN_FILE_HEADER = (
    "# DEFER-LOW-BUGS campaign file -- armed by team/scripts/defer.sh,\n"
    "# cleared by team/scripts/undefer.sh, read by\n"
    "# team/scripts/lib/defer_posture.sh.\n"
)


@dataclasses.dataclass(frozen=True)
class CampaignChange:
    """What one defer or undefer call did to a project's campaign file.

    Attributes:
        verb:          ``"defer"`` or ``"undefer"``.
        project:       The resolved, registered project name.
        campaign_file: Path of the project's DEFER-LOW-BUGS file.
        until:         The normalised ``vX.Y.Z`` target (``defer`` only;
                       ``""`` for ``undefer``).
        changed:       False when the call found the posture already in
                       place (same target armed, or nothing to clear) and
                       wrote nothing.
    """

    verb: str
    project: str
    campaign_file: Path
    until: str
    changed: bool

    @property
    def narration(self) -> str:
        """The one line defer.sh / undefer.sh print on success (no newline)."""
        if self.verb == "defer":
            if self.changed:
                return (
                    f"defer: DEFER-LOW-BUGS armed for project {self.project} "
                    f"(until: {self.until}, path: {self.campaign_file})"
                )
            return (
                f"defer: DEFER-LOW-BUGS already armed for project {self.project} "
                f"with until={self.until} ({self.campaign_file} unchanged; no change made)"
            )
        if self.changed:
            return (
                f"undefer: DEFER-LOW-BUGS cleared for project {self.project} "
                f"({self.campaign_file} removed)"
            )
        return (
            f"undefer: nothing to clear for project {self.project} "
            f"(no campaign file at {self.campaign_file})"
        )


def _normalise_defer_until(project: str, until: str) -> str:
    """Return *until* as ``vX.Y.Z``, or refuse it before anything is resolved.

    One leading ``v`` and then one leading ``V`` are dropped and a single
    ``v`` is put back, so ``2.14.0``, ``v2.14.0`` and ``V2.14.0`` all arm
    ``v2.14.0``. *project* is the name as typed; it fills the remedy.

    Raises:
        Refused: *until* is empty (``defer.until_required``) or not
            ``vX.Y.Z`` once normalised (``defer.until_malformed``).
    """
    if not until:
        # BLOCK-WORTHY (docs/blocking-site-census.md): a campaign with no
        # target is the malformed-by-omission state the resolver refuses to
        # read; refused rather than defaulted.
        # CENSUS-ANCHOR: defer-arm-until-required-01
        raise Refused(format_error_contract(
            "defer.until_required",
            "defer.sh: --until is required -- a deferral campaign always names the release it runs until.",
            "Re-run with --until set to a well-formed version, e.g. "
            f"team/scripts/defer.sh --project {project} --until v2.20.0.",
        ))
    bare = until[1:] if until.startswith("v") else until
    bare = bare[1:] if bare.startswith("V") else bare
    normalised = f"v{bare}"
    if not _defer_posture.is_well_formed_until(normalised):
        # BLOCK-WORTHY (docs/blocking-site-census.md): the invariant the
        # resolver's own read-side refusal enforces, checked at write time
        # so a malformed target never reaches disk.
        # CENSUS-ANCHOR: defer-arm-until-malformed-01
        raise Refused(format_error_contract(
            "defer.until_malformed",
            f"defer.sh: --until value '{until}' is not well-formed semver (expected vX.Y.Z).",
            "Re-run with a well-formed version, e.g. --until v2.14.0.",
        ))
    return normalised


def _require_project_dir(ctx: OpsContext, project: str, verb: str, remedy: str) -> Path:
    """Return *project*'s root, or refuse when it has no directory.

    The resolver passes a name through unchecked when the kanban root has
    no projects.cfg, so this is the check that catches a typo there.

    Raises:
        NotFound: ``<verb>.project_not_found``.
    """
    project_root = ctx.project_root(project)
    if not project_root.is_dir():
        # BLOCK-WORTHY (docs/blocking-site-census.md): a campaign file under a
        # directory that does not exist is a file nothing would ever read,
        # and there is nothing to clear in one.
        # CENSUS-ANCHOR: defer-project-not-found-01
        raise NotFound(format_error_contract(
            f"{verb}.project_not_found",
            f"{verb}.sh: project '{project}' has no directory at {project_root}.",
            remedy,
        ))
    return project_root


def defer(ctx: OpsContext, project: str, until: str) -> CampaignChange:
    """Arm the DEFER-LOW-BUGS deferral campaign for a project.

    Steps, in order, each refusing before anything is written:

    1. Normalise and validate *until* (``defer.until_required``,
       ``defer.until_malformed``).
    2. Resolve *project* through :func:`resolve_project_name`
       (``project_resolve.not_found`` / ``.ambiguous``); a registered name
       resolves to itself.
    3. Require the project directory (``defer.project_not_found``).

    Then, when the project's campaign file already carries the same
    ``until=`` target, nothing is written (its mtime and any operator
    comments are kept). Otherwise the file is replaced atomically -- same
    directory scratch file, then rename -- with the canonical header and
    ``until=vX.Y.Z``, so exactly one campaign file exists afterwards -- and a
    provenance record (op ``defer``) is appended to
    ``<kanban_root>/logs/sentinel-writes.log`` (see "Sentinel provenance" in
    the module docstring).  The no-op writes and records nothing.

    Args:
        ctx:     OpsContext carrying the kanban root path.
        project: Project name or unambiguous prefix.
        until:   Release the campaign runs until; a leading ``v`` is optional.

    Returns:
        The :class:`CampaignChange`; ``changed`` is False for the no-op.

    Raises:
        Refused:   A missing or malformed *until*.
        NotFound:  No registered project matches, or it has no directory.
        Ambiguous: The prefix matches several projects.
        IoError:   The campaign file could not be written.
    """
    until_normalised = _normalise_defer_until(project, until)
    project = resolve_project_name(ctx, project)
    project_root = _require_project_dir(
        ctx, project, "defer",
        "Check the project name against projects.cfg, or register the project first.",
    )

    campaign_file = _defer_posture.campaign_file_path(project_root)
    if campaign_file.is_file() and _defer_posture.read_campaign_until(campaign_file) == until_normalised:
        return CampaignChange("defer", project, campaign_file, until_normalised, changed=False)

    try:
        atomic_write(
            campaign_file,
            f"{_CAMPAIGN_FILE_HEADER}until={until_normalised}\n",
            prefix=f".{_defer_posture.CAMPAIGN_FILE_NAME}.",
        )
    except OSError as exc:
        raise IoError(f"defer: failed to write the campaign file {campaign_file}: {exc}") from exc
    record_sentinel_write(ctx.kanban_root, campaign_file, "defer")
    return CampaignChange("defer", project, campaign_file, until_normalised, changed=True)


def undefer(ctx: OpsContext, project: str) -> CampaignChange:
    """Clear the DEFER-LOW-BUGS deferral campaign for a project.

    Resolves *project* through :func:`resolve_project_name`, requires its
    directory (``undefer.project_not_found``), then removes the campaign
    file. A project with no campaign file is not an error: the call reports
    ``changed=False``. The posture then falls back to the project.cfg /
    kanban.cfg / default tiers; this function decides no posture itself.

    Args:
        ctx:     OpsContext carrying the kanban root path.
        project: Project name or unambiguous prefix.

    Returns:
        The :class:`CampaignChange`; ``changed`` is False when there was
        nothing to clear.

    Raises:
        NotFound:  No registered project matches, or it has no directory.
        Ambiguous: The prefix matches several projects.
        IoError:   The campaign file could not be removed.
    """
    project = resolve_project_name(ctx, project)
    project_root = _require_project_dir(
        ctx, project, "undefer", "Check the project name against projects.cfg."
    )

    campaign_file = _defer_posture.campaign_file_path(project_root)
    if not campaign_file.is_file():
        return CampaignChange("undefer", project, campaign_file, "", changed=False)
    try:
        campaign_file.unlink()
    except FileNotFoundError:
        pass
    except OSError as exc:
        raise IoError(f"undefer: failed to remove the campaign file {campaign_file}: {exc}") from exc
    return CampaignChange("undefer", project, campaign_file, "", changed=True)


# ---------------------------------------------------------------------------
# set_version_ceiling -- project.cfg [versioning] max_minor / max_major
# ---------------------------------------------------------------------------

_CEILING_SECTION = CEILING_SECTION
_CEILING_ACTIONS = "(--show, --minor, --major, --no-minor, --no-major)"
_CEILING_KEYS = (("--minor", "max_minor"), ("--major", "max_major"))


@dataclasses.dataclass(frozen=True)
class CeilingReport:
    """What one set_version_ceiling call did, and the lines it reports.

    Attributes:
        project:  The project whose project.cfg was read or edited.
        cfg_file: Path of that project.cfg.
        lines:    The lines set-version-ceiling.sh prints on success, in
                  order (empty when ceilings were changed without --show).
        changed:  True when project.cfg was rewritten.
    """

    project: str
    cfg_file: Path
    lines: tuple[str, ...]
    changed: bool

    @property
    def narration(self) -> str:
        """The report lines joined by newlines (no trailing newline)."""
        return "\n".join(self.lines)


def set_version_ceiling_exit_code(exc: OpsError) -> int:
    """Return the exit code set-version-ceiling.sh uses for *exc*.

    2 when the project has no project.cfg (``NotFound``), 1 for every other
    refusal -- the codes the script documents, shared by the ops CLI and
    POST /operations/set-version-ceiling.
    """
    return 2 if isinstance(exc, NotFound) else 1


def _ceiling_value(value: "int | str | None") -> "str | None":
    """Return the value asked for one ceiling as text, or None to leave it alone."""
    if value is None:
        return None
    text = str(value)
    return text if text else None


def _ceiling_show_lines(cfg_file: Path) -> tuple[str, ...]:
    """Return the ``max_minor=`` / ``max_major=`` lines --show prints.

    Each line shows the ceiling in effect, by the rule every reader shares
    (lib.version_ceiling): the configured number, the default marked
    ``(default)`` when the key is absent, ``none`` for no ceiling, or the
    bad value marked invalid -- a value discovery treats as ceiling-blocked.
    """
    lines = []
    for _flag, key in _CEILING_KEYS:
        try:
            lines.append(f"{key}={read_ceiling(cfg_file, key).display}")
        except CeilingValueError as exc:
            lines.append(
                f"{key}={exc.raw} (invalid: not a non-negative whole number or {NO_CEILING}; "
                "the project is ceiling-blocked)"
            )
    return tuple(lines)


def set_version_ceiling(
    ctx: OpsContext,
    project: str,
    *,
    show: bool = False,
    minor: "int | str | None" = None,
    major: "int | str | None" = None,
    no_minor: bool = False,
    no_major: bool = False,
    dry_run: bool = False,
) -> CeilingReport:
    """Show, set or lift the max_minor / max_major ceilings in a project's project.cfg.

    Steps, in order, each refusing before anything is written:

    1. At least one of *show*, *minor*, *major*, *no_minor*, *no_major*
       is asked for (``Refused``, a plain ``ERROR:`` line).
    2. Each ceiling being set is a non-negative integer
       (``set_version_ceiling.invalid_ceiling``, dual-channel).
    3. ``<project root>/project.cfg`` exists (``NotFound``).

    Then: *show* with no change asked for reports the values in effect;
    *dry_run* reports the planned changes; otherwise each change is applied
    to the ``[versioning]`` section -- an existing ``max_minor`` /
    ``max_major`` line becomes ``max_minor=N`` in place, a missing one is
    added after the section's last key, and *no_minor* / *no_major* write
    ``max_minor = none`` the same way (the explicit no-ceiling spelling: an
    absent key is not "no ceiling", it is the documented default) -- and
    *show* then reports the new values (``13 (default)`` for an absent key,
    ``none`` for no ceiling). Every other byte of the file is kept, and the
    file is replaced atomically with its permission mode unchanged.

    *no_minor* / *no_major* win over *minor* / *major*. A value is written
    as given (``07`` stays ``07``).

    Args:
        ctx:      OpsContext carrying the kanban root path.
        project:  Registered project name (already resolved by the caller).
        show:     Report the ceilings (after any change).
        minor:    New max_minor; None or ``""`` leaves it alone.
        major:    New max_major; None or ``""`` leaves it alone.
        no_minor: Lift max_minor (write ``none``).
        no_major: Lift max_major (write ``none``).
        dry_run:  Report the planned changes and write nothing.

    Returns:
        The :class:`CeilingReport`.

    Raises:
        Refused:  No action, or a ceiling that is not a non-negative integer.
        NotFound: The project has no project.cfg.
        IoError:  project.cfg could not be read or rewritten.
    """
    requested = [
        (flag, key, _ceiling_value(value), remove)
        for (flag, key), value, remove in zip(_CEILING_KEYS, (minor, major), (no_minor, no_major))
    ]
    if not show and all(value is None and not remove for _f, _k, value, remove in requested):
        raise Refused(f"ERROR: at least one action is required {_CEILING_ACTIONS}")

    for flag, _key, change, remove in requested:
        if change and not remove and not re.fullmatch(r"[0-9]+", change):
            # BLOCK-WORTHY (docs/blocking-site-census.md): a ceiling that is
            # not a non-negative integer is a value discovery's version
            # gate cannot compare against, so it never reaches project.cfg.
            # CENSUS-ANCHOR: ceiling-invalid-value-01
            raise Refused(format_error_contract(
                "set_version_ceiling.invalid_ceiling",
                f"set-version-ceiling.sh: {flag} requires a non-negative integer; got: '{change}'.",
                "Re-run with a non-negative integer, e.g. team/scripts/set-version-ceiling.sh "
                f"--project {shlex.quote(project)} {flag} 5",
            ))

    cfg_file = ctx.project_root(project) / "project.cfg"
    if not cfg_file.is_file():
        raise NotFound(f"ERROR: project '{project}' not found — no project.cfg at: {cfg_file}")

    pending = [
        (flag, key, NO_CEILING if remove else change)
        for flag, key, change, remove in requested
        if remove or change is not None
    ]
    if show and not pending:
        return CeilingReport(project, cfg_file, _ceiling_show_lines(cfg_file), changed=False)

    if dry_run:
        lines = [f"[DRY-RUN] Would apply to: {cfg_file}"]
        lines += [f"  set {key}={change}" for _f, key, change in pending]
        if show:
            lines.append("  [--show would print after changes are applied]")
        return CeilingReport(project, cfg_file, tuple(lines), changed=False)

    try:
        before = cfg_file.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError) as exc:
        raise IoError(f"ERROR: cannot read {cfg_file}: {exc}") from exc
    after = before
    for _flag, key, change in pending:
        # A number keeps the compact ``key=N`` form this verb has always
        # written; the no-ceiling spelling is written ``key = none``, the
        # project.cfg template's own form.
        delimiter = " = " if change == NO_CEILING else "="
        after = ini.set_line_value(after, _CEILING_SECTION, key, change, delimiter=delimiter)
    changed = after != before
    if changed:
        try:
            atomic_write(cfg_file, after, prefix=".project.cfg.", preserve_mode=True)
        except OSError as exc:
            raise IoError(f"ERROR: failed to write {cfg_file}: {exc}") from exc

    lines = _ceiling_show_lines(cfg_file) if show else ()
    return CeilingReport(project, cfg_file, lines, changed=changed)


# ---------------------------------------------------------------------------
# switch_provider -- kanban.cfg [providers] active
# ---------------------------------------------------------------------------

#: Recognized provider -> the CLI binary that must be on PATH to switch to it.
#: The providers are the ones the wake path accepts (lib/active_provider.py);
#: each one's CLI shares its name.
PROVIDER_CLIS = {name: name for name in VALID_PROVIDERS}

_PROVIDERS_SECTION = "providers"
_ACTIVE_KEY = "active"


@dataclasses.dataclass(frozen=True)
class ProviderSwitch:
    """What one switch_provider call did.

    Attributes:
        previous: The ``active`` value before the call (``""`` when empty).
        provider: The provider now active (lowercase).
        cli:      The provider's CLI binary, found on PATH.
        cfg_file: Path of the kanban.cfg that was edited.
        changed:  True when kanban.cfg was rewritten (False when the
                  provider was already active).
    """

    previous: str
    provider: str
    cli: str
    cfg_file: Path
    changed: bool

    @property
    def narration(self) -> str:
        """The confirmation switch-provider.sh prints (no trailing newline)."""
        return (
            f"Active provider switched: {self.previous or '(none)'} → {self.provider}\n"
            f"  Updated: {self.cfg_file} [providers] active\n"
            f"  CLI confirmed in PATH: {self.cli}\n"
            "\n"
            "The change takes effect on the next cron tick (typically 1-2 minutes)."
        )


def _no_active_key(cfg_file: Path, provider: str) -> NotFound:
    return NotFound(
        "switch-provider.sh: error: [providers] section in kanban.cfg has no 'active' key\n"
        f"  Edit {cfg_file} manually and add: active = {provider}"
    )


def switch_provider(ctx: OpsContext, provider: str) -> ProviderSwitch:
    """Make *provider* the active LLM provider in kanban.cfg [providers].

    Steps, in order, each refusing before anything is written:

    1. *provider*, lowercased, is one of :data:`PROVIDER_CLIS`
       (``switch_provider.unknown_provider``, dual-channel).
    2. That provider's CLI binary is on PATH (``Refused``).
    3. ``<kanban root>/kanban.cfg`` exists (``NotFound``).
    4. Its ``[providers]`` section has an ``active`` key (``NotFound``).

    Then the first ``active = ...`` line of ``[providers]`` becomes
    ``active = <provider>``. Comment lines that mention ``active`` and
    every other key and section keep their bytes, and the file is replaced
    atomically with its permission mode unchanged.

    Args:
        ctx:      OpsContext carrying the kanban root path.
        provider: Provider name, any case.

    Returns:
        The :class:`ProviderSwitch`.

    Raises:
        Refused:  An unrecognized provider, or its CLI is not on PATH.
        NotFound: No kanban.cfg, or no ``[providers] active`` key.
        IoError:  kanban.cfg could not be read or rewritten.
    """
    name = provider.lower()
    cli = PROVIDER_CLIS.get(name)
    if cli is None:
        # BLOCK-WORTHY (docs/blocking-site-census.md): an unrecognized
        # provider has no wake script, so writing it would leave every
        # wake firing with nothing to dispatch to.
        # CENSUS-ANCHOR: switch-provider-unknown-01
        raise Refused(format_error_contract(
            "switch_provider.unknown_provider",
            f"switch-provider.sh: unrecognized provider '{provider}'; "
            f"valid providers: {', '.join(PROVIDER_CLIS)}.",
            "Re-run with a valid provider, e.g. team/scripts/switch-provider.sh --provider claude",
        ))

    if shutil.which(cli) is None:
        raise Refused(
            f"switch-provider.sh: error: cannot switch to provider '{name}'\n"
            f"  CLI binary '{cli}' not found in PATH\n"
            f"  Install {cli} before switching to this provider."
        )

    cfg_file = ctx.kanban_root / "kanban.cfg"
    if not cfg_file.is_file():
        raise NotFound(
            f"switch-provider.sh: error: kanban.cfg not found at {cfg_file}\n"
            "  Run install.sh first to seed the configuration file."
        )

    previous = ini.read_line_value(cfg_file, _PROVIDERS_SECTION, _ACTIVE_KEY)
    if previous is None:
        raise _no_active_key(cfg_file, name)

    try:
        before = cfg_file.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError) as exc:
        raise IoError(f"switch-provider.sh: error: cannot read '{cfg_file}': {exc}") from exc
    try:
        after = ini.set_line_value(before, _PROVIDERS_SECTION, _ACTIVE_KEY, name, add=False)
    except ini.NoOptionError as exc:
        raise _no_active_key(cfg_file, name) from exc
    changed = after != before
    if changed:
        try:
            atomic_write(cfg_file, after, prefix=".kanban.cfg.", preserve_mode=True)
        except OSError as exc:
            raise IoError(f"switch-provider.sh: error: failed to update '{cfg_file}': {exc}") from exc
    return ProviderSwitch(previous, name, cli, cfg_file, changed)


# ---------------------------------------------------------------------------
# Intake routing
# ---------------------------------------------------------------------------

# The routing table (INTAKE_ROUTES) and the one filename check that routes
# on it (intake_destination) live in lib/filename_contract.py, so intake,
# the operator API's intake route and discovery share one contract.


def route_intake_name(project: str, file_path: "Path | str") -> str:
    """Return the project sub-directory the basename of *file_path* routes to.

    The intake name check deposit_intake runs before it copies anything, and
    that the operator API's intake route runs before it writes its temp
    file, so an HTTP caller reads the very refusal intake.sh prints.  It
    reads the name only and never touches the filesystem.

    The check itself is ``lib.filename_contract.intake_destination`` -- the
    one intake filename check, shared with the API route's own pre-write
    guard.  A basename that is not a bare file name (it can still carry
    ``..``, a backslash, a URL-encoded separator or a NUL) is refused
    dual-channel; one that matches no routing prefix is refused with the
    plain ``deposit_intake: cannot route ...`` line.

    Args:
        project:   Project name, used only to make the remedy runnable.
        file_path: The staged file whose basename is routed.

    Returns:
        ``"bugs"``, ``"priority"`` or ``"requirements"``.

    Raises:
        Refused: The basename is not a bare file name, or matches no
                 routing prefix.
    """
    src = Path(file_path)
    try:
        return intake_destination(src.name)
    except IntakeFilenameRefused as exc:
        if exc.error == INTAKE_FILENAME_NOT_BARE:
            renamed = src.with_name("BUG-0001-example.md")
            remedy = (
                "Rename the staged file to a bare intake name, then re-run intake: "
                + shlex.join(["mv", str(src), str(renamed)])
                + " && "
                + shlex.join(["intake.sh", "--project", project, str(renamed)])
            )
            # BLOCK-WORTHY (docs/blocking-site-census.md): an invariant by
            # design -- the deposited name is joined under the project
            # directory, so a name that is a path could land outside it.
            # Refused before anything is copied.
            # CENSUS-ANCHOR: write-intake-filename-not-bare-01
            raise Refused(format_error_contract(exc.error, f"intake.sh: {exc.message}", remedy)) from exc
        raise Refused(f"deposit_intake: {exc.message}") from exc


# ---------------------------------------------------------------------------
# deposit_intake
# ---------------------------------------------------------------------------


def deposit_intake(ctx: OpsContext, project: str, file_path: "Path | str") -> Path:
    """Copy a staged intake file into the correct project intake directory.

    Routes purely by the basename of ``file_path`` (case-sensitive prefix
    match).  This is a dumb router: it does not validate file contents,
    assign numbers, or check headings.  Content validation is the
    discovery pipeline's responsibility.

    Routing rules (case-sensitive, matched against the basename by
    ``lib.filename_contract.intake_destination``, the check the operator
    API's intake route shares):
        ``BUG-*``       → ``<project_root>/bugs/``
        ``PRIORITY-*``  → ``<project_root>/priority/``
        ``v[0-9]*.md``  → ``<project_root>/requirements/``
        anything else   → Refused (``Refused`` exception raised)
    A basename that is not a bare file name (``..``, a backslash, a
    URL-encoded separator or a NUL) is refused first, dual-channel
    (``intake.filename_not_bare``), whatever its prefix.

    Copy semantics:
        - Source is never moved or deleted.  Copy semantics only.
        - Atomic: write to a temp file in the destination directory (same
          filesystem, so the placement is a single directory-entry
          operation), chmod 644, then hard-link the temp file to the target
          and unlink the temp.  Discovery never sees a partial file.
        - No clobber at placement, not only at the pre-check: ``link(2)``
          fails with ``EEXIST`` when the target already exists, so a target
          that appears between the pre-check and the placement is left
          intact and the deposit raises the same no-clobber ``OpsError``
          the pre-check raises.  ``rename(2)`` would silently replace it.
        - The temp file is removed on every path: unlinked after a
          successful link, and cleaned up before the error propagates on a
          refused or failed one.
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
        Refused:   If the basename is not a bare file name, or does not
                   match any known intake prefix.  Nothing is copied.
        OpsError:  If the target already exists (no clobber), whether it was
                   present at the pre-check or appeared before placement.
        IoError:   If a filesystem error occurs during copy or placement.
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
    dest_subdir = route_intake_name(project, src)

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

    # --- Atomic copy: mktemp in dest dir → copy → chmod 644 → link → unlink ---
    # Using mktemp in the destination directory guarantees same-filesystem
    # placement, which makes the final link(2) a single directory-entry
    # operation.  link(2) is used instead of rename(2) because rename
    # silently replaces an existing target on POSIX, which would let a
    # deposit that landed after the pre-check above be overwritten; link
    # fails with EEXIST instead, so the no-clobber contract holds at the
    # moment of placement and not only at the pre-check.
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

        # Set mode 644 on the temp file before it becomes visible as the target.
        try:
            tmp_path.chmod(0o644)
        except OSError as exc:
            raise IoError(
                f"deposit_intake: failed to set mode 0o644 on temp file {tmp_path}: {exc}"
            ) from exc

        # No-clobber placement: the target becomes a second name for the
        # temp file's inode, or the call fails because that name is taken.
        try:
            os.link(tmp_path, target)
        except FileExistsError:
            # Same message shape as the pre-check refusal so the CLI maps
            # this path to the same no-clobber exit code.
            raise OpsError(
                f"deposit_intake: target already exists (no clobber): {target}"
            )
        except OSError as exc:
            raise IoError(
                f"deposit_intake: failed to link temp file to target {target}: {exc}"
            ) from exc

        # The target now holds the content; drop the temp name.  A failure
        # here leaves a stray temp file beside a correctly deposited target,
        # which is hygiene litter rather than a failed deposit, so it does
        # not turn a successful placement into an error for the caller.
        try:
            tmp_path.unlink()
        except OSError:
            pass
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
        Sets ## Status to ``state`` (one of ``CLOSED_STATES``: done|wont-do),
        optionally records ``note`` in a ## Close Note section, and flips the
        intake backlog marker to [x].

    The write is atomic: a temp file in the same directory is written, then
    renamed over the target (os.replace is atomic on POSIX).

    Args:
        ctx:     OpsContext carrying the kanban root path.
        project: Project name as registered in projects.cfg.
        key:     Task folder name or intake file base name.
        state:   Terminal state for intake items.  One of ``CLOSED_STATES``
                 ("done", "wont-do"); a legacy alias such as "superseded" is
                 refused with a remedy naming "wont-do".  Validated for
                 agent tasks too, though tasks always close as DONE.
                 Defaults to "done".
        note:    Optional free-form note recorded in ## Close Note section
                 of intake items.  Ignored for agent tasks.
        dry_run: When True, report the intended change to stdout without
                 writing anything.  Exits 0.

    Raises:
        OpsError:  If ``state`` is not a closed state (a dual-channel
                   ``close.invalid_state`` refusal, exit 1), the project root
                   is missing, key resolves to a bad path, or the
                   status.md/intake file cannot be read.
        NotFound:  If no item matches the key.
        Ambiguous: If the key prefix matches multiple items.
        IoError:   If the atomic write fails.
    """
    _check_close_state(state, project, key)

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

        _write_task_terminal_state(status_file, "DONE", "close_item")

        marker_file = _get_marker_file(
            ctx.project_root(project), result.item_type, result.path
        )
        marker_key = _get_item_key_in_marker(result.item_type, result.path)
        set_marker(marker_file, marker_key, "x")
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

    _write_intake_close(item_path, state, note)

    marker_file = _get_marker_file(
        ctx.project_root(project), result.item_type, result.path
    )
    marker_key = _get_item_key_in_marker(result.item_type, result.path)
    set_marker(marker_file, marker_key, "x", ignore_case=True)


def _check_close_state(state: str, project: str, key: str) -> None:
    """Refuse a close *state* that is not one of ``CLOSED_STATES``.

    A legacy alias (``superseded``) is refused with a remedy that closes the
    item as the state it stands for, with a note; any other value with a
    remedy that closes it as ``done``. Both remedies name the real project
    and key so they run as printed.
    """
    if state in CLOSED_STATES:
        return
    target = f"--project {shlex.quote(project)} --key {shlex.quote(key)}"
    valid = " | ".join(CLOSED_STATES)
    alias_of = LEGACY_STATE_ALIASES.get(state.strip().lower())
    if alias_of is not None:
        message = (
            f"close.sh: '{state}' is not a kanban state (valid: {valid}); an item "
            f"another item subsumed closes as '{alias_of}' with a note saying why."
        )
        remedy = (
            f"close.sh {target} --state {alias_of} "
            f"--note {shlex.quote('superseded by another item')}"
        )
    else:
        message = f"close.sh: invalid --state value: {state} (valid: {valid})"
        remedy = f"close.sh {target} --state {CLOSED_STATES[0]}"
    # BLOCK-WORTHY (docs/blocking-site-census.md): an item closes only to one
    # of the closed states -- any other value would leave a status no reader
    # treats as closed. Refused before anything is resolved or written.
    # CENSUS-ANCHOR: write-close-invalid-state-01
    raise OpsError(format_error_contract("close.invalid_state", message, remedy))


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

    _write_task_terminal_state(status_file, "WONT-DO", "wontdo_item")

    marker_file = _get_marker_file(
        ctx.project_root(project), result.item_type, result.path
    )
    marker_key = _get_item_key_in_marker(result.item_type, result.path)
    set_marker(marker_file, marker_key, "x")


# ---------------------------------------------------------------------------
# delete_item
# ---------------------------------------------------------------------------


def delete_item(
    ctx: OpsContext,
    project: str,
    key: str,
    force: bool = False,
) -> None:
    """Delete a task (directory) or intake item (file) identified by key.

    TERMINAL-STATE GUARD (the safety-critical property):
        Deletion is refused unless the item is in a terminal state.
        Terminal states are "done" and "wont-do" (case-insensitive).
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
    # Routes through the shared predicate from lib.terminal_states so the
    # vocabulary is defined exactly once.  Accepts both uppercase task forms
    # ("DONE", "WONT-DO") and lowercase intake forms ("done", "wont-do").
    if not force:
        if not _is_terminal_state(result.state):
            raise Refused(
                f"delete_item: refused to delete {key!r} — state is {result.state!r} "
                f"(not a terminal state).\n"
                f"delete_item: use --force to delete regardless, or move the item to "
                f"done or wont-do first."
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
        remove_marker(marker_file, marker_key)
    else:
        remove_marker(marker_file, marker_key, ignore_case=True)


# ---------------------------------------------------------------------------
# reset_item
# ---------------------------------------------------------------------------


def reset_item(
    ctx: OpsContext,
    project: str,
    key: str,
    keep_artifacts: bool = False,
    force: bool = False,
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
        force:          When True, perform stale-worktree cleanup (git worktree
                        remove --force, git worktree prune, rm -rf of the on-disk
                        path if still present, feature-branch deletion) before
                        proceeding to the normal reset.  Without force, a detected
                        stale worktree causes a Refused error and non-zero exit
                        with the removal recipe printed to stderr.

    Raises:
        OpsError:     If the project root is missing or an argument is invalid.
        NotFound:     If no item matches the key.
        Ambiguous:    If the key prefix matches multiple items.
        Refused:      If the item is in WORKING state (agent task only), or if
                      a stale worktree exists and force is False.
        MountPinned:  If force is True and the stale worktree on-disk path is
                      an active mount target; no cleanup is attempted.
        IoError:      If a filesystem operation fails.
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
            force=force,
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
        # Fresh-decompose recipe: set ## Status to 'open' and clear ## PM Task
        # to 'none' atomically in a single write.  pm_backlog.md is intentionally
        # left untouched — discovery mints a fresh decompose ticket on the next
        # tick, which is the selection model GUARD 4 assumes.
        _write_requirement_reset(
            item_path=result.path,
            current_status=result.state,
        )
        if pm_task_id and pm_task_id.lower() != "none":
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
    force: bool = False,
) -> None:
    """Execute the agent-task reset procedure.

    Refuses if current_state is WORKING.  Then checks for a stale worktree:
    - Without force: prints the detection summary and the exact three-command
      removal recipe to stderr, then raises Refused (exit 2).
    - With force: performs cleanup (git worktree remove --force, git worktree
      prune, rm -rf of the on-disk path if still present, feature-branch
      deletion) narrating each step to stderr.  Raises MountPinned (exit 4)
      if the path is an active mount target.  On success, falls through to the
      normal amnesia procedure.
    - When no stale state: force and bare behave identically; no worktree
      cleanup narration; normal reset proceeds.

    After any stale-worktree handling: regenerates status.md, clears
    artifacts/ and logs/, flips the queue marker, deletes the feature branch,
    runs git worktree prune, and appends to the reset log.

    Args:
        ctx:           OpsContext (used to construct dev-tree paths via config).
        project:       Project name.
        project_root:  Absolute path to the project root.
        kanban_root:   Absolute path to the kanban root.
        task_dir:      Absolute path to the task folder.
        task_id:       Task folder basename (e.g. CODER-20260607-001-slug).
        current_state: Current ## State value from status.md.
        keep_artifacts: When True, skip clearing artifacts/ (logs always cleared).
        force:         When True, perform stale-worktree cleanup before reset.

    Raises:
        Refused:      If current_state is "WORKING", or if a stale worktree
                      exists and force is False.
        MountPinned:  If force is True and the stale on-disk path is mount-pinned.
        IoError:      If the status.md write or directory-clear fails.
    """
    # --- GUARD: refuse on WORKING (filesystem race) ---
    if current_state == "WORKING":
        raise Refused(
            f"reset_item: REFUSED — task {task_id!r} is in state WORKING.\n"
            f"  An agent process may currently hold this task.\n"
            f"  Wait for the agent to finish or investigate before resetting."
        )

    # --- Stale worktree detection and warn/cleanup ---
    # Read dev_tree and branch_prefix early so detection can use them; they are
    # also consumed by Step 4 below.
    dev_tree = _read_dev_tree_path(project_root)
    branch_prefix = _read_branch_prefix(project_root)
    if dev_tree is not None and dev_tree.is_dir():
        stale_wt = _detect_stale_worktree(dev_tree, task_id, branch_prefix)
        if stale_wt.registration_exists or stale_wt.path_exists:
            if not force:
                # Warn path: print diagnostic + recipe to stderr, then refuse.
                _warn_stale_worktree(task_id, stale_wt)
                raise Refused(
                    f"reset_item: REFUSED — task {task_id!r} has a stale worktree.\n"
                    f"  Run the three commands above to clear it, then retry.\n"
                    f"  Or re-run with --force to have reset.sh handle cleanup automatically."
                )
            else:
                # Force path: perform cleanup with narration, then fall through.
                _cleanup_stale_worktree(dev_tree, task_id, stale_wt, branch_prefix)

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
    _write_or_raise_io(status_file, fresh_status)

    # --- Step 2: Clear artifacts/ and task logs/ ---
    artifacts_dir = task_dir / "artifacts"
    task_logs_dir = task_dir / "logs"

    if not keep_artifacts:
        _clear_directory_contents(artifacts_dir)

    _clear_directory_contents(task_logs_dir)

    # --- Step 3: Flip queue marker to [ ] ---
    marker_file = _get_marker_file(project_root, "task", task_dir)
    marker_key = _get_item_key_in_marker("task", task_dir)
    set_marker(marker_file, marker_key, " ")

    # --- Step 4: Dev tree operations (feature branch + worktree prune) ---
    # dev_tree and branch_prefix were resolved earlier for stale-worktree detection;
    # reuse them here rather than calling the cfg readers a second time.
    feature_branch = f"{branch_prefix}feature/{task_id}"

    if dev_tree is not None and dev_tree.is_dir():
        _git_delete_feature_branch(dev_tree, feature_branch, branch_prefix)
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
    set_marker(backlog_file, item_key, " ", ignore_case=True)


def _reset_intake_item_status_only(item_path: Path, current_status: str) -> None:
    """Reset an intake item's ## Status to 'open' without touching any marker file.

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

    _set_sections_or_raise(item_path, {"Status": "open"}, "reset_item")


def _write_requirement_reset(item_path: Path, current_status: str) -> None:
    """Set ## Status to 'open' and ## PM Task to 'none' in a requirement file atomically.

    Implements the fresh-decompose reset recipe for requirements: the status
    is reopened and the recorded PM task reference is cleared so discovery
    mints a new decompose ticket on the next tick.  pm_backlog.md is never
    touched by this function.

    Args:
        item_path:      Path to the requirement .md file.
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

    # Clear ## PM Task to 'none' when the field exists.  If the field is
    # absent, leave the file as-is (discovery handles missing PM Task as
    # an unrecorded requirement — still selectable).
    new_text, _ = re.subn(
        r"(## PM Task\n(?:[ \t]*\n)*)([^\n]+)",
        lambda m: m.group(1) + "none",
        new_text,
        count=1,
    )

    if not new_text.endswith("\n"):
        new_text += "\n"

    _write_or_raise_io(item_path, new_text)


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


def _read_project_cfg_value(project_root: Path, section: str, key: str) -> str:
    """Read a single value from project.cfg (INI format) or PROJECT.cfg (legacy).

    Tries project.cfg first, then PROJECT.cfg; the first file holding a
    non-empty value wins.  Returns '' when neither file exists, neither can
    be parsed as INI, or the key is absent or empty.

    Args:
        project_root: Absolute path to the project root.
        section:      INI section name (e.g. "project").
        key:          Key name within the section (e.g. "dev_tree_path").

    Returns:
        The value with surrounding whitespace and double quotes (the bash
        convention) stripped, or '' when not found.
    """
    for cfg_name in ("project.cfg", "PROJECT.cfg"):
        value = ini.get_value(project_root / cfg_name, section, key).strip('"')
        if value:
            return value
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


def _git_delete_feature_branch(
    dev_tree: Path, feature_branch: str, branch_prefix: str = ""
) -> None:
    """Delete a feature branch from the dev tree, if it exists.

    Tries -d (safe) first; falls back to -D (force) with a warning to stderr
    if the branch is not fully merged.  Both outcomes are non-fatal — warns and
    proceeds if deletion fails. A missing git binary is the same kind of
    non-fatal outcome: it is checked before the first git invocation so a
    reset on a gitless host degrades to a warning (the branch, if any, is
    left in place for an operator with git to clean up later) instead of an
    unhandled FileNotFoundError aborting the whole reset.

    Args:
        dev_tree:       Absolute path to the git repository.
        feature_branch: Branch name to delete (e.g. "feature/CODER-...").
        branch_prefix:  The project's configured branch_prefix (e.g. "ai_"),
                        or "" for an unprefixed project. Passed through to
                        _warn_if_merged so its RC-branch scan looks for
                        <branch_prefix>rc/* rather than a bare "rc/*" that
                        would never match on a prefixed project.
    """
    import subprocess as _sp
    import sys as _sys

    if shutil.which("git") is None:
        print(
            f"WARNING: git binary not found on PATH; skipping feature branch "
            f"cleanup for '{feature_branch}' (dev tree: {dev_tree}).",
            file=_sys.stderr,
        )
        return

    # Check if branch exists.
    check = _sp.run(
        ["git", "-C", str(dev_tree), "rev-parse", "--verify",
         f"refs/heads/{feature_branch}"],
        capture_output=True,
    )
    if check.returncode != 0:
        return  # Branch does not exist; nothing to do.

    # Check if branch is merged into any RC branch before deleting.
    _warn_if_merged(dev_tree, feature_branch, branch_prefix)

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


def _warn_if_merged(
    dev_tree: Path, feature_branch: str, branch_prefix: str = ""
) -> None:
    """Print a warning to stderr if feature_branch is already merged into an RC branch.

    Non-fatal: warns and returns regardless.

    Args:
        dev_tree:       Absolute path to the git repository.
        feature_branch: Branch name to check.
        branch_prefix:  The project's configured branch_prefix (e.g. "ai_"),
                        or "" for an unprefixed project. RC branches are
                        scanned as <branch_prefix>rc/* so this warning still
                        fires on a prefixed project, where the RC branch is
                        named "ai_rc/vX.Y.Z", never bare "rc/vX.Y.Z".
    """
    import subprocess as _sp
    import sys as _sys

    # List all RC branches.
    r = _sp.run(
        ["git", "-C", str(dev_tree), "branch", "--list", f"{branch_prefix}rc/*"],
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

    A missing git binary is one more error this ignores: pruning worktree
    registrations is metadata housekeeping, not a step the reset's caller
    depends on to complete successfully, so a gitless host silently skips it
    rather than raising an unhandled FileNotFoundError.

    Args:
        dev_tree: Absolute path to the git repository.
    """
    import subprocess as _sp
    if shutil.which("git") is None:
        return
    _sp.run(
        ["git", "-C", str(dev_tree), "worktree", "prune"],
        capture_output=True,
    )


# ---------------------------------------------------------------------------
# Stale worktree detection
# ---------------------------------------------------------------------------


@dataclasses.dataclass
class StaleWorktreeInfo:
    """Describes the stale-worktree state for a given task.

    Returned by _detect_stale_worktree.  All fields are read-only snapshots;
    the detection function never mutates anything on disk.

    Attributes:
        registration_exists: True when a worktree registration directory
            exists under <dev_tree>/.git/worktrees/<task_id>.
        path_exists:         True when the resolved on-disk worktree directory
            (per the pgai_worktree_path convention) exists on disk.
        worktree_path:       The resolved worktree path string (using the same
            convention as worktree.sh pgai_worktree_path), or None when the
            path could not be determined.
        branch_pinned:       True when the worktree registration's HEAD points
            at the task's feature branch (indicating the branch is checked out
            in the stale worktree and cannot be deleted without cleanup).
            None when registration_exists is False or HEAD could not be read.
    """

    registration_exists: bool
    path_exists: bool
    worktree_path: "str | None"
    branch_pinned: "bool | None"


def _detect_stale_worktree(
    dev_tree: Path,
    task_id: str,
    branch_prefix: str,
) -> StaleWorktreeInfo:
    """Detect whether a stale worktree exists for the given task.

    This function is read-only: it inspects the git repository and filesystem
    but NEVER modifies anything.  Consumption (warn or teardown) is the
    responsibility of the caller; in ticket 1 the result is only computed.

    Worktree path convention: mirrors worktree.sh pgai_worktree_path.  The
    resolved path is:
        <PGAI_WORKTREE_BASE>/<task_id>          when PGAI_WORKTREE_BASE is set
        /tmp/pgai_kanban_tmp/projects/<PP_project_name>/worktrees/<task_id>
                                                  per-project default (env unset)
        /tmp/pgai_kanban_tmp/worktrees/<task_id>  fallback when no project context

    Because Python cannot source worktree.sh, this function uses the
    PGAI_WORKTREE_BASE environment variable as the canonical override and
    applies the same default resolution logic without reading kanban.cfg.
    The kanban.cfg tier is only relevant when an operator has set
    worktree_base in [paths]; in that scenario the operator may also set
    PGAI_WORKTREE_BASE explicitly — a supported escape hatch.

    Registration detection: checks for the presence of the directory
    <dev_tree>/.git/worktrees/<task_id>.  Git creates this directory when a
    worktree is added and removes it when the worktree is properly torn down
    and pruned.  Its presence indicates a registration exists.

    branch_pinned detection: reads <dev_tree>/.git/worktrees/<task_id>/HEAD
    and checks whether it references the task's feature branch.  A branch is
    considered pinned when the HEAD file contains the feature branch ref.

    Args:
        dev_tree:      Absolute path to the canonical dev-tree git repository.
        task_id:       Task folder basename (e.g. CODER-20260607-001-slug).
        branch_prefix: Branch prefix from project.cfg (e.g. 'ai_'), used to
                       construct the expected feature branch name.

    Returns:
        StaleWorktreeInfo with the detection results.
    """
    feature_branch = f"{branch_prefix}feature/{task_id}"

    # --- Resolve the expected worktree path (mirrors worktree.sh pgai_worktree_path) ---
    pgai_worktree_base = os.environ.get("PGAI_WORKTREE_BASE", "").strip()
    if pgai_worktree_base:
        wt_base = pgai_worktree_base
    else:
        # Default: per-project subtree when PP_project_name is set (mirrors Tier 3).
        temp_root = os.environ.get("PGAI_AGENT_KANBAN_TEMP_DIR", "/tmp/pgai_kanban_tmp")
        pp_project_name = os.environ.get("PP_project_name", "").strip()
        if pp_project_name:
            wt_base = os.path.join(temp_root, "projects", pp_project_name, "worktrees")
        else:
            wt_base = os.path.join(temp_root, "worktrees")

    worktree_path: "str | None" = os.path.join(wt_base, task_id) if wt_base else None

    # --- Check on-disk path existence ---
    path_exists = worktree_path is not None and os.path.isdir(worktree_path)

    # --- Check git registration (presence of .git/worktrees/<task_id>/) ---
    git_dir = dev_tree / ".git"
    worktrees_dir = git_dir / "worktrees" / task_id
    registration_exists = worktrees_dir.is_dir()

    # --- Detect branch_pinned (reads HEAD from the registration dir) ---
    branch_pinned: "bool | None" = None
    if registration_exists:
        head_file = worktrees_dir / "HEAD"
        if head_file.is_file():
            try:
                head_content = head_file.read_text(encoding="utf-8").strip()
                # Typical format: "ref: refs/heads/<branch_name>"
                expected_ref = f"ref: refs/heads/{feature_branch}"
                branch_pinned = head_content == expected_ref
            except OSError:
                branch_pinned = None

    return StaleWorktreeInfo(
        registration_exists=registration_exists,
        path_exists=path_exists,
        worktree_path=worktree_path,
        branch_pinned=branch_pinned,
    )


def _warn_stale_worktree(task_id: str, stale: StaleWorktreeInfo) -> None:
    """Print the stale-worktree detection summary and removal recipe to stderr.

    Called when a stale worktree is detected and force is False.  The output
    gives the operator enough context to perform manual cleanup, and names the
    three commands in the exact order required to safely remove the registration
    and on-disk path before retrying the reset.

    This function never modifies anything on disk.

    Args:
        task_id: Task folder basename (e.g. CODER-20260607-001-slug).
        stale:   Detection snapshot from _detect_stale_worktree.
    """
    import sys as _sys

    wt_path = stale.worktree_path or "<path-unknown>"

    found_parts = []
    if stale.registration_exists:
        found_parts.append("git worktree registration (.git/worktrees/<task_id>)")
    if stale.path_exists:
        found_parts.append(f"on-disk worktree directory ({wt_path})")
    if stale.branch_pinned:
        found_parts.append("feature branch is checked out in the stale worktree")
    found_desc = "; ".join(found_parts) if found_parts else "stale worktree state"

    print(
        f"reset: STALE WORKTREE DETECTED for task {task_id!r}\n"
        f"  Found: {found_desc}\n"
        f"  On-disk path: {wt_path}\n"
        f"\n"
        f"  To clear the stale worktree manually, run these three commands:\n"
        f"    git -C <dev_tree> worktree remove --force {wt_path}\n"
        f"    git -C <dev_tree> worktree prune\n"
        f"    rm -rf {wt_path}\n"
        f"\n"
        f"  Then retry the reset, or re-run with --force to have reset handle"
        f" cleanup automatically.",
        file=_sys.stderr,
    )


def _probe_mount_pin(path: str) -> "str | None":
    """Return the blocking mount description if path is itself a mount point, else None.

    Only returns a result when the exact path is listed as a mount point — a
    broader filesystem that merely contains the path is not a pin.  This is the
    critical distinction: we want to know whether the worktree directory IS a
    mount target, not just whether it lives on some mounted filesystem.

    Probes with ``findmnt --mountpoint`` first (exact-match semantics: exits 1
    when the path is not itself a mount point) and falls back to a scan of
    ``/proc/mounts`` (column 2, exact string match) when findmnt is unavailable.
    Never attempts unmounting or any other mutation.

    Args:
        path: Absolute path string to probe.

    Returns:
        A human-readable description of the blocking mount (e.g. the source
        device and filesystem type) if the exact path is an active mount point,
        or None when no such mount is found.
    """
    import subprocess as _sp

    # Tier 1: findmnt --mountpoint <path> exits 0 only when path IS itself a
    # mount point (exact match semantics).  --target matches ANY path under a
    # mount — that is too broad and would fire on /tmp under the root fs.
    try:
        r = _sp.run(
            ["findmnt", "--mountpoint", path,
             "--output", "SOURCE,TARGET,FSTYPE", "--noheadings"],
            capture_output=True,
            text=True,
        )
        if r.returncode == 0 and r.stdout.strip():
            return r.stdout.strip()
        # returncode != 0: path is not a mount point — return None below.
        return None
    except FileNotFoundError:
        pass  # findmnt not available; fall through to /proc/mounts scan.

    # Tier 2: scan /proc/mounts for an exact match on the mount point column (col 2).
    # /proc/mounts columns: device mountpoint fstype options dump pass
    try:
        with open("/proc/mounts", encoding="utf-8") as fh:
            for line in fh:
                parts = line.split()
                if len(parts) >= 2 and parts[1] == path:
                    return f"{parts[0]} on {parts[1]} type {parts[2] if len(parts) > 2 else '?'}"
    except OSError:
        pass  # /proc/mounts not readable; cannot determine mount status.

    return None


def _cleanup_stale_worktree(
    dev_tree: Path,
    task_id: str,
    stale: StaleWorktreeInfo,
    branch_prefix: str = "",
) -> None:
    """Perform stale-worktree cleanup when --force is given.

    Executes in order: git worktree remove --force, git worktree prune,
    rm -rf of the on-disk path (if it still exists after the git remove),
    and deletion of the worktree-pinned feature branch.  Narrates each
    action to stderr before running it.  Raises on unrecoverable failures
    so the caller's amnesia procedure does not run if cleanup is incomplete.

    Before rm -rf, probes for an active mount at the path; raises MountPinned
    if one is found (no partial cleanup).

    Args:
        dev_tree: Absolute path to the git repository.
        task_id:  Task folder basename.
        stale:    Detection snapshot from _detect_stale_worktree.
        branch_prefix: The project's configured branch_prefix (e.g. "ai_"),
                  or "" for an unprefixed project. Passed through to the
                  feature-branch deletion step's already-merged check.

    Raises:
        MountPinned: If the on-disk path is an active mount target.
        OpsError:    If git worktree remove --force fails and the directory
                     still exists (or if shutil.rmtree fails).
    """
    import subprocess as _sp
    import sys as _sys

    wt_path = stale.worktree_path

    # --- Pre-flight: probe for mount pin before any destructive action ---
    # If the on-disk path is an active mount target, abort immediately so
    # no partial cleanup occurs and the kanban state remains untouched.
    if wt_path is not None and stale.path_exists:
        blocking_mount = _probe_mount_pin(wt_path)
        if blocking_mount is not None:
            raise MountPinned(
                f"reset: [worktree cleanup] ABORTED — on-disk path {wt_path!r}"
                f" is an active mount target.\n"
                f"  Blocking mount: {blocking_mount}\n"
                f"  Unmount it manually, then retry with --force."
            )

    print(
        f"reset: [worktree cleanup] starting stale-worktree removal for task {task_id!r}",
        file=_sys.stderr,
    )

    # --- Step 1: git worktree remove --force ---
    if stale.registration_exists or (wt_path is not None and stale.path_exists):
        if wt_path is not None:
            print(
                f"reset: [worktree cleanup] git worktree remove --force {wt_path}",
                file=_sys.stderr,
            )
            r = _sp.run(
                ["git", "-C", str(dev_tree), "worktree", "remove", "--force", wt_path],
                capture_output=True,
                text=True,
            )
            if r.returncode != 0:
                # Check whether the directory is gone despite the non-zero exit.
                import os as _os
                if _os.path.isdir(wt_path):
                    raise OpsError(
                        f"reset: [worktree cleanup] 'git worktree remove --force' failed"
                        f" for {wt_path!r} and the directory still exists.\n"
                        f"  git stderr: {r.stderr.strip() or '(empty)'}\n"
                        f"  Manual cleanup required before retrying."
                    )
                # Non-zero exit but directory is gone: acceptable (git may exit 1
                # when the worktree was only partially registered).
                print(
                    f"reset: [worktree cleanup] git worktree remove exited {r.returncode}"
                    f" but {wt_path!r} is already gone — treating as success",
                    file=_sys.stderr,
                )
            else:
                print(
                    f"reset: [worktree cleanup] git worktree remove completed",
                    file=_sys.stderr,
                )
        else:
            print(
                f"reset: [worktree cleanup] git registration exists but path unknown;"
                f" skipping 'git worktree remove' (no path to target)",
                file=_sys.stderr,
            )

    # --- Step 2: git worktree prune ---
    print(
        f"reset: [worktree cleanup] git worktree prune",
        file=_sys.stderr,
    )
    _sp.run(
        ["git", "-C", str(dev_tree), "worktree", "prune"],
        capture_output=True,
    )
    print(
        f"reset: [worktree cleanup] git worktree prune completed",
        file=_sys.stderr,
    )

    # --- Step 3: rm -rf the on-disk path if it still exists ---
    # Mount-pin check was done at pre-flight above; proceed directly to removal.
    if wt_path is not None:
        import os as _os
        if _os.path.isdir(wt_path):
            print(
                f"reset: [worktree cleanup] rm -rf {wt_path}",
                file=_sys.stderr,
            )
            try:
                shutil.rmtree(wt_path)
            except OSError as exc:
                raise OpsError(
                    f"reset: [worktree cleanup] rm -rf {wt_path!r} failed: {exc}"
                ) from exc
            print(
                f"reset: [worktree cleanup] rm -rf completed",
                file=_sys.stderr,
            )

    # --- Step 4: delete the worktree-pinned feature branch ---
    # Try to identify the feature branch name from the stale detection snapshot:
    # first from the branch_pinned HEAD ref (most reliable), then by scanning
    # git branch --list for any branch that contains the task_id.
    # _git_delete_feature_branch is idempotent and warns-and-proceeds when the
    # branch does not exist, so we attempt deletion even when the name is inferred.
    feature_branch_name: "str | None" = None
    if stale.branch_pinned and stale.registration_exists:
        # Read HEAD from registration to extract the exact branch name.
        head_file = dev_tree / ".git" / "worktrees" / task_id / "HEAD"
        if head_file.is_file():
            try:
                head_content = head_file.read_text(encoding="utf-8").strip()
                # Format: "ref: refs/heads/<branch_name>"
                prefix = "ref: refs/heads/"
                if head_content.startswith(prefix):
                    feature_branch_name = head_content[len(prefix):]
            except OSError:
                pass

    if feature_branch_name is None:
        # Fall back: scan git branch --list for any branch containing task_id.
        # If nothing is found, skip branch deletion (normal reset Step 4 handles it).
        r2 = _sp.run(
            ["git", "-C", str(dev_tree), "branch", "--list", f"*{task_id}"],
            capture_output=True,
            text=True,
        )
        if r2.returncode == 0:
            candidates = [
                line.strip().lstrip("* ").strip()
                for line in r2.stdout.splitlines()
                if line.strip()
            ]
            # Prefer the one that ends with /feature/<task_id> or similar.
            for c in candidates:
                if task_id in c:
                    feature_branch_name = c
                    break

    if feature_branch_name:
        print(
            f"reset: [worktree cleanup] deleting pinned feature branch"
            f" {feature_branch_name!r}",
            file=_sys.stderr,
        )
        _git_delete_feature_branch(dev_tree, feature_branch_name, branch_prefix)
        print(
            f"reset: [worktree cleanup] feature branch deleted",
            file=_sys.stderr,
        )
    else:
        print(
            f"reset: [worktree cleanup] feature branch not identified;"
            f" skipping branch deletion (will be handled by normal reset Step 4)",
            file=_sys.stderr,
        )

    print(
        f"reset: [worktree cleanup] stale worktree cleanup complete for task {task_id!r}",
        file=_sys.stderr,
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


def _write_task_terminal_state(status_file: Path, state: str, op: str) -> None:
    """Write STATE to ## State, clear ## Blockers and ## Needs Human atomically.

    Used by close_item (DONE) and wontdo_item (WONT-DO).  The sections are
    read and rewritten through ``pgai_agent_kanban.task_status`` — the one
    status.md implementation — in a single atomic write; ## Blockers and
    ## Needs Human are reset only when present, and every other section is
    preserved byte-for-byte.

    Args:
        status_file: Path to the task's status.md.
        state:       New state string to write (e.g. "DONE", "WONT-DO").
        op:          Calling operation name, used in error messages.

    Raises:
        OpsError: If status.md cannot be read or has no ## State heading.
        IoError:  If the atomic write fails.
    """
    try:
        text = status_file.read_text(encoding="utf-8")
    except OSError as exc:
        raise OpsError(f"{op}: failed to read {status_file}: {exc}") from exc

    if _task_status.read_section(text, "State") is None:
        raise OpsError(
            f"{op}: no ## State heading found in {status_file}; left untouched"
        )

    updates = {"State": state}
    for heading, value in (("Blockers", "none"), ("Needs Human", "no")):
        if _task_status.read_section(text, heading) is not None:
            updates[heading] = value

    _set_sections_or_raise(status_file, updates, op)


def _write_intake_close(item_path: Path, state: str, note: str) -> None:
    """Write STATE to ## Status and optionally record NOTE in ## Close Note.

    A missing ## Close Note section is created directly after ## Status.
    The write is a single atomic ``task_status.set_sections`` call.

    Args:
        item_path: Path to the intake .md file.
        state:     New status string (one of CLOSED_STATES, e.g. "done", "wont-do").
        note:      Optional close note text.  Empty string means no change
                   to ## Close Note.

    Raises:
        OpsError: If the file cannot be read or has no ## Status heading.
        IoError:  If the atomic write fails.
    """
    updates = {"Status": state}
    if note:
        updates["Close Note"] = note
    _set_sections_or_raise(item_path, updates, "close_item", insert_after="Status")


def _set_sections_or_raise(
    target: Path,
    updates: "dict[str, str]",
    op: str,
    *,
    insert_after: "str | None" = None,
) -> None:
    """Call ``task_status.set_sections``, mapping its refusals onto ops errors.

    A failed write becomes ``IoError`` (as every writer in this module
    reports one); a missing heading or unreadable file becomes ``OpsError``
    naming *op*.
    """
    try:
        _task_status.set_sections(target, updates, insert_after=insert_after)
    except _task_status.TaskStatusError as exc:
        if exc.error_key == "task-status.write-failed":
            raise IoError(f"_atomic_write: {exc.message}") from exc
        if exc.error_key == "task-status.missing-section":
            heading = next(
                (h for h in updates if f"'## {h}'" in exc.message), "section"
            )
            raise OpsError(
                f"{op}: no ## {heading} heading found in {target}; left untouched"
            ) from exc
        raise OpsError(f"{op}: {exc.message}") from exc


def _write_or_raise_io(target: Path, content: str) -> None:
    """Replace *target*'s contents atomically, reporting failure as IoError.

    The write itself is the shared ``atomic_write`` (same-directory temp
    file, fsync, ``os.replace``); this wrapper maps its ``OSError`` onto the
    ops layer's ``IoError`` so every writer in this module fails the same
    way.

    Args:
        target:  Destination path.
        content: UTF-8 string to write.

    Raises:
        IoError: If the temp-file create, write, or rename step fails.
    """
    try:
        atomic_write(target, content, prefix=".write_tmp_")
    except OSError as exc:
        raise IoError(
            f"_atomic_write: failed writing {target}: {exc}"
        ) from exc


# ---------------------------------------------------------------------------
# Internal marker-routing helpers
#
# Resolve which queue/backlog file holds an item's line and the key that line
# carries.  The marker write itself is set_marker / remove_marker, below;
# both are operate-if-present: a missing file is a no-op.
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
    AGENT-YYYYMMDD-NNN-slug → agent (lowercased).

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


# ---------------------------------------------------------------------------
# Queue-marker helpers (public)
#
# The one implementation of the queue-line marker write, and of the queue
# reads the wake acts on (queue_markers, scan_queue).  A queue line is a
# Markdown list item: leading "- ", the bracketed marker, the item key, and an
# optional description.  Reading is liberal (any spacing around the dash and
# key, any bracket content including the "[]" alias); writing is canonical
# (one of CANONICAL_MARKERS, "[ ]" for pending) and touches nothing on the
# line except the bracket content, so the list prefix and description suffix
# survive byte-for-byte.
# ---------------------------------------------------------------------------

#: The five marker characters a queue line may carry, in state order:
#: BACKLOG, WAITING, WORKING, BLOCKED, DONE/WONT-DO.
CANONICAL_MARKERS: tuple[str, ...] = (" ", "W", "A", "B", "x")


def _canonical_marker(marker: str) -> str:
    """Return the canonical form of *marker*, or raise OpsError.

    Surrounding whitespace is ignored and an empty value reads as the
    pending marker, so "", " " and "[]"-style callers all write "[ ]".
    """
    value = marker.strip() or " "
    if value not in CANONICAL_MARKERS:
        raise OpsError(
            f"set_marker: marker {marker!r} is not one of the queue markers "
            "' ', 'W', 'A', 'B', 'x'"
        )
    return value


def _marker_entry_re(item_key: str, ignore_case: bool) -> "re.Pattern[str]":
    """Compile the queue-entry pattern for *item_key*.

    Groups: 1 = list prefix up to the opening bracket's left edge ("- "),
    2 = the whitespace and key after the closing bracket.  The key must be
    followed by whitespace or end of line, so "TASK-1" never matches a line
    for "TASK-1-slug".
    """
    flags = re.MULTILINE | (re.IGNORECASE if ignore_case else 0)
    return re.compile(
        rf"^([ \t]*-[ \t]*)\[[^\]\n]*\]([ \t]+{re.escape(item_key)})(?=\s|$)",
        flags,
    )


def _read_queue_text(queue_file: Path, op: str) -> "str | None":
    """Return *queue_file*'s text, None when it does not exist.

    Raises:
        IoError: The file exists but cannot be read.
    """
    if not queue_file.is_file():
        return None
    try:
        return queue_file.read_text(encoding="utf-8")
    except OSError as exc:
        raise IoError(f"{op}: cannot read queue file {queue_file}: {exc}") from exc


def set_marker(
    queue_file: "Path | str",
    item_key: str,
    marker: str,
    *,
    ignore_case: bool = False,
) -> bool:
    """Rewrite the marker of every queue line for *item_key* to *marker*.

    Matches ``<ws>-<ws>[<anything>]<ws><item_key>`` followed by whitespace or
    end of line; the "[]" alias and any stale bracket content are accepted.
    Only the bracket content changes.  The file is rewritten atomically, and
    only when its bytes would change.

    Args:
        queue_file:  Path to the queue or backlog .md file.
        item_key:    The key as it appears on the queue line (task folder
                     name, or intake file stem).
        marker:      One of CANONICAL_MARKERS; "" is read as " ".
        ignore_case: Match *item_key* case-insensitively (intake backlogs).

    Returns:
        True when at least one line for *item_key* exists (whether or not
        its marker already read *marker*); False when the file is missing
        or holds no line for the key.

    Raises:
        OpsError: *marker* is not a queue marker.
        IoError:  The file exists but cannot be read or rewritten.
    """
    target = _canonical_marker(marker)
    path = Path(queue_file)
    text = _read_queue_text(path, "set_marker")
    if text is None:
        return False
    new_text, count = replace_marker(text, item_key, target, ignore_case=ignore_case)
    if count == 0:
        return False
    if new_text != text:
        _write_or_raise_io(path, new_text)
    return True


def replace_marker(
    text: str,
    item_key: str,
    marker: str,
    *,
    ignore_case: bool = False,
) -> "tuple[str, int]":
    """Return *text* with every queue line for *item_key* set to *marker*.

    The text-level half of :func:`set_marker`, with the same entry match and
    the same canonical marker, for a caller that commits the result through
    its own atomic writer.

    Returns:
        ``(new_text, lines_matched)``; *new_text* equals *text* when no line
        matched.

    Raises:
        OpsError: *marker* is not a queue marker.
    """
    target = _canonical_marker(marker)
    return _marker_entry_re(item_key, ignore_case).subn(
        lambda m: f"{m.group(1)}[{target}]{m.group(2)}", text
    )


def ensure_marker(text: "str | None", item_key: str, marker: str) -> str:
    """Return queue-file text whose line for *item_key* carries *marker*.

    The text-level writer behind discovery's backlog-cache flip, for a
    caller that commits the result through its own atomic writer.

    - A line for *item_key* exists: every such line is rewritten by
      :func:`replace_marker` (only the bracket changes).
    - *text* is ``None`` (no file yet): a new cache,
      ``# Backlog Cache`` / ``## Queue`` / ``- [<marker>] <item_key>``.
    - No line, a ``## Queue`` heading present: ``- [<marker>] <item_key>``
      is added after the last line of that section, its trailing blank
      lines cut so the entry follows directly.
    - No line and no ``## Queue``: a ``## Queue`` section holding the entry
      is appended at the end.

    Args:
        text:     Current file contents, or ``None`` when the file is absent.
        item_key: Key as it appears on the queue line (an intake file stem).
        marker:   One of CANONICAL_MARKERS; "" is read as " ".

    Returns:
        The new file contents.

    Raises:
        OpsError: *marker* is not a queue marker.
    """
    target = _canonical_marker(marker)
    entry = f"- [{target}] {item_key}"
    if text is None:
        return f"# Backlog Cache\n\n## Queue\n\n{entry}\n"
    new_text, count = replace_marker(text, item_key, target)
    if count:
        return new_text
    if re.search(r"^## Queue\s*$", text, re.M):
        return re.sub(
            r"(^## Queue\s*\n(?:.*\n)*?)(?=\n##|\Z)",
            lambda m: m.group(1).rstrip() + f"\n{entry}\n",
            text,
            count=1,
            flags=re.M,
        )
    suffix = "" if text.endswith("\n") else "\n"
    return text + suffix + f"\n## Queue\n\n{entry}\n"


def queue_markers(
    queue_file: "Path | str",
    item_key: str,
    *,
    ignore_case: bool = False,
) -> list[str]:
    """Return the marker of every queue line for *item_key*, in file order.

    Uses the same entry match as :func:`set_marker`.  Each marker is read
    by :func:`bracket_marker`: ``[ ]`` reads " ", the ``[]`` alias reads
    "", and other content reads as written, stripped.

    Returns:
        The markers; ``[]`` when the file is missing or holds no line for
        *item_key*.

    Raises:
        IoError: The file exists but cannot be read.
    """
    text = _read_queue_text(Path(queue_file), "queue_markers")
    if text is None:
        return []
    markers = []
    for m in _marker_entry_re(item_key, ignore_case).finditer(text):
        entry = m.group(0)
        markers.append(bracket_marker(entry[len(m.group(1)) + 1 : len(entry) - len(m.group(2)) - 1]))
    return markers


def bracket_marker(content: str) -> str:
    """Return the marker a queue line's bracket *content* reads as.

    Surrounding whitespace is dropped, so ``[ W ]`` reads "W".  A bracket
    holding only whitespace (``[ ]``) reads as the pending marker " "; the
    empty ``[]`` alias reads as "" so a reader can tell the two apart (the
    wake's pending scan serves ``[ ]`` lines and leaves ``[]`` ones alone).
    """
    stripped = content.strip()
    if stripped:
        return stripped
    return " " if content else ""


# A queue line as the wake's scans read it: an optional "- ", a bracketed
# marker, then the key and an optional description.
_QUEUE_LINE_RE = re.compile(r"^\s*-?\s*\[([^\]\n]*)\]\s+(\S+)(?:\s+.*)?$")


def _task_state_body(status_file: Path) -> str:
    """Return the ``## State`` body of *status_file*, stripped and uppercased.

    ``""`` when the file does not exist, the heading is absent, or the body
    is empty.  The whole body is used (no first-line or bold handling), so a
    value reads exactly as the writer left it.

    Raises:
        IoError: The file exists but cannot be read (an OS error, or bytes
                 that are not valid UTF-8).  :func:`scan_queue` catches it
                 per task, so one bad file never fails a whole scan.
    """
    if not status_file.is_file():
        return ""
    try:
        text = status_file.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError) as exc:
        raise IoError(f"scan_queue: cannot read {status_file}: {exc}") from exc
    return (_task_status.read_section(text, "State") or "").strip().upper()


def _narrate_unreadable_status(key: str, status_file: Path, exc: IoError) -> None:
    """Name a task :func:`scan_queue` left out because its status.md is unreadable.

    One stderr line: the key, the file, the reason, and that the rest of
    the scan went on.  The wake's log captures it, so the bad file is named
    on every firing until it is fixed.
    """
    reason = str(exc.__cause__ or exc)
    print(
        f"scan_queue: WARNING: skipped {key}: cannot read {status_file} ({reason}); "
        "the other tasks are still scanned. To bring the task back, rewrite the "
        "file as valid UTF-8 (iconv -f utf-8 -t utf-8 -c drops the invalid bytes)",
        file=sys.stderr,
    )


def parse_scan_rule(rule: str) -> "tuple[frozenset[str], frozenset[str] | None]":
    """Parse a ``MARKERS=STATES`` scan rule for :func:`scan_queue`.

    MARKERS is one or more queue-marker characters (space for pending);
    the two characters ``[]`` inside it name the empty-bracket alias, which
    a rule matches only when it names it.  STATES is ``*`` (any state,
    status.md need not exist) or a comma list of ``## State`` values, where
    an empty item stands for an empty or absent ``## State`` (or no
    status.md at all).  ``" B=,BACKLOG"`` selects ``[ ]`` and ``[B]`` lines
    whose task is BACKLOG or has no state yet; ``" []=WAITING"`` selects
    ``[ ]`` and ``[]`` lines whose task is WAITING.

    Returns:
        ``(markers, states)``: the marker strings (see
        :func:`bracket_marker`; "" is the alias) and the states, or
        ``None`` for ``*``.

    Raises:
        OpsError: no ``=``, no marker, or a character that is not a marker.
    """
    marker_text, sep, states = rule.partition("=")
    if not sep or not marker_text:
        raise OpsError(
            f"scan_queue: rule {rule!r} is not MARKERS=STATES "
            "(for example 'W=*' or ' B=,BACKLOG')"
        )
    alias = "[]" in marker_text
    chars = marker_text.replace("[]", "")
    unknown = sorted({char for char in chars if char not in CANONICAL_MARKERS})
    if unknown:
        raise OpsError(
            f"scan_queue: rule {rule!r} names {', '.join(map(repr, unknown))}, "
            "not one of the queue markers ' ', 'W', 'A', 'B', 'x'"
        )
    markers = frozenset(chars) | ({""} if alias else set())
    if states.strip() == "*":
        return markers, None
    return markers, frozenset(item.strip().upper() for item in states.split(","))


def scan_queue(
    queue_file: "Path | str",
    tasks_root: "Path | str",
    rules: "list[tuple[frozenset[str], frozenset[str] | None]]",
    *,
    id_pattern: "str | None" = None,
    limit: int = 0,
    unreadable: "list[Path] | None" = None,
) -> list[str]:
    """Return the keys of queue lines selected by *rules*, in file order.

    A line is selected when its marker (read by :func:`bracket_marker`) is
    one of the markers of the first rule that names it, and its
    task's ``## State`` body — read from ``<tasks_root>/<key>/status.md``
    through :func:`pgai_agent_kanban.task_status.read_section`, stripped
    and uppercased, ``""`` when there is no status.md or no value — is one
    of that rule's states (or the rule's states are ``None``: any).

    Args:
        queue_file: The agent backlog file.
        tasks_root: Directory holding one folder per task key.
        rules:      ``(markers, states)`` pairs, see :func:`parse_scan_rule`.
        id_pattern: A regex every key must match from its start; keys that
                    do not are skipped, so they never become a path.
        limit:      Stop after this many keys; 0 means no limit.
        unreadable: When given, each status.md the scan could not read is
                    appended to it.

    A task whose status.md a rule needs but cannot be read -- bytes that are
    not valid UTF-8, or an OS error -- is left out of the result and named
    in one stderr line; the other tasks are still scanned, so one bad file
    never empties a whole queue's scan.  A rule whose states are ``None``
    never reads status.md, so such a task is still selected by it.

    Raises:
        IoError: the queue file cannot be read.
    """
    path = Path(queue_file)
    try:
        text = path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError) as exc:
        raise IoError(f"scan_queue: cannot read queue file {path}: {exc}") from exc
    canonical = re.compile(id_pattern) if id_pattern else None
    found: list[str] = []
    for line in text.splitlines():
        m = _QUEUE_LINE_RE.match(line)
        if m is None:
            continue
        key = m.group(2)
        if canonical is not None and not canonical.match(key):
            continue
        marker = bracket_marker(m.group(1))
        rule = next((r for r in rules if marker in r[0]), None)
        if rule is None:
            continue
        states = rule[1]
        if states is not None:
            status_file = Path(tasks_root) / key / "status.md"
            try:
                state = _task_state_body(status_file)
            except IoError as exc:
                _narrate_unreadable_status(key, status_file, exc)
                if unreadable is not None:
                    unreadable.append(status_file)
                continue
            if state not in states:
                continue
        found.append(key)
        if limit and len(found) >= limit:
            break
    return found


def remove_marker(
    queue_file: "Path | str",
    item_key: str,
    *,
    ignore_case: bool = False,
) -> bool:
    """Delete every queue line for *item_key*, newline included.

    Uses the same entry match as set_marker.  The file is rewritten
    atomically when a line was removed.

    Args:
        queue_file:  Path to the queue or backlog .md file.
        item_key:    The key as it appears on the queue line.
        ignore_case: Match *item_key* case-insensitively (intake backlogs).

    Returns:
        True when a line was removed; False when the file is missing or
        holds no line for the key.

    Raises:
        IoError: The file exists but cannot be read or rewritten.
    """
    path = Path(queue_file)
    text = _read_queue_text(path, "remove_marker")
    if text is None:
        return False
    entry_re = _marker_entry_re(item_key, ignore_case)
    line_re = re.compile(entry_re.pattern + r"[^\n]*\n?", entry_re.flags)
    new_text, count = line_re.subn("", text)
    if count == 0:
        return False
    _write_or_raise_io(path, new_text)
    return True


def queue_entries(
    queue_file: "Path | str",
    item_key: str,
    *,
    ignore_case: bool = False,
) -> list[str]:
    """Return every queue line for *item_key*, whole and in file order.

    Uses the same entry match as :func:`set_marker` and
    :func:`remove_marker`, so the lines returned are exactly the lines
    :func:`remove_marker` deletes.  Each line is returned as written —
    marker, list prefix and description untouched — without its line
    ending.

    Returns:
        The lines; ``[]`` when the file is missing or holds no line for
        *item_key*.

    Raises:
        IoError: The file exists but cannot be read.
    """
    text = _read_queue_text(Path(queue_file), "queue_entries")
    if text is None:
        return []
    entry_re = _marker_entry_re(item_key, ignore_case)
    line_re = re.compile(entry_re.pattern + r"[^\n]*", entry_re.flags)
    return [m.group(0).rstrip("\r") for m in line_re.finditer(text)]


def append_queue_lines(queue_file: "Path | str", lines: "list[str] | tuple[str, ...]") -> int:
    """Append *lines* to the end of *queue_file*, each exactly as given.

    A line already present in the file, byte for byte, is not appended
    again, so re-running a move that was interrupted after this write
    leaves one copy.  A missing file is created holding just the appended
    lines; its directory must exist.  The file is rewritten atomically,
    and only when at least one line is appended.  Markers are written as
    given — this is how a line moves between queue files with its marker
    unchanged.

    Args:
        queue_file: Path to the queue or backlog .md file.
        lines:      Whole queue lines, without line endings.

    Returns:
        The number of lines appended.

    Raises:
        OpsError: A line contains a line break.
        IoError:  The file exists but cannot be read, or cannot be
                  written.
    """
    path = Path(queue_file)
    for line in lines:
        if "\n" in line or "\r" in line:
            raise OpsError(
                f"append_queue_lines: a queue line must be one line, got {line!r}"
            )
    text = _read_queue_text(path, "append_queue_lines") or ""
    present = set(text.splitlines())
    new_lines = []
    for line in lines:
        if line not in present:
            new_lines.append(line)
            present.add(line)
    if not new_lines:
        return 0
    if text and not text.endswith("\n"):
        text += "\n"
    _write_or_raise_io(path, text + "".join(f"{line}\n" for line in new_lines))
    return len(new_lines)


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
        python3 -m pgai_agent_kanban.ops defer        KANBAN_ROOT PROJECT UNTIL
        python3 -m pgai_agent_kanban.ops undefer      KANBAN_ROOT PROJECT
        python3 -m pgai_agent_kanban.ops set_version_ceiling KANBAN_ROOT PROJECT SHOW MINOR MAJOR
                                                             NO_MINOR NO_MAJOR DRY_RUN
        python3 -m pgai_agent_kanban.ops switch_provider KANBAN_ROOT PROVIDER
        python3 -m pgai_agent_kanban.ops close_item  PROJECT_ROOT KEY [STATE] [NOTE] [DRY_RUN]
        python3 -m pgai_agent_kanban.ops wontdo_item PROJECT_ROOT KEY
        python3 -m pgai_agent_kanban.ops delete_item PROJECT_ROOT KEY [FORCE]
        python3 -m pgai_agent_kanban.ops reset_item  PROJECT_ROOT KEY [KEEP_ARTIFACTS] [FORCE]
        python3 -m pgai_agent_kanban.ops set_marker    QUEUE_FILE KEY MARKER [IGNORE_CASE]
        python3 -m pgai_agent_kanban.ops remove_marker QUEUE_FILE KEY [IGNORE_CASE]
        python3 -m pgai_agent_kanban.ops render_marker QUEUE_FILE KEY MARKER [IGNORE_CASE]
        python3 -m pgai_agent_kanban.ops render_ensure_marker QUEUE_FILE KEY MARKER
        python3 -m pgai_agent_kanban.ops get_markers   QUEUE_FILE KEY [IGNORE_CASE]
        python3 -m pgai_agent_kanban.ops scan_queue    QUEUE_FILE TASKS_ROOT ID_PATTERN LIMIT RULE...

    IGNORE_CASE, like the other trailing flags, is "1" to enable.

    Exit codes:
        0  Success
        1  Error / argument error (printed to stderr)
        2  Ambiguous key (multiple matches); also used by delete_item for guard refusal;
           also used by reset_item for WORKING state refusal or stale-worktree-without-force
        3  Not found; also set_marker/remove_marker with no line for KEY
        4  State mutation failed / I/O error; also reset_item mount-pinned path
    """
    args = argv if argv is not None else sys.argv[1:]

    if not args:
        print(
            "write: VERB argument is required\n"
            "  verbs: halt, unhalt, halt_after, halt_global, unhalt_global,"
            " defer, undefer, set_version_ceiling, switch_provider, deposit_intake,"
            " close_item, wontdo_item, delete_item, reset_item, set_marker, remove_marker",
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

        elif verb in ("defer", "undefer"):
            return _cli_campaign(verb, rest)

        elif verb == "set_version_ceiling":
            return _cli_set_version_ceiling(rest)

        elif verb == "switch_provider":
            return _cli_switch_provider(rest)

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
            force = len(rest) > 3 and rest[3] == "1"
            ctx, project = _ctx_for_project_root(project_root)
            reset_item(ctx, project, key, keep_artifacts=keep_artifacts, force=force)

        elif verb == "set_marker":
            if len(rest) < 3:
                print(
                    "write set_marker: QUEUE_FILE, KEY and MARKER arguments are required",
                    file=sys.stderr,
                )
                return 1
            queue_file, key, marker = Path(rest[0]), rest[1], rest[2]
            ignore_case = len(rest) > 3 and rest[3] == "1"
            if not set_marker(queue_file, key, marker, ignore_case=ignore_case):
                print(
                    f"write set_marker: no queue line for {key} in {queue_file}; "
                    f"expected a line shaped '- [ ] {key} — description'",
                    file=sys.stderr,
                )
                return 3

        elif verb == "remove_marker":
            if len(rest) < 2:
                print(
                    "write remove_marker: QUEUE_FILE and KEY arguments are required",
                    file=sys.stderr,
                )
                return 1
            queue_file, key = Path(rest[0]), rest[1]
            ignore_case = len(rest) > 2 and rest[2] == "1"
            if not remove_marker(queue_file, key, ignore_case=ignore_case):
                print(
                    f"write remove_marker: no queue line for {key} in {queue_file}",
                    file=sys.stderr,
                )
                return 3

        elif verb == "render_marker":
            # Text-level set_marker for the wake's mark_backlog, which commits
            # stdout through atomic_write.sh (keeping the queue file's mode).
            # The "no line" message is the wake writer's own.
            if len(rest) < 3:
                print(
                    "write render_marker: QUEUE_FILE, KEY and MARKER arguments are required",
                    file=sys.stderr,
                )
                return 1
            queue_file, key, marker = Path(rest[0]), rest[1], rest[2]
            ignore_case = len(rest) > 3 and rest[3] == "1"
            text = _read_queue_text(queue_file, "render_marker")
            new_text, count = replace_marker(text or "", key, marker, ignore_case=ignore_case)
            if count == 0:
                print(f"Could not update backlog entry for {key}", file=sys.stderr)
                return 3
            sys.stdout.write(new_text)

        elif verb == "render_ensure_marker":
            # Text-level ensure_marker for discovery's backlog-cache flip,
            # which commits stdout through atomic_write.sh. A missing file
            # renders a new cache.
            if len(rest) < 3:
                print(
                    "write render_ensure_marker: QUEUE_FILE, KEY and MARKER arguments are required",
                    file=sys.stderr,
                )
                return 1
            text = _read_queue_text(Path(rest[0]), "render_ensure_marker")
            sys.stdout.write(ensure_marker(text, rest[1], rest[2]))

        elif verb == "get_markers":
            if len(rest) < 2:
                print(
                    "write get_markers: QUEUE_FILE and KEY arguments are required",
                    file=sys.stderr,
                )
                return 1
            ignore_case = len(rest) > 2 and rest[2] == "1"
            markers = queue_markers(Path(rest[0]), rest[1], ignore_case=ignore_case)
            if not markers:
                return 3
            for found in markers:
                print(f"[{found}]")

        elif verb == "scan_queue":
            if len(rest) < 5 or not rest[3].isdigit():
                print(
                    "write scan_queue: QUEUE_FILE, TASKS_ROOT, ID_PATTERN, LIMIT (a "
                    "number, 0 for all) and at least one MARKERS=STATES rule are required",
                    file=sys.stderr,
                )
                return 1
            rules = [parse_scan_rule(rule) for rule in rest[4:]]
            for key in scan_queue(
                Path(rest[0]), Path(rest[1]), rules, id_pattern=rest[2] or None, limit=int(rest[3])
            ):
                print(key)

        else:
            print(
                f"write: unknown verb {verb!r}\n"
                "  verbs: halt, unhalt, halt_after, halt_global, unhalt_global,"
                " defer, undefer, set_version_ceiling, switch_provider, deposit_intake,"
                " close_item, wontdo_item, delete_item, reset_item, set_marker, remove_marker,"
                " render_marker, render_ensure_marker,"
                " get_markers, scan_queue",
                file=sys.stderr,
            )
            return 1

    except OpsError as exc:
        # The message goes to stderr; the exit code comes from the one shared
        # table (ops.errors.cli_exit_code) the operator API's ops adapter also
        # uses: Ambiguous/Refused 2, NotFound 3, MountPinned/IoError 4, an
        # intake no-clobber 3, any other OpsError 1.
        print(exc, file=sys.stderr)
        return cli_exit_code(exc)

    return 0


def _cli_campaign(verb: str, rest: list[str]) -> int:
    """Run the defer / undefer CLI verbs: ``KANBAN_ROOT PROJECT [UNTIL]``.

    Prints the success narration to stdout. Every refusal exits 1, the code
    defer.sh / undefer.sh exit with; a refusal that carries the dual-channel
    block is printed through the shared emitter, so it is recorded for the
    ambient project exactly as the bash emitter records it.
    """
    want = 3 if verb == "defer" else 2
    if len(rest) != want:
        args = "KANBAN_ROOT, PROJECT and UNTIL" if verb == "defer" else "KANBAN_ROOT and PROJECT"
        print(f"write {verb}: {args} arguments are required", file=sys.stderr)
        return 1
    ctx = OpsContext(kanban_root=Path(rest[0]))
    try:
        change = defer(ctx, rest[1], rest[2]) if verb == "defer" else undefer(ctx, rest[1])
    except OpsError as exc:
        contract = parse_error_contract(str(exc))
        if contract is None:
            print(exc, file=sys.stderr)
        else:
            emit_error_contract(contract["error"], contract["message"], contract["remedy"])
        return 1
    print(change.narration)
    return 0


def _print_refusal(exc: OpsError) -> None:
    """Print an ops refusal to stderr as the CLI scripts do.

    A refusal carrying the dual-channel block goes through the shared
    emitter, so it is recorded for the ambient project exactly as the bash
    emitter records it; any other refusal is printed as is.
    """
    contract = parse_error_contract(str(exc))
    if contract is None:
        print(exc, file=sys.stderr)
    else:
        emit_error_contract(contract["error"], contract["message"], contract["remedy"])


def _cli_set_version_ceiling(rest: list[str]) -> int:
    """Run ``set_version_ceiling KANBAN_ROOT PROJECT SHOW MINOR MAJOR NO_MINOR NO_MAJOR DRY_RUN``.

    Prints the report lines to stdout. Exits 2 when the project has no
    project.cfg and 1 on every other refusal (set_version_ceiling_exit_code).
    """
    if len(rest) != 8:
        print(
            "write set_version_ceiling: KANBAN_ROOT, PROJECT, SHOW, MINOR, MAJOR, NO_MINOR, "
            "NO_MAJOR and DRY_RUN arguments are required",
            file=sys.stderr,
        )
        return 1
    kanban_root, project, show, minor, major, no_minor, no_major, dry_run = rest
    ctx = OpsContext(kanban_root=Path(kanban_root))
    try:
        report = set_version_ceiling(
            ctx, project,
            show=show == "true", minor=minor, major=major,
            no_minor=no_minor == "true", no_major=no_major == "true",
            dry_run=dry_run == "true",
        )
    except OpsError as exc:
        _print_refusal(exc)
        return set_version_ceiling_exit_code(exc)
    for line in report.lines:
        print(line)
    return 0


def _cli_switch_provider(rest: list[str]) -> int:
    """Run ``switch_provider KANBAN_ROOT PROVIDER``; every refusal exits 1."""
    if len(rest) != 2:
        print("write switch_provider: KANBAN_ROOT and PROVIDER arguments are required", file=sys.stderr)
        return 1
    ctx = OpsContext(kanban_root=Path(rest[0]))
    try:
        change = switch_provider(ctx, rest[1])
    except OpsError as exc:
        _print_refusal(exc)
        return 1
    print(change.narration)
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
