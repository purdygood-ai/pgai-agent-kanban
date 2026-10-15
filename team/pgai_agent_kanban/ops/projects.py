"""
projects.py — the project registry verbs: create_project, add_project and
remove_project.

One implementation behind ``team/scripts/create-project.sh``,
``add-project.sh`` and ``remove-project.sh`` (which reach these functions
through ``pp_run_ops``) and ``POST /operations/create-project``,
``/add-project`` and ``/remove-project`` (which call them in process).

The scripts keep flag parsing, ``--help`` and the kanban-root check; every
value check, the plan they print, and every write happen here, so the CLI
and the REST route refuse alike and leave the same bytes on disk.

Input confinement
-----------------
Every verb checks its project name before it touches the filesystem:
``create_project`` against ``[a-zA-Z][a-zA-Z0-9_-]*`` (its long-standing
rule), ``add_project`` and ``remove_project`` against
:func:`check_project_name` -- one plain name
(``api.validation.require_component``: no ``/``, ``..``, control character,
glob character or leading ``-``) made only of the letters, digits, ``_``
and ``-`` a ``[project:<name>]`` registry section can hold. So ``..`` or
``../x`` never reaches ``projects/<name>``, ``shutil.rmtree`` or
projects.cfg.

``create_project`` also runs :func:`check_create_values` before anything
is read or written: ``workflow_type`` must be one plain name -- it is then
looked up by name among the ready plugins, never joined into a path first
-- and ``git_remote``, ``dev_tree`` and ``git_repo``, which are written into
project.cfg, may hold no newline or other control character (no injected
section or key); ``dev_tree`` must also be absolute with no ``..`` segment.
These refusals raise :class:`ProjectValueRefused` carrying the
dual-channel ``<verb>.<field>_invalid`` block, which the REST routes return
as HTTP 422 naming the field.

What each verb does
-------------------
``create_project``
    Validates the ceilings, colour, path fields, name and workflow type,
    prints the plan, and (unless ``dry_run``) lays down
    ``projects/<name>/``: the directory layout, ``project.cfg`` (rendered
    from the project.cfg template, :data:`PROJECT_CFG_TEMPLATE`), the queue
    files, ``overwatch_backlog.md``, the seeded templates and READMEs,
    ``INTENT.md``, ``release-state.md`` and the project ``README.md``
    (from ``templates/project/README-TEMPLATE.md``; the closing "Next
    steps" tell the operator to fill it in); then registers the project in
    projects.cfg, migrating a colon-legacy registry to INI first unless
    ``no_migrate``.

``add_project``
    Registers an existing ``projects/<name>/`` directory, or updates a
    registered project's priority. A project directory with no
    ``README.md`` gets the project README seeded, as ``create_project``
    seeds it, and one with no ``project.cfg`` (nor legacy ``PROJECT.cfg``)
    gets one rendered from the project.cfg template, as ``create_project``
    renders it; an existing file is never overwritten, and nothing else
    under the project directory is touched.

``remove_project``
    Unregisters a project. Without ``force`` the project directory is left
    in place; with ``force`` it is deleted too. A project whose
    release-state.md records an Active RC is refused whether or not
    ``force`` is given, before anything is changed.

The projects.cfg registry
-------------------------
The registry helpers below are the line-for-line Python form of the
readers and editors in ``team/scripts/lib/projects.sh`` and
``projects_cfg.sh`` that these verbs used: format detection (``ini`` /
``colon-legacy``), membership, a project's priority and colour (explicit
or the palette fallback), the next free priority and palette colour, the
colon-to-INI migration, and appending, updating and removing an entry.
They read and write the same bytes the bash helpers do, so a registry the
scripts wrote reads back the same here and the other way round. A
rewritten projects.cfg keeps its permission mode.

Output
------
Each verb prints what its script printed: the plan and the ``+`` progress
lines to stdout, warnings to stderr. A refusal is raised as
:class:`ProjectRefusal` (or :class:`~pgai_agent_kanban.ops.errors.IoError`
for a filesystem failure); :func:`projects_exit_code` gives the exit code
the script exits with for it.

CLI (``python3 -m pgai_agent_kanban.ops <verb> ...``; the verbs in
:data:`CLI_VERBS` are routed here by ``ops/__main__.py``)::

    create_project --kanban-root=ROOT --project=NAME [--scripts-dir=DIR]
                   [--workflow-type=T] [--max-patch=N] [--max-minor=N]
                   [--max-major=N] [--git-remote=NAME] [--priority=P]
                   [--color=C] [--dev-tree=PATH] [--git-repo=URL]
                   [--no-migrate] [--dry-run]
    add_project    --kanban-root=ROOT --project=NAME [--scripts-dir=DIR]
                   [--priority=P] [--color=C] [--no-migrate] [--dry-run]
    remove_project --kanban-root=ROOT --project=NAME [--force] [--dry-run]

An option left out takes the verb's default; ``--dev-tree=`` and
``--git-repo=`` given empty are refused, as the scripts refuse them.
"""

from __future__ import annotations

import argparse
import dataclasses
import datetime
import os
import pathlib
import re
import shlex
import shutil
import sys
from typing import Optional, Union

from pgai_agent_kanban.api.validation import (
    ComponentRefused,
    control_character_fault,
    require_component,
)
from pgai_agent_kanban.cm.read_state_field import (
    ReleaseStateValidationError,
    read_release_state_field,
)
from pgai_agent_kanban.cm.write_rc_state import (
    RELEASE_STATE_FIELDS,
    update_release_state_fields,
)
from pgai_agent_kanban.lib import ini
from pgai_agent_kanban.lib.atomic_write import atomic_write
from pgai_agent_kanban.lib.error_contract import format_error_contract, parse_error_contract
from pgai_agent_kanban.lib.palette import load_default_palette
from pgai_agent_kanban.lib.version_ceiling import CEILING_DEFAULTS
from pgai_agent_kanban.ops.context import OpsContext
from pgai_agent_kanban.ops.errors import IoError, OpsError, Refused

__all__ = [
    "CLI_VERBS",
    "PROJECT_CFG_KEYS",
    "PROJECT_CFG_TEMPLATE",
    "PROJECT_NAME_PLACEHOLDER",
    "ProjectOutcome",
    "ProjectRefusal",
    "ProjectValueRefused",
    "add_project",
    "check_create_values",
    "check_project_name",
    "create_project",
    "dashboard_registry_rows",
    "projects_exit_code",
    "queue_file_text",
    "registered_project_names",
    "remove_project",
    "render_project_cfg",
    "workflow_template_dir",
]

#: The ops CLI verbs this module serves (``ops/__main__.py`` routes them here).
CLI_VERBS: tuple[str, ...] = ("create_project", "add_project", "remove_project")

# This file lives at <tree>/pgai_agent_kanban/ops/projects.py; <tree> is team/
# in the repository and the install root in a live install. Templates,
# workflows and config are found under it -- each tree finds itself.
_OWN_TREE = pathlib.Path(__file__).resolve().parents[2]

#: The project.cfg template: the one copy of every project.cfg key's text,
#: which create_project and add_project render a project's project.cfg from
#: (render_project_cfg). team/config/ in the repository, config/ in a live
#: install; install.sh and upgrade.sh also publish it at the kanban root, and
#: the repository root's project.cfg_example is a link to it.
PROJECT_CFG_TEMPLATE = _OWN_TREE / "config" / "project.cfg_example"

#: A project's config file names, preferred first: what the readers accept
#: (project_paths.sh _pp_project_cfg_file falls back to the legacy name).
_PROJECT_CFG_NAMES = ("project.cfg", "PROJECT.cfg")

#: The placeholder the template writes where a project's name belongs.
PROJECT_NAME_PLACEHOLDER = "<project_name>"

#: Every project.cfg key the kanban reads, as ``(section, key)`` under the
#: section its reader looks in, in the order the template documents them.
#: The template carries each one, active at its default or commented as
#: ``# key = value``, with a comment saying what it does, what it accepts and
#: its default. Readers: team/scripts/lib/project_paths.sh (the pp_*
#: accessors and pp_load_config; its Key Registry comment lists the
#: [project], [versioning], [hooks], [debug] and [training] keys),
#: defer_posture.sh / defer_posture.py (the deferral keys), ops/locks.py,
#: overwatch/resolution_matrix.py, overwatch/block_requeue.py and
#: archive/config.py. A key added to a reader is added here and to the
#: template in the same change.
PROJECT_CFG_KEYS: tuple[tuple[str, str], ...] = (
    ("project", "project_name"),
    ("project", "workflow_type"),
    ("project", "git_remote_name"),
    ("project", "dev_tree_path"),
    ("project", "git_repo_url"),
    ("project", "branch_prefix"),
    ("project", "push_to_remote"),
    ("project", "temp_dir"),
    ("versioning", "max_patch"),
    ("versioning", "max_minor"),
    ("versioning", "max_major"),
    ("versioning", "max_priorities_per_run"),
    ("release", "max_rc_retries"),
    ("release", "deferred_bugs"),
    ("release", "deferred_bug_level"),
    ("release", "defer_piggyback"),
    ("release", "deep_verify"),
    ("release", "deep_verify_offhours_window"),
    ("release", "ship_posture"),
    ("release", "ship_posture_confirm_remote"),
    ("release", "overwatch_resolve"),
    ("locks", "stale_lock_reap_after_wake_periods"),
    ("overwatch", "block_triage"),
    ("overwatch", "requeue_budget"),
    ("archive", "archive_count"),
    ("hooks", "cm_release_pre_squash_hook"),
    ("hooks", "cm_release_pre_squash_hook_required"),
    ("hooks", "cm_release_pre_tag_hook"),
    ("hooks", "cm_release_pre_tag_hook_required"),
    ("hooks", "cm_release_post_tag_hook"),
    ("hooks", "cm_release_post_tag_hook_required"),
    ("debug", "verbose_mode"),
    ("debug", "verbose_agents"),
    ("training", "reasoning_trace"),
    ("training", "training_agents"),
)

# A section header and an active (uncommented) key line, read liberally.
_CFG_SECTION_RE = re.compile(r"^\s*\[\s*([^\]]+?)\s*\]\s*$")
_CFG_ACTIVE_KEY_RE = re.compile(r"^\s*([A-Za-z0-9_.-]+)\s*=")

PathLike = Union[str, "os.PathLike[str]"]
Value = Union[int, str, None]

# Defaults create-project.sh documents for project.cfg.
DEFAULT_WORKFLOW = "release"
DEFAULT_GIT_REMOTE = "origin"
# The version-ceiling defaults come from their one home, lib.version_ceiling.
DEFAULT_MAX_PATCH = str(CEILING_DEFAULTS["max_patch"])
DEFAULT_MAX_MINOR = str(CEILING_DEFAULTS["max_minor"])
DEFAULT_MAX_MAJOR = str(CEILING_DEFAULTS["max_major"])

_CREATE_USAGE = (
    "Usage: create-project.sh --project <name> [--workflow-type <type>] "
    "[--max-minor <N>] [--max-major <N>]"
)
_ADD_USAGE = "Usage: add-project.sh --project <name> [--priority <int>]"
_REMOVE_USAGE = "Usage: remove-project.sh --project <name> [--force] [--dry-run]"

