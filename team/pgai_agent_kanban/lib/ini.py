"""
ini.py — the one INI reader for kanban.cfg, projects.cfg, project.cfg and
workflow.cfg.

Every Python module that reads one of those files calls this module rather
than building its own ``configparser`` parser, so all of them agree on what a
file says.

Reading is raw
--------------
Values are returned exactly as written (surrounding whitespace aside):
``%`` is an ordinary character, never an interpolation marker. That is what
the bash readers (``team/scripts/lib/ini_parser.sh``, ``projects.sh``) see
for the same files, so a Python reader and a bash reader never disagree.
Keys are case-insensitive; section names are case-sensitive. Inline
comments are not stripped (``configparser``'s default).

Three ways to read
------------------
``load(path)``
    Parse a file and return the parser. A missing or unreadable file reads
    as an empty parser (no sections). A malformed file — a duplicate
    section or key, a line before the first section header — raises
    :data:`IniError`, so a caller decides whether that is fatal.

``get_value(path, section, key, default="")``
    One value, stripped. Returns *default* when the file is missing,
    unreadable or malformed, or the section or key is absent. For callers
    whose answer to every one of those is "not configured".

``read_line_value(path, section, key)``
    One value read line by line with the bash ``read_ini`` rules
    (``team/scripts/lib/ini_parser.sh``): the first ``key = value`` line of
    the section wins, keys match case-sensitively, ``=`` is the only
    delimiter, a trailing inline comment (whitespace then ``#`` or ``;``)
    is stripped, and a CRLF line ending reads as LF. A file
    :func:`load` would refuse (a duplicate key, a line before the first
    section) still answers. For a Python resolver whose answer must equal a
    bash resolver's for the same file.

Two ways to edit
----------------
``set_line_value(text, section, key, value, delimiter=" = ", add=True)``
    Return *text* with the first ``key = value`` line of *section* rewritten
    to ``<key><delimiter><value>``. When the section has no such line it is
    added after the section's last ``key = value`` line (or straight after
    its header), and a missing section is appended at the end -- unless
    ``add=False``, which raises :data:`NoOptionError` instead.

``remove_line_value(text, section, key)``
    Return *text* without the first ``key = value`` line of *section*;
    *text* unchanged when there is none.

Both edit text, not a parsed file, with the :func:`read_line_value` line
rules, so every comment, blank line, unrelated key and section keeps its
bytes. The result always ends with a newline. Callers write it back with
``lib/atomic_write.py``.

``projects_registry(kanban_root)``
    The ``[project:<name>]`` entries of ``<kanban_root>/projects.cfg`` in
    priority order — ascending ``priority``, file order breaking ties, an
    absent or non-integer priority counting as 999 — the same order
    ``projects_cfg_list`` in ``team/scripts/lib/projects.sh`` prints. A
    missing projects.cfg is an empty registry; a malformed one raises
    :data:`IniError`.

``project_enabled(kanban_root, project)``
    Whether projects.cfg leaves *project* enabled -- the one answer every
    wake, discovery and OVERWATCH loop, and ``why``, act on. Only
    ``enabled=false`` (case-insensitive, surrounding whitespace ignored)
    disables a project; an absent key, an empty value or ``true`` leaves
    it enabled, and any other value leaves it enabled with one warning on
    stderr per process. The bash twin is ``projects_cfg_enabled`` in
    ``team/scripts/lib/projects.sh``; a change to the rule here needs the
    same change there. :func:`parse_project_enabled` is the rule alone, on
    one raw value. Read-only listings (:func:`projects_registry`) still
    list a disabled project.

Errors
------
:data:`IniError` is the base of every parse error; :data:`NoSectionError`
and :data:`NoOptionError` are what the parser's own ``get``/``getint`` raise
for an absent section or key. They are re-exported so callers catch them
without importing ``configparser`` themselves.

Public API:
    IniParser, IniError, NoSectionError, NoOptionError
    load(path) -> IniParser
    get_value(path, section, key, default="") -> str
    read_line_value(path, section, key) -> str | None
    set_line_value(text, section, key, value, delimiter=" = ", add=True) -> str
    remove_line_value(text, section, key) -> str
    split_lines(text) -> list[str]
    ProjectEntry
    projects_registry(kanban_root) -> list[ProjectEntry]
    parse_project_enabled(raw) -> tuple[bool, bool]
    project_enabled(kanban_root, project) -> bool
"""

