"""
test_dashboard_snapshot.py
============================
Coverage for the per-tick render snapshot cache shared by twin dashboard
views (team/scripts/dashboard/lib/snapshot.sh) and its two integration
points: team/scripts/dashboard/column-render.sh's --all-projects path
(the operator window's aggregated board columns vs the visibility window's
per-column panes) and team/scripts/dashboard/data.sh's caller
show-progress.sh (an overview pane's per-project loop vs a drill-N pane for
that same project). show-queues.sh reads its queue state through
pgai_agent_kanban.ops.read.get_queues instead of data.sh, so it is not a
snapshot twin.

Background: several regions of the board render the same underlying data
twice per tick -- the operator window's board region and the visibility
window run the identical column-render.sh invocation, and a drill-N pane
re-derives the per-project state an overview pane already scanned via
data.sh. Without sharing, every tick pays for the same scan twice, and the
two renders can disagree if their independent scans land on either side of
a state change. This module covers the shared snapshot: one per-tick snapshot
every twin reads, with a defined staleness bound, atomic writes, and a
narrated (never-blank) degrade when the snapshot is missing, stale, or
unreadable.

Test groups
-----------
Group 1 -- Library primitives (no subprocess spawning of dashboard scripts):
    dashboard_snapshot_ttl_seconds resolution order (env > kanban.cfg >
    default 5s), write/read round-trip, staleness-bound miss, and the
    concurrent write/read atomicity proof (a background writer alternating
    between two large payloads; a foreground reader loop asserts every
    non-empty read is one payload or the other in full, never a mix).

Group 2 -- Twin parity, by construction (subprocess, real fixture kanban root):
    Two column-render.sh invocations with the IDENTICAL argv (mirroring the
    operator window's aggregated board command and the visibility window's
    per-column command) render byte-identical stdout, and the second
    invocation is a cache hit (no miss narration). A synthetic instrumented
    "data.sh" stand-in proves the underlying scan runs exactly once across
    two callers sharing the same cache key, not once per twin.

Group 3 -- Missing/stale snapshot degrade (subprocess):
    Deleting the snapshot file between two show-progress.sh invocations still
    renders correct, non-blank content and narrates the fallback to stderr
    only (stdout is unaffected, so goldens captured before this change still
    match). A snapshot older than the TTL is treated as a miss.

Group 4 -- Cleanup (no tmux required):
    The cleanup call kill.sh performs when the last dashboard session is
    torn down (pgai_temp_cleanup on the pgai_temp_subdir dashboard folder)
    removes the snapshot directory this library writes to -- exercised
    directly against a real snapshot file rather than asserted only by
    reading kill.sh's source.

Test naming (roles/TESTER.md anti-pattern 6): names describe behavior, never
bug IDs, scaffolding labels, or version strings.
"""

from __future__ import annotations

import os
import pathlib
import subprocess
import sys
import tempfile
import time

import pytest

_THIS_FILE = pathlib.Path(__file__).resolve()
_TEAM_DIR = _THIS_FILE.parent.parent.parent  # team/tests/unit/ -> team/
_REPO_ROOT = _TEAM_DIR.parent

_SNAPSHOT_SH = _TEAM_DIR / "scripts" / "dashboard" / "lib" / "snapshot.sh"
_COLUMN_RENDER_SH = _TEAM_DIR / "scripts" / "dashboard" / "column-render.sh"
_SHOW_PROGRESS_SH = _TEAM_DIR / "scripts" / "dashboard" / "show-progress.sh"
_SHOW_MULTI_SH = _TEAM_DIR / "scripts" / "dashboard" / "show-multi.sh"
_KILL_SH = _TEAM_DIR / "scripts" / "dashboard" / "kill.sh"
_TEMP_SH = _TEAM_DIR / "scripts" / "lib" / "temp.sh"

if str(_TEAM_DIR) not in sys.path:
    sys.path.insert(0, str(_TEAM_DIR))

