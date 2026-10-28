"""
halt_clearing.py — whether the cause of a chain HALT is resolved (reads only).

The resolution stage (:mod:`pgai_agent_kanban.overwatch.resolution_stage`)
may clear a HALT the chain wrote once the reason for it is gone, and never
touches an operator's HALT. This module answers the "is the reason gone"
half for it. Every function here reads files and git refs and writes
nothing; the stage does the clearing, through ``ops.write.unhalt``.

The checks (names from the resolution matrix's ``triggers`` rows,
:data:`pgai_agent_kanban.overwatch.resolution_matrix.CLEAR_CHECKS`):

``rc-cancelled`` (:func:`check_rc_cancelled`)
    ``## Active RC`` reads ``none``; a release-state record under
    ``release-state/`` or ``release-state/history/`` has ``outcome``
    ``cancelled`` and a ``closed_at`` no earlier than the HALT was written;
    and no cargo file that record names (``bugs/`` or ``priority/``) still
    reads ``## Status`` ``running`` -- the bundle was taken apart for a
    rebundle.
``task-reset`` (:func:`check_task_reset`)
    The named task's ``## State`` reads ``BACKLOG`` or ``WAITING``: it was
    reset and waits to run again.
``notes-status-green`` (:func:`check_notes_status_green`)
    Trigger 9 re-checked: ``release-notes/<version>.md`` on the RC branch is
    absent (the release generates its own) or carries a ``## Status``
    section the release stamp applies to, read with the stamp's own
    functions (:mod:`pgai_agent_kanban.cm.notes_status_section`).

Each check returns a :class:`CauseCheck` whose ``evidence`` line names what
it read, for the stage's Decision event. A check that cannot read what it
needs fails: an unproven cause is an unresolved one.

The HALT-AFTER helpers name the version a completed drain stopped after
(:func:`halt_after_version`) and the inbox text the stage writes when it
clears that HALT (:func:`halt_after_resume_text`).

Command line (reads only)::

    python3 -m pgai_agent_kanban.overwatch.halt_clearing check \\
        --project-root DIR --check rc-cancelled [--halt FILE] [--task ID] \\
        [--version vX.Y.Z --dev-tree DIR --rc-branch BRANCH]

prints ``passed|failed<TAB>check<TAB>evidence`` and exits 0; 2 is a usage
error.
"""

from __future__ import annotations

import argparse
import dataclasses
import datetime
import json
import os
import pathlib
import re
import subprocess
import sys
from typing import Optional

# Run by file path the module has no parent package; put this tree's own
# package root first so the package imports resolve from the same tree.
if not __package__:
    _OWN_PACKAGE_ROOT = str(pathlib.Path(__file__).resolve().parents[2])
    if _OWN_PACKAGE_ROOT not in sys.path:
        sys.path.insert(0, _OWN_PACKAGE_ROOT)

from pgai_agent_kanban.cm.notes_status_section import (  # noqa: E402
    has_status_section,
    stamp_status_placeholder,
    status_section_has_placeholder,
)
from pgai_agent_kanban.cm.read_state_field import read_state_field  # noqa: E402
from pgai_agent_kanban.lib.timeutil import try_parse_iso  # noqa: E402
from pgai_agent_kanban.overwatch.resolution_matrix import (  # noqa: E402
    CLEAR_NOTES_STATUS_GREEN,
    CLEAR_RC_CANCELLED,
    CLEAR_TASK_RESET,
)
from pgai_agent_kanban.task_status import read_state  # noqa: E402

__all__ = [
    "CauseCheck",
    "RESET_STATES",
    "check_notes_status_green",
    "check_rc_cancelled",
    "check_task_reset",
    "halt_after_resume_text",
    "halt_after_version",
    "halt_written_at",
    "task_id_in",
]

#: Task states that mean "reset and waiting to run again".
RESET_STATES = frozenset({"BACKLOG", "WAITING"})

