"""
seeded_templates.py — keep each registered project's seeded intake templates
current with the templates the installed release ships.

``create_project`` (``ops/projects.py``) copies the intake templates in
``_TEMPLATE_COPIES`` -- ``bugs/templates/BUG-TEMPLATE.md``,
``priority/templates/PRIORITY-TEMPLATE.md`` and
``requirements/templates/REQUIREMENTS-TEMPLATE.md`` -- into a new project
once. Filers copy the structure of those per-project files, so a copy that
is never refreshed falls behind every change to the shipped template.
:func:`refresh_seeded_templates` brings them back in line. ``install.sh``
(over an existing install) and ``team/scripts/upgrade.sh`` run it once
after the kanban tree is deposited.

What one run does, per registered project
-----------------------------------------
The template source is the one ``create_project`` seeds from:
``templates/project/<workflow_type>/`` of the tree this module is installed
in (the release set when the workflow has none of its own), where
``workflow_type`` is read from the project's ``project.cfg`` (``release``
when unset). Then, for each ``_TEMPLATE_COPIES`` entry:

- **byte-identical** to the installed template -- left alone, nothing printed;
- **missing** -- seeded from the installed template;
- **different** -- the existing copy is kept beside it as
  ``<file>.<UTC timestamp>.bak`` and the copy is overwritten with the
  installed template. One line names the project, the file and the backup.

A differing copy is never skipped: the refresh cannot tell an operator's
edit from a copy seeded by an older release, and skipping would leave the
stale copies this exists to fix. The backup keeps any edit recoverable,
and its ``.bak`` suffix keeps it out of every ``*.md`` scan.

Then the project README template check. One installed template,
``templates/project/README-TEMPLATE.md`` (``project_readme_template``),
serves every workflow type, and it is compared with the project's
snapshot ``.readme-template.md`` (``PROJECT_README_SNAPSHOT``, which
``create_project`` writes when it seeds the README):

- **identical** -- nothing written, nothing printed;
- **different** -- the old snapshot is moved aside as
  ``.readme-template.<UTC timestamp>.md`` (never over an existing file),
  the installed template becomes the snapshot, and one stdout line names
  the project with a ``diff -u <old snapshot> <installed template>``
  command, absolute paths quoted, that runs as printed. The next run finds
  them identical, so the notice appears once per template change;
- **no snapshot** -- the installed template is stored as one, with no
  per-project line; the closing summary line counts it.

The check runs even for a project whose intake templates were skipped for
want of a template directory. It is informational: a snapshot that
cannot be read or written, or a missing installed README template, is
narrated to stderr, and neither stops the intake refresh nor affects the
exit status or the closing summary line. A snapshot stored or moved aside
is counted in the closing summary line, so a run that wrote one never
closes by saying nothing changed.

Nothing else under a project is created, changed or removed: the intake
copies, their backups, the snapshot and its moved-aside copies are the
only files written. In particular the project README
(``projects/<name>/README.md``), which ``create_project`` and
``add_project`` seed once from ``templates/project/README-TEMPLATE.md``,
is never refreshed, and the template check never opens, reads or compares
it: it holds the operator's own project rules, not a copy of the
template; applying a template change to it is the operator's choice. A
project whose directory is missing, an unusable project name, or a
missing installed intake template is narrated to stderr and skipped; the
other projects still run. A run that writes no intake copy and no
snapshot prints a single no-op line.

Live-install self-containment
-----------------------------
Templates are read from this module's own tree (``<tree>/templates/``):
in a live install that is the install root, so the refresh never reads a
dev-tree checkout. Callers in a live install reach this module through the
installed ``scripts/lib/pp_run_ops.sh``.

CLI (``python3 -m pgai_agent_kanban.ops refresh_seeded_templates ...``;
routed here by ``ops/__main__.py``)::

    refresh_seeded_templates --kanban-root=ROOT [--scripts-dir=DIR] [--dry-run]

``--scripts-dir`` names the ``scripts/`` directory whose sibling
``templates/project/`` is read (default: this package's own tree).
``--dry-run`` prints what would change and writes nothing, the snapshot
included. Exit 0 when every project was handled (changes or not), 1 when
any intake copy could not be written or any project was skipped; the
README template check never changes the exit status.
"""

