"""
verification_retry.py -- the one requeue a release's verification gets.

When a release candidate's verification did not run -- its TESTER task is
BLOCKED, or it reads DONE with no ``artifacts/report.md`` -- the release
neither HALTs nor ships on nothing. The first time, for this RC, the TESTER
task is returned to BACKLOG carrying its block cause; the second time the
release ships NON-FUNCTIONAL, says "verification did not run" under its
notes' ``## Known Issues`` and leaves the operator one inbox entry naming
the TESTER task and the cause. Two callers share this module:

* the release's ship-policy stage (``team/scripts/lib/cm/
  release_verification_retry.sh``, from ``cm/release.sh`` Step 4d), which
  meets the report-missing case and, when it runs while the task is
  BLOCKED, the blocked case;
* the decision wake (:mod:`pgai_agent_kanban.overwatch.decide` and its
  executor), which meets a BLOCKED TESTER task first, because the release
  task waits on it: the first block is returned to BACKLOG through the
  ``reset-to-backlog`` verb, and a block after the requeue is closed through
  ``close-unverified`` so the waiting release runs and ships it recorded.

The retry record
----------------
"Once per RC" is one file per release version, beside the other per-install
CM state::

    <project root>/release-state/verification-retry-<vX.Y.Z>

holding ``key=value`` lines -- ``version``, ``tester_task``, ``case``
(``tester-blocked`` or ``report-missing``), ``cause`` (one line),
``requeued_at`` and ``requeued_by`` (``cm-release`` or ``decision-wake``).
The file existing means the RC's one requeue is spent, whatever the case and
whichever caller spent it; it is never removed (a later RC has its own
version). The version is checked against ``vX.Y.Z`` before it names a path.

Where the next TESTER session reads the cause
---------------------------------------------
The requeue appends one line to the TESTER README's ``## Triage Note``
(:func:`pgai_agent_kanban.overwatch.block_triage.append_triage_note`), the
section the next dispatch reads, and names the cause in the status.md
``## Summary`` the return writes
(:func:`pgai_agent_kanban.overwatch.task_return.return_to_backlog`).

Usage (import)::

    from pgai_agent_kanban.cm.verification_retry import read_record, requeue_task
    if read_record(project_root, "v1.2.3") is None:
        requeue_task(kanban_root, "example", "v1.2.3", "TESTER-20260101-001-verify",
                     case="tester-blocked", cause="the suite runner could not start",
                     requeued_by="cm-release")

Usage (CLI, consumed by release_verification_retry.sh)::

    python3 -m pgai_agent_kanban.cm.verification_retry status --project-root DIR --version V
    python3 -m pgai_agent_kanban.cm.verification_retry cause --task-dir DIR --case CASE
    python3 -m pgai_agent_kanban.cm.verification_retry requeue --kanban-root DIR --project NAME \\
        --version V --task ID --case CASE --cause TEXT [--by cm-release] [--project-root DIR]
    python3 -m pgai_agent_kanban.cm.verification_retry inbox --kanban-root DIR --project NAME \\
        --version V --task ID --case CASE --cause TEXT
    python3 -m pgai_agent_kanban.cm.verification_retry notes-block --case CASE

Each prints ``key=value`` lines (``notes-block`` prints the block itself).
``status`` prints ``retry_spent=yes|no`` and, when spent, the record's
fields. ``requeue`` prints ``outcome=requeued`` or ``outcome=spent`` (the
record already existed; nothing written). ``inbox`` prints ``entry_id=``.
A refusal prints the dual-channel ``error:`` / ``message:`` / ``remedy:``
block on stderr and exits 1, having written nothing; 2 is a usage error.
"""

from __future__ import annotations

import argparse
import dataclasses
import datetime
import pathlib
import re
import shlex
import sys
from pathlib import Path
from typing import Optional

# Run by file path the module has no parent package; put this tree's own
# package root first so the package imports resolve from the same tree.
# Imported as a package member, sys.path is left untouched.
if not __package__:
    _OWN_PACKAGE_ROOT = str(pathlib.Path(__file__).resolve().parents[2])
    if _OWN_PACKAGE_ROOT not in sys.path:
        sys.path.insert(0, _OWN_PACKAGE_ROOT)

