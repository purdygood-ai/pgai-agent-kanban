"""
test_block_triage_requeue_classes.py
====================================
OVERWATCH block triage requeues the three requeue classes to their owner and
verifies each requeue on the task's next terminal outcome
(``pgai_agent_kanban.overwatch.block_triage`` driven by the sweep check
``scripts/lib/overwatch-checks/check-block-triage.sh``).

Covered:
  1. Timeout (AT2): a per_task_timeout block whose feature branch is 2
     commits ahead and whose worktree is gone becomes BACKLOG with
     ``resume: branch``, attempts 1, a Triage Note naming the limit, both
     backups, a ``block-triage:per_task_timeout:requeue_owner`` row and a
     commits-ahead snapshot, using read-only git only; a re-dispatch
     through create_task_worktree in the record's resume mode keeps both
     commits; a second timeout on the same epoch ends with Needs Human yes
     and an OVERWATCH Diagnosis, and no requeue. A snapshot that cannot be
     taken escalates instead of requeueing.
  2. Provider refusal (AT3): a short dispatch whose batch-log tail says
     "model not found" is blocked by the real zero-progress site as
     provider_refusal and is not requeued before its cooldown_until; after
     it, the requeue resets the Zero Progress count. Repeated refusals are
     retried 15 -> 30 -> 60 minutes apart, three times, then escalate naming
     a fleet-level cause.
  3. Zero progress (AT3): a normal-length clean dispatch is blocked as
     zero_progress, gets one requeue, and escalates when the count
     re-advances.
  4. Block epochs: a requeue whose next block is a different incident
     passes verification and restarts the epoch; a block occurrence already
     acted on is never acted on again.
  5. Transient blocks stay with the transient-error handlers below their
     ceiling and escalate at it.
  6. The read-only git runner refuses every subcommand outside its
     allowlist.

Every fixture builds a sandbox kanban root and a throwaway git repository
under pytest's tmp_path; nothing reads or writes the live install or a real
provider.

Naming: function names describe behaviour under test, never bug IDs or task
IDs (roles/TESTER.md "Test Authoring Guidelines").
"""

from __future__ import annotations

import datetime
import json
import pathlib
import re
import shlex
import shutil
import subprocess
import textwrap

import pytest

from pgai_agent_kanban.overwatch import block_triage as bt
from pgai_agent_kanban.overwatch.block_record import load_block_record, update_block_record, write_block_record
from pgai_agent_kanban.overwatch.block_requeue import read_ledger
from pgai_agent_kanban.overwatch.readonly_git import READ_ONLY_SUBCOMMANDS, ReadOnlyGitRefused, run_readonly_git
from pgai_agent_kanban.task_status import read_section, set_or_append_section
from tests.fixtures.scrubbed_subprocess_env import build_scrubbed_env

# This file lives at team/tests/unit/; team/ is three parents up.
_TEAM_DIR = pathlib.Path(__file__).resolve().parent.parent.parent
_LIB = _TEAM_DIR / "scripts" / "lib"
_CHECK = _LIB / "overwatch-checks" / "check-block-triage.sh"

_PROJECT = "triage-proj"
_TASK = "CODER-20260101-001-example-task"
_FEATURE = f"ai_feature/{_TASK}"
_RC = "ai_rc/v9.9.9"


# ---------------------------------------------------------------------------
# Sandbox
# ---------------------------------------------------------------------------


def _git(repo: pathlib.Path, *args: str) -> str:
    done = subprocess.run(["git", "-C", str(repo), *args], check=True, capture_output=True, text=True)
    return done.stdout.strip()


def _section(text: str, heading: str) -> str:
    return (read_section(text, heading) or "").strip()


