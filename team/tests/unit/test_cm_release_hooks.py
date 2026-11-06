"""
test_cm_release_hooks.py
========================
Behavioral unit tests for the CM release hook resolution, printing, and
enforcement subsystem.

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
  (g) RC-branch-aware resolution: when an rc_branch is supplied, tier (c)
      in-repo hooks and tier (a)-relative cfg hooks resolve from that branch's
      git tree rather than the dev tree's current checkout, retiring the
      dev-tree-parking workaround. Omitting rc_branch preserves the legacy
      filesystem-based resolution exercised by (a)-(f) above.
  (h) Symlinked hooks on the RC branch: a hook committed as a symlink
      resolves to its committed regular-file target (target content, not
      the link text); a symlink that cannot reach a 100755 regular file is
      refused with remedy commands that run as printed and clear the
      refusal, and never with `--chmod=+x` on the symlink itself.

All tests use the shell_harness.run_bash pattern with synthetic temp directories.
No live kanban root is touched.
"""

from __future__ import annotations

import os
import pathlib
import stat
import subprocess

import pytest

from tests.unit.shell_harness import run_bash
from tests.fixtures.binary_guard import require_binary


@pytest.fixture(autouse=True)
def _require_git() -> None:
    """Skip every test in this module when git is not on PATH.

    Every test here builds, inspects, or drives a real git repository; a
    module-wide autouse guard keeps that requirement in one place instead
    of repeating a per-test check at each git-dependent call site.
    """
    require_binary("git", label="cm-release-hooks")


# Paths to the libraries under test (relative to team/, where pytest runs from).
_LIB_PROJECT_PATHS = "scripts/lib/project_paths.sh"
_LIB_PROJECTS = "scripts/lib/projects.sh"
_LIB_HOOKS = "scripts/lib/cm_release_hooks.sh"

# Source preamble that loads the two prerequisites before cm_release_hooks.sh.
_SOURCE_PREAMBLE = f"source {_LIB_PROJECT_PATHS} && " f"source {_LIB_PROJECTS} && " f"source {_LIB_HOOKS}"

# A minimal cm_halt stub: prints HALT info to stdout (so tests can inspect it)
# and exits 2 to signal the halt path was taken.
_CM_HALT_STUB = "cm_halt() { " '  echo "HALT: trigger=$1 reason=$2"; ' "  exit 2; " "}"


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
        "[project]\n" f"project_name = {project_name}\n" f"dev_tree_path = {dev_tree}\n",
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
        f'echo "RESOLVED_PATH:$CM_RESOLVED_HOOK_PATH"\n'
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
        f'echo "PATH:$path"\n'
        f'echo "SOURCE:$_PGAI_HOOK_LAST_SOURCE"\n'
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


def _plant_cfg_hook(dev_tree: pathlib.Path, project_dir: pathlib.Path, project_name: str) -> None:
    cfg_hook = dev_tree / "my-hook.sh"
    _write_hook(cfg_hook)
    cfg_text = (
        "[project]\n"
        f"project_name = {project_name}\n"
        f"dev_tree_path = {dev_tree}\n"
        "[hooks]\n"
        f"cm_release_pre_squash_hook = my-hook.sh\n"
    )
    (project_dir / "project.cfg").write_text(cfg_text, encoding="utf-8")


def _plant_kanban_side_hook(dev_tree: pathlib.Path, project_dir: pathlib.Path, project_name: str) -> None:
    kanban_hook = project_dir / "hooks" / "cm-release-pre-squash.sh"
    _write_hook(kanban_hook)


def _plant_inrepo_hook(dev_tree: pathlib.Path, project_dir: pathlib.Path, project_name: str) -> None:
    inrepo_hook = dev_tree / ".pgai" / "hooks" / "cm-release-pre-squash.sh"
    _write_hook(inrepo_hook)


def _plant_no_hook(dev_tree: pathlib.Path, project_dir: pathlib.Path, project_name: str) -> None:
    pass


@pytest.mark.parametrize(
    "project_name, plant_hook, expected_source",
    [
        pytest.param("proj-a", _plant_cfg_hook, "cfg", id="cfg"),
        pytest.param("proj-b", _plant_kanban_side_hook, "kanban-side", id="kanban_side"),
        pytest.param("proj-c", _plant_inrepo_hook, "in-repo", id="inrepo"),
        pytest.param("proj-d", _plant_no_hook, "", id="no_hook"),
    ],
)
def test_resolver_func_sets_source_label(
    tmp_path: pathlib.Path, project_name: str, plant_hook, expected_source: str
) -> None:
    """projects_resolve_release_hook_path sets _PGAI_HOOK_LAST_SOURCE to the
    matching tier label (or empty when no hook is found at any tier).
    Survives-as-named-parameterization of the original four discrete tests;
    no case dropped."""
    kanban_root, project_dir, dev_tree = _make_sandbox(tmp_path, project_name)
    plant_hook(dev_tree, project_dir, project_name)

    result = _run_resolver_func(tmp_path, kanban_root, project_name)
    assert result.returncode == 0
    lines = result.stdout.splitlines()
    src_line = next((l for l in lines if l.startswith("SOURCE:")), "SOURCE:MISSING")
    if expected_source:
        assert expected_source in src_line
    else:
        path_line = next((l for l in lines if l.startswith("PATH:")), "PATH:MISSING")
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
        "[project]\n" "project_name = portable-proj\n" f"dev_tree_path = {dev_tree}\n",
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
    assert str(inrepo_hook) in combined, f"Expected hook path {inrepo_hook} in error output; got: {combined!r}"


def test_nonexecutable_inrepo_hook_mentions_chmod(tmp_path: pathlib.Path) -> None:
    """Error message for non-executable in-repo hook mentions chmod fix."""
    kanban_root, project_dir, dev_tree = _make_sandbox(tmp_path, "noexec-proj3")
    inrepo_hook = dev_tree / ".pgai" / "hooks" / "cm-release-pre-squash.sh"
    _write_hook(inrepo_hook, executable=False)

    result = _run_resolve(tmp_path, kanban_root, "noexec-proj3", project_dir / "hooks", dev_tree)
    combined = result.stdout + result.stderr
    assert "chmod" in combined, f"Expected 'chmod' fix hint in error output; got: {combined!r}"


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
# (e2) Retrofit: full three-part remedy block for the
# non-executable-hook failure — docs/coding-standards.md "Gates prescribe,
# not just diagnose": the pre-tag hook gate's deterministic sub-case is a
# full remedy. Covers the filesystem in-repo tier and the RC-branch-aware
# tiers, plus the CM_HOOK_NONEXEC_PATH / CM_HOOK_NONEXEC_FIX_CMD globals a
# caller (release.sh) reads to build a placeholder-free HALT message.
# ---------------------------------------------------------------------------


def _run_resolve_with_remedy_globals(
    tmp_path: pathlib.Path,
    kanban_root: pathlib.Path,
    project_name: str,
    project_hooks_dir: pathlib.Path,
    dev_tree_path: pathlib.Path,
    phase: str = "pre-squash",
    rc_branch: str = "",
) -> object:
    """Like _run_resolve, but also echoes CM_HOOK_NONEXEC_PATH and
    CM_HOOK_NONEXEC_FIX_CMD so tests can assert on the remedy globals."""
    rc_branch_arg = f" {rc_branch!r}" if rc_branch else ""
    script = (
        f"{_CM_HALT_STUB}\n"
        f"{_SOURCE_PREAMBLE}\n"
        f"_rc=0\n"
        f"cm_resolve_and_enforce_hook {project_name!r} {phase!r} "
        f"{str(project_hooks_dir)!r} {str(dev_tree_path)!r}{rc_branch_arg} || _rc=$?\n"
        f'echo "RESOLVED_PATH:$CM_RESOLVED_HOOK_PATH"\n'
        f'echo "NONEXEC_PATH:$CM_HOOK_NONEXEC_PATH"\n'
        f'echo "NONEXEC_FIX_CMD:$CM_HOOK_NONEXEC_FIX_CMD"\n'
        f"exit $_rc\n"
    )
    return run_bash(
        tmp_path,
        script,
        extra_env={"KANBAN_ROOT": str(kanban_root)},
    )


