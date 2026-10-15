"""
test_wake_guard_wiring.py
==========================
Behavioral fixtures for wiring the git remote-boundary guard
(scripts/lib/git_boundary_guard.sh, scripts/wake/git-guard/git) into wake
dispatch (scripts/lib/wake_guard_wiring.sh's wd_install_git_boundary_guard,
called from scripts/lib/wake_driver_invoke.sh's wd_invoke_task).

The guard primitive itself (git_boundary_guard_enforce) is exercised in
test_git_boundary_guard.py; this suite exercises the wiring that turns it
into an active boundary for every wake-dispatched agent: the PATH
prepend, the two exported variables the guard reads, and the fail-clean
behavior when the shim directory is missing.

Acceptance criteria exercised:

  (1) For a dispatched non-CM role, the shim directory is the first entry
      on the exported PATH, and both PGAI_AGENT_ROLE and
      PGAI_GIT_BOUNDARY_GUARD_EVIDENCE_FILE are exported with non-empty
      values. PGAI_GIT_LOCAL_ONLY is exported too (as "1"), so the
      version lookup and tag probe that honour it answer from local refs
      instead of reaching for origin -- a reach the guard would refuse
      anyway, spending an evidence record on a crossing nobody attempted.
      The flag is exported whether or not the shim directory is present:
      it is about not reaching for the remote, not about the guard.

  (2) For a dispatched CM role, the exported PATH does not contain the
      shim directory — CM's git resolution, and therefore every CM
      release operation, is untouched — and PGAI_GIT_LOCAL_ONLY is left
      unset: CM's fetches are the ones the boundary exists to let through.

  (3) The exported evidence file path resolves inside the task's own
      logs/ directory.

  (4) With the shim directory absent, dispatch still proceeds (the
      function returns 0) and a warning naming the missing directory is
      emitted through log() — the same function that writes the wake log.

  (5) wake_guard_wiring.sh's own header names each exported variable
      (PGAI_AGENT_ROLE, PGAI_GIT_BOUNDARY_GUARD_EVIDENCE_FILE,
      PGAI_GIT_LOCAL_ONLY) and states where the evidence file is written;
      wake_driver_invoke.sh's call site still activates the guard by
      calling wd_install_git_boundary_guard.

wd_install_git_boundary_guard has no module-level side effects (it is a
single function definition), so this suite sources
scripts/lib/wake_guard_wiring.sh directly and calls the function inside a
stubbed bash harness that supplies log() and the caller-frame locals the
function reads by dynamic scoping (SUBAGENT, task_log_dir, task_id,
SCRIPT_DIR) — the same "source the real lib, call the function by name"
technique test_wake_dispatch_role_assignment.py uses for
wake_readme_meta.sh's README-parsing helpers, rather than re-deriving the
mechanism's logic as a stand-in for it.

Naming: function names describe behavior under test, never bug IDs or
task IDs (see roles/TESTER.md "Test Authoring Guidelines").
"""

from __future__ import annotations

import os
import pathlib
import subprocess
import textwrap

import pytest

# ---------------------------------------------------------------------------
# Paths (relative to team/, which is the CWD when run via run-unit-tests.sh)
# ---------------------------------------------------------------------------
_WAKE_GUARD_WIRING_LIB = pathlib.Path("scripts/lib/wake_guard_wiring.sh")
_WAKE_DRIVER_INVOKE = pathlib.Path("scripts/lib/wake_driver_invoke.sh")


