"""
task_status.py — Python core for the status-writer tool.

Agents advance a kanban task by calling this tool instead of hand-editing
status.md.  A small model writing "## State" in a reasonable-but-nonstandard
variant form (wrong case, extra whitespace, a stray heading) is exactly the
failure mode this tool exists to remove: the write path is a validated tool
call, not free-hand markdown.

Behavior:
  - Validates ``--project``, ``--key``, and ``--state`` are present, and
    validates ``--state`` against the canonical six-state set (BACKLOG,
    WAITING, WORKING, BLOCKED, DONE, WONT-DO). Bad input is refused through
    the shared dual-channel error-contract emitter (error key + message +
    remedy) and nothing is written.
  - Writes atomically: a temp file in the same directory, then an atomic
    rename over the target.  A crash mid-write — or a concurrent reader
    racing the write — never observes a half-written status.md.
  - Preserves every section not named on the command line, byte-for-byte.
    Only ``## State`` (always) and ``## Needs Human`` / ``## Summary`` /
    ``## Blockers`` (when the corresponding flag is given) are touched.
  - Section-body substitution mirrors ``set_block``/``set_state`` in
    ``team/scripts/lib/wake_task_state.sh`` — the same regex convention
    (``(^## <heading>\\s*\\n)(.*?)(\\n+##|\\Z)``) so the tool and the wake
    script's own section-write primitive agree on one shape: only the named
    section's body changes, and the heading line plus every other section's
    spacing are preserved exactly. Unlike ``set_block``, this tool re-emits
    the regex's own captured trailing separator verbatim instead of
    synthesizing a fresh newline in front of it — synthesizing one on top of
    an already-captured leading newline is what would silently grow the file
    by one blank line on every write, which the golden-path acceptance
    criterion (byte-identical output) exists to catch.
  - Rides the blocked-interval ledger (pgai_agent_kanban.metrics.
    blocked_ledger) beside a state write, best-effort: a transition into
    BLOCKED carrying ``--needs-human yes`` in THIS call appends a
    block-start; a later transition out of BLOCKED appends the matching
    block-end. Attributed to the project's own Active RC (release-state.md);
    with Active RC "none", or when the CLI's ``--project``/``--key`` are
    unavailable to a library caller, nothing is written. CENSUS CLASS
    CONVERT — a ledger failure (unwritable metrics directory, an
    already-open start, an end with no open start) never changes this
    tool's own exit code or the status.md write it rides beside; see
    ``_stamp_blocked_ledger_boundary`` below.
  - Keeps the task's ``## Block Record``
    (``pgai_agent_kanban.overwatch.block_record``) in step with the state,
    in the same atomic write. A BLOCKED write records the block:
    ``--reason-code`` from the closed agent list (``agent_unclassified``
    when omitted, so every agent self-block is classifiable), owner_role
    from the task's ``## Role``, and for ``agent_operator_data_action`` the
    typed ``--action-args`` as the evidence line. A task already BLOCKED
    that carries a record keeps it when no ``--reason-code`` is given. A
    DONE or WONT-DO write removes the record. An unknown code, a malformed
    or unknown action argument, ``--action-args`` without
    ``agent_operator_data_action`` (or that code without them), or either
    flag on a state other than BLOCKED is refused dual-channel and nothing
    is written. A task whose role is not an owner role (CODER, WRITER,
    TESTER, CM, PM) gets its state written with a warning and no record.

This module is the Python half of the house layering (Python core + thin sh
wrapper).  The sibling wrapper is ``team/scripts/task-status.sh``.  The
sibling bash implementation of the state vocabulary is
``team/scripts/lib/validate_state.sh``; both lists must stay in the same six
values and the same order.

Usage (as a module):
    python3 -m pgai_agent_kanban.task_status \\
        --project <name> --key <task-id> --state DONE \\
        [--needs-human yes|no] [--summary TEXT] [--blockers TEXT] \\
        [--reason-code agent_<...>] [--action-args KEY=VALUE ...]

Usage (via the bash wrapper):
    bash team/scripts/task-status.sh \\
        --project <name> --key <task-id> --state DONE \\
        [--needs-human yes|no] [--summary TEXT] [--blockers TEXT] \\
        [--reason-code agent_<...>] [--action-args KEY=VALUE ...]

    An agent self-block naming an operator-data action (values are an
    example):
    bash team/scripts/task-status.sh --project pgai-agent-kanban \\
        --key CODER-20260101-001-example-task --state BLOCKED \\
        --needs-human yes --reason-code agent_operator_data_action \\
        --action-args verb=restamp-fixed-in project=pgai-agent-kanban \\
                      version=v2.28.0 bugs=BUG-0412,BUG-0413   (example values)

Read mode (the canonical ``## State`` reader for shell callers):
    python3 -m pgai_agent_kanban.task_status --read-state <path/to/status.md>

    Prints the task's ``## State`` value, uppercased, on one line — read
    with the same section regex the writer splices with, so a value the
    writer produced always reads back exactly. Prints an empty line when
    the file is unreadable or carries no ``## State`` value. Never writes.

Subcommands (additive to the flag interface above):
    python3 -m pgai_agent_kanban.task_status read \
        (<path/to/status.md> | --project <name> --key <task-id>) [--section HEADING]
    python3 -m pgai_agent_kanban.task_status set-section \
        (<path/to/status.md> | --project <name> --key <task-id>) \
        --section HEADING --body TEXT

    python3 -m pgai_agent_kanban.task_status read STATUS_FILE --section HEADING --raw
    python3 -m pgai_agent_kanban.task_status read STATUS_FILE --section HEADING \\
        --field NAME [--default TEXT]
    python3 -m pgai_agent_kanban.task_status render STATUS_FILE \\
        [--section HEADING --body TEXT]... [--skip-missing] \\
        [--insert-after HEADING]... [--append] [--append-missing HEADING]... \\
        [--collapse-blank-lines]
    python3 -m pgai_agent_kanban.task_status clip --chars N [TEXT]

    ``read`` prints the ``## State`` value (the same value ``--read-state``
    prints) or, with ``--section``, another section's body; an absent
    section prints an empty line. ``set-section`` replaces one section's
    body atomically; ``## State`` is refused there (use ``--state``) and a
    ``Needs Human`` body must be yes/no. Both refuse an absent status.md and
    a missing or conflicting target dual-channel.

    ``read --raw`` and ``render`` are the primitives the wake's own status
    writers and readers (``team/scripts/lib/wake_task_state.sh``,
    ``team/scripts/lib/wake_driver_finalize.sh``) call.
    ``read --raw`` prints a section body exactly as written and exits 3 when
    the heading is absent. ``read --field`` prints one value of a
    ``key: value`` record section (the OVERWATCH retry and zero-progress
    records in ``team/scripts/lib/overwatch_protocol.sh``), or ``--default``.
    ``render --append-missing HEADING`` writes that section's body verbatim
    and appends the section when the file lacks it. ``render`` prints the file with section bodies
    replaced (or appended to with ``--append``, or a section upserted, or
    blank-line runs collapsed) and
    writes nothing; the wake commits the output through
    ``atomic_write.sh``, which keeps the file's mode. Both report a failure
    as one plain stderr line and exit 1, leaving the refusal to their
    caller.

    ``clip`` prints TEXT (or stdin when TEXT is omitted) with every line cut
    to at most N characters -- characters, not bytes, whatever the locale,
    so a multibyte character is never split -- for wake-written fragments
    such as the transient-error signature. A missing or non-numeric
    ``--chars`` is one plain stderr line and exit 1.

    Every write path emits valid UTF-8: ``render``'s output, ``clip``'s
    output and every status.md the writers here commit. Text that arrives
    holding bytes that are not valid UTF-8 (a command-line argument cut
    mid-character by a byte-counting tool) has each invalid sequence
    replaced with U+FFFD rather than written through.

Library API — the one status.md implementation; Python callers import these
rather than carrying their own ``## Section`` regex:
    read_section(text, heading) -> str | None   section body, None if absent
    section_value(text, heading) -> str         first value line of a section
    section_lines(text, heading, max_lines)     non-blank body lines, stripped
    read_state(path) -> str                     ``## State``, uppercased
    replace_section(text, heading, body) -> str text-level section rewrite
    replace_sections(text, {heading: body})     several, in order
    upsert_section(text, heading, body, after)  replace, or insert/append
    append_to_section(text, heading, addition)  body kept, addition after it
    collapse_blank_lines(text) -> str           blank-line runs cut to one
    valid_utf8_text(text) -> str                invalid UTF-8 bytes -> U+FFFD
    clip_chars(text, limit) -> str              each line cut to limit characters
    set_or_append_section(text, heading, body)  verbatim body; append if absent
    remove_section(text, heading) -> str        whole section dropped, if present
    record_field(text, heading, field, default) one ``key: value`` record value
    set_section(path, heading, body)            one section, atomic write
    set_sections(path, {heading: body})         several sections, one write
    write_task_status(path, state=..., ...)     validated State/Needs Human/
                                                Summary/Blockers write, with the
                                                ## Block Record kept in step
    validate_block_inputs(state, code, args)    --reason-code/--action-args check

Exit codes:
    0   status.md written successfully (or, with --read-state / read, the
        value was printed — an empty value is still exit 0).
    1   Bad input (missing --project, missing --key, invalid --state,
        invalid --needs-human, an unknown --reason-code, invalid
        --action-args, missing status.md, missing section, a
        missing or conflicting subcommand target) — a dual-channel error is
        printed to stderr via pgai_agent_kanban.lib.error_contract; nothing
        is written.
"""

from __future__ import annotations

import argparse
import dataclasses
import datetime
import os
import pathlib
import re
import shlex
import sys
from pathlib import Path
from typing import Callable, Mapping, NoReturn, Optional, Sequence

