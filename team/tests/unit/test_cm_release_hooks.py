"""
test_cm_release_hooks.py
========================
Behavioral unit tests for the CM release hook resolution, printing, and
enforcement subsystem introduced by CODER-20260711-027-resolver-print-enforce-lib.

Covers:
  (a) Precedence: cfg > kanban-side > in-repo — all three tiers planted, then
      peeled back one at a time.
  (b) Portability: in-repo hook survives remove-project + create-project in a
      sandbox (i.e. it lives in the dev tree, not the kanban root, so kanban-
      side recreation does not affect it).
  (c) Required=true + no hook at any tier → HALT before the phase.
  (d) Required=false + no hook → 'none configured' printed, proceeds normally.
  (e) Non-executable in-repo hook → loud error naming the path and chmod fix;
      does NOT silently skip.
  (f) Existing kanban-side hook behavior is preserved (regression lock).

All tests use the shell_harness.run_bash pattern with synthetic temp directories.
No live kanban root is touched.
"""

from __future__ import annotations

import pathlib
import stat

import pytest

from tests.unit.shell_harness import run_bash

# Paths to the libraries under test (relative to team/, where pytest runs from).
_LIB_PROJECT_PATHS = "scripts/lib/project_paths.sh"
_LIB_PROJECTS = "scripts/lib/projects.sh"
_LIB_HOOKS = "scripts/lib/cm_release_hooks.sh"

# Source preamble that loads the two prerequisites before cm_release_hooks.sh.
_SOURCE_PREAMBLE = (
    f"source {_LIB_PROJECT_PATHS} && "
    f"source {_LIB_PROJECTS} && "
    f"source {_LIB_HOOKS}"
)

# A minimal cm_halt stub: prints HALT info to stdout (so tests can inspect it)
# and exits 2 to signal the halt path was taken.
_CM_HALT_STUB = (
    "cm_halt() { "
    "  echo \"HALT: trigger=$1 reason=$2\"; "
    "  exit 2; "
    "}"
)


def _make_sandbox(
    root: pathlib.Path,
    project_name: str,
    dev_tree: pathlib.Path | None = None,
) -> tuple[pathlib.Path, pathlib.Path, pathlib.Path]:
    """
    Create a minimal sandbox kanban tree and an optional dev tree.

    Returns (kanban_root, project_kanban_dir, dev_tree_path).
    dev_tree_path is created at root/dev_tree/<project_name> unless supplied.
    """
    kanban_root = root / "kanban"
    kanban_root.mkdir(parents=True, exist_ok=True)

    # Kanban-side project directory
    project_kanban_dir = kanban_root / "projects" / project_name
    project_kanban_dir.mkdir(parents=True, exist_ok=True)

    # Dev tree (simulated git checkout)
    if dev_tree is None:
        dev_tree = root / "dev" / project_name
    dev_tree.mkdir(parents=True, exist_ok=True)

    # Write a minimal project.cfg
    cfg_path = project_kanban_dir / "project.cfg"
    cfg_path.write_text(
        "[project]\n"
        f"project_name = {project_name}\n"
        f"dev_tree_path = {dev_tree}\n",
        encoding="utf-8",
    )

    return kanban_root, project_kanban_dir, dev_tree


def _write_hook(path: pathlib.Path, executable: bool = True, content: str = "#!/bin/sh\n") -> None:
    """Write a hook script at *path*, optionally marking it executable."""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")
    if executable:
        path.chmod(path.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)


