"""
test_overwatch_block_triage.py
==============================
Behaviour of OVERWATCH block triage: the sweep check
``scripts/lib/overwatch-checks/check-block-triage.sh`` and the executor it
drives, ``pgai_agent_kanban.overwatch.block_triage``.

Covered:
  1. Always-human and unclassifiable blocks escalate and stay BLOCKED:
     missing README, invalid role / effort / state, plugin load failure,
     merge evidence not found, an unknown reason_code and an absent one each
     end with ``Needs Human: yes`` and a one-paragraph ``## OVERWATCH
     Diagnosis`` naming the epoch, the cause, evidence pointers and options;
     the evidence file, both backups and the ``block-triage:<code>:escalate``
     row exist; README and queue are untouched.
  2. An escalated epoch is left alone on the next sweep; a diagnosis naming
     an older epoch is replaced.
  3. Safety refusals change nothing: HALT_OVERWATCH (flag file and env var)
     and a held repo lock (the log line names the holder).
  4. ``[overwatch] block_triage = false``: escalations and Block Record
     writes still happen; no state, queue or README write does.
  5. Fail closed for actions the executor does not perform (an empty
     handler map): they escalate when Needs Human is yes; a transient block
     below its retry
     ceiling is left to the transient-error handler; a cool-down window
     leaves the task alone.
  6. The action envelope (a handler registered in-process): Triage Note,
     attempts +1, cooldown_until and resume set, evidence written; a handler
     that refuses escalates instead.
  7. The check's mutation step goes through promote_blocked_to_backlog and
     transition_task_state, with both backups and the action-log row.
  8. ``--dry-run`` writes no task file, backup or evidence; it logs what it
     would do.
  9. ``overwatch-sweep.sh`` discovers and runs the check; the overwatch wake
     hook runs it only for the overwatch agent.
 10. ``check-blocked-tasks.sh`` no longer promotes an "Active RC" block.

Every fixture builds a sandbox kanban root under pytest's tmp_path; nothing
reads or writes the live install or a real provider.
"""

from __future__ import annotations

import datetime
import json
import pathlib
import re
import subprocess
import textwrap
import time

import pytest

from pgai_agent_kanban.overwatch import block_triage as bt
from pgai_agent_kanban.overwatch.block_record import load_block_record, write_block_record
from tests.unit.shell_harness import run_bash

_CHECK = "scripts/lib/overwatch-checks/check-block-triage.sh"
_CHECK_BLOCKED = "scripts/lib/overwatch-checks/check-blocked-tasks.sh"
_SWEEP = "scripts/overwatch-sweep.sh"
_WAKE_PROJECT = "scripts/lib/wake_project.sh"
_LOCK_PROVENANCE = "scripts/lib/lock_provenance.sh"

_PROJECT = "triage-proj"
_TASK = "CODER-20260101-001-example-task"
_EPOCH = "2026-01-01T10:00:00+00:00"
_EVIDENCE = "worktree=gone; branch=ai_feature/example; ahead=2; merged=no"
_README = "# Task: example\n\n## Role\nCODER\n\n## Goal\nDo the thing.\n"


# ---------------------------------------------------------------------------
# Sandbox builders
# ---------------------------------------------------------------------------


def _record(reason_code: str = "missing_readme", *, attempts: int = 0, cooldown: str = "none",
            first: str = _EPOCH, evidence: str = _EVIDENCE, owner: str = "CODER") -> str:
    return (
        f"reason_code: {reason_code}\nowner_role: {owner}\nattempts: {attempts}\n"
        f"first_blocked_at: {first}\nlast_blocked_at: {first}\ncooldown_until: {cooldown}\n"
        f"evidence: {evidence}\nresume: fresh"
    )


def _status(record: str, *, needs_human: str = "no", state: str = "BLOCKED", extra: str = "") -> str:
    return (
        "# Status\n\n## Task\n" + _TASK + "\n\n## State\n" + state + "\n\n## Summary\nBlocked.\n\n"
        "## Blockers\nsomething blocked\n\n## Needs Human\n" + needs_human + "\n\n" + extra
        + "## Block Record\n" + record + "\n"
    )


