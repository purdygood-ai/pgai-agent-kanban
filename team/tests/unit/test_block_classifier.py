"""
test_block_classifier.py
========================
Behaviour of OVERWATCH's block-triage classifier,
pgai_agent_kanban.overwatch.block_classifier, and the shipped table it reads,
block-classifier.yaml.

Covered:
  1. Every shipped row, for a representative Block Record, decides the
     expected action with the expected max_attempts, cool-down, backoff and
     verification (the expected values are written out here from the
     requirements, not read back from the table), and the table carries a
     row for exactly the Block Record reason_code vocabulary.
  2. Fail closed: an unknown reason_code, an absent one, an unreadable
     record, unparseable evidence, an unknown owner, an unmet precondition
     and a schema-invalid table each decide escalate.
  3. attempts >= max_attempts decides escalate; now < cooldown_until decides
     not_yet; rows with a backoff wait 15 -> 30 -> 60 and 5 -> 10 -> 20
     minutes before their three attempts.
  4. The classifier only decides: classifying leaves status.md unchanged,
     and a guard asserts that the classifier, the block-triage executor and
     the check-block-triage sweep check invoke none of the forbidden git
     mutations or the reset verb, with a seeded violation proving the guard
     fires on each.
  5. The CLI prints the decision as JSON and exits 0, through the module's
     own file path, from a foreign cwd, under a scrubbed environment.

Every fixture lives under pytest's tmp_path.
"""

from __future__ import annotations

import datetime
import json
import pathlib
import py_compile
import re
import subprocess
import sys

import pytest

from pgai_agent_kanban.overwatch import block_classifier as bc
from pgai_agent_kanban.overwatch.block_record import REASON_CODES, BlockRecord
from tests.fixtures.scrubbed_subprocess_env import build_scrubbed_env

_MODULE_FILE = pathlib.Path(bc.__file__).resolve()

_NOW = datetime.datetime(2026, 1, 1, 12, 0, 0, tzinfo=datetime.timezone.utc)
_NOW_ISO = "2026-01-01T12:00:00+00:00"
_PAST_ISO = "2026-01-01T11:00:00+00:00"
_FUTURE_ISO = "2026-01-01T12:10:00+00:00"

_BRANCH_EVIDENCE = "worktree=gone; branch=ai_feature/example; ahead=2; merged=no"

# The requirements' table, written out by hand: reason_code -> (action,
# max_attempts, cooldown_s, backoff as (factor, cap_s) or None, verification).
_EXPECTED_ROWS = {
    "per_task_timeout": ("requeue_owner", 1, 1800, None, "next_outcome_not_timeout"),
    "provider_refusal": ("requeue_owner", 3, 900, (2, 3600), "zero_progress_not_readvanced"),
    "zero_progress": ("requeue_owner", 1, 1800, None, "zero_progress_not_readvanced"),
    "transient_api_error": ("requeue_owner", 3, 300, (2, 1200), None),
    "gate_merge_not_found": ("recompute_gate", 1, 0, None, "gate_passes"),
    "gate_dirty_worktree": ("requeue_owner", 1, 0, None, "gate_passes"),
    "gate_empty_commit": ("requeue_owner", 1, 0, None, "gate_passes"),
    "worktree_create_failed": ("prune_worktree_then_requeue", 1, 300, None, "next_create_succeeds"),
    "ship_policy_halt": ("defer_to_resolution_stage", "stage", 0, None, "stage_decision_event"),
    "agent_operator_data_action": ("run_allowlisted_verb", 1, 0, None, "rerun_reports_no_changes"),
    "missing_readme": ("escalate", 0, 0, None, None),
    "invalid_assignment": ("escalate", 0, 0, None, None),
    "invalid_effort_config": ("escalate", 0, 0, None, None),
    "invalid_state": ("escalate", 0, 0, None, None),
    "branch_prefix_failed": ("escalate", 0, 0, None, None),
    "workflow_type_unparseable": ("escalate", 0, 0, None, None),
    "plugin_load_failed": ("escalate", 0, 0, None, None),
    "source_branch_missing": ("escalate", 0, 0, None, None),
    "gate_merge_evidence_not_found": ("escalate", 0, 0, None, None),
    "agent_unclassified": ("escalate", 0, 0, None, None),
}

_RESTAMP_EVIDENCE = "verb=restamp-fixed-in; project=pgai-agent-kanban; version=v2.28.0; bugs=BUG-0412,BUG-0413"