def _run_guard_wiring(
    tmp_path: pathlib.Path,
    *,
    subagent: str,
    shim_dir_exists: bool,
    shim_file_present: bool = True,
    base_path: str = "/usr/bin:/bin",
    preset_git_guard_real_git: str | None = None,
) -> tuple[subprocess.CompletedProcess, pathlib.Path]:
    """Source the real wake_guard_wiring.sh and call
    wd_install_git_boundary_guard under bash.

    Sets up a synthetic task_log_dir under tmp_path and a script_dir whose
    sibling "git-guard" directory is created only when shim_dir_exists is
    true (mirroring scripts/wake/git-guard living next to scripts/wake's
    own wake script, which is what SCRIPT_DIR resolves to at runtime). When
    the directory is created, an executable "git" file is placed inside it
    unless shim_file_present is explicitly False -- the latter reproduces
    an install that deposited the directory but never placed the shim
    executable inside it, which the wiring must treat exactly like a
    missing directory rather than treating the directory's mere presence
    as sufficient.

    GIT_GUARD_REAL_GIT is explicitly unset before the call (this harness's
    own subprocess inherits the calling process's full environment, which
    must never leak a value here) unless preset_git_guard_real_git names
    one, in which case that value is exported first -- proving the
    function respects an existing value rather than pinning over it.
    PGAI_GIT_LOCAL_ONLY is unset before the call for the same reason: a
    pytest session launched from inside a non-CM agent wake already
    carries the flag this function exports, and "absent for cm" is only
    provable from an environment that did not start with it.

    Returns the completed process and the task_log_dir path.
    """
    lib_path = _WAKE_GUARD_WIRING_LIB.resolve()

    script_dir = tmp_path / "wake"
    script_dir.mkdir()
    if shim_dir_exists:
        shim_dir = script_dir / "git-guard"
        shim_dir.mkdir()
        if shim_file_present:
            shim_file = shim_dir / "git"
            shim_file.write_text("#!/usr/bin/env bash\nexit 0\n", encoding="utf-8")
            shim_file.chmod(0o755)

    task_log_dir = tmp_path / "task" / "logs"
    task_log_dir.mkdir(parents=True)

    preset_line = (
        f'export GIT_GUARD_REAL_GIT="{preset_git_guard_real_git}"'
        if preset_git_guard_real_git is not None
        else "unset GIT_GUARD_REAL_GIT"
    )

    harness = textwrap.dedent(f"""\
        #!/usr/bin/env bash
        set -uo pipefail
        LOG_CALLS="{tmp_path / 'log_calls.txt'}"
        log() {{ echo "$*" >> "$LOG_CALLS"; }}
        # shellcheck source=/dev/null
        source "{lib_path}"
        SCRIPT_DIR="{script_dir}"
        SUBAGENT="{subagent}"
        task_id="SYNTH-000-fixture"
        task_log_dir="{task_log_dir}"
        PATH="{base_path}"
        {preset_line}
        unset PGAI_GIT_LOCAL_ONLY

        wd_install_git_boundary_guard

        echo "PATH=[${{PATH}}]"
        echo "PGAI_AGENT_ROLE=[${{PGAI_AGENT_ROLE:-}}]"
        echo "PGAI_GIT_BOUNDARY_GUARD_EVIDENCE_FILE=[${{PGAI_GIT_BOUNDARY_GUARD_EVIDENCE_FILE:-}}]"
        echo "GIT_GUARD_REAL_GIT=[${{GIT_GUARD_REAL_GIT:-}}]"
        echo "PGAI_GIT_LOCAL_ONLY=[${{PGAI_GIT_LOCAL_ONLY:-}}]"
    """)

    result = subprocess.run(
        ["bash", "-c", harness], capture_output=True, text=True, timeout=15
    )
    return result, task_log_dir


def _log_calls(tmp_path: pathlib.Path) -> str:
    calls_file = tmp_path / "log_calls.txt"
    return calls_file.read_text(encoding="utf-8") if calls_file.exists() else ""


