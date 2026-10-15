"""
test_ops_read_status_report_readme_rejected.py — the four script-shaped reads
in ``pgai_agent_kanban.ops.read`` and what they are built from.

``kanban-status.sh``, ``show-test-report.sh``, ``show-readme.sh`` and
``list-rejected.sh`` reach these functions through ``pp_run_ops``; GET
/status, /test-report, /projects/{p}/tasks/{k}/readme and /rejected call
them in-process. What is proven here:

  1. ``get_status`` / ``render_status``: the snapshot (version, Active RC
     validation, RC Opened At, last released, halt flag, counts, the tasks
     whose State is exactly BLOCKED, quarantined files without sidecars, the
     resolved ship posture and the resolver's warning kept off stderr until
     rendered), the rendered summary in plain text and in colour (colour
     names from ``PGAI_DASHBOARD_COLOR_*``), the RC and working-task age
     labels, an empty project refused with exit 1, and an unknown project
     read as empty with nothing created.
  2. ``lib.released_version``: the framework version tiers (kanban-root
     VERSION, repository VERSION, unknown, the caller's last released) and the
     last released version from release-state.md, from a dev tree's prefixed
     tags merged into ``<prefix>main``, and the ``v0.0.0`` fallback; and
     ``lib.project_cfg``, the project.cfg / legacy PROJECT.cfg reader it shares
     with ``ops.locks`` (the stale-lock threshold's tiers) and ``cm.cancel``.
  3. ``get_test_report``: a version key picks the latest
     ``TESTER-...-verify-X-Y-Z`` task; a task key resolves (an ambiguous prefix
     reads the first match, exit 2, with the resolver's notice); every refusal
     carries show-test-report.sh's own text and exit code.
  4. ``get_task_readme``: the README of a task key or prefix; ledger keys,
     missing READMEs, unknown keys and missing project directories refused with
     show-readme.sh's own text and exit codes.
  5. ``list_rejected`` / ``render_rejected``: per-project sections, one per
     intake directory the files came from (``priority``, ``bugs``, then
     ``rejected`` for an unknown origin), gathered from the project's
     ``rejected/`` directory (routed by the ``.reason`` sidecar) and the legacy
     ``<intake>/.rejected/`` directories (hidden files, symlinks and sidecars
     skipped), the reason derived from each name, the colour rule
     (``NO_COLOR=1`` only), and the two refusals.
  6. The read CLI (``python3 -m pgai_agent_kanban.ops <verb>``) prints the
     render result's stderr and stdout and exits with its code.

Every board is a throwaway directory under ``tmp_path``.
"""

from __future__ import annotations

import datetime
import os
import pathlib
import subprocess
import sys

import pytest

from pgai_agent_kanban.dashboard._rejected import count_quarantined
from pgai_agent_kanban.dashboard.rejected_listing import rejection_reason
from pgai_agent_kanban.dashboard.status_summary import StatusSnapshot, render_status_summary
from pgai_agent_kanban.dashboard.task_counts import scan_task_counts
from pgai_agent_kanban.lib import project_cfg, released_version
from pgai_agent_kanban.ops import locks as ops_locks
from pgai_agent_kanban.ops import read as ops_read
from pgai_agent_kanban.ops.context import OpsContext
from pgai_agent_kanban.ops.errors import CliRefusal

_TEAM_DIR = pathlib.Path(__file__).resolve().parent.parent.parent
_PROJECT = "alpha"
_WORKING = "CODER-20260101-001-work"
_WORKING_MTIME = 1767268800  # 2026-01-01T12:00:00Z
_NOW = _WORKING_MTIME + 3 * 3600 + 5 * 60 + 7
_RC_OPENED_AT = "2026-01-01T10:00:00+00:00"
_PLAIN_ENV = {"TERM": "dumb"}
_COLOR_ENV = {"TERM": "xterm"}


def _write(path: pathlib.Path, text: str = "", mtime: int | None = None) -> pathlib.Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    if mtime is not None:
        os.utime(path, (mtime, mtime))
    return path


def _task(project: pathlib.Path, task_id: str, state: str, role: str | None = "CODER",
          report: str | None = None, mtime: int | None = None) -> pathlib.Path:
    task_dir = project / "tasks" / task_id
    _write(task_dir / "status.md", f"# Status\n\n## State\n{state}\n\n## Summary\nfixture\n", mtime=mtime)
    if role is not None:
        _write(task_dir / "README.md", f"# Task: {task_id}\n\n## Role\n{role}\n")
    if report is not None:
        _write(task_dir / "artifacts" / "report.md", report)
    return task_dir


