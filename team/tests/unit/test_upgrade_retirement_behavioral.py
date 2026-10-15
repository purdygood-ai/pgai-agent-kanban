"""
test_upgrade_retirement_behavioral.py
======================================
Behavioral acceptance tests for upgrade.sh's retirement step.

These tests exercise the three scenarios called out in the originating bug spec,
plus a fourth covering the stale operator scripts and API test an older install
carries after upgrading:

1. **Two-sided fixture** (fossil + custom plugin): plant a fossil
   ``workflows/release.yaml`` AND an operator-authored custom plugin
   ``workflows/my-custom-type/workflow.cfg`` in a live-layout sandbox,
   run the REAL upgrade.sh, and assert BOTH outcomes simultaneously:
   - The fossil is under ``retired/<ts>/workflows/release.yaml``.
   - The custom plugin bytes are identical before and after (not just text,
     actual bytes).

2. **Fresh-install no-op**: run the REAL upgrade.sh against a sandbox with
   no fossil files, assert the summary reports "Retired: none" and no
   ``retired/`` subtree is created.

3. **Malformed-manifest abort**: run the REAL upgrade.sh with a manifest
   containing a glob character, assert the upgrade exits non-zero and the
   fossil file is NOT moved (the lint pre-check fired before any file
   operation).

4. **Stale shipped files retired**: seed the six files the tree no longer
   ships (the defer-low-bugs pair and its lib, the underscore twin of
   release-locks.sh, the top-level agent-metrics.sh, and the API test that
   POSTs to routes that no longer exist) at a live root, run the REAL
   upgrade.sh, and assert none remains at the root, each sits under
   ``retired/<ts>/`` at its relative path, one per-file line narrates each
   move, and the retired-files lint's root scan flags them before the
   upgrade and is clean after it.

These tests complement the broader coverage in ``test_upgrade_fossil_advisory.py``
by focusing on the exact acceptance criteria from the bug spec.

SANDBOX DESIGN
--------------
Each test builds a minimal live-install sandbox and runs the real upgrade.sh
with ``--force --no-backup --stamp-version sandbox-test``.  The sandbox kanban
root lives under the pytest temp tree; upgrade.sh's temp-root guard therefore
skips crontab and pseudocron hooks automatically.

CONSTRAINTS ENFORCED BY THESE TESTS
-------------------------------------
- Real script: upgrade.sh is invoked directly, not shimmed.
- Self-contained: each sandbox is under a per-test tmp_path.
- Byte identity: the custom-plugin assertion compares bytes (``read_bytes()``),
  not modification times or text content.
- Exit code contract: tests exit 0 on green, non-zero on red; pytest fails
  the test if any assertion fails.
"""

from __future__ import annotations

import os
import pathlib
import shutil
import subprocess
import textwrap

import pytest

# ---------------------------------------------------------------------------
# Paths to the real scripts (dev-tree layout, relative to this test file)
# ---------------------------------------------------------------------------

_TEAM_SCRIPTS_DIR = pathlib.Path(__file__).parent.parent.parent / "scripts"
_REAL_UPGRADE_SH = _TEAM_SCRIPTS_DIR / "upgrade.sh"
_REAL_ARGPARSE_SH = _TEAM_SCRIPTS_DIR / "lib" / "argparse.sh"
_REAL_ENV_BOOTSTRAP_SH = _TEAM_SCRIPTS_DIR / "lib" / "env_bootstrap.sh"
_REAL_INI_PARSER_SH = _TEAM_SCRIPTS_DIR / "lib" / "ini_parser.sh"
_REAL_PROJECT_PATHS_SH = _TEAM_SCRIPTS_DIR / "lib" / "project_paths.sh"
_REAL_ERROR_CONTRACT_SH = _TEAM_SCRIPTS_DIR / "lib" / "error_contract.sh"
_REAL_TEMP_SH = _TEAM_SCRIPTS_DIR / "lib" / "temp.sh"
_REAL_RETIRED_LINT_SH = _TEAM_SCRIPTS_DIR / "lib" / "retired_files_lint.sh"
_REAL_VERSION_STAMP_SH = _TEAM_SCRIPTS_DIR / "lib" / "version_stamp.sh"
_REAL_SUBAGENT_DEPLOY_SH = _TEAM_SCRIPTS_DIR / "lib" / "subagent_deploy.sh"
_REAL_SCRIPT_COMMON_SH = _TEAM_SCRIPTS_DIR / "lib" / "script_common.sh"