# ---------------------------------------------------------------------------
# (1) Non-CM role: shim directory first on PATH; both variables non-empty.
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("subagent", ["coder", "writer", "tester", "po", "pm"])
def test_non_cm_role_gets_shim_first_on_path_and_both_variables_exported(
    tmp_path: pathlib.Path, subagent: str
) -> None:
    result, task_log_dir = _run_guard_wiring(
        tmp_path, subagent=subagent, shim_dir_exists=True
    )
    assert result.returncode == 0, result.stderr
    stdout = result.stdout

    expected_shim_dir = str(tmp_path / "wake" / "git-guard")
    assert f"PATH=[{expected_shim_dir}:" in stdout, (
        f"shim directory must be the first PATH entry for role '{subagent}'.\n{stdout}"
    )
    assert f"PGAI_AGENT_ROLE=[{subagent}]" in stdout, stdout
    assert "PGAI_GIT_BOUNDARY_GUARD_EVIDENCE_FILE=[]" not in stdout, (
        f"evidence file variable must be non-empty for role '{subagent}'.\n{stdout}"
    )
    assert "PGAI_GIT_LOCAL_ONLY=[1]" in stdout, (
        f"PGAI_GIT_LOCAL_ONLY must be exported as '1' for the non-CM role "
        f"'{subagent}', so pp_last_released_version and _disc_git_tag_exists "
        f"answer from local refs instead of reaching for origin.\n{stdout}"
    )
    assert _log_calls(tmp_path) == "", (
        "no warning expected when the shim directory exists"
    )


# ---------------------------------------------------------------------------
# (1b) Non-CM role: GIT_GUARD_REAL_GIT is pinned once, before the shim
#      directory is added to PATH, and an operator/caller-preset value is
#      never overwritten.
# ---------------------------------------------------------------------------


def test_non_cm_role_pins_git_guard_real_git_when_unset(tmp_path: pathlib.Path) -> None:
    """With GIT_GUARD_REAL_GIT unset on entry, the wiring resolves it
    against the PATH that was in effect BEFORE the shim directory was
    prepended (base_path here), so the pin names a real, executable git
    binary rather than the shim itself."""
    base_path = "/usr/bin:/bin"
    result, _ = _run_guard_wiring(
        tmp_path, subagent="coder", shim_dir_exists=True, base_path=base_path
    )
    assert result.returncode == 0, result.stderr
    stdout = result.stdout

    pin_line = [
        line for line in stdout.splitlines() if line.startswith("GIT_GUARD_REAL_GIT=[")
    ]
    assert pin_line, stdout
    pinned_path = pin_line[0].split("[", 1)[1].rstrip("]")
    assert pinned_path, f"expected a non-empty GIT_GUARD_REAL_GIT pin.\n{stdout}"
    assert os.path.isfile(pinned_path) and os.access(pinned_path, os.X_OK), (
        f"pinned GIT_GUARD_REAL_GIT ({pinned_path!r}) must be an executable file"
    )
    expected_shim_dir = str(tmp_path / "wake" / "git-guard")
    assert not pinned_path.startswith(expected_shim_dir), (
        f"the pin must never resolve to the guard's own shim directory.\n{stdout}"
    )


def test_non_cm_role_never_overwrites_a_preset_git_guard_real_git(
    tmp_path: pathlib.Path,
) -> None:
    """An already-exported GIT_GUARD_REAL_GIT (an operator's own override,
    or a value a previous stage of the same dispatch already set) is left
    exactly as found -- the wiring pin only fills a gap, it never wins
    over an explicit value."""
    result, _ = _run_guard_wiring(
        tmp_path,
        subagent="coder",
        shim_dir_exists=True,
        preset_git_guard_real_git="/not/a/real/path/but/preserved",
    )
    assert result.returncode == 0, result.stderr
    assert "GIT_GUARD_REAL_GIT=[/not/a/real/path/but/preserved]" in result.stdout, (
        result.stdout
    )


def test_missing_shim_directory_sets_no_git_guard_real_git_pin(
    tmp_path: pathlib.Path,
) -> None:
    """No shim on PATH means nothing for a pin to protect -- the wiring
    must not set GIT_GUARD_REAL_GIT at all when the shim directory is
    absent, matching the existing fail-clean (not fail-closed) contract
    for a missing shim directory."""
    base_path = "/usr/bin:/bin"
    result, _ = _run_guard_wiring(
        tmp_path, subagent="coder", shim_dir_exists=False, base_path=base_path
    )
    assert result.returncode == 0, result.stderr
    assert "GIT_GUARD_REAL_GIT=[]" in result.stdout, result.stdout


