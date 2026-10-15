"""
cancel.py — the ``cancel-rc`` operator verb: cleanly abandon an active
release candidate.

One implementation behind ``team/scripts/cm/cancel-rc.sh`` (which reaches
:func:`cancel_rc` through ``pp_run_ops pgai_agent_kanban.cm cancel_rc``) and
``POST /operations/cancel-rc`` (which calls :func:`cancel_rc` in process).
The script keeps flag parsing, ``--help`` and the project-name fallback to
``PGAI_PROJECT_NAME``; everything it prints and every change it makes happen
here, so both surfaces refuse alike, narrate alike and leave the same bytes
on disk.

What a cancel touches
---------------------
- ``<prefix>rc/<version>`` on the project's remote: deleted with ``git
  push <remote> --delete`` when it exists there and the project pushes (see
  "Push decision and the remote boundary" below). ``<remote>`` is
  ``project.cfg [project] git_remote_name``, ``origin`` when absent or
  blank (:func:`git_remote_name`); every remote step, ref and narration line
  below names it.
- ``<prefix>rc/<version>`` locally: deleted with ``git branch -D`` when it
  exists, switching to ``<prefix>main`` first when it is checked out.
- ``release-state.md``: Active RC, RC Opened At and RC Opened By Task are
  reset to ``none`` through :mod:`pgai_agent_kanban.cm.write_rc_state`;
  ``## Last Released`` is read first, as a section ending at the next
  heading, and written back unchanged — an absent heading is written as
  ``none``, and a heading present with no value is left exactly as written
  (with a warning) rather than given any other field's value — and every
  appended block is kept byte-for-byte. The repository is then left on
  ``<prefix>main`` (fast-forwarded from ``<remote>/<prefix>main`` when the
  project pushes).
- ``release-state/<version>.json``: marked cancelled, then archived to
  ``release-state/history/<version>-cancelled-<timestamp>.json``, freeing the
  version for a later open. A failure here is a warning, never a failed
  cancel. A re-run that finds the cancel otherwise complete (Active RC
  ``none``, no RC branch anywhere) still closes a record left in the slot
  by an earlier run that stopped part-way, and reports
  ``already_cancelled``; a slot that is empty (the record already archived)
  stays empty.

Task folders, queue files, requirements, priority and bug files, tags and
the main branch on the remote are never changed. Tasks whose README names the
version and whose ``## State`` is not DONE or WONT-DO are listed as pending
so the operator can close them.

Gates, in order
---------------
0. The project name is one plain name (``api.validation.require_component``:
   no ``/``, ``..``, control character, glob character or leading ``-``).
   Anything else refuses (``cancel_rc.project_invalid``, dual-channel,
   exit 1) before any projects.cfg or project.cfg is read and before any
   git command runs, so a traversal name never chooses the repository the
   branch deletes act on.
1. Confirmation: ``yes=True``, or a *prompt* that answers yes. With neither,
   the call refuses before reading anything (``cancel_rc.confirmation_required``).
2. The push decision (:func:`push_decision`), taken before the repository
   is resolved, so an unresolved project's skip line is printed even when
   the next gate refuses. It never refuses.
3. Project configuration: ``project.cfg`` (or a legacy ``PROJECT.cfg``) with
   a ``dev_tree_path``, unless *repo_root* names the repository directly; a
   ``branch_prefix`` that forms a branch name; a ``git_remote_name`` that is
   absent, blank or a valid remote name (anything else refuses
   dual-channel, ``git_remote_name.invalid``).
4. The version is ``vX.Y.Z``.
5. The repository is on ``<prefix>main`` or ``<prefix>rc/<version>``.
6. ``release-state.md`` exists.
7. Nothing left to cancel (Active RC ``none``, no RC branch locally or --
   for a project that pushes -- on the remote): a clean no-op, exit 0.
8. Active RC is ``none`` (partial-cancel recovery) or the version being
   cancelled. ``force=True`` bypasses this one check and nothing else: the
   branch and the per-RC record of *version* are cleaned up, and
   ``release-state.md``, which describes a different RC, is left unchanged.
   ``force`` never stands in for confirmation.

The refusals in steps 3-6 and 8 print plain ``ERROR:`` lines to stderr and
exit 1, which the REST twin lifts under its fallback key
``cancel_rc.refused``. A failing git command ends the cancel with git's own
exit code, its stderr passed through unchanged.

Push decision and the remote boundary
-------------------------------------
Whether the cancel may touch the remote is decided by the shared push-decision
resolver (:func:`pgai_agent_kanban.lib.push_decision.resolve_push_decision`),
the one rule every push site applies: the project must be registered in
``projects.cfg``, its own ``project.cfg`` (or legacy ``PROJECT.cfg``) must
exist, and that file must name a ``dev_tree_path``. *repo_root* and an
inherited ``PGAI_DEV_TREE_PATH`` never make a project pushable.

- ``push``: a resolved project whose ``push_to_remote`` is absent, blank,
  ``true`` or anything but ``false`` -- the default is unchanged. Narrated
  ``[cm-cancel-rc] Push policy: push_to_remote=true ...``.
- ``no-push``: a resolved project with ``push_to_remote = false``. Narrated
  ``[cm-cancel-rc] Push policy: push_to_remote=false ...``.
- ``unresolved``: one line, ``[cm-cancel-rc] skipped-unresolved-project:
  project '<name>': missing <piece>: <detail>; nothing pushed and no remote
  contacted`` (``<piece>`` is ``registration``, ``project-cfg`` or
  ``dev-tree-path``), in place of the Push policy line. The cancel then runs
  exactly as for ``no-push``.

A project that does not push (``no-push`` or ``unresolved``) contacts no
remote at all: no fetch, no ``ls-remote`` remote branch check, no remote
branch delete and no fast-forward from the remote. For a project that pushes,
the fetch is still skipped, with a narrated line, when ``PGAI_AGENT_ROLE``
names a role other than ``cm``; only the cm role touches the remote.

Output
------
Narration goes to the *out* callback and diagnostics to *err* (defaults:
this process's stdout and stderr), each receiving text exactly as the CLI
prints it, so a route collects both without capturing the server's streams.
A refusal is raised as :class:`CancelRcError`, whose ``text`` is what is
still to be printed to stderr and whose ``exit_code`` the CLI exits with.

CLI (``python3 -m pgai_agent_kanban.cm cancel_rc ...``; ``cm/__main__.py``
routes :data:`CLI_VERBS` here)::

    cancel_rc --kanban-root=ROOT --project=NAME --key=vX.Y.Z
              [--yes] [--force] [--repo-root=PATH]

Without ``--yes`` the CLI asks ``Proceed with cancellation of <branch>?
[y/N]`` on stdin (the prompt itself is shown only when stdin is a terminal).
"""

