"""
test_dashboard_network_stub_fixture.py
========================================
Network-stub fixture: full dashboard refresh across
ALL windows performs ZERO remote git operations (stub records attempts;
assert empty) — no exceptions.

WHY THIS EXISTS
---------------
Every render-path git call site was converted to route through the
local-only surface, verified behaviorally, but only as throwaway scratch
runs during development — none of that verification survived as permanent
pytest coverage.  This file promotes that verification permanently
into the test suite: a real, git-shadowing stub drives the SAME entry point
the shipped smoke test (test_dashboard_smoke_container.py) uses for a full
refresh (``create.sh --no-tmux``), plus every render-path / 4:git-window
script that touches git and that ``--no-tmux`` mode does not itself invoke (show-header.sh, metadata.sh,
git-recent-tags.sh, status-bottom.sh — see the SCOPE note below), and
asserts the stub's network-call log is empty across every one of them.

SCOPE NOTE — what "ALL windows" means for this fixture
--------------------------------------------------------
``create.sh --no-tmux`` (see create.sh's one-shot block) already drives:
header/queues/progress (show-multi.sh), next-cron-firings.sh, attention.sh,
the visibility columns (column-render.sh, both input and queue subcommands,
--all-projects), legend-render.sh, the cron-log tail, git-status.sh,
show-metrics.sh, the recent-logs tail, and human-review.sh.

Four render-path / git-window scripts are NOT reached by ``--no-tmux``
mode because their tmux windows are wired directly to ``watch`` rather than
into the one-shot block: ``show-header.sh`` (main window in single-project
tmux mode, and every drill-N window), ``metadata.sh`` (5:metadata window),
``git-recent-tags.sh`` (4:git window, right pane), and ``status-bottom.sh``
(the tmux status-bar line).  This fixture invokes all four directly so the
"ALL windows" claim holds without needing a real tmux session (which the
sandbox this suite runs in does not reliably provide).

Every other dashboard script the census classifies as NO-GIT
(costs.sh, debug-logs.sh, training-logs.sh, logs.sh, legend-render.sh,
queue-pane.sh, project-toggle.sh, show-document-drill.sh,
verify-window0-geometry.sh, status-right.sh, and the control scripts
attach.sh/detach.sh/kill.sh) contains no git invocation of any kind (per
the census's exhaustive grep passes and the network-git lint gate, which
scans literally every file in the directory) — those files structurally
cannot emit a network git call, so they are not separately re-invoked here;
their git-free status is enforced by the lint gate
(test_lint_dashboard_network_git_gate.py) rather than re-proven by
behavioral replay in this file.

SUBPROCESS CONSUMERS (show-active.sh, show-progress.sh, show-queues.sh,
show-status-window.sh) call data.sh as a subprocess and inherit whatever
guard data.sh already sets; they are exercised directly below anyway for
completeness since they ARE distinct tmux windows/panes.
"""

from __future__ import annotations

import pathlib
import subprocess

import pytest

from tests.fixtures.dashboard_render_fixture import (
    build_dashboard_project_root,
    clean_subprocess_env,
    run_dashboard_script,
)
from tests.fixtures.git_network_stub import install_git_network_stub

_THIS_FILE = pathlib.Path(__file__).resolve()
_TEAM_DIR = _THIS_FILE.parent.parent.parent  # team/
_DASHBOARD_DIR = _TEAM_DIR / "scripts" / "dashboard"
_CREATE_SH = _DASHBOARD_DIR / "create.sh"

_TIMEOUT_SECONDS = 60


@pytest.fixture
def network_stub_env(tmp_path: pathlib.Path):
    """Install the git network-call interceptor; return (env_overrides, log_file)."""
    stub_bin = tmp_path / "stub-bin"
    log_file = tmp_path / "network-calls.log"
    overrides = install_git_network_stub(stub_bin, log_file)
    return overrides, log_file


def _run(
    argv: list[str],
    *,
    kanban_root: pathlib.Path,
    env_overrides: dict[str, str],
) -> subprocess.CompletedProcess:
    # Start from an ambient-leak-cleaned environment (see
    # dashboard_render_fixture.clean_subprocess_env) so that the CALLING
    # agent's own PGAI_PROJECT_NAME / PGAI_DEV_TREE_PATH / PP_* env vars
    # (legitimately set for the agent's own task) never mask a missing
    # explicit --project/--kanban-root argument by falling back to the
    # real live project instead of the synthetic fixture.
    env = clean_subprocess_env(kanban_root=kanban_root)
    env.update(env_overrides)

    # Uses run_dashboard_script (not a bare subprocess.run) so a timeout can
    # never orphan a grandchild -- see that helper's own docstring.
    return run_dashboard_script(argv, env=env, timeout=_TIMEOUT_SECONDS)


def _assert_no_network_calls(log_file: pathlib.Path, *, label: str) -> None:
    assert not log_file.exists(), (
        f"{label}: expected ZERO remote git operations, but the network-call "
        f"log recorded at least one:\n"
        + (log_file.read_text(encoding="utf-8") if log_file.exists() else "")
    )


# ---------------------------------------------------------------------------
# The primary full-refresh entry point: create.sh --no-tmux
# ---------------------------------------------------------------------------