from pgai_agent_kanban import task_status as ts  # noqa: E402
from pgai_agent_kanban.cm.release_version import task_release_version  # noqa: E402
from pgai_agent_kanban.lib.atomic_write import atomic_write  # noqa: E402
from pgai_agent_kanban.lib.error_contract import emit_error_contract  # noqa: E402
from pgai_agent_kanban.lib.text import one_line  # noqa: E402
from pgai_agent_kanban.lib.timeutil import format_iso, moment_or_now  # noqa: E402
from pgai_agent_kanban.lib.version_arg import (  # noqa: E402
    RELEASE_VERSION_HELP,
    checked_release_version,
    is_release_version,
)
from pgai_agent_kanban.ops.context import OpsContext  # noqa: E402
from pgai_agent_kanban.ops.errors import ContractError  # noqa: E402
from pgai_agent_kanban.overwatch import inbox as operator_inbox  # noqa: E402
from pgai_agent_kanban.overwatch.block_triage import append_triage_note  # noqa: E402
from pgai_agent_kanban.overwatch.task_return import find_queue_file, return_to_backlog  # noqa: E402

__all__ = [
    "CASES",
    "CASE_REPORT_MISSING",
    "CASE_TESTER_BLOCKED",
    "RetryRecord",
    "VerificationRetryError",
    "block_cause",
    "inbox_question",
    "notes_block",
    "read_record",
    "record_path",
    "record_requeue",
    "requeue_task",
    "triage_note",
    "verification_facts",
    "verifies_version",
    "write_inbox_entry",
]

#: The TESTER task reads BLOCKED: verification could not complete.
CASE_TESTER_BLOCKED = "tester-blocked"
#: The TESTER task reads DONE but wrote no report.
CASE_REPORT_MISSING = "report-missing"
CASES: tuple[str, ...] = (CASE_TESTER_BLOCKED, CASE_REPORT_MISSING)

#: Who spent the requeue.
BY_CM_RELEASE = "cm-release"
BY_DECISION_WAKE = "decision-wake"
_REQUEUERS = (BY_CM_RELEASE, BY_DECISION_WAKE)

#: The record directory, beside release-state.md (CM's per-install state).
_RECORD_DIR = "release-state"
_RECORD_PREFIX = "verification-retry-"
_RECORD_FIELDS = ("version", "tester_task", "case", "cause", "requeued_at", "requeued_by")

#: A task ID, the only shape that may name a task folder.
_TASK_ID_RE = re.compile(r"^[A-Za-z][A-Za-z0-9]*-\d{8}-\d+-[a-z0-9][a-z0-9-]*$")

#: The remedy of a bad-argument refusal: the command line's own help.
_HELP_COMMAND = "python3 -m pgai_agent_kanban.cm.verification_retry requeue --help"
#: The error key of a malformed version.
_BAD_VERSION_KEY = "verification-retry.bad-version"

#: The longest block cause carried into a note, a summary or an inbox entry.
_CAUSE_LIMIT = 400

#: What each case reads as, in a sentence ("the previous attempt ...").
_CASE_PHRASES = {
    CASE_TESTER_BLOCKED: "was BLOCKED, so verification could not complete",
    CASE_REPORT_MISSING: "read DONE but wrote no artifacts/report.md",
}

#: The notes line every unverified ship carries under ## Known Issues.
NOTES_LINE = (
    "Verification did not run: this release candidate's verification failed twice "
    "(the second time after one automatic retry), so it ships unverified."
)


class VerificationRetryError(ContractError):
    """Bad input to a write: nothing was written.

    Carries the dual-channel fields (``error``, ``message``, ``remedy``).
    """


@dataclasses.dataclass(frozen=True)
class RetryRecord:
    """One RC's spent requeue, as its record file reads."""

    version: str
    tester_task: str
    case: str
    cause: str
    requeued_at: str
    requeued_by: str

    def as_lines(self) -> list:
        """The record's ``key=value`` lines, in field order."""
        return [f"{name}={getattr(self, name)}" for name in _RECORD_FIELDS]