_PROJECT_NAME_RE = re.compile(r"[a-zA-Z][a-zA-Z0-9_-]*")
# The names a [project:<name>] registry section holds (_PROJECT_HEADER_RE's
# class): what add_project and remove_project accept, so every name the
# registry can carry stays nameable while nothing else reaches a path.
_REGISTRY_NAME_RE = re.compile(r"[a-zA-Z0-9_-]+")
_COLOR_RE = re.compile(r"#[0-9A-Fa-f]{6}")
_COLOR_0X_RE = re.compile(r"0[xX][0-9A-Fa-f]{6}")
_COLOR_SHORT_RE = re.compile(r"#[0-9A-Fa-f]{3}")

_NO_MIGRATE_WARNING = (
    "WARNING: projects.cfg is in colon-legacy format and --no-migrate was given.\n"
    "WARNING: The new project will be appended as a colon-format line.\n"
    "WARNING: This produces a mixed-format file that is not a supported stable state.\n"
    "WARNING: Run 'scripts/migrate/projects-cfg.sh' (or re-run without --no-migrate)\n"
    "WARNING: to convert projects.cfg to INI format."
)


# ---------------------------------------------------------------------------
# Results, refusals, output
# ---------------------------------------------------------------------------


@dataclasses.dataclass(frozen=True)
class ProjectOutcome:
    """What one registry verb did.

    Attributes:
        verb:        ``create_project``, ``add_project`` or ``remove_project``.
        project:     The project name.
        project_dir: ``<kanban root>/projects/<project>``.
        dry_run:     True when only the plan was printed.
        changed:     True when anything on disk was written or removed.
        priority:    The registry priority after the call (``""`` when not
                     registered or not reported).
        color:       The registry colour after the call (``""`` likewise).
        deleted:     True when remove_project deleted the project directory.
    """

    verb: str
    project: str
    project_dir: pathlib.Path
    dry_run: bool = False
    changed: bool = False
    priority: str = ""
    color: str = ""
    deleted: bool = False


class ProjectRefusal(Refused):
    """A registry verb refused; ``exit_code`` is the code its script exits with.

    The message is what the script prints to stderr: its plain ``ERROR:``
    lines, or the dual-channel error/message/remedy block.
    """

    def __init__(self, message: str, exit_code: int = 1) -> None:
        super().__init__(message)
        self.exit_code = exit_code


class ProjectValueRefused(ProjectRefusal):
    """A request value that must never reach a path or project.cfg was refused (exit 1).

    ``str(exc)`` is the dual-channel block (``<verb>.<field>_invalid``),
    built through the shared emitter; the REST routes return it as HTTP 422
    naming the field.

    Attributes:
        field: The value's name as the REST body spells it (``project``,
               ``workflow_type``, ``dev_tree``, ``git_repo``, ``git_remote``).
    """

    def __init__(self, field: str, block: str) -> None:
        super().__init__(block, exit_code=1)
        self.field = field

    @property
    def contract(self) -> dict[str, str]:
        """The ``error``/``message``/``remedy`` triple of the block."""
        triple = parse_error_contract(str(self))
        if triple is None:  # pragma: no cover -- every instance carries a block
            raise ValueError(f"ProjectValueRefused without a dual-channel block: {self}")
        return triple


def projects_exit_code(exc: OpsError) -> int:
    """Return the exit code the registry scripts exit with for *exc*.

    A :class:`ProjectRefusal` carries its own code (1 for a usage or value
    error, 2 for an existing/missing project directory or an Active RC);
    every other failure (a filesystem error) exits 1, as the scripts'
    ``set -e`` did. Shared by the ops CLI and the REST routes.
    """
    return getattr(exc, "exit_code", 1)


def _say(*lines: str) -> None:
    """Print narration lines to stdout (the scripts' ``echo``)."""
    for line in lines:
        print(line)


def _warn(*lines: str) -> None:
    """Print lines to stderr, after flushing stdout so a shared stream keeps order."""
    sys.stdout.flush()
    for line in lines:
        print(line, file=sys.stderr)
    sys.stderr.flush()


def _text(value: Value) -> Optional[str]:
    """None stays None; anything else becomes its text form (an int's digits)."""
    return None if value is None else str(value)


# ---------------------------------------------------------------------------
# Line rules the bash registry helpers (awk) read projects.cfg by
# ---------------------------------------------------------------------------

# awk's [[:space:]] -- newline excluded, records are split on it.
_SPACE = " \t\r\f\v"
_COMMENT_RE = re.compile(r"^[ \t\r\f\v]*#")
_BLANK_RE = re.compile(r"^[ \t\r\f\v]*$")
_PROJECT_HEADER_RE = re.compile(r"^\[project:([a-zA-Z0-9_-]+)\]$")
_ANY_SECTION_RE = re.compile(r"^[ \t\r\f\v]*\[")
_FIELD_RE = re.compile(r"^[a-z_]+[ \t\r\f\v]*=")
_LEADING_INT_RE = re.compile(r"^[ \t\r\f\v]*([+-]?[0-9]+)")
# A priority projects_cfg_list sorts by its value: an optional sign, digits.
_PLAIN_INT_RE = re.compile(r"[+-]?[0-9]+")
_INI_SENTINEL = "# Format: INI-style"


def _records(text: str) -> list[str]:
    """Split *text* into awk records: LF-separated, a final LF ends the last one."""
    if not text:
        return []
    lines = text.split("\n")
    if text.endswith("\n"):
        lines.pop()
    return lines


def _render(lines: list[str]) -> str:
    """Join records the way awk ``print`` writes them (every line LF-terminated)."""
    return "".join(f"{line}\n" for line in lines)


def _skippable(line: str) -> bool:
    """True for a comment or blank line, which every registry reader skips."""
    return bool(_COMMENT_RE.match(line) or _BLANK_RE.match(line))


def _trim(value: str) -> str:
    return value.strip(_SPACE)


def _field(line: str) -> tuple[str, str]:
    """Split a ``key = value`` line into its trimmed key and value."""
    key, _sep, value = line.partition("=")
    return _trim(key), _trim(value)


def _awk_int(value: str) -> int:
    """The integer awk's ``+0`` / ``%d`` reads from *value* (its leading digits, else 0)."""
    match = _LEADING_INT_RE.match(value)
    return int(match.group(1)) if match else 0


def _colon_fields(line: str) -> list[str]:
    """awk ``-F:`` fields of *line*."""
    return line.split(":")


# ---------------------------------------------------------------------------
# projects.cfg registry
# ---------------------------------------------------------------------------

# The header a fresh (or migrated) INI projects.cfg starts with; the
# "# Format: INI-style" line in it is what marks an INI registry that has no
# project section yet.
_CANONICAL_HEADER = """\
# projects.cfg — Registry of projects this kanban installation manages.
#
# Format: INI-style with one [project:NAME] section per project.
#
# Fields per section:
#   priority             — integer; lower = higher priority for wake-script iteration (required)
#   description          — free-text description (optional)
#   enabled              — true|false; default true (optional)
#   dashboard_color      — hex color #RRGGBB for dashboard tag (optional)
#   dashboard_max_rows   — integer rows shown per visibility column (optional; default 20)
#                          Valid range: 5–100. Values outside that range are clamped with a warning.
#
# Future dashboard_* fields can be added without format changes.
#
# Edit by hand or via operator scripts (create-project.sh, add-project.sh, remove-project.sh).
# Restart the tmux dashboard session after edits.
#
# Example:
#
#   [project:pgai-chomp-man]
#   priority=1
#   description=Release-workflow demo project (see demos/chomp-man-demo/)
#   enabled=true
#   dashboard_color=#378ADD
#   dashboard_max_rows=20
#
#   # Minimal: only priority set; color comes from the palette by
#   # registration order, all other fields use defaults.
#   [project:pgai-three-bears]
#   priority=2
"""