class Sandbox:
    """A throwaway kanban root with one project and one blocked task."""

    def __init__(self, root: pathlib.Path, *, block_triage: "str | None" = None) -> None:
        self.root = root
        self.project_root = root / "projects" / _PROJECT
        self.task_dir = self.project_root / "tasks" / _TASK
        self.queue = self.project_root / "tasks" / "queues" / "coder_backlog.md"
        (self.project_root / "overwatch" / "backups").mkdir(parents=True)
        (self.project_root / "overwatch" / "actions.log").write_text("", encoding="utf-8")
        (self.task_dir / "artifacts").mkdir(parents=True)
        (self.task_dir / "logs").mkdir()
        self.queue.parent.mkdir(parents=True)
        (root / "locks").mkdir()
        (root / "logs").mkdir()
        (self.project_root / "project.cfg").write_text(
            f"[project]\nproject_name = {_PROJECT}\n", encoding="utf-8"
        )
        (root / "projects.cfg").write_text(f"[project:{_PROJECT}]\npriority = 1\n", encoding="utf-8")
        cfg = "[wake]\n"
        if block_triage is not None:
            cfg += f"\n[overwatch]\nblock_triage = {block_triage}\n"
        (root / "kanban.cfg").write_text(cfg, encoding="utf-8")

    def pin_dev_tree(self) -> pathlib.Path:
        """Give the project its own throwaway dev tree and local-only push
        posture, so every check a full sweep runs resolves the sandbox's repo
        from the sandbox's own project.cfg and never pushes. The repo has one
        commit and no remote. Returns the dev tree path."""
        dev_tree = self.root / "dev-tree"
        git = ["git", "-c", "user.name=sandbox", "-c", "user.email=sandbox@example.invalid"]
        subprocess.run([*git, "init", "-q", "-b", "main", str(dev_tree)], check=True, capture_output=True)
        subprocess.run([*git, "-C", str(dev_tree), "commit", "-q", "--allow-empty", "-m", "initial"],
                       check=True, capture_output=True)
        cfg = self.project_root / "project.cfg"
        cfg.write_text(
            cfg.read_text(encoding="utf-8") + f"dev_tree_path = {dev_tree}\npush_to_remote = false\n",
            encoding="utf-8",
        )
        return dev_tree

    @property
    def status(self) -> pathlib.Path:
        return self.task_dir / "status.md"

    @property
    def readme(self) -> pathlib.Path:
        return self.task_dir / "README.md"

    @property
    def actions(self) -> str:
        return (self.project_root / "overwatch" / "actions.log").read_text(encoding="utf-8")

    def seed(self, status_text: str, *, readme: "str | None" = _README, marker: str = "B") -> None:
        self.status.write_text(status_text, encoding="utf-8")
        if readme is not None:
            self.readme.write_text(readme, encoding="utf-8")
        self.queue.write_text(f"# CODER Backlog\n\n## Queue\n\n- [{marker}] {_TASK} — example\n", encoding="utf-8")

    def snapshot(self) -> dict:
        files = [self.status, self.queue]
        if self.readme.exists():
            files.append(self.readme)
        return {str(p): p.read_bytes() for p in files}

    def evidence_files(self) -> list:
        d = self.task_dir / "artifacts" / "block-evidence"
        return sorted(d.glob("*.json")) if d.is_dir() else []

    def backups(self) -> list:
        return sorted(p for p in (self.project_root / "overwatch" / "backups").rglob("*") if p.is_file())

    def env(self, **extra: str) -> dict:
        env = {
            "KANBAN_ROOT": str(self.root),
            "PGAI_AGENT_KANBAN_ROOT_PATH": str(self.root),
            "OVERWATCH_PROJECT": _PROJECT,
            "PGAI_AGENT_KANBAN_TEMP_DIR": str(self.root / "tmp"),
            "HALT_OVERWATCH": "",
        }
        env.update(extra)
        return env


@pytest.fixture
def sandbox(tmp_path: pathlib.Path) -> Sandbox:
    return Sandbox(tmp_path / "kanban")


def _run_check(sb: Sandbox, tmp_path: pathlib.Path, *args: str, **env: str):
    script = textwrap.dedent(f"""\
        set -euo pipefail
        source {_CHECK}
        rc=0
        overwatch_check_block_triage {' '.join(args)} || rc=$?
        echo "CHECK_EXIT=$rc"
    """)
    (sb.root / "tmp").mkdir(exist_ok=True)
    return run_bash(tmp_path, script, extra_env=sb.env(**env), timeout=120)


def _section(text: str, heading: str) -> str:
    m = re.search(rf"^## {re.escape(heading)}[ \t]*\n(.*?)(?=^## |\Z)", text, flags=re.M | re.S)
    return m.group(1).strip() if m else ""


def _assert_escalated(sb: Sandbox, code_token: str, result) -> str:
    assert "CHECK_EXIT=0" in result.stdout, result.stderr
    text = sb.status.read_text(encoding="utf-8")
    assert _section(text, "State") == "BLOCKED"
    assert _section(text, "Needs Human") == "yes"
    diagnosis = _section(text, "OVERWATCH Diagnosis")
    assert diagnosis, f"no OVERWATCH Diagnosis written:\n{text}\n{result.stderr}"
    assert "\n\n" not in diagnosis, "the diagnosis must be one paragraph"
    assert diagnosis.startswith(f"Block epoch {_EPOCH}")
    assert f"reason_code {code_token}" in diagnosis
    assert "Evidence:" in diagnosis and "block-evidence/triage-" in diagnosis
    assert "Options: (1)" in diagnosis and "--state BACKLOG --needs-human no" in diagnosis
    assert f"block-triage:{code_token}:escalate" in sb.actions
    assert len(sb.evidence_files()) == 1
    backup_names = {p.name for p in sb.backups()}
    assert {"status.md", "coder_backlog.md"} <= backup_names
    assert all(_TASK in str(p) for p in sb.backups()), "backups are kept per task"
    assert f"- [B] {_TASK}" in sb.queue.read_text(encoding="utf-8")
    return diagnosis


