"""
unwind.py — the ``unwind-rc`` operator verb: fully unwind an in-flight
release candidate across every state store.

One implementation behind ``team/scripts/unwind-rc.sh`` (which reaches
:func:`unwind_rc` through ``pp_run_ops pgai_agent_kanban.cm unwind_rc``) and
``POST /operations/unwind-rc`` (which calls :func:`unwind_rc` in process).
The script keeps flag parsing, ``--help`` and the ``PGAI_PROJECT_NAME``
fallback; everything it prints and every change it makes happen here, so
both surfaces refuse alike, narrate alike and leave the same bytes on disk.

Pre-flight checks, in order (each refusal prints plain ``ERROR:`` lines to
stderr and exits 1 unless noted)
--------------------------------------------------------------------------
0. Confirmation (not for a dry run): ``confirm=True``, ``force=True`` with
   *force_answers_prompt*, or a *prompt* to ask. With none of them the call
   refuses before reading anything (``unwind_rc.confirmation_required``,
   the one dual-channel refusal).
1. The project name resolves (exact, then unambiguous prefix, against
   ``projects.cfg``); a refusal prints the resolver's dual-channel block.
   The push decision (see "Push decision and the remote boundary" below)
   is taken here, before the repository is resolved, so an unresolved
   project's skip line is printed even when a later check refuses. It
   never refuses.
2. The repository: *repo_root*, else ``project.cfg``'s ``dev_tree_path``,
   else ``PGAI_DEV_TREE_PATH`` (environment, then ``kanban.cfg [paths]
   dev_tree_path``). This choice only names where the local steps run; it
   never makes a project pushable. A configured ``branch_prefix`` must form
   a branch name.
3. The version is ``vX.Y.Z``.
4. A HALT file is in place: ``<root>/HALT`` (global) or
   ``<root>/projects/<name>/HALT``.
5. The project directory exists.
6. The version is not a tag in the repository (shipped releases are never
   unwound).
7. ``release-state.md`` exists, its Active RC is not ``none``, and it is the
   version being unwound. ``force=True`` bypasses this last comparison and
   nothing else.

``force`` and ``confirm`` are separate parameters. ``confirm`` answers the
confirmation prompt and bypasses no check. ``force`` bypasses only the
Active-RC mismatch check; it answers the prompt only when the caller passes
*force_answers_prompt* -- the CLI does, because its ``--force`` has always
let a scripted caller run with the one flag. The REST route never does.

What an unwind does (steps a-l)
-------------------------------
a. Backup: ``tasks/queues/``, ``requirements/``, ``priority/``,
   ``release-state.md``, ``release-state/`` and every task folder whose
   README names the version are copied to
   ``<temp root>/unwind-rc-<version>-backup-<timestamp>/`` before anything
   changes. A failed copy stops the unwind (exit 2).
c. Git: check out ``<prefix>main``, delete ``<prefix>rc/<version>`` locally
   (``git branch -D``) and -- for a project that pushes -- on the project's
   remote (``git push <remote> --delete``) when it exists there, then delete local
   ``feature/CLAUDE-<task id>`` branches of the RC's tasks. A failing
   checkout or branch delete stops the unwind (exit 2).
d. Every task folder naming the version gets ``## State`` WONT-DO and a
   ``## Cancellation Note (<timestamp>)`` section, through
   :mod:`pgai_agent_kanban.task_status`; a folder that already carries a
   Cancellation Note is left alone, and a status.md with no ``## State`` is
   left alone with a warning.
e. Their queue lines in the seven backlogs are set to ``[x]`` through
   :func:`pgai_agent_kanban.ops.write.set_marker`.
f. The version's requirements file is renamed
   ``<name>.SUPERSEDED-on-cancel-<timestamp>.md`` (``git mv`` when tracked)
   and the value line of its ``## Status`` set to ``wont-do``
   (:func:`pgai_agent_kanban.lib.md_fields.set_value_line` plus an atomic
   write; when tracked, the change is staged with the rename). A superseded
   bundle is closed, so it never holds discovery's bundling slot. A file
   already superseded by an earlier run is closed the same way; a file with
   no ``## Status`` is left alone with a warning.
g. ``tasks/queues/plans/.materialized.*`` files naming the version are
   removed.
h. / i. Every PRIORITY / BUG item listed under the requirements file's
   ``## Bundled Items`` gets its backlog line set back from ``[x]`` to
   ``[ ]`` and the value line of its own ``## Status`` set to ``open``
   (:func:`pgai_agent_kanban.lib.md_fields.set_value_line`; every other
   line is kept). A file with no ``## Status`` is left alone with a warning.
j. ``release-state.md``: Active RC, RC Opened At and RC Opened By Task are
   set to ``none`` through :mod:`pgai_agent_kanban.cm.write_rc_state`;
   every other line is kept.
k. ``.discovery-state/`` files (two levels deep) whose names carry the
   version are removed.
l. ``release-state/<version>.json`` is marked cancelled through
   :func:`pgai_agent_kanban.cm.write_rc_state.write_cancel`; an absent
   record is skipped and a failed write is a warning.

Push decision and the remote boundary
-------------------------------------
``<remote>`` is ``project.cfg [project] git_remote_name``, ``origin`` when
absent or blank (:func:`pgai_agent_kanban.cm.cancel.git_remote_name`); a
value that is not a valid remote name refuses dual-channel
(``git_remote_name.invalid``) before the push decision is narrated. Every
remote step, ref and narration line names it.

Whether the unwind may touch the remote is decided by the shared push-decision
resolver, through :func:`pgai_agent_kanban.cm.cancel.push_decision` (which
calls :func:`pgai_agent_kanban.lib.push_decision.resolve_push_decision`) --
the one rule every push site applies: the project must be registered in
``projects.cfg``, its own ``project.cfg`` (or legacy ``PROJECT.cfg``) must
exist, and that file must name a ``dev_tree_path``. *repo_root* and an
inherited ``PGAI_DEV_TREE_PATH`` choose where the local steps run and never
make a project pushable.

- ``push``: a resolved project whose ``push_to_remote`` is absent, blank,
  ``true`` or anything but ``false`` -- the default is unchanged. The plan
  checks the remote for the RC branch (``git ls-remote``) and step c deletes it
  there when it exists. Nothing extra is narrated.
- ``no-push``: a resolved project with ``push_to_remote = false``. Narrated
  ``[unwind-rc] Push policy: push_to_remote=false — skipping <remote>
  operations. Local cleanup only.``; the plan lists the remote branch as
  ``[skipped — push_to_remote=false]`` and step c prints
  ``[push_to_remote=false] skipping <remote> push for <name>: git push <remote>
  --delete <branch> (Step c)``.
- ``unresolved``: one line, ``[unwind-rc] skipped-unresolved-project:
  project '<name>': missing <piece>: <detail>; nothing pushed and no remote
  contacted`` (``<piece>`` is ``registration``, ``project-cfg`` or
  ``dev-tree-path``); the plan lists ``[skipped — unresolved project]`` and
  step c prints ``[unresolved project] skipping <remote> push for <name>:
  ...``. The unwind then runs exactly as for ``no-push``.

A project that does not push (``no-push`` or ``unresolved``) contacts no
remote at all -- no ``ls-remote`` in the plan or in step c, and no remote
branch delete -- in a dry run and a confirmed run alike. Every local step is
the same for all three answers.

Output
------
Narration goes to the *out* callback and diagnostics to *err* (defaults:
this process's stdout and stderr), each receiving text exactly as the CLI
prints it. A refusal or a failed step is raised as :class:`UnwindRcError`,
whose ``text`` is what is still to be printed to stderr and whose
``exit_code`` the CLI exits with (1 refused or declined, 2 stopped part-way
after the backup).

CLI (``python3 -m pgai_agent_kanban.cm unwind_rc ...``; ``cm/__main__.py``
routes :data:`CLI_VERBS` here)::

    unwind_rc --kanban-root=ROOT --project=NAME --key=vX.Y.Z
              [--dry-run] [--confirm] [--force]
              [--repo-root=PATH] [--temp-root=PATH]

Without ``--confirm`` or ``--force`` the CLI asks ``Proceed with unwind of
<branch>? Type 'yes' to continue:`` on stdin (the prompt itself is shown
only when stdin is a terminal).
"""

