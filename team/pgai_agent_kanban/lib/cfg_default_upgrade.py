"""
cfg_default_upgrade.py — move an install's kanban.cfg onto changed defaults.

A built-in default only reaches an install whose kanban.cfg leaves the key
unset. An install whose kanban.cfg was copied from an older example carries
the previous default as an explicit line, so changing the default in code
changes nothing there. This module rewrites exactly those lines — a key that
still holds the previously shipped default — to the new default, states each
change with the one cfg line that reverts it, and leaves everything else
alone:

* a key holding the new default is reported as already applied;
* a key that is absent is reported as following the built-in default;
* a key holding any other value is an operator's own choice and is kept;
* every other line of the file keeps its bytes (the rewritten line is
  written in the canonical ``key = value`` form, and a file without a final
  newline still has none afterwards);
* a project's project.cfg is never written. :func:`project_values` only
  reports which projects set a key themselves, so the output can name them
  as keeping their own value.

Edits go through :func:`pgai_agent_kanban.lib.ini.set_line_value` (the one
line-preserving INI editor) and land with
:func:`pgai_agent_kanban.lib.atomic_write.atomic_write`, keeping the file's
permission bits. Values are read with the bash ``read_ini`` rules
(:func:`pgai_agent_kanban.lib.ini.read_line_value`), so this module sees the
same value the wake and release scripts act on.

Command line (what a migration script calls)::

    python3 -m pgai_agent_kanban.lib.cfg_default_upgrade --kanban-root DIR \\
        --change SECTION KEY PREVIOUS CURRENT MEANING [--change ...] \\
        [--project-key SECTION KEY ...] [--project NAME ...] \\
        [--rerun-command CMD] [--dry-run]

Exit codes: 0 success (including nothing to change), 2 usage error, 4
kanban.cfg could not be written (nothing was changed).

Public API:
    DefaultChange, ChangeOutcome, ProjectValue
    plan_changes(cfg_path, changes) -> list[ChangeOutcome]
    upgrade_cfg_defaults(cfg_path, changes, *, dry_run=False) -> list[ChangeOutcome]
    project_values(kanban_root, projects, section, key) -> list[ProjectValue]
    render_outcomes(cfg_path, outcomes, *, dry_run=False) -> list[str]
    render_project_values(values) -> list[str]
    main(argv=None) -> int
"""

from __future__ import annotations

import argparse
import dataclasses
import os
import pathlib
import shlex
import sys
from collections.abc import Iterable, Sequence

# Started by file path (python3 .../lib/cfg_default_upgrade.py) there is no
# parent package: put the package root this file sits in -- the directory
# holding pgai_agent_kanban/ -- at the front of sys.path so the imports below
# resolve from the same tree, never another. Imported as a package member,
# sys.path is left untouched.
if not __package__:
    _OWN_PACKAGE_ROOT = str(pathlib.Path(__file__).resolve().parents[2])
    if _OWN_PACKAGE_ROOT not in sys.path:
        sys.path.insert(0, _OWN_PACKAGE_ROOT)

from pgai_agent_kanban.lib import ini, project_cfg  # noqa: E402
from pgai_agent_kanban.lib.atomic_write import atomic_write  # noqa: E402
from pgai_agent_kanban.lib.error_contract import emit_error_contract  # noqa: E402

__all__ = [
    "ALREADY",
    "ABSENT",
    "CHANGED",
    "CUSTOM",
    "ChangeOutcome",
    "DefaultChange",
    "EXIT_OK",
    "EXIT_USAGE",
    "EXIT_WRITE_FAILED",
    "ProjectValue",
    "WOULD_CHANGE",
    "main",
    "plan_changes",
    "project_values",
    "render_outcomes",
    "render_project_values",
    "upgrade_cfg_defaults",
]

#: The key held the previous default and was rewritten to the new one.
CHANGED = "changed"
#: The key holds the previous default; a dry run left it as it is.
WOULD_CHANGE = "would-change"
#: The key already holds the new default.
ALREADY = "already"
#: The key (or the whole file) is absent; the built-in default applies.
ABSENT = "absent"
#: The key holds some other value, an operator's choice; it is kept.
CUSTOM = "custom"

EXIT_OK = 0
EXIT_USAGE = 2
EXIT_WRITE_FAILED = 4

_WRITE_PREFIX = ".cfg_default_upgrade_tmp_"


@dataclasses.dataclass(frozen=True)
class DefaultChange:
    """One default that changed: ``[section] key`` from *previous* to *current*.

    Attributes:
        section:  INI section, e.g. ``"release"``.
        key:      Key name, matched exactly.
        previous: The value the earlier default wrote; only a key holding
                  exactly this value is rewritten.
        current:  The new default the key is rewritten to.
        meaning:  One plain sentence saying what the new value does,
                  printed with the change. May be empty.
    """

    section: str
    key: str
    previous: str
    current: str
    meaning: str = ""

    @property
    def revert_line(self) -> str:
        """The one cfg line that puts the previous behaviour back."""
        return f"{self.key} = {self.previous}"


