"""
test_acceptance_policy.py
=========================
Behavioral unit tests for team/pm-agent/lib/acceptance_policy.py.

The policy rewrites full invocations of a project's configured runners on
CODER and WRITER tickets to the project's targeted run, appends the
targeted-run constraint and the pre-merge criterion the project's commands
produce, and leaves every other role untouched. A project that configures
no command gets none of it. The module performs no I/O, so these tests call
it directly with plain lists and a ``RunnerConfig``; most use ``KANBAN``,
the kanban project's own commands, whose texts are pinned literally below.
"""

from __future__ import annotations


import pathlib

import pytest

try:
    from pm_agent.lib import acceptance_policy as ap
except ImportError:
    import sys
    sys.path.insert(0, str(pathlib.Path(__file__).parent.parent.parent / "pm-agent"))
    from lib import acceptance_policy as ap  # type: ignore[no-redef]


# The kanban project's own [testing] commands, as its project.cfg sets them.
KANBAN = ap.RunnerConfig(
    targeted_command=(
        "bash team/scripts/run-unit-tests.sh --files {files} && "
        "bash team/scripts/run-integration-tests.sh --files {files}"
    ),
    pre_merge_command="bash team/scripts/run-unit-tests.sh --pre-merge",
)
TARGETED_RUN_CRITERION = KANBAN.targeted_criterion()
TARGETED_RUN_CONSTRAINT = KANBAN.targeted_constraint()
PRE_MERGE_CRITERION = KANBAN.pre_merge_criterion()


def _apply(role, acceptance, constraints, config=KANBAN):
    return ap.apply_acceptance_policy(role, acceptance, constraints, runner_config=config)


BARE_UNIT = "`bash team/scripts/run-unit-tests.sh` exits 0"
BARE_INTEGRATION = "`bash team/scripts/run-integration-tests.sh` exits 0."
TARGETED_UNIT = "`bash team/scripts/run-unit-tests.sh --files tests/unit/test_semver.py` exits 0"
NON_RUNNER = "The dashboard renders the new column."


# ---------------------------------------------------------------------------
# Rewrites
# ---------------------------------------------------------------------------


def test_bare_unit_runner_criterion_is_rewritten_to_targeted_run() -> None:
    result = _apply("CODER", [BARE_UNIT], [])
    assert result.acceptance_criteria == [TARGETED_RUN_CRITERION, PRE_MERGE_CRITERION]
    assert "--files" in result.acceptance_criteria[0]
    assert len(result.rewrites) == 1
    assert result.rewrites[0].field == ap.FIELD_ACCEPTANCE
    assert result.rewrites[0].original == BARE_UNIT


def test_integration_runner_criterion_is_rewritten_to_targeted_run() -> None:
    result = _apply("CODER", [BARE_INTEGRATION], [])
    assert result.acceptance_criteria == [TARGETED_RUN_CRITERION, PRE_MERGE_CRITERION]
    assert [r.original for r in result.rewrites] == [BARE_INTEGRATION]


def test_runner_constraint_is_rewritten_like_a_criterion() -> None:
    constraint = "Before merging, run bash team/scripts/run-unit-tests.sh --coverage"
    result = _apply("WRITER", [], [constraint])
    assert result.constraints[0] == TARGETED_RUN_CRITERION
    assert result.rewrites[0].field == ap.FIELD_CONSTRAINT


def test_criterion_already_passing_files_is_unchanged() -> None:
    result = _apply("CODER", [TARGETED_UNIT], [])
    assert result.acceptance_criteria == [TARGETED_UNIT, PRE_MERGE_CRITERION]
    assert result.rewrites == []


def test_non_runner_criterion_is_unchanged() -> None:
    result = _apply("CODER", [NON_RUNNER], [NON_RUNNER])
    assert result.acceptance_criteria == [NON_RUNNER, PRE_MERGE_CRITERION]
    assert result.constraints[0] == NON_RUNNER
    assert result.rewrites == []


