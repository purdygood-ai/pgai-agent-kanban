"""
test_inbox_answer_reaches_the_planner.py
========================================
An operator's inbox answer about a bug is recorded in the bug itself, and
the PM materializer never parks an answered bug on the same question again:

  1. answer_entry on an entry whose Source is a bug appends
     ``- <answered at> <entry ID>: <answer>`` to the bug's ``## Operator
     Answers`` section (created after ``## Status``) and reopens it; a second
     answer adds a second line.
  2. An answer to an entry that names no bug (an RC) writes no section.
  3. A plan whose needs_operator names a bug that carries no answer is
     accepted and parks it (the unanswered case).
  4. A plan whose needs_operator names an answered bug is refused with
     ``materialize.needs_operator_answered``, naming the answer; no inbox
     entry is written and the bug's status is unchanged.
  5. The two-bundle loop: bundle one parks the bug on a question; the
     operator answers; bundle two re-asking is refused with nothing written,
     and bundle two ticketing the bug materializes with no new inbox entry.

Every root is a sandbox under tmp_path; bug IDs are in the fictitious
BUG-99xx range.
"""

from __future__ import annotations

import datetime
import pathlib

import pytest

from pgai_agent_kanban.lib.error_contract import parse_error_contract
from pgai_agent_kanban.ops import write as ops_write
from pgai_agent_kanban.ops.context import OpsContext
from pgai_agent_kanban.overwatch import inbox
from pgai_agent_kanban.task_status import read_section
from tests.unit.test_pm_materialize import (
    _COVERAGE_PROJECT,
    _COVERAGE_QUESTION,
    _coverage_bug,
    _coverage_materialize,
    _coverage_plan,
    _coverage_requirement,
    _coverage_sandbox,
    _coverage_snapshot,
    _coverage_status,
    _coverage_task_dirs,
    pm,
)

_T0 = datetime.datetime(2026, 10, 30, 1, 15, 0, tzinfo=datetime.UTC)
_T1 = datetime.datetime(2026, 10, 30, 2, 0, 0, tzinfo=datetime.UTC)
_BUG = "BUG-9971-uploader-retry"


def _answers(path: pathlib.Path) -> list[str]:
    return [
        line
        for line in (read_section(path.read_text(encoding="utf-8"), "Operator Answers") or "").splitlines()
        if line.strip()
    ]


def _parked(root: pathlib.Path, project_root: pathlib.Path) -> tuple:
    bug = _coverage_bug(project_root, _BUG)
    ctx = OpsContext(kanban_root=root)
    entry = ops_write.set_needs_operator(ctx, _COVERAGE_PROJECT, _BUG, _COVERAGE_QUESTION, now=_T0)
    return ctx, bug, entry


def test_an_answer_is_recorded_in_the_bug_and_reopens_it(tmp_path) -> None:
    root, project_root = _coverage_sandbox(tmp_path)
    ctx, bug, entry = _parked(root, project_root)

    inbox.answer_entry(ctx, _COVERAGE_PROJECT, entry.entry_id, "(a) three retries", now=_T0)
    inbox.answer_entry(ctx, _COVERAGE_PROJECT, entry.entry_id, "(b) five retries", now=_T1)

    text = bug.read_text(encoding="utf-8")
    assert _coverage_status(bug) == "open"
    assert _answers(bug) == [
        f"- 2026-10-30T01:15:00+00:00 {entry.entry_id}: (a) three retries",
        f"- 2026-10-30T02:00:00+00:00 {entry.entry_id}: (b) five retries",
    ]
    assert text.index("## Status") < text.index("## Operator Answers") < text.index("## Symptom")
    assert ops_write.recorded_operator_answers(bug) == _answers(bug)


def test_an_answer_about_no_bug_writes_no_section(tmp_path) -> None:
    root, project_root = _coverage_sandbox(tmp_path)
    bug = _coverage_bug(project_root, _BUG)
    ctx = OpsContext(kanban_root=root)
    entry = inbox.write_entry(
        ctx,
        _COVERAGE_PROJECT,
        source="rc:v9.9.6",
        question="Keep the RC?",
        meanwhile="Left it open.",
        how_to_answer="Answer keep or cancel.",
        now=_T0,
    )
    before = bug.read_bytes()

    inbox.answer_entry(ctx, _COVERAGE_PROJECT, entry.entry_id, "keep", now=_T0)

    assert bug.read_bytes() == before


