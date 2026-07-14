"""
test_cm_cancel_rc_reminder.py
=============================
Grep assertions that lock the presence of the bundle-close reminder line and
the re-arming note in cm/cancel-rc.sh's post-cancel output block.

WHY GREP-ON-SOURCE
------------------
cm/cancel-rc.sh requires a live git repository, origin access, a real kanban
root, and a valid release-state.md to reach its output section — the setup cost
for a full end-to-end test is disproportionate to the change (three echo lines).
Grepping the source text is the correct tool when the acceptance criterion is
"the line exists in the script"; it is not a shortcut around behavioral testing.

WHAT IS LOCKED
--------------
1. A line in the reminder block that contains both 'close.sh' and '--state wont-do'.
2. A line in the reminder block that refers to the re-arming of the discovery
   re-selection path.
3. The reminder block is unconditional (present outside the PENDING_TASKS if-block),
   so it fires regardless of whether tasks are still pending.
"""

from __future__ import annotations

import pathlib

_THIS_FILE = pathlib.Path(__file__).resolve()
_TEAM_DIR = _THIS_FILE.parent.parent.parent   # team/
_CANCEL_RC = _TEAM_DIR / "scripts" / "cm" / "cancel-rc.sh"


def _cancel_rc_source() -> str:
    """Return the full source text of cm/cancel-rc.sh."""
    assert _CANCEL_RC.exists(), (
        f"cm/cancel-rc.sh not found at {_CANCEL_RC}.\n"
        "The file must exist and be tracked by git."
    )
    return _CANCEL_RC.read_text(encoding="utf-8")


def test_bundle_close_command_present() -> None:
    """Post-cancel reminder includes 'close.sh' and '--state wont-do' on the same line.

    This assertion locks acceptance criterion 1: running cm/cancel-rc.sh emits
    reminder output that grep-matches 'close.sh' and '--state wont-do' on the
    bundle-close line.
    """
    source = _cancel_rc_source()
    bundle_close_lines = [
        line for line in source.splitlines()
        if "close.sh" in line and "--state wont-do" in line
    ]
    assert bundle_close_lines, (
        "No line in cm/cancel-rc.sh contains both 'close.sh' and '--state wont-do'.\n"
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
        "No line in cm/cancel-rc.sh contains both 're-arms' and 're-selection'.\n"
        "The post-cancel reminder must warn that leaving the bundle live re-arms\n"
        "the discovery re-selection path (BUG-0028 rider acceptance criterion).\n\n"
        "Expected a line of the form:\n"
        "  Leaving the bundle live re-arms the discovery re-selection path.\n"
        f"\nSearched: {_CANCEL_RC}"
    )


def test_bundle_close_reminder_is_unconditional() -> None:
    """Bundle-close reminder appears outside the PENDING_TASKS conditional block.

    The operator must see the bundle-close command even when all tasks are already
    marked WONT-DO.  This test verifies the echo is not nested inside the
    'if [[ ${#PENDING_TASKS[@]} -gt 0 ]]' block.

    Strategy: find the line index of 'fi' that closes the PENDING_TASKS block,
    then verify that the bundle-close echo line appears AFTER that 'fi' in the
    source.
    """
    source = _cancel_rc_source()
    lines = source.splitlines()

    # Find the PENDING_TASKS conditional start.
    pending_if_idx = next(
        (i for i, ln in enumerate(lines) if "PENDING_TASKS" in ln and "if" in ln),
        None,
    )
    assert pending_if_idx is not None, (
        "Could not locate the 'if [[ ${#PENDING_TASKS[@]} ... ]]' block.\n"
        "The script structure may have changed; update this test accordingly."
    )

    # Find the 'fi' that closes the PENDING_TASKS block.
    pending_fi_idx = next(
        (i for i, ln in enumerate(lines)
         if i > pending_if_idx and ln.strip() == "fi"),
        None,
    )
    assert pending_fi_idx is not None, (
        "Could not locate the 'fi' closing the PENDING_TASKS if-block.\n"
        "The script structure may have changed; update this test accordingly."
    )

    # Find the bundle-close echo line.
    bundle_close_idx = next(
        (i for i, ln in enumerate(lines)
         if "close.sh" in ln and "--state wont-do" in ln),
        None,
    )
    assert bundle_close_idx is not None, (
        "No bundle-close line found — see test_bundle_close_command_present."
    )

    assert bundle_close_idx > pending_fi_idx, (
        f"Bundle-close echo (line {bundle_close_idx + 1}) is inside or before the "
        f"PENDING_TASKS if-block (closed at line {pending_fi_idx + 1}).\n"
        "The bundle-close reminder must be unconditional — it must appear after the 'fi'."
    )