_TEAM_TEMPLATES_DIR = pathlib.Path(__file__).parent.parent.parent / "templates"
_REAL_RETIRED_FILES_TXT = _TEAM_TEMPLATES_DIR / "retired-files.txt"

# Operator custom plugin path — exact name from the bug spec reproduction steps.
_CUSTOM_PLUGIN_REL = "workflows/my-custom-type/workflow.cfg"

# Files an older install still carries that the current tree no longer ships.
# Each must be named in the real manifest and retired by a real upgrade run.
_STALE_SHIPPED_FILES = [
    "scripts/defer-low-bugs.sh",
    "scripts/undefer-low-bugs.sh",
    "scripts/release_locks.sh",
    "scripts/agent-metrics.sh",
    "scripts/lib/defer_low_bugs.sh",
    "pgai_agent_kanban/api/tests/test_defer_low_bugs.py",
]

# Markers in upgrade.sh output that identify per-file retirement lines.
# Per-file lines: contain "Retired:" AND " -> " (the move arrow) AND "[upgrade]".
# The summary line contains "Retired:" but NOT " -> ".
_RETIRE_MOVE_MARKER = " -> "
_RETIRE_PREFIX = "Retired:"
_GRAVEYARD_PREFIX = "retired/"


# ---------------------------------------------------------------------------
# Shared sandbox builder
# ---------------------------------------------------------------------------


