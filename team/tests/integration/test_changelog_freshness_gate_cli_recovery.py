"""
test_changelog_freshness_gate_cli_recovery.py
===============================================
Integration test: the CHANGELOG-freshness gate's own printed remedy output,
invoked exactly as a stranger would run it from a shell (a real subprocess
against the script's CLI entry point, not an internal function call), is
sufficient on its own to recover a forced-stale CHANGELOG.md — no guidance
from outside the gate's captured output is used anywhere in the recovery.

This complements team/tests/unit/test_lint_changelog_freshness.py, whose
remedy-block tests call check_freshness() directly (an internal function
call, useful for isolating the comparison logic). This file instead launches
`python3 team/scripts/lint_changelog_freshness.py ...` as a real child
process — the literal command a human operator or a downstream script would
run — and parses the fix commands purely out of the captured stderr text.
Placing this proof under team/tests/integration/ means run-integration-tests.sh
exercises the gate's CLI contract independently of run-unit-tests.sh, which
exercises the internal comparison logic via test_lint_changelog_freshness.py.

Design notes:
  - All scratch files live under tmp_path (routed to the framework temp root
    by team/tests/conftest.py). No bare /tmp paths.
  - Each test builds its own forced-stale scratch CHANGELOG.md; no shared
    state or ordering dependency between tests.
  - The gate and its printed regenerate command render with the release
    render, which writes each Public ID it assigns into the ledger it reads,
    so each test hands them a scratch copy of the committed bug-ledger
    fixture under a scratch kanban root — never the operator's live ledger.
  - PGAI_AGENT_KANBAN_TEMP_DIR is pinned to a per-test scratch directory in
    every subprocess environment so the gate's own temp usage cannot depend
    on ambient environment state.
  - No PGAI_LINT_CHANGELOG_MODE is set — the gate runs in strict byte-compare
    mode, so a forced-stale artifact fails deterministically regardless of
    which branch the worktree happens to be on.
"""

from __future__ import annotations

import os
import pathlib
import re
import shutil
import subprocess
import sys

import pytest

# ---------------------------------------------------------------------------
# Path resolution. This file lives at team/tests/integration/<this file>.
# Three parent levels up: integration/ -> tests/ -> team/ -> project_root/
# ---------------------------------------------------------------------------
_TEAM_DIR = pathlib.Path(__file__).resolve().parent.parent.parent  # team/
_DEV_TREE_ROOT = _TEAM_DIR.parent  # project_root/
_LINT_SCRIPT = _TEAM_DIR / "scripts" / "lint_changelog_freshness.py"
_CHANGELOG_ARTIFACT = _DEV_TREE_ROOT / "CHANGELOG.md"
_RELEASE_NOTES_DIR = _DEV_TREE_ROOT / "release-notes"

# Committed bug-ledger fixture; each test runs the gate on a scratch copy.
_FIXTURE_BUGS_DIR = _TEAM_DIR / "tests" / "fixtures" / "bug_ledger"

# A numbered fix-command line, e.g. "  1. bash \"...\" pgai_agent_kanban... > ...".
_NUMBERED_COMMAND_RE = re.compile(r"^\s*(\d+)\.\s+(.+)$", re.MULTILINE)

# An unresolved angle-bracket placeholder such as "<name>" or "<repo-root>".
# The house placeholder law (docs/coding-standards.md) forbids these in the
# printed fix commands.
_ANGLE_BRACKET_PLACEHOLDER_RE = re.compile(r"<[A-Za-z][\w -]*>")


def _scratch_kanban_root(tmp_path: pathlib.Path) -> pathlib.Path:
    """Return a scratch kanban root holding a copy of the committed fixture ledger.

    The copy sits at ``<root>/projects/pgai-agent-kanban/bugs``; Public IDs the
    gate's render or its printed regenerate command write land there.
    """
    root = tmp_path / "kanban_root"
    shutil.copytree(_FIXTURE_BUGS_DIR, _scratch_bugs_dir(root))
    return root


def _scratch_bugs_dir(kanban_root: pathlib.Path) -> pathlib.Path:
    """The project ledger directory under *kanban_root*."""
    return kanban_root / "projects" / "pgai-agent-kanban" / "bugs"


def _skip_if_published_missing() -> None:
    """Skip the test when release-notes/PUBLISHED is absent.

    A fresh regeneration from an absent PUBLISHED manifest produces
    empty-manifest output that never matches the committed CHANGELOG.md,
    which would make the forced-stale-then-recover flow meaningless.
    """
    if not (_RELEASE_NOTES_DIR / "PUBLISHED").exists():
        pytest.skip(
            f"release-notes/PUBLISHED not found under {_DEV_TREE_ROOT} "
            "— regeneration produces empty-manifest output that diverges from "
            "the committed CHANGELOG.md; the recovery flow is not meaningful here."
        )