def test_nonexecutable_inrepo_hook_prints_full_remedy_block(tmp_path: pathlib.Path) -> None:
    """Non-executable in-repo hook prints Cause(s)/To-fix/Rule, per docs/coding-standards.md."""
    kanban_root, project_dir, dev_tree = _make_sandbox(tmp_path, "noexec-remedy1")
    inrepo_hook = dev_tree / ".pgai" / "hooks" / "cm-release-pre-tag.sh"
    _write_hook(inrepo_hook, executable=False)

    result = _run_resolve(tmp_path, kanban_root, "noexec-remedy1", project_dir / "hooks", dev_tree, phase="pre-tag")
    combined = result.stdout + result.stderr
    assert "Cause(s):" in combined
    assert "To fix:" in combined
    assert "Rule:" in combined
    assert f"1. chmod +x {inrepo_hook}" in combined


def test_nonexecutable_inrepo_hook_sets_remedy_globals(tmp_path: pathlib.Path) -> None:
    """CM_HOOK_NONEXEC_PATH / CM_HOOK_NONEXEC_FIX_CMD are set to derived, verbatim values."""
    kanban_root, project_dir, dev_tree = _make_sandbox(tmp_path, "noexec-remedy2")
    inrepo_hook = dev_tree / ".pgai" / "hooks" / "cm-release-pre-tag.sh"
    _write_hook(inrepo_hook, executable=False)

    result = _run_resolve_with_remedy_globals(
        tmp_path, kanban_root, "noexec-remedy2", project_dir / "hooks", dev_tree, phase="pre-tag"
    )
    lines = result.stdout.splitlines()
    nonexec_path = next(l for l in lines if l.startswith("NONEXEC_PATH:")).removeprefix("NONEXEC_PATH:")
    nonexec_fix = next(l for l in lines if l.startswith("NONEXEC_FIX_CMD:")).removeprefix("NONEXEC_FIX_CMD:")
    assert nonexec_path == str(inrepo_hook)
    assert nonexec_fix == f"chmod +x {inrepo_hook}"
    # No angle-bracket placeholders in the derived fix command (house placeholder law).
    assert "<" not in nonexec_fix and ">" not in nonexec_fix


def test_remedy_globals_reset_to_empty_on_successful_call(tmp_path: pathlib.Path) -> None:
    """A successful (executable) hook resolution resets the remedy globals to empty,
    so a caller never reads a stale value from a prior failed call in the same shell."""
    kanban_root, project_dir, dev_tree = _make_sandbox(tmp_path, "noexec-remedy3")
    good_hook = dev_tree / ".pgai" / "hooks" / "cm-release-pre-tag.sh"
    _write_hook(good_hook, executable=True)

    result = _run_resolve_with_remedy_globals(
        tmp_path, kanban_root, "noexec-remedy3", project_dir / "hooks", dev_tree, phase="pre-tag"
    )
    assert result.returncode == 0
    lines = result.stdout.splitlines()
    nonexec_path = next(l for l in lines if l.startswith("NONEXEC_PATH:")).removeprefix("NONEXEC_PATH:")
    nonexec_fix = next(l for l in lines if l.startswith("NONEXEC_FIX_CMD:")).removeprefix("NONEXEC_FIX_CMD:")
    assert nonexec_path == ""
    assert nonexec_fix == ""


def test_nonexecutable_inrepo_hook_remedy_command_clears_the_gate(tmp_path: pathlib.Path) -> None:
    """Executing the printed CM_HOOK_NONEXEC_FIX_CMD verbatim clears the gate on re-run."""
    kanban_root, project_dir, dev_tree = _make_sandbox(tmp_path, "noexec-remedy4")
    inrepo_hook = dev_tree / ".pgai" / "hooks" / "cm-release-pre-tag.sh"
    _write_hook(inrepo_hook, executable=False)

    # First call: must fail with the printed remedy command captured.
    first = _run_resolve_with_remedy_globals(
        tmp_path, kanban_root, "noexec-remedy4", project_dir / "hooks", dev_tree, phase="pre-tag"
    )
    assert first.returncode == 1
    fix_cmd = next(l for l in first.stdout.splitlines() if l.startswith("NONEXEC_FIX_CMD:")).removeprefix(
        "NONEXEC_FIX_CMD:"
    )
    assert fix_cmd == f"chmod +x {inrepo_hook}"

    # Execute the printed fix command verbatim through a real shell.
    exec_result = run_bash(tmp_path, fix_cmd)
    assert exec_result.returncode == 0, f"Fix command failed: {exec_result.stderr}"

    # Re-run: must now succeed (source: in-repo, resolves to the now-executable hook).
    second = _run_resolve(tmp_path, kanban_root, "noexec-remedy4", project_dir / "hooks", dev_tree, phase="pre-tag")
    assert second.returncode == 0, f"Gate still fails after applying the fix: {second.stdout} {second.stderr}"
    assert "source: in-repo" in second.stdout


def test_rc_branch_aware_nonexecutable_hook_prints_full_remedy_block(
    tmp_path: pathlib.Path,
) -> None:
    """RC-branch-aware non-executable hook (git-mode check) prints the same
    three-part remedy shape, naming the RC branch and the git update-index fix."""
    kanban_root, project_dir, dev_tree = _make_sandbox(tmp_path, "rcaware-remedy")
    _make_git_dev_tree(dev_tree)

    _git(dev_tree, "checkout", "-b", "rc/v9.9.5")
    hook_rel = ".pgai/hooks/cm-release-pre-tag.sh"
    hook_path = dev_tree / hook_rel
    _write_hook(hook_path, executable=False)
    hook_path.chmod(0o644)
    _git(dev_tree, "add", hook_rel)
    _git(dev_tree, "commit", "-m", "add non-executable rc pre-tag hook")
    _git(dev_tree, "checkout", "main")

    result = _run_resolve_with_remedy_globals(
        tmp_path,
        kanban_root,
        "rcaware-remedy",
        project_dir / "hooks",
        dev_tree,
        phase="pre-tag",
        rc_branch="rc/v9.9.5",
    )
    combined = result.stdout + result.stderr
    assert result.returncode == 1
    assert "Cause(s):" in combined
    assert "To fix:" in combined
    assert "Rule:" in combined
    assert "git update-index --chmod=+x" in combined
    nonexec_path = next(l for l in result.stdout.splitlines() if l.startswith("NONEXEC_PATH:")).removeprefix(
        "NONEXEC_PATH:"
    )
    assert nonexec_path == f"{hook_rel} (on rc/v9.9.5)"


# ---------------------------------------------------------------------------
# (e3) release.sh's pre-tag call site: HALT reason is placeholder-free
#
# release.sh's own cm_halt call (Step 12c) is not a library function, so it
# cannot be sourced in isolation the way cm_resolve_and_enforce_hook can.
# Instead, this test extracts the exact call-site block verbatim from the
# live release.sh text (between the pre-tag cm_resolve_and_enforce_hook
# invocation and its closing brace) and executes it against a real
# non-executable hook, proving the HALT reason release.sh builds from
# CM_HOOK_NONEXEC_PATH / CM_HOOK_NONEXEC_FIX_CMD carries the derived literal
# values rather than the pre-retrofit "(path shown above)" placeholder.
#
# The call site lives inside _cm_run_pre_tag_hook_step() (v2.9.20
# engine-decomposition threaded it into release_finalize.sh as a callback —
# see that file's "Why two callbacks"), so it is indented one function-body
# level rather than sitting at column 0; the match below compares stripped
# lines so the extraction is indentation-agnostic.
# ---------------------------------------------------------------------------