from __future__ import annotations

import argparse
import dataclasses
import datetime
import fnmatch
import glob
import json
import os
import pathlib
import re
import shutil
import subprocess
import sys
from typing import Callable, Optional

from pgai_agent_kanban import task_status
from pgai_agent_kanban.cm import cancel as _cancel
from pgai_agent_kanban.cm import write_rc_state
from pgai_agent_kanban.cm.read_state_field import read_state_field
from pgai_agent_kanban.lib import ini, md_fields
from pgai_agent_kanban.lib import push_decision as _push_decision
from pgai_agent_kanban.lib.atomic_write import atomic_write
from pgai_agent_kanban.lib.error_contract import format_error_contract
from pgai_agent_kanban.ops import resolve as ops_resolve
from pgai_agent_kanban.ops import write as ops_write
from pgai_agent_kanban.ops.context import OpsContext
from pgai_agent_kanban.ops.errors import Ambiguous, CliRefusal, NotFound, OpsError

__all__ = [
    "CLI_VERBS",
    "UnwindOutcome",
    "UnwindRcError",
    "unwind_exit_code",
    "unwind_rc",
]

#: The CLI verbs this module serves (``cm/__main__.py`` routes them here).
CLI_VERBS: tuple[str, ...] = ("unwind_rc",)

EXIT_REFUSED = 1
EXIT_PARTIAL = 2

_SCRIPT = "team/scripts/unwind-rc.sh"
# Prefix of the push-policy and skipped-unresolved-project lines, the way
# cancel-rc tags its own.
_SITE_TAG = "[unwind-rc]"
# Why origin is left alone, by push decision: shown in the plan and in the
# step c skip line. A decision not listed here (push) contacts origin.
_ORIGIN_SKIP_REASONS = {
    _push_decision.NO_PUSH: "push_to_remote=false",
    _push_decision.UNRESOLVED: "unresolved project",
}
_VERSION_RE = re.compile(r"^v[0-9]+\.[0-9]+\.[0-9]+$")
_FINISHED_STATES = ("DONE", "WONT-DO")
_RULE = "=" * 60
_THIN_RULE = "-" * 60
# Last-resort temp root, the same one team/scripts/lib/temp.sh falls back to.
_FALLBACK_TEMP_ROOT = "/tmp/pgai_kanban_tmp"
_QUEUE_NAMES = ("coder", "cm", "pm", "tester", "writer", "bug", "priority")
# A queue line the plan lists as open: one starting "[ ]", or one carrying a
# [W], [A] or [B] marker anywhere.
_OPEN_QUEUE_LINE_RE = re.compile(r"^\[ \]|\[W\]|\[A\]|\[B\]")
_CANCELLATION_NOTE_RE = re.compile(r"^## Cancellation Note", re.M)
_ORPHAN_BRANCH_PREFIX = "feature/CLAUDE-"
_BUNDLED_ITEMS_HEADING = "## Bundled Items"
# The ## Status a superseded requirements file is closed to.
_SUPERSEDED_STATUS = "wont-do"

Writer = Callable[[str], None]
Prompt = Callable[[str], Optional[str]]


@dataclasses.dataclass(frozen=True)
class UnwindOutcome:
    """What one :func:`unwind_rc` call did.

    Attributes:
        project:    The resolved project name.
        rc_branch:  The (prefixed) RC branch name.
        outcome:    ``planned`` for a dry run, ``unwound`` otherwise.
        backup_dir: The backup directory (``""`` for a dry run).
        cancelled_tasks: Task folders flipped to WONT-DO.
    """

    project: str
    rc_branch: str
    outcome: str
    backup_dir: str = ""
    cancelled_tasks: tuple[str, ...] = ()


class UnwindRcError(CliRefusal):
    """The unwind stopped; nothing after the stopping point was changed.

    Attributes:
        error:     A stable machine key (``unwind_rc.<reason>``, or the
                   resolver's ``project_resolve.<reason>``).
        exit_code: The code ``unwind-rc.sh`` exits with (1 refused or
                   declined, 2 stopped after the backup).
        text:      What remains to be printed to stderr (plain ``ERROR:``
                   lines, a dual-channel block, or ``""`` when nothing is
                   left to print).
    """


def unwind_exit_code(exc: OpsError) -> int:
    """Return the exit code ``unwind-rc.sh`` exits with for *exc* (1 when it names none)."""
    return getattr(exc, "exit_code", EXIT_REFUSED)


def _write_stdout(text: str) -> None:
    sys.stdout.write(text)
    sys.stdout.flush()


def _write_stderr(text: str) -> None:
    sys.stderr.write(text)
    sys.stderr.flush()


def _local_now() -> str:
    """The local time as ``date -Iseconds`` prints it."""
    return datetime.datetime.now().astimezone().isoformat(timespec="seconds")


# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------


def _resolve_project(ctx: OpsContext, typed: str) -> str:
    """The registered project *typed* names (``pp_resolve_project_name``)."""
    try:
        return ops_resolve.resolve_project_name(ctx, typed)
    except OpsError as exc:
        key = "unwind_rc.refused"
        if isinstance(exc, (NotFound, Ambiguous)):
            key = "project_resolve.ambiguous" if isinstance(exc, Ambiguous) else "project_resolve.not_found"
        # BLOCK-WORTHY (docs/blocking-site-census.md): with no single
        # registered project there is nothing to unwind; the resolver's own
        # dual-channel block is printed, then the script's closing line.
        # CENSUS-ANCHOR: unwind-project-unresolved-01
        raise UnwindRcError(
            key, f"{exc}\n\nERROR: could not resolve project context for '{typed}'.\n"
        ) from exc


def _dev_tree_fallback(ctx: OpsContext) -> str:
    """``PGAI_DEV_TREE_PATH``, else ``kanban.cfg [paths] dev_tree_path``."""
    value = os.environ.get("PGAI_DEV_TREE_PATH", "")
    if value:
        return value
    kanban_cfg = ctx.kanban_root / "kanban.cfg"
    if kanban_cfg.is_file():
        return ini.read_line_value(kanban_cfg, "paths", "dev_tree_path") or ""
    return ""


def _repo_root(ctx: OpsContext, project: str) -> str:
    """The repository to unwind in: project.cfg's dev_tree_path, else the fallback.

    Raises:
        UnwindRcError: no project.cfg (nor a legacy PROJECT.cfg), or no
            dev tree configured anywhere.
    """
    project_root = ctx.project_root(project)
    new_cfg = project_root / "project.cfg"
    old_cfg = project_root / "PROJECT.cfg"
    if new_cfg.is_file():
        value = ini.read_line_value(new_cfg, "project", "dev_tree_path") or ""
    elif old_cfg.is_file():
        value = _cancel._read_cfg_key(old_cfg, "project", "dev_tree_path")
    else:
        # BLOCK-WORTHY (docs/blocking-site-census.md): with no project
        # configuration there is no repository to unwind the RC in.
        # CENSUS-ANCHOR: unwind-project-cfg-missing-01
        raise UnwindRcError(
            "unwind_rc.refused",
            f"project_paths.sh: PROJECT.cfg not found at {old_cfg} (and project.cfg not found at {new_cfg})\n"
            f"ERROR: could not load project config for '{project}'\n"
            f"  Expected: {new_cfg}\n",
        )
    value = value or _dev_tree_fallback(ctx)
    if not value:
        # BLOCK-WORTHY (docs/blocking-site-census.md): no dev tree configured,
        # so no repository to unwind the RC in.
        # CENSUS-ANCHOR: unwind-dev-tree-unset-01
        raise UnwindRcError(
            "unwind_rc.refused",
            f"ERROR: dev_tree_path is not set in project config for '{project}'\n"
            "  Set REPO_ROOT in the environment or add dev_tree_path to the project config.\n",
        )
    return value


def _branch_prefix(ctx: OpsContext, project: str) -> str:
    """``project.cfg [project] branch_prefix``, refused when it cannot name a branch."""
    try:
        return _cancel.branch_prefix(ctx, project)
    except _cancel.CancelRcError as exc:
        # BLOCK-WORTHY (docs/blocking-site-census.md): a prefix that cannot
        # form a valid branch name would point every git step at the wrong
        # branch.
        # CENSUS-ANCHOR: unwind-branch-prefix-01
        raise UnwindRcError("unwind_rc.refused", exc.text) from exc


