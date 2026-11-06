"""
inbox.py — the per-project operator inbox: questions parked for a human.

When OVERWATCH or an agent meets a question only a human can answer, and the
question does not endanger the pipeline, it writes one entry here and the
chain carries on around it. This module is the only code that reads or
writes ``projects/<name>/operator-inbox.md``; every surface (the CLI, the
REST routes, the dashboard attention pane, the metrics report, the runaway
detector) calls the functions below rather than touching the file.

The file
--------
Human-readable markdown with two sections. ``## Open`` holds the questions
still waiting; ``## Done`` holds closed ones, in the order they were closed.
Each entry is a ``### INBOX-NNNN`` heading followed by one bullet per field::

    ### INBOX-0001
    - **Date:** 2026-01-01T10:00:00+00:00
    - **Source:** rc:v1.2.3
    - **Question:** Cancel the RC or keep it open for the late fix?
    - **Meanwhile:** Left the RC open; the rest of the queue kept running.
    - **How to answer:** Answer "cancel" or "keep" with the inbox answer command.
    - **Answer:** keep
    - **Answered at:** 2026-01-01T11:00:00+00:00
    - **Closed at:** 2026-01-01T11:05:00+00:00

The first five fields are the ones every entry is written with: when it was
asked, the source task or RC, the question, what OVERWATCH did meanwhile, and
how to answer. ``Answer``, ``Answered at`` and ``Closed at`` appear once they
are set. A value that spans lines continues on lines indented by two
spaces; blank lines inside a value are dropped. Because every continuation
line is indented, no value can start a heading of its own.

Entry IDs count up from ``INBOX-0001`` per project: a new entry takes one
more than the highest ID in either section, so an ID is reused only if the
highest-numbered entries are deleted from the file by hand.

Reading is liberal. Section headings are found by the one section reader,
``task_status.read_section`` (any letter case, any spacing). Field labels may
be bold or plain, in any case, with ``-``, ``*`` or ``+`` bullets; a few
longer spellings are accepted (``Source task or RC``, ``What OVERWATCH did
meanwhile``). CRLF line endings read as LF. Text the reader does not
recognise -- notes above the first entry, an extra bullet inside one -- is
kept in place when the file is rewritten. Writing is canonical: one shape,
LF endings, no trailing whitespace. Only the ``## Open`` and ``## Done``
bodies are rewritten; the title and anything outside those two sections are
left byte-for-byte.

Writes and safety
-----------------
- A missing file is created on the first write and reads as an empty inbox;
  it is never an error. The project directory itself must already exist.
- Every write replaces the file through ``lib.atomic_write``: a failure at
  any point leaves the previous file, or no file, never a partial one.
- Writers hold an advisory lock, ``<kanban root>/locks/operator-inbox-<project>.lock``,
  taken through ``ops.locks.acquire_lock`` with its holder/pid/op/task
  provenance. Read, change and write all happen under the lock, so two
  writers never lose each other's entries. A writer waits up to
  :data:`LOCK_WAIT_SECONDS` for the lock; readers take no lock, since the
  atomic replace means they always see a complete file.
- Bad input is refused before anything is written: an empty source,
  question, meanwhile, how-to-answer or answer; a malformed project name or
  entry ID; a project directory that does not exist; an entry that is not
  in the inbox; an answer or close on an entry already closed. Each refusal
  carries the dual-channel fields (a stable ``error`` key, a ``message``
  and a runnable ``remedy``); see :class:`InboxError`.

Questions parked on an intake item
----------------------------------
``ops.write.set_needs_operator`` sets a bug or priority item to
``needs-operator`` and writes its question here with the item's key as the
Source. Answering an entry whose Source names an item at ``needs-operator``
sets that item back to ``open`` (:func:`answer_entry`); any other Source
leaves every intake file untouched.

Public API
----------
    inbox_path(ctx, project) -> Path
    write_entry(ctx, project, *, source, question, meanwhile, how_to_answer,
                now=None, once=False) -> InboxEntry
    read_inbox(ctx, project) -> Inbox            # open and done entries
    list_open(ctx, project) -> tuple[InboxEntry, ...]
    answer_entry(ctx, project, entry_id, answer, *, now=None) -> InboxEntry
    close_entry(ctx, project, entry_id, *, now=None) -> InboxEntry
    inbox_summary(ctx, project) -> InboxSummary  # open count, newest open
    normalize_entry_id(text) -> str | None
    render_entry(entry) -> str                   # one entry, on-disk shape

``ctx`` is an :class:`pgai_agent_kanban.ops.context.OpsContext`; the inbox
lives at ``ctx.project_root(project) / "operator-inbox.md"``.

Command line
------------
The same operations, for an agent or operator in a shell. ``--kanban-root``
defaults to the root the environment names (``OpsContext.from_env``)::

    python3 -m pgai_agent_kanban.overwatch.inbox write --project NAME \\
        --source SOURCE --question TEXT --meanwhile TEXT --how-to-answer TEXT [--once]
    python3 -m pgai_agent_kanban.overwatch.inbox list --project NAME [--all]
    python3 -m pgai_agent_kanban.overwatch.inbox answer --project NAME --id ID --answer TEXT
    python3 -m pgai_agent_kanban.overwatch.inbox close --project NAME --id ID
    python3 -m pgai_agent_kanban.overwatch.inbox summary --project NAME

``write --once`` writes nothing when an open entry already has the same
source and question (compared with runs of whitespace read as one space)
and prints that entry instead, so a caller that meets the same condition on
every firing leaves one open question, not one per firing.

Each subcommand also answers to the name of the function it runs --
``write_entry``, ``read_inbox`` (for ``list``), ``answer_entry``,
``close_entry``, ``inbox_summary`` -- so a bash caller can name the function
it reaches in a ``pp_run_ops pgai_agent_kanban.overwatch.inbox <function>``
call, the form ``team/scripts/inbox.sh`` uses.

Every subcommand takes ``--json`` for machine-readable output. A refusal
prints the dual-channel block on stderr and exits with the ops exit code
(``ops.errors.cli_exit_code``): 2 bad input, 3 unknown entry or project,
4 a filesystem failure.
"""

