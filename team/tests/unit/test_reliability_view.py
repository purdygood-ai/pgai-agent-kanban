"""
test_reliability_view.py
========================
The reliability streak renderer,
``pgai_agent_kanban.metrics.reliability_view`` — the one renderer the
rc-metrics pane's Reliability block and ``metrics-report.sh
--reliability`` share.

Covered behaviour:

  * the cause text for task blocks (Needs Human / BLOCKED, reason in
    words, the task's role when the reason says nothing), for HALTs by who
    raised them, and for any other source;
  * streak lines: the label, RCs and whole hours aligned within a
    project's two lines, the UTC ``since`` time, ``open since`` for an open
    block, the day-only anchor and ``(no block on record)`` with no block,
    and the bare ``(no block on record)`` for an empty ledger;
  * the 70-column limit: the detailed cause when it fits, else its short
    form, else an ellipsis cut;
  * placeholders for a missing file and for one that does not parse or
    lacks a field the lines need;
  * the pane section's header, blank lines, escapes and empty-project
    placeholder, and its place between Current RC and the footer of the
    pane renderer;
  * the CLI, run by file path: ``text`` and ``json``.

Every file is written under pytest's tmp_path.
"""

from __future__ import annotations

import json
import pathlib
import subprocess
import sys
from datetime import datetime, timedelta, UTC
from typing import Any

import pytest

from pgai_agent_kanban.lib.metrics.metrics_pane import metrics_pane_lines
from pgai_agent_kanban.metrics.reliability_view import (
    PANE_WIDTH,
    cause_candidates,
    cause_text,
    load_reliability,
    project_entry_lines,
    reliability_section_lines,
    streak_lines,
)
from tests.fixtures.scrubbed_subprocess_env import build_scrubbed_env

_TEAM_DIR = pathlib.Path(__file__).resolve().parents[2]
_MODULE_FILE = _TEAM_DIR / "pgai_agent_kanban" / "metrics" / "reliability_view.py"
_FIXTURES = _TEAM_DIR / "tests" / "fixtures" / "reliability"

RESET, BOLD, DIM, CYAN = "\033[0m", "\033[1m", "\033[2m", "\033[0;36m"

#: Render times the line tests pin: hours run from each streak's since to
#: the render time, so a test that names an hours figure names its time.
_AT_FIXTURES = datetime(2026, 10, 18, 12, 0, tzinfo=UTC)  # the fixtures' computed_at
_AT_0331 = datetime(2026, 10, 18, 3, 31, tzinfo=UTC)


def _block(**fields: Any) -> dict:
    block = {
        "time": "2026-10-17T18:40:00+00:00",
        "source": "task:CODER-20261017-012-x",
        "reason_code": None,
        "class": "any",
        "cause": "chain",
        "escalated": False,
        "resolution": None,
    }
    block.update(fields)
    return block


def _resolved(rcs: Any, hours: Any, since: str, block: dict) -> dict:
    return {"status": "resolved", "rcs": rcs, "hours": hours, "since": since, "last_block": block}


def _no_block(rcs: int, hours: float, since: Any) -> dict:
    return {"status": "no block on record", "rcs": rcs, "hours": hours, "since": since, "last_block": None}


def _write(tmp_path: pathlib.Path, content: Any, name: str = "reliability.json") -> str:
    path = tmp_path / name
    path.write_text(content if isinstance(content, str) else json.dumps(content), encoding="utf-8")
    return str(path)


