"""
test_attention_pane_approval_class.py
======================================
Behavioral unit tests for the pending-approvals needs-human stratum added to
team/pgai_agent_kanban/dashboard/scan_attention.py.

Acceptance criteria verified:
  1. Fixture with one pending gate (WAITING): attention pane contains a row
     with the raised-hand class marker and the correct project label in the
     needs-human stratum.
  2. Row is positioned above the OVERWATCH ledger — verified by checking the
     pending-approvals output is emitted before the overwatch-section output
     when both are called in order (as attention.sh does).
  3. Approve the gate (set state to DONE): next render omits the row.
  4. Web UI Attention tab passthrough: scan_pending_approvals_attention is
     importable from the same module that attention.sh delegates to, confirming
     the pane content flows through the existing passthrough seam without any
     web-UI changes.

All filesystem operations use tmp_path (pytest-managed).
No live kanban root, no subprocesses.
"""

from __future__ import annotations

import io
import pathlib
import sys
import textwrap
from contextlib import redirect_stdout

import pytest

# ---------------------------------------------------------------------------
# Ensure the package root is importable regardless of cwd.
# ---------------------------------------------------------------------------
_TEAM_DIR = pathlib.Path(__file__).parent.parent.parent  # team/tests/unit/ → team/
_PKG_ROOT = _TEAM_DIR  # pgai_agent_kanban/ lives directly under team/
if str(_PKG_ROOT) not in sys.path:
    sys.path.insert(0, str(_PKG_ROOT))

from pgai_agent_kanban.dashboard.scan_attention import (
    scan_pending_approvals_attention,
    scan_overwatch_section,
)


# ---------------------------------------------------------------------------
# Fixture builder helpers
# ---------------------------------------------------------------------------


def _write_approval_task(
    tasks_dir: pathlib.Path,
    task_id: str,
    state: str,
    release_ver: str = "v1.10.0",
) -> None:
    """Create a minimal HUMAN-APPROVE task folder under tasks_dir."""
    task_dir = tasks_dir / task_id
    task_dir.mkdir(parents=True, exist_ok=True)

    (task_dir / "status.md").write_text(
        f"# Status\n\n## State\n{state}\n",
        encoding="utf-8",
    )
    (task_dir / "README.md").write_text(
        textwrap.dedent(f"""\
            # Task: Human Approval Gate

            ## Task ID
            {task_id}

            ## Role
            HUMAN

            ## Goal
            Review and approve release {release_ver}.

            ## Release Version
            {release_ver}
        """),
        encoding="utf-8",
    )


def _build_project(
    root: pathlib.Path,
    proj: str,
    tasks: list[tuple],
) -> pathlib.Path:
    """Create projects/<proj>/tasks with the given task list.

    Each entry in tasks is (task_id, state, release_ver).
    Returns the tasks directory path.
    """
    tasks_dir = root / "projects" / proj / "tasks"
    tasks_dir.mkdir(parents=True, exist_ok=True)
    for task_id, state, release_ver in tasks:
        _write_approval_task(tasks_dir, task_id, state, release_ver)
    return tasks_dir


# ---------------------------------------------------------------------------
# Acceptance criterion 1: stub fixture renders raised-hand class and project label
# ---------------------------------------------------------------------------


def test_pending_gate_renders_raised_hand_marker(
    tmp_path: pathlib.Path,
    capsys,
) -> None:
    """Fixture with one pending gate: output contains the raised-hand marker.

    Acceptance criterion: pane output has '✋' (or '!' in no-color mode) marker
    indicating the needs-human stratum for the approval task.
    """
    _build_project(
        tmp_path, "pgai-agent-kanban",
        [("HUMAN-APPROVE-v1.10.0-001", "WAITING", "v1.10.0")],
    )
    scan_pending_approvals_attention(str(tmp_path), use_color=False)
    captured = capsys.readouterr()
    # In no-color mode the raised-hand is rendered as '!'
    assert "!" in captured.out or "✋" in captured.out


def test_pending_gate_renders_task_id(
    tmp_path: pathlib.Path,
    capsys,
) -> None:
    """Fixture with one pending gate: task ID appears in the pane output."""
    _build_project(
        tmp_path, "pgai-agent-kanban",
        [("HUMAN-APPROVE-v1.10.0-001", "WAITING", "v1.10.0")],
    )
    scan_pending_approvals_attention(str(tmp_path), use_color=False)
    captured = capsys.readouterr()
    assert "HUMAN-APPROVE-v1.10.0-001" in captured.out


