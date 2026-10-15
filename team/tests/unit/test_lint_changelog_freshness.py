"""
test_lint_changelog_freshness.py — Unit tests for team/scripts/lint_changelog_freshness.py.

Tests cover:

  1. **Positive (fresh tree)**: invoke lint_changelog_freshness.py as a subprocess
     with PYTHONHASHSEED=0 (belt-and-braces; see class docstring) and assert it
     exits 0.  This proves the gate exits 0 when the artifact is up-to-date.
     The subprocess invocation matches how an operator runs the check by hand
     (a fresh interpreter, the lint's own CLI).  changelog_writer sorts heading
     collections alphabetically before iteration
     (``sorted(_IMPLEMENTED_HEADINGS)`` / ``sorted(_FIXED_HEADINGS)``), so
     output is hash-seed-independent.  The PYTHONHASHSEED=0 env setting is kept
     for defense-in-depth; it is no longer the mechanism responsible for ordering.

  2. **Behavioral negative (stale artifact)**: copy CHANGELOG.md to a scratch
     file under tmp_path, byte-modify one character, and pass the modified path
     to check_freshness().  Assert it returns False and that the error output
     names the artifact as stale plus the regeneration command.  This exercises
     the real comparison code — no mocked exit codes.

  3. **Missing artifact**: pass a nonexistent path as changelog_path; assert
     check_freshness() returns False and the error names the missing artifact.

  4. **CLI: unknown argument exits 2 with usage**: invoke main(['--bad-arg'])
     and assert SystemExit code is 2 and stderr contains usage output.

  5. **CLI: --help exits 0 without running the check**: invoke main(['--help'])
     and assert SystemExit code is 0; no freshness check is run.

  6. **CHANGELOG content gates**: the committed CHANGELOG.md carries no BUG-[0-9]
     tokens, no placeholder strings, entry count equals release-notes file count,
     and KI-1.0.0.1 renders under the v1.0.0 entry.

  7. **RC-mode tolerance**: a fixture that copies the committed fixture ledger to a temp
     location, adds a post-CHANGELOG-commit fake bug file, and asserts that
     check_freshness() passes in RC mode (PGAI_LINT_CHANGELOG_MODE=rc) and
     fails in normal mode (no env var).  Also asserts that a genuine
     CHANGELOG content edit still fails in RC mode (non-KI staleness).

  8. **Writer-migration drift tolerance**:
     Three test cases using the _diff_is_rc_tolerable() helper directly to
     verify the three new drift categories are correctly handled:

     (a) Fixture with all three drift categories present in the checked-in
         content but absent from the regenerated content → _diff_is_rc_tolerable()
         returns True (RC-mode tolerates the three removals).

     (b) Lint exits non-zero on ai_main even when PGAI_LINT_CHANGELOG_MODE=rc
         is set — the branch check in _is_rc_mode() overrides the env var.

     (c) Fixture with a drift line that is NOT one of the three tolerable
         categories (a genuine non-drift removal) → _diff_is_rc_tolerable()
         returns False (no over-broad admission).

  9. **Empty Known Issues placeholder replacement**: _diff_is_rc_tolerable()
     admits a ``None`` placeholder under ``### Known Issues`` replaced only by
     new KI entries, and rejects every wider shape (other headings, other
     removals, non-KI or already-published additions); check_freshness()
     passes that fixture in RC mode and reports it stale, unchanged, in
     strict mode.

The lint renders with the release render, which writes each Public ID it
assigns back to its bug file, so every test that runs it hands it a scratch
copy of the committed bug-ledger fixture — never the operator's live ledger.

All temp paths use pytest's tmp_path (redirected to the framework temp root
by conftest.py when PGAI_AGENT_KANBAN_TEMP_DIR is set).  No bare /tmp paths.
"""

from __future__ import annotations

import importlib.util
import os
import pathlib
import re
import shutil
import subprocess
import sys
import unittest.mock

import pytest

from pgai_agent_kanban.cm import changelog_writer as _changelog_writer
from tests.fixtures.binary_guard import require_binary


@pytest.fixture(autouse=True)
def _require_git() -> None:
    """Skip every test in this module when git is not on PATH.

    Every test here builds, inspects, or drives a real git repository; a
    module-wide autouse guard keeps that requirement in one place instead
    of repeating a per-test check at each git-dependent call site.
    """
    require_binary("git", label="lint-changelog-freshness")

# ---------------------------------------------------------------------------
# Locate lint_changelog_freshness.py and the real CHANGELOG artifact.
# This file lives at team/tests/unit/test_lint_changelog_freshness.py.
# Three parent levels up: unit/ -> tests/ -> team/ -> project_root/
# ---------------------------------------------------------------------------
_THIS_FILE = pathlib.Path(__file__).resolve()
_TEAM_DIR = _THIS_FILE.parent.parent.parent  # team/
_DEV_TREE_ROOT = _TEAM_DIR.parent  # project_root/
_SCRIPTS_DIR = _TEAM_DIR / "scripts"
_LINT_SCRIPT = _SCRIPTS_DIR / "lint_changelog_freshness.py"
_CHANGELOG_ARTIFACT = _DEV_TREE_ROOT / "CHANGELOG.md"
_RELEASE_NOTES_DIR = _DEV_TREE_ROOT / "release-notes"

# Committed bug-ledger fixture — used by the tests that assert specific
# KI-1.0.0.x disclosure CONTENT. Holds one record per published
# KI-1.0.0.x identifier, recovered verbatim from the c2d8056 CHANGELOG.md
# render.  The path is committed to the tree and its content never varies
# by machine or by what the operator's live ledger currently contains.  A
# test that runs the lint copies it first (_scratch_bugs_dir below): the
# lint's render writes any Public ID it assigns into the ledger it reads.
_FIXTURE_BUGS_DIR = _TEAM_DIR / "tests" / "fixtures" / "bug_ledger"

# The project whose ledger the lint resolves by default under a kanban root.
_PROJECT_NAME = "pgai-agent-kanban"


def _import_lint_module():
    """Import lint_changelog_freshness as a module without polluting sys.modules permanently.

    Returns:
        The loaded module object.

    Raises:
        ImportError: if the lint script cannot be loaded.
    """
    spec = importlib.util.spec_from_file_location(
        "lint_changelog_freshness", _LINT_SCRIPT
    )
    if spec is None or spec.loader is None:
        raise ImportError(f"Cannot load spec from {_LINT_SCRIPT}")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)  # type: ignore[union-attr]
    return mod


# Import once at module load time; tests reference `_lint` attributes.
_lint = _import_lint_module()


def _skip_if_published_missing() -> None:
    """Skip the test when release-notes/PUBLISHED is absent.

    The changelog_writer tolerates an absent PUBLISHED (returns empty manifest)
    so regeneration no longer crashes when the file is missing.  However, these
    tests compare the regenerated output against the committed CHANGELOG.md; a
    fresh regeneration from an absent PUBLISHED would produce empty-manifest
    output that does not match the committed artifact (which was generated from
    a populated PUBLISHED).  Skip rather than emit a misleading failure.
    """
    if not (_RELEASE_NOTES_DIR / "PUBLISHED").exists():
        pytest.skip(
            f"release-notes/PUBLISHED not found under {_DEV_TREE_ROOT} "
            "— regeneration produces empty-manifest output that diverges from "
            "the committed CHANGELOG.md; freshness check not meaningful here."
        )


def _scratch_kanban_root(tmp_path: pathlib.Path) -> pathlib.Path:
    """Return a scratch kanban root holding a copy of the committed fixture ledger.

    The copy sits where the lint resolves a project's ledger by default
    (``<root>/projects/pgai-agent-kanban/bugs``), so a subprocess pointed at
    this root through PGAI_AGENT_KANBAN_ROOT_PATH reads it without
    ``--bugs-dir``.  Public IDs the lint's render writes land in the copy.
    """
    root = tmp_path / "kanban_root"
    shutil.copytree(_FIXTURE_BUGS_DIR, root / "projects" / _PROJECT_NAME / "bugs")
    return root


def _scratch_bugs_dir(tmp_path: pathlib.Path) -> pathlib.Path:
    """Return a scratch copy of the committed fixture ledger (see _scratch_kanban_root)."""
    return _scratch_kanban_root(tmp_path) / "projects" / _PROJECT_NAME / "bugs"


def _render_scratch_changelog(
    bugs_dir: pathlib.Path, path: pathlib.Path
) -> pathlib.Path:
    """Write the CHANGELOG.md a release render of *bugs_dir* produces to *path*.

    Renders the dev tree's release notes and PUBLISHED manifest with
    *bugs_dir* as the ledger and nothing published, so the gate run against
    the same ledger finds *path* fresh.

    Returns:
        *path*.
    """
    versions = sorted(
        (p.stem for p in _RELEASE_NOTES_DIR.glob("v*.md")),
        key=_changelog_writer._parse_version,
        reverse=True,
    )
    content = _changelog_writer.regenerate(
        _DEV_TREE_ROOT,
        versions,
        _changelog_writer.load_published_manifest(_DEV_TREE_ROOT),
        bugs_dir,
        published_changelog=None,
    )
    path.write_text(content, encoding="utf-8")
    return path


# ---------------------------------------------------------------------------
# Positive tests (subprocess-based to mirror an operator's direct invocation)
# ---------------------------------------------------------------------------