# ---------------------------------------------------------------------------
# 1. Always-human and unclassifiable blocks escalate and stay BLOCKED
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "reason_code, code_token, cause_fragment",
    [
        ("missing_readme", "missing_readme", "no README.md"),
        ("invalid_assignment", "invalid_assignment", "role or owner"),
        ("invalid_effort_config", "invalid_effort_config", "effort configuration"),
        ("invalid_state", "invalid_state", "six canonical states"),
        ("plugin_load_failed", "plugin_load_failed", "plugin"),
        ("gate_merge_evidence_not_found", "gate_merge_evidence_not_found", "merged"),
        ("not_a_known_code", "not_a_known_code", "has no row"),
        ("", "none", "no reason_code"),
    ],
)
def test_unresolvable_block_escalates_with_a_diagnosis_and_stays_blocked(
    sandbox, tmp_path, reason_code, code_token, cause_fragment
):
    readme = None if reason_code == "missing_readme" else _README
    sandbox.seed(_status(_record(reason_code)), readme=readme)
    before_readme = sandbox.readme.read_bytes() if readme else None
    result = _run_check(sandbox, tmp_path)
    diagnosis = _assert_escalated(sandbox, code_token, result)
    assert cause_fragment in diagnosis
    if readme:
        assert sandbox.readme.read_bytes() == before_readme, "an escalation never edits the README"
    record = load_block_record(sandbox.status)
    assert record is not None and record.attempts == 0, "escalation is not a triage attempt"
    evidence = json.loads(sandbox.evidence_files()[0].read_text(encoding="utf-8"))
    assert evidence["outcome"] == "escalate" and evidence["block_epoch"] == _EPOCH
    assert evidence["backups"]["status.md"].endswith(f"{_TASK}/status.md")


def test_unreadable_block_record_escalates(sandbox, tmp_path):
    sandbox.seed(_status(_record("missing_readme", first="not-a-time")))
    result = _run_check(sandbox, tmp_path)
    assert "CHECK_EXIT=0" in result.stdout, result.stderr
    text = sandbox.status.read_text(encoding="utf-8")
    assert _section(text, "Needs Human") == "yes"
    assert "cannot be read" in _section(text, "OVERWATCH Diagnosis")


# ---------------------------------------------------------------------------
# 2. Block epochs
# ---------------------------------------------------------------------------


def test_already_escalated_epoch_is_left_alone(sandbox, tmp_path):
    sandbox.seed(_status(_record("plugin_load_failed")))
    _run_check(sandbox, tmp_path)
    before = sandbox.snapshot()
    rows_before = sandbox.actions.count("block-triage:")
    result = _run_check(sandbox, tmp_path)
    assert "CHECK_EXIT=0" in result.stdout, result.stderr
    assert sandbox.snapshot() == before
    assert sandbox.actions.count("block-triage:") == rows_before
    assert len(sandbox.evidence_files()) == 1
    assert "already escalated" in result.stderr


def test_diagnosis_for_an_older_epoch_is_replaced(sandbox, tmp_path):
    old = "## OVERWATCH Diagnosis\nBlock epoch 2025-12-01T00:00:00+00:00 (reason_code x): old.\n\n"
    sandbox.seed(_status(_record("invalid_state"), needs_human="yes", extra=old))
    result = _run_check(sandbox, tmp_path)
    assert "CHECK_EXIT=0" in result.stdout, result.stderr
    diagnosis = _section(sandbox.status.read_text(encoding="utf-8"), "OVERWATCH Diagnosis")
    assert diagnosis.startswith(f"Block epoch {_EPOCH}")


# ---------------------------------------------------------------------------
# 3. Safety refusals change nothing
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("how", ["flag-file", "env-var"])
def test_halt_overwatch_changes_nothing_and_is_logged(sandbox, tmp_path, how):
    sandbox.seed(_status(_record("missing_readme")))
    before = sandbox.snapshot()
    env = {}
    if how == "flag-file":
        (sandbox.root / "HALT_OVERWATCH").write_text("", encoding="utf-8")
    else:
        env["HALT_OVERWATCH"] = "1"
    result = _run_check(sandbox, tmp_path, **env)
    assert "CHECK_EXIT=0" in result.stdout, result.stderr
    assert sandbox.snapshot() == before
    assert sandbox.evidence_files() == [] and sandbox.backups() == []
    assert "block-triage-refused" in sandbox.actions and "HALT_OVERWATCH" in sandbox.actions
    assert "block-triage:" not in sandbox.actions


