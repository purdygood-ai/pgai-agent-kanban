"""
test_scan_attention_unreadable_count.py
=======================================
The attention scanners count the inputs they cannot read or parse and close
their section with an ``unreadable: N`` line, so a read failure never shows
as an empty pane:

  1. A discovery state file with a malformed count row makes the
     quarantine section end with ``unreadable: 1`` naming that file.
  2. A task status.md that cannot be read makes the blocked-tasks section
     end with ``unreadable: 1`` naming it, beside the tasks it could read.
  3. A clean root prints no unreadable line, and each scanner call starts
     its count afresh.
  4. The same line reaches the pane through the command line attention.sh
     runs.

Every root is built under tmp_path; nothing reads the live install.
"""

from __future__ import annotations

import pathlib
import subprocess
import sys

import pytest

from pgai_agent_kanban.dashboard import scan_attention
from tests.fixtures.scrubbed_subprocess_env import build_scrubbed_env

_TEAM_DIR = pathlib.Path(__file__).resolve().parents[2]
_SCANNER = _TEAM_DIR / "pgai_agent_kanban" / "dashboard" / "scan_attention.py"


def _kanban_with_bad_state_row(root: pathlib.Path) -> pathlib.Path:
    state = root / "projects" / "alpha" / ".discovery-state" / "rejected-counts.tsv"
    state.parent.mkdir(parents=True)
    state.write_text(
        "BUG-0001-good.md\t1\t2026-10-31T00:00:00\treason\n"
        "BUG-0002-bad.md\tnot-a-number\t2026-10-31T00:00:00\treason\n",
        encoding="utf-8",
    )
    return state


def _blocked_task(tasks: pathlib.Path, name: str) -> pathlib.Path:
    folder = tasks / name
    folder.mkdir(parents=True)
    status = folder / "status.md"
    status.write_text("# Status\n\n## State\nBLOCKED\n\n## Blockers\nwaiting\n", encoding="utf-8")
    return status


def test_a_malformed_state_row_is_counted_on_the_quarantine_section(tmp_path, capsys) -> None:
    state = _kanban_with_bad_state_row(tmp_path)

    scan_attention.scan_quarantine(kanban_root=str(tmp_path), use_color=False, threshold=3)

    out = capsys.readouterr().out
    assert out.rstrip("\n").splitlines()[-1].startswith(f"  unreadable: 1 (first: {state}: "), out
    assert scan_attention.unreadable_inputs().count == 1


def test_an_unreadable_status_file_is_counted_on_the_blocked_section(tmp_path, capsys, monkeypatch) -> None:
    tasks = tmp_path / "tasks"
    _blocked_task(tasks, "CODER-20261031-001-readable")
    broken = _blocked_task(tasks, "CODER-20261031-002-unreadable")
    real_read_text = pathlib.Path.read_text

    def read_text(self, *args, **kwargs):
        if self == broken:
            raise PermissionError(13, "Permission denied", str(self))
        return real_read_text(self, *args, **kwargs)

    monkeypatch.setattr(pathlib.Path, "read_text", read_text)

    scan_attention.scan_blocked_tasks(tasks_root=str(tasks), use_color=False)

    out = capsys.readouterr().out
    assert "CODER-20261031-001-readable" in out, out
    assert f"  unreadable: 1 (first: {broken}: Permission denied)" in out, out


def test_a_clean_root_prints_no_unreadable_line_and_counts_start_fresh(tmp_path, capsys) -> None:
    _kanban_with_bad_state_row(tmp_path / "dirty")
    scan_attention.scan_quarantine(kanban_root=str(tmp_path / "dirty"), use_color=False, threshold=3)
    capsys.readouterr()
    clean = tmp_path / "clean"
    (clean / "projects" / "alpha").mkdir(parents=True)

    scan_attention.scan_quarantine(kanban_root=str(clean), use_color=False, threshold=3)

    assert "unreadable:" not in capsys.readouterr().out
    assert scan_attention.unreadable_inputs().count == 0


@pytest.mark.parametrize("color_flag", ["--no-color", "--color"])
def test_the_pane_command_line_prints_the_unreadable_count(tmp_path, color_flag) -> None:
    state = _kanban_with_bad_state_row(tmp_path)
    env = build_scrubbed_env(home_dir=tmp_path, extra_env={"PYTHONPATH": str(_TEAM_DIR)})

    result = subprocess.run(
        [sys.executable, str(_SCANNER), "quarantine", str(tmp_path), color_flag, "--threshold", "3"],
        env=env,
        capture_output=True,
        text=True,
        timeout=60,
        cwd=str(tmp_path),
        check=False,
    )

    assert result.returncode == 0, result.stdout + result.stderr
    assert f"unreadable: 1 (first: {state}: " in result.stdout, result.stdout + result.stderr
