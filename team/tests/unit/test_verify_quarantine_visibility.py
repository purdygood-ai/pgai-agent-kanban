"""
test_verify_quarantine_visibility.py
====================================
Keeps verification/verify_quarantine_visibility.sh honest when a command it
captures fails.

The verifier runs under ``set -euo pipefail`` and captures the output of a
synthetic quarantine helper (Step C) and of scan_attention.py (Steps D and
F). A capture that exits non-zero must be recorded as a FAIL line carrying
its output; the run must still reach CLEANUP (which removes its sandbox and
helper script) and SUMMARY (which emits the checks_failed error contract and
exits 1). This module asserts:

  1. Pointed at a dev tree with no team/ tree -- so
     team/scripts/lib/ini_parser.sh is missing and the Step C helper fails --
     the verifier exits 1, prints a FAIL line naming Step C with the
     helper's output, prints CLEANUP and SUMMARY after it, emits the
     ``verify_quarantine_visibility.checks_failed`` contract on stderr, and
     leaves no ``verify_quarantine_*`` entry under the temp root.
  2. Pointed at the shipped tree, the verifier still passes every check,
     exits 0 and leaves the temp root empty.
  3. The verifier script parses (bash -n).

Every run uses a sandbox root under pytest's tmp_path with all five
live-install names set or cleared together (docs/test-authoring-guidelines.md
"Live-install safety"): KANBAN_ROOT, PGAI_AGENT_KANBAN_ROOT_PATH and
PGAI_AGENT_KANBAN_TEMP_DIR point into the sandbox, PGAI_TASKS_DIR and
PGAI_PROJECT_ROOT are unset. The verifier runs from tmp_path. No live task
folder or temp root entry is read or written.
"""

from __future__ import annotations

import pathlib
import re

from tests.unit.shell_harness import run_bash

_TEAM_ROOT = pathlib.Path(__file__).resolve().parents[2]
_REPO_ROOT = _TEAM_ROOT.parent
_VERIFIER = _TEAM_ROOT / "verification" / "verify_quarantine_visibility.sh"

_ERROR_KEY = "verify_quarantine_visibility.checks_failed"
_SUMMARY_FAILED_RE = re.compile(r"^\s*Failed:\s+(\d+)\s*$", re.MULTILINE)


def _diagnostic(result) -> str:
    return f"--- stdout ---\n{result.stdout}\n--- stderr ---\n{result.stderr}"


def _run_verifier_in_sandbox(tmp_path: pathlib.Path, dev_tree: pathlib.Path):
    """Run the verifier against *dev_tree* with every live-install name
    pointed into a sandbox; return the result and the sandbox temp root."""
    sandbox = tmp_path / "sandbox"
    temp_root = sandbox / "tmp"
    temp_root.mkdir(parents=True)
    script = (
        "env -u PGAI_TASKS_DIR -u PGAI_PROJECT_ROOT "
        f'KANBAN_ROOT="{sandbox}" '
        f'PGAI_AGENT_KANBAN_ROOT_PATH="{sandbox}" '
        f'PGAI_AGENT_KANBAN_TEMP_DIR="{temp_root}" '
        f'bash "{_VERIFIER}" --dev-tree "{dev_tree}"'
    )
    return run_bash(tmp_path, script, timeout=180, cwd=tmp_path), temp_root


def _leftovers(temp_root: pathlib.Path) -> list[str]:
    return sorted(p.name for p in temp_root.glob("verify_quarantine_*"))


