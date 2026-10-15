"""
ship.py — the ``ship-rc`` operator verb: ship a release candidate by
squash-merging it directly into main (the manual, single-lane release path).

One implementation behind ``team/scripts/ship-rc.sh`` (which reaches
:func:`ship_rc` through ``pp_run_ops pgai_agent_kanban.cm ship_rc``) and
``POST /operations/ship-rc`` (which calls :func:`ship_rc` in process). The
script keeps flag parsing, ``--help`` and the ``PGAI_PROJECT_NAME`` fallback;
everything it prints and every change it makes happen here, so both surfaces
refuse alike, narrate alike and leave the same refs and bytes behind.

Pre-flight, in order (each refusal prints plain ``ERROR:`` lines to stderr
and exits 1 unless noted)
---------------------------------------------------------------------------
1. The version is ``vX.Y.Z``.
2. The project name resolves (exact, then unambiguous prefix, against
   ``projects.cfg``); a refusal prints the resolver's dual-channel block.
3. The repository the local steps run in: *repo_root*, else
   ``project.cfg``'s ``dev_tree_path``, else ``PGAI_DEV_TREE_PATH``
   (environment, then ``kanban.cfg [paths] dev_tree_path``), else the
   directory above this tree's ``scripts/``. The fallbacks after
   ``dev_tree_path`` only ever serve a project that does not push: whether
   the remote is touched is the push decision below, and a project whose
   project.cfg names no ``dev_tree_path`` never resolves for a push. A
   configured ``branch_prefix`` must form a branch name.
4. A dry run prints the plan (project, version, branch and tag names) and
   stops here; nothing is read from git and no push decision is taken.
5. The project's remote: ``project.cfg [project] git_remote_name``,
   ``origin`` when absent or blank
   (:func:`pgai_agent_kanban.cm.cancel.git_remote_name`); a value that is not
   a valid remote name refuses dual-channel (``git_remote_name.invalid``).
   Every remote step, ref and narration line below names ``<remote>``.
6. The push decision (see "Push decision and the remote boundary"). It
   never refuses.
7. The repository is a git repository, its working tree is clean, the tag
   ``<prefix><version>`` does not exist, and ``<prefix>rc/<version>`` exists
   locally and (for a project that pushes) on the remote.

What a ship does
----------------
Fetch the remote with tags; check out ``<prefix>main`` and fast-forward it
from ``<remote>/<prefix>main``; run the pre-squash hook; ``git merge --squash`` the
RC branch (a squash whose only conflicts are modify/delete ones is resolved
by taking the RC's side, ``cm-release: auto-resolved modify/delete: ...``);
commit ``Release <version>``; check the fidelity gate (the RC and main trees
must be identical); run the pre-tag hook; tag ``<prefix><version>``; run the
post-tag hook (a failure there is a warning); push main and the release tag
by its explicit ``refs/tags/<prefix><version>`` ref (never every local tag,
so another release lane's unpublished tags stay local); delete the RC branch
on the remote, then locally. Finally ``release-state.md``'s Active RC,
RC Opened At and RC Opened By Task are reset to ``none`` and Last Released
is raised to the shipped version through
:mod:`pgai_agent_kanban.cm.write_rc_state` -- only the RC headings the file
carries, every other line kept -- after reading them through
:func:`pgai_agent_kanban.cm.read_state_field.read_state_field`. Last Released
only ever moves up (a semver comparison): a value already higher than the
shipped version is kept. A missing release-state.md, or one that cannot be
rewritten, is a warning: the release has already shipped. Task folders and
queue files are never touched.

The reset is an RC close, so a HALT-AFTER sentinel is then spent exactly as
``cm/release.sh`` does at the same point: the project's own
``HALT-AFTER`` wins over the kanban root's, and the winning sentinel goes
through :func:`pgai_agent_kanban.halt_after.rc_gate.evaluate` -- the
evaluator the wake loop uses -- so a ``rc:<version>`` the ship satisfied
becomes a HALT now rather than waiting for a wake that a follow-on RC could
pre-empt. Its outcome is narrated; nothing it does or fails to do fails the
ship.

A failing git step prints git's own output, then ``ERROR: git operation
failed at step: <step>`` and stops with exit 1; nothing after it runs.

Release hooks
-------------
The three phases (pre-squash, pre-tag, post-tag) are resolved by the shared
release-hook library, ``team/scripts/lib/cm_release_hooks.sh``
(``cm_resolve_and_enforce_hook``), the one implementation ``cm/release.sh``
also uses; it prints the one resolution line per phase and enforces
``cm_release_<phase>_hook_required``. A resolved hook runs in the repository
with ``PGAI_TARGET_VERSION``, ``PGAI_PROJECT_NAME``, ``PGAI_PROJECT_ROOT``,
``PGAI_DEV_TREE_PATH``, ``PGAI_RC_BRANCH`` and ``PGAI_KANBAN_ROOT`` set, its
stdout and stderr merged and each line prefixed ``[hook <name>]``. A HALT
(a required hook that is absent, a pre-squash or pre-tag hook that is not
executable, a failing pre-squash hook) prints the dual-channel block between
``[ship-rc] HALT`` and ``HALT: ship-rc.sh stopped.`` and records it in the
project's refusal ledger.

Push decision and the remote boundary
-------------------------------------
Whether the ship may touch the remote is decided by the shared push-decision
resolver (:func:`pgai_agent_kanban.lib.push_decision.resolve_push_decision`,
through :func:`pgai_agent_kanban.cm.cancel.push_decision`), the one rule
every push site applies: the project must be registered in ``projects.cfg``,
its own ``project.cfg`` (or legacy ``PROJECT.cfg``) must exist, and that
file must name a ``dev_tree_path``. *repo_root*, ``PGAI_DEV_TREE_PATH``,
``kanban.cfg [paths] dev_tree_path`` and the directory above ``scripts/``
never make a project pushable, so ship-rc never pushes from a repository
taken from any of them.

- ``push``: a resolved project whose ``push_to_remote`` is absent, blank,
  ``true`` or anything but ``false`` -- the default is unchanged. Every
  remote step runs.
- ``no-push``: a resolved project with ``push_to_remote = false``; prints
  ``[ship-rc] Push policy: push_to_remote=false ...`` after the header.
- ``unresolved``: prints one line in the same place, ``[ship-rc]
  skipped-unresolved-project: project '<name>': missing <piece>: <detail>;
  nothing pushed and no remote contacted`` (``<piece>`` is
  ``registration``, ``project-cfg`` or ``dev-tree-path``), then ships
  exactly as for ``no-push``.

A project that does not push skips every remote step: the remote branch
check, the fetch, the fast-forward from the remote, the push of main and
tags and the remote branch delete. Each skipped step prints
``[push_to_remote=false] skipping <remote> <kind> for <project>: <command>``,
so the output shows that nothing reached a remote.

Output
------
Narration goes to the *out* callback and diagnostics to *err* (defaults:
this process's stdout and stderr), each receiving text exactly as the CLI
prints it. A refusal or a failed step is raised as :class:`ShipRcError`,
whose ``text`` is what is still to be printed to stderr and whose
``exit_code`` the CLI exits with (always 1).

CLI (``python3 -m pgai_agent_kanban.cm ship_rc ...``; ``cm/__main__.py``
routes :data:`CLI_VERBS` here)::

    ship_rc --kanban-root=ROOT --project=NAME --key=vX.Y.Z
            [--dry-run] [--repo-root=PATH]
"""