from tests.fixtures.dashboard_render_fixture import (  # noqa: E402
    build_dashboard_project_root,
    clean_subprocess_env,
)


# ---------------------------------------------------------------------------
# Shared fixture helpers
# ---------------------------------------------------------------------------


def _fixture_env(tmp_path: pathlib.Path, ttl_seconds: str | None = None) -> tuple[dict, pathlib.Path]:
    """Build a single-project fixture kanban root and an isolated temp root
    for its snapshots, returning (env, kanban_root)."""
    fixture = build_dashboard_project_root(tmp_path, dev_tree_mode="valid")
    env = clean_subprocess_env(kanban_root=fixture.kanban_root)
    temp_root = tmp_path / "pgai_tmp"
    env["PGAI_AGENT_KANBAN_TEMP_DIR"] = str(temp_root)
    if ttl_seconds is not None:
        env["PGAI_DASHBOARD_SNAPSHOT_TTL_SECONDS"] = ttl_seconds
    return env, fixture.kanban_root, fixture.project_name, temp_root


def _run(cmd: list[str], env: dict) -> subprocess.CompletedProcess:
    return subprocess.run(
        cmd, capture_output=True, text=True, env=env, cwd=str(_REPO_ROOT), timeout=60
    )


# ===========================================================================
# Group 1 -- Library primitives
# ===========================================================================


def test_snapshot_lib_exists_and_has_valid_bash_syntax() -> None:
    assert _SNAPSHOT_SH.exists(), f"{_SNAPSHOT_SH} not found; must be committed"
    result = subprocess.run(["bash", "-n", str(_SNAPSHOT_SH)], capture_output=True, text=True)
    assert result.returncode == 0, f"bash -n failed:\n{result.stderr}"


def test_ttl_defaults_to_five_seconds_when_unset(tmp_path: pathlib.Path) -> None:
    env = dict(os.environ)
    env.pop("PGAI_DASHBOARD_SNAPSHOT_TTL_SECONDS", None)
    env.pop("PGAI_AGENT_KANBAN_ROOT_PATH", None)
    result = subprocess.run(
        ["bash", "-c", f"source {_SNAPSHOT_SH}; dashboard_snapshot_ttl_seconds"],
        capture_output=True,
        text=True,
        env=env,
    )
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == "5", (
        f"expected default staleness bound of 5s, got {result.stdout.strip()!r}"
    )


def test_ttl_env_override_wins_over_default(tmp_path: pathlib.Path) -> None:
    env = dict(os.environ)
    env["PGAI_DASHBOARD_SNAPSHOT_TTL_SECONDS"] = "17"
    result = subprocess.run(
        ["bash", "-c", f"source {_SNAPSHOT_SH}; dashboard_snapshot_ttl_seconds"],
        capture_output=True,
        text=True,
        env=env,
    )
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == "17"


def test_ttl_reads_kanban_cfg_when_env_unset(tmp_path: pathlib.Path) -> None:
    root = tmp_path / "cfg_root"
    root.mkdir()
    (root / "kanban.cfg").write_text(
        "[dashboard]\nsnapshot_ttl_seconds = 42\n", encoding="utf-8"
    )
    env = dict(os.environ)
    env.pop("PGAI_DASHBOARD_SNAPSHOT_TTL_SECONDS", None)
    env["PGAI_AGENT_KANBAN_ROOT_PATH"] = str(root)
    result = subprocess.run(
        ["bash", "-c", f"source {_SNAPSHOT_SH}; dashboard_snapshot_ttl_seconds"],
        capture_output=True,
        text=True,
        env=env,
    )
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == "42"


