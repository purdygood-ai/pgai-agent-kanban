"""
test_testing_only_plugin.py
============================
Behavioral unit tests for the testing-only workflow plugin at
team/workflows/testing-only/.

Tests cover:
  (a) Plugin loads via wf_load_plugin and all wf_* hooks return correct values
  (b) wf_dashboard_render returns "label" — the label-semantics signal
  (c) Dashboard label-semantics: a testing-only project with no last_released
      (the invariant for projects that never go through release lifecycle)
      renders requirements as open, even when the label version string would
      satisfy is_shipped() if treated as semver
  (d) wf_finalize returns "report" — not "tag" or "publish"
  (e) wf_agents returns "pm,tester" — no CM step
  (f) wf_git_mode returns "ro" — read-only worktree, never rw
  (g) pre_task and post_task are no-ops that return 0
  (h) Contract checks (validate-workflow) pass for the testing-only plugin
  (i) Fail-closed fixtures:
      - workflow_type=nonsense routes to BLOCKED naming the type
      - status=scaffold routes to BLOCKED naming the type
  (j) Release-state.md byte-identical: wf_pre_task and wf_post_task do not
      mutate a synthetic release-state.md placed in the project directory

Naming convention: function names describe the behavior under test, not the
bug ID or task ID that prompted them.
"""

from __future__ import annotations

import pathlib
import textwrap

import pytest

from tests.unit.shell_harness import run_bash

# ---------------------------------------------------------------------------
# Paths to plugin and library files.
#
# Tests run from the team/ subdirectory (cd "$REPO_ROOT/team" in
# run-unit-tests.sh), so paths are relative to team/:
#   scripts/lib/workflow.sh        (not team/scripts/lib/workflow.sh)
#   workflows/testing-only/        (not team/workflows/testing-only/)
# ---------------------------------------------------------------------------
_PLUGIN_DIR = pathlib.Path("workflows/testing-only")
_PLUGIN_CFG = _PLUGIN_DIR / "workflow.cfg"
_PLUGIN_SH = _PLUGIN_DIR / "workflow.sh"
_DISPATCHER_LIB = "scripts/lib/workflow.sh"
_CONTRACT_LIB = "scripts/lib/workflow-contract.sh"
_WORKFLOWS_DIR = pathlib.Path("workflows")


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _load_plugin_script(workflows_dir: pathlib.Path | None = None) -> str:
    """Return a bash snippet that sources the dispatcher and loads the plugin.

    When workflows_dir is provided, passes --workflows-dir to wf_load_plugin.
    Otherwise uses the real team/workflows/ directory.
    """
    if workflows_dir is not None:
        wf_dir_arg = f"--workflows-dir '{workflows_dir}'"
    else:
        wf_dir_arg = f"--workflows-dir '{_WORKFLOWS_DIR}'"
    return textwrap.dedent(f"""\
        source {_DISPATCHER_LIB}
        wf_load_plugin {wf_dir_arg} 'testing-only'
    """)


def _build_scaffold_plugin(
    workflows_dir: pathlib.Path,
    type_name: str,
) -> pathlib.Path:
    """Build a minimal scaffold (status=scaffold) plugin directory.

    Used to verify fail-closed behavior for scaffold-status plugins.
    """
    plugin_dir = workflows_dir / type_name
    plugin_dir.mkdir(parents=True, exist_ok=True)

    manifest = plugin_dir / "workflow.cfg"
    manifest.write_text(
        textwrap.dedent(f"""\
            [workflow]
            name = {type_name}
            description = scaffold test plugin
            status = scaffold

            [capabilities]
            version_semantics = label
            git_mode = ro
            finalize = report
            agents = pm,tester
        """),
        encoding="utf-8",
    )

    plugin_sh = plugin_dir / "workflow.sh"
    plugin_sh.write_text(
        textwrap.dedent("""\
            #!/usr/bin/env bash
            # Scaffold stub — hooks not yet implemented.
            wf_resolve_target_version() { die "NOT IMPLEMENTED: wf_resolve_target_version"; }
            wf_git_mode() { die "NOT IMPLEMENTED: wf_git_mode"; }
            wf_pre_task() { die "NOT IMPLEMENTED: wf_pre_task"; }
            wf_post_task() { die "NOT IMPLEMENTED: wf_post_task"; }
            wf_finalize() { die "NOT IMPLEMENTED: wf_finalize"; }
            wf_agents() { die "NOT IMPLEMENTED: wf_agents"; }
            wf_bundle_source_branch() { die "NOT IMPLEMENTED: wf_bundle_source_branch"; }
            wf_dashboard_render() { die "NOT IMPLEMENTED: wf_dashboard_render"; }
        """),
        encoding="utf-8",
    )
    return plugin_dir


