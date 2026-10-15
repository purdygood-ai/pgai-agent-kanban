"""
test_block_triage_gate_worktree_classes.py
==========================================
OVERWATCH block triage acts on the four git-facing block classes — the three
completion-gate refusals and a failed worktree create — and leaves every
case it must not fix with a human
(``pgai_agent_kanban.overwatch.block_triage``, ``block_gate`` and
``worktree_residue``, driven through the sweep check
``scripts/lib/overwatch-checks/check-block-triage.sh``).

Covered:
  1. Merge not found (AT4): (a) a branch already merged --no-ff into its
     source branch ends DONE with every ref and every reflog byte-identical;
     a branch that is an ancestor only because it never moved, or whose
     worktree is dirty, stays BLOCKED. (b) A clean merge-tree requeues the
     CODER owner with resume: branch and "merge only, then re-complete"; a
     TESTER owner and a second refusal escalate. (c) A conflicting
     merge-tree stays BLOCKED with Needs Human yes and a Diagnosis naming
     the conflicted path.
  2. Dirty worktree (AT5): dirty.patch, untracked.txt and the patch hash
     are written to block-evidence/, the hash is recorded in the Triage
     Note, no ref or reflog moves, the worktree keeps its changes, the patch
     restores them byte-for-byte on a clone, and the task is requeued with
     resume: branch. A worktree that is gone escalates.
  3. Empty commit: the requeue note quotes the empty commit's sha; with no
     sha to quote it escalates.
  4. Worktree create (AT6): a stale registered worktree that makes the real
     create_task_worktree fail is pruned, the task requeued, and the next
     create succeeds; a live clean residue holding the branch is removed
     the same way. Residue holding unique commits, residue outside the
     worktree base, and a live residue with uncommitted changes stay
     BLOCKED with nothing deleted; a repeat failure escalates.
  5. RC branch missing (AT7): stays BLOCKED with a Diagnosis, residue untouched.
  6. Safety: HALT_OVERWATCH and block_triage=false leave the residue alone;
     the git chokepoint refuses every worktree verb but list, remove (no
     --force) and prune.

Every fixture builds a sandbox kanban root and a throwaway git repository
under pytest's tmp_path; nothing reads or writes the live install or a real
provider.
"""

from __future__ import annotations

import datetime
import hashlib
import json
import pathlib
import shlex
import shutil
import subprocess
import textwrap

import pytest

from pgai_agent_kanban.overwatch.block_record import load_block_record, write_block_record
from pgai_agent_kanban.overwatch.block_requeue import read_ledger
from pgai_agent_kanban.overwatch.readonly_git import (
    READ_ONLY_SUBCOMMANDS,
    ReadOnlyGitRefused,
    run_readonly_git,
    run_worktree_cleanup,
)
from pgai_agent_kanban.task_status import read_section, set_or_append_section
from tests.fixtures.binary_guard import require_binary
from tests.fixtures.scrubbed_subprocess_env import build_scrubbed_env

# This file lives at team/tests/unit/; team/ is three parents up.
_TEAM_DIR = pathlib.Path(__file__).resolve().parent.parent.parent
_CHECK = _TEAM_DIR / "scripts" / "lib" / "overwatch-checks" / "check-block-triage.sh"

_PROJECT = "triage-proj"
_TASK = "CODER-20260101-002-example-task"
_FEATURE = f"ai_feature/{_TASK}"
_RC = "ai_rc/v9.9.9"

# git subcommands a gate-class triage may run: the chokepoint's read-only set
# plus diff-tree, which the completion gate's own empty-commit finder runs.
_GATE_READS = READ_ONLY_SUBCOMMANDS | {"diff-tree"}


def _diagnostic(result: subprocess.CompletedProcess) -> str:
    return f"--- stdout ---\n{result.stdout}\n--- stderr ---\n{result.stderr}"


def _git(repo: pathlib.Path, *args: str) -> str:
    done = subprocess.run(["git", "-C", str(repo), *args], capture_output=True, text=True)
    assert done.returncode == 0, f"git {' '.join(args)} failed in {repo}\n{_diagnostic(done)}"
    return done.stdout.strip()


def _section(text: str, heading: str) -> str:
    return (read_section(text, heading) or "").strip()


