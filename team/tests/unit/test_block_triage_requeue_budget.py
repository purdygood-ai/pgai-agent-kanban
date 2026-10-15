"""
test_block_triage_requeue_budget.py
===================================
OVERWATCH block triage bounds how many times one task is requeued over the
life of its Block Record (``[overwatch] requeue_budget``), so a task whose
blocks alternate between reasons — each block passing the previous
requeue's verification and restarting the block epoch — reaches a human
instead of being requeued forever
(``pgai_agent_kanban.overwatch.block_triage``, ``block_requeue`` and
``block_record``).

Covered:
  1. Alternating reasons: per_task_timeout / gate_dirty_worktree, and
     zero_progress / per_task_timeout, are requeued exactly the budget
     number of times across epoch restarts, then escalated (State stays
     BLOCKED, Needs Human yes, an OVERWATCH Diagnosis); the cause names
     every reason code seen and the alternation; a later sweep leaves the
     escalated epoch alone.
  2. The budget is configurable: project.cfg wins over kanban.cfg, a blank
     value falls through, an inline comment is ignored, 0 requeues nothing.
  3. Recovery: a task that reaches DONE (the status-writer removes its
     Block Record) before the budget is spent starts its next block with a
     fresh count and is requeued as usual.
  4. A single-reason requeue still follows its row's max_attempts: a
     provider refusal gets its three attempts under the default budget and
     escalates naming its own fleet-level cause, not the budget.
  5. recompute_gate closes a task rather than requeueing it: it is never
     refused by a spent budget and never counted.
  6. The Block Record keeps requeues / requeue_reasons across re-blocks and
     epoch restarts, reads a record written without them as 0 / none, and
     its update entry point (library and bash wrapper) records a requeue.
  7. An invalid budget refuses the CLI dual-channel before anything is
     planned or written, and the printed remedy, run as printed, fixes it.

Every fixture builds a sandbox kanban root under pytest's tmp_path; CLI runs
use a scrubbed environment whose kanban root is the sandbox. Nothing reads or
writes the live install, a real repository or a real provider: requeue
handlers are stubbed where the real one would need git.
"""

from __future__ import annotations

import datetime
import pathlib
import shlex
import subprocess
from typing import Optional

import pytest

from pgai_agent_kanban.overwatch import block_triage as bt
from pgai_agent_kanban.overwatch.block_record import (
    load_block_record,
    read_block_record,
    update_block_record,
    write_block_record,
)
from pgai_agent_kanban.overwatch.block_requeue import (
    DEFAULT_REQUEUE_BUDGET,
    RequeueBudgetError,
    read_ledger,
    resolve_requeue_budget,
)
from pgai_agent_kanban.task_status import read_section, set_or_append_section, write_task_status
from tests.fixtures.scrubbed_subprocess_env import build_scrubbed_env

# This file lives at team/tests/unit/; team/ is three parents up.
_TEAM_DIR = pathlib.Path(__file__).resolve().parent.parent.parent
_BLOCK_RECORD_LIB = _TEAM_DIR / "scripts" / "lib" / "block_record.sh"

_PROJECT = "budget-proj"
_TASK = "CODER-20260101-001-example-task"
_T0 = datetime.datetime(2026, 1, 1, 12, 0, 0, tzinfo=datetime.timezone.utc)

# Every requeue handler stubbed: the plan, the Block Record, the ledger and
# the Triage Note are exercised for real, without a repository to snapshot.
_STUB_HANDLERS = {"requeue_owner": lambda ctx: bt.HandlerResult()}


def _section(text: str, heading: str) -> str:
    return (read_section(text, heading) or "").strip()


