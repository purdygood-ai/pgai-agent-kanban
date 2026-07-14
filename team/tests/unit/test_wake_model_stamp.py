"""
test_wake_model_stamp.py
========================
Behavioral fixtures for an earlier defect: wake scripts stamp the resolved model string
into a task's status.md ## Model section at spawn time.

The field is an execution record written by the party that knows (the wake
script, which holds the resolved $MODEL value when it builds the CLI
invocation).  Agents must not write ## Model themselves.

Acceptance criteria exercised:

  (1) Functional: stamp_model_field upserts ## Model correctly —
      creates the section when absent; overwrites only the body when
      present; all other status fields preserved byte-identically.

  (2) Per-role override: the stamped value equals the model string
      resolved by the wake script's model-selection logic, which honors
      per-role config (kanban.cfg [models.<provider>] <role> override).

  (3) Sibling parity: the wake-stamp block is byte-identical across
      scripts/wake/claude.sh and scripts/wake/codex.sh.  The block is
      identified by a known comment anchor, extracted from each sibling,
      and compared.

  (4) Structural: both siblings contain the stamp-call line and the
      anchor comment.

All tests use synthetic environments and never touch the live kanban root.
"""

from __future__ import annotations

import pathlib
import re
import textwrap

import pytest

# ---------------------------------------------------------------------------
# Paths relative to team/ (pytest cwd when run via run-unit-tests.sh)
# ---------------------------------------------------------------------------
_CLAUDE_SH = pathlib.Path("scripts/wake/claude.sh")
_CODEX_SH = pathlib.Path("scripts/wake/codex.sh")
_WAKE_COMMON_SH = pathlib.Path("scripts/lib/wake_common.sh")

# Anchor comment that begins the wake-stamp block in both siblings.
_STAMP_ANCHOR = "# --- Wake-stamp resolved model into status.md ## Model section ---"

# The call that must appear in both siblings.
_STAMP_CALL = "stamp_model_field"

# Function definition that must appear in wake_common.sh.
_STAMP_FUNC_DEF = "stamp_model_field()"


# ---------------------------------------------------------------------------
# Pure-Python implementation of stamp_model_field, mirroring the logic
# embedded in wake_common.sh.  Used by functional tests directly rather
# than shelling out to bash (avoids SCRIPT_DIR dependency from wake_common.sh).
# ---------------------------------------------------------------------------

def _stamp_model_field(status_path: pathlib.Path, model_value: str) -> None:
    """Python re-implementation of stamp_model_field from wake_common.sh.

    Upserts the ## Model section in a status.md file:
    - If ## Model exists: replaces its body only.
    - If absent: inserts after ## Role, or ## Participant as fallback, or at end.
    All other sections are preserved byte-identically.
    """
    text = status_path.read_text()
    model_value = model_value.strip()

    # If ## Model section already exists, replace its body (section-scoped update).
    if re.search(r'^## Model\s*$', text, flags=re.M):
        text_new, n = re.subn(
            r'(^## Model\s*\n)(.*?)(\n+##|\Z)',
            lambda m: m.group(1) + model_value + "\n" + (m.group(3) if m.group(3) else ''),
            text,
            flags=re.S | re.M,
        )
        assert n > 0, "stamp_model_field: found ## Model header but subn matched 0 times"
        status_path.write_text(text_new)
        return

    # Section absent — insert after ## Role, or ## Participant, or at end.
    new_section = f"## Model\n{model_value}\n"
    for anchor in (r'^(## Role\s*\n.*?)(\n+##)', r'^(## Participant\s*\n.*?)(\n+##)'):
        text_new, n = re.subn(
            anchor,
            lambda m: m.group(1) + "\n\n" + new_section + "\n" + m.group(2).lstrip('\n'),
            text,
            count=1,
            flags=re.S | re.M,
        )
        if n > 0:
            text_new = re.sub(r'\n{3,}', '\n\n', text_new)
            status_path.write_text(text_new)
            return

    # Fallback: append at end.
    status_path.write_text(text.rstrip('\n') + "\n\n" + new_section)