class Sandbox:
    """A throwaway kanban root with one task, and a dev-tree repository whose
    source branch, feature branch and retained task worktree the test shapes."""

    def __init__(self, tmp_path: pathlib.Path) -> None:
        self.tmp = tmp_path
        self.root = tmp_path / "kanban"
        self.project_root = self.root / "projects" / _PROJECT
        self.task_dir = self.project_root / "tasks" / _TASK
        self.queue = self.project_root / "tasks" / "queues" / "coder_backlog.md"
        self.repo = tmp_path / "repo"
        self.bin_dir = tmp_path / "bin"
        self.trace = tmp_path / "git-trace.log"
        self.worktree_base = self.root / "tmp" / "worktrees"
        self.worktree = self.worktree_base / _TASK
        self.home = tmp_path / "home"
        for d in (self.project_root / "overwatch" / "backups", self.task_dir / "artifacts", self.task_dir / "logs",
                  self.queue.parent, self.root / "locks", self.worktree_base, self.home, self.bin_dir):
            d.mkdir(parents=True, exist_ok=True)
        (self.project_root / "overwatch" / "actions.log").write_text("", encoding="utf-8")
        (self.project_root / "project.cfg").write_text(
            f"[project]\nproject_name = {_PROJECT}\ndev_tree_path = {self.repo}\nbranch_prefix = ai_\n",
            encoding="utf-8",
        )
        (self.root / "projects.cfg").write_text(f"[project:{_PROJECT}]\npriority = 1\n", encoding="utf-8")
        (self.root / "kanban.cfg").write_text("[wake]\n", encoding="utf-8")
        self.readme.write_text(
            f"# Task\n\n## Task ID\n{_TASK}\n\n## Role\nCODER\n\n## Workflow Type\nrelease\n\n"
            f"## Source Branch\n{_RC}\n\n## Feature Branch\n{_FEATURE}\n\n## Goal\nDo the thing.\n",
            encoding="utf-8",
        )
        self.status.write_text(
            f"# Status\n\n## Task\n{_TASK}\n\n## State\nWORKING\n\n## Summary\nWork merged.\n\n"
            "## Blockers\nnone\n\n## Needs Human\nno\n",
            encoding="utf-8",
        )
        self.set_marker("A")
        # A git that records each call, then runs the next git on PATH (never
        # itself, whatever wrapper the host puts ahead of the real binary).
        wrapper = self.bin_dir / "git"
        wrapper.write_text(
            "#!/usr/bin/env bash\n"
            f"printf '%s\\n' \"$*\" >> {shlex.quote(str(self.trace))}\n"
            f"PATH=\"${{PATH#{shlex.quote(str(self.bin_dir))}:}}\"\n"
            "exec git \"$@\"\n",
            encoding="utf-8",
        )
        wrapper.chmod(0o755)

    # -- task files -----------------------------------------------------

    @property
    def status(self) -> pathlib.Path:
        return self.task_dir / "status.md"

    @property
    def readme(self) -> pathlib.Path:
        return self.task_dir / "README.md"

    @property
    def evidence_dir(self) -> pathlib.Path:
        return self.task_dir / "artifacts" / "block-evidence"

    @property
    def actions(self) -> str:
        return (self.project_root / "overwatch" / "actions.log").read_text(encoding="utf-8")

    def text(self) -> str:
        return self.status.read_text(encoding="utf-8")

    def note(self) -> str:
        return _section(self.readme.read_text(encoding="utf-8"), "Triage Note")

    def set_marker(self, marker: str) -> None:
        self.queue.write_text(f"# CODER Backlog\n\n## Queue\n\n- [{marker}] {_TASK} — example\n", encoding="utf-8")

    def block(self, reason_code: str, evidence: list, *, owner: str = "CODER", blockers: str = "",
              now: "datetime.datetime | None" = None) -> None:
        """Block the task the way a block site does (a re-block keeps the epoch)."""
        text = self.text()
        for heading, body in (("State", "BLOCKED"), ("Needs Human", "yes"),
                              ("Blockers", blockers or f"{reason_code} block")):
            text = set_or_append_section(text, heading, body)
        self.status.write_text(text, encoding="utf-8")
        write_block_record(self.status, reason_code, owner, evidence, now=now)
        self.set_marker("B")

    def gate_evidence(self) -> list:
        return [f"worktree={self.worktree}", f"branch={_FEATURE}", "ahead=1", "merged=no"]

    # -- repository -----------------------------------------------------

    def make_repo(self) -> None:
        """main, the source branch and the feature branch at one base commit."""
        self.repo.mkdir()
        _git(self.repo, "init", "-q", "-b", "main")
        _git(self.repo, "config", "user.email", "fixture@example.invalid")
        _git(self.repo, "config", "user.name", "fixture")
        (self.repo / "shared.txt").write_text("base\n", encoding="utf-8")
        _git(self.repo, "add", "shared.txt")
        _git(self.repo, "commit", "-q", "-m", "base")
        _git(self.repo, "branch", _RC)
        _git(self.repo, "branch", _FEATURE, _RC)

    def add_task_worktree(self) -> None:
        """The task worktree on the feature branch, as the wake creates it."""
        _git(self.repo, "worktree", "add", "-q", str(self.worktree), _FEATURE)

    def commit_in(self, tree: pathlib.Path, name: str, content: str, message: str) -> str:
        (tree / name).write_text(content, encoding="utf-8")
        _git(tree, "add", name)
        _git(tree, "commit", "-q", "-m", message)
        return _git(tree, "rev-parse", "HEAD")

    def commit_on_source(self, name: str, content: str) -> None:
        _git(self.repo, "checkout", "-q", _RC)
        self.commit_in(self.repo, name, content, f"source work {name}")
        _git(self.repo, "checkout", "-q", "main")

    def merge_into_source(self, *, no_ff: bool = True) -> None:
        _git(self.repo, "checkout", "-q", _RC)
        _git(self.repo, "merge", "-q", "--no-ff" if no_ff else "--ff-only", "-m", f"Merge {_FEATURE}", _FEATURE)
        _git(self.repo, "checkout", "-q", "main")

    def git_state(self) -> dict:
        """Every ref, every worktree registration and every reflog, byte for byte."""
        git_dir = self.repo / ".git"
        logs = {
            str(p.relative_to(git_dir)): p.read_bytes()
            for p in sorted(git_dir.rglob("*")) if p.is_file() and "logs" in p.relative_to(git_dir).parts
        }
        return {
            "refs": _git(self.repo, "for-each-ref", "--format=%(refname) %(objectname)"),
            "worktrees": _git(self.repo, "worktree", "list", "--porcelain"),
            "logs": logs,
        }

    # -- running the check ----------------------------------------------

    def env(self, **extra: str) -> dict:
        return build_scrubbed_env(
            home_dir=self.home,
            extra_path_dirs=[self.bin_dir],
            extra_env={
                "KANBAN_ROOT": str(self.root),
                "PGAI_AGENT_KANBAN_ROOT_PATH": str(self.root),
                "PGAI_AGENT_KANBAN_TEMP_DIR": str(self.root / "tmp"),
                "PGAI_WORKTREE_BASE": str(self.worktree_base),
                "OVERWATCH_PROJECT": _PROJECT,
                "HALT_OVERWATCH": "",
                **extra,
            },
        )

    def bash(self, body: str, **extra_env: str) -> subprocess.CompletedProcess:
        script = f"set -uo pipefail\ncd {shlex.quote(str(_TEAM_DIR))}\n" + body
        return subprocess.run(["bash", "-c", script], capture_output=True, text=True, timeout=180,
                              env=self.env(**extra_env), cwd=str(self.tmp))

    def run_check(self, **extra_env: str) -> subprocess.CompletedProcess:
        self.trace.write_text("", encoding="utf-8")
        result = self.bash(f"source {shlex.quote(str(_CHECK))}\nrc=0\noverwatch_check_block_triage || rc=$?\n"
                           "echo \"CHECK_EXIT=$rc\"\n", **extra_env)
        assert "CHECK_EXIT=0" in result.stdout, _diagnostic(result)
        return result

    def create_worktree(self, mode: str = "fresh") -> subprocess.CompletedProcess:
        """The wake's real worktree create for the task."""
        return self.bash(textwrap.dedent(f"""\
            source scripts/lib/temp.sh
            source scripts/lib/worktree.sh
            create_task_worktree '{_TASK}' '{_RC}' '{_FEATURE}' '{self.repo}' '{mode}'
            echo "CREATE_RC=[$?]"
            """))

    def git_calls(self) -> list:
        return [shlex.split(line) for line in self.trace.read_text(encoding="utf-8").splitlines() if line.strip()]


