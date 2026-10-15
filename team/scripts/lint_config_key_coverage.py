#!/usr/bin/env python3
"""
lint_config_key_coverage.py
===========================
Cross-checks what the code reads from the two configuration files against
what their templates scaffold and their Key Registries declare, and reports
where they disagree.

For kanban.cfg the three views are:

* the keys the code READS from kanban.cfg -- every ``read_ini`` call against
  kanban.cfg and every ``config_get`` call in the shipped shell code, plus
  every Python read call site whose path names kanban.cfg;
* the keys ``kanban.cfg_example`` SCAFFOLDS -- active ``key = value`` lines
  and documented commented examples (``# key = value``);
* the keys the loader's Key Registry ACCEPTS -- ``_CONFIG_LOADER_KNOWN_KEYS``
  in ``team/scripts/lib/config_loader.sh``.

For project.cfg they are the keys read from project.cfg, the keys
``team/config/project.cfg_example`` scaffolds, and the text ``create_project``
writes for a new project (``render_project_cfg`` in
``team/pgai_agent_kanban/ops/projects.py``, rendered with a dummy project
name in a child process whose working and temporary directories are a
private temp dir; it writes nothing). Defaults come from the two Key
Registry comments: config_loader.sh's for kanban.cfg and for the sections
both files share, project_paths.sh's for the rest of project.cfg.

Finding kinds:

``read_not_scaffolded``
    A key read from kanban.cfg at a literal call site that
    ``kanban.cfg_example`` does not scaffold, so a fresh install's kanban.cfg
    never shows the operator the setting exists. A key the Key Registry
    comment marks ``DEPRECATED`` is exempt: it is read only as a fallback
    for an old spelling and is deliberately not scaffolded.
``example_not_read``
    A key ``kanban.cfg_example`` scaffolds that no code reads, so an operator
    who edits it changes nothing.
``registry_not_read``
    A ``_CONFIG_LOADER_KNOWN_KEYS`` entry no code reads, so a stale value is
    accepted without the unknown-key warning.
``project_cfg_read_not_scaffolded``
    A key read from project.cfg at a literal call site that the project.cfg
    template, or the text ``create_project`` writes, does not carry.
``project_cfg_example_not_read``
    A key the project.cfg template scaffolds that nothing reads from
    project.cfg.
``missing_default``
    A key, active or commented, in either template or in the
    ``create_project`` text whose comment block states no default: none of
    ``Default:``, ``Shipped value:``, ``Inherits ...`` or ``No in-code
    default``.
``default_mismatch``
    A comment block's stated default (``Default: X``, or the last tier of
    ``Inherits: ... > X``) differs from the Key Registry default. A registry
    default of ``N/A`` is not compared. ``empty``, ``unset``, ``absent`` and
    ``blank`` read as the empty value; a trailing parenthetical or sentence
    is cut; a block shared by several keys (``Default: a 13, b 34``) is read
    per key.
``wrong_section``
    A key, active or commented, that a template carries only under a
    section other than the one its reader looks in. Reported instead of the
    read-not-scaffolded and not-read findings the misplacement would cause.
``commented_value_not_default``
    A commented ``# key = value`` line whose value is not the key's default
    (the registry default, else the block's stated one): a commented line
    shows the value in force, so an example override there misleads.
``read_but_unconsumed``
    A key whose every read is ``export VAR="$(read_ini ...)"`` (or the same
    with ``_pp_read_cfg_key``/``config_get``) where no scanned code mentions
    VAR anywhere but the export line itself.
``project_cfg_render_failed``
    The ``create_project`` text could not be rendered, so the comparisons
    against it did not run.

ENFORCEMENT CLASS
-----------------
CONVERT (quality class, ``team/SOP-core.md`` "Blocking Severity: only real
damage blocks"). An unscaffolded, misdocumented or unread key is a
documentation and discoverability defect -- every reader still falls back
to its in-code default -- so the lint narrates each finding with a runnable
remedy, writes them to ``--findings-file`` for the gated runner's
aggregate, and ALWAYS exits 0. It carries no census anchor because it has
no refusal.

WHAT COUNTS AS A READ
---------------------
Shell (``*.sh``; comment lines skipped, backslash continuations joined):

* ``read_ini FILE SECTION KEY [DEFAULT]``. FILE is kanban.cfg when its text
  names ``kanban.cfg`` / ``kanban_cfg``; project.cfg when it names
  ``project`` or ``new_cfg``; another INI file when it names ``manifest`` or
  ``workflow``; any other FILE (``"$cfg"``) is taken as kanban.cfg when
  SECTION is one kanban.cfg uses (a section of the example or the registry)
  or is itself a variable, and as project.cfg when SECTION is one only
  project.cfg uses.
* ``_pp_read_cfg_key FILE SECTION KEY [DEFAULT]`` -- project_paths.sh's
  project.cfg reader, never kanban.cfg.
* ``config_get SECTION KEY [DEFAULT]`` -- always kanban.cfg.

A call whose SECTION and KEY are both literal is a LITERAL site. A
project.cfg read in a section kanban.cfg also uses (the shared ``[release]``
keys are declared once, in the kanban.cfg registry -- see the Key Registry
header in config_loader.sh) satisfies ``example_not_read`` and
``registry_not_read`` but is never a kanban.cfg read; a read from another
INI file counts only in such a shared section.

A kanban.cfg or project.cfg call with a variable SECTION or KEY is a DYNAMIC
site. A bare variable resolves through its assignments (``X=value``,
``local X=value``, ``for X in a b c``) in its enclosing function -- from the
``name() {`` line to the site -- or in the whole file at top level, one
level deep. Then:

* A side that comes from a positional parameter of the enclosing function
  (``local key="$2"``) is resolved through the function's CALLERS anywhere
  in the scanned shell code: a call passing literal values
  (``_config_loader_export_wake_integer "$cfg" max_tasks_per_wake ...``) is
  a literal site at the call, and is checked like one.
* A variable inside a template with literal parts (``models.${provider}``,
  ``budget_bytes_${role}``) is a wildcard: the literal part names the key
  family the site reads, so every key of that family is read.
* Any other wholly variable side is a wildcard that counts only for a key
  whose name appears as a word in the code of the same file -- the evidence
  that the file feeds that name to the site. The Key Registry array is not
  code for this purpose.
* A call whose SECTION and KEY are both unresolved variables is a generic
  accessor's own body (``config_get``) and marks nothing; its callers are
  the reads.

Python (``*.py``, parsed with ``ast``): a read is a call site, never a
module-wide mention. Two call shapes count when SECTION and KEY resolve to
string literals (directly, or through a constant assigned in the same
function or at module level):

* ``get_value(PATH, SECTION, KEY)``, ``read_line_value(PATH, SECTION, KEY)``
  or ``read_cfg_key(PATH, SECTION, KEY)``;
* ``P.get(SECTION, KEY)`` (or ``getint``/``getfloat``/``getboolean``/
  ``has_option``) on a parser ``P`` assigned in the same function from
  ``load(PATH)``, or from ``ConfigParser()`` and then ``P.read(PATH)``; a
  parser that is itself a parameter of the function stands for its PATH.

PATH decides the file: it is kanban.cfg when the expression names
``kanban.cfg`` / ``kanban_cfg`` (a string, a variable, an attribute or a
called function's name), project.cfg when it names ``project.cfg`` /
``project_cfg``, either way through the variables it is assigned from in the
same function or at module level. A PATH that is a parameter of the
enclosing function and names neither file is a read of whichever file the
caller passes: it marks the key read for the "not read" kinds of both files
but is no literal site for either. Any other PATH is not a read.

Only literal sites (including caller-resolved ones) are checked for the
read-not-scaffolded kinds and ``wrong_section``. This is a textual scan, not
an interpreter: a read whose section and key are computed beyond these rules
is invisible to it.

WHAT IS SCANNED
---------------
Every ``*.sh`` and ``*.py`` file under ``team/`` (relative to ``--root``) and
the root ``install.sh``, except test code -- any file with a directory named
``tests``, ``test`` or ``fixtures`` between ``team/`` and the file -- and
this lint itself. ``kanban.cfg_example`` is read from ``--root``, the
registries from ``team/scripts/lib/config_loader.sh`` and
``team/scripts/lib/project_paths.sh`` under it, and the project.cfg template
from ``team/config/project.cfg_example`` (else ``project.cfg_example`` at
``--root``). The project.cfg checks run only when that template exists, and
the ``create_project`` comparison only when
``team/pgai_agent_kanban/ops/projects.py`` does.

OUTPUT
------
One line per finding, then a totals line, then (when there are findings) a
numbered fix block and the rule:

    lint_config_key_coverage: <file>:<line>: <kind>: <what> -- <fix>
    lint_config_key_coverage: totals: <N> file(s) scanned; <R> key(s) read; <F> finding(s)

``--findings-file PATH`` writes the findings as a JSON array (keys ``lint``,
``severity``, ``kind``, ``section``, ``key``, ``file``, ``line``,
``summary``, ``remedy``) in the shape
``team/scripts/lib/gated_runner_lint.sh``'s
``gated_runner_merge_findings_file`` merges; ``[]`` when clean.

``--list-read`` prints every key found read from kanban.cfg, one
``section:key`` per line, and nothing else.

USAGE
-----
  python3 team/scripts/lint_config_key_coverage.py [--root PATH]
                                                   [--findings-file PATH]
                                                   [--list-read]

  --root PATH           Repository root (default: two levels above this
                        script's directory -- the checkout it sits in).
  --findings-file PATH  Write findings as a JSON array to PATH.
  --list-read           Print the keys read from kanban.cfg and exit 0.

EXIT CODES
----------
  0   Always, whatever was found (quality class). A malformed command line
      still exits 2 (argparse usage validation, not a loop-facing gate).
"""