def _git_remote_name(ctx: OpsContext, project: str) -> str:
    """``project.cfg [project] git_remote_name`` (``origin`` when absent or blank), refused when invalid."""
    try:
        return _cancel.git_remote_name(ctx, project)
    except _cancel.CancelRcError as exc:
        # BLOCK-WORTHY (docs/blocking-site-census.md): a value that cannot
        # name a remote would point the RC-branch check and delete at a
        # destination nobody configured.
        # CENSUS-ANCHOR: unwind-git-remote-name-01
        raise UnwindRcError(exc.error, exc.text) from exc


def _origin_skip_reason(ctx: OpsContext, project: str, say: Callable[..., None], remote: str) -> str:
    """Take the shared push decision for *project* and narrate it; return why *remote* is skipped.

    The decision is cancel-rc's (:func:`pgai_agent_kanban.cm.cancel.push_decision`),
    so every push site applies one rule. ``unresolved`` prints its one
    ``skipped-unresolved-project`` line and ``no-push`` the push-policy line,
    each tagged ``[unwind-rc]``; ``push`` prints nothing.

    Returns:
        ``""`` when the project pushes (*remote* may be checked and its RC
        branch deleted), else the short reason the plan and step c show for
        leaving the remote alone (``push_to_remote=false`` or
        ``unresolved project``).
    """
    decision = _cancel.push_decision(ctx, project)
    if decision.may_push:
        return ""
    if decision.decision == _push_decision.UNRESOLVED:
        say(f"{_SITE_TAG} {decision.status_line}")
    else:
        say(f"{_SITE_TAG} Push policy: push_to_remote=false — skipping {remote} operations. Local cleanup only.")
    return _ORIGIN_SKIP_REASONS.get(decision.decision, decision.decision)


def _temp_root(ctx: OpsContext) -> str:
    """The framework temp root, resolved as ``pgai_temp_dir`` resolves it, created if missing.

    ``PGAI_AGENT_KANBAN_TEMP_DIR``, else ``kanban.cfg [paths] tmp_root`` /
    ``tmp_subdir`` under the kanban root, else the documented last-resort
    root. A value naming the filesystem root falls back to the last resort.
    """
    value = os.environ.get("PGAI_AGENT_KANBAN_TEMP_DIR", "")
    if not value:
        kanban_cfg = ctx.kanban_root / "kanban.cfg"
        tmp_root = tmp_subdir = ""
        if kanban_cfg.is_file():
            tmp_root = ini.read_line_value(kanban_cfg, "paths", "tmp_root") or ""
            tmp_subdir = ini.read_line_value(kanban_cfg, "paths", "tmp_subdir") or ""
        value = f"{tmp_root}/{tmp_subdir}" if tmp_root and tmp_subdir else _FALLBACK_TEMP_ROOT
    if os.path.realpath(value) == os.sep:
        value = _FALLBACK_TEMP_ROOT
    os.makedirs(value, exist_ok=True)
    return value


# ---------------------------------------------------------------------------
# git
# ---------------------------------------------------------------------------


def _git(repo: str, *args: str, merge_stderr: bool = False) -> subprocess.CompletedProcess:
    """Run ``git -C <repo> <args>`` with its output captured as text.

    *merge_stderr* sends stderr into stdout (the script's ``2>&1``). A
    missing git binary reads as the shell's exit 127.
    """
    try:
        return subprocess.run(
            ["git", "-C", repo, *args],
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT if merge_stderr else subprocess.PIPE,
            text=True, errors="replace", check=False, stdin=subprocess.DEVNULL,
        )
    except FileNotFoundError:
        return subprocess.CompletedProcess(["git", *args], 127, "", "git: command not found\n")


def _local_branch_exists(repo: str, branch: str) -> bool:
    return _git(repo, "rev-parse", "--verify", f"refs/heads/{branch}").returncode == 0


def _remote_branch_exists(repo: str, remote: str, branch: str) -> bool:
    return _git(repo, "ls-remote", "--exit-code", "--heads", remote, branch).returncode == 0


# ---------------------------------------------------------------------------
# Inventory
# ---------------------------------------------------------------------------


@dataclasses.dataclass
class _Inventory:
    """Everything the plan lists and the steps act on."""

    tasks_all: list[str]
    tasks_active: list[str]
    queue_files: list[pathlib.Path]
    queue_entries: list[str]
    requirements_file: str
    plan_markers: list[str]
    rc_local: bool
    rc_remote: bool
    # Why the remote was not checked ("" when it was): see _origin_skip_reason.
    origin_skip: str
    # The project's remote (git_remote_name, origin when absent or blank).
    remote: str
    priority_to_flip: list[str]
    bugs_to_flip: list[str]
    discovery_files: list[str]
    rc_json: pathlib.Path
    rc_json_outcome: Optional[str]


def _version_pattern(version: str) -> "re.Pattern[str]":
    """*version* as a search pattern, ``.`` matching any character (a grep pattern)."""
    return re.compile(version)


def _grep_literal(text: str) -> str:
    """*text* as a pattern where only ``.`` keeps its any-character meaning."""
    return "".join("." if char == "." else re.escape(char) for char in text)


def _read_text(path: "str | pathlib.Path") -> str:
    try:
        return pathlib.Path(path).read_text(encoding="utf-8", errors="replace")
    except OSError:
        return ""


def _terminated_lines(text: str) -> list[str]:
    """The newline-terminated lines of *text*; an unterminated last line is not one."""
    return text.split("\n")[:-1]


def _sorted_entries(directory: pathlib.Path) -> list[str]:
    """Non-hidden entry names of *directory* in code-point order (``[]`` when unreadable)."""
    try:
        return sorted(name for name in os.listdir(directory) if not name.startswith("."))
    except OSError:
        return []


def _find_names(top: pathlib.Path, pattern: str, max_depth: Optional[int] = None) -> list[str]:
    """Paths below *top* whose names match *pattern*, in ``find``'s directory order.

    Entries come in the order the directory lists them, each directory's
    contents directly after it; *max_depth* limits how deep the walk goes
    (1 = *top*'s own entries).
    """
    found: list[str] = []

    def _walk(directory: str, depth: int) -> None:
        try:
            entries = list(os.scandir(directory))
        except OSError:
            return
        for entry in entries:
            if fnmatch.fnmatchcase(entry.name, pattern):
                found.append(entry.path)
            if entry.is_dir(follow_symlinks=False) and (max_depth is None or depth + 1 < max_depth):
                _walk(entry.path, depth + 1)

    _walk(str(top), 0)
    return found


def _bundled_ids_to_flip(backlog: pathlib.Path, requirements_text: str, kind: str) -> list[str]:
    """IDs of *kind* the requirements file names that a backlog line starting ``[x]`` carries."""
    if not backlog.is_file() or not requirements_text:
        return []
    names = sorted(set(re.findall(kind + r'-[0-9]+[^`)"\s]+', requirements_text)))
    lines = _read_text(backlog).splitlines()
    return [
        name for name in names
        if any(re.match(r"\[x\].*" + _grep_literal(name), line) for line in lines)
    ]


def _rc_json_outcome(path: pathlib.Path) -> Optional[str]:
    """The per-RC record's ``outcome`` as text, ``(unknown)`` / ``(unreadable)``, or None when absent."""
    if not path.is_file():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return str(data.get("outcome", "(unknown)"))
    except Exception:  # noqa: BLE001 -- any unreadable record is reported, never fatal
        return "(unreadable)"


