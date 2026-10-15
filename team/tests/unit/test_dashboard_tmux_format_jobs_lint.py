"""
test_dashboard_tmux_format_jobs_lint.py
=======================================
team/scripts/dashboard/lint_tmux_format_jobs.py reports a tmux option a
dashboard script sets whose value runs a #() job -- tmux re-runs such a job
on almost every redraw, once per attached client.

Pinned here:
  * the shipped dashboard scripts pass (exit 0);
  * a seeded `#(echo hi)` in status-right fails (exit 1), naming the file
    and line, with the dual-channel error/message/remedy block;
  * a #() in a comment, a #() outside any tmux option setter, and a
    set-hook line are not findings; a backslash-continued setter is one.
"""

from __future__ import annotations

import pathlib
import subprocess
import sys

import pytest

_TEAM_DIR = pathlib.Path(__file__).resolve().parents[2]
_LINT = _TEAM_DIR / "scripts" / "dashboard" / "lint_tmux_format_jobs.py"


def _run(*args: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, str(_LINT), *args],
        capture_output=True, text=True, timeout=60, check=False,
    )


def test_shipped_dashboard_scripts_pass() -> None:
    result = _run()
    assert result.returncode == 0, result.stdout + result.stderr
    assert "OK" in result.stdout


def test_seeded_format_job_in_status_right_fails(tmp_path: pathlib.Path) -> None:
    script = tmp_path / "seeded.sh"
    script.write_text(
        "#!/usr/bin/env bash\n"
        'tmux set-option -g status-left "#[fg=cyan]#S"\n'
        'tmux set-option -g status-right "#(echo hi)"\n',
        encoding="utf-8",
    )
    result = _run("--path", str(script))
    assert result.returncode == 1, result.stdout + result.stderr
    assert f"{script}:3:" in result.stderr, result.stderr
    assert "status-right" in result.stderr
    for field in ("error:   dashboard.tmux-format-job", "message: ", "remedy:  "):
        assert field in result.stderr, result.stderr


@pytest.mark.parametrize(
    ("body", "findings"),
    [
        ('# tmux set-option -g status-right "#(echo hi)"\n', 0),
        ('echo "a #(literal) outside tmux"\n', 0),
        ('tmux set-hook -g client-attached "run-shell \'#(echo hi)\'"\n', 0),
        ('tmux setw -g window-status-format "#(date)"\n', 1),
        ('tmux set-option -t "$S" -g "status-format[2]" \\\n  "#[bg=red]#(cmd)#[default]"\n', 1),
        ('tmux select-pane -t "$P" -T "#(hostname)"\n', 1),
    ],
    ids=["comment", "not-a-tmux-option", "set-hook", "setw", "continued-line", "pane-title"],
)
def test_only_tmux_option_setters_are_findings(
    tmp_path: pathlib.Path, body: str, findings: int
) -> None:
    script = tmp_path / "case.sh"
    script.write_text("#!/usr/bin/env bash\n" + body, encoding="utf-8")
    result = _run("--path", str(tmp_path))
    assert result.returncode == (1 if findings else 0), result.stdout + result.stderr
    assert result.stderr.count("#() job in a tmux option") == findings, result.stderr


def test_missing_path_is_a_usage_error(tmp_path: pathlib.Path) -> None:
    result = _run("--path", str(tmp_path / "absent"))
    assert result.returncode == 2
    assert "no such file or directory" in result.stderr
