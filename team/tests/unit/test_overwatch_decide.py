"""
test_overwatch_decide.py
========================
Behaviour of the decision wake's pure function,
pgai_agent_kanban.overwatch.decide.decide, over the shipped classifier table.

Covered:
  1. Every Block Record reason code (iterated from REASON_CODES, so a code
     added without a decision fails here) decides a verb or an inbox
     question from its own table row — never escalate, never a fail-closed
     fallback — both on its first block and once triage is exhausted. The
     expected outcome per code is written out here by hand.
  2. The requirement's mapping: a stale worktree prunes then requeues;
     transient and zero-progress caps return the task to BACKLOG once, then
     ask; missing_readme asks (no operator write regenerates a README) and
     names the runnable command; the requeue budget hands over to the row's
     decision; an unmet precondition asks, never runs the verb.
  2b. A TESTER task that blocked itself while verifying a release: the
     RC's one requeue (reset-to-backlog carrying the block cause), then
     close-unverified once it is spent; any other block, or one outside the
     open RC with the requeue unspent, takes its own row.
  3. HALTs: an operator HALT (project or fleet scope, or beside a chain
     HALT) decides none, even with a runaway trip; a chain HALT goes to the
     resolution stage, or clear-chain-halt once a cause check passed; a
     runaway or repair HALT is left to its writer.
  4. Runaway: any trip decides runaway whatever the reason code.
  5. Fail closed: no record, an unreadable record, an unknown owner, a code
     with no row, a table that does not load, an unknown subject — inbox.
  6. Purity: decide runs over inputs gathered from a sandbox kanban root
     with every write and process start trapped; no file in the sandbox
     changes and no subprocess starts.

Every fixture lives under pytest's tmp_path; the sandbox fixture sets or
clears the kanban root variables together.
"""

from __future__ import annotations

import builtins
import datetime
import hashlib
import io
import os
import pathlib
import subprocess

import pytest

from pgai_agent_kanban.ops.context import OpsContext
from pgai_agent_kanban.overwatch import block_classifier as bc
from pgai_agent_kanban.overwatch import decide as dc
from pgai_agent_kanban.overwatch import runaway
from pgai_agent_kanban.overwatch.block_record import REASON_CODES, BlockRecord, read_block_record
from pgai_agent_kanban.overwatch.halt_clearing import CauseCheck
from pgai_agent_kanban.overwatch.halt_provenance import (
    WRITER_CM_RELEASE,
    WRITER_HALT_AFTER_PROMOTION,
    WRITER_OVERWATCH_REPAIR,
    WRITER_OVERWATCH_RUNAWAY,
    WRITER_ZERO_PROGRESS_CAP,
    HaltProvenance,
    classify_halt,
    provenance_lines,
)

_NOW = datetime.datetime(2026, 1, 1, 12, 0, 0, tzinfo=datetime.UTC)
_PAST_ISO = "2026-01-01T11:00:00+00:00"
_PROJECT = "example-project"
_TASK = "CODER-20260101-001-example-task"
_ROOT = "/srv/kanban-example"

_BRANCH_EVIDENCE = "worktree=gone; branch=ai_feature/example; ahead=2; merged=no"
_RESTAMP_EVIDENCE = "verb=restamp-fixed-in; project=example-project; version=v2.28.0; bugs=BUG-0412,BUG-0413"
_WORKTREE_FACTS = {
    "worktree_residue_in_base": True,
    "residue_holds_no_unique_commits": True,
    "source_branch_exists": True,
}