def _inventory(
    ctx: OpsContext, project: str, version: str, repo: str, rc_branch: str, origin_skip: str, remote: str
) -> _Inventory:
    """Collect what the unwind will touch, without changing anything.

    *remote* is asked about the RC branch (``git ls-remote``) only when
    *origin_skip* is empty, i.e. the project pushes; otherwise ``rc_remote``
    is False and no remote is contacted.
    """
    pattern = _version_pattern(version)
    tasks_dir = ctx.tasks_dir(project)
    tasks_all: list[str] = []
    tasks_active: list[str] = []
    for name in _sorted_entries(tasks_dir):
        folder = tasks_dir / name
        readme = folder / "README.md"
        if not folder.is_dir() or not readme.is_file():
            continue
        if not pattern.search(_read_text(readme)):
            continue
        tasks_all.append(name)
        status = folder / "status.md"
        if status.is_file() and task_status.read_state(status) not in _FINISHED_STATES:
            tasks_active.append(name)

    queue_files = [ctx.queue_path(project, agent) for agent in _QUEUE_NAMES]
    queue_entries: list[str] = []
    for queue in queue_files:
        if not queue.is_file():
            continue
        for line in _terminated_lines(_read_text(queue)):
            if _OPEN_QUEUE_LINE_RE.search(line) and pattern.search(line):
                queue_entries.append(f"{queue.name}: {line}")

    requirements_file = ""
    requirements_dir = ctx.requirements_dir(project)
    if requirements_dir.is_dir():
        for candidate in sorted(glob.glob(str(requirements_dir / f"{glob.escape(version)}-*.md"))):
            if "SUPERSEDED" in candidate:
                continue
            if os.path.isfile(candidate):
                requirements_file = candidate
                break

    plan_markers: list[str] = []
    plans_dir = tasks_dir / "queues" / "plans"
    if plans_dir.is_dir():
        for marker in sorted(glob.glob(str(plans_dir / ".materialized.*"))):
            if os.path.isfile(marker) and pattern.search(_read_text(marker)):
                plan_markers.append(marker)

    requirements_text = _read_text(requirements_file) if requirements_file else ""
    discovery_dir = ctx.project_root(project) / ".discovery-state"
    rc_json = ctx.project_root(project) / "release-state" / f"{version}.json"
    return _Inventory(
        tasks_all=tasks_all,
        tasks_active=tasks_active,
        queue_files=queue_files,
        queue_entries=queue_entries,
        requirements_file=requirements_file,
        plan_markers=plan_markers,
        rc_local=_local_branch_exists(repo, rc_branch),
        rc_remote=False if origin_skip else _remote_branch_exists(repo, remote, rc_branch),
        origin_skip=origin_skip,
        remote=remote,
        priority_to_flip=_bundled_ids_to_flip(ctx.queue_path(project, "priority"), requirements_text, "PRIORITY"),
        bugs_to_flip=_bundled_ids_to_flip(ctx.queue_path(project, "bug"), requirements_text, "BUG"),
        discovery_files=_find_names(discovery_dir, f"*{version}*") if discovery_dir.is_dir() else [],
        rc_json=rc_json,
        rc_json_outcome=_rc_json_outcome(rc_json),
    )


# ---------------------------------------------------------------------------
# The verb
# ---------------------------------------------------------------------------


def unwind_rc(
    ctx: OpsContext,
    project: str,
    version: str,
    *,
    dry_run: bool = False,
    confirm: bool = False,
    force: bool = False,
    force_answers_prompt: bool = False,
    prompt: Optional[Prompt] = None,
    repo_root: Optional[str] = None,
    temp_root: Optional[str] = None,
    out: Optional[Writer] = None,
    err: Optional[Writer] = None,
) -> UnwindOutcome:
    """Unwind *project*'s in-flight RC *version* (see the module docstring).

    Args:
        ctx:       Kanban root.
        project:   Project name as typed: exact, or an unambiguous prefix of
                   a registered name.
        version:   The RC version, ``vX.Y.Z``.
        dry_run:   Print the plan and stop; nothing is changed and no
                   confirmation is needed.
        confirm:   The confirmation: proceed without asking. Bypasses no
                   check.
        force:     Bypass only the Active-RC mismatch check.
        force_answers_prompt: Let *force* also stand in for the confirmation
                   (the CLI's ``--force`` contract). Off by default, so a
                   direct caller's *force* never confirms.
        prompt:    Asked when the call is not confirmed, after the plan is
                   printed; receives the question and returns the answer
                   line, or None at end of input. With no confirmation and
                   no *prompt* the call refuses before doing anything.
        repo_root: The repository to act on, instead of the configured dev
                   tree (the script's ``REPO_ROOT``).
        temp_root: Where the backup directory goes (default: the framework
                   temp root).
        out, err:  Receive stdout / stderr text as it is produced (default:
                   this process's streams).

    Returns:
        The :class:`UnwindOutcome`.

    Raises:
        UnwindRcError: a check refused, the prompt was declined or
            unanswered (exit 1), or a step failed after the backup (exit 2).
    """
    say_raw = out or _write_stdout
    warn_raw = err or _write_stderr

    def say(line: str = "") -> None:
        say_raw(line + "\n")

    def warn(line: str) -> None:
        warn_raw(line + "\n")

    answered = confirm or (force and force_answers_prompt)
    if not dry_run and not answered and prompt is None:
        # BLOCK-WORTHY (docs/blocking-site-census.md): an approval by design.
        # An unwind rewrites task, queue, requirements and release state
        # across the project; a caller that neither confirmed nor can be
        # asked is refused before anything is read or changed.
        # CENSUS-ANCHOR: unwind-confirmation-required-01
        raise UnwindRcError(
            "unwind_rc.confirmation_required",
            format_error_contract(
                "unwind_rc.confirmation_required",
                f"unwind-rc would unwind RC {version} of project {project} across every state "
                "store, and the call carried no confirmation.",
                f"{_SCRIPT} --project {project} --key {version} --confirm",
            ) + "\n",
        )

    name = _resolve_project(ctx, project)
    # Decided before the repository is resolved, so an unresolved project's
    # skip line is printed even when a check below refuses.
    remote = _git_remote_name(ctx, name)
    origin_skip = _origin_skip_reason(ctx, name, say, remote)
    if not repo_root:
        repo_root = _repo_root(ctx, name)
    prefix = _branch_prefix(ctx, name)
    rc_branch = f"{prefix}rc/{version}"
    main_branch = f"{prefix}main"
    release_tag = f"{prefix}{version}"
    project_root = ctx.project_root(name)
    release_state = ctx.release_state_path(name)
    tmp_root = temp_root or _temp_root(ctx)

    _preflight(ctx, name, version, repo_root, release_tag, force)
    active = read_state_field(str(release_state), "Active RC")
    if active == "none":
        # BLOCK-WORTHY (docs/blocking-site-census.md): with no active RC
        # there is nothing to unwind (an earlier unwind already reset it).
        # CENSUS-ANCHOR: unwind-active-rc-none-01
        raise UnwindRcError(
            "unwind_rc.refused",
            f"ERROR: Active RC is 'none' in {release_state}\n\n"
            f"  There is no active RC to unwind for project '{name}'.\n"
            "  If the RC was already unwound, verify with verify-rc-state.sh.\n",
        )
    if active != version:
        if not force:
            # BLOCK-WORTHY (docs/blocking-site-census.md): unwinding an RC
            # other than the active one is almost always a typo and is
            # destructive; force is the deliberate override.
            # CENSUS-ANCHOR: unwind-active-rc-mismatch-01
            raise UnwindRcError(
                "unwind_rc.refused",
                "ERROR: Active RC mismatch.\n\n"
                f"  release-state.md shows Active RC = '{active}'\n"
                f"  You requested unwind of '{version}'\n\n"
                "  Only the active RC can be unwound without --force.\n"
                "  Use --force to override this check (e.g. partial-unwind recovery).\n",
            )
        warn_raw(
            "WARNING: Active RC mismatch (--force bypassing check).\n"
            f"  Active RC: {active}\n"
            f"  Requested: {version}\n\n"
        )

    inv = _inventory(ctx, name, version, repo_root, rc_branch, origin_skip, remote)
    ts = _local_now()
    halt_scope = "global" if (ctx.kanban_root / "HALT").is_file() else "per-project"
    _print_plan(
        say, inv, name=name, version=version, halt_scope=halt_scope, dry_run=dry_run,
        confirm=confirm, force=force, active=active, tmp_root=tmp_root, rc_branch=rc_branch,
        release_state=release_state, ts=ts,
    )
    if dry_run:
        say("Dry run complete.  No changes were made.")
        return UnwindOutcome(name, rc_branch, "planned")

    if not answered:
        say("This will permanently modify state across all stores listed above.")
        say("There is NO undo except the backup created in step (a).")
        say()
        answer = prompt(f"Proceed with unwind of {rc_branch}? Type 'yes' to continue: ")
        if answer is None:
            raise UnwindRcError("unwind_rc.declined", "")
        if answer.strip(" \t\n") != "yes":
            say()
            say("Unwind aborted. No changes were made.")
            raise UnwindRcError("unwind_rc.declined", "")
        say()

    backup_dir = _step_backup(say, ctx, name, version, tmp_root, inv)
    _step_git(say, warn, name, repo_root, main_branch, rc_branch, inv)
    cancelled = _step_tasks(say, warn_raw, ctx, name, version, ts, inv)
    _step_queues(say, inv)
    superseded = _step_requirements(say_raw, warn_raw, ctx, name, version, ts, inv)
    _step_plan_markers(say, inv)
    _step_reopen_bundled(say, warn, ctx, name, superseded, "PRIORITY", "h", "Priority", "priority")
    _step_reopen_bundled(say, warn, ctx, name, superseded, "BUG", "i", "Bug", "bug")
    _step_release_state(say, release_state)
    _step_discovery(say, warn, project_root, version)
    _step_rc_json(say, warn, project_root, version)

    say()
    say(_RULE)
    say("  unwind-rc.sh complete — all steps (a-l) finished.")
    say(f"  RC {version} has been fully unwound.")
    say(f"  Backup is at: {backup_dir}")
    say(f"  (To restore: cp -a {backup_dir}/. {os.path.dirname(str(project_root))}/)")
    say(_RULE)
    say()
    return UnwindOutcome(name, rc_branch, "unwound", backup_dir, tuple(cancelled))