def _build_retirement_sandbox(
    tmp_path: pathlib.Path,
    *,
    fossils: list[str],
    custom_plugin: str | None = None,
    malformed_manifest: bool = False,
    stale_files: list[str] | None = None,
) -> dict[str, pathlib.Path]:
    """Build a minimal live-install sandbox for retirement behavioral tests.

    Creates a synthetic kanban root under ``tmp_path`` that passes upgrade.sh's
    pre-flight check.  Copies real library helpers so the retirement step runs
    exactly as it would in a production upgrade.

    Parameters
    ----------
    tmp_path:
        pytest tmp_path root for this test.
    fossils:
        List of workflow type names whose fossil yaml files should be present
        in the live install's ``workflows/`` directory.  Each name ``N``
        produces ``workflows/<N>.yaml``.  Pass ``[]`` for no fossils.
    custom_plugin:
        Optional kanban-root-relative path for an operator-authored file that
        must survive the upgrade with bytes unchanged.  Pass ``None`` to omit.
    malformed_manifest:
        When ``True``, write a manifest containing a glob character so the
        hygiene lint pre-check fires and aborts the upgrade.
    stale_files:
        Optional list of kanban-root-relative paths to seed as regular files
        at the live root, standing in for files an earlier release deposited
        and the current tree no longer ships.  Parent directories are
        created as needed.  Pass ``None`` to seed none.

    Returns
    -------
    dict with keys:
        ``"kanban_root"`` — path to the synthetic installed kanban root.
        ``"dev_tree"``    — path to the synthetic dev tree.
        ``"pgai_temp"``   — path to the temp dir for this sandbox run.
    """
    kanban_root = tmp_path / "kanban"
    dev_tree = tmp_path / "dev_tree"
    pgai_temp = tmp_path / "pgai_temp"

    kanban_root.mkdir()
    dev_tree.mkdir()
    pgai_temp.mkdir()

    # --- Kanban root: scripts/lib/ with real helpers ---
    installed_lib = kanban_root / "scripts" / "lib"
    installed_lib.mkdir(parents=True)

    _lib_sources = [
        _REAL_ARGPARSE_SH,
        _REAL_ENV_BOOTSTRAP_SH,
        _REAL_INI_PARSER_SH,
        _REAL_PROJECT_PATHS_SH,
        _REAL_ERROR_CONTRACT_SH,
        _REAL_TEMP_SH,
        _REAL_RETIRED_LINT_SH,
        _REAL_VERSION_STAMP_SH,
        _REAL_SUBAGENT_DEPLOY_SH, _REAL_SCRIPT_COMMON_SH,
    ]
    for lib_src in _lib_sources:
        if lib_src.exists():
            shutil.copy2(lib_src, installed_lib / lib_src.name)

    # Copy the real upgrade.sh into the installed tree so upgrade.sh can
    # locate its libraries relative to its own BASH_SOURCE.
    installed_upgrade = kanban_root / "scripts" / "upgrade.sh"
    shutil.copy2(_REAL_UPGRADE_SH, installed_upgrade)
    installed_upgrade.chmod(0o755)

    # --- Kanban root: VERSION (makes the pre-flight check succeed) ---
    (kanban_root / "VERSION").write_text("v1.2.0-sandbox\n", encoding="utf-8")

    # --- Kanban root: kanban.cfg pointing at the dev tree ---
    (kanban_root / "kanban.cfg").write_text(
        textwrap.dedent(
            f"""\
            [paths]
            dev_tree_path = {dev_tree}
            """
        ),
        encoding="utf-8",
    )

    # --- Kanban root: templates/ with retirement manifest ---
    installed_templates = kanban_root / "templates"
    installed_templates.mkdir()
    if malformed_manifest:
        (installed_templates / "retired-files.txt").write_text(
            "# malformed: glob character present\nworkflows/*.yaml\n",
            encoding="utf-8",
        )
    elif _REAL_RETIRED_FILES_TXT.exists():
        shutil.copy2(_REAL_RETIRED_FILES_TXT, installed_templates / "retired-files.txt")
    else:
        # Fallback: write the known v1.3.0 manifest entries inline so the test
        # does not depend on the manifest file being present in the dev tree.
        (installed_templates / "retired-files.txt").write_text(
            "# retired in v1.3.0 (pipeline-in-plugin)\n"
            "workflows/release.yaml\n"
            "workflows/document.yaml\n",
            encoding="utf-8",
        )

    # --- Kanban root: workflows/ with optional fossil files ---
    workflows_dir = kanban_root / "workflows"
    workflows_dir.mkdir()
    for fossil_type in fossils:
        (workflows_dir / f"{fossil_type}.yaml").write_text(
            textwrap.dedent(
                f"""\
                # Fossil pipeline file for {fossil_type} (pre-v1.3.0 layout).
                # Retirement step must move this file to the graveyard.
                type: {fossil_type}
                """
            ),
            encoding="utf-8",
        )

    # --- Kanban root: optional operator-authored custom plugin ---
    if custom_plugin is not None:
        custom_path = kanban_root / custom_plugin
        custom_path.parent.mkdir(parents=True, exist_ok=True)
        # Write binary-safe content so byte comparison is meaningful.
        custom_path.write_bytes(
            b"# operator-authored workflow config\n"
            b"[workflow]\n"
            b"type = my-custom-type\n"
            b"version = 1.0\n"
        )

    # --- Kanban root: optional stale files an earlier release deposited ---
    for stale_rel in stale_files or []:
        stale_path = kanban_root / stale_rel
        stale_path.parent.mkdir(parents=True, exist_ok=True)
        stale_path.write_text(
            f"# stale file the current tree no longer ships: {stale_rel}\n",
            encoding="utf-8",
        )

    # --- Dev tree: minimal content for the deposit loop ---
    # install.sh must exist at dev_tree root.
    dev_install = dev_tree / "install.sh"
    dev_install.write_text("#!/usr/bin/env bash\nexit 0\n", encoding="utf-8")
    dev_install.chmod(0o755)

    # team/ stubs for each item the deposit loop iterates over.
    (dev_tree / "team").mkdir(parents=True, exist_ok=True)
    for doc_name in ["README.md", "DIRECTIVES.md", "OVERVIEW.md", "SOP-core.md"]:
        (dev_tree / "team" / doc_name).write_text(
            f"# {doc_name} stub\n", encoding="utf-8"
        )
    for sub_name in ["roles", "pm-agent", "workflows", "pgai_agent_kanban", "halt_after", "demos"]:
        sub_dir = dev_tree / "team" / sub_name
        sub_dir.mkdir(parents=True, exist_ok=True)
        (sub_dir / ".gitkeep").write_text("", encoding="utf-8")

    # Dev tree scripts/ with real upgrade.sh (so deposit_item writes it through).
    dev_scripts_lib = dev_tree / "team" / "scripts" / "lib"
    dev_scripts_lib.mkdir(parents=True)
    shutil.copy2(_REAL_UPGRADE_SH, dev_tree / "team" / "scripts" / "upgrade.sh")
    (dev_tree / "team" / "scripts" / "upgrade.sh").chmod(0o755)
    for lib_src in _lib_sources:
        if lib_src.exists():
            shutil.copy2(lib_src, dev_scripts_lib / lib_src.name)

    # Dev tree templates/ — same manifest as installed root.
    dev_templates = dev_tree / "team" / "templates"
    dev_templates.mkdir(parents=True, exist_ok=True)
    if malformed_manifest:
        (dev_templates / "retired-files.txt").write_text(
            "# malformed: glob character present\nworkflows/*.yaml\n",
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

    # Dev tree subagents/ stub.
    subagents_dir = dev_tree / "subagents"
    subagents_dir.mkdir()
    (subagents_dir / "MANIFEST.txt").write_text("# Empty manifest\n", encoding="utf-8")

    return {
        "kanban_root": kanban_root,
        "dev_tree": dev_tree,
        "pgai_temp": pgai_temp,
    }


def _run_upgrade(
    sandbox: dict[str, pathlib.Path],
    tmp_path: pathlib.Path,
) -> subprocess.CompletedProcess:
    """Invoke the real upgrade.sh with --force --no-backup --stamp-version.

    The HOME override keeps crontab and pseudocron operations inside the
    sandbox and away from the host environment.
    """
    kanban_root = sandbox["kanban_root"]
    dev_tree = sandbox["dev_tree"]
    pgai_temp = sandbox["pgai_temp"]
    fake_home = tmp_path / "fake_home"
    fake_home.mkdir(exist_ok=True)

    env = dict(os.environ)
    env.update({
        "PGAI_AGENT_KANBAN_ROOT_PATH": str(kanban_root),
        "PGAI_AGENT_KANBAN_TEMP_DIR": str(pgai_temp),
        "HOME": str(fake_home),
    })

    return subprocess.run(
        [
            "bash",
            str(kanban_root / "scripts" / "upgrade.sh"),
            "--force",
            "--no-backup",
            "--dev-tree", str(dev_tree),
            "--stamp-version", "sandbox-test",
        ],
        capture_output=True,
        text=True,
        timeout=60,
        env=env,
    )


def _run_retired_files_root_scan(
    kanban_root: pathlib.Path,
) -> subprocess.CompletedProcess:
    """Run the real retired-files lint's root scan over a sandbox root.

    Uses the manifest and lint helper the sandbox builder installed under
    ``kanban_root`` so the scan reads exactly what upgrade.sh read.
    """
    return subprocess.run(
        [
            "bash",
            str(kanban_root / "scripts" / "lib" / "retired_files_lint.sh"),
            str(kanban_root / "templates" / "retired-files.txt"),
            str(kanban_root),
        ],
        capture_output=True,
        text=True,
        timeout=30,
        env={"PATH": os.environ.get("PATH", ""), "HOME": str(kanban_root), "LANG": "C"},
    )


def _graveyard_dir(kanban_root: pathlib.Path) -> pathlib.Path | None:
    """Return the single timestamped subdirectory under retired/, or None."""
    retired_root = kanban_root / "retired"
    if not retired_root.exists():
        return None
    children = [p for p in retired_root.iterdir() if p.is_dir()]
    return children[0] if len(children) == 1 else None


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


@pytest.mark.skipif(
    not _REAL_UPGRADE_SH.exists(),
    reason=f"Real upgrade.sh not found at {_REAL_UPGRADE_SH}; skipping.",
)
def test_two_sided_fixture_fossil_retired_and_custom_plugin_bytes_unchanged(
    tmp_path: pathlib.Path,
) -> None:
    """Both sides of the retirement contract hold in a single sandbox run.

    Plants a fossil ``workflows/release.yaml`` AND an operator-authored
    ``workflows/my-custom-type/workflow.cfg`` in the same live-layout sandbox.
    Runs the REAL upgrade.sh.  Asserts BOTH outcomes simultaneously:

    - The fossil ``workflows/release.yaml`` is moved to
      ``retired/<ts>/workflows/release.yaml`` (no longer present at the live root).
    - The operator custom plugin ``workflows/my-custom-type/workflow.cfg``
      is byte-identical before and after the upgrade.  Not just the same text —
      the same bytes.

    This is the exact "two-sided fixture" from the original reproduction steps.
    """
    sandbox = _build_retirement_sandbox(
        tmp_path,
        fossils=["release"],
        custom_plugin=_CUSTOM_PLUGIN_REL,
    )
    kanban_root = sandbox["kanban_root"]
    custom_path = kanban_root / _CUSTOM_PLUGIN_REL

    # Capture the custom plugin bytes BEFORE the upgrade.
    bytes_before = custom_path.read_bytes()

    result = _run_upgrade(sandbox, tmp_path)

    assert result.returncode == 0, (
        f"upgrade.sh exited non-zero (code {result.returncode}).\n"
        f"stdout:\n{result.stdout}\n"
        f"stderr:\n{result.stderr}"
    )

    combined = result.stdout + result.stderr

    # Side A: the fossil must be in the graveyard, not at the live root.
    assert not (kanban_root / "workflows" / "release.yaml").exists(), (
        "workflows/release.yaml still exists at the live root after upgrade; "
        "the retirement step did not move it."
    )
    graveyard = _graveyard_dir(kanban_root)
    assert graveyard is not None, (
        "No retired/<ts>/ directory was created; retirement step did not fire."
    )
    assert (graveyard / "workflows" / "release.yaml").exists(), (
        f"workflows/release.yaml not found in graveyard {graveyard}."
    )

    # Side B: the custom plugin must be byte-identical (not just same text).
    assert custom_path.exists(), (
        f"Operator custom plugin was removed or moved: {_CUSTOM_PLUGIN_REL}"
    )
    bytes_after = custom_path.read_bytes()
    assert bytes_after == bytes_before, (
        f"Operator custom plugin bytes changed after upgrade.\n"
        f"Before: {bytes_before!r}\n"
        f"After:  {bytes_after!r}"
    )

    # Per-file retirement line must appear for the fossil.
    retire_lines = [
        line for line in combined.splitlines()
        if _RETIRE_PREFIX in line and _RETIRE_MOVE_MARKER in line and "[upgrade]" in line
    ]
    assert any("release.yaml" in ln for ln in retire_lines), (
        f"No per-file retirement line mentioning release.yaml found.\n"
        f"All retirement lines:\n"
        + ("\n".join(retire_lines) or "  (none)")
        + f"\n\nFull stdout:\n{result.stdout}"
    )


@pytest.mark.skipif(
    not _REAL_UPGRADE_SH.exists(),
    reason=f"Real upgrade.sh not found at {_REAL_UPGRADE_SH}; skipping.",
)
def test_fresh_install_sandbox_has_no_retirement_artifacts(
    tmp_path: pathlib.Path,
) -> None:
    """Retirement step is a silent no-op on a fresh install with no fossils.

    A clean post-v1.3.0 install — or any install where the operator has
    already removed the fossil files — must not produce a ``retired/``
    subtree.  The upgrade summary must report "Retired: none".
    """
    sandbox = _build_retirement_sandbox(tmp_path, fossils=[])

    result = _run_upgrade(sandbox, tmp_path)

    assert result.returncode == 0, (
        f"upgrade.sh exited non-zero (code {result.returncode}).\n"
        f"stdout:\n{result.stdout}\n"
        f"stderr:\n{result.stderr}"
    )

    kanban_root = sandbox["kanban_root"]
    combined = result.stdout + result.stderr

    # No retired/ directory must exist.
    assert not (kanban_root / "retired").exists(), (
        "retired/ subtree was created even though no fossil files were present."
    )

    # Summary line must say "Retired: none" (the exact form upgrade.sh prints).
    assert "Retired:" in combined, (
        "The upgrade summary 'Retired:' line was not found in upgrade output."
    )
    retire_summary_lines = [
        line for line in combined.splitlines()
        if _RETIRE_PREFIX in line and _RETIRE_MOVE_MARKER not in line
    ]
    assert any("none" in ln for ln in retire_summary_lines), (
        f"Expected 'Retired: ... none' in summary, but found:\n"
        + ("\n".join(retire_summary_lines) or "  (none)")
        + f"\n\nFull stdout:\n{result.stdout}"
    )

    # No per-file retirement lines.
    per_file_lines = [
        line for line in combined.splitlines()
        if _RETIRE_PREFIX in line and _GRAVEYARD_PREFIX in line
        and _RETIRE_MOVE_MARKER in line and "[upgrade]" in line
    ]
    assert len(per_file_lines) == 0, (
        f"Expected zero per-file retirement lines, found {len(per_file_lines)}:\n"
        + "\n".join(per_file_lines)
    )


@pytest.mark.skipif(
    not _REAL_UPGRADE_SH.exists(),
    reason=f"Real upgrade.sh not found at {_REAL_UPGRADE_SH}; skipping.",
)
def test_malformed_manifest_aborts_before_any_file_is_moved(
    tmp_path: pathlib.Path,
) -> None:
    """upgrade.sh aborts non-zero when the manifest contains a glob character.

    The hygiene lint pre-check must run BEFORE any file is moved.  If the
    manifest is malformed (glob character, absolute path, or ``..`` segment),
    the upgrade must exit non-zero and leave the live root unchanged.

    This verifies that the lint gate truly fires BEFORE the retirement loop —
    not just that the lint check exists in the code.
    """
    sandbox = _build_retirement_sandbox(
        tmp_path,
        fossils=["release"],
        malformed_manifest=True,
    )
    kanban_root = sandbox["kanban_root"]

    result = _run_upgrade(sandbox, tmp_path)

    # Must exit non-zero.
    assert result.returncode != 0, (
        "Expected non-zero exit when manifest is malformed, but upgrade.sh "
        f"returned exit code 0.\n"
        f"stdout:\n{result.stdout}\n"
        f"stderr:\n{result.stderr}"
    )

    # The fossil file must remain at the live root — not moved.
    assert (kanban_root / "workflows" / "release.yaml").exists(), (
        "workflows/release.yaml was moved even though the manifest was malformed. "
        "The hygiene lint pre-check did not fire before file operations."
    )

    # No graveyard directory should have been created.
    assert not (kanban_root / "retired").exists(), (
        "retired/ directory was created despite manifest lint failure. "
        "Files were moved before or during the failed lint check."
    )


@pytest.mark.skipif(
    not _REAL_UPGRADE_SH.exists(),
    reason=f"Real upgrade.sh not found at {_REAL_UPGRADE_SH}; skipping.",
)
def test_stale_shipped_files_are_retired_and_root_scan_flags_them_only_before(
    tmp_path: pathlib.Path,
) -> None:
    """Files an older install carries that the tree no longer ships are retired.

    Seeds the six stale paths at a live-layout sandbox root, runs the REAL
    upgrade.sh, and asserts the whole retirement contract for each:

    - It is named in the real manifest (so the retirement step can see it).
    - The retired-files lint's root scan names it BEFORE the upgrade and
      exits non-zero; AFTER the upgrade the same scan is clean and exits 0.
    - It is gone from the live root and sits under ``retired/<ts>/`` at its
      original relative path.
    - One per-file ``Retired: <rel> -> retired/<ts>/<rel>`` line narrates it.
    """
    sandbox = _build_retirement_sandbox(
        tmp_path,
        fossils=[],
        stale_files=_STALE_SHIPPED_FILES,
    )
    kanban_root = sandbox["kanban_root"]

    manifest_lines = [
        ln.strip()
        for ln in (kanban_root / "templates" / "retired-files.txt").read_text(
            encoding="utf-8"
        ).splitlines()
        if ln.strip() and not ln.lstrip().startswith("#")
    ]
    missing_from_manifest = [p for p in _STALE_SHIPPED_FILES if p not in manifest_lines]
    assert not missing_from_manifest, (
        "Stale paths not named in templates/retired-files.txt; the retirement "
        f"step cannot retire them: {missing_from_manifest}"
    )

    # Root scan BEFORE the upgrade: every seeded path is reported, exit 1.
    scan_before = _run_retired_files_root_scan(kanban_root)
    assert scan_before.returncode == 1, (
        "retired-files lint root scan should exit 1 while the stale files are "
        f"present, got {scan_before.returncode}.\n"
        f"stdout:\n{scan_before.stdout}\nstderr:\n{scan_before.stderr}"
    )
    not_flagged = [
        p for p in _STALE_SHIPPED_FILES
        if str(kanban_root / p) not in scan_before.stderr
    ]
    assert not not_flagged, (
        f"Root scan did not flag these present stale files: {not_flagged}\n"
        f"stderr:\n{scan_before.stderr}"
    )

    result = _run_upgrade(sandbox, tmp_path)

    assert result.returncode == 0, (
        f"upgrade.sh exited non-zero (code {result.returncode}).\n"
        f"stdout:\n{result.stdout}\n"
        f"stderr:\n{result.stderr}"
    )

    combined = result.stdout + result.stderr
    graveyard = _graveyard_dir(kanban_root)
    assert graveyard is not None, (
        "No single retired/<ts>/ directory was created; retirement step did not fire."
    )

    retire_lines = [
        line for line in combined.splitlines()
        if _RETIRE_PREFIX in line and _RETIRE_MOVE_MARKER in line and "[upgrade]" in line
    ]

    still_at_root = [p for p in _STALE_SHIPPED_FILES if (kanban_root / p).exists()]
    assert not still_at_root, (
        f"Stale files still at the live root after upgrade: {still_at_root}\n"
        f"stdout:\n{result.stdout}"
    )
    not_in_graveyard = [
        p for p in _STALE_SHIPPED_FILES if not (graveyard / p).is_file()
    ]
    assert not not_in_graveyard, (
        f"Stale files not found under {graveyard} at their relative path: "
        f"{not_in_graveyard}"
    )
    not_narrated = [
        p for p in _STALE_SHIPPED_FILES
        if not any(f"{p} -> " in ln and f"{_GRAVEYARD_PREFIX}" in ln for ln in retire_lines)
    ]
    assert not not_narrated, (
        f"No per-file retirement line for: {not_narrated}\n"
        "All retirement lines:\n" + ("\n".join(retire_lines) or "  (none)")
    )
    assert f"Retired:           {len(_STALE_SHIPPED_FILES)} file(s)" in combined, (
        f"Summary line does not count all {len(_STALE_SHIPPED_FILES)} retirements.\n"
        f"stdout:\n{result.stdout}"
    )

    # Root scan AFTER the upgrade: nothing listed remains, exit 0.
    scan_after = _run_retired_files_root_scan(kanban_root)
    assert scan_after.returncode == 0, (
        "retired-files lint root scan should be clean after the upgrade, "
        f"got {scan_after.returncode}.\nstderr:\n{scan_after.stderr}"
    )