# The first ISO-8601 timestamp on a HALT's first line ("# HALT created by CM
# at 2026-07-27T00:00:00Z", "... promotion at 2026-05-29T01:23:45+00:00: rc").
_HEADER_TIME_RE = re.compile(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d+)?(?:Z|[+-]\d{2}:?\d{2})?")
# A task ID: <ROLE>-YYYYMMDD-NNN-slug (the legacy CLAUDE- prefix included).
_TASK_ID_RE = re.compile(r"\b[A-Z][A-Z0-9]*-\d{8}-\d{3}-[a-z0-9][a-z0-9-]*")
# "HALT-AFTER rc:v1.2.3 auto-promotion" -> v1.2.3 (liberal on spacing and case).
_HALT_AFTER_VERSION_RE = re.compile(r"HALT-AFTER\s+rc\s*:\s*(v\d+\.\d+\.\d+)\b", re.IGNORECASE)
_VERSION_RE = re.compile(r"^v\d+\.\d+\.\d+$")
_RUNNING = "running"
# Stands in for the release's real status when the stamp is only being tried.
_PROBE_STATUS = "SHIP"


@dataclasses.dataclass(frozen=True)
class CauseCheck:
    """One cause check's answer.

    Attributes:
        check:    The check name (``rc-cancelled``, ``task-reset``, ...).
        passed:   True when the cause is resolved.
        evidence: One line saying what was read and what it showed.
        paths:    Files the evidence came from, for the Decision event.
    """

    check: str
    passed: bool
    evidence: str
    paths: tuple = ()


def halt_written_at(halt_path: "str | os.PathLike[str]", text: Optional[str] = None) -> Optional[datetime.datetime]:
    """When the HALT was written: the timestamp on its first line, else its mtime.

    None when neither can be read.
    """
    if text is None:
        try:
            text = pathlib.Path(halt_path).read_text(encoding="utf-8", errors="replace")
        except OSError:
            text = ""
    first = text.lstrip().split("\n", 1)[0] if text.strip() else ""
    match = _HEADER_TIME_RE.search(first)
    if match:
        parsed = try_parse_iso(match.group(0))
        if parsed is not None:
            return parsed
    try:
        mtime = os.path.getmtime(halt_path)
    except OSError:
        return None
    return datetime.datetime.fromtimestamp(mtime, tz=datetime.timezone.utc)


def task_id_in(text: Optional[str]) -> Optional[str]:
    """The first task ID *text* names, or None."""
    if not text:
        return None
    match = _TASK_ID_RE.search(text)
    return match.group(0) if match else None


def _rc_records(project_root: pathlib.Path) -> list:
    """``(path, record)`` for every readable release-state JSON record, slot and history."""
    found = []
    for directory in (project_root / "release-state", project_root / "release-state" / "history"):
        if not directory.is_dir():
            continue
        for path in sorted(directory.glob("*.json")):
            try:
                record = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                continue
            if isinstance(record, dict):
                found.append((path, record))
    return found


def _cargo_still_running(project_root: pathlib.Path, cargo: object) -> list:
    """Cargo basenames whose bug or priority file still reads ``## Status`` running."""
    running = []
    if not isinstance(cargo, list):
        return running
    for name in cargo:
        if not isinstance(name, str) or not name.strip():
            continue
        base = os.path.basename(name.strip())
        for directory in ("bugs", "priority"):
            path = project_root / directory / base
            if path.is_file() and read_state_field(str(path), "Status").strip().lower() == _RUNNING:
                running.append(base)
                break
    return running