@pytest.fixture
def board(tmp_path: pathlib.Path) -> pathlib.Path:
    """A kanban root with one populated project and one bare registered project."""
    root = tmp_path / "board"
    _write(root / "VERSION", "v9.1.0\n")
    _write(root / "projects.cfg", f"[project:{_PROJECT}]\npriority = 1\n[project:bare]\npriority = 2\n")
    project = root / "projects" / _PROJECT
    _write(project / "release-state.md",
           f"# Release State\n\n## Active RC\nv1.2.0\n\n## RC Opened At\n{_RC_OPENED_AT}\n\n"
           "## RC Opened By Task\nCM-1\n\n## Last Released\nv1.1.0\n")
    _task(project, _WORKING, "WORKING", mtime=_WORKING_MTIME)
    _task(project, "CODER-20260101-002-blocked", "BLOCKED")
    _task(project, "WRITER-20260101-003-blocked", "BLOCKED", role="WRITER")
    _task(project, "CODER-20260101-004-lower", "blocked")
    _task(project, "PM-20260101-005-done", "DONE", role="PM")
    _task(project, "TESTER-20260101-006-verify-1-2-0", "DONE", role="TESTER", report="# Report 006\n")
    _task(project, "TESTER-20260102-007-verify-1-2-0", "DONE", role="TESTER", report="# Report 007\r\nCRLF\r\n")
    _task(project, "TESTER-20260103-008-verify-1-3-0", "WAITING", role="TESTER")
    _task(project, "TESTER-20260104-010-a", "DONE", role="TESTER", report="# Report 010-a\n")
    _task(project, "TESTER-20260104-010-b", "DONE", role="TESTER", report="# Report 010-b\n")
    _task(project, "CODER-20260101-009-noreadme", "BACKLOG", role=None)
    _task(project, "CM-20260101-011-cm", "WAITING", role="CM")
    (project / "tasks" / "queues").mkdir()
    _write(project / "bugs" / "BUG-0001-crash.md", "# BUG-0001\n\n## Status\nopen\n")
    _write(project / "rejected" / "one.md", "x")
    _write(project / "rejected" / "one.md.reason", "reason=bad\n")
    _write(project / "rejected" / ".two.md", "x")
    (root / "projects" / "bare").mkdir()
    return root


@pytest.fixture
def ctx(board: pathlib.Path) -> OpsContext:
    return OpsContext(kanban_root=board)


def _project_dir(board: pathlib.Path) -> pathlib.Path:
    return board / "projects" / _PROJECT


# ---------------------------------------------------------------------------
# 1. get_status / render_status
# ---------------------------------------------------------------------------


def test_get_status_reads_every_summary_field(ctx: OpsContext) -> None:
    snapshot = ops_read.get_status(ctx, _PROJECT)

    assert snapshot.project == _PROJECT
    assert snapshot.kanban_version == "v9.1.0"
    assert snapshot.active_rc == "v1.2.0"
    assert snapshot.rc_opened_at == _RC_OPENED_AT
    assert snapshot.last_released == "v1.1.0"
    assert snapshot.halt_flag == "no"
    assert snapshot.counts.totals["TOTAL"] == 12
    assert snapshot.counts.totals["BLOCKED"] == 3
    assert snapshot.blocked_tasks == ("CODER-20260101-002-blocked", "WRITER-20260101-003-blocked")
    assert snapshot.quarantined == 2
    assert snapshot.ship_posture == "matrix"
    assert snapshot.ship_posture_notice == ""


def test_get_status_reads_an_active_rc_that_is_not_a_version_as_none(ctx: OpsContext, board: pathlib.Path) -> None:
    _write(_project_dir(board) / "release-state.md", "## Active RC\nv1.2\n\n## RC Opened At\nnone\n")

    snapshot = ops_read.get_status(ctx, _PROJECT)

    assert snapshot.active_rc == "none"
    assert snapshot.rc_opened_at == "none"


def test_get_status_reports_a_kanban_wide_halt(ctx: OpsContext, board: pathlib.Path) -> None:
    _write(board / "HALT")

    assert ops_read.get_status(ctx, _PROJECT).halt_flag == "yes"


def test_render_status_plain_text(ctx: OpsContext) -> None:
    output = ops_read.render_status(ctx, _PROJECT, environ=_PLAIN_ENV, now=_NOW)

    assert output.exit_code == 0 and output.stderr == ""
    assert output.stdout == (
        "pgai-kanban v9.1.0    RC: v1.2.0 (5h 5m)    Last: v1.1.0\n"
        f"HALT: off    Working: {_WORKING} (3h 5m)\n"
        "\n"
        "Queue:\n"
        "  pm:     1/1 done\n"
        "  coder:  0/4 done, 1 working, 2 blocked\n"
        "  writer: 0/1 done, 1 blocked\n"
        "  tester: 4/5 done, waiting\n"
        "  cm:     0/1 done, waiting\n"
        "\n"
        "Progress: [######.........] 41%\n"
        "\n"
        "Blocked (3):\n"
        "  CODER-20260101-002-blocked\n"
        "  WRITER-20260101-003-blocked\n"
        "\n"
        "Quarantined: 2\n"
        "Ship posture: matrix\n"
    )


