"""
test_create_new_workflow.py
===========================
Unit tests for the create_new_workflow generator.

Covers the six scenarios required by the task spec:

(a) Generator refuses shipped type names (release, document, testing-only)
    with clear errors.
(b) Generator refuses an existing directory without --force, and overwrites
    cleanly with --force.
(c) Generator on a fixture LIVE-INSTALL layout (no team/ prefix, no .git)
    creates <root>/workflows/<name>/.
(d) Generator on a dev-tree layout creates team/workflows/<name>/.
(e) Explicit --workflows-dir wins over both defaults.
(f) Stub hooks exit non-zero with 'NOT IMPLEMENTED' when invoked.

TEST NAMING CONVENTION
----------------------
All test function names describe behavior, not the task ID or bug ID that
prompted them.
"""

from __future__ import annotations

import os
import pathlib
import stat
import subprocess
import sys

import pytest

# ---------------------------------------------------------------------------
# Import the module under test
# ---------------------------------------------------------------------------

# Add the team/ directory to sys.path so the package can be imported without
# an installed package.  The tests/ conftest.py handles path setup for the
# main test suite; this import guard covers the module directly.
_TEAM_DIR = pathlib.Path(__file__).parent.parent.parent
if str(_TEAM_DIR) not in sys.path:
    sys.path.insert(0, str(_TEAM_DIR))

from pgai_agent_kanban.workflows.create_new_workflow import (  # noqa: E402
    SHIPPED_WORKFLOW_TYPES,
    WorkflowGeneratorError,
    _cli_main,
    _resolve_workflows_dir,
    create_new_workflow,
)


# ---------------------------------------------------------------------------
# Shared test helpers
# ---------------------------------------------------------------------------


def _minimal_kwargs(**overrides):
    """Return a minimal valid keyword-argument dict for create_new_workflow."""
    defaults = dict(
        description="Test workflow description",
        version_semantics="semver",
        git_mode="none",
        agents="pm,coder,tester",
        finalize="tag",
    )
    defaults.update(overrides)
    return defaults


def _scaffold_in(tmp_path: pathlib.Path, name: str = "acme-test") -> pathlib.Path:
    """Scaffold a workflow under tmp_path/workflows/ and return the plugin dir."""
    workflows_root = tmp_path / "workflows"
    workflows_root.mkdir(parents=True, exist_ok=True)
    return create_new_workflow(name, **_minimal_kwargs(), workflows_dir=workflows_root)


# ---------------------------------------------------------------------------
# (a) Refuses shipped type names
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("shipped_name", sorted(SHIPPED_WORKFLOW_TYPES))
def test_generator_refuses_shipped_type_names(
    tmp_path: pathlib.Path,
    shipped_name: str,
) -> None:
    """Generator raises WorkflowGeneratorError for each shipped workflow type name.

    The refusal must include the type name in the error message so the operator
    knows which name triggered it.
    """
    workflows_root = tmp_path / "workflows"
    workflows_root.mkdir()

    with pytest.raises(WorkflowGeneratorError) as exc_info:
        create_new_workflow(
            shipped_name,
            **_minimal_kwargs(),
            workflows_dir=workflows_root,
        )

    # Error message must name the refused type.
    assert shipped_name in str(exc_info.value), (
        f"WorkflowGeneratorError message did not mention the refused name "
        f"{shipped_name!r}: {exc_info.value}"
    )


def test_generator_refuses_shipped_name_via_cli(tmp_path: pathlib.Path) -> None:
    """CLI wrapper exits non-zero when a shipped type name is passed as --name."""
    workflows_root = tmp_path / "workflows"
    workflows_root.mkdir()

    exit_code = _cli_main(
        [
            "--name", "release",
            "--workflows-dir", str(workflows_root),
        ]
    )

    assert exit_code != 0, (
        "CLI should exit non-zero when --name release (a shipped type) is given"
    )