def test_two_rewrites_to_the_same_text_collapse_to_one_criterion() -> None:
    result = _apply("CODER", [BARE_UNIT, NON_RUNNER, BARE_INTEGRATION], [])
    assert result.acceptance_criteria == [
        TARGETED_RUN_CRITERION, NON_RUNNER, PRE_MERGE_CRITERION,
    ]
    assert [r.merged for r in result.rewrites] == [False, True]


def test_criterion_order_is_preserved_around_a_rewrite() -> None:
    result = _apply("CODER", [NON_RUNNER, BARE_UNIT, "Docs updated."], [])
    assert result.acceptance_criteria == [
        NON_RUNNER, TARGETED_RUN_CRITERION, "Docs updated.", PRE_MERGE_CRITERION,
    ]


def test_input_lists_are_not_mutated() -> None:
    acceptance = [BARE_UNIT]
    constraints = ["Be careful."]
    _apply("CODER", acceptance, constraints)
    assert acceptance == [BARE_UNIT]
    assert constraints == ["Be careful."]


# ---------------------------------------------------------------------------
# Fixed constraint
# ---------------------------------------------------------------------------


def test_fixed_constraint_is_appended_to_coder_and_writer_tickets() -> None:
    for role in ("CODER", "WRITER", "coder"):
        result = _apply(role, [NON_RUNNER], ["Be careful."])
        assert result.constraints == ["Be careful.", TARGETED_RUN_CONSTRAINT], role
        assert result.constraint_added is True


def test_fixed_constraint_is_added_exactly_once_when_already_present() -> None:
    result = _apply("CODER", [], [TARGETED_RUN_CONSTRAINT])
    assert result.constraints.count(TARGETED_RUN_CONSTRAINT) == 1
    assert result.constraint_added is False


def test_fixed_constraint_match_tolerates_whitespace_differences() -> None:
    rewrapped = TARGETED_RUN_CONSTRAINT.replace(". ", ".\n  ") + "  "
    result = _apply("CODER", [], [rewrapped])
    assert result.constraints == [rewrapped]
    assert result.constraint_added is False


def test_policy_applied_to_its_own_output_changes_nothing() -> None:
    first = _apply("CODER", [BARE_UNIT, NON_RUNNER], [BARE_INTEGRATION])
    second = _apply("CODER", first.acceptance_criteria, first.constraints)
    assert second.acceptance_criteria == first.acceptance_criteria
    assert second.constraints == first.constraints
    assert second.rewrites == []
    assert second.constraint_added is False
    assert second.constraints.count(TARGETED_RUN_CONSTRAINT) == 1


# ---------------------------------------------------------------------------
# Fixed pre-merge criterion
# ---------------------------------------------------------------------------


def test_kanban_commands_yield_the_kanban_criteria_texts_unchanged() -> None:
    assert TARGETED_RUN_CRITERION == (
        "`bash team/scripts/run-unit-tests.sh --files <the test files this ticket "
        "adds or changes>` (and the integration runner the same way, if integration "
        "tests were touched) exits 0."
    )
    assert TARGETED_RUN_CONSTRAINT == (
        "Run only the tests for the files you changed using `--files`. "
        "The full gated suites run once per RC in the TESTER task."
    )
    assert PRE_MERGE_CRITERION == (
        "`bash team/scripts/run-unit-tests.sh --pre-merge` exits 0 before merging."
    )


@pytest.mark.parametrize("role", ["CODER", "WRITER", "coder", "Writer", " WRITER "])
def test_pre_merge_criterion_is_appended_last_for_coder_and_writer(role: str) -> None:
    result = _apply(role, [NON_RUNNER, BARE_UNIT], ["Be careful."])
    assert result.acceptance_criteria == [
        NON_RUNNER, TARGETED_RUN_CRITERION, PRE_MERGE_CRITERION,
    ]
    assert result.acceptance_criteria.count(PRE_MERGE_CRITERION) == 1


