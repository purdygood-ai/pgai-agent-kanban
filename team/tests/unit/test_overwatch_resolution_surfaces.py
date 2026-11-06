"""
test_overwatch_resolution_surfaces.py
========================================
Behavioral tests for requirements/v2.23.0-overwatch-block-resolution.md,
item 7: while OVERWATCH's three-lane resolution stage is actively resolving
a project's block, team/scripts/why.sh and the dashboard attention window
must both say so — naming the lane and the attempt — rather than showing a
silent HALT. Once the lane finishes, both surfaces show the recorded
"## OVERWATCH Decision" event instead.

Coverage:
  - pgai_agent_kanban.overwatch.resolution_stage.write_resolving_marker /
    clear_resolving_marker: the shared marker file both surfaces below read
    independently (one bash reader, one Python reader) so they can never
    disagree.
  - team/scripts/why.sh (via team/pgai_agent_kanban/ops/verdict.py): a HALTED verdict
    names the in-progress lane/attempt when the marker is present, falls
    back to the most recent OVERWATCH Decision event when the marker is
    gone but the decision still matches the project's Active RC, and
    renders byte-identically to the pre-existing plain-HALT line when
    neither applies (overwatch_resolve=false's own observable shape, since
    that flag gates whether a marker or decision is ever written at all).
  - pgai_agent_kanban.dashboard.scan_attention.scan_overwatch_section: the
    attention window's OVERWATCH pane renders the same two states, visually
    distinct from a needs-human action-log entry.
  - pgai_agent_kanban.overwatch.resolution_stage.log_action: the action log
    that pane reads gets one six-field line per record, whatever line
    breaks or tabs any field holds.
  - _parse_overwatch_action_log reads a log written through both real
    writers (Python and bash) back as one entry per LF-terminated line,
    whatever other line-break characters a reason holds, an empty reason
    included.

Naming: function names describe behavior under test, never bug IDs or task
IDs (see roles/TESTER.md "Test Authoring Guidelines").
"""

from __future__ import annotations

import os
import pathlib
import subprocess

import pytest

from pgai_agent_kanban.overwatch.resolution_stage import (
    clear_resolving_marker,
    log_action,
    write_resolving_marker,
)
from pgai_agent_kanban.dashboard.scan_attention import _parse_overwatch_action_log, scan_overwatch_section
from tests.unit.shell_harness import run_bash

_TEAM_DIR = pathlib.Path(__file__).parent.parent.parent  # team/
_WHY_SH = _TEAM_DIR / "scripts" / "why.sh"


# ---------------------------------------------------------------------------
# write_resolving_marker / clear_resolving_marker — the shared marker file
# ---------------------------------------------------------------------------


def test_write_resolving_marker_creates_a_file_with_all_four_fields(tmp_path: pathlib.Path) -> None:
    project_root = tmp_path / "proj"
    project_root.mkdir()

    write_resolving_marker(str(project_root), lane="A", attempt=1, version="v2.23.0", timestamp="2026-09-15T12:00:00Z")

    marker = project_root / "overwatch" / "resolving.md"
    assert marker.is_file()
    text = marker.read_text(encoding="utf-8")
    assert "lane: A" in text
    assert "attempt: 1" in text
    assert "version: v2.23.0" in text
    assert "started_at: 2026-09-15T12:00:00Z" in text


def test_write_resolving_marker_overwrites_a_prior_marker(tmp_path: pathlib.Path) -> None:
    project_root = tmp_path / "proj"
    project_root.mkdir()

    write_resolving_marker(str(project_root), lane="A", attempt=1, version="v2.23.0", timestamp="t1")
    write_resolving_marker(str(project_root), lane="B", attempt=1, version="v2.23.1", timestamp="t2")

    text = (project_root / "overwatch" / "resolving.md").read_text(encoding="utf-8")
    assert "lane: B" in text
    assert "lane: A" not in text


def test_clear_resolving_marker_removes_the_file(tmp_path: pathlib.Path) -> None:
    project_root = tmp_path / "proj"
    project_root.mkdir()
    write_resolving_marker(str(project_root), lane="A", attempt=1, version="v2.23.0", timestamp="t1")
    marker = project_root / "overwatch" / "resolving.md"
    assert marker.is_file()

    clear_resolving_marker(str(project_root))

    assert not marker.exists()


def test_clear_resolving_marker_on_a_project_with_no_marker_does_not_raise(tmp_path: pathlib.Path) -> None:
    project_root = tmp_path / "proj"
    project_root.mkdir()

    clear_resolving_marker(str(project_root))  # must not raise


