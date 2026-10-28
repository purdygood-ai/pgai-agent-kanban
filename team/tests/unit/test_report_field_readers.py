"""
test_report_field_readers.py
============================
The token roll-up's and the project summary's markdown field readers read
through the one section reader (``pgai_agent_kanban.task_status``), so they
follow its rules: an empty field reads as empty -- never as the value of
the section after it -- and a ``## `` line inside a fenced code block is
not a heading.

Readers covered:
  pm-agent/aggregate_tokens.py
      read_field_from_markdown (``None`` for an empty field), and read_agent,
      which falls through to the task README when status.md's field is empty
  pgai_agent_kanban/reports/project_summary.py
      _extract_field (``""`` for an empty field), and BugItem, whose Status
      reads ``unknown`` when the field is empty

The token roll-up module is imported from its file, as the live install
runs it. Fixtures live under pytest's tmp_path; nothing reads or writes a
live task folder or kanban install.
"""

from __future__ import annotations

import importlib.util
import pathlib

import pytest

from pgai_agent_kanban.reports.project_summary import BugItem, _extract_field
from pgai_agent_kanban.task_status import section_value

_TEAM_DIR = pathlib.Path(__file__).resolve().parents[2]
_AGGREGATE_TOKENS = _TEAM_DIR / "pm-agent" / "aggregate_tokens.py"


def _load_aggregate_tokens():
    spec = importlib.util.spec_from_file_location("aggregate_tokens_under_test", _AGGREGATE_TOKENS)
    assert spec is not None and spec.loader is not None, f"cannot load {_AGGREGATE_TOKENS}"
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


aggregate_tokens = _load_aggregate_tokens()

_EMPTY_AGENT = "# Status\n\n## Agent\n\n## Role\nCODER\n"
_FENCED_FIRST = (
    "# Status\n\n"
    "## Notes\n"
    "```\n"
    "## Agent\n"
    "FAKE\n"
    "```\n\n"
    "## Agent\n"
    "REAL\n"
)


# ---------------------------------------------------------------------------
# An empty field reads as empty
# ---------------------------------------------------------------------------


def test_token_rollup_reads_an_empty_field_as_none():
    assert aggregate_tokens.read_field_from_markdown(_EMPTY_AGENT, "Agent") is None
    assert aggregate_tokens.read_field_from_markdown(_EMPTY_AGENT, "Role") == "CODER"


def test_project_summary_reads_an_empty_field_as_empty():
    assert _extract_field(_EMPTY_AGENT, "Agent") == ""
    assert _extract_field(_EMPTY_AGENT, "Role") == "CODER"


@pytest.mark.parametrize(
    "text",
    [
        pytest.param("## Agent\n## Role\nCODER\n", id="no-blank-line"),
        pytest.param("## Agent\n\n\n## Role\nCODER\n", id="blank-lines"),
        pytest.param("## Agent  \n\n## Role\nCODER\n", id="heading-trailing-space"),
        pytest.param("## Agent\r\n\r\n## Role\r\nCODER\r\n", id="crlf"),
    ],
)
def test_an_empty_field_never_reads_the_next_section_in_any_spacing(text):
    assert aggregate_tokens.read_field_from_markdown(text, "Agent") is None
    assert _extract_field(text, "Agent") == ""
    assert section_value(text, "Agent") == ""


def test_token_rollup_agent_falls_through_to_the_readme_when_status_agent_is_empty(tmp_path):
    task_dir = tmp_path / "tasks" / "CODER-20260101-001-example"
    task_dir.mkdir(parents=True)
    (task_dir / "status.md").write_text(_EMPTY_AGENT, encoding="utf-8")
    (task_dir / "README.md").write_text("# Task\n\n## Role\nTESTER\n", encoding="utf-8")

    assert aggregate_tokens.read_agent(task_dir) == "tester"


def test_project_summary_bug_with_an_empty_status_reads_unknown(tmp_path):
    bug = tmp_path / "BUG-0001-sample.md"
    bug.write_text("# BUG-0001-sample\n\n## Status\n\n## Symptom\nIt broke.\n", encoding="utf-8")

    item = BugItem(bug)

    assert item.status == "unknown"
    assert item.symptom == "It broke."


# ---------------------------------------------------------------------------
# A heading inside a fenced code block is not a heading
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("fence", ["```", "~~~"])
def test_both_readers_ignore_a_fenced_heading(fence):
    text = _FENCED_FIRST.replace("```", fence)

    assert aggregate_tokens.read_field_from_markdown(text, "Agent") == "REAL"
    assert _extract_field(text, "Agent") == "REAL"


def test_a_fenced_heading_inside_a_section_does_not_end_it():
    text = "## Symptom\nThe probe printed:\n```\n## State\nDONE\n```\n\n## Expected\nNothing.\n"

    assert _extract_field(text, "Symptom") == "The probe printed:"
    assert _extract_field(text, "State") == ""
    assert aggregate_tokens.read_field_from_markdown(text, "State") is None


# ---------------------------------------------------------------------------
# Populated fields read as before
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "text, heading, expected",
    [
        pytest.param("## Release Version\nv1.4.0\n", "Release Version", "v1.4.0", id="plain"),
        pytest.param("## Role\n\n\nCODER\n\n## Next\nx\n", "Role", "CODER", id="blank-lines-before-value"),
        pytest.param("## STATUS\nopen\n", "Status", "open", id="any-letter-case"),
        pytest.param("## Severity\nhigh\n## Status\nopen\n", "Severity", "high", id="stops-at-next-heading"),
        pytest.param("## Symptom\n<!-- guidance -->\nIt broke.\n", "Symptom", "It broke.", id="comment-skipped"),
    ],
)
def test_populated_fields_read_their_own_value(text, heading, expected):
    assert aggregate_tokens.read_field_from_markdown(text, heading) == expected
    assert _extract_field(text, heading) == expected


def test_project_summary_skips_a_subheading_to_the_value_in_the_same_section():
    text = "## Symptom\n### Observed\nIt broke.\n\n## Expected\nNothing.\n"

    assert _extract_field(text, "Symptom") == "It broke."
