"""
test_acceptance_policy.py
=========================
Behavioral unit tests for team/pm-agent/lib/acceptance_policy.py.

The policy rewrites full gated-runner invocations on CODER and WRITER
tickets to targeted ``--files`` runs, appends one fixed constraint and one
fixed pre-merge criterion, and leaves every other role untouched. The module performs no I/O, so these
tests call it directly with plain lists.
"""

from __future__ import annotations

import pytest

try:
    from pm_agent.lib import acceptance_policy as ap
except ImportError:
    import pathlib
    import sys
    sys.path.insert(0, str(pathlib.Path(__file__).parent.parent.parent / "pm-agent"))
    from lib import acceptance_policy as ap  # type: ignore[no-redef]


BARE_UNIT = "`bash team/scripts/run-unit-tests.sh` exits 0"
BARE_INTEGRATION = "`bash team/scripts/run-integration-tests.sh` exits 0."
TARGETED_UNIT = "`bash team/scripts/run-unit-tests.sh --files tests/unit/test_semver.py` exits 0"
NON_RUNNER = "The dashboard renders the new column."


# ---------------------------------------------------------------------------
# Rewrites
# ---------------------------------------------------------------------------


def test_bare_unit_runner_criterion_is_rewritten_to_targeted_run() -> None:
    result = ap.apply_acceptance_policy("CODER", [BARE_UNIT], [])
    assert result.acceptance_criteria == [ap.TARGETED_RUN_CRITERION, ap.PRE_MERGE_CRITERION]
    assert "--files" in result.acceptance_criteria[0]
    assert len(result.rewrites) == 1
    assert result.rewrites[0].field == ap.FIELD_ACCEPTANCE
    assert result.rewrites[0].original == BARE_UNIT


def test_integration_runner_criterion_is_rewritten_to_targeted_run() -> None:
    result = ap.apply_acceptance_policy("CODER", [BARE_INTEGRATION], [])
    assert result.acceptance_criteria == [ap.TARGETED_RUN_CRITERION, ap.PRE_MERGE_CRITERION]
    assert [r.original for r in result.rewrites] == [BARE_INTEGRATION]


def test_runner_constraint_is_rewritten_like_a_criterion() -> None:
    constraint = "Before merging, run bash team/scripts/run-unit-tests.sh --coverage"
    result = ap.apply_acceptance_policy("WRITER", [], [constraint])
    assert result.constraints[0] == ap.TARGETED_RUN_CRITERION
    assert result.rewrites[0].field == ap.FIELD_CONSTRAINT


def test_criterion_already_passing_files_is_unchanged() -> None:
    result = ap.apply_acceptance_policy("CODER", [TARGETED_UNIT], [])
    assert result.acceptance_criteria == [TARGETED_UNIT, ap.PRE_MERGE_CRITERION]
    assert result.rewrites == []


def test_non_runner_criterion_is_unchanged() -> None:
    result = ap.apply_acceptance_policy("CODER", [NON_RUNNER], [NON_RUNNER])
    assert result.acceptance_criteria == [NON_RUNNER, ap.PRE_MERGE_CRITERION]
    assert result.constraints[0] == NON_RUNNER
    assert result.rewrites == []


def test_two_rewrites_to_the_same_text_collapse_to_one_criterion() -> None:
    result = ap.apply_acceptance_policy("CODER", [BARE_UNIT, NON_RUNNER, BARE_INTEGRATION], [])
    assert result.acceptance_criteria == [
        ap.TARGETED_RUN_CRITERION, NON_RUNNER, ap.PRE_MERGE_CRITERION,
    ]
    assert [r.merged for r in result.rewrites] == [False, True]


def test_criterion_order_is_preserved_around_a_rewrite() -> None:
    result = ap.apply_acceptance_policy("CODER", [NON_RUNNER, BARE_UNIT, "Docs updated."], [])
    assert result.acceptance_criteria == [
        NON_RUNNER, ap.TARGETED_RUN_CRITERION, "Docs updated.", ap.PRE_MERGE_CRITERION,
    ]


def test_input_lists_are_not_mutated() -> None:
    acceptance = [BARE_UNIT]
    constraints = ["Be careful."]
    ap.apply_acceptance_policy("CODER", acceptance, constraints)
    assert acceptance == [BARE_UNIT]
    assert constraints == ["Be careful."]