from __future__ import annotations

import argparse
import dataclasses
import datetime
import os
import pathlib
import re
import shlex
import subprocess
import sys
from typing import Callable, Optional

from pgai_agent_kanban.api.validation import ComponentRefused, require_component
from pgai_agent_kanban.cm import write_rc_state
from pgai_agent_kanban.cm.read_state_field import read_state_field, read_state_section
from pgai_agent_kanban.lib import git_remote as _git_remote
from pgai_agent_kanban.lib import ini
from pgai_agent_kanban.lib import push_decision as _push_decision
from pgai_agent_kanban.lib.error_contract import format_error_contract
from pgai_agent_kanban.lib.project_cfg import (
    project_cfg_file as _project_cfg_file,
    read_cfg_key as _read_cfg_key,
    strip_one_quote_each_side as _strip_one_quote_each_side,
)
from pgai_agent_kanban.ops.context import OpsContext
from pgai_agent_kanban.ops.errors import CliRefusal, OpsError
from pgai_agent_kanban.task_status import read_state

__all__ = [
    "CLI_VERBS",
    "CancelOutcome",
    "CancelRcError",
    "branch_prefix",
    "cancel_exit_code",
    "cancel_rc",
    "git_remote_name",
    "pending_tasks",
    "push_decision",
    "push_to_remote",
]

#: The CLI verbs this module serves (``cm/__main__.py`` routes them here).
CLI_VERBS: tuple[str, ...] = ("cancel_rc",)

EXIT_REFUSED = 1

_SCRIPT = "team/scripts/cm/cancel-rc.sh"
_SITE_TAG = "[cm-cancel-rc]"
_VERSION_RE = re.compile(r"^v[0-9]+\.[0-9]+\.[0-9]+$")
_BRANCH_PREFIX_RE = re.compile(r"^[a-zA-Z0-9_-]+$")
_FINISHED_STATES = ("DONE", "WONT-DO")
_YES_ANSWERS = ("y", "yes")

Writer = Callable[[str], None]
Prompt = Callable[[str], Optional[str]]


@dataclasses.dataclass(frozen=True)
class CancelOutcome:
    """What one :func:`cancel_rc` call did.

    Attributes:
        rc_branch:       The (prefixed) RC branch name.
        outcome:         ``cancelled``, or ``already_cancelled`` for the
                         nothing-to-do run.
        pending:         ``"<task id> (<state>)"`` for every task naming the
                         version that is not DONE or WONT-DO.
        release_state_reset: True when release-state.md's RC fields were reset.
    """

    rc_branch: str
    outcome: str
    pending: tuple[str, ...] = ()
    release_state_reset: bool = False