from __future__ import annotations

import argparse
import dataclasses
import errno
import os
import pathlib
import re
import subprocess
import sys
from typing import Callable, Optional

from pgai_agent_kanban.cm import cancel as _cancel
from pgai_agent_kanban.cm import unwind as _unwind
from pgai_agent_kanban.cm import write_rc_state
from pgai_agent_kanban.halt_after import parse_token as _parse_halt_after_token
from pgai_agent_kanban.halt_after import rc_gate as _halt_after_gate
from pgai_agent_kanban.halt_after.token import describe_token as _describe_halt_after_token
from pgai_agent_kanban.lib import push_decision as _push_decision
from pgai_agent_kanban.lib import refusal_ledger
from pgai_agent_kanban.lib import semver as _semver
from pgai_agent_kanban.lib.branch_prefix import release_tag_name
from pgai_agent_kanban.lib.error_contract import format_error_contract
from pgai_agent_kanban.ops import resolve as ops_resolve
from pgai_agent_kanban.ops.context import OpsContext
from pgai_agent_kanban.ops.errors import Ambiguous, CliRefusal, NotFound, OpsError

__all__ = [
    "CLI_VERBS",
    "ShipOutcome",
    "ShipRcError",
    "ship_exit_code",
    "ship_rc",
]

#: The CLI verbs this module serves (``cm/__main__.py`` routes them here).
CLI_VERBS: tuple[str, ...] = ("ship_rc",)

EXIT_REFUSED = 1

_SCRIPT_NAME = "ship-rc.sh"
_VERSION_RE = re.compile(r"^v[0-9]+\.[0-9]+\.[0-9]+$")
# porcelain XY codes of an unmerged path.
_UNMERGED_RE = re.compile(r"^(UU|UD|DU|AA|DD|AU|UA|DA|AD)")
_RC_FIELDS = ("Active RC", "RC Opened At", "RC Opened By Task")

# This tree's own root (the directory holding pgai_agent_kanban/ and
# scripts/): the release-hook library lives under its scripts/lib/.
_TREE_ROOT = pathlib.Path(__file__).resolve().parents[2]
_HOOK_LIB_DIR = _TREE_ROOT / "scripts" / "lib"

# Resolves one hook phase through the shared release-hook library and hands
# the answer back on the file descriptor named by $2: "path\0<path>\0", or
# "halt\0<error>\0<message>\0<remedy>\0" when the library halts the release.
# Exit 72 means the library refused a resolved hook that is not executable
# (its remedy block is already on stderr); exit 70 means it could not be
# loaded.
_HOOK_RESOLVER = (
    'lib_dir="$1"; result_fd="$2"; shift 2\n'
    'source "$lib_dir/project_paths.sh" || exit 70\n'
    'source "$lib_dir/cm_release_hooks.sh" || exit 70\n'
    'set -uo pipefail\n'
    'cm_halt() { printf "halt\\0%s\\0%s\\0%s\\0" "$1" "$2" "$3" >&"$result_fd"; exit 71; }\n'
    'CM_RESOLVED_HOOK_PATH=""\n'
    'cm_resolve_and_enforce_hook "$@" || exit 72\n'
    'printf "path\\0%s\\0" "$CM_RESOLVED_HOOK_PATH" >&"$result_fd"\n'
)
_HOOK_LIB_UNAVAILABLE = 70
_HOOK_LIB_HALTED = 71
_HOOK_NOT_EXECUTABLE = 72

Writer = Callable[[str], None]


@dataclasses.dataclass(frozen=True)
class ShipOutcome:
    """What one :func:`ship_rc` call did.

    Attributes:
        project:     The resolved project name.
        version:     The released version, ``vX.Y.Z``.
        rc_branch:   The (prefixed) RC branch name.
        main_branch: The (prefixed) main branch name.
        release_tag: The (prefixed) tag.
        outcome:     ``planned`` for a dry run, ``shipped`` otherwise.
        pushed:      True when the remote received main, the tag and the RC
                     branch delete (the push decision answered ``push``).
    """

    project: str
    version: str
    rc_branch: str
    main_branch: str
    release_tag: str
    outcome: str
    pushed: bool = False


