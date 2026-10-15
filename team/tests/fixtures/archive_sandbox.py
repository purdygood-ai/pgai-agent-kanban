"""
archive_sandbox.py
==================
Test helper: build a sandbox kanban root holding one project whose bugs,
priorities, requirements, tasks, queue lines and training logs are written
one at a time, in the on-disk shapes the live kanban uses. Every archive
test (planning, moving, restoring, the readers that fall back to the
archive) builds its before-state here, so each one reads the same shapes.

Nothing here reads or sets the environment, and every path it writes is
under the *parent* directory the caller passes (pytest's ``tmp_path``). A
test hands ``sandbox.root`` to the code under test explicitly; nothing can
reach the live install through this helper.

HOW TO USE
----------
Item by item::

    from tests.fixtures.archive_sandbox import ArchiveSandbox

    sandbox = ArchiveSandbox(tmp_path, active_rc="v1.4.0", last_released="v1.3.0")
    sandbox.set_archive_count(2)
    sandbox.add_bug(1, "done")
    task = sandbox.add_task(ArchiveSandbox.task_id("CODER", 1), "DONE", release_version="v1.3.0")
    sandbox.add_queue_line("coder", task.name, marker="x")
    sandbox.add_training_log("coder", task.name)
    plan(sandbox.project, kanban_root=sandbox.root)

The acceptance scenario (70 done and 3 open bugs, 80 tasks of one role of
which 5 are unfinished and 2 belong to the Active RC, queue lines, training
logs, ``archive_count = 55``)::

    from tests.fixtures.archive_sandbox import build_acceptance_scenario

    scenario = build_acceptance_scenario(tmp_path)
    scenario.expected_bug_ids   # the 18 oldest done bugs
    scenario.expected_task_ids  # the older tasks that are finished and not in the RC

To prove a step changed nothing, compare snapshots::

    before = sandbox.snapshot()
    ...
    assert sandbox.snapshot() == before

WHAT THE PROJECT TREE HOLDS
---------------------------
    <root>/kanban.cfg                       [archive] archive_count, when set
    <root>/projects/<project>/
      project.cfg                           [project] project_name (no dev tree,
                                            so Last Released is read from
                                            release-state.md)
      release-state.md                      Active RC / RC Opened At /
                                            RC Opened By Task / Last Released
      bugs/BUG-NNNN-<slug>.md               ## Status
      priority/PRIORITY-NNNN-<slug>.md      ## Status
      requirements/vX.Y.Z-<slug>.md         ## Status, ## Target Version,
                                            optional ## Bundled Items
      tasks/<TASK-ID>/README.md             ## Release Version, ## Prerequisites
      tasks/<TASK-ID>/status.md             ## State
      tasks/queues/<agent>_backlog.md       header plus "- [m] ID — text" lines
      logs/training/<role>/<stamp>-<TASK-ID>.md
"""

from __future__ import annotations

import dataclasses
import os
import pathlib
from typing import Iterable, Optional

#: The project every sandbox builds unless the caller names another.
DEFAULT_PROJECT = "archive-demo"

#: Queue files a fresh project carries (the install's queue set).
QUEUE_AGENTS = ("bug", "cm", "coder", "pm", "priority", "tester", "writer")

_QUEUE_HEADER = """# {title} Backlog

Tasks ready for the {agent} agent to pull. Markers:

- `[ ]` pending (BACKLOG, ready to pull)
- `[W]` waiting on prerequisites
- `[A]` actively being worked
- `[x]` done or won't-do


## Queue

"""


