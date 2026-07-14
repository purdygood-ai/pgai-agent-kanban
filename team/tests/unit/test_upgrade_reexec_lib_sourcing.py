"""
test_upgrade_reexec_lib_sourcing.py
====================================
Converted behavioral tests for upgrade.sh byte-different and byte-identical
installed-vs-dev-tree scenarios.

CONVERSION NOTE (v1.7.0)
-------------------------
This file previously tested that the earlier fix (``_PGAI_UPGRADE_ORIG_DIR``
export before re-exec) allowed the re-exec'd child to source argparse.sh from
the original script's directory, not from the temp self-copy's directory.

The temp-copy re-exec mechanism and that defect's fix are retired in v1.7.0.
Under the two-phase architecture:

- **byte-different path**: Phase 1 always execs into the dev-tree script.
  Whether the installed upgrade.sh and the dev-tree upgrade.sh are byte-
  identical or byte-different is irrelevant to whether the upgrade completes.
  The converted test confirms both byte-different and byte-identical installed
  copies produce a successful upgrade (exit 0, "Upgrade complete!").

- **byte-identical / guard no-op path**: Under the old mechanism, a byte-
  identical installed copy caused the re-exec guard to short-circuit (the
  ``cmp -s`` check).  Under the two-phase architecture, there is no such
  check — the exec handoff always fires.  The converted test confirms a byte-
  identical installed copy still produces a successful upgrade.

The full five-fixture behavioral suite and the no-orphan assertion are in
``test_upgrade_two_phase_behavioral.py``.

DESIGN (REAL-SCRIPT, LIVE-LAYOUT)
----------------------------------
Both tests run the real upgrade.sh with a live-layout sandbox, not a shim,
consistent with the B9 standard.

TEST NAMING CONVENTION (SOP.md)
--------------------------------
Test function names describe behavior, not bug IDs or task IDs.
"""

from __future__ import annotations

import os
import pathlib
import shutil
import subprocess
import textwrap

import pytest

# ---------------------------------------------------------------------------
# Paths to the real scripts (dev tree layout, relative to this test file)
# ---------------------------------------------------------------------------

_TEAM_SCRIPTS_DIR = pathlib.Path(__file__).parent.parent.parent / "scripts"
_REAL_UPGRADE_SH = _TEAM_SCRIPTS_DIR / "upgrade.sh"
_REAL_ARGPARSE_SH = _TEAM_SCRIPTS_DIR / "lib" / "argparse.sh"
_REAL_ENV_BOOTSTRAP_SH = _TEAM_SCRIPTS_DIR / "lib" / "env_bootstrap.sh"
_REAL_INI_PARSER_SH = _TEAM_SCRIPTS_DIR / "lib" / "ini_parser.sh"
_REAL_PROJECT_PATHS_SH = _TEAM_SCRIPTS_DIR / "lib" / "project_paths.sh"
_REAL_TEMP_SH = _TEAM_SCRIPTS_DIR / "lib" / "temp.sh"
_REAL_VERSION_STAMP_SH = _TEAM_SCRIPTS_DIR / "lib" / "version_stamp.sh"

_SANDBOX_VERSION = "v1.7.0-sandbox-converted-reexec"


# ---------------------------------------------------------------------------
# Sandbox builder
# ---------------------------------------------------------------------------