class Sandbox:
    """A throwaway kanban root, one project with one task, and a dev-tree repo."""

    def __init__(self, tmp_path: pathlib.Path) -> None:
        self.root = tmp_path / "kanban"
        self.project_root = self.root / "projects" / _PROJECT
        self.task_dir = self.project_root / "tasks" / _TASK
        self.queue = self.project_root / "tasks" / "queues" / "coder_backlog.md"
        self.repo = tmp_path / "repo"
        self.bin_dir = tmp_path / "bin"
        self.trace = tmp_path / "git-trace.log"
        self.worktree_base = self.root / "tmp" / "worktrees"
        self.batch_log = self.root / "logs" / "agents" / "coder-batch-20260101-000000.log"
        for d in (self.project_root / "overwatch" / "backups", self.task_dir / "artifacts", self.task_dir / "logs",
                  self.queue.parent, self.root / "locks", self.batch_log.parent, self.root / "tmp",
                  self.worktree_base, tmp_path / "home", self.bin_dir):
            d.mkdir(parents=True, exist_ok=True)
        self.home = tmp_path / "home"
        (self.project_root / "overwatch" / "actions.log").write_text("", encoding="utf-8")
        (self.project_root / "project.cfg").write_text(
            f"[project]\nproject_name = {_PROJECT}\ndev_tree_path = {self.repo}\nbranch_prefix = ai_\n",
            encoding="utf-8",
        )
        (self.root / "projects.cfg").write_text(f"[project:{_PROJECT}]\npriority = 1\n", encoding="utf-8")
        (self.root / "kanban.cfg").write_text("[wake]\n", encoding="utf-8")
        self.batch_log.write_text("wake starting\n", encoding="utf-8")
        self.readme.write_text(
            f"# Task\n\n## Task ID\n{_TASK}\n\n## Role\nCODER\n\n## Workflow Type\nrelease\n\n"
            f"## Source Branch\n{_RC}\n\n## Feature Branch\n{_FEATURE}\n\n## Goal\nDo the thing.\n",
            encoding="utf-8",
        )
        self.status.write_text(
            f"# Status\n\n## Task\n{_TASK}\n\n## State\nWORKING\n\n## Summary\nIn progress.\n\n"
            "## Blockers\nnone\n\n## Blocked Reason\nnone\n\n## Needs Human\nno\n",
            encoding="utf-8",
        )
        self.set_marker("A")
        # A git that records each call, then runs the next git on PATH.
        assert shutil.which("git"), "git is required for these fixtures"
        wrapper = self.bin_dir / "git"
        wrapper.write_text(
            "#!/usr/bin/env bash\n"
            f"printf '%s\\n' \"$*\" >> {shlex.quote(str(self.trace))}\n"
            f"PATH=\"${{PATH#{shlex.quote(str(self.bin_dir))}:}}\"\n"
            "exec git \"$@\"\n",
            encoding="utf-8",
        )
        wrapper.chmod(0o755)

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

    def set_marker(self, marker: str) -> None:
        self.queue.write_text(f"# CODER Backlog\n\n## Queue\n\n- [{marker}] {_TASK} — example\n", encoding="utf-8")

    def set_sections(self, **sections: str) -> None:
        text = self.text()
        for heading, body in sections.items():
            text = set_or_append_section(text, heading.replace("_", " "), body)
        self.status.write_text(text, encoding="utf-8")

    def block(self, reason_code: str, evidence: list, *, now: "datetime.datetime | None" = None,
              needs_human: str = "yes") -> None:
        """Block the task the way a block site does: State, Needs Human, the
        record (a re-block keeps the epoch) and the [B] marker."""
        self.set_sections(State="BLOCKED", Needs_Human=needs_human, Blockers=f"{reason_code} block")
        write_block_record(self.status, reason_code, "CODER", evidence, now=now)
        self.set_marker("B")

    def make_repo(self, feature_commits: int) -> None:
        self.repo.mkdir()
        _git(self.repo, "init", "-q", "-b", "main")
        _git(self.repo, "config", "user.email", "fixture@example.invalid")
        _git(self.repo, "config", "user.name", "fixture")
        (self.repo / "base.txt").write_text("base\n", encoding="utf-8")
        _git(self.repo, "add", "base.txt")
        _git(self.repo, "commit", "-q", "-m", "base")
        _git(self.repo, "branch", _RC)
        _git(self.repo, "branch", _FEATURE, _RC)
        scratch = self.repo.parent / "scratch-wt"
        _git(self.repo, "worktree", "add", "-q", str(scratch), _FEATURE)
        for n in range(feature_commits):
            (scratch / f"work{n}.txt").write_text(f"{n}\n", encoding="utf-8")
            _git(scratch, "add", f"work{n}.txt")
            _git(scratch, "commit", "-q", "-m", f"timed-out work {n}")
        _git(self.repo, "worktree", "remove", "--force", str(scratch))

    def leave_worktree_gone(self) -> None:
        """A registered worktree for the task whose directory was removed."""
        path = self.worktree_base / _TASK
        _git(self.repo, "worktree", "add", "-q", str(path), _FEATURE)
        shutil.rmtree(path)

    def write_dispatch(self, tail: str, span_seconds: int) -> None:
        """One dispatch's output in the batch log and its ledger record."""
        start = self.batch_log.stat().st_size
        with self.batch_log.open("a", encoding="utf-8") as fh:
            fh.write(tail)
        end = self.batch_log.stat().st_size
        minute, second = divmod(span_seconds, 60)
        rows = [
            {"event": "dispatched", "batch_log": str(self.batch_log), "offset_start": start,
             "offset_end": None, "exit_code": None, "recorded_at": "2026-01-01T10:00:00Z"},
            {"event": "returned", "batch_log": str(self.batch_log), "offset_start": start,
             "offset_end": end, "exit_code": 0, "recorded_at": f"2026-01-01T10:{minute:02d}:{second:02d}Z"},
        ]
        (self.task_dir / "logs" / "dispatch.jsonl").write_text(
            "".join(json.dumps(r) + "\n" for r in rows), encoding="utf-8"
        )

    def env(self) -> dict:
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
            },
        )

    def bash(self, body: str) -> subprocess.CompletedProcess:
        script = f"set -uo pipefail\ncd {shlex.quote(str(_TEAM_DIR))}\n" + body
        return subprocess.run(["bash", "-c", script], capture_output=True, text=True, timeout=180, env=self.env())

    def run_check(self) -> subprocess.CompletedProcess:
        self.trace.write_text("", encoding="utf-8")
        result = self.bash(f"source {shlex.quote(str(_CHECK))}\nrc=0\noverwatch_check_block_triage || rc=$?\n"
                           "echo \"CHECK_EXIT=$rc\"\n")
        assert "CHECK_EXIT=0" in result.stdout, f"stdout={result.stdout}\nstderr={result.stderr}"
        return result

    def git_calls(self) -> list:
        return [shlex.split(line) for line in self.trace.read_text(encoding="utf-8").splitlines() if line.strip()]

    def drive_zero_progress_site(self) -> None:
        """Run the real zero-progress ceiling site (overwatch_protocol.sh) once,
        with only the queue marker and the state transition stubbed."""
        result = self.bash(textwrap.dedent(f"""\
            set -e
            export LOG_FILE={shlex.quote(str(self.batch_log))}
            log() {{ echo "[test] wake: $*" >&2; }}
            source scripts/lib/pp_run_ops.sh
            source scripts/lib/atomic_write.sh
            source scripts/lib/error_contract.sh
            source scripts/lib/block_record.sh
            source scripts/lib/overwatch_protocol.sh
            {_extract_wake_writers()}
            transition_task_state() {{ set_state "$2" "$3"; }}
            ZERO_PROGRESS_CAP=3
            zero_progress_advance_or_escalate {_TASK} {shlex.quote(str(self.status))} clean_exit_no_progress \
                "agent exited without moving the task" "zero_progress.cap_reached" >/dev/null
            """))
        assert result.returncode == 0, result.stderr


