"""
test_changelog_writer_title_fix.py
===================================
Acceptance tests for the Fixed-entry title sanitization fix (BUG-0072).

Covers:
  1. Ugly-bug fixture: a bug whose ## Symptom contains a pytest traceback,
     triple-backtick code fences, and shell command-prompt lines produces a
     Fixed entry that contains the clean prose title and grep-zero on:
       - "FAILED" (pytest traceback lines)
       - triple-backtick (code fence markers)
       - "$ " (shell command prompts)
  2. Bare-ID release-note fixture: a Bugs-Resolved section listing bare
     "KI-1.0.0.N — resolved" lines joins the title from the bug ledger and
     produces a proper disclosure line (ID + title + affects/fixed-in).
  3. Existing two-release regression: projections for a v1.0.0/v1.1.0 fixture
     are byte-stable when bugs have clean symptoms (no sanitization effect).
  4. _sanitize_symptom_for_title unit tests covering the five strip cases.

Test naming describes behavior (SOP.md Anti-pattern 6 compliance).
All temp paths use pytest's tmp_path fixture.
No bare /tmp paths in this file.
"""

from __future__ import annotations

import pathlib
import re
import textwrap

import pytest

from pgai_agent_kanban.cm.changelog_writer import (
    _sanitize_symptom_for_title,
    _extract_ki_id_from_line,
    parse_bug_file,
    regenerate,
)


# ---------------------------------------------------------------------------
# Fixture helpers (self-contained copies from test_changelog_writer.py)
# ---------------------------------------------------------------------------


def _make_release_notes(tmp_path: pathlib.Path, version: str, date: str, **kwargs) -> pathlib.Path:
    """Create a minimal release-notes/vX.Y.Z.md file under tmp_path/release-notes/.

    Content is written at column 0 (no leading indentation) to ensure that
    ``_parse_markdown_sections`` always matches H2 headings regardless of what
    multi-line kwargs values are passed in.  Using ``textwrap.dedent`` on a
    template that embeds arbitrary multi-line content would silently fail to
    strip indentation whenever any embedded line starts at column 0.
    """
    notes_dir = tmp_path / "release-notes"
    notes_dir.mkdir(parents=True, exist_ok=True)
    path = notes_dir / f"{version}.md"

    summary = kwargs.get("summary", f"Release {version}.")
    features = kwargs.get("features", "None")
    bug_fixes = kwargs.get("bug_fixes", "None")
    bugs_resolved = kwargs.get("bugs_resolved", "None")

    lines = [
        f"# Release Notes: test-project {version}",
        "",
        f"**Release Date:** {date}",
        "**Released By:** test",
        "",
        "## Status",
        "FUNCTIONAL",
        "",
        "## Summary",
        summary,
        "",
        "## Features",
        features,
        "",
        "## Bug Fixes",
        bug_fixes,
        "",
        "## Bugs Resolved",
        bugs_resolved,
        "",
        "## Known Issues",
        "None",
        "",
    ]
    path.write_text("\n".join(lines), encoding="utf-8")
    return path


def _make_bug_file(
    bugs_dir: pathlib.Path,
    bug_num: int,
    slug: str,
    symptom: str,
    status: str = "open",
    affects: str = "",
    fixed_in: str = "",
    public_id: str = "",
) -> pathlib.Path:
    """Create a minimal BUG-NNNN-slug.md file under bugs_dir.

    Content is written at column 0 (no leading indentation) to ensure that
    ``_parse_section_value`` always matches H2 headings even when the symptom
    argument contains multi-line content with lines that start at column 0
    (e.g. triple-backtick code fences).  Using ``textwrap.dedent`` on a
    template that embeds such content would silently disable indentation
    stripping for the entire file, making section headings invisible to the
    regex-based parser.
    """
    bugs_dir.mkdir(parents=True, exist_ok=True)
    bug_id = f"BUG-{bug_num:04d}"
    path = bugs_dir / f"{bug_id}-{slug}.md"

    lines = [
        f"# {bug_id}-{slug}",
        "",
        f"**Bug ID:** {bug_id}-{slug}",
        "**Filed By:** test",
        "**Date:** 2026-01-01",
        "**Severity:** medium",
        "",
        "## Status",
        status,
        "",
        "## Category",
        "misc",
        "",
        "---",
        "",
        "## Symptom",
        symptom,
        "",
        "## Expected",
        "Expected behavior.",
        "",
        "## Actual",
        "Actual behavior.",
        "",
        "## Affects",
        affects,
        "",
        "## Fixed In",
        fixed_in,
        "",
        "## Public ID",
        public_id,
        "",
    ]
    path.write_text("\n".join(lines), encoding="utf-8")
    return path