# ---------------------------------------------------------------------------
# why.sh / ops.verdict — the in-progress line and the Decision-event fallback
# ---------------------------------------------------------------------------


def _env(kanban_root: pathlib.Path) -> dict:
    env = dict(os.environ)
    env.pop("PGAI_PROJECT_NAME", None)
    env["PGAI_AGENT_KANBAN_ROOT_PATH"] = str(kanban_root)
    return env


def _run_why(args: list, kanban_root: pathlib.Path) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["bash", str(_WHY_SH), *args],
        capture_output=True,
        text=True,
        env=_env(kanban_root),
        timeout=15,
    )


def _write_projects_cfg(kanban_root: pathlib.Path, name: str) -> None:
    (kanban_root / "projects.cfg").write_text(f"[project:{name}]\npriority=1\nenabled=true\n", encoding="utf-8")


def _make_project_dir(kanban_root: pathlib.Path, name: str) -> pathlib.Path:
    project_dir = kanban_root / "projects" / name
    (project_dir / "tasks").mkdir(parents=True, exist_ok=True)
    return project_dir


def _write_ship_policy_halt(project_dir: pathlib.Path) -> pathlib.Path:
    halt_path = project_dir / "HALT"
    halt_path.write_text(
        "# HALT created by CM at 2026-09-15T00:00:00Z\n" "# Reason: TESTER systemic_risk=high (TESTER-20260915-001)\n",
        encoding="utf-8",
    )
    return halt_path


def test_why_names_lane_and_attempt_while_a_resolving_marker_is_present(tmp_path: pathlib.Path) -> None:
    _write_projects_cfg(tmp_path, "demo")
    project_dir = _make_project_dir(tmp_path, "demo")
    _write_ship_policy_halt(project_dir)
    write_resolving_marker(str(project_dir), lane="A", attempt=1, version="v2.23.0", timestamp="2026-09-15T12:00:00Z")

    result = _run_why(["--project", "demo"], tmp_path)

    assert result.returncode == 5, f"HALTED exit code must be unchanged: {result.stdout!r}"
    line = result.stdout.strip()
    assert line.startswith("demo: HALTED")
    assert "OVERWATCH resolving (lane A, attempt 1)" in line
    assert "v2.23.0" in line
    assert "remedy:" in line


def test_why_shows_the_decision_event_once_the_marker_is_cleared(tmp_path: pathlib.Path) -> None:
    _write_projects_cfg(tmp_path, "demo")
    project_dir = _make_project_dir(tmp_path, "demo")
    _write_ship_policy_halt(project_dir)
    (project_dir / "release-state.md").write_text(
        "# Release State\n\n"
        "## Active RC\nv2.23.0\n\n"
        "## OVERWATCH Decision\n"
        "Timestamp: 2026-09-15T12:05:00Z\n"
        "Version: v2.23.0\n"
        "Lane: C\n"
        "Evidence:\n"
        "  - some/path\n"
        "Verbs Run:\n"
        "  - Needs Human raised (existing default -- no new mechanism)\n"
        "Outcome: OVERWATCH read: v2.23.0 is HALTed and classifies as Lane C -- no matching class.\n",
        encoding="utf-8",
    )
    # No resolving marker — the lane already finished.

    result = _run_why(["--project", "demo"], tmp_path)

    assert result.returncode == 5
    line = result.stdout.strip()
    assert line.startswith("demo: HALTED")
    assert "OVERWATCH Decision recorded (lane C, v2.23.0)" in line
    assert "resolving" not in line, "a completed decision must not read as an in-progress line"
    assert "remedy:" in line


def test_why_ignores_a_decision_event_recorded_against_a_different_active_rc(
    tmp_path: pathlib.Path,
) -> None:
    """A Decision event from a since-superseded RC must never be presented
    as if it explains a LATER, unrelated HALT on the same project — the
    staleness guard ops/verdict.py applies before surfacing one."""
    _write_projects_cfg(tmp_path, "demo")
    project_dir = _make_project_dir(tmp_path, "demo")
    halt_path = _write_ship_policy_halt(project_dir)
    (project_dir / "release-state.md").write_text(
        "# Release State\n\n"
        "## Active RC\nv2.23.5\n\n"
        "## OVERWATCH Decision\n"
        "Timestamp: 2026-09-10T00:00:00Z\n"
        "Version: v2.23.0\n"
        "Lane: C\n"
        "Evidence:\n"
        "  - some/path\n"
        "Verbs Run:\n"
        "  - Needs Human raised\n"
        "Outcome: a stale decision about a prior RC.\n",
        encoding="utf-8",
    )

    result = _run_why(["--project", "demo"], tmp_path)

    assert result.returncode == 5
    line = result.stdout.strip()
    assert line.startswith("demo: HALTED")
    assert "OVERWATCH Decision" not in line
    assert str(halt_path) in line, "must fall back to the plain HALT-file reason"


