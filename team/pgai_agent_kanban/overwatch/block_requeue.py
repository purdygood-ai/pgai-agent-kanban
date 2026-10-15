"""
block_requeue.py — what OVERWATCH's block triage needs to requeue a blocked
task to its owning role, and to check on the task's next terminal outcome
that the requeue worked.

The executor (:mod:`pgai_agent_kanban.overwatch.block_triage`) owns every
status.md and README.md write and the state mutation; this module supplies
the pieces it composes:

* :func:`resolve_git_target` — the dev tree, feature branch, source branch
  and (still present) worktree a blocked task's git work lives in, shared
  by every action that reads the task's repository.
* :func:`snapshot_commits_ahead` — before a timed-out task is requeued onto
  its existing branch, record the commits that branch holds ahead of the
  source branch under ``artifacts/block-evidence/commits-ahead-<UTC>.txt``.
  Read-only git only (:mod:`readonly_git`): the snapshot records the
  commits, it never creates them, and the branch is left exactly as it is
  for the owner's resume.
* :func:`timeout_seconds` — the per-task limit the timed-out attempt was
  killed at, for the Triage Note.
* :func:`zero_progress_count` / :func:`zero_progress_reset_text` — the
  durable ``## Zero Progress`` count, and the text-level reset a provider
  refusal requeue makes so the requeued attempt starts below the ceiling.
* :func:`transient_retry_state` — the durable ``## Transient Retry`` count
  and limit, which decide when a transient block leaves the transient
  handler for a human.
* :func:`resolve_requeue_budget` — ``[overwatch] requeue_budget``, the most
  times triage requeues one task over the life of its Block Record (the
  record's ``requeues``), whatever reasons its blocks give.
* The verification ledger and its verifiers (below).

Requeue budget
--------------
A verifier passes on any block other than the one its action addressed, so
a task whose blocks alternate between two reasons (a timeout, then a dirty
worktree, then a timeout...) passes every verification, restarts its block
epoch each time and would be requeued forever. The budget is the bound
that survives epoch restarts: the Block Record's ``requeues`` counts every
requeue since the record was created and is reset only when the task
reaches DONE or WONT-DO (which removes the record). Once it reaches the
budget, the executor escalates instead of requeueing again.

Verification ledger
-------------------
Every requeue the executor makes appends one ``pending`` entry to
``artifacts/block-evidence/verification.json``: the block epoch (the Block
Record's ``first_blocked_at``), the reason_code and attempt, the row's
verification, the ``last_blocked_at`` of the block occurrence acted on, and
the Zero Progress count after the action. When the task next blocks (its
next terminal outcome other than DONE / WONT-DO — those remove the record,
so the task is never examined again), the executor runs the entry's
verifier against the new block:

    next_outcome_not_timeout      passed unless the new block is another
                                  per_task_timeout
    zero_progress_not_readvanced  passed unless the Zero Progress count is
                                  above the count recorded after the action
    gate_passes                   passed unless the new block is another
                                  completion-gate refusal (a second refusal)
    next_create_succeeds          passed unless the new block is another
                                  failed worktree create
    rerun_reports_no_changes      passed unless the new block asks for an
                                  operator data action again (the verb's
                                  own idempotence re-run is checked when it
                                  runs; this catches an action that did not
                                  take)

and settles the entry ``passed``, ``failed`` or ``unverified`` (a row with
no verification, or a verification no verifier is registered for yet).
The entries are never deleted; the file is kept with the task like every
other evidence file.

Usage (import):
    from pgai_agent_kanban.overwatch.block_requeue import (
        resolve_git_target, snapshot_commits_ahead, timeout_seconds, zero_progress_count,
        zero_progress_reset_text, transient_retry_state, PendingCheck,
        read_ledger, append_pending, settle_entry, latest_pending, verify,
        resolve_requeue_budget, RequeueBudgetError,
    )
"""

from __future__ import annotations

import dataclasses
import datetime
import json
import re
import shlex
from pathlib import Path
from typing import Any, Callable, Mapping, Optional

