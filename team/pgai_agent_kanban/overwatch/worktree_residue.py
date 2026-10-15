"""
worktree_residue.py — find, judge and clear the stale worktree that makes a
task's worktree create fail with "already exists".

``create_task_worktree`` (``team/scripts/lib/worktree.sh``) removes a
directory left at the task's own worktree path before it creates the
worktree. What it cannot clear is a *registered* worktree that still claims
the task: one at the task's path, or one anywhere holding the task's
feature branch checked out — often a registration whose directory is
already gone. Git then refuses the create ("a branch named ... already
exists", "... is already used by worktree at ..."), and the wake blocks
the task as ``worktree_create_failed``. That diagnostic goes to the cron
log, not to the Block Record, so this module re-derives it from the
repository itself, read-only: ``git worktree list --porcelain``.

Residue
-------
A registered worktree, other than the repository's main one, that is at
``<worktree base>/<task_id>`` or has the task's feature branch checked out.
Block triage clears residue only when all of these hold (the classifier's
preconditions, supplied by :func:`residue_facts`):

``worktree_residue_in_base``
    there is residue, every entry sits strictly inside the worktree base
    (paths canonicalised first), and none is locked;
``residue_holds_no_unique_commits``
    no entry's HEAD reaches a commit that no other branch, tag or
    remote-tracking ref holds. The entry's own checked-out branch does not
    count as "elsewhere": a fresh re-create force-deletes the feature
    branch, so commits only it holds would be lost;
``source_branch_exists``
    the source (RC) branch the task README names exists, so a retried
    create can succeed.

:func:`clear_residue` then, under the caller's lock: re-checks all three,
refuses a live entry holding uncommitted or untracked changes, writes a
``worktree-residue-<UTC>.txt`` evidence file naming every entry, removes
each live entry with ``git worktree remove <path>`` (never ``--force``),
runs ``git worktree prune`` (which drops registrations whose directory is
gone), and confirms the residue is gone. No branch, ref or commit is
deleted: removing a worktree removes its checkout, never the branch it had
checked out. The next dispatch retries the create.

Finished-task worktrees
-----------------------
The stale-worktree sweep check (``check-stale-worktrees.sh``) uses the same
rules for the old worktree of a task that has finished (DONE or WONT-DO):
:func:`finished_worktree_refusal` says, read-only, why one registered
worktree must be kept — a locked or main entry, uncommitted or untracked
changes, or commits no other branch, tag or remote-tracking ref holds — and
:func:`remove_finished_worktree` re-checks that under the caller's lock and
then runs ``git worktree remove <path>`` (never ``--force``) and ``git
worktree prune``. A worktree holding changes is reported, never removed.

Git runs only through :mod:`readonly_git` — its read-only allowlist and its
two worktree-cleanup verbs.

Usage (import):
    from pgai_agent_kanban.overwatch.worktree_residue import (
        residue_facts, clear_residue, ResidueError,
        finished_worktree_refusal, remove_finished_worktree,
    )

Usage (command line, as the sweep check calls it):
    python3 -m pgai_agent_kanban.overwatch.worktree_residue assess-finished --repo DEV_TREE --path WORKTREE
        prints "removable<TAB><entry>" or "keep<TAB><why>"; exits 0.
    python3 -m pgai_agent_kanban.overwatch.worktree_residue remove-finished --repo DEV_TREE --path WORKTREE
        prints "removed<TAB><entry>" and exits 0, or refuses dual-channel
        (error / message / remedy) on stderr, removes nothing, and exits 3.
"""

from __future__ import annotations

import argparse
import dataclasses
import datetime
import pathlib
import re
import shlex
import sys
from pathlib import Path
from typing import Optional

# Run by file path (``python3 .../worktree_residue.py``) or loaded from a
# file outside its package, this module has no parent package and the
# interpreter puts only its own directory on sys.path, so the package imports
# below would not resolve. Put this file's own package root -- the directory
# holding the pgai_agent_kanban/ this file sits in -- at the front of sys.path
# so the imports resolve from the same tree, never another. Imported as a
# package member, sys.path is left untouched.
if not __package__:
    _OWN_PACKAGE_ROOT = str(pathlib.Path(__file__).resolve().parents[2])
    if _OWN_PACKAGE_ROOT not in sys.path:
        sys.path.insert(0, _OWN_PACKAGE_ROOT)