class ArchiveSandbox:
    """A sandbox kanban root with one project, populated item by item.

    Attributes:
        root:         The sandbox kanban root (``<parent>/kanban``).
        project:      The project name.
        project_root: ``<root>/projects/<project>``.
    """

    def __init__(
        self,
        parent: pathlib.Path,
        project: str = DEFAULT_PROJECT,
        *,
        active_rc: str = "none",
        last_released: str = "v1.0.0",
    ) -> None:
        self.root = pathlib.Path(parent) / "kanban"
        self.project = project
        self.project_root = self.root / "projects" / project
        for sub in ("bugs", "priority", "requirements", "tasks/queues", "logs/training"):
            (self.project_root / sub).mkdir(parents=True, exist_ok=True)
        (self.project_root / "project.cfg").write_text(
            f"[project]\nproject_name = {project}\nworkflow_type = release\n", encoding="utf-8"
        )
        for agent in QUEUE_AGENTS:
            queue = self.queue_file(agent)
            if not queue.exists():
                queue.write_text(
                    _QUEUE_HEADER.format(title=agent.capitalize(), agent=agent), encoding="utf-8"
                )
        self.set_release_state(active_rc, last_released)

    # ------------------------------------------------------------------
    # Names
    # ------------------------------------------------------------------

    @staticmethod
    def task_id(agent: str, sequence: int, date: str = "20260301", slug: Optional[str] = None) -> str:
        """A canonical task ID, ``<AGENT>-<date>-<NNN>-<slug>``."""
        return f"{agent.upper()}-{date}-{sequence:03d}-{slug or f'item-{sequence}'}"

    def queue_file(self, agent: str) -> pathlib.Path:
        """``tasks/queues/<agent>_backlog.md`` of the project."""
        return self.project_root / "tasks" / "queues" / f"{agent}_backlog.md"

    # ------------------------------------------------------------------
    # Configuration and release state
    # ------------------------------------------------------------------

    def set_archive_count(self, value: object) -> None:
        """Write ``[archive] archive_count = <value>`` to the root kanban.cfg."""
        (self.root / "kanban.cfg").write_text(f"[archive]\narchive_count = {value}\n", encoding="utf-8")

    def set_release_state(
        self, active_rc: str = "none", last_released: str = "v1.0.0", *, text: Optional[str] = None
    ) -> pathlib.Path:
        """Write release-state.md; *text*, when given, is written verbatim instead."""
        path = self.project_root / "release-state.md"
        if text is None:
            opened_at = "2026-03-01T00:00:00+00:00" if active_rc != "none" else "none"
            opened_by = "CM-20260301-000-open-rc" if active_rc != "none" else "none"
            text = (
                "# Release State\n\n"
                f"## Active RC\n{active_rc}\n\n"
                f"## RC Opened At\n{opened_at}\n\n"
                f"## RC Opened By Task\n{opened_by}\n\n"
                f"## Last Released\n{last_released}\n"
            )
        path.write_text(text, encoding="utf-8")
        return path

    # ------------------------------------------------------------------
    # Items
    # ------------------------------------------------------------------

    def _intake(self, directory: str, prefix: str, number: int, status: Optional[str], slug: Optional[str]) -> pathlib.Path:
        name = f"{prefix}-{number:04d}-{slug or f'item-{number}'}"
        path = self.project_root / directory / f"{name}.md"
        status_block = "" if status is None else f"## Status\n{status}\n\n"
        path.write_text(
            f"# {name}\n\n**Severity:** medium\n\n{status_block}## Category\n{directory}\n\n---\n\n## Symptom\nExample.\n",
            encoding="utf-8",
        )
        return path

    def add_bug(self, number: int, status: Optional[str] = "done", slug: Optional[str] = None) -> pathlib.Path:
        """Write ``bugs/BUG-NNNN-<slug>.md``; *status* ``None`` omits ``## Status``."""
        return self._intake("bugs", "BUG", number, status, slug)

    def add_priority(self, number: int, status: Optional[str] = "done", slug: Optional[str] = None) -> pathlib.Path:
        """Write ``priority/PRIORITY-NNNN-<slug>.md``; *status* ``None`` omits ``## Status``."""
        return self._intake("priority", "PRIORITY", number, status, slug)

    def add_requirement(
        self,
        version: str,
        status: Optional[str] = "done",
        *,
        slug: str = "feature",
        target_version: Optional[str] = None,
        bundled: Iterable[pathlib.Path] = (),
    ) -> pathlib.Path:
        """Write ``requirements/<version>-<slug>.md``.

        *target_version* defaults to *version*. *bundled* item files make
        this a bundle: each is listed under ``## Bundled Items`` by absolute
        path, the shape discovery writes.
        """
        path = self.project_root / "requirements" / f"{version}-{slug}.md"
        status_block = "" if status is None else f"## Status\n{status}\n\n"
        items = "".join(f"- {item.name} (`{item}`)\n" for item in bundled)
        bundle_block = f"## Bundled Items\n\n{items}\n" if items else ""
        path.write_text(
            f"# {version}-{slug}\n\n{status_block}"
            f"## Target Version\n\n{target_version or version}\n\n"
            f"{bundle_block}## Intent\nExample.\n",
            encoding="utf-8",
        )
        return path

    def add_task(
        self,
        task_id: str,
        state: Optional[str] = "DONE",
        *,
        release_version: str = "v1.0.0",
        prerequisites: Iterable[str] = (),
        readme: bool = True,
    ) -> pathlib.Path:
        """Write ``tasks/<task_id>/`` with README.md and status.md.

        *state* ``None`` writes a status.md with no ``## State``; *readme*
        ``False`` leaves README.md out.
        """
        task_dir = self.project_root / "tasks" / task_id
        (task_dir / "artifacts").mkdir(parents=True, exist_ok=True)
        (task_dir / "logs").mkdir(exist_ok=True)
        if readme:
            prereq_lines = "".join(f"- {p}\n" for p in prerequisites) or "none\n"
            (task_dir / "README.md").write_text(
                f"# Task: {task_id}\n\n## Task ID\n{task_id}\n\n## Role\nCODER\n\n"
                f"## Prerequisites\n{prereq_lines}\n"
                f"## Release Version\n{release_version}\n\n## Notes\nExample.\n",
                encoding="utf-8",
            )
        state_block = "" if state is None else f"## State\n{state}\n\n"
        (task_dir / "status.md").write_text(
            f"# Status\n\n## Task\n{task_id}\n\n{state_block}## Summary\nExample.\n",
            encoding="utf-8",
        )
        return task_dir

    def add_queue_line(
        self, agent: str, item_token: str, *, marker: str = "x", description: Optional[str] = None
    ) -> str:
        """Append ``- [<marker>] <item_token> — <description>`` to an agent's queue; return the line."""
        line = f"- [{marker}] {item_token}" + (f" — {description}" if description else "")
        with self.queue_file(agent).open("a", encoding="utf-8") as handle:
            handle.write(line + "\n")
        return line

    def add_training_log(self, role: str, task_id: str, *, stamp: str = "20260301T000000") -> pathlib.Path:
        """Write ``logs/training/<role>/<stamp>-<task_id>.md``."""
        directory = self.project_root / "logs" / "training" / role
        directory.mkdir(parents=True, exist_ok=True)
        path = directory / f"{stamp}-{task_id}.md"
        path.write_text(f"# Training log: {task_id}\n", encoding="utf-8")
        return path

    # ------------------------------------------------------------------
    # Observation
    # ------------------------------------------------------------------

    def snapshot(self) -> dict:
        """Every path under the sandbox root, relative, mapped to its state.

        A directory maps to ``"dir"``; a file to ``(bytes, mtime_ns)``. Two
        equal snapshots mean no file was created, removed, renamed or
        rewritten in between.
        """
        state: dict = {}
        for dirpath, dirnames, filenames in os.walk(self.root):
            base = pathlib.Path(dirpath)
            for name in dirnames:
                state[(base / name).relative_to(self.root).as_posix()] = "dir"
            for name in filenames:
                path = base / name
                state[path.relative_to(self.root).as_posix()] = (path.read_bytes(), path.stat().st_mtime_ns)
        return state