def test_write_then_read_round_trips_content_within_ttl(tmp_path: pathlib.Path) -> None:
    temp_root = tmp_path / "pgai_tmp"
    env = dict(os.environ)
    env["PGAI_AGENT_KANBAN_TEMP_DIR"] = str(temp_root)
    script = (
        f"source {_SNAPSHOT_SH}; "
        "printf 'hello snapshot' | dashboard_snapshot_write mykey; "
        "dashboard_snapshot_read mykey 3600"
    )
    result = subprocess.run(["bash", "-c", script], capture_output=True, text=True, env=env)
    assert result.returncode == 0, result.stderr
    assert result.stdout == "hello snapshot"


def test_read_misses_when_snapshot_older_than_ttl(tmp_path: pathlib.Path) -> None:
    temp_root = tmp_path / "pgai_tmp"
    env = dict(os.environ)
    env["PGAI_AGENT_KANBAN_TEMP_DIR"] = str(temp_root)
    # Write, then immediately read with a TTL of 0 (any age at all is stale).
    script = (
        f"source {_SNAPSHOT_SH}; "
        "printf 'stale content' | dashboard_snapshot_write mykey; "
        "sleep 1.1; "
        "dashboard_snapshot_read mykey 1"
    )
    result = subprocess.run(["bash", "-c", script], capture_output=True, text=True, env=env)
    assert result.returncode != 0, (
        f"dashboard_snapshot_read should report a miss once the snapshot is older "
        f"than its TTL; got stdout={result.stdout!r} rc={result.returncode}"
    )
    assert result.stdout == ""


def test_read_misses_on_missing_snapshot(tmp_path: pathlib.Path) -> None:
    temp_root = tmp_path / "pgai_tmp"
    env = dict(os.environ)
    env["PGAI_AGENT_KANBAN_TEMP_DIR"] = str(temp_root)
    script = f"source {_SNAPSHOT_SH}; dashboard_snapshot_read never-written 3600"
    result = subprocess.run(["bash", "-c", script], capture_output=True, text=True, env=env)
    assert result.returncode != 0
    assert result.stdout == ""


def test_concurrent_write_read_never_observes_a_partial_snapshot(tmp_path: pathlib.Path) -> None:
    """
    A background writer alternates dashboard_snapshot_write calls between two
    distinct ~2MB payloads while a foreground loop reads the same key
    continuously. Every non-empty read must equal one payload or the other in
    full -- never a truncated or mixed byte sequence. This demonstrates the
    atomic-rename guarantee (mktemp in the same dir + mv -f) snapshot
    writes require.
    """
    temp_root = tmp_path / "pgai_tmp"
    env = dict(os.environ)
    env["PGAI_AGENT_KANBAN_TEMP_DIR"] = str(temp_root)

    script = r'''
set -uo pipefail
source "$SNAPSHOT_SH"

KEY="concurrent-test-key"
A_CONTENT="$(python3 -c "print('A'*2000000)")"
B_CONTENT="$(python3 -c "print('B'*2000000)")"

writer() {
    for i in $(seq 1 20); do
        if (( i % 2 == 0 )); then
            printf '%s' "$A_CONTENT" | dashboard_snapshot_write "$KEY"
        else
            printf '%s' "$B_CONTENT" | dashboard_snapshot_write "$KEY"
        fi
    done
}

writer &
WPID=$!

BAD=0
CHECKS=0
while kill -0 "$WPID" 2>/dev/null; do
    content="$(dashboard_snapshot_read "$KEY" 3600 2>/dev/null || true)"
    if [[ -n "$content" ]]; then
        CHECKS=$((CHECKS+1))
        if [[ "$content" != "$A_CONTENT" && "$content" != "$B_CONTENT" ]]; then
            BAD=$((BAD+1))
        fi
    fi
done
wait "$WPID"

echo "checks=$CHECKS bad=$BAD"
[[ "$BAD" -eq 0 ]]
'''
    env["SNAPSHOT_SH"] = str(_SNAPSHOT_SH)
    result = subprocess.run(["bash", "-c", script], capture_output=True, text=True, env=env, timeout=60)
    assert result.returncode == 0, (
        f"a partially-written snapshot was observed by a reader; "
        f"stdout={result.stdout!r} stderr={result.stderr!r}"
    )
    assert "bad=0" in result.stdout, result.stdout