def test_create_sh_no_tmux_full_refresh_makes_zero_network_calls(
    tmp_path: pathlib.Path, network_stub_env
) -> None:
    """create.sh --no-tmux drives header/queues/progress/cron-firings/
    attention/visibility(x8 columns)/legend/cron-logs/git-status/metrics/
    recent-logs/human-review in one process — the same entry point the
    shipped in-container smoke test exercises.  Zero remote git operations
    must be attempted across the whole run.
    """
    fixture = build_dashboard_project_root(tmp_path, dev_tree_mode="valid")
    env_overrides, log_file = network_stub_env

    result = _run(
        ["bash", str(_CREATE_SH), "--no-tmux", "--kanban-root", str(fixture.kanban_root)],
        kanban_root=fixture.kanban_root,
        env_overrides=env_overrides,
    )

    assert result.returncode == 0, (
        f"create.sh --no-tmux exited {result.returncode}.\n"
        f"stdout: {result.stdout!r}\nstderr: {result.stderr!r}"
    )
    _assert_no_network_calls(log_file, label="create.sh --no-tmux (full refresh)")

    # Positive-rendering sanity check: the happy-path version must actually
    # surface somewhere in the output — a guard that silently swallowed all
    # data (rather than reading it locally) would also show zero network
    # calls, which would be a false-positive pass. Confirm real data renders.
    assert fixture.project_name in result.stdout, (
        "Expected the registered project name to appear in the --no-tmux "
        "output; the render may have silently produced no data."
    )


# ---------------------------------------------------------------------------
# Render-path / git-window scripts NOT reached by --no-tmux mode
# (see module docstring SCOPE NOTE), plus drill-window-style single-project
# invocations of column-render.sh / data.sh.
#
# Each entry is (script_name, args_builder, expected_substring_or_None).
# args_builder(fixture) returns the script's argv; expected_substring, when
# not None, is a substring that must appear in stdout (a real-data sanity
# check that a guard silently swallowing all data would not satisfy).
# ---------------------------------------------------------------------------

_SINGLE_SCRIPT_CASES = [
    (
        "show-header.sh",
        lambda f: ["--kanban-root", str(f.kanban_root), "--project", f.project_name],
        "v0.1.0",
    ),
    (
        "metadata.sh",
        lambda f: ["--kanban-root", str(f.kanban_root)],
        None,  # checked dynamically below (fixture.project_name)
    ),
    (
        "git-recent-tags.sh",
        lambda f: ["--kanban-root", str(f.kanban_root), "--project", f.project_name],
        "v0.1.0",
    ),
    (
        "status-bottom.sh",
        lambda f: [str(f.kanban_root), f.project_name],
        None,
    ),
    (
        "show-queues.sh",
        lambda f: ["--kanban-root", str(f.kanban_root), "--project", f.project_name],
        None,
    ),
    (
        "column-render.sh",
        # Drill-N windows invoke column-render.sh in single-project mode (no
        # --all-projects) — exercise that call shape too, not just the
        # --all-projects form --no-tmux drives.
        lambda f: [
            "input",
            str(f.kanban_root / "projects" / f.project_name / "requirements"),
            "none",
            "--label", "REQUIREMENTS",
            "--kanban-root", str(f.kanban_root),
        ],
        None,
    ),
    (
        "data.sh",
        lambda f: ["--project", f.project_name, "--kanban-root", str(f.kanban_root)],
        "LAST_RELEASED=v0.1.0",
    ),
]
_SINGLE_SCRIPT_IDS = [name for name, _, _ in _SINGLE_SCRIPT_CASES]


@pytest.mark.parametrize(
    "script_name, build_args, expected_substring",
    _SINGLE_SCRIPT_CASES,
    ids=_SINGLE_SCRIPT_IDS,
)
def test_single_script_invocation_makes_zero_network_calls(
    tmp_path: pathlib.Path, network_stub_env, script_name: str, build_args, expected_substring,
) -> None:
    """Every render-path / git-window script not reached by --no-tmux mode,
    plus the drill-window single-project call shapes for column-render.sh
    and data.sh, exits 0 and makes zero remote git operations.  Where a
    real committed tag or project name is expected to render, that positive
    sanity check runs too — a guard that silently swallowed all data would
    also show zero network calls, which would be a false-positive pass.
    """
    fixture = build_dashboard_project_root(tmp_path, dev_tree_mode="valid")
    env_overrides, log_file = network_stub_env

    result = _run(
        ["bash", str(_DASHBOARD_DIR / script_name), *build_args(fixture)],
        kanban_root=fixture.kanban_root,
        env_overrides=env_overrides,
    )

    assert result.returncode == 0, f"{script_name}: {result.stderr}"
    _assert_no_network_calls(log_file, label=script_name)
    if script_name == "metadata.sh":
        assert fixture.project_name in result.stdout
    elif expected_substring is not None:
        assert expected_substring in result.stdout, (
            f"{script_name}: expected {expected_substring!r} to render; "
            f"got: {result.stdout!r}"
        )


# ---------------------------------------------------------------------------
# Subprocess-consumer windows (data.sh consumers) — exercised directly since
# each backs a distinct tmux pane, even though they delegate to data.sh.
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "script_name",
    ["show-active.sh", "show-progress.sh", "show-status-window.sh"],
)
def test_data_sh_consumer_windows_make_zero_network_calls(
    tmp_path: pathlib.Path, network_stub_env, script_name: str
) -> None:
    fixture = build_dashboard_project_root(tmp_path, dev_tree_mode="valid")
    env_overrides, log_file = network_stub_env

    result = _run(
        [
            "bash",
            str(_DASHBOARD_DIR / script_name),
            "--kanban-root",
            str(fixture.kanban_root),
            "--project",
            fixture.project_name,
        ],
        kanban_root=fixture.kanban_root,
        env_overrides=env_overrides,
    )

    assert result.returncode == 0, f"{script_name}: {result.stderr}"
    _assert_no_network_calls(log_file, label=script_name)