# A representative record and caller facts per row: (owner_role, evidence,
# cooldown_until, facts). A backoff row's representative record has already
# waited out its first window (cooldown_until in the past).
_REPRESENTATIVE = {
    "per_task_timeout": ("CODER", _BRANCH_EVIDENCE + "; timeout_s=1800", "none", {"killed_at": "1800s"}),
    "provider_refusal": ("CODER", "span_s=12; signature=model-not-found", _PAST_ISO, {}),
    "transient_api_error": ("WRITER", "batch_log=none", _PAST_ISO, {}),
    "gate_merge_not_found": ("WRITER", _BRANCH_EVIDENCE, "none", {"branch_ancestor_of_source": True}),
    "gate_empty_commit": ("CODER", _BRANCH_EVIDENCE, "none", {"shas": "abc1234 def5678"}),
    "worktree_create_failed": (
        "CODER",
        _BRANCH_EVIDENCE,
        "none",
        {"worktree_residue_in_base": True, "residue_holds_no_unique_commits": True, "source_branch_exists": True},
    ),
    "ship_policy_halt": ("CM", "none", "none", {}),
    "agent_operator_data_action": ("CM", _RESTAMP_EVIDENCE, "none", {"action_targets_verified": True}),
}

# Action params a row must carry (a subset check; notes are matched by substring).
_EXPECTED_PARAMS = {
    "per_task_timeout": {"resume": "branch", "snapshot": ("commits_ahead",), "note": "killed at 1800s"},
    "provider_refusal": {"reset_zero_progress": True, "note": "signature=model-not-found"},
    "zero_progress": {"note": "without forward progress"},
    "gate_dirty_worktree": {"resume": "branch", "snapshot": ("dirty_patch",), "note": "dirty.patch"},
    "gate_empty_commit": {"resume": "branch", "note": "abc1234 def5678"},
    "worktree_create_failed": {"note": "pruned"},
    "agent_operator_data_action": {"note": "restamp-fixed-in"},
}


def _record(
    reason_code: str,
    *,
    owner_role: str = "CODER",
    attempts: int = 0,
    cooldown_until: str = "none",
    evidence: str = "none",
    resume: str = "fresh",
) -> BlockRecord:
    return BlockRecord(
        reason_code=reason_code,
        owner_role=owner_role,
        attempts=attempts,
        first_blocked_at="2026-01-01T09:00:00+00:00",
        last_blocked_at="2026-01-01T09:00:00+00:00",
        cooldown_until=cooldown_until,
        evidence=evidence,
        resume=resume,
    )


def _status_text(record_body: str) -> str:
    return (
        "# Status\n\n## Task\nCODER-20260101-001-classifier-task\n\n## State\nBLOCKED\n\n"
        "## Needs Human\nyes\n\n## Block Record\n" + record_body + "\n"
    )


@pytest.fixture(scope="module")
def table() -> bc.ClassifierTable:
    return bc.load_table()


def _representative_decision(code: str, table: bc.ClassifierTable) -> bc.Decision:
    owner, evidence, cooldown, facts = _REPRESENTATIVE.get(code, ("CODER", _BRANCH_EVIDENCE, "none", {}))
    record = _record(code, owner_role=owner, evidence=evidence, cooldown_until=cooldown)
    return bc.decide(record, table, facts=facts, now=_NOW)


# ---------------------------------------------------------------------------
# 1. The shipped rows
# ---------------------------------------------------------------------------


def test_shipped_table_has_a_row_for_exactly_the_block_record_vocabulary(table):
    assert set(table.rules) == set(REASON_CODES) == set(_EXPECTED_ROWS), (
        "the shipped table must classify every Block Record reason_code and nothing else"
    )


@pytest.mark.parametrize("code", sorted(_EXPECTED_ROWS))
def test_shipped_row_decides_its_action_ceiling_cooldown_and_verification(code, table):
    action, max_attempts, cooldown_s, backoff, verification = _EXPECTED_ROWS[code]
    decision = _representative_decision(code, table)
    assert decision.action == action, f"{code}: {decision.why}: {decision.detail}"
    assert decision.reason_code == code
    assert decision.max_attempts == max_attempts
    assert decision.cooldown_s == cooldown_s
    got_backoff = (decision.backoff.factor, decision.backoff.cap_s) if decision.backoff else None
    assert got_backoff == backoff
    assert decision.verification == verification
    for key, expected in _EXPECTED_PARAMS.get(code, {}).items():
        if key == "note":
            assert expected in decision.params["note"], f"{code} note: {decision.params['note']!r}"
        else:
            assert decision.params.get(key) == expected, f"{code} param {key}"
    if action == "escalate":
        assert decision.why == bc.WHY_ALWAYS_HUMAN
        assert decision.attempt is None
    else:
        assert decision.why == bc.WHY_ROW_MATCHED
        assert decision.attempt == 1


