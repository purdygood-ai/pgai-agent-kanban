"""
test_dashboard_board_prerender.py — one board tick built in one Python
process and served pre-rendered
(pgai_agent_kanban.dashboard.column_render.board_gather, board_snapshot's
pre-render helpers, and team/scripts/lib/dashboard/column_render_board_serve.sh
driven from column-render.sh).

Coverage:
  1. The in-process gather gives every board column exactly the per-project
     sources the bash gather stage (column_render_gather_board_multi)
     exports, on a registry with explicit and palette colours, clamped and
     invalid row caps, a missing priority, release-state and git-tag last
     releases, a malformed active RC, and a deferral posture that is on
     (with deferred bugs), off and malformed -- with DASHBOARD_MAX_ROWS
     unset and set.
  2. A registry the gather does not read (colon-legacy) is reported, and
     board-build --gather=tree then prints nothing, writes no snapshot and
     exits 5 so the caller gathers through bash.
  3. --board-widths parsing, and the pre-render file name the bash reader
     and the Python writer agree on.
  4. column-render.sh end to end with --board-widths, in both colour modes:
     a tick of all eight columns gathers once, starts Python once (the
     builder), and every column matches its direct render.
  5. A pre-render is never served without its snapshot: a stale, deleted or
     replaced snapshot is rebuilt with unchanged output.
  6. A width the panes did not declare renders from the fresh snapshot
     without a second gather.
  7. A deferral posture that refuses to resolve: the in-process gather
     gives the bugs column the same malformed line, byte for byte and
     naming the same file, that column_render_defer_multi_line gives the
     bash full path -- for a project campaign file, a root campaign file, a
     project.cfg value, and a refusal that names no file (both sides then
     name the kanban root's campaign file) -- and a board under a malformed
     campaign serves a pre-rendered bugs column identical to its direct
     render in both colour modes.

Every fixture lives under pytest's tmp_path; no live kanban root is read or
written.
"""

from __future__ import annotations

import os
import pathlib
import subprocess
import sys
import time

import pytest

_TEAM_DIR = pathlib.Path(__file__).resolve().parents[2]
if str(_TEAM_DIR) not in sys.path:
    sys.path.insert(0, str(_TEAM_DIR))

from pgai_agent_kanban.dashboard.column_render import board_gather, board_snapshot  # noqa: E402
from tests.fixtures.dashboard_render_fixture import (  # noqa: E402
    AMBIENT_LEAK_ENV_VARS_TO_CLEAR,
    run_dashboard_script,
)
from pgai_agent_kanban.lib import defer_posture  # noqa: E402
from tests.fixtures.golden_board import PROJECT_NAME, build_synthetic_board  # noqa: E402

_PKG_DIR = _TEAM_DIR / "pgai_agent_kanban"
_SCRIPTS = _TEAM_DIR / "scripts"
_BOARD_WIDTHS = "input=29,queue=26"

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


def _git(repo: pathlib.Path, *args: str) -> None:
    subprocess.run(
        ["git", "-C", str(repo), *args], check=True, capture_output=True, text=True,
        env={**os.environ, "GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@example.com",
             "GIT_COMMITTER_NAME": "t", "GIT_COMMITTER_EMAIL": "t@example.com"},
    )


# ---------------------------------------------------------------------------
# 1 & 2. The in-process gather against the bash gather
# ---------------------------------------------------------------------------