from __future__ import annotations

import argparse
import dataclasses
import datetime
import os
import pathlib
import re
import shutil
import sys
from typing import Optional

from pgai_agent_kanban.lib import ini
from pgai_agent_kanban.ops.projects import (
    _OWN_TREE,
    _TEMPLATE_COPIES,
    DEFAULT_WORKFLOW,
    PROJECT_README_SNAPSHOT,
    PathLike,
    _copy,
    _say,
    _warn,
    project_readme_template,
    registered_project_names,
    workflow_template_dir,
)
from pgai_agent_kanban.ops.errors import IoError

__all__ = ["CLI_VERBS", "SeededTemplateRefresh", "refresh_seeded_templates"]

#: The ops CLI verbs this module serves (``ops/__main__.py`` routes them here).
CLI_VERBS: tuple[str, ...] = ("refresh_seeded_templates",)

# Every line this module prints starts with this, so the narration is easy
# to pick out of install.sh / upgrade.sh output.
_PREFIX = "Seeded templates:"

# A project name that is safe to join under projects/ (no separators, no ..).
_SAFE_NAME_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9_-]*")


@dataclasses.dataclass
class SeededTemplateRefresh:
    """What one :func:`refresh_seeded_templates` run did (or, dry, would do).

    Attributes:
        projects:  Registered project names that were examined.
        refreshed: ``(project, target, backup)`` for each differing copy
                   overwritten; *target* and *backup* are relative to the
                   project directory.
        seeded:    ``(project, target)`` for each missing copy written.
        skipped:   ``(project, reason)`` for each project or file not handled.
        readme_notices:   ``(project, diff command)`` for each project whose
                          README template snapshot differed from the
                          installed template (dry: would differ).
        readme_snapshots: Projects that had no snapshot and were given one
                          (dry: would be).
        readme_problems:  stderr lines for each README template check that
                          could not complete. Informational: not a skip,
                          and never part of the exit status.
        dry_run:   True when nothing was written.
    """

    projects: list[str] = dataclasses.field(default_factory=list)
    refreshed: list[tuple[str, str, str]] = dataclasses.field(default_factory=list)
    seeded: list[tuple[str, str]] = dataclasses.field(default_factory=list)
    skipped: list[tuple[str, str]] = dataclasses.field(default_factory=list)
    readme_notices: list[tuple[str, str]] = dataclasses.field(default_factory=list)
    readme_snapshots: list[str] = dataclasses.field(default_factory=list)
    readme_problems: list[str] = dataclasses.field(default_factory=list)
    dry_run: bool = False

    @property
    def changed(self) -> bool:
        """True when any intake copy was (or, dry, would be) written; the README snapshot is not counted."""
        return bool(self.refreshed or self.seeded)


def _project_workflow(project_dir: str) -> str:
    """The project's ``[project] workflow_type`` as the bash readers read it, else ``release``."""
    value = ini.read_line_value(f"{project_dir}/project.cfg", "project", "workflow_type")
    return value or DEFAULT_WORKFLOW


def _backup_path(target: str, stamp: str) -> str:
    """A backup name beside *target* that does not exist yet."""
    candidate = f"{target}.{stamp}.bak"
    counter = 1
    while os.path.lexists(candidate):
        candidate = f"{target}.{stamp}.{counter}.bak"
        counter += 1
    return candidate


def _read_bytes(path: str) -> Optional[bytes]:
    """The bytes at *path*, or None when it is not a readable regular file."""
    try:
        with open(path, "rb") as handle:
            return handle.read()
    except OSError:
        return None