def test_merge_not_found_with_a_clean_merge_tree_requeues_the_owner_to_merge(table):
    record = _record("gate_merge_not_found", owner_role="CODER", evidence=_BRANCH_EVIDENCE)
    decision = bc.decide(
        record, table, facts={"branch_ancestor_of_source": False, "merge_tree_clean": True}, now=_NOW
    )
    assert decision.action == "requeue_owner"
    assert decision.params == {"note": "merge only, then re-complete", "resume": "branch"}


def test_merge_not_found_reads_an_already_merged_branch_from_the_evidence(table):
    record = _record("gate_merge_not_found", evidence="worktree=gone; branch=b; ahead=0; merged=yes")
    assert bc.decide(record, table, now=_NOW).action == "recompute_gate"


def test_merge_not_found_with_a_conflicting_merge_tree_escalates(table):
    record = _record("gate_merge_not_found", evidence=_BRANCH_EVIDENCE)
    decision = bc.decide(
        record, table, facts={"branch_ancestor_of_source": False, "merge_tree_clean": False}, now=_NOW
    )
    assert decision.action == "escalate"
    assert decision.why == bc.WHY_PRECONDITION_UNMET
    assert "merge_tree_clean" in decision.detail


def test_merge_only_requeue_is_refused_for_an_owner_that_does_not_merge(table):
    record = _record("gate_merge_not_found", owner_role="TESTER", evidence=_BRANCH_EVIDENCE)
    facts = {"merge_tree_clean": True, "owner_is_coder_or_writer": True}
    decision = bc.decide(record, table, facts=facts, now=_NOW)
    assert decision.action == "escalate", "the owner check reads the record, never the caller's claim"


def test_requeue_note_fills_an_unknown_placeholder_with_unknown(table):
    record = _record("gate_empty_commit", evidence=_BRANCH_EVIDENCE)
    decision = bc.decide(record, table, now=_NOW)
    assert "(unknown)" in decision.params["note"]


def test_row_with_no_cooldown_records_none_after_acting(table):
    decision = bc.decide(_record("gate_dirty_worktree", evidence=_BRANCH_EVIDENCE), table, now=_NOW)
    assert decision.cooldown_until == "none"


def test_row_with_a_cooldown_records_the_window_end_after_acting(table):
    decision = bc.decide(_record("per_task_timeout", evidence=_BRANCH_EVIDENCE), table, now=_NOW)
    assert decision.cooldown_until == "2026-01-01T12:30:00+00:00"


def test_resolution_stage_row_is_not_capped_by_the_classifier(table):
    decision = bc.decide(_record("ship_policy_halt", owner_role="CM", attempts=7), table, now=_NOW)
    assert decision.action == "defer_to_resolution_stage"
    assert decision.max_attempts == "stage"


# ---------------------------------------------------------------------------
# 2. Fail closed
# ---------------------------------------------------------------------------


def _record_body(**overrides: str) -> str:
    fields = {
        "reason_code": "zero_progress",
        "owner_role": "CODER",
        "attempts": "0",
        "first_blocked_at": "2026-01-01T09:00:00+00:00",
        "last_blocked_at": "2026-01-01T09:00:00+00:00",
        "cooldown_until": "none",
        "evidence": _BRANCH_EVIDENCE,
        "resume": "fresh",
    }
    fields.update(overrides)
    return "\n".join(f"{k}: {v}" for k, v in fields.items() if v is not None)


def test_well_formed_record_text_classifies_to_its_row():
    decision = bc.classify(_status_text(_record_body()), now=_NOW)
    assert decision.action == "requeue_owner"