@pytest.fixture
def registry_root(tmp_path: pathlib.Path) -> pathlib.Path:
    """Four projects covering every per-project answer the gather gives."""
    root = tmp_path / "registry"
    _write(root / "kanban.cfg", "[dashboard]\nmax_rows = 12\n")
    _write(root / "projects.cfg", (
        "[project:alpha]\npriority=2\ndashboard_color=#112233\ndashboard_max_rows=3\n\n"
        "[project:bravo]\npriority=1\ndashboard_max_rows=200\n\n"
        "[project:charlie]\npriority=2\ndashboard_max_rows=abc\n\n"
        "[project:delta]\nenabled=true\n"
    ))
    projects = root / "projects"
    # alpha: no project.cfg, so its last release comes from release-state.md.
    _write(projects / "alpha" / "release-state.md",
           "# Release State\n\n## Active RC\nv1.3.0\n\n## RC Opened At\nnone\n\n"
           "## RC Opened By Task\nnone\n\n## Last Released\nv1.2.0\n")
    # bravo: a git dev tree whose merged tags give the last release.
    repo = tmp_path / "bravo-dev"
    repo.mkdir()
    _git(repo, "init", "-q", "-b", "main")
    _write(repo / "README.md", "x\n")
    _git(repo, "add", "README.md")
    _git(repo, "commit", "-q", "-m", "init")
    for tag in ("v0.9.0", "v0.10.0"):
        _git(repo, "tag", tag)
    _write(projects / "bravo" / "project.cfg", f"[project]\nproject_name = bravo\ndev_tree_path = {repo}\n")
    _write(projects / "bravo" / "release-state.md", "# Release State\n\n## Active RC\nnone\n")
    # charlie: a malformed Active RC, and a deferral campaign in force.
    _write(projects / "charlie" / "release-state.md", "# Release State\n\n## Active RC\nsoon\n")
    _write(projects / "charlie" / "DEFER-LOW-BUGS", "until=v9.9.9\n")
    for number, severity, status in ((1, "low", "open"), (2, "high", "open"), (3, "low", "done")):
        _write(projects / "charlie" / "bugs" / f"BUG-00{number}-x.md",
               f"# BUG\n\n**Severity:** {severity}\n\n## Status\n{status}\n")
    # delta: a campaign file with no until= line (malformed posture).
    _write(projects / "delta" / "DEFER-LOW-BUGS", "# no target\n")
    (projects / "delta" / "bugs").mkdir(parents=True)
    return root


def _bash_gather_sources(root: pathlib.Path, tmp_path: pathlib.Path, max_rows: str) -> str:
    """Run column_render_gather_board_multi and print every column's sources."""
    lib = _SCRIPTS / "lib"
    script = f"""set -euo pipefail
source '{lib}/project_paths.sh'; source '{lib}/projects.sh'; source '{lib}/ini_parser.sh'
source '{lib}/cm/common.sh'; source '{lib}/status_glyphs.sh'
source '{_SCRIPTS}/dashboard/lib/defer_scope.sh'; source '{lib}/dashboard/column_render_defer.sh'
source '{lib}/dashboard/column_render_data_multi.sh'
POSARGS=(input none none 20 29); SUBCOMMAND=input; COLUMN_TYPE=bugs; DASHBOARD_ROWS_PER_COLUMN=20
column_render_gather_board_multi
'{sys.executable}' - <<'PY'
import os
from pgai_agent_kanban.dashboard.column_render.board_snapshot import BOARD_COLUMNS, column_env_suffix
from pgai_agent_kanban.dashboard.column_render.multi import sources_from_env
for _s, column in BOARD_COLUMNS:
    for source in sources_from_env(os.environ, column_env_suffix(column)):
        print(column, repr(source))
PY
"""
    env = {key: value for key, value in os.environ.items() if key not in AMBIENT_LEAK_ENV_VARS_TO_CLEAR}
    env.pop("DASHBOARD_MAX_ROWS", None)
    env.update({"KANBAN_ROOT": str(root), "PGAI_AGENT_KANBAN_ROOT_PATH": str(root),
                "PGAI_AGENT_KANBAN_TEMP_DIR": str(tmp_path / "scratch"), "PGAI_GIT_LOCAL_ONLY": "1",
                "PYTHONPATH": str(_TEAM_DIR)})
    if max_rows:
        env["DASHBOARD_MAX_ROWS"] = max_rows
    result = subprocess.run(["bash", "-c", script], capture_output=True, text=True, env=env,
                            timeout=120, cwd=str(_TEAM_DIR))
    assert result.returncode == 0, result.stderr
    return result.stdout