class CancelRcError(CliRefusal):
    """The cancel stopped; nothing after the stopping point was changed.

    Attributes:
        error:     A stable machine key (``cancel_rc.<reason>``).
        exit_code: The code ``cancel-rc.sh`` exits with (default 1).
        text:      What remains to be printed to stderr (the plain ``ERROR:``
                   lines, a dual-channel block, or ``""`` when the cause --
                   a failing git command's own stderr -- was already printed).
    """


def cancel_exit_code(exc: OpsError) -> int:
    """Return the exit code ``cancel-rc.sh`` exits with for *exc* (1 when it names none)."""
    return getattr(exc, "exit_code", EXIT_REFUSED)


def _write_stdout(text: str) -> None:
    sys.stdout.write(text)
    sys.stdout.flush()


def _write_stderr(text: str) -> None:
    sys.stderr.write(text)
    sys.stderr.flush()


# ---------------------------------------------------------------------------
# project.cfg -- the same answers project_paths.sh gives for the same file
# (pgai_agent_kanban.lib.project_cfg, imported above)
# ---------------------------------------------------------------------------


def push_decision(ctx: OpsContext, project: str) -> _push_decision.PushDecision:
    """The shared push-site decision for *project* under *ctx*'s kanban root.

    :func:`pgai_agent_kanban.lib.push_decision.resolve_push_decision`: ``push``
    only for a project registered in ``projects.cfg`` whose own project.cfg
    names ``dev_tree_path`` and does not set ``push_to_remote = false``;
    ``no-push`` for that ``false``; ``unresolved`` (with the
    ``skipped-unresolved-project`` line in ``status_line``) otherwise.
    """
    return _push_decision.resolve_push_decision(ctx.kanban_root, project)


def push_to_remote(ctx: OpsContext, project: str) -> bool:
    """True only when :func:`push_decision` answers ``push``.

    A resolved project with ``push_to_remote`` absent, blank or anything but
    the exact value ``false`` (one surrounding double quote stripped from
    each side) is True. ``push_to_remote = false`` is False, and so is every
    unresolved project: no project.cfg, a project not registered in
    projects.cfg, or a project.cfg without ``dev_tree_path`` -- an inherited
    ``PGAI_DEV_TREE_PATH`` never counts.
    """
    return push_decision(ctx, project).may_push


def branch_prefix(ctx: OpsContext, project: str) -> str:
    """``project.cfg [project] branch_prefix`` (``""`` when unset).

    Raises:
        CancelRcError: the configured prefix holds a character other than
            a letter, digit, ``_`` or ``-`` (exit 1, the two
            ``project_paths.sh`` lines naming it).
    """
    cfg_file = _project_cfg_file(ctx.project_root(project))
    if cfg_file is None:
        return ""
    raw = _strip_one_quote_each_side(_read_cfg_key(cfg_file, "project", "branch_prefix"), '"')
    if not raw or _BRANCH_PREFIX_RE.match(raw):
        return raw
    # BLOCK-WORTHY (docs/blocking-site-census.md): a prefix that cannot form
    # a valid branch name would point every git step at the wrong branch.
    # CENSUS-ANCHOR: cancel-branch-prefix-01
    raise CancelRcError(
        "cancel_rc.refused",
        f"project_paths.sh: validate_branch_prefix: invalid branch_prefix '{raw}' (allowed "
        "characters: a-z A-Z 0-9 _ -; spaces, slashes, dots, and other special characters "
        "are not permitted)\n"
        f"project_paths.sh: pp_branch_prefix: invalid branch_prefix in config for project '{project}'\n",
    )


def git_remote_name(ctx: OpsContext, project: str) -> str:
    """The git remote *project*'s release steps name (``pp_git_remote_name``).

    ``project.cfg [project] git_remote_name`` (or a legacy ``PROJECT.cfg``),
    ``origin`` when absent, blank or when the project has no config file
    (:func:`pgai_agent_kanban.lib.git_remote.resolve_git_remote_name`).

    Raises:
        CancelRcError: the configured value is not a valid remote name (exit
            1, ``git_remote_name.invalid``, the resolver's dual-channel block).
    """
    try:
        return _git_remote.resolve_git_remote_name(ctx.project_root(project), project)
    except _git_remote.InvalidRemoteName as exc:
        # BLOCK-WORTHY (docs/blocking-site-census.md): a value that cannot
        # name a remote would point the fetch, the RC-branch delete and the
        # fast-forward at a destination nobody configured.
        # CENSUS-ANCHOR: cancel-git-remote-name-01
        raise CancelRcError(exc.error, exc.text) from exc


