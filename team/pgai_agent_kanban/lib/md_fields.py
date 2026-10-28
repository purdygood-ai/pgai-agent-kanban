"""
md_fields.py — read a ``## Heading`` field from a task README, an intake file
(bug, priority, requirements) or a bundle file.

Those files carry their fields as level-2 headings with the value beneath:

    ## PM Task
    PM-20260101-001-example-requirement

Every section is read by ``pgai_agent_kanban.task_status.read_section``,
the one section reader, with its ``end_at_subheading`` reading: a ``###``
subheading ends the body as the next ``##`` heading does. The reader's
docstring carries the section rules (heading match, inline
``## Heading: value``, blank lines, missing versus empty, CRLF). This
module adds the value step on top. ``status.md`` is not read here: its
sections belong to ``pgai_agent_kanban.task_status``.

The value
---------
A field's value is the first non-blank line of its section body, stripped:

- A value wrapped in bold or underscore-bold (``**high**``, ``__high__``)
  reads as the text inside.
- Trailing text on the value line is part of the value
  (``medium (workaround exists)`` reads as written); a caller that wants a
  single token splits it.

Writing
-------
Two text-level writers return the whole new file and write nothing; the
caller commits the result through its own atomic writer (the bash
discovery stage pipes ``render-*`` output into ``pgai_atomic_write``):

- :func:`set_status_text` writes the ``## Status`` field of an intake or
  bundle file in the canonical two-line form (``## Status`` then the value
  on the next line), keeping every other byte. The body rewrite is
  ``task_status.replace_section``'s, so the section boundary is the one
  every status writer shares. An absent ``## Status`` is inserted directly
  after the file's ``# Title`` line.
- :func:`set_value_line` replaces only the value line of a field (its first
  non-blank body line), keeping the heading line, the blank lines above the
  value, the value's indentation and every line after it. This is the shape
  a bundle's ``## PM Task`` is written in (``## PM Task``, a blank line,
  then the value). An absent field is inserted before the first ``##``
  heading that is not ``## Status``, or appended at the end.

Command line
------------
``python3 -m pgai_agent_kanban.lib.md_fields <command> ...``:

  read FILE HEADING
      Print the field value (:func:`field_value`). Exit 3, printing
      nothing, when FILE is missing or unreadable or carries no
      ``## HEADING``. Undecodable bytes read as U+FFFD.
  render-status FILE VALUE
      Print FILE with ``## Status`` set to VALUE (:func:`set_status_text`).
  render-value FILE HEADING VALUE
      Print FILE with the ``## HEADING`` value line set to VALUE
      (:func:`set_value_line`).

The render commands exit 1 with one plain stderr line when FILE cannot be
read. ``-h`` or ``--help`` prints this text and exits 0.

Public API:
    field_value(text, heading) -> str
    read_body(path, heading) -> str | None
    read_field(path, heading) -> str
    set_status_text(text, value) -> str
    set_value_line(text, heading, value) -> str
"""

from __future__ import annotations

import os
import pathlib
import re
import sys
from typing import Optional, Union

# Run by file path (``python3 .../md_fields.py``) or loaded from a file
# outside its package, this module has no parent package and the interpreter
# puts only its own directory on sys.path, so the package import below would
# not resolve. Put this file's own package root -- the directory holding the
# pgai_agent_kanban/ this file sits in -- at the front of sys.path so the
# import resolves from the same tree, never another. Imported as a package
# member, sys.path is left untouched.
if not __package__:
    _OWN_PACKAGE_ROOT = str(pathlib.Path(__file__).resolve().parents[2])
    if _OWN_PACKAGE_ROOT not in sys.path:
        sys.path.insert(0, _OWN_PACKAGE_ROOT)

from pgai_agent_kanban import task_status  # noqa: E402

__all__ = [
    "field_value",
    "read_body",
    "read_field",
    "set_status_text",
    "set_value_line",
]

PathLike = Union[str, "os.PathLike[str]"]

# How this module reads a section through ``task_status.read_section``:
# ``end_at_subheading`` — a ``###`` subheading ends the body, as the next
# ``##`` heading does.
_SECTION_READING = {"end_at_subheading": True}

# A value line wholly wrapped in ** or __ — group 2 is the text inside.
_BOLD_VALUE_RE = re.compile(r"^(\*\*|__)(.+?)\1$")

