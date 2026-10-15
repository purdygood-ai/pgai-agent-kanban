"""
push_decision.py — decide whether an out-of-band push site may publish a
project's refs to its remote.

Out-of-band push sites (the OVERWATCH push-lag check, the CM push watchdog)
push whatever a dev tree holds that its remote lacks. They act only on a
real, registered project's own configuration, so a sandbox root, a
half-built config, or an inherited ``PGAI_DEV_TREE_PATH`` can never steer
them at a real checkout. Every such site asks this module, so all of them
apply one rule.

The rule
--------
A project resolves only when all three hold:

1. it is registered in ``<kanban_root>/projects.cfg`` (a ``[project:<name>]``
   section, read through :func:`pgai_agent_kanban.lib.ini.projects_registry`);
2. its own config file exists — ``projects/<name>/project.cfg``, or a legacy
   ``PROJECT.cfg`` (:func:`pgai_agent_kanban.lib.project_cfg.project_cfg_file`);
3. that file names a ``dev_tree_path`` under ``[project]``. The dev tree is
   that value and nothing else; ``PGAI_DEV_TREE_PATH`` is never consulted.

A resolved project then reads ``[project] push_to_remote``: the exact value
``false`` (one surrounding double quote stripped from each side) is
``no-push``; absent, blank, ``true`` or any other value is ``push``. The
default for a resolved project is therefore unchanged — a missing key means
push.

The answer
----------
:func:`resolve_push_decision` returns a :class:`PushDecision` whose
``decision`` is exactly one of:

``push``        resolved; the site may push from ``dev_tree``.
``no-push``     resolved; ``push_to_remote = false`` — local-ahead state is
                by design and the site pushes nothing.
``unresolved``  a condition above failed. The site pushes nothing, contacts
                no remote, and logs ``status_line`` — one line with the
                status ``skipped-unresolved-project`` naming the missing
                piece (``missing`` is its stable token: ``registration``,
                ``project-cfg`` or ``dev-tree-path``).

The dev tree's existence on disk is not part of the decision; each site
keeps its own handling of a configured path that is absent.

CLI
---
Bash callers use the wrapper ``team/scripts/lib/push_decision.sh``
(``pp_push_decision``), which runs::

    python3 -m pgai_agent_kanban.lib.push_decision --kanban-root ROOT --project NAME

The CLI prints one ``key=value`` line per field (``decision``, ``project``,
``cfg_file``, ``dev_tree``, ``missing``, ``status_line``) and exits 0
whenever it reached a decision, ``unresolved`` included; 2 on a usage error.
"""

from __future__ import annotations

import argparse
import dataclasses
import pathlib
import re
import sys
from typing import Optional, Sequence, Union

# Run by file path (``python3 .../push_decision.py``) or loaded from a file
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

from pgai_agent_kanban.lib import ini, project_cfg  # noqa: E402

__all__ = [
    "PUSH",
    "NO_PUSH",
    "UNRESOLVED",
    "SKIP_STATUS",
    "PushDecision",
    "resolve_push_decision",
    "main",
]

PathLike = Union[str, pathlib.Path]

#: The site may push.
PUSH = "push"
#: push_to_remote = false: the site pushes nothing, by design.
NO_PUSH = "no-push"
#: The project did not resolve: the site pushes nothing and contacts no remote.
UNRESOLVED = "unresolved"

#: The status every site logs for an unresolved project.
SKIP_STATUS = "skipped-unresolved-project"

#: Missing-piece tokens carried in ``PushDecision.missing``.
MISSING_REGISTRATION = "registration"
MISSING_PROJECT_CFG = "project-cfg"
MISSING_DEV_TREE_PATH = "dev-tree-path"

# A project name a registry section can carry; anything else (a path
# separator, "..", whitespace) can never name a registered project.
_PROJECT_NAME_RE = re.compile(r"[A-Za-z0-9_-]+")


@dataclasses.dataclass(frozen=True)
class PushDecision:
    """One push-site decision for one project.

    Attributes:
        decision:    ``push``, ``no-push`` or ``unresolved``.
        project:     The project name asked about.
        cfg_file:    The project's own config file read; ``""`` when none
                     was found (or resolution stopped before looking).
        dev_tree:    ``dev_tree_path`` from that file; ``""`` unless resolved.
        missing:     ``""`` when resolved, else the stable token naming the
                     first failed condition.
        status_line: ``""`` when resolved, else the one log line a site
                     prints: ``skipped-unresolved-project: ...``.
    """

    decision: str
    project: str
    cfg_file: str = ""
    dev_tree: str = ""
    missing: str = ""
    status_line: str = ""

    @property
    def may_push(self) -> bool:
        """True only for ``push``."""
        return self.decision == PUSH

    def as_lines(self) -> list[str]:
        """The CLI's ``key=value`` lines, one per field, newlines flattened."""
        fields = (
            ("decision", self.decision),
            ("project", self.project),
            ("cfg_file", self.cfg_file),
            ("dev_tree", self.dev_tree),
            ("missing", self.missing),
            ("status_line", self.status_line),
        )
        return [f"{key}={_one_line(value)}" for key, value in fields]


