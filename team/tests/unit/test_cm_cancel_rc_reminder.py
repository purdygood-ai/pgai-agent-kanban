"""
test_cm_cancel_rc_reminder.py
=============================
Grep assertions that lock the presence of the bundle-close reminder line and
the re-arming note in the cancel-rc verb's post-cancel output block, which
lives in pgai_agent_kanban/cm/cancel.py (cm/cancel-rc.sh reaches it through
pp_run_ops).

WHY GREP-ON-SOURCE
------------------
The acceptance criterion is "the line exists in the verb's output block";
grepping the source text locks exactly that. The printed output is asserted
behaviourally in tests/unit/test_cm_cancel.py (the reminders print with and
without pending tasks) and byte-for-byte against the recorded CLI output in
tests/integration/test_cm_cancel_rc.py.

WHAT IS LOCKED
--------------
1. A line in the reminder block that contains both 'close.sh' and '--state wont-do'.
2. A line in the reminder block that refers to the re-arming of the discovery
   re-selection path.
3. The reminder block is unconditional (outside the ``if pending:`` block),
   so it fires regardless of whether tasks are still pending.
"""

from __future__ import annotations

import pathlib

_THIS_FILE = pathlib.Path(__file__).resolve()
_TEAM_DIR = _THIS_FILE.parent.parent.parent   # team/
_CANCEL_RC = _TEAM_DIR / "pgai_agent_kanban" / "cm" / "cancel.py"


def _cancel_rc_source() -> str:
    """Return the full source text of pgai_agent_kanban/cm/cancel.py."""
    assert _CANCEL_RC.exists(), (
        f"cm/cancel.py not found at {_CANCEL_RC}.\n"
        "The file must exist and be tracked by git."
    )
    return _CANCEL_RC.read_text(encoding="utf-8")


def test_bundle_close_command_present() -> None:
    """Post-cancel reminder includes 'close.sh' and '--state wont-do' on the same line.

    This assertion locks acceptance criterion 1: running the cancel-rc verb emits
    reminder output that grep-matches 'close.sh' and '--state wont-do' on the
    bundle-close line.
    """
    source = _cancel_rc_source()
    bundle_close_lines = [
        line for line in source.splitlines()
        if "close.sh" in line and "--state wont-do" in line
    ]
    assert bundle_close_lines, (
        "No line in cm/cancel.py contains both 'close.sh' and '--state wont-do'.\n"
        "The post-cancel reminder must include the bundle-close command so the operator\n"
        "knows to close the requirements intake item after cancelling the RC.\n\n"
        "Expected a line of the form:\n"
        "  close.sh --project ... --key ...-bugfix-bundle-<YYYYMMDD> --state wont-do\n"
        f"\nSearched: {_CANCEL_RC}"
    )


def test_bundle_close_command_names_project_and_key() -> None:
    """Bundle-close reminder line contains '--project' and '--key' flags.

    The operator must be able to copy the command and fill in only the date
    suffix — all other flags must be present or clearly derivable.
    """
    source = _cancel_rc_source()
    bundle_close_lines = [
        line for line in source.splitlines()
        if "close.sh" in line and "--state wont-do" in line
    ]
    assert bundle_close_lines, (
        "No bundle-close line found — see test_bundle_close_command_present."
    )
    line = bundle_close_lines[0]
    assert "--project" in line, (
        f"Bundle-close line missing '--project' flag.\nLine: {line!r}"
    )
    assert "--key" in line, (
        f"Bundle-close line missing '--key' flag.\nLine: {line!r}"
    )


def test_rearming_note_present() -> None:
    """Post-cancel reminder includes a note about the discovery re-selection path.

    This assertion locks acceptance criterion 2: the reminder must warn the operator
    that leaving the bundle live re-arms the discovery re-selection path.
    """
    source = _cancel_rc_source()
    rearming_lines = [
        line for line in source.splitlines()
        if "re-arms" in line and "re-selection" in line
    ]
    assert rearming_lines, (
        "No line in cm/cancel.py contains both 're-arms' and 're-selection'.\n"
        "The post-cancel reminder must warn that leaving the bundle live re-arms\n"
        "the discovery re-selection path.\n\n"
        "Expected a line of the form:\n"
        "  Leaving the bundle live re-arms the discovery re-selection path.\n"
        f"\nSearched: {_CANCEL_RC}"
    )


def test_bundle_close_reminder_is_unconditional() -> None:
    """Bundle-close reminder appears outside the pending-tasks conditional block.

    The operator must see the bundle-close command even when all tasks are already
    marked WONT-DO.  This test verifies the line is not nested inside the
    ``if pending:`` block that repeats the WONT-DO reminder.

    Strategy: find the last ``if pending:`` line before the bundle-close line,
    then verify the bundle-close line is indented no deeper than that ``if``
    -- a line inside the block would be indented further.
    """
    lines = _cancel_rc_source().splitlines()

    bundle_close_idx = next(
        (i for i, ln in enumerate(lines)
         if "close.sh" in ln and "--state wont-do" in ln),
        None,
    )
    assert bundle_close_idx is not None, (
        "No bundle-close line found — see test_bundle_close_command_present."
    )

    pending_if_idx = max(
        (i for i, ln in enumerate(lines[:bundle_close_idx]) if ln.strip() == "if pending:"),
        default=None,
    )
    assert pending_if_idx is not None, (
        "Could not locate the 'if pending:' block ahead of the bundle-close line.\n"
        "The module structure may have changed; update this test accordingly."
    )

    def _indent(line: str) -> int:
        return len(line) - len(line.lstrip(" "))

    assert _indent(lines[bundle_close_idx]) <= _indent(lines[pending_if_idx]), (
        f"Bundle-close line (line {bundle_close_idx + 1}) is nested inside the "
        f"'if pending:' block (line {pending_if_idx + 1}).\n"
        "The bundle-close reminder must be unconditional."
    )
