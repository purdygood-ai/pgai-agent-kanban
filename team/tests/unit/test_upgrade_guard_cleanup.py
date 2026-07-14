"""
test_upgrade_guard_cleanup.py
=============================
Converted behavioral tests for cleanup and lib-sourcing properties of
upgrade.sh.

CONVERSION NOTE (v1.7.0)
-------------------------
This file previously tested the temp-copy re-exec guard: an earlier defect (temp-file
cleanup) and an earlier defect (lib-sourcing failure in the re-exec'd child).  The
guard — and the shim-based tests that exercised it — are retired in v1.7.0.

The CONCERNS the guard addressed are now structurally resolved by the
two-phase architecture:

- **Temp-file cleanup**: the two-phase architecture creates no temp self-copy
  at all.  Phase 1 execs directly into the dev-tree script without copying
  itself.  The cleanup concern converts to: after an upgrade, no
  ``upgrade_self.*`` artifacts exist under PGAI_AGENT_KANBAN_TEMP_DIR.

- **Lib-sourcing correctness**: the guard used ``_PGAI_UPGRADE_ORIG_DIR`` to
  route lib sourcing back to the original directory (not the temp copy's
  directory).  Under the two-phase architecture, ``BASH_SOURCE[0]`` is always
  the correct file (installed upgrade.sh in phase 1; dev-tree upgrade.sh in
  phase 2).  The lib-sourcing concern converts to: BASH_SOURCE-relative
  sourcing of argparse.sh succeeds in both phases.

DESIGN (REAL-SCRIPT, LIVE-LAYOUT)
----------------------------------
The converted tests use the real upgrade.sh and a live-layout sandbox, not a
shim.  This matches the B9 standard and avoids the same shim-omission failure
that an earlier defect exposed: a shim that omits a failing dimension verifies nothing
about it.

See test_upgrade_two_phase_behavioral.py for the full set of five behavioral
fixtures plus the two converted one-run and byte-different scenarios.
"""

from __future__ import annotations

import os
import pathlib
import shutil
import subprocess
import textwrap

import pytest

# ---------------------------------------------------------------------------
# Real script paths
# ---------------------------------------------------------------------------

_TEAM_SCRIPTS_DIR = pathlib.Path(__file__).parent.parent.parent / "scripts"
_REAL_UPGRADE_SH = _TEAM_SCRIPTS_DIR / "upgrade.sh"
_REAL_ARGPARSE_SH = _TEAM_SCRIPTS_DIR / "lib" / "argparse.sh"
_REAL_ENV_BOOTSTRAP_SH = _TEAM_SCRIPTS_DIR / "lib" / "env_bootstrap.sh"
_REAL_INI_PARSER_SH = _TEAM_SCRIPTS_DIR / "lib" / "ini_parser.sh"
_REAL_PROJECT_PATHS_SH = _TEAM_SCRIPTS_DIR / "lib" / "project_paths.sh"
_REAL_TEMP_SH = _TEAM_SCRIPTS_DIR / "lib" / "temp.sh"
_REAL_VERSION_STAMP_SH = _TEAM_SCRIPTS_DIR / "lib" / "version_stamp.sh"

_REAL_LIBS = [
    _REAL_ARGPARSE_SH,
    _REAL_ENV_BOOTSTRAP_SH,
    _REAL_INI_PARSER_SH,
    _REAL_PROJECT_PATHS_SH,
    _REAL_TEMP_SH,
    _REAL_VERSION_STAMP_SH,
]

_SANDBOX_VERSION = "v1.7.0-sandbox-converted"


# ---------------------------------------------------------------------------
# Sandbox builder (shared with converted tests)
# ---------------------------------------------------------------------------