def _extract_wake_writers() -> str:
    """Bash defining the real status.md writers from wake_task_state.sh (brace-matched)."""
    names = "ensure_status_file|_read_status_section|get_state|normalize_status_file|set_state|set_block"
    program = (
        "/^(" + names + r")\(\)/ { found=1; depth=0 } "
        'found { print; n = split($0, a, ""); for (i = 1; i <= n; i++) { '
        'if (a[i] == "{") depth++; if (a[i] == "}") { depth--; if (depth == 0) { found=0 } } } }'
    )
    return f"eval \"$(awk '{program}' {shlex.quote(str(_LIB / 'wake_task_state.sh'))})\""


@pytest.fixture
def sandbox(tmp_path: pathlib.Path) -> Sandbox:
    return Sandbox(tmp_path)


# Read-only subcommands, and the -C path every triage git call carries.
def _assert_read_only_git(sb: Sandbox) -> None:
    for call in sb.git_calls():
        assert call[:2] == ["-C", str(sb.repo)], f"git ran outside the dev tree: git {call}"
        assert call[2] in READ_ONLY_SUBCOMMANDS, f"block triage ran a non-read-only git call: git {call}"


def _ledger_statuses(sb: Sandbox) -> list:
    return [(e.reason_code, e.attempt, e.status) for e in read_ledger(sb.evidence_dir) if e is not None]