# A representative first block per code: (owner_role, evidence, cooldown_until, facts).
# A row that waits one window first has already waited it out.
_FIRST = {
    "per_task_timeout": ("CODER", _BRANCH_EVIDENCE + "; timeout_s=1800", "none", {"killed_at": "1800s"}),
    "provider_refusal": ("CODER", "span_s=12; signature=model-not-found", _PAST_ISO, {}),
    "transient_api_error": ("WRITER", "batch_log=none", _PAST_ISO, {}),
    "gate_merge_not_found": ("WRITER", _BRANCH_EVIDENCE, "none", {"branch_ancestor_of_source": True}),
    "gate_empty_commit": ("CODER", _BRANCH_EVIDENCE, "none", {"shas": "abc1234"}),
    "worktree_create_failed": ("CODER", _BRANCH_EVIDENCE, "none", _WORKTREE_FACTS),
    "ship_policy_halt": ("CM", "none", "none", {}),
    "agent_operator_data_action": ("CM", _RESTAMP_EVIDENCE, "none", {"action_targets_verified": True}),
    "agent_transient_provider_error": ("CM", "none", _PAST_ISO, {"blockers": "the provider aborted the call"}),
    "agent_unclassified": ("CM", "none", _PAST_ISO, {"blockers": "the subagent was lost twice"}),
}

# The requirements' decisions, written out by hand: code -> (first block,
# triage exhausted, triage exhausted and the row's verb budget used), each
# (kind, verb name or None). transient_api_error's first block is its
# handler's own (none); see test_transient_block_below_its_ceiling_is_left_to_its_handler.
_V, _I = dc.OUTCOME_VERB, dc.OUTCOME_INBOX
_EXPECTED = {
    "per_task_timeout": ((_V, "requeue"), (_I, None), (_I, None)),
    "provider_refusal": ((_V, "requeue"), (_I, None), (_I, None)),
    "zero_progress": ((_V, "requeue"), (_V, "reset-to-backlog"), (_I, None)),
    "transient_api_error": ((_V, "reset-to-backlog"), (_V, "reset-to-backlog"), (_I, None)),
    "gate_merge_not_found": ((_V, "recompute-gate"), (_I, None), (_I, None)),
    "gate_dirty_worktree": ((_V, "requeue"), (_I, None), (_I, None)),
    "gate_empty_commit": ((_V, "requeue"), (_I, None), (_I, None)),
    "worktree_create_failed": ((_V, "prune-worktree-then-requeue"), (_I, None), (_I, None)),
    "ship_policy_halt": ((_V, "resolution-stage"), (_V, "resolution-stage"), (_V, "resolution-stage")),
    "agent_operator_data_action": ((_V, "restamp-fixed-in"), (_I, None), (_I, None)),
    "missing_readme": ((_I, None), (_I, None), (_I, None)),
    "invalid_assignment": ((_I, None), (_I, None), (_I, None)),
    "invalid_effort_config": ((_I, None), (_I, None), (_I, None)),
    "invalid_state": ((_V, "reset-to-backlog"), (_V, "reset-to-backlog"), (_I, None)),
    "branch_prefix_failed": ((_I, None), (_I, None), (_I, None)),
    "workflow_type_unparseable": ((_I, None), (_I, None), (_I, None)),
    "plugin_load_failed": ((_I, None), (_I, None), (_I, None)),
    "source_branch_missing": ((_I, None), (_I, None), (_I, None)),
    "gate_merge_evidence_not_found": ((_I, None), (_I, None), (_I, None)),
    "agent_transient_provider_error": ((_V, "requeue"), (_I, None), (_I, None)),
    "agent_unclassified": ((_V, "requeue"), (_I, None), (_I, None)),
}

# The outcome causes a row's own decision produces; a fail-closed inbox (a
# code with no row) is none of these.
_ROW_WHYS = {dc.WHY_TRIAGE_ACTION, dc.WHY_ROW_VERB, dc.WHY_ROW_INBOX}


def _record(code: str, *, owner="CODER", attempts=0, cooldown="none", evidence="none", requeues=0) -> BlockRecord:
    return BlockRecord(
        reason_code=code,
        owner_role=owner,
        attempts=attempts,
        first_blocked_at="2026-01-01T09:00:00+00:00",
        last_blocked_at="2026-01-01T09:00:00+00:00",
        cooldown_until=cooldown,
        evidence=evidence,
        resume="fresh",
        requeues=requeues,
    )


def _inputs(record=None, *, subject=dc.SUBJECT_BLOCK, **overrides) -> dc.DecisionInput:
    fields = dict(subject=subject, project=_PROJECT, task_id=_TASK, record=record, kanban_root=_ROOT, now=_NOW)
    fields.update(overrides)
    return dc.DecisionInput(**fields)


