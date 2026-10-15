"""
test_block_triage_stage_deferral_and_operator_verbs.py
======================================================
OVERWATCH block triage for the last two block classes
(``pgai_agent_kanban.overwatch.block_triage`` with ``allowlisted_verbs`` and
``block_stage_deferral``, driven through the sweep check
``scripts/lib/overwatch-checks/check-block-triage.sh``).

Covered:
  1. Operator data action (AT8): restamp-fixed-in with valid typed
     arguments runs through the sweep check — every named done bug is
     stamped, the owner is requeued (BACKLOG, ``[ ]``), the Triage Note and
     the evidence file carry the run and an idempotence re-run reporting 0
     changes, and a pending ``rerun_reports_no_changes`` entry is in the
     ledger. A second run reports 0 changes and writes no byte.
  2. AT8 negatives: a prose-only remedy, a verb not on the allowlist, a
     malformed version, an unknown project, a bug that is not done and a bug
     that does not exist each leave the task BLOCKED with Needs Human yes and
     a Diagnosis naming the cause; no bug file changes and no verb runs.
     The run itself re-checks its targets, so a target that changed after
     planning is never written.
  3. Verification on the next block: another operator data action fails it
     and escalates; any other block passes it and restarts the epoch.
  4. Ship-policy HALT (AT10): routed to the resolution stage only. Through
     the sweep check with the real stage, the task folder, the queue and the
     HALT stay byte-identical, no backup or evidence is written, and the
     action log holds the stage's own decline and the one triage row. With
     a stage that executes, the result is verified by its Decision event and
     still nothing outside the stage is written. A stage that executes
     without a Decision event is unverified; a stage that raises is
     reported. No ship-policy HALT, block_triage=false, HALT_OVERWATCH and a
     plan that changed under the lock each leave the stage uncalled.
  5. The runnable allowlist and the typed-argument allowlist name the same
     verbs.

Every fixture builds a sandbox kanban root and a throwaway git repository
(the project's dev tree) under pytest's tmp_path; nothing reads or writes
the live install or a real provider.
"""

from __future__ import annotations

import dataclasses
import datetime
import pathlib
import shlex
import subprocess

import pytest

from pgai_agent_kanban.overwatch import allowlisted_verbs as av
from pgai_agent_kanban.overwatch import block_triage as bt
from pgai_agent_kanban.overwatch.block_record import ACTION_VERBS, load_block_record, write_block_record
from pgai_agent_kanban.overwatch.block_requeue import read_ledger
from pgai_agent_kanban.task_status import read_section, set_or_append_section
from tests.fixtures.binary_guard import require_binary
from tests.fixtures.scrubbed_subprocess_env import build_scrubbed_env

# This file lives at team/tests/unit/; team/ is three parents up.
_TEAM_DIR = pathlib.Path(__file__).resolve().parent.parent.parent
_CHECK = _TEAM_DIR / "scripts" / "lib" / "overwatch-checks" / "check-block-triage.sh"

_PROJECT = "triage-proj"
_TASK = "CODER-20260101-003-example-task"
_VERSION = "v9.9.9"
_NOW = datetime.datetime(2026, 1, 1, 12, 0, 0, tzinfo=datetime.timezone.utc)
_SHIP_POLICY_HALT = (
    "# HALT created by CM at 2026-01-01T00:00:00Z\n"
    "# Reason: TESTER systemic_risk=high in ai_rc/v9.9.9\n"
    "# Resolution: Operator review required.\n"
)


def _diagnostic(result: subprocess.CompletedProcess) -> str:
    return f"--- stdout ---\n{result.stdout}\n--- stderr ---\n{result.stderr}"


def _section(text: str, heading: str) -> str:
    return (read_section(text, heading) or "").strip()


def _bug_text(name: str, status: str, fixed_in: str = "") -> str:
    """A bug file in the ledger's shape, with a template Fixed In."""
    body = f"{fixed_in}\n" if fixed_in else ""
    return (
        f"# {name}\n\n## Status\n{status}\n\n## Symptom\nSomething misbehaves.\n\n"
        "## Fixed In\n<!-- Written by the release engine at the fixing release. -->\n"
        f"{body}\n## Public ID\n<!-- Assigned at first disclosure. -->\n"
    )


