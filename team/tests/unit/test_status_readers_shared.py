"""
test_status_readers_shared.py
=============================
Behaviour-preservation fixtures for the ``## State`` / ``## Section`` readers
that now read through ``pgai_agent_kanban.task_status``.

Each module below used to carry its own regex. The fixtures feed every
migrated reader the variants the writers and hand-edits produce, and pin:

  * canonical, blank-line and trailing-text values: the migrated reader
    returns exactly what the module's earlier private reader returned (the
    value is spelled out per reader below);
  * inline (``## State: DONE``), bold (``**DONE**``) and heading-with-
    trailing-spaces values, and an empty bug ``## Status``: some earlier
    readers missed these (an empty value, the value with its ``**``
    wrapper, or the next line's ``##``); the shared reader returns the
    value itself. This is the "extend the shared reader rather than regress
    the caller" direction;
  * a ``###`` subheading inside a body stays part of that body, as it did
    for the line-based dashboard, changelog and pm_status readers.
"""

from __future__ import annotations

import importlib.util
import pathlib
import sys

import pytest

from pgai_agent_kanban import task_status
from pgai_agent_kanban.board import read_task_state_from_status_md
from pgai_agent_kanban.cm.changelog_writer import _parse_section_value
from pgai_agent_kanban.dashboard import queue_details, scan_attention, scan_human_approvals
from pgai_agent_kanban.halt_after.drain import _parse_status_md
from pgai_agent_kanban.lib.bug_filing import read_status as bug_read_status
from pgai_agent_kanban.ops.resolve import _read_intake_state, _read_task_state
from pgai_agent_kanban.reports.project_summary import TaskEvent

_TEAM_DIR = pathlib.Path(__file__).resolve().parent.parent.parent  # team/


def _load_script_module(name: str, path: pathlib.Path):
    """Import a script that is not on a package path, from its own file."""
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:
        raise ImportError(f"Cannot load spec from {path}")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)  # type: ignore[union-attr]
    return mod


_promote = _load_script_module(
    "promote_bundled_items_shared_readers", _TEAM_DIR / "scripts" / "cm" / "promote_bundled_items.py"
)
_pm_status = _load_script_module(
    "pm_status_shared_readers", _TEAM_DIR / "pm-agent" / "pm_status.py"
)
if str(_TEAM_DIR / "scripts" / "lib") not in sys.path:
    sys.path.insert(0, str(_TEAM_DIR / "scripts" / "lib"))
import metrics_aggregator  # noqa: E402

# ---------------------------------------------------------------------------
# Writer variants of a status.md carrying State DONE
# ---------------------------------------------------------------------------

_CANONICAL = "# Status\n\n## State\nDONE\n\n## Role\nCODER\n"
_BLANK_LINES = "# Status\n\n## State\n\n\nDONE\n\n## Role\nCODER\n"
_HEADING_TRAILING_SPACE = "# Status\n\n## State  \nDONE\n\n## Role\nCODER\n"
_TRAILING_TEXT = "# Status\n\n## State\nDONE (merged)\n\n## Role\nCODER\n"
_BOLD = "# Status\n\n## State\n**DONE**\n\n## Role\nCODER\n"
_INLINE = "# Status\n\n## State: DONE\n\n## Role\nCODER\n"

# Variants every earlier reader already read as DONE (or "DONE (merged)").
_AGREED = {
    "canonical": _CANONICAL,
    "blank_lines": _BLANK_LINES,
}


def _write_status(tmp_path: pathlib.Path, text: str, task_id: str = "CODER-20261003-001-x") -> pathlib.Path:
    task_dir = tmp_path / "tasks" / task_id
    task_dir.mkdir(parents=True, exist_ok=True)
    status = task_dir / "status.md"
    status.write_text(text, encoding="utf-8")
    return status


# ---------------------------------------------------------------------------
# Full-value readers: resolve, drain, dashboard _read_field, project_summary,
# metrics_aggregator. The earlier readers returned the value line as written.
# ---------------------------------------------------------------------------