# ---------------------------------------------------------------------------
# Validation
# ---------------------------------------------------------------------------


def _clip(value: str, limit: int = _CAUSE_LIMIT) -> str:
    text = one_line(value or "")
    return text if len(text) <= limit else text[: limit - 3].rstrip() + "..."


def _record_version(version: str) -> str:
    """*version* stripped, when it may name a retry record.

    Raises:
        VerificationRetryError: *version* is not ``vX.Y.Z``.
    """
    # CONVERT: a malformed version is refused before anything is written;
    # main() narrates the refusal and release_verification_retry.sh
    # carries on.
    return checked_release_version(
        version, VerificationRetryError, _BAD_VERSION_KEY, _HELP_COMMAND,
        consequence="it cannot name a verification-retry record",
    )


def _checked_task_id(task_id: str) -> str:
    value = (task_id or "").strip()
    if not _TASK_ID_RE.match(value):
        raise VerificationRetryError(
            "verification-retry.bad-task-id",
            f"'{value}' is not a task ID (ROLE-YYYYMMDD-NNN-slug, for example TESTER-20260101-001-verify-1-2-3: "
            "the TESTER task's folder name), so it cannot name a TESTER task folder.",
            _HELP_COMMAND,
        )
    return value


def _checked_case(case: str) -> str:
    value = (case or "").strip()
    if value not in CASES:
        raise VerificationRetryError(
            "verification-retry.bad-case",
            f"'{value}' is not a verification-did-not-run case; the cases are {CASE_TESTER_BLOCKED} (a BLOCKED "
            f"TESTER task) and {CASE_REPORT_MISSING} (a DONE one without a report).",
            _HELP_COMMAND,
        )
    return value


# ---------------------------------------------------------------------------
# The record
# ---------------------------------------------------------------------------


def record_path(project_root: "Path | str", version: str) -> Path:
    """Where *version*'s retry record lives under *project_root*."""
    return Path(project_root) / _RECORD_DIR / f"{_RECORD_PREFIX}{_record_version(version)}"


def read_record(project_root: "Path | str", version: str) -> Optional[RetryRecord]:
    """*version*'s retry record, or None when its requeue is not spent.

    Read liberally: unknown keys are ignored and a missing key reads as
    ``unknown``. A record file that exists but cannot be read still counts
    as spent (its fields read ``unknown``), so an unreadable file never
    grants a second requeue.
    """
    path = record_path(project_root, version)
    if not path.exists():
        return None
    values: dict = {}
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        text = ""
    for line in text.splitlines():
        key, sep, value = line.partition("=")
        if sep and key.strip() in _RECORD_FIELDS:
            values[key.strip()] = value.strip()
    return RetryRecord(**{name: values.get(name) or "unknown" for name in _RECORD_FIELDS})


def _new_record(
    version: str, task_id: str, *, case: str, cause: str, requeued_by: str,
    now: "datetime.datetime | str | None" = None,
) -> RetryRecord:
    """The record a requeue writes, validated; nothing is written."""
    return RetryRecord(
        version=_record_version(version),
        tester_task=_checked_task_id(task_id),
        case=_checked_case(case),
        cause=_clip(cause) or "no block cause recorded",
        requeued_at=format_iso(moment_or_now(now)),
        requeued_by=requeued_by if requeued_by in _REQUEUERS else BY_CM_RELEASE,
    )


def _write_record(project_root: "Path | str", record: RetryRecord) -> None:
    path = record_path(project_root, record.version)
    path.parent.mkdir(parents=True, exist_ok=True)
    atomic_write(path, "\n".join(record.as_lines()) + "\n")


def record_requeue(
    project_root: "Path | str",
    version: str,
    task_id: str,
    *,
    case: str,
    cause: str,
    requeued_by: str,
    now: "datetime.datetime | str | None" = None,
) -> Optional[RetryRecord]:
    """Write *version*'s retry record; None (nothing written) when it already exists."""
    record = _new_record(version, task_id, case=case, cause=cause, requeued_by=requeued_by, now=now)
    if record_path(project_root, record.version).exists():
        return None
    _write_record(project_root, record)
    return record


