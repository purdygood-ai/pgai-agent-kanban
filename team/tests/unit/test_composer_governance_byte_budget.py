"""
test_composer_governance_byte_budget.py
=========================================
Composer fixture (requirements/v2.10.0-lean-governance-read-orders.md,
"Acceptance": "Composer fixture prints assembled byte counts per role; all
under budget on a clean tree.").

Assembles each of the seven catalog roles' governance reading list through
the composer path -- team/scripts/lint_governance_byte_budgets.sh, which
itself resolves every role's manifest via read_order_role_byte_total
(scripts/lib/read_order.sh) and sums the on-disk byte size of the files it
names -- and compares the total against the role's budget_bytes_<role> key
in the real, checked-in kanban.cfg_example [governance] section. This test
never hardcodes a budget number of its own: it reads the same config file
an operator edits, so raising a budget in kanban.cfg (or kanban.cfg_example)
changes what this fixture reports against with no test edit required.

Distinction from team/tests/unit/test_lint_governance_byte_budgets.py: that
file unit-tests the LINT SCRIPT's own behavior (parsing, comparison,
narration, --findings-file output, CLI surface) against synthetic fixture
kanban.cfg files with deliberately extreme budgets so the lint's own
verdicts stay stable regardless of how the real tree's role files grow or
shrink. This file is the opposite: it exercises the composer path against
the real, shipped tree's real, configured budgets.

This suite is quality-class, not a gate: a byte-budget reading is a soft
size heuristic, not evidence of damage, so nothing in this file may fail a
role's verdict. See team/SOP-core.md "Blocking Severity: only real damage
blocks" -- a check earns the right to fail a run only by evidencing real
damage (broken behavior, damaged data or chain integrity, a shipped
artefact that does not work, or a verified functional regression); an
over-threshold byte count meets none of that, so this file narrates and
never asserts on it. The gated runners (run-unit-tests.sh,
run-integration-tests.sh) are what turn an over-budget role into a visible,
non-blocking quality-class finding -- via the lint script's own
--findings-file mode, merged through
team/scripts/lib/gated_runner_lint.sh's gated_runner_merge_findings_file --
not this pytest file.

Two tests:
  - test_composer_prints_per_role_byte_budget_table_for_every_role:
    the clean-tree run. Prints the full per-role table unconditionally
    (via capsys.disabled(), which writes to the real stdout regardless of
    pytest's capture mode) and asserts only that every catalog role
    produced a row -- a structural check, not a byte-threshold one. Any
    role reading "OVER" is reported in the same print, never failed.
  - test_role_over_a_lowered_budget_reports_over_without_failing:
    proves an over-budget role reports "OVER" through the composer path
    without raising. Using an isolated fixture kanban.cfg (never the real
    kanban.cfg_example) that pins one role's budget far below its real
    assembled size, this drives the same composer path the clean-tree
    test uses and confirms the demoted check stays green.
"""

from __future__ import annotations

import pathlib

import pytest

from tests.unit.shell_harness import run_bash

_TEAM_ROOT = pathlib.Path(__file__).resolve().parents[2]
_LINT_SCRIPT = _TEAM_ROOT / "scripts" / "lint_governance_byte_budgets.sh"
_REAL_KANBAN_CFG_EXAMPLE = _TEAM_ROOT.parent / "kanban.cfg_example"

# The seven-role catalog: the six RC pipeline agents plus OVERWATCH, matching
# team/scripts/lint_governance_byte_budgets.sh's own _GBB_ROLES list.
_ROLES = ("po", "pm", "coder", "writer", "tester", "cm", "overwatch")


def _parse_rows(stdout: str) -> dict[str, dict[str, str]]:
    """Parse lint_governance_byte_budgets.sh's table output into
    {role: {"assembled": ..., "budget": ..., "verdict": ...}}. Skips the
    header row and any unconditional over-budget narration line (which
    does not start with a role name). Whitespace-tolerant (splits on any
    run of whitespace), matching the script's own fixed-width printf
    formatting."""
    rows: dict[str, dict[str, str]] = {}
    lines = [ln for ln in stdout.strip().splitlines() if ln.strip()]
    for line in lines[1:]:  # lines[0] is the "ROLE ASSEMBLED BUDGET VERDICT" header
        if line.startswith("lint_governance_byte_budgets:"):
            continue
        parts = line.split()
        if len(parts) < 4:
            continue
        role, assembled, budget, verdict = parts[0], parts[1], parts[2], parts[3]
        rows[role] = {"assembled": assembled, "budget": budget, "verdict": verdict}
    return rows


