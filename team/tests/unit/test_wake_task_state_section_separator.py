"""
test_wake_task_state_section_separator.py
=========================================
Behavioral fixtures for the section separator the bash status writers
set_state and set_block (team/scripts/lib/wake_task_state.sh) leave behind.

Rewriting one ``## Heading`` block of a status.md must re-emit the
separator between that block and the next heading exactly as it was
found, however many times the writer runs without a normalize pass: one
blank line stays one blank line, no blank line stays none, and the
file's end stays as it was.

A section whose body is empty is filled in place: every section splice in
the file (set_state, set_block, stamp_model_field, and both promote_*
writers) must leave every other section byte-for-byte unchanged, never
run the empty body into the next heading and delete that section.

The real wake_task_state.sh is sourced (it self-sources atomic_write.sh);
log() is the faithful stub. Every fixture lives under pytest's tmp_path,
which the gated runner roots under $PGAI_AGENT_KANBAN_TEMP_DIR; no live
task folder or kanban install is read or written.

Naming: function names describe behavior under test, never bug IDs or task
IDs (roles/TESTER.md "Test Authoring Guidelines", Anti-pattern 6).
"""

from __future__ import annotations

import pathlib
import subprocess
import textwrap

import pytest

from tests.fixtures.log_stub import make_log_stub_fragment

# This file lives at team/tests/unit/, so three parents up is team/.
_TEAM_SCRIPTS_DIR = pathlib.Path(__file__).resolve().parent.parent.parent / "scripts"
_WAKE_TASK_STATE_LIB = _TEAM_SCRIPTS_DIR / "lib" / "wake_task_state.sh"

_ONE_BLANK_FIXTURE = "## State\nBACKLOG\n\n## Summary\nx\n\n## Blockers\nnone\n"


def _run_writer_calls(tmp_path: pathlib.Path, status_file: pathlib.Path, calls: list[str]) -> None:
    """Source the real lib and run *calls* (bash lines) in order."""
    temp_dir = tmp_path / "kanban_temp"
    temp_dir.mkdir(exist_ok=True)
    script = textwrap.dedent(f"""\
        #!/usr/bin/env bash
        set -euo pipefail
        export PGAI_AGENT_KANBAN_TEMP_DIR="{temp_dir}"
        unset PGAI_AGENT_KANBAN_ROOT_PATH
        KANBAN_ROOT="{temp_dir}"
        BACKLOG="{tmp_path / 'coder_backlog.md'}"
        {make_log_stub_fragment(tmp_path / 'wake.log')}
        source "{_WAKE_TASK_STATE_LIB}"
    """) + "\n".join(calls) + "\n"
    proc = subprocess.run(["bash", "-c", script], capture_output=True, text=True, timeout=60)
    assert proc.returncode == 0, (
        f"writer calls must succeed on {status_file}\nstdout={proc.stdout}\nstderr={proc.stderr}"
    )


def _status(tmp_path: pathlib.Path, text: str) -> pathlib.Path:
    status_file = tmp_path / "status.md"
    status_file.write_text(text, encoding="utf-8")
    return status_file


def test_three_set_state_calls_keep_one_blank_line_before_next_heading(tmp_path: pathlib.Path) -> None:
    status_file = _status(tmp_path, _ONE_BLANK_FIXTURE)
    _run_writer_calls(tmp_path, status_file, [f'set_state "{status_file}" WORKING'] * 3)
    assert status_file.read_text(encoding="utf-8") == (
        "## State\nWORKING\n\n## Summary\nx\n\n## Blockers\nnone\n"
    ), "set_state must leave exactly one blank line between ## State and ## Summary"


def test_three_set_block_calls_keep_one_blank_line_before_next_heading(tmp_path: pathlib.Path) -> None:
    status_file = _status(tmp_path, _ONE_BLANK_FIXTURE)
    _run_writer_calls(tmp_path, status_file, [f'set_block "{status_file}" Summary "rewritten"'] * 3)
    assert status_file.read_text(encoding="utf-8") == (
        "## State\nBACKLOG\n\n## Summary\nrewritten\n\n## Blockers\nnone\n"
    ), "set_block must leave exactly one blank line between ## Summary and ## Blockers"


def test_set_block_on_last_section_keeps_file_end_unchanged(tmp_path: pathlib.Path) -> None:
    status_file = _status(tmp_path, _ONE_BLANK_FIXTURE)
    _run_writer_calls(tmp_path, status_file, [f'set_block "{status_file}" Blockers "blocked on x"'] * 3)
    assert status_file.read_text(encoding="utf-8") == (
        "## State\nBACKLOG\n\n## Summary\nx\n\n## Blockers\nblocked on x\n"
    ), "rewriting the final section must keep a single trailing newline, not add a blank line"


@pytest.mark.parametrize(
    "call",
    ['set_state "{f}" DONE', 'set_block "{f}" State DONE'],
    ids=["set_state", "set_block"],
)
def test_writer_keeps_absent_blank_line_absent(tmp_path: pathlib.Path, call: str) -> None:
    status_file = _status(tmp_path, "## State\nBACKLOG\n## Summary\nx\n")
    _run_writer_calls(tmp_path, status_file, [call.format(f=status_file)] * 3)
    assert status_file.read_text(encoding="utf-8") == "## State\nDONE\n## Summary\nx\n", (
        "a section followed directly by the next heading must stay that way"
    )


