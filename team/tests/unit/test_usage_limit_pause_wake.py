"""
test_usage_limit_pause_wake.py
==============================
Sandbox wakes for the provider usage-limit pause: real
``bash scripts/wake/claude.sh --agent=<agent>`` firings against a throwaway
kanban root (the sandbox test_dispatch_hold_systemic_blocks.py builds), with
the PATH-shadowing ``claude`` stub in its ``usage-limit`` mode.

Behaviours:
  - a dispatch whose provider output reports the usage limit with a reset
    time returns the task to BACKLOG with no retry counted, writes the
    pause marker carrying that reset time, and stops the chain before the
    next task;
  - while the pause is in force no role dispatches -- another role's wake
    narrates the hold and invokes nothing;
  - once the reset time has passed the next wake narrates the end and
    dispatches again.
"""

from __future__ import annotations

import datetime

from tests.fixtures.claude_provider_stub import call_count
from tests.unit.test_dispatch_hold_systemic_blocks import _output, _Sandbox

_FIRST = "CODER-20261011-001-usage-first"
_SECOND = "CODER-20261011-002-usage-second"
_WRITER = "WRITER-20261011-003-usage-writer"


def _iso(dt: datetime.datetime) -> str:
    return dt.astimezone(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%S+00:00")


def test_usage_limit_pauses_every_role_until_the_reset_then_resumes(tmp_path):
    box = _Sandbox(tmp_path)
    box.add_task(_FIRST, state="BACKLOG")
    box.add_task(_SECOND, state="BACKLOG")
    box.add_task(_WRITER, state="BACKLOG")
    reset = (datetime.datetime.now(datetime.timezone.utc) + datetime.timedelta(hours=2)).replace(microsecond=0)
    (box.control_dir / "mode").write_text("usage-limit\n", encoding="utf-8")
    (box.control_dir / "usage-limit.txt").write_text(
        f"Claude usage limit reached. Your limit resets {_iso(reset)}.\n", encoding="utf-8"
    )

    first = box.wake("coder")

    assert first.returncode == 0, _output(first)
    assert call_count(box.control_dir) == 1, _output(first)
    assert box.state(_FIRST) == "BACKLOG", _output(first)
    assert box.state(_SECOND) == "BACKLOG"
    status = (box.tasks / _FIRST / "status.md").read_text(encoding="utf-8")
    assert "retry_count" not in status, "a usage limit must not count a transient retry"
    marker = box.root / "provider-pause" / "claude.pause"
    assert f"resumes_at={_iso(reset)}" in marker.read_text(encoding="utf-8")
    assert f"paused at its usage limit until {_iso(reset)}" in _output(first)

    # Another role, while the pause is in force: nothing is dispatched.
    held = box.wake("writer")

    assert held.returncode == 0, _output(held)
    assert call_count(box.control_dir) == 1, _output(held)
    assert box.state(_WRITER) == "BACKLOG"
    assert f"not dispatching any task until {_iso(reset)}" in _output(held)
    assert "error:   wake.dispatch_paused_usage_limit" in held.stderr

    # The reset time passes: the next wake narrates the end and dispatches.
    marker.write_text(
        marker.read_text(encoding="utf-8").replace(f"resumes_at={_iso(reset)}", "resumes_at=2026-01-01T00:00:00+00:00"),
        encoding="utf-8",
    )
    (box.control_dir / "mode").write_text("success\n", encoding="utf-8")
    resumed = box.wake("writer")

    assert resumed.returncode == 0, _output(resumed)
    assert "usage-limit pause ended" in _output(resumed)
    assert call_count(box.control_dir) == 2, _output(resumed)
    assert box.state(_WRITER) == "DONE"
    assert not marker.exists()