from pgai_agent_kanban.lib.atomic_write import atomic_write  # noqa: E402
from pgai_agent_kanban.lib.error_contract import emit_error_contract  # noqa: E402
from pgai_agent_kanban.lib.timeutil import format_iso  # noqa: E402
from pgai_agent_kanban.overwatch.block_record import BlockRecord  # noqa: E402
from pgai_agent_kanban.overwatch.block_requeue import GitTarget, GitTargetError, resolve_git_target  # noqa: E402
from pgai_agent_kanban.overwatch.readonly_git import run_readonly_git, run_worktree_cleanup  # noqa: E402

_ATOMIC_PREFIX = ".worktree_residue_tmp_"
_GLOB_SPECIAL_RE = re.compile(r"([*?\[\\])")
_NULL_SHA_RE = re.compile(r"^0+$")


class ResidueError(Exception):
    """The residue cannot be cleared safely; the message says why."""


@dataclasses.dataclass(frozen=True)
class WorktreeEntry:
    """One ``git worktree list --porcelain`` record."""

    path: Path
    head: str
    branch: str
    prunable: bool
    locked: bool
    main: bool

    def describe(self) -> str:
        """Return a one-line description of the registered worktree.

        The path, whether its directory is present or gone, its HEAD, its branch
        (``detached`` when none) and ``locked`` when it is locked.
        """
        state = "registered, directory gone" if self.prunable or not self.path.exists() else "registered, directory present"
        return (
            f"{self.path} ({state}; HEAD {self.head or 'unknown'}; "
            f"branch {self.branch or 'detached'}{'; locked' if self.locked else ''})"
        )


def list_worktrees(repo: Path) -> Optional[list[WorktreeEntry]]:
    """Every worktree *repo* registers, the main one first; None when unreadable."""
    result = run_readonly_git(repo, "worktree", "list", "--porcelain")
    if result is None or not result.ok:
        return None
    entries: list[WorktreeEntry] = []
    for block in re.split(r"\n\s*\n", result.stdout.strip()):
        fields: dict = {}
        for line in block.splitlines():
            key, _, value = line.strip().partition(" ")
            fields[key] = value
        if "worktree" not in fields:
            continue
        branch = fields.get("branch", "")
        entries.append(
            WorktreeEntry(
                path=Path(fields["worktree"]),
                head=fields.get("HEAD", ""),
                branch=branch[len("refs/heads/"):] if branch.startswith("refs/heads/") else branch,
                prunable="prunable" in fields,
                locked="locked" in fields,
                main=not entries,
            )
        )
    return entries


def _canonical(path: Path) -> Path:
    return path.resolve(strict=False)


def strictly_inside(path: Path, base: Path) -> bool:
    """Whether *path*, canonicalised, is a strict descendant of the existing
    directory *base*, canonicalised (symlinks resolved, ``..`` collapsed)."""
    try:
        canon_base = base.resolve(strict=True)
    except OSError:
        return False
    if canon_base == Path(canon_base.anchor):
        return False
    canon = _canonical(path)
    return canon != canon_base and canon_base in canon.parents


def task_residue(entries: list[WorktreeEntry], base: Path, task_id: str, branch: str) -> list[WorktreeEntry]:
    """The non-main entries that claim the task: at ``<base>/<task_id>`` or holding *branch*."""
    task_path = _canonical(base / task_id)
    return [
        e for e in entries
        if not e.main and (_canonical(e.path) == task_path or (branch and e.branch == branch))
    ]


def unique_commits(repo: Path, entry: WorktreeEntry) -> Optional[list[str]]:
    """Commits *entry*'s HEAD reaches that no other branch, tag or
    remote-tracking ref holds (its own branch excluded); None when unreadable."""
    if not entry.head or _NULL_SHA_RE.match(entry.head):
        return None
    args = ["rev-list", entry.head, "--not"]
    if entry.branch:
        # --exclude takes a glob; escape its special characters so the
        # branch name matches only itself.
        escaped = _GLOB_SPECIAL_RE.sub(r"\\\1", entry.branch)
        args.append("--exclude=" + escaped)
    args += ["--branches", "--tags", "--remotes"]
    result = run_readonly_git(repo, *args)
    if result is None or not result.ok:
        return None
    return [line.strip() for line in result.stdout.splitlines() if line.strip()]


def holds_changes(path: Path) -> Optional[bool]:
    """Whether the live worktree at *path* holds uncommitted or untracked
    changes (``git status --porcelain`` prints anything); None when git
    cannot read its status."""
    status = run_readonly_git(path, "status", "--porcelain")
    if status is None or not status.ok:
        return None
    return bool(status.stdout.strip())