class Sandbox:
    """A throwaway kanban root with one project and one task."""

    def __init__(self, tmp_path: pathlib.Path) -> None:
        self.tmp = tmp_path
        self.root = tmp_path / "kanban"
        self.project_root = self.root / "projects" / _PROJECT
        self.task_dir = self.project_root / "tasks" / _TASK
        self.queue = self.project_root / "tasks" / "queues" / "coder_backlog.md"
        for d in (self.task_dir / "artifacts", self.task_dir / "logs", self.queue.parent, self.root / "tmp",
                  tmp_path / "home"):
            d.mkdir(parents=True, exist_ok=True)
        (self.project_root / "project.cfg").write_text(f"[project]\nproject_name = {_PROJECT}\n", encoding="utf-8")
        (self.root / "projects.cfg").write_text(f"[project:{_PROJECT}]\npriority = 1\n", encoding="utf-8")
        (self.root / "kanban.cfg").write_text("[wake]\n", encoding="utf-8")
        self.readme.write_text(
            f"# Task\n\n## Task ID\n{_TASK}\n\n## Role\nCODER\n\n## Source Branch\nai_rc/v9.9.9\n\n"
            "## Goal\nDo the thing.\n",
            encoding="utf-8",
        )
        self.status.write_text(
            f"# Status\n\n## Task\n{_TASK}\n\n## Role\nCODER\n\n## State\nWORKING\n\n## Summary\nIn progress.\n\n"
            "## Blockers\nnone\n\n## Needs Human\nno\n",
            encoding="utf-8",
        )
        self.queue.write_text(f"# CODER Backlog\n\n- [B] {_TASK} — example\n", encoding="utf-8")

    @property
    def status(self) -> pathlib.Path:
        return self.task_dir / "status.md"

    @property
    def readme(self) -> pathlib.Path:
        return self.task_dir / "README.md"

    @property
    def evidence_dir(self) -> pathlib.Path:
        return self.task_dir / "artifacts" / "block-evidence"

    def text(self) -> str:
        return self.status.read_text(encoding="utf-8")

    def set_sections(self, **sections: str) -> None:
        text = self.text()
        for heading, body in sections.items():
            text = set_or_append_section(text, heading.replace("_", " "), body)
        self.status.write_text(text, encoding="utf-8")

    def block(self, reason_code: str, at: datetime.datetime, evidence: Optional[list] = None) -> None:
        """Block the task the way a block site does (a re-block keeps the record's triage fields)."""
        self.set_sections(State="BLOCKED", Needs_Human="yes", Blockers=f"{reason_code} block")
        write_block_record(self.status, reason_code, "CODER", evidence or ["worktree=gone"], now=at)

    def requeued(self) -> None:
        """What the check does after an ``act`` / ``promote``: the task goes back to its owner."""
        self.set_sections(State="BACKLOG", Needs_Human="no")

    def apply(self, at: datetime.datetime, **kwargs) -> bt.ApplyResult:
        kwargs.setdefault("handlers", _STUB_HANDLERS)
        return bt.apply_task(self.status, self.queue, project=_PROJECT, kanban_root=self.root, now=at, **kwargs)

    def triage_notes(self) -> list:
        return [line for line in _section(self.readme.read_text(encoding="utf-8"), "Triage Note").splitlines() if line]

    def cli(self, *args: str) -> subprocess.CompletedProcess:
        """Run the block_triage CLI with the sandbox as the only kanban root it can see."""
        env = build_scrubbed_env(
            home_dir=self.tmp / "home",
            extra_env={
                "KANBAN_ROOT": str(self.root),
                "PGAI_AGENT_KANBAN_ROOT_PATH": str(self.root),
                "PGAI_AGENT_KANBAN_TEMP_DIR": str(self.root / "tmp"),
            },
        )
        return subprocess.run(
            ["python3", "-m", "pgai_agent_kanban.overwatch.block_triage", *args,
             "--kanban-root", str(self.root), "--project", _PROJECT],
            capture_output=True, text=True, timeout=120, env=env, cwd=str(_TEAM_DIR),
        )


@pytest.fixture
def sandbox(tmp_path: pathlib.Path) -> Sandbox:
    return Sandbox(tmp_path)