from __future__ import annotations

import argparse
import dataclasses
import datetime
import json
import os
import pathlib
import re
import shlex
import sys
import time
from typing import NoReturn
from collections.abc import Callable

# Started by file path (python3 .../overwatch/inbox.py) the module has no
# parent package and only its own directory on sys.path: put this tree's
# own package root first so the package imports below resolve from the same
# tree, never another. Imported as a package member, sys.path is untouched.
if not __package__:
    _OWN_PACKAGE_ROOT = str(pathlib.Path(__file__).resolve().parents[2])
    if _OWN_PACKAGE_ROOT not in sys.path:
        sys.path.insert(0, _OWN_PACKAGE_ROOT)

from pgai_agent_kanban import task_status  # noqa: E402
from pgai_agent_kanban.lib import ini  # noqa: E402
from pgai_agent_kanban.lib.atomic_write import atomic_write  # noqa: E402
from pgai_agent_kanban.lib.error_contract import (  # noqa: E402
    emit_error_contract,
    module_file_command,
)
from pgai_agent_kanban.lib.timeutil import format_iso, try_parse_iso  # noqa: E402
from pgai_agent_kanban.ops.context import OpsContext  # noqa: E402
from pgai_agent_kanban.ops.errors import (  # noqa: E402
    ContractError,
    IoError,
    NotFound,
    OpsError,
    Refused,
    cli_exit_code,
)
from pgai_agent_kanban.ops.locks import (  # noqa: E402
    LockContended,
    acquire_lock,
)
from pgai_agent_kanban.ops import write as _ops_write  # noqa: E402

__all__ = [
    "INBOX_FILENAME",
    "LOCK_WAIT_SECONDS",
    "Inbox",
    "InboxEntry",
    "InboxError",
    "InboxIoError",
    "InboxNotFound",
    "InboxRefused",
    "InboxSummary",
    "answer_entry",
    "close_entry",
    "inbox_lock_path",
    "inbox_path",
    "inbox_summary",
    "list_open",
    "list_open_newest_first",
    "normalize_entry_id",
    "read_inbox",
    "render_entry",
    "write_entry",
]

#: The inbox file's name inside a project directory.
INBOX_FILENAME = "operator-inbox.md"

#: How long a writer waits for another writer to release the inbox lock
#: before refusing. Writers hold it for one read-change-write, so a wait this
#: long means a stuck holder, not a busy one.
LOCK_WAIT_SECONDS = 10.0

_LOCK_POLL_SECONDS = 0.05
_LOCK_HOLDER = "operator-inbox"
_ATOMIC_PREFIX = ".operator_inbox_tmp_"
_MODULE_FILE = pathlib.Path(__file__).resolve()

_OPEN_HEADING = "Open"
_DONE_HEADING = "Done"
_OPEN_PLACEHOLDER = "_No open questions._"
_DONE_PLACEHOLDER = "_No closed questions._"

_FILE_INTRO = (
    "# Operator inbox\n"
    "\n"
    "Questions the chain parked for a human answer while it carried on. Each\n"
    "open entry says what was asked, what OVERWATCH did meanwhile and how to\n"
    "answer. Answer and close entries through the inbox command\n"
    "(pgai_agent_kanban.overwatch.inbox) rather than by editing this file.\n"
)

_ENTRY_ID_RE = re.compile(r"^\s*(?:inbox[\s._-]*)?0*(?P<num>\d+)\s*$", re.I)
_ENTRY_HEADING_RE = re.compile(r"^#{3}(?!#)[ \t]*(?P<id>[A-Za-z]+[\s._-]*\d+)\b")
_FIELD_LINE_RE = re.compile(
    r"^[-*+][ \t]+(?:\*\*|__)?(?P<label>[A-Za-z][A-Za-z \t_-]*?)[ \t]*(?:\*\*|__)?"
    r"[ \t]*:[ \t]*(?:\*\*|__)?[ \t]*(?P<value>.*)$"
)
_PLACEHOLDER_RE = re.compile(r"^_?\s*no\s+(?:\w+\s+)?(?:questions|entries)\s*\.?\s*_?$", re.I)
_CONTROL_CHARS_RE = re.compile(r"[\x00-\x08\x0b-\x1f\x7f]")

# Canonical field order and labels. The first five are the fields every
# entry is written with; the last three are set by answer and close.
_FIELDS: tuple[tuple[str, str], ...] = (
    ("date", "Date"),
    ("source", "Source"),
    ("question", "Question"),
    ("meanwhile", "Meanwhile"),
    ("how_to_answer", "How to answer"),
    ("answer", "Answer"),
    ("answered_at", "Answered at"),
    ("closed_at", "Closed at"),
)
_REQUIRED_FIELDS = ("date", "source", "question", "meanwhile", "how_to_answer")