def _dev_tree_path(ctx: OpsContext, project: str) -> str:
    """``dev_tree_path`` from project.cfg / PROJECT.cfg (the ``pp_load_config`` read).

    Raises:
        CancelRcError: neither config file exists, or the value is empty.
    """
    project_root = ctx.project_root(project)
    new_cfg = project_root / "project.cfg"
    old_cfg = project_root / "PROJECT.cfg"
    if new_cfg.is_file():
        value = ini.read_line_value(new_cfg, "project", "dev_tree_path") or ""
    elif old_cfg.is_file():
        value = _read_cfg_key(old_cfg, "project", "dev_tree_path")
    else:
        # BLOCK-WORTHY (docs/blocking-site-census.md): with no project
        # configuration there is no repository to cancel the RC in.
        # CENSUS-ANCHOR: cancel-project-cfg-missing-01
        raise CancelRcError(
            "cancel_rc.refused",
            f"project_paths.sh: PROJECT.cfg not found at {old_cfg} (and project.cfg not found at {new_cfg})\n"
            f"ERROR: could not load project.cfg for project '{project}'\n"
            f"  Expected: {new_cfg}\n",
        )
    if not value:
        # BLOCK-WORTHY (docs/blocking-site-census.md): no dev tree configured,
        # so no repository to cancel the RC in.
        # CENSUS-ANCHOR: cancel-dev-tree-unset-01
        raise CancelRcError(
            "cancel_rc.refused",
            f"ERROR: dev_tree_path is not set in project.cfg for project '{project}'\n"
            f"  Add 'dev_tree_path=<path>' to {project_root}/project.cfg\n",
        )
    return value


def _agent_role_is_non_cm() -> bool:
    """True when ``PGAI_AGENT_ROLE`` names a role other than cm (``pp_agent_role_is_non_cm``)."""
    role = os.environ.get("PGAI_AGENT_ROLE", "").lower()
    return bool(role) and role != "cm"


# ---------------------------------------------------------------------------
# git
# ---------------------------------------------------------------------------


def _run_git(repo: str, args: list[str]) -> subprocess.CompletedProcess:
    """Run ``git -C <repo> <args>`` and capture its output as text.

    A missing git binary reads as the shell's exit 127, so every caller
    treats it as the failing command it would be in bash.
    """
    try:
        return subprocess.run(
            ["git", "-C", repo, *args],
            capture_output=True, text=True, errors="replace", check=False,
            stdin=subprocess.DEVNULL,
        )
    except FileNotFoundError:
        return subprocess.CompletedProcess(["git", *args], 127, "", "git: command not found\n")


class _Git:
    """The git steps of a cancel, with output passed through to *out* / *err*."""

    def __init__(self, repo: str, out: Writer, err: Writer) -> None:
        self.repo = repo
        self.out = out
        self.err = err

    def quiet(self, *args: str) -> subprocess.CompletedProcess:
        """Run a probe whose output is discarded (the script's ``>/dev/null 2>&1``)."""
        return _run_git(self.repo, list(args))

    def step(self, *args: str) -> None:
        """Run a step whose output is shown; a failure ends the cancel with git's exit code."""
        done = _run_git(self.repo, list(args))
        if done.stdout:
            self.out(done.stdout)
        if done.stderr:
            self.err(done.stderr)
        if done.returncode != 0:
            raise CancelRcError("cancel_rc.git_failed", "", exit_code=done.returncode)

    def current_branch(self) -> str:
        done = self.quiet("symbolic-ref", "--short", "HEAD")
        branch = done.stdout.rstrip("\n") if done.returncode == 0 else ""
        return branch or "DETACHED"


# ---------------------------------------------------------------------------
# Pending tasks
# ---------------------------------------------------------------------------