# Run by file path (``python3 .../task_status.py``) or loaded from a file
# outside its package, this module has no parent package and the interpreter
# puts only its own directory on sys.path, so the package imports below would
# not resolve. Put this file's own package root -- the directory holding the
# pgai_agent_kanban/ this file sits in -- at the front of sys.path so the
# imports resolve from the same tree, never another. Imported as a package
# member, sys.path is left untouched.
if not __package__:
    _OWN_PACKAGE_ROOT = str(pathlib.Path(__file__).resolve().parents[1])
    if _OWN_PACKAGE_ROOT not in sys.path:
        sys.path.insert(0, _OWN_PACKAGE_ROOT)

from pgai_agent_kanban.cm.read_state_field import read_state_field  # noqa: E402
from pgai_agent_kanban.lib.atomic_write import atomic_write as _atomic_write  # noqa: E402
from pgai_agent_kanban.lib.error_contract import emit_error_contract  # noqa: E402
from pgai_agent_kanban.metrics.blocked_ledger import (  # noqa: E402
    append_block_end,
    append_block_start,
    ledger_path_for_project,
)
from pgai_agent_kanban.ops.context import OpsContext  # noqa: E402

# ---------------------------------------------------------------------------
# Canonical state vocabulary.
#
# Fixed order (not alphabetical) mirrors the order documented across the
# project's governance stack (team/SOP-core.md "Standard Task States") and
# the sibling bash validator team/scripts/lib/validate_state.sh.  Both lists
# must carry exactly these six values.
# ---------------------------------------------------------------------------
CANONICAL_STATES: tuple[str, ...] = (
    "BACKLOG",
    "WAITING",
    "WORKING",
    "BLOCKED",
    "DONE",
    "WONT-DO",
)

CANONICAL_NEEDS_HUMAN: tuple[str, ...] = ("yes", "no")


class TaskStatusError(Exception):
    """Bad input to the status writer, carrying the dual-channel error fields.

    Library callers (anything importing ``write_task_status`` directly, not
    through the CLI) receive this as a normal Python exception with
    ``.error_key`` / ``.message`` / ``.remedy`` attributes to inspect or
    re-emit. The CLI layer (``main``) is the single place that actually
    prints through the shared error-contract emitter — the exception itself
    carries the three fields rather than printing them, so a caller that
    wants to handle the failure differently (log it, retry, surface it in an
    API response) never has to scrape stderr.
    """

    def __init__(self, error_key: str, message: str, remedy: str) -> None:
        super().__init__(message)
        self.error_key = error_key
        self.message = message
        self.remedy = remedy


# ---------------------------------------------------------------------------
# Path resolution
# ---------------------------------------------------------------------------


def resolve_status_file(
    project: str,
    key: str,
    ctx: Optional[OpsContext] = None,
) -> Path:
    """Return the status.md path for the named task.

    Equivalent to ``<kanban_root>/projects/<project>/tasks/<key>/status.md``,
    resolved through :class:`OpsContext` so the KANBAN_ROOT /
    PGAI_AGENT_KANBAN_ROOT_PATH precedence stays in the one place that owns
    it (``pgai_agent_kanban.ops.context``).

    Args:
        project: Project name as registered in projects.cfg.
        key:     Task ID (the task's folder name under tasks/).
        ctx:     Optional pre-built OpsContext (tests inject a temp root
                 here).  Defaults to ``OpsContext.from_env()``.

    Returns:
        Path to the task's status.md (may or may not exist yet — existence
        is checked by the caller).
    """
    resolved_ctx = ctx if ctx is not None else OpsContext.from_env()
    return resolved_ctx.tasks_dir(project) / key / "status.md"


# ---------------------------------------------------------------------------
# Input validation
# ---------------------------------------------------------------------------


def validate_required_text(value: Optional[str], *, flag: str, error_key: str) -> str:
    """Return *value* stripped, or raise TaskStatusError when blank/None.

    CLI-level presence check for a flag the CLI cannot proceed without but
    that argparse itself must NOT enforce with ``required=True`` — an
    argparse-level failure exits via its own usage-error path (a bare
    ``sys.exit(2)`` and a "the following arguments are required" line), not
    through this tool's dual-channel contract. Routing the presence check
    through here instead guarantees a missing ``--project`` or ``--key``
    refuses the same way an invalid ``--state`` does: error key, message,
    remedy, and nothing written.

    Args:
        value: Raw flag value as given on the command line (``None`` when
            the flag was omitted entirely).
        flag: Flag name without the leading dashes, e.g. ``"project"``.
        error_key: Stable machine key to raise with, e.g.
            ``"task-status.missing-project"``.

    Returns:
        *value* stripped of surrounding whitespace.

    Raises:
        TaskStatusError: when *value* is ``None`` or blank after stripping.
    """
    if value is not None and value.strip():
        return value.strip()
    # AGENT-RESOLVABLE: an agent that omits a required flag fixes its own
    # next invocation in the same session; nothing here needs a human. One
    # physical site serves every required-flag presence check (--project,
    # --key, --state) the same way overwatch_protocol.sh's zero-progress
    # escalation serves both its callers from one site -- see
    # docs/blocking-site-census.md's "one physical site, one anchor" note.
    # CENSUS-ANCHOR: task-status-missing-required-arg-01
    raise TaskStatusError(
        error_key=error_key,
        message=f"--{flag} is required and must be non-empty",
        remedy=f"re-run with --{flag} <value>",
    )


def validate_state(state: str) -> str:
    """Return the canonical uppercase form of *state*, or raise TaskStatusError.

    Comparison is case-insensitive and tolerant of surrounding whitespace
    (an agent typing ``done`` or `` DONE `` is not a reason to fail) but the
    resulting value must match one of the canonical six exactly once
    normalized — anything else is refused.

    Args:
        state: Raw --state value as given on the command line.

    Returns:
        The canonical uppercase state string (e.g. ``"DONE"``,
        ``"WONT-DO"``).

    Raises:
        TaskStatusError: error key ``task-status.invalid-state`` when *state*
            does not normalize to one of the canonical six.
    """
    candidate = state.strip().upper()
    if candidate not in CANONICAL_STATES:
        # AGENT-RESOLVABLE: an agent that typos --state fixes its own next
        # invocation in the same session; nothing here needs a human.
        # CENSUS-ANCHOR: task-status-invalid-state-01
        raise TaskStatusError(
            error_key="task-status.invalid-state",
            message=(
                f"'{state}' is not one of the six canonical task states "
                f"({', '.join(CANONICAL_STATES)})"
            ),
            remedy=(
                "re-run with --state set to one of: "
                + "|".join(CANONICAL_STATES)
            ),
        )
    return candidate


def validate_needs_human(value: str) -> str:
    """Return the canonical lowercase form of *value*, or raise TaskStatusError.

    Args:
        value: Raw --needs-human value as given on the command line.

    Returns:
        ``"yes"`` or ``"no"``.

    Raises:
        TaskStatusError: error key ``task-status.invalid-needs-human`` when
            *value* does not normalize to ``yes`` or ``no``.
    """
    candidate = value.strip().lower()
    if candidate not in CANONICAL_NEEDS_HUMAN:
        raise TaskStatusError(
            error_key="task-status.invalid-needs-human",
            message=(
                f"'{value}' is not a valid --needs-human value "
                f"(expected 'yes' or 'no')"
            ),
            remedy="re-run with --needs-human yes or --needs-human no",
        )
    return candidate


# ---------------------------------------------------------------------------
# Section primitives: the one status.md section reader and writer
# (same section boundary as wake_task_state.sh set_block/set_state)
# ---------------------------------------------------------------------------


def _section_pattern(heading: str) -> str:
    """Return the regex matching one ``## <heading>`` section.

    Groups: (1) the heading line without its newline, including an inline
    ``: value`` and trailing spaces, tabs or a carriage return; (2) the
    body, as newline-led lines none of which starts a level-2 heading
    (``##`` not followed by a third ``#``) — so an empty body matches empty
    and can never run into the following section, while a ``###``
    subheading inside a body stays part of that body; (3) the separator:
    the ``\\n+##`` run up to the next level-2 heading, or the trailing
    newlines at end of file.  Use with ``re.M | re.I``.

    The heading is matched liberally — any run of spaces or tabs after
    ``##``, any letter case, an optional inline ``: value`` — but whole:
    ``## State`` never matches ``## State Notes`` or ``## Status``.
    """
    return (
        rf"(^##[ \t]+{re.escape(heading)}(?:[ \t]*:[^\n]*)?[ \t\r]*)"
        r"((?:\n(?!##(?!#))[^\n]*)*?)(\n+##(?!#)|\n*\Z)"
    )


_SECTION_FLAGS = re.M | re.I


def _split_heading_line(heading_line: str, heading: str) -> tuple[str, str]:
    """Split a matched heading line into ``(heading part, inline value)``.

    ``"## State: DONE"`` splits into ``("## State", "DONE")``; a heading
    line without a colon returns ``(heading_line, "")`` unchanged, trailing
    whitespace included, so a writer can re-emit it byte-for-byte.
    """
    name = re.match(r"##[ \t]+" + re.escape(heading), heading_line, re.I)
    if name is None:  # unreachable for a line _section_pattern matched
        return heading_line, ""
    rest = heading_line[name.end():]
    inline = re.match(r"[ \t]*:(.*)$", rest, re.S)
    if inline is None:
        return heading_line, ""
    return heading_line[: name.end()], inline.group(1).strip()


def _missing_section_error(heading: str) -> TaskStatusError:
    return TaskStatusError(
        error_key="task-status.missing-section",
        message=f"status.md has no '## {heading}' section to update",
        remedy=(
            "confirm the task's status.md was created from the standard "
            "template (team/templates/task/task-status/status.md) before "
            f"writing '## {heading}'"
        ),
    )


