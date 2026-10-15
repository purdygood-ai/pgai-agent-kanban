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

Nothing else under a project is created, changed or removed. In
particular the project README (``projects/<name>/README.md``), which
``create_project`` and ``add_project`` seed once from
``templates/project/README-TEMPLATE.md``, is never refreshed: it holds the
operator's own project rules, not a copy of the template. A project
whose directory is missing, an unusable project name, or a missing
installed template is narrated to stderr and skipped; the other projects
still run. A run that changes nothing prints a single no-op line.

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
``--dry-run`` prints what would change and writes nothing. Exit 0 when
every project was handled (changes or not), 1 when any file could not be
written or any project was skipped.
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
    PathLike,
    _copy,
    _say,
    _warn,
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
        dry_run:   True when nothing was written.
    """

    projects: list[str] = dataclasses.field(default_factory=list)
    refreshed: list[tuple[str, str, str]] = dataclasses.field(default_factory=list)
    seeded: list[tuple[str, str]] = dataclasses.field(default_factory=list)
    skipped: list[tuple[str, str]] = dataclasses.field(default_factory=list)
    dry_run: bool = False

    @property
    def changed(self) -> bool:
        """True when any copy was (or, dry, would be) written."""
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


def refresh_seeded_templates(
    kanban_root: PathLike,
    *,
    scripts_dir: Optional[PathLike] = None,
    dry_run: bool = False,
    now: Optional[datetime.datetime] = None,
) -> SeededTemplateRefresh:
    """Refresh every registered project's seeded intake templates.

    See the module docstring for the per-file policy. Prints one line per
    file written (or, with *dry_run*, that would be), a stderr line per
    skip, and a closing summary -- a single no-op line when nothing
    changed.

    Args:
        kanban_root: The kanban root whose ``projects.cfg`` and
                     ``projects/<name>/`` are refreshed.
        scripts_dir: The ``scripts/`` directory whose sibling
                     ``templates/project/`` is the template source.
                     Default: this package's own tree.
        dry_run:     Print what would change; write nothing.
        now:         The time backups are stamped with (default: now, UTC).

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
            continue
        _refresh_project(name, project_dir, template_dir, workflow, stamp, result)

    count = len(result.projects)
    if result.changed:
        lead = "would refresh" if dry_run else "refreshed"
        seed = "would seed" if dry_run else "seeded"
        _say(
            f"{_PREFIX} {lead} {len(result.refreshed)} file(s) (each backed up first) and {seed} "
            f"{len(result.seeded)} across {count} registered project(s)."
        )
    elif result.skipped:
        _say(f"{_PREFIX} nothing written; {len(result.skipped)} skip(s) reported above.")
    else:
        _say(f"{_PREFIX} all copies in {count} registered project(s) already match the installed templates; nothing changed.")
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
        description="Refresh every registered project's seeded intake templates from the installed templates.",
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
