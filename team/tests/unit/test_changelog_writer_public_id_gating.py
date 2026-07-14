"""
test_changelog_writer_public_id_gating.py
==========================================
Unit tests for the Public-ID gating fix (an earlier defect).

Covers:
  1. Prose-affects fixture: regenerate() mints no Public ID, emits the warning
     to stderr naming the bug file, and renders nothing in any KI section.
  2. Real-affects regression: a v1.0.0-affects fixture still receives the next
     per-release counter and renders in the KI section for that release.
  3. Cleanup unit fixture:
     (a) Running remove_bogus_public_ids on a ledger containing a bogus KI
         stamp removes exactly that stamp.
     (b) Running it on a clean ledger is byte-identical (no writes).
  4. Counter isolation: prose-affects bugs do not advance the per-release
     counter, so valid bugs get contiguous counter values.
  5. _looks_like_version_string recognises valid forms and rejects prose.

Test naming describes behaviour, not bug IDs or scaffolding labels.
All temp paths use pytest's tmp_path fixture.
"""

from __future__ import annotations

import pathlib
import re
import textwrap

import pytest

from pgai_agent_kanban.cm.changelog_writer import (
    _looks_like_version_string,
    parse_bug_file,
    regenerate,
    remove_bogus_public_ids,
)


# ---------------------------------------------------------------------------
# Fixture helpers (mirrored from test_changelog_writer.py for self-contained tests)
# ---------------------------------------------------------------------------


def _make_release_notes(tmp_path: pathlib.Path, version: str, date: str, **kwargs) -> pathlib.Path:
    """Create a minimal release-notes/vX.Y.Z.md file under tmp_path/release-notes/."""
    notes_dir = tmp_path / "release-notes"
    notes_dir.mkdir(parents=True, exist_ok=True)
    path = notes_dir / f"{version}.md"

    summary = kwargs.get("summary", f"Release {version}.")
    features = kwargs.get("features", "None")
    bug_fixes = kwargs.get("bug_fixes", "None")
    bugs_resolved = kwargs.get("bugs_resolved", "None")

    content = textwrap.dedent(f"""\
        # Release Notes: test-project {version}

        **Release Date:** {date}
        **Released By:** test

        ## Status
        FUNCTIONAL

        ## Summary
        {summary}

        ## Features
        {features}

        ## Bug Fixes
        {bug_fixes}

        ## Bugs Resolved
        {bugs_resolved}

        ## Known Issues
        None
        """)
    path.write_text(content, encoding="utf-8")
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
    include_affects_section: bool = True,
) -> pathlib.Path:
    """Create a minimal BUG-NNNN-slug.md file under bugs_dir.

    When include_affects_section is False, the ## Affects section is omitted
    entirely (the correct convention for internal bugs).
    """
    bugs_dir.mkdir(parents=True, exist_ok=True)
    bug_id = f"BUG-{bug_num:04d}"
    path = bugs_dir / f"{bug_id}-{slug}.md"

    affects_section = f"\n## Affects\n{affects}\n" if include_affects_section else ""
    fixed_in_section = f"\n## Fixed In\n{fixed_in}\n" if fixed_in else "\n## Fixed In\n"
    public_id_section = f"\n## Public ID\n{public_id}\n" if public_id else "\n## Public ID\n"

    content = textwrap.dedent(f"""\
        # {bug_id}-{slug}

        **Bug ID:** {bug_id}-{slug}
        **Filed By:** test
        **Date:** 2026-01-01
        **Severity:** medium

        ## Status
        {status}

        ## Category
        misc

        ---

        ## Symptom
        {symptom}

        ## Expected
        Expected behavior.

        ## Actual
        Actual behavior.
        """) + affects_section + fixed_in_section + public_id_section + "\n"

    path.write_text(content, encoding="utf-8")
    return path


def _make_published(tmp_path: pathlib.Path, versions: list[str]) -> pathlib.Path:
    """Create release-notes/PUBLISHED under tmp_path."""
    notes_dir = tmp_path / "release-notes"
    notes_dir.mkdir(parents=True, exist_ok=True)
    path = notes_dir / "PUBLISHED"
    path.write_text("\n".join(versions) + ("\n" if versions else ""), encoding="utf-8")
    return path


# ===========================================================================
# Criterion: _looks_like_version_string recognises valid forms and rejects prose
# ===========================================================================