def pending_tasks(
    ctx: OpsContext, project: str, version: str, err: Optional[Writer] = None
) -> list[str]:
    """``"<task id> (<state>)"`` for each task naming *version* that is not finished.

    A task is a candidate when its folder holds both ``status.md`` and a
    ``README.md`` that matches *version* (as the script's ``grep`` pattern,
    where ``.`` matches any character). Its ``## State`` is read through
    :func:`pgai_agent_kanban.task_status.read_state`; DONE and WONT-DO are
    finished. Folders are taken in code-point order, hidden ones skipped.
    """
    emit_err = err or _write_stderr
    tasks_dir = ctx.tasks_dir(project)
    try:
        names = sorted(n for n in os.listdir(tasks_dir) if not n.startswith("."))
    except OSError:
        return []
    pattern = re.compile(version.encode("ascii", errors="replace"))
    pending: list[str] = []
    for name in names:
        folder = tasks_dir / name
        if not folder.is_dir():
            continue
        status_file = f"{folder}//status.md"
        readme = folder / "README.md"
        if not os.path.isfile(status_file) or not readme.is_file():
            continue
        try:
            if not pattern.search(readme.read_bytes()):
                continue
        except OSError:
            continue
        try:
            state = read_state(status_file)
        except Exception:  # noqa: BLE001 -- an unreadable state never hides live work
            emit_err(f"WARNING: could not read ## State from {status_file}; listing the task as pending.\n")
            state = ""
        if state in _FINISHED_STATES:
            continue
        pending.append(f"{name} ({state})")
    return pending


# ---------------------------------------------------------------------------
# The verb
# ---------------------------------------------------------------------------