def _preflight(
    ctx: OpsContext, name: str, version: str, repo: str, release_tag: str, force: bool
) -> None:
    """Checks 3-6 and the release-state.md presence check, in the script's order."""
    if not _VERSION_RE.match(version):
        # BLOCK-WORTHY (docs/blocking-site-census.md): a malformed version
        # names no RC; refused before any git or file work.
        # CENSUS-ANCHOR: unwind-version-format-01
        raise UnwindRcError(
            "unwind_rc.refused",
            f"ERROR: invalid version format: '{version}'\n  Expected format: vX.Y.Z  (e.g. v0.15.4)\n",
        )
    project_root = ctx.project_root(name)
    if not (ctx.kanban_root / "HALT").is_file() and not (project_root / "HALT").is_file():
        # BLOCK-WORTHY (docs/blocking-site-census.md): an invariant by
        # design. The unwind rewrites state the wake scripts read; running
        # it while they can still schedule work would race them.
        # CENSUS-ANCHOR: unwind-halt-missing-01
        raise UnwindRcError(
            "unwind_rc.refused",
            "ERROR: no HALT file found.\n\n"
            "  unwind-rc.sh requires the system to be halted before it will run.\n"
            "  This prevents the wake scripts from scheduling new work while the\n"
            "  unwind is in progress.\n\n"
            "  To halt the entire kanban, run: team/scripts/halt-global.sh\n\n"
            f"  To halt only this project, run: team/scripts/halt.sh --project {name}\n\n"
            "  After the unwind completes, resume with the matching unhalt verb:\n"
            "    team/scripts/unhalt-global.sh (if you halted globally), or\n"
            f"    team/scripts/unhalt.sh --project {name} (if you halted this project only).\n",
        )
    if not project_root.is_dir():
        # BLOCK-WORTHY (docs/blocking-site-census.md): no project directory,
        # so no state to unwind.
        # CENSUS-ANCHOR: unwind-project-dir-missing-01
        raise UnwindRcError(
            "unwind_rc.refused",
            f"ERROR: project directory not found: {project_root}\n"
            f"  Project '{name}' does not exist under projects/.\n",
        )
    if _git(repo, "rev-parse", "--verify", f"refs/tags/{release_tag}").returncode == 0:
        listed = _git(repo, "rev-list", "-n1", release_tag)
        commit = listed.stdout.rstrip("\n") if listed.returncode == 0 else "<unknown>"
        # BLOCK-WORTHY (docs/blocking-site-census.md): a tagged version has
        # shipped; unwinding it would rewrite released history.
        # CENSUS-ANCHOR: unwind-shipped-tag-01
        raise UnwindRcError(
            "unwind_rc.refused",
            f"ERROR: {version} is a shipped git tag.\n\n"
            "  unwind-rc.sh does not support unwinding a version that has already\n"
            "  been tagged and released.  The tag exists at:\n"
            f"    {commit}\n\n"
            "  Shipped releases cannot be unwound.  If you need to re-roll a\n"
            "  broken release, create a new patch version instead.\n",
        )
    release_state = ctx.release_state_path(name)
    if not release_state.is_file():
        # BLOCK-WORTHY (docs/blocking-site-census.md): without release-state.md
        # there is no record of which RC is active to check or reset.
        # CENSUS-ANCHOR: unwind-release-state-missing-01
        raise UnwindRcError(
            "unwind_rc.refused",
            f"ERROR: release-state.md not found: {release_state}\n"
            f"  Cannot determine Active RC for project '{name}'.\n",
        )


def _print_plan(say: Callable[..., None], inv: _Inventory, **kw) -> None:
    """Print the unwind plan exactly as the script always has."""
    version = kw["version"]
    say()
    say(_RULE)
    say("  unwind-rc.sh — Unwind Plan")
    say(_RULE)
    say()
    say(f"  Project:    {kw['name']}")
    say(f"  Version:    {version}")
    say(f"  HALT scope: {kw['halt_scope']}")
    if kw["dry_run"]:
        say("  Mode:       DRY RUN (no changes will be made)")
    if kw["confirm"]:
        say("  Flags:      --confirm (confirmation prompt answered non-interactively)")
    if kw["force"]:
        say("  Flags:      --force (Active-RC mismatch check bypassed)")
    say()
    say(f"  Active RC in release-state.md: {kw['active']}")
    say()
    say(_THIN_RULE)
    say("  What will be touched:")
    say(_THIN_RULE)
    say()

    say("  a. Backup")
    say(f"     Destination: {kw['tmp_root']}/unwind-rc-{version}-backup-<ts>/")
    say("     Scope: tasks/queues/, requirements/, priority/, release-state.md,")
    say("            release-state/ (per-RC JSON directory), matching task folders")
    say()

    rc_branch = kw["rc_branch"]
    say("  c. Git unwind")
    say(f"     - Delete local branch:  {rc_branch}  " + ("[EXISTS]" if inv.rc_local else "[not found — skip]"))
    if inv.origin_skip:
        remote_state = f"[skipped — {inv.origin_skip}]"
    else:
        remote_state = "[EXISTS]" if inv.rc_remote else "[not found — skip]"
    say(f"     - Delete remote branch: {inv.remote}/{rc_branch}  {remote_state}")
    say()

    say("  d. Task folders -> WONT-DO")
    if inv.tasks_active:
        say(f"     {len(inv.tasks_active)} active task(s) will be marked WONT-DO:")
        for task in inv.tasks_active:
            say(f"     - {task}")
    else:
        say(f"     No active task folders referencing {version} found.")
    if len(inv.tasks_all) > len(inv.tasks_active):
        say(f"     ({len(inv.tasks_all) - len(inv.tasks_active)} task(s) already in DONE/WONT-DO — no change)")
    say()

    say("  e. Queue entries -> [x]")
    if inv.queue_entries:
        say(f"     {len(inv.queue_entries)} open queue entry/entries will be closed:")
        for entry in inv.queue_entries:
            say(f"     - {entry}")
    else:
        say(f"     No open queue entries referencing {version} found.")
    say()

    say("  f. Requirements file -> renamed to *.SUPERSEDED-on-cancel-<ts>.md")
    if inv.requirements_file:
        base = os.path.basename(inv.requirements_file)
        say(f"     Source: {base}")
        say(f"     Target: {_strip_md(base)}.SUPERSEDED-on-cancel-{kw['ts']}.md")
    else:
        say(f"     No requirements file matching {version} found.")
    say()

    say("  g. PM plan markers -> removed")
    if inv.plan_markers:
        say(f"     {len(inv.plan_markers)} marker(s) will be removed:")
        for marker in inv.plan_markers:
            say(f"     - {os.path.basename(marker)}")
    else:
        say(f"     No .materialized.* markers referencing {version} found.")
    say()

    for letter, label, ids in (("h", "PRIORITY", inv.priority_to_flip), ("i", "BUG", inv.bugs_to_flip)):
        title = "Priority" if label == "PRIORITY" else "Bug"
        say(f"  {letter}. {title} backlog -> flip [x] back to [ ]")
        if ids:
            say(f"     {len(ids)} {label} entry/entries will be re-opened:")
            for item in ids:
                say(f"     - {item}")
        else:
            say(f"     No {label} backlog entries to re-open.")
        say()

    say("  j. release-state.md -> reset Active RC to none")
    say(f"     File: {kw['release_state']}")
    say()

    say("  k. Discovery state cache -> remove version-related files")
    if inv.discovery_files:
        say(f"     {len(inv.discovery_files)} file(s) will be removed:")
        for path in inv.discovery_files:
            say(f"     - {os.path.basename(path)}")
    else:
        say(f"     No discovery state files for {version} found.")
    say()

    say("  l. Per-RC release-state JSON -> flip outcome to cancelled")
    say(f"     File: {inv.rc_json}")
    if inv.rc_json_outcome is None:
        say("     (file not found — will skip; absent JSON is not an error)")
    elif inv.rc_json_outcome == "cancelled":
        say("     (already cancelled — will be a no-op)")
    else:
        say(f"     Current outcome: {inv.rc_json_outcome}  ->  cancelled")
    say()

    say(_RULE)
    say()