# ---------------------------------------------------------------------------
# Cause text
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "fields, expected",
    [
        ({"reason_code": "provider_refusal", "class": "human"}, ["Needs Human: provider refusal", "Needs Human"]),
        ({"reason_code": "provider_refusal", "escalated": True}, ["Needs Human: provider refusal", "Needs Human"]),
        ({"reason_code": "needs_human"}, ["Needs Human: CODER task", "Needs Human"]),
        ({"reason_code": "per_task_timeout"}, ["BLOCKED: task timeout", "BLOCKED"]),
        ({"reason_code": "transient_api_error"}, ["BLOCKED: transient API error", "BLOCKED"]),
        ({"reason_code": "gate_dirty_worktree"}, ["BLOCKED: gate dirty worktree", "BLOCKED"]),
        ({"reason_code": "agent_unclassified"}, ["BLOCKED: CODER task", "BLOCKED"]),
        ({"reason_code": None, "source": "task:"}, ["BLOCKED"]),
    ],
)
def test_task_block_cause_names_who_cleared_it_and_why(fields: dict, expected: list) -> None:
    assert cause_candidates(_block(**fields)) == expected


@pytest.mark.parametrize(
    "fields, expected",
    [
        ({"reason_code": "cm-release.ship-policy.halt"}, "CM release HALT"),
        ({"reason_code": "ship_policy_halt"}, "CM release HALT"),
        ({"reason_code": "overwatch_halt_first_fix"}, "OVERWATCH HALT"),
        ({"reason_code": "zero_progress_cap"}, "zero-progress HALT"),
        ({"reason_code": "substrate_broken"}, "substrate HALT"),
        ({"reason_code": "halt_project"}, "chain HALT"),
        ({"reason_code": None}, "chain HALT"),
        ({"reason_code": "halt_global", "source": "halt:global"}, "global HALT"),
        ({"reason_code": None, "source": "halt:global"}, "global HALT"),
        ({"reason_code": "halt_project", "cause": "operator"}, "operator HALT"),
        ({"reason_code": "disk-full"}, "HALT: disk full"),
    ],
)
def test_halt_cause_names_who_raised_it(fields: dict, expected: str) -> None:
    fields.setdefault("source", "halt:project")
    assert cause_text(_block(**fields)) == expected


def test_other_source_shows_itself_with_its_reason() -> None:
    assert cause_candidates(_block(source="timeout:wake", reason_code="killed")) == [
        "timeout:wake: killed",
        "timeout:wake",
    ]
    assert cause_text(_block(source="", reason_code=None)) == "block"


# ---------------------------------------------------------------------------
# Streak lines
# ---------------------------------------------------------------------------


def test_streak_lines_align_rcs_and_hours_within_the_project() -> None:
    document = {
        "hands_off": _resolved(
            12,
            240.9,
            "2026-10-08T03:31:00+00:00",
            _block(source="halt:project", reason_code="cm-release.ship-policy.halt"),
        ),
        "clean": _resolved(2, 7.2, "2026-10-17T19:55:00+00:00", _block(reason_code="zero_progress")),
    }
    assert streak_lines(document, width=100, now=_AT_0331) == [
        "    Hands-off: 12 RCs / 240h   since 2026-10-08 03:31 (CM release HALT)",
        "    Clean:      2 RCs /   7h   since 2026-10-17 19:55 (BLOCKED: zero progress)",
    ]
    # At the pane's 70 columns both causes take their short forms.
    assert streak_lines(document, now=_AT_0331) == [
        "    Hands-off: 12 RCs / 240h   since 2026-10-08 03:31 (CM HALT)",
        "    Clean:      2 RCs /   7h   since 2026-10-17 19:55 (BLOCKED)",
    ]


def test_since_is_shown_in_utc() -> None:
    block = _block(reason_code="zero_progress")
    document = {
        "hands_off": _resolved(1, 3, "2026-10-18T05:00:00+02:00", block),
        "clean": _resolved(1, 3, "2026-10-18T03:00:00Z", block),
    }
    assert [line.split("since ")[1][:16] for line in streak_lines(document)] == ["2026-10-18 03:00", "2026-10-18 03:00"]