def _restamp_evidence(*, version: str = _VERSION, bugs: str = "BUG-0412,BUG-0413",
                      project: str = _PROJECT, verb: str = "restamp-fixed-in") -> list:
    return [f"verb={verb}", f"project={project}", f"version={version}", f"bugs={bugs}"]


class Sandbox:
    """A throwaway kanban root with one project, its bug ledger, one task and
    a throwaway git repository as the project's dev tree."""

    def __init__(self, tmp_path: pathlib.Path) -> None:
        self.tmp = tmp_path
        self.root = tmp_path / "kanban"
        self.project_root = self.root / "projects" / _PROJECT
        self.task_dir = self.project_root / "tasks" / _TASK
        self.queue = self.project_root / "tasks" / "queues" / "coder_backlog.md"
        self.bugs = self.project_root / "bugs"
        self.repo = tmp_path / "repo"
        self.home = tmp_path / "home"
        for d in (self.project_root / "overwatch" / "backups", self.task_dir / "artifacts", self.task_dir / "logs",
                  self.queue.parent, self.bugs, self.root / "locks", self.root / "tmp", self.home):
            d.mkdir(parents=True, exist_ok=True)
        (self.project_root / "overwatch" / "actions.log").write_text("", encoding="utf-8")
        (self.project_root / "project.cfg").write_text(
            f"[project]\nproject_name = {_PROJECT}\ndev_tree_path = {self.repo}\nbranch_prefix = ai_\n",
            encoding="utf-8",
        )
        (self.root / "projects.cfg").write_text(f"[project:{_PROJECT}]\npriority = 1\n", encoding="utf-8")
        (self.root / "kanban.cfg").write_text("[wake]\n", encoding="utf-8")
        (self.project_root / "release-state.md").write_text(
            "# Release State\n\n## Active RC\nnone\n\n## RC Opened At\nnone\n\n## RC Opened By Task\nnone\n",
            encoding="utf-8",
        )
        self.readme.write_text(f"# Task\n\n## Task ID\n{_TASK}\n\n## Role\nCODER\n\n## Goal\nDo the thing.\n",
                               encoding="utf-8")
        self.status.write_text(
            f"# Status\n\n## Task\n{_TASK}\n\n## State\nWORKING\n\n## Summary\nWorking.\n\n"
            "## Blockers\nnone\n\n## Needs Human\nno\n",
            encoding="utf-8",
        )
        self.set_marker("A")
        self.repo.mkdir()
        subprocess.run(["git", "init", "-q", "-b", "main", str(self.repo)], check=True, capture_output=True)

    # -- task and ledger files ------------------------------------------

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
    def halt(self) -> pathlib.Path:
        return self.project_root / "HALT"

    @property
    def actions(self) -> str:
        return (self.project_root / "overwatch" / "actions.log").read_text(encoding="utf-8")

    def text(self) -> str:
        return self.status.read_text(encoding="utf-8")

    def note(self) -> str:
        return _section(self.readme.read_text(encoding="utf-8"), "Triage Note")

    def set_marker(self, marker: str) -> None:
        self.queue.write_text(f"# CODER Backlog\n\n## Queue\n\n- [{marker}] {_TASK} — example\n", encoding="utf-8")

    def add_bug(self, bug_id: str, status: str = "done", fixed_in: str = "") -> pathlib.Path:
        path = self.bugs / f"{bug_id}-example-{status}.md"
        path.write_text(_bug_text(path.stem, status, fixed_in), encoding="utf-8")
        return path

    def block(self, reason_code: str, evidence: list, *, owner: str = "CODER",
              now: "datetime.datetime | None" = None) -> None:
        """Block the task the way a block site does (a re-block keeps the epoch)."""
        text = self.text()
        for heading, body in (("State", "BLOCKED"), ("Needs Human", "yes"), ("Blockers", f"{reason_code} block")):
            text = set_or_append_section(text, heading, body)
        self.status.write_text(text, encoding="utf-8")
        write_block_record(self.status, reason_code, owner, evidence, now=now)
        self.set_marker("B")

    def bug_bytes(self) -> dict:
        return {p.name: p.read_bytes() for p in sorted(self.bugs.glob("*.md"))}

    #: Sandbox plumbing the envelope itself uses (the repo lock's provenance,
    #: scratch files, runner logs) — never task, queue, ledger or HALT state.
    _PLUMBING = ("locks/", "tmp/", "logs/")

    def tree(self, *, skip: tuple = ()) -> dict:
        """Every project-state file under the kanban root, byte for byte,
        except the sandbox plumbing and *skip*."""
        out = {}
        for p in sorted(self.root.rglob("*")):
            rel = p.relative_to(self.root).as_posix()
            if p.is_file() and rel not in skip and not rel.startswith(self._PLUMBING):
                out[rel] = p.read_bytes()
        return out

    def backups(self) -> list:
        return sorted(p for p in (self.project_root / "overwatch" / "backups").rglob("*") if p.is_file())

    # -- running the check ----------------------------------------------

    def env(self, **extra: str) -> dict:
        return build_scrubbed_env(
            home_dir=self.home,
            extra_env={
                "KANBAN_ROOT": str(self.root),
                "PGAI_AGENT_KANBAN_ROOT_PATH": str(self.root),
                "PGAI_AGENT_KANBAN_TEMP_DIR": str(self.root / "tmp"),
                "PGAI_WORKTREE_BASE": str(self.root / "tmp" / "worktrees"),
                "OVERWATCH_PROJECT": _PROJECT,
                "HALT_OVERWATCH": "",
                **extra,
            },
        )

    def run_check(self, **extra_env: str) -> subprocess.CompletedProcess:
        script = (
            f"set -uo pipefail\ncd {shlex.quote(str(_TEAM_DIR))}\n"
            f"source {shlex.quote(str(_CHECK))}\nrc=0\noverwatch_check_block_triage || rc=$?\n"
            "echo \"CHECK_EXIT=$rc\"\n"
        )
        result = subprocess.run(["bash", "-c", script], capture_output=True, text=True, timeout=180,
                                env=self.env(**extra_env), cwd=str(self.tmp))
        assert "CHECK_EXIT=0" in result.stdout, _diagnostic(result)
        return result