_RELEASE_SH = "scripts/cm/release.sh"


def _extract_pretag_call_site() -> str:
    """Extract the pre-tag cm_resolve_and_enforce_hook || { cm_halt ... }
    block verbatim from the live release.sh text."""
    text = pathlib.Path(_RELEASE_SH).read_text(encoding="utf-8")
    lines = text.splitlines()
    start = next(
        i for i, l in enumerate(lines) if l.lstrip().startswith('cm_resolve_and_enforce_hook "$PROJECT_NAME" "pre-tag"')
    )
    end = next(i for i in range(start + 1, len(lines)) if lines[i].strip() == "}")
    return "\n".join(lines[start : end + 1])


def test_release_sh_pretag_call_site_halt_reason_has_no_placeholder(
    tmp_path: pathlib.Path,
) -> None:
    """The real release.sh pre-tag call site's HALT reason is placeholder-free.

    Regression lock for the pre-retrofit text: 'Fix with: chmod +x (path
    shown above)' — a reference to prior output rather than a derived literal.
    """
    kanban_root, project_dir, dev_tree = _make_sandbox(tmp_path, "release-callsite")
    inrepo_hook = dev_tree / ".pgai" / "hooks" / "cm-release-pre-tag.sh"
    _write_hook(inrepo_hook, executable=False)

    call_site = _extract_pretag_call_site()
    script = (
        f"{_CM_HALT_STUB}\n"
        f"{_SOURCE_PREAMBLE}\n"
        f"PROJECT_NAME='release-callsite'\n"
        f"PROJECT_HOOKS_DIR={str(project_dir / 'hooks')!r}\n"
        f"REPO_ROOT={str(dev_tree)!r}\n"
        f"RC_BRANCH=''\n"
        f"CM_RESOLVED_HOOK_PATH=''\n"
        f"_rc=0\n"
        f"( {call_site} ) || _rc=$?\n"
        f"exit $_rc\n"
    )
    result = run_bash(tmp_path, script, extra_env={"KANBAN_ROOT": str(kanban_root)})

    assert result.returncode == 2, (
        f"Expected the cm_halt stub to fire (rc=2); got {result.returncode}.\n"
        f"stdout={result.stdout}\nstderr={result.stderr}"
    )
    combined = result.stdout + result.stderr
    assert "(path shown above)" not in combined
    assert str(inrepo_hook) in combined
    assert f"chmod +x {inrepo_hook}" in combined


def _extract_call_site(phase: str) -> str:
    """The release.sh ``cm_resolve_and_enforce_hook ... "<phase>" ... || { cm_halt ... }``
    block for *phase*, verbatim from the live file text."""
    text = pathlib.Path(_RELEASE_SH).read_text(encoding="utf-8")
    lines = text.splitlines()
    start = next(
        i
        for i, l in enumerate(lines)
        if l.lstrip().startswith(f'cm_resolve_and_enforce_hook "$PROJECT_NAME" "{phase}"')
    )
    end = next(i for i in range(start + 1, len(lines)) if lines[i].strip() == "}")
    return "\n".join(lines[start : end + 1])


# A cm_halt stub that reports its error key and its fourth argument.
_CM_HALT_ARGS_STUB = "cm_halt() { " '  echo "HALT: key=$1 flag=${4:-none}"; ' "  exit 2; " "}"


@pytest.mark.parametrize(
    "phase, key",
    [
        ("pre-squash", "cm-release.trigger-4.pre-squash-hook-not-executable"),
        ("pre-tag", "cm-release.pre-tag-hook-not-executable"),
    ],
)
def test_release_sh_non_executable_hook_halt_also_writes_the_inbox(
    tmp_path: pathlib.Path, phase: str, key: str
) -> None:
    """A non-executable pre-squash or pre-tag hook HALTs with --inbox, so the
    HALT's error key, reason and remedy also reach the operator inbox."""
    kanban_root, project_dir, dev_tree = _make_sandbox(tmp_path, "release-callsite")
    _write_hook(dev_tree / ".pgai" / "hooks" / f"cm-release-{phase}.sh", executable=False)

    script = (
        f"{_CM_HALT_ARGS_STUB}\n"
        f"{_SOURCE_PREAMBLE}\n"
        f"PROJECT_NAME='release-callsite'\n"
        f"PROJECT_HOOKS_DIR={str(project_dir / 'hooks')!r}\n"
        f"REPO_ROOT={str(dev_tree)!r}\n"
        f"RC_BRANCH=''\n"
        f"CM_RESOLVED_HOOK_PATH=''\n"
        f"_rc=0\n"
        f"( {_extract_call_site(phase)} ) || _rc=$?\n"
        f"exit $_rc\n"
    )
    result = run_bash(tmp_path, script, extra_env={"KANBAN_ROOT": str(kanban_root)})

    assert result.returncode == 2, f"stdout={result.stdout}\nstderr={result.stderr}"
    assert f"HALT: key={key} flag=--inbox" in result.stdout, result.stdout


def _extract_pre_squash_step() -> str:
    """release.sh's ``_cm_run_pre_squash_hook_step`` function, verbatim."""
    lines = pathlib.Path(_RELEASE_SH).read_text(encoding="utf-8").splitlines()
    start = lines.index("_cm_run_pre_squash_hook_step() {")
    end = next(i for i in range(start + 1, len(lines)) if lines[i] == "}")
    return "\n".join(lines[start : end + 1])


@pytest.mark.parametrize(
    "runner_broken, key, hook_runs",
    [
        (True, "cm-release.trigger-4.pre-squash-hook-runner-failed", False),
        (False, "cm-release.trigger-4.pre-squash-hook-failed", True),
    ],
)
def test_release_sh_pre_squash_trigger_4_names_the_runner_or_the_hook(
    tmp_path: pathlib.Path, runner_broken: bool, key: str, hook_runs: bool
) -> None:
    """Trigger 4 blames the hook only when the hook ran and failed: a runner
    that cannot start it (no PYTHONPATH) HALTs under the runner key with a
    reason naming the runner, and the hook never runs."""
    kanban_root, project_dir, dev_tree = _make_sandbox(tmp_path, "release-callsite")
    marker = tmp_path / "hook-ran.txt"
    hook = project_dir / "hooks" / "cm-release-pre-squash.sh"
    _write_hook(hook, content=f"#!/usr/bin/env bash\necho ran > {str(marker)!r}\nexit 7\n")

    script = "\n".join(
        [
            'cm_halt() { echo "HALT: key=$1 flag=${4:-none} reason=$2"; exit 2; }',
            f"cm_resolve_and_enforce_hook() {{ CM_RESOLVED_HOOK_PATH={str(hook)!r}; }}",
            "source scripts/lib/error_contract.sh",
            "source scripts/lib/pp_run_ops.sh",
            "source scripts/lib/cm/release_hook_runner.sh",
            "_PP_RUN_OPS_OWN_ROOT=''" if runner_broken else "",
            f"PROJECT_NAME='release-callsite' PROJECT_HOOKS_DIR={str(project_dir / 'hooks')!r}",
            f"REPO_ROOT={str(dev_tree)!r} RC_BRANCH='rc/v1.0.0' ACTIVE_RC='v1.0.0'",
            f"_CM_PROJECT_ROOT={str(project_dir)!r} KANBAN_ROOT={str(kanban_root)!r}",
            _extract_pre_squash_step(),
            "_cm_run_pre_squash_hook_step",
        ]
    )
    result = run_bash(tmp_path, script, extra_env={"KANBAN_ROOT": str(kanban_root)})

    assert result.returncode == 2, f"stdout={result.stdout}\nstderr={result.stderr}"
    assert f"HALT: key={key} flag=--inbox" in result.stdout, result.stdout
    assert marker.exists() is hook_runs, f"stdout={result.stdout}\nstderr={result.stderr}"
    if runner_broken:
        assert "hook runner could not start" in result.stdout, result.stdout
        assert str(hook) not in result.stdout + result.stderr, result.stdout


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
    script = f"source {_LIB_PROJECT_PATHS} && " f"pp_hook_required req-test1 pre-squash"
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

    script = f"source {_LIB_PROJECT_PATHS} && " f"pp_hook_required req-test2 pre-squash"
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
        script = f"source {_LIB_PROJECT_PATHS} && " f"pp_hook_required req-test3 {phase!r}"
        result = run_bash(
            tmp_path,
            script,
            extra_env={"KANBAN_ROOT": str(kanban_root)},
        )
        assert result.returncode == 0, f"Phase {phase!r} failed: {result.stderr}"
        assert (
            result.stdout.strip() == expected
        ), f"Phase {phase!r}: expected {expected!r}, got {result.stdout.strip()!r}"


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
            tmp_path,
            kanban_root,
            "multi-phase",
            project_dir / "hooks",
            dev_tree,
            phase=phase_name,
        )
        assert result.returncode == 0, f"Phase {phase_name!r}: rc={result.returncode}"
        assert "source: in-repo" in result.stdout
        assert f"cm-release-{phase_name}.sh" in result.stdout