def cancel_rc(
    ctx: OpsContext,
    project: str,
    version: str,
    *,
    yes: bool = False,
    force: bool = False,
    prompt: Optional[Prompt] = None,
    repo_root: Optional[str] = None,
    out: Optional[Writer] = None,
    err: Optional[Writer] = None,
) -> CancelOutcome:
    """Cancel *project*'s active RC *version* (see the module docstring).

    Args:
        ctx:       Kanban root.
        project:   Project name, used as given (no prefix matching).
        version:   The RC version, ``vX.Y.Z``.
        yes:       The confirmation: proceed without asking.
        force:     Bypass only the Active-RC mismatch check. Never a
                   confirmation.
        prompt:    Asked when *yes* is False, after the plan is printed;
                   receives the question and returns the answer line, or
                   None at end of input. With neither *yes* nor *prompt*
                   the call refuses before doing anything.
        repo_root: The repository to act on, instead of project.cfg's
                   ``dev_tree_path`` (the script's ``REPO_ROOT``).
        out, err:  Receive stdout / stderr text as it is produced (default:
                   this process's streams).

    Returns:
        The :class:`CancelOutcome`.

    Raises:
        CancelRcError: a gate refused (the first, before anything is read:
            *project* is not one plain name, ``cancel_rc.project_invalid``),
            the prompt was declined or unanswered, a git step failed, or
            release-state.md could not be written. Nothing after the
            stopping point was changed.
    """
    say_raw = out or _write_stdout
    warn_raw = err or _write_stderr

    def say(line: str = "") -> None:
        say_raw(line + "\n")

    try:
        require_component(project, "project")
    except ComponentRefused as exc:
        registry = ctx.kanban_root / "projects.cfg"
        # BLOCK-WORTHY (docs/blocking-site-census.md): an invariant by design.
        # The project name picks the project.cfg whose dev_tree_path is the
        # repository the cancel deletes branches in; a name that is not one
        # plain name could point that read -- and the git steps -- outside
        # the kanban root. Refused before any file is read or git runs.
        # CENSUS-ANCHOR: cancel-project-not-component-01
        raise CancelRcError(
            "cancel_rc.project_invalid",
            exc.block("cancel_rc", f"grep -o '^\\[project:[^]]*\\]' {shlex.quote(str(registry))}") + "\n",
        ) from exc

    if not yes and prompt is None:
        # BLOCK-WORTHY (docs/blocking-site-census.md): an approval by design.
        # Cancelling deletes the RC branch and resets release-state.md; a
        # caller that neither confirmed nor can be asked is refused before
        # anything is read or changed.
        # CENSUS-ANCHOR: cancel-confirmation-required-01
        raise CancelRcError(
            "cancel_rc.confirmation_required",
            format_error_contract(
                "cancel_rc.confirmation_required",
                f"cancel-rc would delete the RC branch for {version} in project {project} and "
                "reset its release-state.md, and the call carried no confirmation.",
                f"{_SCRIPT} --project {project} --key {version}",
            ) + "\n",
        )

    # Decided before the repository is resolved, so an unresolved project's
    # skip line is printed even when the dev-tree gate below refuses.
    decision = push_decision(ctx, project)
    if decision.decision == _push_decision.UNRESOLVED:
        say(f"{_SITE_TAG} {decision.status_line}")
    push = decision.may_push

    if not repo_root:
        repo_root = _dev_tree_path(ctx, project)
    prefix = branch_prefix(ctx, project)
    main_branch = f"{prefix}main"
    remote = git_remote_name(ctx, project)
    if push:
        say(f"{_SITE_TAG} Push policy: push_to_remote=true — {remote} branch delete will proceed.")
    elif decision.decision == _push_decision.NO_PUSH:
        say(f"{_SITE_TAG} Push policy: push_to_remote=false — skipping {remote} operations. Local cleanup only.")
    release_state = ctx.release_state_path(project)

    if not _VERSION_RE.match(version):
        # BLOCK-WORTHY (docs/blocking-site-census.md): a malformed version
        # names no RC branch; refused before any git or file work.
        # CENSUS-ANCHOR: cancel-version-format-01
        raise CancelRcError(
            "cancel_rc.refused",
            f"ERROR: invalid version format: '{version}'\nExpected format: vX.Y.Z (e.g. v0.15.4)\n",
        )
    rc_branch = f"{prefix}rc/{version}"
    git = _Git(repo_root, say_raw, warn_raw)

    if not push:
        say(f"push_to_remote=false — skipping git fetch {remote} (local-only install).")
    elif _agent_role_is_non_cm():
        say(
            f"PGAI_AGENT_ROLE={os.environ.get('PGAI_AGENT_ROLE', '')} — skipping git fetch {remote} "
            "(only the cm role touches the remote; local refs are used as-is)."
        )
    else:
        say(f"Fetching {remote}...")
        git.step("fetch", remote)

    current = git.current_branch()
    if current not in (main_branch, rc_branch):
        # BLOCK-WORTHY (docs/blocking-site-census.md): deleting or switching
        # branches from an unrelated checkout could discard someone's work.
        # CENSUS-ANCHOR: cancel-current-branch-01
        raise CancelRcError(
            "cancel_rc.refused",
            f"ERROR: must be on '{main_branch}' or '{rc_branch}' to cancel this RC\n"
            f"Current branch: {current}\n"
            f"Please checkout {main_branch} or {rc_branch} first.\n",
        )

    if not release_state.is_file():
        # BLOCK-WORTHY (docs/blocking-site-census.md): without release-state.md
        # there is no record of which RC is active to check or reset.
        # CENSUS-ANCHOR: cancel-release-state-missing-01
        raise CancelRcError(
            "cancel_rc.refused",
            f"ERROR: project-scoped release-state.md not found: {release_state}\n"
            "Expected path: $KANBAN_ROOT/projects/<name>/release-state.md\n",
        )
    active = read_state_field(str(release_state), "Active RC")
    # Carried forward unchanged by the reset below; never computed or cleared.
    # Read as a section ending at the next heading, so an empty field is ""
    # (left as written) and never the value of a block after it.
    last_released = read_state_section(str(release_state), "Last Released")

    local_exists = git.quiet("rev-parse", "--verify", f"refs/heads/{rc_branch}").returncode == 0
    # Only a project that pushes asks its remote; one that does not contacts none.
    remote_exists = push and (
        git.quiet("ls-remote", "--exit-code", "--heads", remote, rc_branch).returncode == 0
    )

    if active == "none" and not local_exists and not remote_exists:
        warn_raw("INFO: RC cancellation already complete (idempotent run).\n")
        say("  Active RC on main: none")
        say(f"  {rc_branch}: not found locally or on {remote}")
        # An earlier run that stopped after the release-state reset (a
        # failed fast-forward of main, say) never reached the per-RC record:
        # close it now so it reads cancelled and the attempt count holds. A
        # record already archived to history/ has left its slot and is not
        # touched; no record is ever created here.
        if _rc_record_path(ctx, project, version).is_file():
            say("  Per-RC record still open from an earlier run; marking it cancelled.")
            _archive_rc_record(ctx, project, version, say, warn_raw)
            say()
            say("Nothing else to do. Exiting cleanly.")
        else:
            say()
            say("Nothing to do. Exiting cleanly.")
        return CancelOutcome(rc_branch, "already_cancelled")

    if active not in ("none", version):
        if not force:
            # BLOCK-WORTHY (docs/blocking-site-census.md): cancelling an RC
            # other than the active one is almost always a typo and hard to
            # undo; force is the deliberate override.
            # CENSUS-ANCHOR: cancel-active-rc-mismatch-01
            raise CancelRcError(
                "cancel_rc.refused",
                "ERROR: Active RC mismatch.\n"
                f"  release-state.md shows Active RC = '{active}'\n"
                f"  You requested cancel of: '{version}'\n"
                "  Only the active RC can be cancelled.\n",
            )
        warn_raw(
            "WARNING: Active RC mismatch (--force bypassing check).\n"
            f"  release-state.md shows Active RC = '{active}'\n"
            f"  Cancelling: '{version}'; release-state.md describes another RC and is left unchanged.\n"
        )
    reset_release_state = active == version

    say()
    say(f"RC to cancel: {rc_branch}")
    say(f"Current Active RC on main: {active}")
    say()
    pending = pending_tasks(ctx, project, version, warn_raw)
    if pending:
        say(f"Pending tasks for {version}:")
        for task in pending:
            say(f"  - {task}")
        say()
        say("NOTE: These tasks will NOT be automatically cancelled by this script.")
        say("      You should manually set them to WONT-DO after cancellation.")
        say()
    else:
        say(f"No pending tasks found for {version} in kanban root.")
        say()

    if not yes:
        say("This will:")
        if remote_exists:
            say(f"  - Delete {rc_branch} from {remote}")
        if local_exists:
            say(f"  - Delete local branch {rc_branch}")
        if reset_release_state:
            say("  - Reset release-state.md: Active RC -> none")
        say()
        say("This does NOT touch main, does NOT delete any tags.")
        say()
        answer = prompt(f"Proceed with cancellation of {rc_branch}? [y/N] ")
        if answer is None:
            raise CancelRcError("cancel_rc.declined", "")
        if answer.strip(" \t").lower() not in _YES_ANSWERS:
            say("Cancelled. No changes made.")
            raise CancelRcError("cancel_rc.declined", "")
        say()

    if push:
        if remote_exists:
            say(f"Deleting {rc_branch} from {remote}...")
            git.step("push", remote, "--delete", rc_branch)
            say(f"  Deleted {rc_branch} from {remote}.")
        else:
            say(f"  {rc_branch} not found on {remote} — skipping remote delete (idempotent).")
    else:
        say(
            f"[push_to_remote=false] skipping {remote} push for {project}: "
            f"git push {remote} --delete {rc_branch} (Step 8)"
        )

    if local_exists:
        if current == rc_branch:
            say(f"Switching to {main_branch} before deleting local branch...")
            git.step("checkout", main_branch)
            if push:
                git.step("merge", "--ff-only", f"{remote}/{main_branch}")
        say(f"Deleting local branch {rc_branch}...")
        git.step("branch", "-D", rc_branch)
        say(f"  Deleted local branch {rc_branch}.")
    else:
        say(f"  Local branch {rc_branch} not found — skipping local delete (idempotent).")

    if reset_release_state:
        say("Resetting project-scoped release-state.md (Active RC -> none)...")
        if last_released == "":
            warn_raw(
                "WARNING: the Last Released field has no value in release-state.md; it is left as written.\n"
                f"  Set it to 'none' or the last shipped vX.Y.Z version in {release_state}.\n"
            )
        _reset_release_state(release_state, last_released)
        say("  release-state.md reset.")
        say(f"  Path: {release_state}")
        say(f"Switching to {main_branch}...")
        git.step("checkout", main_branch)
        if push:
            git.step("merge", "--ff-only", f"{remote}/{main_branch}")
            say(f"  {main_branch} fast-forward updated from {remote}.")
        else:
            say(
                f"[push_to_remote=false] skipping {remote} pull for {project}: "
                f"merge --ff-only {remote}/{main_branch} (Step 10)"
            )
            say(f"  {main_branch} checked out locally; no pull (push_to_remote=false).")
    elif active == "none":
        say("  Active RC already none — skipping release-state.md reset (idempotent).")
    else:
        say(f"  Active RC is {active}, not {version} — release-state.md left unchanged (--force).")

    _archive_rc_record(ctx, project, version, say, warn_raw)

    say()
    say("RC cancellation complete.")
    say(f"  Cancelled: {rc_branch}")
    say(f"  Cancelled at: {datetime.datetime.now().astimezone().isoformat(timespec='seconds')}")
    say()
    if pending:
        say("Reminder: mark these tasks as WONT-DO in the kanban:")
        for task in pending:
            say(f"  - {task}")
        say()
    say("Reminder: close the requirements bundle intake item to prevent re-selection:")
    say(f"  close.sh --project {project} --key {version}-bugfix-bundle-<YYYYMMDD> --state wont-do")
    say("  (replace <YYYYMMDD> with the date suffix on the bundle file in requirements/)")
    say("  Leaving the bundle live re-arms the discovery re-selection path.")
    say()
    say("You may now open a new RC with scripts/cm/open-rc.sh <version>.")
    return CancelOutcome(rc_branch, "cancelled", tuple(pending), reset_release_state)