# ---------------------------------------------------------------------------
# What the records say
# ---------------------------------------------------------------------------


def block_cause(task_dir: "Path | str", case: str = CASE_TESTER_BLOCKED) -> str:
    """The TESTER task's block cause, one line.

    For a BLOCKED task: its status.md ``## Blockers``, else its Block
    Record's evidence. For a DONE task without a report: that the report is
    missing, naming the path.
    """
    task_dir = Path(task_dir)
    if _checked_case(case) == CASE_REPORT_MISSING:
        return f"the task reads DONE but {task_dir / 'artifacts' / 'report.md'} does not exist"
    try:
        text = (task_dir / "status.md").read_text(encoding="utf-8", errors="replace")
    except OSError:
        return f"no block cause recorded ({task_dir / 'status.md'} cannot be read)"
    blockers = one_line(ts.read_section(text, "Blockers") or "")
    if blockers and blockers.lower() != "none":
        return _clip(blockers)
    evidence = one_line(ts.record_field(text, "Block Record", "evidence") or "")
    if evidence and evidence.lower() != "none":
        return _clip(evidence)
    return f"no block cause recorded in {task_dir / 'status.md'}"


def verifies_version(task_dir: "Path | str", version: str) -> bool:
    """True when the task README's ``## Release Version`` names *version*."""
    return bool(version) and task_release_version(Path(task_dir) / "README.md") == version


def verification_facts(task_dir: "Path | str", active_rc: str) -> dict:
    """The decision wake's facts about a blocked TESTER task's verification.

    ``{}`` unless the task's README names a release that is the open RC
    (*active_rc*) or whose one requeue is already spent; otherwise
    ``verification_version`` (that release), ``verification_retry_spent``
    (bool) and ``verification_cause`` (its block cause, one line). Read only.
    """
    task_dir = Path(task_dir)
    version = task_release_version(task_dir / "README.md")
    if not is_release_version(version):
        return {}
    spent = read_record(task_dir.parent.parent, version) is not None
    if not spent and version != (active_rc or "").strip():
        return {}
    return {
        "verification_version": version,
        "verification_retry_spent": spent,
        "verification_cause": block_cause(task_dir),
    }


def triage_note(version: str, case: str, cause: str) -> str:
    """The ``## Triage Note`` line the requeued TESTER session reads."""
    return (
        f"verification of {version} was requeued once: the previous attempt {_CASE_PHRASES[_checked_case(case)]} "
        f"(cause: {_clip(cause)}). This is the RC's only automatic retry; if verification cannot complete "
        "again, the release ships NON-FUNCTIONAL as unverified."
    )


def inbox_question(version: str, task_id: str, record: Optional[RetryRecord], case: str, cause: str) -> str:
    """The operator inbox question for an unverified ship."""
    first = ""
    if record is not None:
        first = (
            f" Its one requeue ({record.requeued_by}, {record.requeued_at}) followed an attempt that "
            f"{_CASE_PHRASES.get(record.case, 'did not complete')} (cause: {record.cause})."
        )
    return (
        f"{version} shipped NON-FUNCTIONAL because verification did not run: after the RC's one requeue, "
        f"TESTER task {task_id} {_CASE_PHRASES.get(case, 'did not complete')} (cause: {_clip(cause)}).{first} "
        "What stops verification from completing?"
    )


def notes_block(case: str = CASE_TESTER_BLOCKED) -> str:
    """The ``## Known Issues`` body an unverified ship carries."""
    _checked_case(case)
    return (
        "This release is shipped non-functional. Do not use in production. Fix expected in the next patch.\n"
        f"{NOTES_LINE}"
    )


# ---------------------------------------------------------------------------
# Writes
# ---------------------------------------------------------------------------

#: The sections the return to BACKLOG writes, with the value a status file
#: that lacks one is given first (the status-writer never adds a section).
_RETURN_SECTIONS = (("Summary", "none"), ("Blockers", "none"), ("Needs Human", "no"))


