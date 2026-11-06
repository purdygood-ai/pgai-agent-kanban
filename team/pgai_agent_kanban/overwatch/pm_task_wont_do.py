"""
pm_task_wont_do.py -- close a PM task whose requirements input is a README
or a template, rewriting its status.md to WONT-DO.

OVERWATCH's README-bundled check
(team/scripts/lib/overwatch-checks/check-readme-bundled.sh) finds a PM task
in BACKLOG or WORKING whose ``## Inputs`` names a ``README.md`` or a
``templates/`` path, files a bug for it, and then calls this module to
close the task. The rewrite of the task's status.md:

  * ``## State`` becomes ``WONT-DO``;
  * ``## Summary`` becomes ``PM task marked WONT-DO by OVERWATCH
    check-readme-bundled at <detected at>: input path points at a README.md
    or templates/ file, not a valid requirements bundle. Bug BUG-<n> filed.
    Offending input: <bad input>`` (one line);
  * ``## Needs Human`` becomes ``no``;
  * ``## Blocked Reason`` becomes ``Input path points at a README.md or
    templates/ file: <bad input>. This is not a valid requirements bundle.
    Bug BUG-<n> filed at <bug file>.`` (one line). An existing section is
    rewritten in place; otherwise the section is added directly after
    ``## Blockers``, or, with no ``## Blockers`` either, at the end of the
    file;
  * finally every run of two or more blank lines in the file is cut to one
    (``task_status.collapse_blank_lines``).

State, Summary and Needs Human are rewritten only where the file carries
them; an absent one is not added. Sections are found with
``task_status``'s exact-heading reading (``task_status.section_regex`` and
``task_status.read_section`` with ``exact_heading=True``): a heading line
reads exactly ``## <name>`` (one space, the case shown, surrounding blanks
allowed), every section carrying that heading is rewritten, and a body
ends at the next line that starts with ``##`` -- a ``###`` line included
-- so an empty body is filled in place and never runs into the next
section. The separator before the next section is kept as found. Apart
from the blank-line cut, every other section keeps its bytes.

The file is read as UTF-8 and written back through
``task_status.rewrite_status_file``: one atomic write, so a reader racing
the rewrite sees the old or the new status.md, never a partial one.

Usage:
    python3 -m pgai_agent_kanban.overwatch.pm_task_wont_do [--] STATUS_FILE BAD_INPUT BUG_NUM BUG_FILE DETECTED_AT

BUG_NUM is the bug's number without the ``BUG-`` prefix (``0042``);
DETECTED_AT is the timestamp the check recorded. Exits 0 with nothing
printed when the file is rewritten. Exits 1 with one stderr line, and the
file unchanged, when it is missing or cannot be read (``Cannot read
status file: <error>``); exits 1 with ``Failed to write status file:
<error>``, the file unchanged, when the write fails. ``--help`` prints this text and exits 0.

Public API:
    wont_do_status_text(text, bad_input, bug_num, bug_file, detected_at) -> str
    mark_status_wont_do(status_file, bad_input, bug_num, bug_file, detected_at) -> None
    main(argv=None) -> int
"""

from __future__ import annotations

import argparse
import pathlib
import sys

# Self-location: started by file path the interpreter puts only this file's
# directory on sys.path; put the package root this file lives in first so a
# by-path run binds this tree's package.
if not __package__:
    _OWN_PACKAGE_ROOT = str(pathlib.Path(__file__).resolve().parents[2])
    if _OWN_PACKAGE_ROOT not in sys.path:
        sys.path.insert(0, _OWN_PACKAGE_ROOT)

from pgai_agent_kanban.task_status import (  # noqa: E402
    TaskStatusError,
    collapse_blank_lines,
    read_section,
    rewrite_status_file,
    section_regex,
)

__all__ = [
    "StatusRewriteError",
    "main",
    "mark_status_wont_do",
    "wont_do_status_text",
]


class StatusRewriteError(Exception):
    """The status.md could not be read or written; the message is the one
    stderr line the command prints."""


def _has_section(text: str, heading: str) -> bool:
    """Whether *text* carries a ``## <heading>`` section, read as the
    splices below find one."""
    return read_section(text, heading, exact_heading=True) is not None