def test_why_renders_byte_identically_to_a_plain_halt_when_overwatch_never_ran(
    tmp_path: pathlib.Path,
) -> None:
    """With overwatch_resolve=false (or before OVERWATCH ever fired), no
    marker and no Decision event exist — why.sh's HALTED line must be
    identical to its pre-v2.23.0 shape."""
    _write_projects_cfg(tmp_path, "demo")
    project_dir = _make_project_dir(tmp_path, "demo")
    halt_path = _write_ship_policy_halt(project_dir)

    result = _run_why(["--project", "demo"], tmp_path)

    assert result.returncode == 5
    line = result.stdout.strip()
    assert line.startswith("demo: HALTED")
    assert str(halt_path) in line
    assert "HALT created by CM" in line
    assert "OVERWATCH resolving" not in line
    assert "OVERWATCH Decision" not in line


# ---------------------------------------------------------------------------
# scan_overwatch_section — the attention window's OVERWATCH pane
# ---------------------------------------------------------------------------


def test_scan_overwatch_section_renders_resolving_distinctly_from_needs_human(tmp_path: pathlib.Path, capsys) -> None:
    project_dir = tmp_path / "projects" / "demo"
    project_dir.mkdir(parents=True)
    write_resolving_marker(str(project_dir), lane="A", attempt=1, version="v2.23.0", timestamp="2026-09-15T12:00:00Z")
    # A needs-human entry in the same project's action log, to prove the two
    # render as visually distinct labels within the same section.
    ow_dir = project_dir / "overwatch"
    (ow_dir / "actions.log").write_text(
        "2026-07-07T10:00:00Z\tcheck-transient-api-error\tCODER-002\t"
        "bug-filed\tnone\tunrelated needs-human finding\n",
        encoding="utf-8",
    )

    scan_overwatch_section(str(tmp_path), use_color=False)
    out = capsys.readouterr().out

    assert "resolving" in out
    assert "OVERWATCH resolving (lane A, attempt 1) for v2.23.0" in out
    assert "needs-human" in out
    # The two labels must be distinguishable substrings, not the same token.
    assert out.index("resolving") != out.index("needs-human")


def test_scan_overwatch_section_renders_decision_once_marker_is_cleared(tmp_path: pathlib.Path, capsys) -> None:
    project_dir = tmp_path / "projects" / "demo"
    project_dir.mkdir(parents=True)
    (project_dir / "HALT").write_text("# HALT created by CM at 2026-09-15T00:00:00Z\n", encoding="utf-8")
    (project_dir / "release-state.md").write_text(
        "# Release State\n\n"
        "## Active RC\nv2.23.0\n\n"
        "## OVERWATCH Decision\n"
        "Timestamp: 2026-09-15T12:05:00Z\n"
        "Version: v2.23.0\n"
        "Lane: C\n"
        "Evidence:\n"
        "  - some/path\n"
        "Verbs Run:\n"
        "  - Needs Human raised\n"
        "Outcome: OVERWATCH read: v2.23.0 is HALTed and classifies as Lane C.\n",
        encoding="utf-8",
    )

    scan_overwatch_section(str(tmp_path), use_color=False)
    out = capsys.readouterr().out

    assert "decision" in out
    assert "OVERWATCH Decision recorded (lane C, v2.23.0)" in out
    assert "resolving" not in out


def test_scan_overwatch_section_shows_no_resolving_or_decision_when_neither_exists(
    tmp_path: pathlib.Path, capsys
) -> None:
    project_dir = tmp_path / "projects" / "demo"
    project_dir.mkdir(parents=True)

    scan_overwatch_section(str(tmp_path), use_color=False)
    out = capsys.readouterr().out

    assert "resolving" not in out
    assert "decision" not in out
    assert "no recent OVERWATCH activity" in out


# ---------------------------------------------------------------------------
# log_action -- one record per line in the action log the pane reads
# ---------------------------------------------------------------------------