@dataclasses.dataclass(frozen=True)
class Assessment:
    """What the repository says about a task's worktree residue."""

    target: Optional[GitTarget]
    residue: tuple[WorktreeEntry, ...] = ()
    outside_base: tuple[WorktreeEntry, ...] = ()
    locked: tuple[WorktreeEntry, ...] = ()
    unique: tuple[tuple[WorktreeEntry, tuple[str, ...]], ...] = ()
    unreadable: tuple[WorktreeEntry, ...] = ()
    problem: str = ""

    @property
    def in_base(self) -> bool:
        """True when there is residue and none of it is outside the base directory or locked."""
        return bool(self.residue) and not self.outside_base and not self.locked

    @property
    def no_unique_commits(self) -> bool:
        """True when there is residue and none of it carries unique commits or failed to read."""
        return bool(self.residue) and not self.unique and not self.unreadable

    @property
    def source_exists(self) -> bool:
        """True when the assessment has a git target whose source branch exists."""
        return self.target is not None and self.target.source is not None

    def refusal(self) -> str:
        """Why the residue must not be cleared ("" when it may be)."""
        if self.problem:
            return self.problem
        if self.target is not None and self.target.source is None:
            return f"the source branch {self.target.source_written} does not exist in {self.target.repo}"
        if not self.residue:
            return "no registered worktree claims the task's worktree path or feature branch"
        if self.outside_base:
            return "residue outside the worktree base: " + "; ".join(e.describe() for e in self.outside_base)
        if self.locked:
            return "locked worktree(s): " + "; ".join(e.describe() for e in self.locked)
        if self.unique:
            return "the residue holds commits found nowhere else: " + "; ".join(
                f"{e.describe()} reaches {len(shas)} ({', '.join(s[:12] for s in shas[:5])})" for e, shas in self.unique
            )
        if self.unreadable:
            return "the commits of " + "; ".join(e.describe() for e in self.unreadable) + " could not be read"
        return ""


def assess(task_dir: Path, record: Optional[BlockRecord], worktree_base: Optional[Path]) -> Assessment:
    """Read the repository's worktree registrations for the task. Read-only."""
    if record is None:
        return Assessment(None, problem="the task carries no readable Block Record")
    try:
        target = resolve_git_target(task_dir, record, require_source=False)
    except GitTargetError as exc:
        return Assessment(None, problem=str(exc))
    if worktree_base is None:
        return Assessment(target, problem="the worktree base was not given, so no residue can be shown to be inside it")
    entries = list_worktrees(target.repo)
    if entries is None:
        return Assessment(target, problem=f"git could not list the worktrees of {target.repo}")
    residue = task_residue(entries, worktree_base, task_dir.name, target.branch)
    unique, unreadable = [], []
    for entry in residue:
        shas = unique_commits(target.repo, entry)
        if shas is None:
            unreadable.append(entry)
        elif shas:
            unique.append((entry, tuple(shas)))
    return Assessment(
        target,
        residue=tuple(residue),
        outside_base=tuple(e for e in residue if not strictly_inside(e.path, worktree_base)),
        locked=tuple(e for e in residue if e.locked),
        unique=tuple(unique),
        unreadable=tuple(unreadable),
    )


def residue_facts(task_dir: Path, record: Optional[BlockRecord], worktree_base: Optional[Path]) -> dict:
    """The classifier facts for a ``worktree_create_failed`` block (all False
    when unreadable), plus ``refusal``: why the residue may not be cleared,
    for the diagnosis, when it may not."""
    found = assess(task_dir, record, worktree_base)
    facts: dict = {
        "worktree_residue_in_base": found.in_base and not found.problem,
        "residue_holds_no_unique_commits": found.no_unique_commits and not found.problem,
        "source_branch_exists": found.source_exists,
    }
    refusal = found.refusal()
    if refusal:
        facts["refusal"] = refusal
    return facts


@dataclasses.dataclass(frozen=True)
class Cleared:
    """What :func:`clear_residue` removed."""

    evidence: Path
    removed: tuple[str, ...]
    pruned: tuple[str, ...]