def replace_section(
    text: str,
    heading: str,
    body: str,
    *,
    insert_after: Optional[str] = None,
) -> str:
    """Return *text* with the body of ``## <heading>`` replaced by *body*.

    The text-level primitive under :func:`set_section`, :func:`set_sections`
    and :func:`write_task_status`.  Only the bytes between the heading line
    and the next ``##`` heading (or end of file) change: the heading line
    itself and the exact separator (blank-line count) before the next
    section are preserved byte-for-byte, which is why the replacement
    re-emits the regex's own captured separator instead of synthesizing a
    fresh ``"\\n"`` in front of it — synthesizing one would grow the file by
    a blank line on every write.  A section whose body is empty is filled in
    place; the section after it is untouched.  A heading carrying an inline
    value (``## State: DONE``) is rewritten to the canonical two-line form.

    Args:
        text:         Full status.md contents.
        heading:      Section heading name, without the ``## `` prefix
                      (e.g. ``"State"``, ``"Needs Human"``).
        body:         New body text.  Surrounding whitespace is stripped.
        insert_after: When ``## <heading>`` is absent, insert it as a new
                      section directly after the section named here,
                      separated by one blank line.  ``None`` (the default)
                      refuses an absent heading instead.

    Returns:
        The full contents with the named section's body replaced.

    Raises:
        TaskStatusError: error key ``task-status.missing-section`` when the
            heading is absent and *insert_after* is ``None`` or itself
            absent — the writer never invents a section the caller did not
            ask for.
    """
    new_body = body.strip()

    def _replace(m: "re.Match[str]") -> str:
        heading_line, _inline = _split_heading_line(m.group(1), heading)
        # The separator already begins with every newline between the old
        # body and the next heading (or the file's trailing newlines) — reuse
        # it verbatim; at an end of file with no newline, supply one.
        return heading_line + "\n" + new_body + (m.group(3) or "\n")

    new_text, n = re.subn(
        _section_pattern(heading), _replace, text, count=1, flags=_SECTION_FLAGS
    )
    if n:
        return new_text

    if insert_after is not None:
        def _insert(m: "re.Match[str]") -> str:
            return (
                m.group(1) + m.group(2)
                + f"\n\n## {heading}\n" + new_body
                + (m.group(3) or "\n")
            )

        new_text, n = re.subn(
            _section_pattern(insert_after), _insert, text, count=1,
            flags=_SECTION_FLAGS,
        )
        if n:
            return new_text

    raise _missing_section_error(heading)


# Older private name for replace_section, kept for callers that predate the
# public name.
_set_section_body = replace_section


def read_section(text: str, heading: str) -> Optional[str]:
    """Return the body of the ``## <heading>`` section in *text*, or ``None``.

    The one status.md section reader; it shares :func:`replace_section`'s
    regex, so a reader and the writer never disagree about where a body
    starts and ends.  Reading is liberal, accepting the variants the writers
    and hand-edits produce:

    - any number of blank lines between the heading and the value;
    - an inline value on the heading line (``## State: DONE``), returned as
      the body's first line;
    - any letter case and run of spaces/tabs in the heading;
    - CRLF line endings (returned normalised to ``\\n``).

    The body is returned as written otherwise — bold markers, HTML comments
    and trailing text are kept; :func:`read_state` is the reader that turns
    a body into a value.

    Args:
        text:    Full status.md contents.
        heading: Section heading name, without the ``## `` prefix.

    Returns:
        The section body with surrounding whitespace removed (``""`` for an
        empty section), or ``None`` when the heading is absent.
    """
    normalised = text.replace("\r\n", "\n")
    m = re.search(_section_pattern(heading), normalised, flags=_SECTION_FLAGS)
    if m is None:
        return None
    _heading_line, inline = _split_heading_line(m.group(1), heading)
    body = m.group(2).strip()
    if inline:
        return f"{inline}\n{body}".strip()
    return body


_HTML_COMMENT_RE = re.compile(r"<!--.*?-->", re.S)
_BOLD_VALUE_RE = re.compile(r"^(\*\*|__)(.+?)\1$")


def section_value(text: str, heading: str) -> str:
    """Return the value of the ``## <heading>`` section in *text*, or ``""``.

    The value is the first non-blank line of the body once HTML comments
    (the template's ``<!-- One of: ... -->`` guidance) are removed, with a
    whole-line bold wrapper (``**DONE**``, ``__DONE__``) unwrapped and
    surrounding whitespace dropped.  Trailing text on that line is part of
    the value (``DONE (merged)`` reads as written); a caller that wants a
    single token splits it.

    Args:
        text:    Full status.md contents.
        heading: Section heading name, without the ``## `` prefix.

    Returns:
        The value, or ``""`` when the heading is absent or carries no value.
    """
    body = read_section(text, heading)
    if not body:
        return ""
    for line in _HTML_COMMENT_RE.sub("", body).splitlines():
        stripped = line.strip()
        if stripped:
            bold = _BOLD_VALUE_RE.match(stripped)
            return bold.group(2).strip() if bold else stripped
    return ""


def section_lines(text: str, heading: str, max_lines: Optional[int] = None) -> list[str]:
    """Return the non-blank lines of the ``## <heading>`` section in *text*.

    Each line is stripped of surrounding whitespace; blank lines are
    dropped.  Display callers (the dashboard panes) use this to show the
    first few lines of a Summary or Blockers body.

    Args:
        text:      Full status.md contents.
        heading:   Section heading name, without the ``## `` prefix.
        max_lines: Keep at most this many lines; ``None`` keeps all.

    Returns:
        The lines, or ``[]`` when the heading is absent or the body is empty.
    """
    body = read_section(text, heading) or ""
    lines = [line.strip() for line in body.splitlines() if line.strip()]
    return lines if max_lines is None else lines[:max_lines]


def read_state(status_file: "Path | str", *, upper: bool = True) -> str:
    """Return a task's ``## State`` value, uppercased, or ``""``.

    The canonical State reader: :func:`section_value` over the same section
    regex the writer splices with, so a value :func:`write_task_status`
    produced always reads back exactly.  Callers that need to tell terminal
    tasks (``DONE``/``WONT-DO``) from live ones read through here rather
    than parsing status.md themselves.

    The result is uppercased so a hand-edited ``done`` compares equal to
    ``DONE``.  It is not validated against :data:`CANONICAL_STATES` — a
    reader reports what the file says and leaves the judgment to its caller.

    Args:
        status_file: Path to a task's status.md.
        upper: ``False`` returns the value with its letter case as written,
            for a caller that reports the state verbatim (the resolve shim
            prints it for bash) rather than comparing it.

    Returns:
        The state (uppercased unless *upper* is ``False``), or ``""`` when
        the file cannot be read, has no ``## State`` heading, or the
        section carries no value.
    """
    try:
        text = Path(status_file).read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return ""
    value = section_value(text, "State")
    return value.upper() if upper else value


# The State reader's earlier name; the bash --read-state path and existing
# library callers use it.
read_task_state = read_state


# ---------------------------------------------------------------------------
# Text-level rewrites (no file I/O)
#
# The pieces the ``render`` subcommand composes.  Each takes the full
# status.md text and returns the new text, so a caller that owns its own
# write path (the wake's atomic_write.sh, which keeps the target's mode)
# can print the result and commit it itself.
# ---------------------------------------------------------------------------


def replace_sections(
    text: str,
    updates: "Mapping[str, str]",
    *,
    skip_missing: bool = False,
) -> str:
    """Return *text* with each named section's body replaced, in order.

    Every heading goes through :func:`replace_section`, so the heading line
    and the separator before the next section are preserved byte-for-byte.

    Args:
        text:         Full status.md contents.
        updates:      Ordered mapping of heading name -> new body.
        skip_missing: ``True`` leaves *text* unchanged for a heading it does
                      not carry; ``False`` (the default) raises instead.

    Returns:
        The rewritten text.

    Raises:
        TaskStatusError: ``task-status.missing-section`` for an absent
            heading when *skip_missing* is ``False``.
    """
    for heading, body in updates.items():
        if skip_missing and read_section(text, heading) is None:
            continue
        text = replace_section(text, heading, body)
    return text


def append_to_section(text: str, heading: str, addition: str) -> str:
    """Return *text* with *addition* appended to the ``## <heading>`` body.

    The existing body (stripped) is kept and *addition* follows it after
    one blank line; an empty body becomes *addition* alone. The rewrite goes
    through :func:`replace_section`, so the heading line and the separator
    before the next section are preserved byte-for-byte.

    Args:
        text:     Full status.md contents.
        heading:  Section heading name, without the ``## `` prefix.
        addition: Text to append. Surrounding whitespace is stripped.

    Returns:
        The rewritten text.

    Raises:
        TaskStatusError: ``task-status.missing-section`` when the heading
            is absent.
    """
    existing = read_section(text, heading)
    if existing is None:
        raise _missing_section_error(heading)
    addition = addition.strip()
    return replace_section(text, heading, f"{existing}\n\n{addition}" if existing else addition)


def collapse_blank_lines(text: str) -> str:
    """Return *text* with every run of two or more blank lines cut to one."""
    return re.sub(r"\n\n\n+", "\n\n", text)


def valid_utf8_text(text: str) -> str:
    """Return *text* with every sequence that is not valid UTF-8 replaced by U+FFFD.

    A command-line argument reaches Python decoded from the caller's bytes;
    bytes that are not valid UTF-8 -- a multibyte character a byte-counting
    tool cut in half -- survive as surrogate escapes and would be written
    back out as the same invalid bytes.  Recovering the original bytes and
    decoding them leniently keeps every valid character and turns each
    invalid sequence into U+FFFD, so the result always encodes to valid
    UTF-8.  Text that is already valid comes back unchanged.
    """
    try:
        raw = os.fsencode(text)
    except UnicodeEncodeError:
        # A lone surrogate that is not an escaped byte (it did not come
        # from decoding bytes): encode what can be encoded, mark the rest.
        raw = text.encode("utf-8", "replace")
    return raw.decode("utf-8", "replace")