# ---------------------------------------------------------------------------
# (g) RC-branch-aware resolution
# ---------------------------------------------------------------------------


def _git(dev_tree: pathlib.Path, *args: str) -> None:
    """Run a git command in *dev_tree*, raising on failure."""
    subprocess.run(
        ["git", "-C", str(dev_tree)] + list(args),
        capture_output=True,
        check=True,
    )


def _make_git_dev_tree(dev_tree: pathlib.Path) -> None:
    """Turn *dev_tree* into a minimal git repo with one commit on main."""
    _git(dev_tree, "init", "--initial-branch=main")
    _git(dev_tree, "config", "--local", "user.email", "test@pgai.local")
    _git(dev_tree, "config", "--local", "user.name", "PGAI Test")
    (dev_tree / "README.md").write_text("# rc-branch-aware test repo\n", encoding="utf-8")
    _git(dev_tree, "add", "README.md")
    _git(dev_tree, "commit", "-m", "Initial commit")


def test_rc_branch_aware_in_repo_hook_resolves_from_ref_not_checkout(
    tmp_path: pathlib.Path,
) -> None:
    """Tier (c) resolves from the RC branch's git tree, not the current checkout.

    The dev tree is left checked out on 'main' (which has NO in-repo hook at
    all); the RC branch carries an executable in-repo hook. Passing the RC
    branch name as the 5th argument must resolve and materialize that hook's
    content even though it is absent from the currently checked-out tree —
    this is the fix for the operator workaround of manually parking the dev
    tree on the RC branch before every release.
    """
    kanban_root, project_dir, dev_tree = _make_sandbox(tmp_path, "rcaware-proj")
    _make_git_dev_tree(dev_tree)

    _git(dev_tree, "checkout", "-b", "rc/v9.9.9")
    hook_rel = ".pgai/hooks/cm-release-pre-squash.sh"
    hook_content = "#!/usr/bin/env bash\necho on-rc-branch\n"
    _write_hook(dev_tree / hook_rel, content=hook_content)
    _git(dev_tree, "add", hook_rel)
    _git(dev_tree, "commit", "-m", "add rc pre-squash hook")
    _git(dev_tree, "checkout", "main")

    # Sanity: the checked-out working tree (main) does NOT have the hook file.
    assert not (dev_tree / hook_rel).exists()

    script = (
        f"{_CM_HALT_STUB}\n"
        f"{_SOURCE_PREAMBLE}\n"
        f"_rc=0\n"
        f"cm_resolve_and_enforce_hook 'rcaware-proj' 'pre-squash' "
        f"{str(project_dir / 'hooks')!r} {str(dev_tree)!r} 'rc/v9.9.9' || _rc=$?\n"
        f'echo "RESOLVED_PATH:$CM_RESOLVED_HOOK_PATH"\n'
        f'echo "IS_TEMP:$CM_RESOLVED_HOOK_IS_TEMP"\n'
        f"exit $_rc\n"
    )
    result = run_bash(tmp_path, script, extra_env={"KANBAN_ROOT": str(kanban_root)})

    assert result.returncode == 0, f"stdout={result.stdout}\nstderr={result.stderr}"
    assert "source: in-repo (rc/v9.9.9)" in result.stdout
    assert "IS_TEMP:1" in result.stdout

    resolved_line = [l for l in result.stdout.splitlines() if l.startswith("RESOLVED_PATH:")][0]
    resolved_path = pathlib.Path(resolved_line.split(":", 1)[1])
    assert resolved_path.exists(), "materialized hook file must exist on disk"
    assert resolved_path.read_text(encoding="utf-8") == hook_content
    assert os.access(resolved_path, os.X_OK), "materialized hook file must be executable"


def test_rc_branch_aware_non_executable_hook_on_ref_is_rejected(
    tmp_path: pathlib.Path,
) -> None:
    """A hook committed with mode 644 on the RC branch is rejected, not silently skipped.

    Mirrors the existing filesystem-based non-executable check (tier (c) with
    no rc_branch), but the source of truth is now the git blob's recorded
    mode rather than the working tree's permission bits.
    """
    kanban_root, project_dir, dev_tree = _make_sandbox(tmp_path, "rcaware-noexec")
    _make_git_dev_tree(dev_tree)

    _git(dev_tree, "checkout", "-b", "rc/v9.9.8")
    hook_rel = ".pgai/hooks/cm-release-pre-tag.sh"
    hook_path = dev_tree / hook_rel
    _write_hook(hook_path, executable=False)
    # git records the mode from the working tree's permission bits at add time;
    # ensure it is non-executable (644) before staging.
    hook_path.chmod(0o644)
    _git(dev_tree, "add", hook_rel)
    _git(dev_tree, "commit", "-m", "add non-executable rc pre-tag hook")
    _git(dev_tree, "checkout", "main")

    script = (
        f"{_CM_HALT_STUB}\n"
        f"{_SOURCE_PREAMBLE}\n"
        f"_rc=0\n"
        f"cm_resolve_and_enforce_hook 'rcaware-noexec' 'pre-tag' "
        f"{str(project_dir / 'hooks')!r} {str(dev_tree)!r} 'rc/v9.9.8' || _rc=$?\n"
        f"exit $_rc\n"
    )
    result = run_bash(tmp_path, script, extra_env={"KANBAN_ROOT": str(kanban_root)})

    assert result.returncode == 1, (
        f"Expected rc=1 for a non-executable in-repo hook on the RC branch.\n"
        f"stdout={result.stdout}\nstderr={result.stderr}"
    )
    assert "not executable" in result.stderr
    assert "git update-index --chmod=+x" in result.stderr