def _build_sandbox(tmp_path: pathlib.Path) -> dict[str, pathlib.Path]:
    """Build a minimal live-layout sandbox for the converted cleanup tests."""
    kanban_root = tmp_path / "kanban_root"
    dev_tree = tmp_path / "dev_tree"
    pgai_temp = tmp_path / "pgai_temp"

    kanban_root.mkdir()
    dev_tree.mkdir()
    pgai_temp.mkdir()

    installed_scripts = kanban_root / "scripts"
    installed_lib = installed_scripts / "lib"
    installed_lib.mkdir(parents=True)

    for lib_src in _REAL_LIBS:
        if lib_src.exists():
            shutil.copy2(lib_src, installed_lib / lib_src.name)

    installed_upgrade = installed_scripts / "upgrade.sh"
    shutil.copy2(_REAL_UPGRADE_SH, installed_upgrade)
    installed_upgrade.chmod(0o755)

    (kanban_root / "kanban.cfg").write_text(
        textwrap.dedent(
            f"""\
            [paths]
            dev_tree_path = {dev_tree}
            """
        ),
        encoding="utf-8",
    )
    (kanban_root / "VERSION").write_text("v1.6.0-sandbox\n", encoding="utf-8")

    dev_install = dev_tree / "install.sh"
    dev_install.write_text("#!/usr/bin/env bash\nexit 0\n", encoding="utf-8")
    dev_install.chmod(0o755)

    dev_scripts = dev_tree / "team" / "scripts"
    dev_lib = dev_scripts / "lib"
    dev_lib.mkdir(parents=True)
    shutil.copy2(_REAL_UPGRADE_SH, dev_scripts / "upgrade.sh")
    (dev_scripts / "upgrade.sh").chmod(0o755)
    for lib_src in _REAL_LIBS:
        if lib_src.exists():
            shutil.copy2(lib_src, dev_lib / lib_src.name)

    for doc_name in ["README.md", "DIRECTIVES.md", "OVERVIEW.md", "SOP.md"]:
        (dev_tree / "team" / doc_name).write_text(f"# {doc_name} stub\n", encoding="utf-8")
    for sub_name in [
        "roles", "pm-agent", "workflows", "pgai_agent_kanban",
        "halt_after", "templates", "demos",
    ]:
        sub = dev_tree / "team" / sub_name
        sub.mkdir(parents=True, exist_ok=True)
        (sub / ".gitkeep").write_text("", encoding="utf-8")

    subagents = dev_tree / "subagents"
    subagents.mkdir()
    (subagents / "MANIFEST.txt").write_text("# Empty manifest\n", encoding="utf-8")

    return {
        "kanban_root": kanban_root,
        "dev_tree": dev_tree,
        "pgai_temp": pgai_temp,
    }


def _run_upgrade(
    sandbox: dict[str, pathlib.Path],
    tmp_path: pathlib.Path,
) -> subprocess.CompletedProcess:
    """Run the installed upgrade.sh with --force --no-backup --stamp-version."""
    kanban_root = sandbox["kanban_root"]
    dev_tree = sandbox["dev_tree"]
    pgai_temp = sandbox["pgai_temp"]
    fake_home = tmp_path / "fake_home"
    fake_home.mkdir(exist_ok=True)

    env = dict(os.environ)
    # Remove outer PGAI_DEV_TREE_PATH so the --dev-tree argument wins.
    env.pop("PGAI_DEV_TREE_PATH", None)
    env.update(
        {
            "PGAI_AGENT_KANBAN_ROOT_PATH": str(kanban_root),
            "PGAI_AGENT_KANBAN_TEMP_DIR": str(pgai_temp),
            "HOME": str(fake_home),
        }
    )

    return subprocess.run(
        [
            "bash",
            str(kanban_root / "scripts" / "upgrade.sh"),
            "--force",
            "--no-backup",
            "--dev-tree", str(dev_tree),
            "--stamp-version", _SANDBOX_VERSION,
        ],
        capture_output=True,
        text=True,
        timeout=60,
        env=env,
    )


# ---------------------------------------------------------------------------
# Converted tests: cleanup concern
# ---------------------------------------------------------------------------


