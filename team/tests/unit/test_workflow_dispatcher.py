"""
test_workflow_dispatcher.py
============================
Behavioral unit tests for team/scripts/lib/workflow.sh.

Tests source the shell script and invoke wf_load_plugin plus the wf_*
surface via the bash harness.  Fixture plugin directories are written to
tmp_path (never to a live install or the dev tree).

Functions under test:
  - wf_load_plugin: manifest discovery, validation, plugin sourcing
  - wf_* surface stubs: return non-zero before a successful load

Acceptance criteria covered:
  (a) valid manifest with status=ready loads and sources plugin; wf_git_mode
      returns the value declared in [capabilities] git_mode
  (b) status=scaffold routes to a non-zero return naming the type
  (c) unknown workflow_type routes to a non-zero return naming the type
  (d) missing/invalid manifest routes to a non-zero return

Naming convention: function names describe the behavior under test,
not the bug ID or task ID that prompted them.
"""

from __future__ import annotations

import pathlib
import textwrap

import pytest

from tests.unit.shell_harness import run_bash

_LIB = "scripts/lib/workflow.sh"


def _source_lib() -> str:
    """Return a bash snippet that sources workflow.sh (without calling functions)."""
    return f"source {_LIB}"


def _build_plugin(
    workflows_dir: pathlib.Path,
    type_name: str,
    *,
    status: str = "ready",
    git_mode: str = "none",
    agents: str = "pm,coder",
    version_semantics: str = "semver",
    finalize: str = "tag",
    include_plugin_sh: bool = True,
    extra_hooks: str = "",
) -> pathlib.Path:
    """Build a minimal plugin directory under workflows_dir/<type_name>.

    Creates:
      workflow.cfg  — INI manifest
      workflow.sh   — plugin script that defines minimal wf_* implementations

    Returns the plugin directory path.
    """
    plugin_dir = workflows_dir / type_name
    plugin_dir.mkdir(parents=True, exist_ok=True)

    # Write manifest.
    manifest = plugin_dir / "workflow.cfg"
    manifest.write_text(
        textwrap.dedent(f"""\
            [workflow]
            name = {type_name}
            description = test plugin {type_name}
            status = {status}

            [capabilities]
            version_semantics = {version_semantics}
            git_mode = {git_mode}
            finalize = {finalize}
            agents = {agents}
        """),
        encoding="utf-8",
    )

    if include_plugin_sh:
        # Write a minimal plugin script that implements the wf_* surface.
        plugin_sh = plugin_dir / "workflow.sh"
        plugin_sh.write_text(
            textwrap.dedent(f"""\
                #!/usr/bin/env bash
                # Minimal test plugin for {type_name}
                wf_resolve_target_version() {{ echo "{type_name}-version"; }}
                wf_git_mode() {{ echo "{git_mode}"; }}
                wf_pre_task() {{ echo "{type_name}-pre-task"; }}
                wf_post_task() {{ echo "{type_name}-post-task"; }}
                wf_finalize() {{ echo "{type_name}-finalize"; }}
                wf_agents() {{ echo "{agents}"; }}
                wf_bundle_source_branch() {{ echo "main"; }}
                wf_dashboard_render() {{ echo "{type_name}-render"; }}
                {extra_hooks}
            """),
            encoding="utf-8",
        )

    return plugin_dir


# ---------------------------------------------------------------------------
# (c) Unknown workflow_type routes to non-zero return naming the type
# ---------------------------------------------------------------------------


def test_unknown_workflow_type_returns_nonzero(tmp_path: pathlib.Path) -> None:
    """wf_load_plugin returns non-zero when the workflow_type has no plugin directory."""
    workflows_dir = tmp_path / "workflows"
    workflows_dir.mkdir()

    script = textwrap.dedent(f"""\
        source {_LIB}
        wf_load_plugin --workflows-dir '{workflows_dir}' 'nonsense'
    """)
    result = run_bash(tmp_path, script)
    assert result.returncode != 0


def test_unknown_workflow_type_error_names_the_type(tmp_path: pathlib.Path) -> None:
    """WF_LOAD_ERROR names the unknown workflow type when no plugin directory exists."""
    workflows_dir = tmp_path / "workflows"
    workflows_dir.mkdir()

    # Use || true so the echo runs even after wf_load_plugin returns non-zero.
    script = textwrap.dedent(f"""\
        source {_LIB}
        wf_load_plugin --workflows-dir '{workflows_dir}' 'nonsense' || true
        echo "ERROR: $WF_LOAD_ERROR"
    """)
    result = run_bash(tmp_path, script)
    # The error message should name the type.
    assert "nonsense" in result.stdout or "nonsense" in result.stderr