def clear_residue(
    task_dir: Path, evidence_dir: Path, record: BlockRecord, worktree_base: Optional[Path], *, now: datetime.datetime
) -> Cleared:
    """Remove the task's worktree residue (module docstring). Writes the
    evidence file before removing anything.

    Raises:
        ResidueError: a precondition no longer holds, a live entry holds
            changes, or git refused a removal (what was already removed is
            named) or left the residue registered.
    """
    found = assess(task_dir, record, worktree_base)
    refusal = found.refusal()
    if refusal:
        raise ResidueError(refusal)
    target = found.target
    assert target is not None  # a refusal-free assessment always has one
    live = [e for e in found.residue if e.path.exists() and not e.prunable]
    for entry in live:
        changes = holds_changes(entry.path)
        if changes is None:
            raise ResidueError(f"git could not read the status of {entry.describe()}")
        if changes:
            raise ResidueError(f"{entry.describe()} holds uncommitted or untracked changes")

    evidence_dir.mkdir(parents=True, exist_ok=True)
    stamp = now.astimezone(datetime.timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    evidence = evidence_dir / f"worktree-residue-{stamp}.txt"
    n = 2
    while evidence.exists():
        evidence = evidence_dir / f"worktree-residue-{stamp}-{n}.txt"
        n += 1
    lines = [
        "# Worktree residue cleared by block triage",
        "",
        f"recorded_at: {format_iso(now)}",
        f"repo: {target.repo}",
        f"worktree_base: {worktree_base}",
        f"feature_branch: {target.branch}",
        f"source_branch: {target.source}",
        "",
        "Every entry below claimed the task's worktree path or feature branch. Each one's HEAD",
        "reaches no commit that another branch, tag or remote-tracking ref does not also hold.",
        "Live entries are removed with 'git worktree remove' (no --force); 'git worktree prune'",
        "then drops registrations whose directory is gone. No branch or commit is deleted.",
        "",
        *(f"- {e.describe()}" for e in found.residue),
    ]
    atomic_write(evidence, "\n".join(lines) + "\n", prefix=_ATOMIC_PREFIX)

    removed: list[str] = []
    for entry in live:
        result = run_worktree_cleanup(target.repo, "remove", str(entry.path))
        if result is None or not result.ok:
            why = (result.stderr.strip() if result is not None else "") or "git could not run"
            done = f"; already removed: {', '.join(removed)}" if removed else ""
            raise ResidueError(f"git worktree remove refused {entry.path}: {why}{done}")
        removed.append(str(entry.path))
    pruned = run_worktree_cleanup(target.repo, "prune")
    if pruned is None or not pruned.ok:
        raise ResidueError(f"git worktree prune failed in {target.repo}" + (f"; removed: {', '.join(removed)}" if removed else ""))
    after = list_worktrees(target.repo)
    left = task_residue(after or [], worktree_base, task_dir.name, target.branch) if after is not None else list(found.residue)
    if left:
        raise ResidueError("the residue is still registered after the cleanup: " + "; ".join(e.describe() for e in left))
    return Cleared(
        evidence=evidence,
        removed=tuple(removed),
        pruned=tuple(str(e.path) for e in found.residue if str(e.path) not in removed),
    )


# ---------------------------------------------------------------------------
# Finished-task worktrees (the stale-worktree sweep check)
# ---------------------------------------------------------------------------


def worktree_entry_at(entries: list[WorktreeEntry], path: Path) -> Optional[WorktreeEntry]:
    """The registered entry at *path* (both canonicalised), or None."""
    wanted = _canonical(path)
    for entry in entries:
        if _canonical(entry.path) == wanted:
            return entry
    return None


def finished_worktree_refusal(repo: Path, path: Path) -> tuple[Optional[WorktreeEntry], str]:
    """``(entry, why)``: the registered worktree at *path* in *repo*, and why
    it must be kept ("" when it may be removed). Read-only.

    Kept: an unreadable worktree list, no registration at *path*, the
    repository's main worktree, a locked entry, a live entry holding
    uncommitted or untracked changes (or whose status cannot be read), and
    an entry whose HEAD reaches commits no other branch, tag or
    remote-tracking ref holds (or whose commits cannot be read).
    """
    entries = list_worktrees(repo)
    if entries is None:
        return None, f"git could not list the worktrees of {repo}"
    entry = worktree_entry_at(entries, path)
    if entry is None:
        return None, f"no worktree is registered at {path} in {repo}"
    if entry.main:
        return entry, f"{entry.describe()} is the repository's main worktree"
    if entry.locked:
        return entry, f"{entry.describe()} is locked"
    if entry.path.exists() and not entry.prunable:
        changes = holds_changes(entry.path)
        if changes is None:
            return entry, f"git could not read the status of {entry.describe()}"
        if changes:
            return entry, f"{entry.describe()} holds uncommitted or untracked changes"
    shas = unique_commits(repo, entry)
    if shas is None:
        return entry, f"the commits of {entry.describe()} could not be read"
    if shas:
        return entry, (
            f"{entry.describe()} reaches {len(shas)} commit(s) found nowhere else "
            f"({', '.join(sha[:12] for sha in shas[:5])})"
        )
    return entry, ""


def remove_finished_worktree(repo: Path, path: Path) -> str:
    """Remove the finished task's worktree at *path* (module docstring,
    "Finished-task worktrees"); returns the removed entry's description.
    Run under the caller's repo lock.

    Raises:
        ResidueError: :func:`finished_worktree_refusal` names a reason to
            keep it, git refused the removal or the prune, or the worktree
            is still registered afterwards. Nothing is removed on a refusal
            found before git runs.
    """
    entry, refusal = finished_worktree_refusal(repo, path)
    if refusal:
        raise ResidueError(refusal)
    assert entry is not None  # a refusal-free assessment always has one
    if entry.path.exists() and not entry.prunable:
        result = run_worktree_cleanup(repo, "remove", str(entry.path))
        if result is None or not result.ok:
            why = (result.stderr.strip() if result is not None else "") or "git could not run"
            raise ResidueError(f"git worktree remove refused {entry.path}: {why}")
    pruned = run_worktree_cleanup(repo, "prune")
    if pruned is None or not pruned.ok:
        raise ResidueError(f"git worktree prune failed in {repo}")
    after = list_worktrees(repo)
    if after is None or worktree_entry_at(after, entry.path) is not None:
        raise ResidueError(f"{entry.describe()} is still registered after the cleanup")
    return entry.describe()


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python3 -m pgai_agent_kanban.overwatch.worktree_residue",
        description=(
            "Judge, and remove, the old worktree of a finished task (DONE or WONT-DO) the "
            "stale-worktree sweep check found. A worktree holding uncommitted or untracked "
            "changes, or commits found nowhere else, is kept. Removal never uses --force."
        ),
    )
    sub = parser.add_subparsers(dest="command")
    for name, text in (
        ("assess-finished", 'print "removable<TAB><entry>" or "keep<TAB><why>" (read-only); exit 0'),
        ("remove-finished", 're-check, then git worktree remove (no --force) and prune; '
                            'print "removed<TAB><entry>", or refuse dual-channel and exit 3'),
    ):
        cmd = sub.add_parser(name, help=text, description=text)
        cmd.add_argument("--repo", required=True, help="the project's dev tree (the repository the worktree belongs to)")
        cmd.add_argument("--path", required=True, help="the worktree path, as git worktree list prints it")
        cmd.add_argument("--project", default=None, help="project name the refusal is recorded against (optional)")
    return parser