def _reset_release_state(release_state: pathlib.Path, last_released: Optional[str]) -> None:
    """Reset the three RC fields to none, writing Last Released back unchanged.

    *last_released* is the field as ``read_state_section`` read it: a value
    is written back as it was, ``None`` (no heading) is written as ``none``,
    and ``""`` (a heading with no value) is not named to the writer at all,
    so the empty field is left exactly as written.
    """
    updates = {"Active RC": "none", "RC Opened At": "none", "RC Opened By Task": "none"}
    if last_released is None:
        updates["Last Released"] = "none"
    elif last_released:
        updates["Last Released"] = last_released
    try:
        write_rc_state.update_release_state_fields(str(release_state), updates)
    except ValueError as exc:
        raise CancelRcError(
            "cancel_rc.write_failed", f"write_rc_state.py release-state: error: {exc}\n", exit_code=2
        ) from exc
    except OSError as exc:
        raise CancelRcError(
            "cancel_rc.write_failed", f"ERROR: could not rewrite {release_state}: {exc}\n"
        ) from exc


def _rc_record_path(ctx: OpsContext, project: str, version: str) -> pathlib.Path:
    """The per-RC record slot, ``<project root>/release-state/<version>.json``."""
    return ctx.project_root(project) / "release-state" / f"{version}.json"