def _run_resolve(
    tmp_path: pathlib.Path,
    kanban_root: pathlib.Path,
    project_name: str,
    project_hooks_dir: pathlib.Path,
    dev_tree_path: pathlib.Path,
    phase: str = "pre-squash",
) -> object:
    """
    Call cm_resolve_and_enforce_hook and return the BashResult.

    The script echoes CM_RESOLVED_HOOK_PATH on a separate line so tests can
    parse the resolution line and the path separately.

    Exit code propagation:
      - cm_halt stub exits 2
      - non-executable in-repo hook: cm_resolve_and_enforce_hook returns 1;
        the script stores rc and exits with it
    """
    script = (
        f"{_CM_HALT_STUB}\n"
        f"{_SOURCE_PREAMBLE}\n"
        f"_rc=0\n"
        f"cm_resolve_and_enforce_hook {project_name!r} {phase!r} "
        f"{str(project_hooks_dir)!r} {str(dev_tree_path)!r} || _rc=$?\n"
        f"echo \"RESOLVED_PATH:$CM_RESOLVED_HOOK_PATH\"\n"
        f"exit $_rc\n"
    )
    return run_bash(
        tmp_path,
        script,
        extra_env={"KANBAN_ROOT": str(kanban_root)},
    )


def _run_resolver_func(
    tmp_path: pathlib.Path,
    kanban_root: pathlib.Path,
    project_name: str,
    phase: str = "pre-squash",
) -> object:
    """
    Call projects_resolve_release_hook_path (in projects.sh) and echo both
    the path and _PGAI_HOOK_LAST_SOURCE.

    IMPORTANT: _PGAI_HOOK_LAST_SOURCE is a module-level global set by
    projects_resolve_release_hook_path as a side effect.  We must NOT call
    the function inside a $(...) command substitution — that runs it in a
    subshell and the side-effect variable is lost in the parent.

    Instead, we redirect the function's stdout to a temp file, then read
    that file into a variable in the parent shell.
    """
    # Use a temp file path based on tmp_path to avoid bare /tmp usage.
    tmpfile = str(tmp_path / "_resolver_output.txt")
    script = (
        f"source {_LIB_PROJECT_PATHS} && source {_LIB_PROJECTS}\n"
        # Run in the current shell (no subshell) — redirect stdout to temp file.
        f"projects_resolve_release_hook_path {project_name!r} {phase!r} > {tmpfile!r} 2>&1\n"
        f"path=$(cat {tmpfile!r})\n"
        f"echo \"PATH:$path\"\n"
        f"echo \"SOURCE:$_PGAI_HOOK_LAST_SOURCE\"\n"
    )
    return run_bash(
        tmp_path,
        script,
        extra_env={"KANBAN_ROOT": str(kanban_root)},
    )


# ---------------------------------------------------------------------------
# (a) Precedence: all three tiers
# ---------------------------------------------------------------------------


def test_precedence_cfg_wins_when_all_three_present(tmp_path: pathlib.Path) -> None:
    """Tier (a) cfg wins when all three tiers have a hook file."""
    kanban_root, project_dir, dev_tree = _make_sandbox(tmp_path, "my-proj")

    # Tier (a): write path into project.cfg
    cfg_hook = dev_tree / "hooks" / "cfg-hook.sh"
    _write_hook(cfg_hook)
    cfg_text = (
        "[project]\n"
        f"project_name = my-proj\n"
        f"dev_tree_path = {dev_tree}\n"
        "[hooks]\n"
        f"cm_release_pre_squash_hook = hooks/cfg-hook.sh\n"
    )
    (project_dir / "project.cfg").write_text(cfg_text, encoding="utf-8")

    # Tier (b): kanban-side hook
    kanban_hook = project_dir / "hooks" / "cm-release-pre-squash.sh"
    _write_hook(kanban_hook)

    # Tier (c): in-repo hook
    inrepo_hook = dev_tree / ".pgai" / "hooks" / "cm-release-pre-squash.sh"
    _write_hook(inrepo_hook)

    result = _run_resolve(tmp_path, kanban_root, "my-proj", project_dir / "hooks", dev_tree)
    assert result.returncode == 0
    # Resolution line must name the cfg path and source: cfg
    assert "source: cfg" in result.stdout
    # Resolved path must be the cfg-declared one
    assert "cfg-hook.sh" in result.stdout