@pytest.mark.skipif(
    not _REAL_UPGRADE_SH.exists(),
    reason=f"Real upgrade.sh not found at {_REAL_UPGRADE_SH}; skipping.",
)
def test_no_temp_self_copy_created_by_successful_upgrade(
    tmp_path: pathlib.Path,
) -> None:
    """A successful upgrade leaves no upgrade_self.* artifacts in the temp dir.

    The two-phase architecture does not create a temp self-copy at all.  The
    original guard created a temp file, exec'd it, and the cleanup trap removed
    it.  Under the new design, the concern converts to: no such file is
    created in the first place.

    This test verifies the absence of upgrade_self.* under PGAI_AGENT_KANBAN_TEMP_DIR
    after a successful upgrade run — confirming the guard's temp-file creation
    does not exist in the new code path.
    """
    sandbox = _build_sandbox(tmp_path)
    pgai_temp = sandbox["pgai_temp"]

    result = _run_upgrade(sandbox, tmp_path)

    assert result.returncode == 0, (
        f"Upgrade failed (exit {result.returncode}).\n"
        f"stdout:\n{result.stdout}\n\nstderr:\n{result.stderr}"
    )

    leaked = list(pgai_temp.glob("upgrade_self.*"))
    assert not leaked, (
        f"Unexpected upgrade_self.* artifacts found under {pgai_temp} after "
        f"successful upgrade.  The two-phase architecture should not create a "
        f"temp self-copy at all.\n"
        f"Found {len(leaked)} file(s):\n"
        + "\n".join(f"  {f}" for f in leaked)
    )


@pytest.mark.skipif(
    not _REAL_UPGRADE_SH.exists(),
    reason=f"Real upgrade.sh not found at {_REAL_UPGRADE_SH}; skipping.",
)
def test_no_temp_self_copy_created_by_failed_upgrade(
    tmp_path: pathlib.Path,
) -> None:
    """A failed upgrade leaves no upgrade_self.* artifacts in the temp dir.

    Even when the upgrade fails (e.g., broken dev-tree script), the two-phase
    architecture must not leave temp self-copy artifacts.  The old trap cleaned
    them up on failure; the new architecture produces none.
    """
    # Create a broken dev-tree script so the upgrade fails at bash -n.
    broken = tmp_path / "broken_upgrade.sh"
    broken.write_text(
        textwrap.dedent(
            """\
            #!/usr/bin/env bash
            if [[ true ]]; then
              echo "unclosed if — syntax error
            # missing fi
            """
        ),
        encoding="utf-8",
    )
    broken.chmod(0o755)

    sandbox = _build_sandbox(tmp_path)
    # Replace the dev-tree upgrade.sh with the broken one.
    dev_upgrade = sandbox["dev_tree"] / "team" / "scripts" / "upgrade.sh"
    shutil.copy2(broken, dev_upgrade)
    dev_upgrade.chmod(0o755)

    pgai_temp = sandbox["pgai_temp"]

    result = _run_upgrade(sandbox, tmp_path)

    # Must fail.
    assert result.returncode != 0, (
        f"Expected non-zero exit for broken dev-tree script; "
        f"got {result.returncode}."
    )

    leaked = list(pgai_temp.glob("upgrade_self.*"))
    assert not leaked, (
        f"Unexpected upgrade_self.* artifacts found under {pgai_temp} after "
        f"failed upgrade.  Found {len(leaked)} file(s):\n"
        + "\n".join(f"  {f}" for f in leaked)
    )


# ---------------------------------------------------------------------------
# Converted tests: lib-sourcing concern
# ---------------------------------------------------------------------------