class TestLooksLikeVersionString:
    """_looks_like_version_string must accept version forms and reject prose."""

    def test_canonical_version_accepted(self) -> None:
        """v1.2.3 is accepted."""
        assert _looks_like_version_string("v1.2.3") is True

    def test_version_without_prefix_accepted(self) -> None:
        """1.2.3 (no leading v) is accepted."""
        assert _looks_like_version_string("1.2.3") is True

    def test_ai_v_prefix_accepted(self) -> None:
        """ai_v1.2.3 is accepted."""
        assert _looks_like_version_string("ai_v1.2.3") is True

    def test_two_part_version_accepted(self) -> None:
        """v1.22 (major.minor only) is accepted."""
        assert _looks_like_version_string("v1.22") is True

    def test_internal_prose_rejected(self) -> None:
        """Prose placeholder used by internal bugs is rejected."""
        assert _looks_like_version_string("(internal — plugin era; no KI)") is False

    def test_empty_string_rejected(self) -> None:
        """Empty string is rejected."""
        assert _looks_like_version_string("") is False

    def test_bare_letter_v_rejected(self) -> None:
        """Bare 'v' with no digits is rejected."""
        assert _looks_like_version_string("v") is False

    def test_typo_extra_text_rejected(self) -> None:
        """A version with trailing prose (typo-like) is rejected."""
        assert _looks_like_version_string("v1.0.0 (fixed)") is False

    def test_whitespace_only_rejected(self) -> None:
        """Whitespace-only string is rejected."""
        assert _looks_like_version_string("   ") is False


# ===========================================================================
# Criterion 1: Prose-affects fixture mints no Public ID and emits warning
# ===========================================================================


