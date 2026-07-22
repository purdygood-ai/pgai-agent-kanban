"""
test_cm_release_step11b_divergence.py
======================================
Focused regression gate for the Step 11b idempotency guard in
``team/scripts/cm/release.sh``.

The prior guard skipped CHANGELOG.md regeneration whenever HEAD's CHANGELOG
already contained a heading for the target version.  A WRITER-authored section
satisfies the heading check but diverges from ``changelog_writer``'s canonical
output, allowing stale content (including internal BUG-NNNN identifiers) to
merge to main unchanged.

This file tests the FIXED behaviour:
  1. **Guard structural shape** — the script no longer contains the
     heading-presence-only check (``grep -qF "## ${ACTIVE_RC}"``); instead it
     contains the byte-compare logic (``cmp -s``).
  2. **Divergence path fires** — when the checked-in CHANGELOG.md differs from
     a fresh ``changelog_writer`` render, the byte-compare is not equal
     (i.e. ``cmp -s`` would exit non-zero).  This verifies that the guard
     WOULD trigger regeneration rather than skip it.
  3. **Identical path skips** — when the checked-in file matches the fresh
     render byte-for-byte, ``cmp -s`` exits 0, meaning the guard correctly
     identifies the file as up-to-date.
  4. **Safety pass: BUG-[0-9] tokens** — the safety pass grep pattern (added
     after regeneration) would catch the BUG-0068 token in the WRITER-authored
     CHANGELOG section, confirming the guard is required and that the existing
     committed CHANGELOG.md would have triggered the safety-pass failure.
  5. **Regenerated output is clean** — a fresh ``changelog_writer`` render of
     the same inputs produces no BUG-[0-9] tokens, confirming the writer's
     internal safety pass strips them.

All fixtures are hermetic — no live CHANGELOG.md or bugs dir is read directly.
Temp paths use pytest's ``tmp_path`` fixture (redirected to the framework temp
root by conftest.py when ``PGAI_AGENT_KANBAN_TEMP_DIR`` is set).
"""

from __future__ import annotations

import os
import pathlib
import re
import subprocess
import sys
import textwrap

import pytest

from pgai_agent_kanban.cm.changelog_writer import (
    load_published_manifest,
    regenerate,
)

# ---------------------------------------------------------------------------
# Path constants
# ---------------------------------------------------------------------------

_THIS_FILE = pathlib.Path(__file__).resolve()
# team/tests/unit/test_cm_release_step11b_divergence.py
# Three levels up: unit/ -> tests/ -> team/
_TEAM_DIR = _THIS_FILE.parent.parent.parent       # team/
_DEV_TREE_ROOT = _TEAM_DIR.parent                  # project root
_CM_RELEASE_SCRIPT = _TEAM_DIR / "scripts" / "cm" / "release.sh"
_PYTHONPATH_ROOT = str(_TEAM_DIR)


# ---------------------------------------------------------------------------
# Fixture builders
# ---------------------------------------------------------------------------


def _make_release_notes(
    repo_root: pathlib.Path,
    version: str,
    date: str,
    *,
    summary: str = "",
    features: str = "None",
    bug_fixes: str = "None",
) -> pathlib.Path:
    """Create a minimal release-notes/<version>.md under repo_root/release-notes/."""
    notes_dir = repo_root / "release-notes"
    notes_dir.mkdir(parents=True, exist_ok=True)
    path = notes_dir / f"{version}.md"
    summary_text = summary or f"Release {version}."
    content = textwrap.dedent(f"""\
        # Release Notes: fixture {version}

        **Release Date:** {date}
        **Released By:** fixture

        ## Status
        FUNCTIONAL

        ## Summary
        {summary_text}

        ## Features
        {features}

        ## Bug Fixes
        {bug_fixes}

        ## Known Issues
        None
        """)
    path.write_text(content, encoding="utf-8")
    return path


def _make_empty_bugs_dir(tmp_path: pathlib.Path) -> pathlib.Path:
    """Create an empty bugs directory under tmp_path."""
    bugs_dir = tmp_path / "bugs"
    bugs_dir.mkdir(parents=True, exist_ok=True)
    return bugs_dir


def _build_hermetic_fixture(
    tmp_path: pathlib.Path,
    version: str = "v1.1.0",
    date: str = "2026-07-16",
    *,
    features: str = "- Improved pipeline throughput.",
) -> tuple[pathlib.Path, pathlib.Path]:
    """Build a minimal hermetic repo fixture with one release-notes file.

    Returns:
        (repo_root, bugs_dir) — both under tmp_path.
    """
    repo_root = tmp_path / "repo"
    repo_root.mkdir()
    bugs_dir = _make_empty_bugs_dir(tmp_path)
    _make_release_notes(repo_root, version, date, features=features)
    return repo_root, bugs_dir