def _drive(sb: Sandbox, codes: list, **apply_kwargs) -> list:
    """Block the task once per code, an hour apart, applying triage after
    each block and requeueing the task whenever triage acted; return the
    outcomes, stopping at the first that does not act."""
    outcomes = []
    for n, code in enumerate(codes):
        at = _T0 + datetime.timedelta(hours=n)
        sb.block(code, at)
        result = sb.apply(at + datetime.timedelta(minutes=1), **apply_kwargs)
        outcomes.append(result.outcome)
        if result.outcome != bt.OUTCOME_ACT:
            break
        assert result.mutation == bt.MUTATION_PROMOTE, result
        sb.requeued()
    return outcomes


# ---------------------------------------------------------------------------
# 1. Alternating reasons
# ---------------------------------------------------------------------------

_TIMEOUT_DIRTY = ["per_task_timeout", "gate_dirty_worktree"] * 4
_ZERO_TIMEOUT = ["zero_progress", "per_task_timeout"] * 4


def test_alternating_timeout_and_dirty_worktree_is_requeued_up_to_the_budget_then_escalated(sandbox):
    outcomes = _drive(sandbox, _TIMEOUT_DIRTY)

    assert outcomes == [bt.OUTCOME_ACT] * DEFAULT_REQUEUE_BUDGET + [bt.OUTCOME_ESCALATE], outcomes
    text = sandbox.text()
    assert _section(text, "State") == "BLOCKED" and _section(text, "Needs Human") == "yes", text
    record = load_block_record(sandbox.status)
    assert record.requeues == DEFAULT_REQUEUE_BUDGET
    assert record.requeue_reason_list() == ["per_task_timeout", "gate_dirty_worktree", "per_task_timeout"]
    assert len(sandbox.triage_notes()) == DEFAULT_REQUEUE_BUDGET, "no requeue after the budget is spent"

    # Every requeue after the first passed verification and restarted the epoch.
    assert [(e.reason_code, e.status) for e in read_ledger(sandbox.evidence_dir) if e is not None] == [
        ("per_task_timeout", "passed"), ("gate_dirty_worktree", "passed"), ("per_task_timeout", "passed"),
    ]

    diagnosis = _section(text, "OVERWATCH Diagnosis")
    assert diagnosis.startswith(f"Block epoch {record.first_blocked_at}"), diagnosis
    assert "requeue budget is spent" in diagnosis and "requeue_budget = 3" in diagnosis
    assert "per_task_timeout -> gate_dirty_worktree -> per_task_timeout -> gate_dirty_worktree" in diagnosis
    assert "alternate between per_task_timeout, gate_dirty_worktree" in diagnosis
    assert "Options: (1) read the task README's ## Triage Note lines" in diagnosis
    assert "--state BACKLOG --needs-human no" in diagnosis

    # The escalated epoch is left alone by every later sweep.
    before = sandbox.text()
    later = sandbox.apply(_T0 + datetime.timedelta(days=1))
    assert later.outcome == bt.OUTCOME_SKIP and "already escalated" in later.detail
    assert sandbox.text() == before


def test_alternating_zero_progress_and_timeout_is_escalated_naming_both_reasons(sandbox):
    outcomes = _drive(sandbox, _ZERO_TIMEOUT)

    assert outcomes == [bt.OUTCOME_ACT] * DEFAULT_REQUEUE_BUDGET + [bt.OUTCOME_ESCALATE], outcomes
    diagnosis = _section(sandbox.text(), "OVERWATCH Diagnosis")
    assert "zero_progress -> per_task_timeout -> zero_progress -> per_task_timeout" in diagnosis
    assert "alternate between zero_progress, per_task_timeout" in diagnosis
    assert _section(sandbox.text(), "Needs Human") == "yes"


def test_budget_escalation_without_a_handler_run_takes_no_snapshot_or_requeue(sandbox):
    calls = []

    def counting(ctx):
        calls.append(ctx.record.reason_code)
        return bt.HandlerResult()

    _drive(sandbox, _TIMEOUT_DIRTY, handlers={"requeue_owner": counting})

    assert calls == ["per_task_timeout", "gate_dirty_worktree", "per_task_timeout"]


