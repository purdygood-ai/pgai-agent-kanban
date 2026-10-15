"""
test_dashboard_board_snapshot.py — the shared per-tick board snapshot the
eight mixed-project board columns render from
(pgai_agent_kanban.dashboard.column_render.board_snapshot, driven by
team/scripts/lib/dashboard/column_render_board.sh from column-render.sh).

Coverage:
  1. Module: a column rendered from the snapshot is exactly the column
     gathered and rendered directly; a missing, malformed, other-format,
     other-colour or stale snapshot is a miss with its reason; each build
     increments the gather count.
  2. column-render.sh end to end on a fixture root:
       - rendering all eight columns gathers once (gathers=1) and each
         column's stdout matches the direct (pre-snapshot) render of it;
       - a stale, deleted or corrupt snapshot falls back to building a new
         one, with unchanged stdout;
       - a build lock held by a live process that never produces a
         snapshot makes the column render directly after its bounded wait,
         with unchanged stdout and no gather counted.
"""

from __future__ import annotations

import os
import pathlib
import sys
import time

import pytest

_TEAM_DIR = pathlib.Path(__file__).resolve().parents[2]
if str(_TEAM_DIR) not in sys.path:
    sys.path.insert(0, str(_TEAM_DIR))

from pgai_agent_kanban.dashboard.column_render import board_snapshot  # noqa: E402
from pgai_agent_kanban.dashboard.column_render.cells import Palette  # noqa: E402
from pgai_agent_kanban.dashboard.column_render.multi import (  # noqa: E402
    ProjectSource,
    render_multi_project_column,
)
from tests.fixtures.dashboard_render_fixture import (  # noqa: E402
    AMBIENT_LEAK_ENV_VARS_TO_CLEAR,
    run_dashboard_script,
)
from tests.fixtures.golden_board import PROJECT_NAME, build_synthetic_board  # noqa: E402

_PKG_DIR = _TEAM_DIR / "pgai_agent_kanban"
_LOCK_PROVENANCE = _TEAM_DIR / "scripts" / "lib" / "lock_provenance.sh"

#: (column-render.sh positional args, --label) for every board column, as create.sh sends them.
_COLUMNS = [
    (["input", "none", "none", "20", "29"], "BUGS"),
    (["input", "none", "none", "20", "29"], "PRIORITIES"),
    (["input", "none", "none", "20", "29"], "REQUIREMENTS"),
    (["queue", "none", "20", "26"], "PM"),
    (["queue", "none", "20", "26"], "CODER"),
    (["queue", "none", "20", "26"], "WRITER"),
    (["queue", "none", "20", "26"], "TESTER"),
    (["queue", "none", "20", "26"], "CM"),
]