# The heading line's inline part: an optional colon and the text after it.
_INLINE_RE = re.compile(r"^[^\S\n]*:(.*)$", re.S)


def _section_regex(header: str) -> "re.Pattern[str]":
    """Return the compiled regex for one ``## <header>`` section.

    Groups: (1) the heading line, including any inline ``: value`` and
    trailing whitespace; (2) the body, as newline-led lines none of which
    starts with ``##``; (3) the separator before the next heading, or the
    trailing newlines at end of file.
    """
    pattern = re.compile(
        r"(^##\s+" + re.escape(header) + r"(?:[^\S\n]*:[^\n]*)?[^\S\n]*)((?:\n(?!##)[^\n]*)*?)(\n+##|\n*\Z)",
        re.M | re.IGNORECASE,
    )
    return pattern


def _inline_value(heading_line: str, heading: str) -> str:
    """Return the text after ``## <heading>:`` on the heading line, or ``""``."""
    name_end = re.match(r"##\s+" + re.escape(heading), heading_line, re.IGNORECASE)
    if name_end is None:  # unreachable for a line the section regex matched
        return ""
    m = _INLINE_RE.match(heading_line[name_end.end():])
    return m.group(1).strip() if m else ""


def _unbold(value: str) -> str:
    m = _BOLD_VALUE_RE.match(value)
    return m.group(2).strip() if m else value


def field_value(text: str, heading: str) -> str:
    """Return the value of the ``## <heading>`` field in *text*.

    The value is the first non-blank line of the section body, stripped,
    with a whole-line bold wrapper removed.

    Args:
        text:    Markdown file contents.
        heading: Heading name without the ``## `` prefix.

    Returns:
        The value, or ``""`` when the heading is absent or its section is
        empty.
    """
    body = task_status.read_section(text, heading, **_SECTION_READING)
    if not body:
        return ""
    for line in body.splitlines():
        stripped = line.strip()
        if stripped:
            return _unbold(stripped)
    return ""


def _read_text(path: PathLike) -> Optional[str]:
    if not os.path.isfile(path):
        return None
    try:
        with open(path, "r", encoding="utf-8") as handle:
            return handle.read()
    except (OSError, UnicodeDecodeError):
        return None


def read_body(path: PathLike, heading: str) -> Optional[str]:
    """Return the ``## <heading>`` section body of the file at *path*.

    Returns:
        The body ``task_status.read_section`` returns with this module's
        reading: surrounding whitespace removed, ``""`` for an empty
        section, ``None`` when the heading is absent, the file is missing
        or it is unreadable.
    """
    text = _read_text(path)
    if text is None:
        return None
    return task_status.read_section(text, heading, **_SECTION_READING)


def read_field(path: PathLike, heading: str) -> str:
    """Return the ``## <heading>`` field value of the file at *path*.

    Returns:
        As :func:`field_value`; ``""`` also when the file is missing or
        unreadable.
    """
    text = _read_text(path)
    if text is None:
        return ""
    return field_value(text, heading)


# ---------------------------------------------------------------------------
# Writers (text in, text out)
# ---------------------------------------------------------------------------


def set_status_text(text: str, value: str) -> str:
    """Return *text* with its ``## Status`` field set to *value*.

    Present heading (any case or spacing, an inline ``: value`` included):
    the body is replaced through ``task_status.replace_section``, so the
    heading line and the separator before the next section survive
    byte-for-byte and the result reads ``## Status`` / *value*. An empty
    body is filled in place.

    Absent heading: ``\n## Status\n<value>\n`` is inserted directly after
    the first line that starts with ``# `` (the file title), or at the very
    start when there is no title line.

    Args:
        text:  Full file contents.
        value: New status (``open``, ``running``, ``done`` ...). Surrounding
               whitespace is stripped.

    Returns:
        The rewritten contents.
    """
    if task_status.read_section(text, "Status") is not None:
        return task_status.replace_section(text, "Status", value)
    lines = text.splitlines(keepends=True)
    insert_idx = 0
    for i, line in enumerate(lines):
        if line.startswith("# "):
            insert_idx = i + 1
            break
    lines.insert(insert_idx, f"\n## Status\n{value.strip()}\n")
    return "".join(lines)