def _transient_at_ceiling(code: str) -> dict:
    return {"transient_retry": (3, 3)} if code == "transient_api_error" else {}


@pytest.fixture(scope="module")
def table() -> bc.ClassifierTable:
    return bc.load_table()


def _first(code: str, table, **overrides) -> dc.Outcome:
    owner, evidence, cooldown, facts = _FIRST.get(code, ("CODER", _BRANCH_EVIDENCE, "none", {}))
    record = _record(code, owner=owner, evidence=evidence, cooldown=cooldown)
    overrides = {"facts": facts, **_transient_at_ceiling(code), **overrides}
    return dc.decide(_inputs(record, **overrides), table=table)


def _exhausted(code: str, table, history=None) -> dc.Outcome:
    owner, evidence, cooldown, facts = _FIRST.get(code, ("CODER", _BRANCH_EVIDENCE, "none", {}))
    record = _record(code, owner=owner, evidence=evidence, attempts=99)
    extra = _transient_at_ceiling(code)
    return dc.decide(_inputs(record, facts=facts, history=history or dc.TaskHistory(), **extra), table=table)


def _shape(outcome: dc.Outcome) -> tuple:
    return (outcome.kind, outcome.name)


# ---------------------------------------------------------------------------
# 1. A decision for every reason code
# ---------------------------------------------------------------------------


def test_expected_decisions_cover_exactly_the_block_record_vocabulary():
    assert set(_EXPECTED) == set(REASON_CODES), "write the new code's expected decision into _EXPECTED"


@pytest.mark.parametrize("code", REASON_CODES)
def test_every_reason_code_decides_a_verb_or_an_inbox_question_from_its_own_row(code, table):
    used_up = dc.TaskHistory(verb_uses={verb: 99 for verb in dc.VERBS})
    outcomes = (_first(code, table), _exhausted(code, table), _exhausted(code, table, used_up))
    for stage, outcome in zip(("first block", "triage exhausted", "verb budget used"), outcomes):
        assert outcome.kind in (dc.OUTCOME_VERB, dc.OUTCOME_INBOX), f"{code} {stage}: {outcome.why}: {outcome.detail}"
        assert outcome.why in _ROW_WHYS, f"{code} {stage} fell through to {outcome.why}: {outcome.detail}"
        assert "escalate" not in (outcome.name or "")
    assert tuple(_shape(o) for o in outcomes) == _EXPECTED[code]


@pytest.mark.parametrize("code", REASON_CODES)
def test_a_needs_human_task_decides_as_its_block_does(code, table):
    owner, evidence, cooldown, facts = _FIRST.get(code, ("CODER", _BRANCH_EVIDENCE, "none", {}))
    record = _record(code, owner=owner, evidence=evidence, attempts=99)
    extra = _transient_at_ceiling(code)
    as_block = dc.decide(_inputs(record, facts=facts, **extra), table=table)
    as_needs_human = dc.decide(_inputs(record, subject=dc.SUBJECT_NEEDS_HUMAN, facts=facts, **extra), table=table)
    assert _shape(as_needs_human) == _shape(as_block)


@pytest.mark.parametrize("code", [c for c, rows in _EXPECTED.items() if rows[2][0] == _I])
def test_every_inbox_question_names_the_task_and_a_runnable_answer(code, table):
    used_up = dc.TaskHistory(verb_uses={verb: 99 for verb in dc.VERBS})
    outcome = _exhausted(code, table, used_up)
    assert outcome.source == _TASK
    assert _TASK in outcome.question or _PROJECT in outcome.question
    assert "{" not in outcome.question and "<" not in outcome.how_to_answer
    assert f"bash {_ROOT}/scripts/task-status.sh --project {_PROJECT} --key {_TASK} --state BACKLOG" in (
        outcome.how_to_answer
    )


# ---------------------------------------------------------------------------
# 2. The requirement's mapping
# ---------------------------------------------------------------------------