def test_held_repo_lock_changes_nothing_and_names_the_holder(sandbox, tmp_path):
    sandbox.seed(_status(_record("missing_readme")))
    before = sandbox.snapshot()
    lock_file = sandbox.root / "locks" / f"repo-wake-{_PROJECT}.lock"
    ready, done = sandbox.root / "locks" / "holder-ready", sandbox.root / "locks" / "holder-done"
    holder_script = textwrap.dedent(f"""\
        source {_LOCK_PROVENANCE}
        fd=""
        pgai_lock_acquire "{lock_file}" coder "$$" "wake-chain:{_PROJECT}" none fd
        touch "{ready}"
        while [[ ! -f "{done}" ]]; do sleep 0.05; done
        pgai_lock_release "{lock_file}" "$fd"
    """)
    team = pathlib.Path(__file__).resolve().parents[2]
    holder = subprocess.Popen(["bash", "-c", holder_script], cwd=str(team))
    try:
        deadline = time.time() + 10
        while not ready.exists() and time.time() < deadline:
            time.sleep(0.05)
        assert ready.exists(), "the lock holder did not start"
        result = _run_check(sandbox, tmp_path)
    finally:
        done.touch()
        holder.wait(timeout=10)
    assert "CHECK_EXIT=0" in result.stdout, result.stderr
    assert sandbox.snapshot() == before
    assert sandbox.evidence_files() == [] and sandbox.backups() == []
    refusal = [line for line in sandbox.actions.splitlines() if "block-triage-refused" in line]
    assert refusal, sandbox.actions
    assert "repo lock held by holder=coder" in refusal[0] and f"wake-chain:{_PROJECT}" in refusal[0]


# ---------------------------------------------------------------------------
# 4. block_triage = false: records and diagnoses only
# ---------------------------------------------------------------------------


def test_block_triage_false_still_writes_the_diagnosis(tmp_path):
    sb = Sandbox(tmp_path / "kanban", block_triage="false")
    sb.seed(_status(_record("source_branch_missing")))
    before_readme, before_queue = sb.readme.read_bytes(), sb.queue.read_bytes()
    result = _run_check(sb, tmp_path)
    assert "CHECK_EXIT=0" in result.stdout, result.stderr
    text = sb.status.read_text(encoding="utf-8")
    assert _section(text, "State") == "BLOCKED"
    assert _section(text, "Needs Human") == "yes"
    assert _section(text, "OVERWATCH Diagnosis").startswith("Block epoch")
    assert sb.readme.read_bytes() == before_readme and sb.queue.read_bytes() == before_queue


def test_block_triage_false_still_records_a_first_cool_down(tmp_path):
    sb = Sandbox(tmp_path / "kanban", block_triage="false")
    sb.seed(_status(_record("provider_refusal"), needs_human="yes"))
    before_readme, before_queue = sb.readme.read_bytes(), sb.queue.read_bytes()
    result = _run_check(sb, tmp_path)
    assert "CHECK_EXIT=0" in result.stdout, result.stderr
    record = load_block_record(sb.status)
    assert record.cooldown_until != "none" and record.attempts == 0
    text = sb.status.read_text(encoding="utf-8")
    assert _section(text, "State") == "BLOCKED" and not _section(text, "OVERWATCH Diagnosis")
    assert sb.readme.read_bytes() == before_readme and sb.queue.read_bytes() == before_queue
    assert "block-triage:provider_refusal:not_yet" in sb.actions


def test_block_triage_false_skips_an_action_with_a_handler(sandbox):
    sandbox.seed(_status(_record("zero_progress"), needs_human="yes"))
    plan = bt.plan_task(sandbox.status, triage_enabled=False, handlers={"requeue_owner": lambda ctx: None})
    assert plan.outcome == bt.OUTCOME_SKIP and "block_triage is false" in plan.detail
    enabled = bt.plan_task(sandbox.status, triage_enabled=True, handlers={"requeue_owner": lambda ctx: None})
    assert enabled.outcome == bt.OUTCOME_ACT


# ---------------------------------------------------------------------------
# 5. Actions the executor does not perform yet fail closed
# ---------------------------------------------------------------------------


def test_unperformed_action_escalates_when_needs_human_is_yes(sandbox):
    # Every shipped action has a handler; an empty handler map stands in for
    # a table action this executor does not perform.
    sandbox.seed(_status(_record("zero_progress"), needs_human="yes"))
    result = bt.apply_task(sandbox.status, sandbox.queue, project=_PROJECT, kanban_root=sandbox.root, now=_NOW,
                           handlers={})
    assert result.outcome == bt.OUTCOME_ESCALATE and result.mutation == bt.MUTATION_NONE
    text = sandbox.status.read_text(encoding="utf-8")
    assert _section(text, "State") == "BLOCKED" and _section(text, "Needs Human") == "yes"
    diagnosis = _section(text, "OVERWATCH Diagnosis")
    assert "does not perform yet" in diagnosis and "requeue_owner" in diagnosis


def test_unperformed_action_is_left_to_its_handler_when_needs_human_is_no(sandbox, tmp_path):
    sandbox.seed(_status(_record("transient_api_error", cooldown="2026-01-01T00:00:00+00:00", attempts=1)))
    before = sandbox.snapshot()
    result = _run_check(sandbox, tmp_path)
    assert "CHECK_EXIT=0" in result.stdout, result.stderr
    assert sandbox.snapshot() == before
    assert "left to the handler" in result.stderr
    assert "block-triage:" not in sandbox.actions