def test_unknown_workflow_type_does_not_default_to_release(tmp_path: pathlib.Path) -> None:
    """wf_load_plugin fails loud on unknown type; release is not silently assumed."""
    workflows_dir = tmp_path / "workflows"
    workflows_dir.mkdir()
    # Build a 'release' plugin to verify it is NOT silently used.
    _build_plugin(workflows_dir, "release", git_mode="rw")

    script = textwrap.dedent(f"""\
        source {_LIB}
        if wf_load_plugin --workflows-dir '{workflows_dir}' 'unknown-type'; then
            echo "WRONGLY_LOADED"
        else
            echo "CORRECTLY_BLOCKED"
        fi
    """)
    result = run_bash(tmp_path, script)
    assert result.returncode == 0  # script itself exits 0 (we handled the error)
    assert "CORRECTLY_BLOCKED" in result.stdout
    assert "WRONGLY_LOADED" not in result.stdout


# ---------------------------------------------------------------------------
# (b) status=scaffold routes to non-zero return naming the type
# ---------------------------------------------------------------------------


def test_scaffold_plugin_returns_nonzero(tmp_path: pathlib.Path) -> None:
    """wf_load_plugin returns non-zero when plugin manifest has status=scaffold."""
    workflows_dir = tmp_path / "workflows"
    _build_plugin(workflows_dir, "my-type", status="scaffold")

    script = textwrap.dedent(f"""\
        source {_LIB}
        wf_load_plugin --workflows-dir '{workflows_dir}' 'my-type'
    """)
    result = run_bash(tmp_path, script)
    assert result.returncode != 0


def test_scaffold_plugin_error_names_the_type(tmp_path: pathlib.Path) -> None:
    """WF_LOAD_ERROR names the scaffold workflow type."""
    workflows_dir = tmp_path / "workflows"
    _build_plugin(workflows_dir, "my-scaffold-type", status="scaffold")

    # Use || true so the echo runs even after wf_load_plugin returns non-zero.
    script = textwrap.dedent(f"""\
        source {_LIB}
        wf_load_plugin --workflows-dir '{workflows_dir}' 'my-scaffold-type' || true
        echo "ERROR: $WF_LOAD_ERROR"
    """)
    result = run_bash(tmp_path, script)
    assert "my-scaffold-type" in result.stdout or "my-scaffold-type" in result.stderr


def test_scaffold_plugin_wf_functions_still_return_nonzero(
    tmp_path: pathlib.Path,
) -> None:
    """After a scaffold failure, wf_git_mode stub still returns non-zero."""
    workflows_dir = tmp_path / "workflows"
    _build_plugin(workflows_dir, "scaffold-wf", status="scaffold")

    script = textwrap.dedent(f"""\
        source {_LIB}
        wf_load_plugin --workflows-dir '{workflows_dir}' 'scaffold-wf' || true
        wf_git_mode
    """)
    result = run_bash(tmp_path, script)
    # The stub should return non-zero.
    assert result.returncode != 0


# ---------------------------------------------------------------------------
# (d) Missing/invalid manifest routes to non-zero return
# ---------------------------------------------------------------------------


def test_missing_manifest_returns_nonzero(tmp_path: pathlib.Path) -> None:
    """wf_load_plugin returns non-zero when workflow.cfg is absent."""
    workflows_dir = tmp_path / "workflows"
    plugin_dir = workflows_dir / "no-manifest"
    plugin_dir.mkdir(parents=True)
    # No workflow.cfg written; no workflow.sh.

    script = textwrap.dedent(f"""\
        source {_LIB}
        wf_load_plugin --workflows-dir '{workflows_dir}' 'no-manifest'
    """)
    result = run_bash(tmp_path, script)
    assert result.returncode != 0


def test_missing_plugin_sh_returns_nonzero(tmp_path: pathlib.Path) -> None:
    """wf_load_plugin returns non-zero when workflow.sh is absent despite a valid manifest."""
    workflows_dir = tmp_path / "workflows"
    _build_plugin(workflows_dir, "no-plugin-sh", status="ready", include_plugin_sh=False)

    script = textwrap.dedent(f"""\
        source {_LIB}
        wf_load_plugin --workflows-dir '{workflows_dir}' 'no-plugin-sh'
    """)
    result = run_bash(tmp_path, script)
    assert result.returncode != 0