def test_precedence_kanban_side_wins_when_cfg_absent(tmp_path: pathlib.Path) -> None:
    """Tier (b) kanban-side wins when cfg hook is not declared."""
    kanban_root, project_dir, dev_tree = _make_sandbox(tmp_path, "my-proj")

    # No cfg hook (key absent from project.cfg)
    # Tier (b): kanban-side hook
    kanban_hook = project_dir / "hooks" / "cm-release-pre-squash.sh"
    _write_hook(kanban_hook)

    # Tier (c): in-repo hook
    inrepo_hook = dev_tree / ".pgai" / "hooks" / "cm-release-pre-squash.sh"
    _write_hook(inrepo_hook)

    result = _run_resolve(tmp_path, kanban_root, "my-proj", project_dir / "hooks", dev_tree)
    assert result.returncode == 0
    assert "source: kanban-side" in result.stdout
    assert "cm-release-pre-squash.sh" in result.stdout
    assert "RESOLVED_PATH:" in result.stdout
    resolved = [l for l in result.stdout.splitlines() if l.startswith("RESOLVED_PATH:")][0]
    assert "kanban" in resolved or str(project_dir) in resolved


def test_precedence_inrepo_wins_when_cfg_and_kanban_absent(tmp_path: pathlib.Path) -> None:
    """Tier (c) in-repo wins when neither cfg nor kanban-side hooks are present."""
    kanban_root, project_dir, dev_tree = _make_sandbox(tmp_path, "my-proj")

    # No cfg hook. No kanban-side hook.
    # Tier (c): in-repo hook only.
    inrepo_hook = dev_tree / ".pgai" / "hooks" / "cm-release-pre-squash.sh"
    _write_hook(inrepo_hook)

    result = _run_resolve(tmp_path, kanban_root, "my-proj", project_dir / "hooks", dev_tree)
    assert result.returncode == 0
    assert "source: in-repo" in result.stdout
    assert str(inrepo_hook) in result.stdout


def test_resolver_func_sets_source_label_cfg(tmp_path: pathlib.Path) -> None:
    """projects_resolve_release_hook_path sets _PGAI_HOOK_LAST_SOURCE=cfg."""
    kanban_root, project_dir, dev_tree = _make_sandbox(tmp_path, "proj-a")
    cfg_hook = dev_tree / "my-hook.sh"
    _write_hook(cfg_hook)
    cfg_text = (
        "[project]\n"
        f"project_name = proj-a\n"
        f"dev_tree_path = {dev_tree}\n"
        "[hooks]\n"
        f"cm_release_pre_squash_hook = my-hook.sh\n"
    )
    (project_dir / "project.cfg").write_text(cfg_text, encoding="utf-8")

    result = _run_resolver_func(tmp_path, kanban_root, "proj-a")
    assert result.returncode == 0
    lines = result.stdout.splitlines()
    src_line = next((l for l in lines if l.startswith("SOURCE:")), "")
    assert "cfg" in src_line


def test_resolver_func_sets_source_label_kanban_side(tmp_path: pathlib.Path) -> None:
    """projects_resolve_release_hook_path sets _PGAI_HOOK_LAST_SOURCE=kanban-side."""
    kanban_root, project_dir, dev_tree = _make_sandbox(tmp_path, "proj-b")
    kanban_hook = project_dir / "hooks" / "cm-release-pre-squash.sh"
    _write_hook(kanban_hook)

    result = _run_resolver_func(tmp_path, kanban_root, "proj-b")
    assert result.returncode == 0
    lines = result.stdout.splitlines()
    src_line = next((l for l in lines if l.startswith("SOURCE:")), "")
    assert "kanban-side" in src_line


def test_resolver_func_sets_source_label_inrepo(tmp_path: pathlib.Path) -> None:
    """projects_resolve_release_hook_path sets _PGAI_HOOK_LAST_SOURCE=in-repo."""
    kanban_root, project_dir, dev_tree = _make_sandbox(tmp_path, "proj-c")
    inrepo_hook = dev_tree / ".pgai" / "hooks" / "cm-release-pre-squash.sh"
    _write_hook(inrepo_hook)

    result = _run_resolver_func(tmp_path, kanban_root, "proj-c")
    assert result.returncode == 0
    lines = result.stdout.splitlines()
    src_line = next((l for l in lines if l.startswith("SOURCE:")), "")
    assert "in-repo" in src_line