# ---------------------------------------------------------------------------
# 1. Timeout (AT2)
# ---------------------------------------------------------------------------


def _timeout_requeued(sb: Sandbox) -> str:
    """Block the task at the per-task timeout with 2 commits ahead and the
    worktree gone, run the check once; return the feature branch tip."""
    sb.make_repo(feature_commits=2)
    sb.leave_worktree_gone()
    tip = _git(sb.repo, "rev-parse", _FEATURE)
    sb.block("per_task_timeout", ["worktree=gone", f"branch={_FEATURE}", "ahead=2", "merged=no", "timeout_s=1800"])
    sb.run_check()
    return tip


def test_timeout_with_commits_ahead_and_worktree_gone_is_requeued_onto_its_branch(sandbox):
    tip = _timeout_requeued(sandbox)

    text = sandbox.text()
    assert _section(text, "State") == "BACKLOG" and _section(text, "Needs Human") == "no", text
    assert f"- [ ] {_TASK}" in sandbox.queue.read_text(encoding="utf-8")
    record = load_block_record(sandbox.status)
    assert (record.resume, record.attempts) == ("branch", 1)
    assert record.cooldown_until != "none"

    note = _section(sandbox.readme.read_text(encoding="utf-8"), "Triage Note")
    assert "per_task_timeout: requeue_owner, attempt 1 of 1" in note
    assert "previous attempt was killed at 1800s" in note
    assert f"Branch {_FEATURE} holds 2 commit(s) ahead of {_RC}" in note

    snapshots = sorted(sandbox.evidence_dir.glob("commits-ahead-*.txt"))
    assert len(snapshots) == 1, list(sandbox.evidence_dir.iterdir())
    snapshot = snapshots[0].read_text(encoding="utf-8")
    assert "ahead: 2" in snapshot and tip in snapshot
    assert "timed-out work 0" in snapshot and "timed-out work 1" in snapshot
    assert snapshots[0].name in note

    backups = {p.name for p in (sandbox.project_root / "overwatch" / "backups").rglob("*") if p.is_file()}
    assert {"status.md", "coder_backlog.md"} <= backups
    row = [r for r in sandbox.actions.splitlines() if "block-triage:per_task_timeout:requeue_owner" in r]
    assert row and "status.md" in row[0], sandbox.actions
    assert _ledger_statuses(sandbox) == [("per_task_timeout", 1, "pending")]

    # The snapshot read git and changed nothing: the branch is where it was.
    _assert_read_only_git(sandbox)
    assert sandbox.git_calls(), "the snapshot should have read the dev tree"
    assert _git(sandbox.repo, "rev-parse", _FEATURE) == tip