def test_open_block_reads_open_since_its_start() -> None:
    block = _block(time="2026-10-18T10:00:00+00:00", reason_code="needs_human")
    open_streak = {"status": "open", "rcs": 0, "hours": 0.0, "since": None, "last_block": block}
    assert streak_lines({"hands_off": open_streak, "clean": open_streak}) == [
        "    Hands-off: 0 RCs / 0h   open since 2026-10-18 10:00 (Needs Human)",
        "    Clean:     0 RCs / 0h   open since 2026-10-18 10:00 (Needs Human)",
    ]


def test_no_block_counts_from_the_first_record_day() -> None:
    streak = _no_block(2, 411.0, "2026-10-01T09:00:00+00:00")
    assert streak_lines({"hands_off": streak, "clean": streak}, now=_AT_FIXTURES) == [
        "    Hands-off: 2 RCs / 411h   since 2026-10-01 (no block on record)",
        "    Clean:     2 RCs / 411h   since 2026-10-01 (no block on record)",
    ]


def test_hours_are_elapsed_from_since_to_the_render_time_not_the_stored_figure() -> None:
    """A file last refreshed long ago still shows the hours from its streak's
    since to the moment of rendering."""
    render_at = datetime(2026, 10, 20, 9, 0, tzinfo=UTC)
    block = _block(source="halt:project", reason_code="halt_project")
    document = {
        "computed_at": "2026-10-18T10:00:00+00:00",
        "hands_off": _resolved(3, 1.0, "2026-10-18T09:00:00+00:00", block),
        "clean": _no_block(3, 1.0, "2026-10-18T09:00:00+00:00"),
    }
    hands_off, clean = streak_lines(document, now=render_at)
    assert " 48h   since 2026-10-18 09:00 " in hands_off, hands_off
    assert " 48h   since 2026-10-18 " in clean, clean


def test_hours_default_to_the_current_time() -> None:
    since = datetime.now(UTC).replace(microsecond=0) - timedelta(hours=5, minutes=30)
    block = _block(reason_code="zero_progress")
    streak = _resolved(1, 0.0, since.isoformat(), block)
    assert "/ 5h   since" in streak_lines({"hands_off": streak, "clean": streak})[0]


def test_an_open_streak_and_an_empty_ledger_keep_their_stored_hours() -> None:
    block = _block(time="2026-10-18T10:00:00+00:00", reason_code="needs_human")
    open_streak = {"status": "open", "rcs": 0, "hours": 0.0, "since": None, "last_block": block}
    empty = _no_block(0, 0.0, None)
    later = datetime(2026, 12, 1, tzinfo=UTC)
    assert streak_lines({"hands_off": open_streak, "clean": empty}, now=later) == [
        "    Hands-off: 0 RCs / 0h   open since 2026-10-18 10:00 (Needs Human)",
        "    Clean:     0 RCs / 0h   (no block on record)",
    ]


def test_empty_ledger_has_no_anchor() -> None:
    streak = _no_block(0, 0.0, None)
    assert streak_lines({"hands_off": streak, "clean": streak})[0] == (
        "    Hands-off: 0 RCs / 0h   (no block on record)"
    )


def test_cause_falls_back_to_its_short_form_then_an_ellipsis_within_70_columns() -> None:
    since = "2026-10-08T03:31:00+00:00"
    detailed = _block(reason_code="provider_refusal")
    detailed["class"] = "human"
    document = {
        "hands_off": _resolved(12, 240, since, detailed),
        "clean": _resolved(2, 7, since, _block(source="halt:project", reason_code="cm-x")),
    }
    hands_off, clean = streak_lines(document, now=_AT_0331)
    assert hands_off.endswith("(Needs Human)")
    assert clean.endswith("(CM HALT)")
    assert streak_lines(document, width=200, now=_AT_0331)[0].endswith("(Needs Human: provider refusal)")
    narrow = streak_lines(document, width=60, now=_AT_0331)
    # 54 columns before the cause leave 4 inside the parentheses.
    assert narrow[0] == "    Hands-off: 12 RCs / 240h   since 2026-10-08 03:31 (Nee…)"
    assert all(len(line) <= 60 for line in narrow)