# --- Empty target body -------------------------------------------------------


def test_set_block_fills_empty_section_and_keeps_following_sections(tmp_path: pathlib.Path) -> None:
    status_file = _status(
        tmp_path, "## State\nBACKLOG\n\n## Summary\n\n## Blockers\nnone\n\n## Needs Human\nno\n"
    )
    _run_writer_calls(tmp_path, status_file, [f'set_block "{status_file}" Summary "new summary"'] * 3)
    assert status_file.read_text(encoding="utf-8") == (
        "## State\nBACKLOG\n\n## Summary\nnew summary\n\n## Blockers\nnone\n\n## Needs Human\nno\n"
    ), "set_block on an empty ## Summary must fill it and leave ## Blockers and ## Needs Human unchanged"


def test_set_state_fills_empty_state_and_keeps_following_sections(tmp_path: pathlib.Path) -> None:
    status_file = _status(tmp_path, "## State\n\n## Summary\nx\n\n## Blockers\nnone\n")
    _run_writer_calls(tmp_path, status_file, [f'set_state "{status_file}" WORKING'] * 3)
    assert status_file.read_text(encoding="utf-8") == (
        "## State\nWORKING\n\n## Summary\nx\n\n## Blockers\nnone\n"
    ), "set_state on an empty ## State must fill it and leave ## Summary and ## Blockers unchanged"


@pytest.mark.parametrize(
    ("fixture", "expected"),
    [
        ("## State\n## Summary\nx\n", "## State\nDONE\n## Summary\nx\n"),
        ("## Summary\nx\n\n## State\n", "## Summary\nx\n\n## State\nDONE\n"),
        ("## Summary\nx\n\n## State", "## Summary\nx\n\n## State\nDONE\n"),
    ],
    ids=["no-blank-line-before-next-heading", "empty-last-section", "empty-last-section-no-newline"],
)
def test_set_state_fills_empty_state_in_other_shapes(
    tmp_path: pathlib.Path, fixture: str, expected: str
) -> None:
    status_file = _status(tmp_path, fixture)
    _run_writer_calls(tmp_path, status_file, [f'set_state "{status_file}" DONE'] * 2)
    assert status_file.read_text(encoding="utf-8") == expected


def test_stamp_model_field_fills_empty_model_and_keeps_following_sections(tmp_path: pathlib.Path) -> None:
    status_file = _status(tmp_path, "## Role\nCODER\n\n## Model\n\n## State\nWORKING\n")
    _run_writer_calls(tmp_path, status_file, [f'stamp_model_field "{status_file}" model-a'] * 2)
    assert status_file.read_text(encoding="utf-8") == (
        "## Role\nCODER\n\n## Model\nmodel-a\n\n## State\nWORKING\n"
    ), "stamp_model_field on an empty ## Model must fill it and leave ## State unchanged"


def test_promote_waiting_fills_empty_sections_and_keeps_the_rest(tmp_path: pathlib.Path) -> None:
    (tmp_path / "coder_backlog.md").write_text("- [W] CODER-20260101-001-demo\n", encoding="utf-8")
    status_file = _status(
        tmp_path,
        "## State\nWAITING\n\n## Summary\n\n## Blockers\n\n## Needs Human\nno\n\n"
        "## Next Recommended Step\ngo\n",
    )
    _run_writer_calls(tmp_path, status_file, [f'promote_waiting_to_backlog CODER-20260101-001-demo "{status_file}"'])
    assert status_file.read_text(encoding="utf-8") == (
        "## State\nBACKLOG\n\n"
        "## Summary\nPromoted from WAITING to BACKLOG by wake script: prerequisites satisfied.\n\n"
        "## Blockers\nnone\n\n## Needs Human\nno\n\n## Next Recommended Step\ngo\n"
    )


def test_promote_blocked_fills_empty_sections_and_keeps_the_rest(tmp_path: pathlib.Path) -> None:
    (tmp_path / "coder_backlog.md").write_text("- [B] CODER-20260101-001-demo\n", encoding="utf-8")
    status_file = _status(
        tmp_path,
        "## State\nBLOCKED\n\n## Summary\nold\n\n## Blockers\n\n## Needs Human\nyes\n\n"
        "## Blocked Reason\n\n## Next Recommended Step\ngo\n",
    )
    _run_writer_calls(
        tmp_path, status_file, [f'promote_blocked_to_backlog CODER-20260101-001-demo "{status_file}" cleared']
    )
    assert status_file.read_text(encoding="utf-8") == (
        "## State\nBACKLOG\n\n"
        "## Summary\nPromoted from BLOCKED to BACKLOG by wake script: blocker cleared (cleared).\n\n"
        "## Blockers\nnone\n\n## Needs Human\nno\n\n## Blocked Reason\nnone\n\n"
        "## Next Recommended Step\ngo\n"
    )