def test_resolver_func_empty_source_when_no_hook(tmp_path: pathlib.Path) -> None:
    """projects_resolve_release_hook_path sets _PGAI_HOOK_LAST_SOURCE='' when no hook found."""
    kanban_root, project_dir, dev_tree = _make_sandbox(tmp_path, "proj-d")

    result = _run_resolver_func(tmp_path, kanban_root, "proj-d")
    assert result.returncode == 0
    lines = result.stdout.splitlines()
    path_line = next((l for l in lines if l.startswith("PATH:")), "PATH:MISSING")
    src_line = next((l for l in lines if l.startswith("SOURCE:")), "SOURCE:MISSING")
    assert path_line == "PATH:"
    assert src_line == "SOURCE:"


# ---------------------------------------------------------------------------
# (b) Portability: in-repo hook survives remove-project + create-project
# ---------------------------------------------------------------------------


def test_inrepo_hook_survives_project_recreation(tmp_path: pathlib.Path) -> None:
    """
    Portability: an in-repo hook is found even after the kanban-side project
    directory is removed and recreated (simulating remove-project + create-project).

    The in-repo hook lives in <dev_tree_path>/.pgai/hooks/ which is part of
    the managed repo, not the kanban installation.  It is unaffected by kanban-
    side project removal/recreation.
    """
    kanban_root, project_dir, dev_tree = _make_sandbox(tmp_path, "portable-proj")

    # Plant hook ONLY in the in-repo location.
    inrepo_hook = dev_tree / ".pgai" / "hooks" / "cm-release-pre-squash.sh"
    _write_hook(inrepo_hook, content="#!/bin/sh\necho 'in-repo hook ran'\n")

    # Simulate remove-project: delete the kanban-side project directory entirely.
    import shutil
    shutil.rmtree(str(project_dir))

    # Simulate create-project: recreate the kanban-side directory with a fresh
    # project.cfg (no [hooks] key — as a newly registered project would have).
    project_dir.mkdir(parents=True, exist_ok=True)
    cfg_path = project_dir / "project.cfg"
    cfg_path.write_text(
        "[project]\n"
        "project_name = portable-proj\n"
        f"dev_tree_path = {dev_tree}\n",
        encoding="utf-8",
    )

    # The resolver should still find the in-repo hook after recreation.
    result = _run_resolve(tmp_path, kanban_root, "portable-proj", project_dir / "hooks", dev_tree)
    assert result.returncode == 0, f"Expected rc=0, got {result.returncode}; stderr: {result.stderr}"
    assert "source: in-repo" in result.stdout
    assert str(inrepo_hook) in result.stdout


# ---------------------------------------------------------------------------
# (c) Required=true + no hook → HALT before phase
# ---------------------------------------------------------------------------


def test_required_true_with_no_hook_calls_halt(tmp_path: pathlib.Path) -> None:
    """
    When cm_release_pre_squash_hook_required=true and no hook is at any tier,
    cm_resolve_and_enforce_hook calls cm_halt (stub exits 2).
    """
    kanban_root, project_dir, dev_tree = _make_sandbox(tmp_path, "req-proj")

    # Write project.cfg with required=true and no hook.
    cfg_text = (
        "[project]\n"
        "project_name = req-proj\n"
        f"dev_tree_path = {dev_tree}\n"
        "[hooks]\n"
        "cm_release_pre_squash_hook_required = true\n"
    )
    (project_dir / "project.cfg").write_text(cfg_text, encoding="utf-8")

    result = _run_resolve(tmp_path, kanban_root, "req-proj", project_dir / "hooks", dev_tree)
    # cm_halt stub exits 2
    assert result.returncode == 2, f"Expected HALT (rc=2), got {result.returncode}; stdout: {result.stdout}"