@dataclasses.dataclass(frozen=True)
class ChangeOutcome:
    """What happened to one :class:`DefaultChange` in one cfg file.

    Attributes:
        change: The change considered.
        status: One of :data:`CHANGED`, :data:`WOULD_CHANGE`,
                :data:`ALREADY`, :data:`ABSENT`, :data:`CUSTOM`.
        found:  The value the file held, or ``None`` when the key (or the
                file) is absent.
    """

    change: DefaultChange
    status: str
    found: str | None


@dataclasses.dataclass(frozen=True)
class ProjectValue:
    """A project's own project.cfg value for one key.

    Attributes:
        project: Project name.
        section: INI section read.
        key:     Key read.
        value:   The non-blank value project.cfg sets, or ``""`` when it
                 sets none (the project follows kanban.cfg).
        source:  The project.cfg (or legacy PROJECT.cfg) path, or ``""``
                 when the project has neither.
    """

    project: str
    section: str
    key: str
    value: str
    source: str


def plan_changes(cfg_path: str | os.PathLike[str], changes: Iterable[DefaultChange]) -> list[ChangeOutcome]:
    """Classify each change against *cfg_path* without writing anything.

    A key holding exactly ``change.previous`` is :data:`WOULD_CHANGE`; the
    new value is :data:`ALREADY`; a missing key or file is :data:`ABSENT`;
    anything else is :data:`CUSTOM`.
    """
    outcomes = []
    for change in changes:
        found = ini.read_line_value(cfg_path, change.section, change.key)
        if found is None or found == "":
            status = ABSENT
        elif found == change.previous:
            status = WOULD_CHANGE
        elif found == change.current:
            status = ALREADY
        else:
            status = CUSTOM
        outcomes.append(ChangeOutcome(change, status, found))
    return outcomes


def upgrade_cfg_defaults(
    cfg_path: str | os.PathLike[str],
    changes: Iterable[DefaultChange],
    *,
    dry_run: bool = False,
) -> list[ChangeOutcome]:
    """Rewrite every key in *cfg_path* still holding its previous default.

    Running it again finds every rewritten key at its new default and writes
    nothing. With *dry_run* nothing is written and the keys that would
    change are reported as :data:`WOULD_CHANGE`.

    Raises:
        OSError: the file could not be read or written; it is unchanged.
    """
    outcomes = plan_changes(cfg_path, changes)
    pending = [o for o in outcomes if o.status == WOULD_CHANGE]
    if dry_run or not pending:
        return outcomes

    path = pathlib.Path(cfg_path)
    original = path.read_text(encoding="utf-8")
    text = original
    applied = set()
    for outcome in pending:
        change = outcome.change
        try:
            text = ini.set_line_value(text, change.section, change.key, change.current, add=False)
        except ini.NoOptionError:
            # read_line_value found the key in a repeated section header the
            # editor does not reach; leave that line alone rather than add a
            # second one.
            continue
        applied.add(id(outcome))
    if original and not original.endswith("\n") and text.endswith("\n"):
        text = text[:-1]
    if text != original:
        atomic_write(path, text, prefix=_WRITE_PREFIX, preserve_mode=True)

    result = []
    for outcome in outcomes:
        if outcome.status != WOULD_CHANGE:
            result.append(outcome)
        elif id(outcome) in applied:
            result.append(dataclasses.replace(outcome, status=CHANGED))
        else:
            result.append(dataclasses.replace(outcome, status=CUSTOM))
    return result


def project_values(
    kanban_root: str | os.PathLike[str],
    projects: Iterable[str],
    section: str,
    key: str,
) -> list[ProjectValue]:
    """Report, per project, the value its own project.cfg sets for a key.

    Reads ``<kanban_root>/projects/<name>/project.cfg`` (or a legacy
    ``PROJECT.cfg``) by the same rules the per-project resolvers use. Never
    writes.
    """
    values = []
    for name in projects:
        cfg = project_cfg.project_cfg_file(pathlib.Path(kanban_root) / "projects" / name)
        value = project_cfg.read_cfg_key(cfg, section, key) if cfg is not None else ""
        values.append(ProjectValue(name, section, key, value, str(cfg) if cfg is not None else ""))
    return values


def _change_line(tag: str, outcome: ChangeOutcome) -> str:
    change = outcome.change
    line = f"  [{tag}]  [{change.section}] {change.key}: {change.previous} -> {change.current}"
    if change.meaning:
        line += f" -- {change.meaning}"
    return line