def check_rc_cancelled(
    project_root: "str | os.PathLike[str]",
    written_at: Optional[datetime.datetime],
) -> CauseCheck:
    """``rc-cancelled``: an RC was cancelled after the HALT and its cargo rebundled.

    Args:
        project_root: ``<kanban_root>/projects/<name>``.
        written_at:   When the HALT was written (:func:`halt_written_at`);
                      None fails the check, since "after the HALT" cannot be shown.
    """
    root = pathlib.Path(project_root)
    release_state = root / "release-state.md"
    active = read_state_field(str(release_state), "Active RC").strip()
    if active != "none":
        return CauseCheck(CLEAR_RC_CANCELLED, False, f"Active RC is {active}, not none", (str(release_state),))
    if written_at is None:
        return CauseCheck(CLEAR_RC_CANCELLED, False, "the HALT's write time cannot be read", (str(release_state),))
    newest = None
    for path, record in _rc_records(root):
        if record.get("outcome") != "cancelled":
            continue
        closed = try_parse_iso(str(record.get("closed_at") or ""))
        if closed is None or closed < written_at:
            continue
        if newest is None or closed > newest[0]:
            newest = (closed, path, record)
    if newest is None:
        return CauseCheck(
            CLEAR_RC_CANCELLED, False,
            f"Active RC is none but no RC record shows a cancel at or after {written_at.isoformat()}",
            (str(release_state),),
        )
    _closed, path, record = newest
    rc = record.get("rc") or record.get("version") or path.stem
    running = _cargo_still_running(root, record.get("cargo"))
    if running:
        return CauseCheck(
            CLEAR_RC_CANCELLED, False,
            f"{rc} was cancelled ({path.name}) but cargo still reads running: {', '.join(running)}",
            (str(release_state), str(path)),
        )
    return CauseCheck(
        CLEAR_RC_CANCELLED, True,
        f"{rc} was cancelled at {record.get('closed_at')} ({path.name}), Active RC is none and no cargo reads running",
        (str(release_state), str(path)),
    )


def check_task_reset(project_root: "str | os.PathLike[str]", task_id: Optional[str]) -> CauseCheck:
    """``task-reset``: *task_id* reads BACKLOG or WAITING."""
    if not task_id:
        return CauseCheck(CLEAR_TASK_RESET, False, "no task is named for this HALT")
    status = pathlib.Path(project_root) / "tasks" / task_id / "status.md"
    if not status.is_file():
        return CauseCheck(CLEAR_TASK_RESET, False, f"{task_id} has no status.md", (str(status),))
    state = read_state(status)
    if state in RESET_STATES:
        return CauseCheck(CLEAR_TASK_RESET, True, f"{task_id} was reset and reads {state}", (str(status),))
    return CauseCheck(CLEAR_TASK_RESET, False, f"{task_id} reads {state or 'no state'}, not reset", (str(status),))


def _git(dev_tree: str, *args: str) -> subprocess.CompletedProcess:
    return subprocess.run(["git", "-C", dev_tree, *args], capture_output=True, text=True, check=False)


def check_notes_status_green(
    version: Optional[str],
    dev_tree: Optional[str],
    rc_branch: Optional[str],
) -> CauseCheck:
    """``notes-status-green``: trigger 9 re-checked on the RC branch.

    Reads ``release-notes/<version>.md`` at *rc_branch* in *dev_tree* with
    ``git show``. Passes when the branch has no such file (the release
    writes its own notes) or the file has a ``## Status`` section and,
    when it still holds the placeholder, the release stamp replaces it.
    """
    if not (version and _VERSION_RE.match(version) and dev_tree and rc_branch):
        return CauseCheck(CLEAR_NOTES_STATUS_GREEN, False, "no active RC, dev tree or RC branch to re-check")
    try:
        branch = _git(dev_tree, "rev-parse", "--verify", "--quiet", f"refs/heads/{rc_branch}")
    except OSError as exc:
        return CauseCheck(CLEAR_NOTES_STATUS_GREEN, False, f"git cannot run in {dev_tree}: {exc}")
    if branch.returncode != 0:
        return CauseCheck(CLEAR_NOTES_STATUS_GREEN, False, f"branch {rc_branch} is not in {dev_tree}")
    notes = f"release-notes/{version}.md"
    shown = _git(dev_tree, "show", f"{rc_branch}:{notes}")
    if shown.returncode != 0:
        listed = _git(dev_tree, "ls-tree", "--name-only", rc_branch, notes)
        if listed.returncode == 0 and not listed.stdout.strip():
            return CauseCheck(
                CLEAR_NOTES_STATUS_GREEN, True,
                f"{rc_branch} has no {notes}; the release generates its own notes",
            )
        return CauseCheck(CLEAR_NOTES_STATUS_GREEN, False, f"{notes} on {rc_branch} cannot be read")
    text = shown.stdout
    if not has_status_section(text):
        return CauseCheck(CLEAR_NOTES_STATUS_GREEN, False, f"{notes} on {rc_branch} still has no ## Status heading")
    if status_section_has_placeholder(text):
        stamped = stamp_status_placeholder(text, _PROBE_STATUS)
        if stamped is None or status_section_has_placeholder(stamped) or not has_status_section(stamped):
            return CauseCheck(
                CLEAR_NOTES_STATUS_GREEN, False,
                f"{notes} on {rc_branch} still holds a placeholder the release stamp cannot replace",
            )
    return CauseCheck(
        CLEAR_NOTES_STATUS_GREEN, True,
        f"{notes} on {rc_branch} carries a ## Status section the release stamp applies to",
    )