def test_required_true_halt_message_names_phase(tmp_path: pathlib.Path) -> None:
    """HALT message names the phase."""
    kanban_root, project_dir, dev_tree = _make_sandbox(tmp_path, "req-proj2")
    cfg_text = (
        "[project]\n"
        "project_name = req-proj2\n"
        f"dev_tree_path = {dev_tree}\n"
        "[hooks]\n"
        "cm_release_pre_squash_hook_required = true\n"
    )
    (project_dir / "project.cfg").write_text(cfg_text, encoding="utf-8")

    result = _run_resolve(tmp_path, kanban_root, "req-proj2", project_dir / "hooks", dev_tree)
    assert result.returncode == 2
    combined = result.stdout + result.stderr
    assert "pre-squash" in combined


def test_required_true_halt_message_names_config_key(tmp_path: pathlib.Path) -> None:
    """HALT message names the config key."""
    kanban_root, project_dir, dev_tree = _make_sandbox(tmp_path, "req-proj3")
    cfg_text = (
        "[project]\n"
        "project_name = req-proj3\n"
        f"dev_tree_path = {dev_tree}\n"
        "[hooks]\n"
        "cm_release_pre_squash_hook_required = true\n"
    )
    (project_dir / "project.cfg").write_text(cfg_text, encoding="utf-8")

    result = _run_resolve(tmp_path, kanban_root, "req-proj3", project_dir / "hooks", dev_tree)
    assert result.returncode == 2
    combined = result.stdout + result.stderr
    assert "cm_release_pre_squash_hook_required" in combined


def test_required_true_halt_message_names_all_three_locations(tmp_path: pathlib.Path) -> None:
    """HALT message names all three searched locations."""
    kanban_root, project_dir, dev_tree = _make_sandbox(tmp_path, "req-proj4")
    cfg_text = (
        "[project]\n"
        "project_name = req-proj4\n"
        f"dev_tree_path = {dev_tree}\n"
        "[hooks]\n"
        "cm_release_pre_squash_hook_required = true\n"
    )
    (project_dir / "project.cfg").write_text(cfg_text, encoding="utf-8")

    result = _run_resolve(tmp_path, kanban_root, "req-proj4", project_dir / "hooks", dev_tree)
    assert result.returncode == 2
    combined = result.stdout + result.stderr
    # Must name all three tiers
    assert "cfg" in combined
    assert "kanban-side" in combined or "kanban" in combined
    assert "in-repo" in combined


def test_required_true_no_tag_when_halted(tmp_path: pathlib.Path) -> None:
    """
    When required=true and no hook, the HALT fires before the phase runs.
    Since cm_halt exits 2 in our stub, execution never reaches the git tag step.
    This verifies the 'HALT BEFORE the phase' requirement.
    """
    kanban_root, project_dir, dev_tree = _make_sandbox(tmp_path, "req-proj5")
    cfg_text = (
        "[project]\n"
        "project_name = req-proj5\n"
        f"dev_tree_path = {dev_tree}\n"
        "[hooks]\n"
        "cm_release_pre_tag_hook_required = true\n"
    )
    (project_dir / "project.cfg").write_text(cfg_text, encoding="utf-8")

    # Script that simulates: resolve (should halt), then create a tag file as a sentinel.
    sentinel = tmp_path / "tag_was_created"
    script = (
        f"{_CM_HALT_STUB}\n"
        f"{_SOURCE_PREAMBLE}\n"
        f"cm_resolve_and_enforce_hook req-proj5 pre-tag {str(project_dir / 'hooks')!r} {str(dev_tree)!r}\n"
        f"touch {str(sentinel)!r}\n"
    )
    run_bash(tmp_path, script, extra_env={"KANBAN_ROOT": str(kanban_root)})
    # Sentinel must NOT exist because HALT fired before the tag step.
    assert not sentinel.exists(), "HALT did not fire before the phase — tag sentinel was created"


# ---------------------------------------------------------------------------
# (d) Required=false + no hook → 'none configured' printed, proceeds
# ---------------------------------------------------------------------------