@pytest.mark.parametrize(
    "body, why",
    [
        pytest.param(_record_body(reason_code="made_up_code"), bc.WHY_REASON_UNKNOWN, id="unknown-reason-code"),
        pytest.param(_record_body(reason_code=""), bc.WHY_REASON_ABSENT, id="empty-reason-code"),
        pytest.param(_record_body(reason_code=None), bc.WHY_RECORD_UNREADABLE, id="missing-reason-code-line"),
        pytest.param(_record_body(attempts="many"), bc.WHY_RECORD_UNREADABLE, id="malformed-record"),
        pytest.param(
            _record_body(evidence="please restamp the bugs by hand"), bc.WHY_EVIDENCE_UNPARSEABLE, id="prose-evidence"
        ),
        pytest.param(_record_body(owner_role="JANITOR"), bc.WHY_OWNER_UNKNOWN, id="unknown-owner-role"),
    ],
)
def test_unreadable_or_unknown_record_escalates(body, why):
    decision = bc.classify(_status_text(body), now=_NOW)
    assert decision.action == "escalate"
    assert decision.why == why, decision.detail


def test_status_without_a_block_record_escalates():
    decision = bc.classify("# Status\n\n## State\nBLOCKED\n", now=_NOW)
    assert (decision.action, decision.why) == ("escalate", bc.WHY_RECORD_ABSENT)


def test_unreadable_status_file_escalates(tmp_path):
    decision = bc.classify_status_file(tmp_path / "no-such-status.md", now=_NOW)
    assert (decision.action, decision.why) == ("escalate", bc.WHY_RECORD_UNREADABLE)


@pytest.mark.parametrize(
    "code, evidence, facts",
    [
        pytest.param(
            "worktree_create_failed",
            _BRANCH_EVIDENCE,
            {"worktree_residue_in_base": True, "source_branch_exists": True},
            id="residue-commits-unproven",
        ),
        pytest.param(
            "worktree_create_failed",
            _BRANCH_EVIDENCE,
            {"worktree_residue_in_base": True, "residue_holds_no_unique_commits": False, "source_branch_exists": True},
            id="residue-holds-unique-commits",
        ),
        pytest.param(
            "worktree_create_failed",
            _BRANCH_EVIDENCE,
            {"worktree_residue_in_base": True, "residue_holds_no_unique_commits": True, "source_branch_exists": False},
            id="source-branch-missing",
        ),
        pytest.param(
            "worktree_create_failed",
            _BRANCH_EVIDENCE,
            {"worktree_residue_in_base": "yes", "residue_holds_no_unique_commits": True, "source_branch_exists": True},
            id="non-boolean-fact",
        ),
        pytest.param(
            "agent_operator_data_action",
            "verb=restamp-fixed-in; project=pgai-agent-kanban; version=2.28; bugs=BUG-0412",
            {"action_targets_verified": True},
            id="malformed-version",
        ),
        pytest.param(
            "agent_operator_data_action",
            "verb=delete-everything; project=pgai-agent-kanban",
            {"action_targets_verified": True},
            id="verb-not-allowlisted",
        ),
        pytest.param(
            "agent_operator_data_action",
            "none",
            {"action_targets_verified": True, "action_args_valid": True},
            id="no-typed-args",
        ),
        pytest.param(
            "agent_operator_data_action", _RESTAMP_EVIDENCE, {"action_targets_verified": False}, id="bug-not-done"
        ),
    ],
)
def test_unmet_precondition_escalates(code, evidence, facts, table):
    decision = bc.decide(_record(code, evidence=evidence), table, facts=facts, now=_NOW)
    assert decision.action == "escalate"
    assert decision.why == bc.WHY_PRECONDITION_UNMET, decision.detail


_VALID_ROW = (
    "    action: requeue_owner\n    params: {note: retry}\n    max_attempts: 1\n"
    "    cooldown_s: 0\n    backoff: none\n    verification: none\n    preconditions: []\n"
)