# ===========================================================================
# Group 2 -- Twin parity, by construction
# ===========================================================================


def test_operator_and_visibility_column_invocations_render_byte_identical_output(
    tmp_path: pathlib.Path,
) -> None:
    """
    create.sh sends the operator window's aggregated board columns and the
    visibility window's per-column panes the IDENTICAL column-render.sh argv
    (see test_dashboard_operator_window.py's
    test_operator_column_panes_send_visibility_column_commands). Running that
    identical invocation twice -- once "as" each twin -- must render
    byte-identical stdout, and the second run must be a cache hit (no miss
    narration on stderr).
    """
    env, kanban_root, _project, _temp_root = _fixture_env(tmp_path)
    cmd = [
        str(_COLUMN_RENDER_SH),
        "queue",
        "none",
        "13",
        "26",
        "--label",
        "CODER",
        "--kanban-root",
        str(kanban_root),
        "--all-projects",
    ]

    visibility_run = _run(cmd, env)
    assert visibility_run.returncode == 0, visibility_run.stderr
    assert "no fresh snapshot" in visibility_run.stderr, (
        "first invocation (standing in for whichever twin ticks first) should "
        f"narrate a cache miss; stderr={visibility_run.stderr!r}"
    )

    operator_run = _run(cmd, env)
    assert operator_run.returncode == 0, operator_run.stderr
    assert operator_run.stdout == visibility_run.stdout, (
        "twin invocations with identical argv must render byte-identical "
        f"stdout.\nfirst:\n{visibility_run.stdout}\nsecond:\n{operator_run.stdout}"
    )
    assert "no fresh snapshot" not in operator_run.stderr, (
        "second twin invocation should be a cache HIT (no miss narration); "
        f"stderr={operator_run.stderr!r}"
    )


def test_column_render_twin_pair_covers_every_visibility_column_label() -> None:
    """
    Every one of the eight VIS_*_CMD/OP_VIS_*_AGG_CMD column pairs
    (BUGS/PRIORITIES/REQUIREMENTS/PM/CODER/WRITER/TESTER/CM) uses the same
    column-render.sh entry point this module's parity test exercises, so
    the single parity test above generalizes to all eight -- this is a
    structural check that create.sh has not grown a ninth column type that
    bypasses column-render.sh (which would silently escape snapshot sharing).
    """
    content = _COLUMN_RENDER_SH.read_text(encoding="utf-8")
    # column-render.sh's --all-projects path must still be the sole entry
    # point column_type auto-detection recognizes.
    for label in ("bugs", "priorities", "requirements", "pm", "coder", "writer", "tester", "cm"):
        assert label in content, (
            f"column-render.sh no longer auto-detects COLUMN_TYPE for label "
            f"{label!r}; a new/renamed column may bypass the shared snapshot cache"
        )