def clip_chars(text: str, limit: int) -> str:
    """Return *text* with every line cut to at most *limit* characters.

    Counts characters, never bytes, so a multibyte character is kept or
    dropped whole and the cut never lands inside one.  Lines are split on
    ``\\n`` only and rejoined with it.
    """
    return "\n".join(line[:limit] for line in text.split("\n"))


def upsert_section(
    text: str,
    heading: str,
    body: str,
    *,
    after: "tuple[str, ...] | list[str]" = (),
) -> str:
    """Replace ``## <heading>``'s body, or add the section when it is absent.

    - Present: the body is replaced through :func:`replace_section`; nothing
      else changes.
    - Absent: the section is inserted directly after the first heading in
      *after* that the text carries, one blank line on each side, and every
      run of blank lines in the result is then cut to one
      (:func:`collapse_blank_lines`).
    - Absent, and no heading in *after* present: the section is appended at
      the end, after the text's trailing newlines are cut to one blank line.

    Args:
        text:    Full status.md contents.
        heading: Section heading name, without the ``## `` prefix.
        body:    New body.  Surrounding whitespace is stripped.
        after:   Anchor headings to insert after, in order of preference.

    Returns:
        The rewritten text.
    """
    if read_section(text, heading) is not None:
        return replace_section(text, heading, body)
    for anchor in after:
        try:
            inserted = replace_section(text, heading, body, insert_after=anchor)
        except TaskStatusError:
            continue
        return collapse_blank_lines(inserted)
    return text.rstrip("\n") + f"\n\n## {heading}\n{body.strip()}\n"


def set_or_append_section(text: str, heading: str, body: str) -> str:
    """Set ``## <heading>``'s body exactly as given, appending the section when absent.

    The writer for sections a file may not carry yet — the durable
    ``## Transient Retry`` / ``## Zero Progress`` / ``## Block Record``
    records, OVERWATCH's
    ``## Labels`` and ``## Blocked Reason``, the harness provider's
    ``## State``.

    - Present: the body is replaced, keeping the heading line and the
      separator before the next section byte-for-byte (the same splice as
      :func:`replace_section`), but *body* is written verbatim, not
      stripped, so a record whose last value ends in a space reads back
      exactly as it was written.
    - Absent: a newline is added when the text does not end with one, then
      one blank line and the section — ``"\\n## <heading>\\n<body>\\n"`` —
      are appended at the end of the file. Existing trailing blank lines
      are kept (unlike :func:`upsert_section`, which cuts them to one).

    Args:
        text:    Full file contents.
        heading: Section heading name, without the ``## `` prefix.
        body:    New body, written as given.

    Returns:
        The rewritten text.
    """

    def _replace(m: "re.Match[str]") -> str:
        heading_line, _inline = _split_heading_line(m.group(1), heading)
        return heading_line + "\n" + body + (m.group(3) or "\n")

    new_text, n = re.subn(
        _section_pattern(heading), _replace, text, count=1, flags=_SECTION_FLAGS
    )
    if n:
        return new_text
    if not text.endswith("\n"):
        text += "\n"
    return text + f"\n## {heading}\n{body}\n"


def remove_section(text: str, heading: str) -> str:
    """Return *text* with the whole ``## <heading>`` section removed.

    The inverse of :func:`set_or_append_section`'s append: the heading line
    and its body go, and so does the separator between them and the next
    section, so the next heading takes the removed heading's place and the
    blank lines before it are kept exactly. A section that ends the file is
    removed together with the blank lines in front of it, and the text
    keeps one trailing newline — appending a section and then removing it
    gives back the original bytes. Every other section is untouched.

    Args:
        text:    Full file contents.
        heading: Section heading name, without the ``## `` prefix.

    Returns:
        The rewritten text, or *text* unchanged when the heading is absent.
    """
    m = re.search(_section_pattern(heading), text, flags=_SECTION_FLAGS)
    if m is None:
        return text
    separator = m.group(3) or ""
    if separator.endswith("##"):
        # A later section follows: it moves up into the removed one's place.
        return text[: m.start()] + "##" + text[m.end():]
    head = text[: m.start()].rstrip("\n")
    return head + "\n" if head else ""


def record_field(text: str, heading: str, field: str, default: str = "") -> str:
    """Return one ``field: value`` line's value from the ``## <heading>`` record.

    A record is a section whose body is ``key: value`` lines (the durable
    ``## Transient Retry`` and ``## Zero Progress`` records). The value is
    the rest of the first line starting ``<field>:``, surrounding whitespace
    removed. Matching is exactly the record readers' long-standing rule:
    the whitespace after the colon may run onto the next line, so a field
    with an empty value reads the line after it.

    Args:
        text:    Full status.md contents.
        heading: Record section heading, without the ``## `` prefix.
        field:   Key to read (``retry_count``, ``count``, ...).
        default: Returned when the section is absent, its body is empty or
                 the literal ``none`` (any case), or the field is not present.

    Returns:
        The field value, or *default*.
    """
    body = read_section(text, heading)
    if not body or body.lower() == "none":
        return default
    m = re.search(rf"^{re.escape(field)}:\s*(.*)$", body, flags=re.M)
    return m.group(1).strip() if m else default


# ---------------------------------------------------------------------------
# Atomic section writes
# ---------------------------------------------------------------------------


def _undecodable_status_error(status_file: Path, exc: UnicodeDecodeError) -> TaskStatusError:
    """Build the refusal for a status.md whose bytes are not valid UTF-8.

    The writer reads before it writes, so refusing here leaves the file's
    bytes exactly as they were. The message names the file and the byte
    offset of the first undecodable byte; the remedy is a command that
    rewrites the file with the undecodable bytes dropped.
    """
    bad = exc.object[exc.start:exc.start + 1] if isinstance(exc.object, bytes) else b""
    byte_label = f"byte 0x{bad[0]:02x}" if bad else "a byte"
    quoted = shlex.quote(str(status_file))
    # AGENT-RESOLVABLE: the bytes are already on disk and nothing is
    # written over them; the agent repairs the file with the remedy below
    # and re-runs the same write in the same session.
    # CENSUS-ANCHOR: task-status-undecodable-read-01
    return TaskStatusError(
        error_key="task-status.read-failed",
        message=(
            f"{status_file} is not valid UTF-8: undecodable {byte_label} at byte "
            f"offset {exc.start}; nothing was written"
        ),
        # iconv -c exits 1 whenever it dropped a byte, so the move is gated
        # on the converted copy being non-empty rather than on that status.
        remedy=(
            f"iconv -c -f utf-8 -t utf-8 {quoted} > {quoted}.utf8; "
            f"test -s {quoted}.utf8 && mv {quoted}.utf8 {quoted}"
        ),
    )


def _rewrite_sections(
    status_file: Path,
    updates: "Mapping[str, str]",
    *,
    insert_after: Optional[str] = None,
    transform: "Optional[Callable[[str, str], str]]" = None,
) -> str:
    """Apply *updates* to *status_file* in one atomic write; return the old text.

    Every heading in *updates* is replaced in order through
    :func:`replace_section`; all of them must resolve before anything is
    written, so a missing section leaves the file untouched.

    *transform*, when given, is called as ``transform(old_text, new_text)``
    after the updates and returns the text actually written — the hook
    :func:`write_task_status` uses to add or drop the ``## Block Record`` in
    the same atomic write. A TaskStatusError it raises leaves the file
    untouched.

    Raises:
        TaskStatusError: ``task-status.missing-status-file``,
            ``task-status.read-failed``, ``task-status.missing-section`` or
            ``task-status.write-failed``.
    """
    if not status_file.is_file():
        raise TaskStatusError(
            error_key="task-status.missing-status-file",
            message=f"no status.md found at {status_file}",
            remedy=(
                "verify --project and --key resolve to an existing task "
                f"folder, or create {status_file} from "
                "team/templates/task/task-status/status.md first"
            ),
        )

    try:
        text = status_file.read_text(encoding="utf-8")
    except UnicodeDecodeError as exc:
        raise _undecodable_status_error(status_file, exc) from exc
    except OSError as exc:
        raise TaskStatusError(
            error_key="task-status.read-failed",
            message=f"cannot read {status_file}: {exc}",
            remedy="check file permissions and retry",
        ) from exc

    new_text = text
    for heading, body in updates.items():
        new_text = replace_section(new_text, heading, body, insert_after=insert_after)
    if transform is not None:
        new_text = transform(text, new_text)

    if not new_text.endswith("\n"):
        new_text += "\n"
    # The file was read as strict UTF-8, so only an update body can carry
    # bytes that are not valid UTF-8; the written status.md never does.
    new_text = valid_utf8_text(new_text)

    # The write is atomic (same-directory temp file + fsync + os.replace):
    # a concurrent reader sees the old or the new status.md, never a
    # partial one.
    try:
        _atomic_write(status_file, new_text, prefix=".task_status_tmp_")
    except OSError as exc:
        raise TaskStatusError(
            error_key="task-status.write-failed",
            message=f"failed writing {status_file}: {exc}",
            remedy="check disk space and directory permissions, then retry",
        ) from exc
    return text


def set_sections(
    status_file: "Path | str",
    updates: "Mapping[str, str]",
    *,
    insert_after: Optional[str] = None,
) -> None:
    """Replace several section bodies of *status_file* in one atomic write.

    Every section not named in *updates* is preserved byte-for-byte.  No
    value is validated: this is the raw section writer.  A ``## State``
    change that should be validated against the six canonical states and
    recorded on the blocked-interval ledger goes through
    :func:`write_task_status` instead.

    Args:
        status_file:  Path to a markdown file carrying ``## <heading>``
                      sections (a status.md, or an intake file's
                      ``## Status``).  Must already exist.
        updates:      Ordered mapping of heading name -> new body.
        insert_after: Heading after which an absent section is created (see
                      :func:`replace_section`); ``None`` refuses it.

    Raises:
        TaskStatusError: missing file, unreadable file, an absent section,
            or a failed write.  Nothing is written on any of them.
    """
    _rewrite_sections(Path(status_file), updates, insert_after=insert_after)