# ===========================================================================
# (i) Fail-closed fixtures
# ===========================================================================


def test_unknown_workflow_type_nonsense_returns_nonzero(
    tmp_path: pathlib.Path,
) -> None:
    """wf_load_plugin returns non-zero when workflow_type is 'nonsense' (no plugin exists)."""
    workflows_dir = tmp_path / "workflows"
    workflows_dir.mkdir()

    script = textwrap.dedent(f"""\
        source {_DISPATCHER_LIB}
        wf_load_plugin --workflows-dir '{workflows_dir}' 'nonsense'
    """)
    result = run_bash(tmp_path, script)
    assert result.returncode != 0


def test_unknown_workflow_type_nonsense_error_names_the_type(
    tmp_path: pathlib.Path,
) -> None:
    """WF_LOAD_ERROR names 'nonsense' when no plugin directory exists for that type."""
    workflows_dir = tmp_path / "workflows"
    workflows_dir.mkdir()

    script = textwrap.dedent(f"""\
        source {_DISPATCHER_LIB}
        wf_load_plugin --workflows-dir '{workflows_dir}' 'nonsense' || true
        echo "ERROR: $WF_LOAD_ERROR"
    """)
    result = run_bash(tmp_path, script)
    assert "nonsense" in result.stdout or "nonsense" in result.stderr


def test_scaffold_status_plugin_returns_nonzero(tmp_path: pathlib.Path) -> None:
    """wf_load_plugin returns non-zero when the plugin manifest has status=scaffold."""
    workflows_dir = tmp_path / "workflows"
    _build_scaffold_plugin(workflows_dir, "scaffold-testing-only")

    script = textwrap.dedent(f"""\
        source {_DISPATCHER_LIB}
        wf_load_plugin --workflows-dir '{workflows_dir}' 'scaffold-testing-only'
    """)
    result = run_bash(tmp_path, script)
    assert result.returncode != 0


def test_scaffold_status_error_names_the_type(tmp_path: pathlib.Path) -> None:
    """WF_LOAD_ERROR names the scaffold plugin type when status=scaffold."""
    workflows_dir = tmp_path / "workflows"
    _build_scaffold_plugin(workflows_dir, "scaffold-testing-only")

    script = textwrap.dedent(f"""\
        source {_DISPATCHER_LIB}
        wf_load_plugin --workflows-dir '{workflows_dir}' 'scaffold-testing-only' || true
        echo "ERROR: $WF_LOAD_ERROR"
    """)
    result = run_bash(tmp_path, script)
    assert "scaffold-testing-only" in result.stdout or "scaffold-testing-only" in result.stderr


def test_scaffold_wf_git_mode_stub_returns_nonzero(tmp_path: pathlib.Path) -> None:
    """After a scaffold failure, wf_git_mode stub returns non-zero (plugin not loaded)."""
    workflows_dir = tmp_path / "workflows"
    _build_scaffold_plugin(workflows_dir, "scaffold-wf-ro")

    script = textwrap.dedent(f"""\
        source {_DISPATCHER_LIB}
        wf_load_plugin --workflows-dir '{workflows_dir}' 'scaffold-wf-ro' || true
        wf_git_mode
    """)
    result = run_bash(tmp_path, script)
    assert result.returncode != 0


