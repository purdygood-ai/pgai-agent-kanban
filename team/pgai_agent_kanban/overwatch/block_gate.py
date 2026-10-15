"""
block_gate.py — what OVERWATCH's block triage reads and saves for a task the
CODER/WRITER completion gate refused.

The completion gate (``coder_writer_completion_gate`` in
``team/scripts/lib/wake_lifecycle.sh``) refuses a done-claim with one of
three Block Record reason codes; the triage executor
(:mod:`pgai_agent_kanban.overwatch.block_triage`) acts on each through the
pieces here:

``gate_merge_not_found``
    :func:`gate_merge_facts` supplies the classifier's two preconditions:
    ``branch_ancestor_of_source`` (the feature branch is already an ancestor
    of the source branch) and, only when it is not, ``merge_tree_clean``
    (``merge-tree --write-tree`` of the source branch and the feature branch
    finds no conflict). An ancestor is closed by :func:`recompute_gate`, which runs
    the gate's checks again, read-only, and passes only when they all pass
    and a merge commit on the source branch brings the branch tip in (the
    ``--no-ff`` merge that records the task's work landing). A clean
    merge-tree is the owner's to merge; a conflict stays with a human.
``gate_dirty_worktree``
    :func:`snapshot_dirty_worktree` saves the worktree's uncommitted changes
    to ``artifacts/block-evidence/dirty.patch`` (tracked changes against
    HEAD, plus every untracked file as a new-file diff, binary-safe, so
    ``git apply`` restores them) and lists the untracked files in
    ``untracked.txt``; ``dirty.patch.sha256`` records the patch hash in
    ``sha256sum`` format. Nothing is committed, staged or cleaned.
``gate_empty_commit``
    :func:`empty_commit_shas` names the empty non-merge commits the gate
    found, recomputed with the gate's own ``find_empty_commits``, else read
    from the refusal text in ``## Blockers``.

Recomputing the gate
--------------------
:func:`recompute_gate` runs the completion gate's three checks with
read-only git and changes nothing: no ref moves and no reflog grows.

1. Merge: the feature branch is an ancestor of the source branch; when the
   branch ref is gone, the gate's fallbacks (the worktree is on the source
   branch, a recent source-branch subject names the branch, or the
   worktree's HEAD commit is an ancestor of the source branch).
2. Cleanliness: ``git status --porcelain`` in the task worktree is empty.
3. Empty commits: when the source branch's tip is this task's own merge
   (its second parent is the branch tip), ``<source>^..<branch>`` holds no
   empty non-merge commit (``lint_rc_empty_commits.find_empty_commits``,
   the function the gate itself runs).

Where the gate passes without looking — the worktree directory is gone —
the recompute still runs the merge and empty-commit checks against the dev
tree, whose refs every worktree shares, and skips only the cleanliness
check. Before a task is closed it also requires the ``--no-ff`` merge
commit; a branch that is an ancestor only by a fast-forward, or because it
never moved, is not evidence the work landed and stays with a human.

Every git call goes through :mod:`readonly_git`. A read that fails is never
taken as a pass: facts come back False (the classifier then escalates) and
the actions raise :class:`GateTriageError` (the executor then escalates).

Usage (import):
    from pgai_agent_kanban.overwatch.block_gate import (
        gate_merge_facts, recompute_gate, snapshot_dirty_worktree,
        empty_commit_shas, GateTriageError,
    )
"""

from __future__ import annotations

import dataclasses
import datetime
import hashlib
import importlib.util
import re
from pathlib import Path
from typing import Callable, Optional

from pgai_agent_kanban.lib.atomic_write import atomic_write
from pgai_agent_kanban.lib.timeutil import format_iso
from pgai_agent_kanban.overwatch.block_record import BlockRecord
from pgai_agent_kanban.overwatch.block_requeue import GitTarget, GitTargetError, resolve_git_target
from pgai_agent_kanban.overwatch.readonly_git import run_readonly_git
from pgai_agent_kanban.task_status import read_section