def test_rc_branch_aware_cfg_relative_path_resolves_from_ref(
    tmp_path: pathlib.Path,
) -> None:
    """Tier (a) with a relative cfg path resolves from the RC branch's git tree.

    The cfg-declared relative path is absent from the checked-out working
    tree (main) but present (and executable) on the RC branch.
    """
    kanban_root, project_dir, dev_tree = _make_sandbox(tmp_path, "rcaware-cfg")
    _make_git_dev_tree(dev_tree)

    cfg_text = (
        "[project]\n"
        "project_name = rcaware-cfg\n"
        f"dev_tree_path = {dev_tree}\n"
        "[hooks]\n"
        "cm_release_post_tag_hook = scripts/notify.sh\n"
    )
    (project_dir / "project.cfg").write_text(cfg_text, encoding="utf-8")

    _git(dev_tree, "checkout", "-b", "rc/v9.9.7")
    hook_rel = "scripts/notify.sh"
    hook_content = "#!/usr/bin/env bash\necho notified\n"
    _write_hook(dev_tree / hook_rel, content=hook_content)
    _git(dev_tree, "add", hook_rel)
    _git(dev_tree, "commit", "-m", "add cfg-declared relative hook")
    _git(dev_tree, "checkout", "main")

    assert not (dev_tree / hook_rel).exists()

    script = (
        f"{_CM_HALT_STUB}\n"
        f"{_SOURCE_PREAMBLE}\n"
        f"_rc=0\n"
        f"cm_resolve_and_enforce_hook 'rcaware-cfg' 'post-tag' "
        f"{str(project_dir / 'hooks')!r} {str(dev_tree)!r} 'rc/v9.9.7' || _rc=$?\n"
        f'echo "RESOLVED_PATH:$CM_RESOLVED_HOOK_PATH"\n'
        f"exit $_rc\n"
    )
    result = run_bash(tmp_path, script, extra_env={"KANBAN_ROOT": str(kanban_root)})

    assert result.returncode == 0, f"stdout={result.stdout}\nstderr={result.stderr}"
    assert "source: cfg (rc/v9.9.7)" in result.stdout
    resolved_line = [l for l in result.stdout.splitlines() if l.startswith("RESOLVED_PATH:")][0]
    resolved_path = pathlib.Path(resolved_line.split(":", 1)[1])
    assert resolved_path.read_text(encoding="utf-8") == hook_content


def test_rc_branch_absent_falls_back_to_legacy_filesystem_resolution(
    tmp_path: pathlib.Path,
) -> None:
    """Omitting rc_branch preserves the legacy filesystem-based resolution.

    Locks the backward-compatibility contract for callers (e.g. ship-rc.sh
    call sites left unmigrated) that do not supply a 5th argument: tier (c)
    must still resolve against the dev tree's current checkout exactly as it
    did before RC-branch-aware resolution was introduced.
    """
    kanban_root, project_dir, dev_tree = _make_sandbox(tmp_path, "rcaware-legacy")
    inrepo_hook = dev_tree / ".pgai" / "hooks" / "cm-release-pre-squash.sh"
    _write_hook(inrepo_hook)

    # No 5th argument — identical call shape to _run_resolve's legacy callers.
    result = _run_resolve(tmp_path, kanban_root, "rcaware-legacy", project_dir / "hooks", dev_tree)
    assert result.returncode == 0
    assert "source: in-repo" in result.stdout
    # No branch-qualifier suffix in legacy mode.
    assert "source: in-repo (" not in result.stdout


def test_rc_branch_aware_missing_path_falls_through_to_next_tier(
    tmp_path: pathlib.Path,
) -> None:
    """A cfg-declared relative path absent from the RC branch's tree is treated as unconfigured at tier (a), falling through to tier (b).

    Confirms the resolver does not error or silently succeed with an empty
    path when the RC branch simply never introduced the cfg-declared file —
    it correctly falls through to the next tier, exactly like a filesystem
    miss would in the legacy path.
    """
    kanban_root, project_dir, dev_tree = _make_sandbox(tmp_path, "rcaware-fallthrough")
    _make_git_dev_tree(dev_tree)

    cfg_text = (
        "[project]\n"
        "project_name = rcaware-fallthrough\n"
        f"dev_tree_path = {dev_tree}\n"
        "[hooks]\n"
        "cm_release_pre_squash_hook = scripts/does-not-exist.sh\n"
    )
    (project_dir / "project.cfg").write_text(cfg_text, encoding="utf-8")

    _git(dev_tree, "checkout", "-b", "rc/v9.9.6")
    # Tier (b): kanban-side hook present, so the fall-through has somewhere to land.
    kanban_hook = project_dir / "hooks" / "cm-release-pre-squash.sh"
    _write_hook(kanban_hook)
    _git(dev_tree, "checkout", "main")

    script = (
        f"{_CM_HALT_STUB}\n"
        f"{_SOURCE_PREAMBLE}\n"
        f"_rc=0\n"
        f"cm_resolve_and_enforce_hook 'rcaware-fallthrough' 'pre-squash' "
        f"{str(project_dir / 'hooks')!r} {str(dev_tree)!r} 'rc/v9.9.6' || _rc=$?\n"
        f"exit $_rc\n"
    )
    result = run_bash(tmp_path, script, extra_env={"KANBAN_ROOT": str(kanban_root)})

    assert result.returncode == 0, f"stdout={result.stdout}\nstderr={result.stderr}"
    assert "source: kanban-side" in result.stdout


# ---------------------------------------------------------------------------
# (h) Symlinked hooks on the RC branch
#
# A hook committed as a symlink (git mode 120000) resolves to the regular
# file it points to on the same ref. When it cannot (the target is 100644,
# missing, outside the repository, or not a file), the refusal's remedy must
# run as printed from the repo root with the RC branch checked out, and a
# re-run afterwards must no longer refuse. `git update-index --chmod=+x` on
# a symlink path is the one remedy that may never appear: git refuses it.
# ---------------------------------------------------------------------------

_SYMLINK_HOOK_REL = ".pgai/hooks/cm-release-pre-tag.sh"
_SYMLINK_CFG_HOOK_REL = "scripts/notify.sh"
_TARGET_CONTENT = "#!/usr/bin/env bash\necho target-hook-ran\n"


def _git_out(dev_tree: pathlib.Path, *args: str) -> str:
    """Run a git command in *dev_tree* and return its stdout."""
    return subprocess.run(
        ["git", "-C", str(dev_tree)] + list(args),
        capture_output=True,
        check=True,
        text=True,
    ).stdout


def _tree_mode(dev_tree: pathlib.Path, ref: str, relpath: str) -> str:
    """Return the git mode recorded for *relpath* in *ref*'s tree."""
    return _git_out(dev_tree, "ls-tree", ref, "--", relpath).split()[0]


def _build_rc_repo_with(
    tmp_path: pathlib.Path,
    project_name: str,
    rc_branch: str,
    plant,
    cfg_hook_rel: str = "",
) -> tuple[pathlib.Path, pathlib.Path, pathlib.Path]:
    """Build a throwaway repo whose *rc_branch* commits whatever *plant*
    writes into the dev tree; leaves the dev tree checked out on main.

    *cfg_hook_rel*, when set, declares it as the project's tier (a)
    cm_release_pre_tag_hook (a relative path, so it resolves from the ref).
    """
    kanban_root, project_dir, dev_tree = _make_sandbox(tmp_path, project_name)
    if cfg_hook_rel:
        (project_dir / "project.cfg").write_text(
            "[project]\n"
            f"project_name = {project_name}\n"
            f"dev_tree_path = {dev_tree}\n"
            "[hooks]\n"
            f"cm_release_pre_tag_hook = {cfg_hook_rel}\n",
            encoding="utf-8",
        )
    _make_git_dev_tree(dev_tree)
    _git(dev_tree, "checkout", "-b", rc_branch)
    plant(dev_tree)
    _git(dev_tree, "add", "-A")
    _git(dev_tree, "commit", "-m", "plant release hook entry")
    _git(dev_tree, "checkout", "main")
    return kanban_root, project_dir, dev_tree