class TestProseAffectsMintsNothingAndWarns:
    """regenerate() must not assign a Public ID when ## Affects is prose."""

    def test_prose_affects_no_public_id_minted(
        self, tmp_path: pathlib.Path
    ) -> None:
        """A bug with prose ## Affects receives no Public ID after regeneration."""
        repo_root = tmp_path / "repo"
        repo_root.mkdir()
        bugs_dir = tmp_path / "bugs"

        _make_release_notes(repo_root, "v1.0.0", "2026-01-01")
        _make_published(repo_root, ["v1.0.0"])

        bug_path = _make_bug_file(
            bugs_dir,
            bug_num=51,
            slug="internal-prose-affects",
            symptom="Some internal defect.",
            status="open",
            affects="(internal — plugin era; no KI)",
        )

        regenerate(repo_root, ["v1.0.0"], ["v1.0.0"], bugs_dir)

        rec = parse_bug_file(bug_path)
        assert rec.public_id == "", (
            f"Expected no Public ID for prose-affects bug; got {rec.public_id!r}"
        )

    def test_prose_affects_no_ki_in_output(
        self, tmp_path: pathlib.Path
    ) -> None:
        """A prose-affects bug does not appear in any KI section of the output."""
        repo_root = tmp_path / "repo"
        repo_root.mkdir()
        bugs_dir = tmp_path / "bugs"

        _make_release_notes(repo_root, "v1.0.0", "2026-01-01")
        _make_published(repo_root, ["v1.0.0"])

        _make_bug_file(
            bugs_dir,
            bug_num=51,
            slug="prose-affects-no-ki",
            symptom="Internal defect that should not be disclosed.",
            status="open",
            affects="(internal — plugin era; no KI)",
        )

        content = regenerate(repo_root, ["v1.0.0"], ["v1.0.0"], bugs_dir)

        # No KI- identifier anywhere
        assert "KI-" not in content, (
            "KI- identifier appeared in output for a prose-affects bug"
        )
        # Symptom must not appear in the KI section
        ki_section_match = re.search(r"### Known Issues\n(.+?)(?=###|\Z)", content, re.DOTALL)
        if ki_section_match:
            ki_body = ki_section_match.group(1).strip()
            assert "Internal defect that should not be disclosed" not in ki_body, (
                "Prose-affects bug symptom appeared in Known Issues section"
            )

    def test_prose_affects_emits_warning_to_stderr(
        self, tmp_path: pathlib.Path, capsys: pytest.CaptureFixture
    ) -> None:
        """regenerate() emits a warning to stderr naming the bug file for prose-affects."""
        repo_root = tmp_path / "repo"
        repo_root.mkdir()
        bugs_dir = tmp_path / "bugs"

        _make_release_notes(repo_root, "v1.0.0", "2026-01-01")
        _make_published(repo_root, ["v1.0.0"])

        bug_path = _make_bug_file(
            bugs_dir,
            bug_num=51,
            slug="prose-affects-warning",
            symptom="Internal defect.",
            status="open",
            affects="(internal — plugin era; no KI)",
        )

        regenerate(repo_root, ["v1.0.0"], ["v1.0.0"], bugs_dir)

        _, err = capsys.readouterr()
        # Warning must name the file
        assert str(bug_path) in err or bug_path.name in err, (
            f"Expected bug filename in stderr warning; got: {err!r}"
        )
        # Warning must mention the standard phrase
        assert "Affects value not a known release" in err, (
            f"Expected 'Affects value not a known release' in stderr; got: {err!r}"
        )

    def test_typo_version_emits_warning_to_stderr(
        self, tmp_path: pathlib.Path, capsys: pytest.CaptureFixture
    ) -> None:
        """A typo'd real version (non-parseable) also emits a warning — fail-loud both directions."""
        repo_root = tmp_path / "repo"
        repo_root.mkdir()
        bugs_dir = tmp_path / "bugs"

        _make_release_notes(repo_root, "v1.0.0", "2026-01-01")
        _make_published(repo_root, ["v1.0.0"])

        bug_path = _make_bug_file(
            bugs_dir,
            bug_num=99,
            slug="typo-affects",
            symptom="Defect with typo'd version in affects.",
            status="open",
            affects="v1.0.0 (typo)",  # not a clean version string
        )

        regenerate(repo_root, ["v1.0.0"], ["v1.0.0"], bugs_dir)

        _, err = capsys.readouterr()
        assert str(bug_path) in err or bug_path.name in err, (
            f"Expected bug filename in stderr for typo'd affects; got: {err!r}"
        )
        assert "Affects value not a known release" in err, (
            f"Expected warning phrase in stderr for typo'd affects; got: {err!r}"
        )

    def test_absent_affects_section_no_warning(
        self, tmp_path: pathlib.Path, capsys: pytest.CaptureFixture
    ) -> None:
        """A bug with no ## Affects section at all produces no warning (internal convention)."""
        repo_root = tmp_path / "repo"
        repo_root.mkdir()
        bugs_dir = tmp_path / "bugs"

        _make_release_notes(repo_root, "v1.0.0", "2026-01-01")
        _make_published(repo_root, ["v1.0.0"])

        _make_bug_file(
            bugs_dir,
            bug_num=43,
            slug="internal-no-affects-section",
            symptom="Internal defect without affects section.",
            status="open",
            include_affects_section=False,
        )

        regenerate(repo_root, ["v1.0.0"], ["v1.0.0"], bugs_dir)

        _, err = capsys.readouterr()
        # An absent Affects section produces no warning — absence is the correct
        # internal-bug convention.
        assert "Affects value not a known release" not in err, (
            f"Unexpected warning for bug with absent ## Affects; stderr: {err!r}"
        )


# ===========================================================================
# Criterion 2: Real-affects regression — v1.0.0-affects bug still gets a counter
# ===========================================================================