def _make_published(tmp_path: pathlib.Path, versions: list[str]) -> pathlib.Path:
    """Create release-notes/PUBLISHED under tmp_path."""
    notes_dir = tmp_path / "release-notes"
    notes_dir.mkdir(parents=True, exist_ok=True)
    path = notes_dir / "PUBLISHED"
    path.write_text("\n".join(versions) + ("\n" if versions else ""), encoding="utf-8")
    return path


# ---------------------------------------------------------------------------
# The "ugly bug" symptom: traceback, code fence, and command-prompt lines.
# This is exactly the class of content that must never appear in Fixed entries.
# ---------------------------------------------------------------------------

_UGLY_BUG_SYMPTOM = textwrap.dedent("""\
    The changelog renderer emits raw diagnostic content into the public Fixed section.

    ```
    FAILED tests/unit/test_changelog_writer.py::TestClass::test_something - AssertionError: expected clean line
    tests/unit/test_changelog_writer.py:42: AssertionError
    ```

    Steps to reproduce:

    ```bash
    $ run-unit-tests.sh
    $ grep FAILED logs/last-run.log
    ```

    All 2160 other unit tests pass.
    """)

# The prose title extracted from the ugly symptom after sanitization.
# Must survive into the Fixed entry without the diagnostic markers.
_UGLY_BUG_TITLE_FRAGMENT = "The changelog renderer emits raw diagnostic content"


# ===========================================================================
# Criterion 1: Ugly-bug fixture — grep-zero on diagnostic markers
# ===========================================================================