@pytest.fixture
def sandbox(tmp_path: pathlib.Path) -> Sandbox:
    require_binary("git", label="block-triage-policy-verb")
    return Sandbox(tmp_path)


def _assert_stays_blocked_with_diagnosis(sb: Sandbox, *fragments: str) -> str:
    text = sb.text()
    assert _section(text, "State") == "BLOCKED" and _section(text, "Needs Human") == "yes", text
    assert f"- [B] {_TASK}" in sb.queue.read_text(encoding="utf-8")
    diagnosis = _section(text, "OVERWATCH Diagnosis")
    assert diagnosis.startswith("Block epoch "), text
    for fragment in fragments:
        assert fragment in diagnosis, f"{fragment!r} not in the diagnosis: {diagnosis}"
    assert not sb.note(), "an escalation runs nothing, so it writes no Triage Note"
    assert not list(sb.evidence_dir.glob("verb-*")), "no verb ran, so no verb output was written"
    return diagnosis


# ---------------------------------------------------------------------------
# 1. Operator data action runs, requeues, and is idempotent (AT8)
# ---------------------------------------------------------------------------


def test_restamp_with_valid_typed_args_runs_requeues_and_a_rerun_changes_nothing(sandbox):
    first = sandbox.add_bug("BUG-0412")
    second = sandbox.add_bug("BUG-0413")
    untouched = sandbox.add_bug("BUG-0414")
    untouched_before = untouched.read_bytes()
    sandbox.block("agent_operator_data_action", _restamp_evidence())

    result = sandbox.run_check()

    for bug in (first, second):
        assert _section(bug.read_text(encoding="utf-8"), "Fixed In").endswith(_VERSION), bug.read_text()
    assert untouched.read_bytes() == untouched_before, "a bug the action does not name is never written"

    text = sandbox.text()
    assert _section(text, "State") == "BACKLOG" and _section(text, "Needs Human") == "no", text
    assert f"- [ ] {_TASK}" in sandbox.queue.read_text(encoding="utf-8")
    assert load_block_record(sandbox.status).attempts == 1
    note = sandbox.note()
    assert "run_allowlisted_verb, attempt 1 of 1" in note
    assert "2 stamped" in note and "(0 changes)" in note
    verb_files = list(sandbox.evidence_dir.glob("verb-restamp-fixed-in-*.txt"))
    assert len(verb_files) == 1
    output = verb_files[0].read_text(encoding="utf-8")
    assert "# run\n" in output and "# idempotence re-run\n" in output
    assert f"$ restamp-fixed-in.sh --project {_PROJECT} --version {_VERSION} BUG-0412 BUG-0413" in output
    assert output.count("2 skipped") == 1, "the re-run skips both bugs it already stamped"
    assert [(e.reason_code, e.verification, e.status) for e in read_ledger(sandbox.evidence_dir) if e] == [
        ("agent_operator_data_action", "rerun_reports_no_changes", "pending")
    ]
    assert "block-triage:agent_operator_data_action:run_allowlisted_verb" in sandbox.actions
    assert "block-triage:agent_operator_data_action:run_allowlisted_verb" in result.stderr

    # A second run of the same typed action is a no-op: 0 changes, no byte written.
    before = sandbox.bug_bytes()
    rerun = av.run_allowlisted_verb(sandbox.root, av.validated_args(load_block_record(sandbox.status)))
    assert rerun.changes == 0 and rerun.failed == 0
    assert sandbox.bug_bytes() == before