def _force_stale_changelog(tmp_path: pathlib.Path) -> pathlib.Path:
    """Return a scratch CHANGELOG.md path guaranteed to fail the gate.

    Copies the real committed CHANGELOG.md and appends a marker line so the
    byte-compare in the gate is guaranteed to fail, independent of any
    live-environment drift state.

    Args:
        tmp_path: pytest-provided temp directory to write the scratch file under.

    Returns:
        Path to the scratch, guaranteed-stale CHANGELOG.md copy.
    """
    original_bytes = _CHANGELOG_ARTIFACT.read_bytes()
    mutated = original_bytes + b"\n# FORCED-STALE-FOR-CLI-RECOVERY-TEST\n"
    scratch = tmp_path / "cli_recovery_stale_changelog.md"
    scratch.write_bytes(mutated)
    return scratch


def _gate_subprocess_env(tmp_path: pathlib.Path, kanban_root: pathlib.Path) -> dict:
    """Return a subprocess environment pinned to per-test scratch state.

    PGAI_AGENT_KANBAN_TEMP_DIR is pinned under tmp_path so the gate's own temp
    usage (_get_temp_root() in lint_changelog_freshness.py) cannot depend on
    ambient environment state (roles/TESTER.md Test Authoring Guidelines Anti-pattern 2).
    PGAI_AGENT_KANBAN_ROOT_PATH is the scratch kanban root, never the live one.
    PGAI_LINT_CHANGELOG_MODE is explicitly absent so the gate runs in strict
    byte-compare mode regardless of which branch this worktree is on.

    Args:
        tmp_path:    pytest-provided temp directory for this test.
        kanban_root: The scratch kanban root (see _scratch_kanban_root()).

    Returns:
        A copy of the current environment with the above overrides applied.
    """
    env = os.environ.copy()
    env["PGAI_AGENT_KANBAN_TEMP_DIR"] = str(tmp_path / "gate_temp_root")
    env["PGAI_AGENT_KANBAN_ROOT_PATH"] = str(kanban_root)
    env.pop("PGAI_LINT_CHANGELOG_MODE", None)
    return env


def _run_gate_cli(
    scratch_changelog: pathlib.Path, bugs_dir: pathlib.Path, env: dict
) -> subprocess.CompletedProcess:
    """Invoke the gate's real CLI entry point as a subprocess.

    This is the literal command a human operator (or a downstream script)
    runs — not an internal function call — so the captured stdout/stderr is
    exactly what a stranger would see.

    Args:
        scratch_changelog: Path to the CHANGELOG.md artifact to check.
        bugs_dir:           The scratch bug ledger the gate renders from.
        env:                Subprocess environment (see _gate_subprocess_env()).

    Returns:
        The completed subprocess result.
    """
    return subprocess.run(
        [
            sys.executable,
            str(_LINT_SCRIPT),
            "--changelog",
            str(scratch_changelog),
            "--bugs-dir",
            str(bugs_dir),
        ],
        env=env,
        capture_output=True,
        text=True,
        cwd=str(_TEAM_DIR),
    )