def test_failing_quarantine_helper_is_recorded_and_run_reaches_summary(tmp_path):
    # An empty dev tree: team/scripts/lib/ini_parser.sh (and every other
    # file the verifier reads) is missing, so the Step C helper exits
    # non-zero and both scan_attention.py captures fail too.
    dev_tree = tmp_path / "emptydev"
    dev_tree.mkdir()
    assert not (dev_tree / "team" / "scripts" / "lib" / "ini_parser.sh").exists()

    result, temp_root = _run_verifier_in_sandbox(tmp_path, dev_tree)

    assert result.returncode == 1, _diagnostic(result)

    lines = result.stdout.splitlines()
    step_c_fail = [i for i, line in enumerate(lines) if line.lstrip().startswith("FAIL: STEP C")]
    assert step_c_fail, f"no FAIL line naming Step C\n{_diagnostic(result)}"
    fail_line = lines[step_c_fail[0]]
    # The helper's own error output is shown on the FAIL line.
    assert "ini_parser.sh" in fail_line and "No such file or directory" in fail_line, (
        f"Step C FAIL line does not show the helper's output: {fail_line!r}\n{_diagnostic(result)}"
    )

    cleanup = [i for i, line in enumerate(lines) if line.strip() == "=== CLEANUP ==="]
    summary = [i for i, line in enumerate(lines) if line.strip() == "=== SUMMARY ==="]
    assert cleanup and summary, f"CLEANUP or SUMMARY section missing\n{_diagnostic(result)}"
    assert step_c_fail[0] < cleanup[0] < summary[0], _diagnostic(result)

    failed = _SUMMARY_FAILED_RE.search(result.stdout)
    assert failed and int(failed.group(1)) > 0, _diagnostic(result)

    # The checks_failed contract on stderr: the key plus non-blank message
    # and remedy lines.
    stderr_lines = result.stderr.splitlines()
    assert any(
        line.startswith("error:") and line.split(":", 1)[1].strip() == _ERROR_KEY
        for line in stderr_lines
    ), _diagnostic(result)
    for field in ("message:", "remedy:"):
        values = [line.split(":", 1)[1].strip() for line in stderr_lines if line.startswith(field)]
        assert values and all(values), f"blank or missing {field} line\n{_diagnostic(result)}"

    # CLEANUP ran: neither the sandbox directory nor the helper script remains.
    assert _leftovers(temp_root) == [], _diagnostic(result)


def test_verifier_passes_on_shipped_tree(tmp_path):
    result, temp_root = _run_verifier_in_sandbox(tmp_path, _REPO_ROOT)

    assert result.returncode == 0, _diagnostic(result)
    assert "ALL CHECKS PASSED" in result.stdout, _diagnostic(result)
    assert "FAIL:" not in result.stdout, _diagnostic(result)
    assert _ERROR_KEY not in result.stderr, _diagnostic(result)
    assert _leftovers(temp_root) == [], _diagnostic(result)


def test_verifier_script_parses(tmp_path):
    result = run_bash(tmp_path, f'bash -n "{_VERIFIER}"', cwd=tmp_path)
    assert result.returncode == 0, _diagnostic(result)


def _run_without_dev_tree_flag(tmp_path: pathlib.Path, env: str = ""):
    sandbox = tmp_path / "sandbox"
    temp_root = sandbox / "tmp"
    temp_root.mkdir(parents=True, exist_ok=True)
    script = (
        "env -u PGAI_TASKS_DIR -u PGAI_PROJECT_ROOT -u PGAI_PROJECT_NAME -u PGAI_DEV_TREE_PATH "
        f'KANBAN_ROOT="{sandbox}" '
        f'PGAI_AGENT_KANBAN_ROOT_PATH="{sandbox}" '
        f'PGAI_AGENT_KANBAN_TEMP_DIR="{temp_root}" {env} '
        f'bash "{_VERIFIER}"'
    )
    return run_bash(tmp_path, script, timeout=180, cwd=tmp_path)


def test_no_dev_tree_known_is_refused_with_a_runnable_remedy(tmp_path):
    result = _run_without_dev_tree_flag(tmp_path)

    assert result.returncode == 1, _diagnostic(result)
    assert "error:   verify_quarantine_visibility.dev_tree_unresolved" in result.stderr
    remedy = next(l for l in result.stderr.splitlines() if l.startswith("remedy:"))
    assert "<" not in remedy and ">" not in remedy, remedy
    assert "--dev-tree /srv/develop/my-app" in remedy
    assert "develop/pgai-agent-kanban" not in result.stdout + result.stderr
    assert "=== STEP A" not in result.stdout, "no check may run without a dev tree"


def test_dev_tree_defaults_to_the_projects_dev_tree_path(tmp_path):
    dev_tree = tmp_path / "configured-dev"
    dev_tree.mkdir()
    project_dir = tmp_path / "sandbox" / "projects" / "pgai-agent-kanban"
    project_dir.mkdir(parents=True)
    (project_dir / "project.cfg").write_text(
        f"[project]\nproject_name = pgai-agent-kanban\ndev_tree_path = {dev_tree}\n", encoding="utf-8"
    )
    result = _run_without_dev_tree_flag(tmp_path)

    # The run checked the configured tree: its missing lib is what Step C reports.
    assert "verify_quarantine_visibility.dev_tree_unresolved" not in result.stderr, _diagnostic(result)
    assert f"{dev_tree}/team/scripts/lib/ini_parser.sh" in result.stdout, _diagnostic(result)