# ---------------------------------------------------------------------------
# 2. Configuration
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("kanban_cfg", "project_cfg", "budget"),
    [
        ("[overwatch]\nrequeue_budget = 2\n", "", 2),
        ("[overwatch]\nrequeue_budget = 5\n", "\n[overwatch]\nrequeue_budget = 1\n", 1),
        ("[overwatch]\nrequeue_budget = 2\n", "\n[overwatch]\nrequeue_budget =\n", 2),
        ("[overwatch]\nrequeue_budget = 4   # a little more room\n", "", 4),
        ("[overwatch]\nrequeue_budget = 0\n", "", 0),
        ("[overwatch]\nblock_triage = true\n", "", DEFAULT_REQUEUE_BUDGET),
    ],
)
def test_configured_budget_caps_the_alternating_requeues(sandbox, kanban_cfg, project_cfg, budget):
    (sandbox.root / "kanban.cfg").write_text(kanban_cfg, encoding="utf-8")
    with (sandbox.project_root / "project.cfg").open("a", encoding="utf-8") as fh:
        fh.write(project_cfg)
    assert resolve_requeue_budget(sandbox.root, _PROJECT) == budget

    outcomes = _drive(sandbox, _TIMEOUT_DIRTY)

    assert outcomes == [bt.OUTCOME_ACT] * budget + [bt.OUTCOME_ESCALATE], outcomes
    assert load_block_record(sandbox.status).requeues == budget
    assert f"requeue_budget = {budget}" in _section(sandbox.text(), "OVERWATCH Diagnosis")


def test_explicit_budget_argument_overrides_the_config(sandbox):
    (sandbox.root / "kanban.cfg").write_text("[overwatch]\nrequeue_budget = 1\n", encoding="utf-8")

    assert _drive(sandbox, _TIMEOUT_DIRTY, requeue_budget=2) == [bt.OUTCOME_ACT] * 2 + [bt.OUTCOME_ESCALATE]


# ---------------------------------------------------------------------------
# 3. Recovery
# ---------------------------------------------------------------------------


def test_task_that_reaches_done_before_the_budget_starts_its_next_block_with_a_fresh_count(sandbox):
    assert _drive(sandbox, _TIMEOUT_DIRTY[:2]) == [bt.OUTCOME_ACT, bt.OUTCOME_ACT]
    assert load_block_record(sandbox.status).requeues == 2

    # The requeued attempt finishes: the status-writer's DONE removes the record.
    write_task_status(sandbox.status, state="DONE", needs_human="no", summary="Finished.")
    assert load_block_record(sandbox.status) is None
    assert bt.plan_task(sandbox.status, now=_T0 + datetime.timedelta(days=1)).candidate is False

    # Returned to work later and blocked again: a new record, a full budget.
    sandbox.set_sections(State="WORKING")
    outcomes = _drive(sandbox, _TIMEOUT_DIRTY)
    assert outcomes == [bt.OUTCOME_ACT] * DEFAULT_REQUEUE_BUDGET + [bt.OUTCOME_ESCALATE], outcomes


# ---------------------------------------------------------------------------
# 4. A single reason still follows its row's max_attempts
# ---------------------------------------------------------------------------


def test_single_reason_provider_refusal_keeps_its_three_attempts_and_its_own_cause(sandbox):
    at_ceiling = "reason_code: clean_exit_no_progress\ncount: 3\nlimit: 3\ndetail: refused"
    evidence = ["worktree=gone", "span_s=12", "signature=auth"]
    sandbox.set_sections(Zero_Progress=at_ceiling)
    sandbox.block("provider_refusal", _T0, evidence)
    # The real provider-refusal handler: it needs no repository.
    real = {"handlers": None}

    assert sandbox.apply(_T0, **real).outcome == bt.OUTCOME_RECORD
    moment = _T0 + datetime.timedelta(minutes=15)
    for attempt in (1, 2, 3):
        result = sandbox.apply(moment, **real)
        assert result.outcome == bt.OUTCOME_ACT, result
        record = load_block_record(sandbox.status)
        assert (record.attempts, record.requeues) == (attempt, attempt)
        sandbox.set_sections(Zero_Progress=at_ceiling)
        sandbox.block("provider_refusal", moment + datetime.timedelta(minutes=5), evidence)
        moment = datetime.datetime.fromisoformat(load_block_record(sandbox.status).cooldown_until)

    assert sandbox.apply(moment, **real).outcome == bt.OUTCOME_ESCALATE
    diagnosis = _section(sandbox.text(), "OVERWATCH Diagnosis")
    assert "fleet-level" in diagnosis and "requeue budget" not in diagnosis