@pytest.mark.parametrize("plan_criteria", [
    [PRE_MERGE_CRITERION, NON_RUNNER],
    [NON_RUNNER, PRE_MERGE_CRITERION],
    [PRE_MERGE_CRITERION, NON_RUNNER, PRE_MERGE_CRITERION],
    ["  " + PRE_MERGE_CRITERION.replace(" exits", "\n   exits"), NON_RUNNER],
], ids=["first", "last", "twice", "rewrapped"])
def test_plan_supplied_pre_merge_criterion_ends_up_last_exactly_once(plan_criteria) -> None:
    result = _apply("CODER", plan_criteria, [])
    assert result.acceptance_criteria == [NON_RUNNER, PRE_MERGE_CRITERION]


def test_pre_merge_criterion_is_never_rewritten_to_the_targeted_run() -> None:
    result = _apply(
        "WRITER", [PRE_MERGE_CRITERION], [PRE_MERGE_CRITERION],
    )
    assert result.rewrites == []
    assert TARGETED_RUN_CRITERION not in result.acceptance_criteria
    assert result.acceptance_criteria == [PRE_MERGE_CRITERION]
    # A plan-authored constraint carrying the text is left as written too.
    assert result.constraints == [PRE_MERGE_CRITERION, TARGETED_RUN_CONSTRAINT]
    assert ap.format_policy_notes(result) == ""


def test_pre_merge_command_with_a_further_option_is_rewritten_as_a_full_runner() -> None:
    # A further option makes it a different run of the runner, not the
    # configured pre-merge command, so the full-runner rewrite applies.
    other = "`bash team/scripts/run-unit-tests.sh --pre-merge --base ai_main` exits 0"
    result = _apply("CODER", [other], [])
    assert result.acceptance_criteria == [TARGETED_RUN_CRITERION, PRE_MERGE_CRITERION]
    assert [r.original for r in result.rewrites] == [other]


PRE_MERGE_IN_PLAN_WORDING = (
    "Before merging, `bash team/scripts/run-unit-tests.sh --pre-merge` must exit 0"
)


@pytest.mark.parametrize("plan_criteria", [
    [PRE_MERGE_IN_PLAN_WORDING, NON_RUNNER],
    [NON_RUNNER, PRE_MERGE_IN_PLAN_WORDING],
    [PRE_MERGE_IN_PLAN_WORDING, NON_RUNNER, PRE_MERGE_CRITERION],
    ["Run bash team/scripts/run-unit-tests.sh --pre-merge\nbefore you merge.", NON_RUNNER],
], ids=["first", "last", "with-canonical", "unquoted-wrapped"])
def test_pre_merge_command_in_other_wording_is_kept_once_last_without_a_rewrite(
    plan_criteria,
) -> None:
    result = _apply("CODER", plan_criteria, [])
    assert result.acceptance_criteria == [NON_RUNNER, PRE_MERGE_CRITERION]
    assert result.acceptance_criteria.count(PRE_MERGE_CRITERION) == 1
    assert result.rewrites == []
    assert ap.format_policy_notes(result) == ""


def test_pre_merge_command_in_other_wording_is_left_as_written_in_constraints() -> None:
    result = _apply("CODER", [], [PRE_MERGE_IN_PLAN_WORDING])
    assert result.constraints == [PRE_MERGE_IN_PLAN_WORDING, TARGETED_RUN_CONSTRAINT]
    assert result.rewrites == []


@pytest.mark.parametrize("text, expected", [
    (PRE_MERGE_CRITERION, True),
    (PRE_MERGE_IN_PLAN_WORDING, True),
    ("bash  team/scripts/run-unit-tests.sh\t--pre-merge passes", True),
    ("`bash team/scripts/run-unit-tests.sh --pre-merge --base ai_main` exits 0", False),
    ("`bash team/scripts/run-unit-tests.sh --pre-merge-all` exits 0", False),
    ("`bash team/scripts/run-unit-tests.sh` exits 0", False),
    (NON_RUNNER, False),
])
def test_pre_merge_command_detection(text: str, expected: bool) -> None:
    assert KANBAN.names_pre_merge(text) is expected