@pytest.mark.parametrize(
    "text",
    [
        pytest.param("reason_codes:\n  zero_progress:\n" + _VALID_ROW.replace("requeue_owner", "rebase_it"), id="unknown-action"),
        pytest.param("reason_codes:\n  zero_progress:\n" + _VALID_ROW.replace("requeue_owner", "not_yet"), id="not-yet-as-action"),
        pytest.param("reason_codes:\n  zero_progress:\n" + _VALID_ROW + "    surprise: 1\n", id="extra-key"),
        pytest.param("reason_codes:\n  zero_progress:\n" + _VALID_ROW.replace("    backoff: none\n", ""), id="missing-key"),
        pytest.param("reason_codes:\n  zero_progress:\n" + _VALID_ROW.replace("max_attempts: 1", "max_attempts: -1"), id="negative-max-attempts"),
        pytest.param("reason_codes:\n  zero_progress:\n" + _VALID_ROW.replace("max_attempts: 1", "max_attempts: true"), id="boolean-max-attempts"),
        pytest.param("reason_codes:\n  zero_progress:\n" + _VALID_ROW.replace("max_attempts: 1", "max_attempts: stage"), id="stage-ceiling-on-requeue"),
        pytest.param("reason_codes:\n  zero_progress:\n" + _VALID_ROW.replace("cooldown_s: 0", "cooldown_s: soon"), id="non-numeric-cooldown"),
        pytest.param("reason_codes:\n  zero_progress:\n" + _VALID_ROW.replace("backoff: none", "backoff: {factor: 2}"), id="incomplete-backoff"),
        pytest.param("reason_codes:\n  zero_progress:\n" + _VALID_ROW.replace("verification: none", "verification: looks_fine"), id="unknown-verification"),
        pytest.param("reason_codes:\n  zero_progress:\n" + _VALID_ROW.replace("preconditions: []", "preconditions: [moon_is_full]"), id="unknown-precondition"),
        pytest.param("reason_codes:\n  zero_progress:\n" + _VALID_ROW.replace("{note: retry}", "{}"), id="requeue-without-note"),
        pytest.param("reason_codes:\n  zero_progress:\n" + _VALID_ROW.replace("{note: retry}", "{note: retry, force: true}"), id="unknown-param"),
        pytest.param("reason_codes:\n  made_up_code:\n" + _VALID_ROW, id="row-for-unknown-reason-code"),
        pytest.param("reason_codes:\n  zero_progress:\n" + _VALID_ROW + "  zero_progress:\n" + _VALID_ROW, id="duplicate-row"),
        pytest.param("rows:\n  - zero_progress\n", id="wrong-top-level"),
        pytest.param("reason_codes: [zero_progress]\n", id="rows-not-a-mapping"),
        pytest.param("reason_codes:\n  zero_progress: [\n", id="not-yaml"),
    ],
)
def test_schema_invalid_table_is_refused_and_classifies_as_escalate(text, tmp_path):
    bad = tmp_path / "block-classifier.yaml"
    bad.write_text(text, encoding="utf-8")
    with pytest.raises(bc.TableError) as excinfo:
        bc.load_table(bad)
    assert str(bad) in str(excinfo.value), "the refusal names the table file"
    decision = bc.classify(_status_text(_record_body()), now=_NOW, table_path=bad)
    assert (decision.action, decision.why) == ("escalate", bc.WHY_TABLE_INVALID)


def test_minimal_valid_table_loads(tmp_path):
    good = tmp_path / "block-classifier.yaml"
    good.write_text("reason_codes:\n  zero_progress:\n" + _VALID_ROW, encoding="utf-8")
    decision = bc.classify(_status_text(_record_body()), now=_NOW, table_path=good)
    assert (decision.action, decision.params["note"]) == ("requeue_owner", "retry")


def test_missing_table_file_classifies_as_escalate(tmp_path):
    decision = bc.classify(_status_text(_record_body()), now=_NOW, table_path=tmp_path / "absent.yaml")
    assert (decision.action, decision.why) == ("escalate", bc.WHY_TABLE_INVALID)


# ---------------------------------------------------------------------------
# 3. Attempts and cool-downs
# ---------------------------------------------------------------------------

_CAPPED_ROWS = sorted(
    code for code, (action, cap, *_rest) in _EXPECTED_ROWS.items() if action != "escalate" and cap != "stage"
)


@pytest.mark.parametrize("code", _CAPPED_ROWS)
def test_attempts_at_the_ceiling_escalate(code, table):
    owner, evidence, _cooldown, facts = _REPRESENTATIVE.get(code, ("CODER", _BRANCH_EVIDENCE, "none", {}))
    cap = _EXPECTED_ROWS[code][1]
    record = _record(code, owner_role=owner, evidence=evidence, attempts=cap, cooldown_until=_FUTURE_ISO)
    decision = bc.decide(record, table, facts=facts, now=_NOW)
    assert (decision.action, decision.why) == ("escalate", bc.WHY_ATTEMPTS_EXHAUSTED)