# ---------------------------------------------------------------------------
# 5. recompute_gate is not a requeue
# ---------------------------------------------------------------------------


def test_recomputed_gate_is_not_refused_by_a_spent_budget_and_is_not_counted(sandbox, monkeypatch):
    monkeypatch.setattr(bt, "gate_merge_facts", lambda task_dir, record: {"branch_ancestor_of_source": True})
    handlers = {"recompute_gate": lambda ctx: bt.HandlerResult(mutation=f"{bt.MUTATION_TRANSITION_PREFIX}DONE")}
    sandbox.block("gate_merge_not_found", _T0)
    for code in ("per_task_timeout", "zero_progress", "gate_dirty_worktree"):
        update_block_record(sandbox.status, record_requeue=code)

    result = sandbox.apply(_T0 + datetime.timedelta(minutes=1), handlers=handlers)

    assert result.outcome == bt.OUTCOME_ACT and result.mutation == "transition:DONE", result
    assert load_block_record(sandbox.status).requeues == DEFAULT_REQUEUE_BUDGET


# ---------------------------------------------------------------------------
# 6. The Block Record fields
# ---------------------------------------------------------------------------

_OLD_RECORD = (
    "\n## Block Record\nreason_code: zero_progress\nowner_role: CODER\nattempts: 1\n"
    "first_blocked_at: 2026-01-01T10:00:00+00:00\nlast_blocked_at: 2026-01-01T11:00:00+00:00\n"
    "cooldown_until: none\nevidence: worktree=gone\nresume: fresh\n"
)


def test_record_written_without_the_requeue_fields_reads_as_none_spent_and_gains_them_on_its_next_write(sandbox):
    sandbox.set_sections(State="BLOCKED")
    with sandbox.status.open("a", encoding="utf-8") as fh:
        fh.write(_OLD_RECORD)

    record = read_block_record(sandbox.text())
    assert (record.requeues, record.requeue_reasons, record.attempts) == (0, "none", 1)

    update_block_record(sandbox.status, record_requeue="zero-progress")
    body = _section(sandbox.text(), "Block Record")
    assert body.splitlines()[-2:] == ["requeues: 1", "requeue_reasons: zero_progress"], body


def test_requeue_fields_survive_a_reblock_and_an_epoch_restart(sandbox):
    sandbox.block("per_task_timeout", _T0)
    update_block_record(sandbox.status, record_requeue="per_task_timeout", bump_attempts=True)
    sandbox.block("gate_dirty_worktree", _T0 + datetime.timedelta(hours=1))
    reblocked = load_block_record(sandbox.status)
    assert (reblocked.requeues, reblocked.requeue_reasons) == (1, "per_task_timeout")

    restarted = update_block_record(sandbox.status, restart_epoch=True, record_requeue="gate_dirty_worktree")

    assert (restarted.attempts, restarted.first_blocked_at) == (0, reblocked.last_blocked_at)
    assert (restarted.requeues, restarted.requeue_reasons) == (2, "per_task_timeout, gate_dirty_worktree")
    assert load_block_record(sandbox.status) == restarted


def test_hand_edited_requeue_reasons_read_liberally_and_write_canonically(sandbox):
    sandbox.set_sections(State="BLOCKED")
    with sandbox.status.open("a", encoding="utf-8") as fh:
        fh.write(_OLD_RECORD + "Requeues : 2\nrequeue-reasons: Per-Task-Timeout;gate_dirty_worktree\n")

    record = load_block_record(sandbox.status)
    assert record.requeue_reason_list() == ["per_task_timeout", "gate_dirty_worktree"]
    assert record.requeue_reasons == "per_task_timeout, gate_dirty_worktree"