def test_pre_merge_command_detection_is_false_with_no_command_configured() -> None:
    assert ap.RunnerConfig().names_pre_merge(PRE_MERGE_CRITERION) is False


@pytest.mark.parametrize("role", ["CODER", "WRITER"])
@pytest.mark.parametrize("plan_criteria", [
    [],
    [BARE_UNIT, NON_RUNNER],
    [PRE_MERGE_CRITERION, BARE_INTEGRATION],
])
def test_policy_with_pre_merge_applied_to_its_own_output_is_identical(
    role: str, plan_criteria,
) -> None:
    first = _apply(role, plan_criteria, [BARE_INTEGRATION])
    second = _apply(role, first.acceptance_criteria, first.constraints)
    assert second.acceptance_criteria == first.acceptance_criteria
    assert second.constraints == first.constraints
    assert second.rewrites == []
    assert second.acceptance_criteria[-1] == PRE_MERGE_CRITERION


@pytest.mark.parametrize("role", ["TESTER", "CM", "HUMAN", "cm"])
def test_roles_outside_the_policy_never_gain_or_lose_the_pre_merge_criterion(role: str) -> None:
    for acceptance in ([NON_RUNNER], [PRE_MERGE_CRITERION, NON_RUNNER]):
        result = _apply(role, acceptance, [NON_RUNNER])
        assert result.acceptance_criteria == acceptance
        assert result.constraints == [NON_RUNNER]


# ---------------------------------------------------------------------------
# Roles outside the policy
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("role", ["TESTER", "CM", "tester", "HUMAN", ""])
def test_roles_outside_the_policy_are_a_no_op(role: str) -> None:
    acceptance = [BARE_UNIT, NON_RUNNER]
    constraints = [BARE_INTEGRATION]
    result = _apply(role, acceptance, constraints)
    assert result.acceptance_criteria == acceptance
    assert result.constraints == constraints
    assert result.rewrites == []
    assert result.constraint_added is False
    assert ap.format_policy_notes(result) == ""


def test_none_lists_are_treated_as_empty() -> None:
    result = _apply("CODER", None, None)
    assert result.acceptance_criteria == [PRE_MERGE_CRITERION]
    assert result.constraints == [TARGETED_RUN_CONSTRAINT]


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
    assert ap.invokes_full_runner(text, KANBAN) is expected


def test_invokes_full_runner_is_false_with_no_runner_configured() -> None:
    assert ap.invokes_full_runner(BARE_UNIT) is False
    assert ap.invokes_full_runner(BARE_UNIT, ap.RunnerConfig()) is False


def test_policy_notes_record_each_rewrite_with_its_original_text() -> None:
    result = _apply("CODER", [BARE_UNIT], [BARE_INTEGRATION])
    notes = ap.format_policy_notes(result)
    assert notes.startswith("Targeted-test policy:")
    assert f'- acceptance criterion: "{BARE_UNIT}"' in notes
    assert f'- constraint: "{BARE_INTEGRATION}"' in notes


def test_policy_notes_are_empty_when_nothing_was_rewritten() -> None:
    result = _apply("CODER", [NON_RUNNER], [])
    assert result.constraint_added is True
    assert ap.format_policy_notes(result) == ""


# ---------------------------------------------------------------------------
# Per-project configuration
# ---------------------------------------------------------------------------


# A project with its own runners, the targeted one taking a flag.
OTHER = ap.RunnerConfig(
    targeted_command="npx jest --runTestsByPath {files}",
    pre_merge_command="make lint",
)


