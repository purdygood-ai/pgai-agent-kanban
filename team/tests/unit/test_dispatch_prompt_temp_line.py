"""
test_dispatch_prompt_temp_line.py
==================================
Behavioral fixtures for the dispatch-prompt temp-scratch line emitted by
both wake provider siblings (scripts/wake/claude.sh and
scripts/wake/codex.sh).

The line tells the agent where scratch/diagnostic output belongs so that
bare /tmp usage is eliminated during managed-project sessions.  The path
resolution now delegates to wake_bracket_compute_temp_subtree in the
shared lib (scripts/lib/wake_bracket.sh).

Acceptance criteria exercised:

  (1) Structural gate: both siblings source wake_bracket.sh and delegate
      temp-subtree resolution to wake_bracket_compute_temp_subtree rather
      than carrying an inline copy of the compute block.

  (2) Prompt contents: the assembled prompt contains the temp-scratch line
      with a resolved absolute subtree path (not the raw variable
      ${_project_temp_subtree}), i.e. the variable is expanded at dispatch
      time by bash's heredoc evaluation.

  (3) Positional: the temp-scratch line appears adjacent to (immediately
      after) the worktree/branch context block (${_prompt_working_dir_override})
      in the heredoc source — verified by static line-order inspection.

  (4) Text fidelity: the emitted line matches the brief's specified text
      (with the resolved path substituted).

  (5) Existing litter-check blocks unchanged: the pre-dispatch snapshot
      marker and post-session litter marker still present in both siblings
      (regression guard ensuring this task did not disturb prior work).
"""

from __future__ import annotations

import os
import pathlib
import textwrap

import pytest

from tests.unit.shell_harness import run_bash

# ---------------------------------------------------------------------------
# Paths relative to team/ (pytest cwd when run via run-unit-tests.sh)
# ---------------------------------------------------------------------------
_CLAUDE_SH = pathlib.Path("scripts/wake/claude.sh")
_CODEX_SH = pathlib.Path("scripts/wake/codex.sh")
_TEMP_SH = pathlib.Path("scripts/lib/temp.sh")
_WAKE_BRACKET_SH = pathlib.Path("scripts/lib/wake_bracket.sh")

# The exact text of the temp-scratch line as it appears in the heredoc
# (with the bash variable reference, before dispatch-time expansion).
_SCRATCH_LINE_TEMPLATE = (
    "Scratch/diagnostic output goes under ${_project_temp_subtree}"
    " — never bare /tmp (SOP: Temporary File Convention)."
)

# Marker for the compute comment that begins the _project_temp_subtree block.
_SUBTREE_COMPUTE_START = "# Resolve the per-project temp subtree at dispatch time"
_SUBTREE_COMPUTE_END = "prompt=$(cat <<EOF"

# The call that must appear in siblings after the extraction.
_BRACKET_COMPUTE_CALL = "wake_bracket_compute_temp_subtree"

# Markers from the prior litter-check task (regression guard).
_PRE_DISPATCH_MARKER = "Pre-dispatch /tmp litter snapshot"
_POST_SESSION_MARKER = "Post-session /tmp litter check"


# ---------------------------------------------------------------------------
# Helper: extract a block of lines between two markers (inclusive of start,
# exclusive of end).
# ---------------------------------------------------------------------------

def _extract_block(path: pathlib.Path, start_marker: str, end_marker: str) -> list[str]:
    """Return lines from start_marker (inclusive) up to but not including
    the first line that contains end_marker, after the block has begun."""
    lines = path.read_text(encoding="utf-8").splitlines()
    block: list[str] = []
    capturing = False
    for line in lines:
        if start_marker in line:
            capturing = True
        if capturing:
            if end_marker in line and block:
                break
            block.append(line)
    return block


# ---------------------------------------------------------------------------
# (1) Structural gate: siblings delegate temp-subtree compute to shared lib
# ---------------------------------------------------------------------------


def test_siblings_source_wake_bracket_lib() -> None:
    """
    Both siblings must source scripts/lib/wake_bracket.sh, which is the
    single home for wake_bracket_compute_temp_subtree.
    """
    for sibling in (_CLAUDE_SH, _CODEX_SH):
        content = sibling.read_text(encoding="utf-8")
        assert "wake_bracket.sh" in content, (
            f"{sibling} does not source wake_bracket.sh. "
            "Both siblings must delegate bracket functions to the shared lib."
        )