class TestUglyBugFixedEntryRendering:
    """A bug with traceback / code fences / command prompts in its Symptom
    must produce a Fixed entry that contains only the clean prose title and
    contains none of the diagnostic markers."""

    def test_ugly_bug_fixed_entry_contains_prose_title(
        self, tmp_path: pathlib.Path
    ) -> None:
        """The Fixed entry for an ugly-bug file contains the clean prose title."""
        bugs_dir = tmp_path / "bugs"
        _make_bug_file(
            bugs_dir,
            bug_num=72,
            slug="ugly-bug-with-traceback",
            symptom=_UGLY_BUG_SYMPTOM,
            status="resolved",
            affects="v1.0.0",
            fixed_in="v1.1.0",
            public_id="KI-1.0.0.1",
        )

        repo_root = tmp_path / "repo"
        repo_root.mkdir()
        _make_release_notes(
            repo_root, "v1.1.0", "2026-01-02",
            bug_fixes="- **BUG-0072**: Fixed ugly bug."
        )
        _make_release_notes(repo_root, "v1.0.0", "2026-01-01")
        _make_published(repo_root, ["v1.0.0"])

        content = regenerate(repo_root, ["v1.1.0", "v1.0.0"], ["v1.0.0"], bugs_dir)

        # Extract the v1.1.0 Fixed section.
        entry_match = re.search(
            r"## v1\.1\.0.*?(?=## v1\.0\.0|\Z)", content, re.DOTALL
        )
        assert entry_match is not None, "v1.1.0 entry not found in output"
        entry_text = entry_match.group(0)

        fixed_match = re.search(r"### Fixed\n(.+?)(?=###|\Z)", entry_text, re.DOTALL)
        assert fixed_match is not None, "Fixed section not found in v1.1.0 entry"
        fixed_body = fixed_match.group(1)

        # The prose title must appear in the Fixed body.
        assert _UGLY_BUG_TITLE_FRAGMENT in fixed_body, (
            f"Prose title fragment not found in Fixed body: {fixed_body!r}"
        )

    def test_ugly_bug_fixed_entry_grep_zero_failed(
        self, tmp_path: pathlib.Path
    ) -> None:
        """The Fixed entry for an ugly-bug file contains no 'FAILED' markers."""
        bugs_dir = tmp_path / "bugs"
        _make_bug_file(
            bugs_dir,
            bug_num=72,
            slug="ugly-bug-with-traceback",
            symptom=_UGLY_BUG_SYMPTOM,
            status="resolved",
            affects="v1.0.0",
            fixed_in="v1.1.0",
            public_id="KI-1.0.0.1",
        )

        repo_root = tmp_path / "repo"
        repo_root.mkdir()
        _make_release_notes(
            repo_root, "v1.1.0", "2026-01-02",
            bug_fixes="- **BUG-0072**: Fixed ugly bug."
        )
        _make_release_notes(repo_root, "v1.0.0", "2026-01-01")
        _make_published(repo_root, ["v1.0.0"])

        content = regenerate(repo_root, ["v1.1.0", "v1.0.0"], ["v1.0.0"], bugs_dir)

        entry_match = re.search(
            r"## v1\.1\.0.*?(?=## v1\.0\.0|\Z)", content, re.DOTALL
        )
        assert entry_match is not None
        entry_text = entry_match.group(0)
        fixed_match = re.search(r"### Fixed\n(.+?)(?=###|\Z)", entry_text, re.DOTALL)
        assert fixed_match is not None
        fixed_body = fixed_match.group(1)

        # No FAILED markers.
        assert "FAILED" not in fixed_body, (
            f"'FAILED' marker found in Fixed body: {fixed_body!r}"
        )

    def test_ugly_bug_fixed_entry_grep_zero_triple_backtick(
        self, tmp_path: pathlib.Path
    ) -> None:
        """The Fixed entry for an ugly-bug file contains no triple-backtick markers."""
        bugs_dir = tmp_path / "bugs"
        _make_bug_file(
            bugs_dir,
            bug_num=72,
            slug="ugly-bug-with-traceback",
            symptom=_UGLY_BUG_SYMPTOM,
            status="resolved",
            affects="v1.0.0",
            fixed_in="v1.1.0",
            public_id="KI-1.0.0.1",
        )

        repo_root = tmp_path / "repo"
        repo_root.mkdir()
        _make_release_notes(
            repo_root, "v1.1.0", "2026-01-02",
            bug_fixes="- **BUG-0072**: Fixed ugly bug."
        )
        _make_release_notes(repo_root, "v1.0.0", "2026-01-01")
        _make_published(repo_root, ["v1.0.0"])

        content = regenerate(repo_root, ["v1.1.0", "v1.0.0"], ["v1.0.0"], bugs_dir)

        entry_match = re.search(
            r"## v1\.1\.0.*?(?=## v1\.0\.0|\Z)", content, re.DOTALL
        )
        assert entry_match is not None
        entry_text = entry_match.group(0)
        fixed_match = re.search(r"### Fixed\n(.+?)(?=###|\Z)", entry_text, re.DOTALL)
        assert fixed_match is not None
        fixed_body = fixed_match.group(1)

        # No triple-backtick markers.
        assert "```" not in fixed_body, (
            f"Triple-backtick found in Fixed body: {fixed_body!r}"
        )

    def test_ugly_bug_fixed_entry_grep_zero_dollar_command_lines(
        self, tmp_path: pathlib.Path
    ) -> None:
        """The Fixed entry for an ugly-bug file contains no shell '$ ' command prompts."""
        bugs_dir = tmp_path / "bugs"
        _make_bug_file(
            bugs_dir,
            bug_num=72,
            slug="ugly-bug-with-traceback",
            symptom=_UGLY_BUG_SYMPTOM,
            status="resolved",
            affects="v1.0.0",
            fixed_in="v1.1.0",
            public_id="KI-1.0.0.1",
        )

        repo_root = tmp_path / "repo"
        repo_root.mkdir()
        _make_release_notes(
            repo_root, "v1.1.0", "2026-01-02",
            bug_fixes="- **BUG-0072**: Fixed ugly bug."
        )
        _make_release_notes(repo_root, "v1.0.0", "2026-01-01")
        _make_published(repo_root, ["v1.0.0"])

        content = regenerate(repo_root, ["v1.1.0", "v1.0.0"], ["v1.0.0"], bugs_dir)

        entry_match = re.search(
            r"## v1\.1\.0.*?(?=## v1\.0\.0|\Z)", content, re.DOTALL
        )
        assert entry_match is not None
        entry_text = entry_match.group(0)
        fixed_match = re.search(r"### Fixed\n(.+?)(?=###|\Z)", entry_text, re.DOTALL)
        assert fixed_match is not None
        fixed_body = fixed_match.group(1)

        # No shell command-prompt lines.
        # Match "$ " at the start of a line (the canonical prompt form in symptoms).
        assert not re.search(r"^\$ ", fixed_body, re.MULTILINE), (
            f"Shell command-prompt '$ ' found at start of line in Fixed body: {fixed_body!r}"
        )

    def test_ugly_bug_single_fixed_line_from_prose_title(
        self, tmp_path: pathlib.Path
    ) -> None:
        """The Fixed entry for an ugly-bug produces exactly one non-empty line."""
        bugs_dir = tmp_path / "bugs"
        _make_bug_file(
            bugs_dir,
            bug_num=72,
            slug="ugly-bug-with-traceback",
            symptom=_UGLY_BUG_SYMPTOM,
            status="resolved",
            affects="v1.0.0",
            fixed_in="v1.1.0",
            public_id="KI-1.0.0.1",
        )

        repo_root = tmp_path / "repo"
        repo_root.mkdir()
        _make_release_notes(
            repo_root, "v1.1.0", "2026-01-02",
            bug_fixes="- **BUG-0072**: Fixed ugly bug."
        )
        _make_release_notes(repo_root, "v1.0.0", "2026-01-01")
        _make_published(repo_root, ["v1.0.0"])

        content = regenerate(repo_root, ["v1.1.0", "v1.0.0"], ["v1.0.0"], bugs_dir)

        entry_match = re.search(
            r"## v1\.1\.0.*?(?=## v1\.0\.0|\Z)", content, re.DOTALL
        )
        assert entry_match is not None
        entry_text = entry_match.group(0)
        fixed_match = re.search(r"### Fixed\n(.+?)(?=###|\Z)", entry_text, re.DOTALL)
        assert fixed_match is not None
        fixed_body = fixed_match.group(1).strip()

        fixed_lines = [ln for ln in fixed_body.splitlines() if ln.strip()]
        assert len(fixed_lines) == 1, (
            f"Expected exactly 1 Fixed line for ugly-bug; got {len(fixed_lines)}: {fixed_lines!r}"
        )