class ShipRcError(CliRefusal):
    """The ship stopped; nothing after the stopping point was changed.

    Attributes:
        error:     A stable machine key (``ship_rc.<reason>``, the resolver's
                   ``project_resolve.<reason>``, or a HALT's own key).
        exit_code: The code ``ship-rc.sh`` exits with (1).
        text:      What remains to be printed to stderr (plain ``ERROR:``
                   lines, a dual-channel block, or ``""`` when nothing is
                   left to print).
    """


def ship_exit_code(exc: OpsError) -> int:
    """Return the exit code ``ship-rc.sh`` exits with for *exc* (1 when it names none)."""
    return getattr(exc, "exit_code", EXIT_REFUSED)


def _write_stdout(text: str) -> None:
    sys.stdout.write(text)
    sys.stdout.flush()


def _write_stderr(text: str) -> None:
    sys.stderr.write(text)
    sys.stderr.flush()


# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------


def _resolve_project(ctx: OpsContext, typed: str) -> str:
    """The registered project *typed* names (``pp_resolve_project_name``)."""
    try:
        return ops_resolve.resolve_project_name(ctx, typed)
    except OpsError as exc:
        key = "ship_rc.refused"
        if isinstance(exc, (NotFound, Ambiguous)):
            key = "project_resolve.ambiguous" if isinstance(exc, Ambiguous) else "project_resolve.not_found"
        # BLOCK-WORTHY (docs/blocking-site-census.md): shipping cannot pick
        # which project's RC to ship without one registered project; the
        # resolver's own dual-channel block is printed, then the closing lines.
        # CENSUS-ANCHOR: ship-rc-project-required-01
        raise ShipRcError(
            key,
            f"{exc}\n\nERROR: project context is required.\n"
            "  Pass --project <name>, or set PGAI_PROJECT_NAME=<name> in the environment.\n",
        ) from exc


def _repo_root(ctx: OpsContext, project: str) -> str:
    """The repository the ship's local steps run in.

    ``project.cfg`` (or a legacy ``PROJECT.cfg``) ``dev_tree_path``, else
    ``PGAI_DEV_TREE_PATH`` (environment, then ``kanban.cfg [paths]``), else
    the directory above this tree's ``scripts/`` directory. Only the first
    can serve a push: a project whose config names no ``dev_tree_path`` is
    ``unresolved`` to the push decision and contacts no remote.
    """
    cfg_file = _cancel._project_cfg_file(ctx.project_root(project))
    if cfg_file is not None:
        value = _cancel._read_cfg_key(cfg_file, "project", "dev_tree_path")
        if value:
            return value
    return _unwind._dev_tree_fallback(ctx) or str(_TREE_ROOT.parent)


def _branch_prefix(ctx: OpsContext, project: str) -> str:
    """``project.cfg [project] branch_prefix``, refused when it cannot name a branch."""
    try:
        return _cancel.branch_prefix(ctx, project)
    except _cancel.CancelRcError as exc:
        # BLOCK-WORTHY (docs/blocking-site-census.md): a prefix that cannot
        # form a valid branch name would point every git step at the wrong
        # branch and tag.
        # CENSUS-ANCHOR: ship-rc-branch-prefix-01
        raise ShipRcError("ship_rc.refused", exc.text) from exc


def _git_remote_name(ctx: OpsContext, project: str) -> str:
    """``project.cfg [project] git_remote_name`` (``origin`` when absent or blank), refused when invalid."""
    try:
        return _cancel.git_remote_name(ctx, project)
    except _cancel.CancelRcError as exc:
        # BLOCK-WORTHY (docs/blocking-site-census.md): a value that cannot
        # name a remote would point the fetch, the push of main and the tag
        # and the RC-branch delete at a destination nobody configured.
        # CENSUS-ANCHOR: ship-rc-git-remote-name-01
        raise ShipRcError(exc.error, exc.text) from exc


def _field_as_written(path: pathlib.Path, heading: str) -> str:
    """A release-state field's value, or ``""`` when the file has no such heading."""
    return _unwind._field_as_written(path, heading)


# ---------------------------------------------------------------------------
# git
# ---------------------------------------------------------------------------


class _Git:
    """The git steps of a ship, with output passed through to *out* / *err*."""

    def __init__(self, repo: str, out: Writer, err: Writer) -> None:
        self.repo = repo
        self.out = out
        self.err = err

    def quiet(self, *args: str) -> subprocess.CompletedProcess:
        """Run a probe whose output is discarded."""
        return _cancel._run_git(self.repo, list(args))

    def shown(self, *args: str) -> int:
        """Run a command whose output is shown; return its exit code."""
        done = _cancel._run_git(self.repo, list(args))
        if done.stdout:
            self.out(done.stdout)
        if done.stderr:
            self.err(done.stderr)
        return done.returncode

    def to_err(self, *args: str) -> int:
        """Run a command whose stdout and stderr both go to *err*; return its exit code."""
        done = _cancel._run_git(self.repo, list(args))
        if done.stdout:
            self.err(done.stdout)
        if done.stderr:
            self.err(done.stderr)
        return done.returncode

    def step(self, description: str, *args: str) -> None:
        """Run a step whose output is shown; a failure stops the ship."""
        if self.shown(*args) != 0:
            # BLOCK-WORTHY (docs/blocking-site-census.md): every named git
            # step stops the release at the first failure rather than run the
            # next step on an unknown repository state.
            # CENSUS-ANCHOR: ship-rc-git-step-failure-01
            raise ShipRcError(
                "ship_rc.git_failed",
                f"\nERROR: git operation failed at step: {description}\n"
                "Halting before any further git operations.\n"
                "Recover manually by inspecting git state and re-running, or revert as needed.\n",
            )