def _write(path: pathlib.Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


@pytest.fixture
def board_root(tmp_path: pathlib.Path) -> pathlib.Path:
    """The golden board widened with items in every board column."""
    root = tmp_path / "board"
    project = build_synthetic_board(root, with_queues=True)
    (root / "pgai_agent_kanban").symlink_to(_PKG_DIR, target_is_directory=True)
    for number, status in ((2, "wont-do"), (3, "done"), (4, "running"), (5, "blocked")):
        _write(project / "bugs" / f"BUG-000{number}-snap-{status}.md",
               f"# BUG-000{number}\n\n**Severity:** high\n\n## Status\n{status}\n")
    _write(project / "priority" / "PRIORITY-0001-snap.md", "# P\n\n## Status\nopen\n")
    for version in ("v1.0.0", "v1.2.0", "v1.3.0"):
        _write(project / "requirements" / f"{version}-snap.md", f"# {version}\n\n## Target Version\n{version}\n")
    _write(project / "release-state.md",
           "# Release State\n\n## Active RC\nv1.2.0\n\n## RC Opened At\n2026-01-01T00:00:00Z\n\n"
           "## RC Opened By Task\nsnap-fixture\n\n## Last Released\nv1.1.0\n")
    for agent in ("pm", "writer", "tester", "cm"):
        task = f"{agent.upper()}-20260102-001-snap-task"
        _write(project / "tasks" / task / "status.md", "## State\nWORKING\n")
        _write(project / "tasks" / "queues" / f"{agent}_backlog.md", f"- [A] {task} — snap\n")
    return root


def _env(root: pathlib.Path, scratch: pathlib.Path, ttl: int = 60) -> dict:
    env = dict(os.environ)
    for key in AMBIENT_LEAK_ENV_VARS_TO_CLEAR:
        env.pop(key, None)
    env.pop("NO_COLOR", None)
    env.pop("DASHBOARD_MAX_ROWS", None)
    env.update({
        "PGAI_AGENT_KANBAN_ROOT_PATH": str(root),
        "KANBAN_ROOT": str(root),
        "PGAI_AGENT_KANBAN_TEMP_DIR": str(scratch),
        "PGAI_DASHBOARD_SNAPSHOT_TTL_SECONDS": str(ttl),
        "TERM": "xterm-256color",
    })
    return env


def _render(root: pathlib.Path, env: dict, args: list, label: str):
    return run_dashboard_script(
        ["bash", str(root / "scripts" / "dashboard" / "column-render.sh"), *args,
         "--label", label, "--kanban-root", str(root), "--all-projects"],
        env=env, timeout=60,
    )


def _snapshot_files(scratch: pathlib.Path) -> list:
    return sorted((scratch / "dashboard" / "snapshots").glob("board-*.json"))


def _gathers(scratch: pathlib.Path) -> int:
    (snap,) = _snapshot_files(scratch)
    return int(board_snapshot.read_stats(snap).get("gathers", "0"))


def _hold_build_lock_without_building(root: pathlib.Path, env: dict, snap: pathlib.Path) -> None:
    """Write a build-lock provenance record naming this (live) test process."""
    lock = pathlib.Path(f"{snap}.lock")
    lock.write_text(
        f"holder=dashboard\npid={os.getpid()}\nop=board-snapshot-build\ntask=none\n"
        "acquired_at=2026-01-01T00:00:00Z\n",
        encoding="utf-8",
    )


# ---------------------------------------------------------------------------
# 1. Module
# ---------------------------------------------------------------------------


def _sources(root: pathlib.Path) -> dict:
    project = root / "projects" / PROJECT_NAME
    by_column = {}
    for subcommand, column_type in board_snapshot.BOARD_COLUMNS:
        if subcommand == "input":
            dirname = {"bugs": "bugs", "priorities": "priority", "requirements": "requirements"}[column_type]
            source = ProjectSource(PROJECT_NAME, "#378ADD", max_rows=20, input_dir=str(project / dirname),
                                   active_rc="v1.2.0", last_released="v1.1.0")
        else:
            source = ProjectSource(PROJECT_NAME, "#378ADD", max_rows=20, tasks_dir=str(project / "tasks"),
                                   backlog_file=str(project / "tasks" / "queues" / f"{column_type}_backlog.md"))
        by_column[column_type] = [source, ProjectSource("empty-proj", "#888780")]
    return by_column


@pytest.mark.parametrize("use_color", [True, False])
def test_every_column_from_the_snapshot_matches_its_direct_render(board_root: pathlib.Path, use_color: bool) -> None:
    sources = _sources(board_root)
    palette = Palette(use_color, *(("\x1b[0;31m", "\x1b[0;33m", "\x1b[0;32m", "\x1b[0;37m", "\x1b[2m", "\x1b[0m")
                                    if use_color else ("",) * 6))
    snapshot = board_snapshot.build_snapshot(sources, use_color=use_color)
    for subcommand, column_type in board_snapshot.BOARD_COLUMNS:
        direct = render_multi_project_column(
            sources[column_type], subcommand=subcommand, column_type=column_type, width=30, palette=palette,
        )
        from_snapshot = board_snapshot.render_column(
            snapshot, subcommand=subcommand, column_type=column_type, width=30, palette=palette,
        )
        assert from_snapshot == direct, column_type


def test_snapshot_round_trips_and_each_miss_names_its_reason(tmp_path: pathlib.Path, board_root: pathlib.Path) -> None:
    path = tmp_path / "board.json"
    assert board_snapshot.read_snapshot(path, ttl_seconds=5, use_color=True) == (None, board_snapshot.MISS_MISSING)

    snapshot = board_snapshot.build_snapshot(_sources(board_root), use_color=True)
    board_snapshot.write_snapshot(path, snapshot)
    read, reason = board_snapshot.read_snapshot(path, ttl_seconds=5, use_color=True)
    assert reason is None
    assert read["columns"]["coder"]["subcommand"] == "queue"

    assert board_snapshot.read_snapshot(path, ttl_seconds=5, use_color=False) == (None, board_snapshot.MISS_MISSING)
    old = time.time() - 10
    os.utime(path, (old, old))
    assert board_snapshot.read_snapshot(path, ttl_seconds=5, use_color=True) == (None, board_snapshot.MISS_STALE)

    path.write_text("{not json", encoding="utf-8")
    assert board_snapshot.read_snapshot(path, ttl_seconds=5, use_color=True) == (None, board_snapshot.MISS_MISSING)
    path.write_text('{"format": 999, "use_color": true, "columns": {}}', encoding="utf-8")
    assert board_snapshot.read_snapshot(path, ttl_seconds=5, use_color=True) == (None, board_snapshot.MISS_MISSING)


def test_each_build_increments_the_gather_count(tmp_path: pathlib.Path) -> None:
    path = tmp_path / "board.json"
    assert board_snapshot.read_stats(path) == {}
    assert board_snapshot.record_gather(path) == 1
    assert board_snapshot.record_gather(path) == 2
    stats = board_snapshot.read_stats(path)
    assert stats["gathers"] == "2"
    assert stats["last"].endswith("Z")


# ---------------------------------------------------------------------------
# 2. column-render.sh end to end
# ---------------------------------------------------------------------------


def _direct_outputs(board_root: pathlib.Path, tmp_path: pathlib.Path) -> dict:
    """Each column rendered directly: the build lock looks held by a live
    process that never writes a snapshot, so every column falls back."""
    scratch = tmp_path / "direct"
    env = _env(board_root, scratch, ttl=1)
    first = _render(board_root, env, *_COLUMNS[0])  # creates the snapshot dir and file
    assert first.returncode == 0, first.stderr
    (snap,) = _snapshot_files(scratch)
    snap.unlink()
    _hold_build_lock_without_building(board_root, env, snap)
    outputs = {}
    for args, label in _COLUMNS:
        result = _render(board_root, env, args, label)
        assert result.returncode == 0, result.stderr
        assert "rendering directly" in result.stderr
        outputs[label] = result.stdout
    assert not snap.exists(), "a column that rendered directly must not have built a snapshot"
    return outputs


def test_all_eight_columns_gather_once_and_match_their_direct_render(
    board_root: pathlib.Path, tmp_path: pathlib.Path,
) -> None:
    scratch = tmp_path / "shared"
    env = _env(board_root, scratch)
    outputs = {}
    for args, label in _COLUMNS:
        result = _render(board_root, env, args, label)
        assert result.returncode == 0, result.stderr
        outputs[label] = result.stdout
    assert _gathers(scratch) == 1
    assert outputs == _direct_outputs(board_root, tmp_path)
    assert all(out.strip() and out.strip() != "(empty)" for out in outputs.values()), outputs


@pytest.mark.parametrize("damage", ["stale", "deleted", "corrupt"])
def test_an_unusable_snapshot_is_rebuilt_with_unchanged_output(
    board_root: pathlib.Path, tmp_path: pathlib.Path, damage: str,
) -> None:
    scratch = tmp_path / "shared"
    env = _env(board_root, scratch)
    args, label = _COLUMNS[4]
    before = _render(board_root, env, args, label)
    assert before.returncode == 0, before.stderr
    (snap,) = _snapshot_files(scratch)
    if damage == "stale":
        old = time.time() - 120
        os.utime(snap, (old, old))
    elif damage == "deleted":
        snap.unlink()
    else:
        snap.write_text("{truncated", encoding="utf-8")

    after = _render(board_root, env, args, label)

    assert after.returncode == 0, after.stderr
    assert after.stdout == before.stdout
    assert _gathers(scratch) == 2
    assert "rendering directly" in after.stderr
    hit = _render(board_root, env, args, label)
    assert hit.stdout == before.stdout and "rendering directly" not in hit.stderr
    assert _gathers(scratch) == 2