class TestChangelogFreshnessPositive:
    """Fresh artifact: gate exits 0 when the artifact is up-to-date.

    The positive tests invoke the gate via subprocess rather than calling
    check_freshness() directly, with no ``--bugs-dir``: the gate resolves the
    ledger from PGAI_AGENT_KANBAN_ROOT_PATH, which points at a scratch kanban
    root holding a copy of the committed fixture ledger (never the operator's
    live root).  The artifact checked is a scratch CHANGELOG.md rendered from
    that same ledger.

    PYTHONHASHSEED=0 is set in the subprocess environment as belt-and-braces, but
    it is no longer the mechanism responsible for deterministic heading order.
    changelog_writer.py sorts heading collections alphabetically before iterating
    (``sorted(_IMPLEMENTED_HEADINGS)`` / ``sorted(_FIXED_HEADINGS)``), which
    guarantees a stable, hash-seed-independent iteration order.  The env setting is
    retained for defense-in-depth but does not affect correctness.

    PGAI_LINT_CHANGELOG_MODE=rc is set for the two passing cases, the mode an RC
    worktree runs the gate in.  A companion negative test
    (test_gate_exits_1_without_rc_mode_when_bugs_dir_has_post_commit_entries)
    files a new bug into the scratch ledger after the render and asserts that
    strict mode then fails.
    """

    @staticmethod
    def _run_gate(
        kanban_root: pathlib.Path, changelog: pathlib.Path, rc_mode: bool
    ) -> subprocess.CompletedProcess:
        env = os.environ.copy()
        env["PYTHONHASHSEED"] = "0"
        env["PGAI_AGENT_KANBAN_ROOT_PATH"] = str(kanban_root)
        if rc_mode:
            env["PGAI_LINT_CHANGELOG_MODE"] = "rc"
        else:
            env.pop("PGAI_LINT_CHANGELOG_MODE", None)
        return subprocess.run(
            [sys.executable, str(_LINT_SCRIPT), "--changelog", str(changelog)],
            env=env,
            capture_output=True,
            text=True,
            cwd=str(_TEAM_DIR),
        )

    def test_fresh_artifact_exits_0(self, tmp_path: pathlib.Path) -> None:
        """Gate subprocess exits 0 for a CHANGELOG.md rendered from its own ledger."""
        _skip_if_published_missing()
        root = _scratch_kanban_root(tmp_path)
        changelog = _render_scratch_changelog(
            root / "projects" / _PROJECT_NAME / "bugs", tmp_path / "CHANGELOG.md"
        )

        result = self._run_gate(root, changelog, rc_mode=True)

        assert result.returncode == 0, (
            "Gate subprocess exited non-zero for a CHANGELOG.md rendered from the "
            "ledger it was checked against (RC mode active).\n"
            f"stdout: {result.stdout!r}\n"
            f"stderr: {result.stderr!r}"
        )

    def test_fresh_artifact_prints_ok_message(self, tmp_path: pathlib.Path) -> None:
        """Gate subprocess prints 'ok' confirmation when the artifact is fresh.

        Verifies that the gate emits a positive confirmation message to stdout
        rather than silently succeeding, so an operator can see the gate ran.
        """
        _skip_if_published_missing()
        root = _scratch_kanban_root(tmp_path)
        changelog = _render_scratch_changelog(
            root / "projects" / _PROJECT_NAME / "bugs", tmp_path / "CHANGELOG.md"
        )

        result = self._run_gate(root, changelog, rc_mode=True)

        assert "ok" in result.stdout.lower() or "fresh" in result.stdout.lower(), (
            f"Gate subprocess did not print a positive confirmation to stdout "
            f"(RC mode active).\n"
            f"stdout: {result.stdout!r}\n"
            f"stderr: {result.stderr!r}\n"
            "Expected output containing 'ok' or 'fresh'."
        )

    def test_gate_exits_1_without_rc_mode_when_bugs_dir_has_post_commit_entries(
        self, tmp_path: pathlib.Path
    ) -> None:
        """Gate subprocess exits 1 in strict mode when the ledger gained a bug after the render.

        The scratch CHANGELOG.md is rendered first; a disclosable bug is then
        filed into the scratch ledger, so a fresh render adds its Known Issues
        line.  Without PGAI_LINT_CHANGELOG_MODE=rc the gate byte-compares and
        must exit 1, naming the artifact stale.
        """
        _skip_if_published_missing()
        root = _scratch_kanban_root(tmp_path)
        bugs_dir = root / "projects" / _PROJECT_NAME / "bugs"
        changelog = _render_scratch_changelog(bugs_dir, tmp_path / "CHANGELOG.md")
        (bugs_dir / "BUG-9999-test-rc-mode-tolerance-fixture.md").write_text(
            TestRCModeTolerance._SYNTHETIC_BUG_CONTENT, encoding="utf-8"
        )

        result = self._run_gate(root, changelog, rc_mode=False)

        assert result.returncode == 1, (
            "Gate subprocess did not exit 1 in strict mode for a ledger that gained "
            "a disclosable bug after the artifact was rendered.\n"
            f"stdout: {result.stdout!r}\n"
            f"stderr: {result.stderr!r}"
        )
        assert "stale" in result.stderr.lower(), (
            "Gate subprocess exited 1 in strict mode but stderr does not contain 'stale'.\n"
            f"stdout: {result.stdout!r}\n"
            f"stderr: {result.stderr!r}"
        )


# ---------------------------------------------------------------------------
# Behavioral negative tests
# ---------------------------------------------------------------------------