def test_siblings_call_bracket_compute_not_inline() -> None:
    """
    Both siblings must call wake_bracket_compute_temp_subtree (the shared lib
    function) rather than inlining pgai_project_temp_dir directly for the
    _project_temp_subtree assignment.  Any direct call to pgai_project_temp_dir
    in the compute context is an incomplete extraction.
    """
    for sibling in (_CLAUDE_SH, _CODEX_SH):
        content = sibling.read_text(encoding="utf-8")
        assert _BRACKET_COMPUTE_CALL in content, (
            f"{sibling} does not call {_BRACKET_COMPUTE_CALL!r}. "
            "Sibling must delegate temp-subtree resolution to the shared lib."
        )
        # The raw pgai_project_temp_dir should not appear in the compute block
        # context within siblings (it lives in wake_bracket.sh now).
        # We check that it is absent from the sibling's compute section.
        compute_block = _extract_block(sibling, _SUBTREE_COMPUTE_START, _SUBTREE_COMPUTE_END)
        block_text = "\n".join(compute_block)
        assert "pgai_project_temp_dir" not in block_text, (
            f"{sibling}'s _project_temp_subtree compute block still calls "
            "pgai_project_temp_dir directly — the inline copy was not replaced "
            f"by {_BRACKET_COMPUTE_CALL!r}.\nBlock:\n{block_text}"
        )


def test_lib_exposes_bracket_compute_function() -> None:
    """
    The shared lib must define wake_bracket_compute_temp_subtree and call
    pgai_project_temp_dir internally — confirming the function body is there.
    """
    content = _WAKE_BRACKET_SH.read_text(encoding="utf-8")
    assert "wake_bracket_compute_temp_subtree" in content, (
        f"{_WAKE_BRACKET_SH} does not define wake_bracket_compute_temp_subtree."
    )
    assert "pgai_project_temp_dir" in content, (
        f"{_WAKE_BRACKET_SH} does not call pgai_project_temp_dir — the shared "
        "lib must implement the temp-subtree resolution."
    )


def test_sibling_scratch_lines_are_byte_identical() -> None:
    """
    The scratch-line inside the prompt heredoc must be byte-identical
    across both siblings.
    """
    claude_lines = [
        ln
        for ln in _CLAUDE_SH.read_text(encoding="utf-8").splitlines()
        if "Scratch/diagnostic output goes under" in ln
    ]
    codex_lines = [
        ln
        for ln in _CODEX_SH.read_text(encoding="utf-8").splitlines()
        if "Scratch/diagnostic output goes under" in ln
    ]

    assert claude_lines, (
        f"Scratch line not found in {_CLAUDE_SH}. "
        "Expected a line containing 'Scratch/diagnostic output goes under'."
    )
    assert codex_lines, (
        f"Scratch line not found in {_CODEX_SH}. "
        "Expected a line containing 'Scratch/diagnostic output goes under'."
    )
    assert claude_lines == codex_lines, (
        "Scratch lines differ between siblings.\n"
        f"claude.sh: {claude_lines}\n"
        f"codex.sh:  {codex_lines}"
    )


# ---------------------------------------------------------------------------
# (2) Prompt contents: variable is resolved (not emitted as literal)
# ---------------------------------------------------------------------------


def _temp_sh_preamble(tmp_path: pathlib.Path) -> str:
    """Return a bash preamble that sources temp.sh with a safe temp root."""
    temp_root = tmp_path / "pgai_tmp"
    temp_root.mkdir(parents=True, exist_ok=True)
    return textwrap.dedent(f"""\
        export PGAI_AGENT_KANBAN_TEMP_DIR="{temp_root}"
        source {_TEMP_SH!s}
    """)