def set_section(
    status_file: "Path | str",
    heading: str,
    body: str,
    *,
    insert_after: Optional[str] = None,
) -> None:
    """Replace the body of one ``## <heading>`` section in one atomic write.

    Single-section form of :func:`set_sections`; every other section is
    preserved byte-for-byte.

    Args:
        status_file:  Path to the file.  Must already exist.
        heading:      Section heading name, without the ``## `` prefix.
        body:         New body text.  Surrounding whitespace is stripped.
        insert_after: Heading after which an absent section is created;
                      ``None`` refuses an absent section.

    Raises:
        TaskStatusError: as :func:`set_sections`.
    """
    set_sections(status_file, {heading: body}, insert_after=insert_after)


# ---------------------------------------------------------------------------
# Blocked-interval ledger adapter (best-effort, never raises)
# ---------------------------------------------------------------------------


def _stamp_blocked_ledger_boundary(
    *,
    project: Optional[str],
    key: Optional[str],
    old_state: Optional[str],
    new_state: str,
    needs_human_requested: Optional[str],
) -> None:
    """Best-effort blocked-interval ledger stamp for a BLOCKED transition.

    Opens an interval when THIS call moves the task into BLOCKED carrying
    ``needs_human_requested == "yes"`` (a BLOCKED transition whose call did
    not pass ``--needs-human yes`` opens nothing — "carrying Needs Human
    yes" means this call's flag, not whatever the file already held).
    Closes the matching interval when the task's PRIOR
    state was BLOCKED and this call's *new_state* is not — regardless of
    whether that prior BLOCKED entry itself carried Needs Human yes, since
    an end with no open start is simply swallowed below as a no-op, exactly
    like the wake-path HALT adapter's own idempotent shape
    (wake_blocked_ledger.sh).

    Attributed to the project's own Active RC (release-state.md's
    ``## Active RC``); with Active RC "none" there is no RC to attribute
    the interval to, so nothing is written — the same rule the wake-path
    adapter applies.

    Never raises. Any failure — no project/key context (a library caller
    that omitted them), an unresolvable kanban root, an unwritable metrics
    directory, a duplicate-open or orphaned-close the ledger module itself
    refuses, or a malformed existing ledger — is swallowed silently.
    CENSUS CLASS CONVERT (docs/blocking-site-census.md): this call rides
    beside a status.md write that has, by the time this runs, already
    succeeded; the ledger stamp must never change that outcome. Mirrors
    ``pgai_agent_kanban.lib.refusal_ledger.record_refusal``'s own
    "best-effort, never raises" recording idiom.

    Args:
        project: Project name (``None`` when the caller has no project
            context — e.g. an existing library caller that predates this
            parameter).
        key: Task ID (the ledger's ``source`` becomes ``task:<key>``).
        old_state: The task's ``## State`` value BEFORE this write, or
            ``None`` when absent.
        new_state: The canonical ``## State`` value THIS call is writing.
        needs_human_requested: The canonical ``--needs-human`` value THIS
            call passed (``"yes"``/``"no"``), or ``None`` when the flag was
            omitted from this call.
    """
    if not project or not key:
        return

    opening = new_state == "BLOCKED" and needs_human_requested == "yes"
    closing = (old_state or "").strip().upper() == "BLOCKED" and new_state != "BLOCKED"
    if not opening and not closing:
        return

    try:
        ctx = OpsContext.from_env()
        active_rc = read_state_field(str(ctx.release_state_path(project)), "Active RC")
        if active_rc == "none":
            return

        ledger_path = ledger_path_for_project(str(ctx.kanban_root), project)
        timestamp = datetime.datetime.now(datetime.timezone.utc).strftime(
            "%Y-%m-%dT%H:%M:%S+00:00"
        )
        source = f"task:{key}"
        if opening:
            append_block_start(ledger_path, active_rc, source, timestamp)
        else:
            append_block_end(ledger_path, active_rc, source, timestamp)
    except Exception:  # noqa: BLE001 - best-effort stamp, never a gate
        pass


# ---------------------------------------------------------------------------
# Agent reason codes and the ## Block Record lifecycle
#
# The closed agent_<...> list, the typed action-argument vocabulary and the
# record itself live in pgai_agent_kanban.overwatch.block_record. That
# module imports this one's section primitives, so it is imported lazily,
# inside the functions below, never at module load.
# ---------------------------------------------------------------------------

#: States that end a task; reaching one drops the task's ``## Block Record``.
TERMINAL_STATES: tuple[str, ...] = ("DONE", "WONT-DO")

#: Recorded when a BLOCKED write names no ``--reason-code``.
DEFAULT_AGENT_REASON_CODE = "agent_unclassified"

_EXAMPLE_PROJECT = "pgai-agent-kanban"
_EXAMPLE_KEY = "CODER-20260101-001-example-task"


@dataclasses.dataclass(frozen=True)
class StatusWriteResult:
    """What :func:`write_task_status` did beyond the four managed fields.

    Attributes:
        block_record:         The ``BlockRecord`` written with a BLOCKED
                              state, or ``None`` when none was written.
        block_record_removed: True when a DONE / WONT-DO write dropped an
                              existing ``## Block Record``.
        block_record_kept:    True when a BLOCKED write without
                              ``reason_code`` left the record of a task
                              that was already BLOCKED untouched.
        warning:              Why a BLOCKED write recorded no Block Record
                              (the task's role is not an owner role), or
                              ``""``. The state write itself went ahead.
    """

    block_record: object = None
    block_record_removed: bool = False
    block_record_kept: bool = False
    warning: str = ""


def _refuse_block_input(error_key: str, message: str, remedy: str) -> NoReturn:
    """Raise a TaskStatusError for a refused --reason-code / --action-args.

    One physical refusal site serves every reason-code and action-argument
    check in :func:`validate_block_inputs`; nothing has been written when
    it runs.
    """
    # AGENT-RESOLVABLE: an agent that passes an unknown --reason-code, a
    # malformed or unknown --action-args pair, or the two flags in a
    # combination that does not fit fixes its own next invocation in the
    # same session; nothing here needs a human, and status.md is unchanged.
    # CENSUS-ANCHOR: task-status-reason-code-refusal-01
    raise TaskStatusError(error_key=error_key, message=message, remedy=remedy)


def validate_block_inputs(
    state: str,
    reason_code: Optional[str],
    action_args: "Optional[Sequence[str]]",
    *,
    project: Optional[str] = None,
    key: Optional[str] = None,
) -> tuple[Optional[str], list[str]]:
    """Validate ``--reason-code`` / ``--action-args`` for a write to *state*.

    Args:
        state:       The canonical state being written.
        reason_code: Raw ``--reason-code`` value, or ``None``.
        action_args: Raw ``--action-args`` values, or ``None``/empty.
        project:     Named in a refusal's remedy (an example name otherwise).
        key:         Named in a refusal's remedy (an example ID otherwise).

    Returns:
        ``(code, evidence_tokens)``: the canonical agent reason code (or
        ``None`` when none was given) and the validated action arguments as
        ``key=value`` evidence tokens (``[]`` when none were given).

    Raises:
        TaskStatusError: ``task-status.reason-code-needs-blocked`` (either
            flag on a state other than BLOCKED),
            ``task-status.unknown-reason-code``,
            ``task-status.action-args-without-code``,
            ``task-status.missing-action-args`` or
            ``task-status.invalid-action-args``. Nothing is written.
    """
    from pgai_agent_kanban.overwatch.block_record import (
        AGENT_REASON_CODES,
        OPERATOR_DATA_ACTION,
        ActionArgsError,
        example_action_args,
        format_action_args,
        normalize_reason_code,
        parse_action_args,
    )

    raw_args = [value for value in (action_args or []) if value is not None]
    if reason_code is None and not raw_args:
        return None, []
    base = (
        f"bash team/scripts/task-status.sh --project {(project or _EXAMPLE_PROJECT).strip()} "
        f"--key {(key or _EXAMPLE_KEY).strip()} --state BLOCKED"
    )
    example_args = example_action_args()
    if state != "BLOCKED":
        _refuse_block_input(
            "task-status.reason-code-needs-blocked",
            f"--reason-code and --action-args classify a BLOCKED write; this call writes {state}. Nothing was written",
            f"{base} --reason-code {DEFAULT_AGENT_REASON_CODE}",
        )
    code = normalize_reason_code(reason_code) if reason_code is not None else None
    if code is not None and code not in AGENT_REASON_CODES:
        _refuse_block_input(
            "task-status.unknown-reason-code",
            f"--reason-code {reason_code!r} is not in the closed agent list "
            f"({', '.join(AGENT_REASON_CODES)}). Nothing was written",
            f"{base} --reason-code {DEFAULT_AGENT_REASON_CODE}",
        )
    if raw_args and code != OPERATOR_DATA_ACTION:
        _refuse_block_input(
            "task-status.action-args-without-code",
            f"--action-args only accompanies --reason-code {OPERATOR_DATA_ACTION}"
            f"{'' if code is None else f', not {code}'}. Nothing was written",
            f"{base} --reason-code {OPERATOR_DATA_ACTION} --action-args {example_args}",
        )
    if code == OPERATOR_DATA_ACTION and not raw_args:
        _refuse_block_input(
            "task-status.missing-action-args",
            f"--reason-code {OPERATOR_DATA_ACTION} needs --action-args naming the verb and "
            "its typed key=value arguments; prose alone cannot be acted on. Nothing was written",
            f"{base} --reason-code {OPERATOR_DATA_ACTION} --action-args {example_args}",
        )
    tokens: list[str] = []
    if raw_args:
        try:
            tokens = format_action_args(parse_action_args(raw_args))
        except ActionArgsError as exc:
            _refuse_block_input(
                "task-status.invalid-action-args",
                f"--action-args do not validate: {exc}. Nothing was written",
                f"{base} --reason-code {OPERATOR_DATA_ACTION} --action-args {example_args}",
            )
    return code, tokens