def test_stale_worktree_prunes_then_requeues_with_the_rows_note(table):
    outcome = _first("worktree_create_failed", table)
    assert _shape(outcome) == (dc.OUTCOME_VERB, dc.VERB_PRUNE_WORKTREE_THEN_REQUEUE)
    assert outcome.args["task_id"] == _TASK and outcome.args["triage_action"] == "prune_worktree_then_requeue"
    assert "pruned" in outcome.args["note"]


def test_stale_worktree_holding_unique_commits_asks_and_never_prunes(table):
    facts = dict(_WORKTREE_FACTS, residue_holds_no_unique_commits=False)
    outcome = _first("worktree_create_failed", table, facts=facts)
    assert outcome.kind == dc.OUTCOME_INBOX and outcome.why == dc.WHY_ROW_INBOX
    assert "unique commits" in outcome.question


def test_transient_block_below_its_ceiling_is_left_to_its_handler(table):
    outcome = _first("transient_api_error", table, transient_retry=(1, 3))
    assert outcome.kind == dc.OUTCOME_NONE and outcome.why == dc.WHY_TRANSIENT_HANDLER


@pytest.mark.parametrize("code", ["transient_api_error", "zero_progress"])
def test_cap_reached_returns_to_backlog_once_per_budget_then_asks(code, table):
    once = _exhausted(code, table)
    assert _shape(once) == (dc.OUTCOME_VERB, dc.VERB_RESET_TO_BACKLOG)
    assert (once.args["use"], once.args["budget"]) == (1, 1)
    spent = _exhausted(code, table, dc.TaskHistory(verb_uses={dc.VERB_RESET_TO_BACKLOG: 1}))
    assert spent.kind == dc.OUTCOME_INBOX and spent.why == dc.WHY_ROW_INBOX


def test_missing_readme_asks_naming_the_command_because_no_write_regenerates_a_readme(table):
    outcome = _first("missing_readme", table)
    assert outcome.kind == dc.OUTCOME_INBOX
    assert "no operator write regenerates" in outcome.question
    assert "restore README.md" in outcome.how_to_answer
    assert f"{_ROOT}/scripts/task-status.sh" in outcome.how_to_answer


def test_allowlisted_verb_is_named_with_its_typed_arguments(table):
    outcome = _first("agent_operator_data_action", table)
    assert _shape(outcome) == (dc.OUTCOME_VERB, "restamp-fixed-in")
    assert {k: outcome.args[k] for k in ("project", "version", "bugs")} == {
        "project": "example-project",
        "version": "v2.28.0",
        "bugs": "BUG-0412,BUG-0413",
    }


def test_spent_requeue_budget_hands_over_to_the_rows_decision(table):
    record = _record("zero_progress", requeues=3)
    outcome = dc.decide(_inputs(record, requeue_budget=3), table=table)
    assert _shape(outcome) == (dc.OUTCOME_VERB, dc.VERB_RESET_TO_BACKLOG)
    assert "requeue budget" in outcome.detail


def test_spent_requeue_budget_never_stops_a_gate_recompute(table):
    record = _record("gate_merge_not_found", owner="WRITER", evidence=_BRANCH_EVIDENCE, requeues=9)
    outcome = dc.decide(_inputs(record, facts={"branch_ancestor_of_source": True}, requeue_budget=3), table=table)
    assert _shape(outcome) == (dc.OUTCOME_VERB, dc.VERB_RECOMPUTE_GATE)


def test_task_inside_its_cooldown_window_decides_none(table):
    record = _record("per_task_timeout", cooldown="2026-01-01T12:30:00+00:00")
    outcome = dc.decide(_inputs(record), table=table)
    assert outcome.kind == dc.OUTCOME_NONE and outcome.why == dc.WHY_NOT_YET


def test_every_triage_action_and_row_verb_is_a_known_verb():
    assert set(dc.TRIAGE_ACTION_VERBS) == set(bc.ROW_ACTIONS) - {dc.RUN_ALLOWLISTED_VERB}
    assert set(dc.TRIAGE_ACTION_VERBS.values()) | set(bc.DECISION_VERBS) <= set(dc.VERBS)