from pgai_agent_kanban.lib import ini
from pgai_agent_kanban.lib.atomic_write import atomic_write
from pgai_agent_kanban.lib.timeutil import format_iso
from pgai_agent_kanban.overwatch.block_evidence import latest_dispatch
from pgai_agent_kanban.overwatch.block_record import NONE_VALUE, BlockRecord, parse_evidence
from pgai_agent_kanban.overwatch.readonly_git import run_readonly_git
from pgai_agent_kanban.task_status import (
    TaskStatusError,
    read_section,
    record_field,
    section_value,
    set_or_append_section,
)

#: The reason_code a per-task timeout block carries.
PER_TASK_TIMEOUT = "per_task_timeout"

#: The verification ledger, inside the task's block-evidence directory.
LEDGER_NAME = "verification.json"

#: Ledger entry states.
STATUS_PENDING = "pending"
STATUS_PASSED = "passed"
STATUS_FAILED = "failed"
STATUS_UNVERIFIED = "unverified"

#: Bytes of patch text a commit snapshot keeps; the commit list above it is
#: always complete.
SNAPSHOT_PATCH_LIMIT = 2_000_000

_ATOMIC_PREFIX = ".block_requeue_tmp_"

#: The status.md records this module reads and resets.
ZERO_PROGRESS_HEADING = "Zero Progress"
TRANSIENT_RETRY_HEADING = "Transient Retry"

# How the per-task timeout block site words the limit in ## Blockers, read
# liberally: "(1800s effective per-task limit" or "max_task_seconds (1800s".
_BLOCKERS_TIMEOUT_RES = (
    re.compile(r"\(\s*(\d+)\s*s\s+effective", re.IGNORECASE),
    re.compile(r"max[_ ]task[_ ]seconds\D{0,8}?(\d+)\s*s", re.IGNORECASE),
)


#: The config key, its section, and the value an absent or blank key resolves to.
REQUEUE_BUDGET_SECTION = "overwatch"
REQUEUE_BUDGET_KEY = "requeue_budget"
DEFAULT_REQUEUE_BUDGET = 3


class RequeueBudgetError(TaskStatusError):
    """``[overwatch] requeue_budget`` holds a value that is not a whole
    number; carries ``.error_key`` / ``.message`` / ``.remedy``."""


class GitTargetError(Exception):
    """The repository, feature branch or source branch a task's block names
    could not be resolved; the message names what was missing."""


class SnapshotError(GitTargetError):
    """The commits ahead could not be read; the message names what was missing."""


# ---------------------------------------------------------------------------
# Where things are
# ---------------------------------------------------------------------------


def project_root_of(task_dir: Path) -> Path:
    """The project root holding ``tasks/<task_id>/`` (two levels above the task folder)."""
    return task_dir.parent.parent


def _project_cfg_value(project_root: Path, key: str) -> str:
    """``[project] <key>`` from project.cfg (then the legacy PROJECT.cfg), quotes stripped."""
    for name in ("project.cfg", "PROJECT.cfg"):
        value = ini.get_value(project_root / name, "project", key).strip().strip("\"'")
        if value:
            return value
    return ""


def configured_dev_tree(project_root: Path) -> Optional[Path]:
    """The project's dev tree (``[project] dev_tree_path``), or None when unset."""
    value = _project_cfg_value(project_root, "dev_tree_path")
    return Path(value) if value else None


def _readme_value(task_dir: Path, heading: str) -> str:
    try:
        text = (task_dir / "README.md").read_text(encoding="utf-8", errors="replace")
    except OSError:
        return ""
    value = section_value(text, heading).strip()
    return "" if value.lower() == NONE_VALUE else value


def _branch_exists(repo: Path, branch: str) -> bool:
    result = run_readonly_git(repo, "rev-parse", "--verify", "--quiet", f"refs/heads/{branch}")
    return result is not None and result.ok


def _tip(repo: Path, branch: str) -> str:
    result = run_readonly_git(repo, "rev-parse", f"refs/heads/{branch}")
    return result.stdout.strip() if result is not None and result.ok else "unknown"


def _resolve_source(repo: Path, source: str, prefix: str) -> Optional[str]:
    """*source* as it exists in *repo*: as written, else with the project's branch prefix."""
    candidates = [source]
    if prefix and not source.startswith(prefix):
        candidates.append(prefix + source)
    return next((c for c in candidates if _branch_exists(repo, c)), None)