def test_manifest_missing_status_field_returns_nonzero(tmp_path: pathlib.Path) -> None:
    """wf_load_plugin returns non-zero when [workflow] status is absent from manifest."""
    workflows_dir = tmp_path / "workflows"
    plugin_dir = workflows_dir / "no-status"
    plugin_dir.mkdir(parents=True)

    # Write manifest without status field.
    manifest = plugin_dir / "workflow.cfg"
    manifest.write_text(
        "[workflow]\nname = no-status\ndescription = missing status\n"
        "\n[capabilities]\ngit_mode = none\nagents = pm\n",
        encoding="utf-8",
    )
    # Write minimal plugin.sh.
    plugin_sh = plugin_dir / "workflow.sh"
    plugin_sh.write_text("#!/usr/bin/env bash\n", encoding="utf-8")

    script = textwrap.dedent(f"""\
        source {_LIB}
        wf_load_plugin --workflows-dir '{workflows_dir}' 'no-status'
    """)
    result = run_bash(tmp_path, script)
    assert result.returncode != 0


def test_manifest_invalid_git_mode_returns_nonzero(tmp_path: pathlib.Path) -> None:
    """wf_load_plugin returns non-zero when [capabilities] git_mode is invalid."""
    workflows_dir = tmp_path / "workflows"
    _build_plugin(workflows_dir, "bad-git-mode", status="ready", git_mode="invalid-mode")

    script = textwrap.dedent(f"""\
        source {_LIB}
        wf_load_plugin --workflows-dir '{workflows_dir}' 'bad-git-mode'
    """)
    result = run_bash(tmp_path, script)
    assert result.returncode != 0


# ---------------------------------------------------------------------------
# (a) Valid ready plugin loads and dispatches to its hooks
# ---------------------------------------------------------------------------


def test_valid_ready_plugin_returns_zero(tmp_path: pathlib.Path) -> None:
    """wf_load_plugin returns 0 when the plugin has status=ready and valid manifest."""
    workflows_dir = tmp_path / "workflows"
    _build_plugin(workflows_dir, "valid-type", status="ready", git_mode="rw")

    script = textwrap.dedent(f"""\
        source {_LIB}
        wf_load_plugin --workflows-dir '{workflows_dir}' 'valid-type'
    """)
    result = run_bash(tmp_path, script)
    assert result.returncode == 0


def test_valid_plugin_sets_wf_plugin_dir(tmp_path: pathlib.Path) -> None:
    """wf_load_plugin sets WF_PLUGIN_DIR to the plugin directory on success."""
    workflows_dir = tmp_path / "workflows"
    _build_plugin(workflows_dir, "dir-check", status="ready", git_mode="none")
    expected_dir = str(workflows_dir / "dir-check")

    script = textwrap.dedent(f"""\
        source {_LIB}
        wf_load_plugin --workflows-dir '{workflows_dir}' 'dir-check'
        echo "$WF_PLUGIN_DIR"
    """)
    result = run_bash(tmp_path, script)
    assert result.returncode == 0
    assert result.stdout.strip() == expected_dir


def test_valid_plugin_wf_git_mode_returns_declared_value(tmp_path: pathlib.Path) -> None:
    """After loading a ready plugin, wf_git_mode returns the git_mode from the manifest."""
    workflows_dir = tmp_path / "workflows"
    _build_plugin(workflows_dir, "git-ro-type", status="ready", git_mode="ro")

    script = textwrap.dedent(f"""\
        source {_LIB}
        wf_load_plugin --workflows-dir '{workflows_dir}' 'git-ro-type'
        wf_git_mode
    """)
    result = run_bash(tmp_path, script)
    assert result.returncode == 0
    assert result.stdout.strip() == "ro"


def test_valid_plugin_wf_agents_returns_declared_roster(tmp_path: pathlib.Path) -> None:
    """After loading a ready plugin, wf_agents returns the agent roster."""
    workflows_dir = tmp_path / "workflows"
    _build_plugin(
        workflows_dir, "agent-check", status="ready", git_mode="none", agents="pm,tester"
    )

    script = textwrap.dedent(f"""\
        source {_LIB}
        wf_load_plugin --workflows-dir '{workflows_dir}' 'agent-check'
        wf_agents
    """)
    result = run_bash(tmp_path, script)
    assert result.returncode == 0
    assert result.stdout.strip() == "pm,tester"