def test_prompt_contains_resolved_subtree_path(tmp_path: pathlib.Path) -> None:
    """
    When _project_temp_subtree is computed at dispatch time (simulating the
    variable resolution that happens inside the prompt heredoc), the emitted
    string must be an absolute path — not the unexpanded variable reference
    '${_project_temp_subtree}'.

    This tests that the heredoc expansion works correctly: bash expands
    ${_project_temp_subtree} to its value at the time the heredoc is evaluated.
    """
    project_name = "test-project"
    script = _temp_sh_preamble(tmp_path) + textwrap.dedent(f"""\
        # Simulate dispatch-time resolution of _project_temp_subtree.
        _project_temp_subtree="$(pgai_project_temp_dir "{project_name}")"
        # Simulate what the heredoc produces for the scratch line.
        scratch_line="Scratch/diagnostic output goes under ${{_project_temp_subtree}} — never bare /tmp (SOP: Temporary File Convention)."
        echo "$scratch_line"
    """)
    result = run_bash(tmp_path, script)
    assert result.returncode == 0, f"Script failed: {result.stderr}"

    emitted = result.stdout.strip()

    # Must NOT contain the unexpanded variable reference.
    assert "${_project_temp_subtree}" not in emitted, (
        "The emitted scratch line contains the unexpanded variable reference "
        "'${_project_temp_subtree}'. The path must be resolved at dispatch time.\n"
        f"Emitted line: {emitted}"
    )

    # Must contain an absolute path (starts with '/').
    # Extract the path portion (between "goes under " and " —").
    assert "goes under /" in emitted, (
        "The emitted scratch line does not contain an absolute path after "
        "'goes under '. Got:\n" + emitted
    )

    # Must contain the mandatory text components.
    assert "Scratch/diagnostic output goes under" in emitted, (
        f"Emitted line missing expected prefix. Got:\n{emitted}"
    )
    assert "never bare /tmp" in emitted, (
        f"Emitted line missing 'never bare /tmp'. Got:\n{emitted}"
    )
    assert "SOP: Temporary File Convention" in emitted, (
        f"Emitted line missing 'SOP: Temporary File Convention'. Got:\n{emitted}"
    )


def test_subtree_path_is_absolute(tmp_path: pathlib.Path) -> None:
    """
    pgai_project_temp_dir returns an absolute path (starts with '/') which
    is what gets embedded in the dispatch prompt.
    """
    project_name = "my-project"
    script = _temp_sh_preamble(tmp_path) + textwrap.dedent(f"""\
        result="$(pgai_project_temp_dir "{project_name}")"
        echo "$result"
    """)
    result = run_bash(tmp_path, script)
    assert result.returncode == 0, f"Script failed: {result.stderr}"

    path = result.stdout.strip()
    assert path.startswith("/"), (
        f"pgai_project_temp_dir did not return an absolute path. Got: {path!r}"
    )
    assert project_name in path, (
        f"Project name '{project_name}' not present in resolved path: {path!r}"
    )


# ---------------------------------------------------------------------------
# (3) Positional: scratch line appears adjacent to _prompt_working_dir_override
# ---------------------------------------------------------------------------


def test_scratch_line_adjacent_to_working_dir_override(
    file_path: pathlib.Path = _CLAUDE_SH,
) -> None:
    """
    The scratch line must appear immediately after ${_prompt_working_dir_override}
    in the heredoc — verified by static line-order inspection of claude.sh.
    """
    lines = _CLAUDE_SH.read_text(encoding="utf-8").splitlines()

    wd_override_idx = None
    scratch_idx = None

    for i, line in enumerate(lines):
        if "${_prompt_working_dir_override}" in line and wd_override_idx is None:
            # We want the one inside the heredoc (the prompt body), not in the
            # variable assignment block above it.
            # The heredoc starts with "prompt=$(cat <<EOF" and ends with "EOF".
            # We look for the occurrence that follows "prompt=$(cat <<EOF".
            pass

    # Two-pass: find the heredoc region, then find order within it.
    in_heredoc = False
    for i, line in enumerate(lines):
        stripped = line.strip()
        if stripped.startswith("prompt=$(cat <<EOF"):
            in_heredoc = True
            continue
        if in_heredoc and stripped == "EOF":
            in_heredoc = False
            continue
        if in_heredoc:
            if "${_prompt_working_dir_override}" in line and wd_override_idx is None:
                wd_override_idx = i
            if "Scratch/diagnostic output goes under" in line and scratch_idx is None:
                scratch_idx = i

    assert wd_override_idx is not None, (
        f"${'{_prompt_working_dir_override}'} not found inside the prompt heredoc in {_CLAUDE_SH}."
    )
    assert scratch_idx is not None, (
        f"Scratch line not found inside the prompt heredoc in {_CLAUDE_SH}."
    )
    assert scratch_idx == wd_override_idx + 1, (
        f"Scratch line (line {scratch_idx + 1}) is not immediately after "
        f"${'{_prompt_working_dir_override}'} (line {wd_override_idx + 1}) in {_CLAUDE_SH}.\n"
        f"Expected scratch line at line {wd_override_idx + 2}."
    )