@dataclasses.dataclass(frozen=True)
class GitTarget:
    """Where a blocked task's git work lives.

    Fields:
        repo            the project's dev tree (``[project] dev_tree_path``).
        branch          the task's feature branch.
        branch_present  whether *branch* exists in *repo*.
        source_written  the task README's ``## Source Branch`` as written.
        source          *source_written* as it exists in *repo* (tried as
                        written, then with the project's ``branch_prefix``),
                        or None when it does not exist.
        worktree        the Block Record's ``worktree=`` path when that
                        directory still exists, else None.
    """

    repo: Path
    branch: str
    branch_present: bool
    source_written: str
    source: Optional[str]
    worktree: Optional[Path]

    @property
    def git_dir(self) -> Path:
        """Where git reads run: the task's worktree when it exists, else the dev tree."""
        return self.worktree if self.worktree is not None else self.repo


def resolve_git_target(task_dir: Path, record: BlockRecord, *, require_source: bool = True) -> GitTarget:
    """Resolve the dev tree, feature branch, source branch and worktree of a blocked task.

    The repository is the project's ``dev_tree_path``; the feature branch is
    the record's ``branch=`` evidence (else the task README's ``## Feature
    Branch``); the source branch is the README's ``## Source Branch``.
    Read-only git only.

    Raises:
        GitTargetError: no dev tree is configured or it is not a readable
            repository, no feature or source branch is named, or (with
            *require_source*) the source branch does not exist.
    """
    project_root = project_root_of(task_dir)
    repo = configured_dev_tree(project_root)
    if repo is None:
        raise GitTargetError(f"no [project] dev_tree_path in {project_root / 'project.cfg'}")
    probe = run_readonly_git(repo, "rev-parse", "--git-dir")
    if probe is None or not probe.ok:
        raise GitTargetError(f"the dev tree {repo} is not a readable git repository")
    evidence = parse_evidence(record.evidence)
    branch = evidence.get("branch", "")
    if not branch or branch.lower() == NONE_VALUE:
        branch = _readme_value(task_dir, "Feature Branch")
    if not branch:
        raise GitTargetError("neither the Block Record evidence nor the task README names the feature branch")
    source_written = _readme_value(task_dir, "Source Branch")
    if not source_written:
        raise GitTargetError("the task README names no ## Source Branch")
    source = _resolve_source(repo, source_written, _project_cfg_value(project_root, "branch_prefix"))
    if source is None and require_source:
        raise GitTargetError(f"the source branch {source_written} does not exist in {repo}")
    worktree_value = evidence.get("worktree", "")
    worktree = Path(worktree_value) if worktree_value and Path(worktree_value).is_dir() else None
    return GitTarget(
        repo=repo, branch=branch, branch_present=_branch_exists(repo, branch),
        source_written=source_written, source=source, worktree=worktree,
    )


# ---------------------------------------------------------------------------
# Commit snapshot
# ---------------------------------------------------------------------------


@dataclasses.dataclass(frozen=True)
class CommitSnapshot:
    """A written commits-ahead snapshot."""

    path: Path
    repo: str
    branch: str
    source: str
    ahead: int
    branch_present: bool
    tip: str


def _unique_path(directory: Path, base: str, suffix: str) -> Path:
    target = directory / f"{base}{suffix}"
    n = 2
    while target.exists():
        target = directory / f"{base}-{n}{suffix}"
        n += 1
    return target


def _compact_stamp(moment: datetime.datetime) -> str:
    return moment.astimezone(datetime.timezone.utc).strftime("%Y%m%dT%H%M%SZ")