@pytest.mark.parametrize("max_rows", ["", "15"])
def test_in_process_gather_matches_the_bash_gather(
    registry_root: pathlib.Path, tmp_path: pathlib.Path, max_rows: str,
) -> None:
    bash_out = _bash_gather_sources(registry_root, tmp_path, max_rows)
    glyphs = {"GLYPH_DEFERRED_BUG": "✋", "GLYPH_DEFER_WARNING": "⚠️"}

    sources = board_gather.gather_board_sources(registry_root, dashboard_max_rows=max_rows, environ=glyphs)

    python_out = "".join(
        f"{column} {source!r}\n" for _s, column in board_snapshot.BOARD_COLUMNS for source in sources[column]
    )
    assert python_out == bash_out
    by_name = {s.name: s for s in sources["requirements"]}
    assert [s.name for s in sources["bugs"]] == ["bravo", "alpha", "charlie", "delta"]
    assert (by_name["alpha"].active_rc, by_name["alpha"].last_released) == ("v1.3.0", "v1.2.0")
    assert by_name["bravo"].last_released == "v0.10.0"
    bugs = {s.name: s.defer for s in sources["bugs"]}
    assert (bugs["charlie"].mode, bugs["charlie"].ids) == ("on", frozenset({"BUG-001"}))
    assert bugs["delta"].mode == "malformed"


def test_a_colon_legacy_registry_is_handed_back_to_the_bash_gather(tmp_path: pathlib.Path) -> None:
    root = tmp_path / "legacy"
    _write(root / "projects.cfg", "alpha:1\n")
    (root / "projects" / "alpha").mkdir(parents=True)
    with pytest.raises(board_gather.BoardGatherUnavailable):
        board_gather.gather_board_env(root)

    snapshot = tmp_path / "board-x.json"
    result = subprocess.run(
        [sys.executable, "-m", "pgai_agent_kanban.dashboard.column_render", "board-build",
         f"--snapshot={snapshot}", "--gather=tree", f"--kanban-root={root}",
         "--subcommand=input", "--column-type=bugs", "--width=29"],
        capture_output=True, text=True, cwd=str(_TEAM_DIR), timeout=60, check=False,
    )
    assert result.returncode == 5, result.stderr
    assert result.stdout == ""
    assert "gathering through bash" in result.stderr
    assert not snapshot.exists()


# ---------------------------------------------------------------------------
# 3. --board-widths and the pre-render name
# ---------------------------------------------------------------------------


def test_board_widths_set_each_kind_and_a_column_entry_wins() -> None:
    widths = board_snapshot.parse_board_widths(" input=29 , queue=26, cm=30, bogus=4, pm=x, coder=0 ")
    assert widths == {"bugs": 29, "priorities": 29, "requirements": 29, "pm": 26,
                      "coder": 26, "writer": 26, "tester": 26, "cm": 30}
    assert board_snapshot.parse_board_widths("") == {}


def test_bash_reader_and_python_writer_agree_on_the_pre_render_name(tmp_path: pathlib.Path) -> None:
    snapshot = tmp_path / "board-abc123.json"
    result = subprocess.run(
        ["bash", "-c", f"source '{_SCRIPTS}/lib/dashboard/column_render_board.sh'; "
                       f"column_render_board_prerender_path '{snapshot}' coder 26"],
        capture_output=True, text=True, timeout=30, check=False,
    )
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == str(board_snapshot.prerender_path(snapshot, "coder", 26))
    assert result.stdout.strip() == str(tmp_path / "board-abc123.coder.w26.txt")


# ---------------------------------------------------------------------------
# 4-6. column-render.sh end to end
# ---------------------------------------------------------------------------