# Label spellings the reader accepts, after lower-casing and reading any run
# of spaces, tabs, '_' or '-' as one space.
_LABEL_ALIASES: dict[str, str] = {
    "date": "date",
    "asked": "date",
    "asked at": "date",
    "source": "source",
    "source task": "source",
    "source task or rc": "source",
    "question": "question",
    "meanwhile": "meanwhile",
    "what overwatch did meanwhile": "meanwhile",
    "how to answer": "how_to_answer",
    "answer": "answer",
    "answered at": "answered_at",
    "closed at": "closed_at",
}


# ---------------------------------------------------------------------------
# Errors
# ---------------------------------------------------------------------------


class InboxError(ContractError):
    """An inbox operation that did not happen, carrying the dual-channel fields.

    ``str(exc)`` is the three-line house-shape block
    (``lib.error_contract.format_error_contract``), so a surface that re-reads
    refusals from text (``parse_error_contract``) sees the same three fields.

    Attributes:
        error:   Stable machine key, ``inbox.<reason>``.
        message: What did not happen and why.
        remedy:  A command that shows or fixes the cause, runnable as printed.
    """


class InboxRefused(InboxError, Refused):
    """Bad input refused before anything was written (ops exit code 2)."""


class InboxNotFound(InboxError, NotFound):
    """The project directory or the entry does not exist (ops exit code 3)."""


class InboxIoError(InboxError, IoError):
    """The inbox file or its lock could not be read or written (ops exit code 4)."""


def _refuse[E: InboxError](kind: type[E], error: str, message: str, remedy: str) -> NoReturn:
    """Raise *kind* -- the one refusal site every input and lookup check shares.

    Nothing has been written when this runs: every check happens before the
    atomic write.
    """
    # AGENT-RESOLVABLE: a refused inbox call leaves the file exactly as it
    # was; the caller corrects its own next call (a non-empty field, a real
    # entry ID, an existing project) in the same session and nothing needs a
    # human.
    # CENSUS-ANCHOR: operator-inbox-refusal-01
    raise kind(error, message, remedy)


# ---------------------------------------------------------------------------
# Data
# ---------------------------------------------------------------------------


@dataclasses.dataclass(frozen=True)
class InboxEntry:
    """One inbox question, as read from or written to the file.

    Attributes:
        entry_id:      ``INBOX-NNNN``.
        status:        ``"open"`` (under ``## Open``) or ``"done"`` (under
                       ``## Done``).
        date:          When the question was written, ISO-8601 UTC.
        source:        The task ID or RC (``rc:vX.Y.Z``) the question is about.
        question:      The question itself.
        meanwhile:     What OVERWATCH (or the writing agent) did meanwhile.
        how_to_answer: How the operator answers it.
        answer:        The recorded answer; ``""`` while unanswered.
        answered_at:   When the answer was recorded; ``""`` while unanswered.
        closed_at:     When the entry was closed; ``""`` while open.
        extra:         Lines inside the entry the reader did not recognise,
                       kept verbatim and written back after the fields.
    """

    entry_id: str
    status: str
    date: str = ""
    source: str = ""
    question: str = ""
    meanwhile: str = ""
    how_to_answer: str = ""
    answer: str = ""
    answered_at: str = ""
    closed_at: str = ""
    extra: tuple[str, ...] = ()

    @property
    def number(self) -> int:
        """The numeric part of :attr:`entry_id`."""
        return int(self.entry_id.rsplit("-", 1)[-1])

    def to_dict(self) -> dict[str, object]:
        """Return the entry as a JSON-ready dict (``extra`` as a list)."""
        data = dataclasses.asdict(self)
        data["extra"] = list(self.extra)
        return data


@dataclasses.dataclass(frozen=True)
class Inbox:
    """Every entry in one project's inbox, in file order within each section."""

    open: tuple[InboxEntry, ...] = ()
    done: tuple[InboxEntry, ...] = ()

    def to_dict(self) -> dict[str, object]:
        """Return ``{"open": [...], "done": [...]}`` for a JSON surface."""
        return {
            "open": [entry.to_dict() for entry in self.open],
            "done": [entry.to_dict() for entry in self.done],
        }


@dataclasses.dataclass(frozen=True)
class InboxSummary:
    """What a status surface shows about an inbox.

    Attributes:
        open_count:  Number of open entries.
        newest_open: The open entry with the latest date (the highest ID
                     breaks a tie, and wins over an unparseable date), or
                     ``None`` when nothing is open.
    """

    open_count: int
    newest_open: InboxEntry | None

    def to_dict(self) -> dict[str, object]:
        """Return the summary as a JSON-ready dict."""
        return {
            "open_count": self.open_count,
            "newest_open": self.newest_open.to_dict() if self.newest_open else None,
        }


@dataclasses.dataclass
class _Document:
    """The parsed ``## Open`` and ``## Done`` sections of one inbox file."""

    text: str
    open_preamble: list[str]
    open_entries: list[InboxEntry]
    done_preamble: list[str]
    done_entries: list[InboxEntry]


# ---------------------------------------------------------------------------
# Paths and validation
# ---------------------------------------------------------------------------


def inbox_path(ctx: OpsContext, project: str) -> pathlib.Path:
    """Return ``<project root>/operator-inbox.md`` (it need not exist)."""
    return ctx.project_root(project) / INBOX_FILENAME


def inbox_lock_path(ctx: OpsContext, project: str) -> pathlib.Path:
    """Return the advisory lock writers hold: ``<kanban root>/locks/operator-inbox-<project>.lock``.

    The ``-<project>.lock`` ending lets ``ops.locks.project_lock_files`` and
    ``release-locks.sh`` list it with the project's other locks.
    """
    return ctx.kanban_root / "locks" / f"{_LOCK_HOLDER}-{project}.lock"


