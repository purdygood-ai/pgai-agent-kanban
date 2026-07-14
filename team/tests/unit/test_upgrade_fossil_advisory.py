"""
test_upgrade_fossil_advisory.py
================================
Behavioral unit tests for the fossil-retirement section of upgrade.sh.

WHAT IS TESTED
--------------
After v1.3.0 moved top-level pipeline yamls inside each workflow plugin
directory (``workflows/<type>/pipeline.yaml``), live installs that are
upgraded may still carry the old ``workflows/release.yaml`` and
``workflows/document.yaml`` files.  Since v1.6.2 upgrade.sh MOVES those
fossil files to ``$KANBAN_ROOT/retired/<UTC-ts>/<relative-path>`` using the
retirement manifest at ``$KANBAN_ROOT/templates/retired-files.txt``, rather
than emitting a passive advisory.

These tests verify:

1. When both fossil files are present, the retirement step moves both and
   emits one loud line per file mentioning the graveyard destination.
2. When only one fossil is present, only that file is retired.
3. When neither fossil is present, no file is moved and the summary says "none".
4. A malformed manifest (glob character) causes upgrade.sh to abort before
   any file is moved.
5. Operator-authored files (not listed in the manifest) are never touched.

SANDBOX DESIGN
--------------
Each test builds a minimal live-install sandbox (the same pattern used by
``test_upgrade_reexec_lib_sourcing.py``) and runs the real upgrade.sh with
``--force --no-backup --stamp-version``.  The sandbox kanban root lives
under the pytest temp tree, which causes upgrade.sh's temp-root guard to
skip crontab and pseudocron hooks automatically.

The sandbox places fossil files (and optionally custom files) in the kanban
root's ``workflows/`` directory and supplies a real ``templates/retired-files.txt``
manifest and ``scripts/lib/retired_files_lint.sh`` so the retirement step runs.

TEST NAMING CONVENTION
----------------------
Function names describe the behavior under test, not the scaffolding that
supports it.
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
_REAL_RETIRED_LINT_SH = _TEAM_SCRIPTS_DIR / "lib" / "retired_files_lint.sh"
_REAL_VERSION_STAMP_SH = _TEAM_SCRIPTS_DIR / "lib" / "version_stamp.sh"

_TEAM_TEMPLATES_DIR = pathlib.Path(__file__).parent.parent.parent / "templates"
_REAL_RETIRED_FILES_TXT = _TEAM_TEMPLATES_DIR / "retired-files.txt"

# Text that appears in the per-file retirement line.
# Per-file lines contain both "Retired:" and " -> " (the move arrow).
# The summary line contains "Retired:" but does NOT contain " -> ".
_RETIRE_PER_FILE_MARKER = " -> "
_RETIRE_FRAGMENT = "Retired:"
# Sub-string that confirms the file was moved (graveyard path prefix).
_GRAVEYARD_FRAGMENT = "retired/"


# ---------------------------------------------------------------------------
# Sandbox builder
# ---------------------------------------------------------------------------


def _build_fossil_sandbox(
    tmp_path: pathlib.Path,
    *,
    fossils: list[str],
    custom_files: list[str] | None = None,
    malformed_manifest: bool = False,
) -> dict[str, pathlib.Path]:
    """Build a minimal live-install sandbox with optional fossil yaml files.

    Creates a fake kanban root under ``tmp_path`` (recognized by upgrade.sh's
    pre-flight check) and optionally places fossil pipeline yaml files at
    ``<kanban_root>/workflows/<type>.yaml`` for each name in *fossils*.
    Also supplies the real retirement manifest and lint helper so the
    retirement step runs in each test.

    Parameters
    ----------
    tmp_path:
        pytest tmp_path root.
    fossils:
        List of workflow type names whose fossil yamls should be present in the
        live install's ``workflows/`` directory.  Pass ``["release"]``,
        ``["document"]``, ``["release", "document"]``, or ``[]`` for no fossils.
    custom_files:
        Optional list of paths relative to the kanban root to create as
        operator-authored files (must NOT be listed in the manifest).
    malformed_manifest:
        When True, write a manifest containing a glob character so the lint
        pre-check fires.

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

    # --- Kanban root: scripts/ with real libs ---
    installed_scripts = kanban_root / "scripts"
    installed_lib = installed_scripts / "lib"
    installed_lib.mkdir(parents=True)

    for lib_src in [
        _REAL_ARGPARSE_SH,
        _REAL_ENV_BOOTSTRAP_SH,
        _REAL_INI_PARSER_SH,
        _REAL_PROJECT_PATHS_SH,
        _REAL_TEMP_SH,
        _REAL_RETIRED_LINT_SH,
        _REAL_VERSION_STAMP_SH,
    ]:
        if lib_src.exists():
            shutil.copy2(lib_src, installed_lib / lib_src.name)

    # Copy real upgrade.sh into the installed tree.
    installed_upgrade = installed_scripts / "upgrade.sh"
    shutil.copy2(_REAL_UPGRADE_SH, installed_upgrade)
    installed_upgrade.chmod(0o755)

    # --- Kanban root: templates/ with retirement manifest ---
    installed_templates = kanban_root / "templates"
    installed_templates.mkdir()
    if malformed_manifest:
        # Inject a glob character to trigger the lint pre-check.
        (installed_templates / "retired-files.txt").write_text(
            "# malformed test manifest\nworkflows/*.yaml\n",
            encoding="utf-8",
        )
    elif _REAL_RETIRED_FILES_TXT.exists():
        shutil.copy2(_REAL_RETIRED_FILES_TXT, installed_templates / "retired-files.txt")
    else:
        # Fallback: write the known manifest entries inline.
        (installed_templates / "retired-files.txt").write_text(
            "# retired in v1.3.0 (pipeline-in-plugin)\n"
            "workflows/release.yaml\n"
            "workflows/document.yaml\n",
            encoding="utf-8",
        )

    # --- Kanban root: kanban.cfg pointing at dev tree ---
    (kanban_root / "kanban.cfg").write_text(
        textwrap.dedent(
            f"""\
            [paths]
            dev_tree_path = {dev_tree}
            """
        ),
        encoding="utf-8",
    )

    # --- Kanban root: VERSION (pre-flight recognizes an existing install) ---
    (kanban_root / "VERSION").write_text("v1.2.0-sandbox\n", encoding="utf-8")

    # --- Kanban root: workflows/ dir, with optional fossil yamls ---
    workflows_dir = kanban_root / "workflows"
    workflows_dir.mkdir()
    for fossil_type in fossils:
        fossil_path = workflows_dir / f"{fossil_type}.yaml"
        fossil_path.write_text(
            textwrap.dedent(
                f"""\
                # Fossil pipeline file for {fossil_type} (pre-v1.3.0 layout).
                # This file should not be loaded after the v1.3.0 upgrade.
                type: {fossil_type}
                """
            ),
            encoding="utf-8",
        )

    # --- Kanban root: optional operator-authored files ---
    for custom_rel in (custom_files or []):
        custom_path = kanban_root / custom_rel
        custom_path.parent.mkdir(parents=True, exist_ok=True)
        custom_path.write_text(f"# operator-authored: {custom_rel}\n", encoding="utf-8")

    # --- Dev tree: install.sh stub ---
    dev_install_sh = dev_tree / "install.sh"
    dev_install_sh.write_text(
        textwrap.dedent(
            """\
            #!/usr/bin/env bash
            # Stub install.sh for sandbox tests.
            exit 0
            """
        ),
        encoding="utf-8",
    )
    dev_install_sh.chmod(0o755)

    # --- Dev tree: team/scripts/upgrade.sh (the deposited copy) ---
    dev_team_scripts = dev_tree / "team" / "scripts"
    dev_team_lib = dev_team_scripts / "lib"
    dev_team_lib.mkdir(parents=True)

    dev_upgrade = dev_team_scripts / "upgrade.sh"
    shutil.copy2(_REAL_UPGRADE_SH, dev_upgrade)
    dev_upgrade.chmod(0o755)

    for lib_src in [
        _REAL_ARGPARSE_SH,
        _REAL_ENV_BOOTSTRAP_SH,
        _REAL_INI_PARSER_SH,
        _REAL_PROJECT_PATHS_SH,
        _REAL_TEMP_SH,
        _REAL_RETIRED_LINT_SH,
        _REAL_VERSION_STAMP_SH,
    ]:
        if lib_src.exists():
            shutil.copy2(lib_src, dev_team_lib / lib_src.name)

    # --- Dev tree: subagents/MANIFEST.txt stub ---
    (dev_tree / "subagents").mkdir()
    (dev_tree / "subagents" / "MANIFEST.txt").write_text(
        "# Empty manifest\n",
        encoding="utf-8",
    )

    # --- Dev tree: team content stubs for the deposit loop ---
    for item_name in ["README.md", "DIRECTIVES.md", "OVERVIEW.md", "SOP.md"]:
        (dev_tree / "team" / item_name).write_text(
            f"# {item_name} stub\n", encoding="utf-8"
        )

    for sub in [
        "roles",
        "pm-agent",
        "workflows",
        "pgai_agent_kanban",
        "halt_after",
        "demos",
    ]:
        (dev_tree / "team" / sub).mkdir(exist_ok=True)
        (dev_tree / "team" / sub / ".gitkeep").write_text("", encoding="utf-8")

    # Dev tree templates/ must have the retirement manifest too (deposited in real upgrade).
    dev_templates = dev_tree / "team" / "templates"
    dev_templates.mkdir(exist_ok=True)
    if malformed_manifest:
        (dev_templates / "retired-files.txt").write_text(
            "# malformed test manifest\nworkflows/*.yaml\n",
            encoding="utf-8",
        )
    elif _REAL_RETIRED_FILES_TXT.exists():
        shutil.copy2(_REAL_RETIRED_FILES_TXT, dev_templates / "retired-files.txt")
    else:
        (dev_templates / "retired-files.txt").write_text(
            "# retired in v1.3.0 (pipeline-in-plugin)\n"
            "workflows/release.yaml\n"
            "workflows/document.yaml\n",
            encoding="utf-8",
        )

    return {
        "kanban_root": kanban_root,
        "dev_tree": dev_tree,
        "pgai_temp_dir": pgai_temp,
    }