@pytest.fixture
def sandbox(tmp_path: pathlib.Path) -> Sandbox:
    require_binary("git", label="block-triage-gate-worktree")
    return Sandbox(tmp_path)


def _subcommand(call: list) -> list:
    """A recorded git argv without its leading -C <dir> and -c k=v options."""
    rest = list(call)
    while rest and rest[0] in ("-C", "-c"):
        rest = rest[2:]
    return rest


def _assert_no_git_mutation(sb: Sandbox) -> None:
    for call in sb.git_calls():
        argv = _subcommand(call)
        if argv[:2] == ["worktree", "list"]:
            continue
        assert argv and argv[0] in _GATE_READS, f"block triage ran a non-read-only git call: git {call}"


def _ledger(sb: Sandbox) -> list:
    return [(e.reason_code, e.action, e.status) for e in read_ledger(sb.evidence_dir) if e is not None]


def _assert_stays_blocked_with_diagnosis(sb: Sandbox, *fragments: str) -> str:
    text = sb.text()
    assert _section(text, "State") == "BLOCKED" and _section(text, "Needs Human") == "yes", text
    assert f"- [B] {_TASK}" in sb.queue.read_text(encoding="utf-8")
    diagnosis = _section(text, "OVERWATCH Diagnosis")
    assert diagnosis.startswith("Block epoch "), text
    for fragment in fragments:
        assert fragment in diagnosis, f"{fragment!r} not in the diagnosis: {diagnosis}"
    assert not sb.note(), "an escalation takes no action, so it writes no Triage Note"
    return diagnosis