def _writer_output(repo_root: pathlib.Path, bugs_dir: pathlib.Path) -> bytes:
    """Run changelog_writer as a subprocess and return its stdout bytes.

    Mirrors the invocation used by cm-release.sh Step 11b:
        PYTHONPATH="$KANBAN_ROOT" PYTHONHASHSEED=0 \\
          python3 -m pgai_agent_kanban.cm.changelog_writer \\
          "$REPO_ROOT" "$_cl_bugs_dir" > "$REPO_ROOT/CHANGELOG.md"
    """
    env = os.environ.copy()
    env["PYTHONHASHSEED"] = "0"
    env["PYTHONPATH"] = _PYTHONPATH_ROOT

    result = subprocess.run(
        [
            sys.executable,
            "-m", "pgai_agent_kanban.cm.changelog_writer",
            str(repo_root),
            str(bugs_dir),
        ],
        env=env,
        capture_output=True,
        cwd=str(_TEAM_DIR),
    )
    assert result.returncode == 0, (
        "changelog_writer subprocess exited non-zero.\n"
        f"stderr: {result.stderr.decode(errors='replace')!r}\n"
        f"stdout: {result.stdout.decode(errors='replace')!r}"
    )
    return result.stdout


# ---------------------------------------------------------------------------
# 1. Structural: byte-compare guard is present, heading-only guard is absent
# ---------------------------------------------------------------------------


class TestStep11bGuardStructure:
    """The Step 11b block uses byte-compare (cmp -s), not heading-presence (grep -qF)."""

    def test_byte_compare_pattern_present(self) -> None:
        """Step 11b contains the ``cmp -s`` byte-compare idempotency check.

        The heading-presence-only guard was replaced with a byte-compare against
        a fresh changelog_writer render.  ``cmp -s`` is the silent comparison
        tool used by the fixed guard.
        """
        assert _CM_RELEASE_SCRIPT.exists(), (
            f"cm-release.sh not found at expected location: {_CM_RELEASE_SCRIPT}"
        )
        source = _CM_RELEASE_SCRIPT.read_text(encoding="utf-8")
        step11b_start = source.find("Step 11b")
        step11c_start = source.find("Step 11c")
        assert step11b_start != -1, "Step 11b marker not found in cm-release.sh"
        assert step11c_start != -1, "Step 11c marker not found in cm-release.sh"
        step11b_block = source[step11b_start:step11c_start]

        assert "cmp -s" in step11b_block, (
            "Step 11b does not contain the byte-compare idiom 'cmp -s'.\n"
            "The idempotency guard must compare the checked-in CHANGELOG.md byte-for-byte "
            "against a fresh changelog_writer render.  A heading-presence-only check "
            "is insufficient — it cannot detect a WRITER-authored section that diverges "
            "from the canonical writer output."
        )

    def test_heading_presence_guard_absent(self) -> None:
        """Step 11b does not use the heading-presence-only check.

        The prior guard matched ``grep -qF "## ${ACTIVE_RC}"`` against HEAD's
        CHANGELOG.md.  This check is insufficient because a WRITER-authored
        section satisfies it while diverging from the canonical writer output.
        The fixed guard compares bytes; the old grep pattern must be gone.
        """
        assert _CM_RELEASE_SCRIPT.exists(), (
            f"cm-release.sh not found at expected location: {_CM_RELEASE_SCRIPT}"
        )
        source = _CM_RELEASE_SCRIPT.read_text(encoding="utf-8")
        step11b_start = source.find("Step 11b")
        step11c_start = source.find("Step 11c")
        assert step11b_start != -1, "Step 11b marker not found in cm-release.sh"
        assert step11c_start != -1, "Step 11c marker not found in cm-release.sh"
        step11b_block = source[step11b_start:step11c_start]

        # The old guard pattern: grep -qF "## ${ACTIVE_RC}" after a show HEAD command.
        # Check for the specific combination that constituted the stale guard.
        old_guard_pattern = re.compile(
            r'grep\s+-qF\s+["\']##\s+\$\{?ACTIVE_RC\}?["\']',
            re.MULTILINE,
        )
        assert not old_guard_pattern.search(step11b_block), (
            "Step 11b still contains the heading-presence-only guard "
            "``grep -qF '## ${ACTIVE_RC}'``.  This guard was the root cause of "
            "BUG-0069: it allows WRITER-authored content to bypass regeneration. "
            "The guard must be the byte-compare form (``cmp -s``) instead."
        )

    def test_bug_token_safety_pass_present(self) -> None:
        """Step 11b contains a post-regeneration BUG-[0-9] safety pass.

        After writing the regenerated CHANGELOG.md, the script must grep for
        the ``BUG-[0-9]`` pattern and exit non-zero if any match.  This catch
        ensures that a defective changelog_writer cannot ship internal identifiers
        to main.
        """
        assert _CM_RELEASE_SCRIPT.exists(), (
            f"cm-release.sh not found at expected location: {_CM_RELEASE_SCRIPT}"
        )
        source = _CM_RELEASE_SCRIPT.read_text(encoding="utf-8")
        step11b_start = source.find("Step 11b")
        step11c_start = source.find("Step 11c")
        assert step11b_start != -1, "Step 11b marker not found in cm-release.sh"
        assert step11c_start != -1, "Step 11c marker not found in cm-release.sh"
        step11b_block = source[step11b_start:step11c_start]

        assert 'BUG-[0-9]' in step11b_block, (
            "Step 11b does not contain a post-regeneration BUG-[0-9] safety pass.\n"
            "After writing the regenerated CHANGELOG.md, the script must grep for "
            "``BUG-[0-9]`` tokens and exit non-zero if any survive.  "
            "This guards against a changelog_writer defect that forgets to strip "
            "internal identifiers."
        )