def _run_upgrade(
    sandbox: dict[str, pathlib.Path],
    tmp_path: pathlib.Path,
) -> subprocess.CompletedProcess:
    """Run upgrade.sh from the sandbox kanban root and return the result.

    Invokes upgrade.sh with ``--force --no-backup --stamp-version sandbox-test``
    so the test does not require a real backup, a TTY, or a git history.
    """
    kanban_root = sandbox["kanban_root"]
    dev_tree = sandbox["dev_tree"]
    pgai_temp = sandbox["pgai_temp_dir"]

    upgrade_sh = kanban_root / "scripts" / "upgrade.sh"

    env = dict(os.environ)
    env.update(
        {
            "PGAI_AGENT_KANBAN_ROOT_PATH": str(kanban_root),
            "PGAI_AGENT_KANBAN_TEMP_DIR": str(pgai_temp),
            # Isolate HOME so crontab/pseudocron touches nothing real.
            "HOME": str(tmp_path / "fake_home"),
        }
    )
    (tmp_path / "fake_home").mkdir(exist_ok=True)

    return subprocess.run(
        [
            "bash",
            str(upgrade_sh),
            "--force",
            "--no-backup",
            "--dev-tree",
            str(dev_tree),
            "--stamp-version",
            "sandbox-test",
        ],
        capture_output=True,
        text=True,
        timeout=60,
        env=env,
    )