# ---------------------------------------------------------------------------
# 1. gate_merge_not_found (AT4)
# ---------------------------------------------------------------------------


def _merged_branch(sb: Sandbox) -> None:
    sb.make_repo()
    sb.add_task_worktree()
    sb.commit_in(sb.worktree, "feature.txt", "feature work\n", "feature work")
    sb.merge_into_source()
    sb.block("gate_merge_not_found", sb.gate_evidence())


def test_branch_already_merged_ends_done_with_every_ref_and_reflog_unchanged(sandbox):
    _merged_branch(sandbox)
    before = sandbox.git_state()

    sandbox.run_check()

    text = sandbox.text()
    assert _section(text, "State") == "DONE", text
    assert _section(text, "Needs Human") == "no" and _section(text, "Blockers") == "none"
    assert "## Block Record" not in text, "a task that reached DONE loses its Block Record"
    assert f"- [x] {_TASK}" in sandbox.queue.read_text(encoding="utf-8")
    assert sandbox.git_state() == before, "recomputing the gate moved a ref or wrote a reflog"
    _assert_no_git_mutation(sandbox)

    note = sandbox.note()
    assert "gate_merge_not_found: recompute_gate, attempt 1 of 1" in note
    assert "completion gate recomputed read-only and passes" in note and "merge commit" in note
    assert _ledger(sandbox) == [("gate_merge_not_found", "recompute_gate", "passed")]
    assert "block-triage:gate_merge_not_found:recompute_gate" in sandbox.actions


def test_branch_that_never_moved_is_an_ancestor_but_is_not_closed(sandbox):
    sandbox.make_repo()
    sandbox.add_task_worktree()
    sandbox.block("gate_merge_not_found", sandbox.gate_evidence())

    sandbox.run_check()

    _assert_stays_blocked_with_diagnosis(sandbox, "recompute_gate refused at execution time", "no merge commit")


def test_branch_merged_but_worktree_dirty_is_not_closed(sandbox):
    _merged_branch(sandbox)
    (sandbox.worktree / "feature.txt").write_text("edited after the merge\n", encoding="utf-8")

    sandbox.run_check()

    _assert_stays_blocked_with_diagnosis(sandbox, "cleanliness check", "uncommitted changes")


def _unmerged_branch(sb: Sandbox, *, conflicting: bool, owner: str = "CODER") -> None:
    sb.make_repo()
    sb.add_task_worktree()
    sb.commit_in(sb.worktree, "shared.txt" if conflicting else "feature.txt", "feature side\n", "feature work")
    sb.commit_on_source("shared.txt", "source side\n")
    sb.block("gate_merge_not_found", sb.gate_evidence(), owner=owner)