def test_requeued_timeout_redispatched_through_create_task_worktree_keeps_both_commits(sandbox):
    tip = _timeout_requeued(sandbox)
    worktree = sandbox.worktree_base / _TASK
    result = sandbox.bash(textwrap.dedent(f"""\
        source scripts/lib/temp.sh
        source scripts/lib/worktree.sh
        mode="$(python3 -m pgai_agent_kanban.overwatch.block_record read {shlex.quote(str(sandbox.status))} \
            --field resume)"
        create_task_worktree '{_TASK}' '{_RC}' '{_FEATURE}' '{sandbox.repo}' "$mode"
        echo "RC=[$?] MODE=[$mode]"
        """))
    assert "RC=[0] MODE=[branch]" in result.stdout, f"stdout={result.stdout}\nstderr={result.stderr}"
    assert _git(worktree, "rev-parse", "HEAD") == tip
    assert _git(sandbox.repo, "rev-list", "--count", f"{_RC}..{_FEATURE}") == "2"
    assert (worktree / "work0.txt").is_file() and (worktree / "work1.txt").is_file()


def test_second_timeout_on_the_same_epoch_escalates_without_a_requeue(sandbox):
    _timeout_requeued(sandbox)
    epoch = load_block_record(sandbox.status).first_blocked_at
    note_before = _section(sandbox.readme.read_text(encoding="utf-8"), "Triage Note")
    later = datetime.datetime.now(datetime.timezone.utc) + datetime.timedelta(minutes=5)
    sandbox.block("per_task_timeout", ["worktree=gone", f"branch={_FEATURE}", "ahead=3", "timeout_s=1800"],
                  now=later)

    sandbox.run_check()

    text = sandbox.text()
    assert _section(text, "State") == "BLOCKED" and _section(text, "Needs Human") == "yes"
    assert f"- [B] {_TASK}" in sandbox.queue.read_text(encoding="utf-8")
    diagnosis = _section(text, "OVERWATCH Diagnosis")
    assert diagnosis.startswith(f"Block epoch {epoch}"), diagnosis
    assert "next_outcome_not_timeout failed" in diagnosis and "a second timeout" in diagnosis
    assert "Options: (1) split the task" in diagnosis and "--state BACKLOG --needs-human no" in diagnosis
    record = load_block_record(sandbox.status)
    assert (record.first_blocked_at, record.attempts) == (epoch, 1), "the epoch is not re-attempted"
    assert _section(sandbox.readme.read_text(encoding="utf-8"), "Triage Note") == note_before
    assert "block-triage:per_task_timeout:escalate" in sandbox.actions
    assert _ledger_statuses(sandbox) == [("per_task_timeout", 1, "failed")]

    # A later sweep leaves the escalated epoch alone.
    before = sandbox.text()
    result = sandbox.run_check()
    assert sandbox.text() == before and "already escalated" in result.stderr


def test_timeout_whose_commits_cannot_be_snapshotted_escalates_instead_of_requeueing(sandbox):
    (sandbox.project_root / "project.cfg").write_text(f"[project]\nproject_name = {_PROJECT}\n", encoding="utf-8")
    sandbox.block("per_task_timeout", ["worktree=gone", f"branch={_FEATURE}", "ahead=2", "timeout_s=1800"])

    sandbox.run_check()

    text = sandbox.text()
    assert _section(text, "State") == "BLOCKED" and _section(text, "Needs Human") == "yes"
    diagnosis = _section(text, "OVERWATCH Diagnosis")
    assert "could not be snapshotted" in diagnosis and "dev_tree_path" in diagnosis
    assert load_block_record(sandbox.status).attempts == 0
    assert not _section(sandbox.readme.read_text(encoding="utf-8"), "Triage Note")
    assert f"- [B] {_TASK}" in sandbox.queue.read_text(encoding="utf-8")


# ---------------------------------------------------------------------------
# 2. Provider refusal (AT3)
# ---------------------------------------------------------------------------

_AT_CAP = "\n## Zero Progress\nreason_code: clean_exit_no_progress\ncount: 2\nlimit: 3\ndetail: earlier cycles\n"


def _blocked_by_zero_progress_site(sb: Sandbox, tail: str, span_seconds: int) -> None:
    with sb.status.open("a", encoding="utf-8") as fh:
        fh.write(_AT_CAP)
    sb.write_dispatch(tail, span_seconds)
    sb.drive_zero_progress_site()
    sb.set_marker("B")