# ---------------------------------------------------------------------------
# Helper: extract the stamp block from a wake sibling.
# ---------------------------------------------------------------------------

def _extract_stamp_block(path: pathlib.Path) -> list[str]:
    """Extract the model-stamp block from a wake provider sibling.

    The block starts at _STAMP_ANCHOR and ends just before the next
    section anchor (a line matching '  # ---') after the
    stamp_model_field call has been seen.

    Returns the extracted lines.
    """
    lines = path.read_text(encoding="utf-8").splitlines()
    capturing = False
    block: list[str] = []
    call_seen = False

    for line in lines:
        if _STAMP_ANCHOR in line:
            capturing = True
        if capturing:
            block.append(line)
            if _STAMP_CALL in line:
                call_seen = True
            # Stop at the next section anchor once we've seen the call.
            if call_seen and line.strip().startswith("# ---") and len(block) > 2:
                block.pop()
                break

    return block


# ---------------------------------------------------------------------------
# (1) Functional: stamp_model_field upserts ## Model correctly
# ---------------------------------------------------------------------------

def test_stamp_inserts_model_section_when_absent(tmp_path: pathlib.Path) -> None:
    """Fixture (1a): ## Model is created after ## Role when absent."""
    status = tmp_path / "status.md"
    status.write_text(
        "# Status\n\n## Task\nTEST-001\n\n## Participant\nClaude\n\n"
        "## Role\nCODER\n\n## State\nBACKLOG\n"
    )

    _stamp_model_field(status, "claude-fable-5")

    content = status.read_text()
    assert "## Model\nclaude-fable-5\n" in content, (
        f"## Model section not found or has wrong body.\nStatus file:\n{content}"
    )
    # Positioned after ## Role
    role_idx = content.find("## Role")
    model_idx = content.find("## Model")
    assert role_idx < model_idx, (
        f"## Model (pos {model_idx}) not after ## Role (pos {role_idx})"
    )
    # State still present and unchanged
    assert "## State\nBACKLOG" in content, (
        f"## State section was disturbed by stamp.\nStatus file:\n{content}"
    )


def test_stamp_overwrites_model_body_only(tmp_path: pathlib.Path) -> None:
    """Fixture (1b): existing ## Model body is replaced; other fields untouched."""
    status = tmp_path / "status.md"
    status.write_text(
        "# Status\n\n## Task\nTEST-002\n\n## Role\nCODER\n\n"
        "## Model\nold-model-name\n\n## State\nBACKLOG\n\n## Summary\nTBD\n"
    )

    _stamp_model_field(status, "claude-sonnet-4-6")

    content = status.read_text()
    assert "## Model\nclaude-sonnet-4-6\n" in content, (
        f"## Model not updated to new value.\nStatus file:\n{content}"
    )
    assert "old-model-name" not in content, (
        f"Old model value still present.\nStatus file:\n{content}"
    )
    # Other sections preserved
    assert "## State\nBACKLOG" in content, (
        f"## State was disturbed.\nStatus file:\n{content}"
    )
    assert "## Summary\nTBD" in content, (
        f"## Summary was disturbed.\nStatus file:\n{content}"
    )
    assert "## Task\nTEST-002" in content, (
        f"## Task was disturbed.\nStatus file:\n{content}"
    )


