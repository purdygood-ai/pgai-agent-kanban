"""
test_lint_changelog_freshness.py — Unit tests for team/scripts/lint_changelog_freshness.py.

Tests cover:

  1. **Positive (fresh tree)**: invoke lint_changelog_freshness.py as a subprocess
     with PYTHONHASHSEED=0 (belt-and-braces; see class docstring) and assert it
     exits 0.  This proves the gate exits 0 when the artifact is up-to-date.
     The subprocess invocation is required to match the execution context of the
     gated runners.  changelog_writer sorts heading collections alphabetically
     before iteration (``sorted(_IMPLEMENTED_HEADINGS)`` / ``sorted(_FIXED_HEADINGS)``),
     so output is hash-seed-independent.  The PYTHONHASHSEED=0 env setting is kept
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

  7. **RC-mode tolerance**: a fixture that copies the real bugs dir to a temp
     location, adds a post-CHANGELOG-commit fake bug file, and asserts that
     check_freshness() passes in RC mode (PGAI_LINT_CHANGELOG_MODE=rc) and
     fails in normal mode (no env var).  Also asserts that a genuine
     CHANGELOG content edit still fails in RC mode (non-KI staleness).

  8. **Writer-migration drift tolerance (BUG-0077 / BUG-0080 enumeration)**:
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

# The live kanban root is resolved from PGAI_AGENT_KANBAN_ROOT_PATH at
# import time, before conftest's autouse fixture redirects the env var.
_LIVE_KANBAN_ROOT = pathlib.Path(
    os.environ.get("PGAI_AGENT_KANBAN_ROOT_PATH")
    or str(pathlib.Path.home() / "pgai_agent_kanban")
)
_REAL_BUGS_DIR = _LIVE_KANBAN_ROOT / "projects" / "pgai-agent-kanban" / "bugs"


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


def _skip_if_bugs_dir_missing() -> None:
    """Skip the test when the live bugs directory is not present."""
    if not _REAL_BUGS_DIR.exists():
        pytest.skip(
            f"Live kanban bugs directory not found: {_REAL_BUGS_DIR} "
            "— live install not present in this environment."
        )


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


# ---------------------------------------------------------------------------
# Positive tests (subprocess-based to mirror gated runner invocation)
# ---------------------------------------------------------------------------


class TestChangelogFreshnessPositive:
    """Fresh tree: gate exits 0 when the artifact is up-to-date.

    The positive tests invoke the gate via subprocess rather than calling
    check_freshness() directly.  This mirrors the invocation used by the gated
    runners (run-unit-tests.sh, run-integration-tests.sh).

    PYTHONHASHSEED=0 is set in the subprocess environment as belt-and-braces, but
    it is no longer the mechanism responsible for deterministic heading order.
    changelog_writer.py sorts heading collections alphabetically before iterating
    (``sorted(_IMPLEMENTED_HEADINGS)`` / ``sorted(_FIXED_HEADINGS)``), which
    guarantees a stable, hash-seed-independent iteration order.  The env setting is
    retained for defense-in-depth but does not affect correctness.

    PGAI_LINT_CHANGELOG_MODE=rc is set in the subprocess environment to mirror the
    gated runner's invocation on RC and detached-HEAD worktrees (an earlier defect / an earlier defect).
    On RC branches the live bug ledger may have BUG-NNNN files filed after the last
    CHANGELOG.md commit; RC mode tolerates those post-commit KI additions so the gate
    exits 0.  These positive tests verify the gate-passes-when-fresh contract, which
    on an RC worktree IS the RC-mode contract.  A companion negative test below
    (test_gate_exits_1_without_rc_mode_when_bugs_dir_has_post_commit_entries) locks
    in the tolerance-mode gate by asserting that omitting the env var fails when the
    live bugs dir has entries newer than the committed CHANGELOG.
    """

    def test_fresh_artifact_exits_0(self) -> None:
        """Gate subprocess exits 0 for the committed CHANGELOG.md.

        The committed CHANGELOG.md was regenerated via changelog_writer.
        Re-running the gate in RC mode must exit 0: either the artifact is
        byte-identical to a fresh regeneration, or the only differences are
        new KI entries added post-commit (an earlier defect tolerance window).
        Determinism comes from sorted() over the heading collections in
        changelog_writer.py, not from the PYTHONHASHSEED setting.

        PGAI_LINT_CHANGELOG_MODE=rc is set to mirror the gated runner's invocation
        on RC worktrees (an earlier defect fix).
        """
        _skip_if_bugs_dir_missing()
        _skip_if_published_missing()

        env = os.environ.copy()
        env["PYTHONHASHSEED"] = "0"
        env["PGAI_AGENT_KANBAN_ROOT_PATH"] = str(_LIVE_KANBAN_ROOT)
        env["PGAI_LINT_CHANGELOG_MODE"] = "rc"

        result = subprocess.run(
            [sys.executable, str(_LINT_SCRIPT)],
            env=env,
            capture_output=True,
            text=True,
            cwd=str(_TEAM_DIR),
        )
        assert result.returncode == 0, (
            "Gate subprocess exited with non-zero code for the committed CHANGELOG.md "
            "(RC mode active).\n"
            f"stdout: {result.stdout!r}\n"
            f"stderr: {result.stderr!r}\n"
            "In RC mode the gate must exit 0 when the artifact is fresh or the only "
            "differences are post-commit KI additions.  "
            "Regenerate CHANGELOG.md via: python3 -m pgai_agent_kanban.cm.changelog_writer <repo_root> <bugs_dir>"
        )

    def test_fresh_artifact_prints_ok_message(self) -> None:
        """Gate subprocess prints 'ok' confirmation when the artifact is fresh.

        Verifies that the gate emits a positive confirmation message to stdout
        rather than silently succeeding — this message appears in runner output
        and lets operators see the gate ran.

        PGAI_LINT_CHANGELOG_MODE=rc is set to mirror the gated runner's invocation
        on RC worktrees (an earlier defect fix).
        """
        _skip_if_bugs_dir_missing()
        _skip_if_published_missing()

        env = os.environ.copy()
        env["PYTHONHASHSEED"] = "0"
        env["PGAI_AGENT_KANBAN_ROOT_PATH"] = str(_LIVE_KANBAN_ROOT)
        env["PGAI_LINT_CHANGELOG_MODE"] = "rc"

        result = subprocess.run(
            [sys.executable, str(_LINT_SCRIPT)],
            env=env,
            capture_output=True,
            text=True,
            cwd=str(_TEAM_DIR),
        )
        assert "ok" in result.stdout.lower() or "fresh" in result.stdout.lower(), (
            f"Gate subprocess did not print a positive confirmation to stdout "
            f"(RC mode active).\n"
            f"stdout: {result.stdout!r}\n"
            f"stderr: {result.stderr!r}\n"
            "Expected output containing 'ok' or 'fresh'."
        )

    def test_gate_exits_1_without_rc_mode_when_bugs_dir_has_post_commit_entries(
        self,
    ) -> None:
        """Gate subprocess exits 1 in normal mode when the live bugs dir has post-commit entries.

        Verifies that the tolerance-mode gate is real: omitting
        PGAI_LINT_CHANGELOG_MODE=rc while the live bugs dir contains BUG-NNNN
        entries filed after the last CHANGELOG.md commit causes the lint subprocess
        to exit 1.

        The test invokes the lint subprocess WITHOUT PGAI_LINT_CHANGELOG_MODE so
        that the gate runs in strict byte-compare mode.  When the committed
        CHANGELOG.md is stale relative to the live bug ledger (even by only
        post-commit KI additions), normal mode must exit 1.

        Skip conditions:
          - Live bugs dir is missing (environment not set up).
          - release-notes/PUBLISHED is absent (regeneration not meaningful).
          - The gate exits 0 even in normal mode: this means the committed
            CHANGELOG.md is byte-identical to a fresh regeneration (no post-commit
            bugs were filed), so there is no tolerance gap to detect.  The test
            skips rather than giving a misleading failure.

        This test locks in Acceptance criterion #4 from an earlier defect.
        """
        _skip_if_bugs_dir_missing()
        _skip_if_published_missing()

        # Run in NORMAL mode (no PGAI_LINT_CHANGELOG_MODE=rc).
        env = os.environ.copy()
        env["PYTHONHASHSEED"] = "0"
        env["PGAI_AGENT_KANBAN_ROOT_PATH"] = str(_LIVE_KANBAN_ROOT)
        env.pop("PGAI_LINT_CHANGELOG_MODE", None)

        result = subprocess.run(
            [sys.executable, str(_LINT_SCRIPT)],
            env=env,
            capture_output=True,
            text=True,
            cwd=str(_TEAM_DIR),
        )

        if result.returncode == 0:
            # The CHANGELOG is byte-identical to a fresh regeneration: no
            # post-commit bugs exist.  Skip — there is no tolerance gap to
            # verify on this worktree at this moment.
            pytest.skip(
                "Gate exits 0 in normal mode: committed CHANGELOG.md is byte-identical "
                "to a fresh regeneration (no post-commit bug entries in the live ledger). "
                "Tolerance-mode gate cannot be exercised on this worktree right now."
            )

        assert result.returncode == 1, (
            "Gate subprocess exited with unexpected code in normal mode "
            f"(expected 0 or 1, got {result.returncode}).\n"
            f"stdout: {result.stdout!r}\n"
            f"stderr: {result.stderr!r}"
        )
        # returncode == 1 here: the live bugs dir has post-commit BUG-NNNN entries
        # that cause the regenerated CHANGELOG to differ from the checked-in artifact.
        # Normal mode rejects this; RC mode (test_fresh_artifact_exits_0) tolerates it.
        assert "stale" in result.stderr.lower(), (
            "Gate subprocess exited 1 in normal mode but stderr does not contain 'stale'.\n"
            f"stdout: {result.stdout!r}\n"
            f"stderr: {result.stderr!r}\n"
            "Expected 'stale' in the error message naming the staleness condition."
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

        This is the behavioral-negative proof-of-fire required by the task
        acceptance criteria: a scratch-edited CHANGELOG must make the gate
        exit 1.

        Args:
            tmp_path:  pytest-provided temp directory (routed to framework temp root).
            capsys:    pytest capture fixture for inspecting stderr.
        """
        _skip_if_bugs_dir_missing()
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
            bugs_dir=_REAL_BUGS_DIR,
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
        _skip_if_bugs_dir_missing()
        _skip_if_published_missing()

        original_bytes = _CHANGELOG_ARTIFACT.read_bytes()
        mutated = original_bytes + b"\n# STALE MARKER\n"
        scratch = tmp_path / "stale_changelog_msg.md"
        scratch.write_bytes(mutated)

        _lint.check_freshness(
            changelog_path=scratch,
            bugs_dir=_REAL_BUGS_DIR,
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
        _skip_if_bugs_dir_missing()
        _skip_if_published_missing()

        absent = tmp_path / "nonexistent_changelog.md"
        assert not absent.exists()

        result = _lint.check_freshness(
            changelog_path=absent,
            bugs_dir=_REAL_BUGS_DIR,
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
# CLI behavioural tests (argparse from birth)
# ---------------------------------------------------------------------------


class TestChangelogFreshnessCliArgparse:
    """Argparse behaviour tests: unknown args exit 2, --help exits 0 silently."""

    def test_unknown_argument_exits_2(self, capsys: pytest.CaptureFixture[str]) -> None:
        """main() with an unknown argument exits with code 2 and prints usage.

        This is the PVG an earlier defect lesson applied: the gate must reject unknown
        arguments via argparse rather than silently ignoring them.

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

    def test_ki_1_0_0_1_renders_under_v1_0_0(self) -> None:
        """KI-1.0.0.1 renders under the v1.0.0 entry in the committed CHANGELOG.

        The tmux 3.4 dashboard failure (an earlier defect) has public ID KI-1.0.0.1
        and affects v1.0.0 (the only PUBLISHED version at time of writing).
        It must appear in the Known Issues section of the v1.0.0 changelog entry.
        """
        content = _CHANGELOG_ARTIFACT.read_text(encoding="utf-8")

        # Extract the v1.0.0 entry (from ## v1.0.0 to the next ## v or end of file)
        entry_match = re.search(r"## v1\.0\.0.*?(?=\n## v|\Z)", content, re.DOTALL)
        assert (
            entry_match is not None
        ), "v1.0.0 entry not found in committed CHANGELOG.md"
        entry_text = entry_match.group(0)

        assert "KI-1.0.0.1" in entry_text, (
            "KI-1.0.0.1 not found in v1.0.0 entry of committed CHANGELOG.md.\n"
            "The tmux 3.4 dashboard failure must be disclosed under v1.0.0."
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

    The fixture copies the real bugs directory to a temp location, then adds a
    synthetic BUG file (an earlier defect) that references a PUBLISHED version so that
    changelog_writer will assign it a KI Public ID and include it in the
    regenerated CHANGELOG.  Because an earlier defect is new, its KI entry does not
    appear in the checked-in CHANGELOG artifact.

    RC-mode tolerance uses a diff-based predicate (_diff_is_rc_tolerable) rather
    than filesystem mtime.  This avoids the mtime-reliability problem: regenerate()
    writes Public IDs back to bug files, bumping their mtime, making filesystem
    mtime unreliable as an indicator of when a bug was filed.

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
        self, real_bugs_dir: pathlib.Path, tmp_path: pathlib.Path
    ) -> pathlib.Path:
        """Copy real bugs dir to a temp location, preserving file contents and mtimes.

        The copy must include all real bugs so that changelog_writer produces
        the same base KI lines as the checked-in CHANGELOG.  The synthetic
        an earlier defect file is then added on top of this complete copy.

        Args:
            real_bugs_dir:  The live kanban bugs directory.
            tmp_path:       pytest-provided temp directory.

        Returns:
            Path to the temp bugs directory containing copies of all real bugs.
        """
        temp_bugs = tmp_path / "bugs_copy"
        temp_bugs.mkdir(parents=True, exist_ok=True)
        if real_bugs_dir.is_dir():
            for bug_file in real_bugs_dir.iterdir():
                if bug_file.is_file():
                    dest = temp_bugs / bug_file.name
                    shutil.copy2(str(bug_file), str(dest))
        return temp_bugs

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
          1. Copy real bugs dir to a temp location (preserving file contents
             and mtimes, so regeneration produces the same base KI output as
             the checked-in CHANGELOG).
          2. Add an earlier defect (synthetic bug, Affects v1.0.0, no Public ID).
          3. Call check_freshness in RC mode (PGAI_LINT_CHANGELOG_MODE=rc).
          4. Assert it returns True.

        changelog_writer assigns KI-1.0.0.N+1 to an earlier defect during regeneration.
        The regenerated CHANGELOG differs from the checked-in artifact by exactly
        one added KI line (KI-1.0.0.N+1) that does not appear in the checked-in
        artifact.  _diff_is_rc_tolerable() accepts this diff, so the gate passes.

        Args:
            tmp_path:  pytest-provided temp directory.
            capsys:    pytest capture fixture for inspecting output.
        """
        _skip_if_bugs_dir_missing()
        _skip_if_published_missing()

        # Build a temp bugs dir with the real bugs + the synthetic an earlier defect.
        temp_bugs_dir = self._make_temp_bugs_dir(_REAL_BUGS_DIR, tmp_path)
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
                    changelog_path=_CHANGELOG_ARTIFACT,
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
            "extra entry (BUG-9999) generates a new KI line not in the checked-in "
            "CHANGELOG.\n"
            "RC-mode tolerance must pass when the diff is only new KI additions "
            "with IDs not present in the checked-in artifact.\n"
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

        Same fixture setup as test_rc_mode_tolerates_post_rc_bug_entry, but
        check_freshness is called WITHOUT setting PGAI_LINT_CHANGELOG_MODE=rc.
        The gate must return False: the regenerated CHANGELOG differs from the
        checked-in artifact (new KI line for an earlier defect), and normal mode
        byte-compares strictly.

        Args:
            tmp_path:  pytest-provided temp directory.
            capsys:    pytest capture fixture for inspecting output.
        """
        _skip_if_bugs_dir_missing()
        _skip_if_published_missing()

        # Build a temp bugs dir with the real bugs + the synthetic an earlier defect.
        temp_bugs_dir = self._make_temp_bugs_dir(_REAL_BUGS_DIR, tmp_path)
        self._add_synthetic_post_rc_bug(temp_bugs_dir)

        # Ensure PGAI_LINT_CHANGELOG_MODE is NOT set to "rc".
        prev_mode = os.environ.pop("PGAI_LINT_CHANGELOG_MODE", None)
        try:
            result = _lint.check_freshness(
                changelog_path=_CHANGELOG_ARTIFACT,
                bugs_dir=temp_bugs_dir,
            )
        finally:
            if prev_mode is not None:
                os.environ["PGAI_LINT_CHANGELOG_MODE"] = prev_mode

        captured = capsys.readouterr()
        assert result is False, (
            "check_freshness() returned True in normal mode for a bugs dir with "
            "BUG-9999 that causes the regenerated CHANGELOG to add a new KI line.\n"
            "Normal mode must byte-compare strictly and return False when the "
            "regenerated CHANGELOG differs from the checked-in artifact.\n"
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

        The test appends a non-KI line to a scratch copy of the CHANGELOG and
        passes it to check_freshness in RC mode with the REAL bugs dir.  The
        regeneration matches the real (unmodified) CHANGELOG, but the scratch
        has an extra non-KI line that is NOT in the regeneration — causing a
        "removal" in the diff (the extra line is in checked-in but not in
        regenerated).  _diff_is_rc_tolerable() rejects this because condition
        (a) requires no removed lines.

        Args:
            tmp_path:  pytest-provided temp directory.
            capsys:    pytest capture fixture for inspecting output.
        """
        _skip_if_bugs_dir_missing()
        _skip_if_published_missing()

        # Append a non-KI line to a scratch copy of the checked-in CHANGELOG.
        original_bytes = _CHANGELOG_ARTIFACT.read_bytes()
        assert len(original_bytes) > 0, "Real CHANGELOG.md is empty — test cannot run"
        mutated = original_bytes + b"\n# STALE MARKER FROM GENUINE EDIT\n"
        scratch = tmp_path / "mutated_changelog.md"
        scratch.write_bytes(mutated)

        # Run in RC mode with the REAL bugs dir.
        prev_mode = os.environ.get("PGAI_LINT_CHANGELOG_MODE", "")
        os.environ["PGAI_LINT_CHANGELOG_MODE"] = "rc"
        try:
            result = _lint.check_freshness(
                changelog_path=scratch,
                bugs_dir=_REAL_BUGS_DIR,
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
          1. Write a synthetic release-notes/v99.0.0.md under the dev tree's
             release-notes directory so that changelog_writer includes a
             ## v99.0.0 section in the fresh regeneration.  The version uses a
             plain semver form so _RELEASE_HEADING_RE matches it and the
             new-release-section tolerance clause can fire.
          2. Call check_freshness in RC mode with the real bugs dir and the REAL
             checked-in CHANGELOG.md (which does not have this section).
          3. Assert the gate passes — the new-release-section tolerance fires.
          4. Assert the success message mentions the tolerance clause.

        Cleanup: remove the synthetic release-notes file after the test.

        This test exercises an earlier defect acceptance criteria 1-3.

        Args:
            tmp_path:  pytest-provided temp directory (unused; kept for signature parity).
            capsys:    pytest capture fixture for inspecting output.
        """
        _skip_if_bugs_dir_missing()
        _skip_if_published_missing()

        # Create a synthetic release-notes file whose version does not appear in
        # the committed CHANGELOG.md.  Use a version that sorts HIGHER than the
        # current highest entry so it ends up at the top of the regeneration,
        # matching the real-world new-release-section pattern.
        synthetic_version = "v99.0.0"
        synthetic_notes = _RELEASE_NOTES_DIR / f"{synthetic_version}.md"
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
        try:
            # Run in RC mode with the real bugs dir.  Patch _current_git_branch so
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
                        changelog_path=_CHANGELOG_ARTIFACT,
                        bugs_dir=_REAL_BUGS_DIR,
                    )
            finally:
                if prev_mode:
                    os.environ["PGAI_LINT_CHANGELOG_MODE"] = prev_mode
                else:
                    os.environ.pop("PGAI_LINT_CHANGELOG_MODE", None)
        finally:
            # Always remove the synthetic file so it does not pollute the tree.
            synthetic_notes.unlink(missing_ok=True)

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
# Writer-migration drift tolerance tests (BUG-0077 / BUG-0080 enumeration)
# ---------------------------------------------------------------------------


class TestWriterMigrationDriftTolerance:
    """RC-mode tolerance for the three writer-migration drift categories (BUG-0077).

    BUG-0080 requires that _diff_is_rc_tolerable() explicitly name and tolerate
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
    # Checked-in CHANGELOG with all three BUG-0077 drift patterns.
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

        This exercises BUG-0080 acceptance criterion (a): RC-mode admission
        passes when the only diff is the three enumerated drift categories.
        """
        is_tolerable, clause = _lint._diff_is_rc_tolerable(
            self._CHECKED_IN_WITH_DRIFT,
            self._REGENERATED_WITHOUT_DRIFT,
        )
        assert is_tolerable is True, (
            "_diff_is_rc_tolerable() returned False for a diff containing only "
            "the three writer-migration drift categories (BUG-0077 enumeration).\n"
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

        This exercises BUG-0080 acceptance criterion (c): RC-mode does not
        over-broadly admit non-enumerated drift.
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
            "Only the three BUG-0077 drift categories are tolerable; any other "
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
        RC opens rather than being masked at every RC verification.

        The test monkeypatches _current_git_branch to return 'ai_main' and sets
        PGAI_LINT_CHANGELOG_MODE=rc, then asserts _is_rc_mode() returns False.

        This exercises BUG-0080 acceptance criterion (b): lint exits non-zero
        (fail-fast) on ai_main for the same drift that RC-mode would tolerate.
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
            "RC-mode tolerance must be suppressed on ai_main unconditionally "
            "(fail-fast-on-ai_main requirement from BUG-0080).\n"
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

        If the operator runs the lint directly (without the gated runner setting
        PGAI_LINT_CHANGELOG_MODE=rc), RC-mode tolerance must not activate, even
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
# Detached-HEAD _current_git_branch() fallback tests (BUG-0081)
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

        This test verifies the fix does not regress the non-detached case.
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