def test_clean_merge_tree_requeues_the_owner_to_merge_only(sandbox):
    _unmerged_branch(sandbox, conflicting=False)
    before = sandbox.git_state()

    sandbox.run_check()

    text = sandbox.text()
    assert _section(text, "State") == "BACKLOG" and _section(text, "Needs Human") == "no", text
    assert f"- [ ] {_TASK}" in sandbox.queue.read_text(encoding="utf-8")
    record = load_block_record(sandbox.status)
    assert (record.resume, record.attempts) == ("branch", 1)
    assert "gate_merge_not_found: requeue_owner, attempt 1 of 1. merge only, then re-complete" in sandbox.note()
    assert sandbox.git_state() == before, "merge-tree must leave every ref and reflog alone"
    _assert_no_git_mutation(sandbox)
    assert _ledger(sandbox) == [("gate_merge_not_found", "requeue_owner", "pending")]


def test_second_merge_refusal_after_a_requeue_escalates(sandbox):
    _unmerged_branch(sandbox, conflicting=False)
    sandbox.run_check()
    later = datetime.datetime.now(datetime.timezone.utc) + datetime.timedelta(minutes=5)
    sandbox.block("gate_merge_not_found", sandbox.gate_evidence(), now=later)

    sandbox.run_check()

    text = sandbox.text()
    assert _section(text, "State") == "BLOCKED" and _section(text, "Needs Human") == "yes"
    assert "gate_passes failed" in _section(text, "OVERWATCH Diagnosis")
    assert "a second refusal" in _section(text, "OVERWATCH Diagnosis")
    assert load_block_record(sandbox.status).attempts == 1
    assert _ledger(sandbox) == [("gate_merge_not_found", "requeue_owner", "failed")]


def test_clean_merge_tree_for_a_tester_owner_is_not_requeued(sandbox):
    _unmerged_branch(sandbox, conflicting=False, owner="TESTER")

    sandbox.run_check()

    _assert_stays_blocked_with_diagnosis(sandbox, "owner_is_coder_or_writer")


def test_conflicting_merge_tree_stays_blocked_naming_the_conflict(sandbox):
    _unmerged_branch(sandbox, conflicting=True)
    before = sandbox.git_state()

    sandbox.run_check()

    _assert_stays_blocked_with_diagnosis(
        sandbox, "merge_tree_clean", "merge-tree reports conflicts in: shared.txt", "Options: (1) merge the feature"
    )
    assert load_block_record(sandbox.status).attempts == 0
    assert sandbox.git_state() == before
    _assert_no_git_mutation(sandbox)


# ---------------------------------------------------------------------------
# 2. gate_dirty_worktree (AT5)
# ---------------------------------------------------------------------------

_LATIN1 = b"caf\xe9 au lait\n"
_BINARY = b"\x00\x01\xffbinary\x00payload"


def _dirty_worktree(sb: Sandbox) -> None:
    sb.make_repo()
    sb.add_task_worktree()
    sb.commit_in(sb.worktree, "feature.txt", "committed\n", "feature work")
    (sb.worktree / "feature.txt").write_text("committed\nand an unstaged edit\n", encoding="utf-8")
    (sb.worktree / "staged.txt").write_text("staged but not committed\n", encoding="utf-8")
    _git(sb.worktree, "add", "staged.txt")
    (sb.worktree / "notes dir").mkdir()
    (sb.worktree / "notes dir" / "latin1.txt").write_bytes(_LATIN1)
    (sb.worktree / "blob.bin").write_bytes(_BINARY)
    sb.block("gate_dirty_worktree", sb.gate_evidence())


