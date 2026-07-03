"""
test_shell_syntax_gate.py
=========================
Enforces the bash -n syntax gate over every tracked shell script in the repo,
with explicit coverage assertions for upgrade.sh and install.sh.

WHY THIS EXISTS
---------------
BUG-0013: upgrade.sh shipped syntactically broken because the bash -n gate
did not enumerate it.  These front-door scripts (install.sh and upgrade.sh)
are the first thing a new user runs; a broken installer is the worst possible
first impression.

HOW THE GATE WORKS
------------------
Discovery: ``git ls-files "*.sh"`` from the repo root lists every tracked
shell script.  New scripts are covered automatically — no list to maintain.

Coverage assertion: the test explicitly asserts that install.sh and upgrade.sh
are in the discovered set.  A future refactor that accidentally drops either
file from git tracking will cause this assertion to fail rather than silently
losing gate coverage.

Negative proof: a deliberately broken temp file (unbalanced ``fi``) is passed
to ``bash -n`` to demonstrate the gate *detects* broken syntax, not merely
that the real files happen to parse cleanly.

TEST NAMING CONVENTION (SOP.md Anti-pattern 6)
-----------------------------------------------
All test function names describe behavior, not the bug ID that prompted them.
"""

from __future__ import annotations

import pathlib
import subprocess
import tempfile

import pytest

from tests.unit.shell_harness import run_bash


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _repo_root() -> pathlib.Path:
    """Return the absolute path to the repository root.

    The test suite runs with cwd=team/ (set by run-unit-tests.sh), so the
    repo root is one level up.  We resolve via git to be resilient to
    symlinks and unusual mount points.
    """
    result = subprocess.run(
        ["git", "rev-parse", "--show-toplevel"],
        capture_output=True,
        text=True,
        check=True,
    )
    return pathlib.Path(result.stdout.strip())


def _tracked_shell_files() -> list[pathlib.Path]:
    """Return a list of tracked *.sh files in the repository (absolute paths).

    Uses ``git ls-files "*.sh"`` so the set is defined by the index — the same
    set the gate enforces.  Paths are returned as absolute pathlib.Path objects
    so callers do not need to know the working directory.
    """
    root = _repo_root()
    result = subprocess.run(
        ["git", "ls-files", "*.sh"],
        capture_output=True,
        text=True,
        check=True,
        cwd=str(root),
    )
    return [root / line.strip() for line in result.stdout.splitlines() if line.strip()]


# ---------------------------------------------------------------------------
# Gate coverage assertions
# ---------------------------------------------------------------------------


def test_upgrade_sh_is_in_tracked_shell_file_set() -> None:
    """upgrade.sh must be a tracked shell file so the bash -n gate covers it.

    This assertion exists so a future refactor that accidentally drops upgrade.sh
    from git tracking is caught immediately rather than silently losing coverage.
    The negative consequence of missing this: a broken upgrade.sh ships without
    detection, as happened before BUG-0013.
    """
    root = _repo_root()
    tracked = _tracked_shell_files()
    tracked_relative = {f.relative_to(root) for f in tracked}
    # upgrade.sh lives under team/scripts/
    assert pathlib.Path("team/scripts/upgrade.sh") in tracked_relative, (
        "team/scripts/upgrade.sh is not tracked by git — bash -n gate has no coverage."
        " Either restore the file to git tracking or update this assertion."
    )


def test_install_sh_is_in_tracked_shell_file_set() -> None:
    """install.sh must be a tracked shell file so the bash -n gate covers it.

    Same rationale as the upgrade.sh assertion above.  install.sh lives at
    repo root; it is the first script a new user runs.
    """
    root = _repo_root()
    tracked = _tracked_shell_files()
    tracked_relative = {f.relative_to(root) for f in tracked}
    assert pathlib.Path("install.sh") in tracked_relative, (
        "install.sh is not tracked by git — bash -n gate has no coverage."
        " Either restore the file to git tracking or update this assertion."
    )