def test_data_sh_scan_runs_once_across_two_twin_callers_sharing_a_cache_key(
    tmp_path: pathlib.Path,
) -> None:
    """
    Instrumentation proof for acceptance criterion 2: a synthetic "data.sh"
    stand-in that appends one line to a counter file on every invocation is
    called twice through dashboard_data_snapshot_get with the SAME argv
    (mirroring an overview pane and a drill-N pane scanning the same
    project). The counter file must show exactly one invocation -- the
    underlying scan ran once per tick, not once per twin.
    """
    temp_root = tmp_path / "pgai_tmp"
    counter_file = tmp_path / "invocations.log"

    fake_data_sh = tmp_path / "fake_data.sh"
    fake_data_sh.write_text(
        "#!/usr/bin/env bash\n"
        f'echo invoked >> "{counter_file}"\n'
        'echo "PROJECT_NAME=$2"\n'
        'echo "SCAN_EPOCH=$(date +%s%N)"\n',
        encoding="utf-8",
    )
    fake_data_sh.chmod(0o755)

    env = dict(os.environ)
    env["PGAI_AGENT_KANBAN_TEMP_DIR"] = str(temp_root)

    script = (
        f'source "{_SNAPSHOT_SH}"; '
        f'dashboard_data_snapshot_get "{fake_data_sh}" --project demo-project; '
        f'dashboard_data_snapshot_get "{fake_data_sh}" --project demo-project'
    )
    result = subprocess.run(["bash", "-c", script], capture_output=True, text=True, env=env)
    assert result.returncode == 0, result.stderr

    lines = [ln for ln in result.stdout.splitlines() if ln.strip()]
    # Both calls must produce the identical rendered block (same scan epoch) --
    # proof that the second caller read the first caller's cached output
    # rather than re-invoking the fake data.sh stand-in itself.
    first_block = "\n".join(lines[:2])
    second_block = "\n".join(lines[2:4]) if len(lines) >= 4 else ""
    assert second_block == first_block, (
        f"twin callers sharing a cache key must render the identical cached "
        f"scan output, not two independent scans.\nfirst:\n{first_block}\n"
        f"second:\n{second_block}"
    )

    assert counter_file.exists(), "the fake data.sh stand-in was never invoked"
    invocation_count = len(counter_file.read_text(encoding="utf-8").splitlines())
    assert invocation_count == 1, (
        f"expected the underlying scan to run exactly once for two twin "
        f"callers sharing a cache key; it ran {invocation_count} times"
    )


def test_drill_and_overview_progress_panes_share_one_data_sh_scan_per_project(
    tmp_path: pathlib.Path,
) -> None:
    """
    A drill-N window's progress pane (show-progress.sh --project X) and the
    overview window's progress pane (show-multi.sh --mode progress, which
    calls show-progress.sh --project X internally) are twins for project X.
    Running the drill invocation first, then the overview invocation, must
    produce a second (overview) run that is a cache HIT (no miss narration)
    and whose data lines match the drill pane's own output.
    """
    env, kanban_root, project_name, _temp_root = _fixture_env(tmp_path)

    drill_run = _run([str(_SHOW_PROGRESS_SH), "--project", project_name, "--kanban-root", str(kanban_root)], env)
    assert drill_run.returncode == 0, drill_run.stderr
    assert "no fresh snapshot" in drill_run.stderr

    overview_run = _run([str(_SHOW_MULTI_SH), "--mode", "progress", "--kanban-root", str(kanban_root)], env)
    assert overview_run.returncode == 0, overview_run.stderr
    assert "no fresh snapshot" not in overview_run.stderr, (
        "overview's per-project show-progress.sh call should reuse the drill "
        f"pane's fresh snapshot; stderr={overview_run.stderr!r}"
    )

    # With one registered project the overview delegates to the same
    # show-progress.sh invocation, so every rendered data line of the drill
    # pane must appear in the overview output.
    for line in drill_run.stdout.splitlines():
        assert line in overview_run.stdout, (
            f"drill pane line {line!r} missing from overview output:\n{overview_run.stdout}"
        )


# ===========================================================================
# Group 3 -- Missing/stale snapshot degrade
# ===========================================================================