def test_project_with_both_commands_gets_exactly_its_own_two_criteria() -> None:
    # The targeted criterion enters a ticket by rewriting a full run of the
    # project's runner; the pre-merge criterion is always placed last.
    result = _apply("CODER", ["It works.", "`npx jest` exits 0", BARE_UNIT], ["Be careful."], OTHER)
    assert result.acceptance_criteria == [
        "It works.",
        "`npx jest --runTestsByPath <the test files this ticket adds or changes>` exits 0.",
        BARE_UNIT,  # not this project's runner: left as written
        "`make lint` exits 0 before merging.",
    ]
    assert result.constraints == [
        "Be careful.",
        "Run only the tests for the files you changed using `--runTestsByPath`. "
        "The full gated suites run once per RC in the TESTER task.",
    ]
    assert [r.original for r in result.rewrites] == ["`npx jest` exits 0"]
    added = [c for c in result.acceptance_criteria + result.constraints
             if c not in ("It works.", "Be careful.", BARE_UNIT)]
    for text in added:
        assert "team/scripts" not in text and "--files" not in text


@pytest.mark.parametrize("config", [
    None,
    ap.RunnerConfig(),
    ap.RunnerConfig(targeted_command="   ", pre_merge_command=""),
], ids=["none", "empty", "blank"])
@pytest.mark.parametrize("role", ["CODER", "WRITER"])
def test_project_with_no_commands_gets_no_runner_criterion_or_constraint(role, config) -> None:
    acceptance = ["It works.", BARE_UNIT, PRE_MERGE_CRITERION]
    constraints = ["Be careful.", BARE_INTEGRATION]
    result = _apply(role, acceptance, constraints, config)
    # Everything the plan wrote is left as written, a bare runner included.
    assert result.acceptance_criteria == acceptance
    assert result.constraints == constraints
    assert result.rewrites == []
    assert result.constraint_added is False
    assert ap.format_policy_notes(result) == ""


def test_project_with_no_commands_never_falls_back_to_the_kanban_texts() -> None:
    result = _apply("CODER", [], [], ap.RunnerConfig())
    assert result.acceptance_criteria == []
    assert result.constraints == []


def test_pre_merge_command_alone_adds_only_the_pre_merge_criterion() -> None:
    config = ap.RunnerConfig(pre_merge_command="scripts/check.sh --quick")
    result = _apply("CODER", [BARE_UNIT], [], config)
    assert result.acceptance_criteria == [BARE_UNIT, "`scripts/check.sh --quick` exits 0 before merging."]
    assert result.constraints == []
    assert result.rewrites == []


def test_targeted_command_alone_adds_no_pre_merge_criterion() -> None:
    config = ap.RunnerConfig(targeted_command=KANBAN.targeted_command)
    result = _apply("CODER", [BARE_UNIT], [], config)
    assert result.acceptance_criteria == [TARGETED_RUN_CRITERION]
    assert result.constraints == [TARGETED_RUN_CONSTRAINT]


def test_runner_without_its_flag_is_rewritten_on_the_configured_project_only() -> None:
    plan = "Run `bash team/scripts/run-unit-tests.sh --coverage` and confirm it exits 0."
    configured = _apply("CODER", [plan], [])
    assert configured.acceptance_criteria[0] == TARGETED_RUN_CRITERION
    assert [r.original for r in configured.rewrites] == [plan]
    unconfigured = _apply("CODER", [plan], [], ap.RunnerConfig())
    assert unconfigured.acceptance_criteria == [plan]
    assert unconfigured.rewrites == []


# A runner that takes its files as plain arguments, with no flag.
POSITIONAL = ap.RunnerConfig(targeted_command="scripts/run-unit-tests.sh {files}")
POSITIONAL_CRITERION = (
    "`scripts/run-unit-tests.sh <the test files this ticket adds or changes>` exits 0."
)
POSITIONAL_CONSTRAINT = (
    "Run only the tests for the files you changed using "
    "`scripts/run-unit-tests.sh <the test files this ticket adds or changes>`. "
    "The full gated suites run once per RC in the TESTER task."
)