class TestChangelogFreshnessGateCliRecovery:
    """A stranger with only the gate's CLI stderr output can recover a stale CHANGELOG.

    Each test launches the gate as a real subprocess against a forced-stale
    scratch artifact — the same invocation shape an operator would type — and
    treats the captured stderr as the only source of truth for what to run
    next.
    """

    def test_cli_failure_output_carries_a_complete_verbatim_remedy_block(
        self, tmp_path: pathlib.Path
    ) -> None:
        """The gate's real CLI stderr contains cause(s), a numbered fix block
        ending in a re-check command, the enforced rule, and zero unresolved
        angle-bracket placeholders — everything a stranger needs, with nothing
        left implicit.
        """
        _skip_if_published_missing()

        scratch = _force_stale_changelog(tmp_path)
        kanban_root = _scratch_kanban_root(tmp_path)
        bugs_dir = _scratch_bugs_dir(kanban_root)
        env = _gate_subprocess_env(tmp_path, kanban_root)

        result = _run_gate_cli(scratch, bugs_dir, env)
        assert result.returncode != 0, (
            "Gate CLI subprocess exited 0 for a forced-stale scratch CHANGELOG.md.\n"
            f"stdout: {result.stdout!r}\nstderr: {result.stderr!r}"
        )

        err = result.stderr
        assert "Cause(s):" in err, (
            f"Gate CLI stderr is missing the 'Cause(s):' section.\nstderr: {err!r}"
        )
        assert str(scratch) in err, (
            "Gate CLI stderr does not name the actual scratch changelog path.\n"
            f"stderr: {err!r}"
        )
        assert "To fix:" in err, (
            f"Gate CLI stderr is missing the 'To fix:' numbered fix block.\nstderr: {err!r}"
        )

        commands = _NUMBERED_COMMAND_RE.findall(err)
        assert len(commands) >= 2, (
            "Gate CLI stderr's numbered fix block has fewer than 2 commands "
            "(expected at least: regenerate, then re-lint).\n"
            f"Parsed commands: {commands!r}\nstderr: {err!r}"
        )
        last_command_text = commands[-1][1]
        assert "lint_changelog_freshness.py" in last_command_text, (
            "Last numbered command is not the re-lint verification command.\n"
            f"Last command: {last_command_text!r}"
        )
        assert str(scratch) in last_command_text, (
            "Re-lint command does not carry the actual scratch changelog path.\n"
            f"Last command: {last_command_text!r}"
        )

        assert (
            "the writer owns CHANGELOG.md; hand-edits re-trip this gate" in err
        ), f"Gate CLI stderr is missing the one-line enforced rule.\nstderr: {err!r}"

        placeholder_hits = _ANGLE_BRACKET_PLACEHOLDER_RE.findall(err)
        assert placeholder_hits == [], (
            "Gate CLI stderr contains unresolved angle-bracket placeholder(s), "
            "violating the house placeholder law.\n"
            f"Placeholders found: {placeholder_hits!r}\nstderr: {err!r}"
        )

    def test_running_only_the_printed_commands_clears_the_gate(
        self, tmp_path: pathlib.Path
    ) -> None:
        """Executing ONLY the commands parsed from the gate's real CLI stderr,
        verbatim and in printed order, recovers the forced-stale CHANGELOG and
        the gate subsequently reports success on re-invocation.

        No command text is hand-reconstructed or sourced from anywhere but the
        captured subprocess stderr — this is the motivating end-to-end proof
        that the gate's own output is a complete recovery path.
        """
        _skip_if_published_missing()

        scratch = _force_stale_changelog(tmp_path)
        kanban_root = _scratch_kanban_root(tmp_path)
        bugs_dir = _scratch_bugs_dir(kanban_root)
        env = _gate_subprocess_env(tmp_path, kanban_root)

        failing_result = _run_gate_cli(scratch, bugs_dir, env)
        assert failing_result.returncode != 0, (
            "Gate CLI subprocess exited 0 for a forced-stale scratch CHANGELOG.md; "
            "cannot exercise recovery without a genuine failure.\n"
            f"stdout: {failing_result.stdout!r}\nstderr: {failing_result.stderr!r}"
        )

        commands = _NUMBERED_COMMAND_RE.findall(failing_result.stderr)
        assert len(commands) >= 2, (
            "Expected at least 2 numbered commands in the gate's CLI stderr.\n"
            f"stderr: {failing_result.stderr!r}"
        )

        # Run every parsed command, in order, through a real shell (the
        # regenerate command uses '>' redirection, which is shell syntax).
        for _, command_text in commands:
            proc = subprocess.run(
                command_text,
                shell=True,
                capture_output=True,
                text=True,
                env=env,
            )
            assert proc.returncode == 0, (
                f"Printed remedy command exited non-zero: {command_text!r}\n"
                f"stdout: {proc.stdout!r}\nstderr: {proc.stderr!r}"
            )

        # Independently re-invoke the gate's CLI (the same invocation shape as
        # the initial failing call) to prove the artifact is genuinely fresh,
        # not merely that the last printed command happened to exit 0.
        recovered_result = _run_gate_cli(scratch, bugs_dir, env)
        assert recovered_result.returncode == 0, (
            "Gate CLI subprocess still exits non-zero against the scratch "
            "changelog path after running every printed remedy command verbatim.\n"
            f"Commands run: {[c[1] for c in commands]!r}\n"
            f"stdout: {recovered_result.stdout!r}\nstderr: {recovered_result.stderr!r}"
        )
        assert (
            "ok" in recovered_result.stdout.lower()
            or "fresh" in recovered_result.stdout.lower()
        ), (
            "Gate CLI did not print a positive confirmation after recovery.\n"
            f"stdout: {recovered_result.stdout!r}"
        )