def _print_offenders_if_any(rows: dict[str, dict[str, str]]) -> None:
    """Prints every role whose verdict reads other than 'under' -- purely
    informational. Never raises: a soft byte-budget reading is advisory
    config, not evidence of damage (team/SOP-core.md "Blocking Severity:
    only real damage blocks"), so this suite reports it and moves on."""
    offenders = {role: v for role, v in rows.items() if v["verdict"] != "under"}
    if offenders:
        print(
            "role(s) not under their configured governance budget "
            "(advisory, non-blocking): "
            + ", ".join(
                f"{role} (assembled={v['assembled']} budget={v['budget']} verdict={v['verdict']})"
                for role, v in sorted(offenders.items())
            )
        )


# ---------------------------------------------------------------------------
# Clean-tree run: real manifests, real kanban.cfg_example budgets.
# ---------------------------------------------------------------------------


def test_composer_prints_per_role_byte_budget_table_for_every_role(
    tmp_path: pathlib.Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """Assembles all seven roles' governance through the composer path
    (read_order_role_byte_total, via the shipped lint script) against the
    real, checked-in kanban.cfg_example budgets and prints the per-role
    table unconditionally. Asserts every role produced a row -- structural
    completeness, not a byte-threshold assertion -- so an over-budget role
    is visible in the printed output without failing this test."""
    assert _REAL_KANBAN_CFG_EXAMPLE.is_file(), (
        f"kanban.cfg_example not found at {_REAL_KANBAN_CFG_EXAMPLE} -- "
        "test fixture path is stale."
    )
    result = run_bash(
        tmp_path,
        f"bash {_LINT_SCRIPT} --kanban-cfg {_REAL_KANBAN_CFG_EXAMPLE}",
    )
    assert result.returncode == 0, result.stderr

    # The brief asks for the numbers to be visible, not merely a green
    # tick -- capsys.disabled() writes straight to the real stdout so the
    # table prints whether or not any role reads OVER, regardless of
    # whether the runner was invoked with pytest's default output capture.
    with capsys.disabled():
        print("\nComposer governance byte-budget table (kanban.cfg_example):")
        print(result.stdout)

    rows = _parse_rows(result.stdout)
    assert set(rows) == set(_ROLES), (
        f"Expected rows for {sorted(_ROLES)}, got {sorted(rows)}. "
        f"Raw output:\n{result.stdout}"
    )

    with capsys.disabled():
        _print_offenders_if_any(rows)


# ---------------------------------------------------------------------------
# Liveness proof: a lowered budget still reports OVER, and still never fails.
# ---------------------------------------------------------------------------


def test_role_over_a_lowered_budget_reports_over_without_failing(
    tmp_path: pathlib.Path,
) -> None:
    """Proves the composer path reports 'OVER' for a role pinned below its
    real assembled size, and that this demoted check never raises on it.
    Uses an isolated fixture kanban.cfg (never the real
    kanban.cfg_example) that pins the 'coder' budget to a single byte --
    far below any real assembled size. This is the same comparison the
    clean-tree test drives; the only difference is the configured budget,
    proving the OVER verdict actually tracks the configured value rather
    than always reading 'under' -- while confirming that an OVER verdict
    is reported, not failed."""
    tiny_budget_cfg = tmp_path / "kanban.cfg"
    tiny_budget_cfg.write_text(
        "[governance]\nbudget_bytes_coder = 1\n", encoding="utf-8"
    )
    result = run_bash(
        tmp_path, f"bash {_LINT_SCRIPT} --kanban-cfg {tiny_budget_cfg}"
    )
    # The lint script itself narrates only and always exits 0 -- see its own
    # "WHY IT NEVER BLOCKS" header.
    assert result.returncode == 0, result.stderr

    rows = _parse_rows(result.stdout)
    assert rows["coder"]["verdict"] == "OVER", (
        f"Expected 'coder' to report OVER against a 1-byte budget. Got:\n{result.stdout}"
    )

    # No assertion below is entitled to fail on this OVER verdict -- see
    # this module's docstring and team/SOP-core.md "Blocking Severity:
    # only real damage blocks". Reporting it (as above) is the whole job.
