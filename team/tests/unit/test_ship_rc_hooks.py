"""
test_ship_rc_hooks.py
=====================
Structural and sibling-parity tests for ship-rc.sh hook integration introduced
by CODER-20260711-028-ship-rc-parity.

Covers:
  (A) Structural: the resolution/printing body appears exactly once under
      team/scripts/lib/ and zero times inline in either ship-rc.sh or release.sh.
      (One-implementation rule enforcement.)

  (B) Sibling-parity: ship-rc.sh calls cm_resolve_and_enforce_hook at all three
      phases (pre-squash, pre-tag, post-tag) and produces the same output format
      as release.sh on identical sandbox fixtures — same printed lines, same
      enforcement behavior.

All shell tests use the shell_harness.run_bash pattern with synthetic temp directories.
No live kanban root is touched.
"""

from __future__ import annotations

import pathlib
import stat

import pytest

from tests.unit.shell_harness import run_bash

# ---------------------------------------------------------------------------
# Paths (relative to team/, where pytest runs from)
# ---------------------------------------------------------------------------

_SCRIPTS_ROOT = pathlib.Path("scripts")
_LIB_DIR = _SCRIPTS_ROOT / "lib"
_SHIP_RC = _SCRIPTS_ROOT / "ship-rc.sh"
_RELEASE_SH = _SCRIPTS_ROOT / "cm" / "release.sh"
_LIB_HOOKS = _LIB_DIR / "cm_release_hooks.sh"
_LIB_PROJECT_PATHS = _LIB_DIR / "project_paths.sh"
_LIB_PROJECTS = _LIB_DIR / "projects.sh"

# Source preamble for tests that call cm_resolve_and_enforce_hook directly.
_SOURCE_PREAMBLE = (
    f"source {_LIB_PROJECT_PATHS} && "
    f"source {_LIB_PROJECTS} && "
    f"source {_LIB_HOOKS}"
)

# Minimal cm_halt stub: prints the reason and exits 2.
_CM_HALT_STUB = (
    "cm_halt() { "
    "  echo \"HALT: trigger=$1 reason=$2\"; "
    "  exit 2; "
    "}"
)


# ---------------------------------------------------------------------------
# Helpers shared by parity tests
# ---------------------------------------------------------------------------


def _make_sandbox(
    root: pathlib.Path,
    project_name: str,
) -> tuple[pathlib.Path, pathlib.Path, pathlib.Path]:
    """Create a minimal sandbox kanban tree and dev tree.

    Returns (kanban_root, project_kanban_dir, dev_tree_path).
    """
    kanban_root = root / "kanban"
    kanban_root.mkdir(parents=True, exist_ok=True)

    project_kanban_dir = kanban_root / "projects" / project_name
    project_kanban_dir.mkdir(parents=True, exist_ok=True)

    dev_tree = root / "dev" / project_name
    dev_tree.mkdir(parents=True, exist_ok=True)

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
    """Call cm_resolve_and_enforce_hook and return the BashResult."""
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


# ---------------------------------------------------------------------------
# (A) Structural tests — one-implementation rule enforcement
# ---------------------------------------------------------------------------