def _full_value_readers(tmp_path: pathlib.Path):
    """(name, callable(text) -> state value) for each full-value reader."""

    def via_resolve(text: str) -> str:
        return _read_task_state(_write_status(tmp_path, text))

    def via_project_summary(text: str) -> str:
        return TaskEvent(_write_status(tmp_path, text)).state

    return [
        ("ops.resolve", via_resolve),
        ("halt_after.drain", lambda text: _parse_status_md(text)["State"]),
        ("dashboard.scan_attention", lambda text: scan_attention._read_field(text, "State")),
        ("dashboard.scan_human_approvals", lambda text: scan_human_approvals._read_field(text, "State")),
        ("dashboard.queue_details", lambda text: queue_details._read_field(text, "State")),
        ("reports.project_summary", via_project_summary),
        ("metrics_aggregator", lambda text: metrics_aggregator.read_field_from_markdown(text, "State")),
    ]


@pytest.mark.parametrize("variant", sorted(_AGREED))
def test_full_value_readers_agree_with_earlier_readers_on_canonical_and_blank_lines(
    tmp_path: pathlib.Path, variant: str
) -> None:
    for name, reader in _full_value_readers(tmp_path):
        assert reader(_AGREED[variant]) == "DONE", f"{name} misread the {variant} variant"


def test_full_value_readers_keep_trailing_text_as_written(tmp_path: pathlib.Path) -> None:
    for name, reader in _full_value_readers(tmp_path):
        assert reader(_TRAILING_TEXT).upper() == "DONE (MERGED)", f"{name} dropped trailing text"


@pytest.mark.parametrize("text", [_BOLD, _INLINE, _HEADING_TRAILING_SPACE], ids=["bold", "inline", "heading_space"])
def test_full_value_readers_read_bold_inline_and_spaced_headings_as_the_bare_value(
    tmp_path: pathlib.Path, text: str
) -> None:
    for name, reader in _full_value_readers(tmp_path):
        assert reader(text) == "DONE", f"{name} did not unwrap the variant to DONE"


def test_resolve_keeps_the_state_case_as_written(tmp_path: pathlib.Path) -> None:
    """The resolve shim prints the state verbatim, so case is not normalised."""
    assert _read_task_state(_write_status(tmp_path, "## State\ndone\n")) == "done"


@pytest.mark.parametrize(
    "body, expected",
    [
        ("## Status\nrunning\n", "running"),
        ("## Status\n\n\nopen\n", "open"),
        ("## Status: done\n", "done"),
        ("## State\ndone\n", "done"),
        ("# Title\n", ""),
    ],
)
def test_resolve_intake_reader_reads_status_then_state(tmp_path: pathlib.Path, body: str, expected: str) -> None:
    intake = tmp_path / "BUG-0001-x.md"
    intake.write_text(body, encoding="utf-8")
    assert _read_intake_state(intake) == expected


# ---------------------------------------------------------------------------
# First-token readers: the board classifier, promote_bundled_items, bug_filing.
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "text",
    [_CANONICAL, _BLANK_LINES, _TRAILING_TEXT, _HEADING_TRAILING_SPACE, _BOLD, _INLINE],
    ids=["canonical", "blank_lines", "trailing_text", "heading_space", "bold", "inline"],
)
def test_board_classifier_reads_the_first_state_token(tmp_path: pathlib.Path, text: str) -> None:
    _write_status(tmp_path, text)
    assert read_task_state_from_status_md(tmp_path / "tasks", "CODER-20261003-001-x") == "DONE"


@pytest.mark.parametrize(
    "text, shipped",
    [
        (_CANONICAL, True),
        (_BLANK_LINES, True),
        (_TRAILING_TEXT, True),
        (_BOLD, True),
        (_INLINE, True),
        ("## State\nWORKING\n", False),
        ("## Summary\nno state\n", False),
    ],
    ids=["canonical", "blank_lines", "trailing_text", "bold", "inline", "working", "absent"],
)
def test_promote_counts_only_done_tasks(tmp_path: pathlib.Path, text: str, shipped: bool) -> None:
    status = _write_status(tmp_path, text)
    (status.parent / "README.md").write_text(
        "## Inputs\n- BUG-0042-example\n\n## Release Version\nv9.9.9\n", encoding="utf-8"
    )
    found = _promote.derive_shipped_item_ids_from_tasks(tmp_path / "tasks", "v9.9.9")
    assert bool(found) is shipped