def test_render_status_colour_follows_term_no_color_and_the_flag(ctx: OpsContext) -> None:
    coloured = ops_read.render_status(ctx, _PROJECT, environ=_COLOR_ENV, now=_NOW).stdout
    flagged = ops_read.render_status(ctx, _PROJECT, no_color=True, environ=_COLOR_ENV, now=_NOW).stdout
    no_color_env = ops_read.render_status(ctx, _PROJECT, environ={"TERM": "xterm", "NO_COLOR": "0"}, now=_NOW).stdout
    unset_term = ops_read.render_status(ctx, _PROJECT, environ={}, now=_NOW).stdout

    assert coloured.splitlines()[0] == (
        "\033[1m\033[0;36mpgai-kanban v9.1.0\033[0m    RC: v1.2.0 (5h 5m)    Last: v1.1.0"
    )
    assert "Progress:\033[0m [\033[0;32m" + "▓" * 6 + "░" * 9 + "\033[0m] 41%" in coloured
    assert "\033" not in flagged and "\033" not in no_color_env and "\033" not in unset_term
    assert flagged == ops_read.render_status(ctx, _PROJECT, environ=_PLAIN_ENV, now=_NOW).stdout


def test_render_status_colour_names_come_from_the_dashboard_environment(ctx: OpsContext) -> None:
    env = {"TERM": "xterm", "PGAI_DASHBOARD_COLOR_HEADER": "magenta", "PGAI_DASHBOARD_COLOR_OK": ""}

    lines = ops_read.render_status(ctx, _PROJECT, environ=env, now=_NOW).stdout.splitlines()

    assert lines[0].startswith("\033[1m\033[0;35mpgai-kanban")
    assert lines[1].startswith("\033[0;32mHALT: off\033[0m")  # empty name keeps the default


@pytest.mark.parametrize(
    ("rc_elapsed", "working_elapsed", "rc_label", "working_label"),
    [
        (59, 0, "0m", "0s"),
        (3599, 59, "59m", "59s"),
        (3600, 60, "1h 0m", "1m 0s"),
        (86399, 3599, "23h 59m", "59m 59s"),
        (86400, 3600, "1d", "1h 0m"),
        (-30, -5, "0m", "-5s"),
    ],
)
def test_status_age_labels(rc_elapsed: int, working_elapsed: int, rc_label: str, working_label: str,
                           tmp_path: pathlib.Path) -> None:
    tasks = tmp_path / "tasks"
    _task(tmp_path, "CODER-20260101-001-w", "WORKING", mtime=_WORKING_MTIME)
    opened = datetime.datetime.fromtimestamp(_NOW - rc_elapsed, tz=datetime.timezone.utc).isoformat()
    snapshot = StatusSnapshot(
        project="p", kanban_version="v1.0.0", active_rc="v1.0.0", rc_opened_at=opened,
        last_released="none", halt_flag="no", counts=scan_task_counts(tasks), blocked_tasks=(), quarantined=0,
        ship_posture="matrix",
    )

    lines = render_status_summary(snapshot, use_color=False, now=_WORKING_MTIME + working_elapsed).splitlines()
    rc_lines = render_status_summary(snapshot, use_color=False, now=_NOW).splitlines()

    assert rc_lines[0] == f"pgai-kanban v1.0.0    RC: v1.0.0 ({rc_label})    Last: none"
    assert lines[1] == f"HALT: off    Working: CODER-20260101-001-w ({working_label})"


def test_rc_age_is_omitted_without_an_active_rc_or_a_parseable_timestamp(tmp_path: pathlib.Path) -> None:
    base = dict(project="p", kanban_version="v1", last_released="v0.0.0", halt_flag="no",
                counts=scan_task_counts(tmp_path / "none"), blocked_tasks=(), quarantined=0,
                ship_posture="matrix")

    no_rc = render_status_summary(StatusSnapshot(active_rc="none", rc_opened_at=_RC_OPENED_AT, **base),
                                  use_color=False, now=_NOW)
    bad_time = render_status_summary(StatusSnapshot(active_rc="v1.0.0", rc_opened_at="yesterday", **base),
                                     use_color=False, now=_NOW)

    assert no_rc.splitlines()[0] == "pgai-kanban v1    RC: none    Last: v0.0.0"
    assert bad_time.splitlines()[0] == "pgai-kanban v1    RC: v1.0.0    Last: v0.0.0"
    assert no_rc.splitlines()[1] == "HALT: off    Working: (none)"
    assert "Progress: [...............] 0%" in no_rc and "(no blocked tasks)" in no_rc


def test_get_status_refuses_an_empty_project(ctx: OpsContext) -> None:
    with pytest.raises(CliRefusal) as caught:
        ops_read.get_status(ctx, "")
    output = ops_read.render_status(ctx, "", environ=_PLAIN_ENV)

    assert caught.value.exit_code == 1
    assert output == ops_read.ReadOutput(
        1, "", "ERROR: no project specified; pass --project <name> or set PGAI_PROJECT_NAME\n"
    )


def _set_project_cfg(board: pathlib.Path, text: str) -> None:
    _write(_project_dir(board) / "project.cfg", text)