def _module_command(*args: str) -> str:
    """Return a runnable ``python3 <this file> ...`` command, every argument quoted."""
    return module_file_command(_MODULE_FILE, *args)


def _checked_project_dir(ctx: OpsContext, project: str) -> pathlib.Path:
    """Return the project's directory, refusing a malformed name or a missing directory."""
    projects_dir = ctx.kanban_root / "projects"
    if not isinstance(project, str) or not ini.is_project_name(project):
        _refuse(
            InboxRefused,
            "inbox.bad_project",
            f"project name {project!r} is not a project name (lower-case letters, digits "
            "and '-', starting with a letter or digit); nothing was read or written.",
            f"ls {shlex.quote(str(projects_dir))}",
        )
    project_dir = ctx.project_root(project)
    if not project_dir.is_dir():
        _refuse(
            InboxNotFound,
            "inbox.unknown_project",
            f"project {project!r} has no directory at {project_dir}; the inbox lives inside "
            "an existing project directory, so nothing was read or written.",
            f"ls {shlex.quote(str(projects_dir))}",
        )
    return project_dir


def normalize_entry_id(text: object) -> str | None:
    """Return the canonical ``INBOX-NNNN`` form of *text*, or ``None``.

    Liberal: any letter case, ``-``/``_``/``.``/space after ``INBOX``, the
    prefix itself optional, leading zeros ignored -- ``inbox_7``, ``INBOX-0007``
    and ``7`` all read as ``INBOX-0007``. Zero is not an entry number.
    """
    if not isinstance(text, str):
        return None
    match = _ENTRY_ID_RE.match(text)
    if match is None:
        return None
    number = int(match.group("num"))
    if number < 1:
        return None
    return f"INBOX-{number:04d}"


def _clean_value(value: object) -> str:
    """Return *value* in the canonical stored form.

    CRLF and CR read as LF; control characters other than tab and newline are
    dropped; each line loses its trailing whitespace; blank lines are dropped;
    the whole value is stripped. ``None`` reads as ``""``.
    """
    if value is None:
        return ""
    text = str(value).replace("\r\n", "\n").replace("\r", "\n")
    text = _CONTROL_CHARS_RE.sub("", text)
    lines = [line.rstrip() for line in text.split("\n")]
    return "\n".join(line for line in lines if line.strip()).strip()


def _require_text(value: object, field: str, flag: str) -> str:
    """Return *value* cleaned, refusing it when nothing is left."""
    cleaned = _clean_value(value)
    if not cleaned:
        _refuse(
            InboxRefused,
            f"inbox.empty_{field}",
            f"the {field.replace('_', ' ')} is empty; every inbox entry carries a non-empty "
            f"{flag} value, so nothing was written.",
            _module_command("write", "--help"),
        )
    return cleaned


def _checked_entry_id(entry_id: object, project: str) -> str:
    """Return *entry_id* normalized, refusing one that is not an entry ID."""
    normalized = normalize_entry_id(entry_id)
    if normalized is None:
        _refuse(
            InboxRefused,
            "inbox.bad_entry_id",
            f"{entry_id!r} is not an inbox entry ID (expected INBOX-NNNN); nothing was written.",
            _module_command("list", "--project", project),
        )
    return normalized


# ---------------------------------------------------------------------------
# Reading
# ---------------------------------------------------------------------------


def _read_text(path: pathlib.Path) -> str:
    """Return the inbox text with LF endings; ``""`` when the file does not exist."""
    try:
        raw = path.read_text(encoding="utf-8")
    except FileNotFoundError:
        return ""
    except (OSError, UnicodeDecodeError) as exc:
        raise InboxIoError(
            "inbox.read_failed",
            f"cannot read the operator inbox {path}: {exc}",
            f"ls -l {shlex.quote(str(path))}",
        ) from exc
    return raw.replace("\r\n", "\n")


def _label_key(label: str) -> str | None:
    """Return the field a label names, or ``None`` for a label the reader does not know."""
    return _LABEL_ALIASES.get(re.sub(r"[\s_-]+", " ", label.strip().lower()))


def _continuation(line: str) -> str:
    """Return a continuation line without its indent (two spaces or one tab)."""
    if line.startswith("\t"):
        return line[1:]
    return line[2:] if line.startswith("  ") else line.lstrip()


def _parse_section(body: str | None, status: str) -> tuple[list[str], list[InboxEntry]]:
    """Split one section body into its preamble lines and its entries."""
    preamble: list[str] = []
    entries: list[InboxEntry] = []
    if not body:
        return preamble, entries

    current_id: str | None = None
    fields: dict[str, str] = {}
    extra: list[str] = []
    last_field: str | None = None

    def finish() -> None:
        if current_id is not None:
            entries.append(
                InboxEntry(
                    entry_id=current_id,
                    status=status,
                    extra=tuple(extra),
                    **{key: value.strip() for key, value in fields.items()},
                )
            )

    for raw in body.split("\n"):
        line = raw.rstrip()
        heading = _ENTRY_HEADING_RE.match(line)
        heading_id = normalize_entry_id(heading.group("id")) if heading else None
        if heading_id is not None:
            finish()
            current_id, fields, extra, last_field = heading_id, {}, [], None
            continue
        if current_id is None:
            if not _PLACEHOLDER_RE.match(line.strip()):
                preamble.append(line)
            continue
        if not line.strip():
            last_field = None
            continue
        field_match = _FIELD_LINE_RE.match(line)
        key = _label_key(field_match.group("label")) if field_match else None
        if field_match and key is not None:
            fields[key] = field_match.group("value").strip()
            last_field = key
        elif last_field is not None and line[:1] in (" ", "\t"):
            fields[last_field] = f"{fields[last_field]}\n{_continuation(line)}".strip("\n")
        else:
            extra.append(line)
            last_field = None
    finish()

    while preamble and not preamble[0].strip():
        preamble.pop(0)
    while preamble and not preamble[-1].strip():
        preamble.pop()
    return preamble, entries