class _Registry:
    """projects.cfg under one kanban root, read and edited by the bash helpers' rules."""

    def __init__(self, kanban_root: PathLike) -> None:
        self.path = pathlib.Path(kanban_root) / "projects.cfg"

    # -- reading ------------------------------------------------------------

    def _read(self) -> str:
        try:
            return self.path.read_text(encoding="utf-8")
        except FileNotFoundError:
            return ""
        except (OSError, UnicodeDecodeError) as exc:
            raise IoError(f"ERROR: cannot read {self.path}: {exc}") from exc

    def exists(self) -> bool:
        return self.path.is_file()

    def lines(self) -> list[str]:
        return _records(self._read()) if self.exists() else []

    def format(self) -> str:
        """``ini`` or ``colon-legacy`` (``projects_cfg_format``).

        The first line that is neither blank nor a comment decides: ``[``
        in its first column is INI. With no such line, the INI header's
        ``# Format: INI-style`` comment marks INI. A missing file is
        colon-legacy.
        """
        if not self.exists():
            return "colon-legacy"
        lines = self.lines()
        for line in lines:
            if not _skippable(line):
                return "ini" if line.startswith("[") else "colon-legacy"
        return "ini" if any(_INI_SENTINEL in line for line in lines) else "colon-legacy"

    def has(self, name: str) -> bool:
        """True when *name* is registered (``projects_cfg_has``)."""
        if not name or not self.exists():
            return False
        if self.format() == "ini":
            return f"[project:{name}]" in self.lines()
        pattern = re.compile(rf"^[ \t\r\f\v]*{re.escape(name)}[ \t\r\f\v]*:")
        return any(pattern.match(line) for line in self.lines())

    def _ini_field(self, name: str, key: str) -> tuple[bool, Optional[str]]:
        """``(section_found, value)`` for *key* in ``[project:name]``.

        A section of another kind ends the project's section. Only
        ``key=value`` lines whose key is lowercase letters and underscores
        are read, as the bash readers read them.
        """
        current = ""
        found = False
        for line in self.lines():
            if _skippable(line):
                continue
            header = _PROJECT_HEADER_RE.match(line)
            if header:
                current = header.group(1)
                found = found or current == name
                continue
            if _ANY_SECTION_RE.match(line):
                current = ""
                continue
            if current == name and _FIELD_RE.match(line):
                field_key, value = _field(line)
                if field_key == key:
                    return True, value
        return found, None

    def _colon_entry(self, name: str) -> Optional[list[str]]:
        for line in self.lines():
            if _skippable(line):
                continue
            fields = _colon_fields(line)
            if _trim(fields[0]) == name:
                return fields
        return None

    def priority(self, name: str) -> Optional[str]:
        """The registered priority of *name*, or None (``projects_cfg_priority``)."""
        if not name or not self.exists():
            return None
        if self.format() == "ini":
            return self._ini_field(name, "priority")[1]
        fields = self._colon_entry(name)
        if fields is None:
            return None
        return _trim(fields[1]) if len(fields) > 1 else ""

    def raw_color(self, name: str) -> Optional[str]:
        """The explicit colour of *name*: ``""`` when it has none, None when unregistered."""
        if not name or not self.exists():
            return None
        if self.format() == "ini":
            found, value = self._ini_field(name, "dashboard_color")
            if value is not None:
                return value
            return "" if found else None
        fields = self._colon_entry(name)
        if fields is None:
            return None
        return _trim(fields[2]) if len(fields) >= 3 else ""

    def _palette_index(self, name: str, palette: tuple[str, ...]) -> int:
        """The palette slot of *name* by sorted registration order (``_projects_cfg_palette_index_for``)."""
        if not self.exists():
            return 0
        size = len(palette)
        lines = self.lines()
        if self.format() == "ini":
            # A section of another kind does not end a project's section here.
            line_of: dict[str, int] = {}
            order: list[str] = []
            prio: dict[str, str] = {}
            current = ""
            for number, line in enumerate(lines, start=1):
                if _skippable(line):
                    continue
                header = _PROJECT_HEADER_RE.match(line)
                if header:
                    current = header.group(1)
                    line_of[current] = number
                    order.append(current)
                    continue
                if _ANY_SECTION_RE.match(line):
                    continue
                if current and _FIELD_RE.match(line):
                    key, value = _field(line)
                    if key == "priority":
                        prio[current] = value
            rows = sorted(
                (_awk_int(prio.get(nm, "") or "999"), line_of[nm], nm) for nm in order
            )
            for index, (_p, _l, nm) in enumerate(rows):
                if nm == name:
                    return index % size
            return 0
        # Colon-legacy: rows are keyed by line number, but only the first
        # len(rows) line numbers are visited, so the bash helper finds a
        # project only when no comment or blank line precedes it.
        rows_by_line: dict[int, tuple[int, int, str]] = {}
        for number, line in enumerate(lines, start=1):
            if _skippable(line):
                continue
            fields = _colon_fields(line)
            if len(fields) < 2:
                continue
            prio_text = _trim(fields[1]) or "999"
            rows_by_line[number] = (_awk_int(prio_text), number, _trim(fields[0]))
        keys = [rows_by_line.get(i, (0, 0, "")) for i in range(1, len(rows_by_line) + 1)]
        for index, (_p, _l, nm) in enumerate(sorted(keys)):
            if nm == name:
                return index % size
        return 0

    def color(self, name: str) -> Optional[str]:
        """The display colour of *name*: explicit, else its palette slot (``projects_cfg_color``)."""
        raw = self.raw_color(name)
        if raw is None:
            return None
        if raw:
            return raw
        palette = load_default_palette()
        return palette[self._palette_index(name, palette)]

    def _used_colors(self, palette: tuple[str, ...]) -> list[str]:
        """Upper-cased effective colours of every registered project."""
        if not self.exists():
            return []
        size = len(palette)
        lines = self.lines()
        rows: list[tuple[int, int, str]] = []
        explicit: dict[str, str] = {}
        if self.format() == "ini":
            line_of: dict[str, int] = {}
            order: list[str] = []
            prio: dict[str, str] = {}
            current = ""
            for number, line in enumerate(lines, start=1):
                if _skippable(line):
                    continue
                header = _PROJECT_HEADER_RE.match(line)
                if header:
                    current = header.group(1)
                    line_of[current] = number
                    order.append(current)
                    continue
                if _ANY_SECTION_RE.match(line):
                    current = ""
                    continue
                if current and _FIELD_RE.match(line):
                    key, value = _field(line)
                    if key == "priority":
                        prio[current] = value
                    if key == "dashboard_color" and value:
                        explicit[current] = value
            rows = [(_awk_int(prio.get(nm, "") or "999"), line_of[nm], nm) for nm in order]
        else:
            for number, line in enumerate(lines, start=1):
                if _skippable(line):
                    continue
                fields = _colon_fields(line)
                if len(fields) < 2:
                    continue
                nm = _trim(fields[0])
                rows.append((_awk_int(_trim(fields[1]) or "999"), number, nm))
                if len(fields) >= 3 and _trim(fields[2]):
                    explicit[nm] = _trim(fields[2])
        used = []
        for ordinal, (_p, _l, nm) in enumerate(sorted(rows)):
            used.append((explicit[nm] if nm in explicit else palette[ordinal % size]).upper())
        return used

    def next_color(self) -> str:
        """The first palette colour no registered project uses (``projects_cfg_next_color``).

        Every project's effective colour counts -- an explicit
        ``dashboard_color`` or, without one, the palette slot it holds by
        registration order. With all in use, the first entry is reused and
        a warning goes to stderr.
        """
        palette = load_default_palette()
        used = set(self._used_colors(palette))
        for entry in palette:
            if entry.upper() not in used:
                return entry
        _warn(
            "projects_cfg_next_color: all palette entries in use; wrapping to index 0 "
            "— edit projects.cfg to disambiguate"
        )
        return palette[0]

    def next_priority(self) -> int:
        """One more than the largest integer priority registered, or 1 (``projects_cfg_next_priority``)."""
        if not self.exists():
            return 1
        highest = 0
        ini_format = self.format() == "ini"
        for line in self.lines():
            if ini_format:
                if not _FIELD_RE.match(line):
                    continue
                key, value = _field(line)
                if key == "priority" and re.fullmatch(r"[0-9]+", value):
                    highest = max(highest, int(value))
            else:
                if _skippable(line):
                    continue
                fields = _colon_fields(line)
                if len(fields) >= 2 and re.fullmatch(r"[0-9]+", _trim(fields[1])):
                    highest = max(highest, int(_trim(fields[1])))
        return highest + 1

    # -- writing ------------------------------------------------------------

    def _rewrite(self, text: str) -> None:
        """Replace projects.cfg atomically, keeping its permission mode."""
        try:
            atomic_write(self.path, text, prefix=".projects.cfg.", preserve_mode=True)
        except OSError as exc:
            raise IoError(f"ERROR: failed to write {self.path}: {exc}") from exc

    def _append(self, text: str) -> None:
        try:
            with self.path.open("a", encoding="utf-8") as handle:
                handle.write(text)
        except OSError as exc:
            raise IoError(f"ERROR: failed to write {self.path}: {exc}") from exc

    def ensure(self) -> None:
        """Create projects.cfg with the INI header when it is missing (``projects_cfg_ensure``)."""
        if self.exists():
            return
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            self.path.write_text(_CANONICAL_HEADER, encoding="utf-8")
        except OSError as exc:
            raise IoError(f"ERROR: failed to create {self.path}: {exc}") from exc

    def colon_to_ini(self) -> None:
        """Convert a colon-legacy projects.cfg to INI in place (``projects_cfg_colon_to_ini``).

        Each ``name:priority[:color]`` line becomes a ``[project:name]``
        section with ``priority=`` (and ``dashboard_color=``) lines, under
        the canonical INI header; the old comment lines are dropped. Prints
        the one ``[migration]`` line. A registry already in INI is left alone.
        """
        if self.format() == "ini":
            return
        data = [line for line in self.lines() if not _skippable(line)]
        out = [_CANONICAL_HEADER.rstrip("\n")]
        for line in data:
            fields = _colon_fields(line)
            name = _trim(fields[0])
            prio = _trim(fields[1]) if len(fields) > 1 else ""
            color = _trim(fields[2]) if len(fields) >= 3 else ""
            if not name:
                continue
            out += ["", f"[project:{name}]"]
            if prio:
                out.append(f"priority={prio}")
            if color:
                out.append(f"dashboard_color={color}")
        self._rewrite(_render(out))
        _say(f"[migration] projects.cfg: converted from colon-legacy to INI format ({len(data)} entries)")

    def add(self, name: str, priority: str = "", color: str = "") -> None:
        """Register *name*, or update its priority / colour when registered (``projects_cfg_add``).

        A new entry gets *priority* (the next free one when empty) and,
        when given, *color*; it is appended as an INI section or, in a
        colon-legacy registry, a ``name:priority[:color]`` line. For a
        registered project only an existing ``priority=`` /
        ``dashboard_color=`` line is rewritten, and only for a value given.
        """
        self.ensure()
        fmt = self.format()
        if self.has(name):
            if not (priority or color):
                return
            lines = self.lines()
            if fmt == "ini":
                in_target = False
                for index, line in enumerate(lines):
                    header = _PROJECT_HEADER_RE.match(line)
                    if header:
                        in_target = header.group(1) == name
                    elif _ANY_SECTION_RE.match(line):
                        in_target = False
                    elif in_target and line.startswith("priority=") and priority:
                        lines[index] = f"priority={priority}"
                    elif in_target and line.startswith("dashboard_color=") and color:
                        lines[index] = f"dashboard_color={color}"
            else:
                for index, line in enumerate(lines):
                    if _skippable(line):
                        continue
                    fields = _colon_fields(line)
                    if _trim(fields[0]) != name:
                        continue
                    new_p = priority or (fields[1] if len(fields) > 1 else "")
                    old_c = _trim(fields[2]) if len(fields) >= 3 else ""
                    new_c = _trim(color or old_c)
                    lines[index] = f"{name}:{_trim(new_p)}:{new_c}" if new_c else f"{name}:{_trim(new_p)}"
            self._rewrite(_render(lines))
            return
        if not priority:
            priority = str(self.next_priority())
        if fmt == "ini":
            entry = f"\n[project:{name}]\npriority={priority}\n"
            if color:
                entry += f"dashboard_color={color}\n"
        else:
            entry = f"{name}:{priority}:{color}\n" if color else f"{name}:{priority}\n"
        self._append(entry)

    def remove(self, name: str) -> None:
        """Unregister *name* (``projects_cfg_remove``); a no-op when it is not registered.

        INI: the ``[project:name]`` header and every line after it up to
        the next section header go. Colon-legacy: the ``name:...`` line goes.
        """
        if not name or not self.exists() or not self.has(name):
            return
        kept: list[str] = []
        if self.format() == "ini":
            skip = False
            for line in self.lines():
                header = _PROJECT_HEADER_RE.match(line)
                if header:
                    skip = header.group(1) == name
                    if skip:
                        continue
                elif _ANY_SECTION_RE.match(line):
                    skip = False
                elif skip:
                    continue
                kept.append(line)
        else:
            for line in self.lines():
                if not _skippable(line) and _trim(_colon_fields(line)[0]) == name:
                    continue
                kept.append(line)
        self._rewrite(_render(kept))


# ---------------------------------------------------------------------------
# Value checks shared by create_project and add_project
# ---------------------------------------------------------------------------


def _ini_registration_order(registry: _Registry) -> list[str]:
    """Project names in ``projects_cfg_list`` order, for an INI registry.

    Every ``[project:<name>]`` header, sorted by its priority (999 when
    absent or not a plain integer, as ``projects_cfg_list`` and
    ``ini.projects_registry`` both read it), then by the header's line
    number. A section of another kind does not end a project's section
    here, as in the bash helper.
    """
    line_of: dict[str, int] = {}
    order: list[str] = []
    prio: dict[str, str] = {}
    current = ""
    for number, line in enumerate(registry.lines(), start=1):
        if _skippable(line):
            continue
        header = _PROJECT_HEADER_RE.match(line)
        if header:
            current = header.group(1)
            line_of[current] = number
            order.append(current)
            continue
        if _ANY_SECTION_RE.match(line):
            continue
        if current and _FIELD_RE.match(line):
            key, value = _field(line)
            if key == "priority":
                prio[current] = value
    rows = sorted((_list_priority(prio.get(nm, "")), line_of[nm], nm) for nm in order)
    return [nm for _p, _l, nm in rows]


def _list_priority(value: str) -> int:
    """A registry priority as ``projects_cfg_list`` sorts by it: a plain integer, else 999."""
    return int(value) if _PLAIN_INT_RE.fullmatch(value) else 999