def test_wide_numbers_never_push_a_line_past_70_columns() -> None:
    block = _block(source="halt:project", reason_code="zero_progress_cap")
    document = {
        "hands_off": _resolved(12345, 999999.0, "2026-01-01T00:00:00Z", block),
        "clean": _resolved(1, 1, "2026-01-01T00:00:00Z", block),
    }
    assert all(len(line) <= PANE_WIDTH for line in streak_lines(document))


# ---------------------------------------------------------------------------
# Reading and placeholders
# ---------------------------------------------------------------------------


def test_fixture_documents_load(tmp_path: pathlib.Path) -> None:
    for name in ("alpha", "beta"):
        document, placeholder = load_reliability(str(_FIXTURES / f"{name}.json"))
        assert (isinstance(document, dict), placeholder) == (True, "")


@pytest.mark.parametrize(
    "content",
    [
        "{not json",
        "[1, 2]",
        "{}",
        {"hands_off": _no_block(1, 1, None)},
        {"hands_off": _no_block(1, 1, None), "clean": {**_no_block(1, 1, None), "rcs": True}},
        {"hands_off": _no_block(1, 1, None), "clean": {**_no_block(1, 1, None), "hours": "many"}},
        {"hands_off": _no_block(1, 1, None), "clean": {**_no_block(1, 1, None), "status": "maybe"}},
        {"hands_off": _no_block(1, 1, None), "clean": _no_block(1, 1, "yesterday")},
        {"hands_off": _no_block(1, 1, None), "clean": _resolved(1, 1, "2026-10-01T00:00:00Z", None)},
    ],
)
def test_unreadable_document_renders_the_unreadable_placeholder(tmp_path: pathlib.Path, content: Any) -> None:
    assert project_entry_lines("p", _write(tmp_path, content)) == ["  p", "    (reliability.json unreadable)"]


@pytest.mark.parametrize("path", ["", None, "absent.json"])
def test_missing_document_renders_the_missing_placeholder(tmp_path: pathlib.Path, path: Any) -> None:
    target = str(tmp_path / path) if path else path
    assert project_entry_lines("p", target, bold=BOLD, dim=DIM, reset=RESET) == [
        f"  {BOLD}p{RESET}",
        f"    {DIM}(no reliability data yet){RESET}",
    ]


def test_streak_lines_refuse_a_document_load_would_not_accept() -> None:
    with pytest.raises(ValueError):
        streak_lines({"hands_off": {}, "clean": {}})


# ---------------------------------------------------------------------------
# The pane section
# ---------------------------------------------------------------------------


def test_section_draws_the_header_entries_and_blank_lines_in_the_escapes(tmp_path: pathlib.Path) -> None:
    path = str(_FIXTURES / "beta.json")
    lines = list(
        reliability_section_lines(
            [{"name": "beta", "reliability_file": path}, {"name": "fresh"}],
            reset=RESET,
            bold=BOLD,
            dim=DIM,
            cyan=CYAN,
            now=_AT_FIXTURES,
        )
    )
    assert lines == [
        f"{CYAN}{BOLD}=== Reliability ==={RESET}",
        "",
        f"  {BOLD}beta{RESET}",
        "    Hands-off: 2 RCs / 411h   since 2026-10-01 (no block on record)",
        "    Clean:     2 RCs / 411h   since 2026-10-01 (no block on record)",
        "",
        f"  {BOLD}fresh{RESET}",
        f"    {DIM}(no reliability data yet){RESET}",
        "",
    ]


def test_section_without_projects_says_none_are_registered() -> None:
    assert list(reliability_section_lines([], dim=DIM, cyan=CYAN, bold=BOLD, reset=RESET)) == [
        f"{CYAN}{BOLD}=== Reliability ==={RESET}",
        "",
        f"  {DIM}(no projects registered){RESET}",
    ]