@dataclasses.dataclass
class AcceptanceScenario:
    """The archive acceptance fixture, with the answers it was built to give.

    Attributes:
        sandbox:           The populated sandbox (``archive_count = 55`` in kanban.cfg).
        archive_count:     55.
        open_bug_numbers:  The three open bugs (all among the newest 55).
        bug_numbers:       Every bug number, 1..73.
        task_ids:          All 80 task IDs, oldest first.
        unfinished_task_ids: The 5 WORKING/BACKLOG tasks (all among the oldest 25).
        active_rc_task_ids:  The 2 DONE tasks of the Active RC (both among the oldest 25).
        expected_bug_ids:  the canonical IDs of bugs 1..18: the 18 oldest done bugs.
        expected_task_ids: The oldest 25 tasks minus the unfinished and Active-RC ones.
    """

    sandbox: ArchiveSandbox
    archive_count: int
    open_bug_numbers: tuple[int, ...]
    bug_numbers: tuple[int, ...]
    task_ids: tuple[str, ...]
    unfinished_task_ids: tuple[str, ...]
    active_rc_task_ids: tuple[str, ...]
    expected_bug_ids: tuple[str, ...]
    expected_task_ids: tuple[str, ...]


#: The one role the scenario's tasks belong to, so all 80 are counted
#: together against ``archive_count``; also their queue/training role.
_SCENARIO_AGENT = "CODER"


