"""
test_dashboard_status_line_facts.py
===================================
The bottom status line's facts come from one Python call
(team/pgai_agent_kanban/dashboard/status_line.py) instead of one process per
halt scope, per draining token and per bug file. These tests pin:

  1. The severity decision answers exactly as discovery's own
     _disc_severity_defer_decision (team/scripts/lib/discovery_defer.sh),
     driven with the same inputs through both implementations.
  2. deferred_bug_ids, halt_rows and pending_approval_count read the
     files the way the status line and the bug cards always have.
  3. The facts CLI prints one 0x1F-joined line per fact, in order.
  4. status-bottom.sh's rendered line, for a fixed fixture state, is
     byte-identical to the line the per-process renderer produced -- the
     expected strings below were captured from it -- for the HALT,
     HALT-AFTER, APPROVAL and BUGS DEFERRED segments, on the global bar
     and on a per-project drill bar, in rich and NO_COLOR modes.
"""

from __future__ import annotations

import os
import pathlib
import re
import subprocess
import sys

import pytest

from tests.fixtures.scrubbed_subprocess_env import build_scrubbed_env

_TEAM_DIR = pathlib.Path(__file__).resolve().parents[2]
_REPO_ROOT = _TEAM_DIR.parent
_STATUS_BOTTOM = _TEAM_DIR / "scripts" / "dashboard" / "status-bottom.sh"
_DISCOVERY_DEFER = _TEAM_DIR / "scripts" / "lib" / "discovery_defer.sh"

_team_str = str(_TEAM_DIR)
if _team_str not in sys.path:
    sys.path.insert(0, _team_str)

from pgai_agent_kanban.dashboard import status_line  # noqa: E402

_SEP = status_line.FIELD_SEP


def _env(tmp_path: pathlib.Path, **extra: str) -> dict:
    home = tmp_path / "home"
    home.mkdir(exist_ok=True)
    return build_scrubbed_env(
        home_dir=home,
        extra_path_dirs=[pathlib.Path(sys.executable).parent],
        extra_env={"PGAI_AGENT_KANBAN_TEMP_DIR": str(tmp_path / "scratch"), **extra},
    )


# ---------------------------------------------------------------------------
# 1. Severity decision parity with discovery_defer.sh
# ---------------------------------------------------------------------------

_SEVERITIES = [
    "low", "LOW", "Low.", "  high  ", "high.", "medium", "Medium", "critical",
    "Critical.", "", "weird", "low..", " low .", "lo w", "high\t", ".",
]
_LEVELS = ["low", "medium", "high", "critical", "bogus", ""]


def test_severity_decision_answers_as_discovery_does(tmp_path: pathlib.Path) -> None:
    cases = [(sev, lvl) for sev in _SEVERITIES for lvl in _LEVELS]
    script = (
        f'source "{_DISCOVERY_DEFER}" >/dev/null 2>&1\n'
        'while IFS=$\'\\x1f\' read -r -d $\'\\x1e\' sev lvl; do\n'
        '  printf \'%s\\n\' "$(_disc_severity_defer_decision "$sev" "$lvl")"\n'
        'done\n'
    )
    stdin = "".join(f"{sev}\x1f{lvl}\x1e" for sev, lvl in cases)
    result = subprocess.run(
        ["bash", "-c", script], input=stdin, capture_output=True, text=True,
        env=_env(tmp_path), timeout=60, check=False,
    )
    assert result.returncode == 0, result.stderr
    bash_answers = result.stdout.splitlines()
    assert len(bash_answers) == len(cases), result.stdout
    mismatches = [
        (sev, lvl, expected, status_line.severity_defer_decision(sev, lvl))
        for (sev, lvl), expected in zip(cases, bash_answers)
        if status_line.severity_defer_decision(sev, lvl) != expected
    ]
    assert not mismatches, f"(severity, level, discovery, status_line): {mismatches}"


# ---------------------------------------------------------------------------
# 2. The individual readers
# ---------------------------------------------------------------------------


def _bug(bugs_dir: pathlib.Path, stem: str, severity: str, status: str = "open") -> None:
    bugs_dir.mkdir(parents=True, exist_ok=True)
    (bugs_dir / f"{stem}.md").write_text(
        f"# {stem}\n\n**Severity:** {severity}\n\n## Status\n{status}\n", encoding="utf-8"
    )