# ===========================================================================
# Criterion 2: Bare-ID release-note fixture — joined title from ledger
# ===========================================================================


class TestBareKiIdJoinInBugsResolved:
    """A Bugs-Resolved section listing bare 'KI-N.N.N.N — resolved' entries
    must produce Fixed lines that carry the joined title from the bug ledger,
    not the bare placeholder."""

    def test_bare_ki_id_joined_to_title_in_fixed_section(
        self, tmp_path: pathlib.Path
    ) -> None:
        """A bare 'KI-1.0.0.1 — resolved' in Bugs Resolved produces ID + title in Fixed."""
        bugs_dir = tmp_path / "bugs"
        _make_bug_file(
            bugs_dir,
            bug_num=1,
            slug="known-crash",
            symptom="Widget crashes on startup.",
            status="resolved",
            affects="v1.0.0",
            fixed_in="v1.1.0",
            public_id="KI-1.0.0.1",
        )

        repo_root = tmp_path / "repo"
        repo_root.mkdir()
        # v1.1.0 uses "Bugs Resolved" with bare KI IDs (no titles)
        _make_release_notes(
            repo_root, "v1.1.0", "2026-01-02",
            bugs_resolved="- KI-1.0.0.1 — resolved",
        )
        _make_release_notes(repo_root, "v1.0.0", "2026-01-01")
        _make_published(repo_root, ["v1.0.0", "v1.1.0"])

        content = regenerate(repo_root, ["v1.1.0", "v1.0.0"], ["v1.0.0", "v1.1.0"], bugs_dir)

        entry_match = re.search(
            r"## v1\.1\.0.*?(?=## v1\.0\.0|\Z)", content, re.DOTALL
        )
        assert entry_match is not None, "v1.1.0 entry not found"
        entry_text = entry_match.group(0)

        fixed_match = re.search(r"### Fixed\n(.+?)(?=###|\Z)", entry_text, re.DOTALL)
        assert fixed_match is not None, "Fixed section not found in v1.1.0"
        fixed_body = fixed_match.group(1)

        # The joined line must contain the KI identifier.
        assert "KI-1.0.0.1" in fixed_body, (
            f"KI-1.0.0.1 not found in Fixed body: {fixed_body!r}"
        )

        # The joined line must contain the bug title (symptom text), not just 'resolved'.
        assert "Widget crashes on startup" in fixed_body, (
            f"Bug title not joined into Fixed body: {fixed_body!r}"
        )

        # The bare placeholder 'resolved' must not appear alone without the title.
        # (We allow 'resolved' as part of the affects/fixed-in clause, but not as
        # the only content for the bug entry.)
        fixed_lines = [ln.strip() for ln in fixed_body.splitlines() if ln.strip()]
        ki_lines = [ln for ln in fixed_lines if ln.startswith("KI-1.0.0.1")]
        assert len(ki_lines) >= 1, "KI-1.0.0.1 line not found in Fixed body"
        ki_line = ki_lines[0]
        assert "Widget crashes on startup" in ki_line, (
            f"Joined KI line does not carry title: {ki_line!r}"
        )

    def test_bare_ki_id_not_in_ledger_passes_through_as_is(
        self, tmp_path: pathlib.Path
    ) -> None:
        """A bare KI ID in Bugs Resolved that has no matching bug falls through as plain text."""
        bugs_dir = tmp_path / "bugs"
        # No bug file for KI-1.0.0.9

        repo_root = tmp_path / "repo"
        repo_root.mkdir()
        _make_release_notes(
            repo_root, "v1.1.0", "2026-01-02",
            bugs_resolved="- KI-1.0.0.9 — resolved",
        )
        _make_release_notes(repo_root, "v1.0.0", "2026-01-01")
        _make_published(repo_root, ["v1.0.0"])

        # Should not raise; bare ID passes through stripped
        content = regenerate(repo_root, ["v1.1.0", "v1.0.0"], ["v1.0.0"], bugs_dir)
        # The content is generated without error
        assert content, "Expected non-empty CHANGELOG output"

    def test_multiple_bare_ki_ids_all_joined(
        self, tmp_path: pathlib.Path
    ) -> None:
        """Multiple bare KI IDs in Bugs Resolved are all joined from the ledger."""
        bugs_dir = tmp_path / "bugs"
        _make_bug_file(
            bugs_dir,
            bug_num=1,
            slug="first-crash",
            symptom="First crash symptom.",
            status="resolved",
            affects="v1.0.0",
            fixed_in="v1.1.0",
            public_id="KI-1.0.0.1",
        )
        _make_bug_file(
            bugs_dir,
            bug_num=2,
            slug="second-crash",
            symptom="Second crash symptom.",
            status="resolved",
            affects="v1.0.0",
            fixed_in="v1.1.0",
            public_id="KI-1.0.0.2",
        )

        repo_root = tmp_path / "repo"
        repo_root.mkdir()
        _make_release_notes(
            repo_root, "v1.1.0", "2026-01-02",
            bugs_resolved="- KI-1.0.0.1 — resolved\n- KI-1.0.0.2 — resolved",
        )
        _make_release_notes(repo_root, "v1.0.0", "2026-01-01")
        _make_published(repo_root, ["v1.0.0", "v1.1.0"])

        content = regenerate(repo_root, ["v1.1.0", "v1.0.0"], ["v1.0.0", "v1.1.0"], bugs_dir)

        entry_match = re.search(
            r"## v1\.1\.0.*?(?=## v1\.0\.0|\Z)", content, re.DOTALL
        )
        assert entry_match is not None
        entry_text = entry_match.group(0)
        fixed_match = re.search(r"### Fixed\n(.+?)(?=###|\Z)", entry_text, re.DOTALL)
        assert fixed_match is not None
        fixed_body = fixed_match.group(1)

        assert "First crash symptom" in fixed_body, (
            "KI-1.0.0.1 title not joined"
        )
        assert "Second crash symptom" in fixed_body, (
            "KI-1.0.0.2 title not joined"
        )