def test_dirty_worktree_changes_are_saved_never_committed_and_the_owner_requeued(sandbox):
    _dirty_worktree(sandbox)
    before = sandbox.git_state()
    status_before = _git(sandbox.worktree, "status", "--porcelain")

    sandbox.run_check()

    text = sandbox.text()
    assert _section(text, "State") == "BACKLOG" and _section(text, "Needs Human") == "no", text
    record = load_block_record(sandbox.status)
    assert (record.resume, record.attempts) == ("branch", 1)

    patch = sandbox.evidence_dir / "dirty.patch"
    untracked = sandbox.evidence_dir / "untracked.txt"
    digest = hashlib.sha256(patch.read_bytes()).hexdigest()
    assert (sandbox.evidence_dir / "dirty.patch.sha256").read_text(encoding="utf-8") == f"{digest}  dirty.patch\n"
    listed = untracked.read_text(encoding="utf-8").splitlines()[1:]
    assert sorted(listed) == ["blob.bin", "notes dir/latin1.txt"]

    note = sandbox.note()
    assert f"sha256 {digest}" in note and "artifacts/block-evidence/dirty.patch" in note
    assert "gate_dirty_worktree: requeue_owner, attempt 1 of 1" in note

    # No commit anywhere, no ref moved, and the worktree still holds its changes.
    assert sandbox.git_state() == before
    assert _git(sandbox.worktree, "status", "--porcelain") == status_before
    _assert_no_git_mutation(sandbox)
    assert "block-triage:gate_dirty_worktree:requeue_owner" in sandbox.actions


def test_saved_dirty_patch_restores_every_change_byte_for_byte(sandbox):
    _dirty_worktree(sandbox)
    sandbox.run_check()
    clone = sandbox.tmp / "clone"
    _git(sandbox.tmp, "clone", "-q", "--branch", _FEATURE, str(sandbox.repo), str(clone))

    _git(clone, "apply", str(sandbox.evidence_dir / "dirty.patch"))

    for rel in ("feature.txt", "staged.txt", "notes dir/latin1.txt", "blob.bin"):
        assert (clone / rel).read_bytes() == (sandbox.worktree / rel).read_bytes(), rel


def test_dirty_worktree_that_is_gone_escalates_with_nothing_saved(sandbox):
    _dirty_worktree(sandbox)
    _git(sandbox.repo, "worktree", "remove", "--force", str(sandbox.worktree))

    sandbox.run_check()

    _assert_stays_blocked_with_diagnosis(sandbox, "could not be saved", "no longer exists")
    assert not (sandbox.evidence_dir / "dirty.patch").exists()


# ---------------------------------------------------------------------------
# 3. gate_empty_commit
# ---------------------------------------------------------------------------


def test_empty_commit_requeue_quotes_the_empty_commit_sha(sandbox):
    sandbox.make_repo()
    sandbox.add_task_worktree()
    sandbox.commit_in(sandbox.worktree, "feature.txt", "work\n", "feature work")
    _git(sandbox.worktree, "commit", "-q", "--allow-empty", "-m", "stray empty commit")
    empty_sha = _git(sandbox.worktree, "rev-parse", "HEAD")
    sandbox.merge_into_source()
    sandbox.block("gate_empty_commit", sandbox.gate_evidence())
    before = sandbox.git_state()

    sandbox.run_check()

    text = sandbox.text()
    assert _section(text, "State") == "BACKLOG", text
    assert load_block_record(sandbox.status).resume == "branch"
    assert empty_sha in sandbox.note() and "gate_empty_commit: requeue_owner, attempt 1 of 1" in sandbox.note()
    assert sandbox.git_state() == before
    _assert_no_git_mutation(sandbox)


def test_empty_commit_block_with_no_sha_to_quote_escalates(sandbox):
    sandbox.make_repo()
    sandbox.add_task_worktree()
    sandbox.block("gate_empty_commit", sandbox.gate_evidence(), blockers="refused for an empty commit")

    sandbox.run_check()

    _assert_stays_blocked_with_diagnosis(sandbox, "names the empty commit")


# ---------------------------------------------------------------------------
# 4. worktree_create_failed (AT6) and 5. RC branch missing (AT7)
# ---------------------------------------------------------------------------


def _stale_registration(sb: Sandbox, *, unique_commit: bool = False, path: "pathlib.Path | None" = None) -> pathlib.Path:
    """A registered worktree holding the feature branch whose directory was removed."""
    sb.make_repo()
    stale = path or sb.worktree
    _git(sb.repo, "worktree", "add", "-q", str(stale), _FEATURE)
    if unique_commit:
        sb.commit_in(stale, "unmerged.txt", "work nowhere else\n", "work nowhere else")
    shutil.rmtree(stale)
    return stale