def _dashboard_max_rows(registry: _Registry, name: str, default_rows: str) -> str:
    """``projects_cfg_max_rows`` for an INI registry: the clamped per-project value or *default_rows*."""
    raw = registry._ini_field(name, "dashboard_max_rows")[1]
    if not raw:
        return default_rows
    if not re.fullmatch(r"[0-9]+", raw):
        return default_rows
    value = int(raw)
    if value < 5:
        return "5"
    if value > 100:
        return "100"
    return raw


def dashboard_registry_rows(
    kanban_root: PathLike, default_max_rows: str,
) -> Optional[list[tuple[str, Optional[str], str]]]:
    """The registered projects as the dashboard's mixed-project board reads them.

    One ``(name, colour, max_rows)`` per project, in ``projects_cfg_list``
    order: *colour* is ``projects_cfg_color``'s answer (explicit
    ``dashboard_color``, else the palette slot), and *max_rows* is
    ``projects_cfg_max_rows``'s (the per-project ``dashboard_max_rows``,
    clamped to 5..100, else *default_max_rows* -- the caller's
    ``${DASHBOARD_MAX_ROWS:-20}``), both as the strings the bash helpers
    print.

    Returns ``None`` for an absent or colon-legacy projects.cfg, which this
    reader does not cover; a caller then uses the bash helpers instead.
    """
    registry = _Registry(kanban_root)
    if not registry.exists() or registry.format() != "ini":
        return None
    return [
        (name, registry.color(name), _dashboard_max_rows(registry, name, default_max_rows))
        for name in _ini_registration_order(registry)
    ]


def registered_project_names(kanban_root: PathLike) -> list[str]:
    """Every project registered in ``<kanban_root>/projects.cfg``.

    An INI registry lists in ``projects_cfg_list`` order (priority, then
    registration); a colon-legacy one in file order, one name per entry
    line. An absent projects.cfg registers nothing.
    """
    registry = _Registry(kanban_root)
    if not registry.exists():
        return []
    if registry.format() == "ini":
        return _ini_registration_order(registry)
    names = []
    for line in registry.lines():
        if _skippable(line):
            continue
        name = _trim(_colon_fields(line)[0])
        if name and name not in names:
            names.append(name)
    return names


def _check_color(flag: str, color: str) -> None:
    """Refuse *color* unless it is ``#RRGGBB``, naming the common mistakes."""
    if _COLOR_RE.fullmatch(color):
        return
    if _COLOR_0X_RE.fullmatch(color):
        message = f"ERROR: {flag} got '{color}': use #RRGGBB format (not 0xRRGGBB)"
    elif _COLOR_SHORT_RE.fullmatch(color):
        message = f"ERROR: {flag} got '{color}': short-form #RGB is not supported; use full #RRGGBB"
    else:
        message = f"ERROR: {flag} requires a color in #RRGGBB format (e.g. #378ADD); got: '{color}'"
    raise ProjectRefusal(message)


def _registry_format_line(fmt: str, no_migrate: bool) -> str:
    """The ``projects.cfg:`` line of a create/add plan."""
    if fmt == "colon-legacy":
        if no_migrate:
            return "  projects.cfg:   colon-legacy (--no-migrate: will append colon line; WARNING: mixed format)"
        return "  projects.cfg:   colon-legacy (will auto-migrate to INI before registering)"
    return "  projects.cfg:   INI format"


def _prepare_registry_format(registry: _Registry, fmt: str, no_migrate: bool) -> None:
    """Migrate a colon-legacy registry to INI, or warn that it stays mixed under no_migrate."""
    if fmt != "colon-legacy":
        return
    if no_migrate:
        _warn(_NO_MIGRATE_WARNING)
    else:
        registry.colon_to_ini()


# ---------------------------------------------------------------------------
# Workflow plugins
# ---------------------------------------------------------------------------


def _workflow_manifest_error(root: str, workflow: str) -> tuple[Optional[str], str]:
    """Validate the workflow plugin *workflow* under *root* as the bash dispatcher does.

    Returns ``(error, agents)``: ``error`` is None for a usable plugin (its
    manifest is ``status = ready`` with a valid ``git_mode`` and an
    ``agents`` list, and it ships a readable ``workflow.sh``), otherwise the
    dispatcher's message for the first problem; ``agents`` is the
    manifest's ``[capabilities] agents`` value.
    """
    if not workflow:
        return "wf_load_plugin: workflow_type argument is required", ""
    if not os.path.isdir(root):
        return f"workflow type '{workflow}': cannot resolve workflows root (no valid directory found)", ""
    plugin_dir = f"{root}/{workflow}"
    if not os.path.isdir(plugin_dir):
        return (
            f"workflow type '{workflow}': no plugin directory found at {plugin_dir} (scanned root: {root})",
            "",
        )
    manifest = f"{plugin_dir}/workflow.cfg"
    if not os.path.isfile(manifest):
        return f"workflow type '{workflow}': manifest not found at {manifest}", ""
    if not os.access(manifest, os.R_OK):
        return f"workflow type '{workflow}': manifest not readable at {manifest}", ""

    def _get(section: str, key: str) -> str:
        return ini.read_line_value(manifest, section, key) or ""

    status = _get("workflow", "status")
    git_mode = _get("capabilities", "git_mode")
    agents = _get("capabilities", "agents")
    if not status:
        return f"workflow type '{workflow}': manifest missing [workflow] status field in {manifest}", agents
    if status == "scaffold":
        return (
            f"workflow type '{workflow}': plugin status is 'scaffold' — not ready for use; "
            "flip status to 'ready' after implementing all hooks",
            agents,
        )
    if status != "ready":
        return (
            f"workflow type '{workflow}': manifest [workflow] status must be 'ready' or "
            f"'scaffold', got '{status}' in {manifest}",
            agents,
        )
    if not git_mode:
        return f"workflow type '{workflow}': manifest missing [capabilities] git_mode in {manifest}", agents
    if not agents:
        return f"workflow type '{workflow}': manifest missing [capabilities] agents in {manifest}", agents
    if git_mode not in ("none", "ro", "rw"):
        return (
            f"workflow type '{workflow}': invalid [capabilities] git_mode '{git_mode}' "
            f"(must be none, ro, or rw) in {manifest}",
            agents,
        )
    script = f"{plugin_dir}/workflow.sh"
    if not os.path.isfile(script):
        return f"workflow type '{workflow}': plugin script not found at {script}", agents
    if not os.access(script, os.R_OK):
        return f"workflow type '{workflow}': plugin script not readable at {script}", agents
    return None, agents


def _ready_workflow_types(root: str) -> list[str]:
    """Names of the plugins under *root* whose manifest says ``status = ready``, sorted."""
    try:
        names = os.listdir(root)
    except OSError:
        return []
    ready = []
    for name in names:
        cfg = f"{root}/{name}/workflow.cfg"
        if os.path.isfile(cfg) and (ini.read_line_value(cfg, "workflow", "status") or "") == "ready":
            ready.append(name)
    return sorted(ready)


def _check_workflow(kanban_root: str, scripts_dir: str, workflow: str) -> str:
    """Refuse a workflow type that is unknown, scaffold-only or broken; return its agents.

    The plugins are looked up under ``<kanban root>/workflows`` when it
    exists, else under the ``workflows/`` beside *scripts_dir*. *workflow*
    is matched by name against :func:`_ready_workflow_types` before it is
    joined onto that root; a name that is not one plain name is never
    joined at all, and is refused as an unknown type.
    """
    root = f"{kanban_root}/workflows"
    if not os.path.isdir(root):
        root = f"{scripts_dir}/../workflows"
    # Looked up by name first: only a name listed under the plugins root is
    # joined onto it, so a path-shaped type never names a directory (or a
    # workflow.sh) outside it.
    ready = _ready_workflow_types(root)
    if workflow in ready:
        error, agents = _workflow_manifest_error(root, workflow)
        if error is None:
            return agents
        raise ProjectRefusal(f"ERROR: workflow type '{workflow}' is not valid: {error}")
    try:
        require_component(workflow, "workflow_type")
        probe = f"{root}/{workflow}/workflow.cfg"
    except ComponentRefused:
        probe = ""
    if probe and os.path.isfile(probe):
        error, _agents = _workflow_manifest_error(root, workflow)
        if (ini.read_line_value(probe, "workflow", "status") or "") == "scaffold":
            raise ProjectRefusal(
                f"ERROR: workflow type '{workflow}' cannot be used: plugin status is 'scaffold' "
                "— flip status to 'ready' after implementing all hooks"
            )
        raise ProjectRefusal(f"ERROR: workflow type '{workflow}' is not valid: {error}")
    if ready:
        raise ProjectRefusal(
            f"ERROR: unknown workflow type '{workflow}'; discovered ready types are: {' '.join(ready)}"
        )
    raise ProjectRefusal(
        f"ERROR: unknown workflow type '{workflow}'; no ready workflow plugins found under {root}"
    )


# ---------------------------------------------------------------------------
# Input confinement
# ---------------------------------------------------------------------------

# What a value written into project.cfg must be, for the refusal message.
_CFG_VALUE_RULE = (
    "A value written into project.cfg must stay on one line: no newline or other "
    "control character, which would start a new key or section."
)
_DEV_TREE_RULE = "dev_tree must also be an absolute path with no '..' segment."


def check_project_name(verb: str, project: str, kanban_root: PathLike) -> None:
    """Refuse a project name ``add_project`` / ``remove_project`` must not join into a path.

    Accepted: one plain name (``api.validation.require_component``) made
    only of letters, digits, ``_`` and ``-`` -- every name a
    ``[project:<name>]`` section can hold. Reads nothing.

    Args:
        verb:        ``add_project`` or ``remove_project`` (the error key's verb).
        project:     The name as given.
        kanban_root: Named in the remedy, which lists what is there.

    Raises:
        ProjectValueRefused: ``<verb>.project_invalid``, exit 1.
    """
    try:
        require_component(project, "project")
        if not _REGISTRY_NAME_RE.fullmatch(project):
            raise ComponentRefused(
                "project", "holds a character other than a letter, digit, '_' or '-'"
            )
    except ComponentRefused as exc:
        if verb == "remove_project":
            remedy = f"grep -o '^\\[project:[^]]*\\]' {shlex.quote(f'{kanban_root}/projects.cfg')}"
        else:
            remedy = f"ls {shlex.quote(f'{kanban_root}/projects')}"
        # BLOCK-WORTHY (docs/blocking-site-census.md): an invariant by
        # design -- the name is joined into projects/<name>, which add seeds
        # a README into and remove --force deletes, and is written into
        # projects.cfg; anything but one registrable name could reach
        # outside the kanban root or inject a registry section.
        # CENSUS-ANCHOR: projects-name-not-component-01
        raise ProjectValueRefused("project", exc.block(verb, remedy)) from exc


def _value_refusal(field: str, reason: str, rule: str, remedy: str) -> ProjectValueRefused:
    """Build the ``create_project.<field>_invalid`` refusal for a project.cfg value."""
    return ProjectValueRefused(
        field,
        format_error_contract(
            f"create_project.{field}_invalid",
            f"create_project refused the '{field}' field: the value {reason}. {rule} "
            "Nothing was written.",
            remedy,
        ),
    )