def _resolve_modify_delete_conflicts(git: _Git, say: Callable[..., None], squash_label: str) -> int:
    """Resolve a failed squash whose only conflicts are modify/delete ones.

    UD (the RC deleted the path) takes the deletion; DU (the RC kept it)
    takes the RC's version. Any content (UU) or other unmerged conflict
    resolves nothing.

    Returns:
        0 when every conflict was resolved, 1 when a content conflict was
        found, 2 when git showed no unmerged path or one was left over.
    """
    say("[ship-rc] Squash produced conflicts; inspecting git status...")
    status = _cancel._run_git(git.repo, ["status", "--porcelain"])
    porcelain = (status.stdout + status.stderr).rstrip("\n")
    has_content = False
    resolutions: list[tuple[str, str]] = []
    for line in porcelain.split("\n"):
        if not line:
            continue
        code, path = line[:2], line[3:]
        if code == "UU":
            has_content = True
            git.err(f"[ship-rc] Content conflict (UU) detected: {path} — cannot auto-resolve.\n")
        elif code == "UD":
            resolutions.append((path, "delete"))
        elif code == "DU":
            resolutions.append((path, "keep"))
        elif code in ("AA", "DD", "AU", "UA", "DA", "AD"):
            has_content = True
            git.err(f"[ship-rc] Unexpected conflict type '{code}' for path: {path} — cannot auto-resolve.\n")

    if not resolutions and not has_content:
        git.err("[ship-rc] WARNING: squash returned non-zero but git status shows no U entries.\n")
        return 2
    if has_content:
        git.err("[ship-rc] UU content conflict(s) detected; cannot auto-resolve.\n")
        git.err("[ship-rc] UD/DU paths (if any) will NOT be auto-resolved into a partial commit.\n")
        return 1

    say("[ship-rc] All conflicts are modify/delete (UD/DU); auto-resolving by taking main's side...")
    for path, action in resolutions:
        if action == "delete":
            git.quiet("rm", "--force", "--", path)
            say(f"cm-release: auto-resolved modify/delete: {path} (took main's deletion)")
        else:
            git.shown("checkout", "--theirs", "--", path)
            git.shown("add", "--", path)
            say(f"cm-release: auto-resolved modify/delete: {path} (took main's version)")

    remaining = [
        line for line in _cancel._run_git(git.repo, ["status", "--porcelain"]).stdout.splitlines()
        if _UNMERGED_RE.match(line)
    ]
    if remaining:
        git.err("[ship-rc] ERROR: U entries remain after auto-resolve attempt:\n")
        git.err("\n".join(remaining) + "\n")
        return 2
    say(f"[ship-rc] Auto-resolve complete: {len(resolutions)} modify/delete path(s) resolved for squash: {squash_label}")
    return 0


# ---------------------------------------------------------------------------
# Release hooks
# ---------------------------------------------------------------------------


@dataclasses.dataclass(frozen=True)
class _Release:
    """Everything a hook phase needs to know about the release in progress."""

    ctx: OpsContext
    project: str
    version: str
    repo: str
    rc_branch: str
    project_root: pathlib.Path


def _halt(release: _Release, error: str, message: str, remedy: str) -> ShipRcError:
    """The HALT stop: the dual-channel block between the HALT banners, recorded in the ledger."""
    refusal_ledger.record_refusal(
        error, message, remedy,
        project=release.project, kanban_root=str(release.ctx.kanban_root), surface="ship.py:ship_rc",
    )
    # BLOCK-WORTHY (docs/blocking-site-census.md): the one HALT stop every
    # named ship-rc halt routes through (a required hook absent, a hook that
    # is not executable, a failing pre-squash hook); the error key varies
    # per caller, so this site is named by its anchor token.
    # CENSUS-ANCHOR: ship-rc-halt-01
    return ShipRcError(
        error,
        "\n[ship-rc] HALT\n"
        + format_error_contract(error, message, remedy)
        + "\n\nHALT: ship-rc.sh stopped. The release has NOT been shipped.\n",
    )


def _resolve_hook(release: _Release, phase: str, say_raw: Writer, warn_raw: Writer) -> tuple[Optional[str], bool]:
    """Resolve *phase*'s hook through the shared release-hook library.

    The library's resolution line goes to *say_raw* and any remedy block to
    *warn_raw*.

    Returns:
        ``(path, executable_ok)``: the resolved hook path (None when none is
        configured) and False when the library refused a resolved hook that
        is not executable.

    Raises:
        ShipRcError: the library halted the release (a required hook is
            absent), or the library could not be loaded.
    """
    env = dict(os.environ)
    env["KANBAN_ROOT"] = str(release.ctx.kanban_root)
    env["PGAI_AGENT_KANBAN_ROOT_PATH"] = str(release.ctx.kanban_root)
    read_fd, write_fd = os.pipe()
    try:
        try:
            done = subprocess.run(
                [
                    "bash", "-c", _HOOK_RESOLVER, "cm-release-hook-resolver",
                    str(_HOOK_LIB_DIR), str(write_fd),
                    release.project, phase, str(release.project_root / "hooks"), release.repo,
                ],
                capture_output=True, text=True, errors="replace", check=False,
                stdin=subprocess.DEVNULL, env=env, pass_fds=(write_fd,),
            )
        finally:
            os.close(write_fd)
        chunks = []
        while True:
            chunk = os.read(read_fd, 65536)
            if not chunk:
                break
            chunks.append(chunk)
    finally:
        os.close(read_fd)
    if done.stdout:
        say_raw(done.stdout)
    if done.stderr:
        warn_raw(done.stderr)
    fields = b"".join(chunks).decode("utf-8", errors="replace").split("\0")

    if done.returncode == _HOOK_LIB_HALTED and fields[0] == "halt" and len(fields) >= 4:
        raise _halt(release, fields[1], fields[2], fields[3])
    if done.returncode == _HOOK_NOT_EXECUTABLE:
        return None, False
    if done.returncode != 0 or fields[0] != "path":
        # BLOCK-WORTHY (docs/blocking-site-census.md): without the shared
        # release-hook library no phase's hook can be resolved or its
        # required flag enforced, so the release cannot proceed safely.
        # CENSUS-ANCHOR: ship-rc-hook-library-unavailable-01
        raise ShipRcError(
            "ship_rc.hook_library_unavailable",
            format_error_contract(
                "ship_rc.hook_library_unavailable",
                f"The release-hook library under {_HOOK_LIB_DIR} could not resolve the {phase} hook "
                f"(exit {done.returncode}), so the release cannot run or enforce its hooks.",
                f"ls -l {_HOOK_LIB_DIR}/cm_release_hooks.sh {_HOOK_LIB_DIR}/project_paths.sh",
            ) + "\n",
        )
    return (fields[1] or None), True