class TestRealAffectsRegressionStillReceivesCounter:
    """After the gating fix, bugs with valid version Affects still get Public IDs."""

    def test_valid_affects_bug_receives_public_id(
        self, tmp_path: pathlib.Path
    ) -> None:
        """A bug with Affects=v1.0.0 (a real published version) gets a KI- Public ID."""
        repo_root = tmp_path / "repo"
        repo_root.mkdir()
        bugs_dir = tmp_path / "bugs"

        _make_release_notes(repo_root, "v1.0.0", "2026-01-01")
        _make_published(repo_root, ["v1.0.0"])

        bug_path = _make_bug_file(
            bugs_dir,
            bug_num=23,
            slug="real-affects",
            symptom="Real symptom affecting v1.0.0.",
            status="open",
            affects="v1.0.0",
        )

        content = regenerate(repo_root, ["v1.0.0"], ["v1.0.0"], bugs_dir)

        rec = parse_bug_file(bug_path)
        assert rec.public_id.startswith("KI-1.0.0."), (
            f"Expected KI-1.0.0.N public ID; got {rec.public_id!r}"
        )
        assert rec.public_id in content, (
            "Public ID not present in generated CHANGELOG content"
        )
        assert "KI-1.0.0.1" in content

    def test_valid_affects_bug_renders_in_ki_section(
        self, tmp_path: pathlib.Path
    ) -> None:
        """A valid-affects bug appears in the Known Issues section of the CHANGELOG."""
        repo_root = tmp_path / "repo"
        repo_root.mkdir()
        bugs_dir = tmp_path / "bugs"

        _make_release_notes(repo_root, "v1.0.0", "2026-01-01")
        _make_published(repo_root, ["v1.0.0"])

        _make_bug_file(
            bugs_dir,
            bug_num=23,
            slug="real-affects-ki",
            symptom="Disclosed symptom for KI section.",
            status="open",
            affects="v1.0.0",
            public_id="KI-1.0.0.1",
        )

        content = regenerate(repo_root, ["v1.0.0"], ["v1.0.0"], bugs_dir)

        ki_section_match = re.search(r"### Known Issues\n(.+?)(?=###|\Z)", content, re.DOTALL)
        assert ki_section_match is not None, "Known Issues section not found"
        ki_body = ki_section_match.group(1).strip()
        assert ki_body != "None", "Known Issues section is empty for a disclosed bug"
        assert "KI-1.0.0.1" in ki_body
        assert "Disclosed symptom for KI section" in ki_body

    def test_prose_affects_does_not_advance_counter_for_valid_bugs(
        self, tmp_path: pathlib.Path
    ) -> None:
        """A prose-affects bug does not consume a counter slot for the same anchor.

        Two bugs: one with prose Affects (should get nothing), one with
        Affects=v1.0.0 (should get KI-1.0.0.1 not KI-1.0.0.2).
        """
        repo_root = tmp_path / "repo"
        repo_root.mkdir()
        bugs_dir = tmp_path / "bugs"

        _make_release_notes(repo_root, "v1.0.0", "2026-01-01")
        _make_published(repo_root, ["v1.0.0"])

        _make_bug_file(
            bugs_dir,
            bug_num=44,
            slug="prose-affects-counter",
            symptom="Internal prose-affects defect.",
            status="open",
            affects="(internal — plugin era; no KI)",
        )
        valid_bug_path = _make_bug_file(
            bugs_dir,
            bug_num=45,
            slug="real-affects-counter",
            symptom="Valid v1.0.0 defect.",
            status="open",
            affects="v1.0.0",
        )

        content = regenerate(repo_root, ["v1.0.0"], ["v1.0.0"], bugs_dir)

        rec = parse_bug_file(valid_bug_path)
        # Counter must be 1, not 2 (prose bug must not have consumed .1)
        assert rec.public_id == "KI-1.0.0.1", (
            f"Expected KI-1.0.0.1 (prose bug must not advance counter); "
            f"got {rec.public_id!r}"
        )
        assert "KI-1.0.0.1" in content
        # No KI-1.0.0.2 (prose bug did not get one)
        assert "KI-1.0.0.2" not in content


# ===========================================================================
# Criterion 3: Cleanup rider (remove_bogus_public_ids)
# ===========================================================================