from __future__ import annotations

import argparse
import ast
import json
import os
import re
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import NamedTuple, Optional

# The Key Registry comment parser lives in lint_doc_command_truth.py beside
# this file; it is imported, never copied.
_SCRIPTS_DIR = str(Path(__file__).resolve().parent)
if _SCRIPTS_DIR not in sys.path:
    sys.path.insert(0, _SCRIPTS_DIR)

import lint_doc_command_truth as _doc_truth  # noqa: E402

LINT_NAME = "config_key_coverage"
SCAN_TOP = "team"
ROOT_SCRIPTS = ("install.sh",)
TEST_DIR_NAMES = frozenset({"tests", "test", "fixtures"})
EXAMPLE_REL = "kanban.cfg_example"
REGISTRY_REL = "team/scripts/lib/config_loader.sh"
SELF_REL = "team/scripts/lint_config_key_coverage.py"
REGISTRY_ARRAY = "_CONFIG_LOADER_KNOWN_KEYS"

# The project.cfg template, its Key Registry, and the create_project
# generator that renders a new project's project.cfg from it.
PROJECT_EXAMPLE_RELS = ("team/config/project.cfg_example", "project.cfg_example")
PROJECT_REGISTRY_REL = "team/scripts/lib/project_paths.sh"
GENERATOR_REL = "team/pgai_agent_kanban/ops/projects.py"
GENERATOR_MODULE = "pgai_agent_kanban.ops.projects"
GENERATOR_FUNCTION = "render_project_cfg"
GENERATOR_DISPLAY = "create_project output"
RENDER_PROJECT_NAME = "lint-render-check"
RENDER_TIMEOUT_SECONDS = 60

# Rendered in a child interpreter so the scanned tree's own package is
# imported, never this process's: argv[1] is the scanned team/ directory,
# argv[2] the dummy project name.
_RENDER_PROGRAM = (
    "import sys\n"
    "sys.path.insert(0, sys.argv[1])\n"
    f"from {GENERATOR_MODULE} import {GENERATOR_FUNCTION}\n"
    f"sys.stdout.write({GENERATOR_FUNCTION}(name=sys.argv[2], generated_by='{SELF_REL}'))\n"
)

RULE = (
    "Every key the code reads from kanban.cfg or project.cfg is scaffolded, "
    "under the section its reader uses, in the template (and, for project.cfg, "
    "in the text create_project writes), with a comment stating the default the "
    "Key Registry records; a commented line shows that default; every "
    "scaffolded or registered key is read, and read for more than an export "
    "nothing consumes."
)

# A file argument naming kanban.cfg, project.cfg, or some other INI file.
_KANBAN_FILE_RE = re.compile(r"kanban[._]cfg", re.IGNORECASE)
_PROJECT_FILE_RE = re.compile(r"project|new_cfg", re.IGNORECASE)
_OTHER_FILE_RE = re.compile(r"manifest|workflow", re.IGNORECASE)
# A Python path expression naming project.cfg.
_PROJECT_CFG_NAME_RE = re.compile(r"project[._]cfg", re.IGNORECASE)

# Readers taking read_ini's own arguments: FILE SECTION KEY [DEFAULT].
# _pp_read_cfg_key (project_paths.sh) is the project.cfg reader by contract.
_READ_INI_RE = re.compile(r"(?<![\w-])(read_ini|_pp_read_cfg_key)\s+(?!\()")
_PROJECT_CFG_READERS = frozenset({"_pp_read_cfg_key"})
_CONFIG_GET_RE = re.compile(r"(?<![\w-])config_get\s+(?!\()")
_VAR_RE = re.compile(r"\$(?:\{[^}]*\}|\([^)]*\)|[A-Za-z_][A-Za-z0-9_]*|[0-9@*#?$!-])")
_BARE_VAR_RE = re.compile(r"^\$(?:\{([A-Za-z_][A-Za-z0-9_]*)\}|([A-Za-z_][A-Za-z0-9_]*))$")
# A positional parameter, bare or braced, optionally with an expansion
# modifier: $1, ${2}, ${3:-}.
_POSITIONAL_RE = re.compile(r"^\$\{?([1-9])(?:[:#%/^,][^}]*)?\}?$")
_FUNC_DEF_RE = re.compile(
    r"^\s*(?:function\s+)?([A-Za-z_][A-Za-z0-9_:.-]*)\s*\(\)\s*(?:\{.*)?$"
    r"|^\s*function\s+([A-Za-z_][A-Za-z0-9_:.-]*)\s*(?:\{.*)?$"
)
_WORD_CHARS = r"A-Za-z0-9_"

_SECTION_RE = re.compile(r"^\s*\[([^\]]+)\]\s*$")
_ACTIVE_KEY_RE = re.compile(r"^\s*([A-Za-z0-9_][A-Za-z0-9_.-]*)\s*=")
# A documented commented example: "# key = value" with a single-token value
# (or none). Prose that happens to contain "word = words" never matches.
_COMMENTED_KEY_RE = re.compile(r"^\s*[#;]\s?([a-z_][a-z0-9_]*)\s*=\s*(\S*)\s*$")
_REGISTRY_SECTION_RE = re.compile(r"^#\s*\[([^\]]+)\]\s*$")
_REGISTRY_KEY_RE = re.compile(r"^#\s{2,}([a-z_][a-z0-9_]*)\s+(?:REQUIRED|OPTIONAL)\b(.*)$")

# Python read calls: path readers take (PATH, SECTION, KEY); parser getters
# take (SECTION, KEY) on a parser loaded from PATH.
_PY_PATH_READERS = frozenset({"read_line_value", "get_value", "read_cfg_key"})
_PY_PARSER_GETTERS = frozenset({"get", "getint", "getfloat", "getboolean", "has_option"})
_PY_PARSER_LOADERS = frozenset({"load"})
_PY_PARSER_CLASSES = frozenset({"ConfigParser", "RawConfigParser", "SafeConfigParser"})

# ``export VAR="$(read_ini ...)"``: the read's only use is the export.
_EXPORT_RE = re.compile(r"^\s*export\s+([A-Za-z_][A-Za-z0-9_]*)=")

# Comment-block forms that state a default, or say there is none to state.
# "Default" is matched with its capital D, so "No in-code default: ..." is not
# read as a stated default; "Default when absent or blank: X" is.
_DEFAULT_RE = re.compile(r"\bDefault(?:\s+when\b[^:]*)?\s*:\s*(.*)$")
_DEFAULT_WORD_RE = re.compile(r"\bDefault\b")
# A commented key line in a template: "# key = value", with blanks around
# the "=" as both templates write them, so prose such as "# false=off)." is
# never taken for a key.
_TEMPLATE_COMMENTED_KEY_RE = re.compile(r"^\s*[#;]\s?([a-z_][a-z0-9_]*)\s+=(?:\s+(\S*))?\s*$")
_SHIPPED_RE = re.compile(r"\bShipped\s+value\s*:", re.IGNORECASE)
_NO_DEFAULT_RE = re.compile(r"\bNo\s+in-code\s+default\b", re.IGNORECASE)
_INHERITS_RE = re.compile(r"\bInherits\b\s*:?\s*(.*)$", re.IGNORECASE)
# Where a stated default's value ends: a parenthetical, a clause, a sentence.
_DEFAULT_END_RE = re.compile(r"\s+\(|;\s|\.\s|,\s|\s+(?:—|--)\s|\s{2,}")
# Spellings of "no value" in a stated default.
_EMPTY_DEFAULTS = frozenset({"empty", "unset", "absent", "blank", "nothing", '""', "''"})