def _strip_md(name: str) -> str:
    """*name* without a trailing ``.md`` (``basename NAME .md``)."""
    return name[:-3] if name.endswith(".md") and name != ".md" else name


# ---------------------------------------------------------------------------
# Steps
# ---------------------------------------------------------------------------


def _step_backup(
    say: Callable[..., None], ctx: OpsContext, name: str, version: str, tmp_root: str, inv: _Inventory
) -> str:
    """Step a: copy the state stores into a fresh backup directory; return its path."""
    say("--- Step a: Backup ---")
    stamp = datetime.datetime.now().strftime("%Y%m%dT%H%M%S")
    backup_dir = f"{tmp_root}/unwind-rc-{version}-backup-{stamp}"
    say(f"  Creating backup directory: {backup_dir}")
    try:
        os.makedirs(backup_dir, exist_ok=True)
    except OSError as exc:
        raise UnwindRcError(
            "unwind_rc.backup_failed",
            f"ERROR: could not create backup directory: {backup_dir}\n",
            exit_code=EXIT_PARTIAL,
        ) from exc

    def _backup(src: pathlib.Path, rel: str) -> None:
        dest = os.path.join(backup_dir, rel)
        if not src.exists():
            say(f"  Skipped (not found): {rel}")
            return
        try:
            os.makedirs(os.path.dirname(dest), exist_ok=True)
            if src.is_dir():
                shutil.copytree(src, dest, symlinks=True, dirs_exist_ok=True)
            else:
                shutil.copy2(src, dest, follow_symlinks=False)
        except (OSError, shutil.Error) as exc:
            raise UnwindRcError(
                "unwind_rc.backup_failed",
                f"ERROR: backup copy failed: {src} -> {dest}\n",
                exit_code=EXIT_PARTIAL,
            ) from exc
        say(f"  Backed up: {rel}")

    tasks_dir = ctx.tasks_dir(name)
    _backup(tasks_dir / "queues", "tasks/queues")
    _backup(ctx.requirements_dir(name), "requirements")
    _backup(ctx.priority_dir(name), "priority")
    _backup(ctx.release_state_path(name), "release-state.md")
    _backup(ctx.project_root(name) / "release-state", "release-state")
    for task in inv.tasks_all:
        _backup(tasks_dir / task, f"tasks/{task}")
    say(f"  Backup complete: {backup_dir}")
    say(f"  (To restore: cp -a {backup_dir}/. <project-root>/)")
    say()
    return backup_dir


def _step_git(
    say: Callable[..., None], warn: Callable[[str], None],
    name: str, repo: str, main_branch: str, rc_branch: str, inv: _Inventory,
) -> None:
    """Step c: leave the RC branch, delete it locally and on the remote, delete orphan task branches.

    The remote (``inv.remote``) is checked and its RC branch deleted only for a project that
    pushes (``inv.origin_skip`` empty); otherwise one skip line is printed
    and no remote is contacted.
    """
    say("--- Step c: Git unwind ---")
    say(f"  Checking out {main_branch} ...")
    done = _git(repo, "checkout", main_branch, merge_stderr=True)
    say_text(say, done.stdout)
    if done.returncode != 0:
        raise UnwindRcError(
            "unwind_rc.git_failed",
            f"ERROR: could not checkout {main_branch} in {repo}\n",
            exit_code=EXIT_PARTIAL,
        )

    if _local_branch_exists(repo, rc_branch):
        say(f"  Deleting local branch: {rc_branch}")
        done = _git(repo, "branch", "-D", rc_branch, merge_stderr=True)
        say_text(say, done.stdout)
        if done.returncode != 0:
            raise UnwindRcError(
                "unwind_rc.git_failed",
                f"ERROR: failed to delete local branch {rc_branch}\n",
                exit_code=EXIT_PARTIAL,
            )
        say(f"  Local branch {rc_branch} deleted.")
    else:
        say(f"  Local branch {rc_branch} not found — skipping.")

    remote = inv.remote
    if inv.origin_skip:
        say(
            f"  [{inv.origin_skip}] skipping {remote} push for {name}: "
            f"git push {remote} --delete {rc_branch} (Step c)"
        )
    elif _remote_branch_exists(repo, remote, rc_branch):
        say(f"  Deleting remote branch: {remote}/{rc_branch}")
        done = _git(repo, "push", remote, "--delete", rc_branch, merge_stderr=True)
        say_text(say, done.stdout)
        if done.returncode != 0:
            raise UnwindRcError(
                "unwind_rc.git_failed",
                f"ERROR: failed to delete remote branch {remote}/{rc_branch}\n",
                exit_code=EXIT_PARTIAL,
            )
        say(f"  Remote branch {remote}/{rc_branch} deleted.")
    else:
        say(f"  Remote branch {remote}/{rc_branch} not found — skipping.")

    say()
    say(f"  Checking for orphan {_ORPHAN_BRANCH_PREFIX}* branches ...")
    cancelled = set(inv.tasks_all)
    orphans: list[str] = []
    listing = _git(repo, "branch")
    for line in _terminated_lines(listing.stdout if listing.returncode == 0 else ""):
        branch = line.removeprefix("  ").removeprefix(" ").removeprefix("* ")
        if branch.startswith(_ORPHAN_BRANCH_PREFIX) and branch[len("feature/"):] in cancelled:
            orphans.append(branch)
    if orphans:
        say(f"  WARNING: Found {len(orphans)} orphan feature branch(es) from cancelled tasks:")
        for branch in orphans:
            say(f"    {branch}")
            done = _git(repo, "branch", "-D", branch)
            say_text(say, done.stdout)
            if done.returncode == 0:
                say("    -> deleted.")
            else:
                say(f"    -> WARNING: could not delete {branch} — may already be gone or checked out.")
    else:
        say(f"  No orphan {_ORPHAN_BRANCH_PREFIX}* branches found.")
    say()


def say_text(say: Callable[..., None], text: str) -> None:
    """Pass a command's captured output on unchanged (*say* adds the final newline back)."""
    if text:
        say(text[:-1] if text.endswith("\n") else text)