class TestStructuralOneImplementation:
    """
    The resolution/printing body lives in exactly one place (lib/) and has
    zero inline copies in either ship-rc.sh or release.sh.

    We use the internal variable name '_creh_phase' as the canary: it is
    a distinctive identifier that only appears in the body of
    cm_resolve_and_enforce_hook (not in callers, not in comments on callers).
    """

    _CANARY = "_creh_phase"

    def test_resolution_body_appears_exactly_once_under_lib(self) -> None:
        """The resolution/printing body (_creh_phase) is defined exactly once under lib/."""
        lib_dir = _LIB_DIR
        assert lib_dir.is_dir(), f"lib/ directory not found: {lib_dir}"

        hits = []
        for f in lib_dir.rglob("*.sh"):
            text = f.read_text(encoding="utf-8", errors="replace")
            count = text.count(self._CANARY)
            if count > 0:
                hits.append((f, count))

        total = sum(c for _, c in hits)
        assert total > 0, (
            f"Canary '{self._CANARY}' not found anywhere under {lib_dir}. "
            "cm_resolve_and_enforce_hook body may have been moved or renamed."
        )
        # The body must appear in exactly one file under lib/.
        assert len(hits) == 1, (
            f"Canary '{self._CANARY}' found in {len(hits)} files under {lib_dir} "
            f"(expected 1): {[str(p) for p, _ in hits]}"
        )
        assert hits[0][0].name == "cm_release_hooks.sh", (
            f"Canary found in unexpected file: {hits[0][0]}; expected cm_release_hooks.sh"
        )

    def test_resolution_body_absent_from_ship_rc(self) -> None:
        """ship-rc.sh must not inline the resolution/printing body."""
        assert _SHIP_RC.is_file(), f"ship-rc.sh not found: {_SHIP_RC}"
        text = _SHIP_RC.read_text(encoding="utf-8", errors="replace")
        count = text.count(self._CANARY)
        assert count == 0, (
            f"Canary '{self._CANARY}' found {count} time(s) inline in {_SHIP_RC}. "
            "The resolution body must stay in lib/cm_release_hooks.sh, not be copied."
        )

    def test_resolution_body_absent_from_release_sh(self) -> None:
        """release.sh must not inline the resolution/printing body."""
        assert _RELEASE_SH.is_file(), f"release.sh not found: {_RELEASE_SH}"
        text = _RELEASE_SH.read_text(encoding="utf-8", errors="replace")
        count = text.count(self._CANARY)
        assert count == 0, (
            f"Canary '{self._CANARY}' found {count} time(s) inline in {_RELEASE_SH}. "
            "The resolution body must stay in lib/cm_release_hooks.sh, not be copied."
        )

    def _count_actual_calls(self, path: pathlib.Path) -> int:
        """Count non-comment lines that invoke cm_resolve_and_enforce_hook."""
        call_lines = [
            line
            for line in path.read_text(encoding="utf-8", errors="replace").splitlines()
            if "cm_resolve_and_enforce_hook" in line
            and not line.lstrip().startswith("#")
        ]
        return len(call_lines)

    def test_ship_rc_calls_cm_resolve_and_enforce_hook_three_times(self) -> None:
        """ship-rc.sh has exactly three cm_resolve_and_enforce_hook call sites (non-comment)."""
        assert _SHIP_RC.is_file(), f"ship-rc.sh not found: {_SHIP_RC}"
        count = self._count_actual_calls(_SHIP_RC)
        assert count == 3, (
            f"Expected exactly 3 cm_resolve_and_enforce_hook calls (non-comment) in ship-rc.sh, "
            f"found {count}. Each release phase (pre-squash, pre-tag, post-tag) needs one."
        )

    def test_release_sh_calls_cm_resolve_and_enforce_hook_three_times(self) -> None:
        """release.sh has exactly three cm_resolve_and_enforce_hook call sites (non-comment)."""
        assert _RELEASE_SH.is_file(), f"release.sh not found: {_RELEASE_SH}"
        count = self._count_actual_calls(_RELEASE_SH)
        assert count == 3, (
            f"Expected exactly 3 cm_resolve_and_enforce_hook calls (non-comment) in release.sh, "
            f"found {count}. Each release phase (pre-squash, pre-tag, post-tag) needs one."
        )


# ---------------------------------------------------------------------------
# (B) Sibling-parity tests
#
# These tests verify that ship-rc.sh's hook-resolution call sites produce the
# same output format and enforcement behavior as release.sh on identical sandbox
# fixtures.  We test the shared cm_resolve_and_enforce_hook function directly
# (since that is the implementation both scripts use), verifying that the call
# signatures and behaviors match for all three phases.
# ---------------------------------------------------------------------------