def _find_graveyard_dir(kanban_root: pathlib.Path) -> pathlib.Path | None:
    """Return the single timestamped subdirectory under retired/, or None."""
    retired_root = kanban_root / "retired"
    if not retired_root.exists():
        return None
    children = [p for p in retired_root.iterdir() if p.is_dir()]
    if len(children) == 1:
        return children[0]
    return None


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


@pytest.mark.skipif(
    not _REAL_UPGRADE_SH.exists(),
    reason=f"Real upgrade.sh not found at {_REAL_UPGRADE_SH}; skipping.",
)
def test_retirement_moves_both_fossils_when_both_are_present(
    tmp_path: pathlib.Path,
) -> None:
    """upgrade.sh moves both fossil yamls to the graveyard when both are present.

    When both ``workflows/release.yaml`` and ``workflows/document.yaml`` exist
    in the live kanban root, the retirement step moves both files to
    ``retired/<UTC-ts>/workflows/`` and emits one loud per-file line.
    Neither file remains at the live root after the upgrade.
    """
    sandbox = _build_fossil_sandbox(
        tmp_path, fossils=["release", "document"]
    )
    result = _run_upgrade(sandbox, tmp_path)

    assert result.returncode == 0, (
        f"upgrade.sh failed (exit {result.returncode}).\n"
        f"stdout:\n{result.stdout}\n\nstderr:\n{result.stderr}"
    )

    kanban_root = sandbox["kanban_root"]
    combined_output = result.stdout + result.stderr

    # Both per-file retirement lines must appear.
    retire_lines = [
        line for line in combined_output.splitlines()
        if _RETIRE_FRAGMENT in line and _GRAVEYARD_FRAGMENT in line and _RETIRE_PER_FILE_MARKER in line and "[upgrade]" in line
    ]
    assert len(retire_lines) == 2, (
        f"Expected 2 retirement lines, found {len(retire_lines)}.\n"
        f"Lines found:\n"
        + ("\n".join(retire_lines) or "  (none)")
        + f"\n\nFull stdout:\n{result.stdout}\n\nstderr:\n{result.stderr}"
    )
    retire_text = "\n".join(retire_lines)
    assert "release.yaml" in retire_text
    assert "document.yaml" in retire_text

    # Both fossil files must now be in the graveyard, not the live root.
    assert not (kanban_root / "workflows" / "release.yaml").exists(), (
        "workflows/release.yaml was not moved to the graveyard."
    )
    assert not (kanban_root / "workflows" / "document.yaml").exists(), (
        "workflows/document.yaml was not moved to the graveyard."
    )

    graveyard = _find_graveyard_dir(kanban_root)
    assert graveyard is not None, "No retired/<ts>/ directory was created."
    assert (graveyard / "workflows" / "release.yaml").exists(), (
        f"workflows/release.yaml not found in graveyard {graveyard}."
    )
    assert (graveyard / "workflows" / "document.yaml").exists(), (
        f"workflows/document.yaml not found in graveyard {graveyard}."
    )

    # Summary line must mention the count.
    assert "Retired:" in combined_output, (
        "Summary 'Retired:' line not found in upgrade output."
    )


