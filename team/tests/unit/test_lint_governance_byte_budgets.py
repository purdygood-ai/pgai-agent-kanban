"""
test_lint_governance_byte_budgets.py
=====================================
Behavioral unit tests for team/scripts/lint_governance_byte_budgets.sh.

The script is invoked as a subprocess (never sourced -- it parses argv and
calls ``exit 0`` at top level, which would kill the test process if
sourced). Fixture kanban.cfg files are written under tmp_path so every
budget value in this file is deliberately controlled rather than depending
on the real tree's role-file sizes, which change over time. Extreme budget
values (very small / very large) are used precisely so a future edit to a
role file or the shared governance stack can never flip a test's verdict.

Behavioral requirements under test:
  - A role budgeted far above its assembled total reports "under".
  - A role budgeted far below its assembled total reports "OVER" -- and the
    script still exits 0 (NARRATE, DO NOT BLOCK; see the script's own
    "WHY IT NEVER BLOCKS" header).
  - A role with no budget_bytes_<role> key in [governance] reports
    "unbudgeted" and the script still exits 0.
  - A [governance] budget value that is not a bare non-negative integer
    reports "invalid-budget" rather than crashing the comparison, and the
    script still exits 0.
  - A --kanban-cfg path that does not exist on disk is not an error: every
    role reports "unbudgeted" and the script still exits 0.
  - The script reads byte totals through team/scripts/lib/read_order.sh
    (asserted indirectly: the "coder" row's assembled total matches an
    independent read_order_role_byte_total call against the real tree).
  - --help exits 0 and prints usage; an unknown flag exits 2.
  - An over-budget role prints one unconditional narration line (no
    --verbose needed) naming the role, its assembled bytes, and its
    budget, and (with --findings-file) writes exactly one quality-class
    finding naming that role. This is a soft size heuristic, not evidence
    of damage (team/SOP-core.md "Blocking Severity: only real damage
    blocks"), so it is surfaced, not failed.
  - An under-budget role produces neither the narration line nor a
    findings-file entry.
"""

from __future__ import annotations

import json
import pathlib

from tests.unit.shell_harness import BashResult, run_bash

_TEAM_ROOT = pathlib.Path(__file__).resolve().parents[2]
_SCRIPT = _TEAM_ROOT / "scripts" / "lint_governance_byte_budgets.sh"

# Extreme budgets: no plausible future edit to a role file or the shared
# governance stack will ever cross these thresholds, so the "under" / "OVER"
# assertions below stay stable without depending on the real tree's sizes.
_HUGE_BUDGET = 999_999_999
_TINY_BUDGET = 1


def _write_cfg(tmp_path: pathlib.Path, body: str) -> pathlib.Path:
    """Write a fixture kanban.cfg-shaped file under tmp_path and return its path."""
    cfg = tmp_path / "kanban.cfg"
    cfg.write_text(body, encoding="utf-8")
    return cfg


def _run(tmp_path: pathlib.Path, *extra_args: str) -> BashResult:
    args = " ".join(extra_args)
    return run_bash(tmp_path, f"bash {_SCRIPT} {args}".strip())


def _row_for(stdout: str, role: str) -> str:
    """Return the single output line for *role*, or raise if absent."""
    for line in stdout.splitlines():
        if line.split()[:1] == [role]:
            return line
    raise AssertionError(f"no output row for role {role!r} in:\n{stdout}")


# ---------------------------------------------------------------------------
# Comparison: under budget
# ---------------------------------------------------------------------------


def test_role_far_under_huge_budget_reports_under_and_exits_zero(
    tmp_path: pathlib.Path,
) -> None:
    """A role budgeted far above its assembled total reports 'under'."""
    cfg = _write_cfg(
        tmp_path,
        f"[governance]\nbudget_bytes_coder = {_HUGE_BUDGET}\n",
    )
    result = _run(tmp_path, "--kanban-cfg", str(cfg))
    assert result.returncode == 0
    row = _row_for(result.stdout, "coder")
    assert "under" in row
    assert "OVER" not in row


# ---------------------------------------------------------------------------
# Comparison: over budget (still exits 0 -- narrate, do not block)
# ---------------------------------------------------------------------------


def test_role_far_over_tiny_budget_reports_over_and_still_exits_zero(
    tmp_path: pathlib.Path,
) -> None:
    """A role budgeted far below its assembled total reports 'OVER' and the
    script still exits 0 -- this lint never blocks."""
    cfg = _write_cfg(
        tmp_path,
        f"[governance]\nbudget_bytes_coder = {_TINY_BUDGET}\n",
    )
    result = _run(tmp_path, "--kanban-cfg", str(cfg))
    assert result.returncode == 0
    row = _row_for(result.stdout, "coder")
    assert "OVER" in row


