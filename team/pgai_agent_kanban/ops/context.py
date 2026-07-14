"""
context.py — OpsContext: execution context for pgai_agent_kanban.ops functions.

OpsContext carries the kanban root path and provides project-scoped path
resolution.  It is the Python counterpart of team/scripts/lib/project_paths.sh:
all path layout formulas are expressed here once so read (and later write)
functions receive resolved paths rather than reconstructing them inline.

Path layout (mirrors project_paths.sh):

    <kanban_root>/
        projects/
            <project>/
                tasks/
                    queues/
                        <agent>_backlog.md
                requirements/
                bugs/
                priority/
                rejected/
                release-state.md

Usage:

    ctx = OpsContext(kanban_root=Path("/home/<operator>/pgai_agent_kanban"))
    # or resolve from the environment:
    ctx = OpsContext.from_env()

    project_dir = ctx.project_root("pgai-agent-kanban")
    tasks_path  = ctx.tasks_dir("pgai-agent-kanban")
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

from pgai_agent_kanban.env import resolve_kanban_root


@dataclass
class OpsContext:
    """Execution context passed as the first argument to all ops functions.

    Attributes:
        kanban_root: Absolute path to the kanban installation root.  Must
            point to the directory that contains ``projects/``, ``tasks/``,
            and other top-level kanban directories.
    """

    kanban_root: Path = field(default_factory=resolve_kanban_root)

    def __post_init__(self) -> None:
        # Normalize to an absolute Path regardless of how the caller supplied it.
        self.kanban_root = Path(self.kanban_root).expanduser().resolve()

    # ------------------------------------------------------------------
    # Class-level constructor
    # ------------------------------------------------------------------

    @classmethod
    def from_env(cls) -> "OpsContext":
        """Construct an OpsContext from the process environment.

        Resolution order for the kanban root (highest to lowest precedence):

        1. ``KANBAN_ROOT`` environment variable (legacy alias)
        2. ``PGAI_AGENT_KANBAN_ROOT_PATH`` environment variable, resolved
           through the canonical resolver in :mod:`pgai_agent_kanban.env`

        Raises:
            RuntimeError: When neither ``KANBAN_ROOT`` nor
                ``PGAI_AGENT_KANBAN_ROOT_PATH`` is set in the environment.

        Returns:
            A new OpsContext with kanban_root resolved from the environment.
        """
        # KANBAN_ROOT is a legacy alias honored for backward compatibility.
        # When present it takes precedence; otherwise the canonical resolver
        # reads PGAI_AGENT_KANBAN_ROOT_PATH and fails loud when unset.
        legacy = os.environ.get("KANBAN_ROOT", "").strip()
        if legacy:
            return cls(kanban_root=Path(legacy))
        return cls(kanban_root=resolve_kanban_root())

    # ------------------------------------------------------------------
    # Project-scoped path helpers (mirror of project_paths.sh pp_* functions)
    # ------------------------------------------------------------------

    def project_root(self, project: str) -> Path:
        """Return the root directory for the named project.

        Equivalent to ``pp_project_root`` in project_paths.sh.

        Args:
            project: Project name as registered in projects.cfg.

        Returns:
            ``<kanban_root>/projects/<project>``
        """
        return self.kanban_root / "projects" / project

    def tasks_dir(self, project: str) -> Path:
        """Return the tasks directory for the named project.

        Equivalent to ``pp_tasks_dir`` in project_paths.sh.

        Args:
            project: Project name as registered in projects.cfg.

        Returns:
            ``<kanban_root>/projects/<project>/tasks``
        """
        return self.project_root(project) / "tasks"

    def requirements_dir(self, project: str) -> Path:
        """Return the requirements directory for the named project.

        Equivalent to ``pp_requirements_dir`` in project_paths.sh.

        Args:
            project: Project name as registered in projects.cfg.

        Returns:
            ``<kanban_root>/projects/<project>/requirements``
        """
        return self.project_root(project) / "requirements"

    def bugs_dir(self, project: str) -> Path:
        """Return the bugs directory for the named project.

        Equivalent to ``pp_bugs_dir`` in project_paths.sh.

        Args:
            project: Project name as registered in projects.cfg.

        Returns:
            ``<kanban_root>/projects/<project>/bugs``
        """
        return self.project_root(project) / "bugs"

    def priority_dir(self, project: str) -> Path:
        """Return the priority intake directory for the named project.

        Equivalent to ``pp_priority_dir`` in project_paths.sh.

        Args:
            project: Project name as registered in projects.cfg.

        Returns:
            ``<kanban_root>/projects/<project>/priority``
        """
        return self.project_root(project) / "priority"

    def rejected_dir(self, project: str) -> Path:
        """Return the rejected-items directory for the named project.

        Equivalent to ``pp_rejected_dir`` in project_paths.sh.  Unlike the
        bash version, this method does not create the directory — callers
        are responsible for mkdir as needed.

        Args:
            project: Project name as registered in projects.cfg.

        Returns:
            ``<kanban_root>/projects/<project>/rejected``
        """
        return self.project_root(project) / "rejected"

    def release_state_path(self, project: str) -> Path:
        """Return the release-state.md path for the named project.

        Equivalent to ``pp_release_state`` in project_paths.sh.

        Args:
            project: Project name as registered in projects.cfg.

        Returns:
            ``<kanban_root>/projects/<project>/release-state.md``
        """
        return self.project_root(project) / "release-state.md"

    def queue_path(self, project: str, agent: str) -> Path:
        """Return the backlog queue file path for the named agent and project.

        Equivalent to ``pp_queue_path`` in project_paths.sh.

        Args:
            project: Project name as registered in projects.cfg.
            agent:   Agent type slug (e.g. ``"coder"``, ``"pm"``, ``"tester"``).

        Returns:
            ``<kanban_root>/projects/<project>/tasks/queues/<agent>_backlog.md``
        """
        return self.tasks_dir(project) / "queues" / f"{agent}_backlog.md"