@pytest.mark.skipif(
    not _REAL_UPGRADE_SH.exists(),
    reason=f"Real upgrade.sh not found at {_REAL_UPGRADE_SH}; skipping.",
)
def test_retirement_moves_release_fossil_only(
    tmp_path: pathlib.Path,
) -> None:
    """Retirement step handles a single fossil correctly.

    When only ``workflows/release.yaml`` is present, only that file is moved
    to the graveyard.  The missing document.yaml is a silent no-op.
    """
    sandbox = _build_fossil_sandbox(tmp_path, fossils=["release"])
    result = _run_upgrade(sandbox, tmp_path)

    assert result.returncode == 0, (
        f"upgrade.sh failed (exit {result.returncode}).\n"
        f"stdout:\n{result.stdout}\n\nstderr:\n{result.stderr}"
    )

    kanban_root = sandbox["kanban_root"]
    combined_output = result.stdout + result.stderr

    retire_lines = [
        line for line in combined_output.splitlines()
        if _RETIRE_FRAGMENT in line and _GRAVEYARD_FRAGMENT in line and _RETIRE_PER_FILE_MARKER in line and "[upgrade]" in line
    ]
    assert len(retire_lines) == 1, (
        f"Expected 1 retirement line, found {len(retire_lines)}.\n"
        + ("\n".join(retire_lines) or "  (none)")
        + f"\n\nFull stdout:\n{result.stdout}"
    )
    assert "release.yaml" in retire_lines[0]

    assert not (kanban_root / "workflows" / "release.yaml").exists(), (
        "workflows/release.yaml was not moved to the graveyard."
    )

    graveyard = _find_graveyard_dir(kanban_root)
    assert graveyard is not None
    assert (graveyard / "workflows" / "release.yaml").exists()
    # document.yaml was never present; graveyard should not have it either.
    assert not (graveyard / "workflows" / "document.yaml").exists()


@pytest.mark.skipif(
    not _REAL_UPGRADE_SH.exists(),
    reason=f"Real upgrade.sh not found at {_REAL_UPGRADE_SH}; skipping.",
)
def test_retirement_moves_document_fossil_only(
    tmp_path: pathlib.Path,
) -> None:
    """Retirement step mirrors test_retirement_moves_release_fossil_only for document."""
    sandbox = _build_fossil_sandbox(tmp_path, fossils=["document"])
    result = _run_upgrade(sandbox, tmp_path)

    assert result.returncode == 0, (
        f"upgrade.sh failed (exit {result.returncode}).\n"
        f"stdout:\n{result.stdout}\n\nstderr:\n{result.stderr}"
    )

    kanban_root = sandbox["kanban_root"]
    combined_output = result.stdout + result.stderr

    retire_lines = [
        line for line in combined_output.splitlines()
        if _RETIRE_FRAGMENT in line and _GRAVEYARD_FRAGMENT in line and _RETIRE_PER_FILE_MARKER in line and "[upgrade]" in line
    ]
    assert len(retire_lines) == 1
    assert "document.yaml" in retire_lines[0]

    assert not (kanban_root / "workflows" / "document.yaml").exists()

    graveyard = _find_graveyard_dir(kanban_root)
    assert graveyard is not None
    assert (graveyard / "workflows" / "document.yaml").exists()