def _build_upgrade_sandbox(
    tmp_path: pathlib.Path,
    *,
    byte_different: bool,
) -> dict[str, pathlib.Path]:
    """Build a minimal live-layout sandbox for upgrade.sh behavioral tests.

    Parameters
    ----------
    tmp_path:
        pytest tmp_path root (already inside PGAI_AGENT_KANBAN_TEMP_DIR/tests).
    byte_different:
        When True, the installed upgrade.sh has a no-op comment appended so
        its bytes differ from the dev-tree copy.  When False, both copies are
        byte-identical.

    Under the two-phase architecture, whether the installed copy is byte-
    different or byte-identical does not affect the exec handoff — phase 1
    always execs into the dev-tree script.

    Returns
    -------
    dict with keys:
        "kanban_root"   — Path to the simulated installed kanban root
        "dev_tree"      — Path to the simulated dev tree
        "pgai_temp_dir" — Path to the temp dir for this sandbox run
    """
    kanban_root = tmp_path / "kanban_root"
    dev_tree = tmp_path / "dev_tree"
    pgai_temp = tmp_path / "pgai_temp"

    kanban_root.mkdir()
    dev_tree.mkdir()
    pgai_temp.mkdir()

    # --- Kanban root: scripts/ and lib/ ---
    installed_scripts = kanban_root / "scripts"
    installed_lib = installed_scripts / "lib"
    installed_lib.mkdir(parents=True)

    for lib_src in [
        _REAL_ARGPARSE_SH, _REAL_ENV_BOOTSTRAP_SH, _REAL_INI_PARSER_SH,
        _REAL_PROJECT_PATHS_SH, _REAL_TEMP_SH,
        _REAL_VERSION_STAMP_SH,
    ]:
        if lib_src.exists():
            shutil.copy2(lib_src, installed_lib / lib_src.name)

    installed_upgrade = installed_scripts / "upgrade.sh"
    shutil.copy2(_REAL_UPGRADE_SH, installed_upgrade)
    installed_upgrade.chmod(0o755)

    if byte_different:
        # Append a no-op comment so the installed copy differs byte-for-byte
        # from the dev-tree copy.  Under the two-phase architecture this has
        # no effect on the exec handoff — phase 1 always execs into the
        # dev-tree script regardless of byte similarity.
        with installed_upgrade.open("a", encoding="utf-8") as f:
            f.write("\n# sandbox: byte-different marker (test-only comment)\n")

    # --- Kanban root: kanban.cfg with dev_tree_path ---
    (kanban_root / "kanban.cfg").write_text(
        textwrap.dedent(
            f"""\
            [paths]
            dev_tree_path = {dev_tree}
            """
        ),
        encoding="utf-8",
    )

    # --- Kanban root: VERSION file ---
    (kanban_root / "VERSION").write_text("v0.0.0-sandbox\n", encoding="utf-8")

    # --- Dev tree: install.sh stub ---
    dev_install_sh = dev_tree / "install.sh"
    dev_install_sh.write_text(
        textwrap.dedent(
            """\
            #!/usr/bin/env bash
            # Stub install.sh for sandbox tests — no side effects.
            exit 0
            """
        ),
        encoding="utf-8",
    )
    dev_install_sh.chmod(0o755)

    # --- Dev tree: team/scripts/upgrade.sh (the dev-tree copy) ---
    dev_team_scripts = dev_tree / "team" / "scripts"
    dev_team_lib = dev_team_scripts / "lib"
    dev_team_lib.mkdir(parents=True)

    dev_upgrade = dev_team_scripts / "upgrade.sh"
    shutil.copy2(_REAL_UPGRADE_SH, dev_upgrade)
    dev_upgrade.chmod(0o755)

    for lib_src in [
        _REAL_ARGPARSE_SH, _REAL_ENV_BOOTSTRAP_SH, _REAL_INI_PARSER_SH,
        _REAL_PROJECT_PATHS_SH, _REAL_TEMP_SH,
        _REAL_VERSION_STAMP_SH,
    ]:
        if lib_src.exists():
            shutil.copy2(lib_src, dev_team_lib / lib_src.name)

    # --- Dev tree: subagents/MANIFEST.txt stub ---
    (dev_tree / "subagents").mkdir()
    (dev_tree / "subagents" / "MANIFEST.txt").write_text(
        "# Subagent manifest — empty in sandbox\n",
        encoding="utf-8",
    )

    # --- Dev tree: team items the deposit loop iterates over ---
    for item_name in ["README.md", "DIRECTIVES.md", "OVERVIEW.md", "SOP.md"]:
        (dev_tree / "team" / item_name).write_text(f"# {item_name} stub\n", encoding="utf-8")

    for sub in ["roles", "pm-agent", "workflows", "pgai_agent_kanban",
                "halt_after", "templates", "demos"]:
        (dev_tree / "team" / sub).mkdir(exist_ok=True)
        (dev_tree / "team" / sub / ".gitkeep").write_text("", encoding="utf-8")

    return {
        "kanban_root": kanban_root,
        "dev_tree": dev_tree,
        "pgai_temp_dir": pgai_temp,
    }