def _parse(text: str) -> _Document:
    """Parse a whole inbox file (``""`` for a file that does not exist)."""
    open_preamble, open_entries = _parse_section(task_status.read_section(text, _OPEN_HEADING), "open")
    done_preamble, done_entries = _parse_section(task_status.read_section(text, _DONE_HEADING), "done")
    return _Document(text, open_preamble, open_entries, done_preamble, done_entries)


def read_inbox(ctx: OpsContext, project: str) -> Inbox:
    """Return every open and done entry in *project*'s inbox.

    A missing file is an empty inbox; nothing is created.

    Raises:
        InboxRefused:  *project* is not a project name.
        InboxNotFound: the project directory does not exist.
        InboxIoError:  the file exists but cannot be read as UTF-8 text.
    """
    _checked_project_dir(ctx, project)
    doc = _parse(_read_text(inbox_path(ctx, project)))
    return Inbox(open=tuple(doc.open_entries), done=tuple(doc.done_entries))


def list_open(ctx: OpsContext, project: str) -> tuple[InboxEntry, ...]:
    """Return *project*'s open entries in file order (oldest first as written).

    Raises the same errors as :func:`read_inbox`.
    """
    return read_inbox(ctx, project).open


def _recency_key(entry: InboxEntry) -> tuple[float, int]:
    """Sort key for "newest": parsed date (unparseable dates sort first), then ID number."""
    parsed = try_parse_iso(entry.date) if entry.date else None
    return (parsed.timestamp() if parsed else float("-inf"), entry.number)


def list_open_newest_first(ctx: OpsContext, project: str) -> tuple[InboxEntry, ...]:
    """Return *project*'s open entries, newest first.

    "Newest" ranks as :func:`inbox_summary` ranks it: the parsed date, then
    the higher ID; an unparseable date sorts last. Raises the same errors
    as :func:`read_inbox`.
    """
    return tuple(sorted(list_open(ctx, project), key=_recency_key, reverse=True))


def inbox_summary(ctx: OpsContext, project: str) -> InboxSummary:
    """Return the open count and the newest open entry for *project*.

    Raises the same errors as :func:`read_inbox`.
    """
    entries = list_open(ctx, project)
    newest = max(entries, key=_recency_key) if entries else None
    return InboxSummary(open_count=len(entries), newest_open=newest)


# ---------------------------------------------------------------------------
# Writing
# ---------------------------------------------------------------------------


def _render_value(label: str, value: str) -> list[str]:
    """Return the bullet line(s) for one field; continuation lines indented two spaces."""
    first, *rest = value.split("\n")
    return [f"- **{label}:** {first}".rstrip()] + [f"  {line}".rstrip() for line in rest]


def render_entry(entry: InboxEntry) -> str:
    """Return one entry in the canonical on-disk shape (no trailing newline).

    The text the command line prints for an entry, and the text the REST
    answer route returns as its ``stdout``.
    """
    lines = [f"### {entry.entry_id}"]
    for key, label in _FIELDS:
        value = getattr(entry, key)
        if value:
            lines.extend(_render_value(label, value))
    lines.extend(line.rstrip() for line in entry.extra)
    return "\n".join(lines)


def _render_section(preamble: list[str], entries: list[InboxEntry], placeholder: str) -> str:
    """Return one section body: the preamble, then each entry, blank-line separated."""
    blocks: list[str] = []
    if preamble:
        blocks.append("\n".join(line.rstrip() for line in preamble))
    blocks.extend(render_entry(entry) for entry in entries)
    if not entries:
        blocks.append(placeholder)
    return "\n\n".join(blocks)


def _render_document(doc: _Document) -> str:
    """Return the whole file with only the ``## Open`` and ``## Done`` bodies rewritten."""
    open_body = _render_section(doc.open_preamble, doc.open_entries, _OPEN_PLACEHOLDER)
    done_body = _render_section(doc.done_preamble, doc.done_entries, _DONE_PLACEHOLDER)
    if not doc.text.strip():
        return f"{_FILE_INTRO}\n## {_OPEN_HEADING}\n\n{open_body}\n\n## {_DONE_HEADING}\n\n{done_body}\n"
    # Bodies are written verbatim (a blank line after each heading); a
    # section the file lacks is appended at the end.
    text = task_status.set_or_append_section(doc.text, _OPEN_HEADING, f"\n{open_body}")
    text = task_status.set_or_append_section(text, _DONE_HEADING, f"\n{done_body}")
    return text if text.endswith("\n") else text + "\n"


def _write_text(path: pathlib.Path, text: str) -> None:
    """Replace the inbox atomically, keeping an existing file's permission bits."""
    try:
        atomic_write(path, text, prefix=_ATOMIC_PREFIX, preserve_mode=True)
    except OSError as exc:
        raise InboxIoError(
            "inbox.write_failed",
            f"cannot write the operator inbox {path}: {exc}; the previous file (if any) is " "unchanged.",
            f"ls -ld {shlex.quote(str(path.parent))}",
        ) from exc