def snapshot_commits_ahead(
    task_dir: Path, evidence_dir: Path, record: BlockRecord, *, now: datetime.datetime
) -> CommitSnapshot:
    """Write the commits the task's feature branch holds ahead of its source branch.

    The repository is the project's ``dev_tree_path``; the feature branch is
    the record's ``branch=`` evidence (else the task README's ``## Feature
    Branch``); the source branch is the README's ``## Source Branch`` (tried
    as written, then with the project's ``branch_prefix``). A feature branch
    that no longer exists is recorded as such with nothing ahead — there is
    nothing to keep, and the resume then starts fresh from the source
    branch.

    Raises:
        SnapshotError: no dev tree is configured or it is not a readable
            repository, no feature or source branch is named, the source
            branch does not exist, or a git read fails.
    """
    try:
        target = resolve_git_target(task_dir, record)
    except GitTargetError as exc:
        raise SnapshotError(str(exc)) from exc
    repo, branch, source, present = target.repo, target.branch, target.source, target.branch_present
    lines = [
        "# Commits ahead snapshot",
        "",
        f"recorded_at: {format_iso(now)}",
        f"repo: {repo}",
        f"source: {source} (tip {_tip(repo, source)})",
    ]
    ahead = 0
    commits = patch = ""
    if present:
        span = f"refs/heads/{source}..refs/heads/{branch}"
        counted = run_readonly_git(repo, "rev-list", "--count", span)
        listed = run_readonly_git(repo, "log", "--reverse", "--format=%H %aI %s", span)
        patched = run_readonly_git(repo, "log", "--reverse", "--format=fuller", "--stat", "-p", span)
        if any(r is None or not r.ok for r in (counted, listed, patched)) or not counted.stdout.strip().isdigit():
            raise SnapshotError(f"git could not list the commits {span} in {repo}")
        ahead = int(counted.stdout.strip())
        commits, patch = listed.stdout.rstrip(), patched.stdout
        lines.append(f"branch: {branch} (tip {_tip(repo, branch)})")
    else:
        lines.append(f"branch: {branch} (absent: there are no commits to keep)")
    lines += [
        f"ahead: {ahead}",
        "",
        "The branch is left exactly as it is for the owner's resume; this snapshot was taken",
        "with read-only git and created, moved or deleted nothing.",
        "",
        "## Commits (oldest first)",
        commits or "(none)",
    ]
    if patch:
        kept = patch[:SNAPSHOT_PATCH_LIMIT]
        lines += ["", "## Patch", kept.rstrip()]
        if len(patch) > SNAPSHOT_PATCH_LIMIT:
            lines.append(f"[patch cut at {SNAPSHOT_PATCH_LIMIT} bytes of {len(patch)}]")
    evidence_dir.mkdir(parents=True, exist_ok=True)
    target = _unique_path(evidence_dir, f"commits-ahead-{_compact_stamp(now)}", ".txt")
    atomic_write(target, "\n".join(lines) + "\n", prefix=_ATOMIC_PREFIX)
    return CommitSnapshot(
        path=target, repo=str(repo), branch=branch, source=source, ahead=ahead,
        branch_present=present, tip=_tip(repo, branch) if present else "absent",
    )


# ---------------------------------------------------------------------------
# Timeout, Zero Progress, Transient Retry
# ---------------------------------------------------------------------------


def timeout_seconds(status_text: str, record: Optional[BlockRecord], task_dir: Path) -> Optional[int]:
    """The per-task limit a timed-out attempt was killed at, in seconds.

    Read, first hit wins, from the record's ``timeout_s=`` evidence, the
    ``## Blockers`` text the timeout block site writes, then the latest
    dispatch's span in the task's dispatch ledger. None when none says.
    """
    if record is not None:
        value = parse_evidence(record.evidence).get("timeout_s", "")
        if value.isdigit():
            return int(value)
    blockers = read_section(status_text, "Blockers") or ""
    for pattern in _BLOCKERS_TIMEOUT_RES:
        m = pattern.search(blockers)
        if m:
            return int(m.group(1))
    dispatch = latest_dispatch(task_dir)
    if dispatch is not None and dispatch.span_seconds is not None:
        return int(dispatch.span_seconds)
    return None


def _whole(value: str) -> Optional[int]:
    text = value.strip()
    return int(text) if text.isdigit() else None


def zero_progress_count(status_text: str) -> int:
    """The ``## Zero Progress`` record's ``count`` (0 when absent or unreadable)."""
    return _whole(record_field(status_text, ZERO_PROGRESS_HEADING, "count", "0")) or 0


def zero_progress_reset_text(status_text: str, detail: str) -> str:
    """*status_text* with its ``## Zero Progress`` count reset to 0.

    The record keeps the shape the wake writes (``reason_code``, ``count``,
    ``limit``, ``detail``); ``limit`` keeps its recorded value. Text without
    the record, or whose count is already 0, comes back unchanged.
    """
    if read_section(status_text, ZERO_PROGRESS_HEADING) is None or zero_progress_count(status_text) == 0:
        return status_text
    limit = record_field(status_text, ZERO_PROGRESS_HEADING, "limit", "") or NONE_VALUE
    body = f"reason_code: {NONE_VALUE}\ncount: 0\nlimit: {limit}\ndetail: {detail}"
    return set_or_append_section(status_text, ZERO_PROGRESS_HEADING, body)