def _create_fails(sb: Sandbox) -> None:
    result = sb.create_worktree()
    assert "CREATE_RC=[1]" in result.stdout and "already exists" in result.stderr, _diagnostic(result)
    sb.block("worktree_create_failed", ["worktree=gone", f"branch={_FEATURE}", "ahead=0", "merged=no"])


def test_stale_registered_worktree_is_pruned_and_the_next_create_succeeds(sandbox):
    _stale_registration(sandbox)
    _create_fails(sandbox)
    tip = _git(sandbox.repo, "rev-parse", _FEATURE)

    sandbox.run_check()

    text = sandbox.text()
    assert _section(text, "State") == "BACKLOG" and _section(text, "Needs Human") == "no", text
    assert str(sandbox.worktree) not in _git(sandbox.repo, "worktree", "list", "--porcelain")
    assert _git(sandbox.repo, "rev-parse", _FEATURE) == tip, "no branch may be deleted or moved"
    note = sandbox.note()
    assert "worktree_create_failed: prune_worktree_then_requeue, attempt 1 of 1" in note
    assert "no branch or commit was deleted" in note
    residue = sorted(sandbox.evidence_dir.glob("worktree-residue-*.txt"))
    assert len(residue) == 1 and str(sandbox.worktree) in residue[0].read_text(encoding="utf-8")
    assert "block-triage:worktree_create_failed:prune_worktree_then_requeue" in sandbox.actions

    mode = load_block_record(sandbox.status).resume
    created = sandbox.create_worktree(mode)
    assert "CREATE_RC=[0]" in created.stdout, _diagnostic(created)
    assert _git(sandbox.worktree, "rev-parse", "--abbrev-ref", "HEAD") == _FEATURE


def test_live_clean_residue_holding_the_branch_is_removed_and_the_next_create_succeeds(sandbox):
    sandbox.make_repo()
    old = sandbox.worktree_base / "earlier-attempt"
    _git(sandbox.repo, "worktree", "add", "-q", str(old), _FEATURE)
    _create_fails(sandbox)

    sandbox.run_check()

    assert _section(sandbox.text(), "State") == "BACKLOG", sandbox.text()
    assert not old.exists()
    created = sandbox.create_worktree(load_block_record(sandbox.status).resume)
    assert "CREATE_RC=[0]" in created.stdout, _diagnostic(created)


def test_repeat_create_failure_after_a_prune_escalates(sandbox):
    _stale_registration(sandbox)
    _create_fails(sandbox)
    sandbox.run_check()
    later = datetime.datetime.now(datetime.timezone.utc) + datetime.timedelta(minutes=10)
    sandbox.block("worktree_create_failed", ["worktree=gone", f"branch={_FEATURE}"], now=later)

    sandbox.run_check()

    text = sandbox.text()
    assert _section(text, "State") == "BLOCKED" and _section(text, "Needs Human") == "yes"
    assert "next_create_succeeds failed" in _section(text, "OVERWATCH Diagnosis")


def test_residue_holding_unique_commits_stays_blocked_and_nothing_is_deleted(sandbox):
    _stale_registration(sandbox, unique_commit=True)
    _create_fails(sandbox)
    before = sandbox.git_state()
    admin = sorted((sandbox.repo / ".git" / "worktrees").iterdir())

    sandbox.run_check()

    _assert_stays_blocked_with_diagnosis(
        sandbox, "residue_holds_no_unique_commits", "the residue holds commits found nowhere else"
    )
    assert sandbox.git_state() == before, "a residue with unique commits must be left exactly as it was"
    assert sorted((sandbox.repo / ".git" / "worktrees").iterdir()) == admin
    assert not list(sandbox.evidence_dir.glob("worktree-residue-*.txt"))


def test_residue_outside_the_worktree_base_stays_blocked_and_untouched(sandbox):
    _stale_registration(sandbox, path=sandbox.tmp / "elsewhere" / "operator-worktree")
    _create_fails(sandbox)
    before = sandbox.git_state()

    sandbox.run_check()

    _assert_stays_blocked_with_diagnosis(sandbox, "worktree_residue_in_base", "outside the worktree base")
    assert sandbox.git_state() == before