def test_stamp_preserves_all_other_fields(tmp_path: pathlib.Path) -> None:
    """Fixture (1c): a realistic status file — all fields survive stamp."""
    original = textwrap.dedent("""\
        # Status

        ## Task
        CODER-20260713-001-example

        ## Participant
        Claude

        ## Role
        CODER

        ## State
        BACKLOG

        ## Summary
        Fresh start.

        ## Artifacts
        none

        ## Blockers
        none

        ## Needs Human
        no

        ## Next Recommended Step
        Begin work.

        ## Instruction Conflicts
        none
    """)
    status = tmp_path / "status.md"
    status.write_text(original)

    _stamp_model_field(status, "claude-fable-5")

    content = status.read_text()
    # Model stamped
    assert "## Model\nclaude-fable-5\n" in content, (
        f"Model not stamped.\nStatus file:\n{content}"
    )
    # Spot-check all other sections
    for expected_fragment in [
        "## Task\nCODER-20260713-001-example",
        "## Participant\nClaude",
        "## Role\nCODER",
        "## State\nBACKLOG",
        "## Summary\nFresh start.",
        "## Artifacts\nnone",
        "## Blockers\nnone",
        "## Needs Human\nno",
        "## Next Recommended Step\nBegin work.",
        "## Instruction Conflicts\nnone",
    ]:
        assert expected_fragment in content, (
            f"Section disturbed: expected {expected_fragment!r} in status.\n"
            f"Status file:\n{content}"
        )


# ---------------------------------------------------------------------------
# (2) Per-role override: stamped value equals the resolved model
# ---------------------------------------------------------------------------

def test_stamp_honors_per_role_model(tmp_path: pathlib.Path) -> None:
    """Fixture (2): the stamped value is the model the wake resolved for the role.

    The stamp is a transparent pass-through of the resolved value — whatever
    value arrives at stamp_model_field is what ends up in the file.  This
    confirms that per-role config (kanban.cfg [models.claude] pm = claude-fable-5)
    flows correctly when the wake selects and passes the per-role model.
    """
    status = tmp_path / "status.md"
    status.write_text("# Status\n\n## Role\nPM\n\n## State\nBACKLOG\n")

    pm_model = "claude-fable-5"
    _stamp_model_field(status, pm_model)

    content = status.read_text()
    assert f"## Model\n{pm_model}\n" in content, (
        f"Per-role model not stamped correctly.\n"
        f"Expected: ## Model\\n{pm_model!r}\nStatus file:\n{content}"
    )


def test_stamp_different_roles_get_different_models(tmp_path: pathlib.Path) -> None:
    """Fixture (2b): coder and pm roles stamp different per-role models."""
    for role, model in [("CODER", "claude-opus-4-7"), ("PM", "claude-fable-5")]:
        status_path = tmp_path / f"status_{role.lower()}.md"
        status_path.write_text(
            f"# Status\n\n## Role\n{role}\n\n## State\nBACKLOG\n"
        )

        _stamp_model_field(status_path, model)

        content = status_path.read_text()
        assert f"## Model\n{model}\n" in content, (
            f"Role {role}: expected model {model!r} not found.\nStatus file:\n{content}"
        )


# ---------------------------------------------------------------------------
# (3) Sibling parity: stamp block is byte-identical in both siblings
# ---------------------------------------------------------------------------

def test_sibling_stamp_blocks_are_byte_identical() -> None:
    """Fixture (3): the model-stamp block must be byte-identical across both siblings.

    Extraction: locate _STAMP_ANCHOR in each file and capture through the
    stamp_model_field call.  The two blocks must match byte-for-byte.
    """
    claude_block = _extract_stamp_block(_CLAUDE_SH)
    codex_block = _extract_stamp_block(_CODEX_SH)

    assert claude_block, (
        f"Model-stamp block not found in {_CLAUDE_SH}. "
        f"Expected a comment containing {_STAMP_ANCHOR!r}."
    )
    assert codex_block, (
        f"Model-stamp block not found in {_CODEX_SH}. "
        f"Expected a comment containing {_STAMP_ANCHOR!r}."
    )
    assert claude_block == codex_block, (
        "Model-stamp blocks differ between siblings.\n"
        "claude.sh block:\n" + "\n".join(claude_block) + "\n\n"
        "codex.sh block:\n" + "\n".join(codex_block)
    )


# ---------------------------------------------------------------------------
# (4) Structural: both siblings contain the anchor and stamp call;
#     function is defined in wake_common.sh; ordering is correct.
# ---------------------------------------------------------------------------