from __future__ import annotations

import configparser
import dataclasses
import os
import pathlib
import re
import sys
from typing import Mapping, Union

__all__ = [
    "IniError",
    "IniParser",
    "NoOptionError",
    "NoSectionError",
    "ProjectEntry",
    "get_value",
    "load",
    "parse_project_enabled",
    "project_enabled",
    "projects_registry",
    "read_line_value",
    "remove_line_value",
    "set_line_value",
    "split_lines",
]

#: The parser type :func:`load` returns (for annotations).
IniParser = configparser.RawConfigParser

#: Base class of every INI parse error.
IniError = configparser.Error

#: Raised by ``IniParser.get``/``getint`` for an absent section.
NoSectionError = configparser.NoSectionError

#: Raised by ``IniParser.get``/``getint`` for an absent key.
NoOptionError = configparser.NoOptionError

PathLike = Union[str, "os.PathLike[str]"]

#: Priority given to a projects.cfg entry with no usable ``priority`` key,
#: matching the bash registry reader.
DEFAULT_PROJECT_PRIORITY = 999

_PROJECT_SECTION_PREFIX = "project:"


def load(path: PathLike) -> IniParser:
    """Parse the INI file at *path* without interpolation.

    Args:
        path: File to read, decoded as UTF-8.

    Returns:
        The parsed file. A missing or unreadable file yields a parser with
        no sections.

    Raises:
        IniError: The file exists but is not valid INI (for example a
            duplicate section or key, or text before the first section).
        UnicodeDecodeError: The file is not valid UTF-8.
    """
    parser = IniParser()
    parser.read(os.fspath(path), encoding="utf-8")
    return parser


def get_value(path: PathLike, section: str, key: str, default: str = "") -> str:
    """Return ``[section] key`` from the INI file at *path*, stripped.

    Args:
        path:    File to read.
        section: Section name, exactly as written in the file.
        key:     Key name (case-insensitive).
        default: Returned when the file is missing, unreadable or malformed,
                 or the section or key is absent.

    Returns:
        The value with surrounding whitespace removed (an empty value reads
        as ``""``), or *default*.
    """
    if not os.path.isfile(path):
        return default
    try:
        parser = load(path)
    except (IniError, UnicodeDecodeError):
        return default
    if not parser.has_option(section, key):
        return default
    return parser.get(section, key).strip()


# The read_ini line rules: a section header starts in column 0; blank lines
# and lines whose first non-blank character is '#' or ';' are skipped; an
# inline comment needs whitespace before its marker.
_LINE_SECTION_OPEN_RE = re.compile(r"^\[[ \t]*")
_LINE_SECTION_CLOSE_RE = re.compile(r"[ \t]*\].*$")
_LINE_SKIP_RE = re.compile(r"^[ \t]*([#;].*)?$")
_LINE_INLINE_COMMENT_RE = re.compile(r"[ \t]+[#;].*$")


def read_line_value(path: PathLike, section: str, key: str) -> "str | None":
    """Return ``[section] key`` from *path* by the bash ``read_ini`` rules.

    The line-for-line Python form of ``read_ini`` in
    ``team/scripts/lib/ini_parser.sh``, for a caller whose answer must
    equal a bash resolver's for the same file. Unlike :func:`get_value` it
    strips an inline comment (``deferred_bugs = true  # on``) and matches
    *key* case-sensitively, and it reads a file :func:`load` would refuse
    as malformed.

    Args:
        path:    File to read, decoded as UTF-8 (undecodable bytes are
                 replaced).
        section: Section name; ``[ section ]`` matches ``section``.
        key:     Key name, matched exactly after trimming blanks.

    Returns:
        The first matching value with leading blanks, an inline comment and
        trailing blanks removed (``""`` for ``key =``), or ``None`` when the
        file is missing or unreadable or the section or key is absent.
    """
    try:
        text = pathlib.Path(path).read_text(encoding="utf-8", errors="replace")
    except OSError:
        return None
    in_section = False
    for line in text.split("\n"):
        if line.endswith("\r"):
            line = line[:-1]
        if line.startswith("["):
            name = _LINE_SECTION_CLOSE_RE.sub("", _LINE_SECTION_OPEN_RE.sub("", line, count=1), count=1)
            in_section = name == section
            continue
        if not in_section or _LINE_SKIP_RE.match(line):
            continue
        raw_key, sep, raw_value = line.partition("=")
        if not sep or raw_key.strip(" \t") != key:
            continue
        value = raw_value.lstrip(" \t")
        value = _LINE_INLINE_COMMENT_RE.sub("", value, count=1)
        return value.rstrip(" \t")
    return None