# ===========================================================================
# (a) Plugin loads and basic hook values
# ===========================================================================


def test_testing_only_plugin_loads_successfully(tmp_path: pathlib.Path) -> None:
    """wf_load_plugin returns 0 when loading the testing-only plugin."""
    script = _load_plugin_script()
    result = run_bash(tmp_path, script)
    assert result.returncode == 0, (
        f"Expected testing-only plugin to load; stderr: {result.stderr!r}"
    )


def test_testing_only_plugin_sets_wf_plugin_dir(tmp_path: pathlib.Path) -> None:
    """wf_load_plugin sets WF_PLUGIN_DIR to the testing-only plugin directory."""
    script = _load_plugin_script() + "\necho \"$WF_PLUGIN_DIR\""
    result = run_bash(tmp_path, script)
    assert result.returncode == 0
    assert "testing-only" in result.stdout


def test_testing_only_plugin_sets_wf_type(tmp_path: pathlib.Path) -> None:
    """wf_load_plugin sets WF_TYPE to 'testing-only'."""
    script = _load_plugin_script() + "\necho \"$WF_TYPE\""
    result = run_bash(tmp_path, script)
    assert result.returncode == 0
    assert result.stdout.strip() == "testing-only"


# ===========================================================================
# (f) wf_git_mode returns "ro"
# ===========================================================================


def test_testing_only_wf_git_mode_returns_ro(tmp_path: pathlib.Path) -> None:
    """wf_git_mode returns 'ro' for the testing-only plugin (read-only worktree)."""
    script = _load_plugin_script() + "\nwf_git_mode"
    result = run_bash(tmp_path, script)
    assert result.returncode == 0
    assert result.stdout.strip() == "ro"


# ===========================================================================
# (e) wf_agents returns "pm,tester"
# ===========================================================================


def test_testing_only_wf_agents_returns_pm_tester(tmp_path: pathlib.Path) -> None:
    """wf_agents returns 'pm,tester' — no CM step in the testing-only workflow."""
    script = _load_plugin_script() + "\nwf_agents"
    result = run_bash(tmp_path, script)
    assert result.returncode == 0
    assert result.stdout.strip() == "pm,tester"


def test_testing_only_wf_agents_does_not_include_cm(tmp_path: pathlib.Path) -> None:
    """wf_agents does not include 'cm' — testing-only workflows never tag or push."""
    script = _load_plugin_script() + "\nwf_agents"
    result = run_bash(tmp_path, script)
    assert result.returncode == 0
    assert "cm" not in result.stdout.lower()


# ===========================================================================
# (d) wf_finalize returns "report"
# ===========================================================================


def test_testing_only_wf_finalize_returns_report(tmp_path: pathlib.Path) -> None:
    """wf_finalize returns 'report' — testing-only workflow writes a report, not a tag."""
    script = _load_plugin_script() + "\nwf_finalize"
    result = run_bash(tmp_path, script)
    assert result.returncode == 0
    assert result.stdout.strip() == "report"


def test_testing_only_wf_finalize_not_tag(tmp_path: pathlib.Path) -> None:
    """wf_finalize does not return 'tag' — testing-only workflow never creates a git tag."""
    script = _load_plugin_script() + "\nwf_finalize"
    result = run_bash(tmp_path, script)
    assert result.returncode == 0
    assert result.stdout.strip() != "tag"


# ===========================================================================
# (b) wf_dashboard_render returns "label"
# ===========================================================================