class Finding(NamedTuple):
    """One reported disagreement between the read, example and registry key sets."""

    kind: str
    section: str
    key: str
    file: str
    line: int
    summary: str
    remedy: str

    def to_dict(self) -> dict:
        """Return the finding as the gated runner's per-instance JSON object.

        Keys: ``lint``, ``severity``, ``kind``, ``section``, ``key``, ``file``, ``line``, ``summary``, ``remedy``.
        """
        return {
            "lint": LINT_NAME,
            "severity": "quality",
            "kind": self.kind,
            "section": self.section,
            "key": self.key,
            "file": self.file,
            "line": self.line,
            "summary": self.summary,
            "remedy": self.remedy,
        }


class ReadSite(NamedTuple):
    """A literal INI read: both section and key are fixed strings.

    *cfg* is the file read: ``kanban`` (kanban.cfg), ``project``
    (project.cfg), ``other`` (another INI file, kept only in a section
    kanban.cfg shares) or ``either`` (a Python read whose path is a
    parameter -- whichever file the caller passes). *export* names the
    variable when the read's only use is ``export VAR=...`` on its line.
    """

    section: str
    key: str
    file: str
    line: int
    cfg: str = "kanban"
    export: str = ""

    @property
    def kanban_file(self) -> bool:
        """True for a read from kanban.cfg."""
        return self.cfg == "kanban"


class DynamicSite(NamedTuple):
    """A kanban.cfg or project.cfg read (*cfg*) with a variable section or key.

    *section_re* / *key_re* are full-match patterns. When the variable is a
    positional parameter of *function*, *section_pos* / *key_pos* name it and
    the function's callers supply the value; *section_literal* /
    *key_literal* hold the side that is a fixed string, if either is.
    """

    section_re: "re.Pattern[str]"
    key_re: "re.Pattern[str]"
    file: str
    line: int
    function: "str | None" = None
    section_pos: "int | None" = None
    key_pos: "int | None" = None
    section_literal: "str | None" = None
    key_literal: "str | None" = None
    cfg: str = "kanban"


# ---------------------------------------------------------------------------
# kanban.cfg_example and the Key Registry
# ---------------------------------------------------------------------------


def parse_example(path: Path) -> dict[tuple[str, str], int]:
    """Return ``{(section, key): line}`` for every key *path* scaffolds.

    Counts active ``key = value`` lines and documented commented examples
    (``# key = value`` with at most one value token). A missing file is an
    empty mapping.
    """
    keys: dict[tuple[str, str], int] = {}
    try:
        lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
    except OSError:
        return keys
    section = ""
    for number, line in enumerate(lines, start=1):
        match = _SECTION_RE.match(line)
        if match:
            section = match.group(1).strip()
            continue
        if not section:
            continue
        match = _ACTIVE_KEY_RE.match(line) or _COMMENTED_KEY_RE.match(line)
        if match:
            keys.setdefault((section, match.group(1)), number)
    return keys


def example_section_lines(path: Path) -> dict[str, int]:
    """Return ``{section: header line}`` for *path*; empty when unreadable."""
    sections: dict[str, int] = {}
    try:
        lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
    except OSError:
        return sections
    for number, line in enumerate(lines, start=1):
        match = _SECTION_RE.match(line)
        if match:
            sections.setdefault(match.group(1).strip(), number)
    return sections


def _registry_array_span(lines: list[str]) -> tuple[int, int]:
    """Return the 0-based ``[start, end]`` line span of the registry array, or ``(-1, -2)``."""
    for start, line in enumerate(lines):
        if line.lstrip().startswith(f"{REGISTRY_ARRAY}=("):
            for end in range(start, len(lines)):
                code = lines[end].split("#", 1)[0]
                if ")" in code:
                    return start, end
            return start, len(lines) - 1
    return -1, -2


def parse_registry(path: Path) -> tuple[dict[tuple[str, str], int], set[tuple[str, str]]]:
    """Return ``({(section, key): line}, deprecated)`` from config_loader.sh.

    The first mapping is ``_CONFIG_LOADER_KNOWN_KEYS``; *deprecated* holds the
    keys whose Key Registry comment line says ``DEPRECATED``.
    """
    known: dict[tuple[str, str], int] = {}
    deprecated: set[tuple[str, str]] = set()
    try:
        lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
    except OSError:
        return known, deprecated
    start, end = _registry_array_span(lines)
    for index in range(max(start, 0), end + 1):
        code = lines[index].split("#", 1)[0]
        if index == start:
            code = code.split("(", 1)[1] if "(" in code else ""
        code = code.split(")", 1)[0]
        for token in code.split():
            section, sep, key = token.partition(":")
            if sep and section and key:
                known.setdefault((section, key), index + 1)
    section = ""
    for line in lines[: max(start, 0)]:
        match = _REGISTRY_SECTION_RE.match(line)
        if match:
            section = match.group(1).strip()
            continue
        match = _REGISTRY_KEY_RE.match(line)
        if match and section and "DEPRECATED" in match.group(2):
            deprecated.add((section, match.group(1)))
    return known, deprecated


# ---------------------------------------------------------------------------
# Shell scanning
# ---------------------------------------------------------------------------


def _skip_balanced(text: str, i: int, open_ch: str, close_ch: str) -> int:
    """Return the index just past the bracket closing the one at ``text[i]``."""
    depth = 0
    n = len(text)
    while i < n:
        ch = text[i]
        if ch == "\\":
            i += 2
            continue
        if ch == open_ch:
            depth += 1
        elif ch == close_ch:
            depth -= 1
            if depth == 0:
                return i + 1
        i += 1
    return n


def shell_words(text: str, limit: int) -> list[str]:
    """Split up to *limit* shell words from *text*, stopping at an operator.

    Quotes, ``${...}`` and ``$(...)`` are kept whole; an unquoted ``)``,
    ``|``, ``;``, ``&``, ``<``, ``>`` or backtick ends the command.
    """
    words: list[str] = []
    i, n = 0, len(text)
    while i < n and len(words) < limit:
        while i < n and text[i] in " \t":
            i += 1
        if i >= n or text[i] in "\n)|;&<>`}" or text[i] == "#":
            break
        start = i
        while i < n and text[i] not in " \t\n)|;&<>`":
            ch = text[i]
            if ch == "\\":
                i += 2
            elif ch == "'":
                end = text.find("'", i + 1)
                i = n if end < 0 else end + 1
            elif ch == '"':
                i += 1
                while i < n and text[i] != '"':
                    if text[i] == "\\":
                        i += 2
                    elif text.startswith("${", i):
                        i = _skip_balanced(text, i + 1, "{", "}")
                    elif text.startswith("$(", i):
                        i = _skip_balanced(text, i + 1, "(", ")")
                    else:
                        i += 1
                i += 1
            elif text.startswith("${", i):
                i = _skip_balanced(text, i + 1, "{", "}")
            elif text.startswith("$(", i):
                i = _skip_balanced(text, i + 1, "(", ")")
            elif ch == "}":
                break
            else:
                i += 1
        word = text[start:i]
        if word:
            words.append(word)
    return words


def unquote(word: str) -> str:
    """Drop shell quote characters from *word*, keeping everything else."""
    return word.replace('"', "").replace("'", "")


def _strip_shell_comment(line: str) -> str:
    """Return *line* without a trailing ``# comment`` outside quotes and ``${...}``."""
    quote = ""
    depth = 0
    for i, ch in enumerate(line):
        if quote:
            if ch == quote:
                quote = ""
            continue
        if ch in "\"'":
            quote = ch
        elif ch == "{":
            depth += 1
        elif ch == "}":
            depth = max(depth - 1, 0)
        elif ch == "#" and depth == 0 and (i == 0 or line[i - 1] in " \t;"):
            return line[:i]
    return line


def shell_code_lines(text: str) -> list[tuple[int, str]]:
    """Return ``(line_number, code)`` with comments stripped and continuations joined."""
    out: list[tuple[int, str]] = []
    pending = ""
    pending_line = 0
    for number, raw in enumerate(text.splitlines(), start=1):
        if raw.lstrip().startswith("#") and not pending:
            continue
        code = _strip_shell_comment(raw)
        if not pending:
            pending_line = number
        if code.rstrip().endswith("\\"):
            pending += code.rstrip()[:-1] + " "
            continue
        out.append((pending_line, pending + code))
        pending = ""
    if pending:
        out.append((pending_line, pending))
    return out


def _assignment_values(code: str, name: str) -> list[str]:
    """Return every value *name* is assigned in shell *code* (``=`` and ``for ... in``)."""
    values: list[str] = []
    assign = re.compile(rf"(?<![{_WORD_CHARS}$]){re.escape(name)}=(?!=)")
    for match in assign.finditer(code):
        words = shell_words(code[match.end():], 1)
        values.append(words[0] if words else "")
    loop = re.compile(rf"\bfor\s+{re.escape(name)}\s+in\s+([^;\n]*)")
    for match in loop.finditer(code):
        values.extend(shell_words(match.group(1), 64))
    return values