def test_non_shipped_name_is_accepted(tmp_path: pathlib.Path) -> None:
    """Generator accepts a custom name that is not in the shipped-type list."""
    workflows_root = tmp_path / "workflows"
    workflows_root.mkdir()

    plugin_dir = create_new_workflow(
        "acme-custom",
        **_minimal_kwargs(),
        workflows_dir=workflows_root,
    )

    assert plugin_dir.is_dir(), "Plugin directory was not created for a custom name"


# ---------------------------------------------------------------------------
# (b) Refuses existing directory without --force; overwrites cleanly with --force
# ---------------------------------------------------------------------------


def test_generator_refuses_existing_directory_without_force(
    tmp_path: pathlib.Path,
) -> None:
    """Generator raises WorkflowGeneratorError when the target directory exists.

    Without --force the generator must fail loudly rather than silently
    overwriting operator-authored hook implementations.
    """
    # First scaffold succeeds.
    _scaffold_in(tmp_path, name="acme-dup")

    # Second scaffold without force must fail.
    workflows_root = tmp_path / "workflows"
    with pytest.raises(WorkflowGeneratorError) as exc_info:
        create_new_workflow(
            "acme-dup",
            **_minimal_kwargs(),
            workflows_dir=workflows_root,
        )

    assert "already exists" in str(exc_info.value).lower(), (
        "WorkflowGeneratorError message should mention that the directory exists"
    )


def test_generator_overwrites_existing_directory_with_force(
    tmp_path: pathlib.Path,
) -> None:
    """Generator replaces an existing plugin directory when force=True.

    After the overwrite the resulting directory must contain the freshly
    scaffolded files; the original files (if any) are gone.
    """
    workflows_root = tmp_path / "workflows"
    workflows_root.mkdir()

    # First scaffold.
    plugin_dir = create_new_workflow(
        "acme-overwrite",
        **_minimal_kwargs(),
        workflows_dir=workflows_root,
    )
    # Plant a sentinel file that should vanish after --force.
    sentinel = plugin_dir / "operator_note.txt"
    sentinel.write_text("do not keep", encoding="utf-8")

    # Second scaffold with force=True.
    plugin_dir2 = create_new_workflow(
        "acme-overwrite",
        **_minimal_kwargs(),
        workflows_dir=workflows_root,
        force=True,
    )

    assert plugin_dir2 == plugin_dir, "Returned path should be identical after --force"
    assert not sentinel.exists(), (
        "Sentinel file from the first scaffold should be gone after --force overwrite"
    )
    assert (plugin_dir2 / "workflow.cfg").exists(), (
        "workflow.cfg must be recreated after --force overwrite"
    )
    assert (plugin_dir2 / "workflow.sh").exists(), (
        "workflow.sh must be recreated after --force overwrite"
    )


def test_generator_force_via_cli(tmp_path: pathlib.Path) -> None:
    """CLI --force flag passes through to the library and overwrites cleanly."""
    workflows_root = tmp_path / "workflows"
    workflows_root.mkdir()

    # First scaffold via library.
    create_new_workflow("acme-cli-force", **_minimal_kwargs(), workflows_dir=workflows_root)

    # Second scaffold via CLI with --force.
    exit_code = _cli_main(
        [
            "--name", "acme-cli-force",
            "--force",
            "--workflows-dir", str(workflows_root),
        ]
    )

    assert exit_code == 0, "CLI --force should exit 0 even when directory exists"


# ---------------------------------------------------------------------------
# (c) Fixture LIVE-INSTALL layout: no team/ prefix, no .git
# ---------------------------------------------------------------------------