def test_an_agent_self_block_through_the_status_writer_reaches_the_verb(sandbox):
    # The agent writes its block with the status-writer tool; liberally
    # written typed arguments are stored canonically and run as such.
    bug = sandbox.add_bug("BUG-0412")
    wrote = subprocess.run(
        ["bash", str(_TEAM_DIR / "scripts" / "task-status.sh"), "--project", _PROJECT, "--key", _TASK,
         "--state", "BLOCKED", "--needs-human", "yes", "--reason-code", "agent_operator_data_action",
         "--action-args", "verb=restamp-fixed-in.sh", f"project={_PROJECT}", "version=9.9.9", "bugs=bug-0412"],
        capture_output=True, text=True, timeout=60, env=sandbox.env(), cwd=str(sandbox.tmp),
    )
    assert wrote.returncode == 0, _diagnostic(wrote)
    sandbox.set_marker("B")
    assert load_block_record(sandbox.status).evidence.startswith("verb=restamp-fixed-in; ")

    sandbox.run_check()

    assert _section(bug.read_text(encoding="utf-8"), "Fixed In").endswith(_VERSION)
    assert _section(sandbox.text(), "State") == "BACKLOG"


# ---------------------------------------------------------------------------
# 2. AT8 negatives: the task stays BLOCKED and the verb never runs
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "evidence, fragment",
    [
        pytest.param(["worktree=gone", "branch=ai_feature/example"], "prose only", id="prose-only-remedy"),
        pytest.param(_restamp_evidence(verb="delete-everything"), "not allowlisted", id="verb-not-allowlisted"),
        pytest.param(_restamp_evidence(version="v9.9"), "action_args_valid", id="malformed-version"),
        pytest.param(_restamp_evidence(version="latest"), "action_args_valid", id="version-not-a-version"),
        pytest.param(_restamp_evidence(project="no-such-project"), "not registered", id="unknown-project"),
        pytest.param(_restamp_evidence(bugs="BUG-0412,BUG-0415"), "not done", id="bug-not-done"),
        pytest.param(_restamp_evidence(bugs="BUG-0412,BUG-0499"), "BUG-0499", id="bug-missing"),
        pytest.param(_restamp_evidence(bugs="BUG-12"), "action_args_valid", id="bug-id-wrong-shape"),
    ],
)
def test_invalid_operator_data_action_stays_blocked_and_runs_nothing(sandbox, evidence, fragment):
    sandbox.add_bug("BUG-0412")
    sandbox.add_bug("BUG-0415", status="open")
    sandbox.block("agent_operator_data_action", evidence)
    bugs_before = sandbox.bug_bytes()

    sandbox.run_check()

    diagnosis = _assert_stays_blocked_with_diagnosis(sandbox, "agent_operator_data_action", fragment)
    assert "Options: (1)" in diagnosis
    assert sandbox.bug_bytes() == bugs_before, "a refused action writes no bug file"
    assert load_block_record(sandbox.status).attempts == 0
    assert "block-triage:agent_operator_data_action:escalate" in sandbox.actions