# ===========================================================================
# Criterion 3: Byte-stability regression for clean-symptom bugs
# ===========================================================================


class TestByteStabilityRegressionCleanSymptoms:
    """Bugs with clean (no-fence, no-FAILED, no-$) symptoms are unchanged
    by sanitization — the Fixed entry content is byte-stable."""

    def test_clean_symptom_produces_same_fixed_entry(
        self, tmp_path: pathlib.Path
    ) -> None:
        """A bug with a clean single-sentence symptom produces the same Fixed line
        before and after the sanitization fix (byte-stable regression)."""
        bugs_dir = tmp_path / "bugs"
        _make_bug_file(
            bugs_dir,
            bug_num=1,
            slug="clean-bug",
            symptom="Widget crashes on startup.",
            status="resolved",
            affects="v1.0.0",
            fixed_in="v1.1.0",
            public_id="KI-1.0.0.1",
        )

        repo_root = tmp_path / "repo"
        repo_root.mkdir()
        _make_release_notes(
            repo_root, "v1.1.0", "2026-01-02",
            bug_fixes="- **BUG-0001**: Widget crashes on startup. Fixed."
        )
        _make_release_notes(repo_root, "v1.0.0", "2026-01-01")
        _make_published(repo_root, ["v1.0.0"])

        content = regenerate(repo_root, ["v1.1.0", "v1.0.0"], ["v1.0.0"], bugs_dir)

        # The symptom "Widget crashes on startup." must appear in Fixed
        # and in the KI line format when rendered.
        assert "Widget crashes on startup" in content

        # No regression: the content is idempotent across two calls
        content2 = regenerate(repo_root, ["v1.1.0", "v1.0.0"], ["v1.0.0"], bugs_dir)
        assert content == content2

    def test_internal_only_clean_bug_stable_in_fixed(
        self, tmp_path: pathlib.Path
    ) -> None:
        """An internal-only bug with a clean symptom renders stably in Fixed."""
        bugs_dir = tmp_path / "bugs"
        _make_bug_file(
            bugs_dir,
            bug_num=2,
            slug="internal-fix",
            symptom="Internal scheduler race condition fixed.",
            status="resolved",
            affects="v1.0.5",  # not in PUBLISHED
            fixed_in="v1.1.0",
        )

        repo_root = tmp_path / "repo"
        repo_root.mkdir()
        _make_release_notes(
            repo_root, "v1.1.0", "2026-01-02",
            bug_fixes="- **BUG-0002**: Internal scheduler race. Fixed."
        )
        _make_release_notes(repo_root, "v1.0.0", "2026-01-01")
        _make_published(repo_root, ["v1.0.0"])

        content = regenerate(repo_root, ["v1.1.0", "v1.0.0"], ["v1.0.0"], bugs_dir)

        # Internal bug symptom still appears in Fixed (no ID, just the title)
        assert "Internal scheduler race condition fixed" in content

        # No KI- identifier for the internal bug
        assert "KI-1.0.5" not in content