def test_short_span_model_not_found_is_a_provider_refusal_not_requeued_before_its_cool_down(sandbox):
    _blocked_by_zero_progress_site(sandbox, "Error: model not found: example-model-name\n", span_seconds=20)
    record = load_block_record(sandbox.status)
    assert record.reason_code == "provider_refusal", sandbox.text()
    assert "signature=model_not_found" in record.evidence and "span_s=20" in record.evidence

    sandbox.run_check()
    record = load_block_record(sandbox.status)
    cooldown_until = datetime.datetime.fromisoformat(record.cooldown_until)
    assert record.attempts == 0 and _section(sandbox.text(), "State") == "BLOCKED"
    assert "block-triage:provider_refusal:not_yet" in sandbox.actions

    # Up to cooldown_until nothing is requeued; from it, the requeue is due.
    just_before = cooldown_until - datetime.timedelta(seconds=1)
    assert bt.plan_task(sandbox.status, now=just_before).outcome == bt.OUTCOME_SKIP
    assert bt.plan_task(sandbox.status, now=cooldown_until).outcome == bt.OUTCOME_ACT
    result = sandbox.run_check()
    assert _section(sandbox.text(), "State") == "BLOCKED" and "cool-down" in result.stderr
    assert not _section(sandbox.readme.read_text(encoding="utf-8"), "Triage Note")

    # Once the window has passed, the requeue lands and resets the count.
    update_block_record(sandbox.status, cooldown_until="2026-01-01T00:00:00+00:00")
    sandbox.run_check()
    text = sandbox.text()
    assert _section(text, "State") == "BACKLOG", text
    assert re.search(r"^count:\s*0$", _section(text, "Zero Progress"), re.M), _section(text, "Zero Progress")
    note = _section(sandbox.readme.read_text(encoding="utf-8"), "Triage Note")
    assert "provider_refusal: requeue_owner, attempt 1 of 3" in note and "signature=model_not_found" in note
    assert "Zero Progress count reset from 3 to 0" in note
    assert "block-triage:provider_refusal:requeue_owner" in sandbox.actions