def test_recording_a_requeue_for_an_unknown_reason_code_is_refused_and_writes_nothing(sandbox):
    sandbox.block("zero_progress", _T0)
    before = sandbox.text()

    with pytest.raises(Exception) as info:
        update_block_record(sandbox.status, record_requeue="made_up_reason")

    assert getattr(info.value, "error_key", "") == "block-record.unknown-reason-code"
    assert sandbox.text() == before


def test_bash_wrapper_records_a_requeue_and_reads_the_new_fields(sandbox):
    sandbox.block("per_task_timeout", _T0)
    env = build_scrubbed_env(
        home_dir=sandbox.tmp / "home", extra_env={"PGAI_AGENT_KANBAN_TEMP_DIR": str(sandbox.root / "tmp")}
    )
    status = shlex.quote(str(sandbox.status))
    script = (
        f"set -uo pipefail\nsource {shlex.quote(str(_BLOCK_RECORD_LIB))}\n"
        f"block_record_update {status} --record-requeue per_task_timeout || exit 9\n"
        f"echo \"N=[$(block_record_field {status} requeues)] R=[$(block_record_field {status} requeue_reasons)]\"\n"
    )
    result = subprocess.run(["bash", "-c", script], capture_output=True, text=True, timeout=60, env=env,
                            cwd=str(sandbox.tmp))

    assert "N=[1] R=[per_task_timeout]" in result.stdout, f"stdout={result.stdout}\nstderr={result.stderr}"


# ---------------------------------------------------------------------------
# 7. An invalid budget refuses loud
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("value", ["three", "-1", "2.5", "3s"])
def test_invalid_budget_refuses_the_cli_dual_channel_before_anything_is_planned(sandbox, value):
    (sandbox.root / "kanban.cfg").write_text(f"[overwatch]\nrequeue_budget = {value}\n", encoding="utf-8")
    sandbox.block("per_task_timeout", _T0)
    before = sandbox.text()

    with pytest.raises(RequeueBudgetError):
        resolve_requeue_budget(sandbox.root, _PROJECT)
    for command in (["plan"], ["apply", "--task-id", _TASK, "--queue-file", str(sandbox.queue)]):
        result = sandbox.cli(*command)
        assert result.returncode == 1, f"stdout={result.stdout}\nstderr={result.stderr}"
        assert result.stdout == ""
        assert "block-triage.bad-requeue-budget" in result.stderr
        assert repr(value) in result.stderr and "kanban.cfg" in result.stderr and "remedy:" in result.stderr
    assert sandbox.text() == before and not sandbox.evidence_dir.exists()


def test_invalid_budget_remedy_runs_as_printed_and_fixes_the_value(sandbox):
    cfg = sandbox.project_root / "project.cfg"
    cfg.write_text(cfg.read_text(encoding="utf-8") + "\n[overwatch]\nrequeue_budget = lots\n", encoding="utf-8")
    with pytest.raises(RequeueBudgetError) as info:
        resolve_requeue_budget(sandbox.root, _PROJECT)
    assert str(cfg) in info.value.message

    done = subprocess.run(["bash", "-c", info.value.remedy], capture_output=True, text=True, timeout=30,
                          env=build_scrubbed_env(home_dir=sandbox.tmp / "home"))

    assert done.returncode == 0, done.stderr
    assert resolve_requeue_budget(sandbox.root, _PROJECT) == DEFAULT_REQUEUE_BUDGET
    assert "project_name = budget-proj" in cfg.read_text(encoding="utf-8")


def test_cli_plan_reports_the_budget_escalation(sandbox):
    (sandbox.root / "kanban.cfg").write_text("[overwatch]\nrequeue_budget = 0\n", encoding="utf-8")
    sandbox.block("zero_progress", _T0)

    result = sandbox.cli("plan", "--now", (_T0 + datetime.timedelta(hours=1)).isoformat())

    assert result.returncode == 0, result.stderr
    fields = result.stdout.rstrip("\n").split("\t")
    assert fields[0] == _TASK and fields[2:5] == ["escalate", "zero_progress", "escalate"], fields
    assert "requeue budget is spent" in fields[5]