def _task_owner_role(status_file: Path, text: str) -> str:
    """The task's role as a Block Record owner role, or ``""``.

    The ``## Role`` of the task README beside *status_file* first, then the
    ``## Role`` of status.md itself; the first one that is an owner role
    (CODER, WRITER, TESTER, CM, PM) wins.
    """
    from pgai_agent_kanban.overwatch.block_evidence import resolve_owner_role

    return resolve_owner_role(status_file) or resolve_owner_role(
        status_file, [section_value(text, "Role")]
    )


class _BlockRecordStep:
    """The ``## Block Record`` half of one status write (a transform).

    Called by :func:`_rewrite_sections` with the old and the updated text;
    returns the text to write and remembers what it did for
    :class:`StatusWriteResult`.
    """

    def __init__(self, status_file: Path, state: str, code: Optional[str], evidence: list[str]) -> None:
        self.status_file = status_file
        self.state = state
        self.code = code
        self.evidence = evidence
        self.result = StatusWriteResult()

    def __call__(self, old_text: str, new_text: str) -> str:
        from pgai_agent_kanban.overwatch.block_record import (
            BlockRecordError,
            has_block_record,
            render_block_record,
            strip_block_record,
        )

        if self.state in TERMINAL_STATES:
            stripped = strip_block_record(new_text)
            self.result = StatusWriteResult(block_record_removed=stripped != new_text)
            return stripped
        if self.state != "BLOCKED":
            return new_text
        already_blocked = section_value(old_text, "State").strip().upper() == "BLOCKED"
        if self.code is None and already_blocked and has_block_record(old_text):
            # A re-write of a task that is already BLOCKED and classified
            # (a wake block site's code, or an earlier --reason-code) names
            # no new cause: keep the record rather than downgrade it.
            self.result = StatusWriteResult(block_record_kept=True)
            return new_text
        role = _task_owner_role(self.status_file, old_text)
        if not role:
            self.result = StatusWriteResult(
                warning=(
                    f"no Block Record written: the task's ## Role (README.md beside {self.status_file}, "
                    "then status.md) is not one of CODER, WRITER, TESTER, CM, PM"
                )
            )
            return new_text
        try:
            rendered, record = render_block_record(
                new_text,
                self.code or DEFAULT_AGENT_REASON_CODE,
                role,
                self.evidence,
                status_file=self.status_file,
            )
        except BlockRecordError as exc:  # unreachable: code and role are validated
            self.result = StatusWriteResult(warning=f"no Block Record written: {exc.message}")
            return new_text
        self.result = StatusWriteResult(block_record=record)
        return rendered


# ---------------------------------------------------------------------------
# Public write entry point
# ---------------------------------------------------------------------------


def write_task_status(
    status_file: Path,
    *,
    state: str,
    needs_human: Optional[str] = None,
    summary: Optional[str] = None,
    blockers: Optional[str] = None,
    project: Optional[str] = None,
    key: Optional[str] = None,
    reason_code: Optional[str] = None,
    action_args: "Optional[Sequence[str]]" = None,
) -> StatusWriteResult:
    """Validate inputs and atomically write the requested fields to status_file.

    All validation happens BEFORE any bytes are touched: a bad --state or
    --needs-human value, or a status_file that does not exist, raises
    TaskStatusError with nothing written — satisfying "bad input is refused
    dual-channel; nothing is written".

    Only the sections named by a non-None argument are modified.  ``##
    State`` is always written (state is a required argument).  Every other
    section — ``## Task``, ``## Participant``, ``## Role``, ``## Model``,
    ``## Artifacts``, ``## Blocked By Agent``, ``## Blocked Reason``,
    ``## Transient Retry``, ``## Next Recommended Step``, ``##
    Instruction Conflicts``, and any project-specific section — is
    preserved byte-for-byte, with one exception, the ``## Block Record``:

    - A BLOCKED write records the block in the same atomic write
      (``pgai_agent_kanban.overwatch.block_record``): *reason_code*, or
      ``agent_unclassified`` when none is given, with owner_role taken from
      the task's ``## Role`` (README.md beside status.md, then status.md)
      and the validated *action_args* as the evidence line. A task already
      BLOCKED and carrying a record keeps it when no *reason_code* is
      given. A role outside the owner roles writes the state without a
      record and says so in the result's ``warning``.
    - A DONE or WONT-DO write removes the section.

    Args:
        status_file: Path to the task's status.md.  Must already exist.
        state:       New ## State value.  Validated against the canonical
                     six-state set via :func:`validate_state`.
        needs_human: New ## Needs Human value (``"yes"``/``"no"``), or
                     ``None`` to leave the section untouched.
        summary:     New ## Summary body, or ``None`` to leave untouched.
        blockers:    New ## Blockers body, or ``None`` to leave untouched.
        project:     Project name, for the best-effort blocked-interval
                     ledger stamp (see module docstring and
                     :func:`_stamp_blocked_ledger_boundary`). ``None`` skips
                     the stamp entirely — existing callers that predate this
                     parameter keep behaving exactly as before.
        key:         Task ID, for the same ledger stamp's ``source`` field.
                     ``None`` skips the stamp entirely, same as *project*.
        reason_code: An agent reason code from the closed ``agent_<...>``
                     list, for a BLOCKED write only.
        action_args: Typed ``key=value`` action arguments, only with
                     ``reason_code="agent_operator_data_action"`` (which
                     requires them); see :func:`validate_block_inputs`.

    Returns:
        A :class:`StatusWriteResult` naming the Block Record written,
        removed or kept.

    Raises:
        TaskStatusError: On any bad input — invalid state, invalid
            needs_human, an unknown reason_code or invalid action_args,
            missing status_file, a named section absent from
            the file, or an atomic-write failure.  Nothing is written when
            this is raised before the write step; the write step itself is
            atomic, so a failure there also never leaves a partial file.
    """
    # Validate everything first — no writes happen until every field checks out.
    canonical_state = validate_state(state)
    canonical_needs_human = (
        validate_needs_human(needs_human) if needs_human is not None else None
    )
    code, evidence = validate_block_inputs(
        canonical_state, reason_code, action_args, project=project, key=key
    )

    updates: dict[str, str] = {"State": canonical_state}
    if canonical_needs_human is not None:
        updates["Needs Human"] = canonical_needs_human
    if summary is not None:
        updates["Summary"] = summary
    if blockers is not None:
        updates["Blockers"] = blockers

    block_step = _BlockRecordStep(Path(status_file), canonical_state, code, evidence)
    old_text = _rewrite_sections(Path(status_file), updates, transform=block_step)

    # The task's state as of the moment this call started, never the value
    # this call just wrote — the blocked-ledger stamp below needs it to tell
    # "moving into BLOCKED" apart from "moving out of it".
    old_state = section_value(old_text, "State")

    # Best-effort; status.md is already written and this call has already
    # succeeded by this point — see _stamp_blocked_ledger_boundary's own
    # docstring for why a ledger failure here never surfaces.
    _stamp_blocked_ledger_boundary(
        project=project,
        key=key,
        old_state=old_state,
        new_state=canonical_state,
        needs_human_requested=canonical_needs_human,
    )
    return block_step.result


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def _parse_args(argv: Optional[list[str]] = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="task_status",
        description=(
            "Write a kanban task's status.md fields as a tool call instead "
            "of a hand-edit. Validates --project, --key and --state, writes "
            "atomically, and preserves every section not named on the "
            "command line."
        ),
        epilog=(
            "Block Record: --state BLOCKED also writes the task's ## Block "
            "Record (reason_code from --reason-code, else "
            + DEFAULT_AGENT_REASON_CODE + "); --state DONE or WONT-DO removes "
            "it. List the codes and verbs with: python3 -m "
            "pgai_agent_kanban.overwatch.block_record codes --agent (or "
            "--action-verbs). "
            "Subcommands (additive; run 'task_status read --help' or "
            "'task_status set-section --help' for their flags): "
            "'read STATUS_FILE|--project P --key K [--section HEADING] [--raw]' "
            "prints the ## State value (or another section's body) and writes "
            "nothing; 'set-section STATUS_FILE|--project P --key K --section "
            "HEADING --body TEXT' replaces one section's body atomically; "
            "'render STATUS_FILE --section HEADING --body TEXT ...' prints "
            "the file with sections rewritten and writes nothing."
        ),
    )
    # --project/--key/--state are NOT argparse `required=True`: a missing
    # one must refuse through this tool's own dual-channel contract (see
    # validate_required_text), not through argparse's usage-error path.
    parser.add_argument(
        "--project",
        default=None,
        help="Project name as registered in projects.cfg. Required.",
    )
    parser.add_argument(
        "--key",
        default=None,
        help="Task ID (the task's folder name under tasks/). Required.",
    )
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument(
        "--state",
        default=None,
        help=(
            "New ## State value. Required unless --read-state is given. "
            "One of: "
            + "|".join(CANONICAL_STATES)
            + "."
        ),
    )
    mode.add_argument(
        "--read-state",
        default=None,
        metavar="STATUS_FILE",
        help=(
            "Read-only mode: print the ## State value of STATUS_FILE, "
            "uppercased (an empty line when the file or value is absent), "
            "and exit 0 without writing anything. --project and --key are "
            "not needed; the write flags must not be combined with it."
        ),
    )
    parser.add_argument(
        "--needs-human",
        default=None,
        help="New ## Needs Human value: yes|no. Omit to leave unchanged.",
    )
    parser.add_argument(
        "--summary",
        default=None,
        help="New ## Summary body text. Omit to leave unchanged.",
    )
    parser.add_argument(
        "--blockers",
        default=None,
        help="New ## Blockers body text. Omit to leave unchanged.",
    )
    from pgai_agent_kanban.overwatch.block_record import (
        ACTION_VERBS,
        AGENT_REASON_CODES,
        OPERATOR_DATA_ACTION,
    )

    parser.add_argument(
        "--reason-code",
        default=None,
        metavar="CODE",
        help=(
            "With --state BLOCKED only: why the task blocked, from the closed "
            "agent list (" + "|".join(AGENT_REASON_CODES) + "). Recorded in "
            "the ## Block Record with owner_role taken from the task's ## Role. "
            "A BLOCKED write without it records " + DEFAULT_AGENT_REASON_CODE
            + " (a task already BLOCKED keeps its existing record). An unknown "
            "code is refused and nothing is written. DONE or WONT-DO removes "
            "the ## Block Record."
        ),
    )
    parser.add_argument(
        "--action-args",
        action="extend",
        nargs="+",
        default=None,
        metavar="KEY=VALUE",
        help=(
            "Required with --reason-code " + OPERATOR_DATA_ACTION + " and refused "
            "without it: typed key=value pairs, never prose. verb= names an "
            "allowlisted verb (" + "|".join(ACTION_VERBS) + ") and the other "
            "keys are exactly that verb's arguments, "
            "e.g. verb=restamp-fixed-in project=pgai-agent-kanban version=v2.28.0 bugs=BUG-0412,BUG-0413. "
            "Stored in the record's evidence line. An unknown key or verb, a "
            "missing argument or a malformed value is refused and nothing is "
            "written."
        ),
    )
    return parser.parse_args(argv)