_MULTI_LINE_REASON = (
    "remote: pushes are frozen\r\n"
    "To /srv/origin.git\n"
    " ! [remote rejected] main -> main (pre-receive hook declined)\n"
    "error:\tfailed to push some refs\n"
)
_ONE_LINE_REASON = " already one line,  inner  spacing kept "


@pytest.mark.parametrize(
    ("reason", "written"),
    [
        (
            _MULTI_LINE_REASON,
            "remote: pushes are frozen To /srv/origin.git ! [remote rejected] main -> main "
            "(pre-receive hook declined) error: failed to push some refs",
        ),
        (_ONE_LINE_REASON, _ONE_LINE_REASON),
    ],
    ids=["newline-cr-tab-flattened", "one-line-unchanged"],
)
def test_log_action_writes_one_six_field_line_whatever_the_reason_holds(
    tmp_path: pathlib.Path, reason: str, written: str
) -> None:
    log_action(str(tmp_path), "demo", "check-push-lag", "main", "push-main-failed", reason)

    # Bytes, not read_text(): universal newlines would turn a written CR into LF.
    log = (tmp_path / "projects" / "demo" / "overwatch" / "actions.log").read_bytes().decode("utf-8")
    assert log.endswith("\n") and log.count("\n") == 1 and "\r" not in log, repr(log)
    fields = log[:-1].split("\t")
    assert len(fields) == 6, fields
    assert fields[1:5] == ["check-push-lag", "main", "push-main-failed", "none"]
    assert fields[5] == written
    assert fields[5].split() == reason.split(), "every word of every original line is kept, in order"


_BROKEN_FIELD = "first\tpart\r\nsecond  part\n"
_FLATTENED_FIELD = "first part second part"
_WRITER_ARGS = {"name": "check-push-lag", "target": "main", "action": "push-main-failed", "backup_path": "none"}


@pytest.mark.parametrize("field", list(_WRITER_ARGS))
def test_log_action_writes_one_six_field_line_whatever_any_field_holds(tmp_path: pathlib.Path, field: str) -> None:
    args = dict(_WRITER_ARGS, **{field: _BROKEN_FIELD})

    log_action(str(tmp_path), "demo", reason="one line", **args)

    log = (tmp_path / "projects" / "demo" / "overwatch" / "actions.log").read_bytes().decode("utf-8")
    assert log.endswith("\n") and log.count("\n") == 1 and "\r" not in log, repr(log)
    fields = log[:-1].split("\t")
    assert len(fields) == 6, fields
    expected = dict(args, **{field: _FLATTENED_FIELD})
    assert fields[1:] == [expected[key] for key in _WRITER_ARGS] + ["one line"]


# Reasons the reader once cut short or dropped, and the form each writer stores.
_ROUND_TRIP_REASONS = {
    "vt-ff": ("a\vb\fc", "a\vb\fc"),
    "line-separator": ("p\u2028q", "p\u2028q"),
    "empty": ("", ""),
    "breaks-only": ("\n\t\r\n", ""),
    "plain": ("fine", "fine"),
}


def test_a_log_from_both_writers_reads_back_one_entry_per_record(tmp_path: pathlib.Path) -> None:
    proj_dir = tmp_path / "projects" / "demo"
    for target, (reason, _stored) in _ROUND_TRIP_REASONS.items():
        log_action(str(tmp_path), "demo", "py-writer", target, "act", reason)
    calls = "\n".join(
        f'overwatch_log_action sh-writer {target} act none "$REASON_{index}"'
        for index, target in enumerate(_ROUND_TRIP_REASONS)
    )
    reasons_env = {f"REASON_{index}": reason for index, (reason, _stored) in enumerate(_ROUND_TRIP_REASONS.values())}
    script = f"""set -euo pipefail
source scripts/lib/overwatch_protocol.sh
export KANBAN_ROOT="{tmp_path}"
export OVERWATCH_PROJECT=demo
{calls}
"""
    result = run_bash(tmp_path, script, extra_env=reasons_env)
    assert result.returncode == 0, result.stderr

    log_path = proj_dir / "overwatch" / "actions.log"
    entries = _parse_overwatch_action_log(log_path, 0)

    assert len(entries) == log_path.read_bytes().count(b"\n") == 2 * len(_ROUND_TRIP_REASONS)
    expected = sorted(
        (writer, target, stored)
        for writer in ("py-writer", "sh-writer")
        for target, (_reason, stored) in _ROUND_TRIP_REASONS.items()
    )
    assert sorted((entry["name"], entry["target"], entry["reason"]) for entry in entries) == expected