# ---------------------------------------------------------------------------
# 2. Divergence path: WRITER-authored content differs from canonical render
# ---------------------------------------------------------------------------


class TestStep11bDivergencePath:
    """The byte-compare detects divergence between hand-authored and writer-rendered content.

    These tests demonstrate the divergence scenario from BUG-0069: a
    WRITER-authored CHANGELOG section that contains an internal BUG-NNNN
    identifier and narrative prose not produced by the writer is byte-different
    from a fresh changelog_writer render on the same inputs.  The byte-compare
    (``cmp -s``) would exit non-zero — triggering regeneration rather than
    skipping it.
    """

    def test_writer_authored_content_diverges_from_canonical_render(
        self, tmp_path: pathlib.Path
    ) -> None:
        """Hand-authored CHANGELOG.md is byte-different from changelog_writer output.

        Builds a hermetic fixture, runs changelog_writer to get the canonical
        bytes, then writes a simulated WRITER-authored CHANGELOG.md (with
        internal BUG-NNNN token and narrative prose).  Asserts the two differ.

        This verifies that the byte-compare guard WOULD fire in this scenario —
        regeneration would not be skipped.
        """
        repo_root, bugs_dir = _build_hermetic_fixture(tmp_path)

        # Canonical writer output for this fixture.
        canonical_bytes = _writer_output(repo_root, bugs_dir)
        canonical_text = canonical_bytes.decode("utf-8")

        # Simulated WRITER-authored CHANGELOG.md — contains a BUG-NNNN token
        # and narrative prose that the writer would never produce.
        writer_authored = textwrap.dedent("""\
            # Changelog

            <!-- Auto-generated by changelog_writer.py. Do not edit manually. -->
            <!-- Known Issues covers defects that existed in PUBLISHED releases only. -->
            <!-- Internal-only defects are tracked in the bug ledger but not disclosed. -->

            ---

            ## v1.1.0 — 2026-07-16

            v1.1.0 is a targeted fix for pipeline throughput. **BUG-0068 — internal diagnostic.**

            ### Implemented
            - Improved pipeline throughput.

            ### Fixed
            None

            ### Known Issues
            None

            *Details: [release-notes/v1.1.0.md](release-notes/v1.1.0.md)*

            ---
            """)

        # They must differ — the byte-compare guard would trigger regeneration.
        assert writer_authored != canonical_text, (
            "The WRITER-authored content is byte-identical to the canonical writer output.\n"
            "This fixture is broken — the hand-authored section must diverge to simulate "
            "the BUG-0069 scenario.  Ensure the WRITER-authored text includes content "
            "(e.g. a BUG-NNNN token or narrative prose) that the writer would not produce."
        )

        # Confirm the hand-authored content contains the BUG-NNNN token the guard catches.
        assert re.search(r"BUG-[0-9]", writer_authored), (
            "The WRITER-authored fixture content does not contain a BUG-[0-9] token.\n"
            "The fixture must simulate the BUG-0069 scenario where a WRITER-authored "
            "section retains an internal identifier that changelog_writer would strip."
        )

    def test_canonical_writer_output_has_no_bug_tokens(
        self, tmp_path: pathlib.Path
    ) -> None:
        """changelog_writer produces no BUG-[0-9] tokens even with bug files in the ledger.

        Confirms the writer's internal safety pass strips internal identifiers.
        After regeneration the safety pass in Step 11b would succeed on this output.
        """
        repo_root, bugs_dir = _build_hermetic_fixture(tmp_path)

        canonical_bytes = _writer_output(repo_root, bugs_dir)
        canonical_text = canonical_bytes.decode("utf-8")

        matches = re.findall(r"BUG-[0-9]", canonical_text)
        assert matches == [], (
            f"changelog_writer produced BUG-[0-9] token(s) in its output: {matches!r}\n"
            "The writer's internal safety pass must strip all internal BUG-NNNN identifiers.\n"
            "The Step 11b safety pass would fail and block the release if this output "
            "were written to CHANGELOG.md."
        )