def _run_hook(release: _Release, name: str, path: str, say_raw: Writer, warn_raw: Writer) -> int:
    """Run one resolved hook in the repository; return its exit code (0 when skipped).

    A path that is not a file is skipped with a note; one that is not
    executable is skipped with a warning. The hook's stdout and stderr are
    merged and each line prefixed ``[hook <name>] ``.
    """
    say = lambda line: say_raw(line + "\n")  # noqa: E731 -- local one-liner
    if not os.path.isfile(path):
        say(f"[ship-rc] hook {name}: not present, skipping")
        return 0
    if not os.access(path, os.X_OK):
        warn_raw(f"[ship-rc] WARNING: hook {name} exists but is not executable, skipping\n")
        return 0
    say(f"[ship-rc] running hook {name}...")
    env = dict(os.environ)
    env.update({
        "PGAI_TARGET_VERSION": release.version,
        "PGAI_PROJECT_NAME": release.project,
        "PGAI_PROJECT_ROOT": str(release.project_root),
        "PGAI_DEV_TREE_PATH": release.repo,
        "PGAI_RC_BRANCH": release.rc_branch,
        "PGAI_KANBAN_ROOT": str(release.ctx.kanban_root),
    })
    output, code = "", 0
    # A hook with no interpreter line runs as a bash script, as bash itself
    # runs an executable it cannot exec.
    for argv in ([path], ["bash", path]):
        try:
            done = subprocess.run(
                argv, cwd=release.repo, env=env, stdin=subprocess.DEVNULL,
                stdout=subprocess.PIPE, stderr=subprocess.STDOUT, check=False,
            )
        except OSError as exc:
            output, code = f"{path}: {exc.strerror}\n", 126
            if exc.errno == errno.ENOEXEC:
                continue
            break
        output, code = done.stdout.decode("utf-8", errors="replace"), done.returncode
        break
    if output:
        say_raw("".join(f"[hook {name}] {line}" for line in output.splitlines(keepends=True)))
    return code


def _hook_phase(
    release: _Release, phase: str, say_raw: Writer, warn_raw: Writer,
) -> None:
    """Resolve and run *phase*'s hook with the stop rules of that phase.

    pre-squash: a hook that is not executable, or fails, is a HALT.
    pre-tag:    a hook that is not executable is a HALT; one that fails
                stops the ship.
    post-tag:   a hook that is not executable, or fails, is a warning.
    """
    name = f"cm-release-{phase}"
    path, executable = _resolve_hook(release, phase, say_raw, warn_raw)
    if not executable:
        if phase == "post-tag":
            warn_raw(
                "[ship-rc] WARNING: post-tag in-repo hook exists but is not executable — "
                "skipping (post-tag does not block release)\n"
            )
            return
        what = "the squash cannot proceed safely" if phase == "pre-squash" else "the tag phase cannot run it"
        raise _halt(
            release,
            f"ship-rc.{phase}-hook-not-executable",
            f"The {name} in-repo hook exists but is not executable, so {what}.",
            "chmod +x the in-repo hook path printed above by cm_release_hooks.sh, commit the mode change, "
            "then re-run ship-rc.sh.",
        )
    if not path:
        return
    code = _run_hook(release, name, path, say_raw, warn_raw)
    if code == 0:
        if os.path.isfile(path) and os.access(path, os.X_OK):
            say_raw(f"[ship-rc] hook {name}: completed successfully\n")
        return
    if phase == "post-tag":
        warn_raw(f"[ship-rc] WARNING: hook {name} failed (rc={code}), continuing per --no-block\n")
        return
    warn_raw(
        f"[ship-rc] ERROR: hook {name} failed (rc={code}), blocking release\n"
        f"[ship-rc] ERROR: see [hook {name}] lines above for the hook's output\n"
    )
    if phase == "pre-squash":
        raise _halt(
            release,
            "ship-rc.pre-squash-hook-failed",
            f"The {name} hook exited non-zero (rc=1) for {release.version} — the squash cannot proceed safely.",
            f"Inspect the [hook {name}]-prefixed lines printed above for the hook's own diagnostic output, "
            "fix the hook, then re-run ship-rc.sh.",
        )
    # BLOCK-WORTHY (docs/blocking-site-census.md): a blocking release hook
    # failing mid-ship is a real stop -- the hook's name varies per phase,
    # so this site is named by its anchor token.
    # CENSUS-ANCHOR: ship-rc-hook-failure-01
    raise ShipRcError("ship_rc.hook_failed", "")


# ---------------------------------------------------------------------------
# The verb
# ---------------------------------------------------------------------------