class TestSiblingParityResolutionOutput:
    """
    ship-rc.sh and release.sh both call cm_resolve_and_enforce_hook with the
    same argument shape and the same three phases.  Parity means: identical
    printed lines, identical enforcement, identical CM_RESOLVED_HOOK_PATH for
    identical sandbox fixtures.

    We verify this by running cm_resolve_and_enforce_hook directly in a
    sandbox — the same function both scripts call — for each of the three
    phases.  This exercises the shared implementation (which is the contract).
    """

    def test_pre_squash_prints_none_configured_when_no_hook(
        self, tmp_path: pathlib.Path
    ) -> None:
        """Both scripts print 'pre-squash hook: none configured' when no hook exists."""
        kanban_root, project_dir, dev_tree = _make_sandbox(tmp_path, "parity-pre-squash")

        result = _run_resolve(
            tmp_path, kanban_root, "parity-pre-squash",
            project_dir / "hooks", dev_tree,
            phase="pre-squash",
        )
        assert result.returncode == 0
        assert "pre-squash hook: none configured" in result.stdout

    def test_pre_tag_prints_none_configured_when_no_hook(
        self, tmp_path: pathlib.Path
    ) -> None:
        """Both scripts print 'pre-tag hook: none configured' when no hook exists."""
        kanban_root, project_dir, dev_tree = _make_sandbox(tmp_path, "parity-pre-tag")

        result = _run_resolve(
            tmp_path, kanban_root, "parity-pre-tag",
            project_dir / "hooks", dev_tree,
            phase="pre-tag",
        )
        assert result.returncode == 0
        assert "pre-tag hook: none configured" in result.stdout

    def test_post_tag_prints_none_configured_when_no_hook(
        self, tmp_path: pathlib.Path
    ) -> None:
        """Both scripts print 'post-tag hook: none configured' when no hook exists."""
        kanban_root, project_dir, dev_tree = _make_sandbox(tmp_path, "parity-post-tag")

        result = _run_resolve(
            tmp_path, kanban_root, "parity-post-tag",
            project_dir / "hooks", dev_tree,
            phase="post-tag",
        )
        assert result.returncode == 0
        assert "post-tag hook: none configured" in result.stdout

    def test_pre_squash_prints_path_and_source_when_kanban_side_hook_present(
        self, tmp_path: pathlib.Path
    ) -> None:
        """Both scripts print '<phase> hook: <path> (source: kanban-side)' when kanban-side hook present."""
        kanban_root, project_dir, dev_tree = _make_sandbox(tmp_path, "parity-ks-pre")
        hook = project_dir / "hooks" / "cm-release-pre-squash.sh"
        _write_hook(hook)

        result = _run_resolve(
            tmp_path, kanban_root, "parity-ks-pre",
            project_dir / "hooks", dev_tree,
            phase="pre-squash",
        )
        assert result.returncode == 0
        assert "pre-squash hook:" in result.stdout
        assert "source: kanban-side" in result.stdout

    def test_pre_tag_prints_path_and_source_when_inrepo_hook_present(
        self, tmp_path: pathlib.Path
    ) -> None:
        """Both scripts print '<phase> hook: <path> (source: in-repo)' when in-repo hook present."""
        kanban_root, project_dir, dev_tree = _make_sandbox(tmp_path, "parity-ir-pre-tag")
        hook = dev_tree / ".pgai" / "hooks" / "cm-release-pre-tag.sh"
        _write_hook(hook)

        result = _run_resolve(
            tmp_path, kanban_root, "parity-ir-pre-tag",
            project_dir / "hooks", dev_tree,
            phase="pre-tag",
        )
        assert result.returncode == 0
        assert "pre-tag hook:" in result.stdout
        assert "source: in-repo" in result.stdout
        assert str(hook) in result.stdout

    def test_post_tag_prints_path_and_source_when_cfg_hook_present(
        self, tmp_path: pathlib.Path
    ) -> None:
        """Both scripts print '<phase> hook: <path> (source: cfg)' when cfg hook configured."""
        kanban_root, project_dir, dev_tree = _make_sandbox(tmp_path, "parity-cfg-post")
        cfg_hook = dev_tree / "post-tag-hook.sh"
        _write_hook(cfg_hook)
        cfg_text = (
            "[project]\n"
            "project_name = parity-cfg-post\n"
            f"dev_tree_path = {dev_tree}\n"
            "[hooks]\n"
            "cm_release_post_tag_hook = post-tag-hook.sh\n"
        )
        (project_dir / "project.cfg").write_text(cfg_text, encoding="utf-8")

        result = _run_resolve(
            tmp_path, kanban_root, "parity-cfg-post",
            project_dir / "hooks", dev_tree,
            phase="post-tag",
        )
        assert result.returncode == 0
        assert "post-tag hook:" in result.stdout
        assert "source: cfg" in result.stdout