@pytest.mark.skipif(
    not _REAL_UPGRADE_SH.exists(),
    reason=f"Real upgrade.sh not found at {_REAL_UPGRADE_SH}; skipping.",
)
def test_retirement_is_silent_no_op_when_no_fossils_present(
    tmp_path: pathlib.Path,
) -> None:
    """No file is moved and summary reads 'none' when no fossils are present.

    A clean post-v1.3.0 install (or one where the operator already removed
    the fossils) must not create a graveyard directory.
    """
    sandbox = _build_fossil_sandbox(tmp_path, fossils=[])
    result = _run_upgrade(sandbox, tmp_path)

    assert result.returncode == 0, (
        f"upgrade.sh failed (exit {result.returncode}).\n"
        f"stdout:\n{result.stdout}\n\nstderr:\n{result.stderr}"
    )

    kanban_root = sandbox["kanban_root"]
    combined_output = result.stdout + result.stderr

    # No per-file retirement lines.
    retire_lines = [
        line for line in combined_output.splitlines()
        if _RETIRE_FRAGMENT in line and _GRAVEYARD_FRAGMENT in line and _RETIRE_PER_FILE_MARKER in line and "[upgrade]" in line
    ]
    assert len(retire_lines) == 0, (
        f"Expected no retirement lines when no fossils are present, "
        f"found {len(retire_lines)}:\n" + "\n".join(retire_lines)
    )

    # No graveyard directory created.
    assert not (kanban_root / "retired").exists(), (
        "retired/ directory was created even though no fossils were present."
    )

    # Summary must say 'none'.
    assert "Retired:           none" in combined_output or "Retired: none" in combined_output, (
        f"Summary 'Retired: none' not found.\n"
        f"Full stdout:\n{result.stdout}"
    )


@pytest.mark.skipif(
    not _REAL_UPGRADE_SH.exists(),
    reason=f"Real upgrade.sh not found at {_REAL_UPGRADE_SH}; skipping.",
)
def test_malformed_manifest_aborts_before_any_file_is_moved(
    tmp_path: pathlib.Path,
) -> None:
    """upgrade.sh aborts with exit 1 when the manifest contains a glob.

    The hygiene lint must run before any file is touched.  If the manifest is
    malformed, the upgrade exits non-zero and leaves the live root unchanged.
    """
    sandbox = _build_fossil_sandbox(
        tmp_path,
        fossils=["release"],
        malformed_manifest=True,
    )
    result = _run_upgrade(sandbox, tmp_path)

    assert result.returncode != 0, (
        f"Expected non-zero exit for malformed manifest, got 0.\n"
        f"stdout:\n{result.stdout}\n\nstderr:\n{result.stderr}"
    )

    kanban_root = sandbox["kanban_root"]

    # The fossil file must still be at the live root (not moved).
    assert (kanban_root / "workflows" / "release.yaml").exists(), (
        "workflows/release.yaml was moved even though manifest was malformed — "
        "lint pre-check did not fire before file operations."
    )

    # No graveyard directory should have been created.
    assert not (kanban_root / "retired").exists(), (
        "retired/ directory was created despite manifest lint failure."
    )


@pytest.mark.skipif(
    not _REAL_UPGRADE_SH.exists(),
    reason=f"Real upgrade.sh not found at {_REAL_UPGRADE_SH}; skipping.",
)
def test_operator_authored_files_are_never_touched(
    tmp_path: pathlib.Path,
) -> None:
    """Operator-authored files not listed in the manifest survive the retirement step.

    Places a custom workflow plugin alongside the fossil yamls.  Only the
    manifest-listed files must be moved; the custom file must be byte-identical
    after the upgrade.
    """
    custom_rel = "workflows/custom-type/workflow.cfg"
    sandbox = _build_fossil_sandbox(
        tmp_path,
        fossils=["release"],
        custom_files=[custom_rel],
    )
    # Capture expected content before upgrade.
    kanban_root = sandbox["kanban_root"]
    custom_content_before = (kanban_root / custom_rel).read_text(encoding="utf-8")

    result = _run_upgrade(sandbox, tmp_path)

    assert result.returncode == 0, (
        f"upgrade.sh failed (exit {result.returncode}).\n"
        f"stdout:\n{result.stdout}\n\nstderr:\n{result.stderr}"
    )

    # Custom file must still be present and unchanged.
    custom_path = kanban_root / custom_rel
    assert custom_path.exists(), (
        f"Operator-authored file was removed or moved: {custom_rel}"
    )
    assert custom_path.read_text(encoding="utf-8") == custom_content_before, (
        f"Operator-authored file content changed after upgrade: {custom_rel}"
    )

    # The fossil was moved.
    assert not (kanban_root / "workflows" / "release.yaml").exists(), (
        "The manifest-listed fossil was not moved."
    )
