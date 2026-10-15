"""
test_halt_after_rc_gate.py
===========================
Direct unit coverage for pgai_agent_kanban.halt_after.rc_gate -- the
RC-close gate that decides whether a satisfied drain condition
(halt_after.drain.check_drain) may act (promote) right now, or must be
latched and deferred until the project's Active RC reads "none".

End-to-end behavioral coverage of the same invariant, driven through the
`python3 -m halt_after` CLI and through a real cm-release.sh ship, lives in
team/tests/integration/test_halt_after_rc_close_gate.py. This file covers
the module's own function-level contract: what rc_is_open reports, what
evaluate() returns for each combination of (drained, RC open, latch
present), and the exact latch-file behavior that makes "deferring a
satisfied drain must not lose it" hold.
"""

from __future__ import annotations

import pathlib
from pathlib import Path

import pytest

from pgai_agent_kanban.halt_after.rc_gate import RcGateOutcome, evaluate, rc_is_open


def _write_release_state(root: Path, active_rc: str = "none", last_released: str = "none") -> None:
    lines = ["# Release State\n\n", "## Active RC\n", f"{active_rc}\n"]
    if last_released and last_released != "none":
        lines.append(f"\n## Last Released\n{last_released}\n")
    (root / "release-state.md").write_text("".join(lines), encoding="utf-8")


def _project_root(tmp_path: Path, active_rc: str = "none") -> Path:
    root = tmp_path / "project"
    root.mkdir()
    (root / "tasks").mkdir()
    _write_release_state(root, active_rc=active_rc)
    return root


# ---------------------------------------------------------------------------
# rc_is_open
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "active_rc, expected",
    [
        ("none", False),
        ("None", False),  # case-insensitive
        ("  none  ", False),  # whitespace-tolerant
        ("v1.0.0", True),
        ("rc/v1.0.0", True),
    ],
)
def test_rc_is_open(tmp_path: Path, active_rc: str, expected: bool) -> None:
    root = _project_root(tmp_path, active_rc=active_rc)
    assert rc_is_open(root) is expected


def test_rc_is_open_treats_missing_release_state_as_closed(tmp_path: Path) -> None:
    """No release-state.md at all reads as RC-closed (conservative default)."""
    root = tmp_path / "project"
    root.mkdir()
    assert rc_is_open(root) is False


# ---------------------------------------------------------------------------
# evaluate() -- no latch present yet
# ---------------------------------------------------------------------------


def test_evaluate_not_drained_when_condition_unsatisfied(tmp_path: Path) -> None:
    """NOT_DRAINED when check_drain itself is False -- RC state is irrelevant."""
    root = _project_root(tmp_path, active_rc="v1.0.0")
    (root / "tasks" / "CODER-001").mkdir()
    (root / "tasks" / "CODER-001" / "status.md").write_text(
        "## State\nWORKING\n\n## Role\nCODER\n", encoding="utf-8"
    )
    result = evaluate(root, root, "coder")
    assert result.outcome is RcGateOutcome.NOT_DRAINED
    assert result.event == "coder"
    assert not (root / "HALT").exists()
    assert not (root / "HALT-AFTER.rc-gate").exists()


def test_evaluate_promotes_immediately_when_drained_and_rc_closed(tmp_path: Path) -> None:
    """PROMOTED, no latch needed, when the drain condition is satisfied and RC is already closed."""
    root = _project_root(tmp_path, active_rc="none")
    (root / "HALT-AFTER").write_text("coder\n", encoding="utf-8")
    result = evaluate(root, root, "coder")
    assert result.outcome is RcGateOutcome.PROMOTED
    assert result.event == "coder"
    assert (root / "HALT").exists()
    assert not (root / "HALT-AFTER").exists()
    assert not (root / "HALT-AFTER.rc-gate").exists()


def test_evaluate_defers_and_latches_when_drained_but_rc_open(tmp_path: Path) -> None:
    """DEFERRED + a latch file when the drain condition is satisfied but Active RC is open."""
    root = _project_root(tmp_path, active_rc="v2.0.0")
    (root / "HALT-AFTER").write_text("writer\n", encoding="utf-8")
    result = evaluate(root, root, "writer")
    assert result.outcome is RcGateOutcome.DEFERRED
    assert result.event == "writer"
    assert not (root / "HALT").exists()
    latch = root / "HALT-AFTER.rc-gate"
    assert latch.exists()
    assert latch.read_text(encoding="utf-8").strip() == "writer"