@pytest.mark.skipif(
    not _REAL_UPGRADE_SH.exists(),
    reason=f"Real upgrade.sh not found at {_REAL_UPGRADE_SH}; skipping.",
)
def test_lib_sourcing_succeeds_in_phase1_and_phase2(
    tmp_path: pathlib.Path,
) -> None:
    """BASH_SOURCE-relative lib sourcing succeeds in both phase 1 and phase 2.

    The old an earlier defect fix used _PGAI_UPGRADE_ORIG_DIR to route library sourcing
    from the re-exec'd child (running in the temp copy's directory) back to the
    original script's directory.  Under the two-phase architecture, BASH_SOURCE[0]
    always resolves to the correct file:
      - Phase 1: the installed upgrade.sh in kanban_root/scripts/
      - Phase 2: the dev-tree upgrade.sh in dev_tree/team/scripts/

    A successful upgrade (exit 0, "Upgrade complete!") confirms that argparse.sh
    and the other libs were sourced correctly in both phases.  An argparse.sh
    sourcing failure causes upgrade.sh to exit immediately with a missing-lib
    error, which would produce a non-zero exit.
    """
    sandbox = _build_sandbox(tmp_path)

    result = _run_upgrade(sandbox, tmp_path)

    assert result.returncode == 0, (
        f"Upgrade failed — lib sourcing may have broken in phase 1 or phase 2.\n"
        f"Under the old code, a missing argparse.sh from the wrong directory "
        f"(temp copy's dir) caused exit with: '[upgrade] ERROR: argparse.sh not found'.\n"
        f"stdout:\n{result.stdout}\n\nstderr:\n{result.stderr}"
    )
    assert "Upgrade complete!" in result.stdout, (
        f"'Upgrade complete!' not found — upgrade did not reach the summary.\n"
        f"stdout:\n{result.stdout}"
    )

    # Confirm no argparse error appeared.
    combined = result.stdout + result.stderr
    assert "argparse.sh not found" not in combined, (
        f"argparse.sh-not-found error appeared despite successful exit code.\n"
        f"Output:\n{combined}"
    )


# ---------------------------------------------------------------------------
# Permanent lint guard: temp-copy guard must not reappear in upgrade.sh
# ---------------------------------------------------------------------------


def test_upgrade_no_temp_copy_guard() -> None:
    """Permanent assertion: the retired temp-copy guard variables must not exist
    in upgrade.sh as executable code.

    The v1.7.0 two-phase upgrade architecture eliminated the need for the
    self-overwrite guard (_PGAI_UPGRADE_SELF_COPY / _PGAI_UPGRADE_REEXEC /
    _PGAI_UPGRADE_ORIG_DIR).  a follow-up defect confirmed that a squash-merge conflict
    at the CM layer can silently resurrect the retired block.  This test makes
    resurrection-by-merge fail the unit suite permanently, the same way
    lint_api_parity promotes one-time acceptance criteria into the runner.

    The check excludes comment-only lines (lines where the first non-whitespace
    character is '#') so that past-tense explanatory comments describing the
    retirement are allowed to remain as historical documentation.
    """
    assert _REAL_UPGRADE_SH.exists(), (
        f"upgrade.sh not found at {_REAL_UPGRADE_SH}; cannot run guard check."
    )

    _RETIRED_GUARD_PATTERN = (
        "_PGAI_UPGRADE_SELF_COPY",
        "_PGAI_UPGRADE_REEXEC",
        "_PGAI_UPGRADE_ORIG_DIR",
    )

    executable_hits: list[tuple[int, str]] = []
    upgrade_text = _REAL_UPGRADE_SH.read_text(encoding="utf-8")

    for lineno, line in enumerate(upgrade_text.splitlines(), start=1):
        stripped = line.lstrip()
        if stripped.startswith("#"):
            # Comment-only line — past-tense documentation is permitted.
            continue
        for name in _RETIRED_GUARD_PATTERN:
            if name in line:
                executable_hits.append((lineno, line.rstrip()))
                break  # one hit per line is enough

    assert not executable_hits, (
        f"Retired temp-copy guard variable(s) found as executable code in "
        f"{_REAL_UPGRADE_SH}.  The two-phase architecture retired these in "
        f"v1.7.0; their reappearance indicates a squash-merge conflict restored "
        f"the pre-retirement block.  Remove the listed lines and re-run.\n\n"
        f"Executable hits ({len(executable_hits)}):\n"
        + "\n".join(f"  line {n}: {l}" for n, l in executable_hits)
    )