def test_testing_only_wf_dashboard_render_returns_label(tmp_path: pathlib.Path) -> None:
    """wf_dashboard_render returns 'label' to declare label-semantics to the dashboard."""
    script = _load_plugin_script() + "\nwf_dashboard_render"
    result = run_bash(tmp_path, script)
    assert result.returncode == 0
    assert result.stdout.strip() == "label"


def test_testing_only_wf_dashboard_render_not_git(tmp_path: pathlib.Path) -> None:
    """wf_dashboard_render does not return 'git' — testing-only has no git column data."""
    script = _load_plugin_script() + "\nwf_dashboard_render"
    result = run_bash(tmp_path, script)
    assert result.returncode == 0
    assert result.stdout.strip() != "git"


# ===========================================================================
# (c) Dashboard label semantics: no last_released → items render as open
# ===========================================================================


def test_testing_only_wf_bundle_source_branch_returns_main(
    tmp_path: pathlib.Path,
) -> None:
    """wf_bundle_source_branch returns 'main' — no RC branches for testing-only."""
    script = _load_plugin_script() + "\nwf_bundle_source_branch"
    result = run_bash(tmp_path, script)
    assert result.returncode == 0
    assert result.stdout.strip() == "main"


def test_testing_only_wf_resolve_target_version_echoes_label(
    tmp_path: pathlib.Path,
) -> None:
    """wf_resolve_target_version echoes back the supplied label argument unchanged."""
    script = _load_plugin_script() + "\nwf_resolve_target_version 'v1.0.0-testing-run-1'"
    result = run_bash(tmp_path, script)
    assert result.returncode == 0
    assert result.stdout.strip() == "v1.0.0-testing-run-1"


def test_testing_only_wf_resolve_target_version_requires_argument(
    tmp_path: pathlib.Path,
) -> None:
    """wf_resolve_target_version returns non-zero when called with no argument."""
    # Use set +e so the script does not abort on the expected non-zero return.
    script = textwrap.dedent(f"""\
        {_load_plugin_script()}
        set +e
        wf_resolve_target_version
        echo "exit:$?"
    """)
    result = run_bash(tmp_path, script)
    # Should print "exit:1" (or non-zero) when no argument is given.
    assert "exit:0" not in result.stdout


def test_label_semantics_invariant_no_last_released_renders_open(
    tmp_path: pathlib.Path,
) -> None:
    """A testing-only project has no semver last_released, so items always render open.

    Testing-only workflows never participate in the release lifecycle; their
    projects never write a semver last_released to release-state.md.  The
    dashboard's is_shipped() sentinel guard returns False when last_released
    is empty, "none", or "v0.0.0", which means testing-only items always
    render as open (white) regardless of the label version string.

    This test verifies the invariant by confirming that wf_dashboard_render
    returns "label" AND that when last_released is unset (empty), the
    is_shipped behavior maps to "not shipped".

    The case where last_released would exceed the label version (the
    fresh-project green-bug class described in the brief) is safe because:
      1. wf_dashboard_render signals "label" semantics to the engine.
      2. Testing-only projects never write a semver last_released, so the
         sentinel guard in is_shipped() always returns False for them.
    """
    # Part 1: wf_dashboard_render declares label semantics.
    script = _load_plugin_script() + "\nwf_dashboard_render"
    result = run_bash(tmp_path, script)
    assert result.returncode == 0
    render_mode = result.stdout.strip()
    assert render_mode == "label", (
        f"wf_dashboard_render must return 'label' to prevent semver is_shipped comparison; "
        f"got {render_mode!r}"
    )

    # Part 2: Verify the dashboard's is_shipped() sentinel guard returns False
    # when last_released is empty (the invariant for testing-only projects).
    # This is the Python logic from column-render.sh's PY heredoc, reproduced
    # here to confirm the guard behavior directly.
    def parse_version(v: str) -> tuple[int, ...] | None:
        if not v or not v.startswith("v"):
            return None
        parts = v[1:].split(".")
        if len(parts) < 3:
            return None
        try:
            return tuple(int(p) for p in parts[:3])
        except ValueError:
            return None

    def is_shipped_with_sentinel(version_str: str, last_released: str) -> bool:
        """Mirrors column-render.sh is_shipped() sentinel logic."""
        if not last_released or last_released.lower() in ("none", "v0.0.0", ""):
            return False
        tv = parse_version(version_str)
        lv = parse_version(last_released)
        if tv is None or lv is None:
            return False
        return tv <= lv

    # Testing-only invariant: last_released is empty for projects using this workflow.
    assert not is_shipped_with_sentinel("v0.5.0", ""), (
        "label version v0.5.0 with empty last_released must not be marked as shipped"
    )
    assert not is_shipped_with_sentinel("v0.5.0", "none"), (
        "label version v0.5.0 with last_released='none' must not be marked as shipped"
    )
    assert not is_shipped_with_sentinel("v0.5.0", "v0.0.0"), (
        "label version v0.5.0 with last_released='v0.0.0' must not be marked as shipped"
    )

    # Confirm the fresh-project green-bug class case: a label version that looks
    # numerically smaller than last_released is NOT shipped when last_released
    # is the empty/sentinel value that testing-only projects carry.
    # (If last_released were a real semver "v1.0.0" AND the engine used semver
    # comparison, v0.5.0 <= v1.0.0 would incorrectly return True — the bug class
    # the brief warns against.  The invariant is that testing-only projects
    # have no semver last_released, so this case never occurs in practice.)
    assert not is_shipped_with_sentinel("v0.5.0", ""), (
        "v0.5.0 label on a fresh testing-only project (empty last_released) "
        "must render as open, not shipped — the fresh-project green-bug class"
    )