def test_the_run_rechecks_its_targets_immediately_before_writing(sandbox):
    bug = sandbox.add_bug("BUG-0412")
    sandbox.block("agent_operator_data_action", _restamp_evidence(bugs="BUG-0412"))
    args = av.validated_args(load_block_record(sandbox.status))
    assert av.target_problems(sandbox.root, args) == []

    # The bug is reopened after planning and before the run.
    bug.write_text(_bug_text(bug.stem, "open"), encoding="utf-8")
    before = bug.read_bytes()
    with pytest.raises(av.VerbRefused, match="not done"):
        av.run_allowlisted_verb(sandbox.root, args)
    assert bug.read_bytes() == before


def test_a_rerun_that_still_changes_something_escalates(sandbox, monkeypatch):
    sandbox.add_bug("BUG-0412")
    sandbox.block("agent_operator_data_action", _restamp_evidence(bugs="BUG-0412"))
    real = av.run_allowlisted_verb
    runs = []

    def never_settles(kanban_root, args):
        run = real(kanban_root, args)
        runs.append(run)
        return dataclasses.replace(run, changes=1)

    monkeypatch.setattr(bt, "run_allowlisted_verb", never_settles)
    result = bt.apply_task(sandbox.status, sandbox.queue, project=_PROJECT, kanban_root=sandbox.root, now=_NOW)
    assert len(runs) == 2
    assert result.outcome == bt.OUTCOME_ESCALATE and result.mutation == bt.MUTATION_NONE
    _section_text = _section(sandbox.text(), "OVERWATCH Diagnosis")
    assert "re-run still reported 1 change(s)" in _section_text
    assert len(list(sandbox.evidence_dir.glob("verb-restamp-fixed-in-*.txt"))) == 1


# ---------------------------------------------------------------------------
# 3. Verification on the next block
# ---------------------------------------------------------------------------


def _acted_on_restamp(sb: Sandbox) -> None:
    sb.add_bug("BUG-0412")
    first_block = _NOW - datetime.timedelta(hours=2)
    sb.block("agent_operator_data_action", _restamp_evidence(bugs="BUG-0412"), now=first_block)
    result = bt.apply_task(sb.status, sb.queue, project=_PROJECT, kanban_root=sb.root,
                           now=_NOW - datetime.timedelta(hours=1))
    assert result.outcome == bt.OUTCOME_ACT and result.mutation == bt.MUTATION_PROMOTE, result
    sb.status.write_text(set_or_append_section(sb.text(), "State", "BACKLOG"), encoding="utf-8")


def test_asking_for_an_operator_data_action_again_fails_verification_and_escalates(sandbox):
    _acted_on_restamp(sandbox)
    sandbox.block("agent_operator_data_action", _restamp_evidence(bugs="BUG-0412"), now=_NOW)
    result = bt.apply_task(sandbox.status, sandbox.queue, project=_PROJECT, kanban_root=sandbox.root, now=_NOW)
    assert result.outcome == bt.OUTCOME_ESCALATE
    diagnosis = _section(sandbox.text(), "OVERWATCH Diagnosis")
    assert "rerun_reports_no_changes failed" in diagnosis and "operator data action again" in diagnosis
    assert [e.status for e in read_ledger(sandbox.evidence_dir) if e] == ["failed"]