def _ensure_return_sections(status: Path) -> None:
    """Append any of the return's sections a hand-made status.md lacks."""
    text = status.read_text(encoding="utf-8")
    missing = [(heading, body) for heading, body in _RETURN_SECTIONS if ts.section_span(text, heading) is None]
    for heading, body in missing:
        text = ts.set_or_append_section(text, heading, body)
    if missing:
        atomic_write(status, text, preserve_mode=True)



def requeue_task(
    kanban_root: "Path | str",
    project: str,
    version: str,
    task_id: str,
    *,
    case: str,
    cause: str,
    requeued_by: str = BY_CM_RELEASE,
    now: "datetime.datetime | str | None" = None,
    project_root: "Path | str | None" = None,
) -> Optional[RetryRecord]:
    """Spend *version*'s one requeue on *task_id*: record it, append the
    Triage Note, and return the task to BACKLOG.

    Returns the record written, or None when the requeue was already spent
    (nothing is written then). *project_root* defaults to the kanban root's
    ``projects/<project>``; the release passes the one it resolved.

    Raises:
        VerificationRetryError: a bad version, task ID or case, or a task
            folder without README.md and status.md; nothing was written.
    """
    project_root = Path(project_root) if project_root else OpsContext(kanban_root=kanban_root).project_root(project)
    task_id = _checked_task_id(task_id)
    task_dir = project_root / "tasks" / task_id
    readme, status = task_dir / "README.md", task_dir / "status.md"
    if not readme.is_file() or not status.is_file():
        raise VerificationRetryError(
            "verification-retry.task-missing",
            f"TESTER task {task_id} has no README.md and status.md under {task_dir}, so it cannot be requeued.",
            f"ls -la {shlex.quote(str(task_dir))}",
        )
    record = _new_record(version, task_id, case=case, cause=cause, requeued_by=requeued_by, now=now)
    if record_path(project_root, record.version).exists():
        return None
    # The status write goes first: the status-writer validates before it
    # writes, so a refusal leaves no record and no note behind, and the
    # requeue stays available.
    state = ts.section_value(status.read_text(encoding="utf-8"), "State").strip().upper() or "unknown"
    _ensure_return_sections(status)
    return_to_backlog(
        status, find_queue_file(project_root, task_id), project=project, task_id=task_id,
        summary=(
            f"Returned from {state} to BACKLOG by {record.requeued_by}: verification of {record.version} "
            f"{_CASE_PHRASES[record.case]} (cause: {record.cause}). This is the RC's one requeue; "
            "the cause is in the README's ## Triage Note."
        ),
    )
    _write_record(project_root, record)
    append_triage_note(readme, triage_note(record.version, record.case, record.cause))
    return record


def write_inbox_entry(
    kanban_root: "Path | str",
    project: str,
    version: str,
    task_id: str,
    *,
    case: str,
    cause: str,
    now: Optional[datetime.datetime] = None,
) -> str:
    """Write the operator inbox entry for an unverified ship; its entry ID.

    Written once: an open entry asking the same question is reused.
    """
    ctx = OpsContext(kanban_root=kanban_root)
    version = _record_version(version)
    task_id = _checked_task_id(task_id)
    record = read_record(ctx.project_root(project), version)
    root = shlex.quote(str(ctx.kanban_root))
    entry = operator_inbox.write_entry(
        ctx, project,
        source=task_id,
        question=inbox_question(version, task_id, record, _checked_case(case), cause),
        meanwhile=(
            f"Shipped {version} with release-notes Status NON-FUNCTIONAL and a 'verification did not run' line "
            "under ## Known Issues; the next release verifies again."
        ),
        how_to_answer=(
            f"read ## Blockers and ## Triage Note of {task_id} under {ctx.project_root(project) / 'tasks'} and fix "
            "what keeps verification from completing; the next release verifies again. List this entry with "
            f"`python3 -m pgai_agent_kanban.overwatch.inbox list --kanban-root {root} --project "
            f"{shlex.quote(project)}` and close it with the same module's close command"
        ),
        now=now,
        once=True,
    )
    return entry.entry_id


# ---------------------------------------------------------------------------
# Command line
# ---------------------------------------------------------------------------