@pytest.fixture
def board_root(tmp_path: pathlib.Path) -> pathlib.Path:
    """The golden board (with its deferral campaign) widened with items in every column."""
    root = tmp_path / "board"
    project = build_synthetic_board(root, with_queues=True)
    (root / "pgai_agent_kanban").symlink_to(_PKG_DIR, target_is_directory=True)
    for number, status in ((2, "wont-do"), (3, "done"), (4, "running"), (5, "blocked")):
        _write(project / "bugs" / f"BUG-000{number}-pre-{status}.md",
               f"# BUG-000{number}\n\n**Severity:** high\n\n## Status\n{status}\n")
    _write(project / "priority" / "PRIORITY-0001-pre.md", "# P\n\n## Status\nopen\n")
    for version in ("v1.0.0", "v1.2.0", "v1.3.0"):
        _write(project / "requirements" / f"{version}-pre.md", f"# {version}\n\n## Target Version\n{version}\n")
    _write(project / "release-state.md",
           "# Release State\n\n## Active RC\nv1.2.0\n\n## RC Opened At\n2026-01-01T00:00:00Z\n\n"
           "## RC Opened By Task\npre-fixture\n\n## Last Released\nv1.1.0\n")
    for agent in ("pm", "writer", "tester", "cm"):
        task = f"{agent.upper()}-20260102-001-pre-task"
        _write(project / "tasks" / task / "status.md", "## State\nWORKING\n")
        _write(project / "tasks" / "queues" / f"{agent}_backlog.md", f"- [A] {task} — pre\n")
    return root


def _python_spy(tmp_path: pathlib.Path) -> tuple:
    """A directory holding a python3 that logs each start, then runs the real one."""
    spy_dir = tmp_path / "spy"
    spy_dir.mkdir(exist_ok=True)
    log = tmp_path / "python-starts.log"
    spy = spy_dir / "python3"
    spy.write_text(f"#!/usr/bin/env bash\necho start >> '{log}'\nexec '{sys.executable}' \"$@\"\n",
                   encoding="utf-8")
    spy.chmod(0o755)
    return spy_dir, log


def _env(root: pathlib.Path, scratch: pathlib.Path, *, color: bool, ttl: int = 60, path_prefix: str = "") -> dict:
    env = dict(os.environ)
    for key in AMBIENT_LEAK_ENV_VARS_TO_CLEAR:
        env.pop(key, None)
    for key in ("NO_COLOR", "DASHBOARD_MAX_ROWS", "DASHBOARD_STATUS_GLYPH"):
        env.pop(key, None)
    env.update({
        "PGAI_AGENT_KANBAN_ROOT_PATH": str(root),
        "KANBAN_ROOT": str(root),
        "PGAI_AGENT_KANBAN_TEMP_DIR": str(scratch),
        "PGAI_DASHBOARD_SNAPSHOT_TTL_SECONDS": str(ttl),
        "TERM": "xterm-256color",
    })
    if not color:
        env["NO_COLOR"] = "1"
    if path_prefix:
        env["PATH"] = f"{path_prefix}:{env.get('PATH', '')}"
    return env


def _render(root: pathlib.Path, env: dict, args: list, label: str, *, widths: bool = True):
    extra = ["--board-widths", _BOARD_WIDTHS] if widths else []
    return run_dashboard_script(
        ["bash", str(root / "scripts" / "dashboard" / "column-render.sh"), *args,
         "--label", label, "--kanban-root", str(root), "--all-projects", *extra],
        env=env, timeout=60,
    )


def _snapshot(scratch: pathlib.Path) -> pathlib.Path:
    (snap,) = sorted((scratch / "dashboard" / "snapshots").glob("board-*.json"))
    return snap


def _gathers(scratch: pathlib.Path) -> int:
    return int(board_snapshot.read_stats(_snapshot(scratch)).get("gathers", "0"))


def _direct_outputs(root: pathlib.Path, tmp_path: pathlib.Path, *, color: bool, columns=_COLUMNS) -> dict:
    """Each column rendered directly: the build lock is held by this live
    test process, which never builds, so every column renders on its own."""
    scratch = tmp_path / f"direct-{color}"
    env = _env(root, scratch, color=color, ttl=1)
    first = _render(root, env, *_COLUMNS[0])
    assert first.returncode == 0, first.stderr
    snap = _snapshot(scratch)
    snap.unlink()
    pathlib.Path(f"{snap}.lock").write_text(
        f"holder=dashboard\npid={os.getpid()}\nop=board-snapshot-build\ntask=none\n"
        "acquired_at=2026-01-01T00:00:00Z\n", encoding="utf-8",
    )
    outputs = {}
    for args, label in columns:
        result = _render(root, env, args, label)
        assert result.returncode == 0, result.stderr
        assert "rendering directly" in result.stderr
        outputs[label] = result.stdout
    assert not snap.exists()
    return outputs