def test_evaluate_propagates_value_error_for_unsupported_token_when_no_latch(tmp_path: Path) -> None:
    root = _project_root(tmp_path, active_rc="none")
    with pytest.raises(ValueError, match="unsupported event token"):
        evaluate(root, root, "deploy")


# ---------------------------------------------------------------------------
# evaluate() -- a latch already exists from an earlier evaluation
# ---------------------------------------------------------------------------


def test_evaluate_stays_deferred_while_latched_and_rc_still_open(tmp_path: Path) -> None:
    """A latch present + RC still open -> DEFERRED again, without re-deriving check_drain."""
    root = _project_root(tmp_path, active_rc="v3.0.0")
    (root / "HALT-AFTER").write_text("tester\n", encoding="utf-8")
    (root / "HALT-AFTER.rc-gate").write_text("tester\n", encoding="utf-8")
    result = evaluate(root, root, "tester")
    assert result.outcome is RcGateOutcome.DEFERRED
    assert result.event == "tester"
    assert not (root / "HALT").exists()
    assert (root / "HALT-AFTER.rc-gate").exists(), "latch must survive an RC-still-open re-check"


def test_evaluate_promotes_the_latched_event_once_rc_closes_ignoring_current_token_arg(
    tmp_path: Path,
) -> None:
    """Once latched, the LATCHED token wins on promotion -- not whatever *event* is passed.

    This is the concrete proof that 'deferring a satisfied drain must not
    lose it, even if the condition would no longer evaluate true at that
    moment' holds: the caller passes a token that would raise if
    re-derived (an unsupported token), and evaluate() still promotes using
    the latched value without ever calling check_drain again.
    """
    root = _project_root(tmp_path, active_rc="v3.0.0")
    (root / "HALT-AFTER").write_text("cm\n", encoding="utf-8")
    (root / "HALT-AFTER.rc-gate").write_text("cm\n", encoding="utf-8")

    # RC closes.
    _write_release_state(root, active_rc="none")

    # Pass a bogus/unsupported token — proves the latch is authoritative,
    # not a re-derivation of the current HALT-AFTER file's own token.
    result = evaluate(root, root, "not-a-real-token")
    assert result.outcome is RcGateOutcome.PROMOTED
    assert result.event == "cm", "the LATCHED token must be the one that promotes"
    assert (root / "HALT").exists()
    assert not (root / "HALT-AFTER.rc-gate").exists()

    release_state_text = (root / "release-state.md").read_text(encoding="utf-8")
    assert "Trigger: HALT-AFTER cm auto-promotion" in release_state_text


def test_evaluate_latch_survives_a_later_unsatisfied_condition(tmp_path: Path) -> None:
    """A latch written while 'coder' was drained is not undone by a later WORKING coder task."""
    root = _project_root(tmp_path, active_rc="v1.5.0")
    (root / "HALT-AFTER").write_text("coder\n", encoding="utf-8")

    # First evaluation: no CODER tasks at all — drained, RC open — defers + latches.
    result_1 = evaluate(root, root, "coder")
    assert result_1.outcome is RcGateOutcome.DEFERRED

    # A new CODER task starts WORKING before the RC closes. If evaluate()
    # re-derived the condition on this call, it would find check_drain
    # False and (incorrectly) report NOT_DRAINED, silently dropping the
    # promotion that was already earned.
    (root / "tasks" / "CODER-999").mkdir()
    (root / "tasks" / "CODER-999" / "status.md").write_text(
        "## State\nWORKING\n\n## Role\nCODER\n", encoding="utf-8"
    )
    result_2 = evaluate(root, root, "coder")
    assert result_2.outcome is RcGateOutcome.DEFERRED, "the latch, not a fresh check_drain, must decide"

    # RC closes while the new task is still WORKING.
    _write_release_state(root, active_rc="none")
    result_3 = evaluate(root, root, "coder")
    assert result_3.outcome is RcGateOutcome.PROMOTED
    assert (root / "HALT").exists()


# ---------------------------------------------------------------------------
# A latch is honoured only while the sentinel still names its token
# ---------------------------------------------------------------------------


def _working_task(root: Path, role: str) -> None:
    task = root / "tasks" / f"{role}-777"
    task.mkdir(parents=True)
    (task / "status.md").write_text(f"## State\nWORKING\n\n## Role\n{role}\n", encoding="utf-8")