class TestSiblingParityEnforcement:
    """
    Required-hook enforcement (HALT behavior) is identical for both scripts
    because both call the same cm_resolve_and_enforce_hook implementation.
    These tests verify the shared enforcement semantics at each phase.
    """

    def test_required_pre_squash_halts_when_no_hook(self, tmp_path: pathlib.Path) -> None:
        """Both scripts HALT before pre-squash when required=true and no hook."""
        kanban_root, project_dir, dev_tree = _make_sandbox(tmp_path, "enf-pre-squash")
        cfg_text = (
            "[project]\n"
            "project_name = enf-pre-squash\n"
            f"dev_tree_path = {dev_tree}\n"
            "[hooks]\n"
            "cm_release_pre_squash_hook_required = true\n"
        )
        (project_dir / "project.cfg").write_text(cfg_text, encoding="utf-8")

        result = _run_resolve(
            tmp_path, kanban_root, "enf-pre-squash",
            project_dir / "hooks", dev_tree,
            phase="pre-squash",
        )
        # cm_halt stub exits 2
        assert result.returncode == 2, (
            f"Expected HALT (rc=2) for required pre-squash hook, got {result.returncode}; "
            f"stdout: {result.stdout}; stderr: {result.stderr}"
        )

    def test_required_pre_tag_halts_when_no_hook(self, tmp_path: pathlib.Path) -> None:
        """Both scripts HALT before pre-tag when required=true and no hook."""
        kanban_root, project_dir, dev_tree = _make_sandbox(tmp_path, "enf-pre-tag")
        cfg_text = (
            "[project]\n"
            "project_name = enf-pre-tag\n"
            f"dev_tree_path = {dev_tree}\n"
            "[hooks]\n"
            "cm_release_pre_tag_hook_required = true\n"
        )
        (project_dir / "project.cfg").write_text(cfg_text, encoding="utf-8")

        result = _run_resolve(
            tmp_path, kanban_root, "enf-pre-tag",
            project_dir / "hooks", dev_tree,
            phase="pre-tag",
        )
        assert result.returncode == 2, (
            f"Expected HALT (rc=2) for required pre-tag hook, got {result.returncode}; "
            f"stdout: {result.stdout}; stderr: {result.stderr}"
        )

    def test_required_post_tag_halts_when_no_hook(self, tmp_path: pathlib.Path) -> None:
        """Both scripts HALT before post-tag when required=true and no hook."""
        kanban_root, project_dir, dev_tree = _make_sandbox(tmp_path, "enf-post-tag")
        cfg_text = (
            "[project]\n"
            "project_name = enf-post-tag\n"
            f"dev_tree_path = {dev_tree}\n"
            "[hooks]\n"
            "cm_release_post_tag_hook_required = true\n"
        )
        (project_dir / "project.cfg").write_text(cfg_text, encoding="utf-8")

        result = _run_resolve(
            tmp_path, kanban_root, "enf-post-tag",
            project_dir / "hooks", dev_tree,
            phase="post-tag",
        )
        assert result.returncode == 2, (
            f"Expected HALT (rc=2) for required post-tag hook, got {result.returncode}; "
            f"stdout: {result.stdout}; stderr: {result.stderr}"
        )

    def test_halt_message_names_phase_pre_squash(self, tmp_path: pathlib.Path) -> None:
        """HALT message names the phase 'pre-squash'."""
        kanban_root, project_dir, dev_tree = _make_sandbox(tmp_path, "enf-msg-pre-squash")
        cfg_text = (
            "[project]\n"
            "project_name = enf-msg-pre-squash\n"
            f"dev_tree_path = {dev_tree}\n"
            "[hooks]\n"
            "cm_release_pre_squash_hook_required = true\n"
        )
        (project_dir / "project.cfg").write_text(cfg_text, encoding="utf-8")

        result = _run_resolve(
            tmp_path, kanban_root, "enf-msg-pre-squash",
            project_dir / "hooks", dev_tree,
            phase="pre-squash",
        )
        assert result.returncode == 2
        combined = result.stdout + result.stderr
        assert "pre-squash" in combined

    def test_halt_message_names_three_locations(self, tmp_path: pathlib.Path) -> None:
        """HALT message names all three searched locations."""
        kanban_root, project_dir, dev_tree = _make_sandbox(tmp_path, "enf-locs")
        cfg_text = (
            "[project]\n"
            "project_name = enf-locs\n"
            f"dev_tree_path = {dev_tree}\n"
            "[hooks]\n"
            "cm_release_pre_squash_hook_required = true\n"
        )
        (project_dir / "project.cfg").write_text(cfg_text, encoding="utf-8")

        result = _run_resolve(
            tmp_path, kanban_root, "enf-locs",
            project_dir / "hooks", dev_tree,
            phase="pre-squash",
        )
        assert result.returncode == 2
        combined = result.stdout + result.stderr
        # All three tiers must be named
        assert "cfg" in combined
        assert "kanban-side" in combined or "kanban" in combined
        assert "in-repo" in combined

    def test_required_false_proceeds_with_none_configured(self, tmp_path: pathlib.Path) -> None:
        """When required=false and no hook, both scripts print 'none configured' and proceed."""
        kanban_root, project_dir, dev_tree = _make_sandbox(tmp_path, "enf-false-pre")
        cfg_text = (
            "[project]\n"
            "project_name = enf-false-pre\n"
            f"dev_tree_path = {dev_tree}\n"
            "[hooks]\n"
            "cm_release_pre_squash_hook_required = false\n"
        )
        (project_dir / "project.cfg").write_text(cfg_text, encoding="utf-8")

        result = _run_resolve(
            tmp_path, kanban_root, "enf-false-pre",
            project_dir / "hooks", dev_tree,
            phase="pre-squash",
        )
        assert result.returncode == 0
        assert "none configured" in result.stdout

    def test_all_three_phases_planted_in_sandbox_resolve_correctly(
        self, tmp_path: pathlib.Path
    ) -> None:
        """
        Plant hooks at all three tiers for all three phases and verify each
        phase resolves to its own hook — same behavior for both scripts.
        """
        kanban_root, project_dir, dev_tree = _make_sandbox(tmp_path, "parity-all-phases")

        # Plant a kanban-side hook for each phase
        for phase in ("pre-squash", "pre-tag", "post-tag"):
            hook = project_dir / "hooks" / f"cm-release-{phase}.sh"
            _write_hook(hook)

        for phase in ("pre-squash", "pre-tag", "post-tag"):
            result = _run_resolve(
                tmp_path, kanban_root, "parity-all-phases",
                project_dir / "hooks", dev_tree,
                phase=phase,
            )
            assert result.returncode == 0, (
                f"Phase {phase!r}: rc={result.returncode}; stderr={result.stderr}"
            )
            assert f"{phase} hook:" in result.stdout, (
                f"Phase {phase!r}: expected resolution line in stdout; got: {result.stdout!r}"
            )
            assert "source: kanban-side" in result.stdout, (
                f"Phase {phase!r}: expected 'source: kanban-side'; got: {result.stdout!r}"
            )