def test_required_false_no_hook_prints_none_configured(tmp_path: pathlib.Path) -> None:
    """When required=false and no hook, prints '<phase> hook: none configured'."""
    kanban_root, project_dir, dev_tree = _make_sandbox(tmp_path, "opt-proj")

    result = _run_resolve(tmp_path, kanban_root, "opt-proj", project_dir / "hooks", dev_tree)
    assert result.returncode == 0
    assert "none configured" in result.stdout


def test_required_false_explicit_no_hook_does_not_halt(tmp_path: pathlib.Path) -> None:
    """When cm_release_pre_squash_hook_required=false (explicit), no HALT fires."""
    kanban_root, project_dir, dev_tree = _make_sandbox(tmp_path, "opt-proj2")
    cfg_text = (
        "[project]\n"
        "project_name = opt-proj2\n"
        f"dev_tree_path = {dev_tree}\n"
        "[hooks]\n"
        "cm_release_pre_squash_hook_required = false\n"
    )
    (project_dir / "project.cfg").write_text(cfg_text, encoding="utf-8")

    result = _run_resolve(tmp_path, kanban_root, "opt-proj2", project_dir / "hooks", dev_tree)
    # Must exit 0 (no HALT)
    assert result.returncode == 0
    assert "none configured" in result.stdout


def test_none_configured_line_format(tmp_path: pathlib.Path) -> None:
    """The 'none configured' line matches the exact format '<phase> hook: none configured'."""
    kanban_root, project_dir, dev_tree = _make_sandbox(tmp_path, "fmt-proj")

    result = _run_resolve(tmp_path, kanban_root, "fmt-proj", project_dir / "hooks", dev_tree)
    assert result.returncode == 0
    lines = result.stdout.splitlines()
    hook_lines = [l for l in lines if "hook:" in l and "RESOLVED_PATH" not in l]
    assert len(hook_lines) >= 1
    assert hook_lines[0] == "pre-squash hook: none configured"


def test_resolution_line_format_with_hook(tmp_path: pathlib.Path) -> None:
    """Resolution line format: '<phase> hook: <path> (source: <label>)'."""
    kanban_root, project_dir, dev_tree = _make_sandbox(tmp_path, "fmt-proj2")
    inrepo_hook = dev_tree / ".pgai" / "hooks" / "cm-release-pre-squash.sh"
    _write_hook(inrepo_hook)

    result = _run_resolve(tmp_path, kanban_root, "fmt-proj2", project_dir / "hooks", dev_tree)
    assert result.returncode == 0
    lines = result.stdout.splitlines()
    hook_lines = [l for l in lines if l.startswith("pre-squash hook:")]
    assert len(hook_lines) == 1
    assert "(source:" in hook_lines[0]
    assert hook_lines[0].endswith(")") or "(source: in-repo)" in hook_lines[0]


# ---------------------------------------------------------------------------
# (e) Non-executable in-repo hook → loud error + rc 1
# ---------------------------------------------------------------------------


def test_nonexecutable_inrepo_hook_returns_error(tmp_path: pathlib.Path) -> None:
    """A non-executable in-repo hook triggers a loud error and returns 1."""
    kanban_root, project_dir, dev_tree = _make_sandbox(tmp_path, "noexec-proj")

    # Plant a non-executable in-repo hook (no exec bit)
    inrepo_hook = dev_tree / ".pgai" / "hooks" / "cm-release-pre-squash.sh"
    _write_hook(inrepo_hook, executable=False)

    result = _run_resolve(tmp_path, kanban_root, "noexec-proj", project_dir / "hooks", dev_tree)
    # Must exit non-zero (not 0, not 2 from HALT)
    assert result.returncode == 1, (
        f"Expected rc=1 for non-executable hook, got {result.returncode}; "
        f"stdout: {result.stdout}; stderr: {result.stderr}"
    )