def _archive_rc_record(ctx: OpsContext, project: str, version: str, say: Callable[[str], None], warn: Writer) -> None:
    """Mark ``release-state/<version>.json`` cancelled, then archive it to history/.

    Never fails the cancel: a write or archive failure is one warning line.
    """
    state_dir = ctx.project_root(project) / "release-state"
    record = str(_rc_record_path(ctx, project, version))
    closed_at = datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    try:
        state_dir.mkdir(parents=True, exist_ok=True)
    except OSError:
        pass
    try:
        write_rc_state.write_cancel(record, version, closed_at, say=say)
    except Exception:  # noqa: BLE001 -- non-blocking by contract
        warn(f"  WARNING: could not update per-RC release-state JSON at {record}\n")
    try:
        write_rc_state.archive_cancelled(record, version, closed_at, say=say)
    except Exception:  # noqa: BLE001 -- non-blocking by contract
        warn(f"  WARNING: could not archive per-RC release-state JSON from {record}\n")


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def _stdin_prompt(question: str) -> Optional[str]:
    """Ask on stdin as bash ``read -r -p`` does; None when no full line arrives.

    The question is shown (on stderr) only when stdin is a terminal. An
    answer cut off by end of input counts as no answer.
    """
    if sys.stdin is None:
        return None
    if sys.stdin.isatty():
        _write_stderr(question)
    line = sys.stdin.readline()
    if not line.endswith("\n"):
        return None
    return line[:-1]


def _cli_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="python3 -m pgai_agent_kanban.cm cancel_rc", add_help=False)
    parser.add_argument(
        "--kanban-root",
        required=True,
        help="kanban root holding projects.cfg and projects/ (required)",
    )
    parser.add_argument(
        "--project",
        required=True,
        help="project name, used as given with no prefix matching (required)",
    )
    parser.add_argument(
        "--key",
        required=True,
        help="the active RC version to cancel, vX.Y.Z (required)",
    )
    parser.add_argument(
        "--yes",
        action="store_true",
        help="proceed without asking for confirmation",
    )
    parser.add_argument(
        "--force",
        action="store_true",
        help="bypass only the Active-RC mismatch check; never a confirmation",
    )
    parser.add_argument(
        "--repo-root",
        default="",
        help="repository to act on (default: project.cfg's dev_tree_path)",
    )
    return parser


def _cli_main(argv: Optional[list[str]] = None) -> int:
    """Run ``cancel_rc`` from the command line (see the module docstring).

    Prints the narration to stdout; a refusal's remaining text goes to
    stderr and the exit code is :func:`cancel_exit_code`'s.
    """
    args = list(sys.argv[1:] if argv is None else argv)
    if not args or args[0] not in CLI_VERBS:
        _write_stderr(f"cm: VERB must be one of: {', '.join(CLI_VERBS)}\n")
        return EXIT_REFUSED
    opts = _cli_parser().parse_args(args[1:])
    ctx = OpsContext(kanban_root=pathlib.Path(opts.kanban_root))
    try:
        cancel_rc(
            ctx, opts.project, opts.key,
            yes=opts.yes, force=opts.force, prompt=_stdin_prompt,
            repo_root=opts.repo_root or None,
        )
    except CancelRcError as exc:
        if exc.text:
            _write_stderr(exc.text)
        return cancel_exit_code(exc)
    return 0