# ---------------------------------------------------------------------------
# 2b. A TESTER task's own block while it verifies a release
# ---------------------------------------------------------------------------

_TESTER = "TESTER-20260101-002-verify-example"
_VERIFY_FACTS = {
    dc.FACT_VERIFICATION_VERSION: "v1.2.3",
    dc.FACT_VERIFICATION_RETRY_SPENT: False,
    dc.FACT_VERIFICATION_CAUSE: "the suite runner could not start",
}


def _tester_block(facts, *, active_rc="v1.2.3", owner="TESTER", code="agent_unclassified", table=None):
    # A row that waits one window first has already waited it out, as in _FIRST.
    cooldown = _FIRST.get(code, (None, None, "none", None))[2]
    record = _record(code, owner=owner, evidence="none", cooldown=cooldown)
    return dc.decide(_inputs(record, task_id=_TESTER, facts=facts, active_rc=active_rc), table=table)


def test_tester_block_on_the_open_rc_is_requeued_once_with_its_block_cause(table):
    outcome = _tester_block(_VERIFY_FACTS, table=table)
    assert _shape(outcome) == (_V, dc.VERB_RESET_TO_BACKLOG)
    assert outcome.why == dc.WHY_VERIFICATION_REQUEUE
    assert outcome.args["verification_retry"] == "v1.2.3"
    assert outcome.args["cause"] == "the suite runner could not start"
    assert outcome.args["task_id"] == _TESTER


def test_tester_block_after_the_rcs_one_requeue_is_closed_so_the_release_ships(table):
    facts = {**_VERIFY_FACTS, dc.FACT_VERIFICATION_RETRY_SPENT: True}
    outcome = _tester_block(facts, table=table)
    assert _shape(outcome) == (_V, dc.VERB_CLOSE_UNVERIFIED)
    assert outcome.why == dc.WHY_VERIFICATION_UNVERIFIED
    assert "verification_retry" not in outcome.args
    # Spent is spent whatever the RC: a shipped RC's task is still closed.
    assert _shape(_tester_block(facts, active_rc="none", table=table)) == (_V, dc.VERB_CLOSE_UNVERIFIED)


@pytest.mark.parametrize(
    "facts, active_rc, owner, code",
    [
        ({}, "v1.2.3", "TESTER", "agent_unclassified"),  # not a release's verification
        (_VERIFY_FACTS, "v9.9.9", "TESTER", "agent_unclassified"),  # another RC is open, retry unspent
        (_VERIFY_FACTS, "v1.2.3", "CODER", "agent_unclassified"),  # not a TESTER task
    ],
)
def test_other_blocks_take_their_own_classifier_row(table, facts, active_rc, owner, code):
    outcome = _tester_block(facts, active_rc=active_rc, owner=owner, code=code, table=table)
    assert outcome.why in _ROW_WHYS, outcome.why
    assert _shape(outcome) == _EXPECTED[code][0]


def test_a_tester_timeout_keeps_its_own_requeue_row(table):
    facts = {**_FIRST["per_task_timeout"][3], **_VERIFY_FACTS}
    outcome = _tester_block(facts, code="per_task_timeout", table=table)
    assert _shape(outcome) == (_V, "requeue")


# ---------------------------------------------------------------------------
# 3. HALTs
# ---------------------------------------------------------------------------


def _halt(origin="chain", writer=WRITER_CM_RELEASE, *, fleet=False, path=f"{_ROOT}/projects/{_PROJECT}/HALT"):
    return HaltProvenance(
        path=path,
        origin=origin,
        writer=writer,
        reason="cm-release.trigger-5.squash-conflict",
        trigger="5",
        source="body",
        detail="example",
        fleet_scope=fleet,
    )


def _trip() -> runaway.Trip:
    event = runaway.Event(runaway.COUNTER_RELEASES, "release:v1.0.6", "2026-01-01T11:50:00+00:00", detail="v1.0.6")
    return runaway.Trip(runaway.COUNTER_RELEASES, runaway.PROJECT_SUBJECT, 5, 6, "1h", (event,))