# ---------------------------------------------------------------------------
# Fixed constraint
# ---------------------------------------------------------------------------


def test_fixed_constraint_is_appended_to_coder_and_writer_tickets() -> None:
    for role in ("CODER", "WRITER", "coder"):
        result = ap.apply_acceptance_policy(role, [NON_RUNNER], ["Be careful."])
        assert result.constraints == ["Be careful.", ap.TARGETED_RUN_CONSTRAINT], role
        assert result.constraint_added is True


def test_fixed_constraint_is_added_exactly_once_when_already_present() -> None:
    result = ap.apply_acceptance_policy("CODER", [], [ap.TARGETED_RUN_CONSTRAINT])
    assert result.constraints.count(ap.TARGETED_RUN_CONSTRAINT) == 1
    assert result.constraint_added is False


def test_fixed_constraint_match_tolerates_whitespace_differences() -> None:
    rewrapped = ap.TARGETED_RUN_CONSTRAINT.replace(". ", ".\n  ") + "  "
    result = ap.apply_acceptance_policy("CODER", [], [rewrapped])
    assert result.constraints == [rewrapped]
    assert result.constraint_added is False


def test_policy_applied_to_its_own_output_changes_nothing() -> None:
    first = ap.apply_acceptance_policy("CODER", [BARE_UNIT, NON_RUNNER], [BARE_INTEGRATION])
    second = ap.apply_acceptance_policy("CODER", first.acceptance_criteria, first.constraints)
    assert second.acceptance_criteria == first.acceptance_criteria
    assert second.constraints == first.constraints
    assert second.rewrites == []
    assert second.constraint_added is False
    assert second.constraints.count(ap.TARGETED_RUN_CONSTRAINT) == 1


# ---------------------------------------------------------------------------
# Fixed pre-merge criterion
# ---------------------------------------------------------------------------


def test_pre_merge_criterion_text_is_the_fixed_runner_invocation() -> None:
    assert ap.PRE_MERGE_CRITERION == (
        "`bash team/scripts/run-unit-tests.sh --pre-merge` exits 0 before merging."
    )


@pytest.mark.parametrize("role", ["CODER", "WRITER", "coder", "Writer", " WRITER "])
def test_pre_merge_criterion_is_appended_last_for_coder_and_writer(role: str) -> None:
    result = ap.apply_acceptance_policy(role, [NON_RUNNER, BARE_UNIT], ["Be careful."])
    assert result.acceptance_criteria == [
        NON_RUNNER, ap.TARGETED_RUN_CRITERION, ap.PRE_MERGE_CRITERION,
    ]
    assert result.acceptance_criteria.count(ap.PRE_MERGE_CRITERION) == 1


@pytest.mark.parametrize("plan_criteria", [
    [ap.PRE_MERGE_CRITERION, NON_RUNNER],
    [NON_RUNNER, ap.PRE_MERGE_CRITERION],
    [ap.PRE_MERGE_CRITERION, NON_RUNNER, ap.PRE_MERGE_CRITERION],
    ["  " + ap.PRE_MERGE_CRITERION.replace(" exits", "\n   exits"), NON_RUNNER],
], ids=["first", "last", "twice", "rewrapped"])
def test_plan_supplied_pre_merge_criterion_ends_up_last_exactly_once(plan_criteria) -> None:
    result = ap.apply_acceptance_policy("CODER", plan_criteria, [])
    assert result.acceptance_criteria == [NON_RUNNER, ap.PRE_MERGE_CRITERION]


def test_pre_merge_criterion_is_never_rewritten_to_the_targeted_run() -> None:
    result = ap.apply_acceptance_policy(
        "WRITER", [ap.PRE_MERGE_CRITERION], [ap.PRE_MERGE_CRITERION],
    )
    assert result.rewrites == []
    assert ap.TARGETED_RUN_CRITERION not in result.acceptance_criteria
    assert result.acceptance_criteria == [ap.PRE_MERGE_CRITERION]
    # A plan-authored constraint carrying the text is left as written too.
    assert result.constraints == [ap.PRE_MERGE_CRITERION, ap.TARGETED_RUN_CONSTRAINT]
    assert ap.format_policy_notes(result) == ""