def transient_retry_state(status_text: str) -> tuple[int, Optional[int]]:
    """``(retry_count, retry_limit)`` from the ``## Transient Retry`` record.

    ``retry_count`` is 0 and ``retry_limit`` None when the record or the
    field is absent or not a whole number.
    """
    count = _whole(record_field(status_text, TRANSIENT_RETRY_HEADING, "retry_count", "0")) or 0
    limit = _whole(record_field(status_text, TRANSIENT_RETRY_HEADING, "retry_limit", ""))
    return count, limit


# ---------------------------------------------------------------------------
# Requeue budget
# ---------------------------------------------------------------------------


def resolve_requeue_budget(kanban_root: "Path | str", project: str) -> int:
    """``[overwatch] requeue_budget`` for *project*: how many times block
    triage may requeue one task over the life of its Block Record.

    Resolution order, first non-blank value wins (the same project-first
    precedence as ``[overwatch] block_triage``), read by the bash
    ``read_ini`` rules (an inline ``# comment`` is stripped):
      1. ``<kanban_root>/projects/<project>/project.cfg``
      2. ``<kanban_root>/kanban.cfg``
    Absent or blank in both, or a file that cannot be read, resolves to
    ``DEFAULT_REQUEUE_BUDGET``. ``0`` is accepted: no requeue at all, every
    block that would be requeued escalates.

    Raises:
        RequeueBudgetError: a present value that is not a whole number (a
            sign, a unit, a decimal point, a word), naming the value, the
            file and the accepted form. Never guessed.
    """
    root = Path(kanban_root)
    for source in (root / "projects" / project / "project.cfg", root / "kanban.cfg"):
        raw = ini.read_line_value(source, REQUEUE_BUDGET_SECTION, REQUEUE_BUDGET_KEY)
        if raw is None or not raw.strip():
            continue
        value = raw.strip()
        if value.isdigit():
            return int(value)
        # CONVERT: an unreadable budget stops block triage for this project on
        # this firing (nothing is planned or written, the calling check logs
        # the refusal); it never stops a wake, a task or a release.
        raise RequeueBudgetError(
            error_key="block-triage.bad-requeue-budget",
            message=(
                f"[{REQUEUE_BUDGET_SECTION}] {REQUEUE_BUDGET_KEY} = {value!r} in {source} is not a whole number "
                f"(accepted: 0 or more, e.g. {DEFAULT_REQUEUE_BUDGET}); block triage planned nothing for "
                f"project {project!r}"
            ),
            remedy=(
                f"sed -i 's/^[[:space:]]*{REQUEUE_BUDGET_KEY}[[:space:]]*=.*/{REQUEUE_BUDGET_KEY} = "
                f"{DEFAULT_REQUEUE_BUDGET}/' {shlex.quote(str(source))}"
            ),
        )
    return DEFAULT_REQUEUE_BUDGET


# ---------------------------------------------------------------------------
# Verification ledger
# ---------------------------------------------------------------------------