def test_valid_plugin_wf_type_is_set(tmp_path: pathlib.Path) -> None:
    """wf_load_plugin sets WF_TYPE to the resolved workflow type string."""
    workflows_dir = tmp_path / "workflows"
    _build_plugin(workflows_dir, "type-var-check", status="ready", git_mode="none")

    script = textwrap.dedent(f"""\
        source {_LIB}
        wf_load_plugin --workflows-dir '{workflows_dir}' 'type-var-check'
        echo "$WF_TYPE"
    """)
    result = run_bash(tmp_path, script)
    assert result.returncode == 0
    assert result.stdout.strip() == "type-var-check"


def test_valid_plugin_all_hooks_callable(tmp_path: pathlib.Path) -> None:
    """All eight wf_* hooks are callable after a successful load."""
    workflows_dir = tmp_path / "workflows"
    _build_plugin(workflows_dir, "all-hooks", status="ready", git_mode="none")

    script = textwrap.dedent(f"""\
        source {_LIB}
        wf_load_plugin --workflows-dir '{workflows_dir}' 'all-hooks'
        wf_resolve_target_version
        wf_git_mode
        wf_pre_task
        wf_post_task
        wf_finalize
        wf_agents
        wf_bundle_source_branch
        wf_dashboard_render
    """)
    result = run_bash(tmp_path, script)
    assert result.returncode == 0


# ---------------------------------------------------------------------------
# Stubs return non-zero before wf_load_plugin is called
# ---------------------------------------------------------------------------


def test_stubs_return_nonzero_before_load(tmp_path: pathlib.Path) -> None:
    """All wf_* stub functions return non-zero when called before wf_load_plugin."""
    hooks = [
        "wf_resolve_target_version",
        "wf_git_mode",
        "wf_pre_task",
        "wf_post_task",
        "wf_finalize",
        "wf_agents",
        "wf_bundle_source_branch",
        "wf_dashboard_render",
    ]
    for hook in hooks:
        script = f"source {_LIB} && {hook}"
        result = run_bash(tmp_path, script)
        assert result.returncode != 0, (
            f"Expected {hook} to return non-zero before wf_load_plugin, "
            f"but got returncode={result.returncode}"
        )


# ---------------------------------------------------------------------------
# Workflows root resolution
# ---------------------------------------------------------------------------


def test_explicit_workflows_dir_takes_precedence(tmp_path: pathlib.Path) -> None:
    """--workflows-dir argument is used over the environment fallback."""
    # Build plugin in an explicit dir.
    explicit_dir = tmp_path / "explicit_workflows"
    _build_plugin(explicit_dir, "explicit-type", status="ready", git_mode="none")

    # Build a different plugin in a 'live install' location that should NOT be used.
    kanban_root = tmp_path / "kanban"
    (kanban_root / "workflows").mkdir(parents=True)

    script = textwrap.dedent(f"""\
        source {_LIB}
        wf_load_plugin --workflows-dir '{explicit_dir}' 'explicit-type'
        echo "LOADED_FROM: $WF_PLUGIN_DIR"
    """)
    result = run_bash(
        tmp_path,
        script,
        extra_env={"PGAI_AGENT_KANBAN_ROOT_PATH": str(kanban_root)},
    )
    assert result.returncode == 0
    assert str(explicit_dir) in result.stdout


def test_live_install_path_used_when_present(tmp_path: pathlib.Path) -> None:
    """Tier 2: $PGAI_AGENT_KANBAN_ROOT_PATH/workflows/ is used when it exists."""
    kanban_root = tmp_path / "kanban_root"
    live_workflows = kanban_root / "workflows"
    _build_plugin(live_workflows, "live-type", status="ready", git_mode="rw")

    script = textwrap.dedent(f"""\
        source {_LIB}
        wf_load_plugin 'live-type'
        echo "LOADED: $WF_PLUGIN_DIR"
    """)
    result = run_bash(
        tmp_path,
        script,
        extra_env={"PGAI_AGENT_KANBAN_ROOT_PATH": str(kanban_root)},
    )
    assert result.returncode == 0
    assert "live-type" in result.stdout


def test_nonexistent_explicit_dir_returns_nonzero(tmp_path: pathlib.Path) -> None:
    """wf_load_plugin returns non-zero when explicit --workflows-dir does not exist."""
    script = textwrap.dedent(f"""\
        source {_LIB}
        wf_load_plugin --workflows-dir '{tmp_path}/does-not-exist' 'any-type'
    """)
    result = run_bash(tmp_path, script)
    assert result.returncode != 0