def test_all_seven_roles_over_tiny_budgets_still_exits_zero(
    tmp_path: pathlib.Path,
) -> None:
    """Every role over budget simultaneously is still a clean (exit 0) run."""
    lines = [
        f"budget_bytes_{role} = {_TINY_BUDGET}"
        for role in ("po", "pm", "coder", "writer", "tester", "cm", "overwatch")
    ]
    cfg = _write_cfg(tmp_path, "[governance]\n" + "\n".join(lines) + "\n")
    result = _run(tmp_path, "--kanban-cfg", str(cfg))
    assert result.returncode == 0
    for role in ("po", "pm", "coder", "writer", "tester", "cm", "overwatch"):
        assert "OVER" in _row_for(result.stdout, role)


# ---------------------------------------------------------------------------
# Absent-budget case
# ---------------------------------------------------------------------------


def test_role_with_no_configured_key_reports_unbudgeted(
    tmp_path: pathlib.Path,
) -> None:
    """A role whose budget_bytes_<role> key is absent reports 'unbudgeted'."""
    cfg = _write_cfg(
        tmp_path,
        f"[governance]\nbudget_bytes_tester = {_HUGE_BUDGET}\n",
    )
    result = _run(tmp_path, "--kanban-cfg", str(cfg))
    assert result.returncode == 0
    coder_row = _row_for(result.stdout, "coder")
    assert "unbudgeted" in coder_row
    tester_row = _row_for(result.stdout, "tester")
    assert "unbudgeted" not in tester_row


def test_empty_governance_section_reports_all_roles_unbudgeted(
    tmp_path: pathlib.Path,
) -> None:
    """A [governance] section present but carrying no keys unbudgets every role."""
    cfg = _write_cfg(tmp_path, "[governance]\n")
    result = _run(tmp_path, "--kanban-cfg", str(cfg))
    assert result.returncode == 0
    for role in ("po", "pm", "coder", "writer", "tester", "cm", "overwatch"):
        assert "unbudgeted" in _row_for(result.stdout, role)


def test_nonexistent_config_path_unbudgets_every_role_without_error(
    tmp_path: pathlib.Path,
) -> None:
    """A --kanban-cfg path that does not exist on disk is not an error."""
    missing = tmp_path / "does-not-exist.cfg"
    result = _run(tmp_path, "--kanban-cfg", str(missing))
    assert result.returncode == 0
    for role in ("po", "pm", "coder", "writer", "tester", "cm", "overwatch"):
        assert "unbudgeted" in _row_for(result.stdout, role)


# ---------------------------------------------------------------------------
# Malformed budget value
# ---------------------------------------------------------------------------


def test_non_numeric_budget_reports_invalid_budget_and_does_not_crash(
    tmp_path: pathlib.Path,
) -> None:
    """A [governance] value that is not a bare non-negative integer is reported
    as 'invalid-budget' rather than crashing the numeric comparison."""
    cfg = _write_cfg(
        tmp_path,
        "[governance]\nbudget_bytes_coder = sixty-thousand\n",
    )
    result = _run(tmp_path, "--kanban-cfg", str(cfg))
    assert result.returncode == 0
    row = _row_for(result.stdout, "coder")
    assert "invalid-budget" in row


# ---------------------------------------------------------------------------
# read_order.sh is the byte-total source (not a reimplementation)
# ---------------------------------------------------------------------------


def test_assembled_total_matches_read_order_role_byte_total(
    tmp_path: pathlib.Path,
) -> None:
    """The reported 'coder' total matches an independent read_order_role_byte_total
    call against the real tree -- confirming the script reads through
    read_order.sh rather than re-summing the manifest itself."""
    lib = _TEAM_ROOT / "scripts" / "lib" / "read_order.sh"
    independent = run_bash(
        tmp_path, f"source {lib} && read_order_role_byte_total coder"
    )
    assert independent.returncode == 0
    expected_total = independent.stdout.strip()

    cfg = _write_cfg(tmp_path, f"[governance]\nbudget_bytes_coder = {_HUGE_BUDGET}\n")
    result = _run(tmp_path, "--kanban-cfg", str(cfg))
    assert result.returncode == 0
    row = _row_for(result.stdout, "coder")
    assert row.split()[1] == expected_total


# ---------------------------------------------------------------------------
# CLI surface
# ---------------------------------------------------------------------------