# ---------------------------------------------------------------------------
# Negative proof
# ---------------------------------------------------------------------------


def test_broken_shell_syntax_fails_gate(tmp_path: pathlib.Path) -> None:
    """bash -n exits nonzero on a file with a syntax error.

    This is the negative proof: it demonstrates the gate mechanism is actually
    capable of catching broken syntax.  Without this, adding upgrade.sh to a
    list and running bash -n on it would be unverified — we'd only know the
    real file happens to parse, not that the gate would catch a broken version.

    The broken file contains an unbalanced ``fi`` (a closing keyword with no
    matching ``if``), which bash -n detects as a syntax error.
    """
    broken_script = tmp_path / "broken_upgrade_sh"
    broken_script.write_text(
        "#!/usr/bin/env bash\n"
        "# Deliberately broken script for negative-proof gate test.\n"
        "echo starting\n"
        "fi\n"  # unbalanced fi — no matching if
        "echo done\n",
        encoding="utf-8",
    )
    result = run_bash(
        tmp_path,
        f"bash -n {broken_script}",
    )
    assert result.returncode != 0, (
        "bash -n did not detect the syntax error in the broken script."
        " The gate mechanism is not working as expected."
    )


# ---------------------------------------------------------------------------
# Positive proof: upgrade.sh and install.sh must pass
# ---------------------------------------------------------------------------


def test_upgrade_sh_passes_syntax_gate(tmp_path: pathlib.Path) -> None:
    """bash -n exits 0 on the real upgrade.sh.

    Paired with test_broken_shell_syntax_fails_gate to confirm:
    (a) the gate CAN detect broken syntax (negative proof above), and
    (b) the real upgrade.sh is currently valid (this test).
    """
    root = _repo_root()
    upgrade_sh = root / "team" / "scripts" / "upgrade.sh"
    result = run_bash(
        tmp_path,
        f"bash -n {upgrade_sh}",
    )
    assert result.returncode == 0, (
        f"bash -n failed on {upgrade_sh}:\n{result.stderr}"
    )


def test_install_sh_passes_syntax_gate(tmp_path: pathlib.Path) -> None:
    """bash -n exits 0 on the real install.sh."""
    root = _repo_root()
    install_sh = root / "install.sh"
    result = run_bash(
        tmp_path,
        f"bash -n {install_sh}",
    )
    assert result.returncode == 0, (
        f"bash -n failed on {install_sh}:\n{result.stderr}"
    )


# ---------------------------------------------------------------------------
# Gate: all tracked shell files must pass bash -n
# ---------------------------------------------------------------------------


def test_all_tracked_shell_files_parse(tmp_path: pathlib.Path) -> None:
    """Every tracked *.sh file in the repo must pass bash -n syntax checking.

    Discovery is by ``git ls-files "*.sh"`` from the repo root, so every
    future shell script committed to the repo is automatically covered —
    no list to maintain.

    Failures are collected and reported together so a single run surfaces all
    broken files rather than stopping at the first.
    """
    # anti-pattern-allowlist: 1 (justification: structural invariant — every
    # tracked shell file must parse; adding a syntactically broken file is a
    # regression, not a legitimate new item that the assertion should tolerate.
    # This is precisely the class of "every X must Y" assertion that is valid
    # to assert universally over all items returned by the scan.)
    files = _tracked_shell_files()
    assert len(files) > 0, "git ls-files returned no *.sh files — discovery broken"

    failures: list[str] = []
    for sh_file in files:
        result = run_bash(
            tmp_path,
            f"bash -n {sh_file}",
        )
        if result.returncode != 0:
            failures.append(f"  {sh_file}: bash -n FAILED\n    {result.stderr.strip()}")

    assert not failures, (
        f"bash -n gate failed on {len(failures)} shell file(s):\n"
        + "\n".join(failures)
    )