def test_an_unanswered_bug_is_parked_by_the_plan(tmp_path) -> None:
    root, project_root = _coverage_sandbox(tmp_path)
    bug = _coverage_bug(project_root, _BUG)
    requirement = _coverage_requirement(project_root, [bug])
    plan = _coverage_plan(
        requirement, "Fix the uploader.", needs_operator=[{"item": "BUG-9971", "question": _COVERAGE_QUESTION}]
    )

    intake = pm.check_plan_intake_fields(plan, str(project_root), str(requirement))

    assert intake is not None and intake.parked == (bug.resolve(),)


def test_an_answered_bug_is_never_asked_again(tmp_path) -> None:
    root, project_root = _coverage_sandbox(tmp_path)
    ctx, bug, entry = _parked(root, project_root)
    inbox.answer_entry(ctx, _COVERAGE_PROJECT, entry.entry_id, "(a) three retries", now=_T0)
    requirement = _coverage_requirement(project_root, [bug])
    plan = _coverage_plan(
        requirement, "Fix the uploader.", needs_operator=[{"item": "BUG-9971", "question": _COVERAGE_QUESTION}]
    )

    with pytest.raises(pm.PlanIntakeRefused) as caught:
        pm.check_plan_intake_fields(plan, str(project_root), str(requirement))

    assert caught.value.error == "materialize.needs_operator_answered"
    assert "(a) three retries" in caught.value.problems[0]


def _run_dir(tmp_path: pathlib.Path, name: str) -> pathlib.Path:
    path = tmp_path / name
    path.mkdir()
    return path


def test_the_two_bundle_loop_is_broken(tmp_path) -> None:
    root, project_root = _coverage_sandbox(tmp_path)
    bug = _coverage_bug(project_root, _BUG)
    ctx = OpsContext(kanban_root=root)

    # Bundle one: the planner parks the bug on a question.
    first = _coverage_requirement(project_root, [bug])
    ask = _coverage_plan(
        first, "Fix the uploader.", needs_operator=[{"item": "BUG-9971", "question": _COVERAGE_QUESTION}]
    )
    result = _coverage_materialize(root, project_root, _run_dir(tmp_path, "bundle-one"), ask)
    assert result.returncode == 0, result.stderr
    [asked] = inbox.list_open(ctx, _COVERAGE_PROJECT)
    assert _coverage_status(bug) == "needs-operator"

    # The operator answers and closes the entry; the bug is open again.
    inbox.answer_entry(ctx, _COVERAGE_PROJECT, asked.entry_id, "(a) three retries", now=_T0)
    inbox.close_entry(ctx, _COVERAGE_PROJECT, asked.entry_id, now=_T0)
    assert _coverage_status(bug) == "open"
    first_tasks = set(_coverage_task_dirs(project_root))

    # Bundle two re-asking the same question is refused and writes nothing.
    before = _coverage_snapshot(project_root)
    reask = _coverage_plan(
        first, "Fix the uploader.", needs_operator=[{"item": "BUG-9971", "question": _COVERAGE_QUESTION}]
    )
    refused = _coverage_materialize(root, project_root, _run_dir(tmp_path, "bundle-two-reask"), reask)
    assert refused.returncode != 0, refused.stdout + refused.stderr
    contract = parse_error_contract(refused.stderr[refused.stderr.find("error:") :])
    assert contract is not None and contract["error"] == "materialize.needs_operator_answered", refused.stderr
    assert _coverage_snapshot(project_root) == before
    assert list(inbox.list_open(ctx, _COVERAGE_PROJECT)) == []

    # Bundle two ticketing the bug with the answer materializes, asking nothing.
    ticket = _coverage_plan(first, "Fix BUG-9971 with three retries, as the operator answered.")
    ticket["tasks"][0]["slug"] = "uploader-retry-answered"
    planned = _coverage_materialize(root, project_root, _run_dir(tmp_path, "bundle-two-ticket"), ticket)
    assert planned.returncode == 0, planned.stderr
    assert any(
        "uploader-retry-answered" in name for name in set(_coverage_task_dirs(project_root)) - first_tasks
    ), planned.stderr
    assert list(inbox.list_open(ctx, _COVERAGE_PROJECT)) == []
    assert _coverage_status(bug) == "open"