@pytest.mark.parametrize(
    "halt, operator_halts",
    [
        pytest.param(_halt("operator", None), (), id="operator-project-halt"),
        pytest.param(_halt("operator", None, fleet=True, path=f"{_ROOT}/HALT"), (), id="fleet-halt"),
        pytest.param(_halt(), (f"{_ROOT}/HALT",), id="chain-halt-beside-operator-halt"),
    ],
)
def test_operator_halt_decides_none_even_with_a_runaway_trip(halt, operator_halts):
    for trips in ((), (_trip(),)):
        outcome = dc.decide(
            _inputs(subject=dc.SUBJECT_HALT, halt=halt, operator_halts=operator_halts, runaway_trips=trips)
        )
        assert outcome.kind == dc.OUTCOME_NONE and outcome.why == dc.WHY_OPERATOR_HALT


def test_no_halt_decides_none():
    outcome = dc.decide(_inputs(subject=dc.SUBJECT_HALT, halt=None))
    assert outcome.kind == dc.OUTCOME_NONE and outcome.why == dc.WHY_NO_HALT


@pytest.mark.parametrize("writer", [WRITER_CM_RELEASE, WRITER_HALT_AFTER_PROMOTION, WRITER_ZERO_PROGRESS_CAP])
def test_chain_halt_goes_to_the_resolution_stage(writer):
    outcome = dc.decide(_inputs(subject=dc.SUBJECT_HALT, halt=_halt(writer=writer)))
    assert _shape(outcome) == (dc.OUTCOME_VERB, dc.VERB_RESOLUTION_STAGE)
    assert outcome.args["writer"] == writer and outcome.args["project"] == _PROJECT


def test_chain_halt_with_a_resolved_cause_is_cleared():
    checks = (
        CauseCheck("rc-cancelled", False, "Active RC is v1.2.3"),
        CauseCheck("task-reset", True, "TESTER-20260101-002-verify reads BACKLOG"),
    )
    outcome = dc.decide(_inputs(subject=dc.SUBJECT_HALT, halt=_halt(), cause_checks=checks))
    assert _shape(outcome) == (dc.OUTCOME_VERB, dc.VERB_CLEAR_CHAIN_HALT)
    assert outcome.args["checks"] == ("task-reset",)


@pytest.mark.parametrize("writer", [WRITER_OVERWATCH_RUNAWAY, WRITER_OVERWATCH_REPAIR])
def test_runaway_and_repair_halts_are_left_to_their_writer(writer):
    passed = (CauseCheck("task-reset", True, "reset"),)
    outcome = dc.decide(_inputs(subject=dc.SUBJECT_HALT, halt=_halt(writer=writer), cause_checks=passed))
    assert outcome.kind == dc.OUTCOME_NONE and outcome.why == dc.WHY_HALT_OWN_WRITER


# ---------------------------------------------------------------------------
# 4. Runaway
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("code", REASON_CODES)
def test_a_runaway_trip_decides_runaway_whatever_the_reason_code(code, table):
    outcome = _first(code, table, runaway_trips=(_trip(),))
    assert outcome.kind == dc.OUTCOME_RUNAWAY
    assert outcome.args["counters"] == (runaway.COUNTER_RELEASES,)
    assert any("runaway.releases_per_hour" in line for line in outcome.evidence)
    assert any("release:v1.0.6" in line for line in outcome.evidence)


def test_a_runaway_trip_decides_runaway_for_a_chain_halt():
    inputs = _inputs(subject=dc.SUBJECT_HALT, halt=_halt(), runaway_trips=(_trip(),))
    assert dc.decide(inputs).kind == dc.OUTCOME_RUNAWAY


# ---------------------------------------------------------------------------
# 5. Fail closed
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("subject", [dc.SUBJECT_BLOCK, dc.SUBJECT_NEEDS_HUMAN])
def test_task_without_a_block_record_asks(subject, table):
    outcome = dc.decide(_inputs(None, subject=subject), table=table)
    assert outcome.kind == dc.OUTCOME_INBOX and outcome.why == dc.WHY_RECORD_ABSENT