class TestSiblingParityNonExecutableHook:
    """
    Non-executable in-repo hook behavior is identical in both scripts.
    Both call cm_resolve_and_enforce_hook which returns 1 for this case.
    """

    def test_nonexecutable_inrepo_hook_returns_error_for_pre_squash(
        self, tmp_path: pathlib.Path
    ) -> None:
        """A non-executable pre-squash in-repo hook triggers a loud error (rc=1)."""
        kanban_root, project_dir, dev_tree = _make_sandbox(tmp_path, "noexec-ps")
        hook = dev_tree / ".pgai" / "hooks" / "cm-release-pre-squash.sh"
        _write_hook(hook, executable=False)

        result = _run_resolve(
            tmp_path, kanban_root, "noexec-ps",
            project_dir / "hooks", dev_tree,
            phase="pre-squash",
        )
        assert result.returncode == 1
        combined = result.stdout + result.stderr
        assert "chmod" in combined

    def test_nonexecutable_inrepo_hook_returns_error_for_pre_tag(
        self, tmp_path: pathlib.Path
    ) -> None:
        """A non-executable pre-tag in-repo hook triggers a loud error (rc=1)."""
        kanban_root, project_dir, dev_tree = _make_sandbox(tmp_path, "noexec-pt")
        hook = dev_tree / ".pgai" / "hooks" / "cm-release-pre-tag.sh"
        _write_hook(hook, executable=False)

        result = _run_resolve(
            tmp_path, kanban_root, "noexec-pt",
            project_dir / "hooks", dev_tree,
            phase="pre-tag",
        )
        assert result.returncode == 1
        combined = result.stdout + result.stderr
        assert "chmod" in combined

    def test_nonexecutable_inrepo_hook_returns_error_for_post_tag(
        self, tmp_path: pathlib.Path
    ) -> None:
        """A non-executable post-tag in-repo hook triggers a loud error (rc=1)."""
        kanban_root, project_dir, dev_tree = _make_sandbox(tmp_path, "noexec-post")
        hook = dev_tree / ".pgai" / "hooks" / "cm-release-post-tag.sh"
        _write_hook(hook, executable=False)

        result = _run_resolve(
            tmp_path, kanban_root, "noexec-post",
            project_dir / "hooks", dev_tree,
            phase="post-tag",
        )
        assert result.returncode == 1
        combined = result.stdout + result.stderr
        assert "chmod" in combined