class TestRemoveBogusPublicIds:
    """remove_bogus_public_ids must remove bogus stamps and be idempotent."""

    def test_bogus_stamp_removed_from_prose_affects_bug(
        self, tmp_path: pathlib.Path
    ) -> None:
        """A bug with prose Affects and an existing Public ID has that ID removed."""
        bugs_dir = tmp_path / "bugs"

        bug_path = _make_bug_file(
            bugs_dir,
            bug_num=51,
            slug="bogus-stamp",
            symptom="Internal defect erroneously stamped.",
            status="open",
            affects="(internal — plugin era; no KI)",
            public_id="KI-1.0.0.12",  # the bogus stamp
        )

        # Confirm the Public ID is present before cleanup.
        rec_before = parse_bug_file(bug_path)
        assert rec_before.public_id == "KI-1.0.0.12", (
            "Test setup error: Public ID not present before cleanup"
        )

        modified = remove_bogus_public_ids(bugs_dir, ["v1.0.0"])

        assert bug_path in modified, (
            "Expected bug_path in the list of modified files"
        )

        rec_after = parse_bug_file(bug_path)
        assert rec_after.public_id == "", (
            f"Expected Public ID to be removed; got {rec_after.public_id!r}"
        )

    def test_clean_ledger_is_byte_identical(
        self, tmp_path: pathlib.Path
    ) -> None:
        """Running the cleanup on a clean ledger (no bogus stamps) changes no bytes."""
        bugs_dir = tmp_path / "bugs"

        # A valid bug with a correctly assigned Public ID (Affects = v1.0.0).
        bug_path = _make_bug_file(
            bugs_dir,
            bug_num=23,
            slug="clean-valid-stamp",
            symptom="Valid bug with correct Public ID.",
            status="open",
            affects="v1.0.0",
            public_id="KI-1.0.0.1",
        )

        content_before = bug_path.read_text(encoding="utf-8")
        modified = remove_bogus_public_ids(bugs_dir, ["v1.0.0"])

        assert bug_path not in modified, (
            "Clean bug was included in the list of modified files"
        )
        content_after = bug_path.read_text(encoding="utf-8")
        assert content_before == content_after, (
            "Clean bug file content changed after running cleanup — not byte-identical"
        )

    def test_cleanup_is_idempotent(
        self, tmp_path: pathlib.Path
    ) -> None:
        """Running remove_bogus_public_ids twice on the same ledger is idempotent."""
        bugs_dir = tmp_path / "bugs"

        bug_path = _make_bug_file(
            bugs_dir,
            bug_num=51,
            slug="idempotent-cleanup",
            symptom="Internal defect to be cleaned twice.",
            status="open",
            affects="(internal — plugin era; no KI)",
            public_id="KI-1.0.0.6",
        )

        # First run: removes the bogus stamp.
        modified_first = remove_bogus_public_ids(bugs_dir, ["v1.0.0"])
        assert bug_path in modified_first

        content_after_first = bug_path.read_text(encoding="utf-8")

        # Second run: bug now has no Public ID — should be a no-op.
        modified_second = remove_bogus_public_ids(bugs_dir, ["v1.0.0"])
        assert bug_path not in modified_second, (
            "Second cleanup run included a bug that was already clean"
        )

        content_after_second = bug_path.read_text(encoding="utf-8")
        assert content_after_first == content_after_second, (
            "Second cleanup run changed file content — not idempotent"
        )

    def test_cleanup_targets_only_prose_affects_bugs(
        self, tmp_path: pathlib.Path
    ) -> None:
        """Cleanup removes only the bogus stamp; valid stamps are not touched."""
        bugs_dir = tmp_path / "bugs"

        valid_path = _make_bug_file(
            bugs_dir,
            bug_num=23,
            slug="valid-affects",
            symptom="Valid bug.",
            status="open",
            affects="v1.0.0",
            public_id="KI-1.0.0.1",
        )
        bogus_path = _make_bug_file(
            bugs_dir,
            bug_num=51,
            slug="prose-affects-bogus",
            symptom="Internal bug with bogus stamp.",
            status="open",
            affects="(internal — plugin era; no KI)",
            public_id="KI-1.0.0.12",
        )

        valid_content_before = valid_path.read_text(encoding="utf-8")
        modified = remove_bogus_public_ids(bugs_dir, ["v1.0.0"])

        # Valid bug must not be touched
        assert valid_path not in modified
        valid_content_after = valid_path.read_text(encoding="utf-8")
        assert valid_content_before == valid_content_after

        # Bogus bug must be cleaned
        assert bogus_path in modified
        rec = parse_bug_file(bogus_path)
        assert rec.public_id == ""

    def test_cleanup_absent_affects_bug_with_stamp_is_not_modified(
        self, tmp_path: pathlib.Path
    ) -> None:
        """A bug with no ## Affects section but an existing Public ID is not touched.

        The cleanup only removes Public IDs when ## Affects is non-empty prose.
        A missing ## Affects is considered clean (unknown/unverified state).
        """
        bugs_dir = tmp_path / "bugs"

        # Bug with no Affects section but a pre-existing Public ID.
        # This is an unusual state, but the cleanup must not corrupt it.
        bug_path = _make_bug_file(
            bugs_dir,
            bug_num=10,
            slug="no-affects-has-id",
            symptom="Bug with a Public ID but no Affects section.",
            status="open",
            include_affects_section=False,
            public_id="KI-1.0.0.3",
        )

        content_before = bug_path.read_text(encoding="utf-8")
        modified = remove_bogus_public_ids(bugs_dir, ["v1.0.0"])

        assert bug_path not in modified, (
            "Cleanup must not touch a bug with absent ## Affects"
        )
        content_after = bug_path.read_text(encoding="utf-8")
        assert content_before == content_after