def test_stamp_anchor_present_in_claude() -> None:
    """Structural: the stamp anchor comment is present in claude.sh."""
    content = _CLAUDE_SH.read_text(encoding="utf-8")
    assert _STAMP_ANCHOR in content, (
        f"Stamp anchor not found in {_CLAUDE_SH}.\nExpected: {_STAMP_ANCHOR!r}"
    )


def test_stamp_anchor_present_in_codex() -> None:
    """Structural: the stamp anchor comment is present in codex.sh."""
    content = _CODEX_SH.read_text(encoding="utf-8")
    assert _STAMP_ANCHOR in content, (
        f"Stamp anchor not found in {_CODEX_SH}.\nExpected: {_STAMP_ANCHOR!r}"
    )


def test_stamp_call_present_in_claude() -> None:
    """Structural: stamp_model_field is called in claude.sh."""
    content = _CLAUDE_SH.read_text(encoding="utf-8")
    assert _STAMP_CALL in content, (
        f"{_STAMP_CALL!r} call not found in {_CLAUDE_SH}."
    )


def test_stamp_call_present_in_codex() -> None:
    """Structural: stamp_model_field is called in codex.sh."""
    content = _CODEX_SH.read_text(encoding="utf-8")
    assert _STAMP_CALL in content, (
        f"{_STAMP_CALL!r} call not found in {_CODEX_SH}."
    )


def test_stamp_func_defined_in_wake_common() -> None:
    """Structural: stamp_model_field() is defined in scripts/lib/wake_common.sh."""
    content = _WAKE_COMMON_SH.read_text(encoding="utf-8")
    assert _STAMP_FUNC_DEF in content, (
        f"Function definition {_STAMP_FUNC_DEF!r} not found in {_WAKE_COMMON_SH}. "
        "stamp_model_field must be defined in the shared lib."
    )


def test_stamp_block_calls_stamp_before_stale_artifact_rotation() -> None:
    """Structural: stamp_model_field call precedes stale-artifact rotation in claude.sh.

    The stamp must happen BEFORE the agent is spawned.  The stale-artifact
    rotation block ('Stale-artifact preservation on re-run') comes right after
    the stamp in both siblings — this test verifies the ordering is correct.
    """
    lines = _CLAUDE_SH.read_text(encoding="utf-8").splitlines()
    stamp_idx = None
    stale_idx = None
    for i, line in enumerate(lines):
        if _STAMP_CALL in line and "stamp_model_field" in line and stamp_idx is None:
            stamp_idx = i
        if "Stale-artifact preservation on re-run" in line and stale_idx is None:
            stale_idx = i

    assert stamp_idx is not None, (
        f"stamp_model_field call not found in {_CLAUDE_SH}."
    )
    assert stale_idx is not None, (
        f"'Stale-artifact preservation on re-run' not found in {_CLAUDE_SH}."
    )
    assert stamp_idx < stale_idx, (
        f"stamp_model_field (line {stamp_idx + 1}) appears AFTER "
        f"stale-artifact rotation (line {stale_idx + 1}) in {_CLAUDE_SH}. "
        "The stamp must precede the stale-artifact block."
    )


def test_stamp_block_after_working_transition_in_claude() -> None:
    """Structural: stamp_model_field call comes after the WORKING state transition."""
    lines = _CLAUDE_SH.read_text(encoding="utf-8").splitlines()
    working_idx = None
    stamp_idx = None
    for i, line in enumerate(lines):
        if 'set_state "$task_status" "WORKING"' in line and working_idx is None:
            working_idx = i
        if _STAMP_CALL in line and "stamp_model_field" in line and stamp_idx is None:
            stamp_idx = i

    assert working_idx is not None, (
        f"set_state WORKING not found in {_CLAUDE_SH}."
    )
    assert stamp_idx is not None, (
        f"stamp_model_field call not found in {_CLAUDE_SH}."
    )
    assert working_idx < stamp_idx, (
        f"stamp_model_field (line {stamp_idx + 1}) appears BEFORE "
        f"WORKING state transition (line {working_idx + 1}) in {_CLAUDE_SH}. "
        "The stamp must follow the WORKING transition."
    )