def test_a_different_next_block_passes_verification_and_restarts_the_epoch(sandbox):
    _acted_on_restamp(sandbox)
    sandbox.block("zero_progress", ["worktree=gone"], now=_NOW)
    plan = bt.plan_task(sandbox.status, now=_NOW)
    assert plan.restart_epoch and plan.verdict is not None and plan.verdict.passed
    assert plan.record.attempts == 0, "a passed verification starts a new epoch for the new block"


# ---------------------------------------------------------------------------
# 4. Ship-policy HALT is routed to the resolution stage only (AT10)
# ---------------------------------------------------------------------------


@dataclasses.dataclass
class _StageOutcome:
    status: str
    reason: str
    ledger_entry_id: "str | None" = None


class _Stage:
    """A stand-in for the resolution stage that records each call and, when
    told to, appends a Decision event the way the stage does."""

    def __init__(self, sb: Sandbox, status: str = "executed", *, decision_event: bool = True,
                 raises: bool = False) -> None:
        self.sb, self.status, self.decision_event, self.raises = sb, status, decision_event, raises
        self.calls: list = []

    def __call__(self, project: str, kanban_root: str) -> _StageOutcome:
        self.calls.append((project, kanban_root))
        if self.raises:
            raise RuntimeError("stage exploded")
        if self.decision_event:
            state = self.sb.project_root / "release-state.md"
            state.write_text(state.read_text(encoding="utf-8") + "\n## OVERWATCH Decision\nLane: B\nOutcome: shipped\n",
                             encoding="utf-8")
        return _StageOutcome(self.status, "stage reason", ledger_entry_id="entry-1")


def _ship_policy_block(sb: Sandbox, *, halt: "str | None" = _SHIP_POLICY_HALT) -> None:
    if halt is not None:
        sb.halt.write_text(halt, encoding="utf-8")
    sb.block("ship_policy_halt", ["halt=projects/triage-proj/HALT"], owner="CM")


def test_ship_policy_block_is_routed_to_the_real_stage_and_nothing_else_is_written(sandbox):
    _ship_policy_block(sandbox)
    before = sandbox.tree(skip=(f"projects/{_PROJECT}/overwatch/actions.log",))

    result = sandbox.run_check()

    after = sandbox.tree(skip=(f"projects/{_PROJECT}/overwatch/actions.log",))
    assert after == before, "deferral writes nothing outside the stage: " + ", ".join(
        sorted(set(after) ^ set(before) | {k for k in before if k in after and before[k] != after[k]})
    )
    assert sandbox.backups() == [] and not sandbox.evidence_dir.exists()
    rows = [r for r in sandbox.actions.splitlines() if r.strip()]
    stage_rows = [r for r in rows if "\tresolution-stage\t" in r]
    triage_rows = [r for r in rows if "block-triage:ship_policy_halt:defer_to_resolution_stage" in r]
    assert len(stage_rows) == 1 and "Active RC is none" in stage_rows[0], rows
    assert len(triage_rows) == 1 and "resolution stage declined" in triage_rows[0], rows
    assert "stays with a human" in triage_rows[0]
    assert len(rows) == 2, rows
    assert "block-triage:ship_policy_halt:defer_to_resolution_stage (defer)" in result.stderr


def test_a_stage_that_executes_is_verified_by_its_decision_event(sandbox):
    _ship_policy_block(sandbox)
    stage = _Stage(sandbox)
    before = sandbox.tree(skip=(f"projects/{_PROJECT}/release-state.md",))

    result = bt.apply_task(sandbox.status, sandbox.queue, project=_PROJECT, kanban_root=sandbox.root, now=_NOW,
                           defer_only=True, stage_runner=stage)

    assert stage.calls == [(_PROJECT, str(sandbox.root))]
    assert (result.outcome, result.action, result.mutation) == (bt.OUTCOME_DEFER, "defer_to_resolution_stage",
                                                                 bt.MUTATION_NONE)
    assert "verified by the stage's Decision event" in result.detail
    assert sandbox.tree(skip=(f"projects/{_PROJECT}/release-state.md",)) == before