def test_task_inside_its_cool_down_is_left_alone(sandbox, tmp_path):
    future = (datetime.datetime.now(datetime.timezone.utc) + datetime.timedelta(hours=1)).isoformat()
    sandbox.seed(_status(_record("zero_progress", cooldown=future), needs_human="yes"))
    before = sandbox.snapshot()
    result = _run_check(sandbox, tmp_path)
    assert "CHECK_EXIT=0" in result.stdout, result.stderr
    assert sandbox.snapshot() == before and sandbox.evidence_files() == []


def test_task_without_a_block_record_is_not_a_candidate(sandbox):
    sandbox.seed("# Status\n\n## State\nBLOCKED\n\n## Needs Human\nyes\n")
    assert bt.plan_project(sandbox.project_root) == []


# ---------------------------------------------------------------------------
# 6. The action envelope (in-process handler)
# ---------------------------------------------------------------------------

_NOW = datetime.datetime(2026, 1, 1, 12, 0, 0, tzinfo=datetime.timezone.utc)


def test_action_writes_note_bumps_attempts_and_sets_cool_down(sandbox):
    sandbox.seed(_status(_record("per_task_timeout", evidence=_EVIDENCE + "; timeout_s=3600"), needs_human="yes"))
    seen = {}

    def handler(ctx: bt.TriageContext) -> bt.HandlerResult:
        seen["evidence_dir_exists"] = ctx.evidence_dir.is_dir() and any(ctx.evidence_dir.iterdir())
        seen["status_untouched"] = "attempts: 0" in ctx.status_file.read_text(encoding="utf-8")
        return bt.HandlerResult(mutation=bt.MUTATION_PROMOTE, evidence=("artifacts/block-evidence/commits.txt",))

    result = bt.apply_task(
        sandbox.status, sandbox.queue, project=_PROJECT, kanban_root=sandbox.root, now=_NOW,
        backups={"status.md": "b/status.md", "queue": "b/coder_backlog.md"},
        handlers={"requeue_owner": handler},
    )
    assert result.outcome == bt.OUTCOME_ACT and result.mutation == bt.MUTATION_PROMOTE
    assert result.reason_code == "per_task_timeout" and result.action == "requeue_owner"
    assert seen == {"evidence_dir_exists": True, "status_untouched": True}, "evidence is written before any mutation"
    record = load_block_record(sandbox.status)
    assert record.attempts == 1
    assert record.cooldown_until == "2026-01-01T12:30:00+00:00"
    assert record.resume == "branch"
    note = _section(sandbox.readme.read_text(encoding="utf-8"), "Triage Note")
    assert "per_task_timeout: requeue_owner, attempt 1 of 1" in note
    assert "killed at 3600s" in note
    assert "commits.txt" in note
    assert "attempt 1" in result.detail
    # The state itself is the check's to change: still BLOCKED here.
    assert _section(sandbox.status.read_text(encoding="utf-8"), "State") == "BLOCKED"


def test_second_action_appends_to_the_triage_note(sandbox):
    sandbox.seed(_status(_record("provider_refusal", cooldown="2026-01-01T11:00:00+00:00"), needs_human="yes"))
    handlers = {"requeue_owner": lambda ctx: bt.HandlerResult()}
    bt.apply_task(sandbox.status, sandbox.queue, project=_PROJECT, kanban_root=sandbox.root, now=_NOW,
                  handlers=handlers)
    # The requeued attempt is refused again: it re-blocks at the Zero
    # Progress ceiling (a new block write, the count re-advanced).
    with sandbox.status.open("a", encoding="utf-8") as fh:
        fh.write("\n## Zero Progress\nreason_code: clean_exit_no_progress\ncount: 3\nlimit: 3\ndetail: x\n")
    write_block_record(sandbox.status, "provider_refusal", "CODER", [_EVIDENCE],
                       now=_NOW + datetime.timedelta(minutes=10))
    later = _NOW + datetime.timedelta(hours=2)
    bt.apply_task(sandbox.status, sandbox.queue, project=_PROJECT, kanban_root=sandbox.root, now=later,
                  handlers=handlers)
    note = _section(sandbox.readme.read_text(encoding="utf-8"), "Triage Note")
    assert note.count("\n") == 1 and "attempt 1 of 3" in note and "attempt 2 of 3" in note
    assert load_block_record(sandbox.status).attempts == 2


def test_handler_refusal_escalates_instead(sandbox):
    sandbox.seed(_status(_record("zero_progress"), needs_human="yes"))

    def refusing(ctx):
        raise bt.HandlerRefused("the branch holds commits found nowhere else")

    result = bt.apply_task(sandbox.status, sandbox.queue, project=_PROJECT, kanban_root=sandbox.root, now=_NOW,
                           handlers={"requeue_owner": refusing})
    assert result.outcome == bt.OUTCOME_ESCALATE and result.mutation == bt.MUTATION_NONE
    text = sandbox.status.read_text(encoding="utf-8")
    assert "commits found nowhere else" in _section(text, "OVERWATCH Diagnosis")
    assert load_block_record(sandbox.status).attempts == 0
    assert not _section(sandbox.readme.read_text(encoding="utf-8"), "Triage Note")