def _run_upgrade(
    sandbox: dict[str, pathlib.Path],
    tmp_path: pathlib.Path,
    extra_env: dict[str, str] | None = None,
) -> subprocess.CompletedProcess:
    """Run the installed upgrade.sh from the sandbox kanban root."""
    kanban_root = sandbox["kanban_root"]
    dev_tree = sandbox["dev_tree"]
    pgai_temp = sandbox["pgai_temp_dir"]

    upgrade_sh = kanban_root / "scripts" / "upgrade.sh"

    env = dict(os.environ)
    # Remove outer PGAI_DEV_TREE_PATH so the --dev-tree argument wins.
    env.pop("PGAI_DEV_TREE_PATH", None)
    env.update(
        {
            "PGAI_AGENT_KANBAN_ROOT_PATH": str(kanban_root),
            "PGAI_AGENT_KANBAN_TEMP_DIR": str(pgai_temp),
            "HOME": str(tmp_path / "fake_home"),
        }
    )
    if extra_env:
        env.update(extra_env)

    (tmp_path / "fake_home").mkdir(exist_ok=True)

    result = subprocess.run(
        [
            "bash",
            str(upgrade_sh),
            "--force",
            "--no-backup",
            "--dev-tree",
            str(dev_tree),
            "--stamp-version",
            _SANDBOX_VERSION,
        ],
        capture_output=True,
        text=True,
        timeout=60,
        env=env,
    )
    return result


def _assert_no_leaked_self_copies(pgai_temp: pathlib.Path) -> None:
    """Assert no upgrade_self.* temp files remain under pgai_temp.

    Under the two-phase architecture, no temp self-copy is created at all.
    This assertion verifies the absence holds.
    """
    leaked = list(pgai_temp.glob("upgrade_self.*"))
    assert not leaked, (
        f"Unexpected upgrade_self.* file(s) found under {pgai_temp}.  "
        f"The two-phase architecture must not create temp self-copy files.  "
        f"Found {len(leaked)} file(s):\n"
        + "\n".join(f"  {f}" for f in leaked)
    )


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


@pytest.mark.skipif(
    not _REAL_UPGRADE_SH.exists(),
    reason=f"Real upgrade.sh not found at {_REAL_UPGRADE_SH}; skipping behavioral test.",
)
def test_upgrade_completes_when_installed_copy_is_byte_different(
    tmp_path: pathlib.Path,
) -> None:
    """upgrade.sh completes when the installed copy differs byte-for-byte from the dev tree.

    Under the old mechanism, byte difference triggered the re-exec guard (an earlier defect
    regression path).  Under the two-phase architecture, phase 1 ALWAYS execs
    into the dev-tree script — byte similarity between the installed and dev-tree
    copies is irrelevant to the exec handoff.

    The upgrade must complete: exit 0, "Upgrade complete!" in stdout.  No
    upgrade_self.* temp files must remain.
    """
    sandbox = _build_upgrade_sandbox(tmp_path, byte_different=True)
    result = _run_upgrade(sandbox, tmp_path)

    assert result.returncode == 0, (
        f"upgrade.sh (byte-different path) failed with exit code {result.returncode}.\n"
        f"stdout:\n{result.stdout}\n\nstderr:\n{result.stderr}"
    )
    assert "Upgrade complete!" in result.stdout, (
        f"upgrade.sh exited 0 but 'Upgrade complete!' not found in stdout.\n"
        f"stdout:\n{result.stdout}\n\nstderr:\n{result.stderr}"
    )

    _assert_no_leaked_self_copies(sandbox["pgai_temp_dir"])


@pytest.mark.skipif(
    not _REAL_UPGRADE_SH.exists(),
    reason=f"Real upgrade.sh not found at {_REAL_UPGRADE_SH}; skipping behavioral test.",
)
def test_upgrade_completes_when_installed_copy_is_byte_identical(
    tmp_path: pathlib.Path,
) -> None:
    """upgrade.sh completes when the installed copy is byte-identical to the dev-tree copy.

    Under the old mechanism, byte identity caused the re-exec guard's cmp -s
    check to short-circuit (the guard no-op path).  Under the two-phase
    architecture there is no such check — the exec handoff always fires.

    The upgrade must complete: exit 0, "Upgrade complete!" in stdout.  No
    upgrade_self.* temp files must remain.
    """
    sandbox = _build_upgrade_sandbox(tmp_path, byte_different=False)
    result = _run_upgrade(sandbox, tmp_path)

    assert result.returncode == 0, (
        f"upgrade.sh (byte-identical path) failed with exit code "
        f"{result.returncode}.\n\n"
        f"stdout:\n{result.stdout}\n\nstderr:\n{result.stderr}"
    )
    assert "Upgrade complete!" in result.stdout, (
        f"upgrade.sh exited 0 but 'Upgrade complete!' not found in stdout.\n"
        f"stdout:\n{result.stdout}\n\nstderr:\n{result.stderr}"
    )

    _assert_no_leaked_self_copies(sandbox["pgai_temp_dir"])