def _section_name(line: str) -> "str | None":
    """Return the section a header line opens, or None for any other line."""
    if not line.startswith("["):
        return None
    return _LINE_SECTION_CLOSE_RE.sub("", _LINE_SECTION_OPEN_RE.sub("", line, count=1), count=1)


def _is_data_line(line: str) -> bool:
    """True for a ``key = value`` line (not blank, not a comment, has ``=``)."""
    return not _LINE_SKIP_RE.match(line) and "=" in line


def _line_key(line: str) -> str:
    return line.partition("=")[0].strip(" \t")


def split_lines(text: str) -> list[str]:
    """Split *text* on LF; a final newline does not start an extra line.

    These are the records awk reads from the same text. Shared by every
    module that walks a file line by line the way the shell readers do.

    A CR before the LF stays on its line, so an untouched CRLF line is
    written back byte for byte.
    """
    lines = text.split("\n")
    if lines and lines[-1] == "":
        lines.pop()
    return lines


def _join_lines(lines: list[str]) -> str:
    return "".join(f"{line}\n" for line in lines)


def _locate(lines: list[str], section: str, key: str) -> "tuple[int | None, int | None, int | None]":
    """Return ``(header, last_data, match)`` line indexes for *section*/*key*.

    *header* is the section's first header line, *last_data* the last
    ``key = value`` line of that section (None when it has none), and
    *match* the first line in it whose key is *key*. Each is None when
    absent. Only the first occurrence of the section is searched.
    """
    header = last_data = match = None
    in_section = False
    for index, raw in enumerate(lines):
        line = raw[:-1] if raw.endswith("\r") else raw
        name = _section_name(line)
        if name is not None:
            if in_section:
                break
            if name == section and header is None:
                header, in_section = index, True
            continue
        if not in_section or not _is_data_line(line):
            continue
        last_data = index
        if match is None and _line_key(line) == key:
            match = index
    return header, last_data, match


def set_line_value(
    text: str,
    section: str,
    key: str,
    value: str,
    *,
    delimiter: str = " = ",
    add: bool = True,
) -> str:
    """Return *text* with ``[section] key`` set to *value*, every other line kept.

    The first ``key = value`` line of *section* (matched by the
    :func:`read_line_value` rules: header in column 0, key compared exactly
    after trimming blanks, blank and comment lines skipped) is replaced by
    ``<key><delimiter><value>``. Without one, that line is added after the
    section's last ``key = value`` line, or straight after its header when
    it has none; without the section, ``[section]`` and the line are
    appended at the end, after a blank line.

    Args:
        text:      The file's text.
        section:   Section name.
        key:       Key name.
        value:     Value to write, as is.
        delimiter: Text between key and value (``"="`` writes ``key=value``).
        add:       When False, a missing key or section raises instead of
                   being added.

    Returns:
        The edited text, ending with a newline.

    Raises:
        NoOptionError: ``add`` is False and *section* has no *key* line
            (or there is no *section*).
    """
    lines = split_lines(text)
    header, last_data, match = _locate(lines, section, key)
    new_line = f"{key}{delimiter}{value}"
    if match is not None:
        lines[match] = new_line
    elif not add:
        raise NoOptionError(key, section)
    elif header is not None:
        lines.insert((last_data if last_data is not None else header) + 1, new_line)
    else:
        if lines and lines[-1].strip(" \t\r") != "":
            lines.append("")
        lines.extend([f"[{section}]", new_line])
    return _join_lines(lines)


def remove_line_value(text: str, section: str, key: str) -> str:
    """Return *text* without the first ``[section] key`` line.

    The line is found by the same rules as :func:`set_line_value`. When
    there is none, *text* is returned exactly as given.

    Args:
        text:    The file's text.
        section: Section name.
        key:     Key name.

    Returns:
        The edited text, ending with a newline, or *text* unchanged.
    """
    lines = split_lines(text)
    _header, _last, match = _locate(lines, section, key)
    if match is None:
        return text
    del lines[match]
    return _join_lines(lines)