# ---------------------------------------------------------------------------
# 7. The check's mutation step (executor output stubbed, everything else real)
# ---------------------------------------------------------------------------


def _run_check_with_stubbed_apply(sb: Sandbox, tmp_path, apply_line: str, **env: str):
    script = textwrap.dedent(f"""\
        set -euo pipefail
        source scripts/lib/pp_run_ops.sh
        eval "$(declare -f pp_run_ops | sed '1s/^pp_run_ops/_real_pp_run_ops/')"
        pp_run_ops() {{
            if [[ "$1" == "pgai_agent_kanban.overwatch.block_triage" && "$2" == "apply" ]]; then
                printf '%s\\n' '{apply_line}'
                return 0
            fi
            _real_pp_run_ops "$@"
        }}
        source {_CHECK}
        rc=0
        overwatch_check_block_triage || rc=$?
        echo "CHECK_EXIT=$rc"
    """)
    (sb.root / "tmp").mkdir(exist_ok=True)
    return run_bash(tmp_path, script, extra_env=sb.env(**env), timeout=120)


def test_promote_mutation_goes_through_promote_blocked_to_backlog(sandbox, tmp_path):
    sandbox.seed(_status(_record("zero_progress"), needs_human="yes"))
    line = "act\tzero_progress\trequeue_owner\tpromote\tOVERWATCH block triage zero_progress requeue_owner, attempt 1"
    result = _run_check_with_stubbed_apply(sandbox, tmp_path, line)
    assert "CHECK_EXIT=0" in result.stdout, result.stderr
    text = sandbox.status.read_text(encoding="utf-8")
    assert _section(text, "State") == "BACKLOG" and _section(text, "Needs Human") == "no"
    assert "zero_progress requeue_owner, attempt 1" in _section(text, "Summary")
    assert _section(text, "Block Record"), "promotion keeps the Block Record"
    assert f"- [ ] {_TASK}" in sandbox.queue.read_text(encoding="utf-8")
    assert {p.name for p in sandbox.backups()} == {"status.md", "coder_backlog.md"}
    row = [r for r in sandbox.actions.splitlines() if "block-triage:zero_progress:requeue_owner" in r]
    assert row and f"{_TASK}/status.md" in row[0] and "queue backup" in row[0]


def test_reverting_a_triage_firing_restores_both_status_and_queue(sandbox, tmp_path):
    firing = "2026-02-02T02:02:02Z"
    sandbox.seed(_status(_record("zero_progress"), needs_human="yes"))
    seeded = sandbox.snapshot()
    line = "act\tzero_progress\trequeue_owner\tpromote\tOVERWATCH block triage zero_progress requeue_owner, attempt 1"
    result = _run_check_with_stubbed_apply(sandbox, tmp_path, line, OVERWATCH_FIRING_TIMESTAMP=firing)
    assert "CHECK_EXIT=0" in result.stdout, result.stderr
    assert sandbox.snapshot() != seeded, "the fix changed the task"

    row = next(r for r in sandbox.actions.splitlines() if "block-triage:zero_progress:requeue_owner" in r)
    backup_column = row.split("\t")[4].split(",")
    assert len(backup_column) == 2, f"both backups are listed machine-readably: {row}"
    assert backup_column[0].endswith(f"tasks/{_TASK}/status.md")
    assert backup_column[1].endswith("tasks/queues/coder_backlog.md")

    revert = run_bash(
        tmp_path, f"bash scripts/overwatch-revert.sh --project {_PROJECT} {firing}",
        extra_env=sandbox.env(), timeout=120,
    )

    assert revert.returncode == 0, revert.stdout + revert.stderr
    assert sandbox.snapshot() == seeded, revert.stdout
    assert "Files restored: 2" in revert.stdout


def test_transition_mutation_goes_through_transition_task_state(sandbox, tmp_path):
    sandbox.seed(_status(_record("gate_merge_not_found"), needs_human="yes"))
    line = "act\tgate_merge_not_found\trecompute_gate\ttransition:DONE\tgate recomputed"
    result = _run_check_with_stubbed_apply(sandbox, tmp_path, line)
    assert "CHECK_EXIT=0" in result.stdout, result.stderr
    text = sandbox.status.read_text(encoding="utf-8")
    assert _section(text, "State") == "DONE"
    assert f"- [x] {_TASK}" in sandbox.queue.read_text(encoding="utf-8")
    assert "block-triage:gate_merge_not_found:recompute_gate" in sandbox.actions