def _pattern_parts(word: str) -> list[tuple[str, bool]]:
    """Split an unquoted word into ``(text, is_variable)`` parts."""
    parts: list[tuple[str, bool]] = []
    pos = 0
    for match in _VAR_RE.finditer(word):
        if match.start() > pos:
            parts.append((word[pos:match.start()], False))
        parts.append((match.group(0), True))
        pos = match.end()
    if pos < len(word):
        parts.append((word[pos:], False))
    return parts


def resolved_values(word: str, scope: str) -> list[str]:
    """Return the values a shell word may take: itself, or a bare variable's assignments in *scope*."""
    text = unquote(word)
    match = _BARE_VAR_RE.match(text)
    if not match:
        return [text]
    resolved = {unquote(v) for v in _assignment_values(scope, match.group(1) or match.group(2))}
    resolved.discard("")
    return sorted(resolved) or [text]


def positional_index(word: str, scope: str) -> "int | None":
    """Return N when *word* is, or is assigned only from, positional parameter N."""
    values = resolved_values(word, scope)
    if len(values) != 1:
        return None
    match = _POSITIONAL_RE.match(values[0])
    return int(match.group(1)) if match else None


def word_alternatives(word: str, scope: str) -> list[str]:
    """Return distinct regex alternatives for a shell word, one per resolved value.

    A literal word is itself. A bare variable resolves through its
    assignments in *scope* (one level). A value that is wholly one variable
    becomes a capture group, which the evidence check later requires to
    appear in the file as a word; a variable inside a template with literal
    parts (``models.${provider}``) becomes a non-capturing wildcard -- the
    literal part already names the key family the site reads.
    """
    alternatives: list[str] = []
    for candidate in resolved_values(word, scope):
        parts = _pattern_parts(candidate)
        if len(parts) == 1 and parts[0][1]:
            alternative = "(.+)"
        else:
            alternative = "".join(
                "(?:.+)" if is_var else re.escape(part) for part, is_var in parts
            )
        if alternative not in alternatives:
            alternatives.append(alternative)
    return alternatives


def _file_kind(
    reader: str, file_word: str, section_word: str, kanban_sections: set[str], project_sections: set[str],
) -> str:
    """Return the file a shell read reads: ``kanban``, ``project`` or ``other``.

    See the module docstring's "WHAT COUNTS AS A READ" for the rules.
    """
    if reader in _PROJECT_CFG_READERS:
        return "project"
    if _KANBAN_FILE_RE.search(file_word):
        return "kanban"
    if _PROJECT_FILE_RE.search(file_word):
        return "project"
    if _OTHER_FILE_RE.search(file_word):
        return "other"
    section = unquote(section_word)
    if "$" in section or section in kanban_sections:
        return "kanban"
    if section in project_sections:
        return "project"
    return "other"


class ShellScan(NamedTuple):
    """One shell file's reads: literal sites, dynamic sites, and its code lines."""

    literal: list[ReadSite]
    dynamic: list[DynamicSite]
    code: str
    lines: list[tuple[int, str]]


def scan_shell(
    text: str, rel: str, kanban_sections: set[str], registry_span: "tuple[int, int] | None" = None,
    project_sections: "set[str] | None" = None,
) -> ShellScan:
    """Scan shell *text* for kanban.cfg and project.cfg reads.

    *registry_span* (1-based inclusive lines) is dropped from the code so the
    Key Registry array is never taken as evidence of a read. A variable is
    resolved within its enclosing function (from the ``name() {`` line to
    the site), or within the whole file at top level. *project_sections*
    are the sections only project.cfg uses (template and registry).
    """
    project_sections = project_sections or set()
    lines = shell_code_lines(text)
    if registry_span is not None:
        lo, hi = registry_span
        lines = [(n, c) for n, c in lines if not lo <= n <= hi]
    code = "\n".join(c for _, c in lines)
    literal: list[ReadSite] = []
    dynamic: list[DynamicSite] = []
    function: "str | None" = None
    function_start = 0
    for index, (number, line) in enumerate(lines):
        definition = _FUNC_DEF_RE.match(line)
        if definition:
            function, function_start = definition.group(1) or definition.group(2), index
        elif line.rstrip() == "}":
            function = None
        scope = "\n".join(c for _, c in lines[function_start:index + 1]) if function else code
        calls: list[tuple[str, str, str]] = []
        for match in _READ_INI_RE.finditer(line):
            words = shell_words(line[match.end():], 3)
            if len(words) == 3:
                kind = _file_kind(match.group(1), words[0], words[1], kanban_sections, project_sections)
                calls.append((words[1], words[2], kind))
        for match in _CONFIG_GET_RE.finditer(line):
            words = shell_words(line[match.end():], 2)
            if len(words) == 2:
                calls.append((words[0], words[1], "kanban"))
        export_match = _EXPORT_RE.match(line)
        export = export_match.group(1) if export_match and len(calls) == 1 else ""
        for section_word, key_word, kind in calls:
            section, key = unquote(section_word), unquote(key_word)
            if "$" not in section and "$" not in key and "`" not in section + key:
                if kind in ("kanban", "project") or section in kanban_sections:
                    literal.append(ReadSite(section, key, rel, number, kind, export))
                continue
            if kind not in ("kanban", "project"):
                continue
            section_alts = word_alternatives(section_word, scope)
            key_alts = word_alternatives(key_word, scope)
            if section_alts == ["(.+)"] and key_alts == ["(.+)"]:
                # A generic accessor's own body (config_get); its callers are the reads.
                continue
            section_pos = positional_index(section_word, scope) if function else None
            key_pos = positional_index(key_word, scope) if function else None
            for section_alt in section_alts:
                for key_alt in key_alts:
                    dynamic.append(DynamicSite(
                        re.compile(section_alt), re.compile(key_alt), rel, number,
                        function if (section_pos or key_pos) else None, section_pos, key_pos,
                        section if "$" not in section else None, key if "$" not in key else None,
                        kind,
                    ))
    return ShellScan(literal, dynamic, code, lines)


# ---------------------------------------------------------------------------
# Python scanning
# ---------------------------------------------------------------------------


class _PyScope(NamedTuple):
    """One function's (or the module's) names: parameters, assignments, and ``P.read(PATH)`` calls."""

    params: frozenset[str]
    assigns: dict[str, list[ast.expr]]
    parser_reads: dict[str, ast.expr]


def _iter_elements(iterable: ast.expr, assigns: dict[str, list[ast.expr]],
                   appends: dict[str, list[ast.expr]]) -> list[ast.expr]:
    """The element expressions a ``for`` loop over *iterable* may bind.

    A tuple, list or set literal gives its elements; a name gives the
    elements of the literals assigned to it plus everything ``.append``-ed
    to it in the same scope.
    """
    if isinstance(iterable, (ast.Tuple, ast.List, ast.Set)):
        return list(iterable.elts)
    if isinstance(iterable, ast.Name):
        out: list[ast.expr] = []
        for value in assigns.get(iterable.id, []):
            if isinstance(value, (ast.Tuple, ast.List, ast.Set)):
                out.extend(value.elts)
        out.extend(appends.get(iterable.id, []))
        return out
    return []


def _scope_names(body_nodes: list[ast.AST], params: frozenset[str]) -> _PyScope:
    """Collect *body_nodes*' simple-name assignments and parser ``.read(PATH)`` calls.

    A ``for`` target counts as assigned each element of what it iterates
    (see :func:`_iter_elements`); a tuple target takes the matching member
    of each tuple element.
    """
    assigns: dict[str, list[ast.expr]] = {}
    appends: dict[str, list[ast.expr]] = {}
    parser_reads: dict[str, ast.expr] = {}
    loops: list[ast.For] = []
    for top in body_nodes:
        for node in ast.walk(top):
            if isinstance(node, ast.Assign):
                for target in node.targets:
                    if isinstance(target, ast.Name):
                        assigns.setdefault(target.id, []).append(node.value)
            elif isinstance(node, (ast.AnnAssign, ast.AugAssign)) and isinstance(node.target, ast.Name) \
                    and node.value is not None:
                assigns.setdefault(node.target.id, []).append(node.value)
            elif isinstance(node, (ast.For, ast.AsyncFor)):
                loops.append(node)
            elif isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute) \
                    and isinstance(node.func.value, ast.Name) and node.args:
                if node.func.attr == "read":
                    parser_reads.setdefault(node.func.value.id, node.args[0])
                elif node.func.attr == "append":
                    appends.setdefault(node.func.value.id, []).append(node.args[0])
    for loop in loops:
        elements = _iter_elements(loop.iter, assigns, appends)
        if isinstance(loop.target, ast.Name):
            assigns.setdefault(loop.target.id, []).extend(elements)
        elif isinstance(loop.target, ast.Tuple):
            for position, member in enumerate(loop.target.elts):
                if not isinstance(member, ast.Name):
                    continue
                for element in elements:
                    if isinstance(element, ast.Tuple) and len(element.elts) == len(loop.target.elts):
                        assigns.setdefault(member.id, []).append(element.elts[position])
    return _PyScope(params, assigns, parser_reads)