def test_other_pre_merge_wording_is_still_rewritten_as_a_full_runner() -> None:
    # Only the fixed text is exempt; any other bare-runner wording keeps
    # the existing rewrite.
    other = "`bash team/scripts/run-unit-tests.sh --pre-merge --base ai_main` exits 0"
    result = ap.apply_acceptance_policy("CODER", [other], [])
    assert result.acceptance_criteria == [ap.TARGETED_RUN_CRITERION, ap.PRE_MERGE_CRITERION]
    assert [r.original for r in result.rewrites] == [other]


@pytest.mark.parametrize("role", ["CODER", "WRITER"])
@pytest.mark.parametrize("plan_criteria", [
    [],
    [BARE_UNIT, NON_RUNNER],
    [ap.PRE_MERGE_CRITERION, BARE_INTEGRATION],
])
def test_policy_with_pre_merge_applied_to_its_own_output_is_identical(
    role: str, plan_criteria,
) -> None:
    first = ap.apply_acceptance_policy(role, plan_criteria, [BARE_INTEGRATION])
    second = ap.apply_acceptance_policy(role, first.acceptance_criteria, first.constraints)
    assert second.acceptance_criteria == first.acceptance_criteria
    assert second.constraints == first.constraints
    assert second.rewrites == []
    assert second.acceptance_criteria[-1] == ap.PRE_MERGE_CRITERION


@pytest.mark.parametrize("role", ["TESTER", "CM", "HUMAN", "cm"])
def test_roles_outside_the_policy_never_gain_or_lose_the_pre_merge_criterion(role: str) -> None:
    for acceptance in ([NON_RUNNER], [ap.PRE_MERGE_CRITERION, NON_RUNNER]):
        result = ap.apply_acceptance_policy(role, acceptance, [NON_RUNNER])
        assert result.acceptance_criteria == acceptance
        assert result.constraints == [NON_RUNNER]


# ---------------------------------------------------------------------------
# Roles outside the policy
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("role", ["TESTER", "CM", "tester", "HUMAN", ""])
def test_roles_outside_the_policy_are_a_no_op(role: str) -> None:
    acceptance = [BARE_UNIT, NON_RUNNER]
    constraints = [BARE_INTEGRATION]
    result = ap.apply_acceptance_policy(role, acceptance, constraints)
    assert result.acceptance_criteria == acceptance
    assert result.constraints == constraints
    assert result.rewrites == []
    assert result.constraint_added is False
    assert ap.format_policy_notes(result) == ""


def test_none_lists_are_treated_as_empty() -> None:
    result = ap.apply_acceptance_policy("CODER", None, None)
    assert result.acceptance_criteria == [ap.PRE_MERGE_CRITERION]
    assert result.constraints == [ap.TARGETED_RUN_CONSTRAINT]


# ---------------------------------------------------------------------------
# Detection and notes
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("text, expected", [
    (BARE_UNIT, True),
    (BARE_INTEGRATION, True),
    ("run_unit_tests.sh exits 0", True),
    (TARGETED_UNIT, False),
    ("bash team/scripts/run-unit-tests.sh --files=tests/unit/test_x.py", False),
    ("run-unit-tests.sh --files-from list.txt", True),
    (NON_RUNNER, False),
    ("run-unit-tests is mentioned without the script suffix", False),
])
def test_invokes_full_runner_detection(text: str, expected: bool) -> None:
    assert ap.invokes_full_runner(text) is expected


def test_policy_notes_record_each_rewrite_with_its_original_text() -> None:
    result = ap.apply_acceptance_policy("CODER", [BARE_UNIT], [BARE_INTEGRATION])
    notes = ap.format_policy_notes(result)
    assert notes.startswith("Targeted-test policy:")
    assert f'- acceptance criterion: "{BARE_UNIT}"' in notes
    assert f'- constraint: "{BARE_INTEGRATION}"' in notes


def test_policy_notes_are_empty_when_nothing_was_rewritten() -> None:
    result = ap.apply_acceptance_policy("CODER", [NON_RUNNER], [])
    assert result.constraint_added is True
    assert ap.format_policy_notes(result) == ""