def _one_line(text: str) -> str:
    return " ".join(text.split())


def main(argv: Optional[list] = None) -> int:
    """CLI entry point; returns the exit code (0 done, 3 refused, 2 bad usage)."""
    parser = _parser()
    args = parser.parse_args(argv)
    if not args.command:
        parser.print_help()
        return 0
    repo, path = Path(args.repo), Path(args.path)
    if args.command == "assess-finished":
        entry, refusal = finished_worktree_refusal(repo, path)
        if refusal:
            sys.stdout.write(f"keep\t{_one_line(refusal)}\n")
        else:
            sys.stdout.write(f"removable\t{_one_line(entry.describe() if entry else str(path))}\n")
        return 0
    try:
        removed = remove_finished_worktree(repo, path)
    except ResidueError as exc:
        look = (
            f"git -C {shlex.quote(str(path))} status --short"
            if path.is_dir()
            else f"git -C {shlex.quote(str(repo))} worktree list --porcelain"
        )
        # CONVERT: declining an automatic cleanup keeps the worktree exactly
        # as it was; nothing stops, the sweep carries on and reports it.
        # CENSUS-ANCHOR: worktree-residue-finished-refusal-01
        emit_error_contract(
            "overwatch.stale_worktree_kept",
            f"the finished task's worktree at {path} was not removed: {_one_line(str(exc))}",
            look,
            project=args.project,
        )
        return 3
    sys.stdout.write(f"removed\t{_one_line(removed)}\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