def _step_tasks(
    say: Callable[..., None], warn_raw: Writer, ctx: OpsContext, name: str,
    version: str, ts: str, inv: _Inventory,
) -> list[str]:
    """Step d: WONT-DO and a Cancellation Note for every task folder naming the version."""
    say("--- Step d: Task folder state ---")
    flipped: list[str] = []
    already = missing = 0
    note = (
        f"This task was cancelled as part of `unwind-rc.sh --key {version}` "
        "unwinding the in-flight release candidate.  The state was flipped "
        "to WONT-DO by the unwind script; the work itself was not completed."
    )
    for task in inv.tasks_all:
        status = ctx.tasks_dir(name) / task / "status.md"
        if not status.is_file():
            say(f"  {task} — no status.md (skipped)")
            missing += 1
            continue
        try:
            text = status.read_text(encoding="utf-8")
        except OSError as exc:
            raise UnwindRcError(
                "unwind_rc.write_failed", f"ERROR: failed to update {status} ({exc})\n", exit_code=EXIT_PARTIAL
            ) from exc
        if _CANCELLATION_NOTE_RE.search(text):
            say(f"  {task} — already cancelled (skipping)")
            already += 1
            continue
        try:
            new_text = task_status.replace_section(text, "State", "WONT-DO")
        except task_status.TaskStatusError:
            # A status.md with no state to flip is left whole, note included,
            # for the operator to inspect; the unwind carries on.
            warn_raw(f"  WARNING: no `## State` heading found in {status}; left untouched\n")
            continue
        new_text = task_status.set_or_append_section(new_text, f"Cancellation Note ({ts})", note)
        try:
            atomic_write(status, new_text, prefix=".unwind_rc_tmp_", preserve_mode=True)
        except OSError as exc:
            raise UnwindRcError(
                "unwind_rc.write_failed", f"ERROR: failed to update {status} ({exc})\n", exit_code=EXIT_PARTIAL
            ) from exc
        say(f"  {task} -> WONT-DO (+ Cancellation Note)")
        flipped.append(task)
    say(f"  Step d summary: {len(flipped)} cancelled, {already} already-cancelled, {missing} missing-status.")
    say()
    return flipped


def _step_queues(say: Callable[..., None], inv: _Inventory) -> None:
    """Step e: set every queue line of a cancelled task to ``[x]``."""
    say("--- Step e: Queue caches ---")
    flipped_total = already_total = 0
    if inv.tasks_all:
        for queue in inv.queue_files:
            if not queue.is_file():
                continue
            flipped = already = 0
            try:
                for task in inv.tasks_all:
                    markers = ops_write.queue_markers(queue, task)
                    done = sum(1 for marker in markers if marker == "x")
                    already += done
                    if len(markers) > done:
                        flipped += len(markers) - done
                        ops_write.set_marker(queue, task, "x")
            except OpsError as exc:
                raise UnwindRcError(
                    "unwind_rc.write_failed",
                    f"ERROR: queue rewrite failed for {queue}\n",
                    exit_code=EXIT_PARTIAL,
                ) from exc
            if flipped or already:
                say(f"  {queue.name}: flipped={flipped} already={already}")
            flipped_total += flipped
            already_total += already
    say(f"  Step e summary: {flipped_total} flipped, {already_total} already-[x].")
    say()


def _step_requirements(
    say_raw: Writer, warn_raw: Writer, ctx: OpsContext, name: str, version: str, ts: str, inv: _Inventory
) -> str:
    """Step f: rename the requirements file SUPERSEDED; return the superseded file's path ("" if none)."""

    def say(line: str = "") -> None:
        say_raw(line + "\n")

    say("--- Step f: Requirements file ---")
    superseded = ""
    requirements_dir = ctx.requirements_dir(name)
    if not inv.requirements_file:
        say("  No requirements file to rename (none found, or already superseded).")
        if requirements_dir.is_dir():
            pattern = f"{glob.escape(version)}-*.SUPERSEDED-on-cancel-*.md"
            for candidate in sorted(glob.glob(str(requirements_dir / pattern))):
                if os.path.isfile(candidate):
                    superseded = candidate
                    say(f"  Found already-superseded file: {os.path.basename(candidate)}")
                    break
        if superseded:
            _close_superseded(warn_raw, superseded)
        say()
        return superseded

    source = inv.requirements_file
    directory = os.path.dirname(source)
    new_name = f"{_strip_md(os.path.basename(source))}.SUPERSEDED-on-cancel-{ts}.md"
    target = os.path.join(directory, new_name)
    if os.path.exists(target):
        say(f"  Target already exists: {new_name} — skipping rename.")
        _close_superseded(warn_raw, target)
        say()
        return target

    tracked = (
        _git(directory, "rev-parse", "--show-toplevel").returncode == 0
        and _git(directory, "ls-files", "--error-unmatch", "--", source).returncode == 0
    )
    if tracked:
        say(f"  git mv: {os.path.basename(source)} -> {new_name}")
        done = _git(directory, "mv", "--", source, target)
        if done.stdout:
            say_raw(done.stdout)
        if done.stderr:
            warn_raw(done.stderr)
        if done.returncode != 0:
            raise UnwindRcError(
                "unwind_rc.write_failed", f"ERROR: git mv failed for {source}\n", exit_code=EXIT_PARTIAL
            )
        if _close_superseded(warn_raw, target):
            staged = _git(directory, "add", "--", target)
            if staged.returncode != 0:
                warn_raw(f"  WARNING: could not stage the ## Status change in {target}\n")
    else:
        say(f"  mv (untracked): {os.path.basename(source)} -> {new_name}")
        try:
            os.rename(source, target)
        except OSError as exc:
            raise UnwindRcError(
                "unwind_rc.write_failed", f"ERROR: mv failed for {source}\n", exit_code=EXIT_PARTIAL
            ) from exc
        _close_superseded(warn_raw, target)
    say()
    return target


def _close_superseded(warn_raw: Writer, path: str) -> bool:
    """Set a superseded requirements file's ``## Status`` to ``wont-do``.

    A superseded bundle will never ship, so it is closed: discovery's mint
    governor and requirements-queue scan then treat it as finished rather
    than as a pending bundle. Returns True when the file was rewritten;
    False, leaving it as it was, when it already reads ``wont-do`` or when
    it cannot be rewritten (warned on *warn_raw*).
    """
    if read_state_field(path, "Status").strip().lower() == _SUPERSEDED_STATUS:
        return False
    if not _set_status_line(path, _SUPERSEDED_STATUS):
        warn_raw(f"  WARNING: could not set ## Status to {_SUPERSEDED_STATUS} in {path}\n")
        return False
    return True


def _step_plan_markers(say: Callable[..., None], inv: _Inventory) -> None:
    """Step g: remove the PM plan markers naming the version."""
    say("--- Step g: PM plan markers ---")
    removed = 0
    for marker in inv.plan_markers:
        if not os.path.isfile(marker):
            continue
        try:
            os.remove(marker)
        except OSError as exc:
            raise UnwindRcError(
                "unwind_rc.write_failed", f"ERROR: failed to remove {marker}\n", exit_code=EXIT_PARTIAL
            ) from exc
        say(f"  removed: {os.path.basename(marker)}")
        removed += 1
    say(f"  Step g summary: {removed} marker(s) removed.")
    say()


def bundled_items(requirements_text: str, kind: str) -> list[str]:
    """IDs of *kind* (``PRIORITY`` / ``BUG``) under ``## Bundled Items``, in order, repeats kept.

    Each line of the section contributes its first ``<KIND>-NNNN...`` token,
    with a trailing ``.md`` removed; the section ends at the next ``## ``
    heading.
    """
    ids: list[str] = []
    token = re.compile(r"\b(" + kind + r"-\d+[^\s`)\"]*)")
    in_section = False
    for line in requirements_text.splitlines():
        if line.strip() == _BUNDLED_ITEMS_HEADING:
            in_section = True
            continue
        if not in_section:
            continue
        if line.startswith("## "):
            break
        match = token.search(line)
        if match:
            ident = match.group(1)
            ids.append(ident[:-3] if ident.endswith(".md") else ident)
    return ids


def _intake_file(requirements_text: str, ident: str, fallback_dir: pathlib.Path) -> str:
    """The intake file for *ident*: a backquoted path naming it in the requirements file, else ``<dir>/<ident>*.md``."""
    pattern = re.compile(r"(?<=`)[^`\n]+" + re.escape(ident) + r"[^`\n]*(?=`)")
    for line in requirements_text.splitlines():
        match = pattern.search(line)
        if match:
            if os.path.isfile(match.group(0)):
                return match.group(0)
            break
    for candidate in sorted(glob.glob(str(fallback_dir / f"{glob.escape(ident)}*.md"))):
        if os.path.isfile(candidate):
            return candidate
    return ""