def _refresh_project(
    name: str,
    project_dir: str,
    template_dir: str,
    workflow: str,
    stamp: str,
    result: SeededTemplateRefresh,
) -> None:
    """Bring one project's ``_TEMPLATE_COPIES`` in line with *template_dir*."""
    verb_refresh = "would refresh" if result.dry_run else "refreshed"
    verb_seed = "would seed" if result.dry_run else "seeded"
    for source_name, target_rel in _TEMPLATE_COPIES:
        source = f"{template_dir}/{source_name}"
        target = f"{project_dir}/{target_rel}"
        wanted = _read_bytes(source)
        if wanted is None:
            reason = f"installed template {source} not found; {target_rel} left as it is"
            result.skipped.append((name, reason))
            _warn(f"{_PREFIX} project '{name}': skipped {target_rel}; {reason}")
            continue
        if os.path.isfile(target) and _read_bytes(target) == wanted:
            continue
        try:
            if os.path.lexists(target):
                backup = _backup_path(target, stamp)
                backup_rel = os.path.relpath(backup, project_dir)
                if not result.dry_run:
                    shutil.copy2(target, backup)
                    _copy(source, target)
                result.refreshed.append((name, target_rel, backup_rel))
                _say(
                    f"{_PREFIX} project '{name}': {verb_refresh} {target_rel} from the installed "
                    f"{workflow} template; the previous copy differed and is kept as {backup_rel}"
                )
            else:
                if not result.dry_run:
                    os.makedirs(os.path.dirname(target), exist_ok=True)
                    _copy(source, target)
                result.seeded.append((name, target_rel))
                _say(f"{_PREFIX} project '{name}': {verb_seed} missing {target_rel} from the installed {workflow} template")
        except (OSError, IoError) as exc:
            reason = f"could not write {target_rel}: {exc}"
            result.skipped.append((name, reason))
            _warn(f"{_PREFIX} project '{name}': {reason}")


def _shell_quote(path: str) -> str:
    """*path* in single quotes, so a printed command runs as printed when pasted into a POSIX shell."""
    return "'" + path.replace("'", "'\"'\"'") + "'"


def _rotated_snapshot_path(snapshot: str, stamp: str) -> str:
    """A ``.readme-template.<stamp>.md`` name beside *snapshot* that does not exist yet."""
    stem, suffix = os.path.splitext(snapshot)
    candidate = f"{stem}.{stamp}{suffix}"
    counter = 1
    while os.path.lexists(candidate):
        candidate = f"{stem}.{stamp}.{counter}{suffix}"
        counter += 1
    return candidate


def _readme_problem(name: str, text: str, result: SeededTemplateRefresh) -> None:
    """Narrate a README template check that could not complete (stderr; informational only)."""
    line = f"{_PREFIX} project '{name}': {text}; README.md is left as it is"
    result.readme_problems.append(line)
    _warn(line)


def _check_readme_template(
    name: str,
    project_dir: str,
    installed: str,
    wanted: bytes,
    stamp: str,
    result: SeededTemplateRefresh,
) -> None:
    """Compare one project's README template snapshot with the installed template *installed*.

    *wanted* is the installed template's bytes. Only the snapshot and its
    moved-aside copy are touched; the project's ``README.md`` is not.
    """
    snapshot = os.path.abspath(f"{project_dir}/{PROJECT_README_SNAPSHOT}")
    if not os.path.lexists(snapshot):
        if not result.dry_run:
            try:
                _copy(installed, snapshot)
            except IoError as exc:
                _readme_problem(name, f"could not store the project README template snapshot: {exc}", result)
                return
        result.readme_snapshots.append(name)
        return
    current = _read_bytes(snapshot)
    if current is None:
        _readme_problem(name, f"could not read the project README template snapshot {snapshot}", result)
        return
    if current == wanted:
        return
    rotated = _rotated_snapshot_path(snapshot, stamp)
    if result.dry_run:
        command = f"diff -u {_shell_quote(snapshot)} {_shell_quote(installed)}"
        result.readme_notices.append((name, command))
        _say(
            f"{_PREFIX} project '{name}': the project README template changed since README.md was seeded "
            f"or last checked; would keep the old snapshot as {os.path.basename(rotated)}; "
            f"README.md is not changed; see the change: {command}"
        )
        return
    try:
        os.rename(snapshot, rotated)
    except OSError as exc:
        command = f"diff -u {_shell_quote(snapshot)} {_shell_quote(installed)}"
        _readme_problem(
            name,
            f"the project README template changed, but the old snapshot could not be moved aside as "
            f"{rotated}: {exc}; see the change: {command}",
            result,
        )
        return
    command = f"diff -u {_shell_quote(rotated)} {_shell_quote(installed)}"
    result.readme_notices.append((name, command))
    _say(
        f"{_PREFIX} project '{name}': the project README template changed since README.md was seeded "
        f"or last checked; README.md is not changed; see the change: {command}"
    )
    try:
        _copy(installed, snapshot)
    except IoError as exc:
        _readme_problem(name, f"could not store the new project README template snapshot: {exc}", result)