# ---------------------------------------------------------------------------
# Subcommand CLI: read | set-section | render | clip
#
# The flag interface above (--project/--key/--state, --read-state) is the
# status-writer tool and stays exactly as it is; these subcommands are an
# additive surface over the public read_section/read_state/set_section
# functions, for shell callers that need a section other than the four the
# writer manages.
# ---------------------------------------------------------------------------

SUBCOMMANDS: tuple[str, ...] = ("read", "set-section", "render", "clip")

#: ``read --raw`` exit code when the file carries no such heading (an empty
#: section is still exit 0), so a shell caller can tell the two apart.
EXIT_SECTION_ABSENT = 3

_EXAMPLE_TARGET = "--project pgai-agent-kanban --key CODER-20260101-001-example-task"


def _refuse(error_key: str, message: str, remedy: str) -> NoReturn:
    """Raise a TaskStatusError for a refused subcommand invocation.

    One physical refusal site serves every subcommand presence and target
    check (a missing or conflicting target, a missing --section or --body,
    a managed heading, an absent status.md), so the CLI layer emits every
    one of them through the same dual-channel contract.
    """
    # AGENT-RESOLVABLE: a caller that omits or mistypes a subcommand
    # argument fixes its own next invocation in the same session; nothing
    # here needs a human.
    # CENSUS-ANCHOR: task-status-subcommand-refusal-01
    raise TaskStatusError(error_key=error_key, message=message, remedy=remedy)


def _subcommand_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="task_status",
        description=(
            "Read or write one ## section of a task's status.md. Name the "
            "file either as a path or with --project and --key."
        ),
    )
    sub = parser.add_subparsers(dest="command", metavar="{read,set-section,render,clip}")

    def _target_args(p: argparse.ArgumentParser) -> None:
        # Every argument is optional at the argparse level: a missing one
        # refuses through _refuse's dual-channel contract, not argparse's
        # bare usage error.
        p.add_argument(
            "status_file", nargs="?", default=None, metavar="STATUS_FILE",
            help="Path to the status.md. Omit when --project/--key are given.",
        )
        p.add_argument("--project", default=None, help="Project name as registered in projects.cfg.")
        p.add_argument("--key", default=None, help="Task ID (the task's folder name under tasks/).")

    read_p = sub.add_parser(
        "read",
        help="Print a section's value (default: ## State).",
        description=(
            "Print a section of a status.md and write nothing. With no "
            "--section (or --section State) prints the ## State value "
            "uppercased, exactly as --read-state does; with any other "
            "--section prints that section's body. An absent section prints "
            "an empty line and exits 0; an absent status.md is refused."
        ),
    )
    _target_args(read_p)
    read_p.add_argument("--section", default=None, metavar="HEADING",
                        help="Heading name without '## ' (default: State).")
    read_p.add_argument(
        "--raw", action="store_true",
        help=(
            "Print the section body exactly as written, for ## State too (no "
            "first-line, bold or case handling), and exit 3 printing nothing "
            "when the heading is absent. An unreadable file prints one plain "
            "line to stderr and exits 1."
        ),
    )
    read_p.add_argument(
        "--field", default=None, metavar="NAME",
        help=(
            "Treat --section as a 'key: value' record and print the value of "
            "NAME (the durable ## Transient Retry / ## Zero Progress records). "
            "Prints --default when the file or section is absent, the body is "
            "empty or 'none', or NAME is not present; exit 0. An unreadable "
            "file prints one plain line to stderr and exits 1."
        ),
    )
    read_p.add_argument("--default", default="", metavar="TEXT",
                        help="Value --field prints when it finds none (default: empty).")

    set_p = sub.add_parser(
        "set-section",
        help="Replace one section's body atomically.",
        description=(
            "Replace the body of one ## section atomically, preserving every "
            "other section byte-for-byte. ## State is refused here: change it "
            "with --state, which validates it. A --section Needs Human body "
            "must be yes or no. The section must already exist."
        ),
    )
    _target_args(set_p)
    set_p.add_argument("--section", default=None, metavar="HEADING",
                       help="Heading name without '## '. Required.")
    set_p.add_argument("--body", default=None, metavar="TEXT",
                       help="New section body (may be empty). Required.")

    render_p = sub.add_parser(
        "render",
        help="Print the file with sections rewritten; write nothing.",
        description=(
            "Read STATUS_FILE, apply the rewrites below in order, and print "
            "the whole new text to stdout. Nothing is written: the caller "
            "commits the output through its own atomic writer. Each "
            "--section is paired with the --body at the same position. ## "
            "State is accepted, and no value is validated. On failure, one "
            "plain line goes to stderr and the exit code is 1: an unreadable "
            "file, or a section the file lacks (unless --skip-missing or "
            "--insert-after is given)."
        ),
    )
    render_p.add_argument("status_file", metavar="STATUS_FILE", help="Path to the status.md to read.")
    render_p.add_argument("--section", action="append", default=[], metavar="HEADING",
                          help="Heading to rewrite, without '## '. Repeatable.")
    render_p.add_argument("--body", action="append", default=[], metavar="TEXT",
                          help="New body for the --section at the same position. Repeatable.")
    render_p.add_argument("--skip-missing", action="store_true",
                          help="Leave a section the file lacks alone instead of failing.")
    render_p.add_argument(
        "--insert-after", action="append", default=[], metavar="HEADING",
        help=(
            "With exactly one --section: when that section is absent, insert "
            "it after the first of these headings present (blank-line runs "
            "then cut to one), or append it at the end when none is. "
            "Repeatable, in order of preference."
        ),
    )
    render_p.add_argument(
        "--append", action="store_true",
        help=(
            "Append each --body to its section's existing body, after one "
            "blank line, instead of replacing it (an empty body becomes the "
            "--body alone)."
        ),
    )
    render_p.add_argument(
        "--append-missing", action="append", default=[], metavar="HEADING",
        help=(
            "Write the --section named HEADING with its --body exactly as "
            "given (not stripped), appending '## HEADING' and the body at the "
            "end of the file when the section is absent. Repeatable; the "
            "other --section pairs keep the replace rules above. Not combined "
            "with --append or --insert-after."
        ),
    )
    render_p.add_argument("--collapse-blank-lines", action="store_true",
                          help="Finally, cut every run of two or more blank lines to one.")

    clip_p = sub.add_parser(
        "clip",
        help="Print text cut to at most N characters per line, as valid UTF-8.",
        description=(
            "Print TEXT, or stdin when TEXT is omitted, with every line cut to "
            "at most N characters. Characters are counted, not bytes, whatever "
            "the locale, so a multibyte character is never split; bytes that "
            "are not valid UTF-8 are printed as U+FFFD. Writes nothing. A "
            "missing or non-numeric --chars prints one plain line to stderr "
            "and exits 1."
        ),
    )
    clip_p.add_argument("text", nargs="?", default=None, metavar="TEXT",
                        help="Text to clip. Read from stdin when omitted.")
    clip_p.add_argument("--chars", default=None, metavar="N",
                        help="Most characters kept per line (a whole number). Required.")
    return parser


def _resolve_subcommand_target(args: argparse.Namespace, *, require_file: bool = True) -> Path:
    """Return the status.md the subcommand names, refusing a bad target.

    ``require_file=False`` skips the existence refusal, for the plain
    ``--raw`` read path that reports an unreadable file itself.
    """
    by_key = args.project is not None or args.key is not None
    if args.status_file is not None and by_key:
        _refuse(
            "task-status.conflicting-target",
            "give either STATUS_FILE or --project/--key, not both",
            f"bash team/scripts/task-status.sh {args.command} {args.status_file}",
        )
    if args.status_file is not None:
        status_file = Path(args.status_file)
    elif args.project and args.key:
        status_file = resolve_status_file(args.project.strip(), args.key.strip())
    else:
        example_tail = " --section Artifacts --body none" if args.command == "set-section" else ""
        _refuse(
            "task-status.missing-target",
            f"'{args.command}' needs a STATUS_FILE path or both --project and --key",
            f"bash team/scripts/task-status.sh {args.command} {_EXAMPLE_TARGET}{example_tail}",
        )
    if require_file and not status_file.is_file():
        _refuse(
            "task-status.missing-status-file",
            f"no status.md found at {status_file}",
            f"ls -l {status_file.parent}",
        )
    return status_file