def _function_params(func: "ast.FunctionDef | ast.AsyncFunctionDef") -> frozenset[str]:
    args = func.args
    names = [a.arg for a in args.posonlyargs + args.args + args.kwonlyargs]
    names += [a.arg for a in (args.vararg, args.kwarg) if a is not None]
    return frozenset(names)


def _name_kind(text: str) -> "str | None":
    """``kanban`` / ``project`` when *text* names that cfg file, else None."""
    if _KANBAN_FILE_RE.search(text):
        return "kanban"
    if _PROJECT_CFG_NAME_RE.search(text):
        return "project"
    return None


def _py_cfg_kinds(expr: ast.expr, scope: _PyScope, module: _PyScope, depth: int = 0) -> set[str]:
    """Return the files a Python path expression may name: a subset of ``kanban``, ``project``, ``either``.

    A string, variable, attribute or called function's name naming
    kanban.cfg/project.cfg decides it; a variable is followed through its
    assignments (``for`` targets included) in *scope*, then at module level,
    three levels deep. A tiered reader whose path variable takes both files
    names both. A bare parameter of the enclosing function that names
    neither file is ``either``; an expression naming neither is ``set()``.
    """
    kinds: set[str] = set()
    param = False
    for node in ast.walk(expr):
        text = ""
        if isinstance(node, ast.Constant) and isinstance(node.value, str):
            text = node.value
        elif isinstance(node, ast.Attribute):
            text = node.attr
        elif isinstance(node, ast.Name):
            text = node.id
            if depth < 3:
                for value in scope.assigns.get(node.id) or module.assigns.get(node.id, []):
                    kinds |= _py_cfg_kinds(value, scope, module, depth + 1)
            if node.id in scope.params and expr is node:
                param = True
        kind = _name_kind(text)
        if kind:
            kinds.add(kind)
    if kinds - {"either"}:
        return kinds - {"either"}
    return {"either"} if param or kinds else set()


def _py_literal(expr: ast.expr, scope: _PyScope, module: _PyScope) -> "str | None":
    """The string *expr* is: a literal, or a name assigned one literal in *scope* or at module level."""
    if isinstance(expr, ast.Constant) and isinstance(expr.value, str):
        return expr.value
    if isinstance(expr, ast.Name):
        values = scope.assigns.get(expr.id) or module.assigns.get(expr.id, [])
        strings = {v.value for v in values if isinstance(v, ast.Constant) and isinstance(v.value, str)}
        if len(strings) == 1 and len(values) == 1:
            return strings.pop()
    return None


def _parser_path(receiver: ast.expr, scope: _PyScope) -> "ast.expr | None":
    """The PATH a parser variable was loaded from in *scope*, or the parameter itself."""
    if not isinstance(receiver, ast.Name):
        return None
    for value in scope.assigns.get(receiver.id, []):
        if isinstance(value, ast.Call):
            name = value.func.attr if isinstance(value.func, ast.Attribute) else getattr(value.func, "id", "")
            if name in _PY_PARSER_LOADERS and value.args:
                return value.args[0]
            if name in _PY_PARSER_CLASSES and receiver.id in scope.parser_reads:
                return scope.parser_reads[receiver.id]
    if receiver.id in scope.params:
        return receiver
    return None


def scan_python(text: str, rel: str) -> list[ReadSite]:
    """Return the kanban.cfg / project.cfg read call sites in Python *text*.

    Each site is tied to its own call: see the module docstring's "WHAT
    COUNTS AS A READ". A module that does not parse yields ``[]``.
    """
    try:
        tree = ast.parse(text)
    except (SyntaxError, ValueError):
        return []
    module = _scope_names(
        [n for n in tree.body if not isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef))],
        frozenset(),
    )
    scopes: list[tuple[_PyScope, list[ast.AST]]] = [(module, [
        n for n in tree.body if not isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef))
    ])]
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            scopes.append((_scope_names(node.body, _function_params(node)), node.body))
    sites: dict[tuple[str, str, int, str], ReadSite] = {}
    for scope, body in scopes:
        for top in body:
            for node in ast.walk(top):
                if not isinstance(node, ast.Call):
                    continue
                func = node.func
                name = func.attr if isinstance(func, ast.Attribute) else getattr(func, "id", "")
                path: "ast.expr | None" = None
                if name in _PY_PATH_READERS and len(node.args) >= 3:
                    path, section_node, key_node = node.args[0], node.args[1], node.args[2]
                elif name in _PY_PARSER_GETTERS and isinstance(func, ast.Attribute) and len(node.args) >= 2:
                    path = _parser_path(func.value, scope)
                    section_node, key_node = node.args[0], node.args[1]
                if path is None:
                    continue
                section = _py_literal(section_node, scope, module)
                key = _py_literal(key_node, scope, module)
                if section is None or key is None:
                    continue
                for kind in _py_cfg_kinds(path, scope, module):
                    sites.setdefault(
                        (section, key, node.lineno, kind), ReadSite(section, key, rel, node.lineno, kind)
                    )
    return sorted(sites.values(), key=lambda s: (s.line, s.section, s.key, s.cfg))


# ---------------------------------------------------------------------------
# The cross-check
# ---------------------------------------------------------------------------


def _scanned_files(root: Path) -> list[Path]:
    files: list[Path] = []
    top = root / SCAN_TOP
    if top.is_dir():
        for path in sorted(top.rglob("*")):
            if path.suffix not in (".sh", ".py") or not path.is_file():
                continue
            if any(part in TEST_DIR_NAMES for part in path.relative_to(top).parts[:-1]):
                continue
            if path.relative_to(root).as_posix() == SELF_REL:
                continue
            files.append(path)
    for name in ROOT_SCRIPTS:
        if (root / name).is_file():
            files.append(root / name)
    return files


def _dynamic_reads(site: DynamicSite, code: str, candidates: set[tuple[str, str]]) -> set[tuple[str, str]]:
    """Return the *candidates* a dynamic site reads, by the word-evidence rule."""
    hits: set[tuple[str, str]] = set()
    for section, key in candidates:
        section_match = site.section_re.fullmatch(section)
        key_match = site.key_re.fullmatch(key) if section_match else None
        if not key_match:
            continue
        groups = [g for g in section_match.groups() + key_match.groups() if g]
        if all(re.search(rf"(?<![{_WORD_CHARS}]){re.escape(g)}(?![{_WORD_CHARS}])", code) for g in groups):
            hits.add((section, key))
    return hits


def _call_args(name: str, corpus: list[tuple[str, int, str]], count: int) -> list[tuple[str, int, list[str]]]:
    """Return ``(file, line, words)`` for every call of shell function *name* in *corpus*."""
    call = re.compile(rf"(?<![\w.:/-]){re.escape(name)}(?![\w.:/-])\s+(?!\()")
    out: list[tuple[str, int, list[str]]] = []
    for rel, number, line in corpus:
        if _FUNC_DEF_RE.match(line):
            # A one-line definition ("f() { g x y; }") calls on its body.
            if "{" not in line:
                continue
            line = line[line.index("{") + 1:]
        for match in call.finditer(line):
            out.append((rel, number, shell_words(line[match.end():], count)))
    return out


def caller_reads(
    site: DynamicSite, corpus: list[tuple[str, int, str]], candidates: set[tuple[str, str]],
) -> tuple[list[ReadSite], set[tuple[str, str]]]:
    """Resolve a site whose section or key is a positional parameter, through its callers.

    A call passing literal values for every positional side yields a literal
    ``ReadSite`` at the call; a call fixing one side while the other is a
    pattern marks the matching *candidates* read. A call passing a variable
    for a positional side resolves nothing.
    """
    assert site.function is not None
    count = max(site.section_pos or 0, site.key_pos or 0)
    sites: list[ReadSite] = []
    keys: set[tuple[str, str]] = set()
    for rel, number, words in _call_args(site.function, corpus, count):
        values: list["str | None"] = []
        for pos, fixed in ((site.section_pos, site.section_literal), (site.key_pos, site.key_literal)):
            if pos is None:
                values.append(fixed)
                continue
            word = unquote(words[pos - 1]) if len(words) >= pos else ""
            values.append(word if word and "$" not in word else "")
        section, key = values
        if section == "" or key == "":
            continue
        if section is not None and key is not None:
            sites.append(ReadSite(section, key, rel, number, site.cfg))
            continue
        keys.update(
            (s, k) for s, k in candidates
            if (s == section if section is not None else site.section_re.fullmatch(s))
            and (k == key if key is not None else site.key_re.fullmatch(k))
        )
    return sites, keys