class _InboxLock:
    """The writers' advisory lock, waited for up to :data:`LOCK_WAIT_SECONDS`."""

    def __init__(self, ctx: OpsContext, project: str, op: str, task: str) -> None:
        self._ctx = ctx
        self._project = project
        self._op = op
        self._task = task
        self._held = None

    def __enter__(self) -> _InboxLock:
        lock_file = inbox_lock_path(self._ctx, self._project)
        deadline = time.monotonic() + LOCK_WAIT_SECONDS
        while True:
            try:
                self._held = acquire_lock(lock_file, holder=_LOCK_HOLDER, pid=os.getpid(), op=self._op, task=self._task)
                return self
            except LockContended as busy:
                if time.monotonic() >= deadline:
                    # CONVERT: another writer kept the inbox lock past the
                    # wait; this question is not parked, the file is
                    # untouched and the caller's chain carries on -- the
                    # next write retries. No task, wake or release stops.
                    # CENSUS-ANCHOR: operator-inbox-lock-busy-01
                    raise InboxRefused(
                        "inbox.lock_busy",
                        f"the operator inbox for project {self._project!r} is locked by "
                        f"{busy.describe()} and stayed locked for {LOCK_WAIT_SECONDS:g}s; "
                        f"nothing was written.",
                        shlex.quote(str(self._ctx.kanban_root / "scripts" / "release-locks.sh")),
                    ) from busy
                time.sleep(_LOCK_POLL_SECONDS)
            except OpsError as exc:
                raise InboxIoError(
                    "inbox.lock_failed",
                    f"cannot take the operator inbox lock {lock_file}: {exc}",
                    f"ls -ld {shlex.quote(str(lock_file.parent))}",
                ) from exc

    def __exit__(self, *exc_info: object) -> None:
        if self._held is not None:
            self._held.release()
            self._held = None


@dataclasses.dataclass(frozen=True)
class _Unchanged:
    """What a ``_mutate`` change returns to leave the file untouched: *result* is returned, nothing is written."""

    result: object


def _mutate[R](ctx: OpsContext, project: str, op: str, task: str, change: Callable[[_Document], R]) -> R:
    """Run *change* on the parsed inbox under the writers' lock, then write the result.

    *change* edits the document in place and returns what the caller
    returns; if it raises, nothing is written. A change that returns
    :class:`_Unchanged` leaves the file as it was and the wrapped result is
    returned.
    """
    _checked_project_dir(ctx, project)
    path = inbox_path(ctx, project)
    with _InboxLock(ctx, project, op, task):
        doc = _parse(_read_text(path))
        result = change(doc)
        if isinstance(result, _Unchanged):
            return result.result  # type: ignore[return-value]
        _write_text(path, _render_document(doc))
    return result


def _timestamp(now: datetime.datetime | None) -> str:
    """ISO-8601 UTC for *now*, or for the current time when ``None``."""
    return format_iso(now if now is not None else datetime.datetime.now(datetime.UTC))


def write_entry(
    ctx: OpsContext,
    project: str,
    *,
    source: str,
    question: str,
    meanwhile: str,
    how_to_answer: str,
    now: datetime.datetime | None = None,
    once: bool = False,
) -> InboxEntry:
    """Append one open question to *project*'s inbox and return it.

    The file is created when it does not exist. The entry takes the next
    ``INBOX-NNNN`` ID and is dated *now* (default: the current UTC time).

    With *once*, an open entry whose source and question match these (runs
    of whitespace read as one space) is returned instead and nothing is
    written -- the check and the write happen under the one writers' lock,
    so two writers asking the same question at once still leave one entry.

    Args:
        ctx:           Kanban root context.
        project:       Project name; its directory must exist.
        source:        The task ID or RC (``rc:vX.Y.Z``) the question is about.
        question:      The question for the operator.
        meanwhile:     What was done meanwhile (what the chain did instead of
                       waiting).
        how_to_answer: How the operator answers it.
        now:           The entry's date; ``None`` for the current time.
        once:          Reuse a matching open entry rather than add a second.

    Returns:
        The entry as written (values in their stored form), or with *once*
        the matching open entry already in the inbox.

    Raises:
        InboxRefused:  an empty field, a malformed project name, or the lock
            stayed busy.
        InboxNotFound: the project directory does not exist.
        InboxIoError:  the file or lock could not be read or written; the
            previous file is unchanged.
    """
    values = {
        "source": _require_text(source, "source", "--source"),
        "question": _require_text(question, "question", "--question"),
        "meanwhile": _require_text(meanwhile, "meanwhile", "--meanwhile"),
        "how_to_answer": _require_text(how_to_answer, "how_to_answer", "--how-to-answer"),
    }
    date = _timestamp(now)
    lock_task = values["source"].split("\n", 1)[0]

    def change(doc: _Document) -> InboxEntry | _Unchanged:
        if once:
            wanted = (_same_text_key(values["source"]), _same_text_key(values["question"]))
            for open_entry in doc.open_entries:
                if (_same_text_key(open_entry.source), _same_text_key(open_entry.question)) == wanted:
                    return _Unchanged(open_entry)
        numbers = [entry.number for entry in doc.open_entries + doc.done_entries]
        entry = InboxEntry(
            entry_id=f"INBOX-{max(numbers, default=0) + 1:04d}",
            status="open",
            date=date,
            **values,
        )
        doc.open_entries.append(entry)
        return entry

    return _mutate(ctx, project, "inbox write", lock_task, change)


def _same_text_key(text: str) -> str:
    """*text* with every run of whitespace read as one space: how ``once`` compares values."""
    return " ".join(text.split())