def test_generator_on_live_install_layout_creates_under_workflows_root(
    tmp_path: pathlib.Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Generator on a fixture live-install root creates <root>/workflows/<name>/.

    A live install has PGAI_AGENT_KANBAN_ROOT_PATH set and no .git directory.
    The generator must use $PGAI_AGENT_KANBAN_ROOT_PATH/workflows/ as the root
    without requiring an explicit --workflows-dir argument.
    """
    # Simulate a live-install kanban root (no .git, no team/ directory).
    live_root = tmp_path / "live_kanban"
    live_root.mkdir()
    (live_root / "workflows").mkdir()

    # Point the env var at our fixture root.
    monkeypatch.setenv("PGAI_AGENT_KANBAN_ROOT_PATH", str(live_root))

    # Run with no explicit workflows_dir — must pick up from env var.
    plugin_dir = create_new_workflow("acme-live", **_minimal_kwargs())

    expected = live_root / "workflows" / "acme-live"
    assert plugin_dir == expected, (
        f"Expected plugin at {expected}, got {plugin_dir}"
    )
    assert (plugin_dir / "workflow.cfg").is_file(), "workflow.cfg must exist"
    assert (plugin_dir / "workflow.sh").is_file(), "workflow.sh must exist"
    assert (plugin_dir / "contract_check.sh").is_file(), "contract_check.sh must exist"


def test_live_install_layout_second_run_refuses_without_force(
    tmp_path: pathlib.Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Second run against the same live-install target refuses without --force."""
    live_root = tmp_path / "live_kanban2"
    live_root.mkdir()
    (live_root / "workflows").mkdir()

    monkeypatch.setenv("PGAI_AGENT_KANBAN_ROOT_PATH", str(live_root))

    # First run succeeds.
    create_new_workflow("acme-live-dup", **_minimal_kwargs())

    # Second run without force must fail.
    with pytest.raises(WorkflowGeneratorError):
        create_new_workflow("acme-live-dup", **_minimal_kwargs())


def test_live_install_layout_second_run_succeeds_with_force(
    tmp_path: pathlib.Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Second run against the same live-install target succeeds with force=True."""
    live_root = tmp_path / "live_kanban3"
    live_root.mkdir()
    (live_root / "workflows").mkdir()

    monkeypatch.setenv("PGAI_AGENT_KANBAN_ROOT_PATH", str(live_root))

    create_new_workflow("acme-live-force", **_minimal_kwargs())
    plugin_dir = create_new_workflow("acme-live-force", **_minimal_kwargs(), force=True)

    assert (plugin_dir / "workflow.cfg").is_file()


# ---------------------------------------------------------------------------
# (d) Dev-tree layout: creates team/workflows/<name>/
# ---------------------------------------------------------------------------


def test_generator_on_dev_tree_layout_uses_team_workflows(
    tmp_path: pathlib.Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Generator resolves team/workflows/ when running inside the dev tree.

    When no --workflows-dir is given and PGAI_AGENT_KANBAN_ROOT_PATH is not
    set, and the CWD is inside a git repo that has team/workflows/, the
    generator uses <git-root>/team/workflows/ as the output root.

    This test creates a minimal synthetic git repo with team/workflows/ to
    simulate the dev-tree condition without requiring the real repo on PATH.
    """
    # Build a minimal git repo with team/workflows/.
    fake_repo = tmp_path / "fake_repo"
    fake_repo.mkdir()
    team_workflows = fake_repo / "team" / "workflows"
    team_workflows.mkdir(parents=True)

    subprocess.run(
        ["git", "init", "--initial-branch=main", str(fake_repo)],
        check=True,
        capture_output=True,
    )
    subprocess.run(
        ["git", "config", "user.email", "test@example.com"],
        cwd=str(fake_repo),
        check=True,
        capture_output=True,
    )
    subprocess.run(
        ["git", "config", "user.name", "Test"],
        cwd=str(fake_repo),
        check=True,
        capture_output=True,
    )
    (fake_repo / "README.md").write_text("test repo", encoding="utf-8")
    subprocess.run(
        ["git", "add", "README.md"],
        cwd=str(fake_repo),
        check=True,
        capture_output=True,
    )
    subprocess.run(
        ["git", "commit", "-m", "init"],
        cwd=str(fake_repo),
        check=True,
        capture_output=True,
    )

    # Remove the env var so fallback resolution kicks in.
    monkeypatch.delenv("PGAI_AGENT_KANBAN_ROOT_PATH", raising=False)

    # Change CWD to inside the fake repo.
    monkeypatch.chdir(fake_repo)

    plugin_dir = create_new_workflow("acme-dev", **_minimal_kwargs())

    expected = team_workflows / "acme-dev"
    assert plugin_dir == expected, (
        f"Expected dev-tree plugin at {expected}, got {plugin_dir}"
    )
    assert (plugin_dir / "workflow.cfg").is_file()


# ---------------------------------------------------------------------------
# (e) Explicit --workflows-dir wins over both defaults
# ---------------------------------------------------------------------------


def test_explicit_workflows_dir_wins_over_env_var(
    tmp_path: pathlib.Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Explicit --workflows-dir argument takes priority over PGAI_AGENT_KANBAN_ROOT_PATH.

    Even when the env var points to a valid live-install root, an explicit
    --workflows-dir must be used instead.
    """
    # Set up an env-var-pointing root (should be ignored).
    env_root = tmp_path / "env_kanban"
    env_root.mkdir()
    (env_root / "workflows").mkdir()
    monkeypatch.setenv("PGAI_AGENT_KANBAN_ROOT_PATH", str(env_root))

    # Set up an explicit workflows dir (should win).
    explicit_root = tmp_path / "explicit_workflows"
    explicit_root.mkdir()

    plugin_dir = create_new_workflow(
        "acme-explicit",
        **_minimal_kwargs(),
        workflows_dir=explicit_root,
    )

    expected = explicit_root / "acme-explicit"
    assert plugin_dir == expected, (
        f"Expected plugin at explicit_root/{expected.name}, got {plugin_dir}"
    )
    # Must NOT appear under the env-var root.
    env_plugin = env_root / "workflows" / "acme-explicit"
    assert not env_plugin.exists(), (
        "Plugin must not be created under PGAI_AGENT_KANBAN_ROOT_PATH when "
        "explicit --workflows-dir is given"
    )


def test_explicit_workflows_dir_via_cli_wins(tmp_path: pathlib.Path) -> None:
    """CLI --workflows-dir flag routes output to the explicit directory."""
    explicit_root = tmp_path / "cli_explicit"
    explicit_root.mkdir()

    exit_code = _cli_main(
        [
            "--name", "acme-cli-explicit",
            "--workflows-dir", str(explicit_root),
        ]
    )

    assert exit_code == 0, "CLI should succeed with explicit --workflows-dir"
    plugin_dir = explicit_root / "acme-cli-explicit"
    assert plugin_dir.is_dir(), "Plugin directory must be created at --workflows-dir"


# ---------------------------------------------------------------------------
# (f) Stub hooks exit non-zero with 'NOT IMPLEMENTED' when invoked
# ---------------------------------------------------------------------------


def test_stub_hooks_exit_nonzero_with_not_implemented(tmp_path: pathlib.Path) -> None:
    """Each wf_* stub in a scaffolded workflow.sh exits non-zero with NOT IMPLEMENTED.

    This verifies the fail-closed behavior: an operator who drops a scaffold
    plugin into a live install gets a loud failure from every hook, not silent
    wrong behavior from a default implementation.
    """
    plugin_dir = _scaffold_in(tmp_path, name="acme-stubs")
    workflow_sh = plugin_dir / "workflow.sh"

    hook_names = [
        "wf_git_mode",
        "wf_resolve_target_version",
        "wf_pre_task",
        "wf_post_task",
        "wf_finalize",
        "wf_agents",
        "wf_bundle_source_branch",
        "wf_dashboard_render",
    ]

    for hook in hook_names:
        result = subprocess.run(
            ["bash", "-c", f"source {workflow_sh}; {hook}"],
            capture_output=True,
            text=True,
        )
        assert result.returncode != 0, (
            f"Hook {hook} exited 0; expected non-zero (NOT IMPLEMENTED stub)"
        )
        assert "NOT IMPLEMENTED" in result.stderr, (
            f"Hook {hook} stderr did not contain 'NOT IMPLEMENTED': "
            f"{result.stderr!r}"
        )


def test_workflow_sh_is_executable(tmp_path: pathlib.Path) -> None:
    """workflow.sh is created with executable permission bits."""
    plugin_dir = _scaffold_in(tmp_path, name="acme-exec")
    workflow_sh = plugin_dir / "workflow.sh"
    mode = workflow_sh.stat().st_mode
    assert mode & stat.S_IXUSR, "workflow.sh must have owner execute bit set"


def test_contract_check_sh_is_executable(tmp_path: pathlib.Path) -> None:
    """contract_check.sh is created with executable permission bits."""
    plugin_dir = _scaffold_in(tmp_path, name="acme-contract-exec")
    contract_sh = plugin_dir / "contract_check.sh"
    mode = contract_sh.stat().st_mode
    assert mode & stat.S_IXUSR, "contract_check.sh must have owner execute bit set"


# ---------------------------------------------------------------------------
# Scaffold structure checks
# ---------------------------------------------------------------------------


def test_scaffold_creates_workflow_cfg_with_scaffold_status(
    tmp_path: pathlib.Path,
) -> None:
    """Scaffolded workflow.cfg has status = scaffold in the [workflow] section."""
    plugin_dir = _scaffold_in(tmp_path, name="acme-cfg-status")
    cfg_text = (plugin_dir / "workflow.cfg").read_text(encoding="utf-8")

    assert "status = scaffold" in cfg_text, (
        "workflow.cfg must contain 'status = scaffold' after scaffolding"
    )


def test_scaffold_creates_workflow_cfg_with_correct_name(
    tmp_path: pathlib.Path,
) -> None:
    """Scaffolded workflow.cfg contains the workflow name in the [workflow] section."""
    plugin_dir = _scaffold_in(tmp_path, name="acme-name-check")
    cfg_text = (plugin_dir / "workflow.cfg").read_text(encoding="utf-8")

    assert "name = acme-name-check" in cfg_text, (
        "workflow.cfg must contain the workflow name"
    )


def test_scaffold_creates_workflow_cfg_with_capabilities(
    tmp_path: pathlib.Path,
) -> None:
    """Scaffolded workflow.cfg contains all capability keys from the call."""
    workflows_root = tmp_path / "workflows"
    workflows_root.mkdir()
    plugin_dir = create_new_workflow(
        "acme-caps",
        description="My capability test",
        version_semantics="label",
        git_mode="ro",
        agents="pm,tester",
        finalize="report",
        workflows_dir=workflows_root,
    )
    cfg_text = (plugin_dir / "workflow.cfg").read_text(encoding="utf-8")

    assert "version_semantics = label" in cfg_text
    assert "git_mode = ro" in cfg_text
    assert "finalize = report" in cfg_text
    assert "agents = pm,tester" in cfg_text


def test_scaffold_workflow_sh_contains_all_hook_stubs(tmp_path: pathlib.Path) -> None:
    """Scaffolded workflow.sh defines all standard wf_* hooks."""
    plugin_dir = _scaffold_in(tmp_path, name="acme-all-hooks")
    workflow_sh_text = (plugin_dir / "workflow.sh").read_text(encoding="utf-8")

    expected_hooks = [
        "wf_git_mode",
        "wf_resolve_target_version",
        "wf_pre_task",
        "wf_post_task",
        "wf_finalize",
        "wf_agents",
        "wf_bundle_source_branch",
        "wf_dashboard_render",
    ]
    for hook in expected_hooks:
        assert f"{hook}()" in workflow_sh_text, (
            f"workflow.sh should contain a function definition for {hook}"
        )


def test_scaffold_contract_check_sh_is_present(tmp_path: pathlib.Path) -> None:
    """Scaffolded plugin directory contains contract_check.sh."""
    plugin_dir = _scaffold_in(tmp_path, name="acme-contract")
    assert (plugin_dir / "contract_check.sh").is_file(), (
        "contract_check.sh must be created in the plugin directory"
    )


# ---------------------------------------------------------------------------
# contract_check.sh behavior
# ---------------------------------------------------------------------------


def test_contract_check_sh_fails_on_scaffold_status(tmp_path: pathlib.Path) -> None:
    """contract_check.sh exits non-zero on a freshly scaffolded plugin.

    The scaffold plugin has status = scaffold and NOT IMPLEMENTED stubs,
    so the contract check must fail (the operator must implement hooks and
    flip status = ready first).
    """
    plugin_dir = _scaffold_in(tmp_path, name="acme-cc-scaffold")
    result = subprocess.run(
        ["bash", str(plugin_dir / "contract_check.sh")],
        capture_output=True,
        text=True,
    )
    assert result.returncode != 0, (
        "contract_check.sh must exit non-zero on a scaffold (status=scaffold, stubs present)"
    )


def test_contract_check_sh_passes_on_implemented_plugin(tmp_path: pathlib.Path) -> None:
    """contract_check.sh exits 0 after hooks are implemented and status flipped to ready.

    This test simulates the operator path: scaffold, implement all hooks,
    flip status = ready, run the contract check.
    """
    plugin_dir = _scaffold_in(tmp_path, name="acme-cc-pass")

    # Replace the stub workflow.sh with fully implemented no-op hooks.
    implemented_sh = plugin_dir / "workflow.sh"
    implemented_sh.write_text(
        "#!/usr/bin/env bash\n"
        "wf_git_mode() { echo none; }\n"
        "wf_resolve_target_version() { echo \"${1:-}\"; }\n"
        "wf_pre_task() { return 0; }\n"
        "wf_post_task() { return 0; }\n"
        "wf_finalize() { echo tag; }\n"
        "wf_agents() { echo pm,tester; }\n"
        "wf_bundle_source_branch() { echo main; }\n"
        "wf_dashboard_render() { echo ''; }\n",
        encoding="utf-8",
    )

    # Flip status from scaffold to ready.
    cfg = plugin_dir / "workflow.cfg"
    cfg_text = cfg.read_text(encoding="utf-8").replace(
        "status = scaffold", "status = ready"
    )
    cfg.write_text(cfg_text, encoding="utf-8")

    result = subprocess.run(
        ["bash", str(plugin_dir / "contract_check.sh")],
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, (
        f"contract_check.sh should pass after all hooks implemented and status=ready.\n"
        f"stdout: {result.stdout!r}\nstderr: {result.stderr!r}"
    )


# ---------------------------------------------------------------------------
# CLI output
# ---------------------------------------------------------------------------


def test_cli_prints_scaffolded_path_on_success(
    tmp_path: pathlib.Path,
    capsys: pytest.CaptureFixture,
) -> None:
    """CLI prints the scaffolded plugin directory path on successful run."""
    explicit_root = tmp_path / "cli_out"
    explicit_root.mkdir()

    exit_code = _cli_main(
        [
            "--name", "acme-output",
            "--workflows-dir", str(explicit_root),
        ]
    )

    assert exit_code == 0
    captured = capsys.readouterr()
    assert "acme-output" in captured.out, (
        "CLI stdout should mention the scaffolded plugin name"
    )


def test_cli_prints_error_to_stderr_on_refused_name(
    tmp_path: pathlib.Path,
    capsys: pytest.CaptureFixture,
) -> None:
    """CLI writes the error message to stderr when the name is refused."""
    explicit_root = tmp_path / "cli_err"
    explicit_root.mkdir()

    exit_code = _cli_main(
        [
            "--name", "document",
            "--workflows-dir", str(explicit_root),
        ]
    )

    assert exit_code != 0
    captured = capsys.readouterr()
    assert "error" in captured.err.lower() or "document" in captured.err, (
        "CLI stderr should contain error information when a shipped name is refused"
    )
