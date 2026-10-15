"""
test_dashboard_missing_tree_fixture.py
========================================
Missing-tree fixture: dev tree path absent/invalid
— every git-dependent pane shows the documented clear-error or
suppression-with-narration behavior; nothing hangs, prompts, or
stack-traces.

WHY THIS EXISTS
---------------
Every RENDER-PATH dashboard site falls back
to a safe sentinel (v0.0.0 / unknown / none) when the dev tree is missing or
invalid, and the two 4:GIT-WINDOW scripts
(git-status.sh, git-recent-tags.sh) are unified to render a single
clear-error string ("git tree not available: <path>") for the same
condition.  This test file promotes that verification into permanent pytest
coverage, exercising THREE distinct missing/invalid dev-tree states —
unconfigured, missing directory, present-but-not-a-git-repo — against every
render-path and git-window script that touches git.

WHAT "NOTHING HANGS, PROMPTS, OR STACK-TRACES" MEANS HERE
-----------------------------------------------------------
  - Every invocation is wrapped in a subprocess timeout (15s) as a
    hang-detection safety net — a genuine hang fails the test via
    subprocess.TimeoutExpired rather than the test suite itself hanging.
  - Every invocation asserts exit code 0 (the documented contract: a
    missing/invalid tree degrades the pane's own content but never fails
    the whole script, so `watch` does not spin/error).
  - Output is checked for stack-trace / crash markers ("Traceback",
    "command not found", "Segmentation fault", "syntax error") that would
    indicate a code defect rather than a graceful degrade.
  - A PATH-shadowing git stub is installed for every invocation too (not
    just the network-stub fixture file) as a second safety net: even under
    a missing/invalid tree, no code path should attempt a network git call.
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

_TIMEOUT_SECONDS = 15

_CRASH_MARKERS = (
    "Traceback (most recent call last)",
    "command not found",
    "Segmentation fault",
    "syntax error",
    "Unbound variable",
)

_MISSING_TREE_MODES = ["missing", "not_a_repo", "unconfigured"]


def _run(
    script_name: str,
    args: list[str],
    *,
    kanban_root: pathlib.Path,
    env_overrides: dict[str, str],
    input_text: str = "",
) -> subprocess.CompletedProcess:
    """Run a dashboard script with a bounded timeout and captured output.

    Uses run_dashboard_script (not a bare subprocess.run) so a timeout can
    never orphan a grandchild -- see that helper's own docstring.
    """
    env = clean_subprocess_env(kanban_root=kanban_root)
    env.update(env_overrides)

    return run_dashboard_script(
        ["bash", str(_DASHBOARD_DIR / script_name), *args],
        env=env,
        timeout=_TIMEOUT_SECONDS,
        input_text=input_text,
    )


def _assert_clean_degrade(result: subprocess.CompletedProcess, *, label: str) -> None:
    """Shared assertions: exit 0, no crash markers, no stack trace."""
    combined = result.stdout + result.stderr
    assert result.returncode == 0, (
        f"{label}: expected exit 0 on a missing/invalid dev tree "
        f"(clear-error/suppression must not fail the script); got "
        f"rc={result.returncode}.\nstdout: {result.stdout!r}\nstderr: {result.stderr!r}"
    )
    for marker in _CRASH_MARKERS:
        assert marker not in combined, (
            f"{label}: output contains crash marker {marker!r} — the "
            f"missing-tree degrade must never hang, prompt, or stack-trace.\n"
            f"Combined output: {combined!r}"
        )


# ---------------------------------------------------------------------------
# 4:GIT-WINDOW scripts — must render the unified clear-error string
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("dev_tree_mode", _MISSING_TREE_MODES)
@pytest.mark.parametrize("script_name", ["git-status.sh", "git-recent-tags.sh"])
def test_git_window_scripts_render_clear_error_for_missing_tree(
    tmp_path: pathlib.Path, script_name: str, dev_tree_mode: str
) -> None:
    """git-status.sh / git-recent-tags.sh render "git tree not available: ..."
    for every missing/invalid dev-tree mode, exit 0, and make zero network
    calls — no exceptions for the 4:git window (same design law the lint
    gate enforces structurally).
    """
    fixture = build_dashboard_project_root(tmp_path, dev_tree_mode=dev_tree_mode)

    stub_bin = tmp_path / "stub-bin"
    log_file = tmp_path / "network-calls.log"
    env_overrides = install_git_network_stub(stub_bin, log_file)

    result = _run(
        script_name,
        ["--kanban-root", str(fixture.kanban_root), "--project", fixture.project_name],
        kanban_root=fixture.kanban_root,
        env_overrides=env_overrides,
    )

    _assert_clean_degrade(result, label=f"{script_name} [{dev_tree_mode}]")
    assert "git tree not available:" in result.stdout, (
        f"{script_name} [{dev_tree_mode}]: expected the documented clear-error "
        f"string 'git tree not available:' in stdout; got: {result.stdout!r}"
    )
    assert not log_file.exists(), (
        f"{script_name} [{dev_tree_mode}]: zero network calls expected even "
        f"on a missing/invalid tree, but the network-call log was created: "
        f"{log_file.read_text(encoding='utf-8') if log_file.exists() else ''}"
    )


# ---------------------------------------------------------------------------
# RENDER-PATH scripts — must degrade to a safe sentinel, never crash
#
# Each entry below is (script_name, args_builder). args_builder takes the
# fixture and returns the script's argv (excluding "bash <script>" itself,
# which _run prepends). column-render.sh's requirements-column invocation is
# included here because its requirements column calls
# pp_last_released_version per registered project unconditionally (census
# site A8) — exercised even though this fixture project has zero
# requirement files.
# ---------------------------------------------------------------------------

_RENDER_PATH_SCRIPTS = [
    (
        "data.sh",
        lambda f: ["--project", f.project_name, "--kanban-root", str(f.kanban_root)],
    ),
    (
        "show-header.sh",
        lambda f: ["--kanban-root", str(f.kanban_root), "--project", f.project_name],
    ),
    (
        "show-multi.sh",
        lambda f: ["--kanban-root", str(f.kanban_root), "--mode", "header"],
    ),
    (
        "metadata.sh",
        lambda f: ["--kanban-root", str(f.kanban_root)],
    ),
    (
        "attention.sh",
        lambda f: ["--kanban-root", str(f.kanban_root)],
    ),
    (
        "status-bottom.sh",
        lambda f: [str(f.kanban_root), f.project_name],
    ),
    (
        "column-render.sh",
        lambda f: [
            "input", "none", "none",
            "--label", "REQUIREMENTS",
            "--kanban-root", str(f.kanban_root),
            "--all-projects",
        ],
    ),
]
_RENDER_PATH_SCRIPT_IDS = [name for name, _ in _RENDER_PATH_SCRIPTS]


@pytest.mark.parametrize("dev_tree_mode", _MISSING_TREE_MODES)
@pytest.mark.parametrize(
    "script_name, build_args", _RENDER_PATH_SCRIPTS, ids=_RENDER_PATH_SCRIPT_IDS
)
def test_render_path_script_degrades_gracefully_for_missing_tree(
    tmp_path: pathlib.Path, script_name: str, build_args, dev_tree_mode: str
) -> None:
    """Every render-path script (data.sh, show-header.sh, show-multi.sh,
    metadata.sh, attention.sh, status-bottom.sh, column-render.sh) degrades
    cleanly — exit 0, no crash markers, zero network calls — across every
    missing/invalid dev-tree mode.  data.sh additionally degrades
    LAST_RELEASED to a safe sentinel rather than surfacing a raw error.
    """
    fixture = build_dashboard_project_root(tmp_path, dev_tree_mode=dev_tree_mode)
    stub_bin = tmp_path / "stub-bin"
    log_file = tmp_path / "network-calls.log"
    env_overrides = install_git_network_stub(stub_bin, log_file)

    result = _run(
        script_name,
        build_args(fixture),
        kanban_root=fixture.kanban_root,
        env_overrides=env_overrides,
    )

    label = f"{script_name} [{dev_tree_mode}]"
    _assert_clean_degrade(result, label=label)
    assert not log_file.exists(), (
        f"{label}: zero network calls expected even on a missing/invalid "
        f"tree, but the network-call log was created: "
        f"{log_file.read_text(encoding='utf-8') if log_file.exists() else ''}"
    )
    if script_name == "data.sh":
        assert (
            "LAST_RELEASED=none" in result.stdout
            or "LAST_RELEASED=v0.0.0" in result.stdout
        ), (
            f"{label}: expected LAST_RELEASED to degrade to a safe sentinel; "
            f"got: {result.stdout!r}"
        )