# ---------------------------------------------------------------------------
# (2) CM role: PATH does not gain the shim directory.
# ---------------------------------------------------------------------------


def test_cm_role_path_never_gains_shim_directory(tmp_path: pathlib.Path) -> None:
    base_path = "/usr/bin:/bin"
    result, _ = _run_guard_wiring(
        tmp_path, subagent="cm", shim_dir_exists=True, base_path=base_path
    )
    assert result.returncode == 0, result.stderr
    stdout = result.stdout

    assert f"PATH=[{base_path}]" in stdout, (
        f"CM's PATH must be untouched by the guard wiring.\n{stdout}"
    )
    shim_dir = str(tmp_path / "wake" / "git-guard")
    assert shim_dir not in stdout.split("PATH=[")[1].split("]")[0], (
        f"CM's exported PATH must never contain the shim directory.\n{stdout}"
    )
    # The role signal is still exported (positive and explicit for every
    # role, including cm) -- it is what lets the guard's own role=="cm"
    # pass-through path do the work, rather than a PATH-based exemption.
    assert "PGAI_AGENT_ROLE=[cm]" in stdout, stdout
    # CM never gets a GIT_GUARD_REAL_GIT pin either -- there is no shim on
    # CM's PATH for one to protect, and CM's git resolution stays
    # byte-identical to an install without this guard.
    assert "GIT_GUARD_REAL_GIT=[]" in stdout, stdout
    # And CM never receives the local-only flag: its fetches are exactly
    # the remote operations the boundary exists to let through, and a
    # suppressed fetch would leave CM with a stale view of origin's tags.
    assert "PGAI_GIT_LOCAL_ONLY=[]" in stdout, (
        f"PGAI_GIT_LOCAL_ONLY must NOT be exported for the cm role.\n{stdout}"
    )


# ---------------------------------------------------------------------------
# (3) Evidence file resolves inside the task's own logs/ directory.
# ---------------------------------------------------------------------------


def test_evidence_file_resolves_inside_task_logs_directory(
    tmp_path: pathlib.Path,
) -> None:
    result, task_log_dir = _run_guard_wiring(
        tmp_path, subagent="coder", shim_dir_exists=True
    )
    assert result.returncode == 0, result.stderr
    stdout = result.stdout

    evidence_line = [
        line
        for line in stdout.splitlines()
        if line.startswith("PGAI_GIT_BOUNDARY_GUARD_EVIDENCE_FILE=[")
    ]
    assert evidence_line, stdout
    evidence_path = evidence_line[0].split("[", 1)[1].rstrip("]")
    assert evidence_path.startswith(str(task_log_dir) + "/"), (
        f"evidence file must live under the task's logs/ directory "
        f"({task_log_dir}), got: {evidence_path}"
    )


# ---------------------------------------------------------------------------
# (4) Missing shim directory: dispatch proceeds, warning names the path.
# ---------------------------------------------------------------------------


def test_missing_shim_directory_logs_warning_and_dispatch_still_proceeds(
    tmp_path: pathlib.Path,
) -> None:
    base_path = "/usr/bin:/bin"
    result, _ = _run_guard_wiring(
        tmp_path, subagent="coder", shim_dir_exists=False, base_path=base_path
    )
    assert result.returncode == 0, (
        f"dispatch must still proceed when the shim directory is missing.\n{result.stderr}"
    )
    assert f"PATH=[{base_path}]" in result.stdout, (
        "PATH must be unchanged when the shim directory does not exist.\n"
        f"{result.stdout}"
    )
    # The local-only flag does not depend on the shim: it stops a non-CM
    # dispatch reaching for origin at all, guard or no guard.
    assert "PGAI_GIT_LOCAL_ONLY=[1]" in result.stdout, (
        "PGAI_GIT_LOCAL_ONLY must still be exported for a non-CM role when "
        f"the shim directory is missing.\n{result.stdout}"
    )
    calls = _log_calls(tmp_path)
    expected_missing_dir = str(tmp_path / "wake" / "git-guard")
    assert "WARNING" in calls, f"expected a warning; log calls were: {calls!r}"
    assert expected_missing_dir in calls, (
        f"warning must name the missing shim directory ({expected_missing_dir}); "
        f"got: {calls!r}"
    )