def ship_rc(
    ctx: OpsContext,
    project: str,
    version: str,
    *,
    dry_run: bool = False,
    repo_root: Optional[str] = None,
    out: Optional[Writer] = None,
    err: Optional[Writer] = None,
) -> ShipOutcome:
    """Ship *project*'s RC *version* (see the module docstring).

    Args:
        ctx:       Kanban root.
        project:   Project name as typed: exact, or an unambiguous prefix of
                   a registered name.
        version:   The release version, ``vX.Y.Z``.
        dry_run:   Print the plan and stop; no git command runs and nothing
                   is changed.
        repo_root: The repository to act on, instead of the configured dev
                   tree (the script's ``REPO_ROOT``).
        out, err:  Receive stdout / stderr text as it is produced (default:
                   this process's streams).

    Returns:
        The :class:`ShipOutcome`.

    Raises:
        ShipRcError: a check refused, a hook halted or failed, or a git step
            failed. Nothing after the stopping point was changed.
    """
    say_raw = out or _write_stdout
    warn_raw = err or _write_stderr

    def say(line: str = "") -> None:
        say_raw(line + "\n")

    if not _VERSION_RE.match(version):
        # BLOCK-WORTHY (docs/blocking-site-census.md): a malformed version
        # cannot name a real tag or branch -- refused rather than guessing
        # what the operator meant.
        # CENSUS-ANCHOR: ship-rc-version-format-invalid-01
        raise ShipRcError(
            "ship_rc.refused",
            f"ERROR: invalid version format: '{version}'\nExpected format: vX.Y.Z (e.g. v0.5.0)\n",
        )

    name = _resolve_project(ctx, project)
    repo = repo_root or _repo_root(ctx, name)
    prefix = _branch_prefix(ctx, name)
    rc_branch = f"{prefix}rc/{version}"
    main_branch = f"{prefix}main"
    release_tag = release_tag_name(version, prefix)

    if dry_run:
        say("ship-rc dry-run: no git operations will be performed.")
        say(f"  Project:   {name}")
        say(f"  Version:   {version}")
        say(f"  RC branch: {rc_branch}")
        say(f"  Main:      {main_branch}")
        say(f"  Tag:       {release_tag}")
        say("  (dry-run: exiting before any git step)")
        return ShipOutcome(name, version, rc_branch, main_branch, release_tag, "planned")

    remote = _git_remote_name(ctx, name)
    decision = _cancel.push_decision(ctx, name)
    push = decision.may_push
    release = _Release(ctx, name, version, repo, rc_branch, ctx.project_root(name))
    git = _Git(repo, say_raw, warn_raw)

    def skip_remote(kind: str, command: str) -> None:
        say(f"[push_to_remote=false] skipping {remote} {kind} for {name}: {command}")

    say(f"Shipping release: {version} (project: {name})")
    say(f"  RC branch:    {rc_branch}")
    say(f"  Main:         {main_branch}")
    say(f"  Tag:          {release_tag}")
    say()
    if decision.decision == _push_decision.UNRESOLVED:
        say(f"[ship-rc] {decision.status_line}")
    elif not push:
        say(f"[ship-rc] Push policy: push_to_remote=false — skipping {remote} operations. Local ship only.")

    if git.quiet("rev-parse", "--git-dir").returncode != 0:
        # BLOCK-WORTHY (docs/blocking-site-census.md): release mechanics
        # need a real git repository to ship from.
        # CENSUS-ANCHOR: ship-rc-not-a-repo-01
        raise ShipRcError("ship_rc.refused", f"ERROR: not in a git repository at {repo}\n")
    if git.quiet("diff-index", "--quiet", "HEAD", "--").returncode != 0:
        warn_raw("ERROR: working tree is dirty. Commit or stash changes first.\n")
        git.to_err("status", "--short")
        # BLOCK-WORTHY (docs/blocking-site-census.md): shipping over
        # uncommitted local changes risks folding unreviewed work into the
        # release.
        # CENSUS-ANCHOR: ship-rc-dirty-worktree-01
        raise ShipRcError("ship_rc.refused", "")
    if git.quiet("rev-parse", "--verify", f"refs/tags/{release_tag}").returncode == 0:
        # BLOCK-WORTHY (docs/blocking-site-census.md): re-creating an
        # existing tag would fail or silently move it -- refused rather than
        # risk re-tagging a shipped version.
        # CENSUS-ANCHOR: ship-rc-tag-exists-01
        raise ShipRcError("ship_rc.refused", f"ERROR: tag '{release_tag}' already exists locally\n")

    if push:
        say(f"[Step 1] Verifying {rc_branch} exists locally and on {remote}...")
    else:
        say(f"[Step 1] Verifying {rc_branch} exists locally...")
    if git.quiet("rev-parse", "--verify", f"refs/heads/{rc_branch}").returncode != 0:
        # BLOCK-WORTHY (docs/blocking-site-census.md): there is nothing to
        # ship without the RC branch.
        # CENSUS-ANCHOR: ship-rc-branch-missing-local-01
        raise ShipRcError("ship_rc.refused", f"ERROR: branch '{rc_branch}' does not exist locally\n")
    if push:
        if git.quiet("ls-remote", "--exit-code", "--heads", remote, rc_branch).returncode != 0:
            # BLOCK-WORTHY (docs/blocking-site-census.md): shipping an RC
            # branch the remote has never seen risks publishing work no one
            # else can verify landed correctly.
            # CENSUS-ANCHOR: ship-rc-branch-missing-origin-01
            raise ShipRcError("ship_rc.refused", f"ERROR: branch '{rc_branch}' does not exist on {remote}\n")
        say(f"  Confirmed: {rc_branch} exists locally and on {remote}.")
    else:
        skip_remote("check", f"git ls-remote --exit-code --heads {remote} {rc_branch}")
        say(f"  Confirmed: {rc_branch} exists locally.")

    say(f"[Step 2] Fetching {remote} and tags...")
    if push:
        git.step(f"git fetch {remote} --tags", "fetch", remote, "--tags")
    else:
        skip_remote("fetch", f"git fetch {remote} --tags")

    say(f"[Step 3] Checking out {main_branch} and pulling...")
    git.step(f"git checkout {main_branch}", "checkout", main_branch)
    if push:
        git.step(f"git pull --ff-only {main_branch}", "merge", "--ff-only", f"{remote}/{main_branch}")
    else:
        skip_remote("pull", f"git merge --ff-only {remote}/{main_branch}")

    say("[Step 3b] Running pre-squash hook (if present)...")
    _hook_phase(release, "pre-squash", say_raw, warn_raw)

    say(f"[Step 4] Squash-merging {rc_branch} into {main_branch}...")
    if git.shown("merge", "--squash", rc_branch) != 0:
        if _resolve_modify_delete_conflicts(git, say, f"{rc_branch} into {main_branch}") != 0:
            # BLOCK-WORTHY (docs/blocking-site-census.md): a squash conflict
            # the modify/delete resolver could not clear is stopped rather
            # than resolved further.
            # CENSUS-ANCHOR: ship-rc-squash-conflict-01
            raise ShipRcError(
                "ship_rc.squash_conflict",
                f"\nERROR: git operation failed at step: git merge --squash {rc_branch}\n"
                "Halting before any further git operations.\n"
                "Recover manually by inspecting git state and re-running, or revert as needed.\n",
            )

    say(f"[Step 5] Committing squash on {main_branch}...")
    git.step(f"git commit ({main_branch} squash)", "commit", "-m", f"Release {version}")

    say(f"[Step 5a] Fidelity gate: verifying {rc_branch} tree matches {main_branch} after squash...")
    if git.quiet("diff", "--quiet", rc_branch, main_branch).returncode == 0:
        say(f"[Step 5a] Fidelity gate: OK — {rc_branch} and {main_branch} are identical.")
    else:
        warn_raw(f"\n[Step 5a] Fidelity gate: DIVERGENCE DETECTED — {rc_branch} and {main_branch} differ:\n")
        git.to_err("diff", "--stat", rc_branch, main_branch)
        # BLOCK-WORTHY (docs/blocking-site-census.md): shipping while main
        # and the RC branch diverge would publish a release that silently
        # drops or reorders the mid-RC main commits.
        # CENSUS-ANCHOR: ship-rc-fidelity-gate-divergence-01
        raise ShipRcError(
            "ship_rc.fidelity_divergence",
            f"\nERROR: Post-squash fidelity gate failed for {version}: {main_branch} contains commits\n"
            f"not present in {rc_branch}. A commit landed on main mid-RC.\n"
            "Operator must inspect the divergent paths shown above and resolve before re-running.\n",
        )

    say("[Step 5b] Running pre-tag hook (if present)...")
    _hook_phase(release, "pre-tag", say_raw, warn_raw)

    say(f"[Step 6] Tagging {release_tag} on {main_branch}...")
    git.step(f"git tag {release_tag}", "tag", release_tag)

    say("[Step 6b] Running post-tag hook (if present)...")
    _hook_phase(release, "post-tag", say_raw, warn_raw)

    # The release tag goes by its own explicit ref, never every local tag:
    # in a repository shared with another release lane, that lane's
    # unpublished tags stay local.
    tag_ref = f"refs/tags/{release_tag}"
    push_command = f"git push {remote} {main_branch} {tag_ref}"
    say(f"[Step 7] Pushing {main_branch} and {tag_ref} to {remote}...")
    if push:
        git.step(push_command, "push", remote, main_branch, tag_ref)
    else:
        skip_remote("push", push_command)

    say(f"[Step 8] Deleting {rc_branch} on {remote}...")
    if push:
        git.step(f"git push {remote} --delete {rc_branch}", "push", remote, "--delete", rc_branch)
    else:
        skip_remote("push", f"git push {remote} --delete {rc_branch}")

    say(f"[Step 9] Deleting local branch {rc_branch}...")
    git.step(f"git branch -D {rc_branch}", "branch", "-D", rc_branch)

    say("[Post-ship] Reconciling release-state.md...")
    release_state = ctx.release_state_path(name)
    _reset_release_state(release_state, version, say, warn_raw)
    _spend_halt_after(ctx.project_root(name), ctx.kanban_root, release_state.parent, say, warn_raw)
    say("  NOTE: The CM release task (if one exists) is not auto-closed by this script.")
    say(f"  If a CM task for {version} remains open, mark it DONE manually.")
    say()

    say()
    say("Release complete.")
    say(f"  Version:    {version}")
    say(f"  Tagged as:  {release_tag}  (on {main_branch})")
    if push:
        say(f"  RC deleted: {rc_branch} (local + {remote})")
    else:
        say(f"  RC deleted: {rc_branch} (local; {remote} not touched)")
    say()
    say("Summary of what shipped:")
    say(f"  - {rc_branch} squashed directly to {main_branch} (single-lane squash commit)")
    say(f"  - Post-squash fidelity gate passed: {rc_branch} and {main_branch} trees are identical")
    if push:
        say(f"  - Tag {release_tag} created and pushed")
        say(f"  - {rc_branch} deleted from {remote} and locally")
    else:
        say(f"  - Tag {release_tag} created locally (push_to_remote=false: not pushed)")
        say(f"  - {rc_branch} deleted locally")
    return ShipOutcome(name, version, rc_branch, main_branch, release_tag, "shipped", push)