def test_pane_renderer_places_the_section_between_current_rc_and_the_footer() -> None:
    projects = json.dumps([{"name": "beta", "rc": "", "reliability_file": str(_FIXTURES / "beta.json")}])
    now = datetime(2026, 10, 18, 12, 0, tzinfo=UTC)
    lines = list(metrics_pane_lines(False, "2026-10-18", projects, now=now))
    start = lines.index("=== Reliability ===")
    assert lines[start - 3 : start] == ["=== Current RC ===", "", "  (no RC active in any project)"]
    assert lines[start + 1 : start + 6] == [
        "",
        "  beta",
        "    Hands-off: 2 RCs / 411h   since 2026-10-01 (no block on record)",
        "    Clean:     2 RCs / 411h   since 2026-10-01 (no block on record)",
        "",
    ]
    assert lines[start + 6].startswith("Updated: ")


# ---------------------------------------------------------------------------
# CLI, run by file path
# ---------------------------------------------------------------------------


def _cli(tmp_path: pathlib.Path, *args: str) -> subprocess.CompletedProcess:
    env = build_scrubbed_env(home_dir=tmp_path / "home")
    return subprocess.run(
        [sys.executable, str(_MODULE_FILE), *args],
        capture_output=True,
        text=True,
        env=env,
        cwd=str(tmp_path),
        timeout=60,
        check=False,
    )


def _root(tmp_path: pathlib.Path) -> pathlib.Path:
    root = tmp_path / "root"
    for name in ("alpha", "beta"):
        (root / "projects" / name / "metrics").mkdir(parents=True)
        (root / "projects" / name / "metrics" / "reliability.json").write_bytes(
            (_FIXTURES / f"{name}.json").read_bytes()
        )
    return root


def test_cli_text_prints_each_entry_separated_by_a_blank_line(tmp_path: pathlib.Path) -> None:
    root = _root(tmp_path)
    result = _cli(tmp_path, "text", "--kanban-root", str(root), "--now", "2026-10-18T12:00:00+00:00", "alpha", "beta")
    assert result.returncode == 0, result.stderr
    expected = (
        project_entry_lines("alpha", str(_FIXTURES / "alpha.json"), now=_AT_FIXTURES)
        + [""]
        + project_entry_lines("beta", str(_FIXTURES / "beta.json"), now=_AT_FIXTURES)
    )
    assert result.stdout.splitlines() == expected


def test_cli_text_counts_hours_to_now_and_refuses_a_bad_now(tmp_path: pathlib.Path) -> None:
    root = _root(tmp_path)
    later = _cli(tmp_path, "text", "--kanban-root", str(root), "--now", "2026-10-03T09:00:00Z", "beta")
    assert later.returncode == 0, later.stderr
    assert "2 RCs /  48h   since 2026-10-01" in later.stdout or "2 RCs / 48h   since 2026-10-01" in later.stdout
    bad = _cli(tmp_path, "text", "--kanban-root", str(root), "--now", "yesterday", "beta")
    assert bad.returncode == 2 and "not an ISO-8601 time" in bad.stderr


def test_cli_text_without_projects_says_none_are_registered(tmp_path: pathlib.Path) -> None:
    result = _cli(tmp_path, "text", "--kanban-root", str(tmp_path))
    assert (result.returncode, result.stdout) == (0, "  (no projects registered)\n")


def test_cli_json_prints_the_file_and_refuses_a_missing_one(tmp_path: pathlib.Path) -> None:
    root = _root(tmp_path)
    shown = _cli(tmp_path, "json", "--kanban-root", str(root), "alpha")
    assert (shown.returncode, shown.stdout) == (0, (_FIXTURES / "alpha.json").read_text(encoding="utf-8"))
    missing = _cli(tmp_path, "json", "--kanban-root", str(root), "gamma")
    assert (missing.returncode, missing.stdout) == (2, "")
    assert "ERROR: no reliability.json for project 'gamma'" in missing.stderr
    assert "reliability refresh --project gamma" in missing.stderr