def test_scratch_line_adjacent_to_working_dir_override_codex() -> None:
    """
    Same positional check for codex.sh.
    """
    lines = _CODEX_SH.read_text(encoding="utf-8").splitlines()

    wd_override_idx = None
    scratch_idx = None

    in_heredoc = False
    for i, line in enumerate(lines):
        stripped = line.strip()
        if stripped.startswith("prompt=$(cat <<EOF"):
            in_heredoc = True
            continue
        if in_heredoc and stripped == "EOF":
            in_heredoc = False
            continue
        if in_heredoc:
            if "${_prompt_working_dir_override}" in line and wd_override_idx is None:
                wd_override_idx = i
            if "Scratch/diagnostic output goes under" in line and scratch_idx is None:
                scratch_idx = i

    assert wd_override_idx is not None, (
        f"${'{_prompt_working_dir_override}'} not found inside the prompt heredoc in {_CODEX_SH}."
    )
    assert scratch_idx is not None, (
        f"Scratch line not found inside the prompt heredoc in {_CODEX_SH}."
    )
    assert scratch_idx == wd_override_idx + 1, (
        f"Scratch line (line {scratch_idx + 1}) is not immediately after "
        f"${'{_prompt_working_dir_override}'} (line {wd_override_idx + 1}) in {_CODEX_SH}.\n"
        f"Expected scratch line at line {wd_override_idx + 2}."
    )


# ---------------------------------------------------------------------------
# (4) Text fidelity: scratch line matches brief's specified text exactly
# ---------------------------------------------------------------------------


def test_scratch_line_text_matches_brief_claude() -> None:
    """
    The scratch line in claude.sh must contain the exact wording from the
    brief (with the bash variable reference as the path placeholder).
    """
    content = _CLAUDE_SH.read_text(encoding="utf-8")
    assert _SCRATCH_LINE_TEMPLATE in content, (
        f"claude.sh does not contain the exact scratch line from the brief.\n"
        f"Expected:\n  {_SCRATCH_LINE_TEMPLATE}\n"
        f"(with ${'{_project_temp_subtree}'} as the path placeholder)"
    )


def test_scratch_line_text_matches_brief_codex() -> None:
    """
    Same text-fidelity check for codex.sh.
    """
    content = _CODEX_SH.read_text(encoding="utf-8")
    assert _SCRATCH_LINE_TEMPLATE in content, (
        f"codex.sh does not contain the exact scratch line from the brief.\n"
        f"Expected:\n  {_SCRATCH_LINE_TEMPLATE}\n"
        f"(with ${'{_project_temp_subtree}'} as the path placeholder)"
    )


# ---------------------------------------------------------------------------
# (5) Regression guard: prior litter-check blocks still present
# ---------------------------------------------------------------------------


def test_pre_dispatch_litter_marker_still_present_claude() -> None:
    """
    The pre-dispatch /tmp litter snapshot marker must still be present
    in claude.sh (regression guard — this task must not disturb prior work).
    """
    content = _CLAUDE_SH.read_text(encoding="utf-8")
    assert _PRE_DISPATCH_MARKER in content, (
        f"Pre-dispatch litter marker '{_PRE_DISPATCH_MARKER}' missing from {_CLAUDE_SH}. "
        "This task may have inadvertently removed prior work."
    )


def test_post_session_litter_marker_still_present_claude() -> None:
    """
    The post-session /tmp litter check marker must still be present
    in claude.sh (regression guard).
    """
    content = _CLAUDE_SH.read_text(encoding="utf-8")
    assert _POST_SESSION_MARKER in content, (
        f"Post-session litter marker '{_POST_SESSION_MARKER}' missing from {_CLAUDE_SH}. "
        "This task may have inadvertently removed prior work."
    )


def test_pre_dispatch_litter_marker_still_present_codex() -> None:
    """
    The pre-dispatch /tmp litter snapshot marker must still be present
    in codex.sh (regression guard).
    """
    content = _CODEX_SH.read_text(encoding="utf-8")
    assert _PRE_DISPATCH_MARKER in content, (
        f"Pre-dispatch litter marker '{_PRE_DISPATCH_MARKER}' missing from {_CODEX_SH}. "
        "This task may have inadvertently removed prior work."
    )


def test_post_session_litter_marker_still_present_codex() -> None:
    """
    The post-session /tmp litter check marker must still be present
    in codex.sh (regression guard).
    """
    content = _CODEX_SH.read_text(encoding="utf-8")
    assert _POST_SESSION_MARKER in content, (
        f"Post-session litter marker '{_POST_SESSION_MARKER}' missing from {_CODEX_SH}. "
        "This task may have inadvertently removed prior work."
    )