def test_failed_executor_writes_leave_the_state_alone(sandbox, tmp_path):
    sandbox.seed(_status(_record("zero_progress"), needs_human="yes"))
    script = textwrap.dedent(f"""\
        set -euo pipefail
        source scripts/lib/pp_run_ops.sh
        eval "$(declare -f pp_run_ops | sed '1s/^pp_run_ops/_real_pp_run_ops/')"
        pp_run_ops() {{
            if [[ "$1" == "pgai_agent_kanban.overwatch.block_triage" && "$2" == "apply" ]]; then
                return 1
            fi
            _real_pp_run_ops "$@"
        }}
        source {_CHECK}
        rc=0
        overwatch_check_block_triage || rc=$?
        echo "CHECK_EXIT=$rc"
    """)
    (sandbox.root / "tmp").mkdir(exist_ok=True)
    result = run_bash(tmp_path, script, extra_env=sandbox.env(), timeout=120)
    assert "CHECK_EXIT=1" in result.stdout, result.stderr
    assert _section(sandbox.status.read_text(encoding="utf-8"), "State") == "BLOCKED"
    assert "block-triage:" not in sandbox.actions
    assert "1 failed" in result.stderr


# ---------------------------------------------------------------------------
# 8. --dry-run
# ---------------------------------------------------------------------------


def test_dry_run_writes_nothing(sandbox, tmp_path):
    sandbox.seed(_status(_record("missing_readme")))
    before = sandbox.snapshot()
    result = _run_check(sandbox, tmp_path, "--dry-run")
    assert "CHECK_EXIT=0" in result.stdout, result.stderr
    assert sandbox.snapshot() == before and sandbox.evidence_files() == [] and sandbox.backups() == []
    assert "block-triage-dry-run" in sandbox.actions and "would escalate missing_readme" in sandbox.actions
    assert "block-triage:" not in sandbox.actions
    assert "[dry-run]" in result.stderr and "would escalate" in result.stderr


def test_help_prints_the_usage_and_exits_zero(tmp_path):
    result = run_bash(tmp_path, f"bash {_CHECK} --help")
    assert result.returncode == 0
    assert "--dry-run" in result.stdout and "Exit codes:" in result.stdout


# ---------------------------------------------------------------------------
# 9. Callers: the sweep runner and the overwatch wake
# ---------------------------------------------------------------------------


def test_sweep_runner_discovers_and_runs_block_triage(sandbox, tmp_path):
    # The full sweep runs every check, push-lag among them: pin the project
    # to a throwaway dev tree with push_to_remote = false so no check
    # resolves a dev tree or a remote from outside the sandbox.
    sandbox.pin_dev_tree()
    sandbox.seed(_status(_record("plugin_load_failed")))
    (sandbox.root / "tmp").mkdir(exist_ok=True)
    result = run_bash(tmp_path, f"bash {_SWEEP}", extra_env=sandbox.env(), timeout=300)
    assert result.returncode == 0, result.stderr
    assert "running block-triage" in result.stderr
    assert "running push-lag" in result.stderr, "the sweep ran a subset, not every check"
    assert "block-triage:plugin_load_failed:escalate" in sandbox.actions
    assert _section(sandbox.status.read_text(encoding="utf-8"), "Needs Human") == "yes"


def _wake_hook_script(agent: str) -> str:
    return textwrap.dedent(f"""\
        set -euo pipefail
        log() {{ echo "LOG: $*"; }}
        source scripts/lib/pp_run_ops.sh
        eval "$(awk '/^run_overwatch_block_triage\\(\\)/{{f=1}} f{{print}} f&&/^}}/{{exit}}' {_WAKE_PROJECT})"
        eval "$(awk '/^_overwatch_wake_load_firing_lock\\(\\)/{{f=1}} f{{print}} f&&/^}}/{{exit}}' {_WAKE_PROJECT})"
        AGENT={agent}
        SCRIPT_DIR="$PWD/scripts/wake"
        run_overwatch_block_triage "{_PROJECT}"
        echo "HOOK_DONE"
    """)


def test_overwatch_wake_hook_runs_block_triage(sandbox, tmp_path):
    sandbox.seed(_status(_record("invalid_assignment")))
    (sandbox.root / "tmp").mkdir(exist_ok=True)
    result = run_bash(tmp_path, _wake_hook_script("overwatch"), extra_env=sandbox.env(), timeout=120)
    assert "HOOK_DONE" in result.stdout, result.stderr
    assert "block-triage:invalid_assignment:escalate" in sandbox.actions
    assert (sandbox.root / "logs" / "overwatch-block-triage.log").is_file()


def test_wake_hook_is_a_no_op_for_other_agents(sandbox, tmp_path):
    sandbox.seed(_status(_record("invalid_assignment")))
    before = sandbox.snapshot()
    (sandbox.root / "tmp").mkdir(exist_ok=True)
    result = run_bash(tmp_path, _wake_hook_script("coder"), extra_env=sandbox.env(), timeout=120)
    assert "HOOK_DONE" in result.stdout, result.stderr
    assert sandbox.snapshot() == before and sandbox.actions == ""


# ---------------------------------------------------------------------------
# 9b. Every entry point takes the project's firing lock
# ---------------------------------------------------------------------------