def _step_reopen_bundled(
    say: Callable[..., None], warn: Callable[[str], None], ctx: OpsContext, name: str,
    superseded: str, kind: str, letter: str, title: str, agent: str,
) -> None:
    """Steps h / i: reopen every bundled PRIORITY / BUG item (backlog ``[ ]``, ``## Status`` open)."""
    say(f"--- Step {letter}: {title} backlog ---")
    flipped = already = reset = already_open = 0
    if not superseded:
        say("  No superseded requirements file found — nothing to flip.")
    else:
        backlog = ctx.queue_path(name, agent)
        intake_dir = ctx.priority_dir(name) if kind == "PRIORITY" else ctx.bugs_dir(name)
        text = _read_text(superseded)
        label = backlog.name[: -len(".md")]
        for ident in bundled_items(text, kind):
            if backlog.is_file():
                if "x" in ops_write.queue_markers(backlog, ident):
                    ops_write.set_marker(backlog, ident, " ")
                    say(f"  {label}: {ident} -> [ ]")
                    flipped += 1
                else:
                    say(f"  {label}: {ident} — not [x] (already open or absent)")
                    already += 1
            intake = _intake_file(text, ident, intake_dir)
            if not intake:
                warn(f"  WARNING: could not find {kind.lower()} file for {ident} — ## Status not reset")
                continue
            current = read_state_field(intake, "Status")
            if current == "open":
                say(f"  {ident} — ## Status already open")
                already_open += 1
                continue
            if not _reset_status_line(intake):
                warn(f"  WARNING: could not reset ## Status in {intake}")
                continue
            say(f"  {ident} — ## Status reset to open (was: {current})")
            reset += 1
    say(
        f"  Step {letter} summary: backlog-flipped={flipped} already-open={already} "
        f"status-reset={reset} status-already-open={already_open}"
    )
    say()


def _reset_status_line(path: str) -> bool:
    """Set the value line of *path*'s ``## Status`` to ``open``, keeping every other line.

    Returns False, writing nothing, when the file cannot be read or written
    or carries no ``## Status`` heading.
    """
    return _set_status_line(path, "open")


def _set_status_line(path: str, value: str) -> bool:
    """Set the value line of *path*'s ``## Status`` to *value*, keeping every other line.

    The rewrite is :func:`md_fields.set_value_line`, committed by an atomic
    write that keeps the file's mode. Returns False, writing nothing, when
    the file cannot be read or written or carries no ``## Status`` heading.
    """
    try:
        text = pathlib.Path(path).read_text(encoding="utf-8")
        if md_fields.section_body(text, "Status") is None:
            return False
        atomic_write(path, md_fields.set_value_line(text, "Status", value),
                     prefix=".unwind_rc_tmp_", preserve_mode=True)
    except (OSError, UnicodeDecodeError):
        return False
    return True


def _field_as_written(path: pathlib.Path, heading: str) -> str:
    """A release-state field's value, or ``""`` when the file has no such heading."""
    lines = _read_text(path).splitlines()
    if not any(line.strip() == f"## {heading}" for line in lines):
        return ""
    return read_state_field(str(path), heading)


def _step_release_state(say: Callable[..., None], release_state: pathlib.Path) -> None:
    """Step j: reset the three RC fields to none; Last Released and every appended block are kept."""
    say("--- Step j: release-state.md ---")
    active = _field_as_written(release_state, "Active RC")
    opened_at = _field_as_written(release_state, "RC Opened At")
    opened_by = _field_as_written(release_state, "RC Opened By Task")
    if active == opened_at == opened_by == "none":
        say("  release-state.md: already reset — nothing to do.")
        say()
        return
    try:
        write_rc_state.update_release_state_fields(
            str(release_state),
            {"Active RC": "none", "RC Opened At": "none", "RC Opened By Task": "none"},
        )
    except (OSError, ValueError) as exc:
        raise UnwindRcError(
            "unwind_rc.write_failed", "ERROR: failed to reset release-state.md\n", exit_code=EXIT_PARTIAL
        ) from exc
    say(f"  Active RC        -> none  (was: {active})")
    say(f"  RC Opened At     -> none  (was: {opened_at})")
    say(f"  RC Opened By Task-> none  (was: {opened_by})")
    say(f"  Last Released      preserved: {_field_as_written(release_state, 'Last Released')}")
    say(f"  Last Released At   preserved: {_field_as_written(release_state, 'Last Released At')}")
    say(f"  Last Released By Task preserved: {_field_as_written(release_state, 'Last Released By Task')}")
    say()


def _step_discovery(
    say: Callable[..., None], warn: Callable[[str], None], project_root: pathlib.Path, version: str
) -> None:
    """Step k: remove discovery-cache files (two levels deep) whose names carry the version."""
    say("--- Step k: Discovery state cache ---")
    discovery_dir = project_root / ".discovery-state"
    removed = 0
    if not discovery_dir.is_dir():
        say("  No .discovery-state directory found — nothing to remove.")
    else:
        for path in _find_names(discovery_dir, f"*{version}*", max_depth=2):
            if not os.path.isfile(path):
                continue
            try:
                os.remove(path)
            except OSError:
                warn(f"  WARNING: could not remove {path}")
                continue
            say(f"  removed: {os.path.basename(path)}")
            removed += 1
    say(f"  Step k summary: {removed} file(s) removed.")
    say()


def _step_rc_json(
    say: Callable[..., None], warn: Callable[[str], None], project_root: pathlib.Path, version: str
) -> None:
    """Step l: mark the per-RC record cancelled; an absent record is skipped, a failure warned."""
    say("--- Step l: Per-RC release-state JSON ---")
    record = project_root / "release-state" / f"{version}.json"
    closed_at = datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    if not record.is_file():
        say(f"  Per-RC JSON not found: {record}")
        say("  Skipping — absent JSON is not an error (RC may have been unwound before open-rc wrote it).")
    else:
        try:
            write_rc_state.write_cancel(str(record), version, closed_at, say=say)
        except Exception:  # noqa: BLE001 -- non-blocking by contract
            warn(f"  WARNING: write_rc_state.py cancel returned non-zero for {record} — JSON may be unchanged.")
        else:
            say(f"  Per-RC JSON reconciled: outcome=cancelled, closed_at={closed_at}")
    say()


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def _cli_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="python3 -m pgai_agent_kanban.cm unwind_rc", add_help=False)
    parser.add_argument(
        "--kanban-root",
        required=True,
        help="kanban root holding projects.cfg and projects/ (required)",
    )
    parser.add_argument(
        "--project",
        required=True,
        help="project name: exact, or an unambiguous prefix of a registered name (required)",
    )
    parser.add_argument(
        "--key",
        required=True,
        help="the in-flight RC version to unwind, vX.Y.Z (required)",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="print the plan and stop; nothing is changed and no confirmation is needed",
    )
    parser.add_argument(
        "--confirm",
        action="store_true",
        help="proceed without asking; bypasses no check",
    )
    parser.add_argument(
        "--force",
        action="store_true",
        help="bypass only the Active-RC mismatch check; also answers the confirmation prompt",
    )
    parser.add_argument(
        "--repo-root",
        default="",
        help="repository to act on (default: the project's configured dev tree)",
    )
    parser.add_argument(
        "--temp-root",
        default="",
        help="where the backup directory goes (default: the framework temp root)",
    )
    return parser


def _cli_main(argv: Optional[list[str]] = None) -> int:
    """Run ``unwind_rc`` from the command line (see the module docstring).

    ``--force`` also answers the prompt here, as the script's ``--force``
    always has. Prints the narration to stdout; a refusal's remaining text
    goes to stderr and the exit code is :func:`unwind_exit_code`'s.
    """
    args = list(sys.argv[1:] if argv is None else argv)
    if not args or args[0] not in CLI_VERBS:
        _write_stderr(f"cm: VERB must be one of: {', '.join(CLI_VERBS)}\n")
        return EXIT_REFUSED
    opts = _cli_parser().parse_args(args[1:])
    ctx = OpsContext(kanban_root=pathlib.Path(opts.kanban_root))
    try:
        unwind_rc(
            ctx, opts.project, opts.key,
            dry_run=opts.dry_run, confirm=opts.confirm, force=opts.force,
            force_answers_prompt=True, prompt=_cancel._stdin_prompt,
            repo_root=opts.repo_root or None, temp_root=opts.temp_root or None,
        )
    except UnwindRcError as exc:
        if exc.text:
            _write_stderr(exc.text)
        return unwind_exit_code(exc)
    return 0