def halt_after_version(reason: Optional[str], release_state_path: "str | os.PathLike[str]") -> Optional[str]:
    """The version a completed HALT-AFTER drain stopped after.

    The ``rc:vX.Y.Z`` token in the HALT's reason when it names one, else
    ``## Last Released`` in release-state.md (every promotion happens at an
    RC close, so that is the release the drain waited for). None when
    neither names a version.
    """
    match = _HALT_AFTER_VERSION_RE.search(reason or "")
    if match:
        return match.group(1)
    last = read_state_field(str(release_state_path), "Last Released").strip()
    return last if _VERSION_RE.match(last) else None


def halt_after_resume_text(version: str) -> str:
    """The inbox question for a cleared HALT-AFTER drain, verbatim as specified."""
    return f"stopped after rc:{version} as armed; resume with unhalt"


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def main(argv: Optional[list] = None) -> int:
    """Run one cause check and print its answer. Exit 0 answered; 2 usage error."""
    parser = argparse.ArgumentParser(
        prog="python3 -m pgai_agent_kanban.overwatch.halt_clearing",
        description="Say whether the cause of a chain HALT is resolved (reads only).",
    )
    sub = parser.add_subparsers(dest="command", required=True)
    check_p = sub.add_parser("check", help="Run one cause check.")
    check_p.add_argument("--project-root", required=True, help="the project directory, e.g. $PGAI_AGENT_KANBAN_ROOT_PATH/projects/demo")
    check_p.add_argument(
        "--check", required=True, choices=sorted([CLEAR_RC_CANCELLED, CLEAR_TASK_RESET, CLEAR_NOTES_STATUS_GREEN]),
        help="the cause check to run (required, no default): rc-cancelled (the RC was cancelled after the "
             "HALT was written), task-reset (the --task task is back in BACKLOG or WAITING), or "
             "notes-status-green (the RC's release notes pass the Status check; needs --version, "
             "--dev-tree and --rc-branch)",
    )
    check_p.add_argument("--halt", default=None, help="the HALT file (rc-cancelled; default: PROJECT_ROOT/HALT)")
    check_p.add_argument("--task", default=None, help="the task ID (task-reset)")
    check_p.add_argument("--version", default=None, help="the active RC (notes-status-green)")
    check_p.add_argument("--dev-tree", default=None, help="the project's dev tree (notes-status-green)")
    check_p.add_argument("--rc-branch", default=None, help="the RC branch, e.g. ai_rc/v1.2.3 (notes-status-green)")
    args = parser.parse_args(argv)

    if args.check == CLEAR_RC_CANCELLED:
        halt = args.halt or os.path.join(args.project_root, "HALT")
        result = check_rc_cancelled(args.project_root, halt_written_at(halt))
    elif args.check == CLEAR_TASK_RESET:
        result = check_task_reset(args.project_root, args.task)
    else:
        result = check_notes_status_green(args.version, args.dev_tree, args.rc_branch)
    print("\t".join(["passed" if result.passed else "failed", result.check, result.evidence]))
    return 0


if __name__ == "__main__":
    sys.exit(main())