class TestChangelogFreshnessBehavioralNegative:
    """Stale artifact: check_freshness returns False and names the staleness.

    Each test in this class actually modifies a scratch artifact and exercises
    the real comparison code — exit codes and return values are not mocked.
    The behavioral negative is a hard requirement: a gate that cannot fail is
    not a gate.
    """

    def test_byte_modified_artifact_fails(
        self, tmp_path: pathlib.Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        """check_freshness() returns False when the artifact has been byte-modified.

        The test copies CHANGELOG.md to a scratch file under tmp_path,
        appends a byte to guarantee a mismatch, and passes the modified
        file as changelog_path.  The gate regenerates CHANGELOG.md to a temp
        path and byte-compares; the modification causes the comparison to fail.

        This is the behavioral-negative proof-of-fire: a scratch-edited
        CHANGELOG must make the gate exit 1.

        Args:
            tmp_path:  pytest-provided temp directory (routed to framework temp root).
            capsys:    pytest capture fixture for inspecting stderr.
        """
        bugs_dir = _scratch_bugs_dir(tmp_path)
        _skip_if_published_missing()

        # Create a byte-modified scratch artifact.
        original_bytes = _CHANGELOG_ARTIFACT.read_bytes()
        assert len(original_bytes) > 0, "Artifact is empty — cannot byte-modify"
        # Append a byte to guarantee a mismatch (append rather than flip so
        # the scratch file is still valid UTF-8 markdown).
        mutated = original_bytes + b"\x0a# STALE MARKER\n"

        scratch = tmp_path / "stale_changelog.md"
        scratch.write_bytes(mutated)

        result = _lint.check_freshness(
            changelog_path=scratch,
            bugs_dir=bugs_dir,
        )
        assert result is False, (
            "check_freshness() returned True for a byte-modified artifact.\n"
            "The staleness detection code is not firing — the byte-compare must "
            "return False when the artifact differs from a fresh regeneration."
        )

    def test_stale_artifact_error_names_staleness(
        self, tmp_path: pathlib.Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        """Stale artifact error output names the artifact as stale.

        Verifies that the error message emitted on a byte mismatch includes:
          - the word 'stale' (naming the condition)
          - a regeneration command reference ('changelog_writer' or 'changelog')

        Args:
            tmp_path:  pytest-provided temp directory.
            capsys:    pytest capture fixture for inspecting stderr.
        """
        bugs_dir = _scratch_bugs_dir(tmp_path)
        _skip_if_published_missing()

        original_bytes = _CHANGELOG_ARTIFACT.read_bytes()
        mutated = original_bytes + b"\n# STALE MARKER\n"
        scratch = tmp_path / "stale_changelog_msg.md"
        scratch.write_bytes(mutated)

        _lint.check_freshness(
            changelog_path=scratch,
            bugs_dir=bugs_dir,
        )
        captured = capsys.readouterr()
        err = captured.err

        assert "stale" in err.lower(), (
            f"Error output does not name the artifact as stale.\n"
            f"stderr: {err!r}\n"
            "Expected the word 'stale' in the error message."
        )
        assert "changelog_writer" in err.lower() or "changelog" in err.lower(), (
            f"Error output does not reference the regeneration command.\n"
            f"stderr: {err!r}\n"
            "Expected a reference to 'changelog_writer' or 'changelog' in the "
            "error message so operators know how to fix the staleness."
        )

    def test_missing_artifact_fails(
        self, tmp_path: pathlib.Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        """check_freshness() returns False when the artifact file does not exist.

        An absent artifact is treated as stale — it has not been generated
        and committed.  The error output must identify the missing file.

        Args:
            tmp_path:  pytest-provided temp directory.
            capsys:    pytest capture fixture for inspecting stderr.
        """
        bugs_dir = _scratch_bugs_dir(tmp_path)
        _skip_if_published_missing()

        absent = tmp_path / "nonexistent_changelog.md"
        assert not absent.exists()

        result = _lint.check_freshness(
            changelog_path=absent,
            bugs_dir=bugs_dir,
        )
        assert result is False, (
            "check_freshness() returned True for a nonexistent artifact path.\n"
            "A missing artifact must be treated as stale (exit 1)."
        )
        captured = capsys.readouterr()
        assert captured.err, (
            "check_freshness() emitted no error output for a missing artifact.\n"
            "The error message must identify the issue so operators know what to fix."
        )


# ---------------------------------------------------------------------------
# Remedy-block tests (the gates-prescribe-remedies motivating case)
# ---------------------------------------------------------------------------

# A numbered fix-command line, e.g. "  1. PYTHONPATH=... python3 -m ...".
_NUMBERED_COMMAND_RE = re.compile(r"^\s*(\d+)\.\s+(.+)$", re.MULTILINE)

# An unresolved angle-bracket placeholder such as "<name>" or "<repo-root>".
# The house placeholder law (docs/coding-standards.md) forbids these in the
# printed fix commands — every site-specific value must already be derived
# and substituted by the time the remedy block is printed.
_ANGLE_BRACKET_PLACEHOLDER_RE = re.compile(r"<[A-Za-z][\w -]*>")


class TestChangelogFreshnessRemedyBlock:
    """The remedy block: cause(s), numbered verbatim commands, re-lint, rule.

    Exercises the gates-prescribe-remedies motivating case end to
    end: force a stale-CHANGELOG failure, assert the remedy block contains
    every required part (concrete cause, a numbered fix block ending in the
    re-lint check, and the one-line enforced rule), assert the printed
    commands carry no angle-bracket placeholders, and then actually execute
    the printed commands verbatim to prove they clear the gate — a stranger
    reading only the gate's failure output, with no other guidance, must be
    able to fix it.
    """

    def _force_stale_changelog(self, tmp_path: pathlib.Path) -> pathlib.Path:
        """Return a scratch CHANGELOG.md path that is guaranteed stale.

        Copies the real committed CHANGELOG.md and appends a marker line so
        the byte-compare in check_freshness() is guaranteed to fail, without
        depending on any particular live-environment drift state.

        Args:
            tmp_path: pytest-provided temp directory to write the scratch file under.

        Returns:
            Path to the scratch, guaranteed-stale CHANGELOG.md copy.
        """
        original_bytes = _CHANGELOG_ARTIFACT.read_bytes()
        mutated = original_bytes + b"\n# FORCED-STALE-FOR-REMEDY-BLOCK-TEST\n"
        scratch = tmp_path / "remedy_block_stale_changelog.md"
        scratch.write_bytes(mutated)
        return scratch

    def test_remedy_block_contains_all_required_parts(
        self, tmp_path: pathlib.Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        """Forcing a stale-CHANGELOG failure prints the full three-part remedy block.

        Asserts, per docs/coding-standards.md "Gates prescribe, not just
        diagnose":
          1. Cause(s) with a concrete location (the "Cause(s):" section,
             naming the actual scratch changelog_path).
          2. A numbered fix block of at least two verbatim-runnable commands,
             ending with the re-lint verification command (the lint script's
             own path, invoked with --changelog/--bugs-dir).
          3. The one-line enforced rule.
          4. No angle-bracket placeholders anywhere in the output — every
             site-specific value (repo_root, bugs_dir, changelog_path) is
             already substituted with its derived actual value.
        """
        bugs_dir = _scratch_bugs_dir(tmp_path)
        _skip_if_published_missing()

        scratch = self._force_stale_changelog(tmp_path)

        result = _lint.check_freshness(
            changelog_path=scratch,
            bugs_dir=bugs_dir,
        )
        assert result is False, "Forced-stale scratch CHANGELOG did not fail the gate."

        err = capsys.readouterr().err

        assert "Cause(s):" in err, (
            f"Remedy block missing the 'Cause(s):' section.\nstderr: {err!r}"
        )
        assert str(scratch) in err, (
            "Remedy block does not name the actual stale changelog_path.\n"
            f"stderr: {err!r}"
        )

        assert "To fix:" in err, (
            f"Remedy block missing the 'To fix:' numbered fix block.\nstderr: {err!r}"
        )
        commands = _NUMBERED_COMMAND_RE.findall(err)
        assert len(commands) >= 2, (
            "Remedy block's numbered fix block has fewer than 2 commands "
            "(expected at least: regenerate, then re-lint).\n"
            f"Parsed commands: {commands!r}\nstderr: {err!r}"
        )
        # The re-lint verification command must be the last numbered command
        # and must invoke the lint script itself.
        last_command_text = commands[-1][1]
        assert "lint_changelog_freshness.py" in last_command_text, (
            "Last numbered command is not the re-lint verification command "
            "(expected it to invoke lint_changelog_freshness.py).\n"
            f"Last command: {last_command_text!r}\nstderr: {err!r}"
        )
        assert str(scratch) in last_command_text, (
            "Re-lint command does not carry the actual scratch changelog_path.\n"
            f"Last command: {last_command_text!r}"
        )

        assert (
            "the writer owns CHANGELOG.md; hand-edits re-trip this gate" in err
        ), (
            "Remedy block missing the one-line enforced rule.\n"
            f"stderr: {err!r}"
        )

        placeholder_hits = _ANGLE_BRACKET_PLACEHOLDER_RE.findall(err)
        assert placeholder_hits == [], (
            "Remedy block contains unresolved angle-bracket placeholder(s), "
            "violating the house placeholder law.\n"
            f"Placeholders found: {placeholder_hits!r}\nstderr: {err!r}"
        )

    def test_remedy_block_commands_clear_the_gate(
        self, tmp_path: pathlib.Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        """Executing the remedy block's printed commands verbatim clears the gate.

        This is the gates-prescribe-remedies motivating case, end to end:
        a stale CHANGELOG fixture is recovered using ONLY the
        gate's printed output — no external guidance. The test parses the
        exact numbered command strings out of the captured stderr (not a
        hand-reconstructed equivalent) and runs them through a real shell, so
        it proves the literal printed text is runnable, not just that the
        underlying mechanism works.
        """
        bugs_dir = _scratch_bugs_dir(tmp_path)
        _skip_if_published_missing()

        scratch = self._force_stale_changelog(tmp_path)

        result = _lint.check_freshness(
            changelog_path=scratch,
            bugs_dir=bugs_dir,
        )
        assert result is False, "Forced-stale scratch CHANGELOG did not fail the gate."

        err = capsys.readouterr().err
        commands = _NUMBERED_COMMAND_RE.findall(err)
        assert len(commands) >= 2, (
            f"Expected at least 2 numbered commands in the remedy block.\n"
            f"stderr: {err!r}"
        )

        # Run every printed command in order, verbatim, through a shell (the
        # regenerate command uses '>' redirection and a leading env-var
        # assignment, both shell syntax).
        for _, command_text in commands:
            proc = subprocess.run(
                command_text,
                shell=True,
                capture_output=True,
                text=True,
            )
            assert proc.returncode == 0, (
                f"Printed remedy command exited non-zero: {command_text!r}\n"
                f"stdout: {proc.stdout!r}\nstderr: {proc.stderr!r}"
            )

        # The last command IS the re-lint verification command and already
        # asserted returncode == 0 above — that alone proves the gate is
        # clear.  Confirm independently via a direct check_freshness() call
        # against the now-regenerated scratch file for belt-and-braces.
        recheck = _lint.check_freshness(
            changelog_path=scratch,
            bugs_dir=bugs_dir,
        )
        assert recheck is True, (
            "check_freshness() still returns False against the scratch changelog_path "
            "after running every printed remedy command verbatim.\n"
            f"Commands run: {[c[1] for c in commands]!r}"
        )


class TestRemedyRegenerateCommand:
    """The printed regenerate command is the writer's release render, verbatim."""

    def test_regenerate_command_is_the_release_render_with_no_placeholder(
        self, tmp_path: pathlib.Path
    ) -> None:
        changelog = tmp_path / "CHANGELOG.md"
        bugs_dir = tmp_path / "bugs"

        regen_cmd, _ = _lint._build_remedy_commands(changelog, bugs_dir)

        pp_run_ops = _TEAM_DIR / "scripts" / "lib" / "pp_run_ops.sh"
        assert regen_cmd == (
            f'bash "{pp_run_ops}" pgai_agent_kanban.cm.changelog_writer '
            f'"{_DEV_TREE_ROOT}" "{bugs_dir}" > "{changelog}"'
        )
        assert _ANGLE_BRACKET_PLACEHOLDER_RE.findall(regen_cmd) == [], regen_cmd

    def test_lint_render_writes_the_assigned_public_id_into_the_ledger_it_reads(
        self, tmp_path: pathlib.Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        """The lint renders exactly as a release does, writing back the IDs it assigns.

        This is why every test here hands the lint a scratch ledger.
        """
        _skip_if_published_missing()
        bugs_dir = _scratch_bugs_dir(tmp_path)
        changelog = _render_scratch_changelog(bugs_dir, tmp_path / "CHANGELOG.md")
        new_bug = bugs_dir / "BUG-9999-test-rc-mode-tolerance-fixture.md"
        new_bug.write_text(TestRCModeTolerance._SYNTHETIC_BUG_CONTENT, encoding="utf-8")
        assert _changelog_writer.parse_bug_file(new_bug).public_id == ""

        _lint.check_freshness(changelog_path=changelog, bugs_dir=bugs_dir)
        capsys.readouterr()

        assigned = _changelog_writer.parse_bug_file(new_bug).public_id
        assert re.fullmatch(r"KI-1\.0\.0\.\d+", assigned), (
            f"the lint's render must write a KI-1.0.0.N Public ID into the new bug "
            f"file, as the release render does; got {assigned!r}"
        )


# ---------------------------------------------------------------------------
# CLI behavioural tests (argparse from birth)
# ---------------------------------------------------------------------------


class TestChangelogFreshnessCliArgparse:
    """Argparse behaviour tests: unknown args exit 2, --help exits 0 silently."""

    def test_unknown_argument_exits_2(self, capsys: pytest.CaptureFixture[str]) -> None:
        """main() with an unknown argument exits with code 2 and prints usage.

        The gate must reject unknown arguments via argparse rather than
        silently ignoring them.

        Args:
            capsys: pytest capture fixture.
        """
        with pytest.raises(SystemExit) as exc_info:
            _lint.main(["--unknown-argument-xyz"])
        assert (
            exc_info.value.code == 2
        ), f"Expected SystemExit(2) for unknown argument, got {exc_info.value.code}."

    def test_help_exits_0_without_running_check(
        self, capsys: pytest.CaptureFixture[str]
    ) -> None:
        """main(['--help']) exits 0 without running the freshness check.

        Verifies that --help shows the usage message and exits without
        attempting to regenerate the CHANGELOG or compare bytes.

        Args:
            capsys: pytest capture fixture.
        """
        with pytest.raises(SystemExit) as exc_info:
            _lint.main(["--help"])
        assert (
            exc_info.value.code == 0
        ), f"Expected SystemExit(0) for --help, got {exc_info.value.code}."
        captured = capsys.readouterr()
        # The help message should mention the script name or usage
        assert "changelog" in captured.out.lower() or "usage" in captured.out.lower(), (
            f"--help output does not mention 'changelog' or 'usage'.\n"
            f"stdout: {captured.out!r}"
        )


# ---------------------------------------------------------------------------
# CHANGELOG content gate tests (acceptance criteria)
# ---------------------------------------------------------------------------


class TestChangelogContentGates:
    """Verify committed CHANGELOG.md meets all content acceptance criteria.

    These tests operate on the committed artifact directly (no regeneration
    in each test) and assert the content invariants that the v1.19.0 migration
    established.
    """

    def test_no_internal_bug_identifiers(self) -> None:
        """Committed CHANGELOG.md contains zero BUG-[0-9] tokens.

        Internal BUG-NNNN identifiers must never appear in the generated
        CHANGELOG.  The writer has a safety pass that strips them; this test
        is the committed-file proof that the safety pass worked.
        """
        content = _CHANGELOG_ARTIFACT.read_text(encoding="utf-8")
        matches = re.findall(r"BUG-[0-9]", content)
        assert matches == [], (
            f"Internal BUG-NNNN identifiers found in committed CHANGELOG.md: {matches}\n"
            "Regenerate CHANGELOG.md via changelog_writer and commit the result."
        )

    def test_entry_count_matches_release_notes_file_count(self) -> None:
        """Entry count in committed CHANGELOG.md equals release-notes file count.

        The writer must produce exactly one ## vX.Y.Z heading for every
        release-notes/vX.Y.Z.md file — no release is dropped or duplicated.

        Skip condition: on an RC branch whose CHANGELOG.md is legitimately stale
        by one new-release section (release-notes/vX.Y.Z.md written by WRITER but
        CM Step 11b has not yet run to update CHANGELOG.md).  In this window, the
        entry count is expected to be off by the number of pending release-notes
        files, and the CHANGELOG freshness gate (RC-mode tolerance clause 2) handles
        the staleness.  Failing this test in that window would be a false alarm.
        """
        release_note_files = list(_RELEASE_NOTES_DIR.glob("v*.md"))
        expected_count = len(release_note_files)
        assert (
            expected_count > 0
        ), f"No release-notes/v*.md files found under {_RELEASE_NOTES_DIR}"

        content = _CHANGELOG_ARTIFACT.read_text(encoding="utf-8")
        headings = re.findall(r"^## (v\d+\.\d+\.\d+)", content, re.MULTILINE)

        if len(headings) != expected_count:
            # Check whether we are on an RC branch and the mismatch is due to
            # release-notes files whose versions are absent from the committed
            # CHANGELOG (new-release-section staleness tolerated by the RC gate).
            # Use for-each-ref --points-at HEAD so this works in both symbolic
            # and detached-HEAD worktrees (TESTER always runs detached).
            import subprocess as _sp

            _refs = _sp.run(
                [
                    "git", "-C", str(_DEV_TREE_ROOT),
                    "for-each-ref",
                    "--points-at", "HEAD",
                    "--format=%(refname:short)",
                ],
                capture_output=True,
                text=True,
            ).stdout.splitlines()
            _is_rc_branch = any(
                re.match(r"^(rc/|ai_rc/)", r.strip()) for r in _refs
            )
            if _is_rc_branch:
                # Identify versions present in release-notes but absent from CHANGELOG.
                _heading_versions = set(headings)
                _note_versions = {p.stem for p in release_note_files}
                _pending = _note_versions - _heading_versions
                _rc_ref = next(
                    (r.strip() for r in _refs if re.match(r"^(rc/|ai_rc/)", r.strip())),
                    "rc branch",
                )
                if _pending:
                    pytest.skip(
                        f"On RC branch '{_rc_ref}': release-notes/ has "
                        f"{len(_pending)} pending version(s) not yet in CHANGELOG.md "
                        f"({', '.join(sorted(_pending))}). "
                        "CM Step 11b will update CHANGELOG.md at release time; "
                        "the freshness gate RC-mode tolerance handles the staleness "
                        "in the interim. Skipping entry-count check."
                    )

        assert len(headings) == expected_count, (
            f"CHANGELOG has {len(headings)} entries but "
            f"release-notes/ has {expected_count} files.\n"
            f"Missing or extra versions: "
            f"{set(headings).symmetric_difference({p.stem for p in release_note_files})}"
        )

    def test_ki_1_0_0_1_renders_under_v1_0_0(self, tmp_path: pathlib.Path) -> None:
        """KI-1.0.0.1 renders under the v1.0.0 entry when regenerated from the fixture.

        The tmux 3.4 dashboard failure (an earlier defect) has public ID KI-1.0.0.1
        and affects v1.0.0 (the only PUBLISHED version at time of writing).
        It must appear in the Known Issues section of the v1.0.0 changelog entry.

        This test regenerates CHANGELOG.md from the committed bug-ledger
        fixture (team/tests/fixtures/bug_ledger/) into a tmp_path scratch
        file and asserts on that output — it does NOT read the checked-in
        CHANGELOG.md.  The checked-in artifact's Known Issues content
        reflects whatever the operator's live bug ledger currently holds
        (those source bug files are recycled/archived after resolution,
        which is what makes them unavailable for re-disclosure at
        regeneration time), so asserting on the checked-in file would
        re-couple this test to that same live-environment state.  Sourcing
        from the fixture instead also makes the test genuinely behavioral:
        it fails if changelog_writer stops emitting the public ID, which a
        static read of a pre-existing file cannot detect.
        """
        _skip_if_published_missing()

        notes_dir = _RELEASE_NOTES_DIR
        found = sorted(
            notes_dir.glob("v*.md"),
            key=lambda p: _changelog_writer._parse_version(p.stem),
            reverse=True,
        )
        versions = [p.stem for p in found]
        published = _changelog_writer.load_published_manifest(_DEV_TREE_ROOT)

        content = _changelog_writer.regenerate(
            _DEV_TREE_ROOT, versions, published, _FIXTURE_BUGS_DIR,
            published_changelog=None,  # the fixture ledger is rendered without
            # the live artifact: its published IDs belong to the live ledger
        )
        scratch = tmp_path / "CHANGELOG.md"
        scratch.write_text(content, encoding="utf-8")

        # Extract the v1.0.0 entry (from its "## v1.0.0" heading line to the
        # next "## v" heading or end of file). Anchored to a line start: a
        # release-notes bullet may quote "## v1.0.0" mid-line.
        entry_match = re.search(
            r"^## v1\.0\.0(?:\s|$).*?(?=\n## v|\Z)", content, re.DOTALL | re.MULTILINE
        )
        assert (
            entry_match is not None
        ), f"v1.0.0 entry not found in fixture-regenerated CHANGELOG:\n{content}"
        entry_text = entry_match.group(0)

        assert "KI-1.0.0.1" in entry_text, (
            "KI-1.0.0.1 not found in v1.0.0 entry of the fixture-regenerated "
            "CHANGELOG.\nThe tmux 3.4 dashboard failure must be disclosed "
            f"under v1.0.0.\nRegenerated entry:\n{entry_text}"
        )
        assert "size missing" in entry_text, (
            "The tmux 'size missing' symptom is not present in the "
            f"fixture-regenerated v1.0.0 entry:\n{entry_text}"
        )


# ---------------------------------------------------------------------------
# RC-mode tolerance tests (an earlier defect acceptance criteria)
# ---------------------------------------------------------------------------


class TestRCModeTolerance:
    """RC-mode: post-CHANGELOG-commit bug-ledger drift is tolerated; genuine edits are not.

    Acceptance criteria (an earlier defect):
      - On an RC worktree whose CHANGELOG.md is stale relative to the live
        ledger only by post-RC BUG entries (new KI-only additions), the gate
        passes in RC mode.
      - On any branch / in normal mode, the same staleness is still a failure.
      - A non-KI diff (a genuine CHANGELOG content edit that causes removals or
        non-KI additions) still fails in RC mode.

    The fixture copies the committed bug-ledger fixture (team/tests/fixtures/
    bug_ledger/ — never the operator's live kanban bugs directory)
    to a temp location, then adds a synthetic BUG file (an earlier defect)
    that references a PUBLISHED version so that changelog_writer will assign
    it a KI Public ID and include it in the regenerated CHANGELOG.  Because
    an earlier defect is new, its KI entry does not appear in a baseline
    regenerated from the fixture alone.

    The "checked-in" side of the comparison is a scratch CHANGELOG.md
    regenerated from the fixture alone (no synthetic bug) — NOT the repo's
    real committed CHANGELOG.md.  The real committed artifact reflects
    whatever the operator's live bug ledger currently holds (which may be
    emptier or richer than the fixture), so comparing against it would
    reintroduce the exact live-environment coupling this fixture design
    avoids. Regenerating both sides of the comparison from the same committed
    fixture keeps the diff isolated to exactly what each test intends to
    exercise: the one synthetic post-RC addition.

    RC-mode tolerance uses a diff-based predicate (_diff_is_rc_tolerable) rather
    than filesystem mtime.  This avoids the mtime-reliability problem: a bug
    file's mtime moves with every edit, including the release render writing
    its Public ID back, making filesystem mtime unreliable as an indicator of
    when a bug was filed.

    The diff predicate accepts a diff only when:
      (a) no lines are removed from the checked-in artifact,
      (b) every added line is a KI line (KI-X.X.X.N — prefix), and
      (c) every added KI line's Public ID is not already in the checked-in artifact.
    """

    # Synthetic BUG file content: a disclosable bug that maps to v1.0.0
    # (the only PUBLISHED version in release-notes/PUBLISHED at time of writing).
    # The "Affects" field is "v1.0.0" — a version string parseable by _parse_version
    # so that _bug_intersects_published() returns True against the published manifest.
    # No "## Public ID" section — changelog_writer assigns one on first regeneration.
    _SYNTHETIC_BUG_CONTENT = """\
# BUG-9999-test-rc-mode-tolerance-fixture

**Filed By:** Test fixture (test_lint_changelog_freshness.py)

## Status
open

## Severity
low

## Affects
v1.0.0

## Symptom
Synthetic bug filed post-RC to exercise RC-mode tolerance in the
CHANGELOG freshness gate.  This file is created by the unit test
fixture and must never appear in the real bug ledger.

## Category
test-fixture
"""

    def _make_temp_bugs_dir(
        self, source_bugs_dir: pathlib.Path, tmp_path: pathlib.Path
    ) -> pathlib.Path:
        """Copy source_bugs_dir to a temp location, preserving file contents and mtimes.

        The copy must include every record in source_bugs_dir so that
        changelog_writer produces the same base KI lines as a baseline
        regeneration from that same source.  The synthetic an earlier
        defect file is then added on top of this complete copy.

        Args:
            source_bugs_dir:  The bug ledger directory to copy from — the
                               committed fixture (team/tests/fixtures/
                               bug_ledger/), never the operator's live kanban
                               bugs directory.
            tmp_path:          pytest-provided temp directory.

        Returns:
            Path to the temp bugs directory containing copies of every
            record from source_bugs_dir.
        """
        temp_bugs = tmp_path / "bugs_copy"
        temp_bugs.mkdir(parents=True, exist_ok=True)
        if source_bugs_dir.is_dir():
            for bug_file in source_bugs_dir.iterdir():
                if bug_file.is_file():
                    dest = temp_bugs / bug_file.name
                    shutil.copy2(str(bug_file), str(dest))
        return temp_bugs

    def _regenerate_fixture_baseline_changelog(
        self, tmp_path: pathlib.Path, suffix: str = "baseline"
    ) -> pathlib.Path:
        """Regenerate a scratch CHANGELOG.md from the fixture ledger alone.

        This is the "checked-in" side of the RC-tolerance comparison for the
        tests below — a scratch artifact derived from the same committed
        fixture the temp bugs dir is built from, so both sides of every
        comparison come from the tree (no live-ledger coupling, no
        dependency on the real repo's committed CHANGELOG.md, which may
        differ from a fresh fixture regeneration for reasons unrelated to
        what these tests exercise).

        Args:
            tmp_path:  pytest-provided temp directory.
            suffix:    Filename disambiguator when called more than once
                       per test.

        Returns:
            Path to the scratch baseline CHANGELOG.md.
        """
        notes_dir = _RELEASE_NOTES_DIR
        found = sorted(
            notes_dir.glob("v*.md"),
            key=lambda p: _changelog_writer._parse_version(p.stem),
            reverse=True,
        )
        versions = [p.stem for p in found]
        published = _changelog_writer.load_published_manifest(_DEV_TREE_ROOT)

        content = _changelog_writer.regenerate(
            _DEV_TREE_ROOT, versions, published, _FIXTURE_BUGS_DIR,
            published_changelog=None,  # the fixture ledger is rendered without
            # the live artifact: its published IDs belong to the live ledger
        )
        baseline_path = tmp_path / f"changelog_{suffix}.md"
        baseline_path.write_text(content, encoding="utf-8")
        return baseline_path

    def _add_synthetic_post_rc_bug(self, bugs_dir: pathlib.Path) -> pathlib.Path:
        """Write an earlier defect into bugs_dir.

        The synthetic bug has an Affects field referencing v1.0.0 (a PUBLISHED
        version) so that changelog_writer assigns it a KI Public ID and includes
        a KI line in the regenerated CHANGELOG.  Since an earlier defect is not in the
        checked-in CHANGELOG, the resulting KI entry is wholly new, satisfying
        the RC-mode tolerance predicate.

        Args:
            bugs_dir:  The temp bugs directory to write into.

        Returns:
            Path to the written synthetic bug file.
        """
        bug_path = bugs_dir / "BUG-9999-test-rc-mode-tolerance-fixture.md"
        bug_path.write_text(self._SYNTHETIC_BUG_CONTENT, encoding="utf-8")
        return bug_path

    def test_rc_mode_tolerates_post_rc_bug_entry(
        self, tmp_path: pathlib.Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        """RC mode passes when CHANGELOG is stale only by a new KI addition.

        Fixture setup:
          1. Regenerate a scratch baseline CHANGELOG.md from the committed
             bug-ledger fixture alone (team/tests/fixtures/bug_ledger/ —
             never the operator's live kanban bugs directory).
          2. Copy the fixture to a temp bugs dir and add an earlier defect
             (synthetic bug, Affects v1.0.0, no Public ID).
          3. Call check_freshness in RC mode (PGAI_LINT_CHANGELOG_MODE=rc),
             comparing the temp bugs dir's regeneration against the scratch
             baseline from step 1 (not the repo's real committed
             CHANGELOG.md).
          4. Assert it returns True.

        changelog_writer assigns the next free KI-1.0.0.N to an earlier
        defect during regeneration.  The regenerated CHANGELOG differs from
        the scratch baseline by exactly one added KI line that does not
        appear in the baseline.  _diff_is_rc_tolerable() accepts this diff,
        so the gate passes.

        Args:
            tmp_path:  pytest-provided temp directory.
            capsys:    pytest capture fixture for inspecting output.
        """
        _skip_if_published_missing()

        # Baseline: a fresh regeneration from the fixture alone.
        baseline_changelog = self._regenerate_fixture_baseline_changelog(tmp_path)

        # Build a temp bugs dir with the fixture + the synthetic an earlier defect.
        temp_bugs_dir = self._make_temp_bugs_dir(_FIXTURE_BUGS_DIR, tmp_path)
        self._add_synthetic_post_rc_bug(temp_bugs_dir)

        # Run in RC mode.  Patch _current_git_branch so the branch-aware check
        # in _is_rc_mode() treats this as an RC branch regardless of the
        # actual worktree branch (which may be ai_feature/* during development).
        prev_mode = os.environ.get("PGAI_LINT_CHANGELOG_MODE", "")
        os.environ["PGAI_LINT_CHANGELOG_MODE"] = "rc"
        try:
            with unittest.mock.patch.object(
                _lint, "_current_git_branch", return_value="ai_rc/v1.23.10"
            ):
                result = _lint.check_freshness(
                    changelog_path=baseline_changelog,
                    bugs_dir=temp_bugs_dir,
                )
        finally:
            if prev_mode:
                os.environ["PGAI_LINT_CHANGELOG_MODE"] = prev_mode
            else:
                os.environ.pop("PGAI_LINT_CHANGELOG_MODE", None)

        captured = capsys.readouterr()
        assert result is True, (
            "check_freshness() returned False in RC mode for a bugs dir whose only "
            "extra entry generates a new KI line not in the fixture "
            "baseline CHANGELOG.\n"
            "RC-mode tolerance must pass when the diff is only new KI additions "
            "with IDs not present in the baseline artifact.\n"
            f"stdout: {captured.out!r}\n"
            f"stderr: {captured.err!r}"
        )
        assert "rc mode" in captured.out.lower() or "tolerat" in captured.out.lower(), (
            "check_freshness() did not mention RC mode or tolerance in its success "
            "message.\n"
            f"stdout: {captured.out!r}"
        )

    def test_normal_mode_fails_for_post_rc_bug_entry(
        self, tmp_path: pathlib.Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        """Normal mode fails when CHANGELOG is stale by a new KI addition.

        Same fixture setup as test_rc_mode_tolerates_post_rc_bug_entry
        (fixture-based scratch baseline, never the operator's live kanban
        bugs directory), but check_freshness is called WITHOUT setting
        PGAI_LINT_CHANGELOG_MODE=rc.  The gate must return False: the
        regenerated CHANGELOG differs from the baseline (new KI line for an
        earlier defect), and normal mode byte-compares strictly.

        Args:
            tmp_path:  pytest-provided temp directory.
            capsys:    pytest capture fixture for inspecting output.
        """
        _skip_if_published_missing()

        # Baseline: a fresh regeneration from the fixture alone.
        baseline_changelog = self._regenerate_fixture_baseline_changelog(tmp_path)

        # Build a temp bugs dir with the fixture + the synthetic an earlier defect.
        temp_bugs_dir = self._make_temp_bugs_dir(_FIXTURE_BUGS_DIR, tmp_path)
        self._add_synthetic_post_rc_bug(temp_bugs_dir)

        # Ensure PGAI_LINT_CHANGELOG_MODE is NOT set to "rc".
        prev_mode = os.environ.pop("PGAI_LINT_CHANGELOG_MODE", None)
        try:
            result = _lint.check_freshness(
                changelog_path=baseline_changelog,
                bugs_dir=temp_bugs_dir,
            )
        finally:
            if prev_mode is not None:
                os.environ["PGAI_LINT_CHANGELOG_MODE"] = prev_mode

        captured = capsys.readouterr()
        assert result is False, (
            "check_freshness() returned True in normal mode for a bugs dir with "
            "a new entry that causes the regenerated CHANGELOG to add a new KI line.\n"
            "Normal mode must byte-compare strictly and return False when the "
            "regenerated CHANGELOG differs from the fixture baseline artifact.\n"
            f"stdout: {captured.out!r}\n"
            f"stderr: {captured.err!r}"
        )

    def test_rc_mode_fails_for_genuine_changelog_edit(
        self, tmp_path: pathlib.Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        """RC mode still fails when the CHANGELOG has a genuine content edit.

        A checked-in CHANGELOG.md that has been modified beyond post-RC bug
        additions (e.g., a line appended that is not a new KI entry) must fail
        even in RC mode.  This verifies that RC-mode tolerance is bounded.

        The test renders a scratch CHANGELOG from a scratch copy of the
        fixture ledger, appends a non-KI line, and passes it to
        check_freshness in RC mode with that same ledger.  The regeneration
        matches the unmodified render, but the scratch has an extra non-KI
        line that is NOT in the regeneration — causing a "removal" in the
        diff (the extra line is in checked-in but not in regenerated).
        _diff_is_rc_tolerable() rejects this because condition (a) requires
        no removed lines.

        Args:
            tmp_path:  pytest-provided temp directory.
            capsys:    pytest capture fixture for inspecting output.
        """
        _skip_if_published_missing()
        bugs_dir = _scratch_bugs_dir(tmp_path)

        # Append a non-KI line to a CHANGELOG rendered from the scratch ledger.
        scratch = _render_scratch_changelog(bugs_dir, tmp_path / "mutated_changelog.md")
        with scratch.open("a", encoding="utf-8") as fh:
            fh.write("\n# STALE MARKER FROM GENUINE EDIT\n")

        # Run in RC mode against the ledger the scratch CHANGELOG was rendered from.
        prev_mode = os.environ.get("PGAI_LINT_CHANGELOG_MODE", "")
        os.environ["PGAI_LINT_CHANGELOG_MODE"] = "rc"
        try:
            result = _lint.check_freshness(
                changelog_path=scratch,
                bugs_dir=bugs_dir,
            )
        finally:
            if prev_mode:
                os.environ["PGAI_LINT_CHANGELOG_MODE"] = prev_mode
            else:
                os.environ.pop("PGAI_LINT_CHANGELOG_MODE", None)

        captured = capsys.readouterr()
        assert result is False, (
            "check_freshness() returned True in RC mode for a CHANGELOG with a "
            "non-KI line appended (genuine content edit).\n"
            "RC-mode tolerance must NOT pass when the diff contains removed lines or "
            "non-KI additions — this guards against masking genuine staleness.\n"
            f"stdout: {captured.out!r}\n"
            f"stderr: {captured.err!r}"
        )

    def test_rc_mode_tolerates_new_release_section_addition(
        self, tmp_path: pathlib.Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        """RC mode passes when CHANGELOG is stale by a new ## vX.Y.Z section.

        This covers the in-flight-RC scenario where WRITER has authored a new
        release-notes/vX.Y.Z.md file on the RC branch but CM Step 11b has not yet
        run to regenerate CHANGELOG.md.  The committed CHANGELOG.md does not yet
        have the ## vX.Y.Z heading, so a fresh regeneration adds it — a class of
        diff that the original RC-mode tolerance (KI-only additions) did not handle.

        Fixture setup:
          1. Write a synthetic vX.Y.Z.md release-notes file under an overlay
             directory of its own inside tmp_path — never inside the checked-out
             dev tree — and pass it to check_freshness() via
             extra_release_notes_dir.  changelog_writer.regenerate() and
             _diff_is_rc_tolerable() both fall back to this overlay for a
             version they cannot find under the real release-notes/ directory,
             so the fresh regeneration includes a ## v99.0.0 section without
             ever touching the real tree.  The version uses a plain semver form
             so _RELEASE_HEADING_RE matches it and the new-release-section
             tolerance clause can fire.
          2. Call check_freshness in RC mode against a scratch CHANGELOG.md
             rendered from a scratch copy of the fixture ledger without the
             overlay (so it does not have this section), with that ledger.
          3. Assert the gate passes — the new-release-section tolerance fires.
          4. Assert the success message mentions the tolerance clause.

        Nothing is written under the checked-out repository at any point: the
        synthetic file lives entirely under tmp_path, so there is no teardown
        step and no window during which the real tree is dirty (including
        under a hard kill of the test process).

        This test exercises an earlier defect acceptance criteria 1-3.

        Args:
            tmp_path:  pytest-provided temp directory; holds the overlay
                       release-notes directory the synthetic fixture file
                       lives under.
            capsys:    pytest capture fixture for inspecting output.
        """
        _skip_if_published_missing()
        bugs_dir = _scratch_bugs_dir(tmp_path)
        changelog = _render_scratch_changelog(bugs_dir, tmp_path / "CHANGELOG.md")

        # Create a synthetic release-notes file whose version does not appear in
        # the scratch CHANGELOG.md, under an overlay directory of its own
        # (never the real release-notes/ directory).  Use a version that sorts
        # HIGHER than the current highest entry so it ends up at the top of the
        # regeneration, matching the real-world new-release-section pattern.
        synthetic_version = "v99.0.0"
        extra_release_notes_dir = tmp_path / "extra_release_notes"
        extra_release_notes_dir.mkdir()
        synthetic_notes = extra_release_notes_dir / f"{synthetic_version}.md"
        synthetic_notes.write_text(
            f"# Release Notes: pgai-agent-kanban {synthetic_version}\n\n"
            "**Release Date:** 2099-01-01\n"
            "**Released By:** test-fixture\n\n"
            "## Status\nRELEASED\n\n"
            "## Summary\n"
            "Synthetic release-notes file created by the unit test fixture "
            "(test_rc_mode_tolerates_new_release_section_addition).  "
            "This file must never appear in the real release-notes directory.\n",
            encoding="utf-8",
        )

        # Run in RC mode with the scratch ledger.  Patch _current_git_branch so
        # the branch-aware check in _is_rc_mode() treats this as an RC branch
        # regardless of the actual worktree branch (which may be ai_feature/*
        # during development).
        prev_mode = os.environ.get("PGAI_LINT_CHANGELOG_MODE", "")
        os.environ["PGAI_LINT_CHANGELOG_MODE"] = "rc"
        try:
            with unittest.mock.patch.object(
                _lint, "_current_git_branch", return_value="ai_rc/v1.23.10"
            ):
                result = _lint.check_freshness(
                    changelog_path=changelog,
                    bugs_dir=bugs_dir,
                    extra_release_notes_dir=extra_release_notes_dir,
                )
        finally:
            if prev_mode:
                os.environ["PGAI_LINT_CHANGELOG_MODE"] = prev_mode
            else:
                os.environ.pop("PGAI_LINT_CHANGELOG_MODE", None)

        captured = capsys.readouterr()
        assert result is True, (
            "check_freshness() returned False in RC mode when the only diff is a "
            "new ## vX.Y.Z section backed by a release-notes file on the branch.\n"
            "RC-mode tolerance (new-release-section clause) must pass in this case.\n"
            f"stdout: {captured.out!r}\n"
            f"stderr: {captured.err!r}"
        )
        assert "tolerat" in captured.out.lower() or "rc mode" in captured.out.lower(), (
            "check_freshness() did not mention RC mode or tolerance in its success "
            "message when the new-release-section clause fired.\n"
            f"stdout: {captured.out!r}"
        )


# ---------------------------------------------------------------------------
# Writer-migration drift tolerance tests
# ---------------------------------------------------------------------------


class TestWriterMigrationDriftTolerance:
    """RC-mode tolerance for the three writer-migration drift categories.

    _diff_is_rc_tolerable() must explicitly name and tolerate
    the three drift categories that recur between releases due to older writer
    rules being preserved in the committed CHANGELOG.md:

    Category 3 — Fenced ``## Fixed`` sub-section header:
        The older writer emitted ``## Fixed`` as a sub-section header; the
        current writer omits it.  In the diff: a ``-## Fixed`` removed line.

    Category 4 — Hyphen-prefixed KI-resolved line:
        The older writer emitted ``- KI-N.N.N.N — resolved`` for resolved
        Known Issues; the current writer emits a full-format KI title.
        In the diff: ``-- KI-N.N.N.N — resolved`` removed lines.

    Category 5 — Embedded triple-backtick code fence:
        The older writer embedded ``` fences inside KI descriptions and Fixed
        entries; the current writer strips them.  In the diff: ``-``` `` lines.

    Tests use _diff_is_rc_tolerable() directly (not check_freshness()) because:
    (a) they exercise the predicate logic, not the full regeneration pipeline;
    (b) they avoid needing the live bugs dir or PUBLISHED manifest.

    The fail-fast-on-ai_main test uses _is_rc_mode() directly and the
    _current_git_branch() / _is_rc_branch() helpers to verify the branch-aware
    suppression without spawning a subprocess.
    """

    # Minimal synthetic CHANGELOG base: a v1.0.0 entry with all three drift
    # patterns present.  A fresh regeneration of this changelog would NOT have
    # the drift lines (the current writer strips them).
    #
    # Category 3: line "## Fixed" (sub-section header, no content — just the header)
    # Category 4: lines "- KI-1.0.0.1 — resolved" (hyphen-prefix resolved-KI)
    # Category 5: triple-backtick fence lines wrapping embedded code in a KI entry;
    #             the content between the fences is also drift and also removed.
    #
    # The checked-in CHANGELOG has the drift patterns; the regenerated CHANGELOG
    # has them stripped.  The diff must be entirely explained by Categories 3/4/5.
    # Checked-in CHANGELOG with all three writer-migration drift patterns.
    # Structured so that ONLY the drift-category lines differ from the
    # regenerated version (no incidental blank-line drift):
    #   - "## Fixed" → Category 3 (sub-section header)
    #   - "```" / "error output here" / "```" → Category 5 (fence + content)
    #   - "- KI-1.0.0.2 — resolved" and "- KI-1.0.0.3 — resolved" → Category 4
    # The blank lines and section headers that ARE shared between the two
    # versions are kept identical so they appear as context lines in the diff.
    _CHECKED_IN_WITH_DRIFT = (
        "## v1.0.0 — 2026-01-01\n"
        "\n"
        "### Implemented\n"
        "- Feature A\n"
        "\n"
        "## Fixed\n"
        "### Known Issues\n"
        "KI-1.0.0.1 — symptom text; affects v1.0.0 · open\n"
        "```\n"
        "error output here\n"
        "```\n"
        "- KI-1.0.0.2 — resolved\n"
        "- KI-1.0.0.3 — resolved\n"
    )

    # Fresh regeneration without the three drift patterns.
    # The ONLY diff from _CHECKED_IN_WITH_DRIFT is the removal of:
    #   - "## Fixed" (Category 3)
    #   - "```", "error output here", "```" (Category 5 fence)
    #   - "- KI-1.0.0.2 — resolved", "- KI-1.0.0.3 — resolved" (Category 4)
    _REGENERATED_WITHOUT_DRIFT = (
        "## v1.0.0 — 2026-01-01\n"
        "\n"
        "### Implemented\n"
        "- Feature A\n"
        "\n"
        "### Known Issues\n"
        "KI-1.0.0.1 — symptom text; affects v1.0.0 · open\n"
    )

    def test_all_three_drift_categories_tolerated_in_rc_mode(self) -> None:
        """_diff_is_rc_tolerable() accepts a diff containing only the three drift removals.

        The checked-in CHANGELOG has all three writer-migration drift patterns
        (fenced ## Fixed header, hyphen-prefix KI-resolved lines, triple-backtick
        code fences); the regenerated CHANGELOG has them stripped.  RC-mode
        tolerance (Categories 3, 4, 5) must accept this diff and return True.

        RC-mode admission passes when the only diff is the three enumerated
        drift categories.
        """
        is_tolerable, clause = _lint._diff_is_rc_tolerable(
            self._CHECKED_IN_WITH_DRIFT,
            self._REGENERATED_WITHOUT_DRIFT,
        )
        assert is_tolerable is True, (
            "_diff_is_rc_tolerable() returned False for a diff containing only "
            "the three enumerated writer-migration drift categories.\n"
            "Categories 3 (## Fixed header removal), 4 (KI-resolved line removal), "
            "and 5 (triple-backtick fence removal) must all be tolerable.\n"
            f"Tolerance clause returned: {clause!r}"
        )
        assert "drift" in clause.lower() or "migration" in clause.lower(), (
            "_diff_is_rc_tolerable() returned True but the clause description does "
            "not mention drift or migration, suggesting a different clause fired.\n"
            f"Clause: {clause!r}"
        )

    def test_non_enumerated_drift_still_fails_in_rc_mode(self) -> None:
        """_diff_is_rc_tolerable() rejects a diff containing a non-enumerated removal.

        A removed line that is NOT one of the three enumerated drift categories
        must still cause _diff_is_rc_tolerable() to return False.  This verifies
        that the tolerance set is not over-broad: only Categories 3, 4, and 5
        are admitted; all other removals are genuine staleness.

        RC-mode must not over-broadly admit non-enumerated drift.
        """
        # The checked-in content has a non-drift removed line: a genuine content
        # line ("- Fixed item B") that is NOT one of the three drift categories.
        checked_in_with_genuine_removal = (
            "## v1.0.0 — 2026-01-01\n"
            "\n"
            "### Implemented\n"
            "- Feature A\n"
            "- Feature B (genuine content, will be removed in regen)\n"
            "\n"
            "### Known Issues\n"
        )
        # The fresh regeneration omits Feature B — a genuine content edit.
        regenerated_without_feature_b = (
            "## v1.0.0 — 2026-01-01\n"
            "\n"
            "### Implemented\n"
            "- Feature A\n"
            "\n"
            "### Known Issues\n"
        )
        is_tolerable, clause = _lint._diff_is_rc_tolerable(
            checked_in_with_genuine_removal,
            regenerated_without_feature_b,
        )
        assert is_tolerable is False, (
            "_diff_is_rc_tolerable() returned True for a diff containing a "
            "non-enumerated removed line ('- Feature B (genuine content...').\n"
            "Only the three enumerated drift categories are tolerable; any other "
            "removal must be treated as genuine staleness and rejected.\n"
            f"Clause returned: {clause!r}"
        )

    def test_fail_fast_on_ai_main_overrides_rc_env_var(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """_is_rc_mode() returns False on ai_main even when the env var is set to 'rc'.

        When PGAI_LINT_CHANGELOG_MODE=rc is set but the current git branch is
        ai_main (not rc/* or ai_rc/*), RC-mode tolerance must be suppressed.
        This is the fail-fast-on-ai_main behaviour: drift is caught before an
        RC opens rather than being masked whenever the check runs on an RC branch.

        The test monkeypatches _current_git_branch to return 'ai_main' and sets
        PGAI_LINT_CHANGELOG_MODE=rc, then asserts _is_rc_mode() returns False.

        The lint must exit non-zero (fail-fast) on ai_main for the same
        drift that RC-mode would otherwise tolerate.
        """
        monkeypatch.setenv("PGAI_LINT_CHANGELOG_MODE", "rc")
        # Patch _current_git_branch at the module level to return 'ai_main'.
        monkeypatch.setattr(
            _lint, "_current_git_branch", lambda _root: "ai_main"
        )
        result = _lint._is_rc_mode()
        assert result is False, (
            "_is_rc_mode() returned True when the branch is 'ai_main' even "
            "with PGAI_LINT_CHANGELOG_MODE=rc set in the environment.\n"
            "RC-mode tolerance must be suppressed on ai_main unconditionally.\n"
            "Branch-aware suppression: 'ai_main' does not match rc/* or ai_rc/*."
        )

    def test_rc_mode_active_on_rc_branch_with_env_var(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """_is_rc_mode() returns True on an ai_rc/* branch when env var is 'rc'.

        Complements the fail-fast-on-ai_main test: when both conditions are met
        (env var is 'rc' AND branch is ai_rc/*), _is_rc_mode() must return True.
        This ensures the branch-aware check does not over-suppress RC mode.
        """
        monkeypatch.setenv("PGAI_LINT_CHANGELOG_MODE", "rc")
        monkeypatch.setattr(
            _lint, "_current_git_branch", lambda _root: "ai_rc/v1.23.10"
        )
        result = _lint._is_rc_mode()
        assert result is True, (
            "_is_rc_mode() returned False for branch 'ai_rc/v1.23.10' with "
            "PGAI_LINT_CHANGELOG_MODE=rc set.\n"
            "RC-mode tolerance must be active when both conditions hold: "
            "the env var is 'rc' AND the branch matches the rc/* or ai_rc/* prefix."
        )

    def test_rc_mode_inactive_without_env_var_on_rc_branch(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """_is_rc_mode() returns False when env var is absent, even on an rc/* branch.

        If the operator runs the lint without setting
        PGAI_LINT_CHANGELOG_MODE=rc, RC-mode tolerance must not activate, even
        when the branch is rc/*.  This preserves strict byte-compare for direct
        invocations.
        """
        monkeypatch.delenv("PGAI_LINT_CHANGELOG_MODE", raising=False)
        monkeypatch.setattr(
            _lint, "_current_git_branch", lambda _root: "ai_rc/v1.23.10"
        )
        result = _lint._is_rc_mode()
        assert result is False, (
            "_is_rc_mode() returned True for branch 'ai_rc/v1.23.10' WITHOUT "
            "PGAI_LINT_CHANGELOG_MODE=rc set in the environment.\n"
            "RC-mode tolerance requires BOTH conditions: the env var AND an RC branch. "
            "Without the env var, even an RC branch must use strict byte-compare."
        )


# ---------------------------------------------------------------------------
# _is_rc_branch() prefix-awareness tests
# ---------------------------------------------------------------------------


class TestIsRcBranchPrefixAware:
    """_is_rc_branch() must recognize a project's configured branch_prefix
    alongside the bare 'rc/' form, not just the literal 'ai_' this repo
    happens to ship with."""

    def test_prefixed_branch_matches_its_own_configured_prefix(self) -> None:
        assert _lint._is_rc_branch("foo_rc/v1.0.0", branch_prefix="foo_") is True

    def test_bare_rc_branch_matches_regardless_of_configured_prefix(self) -> None:
        assert _lint._is_rc_branch("rc/v1.0.0", branch_prefix="foo_") is True

    def test_bare_rc_branch_matches_with_no_prefix_configured(self) -> None:
        assert _lint._is_rc_branch("rc/v1.0.0", branch_prefix="") is True

    def test_unrelated_branch_never_matches(self) -> None:
        assert _lint._is_rc_branch("feature/x", branch_prefix="foo_") is False
        assert _lint._is_rc_branch("feature/x", branch_prefix="") is False

    def test_prefix_does_not_match_a_different_projects_prefix(self) -> None:
        """A branch prefixed for one project's configured prefix must not
        match when a different prefix is asserted -- the alternative added
        is the configured prefix, not an open wildcard."""
        assert _lint._is_rc_branch("ai_rc/v1.0.0", branch_prefix="foo_") is False


# ---------------------------------------------------------------------------
# Detached-HEAD _current_git_branch() fallback tests
# ---------------------------------------------------------------------------


class TestCurrentGitBranchDetachedHead:
    """Unit tests for _current_git_branch() detached-HEAD sentinel-skip logic.

    Tests cover three scenarios, all mocking subprocess.run so no live git
    repository is required:

    1. Sentinel-skip + rc-preference: symbolic-ref fails, 'git branch --points-at
       HEAD' emits '(no branch)' then 'ai_rc/v1.23.11' — assert the function
       returns 'ai_rc/v1.23.11' (skips sentinel, returns rc-prefixed branch).

    2. No rc-branch fallback: same failure scenario but 'git branch --points-at
       HEAD' emits '(no branch)' then 'feature/foo' — assert the function
       returns '' (no rc-prefixed branch at HEAD, byte-exact fallback preserved).

    3. Symbolic-ref success path (unchanged): symbolic-ref succeeds and emits
       'ai_rc/v1.23.11' — assert the function returns 'ai_rc/v1.23.11' without
       reaching the fallback loop.

    Mocking strategy: _current_git_branch() uses 'import subprocess as _sp'
    inside the function body.  The local _sp is the cached subprocess module
    from sys.modules, so patching 'subprocess.run' intercepts the _sp.run call.
    """

    def _make_run_side_effect(self, symbolic_ref_output, symbolic_ref_rc,
                              points_at_output, points_at_rc):
        """Return a side_effect callable for subprocess.run mock.

        The side_effect dispatches on the command list to return the appropriate
        CompletedProcess for each of the two git calls inside _current_git_branch().

        Args:
            symbolic_ref_output: stdout for 'git symbolic-ref --short HEAD'.
            symbolic_ref_rc:     returncode for the symbolic-ref call.
            points_at_output:    stdout for 'git branch --points-at HEAD'.
            points_at_rc:        returncode for the points-at call.

        Returns:
            A callable suitable as unittest.mock.patch side_effect.
        """
        import subprocess

        def _side_effect(cmd, **kwargs):
            if "symbolic-ref" in cmd:
                return subprocess.CompletedProcess(
                    args=cmd,
                    returncode=symbolic_ref_rc,
                    stdout=symbolic_ref_output,
                    stderr="",
                )
            # 'git branch --points-at HEAD'
            return subprocess.CompletedProcess(
                args=cmd,
                returncode=points_at_rc,
                stdout=points_at_output,
                stderr="",
            )

        return _side_effect

    def test_sentinel_skipped_rc_branch_returned(self) -> None:
        """(no branch) sentinel is skipped; ai_rc/v1.23.11 is returned.

        Simulates a detached-HEAD worktree where 'git symbolic-ref --short HEAD'
        fails (exit 128) and 'git branch --format=%(refname:short) --points-at HEAD'
        emits '(no branch)\\nai_rc/v1.23.11\\n'.

        _current_git_branch() must skip '(no branch)' (starts with '('), find
        'ai_rc/v1.23.11', confirm _is_rc_branch() returns True, and return
        'ai_rc/v1.23.11'.
        """
        side_effect = self._make_run_side_effect(
            symbolic_ref_output="",
            symbolic_ref_rc=128,
            points_at_output="(no branch)\nai_rc/v1.23.11\n",
            points_at_rc=0,
        )
        with unittest.mock.patch("subprocess.run", side_effect=side_effect):
            result = _lint._current_git_branch(pathlib.Path("."))

        assert result == "ai_rc/v1.23.11", (
            "_current_git_branch() returned {result!r} instead of 'ai_rc/v1.23.11' "
            "when 'git branch --points-at HEAD' emitted '(no branch)\\nai_rc/v1.23.11\\n'.\n"
            "The '(no branch)' sentinel line must be skipped; the rc-prefixed branch "
            "that follows must be returned."
        )

    def test_no_rc_branch_returns_empty_string(self) -> None:
        """Empty string is returned when no rc-prefixed branch is found at HEAD.

        Simulates a detached-HEAD worktree where 'git branch --points-at HEAD'
        emits '(no branch)\\nfeature/foo\\n' — there is no rc/* or ai_rc/* branch.

        _current_git_branch() must skip '(no branch)', skip 'feature/foo'
        (not rc-prefixed per _is_rc_branch), and return '' so that
        _is_rc_mode() stays False (byte-exact fallback preserved).
        """
        side_effect = self._make_run_side_effect(
            symbolic_ref_output="",
            symbolic_ref_rc=128,
            points_at_output="(no branch)\nfeature/foo\n",
            points_at_rc=0,
        )
        with unittest.mock.patch("subprocess.run", side_effect=side_effect):
            result = _lint._current_git_branch(pathlib.Path("."))

        assert result == "", (
            f"_current_git_branch() returned {result!r} instead of '' "
            "when 'git branch --points-at HEAD' emitted '(no branch)\\nfeature/foo\\n'.\n"
            "No rc-prefixed branch is present at HEAD; the function must return '' "
            "so that _is_rc_mode() stays False for feature branches and unrelated worktrees."
        )

    def test_symbolic_ref_success_path_unchanged(self) -> None:
        """Primary symbolic-ref success path returns the branch name unchanged.

        When 'git symbolic-ref --short HEAD' succeeds (exit 0) and emits
        'ai_rc/v1.23.11', _current_git_branch() must return 'ai_rc/v1.23.11'
        directly without reaching the detached-HEAD fallback loop.

        This test verifies the detached-HEAD fallback does not regress the non-detached case.
        """
        side_effect = self._make_run_side_effect(
            symbolic_ref_output="ai_rc/v1.23.11\n",
            symbolic_ref_rc=0,
            # The points-at call must NOT be reached; provide a clearly wrong
            # value so a regression would produce an obviously wrong result.
            points_at_output="(no branch)\nwrong-branch\n",
            points_at_rc=0,
        )
        with unittest.mock.patch("subprocess.run", side_effect=side_effect):
            result = _lint._current_git_branch(pathlib.Path("."))

        assert result == "ai_rc/v1.23.11", (
            f"_current_git_branch() returned {result!r} instead of 'ai_rc/v1.23.11' "
            "when 'git symbolic-ref --short HEAD' succeeded with 'ai_rc/v1.23.11'.\n"
            "The primary symbolic-ref success path must return the branch name as-is "
            "without entering the detached-HEAD fallback loop."
        )


# ---------------------------------------------------------------------------
# RC-mode tolerance: an empty Known Issues placeholder replaced by its first entry
# ---------------------------------------------------------------------------


class TestEmptyKnownIssuesPlaceholderReplacement:
    """RC-mode tolerance for a ``None`` placeholder that a first entry replaces.

    The writer renders ``None`` under ``### Known Issues`` when a version has
    no disclosed bug.  A bug filed post-commit against that version turns the
    placeholder into a KI line, so the diff shows one removed ``None`` and one
    added KI line in its place.  _diff_is_rc_tolerable() must admit exactly
    that shape and nothing wider: the removal is tolerable only directly under
    a Known Issues heading and only when the very next diff line is a KI entry
    whose Public ID the checked-in artifact does not already carry.

    Tests use _diff_is_rc_tolerable() directly, as the drift-category tests
    do, because they exercise the predicate rather than the regeneration
    pipeline.
    """

    _CHECKED_IN_EMPTY_SECTION = (
        "## v1.0.0 — 2026-01-01\n"
        "\n"
        "### Implemented\n"
        "- Feature A\n"
        "\n"
        "### Fixed\n"
        "None\n"
        "\n"
        "### Known Issues\n"
        "None\n"
        "\n"
        "*Details: [release-notes/v1.0.0.md](release-notes/v1.0.0.md)*\n"
    )

    @staticmethod
    def _with_known_issues(*lines: str) -> str:
        """The checked-in text with the Known Issues placeholder replaced by *lines*."""
        body = "\n".join(lines) + "\n"
        return TestEmptyKnownIssuesPlaceholderReplacement._CHECKED_IN_EMPTY_SECTION.replace(
            "### Known Issues\nNone\n", "### Known Issues\n" + body
        )

    def test_placeholder_replaced_by_first_new_entry_is_tolerated(self) -> None:
        """A ``None`` under Known Issues replaced by a new-ID KI line is admitted."""
        regenerated = self._with_known_issues(
            "KI-1.0.0.1 — symptom text; affects v1.0.0 · open"
        )
        is_tolerable, clause = _lint._diff_is_rc_tolerable(
            self._CHECKED_IN_EMPTY_SECTION, regenerated
        )
        assert is_tolerable is True, (
            "_diff_is_rc_tolerable() rejected the one-line replacement of an "
            "empty Known Issues placeholder by a KI entry whose ID the checked-in "
            "artifact does not carry.  That is the shape every first post-commit "
            "bug against a previously clean version produces.\n"
            f"Clause returned: {clause!r}"
        )
        assert "placeholder" in clause.lower(), (
            "_diff_is_rc_tolerable() admitted the diff but the clause description "
            "does not name the placeholder replacement, so the log would not say "
            f"why the gate passed.\nClause: {clause!r}"
        )

    def test_placeholder_replaced_by_several_new_entries_is_tolerated(self) -> None:
        """Two bugs filed against the same clean version both stand in for ``None``."""
        regenerated = self._with_known_issues(
            "KI-1.0.0.1 — first symptom; affects v1.0.0 · open",
            "KI-1.0.0.2 — second symptom; affects v1.0.0 · open",
        )
        is_tolerable, clause = _lint._diff_is_rc_tolerable(
            self._CHECKED_IN_EMPTY_SECTION, regenerated
        )
        assert is_tolerable is True, (
            "_diff_is_rc_tolerable() rejected a placeholder replaced by two new "
            "KI entries; the first entry replaces the placeholder and the second "
            f"is an ordinary Category 1 addition.\nClause returned: {clause!r}"
        )

    def test_placeholder_replaced_by_an_already_published_id_is_rejected(self) -> None:
        """A KI line whose ID the artifact already carries is not a first entry."""
        checked_in = self._CHECKED_IN_EMPTY_SECTION.replace(
            "### Fixed\nNone\n",
            "### Fixed\n- KI-1.0.0.1 — resolved (the original meaning)\n",
        )
        regenerated = checked_in.replace(
            "### Known Issues\nNone\n",
            "### Known Issues\nKI-1.0.0.1 — a different symptom; affects v1.0.0 · open\n",
        )
        is_tolerable, clause = _lint._diff_is_rc_tolerable(checked_in, regenerated)
        assert is_tolerable is False, (
            "_diff_is_rc_tolerable() admitted a placeholder replaced by a KI line "
            "whose Public ID the checked-in artifact already publishes elsewhere.  "
            "A reissued identifier is genuine staleness, never a first entry.\n"
            f"Clause returned: {clause!r}"
        )

    def test_placeholder_removed_without_a_replacement_is_rejected(self) -> None:
        """A ``None`` that simply disappears is missing content."""
        regenerated = self._CHECKED_IN_EMPTY_SECTION.replace(
            "### Known Issues\nNone\n", "### Known Issues\n"
        )
        is_tolerable, clause = _lint._diff_is_rc_tolerable(
            self._CHECKED_IN_EMPTY_SECTION, regenerated
        )
        assert is_tolerable is False, (
            "_diff_is_rc_tolerable() admitted the removal of a Known Issues "
            "placeholder that nothing replaced.  Only a KI entry standing directly "
            f"in its place is tolerable.\nClause returned: {clause!r}"
        )

    def test_placeholder_replaced_by_prose_is_rejected(self) -> None:
        """Only a KI entry may stand where the placeholder was."""
        regenerated = self._with_known_issues("Nothing to see here")
        is_tolerable, clause = _lint._diff_is_rc_tolerable(
            self._CHECKED_IN_EMPTY_SECTION, regenerated
        )
        assert is_tolerable is False, (
            "_diff_is_rc_tolerable() admitted a Known Issues placeholder replaced "
            "by a line that is not a KI entry.  That is a hand edit, not a "
            f"post-commit ledger addition.\nClause returned: {clause!r}"
        )

    def test_placeholder_under_another_heading_is_rejected(self) -> None:
        """The tolerance is scoped to Known Issues; a Fixed ``None`` is content."""
        regenerated = self._CHECKED_IN_EMPTY_SECTION.replace(
            "### Fixed\nNone\n",
            "### Fixed\nKI-1.0.0.1 — symptom text; affects v1.0.0 · open\n",
        )
        is_tolerable, clause = _lint._diff_is_rc_tolerable(
            self._CHECKED_IN_EMPTY_SECTION, regenerated
        )
        assert is_tolerable is False, (
            "_diff_is_rc_tolerable() admitted a ``None`` removed from a Fixed "
            "section.  Fixed lines come from release notes, not the bug ledger, "
            "so a changed Fixed section is genuine staleness whatever replaced "
            f"the placeholder.\nClause returned: {clause!r}"
        )

    # A whole-file fixture of the shape a published version with a clean
    # Known Issues section renders as, and the one-line change a single bug
    # filed against it afterwards produces.
    _FIRST_ENTRY_HEAD = (
        "# Changelog\n\n---\n\n## v9.9.9 — 2026-01-01\n\n"
        "### Fixed\n- something\n\n### Known Issues\n"
    )
    _FIRST_ENTRY_TAIL = "\n*Details: [release-notes/v9.9.9.md](release-notes/v9.9.9.md)*\n"
    _FIRST_ENTRY_KI = "KI-9.9.9.1 — `x.md`: first bug; affects v9.9.9 · open\n"

    def _first_entry_pair(self) -> tuple[str, str]:
        """Return (checked-in, regenerated) for a placeholder replaced by one entry."""
        checked_in = self._FIRST_ENTRY_HEAD + "None\n" + self._FIRST_ENTRY_TAIL
        regenerated = self._FIRST_ENTRY_HEAD + self._FIRST_ENTRY_KI + self._FIRST_ENTRY_TAIL
        return checked_in, regenerated

    def test_first_entry_on_whole_file_fixture_is_tolerated(self) -> None:
        """The full-file first-entry shape returns True with a non-empty clause."""
        checked_in, regenerated = self._first_entry_pair()
        is_tolerable, clause = _lint._diff_is_rc_tolerable(checked_in, regenerated)
        assert is_tolerable is True and clause, (
            "_diff_is_rc_tolerable() did not admit a whole CHANGELOG in which "
            "one version's Known Issues placeholder became its first KI entry.\n"
            f"Result: {(is_tolerable, clause)!r}"
        )

    def test_placeholder_replaced_by_entry_then_prose_is_rejected(self) -> None:
        """Every line filling the emptied section must be a KI entry, not just the first."""
        checked_in, _ = self._first_entry_pair()
        regenerated = (
            self._FIRST_ENTRY_HEAD
            + self._FIRST_ENTRY_KI
            + "Hand-written note\n"
            + self._FIRST_ENTRY_TAIL
        )
        is_tolerable, clause = _lint._diff_is_rc_tolerable(checked_in, regenerated)
        assert is_tolerable is False, (
            "_diff_is_rc_tolerable() admitted a Known Issues placeholder replaced "
            "by a KI entry followed by a non-KI line.  Only ledger entries may "
            f"fill the section.\nClause returned: {clause!r}"
        )

    def test_placeholder_removed_with_a_second_line_is_rejected(self) -> None:
        """A removal beside the placeholder is not covered by the replacement."""
        checked_in = (
            self._FIRST_ENTRY_HEAD + "None\n```\n" + self._FIRST_ENTRY_TAIL
        )
        _, regenerated = self._first_entry_pair()
        is_tolerable, clause = _lint._diff_is_rc_tolerable(checked_in, regenerated)
        assert is_tolerable is False, (
            "_diff_is_rc_tolerable() admitted a diff removing the placeholder "
            "and a further line before the new entry.  The tolerance covers the "
            f"placeholder alone.\nClause returned: {clause!r}"
        )

    def _run_check_freshness(
        self,
        tmp_path: pathlib.Path,
        monkeypatch: pytest.MonkeyPatch,
        rc_mode: bool,
    ) -> tuple[bool, pathlib.Path, str, str]:
        """Run check_freshness() on the first-entry fixture with a stubbed regeneration.

        The regeneration step is replaced by one that writes the fixture's
        regenerated text, so the gate compares exactly the placeholder
        replacement and nothing from the real release notes or ledger.

        Returns:
            (result, changelog_path, checked_in_text, regenerated_text).
        """
        checked_in, regenerated = self._first_entry_pair()
        changelog_path = tmp_path / "CHANGELOG.md"
        changelog_path.write_text(checked_in, encoding="utf-8")

        def _fake_regenerate(temp_root, bugs_dir, extra_release_notes_dir=None,
                             published_changelog=None):
            out = tmp_path / "regenerated.md"
            out.write_text(regenerated, encoding="utf-8")
            return out

        monkeypatch.setattr(_lint, "_regenerate_to_temp", _fake_regenerate)
        monkeypatch.setattr(_lint, "_is_rc_mode", lambda: rc_mode)
        result = _lint.check_freshness(changelog_path=changelog_path, bugs_dir=tmp_path)
        return result, changelog_path, checked_in, regenerated

    def test_strict_mode_reports_placeholder_replacement_unchanged(
        self,
        tmp_path: pathlib.Path,
        monkeypatch: pytest.MonkeyPatch,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        """Strict mode fails the first-entry fixture with the plain stale report."""
        result, changelog_path, checked_in, regenerated = self._run_check_freshness(
            tmp_path, monkeypatch, rc_mode=False
        )
        captured = capsys.readouterr()
        expected = _lint._format_remedy_block(
            fail_summary=f"{changelog_path} is stale",
            causes=_lint._build_causes(changelog_path, checked_in, regenerated),
            changelog_path=changelog_path,
            bugs_dir=tmp_path,
        )
        assert result is False, (
            "check_freshness() passed the placeholder-replacement fixture in "
            "strict mode; strict mode byte-compares and must report it stale.\n"
            f"stdout: {captured.out!r}"
        )
        assert captured.err == expected + "\n", (
            "Strict mode's report for the placeholder-replacement fixture is not "
            "the plain stale-artifact remedy block; the RC-mode tolerance must "
            "not leak into strict output.\n"
            f"stderr: {captured.err!r}"
        )
        assert captured.out == "", f"Strict mode printed to stdout: {captured.out!r}"

    def test_rc_mode_check_freshness_passes_placeholder_replacement(
        self,
        tmp_path: pathlib.Path,
        monkeypatch: pytest.MonkeyPatch,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        """RC mode passes the first-entry fixture end to end and names why."""
        result, _, _, _ = self._run_check_freshness(tmp_path, monkeypatch, rc_mode=True)
        captured = capsys.readouterr()
        assert result is True, (
            "check_freshness() in RC mode rejected a CHANGELOG whose only change "
            "is a Known Issues placeholder replaced by a first KI entry.\n"
            f"stderr: {captured.err!r}"
        )
        assert "placeholder" in captured.out, (
            "RC mode passed but its ok line does not name the placeholder "
            f"replacement.\nstdout: {captured.out!r}"
        )