def check_priority(verb: str, priority: str, project: Optional[str] = None) -> None:
    """Refuse a registry priority that is not a whole number.

    The value is written verbatim into ``projects.cfg`` as ``priority=<value>``
    (or a ``name:priority`` line), so anything but an integer -- a newline
    above all -- could add a section or key of its own. The REST field is an
    integer already; this holds the CLI (create-project.sh, add-project.sh)
    to the same rule. An empty value (take the next free priority) is not
    checked. Reads nothing.

    Args:
        verb:     ``create_project`` or ``add_project`` (the error key's verb).
        priority: The value as given.
        project:  Interpolated into the remedy when it is a plain name.

    Raises:
        ProjectValueRefused: ``<verb>.priority_invalid``, exit 1.
    """
    if not priority or re.fullmatch(r"-?[0-9]+", priority):
        return
    name = project if project and _PROJECT_NAME_RE.fullmatch(project) else "my-app"
    script = "create-project.sh" if verb == "create_project" else "add-project.sh"
    fault = control_character_fault(priority)
    reason = fault if fault is not None else "is not a whole number"
    # BLOCK-WORTHY (docs/blocking-site-census.md): an invariant by design --
    # the priority is written verbatim into projects.cfg, where a newline
    # starts a new key or a new [project:...] section.
    # CENSUS-ANCHOR: projects-priority-invalid-01
    raise ProjectValueRefused(
        "priority",
        format_error_contract(
            f"{verb}.priority_invalid",
            f"{verb} refused the 'priority' field: the value {reason}. A registry priority is a "
            "whole number (lower runs first) and is written verbatim into projects.cfg. "
            "Nothing was written.",
            f"team/scripts/{script} --project {name} --priority 2 --dry-run",
        ),
    )


def check_create_values(
    *,
    project: Optional[str] = None,
    workflow_type: Optional[str] = None,
    git_remote: Optional[str] = None,
    dev_tree: Optional[str] = None,
    git_repo: Optional[str] = None,
) -> None:
    """Refuse a ``create_project`` value that could escape a path or inject project.cfg keys.

    Pure string checks -- nothing is read -- run by :func:`create_project`
    before it touches the filesystem, and by ``POST
    /operations/create-project`` before its dry-run description, so a dry
    run and a real call refuse the same values. A value left None (or
    empty) is not checked here.

      - ``project``: the name create_project registers -- a letter, then
        letters, digits, ``-`` and ``_``.
      - ``workflow_type``: one plain name (``require_component``); it is
        then looked up by name among the ready plugins.
      - ``git_remote``, ``dev_tree``, ``git_repo``: no newline or other
        control character (``control_character_fault``).
      - ``dev_tree``: absolute, with no ``..`` segment.

    Args:
        project: The project name, interpolated into the remedies when it
                 is a valid one (else the example ``my-app``).

    Raises:
        ProjectValueRefused: ``create_project.<field>_invalid``, exit 1.
    """
    name = project if project and _PROJECT_NAME_RE.fullmatch(project) else "my-app"
    retry = f"team/scripts/create-project.sh --project {name}"
    if project and not _PROJECT_NAME_RE.fullmatch(project):
        # BLOCK-WORTHY (docs/blocking-site-census.md): an invariant by
        # design -- the name becomes projects/<name>, the [project:<name>]
        # registry section and the branch-free id every surface resolves;
        # one outside the rule is refused before anything is created.
        # CENSUS-ANCHOR: projects-create-name-invalid-01
        raise _value_refusal(
            "project",
            f"'{project}' does not start with a letter followed only by letters, digits, '-' and '_'",
            "A project name starts with a letter and holds only letters, digits, '-' and '_'.",
            f"{retry} --dry-run",
        )
    if workflow_type:
        try:
            require_component(workflow_type, "workflow_type")
        except ComponentRefused as exc:
            # BLOCK-WORTHY (docs/blocking-site-census.md): an invariant by
            # design -- the workflow type names a plugin whose workflow.sh
            # every wake sources; a path-shaped value could name a script
            # outside the kanban. Refused before any plugin lookup.
            # CENSUS-ANCHOR: projects-workflow-type-not-component-01
            raise ProjectValueRefused(
                "workflow_type", exc.block("create_project", f"{retry} --workflow-type release --dry-run")
            ) from exc
    examples = {
        "git_remote": "--git-remote origin",
        "dev_tree": f"--dev-tree /srv/develop/{name}",
        "git_repo": f"--git-repo git@example.com:org/{name}.git",
    }
    for field, value in (("git_remote", git_remote), ("dev_tree", dev_tree), ("git_repo", git_repo)):
        if not value:
            continue
        reason = control_character_fault(value)
        if reason is None and field == "dev_tree":
            if not os.path.isabs(value):
                reason = "is not an absolute path"
            elif ".." in pathlib.PurePath(value).parts:
                reason = "contains a '..' segment"
        if reason is None:
            continue
        rule = f"{_CFG_VALUE_RULE} {_DEV_TREE_RULE}" if field == "dev_tree" else _CFG_VALUE_RULE
        # BLOCK-WORTHY (docs/blocking-site-census.md): an invariant by
        # design -- the value is written verbatim into project.cfg, where a
        # newline starts a new key or section (a whole [release] block),
        # and dev_tree names the repository every release acts on.
        # CENSUS-ANCHOR: projects-cfg-value-invalid-01
        raise _value_refusal(field, reason, rule, f"{retry} {examples[field]} --dry-run")


# ---------------------------------------------------------------------------
# create_project
# ---------------------------------------------------------------------------

# Agent role -> (queue file, title, description) for a workflow whose
# template directory ships no queue-files.list.
_AGENT_QUEUES = {
    "pm": ("pm_backlog.md", "PM Backlog", "pm agent"),
    "coder": ("coder_backlog.md", "Coder Backlog", "coder agent"),
    "writer": ("writer_backlog.md", "Writer Backlog", "writer agent"),
    "tester": ("tester_backlog.md", "Tester Backlog", "tester agent"),
    "cm": ("cm_backlog.md", "CM Backlog", "cm agent"),
}
# Seeded for every workflow: the intake queues, and OVERWATCH's queue, which
# no workflow's agent roster names.
_INTAKE_QUEUES = (
    ("bug_backlog.md", "Bug Backlog", "bug triage"),
    ("priority_backlog.md", "Priority Backlog", "priority intake"),
)
_OVERWATCH_QUEUE = ("overwatch_backlog.md", "Overwatch Backlog", "overwatch agent")

# Template file -> path under the project directory it is copied to.
# create_project seeds these; ops.seeded_templates keeps an existing
# project's copies current with the installed templates on install and
# upgrade. The README copies below are seeded once and never refreshed,
# and so is the project README (_PROJECT_README_TEMPLATE).
_TEMPLATE_COPIES = (
    ("BUG-TEMPLATE.md", "bugs/templates/BUG-TEMPLATE.md"),
    ("PRIORITY-TEMPLATE.md", "priority/templates/PRIORITY-TEMPLATE.md"),
    ("REQUIREMENTS-TEMPLATE.md", "requirements/templates/REQUIREMENTS-TEMPLATE.md"),
)
_README_COPIES = (
    ("README-bugs.md", "bugs/README.md"),
    ("README-priority.md", "priority/README.md"),
    ("README-requirements.md", "requirements/README.md"),
)
# The project-layer README template -> projects/<name>/README.md. One
# template serves every workflow type, so it sits in templates/project/
# itself, beside the per-workflow directories. create_project and
# add_project seed it when the README is absent; nothing ever overwrites
# an existing one, because it holds the operator's own project rules.
_PROJECT_README_TEMPLATE = "README-TEMPLATE.md"
_PROJECT_README = "README.md"
_LAYOUT_DIRS = (
    "",
    "tasks/queues",
    "tasks/queues/plans",
    "bugs/templates",
    "priority/templates",
    "requirements/templates",
    "artifacts",
    "release-notes",
    "metrics",
    "logs",
)

#: A new project's release-state.md: every schema field set to "none",
#: written through the release-state writer.
_RELEASE_STATE_SEED_FIELDS = {heading: "none" for heading in RELEASE_STATE_FIELDS}


def queue_file_text(title: str, description: str) -> str:
    """The content of a freshly seeded ``<agent>_backlog.md``: title and marker legend."""
    return (
        f"# {title}\n"
        "\n"
        f"Tasks ready for the {description} to pull. Markers:\n"
        "\n"
        "- `[ ]` pending (BACKLOG, ready to pull)\n"
        "- `[W]` waiting on prerequisites\n"
        "- `[A]` actively being worked\n"
        "- `[B]` blocked, needs human intervention\n"
        "- `[x]` done or won't-do\n"
        "\n"
    )


def _queue_list_entries(path: str) -> list[tuple[str, str, str]]:
    """``(file, title, description)`` rows of a template's queue-files.list.

    Format ``<filename>:<title>:<description>``; comment and blank lines
    are skipped, trailing blanks trimmed from each field, and a last line
    with no newline is not read.
    """
    with open(path, encoding="utf-8") as handle:
        text = handle.read()
    rows = []
    for line in text.split("\n")[:-1]:
        queue_file, _sep, rest = line.partition(":")
        title, _sep, desc = rest.partition(":")
        if not queue_file or _COMMENT_RE.match(queue_file):
            continue
        queue_file, title, desc = (field.rstrip(" \t\r\f\v") for field in (queue_file, title, desc))
        if queue_file:
            rows.append((queue_file, title, desc))
    return rows


def _project_cfg_header(name: str, generated_by: str, paths_supplied: bool) -> list[str]:
    """The comment lines that open a rendered project.cfg, in place of the template's header."""
    generated = datetime.datetime.now().astimezone().isoformat(timespec="seconds")
    if paths_supplied:
        path_note = [
            "# dev_tree_path and/or git_repo_url were supplied via --dev-tree/--git-repo",
            "# at creation time. Edit this file directly if either value needs to change.",
        ]
    else:
        path_note = [
            "# IMPORTANT: dev_tree_path and git_repo_url are intentionally empty.",
            "# Edit this file to supply them before the chain runs its first release.",
        ]
    return [
        f"# project.cfg for {name}",
        f"# Generated by {generated_by} on {generated}",
        "# from the project.cfg template (config/project.cfg_example).",
        "#",
        "# INI format: [section] headers and key = value lines; # starts a comment.",
        "# A commented \"# key = value\" line is not set here and shows the value in",
        "# force while it stays commented; uncomment it to set it for this project.",
        "# Every key is documented where it appears.",
        "#",
        *path_note,
        "#",
        "# Ceilings are operator checkpoints — raise or lower per project as needed:",
        f"#   set-version-ceiling.sh --project {name} --minor <N>",
    ]