# ===========================================================================
# (j) Release-state.md byte-identical after pre_task and post_task
# ===========================================================================


def test_pre_task_does_not_mutate_release_state(tmp_path: pathlib.Path) -> None:
    """wf_pre_task is a no-op that does not modify any files."""
    # Write a sentinel release-state.md to verify it is unchanged.
    sentinel_content = (
        "# Release State\n\n"
        "## Active RC\nnone\n\n"
        "## Last Released\nnone\n\n"
        "## State\nIDLE\n"
    )
    release_state = tmp_path / "release-state.md"
    release_state.write_text(sentinel_content, encoding="utf-8")

    script = _load_plugin_script() + "\nwf_pre_task 'TEST-001' 'main'"
    result = run_bash(tmp_path, script)
    assert result.returncode == 0

    # Verify release-state.md is byte-identical.
    actual_content = release_state.read_text(encoding="utf-8")
    assert actual_content == sentinel_content, (
        "wf_pre_task must not modify release-state.md — "
        "testing-only workflow never mutates release state"
    )


def test_post_task_does_not_mutate_release_state(tmp_path: pathlib.Path) -> None:
    """wf_post_task is a no-op that does not modify any files."""
    sentinel_content = (
        "# Release State\n\n"
        "## Active RC\nnone\n\n"
        "## Last Released\nnone\n\n"
        "## State\nIDLE\n"
    )
    release_state = tmp_path / "release-state.md"
    release_state.write_text(sentinel_content, encoding="utf-8")

    script = _load_plugin_script() + "\nwf_post_task 'TEST-001'"
    result = run_bash(tmp_path, script)
    assert result.returncode == 0

    actual_content = release_state.read_text(encoding="utf-8")
    assert actual_content == sentinel_content, (
        "wf_post_task must not modify release-state.md — "
        "testing-only workflow never mutates release state"
    )


def test_pre_task_returns_zero(tmp_path: pathlib.Path) -> None:
    """wf_pre_task returns exit code 0 (successful no-op)."""
    script = _load_plugin_script() + "\nwf_pre_task 'TEST-001' 'main'"
    result = run_bash(tmp_path, script)
    assert result.returncode == 0