def test_unreadable_block_record_asks(table):
    outcome = dc.decide(_inputs(None, record_error="first_blocked_at 'soon' is not a time"), table=table)
    assert outcome.kind == dc.OUTCOME_INBOX and outcome.why == dc.WHY_RECORD_UNREADABLE


def test_unknown_owner_asks(table):
    outcome = dc.decide(_inputs(_record("zero_progress", owner="JANITOR")), table=table)
    assert outcome.kind == dc.OUTCOME_INBOX and outcome.why == dc.WHY_FAIL_CLOSED


def test_code_with_no_row_asks(tmp_path):
    only = tmp_path / "block-classifier.yaml"
    only.write_text(
        "reason_codes:\n  missing_readme:\n    decision: {inbox: 'restore it?', answer: 'restore it'}\n",
        encoding="utf-8",
    )
    outcome = dc.decide(_inputs(_record("zero_progress")), table=bc.load_table(only))
    assert outcome.kind == dc.OUTCOME_INBOX and outcome.why == dc.WHY_FAIL_CLOSED


def test_table_that_does_not_load_asks_and_a_row_without_a_decision_is_refused(tmp_path, monkeypatch):
    bad = tmp_path / "block-classifier.yaml"
    bad.write_text(
        "reason_codes:\n  zero_progress:\n    action: requeue_owner\n    params: {note: retry}\n"
        "    max_attempts: 1\n    cooldown_s: 0\n    backoff: none\n    verification: none\n    preconditions: []\n",
        encoding="utf-8",
    )
    with pytest.raises(bc.TableError, match="missing key 'decision'"):
        bc.load_table(bad)
    monkeypatch.setattr(bc, "default_table_path", lambda: str(bad))
    outcome = dc.decide(_inputs(_record("zero_progress")))
    assert outcome.kind == dc.OUTCOME_INBOX and outcome.why == dc.WHY_TABLE_INVALID


def test_unknown_subject_asks():
    outcome = dc.decide(_inputs(subject="meteor"))
    assert outcome.kind == dc.OUTCOME_INBOX and outcome.why == dc.WHY_SUBJECT_UNKNOWN


def test_inbox_answer_without_a_kanban_root_prints_the_root_variable(table):
    outcome = dc.decide(_inputs(_record("missing_readme"), kanban_root=""), table=table)
    assert '"${PGAI_AGENT_KANBAN_ROOT_PATH:-$HOME/pgai_agent_kanban}"/scripts/task-status.sh' in outcome.how_to_answer


# ---------------------------------------------------------------------------
# 6. Purity
# ---------------------------------------------------------------------------


@pytest.fixture()
def sandbox(tmp_path, monkeypatch) -> pathlib.Path:
    root = tmp_path / "kanban"
    (root / "tmp").mkdir(parents=True)
    monkeypatch.delenv("PGAI_TASKS_DIR", raising=False)
    monkeypatch.delenv("PGAI_PROJECT_ROOT", raising=False)
    monkeypatch.setenv("KANBAN_ROOT", str(root))
    monkeypatch.setenv("PGAI_AGENT_KANBAN_ROOT_PATH", str(root))
    monkeypatch.setenv("PGAI_AGENT_KANBAN_TEMP_DIR", str(root / "tmp"))
    return root


def _snapshot(root: pathlib.Path) -> dict:
    return {
        str(path.relative_to(root)): (hashlib.sha256(path.read_bytes()).hexdigest(), path.stat().st_mtime_ns)
        for path in sorted(root.rglob("*"))
        if path.is_file()
    }