#: Evidence file names a dirty-worktree snapshot writes (a name already taken
#: gets a -2, -3... suffix before its extension).
DIRTY_PATCH_NAME = "dirty.patch"
UNTRACKED_LIST_NAME = "untracked.txt"

#: Bytes an untracked file may hold and still be saved into dirty.patch; a
#: larger one is listed in untracked.txt as not saved.
UNTRACKED_FILE_LIMIT = 5_000_000

#: Source-branch subjects the gate searches for a merge naming the branch.
GATE_MERGE_LOG_DEPTH = 50

#: The recomputed gate's check labels (GateVerdict.check).
CHECK_MERGE = "merge-into-source"
CHECK_CLEAN = "cleanliness"
CHECK_EMPTY = "empty-commit"
CHECK_ALL = "all"

_ATOMIC_PREFIX = ".block_gate_tmp_"
_SHA_RE = re.compile(r"\b[0-9a-f]{40}\b")

#: The gate's own empty-commit finder, beside this package in every tree
#: (team/scripts/ in the dev tree, scripts/ in the install).
_EMPTY_COMMIT_LINT = Path(__file__).resolve().parents[2] / "scripts" / "lint_rc_empty_commits.py"


class GateTriageError(Exception):
    """A gate-class action cannot be taken; the message says why."""


# ---------------------------------------------------------------------------
# Git reads
# ---------------------------------------------------------------------------


def _ok(repo: Path, *args: str) -> Optional[bool]:
    """True / False for a git call's exit 0 / 1; None when it could not run."""
    result = run_readonly_git(repo, *args)
    if result is None or result.returncode not in (0, 1):
        return None
    return result.ok


def _rev(repo: Path, rev: str) -> str:
    result = run_readonly_git(repo, "rev-parse", "--verify", "--quiet", rev)
    return result.stdout.strip() if result is not None and result.ok else ""


def is_ancestor(repo: Path, ancestor: str, descendant: str) -> Optional[bool]:
    """Whether *ancestor* is an ancestor of *descendant* (None: unreadable)."""
    return _ok(repo, "merge-base", "--is-ancestor", ancestor, descendant)


def _target(task_dir: Path, record: BlockRecord) -> GitTarget:
    try:
        return resolve_git_target(task_dir, record)
    except GitTargetError as exc:
        raise GateTriageError(str(exc)) from exc


# ---------------------------------------------------------------------------
# gate_merge_not_found: facts and the read-only gate recompute
# ---------------------------------------------------------------------------


def merge_tree_conflicts(target: GitTarget) -> Optional[list[str]]:
    """The paths ``merge-tree --write-tree`` of the source branch and the
    feature branch reports in conflict: ``[]`` for a clean merge, None when it could not be computed."""
    if not target.branch_present or target.source is None:
        return None
    result = run_readonly_git(
        target.repo, "merge-tree", "--write-tree", "--name-only", "--no-messages",
        f"refs/heads/{target.source}", f"refs/heads/{target.branch}",
    )
    if result is None or result.returncode not in (0, 1):
        return None
    if result.ok:
        return []
    # Exit 1: the first line is the tree, the conflicted paths follow.
    return [line for line in result.stdout.splitlines()[1:] if line.strip()]


def gate_merge_facts(task_dir: Path, record: Optional[BlockRecord]) -> dict:
    """The classifier facts for a ``gate_merge_not_found`` block.

    ``branch_ancestor_of_source`` is always supplied (False when unreadable,
    so the record's block-time ``merged=`` evidence never stands in for a
    fresh read); ``merge_tree_clean`` only when the branch is not an
    ancestor. ``conflicts`` names the conflicted paths for the diagnosis.
    """
    facts: dict = {"branch_ancestor_of_source": False}
    if record is None:
        return facts
    try:
        target = resolve_git_target(task_dir, record)
    except GitTargetError:
        return facts
    if not target.branch_present or target.source is None:
        return facts
    ancestor = is_ancestor(target.repo, f"refs/heads/{target.branch}", f"refs/heads/{target.source}")
    facts["branch_ancestor_of_source"] = ancestor is True
    if ancestor is not True:
        conflicts = merge_tree_conflicts(target)
        facts["merge_tree_clean"] = conflicts == []
        if conflicts:
            facts["conflicts"] = ", ".join(conflicts)
    return facts