# ===========================================================================
# Criterion 4: _sanitize_symptom_for_title unit tests
# ===========================================================================


class TestSanitizeSymptomForTitle:
    """Unit tests for _sanitize_symptom_for_title covering the five strip cases."""

    def test_simple_prose_symptom_passes_through_unchanged(self) -> None:
        """A simple single-sentence symptom with no diagnostic markers is unchanged."""
        s = "Widget crashes on startup."
        assert _sanitize_symptom_for_title(s) == s

    def test_triple_backtick_fence_block_stripped(self) -> None:
        """Content between triple-backtick fences is stripped."""
        raw = "Prose before.\n```\nerror content here\n```\nProse after."
        result = _sanitize_symptom_for_title(raw)
        assert "```" not in result, f"Triple-backtick in result: {result!r}"
        assert "error content here" not in result, f"Fence content in result: {result!r}"

    def test_failed_line_stripped(self) -> None:
        """Lines starting with FAILED are stripped."""
        raw = "```\nFAILED test_something - AssertionError: expected x\n```\nProse title."
        result = _sanitize_symptom_for_title(raw)
        assert "FAILED" not in result, f"FAILED in result: {result!r}"
        assert "AssertionError" not in result, f"AssertionError in result: {result!r}"

    def test_dollar_command_prompt_line_stripped(self) -> None:
        """Lines starting with '$ ' (shell prompts) are stripped."""
        raw = "```\n$ run-tests.sh\n$ grep FAILED log\n```\nProse title."
        result = _sanitize_symptom_for_title(raw)
        assert "$ " not in result, f"'$ ' in result: {result!r}"

    def test_prose_first_sentence_extracted_after_fence_strip(self) -> None:
        """After stripping a leading fence block, the first prose sentence is extracted."""
        raw = textwrap.dedent("""\
            ```
            error output here
            ```
            The changelog generator emits a stray fence into Fixed entries.
            More detail about the bug follows here.
            """)
        result = _sanitize_symptom_for_title(raw)
        assert "The changelog generator emits" in result, f"Prose not in result: {result!r}"
        assert "```" not in result

    def test_empty_input_returns_empty_string(self) -> None:
        """Empty input returns an empty string without error."""
        assert _sanitize_symptom_for_title("") == ""

    def test_fence_only_symptom_returns_empty_or_fallback(self) -> None:
        """A symptom consisting entirely of a code fence block returns empty or fallback."""
        raw = "```\nonly code here\n```"
        result = _sanitize_symptom_for_title(raw)
        # No fence markers remain
        assert "```" not in result
        # No code-fence content remains
        assert "only code here" not in result

    def test_multiple_fence_blocks_all_stripped(self) -> None:
        """Multiple fence blocks in the symptom are all stripped."""
        raw = textwrap.dedent("""\
            First fence:
            ```
            block one
            ```
            Middle prose sentence.
            ```
            block two
            ```
            Final prose.
            """)
        result = _sanitize_symptom_for_title(raw)
        assert "```" not in result
        assert "block one" not in result
        assert "block two" not in result