def _read_text_plain(path: Path) -> Optional[str]:
    """Return *path*'s text, or print one plain stderr line and return None.

    The wake-facing primitives (``read --raw``, ``render``) report an
    unreadable file this way rather than through the error-contract emitter:
    they are building blocks whose callers own the refusal (the same stance
    as ``team/scripts/lib/atomic_write.sh``), and the emitter would also
    append to the refusal ledger.
    """
    try:
        return path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError) as exc:
        print(f"cannot read {path}: {exc}", file=sys.stderr)
        return None


def _run_read_raw(args: argparse.Namespace) -> int:
    text = _read_text_plain(_resolve_subcommand_target(args, require_file=False))
    if text is None:
        return 1
    body = read_section(text, (args.section or "State").strip())
    if body is None:
        return EXIT_SECTION_ABSENT
    print(body)
    return 0


def _run_render(args: argparse.Namespace) -> int:
    """Print STATUS_FILE with the requested rewrites applied; write nothing."""
    if len(args.section) != len(args.body):
        print(
            f"render: {len(args.section)} --section but {len(args.body)} --body; "
            "give one --body per --section",
            file=sys.stderr,
        )
        return 1
    if args.insert_after and len(args.section) != 1:
        print("render: --insert-after needs exactly one --section", file=sys.stderr)
        return 1
    if args.insert_after and args.append:
        print("render: --append and --insert-after cannot be combined", file=sys.stderr)
        return 1
    if args.append_missing and (args.append or args.insert_after):
        print("render: --append-missing cannot be combined with --append or --insert-after",
              file=sys.stderr)
        return 1
    unpaired = [h for h in args.append_missing if h not in args.section]
    if unpaired:
        print(f"render: --append-missing {unpaired[0]} names no --section", file=sys.stderr)
        return 1
    status_file = Path(args.status_file)
    text = _read_text_plain(status_file)
    if text is None:
        return 1
    try:
        if args.insert_after:
            text = upsert_section(text, args.section[0], args.body[0], after=tuple(args.insert_after))
        elif args.append:
            for heading, addition in zip(args.section, args.body):
                if args.skip_missing and read_section(text, heading) is None:
                    continue
                text = append_to_section(text, heading, addition)
        elif args.append_missing:
            for heading, body in zip(args.section, args.body):
                if heading in args.append_missing:
                    text = set_or_append_section(text, heading, body)
                else:
                    text = replace_sections(text, {heading: body}, skip_missing=args.skip_missing)
        else:
            updates = dict(zip(args.section, args.body))
            text = replace_sections(text, updates, skip_missing=args.skip_missing)
    except TaskStatusError:
        missing = next(h for h in args.section if read_section(text, h) is None)
        print(f"Could not update ## {missing} block", file=sys.stderr)
        return 1
    if args.collapse_blank_lines:
        text = collapse_blank_lines(text)
    _write_stdout_utf8(valid_utf8_text(text))
    return 0


def _write_stdout_utf8(text: str) -> None:
    """Write *text* to stdout encoded as UTF-8, whatever the locale's encoding.

    Under an unset (C) locale stdout's own encoding may not be UTF-8; the
    status.md the caller commits from this output always is.  A stdout with
    no byte buffer (a test's in-memory stream) gets the text as is.
    """
    buffer = getattr(sys.stdout, "buffer", None)
    if buffer is None:
        sys.stdout.write(text)
        return
    sys.stdout.flush()
    buffer.write(text.encode("utf-8"))
    buffer.flush()


def _run_clip(args: argparse.Namespace) -> int:
    """Print TEXT (or stdin) with every line cut to ``--chars`` characters."""
    chars = (args.chars or "").strip()
    if not chars.isdigit():
        print(
            f"clip: --chars needs a whole number of characters to keep (got {args.chars!r})",
            file=sys.stderr,
        )
        return 1
    if args.text is not None:
        text = valid_utf8_text(args.text)
    else:
        text = sys.stdin.buffer.read().decode("utf-8", "replace")
    if not text:
        return 0
    text = text[:-1] if text.endswith("\n") else text
    _write_stdout_utf8(clip_chars(text, int(chars)) + "\n")
    return 0


def _run_read_field(args: argparse.Namespace) -> int:
    """Print one record field's value (``read --section H --field NAME``)."""
    if args.raw:
        print("read: --field and --raw cannot be combined", file=sys.stderr)
        return 1
    if args.section is None or not args.section.strip():
        print("read: --field needs --section naming the record heading", file=sys.stderr)
        return 1
    status_file = _resolve_subcommand_target(args, require_file=False)
    text = ""
    if status_file.is_file():
        text = _read_text_plain(status_file)
        if text is None:
            return 1
    print(record_field(text, args.section.strip(), args.field, args.default))
    return 0


def _run_read(args: argparse.Namespace) -> int:
    if args.field is not None:
        return _run_read_field(args)
    if args.raw:
        return _run_read_raw(args)
    status_file = _resolve_subcommand_target(args)
    heading = (args.section or "State").strip()
    if heading.lower() == "state":
        print(read_state(status_file))
        return 0
    try:
        text = status_file.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError) as exc:
        _refuse(
            "task-status.read-failed",
            f"cannot read {status_file}: {exc}",
            f"ls -l {status_file}",
        )
    print(read_section(text, heading) or "")
    return 0


def _run_set_section(args: argparse.Namespace) -> int:
    if args.section is None or not args.section.strip():
        _refuse(
            "task-status.missing-section-arg",
            "'set-section' needs --section naming the heading to write",
            f"bash team/scripts/task-status.sh set-section {_EXAMPLE_TARGET} "
            "--section Artifacts --body none",
        )
    if args.body is None:
        _refuse(
            "task-status.missing-body",
            "'set-section' needs --body with the new section text (it may be empty)",
            f"bash team/scripts/task-status.sh set-section {_EXAMPLE_TARGET} "
            f"--section '{args.section.strip()}' --body none",
        )
    heading = args.section.strip()
    body = args.body
    if heading.lower() == "state":
        project = (args.project or "pgai-agent-kanban").strip()
        key = (args.key or "CODER-20260101-001-example-task").strip()
        _refuse(
            "task-status.state-via-set-section",
            "## State is written with --state, which validates the value and "
            "records the blocked-interval ledger; set-section does not",
            f"bash team/scripts/task-status.sh --project {project} --key {key} --state DONE",
        )
    if heading.lower() == "needs human":
        body = validate_needs_human(body)
    status_file = _resolve_subcommand_target(args)
    set_section(status_file, heading, body)
    print(f"status.md updated: {status_file}  (## {heading})")
    return 0


def _subcommand_main(argv: list[str]) -> int:
    """Run the ``read`` / ``set-section`` / ``render`` / ``clip`` subcommands; return the exit code."""
    args = _subcommand_parser().parse_args(argv)
    runners = {
        "read": _run_read,
        "set-section": _run_set_section,
        "render": _run_render,
        "clip": _run_clip,
    }
    try:
        return runners[args.command](args)
    except TaskStatusError as exc:
        emit_error_contract(exc.error_key, exc.message, exc.remedy, project=getattr(args, "project", None))
        return 1


def main(argv: Optional[list[str]] = None) -> int:
    """Entry point for CLI invocation.

    Args:
        argv: Argument list (default: sys.argv[1:]).

    Returns:
        0 on success. 1 on bad input (a dual-channel error was already
        printed to stderr).
    """
    arg_list = list(sys.argv[1:] if argv is None else argv)
    if arg_list and arg_list[0] in SUBCOMMANDS:
        return _subcommand_main(arg_list)

    args = _parse_args(arg_list)

    if args.read_state is not None:
        print(read_task_state(Path(args.read_state)))
        return 0

    try:
        project = validate_required_text(
            args.project, flag="project", error_key="task-status.missing-project"
        )
        key = validate_required_text(
            args.key, flag="key", error_key="task-status.missing-key"
        )
        state = validate_required_text(
            args.state, flag="state", error_key="task-status.missing-state"
        )
    except TaskStatusError as exc:
        emit_error_contract(exc.error_key, exc.message, exc.remedy, project=args.project)
        return 1

    status_file = resolve_status_file(project, key)

    try:
        result = write_task_status(
            status_file,
            state=state,
            needs_human=args.needs_human,
            summary=args.summary,
            blockers=args.blockers,
            project=project,
            key=key,
            reason_code=args.reason_code,
            action_args=args.action_args,
        )
    except TaskStatusError as exc:
        emit_error_contract(exc.error_key, exc.message, exc.remedy, project=project)
        return 1

    canonical_state = state.strip().upper()
    print(f"status.md updated: {status_file}  (## State -> {canonical_state})")
    _print_block_record_result(result, status_file)
    return 0


def _print_block_record_result(result: StatusWriteResult, status_file: Path) -> None:
    """Narrate the ## Block Record half of a write: one line, or nothing."""
    record = result.block_record
    if record is not None:
        print(
            f"## Block Record written: reason_code={record.reason_code} "  # type: ignore[attr-defined]
            f"owner_role={record.owner_role}"  # type: ignore[attr-defined]
        )
    elif result.block_record_removed:
        print("## Block Record removed (terminal state)")
    elif result.block_record_kept:
        print("## Block Record kept (task was already BLOCKED; pass --reason-code to reclassify)")
    if result.warning:
        print(
            f"warning: {result.warning}; check the role with: "
            f"bash team/scripts/task-status.sh read {status_file} --section Role",
            file=sys.stderr,
        )


if __name__ == "__main__":
    sys.exit(main())