@pytest.mark.parametrize("color", [True, False], ids=["color", "no-color"])
def test_a_declared_tick_gathers_once_starts_python_once_and_matches_direct_renders(
    board_root: pathlib.Path, tmp_path: pathlib.Path, color: bool,
) -> None:
    scratch = tmp_path / "shared"
    spy_dir, log = _python_spy(tmp_path)
    env = _env(board_root, scratch, color=color, path_prefix=str(spy_dir))
    outputs = {}
    for args, label in _COLUMNS:
        result = _render(board_root, env, args, label)
        assert result.returncode == 0, result.stderr
        assert "in-process board gather unavailable" not in result.stderr
        outputs[label] = result.stdout

    assert _gathers(scratch) == 1
    assert log.read_text(encoding="utf-8").splitlines() == ["start"], "only the builder may start Python"
    assert outputs == _direct_outputs(board_root, tmp_path, color=color)
    assert all(out.startswith(f"=== {label} ===\n") for (_a, label), out in zip(_COLUMNS, outputs.values()))


@pytest.mark.parametrize("damage", ["stale", "deleted", "replaced"])
def test_a_pre_render_is_never_served_without_its_snapshot(
    board_root: pathlib.Path, tmp_path: pathlib.Path, damage: str,
) -> None:
    scratch = tmp_path / "shared"
    env = _env(board_root, scratch, color=True)
    args, label = _COLUMNS[4]
    before = _render(board_root, env, args, label)
    assert before.returncode == 0, before.stderr
    snap = _snapshot(scratch)
    prerender = board_snapshot.prerender_path(snap, "coder", 26)
    assert prerender.is_file()
    if damage == "stale":
        old = time.time() - 120
        os.utime(snap, (old, old))
    elif damage == "deleted":
        snap.unlink()
    else:
        time.sleep(0.05)
        snap.write_text("{truncated", encoding="utf-8")

    after = _render(board_root, env, args, label)

    assert after.returncode == 0, after.stderr
    assert after.stdout == before.stdout
    assert "rendering directly" in after.stderr
    assert _gathers(scratch) == 2
    hit = _render(board_root, env, args, label)
    assert hit.stdout == before.stdout and "rendering directly" not in hit.stderr
    assert _gathers(scratch) == 2


def test_an_undeclared_width_renders_from_the_snapshot_without_regathering(
    board_root: pathlib.Path, tmp_path: pathlib.Path,
) -> None:
    scratch = tmp_path / "shared"
    env = _env(board_root, scratch, color=True)
    build = _render(board_root, env, *_COLUMNS[0])
    assert build.returncode == 0, build.stderr
    wide = (["queue", "none", "20", "40"], "CODER")

    result = _render(board_root, env, *wide)

    assert result.returncode == 0, result.stderr
    assert _gathers(scratch) == 1
    assert "rendering directly" not in result.stderr
    assert result.stdout == _direct_outputs(board_root, tmp_path, color=True, columns=[wide])["CODER"]


# ---------------------------------------------------------------------------
# 7. A malformed deferral posture
# ---------------------------------------------------------------------------

#: Each way the posture can refuse, as (fixture writer, file the refusal names).
_MALFORMED_POSTURES = {
    "project-campaign-file": (
        lambda root: _write(defer_posture.campaign_file_path(root / "projects" / "solo"), "# no target\n"),
        lambda root: defer_posture.campaign_file_path(root / "projects" / "solo"),
    ),
    "root-campaign-file": (
        lambda root: _write(defer_posture.campaign_file_path(root), "until=soon\n"),
        lambda root: defer_posture.campaign_file_path(root),
    ),
    "project-cfg-value": (
        lambda root: _write(root / "projects" / "solo" / "project.cfg", "[release]\ndeferred_bugs = on\n"),
        lambda root: root / "projects" / "solo" / "project.cfg",
    ),
    "refusal-naming-no-file": (
        lambda root: None,
        lambda root: defer_posture.campaign_file_path(root),
    ),
}