class Result(NamedTuple):
    """Everything one run learned: scan size, the read key sets, and the findings.

    *kanban_read_keys* are the keys read from kanban.cfg; *read_keys* adds the
    keys read from project.cfg, from another INI file in a shared section,
    and through a Python path parameter. *project_read_keys* are the keys
    read from project.cfg, path-parameter reads included. *notes* are the
    one-line narrations of checks that did not run.
    """

    files_scanned: int
    kanban_read_keys: set[tuple[str, str]]
    read_keys: set[tuple[str, str]]
    literal_sites: list[ReadSite]
    findings: list[Finding]
    project_read_keys: "set[tuple[str, str]] | None" = None
    notes: "list[str] | None" = None


def _edit_remedy(rel: str, line: int) -> str:
    return f"${{EDITOR:-vi}} +{line} {rel}"


# ---------------------------------------------------------------------------
# Templates: entries, comment blocks, stated defaults
# ---------------------------------------------------------------------------


class TemplateEntry(NamedTuple):
    """One key line in a template: active ``key = value`` or commented ``# key = value``."""

    section: str
    key: str
    line: int  # 1-based
    commented: bool
    value: str


def template_entries(lines: list[str]) -> list[TemplateEntry]:
    """Every key line under a section header in *lines*, in file order."""
    entries: list[TemplateEntry] = []
    section = ""
    for number, line in enumerate(lines, start=1):
        match = _SECTION_RE.match(line)
        if match:
            section = match.group(1).strip()
            continue
        if not section:
            continue
        active = _ACTIVE_KEY_RE.match(line)
        if active:
            entries.append(TemplateEntry(section, active.group(1), number, False, line.split("=", 1)[1].strip()))
            continue
        commented = _TEMPLATE_COMMENTED_KEY_RE.match(line)
        if commented:
            entries.append(TemplateEntry(section, commented.group(1), number, True, commented.group(2) or ""))
    return entries


def comment_block(lines: list[str], line: int) -> list[str]:
    """The comment lines documenting the key on 1-based *line*, top to bottom.

    Walks up over blank lines and sibling key lines (a group of keys sharing
    one comment), then collects the contiguous comment run, stopping at the
    section header.
    """
    block: list[str] = []
    index = line - 2
    while index >= 0:
        text = lines[index]
        if _SECTION_RE.match(text):
            break
        is_comment = text.lstrip().startswith(("#", ";")) and not _COMMENTED_KEY_RE.match(text)
        if is_comment:
            block.append(text)
        elif block:
            break
        index -= 1
    return list(reversed(block))


def normalize_default(value: str) -> str:
    """A stated or registry default reduced to the value alone, for comparison.

    Drops a leading ``(``, cuts at the first parenthetical, clause or
    sentence break, strips one layer of quotes or backticks, and reads the
    "no value" spellings (``empty``, ``unset``, ``absent``, ...) as ``""``.
    """
    v = value.strip()
    if v.startswith("("):
        v = v[1:].strip()
    v = _DEFAULT_END_RE.split(v, 1)[0].strip().rstrip(".").strip()
    if len(v) >= 2 and v[0] == v[-1] and v[0] in "\"'`":
        v = v[1:-1].strip()
    return "" if v.casefold() in _EMPTY_DEFAULTS else v


class StatedDefault(NamedTuple):
    """What a key's comment block says about its default.

    *form* is ``default``, ``inherits``, ``shipped``, ``none`` (no in-code
    default) or ``""`` (nothing stated). *value* is the comparable value, or
    None when the form states none.
    """

    form: str
    value: "str | None"


def stated_default(block: list[str], key: str) -> StatedDefault:
    """Read the default *block* states for *key* (see the module docstring's ``missing_default``)."""
    text = [line.lstrip("#; \t") for line in block]
    stated_raw: "str | None" = None
    for index, line in enumerate(text):
        match = _DEFAULT_RE.search(line)
        if match is None and _DEFAULT_WORD_RE.search(line) and index + 1 < len(text):
            # "... Default when" wrapped onto the next line before its colon.
            match = _DEFAULT_RE.search(f"{line} {text[index + 1]}")
        if match:
            stated_raw = match.group(1).strip()  # the last stated default wins
    if stated_raw is not None:
        if _NO_DEFAULT_RE.search(stated_raw):
            return StatedDefault("none", None)
        per_key = re.search(rf"(?<![\w]){re.escape(key)}\s*[:=]?\s+([^\s,;]+)", stated_raw)
        if per_key:
            return StatedDefault("default", normalize_default(per_key.group(1)))
        value = normalize_default(stated_raw)
        # Three or more words is a description ("the value of
        # max_runtime_seconds"), not a value to compare.
        return StatedDefault("default", value if len(value.split()) <= 2 else None)
    for line in text:
        match = _INHERITS_RE.search(line)
        if match:
            last = match.group(1).rsplit(">", 1)[-1].strip()
            if ">" not in match.group(1) or _KANBAN_FILE_RE.search(last) or _PROJECT_CFG_NAME_RE.search(last):
                return StatedDefault("inherits", None)
            return StatedDefault("inherits", normalize_default(last))
    if any(_SHIPPED_RE.search(line) for line in text):
        return StatedDefault("shipped", None)
    if any(_NO_DEFAULT_RE.search(line) for line in text):
        return StatedDefault("none", None)
    return StatedDefault("", None)


class TemplateView(NamedTuple):
    """One template's text as the checks read it: where findings point, and its entries."""

    display: str  # how findings name it
    file: str  # the file a finding's remedy opens
    lines: list[str]
    entries: list[TemplateEntry]
    remedy_line: "int | None" = None  # a fixed line to open, for rendered text


def _registry_entries(path: Path, display: str) -> dict:
    """The Key Registry comment block of *path* as ``{(section, key): RegistryKey}``; ``{}`` when absent."""
    try:
        return _doc_truth._parse_config_loader_registry(path, display)
    except (OSError, _doc_truth.RegistryFormatError):
        return {}


def check_template_defaults(view: TemplateView, registry: dict) -> list[Finding]:
    """``missing_default``, ``default_mismatch`` and ``commented_value_not_default`` for one template."""
    findings: list[Finding] = []
    seen: set[tuple[str, str]] = set()
    for entry in view.entries:
        if (entry.section, entry.key) in seen:
            continue
        seen.add((entry.section, entry.key))
        where = f"{view.display}:{entry.line}"
        remedy = _edit_remedy(view.file, view.remedy_line or entry.line)
        stated = stated_default(comment_block(view.lines, entry.line), entry.key)
        reg = registry.get((entry.section, entry.key))
        if not stated.form:
            findings.append(Finding(
                "missing_default", entry.section, entry.key, view.file, view.remedy_line or entry.line,
                f"[{entry.section}] {entry.key} at {where} has no comment stating its default; add a "
                "'Default: X' line (or 'Shipped value:', 'Inherits ...', 'No in-code default')",
                remedy,
            ))
        registry_default = normalize_default(reg.default) if reg is not None and reg.default_checked else None
        if stated.value is not None and registry_default is not None \
                and stated.value.casefold() != registry_default.casefold():
            findings.append(Finding(
                "default_mismatch", entry.section, entry.key, view.file, view.remedy_line or entry.line,
                f"[{entry.section}] {entry.key} at {where} states default {stated.value!r} but "
                f"{reg.source_display}'s Key Registry records {registry_default!r}; correct whichever is wrong",
                remedy,
            ))
        if entry.commented:
            if reg is not None:
                expected = registry_default
            else:
                expected = stated.value
            value = normalize_default(entry.value)
            # An empty commented value ("# key =") shows no value at all,
            # so it cannot mislead about the one in force.
            if expected is not None and entry.value and value.casefold() != expected.casefold():
                findings.append(Finding(
                    "commented_value_not_default", entry.section, entry.key, view.file,
                    view.remedy_line or entry.line,
                    f"[{entry.section}] {entry.key} at {where} is commented as {entry.value!r}, but a "
                    f"commented line shows the value in force and the default is {expected!r}; show the "
                    "default there and move the override into the comment as an example",
                    remedy,
                ))
    return findings


def misplaced_keys(
    sites: list[ReadSite], present: "set[tuple[str, str]]", entries: list[TemplateEntry],
) -> dict[tuple[str, str], tuple[ReadSite, TemplateEntry]]:
    """Reads whose key a template carries only under another section.

    Returns ``{(read section, key): (first read site, misplaced template entry)}``.
    """
    by_key: dict[str, list[TemplateEntry]] = {}
    for entry in entries:
        by_key.setdefault(entry.key, []).append(entry)
    out: dict[tuple[str, str], tuple[ReadSite, TemplateEntry]] = {}
    for site in sites:
        slot = (site.section, site.key)
        if slot in present or slot in out:
            continue
        elsewhere = [e for e in by_key.get(site.key, []) if e.section != site.section]
        if elsewhere:
            out[slot] = (site, elsewhere[0])
    return out