def _last_released_after_ship(current: str, version: str) -> str:
    """The Last Released value a ship of *version* leaves: never lower than *current*.

    *current* is the field as written (``""`` when absent, ``none`` before
    any release). A value that is not a release version is replaced.
    """
    if _semver.parse_version(current.strip()) is None:
        return version
    if _semver.ge(current.strip(), version):
        return current.strip()
    return version


def _reset_release_state(
    release_state: pathlib.Path, version: str, say: Callable[..., None], warn_raw: Writer
) -> None:
    """Close the RC in release-state.md: RC fields to none, Last Released raised to *version*.

    Only the RC headings the file carries are reset; Last Released is set
    (or added) only when it is lower than *version*, compared as semver, so
    it never moves backwards. Every other line is kept. A missing file, or
    one that cannot be rewritten, is a warning: the release has already
    shipped.
    """
    if not release_state.is_file():
        warn_raw(f"  WARNING: release-state.md not found: {release_state}\n  Skipping reconciliation.\n")
        return
    was = {heading: _field_as_written(release_state, heading) for heading in _RC_FIELDS}
    last_was = _field_as_written(release_state, "Last Released")
    last_now = _last_released_after_ship(last_was, version)
    rc_closed = all(value == "none" for value in was.values())
    if rc_closed and last_now == last_was:
        say("  release-state.md: already reset — nothing to do.")
        return
    updates = {heading: "none" for heading, value in was.items() if value}
    if last_now != last_was:
        updates["Last Released"] = last_now
    try:
        if updates:
            write_rc_state.update_release_state_fields(str(release_state), updates)
    except (OSError, ValueError) as exc:
        warn_raw(
            f"  WARNING: release-state.md reconciliation failed: {exc}\n"
            "  Verify and manually reset Active RC / RC Opened At / RC Opened By Task if needed,\n"
            f"  and set Last Released to {version} if it is lower.\n"
        )
        return
    if rc_closed:
        say("  RC fields already none.")
    else:
        say(f"  Active RC         -> none  (was: {was['Active RC']})")
        say(f"  RC Opened At      -> none  (was: {was['RC Opened At']})")
        say(f"  RC Opened By Task -> none  (was: {was['RC Opened By Task']})")
    if last_now != last_was:
        say(f"  Last Released     -> {last_now}  (was: {last_was or 'absent'})")
    else:
        say(f"  Last Released     kept at {last_was} (not lower than {version})")