def test_deferred_bug_ids_name_open_bugs_the_level_defers(tmp_path: pathlib.Path) -> None:
    bugs = tmp_path / "bugs"
    _bug(bugs, "BUG-0001-slugged-title", "low")
    _bug(bugs, "bug-0002", "Low.")
    _bug(bugs, "BUG-0003", "high")
    _bug(bugs, "BUG-0004", "low", status="done")
    _bug(bugs, "BUG-0005", "no such rung")
    _bug(bugs, "no-id-here", "medium")
    (bugs / "README.md").write_text("**Severity:** low\n\n## Status\nopen\n", encoding="utf-8")

    assert status_line.deferred_bug_ids(bugs, "low") == ["BUG-0001", "bug-0002"]
    assert status_line.deferred_bug_ids(bugs, "medium") == ["BUG-0001", "bug-0002", "no-id-here"]
    assert status_line.deferred_bug_ids(tmp_path / "missing", "low") == []


def test_halt_rows_report_active_scopes_in_order_with_tokens_in_words(
    tmp_path: pathlib.Path,
) -> None:
    root = tmp_path / "root"
    for name in ("alpha", "beta", "gamma"):
        (root / "projects" / name).mkdir(parents=True)
    (root / "HALT").write_text("", encoding="utf-8")
    (root / "projects" / "alpha" / "HALT-AFTER").write_text("rc:v1.2.3\n", encoding="utf-8")
    (root / "projects" / "beta" / "HALT-AFTER").write_text("not-a-token\n", encoding="utf-8")
    (root / "projects" / "gamma" / "HALT-AFTER").write_text("coder\n", encoding="utf-8")

    scopes = [("GLOBAL", root)] + [
        (name, root / "projects" / name) for name in ("alpha", "beta", "gamma", "absent")
    ]
    assert status_line.halt_rows(scopes) == [
        ("halted", "GLOBAL", ""),
        ("draining", "alpha", "until v1.2.3"),
        ("draining", "gamma", "coder"),
    ]


def test_pending_approvals_count_waiting_and_backlog_gates_only(tmp_path: pathlib.Path) -> None:
    root = tmp_path / "root"
    for project, task, state in [
        ("alpha", "HUMAN-APPROVE-1", "WAITING"),
        ("beta", "HUMAN-APPROVE-2", "BACKLOG"),
        ("beta", "HUMAN-APPROVE-3", "DONE"),
        ("beta", "CODER-20260101-001-x", "WAITING"),
    ]:
        task_dir = root / "projects" / project / "tasks" / task
        task_dir.mkdir(parents=True)
        (task_dir / "status.md").write_text(f"# Status\n\n## State\n{state}\n", encoding="utf-8")

    assert status_line.pending_approval_count(root) == 2


def test_facts_cli_prints_one_separated_line_per_fact(tmp_path: pathlib.Path) -> None:
    root = tmp_path / "root"
    (root / "projects" / "alpha").mkdir(parents=True)
    (root / "projects" / "alpha" / "HALT").write_text("", encoding="utf-8")
    bugs = root / "projects" / "alpha" / "bugs"
    _bug(bugs, "BUG-0001", "low")
    _bug(bugs, "BUG-0002", "low")

    result = subprocess.run(
        [
            sys.executable, "-m", "pgai_agent_kanban.dashboard.status_line", "facts", str(root),
            "--halt-scope", "GLOBAL", str(root),
            "--halt-scope", "alpha", str(root / "projects" / "alpha"),
            "--defer-count", "alpha", "low", str(bugs),
            "--defer-count", "GLOBAL", "low",
        ],
        cwd=_TEAM_DIR, capture_output=True, text=True, env=_env(tmp_path), timeout=60, check=False,
    )
    assert result.returncode == 0, result.stderr
    assert result.stdout.splitlines() == [
        f"halt{_SEP}halted{_SEP}alpha{_SEP}",
        f"approvals{_SEP}0",
        f"defer{_SEP}alpha{_SEP}2",
        f"defer{_SEP}GLOBAL{_SEP}0",
    ]


# ---------------------------------------------------------------------------
# 4. The rendered line is byte-identical to the per-process renderer's
# ---------------------------------------------------------------------------

_TIMESTAMP = re.compile(r"\w{3} \d{4}-\d\d-\d\dT\d\d:\d\d:\d\d")


def _fixture_root(tmp_path: pathlib.Path) -> pathlib.Path:
    root = tmp_path / "root"
    root.mkdir()
    (root / "VERSION").write_text("v9.9.9\n", encoding="utf-8")
    (root / "projects.cfg").write_text(
        "[project:alpha]\npriority=1\n\n[project:beta]\npriority=2\n\n", encoding="utf-8"
    )
    for project in ("alpha", "beta"):
        (root / "projects" / project).mkdir(parents=True)
        (root / "projects" / project / "project.cfg").write_text(
            "[project]\nworkflow_type = release\n", encoding="utf-8"
        )
    return root