def _plant_target(dev_tree: pathlib.Path, relpath: str, mode: int) -> None:
    """Write the hook target script at *relpath* with permission *mode*."""
    target = dev_tree / relpath
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(_TARGET_CONTENT, encoding="utf-8")
    target.chmod(mode)


def _plant_symlink(dev_tree: pathlib.Path, link_rel: str, link_text: str) -> None:
    """Create a symlink at *link_rel* whose content is *link_text*."""
    link = dev_tree / link_rel
    link.parent.mkdir(parents=True, exist_ok=True)
    os.symlink(link_text, link)


def _global(result, name: str) -> str:
    """Return the value of a NAME:value line the driver echoed on stdout."""
    prefix = f"{name}:"
    return next(l for l in result.stdout.splitlines() if l.startswith(prefix)).removeprefix(prefix)


def _to_fix_commands(stderr: str) -> list[str]:
    """Return the numbered 'To fix:' steps from a remedy block that are shell
    commands (every step except the closing 're-run the release' step)."""
    steps = []
    in_block = False
    for line in stderr.splitlines():
        text = line.removeprefix("[cm-release] ERROR:").strip()
        if text == "To fix:":
            in_block = True
            continue
        if in_block:
            parts = text.split(". ", 1)
            if len(parts) == 2 and parts[0].isdigit():
                steps.append(parts[1])
            elif not text:
                break
    assert steps, f"no numbered 'To fix:' steps in stderr:\n{stderr}"
    commands = [s for s in steps if not s.startswith("Re-run ")]
    assert len(commands) == len(steps) - 1, f"unexpected non-command step in {steps}"
    return commands


def _assert_no_chmod_on_symlink(result, link_rel: str) -> None:
    """No stderr line and no CM_HOOK_NONEXEC_FIX_CMD applies --chmod=+x to the link."""
    forbidden = f"--chmod=+x {link_rel}"
    for line in result.stderr.splitlines():
        assert forbidden not in line, f"stderr prints an unrunnable remedy: {line}"
    assert forbidden not in _global(result, "NONEXEC_FIX_CMD")


def _apply_remedy(tmp_path: pathlib.Path, dev_tree: pathlib.Path, rc_branch: str, commands: list[str]) -> None:
    """Run each remedy command through bash from the repo root on *rc_branch*,
    asserting each exits 0 exactly as printed."""
    _git(dev_tree, "checkout", rc_branch)
    for cmd in commands:
        ran = run_bash(tmp_path, cmd, cwd=dev_tree)
        assert ran.returncode == 0, (
            f"remedy command exited {ran.returncode} when run as printed: {cmd}\n"
            f"stdout={ran.stdout}\nstderr={ran.stderr}"
        )


def _assert_resolved_to_target_content(result) -> None:
    """The materialized hook holds the target script, executable, not the link text."""
    resolved = pathlib.Path(_global(result, "RESOLVED_PATH"))
    assert resolved.is_file() and not resolved.is_symlink()
    assert resolved.read_text(encoding="utf-8") == _TARGET_CONTENT
    assert os.access(resolved, os.X_OK), "materialized hook file must be executable"


def test_rc_branch_symlinked_in_repo_hook_resolves_to_target_content(
    tmp_path: pathlib.Path,
) -> None:
    """A tier (c) hook committed as a symlink to a 100755 file resolves to that
    file, materialized with the target's content rather than the link text."""
    rc = "rc/v9.9.9"
    link_text = "../../tools/real-hook.sh"

    def plant(dev_tree: pathlib.Path) -> None:
        _plant_target(dev_tree, "tools/real-hook.sh", 0o755)
        _plant_symlink(dev_tree, _SYMLINK_HOOK_REL, link_text)

    kanban_root, project_dir, dev_tree = _build_rc_repo_with(tmp_path, "symlink-inrepo", rc, plant)
    assert _tree_mode(dev_tree, rc, _SYMLINK_HOOK_REL) == "120000"
    assert _tree_mode(dev_tree, rc, "tools/real-hook.sh") == "100755"

    result = _run_resolve_with_remedy_globals(
        tmp_path,
        kanban_root,
        "symlink-inrepo",
        project_dir / "hooks",
        dev_tree,
        phase="pre-tag",
        rc_branch=rc,
    )
    assert result.returncode == 0, f"stdout={result.stdout}\nstderr={result.stderr}"
    assert f"source: in-repo ({rc})" in result.stdout
    _assert_resolved_to_target_content(result)
    assert pathlib.Path(_global(result, "RESOLVED_PATH")).read_text(encoding="utf-8") != link_text
    assert _global(result, "NONEXEC_FIX_CMD") == ""
    _assert_no_chmod_on_symlink(result, _SYMLINK_HOOK_REL)


def test_rc_branch_symlinked_cfg_relative_hook_resolves_to_target_content(
    tmp_path: pathlib.Path,
) -> None:
    """A tier (a) cfg-declared relative hook committed as a symlink resolves
    to its 100755 target exactly as the tier (c) hook does."""
    rc = "rc/v9.9.8"

    def plant(dev_tree: pathlib.Path) -> None:
        _plant_target(dev_tree, "tools/real-notify.sh", 0o755)
        _plant_symlink(dev_tree, _SYMLINK_CFG_HOOK_REL, "../tools/real-notify.sh")

    kanban_root, project_dir, dev_tree = _build_rc_repo_with(
        tmp_path,
        "symlink-cfg",
        rc,
        plant,
        cfg_hook_rel=_SYMLINK_CFG_HOOK_REL,
    )
    assert _tree_mode(dev_tree, rc, _SYMLINK_CFG_HOOK_REL) == "120000"

    result = _run_resolve_with_remedy_globals(
        tmp_path,
        kanban_root,
        "symlink-cfg",
        project_dir / "hooks",
        dev_tree,
        phase="pre-tag",
        rc_branch=rc,
    )
    assert result.returncode == 0, f"stdout={result.stdout}\nstderr={result.stderr}"
    assert f"source: cfg ({rc})" in result.stdout
    _assert_resolved_to_target_content(result)
    assert _global(result, "NONEXEC_FIX_CMD") == ""
    _assert_no_chmod_on_symlink(result, _SYMLINK_CFG_HOOK_REL)


def test_rc_branch_symlink_chain_resolves_to_final_regular_file(
    tmp_path: pathlib.Path,
) -> None:
    """A symlink to a symlink is followed, each link relative to its own
    directory, to the committed regular file at the end of the chain."""
    rc = "rc/v9.9.7"

    def plant(dev_tree: pathlib.Path) -> None:
        _plant_target(dev_tree, "tools/real-hook.sh", 0o755)
        _plant_symlink(dev_tree, "tools/current-hook.sh", "real-hook.sh")
        _plant_symlink(dev_tree, _SYMLINK_HOOK_REL, "../../tools/current-hook.sh")

    kanban_root, project_dir, dev_tree = _build_rc_repo_with(tmp_path, "symlink-chain", rc, plant)
    result = _run_resolve_with_remedy_globals(
        tmp_path,
        kanban_root,
        "symlink-chain",
        project_dir / "hooks",
        dev_tree,
        phase="pre-tag",
        rc_branch=rc,
    )
    assert result.returncode == 0, f"stdout={result.stdout}\nstderr={result.stderr}"
    _assert_resolved_to_target_content(result)