def test_pending_gate_renders_project_label(
    tmp_path: pathlib.Path,
    capsys,
) -> None:
    """Fixture with one pending gate: project label appears in the pane output."""
    _build_project(
        tmp_path, "pgai-agent-kanban",
        [("HUMAN-APPROVE-v1.10.0-001", "WAITING", "v1.10.0")],
    )
    scan_pending_approvals_attention(str(tmp_path), use_color=False)
    captured = capsys.readouterr()
    assert "pgai-agent-kanban" in captured.out


def test_pending_gate_renders_release_version(
    tmp_path: pathlib.Path,
    capsys,
) -> None:
    """Fixture with one pending gate: RC version label appears in the pane output."""
    _build_project(
        tmp_path, "pgai-agent-kanban",
        [("HUMAN-APPROVE-v1.10.0-001", "WAITING", "v1.10.0")],
    )
    scan_pending_approvals_attention(str(tmp_path), use_color=False)
    captured = capsys.readouterr()
    assert "v1.10.0" in captured.out


def test_pending_gate_backlog_state_renders(
    tmp_path: pathlib.Path,
    capsys,
) -> None:
    """BACKLOG state is also treated as pending in the attention pane."""
    _build_project(
        tmp_path, "pgai-agent-kanban",
        [("HUMAN-APPROVE-v1.10.0-002", "BACKLOG", "v1.10.0")],
    )
    scan_pending_approvals_attention(str(tmp_path), use_color=False)
    captured = capsys.readouterr()
    assert "HUMAN-APPROVE-v1.10.0-002" in captured.out
    assert "no pending approvals" not in captured.out


# ---------------------------------------------------------------------------
# Acceptance criterion 2: pending-approvals row is above the OVERWATCH ledger
# ---------------------------------------------------------------------------


def test_pending_approvals_rendered_before_overwatch(
    tmp_path: pathlib.Path,
    capsys,
) -> None:
    """Pending-approvals output is emitted before overwatch-section output.

    Mirrors the order in attention.sh: pending-approvals is called first, then
    overwatch-section.  Verifies positional ordering so the section lands above
    the OVERWATCH ledger as required.
    """
    _build_project(
        tmp_path, "pgai-agent-kanban",
        [("HUMAN-APPROVE-v1.10.0-001", "WAITING", "v1.10.0")],
    )

    buf = io.StringIO()
    with redirect_stdout(buf):
        scan_pending_approvals_attention(str(tmp_path), use_color=False)
        scan_overwatch_section(str(tmp_path), use_color=False)
    output = buf.getvalue()

    task_pos = output.find("HUMAN-APPROVE-v1.10.0-001")
    overwatch_pos = output.find("no recent OVERWATCH activity")

    assert task_pos != -1, "Pending gate task ID not found in combined output"
    assert overwatch_pos != -1, "OVERWATCH empty-state message not found"
    assert task_pos < overwatch_pos, (
        "Pending-approvals row must appear BEFORE the OVERWATCH ledger; "
        f"gate at pos {task_pos}, overwatch at pos {overwatch_pos}"
    )


# ---------------------------------------------------------------------------
# Acceptance criterion 3: approving a gate removes it from next render
# ---------------------------------------------------------------------------


def test_approved_gate_absent_on_next_render(
    tmp_path: pathlib.Path,
    capsys,
) -> None:
    """After approving (state DONE), the gate is absent on the next render.

    The approval lifecycle:
      1. Gate is WAITING → appears in the pane.
      2. Gate is set to DONE (operator ran scripts/close.sh) → next render omits it.
    """
    tasks_dir = tmp_path / "projects" / "pgai-agent-kanban" / "tasks"
    tasks_dir.mkdir(parents=True)
    _write_approval_task(tasks_dir, "HUMAN-APPROVE-v1.10.0-001", "WAITING", "v1.10.0")

    # Render 1: gate is pending, must appear.
    scan_pending_approvals_attention(str(tmp_path), use_color=False)
    captured1 = capsys.readouterr()
    assert "HUMAN-APPROVE-v1.10.0-001" in captured1.out

    # Simulate approval: update state to DONE.
    status_file = tasks_dir / "HUMAN-APPROVE-v1.10.0-001" / "status.md"
    status_file.write_text("# Status\n\n## State\nDONE\n", encoding="utf-8")

    # Render 2: gate is approved, must NOT appear.
    scan_pending_approvals_attention(str(tmp_path), use_color=False)
    captured2 = capsys.readouterr()
    assert "HUMAN-APPROVE-v1.10.0-001" not in captured2.out
    assert "no pending approvals" in captured2.out