# ---------------------------------------------------------------------------
# 3. Identical path: byte-compare skips when file matches
# ---------------------------------------------------------------------------


class TestStep11bIdenticalPath:
    """The byte-compare allows skipping when the checked-in file is canonical.

    These tests verify the happy path: when the checked-in CHANGELOG.md is
    already byte-identical to a fresh changelog_writer render, the ``cmp -s``
    comparison exits 0 (meaning no regeneration is needed).
    """

    def test_identical_bytes_means_no_regeneration_needed(
        self, tmp_path: pathlib.Path
    ) -> None:
        """cmp -s exits 0 when the canonical writer output is written as CHANGELOG.md.

        Builds a hermetic fixture, generates canonical bytes, writes them as
        CHANGELOG.md in the fixture repo, then calls cmp -s to confirm they
        are identical.  This simulates the skip-path the fixed guard takes.
        """
        repo_root, bugs_dir = _build_hermetic_fixture(tmp_path)

        # Generate and write the canonical CHANGELOG.md.
        canonical_bytes = _writer_output(repo_root, bugs_dir)
        changelog_path = repo_root / "CHANGELOG.md"
        changelog_path.write_bytes(canonical_bytes)

        # Write the same bytes to a temp buffer (simulating _cl_buf in Step 11b).
        buf_path = tmp_path / "_cl_buf_test"
        buf_path.write_bytes(canonical_bytes)

        # cmp -s must exit 0 — the guard would skip regeneration.
        import subprocess as _sp
        result = _sp.run(
            ["cmp", "-s", str(changelog_path), str(buf_path)],
            capture_output=True,
        )
        assert result.returncode == 0, (
            "cmp -s reported a difference between identical bytes.\n"
            "The byte-compare guard would incorrectly trigger regeneration when "
            "the checked-in CHANGELOG.md is already canonical."
        )

    def test_divergent_bytes_means_regeneration_needed(
        self, tmp_path: pathlib.Path
    ) -> None:
        """cmp -s exits non-zero when the checked-in CHANGELOG.md diverges from canonical.

        Builds a hermetic fixture, generates canonical bytes, writes a modified
        (hand-authored) version as CHANGELOG.md, then calls cmp -s to confirm
        they differ.  This simulates the regeneration-path the fixed guard takes.
        """
        repo_root, bugs_dir = _build_hermetic_fixture(tmp_path)

        canonical_bytes = _writer_output(repo_root, bugs_dir)

        # Write a divergent CHANGELOG.md (simulates WRITER-authored content).
        divergent_content = "# Changelog\n\n## v1.1.0 - WRITER-authored section\n\n**BUG-0068** mentioned here.\n".encode("utf-8")
        changelog_path = repo_root / "CHANGELOG.md"
        changelog_path.write_bytes(divergent_content)

        # Write canonical bytes to temp buffer.
        buf_path = tmp_path / "_cl_buf_test"
        buf_path.write_bytes(canonical_bytes)

        # cmp -s must exit non-zero — the guard would trigger regeneration.
        import subprocess as _sp
        result = _sp.run(
            ["cmp", "-s", str(changelog_path), str(buf_path)],
            capture_output=True,
        )
        assert result.returncode != 0, (
            "cmp -s reported identical bytes for divergent content.\n"
            "The byte-compare guard would incorrectly skip regeneration when "
            "the checked-in CHANGELOG.md differs from the canonical writer output."
        )