def test_nonexecutable_inrepo_hook_names_path_in_error(tmp_path: pathlib.Path) -> None:
    """Error message for non-executable in-repo hook names the path."""
    kanban_root, project_dir, dev_tree = _make_sandbox(tmp_path, "noexec-proj2")
    inrepo_hook = dev_tree / ".pgai" / "hooks" / "cm-release-pre-squash.sh"
    _write_hook(inrepo_hook, executable=False)

    result = _run_resolve(tmp_path, kanban_root, "noexec-proj2", project_dir / "hooks", dev_tree)
    combined = result.stdout + result.stderr
    assert str(inrepo_hook) in combined, (
        f"Expected hook path {inrepo_hook} in error output; got: {combined!r}"
    )


def test_nonexecutable_inrepo_hook_mentions_chmod(tmp_path: pathlib.Path) -> None:
    """Error message for non-executable in-repo hook mentions chmod fix."""
    kanban_root, project_dir, dev_tree = _make_sandbox(tmp_path, "noexec-proj3")
    inrepo_hook = dev_tree / ".pgai" / "hooks" / "cm-release-pre-squash.sh"
    _write_hook(inrepo_hook, executable=False)

    result = _run_resolve(tmp_path, kanban_root, "noexec-proj3", project_dir / "hooks", dev_tree)
    combined = result.stdout + result.stderr
    assert "chmod" in combined, (
        f"Expected 'chmod' fix hint in error output; got: {combined!r}"
    )


def test_nonexecutable_inrepo_hook_not_silently_skipped(tmp_path: pathlib.Path) -> None:
    """Non-executable in-repo hook must not silently skip (rc must be non-zero)."""
    kanban_root, project_dir, dev_tree = _make_sandbox(tmp_path, "noexec-proj4")
    inrepo_hook = dev_tree / ".pgai" / "hooks" / "cm-release-pre-squash.sh"
    # Non-executable — mode 0644
    _write_hook(inrepo_hook, executable=False)

    result = _run_resolve(tmp_path, kanban_root, "noexec-proj4", project_dir / "hooks", dev_tree)
    # A silent skip would return 0. An error must return non-zero.
    assert result.returncode != 0


# ---------------------------------------------------------------------------
# (f) Existing kanban-side hook behavior regression lock
# ---------------------------------------------------------------------------


def test_kanban_side_hook_resolves_correctly(tmp_path: pathlib.Path) -> None:
    """
    Regression lock: a kanban-side hook (tier b) resolves and its path is
    returned with source label 'kanban-side'.  This is the existing behavior
    before this RC; it must not regress.
    """
    kanban_root, project_dir, dev_tree = _make_sandbox(tmp_path, "legacy-proj")

    # Only a kanban-side hook — no cfg, no in-repo.
    kanban_hook = project_dir / "hooks" / "cm-release-pre-squash.sh"
    _write_hook(kanban_hook)

    result = _run_resolve(tmp_path, kanban_root, "legacy-proj", project_dir / "hooks", dev_tree)
    assert result.returncode == 0
    assert "source: kanban-side" in result.stdout
    lines = [l for l in result.stdout.splitlines() if l.startswith("RESOLVED_PATH:")]
    assert lines, "RESOLVED_PATH line missing from output"
    resolved = lines[0].removeprefix("RESOLVED_PATH:")
    assert resolved == str(kanban_hook)


def test_kanban_side_hook_not_shadowed_by_absent_cfg(tmp_path: pathlib.Path) -> None:
    """
    When cfg key is absent (empty value), the kanban-side hook is still picked up.
    """
    kanban_root, project_dir, dev_tree = _make_sandbox(tmp_path, "legacy-proj2")

    # Write cfg with an empty hook key (should not shadow kanban-side)
    cfg_text = (
        "[project]\n"
        "project_name = legacy-proj2\n"
        f"dev_tree_path = {dev_tree}\n"
        "[hooks]\n"
        "cm_release_pre_squash_hook =\n"
    )
    (project_dir / "project.cfg").write_text(cfg_text, encoding="utf-8")

    kanban_hook = project_dir / "hooks" / "cm-release-pre-squash.sh"
    _write_hook(kanban_hook)

    result = _run_resolve(tmp_path, kanban_root, "legacy-proj2", project_dir / "hooks", dev_tree)
    assert result.returncode == 0
    assert "source: kanban-side" in result.stdout