def set_value_line(text: str, heading: str, value: str) -> str:
    """Return *text* with the value line of ``## <heading>`` set to *value*.

    Present heading: the first non-blank body line is replaced by *value*,
    keeping that line's leading indentation; the heading line, the blank
    lines above the value and every body line after it are unchanged. A
    value line at end of file gains a trailing newline. A body with no
    non-blank line gets *value* as its only line. A heading carrying an
    inline value (``## PM Task: none``) is rewritten to the two-line form,
    the heading alone and *value* below it.

    Absent heading: ``## <heading>\n\n<value>\n\n`` is inserted before the
    first ``##`` heading that is not ``## Status``; with no such heading,
    ``\n## <heading>\n\n<value>\n`` is appended after the text's final
    newline.

    Args:
        text:    Full file contents.
        heading: Heading name without the ``## `` prefix, e.g. ``"PM Task"``.
        value:   New value. Surrounding whitespace is stripped.

    Returns:
        The rewritten contents.
    """
    value = value.strip()
    m = _section_regex(heading).search(text)
    if m is None:
        other = re.search(r"^##\s+(?!Status\b)", text, re.M | re.IGNORECASE)
        if other is not None:
            pos = other.start()
            return text[:pos] + f"## {heading}\n\n{value}\n\n" + text[pos:]
        suffix = "" if text.endswith("\n") else "\n"
        return text + suffix + f"\n## {heading}\n\n{value}\n"

    heading_line, body, separator = m.group(1), m.group(2), m.group(3) or "\n"
    if _inline_value(heading_line, heading):
        name_end = re.match(r"##\s+" + re.escape(heading), heading_line, re.IGNORECASE)
        heading_line = heading_line[: name_end.end()] if name_end else heading_line
        new_body = "\n" + value
    else:
        body_lines = body.split("\n")
        target = next((i for i, line in enumerate(body_lines) if line.strip()), None)
        if target is None:
            new_body = "\n" + value
        else:
            line = body_lines[target]
            body_lines[target] = line[: len(line) - len(line.lstrip())] + value
            new_body = "\n".join(body_lines)
    return text[: m.start()] + heading_line + new_body + separator + text[m.end():]


# ---------------------------------------------------------------------------
# Command line
# ---------------------------------------------------------------------------

#: Exit code of ``read`` when the file or heading is absent.
EXIT_FIELD_ABSENT = 3

_USAGE = (
    "usage: python3 -m pgai_agent_kanban.lib.md_fields "
    "{read FILE HEADING | render-status FILE VALUE | render-value FILE HEADING VALUE}"
)


def _render(path: str, rewrite) -> int:
    """Print *rewrite* applied to the text of *path*; write nothing."""
    try:
        with open(path, "r", encoding="utf-8") as handle:
            text = handle.read()
    except (OSError, UnicodeDecodeError) as exc:
        print(f"cannot read {path}: {exc}", file=sys.stderr)
        return 1
    sys.stdout.buffer.write(rewrite(text).encode("utf-8"))
    return 0


def main(argv: Optional[list] = None) -> int:
    """Run one command-line command; return its exit code."""
    args = list(sys.argv[1:] if argv is None else argv)
    if args and args[0] in ("-h", "--help"):
        print(__doc__.strip())
        return 0
    command, rest = (args[0], args[1:]) if args else ("", [])
    if command == "read" and len(rest) == 2:
        # Undecodable bytes read as U+FFFD rather than as an absent field, so
        # one stray byte cannot make a done intake file read as missing.
        try:
            with open(rest[0], "r", encoding="utf-8", errors="replace") as handle:
                text = handle.read()
        except OSError:
            return EXIT_FIELD_ABSENT
        if task_status.read_section(text, rest[1], **_SECTION_READING) is None:
            return EXIT_FIELD_ABSENT
        print(field_value(text, rest[1]))
        return 0
    if command == "render-status" and len(rest) == 2:
        return _render(rest[0], lambda text: set_status_text(text, rest[1]))
    if command == "render-value" and len(rest) == 3:
        return _render(rest[0], lambda text: set_value_line(text, rest[1], rest[2]))
    print(_USAGE, file=sys.stderr)
    return 1


if __name__ == "__main__":
    sys.exit(main())