@dataclasses.dataclass(frozen=True)
class PendingCheck:
    """One ledger entry: a requeue and, once settled, its verification result.

    Fields:
        epoch                the block epoch acted in (``first_blocked_at``).
        reason_code          the block's reason_code when acted on.
        action               the action taken.
        attempt              the attempt number the action was.
        max_attempts         the row's ceiling (a whole number, or text).
        verification         the row's verification name, or None.
        acted_at             when the action was taken.
        blocked_at           ``last_blocked_at`` of the block acted on; a
                             later block write changes it, which is how the
                             next terminal outcome is recognised.
        zero_progress_count  the Zero Progress count after the action.
        status               pending | passed | failed | unverified.
        settled_at           when the verification ran ("" while pending).
        detail               what the verification found.
    """

    epoch: str
    reason_code: str
    action: str
    attempt: int
    max_attempts: Any
    verification: Optional[str]
    acted_at: str
    blocked_at: str
    zero_progress_count: int = 0
    status: str = STATUS_PENDING
    settled_at: str = ""
    detail: str = ""

    def as_dict(self) -> dict:
        """Return every field of the pending check as a dict, keyed by field name."""
        return dataclasses.asdict(self)

    @classmethod
    def from_dict(cls, raw: Mapping[str, Any]) -> Optional["PendingCheck"]:
        """The entry, or None when *raw* lacks a field or a field has the wrong type."""
        try:
            return cls(
                epoch=str(raw["epoch"]),
                reason_code=str(raw["reason_code"]),
                action=str(raw["action"]),
                attempt=int(raw["attempt"]),
                max_attempts=raw.get("max_attempts"),
                verification=(str(raw["verification"]) if raw.get("verification") else None),
                acted_at=str(raw["acted_at"]),
                blocked_at=str(raw["blocked_at"]),
                zero_progress_count=int(raw.get("zero_progress_count", 0)),
                status=str(raw.get("status", STATUS_PENDING)),
                settled_at=str(raw.get("settled_at", "")),
                detail=str(raw.get("detail", "")),
            )
        except (KeyError, TypeError, ValueError):
            return None


def ledger_path(evidence_dir: Path) -> Path:
    """The verification ledger inside *evidence_dir*."""
    return evidence_dir / LEDGER_NAME


def read_ledger(evidence_dir: Path) -> list[Optional[PendingCheck]]:
    """Every ledger entry in written order; an unreadable entry reads as None.

    A missing or unreadable ledger reads as empty: the Block Record's
    ``attempts`` still caps how often a block is acted on.
    """
    try:
        data = json.loads(ledger_path(evidence_dir).read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, ValueError):
        return []
    entries = data.get("entries") if isinstance(data, dict) else None
    if not isinstance(entries, list):
        return []
    return [PendingCheck.from_dict(e) if isinstance(e, dict) else None for e in entries]


def _write_ledger(evidence_dir: Path, entries: list[Optional[PendingCheck]]) -> None:
    evidence_dir.mkdir(parents=True, exist_ok=True)
    payload = {"entries": [e.as_dict() for e in entries if e is not None]}
    atomic_write(ledger_path(evidence_dir), json.dumps(payload, indent=2, sort_keys=True) + "\n", prefix=_ATOMIC_PREFIX)


def append_pending(evidence_dir: Path, entry: PendingCheck) -> None:
    """Append *entry* to the ledger (created when absent)."""
    _write_ledger(evidence_dir, [*read_ledger(evidence_dir), entry])


def latest_pending(entries: list[Optional[PendingCheck]], epoch: str) -> Optional[tuple[int, PendingCheck]]:
    """``(index, entry)`` of the newest ``pending`` entry for *epoch*, or None."""
    for index in range(len(entries) - 1, -1, -1):
        entry = entries[index]
        if entry is not None and entry.status == STATUS_PENDING and entry.epoch == epoch:
            return index, entry
    return None


def settle_entry(evidence_dir: Path, index: int, status: str, detail: str, *, now: datetime.datetime) -> None:
    """Record the verification result on entry *index* (a no-op when it is gone)."""
    entries = read_ledger(evidence_dir)
    if not 0 <= index < len(entries) or entries[index] is None:
        return
    entries[index] = dataclasses.replace(entries[index], status=status, detail=detail, settled_at=format_iso(now))
    _write_ledger(evidence_dir, entries)


# ---------------------------------------------------------------------------
# Verifiers
# ---------------------------------------------------------------------------


@dataclasses.dataclass(frozen=True)
class Verdict:
    """What a verification found about the block that followed a requeue."""

    name: Optional[str]
    status: str
    detail: str

    @property
    def passed(self) -> bool:
        """True when the verdict's status is ``passed``."""
        return self.status == STATUS_PASSED

    @property
    def failed(self) -> bool:
        """True when the verdict's status is ``failed``."""
        return self.status == STATUS_FAILED

    def as_dict(self) -> dict:
        """Return the verdict as a dict with keys ``name``, ``status`` and ``detail``."""
        return dataclasses.asdict(self)


Verifier = Callable[[PendingCheck, str, BlockRecord], Verdict]