@pytest.mark.parametrize(
    ("project_cfg_text", "kanban_cfg_text", "expected"),
    [
        ("[project]\npush_to_remote = false\n\n[release]\nship_posture = always\n", None, "always"),
        ("[project]\npush_to_remote = false\n\n[release]\nship_posture = matrix\n", None, "matrix"),
        ("[project]\npush_to_remote = false\n", "[release]\nship_posture = always\n", "always"),
        ("[project]\npush_to_remote = true\n\n[release]\nship_posture = always\n"
         "ship_posture_confirm_remote = true\n", None, "always"),
    ],
    ids=["project-always", "project-matrix", "kanban-cfg-always", "remote-confirmed-always"],
)
def test_get_status_reads_the_resolved_ship_posture(
    ctx: OpsContext, board: pathlib.Path, project_cfg_text: str, kanban_cfg_text: str | None, expected: str
) -> None:
    _set_project_cfg(board, project_cfg_text)
    if kanban_cfg_text is not None:
        _write(board / "kanban.cfg", kanban_cfg_text)

    snapshot = ops_read.get_status(ctx, _PROJECT)
    output = ops_read.render_status(ctx, _PROJECT, environ=_PLAIN_ENV, now=_NOW)

    assert snapshot.ship_posture == expected and snapshot.ship_posture_notice == ""
    assert output.exit_code == 0 and output.stderr == ""
    assert output.stdout.endswith(f"Quarantined: 2\nShip posture: {expected}\n")


def test_unconfirmed_always_on_a_remote_lane_reads_matrix_with_the_warning_on_stderr(
    ctx: OpsContext, board: pathlib.Path, capsys: pytest.CaptureFixture[str]
) -> None:
    _set_project_cfg(board, "[project]\npush_to_remote = true\n\n[release]\nship_posture = always\n")

    snapshot = ops_read.get_status(ctx, _PROJECT)
    output = ops_read.render_status(ctx, _PROJECT, environ=_PLAIN_ENV, now=_NOW)

    assert snapshot.ship_posture == "matrix"
    assert snapshot.ship_posture_notice.count("\n") == 1
    assert "not confirmed for a push_to_remote = true lane" in snapshot.ship_posture_notice
    assert output.exit_code == 0
    assert output.stderr == snapshot.ship_posture_notice
    assert output.stdout.endswith("Ship posture: matrix\n")
    assert capsys.readouterr().err == ""  # kept on the snapshot, never written by the read itself


def test_an_unrecognized_ship_posture_reads_matrix_with_the_warning_on_stderr(
    ctx: OpsContext, board: pathlib.Path
) -> None:
    _set_project_cfg(board, "[project]\npush_to_remote = false\n\n[release]\nship_posture = sometimes\n")

    output = ops_read.render_status(ctx, _PROJECT, environ=_PLAIN_ENV, now=_NOW)

    assert output.exit_code == 0 and output.stdout.endswith("Ship posture: matrix\n")
    assert "unrecognized value 'sometimes'" in output.stderr and output.stderr.count("\n") == 1


def test_read_status_returns_the_rendered_output_and_its_snapshot(ctx: OpsContext) -> None:
    read = ops_read.read_status(ctx, _PROJECT, environ=_PLAIN_ENV, now=_NOW)
    refused = ops_read.read_status(ctx, "", environ=_PLAIN_ENV)

    assert read.output == ops_read.render_status(ctx, _PROJECT, environ=_PLAIN_ENV, now=_NOW)
    assert read.snapshot == ops_read.get_status(ctx, _PROJECT)
    assert refused.snapshot is None and refused.output.exit_code == 1


def test_unknown_project_reads_as_empty_and_creates_nothing(ctx: OpsContext, board: pathlib.Path) -> None:
    output = ops_read.render_status(ctx, "nosuch", environ=_PLAIN_ENV, now=_NOW)

    assert output.exit_code == 0
    assert output.stdout.splitlines()[0] == "pgai-kanban v9.1.0    RC: none    Last: v0.0.0"
    assert "  coder:  0/0" in output.stdout and output.stdout.endswith("Quarantined: 0\nShip posture: matrix\n")
    assert not (board / "projects" / "nosuch").exists()


def test_count_quarantined_skips_sidecars_symlinks_and_directories(tmp_path: pathlib.Path) -> None:
    rejected = tmp_path / "rejected"
    _write(rejected / "a.md", "x")
    _write(rejected / "a.md.reason", "reason=x\n")
    _write(rejected / ".hidden", "x")
    (rejected / "sub").mkdir()
    (rejected / "link.md").symlink_to(rejected / "a.md")

    assert count_quarantined(rejected) == 2
    assert count_quarantined(tmp_path / "missing") == 0


# ---------------------------------------------------------------------------
# 2. lib.released_version
# ---------------------------------------------------------------------------


def test_kanban_version_tiers(tmp_path: pathlib.Path) -> None:
    root, repo = tmp_path / "root", tmp_path / "repo"
    root.mkdir()
    repo.mkdir()

    assert released_version.kanban_version(root, repo) == "unknown"
    assert released_version.kanban_version(root, repo, last_released="v1.0.0") == "v1.0.0"
    assert released_version.kanban_version(root, repo, last_released="none") == "unknown"
    _write(repo / "VERSION", " v2.0.0 \n")
    assert released_version.kanban_version(root, repo) == "v2.0.0"
    _write(root / "VERSION", "v3.0.0\n")
    assert released_version.kanban_version(root, repo) == "v3.0.0"