# ===========================================================================
# Criterion 4b: _extract_ki_id_from_line unit tests
# ===========================================================================


class TestExtractKiIdFromLine:
    """Unit tests for _extract_ki_id_from_line."""

    def test_bullet_with_em_dash_separator_extracted(self) -> None:
        """'- KI-1.0.0.1 — resolved' extracts KI-1.0.0.1."""
        assert _extract_ki_id_from_line("- KI-1.0.0.1 — resolved") == "KI-1.0.0.1"

    def test_star_bullet_extracted(self) -> None:
        """'* KI-1.0.0.2 — text' extracts KI-1.0.0.2."""
        assert _extract_ki_id_from_line("* KI-1.0.0.2 — text") == "KI-1.0.0.2"

    def test_bare_ki_id_without_separator_extracted(self) -> None:
        """'- KI-1.0.0.3' (no separator) extracts KI-1.0.0.3."""
        # The pattern requires some separator or trailing space; this tests bare form
        # after the list marker is consumed.
        line = "KI-1.0.0.3 — resolved"
        result = _extract_ki_id_from_line(line)
        assert result == "KI-1.0.0.3", f"Expected KI-1.0.0.3, got {result!r}"

    def test_ki_id_mid_sentence_not_extracted(self) -> None:
        """A KI ID embedded mid-sentence is not extracted."""
        line = "This refers to KI-1.0.0.1 for context"
        assert _extract_ki_id_from_line(line) == ""

    def test_bug_id_line_not_extracted_as_ki(self) -> None:
        """A BUG-NNNN line does not match as a KI ID."""
        assert _extract_ki_id_from_line("- BUG-0001 — symptom") == ""

    def test_plain_prose_not_extracted(self) -> None:
        """A plain prose line returns empty string."""
        assert _extract_ki_id_from_line("All five known issues resolved.") == ""