def _spend_halt_after(
    project_root: pathlib.Path,
    kanban_root: pathlib.Path,
    release_state_root: pathlib.Path,
    say: Callable[..., None],
    warn_raw: Writer,
) -> None:
    """Spend a HALT-AFTER sentinel the RC close just satisfied.

    The project's own ``HALT-AFTER`` wins over the kanban root's (the scope
    rule the wake loop applies). The winning sentinel is evaluated by
    :func:`pgai_agent_kanban.halt_after.rc_gate.evaluate` against
    *release_state_root*'s release-state.md; a satisfied drain promotes it to
    HALT there. Every outcome is narrated and none of them fails the ship:
    the tag is already cut.
    """
    say("[Post-ship] Checking for a HALT-AFTER sentinel to spend at RC close...")
    scope_root = next(
        (root for root in (pathlib.Path(project_root), pathlib.Path(kanban_root)) if (root / "HALT-AFTER").is_file()),
        None,
    )
    if scope_root is None:
        say("  no HALT-AFTER sentinel present at either scope; nothing to spend.")
        return
    try:
        raw = (scope_root / "HALT-AFTER").read_text(encoding="utf-8")
    except OSError as exc:
        warn_raw(f"  WARNING: cannot read {scope_root / 'HALT-AFTER'}: {exc}; the next wake evaluates it.\n")
        return
    event = _parse_halt_after_token(raw)
    if event is None:
        say(f"  HALT-AFTER at {scope_root} names no valid token; left for the wake loop to report.")
        return
    try:
        result = _halt_after_gate.evaluate(scope_root, release_state_root, event)
    except (OSError, ValueError) as exc:
        warn_raw(f"  WARNING: HALT-AFTER evaluation failed: {exc}; the next wake evaluates it.\n")
        return
    described = _describe_halt_after_token(result.event)
    if result.outcome is _halt_after_gate.RcGateOutcome.PROMOTED:
        say(
            f"  HALT-AFTER sentinel spent at RC close: {described} (scope: {scope_root}) -- "
            "HALT-AFTER removed, HALT created."
        )
    elif result.outcome is _halt_after_gate.RcGateOutcome.DEFERRED:
        say(f"  HALT-AFTER {described} is satisfied but an RC is still open; promotion deferred to its close.")
    else:
        say(f"  HALT-AFTER {described} is not yet satisfied; the chain continues.")


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def _cli_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="python3 -m pgai_agent_kanban.cm ship_rc", add_help=False)
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
        help="the release version to ship, vX.Y.Z (required)",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="print the plan and stop; no git command runs and nothing is changed",
    )
    parser.add_argument(
        "--repo-root",
        default="",
        help="repository to act on (default: the project's configured dev tree)",
    )
    return parser


def _cli_main(argv: Optional[list[str]] = None) -> int:
    """Run ``ship_rc`` from the command line (see the module docstring).

    Prints the narration to stdout; a refusal's remaining text goes to
    stderr and the exit code is :func:`ship_exit_code`'s.
    """
    args = list(sys.argv[1:] if argv is None else argv)
    if not args or args[0] not in CLI_VERBS:
        _write_stderr(f"cm: VERB must be one of: {', '.join(CLI_VERBS)}\n")
        return EXIT_REFUSED
    opts = _cli_parser().parse_args(args[1:])
    ctx = OpsContext(kanban_root=pathlib.Path(opts.kanban_root))
    try:
        ship_rc(ctx, opts.project, opts.key, dry_run=opts.dry_run, repo_root=opts.repo_root or None)
    except ShipRcError as exc:
        if exc.text:
            _write_stderr(exc.text)
        return ship_exit_code(exc)
    return 0