def _replace_block(text: str, heading: str, new_body: str) -> str:
    """Rewrite the body of every ``## <heading>`` section to *new_body*
    (stripped); *text* unchanged when the heading is absent.

    The section boundary is ``task_status.section_regex``'s exact-heading
    reading: groups (1) heading line, (2) body, (3) the separator, which is
    re-emitted as found."""
    return section_regex(heading, exact_heading=True).sub(
        lambda m: m.group(1) + "\n" + new_body.strip() + (m.group(3) or "\n"),
        text,
    )


def _insert_after_block(text: str, heading: str, new_heading: str, new_body: str) -> str:
    """Add a ``## <new_heading>`` section directly after the first
    ``## <heading>`` section, one blank line between them; *text* unchanged
    when the heading is absent."""
    return section_regex(heading, exact_heading=True).sub(
        lambda m: (m.group(1) + m.group(2) + f"\n\n## {new_heading}\n{new_body.strip()}" + (m.group(3) or "\n")),
        text,
        count=1,
    )


def wont_do_status_text(text: str, bad_input: str, bug_num: str, bug_file: str, detected_at: str) -> str:
    """Return status.md *text* rewritten to WONT-DO as the module docstring
    describes, with the Summary and Blocked Reason naming *bad_input*, bug
    ``BUG-<bug_num>`` at *bug_file*, and *detected_at*."""
    new_summary = (
        f"PM task marked WONT-DO by OVERWATCH check-readme-bundled at {detected_at}: "
        f"input path points at a README.md or templates/ file, not a valid requirements bundle. "
        f"Bug BUG-{bug_num} filed. Offending input: {bad_input}"
    )

    blocked_reason = (
        f"Input path points at a README.md or templates/ file: {bad_input}. "
        f"This is not a valid requirements bundle. "
        f"Bug BUG-{bug_num} filed at {bug_file}."
    )

    new_text = _replace_block(text, "State", "WONT-DO")
    new_text = _replace_block(new_text, "Summary", new_summary)
    new_text = _replace_block(new_text, "Needs Human", "no")

    # An existing Blocked Reason is rewritten; otherwise it goes directly
    # after Blockers, or at the end of the file.
    if _has_section(new_text, "Blocked Reason"):
        new_text = _replace_block(new_text, "Blocked Reason", blocked_reason)
    elif _has_section(new_text, "Blockers"):
        new_text = _insert_after_block(new_text, "Blockers", "Blocked Reason", blocked_reason)
    else:
        new_text = new_text.rstrip() + f"\n\n## Blocked Reason\n{blocked_reason.strip()}\n"

    return collapse_blank_lines(new_text)


def mark_status_wont_do(
    status_file: pathlib.Path | str,
    bad_input: str,
    bug_num: str,
    bug_file: str,
    detected_at: str,
) -> None:
    """Rewrite *status_file* with :func:`wont_do_status_text`, through
    ``task_status.rewrite_status_file`` (one atomic write).

    Raises:
        StatusRewriteError: the file is missing or cannot be read, or
            cannot be written; it is left unchanged either way.
    """
    try:
        rewrite_status_file(
            pathlib.Path(status_file),
            lambda text: wont_do_status_text(text, bad_input, bug_num, bug_file, detected_at),
        )
    except TaskStatusError as e:
        if e.error_key == "task-status.write-failed":
            raise StatusRewriteError(f"Failed to write status file: {e.message}") from e
        raise StatusRewriteError(f"Cannot read status file: {e.message}") from e


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python3 -m pgai_agent_kanban.overwatch.pm_task_wont_do",
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("status_file", help="The PM task's status.md.")
    parser.add_argument("bad_input", help="The offending ## Inputs path.")
    parser.add_argument("bug_num", help="The filed bug's number, without the BUG- prefix.")
    parser.add_argument("bug_file", help="The filed bug report's path.")
    parser.add_argument("detected_at", help="The timestamp the check recorded.")
    return parser


def main(argv: list | None = None) -> int:
    """Command-line entry point; see the module docstring."""
    args = _build_parser().parse_args(argv)
    try:
        mark_status_wont_do(args.status_file, args.bad_input, args.bug_num, args.bug_file, args.detected_at)
    except StatusRewriteError as exc:
        print(exc, file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