def _trap_side_effects(monkeypatch) -> list:
    """Make every file write and process start raise, recording the attempt."""
    attempts: list = []
    real_open, real_os_open = builtins.open, os.open

    def guarded_open(file, mode="r", *args, **kwargs):
        if any(flag in mode for flag in "wax+"):
            attempts.append(f"open({file!r}, {mode!r})")
            raise AssertionError(f"decide opened {file!r} for writing")
        return real_open(file, mode, *args, **kwargs)

    def guarded_os_open(path, flags, *args, **kwargs):
        if flags & (os.O_WRONLY | os.O_RDWR | os.O_CREAT | os.O_APPEND | os.O_TRUNC):
            attempts.append(f"os.open({path!r})")
            raise AssertionError(f"decide opened {path!r} for writing")
        return real_os_open(path, flags, *args, **kwargs)

    def refuse(name):
        def _refused(*args, **kwargs):
            attempts.append(name)
            raise AssertionError(f"decide started a process through {name}")

        return _refused

    monkeypatch.setattr(builtins, "open", guarded_open)
    monkeypatch.setattr(io, "open", guarded_open)
    monkeypatch.setattr(os, "open", guarded_os_open)
    monkeypatch.setattr(subprocess, "Popen", refuse("subprocess.Popen"))
    for name in ("system", "fork", "execv", "execve", "execvp", "posix_spawn", "posix_spawnp", "spawnv", "popen"):
        if hasattr(os, name):
            monkeypatch.setattr(os, name, refuse(f"os.{name}"))
    return attempts


def test_decide_changes_no_file_and_starts_no_process(sandbox, monkeypatch):
    project_dir = sandbox / "projects" / _PROJECT
    task_dir = project_dir / "tasks" / _TASK
    task_dir.mkdir(parents=True)
    (sandbox / "logs").mkdir()
    (project_dir / "release-state.md").write_text(
        "# Release State\n\n## Active RC\nv1.2.3\n\n## RC Opened At\n2026-01-01T00:00:00Z\n\n"
        "## RC Opened By Task\nCM-20260101-001-open-rc\n",
        encoding="utf-8",
    )
    (task_dir / "status.md").write_text(
        "# Status\n\n## Task\n"
        + _TASK
        + "\n\n## State\nBLOCKED\n\n## Needs Human\nyes\n\n## Block Record\n"
        + _record("zero_progress", attempts=1).to_body()
        + "\n",
        encoding="utf-8",
    )
    halt = project_dir / "HALT"
    halt.write_text(
        "# HALT created by CM at 2026-01-01T10:00:00Z\n"
        + "\n".join(provenance_lines(WRITER_CM_RELEASE, "cm-release.trigger-5.squash-conflict", "5"))
        + "\n",
        encoding="utf-8",
    )
    ctx = OpsContext(kanban_root=str(sandbox))
    for minute in range(4):
        runaway.record_event(
            ctx,
            _PROJECT,
            runaway.COUNTER_REASON_REPEATS,
            key="",
            subject=_TASK,
            detail="zero_progress",
            at=f"2026-01-01T11:0{minute}:00+00:00",
        )

    # Gather every input the way a caller would: read-only, before the trap.
    status_text = (task_dir / "status.md").read_text(encoding="utf-8")
    record = read_block_record(status_text)
    provenance = classify_halt(str(halt), kanban_root=str(sandbox))
    trips = runaway.evaluate(ctx, _PROJECT, now=_NOW)
    assert record is not None and provenance.is_chain and trips, "the sandbox inputs must exercise every path"
    inputs = [
        _inputs(record, kanban_root=str(sandbox)),
        _inputs(record, subject=dc.SUBJECT_NEEDS_HUMAN, kanban_root=str(sandbox)),
        _inputs(record, kanban_root=str(sandbox), runaway_trips=trips),
        _inputs(subject=dc.SUBJECT_HALT, halt=provenance, kanban_root=str(sandbox)),
        _inputs(subject=dc.SUBJECT_HALT, halt=provenance, operator_halts=(str(sandbox / "HALT"),)),
    ] + [_inputs(_record(code), kanban_root=str(sandbox)) for code in REASON_CODES]

    before = _snapshot(sandbox)
    attempts = _trap_side_effects(monkeypatch)
    outcomes = [dc.decide(item) for item in inputs]  # the shipped table, loaded read-only
    monkeypatch.undo()

    assert attempts == [], f"decide tried to write or start a process: {attempts}"
    assert _snapshot(sandbox) == before, "decide changed a file in the sandbox"
    kinds = {outcome.kind for outcome in outcomes}
    assert kinds == {dc.OUTCOME_VERB, dc.OUTCOME_INBOX, dc.OUTCOME_RUNAWAY, dc.OUTCOME_NONE}