def render_outcomes(
    cfg_path: str | os.PathLike[str],
    outcomes: Sequence[ChangeOutcome],
    *,
    dry_run: bool = False,
) -> list[str]:
    """Return the operator-facing lines for *outcomes* in *cfg_path*.

    Every change made (or, under *dry_run*, that would be made) is stated
    with the one cfg line that reverts it; the last line says whether the
    file changed, would change, or was already applied.
    """
    cfg = str(cfg_path)
    lines = []
    if not pathlib.Path(cfg).is_file():
        lines.append(f"  [absent]  no kanban.cfg at {cfg} -- the built-in defaults apply, no change")
        lines.append(f"already applied: nothing to change in {cfg}")
        return lines

    changed = 0
    for outcome in outcomes:
        change = outcome.change
        if outcome.status in (CHANGED, WOULD_CHANGE):
            changed += 1
            lines.append(_change_line(outcome.status, outcome))
            lines.append(f"      revert: put this line under [{change.section}] in {cfg}: {change.revert_line}")
        elif outcome.status == ALREADY:
            lines.append(
                f"  [already]  [{change.section}] {change.key} = {change.current}" " -- already applied, no change"
            )
        elif outcome.status == ABSENT:
            lines.append(
                f"  [absent]  [{change.section}] {change.key} is not set"
                f" -- the built-in default {change.current} applies, no change"
            )
        else:
            lines.append(
                f"  [custom]  [{change.section}] {change.key} = {outcome.found}"
                f" -- not the previous default {change.previous}, left as set"
            )

    if changed and dry_run:
        lines.append(f"dry run: {changed} line(s) would change in {cfg}; nothing written")
    elif changed:
        lines.append(f"updated: {changed} line(s) changed in {cfg}")
    else:
        lines.append(f"already applied: nothing to change in {cfg}")
    return lines


def render_project_values(values: Iterable[ProjectValue]) -> list[str]:
    """Return one line per project: keeps its own value, or follows kanban.cfg."""
    lines = []
    for item in values:
        if item.value:
            lines.append(
                f"  [keeps]  {item.project}: project.cfg [{item.section}] {item.key} = {item.value}"
                " -- keeps its own value, not changed"
            )
        else:
            lines.append(
                f"  [inherits]  {item.project}: project.cfg sets no [{item.section}] {item.key}"
                " -- follows kanban.cfg"
            )
    return lines


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python3 -m pgai_agent_kanban.lib.cfg_default_upgrade",
        description=(
            "Rewrite kanban.cfg keys that still hold a previously shipped default to the "
            "new default, state each change with the cfg line that reverts it, and report "
            "which projects set the key in their own project.cfg (never written)."
        ),
    )
    parser.add_argument("--kanban-root", required=True, help="Kanban root holding kanban.cfg and projects/.")
    parser.add_argument(
        "--change",
        nargs=5,
        action="append",
        required=True,
        metavar=("SECTION", "KEY", "PREVIOUS", "CURRENT", "MEANING"),
        help=(
            "A changed default: rewrite [SECTION] KEY from exactly PREVIOUS to CURRENT. "
            "MEANING is one sentence printed with the change. Repeatable."
        ),
    )
    parser.add_argument(
        "--project-key",
        nargs=2,
        action="append",
        default=[],
        metavar=("SECTION", "KEY"),
        help="Report each --project whose project.cfg sets [SECTION] KEY itself. Repeatable.",
    )
    parser.add_argument(
        "--project",
        action="append",
        default=[],
        metavar="NAME",
        help="A project to report on for every --project-key. Repeatable.",
    )
    parser.add_argument(
        "--rerun-command",
        default="",
        metavar="CMD",
        help="The command that re-runs the caller, appended to the remedy when kanban.cfg cannot be written.",
    )
    parser.add_argument("--dry-run", action="store_true", help="Print what would change; write nothing.")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    """Command-line entry point; returns the exit code."""
    try:
        args = _build_parser().parse_args(argv)
    except SystemExit as exc:
        return EXIT_OK if exc.code in (0, None) else EXIT_USAGE

    changes = [DefaultChange(*spec) for spec in args.change]
    cfg_path = pathlib.Path(args.kanban_root) / "kanban.cfg"

    try:
        outcomes = upgrade_cfg_defaults(cfg_path, changes, dry_run=args.dry_run)
    except OSError as exc:
        # BLOCK-WORTHY: an upgrade that reports success while kanban.cfg still
        # holds the previous defaults leaves the install on the old behaviour
        # with nothing telling the operator; the file itself is unchanged.
        remedy = f"ls -ld {shlex.quote(str(cfg_path.parent))} {shlex.quote(str(cfg_path))}"
        if args.rerun_command:
            remedy += f" && {args.rerun_command}"
        emit_error_contract(
            "cfg_default_upgrade.write_failed",
            f"kanban.cfg at {cfg_path} could not be read or rewritten ({exc}); it was left unchanged.",
            remedy,
        )
        # CENSUS-ANCHOR: cfg-default-upgrade-write-01
        return EXIT_WRITE_FAILED

    for line in render_outcomes(cfg_path, outcomes, dry_run=args.dry_run):
        print(line)
    for section, key in args.project_key:
        for line in render_project_values(project_values(args.kanban_root, args.project, section, key)):
            print(line)
    return EXIT_OK


if __name__ == "__main__":
    sys.exit(main())