class _FiringLockHolder:
    """Another OVERWATCH firing: a background shell holding the project's
    firing lock (with live-pid provenance) until release() is called."""

    def __init__(self, sb: Sandbox, tmp_path: pathlib.Path) -> None:
        self.ready = tmp_path / "holder-ready"
        self.hold = tmp_path / "holder-hold"
        self.hold.write_text("", encoding="utf-8")
        script = textwrap.dedent(f"""\
            source scripts/lib/lock_provenance.sh
            source scripts/lib/overwatch_lib.sh
            overwatch_acquire_firing_lock {_PROJECT} || exit 9
            touch "{self.ready}"
            while [[ -f "{self.hold}" ]]; do sleep 0.2; done
            overwatch_release_firing_lock
        """)
        self.proc = subprocess.Popen(
            ["bash", "-c", script], cwd=pathlib.Path(__file__).resolve().parents[2],
            env={**__import__("os").environ, **sb.env()},
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
        )
        deadline = time.monotonic() + 30
        while not self.ready.exists():
            assert self.proc.poll() is None, self.proc.communicate()
            assert time.monotonic() < deadline, "the lock holder never became ready"
            time.sleep(0.1)

    def release(self) -> None:
        self.hold.unlink(missing_ok=True)
        self.proc.wait(timeout=30)


def test_sweep_and_nudge_skip_a_project_whose_firing_lock_is_held(sandbox, tmp_path):
    sandbox.pin_dev_tree()
    sandbox.seed(_status(_record("plugin_load_failed")))
    (sandbox.root / "tmp").mkdir(exist_ok=True)
    before = sandbox.snapshot()
    holder = _FiringLockHolder(sandbox, tmp_path)
    try:
        sweep = run_bash(tmp_path, f"bash {_SWEEP}", extra_env=sandbox.env(), timeout=300)
        nudge = run_bash(tmp_path, f"bash {_SWEEP} --only block-triage --lock-wait 60",
                         extra_env=sandbox.env(), timeout=300)
    finally:
        holder.release()

    for result in (sweep, nudge):
        assert result.returncode == 0, result.stderr
        assert "overwatch.firing_lock_held" in result.stderr, result.stderr
        assert "running block-triage" not in result.stderr, "nothing runs while another firing holds the lock"
    assert sandbox.snapshot() == before
    assert sandbox.actions.count("\tfiring-skipped:lock-held\t") == 2, sandbox.actions
    assert "block-triage:" not in sandbox.actions

    # Released, the next nudge acts.
    after = run_bash(tmp_path, f"bash {_SWEEP} --only block-triage", extra_env=sandbox.env(), timeout=300)
    assert after.returncode == 0, after.stderr
    assert "block-triage:plugin_load_failed:escalate" in sandbox.actions
    lock = sandbox.root / "locks" / f"overwatch-{_PROJECT}.lock"
    assert not lock.exists() or not lock.read_text(encoding="utf-8").strip(), (
        "the sweep released the lock and cleared its provenance"
    )


def test_overwatch_wake_skips_block_triage_while_another_firing_holds_the_lock(sandbox, tmp_path):
    sandbox.seed(_status(_record("invalid_assignment")))
    (sandbox.root / "tmp").mkdir(exist_ok=True)
    before = sandbox.snapshot()
    holder = _FiringLockHolder(sandbox, tmp_path)
    try:
        result = run_bash(tmp_path, _wake_hook_script("overwatch"), extra_env=sandbox.env(), timeout=120)
    finally:
        holder.release()

    assert "HOOK_DONE" in result.stdout, result.stderr
    assert sandbox.snapshot() == before
    assert "\tfiring-skipped:lock-held\t" in sandbox.actions
    log = (sandbox.root / "logs" / "overwatch-block-triage.log").read_text(encoding="utf-8")
    assert "overwatch.firing_lock_held" in log


# ---------------------------------------------------------------------------
# 10. check-blocked-tasks.sh no longer promotes an "Active RC" block
# ---------------------------------------------------------------------------


def test_blocked_tasks_check_no_longer_promotes_active_rc_blocks(sandbox, tmp_path):
    sandbox.seed(
        "# Status\n\n## State\nBLOCKED\n\n## Blocked Reason\nWaiting for Active RC.\n\n## Needs Human\nno\n"
    )
    (sandbox.project_root / "release-state.md").write_text("# Release State\n\n## Active RC\nnone\n", encoding="utf-8")
    before = sandbox.snapshot()
    (sandbox.root / "tmp").mkdir(exist_ok=True)
    result = run_bash(
        tmp_path,
        f"set -euo pipefail\nsource {_CHECK_BLOCKED}\noverwatch_check_blocked_tasks\necho DONE",
        extra_env=sandbox.env(),
    )
    assert "DONE" in result.stdout, result.stderr
    assert sandbox.snapshot() == before
    assert sandbox.actions == ""
    assert "no ## Block Record" in result.stderr and _TASK in result.stderr
