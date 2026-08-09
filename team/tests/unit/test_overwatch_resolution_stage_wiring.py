"""
test_overwatch_resolution_stage_wiring.py
============================================
Structural guards for the v2.0.0 Goal 2 wiring: the OVERWATCH resolution
stage must be driven by the existing on-BLOCK trigger via the production
entry point (Acceptance Criterion: "The stage is driven by the existing
on-BLOCK trigger via the production entry point").

The on-BLOCK trigger (team/tests/unit/test_on_block_overwatch_trigger.py)
already proves that a fresh BLOCKED task fires
`wake-now.sh --agent overwatch`, which dispatches to
`wake-batch.sh --agent=overwatch`, which reaches whichever wake provider
sibling is active (scripts/wake/claude.sh or scripts/wake/codex.sh). This
file proves the NEXT hop: that firing reaches
`run_overwatch_resolution_stage`, the shared wake_common.sh function that
invokes `pgai_agent_kanban.overwatch.resolution_stage`, from BOTH provider
siblings' `run_project_chain`, with a byte-identical call-site block (same
sibling-equality discipline the on-BLOCK trigger itself is held to).
"""

from __future__ import annotations

import pathlib
import textwrap

_TEAM_DIR = pathlib.Path(__file__).parent.parent.parent  # team/
_CLAUDE_SH = _TEAM_DIR / "scripts" / "wake" / "claude.sh"
_CODEX_SH = _TEAM_DIR / "scripts" / "wake" / "codex.sh"
_WAKE_COMMON_SH = _TEAM_DIR / "scripts" / "lib" / "wake_common.sh"

_MARKER = "v2.0.0 autonomous block-resolution stage"


def _extract_call_block(path: pathlib.Path) -> list:
    """Extract the marker comment line plus the following non-blank lines up
    to (and including) the run_overwatch_resolution_stage call."""
    lines = path.read_text(encoding="utf-8").splitlines()
    block: list = []
    capturing = False
    for line in lines:
        if _MARKER in line:
            capturing = True
        if capturing:
            block.append(line)
            if "run_overwatch_resolution_stage" in line:
                break
    return block


def test_claude_sh_calls_run_overwatch_resolution_stage_in_run_project_chain() -> None:
    content = _CLAUDE_SH.read_text(encoding="utf-8")
    assert "run_overwatch_resolution_stage" in content
    block = _extract_call_block(_CLAUDE_SH)
    assert block, f"call-site marker '{_MARKER}' not found in {_CLAUDE_SH}"


def test_codex_sh_calls_run_overwatch_resolution_stage_in_run_project_chain() -> None:
    content = _CODEX_SH.read_text(encoding="utf-8")
    assert "run_overwatch_resolution_stage" in content
    block = _extract_call_block(_CODEX_SH)
    assert block, f"call-site marker '{_MARKER}' not found in {_CODEX_SH}"


def test_call_site_blocks_are_byte_identical_between_siblings() -> None:
    """The sibling-equality gate: the call-site block claude.sh and codex.sh
    each carry must match exactly, mirroring the on-BLOCK trigger's own
    sibling discipline (test_on_block_overwatch_trigger.py)."""
    claude_block = _extract_call_block(_CLAUDE_SH)
    codex_block = _extract_call_block(_CODEX_SH)
    assert claude_block == codex_block, (
        "run_overwatch_resolution_stage call-site blocks differ between siblings.\n"
        "claude.sh block:\n" + "\n".join(claude_block) + "\n\n"
        "codex.sh block:\n" + "\n".join(codex_block)
    )


def test_call_site_precedes_reconcile_stale_active_in_both_siblings() -> None:
    """Structural: the resolution-stage call must run once per project,
    unconditionally (before task dispatch begins), so it fires even when
    the overwatch task queue is empty — matching the "no-op unless there is
    a ship-policy HALT to evaluate" contract the stage itself implements.
    Anchored on `reconcile_stale_active`, the first call after the dev-tree
    gate in both siblings' run_project_chain.
    """
    for path in (_CLAUDE_SH, _CODEX_SH):
        content = path.read_text(encoding="utf-8")
        call_idx = content.index("run_overwatch_resolution_stage \"$project_name\"")
        reconcile_idx = content.index("reconcile_stale_active", call_idx)
        assert call_idx < reconcile_idx, (
            f"run_overwatch_resolution_stage call does not precede "
            f"reconcile_stale_active in {path}"
        )


def test_run_overwatch_resolution_stage_defined_once_in_wake_common() -> None:
    """The function body lives in exactly one place (wake_common.sh), shared
    by both siblings — not duplicated per-sibling. This is what keeps the
    two call sites trivially byte-identical: they invoke the same shared
    function rather than each carrying their own copy of the logic."""
    content = _WAKE_COMMON_SH.read_text(encoding="utf-8")
    assert content.count("run_overwatch_resolution_stage() {") == 1

    for path in (_CLAUDE_SH, _CODEX_SH):
        sibling_content = path.read_text(encoding="utf-8")
        assert "run_overwatch_resolution_stage() {" not in sibling_content, (
            f"{path} must not redefine run_overwatch_resolution_stage — it "
            f"should only call the shared wake_common.sh function."
        )


def test_wake_common_function_is_a_no_op_for_non_overwatch_agents() -> None:
    """Structural: the guard clause `[[ "$AGENT" == "overwatch" ]] || return 0`
    must be the first statement in the function body, so every other agent
    (pm, coder, writer, tester, cm) is unaffected."""
    content = _WAKE_COMMON_SH.read_text(encoding="utf-8")
    start = content.index("run_overwatch_resolution_stage() {")
    end = content.index("\n}\n", start)
    body = content[start:end]
    assert '[[ "$AGENT" == "overwatch" ]] || return 0' in body


def test_pp_run_ops_invokes_the_resolution_stage_module() -> None:
    content = _WAKE_COMMON_SH.read_text(encoding="utf-8")
    start = content.index("run_overwatch_resolution_stage() {")
    end = content.index("\n}\n", start)
    body = content[start:end]
    assert "pp_run_ops pgai_agent_kanban.overwatch.resolution_stage" in body
    assert "--project" in body
    assert "--kanban-root" in body