def _next_outcome_not_timeout(entry: PendingCheck, status_text: str, record: BlockRecord) -> Verdict:
    name = "next_outcome_not_timeout"
    if record.reason_code == PER_TASK_TIMEOUT:
        return Verdict(name, STATUS_FAILED, "the next attempt was killed at the per-task timeout again (a second timeout)")
    return Verdict(name, STATUS_PASSED, f"the next attempt ended in {record.reason_code or 'a block'}, not a timeout")


def _zero_progress_not_readvanced(entry: PendingCheck, status_text: str, record: BlockRecord) -> Verdict:
    name = "zero_progress_not_readvanced"
    now_count = zero_progress_count(status_text)
    if now_count > entry.zero_progress_count:
        return Verdict(
            name, STATUS_FAILED,
            f"the Zero Progress count re-advanced from {entry.zero_progress_count} to {now_count}; "
            f"the next attempt blocked as {record.reason_code}",
        )
    return Verdict(
        name, STATUS_PASSED,
        f"the Zero Progress count did not re-advance ({now_count}, recorded {entry.zero_progress_count}); "
        f"the next attempt blocked as {record.reason_code}",
    )


#: The reason codes the completion gate blocks with.
GATE_REFUSAL_CODES: tuple[str, ...] = (
    "gate_merge_not_found",
    "gate_merge_evidence_not_found",
    "gate_dirty_worktree",
    "gate_empty_commit",
)

#: The reason codes a failed worktree create blocks with.
WORKTREE_CREATE_CODES: tuple[str, ...] = ("worktree_create_failed", "source_branch_missing")


def _gate_passes(entry: PendingCheck, status_text: str, record: BlockRecord) -> Verdict:
    name = "gate_passes"
    if record.reason_code in GATE_REFUSAL_CODES:
        return Verdict(
            name, STATUS_FAILED,
            f"the completion gate refused the next done-claim again ({record.reason_code}; a second refusal)",
        )
    return Verdict(
        name, STATUS_PASSED,
        f"the next attempt did not end in a completion-gate refusal (it blocked as {record.reason_code or 'a block'})",
    )


def _next_create_succeeds(entry: PendingCheck, status_text: str, record: BlockRecord) -> Verdict:
    name = "next_create_succeeds"
    if record.reason_code in WORKTREE_CREATE_CODES:
        return Verdict(
            name, STATUS_FAILED,
            f"the next attempt's worktree create failed again ({record.reason_code})",
        )
    return Verdict(
        name, STATUS_PASSED,
        f"the next attempt got past the worktree create (it blocked as {record.reason_code or 'a block'})",
    )


#: The reason code an agent blocks with when it needs an operator data action.
OPERATOR_DATA_ACTION = "agent_operator_data_action"


def _rerun_reports_no_changes(entry: PendingCheck, status_text: str, record: BlockRecord) -> Verdict:
    name = "rerun_reports_no_changes"
    if record.reason_code == OPERATOR_DATA_ACTION:
        return Verdict(
            name, STATUS_FAILED,
            "the next attempt blocked on an operator data action again, so the action that ran did not "
            "unblock the task",
        )
    return Verdict(
        name, STATUS_PASSED,
        f"the next attempt did not ask for an operator data action again (it blocked as "
        f"{record.reason_code or 'a block'})",
    )


#: verification name -> verifier. A row naming a verification with no
#: verifier here settles ``unverified``: its own attempts ceiling decides.
VERIFIERS: dict[str, Verifier] = {
    "next_outcome_not_timeout": _next_outcome_not_timeout,
    "zero_progress_not_readvanced": _zero_progress_not_readvanced,
    "gate_passes": _gate_passes,
    "next_create_succeeds": _next_create_succeeds,
    "rerun_reports_no_changes": _rerun_reports_no_changes,
}


def verify(
    entry: PendingCheck, status_text: str, record: BlockRecord, verifiers: Optional[Mapping[str, Verifier]] = None
) -> Verdict:
    """Run *entry*'s verification against the block that followed it."""
    verifiers = VERIFIERS if verifiers is None else verifiers
    if entry.verification is None:
        return Verdict(None, STATUS_UNVERIFIED, f"{entry.reason_code} names no verification")
    verifier = verifiers.get(entry.verification)
    if verifier is None:
        return Verdict(entry.verification, STATUS_UNVERIFIED, f"no verifier is registered for {entry.verification}")
    return verifier(entry, status_text, record)
