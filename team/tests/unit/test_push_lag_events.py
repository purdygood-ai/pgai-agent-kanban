"""
test_push_lag_events.py
=======================
pgai_agent_kanban.cm.push_lag_events decides which RC branches the Push Lag
Events in release-state.md let push-watchdog.sh delete from the remote.

  - An event item "<branch> (still on <remote>; deletion failed)" is a
    delete only when the branch is exactly <branch_prefix>rc/<Version> for
    the event's own vX.Y.Z Version and both the item and the event name the
    remote the caller deletes from.
  - Any other failed-deletion item -- the main branch, another version's RC
    branch, another lane's unprefixed branch, a different remote, a
    malformed Version -- is a skip carrying its reason, never a delete.
  - Items that are not a failed deletion ("push failed") and lines outside a
    Push Lag Event are ignored; spacing and heading case are read liberally;
    the same branch named by two events is reported once.
  - The command line prints one tab-separated line per decision and prints
    nothing for a missing release-state.md.
"""

from __future__ import annotations

import pathlib
import subprocess
import sys

from pgai_agent_kanban.cm.push_lag_events import rc_branch_decisions

_TEAM_DIR = pathlib.Path(__file__).resolve().parents[2]


def _event(version: str, remote: str, *items: str) -> str:
    lines = [
        "## Push Lag Event",
        "Timestamp: 2026-01-01T00:00:00Z",
        f"Version: {version}",
        f"Remote: {remote}",
        "Not On Remote:",
        *(f"  - {item}" for item in items),
        "Retry: /kanban/scripts/cm/push-watchdog.sh --project sample-lane",
    ]
    return "\n".join(lines) + "\n"


def _state(*events: str) -> str:
    return "# Release State\n\n## Active RC\nnone\n\n" + "\n".join(events) + "\n## Last Released\nv0.1.0\n"


def _summary(text: str, remote: str = "origin", prefix: str = "ai_") -> list:
    return [(d.action, d.branch) for d in rc_branch_decisions(text, remote, prefix)]


def test_the_events_own_rc_branch_is_a_delete() -> None:
    text = _state(
        _event(
            "v0.1.0",
            "origin",
            "ai_main (push failed)",
            "refs/tags/ai_v0.1.0 (push failed)",
            "ai_rc/v0.1.0 (still on origin; deletion failed)",
        )
    )
    assert _summary(text) == [("delete", "ai_rc/v0.1.0")]


def test_an_unprefixed_lane_deletes_its_bare_rc_branch() -> None:
    text = _state(_event("v2.0.0", "upstream", "rc/v2.0.0 (still on upstream; deletion failed)"))
    assert _summary(text, remote="upstream", prefix="") == [("delete", "rc/v2.0.0")]


def test_no_other_branch_is_ever_a_delete() -> None:
    text = _state(
        _event(
            "v0.1.0",
            "origin",
            "ai_main (still on origin; deletion failed)",
            "ai_rc/v0.0.9 (still on origin; deletion failed)",
            "rc/v0.1.0 (still on origin; deletion failed)",
            "feature/keep (still on origin; deletion failed)",
        )
    )
    decisions = rc_branch_decisions(text, "origin", "ai_")
    assert [d.action for d in decisions] == ["skip"] * 4, decisions
    assert all("is not this project's RC branch for v0.1.0 (ai_rc/v0.1.0)" in d.reason for d in decisions), decisions


def test_a_different_remote_is_a_skip() -> None:
    item_elsewhere = _event("v0.1.0", "origin", "ai_rc/v0.1.0 (still on upstream; deletion failed)")
    event_elsewhere = _event("v0.2.0", "upstream", "ai_rc/v0.2.0 (still on upstream; deletion failed)")
    decisions = rc_branch_decisions(_state(item_elsewhere, event_elsewhere), "origin", "ai_")
    assert [(d.action, d.branch) for d in decisions] == [("skip", "ai_rc/v0.1.0"), ("skip", "ai_rc/v0.2.0")]
    assert all("not the configured remote origin" in d.reason for d in decisions), decisions


def test_a_malformed_version_is_a_skip() -> None:
    for version in ("v0.1", "0.1.0", "next"):
        text = _state(_event(version, "origin", f"ai_rc/{version} (still on origin; deletion failed)"))
        decisions = rc_branch_decisions(text, "origin", "ai_")
        assert [d.action for d in decisions] == ["skip"], (version, decisions)
        assert "is not vX.Y.Z" in decisions[0].reason, decisions


def test_items_outside_an_event_and_pushes_are_ignored() -> None:
    text = (
        "# Release State\n\n## Notes\n  - ai_rc/v0.1.0 (still on origin; deletion failed)\n\n"
        + _event("v0.1.0", "origin", "ai_main (push failed)")
        + "\n## Last Released\nVersion: v0.1.0\n  - ai_rc/v0.1.0 (still on origin; deletion failed)\n"
    )
    assert _summary(text) == []


def test_spacing_and_heading_case_are_read_liberally_and_duplicates_once() -> None:
    loose = (
        "##   push lag EVENT   \n"
        "Version:   v0.3.0  \n"
        "Remote:\torigin\n"
        "Not On Remote:\n"
        "  *   ai_rc/v0.3.0   ( still on origin ;  deletion failed )  \n"
    )
    text = _state(loose, _event("v0.3.0", "origin", "ai_rc/v0.3.0 (still on origin; deletion failed)"))
    assert _summary(text) == [("delete", "ai_rc/v0.3.0")]


def test_command_line_prints_one_tab_separated_line_per_decision(tmp_path: pathlib.Path) -> None:
    state = tmp_path / "release-state.md"
    state.write_text(
        _state(
            _event(
                "v0.1.0",
                "origin",
                "ai_rc/v0.1.0 (still on origin; deletion failed)",
                "ai_main (still on origin; deletion failed)",
            )
        ),
        encoding="utf-8",
    )
    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "pgai_agent_kanban.cm.push_lag_events",
            "rc-branch-deletes",
            str(state),
            "--remote",
            "origin",
            "--branch-prefix",
            "ai_",
        ],
        cwd=str(_TEAM_DIR),
        capture_output=True,
        text=True,
        timeout=60,
    )
    assert result.returncode == 0, result.stderr
    lines = result.stdout.splitlines()
    assert lines[0] == "delete\tai_rc/v0.1.0", lines
    action, item, reason = lines[1].split("\t")
    assert (action, item) == ("skip", "ai_main (still on origin; deletion failed)"), lines
    assert "ai_rc/v0.1.0" in reason, lines
    assert len(lines) == 2, lines


def test_command_line_prints_nothing_for_a_missing_file(tmp_path: pathlib.Path) -> None:
    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "pgai_agent_kanban.cm.push_lag_events",
            "rc-branch-deletes",
            str(tmp_path / "absent.md"),
            "--remote",
            "origin",
        ],
        cwd=str(_TEAM_DIR),
        capture_output=True,
        text=True,
        timeout=60,
    )
    assert result.returncode == 0, result.stderr
    assert result.stdout == ""