def test_positional_files_runner_named_without_files_is_rewritten() -> None:
    bare = "`scripts/run-unit-tests.sh` exits 0"
    with_files = "`scripts/run-unit-tests.sh tests/unit/test_semver.py` exits 0"
    result = _apply("CODER", [bare, with_files], [], POSITIONAL)
    assert result.acceptance_criteria == [POSITIONAL_CRITERION, with_files]
    assert len(result.rewrites) == 1
    assert result.rewrites[0].original == bare
    assert result.rewrites[0].field == ap.FIELD_ACCEPTANCE
    assert result.constraints == [POSITIONAL_CONSTRAINT]


def test_positional_files_runner_policy_applied_to_its_own_output_changes_nothing() -> None:
    first = _apply("CODER", ["Run scripts/run-unit-tests.sh and confirm it passes."], [], POSITIONAL)
    second = _apply("CODER", first.acceptance_criteria, first.constraints, POSITIONAL)
    assert first.acceptance_criteria == [POSITIONAL_CRITERION]
    assert second.acceptance_criteria == first.acceptance_criteria
    assert second.constraints == first.constraints
    assert second.rewrites == []


@pytest.mark.parametrize("text, expected", [
    ("`scripts/run-unit-tests.sh` exits 0", True),
    ("Run scripts/run-unit-tests.sh and confirm it passes.", True),
    ("`scripts/run-unit-tests.sh --verbose` exits 0", True),
    ("`scripts/run-unit-tests.sh tests/unit/test_x.py` exits 0", False),
    ("`scripts/run-unit-tests.sh -v tests/unit/test_x.py` exits 0", False),
    ("scripts/run-unit-tests.sh test_x.py, then commit", False),
    ("`scripts/run-unit-tests.sh 'tests/unit/test_*.py'` exits 0", False),
    (POSITIONAL_CRITERION, False),
    (NON_RUNNER, False),
], ids=["code-span", "prose", "option-only", "path", "option-then-path",
        "file-name", "glob", "own-criterion", "non-runner"])
def test_positional_files_runner_detection(text: str, expected: bool) -> None:
    assert ap.invokes_full_runner(text, POSITIONAL) is expected


@pytest.mark.parametrize("template, script, flag", [
    ("bash team/scripts/run-unit-tests.sh --files {files}", "run-unit-tests.sh", "--files"),
    ("bash team/scripts/run-unit-tests.sh --files={files}", "run-unit-tests.sh", "--files"),
    ("scripts/run-unit-tests.sh {files}", "run-unit-tests.sh", ""),
    ("scripts/run-unit-tests.sh --files", "run-unit-tests.sh", "--files"),
    ("python3 -m pytest {files}", "pytest", ""),
], ids=["flag", "flag-equals", "positional", "no-placeholder", "module"])
def test_targeted_template_parses_into_runner_and_flag(template, script, flag) -> None:
    (runner,) = ap.RunnerConfig(targeted_command=template).runners()
    assert (runner.script, runner.flag) == (script, flag)
    assert ap.FILES_PLACEHOLDER in runner.template


def test_and_joined_value_is_one_command_unless_every_part_takes_files() -> None:
    config = ap.RunnerConfig(targeted_command="cd web && npx jest --runTestsByPath {files}")
    (runner,) = config.runners()
    assert runner.script == "jest"
    assert config.targeted_criterion() == (
        "`cd web && npx jest --runTestsByPath <the test files this ticket adds or changes>` exits 0."
    )


def test_each_further_runner_is_named_after_the_first() -> None:
    config = ap.RunnerConfig(targeted_command=(
        "tools/unit.sh -f {files} && tools/run_e2e_tests.py -f {files}"
    ))
    assert config.targeted_criterion() == (
        "`tools/unit.sh -f <the test files this ticket adds or changes>` "
        "(and the e2e runner the same way, if e2e tests were touched) exits 0."
    )


def test_policy_module_holds_no_kanban_runner_path() -> None:
    source = pathlib.Path(ap.__file__).read_text(encoding="utf-8")
    assert "team/scripts/run-unit-tests" not in source
    assert "run-integration-tests" not in source