def build_acceptance_scenario(parent: pathlib.Path, project: str = DEFAULT_PROJECT) -> AcceptanceScenario:
    """Build the archive acceptance fixture under *parent*.

    Bugs 1..73: 70 done, 3 open (numbers 25, 40 and 60, all inside the
    newest 55), each with a ``bug_backlog.md`` line. Tasks 1..80 are all of
    one role (tasks are counted per role) on one date, sequence = age:
    among the oldest 25, sequences 3, 7, 11 are WORKING and 15, 19 BACKLOG;
    5 and 9 are DONE in the Active RC (v1.4.0); 2 is WONT-DO; the rest are
    DONE in v1.3.0, the last release. The newest 55 tasks are DONE in the
    Active RC, so 5 and 9, ordered by release version, are still the two
    newest of the oldest 25. Every task has a queue line in its agent's
    backlog; every DONE or WONT-DO task has a training log.
    """
    sandbox = ArchiveSandbox(parent, project, active_rc="v1.4.0", last_released="v1.3.0")
    archive_count = 55
    sandbox.set_archive_count(archive_count)

    open_bugs = (25, 40, 60)
    bug_numbers = tuple(range(1, 74))
    for number in bug_numbers:
        status = "open" if number in open_bugs else "done"
        path = sandbox.add_bug(number, status)
        sandbox.add_queue_line("bug", path.stem, marker=" " if status == "open" else "x")

    working, backlog, in_rc, wont_do = (3, 7, 11), (15, 19), (5, 9), (2,)
    task_ids = []
    task_total = 80
    for sequence in range(1, task_total + 1):
        agent = _SCENARIO_AGENT
        task_id = ArchiveSandbox.task_id(agent, sequence)
        task_ids.append(task_id)
        if sequence in working:
            state, marker = "WORKING", "A"
        elif sequence in backlog:
            state, marker = "BACKLOG", " "
        elif sequence in wont_do:
            state, marker = "WONT-DO", "x"
        else:
            state, marker = "DONE", "x"
        in_newest = sequence > task_total - archive_count
        release = "v1.4.0" if sequence in in_rc or in_newest else "v1.3.0"
        sandbox.add_task(task_id, state, release_version=release)
        sandbox.add_queue_line(agent.lower(), task_id, marker=marker, description=f"example task {sequence}")
        if state in ("DONE", "WONT-DO"):
            sandbox.add_training_log(agent.lower(), task_id, stamp=f"20260301T{sequence:06d}")

    older = len(task_ids) - archive_count
    unfinished = tuple(task_ids[s - 1] for s in (*working, *backlog))
    active = tuple(task_ids[s - 1] for s in in_rc)
    expected_tasks = tuple(t for t in task_ids[:older] if t not in unfinished and t not in active)
    older_bugs = len(bug_numbers) - archive_count
    return AcceptanceScenario(
        sandbox=sandbox,
        archive_count=archive_count,
        open_bug_numbers=open_bugs,
        bug_numbers=bug_numbers,
        task_ids=tuple(task_ids),
        unfinished_task_ids=unfinished,
        active_rc_task_ids=active,
        expected_bug_ids=tuple(f"BUG-{n:04d}" for n in bug_numbers[:older_bugs]),
        expected_task_ids=expected_tasks,
    )