def _snapshot_clause(result: SeededTemplateRefresh) -> str:
    """The README template snapshot writes of *result* as a closing-line clause, or "" when there were none."""
    moved = "would move aside" if result.dry_run else "moved aside"
    stored = "would store" if result.dry_run else "stored"
    parts = []
    if result.readme_notices:
        parts.append(f"{moved} {len(result.readme_notices)} changed")
    if result.readme_snapshots:
        parts.append(f"{stored} {len(result.readme_snapshots)} missing")
    if not parts:
        return ""
    return f"{' and '.join(parts)} project README template snapshot(s) ({PROJECT_README_SNAPSHOT})"


def _closing_line(result: SeededTemplateRefresh) -> str:
    """The one summary line a run ends with.

    It counts the intake copies written and, when any README template
    snapshot was stored or moved aside, those too, so it never says that
    nothing changed or nothing was written on a run that wrote a snapshot.
    A run that wrote no snapshot gets the intake-only line unchanged.
    """
    count = len(result.projects)
    snapshots = _snapshot_clause(result)
    if result.changed:
        lead = "would refresh" if result.dry_run else "refreshed"
        seed = "would seed" if result.dry_run else "seeded"
        line = (
            f"{_PREFIX} {lead} {len(result.refreshed)} file(s) (each backed up first) and {seed} "
            f"{len(result.seeded)} across {count} registered project(s)"
        )
        return f"{line}; {snapshots}." if snapshots else f"{line}."
    if result.skipped:
        if snapshots:
            return f"{_PREFIX} no intake copy written; {len(result.skipped)} skip(s) reported above; {snapshots}."
        return f"{_PREFIX} nothing written; {len(result.skipped)} skip(s) reported above."
    if snapshots:
        return (
            f"{_PREFIX} all intake copies in {count} registered project(s) already match the installed "
            f"templates; {snapshots}."
        )
    return f"{_PREFIX} all copies in {count} registered project(s) already match the installed templates; nothing changed."


