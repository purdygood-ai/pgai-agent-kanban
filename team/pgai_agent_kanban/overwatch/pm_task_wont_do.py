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
them; an absent one is not added. A heading is matched exactly as
``## <name>`` at the start of a line (one space, the case shown, trailing
blanks allowed), every section carrying that heading is rewritten, and a
body ends at the next line that starts with ``##`` -- a ``###`` line
included -- so an empty body is filled in place and never runs into the
next section. The separator before the next section is kept as found.
Apart from the blank-line cut, every other section keeps its bytes.

The file is read and written as text in the interpreter's default
encoding, in place.

Usage:
    python3 -m pgai_agent_kanban.overwatch.pm_task_wont_do [--] STATUS_FILE BAD_INPUT BUG_NUM BUG_FILE DETECTED_AT

BUG_NUM is the bug's number without the ``BUG-`` prefix (``0042``);
DETECTED_AT is the timestamp the check recorded. Exits 0 with nothing
printed when the file is rewritten. Exits 1 with one stderr line, and the
file unchanged, when it cannot be read (``Cannot read status file:
<error>``); exits 1 with ``Failed to write status file: <error>`` when the
write fails. ``--help`` prints this text and exits 0.

Public API:
    wont_do_status_text(text, bad_input, bug_num, bug_file, detected_at) -> str
    mark_status_wont_do(status_file, bad_input, bug_num, bug_file, detected_at) -> None
    main(argv=None) -> int
"""

from __future__ import annotations

import argparse
import pathlib
import re
import sys
from typing import Optional

# Self-location: started by file path the interpreter puts only this file's
# directory on sys.path; put the package root this file lives in first so a
# by-path run binds this tree's package.
if not __package__:
    _OWN_PACKAGE_ROOT = str(pathlib.Path(__file__).resolve().parents[2])
    if _OWN_PACKAGE_ROOT not in sys.path:
        sys.path.insert(0, _OWN_PACKAGE_ROOT)

from pgai_agent_kanban.task_status import collapse_blank_lines  # noqa: E402

__all__ = [
    "StatusRewriteError",
    "main",
    "mark_status_wont_do",
    "wont_do_status_text",
]


class StatusRewriteError(Exception):
    """The status.md could not be read or written; the message is the one
    stderr line the command prints."""


# Section boundary: the heading line, then body lines none of which starts
# with "##" (the boundary set_block in wake_task_state.sh splices with), so an
# empty body is never read as running into the next section. The separator
# after the body is re-emitted as found.
def _section_pattern(heading: str) -> str:
    return rf'(^## {re.escape(heading)}[^\S\n]*)((?:\n(?!##)[^\n]*)*?)(\n+##|\n*\Z)'


def _replace_block(text: str, heading: str, new_body: str) -> str:
    """Rewrite the body of every ``## <heading>`` section to *new_body*
    (stripped); *text* unchanged when the heading is absent."""
    text_new, n = re.subn(
        _section_pattern(heading),
        lambda m: m.group(1) + "\n" + new_body.strip() + (m.group(3) or "\n"),
        text,
        flags=re.M,
    )
    if n == 0:
        return text
    return text_new


def wont_do_status_text(
    text: str, bad_input: str, bug_num: str, bug_file: str, detected_at: str
) -> str:
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
    if re.search(r'^## Blocked Reason\s*$', new_text, flags=re.M):
        new_text = _replace_block(new_text, "Blocked Reason", blocked_reason)
    elif re.search(r'^## Blockers\s*$', new_text, flags=re.M):
        new_text = re.sub(
            _section_pattern("Blockers"),
            lambda m: m.group(1) + m.group(2) + f"\n\n## Blocked Reason\n{blocked_reason.strip()}" + (m.group(3) or "\n"),
            new_text,
            flags=re.M,
            count=1,
        )
    else:
        new_text = new_text.rstrip() + f"\n\n## Blocked Reason\n{blocked_reason.strip()}\n"

    return collapse_blank_lines(new_text)


def mark_status_wont_do(
    status_file: "pathlib.Path | str",
    bad_input: str,
    bug_num: str,
    bug_file: str,
    detected_at: str,
) -> None:
    """Rewrite *status_file* in place with :func:`wont_do_status_text`.

    Raises:
        StatusRewriteError: the file cannot be read (it is left unchanged)
            or cannot be written.
    """
    status_path = pathlib.Path(status_file)
    try:
        text = status_path.read_text()
    except OSError as e:
        raise StatusRewriteError(f"Cannot read status file: {e}") from e

    new_text = wont_do_status_text(text, bad_input, bug_num, bug_file, detected_at)

    try:
        status_path.write_text(new_text)
    except OSError as e:
        raise StatusRewriteError(f"Failed to write status file: {e}") from e


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


def main(argv: Optional[list] = None) -> int:
    """Command-line entry point; see the module docstring."""
    args = _build_parser().parse_args(argv)
    try:
        mark_status_wont_do(
            args.status_file, args.bad_input, args.bug_num, args.bug_file, args.detected_at
        )
    except StatusRewriteError as exc:
        print(exc, file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