def _bash_defer_line(root: pathlib.Path, tmp_path: pathlib.Path, bugs_dir: pathlib.Path, *, stub: str) -> str:
    """The line column_render_defer_multi_line adds for project "solo" on the bash full path."""
    script = f"""set -euo pipefail
source '{_SCRIPTS}/dashboard/lib/defer_scope.sh'; source '{_SCRIPTS}/lib/dashboard/column_render_defer.sh'
{stub}
column_render_defer_multi_line "$1" solo "$2"
"""
    env = {key: value for key, value in os.environ.items() if key not in AMBIENT_LEAK_ENV_VARS_TO_CLEAR}
    env.update({"KANBAN_ROOT": str(root), "PGAI_AGENT_KANBAN_ROOT_PATH": str(root),
                "PGAI_AGENT_KANBAN_TEMP_DIR": str(tmp_path / "scratch"), "PYTHONPATH": str(_TEAM_DIR)})
    result = subprocess.run(["bash", "-c", script, "bash", str(root), str(bugs_dir)], capture_output=True,
                            text=True, env=env, timeout=60, cwd=str(_TEAM_DIR))
    assert result.returncode == 0, result.stderr
    return result.stdout


@pytest.mark.parametrize("fault", sorted(_MALFORMED_POSTURES))
def test_a_malformed_posture_gathers_the_line_the_bash_full_path_builds(
    tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch, fault: str,
) -> None:
    root = tmp_path / "registry"
    _write(root / "projects.cfg", "[project:solo]\nenabled=true\n")
    bugs_dir = root / "projects" / "solo" / "bugs"
    _write(bugs_dir / "BUG-001-x.md", "# BUG\n\n**Severity:** low\n\n## Status\nopen\n")
    write_fault, named_file = _MALFORMED_POSTURES[fault]
    write_fault(root)
    stub = ""
    if fault == "refusal-naming-no-file":
        # Both resolvers refuse without naming the file at fault.
        stub = 'resolve_defer_posture() { DEFER_POSTURE_LAST_ERROR_FILE=""; return 1; }'

        def _refuse(_kanban_root, _project=None):
            raise defer_posture.DeferPostureError("defer_posture.test", "refused", "none", "")

        monkeypatch.setattr(defer_posture, "resolve_defer_posture", _refuse)

    bash_line = _bash_defer_line(root, tmp_path, bugs_dir, stub=stub)
    env = board_gather.gather_board_env(root)

    gathered = env["_PROJ_DEFER_INFO_STR" + board_snapshot.column_env_suffix("bugs")]
    assert f"{gathered}\n" == bash_line
    assert gathered == f"malformed\x1f{named_file(root)}\x1f"


@pytest.mark.parametrize("color", [True, False], ids=["color", "no-color"])
def test_a_malformed_campaign_serves_a_bugs_pre_render_identical_to_its_direct_render(
    board_root: pathlib.Path, tmp_path: pathlib.Path, color: bool,
) -> None:
    project = board_root / "projects" / PROJECT_NAME
    _write(defer_posture.campaign_file_path(project), "# no target\n")
    scratch = tmp_path / "shared"
    env = _env(board_root, scratch, color=color)
    build = _render(board_root, env, *_COLUMNS[1])
    assert build.returncode == 0, build.stderr
    assert "in-process board gather unavailable" not in build.stderr

    served = _render(board_root, env, *_COLUMNS[0])

    assert served.returncode == 0, served.stderr
    assert "rendering directly" not in served.stderr
    assert _gathers(scratch) == 1
    assert board_snapshot.prerender_path(_snapshot(scratch), "bugs", 29).is_file()
    assert served.stdout == _direct_outputs(board_root, tmp_path, color=color, columns=[_COLUMNS[0]])["BUGS"]
    assert ("⚠️" in served.stdout) is color, "an open bug under a malformed posture carries the warning glyph"