def _find_open(doc: _Document, entry_id: str, project: str) -> int:
    """Return the index of *entry_id* under ``## Open``, refusing a closed or unknown one."""
    for index, entry in enumerate(doc.open_entries):
        if entry.entry_id == entry_id:
            return index
    if any(entry.entry_id == entry_id for entry in doc.done_entries):
        _refuse(
            InboxRefused,
            "inbox.entry_closed",
            f"inbox entry {entry_id} in project {project!r} is already closed; only open "
            "entries are answered or closed, so nothing was written.",
            _module_command("list", "--project", project, "--all"),
        )
    _refuse(
        InboxNotFound,
        "inbox.unknown_entry",
        f"project {project!r} has no inbox entry {entry_id}; nothing was written.",
        _module_command("list", "--project", project),
    )


def answer_entry(
    ctx: OpsContext,
    project: str,
    entry_id: str,
    answer: str,
    *,
    now: datetime.datetime | None = None,
) -> InboxEntry:
    """Record *answer* on open entry *entry_id* and return the updated entry.

    The entry stays under ``## Open`` until :func:`close_entry` moves it. A
    second answer replaces the first.

    When the entry's Source names a bug or priority item, the answer, the
    entry ID and the time are appended to the item's ``## Operator
    Answers`` section (``ops.write.record_operator_answer``), where the
    planner reads it, and an item at ``needs-operator`` is set back to
    ``open`` (``ops.write.reopen_needs_operator_item``); every answer
    surface -- this module's command line, ``inbox.sh``, ``POST
    /operations/inbox-answer`` -- calls this function and so does both. A
    Source that names anything else (an RC, a task) changes no item.

    Raises:
        InboxRefused:  an empty answer, a malformed entry ID or project name,
            an entry already closed, or the lock stayed busy.
        InboxNotFound: the project directory or the entry does not exist.
        InboxIoError:  the file or lock could not be read or written, or the
            answer was recorded in the inbox but the item it names could not
            be updated (``inbox.reopen_failed``; answering again retries).
    """
    normalized = _checked_entry_id(entry_id, project)
    cleaned = _clean_value(answer)
    if not cleaned:
        _refuse(
            InboxRefused,
            "inbox.empty_answer",
            f"the answer for inbox entry {normalized} is empty; nothing was written.",
            _module_command("answer", "--project", project, "--id", normalized, "--answer", "yes, go ahead"),
        )
    answered_at = _timestamp(now)

    def change(doc: _Document) -> InboxEntry:
        index = _find_open(doc, normalized, project)
        entry = dataclasses.replace(doc.open_entries[index], answer=cleaned, answered_at=answered_at)
        doc.open_entries[index] = entry
        return entry

    answered = _mutate(ctx, project, "inbox answer", "none", change)
    _reopen_named_item(ctx, project, answered)
    return answered


def _reopen_named_item(ctx: OpsContext, project: str, entry: InboxEntry) -> None:
    """Record *entry*'s answer on the item its Source names and reopen it, if any."""
    try:
        _ops_write.record_operator_answer(ctx, project, entry.source, entry.entry_id, entry.answer, entry.answered_at)
        _ops_write.reopen_needs_operator_item(ctx, project, entry.source)
    except OpsError as exc:
        item = entry.source.strip().split("\n", 1)[0]
        # AGENT-RESOLVABLE: the answer is recorded in the inbox and nothing is
        # lost; the item stays as it was until the caller answers again
        # (which retries this) or reopens it with the remedy, in the same session.
        # CENSUS-ANCHOR: operator-inbox-reopen-failed-01
        raise InboxIoError(
            "inbox.reopen_failed",
            f"the answer to {entry.entry_id} was recorded in the inbox, but {item} could not be "
            f"updated with it or set back to open: {exc}",
            shlex.quote(str(ctx.kanban_root / "scripts" / "reset.sh"))
            + f" --project {shlex.quote(project)} --key {shlex.quote(item)}",
        ) from exc


def close_entry(
    ctx: OpsContext,
    project: str,
    entry_id: str,
    *,
    now: datetime.datetime | None = None,
) -> InboxEntry:
    """Move open entry *entry_id* to the end of ``## Done`` and return it.

    The entry keeps its answer, if it has one; an unanswered question may
    be closed too (one that no longer needs answering).

    Raises:
        InboxRefused:  a malformed entry ID or project name, an entry already
            closed, or the lock stayed busy.
        InboxNotFound: the project directory or the entry does not exist.
        InboxIoError:  the file or lock could not be read or written.
    """
    normalized = _checked_entry_id(entry_id, project)
    closed_at = _timestamp(now)

    def change(doc: _Document) -> InboxEntry:
        index = _find_open(doc, normalized, project)
        entry = dataclasses.replace(doc.open_entries.pop(index), status="done", closed_at=closed_at)
        doc.done_entries.append(entry)
        return entry

    return _mutate(ctx, project, "inbox close", "none", change)


# ---------------------------------------------------------------------------
# Command line
# ---------------------------------------------------------------------------


# Each subcommand's second name: the function it runs (see "Command line"
# in the module docstring).
_COMMAND_ALIASES: dict[str, str] = {
    "write_entry": "write",
    "read_inbox": "list",
    "answer_entry": "answer",
    "close_entry": "close",
    "inbox_summary": "summary",
}
_ALIASES_BY_COMMAND: dict[str, list[str]] = {command: [alias] for alias, command in _COMMAND_ALIASES.items()}