def test_a_stage_that_executes_without_a_decision_event_is_unverified(sandbox):
    _ship_policy_block(sandbox)
    stage = _Stage(sandbox, decision_event=False)
    result = bt.apply_task(sandbox.status, sandbox.queue, project=_PROJECT, kanban_root=sandbox.root, now=_NOW,
                           stage_runner=stage)
    assert result.outcome == bt.OUTCOME_DEFER
    assert "unverified" in result.detail and "entry-1" in result.detail


def test_a_stage_that_raises_is_reported_and_nothing_is_written(sandbox):
    _ship_policy_block(sandbox)
    before = sandbox.tree()
    result = bt.apply_task(sandbox.status, sandbox.queue, project=_PROJECT, kanban_root=sandbox.root, now=_NOW,
                           stage_runner=_Stage(sandbox, raises=True))
    assert result.outcome == bt.OUTCOME_DEFER and "stage exploded" in result.detail
    assert "stays with a human" in result.detail
    assert sandbox.tree() == before


@pytest.mark.parametrize(
    "halt, fragment",
    [
        pytest.param(None, "no HALT at", id="no-halt"),
        pytest.param("# operator full-stop\n", "not a ship-policy HALT", id="operator-halt"),
    ],
)
def test_without_a_ship_policy_halt_the_stage_is_not_called(sandbox, halt, fragment):
    _ship_policy_block(sandbox, halt=halt)
    stage = _Stage(sandbox)
    before = sandbox.tree()
    result = bt.apply_task(sandbox.status, sandbox.queue, project=_PROJECT, kanban_root=sandbox.root, now=_NOW,
                           stage_runner=stage)
    assert result.outcome == bt.OUTCOME_SKIP and fragment in result.detail and "stays with a human" in result.detail
    assert stage.calls == [] and sandbox.tree() == before


def test_block_triage_false_does_not_route_to_the_stage(sandbox):
    _ship_policy_block(sandbox)
    stage = _Stage(sandbox)
    result = bt.apply_task(sandbox.status, sandbox.queue, project=_PROJECT, kanban_root=sandbox.root, now=_NOW,
                           triage_enabled=False, stage_runner=stage)
    assert result.outcome == bt.OUTCOME_SKIP and "block_triage is false" in result.detail
    assert stage.calls == []


def test_a_plan_that_is_no_longer_a_deferral_writes_nothing_under_defer_only(sandbox):
    sandbox.block("missing_readme", ["worktree=gone"])
    before = sandbox.tree()
    result = bt.apply_task(sandbox.status, sandbox.queue, project=_PROJECT, kanban_root=sandbox.root, now=_NOW,
                           defer_only=True, stage_runner=_Stage(sandbox))
    assert result.outcome == bt.OUTCOME_SKIP and "planned as a deferral" in result.detail
    assert sandbox.tree() == before


def test_halt_overwatch_leaves_the_ship_policy_block_and_the_stage_alone(sandbox):
    _ship_policy_block(sandbox)
    before = sandbox.tree(skip=(f"projects/{_PROJECT}/overwatch/actions.log",))
    sandbox.run_check(HALT_OVERWATCH="1")
    assert sandbox.tree(skip=(f"projects/{_PROJECT}/overwatch/actions.log",)) == before
    assert "\tresolution-stage\t" not in sandbox.actions
    assert "block-triage-refused" in sandbox.actions


# ---------------------------------------------------------------------------
# 5. The two halves of the allowlist agree
# ---------------------------------------------------------------------------


def test_every_typed_argument_verb_has_a_runner_and_every_runner_is_typed():
    assert set(av.VERBS) == set(ACTION_VERBS)
    assert av.VERBS["restamp-fixed-in"].entry_point == "restamp-fixed-in.sh"
    assert (_TEAM_DIR / "scripts" / av.VERBS["restamp-fixed-in"].entry_point).is_file()