def test_task_inside_its_cooldown_window_gets_no_action(table):
    record = _record("provider_refusal", attempts=1, cooldown_until=_FUTURE_ISO, evidence="span_s=12")
    decision = bc.decide(record, table, now=_NOW)
    assert decision.action == bc.NOT_YET
    assert decision.why == bc.WHY_COOLING_DOWN
    assert decision.not_before == _FUTURE_ISO
    assert decision.cooldown_until is None, "an existing window is left as it is"
    assert decision.params == {} and decision.attempt is None


def test_task_whose_cooldown_has_passed_is_acted_on(table):
    record = _record("provider_refusal", attempts=1, cooldown_until=_PAST_ISO, evidence="span_s=12")
    decision = bc.decide(record, table, now=_NOW)
    assert (decision.action, decision.attempt) == ("requeue_owner", 2)


@pytest.mark.parametrize(
    "code, expected_waits",
    [("provider_refusal", [900, 1800, 3600]), ("transient_api_error", [300, 600, 1200])],
)
def test_backoff_rows_wait_a_growing_window_before_each_attempt(code, expected_waits, table):
    """Walk one block through its attempts: each window is waited out, then acted on."""
    now = _NOW
    first = bc.decide(_record(code, evidence="span_s=12"), table, now=now)
    assert first.action == bc.NOT_YET and first.why == bc.WHY_FIRST_COOLDOWN
    waits, cooldown_until, attempts = [], first.cooldown_until, 0
    waits.append(int((datetime.datetime.fromisoformat(cooldown_until) - now).total_seconds()))
    while True:
        now = datetime.datetime.fromisoformat(cooldown_until)
        decision = bc.decide(
            _record(code, evidence="span_s=12", attempts=attempts, cooldown_until=cooldown_until), table, now=now
        )
        if decision.action == "escalate":
            break
        assert decision.action == "requeue_owner"
        attempts = decision.attempt
        cooldown_until = decision.cooldown_until
        if attempts < 3:
            waits.append(int((datetime.datetime.fromisoformat(cooldown_until) - now).total_seconds()))
    assert waits == expected_waits
    assert attempts == 3 and decision.why == bc.WHY_ATTEMPTS_EXHAUSTED


def test_now_given_as_iso_text_is_read():
    decision = bc.classify(_status_text(_record_body(cooldown_until=_FUTURE_ISO)), now=_NOW_ISO)
    assert decision.action == bc.NOT_YET


# ---------------------------------------------------------------------------
# 4. Decision only: no side effects
# ---------------------------------------------------------------------------


def test_classifying_leaves_status_file_unchanged(tmp_path):
    status = tmp_path / "status.md"
    status.write_text(_status_text(_record_body(reason_code="provider_refusal")), encoding="utf-8")
    before = status.read_bytes(), status.stat().st_mtime_ns
    decision = bc.classify_status_file(status, now=_NOW)
    assert decision.action == bc.NOT_YET and decision.cooldown_until is not None
    assert (status.read_bytes(), status.stat().st_mtime_ns) == before


# Intentional pattern-scan: block triage — the classifier, the executor that
# acts on its decisions, its requeue, gate, worktree-residue,
# transient-backoff, allowlisted-verb and stage-deferral helpers, and the
# sweep check that runs the executor — must
# never mutate a repository or reset a task, whatever form the call would
# take — a shell string, an argv list, or a call to the reset verb. Every line
# of each file is read.
_FORBIDDEN_INVOCATIONS = (
    re.compile(r"\bgit\s+(?:-C\s+\S+\s+)?(?:merge|commit|checkout|push)\b"),
    re.compile(r"""['"](?:merge|commit|checkout|push)['"]"""),
    re.compile(r"\bbranch\s+-D\b"),
    re.compile(r"""['"]-D['"]"""),
    re.compile(r"\breset_item\b"),
    re.compile(r"^\s*(?:import|from)\s+(?:subprocess|os)\b"),
    re.compile(r"\bos\.system\s*\("),
)


def _forbidden_lines(path: pathlib.Path) -> list:
    return [
        f"{path.name}:{n}: {line.strip()}"
        for n, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1)
        if any(p.search(line) for p in _FORBIDDEN_INVOCATIONS)
    ]


