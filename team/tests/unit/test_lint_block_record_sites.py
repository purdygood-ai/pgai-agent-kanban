"""
test_lint_block_record_sites.py
===============================
team/scripts/lint_block_record_sites.py reports every BLOCKED state write
that does not record a ``## Block Record`` through the shared helper, and
always exits 0 (quality class).

Fixtures are synthetic shell and Python files under pytest's tmp_path; the
real tree is scanned read-only for the zero-findings check.

Naming: function names describe behaviour under test, never bug IDs or task
IDs (roles/TESTER.md "Test Authoring Guidelines").
"""

from __future__ import annotations

import importlib.util
import json
import pathlib
import subprocess
import sys

import pytest

# This file lives at team/tests/unit/; team/ is three parents up.
_TEAM_DIR = pathlib.Path(__file__).resolve().parent.parent.parent
_REPO_ROOT = _TEAM_DIR.parent
_LINT_SCRIPT = _TEAM_DIR / "scripts" / "lint_block_record_sites.py"

_spec = importlib.util.spec_from_file_location("lint_block_record_sites", _LINT_SCRIPT)
assert _spec is not None and _spec.loader is not None
lint = importlib.util.module_from_spec(_spec)
sys.modules["lint_block_record_sites"] = lint
_spec.loader.exec_module(lint)


_UNRECORDED_SHELL = """\
#!/usr/bin/env bash
block_it() {
  log "task ${task_id}: blocking"
  set_state "$task_status" "BLOCKED"
  set_block "$task_status" "Blockers" "Something broke"
  set_block "$task_status" "Needs Human" "yes"
  mark_backlog "$task_id" "B"
}
"""

_RECORDED_SHELL = """\
#!/usr/bin/env bash
block_it() {
  set_state "$task_status" "BLOCKED"
  set_block "$task_status" "Blockers" "Something broke"
  set_block "$task_status" "Needs Human" "yes"
  block_record_write "$task_status" missing_readme "$(block_record_owner_role "$task_status")" \\
    || log "task ${task_id}: WARNING: Block Record not written"
  mark_backlog "$task_id" "B"
}
"""


def _write(tmp_path: pathlib.Path, name: str, text: str) -> pathlib.Path:
    path = tmp_path / name
    path.write_text(text, encoding="utf-8")
    return path


def _run_cli(*args: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, str(_LINT_SCRIPT), *args], capture_output=True, text=True, timeout=300,
    )


def test_fixture_blocked_write_without_helper_is_reported_and_exit_is_zero(tmp_path: pathlib.Path) -> None:
    fixture = _write(tmp_path, "block_site.sh", _UNRECORDED_SHELL)
    findings_file = tmp_path / "out" / "findings.json"
    result = _run_cli("--paths", str(fixture), "--findings-file", str(findings_file))
    assert result.returncode == 0
    assert f"{fixture}:4:" in result.stdout
    findings = json.loads(findings_file.read_text(encoding="utf-8"))
    assert [(f["line"], f["kind"], f["severity"]) for f in findings] == [
        (4, "blocked-write-without-block-record", "quality")
    ]


def test_fixture_blocked_write_with_helper_is_clean(tmp_path: pathlib.Path) -> None:
    fixture = _write(tmp_path, "block_site.sh", _RECORDED_SHELL)
    findings_file = tmp_path / "findings.json"
    result = _run_cli("--paths", str(fixture), "--findings-file", str(findings_file))
    assert result.returncode == 0
    assert json.loads(findings_file.read_text(encoding="utf-8")) == []
    assert "0 finding(s)" in result.stdout


@pytest.mark.parametrize(
    "line",
    [
        'set_state "$task_status" "BLOCKED"',
        "set_state \"$status\" BLOCKED",
        'transition_task_state "$task_id" "$task_status" "BLOCKED" \\',
        'if transition_task_state "$task_id" "$task_status" "BLOCKED"; then',
        'set_block "$task_status" "State" "BLOCKED"',
        'pp_run_ops pgai_agent_kanban.task_status render "$f" --section=State --body=BLOCKED',
    ],
)
def test_each_shell_blocked_write_shape_is_recognised(line: str) -> None:
    assert [n for n, _ in lint.scan_shell(f"#!/usr/bin/env bash\n{line}\n")] == [2]


@pytest.mark.parametrize(
    "line",
    [
        'bash team/scripts/task-status.sh --project demo --key T-1 --state BLOCKED',
        'transition_task_state "$task_id" "$task_status" "$new_state"',
        'set_state "$task_status" "DONE"',
        '# set_state "$task_status" "BLOCKED"',
        'if [[ "$state" == "BLOCKED" ]]; then',
        'LAST_TASK_FINAL_STATE="BLOCKED"',
    ],
)
def test_non_write_and_status_writer_shell_lines_are_not_sites(line: str) -> None:
    assert lint.scan_shell(f"#!/usr/bin/env bash\n{line}\n") == []


def test_python_blocked_write_is_recognised_and_reads_are_not() -> None:
    source = (
        'set_sections(path, {"State": "BLOCKED", "Needs Human": "yes"})\n'
        'if section_value(text, "State") == "BLOCKED":\n'
        '    pass\n'
        'write_task_status(path, state="BLOCKED")\n'
        '"""A docstring naming "State" and "BLOCKED" is prose."""\n'
    )
    assert [n for n, _ in lint.scan_python(source)] == [1]
    recorded = source + 'write_block_record(path, "agent_unclassified", "CODER")\n'
    assert lint.scan_python(recorded) == []


def test_helper_far_below_the_site_does_not_count() -> None:
    padding = "\n".join(f"echo {i}" for i in range(lint.FORWARD_WINDOW + 2))
    source = f'set_state "$s" "BLOCKED"\n{padding}\nblock_record_write "$s" missing_readme CODER\n'
    assert [n for n, _ in lint.scan_shell(source)] == [1]


def test_tree_scan_skips_test_code(tmp_path: pathlib.Path) -> None:
    root = tmp_path / "repo"
    (root / "team" / "scripts").mkdir(parents=True)
    (root / "team" / "tests" / "unit").mkdir(parents=True)
    _write(root / "team" / "scripts", "site.sh", _UNRECORDED_SHELL)
    _write(root / "team" / "tests" / "unit", "fixture_site.sh", _UNRECORDED_SHELL)
    scanned, findings = lint.run(root)
    assert scanned == 1
    assert [(f.file, f.line) for f in findings] == [("team/scripts/site.sh", 4)]


def test_real_tree_reports_zero_findings() -> None:
    result = _run_cli("--root", str(_REPO_ROOT))
    assert result.returncode == 0
    assert "totals:" in result.stdout and " 0 finding(s)" in result.stdout, result.stdout


def test_help_exits_zero() -> None:
    result = _run_cli("--help")
    assert result.returncode == 0
    assert "--findings-file" in result.stdout
