"""
readonly_git.py — the one way OVERWATCH's block triage runs git: read-only
subcommands from a closed allowlist, plus the two worktree-registration
cleanup verbs, nothing else.

Block triage stays inside OVERWATCH's swim lane: it may read a repository
(count the commits a branch holds, list them, diff them) but never creates,
moves or deletes a ref, never touches a worktree's checkout and never
commits. Where a fix needs git, the task is requeued to its owning role.
This module is where that line is drawn in code: :func:`run_readonly_git`
refuses any subcommand outside ``READ_ONLY_SUBCOMMANDS`` (and, for the
subcommands in ``READ_ONLY_FORMS``, any form but the read-only one) before a
process is started, so a caller cannot reach a mutating verb through it by
mistake.

:func:`run_worktree_cleanup` is the single exception, and it is narrow:
``git worktree remove <path>`` (never ``--force``, so git itself refuses a
worktree holding uncommitted or untracked changes, or a locked one) and
``git worktree prune``. Both drop a worktree registration and its checkout
directory; neither deletes, moves or creates a ref or a commit, so every
commit a branch holds survives them. Block triage uses them only on a stale
worktree inside the worktree base whose commits all exist elsewhere.

Every call runs ``git -C <repo> <subcommand> <args...>`` with a timeout,
``GIT_OPTIONAL_LOCKS=0`` (no opportunistic index refresh) and no terminal
prompt. A git that cannot start, times out or names a repository that does
not exist comes back as ``None`` — the caller decides what an unreadable
repository means (block triage fails closed: it escalates).

Usage (import):
    from pgai_agent_kanban.overwatch.readonly_git import run_readonly_git, run_worktree_cleanup
    result = run_readonly_git(repo, "rev-list", "--count", "refs/heads/a..refs/heads/b")
    if result is not None and result.ok:
        count = int(result.stdout.strip())
    run_worktree_cleanup(repo, "remove", "/path/inside/the/worktree/base")
    run_worktree_cleanup(repo, "prune")
"""

from __future__ import annotations

import dataclasses
import os
import subprocess
from pathlib import Path
from typing import Optional

#: Subcommands block triage may run with any arguments. Each only reads
#: refs, objects and the working tree (merge-tree --write-tree also writes
#: loose objects no ref points to; status runs with GIT_OPTIONAL_LOCKS=0, so
#: it never rewrites the index).
READ_ONLY_SUBCOMMANDS = frozenset(
    {
        "rev-parse",
        "rev-list",
        "log",
        "diff",
        "merge-base",
        "merge-tree",
        "show-ref",
        "for-each-ref",
        "status",
        "ls-files",
    }
)

#: Subcommands that also have mutating forms, mapped to the one first
#: argument (the read-only form) block triage may run them with.
READ_ONLY_FORMS: dict = {"worktree": frozenset({"list"})}

#: The worktree-registration cleanup verbs :func:`run_worktree_cleanup` runs.
WORKTREE_CLEANUP_VERBS = frozenset({"remove", "prune"})

#: Seconds any one git call may take before it counts as unreadable.
GIT_TIMEOUT_SECONDS = 60


class ReadOnlyGitRefused(ValueError):
    """A subcommand (or form) outside the allowlists was asked for; no
    process was started."""


@dataclasses.dataclass(frozen=True)
class GitResult:
    """One finished git call."""

    returncode: int
    stdout: str
    stderr: str

    @property
    def ok(self) -> bool:
        """True when git exited 0."""
        return self.returncode == 0


def run_readonly_git(
    repo: "Path | str", subcommand: str, *args: str, exact_bytes: bool = False
) -> Optional[GitResult]:
    """Run ``git -C <repo> <subcommand> <args...>`` when *subcommand* is read-only.

    Args:
        repo:       The repository (or worktree) directory.
        subcommand: One of ``READ_ONLY_SUBCOMMANDS``, or a key of
                    ``READ_ONLY_FORMS`` whose first argument is its
                    read-only form (``worktree list``).
        args:       The subcommand's arguments, passed as given.
        exact_bytes: Decode the output with ``surrogateescape`` instead of
                    replacing undecodable bytes, so
                    ``result.stdout.encode("utf-8", "surrogateescape")``
                    gives git's exact bytes back (a patch that must apply).

    Returns:
        The finished call, or None when *repo* is not a directory, git
        cannot be started, or the call times out.

    Raises:
        ReadOnlyGitRefused: *subcommand* (or its form) is not in the allowlist.
    """
    forms = READ_ONLY_FORMS.get(subcommand)
    if forms is not None:
        first = args[0] if args else ""
        if first not in forms:
            raise ReadOnlyGitRefused(
                f"git {subcommand!r} {first!r} is not the read-only form of {subcommand!r} block triage may "
                f"run (allowed: {', '.join(f'{subcommand} {f}' for f in sorted(forms))})"
            )
    elif subcommand not in READ_ONLY_SUBCOMMANDS:
        raise ReadOnlyGitRefused(
            f"git {subcommand!r} is not a read-only subcommand block triage may run "
            f"(allowed: {', '.join(sorted(READ_ONLY_SUBCOMMANDS))})"
        )
    return _run(repo, subcommand, *args, errors="surrogateescape" if exact_bytes else "replace")


def run_worktree_cleanup(repo: "Path | str", verb: str, *args: str) -> Optional[GitResult]:
    """Run ``git -C <repo> worktree remove <path>`` or ``git -C <repo> worktree prune``.

    Args:
        repo: The repository whose worktree registrations are cleaned.
        verb: ``remove`` (exactly one argument, the worktree path, which
              must not start with ``-``: no ``--force``) or ``prune`` (no
              arguments).

    Returns:
        The finished call, or None when *repo* is not a directory, git
        cannot be started, or the call times out. A non-zero exit (git
        refusing a dirty or locked worktree) is the caller's to read.

    Raises:
        ReadOnlyGitRefused: any other verb, or arguments outside that shape.
    """
    if verb not in WORKTREE_CLEANUP_VERBS:
        raise ReadOnlyGitRefused(
            f"git worktree {verb!r} is not a cleanup verb block triage may run "
            f"(allowed: {', '.join(f'worktree {v}' for v in sorted(WORKTREE_CLEANUP_VERBS))})"
        )
    if verb == "remove" and (len(args) != 1 or not args[0] or args[0].startswith("-")):
        raise ReadOnlyGitRefused(
            f"git worktree remove takes exactly one worktree path and no option (got {list(args)!r}); "
            "--force is never used, so git refuses a worktree holding changes"
        )
    if verb == "prune" and args:
        raise ReadOnlyGitRefused(f"git worktree prune takes no argument here (got {list(args)!r})")
    return _run(repo, "worktree", verb, *args)


def _run(repo: "Path | str", *argv: str, errors: str = "replace") -> Optional[GitResult]:
    """Run ``git -C <repo> <argv...>`` once the caller has checked *argv*."""
    if not Path(repo).is_dir():
        return None
    env = dict(os.environ, GIT_OPTIONAL_LOCKS="0", GIT_TERMINAL_PROMPT="0")
    try:
        done = subprocess.run(
            ["git", "-C", str(repo), *argv],
            capture_output=True,
            text=True,
            errors=errors,
            timeout=GIT_TIMEOUT_SECONDS,
            check=False,
            env=env,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    return GitResult(done.returncode, done.stdout, done.stderr)
