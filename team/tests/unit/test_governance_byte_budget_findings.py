"""
test_governance_byte_budget_findings.py
=======================================
The findings JSON lint_governance_byte_budgets.sh writes with
--findings-file: ``pgai_agent_kanban.lint.governance_byte_budget``.

Covered behaviour:

  * zero lines (or only blank lines) render ``[]``;
  * one ``<role>\\t<assembled>\\t<budget>`` line renders one quality-class
    finding with integer byte counts and the summary sentence;
  * several lines render one finding each, in input order, as an array
    indented by two spaces with keys sorted;
  * a malformed line raises ValueError, and on the command line gives one
    stderr line, empty stdout and exit 1;
  * the command line prints the rendered array with a trailing newline;
  * lint_governance_byte_budgets.sh, with several roles over budget, writes
    exactly the module's rendering of those roles to its findings file.

The command line and the script run as subprocesses with an environment
built from an explicit set and a throwaway HOME under tmp_path.
"""

from __future__ import annotations

import json
import pathlib
import subprocess
import sys

import pytest

from pgai_agent_kanban.lint import governance_byte_budget
from tests.fixtures.scrubbed_subprocess_env import build_scrubbed_env
from tests.unit.shell_harness import run_bash

_TEAM_DIR = pathlib.Path(__file__).resolve().parents[2]
_MODULE = "pgai_agent_kanban.lint.governance_byte_budget"
_SCRIPT = _TEAM_DIR / "scripts" / "lint_governance_byte_budgets.sh"


def _expected(role: str, assembled: int, budget: int) -> dict:
    return {
        "lint": "governance_byte_budget",
        "severity": "quality",
        "role": role,
        "assembled_bytes": assembled,
        "budget_bytes": budget,
        "summary": f"{role} over governance byte budget: assembled={assembled} bytes budget={budget} bytes",
    }


def _run_cli(tmp_path: pathlib.Path, stdin: str) -> subprocess.CompletedProcess:
    env = build_scrubbed_env(home_dir=tmp_path / "home", extra_env={"PYTHONPATH": str(_TEAM_DIR)})
    return subprocess.run(
        [sys.executable, "-m", _MODULE],
        input=stdin, capture_output=True, text=True, env=env, cwd=str(tmp_path),
        timeout=60, check=False,
    )


@pytest.mark.parametrize("lines", [[], ["\n", "\n"]], ids=["no-lines", "blank-lines"])
def test_zero_over_budget_lines_render_an_empty_array(lines: list) -> None:
    assert governance_byte_budget.findings_from_lines(lines) == []
    assert governance_byte_budget.render_findings(lines) == "[]"


def test_one_over_budget_line_renders_one_finding() -> None:
    lines = ["coder\t120\t100\n"]

    assert governance_byte_budget.findings_from_lines(lines) == [_expected("coder", 120, 100)]
    assert governance_byte_budget.render_findings(lines) == (
        "[\n"
        "  {\n"
        '    "assembled_bytes": 120,\n'
        '    "budget_bytes": 100,\n'
        '    "lint": "governance_byte_budget",\n'
        '    "role": "coder",\n'
        '    "severity": "quality",\n'
        '    "summary": "coder over governance byte budget: assembled=120 bytes budget=100 bytes"\n'
        "  }\n"
        "]"
    )


def test_several_over_budget_lines_render_one_finding_each_in_input_order() -> None:
    lines = ["pm\t5000\t4000\n", "\n", "coder\t120\t100\n", "tester\t9\t1"]

    findings = governance_byte_budget.findings_from_lines(lines)

    assert findings == [
        _expected("pm", 5000, 4000),
        _expected("coder", 120, 100),
        _expected("tester", 9, 1),
    ]
    assert governance_byte_budget.render_findings(lines) == json.dumps(findings, indent=2, sort_keys=True)


@pytest.mark.parametrize(
    "line", ["coder\t120\n", "coder\t120\t100\textra\n", "coder\tmany\t100\n"],
    ids=["two-fields", "four-fields", "non-integer"],
)
def test_malformed_line_raises_value_error(line: str) -> None:
    with pytest.raises(ValueError):
        governance_byte_budget.findings_from_lines([line])


def test_command_line_prints_the_rendered_array(tmp_path: pathlib.Path) -> None:
    stdin = "coder\t120\t100\npm\t5000\t4000\n"

    result = _run_cli(tmp_path, stdin)

    assert result.returncode == 0, result.stderr
    assert result.stdout == governance_byte_budget.render_findings(stdin.splitlines(keepends=True)) + "\n"


def test_command_line_prints_an_empty_array_for_no_input(tmp_path: pathlib.Path) -> None:
    result = _run_cli(tmp_path, "")

    assert result.returncode == 0, result.stderr
    assert result.stdout == "[]\n"


def test_command_line_reports_a_malformed_line_on_stderr(tmp_path: pathlib.Path) -> None:
    result = _run_cli(tmp_path, "coder\t120\n")

    assert result.returncode == 1
    assert result.stdout == ""
    assert "Traceback" not in result.stderr
    assert result.stderr.startswith("governance_byte_budget: expected ")


def test_lint_script_writes_the_module_rendering_for_several_over_budget_roles(
    tmp_path: pathlib.Path,
) -> None:
    cfg = tmp_path / "kanban.cfg"
    cfg.write_text(
        "[governance]\nbudget_bytes_pm = 1\nbudget_bytes_coder = 2\nbudget_bytes_cm = 3\n",
        encoding="utf-8",
    )
    findings_path = tmp_path / "findings.json"

    result = run_bash(
        tmp_path, f"bash {_SCRIPT} --kanban-cfg {cfg} --findings-file {findings_path}"
    )

    assert result.returncode == 0, result.stderr
    findings = json.loads(findings_path.read_text(encoding="utf-8"))
    assert [f["role"] for f in findings] == ["pm", "coder", "cm"]
    assert [f["budget_bytes"] for f in findings] == [1, 2, 3]
    lines = [f"{f['role']}\t{f['assembled_bytes']}\t{f['budget_bytes']}\n" for f in findings]
    assert findings_path.read_text(encoding="utf-8") == (
        governance_byte_budget.render_findings(lines) + "\n"
    )