_TEAM_DIR = _MODULE_FILE.parents[2]
_TRIAGE_FILES = (
    _MODULE_FILE,
    _TEAM_DIR / "pgai_agent_kanban" / "overwatch" / "block_triage.py",
    _TEAM_DIR / "pgai_agent_kanban" / "overwatch" / "block_requeue.py",
    _TEAM_DIR / "pgai_agent_kanban" / "overwatch" / "block_gate.py",
    _TEAM_DIR / "pgai_agent_kanban" / "overwatch" / "worktree_residue.py",
    _TEAM_DIR / "pgai_agent_kanban" / "overwatch" / "transient_backoff.py",
    _TEAM_DIR / "pgai_agent_kanban" / "overwatch" / "allowlisted_verbs.py",
    _TEAM_DIR / "pgai_agent_kanban" / "overwatch" / "block_stage_deferral.py",
    _TEAM_DIR / "scripts" / "lib" / "overwatch-checks" / "check-block-triage.sh",
)


@pytest.mark.parametrize("path", _TRIAGE_FILES, ids=lambda p: p.name)
def test_block_triage_code_invokes_no_git_mutation_or_reset(path):
    assert path.is_file(), f"guarded file is missing: {path}"
    hits = _forbidden_lines(path)
    assert not hits, "block triage must not run git mutations or reset a task: " + ", ".join(hits)


@pytest.mark.parametrize(
    "seeded",
    [
        'subprocess.run(["git", "-C", repo, "merge", branch])',
        "os.system('git commit -m x')",
        "run('git branch -D ' + name)",
        "ops.write.reset_item(task)",
        "import subprocess",
    ],
)
@pytest.mark.parametrize("path", _TRIAGE_FILES, ids=lambda p: p.name)
def test_forbidden_invocation_guard_fires_on_a_seeded_violation(seeded, path, tmp_path):
    copy = tmp_path / path.name
    copy.write_text(path.read_text(encoding="utf-8") + "\n" + seeded + "\n", encoding="utf-8")
    assert _forbidden_lines(copy), f"the guard did not report the seeded line {seeded!r} in {path.name}"


def test_classifier_module_compiles(tmp_path):
    py_compile.compile(str(_MODULE_FILE), cfile=str(tmp_path / "block_classifier.pyc"), doraise=True)


# ---------------------------------------------------------------------------
# 5. The CLI
# ---------------------------------------------------------------------------


def _run_cli(tmp_path: pathlib.Path, *args: str) -> subprocess.CompletedProcess:
    cwd = tmp_path / "elsewhere"
    cwd.mkdir(exist_ok=True)
    env = build_scrubbed_env(
        home_dir=tmp_path / "home",
        extra_env={"PGAI_AGENT_KANBAN_TEMP_DIR": str(tmp_path / "kanban-temp")},
    )
    return subprocess.run(
        [sys.executable, str(_MODULE_FILE), *args],
        capture_output=True, text=True, timeout=60, env=env, cwd=str(cwd),
    )


def test_cli_prints_the_decision_as_json(tmp_path):
    status = tmp_path / "status.md"
    status.write_text(
        _status_text(_record_body(reason_code="gate_merge_not_found", owner_role="WRITER")), encoding="utf-8"
    )
    facts = tmp_path / "facts.json"
    facts.write_text(json.dumps({"merge_tree_clean": True}), encoding="utf-8")
    result = _run_cli(tmp_path, "classify", str(status), "--facts", str(facts), "--now", _NOW_ISO)
    assert result.returncode == 0, result.stderr
    decision = json.loads(result.stdout)
    assert decision["action"] == "requeue_owner"
    assert decision["params"] == {"note": "merge only, then re-complete", "resume": "branch"}
    assert decision["verification"] == "gate_passes"


def test_cli_unreadable_facts_file_decides_escalate(tmp_path):
    status = tmp_path / "status.md"
    status.write_text(_status_text(_record_body()), encoding="utf-8")
    facts = tmp_path / "facts.json"
    facts.write_text("{not json", encoding="utf-8")
    result = _run_cli(tmp_path, "classify", str(status), "--facts", str(facts))
    assert result.returncode == 0, result.stderr
    assert json.loads(result.stdout)["why"] == bc.WHY_FACTS_UNREADABLE


def test_cli_help_describes_every_classify_argument(tmp_path):
    result = _run_cli(tmp_path, "classify", "--help")
    assert result.returncode == 0, result.stderr
    for flag in ("STATUS_FILE", "--facts", "--now", "--table"):
        assert flag in result.stdout