def test_help_flag_exits_zero_and_prints_usage(tmp_path: pathlib.Path) -> None:
    """--help exits 0 and prints a usage block."""
    result = _run(tmp_path, "--help")
    assert result.returncode == 0
    assert "Usage:" in result.stdout


def test_unknown_argument_exits_two(tmp_path: pathlib.Path) -> None:
    """An unrecognized flag is an ordinary CLI usage error (exit 2), distinct
    from the always-0 narration exit the per-role verdicts use."""
    result = _run(tmp_path, "--not-a-real-flag")
    assert result.returncode == 2


# ---------------------------------------------------------------------------
# Over-budget narration and findings-file demotion (quality-class, not a
# gate): team/SOP-core.md "Blocking Severity: only real damage blocks".
# ---------------------------------------------------------------------------


def test_over_budget_role_prints_narration_line_without_verbose_flag(
    tmp_path: pathlib.Path,
) -> None:
    """An over-budget role prints one unconditional narration line -- no
    --verbose required -- naming the role, its assembled bytes, and its
    budget, and the script still exits 0."""
    cfg = _write_cfg(
        tmp_path,
        f"[governance]\nbudget_bytes_coder = {_TINY_BUDGET}\n",
    )
    result = _run(tmp_path, "--kanban-cfg", str(cfg))
    assert result.returncode == 0
    narration_lines = [
        line
        for line in result.stdout.splitlines()
        if line.startswith("lint_governance_byte_budgets:") and "coder" in line
    ]
    assert len(narration_lines) == 1, (
        f"expected exactly one narration line for 'coder', got:\n{result.stdout}"
    )
    assert "coder" in narration_lines[0]
    assert "assembled=" in narration_lines[0]
    assert f"budget={_TINY_BUDGET} bytes" in narration_lines[0]


def test_over_budget_role_produces_one_findings_file_entry_naming_role(
    tmp_path: pathlib.Path,
) -> None:
    """--findings-file writes exactly one quality-class finding for the
    over-budget role, naming its assembled bytes and its budget."""
    cfg = _write_cfg(
        tmp_path,
        f"[governance]\nbudget_bytes_coder = {_TINY_BUDGET}\n",
    )
    findings_path = tmp_path / "findings.json"
    result = _run(
        tmp_path, "--kanban-cfg", str(cfg), "--findings-file", str(findings_path)
    )
    assert result.returncode == 0
    findings = json.loads(findings_path.read_text(encoding="utf-8"))
    assert len(findings) == 1, f"expected one finding, got: {findings}"
    finding = findings[0]
    assert finding["lint"] == "governance_byte_budget"
    assert finding["severity"] == "quality"
    assert finding["role"] == "coder"
    assert finding["budget_bytes"] == _TINY_BUDGET
    assert finding["assembled_bytes"] > _TINY_BUDGET
    assert "coder" in finding["summary"]


def test_under_budget_role_produces_no_narration_and_empty_findings_file(
    tmp_path: pathlib.Path,
) -> None:
    """A role budgeted far above its assembled total produces neither a
    narration line nor a findings-file entry -- silence is the correct
    output for a clean role."""
    cfg = _write_cfg(
        tmp_path,
        f"[governance]\nbudget_bytes_coder = {_HUGE_BUDGET}\n",
    )
    findings_path = tmp_path / "findings.json"
    result = _run(
        tmp_path, "--kanban-cfg", str(cfg), "--findings-file", str(findings_path)
    )
    assert result.returncode == 0
    assert "lint_governance_byte_budgets:" not in result.stdout
    findings = json.loads(findings_path.read_text(encoding="utf-8"))
    assert findings == []


def test_findings_file_written_even_when_no_role_is_over_budget(
    tmp_path: pathlib.Path,
) -> None:
    """--findings-file always writes the file (an empty array, not a
    missing file) so a caller never mistakes 'no findings' for 'the lint
    never ran'."""
    cfg = _write_cfg(
        tmp_path,
        "[governance]\n"
        + "\n".join(
            f"budget_bytes_{role} = {_HUGE_BUDGET}"
            for role in ("po", "pm", "coder", "writer", "tester", "cm", "overwatch")
        )
        + "\n",
    )
    findings_path = tmp_path / "findings.json"
    result = _run(
        tmp_path, "--kanban-cfg", str(cfg), "--findings-file", str(findings_path)
    )
    assert result.returncode == 0
    assert findings_path.is_file()
    assert json.loads(findings_path.read_text(encoding="utf-8")) == []