@pytest.mark.parametrize(
    "text, expected",
    [
        ("## Status\nrunning\n", "running"),
        ("## Status\n\n\nOPEN\n", "open"),
        ("## Status\ndone (shipped in v2.27.0)\n", "done"),
        ("## Status\n**wont-do**\n", "wont-do"),
        ("## Status: running\n", "running"),
        ("# Bug\n\n## Symptom\nbroken\n", "open"),
        ("## Status\n\n## Symptom\nbroken\n", "open"),
    ],
    ids=["canonical", "blank_lines", "trailing_text", "bold", "inline", "absent", "empty"],
)
def test_bug_filing_reads_the_first_status_token_lowercased(text: str, expected: str) -> None:
    assert bug_read_status(text) == expected


# ---------------------------------------------------------------------------
# Section-body readers: dashboard lines, changelog_writer, pm_status.
# ---------------------------------------------------------------------------

_SUMMARY_WITH_SUBHEADING = (
    "## State\nBLOCKED\n\n## Summary\nfirst line\n\n### Detail\nsecond line\n\n## Blockers\nnone\n"
)


def test_dashboard_section_lines_keep_a_subheading_inside_the_body() -> None:
    assert scan_attention._read_section(_SUMMARY_WITH_SUBHEADING, "Summary", max_lines=5) == [
        "first line",
        "### Detail",
        "second line",
    ]
    assert task_status.section_lines(_SUMMARY_WITH_SUBHEADING, "Summary", max_lines=2) == [
        "first line",
        "### Detail",
    ]
    assert task_status.section_lines(_SUMMARY_WITH_SUBHEADING, "Absent") == []


def test_changelog_section_body_keeps_a_subheading_and_stops_at_the_next_section() -> None:
    assert _parse_section_value(_SUMMARY_WITH_SUBHEADING, "Summary") == "first line\n\n### Detail\nsecond line"
    assert _parse_section_value(_SUMMARY_WITH_SUBHEADING, "Absent") == ""


def test_pm_status_fields_match_the_section_bodies(tmp_path: pathlib.Path) -> None:
    status = _write_status(tmp_path, _SUMMARY_WITH_SUBHEADING)
    fields = _pm_status.get_status_fields(status)
    assert fields == {
        "State": "BLOCKED",
        "Summary": "first line\n\n### Detail\nsecond line",
        "Blockers": "none",
    }


def test_pm_status_desync_check_reads_a_bold_lowercase_state_as_done(tmp_path: pathlib.Path) -> None:
    _write_status(tmp_path, "## State\n**done**\n")
    queues = tmp_path / "tasks" / "queues"
    queues.mkdir(parents=True)
    (queues / "coder_backlog.md").write_text("- [x] CODER-20261003-001-x\n", encoding="utf-8")
    assert _pm_status.check_desync(tmp_path / "tasks", tmp_path) == []


# ---------------------------------------------------------------------------
# The shared writer and reader agree on a ### subheading inside a body.
# ---------------------------------------------------------------------------


def test_set_section_replaces_a_body_that_contains_a_subheading(tmp_path: pathlib.Path) -> None:
    status = _write_status(tmp_path, _SUMMARY_WITH_SUBHEADING)
    task_status.set_section(status, "Summary", "rewritten")
    text = status.read_text(encoding="utf-8")
    assert task_status.read_section(text, "Summary") == "rewritten"
    assert "### Detail" not in text
    assert task_status.read_section(text, "Blockers") == "none"


def test_read_state_upper_false_preserves_case(tmp_path: pathlib.Path) -> None:
    status = _write_status(tmp_path, "## State\nWorking\n")
    assert task_status.read_state(status) == "WORKING"
    assert task_status.read_state(status, upper=False) == "Working"
