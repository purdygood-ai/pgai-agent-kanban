"""
test_ship_rc_hooks.py
=====================
Structural and sibling-parity tests for the ship-rc verb's hook integration.

ship-rc's logic is pgai_agent_kanban.cm.ship.ship_rc (ship-rc.sh is a thin
wrapper over it), which resolves each release-hook phase by running the shared
library function cm_resolve_and_enforce_hook -- the one release.sh calls.

Covers:
  (A) Structural: the resolution/printing body appears exactly once under
      team/scripts/lib/ and zero times inline in ship-rc.sh, cm/ship.py or
      release.sh. (One-implementation rule enforcement.)

  (B) Sibling-parity: ship_rc resolves all three phases (pre-squash, pre-tag,
      post-tag) through cm_resolve_and_enforce_hook and prints exactly what
      the library prints on identical sandbox fixtures -- the same lines,
      the same resolved path, the same enforcement -- as release.sh does.

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
_SHIP_PY = pathlib.Path("pgai_agent_kanban") / "cm" / "ship.py"
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
    zero inline copies in ship-rc.sh, cm/ship.py or release.sh.

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

    @pytest.mark.parametrize("path", [_SHIP_RC, _SHIP_PY], ids=["ship-rc.sh", "cm-ship.py"])
    def test_resolution_body_absent_from_ship_rc(self, path: pathlib.Path) -> None:
        """Neither ship-rc.sh nor cm/ship.py inlines the resolution/printing body."""
        assert path.is_file(), f"not found: {path}"
        text = path.read_text(encoding="utf-8", errors="replace")
        count = text.count(self._CANARY)
        assert count == 0, (
            f"Canary '{self._CANARY}' found {count} time(s) inline in {path}. "
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

    def test_ship_rc_runs_every_phase_through_the_shared_resolver(self) -> None:
        """cm/ship.py resolves through cm_resolve_and_enforce_hook, once per phase.

        Its one resolver call site (the bash it runs) names the library
        function, and ship_rc runs a hook phase for each of pre-squash,
        pre-tag and post-tag exactly once. ship-rc.sh itself resolves
        nothing.
        """
        from pgai_agent_kanban.cm import ship

        assert "cm_resolve_and_enforce_hook" in ship._HOOK_RESOLVER
        assert ship._HOOK_RESOLVER.count("cm_resolve_and_enforce_hook") == 1
        text = _SHIP_PY.read_text(encoding="utf-8")
        for phase in ("pre-squash", "pre-tag", "post-tag"):
            assert text.count(f'_hook_phase(release, "{phase}"') == 1, phase
        assert self._count_actual_calls(_SHIP_RC) == 0

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

    @pytest.mark.parametrize("phase", ["pre-squash", "pre-tag", "post-tag"])
    def test_prints_none_configured_when_no_hook(
        self, tmp_path: pathlib.Path, phase: str
    ) -> None:
        """Both scripts print '<phase> hook: none configured' when no hook exists.

        Survives-as-named-parameterization of the original three per-phase
        tests; no case dropped."""
        kanban_root, project_dir, dev_tree = _make_sandbox(tmp_path, f"parity-{phase}")

        result = _run_resolve(
            tmp_path, kanban_root, f"parity-{phase}",
            project_dir / "hooks", dev_tree,
            phase=phase,
        )
        assert result.returncode == 0
        assert f"{phase} hook: none configured" in result.stdout

    @staticmethod
    def _plant_kanban_side(dev_tree: pathlib.Path, project_dir: pathlib.Path, phase: str) -> None:
        _write_hook(project_dir / "hooks" / f"cm-release-{phase}.sh")

    @staticmethod
    def _plant_inrepo(dev_tree: pathlib.Path, project_dir: pathlib.Path, phase: str) -> None:
        _write_hook(dev_tree / ".pgai" / "hooks" / f"cm-release-{phase}.sh")

    @staticmethod
    def _plant_cfg(dev_tree: pathlib.Path, project_dir: pathlib.Path, phase: str) -> None:
        cfg_hook = dev_tree / f"{phase}-hook.sh"
        _write_hook(cfg_hook)
        cfg_text = (
            "[project]\n"
            f"project_name = parity-cfg-{phase}\n"
            f"dev_tree_path = {dev_tree}\n"
            "[hooks]\n"
            f"cm_release_{phase.replace('-', '_')}_hook = {phase}-hook.sh\n"
        )
        (project_dir / "project.cfg").write_text(cfg_text, encoding="utf-8")

    @pytest.mark.parametrize(
        "phase, plant_hook, expected_source",
        [
            pytest.param("pre-squash", _plant_kanban_side, "kanban-side", id="kanban_side_pre_squash"),
            pytest.param("pre-tag", _plant_inrepo, "in-repo", id="inrepo_pre_tag"),
            pytest.param("post-tag", _plant_cfg, "cfg", id="cfg_post_tag"),
        ],
    )
    def test_prints_path_and_source_when_hook_present(
        self, tmp_path: pathlib.Path, phase: str, plant_hook, expected_source: str
    ) -> None:
        """Both scripts print '<phase> hook: <path> (source: <tier>)' for each
        resolution tier. Survives-as-named-parameterization of the original
        three per-tier tests; no case dropped."""
        project_name = f"parity-{expected_source}-{phase}"
        kanban_root, project_dir, dev_tree = _make_sandbox(tmp_path, project_name)
        plant_hook(dev_tree, project_dir, phase)

        result = _run_resolve(
            tmp_path, kanban_root, project_name,
            project_dir / "hooks", dev_tree,
            phase=phase,
        )
        assert result.returncode == 0
        assert f"{phase} hook:" in result.stdout
        assert f"source: {expected_source}" in result.stdout


class TestSiblingParityEnforcement:
    """
    Required-hook enforcement (HALT behavior) is identical for both scripts
    because both call the same cm_resolve_and_enforce_hook implementation.
    These tests verify the shared enforcement semantics at each phase.
    """

    @pytest.mark.parametrize("phase", ["pre-squash", "pre-tag", "post-tag"])
    def test_required_halts_when_no_hook_and_names_the_phase(
        self, tmp_path: pathlib.Path, phase: str
    ) -> None:
        """Both scripts HALT before the phase when required=true and no hook,
        and the HALT message names the phase.

        Survives-as-named-parameterization of the original three
        'halts_when_no_hook' tests plus 'test_halt_message_names_phase_pre_squash'
        (folded in as an extra assertion applied to every phase, not just
        pre-squash); no case dropped."""
        project_name = f"enf-{phase}"
        kanban_root, project_dir, dev_tree = _make_sandbox(tmp_path, project_name)
        key = f"cm_release_{phase.replace('-', '_')}_hook_required"
        cfg_text = (
            "[project]\n"
            f"project_name = {project_name}\n"
            f"dev_tree_path = {dev_tree}\n"
            "[hooks]\n"
            f"{key} = true\n"
        )
        (project_dir / "project.cfg").write_text(cfg_text, encoding="utf-8")

        result = _run_resolve(
            tmp_path, kanban_root, project_name,
            project_dir / "hooks", dev_tree,
            phase=phase,
        )
        # cm_halt stub exits 2
        assert result.returncode == 2, (
            f"Expected HALT (rc=2) for required {phase} hook, got {result.returncode}; "
            f"stdout: {result.stdout}; stderr: {result.stderr}"
        )
        combined = result.stdout + result.stderr
        assert phase in combined

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

    @pytest.mark.parametrize("phase", ["pre-squash", "pre-tag", "post-tag"])
    def test_nonexecutable_inrepo_hook_returns_error(
        self, tmp_path: pathlib.Path, phase: str
    ) -> None:
        """A non-executable in-repo hook triggers a loud error (rc=1) at every
        phase. Survives-as-named-parameterization of the original three
        per-phase tests; no case dropped."""
        project_name = f"noexec-{phase}"
        kanban_root, project_dir, dev_tree = _make_sandbox(tmp_path, project_name)
        hook = dev_tree / ".pgai" / "hooks" / f"cm-release-{phase}.sh"
        _write_hook(hook, executable=False)

        result = _run_resolve(
            tmp_path, kanban_root, project_name,
            project_dir / "hooks", dev_tree,
            phase=phase,
        )
        assert result.returncode == 1
        combined = result.stdout + result.stderr
        assert "chmod" in combined


class TestShipRcResolvesLikeTheLibrary:
    """ship_rc's per-phase resolution prints exactly what the library prints.

    For each tier and for the not-executable and required-but-absent stops,
    ``cm.ship._resolve_hook`` is run on the same sandbox as a direct
    ``cm_resolve_and_enforce_hook`` call: the resolution line, the stderr
    remedy block and the resolved path are identical, and the library's
    HALT reaches ship_rc as its own HALT with the library's key.
    """

    @staticmethod
    def _release(kanban_root: pathlib.Path, project: str, dev_tree: pathlib.Path):
        from pgai_agent_kanban.cm import ship
        from pgai_agent_kanban.ops.context import OpsContext

        ctx = OpsContext(kanban_root=kanban_root)
        return ship._Release(ctx, project, "v1.0.0", str(dev_tree), "rc/v1.0.0", ctx.project_root(project))

    @staticmethod
    def _ship_resolve(release, phase: str):
        from pgai_agent_kanban.cm import ship

        out: list[str] = []
        err: list[str] = []
        path, executable = ship._resolve_hook(release, phase, out.append, err.append)
        return "".join(out), "".join(err), path, executable

    @pytest.mark.parametrize(
        "phase, tier",
        [
            ("pre-squash", None),
            ("pre-squash", "kanban-side"),
            ("pre-tag", "in-repo"),
            ("post-tag", "in-repo-not-executable"),
            ("pre-tag", "cfg-absolute"),
        ],
    )
    def test_resolution_matches_the_library(self, tmp_path: pathlib.Path, phase: str, tier) -> None:
        project = "parity-ship"
        kanban_root, project_dir, dev_tree = _make_sandbox(tmp_path, project)
        hook = None
        if tier == "kanban-side":
            hook = project_dir / "hooks" / f"cm-release-{phase}.sh"
            _write_hook(hook)
        elif tier in ("in-repo", "in-repo-not-executable"):
            hook = dev_tree / ".pgai" / "hooks" / f"cm-release-{phase}.sh"
            _write_hook(hook, executable=(tier == "in-repo"))
        elif tier == "cfg-absolute":
            hook = tmp_path / "ops" / f"{phase}.sh"
            _write_hook(hook)
            with (project_dir / "project.cfg").open("a", encoding="utf-8") as fh:
                fh.write(f"[hooks]\ncm_release_{phase.replace('-', '_')}_hook = {hook}\n")

        library = _run_resolve(tmp_path, kanban_root, project, project_dir / "hooks", dev_tree, phase=phase)
        out, err, path, executable = self._ship_resolve(self._release(kanban_root, project, dev_tree), phase)

        library_lines = [line for line in library.stdout.splitlines() if not line.startswith("RESOLVED_PATH:")]
        assert out.splitlines() == library_lines
        assert err == library.stderr
        assert executable is (library.returncode == 0)
        resolved = library.stdout.rsplit("RESOLVED_PATH:", 1)[1].strip()
        assert (path or "") == resolved
        if tier in (None, "in-repo-not-executable"):
            assert path is None
        else:
            assert path == str(hook)

    def test_required_absent_hook_is_a_ship_rc_halt(self, tmp_path: pathlib.Path) -> None:
        from pgai_agent_kanban.cm import ship

        project = "parity-ship-required"
        kanban_root, project_dir, dev_tree = _make_sandbox(tmp_path, project)
        with (project_dir / "project.cfg").open("a", encoding="utf-8") as fh:
            fh.write("[hooks]\ncm_release_pre_tag_hook_required = true\n")

        library = _run_resolve(tmp_path, kanban_root, project, project_dir / "hooks", dev_tree, phase="pre-tag")
        assert library.returncode == 2 and "trigger=cm-release.hook-required-missing.pre-tag" in library.stdout

        with pytest.raises(ship.ShipRcError) as caught:
            self._ship_resolve(self._release(kanban_root, project, dev_tree), "pre-tag")
        assert caught.value.error == "cm-release.hook-required-missing.pre-tag"
        assert caught.value.exit_code == 1
        text = caught.value.text
        assert text.startswith("\n[ship-rc] HALT\nerror:   cm-release.hook-required-missing.pre-tag\n")
        assert "cm_release_pre_tag_hook_required=true but no hook was found at any tier" in text
        assert text.endswith("\n\nHALT: ship-rc.sh stopped. The release has NOT been shipped.\n")
        ledger = (project_dir / "refusal-ledger.md").read_text(encoding="utf-8")
        assert "- Error: cm-release.hook-required-missing.pre-tag" in ledger