def _wrong_section_finding(view: TemplateView, site: ReadSite, entry: TemplateEntry) -> Finding:
    return Finding(
        "wrong_section", entry.section, entry.key, view.file, view.remedy_line or entry.line,
        f"{entry.key} sits under [{entry.section}] at {view.display}:{entry.line}, but its reader "
        f"({site.file}:{site.line}) reads [{site.section}] {site.key}; move it under a [{site.section}] header",
        _edit_remedy(view.file, view.remedy_line or entry.line),
    )


# ---------------------------------------------------------------------------
# create_project output
# ---------------------------------------------------------------------------


def render_project_cfg_text(root: Path) -> tuple["str | None", str]:
    """Render the project.cfg ``create_project`` writes, in a child interpreter.

    The child imports the scanned tree's own generator, runs with a fresh
    temp dir as its working and temporary directory, and writes no bytecode,
    so the render touches nothing outside that temp dir. Returns ``(text,
    "")`` on success and ``(None, reason)`` on failure.
    """
    env = {k: v for k, v in os.environ.items() if k not in ("PYTHONPATH", "PYTHONSTARTUP")}
    temp_parent = os.environ.get("PGAI_AGENT_KANBAN_TEMP_DIR") or None
    if temp_parent and not Path(temp_parent).is_dir():
        temp_parent = None
    with tempfile.TemporaryDirectory(prefix="config-key-render-", dir=temp_parent) as scratch:
        env.update({"PYTHONDONTWRITEBYTECODE": "1", "TMPDIR": scratch, "HOME": scratch})
        try:
            proc = subprocess.run(
                [sys.executable, "-c", _RENDER_PROGRAM, str(root / SCAN_TOP), RENDER_PROJECT_NAME],
                capture_output=True, text=True, timeout=RENDER_TIMEOUT_SECONDS, cwd=scratch, env=env,
            )
        except (OSError, subprocess.TimeoutExpired) as exc:
            return None, f"could not run: {exc}"
    if proc.returncode != 0:
        tail = (proc.stderr.strip().splitlines() or ["(no stderr)"])[-1]
        return None, f"exited {proc.returncode}: {tail}"
    return proc.stdout, ""


def _definition_line(path: Path, name: str) -> int:
    """1-based line of ``def <name>`` in *path*, or 1."""
    try:
        for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
            if re.match(rf"\s*def\s+{re.escape(name)}\b", line):
                return number
    except OSError:
        pass
    return 1


# ---------------------------------------------------------------------------
# Exports nothing consumes
# ---------------------------------------------------------------------------


def unconsumed_exports(
    sites: list[ReadSite], non_export_keys: "set[tuple[str, str]]", texts: dict[str, list[str]],
) -> list[tuple[ReadSite, str]]:
    """Keys whose every read is an export no scanned code mentions again.

    *texts* maps each scanned file to its code lines (comments dropped for
    shell). Returns ``(first export site, variable)`` per such key.
    """
    exports: dict[tuple[str, str], list[ReadSite]] = {}
    for site in sites:
        if site.export:
            exports.setdefault((site.section, site.key), []).append(site)
    out: list[tuple[ReadSite, str]] = []
    for slot, export_sites in sorted(exports.items()):
        if slot in non_export_keys:
            continue
        defining = {(s.file, s.line) for s in export_sites}
        variables = {s.export for s in export_sites}
        used = False
        for variable in variables:
            word = re.compile(rf"(?<![\w]){re.escape(variable)}(?![\w])")
            for rel, lines in texts.items():
                for number, code in enumerate(lines, start=1):
                    if (rel, number) in defining:
                        continue
                    if word.search(code):
                        used = True
                        break
                if used:
                    break
            if used:
                break
        if not used:
            out.append((export_sites[0], sorted(variables)[0]))
    return out


# ---------------------------------------------------------------------------
# The run
# ---------------------------------------------------------------------------


def _project_example_path(root: Path) -> "Path | None":
    for rel in PROJECT_EXAMPLE_RELS:
        if (root / rel).is_file():
            return root / rel
    return None


def _read_lines(path: Path) -> list[str]:
    try:
        return path.read_text(encoding="utf-8", errors="replace").splitlines()
    except OSError:
        return []