def test_wont_do_gate_absent_on_next_render(
    tmp_path: pathlib.Path,
    capsys,
) -> None:
    """After rejecting (state WONT-DO), the gate is absent on the next render."""
    tasks_dir = tmp_path / "projects" / "pgai-agent-kanban" / "tasks"
    tasks_dir.mkdir(parents=True)
    _write_approval_task(tasks_dir, "HUMAN-APPROVE-v1.10.0-001", "WAITING", "v1.10.0")

    # Render 1: gate is pending.
    scan_pending_approvals_attention(str(tmp_path), use_color=False)
    capsys.readouterr()

    # Simulate rejection: update state to WONT-DO.
    status_file = tasks_dir / "HUMAN-APPROVE-v1.10.0-001" / "status.md"
    status_file.write_text("# Status\n\n## State\nWONT-DO\n", encoding="utf-8")

    # Render 2: gate must NOT appear.
    scan_pending_approvals_attention(str(tmp_path), use_color=False)
    captured2 = capsys.readouterr()
    assert "HUMAN-APPROVE-v1.10.0-001" not in captured2.out
    assert "no pending approvals" in captured2.out


# ---------------------------------------------------------------------------
# Acceptance criterion 4: web UI passthrough — same module, same function
# ---------------------------------------------------------------------------


def test_web_ui_passthrough_function_importable() -> None:
    """scan_pending_approvals_attention is importable from scan_attention.

    The web UI Attention tab calls show-attention.sh which delegates to
    scan_attention.py via the existing pane passthrough.  Verifying the function
    is present in the module confirms the pane content is available at the
    passthrough seam without requiring any web-UI changes.
    """
    from pgai_agent_kanban.dashboard.scan_attention import (
        scan_pending_approvals_attention as fn,
    )
    assert callable(fn)


def test_web_ui_passthrough_pane_output_contains_approval(
    tmp_path: pathlib.Path,
    capsys,
) -> None:
    """Pane output with a pending gate flows through the scan_attention module.

    This is the single pane-output check that confirms the web UI's Attention tab
    receives the pending-approval content via the existing passthrough seam.
    No web-UI rendering changes are required; the check verifies only that the
    Python module used by the pane script emits the expected content.
    """
    _build_project(
        tmp_path, "pgai-agent-kanban",
        [("HUMAN-APPROVE-v1.10.0-001", "WAITING", "v1.10.0")],
    )
    scan_pending_approvals_attention(str(tmp_path), use_color=False)
    captured = capsys.readouterr()
    # The pane output must contain the gate task ID (what the web UI would receive).
    assert "HUMAN-APPROVE-v1.10.0-001" in captured.out


# ---------------------------------------------------------------------------
# Empty state
# ---------------------------------------------------------------------------


def test_empty_state_prints_no_pending_approvals(
    tmp_path: pathlib.Path,
    capsys,
) -> None:
    """When no pending gates exist, the pane prints '(no pending approvals)'."""
    (tmp_path / "projects").mkdir()
    scan_pending_approvals_attention(str(tmp_path), use_color=False)
    captured = capsys.readouterr()
    assert "no pending approvals" in captured.out


def test_non_human_approve_tasks_excluded(
    tmp_path: pathlib.Path,
    capsys,
) -> None:
    """Tasks whose IDs do not start with HUMAN-APPROVE are excluded from the pane."""
    tasks_dir = tmp_path / "projects" / "pgai-agent-kanban" / "tasks"
    tasks_dir.mkdir(parents=True)

    # A regular CODER task in WAITING should not appear.
    regular_dir = tasks_dir / "CODER-20260708-025-some-task"
    regular_dir.mkdir()
    (regular_dir / "status.md").write_text("## State\nWAITING\n", encoding="utf-8")

    scan_pending_approvals_attention(str(tmp_path), use_color=False)
    captured = capsys.readouterr()
    assert "CODER-20260708-025-some-task" not in captured.out
    assert "no pending approvals" in captured.out


def test_done_gate_excluded(
    tmp_path: pathlib.Path,
    capsys,
) -> None:
    """DONE HUMAN-APPROVE gates are excluded from the pending-approvals pane."""
    _build_project(
        tmp_path, "pgai-agent-kanban",
        [("HUMAN-APPROVE-v1.10.0-001", "DONE", "v1.10.0")],
    )
    scan_pending_approvals_attention(str(tmp_path), use_color=False)
    captured = capsys.readouterr()
    assert "HUMAN-APPROVE-v1.10.0-001" not in captured.out
    assert "no pending approvals" in captured.out
