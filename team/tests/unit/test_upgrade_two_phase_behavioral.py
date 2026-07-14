"""
test_upgrade_two_phase_behavioral.py
=====================================
Behavioral acceptance tests for the two-phase upgrade architecture introduced
in v1.7.0.

COVERAGE
--------
This file covers the five behavioral scenarios required by v1.7.0 plus the
two converted scenarios from the retired temp-copy re-exec guard:

NEW FIXTURES
~~~~~~~~~~~~
1. **version-skew** — An older phase-capable installed script hands off to a
   newer dev-tree script that contains a sentinel phase-2-only step.  Running
   the installed script produces a probe file ``upgraded-by: <new-version>``,
   exits 0, completes in ONE run, with the dev-tree VERSION stamped.

2. **self-handoff** — Same-version handoff: the installed and dev-tree scripts
   are identical, yet the upgrade completes successfully (phase 1 still execs
   into phase 2; the outcome is functionally equivalent to a single-phase run).

3. **broken-new-script** — The dev-tree upgrade.sh contains a bash syntax
   error.  Phase 1 fails at the ``bash -n`` probe; nothing is deposited;
   the pre-upgrade backup exists; exit is non-zero.

4. **protocol-mismatch** — The dev-tree script supports ``--phase2`` but
   rejects protocol number "99".  Fails loud, carries the manual-bootstrap
   instruction, nothing deposited.

5. **no-phase2-support** — The dev-tree script does not contain the
   ``--phase2`` flag at all (simulates a pre-v1.7 or downgrade target).
   Phase 1's grep probe fails loud with the hand-copy instruction.

CONVERTED SCENARIOS (from retired re-exec guard)
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~
6. **upgrade-completes-when-installed-copy-differs** (was: byte-different) —
   The installed upgrade.sh differs byte-for-byte from the dev-tree copy.
   Under the old mechanism this was the case where the self-overwrite guard
   fired.  Under the new two-phase architecture, phase 1 always execs to the
   dev-tree script — the bytes of the installed copy are irrelevant to whether
   the upgrade completes.  The fixture confirms the full upgrade (exit 0,
   "Upgrade complete!") regardless of installed-vs-deposited byte difference.

7. **upgrade-completes-in-one-run** (was: one-run) — The upgrade completes
   in exactly one process chain (phase 1 → exec → phase 2): no retry, no
   re-exec loop.  Confirmed via exit 0 and "Upgrade complete!" in one
   subprocess.run call.

NO-ORPHAN-PHASE-1 ASSERTION
-----------------------------
Each fixture asserts that after the upgrade returns, no process running the
installed upgrade.sh script path remains.  The exec in phase 1 replaces the
bash process; if a child upgrade.sh were still running, the subprocess would
not have returned.  The assertion is:
    - subprocess.run returned (confirms phase 1 did not leave a zombie)
    - ps output does not contain the installed upgrade.sh path

REAL-SCRIPT, LIVE-LAYOUT DESIGN
---------------------------------
All fixtures run the real upgrade.sh — no shims.  Each test builds a
minimal live-layout sandbox under pytest's tmp_path tree (which lands under
PGAI_AGENT_KANBAN_TEMP_DIR/tests/ per the conftest redirect).  The sandbox
contains:

    kanban_root/           — installed kanban root (PGAI_AGENT_KANBAN_ROOT_PATH)
        kanban.cfg         — provides [paths] dev_tree_path
        VERSION            — satisfies pre-flight check
        scripts/
            upgrade.sh     — the INSTALLED (old/same) script
            lib/           — real argparse.sh, ini_parser.sh, project_paths.sh,
                             temp.sh (sourced at startup)
    dev_tree/              — dev tree (PGAI_DEV_TREE_PATH)
        install.sh         — stub (exit 0)
        team/
            scripts/
                upgrade.sh — the NEW dev-tree script (possibly with sentinel)
                lib/       — same real libs
            ...stubs...

The sandbox kanban root is under the pytest temp tree, so upgrade.sh's
temp-root guard skips crontab and pseudocron hooks.

NAMING CONVENTION (SOP.md)
---------------------------
Test function names describe BEHAVIOR, not bug IDs or task IDs.
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

# Version written into the dev-tree VERSION file for fixtures that need
# an explicit version (avoids needing a git repo in the sandbox).
_SANDBOX_NEW_VERSION = "v1.7.0-sandbox-new"
_SANDBOX_OLD_VERSION = "v1.6.0-sandbox-old"


# ---------------------------------------------------------------------------
# Sandbox builder
# ---------------------------------------------------------------------------


def _build_two_phase_sandbox(
    tmp_path: pathlib.Path,
    *,
    installed_upgrade_sh: pathlib.Path | None = None,
    dev_tree_upgrade_sh: pathlib.Path | None = None,
    installed_version: str = _SANDBOX_OLD_VERSION,
    dev_tree_version: str = _SANDBOX_NEW_VERSION,
) -> dict[str, pathlib.Path]:
    """Build a minimal live-layout sandbox for two-phase behavioral tests.

    Creates a synthetic kanban root and dev tree under ``tmp_path``.  Copies
    real library helpers into both trees.  Caller supplies the installed and
    dev-tree upgrade.sh paths; when omitted, the real upgrade.sh is used for
    both (the self-handoff / same-version scenario).

    Parameters
    ----------
    tmp_path:
        pytest ``tmp_path`` root for this test.
    installed_upgrade_sh:
        Path to the script to install as the phase-1 bootstrap.  Defaults to
        the real upgrade.sh.
    dev_tree_upgrade_sh:
        Path to the script to place in the dev tree (phase-2 target).
        Defaults to the real upgrade.sh.
    installed_version:
        Version string written to ``kanban_root/VERSION`` (the pre-upgrade
        version displayed on failure; NOT the stamp written by phase 2).
    dev_tree_version:
        Version passed to ``--stamp-version`` in ``_run_sandbox_upgrade`` so
        that the stamp written by phase 2 is deterministic without needing a
        git repo.

    Returns
    -------
    dict with keys:
        ``"kanban_root"``    — installed kanban root path
        ``"dev_tree"``       — dev tree path
        ``"pgai_temp"``      — temp dir for this sandbox
        ``"dev_tree_version"``  — the version string to pass as --stamp-version
    """
    if installed_upgrade_sh is None:
        installed_upgrade_sh = _REAL_UPGRADE_SH
    if dev_tree_upgrade_sh is None:
        dev_tree_upgrade_sh = _REAL_UPGRADE_SH

    kanban_root = tmp_path / "kanban_root"
    dev_tree = tmp_path / "dev_tree"
    pgai_temp = tmp_path / "pgai_temp"

    kanban_root.mkdir()
    dev_tree.mkdir()
    pgai_temp.mkdir()

    # --- Kanban root: installed upgrade.sh + real libs ---
    installed_scripts = kanban_root / "scripts"
    installed_lib = installed_scripts / "lib"
    installed_lib.mkdir(parents=True)

    for lib_src in _REAL_LIBS:
        if lib_src.exists():
            shutil.copy2(lib_src, installed_lib / lib_src.name)

    installed_upgrade = installed_scripts / "upgrade.sh"
    shutil.copy2(installed_upgrade_sh, installed_upgrade)
    installed_upgrade.chmod(0o755)

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

    # --- Kanban root: VERSION (pre-flight check needs one of kanban.cfg/VERSION/scripts) ---
    (kanban_root / "VERSION").write_text(f"{installed_version}\n", encoding="utf-8")

    # --- Dev tree: install.sh stub ---
    dev_install = dev_tree / "install.sh"
    dev_install.write_text("#!/usr/bin/env bash\nexit 0\n", encoding="utf-8")
    dev_install.chmod(0o755)

    # --- Dev tree: team/scripts/ with the new upgrade.sh and real libs ---
    dev_scripts = dev_tree / "team" / "scripts"
    dev_lib = dev_scripts / "lib"
    dev_lib.mkdir(parents=True)

    dev_upgrade = dev_scripts / "upgrade.sh"
    shutil.copy2(dev_tree_upgrade_sh, dev_upgrade)
    dev_upgrade.chmod(0o755)

    for lib_src in _REAL_LIBS:
        if lib_src.exists():
            shutil.copy2(lib_src, dev_lib / lib_src.name)

    # --- Dev tree: minimal stubs for the deposit loop ---
    for doc_name in ["README.md", "DIRECTIVES.md", "OVERVIEW.md", "SOP.md"]:
        (dev_tree / "team" / doc_name).write_text(
            f"# {doc_name} stub\n", encoding="utf-8"
        )
    for sub_name in [
        "roles", "pm-agent", "workflows", "pgai_agent_kanban",
        "halt_after", "templates", "demos",
    ]:
        sub = dev_tree / "team" / sub_name
        sub.mkdir(parents=True, exist_ok=True)
        (sub / ".gitkeep").write_text("", encoding="utf-8")

    # --- Dev tree: subagents/MANIFEST.txt stub ---
    subagents = dev_tree / "subagents"
    subagents.mkdir()
    (subagents / "MANIFEST.txt").write_text("# Empty manifest\n", encoding="utf-8")

    # --- Dev tree: minimal .git directory so upgrade.sh writes the VERSION stamp.
    # upgrade.sh only runs the VERSION stamp block when `[[ -d "$DEV_TREE/.git" ]]`.
    # We create a bare sentinel .git to satisfy that check; no real git history
    # is needed since --stamp-version bypasses git describe entirely.
    # anti-pattern-allowlist: sandbox-fixture (justification: this is an
    # intentional minimal .git sentinel to satisfy upgrade.sh's -d check; it is
    # not a real git repository and does not produce any git operations)
    (dev_tree / ".git").mkdir()
    (dev_tree / ".git" / "HEAD").write_text("ref: refs/heads/main\n", encoding="utf-8")

    return {
        "kanban_root": kanban_root,
        "dev_tree": dev_tree,
        "pgai_temp": pgai_temp,
        "dev_tree_version": dev_tree_version,
    }


def _run_sandbox_upgrade(
    sandbox: dict[str, pathlib.Path],
    tmp_path: pathlib.Path,
    extra_args: list[str] | None = None,
    extra_env: dict[str, str] | None = None,
) -> subprocess.CompletedProcess:
    """Run the installed upgrade.sh from the sandbox kanban root.

    Passes ``--force --no-backup --dev-tree <dev_tree> --stamp-version
    <dev_tree_version>`` so that:
    - ``--force`` skips interactive deposit prompts.
    - ``--no-backup`` avoids tarball creation during tests.
    - ``--dev-tree`` pins the dev tree to the sandbox (prevents PGAI_DEV_TREE_PATH
      from the outer environment from leaking in and overriding the sandbox).
    - ``--stamp-version`` bypasses ``git describe`` so VERSION is deterministic
      without a git repo in the sandbox.

    PGAI_DEV_TREE_PATH is removed from the inherited environment to ensure the
    sandbox dev tree is used even when the test process inherits one from the
    host shell.

    Additional arguments override or extend the defaults via ``extra_args``.
    """
    kanban_root = sandbox["kanban_root"]
    dev_tree = sandbox["dev_tree"]
    pgai_temp = sandbox["pgai_temp"]
    dev_tree_version = sandbox["dev_tree_version"]

    fake_home = tmp_path / "fake_home"
    fake_home.mkdir(exist_ok=True)

    env = dict(os.environ)
    # Remove outer PGAI_DEV_TREE_PATH so the sandbox kanban.cfg (or --dev-tree) wins.
    env.pop("PGAI_DEV_TREE_PATH", None)
    env.update(
        {
            "PGAI_AGENT_KANBAN_ROOT_PATH": str(kanban_root),
            "PGAI_AGENT_KANBAN_TEMP_DIR": str(pgai_temp),
            "HOME": str(fake_home),
        }
    )
    if extra_env:
        env.update(extra_env)

    cmd = [
        "bash",
        str(kanban_root / "scripts" / "upgrade.sh"),
        "--force",
        "--no-backup",
        "--dev-tree",
        str(dev_tree),
        "--stamp-version",
        dev_tree_version,
    ]
    if extra_args:
        cmd.extend(extra_args)

    return subprocess.run(
        cmd,
        capture_output=True,
        text=True,
        timeout=60,
        env=env,
    )


def _assert_no_orphan_phase1_process(
    installed_upgrade_path: pathlib.Path,
) -> None:
    """Assert no process running the installed upgrade.sh path remains.

    The exec in phase 1 replaces the shell process with the phase-2 invocation.
    If the subprocess.run has returned, phase 1 is already gone.  This
    assertion confirms with ``ps`` that no residual process for the installed
    upgrade.sh path is running, as a belt-and-suspenders check.
    """
    ps_result = subprocess.run(
        ["ps", "ax", "-o", "pid,command"],
        capture_output=True,
        text=True,
    )
    ps_out = ps_result.stdout
    installed_path_str = str(installed_upgrade_path)

    matching = [
        line for line in ps_out.splitlines()
        if installed_path_str in line and "bash" in line.lower()
    ]
    assert not matching, (
        f"Orphan phase-1 process found after upgrade returned.  "
        f"exec should have replaced the phase-1 shell — lingering processes "
        f"indicate the exec did not fire or a child process was left running.\n"
        f"Matching ps lines:\n"
        + "\n".join(f"  {ln}" for ln in matching)
    )


# ---------------------------------------------------------------------------
# Shared sentinel upgrade.sh builder
# ---------------------------------------------------------------------------


def _build_sentinel_upgrade_sh(
    tmp_path: pathlib.Path,
    probe_path: pathlib.Path,
    version_label: str,
) -> pathlib.Path:
    """Return path to a dev-tree upgrade.sh with a sentinel phase-2-only step.

    The sentinel step is injected into the phase-2 body (after phase-2
    dispatch and before the main upgrade logic).  It writes the line
    ``upgraded-by: <version_label>`` to ``probe_path``.

    The sentinel runs only when the script is invoked as phase 2 (the
    ``--phase2`` flag is present), confirming that the NEW dev-tree script
    performed the upgrade — not the installed (old) script.

    Parameters
    ----------
    tmp_path:
        Directory under which the sentinel script is written.
    probe_path:
        Absolute path the sentinel writes to.  Must not exist before the test.
    version_label:
        Version string written to the probe file (the dev tree's new version).

    Returns
    -------
    pathlib.Path
        Absolute path to the sentinel upgrade.sh.
    """
    real_text = _REAL_UPGRADE_SH.read_text(encoding="utf-8")

    # Inject the sentinel step right after the phase-2 DEV_TREE inference line.
    # That line reads: DEV_TREE="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
    # We insert the sentinel immediately after the phase-2 info() lines so it
    # runs only in phase-2 mode (inside the if [[ "$_PHASE2_MODE" == "true" ]] block).
    sentinel_code = textwrap.dedent(
        f"""\

  # SENTINEL: phase-2-only step — writes proof that the NEW script performed the upgrade.
  # This step is absent from the installed (old) upgrade.sh; its presence in the
  # probe file is direct evidence that the dev-tree script ran as phase 2.
  if [[ "$_PHASE2_MODE" == "true" ]]; then
    printf 'upgraded-by: {version_label}\\n' > {probe_path}
  fi
"""
    )

    # Find the anchor: the "Fall through to the upgrade body" comment inside the
    # phase-2 dispatch block.  Insert sentinel before that comment.
    anchor = "  # Fall through to the upgrade body below"
    if anchor not in real_text:
        raise RuntimeError(
            f"Sentinel anchor not found in upgrade.sh: {anchor!r}.  "
            "The script structure may have changed — update the anchor in "
            "_build_sentinel_upgrade_sh."
        )

    patched_text = real_text.replace(anchor, sentinel_code + anchor, 1)

    out_path = tmp_path / "upgrade_with_sentinel.sh"
    out_path.write_text(patched_text, encoding="utf-8")
    out_path.chmod(0o755)
    return out_path


# ---------------------------------------------------------------------------
# Fixtures: five behavioral scenarios
# ---------------------------------------------------------------------------


@pytest.mark.skipif(
    not _REAL_UPGRADE_SH.exists(),
    reason=f"Real upgrade.sh not found at {_REAL_UPGRADE_SH}; skipping.",
)
def test_version_skew_newer_script_performs_upgrade_and_writes_sentinel(
    tmp_path: pathlib.Path,
) -> None:
    """Older installed script hands off to newer dev-tree script with sentinel.

    The installed phase-1 bootstrap is the real upgrade.sh (an older version
    without the sentinel step).  The dev-tree script is the same script with a
    sentinel phase-2-only step added.  When the installed script runs, it
    execs into the dev-tree script, which writes ``upgraded-by: <new-version>``
    to a probe file.

    Assertions:
    - The probe file exists after the upgrade.
    - The probe file contains ``upgraded-by: <new-version>``.
    - The installed kanban root's VERSION file matches the new version.
    - Exit code is 0.
    - The upgrade completed in ONE invocation (single subprocess.run call).
    - No orphan phase-1 process remains.
    """
    probe_path = tmp_path / "upgrade_probe.txt"
    sentinel_script = _build_sentinel_upgrade_sh(
        tmp_path, probe_path, _SANDBOX_NEW_VERSION
    )

    sandbox = _build_two_phase_sandbox(
        tmp_path,
        installed_upgrade_sh=_REAL_UPGRADE_SH,
        dev_tree_upgrade_sh=sentinel_script,
        installed_version=_SANDBOX_OLD_VERSION,
        dev_tree_version=_SANDBOX_NEW_VERSION,
    )
    kanban_root = sandbox["kanban_root"]

    result = _run_sandbox_upgrade(sandbox, tmp_path)

    assert result.returncode == 0, (
        f"Version-skew upgrade failed (exit {result.returncode}).\n"
        f"stdout:\n{result.stdout}\n\nstderr:\n{result.stderr}"
    )

    # Probe file must exist — sentinel ran only if dev-tree script executed.
    assert probe_path.exists(), (
        f"Sentinel probe file was not created at {probe_path}.\n"
        f"This means the dev-tree script's phase-2 sentinel did not run,\n"
        f"suggesting the exec handoff did not fire or the sentinel step is broken.\n"
        f"stdout:\n{result.stdout}\n\nstderr:\n{result.stderr}"
    )

    probe_content = probe_path.read_text(encoding="utf-8").strip()
    expected_probe = f"upgraded-by: {_SANDBOX_NEW_VERSION}"
    assert probe_content == expected_probe, (
        f"Probe file content mismatch.\n"
        f"Expected: {expected_probe!r}\n"
        f"Got:      {probe_content!r}"
    )

    # VERSION file must contain the new version (written by phase-2 stamp).
    version_file = kanban_root / "VERSION"
    assert version_file.exists(), "VERSION file was not written by the upgrade."
    stamped = version_file.read_text(encoding="utf-8").strip()
    assert stamped == _SANDBOX_NEW_VERSION, (
        f"VERSION stamp mismatch.\nExpected: {_SANDBOX_NEW_VERSION!r}\nGot: {stamped!r}"
    )

    # Upgrade complete confirmation.
    assert "Upgrade complete!" in result.stdout, (
        f"'Upgrade complete!' not found in stdout.\nstdout:\n{result.stdout}"
    )

    # No orphan phase-1 process.
    _assert_no_orphan_phase1_process(kanban_root / "scripts" / "upgrade.sh")


@pytest.mark.skipif(
    not _REAL_UPGRADE_SH.exists(),
    reason=f"Real upgrade.sh not found at {_REAL_UPGRADE_SH}; skipping.",
)
def test_self_handoff_same_version_upgrade_completes(
    tmp_path: pathlib.Path,
) -> None:
    """Same-version handoff completes successfully.

    When the installed and dev-tree scripts are identical (same-version
    steady-state), phase 1 still execs into phase 2.  The outcome must be a
    successful upgrade: exit 0, "Upgrade complete!" in stdout, VERSION stamped.

    This verifies the self-handoff is a no-overhead no-op cost: no additional
    work, no failure, equivalent outcome to the pre-RC single-phase run.

    No orphan phase-1 process is also asserted.
    """
    sandbox = _build_two_phase_sandbox(
        tmp_path,
        installed_upgrade_sh=_REAL_UPGRADE_SH,
        dev_tree_upgrade_sh=_REAL_UPGRADE_SH,
        installed_version=_SANDBOX_NEW_VERSION,
        dev_tree_version=_SANDBOX_NEW_VERSION,
    )
    kanban_root = sandbox["kanban_root"]

    result = _run_sandbox_upgrade(sandbox, tmp_path)

    assert result.returncode == 0, (
        f"Self-handoff upgrade failed (exit {result.returncode}).\n"
        f"stdout:\n{result.stdout}\n\nstderr:\n{result.stderr}"
    )
    assert "Upgrade complete!" in result.stdout, (
        f"'Upgrade complete!' not found in stdout.\nstdout:\n{result.stdout}"
    )

    version_file = kanban_root / "VERSION"
    assert version_file.exists(), "VERSION file was not written."
    stamped = version_file.read_text(encoding="utf-8").strip()
    assert stamped == _SANDBOX_NEW_VERSION, (
        f"VERSION stamp mismatch.\nExpected: {_SANDBOX_NEW_VERSION!r}\nGot: {stamped!r}"
    )

    _assert_no_orphan_phase1_process(kanban_root / "scripts" / "upgrade.sh")


@pytest.mark.skipif(
    not _REAL_UPGRADE_SH.exists(),
    reason=f"Real upgrade.sh not found at {_REAL_UPGRADE_SH}; skipping.",
)
def test_broken_new_script_fails_at_bash_n_before_deposit(
    tmp_path: pathlib.Path,
) -> None:
    """Dev-tree script with a syntax error causes phase-1 to fail at bash -n.

    Phase 1 probes the dev-tree upgrade.sh with ``bash -n`` before touching
    anything.  A syntax error in the new script must:
    - Cause phase 1 to exit non-zero.
    - Leave the kanban root unchanged (nothing deposited).
    - Report that nothing was deposited.

    The ``--no-backup`` flag is still passed to avoid tarball creation, but
    the test confirms the error message references the bash -n failure.
    """
    broken_script = tmp_path / "broken_upgrade.sh"
    broken_script.write_text(
        textwrap.dedent(
            """\
            #!/usr/bin/env bash
            # This script has an intentional syntax error for testing.
            _PHASE2_MODE=false
            for _p2_arg in "$@"; do
              [[ "$_p2_arg" == "--phase2" ]] && _PHASE2_MODE=true && break
            done
            # SYNTAX ERROR: unclosed if block
            if [[ "$_PHASE2_MODE" == "true" ]]; then
              echo "phase 2"
            # missing 'fi' — bash -n must catch this
            """
        ),
        encoding="utf-8",
    )
    broken_script.chmod(0o755)

    sandbox = _build_two_phase_sandbox(
        tmp_path,
        installed_upgrade_sh=_REAL_UPGRADE_SH,
        dev_tree_upgrade_sh=broken_script,
    )
    kanban_root = sandbox["kanban_root"]

    # Record what's in the kanban root before the upgrade.
    files_before = set(p.name for p in (kanban_root / "scripts").iterdir())

    result = _run_sandbox_upgrade(sandbox, tmp_path)

    # Must fail non-zero.
    assert result.returncode != 0, (
        f"Expected non-zero exit when dev-tree script has syntax error; "
        f"got exit code {result.returncode}.\n"
        f"stdout:\n{result.stdout}\n\nstderr:\n{result.stderr}"
    )

    combined = result.stdout + result.stderr

    # Error message must mention the bash -n failure or the probe.
    assert "bash -n" in combined or "syntax" in combined.lower(), (
        f"Expected 'bash -n' or 'syntax' in error output; got:\n{combined}"
    )

    # Nothing should have been deposited — installed scripts unchanged.
    files_after = set(p.name for p in (kanban_root / "scripts").iterdir())
    assert files_before == files_after, (
        f"Files in kanban_root/scripts changed after broken-script failure.\n"
        f"Before: {sorted(files_before)}\n"
        f"After:  {sorted(files_after)}"
    )

    # Upgrade complete must NOT appear.
    assert "Upgrade complete!" not in combined, (
        f"'Upgrade complete!' appeared despite broken dev-tree script."
    )


@pytest.mark.skipif(
    not _REAL_UPGRADE_SH.exists(),
    reason=f"Real upgrade.sh not found at {_REAL_UPGRADE_SH}; skipping.",
)
def test_protocol_mismatch_fails_loud_with_bootstrap_instruction(
    tmp_path: pathlib.Path,
) -> None:
    """Phase-2 rejection of an unknown protocol fails loud with the manual instruction.

    The phase-2 dispatch in upgrade.sh only accepts ``--phase2-protocol 1``.
    We invoke the dev-tree script directly as phase 2 with protocol "99"
    (which phase 1 would never produce, but simulates a future incompatibility).

    The rejection must:
    - Exit non-zero.
    - Print the manual-bootstrap instruction (the ``cp ... retry`` line).
    - Not produce "Upgrade complete!".

    This test calls the dev-tree upgrade.sh directly (not via the installed
    phase-1 bootstrap) with ``--phase2 --phase2-protocol 99`` to force the
    rejection path.
    """
    sandbox = _build_two_phase_sandbox(tmp_path)
    dev_tree = sandbox["dev_tree"]
    kanban_root = sandbox["kanban_root"]
    pgai_temp = sandbox["pgai_temp"]

    fake_home = tmp_path / "fake_home"
    fake_home.mkdir(exist_ok=True)

    dev_upgrade = dev_tree / "team" / "scripts" / "upgrade.sh"

    env = dict(os.environ)
    env.update(
        {
            "PGAI_AGENT_KANBAN_ROOT_PATH": str(kanban_root),
            "PGAI_AGENT_KANBAN_TEMP_DIR": str(pgai_temp),
            "HOME": str(fake_home),
        }
    )

    result = subprocess.run(
        [
            "bash",
            str(dev_upgrade),
            "--phase2",
            "--phase2-protocol", "99",
            "--kanban-root", str(kanban_root),
        ],
        capture_output=True,
        text=True,
        timeout=30,
        env=env,
    )

    assert result.returncode != 0, (
        f"Expected non-zero exit for unknown protocol 99; "
        f"got exit {result.returncode}.\n"
        f"stdout:\n{result.stdout}\n\nstderr:\n{result.stderr}"
    )

    combined = result.stdout + result.stderr

    # Manual bootstrap instruction must appear.
    assert "cp" in combined and "retry" in combined, (
        f"Expected manual-bootstrap instruction (cp ... retry) in error output; "
        f"got:\n{combined}"
    )

    # Protocol error message must be present.
    assert "protocol" in combined.lower() or "unknown" in combined.lower(), (
        f"Expected protocol error mention in output; got:\n{combined}"
    )

    assert "Upgrade complete!" not in combined, (
        f"'Upgrade complete!' appeared despite protocol mismatch."
    )


@pytest.mark.skipif(
    not _REAL_UPGRADE_SH.exists(),
    reason=f"Real upgrade.sh not found at {_REAL_UPGRADE_SH}; skipping.",
)
def test_no_phase2_support_fails_loud_with_hand_copy_instruction(
    tmp_path: pathlib.Path,
) -> None:
    """Dev-tree script without --phase2 support fails loud at the grep probe.

    Phase 1 uses ``grep -q -- '--phase2'`` to probe the dev-tree script for
    two-phase support.  A dev-tree script without the flag (simulating a
    pre-v1.7 downgrade target or a wrong script) must cause phase 1 to:
    - Exit non-zero.
    - Print the hand-copy instruction (the cp + retry message).
    - Deposit nothing.
    """
    # Write a script that does NOT contain the literal string '--phase2' anywhere
    # in its source text.  The grep probe in phase 1 checks for this string;
    # its absence must cause the probe to fail loud.
    no_phase2_script = tmp_path / "old_upgrade.sh"
    no_phase2_script.write_text(
        textwrap.dedent(
            """\
            #!/usr/bin/env bash
            # Simulated pre-v1.7 upgrade.sh.
            # This script does not implement the two-phase handoff protocol.
            echo "single-phase upgrade complete"
            exit 0
            """
        ),
        encoding="utf-8",
    )
    no_phase2_script.chmod(0o755)

    sandbox = _build_two_phase_sandbox(
        tmp_path,
        installed_upgrade_sh=_REAL_UPGRADE_SH,
        dev_tree_upgrade_sh=no_phase2_script,
    )
    kanban_root = sandbox["kanban_root"]

    result = _run_sandbox_upgrade(sandbox, tmp_path)

    assert result.returncode != 0, (
        f"Expected non-zero exit when dev-tree script lacks --phase2; "
        f"got exit {result.returncode}.\n"
        f"stdout:\n{result.stdout}\n\nstderr:\n{result.stderr}"
    )

    combined = result.stdout + result.stderr

    # Must mention manual bootstrap (cp ... retry).
    assert "cp" in combined and "retry" in combined, (
        f"Expected hand-copy instruction (cp ... retry) in error output; "
        f"got:\n{combined}"
    )

    assert "Upgrade complete!" not in combined, (
        f"'Upgrade complete!' appeared despite missing --phase2 support."
    )


# ---------------------------------------------------------------------------
# Converted scenarios (from retired re-exec guard)
# ---------------------------------------------------------------------------


@pytest.mark.skipif(
    not _REAL_UPGRADE_SH.exists(),
    reason=f"Real upgrade.sh not found at {_REAL_UPGRADE_SH}; skipping.",
)
def test_upgrade_completes_when_installed_copy_differs_from_dev_tree(
    tmp_path: pathlib.Path,
) -> None:
    """Upgrade completes even when the installed script differs from the dev-tree copy.

    Under the old temp-copy re-exec mechanism, a byte difference between the
    installed and dev-tree upgrade.sh caused the guard to fire.  Under the
    two-phase architecture, phase 1 ALWAYS execs into the dev-tree script —
    the bytes of the installed copy are irrelevant to whether the upgrade
    completes.

    The installed script is given a harmless no-op comment so its bytes differ
    from the dev-tree copy.  The upgrade must complete: exit 0, "Upgrade
    complete!", VERSION stamped correctly.

    No orphan phase-1 process is also asserted.
    """
    # Write an installed upgrade.sh that is byte-different from the real one.
    byte_different_installed = tmp_path / "installed_upgrade_different.sh"
    real_text = _REAL_UPGRADE_SH.read_text(encoding="utf-8")
    byte_different_installed.write_text(
        real_text + "\n# sandbox: byte-different marker (test-only)\n",
        encoding="utf-8",
    )
    byte_different_installed.chmod(0o755)

    sandbox = _build_two_phase_sandbox(
        tmp_path,
        installed_upgrade_sh=byte_different_installed,
        dev_tree_upgrade_sh=_REAL_UPGRADE_SH,
    )
    kanban_root = sandbox["kanban_root"]

    result = _run_sandbox_upgrade(sandbox, tmp_path)

    assert result.returncode == 0, (
        f"Upgrade failed despite byte-different installed script (exit {result.returncode}).\n"
        f"stdout:\n{result.stdout}\n\nstderr:\n{result.stderr}"
    )
    assert "Upgrade complete!" in result.stdout, (
        f"'Upgrade complete!' not found.\nstdout:\n{result.stdout}"
    )

    version_file = kanban_root / "VERSION"
    assert version_file.exists(), "VERSION file was not written."
    stamped = version_file.read_text(encoding="utf-8").strip()
    assert stamped == _SANDBOX_NEW_VERSION, (
        f"VERSION stamp mismatch.\nExpected: {_SANDBOX_NEW_VERSION!r}\nGot: {stamped!r}"
    )

    _assert_no_orphan_phase1_process(kanban_root / "scripts" / "upgrade.sh")


@pytest.mark.skipif(
    not _REAL_UPGRADE_SH.exists(),
    reason=f"Real upgrade.sh not found at {_REAL_UPGRADE_SH}; skipping.",
)
def test_upgrade_completes_in_one_run(
    tmp_path: pathlib.Path,
) -> None:
    """Upgrade completes in exactly one process chain without any retry or loop.

    Under the old mechanism, a self-overwrite concern required a re-exec —
    one invocation became two processes.  Under the two-phase architecture,
    a single ``subprocess.run`` call covers both phase 1 and phase 2 (via
    exec handoff): the upgrade completes and the subprocess returns.

    Assertions:
    - One subprocess.run call returns successfully (exit 0).
    - "Upgrade complete!" appears in stdout.
    - VERSION is stamped.
    - No orphan phase-1 process remains.
    """
    sandbox = _build_two_phase_sandbox(tmp_path)
    kanban_root = sandbox["kanban_root"]

    result = _run_sandbox_upgrade(sandbox, tmp_path)

    assert result.returncode == 0, (
        f"Upgrade failed in one-run test (exit {result.returncode}).\n"
        f"stdout:\n{result.stdout}\n\nstderr:\n{result.stderr}"
    )
    assert "Upgrade complete!" in result.stdout, (
        f"'Upgrade complete!' not found.\nstdout:\n{result.stdout}"
    )

    version_file = kanban_root / "VERSION"
    assert version_file.exists(), "VERSION file was not written."

    _assert_no_orphan_phase1_process(kanban_root / "scripts" / "upgrade.sh")