def test_live_residue_with_uncommitted_changes_stays_blocked_and_untouched(sandbox):
    sandbox.make_repo()
    old = sandbox.worktree_base / "earlier-attempt"
    _git(sandbox.repo, "worktree", "add", "-q", str(old), _FEATURE)
    (old / "uncommitted.txt").write_text("not committed anywhere\n", encoding="utf-8")
    _create_fails(sandbox)
    before = sandbox.git_state()

    sandbox.run_check()

    _assert_stays_blocked_with_diagnosis(sandbox, "holds uncommitted or untracked changes")
    assert (old / "uncommitted.txt").is_file()
    assert sandbox.git_state() == before


def test_rc_branch_missing_stays_blocked_with_a_diagnosis(sandbox):
    _stale_registration(sandbox)
    _create_fails(sandbox)
    _git(sandbox.repo, "branch", "-q", "-D", _RC)
    before = sandbox.git_state()

    sandbox.run_check()

    _assert_stays_blocked_with_diagnosis(sandbox, "source_branch_exists", f"the source branch {_RC} does not exist")
    assert sandbox.git_state() == before, "the residue is left in place when the source branch is missing"


# ---------------------------------------------------------------------------
# 6. Safety
# ---------------------------------------------------------------------------


def test_halt_overwatch_leaves_the_residue_and_the_task_unchanged(sandbox):
    _stale_registration(sandbox)
    _create_fails(sandbox)
    before, text_before = sandbox.git_state(), sandbox.text()

    sandbox.run_check(HALT_OVERWATCH="1")

    assert sandbox.git_state() == before and sandbox.text() == text_before
    assert "block-triage-refused" in sandbox.actions


def test_block_triage_disabled_leaves_the_residue_and_the_state_alone(sandbox):
    _stale_registration(sandbox)
    _create_fails(sandbox)
    (sandbox.project_root / "project.cfg").write_text(
        f"[project]\nproject_name = {_PROJECT}\ndev_tree_path = {sandbox.repo}\nbranch_prefix = ai_\n\n"
        "[overwatch]\nblock_triage = false\n",
        encoding="utf-8",
    )
    before = sandbox.git_state()

    result = sandbox.run_check()

    assert sandbox.git_state() == before
    assert _section(sandbox.text(), "State") == "BLOCKED"
    assert "block_triage is false" in result.stderr, _diagnostic(result)


@pytest.mark.parametrize(
    ("verb", "args"),
    [("add", ("x",)), ("remove", ("--force", "x")), ("remove", ("-f", "x")), ("remove", ()),
     ("prune", ("--expire", "now")), ("move", ("a", "b")), ("lock", ("x",))],
)
def test_worktree_cleanup_refuses_anything_but_plain_remove_and_prune(sandbox, verb, args):
    sandbox.make_repo()
    with pytest.raises(ReadOnlyGitRefused):
        run_worktree_cleanup(sandbox.repo, verb, *args)


@pytest.mark.parametrize("form", ["add", "remove", "prune", "move", "repair"])
def test_read_only_runner_allows_only_the_list_form_of_worktree(sandbox, form):
    sandbox.make_repo()
    with pytest.raises(ReadOnlyGitRefused, match="worktree"):
        run_readonly_git(sandbox.repo, "worktree", form, "x")
    listed = run_readonly_git(sandbox.repo, "worktree", "list", "--porcelain")
    assert listed is not None and listed.ok and str(sandbox.repo) in listed.stdout


def test_plain_worktree_remove_refuses_a_dirty_worktree(sandbox):
    sandbox.make_repo()
    live = sandbox.worktree_base / "dirty"
    _git(sandbox.repo, "worktree", "add", "-q", str(live), _FEATURE)
    (live / "untracked.txt").write_text("x\n", encoding="utf-8")

    result = run_worktree_cleanup(sandbox.repo, "remove", str(live))

    assert result is not None and not result.ok
    assert (live / "untracked.txt").is_file()


def test_executor_evidence_records_the_recomputed_gate(sandbox):
    _merged_branch(sandbox)

    sandbox.run_check()

    files = sorted(sandbox.evidence_dir.glob("triage-*-act.json"))
    assert len(files) == 1, list(sandbox.evidence_dir.iterdir())
    payload = json.loads(files[0].read_text(encoding="utf-8"))
    assert payload["reason_code"] == "gate_merge_not_found" and payload["action"] == "recompute_gate"
    assert pathlib.Path(payload["backups"]["status.md"]).is_file(), payload["backups"]