# ---------------------------------------------------------------------------
# (4b) Shim directory present but empty: the same fail-clean warning fires.
#      A directory that exists but never received the "git" shim file must
#      not look identical to a healthy cycle: PATH must not gain the
#      (useless) empty directory, and the WARNING must fire exactly as it
#      does for a directory that does not exist at all.
# ---------------------------------------------------------------------------


def test_empty_shim_directory_logs_warning_and_does_not_extend_path(
    tmp_path: pathlib.Path,
) -> None:
    base_path = "/usr/bin:/bin"
    result, _ = _run_guard_wiring(
        tmp_path,
        subagent="coder",
        shim_dir_exists=True,
        shim_file_present=False,
        base_path=base_path,
    )
    assert result.returncode == 0, (
        f"dispatch must still proceed when the shim directory is empty.\n{result.stderr}"
    )
    expected_shim_dir = str(tmp_path / "wake" / "git-guard")
    assert f"PATH=[{base_path}]" in result.stdout, (
        "PATH must NOT gain the shim directory when it holds no shim "
        f"executable -- an empty directory must not look like an active "
        f"guard.\n{result.stdout}"
    )
    assert expected_shim_dir not in result.stdout.split("PATH=[")[1].split("]")[0], (
        f"an empty shim directory must never reach PATH.\n{result.stdout}"
    )
    assert "GIT_GUARD_REAL_GIT=[]" in result.stdout, (
        f"no pin is set when there is no shim to protect.\n{result.stdout}"
    )
    calls = _log_calls(tmp_path)
    assert "WARNING" in calls, (
        f"a directory with no shim executable inside it must warn exactly "
        f"like a missing directory does; log calls were: {calls!r}"
    )
    assert expected_shim_dir in calls, (
        f"warning must name the shim directory ({expected_shim_dir}) even "
        f"when the directory itself exists but is empty; got: {calls!r}"
    )


# ---------------------------------------------------------------------------
# (5) Structural: wake_guard_wiring.sh's own header names both exported
#     variables and the evidence file location; wake_driver_invoke.sh
#     still calls the mechanism.
# ---------------------------------------------------------------------------


def test_header_comment_names_exported_variables_and_evidence_location() -> None:
    lib_content = _WAKE_GUARD_WIRING_LIB.read_text(encoding="utf-8")
    assert "PGAI_AGENT_ROLE" in lib_content, (
        "wake_guard_wiring.sh must document the PGAI_AGENT_ROLE export."
    )
    assert "PGAI_GIT_BOUNDARY_GUARD_EVIDENCE_FILE" in lib_content, (
        "wake_guard_wiring.sh must document the "
        "PGAI_GIT_BOUNDARY_GUARD_EVIDENCE_FILE export."
    )
    assert "logs/" in lib_content or "task_log_dir" in lib_content, (
        "wake_guard_wiring.sh must state where the evidence file is "
        "written (under the task's own logs/ directory)."
    )
    assert "PGAI_GIT_LOCAL_ONLY" in lib_content, (
        "wake_guard_wiring.sh must document the PGAI_GIT_LOCAL_ONLY export "
        "(set for every non-cm dispatch, never for cm)."
    )

    invoke_content = _WAKE_DRIVER_INVOKE.read_text(encoding="utf-8")
    assert "wd_install_git_boundary_guard" in invoke_content, (
        "wake_driver_invoke.sh must still call wd_install_git_boundary_guard "
        "to activate the guard for each dispatch."
    )