def _build_parser() -> argparse.ArgumentParser:
    """Return the argument parser for the five subcommands."""
    parser = argparse.ArgumentParser(
        prog="inbox.py",
        description=(
            "Read and write a project's operator inbox (projects/<name>/operator-inbox.md): "
            "questions parked for a human answer while the chain carries on. A refusal "
            "prints error/message/remedy on stderr and exits 2 (bad input), 3 (unknown "
            "entry or project) or 4 (filesystem failure)."
        ),
    )
    sub = parser.add_subparsers(dest="command", metavar="COMMAND")

    def common(p: argparse.ArgumentParser) -> None:
        p.add_argument("--project", default="", help="project name, e.g. pgai-agent-kanban")
        p.add_argument(
            "--kanban-root",
            default="",
            help="kanban root directory (default: the root PGAI_AGENT_KANBAN_ROOT_PATH names)",
        )
        p.add_argument("--json", action="store_true", help="print JSON instead of markdown text")

    write = sub.add_parser(
        "write",
        aliases=_ALIASES_BY_COMMAND["write"],
        help="add an open question",
        description="Add an open question.",
    )
    common(write)
    write.add_argument("--source", default="", help="the task ID or RC (rc:vX.Y.Z) the question is about")
    write.add_argument("--question", default="", help="the question for the operator")
    write.add_argument("--meanwhile", default="", help="what was done meanwhile, instead of waiting")
    write.add_argument("--how-to-answer", default="", help="how the operator answers the question")
    write.add_argument(
        "--once",
        action="store_true",
        help="write nothing when an open entry has the same source and question; print that entry instead",
    )

    lister = sub.add_parser(
        "list",
        aliases=_ALIASES_BY_COMMAND["list"],
        help="list open entries",
        description="List open entries.",
    )
    common(lister)
    lister.add_argument("--all", action="store_true", help="list closed entries too, under Done")

    answer = sub.add_parser(
        "answer",
        aliases=_ALIASES_BY_COMMAND["answer"],
        help="record an answer; answering an entry about a needs-operator bug or priority returns that item to open",
        description=(
            "Record an answer on an open entry. When the entry's Source names a bug or "
            "priority at needs-operator, answering also returns that item to open."
        ),
    )
    common(answer)
    answer.add_argument("--id", default="", help="entry ID, e.g. INBOX-0001 (case and leading zeros ignored)")
    answer.add_argument("--answer", default="", help="the answer text")

    close = sub.add_parser(
        "close",
        aliases=_ALIASES_BY_COMMAND["close"],
        help="close an entry",
        description="Move an open entry to Done.",
    )
    common(close)
    close.add_argument("--id", default="", help="entry ID, e.g. INBOX-0001 (case and leading zeros ignored)")

    summary = sub.add_parser(
        "summary",
        aliases=_ALIASES_BY_COMMAND["summary"],
        help="open count and newest open question",
        description="Print the open count and newest open question.",
    )
    common(summary)
    return parser


def _context(kanban_root: str) -> OpsContext:
    """Return the OpsContext for ``--kanban-root``, or the environment's root."""
    if kanban_root.strip():
        return OpsContext(kanban_root=pathlib.Path(kanban_root))
    try:
        return OpsContext.from_env()
    except RuntimeError as exc:
        raise InboxRefused(
            "inbox.no_kanban_root",
            f"no kanban root: --kanban-root was not given and {exc}.",
            'export PGAI_AGENT_KANBAN_ROOT_PATH="$HOME/pgai_agent_kanban"',
        ) from exc


def _print_entries(title: str, entries: tuple[InboxEntry, ...]) -> None:
    """Print one section heading and its entries in the on-disk shape."""
    print(f"## {title}\n")
    print("\n\n".join(render_entry(e) for e in entries) if entries else "(none)")


def main(argv: list[str] | None = None) -> int:
    """Run the command line; return the exit code."""
    parser = _build_parser()
    args = parser.parse_args(argv)
    if not args.command:
        parser.print_help()
        return 2
    args.command = _COMMAND_ALIASES.get(args.command, args.command)
    try:
        ctx = _context(args.kanban_root)
        if args.command == "write":
            entry = write_entry(
                ctx,
                args.project,
                source=args.source,
                question=args.question,
                meanwhile=args.meanwhile,
                how_to_answer=args.how_to_answer,
                once=args.once,
            )
            print(json.dumps(entry.to_dict(), indent=2) if args.json else render_entry(entry))
        elif args.command == "list":
            inbox = read_inbox(ctx, args.project)
            if args.json:
                data = inbox.to_dict() if args.all else {"open": inbox.to_dict()["open"]}
                print(json.dumps(data, indent=2))
            else:
                _print_entries(_OPEN_HEADING, inbox.open)
                if args.all:
                    print()
                    _print_entries(_DONE_HEADING, inbox.done)
        elif args.command in ("answer", "close"):
            if args.command == "answer":
                entry = answer_entry(ctx, args.project, args.id, args.answer)
            else:
                entry = close_entry(ctx, args.project, args.id)
            print(json.dumps(entry.to_dict(), indent=2) if args.json else render_entry(entry))
        else:
            summary = inbox_summary(ctx, args.project)
            if args.json:
                print(json.dumps(summary.to_dict(), indent=2))
            else:
                print(f"open: {summary.open_count}")
                newest = summary.newest_open
                if newest is not None:
                    print(f"newest: {newest.entry_id} {newest.date} {newest.question.splitlines()[0]}")
    except InboxError as exc:
        project = args.project if ini.is_project_name(args.project or "") else None
        emit_error_contract(exc.error, exc.message, exc.remedy, project=project)
        return cli_exit_code(exc)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