@dataclasses.dataclass(frozen=True)
class ProjectEntry:
    """One ``[project:<name>]`` section of projects.cfg.

    Attributes:
        name:          Project name (the text after ``project:``).
        priority:      ``priority`` as an integer; 999 when absent or not an
                       integer.
        section_index: Position of this section among ALL sections of the
                       file (0-based, other section types included) — the
                       registration order the palette fallback colours by.
        options:       Every key of the section, values as written.
    """

    name: str
    priority: int
    section_index: int
    options: Mapping[str, str]

    @property
    def color(self) -> str:
        """``dashboard_color``, stripped; ``""`` when absent."""
        return self.options.get("dashboard_color", "").strip()


def _parse_priority(raw: "str | None") -> int:
    if raw is None:
        return DEFAULT_PROJECT_PRIORITY
    try:
        return int(raw)
    except ValueError:
        return DEFAULT_PROJECT_PRIORITY


def projects_registry(kanban_root: PathLike) -> list[ProjectEntry]:
    """Return the projects registered in ``<kanban_root>/projects.cfg``.

    Args:
        kanban_root: The kanban root directory.

    Returns:
        One :class:`ProjectEntry` per ``[project:<name>]`` section, sorted by
        ``(priority, section_index)``. Empty when projects.cfg is absent or
        registers no project.

    Raises:
        IniError: projects.cfg exists but is not valid INI.
    """
    cfg_path = pathlib.Path(kanban_root) / "projects.cfg"
    if not cfg_path.is_file():
        return []
    parser = load(cfg_path)
    entries: list[ProjectEntry] = []
    for index, section in enumerate(parser.sections()):
        if not section.startswith(_PROJECT_SECTION_PREFIX):
            continue
        options = dict(parser.items(section))
        entries.append(
            ProjectEntry(
                name=section[len(_PROJECT_SECTION_PREFIX):],
                priority=_parse_priority(options.get("priority")),
                section_index=index,
                options=options,
            )
        )
    entries.sort(key=lambda entry: (entry.priority, entry.section_index))
    return entries


#: The one ``enabled`` value that disables a project (compared lowercased).
PROJECT_DISABLED_VALUE = "false"

#: ``enabled`` values that leave a project enabled without a warning
#: (compared lowercased); ``""`` is an absent key or an empty value.
_PROJECT_ENABLED_VALUES = frozenset({"", "true"})

#: ``(projects.cfg path, project, raw value)`` already warned about in this
#: process, so each unrecognised value is narrated once.
_ENABLED_WARNED: "set[tuple[str, str, str]]" = set()


def parse_project_enabled(raw: "str | None") -> "tuple[bool, bool]":
    """Read one raw projects.cfg ``enabled`` value.

    Args:
        raw: The value as written, or None when the key is absent.

    Returns:
        ``(enabled, recognised)``. Only ``false`` (any case, surrounding
        whitespace ignored) gives ``enabled`` False. ``recognised`` is False
        for a non-empty value other than ``true`` / ``false`` -- still
        enabled, but worth a warning.
    """
    value = (raw or "").strip().lower()
    if value == PROJECT_DISABLED_VALUE:
        return False, True
    return True, value in _PROJECT_ENABLED_VALUES


def project_enabled(kanban_root: PathLike, project: str) -> bool:
    """True unless ``<kanban_root>/projects.cfg`` sets ``enabled=false`` for *project*.

    A project projects.cfg does not register, or a missing projects.cfg,
    reads as enabled. An unrecognised value (``enabled=no``) reads as
    enabled and prints one warning per process on stderr naming the value
    and the fix.

    Raises:
        IniError: projects.cfg exists but is not valid INI.
    """
    for entry in projects_registry(kanban_root):
        if entry.name != project:
            continue
        raw = entry.options.get("enabled")
        enabled, recognised = parse_project_enabled(raw)
        if not recognised:
            key = (str(pathlib.Path(kanban_root) / "projects.cfg"), project, raw or "")
            if key not in _ENABLED_WARNED:
                _ENABLED_WARNED.add(key)
                print(
                    f"[projects.cfg] WARNING: enabled for '{project}' is '{(raw or '').strip()}' "
                    "(only true or false are recognised); treating the project as enabled "
                    "-- set enabled=false to pause it or enabled=true to silence this warning",
                    file=sys.stderr,
                )
        return enabled
    return True