def _one_line(value: str) -> str:
    return value.replace("\r", " ").replace("\n", " ")


def _unresolved(project: str, missing: str, detail: str, cfg_file: str = "") -> PushDecision:
    line = (
        f"{SKIP_STATUS}: project '{project}': missing {missing}: {detail}; "
        "nothing pushed and no remote contacted"
    )
    return PushDecision(
        decision=UNRESOLVED,
        project=project,
        cfg_file=cfg_file,
        missing=missing,
        status_line=line,
    )


def _registration_gap(root: pathlib.Path, project: str) -> Optional[str]:
    """Why *project* is not registered under *root*, or None when it is."""
    registry = root / "projects.cfg"
    if not registry.is_file():
        return f"{registry} does not exist, so no project is registered"
    try:
        names = {entry.name for entry in ini.projects_registry(root)}
    except ini.IniError as exc:
        first_line = str(exc).splitlines()[0] if str(exc) else type(exc).__name__
        return f"{registry} cannot be read as INI ({first_line})"
    if project not in names:
        return f"project is not registered in {registry} (no [project:{project}] section)"
    return None


def resolve_push_decision(kanban_root: PathLike, project: str) -> PushDecision:
    """Decide whether a push site may push *project*'s refs.

    Args:
        kanban_root: The kanban root holding ``projects.cfg`` and
                     ``projects/<name>/``.
        project:     The project name, exactly as registered.

    Returns:
        A :class:`PushDecision`. Never raises for a missing or unreadable
        file: every such case is an ``unresolved`` decision naming it.
    """
    name = project or ""
    if not _PROJECT_NAME_RE.fullmatch(name):
        return _unresolved(
            name,
            MISSING_REGISTRATION,
            f"{name!r} is not a project name a registry can hold (letters, digits, '_' and '-' only)",
        )

    root = pathlib.Path(kanban_root)
    gap = _registration_gap(root, name)
    if gap is not None:
        return _unresolved(name, MISSING_REGISTRATION, gap)

    project_root = root / "projects" / name
    cfg_path = project_cfg.project_cfg_file(project_root)
    if cfg_path is None:
        return _unresolved(
            name,
            MISSING_PROJECT_CFG,
            f"{project_root / 'project.cfg'} does not exist",
        )

    dev_tree = project_cfg.read_cfg_key(cfg_path, "project", "dev_tree_path").strip()
    if not dev_tree:
        return _unresolved(
            name,
            MISSING_DEV_TREE_PATH,
            f"{cfg_path} names no dev_tree_path (an inherited PGAI_DEV_TREE_PATH is never used for a push)",
            cfg_file=str(cfg_path),
        )

    raw = project_cfg.strip_one_quote_each_side(
        project_cfg.read_cfg_key(cfg_path, "project", "push_to_remote"), '"'
    )
    return PushDecision(
        decision=NO_PUSH if raw == "false" else PUSH,
        project=name,
        cfg_file=str(cfg_path),
        dev_tree=dev_tree,
    )


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python3 -m pgai_agent_kanban.lib.push_decision",
        description=(
            "Decide whether an out-of-band push site may push a project's refs: "
            "prints decision=push|no-push|unresolved and the fields behind it, "
            "one key=value line each. A project resolves only when it is "
            "registered in projects.cfg, its own project.cfg exists, and that "
            "file names a dev_tree_path; push_to_remote = false then gives "
            "no-push, anything else push. Exits 0 whenever a decision was "
            "reached, unresolved included."
        ),
    )
    parser.add_argument(
        "--kanban-root",
        required=True,
        help="Kanban root holding projects.cfg and projects/<name>/.",
    )
    parser.add_argument(
        "--project",
        required=True,
        help="Project name, exactly as registered in projects.cfg.",
    )
    return parser


def main(argv: Optional[Sequence[str]] = None) -> int:
    """CLI entry point: print the decision's key=value lines; exit 0."""
    args = _parser().parse_args(argv)
    decision = resolve_push_decision(args.kanban_root, args.project)
    sys.stdout.write("\n".join(decision.as_lines()) + "\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