def render_project_cfg(
    *,
    name: str,
    generated_by: str,
    workflow: str = DEFAULT_WORKFLOW,
    git_remote: str = DEFAULT_GIT_REMOTE,
    dev_tree: str = "",
    git_repo: str = "",
    max_patch: str = DEFAULT_MAX_PATCH,
    max_minor: str = DEFAULT_MAX_MINOR,
    max_major: str = DEFAULT_MAX_MAJOR,
    template: PathLike = PROJECT_CFG_TEMPLATE,
) -> str:
    """The project.cfg a project starts with, rendered from the project.cfg template.

    The template is the only copy of the key text. Rendering changes three
    things and nothing else, so the result carries the template's sections,
    keys, order and comments:

    * the template's header -- its leading comment lines, up to the first
      line that is not a comment -- is replaced by one naming the project,
      *generated_by* and the time, and saying whether the path fields were
      supplied;
    * the active line of each project-specific key (``[project]``
      ``project_name``, ``workflow_type``, ``git_remote_name``,
      ``dev_tree_path``, ``git_repo_url`` and the three ``[versioning]``
      ceilings) gets the given value, written ``key = value`` (``key =``
      when the value is empty);
    * every ``<project_name>`` in the text becomes *name*.

    Args:
        name:         The project name.
        generated_by: The command named in the header (``create-project.sh``,
                      ``add-project.sh``).
        workflow, git_remote, dev_tree, git_repo, max_patch, max_minor,
        max_major:    The project-specific values; defaults are the
                      template's own.
        template:     The template file (default :data:`PROJECT_CFG_TEMPLATE`).

    Returns:
        The file text, ending in one newline.

    Raises:
        IoError: The template cannot be read, or lacks the active line of a
                 key that is substituted (naming the template and the keys).
    """
    try:
        text = pathlib.Path(template).read_text(encoding="utf-8")
    except OSError as exc:
        raise IoError(
            f"ERROR: cannot read the project.cfg template {template}: {exc}\n"
            "       Every install ships it as config/project.cfg_example; re-run upgrade.sh to restore it."
        ) from exc
    values = {
        ("project", "project_name"): name,
        ("project", "workflow_type"): workflow,
        ("project", "git_remote_name"): git_remote,
        ("project", "dev_tree_path"): dev_tree,
        ("project", "git_repo_url"): git_repo,
        ("versioning", "max_patch"): max_patch,
        ("versioning", "max_minor"): max_minor,
        ("versioning", "max_major"): max_major,
    }
    lines = text.splitlines()
    first_body = 0
    while first_body < len(lines) and lines[first_body].lstrip().startswith(("#", ";")):
        first_body += 1
    body: list[str] = []
    section = ""
    seen: set[tuple[str, str]] = set()
    for line in lines[first_body:]:
        header = _CFG_SECTION_RE.match(line)
        if header:
            section = header.group(1)
        else:
            active = _CFG_ACTIVE_KEY_RE.match(line)
            slot = (section, active.group(1)) if active else None
            if slot in values and slot not in seen:
                seen.add(slot)
                value = values[slot]
                line = f"{slot[1]} = {value}" if value else f"{slot[1]} ="
        body.append(line.replace(PROJECT_NAME_PLACEHOLDER, name))
    missing = [f"[{s}] {k}" for s, k in values if (s, k) not in seen]
    if missing:
        raise IoError(
            f"ERROR: the project.cfg template {template} has no active line for: {', '.join(missing)}\n"
            "       Restore the template from the install (re-run upgrade.sh) and retry."
        )
    header_lines = _project_cfg_header(name, generated_by, bool(dev_tree or git_repo))
    return "\n".join(header_lines + body).rstrip("\n") + "\n"


def _write_new(path: str, text: str) -> bool:
    """Create *path* holding *text*; False, writing nothing, when it already exists."""
    try:
        with open(path, "x", encoding="utf-8") as handle:
            handle.write(text)
    except FileExistsError:
        return False
    except OSError as exc:
        raise IoError(f"ERROR: failed to write {path}: {exc}") from exc
    return True


def _write(path: str, text: str) -> None:
    try:
        with open(path, "w", encoding="utf-8") as handle:
            handle.write(text)
    except OSError as exc:
        raise IoError(f"ERROR: failed to write {path}: {exc}") from exc


def _seed_release_state(path: str) -> None:
    """Create *path* through the release-state writer with every field "none"."""
    try:
        update_release_state_fields(path, _RELEASE_STATE_SEED_FIELDS)
    except OSError as exc:
        raise IoError(f"ERROR: failed to write {path}: {exc}") from exc


def _copy(source: str, target: str) -> None:
    try:
        shutil.copyfile(source, target)
    except OSError as exc:
        raise IoError(f"ERROR: cannot copy {source} to {target}: {exc}") from exc


def project_readme_template(scripts_dir: PathLike) -> str:
    """The project README template a project's ``README.md`` is seeded from.

    ``<scripts_dir>/../templates/project/README-TEMPLATE.md``: shared by
    every workflow type, so it is not inside a per-workflow directory.
    """
    return f"{scripts_dir}/../templates/project/{_PROJECT_README_TEMPLATE}"


def _project_readme_source(project_dir: str, scripts: str) -> Optional[str]:
    """The template to seed ``<project_dir>/README.md`` from, or None to seed nothing.

    None when the README already exists -- any directory entry at that
    path, a dangling link included, counts: the README is seeded once and
    never overwritten -- or when the template is missing, which is warned
    to stderr (the verb still finishes; only the README is not seeded).
    """
    if os.path.lexists(f"{project_dir}/{_PROJECT_README}"):
        return None
    source = project_readme_template(scripts)
    if not os.path.isfile(source):
        _warn(
            f"WARNING: project README template not found: {source}",
            f"         {project_dir}/{_PROJECT_README} was not seeded; write it by hand "
            "(the template ships in every install as templates/project/README-TEMPLATE.md).",
        )
        return None
    return source


def workflow_template_dir(scripts_dir: PathLike, workflow: str) -> tuple[Optional[str], bool]:
    """The template directory a project of *workflow* is seeded from.

    ``<scripts_dir>/../templates/project/<workflow>`` when it exists, else
    the ``release`` one beside it. The seeded copies a project carries
    (:data:`_TEMPLATE_COPIES` and the READMEs) come from this directory,
    both when :func:`create_project` lays a project down and when
    ``ops.seeded_templates`` refreshes an existing one.

    Returns:
        ``(directory, fell_back)``: *fell_back* is True when the workflow
        has no template directory of its own and the release one is used.
        *directory* is None when neither exists.
    """
    template_dir = f"{scripts_dir}/../templates/project/{workflow}"
    if os.path.isdir(template_dir):
        return template_dir, False
    fallback = f"{scripts_dir}/../templates/project/release"
    if os.path.isdir(fallback):
        return fallback, True
    return None, True