def test_latch_from_an_earlier_arm_does_not_promote_after_a_hand_re_arm(tmp_path: Path) -> None:
    """Latched 'coder', then HALT-AFTER re-armed with another token by hand: the
    RC closing does not promote on 'coder', and the stale latch is removed."""
    root = _project_root(tmp_path, active_rc="v1.5.0")
    (root / "HALT-AFTER").write_text("coder\n", encoding="utf-8")
    assert evaluate(root, root, "coder").outcome is RcGateOutcome.DEFERRED
    assert (root / "HALT-AFTER.rc-gate").exists()

    (root / "HALT-AFTER").write_text("writer\n", encoding="utf-8")
    _working_task(root, "WRITER")  # the new token's drain is not satisfied
    _write_release_state(root, active_rc="none")

    result = evaluate(root, root, "writer")

    assert result.outcome is RcGateOutcome.NOT_DRAINED
    assert result.event == "writer"
    assert not (root / "HALT").exists(), "the stale 'coder' latch must not promote"
    assert not (root / "HALT-AFTER.rc-gate").exists()
    assert (root / "HALT-AFTER").read_text(encoding="utf-8") == "writer\n"


def test_re_arm_through_the_operator_verb_clears_the_latch(tmp_path: Path) -> None:
    from pgai_agent_kanban.ops.context import OpsContext
    from pgai_agent_kanban.ops.write import halt_after

    kanban_root = tmp_path / "kanban"
    root = kanban_root / "projects" / "demo"
    (root / "tasks").mkdir(parents=True)
    _write_release_state(root, active_rc="v1.5.0")
    ctx = OpsContext(kanban_root=kanban_root)

    halt_after(ctx, "demo", "coder")
    assert evaluate(root, root, "coder").outcome is RcGateOutcome.DEFERRED
    assert (root / "HALT-AFTER.rc-gate").exists()

    halt_after(ctx, "demo", "coder")  # same token: the latch still belongs to this arm
    assert (root / "HALT-AFTER.rc-gate").exists()

    halt_after(ctx, "demo", "rc:v9.9.9")
    assert not (root / "HALT-AFTER.rc-gate").exists()
    assert (root / "HALT-AFTER").read_text(encoding="utf-8") == "rc:v9.9.9\n"


def _run_cli(root: Path):
    import subprocess
    import sys

    return subprocess.run(
        [sys.executable, "-m", "pgai_agent_kanban.halt_after", str(root)],
        capture_output=True,
        text=True,
        cwd=Path(__file__).resolve().parents[2],
        timeout=60,
    )


def test_cli_removes_a_latch_whose_sentinel_was_removed(tmp_path: Path) -> None:
    root = _project_root(tmp_path, active_rc="none")
    (root / "HALT-AFTER.rc-gate").write_text("coder\n", encoding="utf-8")

    result = _run_cli(root)

    assert result.returncode == 3, result.stderr
    assert not (root / "HALT-AFTER.rc-gate").exists(), result.stderr
    assert not (root / "HALT").exists()

    # A later arm does not inherit the removed latch: 'rc' with an open RC defers on its own terms.
    _write_release_state(root, active_rc="v2.0.0")
    (root / "HALT-AFTER").write_text("tester\n", encoding="utf-8")
    _working_task(root, "TESTER")
    assert _run_cli(root).returncode == 1
    assert not (root / "HALT").exists()


def test_cli_removes_a_latch_beside_an_invalid_sentinel(tmp_path: Path) -> None:
    root = _project_root(tmp_path, active_rc="none")
    (root / "HALT-AFTER").write_text("deploy\n", encoding="utf-8")
    (root / "HALT-AFTER.rc-gate").write_text("coder\n", encoding="utf-8")

    result = _run_cli(root)

    assert result.returncode == 2, result.stderr
    assert not (root / "HALT-AFTER.rc-gate").exists()
    assert not (root / "HALT").exists()


def test_latch_with_unchanged_sentinel_promotes_when_the_rc_closes(tmp_path: Path) -> None:
    root = _project_root(tmp_path, active_rc="v1.5.0")
    (root / "HALT-AFTER").write_text("coder\n", encoding="utf-8")
    assert _run_cli(root).returncode == 5
    assert (root / "HALT-AFTER.rc-gate").exists()

    _working_task(root, "CODER")  # the drain flips back; the latch still decides
    _write_release_state(root, active_rc="none")
    result = _run_cli(root)

    assert result.returncode == 0, result.stderr
    assert (root / "HALT").exists()
    assert not (root / "HALT-AFTER.rc-gate").exists()