#: The help of --case: what each case means.
_CASE_HELP = (
    f"why verification did not run: {CASE_TESTER_BLOCKED} (the TESTER task is BLOCKED) or "
    f"{CASE_REPORT_MISSING} (it reads DONE with no report)"
)


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="verification_retry",
        description="The one requeue a release's verification gets, and the records an unverified ship leaves.",
    )
    sub = parser.add_subparsers(dest="command", required=True)
    status = sub.add_parser("status", help="print whether the RC's one requeue is spent")
    status.add_argument("--project-root", required=True, help="the project's root, e.g. projects/example")
    status.add_argument("--version", required=True, help=RELEASE_VERSION_HELP)
    cause = sub.add_parser("cause", help="print the TESTER task's block cause, one line")
    cause.add_argument("--task-dir", required=True, help="the TESTER task folder")
    cause.add_argument("--case", default=CASE_TESTER_BLOCKED, choices=CASES,
                       help=f"{_CASE_HELP}; default: {CASE_TESTER_BLOCKED}")
    for name, helptext in (("requeue", "spend the RC's one requeue on the TESTER task"),
                           ("inbox", "write the operator inbox entry for an unverified ship")):
        p = sub.add_parser(name, help=helptext)
        p.add_argument("--kanban-root", required=True, help="the kanban root")
        p.add_argument("--project", required=True, help="the project, e.g. pgai-agent-kanban")
        p.add_argument("--version", required=True, help=RELEASE_VERSION_HELP)
        p.add_argument("--task", required=True, help="the TESTER task ID")
        p.add_argument("--case", required=True, choices=CASES, help=_CASE_HELP)
        p.add_argument("--cause", default="", help="the block cause, one line")
        if name == "requeue":
            p.add_argument(
                "--by", default=BY_CM_RELEASE, choices=_REQUEUERS,
                help=f"who spends the requeue, recorded as requeued_by (default: {BY_CM_RELEASE})",
            )
            p.add_argument("--project-root", default="", help="the project's root (default: <kanban root>/projects/<project>)")
    notes = sub.add_parser("notes-block", help="print the ## Known Issues body of an unverified ship")
    notes.add_argument("--case", default=CASE_TESTER_BLOCKED, choices=CASES,
                       help=f"{_CASE_HELP}; default: {CASE_TESTER_BLOCKED}")
    return parser


def main(argv: Optional[list] = None) -> int:
    """Run one subcommand and print its output (``key=value`` lines, or
    the notes block for ``notes-block``).

    Returns 0 when the subcommand ran, 1 when a write was refused (the
    dual-channel block is on stderr and nothing was written); argparse
    exits 2 on a usage error.
    """
    args = _parser().parse_args(argv)
    try:
        if args.command == "status":
            record = read_record(args.project_root, args.version)
            print(f"retry_spent={'yes' if record is not None else 'no'}")
            for line in record.as_lines() if record is not None else []:
                print(line)
        elif args.command == "cause":
            print(f"cause={block_cause(args.task_dir, args.case)}")
        elif args.command == "requeue":
            record = requeue_task(
                args.kanban_root, args.project, args.version, args.task, case=args.case, cause=args.cause,
                requeued_by=args.by, project_root=args.project_root or None,
            )
            print(f"outcome={'requeued' if record is not None else 'spent'}")
            for line in record.as_lines() if record is not None else []:
                print(line)
        elif args.command == "inbox":
            entry_id = write_inbox_entry(
                args.kanban_root, args.project, args.version, args.task, case=args.case, cause=args.cause,
            )
            print(f"entry_id={entry_id}")
        else:
            print(notes_block(args.case))
    except ContractError as exc:
        # CONVERT: a refused write (this module's own refusal, or the inbox
        # module's) is narrated and the release carries on --
        # release_verification_retry.sh reads a non-zero status as "not
        # written" and never stops on it; nothing was written.
        emit_error_contract(exc.error, exc.message, exc.remedy)
        return 1
    except ts.TaskStatusError as exc:
        emit_error_contract(exc.error_key, exc.message, exc.remedy)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