def refresh_seeded_templates(
    kanban_root: PathLike,
    *,
    scripts_dir: Optional[PathLike] = None,
    dry_run: bool = False,
    now: Optional[datetime.datetime] = None,
) -> SeededTemplateRefresh:
    """Refresh every registered project's seeded intake templates.

    See the module docstring for the per-file policy and the README
    template check. Prints one line per intake copy written (or, with
    *dry_run*, that would be), one notice per project whose README template
    snapshot differed, a stderr line per skip or failed README check, and
    a closing summary of the intake copies and README template snapshots
    written -- a single no-op line when neither changed.

    Args:
        kanban_root: The kanban root whose ``projects.cfg`` and
                     ``projects/<name>/`` are refreshed.
        scripts_dir: The ``scripts/`` directory whose sibling
                     ``templates/project/`` is the template source.
                     Default: this package's own tree.
        dry_run:     Print what would change; write nothing.
        now:         The time backups and moved-aside snapshots are stamped
                     with (default: now, UTC).

    Returns:
        The :class:`SeededTemplateRefresh` describing the run.
    """
    root = str(kanban_root)
    scripts = str(scripts_dir) if scripts_dir is not None else str(_OWN_TREE / "scripts")
    stamp = (now or datetime.datetime.now(datetime.timezone.utc)).strftime("%Y%m%dT%H%M%SZ")
    result = SeededTemplateRefresh(dry_run=dry_run)

    names = registered_project_names(root)
    if not names:
        _say(f"{_PREFIX} no registered projects; nothing to refresh.")
        return result

    readme_installed = os.path.realpath(project_readme_template(scripts))
    readme_wanted = _read_bytes(readme_installed)
    if readme_wanted is None:
        line = (
            f"{_PREFIX} installed project README template {readme_installed} not found; "
            "project README template changes are not checked"
        )
        result.readme_problems.append(line)
        _warn(line)

    for name in names:
        result.projects.append(name)
        if not _SAFE_NAME_RE.fullmatch(name):
            reason = "the registered name is not a plain project directory name"
            result.skipped.append((name, reason))
            _warn(f"{_PREFIX} project '{name}': skipped; {reason}")
            continue
        project_dir = f"{root}/projects/{name}"
        if not os.path.isdir(project_dir):
            reason = f"project directory {project_dir} does not exist"
            result.skipped.append((name, reason))
            _warn(f"{_PREFIX} project '{name}': skipped; {reason}")
            continue
        workflow = _project_workflow(project_dir)
        template_dir, _fell_back = workflow_template_dir(scripts, workflow)
        if template_dir is None:
            reason = (
                f"no installed template directory for workflow '{workflow}' "
                f"and no release fallback under {scripts}/../templates/project"
            )
            result.skipped.append((name, reason))
            _warn(f"{_PREFIX} project '{name}': skipped; {reason}")
        else:
            _refresh_project(name, project_dir, template_dir, workflow, stamp, result)
        # The README template is shared by every workflow, so a project
        # skipped above for its intake templates is still checked.
        if readme_wanted is not None:
            _check_readme_template(name, project_dir, readme_installed, readme_wanted, stamp, result)

    _say(_closing_line(result))
    return result


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def _cli_main(argv: Optional[list[str]] = None) -> int:
    """Run ``refresh_seeded_templates`` from the command line (see the module docstring)."""
    args = list(sys.argv[1:] if argv is None else argv)
    if not args or args[0] not in CLI_VERBS:
        print(f"seeded_templates: VERB must be one of: {', '.join(CLI_VERBS)}", file=sys.stderr)
        return 1
    parser = argparse.ArgumentParser(
        prog=f"python3 -m pgai_agent_kanban.ops {args[0]}",
        description=(
            "Refresh every registered project's seeded intake templates from the installed "
            "templates (a differing copy is kept beside it as a timestamped .bak first), then "
            "check each project's README template snapshot (.readme-template.md) against the "
            "installed README-TEMPLATE.md. When the template changed, the old snapshot is moved "
            "aside, the installed template becomes the snapshot, and one notice line names the "
            "project with a runnable 'diff -u <old snapshot> <installed template>' command; "
            "a project with no snapshot is given one. The project README.md itself is never "
            "changed. Exit 0; 1 when a project was skipped."
        ),
    )
    parser.add_argument("--kanban-root", required=True, help="Kanban root holding projects.cfg and projects/.")
    parser.add_argument(
        "--scripts-dir",
        help="scripts/ directory whose sibling templates/project/ is read (default: this package's own tree).",
    )
    parser.add_argument("--dry-run", action="store_true", help="Print what would change; write nothing.")
    opts = parser.parse_args(args[1:])
    result = refresh_seeded_templates(
        pathlib.Path(opts.kanban_root), scripts_dir=opts.scripts_dir, dry_run=opts.dry_run,
    )
    return 1 if result.skipped else 0