@pytest.mark.parametrize("channel", ["stderr", "fix_cmd"])
@pytest.mark.parametrize("tier", ["in-repo", "cfg"])
def test_rc_branch_symlink_to_nonexecutable_target_remedy_clears_refusal(
    tmp_path: pathlib.Path,
    tier: str,
    channel: str,
) -> None:
    """A symlink to a 100644 file is refused with a remedy that chmods the
    target, not the link; run as printed (either the stderr 'To fix:' steps
    or CM_HOOK_NONEXEC_FIX_CMD), it clears the refusal and the re-run
    resolves the hook to the target's content."""
    rc = "rc/v9.9.6"
    link_rel = _SYMLINK_HOOK_REL if tier == "in-repo" else _SYMLINK_CFG_HOOK_REL
    link_text = "../../tools/plain-hook.sh" if tier == "in-repo" else "../tools/plain-hook.sh"

    def plant(dev_tree: pathlib.Path) -> None:
        _plant_target(dev_tree, "tools/plain-hook.sh", 0o644)
        _plant_symlink(dev_tree, link_rel, link_text)

    kanban_root, project_dir, dev_tree = _build_rc_repo_with(
        tmp_path,
        "symlink-plain",
        rc,
        plant,
        cfg_hook_rel=_SYMLINK_CFG_HOOK_REL if tier == "cfg" else "",
    )
    assert _tree_mode(dev_tree, rc, link_rel) == "120000"
    assert _tree_mode(dev_tree, rc, "tools/plain-hook.sh") == "100644"

    first = _run_resolve_with_remedy_globals(
        tmp_path,
        kanban_root,
        "symlink-plain",
        project_dir / "hooks",
        dev_tree,
        phase="pre-tag",
        rc_branch=rc,
    )
    assert first.returncode == 1, f"stdout={first.stdout}\nstderr={first.stderr}"
    _assert_no_chmod_on_symlink(first, link_rel)
    fix_cmd = _global(first, "NONEXEC_FIX_CMD")
    assert fix_cmd.startswith("git update-index --chmod=+x tools/plain-hook.sh && git commit ")
    assert "<" not in fix_cmd and ">" not in fix_cmd
    assert _global(first, "NONEXEC_PATH") == f"{link_rel} (symlink to tools/plain-hook.sh, on {rc})"

    commands = _to_fix_commands(first.stderr) if channel == "stderr" else [fix_cmd]
    _apply_remedy(tmp_path, dev_tree, rc, commands)
    # The re-run resolves from the ref, so it is left on the RC branch:
    # `git update-index --chmod=+x` changes the index, not the working
    # file's permission bits, so a checkout away would see a local change.

    second = _run_resolve_with_remedy_globals(
        tmp_path,
        kanban_root,
        "symlink-plain",
        project_dir / "hooks",
        dev_tree,
        phase="pre-tag",
        rc_branch=rc,
    )
    assert second.returncode == 0, f"refusal not cleared: stdout={second.stdout}\nstderr={second.stderr}"
    _assert_resolved_to_target_content(second)


def _plant_dangling(dev_tree: pathlib.Path) -> None:
    _plant_symlink(dev_tree, _SYMLINK_HOOK_REL, "../../tools/never-committed.sh")


def _plant_escaping(dev_tree: pathlib.Path) -> None:
    _plant_symlink(dev_tree, _SYMLINK_HOOK_REL, "../../../outside-hook.sh")


def _plant_absolute(dev_tree: pathlib.Path) -> None:
    outside = dev_tree.parent / "outside-hook.sh"
    outside.write_text(_TARGET_CONTENT, encoding="utf-8")
    outside.chmod(0o755)
    _plant_symlink(dev_tree, _SYMLINK_HOOK_REL, str(outside))


def _plant_directory_link(dev_tree: pathlib.Path) -> None:
    _plant_target(dev_tree, "tools/real-hook.sh", 0o755)
    _plant_symlink(dev_tree, _SYMLINK_HOOK_REL, "../../tools")


def _plant_gitlink(dev_tree: pathlib.Path) -> None:
    head = _git_out(dev_tree, "rev-parse", "HEAD").strip()
    # The submodule's (empty) working directory keeps `git add -A` from
    # staging the gitlink's removal.
    (dev_tree / _SYMLINK_HOOK_REL).mkdir(parents=True)
    _git(dev_tree, "update-index", "--add", "--cacheinfo", f"160000,{head},{_SYMLINK_HOOK_REL}")


@pytest.mark.parametrize("channel", ["stderr", "fix_cmd"])
@pytest.mark.parametrize(
    "plant, entry_mode",
    [
        (_plant_dangling, "120000"),
        (_plant_escaping, "120000"),
        (_plant_absolute, "120000"),
        (_plant_directory_link, "120000"),
        (_plant_gitlink, "160000"),
    ],
    ids=["target-not-committed", "target-above-repo-root", "absolute-target", "target-is-directory", "submodule-entry"],
)
def test_rc_branch_unresolvable_hook_entry_remedy_clears_refusal(
    tmp_path: pathlib.Path,
    plant,
    entry_mode: str,
    channel: str,
) -> None:
    """A hook entry that cannot reach a committed regular file — a symlink
    whose target is missing, outside the repository or a directory, or a
    submodule at the hook path — is refused without any --chmod=+x remedy;
    the printed remedy runs as printed and the re-run no longer refuses."""
    rc = "rc/v9.9.5"
    kanban_root, project_dir, dev_tree = _build_rc_repo_with(tmp_path, "symlink-unusable", rc, plant)
    assert _tree_mode(dev_tree, rc, _SYMLINK_HOOK_REL) == entry_mode

    first = _run_resolve_with_remedy_globals(
        tmp_path,
        kanban_root,
        "symlink-unusable",
        project_dir / "hooks",
        dev_tree,
        phase="pre-tag",
        rc_branch=rc,
    )
    assert first.returncode == 1, f"stdout={first.stdout}\nstderr={first.stderr}"
    assert "--chmod=+x" not in first.stderr
    fix_cmd = _global(first, "NONEXEC_FIX_CMD")
    assert "--chmod=+x" not in fix_cmd
    assert "<" not in fix_cmd and ">" not in fix_cmd
    assert "Cause(s):" in first.stderr and "Rule:" in first.stderr

    commands = _to_fix_commands(first.stderr) if channel == "stderr" else [fix_cmd]
    _apply_remedy(tmp_path, dev_tree, rc, commands)
    # The re-run resolves from the ref, so it is left on the RC branch:
    # `git update-index --chmod=+x` changes the index, not the working
    # file's permission bits, so a checkout away would see a local change.

    second = _run_resolve_with_remedy_globals(
        tmp_path,
        kanban_root,
        "symlink-unusable",
        project_dir / "hooks",
        dev_tree,
        phase="pre-tag",
        rc_branch=rc,
    )
    assert second.returncode == 0, f"refusal not cleared: stdout={second.stdout}\nstderr={second.stderr}"
    assert _global(second, "NONEXEC_FIX_CMD") == ""


def test_filesystem_symlinked_hook_without_rc_branch_follows_the_link(
    tmp_path: pathlib.Path,
) -> None:
    """With no rc_branch, the working-tree check follows a symlinked in-repo
    hook: a link to a non-executable file is refused with `chmod +x` on the
    link, which changes the target, exits 0 and clears the refusal."""
    kanban_root, project_dir, dev_tree = _make_sandbox(tmp_path, "symlink-fs")
    _plant_target(dev_tree, "tools/real-hook.sh", 0o644)
    _plant_symlink(dev_tree, _SYMLINK_HOOK_REL, "../../tools/real-hook.sh")
    link = dev_tree / _SYMLINK_HOOK_REL

    first = _run_resolve_with_remedy_globals(
        tmp_path,
        kanban_root,
        "symlink-fs",
        project_dir / "hooks",
        dev_tree,
        phase="pre-tag",
    )
    assert first.returncode == 1
    fix_cmd = _global(first, "NONEXEC_FIX_CMD")
    assert fix_cmd == f"chmod +x {link}"
    ran = run_bash(tmp_path, fix_cmd, cwd=dev_tree)
    assert ran.returncode == 0, ran.stderr

    second = _run_resolve_with_remedy_globals(
        tmp_path,
        kanban_root,
        "symlink-fs",
        project_dir / "hooks",
        dev_tree,
        phase="pre-tag",
    )
    assert second.returncode == 0, f"stdout={second.stdout}\nstderr={second.stderr}"
    assert _global(second, "RESOLVED_PATH") == str(link)