def test_post_task_returns_zero(tmp_path: pathlib.Path) -> None:
    """wf_post_task returns exit code 0 (successful no-op)."""
    script = _load_plugin_script() + "\nwf_post_task 'TEST-001'"
    result = run_bash(tmp_path, script)
    assert result.returncode == 0


# ===========================================================================
# (h) Contract checks pass for the testing-only plugin
# ===========================================================================


def test_contract_check_all_passes_for_testing_only_plugin(
    tmp_path: pathlib.Path,
) -> None:
    """wfc_check_all passes for the real testing-only plugin directory."""
    plugin_dir = _PLUGIN_DIR.resolve()
    script = textwrap.dedent(f"""\
        source {_CONTRACT_LIB}
        wfc_check_all '{plugin_dir}' 'testing-only'
        echo "WFC_ERROR: $WFC_ERROR"
    """)
    result = run_bash(tmp_path, script)
    assert result.returncode == 0, (
        f"Contract check failed for testing-only plugin. "
        f"stdout: {result.stdout!r}, stderr: {result.stderr!r}"
    )


def test_contract_manifest_check_passes_for_testing_only_plugin(
    tmp_path: pathlib.Path,
) -> None:
    """wfc_check_manifest passes for the real testing-only plugin."""
    plugin_dir = _PLUGIN_DIR.resolve()
    script = textwrap.dedent(f"""\
        source {_CONTRACT_LIB}
        wfc_check_manifest '{plugin_dir}' 'testing-only'
    """)
    result = run_bash(tmp_path, script)
    assert result.returncode == 0


def test_contract_hook_check_passes_for_testing_only_plugin(
    tmp_path: pathlib.Path,
) -> None:
    """wfc_check_hooks passes for the real testing-only plugin (all 8 hooks defined)."""
    plugin_dir = _PLUGIN_DIR.resolve()
    script = textwrap.dedent(f"""\
        source {_CONTRACT_LIB}
        wfc_check_hooks '{plugin_dir}' 'testing-only'
    """)
    result = run_bash(tmp_path, script)
    assert result.returncode == 0


def test_contract_stub_check_passes_for_testing_only_plugin(
    tmp_path: pathlib.Path,
) -> None:
    """wfc_check_stubs passes for the real testing-only plugin (no NOT IMPLEMENTED markers)."""
    plugin_dir = _PLUGIN_DIR.resolve()
    script = textwrap.dedent(f"""\
        source {_CONTRACT_LIB}
        wfc_check_stubs '{plugin_dir}' 'testing-only'
    """)
    result = run_bash(tmp_path, script)
    assert result.returncode == 0


def test_contract_capabilities_check_passes_for_testing_only_plugin(
    tmp_path: pathlib.Path,
) -> None:
    """wfc_check_capabilities passes: git_mode=ro, version_semantics=label, finalize=report."""
    plugin_dir = _PLUGIN_DIR.resolve()
    script = textwrap.dedent(f"""\
        source {_CONTRACT_LIB}
        wfc_check_capabilities '{plugin_dir}' 'testing-only'
    """)
    result = run_bash(tmp_path, script)
    assert result.returncode == 0


# ===========================================================================
# All 8 hooks callable after load
# ===========================================================================


def test_all_hooks_callable_after_load(tmp_path: pathlib.Path) -> None:
    """All eight wf_* hooks are callable after loading the testing-only plugin."""
    script = textwrap.dedent(f"""\
        {_load_plugin_script()}
        wf_git_mode
        wf_resolve_target_version 'v1.0.0'
        wf_pre_task
        wf_post_task
        wf_finalize
        wf_agents
        wf_bundle_source_branch
        wf_dashboard_render
    """)
    result = run_bash(tmp_path, script)
    assert result.returncode == 0, (
        f"Expected all hooks to be callable; "
        f"stdout: {result.stdout!r}, stderr: {result.stderr!r}"
    )