def create_project(
    ctx: OpsContext,
    project: Optional[str],
    *,
    workflow_type: Optional[str] = None,
    max_patch: Value = None,
    max_minor: Value = None,
    max_major: Value = None,
    git_remote: Optional[str] = None,
    priority: Value = None,
    color: Optional[str] = None,
    dev_tree: Optional[str] = None,
    git_repo: Optional[str] = None,
    no_migrate: bool = False,
    dry_run: bool = False,
    scripts_dir: Optional[PathLike] = None,
) -> ProjectOutcome:
    """Bootstrap ``projects/<project>/`` and register it in projects.cfg.

    Checks, in order, each refusing (exit 1 unless noted) before anything
    is written:

    1. *max_patch*, *max_minor*, *max_major* given non-empty are
       non-negative integers.
    2. *color* given non-empty is ``#RRGGBB``.
    3. *dev_tree* / *git_repo* given (not None) are not empty.
    4. :func:`check_create_values`: the workflow type is one plain name;
       *git_remote*, *dev_tree* and *git_repo* hold no newline or other
       control character; *dev_tree* is absolute with no ``..`` segment
       (``create_project.<field>_invalid``, dual-channel).
    5. *project* is given and matches ``[a-zA-Z][a-zA-Z0-9_-]*`` -- before
       any filesystem access. A *dev_tree* that does not exist only warns.
    6. ``projects/<project>/`` does not exist yet (exit 2).
    7. The workflow type names a ready plugin, matched by name (see
       ``_check_workflow``), and a template directory exists for it (or
       the release one).

    Then the plan is printed; with *dry_run* the template copies are listed
    and nothing is written. Otherwise the project tree is laid down and the
    project registered: a colon-legacy projects.cfg is migrated to INI
    first unless *no_migrate*, and an empty *priority* / *color* takes the
    next free priority and palette colour.

    ``project.cfg`` is rendered from the project.cfg template
    (:func:`render_project_cfg`): every key the kanban reads, documented,
    with this call's values in place; an unreadable template raises
    :class:`IoError` before anything is written.

    The tree includes ``README.md``, the project layer every agent reads,
    copied from :func:`project_readme_template` (a missing template only
    warns, and the README is not seeded). The closing "Next steps" tell
    the operator to fill it in.

    Args:
        ctx:           Kanban root.
        project:       The new project's name.
        workflow_type: Workflow plugin (default ``release``).
        max_patch, max_minor, max_major: project.cfg ceilings (defaults
                       21, 13, 0); written as given.
        git_remote:    ``git_remote_name`` (default ``origin``).
        priority:      Registry priority; written as given (default: the
                       next free one).
        color:         Registry colour (default: the next unused palette entry).
        dev_tree:      ``dev_tree_path``, written verbatim once checked
                       (default empty).
        git_repo:      ``git_repo_url``, written verbatim once checked
                       (default empty).
        no_migrate:    Append to a colon-legacy registry instead of migrating it.
        dry_run:       Print the plan only.
        scripts_dir:   The caller's ``scripts/`` directory; the workflow
                       templates and the project README template are read
                       from ``../templates/project/`` beside it, and plugins
                       from ``../workflows`` when the kanban root has none.
                       Default: this package's own tree.

    Returns:
        The :class:`ProjectOutcome` (priority and colour as registered).

    Raises:
        ProjectRefusal: A check above failed.
        IoError:        A file or directory could not be read or written.
    """
    root = str(ctx.kanban_root)
    scripts = str(scripts_dir) if scripts_dir is not None else str(_OWN_TREE / "scripts")
    name = project or ""
    workflow = DEFAULT_WORKFLOW if workflow_type is None else workflow_type
    remote = DEFAULT_GIT_REMOTE if git_remote is None else git_remote
    ceilings = [
        ("--max-patch", DEFAULT_MAX_PATCH if max_patch is None else str(max_patch)),
        ("--max-minor", DEFAULT_MAX_MINOR if max_minor is None else str(max_minor)),
        ("--max-major", DEFAULT_MAX_MAJOR if max_major is None else str(max_major)),
    ]
    prio = _text(priority) or ""
    colour = color or ""

    for flag, value in ceilings:
        if value and not re.fullmatch(r"[0-9]+", value):
            raise ProjectRefusal(f"ERROR: {flag} requires a non-negative integer; got: '{value}'")
    check_priority("create_project", prio, name)
    if colour:
        _check_color("--color", colour)
    if dev_tree is not None and not dev_tree:
        raise ProjectRefusal("ERROR: --dev-tree/--dev-tree-path requires a non-empty value.")
    if git_repo is not None and not git_repo:
        raise ProjectRefusal("ERROR: --git-repo/--git-repo-url requires a non-empty value.")
    check_create_values(
        project=name, workflow_type=workflow, git_remote=remote, dev_tree=dev_tree, git_repo=git_repo
    )
    dev_tree = dev_tree or ""
    git_repo = git_repo or ""
    if not name:
        raise ProjectRefusal(f"ERROR: project name is required (--project <name>)\n{_CREATE_USAGE}")
    if dev_tree and not os.path.exists(dev_tree):
        _warn(
            f"WARNING: --dev-tree path does not currently exist: {dev_tree}",
            "         Writing it into project.cfg anyway; clone or create the tree before the first release.",
        )

    project_dir = f"{root}/projects/{name}"
    if os.path.isdir(project_dir):
        raise ProjectRefusal(
            f"ERROR: project directory already exists: {project_dir}\n"
            "       Use add-project.sh to register an existing project, or\n"
            "       remove-project.sh to clear it before re-creating.",
            exit_code=2,
        )

    agents = _check_workflow(root, scripts, workflow)

    template_dir, template_fallback = workflow_template_dir(scripts, workflow)
    if template_dir is None:
        raise ProjectRefusal(
            f"ERROR: template directory not found for workflow type '{workflow}' "
            "and fallback release/ template is also missing"
        )

    # Rendered before the plan is printed, so an unreadable template refuses
    # before anything is written.
    cfg_text = render_project_cfg(
        name=name, generated_by="create-project.sh", workflow=workflow, git_remote=remote,
        dev_tree=dev_tree, git_repo=git_repo, max_patch=ceilings[0][1],
        max_minor=ceilings[1][1], max_major=ceilings[2][1],
    )

    registry = _Registry(root)
    _say(
        f"Creating project: {name}",
        f"  Project root:   {project_dir}",
        f"  Workflow:       {workflow}",
        f"  Dev tree:       {dev_tree or '<empty — edit project.cfg after creation>'}",
        f"  Git repo:       {git_repo or '<empty — edit project.cfg after creation>'}",
        f"  Git remote:     {remote}",
        f"  max_patch:      {ceilings[0][1]}",
        f"  max_minor:      {ceilings[1][1]}",
        f"  max_major:      {ceilings[2][1]}",
        f"  Priority:       {prio or '<next available>'}",
        f"  Color:          {colour or '<next unused palette entry>'}",
    )
    if registry.exists():
        _say(_registry_format_line(registry.format(), no_migrate))
    _say("")

    brief = os.path.isfile(f"{template_dir}/BRIEF-EXAMPLE.md")
    readme_source = _project_readme_source(project_dir, scripts)
    if dry_run:
        _say(
            "[DRY-RUN] No changes will be made.",
            "",
            f"[DRY-RUN] Template files that would be copied from {template_dir}:",
        )
        for source, target in _TEMPLATE_COPIES + _README_COPIES:
            _say(f"  cp {template_dir}/{source} -> {project_dir}/{target}")
        if brief:
            _say(f"  cp {template_dir}/BRIEF-EXAMPLE.md -> {project_dir}/brief-example.md")
        _say(f"  cp {template_dir}/INTENT-TEMPLATE.md -> {project_dir}/INTENT.md")
        if readme_source:
            _say(f"  cp {readme_source} -> {project_dir}/{_PROJECT_README}")
        return ProjectOutcome("create_project", name, pathlib.Path(project_dir), dry_run=True)

    try:
        for sub in _LAYOUT_DIRS:
            os.makedirs(f"{project_dir}/{sub}" if sub else project_dir, exist_ok=True)
    except OSError as exc:
        raise IoError(f"ERROR: cannot create the project layout under {project_dir}: {exc}") from exc
    _say("  + created directory layout")

    _write(f"{project_dir}/project.cfg", cfg_text)
    _say("  + wrote project.cfg")

    queues = f"{project_dir}/tasks/queues"
    queue_list = f"{template_dir}/queue-files.list"
    count = 0
    if not template_fallback and os.path.isfile(queue_list):
        try:
            entries = _queue_list_entries(queue_list)
        except OSError as exc:
            raise IoError(f"ERROR: cannot read {queue_list}: {exc}") from exc
        for queue_file, title, desc in entries:
            _write(f"{queues}/{queue_file}", queue_file_text(title, desc))
            count += 1
    else:
        # No queue-files.list: one queue per agent the plugin names (an
        # unknown role is counted but gets no file), plus the intake queues.
        for agent in agents.split(","):
            agent = agent.replace(" ", "")
            if not agent:
                continue
            if agent in _AGENT_QUEUES:
                queue_file, title, desc = _AGENT_QUEUES[agent]
                _write(f"{queues}/{queue_file}", queue_file_text(title, desc))
            count += 1
        for queue_file, title, desc in _INTAKE_QUEUES:
            _write(f"{queues}/{queue_file}", queue_file_text(title, desc))
            count += 1
    _say(f"  + seeded {count} queue files")

    queue_file, title, desc = _OVERWATCH_QUEUE
    _write(f"{queues}/{queue_file}", queue_file_text(title, desc))
    _say("  + seeded overwatch_backlog.md")

    for source, target in _TEMPLATE_COPIES:
        _copy(f"{template_dir}/{source}", f"{project_dir}/{target}")
    _say("  + seeded 3 templates")
    for source, target in _README_COPIES:
        _copy(f"{template_dir}/{source}", f"{project_dir}/{target}")
    _say("  + seeded 3 README.md files")
    if brief:
        _copy(f"{template_dir}/BRIEF-EXAMPLE.md", f"{project_dir}/brief-example.md")
        _say("  + seeded brief-example.md")
    _copy(f"{template_dir}/INTENT-TEMPLATE.md", f"{project_dir}/INTENT.md")
    _say("  + seeded INTENT.md")
    if readme_source:
        _copy(readme_source, f"{project_dir}/{_PROJECT_README}")
        _say("  + seeded README.md from the project README template")

    _seed_release_state(f"{project_dir}/release-state.md")
    _say("  + seeded release-state.md (includes Last Released field)")

    registry.ensure()
    _prepare_registry_format(registry, registry.format(), no_migrate)
    if not prio:
        prio = str(registry.next_priority())
    if not colour:
        colour = registry.next_color()
    registry.add(name, prio, colour)
    final_priority = registry.priority(name) or ""
    final_color = registry.color(name) or ""
    _say(
        f"  + registered in projects.cfg (priority={final_priority}, color={final_color})",
        "",
        f"Done. Project '{name}' is ready (max_patch={ceilings[0][1]}, "
        f"max_minor={ceilings[1][1]}, max_major={ceilings[2][1]}).",
        "",
        "Next steps:",
        "  1. Edit project.cfg to set dev_tree_path and git_repo_url:",
        f"     $EDITOR {project_dir}/project.cfg",
        "  2. Fill in the project README -- this project's test runners, lints, coding",
        "     standard and contracts, which every agent reads after its role file:",
        f"     $EDITOR {project_dir}/{_PROJECT_README}",
        "  3. Push the chain's base branches to origin (run once, before first release):",
        f"     init-project-git-repo.sh --project {name}",
        f"  4. Drop requirements docs in: {project_dir}/requirements/",
        "  5. The chain runs immediately (ceilings are operator checkpoints).",
        f"     To raise or lower a ceiling: set-version-ceiling.sh --project {name} --minor <N>",
    )
    return ProjectOutcome(
        "create_project", name, pathlib.Path(project_dir),
        changed=True, priority=final_priority, color=final_color,
    )


# ---------------------------------------------------------------------------
# add_project
# ---------------------------------------------------------------------------


def add_project(
    ctx: OpsContext,
    project: Optional[str],
    *,
    priority: Value = None,
    color: Optional[str] = None,
    no_migrate: bool = False,
    dry_run: bool = False,
    scripts_dir: Optional[PathLike] = None,
) -> ProjectOutcome:
    """Register an existing ``projects/<project>/`` directory in projects.cfg.

    Checks, each refusing before anything is written: *color* given is
    ``#RRGGBB`` (exit 1), *project* is given (exit 1) and passes
    :func:`check_project_name` before any filesystem access (exit 1,
    ``add_project.project_invalid``), and ``projects/<project>/`` exists
    (exit 2).

    A project not yet registered is added at *priority* (default: the next
    free one) with *color* (default: the next unused palette entry). A
    registered project is updated only when *priority* differs from its
    current one. projects.cfg is created with its INI header when missing
    -- with *dry_run* too, which otherwise only prints the plan -- and a
    colon-legacy registry is migrated to INI before writing unless
    *no_migrate*.

    A project directory with no ``README.md`` gets one seeded from
    :func:`project_readme_template`, registered already or not, with a line
    telling the operator to fill it in; an existing ``README.md`` is never
    touched.

    A project directory with neither ``project.cfg`` nor a legacy
    ``PROJECT.cfg`` gets ``project.cfg`` rendered from the project.cfg
    template (:func:`render_project_cfg`, the template's own defaults and
    this project's name), registered already or not, narrated in one line;
    an existing project.cfg or PROJECT.cfg -- any directory entry at that
    path, a dangling link included -- is never touched, and the file is
    created exclusively, so one appearing meanwhile is left as it is.
    Nothing else under the project directory is written. A registered
    project at its current priority with a README and a project config
    already present has nothing to do.

    Args:
        ctx:         Kanban root.
        project:     Project name (the directory under ``projects/``).
        priority:    Registry priority, written as given.
        color:       Registry colour.
        no_migrate:  Append to a colon-legacy registry instead of migrating it.
        dry_run:     Print the plan only.
        scripts_dir: The caller's ``scripts/`` directory; the project README
                     template is read from ``../templates/project/`` beside
                     it. Default: this package's own tree.

    Returns:
        The :class:`ProjectOutcome`.

    Raises:
        ProjectRefusal: A check above failed.
        IoError:        projects.cfg, the README, the project.cfg template or
                        project.cfg could not be read or written.
    """
    name = project or ""
    prio = _text(priority) or ""
    colour = ""
    if color is not None:
        _check_color("--color", color)
        colour = color
    if not name:
        raise ProjectRefusal(f"ERROR: project name is required (--project <name>)\n{_ADD_USAGE}")

    root = str(ctx.kanban_root)
    check_project_name("add_project", name, root)
    check_priority("add_project", prio, name)
    project_dir = f"{root}/projects/{name}"
    if not os.path.isdir(project_dir):
        raise ProjectRefusal(
            f"ERROR: project directory does not exist: {project_dir}\n"
            f"       To create a new project, use: create-project.sh {name}",
            exit_code=2,
        )

    scripts = str(scripts_dir) if scripts_dir is not None else str(_OWN_TREE / "scripts")
    cfg_target = f"{project_dir}/project.cfg"
    cfg_text: Optional[str] = None
    if not any(os.path.lexists(f"{project_dir}/{cfg}") for cfg in _PROJECT_CFG_NAMES):
        cfg_text = render_project_cfg(name=name, generated_by="add-project.sh")
    registry = _Registry(root)
    if not colour and not registry.has(name):
        colour = registry.next_color()
    registry.ensure()
    fmt = registry.format()

    _say(f"Registering project: {name}", f"  Project root: {project_dir}")
    readme_source = _project_readme_source(project_dir, scripts)
    readme_target = f"{project_dir}/{_PROJECT_README}"
    register = True
    if registry.has(name):
        current = registry.priority(name)
        if current is None:
            raise ProjectRefusal(
                f"ERROR: project '{name}' is registered in {registry.path} without a priority line.\n"
                f"       Add 'priority=N' under [project:{name}] and re-run add-project.sh."
            )
        if prio and current != prio:
            _say(f"  Already registered at priority={current}; will update to {prio}")
        elif readme_source is None and cfg_text is None:
            _say(f"  Already registered at priority={current}; nothing to do.")
            return ProjectOutcome("add_project", name, pathlib.Path(project_dir), dry_run=dry_run, priority=current)
        else:
            _say(f"  Already registered at priority={current}; registry unchanged.")
            register = False
    else:
        _say(
            f"  Will register at priority={prio or '<next available>'}, "
            f"color={colour or '<next available>'}",
            _registry_format_line(fmt, no_migrate),
        )
    if readme_source:
        _say("  Will seed README.md from the project README template (none present)")
    if cfg_text is not None:
        _say("  Will write project.cfg from the project.cfg template (none present)")
    _say("")

    if dry_run:
        _say("[DRY-RUN] No changes will be made.")
        return ProjectOutcome("add_project", name, pathlib.Path(project_dir), dry_run=True)

    if readme_source:
        _copy(readme_source, readme_target)
        _say(
            "  + seeded README.md from the project README template; fill in this project's rules:",
            f"     $EDITOR {readme_target}",
        )
    if cfg_text is not None:
        if _write_new(cfg_target, cfg_text):
            _say(
                "  + wrote project.cfg from the project.cfg template (none present); "
                "set dev_tree_path and git_repo_url in it"
            )
        else:
            _say("  project.cfg appeared meanwhile; left as it is")
    if not register:
        _say("", "Done.")
        return ProjectOutcome(
            "add_project", name, pathlib.Path(project_dir),
            changed=True, priority=registry.priority(name) or "", color=registry.color(name) or "",
        )
    _prepare_registry_format(registry, fmt, no_migrate)
    registry.add(name, prio, colour)
    final_priority = registry.priority(name) or ""
    final_color = registry.color(name) or ""
    _say(f"  + registered (priority={final_priority}, color={final_color})", "", "Done.")
    return ProjectOutcome(
        "add_project", name, pathlib.Path(project_dir),
        changed=True, priority=final_priority, color=final_color,
    )