# ---------------------------------------------------------------------------
# pp_hook_required accessor
# ---------------------------------------------------------------------------


def test_pp_hook_required_returns_false_by_default(tmp_path: pathlib.Path) -> None:
    """pp_hook_required returns false when the key is absent from project.cfg."""
    kanban_root, project_dir, dev_tree = _make_sandbox(tmp_path, "req-test1")
    script = (
        f"source {_LIB_PROJECT_PATHS} && "
        f"pp_hook_required req-test1 pre-squash"
    )
    result = run_bash(
        tmp_path,
        script,
        extra_env={"KANBAN_ROOT": str(kanban_root)},
    )
    assert result.returncode == 0
    assert result.stdout.strip() == "false"


def test_pp_hook_required_returns_true_when_set(tmp_path: pathlib.Path) -> None:
    """pp_hook_required returns true when cm_release_pre_squash_hook_required=true."""
    kanban_root, project_dir, dev_tree = _make_sandbox(tmp_path, "req-test2")
    cfg_text = (
        "[project]\n"
        "project_name = req-test2\n"
        f"dev_tree_path = {dev_tree}\n"
        "[hooks]\n"
        "cm_release_pre_squash_hook_required = true\n"
    )
    (project_dir / "project.cfg").write_text(cfg_text, encoding="utf-8")

    script = (
        f"source {_LIB_PROJECT_PATHS} && "
        f"pp_hook_required req-test2 pre-squash"
    )
    result = run_bash(
        tmp_path,
        script,
        extra_env={"KANBAN_ROOT": str(kanban_root)},
    )
    assert result.returncode == 0
    assert result.stdout.strip() == "true"


def test_pp_hook_required_supports_all_phases(tmp_path: pathlib.Path) -> None:
    """pp_hook_required works for pre-squash, pre-tag, and post-tag."""
    kanban_root, project_dir, dev_tree = _make_sandbox(tmp_path, "req-test3")
    cfg_text = (
        "[project]\n"
        "project_name = req-test3\n"
        f"dev_tree_path = {dev_tree}\n"
        "[hooks]\n"
        "cm_release_pre_squash_hook_required = true\n"
        "cm_release_pre_tag_hook_required = false\n"
        "cm_release_post_tag_hook_required = true\n"
    )
    (project_dir / "project.cfg").write_text(cfg_text, encoding="utf-8")

    for phase, expected in [("pre-squash", "true"), ("pre-tag", "false"), ("post-tag", "true")]:
        script = (
            f"source {_LIB_PROJECT_PATHS} && "
            f"pp_hook_required req-test3 {phase!r}"
        )
        result = run_bash(
            tmp_path,
            script,
            extra_env={"KANBAN_ROOT": str(kanban_root)},
        )
        assert result.returncode == 0, f"Phase {phase!r} failed: {result.stderr}"
        assert result.stdout.strip() == expected, (
            f"Phase {phase!r}: expected {expected!r}, got {result.stdout.strip()!r}"
        )


# ---------------------------------------------------------------------------
# Multi-phase: correct phase resolution
# ---------------------------------------------------------------------------


def test_different_hooks_per_phase(tmp_path: pathlib.Path) -> None:
    """Each phase resolves its own hook file independently."""
    kanban_root, project_dir, dev_tree = _make_sandbox(tmp_path, "multi-phase")

    # Plant hooks only at tier (c) for each phase
    for phase_name in ("pre-squash", "pre-tag", "post-tag"):
        hook = dev_tree / ".pgai" / "hooks" / f"cm-release-{phase_name}.sh"
        _write_hook(hook)

    for phase_name in ("pre-squash", "pre-tag", "post-tag"):
        result = _run_resolve(
            tmp_path, kanban_root, "multi-phase",
            project_dir / "hooks", dev_tree,
            phase=phase_name,
        )
        assert result.returncode == 0, f"Phase {phase_name!r}: rc={result.returncode}"
        assert "source: in-repo" in result.stdout
        assert f"cm-release-{phase_name}.sh" in result.stdout