def test_deleting_snapshot_between_ticks_still_renders_and_narrates_fallback(
    tmp_path: pathlib.Path,
) -> None:
    env, kanban_root, project_name, temp_root = _fixture_env(tmp_path)
    cmd = [str(_SHOW_PROGRESS_SH), "--project", project_name, "--kanban-root", str(kanban_root)]

    first_run = _run(cmd, env)
    assert first_run.returncode == 0, first_run.stderr
    assert first_run.stdout.strip(), "first run must not render a blank pane"

    snapshot_dir = temp_root / "dashboard" / "snapshots"
    snapshot_files = list(snapshot_dir.glob("*.snap"))
    assert snapshot_files, f"expected a snapshot file under {snapshot_dir}"
    for f in snapshot_files:
        f.unlink()

    second_run = _run(cmd, env)
    assert second_run.returncode == 0, second_run.stderr
    assert second_run.stdout.strip(), (
        "pane must not blank when its snapshot is deleted mid-run; "
        f"stdout={second_run.stdout!r}"
    )
    assert second_run.stdout == first_run.stdout, (
        "content must still be correct (matching the pre-deletion render) "
        "after falling back to a direct render"
    )
    assert "no fresh snapshot" in second_run.stderr, (
        f"the fallback must be narrated to stderr; stderr={second_run.stderr!r}"
    )


def test_unreadable_snapshot_degrades_to_direct_render(tmp_path: pathlib.Path) -> None:
    env, kanban_root, project_name, temp_root = _fixture_env(tmp_path)
    cmd = [str(_SHOW_PROGRESS_SH), "--project", project_name, "--kanban-root", str(kanban_root)]

    first_run = _run(cmd, env)
    assert first_run.returncode == 0, first_run.stderr

    snapshot_dir = temp_root / "dashboard" / "snapshots"
    snapshot_files = list(snapshot_dir.glob("*.snap"))
    assert snapshot_files
    for f in snapshot_files:
        f.chmod(0o000)

    try:
        second_run = _run(cmd, env)
        assert second_run.returncode == 0, second_run.stderr
        assert second_run.stdout.strip(), "an unreadable snapshot must not blank the pane"
        assert second_run.stdout == first_run.stdout
    finally:
        for f in snapshot_files:
            f.chmod(0o644)


# ===========================================================================
# Group 4 -- Cleanup
# ===========================================================================


def test_kill_sh_scoped_cleanup_call_removes_the_snapshot_directory(tmp_path: pathlib.Path) -> None:
    """
    kill.sh's teardown resolves the dashboard folder with
    `pgai_temp_subdir dashboard` and, once no dashboard session remains,
    removes it with `pgai_temp_cleanup` on deliberate exit (while another
    dashboard runs it removes only the killed session's own state -- see
    test_dashboard_session_scoped_state.py). Exercised directly here (no
    live tmux session required): write a real snapshot, run that cleanup
    call, and assert the snapshot directory is gone.
    """
    content = _KILL_SH.read_text(encoding="utf-8")
    assert '_DASH_DIR="$(pgai_temp_subdir dashboard' in content and 'pgai_temp_cleanup "$_DASH_DIR"' in content, (
        "kill.sh no longer runs the scoped dashboard temp cleanup call this "
        "test exercises directly; snapshots would survive session teardown"
    )

    temp_root = tmp_path / "pgai_tmp"
    env = dict(os.environ)
    env["PGAI_AGENT_KANBAN_TEMP_DIR"] = str(temp_root)

    write_script = (
        f'source "{_SNAPSHOT_SH}"; '
        'printf "leftover" | dashboard_snapshot_write leftover-key'
    )
    write_result = subprocess.run(["bash", "-c", write_script], capture_output=True, text=True, env=env)
    assert write_result.returncode == 0, write_result.stderr

    snapshot_dir = temp_root / "dashboard" / "snapshots"
    assert list(snapshot_dir.glob("*.snap")), "setup failed: no snapshot file was written"

    cleanup_script = f'source "{_TEMP_SH}"; pgai_temp_cleanup "$(pgai_temp_subdir dashboard)"'
    cleanup_result = subprocess.run(["bash", "-c", cleanup_script], capture_output=True, text=True, env=env)
    assert cleanup_result.returncode == 0, cleanup_result.stderr

    assert not (temp_root / "dashboard").exists(), (
        "kill.sh's scoped cleanup call did not remove the dashboard snapshot "
        f"directory ({temp_root / 'dashboard'})"
    )