# ---------------------------------------------------------------------------
# remove_project
# ---------------------------------------------------------------------------


def _contract_message(exc: Exception) -> str:
    """The ``message`` of the dual-channel block *exc* carries, else its text."""
    contract = parse_error_contract(str(exc))
    return contract["message"] if contract else str(exc)


def _refuse_active_rc(name: str, release_state: pathlib.Path) -> None:
    """Refuse removing a project that has a release in flight, whatever *force* says.

    The Active RC is read through ``cm.read_state_field`` (validated): an
    RC recorded there, or a field that does not validate, refuses with exit
    code 2 before anything is changed.
    """
    retry = f"team/scripts/remove-project.sh --project {name}"
    try:
        active_rc = read_release_state_field(str(release_state), "Active RC")
    except ReleaseStateValidationError as exc:
        # BLOCK-WORTHY (docs/blocking-site-census.md): an Active RC field
        # that does not validate may be hiding a release in flight, and
        # removing the project would discard it -- refuse rather than guess.
        # CENSUS-ANCHOR: projects-remove-active-rc-invalid-01
        raise ProjectRefusal(
            format_error_contract(
                "remove_project.release_state_invalid",
                f"remove-project.sh: cannot tell whether project '{name}' has a release in "
                f"flight: {_contract_message(exc)}. Nothing was changed.",
                f"Set the Active RC field in {release_state} to none (or to the open "
                f"RC's version, e.g. v1.2.3), then re-run {retry}",
            ),
            exit_code=2,
        ) from exc
    if active_rc == "none":
        return
    # BLOCK-WORTHY (docs/blocking-site-census.md): removing a project with a
    # release in flight discards the RC's work and leaves its branch and
    # release-state orphaned; the guard holds with or without --force.
    # CENSUS-ANCHOR: projects-remove-active-rc-01
    raise ProjectRefusal(
        format_error_contract(
            "remove_project.active_rc",
            f"remove-project.sh: project '{name}' has Active RC = {active_rc}; a project with a "
            "release in flight is not removed, with or without --force. Nothing was changed.",
            f"Ship or cancel {active_rc} first, e.g. team/scripts/cm/cancel-rc.sh --project {name} "
            f"--key {active_rc} --yes, then re-run {retry}",
        ),
        exit_code=2,
    )


def remove_project(
    ctx: OpsContext,
    project: Optional[str],
    *,
    force: bool = False,
    dry_run: bool = False,
) -> ProjectOutcome:
    """Unregister a project from projects.cfg; with *force* also delete its directory.

    Refuses, before anything is changed: no *project* (exit 1), a
    *project* :func:`check_project_name` refuses -- before any filesystem
    access, so ``..`` never reaches ``shutil.rmtree`` (exit 1,
    ``remove_project.project_invalid``) --, and a
    project whose ``release-state.md`` records an Active RC -- or whose
    Active RC field does not validate -- with or without *force* (exit 2,
    dual-channel ``remove_project.active_rc`` /
    ``remove_project.release_state_invalid``).

    Without *force* the project directory is left in place. Removing a
    project that is not registered is a no-op for the registry, so a second
    call succeeds. A colon-legacy projects.cfg (or a missing one) gets a
    warning suggesting migration.

    Args:
        ctx:     Kanban root.
        project: Project name. Not resolved by prefix: a project no longer
                 registered must still be nameable.
        force:   Also delete ``projects/<project>/``.
        dry_run: Print the plan only.

    Returns:
        The :class:`ProjectOutcome` (``deleted`` when the directory went).

    Raises:
        ProjectRefusal: No project, a project name that is not one
                        registrable name, or an Active RC.
        IoError:        projects.cfg could not be rewritten or the directory
                        could not be deleted.
    """
    name = project or ""
    if not name:
        raise ProjectRefusal(f"ERROR: project name is required (--project <name>)\n{_REMOVE_USAGE}")

    root = str(ctx.kanban_root)
    check_project_name("remove_project", name, root)
    project_dir = f"{root}/projects/{name}"
    _refuse_active_rc(name, ctx.project_root(name) / "release-state.md")

    registry = _Registry(root)
    fmt = registry.format()
    dir_exists = os.path.isdir(project_dir)
    _say(f"Removing project: {name}")
    if force:
        if dir_exists:
            _say(f"  - will DELETE: {project_dir}")
        else:
            _say(f"  - directory absent: {project_dir} (skipping delete)")
    else:
        _say(f"  - directory will be left in place: {project_dir}", "  - to fully delete, re-run with --force")
    registered = registry.has(name)
    if registered:
        if fmt == "colon-legacy":
            _say("  - will unregister from projects.cfg (colon-legacy: removes matching line)")
        else:
            _say(f"  - will unregister from projects.cfg (INI: removes [project:{name}] section)")
    else:
        _say("  - already absent from projects.cfg")
    _say("")

    if dry_run:
        _say("[DRY-RUN] No changes will be made.")
        return ProjectOutcome("remove_project", name, pathlib.Path(project_dir), dry_run=True)

    if fmt == "colon-legacy":
        _warn(
            "WARNING: projects.cfg is in colon-legacy format.",
            f"WARNING: Removal will delete the matching colon-format line for '{name}'.",
            "WARNING: Consider migrating to INI format: scripts/migrate/projects-cfg.sh",
        )
    registry.remove(name)
    _say("  + projects.cfg updated")

    deleted = False
    if force and os.path.isdir(project_dir):
        try:
            shutil.rmtree(project_dir)
        except OSError as exc:
            raise IoError(f"ERROR: cannot delete {project_dir}: {exc}") from exc
        deleted = True
        _say(f"  + deleted: {project_dir}")
    _say("", "Done.")
    return ProjectOutcome(
        "remove_project", name, pathlib.Path(project_dir), changed=True, deleted=deleted
    )


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def _cli_parser(verb: str) -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog=f"python3 -m pgai_agent_kanban.ops {verb}", add_help=False)
    parser.add_argument(
        "--kanban-root",
        required=True,
        help="kanban root holding projects.cfg and projects/ (required)",
    )
    parser.add_argument("--project", default="", help="the project to create, add or remove")
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="print the plan only; write nothing",
    )
    if verb in ("create_project", "add_project"):
        parser.add_argument(
            "--scripts-dir",
            help=(
                "the caller's scripts/ directory; templates are read beside it (default: this "
                "package's own tree)"
            ),
        )
    if verb == "create_project":
        for option, text in (
            ("--workflow-type", "workflow plugin for the new project (default: release)"),
            ("--max-patch", f"project.cfg patch ceiling, a non-negative integer (default: {DEFAULT_MAX_PATCH})"),
            ("--max-minor", f"project.cfg minor ceiling, a non-negative integer (default: {DEFAULT_MAX_MINOR})"),
            ("--max-major", f"project.cfg major ceiling, a non-negative integer (default: {DEFAULT_MAX_MAJOR})"),
            ("--git-remote", f"project.cfg git_remote_name (default: {DEFAULT_GIT_REMOTE})"),
            ("--dev-tree", "project.cfg dev_tree_path, an absolute path (default: empty)"),
            ("--git-repo", "project.cfg git_repo_url (default: empty)"),
        ):
            parser.add_argument(option, help=text)
    if verb in ("create_project", "add_project"):
        parser.add_argument("--priority", help="registry priority (default: the next free one)")
        parser.add_argument(
            "--color",
            help="registry colour as #RRGGBB (default: the next unused palette entry)",
        )
        parser.add_argument(
            "--no-migrate",
            action="store_true",
            help="append to a colon-legacy projects.cfg instead of migrating it to INI",
        )
    if verb == "remove_project":
        parser.add_argument(
            "--force",
            action="store_true",
            help=(
                "also delete the project's directory under projects/ (default: the directory is "
                "kept)"
            ),
        )
    return parser


def _cli_main(argv: Optional[list[str]] = None) -> int:
    """Run one registry verb from the command line (see the module docstring).

    Prints what the verb prints; a refusal goes to stderr (a dual-channel
    one through the shared emitter) and the exit code is
    :func:`projects_exit_code`'s.
    """
    from pgai_agent_kanban.ops.write import _print_refusal

    args = list(sys.argv[1:] if argv is None else argv)
    if not args or args[0] not in CLI_VERBS:
        print(f"projects: VERB must be one of: {', '.join(CLI_VERBS)}", file=sys.stderr)
        return 1
    verb = args[0]
    opts = _cli_parser(verb).parse_args(args[1:])
    ctx = OpsContext(kanban_root=pathlib.Path(opts.kanban_root))
    try:
        if verb == "create_project":
            create_project(
                ctx, opts.project,
                workflow_type=opts.workflow_type, max_patch=opts.max_patch,
                max_minor=opts.max_minor, max_major=opts.max_major,
                git_remote=opts.git_remote, priority=opts.priority, color=opts.color,
                dev_tree=opts.dev_tree, git_repo=opts.git_repo,
                no_migrate=opts.no_migrate, dry_run=opts.dry_run,
                scripts_dir=opts.scripts_dir,
            )
        elif verb == "add_project":
            add_project(
                ctx, opts.project, priority=opts.priority, color=opts.color,
                no_migrate=opts.no_migrate, dry_run=opts.dry_run,
                scripts_dir=opts.scripts_dir,
            )
        else:
            remove_project(ctx, opts.project, force=opts.force, dry_run=opts.dry_run)
    except OpsError as exc:
        sys.stdout.flush()
        _print_refusal(exc)
        return projects_exit_code(exc)
    return 0