@dataclasses.dataclass(frozen=True)
class GateVerdict:
    """The recomputed gate: passed, or the check that refused and why."""

    passed: bool
    check: str
    detail: str


def _load_empty_commit_finder() -> Callable:
    spec = importlib.util.spec_from_file_location("lint_rc_empty_commits", _EMPTY_COMMIT_LINT)
    if spec is None or spec.loader is None:
        raise ImportError(f"cannot load {_EMPTY_COMMIT_LINT}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module.find_empty_commits


def _own_merge_range(target: GitTarget) -> Optional[str]:
    """``<source>^..<branch>`` when the source tip is this branch's own merge
    (second parent == branch tip), the gate's precondition for its
    empty-commit check; None otherwise."""
    if not target.branch_present or target.source is None:
        return None
    tip = _rev(target.repo, f"refs/heads/{target.branch}")
    second_parent = _rev(target.repo, f"refs/heads/{target.source}^2")
    if tip and second_parent and tip == second_parent:
        return f"refs/heads/{target.source}^..refs/heads/{target.branch}"
    return None


def gate_empty_commits(target: GitTarget) -> Optional[list[str]]:
    """The empty non-merge commits the gate's empty-commit check finds:
    ``[]`` when none (or the check does not apply), None when it cannot run."""
    span = _own_merge_range(target)
    if span is None:
        return []
    try:
        finder = _load_empty_commit_finder()
        return [found.sha for found in finder(target.repo, span)]
    except (ImportError, OSError, ValueError, AttributeError):
        return None


def _merge_check(target: GitTarget) -> GateVerdict:
    """The gate's merge check (see the module docstring), read-only."""
    repo, branch, source = target.git_dir, target.branch, target.source
    if source is None:
        return GateVerdict(False, CHECK_MERGE, f"the source branch {target.source_written} does not exist")
    if target.branch_present:
        ancestor = is_ancestor(repo, f"refs/heads/{branch}", f"refs/heads/{source}")
        if ancestor is None:
            return GateVerdict(False, CHECK_MERGE, f"git could not tell whether {branch} is merged into {source}")
        if ancestor:
            return GateVerdict(True, CHECK_MERGE, f"{branch} is an ancestor of {source}")
        return GateVerdict(False, CHECK_MERGE, f"{branch} is not merged into {source}")
    if target.worktree is not None:
        head = run_readonly_git(target.worktree, "rev-parse", "--abbrev-ref", "HEAD")
        if head is not None and head.ok and head.stdout.strip() == source:
            return GateVerdict(True, CHECK_MERGE, f"the worktree is on {source}")
    subjects = run_readonly_git(repo, "log", "--format=%s", f"-{GATE_MERGE_LOG_DEPTH}", f"refs/heads/{source}")
    if subjects is not None and subjects.ok and branch in subjects.stdout:
        return GateVerdict(True, CHECK_MERGE, f"a merge commit naming {branch} is in {source}'s recent history")
    if target.worktree is not None:
        head_commit = _rev(target.worktree, "HEAD")
        if head_commit and is_ancestor(repo, head_commit, f"refs/heads/{source}") is True:
            return GateVerdict(True, CHECK_MERGE, f"the worktree's commit {head_commit} is an ancestor of {source}")
    return GateVerdict(False, CHECK_MERGE, f"{branch} no longer exists and no evidence of its merge into {source} was found")


def recompute_gate(target: GitTarget) -> GateVerdict:
    """Run the completion gate's three checks again, read-only (module docstring)."""
    merge = _merge_check(target)
    if not merge.passed:
        return merge
    details = [merge.detail]
    if target.worktree is not None:
        dirty = run_readonly_git(target.worktree, "status", "--porcelain")
        if dirty is None or not dirty.ok:
            return GateVerdict(False, CHECK_CLEAN, f"git could not read the status of {target.worktree}")
        if dirty.stdout.strip():
            paths = "; ".join(line.strip() for line in dirty.stdout.splitlines() if line.strip())
            return GateVerdict(False, CHECK_CLEAN, f"the worktree {target.worktree} has uncommitted changes ({paths})")
        details.append("the worktree is clean")
    else:
        details.append("the worktree is gone, so there is nothing uncommitted to check")
    empty = gate_empty_commits(target)
    if empty is None:
        return GateVerdict(False, CHECK_EMPTY, "the empty-commit check could not run")
    if empty:
        return GateVerdict(False, CHECK_EMPTY, f"{target.branch} carries empty commit(s) {', '.join(empty)}")
    details.append("no empty commit")
    return GateVerdict(True, CHECK_ALL, "; ".join(details))


def landing_merge(target: GitTarget) -> Optional[str]:
    """The merge commit on the source branch's first-parent history that
    brings the branch tip in as a non-first parent, or None."""
    if not target.branch_present or target.source is None:
        return None
    tip = _rev(target.repo, f"refs/heads/{target.branch}")
    if not tip:
        return None
    merges = run_readonly_git(
        target.repo, "rev-list", "--first-parent", "--merges", "--parents",
        f"{tip}..refs/heads/{target.source}",
    )
    if merges is None or not merges.ok:
        return None
    for line in merges.stdout.splitlines():
        shas = line.split()
        if len(shas) >= 3 and tip in shas[2:]:
            return shas[0]
    return None


def recompute_gate_for_task(task_dir: Path, record: BlockRecord) -> str:
    """Close-out check for an already-merged branch: the recomputed gate must
    pass and a merge commit must bring the branch in.

    Returns:
        A one-line account of what passed, for the Triage Note.

    Raises:
        GateTriageError: the branch is not (or no longer) an ancestor, no
            merge commit brings it in, or a recomputed check refuses.
    """
    target = _target(task_dir, record)
    if not target.branch_present:
        raise GateTriageError(f"the feature branch {target.branch} no longer exists, so its merge cannot be recomputed")
    verdict = recompute_gate(target)
    if not verdict.passed:
        raise GateTriageError(f"the recomputed completion gate still refuses at its {verdict.check} check: {verdict.detail}")
    merge = landing_merge(target)
    if merge is None:
        raise GateTriageError(
            f"{target.branch} is an ancestor of {target.source}, but no merge commit on {target.source} brings it in "
            "(a fast-forward, or a branch that never moved), so the task's work is not shown to have landed"
        )
    return f"completion gate recomputed read-only and passes ({verdict.detail}); merge commit {merge} on {target.source}"


# ---------------------------------------------------------------------------
# gate_dirty_worktree: the uncommitted-changes snapshot
# ---------------------------------------------------------------------------


@dataclasses.dataclass(frozen=True)
class DirtySnapshot:
    """The written dirty-worktree evidence."""

    patch: Path
    untracked: Path
    checksum: Path
    sha256: str
    tracked_changes: bool
    untracked_saved: int
    untracked_skipped: int


def _unique(directory: Path, name: str) -> Path:
    stem, dot, ext = name.partition(".")
    target = directory / name
    n = 2
    while target.exists():
        target = directory / f"{stem}-{n}{dot}{ext}"
        n += 1
    return target


def _exact(text: str) -> bytes:
    return text.encode("utf-8", "surrogateescape")


def _write_bytes(path: Path, data: bytes) -> None:
    # latin-1 maps every byte to one code point and back, so the bytes land unchanged.
    atomic_write(path, data.decode("latin-1"), prefix=_ATOMIC_PREFIX, encoding="latin-1")


def snapshot_dirty_worktree(
    task_dir: Path, evidence_dir: Path, record: BlockRecord, *, now: datetime.datetime
) -> DirtySnapshot:
    """Save the task worktree's uncommitted changes under *evidence_dir*.

    Reads the worktree with ``git diff --binary HEAD`` (tracked changes,
    staged or not), ``git ls-files --others --exclude-standard`` (untracked
    files) and ``git diff --no-index --binary /dev/null <file>`` per
    untracked file; writes ``dirty.patch``, ``untracked.txt`` and
    ``dirty.patch.sha256``. Changes nothing in the worktree or the repository.

    Raises:
        GateTriageError: the worktree is gone or unreadable, it has no
            uncommitted change left to save, or a git read fails.
    """
    target = _target(task_dir, record)
    worktree = target.worktree
    if worktree is None:
        raise GateTriageError("the task worktree named in the Block Record evidence no longer exists")
    tracked = run_readonly_git(worktree, "diff", "--binary", "HEAD", exact_bytes=True)
    listed = run_readonly_git(worktree, "ls-files", "--others", "--exclude-standard", "-z")
    if tracked is None or not tracked.ok or listed is None or not listed.ok:
        raise GateTriageError(f"git could not read the uncommitted changes in {worktree}")
    patch = bytearray(_exact(tracked.stdout))
    untracked_lines: list[str] = []
    saved = skipped = 0
    for rel in (p for p in listed.stdout.split("\0") if p):
        try:
            size = (worktree / rel).lstat().st_size
        except OSError:
            size = -1
        if size < 0 or size > UNTRACKED_FILE_LIMIT:
            untracked_lines.append(
                f"{rel}\t(not saved in {DIRTY_PATCH_NAME}: "
                + (f"{size} bytes, over the {UNTRACKED_FILE_LIMIT}-byte limit)" if size >= 0 else "unreadable)")
            )
            skipped += 1
            continue
        added = run_readonly_git(worktree, "diff", "--no-index", "--binary", "--", "/dev/null", rel, exact_bytes=True)
        if added is None or added.returncode not in (0, 1):
            raise GateTriageError(f"git could not read the untracked file {rel} in {worktree}")
        patch += _exact(added.stdout)
        untracked_lines.append(rel)
        saved += 1
    if not patch and not untracked_lines:
        raise GateTriageError(
            f"the worktree {worktree} has no uncommitted change left to save, so the refusal no longer describes it"
        )
    evidence_dir.mkdir(parents=True, exist_ok=True)
    patch_path = _unique(evidence_dir, DIRTY_PATCH_NAME)
    untracked_path = _unique(evidence_dir, UNTRACKED_LIST_NAME)
    checksum_path = patch_path.with_name(patch_path.name + ".sha256")
    digest = hashlib.sha256(bytes(patch)).hexdigest()
    _write_bytes(patch_path, bytes(patch))
    header = (
        f"# Untracked files in {worktree} at {format_iso(now)}; each one saved is a new-file diff in "
        f"{patch_path.name}\n"
    )
    atomic_write(untracked_path, header + "".join(f"{line}\n" for line in untracked_lines), prefix=_ATOMIC_PREFIX)
    atomic_write(checksum_path, f"{digest}  {patch_path.name}\n", prefix=_ATOMIC_PREFIX)
    return DirtySnapshot(
        patch=patch_path, untracked=untracked_path, checksum=checksum_path, sha256=digest,
        tracked_changes=bool(tracked.stdout), untracked_saved=saved, untracked_skipped=skipped,
    )


# ---------------------------------------------------------------------------
# gate_empty_commit: the commits to quote
# ---------------------------------------------------------------------------


def empty_commit_shas(task_dir: Path, record: Optional[BlockRecord], status_text: str) -> list[str]:
    """The empty commits behind a ``gate_empty_commit`` block, oldest reading first.

    Recomputed with the gate's own finder when the source tip is still the
    task's merge; otherwise the full shas the refusal text in ``## Blockers``
    quotes. Empty when neither names one.
    """
    if record is not None:
        try:
            found = gate_empty_commits(resolve_git_target(task_dir, record))
        except GitTargetError:
            found = None
        if found:
            return found
    blockers = read_section(status_text, "Blockers") or ""
    return list(dict.fromkeys(_SHA_RE.findall(blockers)))


def gate_empty_commit_facts(task_dir: Path, record: Optional[BlockRecord], status_text: str) -> dict:
    """The note value ``shas`` for a ``gate_empty_commit`` block (absent when none is known)."""
    shas = empty_commit_shas(task_dir, record, status_text)
    return {"shas": ", ".join(shas)} if shas else {}