def test_last_released_version_without_a_dev_tree_reads_release_state(tmp_path: pathlib.Path) -> None:
    project = tmp_path / "projects" / "p"
    _write(project / "release-state.md", "## Active RC\nnone\n\n## Last Released\nv1.4.2\n")

    assert released_version.last_released_version(tmp_path, "p") == "v1.4.2"
    _write(project / "project.cfg", "[project]\nproject_name = p\n")
    assert released_version.last_released_version(tmp_path, "p") == "v1.4.2"
    _write(project / "release-state.md", "## Last Released\nnone\n")
    assert released_version.last_released_version(tmp_path, "p") == "v0.0.0"
    assert released_version.last_released_version(tmp_path, "missing") == "v0.0.0"


def _git(repo: pathlib.Path, *args: str) -> None:
    env = {**os.environ, "GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@example.invalid",
           "GIT_COMMITTER_NAME": "t", "GIT_COMMITTER_EMAIL": "t@example.invalid"}
    subprocess.run(["git", *args], cwd=repo, env=env, check=True, capture_output=True)


def test_last_released_version_reads_prefixed_tags_merged_into_main(tmp_path: pathlib.Path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    _git(repo, "init", "-q", "-b", "ai_main")
    for content, tags in (("1", ("ai_v1.0.0",)), ("2", ("ai_v1.9.0", "ai_v1.10.0", "v5.0.0"))):
        _write(repo / "f.txt", content)
        _git(repo, "add", ".")
        _git(repo, "commit", "-q", "-m", content)
        for tag in tags:
            _git(repo, "tag", tag)
    _git(repo, "checkout", "-q", "-b", "side")
    _write(repo / "f.txt", "3")
    _git(repo, "commit", "-q", "-am", "3")
    _git(repo, "tag", "ai_v2.0.0")
    project = tmp_path / "projects" / "p"
    _write(project / "project.cfg", f'[project]\ndev_tree_path = {repo}\nbranch_prefix = "ai_"\n')
    _write(project / "release-state.md", "## Last Released\nv0.0.1\n")

    assert released_version.last_released_version(tmp_path, "p") == "v1.10.0"
    _git(repo, "branch", "-q", "-m", "ai_main", "elsewhere")
    assert released_version.last_released_version(tmp_path, "p") == "v2.0.0"


def test_project_cfg_locator_and_key_reader(tmp_path: pathlib.Path) -> None:
    assert project_cfg.project_cfg_file(tmp_path) is None
    legacy = _write(tmp_path / "PROJECT.cfg", "# legacy\n  branch_prefix = 'ai_'\ndev_tree_path=\"/x\"\n")
    assert project_cfg.project_cfg_file(tmp_path) == legacy
    assert project_cfg.read_cfg_key(legacy, "project", "branch_prefix") == "ai_"
    assert project_cfg.read_cfg_key(legacy, "project", "dev_tree_path") == "/x"
    assert project_cfg.read_cfg_key(legacy, "project", "absent") == ""
    ini_cfg = _write(tmp_path / "project.cfg", "[project]\nbranch_prefix = \"ai_\"  # quoted\n")
    assert project_cfg.project_cfg_file(tmp_path) == ini_cfg
    assert project_cfg.read_cfg_key(ini_cfg, "project", "branch_prefix") == '"ai_"'
    assert project_cfg.strip_one_quote_each_side('"ai_"', '"') == "ai_"


def test_stale_lock_reap_periods_reads_project_cfg_then_legacy_then_kanban_cfg(tmp_path: pathlib.Path) -> None:
    ctx = OpsContext(kanban_root=tmp_path)
    project = tmp_path / "projects" / "p"
    project.mkdir(parents=True)
    _write(tmp_path / "kanban.cfg", "[locks]\nstale_lock_reap_after_wake_periods = 7\n")
    assert ops_locks.stale_lock_reap_periods(ctx, "p") == 7
    _write(project / "PROJECT.cfg", "stale_lock_reap_after_wake_periods = 5\n")
    assert ops_locks.stale_lock_reap_periods(ctx, "p") == 5
    _write(project / "project.cfg", "[locks]\nstale_lock_reap_after_wake_periods = 4\n")
    assert ops_locks.stale_lock_reap_periods(ctx, "p") == 4


# ---------------------------------------------------------------------------
# 3. get_test_report
# ---------------------------------------------------------------------------


def test_version_key_reads_the_latest_verify_task(ctx: OpsContext, board: pathlib.Path) -> None:
    report = ops_read.get_test_report(ctx, _PROJECT, "v1.2.0")

    assert report.task == "TESTER-20260102-007-verify-1-2-0"
    assert report.text == "# Report 007\r\nCRLF\r\n"
    assert (report.notice, report.exit_code) == ("", 0)


def test_task_key_and_prefix_read_that_task_report(ctx: OpsContext) -> None:
    output = ops_read.render_test_report(ctx, _PROJECT, "TESTER-20260101-006")

    assert output == ops_read.ReadOutput(0, "# Report 006\n", "")


def test_ambiguous_prefix_reads_the_first_match_and_exits_two(ctx: OpsContext, board: pathlib.Path) -> None:
    tasks = _project_dir(board) / "tasks"

    output = ops_read.render_test_report(ctx, _PROJECT, "TESTER-20260104-010")

    assert (output.exit_code, output.stdout) == (2, "# Report 010-a\n")
    assert output.stderr == (
        f"resolve_item: ambiguous key 'TESTER-20260104-010' — 2 task directories match in tasks/; "
        f"resolving to first match: {tasks / 'TESTER-20260104-010-a'}\n"
        f"resolve_item:   {tasks / 'TESTER-20260104-010-a'}\n"
        f"resolve_item:   {tasks / 'TESTER-20260104-010-b'}\n"
        f"resolve_item: resolving to first match: {tasks / 'TESTER-20260104-010-a'}\n"
    )


def _refusal(output: ops_read.ReadOutput) -> tuple[int, str, str]:
    return output.exit_code, output.stdout, output.stderr


def test_test_report_refusals_keep_the_script_text_and_exit_codes(ctx: OpsContext, board: pathlib.Path) -> None:
    project = _project_dir(board)
    tasks = project / "tasks"
    missing_report = tasks / "TESTER-20260103-008-verify-1-3-0" / "artifacts" / "report.md"

    assert _refusal(ops_read.render_test_report(ctx, _PROJECT, "v9.9.9")) == (
        3, "", "show-test-report.sh: no TESTER task found for RC version: v9.9.9\n")
    assert _refusal(ops_read.render_test_report(ctx, _PROJECT, "v1.3.0")) == (
        3, "", f"show-test-report.sh: report not found for task TESTER-20260103-008-verify-1-3-0: {missing_report}\n")
    assert _refusal(ops_read.render_test_report(ctx, _PROJECT, "TESTER-20260103-008")) == (
        3, "", f"show-test-report.sh: report not found for task TESTER-20260103-008-verify-1-3-0: {missing_report}\n")
    assert _refusal(ops_read.render_test_report(ctx, _PROJECT, "CODER-20260101-002")) == (
        3, "", "show-test-report.sh: resolved task is not a TESTER task: CODER-20260101-002-blocked\n")
    assert _refusal(ops_read.render_test_report(ctx, _PROJECT, "BUG-0001")) == (
        3, "", "show-test-report.sh: key does not resolve to a task: BUG-0001\n")
    assert _refusal(ops_read.render_test_report(ctx, _PROJECT, "TESTER-99999999-999")) == (
        3, "",
        "resolve_item: item not found for key 'TESTER-99999999-999' (searched tasks/, bugs/, priority/, "
        f"requirements/ under {project})\nshow-test-report.sh: key not found: TESTER-99999999-999\n")
    assert _refusal(ops_read.render_test_report(ctx, "nosuch", "TESTER-20260101-006")) == (
        1, "",
        f"resolve_item: PROJECT_ROOT does not exist or is not a directory: {board / 'projects' / 'nosuch'}\n")
    assert _refusal(ops_read.render_test_report(ctx, "bare", "v1.2.0")) == (
        3, "", f"show-test-report.sh: tasks directory not found: {board / 'projects' / 'bare' / 'tasks'}\n")


# ---------------------------------------------------------------------------
# 4. get_task_readme
# ---------------------------------------------------------------------------


def test_task_readme_by_key_and_prefix(ctx: OpsContext, board: pathlib.Path) -> None:
    readme = _project_dir(board) / "tasks" / _WORKING / "README.md"

    exact = ops_read.get_task_readme(ctx, _PROJECT, _WORKING)
    prefix = ops_read.render_task_readme(ctx, _PROJECT, "CODER-20260101-001")

    assert (exact.path, exact.text, exact.notice) == (readme, readme.read_text(), "")
    assert prefix == ops_read.ReadOutput(0, readme.read_text(), "")


def test_task_readme_ambiguous_prefix_emits_the_first_match_with_exit_zero(ctx: OpsContext) -> None:
    output = ops_read.render_task_readme(ctx, _PROJECT, "TESTER-20260104-010")

    assert output.exit_code == 0
    assert output.stdout.startswith("# Task: TESTER-20260104-010-a\n")
    assert output.stderr.startswith("resolve_item: ambiguous key 'TESTER-20260104-010'")


def test_task_readme_refusals_keep_the_script_text_and_exit_codes(ctx: OpsContext, board: pathlib.Path) -> None:
    project = _project_dir(board)

    assert _refusal(ops_read.render_task_readme(ctx, _PROJECT, "BUG-0001")) == (
        1, "",
        "show-readme.sh: BUG-0001 is a bug item, not a task — READMEs are task artifacts.\n"
        "show.sh already prints BUG-0001's document: show.sh --project alpha --key BUG-0001\n")
    assert _refusal(ops_read.render_task_readme(ctx, _PROJECT, "CODER-20260101-009")) == (
        1, "",
        "show-readme.sh: README.md not found for task CODER-20260101-009: "
        f"{project / 'tasks' / 'CODER-20260101-009-noreadme' / 'README.md'}\n")
    assert _refusal(ops_read.render_task_readme(ctx, _PROJECT, "CODER-99999999-999")) == (
        3, "",
        "resolve_item: item not found for key 'CODER-99999999-999' (searched tasks/, bugs/, priority/, "
        f"requirements/ under {project})\nshow-readme.sh: key not found: CODER-99999999-999\n")
    assert ops_read.render_task_readme(ctx, "nosuch", _WORKING).exit_code == 1


# ---------------------------------------------------------------------------
# 5. list_rejected / render_rejected
# ---------------------------------------------------------------------------


@pytest.fixture
def quarantine(board: pathlib.Path) -> pathlib.Path:
    """Quarantined intake files on the board, every one at a fixed mtime."""
    project = _project_dir(board)
    _write(project / "rejected" / "one.md", "x", mtime=_WORKING_MTIME)
    _write(project / "rejected" / "PRIORITY-5-moved.md", "x", mtime=_WORKING_MTIME)
    _write(project / "rejected" / "PRIORITY-5-moved.md.reason",
           f"original_type=priority\noriginal_dir={project / 'priority'}\nreason=malformed filename\n")
    _write(project / "bugs" / ".rejected" / "BUG-12-bad.md", "x", mtime=_WORKING_MTIME)
    _write(project / "bugs" / ".rejected" / "BUG-0002-ok.md", "x", mtime=_WORKING_MTIME + 61)
    _write(project / "priority" / ".rejected" / "notes.txt", "x", mtime=_WORKING_MTIME)
    _write(project / "priority" / ".rejected" / ".hidden", "x", mtime=_WORKING_MTIME)
    (project / "priority" / ".rejected" / "link.md").symlink_to(project / "priority" / ".rejected" / "notes.txt")
    _write(board / "projects" / "bare" / "priority" / ".rejected" / "PRIORITY-0003-fine.md", "x",
           mtime=_WORKING_MTIME)
    _write(board / "projects" / ".hidden-project" / "bugs" / ".rejected" / "BUG-1.md", "x")
    return board


def _local_minute(epoch: int) -> str:
    return datetime.datetime.fromtimestamp(epoch).strftime("%Y-%m-%dT%H:%M")


def test_render_rejected_lists_every_project_in_order(ctx: OpsContext, quarantine: pathlib.Path) -> None:
    output = ops_read.render_rejected(ctx, environ=_PLAIN_ENV)
    at = _local_minute(_WORKING_MTIME)

    assert (output.exit_code, output.stderr) == (0, "")
    assert output.stdout == (
        "project=alpha dir=priority\n"
        f"  PRIORITY-5-moved.md  (quarantined {at})\n"
        "  Reason: filename does not match expected pattern "
        "(^PRIORITY-[0-9]{4}(?:-[0-9]{8})?-[a-z0-9][a-z0-9-]*\\.md$)\n"
        f"  notes.txt  (quarantined {at})\n"
        "  Reason: unknown prefix — not a BUG- or PRIORITY- file\n"
        "\n"
        "project=alpha dir=bugs\n"
        f"  BUG-0002-ok.md  (quarantined {_local_minute(_WORKING_MTIME + 61)})\n"
        "  Reason: pattern matches — unknown rejection reason (see logs)\n"
        f"  BUG-12-bad.md  (quarantined {at})\n"
        "  Reason: filename does not match expected pattern (^BUG-[0-9]{4}(?:-[0-9]{8})?-[a-z0-9][a-z0-9-]*\\.md$)\n"
        "\n"
        "project=alpha dir=rejected\n"
        f"  one.md  (quarantined {at})\n"
        "  Reason: unknown prefix — not a BUG- or PRIORITY- file\n"
        "\n"
        "project=bare dir=priority\n"
        f"  PRIORITY-0003-fine.md  (quarantined {at})\n"
        "  Reason: pattern matches — unknown rejection reason (see logs)\n"
        "\n"
        "To recover: scripts/recover-rejected.sh --project <name> --file <filename> [--rename NEW]\n"
    )


def test_render_rejected_one_project_and_colour_rule(ctx: OpsContext, quarantine: pathlib.Path) -> None:
    coloured = ops_read.render_rejected(ctx, "bare", environ={"TERM": "xterm", "NO_COLOR": "yes"}).stdout
    plain = ops_read.render_rejected(ctx, "bare", environ={"TERM": "xterm", "NO_COLOR": "1"}).stdout

    assert coloured.splitlines()[0] == "\033[1m\033[0;31mproject=bare dir=priority\033[0m"
    assert coloured.splitlines()[-1] == (
        "\033[2mTo recover: \033[1mscripts/recover-rejected.sh --project <name> --file <filename> "
        "[--rename NEW]\033[0m"
    )
    assert plain.splitlines()[0] == "project=bare dir=priority"


def test_render_rejected_with_nothing_quarantined(ctx: OpsContext, board: pathlib.Path) -> None:
    (_project_dir(board) / "rejected" / "one.md").unlink()
    assert ops_read.render_rejected(ctx, environ=_PLAIN_ENV) == ops_read.ReadOutput(
        0, "(no quarantined files found across all projects)\n", "")
    assert ops_read.render_rejected(ctx, "bare", environ=_COLOR_ENV).stdout == (
        "\033[2m(no quarantined files found across all projects)\033[0m\n")


def test_rejected_refusals(ctx: OpsContext, board: pathlib.Path, tmp_path: pathlib.Path) -> None:
    empty_root = tmp_path / "empty-root"
    empty_root.mkdir()

    assert _refusal(ops_read.render_rejected(ctx, "nosuch", environ=_PLAIN_ENV)) == (
        1, "", f"list-rejected.sh: error: project 'nosuch' not found under {board / 'projects'}\n")
    assert _refusal(ops_read.render_rejected(OpsContext(kanban_root=empty_root), environ=_PLAIN_ENV)) == (
        1, "",
        "ERROR: no project specified and none resolvable from projects.cfg\n"
        f"       projects/ directory not found under {empty_root}.\n"
        "       Create ${KANBAN_ROOT}/projects/<name>/ or migrate to the multi-project layout.\n")


@pytest.mark.parametrize(
    ("name", "reason"),
    [
        ("PRIORITY-0001-x.md", "pattern matches — unknown rejection reason (see logs)"),
        ("PRIORITY-77.md", "filename does not match expected pattern (^PRIORITY-[0-9]{4}(?:-[0-9]{8})?-[a-z0-9][a-z0-9-]*\\.md$)"),
        ("BUG-0042-y.md", "pattern matches — unknown rejection reason (see logs)"),
        ("BUG-42-y.md", "filename does not match expected pattern (^BUG-[0-9]{4}(?:-[0-9]{8})?-[a-z0-9][a-z0-9-]*\\.md$)"),
        ("notes.md", "unknown prefix — not a BUG- or PRIORITY- file"),
    ],
)
def test_rejection_reason(name: str, reason: str) -> None:
    assert rejection_reason(name) == reason


# ---------------------------------------------------------------------------
# 6. The read CLI
# ---------------------------------------------------------------------------


def _ops_cli(*args: str, env_extra: dict | None = None) -> subprocess.CompletedProcess:
    env = {"PATH": os.environ.get("PATH", "/usr/bin:/bin"), "PYTHONPATH": str(_TEAM_DIR), "TERM": "dumb"}
    env.update(env_extra or {})
    return subprocess.run([sys.executable, "-m", "pgai_agent_kanban.ops", *args], capture_output=True,
                          text=True, env=env, cwd=str(_TEAM_DIR), timeout=60, stdin=subprocess.DEVNULL)


def test_cli_verbs_print_the_render_result_and_exit_with_its_code(board: pathlib.Path) -> None:
    status = _ops_cli("get_status", str(board), _PROJECT, "1")
    report = _ops_cli("get_test_report", str(board), _PROJECT, "TESTER-20260104-010")
    readme = _ops_cli("get_task_readme", str(board), _PROJECT, "BUG-0001")
    rejected = _ops_cli("list_rejected", str(board))
    rejected_one = _ops_cli("list_rejected", str(board), "nosuch")
    no_project = _ops_cli("get_status", str(board), "", "0")

    assert status.returncode == 0 and status.stdout.startswith("pgai-kanban v9.1.0    RC: v1.2.0 (")
    assert (report.returncode, report.stdout) == (2, "# Report 010-a\n")
    assert report.stderr.startswith("resolve_item: ambiguous key 'TESTER-20260104-010'")
    assert (readme.returncode, readme.stdout) == (1, "")
    assert readme.stderr.startswith("show-readme.sh: BUG-0001 is a bug item, not a task")
    assert rejected.returncode == 0
    assert rejected.stdout.startswith(f"project={_PROJECT} dir=rejected\n  one.md  (quarantined ")
    assert (rejected_one.returncode, rejected_one.stdout) == (1, "")
    assert (no_project.returncode, no_project.stdout) == (1, "")
    assert no_project.stderr == "ERROR: no project specified; pass --project <name> or set PGAI_PROJECT_NAME\n"


@pytest.mark.parametrize(
    "argv",
    [
        ["get_status", "root", "p"],
        ["get_test_report", "root", "p"],
        ["get_task_readme", "root"],
        ["list_rejected"],
        ["list_rejected", "root", "p", "extra"],
    ],
)
def test_cli_wrong_argument_counts_exit_one(argv: list[str], capfd: pytest.CaptureFixture) -> None:
    assert ops_read._cli_main(argv) == 1
    assert f"read {argv[0]}: wrong number of arguments" in capfd.readouterr().err