def run(root: Path) -> Result:
    """Scan the tree under *root* and cross-check the read, scaffolded and registered key sets."""
    example_path = root / EXAMPLE_REL
    registry_path = root / REGISTRY_REL
    example = parse_example(example_path)
    sections_at = example_section_lines(example_path)
    registry, deprecated = parse_registry(registry_path)
    candidates = set(example) | set(registry)
    kanban_sections = {s for s, _ in candidates}
    kanban_entries = _registry_entries(registry_path, REGISTRY_REL)

    project_example_path = _project_example_path(root)
    project_example_rel = project_example_path.relative_to(root).as_posix() if project_example_path else ""
    project_example: dict[tuple[str, str], int] = {}
    for entry in template_entries(_read_lines(project_example_path) if project_example_path else []):
        project_example.setdefault((entry.section, entry.key), entry.line)
    project_entries = _registry_entries(root / PROJECT_REGISTRY_REL, PROJECT_REGISTRY_REL)
    project_candidates = set(project_example) | set(project_entries)
    project_sections = {s for s, _ in project_candidates} - kanban_sections
    project_candidates |= {(s, k) for s, k in kanban_entries if s in {s2 for s2, _ in project_example}}

    registry_span = None
    try:
        reg_lines = registry_path.read_text(encoding="utf-8", errors="replace").splitlines()
        start, end = _registry_array_span(reg_lines)
        if start >= 0:
            registry_span = (start + 1, end + 1)
    except OSError:
        pass

    files = _scanned_files(root)
    literal_sites: list[ReadSite] = []
    kanban_read_keys: set[tuple[str, str]] = set()
    project_read_keys: set[tuple[str, str]] = set()
    dynamic_keys: set[tuple[str, str]] = set()
    corpus: list[tuple[str, int, str]] = []
    positional: list[DynamicSite] = []
    texts: dict[str, list[str]] = {}
    for path in files:
        rel = path.relative_to(root).as_posix()
        try:
            text = path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        if path.suffix == ".py":
            texts[rel] = text.splitlines()
            literal_sites.extend(scan_python(text, rel))
            continue
        span = registry_span if rel == REGISTRY_REL else None
        scan = scan_shell(text, rel, kanban_sections, span, project_sections)
        literal_sites.extend(scan.literal)
        code_by_line = dict(scan.lines)
        texts[rel] = [code_by_line.get(n, "") for n in range(1, len(text.splitlines()) + 1)]
        corpus.extend((rel, number, line) for number, line in scan.lines)
        for site in scan.dynamic:
            if site.function:
                positional.append(site)
                continue
            hits = _dynamic_reads(site, scan.code, candidates if site.cfg == "kanban" else project_candidates)
            (kanban_read_keys if site.cfg == "kanban" else project_read_keys).update(hits)
            dynamic_keys |= hits
    for site in positional:
        sites, keys = caller_reads(site, corpus, candidates if site.cfg == "kanban" else project_candidates)
        literal_sites.extend(sites)
        (kanban_read_keys if site.cfg == "kanban" else project_read_keys).update(keys)
        dynamic_keys |= keys
    kanban_read_keys.update((s.section, s.key) for s in literal_sites if s.cfg == "kanban")
    project_read_keys.update((s.section, s.key) for s in literal_sites if s.cfg in ("project", "either"))
    read_keys = kanban_read_keys | project_read_keys | {(s.section, s.key) for s in literal_sites}

    findings: list[Finding] = []
    notes: list[str] = []

    # --- kanban.cfg: placement, then the three original kinds ---------------
    kanban_sites = [s for s in literal_sites if s.cfg == "kanban"]
    kanban_lines = _read_lines(example_path)
    kanban_view = TemplateView(EXAMPLE_REL, EXAMPLE_REL, kanban_lines, template_entries(kanban_lines))
    kanban_misplaced = misplaced_keys(kanban_sites, set(example), kanban_view.entries)
    for site, entry in kanban_misplaced.values():
        findings.append(_wrong_section_finding(kanban_view, site, entry))
    misplaced_entries = {(e.section, e.key) for _s, e in kanban_misplaced.values()}

    first_site: dict[tuple[str, str], ReadSite] = {}
    for site in kanban_sites:
        first_site.setdefault((site.section, site.key), site)
    for (section, key), site in sorted(first_site.items()):
        if (section, key) in example or (section, key) in deprecated or (section, key) in kanban_misplaced:
            continue
        edit_line = sections_at.get(section, 1)
        where = f"under [{section}]" if section in sections_at else f"in a new [{section}] section"
        findings.append(Finding(
            "read_not_scaffolded", section, key, site.file, site.line,
            f"[{section}] {key} is read from kanban.cfg here but {EXAMPLE_REL} does not "
            f"scaffold it; add a comment naming its default and a '{key} = ' line {where}",
            _edit_remedy(EXAMPLE_REL, edit_line),
        ))
    for (section, key), line in sorted(example.items(), key=lambda item: item[1]):
        if (section, key) not in read_keys and (section, key) not in misplaced_entries:
            findings.append(Finding(
                "example_not_read", section, key, EXAMPLE_REL, line,
                f"[{section}] {key} is scaffolded in {EXAMPLE_REL} but no code reads it; "
                "remove it, or wire the reader its comment names",
                _edit_remedy(EXAMPLE_REL, line),
            ))
    for (section, key), line in sorted(registry.items(), key=lambda item: item[1]):
        if (section, key) not in read_keys:
            findings.append(Finding(
                "registry_not_read", section, key, REGISTRY_REL, line,
                f"{section}:{key} is in {REGISTRY_ARRAY} but no code reads it; remove it from "
                "the array and the Key Registry comment, or wire its reader",
                _edit_remedy(REGISTRY_REL, line),
            ))
    findings.extend(check_template_defaults(kanban_view, kanban_entries))

    # --- project.cfg ---------------------------------------------------------
    if project_example_path is None:
        notes.append(f"no {PROJECT_EXAMPLE_RELS[0]} under {root}; project.cfg checks skipped")
    else:
        merged_entries = dict(kanban_entries)
        merged_entries.update(project_entries)
        project_lines = _read_lines(project_example_path)
        project_view = TemplateView(project_example_rel, project_example_rel, project_lines,
                                    template_entries(project_lines))
        project_sites = [s for s in literal_sites if s.cfg == "project"]
        project_misplaced = misplaced_keys(project_sites, set(project_example), project_view.entries)
        for site, entry in project_misplaced.values():
            findings.append(_wrong_section_finding(project_view, site, entry))
        project_misplaced_entries = {(e.section, e.key) for _s, e in project_misplaced.values()}
        template_findings = check_template_defaults(project_view, merged_entries)
        findings.extend(template_findings)

        generated: "set[tuple[str, str]] | None" = None
        generator_path = root / GENERATOR_REL
        generator_line = _definition_line(generator_path, GENERATOR_FUNCTION)
        if not generator_path.is_file():
            notes.append(f"no {GENERATOR_REL} under {root}; create_project output not compared")
        else:
            rendered, reason = render_project_cfg_text(root)
            if rendered is None:
                findings.append(Finding(
                    "project_cfg_render_failed", "", "", GENERATOR_REL, generator_line,
                    f"{GENERATOR_FUNCTION} in {GENERATOR_REL} could not render a project.cfg ({reason}); "
                    "the create_project output was not compared",
                    f"python3 {SELF_REL}",
                ))
            else:
                generated_lines = rendered.splitlines()
                generated_view = TemplateView(
                    GENERATOR_DISPLAY, GENERATOR_REL, generated_lines, template_entries(generated_lines),
                    remedy_line=generator_line,
                )
                generated = {(e.section, e.key) for e in generated_view.entries}
                already = {(f.kind, f.section, f.key) for f in template_findings}
                for finding in check_template_defaults(generated_view, merged_entries):
                    if (finding.kind, finding.section, finding.key) not in already:
                        findings.append(finding)
                generated_misplaced = misplaced_keys(project_sites, generated, generated_view.entries)
                for slot, (site, entry) in generated_misplaced.items():
                    if slot not in project_misplaced:
                        findings.append(_wrong_section_finding(generated_view, site, entry))
                project_misplaced = {**generated_misplaced, **project_misplaced}

        first_project_site: dict[tuple[str, str], ReadSite] = {}
        for site in project_sites:
            first_project_site.setdefault((site.section, site.key), site)
        for (section, key), site in sorted(first_project_site.items()):
            if (section, key) in project_misplaced:
                continue
            missing = []
            if (section, key) not in project_example:
                missing.append(project_example_rel)
            if generated is not None and (section, key) not in generated:
                missing.append(f"the {GENERATOR_DISPLAY} ({GENERATOR_FUNCTION} in {GENERATOR_REL})")
            if not missing:
                continue
            fix_file, fix_line = (
                (project_example_rel, example_section_lines(project_example_path).get(section, 1))
                if (section, key) not in project_example else (GENERATOR_REL, generator_line)
            )
            findings.append(Finding(
                "project_cfg_read_not_scaffolded", section, key, site.file, site.line,
                f"[{section}] {key} is read from project.cfg here but {' and '.join(missing)} "
                f"does not carry it; add it under [{section}] with a comment naming its default",
                _edit_remedy(fix_file, fix_line),
            ))
        for (section, key), line in sorted(project_example.items(), key=lambda item: item[1]):
            if (section, key) in project_read_keys or (section, key) in project_misplaced_entries:
                continue
            findings.append(Finding(
                "project_cfg_example_not_read", section, key, project_example_rel, line,
                f"[{section}] {key} is scaffolded in {project_example_rel} but nothing reads it from "
                "project.cfg; remove it, or wire the reader its comment names",
                _edit_remedy(project_example_rel, line),
            ))

    # --- reads that only feed an export nothing consumes ----------------------
    non_export_keys = {(s.section, s.key) for s in literal_sites if not s.export} | dynamic_keys
    for site, variable in unconsumed_exports(literal_sites, non_export_keys, texts):
        findings.append(Finding(
            "read_but_unconsumed", site.section, site.key, site.file, site.line,
            f"[{site.section}] {site.key} is read only to export {variable}, which no scanned code "
            f"mentions anywhere else; remove the export (and the key, if nothing else needs it) or "
            f"wire the reader of {variable}",
            _edit_remedy(site.file, site.line),
        ))

    return Result(len(files), kanban_read_keys, read_keys, literal_sites, findings, project_read_keys, notes)


def main(argv: "list[str] | None" = None) -> int:
    """Run the config-key coverage lint, printing each finding and writing ``--findings-file``.

    ``--list-read`` prints the keys read from kanban.cfg instead. Exit codes: 0 always, whatever was found (quality class); 2 usage error (argparse).
    """
    parser = argparse.ArgumentParser(
        prog="lint_config_key_coverage.py",
        description=(
            "Report kanban.cfg and project.cfg keys read by the code but not scaffolded "
            "in their templates (or, for project.cfg, in the text create_project writes), "
            "scaffolded or registered keys no code reads, stated defaults that disagree "
            "with the Key Registries, keys under the wrong section, commented lines that "
            "do not show the default, and keys read only for an export nothing consumes "
            "(quality class; always exits 0)."
        ),
    )
    parser.add_argument(
        "--root",
        default=str(Path(__file__).resolve().parent.parent.parent),
        help="repository root holding kanban.cfg_example and team/ (default: this checkout)",
    )
    parser.add_argument("--findings-file", default=None, help="write findings as a JSON array here")
    parser.add_argument("--list-read", action="store_true",
                        help="print every section:key read from kanban.cfg and exit")
    args = parser.parse_args(argv)

    root = Path(args.root).resolve()
    if not (root / EXAMPLE_REL).is_file():
        print(f"lint_{LINT_NAME}: no {EXAMPLE_REL} under {root}; nothing to check "
              f"(pass --root with the repository root)")
        return 0
    result = run(root)

    if args.list_read:
        for section, key in sorted(result.kanban_read_keys):
            print(f"{section}:{key}")
        return 0

    for note in result.notes or []:
        print(f"lint_{LINT_NAME}: note: {note}")
    for finding in result.findings:
        print(f"lint_{LINT_NAME}: {finding.file}:{finding.line}: {finding.kind}: {finding.summary}")
    print(f"lint_{LINT_NAME}: totals: {result.files_scanned} file(s) scanned; "
          f"{len(result.read_keys)} key(s) read; {len(result.findings)} finding(s)"
          + (" (non-blocking finding)" if result.findings else ""))
    if result.findings:
        print("\nTo fix:")
        print(f"  1. cd {root}")
        step = 2
        for remedy in dict.fromkeys(f.remedy for f in result.findings):
            print(f"  {step}. {remedy}")
            step += 1
        print(f"  {step}. python3 {SELF_REL}")
        print(f"\nRule: {RULE}")

    if args.findings_file:
        out = Path(args.findings_file)
        try:
            out.parent.mkdir(parents=True, exist_ok=True)
            out.write_text(json.dumps([f.to_dict() for f in result.findings], indent=2) + "\n",
                           encoding="utf-8")
        except OSError as exc:
            print(f"lint_{LINT_NAME}: could not write findings file {out}: {exc}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