def test_provider_refusal_is_retried_with_a_growing_window_then_escalates_naming_a_fleet_cause(sandbox):
    t0 = datetime.datetime(2026, 1, 1, 12, 0, 0, tzinfo=datetime.timezone.utc)
    evidence = ["worktree=gone", "branch=none", "span_s=12", "signature=auth"]
    zero_progress_at_cap = "reason_code: clean_exit_no_progress\ncount: 3\nlimit: 3\ndetail: refused"
    sandbox.set_sections(Zero_Progress=zero_progress_at_cap)
    sandbox.block("provider_refusal", evidence, now=t0)

    def apply(at: datetime.datetime) -> bt.ApplyResult:
        return bt.apply_task(sandbox.status, sandbox.queue, project=_PROJECT, kanban_root=sandbox.root, now=at)

    assert apply(t0).outcome == bt.OUTCOME_RECORD
    windows = []
    moment = t0 + datetime.timedelta(minutes=15)
    for attempt in (1, 2, 3):
        result = apply(moment)
        assert result.outcome == bt.OUTCOME_ACT and result.mutation == bt.MUTATION_PROMOTE, result
        record = load_block_record(sandbox.status)
        assert record.attempts == attempt
        cooldown_until = datetime.datetime.fromisoformat(record.cooldown_until)
        windows.append(int((cooldown_until - moment).total_seconds() // 60))
        # The requeued attempt is refused again: back at the ceiling, re-blocked.
        sandbox.set_sections(Zero_Progress=zero_progress_at_cap)
        sandbox.block("provider_refusal", evidence, now=moment + datetime.timedelta(minutes=5))
        if attempt < 3:
            assert apply(cooldown_until - datetime.timedelta(seconds=1)).outcome == bt.OUTCOME_SKIP
        moment = cooldown_until
    # 15 minutes before the first attempt, then 30 and 60 between attempts.
    assert windows[:2] == [30, 60]

    # Still refusing after the third attempt: nothing is left to wait for.
    result = apply(moment - datetime.timedelta(minutes=50))
    assert result.outcome == bt.OUTCOME_ESCALATE
    text = sandbox.text()
    diagnosis = _section(text, "OVERWATCH Diagnosis")
    assert _section(text, "Needs Human") == "yes" and _section(text, "State") == "BLOCKED"
    assert "fleet-level" in diagnosis and "authentication" in diagnosis and "usage limit" in diagnosis
    assert "signature=auth" in diagnosis
    assert [s for _, _, s in _ledger_statuses(sandbox)] == ["failed", "failed", "failed"]
    assert apply(moment + datetime.timedelta(hours=1)).outcome == bt.OUTCOME_SKIP


# ---------------------------------------------------------------------------
# 3. Zero progress (AT3)
# ---------------------------------------------------------------------------


def _backdate_record(sb: Sandbox, hours: int = 1) -> None:
    """Move the record's timestamps into the past, as if blocked earlier."""
    record = load_block_record(sb.status)
    stamp = (datetime.datetime.fromisoformat(record.last_blocked_at) - datetime.timedelta(hours=hours)).isoformat()
    body = _section(sb.text(), "Block Record")
    body = re.sub(r"^(first|last)_blocked_at:.*$", lambda m: f"{m.group(1)}_blocked_at: {stamp}", body, flags=re.M)
    sb.set_sections(Block_Record=body)


def test_normal_span_zero_progress_gets_one_requeue_then_escalates(sandbox):
    _blocked_by_zero_progress_site(sandbox, "agent read files\nagent ended its turn\n", span_seconds=900)
    assert load_block_record(sandbox.status).reason_code == "zero_progress", sandbox.text()
    _backdate_record(sandbox)

    sandbox.run_check()
    text = sandbox.text()
    assert _section(text, "State") == "BACKLOG"
    note = _section(sandbox.readme.read_text(encoding="utf-8"), "Triage Note")
    assert "zero_progress: requeue_owner, attempt 1 of 1" in note and "without forward progress" in note

    # The requeued attempt makes no progress either: the real site advances
    # the count past the ceiling and blocks again.
    sandbox.set_sections(State="WORKING")
    sandbox.drive_zero_progress_site()
    sandbox.set_marker("B")
    sandbox.run_check()

    text = sandbox.text()
    assert _section(text, "State") == "BLOCKED" and _section(text, "Needs Human") == "yes"
    diagnosis = _section(text, "OVERWATCH Diagnosis")
    assert "zero_progress_not_readvanced failed" in diagnosis and "re-advanced from 3 to 4" in diagnosis
    assert _section(sandbox.readme.read_text(encoding="utf-8"), "Triage Note") == note, "no second requeue"
    assert f"- [B] {_TASK}" in sandbox.queue.read_text(encoding="utf-8")


# ---------------------------------------------------------------------------
# 4. Block epochs
# ---------------------------------------------------------------------------

_T0 = datetime.datetime(2026, 1, 1, 12, 0, 0, tzinfo=datetime.timezone.utc)
_STUB_HANDLERS = {"requeue_owner": lambda ctx: bt.HandlerResult()}


def _apply(sb: Sandbox, at: datetime.datetime) -> bt.ApplyResult:
    return bt.apply_task(sb.status, sb.queue, project=_PROJECT, kanban_root=sb.root, now=at, handlers=_STUB_HANDLERS)


def test_requeue_followed_by_a_different_block_passes_verification_and_restarts_the_epoch(sandbox):
    sandbox.block("per_task_timeout", ["worktree=gone", "timeout_s=60"], now=_T0)
    assert _apply(sandbox, _T0).outcome == bt.OUTCOME_ACT
    reblocked = _T0 + datetime.timedelta(hours=1)
    sandbox.block("zero_progress", ["worktree=gone"], now=reblocked)

    result = _apply(sandbox, reblocked + datetime.timedelta(minutes=1))

    assert result.outcome == bt.OUTCOME_ACT and result.reason_code == "zero_progress", result
    record = load_block_record(sandbox.status)
    assert record.first_blocked_at == record.last_blocked_at == reblocked.isoformat()
    assert record.attempts == 1, "the new epoch's first attempt, not the old epoch's second"
    assert _ledger_statuses(sandbox) == [("per_task_timeout", 1, "passed"), ("zero_progress", 1, "pending")]
    evidence = json.loads(sorted(sandbox.evidence_dir.glob("triage-*-act*.json"))[-1].read_text(encoding="utf-8"))
    assert evidence["epoch_restarted"] is True and evidence["verification"]["status"] == "passed"


def test_block_already_acted_on_is_never_acted_on_again(sandbox):
    sandbox.block("zero_progress", ["worktree=gone"], now=_T0)
    assert _apply(sandbox, _T0).outcome == bt.OUTCOME_ACT
    # The requeue never landed: still BLOCKED on the same block occurrence.
    result = _apply(sandbox, _T0 + datetime.timedelta(hours=2))

    assert result.outcome == bt.OUTCOME_ESCALATE
    diagnosis = _section(sandbox.text(), "OVERWATCH Diagnosis")
    assert "requeue did not land" in diagnosis and "never acted on twice" in diagnosis
    assert load_block_record(sandbox.status).attempts == 1
    assert _ledger_statuses(sandbox) == [("zero_progress", 1, "failed")]


# ---------------------------------------------------------------------------
# 5. Transient blocks
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(("count", "outcome"), [(1, bt.OUTCOME_SKIP), (2, bt.OUTCOME_SKIP), (3, bt.OUTCOME_ESCALATE)])
def test_transient_block_is_left_to_its_handler_below_the_ceiling_and_escalates_at_it(sandbox, count, outcome):
    sandbox.set_sections(Transient_Retry=(
        f"reason_code: transient_api_error\nretry_count: {count}\nretry_limit: 3\nprovider: claude\n"
        "last_error: overloaded"))
    sandbox.block("transient_api_error", ["batch_log=none"], now=_T0, needs_human="no" if count < 3 else "yes")

    result = bt.apply_task(sandbox.status, sandbox.queue, project=_PROJECT, kanban_root=sandbox.root,
                           now=_T0 + datetime.timedelta(hours=1))

    assert result.outcome == outcome and result.mutation == bt.MUTATION_NONE
    if outcome == bt.OUTCOME_SKIP:
        assert "left to the handler" in result.detail and f"transient retry {count} of 3" in result.detail
        assert not sandbox.evidence_dir.exists()
    else:
        diagnosis = _section(sandbox.text(), "OVERWATCH Diagnosis")
        assert "retry ceiling (3 of 3" in diagnosis and "sustained outage" in diagnosis
    assert _section(sandbox.text(), "State") == "BLOCKED"


# ---------------------------------------------------------------------------
# 6. The read-only git runner
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("subcommand", ["commit", "checkout", "merge", "push", "branch", "reset", "worktree",
                                        "switch", "rebase", "update-ref", "fetch", "gc"])
def test_read_only_git_runner_refuses_a_mutating_subcommand_before_running_it(sandbox, subcommand):
    sandbox.make_repo(feature_commits=1)
    refs_before = _git(sandbox.repo, "for-each-ref")
    with pytest.raises(ReadOnlyGitRefused, match=re.escape(subcommand)):
        run_readonly_git(sandbox.repo, subcommand, "-D", _FEATURE)
    assert _git(sandbox.repo, "for-each-ref") == refs_before


def test_read_only_git_runner_reads_and_reports_a_missing_repository_as_none(sandbox):
    sandbox.make_repo(feature_commits=2)
    counted = run_readonly_git(sandbox.repo, "rev-list", "--count", f"refs/heads/{_RC}..refs/heads/{_FEATURE}")
    assert counted is not None and counted.ok and counted.stdout.strip() == "2"
    assert run_readonly_git(sandbox.repo.parent / "no-such-repo", "rev-parse", "HEAD") is None