# ---------------------------------------------------------------------------
# (i) Remedies quote every path they print
#
# A hook path or symlink target holding a space (or a quote) must still yield
# a remedy that runs as printed: each interpolated path is shell-quoted, and
# the commit message is one quoted word.  Each case runs every stderr
# 'To fix:' command and CM_HOOK_NONEXEC_FIX_CMD through bash from the repo
# root on the RC branch, then re-runs the resolver and expects no refusal.
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("channel", ["stderr", "fix_cmd"])
@pytest.mark.parametrize("target_rel", ["tools/my hook.sh", "tools/it's a hook.sh"])
def test_rc_branch_symlink_to_spaced_nonexecutable_target_remedy_runs_as_printed(
    tmp_path: pathlib.Path,
    target_rel: str,
    channel: str,
) -> None:
    """A pre-tag symlink whose target path holds a space (or a quote) at
    100644 is refused with a quoted remedy that exits 0 as printed, and the
    re-run resolves the hook to the target's content."""
    rc = "rc/v9.9.9"

    def plant(dev_tree: pathlib.Path) -> None:
        _plant_target(dev_tree, target_rel, 0o644)
        _plant_symlink(dev_tree, _SYMLINK_HOOK_REL, f"../../{target_rel}")

    kanban_root, project_dir, dev_tree = _build_rc_repo_with(tmp_path, "spaced-target", rc, plant)
    assert _tree_mode(dev_tree, rc, _SYMLINK_HOOK_REL) == "120000"
    assert _tree_mode(dev_tree, rc, target_rel) == "100644"

    first = _run_resolve_with_remedy_globals(
        tmp_path,
        kanban_root,
        "spaced-target",
        project_dir / "hooks",
        dev_tree,
        phase="pre-tag",
        rc_branch=rc,
    )
    assert first.returncode == 1, f"stdout={first.stdout}\nstderr={first.stderr}"
    fix_cmd = _global(first, "NONEXEC_FIX_CMD")
    assert f"--chmod=+x {target_rel} " not in fix_cmd, f"target path printed unquoted: {fix_cmd}"

    commands = _to_fix_commands(first.stderr) if channel == "stderr" else [fix_cmd]
    _apply_remedy(tmp_path, dev_tree, rc, commands)
    assert _tree_mode(dev_tree, rc, target_rel) == "100755"

    second = _run_resolve_with_remedy_globals(
        tmp_path,
        kanban_root,
        "spaced-target",
        project_dir / "hooks",
        dev_tree,
        phase="pre-tag",
        rc_branch=rc,
    )
    assert second.returncode == 0, f"refusal not cleared: stdout={second.stdout}\nstderr={second.stderr}"
    _assert_resolved_to_target_content(second)


@pytest.mark.parametrize("channel", ["stderr", "fix_cmd"])
def test_rc_branch_cfg_hook_with_spaced_relative_path_remedy_runs_as_printed(
    tmp_path: pathlib.Path,
    channel: str,
) -> None:
    """A tier (a) cm_release_pre_tag_hook relative path holding a space,
    committed at 100644, is refused with a quoted chmod remedy that exits 0
    as printed; the re-run resolves the hook from the ref."""
    rc = "rc/v9.9.9"
    hook_rel = "scripts/my hook.sh"

    def plant(dev_tree: pathlib.Path) -> None:
        _plant_target(dev_tree, hook_rel, 0o644)

    kanban_root, project_dir, dev_tree = _build_rc_repo_with(
        tmp_path,
        "spaced-cfg",
        rc,
        plant,
        cfg_hook_rel=hook_rel,
    )
    assert _tree_mode(dev_tree, rc, hook_rel) == "100644"

    first = _run_resolve_with_remedy_globals(
        tmp_path,
        kanban_root,
        "spaced-cfg",
        project_dir / "hooks",
        dev_tree,
        phase="pre-tag",
        rc_branch=rc,
    )
    assert first.returncode == 1, f"stdout={first.stdout}\nstderr={first.stderr}"
    assert _global(first, "NONEXEC_PATH") == f"{hook_rel} (on {rc})"

    fix_cmd = _global(first, "NONEXEC_FIX_CMD")
    commands = _to_fix_commands(first.stderr) if channel == "stderr" else [fix_cmd]
    _apply_remedy(tmp_path, dev_tree, rc, commands)
    assert _tree_mode(dev_tree, rc, hook_rel) == "100755"

    second = _run_resolve_with_remedy_globals(
        tmp_path,
        kanban_root,
        "spaced-cfg",
        project_dir / "hooks",
        dev_tree,
        phase="pre-tag",
        rc_branch=rc,
    )
    assert second.returncode == 0, f"refusal not cleared: stdout={second.stdout}\nstderr={second.stderr}"
    assert f"source: cfg ({rc})" in second.stdout
    _assert_resolved_to_target_content(second)


@pytest.mark.parametrize("channel", ["stderr", "fix_cmd"])
def test_rc_branch_unresolvable_symlink_at_spaced_cfg_path_git_rm_runs_as_printed(
    tmp_path: pathlib.Path,
    channel: str,
) -> None:
    """An unresolvable symlink at a cfg-declared hook path holding a space is
    refused with a quoted git rm remedy that exits 0 as printed; with the
    entry removed the tier reads as empty and the re-run does not refuse."""
    rc = "rc/v9.9.9"
    hook_rel = "scripts/my notify.sh"

    def plant(dev_tree: pathlib.Path) -> None:
        _plant_symlink(dev_tree, hook_rel, "../tools/never-committed.sh")

    kanban_root, project_dir, dev_tree = _build_rc_repo_with(
        tmp_path,
        "spaced-unusable",
        rc,
        plant,
        cfg_hook_rel=hook_rel,
    )
    assert _tree_mode(dev_tree, rc, hook_rel) == "120000"

    first = _run_resolve_with_remedy_globals(
        tmp_path,
        kanban_root,
        "spaced-unusable",
        project_dir / "hooks",
        dev_tree,
        phase="pre-tag",
        rc_branch=rc,
    )
    assert first.returncode == 1, f"stdout={first.stdout}\nstderr={first.stderr}"
    fix_cmd = _global(first, "NONEXEC_FIX_CMD")
    assert fix_cmd.startswith("git rm -r --quiet -- "), fix_cmd

    commands = _to_fix_commands(first.stderr) if channel == "stderr" else [fix_cmd]
    _apply_remedy(tmp_path, dev_tree, rc, commands)
    assert _git_out(dev_tree, "ls-tree", rc, "--", hook_rel).strip() == ""

    second = _run_resolve_with_remedy_globals(
        tmp_path,
        kanban_root,
        "spaced-unusable",
        project_dir / "hooks",
        dev_tree,
        phase="pre-tag",
        rc_branch=rc,
    )
    assert second.returncode == 0, f"refusal not cleared: stdout={second.stdout}\nstderr={second.stderr}"
    assert _global(second, "NONEXEC_FIX_CMD") == ""