def _approval(root: pathlib.Path, project: str, task: str, state: str) -> None:
    task_dir = root / "projects" / project / "tasks" / task
    task_dir.mkdir(parents=True)
    (task_dir / "status.md").write_text(f"# Status\n\n## State\n{state}\n", encoding="utf-8")


def _halts_and_approvals(root: pathlib.Path) -> None:
    (root / "HALT").write_text("", encoding="utf-8")
    (root / "projects" / "alpha" / "HALT").write_text("", encoding="utf-8")
    (root / "projects" / "beta" / "HALT-AFTER").write_text("coder\n", encoding="utf-8")
    _approval(root, "alpha", "HUMAN-APPROVE-1", "WAITING")
    _approval(root, "beta", "HUMAN-APPROVE-2", "BACKLOG")
    _approval(root, "beta", "HUMAN-APPROVE-3", "DONE")


def _global_halt_after(root: pathlib.Path) -> None:
    (root / "HALT-AFTER").write_text("rc:v1.2.3\n", encoding="utf-8")


def _project_deferral(root: pathlib.Path) -> None:
    with (root / "projects" / "alpha" / "project.cfg").open("a", encoding="utf-8") as cfg:
        cfg.write("\n[release]\ndeferred_bugs = true\ndeferred_bug_level = low\n")
    _bug(root / "projects" / "alpha" / "bugs", "BUG-0001-x", "low")
    _bug(root / "projects" / "alpha" / "bugs", "BUG-0002-x", "low")
    _bug(root / "projects" / "alpha" / "bugs", "BUG-0003-x", "high")


_RENDER_CASES = {
    # (fixture, project argument, NO_COLOR?) -> expected line, timestamp masked
    "global-halts-approvals-plain": (
        _halts_and_approvals, None, True,
        "v9.9.9 | PM:auto | [HALT GLOBAL] | [HALT alpha] | [HALT-AFTER:beta coder] | [APPROVAL(2)] | <TS>",
    ),
    "global-halts-approvals-rich": (
        _halts_and_approvals, None, False,
        "📝 v9.9.9 | 🟢 PM:auto | #[fg=red]🛑 HALT GLOBAL#[default] | #[fg=red]🛑 HALT alpha#[default] | "
        "#[fg=yellow]⚠️ HALT-AFTER beta coder#[default] | #[fg=yellow]✋ APPROVAL(2)#[default] | 📅 <TS>",
    ),
    "drill-halts-approvals-plain": (
        _halts_and_approvals, "alpha", True,
        "v9.9.9 | alpha:v0.0.0 | workflow:release | PM:auto | [HALT GLOBAL] | [HALT alpha] | [APPROVAL(2)] | <TS>",
    ),
    "global-halt-after-until-rich": (
        _global_halt_after, None, False,
        "📝 v9.9.9 | 🟢 PM:auto | #[fg=yellow]⚠️ HALT-AFTER GLOBAL until v1.2.3#[default] | 📅 <TS>",
    ),
    "global-halt-after-until-plain": (
        _global_halt_after, None, True,
        "v9.9.9 | PM:auto | [HALT-AFTER:GLOBAL until v1.2.3] | <TS>",
    ),
    "drill-bugs-deferred-plain": (
        _project_deferral, "alpha", True,
        "v9.9.9 | alpha:v0.0.0 | workflow:release | PM:auto | BUGS DEFERRED 2 (<=low) | <TS>",
    ),
    "drill-bugs-deferred-rich": (
        _project_deferral, "alpha", False,
        "📝 v9.9.9 | 📝 alpha:v0.0.0 | workflow:release | 🟢 PM:auto | ✋ BUGS DEFERRED 2 (<=low) | 📅 <TS>",
    ),
}


@pytest.mark.parametrize("case", sorted(_RENDER_CASES))
def test_rendered_line_is_byte_identical_for_the_same_fixture_state(
    tmp_path: pathlib.Path, case: str
) -> None:
    build, project, no_color, expected = _RENDER_CASES[case]
    root = _fixture_root(tmp_path)
    build(root)
    extra = {
        "PGAI_AGENT_KANBAN_ROOT_PATH": str(root),
        "PGAI_DEV_TREE_PATH": str(_REPO_ROOT),
        "TERM": "dumb" if no_color else "xterm-256color",
    }
    if no_color:
        extra["NO_COLOR"] = "1"
    argv = ["bash", str(_STATUS_BOTTOM), str(root)] + ([project] if project else [])
    result = subprocess.run(
        argv, capture_output=True, text=True, env=_env(tmp_path, **extra), timeout=120, check=False
    )
    assert result.returncode == 0, result.stderr
    assert _TIMESTAMP.sub("<TS>", result.stdout) == expected, result.stderr
